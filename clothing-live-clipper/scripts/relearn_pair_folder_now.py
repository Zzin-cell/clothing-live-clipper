from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = Path(r"C:\Users\MR\Desktop\检查文件\学习2.0\新建文件夹 (18)\新建文件夹")

env = os.environ.copy()
env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT / "scripts")
ff = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if ff.exists():
    env["PATH"] = str(ff) + os.pathsep + env.get("PATH", "")
env["CLIPPER_ASR_DEVICE"] = "cuda"
env["CLIPPER_ASR_COMPUTE_TYPE"] = "float16"
env["CLIPPER_ASR_QUALITY"] = "high"
env["CLIPPER_LOCAL_WHISPER_MODEL"] = r"C:\Users\MR\AppData\grok\models\whisper-small"
env["CLIPPER_ASR_BEAM_SIZE"] = "3"
env["CLIPPER_ASR_BEST_OF"] = "3"

print("folder exists", FOLDER.exists(), FOLDER)
if not FOLDER.exists():
    raise SystemExit(1)

# 1) refresh ASR pair bootstrap (reuses wav if present)
p1 = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "bootstrap_learning_posneg_pairs.py"), str(FOLDER)],
    cwd=str(ROOT),
    env=env,
)
print("bootstrap_exit", p1.returncode)
if p1.returncode != 0:
    raise SystemExit(p1.returncode)

# 2) rebuild clause-level prefs from seeded transcripts
p2 = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "rebuild_learning_clause_level.py")],
    cwd=str(ROOT),
    env=env,
)
print("rebuild_exit", p2.returncode)
if p2.returncode != 0:
    raise SystemExit(p2.returncode)

# 3) print effect
p3 = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "check_learning_effect.py")],
    cwd=str(ROOT),
    env=env,
)
print("check_exit", p3.returncode)
raise SystemExit(p3.returncode)
