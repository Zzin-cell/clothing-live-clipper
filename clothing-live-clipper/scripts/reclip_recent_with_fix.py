"""Re-run filter+rank+render for the latest web job to verify multi-segment plans."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

ff = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if ff.exists():
    os.environ["PATH"] = str(ff) + os.pathsep + os.environ.get("PATH", "")

from filter_transcript_v2 import filter_for_duration  # type: ignore
from clipper.config import Settings
from clipper.pipeline import run_pipeline


def main() -> int:
    jobs = ROOT / "output" / "web_jobs"
    dirs = sorted([p for p in jobs.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    if not dirs:
        print("no jobs")
        return 1
    d = dirs[0]
    print("job", d.name)
    raw_p = d / "transcript_asr.json"
    if not raw_p.exists():
        print("no asr")
        return 1
    raw = json.loads(raw_p.read_text(encoding="utf-8"))
    kept = filter_for_duration(raw, target_ms=78000, min_ms=20000, max_ms=90000)
    print("raw", len(raw), "kept", len(kept))
    for u in kept[:8]:
        print(" -", u.get("t0_ms"), u.get("t1_ms"), str(u.get("text"))[:60])
    tr = d / "transcript_for_clipper.json"
    tr.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")

    # find video
    uploads = d / "uploads"
    vids = list(uploads.glob("*")) if uploads.exists() else []
    video = vids[0] if vids else None
    if not video:
        print("no video")
        return 1
    settings = Settings.from_env()
    settings = Settings(
        target_duration_s=60,
        golden_s=settings.golden_s,
        cta_s=settings.cta_s,
        min_clip_ms=settings.min_clip_ms,
        max_clip_ms=settings.max_clip_ms,
        min_plan_ms=settings.min_plan_ms,
        max_plan_ms=settings.max_plan_ms,
        playback_speed=1.3,
        golden_weight_ratio=settings.golden_weight_ratio,
        golden_features_only=True,
        demote_outfit_change_from_golden=True,
        exclude_price_from_cut=True,
        clothing_only=True,
    )
    result = run_pipeline(video=video, transcript_path=tr, out_dir=d, settings=settings, render=False)
    plan = result.plan
    print("golden", len(plan.golden), "trust", len(plan.trust), "cta", len(plan.cta))
    for s in plan.golden:
        print("G", s.t0_ms, s.t1_ms, s.text[:50])
    for s in plan.trust[:4]:
        print("T", s.t0_ms, s.t1_ms, s.text[:50])
    print("warnings", plan.warnings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
