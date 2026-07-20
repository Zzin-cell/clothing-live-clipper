#!/usr/bin/env python3
"""
Agent-first clothing clip worker (no Web UI required).

Flow (skill core):
  video → extract audio → local ASR timestamps → filter → clipper → plan/final

Usage:
  set PYTHONPATH=src
  set PATH=%LOCALAPPDATA%\\ffmpeg\\bin;%PATH%
  python scripts\\agent_clip_video.py "D:\\path\\live.mp4"
  python scripts\\agent_clip_video.py "D:\\path\\live.mp4" --out output\\myjob --seconds 60 --no-render
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

# re used in keep_line for livestream try-on filler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ffbin = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if ffbin.exists():
    os.environ["PATH"] = str(ffbin) + os.pathsep + os.environ.get("PATH", "")

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
    "过一下", "过一遍", "带过", "先过", "往下过", "咱们过",
    "看一下", "看一看", "说一下", "讲一下", "介绍一下",
    "给大家看", "给你们看", "来看一下", "注意看",
    "一会儿", "待会", "等会", "马上", "接下来",
    "铃铃铃", "有没有人", "在不在", "刚进来", "欢迎",
    "感谢", "谢谢老板", "谢谢姐妹", "公屏", "弹幕",
)
OFFTOPIC_WORDS = (
    "零食", "好吃", "水果", "开心果", "坚果", "包装太大", "浪费了",
)
CORE_WORDS = (
    "收腰", "修身", "版型", "显瘦", "遮肉", "梨形", "面料", "布料", "醋酸", "凉感",
    "雪纺", "纯棉", "牛仔", "材质", "柔软", "透气", "不起球", "垂感", "弹力", "不透",
    "券后", "只要", "原价", "小黄车", "链接", "加购", "下单", "弹窗",
    "领口", "袖口", "开叉", "通勤", "搭配", "号链接", "购物车", "库存",
    "上衣", "裤子", "裙子", "外套", "内搭", "牛仔裤", "蕾丝", "雷丝", "洗水",
    "显白", "百搭", "闭眼入", "遮胯", "高腰", "宽松", "直筒",
)


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
    """Prefer explicit path / pointer file / default local download dir."""
    env = (os.environ.get("CLIPPER_LOCAL_WHISPER_MODEL") or "").strip()
    if env and (Path(env).exists() or env in {
        "tiny", "base", "small", "medium", "large-v2", "large-v3", "turbo",
        "tiny.en", "base.en", "small.en", "medium.en",
    }):
        return env
    pointer = Path(__file__).resolve().parent / "local_whisper_model_path.txt"
    if pointer.exists():
        p = pointer.read_text(encoding="utf-8").strip()
        if p and Path(p).exists():
            return p
    default_dir = Path(r"C:\Users\MR\AppData\grok\models\whisper-tiny")
    if default_dir.exists() and (default_dir / "model.bin").exists():
        return str(default_dir)
    return "tiny"


def asr_local(wav: Path) -> list[dict]:
    from faster_whisper import WhisperModel

    model_size = resolve_local_model()
    # Optional mirror for first download only
    if "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    print(f"[asr] local faster-whisper model={model_size!r}")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(wav), language="zh", vad_filter=True)
    out: list[dict] = []
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
        raise RuntimeError("ASR produced no speech segments")
    print(f"[asr] segments={len(out)} language={getattr(info, 'language', None)}")
    return out


def filter_transcript(raw: list[dict]) -> list[dict]:
    """Clothing-only keep, fill toward 55–60s with medium product lines (not filler)."""
    # local import avoids circular issues when used as library
    from filter_transcript_v2 import filter_for_duration

    return filter_for_duration(raw, target_ms=60_000, min_ms=55_000, max_ms=65_000)


def run_clipper(video: Path, transcript: Path, out_dir: Path, seconds: int, render: bool) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    cmd = [
        sys.executable,
        "-m",
        "clipper",
        "run",
        "--video",
        str(video),
        "--transcript",
        str(transcript),
        "--out",
        str(out_dir),
        "--target-seconds",
        str(seconds),
    ]
    if not render:
        cmd.append("--no-render")
    print("[clipper]", " ".join(cmd))
    p = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if p.stdout:
        print(p.stdout[-2000:])
    if p.returncode != 0:
        raise RuntimeError(f"clipper failed: {(p.stderr or p.stdout or '')[-1200:]}")


def write_report(out_dir: Path, raw_n: int, kept_n: int, source: str) -> None:
    lines = [
        "# Agent clip report",
        "",
        f"- asr_source: {source}",
        f"- raw_segments: {raw_n}",
        f"- kept_segments: {kept_n}",
        f"- plan: {(out_dir / 'plan.json').exists()}",
        f"- final: {(out_dir / 'final.mp4').exists()}",
        f"- review: {(out_dir / 'review.md').exists()}",
        "",
        "Core path: video → auto transcript+timestamps → content filter → clipper reorder (~60s, golden 20s).",
    ]
    (out_dir / "run_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent clothing clip: video only → ASR → cut")
    ap.add_argument("video", help="Path to livestream VOD")
    ap.add_argument("--out", default=None, help="Output dir (default output/agent_jobs/<id>)")
    ap.add_argument("--seconds", type=int, default=60, help="Target duration seconds")
    ap.add_argument("--no-render", action="store_true", help="Plan only, no final.mp4")
    ap.add_argument(
        "--transcript",
        default=None,
        help="Optional existing transcript json/srt (skip ASR). Prefer auto ASR.",
    )
    args = ap.parse_args()

    video = Path(args.video).expanduser().resolve()
    if not video.exists():
        print(f"[error] video not found: {video}")
        return 2

    job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    out_dir = Path(args.out).expanduser().resolve() if args.out else (ROOT / "output" / "agent_jobs" / job_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[job]", job_id)
    print("[video]", video)
    print("[out]", out_dir)

    try:
        if args.transcript:
            tr_src = Path(args.transcript).expanduser().resolve()
            if not tr_src.exists():
                raise RuntimeError(f"transcript not found: {tr_src}")
            raw_path = out_dir / "transcript_asr.json"
            # normalize via clipper loader if needed later; copy as working raw
            if tr_src.suffix.lower() == ".json":
                data = json.loads(tr_src.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "transcript" in data:
                    data = data["transcript"]
                raw = data
                raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                # srt: let clipper read it directly as for_clipper
                raw = []
                raw_path = tr_src
            source = "provided_transcript"
        else:
            work = out_dir / "asr_work"
            wav = work / "audio_16k.wav"
            print("[1/4] extract audio")
            extract_wav(video, wav)
            print("[2/4] auto speech-to-text with timestamps (local, no Web API required)")
            raw = asr_local(wav)
            raw_path = out_dir / "transcript_asr.json"
            raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            source = "faster_whisper_local"

        print("[3/4] filter size/sentiment/pure chitchat")
        if isinstance(raw_path, Path) and raw_path.suffix.lower() == ".json" and raw:
            kept = filter_transcript(raw)
            tr_path = out_dir / "transcript_for_clipper.json"
            tr_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
            raw_n, kept_n = len(raw), len(kept)
        else:
            # srt path: skip python filter; clipper lexicon still scores
            tr_path = raw_path
            raw_n, kept_n = -1, -1
            kept = []

        print(f"[filter] raw={raw_n} kept={kept_n}")
        print("[4/4] clipper reorder + optional render")
        run_clipper(video, tr_path, out_dir, args.seconds, render=not args.no_render)
        write_report(out_dir, raw_n if raw_n >= 0 else 0, kept_n if kept_n >= 0 else 0, source)

        print("\n=== DONE ===")
        print("out:", out_dir)
        for name in ("transcript_asr.json", "transcript_for_clipper.json", "plan.json", "review.md", "final.mp4", "run_report.md"):
            p = out_dir / name
            if p.exists():
                print(" -", p)
        return 0
    except Exception as e:
        err = out_dir / "error.txt"
        err.write_text(str(e), encoding="utf-8")
        print("[FAIL]", e)
        print(
            "\nIf ASR model download failed: set network/mirror, then:\n"
            "  set HF_ENDPOINT=https://hf-mirror.com\n"
            "  set CLIPPER_LOCAL_WHISPER_MODEL=tiny\n"
            "  python scripts\\download_whisper_tiny.py\n"
            "No Web API key is required for this local path."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
