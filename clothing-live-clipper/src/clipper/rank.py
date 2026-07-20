from __future__ import annotations

import re
from collections.abc import Iterable

from clipper.config import Settings
from clipper.extract import is_chitchat_text
from clipper.models import ClaimType, Clip, PlanSlot, TimelinePlan

# Simplified weights for MVP
SCORE_WEIGHTS = {
    ClaimType.SELLING_POINT: 40.0,
    ClaimType.FIT: 20.0,
    ClaimType.FABRIC: 20.0,
    ClaimType.PRICE: 15.0,
    ClaimType.DETAIL: 8.0,
    ClaimType.SCENE: 8.0,
    ClaimType.SIZE: 8.0,
    ClaimType.OUTFIT: 6.0,
    ClaimType.CHITCHAT: 0.0,
}


_CLOTHING_TEXT_HINTS = (
    "面料", "布料", "材质", "牛仔", "蕾丝", "雷丝", "不透", "柔软", "软到", "超软",
    "洗水", "破洞", "天丝", "醋酸", "显瘦", "遮肉", "版型", "收腰", "上衣", "裙子",
    "白色", "黑色", "口袋", "穿上", "上身", "这件", "这套", "裤子", "外套", "好看",
    "推荐", "客户", "软", "弹", "拼接",
)


def score_clip(clip: Clip) -> Clip:
    types = set(clip.claim_types)
    breakdown: dict[str, float] = {}
    raw = 0.0
    text = clip.text or ""

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

    content_types = {t for t in types if t != ClaimType.CHITCHAT and t != ClaimType.SIZE}
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


def _is_pure_filler(c: Clip) -> bool:
    if c.score <= 0:
        return True
    types = set(c.claim_types)
    if ClaimType.CHITCHAT in types and len(types) == 1:
        if not any(h in (c.text or "") for h in _CLOTHING_TEXT_HINTS):
            return True
    if is_chitchat_text(c.text) and not any(h in (c.text or "") for h in _CLOTHING_TEXT_HINTS):
        if not (types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC, ClaimType.PRICE}):
            return True
    return False


def _primary_stage(c: Clip) -> int:
    """Narrative stage index (logic order). Lower = earlier in story after hook."""
    types = set(c.claim_types)
    text = c.text or ""
    # 0 hook-ish: selling + fabric/fit combo feel
    if ClaimType.SELLING_POINT in types and (ClaimType.FABRIC in types or ClaimType.FIT in types):
        return 0
    if ClaimType.SELLING_POINT in types:
        return 0
    if ClaimType.FIT in types:
        return 1
    if ClaimType.FABRIC in types or any(k in text for k in ("面料", "牛仔", "蕾丝", "雷丝", "天丝", "软")):
        return 2
    if ClaimType.DETAIL in types or any(k in text for k in ("细节", "拼接", "口袋", "破洞")):
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
) -> list[PlanSlot]:
    """
    Pick clips with:
    1) narrative stage / preferred types (logic)
    2) score
    3) soft near-duplicate penalty (logic > hard non-repeat)
    4) light chronological continuity within section
    """
    slots: list[PlanSlot] = []
    remaining = budget_ms
    pool = [c for c in candidates if c.clip_id not in used and not _is_pure_filler(c)]
    if not pool or remaining <= 200:
        return slots

    stage_pref = set(prefer_stages or [])
    selected_texts: list[str] = []
    last_t0: int | None = None

    while remaining > 200 and pool:
        best: Clip | None = None
        best_key: tuple | None = None

        for c in pool:
            if c.duration_ms > remaining + 8000 and slots:
                continue

            types = set(c.claim_types)
            stage = _primary_stage(c)
            type_boost = 100.0 if prefer_types and (types & prefer_types) else 0.0
            stage_boost = 40.0 if stage in stage_pref else 0.0

            # soft dedupe: high similarity penalizes but does not hard-ban if needed for logic/duration
            sim = 0.0
            for prev in selected_texts:
                sim = max(sim, _similarity(c.text, prev))
            if sim >= 0.95:
                # almost exact repeat: only allow if remaining still large and logic needs it
                if not logic_over_dedupe or remaining < 8000:
                    continue
                dedupe_pen = 80.0
            elif sim >= dedupe_threshold:
                dedupe_pen = 25.0 + 40.0 * sim  # soft
            else:
                dedupe_pen = sim * 12.0

            # chronological continuity: prefer next-in-time after previous pick
            chrono = 0.0
            if last_t0 is not None:
                delta = c.t0_ms - last_t0
                if 0 <= delta <= 45000:
                    chrono = chronological_bias * (1.0 - min(delta, 45000) / 45000.0) * 30.0
                elif delta < 0:
                    chrono = -8.0  # jumping backward is less logical mid-section

            # role-specific stage pressure
            if role == "hook" and stage <= 2:
                stage_boost += 20.0
            if role == "trust" and 1 <= stage <= 4:
                stage_boost += 15.0
            if role == "cta" and stage >= 4:
                stage_boost += 25.0

            key = (
                type_boost + stage_boost + c.score + chrono - dedupe_pen,
                -stage if role != "cta" else stage,  # earlier stages first except CTA
                c.weight,
                -abs((last_t0 or c.t0_ms) - c.t0_ms),
            )
            if best is None or key > best_key:  # type: ignore[operator]
                best = c
                best_key = key

        if best is None:
            break

        # if highly similar to last clip and we still have alternatives later, skip once
        if selected_texts and _similarity(best.text, selected_texts[-1]) >= 0.88:
            # try find alternative with lower sim
            alt = None
            alt_key = None
            for c in pool:
                if c.clip_id == best.clip_id:
                    continue
                if c.duration_ms > remaining + 8000 and slots:
                    continue
                if _similarity(c.text, selected_texts[-1]) >= 0.88:
                    continue
                types = set(c.claim_types)
                type_boost = 100.0 if prefer_types and (types & prefer_types) else 0.0
                k = (type_boost + c.score, c.weight)
                if alt is None or k > alt_key:  # type: ignore[operator]
                    alt, alt_key = c, k
            if alt is not None and logic_over_dedupe:
                # only replace if alt isn't much weaker
                if alt.score >= best.score * 0.55:
                    best = alt

        slots.append(_to_slot(best, role))
        used.add(best.clip_id)
        selected_texts.append(best.text)
        last_t0 = best.t0_ms
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
        # price first, then supporting selling, keep relative time among ties
        return sorted(
            slots,
            key=lambda s: (
                0 if by_id.get(s.clip_id) and ClaimType.PRICE in by_id[s.clip_id].claim_types else 1,
                stage_of(s),
                s.t0_ms,
            ),
        )

    if role == "hook":
        # best hook content first (score), but group fabric/selling before weak lines
        return sorted(
            slots,
            key=lambda s: (
                stage_of(s),
                -(by_id[s.clip_id].score if s.clip_id in by_id else s.score),
                s.t0_ms,
            ),
        )

    # trust: stage ascending, within stage chronological for logic
    return sorted(slots, key=lambda s: (stage_of(s), s.t0_ms))


