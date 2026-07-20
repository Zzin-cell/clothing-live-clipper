import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from filter_transcript_v2 import classify, filter_for_duration

for name in ("001", "002"):
    p = Path(r"C:\Users\MR\AppData\grok\clothing-live-clipper\output\agent_jobs\desktop_batch") / name / "transcript_asr.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    print("====", name, "raw", len(raw))
    counts = {"strong": 0, "medium": 0, "price": 0, "drop": 0}
    strong_ms = med_ms = 0
    for u in raw:
        g = classify(u["text"])
        counts[g] += 1
        d = max(0, u["t1_ms"] - u["t0_ms"])
        if g == "strong":
            strong_ms += d
        if g == "medium":
            med_ms += d
        if g != "drop" and d >= 1500:
            print(f"  [{g}] {d}ms {u['text']}")
    kept = filter_for_duration(raw)
    kept_ms = sum(max(0, u["t1_ms"] - u["t0_ms"]) for u in kept)
    print(counts, "strong_ms", strong_ms, "med_ms", med_ms, "kept", len(kept), "kept_ms", kept_ms)
