import re
import subprocess
from pathlib import Path

ffmpeg = Path(r"C:\Users\MR\AppData\Local\ffmpeg\bin\ffmpeg.exe")

def dur(p: Path) -> float:
    proc = subprocess.run(
        [str(ffmpeg), "-i", str(p)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr or "")
    if not m:
        return -1
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)

job = Path(r"C:\Users\MR\AppData\grok\clothing-live-clipper\output\agent_jobs\desktop_batch\001")
parts = sorted((job / "_parts").glob("part_*.mp4"))
print("parts", len(parts))
total = 0.0
for p in parts:
    d = dur(p)
    total += max(0, d)
    print(f"  {p.name}: {d:.3f}s")
print("sum_parts", total)
joined = job / "_parts" / "_joined_1x.mp4"
final = job / "final.mp4"
print("joined", joined.exists(), dur(joined) if joined.exists() else None)
print("final", final.exists(), dur(final) if final.exists() else None)
