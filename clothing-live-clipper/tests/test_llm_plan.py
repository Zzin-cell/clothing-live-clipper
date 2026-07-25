from __future__ import annotations

from clipper.llm_plan import llm_obj_to_timeline
from clipper.models import TimelinePlan


def _lines():
    return [
        {"utt_id": "u1", "text": "家人们扣1点关注", "t0_ms": 0, "t1_ms": 2000},
        {"utt_id": "u2", "text": "这件面料超级软还不透", "t0_ms": 3000, "t1_ms": 7000},
        {"utt_id": "u3", "text": "收腰版型梨形显瘦", "t0_ms": 8000, "t1_ms": 12000},
        {"utt_id": "u4", "text": "贴肤冰冰的很舒服不闷", "t0_ms": 13000, "t1_ms": 17000},
        {"utt_id": "u5", "text": "建议穿M码偏大", "t0_ms": 18000, "t1_ms": 20000},
        {"utt_id": "u6", "text": "底下蕾丝拼接很精致", "t0_ms": 21000, "t1_ms": 25000},
    ]


def test_llm_obj_to_timeline_orders_and_clamps():
    llm_obj = {
        "product_summary": "软糯不透+收腰显瘦",
        "logic": ["卖点", "版型", "体验", "细节"],
        "keep": [
            {"id": "u2", "t0_ms": 3000, "t1_ms": 7000, "text": "这件面料超级软还不透", "why": "卖点"},
            {"id": "u3", "t0_ms": 8000, "t1_ms": 12000, "text": "收腰版型梨形显瘦", "why": "版型"},
            # out-of-range timestamps should clamp into source window
            {"id": "u4", "t0_ms": 1000, "t1_ms": 999999, "text": "贴肤冰冰的很舒服不闷", "why": "体验"},
            {"id": "u6", "t0_ms": 21000, "t1_ms": 25000, "text": "底下蕾丝拼接很精致", "why": "细节"},
            # should be dropped by safety even if llm kept
            {"id": "u5", "t0_ms": 18000, "t1_ms": 20000, "text": "建议穿M码偏大", "why": "bad"},
        ],
        "drop_ids": ["u1", "u5"],
    }
    plan = llm_obj_to_timeline(llm_obj, _lines(), target_seconds=60, playback_speed=1.0)
    assert isinstance(plan, TimelinePlan)
    assert plan.golden
    texts = [s.text for s in plan.golden]
    assert "面料" in texts[0]
    assert all("M码" not in t for t in texts)
    # clamp check for u4
    u4 = next(s for s in plan.golden if "舒服" in s.text)
    assert u4.t0_ms >= 13000
    assert u4.t1_ms <= 17200
    assert any("llm_logic_plan" in w for w in plan.warnings)


def test_llm_empty_keep_yields_empty_plan_flag():
    plan = llm_obj_to_timeline({"keep": []}, _lines(), target_seconds=60, playback_speed=1.4)
    assert plan.golden == []
    assert any("llm_empty_keep" in w for w in plan.warnings)
