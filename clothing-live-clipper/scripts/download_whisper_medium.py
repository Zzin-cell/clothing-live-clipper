"""Download faster-whisper medium for higher Chinese ASR accuracy."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = "Systran/faster-whisper-medium"
MIRRORS = [
    "https://hf-mirror.com",
    "https://huggingface.co",
]
FILES = [
    "config.json",
    "tokenizer.json",
    "vocabulary.txt",
    "model.bin",
]


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
        "3600",
        "--create-dirs",
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
    local_dir = Path(r"C:\Users\MR\AppData\grok\models\whisper-medium")
    local_dir.mkdir(parents=True, exist_ok=True)
    ok_all = True
    for name in FILES:
        dest = local_dir / name
        if dest.exists() and dest.stat().st_size > 1000:
            print("skip", dest, dest.stat().st_size)
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
        return 1

    print("verifying load…")
    from faster_whisper import WhisperModel

    # load on CPU int8 just to verify files; runtime uses CUDA
    WhisperModel(str(local_dir), device="cpu", compute_type="int8")
    pointer = Path(__file__).resolve().parent / "local_whisper_model_path.txt"
    pointer.write_text(str(local_dir), encoding="utf-8")
    print("LOAD_OK", local_dir)
    print("POINTER", pointer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
