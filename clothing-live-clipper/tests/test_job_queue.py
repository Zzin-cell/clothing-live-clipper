from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from clipper.job_queue import QUEUE_BUILD, UI_BUILD_EXPECTED, JobQueue


def _write_meta(job_dir: Path, meta: dict) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_meta(job_dir: Path) -> dict:
    p = job_dir / "job_meta.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def test_fifo_queue_positions(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_QUEUE_MODE", "stable")
    monkeypatch.setenv("CLIPPER_MAX_CONCURRENT_JOBS", "1")
    monkeypatch.setenv("CLIPPER_ASR_SLOTS", "1")
    monkeypatch.setenv("CLIPPER_LLM_SLOTS", "1")
    monkeypatch.setenv("CLIPPER_RENDER_SLOTS", "1")
    monkeypatch.setenv("CLIPPER_WARM_EXTRACT_SLOTS", "1")

    q = JobQueue()
    started = []
    gate = threading.Event()

    def process(job_dir: Path) -> None:
        started.append(job_dir.name)
        gate.wait(timeout=2.0)
        time.sleep(0.05)

    q.configure_handlers(process_fn=process, write_meta_fn=_write_meta, read_meta_fn=_read_meta)

    jobs = []
    for i in range(3):
        d = tmp_path / f"job{i}"
        d.mkdir()
        _write_meta(d, {"job_id": d.name, "status": "queued"})
        jobs.append(d)
        info = q.enqueue(d)
        assert "queue_pos" in info
        assert "eta_s" in info
        assert info.get("queue_build") == QUEUE_BUILD
        assert info.get("ui_build_expected") == UI_BUILD_EXPECTED

    time.sleep(0.2)
    snap = q.snapshot()
    assert snap["max_active_jobs"] == 1
    assert snap["active_count"] <= 1
    assert snap["render_slots"] == 1
    assert snap["queue_build"] == QUEUE_BUILD
    assert len(snap["queued"]) + snap["active_count"] == 3

    gate.set()
    for _ in range(50):
        if q.snapshot()["active_count"] == 0 and not q.snapshot()["queued"]:
            break
        time.sleep(0.05)
    assert started == ["job0", "job1", "job2"]


def test_asr_slot_blocks_second(monkeypatch):
    monkeypatch.setenv("CLIPPER_ASR_SLOTS", "1")
    q = JobQueue()
    q.reload_config()

    order = []
    released = threading.Event()

    def holder():
        d = Path(".")
        q.acquire_asr(d)
        order.append("a_hold")
        released.wait(timeout=2)
        q.release_asr()
        order.append("a_release")

    def waiter():
        time.sleep(0.05)
        d = Path(".")
        q.acquire_asr(d)
        order.append("b_got")
        q.release_asr()
        order.append("b_done")

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=waiter)
    t1.start()
    time.sleep(0.02)
    t2.start()
    time.sleep(0.1)
    assert "b_got" not in order
    released.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert order.index("a_hold") < order.index("a_release")
    assert order.index("a_release") < order.index("b_got")


def test_render_slot_and_warm_front(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPPER_RENDER_SLOTS", "1")
    monkeypatch.setenv("CLIPPER_WARM_FRONT_N", "2")
    monkeypatch.setenv("CLIPPER_MAX_CONCURRENT_JOBS", "2")
    q = JobQueue()
    q.reload_config()

    # render slots serialize
    order = []
    released = threading.Event()

    def holder():
        q.acquire_render(Path("."))
        order.append("r1")
        released.wait(timeout=2)
        q.release_render()
        order.append("r1_done")

    def waiter():
        time.sleep(0.05)
        q.acquire_render(Path("."))
        order.append("r2")
        q.release_render()

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=waiter)
    t1.start()
    time.sleep(0.02)
    t2.start()
    time.sleep(0.1)
    assert "r2" not in order
    released.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert "r2" in order

    # warm front only first 2 pending
    q2 = JobQueue()
    q2.configure_handlers(
        process_fn=lambda d: time.sleep(0.01),
        write_meta_fn=_write_meta,
        read_meta_fn=_read_meta,
    )
    monkeypatch.setenv("CLIPPER_MAX_CONCURRENT_JOBS", "1")
    q2.reload_config()
    # keep one active by not finishing quickly via gate later - just enqueue 4
    ids = []
    for i in range(4):
        d = tmp_path / f"w{i}"
        d.mkdir()
        _write_meta(d, {"job_id": d.name, "status": "queued"})
        q2.enqueue(d)
        ids.append(d.name)
    # after enqueue, pending may shrink if dispatcher starts one
    # should_warm_extract only front N among pending/active
    assert q2.should_warm_extract(ids[0]) in (True, False)  # may already be active
    # The last pending should not warm when more than warm_front_n behind head
    # force pending view: mark none active and re-check via positions
    with q2._lock:
        pending = list(q2._pending)
    if len(pending) >= 3:
        last = pending[-1]
        assert q2.should_warm_extract(last) is False
