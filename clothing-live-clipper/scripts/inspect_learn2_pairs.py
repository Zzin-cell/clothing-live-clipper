from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ff = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if ff.exists():
    os.environ["PATH"] = str(ff) + os.pathsep + os.environ.get("PATH", "")

ROOT = Path(r"C:\Users\MR\Desktop\检查文件\学习2.0\新建文件夹 (18)\新建文件夹")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".mts", ".m2ts"}


def which_ffprobe() -> str:
    import shutil

    cand = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin" / "ffprobe.exe"
    if cand.exists():
        return str(cand)
    p = shutil.which("ffprobe")
    if p:
        return p
    return str(cand)


def duration(p: Path) -> float:
    exe = which_ffprobe()
    cmd = [
        exe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(p),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


pairs = []
for d in sorted([x for x in ROOT.iterdir() if x.is_dir()], key=lambda x: x.name):
    vids = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT]
    info = []
    for v in vids:
        info.append(
            {
                "name": v.name,
                "size": v.stat().st_size,
                "dur": round(duration(v), 2),
                "path": str(v),
            }
        )
    info.sort(key=lambda x: x["dur"])
    print("\n===", d.name)
    for i in info:
        print(f"  {i['dur']:7.1f}s  {i['size']/1e6:7.1f}MB  {i['name']}")
    if len(info) >= 2:
        short, longv = info[0], info[-1]
        pairs.append({"folder": d.name, "positive": short, "negative_source": longv})

out = Path(r"C:\Users\MR\AppData\grok\clothing-live-clipper\output\learning_bootstrap\learn2_pairs.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8")
print("\npairs", len(pairs), "->", out)
