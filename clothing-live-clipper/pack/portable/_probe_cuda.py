"""Probe whether package venv can see CUDA for faster-whisper/ctranslate2."""
from __future__ import annotations

import os
import site
from pathlib import Path


def main() -> int:
    cands: list[Path] = []
    roots: list[Path] = []
    try:
        roots.extend(Path(p) for p in site.getsitepackages())
    except Exception:
        pass
    try:
        roots.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    exe = Path(__import__("sys").executable).resolve()
    roots.append(exe.parents[1] / "Lib" / "site-packages")

    for sp in roots:
        root = sp / "nvidia"
        if not root.exists():
            continue
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                for leaf in ("bin", "lib"):
                    d = child / leaf
                    if d.exists():
                        cands.append(d)
        except Exception:
            pass

    # de-dup
    uniq: list[str] = []
    seen = set()
    for p in cands:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        uniq.append(s)

    if uniq:
        os.environ["PATH"] = os.pathsep.join(uniq) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            for d in uniq:
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass

    print("python =", __import__("sys").executable)
    print("nvidia_dll_dirs =", len(uniq))
    for d in uniq[:12]:
        print("  ", d)

    try:
        import ctranslate2

        n = int(ctranslate2.get_cuda_device_count() or 0)
        print("ctranslate2_version =", getattr(ctranslate2, "__version__", None))
        print("ctranslate2_cuda_count =", n)
        if n > 0:
            print("RESULT = GPU_OK")
            return 0
        print("RESULT = DRIVER_MAY_BE_OK_BUT_PYTHON_CUDA_COUNT_0")
        print("HINT = reinstall nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12")
        return 2
    except Exception as e:
        print("RESULT = CTRANSLATE2_IMPORT_OR_PROBE_FAIL")
        print("ERROR =", type(e).__name__, e)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
