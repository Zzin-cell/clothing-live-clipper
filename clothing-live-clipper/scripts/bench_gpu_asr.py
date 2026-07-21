"""Quick GPU ASR smoke test on existing wav if available."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("CLIPPER_ASR_DEVICE", "cuda")
os.environ.setdefault("CLIPPER_ASR_COMPUTE_TYPE", "float16")
os.environ.setdefault("CLIPPER_ASR_QUALITY", "high")
os.environ.setdefault(
    "CLIPPER_LOCAL_WHISPER_MODEL",
    str(Path(r"C:\Users\MR\AppData\grok\models\whisper-small")),
)

from agent_clip_video import asr_local, resolve_local_model, _cuda_available


def main() -> int:
    print("cuda_available", _cuda_available())
    print("model", resolve_local_model())
    jobs = ROOT / "output" / "web_jobs"
    wav = None
    if jobs.exists():
        for d in sorted(jobs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            cand = d / "asr_work" / "audio_16k.wav"
            if cand.exists() and cand.stat().st_size > 100_000:
                wav = cand
                break
    if not wav:
        print("no wav found to bench")
        return 1
    print("wav", wav, wav.stat().st_size)
    t0 = time.time()
    segs = asr_local(wav)
    dt = time.time() - t0
    print("segments", len(segs), f"elapsed={dt:.1f}s")
    for s in segs[:5]:
        print("-", s["t0_ms"], s["t1_ms"], s["text"][:60])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
