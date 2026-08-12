# -*- coding: utf-8 -*-
"""
Build launcher EXE only and put it in a NEW Desktop folder.
Does NOT modify 小面CapCut-便携版.zip or overwrite the full portable folder.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "xiaomian_launcher.py"
DESK = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
OUT_DIR = DESK / "小面CapCut-EXE启动版"
WORK = Path(os.environ.get("TEMP", str(Path.home()))) / "xiaomian_launcher_build"
SRC_PORTABLE = DESK / "小面CapCut-便携版"


def main() -> int:
    print("ROOT", ROOT)
    print("OUT ", OUT_DIR)
    if not LAUNCHER.exists():
        raise SystemExit(f"missing {LAUNCHER}")

    print("install pyinstaller...")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "pyinstaller"],
        check=False,
    )
    if r.returncode != 0:
        raise SystemExit("pip install pyinstaller failed")

    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    dist = WORK / "dist"
    build = WORK / "build"
    WORK.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        "XiaomianCapCut",
        "--distpath",
        str(dist),
        "--workpath",
        str(build),
        "--specpath",
        str(WORK),
        str(LAUNCHER),
    ]
    print("PyInstaller...")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit("PyInstaller failed")

    built = dist / "XiaomianCapCut.exe"
    if not built.exists():
        raise SystemExit(f"missing output {built}")

    # New folder on Desktop — do not touch existing zip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target_exe = OUT_DIR / "小面CapCut.exe"
    shutil.copy2(built, target_exe)
    print("copied", target_exe, "size_mb", round(target_exe.stat().st_size / 1024 / 1024, 2))

    # stop / open helpers (ASCII-safe bat)
    (OUT_DIR / "stop.bat").write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "\"%~dp0XiaomianCapCut.exe\" --stop 2>nul\r\n"
        "if not exist \"%~dp0XiaomianCapCut.exe\" \"%~dp0小面CapCut.exe\" --stop\r\n"
        "if errorlevel 1 pause\r\n",
        encoding="utf-8",
    )
    # primary stop uses Chinese exe name we just wrote
    (OUT_DIR / "停止服务.bat").write_text(
        "@echo off\r\nchcp 65001 >nul\r\ncd /d \"%~dp0\"\r\n"
        "\"%~dp0小面CapCut.exe\" --stop\r\nif errorlevel 1 pause\r\n",
        encoding="utf-8",
    )
    (OUT_DIR / "打开网页.bat").write_text(
        "@echo off\r\nstart \"\" \"http://127.0.0.1:8787/\"\r\n",
        encoding="utf-8",
    )

    readme = """小面 CapCut · EXE 启动版（独立文件夹，不改动原 zip）

【不会动】
- 桌面上的 小面CapCut-便携版.zip 保持原样
- 不会覆盖「小面CapCut-便携版」完整包内容

【本文件夹有什么】
- 小面CapCut.exe   启动器（ensure + 启动服务 + 开浏览器）
- 停止服务.bat
- 打开网页.bat
- 先读我-EXE.txt（本文件）

【正确用法】
启动器必须和便携包「根目录文件」放在一起，同级应能看到：
  pack\\portable\\
  clothing-live-clipper\\
  models\\
  tools\\

推荐两种方式（二选一）：

方式 A（推荐）
  1. 把「小面CapCut-便携版」文件夹里的全部内容复制到本文件夹
     （与 小面CapCut.exe 同级）
  2. 双击 小面CapCut.exe

方式 B
  1. 把本文件夹里的 小面CapCut.exe 复制进「小面CapCut-便携版」根目录
  2. 在完整包里双击 EXE

【命令】
  小面CapCut.exe              安装修复 + 启动 + 打开网页
  小面CapCut.exe --stop       停止
  小面CapCut.exe --open       只开网页
  小面CapCut.exe --no-browser 启动但不自动开浏览器

【路径建议】
  D:\\xiaomian\\
  不要放在 桌面\\xxx (3)\\ 长中文路径

【体积说明】
  EXE 只有几 MB，模型/ffmpeg/Python 仍在完整便携目录中。
"""
    (OUT_DIR / "先读我-EXE.txt").write_text(readme, encoding="utf-8")

    # Optional: if full portable exists, mirror a JOINED folder for one-click use
    # WITHOUT modifying original portable folder or zip.
    joined = DESK / "小面CapCut-EXE完整可运行"
    if SRC_PORTABLE.exists() and (SRC_PORTABLE / "pack" / "portable").exists():
        print("Assembling joined runnable folder (copy) ->", joined)
        if joined.exists():
            # remove old joined only (not the original portable / zip)
            shutil.rmtree(joined, ignore_errors=True)
        # copytree excluding huge unnecessary if any; keep full package
        def ignore(dirpath, names):
            drop = set()
            for n in names:
                if n in {".venv", "__pycache__", "web_jobs"}:
                    drop.add(n)
                if n.endswith((".pyc", ".log")):
                    drop.add(n)
            return drop

        shutil.copytree(SRC_PORTABLE, joined, ignore=ignore)
        shutil.copy2(target_exe, joined / "小面CapCut.exe")
        shutil.copy2(OUT_DIR / "停止服务.bat", joined / "停止服务.bat")
        shutil.copy2(OUT_DIR / "先读我-EXE.txt", joined / "先读我-EXE.txt")
        print("joined ready:", joined)
    else:
        print("SKIP joined: full portable folder not found at", SRC_PORTABLE)

    print("DONE")
    print("launcher folder:", OUT_DIR)
    for p in sorted(OUT_DIR.iterdir()):
        print(" -", p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
