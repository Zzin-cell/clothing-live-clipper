from __future__ import annotations

from pathlib import Path

import clipper.user_llm as UL


def test_user_llm_save_and_public(tmp_path, monkeypatch):
    p = tmp_path / "llm.json"
    monkeypatch.setattr(UL, "USER_CFG_PATH", p)
    UL._CACHE.clear()

    pub = UL.public_user_llm()
    assert pub["has_key"] is False
    assert pub["plan_ready"] is False

    UL.save_user_llm(
        {
            "llm_plan": True,
            "llm_enabled": True,
            "llm_base_url": "https://example.com/v1",
            "llm_model": "grok-4.5",
            "llm_api_key": "sk-test-key-1234",
        }
    )
    pub2 = UL.public_user_llm()
    assert pub2["has_key"] is True
    assert pub2["plan_ready"] is True
    assert pub2["model"] == "grok-4.5"
    assert pub2["key_hint"] == "1234"

    # blank key should keep old
    UL.save_user_llm({"llm_model": "gpt-x", "llm_api_key": ""})
    rt = UL.runtime_llm()
    assert rt["api_key"] == "sk-test-key-1234"
    assert rt["model"] == "gpt-x"

    headers = UL.build_openai_headers(rt)
    assert headers["Authorization"].startswith("Bearer ")
    assert "api-key" in headers and "x-api-key" in headers
