"""Strip developer junk from built portable package on Desktop."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

DESKTOP = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
PKG = DESKTOP / "小面CapCut-便携版"

# directories to purge anywhere under package app tree
PURGE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    "tests",
    "docs",
    ".superpowers",
}

# file name prefixes / exact junk under scripts
JUNK_PREFIXES = (
    "_commit_msg",
    "_probe_",
    "_diag_",
    "_check_",
    "_patch_",
    "_scan_",
    "_find_",
    "_styles_",
    "_blank_",
    "_parse_",
    "_run_",
    "_write_",
    "_boot_",
    "_styles",
)
JUNK_EXACT = {
    "migrate_env_llm_to_user.py",
    "make_share_package.py",  # old packer, not for end users
    "agent_clip_video.py",
    "bench_gpu_asr.py",
    "bootstrap_learning_from_folder.py",
    "bootstrap_learning_posneg_pairs.py",
    "check_asr_medium.py",
    "download_whisper_medium.py",
    "download_whisper_small.py",
    "download_whisper_tiny_curl.py",
    "process_claimed_job.py",
    "process_one_job.py",
    "relearn_pair_folder_now.py",
    "reseed_learning.py",
    "verify_local_whisper.py",
    "install_ffmpeg.ps1",
    "local_whisper_model_path.txt",
    "restart_web.bat",
    "start_web_now.bat",
    "build_portable_package.py",  # developer only
}


def should_delete_file(path: Path) -> bool:
    name = path.name
    if name in JUNK_EXACT:
        return True
    if name.startswith(JUNK_PREFIXES):
        return True
    if name.endswith((".pyc", ".pyo", ".log", ".part", ".tmp")):
        return True
    # pytest / cache leftovers
    if name == ".coverage" or name.startswith("test_") and path.suffix == ".py" and "tests" in path.parts:
        return True
    return False


def main() -> int:
    if not PKG.exists():
        print("package not found:", PKG)
        return 1

    removed_dirs = 0
    removed_files = 0

    # walk bottom-up for dirs
    for root, dirs, files in os.walk(PKG, topdown=False):
        root_p = Path(root)
        for d in list(dirs):
            if d in PURGE_DIR_NAMES:
                p = root_p / d
                try:
                    shutil.rmtree(p, ignore_errors=True)
                    removed_dirs += 1
                    print("rmdir", p.relative_to(PKG))
                except Exception as e:
                    print("skip dir", p, e)
        for f in files:
            p = root_p / f
            # never remove pack/portable runtime scripts or user guides
            rel = p.relative_to(PKG)
            parts = rel.parts
            if parts[:2] == ("pack", "portable"):
                continue
            # keep root launchers / docs
            if len(parts) == 1 and p.suffix.lower() in {".bat", ".txt", ".url", ".md"}:
                continue
            if should_delete_file(p) or ("scripts" in parts and p.name.startswith("_")):
                try:
                    p.unlink(missing_ok=True)
                    removed_files += 1
                    print("rm", rel)
                except Exception as e:
                    print("skip file", p, e)

    # explicit purge known heavy/dev trees if any remained
    for rel in (
        "clothing-live-clipper/tests",
        "clothing-live-clipper/docs",
        "clothing-live-clipper/.superpowers",
        "clothing-live-clipper/scripts/__pycache__",
    ):
        p = PKG / rel
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed_dirs += 1
            print("rmdir", rel)

    # remove developer tools under scripts but keep none for users
    scripts = PKG / "clothing-live-clipper" / "scripts"
    if scripts.exists():
        for p in scripts.iterdir():
            if p.is_file() and (p.name.startswith("_") or p.name in JUNK_EXACT or p.suffix in {".ps1", ".bat"}):
                # end-users don't need dev scripts; pack/portable has installers
                try:
                    p.unlink()
                    removed_files += 1
                    print("rm scripts/", p.name)
                except Exception as e:
                    print("skip", p, e)

    print("DONE removed_files=", removed_files, "removed_dirs=", removed_dirs)
    print("PKG=", PKG)
    # print top-level still
    for p in sorted(PKG.iterdir()):
        print(" -", p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
