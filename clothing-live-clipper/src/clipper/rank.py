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


_CLOTHING_TEXT_HINTS = (
    "面料", "布料", "材质", "牛仔", "蕾丝", "雷丝", "不透", "柔软", "软到", "超软",
    "洗水", "破洞", "天丝", "醋酸", "显瘦", "遮肉", "版型", "收腰", "上衣", "裙子",
    "白色", "黑色", "口袋", "穿上", "上身", "这件", "这套", "裤子", "外套", "好看",
    "推荐", "客户", "软", "弹", "不透", "拼接",
)


def score_clip(clip: Clip) -> Clip:
    types = set(clip.claim_types)
    breakdown: dict[str, float] = {}
    raw = 0.0
    text = clip.text or ""

    if ClaimType.CHITCHAT in types and len(types) == 1:
        # rescue if ASR tagged wrong but clothing words present
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

    # No clothing claim → score 0 unless hard clothing keywords in text (ASR miss)
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

    # Size-only lines: never useful for cuts (hard exclude policy)
    if content_types <= {ClaimType.SIZE}:
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"size_only": 0.0, "raw": 0.0}
        return clip

    # Outfit-only weak lines without clothing hints → drop
    if content_types <= {ClaimType.OUTFIT} and not any(h in text for h in _CLOTHING_TEXT_HINTS):
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

    # text hints boost medium clothing talk so duration fill works
    if any(h in text for h in _CLOTHING_TEXT_HINTS):
        raw += 6.0
        breakdown["clothing_hint"] = 6.0

    # specificity: has digits or material-ish length
    spec = 0.0
    if any(ch.isdigit() for ch in clip.text):
        spec += 5.0
    if len(clip.text) >= 12:
        spec += 3.0
    if len(clip.text) >= 24:
        spec += 4.0
    raw += spec
    breakdown["specificity"] = spec

    # prefer usable durations for 55–60s packing
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
        # Only drop pure filler; keep scored clothing lines even if ASR tagged chitchat
        pool = [
            c
            for c in pool
            if c.score > 0
            and not (
                ClaimType.CHITCHAT in c.claim_types
                and len(set(c.claim_types)) == 1
                and not any(h in (c.text or "") for h in _CLOTHING_TEXT_HINTS)
            )
        ]

    def sort_key(c: Clip) -> tuple:
        boost = 0.0
        if prefer_types and set(c.claim_types) & prefer_types:
            boost = 100.0
        return (boost + c.score, c.weight)

    pool = sorted(pool, key=sort_key, reverse=True)

    for c in pool:
        if remaining <= 200:
            break
        # allow larger overshoot so short ASR segments can still fill 55–60s
        if c.duration_ms > remaining + 8000 and slots:
            continue
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

    # Select longer source timeline when final will be sped up (e.g. 1.3x → ~78s source for 60s out)
    speed = getattr(settings, "playback_speed", 1.0) or 1.0
    if speed < 0.8:
        speed = 1.0
    source_target_s = getattr(settings, "source_select_duration_s", settings.target_duration_s)
    target_ms = int(source_target_s) * 1000
    # scale section budgets with speed so golden still ~20s *after* speedup
    golden_ms = int(round(settings.golden_s * 1000 * speed))
    cta_ms = int(round(settings.cta_s * 1000 * speed))
    trust_ms = max(0, target_ms - golden_ms - cta_ms)

    used: set[str] = set()
    warnings: list[str] = []
    if abs(speed - 1.0) > 0.01:
        warnings.append(f"source_select_for_speed={speed:.2f}x")

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
            ClaimType.SCENE,
            ClaimType.OUTFIT,
            ClaimType.FIT,
            ClaimType.SELLING_POINT,
        },
        ban_chitchat=True,
    )

    # Duration fill toward source length that becomes ~55–60s after playback_speed
    min_plan = getattr(settings, "source_min_plan_ms", None)
    max_plan = getattr(settings, "source_max_plan_ms", None)
    if min_plan is None:
        min_plan = int(round(getattr(settings, "min_plan_ms", 55_000) * speed))
    if max_plan is None:
        max_plan = int(round(getattr(settings, "max_plan_ms", 65_000) * speed))

    def _plan_ms() -> int:
        return sum(s.t1_ms - s.t0_ms for s in [*golden, *trust, *cta])

    # multi-pass fill until 55s: take any remaining positive-score clothing clips
    for _ in range(6):
        if _plan_ms() >= min_plan:
            break
        need = max_plan - _plan_ms()
        extra = _pick_fill(
            scored,
            max(need, 15000),
            used,
            role="trust",
            prefer_types=None,
            ban_chitchat=True,
        )
        if not extra:
            # last resort: include any unused score>0 regardless of role preference
            leftover = [
                c for c in scored if c.clip_id not in used and c.score > 0
            ]
            leftover = sorted(leftover, key=lambda c: c.score, reverse=True)
            if not leftover:
                break
            for c in leftover:
                if _plan_ms() >= min_plan:
                    break
                trust.append(
                    PlanSlot(
                        clip_id=c.clip_id,
                        role="trust",
                        t0_ms=c.t0_ms,
                        t1_ms=c.t1_ms,
                        text=c.text,
                        score=c.score,
                    )
                )
                used.add(c.clip_id)
            break
        trust.extend(extra)
    if _plan_ms() < min_plan:
        warnings.append(f"short_content_ms={_plan_ms()}")
    if _plan_ms() > max_plan + 5000:
        warnings.append(f"overlong_ms={_plan_ms()}")

    # Soft pad clip edges to approach 55–60s without adding filler speech
    # (expands cut windows slightly around existing product lines)
    def _pad_slots(slots: list[PlanSlot], need_ms: int) -> None:
        if need_ms <= 0 or not slots:
            return
        per = max(100, need_ms // max(1, len(slots)))
        # cap pad per side
        per = min(per, 800)
        for s in slots:
            s.t0_ms = max(0, s.t0_ms - per)
            s.t1_ms = s.t1_ms + per

    cur = _plan_ms()
    if cur < min_plan:
        _pad_slots([*golden, *trust, *cta], min_plan - cur + 500)
        # second pass if still short
        cur2 = _plan_ms()
        if cur2 < min_plan:
            _pad_slots([*golden, *trust, *cta], min_plan - cur2 + 500)
        if _plan_ms() >= min_plan:
            warnings.append("duration_edge_padded")
        # refresh short warning
        warnings[:] = [w for w in warnings if not str(w).startswith("short_content_ms=")]
        if _plan_ms() < min_plan:
            warnings.append(f"short_content_ms={_plan_ms()}")

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
