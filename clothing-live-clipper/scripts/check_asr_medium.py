import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("CLIPPER_ASR_DEVICE", "cuda")
os.environ.setdefault("CLIPPER_ASR_COMPUTE_TYPE", "float16")
os.environ.setdefault("CLIPPER_ASR_QUALITY", "high")
os.environ.setdefault("CLIPPER_ASR_BEAM_SIZE", "5")
os.environ.setdefault("CLIPPER_ASR_DENOISE", "1")
os.environ["CLIPPER_LOCAL_WHISPER_MODEL"] = r"C:\Users\MR\AppData\grok\models\whisper-medium"

from agent_clip_video import resolve_local_model, _cuda_available, _get_whisper_model

print("cuda", _cuda_available())
print("model", resolve_local_model())
m = _get_whisper_model(resolve_local_model())
print("loaded_ok", type(m).__name__)
