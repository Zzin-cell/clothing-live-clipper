from __future__ import annotations

import json
from pathlib import Path

from clipper.learning import learned_text_score, learning_status, load_preferences
from clipper.models import ClaimType, Clip
from clipper.rank import build_timeline_plan, score_clip
from clipper.config import Settings

print("=== learning status ===")
print(json.dumps(learning_status(), ensure_ascii=False, indent=2)[:1500])

prefs = load_preferences()
print("\npref_path", Path("output/learning/preferences.json").resolve())
print("events", (prefs.get("stats") or {}).get("events"))
print("top_hook", list((prefs.get("hook_boost") or {}).items())[:12])

samples = [
    "家人们扣1点关注直播间有福袋",
    "独家凉感面料显瘦不透",
    "收腰版型梨形闭眼入",
    "裙子颜色好显白",
    "穿一下牛仔裤看看",
    "建议穿M码偏大选小一码",
]
print("\n=== learned_text_score ===")
for s in samples:
    print(f"{learned_text_score(s, for_hook=True):7.2f} | {learned_text_score(s, for_hook=False):7.2f} | {s}")

print("\n=== score_clip breakdown ===")
for s in samples:
    c = Clip(clip_id="x", text=s, t0_ms=0, t1_ms=3000, claim_types=[ClaimType.SELLING_POINT, ClaimType.FABRIC])
    sc = score_clip(c)
    print(sc.score, sc.score_breakdown, s)

print("\n=== plan order smoke ===")
clips = [
    Clip(clip_id="a", text="家人们扣1点关注", t0_ms=0, t1_ms=2000, claim_types=[ClaimType.CHITCHAT]),
    Clip(clip_id="b", text="裙子颜色好显白", t0_ms=3000, t1_ms=6000, claim_types=[ClaimType.SELLING_POINT]),
    Clip(clip_id="c", text="版型显瘦收腰", t0_ms=7000, t1_ms=10000, claim_types=[ClaimType.FIT, ClaimType.SELLING_POINT]),
    Clip(clip_id="d", text="穿一下牛仔裤", t0_ms=11000, t1_ms=14000, claim_types=[ClaimType.OUTFIT]),
]
plan = build_timeline_plan(clips, Settings(target_duration_s=60, playback_speed=1.0))
print("golden", [s.text for s in plan.golden])
print("trust", [s.text for s in plan.trust])
print("warnings", plan.warnings)
