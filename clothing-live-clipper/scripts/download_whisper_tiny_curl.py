"""Download faster-whisper tiny files via curl from hf-mirror (no huggingface_hub DNS issues)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# CTranslate2 model layout for faster-whisper
REPO = "Systran/faster-whisper-tiny"
MIRRORS = [
    "https://hf-mirror.com",
    "https://huggingface.co",
]
FILES = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.txt",
    "model.bin",
]

# faster-whisper / HF hub expected cache structure
# models--Systran--faster-whisper-tiny/snapshots/<rev>/...
# We put files in a local dir and point WHISPER_MODEL or use local path.


def curl_download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    cmd = [
        "curl.exe",
        "-L",
        "--fail",
        "--retry",
        "5",
        "--retry-delay",
        "2",
        "--connect-timeout",
        "30",
        "--max-time",
        "600",
        "-o",
        str(tmp),
        url,
    ]
    print("GET", url)
    p = subprocess.run(cmd)
    if p.returncode != 0:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    print(" OK", dest, dest.stat().st_size)
    return True


def main() -> int:
    # Use a simple local path faster-whisper accepts
    local_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "faster-whisper-models" / "tiny"
    local_dir.mkdir(parents=True, exist_ok=True)

    ok_all = True
    for name in FILES:
        dest = local_dir / name
        if dest.exists() and dest.stat().st_size > 100:
            print("skip existing", dest, dest.stat().st_size)
            continue
        got = False
        for mir in MIRRORS:
            url = f"{mir}/{REPO}/resolve/main/{name}"
            if curl_download(url, dest):
                got = True
                break
        if not got:
            print("FAIL", name)
            ok_all = False

    if not ok_all:
        print("PARTIAL_OR_FAILED", local_dir)
        return 1

    # verify load
    print("verifying load…")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from faster_whisper import WhisperModel

    m = WhisperModel(str(local_dir), device="cpu", compute_type="int8")
    print("LOAD_OK", local_dir)
    # write pointer file for agent scripts
    pointer = Path(__file__).resolve().parents[1] / "scripts" / "local_whisper_model_path.txt"
    pointer.write_text(str(local_dir), encoding="utf-8")
    print("POINTER", pointer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
