"""Re-run filter+clipper using existing transcript_asr.json (skip slow ASR)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from filter_transcript_v2 import filter_for_duration  # type: ignore
from agent_clip_video import run_clipper  # type: ignore

SRC = Path(r"C:\Users\MR\Desktop\检查文件\待剪辑")
DST = Path(r"C:\Users\MR\Desktop\检查文件\已经完成")
WORK = ROOT / "output" / "agent_jobs" / "desktop_batch"


def process_one(name: str) -> None:
    video = SRC / f"{name}.mp4"
    job = WORK / name
    asr_path = job / "transcript_asr.json"
    if not asr_path.exists():
        raise SystemExit(f"missing asr: {asr_path}")
    raw = json.loads(asr_path.read_text(encoding="utf-8"))
    # 1.3x final → select ~78s clothing source (72–85s)
    kept = filter_for_duration(raw, target_ms=78_000, min_ms=72_000, max_ms=85_000)
    tr = job / "transcript_for_clipper.json"
    tr.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    kept_ms = sum(max(0, int(u["t1_ms"]) - int(u["t0_ms"])) for u in kept)
    print(name, "raw", len(raw), "kept", len(kept), "kept_ms", kept_ms)
    for u in kept[:20]:
        print(" ", u.get("text"), f"({max(0,int(u['t1_ms'])-int(u['t0_ms']))}ms)")

    # Also write unmerged individual kept lines for clipper scoring density
    # if merged still short: expand by re-including non-drop singles already in kept
    if kept_ms < 72_000:
        print("WARN short clothing material", kept_ms, "ms — target source ~78s for 1.3x→60s")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    ffbin = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
    if ffbin.exists():
        env["PATH"] = str(ffbin) + os.pathsep + env.get("PATH", "")
    run_clipper(video, tr, job, 60, True)

    dest = DST / name
    dest.mkdir(parents=True, exist_ok=True)
    for fname in (
        "final.mp4",
        "plan.json",
        "review.md",
        "transcript_for_clipper.json",
        "transcript_asr.json",
        "run_report.md",
        "clips.json",
    ):
        p = job / fname
        if p.exists():
            dest.joinpath(fname).write_bytes(p.read_bytes())
    print("updated", dest)


def main() -> None:
    for name in ("001", "002"):
        print("====", name)
        process_one(name)


if __name__ == "__main__":
    main()
