from __future__ import annotations

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
    selected, stats = select_clauses_for_llm(clauses, max_clauses=150)
    assert stats["clauses_raw"] == len(clauses)
    assert stats["clauses_sent"] == len(selected)
    assert len(selected) <= 150
    assert stats["clauses_sent"] <= stats["clauses_raw"]
    # no invented ids
    raw_ids = {c["id"] for c in clauses}
    assert all(c["id"] in raw_ids for c in selected)
    # size / control should be rare or zero in selection
    joined = " ".join(c["text"] for c in selected)
    assert "M码" not in joined
    assert "扣1" not in joined
    assert stats.get("dropped_size", 0) >= 1 or stats.get("dropped_control", 0) >= 1


def test_expand_lines_to_clauses_splits_full_asr():
    clauses = expand_lines_to_clauses(_lines())
    assert len(clauses) >= len(_lines())
    # multi-clause line becomes multiple units
    texts = " ".join(c["text"] for c in clauses)
    assert "超级软" in texts and "不透" in texts
    assert all(c["t1_ms"] > c["t0_ms"] for c in clauses)


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


def test_llm_empty_keep_yields_empty_plan_flag():
    plan = llm_obj_to_timeline({"keep": []}, _lines(), target_seconds=60, playback_speed=1.4)
    assert plan.golden == []
    assert any("llm_empty_keep" in w for w in plan.warnings)


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
