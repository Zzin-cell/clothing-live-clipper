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
    # wear experience
    "舒服", "舒适", "贴肤", "亲肤", "冰冰的", "凉凉的", "不闷", "不闷汗",
    "凉快", "轻盈", "松弛", "好穿", "穿着舒服", "上身舒服",
)

# Outfit / change-look / try-on → keep for later body, NOT golden 20s
_OUTFIT_CHANGE_WORDS = (
    "搭配", "换装", "换上", "换件", "换一个", "下一件", "再穿", "套装",
    "一整套", "穿一下", "打一下", "试穿", "上身看看", "搭个", "配个",
    "牛仔裤", "小白鞋", "内搭", "外套怎么", "怎么搭", "破洞牛仔", "破洞牛",
    "小破洞", "你的衣服里", "衣服人",
)

# Wear experience phrases — ALLOW in final cut (often trust, sometimes golden)
_WEAR_EXPERIENCE_WORDS = (
    "舒服", "舒适", "贴肤", "亲肤", "冰冰的", "凉凉的", "不闷", "不闷汗",
    "透气", "凉快", "轻盈", "松弛", "好穿", "穿着舒服", "上身舒服",
    "一整个夏天", "一整天", "不勒肉", "不磨", "软软的", "遮盖", "体感",
    "上身感", "手感", "质感",
)


def _is_wear_experience(c: Clip) -> bool:
    text = c.text or ""
    return any(w in text for w in _WEAR_EXPERIENCE_WORDS)


def _is_outfit_or_change(c: Clip) -> bool:
    """Outfit / try-on / change-clothes talk should not lead the first 20s.

    Wear-experience talk (舒服/贴肤/不闷) is NOT treated as pure outfit ban —
    it can stay in final cut (usually trust section).
    """
    text = c.text or ""
    types = set(c.claim_types)
    wear = _is_wear_experience(c)

    if ClaimType.OUTFIT in types or ClaimType.SCENE in types:
        # still allow if mainly feature / wear experience
        if ClaimType.SELLING_POINT in types or ClaimType.FABRIC in types or ClaimType.FIT in types:
            if any(w in text for w in _HOOK_FEATURE_WORDS) or wear:
                return False
        if wear:
            return False
        return True
    if any(w in text for w in _OUTFIT_CHANGE_WORDS):
        # pure try-on / change / match without clear product feature or wear feel
        if any(w in text for w in _HOOK_FEATURE_WORDS) or wear:
            # "穿一下牛仔裤" style without feature → still outfit
            if re.search(r"(穿一下|打一下|试穿|换装|换上).{0,8}(牛仔|裤子|裙子|外套|上衣)", text) and not wear:
                return True
            if ("搭配" in text or "搭个" in text or "配个" in text) and not any(
                w in text for w in ("显瘦", "遮肉", "不透", "面料", "版型", "柔软", "软到", "舒服", "透气", "凉快")
            ):
                return True
            return False
        return True
    return False


def _is_true_feature(c: Clip) -> bool:
    """True clothing features for golden 20s (includes wear experience)."""
    if _is_outfit_or_change(c) and not _is_wear_experience(c):
        return False
    types = set(c.claim_types)
    text = c.text or ""
    if types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC}:
        return True
    if any(w in text for w in _HOOK_FEATURE_WORDS):
        return True
    if _is_wear_experience(c):
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
    # wear experience can support golden when paired with product talk
    if _is_wear_experience(c):
        s += 14.0
        if any(w in text for w in ("面料", "版型", "显瘦", "不透", "软", "透气", "凉")):
            s += 10.0

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


def _eligible(c: Clip) -> bool:
    """Global keep rules for story plan (no price/size/live filler)."""
    if c.score <= 0 or _is_pure_filler(c):
        return False
    text = c.text or ""
    if ClaimType.PRICE in c.claim_types or any(p in text for p in _PRICE_TEXT):
        return False
    if ClaimType.SIZE in c.claim_types or any(p in text for p in _SIZE_TEXT):
        return False
    if _looks_like_live_room(text) and not _is_true_feature(c) and not _is_wear_experience(c):
        return False
    return True


def _logic_order_key(c: Clip) -> tuple:
    """
    Story logic inspired by good sample shorts:
    1) open with unique/selling/fabric hook
    2) fit / structure
    3) wear experience proof
    4) detail
    5) outfit/match later
    """
    stage = _primary_stage(c)
    if _is_wear_experience(c) and stage > 2:
        stage = 2
    if _is_outfit_or_change(c):
        stage = max(stage, 4)
    uniq = -_unique_feature_boost(c.text or "")
    hook = -_hook_strength(c)
    try:
        learn = -learned_text_score(c.text or "", for_hook=(stage <= 2))
    except Exception:
        learn = 0.0
    return (stage, uniq, learn, hook, -c.score, c.t0_ms)


