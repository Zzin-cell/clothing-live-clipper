"""Reset a failed job back to queued for Agent processing."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "output" / "web_jobs"


def main() -> int:
    job_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not job_id:
        # pick latest job with video
        dirs = sorted(
            [p for p in JOBS.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for d in dirs:
            meta_p = d / "job_meta.json"
            if not meta_p.exists():
                continue
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            uploads = d / "uploads"
            vids = list(uploads.glob("*.*")) if uploads.exists() else []
            vids = [
                v
                for v in vids
                if v.suffix.lower()
                in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".mts", ".m2ts"}
            ]
            if vids:
                job_id = d.name
                break
    if not job_id:
        print("NO_JOB")
        return 1
    d = JOBS / job_id
    meta_p = d / "job_meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["status"] = "queued"
    meta["error"] = None
    meta["process_mode"] = "agent"
    meta["queue_hint"] = "在 Agent 对话发送：处理队列"
    meta["requeued_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta.pop("finished_at", None)
    meta.pop("claimed_at", None)
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REQUEUED", job_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
