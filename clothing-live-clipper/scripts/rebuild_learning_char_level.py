"""Rebuild learning preferences at char/phrase granularity from existing seed cases."""
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

    # 1) old positive-only seeds
    boot = ROOT / "output" / "learning_bootstrap"
    pos_dirs = [p for p in boot.iterdir() if p.is_dir() and p.name not in {"learn2_pairs"}] if boot.exists() else []
    n_pos = 0
    for d in pos_dirs:
        kept = d / "transcript_kept.json"
        asr = d / "transcript_asr.json"
        data = _load_json(kept) or _load_json(asr) or []
        if not isinstance(data, list) or not data:
            continue
        # only keep feature-ish
        after = {"golden": [], "trust": [], "cta": []}
        for i, u in enumerate(data[:12]):
            text = str((u or {}).get("text") or "").strip()
            if not text:
                continue
            after["golden" if i < 6 else "trust"].append(
                {
                    "clip_id": f"old_{i}",
                    "role": "hook" if i < 6 else "trust",
                    "text": text,
                    "t0_ms": int((u or {}).get("t0_ms") or 0),
                    "t1_ms": int((u or {}).get("t1_ms") or 1000),
                }
            )
        if after["golden"] or after["trust"]:
            record_plan_feedback(
                job_id=f"rebuild_pos::{d.name}",
                before_plan={"golden": [], "trust": [], "cta": []},
                after_plan=after,
                source="rebuild_char_level_pos",
            )
            n_pos += 1

    # 2) pos-neg pair seeds
    pair_root = boot / "learn2_pairs"
    n_pair = 0
    if pair_root.exists():
        for d in sorted([p for p in pair_root.iterdir() if p.is_dir()]):
            pos = _load_json(d / "pos" / "transcript_asr.json") or []
            neg = _load_json(d / "neg" / "transcript_asr.json") or []
            if not pos and not neg:
                continue
            after = {"golden": [], "trust": [], "cta": []}
            for i, u in enumerate(pos[:10]):
                text = str((u or {}).get("text") or "").strip()
                if not text:
                    continue
                after["golden" if i < 6 else "trust"].append(
                    {
                        "clip_id": f"p_{i}",
                        "role": "hook" if i < 6 else "trust",
                        "text": text,
                        "t0_ms": int((u or {}).get("t0_ms") or 0),
                        "t1_ms": int((u or {}).get("t1_ms") or 1000),
                    }
                )
            before = {"golden": [], "trust": [], "cta": []}
            for i, u in enumerate(neg[:20]):
                text = str((u or {}).get("text") or "").strip()
                if not text:
                    continue
                # put worst-ish into before so dropping them becomes penalty
                slot = {
                    "clip_id": f"n_{i}",
                    "role": "hook" if i < 5 else "trust",
                    "text": text,
                    "t0_ms": int((u or {}).get("t0_ms") or 0),
                    "t1_ms": int((u or {}).get("t1_ms") or 1000),
                }
                (before["golden"] if i < 5 else before["trust"]).append(slot)
            if after["golden"] or after["trust"] or before["golden"] or before["trust"]:
                record_plan_feedback(
                    job_id=f"rebuild_pair::{d.name}",
                    before_plan=before,
                    after_plan=after,
                    source="rebuild_char_level_pair",
                )
                n_pair += 1

    # strip any legacy single-char weights if present
    pref_path = ROOT / "output" / "learning" / "preferences.json"
    if pref_path.exists():
        prefs = json.loads(pref_path.read_text(encoding="utf-8"))
        for key in ("keep_boost", "drop_penalty", "hook_boost"):
            m = prefs.get(key) or {}
            prefs[key] = {k: v for k, v in m.items() if isinstance(k, str) and len(k) >= 2}
        pref_path.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")

    st = learning_status()
    print("rebuilt pos_dirs", n_pos, "pair_dirs", n_pair)
    print("events", st.get("events"), "kept", st.get("kept_slots"), "dropped", st.get("dropped_slots"))
    print("top_hook", st.get("top_hook")[:20])
    print("top_drop", st.get("top_drop")[:20])
    prefs = json.loads(pref_path.read_text(encoding="utf-8"))
    singles = [k for k in (prefs.get("hook_boost") or {}) if len(k) == 1]
    print("single_char_left", len(singles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
