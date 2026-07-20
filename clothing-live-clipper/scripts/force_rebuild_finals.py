"""Force rebuild finals from existing plan.json with clean work dir."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["PATH"] = str(Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin") + os.pathsep + os.environ.get("PATH", "")

from clipper.media import render_plan

SRC = Path(r"C:\Users\MR\Desktop\检查文件\待剪辑")
DST = Path(r"C:\Users\MR\Desktop\检查文件\已经完成")
WORK = ROOT / "output" / "agent_jobs" / "desktop_batch"


def main() -> None:
    for name in ("001", "002"):
        job = WORK / name
        plan = json.loads((job / "plan.json").read_text(encoding="utf-8"))
        segs = []
        for key in ("golden", "trust", "cta"):
            for s in plan.get(key) or []:
                segs.append((int(s["t0_ms"]), int(s["t1_ms"])))
        video = SRC / f"{name}.mp4"
        work_dir = job / "_parts_clean"
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        out = job / "final.mp4"
        print(name, "segs", len(segs), "render…")
        render_plan(
            video,
            segs,
            out,
            work_dir=work_dir,
            smooth=True,
            crossfade_s=0.20,
            edge_fade_s=0.14,
            playback_speed=1.3,
        )
        dest = DST / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, dest / "final.mp4")
        if (job / "review.md").exists():
            shutil.copy2(job / "review.md", dest / "review.md")
        if (job / "plan.json").exists():
            shutil.copy2(job / "plan.json", dest / "plan.json")
        print("done", dest / "final.mp4")


if __name__ == "__main__":
    main()
