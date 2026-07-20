"""
Agent worker path: video → ASR timestamps → filter → clipper → complete.
Uses local faster-whisper if available; otherwise reports clear failure.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("PATH", "")
ffbin = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if ffbin.exists():
    os.environ["PATH"] = str(ffbin) + os.pathsep + os.environ["PATH"]

BASE = os.environ.get("CLIPPER_WEB_BASE", "http://127.0.0.1:8787")

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
    "领口", "袖口", "开叉", "通勤", "搭配",
)


def claim() -> dict | None:
    r = httpx.get(f"{BASE}/api/agent/next", timeout=30)
    r.raise_for_status()
    data = r.json()
    return data if data.get("job") else None


def complete(job_id: str, **kwargs) -> None:
    httpx.post(
        f"{BASE}/api/agent/jobs/{job_id}/complete",
        json=kwargs or {"status": "success", "transcript_source": "agent_skill"},
        timeout=30,
    ).raise_for_status()


def fail(job_id: str, error: str) -> None:
    httpx.post(
        f"{BASE}/api/agent/jobs/{job_id}/fail",
        json={"error": error},
        timeout=30,
    ).raise_for_status()


def extract_wav(video: Path, wav: Path) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg extract failed: {p.stderr[-500:]}")


def asr_faster_whisper(wav: Path) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "未安装 faster-whisper。请: python -m pip install faster-whisper"
        ) from e

    model_size = (os.environ.get("CLIPPER_LOCAL_WHISPER_MODEL") or "base").strip()
    print(f"[asr] loading faster-whisper model={model_size!r}")
    # Prefer mirror if set; also try default cache
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
        raise RuntimeError("ASR 未识别到有效口播文本")
    print(f"[asr] segments={len(out)} language={getattr(info, 'language', '?')}")
    return out


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
    # keep product-ish lines; also keep if has digits/price-ish
    if has_core or any(ch.isdigit() for ch in t) or "块" in t or "元" in t:
        return True
    # keep medium informative lines
    return len(t) >= 8 and not is_chat


def filter_transcript(raw: list[dict]) -> list[dict]:
    kept = []
    for u in raw:
        if keep_line(u.get("text") or ""):
            kept.append(u)
    # fallback: if over-filtered, keep non-empty non-pure-chitchat
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
    print(p.stdout[-2000:] if p.stdout else "")
    if p.returncode != 0:
        raise RuntimeError(f"clipper failed: {p.stderr[-800:] or p.stdout[-800:]}")


def main() -> int:
    data = claim()
    if not data:
        print("QUEUE_EMPTY")
        return 0

    job = data["job"]
    paths = data["paths"]
    job_id = job["job_id"]
    job_dir = Path(paths["job_dir"])
    video = Path(paths["video"]) if paths.get("video") else None
    target = int(job.get("target_seconds") or 60)
    render = bool(job.get("render_requested", True))

    print("[job]", job_id, "video=", video)
    try:
        if not video or not video.exists():
            raise RuntimeError("job missing video file")

        work = job_dir / "asr_work"
        work.mkdir(parents=True, exist_ok=True)
        wav = work / "audio_16k.wav"
        print("[step] extract audio")
        extract_wav(video, wav)

        print("[step] ASR → transcript with timestamps")
        raw = asr_faster_whisper(wav)
        raw_path = job_dir / "transcript_asr.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        print("[step] filter size/sentiment/chitchat")
        kept = filter_transcript(raw)
        tr_path = job_dir / "transcript_for_clipper.json"
        tr_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[filter] raw={len(raw)} kept={len(kept)}")

        print("[step] clipper rank + cut")
        run_clipper(video, tr_path, job_dir, target, render)

        has_plan = (job_dir / "plan.json").exists()
        has_final = (job_dir / "final.mp4").exists()
        status = "success" if has_plan and has_final else ("success_partial" if has_plan else "failed")
        if status == "failed":
            raise RuntimeError("clipper did not produce plan.json")

        complete(
            job_id,
            status=status,
            transcript_source="faster_whisper_local",
            message=f"asr_segments={len(raw)} kept={len(kept)}",
        )
        print("[done]", status, job_id)
        return 0
    except Exception as e:
        print("[fail]", e)
        try:
            fail(job_id, str(e))
        except Exception as e2:
            print("[fail-report-error]", e2)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
