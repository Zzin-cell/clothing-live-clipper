"""
Build offline-ready portable package for beginners:

  Desktop/xiaomian/
    启动小面.bat / 停止小面.bat / 打开网页.bat / 局域网启动.bat / 首次安装配置.bat
    先读我.txt / 使用说明-操作指南.txt / 注意事项.txt / 干净包验收清单.txt
    clothing-live-clipper/   (runtime app code only)
    pack/portable/           (installer scripts)
    models/                  (whisper models when available)
    tools/
      python/                (embed CPython + get-pip)
      ffmpeg/bin/            (ffmpeg [+ffprobe])
      wheels/                (offline pip wheels — no network on first install)
      logs/                  (empty)
    output/                  (empty skeleton only — no secrets / no jobs)

Unzip to short path e.g. D:\\xiaomian → double-click 启动小面.bat
First run creates a CLEAN local .venv from tools/wheels (offline).

Never ships dirty developer .venv / web_jobs / llm.json / learning prefs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
PORTABLE_SCRIPTS = SRC / "pack" / "portable"
CACHE = SRC / "pack" / "cache"
DESKTOP = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
# V3 offline redistributable: Desktop/xiaomian-V3/ + xiaomian-V3.zip
OUT = DESKTOP / "xiaomian-V3"
ZIP_PATH = DESKTOP / "xiaomian-V3.zip"
MODELS_SRC = Path(r"C:\Users\MR\AppData\grok\models")
if not MODELS_SRC.exists():
    MODELS_SRC = SRC.parent / "models"
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
    "tests",
    "docs",
    ".superpowers",
    "pack",
}
SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".tmp", ".part"}
SKIP_FILE_NAMES = {
    ".env",  # may contain secrets on developer machines
    ".DS_Store",
    "Thumbs.db",
}
KEEP_RUNTIME_SCRIPTS = {
    "agent_clip_video.py",
    "filter_transcript_v2.py",
    "asr_enhance.py",
}
KEEP_UNDERSCORE_FILES = {
    "__init__.py",
    "__main__.py",
}
SKIP_SCRIPT_NAMES = {
    "build_portable_package.py",
    "make_share_package.py",
    "migrate_env_llm_to_user.py",
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
    "_boot_web.ps1",
    "_blank_install_test.py",
    "_check_portable_health.py",
    "_write_ps1_utf8bom.py",
    "_parse_ps1.ps1",
    "_run_parse_ps1.py",
    "_clean_package_dir.py",
}

# Core runtime packages for offline wheelhouse (no pytest/dev tools)
CORE_WHEEL_PACKAGES = [
    "pip",
    "setuptools",
    "wheel",
    "virtualenv",
    "pydantic>=2.0",
    "python-dotenv>=1.0",
    "httpx>=0.27",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "python-multipart>=0.0.9",
    "faster-whisper>=1.0",
    "ctranslate2",
    "av",
    "tokenizers",
    "huggingface-hub",
    "numpy",
    "tqdm",
    "onnxruntime",
]
# Optional GPU wheels (large). Include if download succeeds.
OPTIONAL_CUDA_PACKAGES = [
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cuda-nvrtc-cu12",
]

PS_EXE = r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"


def du(p: Path) -> int:
    t = 0
    if not p.exists():
        return 0
    for r, _, files in os.walk(p):
        for f in files:
            try:
                t += (Path(r) / f).stat().st_size
            except OSError:
                pass
    return t


def copy_app(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        root_p = Path(root)
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        rel = root_p.relative_to(src)
        if rel.parts[:1] == ("pack",):
            dirs[:] = []
            continue
        target_root = dst / rel
        target_root.mkdir(parents=True, exist_ok=True)
        for f in files:
            if f in SKIP_FILE_NAMES:
                continue
            if Path(f).suffix.lower() in SKIP_SUFFIX:
                continue
            if f in KEEP_RUNTIME_SCRIPTS:
                try:
                    shutil.copy2(root_p / f, target_root / f)
                except Exception as e:
                    print("skip", root_p / f, e)
                continue
            if f.startswith("_") and f not in KEEP_UNDERSCORE_FILES:
                continue
            if f in SKIP_SCRIPT_NAMES:
                continue
            if f.startswith("test_") and Path(f).suffix == ".py":
                continue
            if rel.parts[:1] == ("scripts",):
                if f not in KEEP_RUNTIME_SCRIPTS and Path(f).suffix == ".py":
                    continue
            try:
                shutil.copy2(root_p / f, target_root / f)
            except Exception as e:
                print("skip", root_p / f, e)

    # Never leave developer .env; only example if present
    env_example = src / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, dst / ".env.example")
    env_path = dst / ".env"
    if env_path.exists():
        env_path.unlink()


def write_root_bats(out: Path) -> None:
    mapping = {
        "首次安装配置.bat": rf"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0pack\portable\首次安装配置.bat"
""",
        "启动小面.bat": rf"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
