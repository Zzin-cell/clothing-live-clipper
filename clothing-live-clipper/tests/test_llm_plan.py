from __future__ import annotations

import re
from unittest.mock import patch

from clipper import llm_plan as lp
from clipper.llm_plan import LIGHT_MAX_CLAUSES, expand_lines_to_clauses, llm_obj_to_timeline, select_clauses_for_llm
from clipper.models import TimelinePlan


def _lines():
    return [
        {"utt_id": "u1", "text": "家人们扣1点关注", "t0_ms": 0, "t1_ms": 2000},
        {
            "utt_id": "u2",
            "text": "这件面料超级软，还不透，夏天也不闷",
            "t0_ms": 3000,
            "t1_ms": 9000,
        },
        {"utt_id": "u3", "text": "收腰版型，梨形显瘦", "t0_ms": 10000, "t1_ms": 14000},
        {"utt_id": "u4", "text": "贴肤冰冰的，很舒服不闷", "t0_ms": 15000, "t1_ms": 19000},
        {"utt_id": "u5", "text": "建议穿M码偏大", "t0_ms": 20000, "t1_ms": 22000},
        {"utt_id": "u6", "text": "底下蕾丝拼接很精致", "t0_ms": 23000, "t1_ms": 27000},
    ]


def _many_lines(n: int = 80):
    rows = []
    for i in range(n):
        if i % 10 == 0:
            text = "家人们晚上好扣1点关注"
        elif i % 10 == 1:
            text = "建议穿M码偏大一码"
        elif i % 10 == 2:
            text = "这件面料超级软还不透"
        elif i % 10 == 3:
            text = "收腰版型梨形显瘦"
        elif i % 10 == 4:
            text = "今天199块包邮当天发货"
        elif i % 10 == 5:
            text = "微胖小个子也适合通勤日常"
        else:
            text = f"补充一句穿着体验很舒服{i}"
        rows.append(
            {
                "utt_id": f"u{i}",
                "text": text,
                "t0_ms": i * 2000,
                "t1_ms": i * 2000 + 1500,
            }
        )
    return rows


def test_select_clauses_for_llm_caps_and_drops_bad():
    clauses = expand_lines_to_clauses(_many_lines(160), max_clauses=500)
    assert len(clauses) > LIGHT_MAX_CLAUSES or len(clauses) > 100
    selected, stats = select_clauses_for_llm(clauses, max_clauses=lp.LIGHT_MAX_CLAUSES)
    assert stats["clauses_raw"] == len(clauses)
    assert stats["clauses_sent"] == len(selected)
    assert len(selected) <= lp.LIGHT_MAX_CLAUSES
    assert stats["clauses_sent"] <= stats["clauses_raw"]
    # no invented ids
    raw_ids = {c["id"] for c in clauses}
    assert all(c["id"] in raw_ids for c in selected)
    # size / control / price-ship should be rare or zero in selection
    joined = " ".join(c["text"] for c in selected)
    assert "M码" not in joined
    assert "扣1" not in joined
    assert "包邮" not in joined and "发货" not in joined
    assert stats.get("dropped_size", 0) >= 1 or stats.get("dropped_control", 0) >= 1
    assert stats.get("dropped_price_ship", 0) >= 1
    # audience / fabric details may be kept
    assert ("面料" in joined) or ("显瘦" in joined) or ("适合" in joined)


def test_build_plan_messages_is_ids_only_schema():
    clauses = expand_lines_to_clauses(_lines())
    messages, id_map, compact = lp._build_plan_messages(
        clauses,
        target_seconds=60,
        sp=1.4,
        target_source_ms=84_000,
        text_max=lp.CLAUSE_TEXT_MAX,
    )
    assert messages[0]["role"] == "system"
    assert '"ids"' in messages[0]["content"]
    assert "keep\":[{" not in messages[0]["content"]
    assert "why" not in messages[0]["content"]
    # Hard DROP rules preserved + clothing features first
    sys = messages[0]["content"]
    assert "尺码" in sys and ("价格" in sys or "发货" in sys)
    assert "直播" in sys or "控场" in sys
    assert "服装特点" in sys or "版型" in sys
    assert "面料" in sys
    assert "同主题" in sys or "连排" in sys or "主题" in sys
    user = messages[1]["content"]
    assert "只输出JSON" in user or "ids" in user
    assert "尺码" in user
    assert "服装特点" in user or "开头" in user
    assert "硬删" in user or "硬规则" in sys
    assert "同主题" in user or "连排" in user
    assert "c|" in user or "c" in user
    assert compact
    assert id_map
    # compact carries text only (no bulky t0/t1 forced into model echo)
    assert all("id" in c and "text" in c for c in compact)


def test_repair_opens_with_clothing_features():
    lines = [
        {"utt_id": "u0", "text": "通勤日常都适合上班穿", "t0_ms": 0, "t1_ms": 3000},
        {"utt_id": "u1", "text": "小个子梨形也能穿", "t0_ms": 3000, "t1_ms": 6000},
        {"utt_id": "u2", "text": "收腰版型上身显瘦遮肉", "t0_ms": 6000, "t1_ms": 10000},
        {"utt_id": "u3", "text": "面料超级软还不透气亲肤", "t0_ms": 10000, "t1_ms": 14000},
        {"utt_id": "u4", "text": "细节蕾丝拼接做工精细", "t0_ms": 14000, "t1_ms": 18000},
    ]
    clauses = expand_lines_to_clauses(lines)
    # Model returns scene-first order — repair must promote clothing features
    obj = lp._repair_keep_ids(
        {
            "ids": [c["id"] for c in clauses],
        },
        clauses,
    )
    keep = obj["keep"]
    assert keep
    first = keep[0]["text"]
    assert any(k in first for k in ("版型", "显瘦", "面料", "软", "收腰", "上身")), first
    assert obj.get("_narrative") == "clothing_features_first"


