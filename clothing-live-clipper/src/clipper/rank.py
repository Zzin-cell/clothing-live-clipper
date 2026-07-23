from __future__ import annotations

import re
from collections.abc import Iterable

from clipper.config import Settings
from clipper.extract import is_chitchat_text
from clipper.learning import learned_text_score
from clipper.models import ClaimType, Clip, PlanSlot, TimelinePlan

# Simplified weights for MVP
SCORE_WEIGHTS = {
    ClaimType.SELLING_POINT: 40.0,
    ClaimType.FIT: 20.0,
    ClaimType.FABRIC: 20.0,
    ClaimType.PRICE: 0.0,  # product policy: no price talk in cuts
    ClaimType.DETAIL: 8.0,
    ClaimType.SCENE: 8.0,
    ClaimType.SIZE: 8.0,
    ClaimType.OUTFIT: 6.0,
    ClaimType.CHITCHAT: 0.0,
}

_PRICE_TEXT = (
    "券后", "只要", "原价", "秒杀", "限时", "包邮", "拍下", "链接", "库存",
    "凑单", "满减", "到手", "块钱", "多少钱", "便宜", "加一捕", "加购", "下单",
    "小黄车", "购物车", "号链接", "弹窗", "福袋", "直播价", "专属价", "到手价",
)

# Hard size advice — never keep in final cut
_SIZE_TEXT = (
    "尺码", "选码", "偏大", "偏小", "腰围", "胸围", "臀围", "肩宽", "均码",
    "加大码", "码数", "建议穿", "该穿", "斤穿", "身高", "体重", "试码",
    "报尺码", "穿M", "穿S", "穿L", "穿XL", "穿XXL", "S码", "M码", "L码",
    "XL码", "XXL码", "袖长", "衣长", "裤长", "能穿吗", "能不能穿",
)

# Unique / rare product claims — rank to front of golden 20s
_UNIQUE_FEATURE_WORDS = (
    "独家", "独创", "专利", "首创", "限定", "限量", "仅此", "独一无二",
    "只有我们", "市面少见", "很少见", "别处没有", "买不到", "独家面料",
    "独家版型", "自研", "私模", "独家工艺", "独家设计", "独家配方",
    "全网首发", "首发", "仅此一家", "稀缺", "紧俏", "断码前",
    "三防", "防晒", "防水", "防风", "凉感", "冰丝", "醋酸", "真丝",
    "羊绒", "桑蚕丝", "四面弹", "360度", "不勒", "不卷边", "不起球",
    "不缩水", "不掉色", "免烫", "可机洗", "抗皱",
)


_CLOTHING_TEXT_HINTS = (
    "面料", "布料", "材质", "牛仔", "蕾丝", "雷丝", "不透", "柔软", "软到", "超软",
    "洗水", "破洞", "天丝", "醋酸", "显瘦", "遮肉", "版型", "收腰", "上衣", "裙子",
    "裤子", "外套", "内搭", "连衣裙", "衣服", "服装", "衬衫", "毛衣", "大衣", "风衣",
    "口袋", "穿上", "上身", "这件", "这套", "推荐", "软", "弹", "拼接", "领口", "袖口",
    "开叉", "高腰", "梨形", "闭眼入", "显白", "垂感", "透气",
)


