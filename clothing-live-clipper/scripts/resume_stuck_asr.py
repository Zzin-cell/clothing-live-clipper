"""Resume stuck jobs that already have wav but no transcript."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

ffbin = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if ffbin.exists():
    os.environ["PATH"] = str(ffbin) + os.pathsep + os.environ.get("PATH", "")

from clipper.job_worker import process_job_dir, _read_meta, _write_meta, _utc_now

JOBS = ROOT / "output" / "web_jobs"


def main() -> int:
    # Prefer faster decode while unblocking
    os.environ.setdefault("CLIPPER_ASR_BEAM_SIZE", "3")
    os.environ.setdefault("CLIPPER_ASR_BEST_OF", "3")

    stuck = []
    for d in sorted(JOBS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        meta_p = d / "job_meta.json"
        if not meta_p.exists():
            continue
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        if meta.get("status") == "processing" and meta.get("stage") == "asr":
            stuck.append(d)

    print("stuck_asr", len(stuck))
    if not stuck:
        return 0

    # only newest 2
    for d in stuck[:2]:
        print("resume", d.name)
        meta = _read_meta(d)
        meta["error"] = None
        meta["stage"] = "asr"
        meta["progress"] = 25
        meta["status"] = "processing"
        meta["resume_note"] = f"manual_resume {_utc_now()}"
        _write_meta(d, meta)
        t0 = time.time()
        process_job_dir(d)
        meta2 = _read_meta(d)
        print(
            "done",
            d.name,
            meta2.get("status"),
            meta2.get("stage"),
            meta2.get("error"),
            f"elapsed={time.time()-t0:.1f}s",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
