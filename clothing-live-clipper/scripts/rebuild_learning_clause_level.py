"""Rebuild learning preferences at 小句 (clause) granularity from existing seeds."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from clipper.learning import (  # type: ignore
    clear_learning,
    learning_status,
    record_plan_feedback,
    seed_negative_live_phrases,
    split_clauses,
)


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    print("clear old learning…")
    clear_learning(keep_events_backup=True)
    seed_negative_live_phrases()

    boot = ROOT / "output" / "learning_bootstrap"
    n_pos = 0
    if boot.exists():
        for d in [p for p in boot.iterdir() if p.is_dir() and p.name != "learn2_pairs"]:
            data = _load_json(d / "transcript_kept.json") or _load_json(d / "transcript_asr.json") or []
            if not isinstance(data, list) or not data:
                continue
            after = {"golden": [], "trust": [], "cta": []}
            # explode into clause-sized pseudo slots for stronger 小句 learning
            gi = ti = 0
            for u in data[:20]:
                text = str((u or {}).get("text") or "").strip()
                if not text:
                    continue
                clauses = split_clauses(text) or [text]
                t0 = int((u or {}).get("t0_ms") or 0)
                t1 = int((u or {}).get("t1_ms") or (t0 + 1000))
                span = max(800, (t1 - t0) // max(1, len(clauses)))
                cur = t0
                for j, c in enumerate(clauses[:6]):
                    nxt = t1 if j == min(5, len(clauses) - 1) else min(t1, cur + span)
                    slot = {
                        "clip_id": f"pos_{n_pos}_{gi}_{j}",
                        "role": "hook" if gi < 8 else "trust",
                        "text": c,
                        "t0_ms": cur,
                        "t1_ms": max(cur + 500, nxt),
                    }
                    if gi < 8:
                        after["golden"].append(slot)
                        gi += 1
                    else:
                        after["trust"].append(slot)
                        ti += 1
                    cur = nxt
            if after["golden"] or after["trust"]:
                record_plan_feedback(
                    job_id=f"clause_pos::{d.name}",
                    before_plan={"golden": [], "trust": [], "cta": []},
                    after_plan=after,
                    source="rebuild_clause_level_pos",
                )
                n_pos += 1

    n_pair = 0
    pair_root = boot / "learn2_pairs"
    if pair_root.exists():
        for d in sorted([p for p in pair_root.iterdir() if p.is_dir()]):
            pos = _load_json(d / "pos" / "transcript_asr.json") or []
            neg = _load_json(d / "neg" / "transcript_asr.json") or []
            after = {"golden": [], "trust": [], "cta": []}
            gi = 0
            for u in pos[:16]:
                text = str((u or {}).get("text") or "").strip()
                for j, c in enumerate((split_clauses(text) or [text])[:5]):
                    after["golden" if gi < 10 else "trust"].append(
                        {
                            "clip_id": f"cp_{gi}_{j}",
                            "role": "hook" if gi < 10 else "trust",
                            "text": c,
                            "t0_ms": int((u or {}).get("t0_ms") or 0),
                            "t1_ms": int((u or {}).get("t1_ms") or 1000),
                        }
                    )
                    gi += 1
            before = {"golden": [], "trust": [], "cta": []}
            ni = 0
            for u in neg[:30]:
                text = str((u or {}).get("text") or "").strip()
                for j, c in enumerate((split_clauses(text) or [text])[:4]):
                    before["golden" if ni < 8 else "trust"].append(
                        {
                            "clip_id": f"cn_{ni}_{j}",
                            "role": "hook" if ni < 8 else "trust",
                            "text": c,
                            "t0_ms": int((u or {}).get("t0_ms") or 0),
                            "t1_ms": int((u or {}).get("t1_ms") or 1000),
                        }
                    )
                    ni += 1
            if after["golden"] or after["trust"] or before["golden"] or before["trust"]:
                record_plan_feedback(
                    job_id=f"clause_pair::{d.name}",
                    before_plan=before,
                    after_plan=after,
                    source="rebuild_clause_level_pair",
                )
                n_pair += 1

    # remove accidental single-char leftovers
    pref_path = ROOT / "output" / "learning" / "preferences.json"
    prefs = json.loads(pref_path.read_text(encoding="utf-8"))
    for key in ("keep_boost", "drop_penalty", "hook_boost"):
        m = prefs.get(key) or {}
        prefs[key] = {k: v for k, v in m.items() if isinstance(k, str) and len(k) >= 2}
    pref_path.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")

    st = learning_status()
    print("rebuilt clause pos", n_pos, "pair", n_pair)
    print("events", st.get("events"), "kept", st.get("kept_slots"), "dropped", st.get("dropped_slots"))
    print("top_hook clauses:")
    for k, v in (st.get("top_hook") or [])[:15]:
        print(f"  {v:6.1f} | {k}")
    print("top_drop clauses:")
    for k, v in (st.get("top_drop") or [])[:15]:
        print(f"  {v:6.1f} | {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