def test_rules_fallback_only_when_under_40s():
    """Cloud path under ~40s final may yield to longer rules; >=40s keeps cloud."""
    # Build material with plenty of clothing lines so all paths non-empty
    lines = []
    samples = [
        "收腰版型上身显瘦遮肉",
        "面料超级软还不透气亲肤",
        "小个子梨形通勤也适合",
        "垂感很好日常穿舒服",
        "细节蕾丝拼接做工精细",
        "家人们扣1点关注",
        "今天199包邮加一单",
    ]
    for i in range(24):
        lines.append(
            {
                "utt_id": f"u{i}",
                "text": samples[i % len(samples)] if i % 6 != 5 else f"上身效果真的显瘦很舒服{i}",
                "t0_ms": i * 3500,
                "t1_ms": i * 3500 + 3200,
            }
        )

    from clipper.models import TimelinePlan, PlanSlot

    short_slots = [
        PlanSlot(clip_id="c1", role="story", t0_ms=0, t1_ms=20_000, text="收腰版型显瘦", score=50),
        PlanSlot(clip_id="c2", role="story", t0_ms=20_000, t1_ms=35_000, text="面料超软", score=40),
    ]
    # source ms @1.4x → final 35s/1.4≈25s under 40s
    short_plan = TimelinePlan(
        target_duration_s=60,
        golden=short_slots,
        trust=[],
        cta=[],
        total_duration_ms=35_000,
        golden_weight_ratio=1.0,
        golden20_passed=True,
        warnings=[],
    )
    long_slots = [
        PlanSlot(clip_id=f"r{i}", role="story", t0_ms=i * 6000, t1_ms=i * 6000 + 5500, text=f"版型面料适合{i}", score=30)
        for i in range(16)
    ]
    long_plan = TimelinePlan(
        target_duration_s=60,
        golden=long_slots,
        trust=[],
        cta=[],
        total_duration_ms=88_000,
        golden_weight_ratio=1.0,
        golden20_passed=True,
        warnings=[],
    )

    # Case A: cloud short → rules should win if longer
    with patch("clipper.llm_plan.call_llm_for_plan", return_value={"keep": [], "_clauses": [], "_meta": {"model": "Qwen/Qwen2.5-7B-Instruct"}}):
        with patch("clipper.llm_plan.llm_obj_to_timeline", return_value=short_plan):
            with patch("clipper.llm_plan.plan_from_local_clauses", return_value=(short_plan, {"_meta": {"model": "local_clause_rank"}})):
                with patch("clipper.rank.build_timeline_plan", return_value=long_plan):
                    plan, obj = lp.plan_from_asr_with_llm(lines, target_seconds=60, playback_speed=1.4)
    assert (obj.get("_meta") or {}).get("chosen_path") == "rules_duration"
    assert plan.total_duration_ms >= 80_000

    # Case B: cloud already ~50s final (source 70s) → keep cloud, not rules
    ok_slots = [
        PlanSlot(clip_id=f"k{i}", role="story", t0_ms=i * 5000, t1_ms=i * 5000 + 4800, text=f"显瘦面料{i}", score=40)
        for i in range(15)
    ]
    ok_plan = TimelinePlan(
        target_duration_s=60,
        golden=ok_slots,
        trust=[],
        cta=[],
        total_duration_ms=70_000,  # final ≈50s @1.4x
        golden_weight_ratio=1.0,
        golden20_passed=True,
        warnings=[],
    )
    with patch("clipper.llm_plan.call_llm_for_plan", return_value={"keep": [{"id": "c1"}], "_clauses": [], "_meta": {"model": "Qwen/Qwen2.5-7B-Instruct"}}):
        with patch("clipper.llm_plan.llm_obj_to_timeline", return_value=ok_plan):
            with patch("clipper.llm_plan.plan_from_local_clauses", return_value=(short_plan, {"_meta": {"model": "local_clause_rank"}})):
                with patch("clipper.rank.build_timeline_plan", return_value=long_plan):
                    plan2, obj2 = lp.plan_from_asr_with_llm(lines, target_seconds=60, playback_speed=1.4)
    assert (obj2.get("_meta") or {}).get("chosen_path") == "cloud_or_repaired"
    assert plan2.total_duration_ms == 70_000


def test_size_talk_never_enters_timeline_even_if_llm_keeps():
    from clipper.llm_plan import _is_size, _is_onbody_effect

    assert _is_size("建议穿M码偏大")
    assert _is_size("100斤穿L就行")
    assert _is_size("来个XL码")
    assert _is_size("胸围量一下偏大")
    assert not _is_size("收腰版型上身显瘦")
    # wearing effects are on-body content, not size chart
    assert _is_onbody_effect("不会走光漏光")
    assert _is_onbody_effect("不显肚子 胃包拜拜肉都遮住")
    assert not _is_size("不会走光漏光")
    assert not _is_size("不显肚子 遮拜拜肉")
    assert not _is_size("收腹遮胃包很有安全感")

    lines = [
        {"utt_id": "u1", "text": "收腰版型上身显瘦遮肉", "t0_ms": 0, "t1_ms": 4000},
        {"utt_id": "u2", "text": "建议穿M码偏大一码", "t0_ms": 4000, "t1_ms": 7000},
        {"utt_id": "u3", "text": "面料超级软还不透", "t0_ms": 7000, "t1_ms": 11000},
        {"utt_id": "u4", "text": "100斤穿L完全可以", "t0_ms": 11000, "t1_ms": 14000},
        {"utt_id": "u5", "text": "小个子梨形也适合", "t0_ms": 14000, "t1_ms": 18000},
        {"utt_id": "u6", "text": "XL码的姐妹也可以", "t0_ms": 18000, "t1_ms": 21000},
    ]
    clauses = expand_lines_to_clauses(lines)
    # Even if LLM wrongly keeps size ids, timeline must strip them
    plan = llm_obj_to_timeline(
        {
            "keep": [{"id": c["id"], "text": c["text"]} for c in clauses],
            "_clauses": clauses,
            "_clauses_raw": clauses,
        },
        lines,
        target_seconds=60,
        playback_speed=1.0,
    )
    blob = " ".join(s.text for s in plan.golden)
    assert "M码" not in blob
    assert "建议穿" not in blob and "100斤" not in blob and "XL" not in blob
    assert "偏大" not in blob
    assert "显瘦" in blob or "面料" in blob

    # on-body effects must survive even mixed with size-banned corpus
    lines2 = [
        {"utt_id": "a1", "text": "不会走光漏光 穿上很安心", "t0_ms": 0, "t1_ms": 3000},
        {"utt_id": "a2", "text": "不显肚子 胃包拜拜肉都盖住", "t0_ms": 3000, "t1_ms": 7000},
        {"utt_id": "a3", "text": "建议穿M码", "t0_ms": 7000, "t1_ms": 9000},
        {"utt_id": "a4", "text": "面料软软的不透", "t0_ms": 9000, "t1_ms": 12000},
    ]
    clauses2 = expand_lines_to_clauses(lines2)
    plan2 = llm_obj_to_timeline(
        {
            "keep": [{"id": c["id"], "text": c["text"]} for c in clauses2],
            "_clauses": clauses2,
        },
        lines2,
        target_seconds=60,
        playback_speed=1.0,
    )
    blob2 = " ".join(s.text for s in plan2.golden)
    assert "走光" in blob2 or "不显肚子" in blob2 or "拜拜肉" in blob2 or "胃包" in blob2
    assert "M码" not in blob2 and "建议穿" not in blob2


