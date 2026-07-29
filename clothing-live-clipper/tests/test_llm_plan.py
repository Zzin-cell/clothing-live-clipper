from __future__ import annotations

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
    # system should be short stability prompt
    msgs = captured.get("messages") or []
    assert msgs and msgs[0]["role"] == "system"
    assert len(msgs[0]["content"]) < 900
    user_content = msgs[1]["content"]
    assert "已筛选" in user_content
    assert "all_clauses" not in user_content
    assert "must_cover" in user_content
    assert "版型" in user_content and "面料" in user_content and "适用人群" in user_content
    assert "60" in user_content or "target_s" in user_content
    assert obj.get("_meta", {}).get("clauses_sent", 10**9) <= lp.LIGHT_MAX_CLAUSES
    assert obj.get("_meta", {}).get("attempt") in {"primary", "retry_light"}