def score_clip(clip: Clip) -> Clip:
    types = set(clip.claim_types)
    breakdown: dict[str, float] = {}
    raw = 0.0
    text = clip.text or ""

    # Hard policy: never put price / deal talk into final cut
    if ClaimType.PRICE in types or any(p in text for p in _PRICE_TEXT):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"price_excluded": 0.0, "raw": 0.0}
        return clip

    # Hard policy: never put size chart / sizing advice into final cut
    if ClaimType.SIZE in types or any(p in text for p in _SIZE_TEXT):
        # pure size always out; mixed size+feature still out (user: 去除尺码)
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"size_excluded": 0.0, "raw": 0.0}
        return clip

    if ClaimType.CHITCHAT in types and len(types) == 1:
        if not any(h in text for h in _CLOTHING_TEXT_HINTS):
            clip.score = 0.0
            clip.weight = 0.0
            clip.score_breakdown = {"chitchat": 0.0, "raw": 0.0}
            return clip

    if is_chitchat_text(clip.text) and not (
        types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC, ClaimType.PRICE}
    ) and not any(h in text for h in _CLOTHING_TEXT_HINTS):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"chitchat": 0.0, "raw": 0.0}
        return clip

    content_types = {
        t
        for t in types
        if t not in {ClaimType.CHITCHAT, ClaimType.SIZE, ClaimType.PRICE}
    }
    if not content_types:
        if any(h in text for h in _CLOTHING_TEXT_HINTS):
            raw = 14.0
            if len(text) >= 10:
                raw += 3.0
            if clip.duration_ms >= 2000:
                raw += 3.0
            breakdown["text_hint_rescue"] = raw
            breakdown["raw"] = raw
            clip.score = raw
            clip.score_breakdown = breakdown
            return clip
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"no_clothing_claim": 0.0, "raw": 0.0}
        return clip

    if content_types <= {ClaimType.SIZE}:
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"size_only": 0.0, "raw": 0.0}
        return clip

    if content_types <= {ClaimType.OUTFIT} and not any(h in text for h in _CLOTHING_TEXT_HINTS):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"outfit_only": 0.0, "raw": 0.0}
        return clip

    for t in content_types:
        w = SCORE_WEIGHTS.get(t, 0.0)
        breakdown[t.value] = w
        raw += w

    combo = 0.0
    if ClaimType.SELLING_POINT in content_types and (
        ClaimType.FIT in content_types or ClaimType.FABRIC in content_types
    ):
        combo = 10.0
        raw += combo
    breakdown["combo_bonus"] = combo

    if any(h in text for h in _CLOTHING_TEXT_HINTS):
        raw += 6.0
        breakdown["clothing_hint"] = 6.0

    spec = 0.0
    if any(ch.isdigit() for ch in clip.text):
        spec += 5.0
    if len(clip.text) >= 12:
        spec += 3.0
    if len(clip.text) >= 24:
        spec += 4.0
    raw += spec
    breakdown["specificity"] = spec

    dur = clip.duration_ms
    if 1500 <= dur <= 15000:
        raw += 4.0
        breakdown["duration_bonus"] = 4.0
    elif dur > 0:
        breakdown["duration_bonus"] = 1.0
        raw += 1.0
    else:
        breakdown["duration_bonus"] = 0.0

    # Plan D learning boost for general ranking (non-hook too)
    try:
        learned = learned_text_score(text, for_hook=False)
        if abs(learned) > 0.01:
            # amplify so it can override weak keyword ties
            learned_adj = learned * 1.8
            raw += learned_adj
            breakdown["learned"] = learned_adj
    except Exception:
        pass

    breakdown["raw"] = raw
    clip.score = raw
    clip.score_breakdown = breakdown
    return clip


def score_all(clips: list[Clip]) -> list[Clip]:
    scored = [score_clip(c.model_copy(deep=True)) for c in clips]
    positives = [c.score for c in scored if c.score > 0]
    if not positives:
        for c in scored:
            c.weight = 0.0
        return scored
    lo, hi = min(positives), max(positives)
    span = hi - lo if hi > lo else 1.0
    for c in scored:
        c.weight = 0.0 if c.score <= 0 else (c.score - lo) / span
    return scored


def _norm_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    # collapse repeated punctuation/chars noise from ASR
    t = re.sub(r"(.)\1{2,}", r"\1\1", t)
    return t


def _token_set(text: str) -> set[str]:
    # rough CJK bigrams + alnum words
    t = _norm_text(text)
    if not t:
        return set()
    toks: set[str] = set()
    for m in re.finditer(r"[a-z0-9]+|[\u4e00-\u9fff]{1,2}", t):
        toks.add(m.group(0))
    # also add overlapping bigrams for CJK
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", t)
    for i in range(len(cjk) - 1):
        toks.add(cjk[i : i + 2])
    return toks


def _similarity(a: str, b: str) -> float:
    """0–1 soft similarity; high means near-duplicate."""
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb) or 1
    jacc = inter / union
    # boost if share long common substring
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    substr = 0.0
    if len(shorter) >= 4:
        for n in (6, 5, 4):
            if len(shorter) < n:
                continue
            for i in range(0, len(shorter) - n + 1, max(1, n // 2)):
                if shorter[i : i + n] in longer:
                    substr = max(substr, n / max(len(longer), 1))
                    break
    return max(jacc, substr)


_LIVE_ROOM_MARKERS = (
    "家人们", "老铁", "宝宝们", "姐妹们", "宝贝们", "直播间", "扣1", "扣一",
    "点关注", "双击", "刷波", "公屏", "弹幕", "福袋", "连麦", "上链接",
    "小黄车", "欢迎进来", "新进来", "听得到", "在不在", "来了吗", "过一下",
)


def _looks_like_live_room(text: str) -> bool:
    t = text or ""
    hits = sum(1 for w in _LIVE_ROOM_MARKERS if w in t)
    if hits >= 1 and not any(h in t for h in _CLOTHING_TEXT_HINTS):
        return True
    if hits >= 2:
        return True
    if re.search(r"(扣|点|刷).{0,2}(1|一|关注)", t):
        return True
    return False


def _is_pure_filler(c: Clip) -> bool:
    if c.score <= 0:
        return True
    text = c.text or ""
    types = set(c.claim_types)
    if _looks_like_live_room(text) and not (
        types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC}
    ):
        return True
    if ClaimType.CHITCHAT in types and len(types) == 1:
        if not any(h in text for h in _CLOTHING_TEXT_HINTS):
            return True
    if is_chitchat_text(c.text) and not any(h in text for h in _CLOTHING_TEXT_HINTS):
        if not (types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC, ClaimType.PRICE}):
            return True
    return False