def test_live_deal_call_dropped_jia_yi_dan():
    from clipper.llm_plan import _is_control, _is_price_or_shipping, _is_deal_call, _is_link_or_slot_talk

    assert _is_price_or_shipping("姐妹们赶紧加一单")
    assert _is_price_or_shipping("喜欢的加一单啊")
    assert _is_deal_call("赶紧加两单")
    assert _is_deal_call("来一单啊")
    assert _is_control("小黄车加购") or _is_price_or_shipping("小黄车加购")
    assert not _is_price_or_shipping("收腰版型上身显瘦")

    lines = [
        {"utt_id": "u1", "text": "收腰版型上身显瘦遮肉", "t0_ms": 0, "t1_ms": 4000},
        {"utt_id": "u2", "text": "姐妹们赶紧加一单", "t0_ms": 4000, "t1_ms": 7000},
        {"utt_id": "u3", "text": "面料超级软还不透气", "t0_ms": 7000, "t1_ms": 11000},
        {"utt_id": "u4", "text": "喜欢的再拍一单", "t0_ms": 11000, "t1_ms": 14000},
    ]
    clauses = expand_lines_to_clauses(lines)
    plan = llm_obj_to_timeline(
        {"keep": [{"id": c["id"], "text": c["text"]} for c in clauses], "_clauses": clauses},
        lines,
        target_seconds=60,
        playback_speed=1.0,
    )
    blob = " ".join(s.text for s in plan.golden)
    assert "加一单" not in blob and "拍一单" not in blob
    assert "显瘦" in blob or "面料" in blob


def test_pacing_filler_shou_su_kai_jia_bao_hard_dropped():
    from clipper.llm_plan import _is_control, _is_live_pacing_filler, scrub_live_pacing_from_text

    for s in (
        "手速啊",
        "手速要快姐妹们",
        "我们准备开架了",
        "马上开架注意了",
        "吃饭给大家抱一下",
        "给大家抱一下哈",
        "先抱一下啊",
        "稍等一下哈",
    ):
        assert _is_live_pacing_filler(s) or _is_control(s), s
    assert not _is_live_pacing_filler("收腰版型上身显瘦")
    assert not _is_control("面料超级软还不透")

    # Mixed module: keep clothing head, strip live pacing tail (user screenshot case)
    mixed = "它也是高腰线，T手速啊，我们准备开架了，吃饭给大家抱一下，好吧，目前"
    cleaned, changed = scrub_live_pacing_from_text(mixed)
    assert changed
    assert "高腰" in cleaned or "腰线" in cleaned
    assert "手速" not in cleaned and "开架" not in cleaned
    assert "抱一下" not in cleaned and "吃饭" not in cleaned

    lines = [
        {"utt_id": "u1", "text": "收腰版型上身显瘦遮肉", "t0_ms": 0, "t1_ms": 3500},
        {"utt_id": "u2", "text": "手速啊准备开架了", "t0_ms": 3500, "t1_ms": 6000},
        {"utt_id": "u3", "text": "面料超级软还不透", "t0_ms": 6000, "t1_ms": 9500},
        {"utt_id": "u4", "text": mixed, "t0_ms": 9500, "t1_ms": 15600},
        {"utt_id": "u5", "text": "通勤日常都适合", "t0_ms": 15600, "t1_ms": 18600},
    ]
    clauses = expand_lines_to_clauses(lines)
    plan = llm_obj_to_timeline(
        {"keep": [{"id": c["id"], "text": c["text"]} for c in clauses], "_clauses": clauses},
        lines,
        target_seconds=60,
        playback_speed=1.0,
    )
    blob = " ".join(s.text for s in plan.golden)
    assert "手速" not in blob and "开架" not in blob
    assert "抱一下" not in blob
    assert "显瘦" in blob or "面料" in blob or "腰线" in blob or "高腰" in blob


def test_link_and_n_hao_expressions_hard_dropped():
    from clipper.llm_plan import _is_link_or_slot_talk, _is_price_or_shipping, _is_control

    for s in (
        "点1号链接",
        "看一下2号链接",
        "三号小黄车",
        "N号链接拍下",
        "几号链接都有",
        "上方链接点一下",
        "小黄车挂上车了",
        "戳链接加购",
        "左下角点链接",
    ):
        assert _is_link_or_slot_talk(s) or _is_price_or_shipping(s) or _is_control(s), s
    assert not _is_link_or_slot_talk("领口细节很好看")
    assert not _is_price_or_shipping("收腰版型上身显瘦")

    lines = [
        {"utt_id": "u1", "text": "收腰版型上身显瘦遮肉", "t0_ms": 0, "t1_ms": 3500},
        {"utt_id": "u2", "text": "喜欢的姐妹点1号链接", "t0_ms": 3500, "t1_ms": 6500},
        {"utt_id": "u3", "text": "面料超级软还不透", "t0_ms": 6500, "t1_ms": 10000},
        {"utt_id": "u4", "text": "三号链接也可以拍", "t0_ms": 10000, "t1_ms": 12500},
        {"utt_id": "u5", "text": "小黄车挂上车了", "t0_ms": 12500, "t1_ms": 15000},
        {"utt_id": "u6", "text": "通勤日常都适合", "t0_ms": 15000, "t1_ms": 18000},
        {"utt_id": "u7", "text": "赶紧加两单别犹豫", "t0_ms": 18000, "t1_ms": 20500},
    ]
    clauses = expand_lines_to_clauses(lines)
    plan = llm_obj_to_timeline(
        {"keep": [{"id": c["id"], "text": c["text"]} for c in clauses], "_clauses": clauses},
        lines,
        target_seconds=60,
        playback_speed=1.0,
    )
    blob = " ".join(s.text for s in plan.golden)
    assert "链接" not in blob and "小黄车" not in blob
    assert "加两单" not in blob and "1号" not in blob and "三号" not in blob
    assert "显瘦" in blob or "面料" in blob or "通勤" in blob


