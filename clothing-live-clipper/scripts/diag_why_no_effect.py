from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
jobs = ROOT / "output" / "web_jobs"

print("=== health ===")
try:
    with urllib.request.urlopen("http://127.0.0.1:8787/api/health", timeout=5) as r:
        h = json.loads(r.read().decode("utf-8"))
    print("ok", h.get("ok"), "llm_plan_ready", h.get("llm_plan_ready"), "llm_model", h.get("llm_model"))
    print("llm_note", h.get("llm_note"))
except Exception as e:
    print("health_error", e)

print("\n=== code markers ===")
rank = (ROOT / "src/clipper/rank.py").read_text(encoding="utf-8", errors="replace")
llm = (ROOT / "src/clipper/llm_plan.py").read_text(encoding="utf-8", errors="replace")
worker = (ROOT / "src/clipper/job_worker.py").read_text(encoding="utf-8", errors="replace")
print("rank has complete_logic", "complete_logic_no_cutoff" in rank or "dropped_incomplete_tail" in rank)
print("llm has main_points_first schema", "main_points" in llm and "all_clauses" in llm)
print("worker calls plan_from_asr_with_llm", "plan_from_asr_with_llm" in worker)
print("worker llm stage", "llm_plan" in worker)

print("\n=== recent jobs ===")
if not jobs.exists():
    print("no jobs dir")
else:
    dirs = sorted([p for p in jobs.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)[:6]
    for d in dirs:
        meta = {}
        mp = d / "job_meta.json"
        if mp.exists():
            meta = json.loads(mp.read_text(encoding="utf-8"))
        plan = {}
        if (d / "plan.json").exists():
            plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
        llm_p = d / "llm_plan.json"
        print("---", d.name)
        print(" video:", meta.get("video_source"))
        print(" status:", meta.get("status"), "planner:", meta.get("planner"), "llm_fallback:", meta.get("llm_fallback"))
        print(" llm_error:", (meta.get("llm_error") or "")[:160])
        print(" llm_summary:", (meta.get("llm_summary") or "")[:100])
        print(" warnings:", (meta.get("warnings") or plan.get("warnings") or [])[:8])
        print(" has_llm_plan:", llm_p.exists(), "has_final:", (d / "final.mp4").exists())
        g = plan.get("golden") or []
        print(" plan_segments:", len(g))
        for s in g[:3]:
            print("  G", s.get("t0_ms"), s.get("t1_ms"), str(s.get("text") or "")[:70])
        if llm_p.exists():
            try:
                obj = json.loads(llm_p.read_text(encoding="utf-8"))
                print(" main_points:", obj.get("main_points"))
                print(" hook_type:", obj.get("hook_type"))
                print(" keep_n:", len(obj.get("keep") or []))
                print(" meta:", obj.get("_meta"))
            except Exception as e:
                print(" llm_plan_read_error", e)
