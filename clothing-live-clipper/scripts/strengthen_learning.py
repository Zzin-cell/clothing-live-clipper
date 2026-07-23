from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clipper.learning import learning_status, seed_negative_live_phrases
from clipper.models import ClaimType, Clip
from clipper.rank import build_timeline_plan, score_clip
from clipper.config import Settings

print("seed negatives…")
prefs = seed_negative_live_phrases()
print("events", (prefs.get("stats") or {}).get("events"))
print("top_drop", list((prefs.get("drop_penalty") or {}).items())[:12])
print("top_hook", list((prefs.get("hook_boost") or {}).items())[:12])

samples = [
    "家人们扣1点关注直播间有福袋",
    "裙子颜色好显白",
    "版型显瘦收腰",
    "穿一下牛仔裤看看",
]
print("\nscore deltas:")
for s in samples:
    c = Clip(
        clip_id="x",
        text=s,
        t0_ms=0,
        t1_ms=3000,
        claim_types=[ClaimType.SELLING_POINT, ClaimType.FABRIC],
    )
    sc = score_clip(c)
    print(f"{sc.score:7.1f} learned={sc.score_breakdown.get('learned')} | {s}")

clips = [
    Clip(clip_id="a", text="家人们扣1点关注", t0_ms=0, t1_ms=2000, claim_types=[ClaimType.CHITCHAT]),
    Clip(clip_id="b", text="裙子颜色好显白", t0_ms=3000, t1_ms=6000, claim_types=[ClaimType.SELLING_POINT]),
    Clip(clip_id="c", text="版型显瘦收腰", t0_ms=7000, t1_ms=10000, claim_types=[ClaimType.FIT, ClaimType.SELLING_POINT]),
    Clip(clip_id="d", text="穿一下牛仔裤", t0_ms=11000, t1_ms=14000, claim_types=[ClaimType.OUTFIT]),
]
plan = build_timeline_plan(clips, Settings(target_duration_s=60, playback_speed=1.0))
print("\ngolden:", [s.text for s in plan.golden])
print("status:", json.dumps(learning_status(), ensure_ascii=False)[:500])
