from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipper import web as webmod
from clipper.web import create_app


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


def test_create_requires_video(client, monkeypatch):
    monkeypatch.setattr("clipper.web.start_job_async", lambda d: True)
    c, _ = client
    r = c.post("/api/jobs", data={"target_seconds": "60", "render": "true"})
    assert r.status_code == 400
    assert "视频" in (r.json().get("detail") or "")


def test_create_video_only_queues(client, monkeypatch):
    # do not run heavy worker in unit test
    monkeypatch.setattr("clipper.web.start_job_async", lambda d: True)
    c, jobs = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "true", "auto_process": "true"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("process_mode") == "local_auto"
    assert body.get("has_video") is True
    assert body["status"] in {"queued", "processing"}
    job_id = body["job_id"]
    assert list((jobs / job_id / "uploads").glob("*.mp4"))


def test_agent_next_claim_and_complete(client, monkeypatch):
    monkeypatch.setattr("clipper.web.start_job_async", lambda d: False)
    c, jobs = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "false", "auto_process": "false"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    job_id = r.json()["job_id"]
    # force queued for agent API compatibility
    meta_path = jobs / job_id / "job_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["status"] = "queued"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    nxt = c.get("/api/agent/next")
    assert nxt.status_code == 200
    payload = nxt.json()
    assert payload["job"]["job_id"] == job_id
    assert payload["job"]["status"] == "claimed"
    assert payload["paths"]["video"]

    assert c.get("/api/agent/next").json()["job"] is None

    plan = {
        "golden": [
            {
                "clip_id": "c1",
                "t0_ms": 0,
                "t1_ms": 2000,
                "text": "显瘦",
                "role": "hook",
                "score": 1,
            }
        ],
        "trust": [],
        "cta": [],
        "total_duration_ms": 2000,
        "golden20_passed": True,
        "warnings": [],
    }
    (jobs / job_id / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )
    done = c.post(
        f"/api/agent/jobs/{job_id}/complete",
        json={"status": "success_partial"},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "success_partial"
    assert done.json()["files"]["plan"] is True


def test_list_jobs_includes_queued(client, monkeypatch):
    monkeypatch.setattr("clipper.web.start_job_async", lambda d: True)
    c, _ = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "auto_process": "true"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    job_id = r.json()["job_id"]
    ids = {j["job_id"] for j in c.get("/api/jobs").json()["jobs"]}
    assert job_id in ids


def test_transcript_get_and_save(client, monkeypatch):
    monkeypatch.setattr("clipper.web.start_job_async", lambda d: False)
    monkeypatch.setattr("clipper.job_worker.start_reclip_async", lambda d: True)
    c, jobs = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "auto_process": "false"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    job_id = r.json()["job_id"]
    raw = [
        {"utt_id": "u1", "text": "这件面料很软", "t0_ms": 0, "t1_ms": 2000},
        {"utt_id": "u2", "text": "家人们来了吗", "t0_ms": 2000, "t1_ms": 3000},
    ]
    (jobs / job_id / "transcript_asr.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    (jobs / job_id / "transcript_for_clipper.json").write_text(
        json.dumps([raw[0]], ensure_ascii=False), encoding="utf-8"
    )
    g = c.get(f"/api/jobs/{job_id}/transcript?kind=all")
    assert g.status_code == 200
    assert g.json()["count"] == 2
    saved = c.put(
        f"/api/jobs/{job_id}/transcript",
        json={
            "reclip": True,
            "items": [
                {"utt_id": "u1", "text": "这件面料很软很显瘦", "t0_ms": 0, "t1_ms": 2000, "keep": True},
                {"utt_id": "u2", "text": "家人们来了吗", "t0_ms": 2000, "t1_ms": 3000, "keep": False},
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    kept = json.loads((jobs / job_id / "transcript_for_clipper.json").read_text(encoding="utf-8"))
    assert len(kept) == 1
    assert "显瘦" in kept[0]["text"]
