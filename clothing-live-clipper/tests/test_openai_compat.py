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