def test_policy_risk_dropped_from_plans():
    from clipper.llm_plan import _is_policy_risk

    assert _is_policy_risk("全网最低价保证瘦三天")
    assert _is_policy_risk("加我微信私信领链接")
    assert not _is_policy_risk("收腰版型上身显瘦遮肉")

    lines = [
        {"utt_id": "u1", "text": "收腰版型上身显瘦遮肉", "t0_ms": 0, "t1_ms": 4000},
        {"utt_id": "u2", "text": "面料超级软还不透气亲肤", "t0_ms": 4000, "t1_ms": 8000},
        {"utt_id": "u3", "text": "全网最低绝对第一最好穿", "t0_ms": 8000, "t1_ms": 11000},
        {"utt_id": "u4", "text": "加我微信vx私信领优惠", "t0_ms": 11000, "t1_ms": 14000},
        {"utt_id": "u5", "text": "小个子梨形通勤也适合", "t0_ms": 14000, "t1_ms": 18000},
    ]
    clauses = expand_lines_to_clauses(lines)
    plan = llm_obj_to_timeline(
        {"keep": [{"id": c["id"], "text": c["text"]} for c in clauses], "_clauses": clauses},
        lines,
        target_seconds=60,
        playback_speed=1.0,
    )
    blob = " ".join(s.text for s in plan.golden)
    assert "全网最低" not in blob and "微信" not in blob and "vx" not in blob
    assert "显瘦" in blob or "面料" in blob


def test_duration_fill_never_keeps_live_or_shipping():
    lines = [
        {"utt_id": "u0", "text": "家人们晚上好扣1点关注", "t0_ms": 0, "t1_ms": 2000},
        {"utt_id": "u1", "text": "今天199包邮当天发货", "t0_ms": 2000, "t1_ms": 5000},
        {"utt_id": "u2", "text": "准备一下321里面去拍", "t0_ms": 5000, "t1_ms": 8000},
        {"utt_id": "u3", "text": "收腰版型上身显瘦遮肉", "t0_ms": 8000, "t1_ms": 14000},
        {"utt_id": "u4", "text": "面料超级软还不透气亲肤", "t0_ms": 14000, "t1_ms": 20000},
        {"utt_id": "u5", "text": "小个子梨形通勤也适合", "t0_ms": 20000, "t1_ms": 26000},
        {"utt_id": "u6", "text": "建议穿M码偏大", "t0_ms": 26000, "t1_ms": 28000},
        {"utt_id": "u7", "text": "垂感很好日常穿舒服", "t0_ms": 28000, "t1_ms": 34000},
        {"utt_id": "u8", "text": "细节蕾丝拼接做工精细", "t0_ms": 34000, "t1_ms": 40000},
        {"utt_id": "u9", "text": "链接小黄车去拍下", "t0_ms": 40000, "t1_ms": 43000},
    ]
    # stretch with more clothing lines so fill has material without banned lines
    for i in range(10, 30):
        lines.append(
            {
                "utt_id": f"u{i}",
                "text": f"上身效果真的显瘦很舒服不闷{i}",
                "t0_ms": 40000 + (i - 9) * 3000,
                "t1_ms": 40000 + (i - 9) * 3000 + 2800,
            }
        )
    plan, obj = lp.plan_from_asr_with_llm(lines, target_seconds=60, playback_speed=1.4)
    blob = " ".join(s.text for s in plan.golden)
    assert "扣1" not in blob and "家人们" not in blob
    assert "发货" not in blob and "包邮" not in blob and "199" not in blob
    assert "准备" not in blob and "321" not in blob
    assert "小黄车" not in blob and "链接" not in blob
    assert "M码" not in blob
    assert ("显瘦" in blob) or ("面料" in blob) or ("版型" in blob)


def test_repair_narrative_opens_with_onbody_or_fabric():
    lines = [
        {"utt_id": "u0", "text": "家人们晚上好扣1", "t0_ms": 0, "t1_ms": 1500},
        {"utt_id": "u1", "text": "通勤日常都适合上班穿", "t0_ms": 1500, "t1_ms": 4500},
        {"utt_id": "u2", "text": "细节蕾丝拼接做工精细", "t0_ms": 4500, "t1_ms": 7500},
        {"utt_id": "u3", "text": "收腰版型上身显瘦遮肉", "t0_ms": 7500, "t1_ms": 11000},
        {"utt_id": "u4", "text": "面料超级软还不透气亲肤", "t0_ms": 11000, "t1_ms": 14500},
        {"utt_id": "u5", "text": "小个子梨形也能穿", "t0_ms": 14500, "t1_ms": 17500},
    ]
    clauses = expand_lines_to_clauses(lines)
    # Simulate model picking scene first — repair must promote hook + body→craft→scene
    obj = lp._repair_keep_ids(
        {
            "ids": [
                next(c["id"] for c in clauses if "通勤" in c["text"]),
                next(c["id"] for c in clauses if "蕾丝" in c["text"]),
                next(c["id"] for c in clauses if "显瘦" in c["text"]),
                next(c["id"] for c in clauses if "面料" in c["text"]),
                next(c["id"] for c in clauses if "小个子" in c["text"]),
            ]
        },
        clauses,
    )
    keep = obj["keep"]
    assert keep
    first = keep[0]["text"]
    assert any(k in first for k in ("显瘦", "版型", "上身", "面料", "软", "收腰")), first
    # After opener, body/craft should appear before pure scene if present
    texts = [k["text"] for k in keep]
    body_i = next((i for i, t in enumerate(texts) if "显瘦" in t or "版型" in t), None)
    scene_i = next((i for i, t in enumerate(texts) if "通勤" in t or "小个子" in t), None)
    if body_i is not None and scene_i is not None and body_i > 0:
        assert body_i <= scene_i
    assert obj.get("_narrative") == "clothing_features_first"


