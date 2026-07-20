import re
import subprocess
from pathlib import Path

ffmpeg = Path(r"C:\Users\MR\AppData\Local\ffmpeg\bin\ffmpeg.exe")
print("ffmpeg", ffmpeg.exists())
for p in [
    Path(r"C:\Users\MR\Desktop") / "检查文件" / "已经完成" / "001" / "final.mp4",
    Path(r"C:\Users\MR\Desktop") / "检查文件" / "已经完成" / "002" / "final.mp4",
]:
    print("---", p.exists(), p)
    if not p.exists():
        continue
    proc = subprocess.run(
        [str(ffmpeg), "-i", str(p)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr or "")
    if m:
        h, mi, s = m.groups()
        sec = int(h) * 3600 + int(mi) * 60 + float(s)
        print(f"duration={sec:.2f}s size={p.stat().st_size}")
    else:
        print("duration parse fail", (proc.stderr or "")[:300])
    rev = p.parent / "review.md"
    if rev.exists():
        for line in rev.read_text(encoding="utf-8").splitlines()[:12]:
            print(line)
