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

# Fallback embedded lists if assets/exclude-lexicon.md is missing.
_FALLBACK_SIZE = [
    "尺码",
    "选码",
    "偏大",
    "偏小",
    "胸围",
    "腰围",
    "臀围",
    "身高",
    "穿M",
    "穿S",
    "穿L",
    "穿XL",
    "均码",
    "加大码",
    "码数",
    "建议穿",
]
_FALLBACK_SENTIMENT = [
    "做了五年",
    "不容易",
    "感谢陪伴",
    "创业",
    "初心",
    "故事是这样",
    "一路走来",
    "谢谢支持我",
    "喜欢我的人",
]
_FALLBACK_CHITCHAT = [
    "家人们",
    "老铁们",
    "听得到吗",
    "扣1",
    "扣一",
    "点点关注",
    "双击",
    "晚上好啊",
    "来了吗",
]


def _skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _parse_lexicon_section(body: str) -> list[str]:
    """Parse comma-separated markers from a markdown section body."""
    words: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        for part in line.split(","):
            w = part.strip()
            if w:
                words.append(w)
    return words


def load_lexicon() -> tuple[list[str], list[str], list[str]]:
    """Load SIZE/SENTIMENT/CHITCHAT from assets/exclude-lexicon.md sections.

    Headings matched (case-insensitive prefix): size, sentiment, chitchat.
    Falls back to embedded lists if the file is missing or a section empty.
    """
    path = _skill_root() / "assets" / "exclude-lexicon.md"
    size = list(_FALLBACK_SIZE)
    sentiment = list(_FALLBACK_SENTIMENT)
    chitchat = list(_FALLBACK_CHITCHAT)
    if not path.is_file():
        return size, sentiment, chitchat

    raw = path.read_text(encoding="utf-8")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal current, buf
        if current is not None:
            sections[current] = _parse_lexicon_section("\n".join(buf))
        current = None
        buf = []

    for line in raw.splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip().lower()
            if heading.startswith("size"):
                current = "size"
            elif heading.startswith("sentiment"):
                current = "sentiment"
            elif heading.startswith("chitchat"):
                current = "chitchat"
            else:
                current = None
            buf = []
        else:
            if current is not None:
                buf.append(line)
    flush()

    if sections.get("size"):
        size = sections["size"]
    if sections.get("sentiment"):
        sentiment = sections["sentiment"]
    if sections.get("chitchat"):
        chitchat = sections["chitchat"]
    return size, sentiment, chitchat


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