def build_timeline_plan(
    clips: list[Clip],
    settings: Settings | None = None,
) -> TimelinePlan:
    settings = settings or Settings()
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

    # --- Golden: strongest product hook, logical mini-arc ---
    golden = _pick_logical(
        scored,
        golden_ms,
        used,
        role="hook",
        prefer_types={
            ClaimType.SELLING_POINT,
            ClaimType.FIT,
            ClaimType.FABRIC,
            ClaimType.PRICE,
        },
        prefer_stages={0, 1, 2},
        dedupe_threshold=0.70,
        logic_over_dedupe=True,
        chronological_bias=0.25,
    )
    golden = _reorder_section_logical(golden, by_id, "hook")
    if not golden:
        warnings.append("no_golden_clips")

    # --- CTA: price / action, after body ---
    cta: list[PlanSlot] = []
    price_clips = sorted(
        [
            c
            for c in scored
            if c.clip_id not in used
            and c.score > 0
            and ClaimType.PRICE in c.claim_types
        ],
        key=lambda c: (c.score, -c.t0_ms),
        reverse=True,
    )
    remaining_cta = cta_ms
    if price_clips:
        # pick best price that isn't near-dup of golden texts
        gtexts = [s.text for s in golden]
        chosen_price = None
        for c in price_clips:
            if any(_similarity(c.text, g) >= 0.9 for g in gtexts):
                continue
            chosen_price = c
            break
        if chosen_price is None:
            chosen_price = price_clips[0]
        cta.append(_to_slot(chosen_price, "cta"))
        used.add(chosen_price.clip_id)
        remaining_cta = max(0, cta_ms - chosen_price.duration_ms)
    else:
        warnings.append("missing_price")

    cta.extend(
        _pick_logical(
            scored,
            remaining_cta,
            used,
            role="cta",
            prefer_types={ClaimType.PRICE, ClaimType.SELLING_POINT},
            prefer_stages={5, 0},
            dedupe_threshold=0.75,
            logic_over_dedupe=True,
            chronological_bias=0.2,
        )
    )
    cta = _reorder_section_logical(cta, by_id, "cta")

    # --- Trust: expand fabric → detail → outfit, soft dedupe, time flow ---
    trust = _pick_logical(
        scored,
        trust_ms,
        used,
        role="trust",
        prefer_types={
            ClaimType.DETAIL,
            ClaimType.FABRIC,
            ClaimType.SCENE,
            ClaimType.OUTFIT,
            ClaimType.FIT,
            ClaimType.SELLING_POINT,
        },
        prefer_stages={2, 3, 1, 4},
        dedupe_threshold=0.68,
        logic_over_dedupe=True,
        chronological_bias=0.45,
    )
    trust = _reorder_section_logical(trust, by_id, "trust")

    min_plan = getattr(settings, "source_min_plan_ms", None)
    max_plan = getattr(settings, "source_max_plan_ms", None)
    if min_plan is None:
        min_plan = int(round(getattr(settings, "min_plan_ms", 55_000) * speed))
    if max_plan is None:
        max_plan = int(round(getattr(settings, "max_plan_ms", 65_000) * speed))

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
            chronological_bias=0.5,
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
    coverage_ok = has_selling or has_fit_or_fabric or ClaimType.PRICE in golden_types

    if not has_selling and ClaimType.PRICE not in golden_types:
        warnings.append("golden_missing_selling_or_price")
    if not has_fit_or_fabric and has_selling:
        warnings.append("golden_missing_fit_or_fabric")

    plan.golden20_passed = bool(golden) and coverage_ok
    if not ratio_ok and plan.golden_weight_ratio < settings.golden_weight_ratio:
        warnings.append(
            f"golden_weight_ratio={plan.golden_weight_ratio:.2f}<{settings.golden_weight_ratio}"
        )

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
