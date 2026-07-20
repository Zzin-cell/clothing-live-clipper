import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clipper.asr import load_transcript
from clipper.extract import extract_claims, utterances_to_clips
from clipper.rank import build_timeline_plan, score_all
from clipper.config import Settings

for name in ("001", "002"):
    p = ROOT / "output" / "agent_jobs" / "desktop_batch" / name / "transcript_for_clipper.json"
    tr = load_transcript(p)
    claims = extract_claims(tr)
    clips = utterances_to_clips(tr, claims=claims)
    scored = score_all(clips)
    pos = [c for c in scored if c.score > 0]
    zero = [c for c in scored if c.score <= 0]
    pos_ms = sum(c.duration_ms for c in pos)
    print("====", name, "clips", len(scored), "pos", len(pos), "pos_ms", pos_ms, "zero", len(zero))
    for c in sorted(pos, key=lambda x: -x.score)[:15]:
        print(f"  +{c.score:.0f} {c.duration_ms}ms {c.claim_types} {c.text[:60]}")
    for c in zero[:8]:
        print(f"  0 {c.duration_ms}ms {c.claim_types} {c.text[:60]}")
    plan = build_timeline_plan(scored, Settings(target_duration_s=60))
    print("plan_ms", plan.total_duration_ms, "warn", plan.warnings, "n", len(plan.all_slots()))
