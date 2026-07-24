from __future__ import annotations

import json
from pathlib import Path

from clipper.learning import LEARN_DIR, PREF_PATH, learned_text_score, learning_status, load_preferences
from clipper.config import Settings
from clipper.models import ClaimType, Clip
from clipper.rank import build_timeline_plan, score_clip

print("PREF_PATH", PREF_PATH)
print("exists", PREF_PATH.exists(), "size", PREF_PATH.stat().st_size if PREF_PATH.exists() else 0)
st = learning_status()
print("events", st.get("events"), "kept", st.get("kept_slots"), "dropped", st.get("dropped_slots"))
print("top_hook", st.get("top_hook")[:10])
print("top_drop", st.get("top_drop")[:10])

samples = [
    ("pos", "裙子颜色好显白 面料软 版型显瘦"),
    ("pos2", "独家凉感面料显瘦不透"),
    ("neg", "家人们扣1点关注直播间有福袋"),
    ("neg2", "建议穿M码偏大选小一码 只要199"),
    ("mid", "穿一下牛仔裤看看搭配"),
]
print("\nlearned scores:")
for tag, s in samples:
    print(f"{tag:4} hook={learned_text_score(s, for_hook=True):7.2f} all={learned_text_score(s, for_hook=False):7.2f} | {s}")

print("\nscore_clip:")
for tag, s in samples:
    c = Clip(clip_id=tag, text=s, t0_ms=0, t1_ms=3000, claim_types=[ClaimType.SELLING_POINT, ClaimType.FABRIC])
    sc = score_clip(c)
    print(tag, sc.score, sc.score_breakdown.get("learned"), s)

# recent auto plans
jobs = Path("output/web_jobs")
if jobs.exists():
    dirs = sorted([p for p in jobs.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    print("\nrecent jobs:")
    for d in dirs:
        meta = {}
        if (d / "job_meta.json").exists():
            meta = json.loads((d / "job_meta.json").read_text(encoding="utf-8"))
        plan = {}
        if (d / "plan.json").exists():
            plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
        print("---", d.name, meta.get("status"), meta.get("video_source"))
        for s in (plan.get("golden") or [])[:3]:
            print("  G", s.get("text", "")[:80])
        warns = plan.get("warnings") or meta.get("warnings") or []
        print("  warnings", warns[:8])
