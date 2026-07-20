import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
video = ROOT / "output" / "web_jobs" / "20260719_013931_034905fb" / "uploads" / "7月11日.mp4"
out = ROOT / "output" / "agent_jobs" / "try_7yue11"
print("video exists", video.exists(), video)
cmd = [
    sys.executable,
    str(ROOT / "scripts" / "agent_clip_video.py"),
    str(video),
    "--out",
    str(out),
    "--seconds",
    "60",
]
raise SystemExit(subprocess.call(cmd, cwd=str(ROOT)))
