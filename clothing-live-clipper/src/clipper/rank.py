from __future__ import annotations

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


def score_clip(clip: Clip) -> Clip:
    types = set(clip.claim_types)
    breakdown: dict[str, float] = {}
    raw = 0.0

    if ClaimType.CHITCHAT in types and len(types) == 1:
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"chitchat": 0.0, "raw": 0.0}
        return clip

    if is_chitchat_text(clip.text) and not (
        types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC, ClaimType.PRICE}
    ):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"chitchat": 0.0, "raw": 0.0}
        return clip

    # No clothing claim → score 0 (do not pad plan with filler / off-topic)
    content_types = {t for t in types if t != ClaimType.CHITCHAT and t != ClaimType.SIZE}
    if not content_types:
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"no_clothing_claim": 0.0, "raw": 0.0}
        return clip

    # Size-only lines: never useful for cuts (hard exclude policy)
    if content_types <= {ClaimType.SIZE}:
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"size_only": 0.0, "raw": 0.0}
        return clip

    # Outfit-only weak lines (e.g. 搭配就可以了 without fabric/fit) → drop
    if content_types <= {ClaimType.OUTFIT}:
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"outfit_only": 0.0, "raw": 0.0}
        return clip

    for t in content_types:
        w = SCORE_WEIGHTS.get(t, 0.0)
        breakdown[t.value] = w
        raw += w

    # combo bonus: selling_point + (fit|fabric)
    combo = 0.0
    if ClaimType.SELLING_POINT in content_types and (
        ClaimType.FIT in content_types or ClaimType.FABRIC in content_types
    ):
        combo = 10.0
        raw += combo
    breakdown["combo_bonus"] = combo

    # specificity: has digits or material-ish length
    spec = 0.0
    if any(ch.isdigit() for ch in clip.text):
        spec += 5.0
    if len(clip.text) >= 12:
        spec += 3.0
    raw += spec
    breakdown["specificity"] = spec

    # prefer mid-length clips slightly
    dur = clip.duration_ms
    if 1500 <= dur <= 8000:
        raw += 3.0
        breakdown["duration_bonus"] = 3.0
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


def _pick_fill(
    candidates: list[Clip],
    budget_ms: int,
    used: set[str],
    role: str,
    prefer_types: set[ClaimType] | None = None,
    ban_chitchat: bool = True,
) -> list[PlanSlot]:
    slots: list[PlanSlot] = []
    remaining = budget_ms
    pool = [c for c in candidates if c.clip_id not in used and c.score > 0]
    if ban_chitchat:
        pool = [
            c
            for c in pool
            if ClaimType.CHITCHAT not in c.claim_types and not is_chitchat_text(c.text)
        ]

    def sort_key(c: Clip) -> tuple:
        boost = 0.0
        if prefer_types and set(c.claim_types) & prefer_types:
            boost = 100.0
        return (boost + c.score, c.weight)

    pool = sorted(pool, key=sort_key, reverse=True)

    for c in pool:
        if remaining <= 400:
            break
        # allow slight overshoot on last piece
        if c.duration_ms > remaining + 1500 and slots:
            continue
        take_ms = min(c.duration_ms, remaining + 1500)
        # always use full clip timestamps from source (no intra-clip trim in MVP)
        slots.append(
            PlanSlot(
                clip_id=c.clip_id,
                role=role,
                t0_ms=c.t0_ms,
                t1_ms=c.t1_ms,
                text=c.text,
                score=c.score,
            )
        )
        used.add(c.clip_id)
        remaining -= c.duration_ms
    return slots


def build_timeline_plan(
    clips: list[Clip],
    settings: Settings | None = None,
) -> TimelinePlan:
    settings = settings or Settings()
    scored = score_all(clips)
    by_id = {c.clip_id: c for c in scored}

    target_ms = settings.target_duration_s * 1000
    golden_ms = settings.golden_s * 1000
    cta_ms = settings.cta_s * 1000
    trust_ms = max(0, target_ms - golden_ms - cta_ms)

    used: set[str] = set()
    warnings: list[str] = []

    golden = _pick_fill(
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
        ban_chitchat=True,
    )
    if not golden:
        warnings.append("no_golden_clips")

    # CTA: force-include best price clip first when available
    cta: list[PlanSlot] = []
    price_clips = sorted(
        [
            c
            for c in scored
            if c.clip_id not in used
            and c.score > 0
            and ClaimType.PRICE in c.claim_types
        ],
        key=lambda c: c.score,
        reverse=True,
    )
    remaining_cta = cta_ms
    if price_clips:
        c = price_clips[0]
        cta.append(
            PlanSlot(
                clip_id=c.clip_id,
                role="cta",
                t0_ms=c.t0_ms,
                t1_ms=c.t1_ms,
                text=c.text,
                score=c.score,
            )
        )
        used.add(c.clip_id)
        remaining_cta = max(0, cta_ms - c.duration_ms)
    else:
        warnings.append("missing_price")

    cta.extend(
        _pick_fill(
            scored,
            remaining_cta,
            used,
            role="cta",
            prefer_types={ClaimType.PRICE, ClaimType.SELLING_POINT},
            ban_chitchat=True,
        )
    )

    trust = _pick_fill(
        scored,
        trust_ms,
        used,
        role="trust",
        prefer_types={
            ClaimType.DETAIL,
            ClaimType.FABRIC,
            ClaimType.SIZE,
            ClaimType.SCENE,
            ClaimType.OUTFIT,
            ClaimType.FIT,
        },
        ban_chitchat=True,
    )

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

    # coverage check
    golden_types: set[ClaimType] = set()
    for s in golden:
        c = by_id.get(s.clip_id)
        if c:
            golden_types.update(c.claim_types)

    has_selling = ClaimType.SELLING_POINT in golden_types
    has_fit_or_fabric = bool(golden_types & {ClaimType.FIT, ClaimType.FABRIC})
    ratio_ok = plan.golden_weight_ratio >= settings.golden_weight_ratio or golden_w == 0
    # if only one high clip, ratio may be high anyway
    coverage_ok = has_selling or has_fit_or_fabric or ClaimType.PRICE in golden_types

    if not has_selling and ClaimType.PRICE not in golden_types:
        warnings.append("golden_missing_selling_or_price")
    if not has_fit_or_fabric and has_selling:
        warnings.append("golden_missing_fit_or_fabric")

    plan.golden20_passed = bool(golden) and coverage_ok and (
        plan.golden_weight_ratio >= settings.golden_weight_ratio * 0.5
    )
    # softer ratio for MVP: pass if coverage_ok and has golden
    if bool(golden) and coverage_ok:
        plan.golden20_passed = True
    if not ratio_ok and plan.golden_weight_ratio < settings.golden_weight_ratio:
        warnings.append(
            f"golden_weight_ratio={plan.golden_weight_ratio:.2f}<{settings.golden_weight_ratio}"
        )
    plan.warnings = warnings

    # mutate original list scores for caller convenience
    score_map = {c.clip_id: c for c in scored}
    for i, c in enumerate(clips):
        sc = score_map.get(c.clip_id)
        if sc:
            clips[i].score = sc.score
            clips[i].weight = sc.weight
            clips[i].score_breakdown = sc.score_breakdown

    return plan