# True product FEATURES for first ~20s only (not outfit / try-on / change clothes)
_HOOK_FEATURE_WORDS = (
    "显瘦", "遮肉", "遮胯", "不透", "柔软", "超软", "软到", "软的", "超级软",
    "闭眼入", "垂感", "弹力", "不起球", "透气", "显白", "收腰", "修身",
    "面料", "布料", "材质", "天丝", "醋酸", "凉感", "雪纺", "纯棉",
    "版型", "高腰", "梨形", "显腿长", "不挑人", "好打理", "可机洗",
)

# Outfit / change-look / try-on → keep for later body, NOT golden 20s
_OUTFIT_CHANGE_WORDS = (
    "搭配", "换装", "换上", "换件", "换一个", "下一件", "再穿", "套装",
    "一整套", "穿一下", "打一下", "试穿", "上身看看", "搭个", "配个",
    "牛仔裤", "小白鞋", "内搭", "外套怎么", "怎么搭", "破洞牛仔", "破洞牛",
    "小破洞", "你的衣服里", "衣服人",
)


def _is_outfit_or_change(c: Clip) -> bool:
    """Outfit / try-on / change-clothes talk should not lead the first 20s."""
    text = c.text or ""
    types = set(c.claim_types)
    if ClaimType.OUTFIT in types or ClaimType.SCENE in types:
        # still allow if it is mainly a strong fabric/selling feature line
        if ClaimType.SELLING_POINT in types or ClaimType.FABRIC in types or ClaimType.FIT in types:
            # e.g. 面料软 + 搭配 → feature first if has strong feature words
            if any(w in text for w in _HOOK_FEATURE_WORDS):
                return False
        return True
    if any(w in text for w in _OUTFIT_CHANGE_WORDS):
        # pure try-on / change / match without clear product feature
        if not any(w in text for w in _HOOK_FEATURE_WORDS):
            return True
        # "穿一下牛仔裤" style → outfit
        if re.search(r"(穿一下|打一下|试穿|换装|换上).{0,8}(牛仔|裤子|裙子|外套|上衣)", text):
            return True
        if "搭配" in text or "搭个" in text or "配个" in text:
            # matching talk after features
            if not any(w in text for w in ("显瘦", "遮肉", "不透", "面料", "版型", "柔软", "软到")):
                return True
    return False


def _is_true_feature(c: Clip) -> bool:
    """True clothing features for golden 20s."""
    if _is_outfit_or_change(c):
        return False
    types = set(c.claim_types)
    text = c.text or ""
    if types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC}:
        return True
    if any(w in text for w in _HOOK_FEATURE_WORDS):
        return True
    # detail alone is weaker; allow only with feature word
    if ClaimType.DETAIL in types and any(w in text for w in ("蕾丝", "雷丝", "拼接", "面料", "不透")):
        return True
    return False


def _unique_feature_boost(text: str) -> float:
    """Unique / scarce product claims go first among features."""
    t = text or ""
    hits = [w for w in _UNIQUE_FEATURE_WORDS if w in t]
    if not hits:
        return 0.0
    # stronger boost for exclusivity words
    exclusivity = ("独家", "独创", "专利", "首创", "独一无二", "只有我们", "别处没有", "全网首发", "限量")
    bonus = 0.0
    for w in hits:
        bonus += 28.0 if any(e in w or w in e for e in exclusivity) else 16.0
    return min(90.0, bonus)


