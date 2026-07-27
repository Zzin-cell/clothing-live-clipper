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
