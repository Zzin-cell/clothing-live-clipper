from __future__ import annotations

import io
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


def test_health_ok(client):
    c, _ = client
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_create_video_queues_for_agent(client):
    c, jobs = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "true", "mode": "agent"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body.get("process_mode") == "agent"
    job_id = body["job_id"]
    assert list((jobs / job_id / "uploads").glob("*.mp4"))


def test_agent_next_claim_and_complete(client):
    c, jobs = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "false", "mode": "agent"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    job_id = r.json()["job_id"]

    nxt = c.get("/api/agent/next")
    assert nxt.status_code == 200
    payload = nxt.json()
    assert payload["job"]["job_id"] == job_id
    assert payload["job"]["status"] == "claimed"
    assert payload["paths"]["video"]

    # empty second claim
    nxt2 = c.get("/api/agent/next")
    assert nxt2.json()["job"] is None

    # agent writes plan
    plan = {
        "golden": [{"clip_id": "c1", "t0_ms": 0, "t1_ms": 2000, "text": "显瘦", "role": "hook", "score": 1}],
        "trust": [],
        "cta": [],
        "total_duration_ms": 2000,
        "golden20_passed": True,
        "warnings": [],
    }
    import json

    (jobs / job_id / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )
    done = c.post(f"/api/agent/jobs/{job_id}/complete", json={"status": "success_partial"})
    assert done.status_code == 200
    assert done.json()["status"] == "success_partial"
    assert done.json()["files"]["plan"] is True


def test_empty_submit_400(client):
    c, _ = client
    r = c.post("/api/jobs", data={"render": "false", "mode": "agent"})
    assert r.status_code == 400


def test_list_jobs_includes_queued(client):
    c, _ = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "mode": "agent"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    job_id = r.json()["job_id"]
    ids = {j["job_id"] for j in c.get("/api/jobs").json()["jobs"]}
    assert job_id in ids
