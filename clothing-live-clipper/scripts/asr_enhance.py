"""
ASR accuracy helpers for Chinese clothing livestream.

Inspired by common open-source practices:
- faster-whisper + VAD (SYSTRAN/faster-whisper, OpenAI Whisper)
- domain prompt / hotwords (FunASR-style)
- segment merge & duration cleanup (whisperX / practical pipelines)
"""
from __future__ import annotations

import re
from typing import Any

# Domain prompt helps Whisper bias toward clothing terms (Chinese live selling)
CLOTHING_INITIAL_PROMPT = (
    "这是服装带货直播口播。重点词：面料、版型、显瘦、遮肉、收腰、修身、不透、"
    "柔软、天丝、醋酸、雪纺、纯棉、牛仔、蕾丝、破洞、拼接、领口、袖口、高腰、"
    "梨形、百搭、通勤、独家、专利、限定、凉感、抗皱、不起球、可机洗。"
    "不要把衣服讲成食物或故事。"
)

# Common ASR mis-hearings → clothing terms (substring replace, ordered longer first)
CLOTHING_CORRECTIONS: list[tuple[str, str]] = [
    ("雷丝", "蕾丝"),
    ("小雷丝", "小蕾丝"),
    ("马洗", "磨洗"),
    ("带谈", "带弹"),
    ("纯一车", "纯一扯"),  # soft fabric demo
    ("下天的面料", "夏天的面料"),
    ("小破洞牛肉", "小破洞牛仔"),
    ("破洞牛仔股", "破洞牛仔裤"),
    ("破洞单梦", "破洞单宁"),
    ("不破洞单梦", "不破洞单宁"),
    ("不破洞单位", "不破洞单宁"),
    ("暗通", "安通"),
    ("两块的面料", "凉快的面料"),
    ("356度", "360度"),
    ("吃了面料", "次的面料"),
    ("超简售", "超简约"),
    ("简售", "简约"),
    ("阴阳的马", "阴阳的码"),
    ("衣服人", "衣服里"),
    ("牛仔酷了", "牛仔裤了"),
    ("打一下牛仔", "搭一下牛仔"),
    ("洗得很热", "洗得很活"),
]


def apply_text_corrections(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    for bad, good in CLOTHING_CORRECTIONS:
        if bad in t:
            t = t.replace(bad, good)
    # collapse repeated punctuation / spaces
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"([，。！？、])\1+", r"\1", t)
    return t.strip()


def merge_close_segments(
    items: list[dict[str, Any]],
    *,
    max_gap_ms: int = 350,
    max_span_ms: int = 12000,
) -> list[dict[str, Any]]:
    """Merge adjacent short ASR crumbs into more natural sentences."""
    if not items:
        return []
    segs = sorted(
        [dict(u) for u in items if str(u.get("text") or "").strip()],
        key=lambda u: int(u.get("t0_ms") or 0),
    )
    out: list[dict[str, Any]] = []
    cur = segs[0]
    for nxt in segs[1:]:
        gap = int(nxt.get("t0_ms") or 0) - int(cur.get("t1_ms") or 0)
        span = int(nxt.get("t1_ms") or 0) - int(cur.get("t0_ms") or 0)
        cur_text = str(cur.get("text") or "")
        nxt_text = str(nxt.get("text") or "")
        # merge if close in time and not both already long
        if gap <= max_gap_ms and span <= max_span_ms and len(cur_text) < 40:
            cur["t1_ms"] = int(nxt.get("t1_ms") or cur["t1_ms"])
            joiner = "" if cur_text.endswith(("，", "。", "！", "？", ",", ".")) else "，"
            cur["text"] = cur_text + joiner + nxt_text
        else:
            out.append(cur)
            cur = nxt
    out.append(cur)
    for i, u in enumerate(out):
        u["utt_id"] = u.get("utt_id") or f"m{i:04d}"
        u["text"] = apply_text_corrections(str(u.get("text") or ""))
        # ensure order
        t0 = int(u.get("t0_ms") or 0)
        t1 = int(u.get("t1_ms") or 0)
        if t1 <= t0:
            u["t1_ms"] = t0 + 400
    return [u for u in out if str(u.get("text") or "").strip()]


def enhance_asr_segments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Post-process raw ASR for better clothing livestream usability."""
    fixed = []
    for i, u in enumerate(items or []):
        if not isinstance(u, dict):
            continue
        text = apply_text_corrections(str(u.get("text") or ""))
        if not text:
            continue
        t0 = max(0, int(u.get("t0_ms") or 0))
        t1 = max(t0 + 200, int(u.get("t1_ms") or (t0 + 1000)))
        fixed.append(
            {
                "utt_id": str(u.get("utt_id") or f"w{i:04d}"),
                "text": text,
                "t0_ms": t0,
                "t1_ms": t1,
            }
        )
    return merge_close_segments(fixed)