def test_normalize_llm_keep_obj_ids_only_and_numbers():
    clauses = expand_lines_to_clauses(
        [
            {"utt_id": "u1", "text": "家人们扣1", "t0_ms": 0, "t1_ms": 1000},
            {"utt_id": "u2", "text": "收腰版型显瘦", "t0_ms": 1000, "t1_ms": 3000},
            {"utt_id": "u3", "text": "面料超软不透", "t0_ms": 3000, "t1_ms": 5000},
            {"utt_id": "u4", "text": "小个子适合", "t0_ms": 5000, "t1_ms": 7000},
        ]
    )
    # map short -> full like production
    id_map = {}
    for c in clauses:
        m = re.search(r"(\d+)$", c["id"])
        if m:
            id_map[f"c{int(m.group(1))}"] = c["id"]
    obj = lp._normalize_llm_keep_obj(
        {"ids": ["c2", "c3", "c4"], "hook": "effect"}, clauses, id_map
    )
    assert [k["id"] for k in obj["keep"]]
    texts = " ".join(k["text"] for k in obj["keep"])
    assert "版型" in texts or "面料" in texts or "适合" in texts

    obj2 = lp._normalize_llm_keep_obj({"sel": [2, 3, 4]}, clauses, id_map)
    assert obj2["keep"]
    # numbers 2/3/4 should resolve via id_map
    assert all(k["id"] in {c["id"] for c in clauses} for k in obj2["keep"])


def test_extract_json_obj_accepts_ids_schema():
    obj = lp._extract_json_obj('```json\n{"ids":["c2","c3"],"hook":"effect"}\n```')
    assert obj.get("ids") == ["c2", "c3"]


def test_call_llm_for_plan_ids_only_success_path():
    lines = _many_lines(30)
    fake = {
        "content": '{"ids":["c3","c4","c6","c7","c12"],"hook":"effect"}',
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "base_url": "https://api.siliconflow.cn/v1",
        "endpoint": "https://api.siliconflow.cn/v1/chat/completions",
        "auth_variant": 0,
        "payload_variant": 0,
        "latency_ms": 900,
    }
    with patch("clipper.llm_plan.chat_completions", return_value=fake):
        with patch(
            "clipper.llm_plan.runtime_llm",
            return_value={
                "enabled": True,
                "plan_enabled": True,
                "api_key": "sk-test",
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "Qwen/Qwen2.5-7B-Instruct",
            },
        ):
            obj = lp.call_llm_for_plan(lines, target_seconds=60, playback_speed=1.4)
    assert obj.get("keep")
    assert obj.get("_meta", {}).get("submit_mode") == "stable_ids_only_asr_selected_clauses"
    assert obj.get("_meta", {}).get("latency_ms") == 900
    plan = llm_obj_to_timeline(obj, lines, target_seconds=60, playback_speed=1.4)
    assert plan.golden


def test_plan_from_local_clauses_not_empty():
    lines = _many_lines(40)
    plan, obj = lp.plan_from_local_clauses(lines, target_seconds=60, playback_speed=1.4)
    assert plan.golden
    assert plan.total_duration_ms > 0
    assert obj.get("_meta", {}).get("model") == "local_clause_rank"
    blob = " ".join(s.text for s in plan.golden)
    # core product focus should surface when present in input
    assert ("面料" in blob) or ("显瘦" in blob) or ("版型" in blob)
    assert ("适合" in blob) or ("梨形" in blob) or ("小个子" in blob) or ("微胖" in blob)
    # ~60s final needs enough source @1.4x (~>42s source preferred when material exists)
    assert plan.total_duration_ms >= 20_000


def _topic_scattered(seq: list[str]) -> bool:
    """True if any topic reappears after a different topic intervened."""
    last: dict[str, int] = {}
    for i, t in enumerate(seq):
        if t in last and any(x != t for x in seq[last[t] + 1 : i]):
            return True
        last[t] = i
    return False


def test_topic_blocks_together_after_scattered_keep():
    """Same selling-point clauses must form contiguous blocks, not fit/fabric hopscotch."""
    # Larger gaps so merge-on-timeline doesn't glue different topics
    lines = [
        {"utt_id": "u1", "text": "收腰版型上身显瘦", "t0_ms": 0, "t1_ms": 2500},
        {"utt_id": "u2", "text": "面料超级软还不透", "t0_ms": 4000, "t1_ms": 6500},
        {"utt_id": "u3", "text": "遮肉修身比例好看", "t0_ms": 8000, "t1_ms": 10500},
        {"utt_id": "u4", "text": "亲肤凉感夏天不闷", "t0_ms": 12000, "t1_ms": 14500},
        {"utt_id": "u5", "text": "底下蕾丝拼接很精致", "t0_ms": 16000, "t1_ms": 18500},
        {"utt_id": "u6", "text": "小个子梨形也适合通勤", "t0_ms": 20000, "t1_ms": 22500},
        {"utt_id": "u7", "text": "全身显瘦效果更好", "t0_ms": 24000, "t1_ms": 26500},
        {"utt_id": "u8", "text": "垂感特别好手感软", "t0_ms": 28000, "t1_ms": 30500},
    ]
    clauses = expand_lines_to_clauses(lines)
    by_text = {c["text"]: c for c in clauses}
    # Deliberately alternate fit / fabric
    order = [
        "收腰版型上身显瘦",
        "面料超级软还不透",
        "遮肉修身比例好看",
        "亲肤凉感夏天不闷",
        "底下蕾丝拼接很精致",
        "小个子梨形也适合通勤",
        "全身显瘦效果更好",
        "垂感特别好手感软",
    ]
    keep = [{"id": by_text[t]["id"], "text": t} for t in order if t in by_text]
    plan = llm_obj_to_timeline(
        {"keep": keep, "_clauses": clauses, "_clauses_raw": clauses},
        lines,
        target_seconds=60,
        playback_speed=1.0,
    )
    ts = [lp._topic_of_text(s.text or "") for s in plan.golden]
    assert plan.golden
    assert "policy:topic_blocks_together" in (plan.warnings or [])
    assert not _topic_scattered(ts), ts
    fit_idxs = [i for i, t in enumerate(ts) if t == "fit"]
    if len(fit_idxs) >= 2:
        assert fit_idxs[-1] - fit_idxs[0] + 1 == len(fit_idxs), (fit_idxs, ts)