title xiaomian
echo xiaomian - first run installs offline deps into local .venv (no network needed if wheels present)
echo recommended path: D:\xiaomian
{PS_EXE} -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack\portable\ensure_ready.ps1"
if errorlevel 1 (
  echo auto install failed. see tools\logs\
  pause
  exit /b 1
)
{PS_EXE} -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack\portable\start_service.ps1"
if errorlevel 1 (
  echo start failed. see tools\logs\
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
        "局域网启动.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0pack\portable\局域网启动.bat"
""",
    }
    for extra in ("修复GPU.bat", "查看GPU状态.bat"):
        src = PORTABLE_SCRIPTS / extra
        if src.exists():
            mapping[extra] = (
                f'@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n'
                f'call "%~dp0pack\\portable\\{extra}"\r\n'
            )

    for name, content in mapping.items():
        (out / name).write_text(content.replace("\n", "\r\n"), encoding="utf-8")

    for name in (
        "使用说明-操作指南.txt",
        "注意事项.txt",
        "先读我.txt",
    ):
        src = PORTABLE_SCRIPTS / name
        if src.exists():
            shutil.copy2(src, out / name)

    check = SRC / "pack" / "干净包验收清单.txt"
    if check.exists():
        shutil.copy2(check, out / "干净包验收清单.txt")


def copy_models(out: Path) -> list[str]:
    copied: list[str] = []
    models_out = out / "models"
    models_out.mkdir(parents=True, exist_ok=True)
    # Prefer small+tiny for offline speed; include medium if present
    for name in ("whisper-medium", "whisper-small", "whisper-tiny"):
        s = MODELS_SRC / name
        bin_path = s / "model.bin"
        if s.exists() and bin_path.exists() and bin_path.stat().st_size > 1_000_000:
            print(f"copy model {name} ({round(bin_path.stat().st_size/1024/1024,1)} MB)...")
            dest = models_out / name
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(s, dest)
            copied.append(name)
        else:
            print(f"WARN: model missing on builder: {s}")
    return copied


def copy_ffmpeg(out: Path) -> bool:
    dest_bin = out / "tools" / "ffmpeg" / "bin"
    dest_bin.mkdir(parents=True, exist_ok=True)
    ok = False
    if FFMPEG_SRC.exists():
        for exe in ("ffmpeg.exe", "ffprobe.exe"):
            s = FFMPEG_SRC / exe
            if s.exists():
                print("copy ffmpeg from LOCALAPPDATA:", exe)
                shutil.copy2(s, dest_bin / exe)
                ok = True
    if not ok:
        for cand in (
            SRC / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
            Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
            DESKTOP / "xiaomian-V3" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
    DESKTOP / "xiaomian" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
            DESKTOP / "小面CapCut-便携版" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        ):
            if cand.exists():
                print("copy ffmpeg from", cand)
                shutil.copy2(cand, dest_bin / "ffmpeg.exe")
                probe = cand.with_name("ffprobe.exe")
                if probe.exists():
                    shutil.copy2(probe, dest_bin / "ffprobe.exe")
                ok = True
                break
    if not ok:
        print("WARN: ffmpeg.exe not found on builder")
    return ok


def copy_embedded_python(out: Path) -> bool:
    tools_py = out / "tools" / "python"
    tools_py.mkdir(parents=True, exist_ok=True)

    zip_name = "python-3.12.10-embed-amd64.zip"
    cache_zip = CACHE / zip_name
    get_pip = CACHE / "get-pip.py"

    if not cache_zip.exists():
        print("WARN: missing pack/cache/", zip_name)
        return False

    extract_dir = out / "tools" / "_py_extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    print("extract embed Python...", cache_zip)
    shutil.unpack_archive(str(cache_zip), str(extract_dir))

    src_root = extract_dir
    kids = [p for p in extract_dir.iterdir()]
    if len(kids) == 1 and kids[0].is_dir():
        src_root = kids[0]

    for item in src_root.iterdir():
        dest = tools_py / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    shutil.rmtree(extract_dir, ignore_errors=True)

    for pth in tools_py.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8", errors="replace")
        lines = []
        seen_import = False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#import site"):
                lines.append("import site")
                seen_import = True
            elif s == "import site":
                lines.append("import site")
                seen_import = True
            else:
                lines.append(line)
        if not seen_import:
            lines.append("import site")
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("patched embed pth:", pth.name)

    if get_pip.exists():
        shutil.copy2(get_pip, tools_py / "get-pip.py")
        print("copy get-pip.py")
    else:
        print("WARN: pack/cache/get-pip.py missing")

    py_exe = tools_py / "python.exe"
    if not py_exe.exists():
        print("ERROR: tools/python/python.exe missing after extract")
        return False
    print("bundled python OK:", py_exe)
    return True


def bootstrap_get_pip(py_exe: Path, tools_py: Path) -> bool:
    get_pip = tools_py / "get-pip.py"
    if not get_pip.exists() and (CACHE / "get-pip.py").exists():
        shutil.copy2(CACHE / "get-pip.py", get_pip)
    if not get_pip.exists():
        print("WARN: get-pip.py missing")
        return False
    r = subprocess.run(
        [str(py_exe), str(get_pip), "--no-warn-script-location", "--disable-pip-version-check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        print("get-pip failed:", (r.stderr or r.stdout or "")[-800:])
        return False
    print("get-pip OK into bundled python")
    return True


def install_runtime_into_bundled_python(out: Path) -> bool:
    """
    Pre-install all runtime deps into tools/python site-packages on the builder.
    Blank PCs can then import fastapi/faster_whisper without creating .venv / network.
    This is the key reason '小面CapCut.zip' works better than wheel-only packages.
    """
    tools_py = out / "tools" / "python"
    py_exe = tools_py / "python.exe"
    wheels = out / "tools" / "wheels"
    if not py_exe.exists():
        return False
    if not bootstrap_get_pip(py_exe, tools_py):
        return False

    pkgs = [
        "pip",
        "setuptools",
        "wheel",
        "pydantic>=2.0",
        "python-dotenv>=1.0",
        "httpx>=0.27",
        "fastapi>=0.110",
        "uvicorn[standard]>=0.27",
        "python-multipart>=0.0.9",
        "faster-whisper>=1.0",
        "ctranslate2",
    ]
    cuda_pkgs = [
        "nvidia-cublas-cu12",
        "nvidia-cudnn-cu12",
        "nvidia-cuda-runtime-cu12",
        "nvidia-cuda-nvrtc-cu12",
    ]

    def pip_install(args: list[str]) -> bool:
        cmd = [str(py_exe), "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location"] + args
        print("bundled-pip:", " ".join(args[:8]), "...")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print((r.stderr or r.stdout or "")[-1500:])
            return False
        return True

    # Prefer offline wheels first
    ok = False
    if wheels.exists() and any(wheels.glob("*.whl")):
        ok = pip_install(["--no-index", "--find-links", str(wheels)] + pkgs)
        if not ok:
            print("offline install into bundled python failed; try online fallback")
    if not ok:
        # online fallback on builder only
        ok = pip_install(
            [
                "-i",
                "https://mirrors.aliyun.com/pypi/simple/",
                "--trusted-host",
                "mirrors.aliyun.com",
            ]
            + pkgs
        )
    if not ok:
        print("ERROR: failed to preinstall runtime into bundled python")
        return False

    # CUDA optional
    if wheels.exists():
        pip_install(["--no-index", "--find-links", str(wheels)] + cuda_pkgs)
    else:
        pip_install(
            [
                "-i",
                "https://mirrors.aliyun.com/pypi/simple/",
                "--trusted-host",
                "mirrors.aliyun.com",
            ]
            + cuda_pkgs
        )

    probe = subprocess.run(
        [
            str(py_exe),
            "-c",
            "import fastapi,uvicorn,faster_whisper; print('BUNDLE_IMPORT_OK')",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(probe.stdout)
    if probe.returncode != 0 or "BUNDLE_IMPORT_OK" not in (probe.stdout or ""):
        print("ERROR: bundled import check failed")
        print(probe.stderr[-1000:] if probe.stderr else "")
        return False
    print("bundled runtime deps ready (no blank-PC pip needed if VC++ present)")
    return True


def copy_vc_redist(out: Path) -> bool:
    """Ship VC++ x64 redistributable so blank PCs don't need manual browser download."""
    dest = out / "tools" / "vc_redist.x64.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    candidates = [
        CACHE / "vc_redist.x64.exe",
        SRC / "pack" / "cache" / "vc_redist.x64.exe",
        Path(r"C:\Users\MR\Downloads\vc_redist.x64.exe"),
        DESKTOP / "vc_redist.x64.exe",
        out / "tools" / "vc_redist.x64.exe",
    ]
    for c in candidates:
        if c.exists() and c.stat().st_size > 1_000_000:
            if c.resolve() != dest.resolve():
                shutil.copy2(c, dest)
            print("bundled VC++ redist:", dest, "MB", round(dest.stat().st_size / 1024 / 1024, 1))
            return True

    # Download once on builder (saved into package tools/ and pack/cache for reuse)
    urls = [
        "https://aka.ms/vs/17/release/vc_redist.x64.exe",
        "https://aka.ms/vs/16/release/vc_redist.x64.exe",
    ]
    import urllib.request

    for url in urls:
        try:
            print("download VC++ redist:", url)
            tmp = dest.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)
            if tmp.exists() and tmp.stat().st_size > 1_000_000:
                tmp.replace(dest)
                CACHE.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, CACHE / "vc_redist.x64.exe")
                print(
                    "bundled VC++ redist OK MB",
                    round(dest.stat().st_size / 1024 / 1024, 1),
                )
                return True
        except Exception as e:
            print("WARN: vc_redist download failed:", e)
    print("WARN: vc_redist.x64.exe not bundled — blank PC may need manual VC++ install")
    return False


