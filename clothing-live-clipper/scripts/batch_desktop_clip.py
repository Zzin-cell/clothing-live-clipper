"""Batch clip videos from desktop folders."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(r"C:\Users\MR\Desktop\检查文件\待剪辑")
DST = Path(r"C:\Users\MR\Desktop\检查文件\已经完成")
WORK = ROOT / "output" / "agent_jobs" / "desktop_batch"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts", ".mts", ".m2ts"}


def main() -> int:
    DST.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    if not SRC.exists():
        print("SRC_MISSING", SRC)
        return 2

    videos = sorted(
        p for p in SRC.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT
    )
    print("found", len(videos), "videos in", SRC)
    if not videos:
        return 0

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    ffbin = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
    if ffbin.exists():
        env["PATH"] = str(ffbin) + os.pathsep + env.get("PATH", "")

    results = []
    for i, video in enumerate(videos, 1):
        print(f"\n===== [{i}/{len(videos)}] {video.name} =====")
        job_out = WORK / video.stem
        job_out.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "agent_clip_video.py"),
            str(video),
            "--out",
            str(job_out),
            "--seconds",
            "60",
        ]
        code = subprocess.call(cmd, cwd=str(ROOT), env=env)
        dest_dir = DST / video.stem
        dest_dir.mkdir(parents=True, exist_ok=True)

        # copy useful outputs
        copied = []
        for name in (
            "final.mp4",
            "plan.json",
            "review.md",
            "run_report.md",
            "transcript_asr.json",
            "transcript_for_clipper.json",
            "clips.json",
            "error.txt",
        ):
            src_f = job_out / name
            if src_f.exists():
                shutil.copy2(src_f, dest_dir / name)
                copied.append(name)

        # also copy original for reference
        try:
            shutil.copy2(video, dest_dir / f"source{video.suffix.lower()}")
        except Exception as e:
            print("copy source failed", e)

        final = dest_dir / "final.mp4"
        status = "success" if final.exists() else ("partial" if (dest_dir / "plan.json").exists() else "failed")
        results.append(
            {
                "video": video.name,
                "status": status,
                "code": code,
                "out": str(dest_dir),
                "copied": copied,
            }
        )
        print("status", status, "->", dest_dir)

    summary = DST / "_batch_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY", summary)
    for r in results:
        print(r["status"], r["video"], r["out"])
    return 0 if all(r["status"] != "failed" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