def _hook_strength(c: Clip) -> float:
    """Front 20s score: attractive product claims only; no live-room feel."""
    types = set(c.claim_types)
    text = c.text or ""
    # hard ban outfit/change / size / live-room from front 20s ranking
    if _looks_like_live_room(text):
        return -150.0
    if _is_outfit_or_change(c):
        return -100.0
    if ClaimType.SIZE in types or any(p in text for p in _SIZE_TEXT):
        return -120.0
    if ClaimType.PRICE in types or any(p in text for p in _PRICE_TEXT):
        return -120.0

    s = 0.0
    if ClaimType.SELLING_POINT in types:
        s += 60.0
    if ClaimType.FIT in types:
        s += 34.0
    if ClaimType.FABRIC in types:
        s += 36.0
    if ClaimType.DETAIL in types:
        s += 8.0  # detail secondary even in golden

    hits = sum(1 for w in _HOOK_FEATURE_WORDS if w in text)
    s += min(45.0, hits * 10.0)

    # UNIQUE features float to the very front (吸引力核心)
    uniq = _unique_feature_boost(text)
    s += uniq * 1.25

    if ClaimType.SELLING_POINT in types and (ClaimType.FIT in types or ClaimType.FABRIC in types):
        s += 28.0
    # high-attraction concrete benefits
    if any(w in text for w in ("不透", "显瘦", "遮肉", "软到", "超级软", "面料", "版型", "收腰", "闭眼入", "梨形")):
        s += 22.0
    if any(w in text for w in ("独家", "专利", "限定", "首创", "凉感", "不起球", "可机洗", "抗皱")):
        s += 26.0

    # demote vague praise / demo filler
    if "好看" in text and hits == 0 and ClaimType.SELLING_POINT not in types:
        s -= 55.0
    if re.search(r"穿一下|打一下|试穿|换装", text):
        s -= 45.0
    if not _is_true_feature(c):
        s -= 70.0

    # Plan D: human feedback memory (what you kept/dropped/hooked)
    try:
        # stronger on hook path: this decides front 20s order
        s += learned_text_score(text, for_hook=True) * 2.2
    except Exception:
        pass

    s += c.score * 0.28
    return s


def _primary_stage(c: Clip) -> int:
    """Narrative stage: 0=feature hook, then fit/fabric, detail, outfit last."""
    types = set(c.claim_types)
    text = c.text or ""
    # outfit / change always late body
    if _is_outfit_or_change(c):
        return 4
    if ClaimType.SELLING_POINT in types and (ClaimType.FABRIC in types or ClaimType.FIT in types):
        return 0
    if ClaimType.SELLING_POINT in types or any(
        w in text for w in ("显瘦", "遮肉", "不透", "软到", "超级软", "闭眼入")
    ):
        return 0
    if ClaimType.FIT in types or any(w in text for w in ("收腰", "修身", "版型", "高腰", "梨形")):
        return 1
    if ClaimType.FABRIC in types or any(
        k in text for k in ("面料", "材质", "布料", "天丝", "醋酸", "柔软", "软", "蕾丝", "雷丝")
    ):
        return 2
    if ClaimType.DETAIL in types or any(k in text for k in ("细节", "拼接", "口袋", "开叉", "领口")):
        return 3
    if ClaimType.OUTFIT in types or ClaimType.SCENE in types or "搭配" in text:
        return 4
    if ClaimType.PRICE in types:
        return 5
    return 3


def _to_slot(c: Clip, role: str) -> PlanSlot:
    return PlanSlot(
        clip_id=c.clip_id,
        role=role,
        t0_ms=c.t0_ms,
        t1_ms=c.t1_ms,
        text=c.text,
        score=c.score,
    )


