from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipper import config as cfg
from clipper import web as webmod
from clipper.web import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    jobs = tmp_path / "web_jobs"
    jobs.mkdir()
    env_path = tmp_path / ".env"
    monkeypatch.setattr(webmod, "JOBS_DIR", jobs)
    monkeypatch.setattr(cfg, "DEFAULT_ENV_PATH", env_path)
    monkeypatch.setattr("clipper.system_status.JOBS_DIR", jobs)
    cfg.session_clear()
    for k in (
        "CLIPPER_ASR_API_KEY",
        "OPENAI_API_KEY",
        "CLIPPER_LLM_API_KEY",
        "CLIPPER_ASR_BASE_URL",
        "CLIPPER_ASR_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLIPPER_ASR_ENABLED", "true")
    monkeypatch.setenv("CLIPPER_ASR_PROVIDER", "openai_whisper")
    app = create_app()
    with TestClient(app) as c:
        yield c, env_path
    cfg.session_clear()


def test_system_status_shape(client):
    c, _ = client
    r = c.get("/api/system/status")
    assert r.status_code == 200
    body = r.json()
    assert "lights" in body
    assert "asr" in body
    assert "compat" in body
    assert body["asr"]["configured"] is False
    assert "api_key" not in body.get("config", {})


def test_config_put_session_only(client):
    c, env_path = client
    r = c.put(
        "/api/system/config",
        json={
            "persist": False,
            "api_key": "sk-test-session-key-1234",
            "base_url": "https://example.com/v1",
            "asr_model": "whisper-1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["config"]["has_api_key"] is True
    assert body["config"]["api_key_hint"] == "1234"
    assert not env_path.exists()
    # full key never returned
    assert "sk-test-session-key-1234" not in r.text


def test_config_put_persist_env(client):
    c, env_path = client
    r = c.put(
        "/api/system/config",
        json={
            "persist": True,
            "api_key": "sk-persist-9999",
            "base_url": "https://api.openai.com/v1",
            "asr_model": "whisper-1",
            "llm_model": "gpt-4o-mini",
        },
    )
    assert r.status_code == 200
    assert env_path.exists()
    text = env_path.read_text(encoding="utf-8")
    assert "CLIPPER_ASR_API_KEY=sk-persist-9999" in text
    assert "sk-persist-9999" not in r.text
    g = c.get("/api/system/config")
    assert g.json()["has_api_key"] is True
    assert g.json()["api_key_hint"] == "9999"


def test_health_includes_lights(client):
    c, _ = client
    r = c.get("/api/health")
    assert r.status_code == 200
    assert "lights" in r.json()
