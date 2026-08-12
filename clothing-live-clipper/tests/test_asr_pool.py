from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import agent_clip_video as A  # noqa: E402
from agent_clip_video import AsrDevicePool  # noqa: E402


def test_asr_pool_two_gpus_parallel_capacity():
    pool = AsrDevicePool([0, 1], allow_cpu=False)
    assert pool.capacity == 2
    a = pool.acquire()
    b = pool.acquire()
    assert {a, b} == {"cuda:0", "cuda:1"}
    snap = pool.snapshot()
    assert snap["free"] == 0
    assert sorted(snap["busy"]) == ["cuda:0", "cuda:1"]
    pool.release(a)
    snap2 = pool.snapshot()
    assert snap2["free"] == 1
    c = pool.acquire()
    assert c in {"cuda:0", "cuda:1"}
    pool.release(b)
    pool.release(c)
    assert pool.snapshot()["free"] == 2


def test_asr_pool_cpu_fallback_when_no_gpu():
    pool = AsrDevicePool([], allow_cpu=True)
    assert pool.capacity == 1
    tok = pool.acquire()
    assert tok == "cpu"
    pool.release(tok)


def test_detect_gpu_indices_filters_invalid_and_respects_count(monkeypatch):
    monkeypatch.setenv("CLIPPER_ASR_GPU_IDS", "0,1,9")
    monkeypatch.setattr(A, "_cuda_available", lambda: True)

    class _Ct2:
        @staticmethod
        def get_cuda_device_count():
            return 2

    # Patch the symbol used inside _detect_gpu_indices via import ctranslate2
    import types
    import sys

    mod = types.ModuleType("ctranslate2")
    mod.get_cuda_device_count = lambda: 2  # type: ignore
    monkeypatch.setitem(sys.modules, "ctranslate2", mod)
    ids = A._detect_gpu_indices()
    assert ids == [0, 1]


def test_detect_gpu_indices_empty_when_no_cuda(monkeypatch):
    monkeypatch.setenv("CLIPPER_ASR_GPU_IDS", "0,1")
    monkeypatch.setattr(A, "_cuda_available", lambda: False)
    assert A._detect_gpu_indices() == []
