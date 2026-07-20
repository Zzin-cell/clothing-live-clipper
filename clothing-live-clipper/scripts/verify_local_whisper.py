from pathlib import Path
from faster_whisper import WhisperModel

model_dir = Path(r"C:\Users\MR\AppData\grok\models\whisper-tiny")
print("files:", sorted(p.name for p in model_dir.iterdir()))
print("loading…")
m = WhisperModel(str(model_dir), device="cpu", compute_type="int8")
print("LOAD_OK", model_dir)
# pointer for other scripts
pointer = Path(__file__).resolve().parents[1] / "scripts" / "local_whisper_model_path.txt"
pointer.write_text(str(model_dir), encoding="utf-8")
print("POINTER", pointer)
