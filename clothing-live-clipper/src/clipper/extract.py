from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

from clipper.models import Claim, ClaimType, Clip, TranscriptUtterance

# Clothing livestream keyword lexicon (simple MVP rules)
LEXICON: dict[ClaimType, tuple[str, ...]] = {
    ClaimType.FIT: (
        "收腰", "修身", "oversize", "oversized", "a字", "a型", "h型", "廓形",
        "宽松", "紧身", "直筒", "高腰", "梨形", "显高", "版型",
    ),
    ClaimType.FABRIC: (
        "纯棉", "棉质", "真丝", "雪纺", "羊毛", "混纺", "凉感", "醋酸",
        "牛仔", "针织", "西装料", "天丝", "莫代尔", "面料", "布料", "材质",
        "不起球", "抗皱", "透气", "柔软",
    ),
    ClaimType.SELLING_POINT: (
        "显瘦", "遮肉", "遮胯", "遮肚子", "显腿长", "不挑人", "闭眼入",
        "必入", "百搭", "耐造", "好打理", "可机洗", "三防", "弹力",
        "不透", "垂感", "高级感", "显白", "瘦十斤", "瘦10斤",
    ),
    ClaimType.DETAIL: (
        "领口", "V领", "圆领", "袖口", "袖型", "下摆", "开叉", "口袋",
        "抽绳", "扣子", "拉链", "褶", "花边",
    ),
    ClaimType.SCENE: (
        "通勤", "上班", "约会", "度假", "逛街", "聚会", "孕妇", "四季",
    ),
    ClaimType.PRICE: (
        "券后", "只要", "原价", "秒杀", "限时", "包邮", "拍下", "链接",
        "库存", "凑单", "满减", "到手", "块钱",
        # 专属直播挂车 / CTA（勿单独加「左上角/右下角」——易与控场寒暄冲突）
        "小黄车", "购物车", "号链接", "1号链接", "2号链接", "3号链接",
        "4号链接", "5号链接", "几号链接", "弹窗", "挂上车", "上车了",
        "加购", "下单", "领券", "福袋", "专属价", "直播价", "到手价",
        "拍链接", "点链接", "戳链接", "上方链接", "下方链接",
    ),
    ClaimType.SIZE: (
        "尺码", "偏大", "偏小", "选码", "腰围", "胸围", "均码", "加大",
    ),
    ClaimType.OUTFIT: (
        "搭配", "配牛仔裤", "小白鞋", "西装裤", "半身裙", "内搭",
    ),
}

CHITCHAT_WORDS = (
    "家人们", "老铁们", "扣1", "扣一", "点点关注", "双击", "刷波",
    "来了吗", "听得到", "声音OK", "声音ok", "晚上好", "下午好",
    "谢谢老师", "左上角", "右下角", "关注主播",
)

# fabric list also contains quality words also used as selling points — OK to multi-label


def _contains_any(text: str, words: Iterable[str]) -> list[str]:
    lower = text.lower()
    hit = []
    for w in words:
        if w.lower() in lower:
            hit.append(w)
    return hit


def tag_utterance(utt: TranscriptUtterance) -> list[Claim]:
    text = utt.text.strip()
    if not text:
        return []

    claims: list[Claim] = []
    # Pure chitchat only when no clothing/CTA content (price CTA counts as content).
    has_content = any(_contains_any(text, LEXICON[t]) for t in LEXICON)
    if _contains_any(text, CHITCHAT_WORDS) and not has_content:
        claims.append(
            Claim(
                claim_id=f"c_{uuid.uuid4().hex[:8]}",
                type=ClaimType.CHITCHAT,
                text=text,
                t0_ms=utt.t0_ms,
                t1_ms=utt.t1_ms,
            )
        )
        return claims

    for ctype, words in LEXICON.items():
        if _contains_any(text, words):
            claims.append(
                Claim(
                    claim_id=f"c_{uuid.uuid4().hex[:8]}",
                    type=ctype,
                    text=text,
                    t0_ms=utt.t0_ms,
                    t1_ms=utt.t1_ms,
                )
            )
    return claims


def extract_claims(transcript: list[TranscriptUtterance]) -> list[Claim]:
    out: list[Claim] = []
    for utt in transcript:
        out.extend(tag_utterance(utt))
    return out


def utterances_to_clips(
    transcript: list[TranscriptUtterance],
    claims: list[Claim] | None = None,
    min_clip_ms: int = 500,
    max_clip_ms: int = 15_000,
) -> list[Clip]:
    """One clip per utterance (MVP). Filter extreme durations lightly."""
    claims = claims or extract_claims(transcript)
    by_span: dict[tuple[int, int], list[ClaimType]] = {}
    for c in claims:
        key = (c.t0_ms, c.t1_ms)
        by_span.setdefault(key, []).append(c.type)

    clips: list[Clip] = []
    for i, utt in enumerate(transcript):
        dur = utt.t1_ms - utt.t0_ms
        if dur < min_clip_ms:
            continue
        # Keep long lines but cap scoring window conceptually; still allow up to max
        if dur > max_clip_ms * 2:
            continue
        types = by_span.get((utt.t0_ms, utt.t1_ms), [])
        # de-dup preserve order
        seen: set[ClaimType] = set()
        ordered: list[ClaimType] = []
        for t in types:
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        clips.append(
            Clip(
                clip_id=f"clip_{i:04d}",
                t0_ms=utt.t0_ms,
                t1_ms=utt.t1_ms,
                text=utt.text.strip(),
                claim_types=ordered,
            )
        )
    return clips


def is_chitchat_text(text: str) -> bool:
    return bool(_contains_any(text, CHITCHAT_WORDS))


_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*")


def split_long_utterance(utt: TranscriptUtterance) -> list[TranscriptUtterance]:
    """Roughly split long text into sentence-like chunks with proportional times."""
    parts = [p.strip() for p in _SENT_SPLIT.split(utt.text) if p.strip()]
    if len(parts) <= 1:
        return [utt]
    total_chars = sum(len(p) for p in parts) or 1
    span = max(1, utt.t1_ms - utt.t0_ms)
    out: list[TranscriptUtterance] = []
    cursor = utt.t0_ms
    acc = 0
    for i, p in enumerate(parts):
        acc += len(p)
        if i == len(parts) - 1:
            end = utt.t1_ms
        else:
            end = utt.t0_ms + int(span * acc / total_chars)
        end = max(end, cursor + 200)
        out.append(
            TranscriptUtterance(
                utt_id=f"{utt.utt_id}_{i}",
                text=p,
                t0_ms=cursor,
                t1_ms=min(end, utt.t1_ms),
                confidence=utt.confidence,
            )
        )
        cursor = out[-1].t1_ms
    return out