def test_cluster_helper_and_local_no_scatter():
    from clipper.models import PlanSlot

    raw = [
        PlanSlot(clip_id="a", role="story", t0_ms=0, t1_ms=2000, text="面料超级软", score=10),
        PlanSlot(clip_id="b", role="story", t0_ms=2000, t1_ms=4000, text="收腰版型显瘦", score=10),
        PlanSlot(clip_id="c", role="story", t0_ms=4000, t1_ms=6000, text="还不透气亲肤", score=10),
        PlanSlot(clip_id="d", role="story", t0_ms=6000, t1_ms=8000, text="上身遮肉比例好看", score=10),
        PlanSlot(clip_id="e", role="story", t0_ms=8000, t1_ms=10000, text="通勤日常都适合", score=10),
    ]
    out = lp._cluster_slots_by_topic(raw)
    assert [s.clip_id for s in out][0] == "a"
    assert not _topic_scattered([lp._topic_of_text(s.text or "") for s in out])

    lines = [
        {"utt_id": "u0", "text": "家人们晚上好扣1", "t0_ms": 0, "t1_ms": 1500},
        {"utt_id": "u1", "text": "收腰版型上身显瘦遮肉", "t0_ms": 1500, "t1_ms": 4500},
        {"utt_id": "u2", "text": "面料超级软还不透气", "t0_ms": 4500, "t1_ms": 7500},
        {"utt_id": "u3", "text": "全身比例特别好看", "t0_ms": 7500, "t1_ms": 10500},
        {"utt_id": "u4", "text": "亲肤凉感夏天不闷", "t0_ms": 10500, "t1_ms": 13500},
        {"utt_id": "u5", "text": "细节蕾丝拼接做工精细", "t0_ms": 13500, "t1_ms": 16500},
        {"utt_id": "u6", "text": "微胖梨形通勤也适合", "t0_ms": 16500, "t1_ms": 19500},
        {"utt_id": "u7", "text": "修身不显胯遮肚子", "t0_ms": 19500, "t1_ms": 22500},
        {"utt_id": "u8", "text": "垂感手感都特别舒服", "t0_ms": 22500, "t1_ms": 25500},
    ]
    plan, _obj = lp.plan_from_local_clauses(lines, target_seconds=60, playback_speed=1.4)
    ts = [lp._topic_of_text(s.text or "") for s in plan.golden]
    assert plan.golden
    assert not _topic_scattered(ts), ts


def test_repair_forces_coverage_and_duration():
    lines = []
    samples = [
        "家人们晚上好扣1点关注",
        "这件收腰版型上身显瘦",
        "面料超级软还不透气亲肤",
        "黄黑皮小个子也能穿显白",
        "建议穿M码偏大",
        "今天199块包邮发货",
        "垂感很好日常通勤合适",
        "遮肉不显胯梨形友好",
    ]
    for i in range(30):
        lines.append(
            {
                "utt_id": f"u{i}",
                "text": samples[i % len(samples)] if i < 16 else f"补充穿着体验很舒服不闷{i}",
                "t0_ms": i * 3000,
                "t1_ms": i * 3000 + 2800,
            }
        )
    clauses = expand_lines_to_clauses(lines)
    obj = lp._repair_keep_ids({"keep": [{"id": "bad000", "text": "xxx"}]}, clauses)
    keep_text = " ".join(k["text"] for k in obj["keep"])
    assert "版型" in keep_text or "显瘦" in keep_text or "收腰" in keep_text
    assert "面料" in keep_text or "软" in keep_text
    assert "小个子" in keep_text or "黄黑皮" in keep_text or "梨形" in keep_text or "适合" in keep_text
    assert "199" not in keep_text and "包邮" not in keep_text and "扣1" not in keep_text
    total = sum(int(k["t1_ms"]) - int(k["t0_ms"]) for k in obj["keep"])
    assert total >= 40_000
    plan = llm_obj_to_timeline({**obj, "_clauses": clauses}, lines, target_seconds=60, playback_speed=1.4)
    assert plan.golden
    assert plan.total_duration_ms >= 35_000


def test_repair_keep_ids_from_mangled_model_output():
    lines = [
        {"utt_id": "u1", "text": "收腰版型显瘦遮肉", "t0_ms": 0, "t1_ms": 2000},
        {"utt_id": "u2", "text": "面料超级软还不透", "t0_ms": 2000, "t1_ms": 4000},
        {"utt_id": "u3", "text": "小个子梨形都适合", "t0_ms": 4000, "t1_ms": 6000},
    ]
    clauses = expand_lines_to_clauses(lines)
    bad = {
        "main_points": ["版型", "面料", "适用人群"],
        "keep": [
            {
                "id": "c0000000000000000000000000000001",
                "text": "收腰版型显瘦遮肉",
            },
            {"id": "xxx", "text": "面料超级软还不透"},
            {"id": "nope", "text": "完全对不上的句子"},
        ],
    }
    fixed = lp._repair_keep_ids(bad, clauses)
    ids = [k["id"] for k in fixed["keep"]]
    assert ids
    assert all(i in {c["id"] for c in clauses} for i in ids)
    assert any("面料" in k["text"] or "版型" in k["text"] for k in fixed["keep"])
    plan = llm_obj_to_timeline({**fixed, "_clauses": clauses}, lines, target_seconds=60, playback_speed=1.0)
    assert plan.golden