def _pick_logical(
    candidates: list[Clip],
    budget_ms: int,
    used: set[str],
    role: str,
    *,
    prefer_types: set[ClaimType] | None = None,
    prefer_stages: Iterable[int] | None = None,
    dedupe_threshold: float = 0.72,
    logic_over_dedupe: bool = True,
    chronological_bias: float = 0.35,
    feature_first: bool = False,
    time_chain: bool = False,
) -> list[PlanSlot]:
    """
    Pick clips with:
    1) narrative stage / preferred types (logic)
    2) feature-first boost for golden 20s
    3) score
    4) soft near-duplicate penalty (logic > hard non-repeat)
    5) stronger chronological continuity to reduce jump-cut feel
    """
    slots: list[PlanSlot] = []
    remaining = budget_ms
    pool = [c for c in candidates if c.clip_id not in used and not _is_pure_filler(c)]
    if not pool or remaining <= 200:
        return slots

    stage_pref = set(prefer_stages or [])
    selected_texts: list[str] = []
    last_t0: int | None = None
    last_t1: int | None = None

    while remaining > 200 and pool:
        best: Clip | None = None
        best_key: tuple | None = None

        for c in pool:
            if c.duration_ms > remaining + 8000 and slots:
                continue

            types = set(c.claim_types)
            stage = _primary_stage(c)
            type_boost = 120.0 if prefer_types and (types & prefer_types) else 0.0
            stage_boost = 45.0 if stage in stage_pref else 0.0
            feature_boost = _hook_strength(c) if (feature_first or role == "hook") else 0.0

            # soft dedupe
            sim = 0.0
            for prev in selected_texts:
                sim = max(sim, _similarity(c.text, prev))
            if sim >= 0.95:
                if not logic_over_dedupe or remaining < 8000:
                    continue
                dedupe_pen = 80.0
            elif sim >= dedupe_threshold:
                dedupe_pen = 25.0 + 40.0 * sim
            else:
                dedupe_pen = sim * 12.0

            # chronological continuity (stronger when time_chain)
            chrono = 0.0
            bias = chronological_bias * (1.7 if time_chain else 1.0)
            if last_t1 is not None:
                gap = c.t0_ms - last_t1
                # prefer next nearby segment (like continuous live talk)
                if 0 <= gap <= 8000:
                    chrono = bias * 55.0
                elif 0 <= gap <= 25000:
                    chrono = bias * 35.0 * (1.0 - gap / 25000.0)
                elif 0 <= gap <= 60000:
                    chrono = bias * 12.0 * (1.0 - gap / 60000.0)
                elif gap < 0:
                    # jumping backward feels edited
                    chrono = -22.0 if time_chain else -10.0
                else:
                    chrono = -6.0 if time_chain else -2.0
            elif last_t0 is not None:
                delta = c.t0_ms - last_t0
                if 0 <= delta <= 45000:
                    chrono = bias * (1.0 - min(delta, 45000) / 45000.0) * 30.0
                elif delta < 0:
                    chrono = -10.0

            if role == "hook":
                # features MUST dominate first 20s
                stage_boost += 35.0 if stage <= 2 else -25.0
                feature_boost *= 1.35
            if role == "trust" and 1 <= stage <= 4:
                stage_boost += 15.0
            if role == "cta" and stage <= 2:
                stage_boost += 20.0  # recap features, not price

            key = (
                feature_boost + type_boost + stage_boost + c.score + chrono - dedupe_pen,
                -stage if role != "cta" else stage,
                c.weight,
                -abs((last_t1 or last_t0 or c.t0_ms) - c.t0_ms),
            )
            if best is None or key > best_key:  # type: ignore[operator]
                best = c
                best_key = key

        if best is None:
            break

        if selected_texts and _similarity(best.text, selected_texts[-1]) >= 0.88:
            alt = None
            alt_key = None
            for c in pool:
                if c.clip_id == best.clip_id:
                    continue
                if c.duration_ms > remaining + 8000 and slots:
                    continue
                if _similarity(c.text, selected_texts[-1]) >= 0.88:
                    continue
                # keep time continuity when replacing
                if last_t1 is not None and time_chain:
                    gap = c.t0_ms - last_t1
                    if gap < -5000 or gap > 90000:
                        continue
                types = set(c.claim_types)
                type_boost = 100.0 if prefer_types and (types & prefer_types) else 0.0
                k = (
                    (_hook_strength(c) if feature_first or role == "hook" else 0.0)
                    + type_boost
                    + c.score,
                    c.weight,
                )
                if alt is None or k > alt_key:  # type: ignore[operator]
                    alt, alt_key = c, k
            if alt is not None and logic_over_dedupe and alt.score >= best.score * 0.50:
                best = alt

        slots.append(_to_slot(best, role))
        used.add(best.clip_id)
        selected_texts.append(best.text)
        last_t0 = best.t0_ms
        last_t1 = best.t1_ms
        remaining -= best.duration_ms
        pool = [c for c in pool if c.clip_id not in used]

    return slots


