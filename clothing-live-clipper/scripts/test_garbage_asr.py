from asr_enhance import apply_text_corrections, enhance_asr_segments, is_garbage_asr_text

samples = [
    "对,对,对,对,对,对,对,对,对,对,对,对,对,对,对,对",
    "xy,xy,xy,xy,xy,xy,xy,xy,xy,xy",
    "对对对对对对对对对对对对",
    "嗯嗯嗯嗯嗯嗯嗯",
    "面料很软，显瘦还不透",
    "这件裙子版型好，收腰显瘦",
    "对，这件面料很软",
]
print("is_garbage:")
for s in samples:
    print(is_garbage_asr_text(s), "|", apply_text_corrections(s)[:40], "|", s[:40])

items = [
    {"utt_id": "1", "text": samples[0], "t0_ms": 0, "t1_ms": 2000},
    {"utt_id": "2", "text": samples[1], "t0_ms": 2000, "t1_ms": 4000},
    {"utt_id": "3", "text": samples[4], "t0_ms": 4000, "t1_ms": 7000},
    {"utt_id": "4", "text": samples[5], "t0_ms": 7000, "t1_ms": 10000},
]
out = enhance_asr_segments(items)
print("\nenhanced keep:")
for u in out:
    print(u["text"])
assert all("xy" not in u["text"] for u in out)
assert all(u["text"].count("对") <= 2 for u in out)
print("OK")