def test_live_stage_direction_dropped():
    from clipper.llm_plan import _is_control, _is_size, _is_persona_or_hype, llm_obj_to_timeline

    assert _is_control("里面去拍就可以了，好不好 来准备一下")
    assert _is_control("我们先上裤紫袜，来凳备一下，321，用鞋把给它")
    assert _is_control("来准备一下 3 2 1")
    assert not _is_control("这件收腰版型显瘦，面料很透气")
    assert _is_persona_or_hype("不要随便定义我的标签啊，我告诉你 甄姐的标签不是随意定出来的是你根本就摸不着拆不透的 是不是")
    assert _is_size("衣缝胸大的，卡满，网袋胸小的，我推荐什么来三")

    lines = [
        {"utt_id": "u1", "text": "里面去拍就可以了，好不好 来准备一下", "t0_ms": 0, "t1_ms": 2000},
        {"utt_id": "u2", "text": "收腰版型上身显瘦遮肉", "t0_ms": 2000, "t1_ms": 5000},
        {"utt_id": "u3", "text": "来准备一下 321 用鞋把它卡住", "t0_ms": 5000, "t1_ms": 7000},
    ]
    clauses = expand_lines_to_clauses(lines)
    id_bad1 = next(c["id"] for c in clauses if "准备" in c["text"] or "里面" in c["text"])
    id_good = next(c["id"] for c in clauses if "版型" in c["text"] or "显瘦" in c["text"])
    plan = llm_obj_to_timeline(
        {
            "keep": [
                {"id": id_bad1, "text": "来准备一下"},
                {"id": id_good, "text": "收腰版型上身显瘦遮肉"},
                {"id": "x", "text": "321 用鞋卡住"},
            ],
            "_clauses": clauses,
        },
        lines,
        target_seconds=60,
        playback_speed=1.0,
    )
    blob = " ".join(s.text for s in plan.golden)
    assert "准备" not in blob and "321" not in blob and "里面去拍" not in blob
    assert "显瘦" in blob or "版型" in blob

    lines2 = [
        {"utt_id": "u1", "text": "不要随便定义我的标签啊，甄姐的标签不是随意定出来的", "t0_ms": 0, "t1_ms": 3000},
        {"utt_id": "u2", "text": "面料超级软还不透", "t0_ms": 3000, "t1_ms": 6000},
        {"utt_id": "u3", "text": "衣缝胸大的，卡满，网袋胸小的，我推荐什么来三", "t0_ms": 6000, "t1_ms": 9000},
    ]
    clauses2 = expand_lines_to_clauses(lines2)
    plan2 = llm_obj_to_timeline(
        {
            "keep": [{"id": c["id"], "text": c["text"]} for c in clauses2],
            "_clauses": clauses2,
        },
        lines2,
        target_seconds=60,
        playback_speed=1.0,
    )
    blob2 = " ".join(s.text for s in plan2.golden)
    assert "标签" not in blob2
    assert "胸大" not in blob2 and "卡满" not in blob2 and "胸小" not in blob2
    assert "面料" in blob2 or "软" in blob2


def test_price_shipping_variants_dropped():
    from clipper.llm_plan import _is_price_or_shipping, llm_obj_to_timeline

    # exact screenshot-like lines
    assert _is_price_or_shipping("日常定价，我们爱个599拨分，套装买下来1000多拨")
    assert _is_price_or_shipping("發貨時間會有點慢，因为是定制面")
    assert _is_price_or_shipping("发货时间会有点慢")
    assert not _is_price_or_shipping("这件面料超级软，小个子也适合")

    # timeline hard gate even if llm keeps them
    lines = [
        {"utt_id": "u1", "text": "日常定价我们爱个599拨分", "t0_ms": 0, "t1_ms": 2000},
        {"utt_id": "u2", "text": "這件品牌面料超级软", "t0_ms": 2000, "t1_ms": 4000},
        {"utt_id": "u3", "text": "發貨時間會有點慢因为是定制", "t0_ms": 4000, "t1_ms": 6000},
    ]
    clauses = expand_lines_to_clauses(lines)
    id_price = next(c["id"] for c in clauses if "定价" in c["text"] or "拨分" in c["text"])
    id_good = next(c["id"] for c in clauses if "面料" in c["text"] or "软" in c["text"])
    id_ship = next(c["id"] for c in clauses if "貨" in c["text"] or "发货" in c["text"] or "發貨" in c["text"])
    plan = llm_obj_to_timeline(
        {
            "keep": [
                {"id": id_price, "text": "日常定价我们爱个599拨分"},
                {"id": id_good, "text": "这件品牌面料超级软"},
                {"id": id_ship, "text": "發貨時間會有點慢"},
            ],
            "_clauses": clauses,
        },
        lines,
        target_seconds=60,
        playback_speed=1.0,
    )
    texts = " ".join(s.text for s in plan.golden)
    assert "定价" not in texts and "拨分" not in texts
    assert "發貨" not in texts and "发货" not in texts
    assert "面料" in texts or "软" in texts


def test_expand_lines_to_clauses_splits_full_asr():
    clauses = expand_lines_to_clauses(_lines())
    # Keep parent windows when moderate-length — need enough duration for ~60s final
    assert len(clauses) >= 1
    texts = " ".join(c["text"] for c in clauses)
    assert "超级软" in texts and "不透" in texts
    assert all(c["t1_ms"] > c["t0_ms"] for c in clauses)
    # short sample should not explode into dozens of crumbs
    assert len(clauses) <= len(_lines()) + 4


def test_llm_obj_to_timeline_orders_and_clamps():
    clauses = expand_lines_to_clauses(_lines())
    assert clauses

    def find_id(*keys: str) -> str:
        for c in clauses:
            t = str(c["text"]).lower()
            if any(k.lower() in t for k in keys):
                return c["id"]
        raise AssertionError(f"no clause matching {keys}: {[c['text'] for c in clauses]}")

    id_soft = find_id("软", "面料")
    id_fit = find_id("收腰", "显瘦", "版型")
    id_wear = find_id("舒服", "冰冰", "闷")
    id_detail = find_id("蕾丝", "拼接")
    id_size = find_id("m码", "M码", "尺码", "偏大")
    llm_obj = {
        "product_summary": "软糯不透+收腰显瘦",
        "main_points": ["面料软不透", "收腰显瘦", "穿着凉快"],
        "logic": ["钩子", "版型", "体验", "细节"],
        "keep": [
            {"id": id_soft, "text": "这件面料超级软", "why": "卖点", "point": "面料"},
            {"id": id_fit, "text": "收腰版型", "why": "版型", "point": "显瘦"},
            # out-of-range timestamps should clamp into source window
            {"id": id_wear, "t0_ms": 1000, "t1_ms": 999999, "text": "很舒服不闷", "why": "体验"},
            {"id": id_detail, "text": "底下蕾丝拼接很精致", "why": "细节"},
            # should be dropped by safety even if llm kept
            {"id": id_size, "text": "建议穿M码偏大", "why": "bad"},
        ],
        "drop_ids": [],
        "_clauses": clauses,
    }
    plan = llm_obj_to_timeline(llm_obj, _lines(), target_seconds=60, playback_speed=1.0)
    assert isinstance(plan, TimelinePlan)
    assert plan.golden
    texts = [s.text for s in plan.golden]
    assert any(("软" in t) or ("面料" in t) for t in texts[:2])
    assert all("M码" not in t for t in texts)
    # clamp check: even if LLM asks 1000~999999, result stays inside source clause window
    wear_src = next(c for c in clauses if c["id"] == id_wear)
    wear = next(s for s in plan.golden if s.clip_id.startswith(f"llm_{id_wear}_"))
    assert wear.t0_ms >= wear_src["t0_ms"]
    assert wear.t1_ms <= wear_src["t1_ms"] + 200
    assert any("llm_logic_plan" in w for w in plan.warnings)


