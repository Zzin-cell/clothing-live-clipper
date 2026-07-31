"""
Build a beginner-friendly portable package:

  Desktop/小面CapCut-便携版/
    首次安装配置.bat
    启动小面.bat / 停止小面.bat / 打开网页.bat
    使用说明-操作指南.txt / 注意事项.txt
    clothing-live-clipper/   (app)
    pack/portable/           (installer scripts)
    models/                  (empty or copied if present)
    tools/
    output/

User unzips → runs 首次安装配置.bat (downloads ffmpeg+model, venv) →
desktop shortcut → 启动小面.bat starts hidden uvicorn and opens browser.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC
PORTABLE_SCRIPTS = SRC / "pack" / "portable"
DESKTOP = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
OUT = DESKTOP / "小面CapCut-便携版"
ZIP_PATH = DESKTOP / "小面CapCut-便携版.zip"
MODELS_SRC = Path(r"C:\Users\MR\AppData\grok\models")
FFMPEG_SRC = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"

SKIP_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    "output",
    "web_jobs",
    "agent_jobs",
    "learning_bootstrap",
    ".venv",
    "venv",
    "node_modules",
    "检查文件",
}
SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".tmp", ".part"}


def copy_app(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        root_p = Path(root)
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        rel = root_p.relative_to(src)
        # skip pack build temp heavy
        if "scripts" in rel.parts and any(
            str(rel).endswith(x)
            for x in ()
        ):
            pass
        target_root = dst / rel
        target_root.mkdir(parents=True, exist_ok=True)
        for f in files:
            if Path(f).suffix.lower() in SKIP_SUFFIX:
                continue
            if f.startswith("_commit_msg") or f.startswith("_probe_") or f.startswith("_diag_"):
                continue
            if f.startswith("_check_") or f.startswith("_patch_") or f.startswith("_scan_"):
                continue
            sp = root_p / f
            tp = target_root / f
            try:
                shutil.copy2(sp, tp)
            except Exception as e:
                print("skip", sp, e)


def write_root_bats(out: Path) -> None:
    """Thin wrappers in package root pointing to pack/portable."""
    mapping = {
        "首次安装配置.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0pack\portable\首次安装配置.bat"
""",
        # Root launcher also auto-installs then starts (one click for beginners)
        "启动小面.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小面 CapCut
echo 小面 CapCut - 首次将自动安装配置...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack\portable\ensure_ready.ps1"
if errorlevel 1 (
  echo 自动安装失败，请联网后重试。
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack\portable\start_service.ps1"
if errorlevel 1 (
  echo 启动失败，见 tools\logs\
  pause
  exit /b 1
)
timeout /t 2 >nul
exit /b 0
""",
        "停止小面.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0pack\portable\停止小面.bat"
""",
        "打开网页.bat": r"""@echo off
start "" "http://127.0.0.1:8787/"
""",
    }
    for name, content in mapping.items():
        (out / name).write_text(content.replace("\n", "\r\n"), encoding="utf-8")

    # copy guides to root for visibility
    for name in ("使用说明-操作指南.txt", "注意事项.txt"):
        src = PORTABLE_SCRIPTS / name
        if src.exists():
            shutil.copy2(src, out / name)


def zip_dir(src: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
            for f in files:
                fp = Path(root) / f
                arc = Path(OUT.name) / fp.relative_to(src)
                zf.write(fp, arc.as_posix())


def main() -> int:
    print("Building portable package ->", OUT)
    if not PORTABLE_SCRIPTS.exists():
        raise SystemExit(f"missing {PORTABLE_SCRIPTS}")

    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    # layout:
    # OUT/
    #   clothing-live-clipper/  (full app)
    #   pack/portable/          (installer scripts)  -- install_all expects AppRoot parent of pack
    # Actually install_all.ps1 sets AppRoot = parent of pack/portable = OUT if we nest as OUT/pack/portable
    # and app code as OUT/clothing-live-clipper OR OUT itself with src.
    # Our install scripts assume:
    #   PackRoot = pack/portable
    #   AppRoot = PackRoot/..
    #   python venv at AppRoot/.venv
    #   src at AppRoot/clothing-live-clipper/src OR AppRoot/src
    #   models at AppRoot/models
    #   tools at AppRoot/tools

    app_dst = OUT / "clothing-live-clipper"
    print("copy app code...")
    copy_app(SRC, app_dst)

    # portable scripts
    pack_dst = OUT / "pack" / "portable"
    pack_dst.mkdir(parents=True, exist_ok=True)
    for f in PORTABLE_SCRIPTS.iterdir():
        if f.is_file():
            shutil.copy2(f, pack_dst / f.name)

    # fix 首次安装/启动 bat inside pack/portable: they use %~dp0.. which is AppRoot=OUT
    # already correct if pack/portable is under OUT

    write_root_bats(OUT)

    # empty dirs
    (OUT / "models").mkdir(exist_ok=True)
    (OUT / "tools" / "ffmpeg" / "bin").mkdir(parents=True, exist_ok=True)
    (OUT / "tools" / "logs").mkdir(parents=True, exist_ok=True)
    (OUT / "output" / "web_jobs").mkdir(parents=True, exist_ok=True)
    (OUT / "output" / "user_config").mkdir(parents=True, exist_ok=True)
    (app_dst / "output" / "web_jobs").mkdir(parents=True, exist_ok=True)
    (app_dst / "output" / "user_config").mkdir(parents=True, exist_ok=True)

    # optional: copy local ffmpeg if present (so offline install works partially)
    if FFMPEG_SRC.exists():
        for exe in ("ffmpeg.exe", "ffprobe.exe"):
            s = FFMPEG_SRC / exe
            if s.exists():
                print("copy", exe)
                shutil.copy2(s, OUT / "tools" / "ffmpeg" / "bin" / exe)

    # optional: copy small model if present (medium is huge; only small/tiny by default)
    for name in ("whisper-small", "whisper-tiny"):
        s = MODELS_SRC / name
        if s.exists() and (s / "model.bin").exists():
            print("copy model", name)
            shutil.copytree(s, OUT / "models" / name, dirs_exist_ok=True)

    # README at root
    (OUT / "先读我.txt").write_text(
        "只需双击「启动小面.bat」：\n"
        "  · 第一次会自动安装配置（需联网）\n"
        "  · 然后自动启动服务并打开网页\n"
        "  · 在右侧填写 API 后即可上传切片\n"
        "详细见：使用说明-操作指南.txt\n",
        encoding="utf-8",
    )

    print("zip ->", ZIP_PATH)
    zip_dir(OUT, ZIP_PATH)

    def du(p: Path) -> int:
        t = 0
        for r, _, files in os.walk(p):
            for f in files:
                try:
                    t += (Path(r) / f).stat().st_size
                except OSError:
                    pass
        return t

    print("DONE")
    print("folder MB", round(du(OUT) / 1024 / 1024, 1))
    print("zip MB", round(ZIP_PATH.stat().st_size / 1024 / 1024, 1) if ZIP_PATH.exists() else 0)
    for p in sorted(OUT.iterdir()):
        print(" -", p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