def _reorder_section_logical(slots: list[PlanSlot], by_id: dict[str, Clip], role: str) -> list[PlanSlot]:
    """
    After selection, reorder within section for narrative flow.
    Logic > pure score order. Soft time continuity inside same stage.
    """
    if len(slots) <= 1:
        return slots

    def stage_of(s: PlanSlot) -> int:
        c = by_id.get(s.clip_id)
        return _primary_stage(c) if c else 3

    if role == "cta":
        # closing recap: selling/fabric first (no price)
        return sorted(
            slots,
            key=lambda s: (
                0
                if by_id.get(s.clip_id)
                and (
                    ClaimType.SELLING_POINT in by_id[s.clip_id].claim_types
                    or ClaimType.FABRIC in by_id[s.clip_id].claim_types
                )
                else 1,
                stage_of(s),
                s.t0_ms,
            ),
        )

    if role == "hook":
        # strongest features first in front 20s; within same strength keep time order
        return sorted(
            slots,
            key=lambda s: (
                -(_hook_strength(by_id[s.clip_id]) if s.clip_id in by_id else s.score),
                stage_of(s),
                s.t0_ms,
            ),
        )

    # trust/body: stage then chronological (less jump-cut)
    return sorted(slots, key=lambda s: (stage_of(s), s.t0_ms))


