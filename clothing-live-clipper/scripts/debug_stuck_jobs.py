from __future__ import annotations

import json
from pathlib import Path

jobs = Path(r"C:\Users\MR\AppData\grok\clothing-live-clipper\output\web_jobs")
print("jobs_dir", jobs, "exists", jobs.exists())
if not jobs.exists():
    raise SystemExit(0)
dirs = sorted([p for p in jobs.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
print("count", len(dirs))
for d in dirs[:8]:
    meta_p = d / "job_meta.json"
    print("\n===", d.name)
    if meta_p.exists():
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        for k in (
            "status",
            "stage",
            "progress",
            "error",
            "asr_model",
            "video_source",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "worker",
        ):
            if k in meta:
                print(f"  {k}: {meta.get(k)}")
    else:
        print("  no meta")
    for name in (
        "uploads",
        "asr_work/audio_16k.wav",
        "transcript_asr.json",
        "transcript_for_clipper.json",
        "plan.json",
        "final.mp4",
    ):
        p = d / name
        if p.exists():
            if p.is_file():
                print(f"  file {name}: {p.stat().st_size}")
            else:
                print(f"  dir {name}: {len(list(p.iterdir()))} items")
