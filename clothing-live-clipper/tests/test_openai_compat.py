from __future__ import annotations

from clipper.openai_compat import (
    build_payload_variants,
    extract_chat_text,
    normalize_base_url,
    pick_default_model,
)
from clipper.user_llm import candidate_chat_endpoints


def test_normalize_base_url_variants():
    assert normalize_base_url("https://api.openai.com").endswith("/v1")
    assert normalize_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    assert normalize_base_url("https://x.com/v1/chat/completions") == "https://x.com/v1"
    assert "/chat/completions" in candidate_chat_endpoints("https://x.com/v1")[0]


def test_pick_default_model_prefers_available_chat_models():
    models = ["whisper-1", "grok-4.5", "text-embedding-3-small"]
    assert pick_default_model(models, preferred="gpt-4o-mini") == "grok-4.5"
    assert pick_default_model(models, preferred="grok-4.5") == "grok-4.5"
    assert pick_default_model(["a", "b"], preferred=None) in {"a", "b"}


def test_pick_default_model_prefers_light_qwen():
    models = [
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-7B-Instruct",
        "deepseek-ai/DeepSeek-R1",
        "THUDM/glm-4-9b-chat",
    ]
    picked = pick_default_model(models, preferred=None)
    assert picked == "Qwen/Qwen2.5-7B-Instruct"

    picked2 = pick_default_model(models, preferred="THUDM/glm-4-9b-chat")
    assert picked2 == "THUDM/glm-4-9b-chat"


def test_extract_chat_text_standard_and_parts():
    t1 = extract_chat_text({"choices": [{"message": {"content": '{"a":1}'}}]})
    assert t1.startswith("{")
    t2 = extract_chat_text(
        {"choices": [{"message": {"content": [{"type": "text", "text": "hello"}]}}]}
    )
    assert t2 == "hello"
    t3 = extract_chat_text({"choices": [{"text": "plain"}]})
    assert t3 == "plain"


def test_payload_variants_include_json_and_minimal():
    msgs = [{"role": "user", "content": "hi"}]
    vars = build_payload_variants(model="m", messages=msgs, force_json=True)
    assert any("response_format" in v for v in vars)
    assert any(set(v.keys()) == {"model", "messages"} for v in vars)


def test_payload_variants_disable_thinking_for_qwen3():
    msgs = [{"role": "user", "content": "hi"}]
    vars = build_payload_variants(
        model="Qwen/Qwen3.5-9B", messages=msgs, force_json=True, max_tokens=1024
    )
    assert any(v.get("enable_thinking") is False for v in vars)
    assert any(
        isinstance(v.get("chat_template_kwargs"), dict)
        and v["chat_template_kwargs"].get("enable_thinking") is False
        for v in vars
    )


def test_extract_chat_text_prefers_content_json_over_reasoning():
    t = extract_chat_text(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"keep":[]}',
                        "reasoning_content": "长篇思考过程……没有json",
                    }
                }
            ]
        }
    )
    assert t.startswith("{")
    assert "keep" in t


def test_extract_chat_text_falls_back_to_reasoning_json():
    t = extract_chat_text(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": '分析后输出 {"product_summary":"x","keep":[]}',
                    }
                }
            ]
        }
    )
    assert "product_summary" in t or t.strip().startswith("{")


from unittest.mock import patch

from clipper.openai_compat import (
    OpenAICompatError,
    chat_completions,
    classify_llm_error,
    is_auth_invalid_error,
)


def test_classify_llm_error_siliconflow_token_invalid():
    raw = (
        'HTTP 401 https://api.siliconflow.cn/v1/chat/completions: '
        '{"code":30014,"data":null,"message":"Token is invalid."}'
    )
    info = classify_llm_error(raw, base_url="https://api.siliconflow.cn/v1")
    assert info["error_class"] == "auth_invalid"
    assert "Token" in info["message"] or "无效" in info["message"]
    assert info.get("provider_hint") == "siliconflow"
    assert is_auth_invalid_error(raw) is True


def test_chat_completions_401_stops_quickly():
    calls = {"n": 0}

    def fake_http(url, headers, payload=None, method="POST", timeout=180):
        calls["n"] += 1
        raise OpenAICompatError(
            'HTTP 401 https://api.siliconflow.cn/v1/chat/completions: '
            '{"code":30014,"message":"Token is invalid."}'
        )

    with patch("clipper.openai_compat._http_json", side_effect=fake_http):
        try:
            chat_completions(
                messages=[{"role": "user", "content": "1"}],
                model="Qwen/Qwen2.5-7B-Instruct",
                base_url="https://api.siliconflow.cn/v1",
                api_key="sk-invalid-key-for-test",
                force_json=False,
                timeout=10,
                fast=False,
                cfg={
                    "api_key": "sk-invalid-key-for-test",
                    "base_url": "https://api.siliconflow.cn/v1",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "last_endpoint": "",
                    "last_auth_variant": 0,
                    "last_payload_variant": 0,
                    "extra_headers": {},
                    "organization": "",
                },
            )
            assert False, "expected OpenAICompatError"
        except OpenAICompatError as e:
            msg = str(e)
            assert "401" in msg or "auth_invalid" in msg or "Token" in msg or "无效" in msg

    # Must not run full endpoint x auth x payload Cartesian product
    assert calls["n"] <= 4, f"too many HTTP attempts on 401: {calls['n']}"


def test_chat_completions_fast_with_last_route_is_single_shot():
    calls = {"n": 0}

    def fake_http(url, headers, payload=None, method="POST", timeout=180):
        calls["n"] += 1
        calls["timeout"] = timeout
        return {
            "choices": [{"message": {"role": "assistant", "content": '{"ok":true}'}}]
        }

    with patch("clipper.openai_compat._http_json", side_effect=fake_http):
        with patch("clipper.user_llm.remember_successful_route"):
            out = chat_completions(
                messages=[{"role": "user", "content": "1"}],
                model="Qwen/Qwen2.5-7B-Instruct",
                base_url="https://api.siliconflow.cn/v1",
                api_key="sk-test-key-xxxxxxxx",
                force_json=True,
                timeout=35,
                fast=True,
                cfg={
                    "api_key": "sk-test-key-xxxxxxxx",
                    "base_url": "https://api.siliconflow.cn/v1",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "last_endpoint": "https://api.siliconflow.cn/v1/chat/completions",
                    "last_auth_variant": 0,
                    "last_payload_variant": 0,
                    "extra_headers": {},
                    "organization": "",
                },
            )
    assert out.get("content")
    assert calls["n"] == 1
    assert calls["timeout"] <= 30
