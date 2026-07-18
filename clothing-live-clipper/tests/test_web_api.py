from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from clipper import web as webmod
from clipper.web import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.json"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    jobs = tmp_path / "web_jobs"
    jobs.mkdir()
    monkeypatch.setattr(webmod, "JOBS_DIR", jobs)
    # default: no API key unless test sets it
    monkeypatch.delenv("CLIPPER_ASR_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CLIPPER_LLM_API_KEY", raising=False)
    monkeypatch.setenv("CLIPPER_ASR_ENABLED", "true")
    monkeypatch.setenv("CLIPPER_ASR_PROVIDER", "openai_whisper")
    app = create_app()
    with TestClient(app) as c:
        yield c, jobs


def test_health_has_asr_configured(client, monkeypatch):
    c, _ = client
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "ffmpeg" in body
    assert "asr_configured" in body
    # no key in fixture env
    assert body["asr_configured"] is False

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    r2 = c.get("/api/health")
    assert r2.json()["asr_configured"] is True


def test_create_video_only_without_key_400(client):
    c, _ = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "false"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    assert r.status_code == 400
    assert "API" in (r.json().get("detail") or "")


def test_create_video_only_auto_asr(client, monkeypatch, tmp_path):
    c, jobs = client
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(webmod, "which_ffmpeg", lambda: "ffmpeg")

    def fake_transcribe(video, transcript_json, work_dir=None, language="zh"):
        src = FIXTURE.read_text(encoding="utf-8")
        Path(transcript_json).parent.mkdir(parents=True, exist_ok=True)
        Path(transcript_json).write_text(src, encoding="utf-8")
        return Path(transcript_json)

    monkeypatch.setattr(webmod, "transcribe_video_to_json", fake_transcribe)

    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "false"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in {"success", "success_partial"}
    assert body.get("transcript_source") == "whisper_api"
    job_id = body["job_id"]
    assert (jobs / job_id / "plan.json").exists()
    assert (jobs / job_id / "transcript_asr.json").exists()


def test_empty_submit_400(client):
    c, _ = client
    r = c.post("/api/jobs", data={"render": "false"})
    assert r.status_code == 400


def test_list_jobs_includes_new(client):
    c, _ = client
    r = c.post(
        "/api/jobs",
        data={"use_sample": "true", "target_seconds": "60", "render": "false"},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    lst = c.get("/api/jobs").json()["jobs"]
    ids = {j["job_id"] for j in lst}
    assert job_id in ids
