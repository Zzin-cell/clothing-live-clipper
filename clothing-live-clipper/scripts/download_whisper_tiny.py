"""Download faster-whisper tiny model via hf-mirror."""
from __future__ import annotations

import os
from pathlib import Path

os.environ["HF_ENDPOINT"] = os.environ.get("HF_ENDPOINT") or "https://hf-mirror.com"

from huggingface_hub import snapshot_download

# Systran faster-whisper tiny
repo = "Systran/faster-whisper-tiny"
local = Path.home() / ".cache" / "huggingface" / "hub" / "models--Systran--faster-whisper-tiny"
print("downloading", repo, "via", os.environ["HF_ENDPOINT"])
path = snapshot_download(repo_id=repo, local_dir=None)
print("OK", path)