def build_timeline_plan(
    clips: list[Clip],
    settings: Settings | None = None,
) -> TimelinePlan:
    """
    Single logical storyline plan (NO forced 黄金/信任/收尾 buckets).

    Select product-useful clips, order by narrative logic + soft time continuity,
    store as one sequence in `golden` with role=story (trust/cta empty for compat).
    """
    settings = settings or Settings()
    scored = score_all(clips)
    by_id = {c.clip_id: c for c in scored}

    speed = getattr(settings, "playback_speed", 1.0) or 1.0
    if speed < 0.8:
        speed = 1.0
    source_target_s = getattr(settings, "source_select_duration_s", settings.target_duration_s)
    target_ms = int(source_target_s) * 1000

    warnings: list[str] = ["policy:logic_storyline"]
    if abs(speed - 1.0) > 0.01:
        warnings.append(f"source_select_for_speed={speed:.2f}x")

    pool = [c for c in scored if _eligible(c)]
    core = [c for c in pool if _is_true_feature(c) or _is_wear_experience(c) or c.score >= 12]
    if len(core) < 3:
        core = pool[:]

    min_plan = getattr(settings, "source_min_plan_ms", None)
    max_plan = getattr(settings, "source_max_plan_ms", None)
    if min_plan is None:
        min_plan = int(round(getattr(settings, "min_plan_ms", 55_000) * speed * 1.05))
    if max_plan is None:
        max_plan = int(round(getattr(settings, "max_plan_ms", 65_000) * speed * 1.10))
    aim = max(min_plan, min(max_plan, target_ms))

    ordered = sorted(core, key=_logic_order_key)

    selected: list[Clip] = []
    used: set[str] = set()
    total = 0
    last_t1: int | None = None
    selected_texts: list[str] = []

    openers = [c for c in ordered if _primary_stage(c) <= 2 and not _is_outfit_or_change(c)]
    if not openers:
        openers = ordered[:1]
    if openers:
        first = openers[0]
        selected.append(first)
        used.add(first.clip_id)
        total += first.duration_ms
        last_t1 = first.t1_ms
        selected_texts.append(first.text or "")

    while total < aim and len(selected) < 16:
        best = None
        best_key = None
        for c in ordered:
            if c.clip_id in used:
                continue
            sim = max((_similarity(c.text or "", p) for p in selected_texts), default=0.0)
            if sim >= 0.92 and total > aim * 0.55:
                continue
            stage = _primary_stage(c)
            stage_pen = 30.0 if (_is_outfit_or_change(c) and total < aim * 0.45) else 0.0
            chrono = 0.0
            if last_t1 is not None:
                gap = c.t0_ms - last_t1
                if 0 <= gap <= 12000:
                    chrono = 40.0 * (1.0 - gap / 12000.0)
                elif 0 <= gap <= 45000:
                    chrono = 15.0 * (1.0 - gap / 45000.0)
                elif gap < 0:
                    chrono = -18.0
                else:
                    chrono = -4.0
            try:
                learn = learned_text_score(c.text or "", for_hook=(total < aim * 0.35))
            except Exception:
                learn = 0.0
            progress = total / max(1, aim)
            desired = 0 if progress < 0.25 else 1 if progress < 0.45 else 2 if progress < 0.7 else 3
            stage_fit = -abs(stage - desired) * 8.0
            key = (
                c.score
                + learn
                + chrono
                + stage_fit
                + _hook_strength(c) * (0.35 if total < aim * 0.4 else 0.12)
                - stage_pen
                - sim * 20.0
            )
            if best is None or key > best_key:  # type: ignore[operator]
                best, best_key = c, key
        if best is None:
            break
        selected.append(best)
        used.add(best.clip_id)
        total += best.duration_ms
        last_t1 = best.t1_ms
        selected_texts.append(best.text or "")

    if total < min_plan:
        leftovers = [c for c in ordered if c.clip_id not in used]
        for c in leftovers:
            if total >= min_plan:
                break
            if any(_similarity(c.text or "", p) >= 0.93 for p in selected_texts) and total > min_plan * 0.8:
                continue
            selected.append(c)
            used.add(c.clip_id)
            total += c.duration_ms
            selected_texts.append(c.text or "")

    if len(selected) >= 2:
        head = selected[0]
        rest = sorted(selected[1:], key=lambda c: (_logic_order_key(c)[0], c.t0_ms))
        selected = [head, *rest]

    def _plan_ms(items: list[Clip]) -> int:
        return sum(c.duration_ms for c in items)

    while _plan_ms(selected) > max_plan and len(selected) > 3:
        drop_i = max(
            range(1, len(selected)),
            key=lambda i: (1 if _is_outfit_or_change(selected[i]) else 0, -selected[i].score),
        )
        selected.pop(drop_i)

    story = [
        PlanSlot(
            clip_id=c.clip_id,
            role="story",
            t0_ms=c.t0_ms,
            t1_ms=c.t1_ms,
            text=c.text,
            score=c.score,
        )
        for c in selected
    ]

    total = sum(s.t1_ms - s.t0_ms for s in story)
    if total < min_plan and story:
        need = min_plan - total
        story[-1].t1_ms += min(need, 2500)
        total = sum(s.t1_ms - s.t0_ms for s in story)
        warnings.append("duration_edge_padded")
    if total < min_plan:
        warnings.append(f"short_content_ms={total}")

    front_ms = int(round(settings.golden_s * 1000 * speed))
    acc = 0
    front_has_feature = False
    for s in story:
        acc += s.t1_ms - s.t0_ms
        c = by_id.get(s.clip_id)
        if c and (_is_true_feature(c) or _is_wear_experience(c)):
            front_has_feature = True
        if acc >= front_ms:
            break
    golden20_passed = bool(story) and front_has_feature
    if not story:
        warnings.append("no_story_clips")
    warnings.append("policy:size_excluded")
    warnings.append("policy:de_live_room_feel")
    warnings.append("policy:logic_over_sections")

    ratio = 1.0 if story else 0.0
    return TimelinePlan(
        target_duration_s=settings.target_duration_s,
        golden=story,
        trust=[],
        cta=[],
        total_duration_ms=total,
        golden_weight_ratio=ratio,
        golden20_passed=golden20_passed,
        warnings=warnings,
    )