def build_timeline_plan(
    clips: list[Clip],
    settings: Settings | None = None,
) -> TimelinePlan:
    settings = settings or Settings()
    # GLOBAL policy flags (default ON for all jobs)
    features_only = bool(getattr(settings, "golden_features_only", True))
    demote_outfit = bool(getattr(settings, "demote_outfit_change_from_golden", True))
    scored = score_all(clips)
    by_id = {c.clip_id: c for c in scored}

    speed = getattr(settings, "playback_speed", 1.0) or 1.0
    if speed < 0.8:
        speed = 1.0
    source_target_s = getattr(settings, "source_select_duration_s", settings.target_duration_s)
    target_ms = int(source_target_s) * 1000
    golden_ms = int(round(settings.golden_s * 1000 * speed))
    cta_ms = int(round(settings.cta_s * 1000 * speed))
    trust_ms = max(0, target_ms - golden_ms - cta_ms)

    used: set[str] = set()
    warnings: list[str] = []
    if abs(speed - 1.0) > 0.01:
        warnings.append(f"source_select_for_speed={speed:.2f}x")

    # --- Golden (~front 20s final): ONLY true product features (GLOBAL) ---
    # Applies to every job (Web auto / CLI / reclip / batch). Not per-file.
    # No outfit / change-clothes / try-on in the first 20s when flags enabled.
    golden: list[PlanSlot] = []
    feature_pool = sorted(
        [
            c
            for c in scored
            if not _is_pure_filler(c)
            and c.score > 0
            and (
                _is_true_feature(c)
                if features_only
                else (c.score > 0)
            )
            and (not demote_outfit or not _is_outfit_or_change(c))
            and ClaimType.PRICE not in c.claim_types
            and not any(p in (c.text or "") for p in _PRICE_TEXT)
        ],
        key=_hook_strength,
        reverse=True,
    )
    seed_budget = golden_ms  # fill golden with features as much as possible
    seed_used = 0
    for c in feature_pool:
        if seed_used >= seed_budget:
            break
        if c.clip_id in used:
            continue
        if _hook_strength(c) < 20 and golden:
            # only pad with weaker features if golden still short
            if seed_used >= int(seed_budget * 0.55):
                break
        golden.append(_to_slot(c, "hook"))
        used.add(c.clip_id)
        seed_used += c.duration_ms
        if len(golden) >= 6:
            break

    # if still short, only allow more FIT/FABRIC/SELLING — never outfit/change
    remain_g = max(0, golden_ms - sum(s.t1_ms - s.t0_ms for s in golden))
    if remain_g > 400:
        more_pool = [
            c
            for c in scored
            if (not demote_outfit or not _is_outfit_or_change(c))
            and (not features_only or _is_true_feature(c))
        ]
        more = _pick_logical(
            more_pool,
            remain_g,
            used,
            role="hook",
            prefer_types={ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC},
            prefer_stages={0, 1, 2},
            dedupe_threshold=0.70,
            logic_over_dedupe=True,
            chronological_bias=0.45,
            feature_first=True,
            time_chain=True,
        )
        golden.extend(more)

    # GLOBAL hard filter on golden (every job)
    def _keep_in_golden(s: PlanSlot) -> bool:
        text = s.text or ""
        if any(p in text for p in _PRICE_TEXT) or any(p in text for p in _SIZE_TEXT):
            return False
        if _looks_like_live_room(text):
            return False
        c = by_id.get(s.clip_id)
        if c and ClaimType.PRICE in c.claim_types:
            return False
        if c and ClaimType.SIZE in c.claim_types:
            return False
        if demote_outfit and c and _is_outfit_or_change(c):
            return False
        if features_only and c and not _is_true_feature(c):
            return False
        return True

    golden = [s for s in golden if _keep_in_golden(s)]
    # order: UNIQUE features first, then other features
    golden = sorted(
        golden,
        key=lambda s: (
            -(_unique_feature_boost(s.text or "")),
            -(_hook_strength(by_id[s.clip_id]) if s.clip_id in by_id else s.score),
            s.t0_ms,
        ),
    )
    if not golden:
        warnings.append("no_golden_clips")
    else:
        if features_only:
            warnings.append("policy:golden_features_only")
        if demote_outfit:
            warnings.append("policy:outfit_change_after_20s")
        warnings.append("policy:unique_features_first")
        warnings.append("policy:size_excluded")
        warnings.append("policy:de_live_room_feel")
        warnings.append("policy:hook_attract_first")

    # --- CTA: NO price. Closing = remaining selling / fabric recap ---
    cta: list[PlanSlot] = []
    cta.extend(
        _pick_logical(
            scored,
            cta_ms,
            used,
            role="cta",
            prefer_types={ClaimType.SELLING_POINT, ClaimType.FABRIC, ClaimType.FIT},
            prefer_stages={0, 2, 1},
            dedupe_threshold=0.75,
            logic_over_dedupe=True,
            chronological_bias=0.35,
            feature_first=True,
            time_chain=True,
        )
    )
    cta = _reorder_section_logical(cta, by_id, "cta")
    cta = [
        s
        for s in cta
        if not any(p in (s.text or "") for p in _PRICE_TEXT)
        and not (
            s.clip_id in by_id and ClaimType.PRICE in by_id[s.clip_id].claim_types
        )
    ]

    # --- Trust: expand fabric → detail → outfit; stronger time chain ---
    # Trust body: outfit / change-clothes / try-on go HERE (after features)
    trust = _pick_logical(
        scored,
        trust_ms,
        used,
        role="trust",
        prefer_types={
            ClaimType.DETAIL,
            ClaimType.FABRIC,
            ClaimType.OUTFIT,
            ClaimType.SCENE,
            ClaimType.FIT,
            ClaimType.SELLING_POINT,
        },
        prefer_stages={2, 3, 4, 1},  # fabric/detail first, outfit later in body
        dedupe_threshold=0.68,
        logic_over_dedupe=True,
        chronological_bias=0.65,
        feature_first=False,
        time_chain=True,
    )
    trust = _reorder_section_logical(trust, by_id, "trust")
    # push pure outfit/change toward end of trust
    trust = sorted(
        trust,
        key=lambda s: (
            1 if (s.clip_id in by_id and _is_outfit_or_change(by_id[s.clip_id])) else 0,
            _primary_stage(by_id[s.clip_id]) if s.clip_id in by_id else 3,
            s.t0_ms,
        ),
    )

    min_plan = getattr(settings, "source_min_plan_ms", None)
    max_plan = getattr(settings, "source_max_plan_ms", None)
    if min_plan is None:
        # leave headroom for crossfades (~0.15–0.2s * cuts) before 1.3x speed
        min_plan = int(round(getattr(settings, "min_plan_ms", 55_000) * speed * 1.08))
    if max_plan is None:
        max_plan = int(round(getattr(settings, "max_plan_ms", 65_000) * speed * 1.10))

    def _plan_ms() -> int:
        return sum(s.t1_ms - s.t0_ms for s in [*golden, *trust, *cta])

    # fill remaining duration with logical next pieces (still soft-deduped)
    for _ in range(6):
        if _plan_ms() >= min_plan:
            break
        need = max_plan - _plan_ms()
        extra = _pick_logical(
            scored,
            max(need, 15000),
            used,
            role="trust",
            prefer_types=None,
            prefer_stages={2, 3, 4, 1, 0},
            dedupe_threshold=0.80,  # looser when filling duration (logic/duration > strict uniqueness)
            logic_over_dedupe=True,
            chronological_bias=0.7,
            feature_first=False,
            time_chain=True,
        )
        if not extra:
            leftover = [c for c in scored if c.clip_id not in used and c.score > 0 and not _is_pure_filler(c)]
            leftover = sorted(leftover, key=lambda c: (_primary_stage(c), c.t0_ms, -c.score))
            if not leftover:
                break
            for c in leftover:
                if _plan_ms() >= min_plan:
                    break
                # soft skip near-exact dups unless desperate for duration
                if any(_similarity(c.text, s.text) >= 0.93 for s in [*golden, *trust, *cta]):
                    if _plan_ms() > min_plan * 0.85:
                        continue
                trust.append(_to_slot(c, "trust"))
                used.add(c.clip_id)
            break
        trust.extend(extra)
        trust = _reorder_section_logical(trust, by_id, "trust")

    if _plan_ms() < min_plan:
        warnings.append(f"short_content_ms={_plan_ms()}")
    if _plan_ms() > max_plan + 5000:
        warnings.append(f"overlong_ms={_plan_ms()}")

    def _pad_slots(slots: list[PlanSlot], need_ms: int) -> None:
        if need_ms <= 0 or not slots:
            return
        per = max(100, need_ms // max(1, len(slots)))
        per = min(per, 800)
        for s in slots:
            s.t0_ms = max(0, s.t0_ms - per)
            s.t1_ms = s.t1_ms + per

    cur = _plan_ms()
    if cur < min_plan:
        _pad_slots([*golden, *trust, *cta], min_plan - cur + 500)
        cur2 = _plan_ms()
        if cur2 < min_plan:
            _pad_slots([*golden, *trust, *cta], min_plan - cur2 + 500)
        if _plan_ms() >= min_plan:
            warnings.append("duration_edge_padded")
        warnings[:] = [w for w in warnings if not str(w).startswith("short_content_ms=")]
        if _plan_ms() < min_plan:
            warnings.append(f"short_content_ms={_plan_ms()}")

    # final narrative polish: keep section roles, ensure trust chronological-ish by stage
    trust = _reorder_section_logical(trust, by_id, "trust")
    golden = _reorder_section_logical(golden, by_id, "hook")
    cta = _reorder_section_logical(cta, by_id, "cta")

    plan = TimelinePlan(
        target_duration_s=settings.target_duration_s,
        golden=golden,
        trust=trust,
        cta=cta,
        warnings=warnings,
    )

    all_slots = plan.all_slots()
    plan.total_duration_ms = sum(s.t1_ms - s.t0_ms for s in all_slots)

    selected_weights = [by_id[s.clip_id].weight for s in all_slots if s.clip_id in by_id]
    golden_weights = [by_id[s.clip_id].weight for s in golden if s.clip_id in by_id]
    total_w = sum(selected_weights) or 1.0
    golden_w = sum(golden_weights)
    plan.golden_weight_ratio = golden_w / total_w

    golden_types: set[ClaimType] = set()
    for s in golden:
        c = by_id.get(s.clip_id)
        if c:
            golden_types.update(c.claim_types)

    has_selling = ClaimType.SELLING_POINT in golden_types
    has_fit_or_fabric = bool(golden_types & {ClaimType.FIT, ClaimType.FABRIC})
    ratio_ok = plan.golden_weight_ratio >= settings.golden_weight_ratio or golden_w == 0
    coverage_ok = has_selling or has_fit_or_fabric

    if not has_selling and not has_fit_or_fabric:
        warnings.append("golden_missing_selling_or_fabric")
    if not has_fit_or_fabric and has_selling:
        warnings.append("golden_missing_fit_or_fabric")

    plan.golden20_passed = bool(golden) and coverage_ok
    if not ratio_ok and plan.golden_weight_ratio < settings.golden_weight_ratio:
        warnings.append(
            f"golden_weight_ratio={plan.golden_weight_ratio:.2f}<{settings.golden_weight_ratio}"
        )

    # Final safety: purge any remaining price lines from all sections
    def _no_price(slots: list[PlanSlot]) -> list[PlanSlot]:
        out: list[PlanSlot] = []
        for s in slots:
            if any(p in (s.text or "") for p in _PRICE_TEXT):
                continue
            c = by_id.get(s.clip_id)
            if c and ClaimType.PRICE in c.claim_types:
                continue
            out.append(s)
        return out

    golden = _no_price(golden)
    trust = _no_price(trust)
    cta = _no_price(cta)
    plan.golden, plan.trust, plan.cta = golden, trust, cta
    all_slots = plan.all_slots()
    plan.total_duration_ms = sum(s.t1_ms - s.t0_ms for s in all_slots)

    # report soft-dedupe stats
    texts = [s.text for s in all_slots]
    near = 0
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if _similarity(texts[i], texts[j]) >= 0.85:
                near += 1
    if near:
        warnings.append(f"near_dup_pairs={near}")

    plan.warnings = warnings

    score_map = {c.clip_id: c for c in scored}
    for i, c in enumerate(clips):
        sc = score_map.get(c.clip_id)
        if sc:
            clips[i].score = sc.score
            clips[i].weight = sc.weight
            clips[i].score_breakdown = sc.score_breakdown

    return plan
