# -*- coding: utf-8 -*-
"""Mechanical gate: fail if plan.json slot texts contain hard-exclude markers.

Primary contract: scan **plan.json** slot texts only (fields named ``text``).
Do not pass review.md — refuse with exit 2.

M1 mechanical = size + sentiment + chitchat via this checker on plan.json.
Agent still owns semantic mixed_keep (substring may FP on mixed lines —
prefer checking the filtered drop set). False LEAK on mixed_keep should not
auto-fail publish if claim-taxonomy mixed rule applies; re-check excluded.json
/ transcript_for_clipper and escalate need_review rather than blind fail.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lexicon import load_lexicon


def iter_texts(obj) -> list[str]:
    """Collect all string values under keys named ``text`` (plan slot texts)."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "text" and isinstance(v, str):
                out.append(v)
            else:
                out.extend(iter_texts(v))
    elif isinstance(obj, list):
        for x in obj:
            out.extend(iter_texts(x))
    return out


def find_leaks(texts: list[str], markers: list[str]) -> list[str]:
    hits: list[str] = []
    for t in texts:
        tl = t.lower()
        for w in markers:
            if w.lower() in tl:
                hits.append(f"{w} <= {t}")
    return hits


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: check_plan_exclusions.py <plan.json>\n"
            "Primary target: plan.json slot texts only.\n"
            "Do not pass review.md — refuse (exit 2)."
        )
        return 2

    path = Path(argv[1])
    suffix = path.suffix.lower()

    if suffix == ".md" or path.name.lower().endswith("review.md"):
        print(
            "REFUSE: scan plan.json not review.md\n"
            "This checker only accepts plan.json (slot texts). "
            "Pass output/{job_id}/plan.json."
        )
        return 2

    if suffix != ".json":
        print(
            f"REFUSE: expected plan.json, got {path.name!r}\n"
            "Primary target is plan.json slot texts only."
        )
        return 2

    if not path.is_file():
        print(f"ERROR: file not found: {path}")
        return 2

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}")
        return 2

    size, sentiment, chitchat = load_lexicon()
    markers = size + sentiment + chitchat
    texts = iter_texts(data)
    hits = find_leaks(texts, markers)
    if hits:
        print("LEAKS:")
        for h in hits:
            print(" -", h)
        return 1
    print("OK: no hard-exclude leaks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