def build_offline_wheels(out: Path) -> int:
    """Download pure offline wheelhouse into tools/wheels (builder needs network once)."""
    wheels = out / "tools" / "wheels"
    if wheels.exists():
        shutil.rmtree(wheels, ignore_errors=True)
    wheels.mkdir(parents=True, exist_ok=True)

    py = sys.executable
    print("building offline wheelhouse with", py)
    # Prefer Windows amd64 binary wheels
    cmd = [
        py,
        "-m",
        "pip",
        "download",
        "-d",
        str(wheels),
        "--only-binary=:all:",
        "--python-version",
        "3.12",
        "--platform",
        "win_amd64",
        "--implementation",
        "cp",
        "--abi",
        "cp312",
    ] + CORE_WHEEL_PACKAGES

    # First try strict binary-only for py3.12 win
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("strict binary download failed, retry relaxed...")
        print((r.stderr or r.stdout or "")[-1500:])
        cmd2 = [py, "-m", "pip", "download", "-d", str(wheels)] + CORE_WHEEL_PACKAGES
        r2 = subprocess.run(cmd2, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r2.returncode != 0:
            print("CORE wheel download FAILED")
            print((r2.stderr or r2.stdout or "")[-2000:])
        else:
            print("core wheels ok (relaxed)")
    else:
        print("core wheels ok (binary-only)")

    # optional CUDA — best effort, non-fatal
    print("optional CUDA wheels (best effort)...")
    cmd_cuda = [py, "-m", "pip", "download", "-d", str(wheels)] + OPTIONAL_CUDA_PACKAGES
    r3 = subprocess.run(cmd_cuda, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r3.returncode != 0:
        print("WARN: CUDA wheels incomplete — CPU ASR still works offline")
    else:
        print("CUDA wheels included")

    n = len(list(wheels.glob("*.whl"))) + len(list(wheels.glob("*.tar.gz")))
    print("wheel files:", n, "size_mb", round(du(wheels) / 1024 / 1024, 1))
    return n


def write_full_readme(
    out: Path,
    *,
    models: list[str],
    has_ffmpeg: bool,
    has_py: bool,
    wheels: int,
    has_vc: bool,
) -> None:
    model_line = "、".join(models) if models else "（打包时无本地模型）"
    ff_line = "已预置 tools\\ffmpeg" if has_ffmpeg else "未预置 ffmpeg"
    py_line = (
        "已预置 tools\\python（空白机通常无需系统 Python）"
        if has_py
        else "未预置便携 Python"
    )
    wheel_line = (
        f"已预置 tools\\wheels（{wheels} 个包，首次安装可离线装依赖）"
        if wheels > 0
        else "未预置 wheels：首次安装需要联网"
    )
    vc_line = (
        "已预置 tools\\vc_redist.x64.exe（首次自动静默安装，解决 ctranslate2.dll）"
        if has_vc
        else "未预置 VC++"
    )
    text = f"""【xiaomian-V3 离线绿色包 · 开箱即用】

预置听写模型：{model_line}
预置工具：{ff_line}
预置 Python：{py_line}
预置依赖：{wheel_line}
预置运行库：依赖已预装进 tools\\python（空白机可直接 import，不必先联网 pip）
预置 VC++：{vc_line}
LLM：不预置 Key；网页右侧自己填写

本包剔除：开发 .venv / web_jobs / 学习缓存 / .env / llm.json 隐私配置 / tests/docs

====================
推荐路径
====================
  D:\\xiaomian
  C:\\xiaomian
  D:\\xiaomian-V3

====================
小白使用
====================
1. 解压 xiaomian-V3.zip 后进入本文件夹（能直接看到「启动小面.bat」）
2. 双击「启动小面.bat」
   · 优先使用内置 Python + 已预装依赖
   · 若缺 VC++，自动运行 tools\\vc_redist.x64.exe（可能弹 UAC 点「是」）
   · 模型 / ffmpeg 已带，可离线听写渲染
   · 自动打开 http://127.0.0.1:8787/
3. 网页右侧配置 LLM API 后上传视频
4. 不用时双击「停止小面.bat」

注意：
- 不附带历史任务 / 别人的 API Key
- 若装完 VC++ 仍提示 ctranslate2.dll：重启一次再启动
- 重新分发请用开发仓：python scripts\\build_portable_package.py
"""
    (out / "先读我.txt").write_text(text, encoding="utf-8")


def assert_clean(out: Path) -> None:
    bad: list[str] = []
    if (out / "clothing-live-clipper" / "pack").exists():
        bad.append("nested clothing-live-clipper/pack")
    # developer dirty venv under app tree must not ship; root .venv also forbidden
    if (out / ".venv").exists():
        bad.append("root .venv must not ship (fresh install on user PC)")
    if (out / "clothing-live-clipper" / ".venv").exists():
        bad.append("app .venv must not ship")
    if (out / "clothing-live-clipper" / ".env").exists():
        bad.append("must not ship .env secrets")

    jobs = out / "clothing-live-clipper" / "output" / "web_jobs"
    if jobs.exists():
        for k in jobs.iterdir():
            if k.name in {".gitkeep"}:
                continue
            if k.is_dir() and any(k.rglob("*")):
                files = [x for x in k.rglob("*") if x.is_file()]
                if files:
                    bad.append(f"dirty web_jobs: {k.name}")
            if k.is_file() and k.stat().st_size > 1000:
                bad.append(f"dirty web_jobs file: {k.name}")

    for name in KEEP_RUNTIME_SCRIPTS:
        p = out / "clothing-live-clipper" / "scripts" / name
        if not p.exists():
            bad.append(f"missing runtime script {name}")

    clip_pkg = out / "clothing-live-clipper" / "src" / "clipper"
    if not (clip_pkg / "web.py").exists():
        bad.append("missing web.py")
    for dunder in KEEP_UNDERSCORE_FILES:
        if not (clip_pkg / dunder).exists():
            bad.append(f"missing package dunder {dunder}")
    if not (out / "启动小面.bat").exists():
        bad.append("missing root 启动小面.bat")

    for secret in (
        out / "clothing-live-clipper" / "output" / "user_config" / "llm.json",
        out / "output" / "user_config" / "llm.json",
    ):
        if secret.exists() and secret.stat().st_size > 2:
            bad.append(f"must not ship user secret: {secret.relative_to(out)}")

    # learning prefs / events must never ship (privacy + dirty machine state)
    for learn_root in (
        out / "clothing-live-clipper" / "output" / "learning",
        out / "output" / "learning",
    ):
        if learn_root.exists():
            for f in learn_root.rglob("*"):
                if f.is_file() and f.stat().st_size > 0 and f.name != ".gitkeep":
                    bad.append(f"must not ship learning data: {f.relative_to(out)}")
                    break

    # no app/source caches (bundled python site-packages may contain its own __pycache__)
    for p in out.rglob("__pycache__"):
        rel = str(p.relative_to(out)).replace("\\", "/")
        if "tools/python/" in rel or "tools\\python\\" in rel:
            continue
        bad.append(f"cache dir shipped: {p.relative_to(out)}")
        break
    for p in out.rglob("*.pyc"):
        rel = str(p.relative_to(out)).replace("\\", "/")
        if "tools/python/" in rel:
            continue
        bad.append(f"pyc shipped: {p.relative_to(out)}")
        break

    # forbid nested developer folders that bloat redistributable
    for name in ("tests", "docs", ".pytest_cache", ".git"):
        hit = out / "clothing-live-clipper" / name
        if hit.exists():
            bad.append(f"must not ship {name}/ under app tree")

    if bad:
        raise SystemExit("CLEAN_CHECK_FAILED:\n  - " + "\n  - ".join(bad))


def zip_dir(src: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    print("zip (this may take several minutes)...")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
        for root, dirs, files in os.walk(src):
            dirs[:] = [
                d
                for d in dirs
                if d
                not in {
                    "__pycache__",
                    ".git",
                    ".venv",
                    "venv",
                    "web_jobs",
                    ".pytest_cache",
                }
            ]
            for f in files:
                if f.endswith((".pyc", ".pyo", ".log", ".tmp")):
                    continue
                if f in {".env"}:
                    continue
                fp = Path(root) / f
                rel = fp.relative_to(src)
                if "web_jobs" in rel.parts:
                    continue
                if any(part == "__pycache__" for part in rel.parts):
                    continue
                arc = Path(OUT.name) / rel
                zf.write(fp, arc.as_posix())


def main() -> int:
    print("Building OFFLINE portable package ->", OUT)
    print("zip target:", ZIP_PATH)
    print("models src:", MODELS_SRC)
    if not PORTABLE_SCRIPTS.exists():
        raise SystemExit(f"missing {PORTABLE_SCRIPTS}")
    if not CACHE.exists():
        print("WARN: pack/cache missing — embed python may be unavailable")

    if OUT.exists():
        print("removing old package folder...")
        shutil.rmtree(OUT, ignore_errors=True)
    # stop leftover service if user left old Desktop package running
    try:
        stop = DESKTOP / "xiaomian" / "停止小面.bat"
        if stop.exists():
            subprocess.run(
                ["cmd", "/c", str(stop)],
                cwd=str(stop.parent),
                timeout=60,
                capture_output=True,
            )
    except Exception:
        pass
    OUT.mkdir(parents=True)

    app_dst = OUT / "clothing-live-clipper"
    print("copy app code (runtime only, no secrets/tests/docs/web_jobs)...")
    copy_app(SRC, app_dst)

    pack_dst = OUT / "pack" / "portable"
    pack_dst.mkdir(parents=True, exist_ok=True)
    for f in PORTABLE_SCRIPTS.iterdir():
        if f.is_file() and not f.name.startswith("_tmp"):
            shutil.copy2(f, pack_dst / f.name)

    if (CACHE / "get-pip.py").exists():
        cache_out = OUT / "pack" / "cache"
        cache_out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CACHE / "get-pip.py", cache_out / "get-pip.py")

    write_root_bats(OUT)

    # empty skeletons only
    (OUT / "tools" / "logs").mkdir(parents=True, exist_ok=True)
    (OUT / "output" / "web_jobs").mkdir(parents=True, exist_ok=True)
    (OUT / "output" / "user_config").mkdir(parents=True, exist_ok=True)
    (app_dst / "output" / "web_jobs").mkdir(parents=True, exist_ok=True)
    (app_dst / "output" / "user_config").mkdir(parents=True, exist_ok=True)

    has_ff = copy_ffmpeg(OUT)
    models = copy_models(OUT)
    has_py = copy_embedded_python(OUT)
    has_vc = copy_vc_redist(OUT)
    wheel_n = build_offline_wheels(OUT)
    # Critical for blank PCs: install deps into tools/python so first start
    # does not depend on creating venv or network (matches working 小面CapCut.zip idea).
    bundled_ready = False
    if has_py:
        bundled_ready = install_runtime_into_bundled_python(OUT)

    write_full_readme(
        OUT,
        models=models,
        has_ffmpeg=has_ff,
        has_py=has_py,
        wheels=wheel_n,
        has_vc=has_vc,
    )
    # stamp readiness
    stamp = OUT / "tools" / "bundle_status.txt"
    stamp.write_text(
        "\n".join(
            [
                f"bundled_python={has_py}",
                f"bundled_runtime_import_ok={bundled_ready}",
                f"wheels={wheel_n}",
                f"vc_redist={has_vc}",
                f"ffmpeg={has_ff}",
                f"models={','.join(models)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print("clean check...")
    assert_clean(OUT)

    print("zip ->", ZIP_PATH)
    zip_dir(OUT, ZIP_PATH)

    folder_mb = round(du(OUT) / 1024 / 1024, 1)
    zip_mb = round(ZIP_PATH.stat().st_size / 1024 / 1024, 1) if ZIP_PATH.exists() else 0
    print("DONE")
    print("folder MB", folder_mb)
    print("zip MB", zip_mb)
    print("models", models)
    print(
        "ffmpeg",
        has_ff,
        "bundled_python",
        has_py,
        "wheels",
        wheel_n,
        "vc_redist",
        has_vc,
    )
    print("root entries:")
    for p in sorted(OUT.iterdir()):
        print(" -", p.name)

    jobs_bytes = du(OUT / "clothing-live-clipper" / "output" / "web_jobs")
    print("web_jobs bytes (must be 0):", jobs_bytes)
    if jobs_bytes > 0:
        raise SystemExit("web_jobs not empty after build")
    if wheel_n <= 0:
        print("WARN: no offline wheels — first install needs network")
    if not models:
        print("WARN: no models bundled")
    if not has_py:
        print("WARN: no tools/python")
    if not has_vc:
        print("WARN: no tools/vc_redist.x64.exe — blank PC may need manual VC++")
    if has_py and has_ff and models and has_vc and bundled_ready:
        print("OFFLINE_BLANK_READY: yes (preinstalled-bundled-python+ffmpeg+models+vc_redist)")
    elif has_py and has_ff and models and wheel_n > 0 and has_vc:
        print("OFFLINE_BLANK_READY: wheels-only (bundled import not pre-verified)")
    else:
        print("OFFLINE_BLANK_READY: partial — see WARNs above")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
