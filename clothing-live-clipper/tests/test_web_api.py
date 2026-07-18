from __future__ import annotations

import io
import json
from pathlib import Path

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
    app = create_app()
    with TestClient(app) as c:
        yield c, jobs


def test_health_has_asr_configured(client):
    c, _ = client
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "ffmpeg" in body
    assert "asr_configured" in body
    assert body["asr_configured"] is False  # default


def test_create_video_only_needs_transcript(client):
    c, jobs = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "false"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "needs_transcript"
    job_id = body["job_id"]
    assert (jobs / job_id / "uploads").exists()
    vids = list((jobs / job_id / "uploads").glob("*.mp4"))
    assert vids, "video should be saved under uploads"


def test_empty_submit_400(client):
    c, _ = client
    r = c.post("/api/jobs", data={"render": "false"})
    assert r.status_code == 400


def test_attach_transcript_and_process(client):
    c, jobs = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "false"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    tr = FIXTURE.read_bytes()
    r2 = c.post(
        f"/api/jobs/{job_id}/transcript",
        data={"render": "false"},
        files={"transcript": ("t.json", io.BytesIO(tr), "application/json")},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] in {"success", "success_partial"}
    assert body.get("files", {}).get("plan") is True
    assert (jobs / job_id / "plan.json").exists()


def test_list_jobs_includes_new(client):
    c, _ = client
    tr = FIXTURE.read_bytes()
    r = c.post(
        "/api/jobs",
        data={"use_sample": "false", "target_seconds": "60", "render": "false"},
        files={"transcript": ("t.json", io.BytesIO(tr), "application/json")},
    )
    # sample path optional — if use_sample true preferred:
    assert r.status_code in {200, 400}
    # primary: use_sample
    r = c.post(
        "/api/jobs",
        data={"use_sample": "true", "target_seconds": "60", "render": "false"},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    lst = c.get("/api/jobs").json()["jobs"]
    ids = {j["job_id"] for j in lst}
    assert job_id in ids