def test_llm_empty_keep_gets_duration_fill_from_lines():
    # empty keep should no longer hard-fail to zero; timeline fills sell lines toward ~60s
    plan = llm_obj_to_timeline({"keep": []}, _lines(), target_seconds=60, playback_speed=1.4)
    assert plan.golden
    blob = " ".join(s.text for s in plan.golden)
    assert ("面料" in blob) or ("显瘦" in blob) or ("版型" in blob)


def test_llm_completes_incomplete_tail_instead_of_hard_cutoff():
    clauses = expand_lines_to_clauses(_lines())
    # force an incomplete keep item then ensure planner prefers complete windows
    id_soft = next(c["id"] for c in clauses if "软" in c["text"] or "面料" in c["text"])
    id_fit = next(c["id"] for c in clauses if "收腰" in c["text"] or "显瘦" in c["text"])
    id_wear = next(c["id"] for c in clauses if "舒服" in c["text"] or "冰冰" in c["text"])
    llm_obj = {
        "product_summary": "完整逻辑测试",
        "main_points": ["面料", "版型", "体验"],
        "keep": [
            {"id": id_soft, "text": "这件面料超级软", "why": "钩子"},
            {"id": id_fit, "text": "收腰版型", "why": "版型"},
            {"id": id_wear, "text": "很舒服不闷", "why": "收束"},
        ],
        "_clauses": clauses,
    }
    plan = llm_obj_to_timeline(llm_obj, _lines(), target_seconds=60, playback_speed=1.0)
    assert plan.golden
    assert any("complete_logic_no_cutoff" in w for w in plan.warnings)
    # ending should not be an empty/incomplete crumb only
    assert len(plan.golden[-1].text.strip()) >= 4


def test_system_prompt_light_is_much_shorter():
    assert hasattr(lp, "SYSTEM_PROMPT_LIGHT")
    assert len(lp.SYSTEM_PROMPT_LIGHT) < len(lp.SYSTEM_PROMPT)
    assert len(lp.SYSTEM_PROMPT_LIGHT) < 2800
    assert "JSON" in lp.SYSTEM_PROMPT_LIGHT or "json" in lp.SYSTEM_PROMPT_LIGHT.lower()
    assert "尺码" in lp.SYSTEM_PROMPT_LIGHT
    # product focus + de-live + ~60s
    assert "版型" in lp.SYSTEM_PROMPT_LIGHT
    assert "面料" in lp.SYSTEM_PROMPT_LIGHT
    assert "适用人群" in lp.SYSTEM_PROMPT_LIGHT
    assert "直播" in lp.SYSTEM_PROMPT_LIGHT or "控场" in lp.SYSTEM_PROMPT_LIGHT
    assert "60" in lp.SYSTEM_PROMPT_LIGHT or "55" in lp.SYSTEM_PROMPT_LIGHT
    assert "价格" in lp.SYSTEM_PROMPT_LIGHT or "发货" in lp.SYSTEM_PROMPT_LIGHT


def test_extract_json_obj_strips_think_and_fences():
    raw = """
    <think>
    我先分析一下……
    </think>
    ```json
    {"product_summary":"软","hook_type":"pain","main_points":["面料"],"logic":["钩子"],"keep":[],"drop_ids":[],"notes":""}
    ```
    """
    obj = lp._extract_json_obj(raw)
    assert obj["product_summary"] == "软"
    assert obj["keep"] == []


def test_call_llm_for_plan_uses_trim_and_lower_tokens(monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return {
            "content": '{"product_summary":"x","hook_type":"pain","main_points":["a"],"logic":["钩子"],"keep":[],"drop_ids":[],"notes":""}',
            "model": kwargs.get("model") or "m",
            "base_url": kwargs.get("base_url") or "https://api.siliconflow.cn/v1",
            "endpoint": "https://api.siliconflow.cn/v1/chat/completions",
            "auth_variant": 0,
            "payload_variant": 0,
            "latency_ms": 1,
        }

    monkeypatch.setattr(lp, "chat_completions", fake_chat)
    monkeypatch.setattr(
        lp,
        "runtime_llm",
        lambda: {
            "enabled": True,
            "plan_enabled": True,
            "api_key": "sk-test-key-xxxxxxxx",
            "base_url": "https://api.siliconflow.cn/v1",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "extra_headers": {},
            "organization": "",
            "last_endpoint": "",
            "last_auth_variant": 0,
            "last_payload_variant": 0,
        },
    )
    lines = _many_lines(60)
    obj = lp.call_llm_for_plan(lines, target_seconds=60, playback_speed=1.4)
    assert captured.get("max_tokens") == lp.PLAN_MAX_TOKENS
    assert captured.get("timeout") == lp.PLAN_TIMEOUT_S
    assert captured.get("force_json") is True
    assert captured.get("fast") is True
    # system should be short stability prompt (ids-only)
    msgs = captured.get("messages") or []
    assert msgs and msgs[0]["role"] == "system"
    assert len(msgs[0]["content"]) < 900
    assert "ids" in msgs[0]["content"]
    user_content = msgs[1]["content"]
    assert "候选" in user_content
    assert "all_clauses" not in user_content
    assert "ids" in user_content
    assert "全身效果" in user_content or "上身效果" in user_content or "面料" in user_content
    assert "60" in user_content
    assert obj.get("_meta", {}).get("clauses_sent", 10**9) <= lp.LIGHT_MAX_CLAUSES
    assert obj.get("_meta", {}).get("submit_mode") == "stable_ids_only_asr_selected_clauses"
    assert obj.get("_meta", {}).get("attempt") in {"primary", "retry_light"}
