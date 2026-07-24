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


_GARBAGE_TOKENS = (
    "对", "嗯", "啊", "哦", "呃", "额", "呀", "哈", "嘿", "喂", "哎",
    "xy", "xx", "yy", "zzz", "hhh", "mmm", "um", "uh", "ah",
)


def _collapse_repeated_token_runs(text: str) -> str:
    """Collapse '对,对,对,对' / 'xy,xy,xy' style hallucination loops."""
    t = text
    # same token repeated with optional punctuation/space between
    # e.g. 对,对,对  or xy,xy,xy or 对对对对
    t = re.sub(
        r"([A-Za-z\u4e00-\u9fff]{1,4})(?:[\s,，、。.!！?？]*\1){2,}",
        r"\1",
        t,
    )
    # pure CJK single-char spam without separators: 对对对对对
    t = re.sub(r"([\u4e00-\u9fff])\1{2,}", r"\1", t)
    # latin spam: xxxxxx / yyyyy
    t = re.sub(r"([A-Za-z])\1{3,}", r"\1", t)
    return t


def is_garbage_asr_text(text: str) -> bool:
    """
    Detect whisper hallucination / looped filler that looks like:
      对,对,对,对...   xy,xy,xy...   嗯嗯嗯...
    """
    t = (text or "").strip()
    if not t:
        return True
    # strip punctuation/spaces for density checks
    core = re.sub(r"[\s,，、。.!！?？;；:：\-_/\\]+", "", t)
    if not core:
        return True
    # too short non-clothing crumbs
    if len(core) <= 1:
        return True

    # unique char ratio extremely low => spam
    uniq = set(core.lower())
    if len(core) >= 8 and len(uniq) <= 2:
        return True
    if len(core) >= 12 and len(uniq) <= 3:
        return True

    # mostly garbage tokens
    # split into tokens by punctuation
    parts = [p for p in re.split(r"[\s,，、。.!！?？;；]+", t) if p]
    if parts:
        garbage_n = 0
        for p in parts:
            pl = p.lower()
            if pl in _GARBAGE_TOKENS or re.fullmatch(r"(对|嗯|啊|哦|呃|额|哈|呀)+", p):
                garbage_n += 1
            elif re.fullmatch(r"[xy]{1,4}", pl):
                garbage_n += 1
        if len(parts) >= 3 and garbage_n / len(parts) >= 0.7:
            return True

    # if after collapse almost nothing remains
    collapsed = _collapse_repeated_token_runs(t)
    collapsed_core = re.sub(r"[\s,，、。.!！?？;；:：\-_/\\]+", "", collapsed)
    if len(core) >= 10 and len(collapsed_core) <= 2:
        return True

    # "对" density
    if core.count("对") >= 8 and core.count("对") / max(1, len(core)) >= 0.5:
        return True
    # pure filler mono-token after collapse (嗯/啊...) with almost no content
    if re.fullmatch(r"(嗯|啊|哦|呃|额|哈|呀|嘿|喂|哎)+", collapsed_core or core):
        return True
    # xy density
    if re.fullmatch(r"[xyXY,，、\s]+", t) and len(core) >= 4:
        return True

    return False


def apply_text_corrections(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    # first kill loop spam then lexicon fixes
    t = _collapse_repeated_token_runs(t)
    for bad, good in CLOTHING_CORRECTIONS:
        if bad in t:
            t = t.replace(bad, good)
    # collapse repeated punctuation / spaces
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"([，。！？、])\1+", r"\1", t)
    t = re.sub(r"(，){2,}", "，", t)
    return t.strip("，,。.!！?？ \t")


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
    dropped = 0
    for i, u in enumerate(items or []):
        if not isinstance(u, dict):
            continue
        raw_text = str(u.get("text") or "")
        if is_garbage_asr_text(raw_text):
            dropped += 1
            continue
        text = apply_text_corrections(raw_text)
        if not text or is_garbage_asr_text(text):
            dropped += 1
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
    # if everything got dropped, keep original non-empty to avoid empty pipeline
    if not fixed and items:
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
                    "asr_garbage_fallback": True,
                }
            )
            break
    merged = merge_close_segments(fixed)
    # final pass: drop any still-spam after merge
    cleaned = []
    for u in merged:
        if is_garbage_asr_text(str(u.get("text") or "")):
            dropped += 1
            continue
        cleaned.append(u)
    if dropped:
        # stash metric on first item for optional debug
        if cleaned:
            cleaned[0]["_asr_dropped_garbage"] = dropped
    return cleaned or merged
