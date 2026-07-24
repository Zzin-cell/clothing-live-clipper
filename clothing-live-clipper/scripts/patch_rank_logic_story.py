from __future__ import annotations

from pathlib import Path

p = Path(r"C:\Users\MR\AppData\grok\clothing-live-clipper\src\clipper\rank.py")
t = p.read_text(encoding="utf-8")
start = t.find("def build_timeline_plan(")
if start < 0:
    raise SystemExit("build_timeline_plan not found")
# keep everything before function
head = t[:start]
# drop old function to EOF (file ends with it)
# ensure trailing newline
new_fn = r'''
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
'''
p.write_text(head + new_fn.lstrip("\n"), encoding="utf-8")
print("patched", p, "new_len", p.stat().st_size)
