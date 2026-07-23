from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from clipper.learning import clear_learning, learning_status

print("clear", clear_learning(keep_events_backup=True))
env = os.environ.copy()
env["PYTHONPATH"] = str(ROOT / "src")
env["CLIPPER_ASR_DEVICE"] = "cuda"
env["CLIPPER_ASR_COMPUTE_TYPE"] = "float16"
env["CLIPPER_ASR_QUALITY"] = "high"
env["CLIPPER_LOCAL_WHISPER_MODEL"] = r"C:\Users\MR\AppData\grok\models\whisper-small"
env["CLIPPER_ASR_BEAM_SIZE"] = "3"
env["CLIPPER_ASR_BEST_OF"] = "3"
ff = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if ff.exists():
    env["PATH"] = str(ff) + os.pathsep + env.get("PATH", "")

cmd = [sys.executable, str(ROOT / "scripts" / "bootstrap_learning_from_folder.py")]
print("run", cmd)
p = subprocess.run(cmd, cwd=str(ROOT), env=env)
print("bootstrap_exit", p.returncode)
print(json.dumps(learning_status(), ensure_ascii=False, indent=2)[:2500])
raise SystemExit(p.returncode)
