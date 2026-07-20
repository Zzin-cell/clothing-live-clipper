"""Process an already-claimed job by id (video → ASR → filter → clipper → complete)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ffbin = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if ffbin.exists():
    os.environ["PATH"] = str(ffbin) + os.pathsep + os.environ.get("PATH", "")

import httpx

BASE = os.environ.get("CLIPPER_WEB_BASE", "http://127.0.0.1:8787")
JOBS = ROOT / "output" / "web_jobs"

SIZE_WORDS = (
    "尺码", "选码", "偏大", "偏小", "胸围", "腰围", "臀围", "身高", "穿M", "穿S",
    "穿L", "穿XL", "均码", "加大码", "码数", "建议穿",
)
SENTIMENT_WORDS = (
    "做了五年", "不容易", "感谢陪伴", "创业", "初心", "故事是这样", "一路走来",
    "谢谢支持我", "喜欢我的人",
)
CHITCHAT_WORDS = (
    "家人们", "老铁们", "听得到吗", "扣1", "扣一", "点点关注", "双击", "晚上好啊", "来了吗",
)
CORE_WORDS = (
    "收腰", "修身", "版型", "显瘦", "遮肉", "梨形", "面料", "布料", "醋酸", "凉感",
    "雪纺", "纯棉", "券后", "只要", "原价", "小黄车", "链接", "加购", "下单", "弹窗",
    "领口", "袖口", "开叉", "通勤", "搭配", "号链接", "购物车", "库存",
)


def fail(job_id: str, error: str) -> None:
    httpx.post(f"{BASE}/api/agent/jobs/{job_id}/fail", json={"error": error}, timeout=60).raise_for_status()


def complete(job_id: str, **kwargs) -> None:
    body = {"status": "success", "transcript_source": "agent_skill", **kwargs}
    httpx.post(f"{BASE}/api/agent/jobs/{job_id}/complete", json=body, timeout=60).raise_for_status()


def extract_wav(video: Path, wav: Path) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not wav.exists() or wav.stat().st_size < 100:
        raise RuntimeError(f"ffmpeg extract failed: {(p.stderr or '')[-800:]}")


def resolve_local_model() -> str:
    env = (os.environ.get("CLIPPER_LOCAL_WHISPER_MODEL") or "").strip()
    if env and Path(env).exists():
        return env
    pointer = Path(__file__).resolve().parent / "local_whisper_model_path.txt"
    if pointer.exists():
        p = pointer.read_text(encoding="utf-8").strip()
        if p and Path(p).exists():
            return p
    default_dir = Path(r"C:\Users\MR\AppData\grok\models\whisper-tiny")
    if (default_dir / "model.bin").exists():
        return str(default_dir)
    return env or "tiny"


def asr_local(wav: Path) -> list[dict]:
    from faster_whisper import WhisperModel

    model_size = resolve_local_model()
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    print(f"[asr] faster-whisper {model_size!r}")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(wav), language="zh", vad_filter=True)
    out = []
    for i, seg in enumerate(segments):
        text = (seg.text or "").strip()
        if not text:
            continue
        out.append(
            {
                "utt_id": f"w{i:04d}",
                "text": text,
                "t0_ms": max(0, int(round(seg.start * 1000))),
                "t1_ms": max(0, int(round(seg.end * 1000))),
            }
        )
    if not out:
        raise RuntimeError("ASR empty")
    print(f"[asr] segs={len(out)} lang={getattr(info,'language',None)}")
    return out


def asr_openai_compatible(wav: Path) -> list[dict]:
    """Try OpenAI-compatible transcriptions with env key/base; try several model ids."""
    from clipper.config import resolve_api_key, resolve_asr_base_url

    key = resolve_api_key()
    base = resolve_asr_base_url()
    if not key:
        raise RuntimeError("no api key")
    models = []
    env_m = (os.environ.get("CLIPPER_ASR_MODEL") or "").strip()
    if env_m:
        models.append(env_m)
    models += [
        "whisper-1",
        "whisper-large-v3",
        "whisper-large-v3-turbo",
        "gpt-4o-mini-transcribe",
        "gpt-4o-transcribe",
    ]
    # dedupe
    seen = set()
    models = [m for m in models if not (m in seen or seen.add(m))]
    url = f"{base}/audio/transcriptions"
    data_file = wav.read_bytes()
    last_err = None
    for model in models:
        print(f"[asr-api] try model={model}")
        try:
            with httpx.Client(timeout=600.0) as client:
                r = client.post(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    data={"model": model, "response_format": "verbose_json", "language": "zh"},
                    files={"file": (wav.name, data_file, "audio/wav")},
                )
            if r.status_code >= 400:
                last_err = f"{model}: HTTP {r.status_code} {r.text[:300]}"
                print("[asr-api]", last_err)
                continue
            payload = r.json()
            segs = payload.get("segments") or []
            out = []
            if segs:
                for i, seg in enumerate(segs):
                    text = str(seg.get("text") or "").strip()
                    if not text:
                        continue
                    out.append(
                        {
                            "utt_id": f"a{i:04d}",
                            "text": text,
                            "t0_ms": max(0, int(round(float(seg.get("start") or 0) * 1000))),
                            "t1_ms": max(0, int(round(float(seg.get("end") or 0) * 1000))),
                        }
                    )
            else:
                text = str(payload.get("text") or "").strip()
                if text:
                    # split roughly by punctuation with proportional times unknown → equal chunks
                    parts = [p.strip() for p in re_split_zh(text) if p.strip()]
                    if not parts:
                        parts = [text]
                    # without real segment times, spread over file duration estimate
                    # use ffprobe if possible else 60s
                    dur_ms = probe_duration_ms(wav) or 60000
                    span = dur_ms / max(1, len(parts))
                    for i, p in enumerate(parts):
                        out.append(
                            {
                                "utt_id": f"a{i:04d}",
                                "text": p,
                                "t0_ms": int(i * span),
                                "t1_ms": int(min(dur_ms, (i + 1) * span)),
                            }
                        )
            if out:
                print(f"[asr-api] ok model={model} segs={len(out)}")
                return out
            last_err = f"{model}: empty"
        except Exception as e:  # noqa: BLE001
            last_err = f"{model}: {e}"
            print("[asr-api]", last_err)
    raise RuntimeError(last_err or "api asr failed")


def re_split_zh(text: str) -> list[str]:
    import re

    return re.split(r"(?<=[。！？!?；;，,])\s*", text)


def probe_duration_ms(media: Path) -> int | None:
    try:
        p = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(media),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if p.returncode == 0 and p.stdout.strip():
            return int(float(p.stdout.strip()) * 1000)
    except Exception:
        return None
    return None


def keep_line(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    has_core = any(w.lower() in t.lower() for w in CORE_WORDS)
    is_size = any(w in t for w in SIZE_WORDS)
    is_sent = any(w in t for w in SENTIMENT_WORDS)
    is_chat = any(w in t for w in CHITCHAT_WORDS)
    if is_size and not has_core:
        return False
    if is_sent and not has_core:
        return False
    if is_chat and not has_core:
        return False
    if has_core or any(ch.isdigit() for ch in t) or "块" in t or "元" in t:
        return True
    return len(t) >= 8 and not is_chat


def filter_transcript(raw: list[dict]) -> list[dict]:
    kept = [u for u in raw if keep_line(u.get("text") or "")]
    if len(kept) < 2:
        kept = [
            u for u in raw
            if (u.get("text") or "").strip()
            and not any(w in (u.get("text") or "") for w in CHITCHAT_WORDS)
        ]
    if not kept:
        kept = [u for u in raw if (u.get("text") or "").strip()]
    return kept


def run_clipper(video: Path, transcript: Path, out_dir: Path, target: int, render: bool) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    cmd = [
        sys.executable, "-m", "clipper", "run",
        "--video", str(video),
        "--transcript", str(transcript),
        "--out", str(out_dir),
        "--target-seconds", str(target),
    ]
    if not render:
        cmd.append("--no-render")
    print("[clipper]", " ".join(cmd))
    p = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((p.stdout or "")[-2000:])
    if p.returncode != 0:
        raise RuntimeError(f"clipper failed: {(p.stderr or p.stdout or '')[-1000:]}")


def transcribe(wav: Path) -> tuple[list[dict], str]:
    errors = []
    # 1) local faster-whisper
    try:
        return asr_local(wav), "faster_whisper_local"
    except Exception as e:
        errors.append(f"local: {e}")
        print("[asr] local failed:", e)
    # 2) openai compatible API
    try:
        return asr_openai_compatible(wav), "openai_compatible_api"
    except Exception as e:
        errors.append(f"api: {e}")
        print("[asr] api failed:", e)
    raise RuntimeError(" | ".join(errors))


def main() -> int:
    job_id = sys.argv[1] if len(sys.argv) > 1 else "20260719_013931_034905fb"
    job_dir = JOBS / job_id
    meta_path = job_dir / "job_meta.json"
    if not meta_path.exists():
        print("missing job", job_id)
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    uploads = job_dir / "uploads"
    vids = [
        p for p in uploads.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    ] if uploads.exists() else []
    if not vids:
        fail(job_id, "missing video")
        return 1
    video = vids[0]
    target = int(meta.get("target_seconds") or 60)
    render = bool(meta.get("render_requested", True))
    print("[job]", job_id, video)

    try:
        work = job_dir / "asr_work"
        wav = work / "audio_16k.wav"
        print("[step] extract audio")
        extract_wav(video, wav)

        print("[step] ASR")
        raw, src = transcribe(wav)
        raw_path = job_dir / "transcript_asr.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        print("[step] filter")
        kept = filter_transcript(raw)
        tr_path = job_dir / "transcript_for_clipper.json"
        tr_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[filter] raw={len(raw)} kept={len(kept)} source={src}")

        print("[step] clipper")
        run_clipper(video, tr_path, job_dir, target, render)
        has_plan = (job_dir / "plan.json").exists()
        has_final = (job_dir / "final.mp4").exists()
        status = "success" if has_plan and has_final else ("success_partial" if has_plan else "failed")
        if status == "failed":
            raise RuntimeError("no plan.json")
        complete(
            job_id,
            status=status,
            transcript_source=src,
            message=f"raw={len(raw)} kept={len(kept)}",
        )
        print("[done]", status)
        return 0
    except Exception as e:
        print("[fail]", e)
        try:
            fail(job_id, str(e)[:1500])
        except Exception as e2:
            print("[fail-report]", e2)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
