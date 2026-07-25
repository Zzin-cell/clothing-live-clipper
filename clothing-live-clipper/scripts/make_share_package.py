"""Build a portable share package on Desktop\\capcut for other users."""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

SRC = Path(r"C:\Users\MR\AppData\grok\clothing-live-clipper")
MODELS = Path(r"C:\Users\MR\AppData\grok\models")
FFMPEG = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
DESKTOP = Path(r"C:\Users\MR\Desktop")
OUT = DESKTOP / "capcut"
ZIP_PATH = DESKTOP / "capcut-share.zip"

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
}
SKIP_FILE_SUFFIX = {".pyc", ".pyo", ".log", ".tmp"}


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        root_p = Path(root)
        # prune dirs
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        rel = root_p.relative_to(src)
        target_root = dst / rel
        target_root.mkdir(parents=True, exist_ok=True)
        for f in files:
            if Path(f).suffix.lower() in SKIP_FILE_SUFFIX:
                continue
            if f.endswith(".part"):
                continue
            sp = root_p / f
            tp = target_root / f
            try:
                shutil.copy2(sp, tp)
            except Exception as e:
                print("skip file", sp, e)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_url_shortcut(path: Path, url: str) -> None:
    content = "\n".join(
        [
            "[{000214A0-0000-0000-C000-000000000046}]",
            "Prop3=19,11",
            "[InternetShortcut]",
            f"URL={url}",
            "IconIndex=0",
            "",
        ]
    )
    write_text(path, content)


def make_bat_shortcut(path: Path, lines: list[str]) -> None:
    write_text(path, "\r\n".join(lines) + "\r\n")


def zip_dir(src: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
        for root, dirs, files in os.walk(src):
            # keep package small-ish: skip nested huge caches if any
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
            for f in files:
                fp = Path(root) / f
                # skip the zip itself if inside
                if fp.resolve() == zip_path.resolve():
                    continue
                arc = fp.relative_to(src.parent if src.name == "capcut" else src)
                # store as capcut/...
                if src.name == "capcut":
                    arcname = Path("capcut") / fp.relative_to(src)
                else:
                    arcname = arc
                zf.write(fp, arcname.as_posix())


def main() -> int:
    print("OUT", OUT)
    if OUT.exists():
        print("cleaning old package…")
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) app code
    app_dst = OUT / "clothing-live-clipper"
    print("copy app…")
    copy_tree(SRC, app_dst)

    # ensure empty output dirs exist for first run
    (app_dst / "output").mkdir(exist_ok=True)
    (app_dst / "output" / "web_jobs").mkdir(exist_ok=True)
    (app_dst / "output" / "learning").mkdir(exist_ok=True)

    # 2) models (needed for offline ASR)
    models_dst = OUT / "models"
    models_dst.mkdir(exist_ok=True)
    for name in ("whisper-medium", "whisper-small", "whisper-tiny"):
        s = MODELS / name
        if s.exists() and (s / "model.bin").exists():
            print("copy model", name)
            shutil.copytree(s, models_dst / name, dirs_exist_ok=True)

    # 3) portable ffmpeg tools
    tools = OUT / "tools" / "ffmpeg" / "bin"
    tools.mkdir(parents=True, exist_ok=True)
    if FFMPEG.exists():
        for exe in ("ffmpeg.exe", "ffprobe.exe"):
            src_exe = FFMPEG / exe
            if src_exe.exists():
                print("copy", exe)
                shutil.copy2(src_exe, tools / exe)
    else:
        print("WARN: ffmpeg bin not found at", FFMPEG)

    # 4) root launchers / shortcuts
    print("write launchers…")

    make_bat_shortcut(
        OUT / "启动小面.bat",
        [
            "@echo off",
            "cd /d \"%~dp0\"",
            "set \"PATH=%~dp0tools\\ffmpeg\\bin;%PATH%\"",
            "set \"PYTHONPATH=%~dp0clothing-live-clipper\\src\"",
            "set \"CLIPPER_ASR_DEVICE=cuda\"",
            "set \"CLIPPER_ASR_COMPUTE_TYPE=float16\"",
            "set \"CLIPPER_ASR_QUALITY=high\"",
            "set \"CLIPPER_ASR_BEAM_SIZE=5\"",
            "set \"CLIPPER_ASR_BEST_OF=5\"",
            "set \"CLIPPER_ASR_DENOISE=1\"",
            "set \"CLIPPER_PLAYBACK_SPEED=1.4\"",
            "if exist \"%~dp0models\\whisper-medium\\model.bin\" set \"CLIPPER_LOCAL_WHISPER_MODEL=%~dp0models\\whisper-medium\"",
            "if not defined CLIPPER_LOCAL_WHISPER_MODEL if exist \"%~dp0models\\whisper-small\\model.bin\" set \"CLIPPER_LOCAL_WHISPER_MODEL=%~dp0models\\whisper-small\"",
            "echo ============================================",
            "echo  小面 CapCut",
            "echo  http://127.0.0.1:8787/",
            "echo  LLM请在网页右侧自行填写（多用户）",
            "echo ============================================",
            "where ffmpeg >nul 2>&1 && echo ffmpeg: OK || echo ffmpeg: MISSING",
            "where python >nul 2>&1 && echo python: OK || echo python: MISSING",
            "echo.",
            "echo 首次使用请先运行：安装依赖.bat",
            "echo 保持本窗口不要关闭",
            "echo.",
            "start \"\" \"%~dp0打开小面网页.url\"",
            "cd /d \"%~dp0clothing-live-clipper\"",
            "python -m uvicorn clipper.web:app --host 127.0.0.1 --port 8787",
            "pause",
        ],
    )

    make_bat_shortcut(
        OUT / "安装依赖.bat",
        [
            "@echo off",
            "cd /d \"%~dp0\"",
            "echo 安装 Python 依赖（需已安装 Python 3.11+）...",
            "python -m pip install -U pip",
            "python -m pip install -r \"%~dp0clothing-live-clipper\\requirements.txt\"",
            "python -m pip install faster-whisper",
            "python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12",
            "echo.",
            "echo 完成。可双击：启动小面.bat",
            "pause",
        ],
    )

    make_bat_shortcut(
        OUT / "打开小面.bat",
        [
            "@echo off",
            "start \"\" \"http://127.0.0.1:8787/\"",
        ],
    )

    # URL shortcut at package root
    make_url_shortcut(OUT / "打开小面网页.url", "http://127.0.0.1:8787/")

    # also English aliases
    shutil.copy2(OUT / "启动小面.bat", OUT / "Start-Xiaomian.bat")
    shutil.copy2(OUT / "打开小面.bat", OUT / "Open-Web.bat")
    shutil.copy2(OUT / "安装依赖.bat", OUT / "Install-Deps.bat")
    make_url_shortcut(OUT / "Open-Xiaomian.url", "http://127.0.0.1:8787/")

    write_text(
        OUT / "使用说明.txt",
        """
小面 CapCut 分享包
==================

一、环境要求
1) Windows 10/11
2) 已安装 Python 3.11+
3) 建议 NVIDIA 显卡（可用 GPU 听写更快更准；无显卡也可 CPU 跑，更慢）

二、首次使用
1) 双击：安装依赖.bat
2) 双击：启动小面.bat
3) 浏览器打开：http://127.0.0.1:8787/
4) 在右侧「LLM 用户配置」填写你自己的：
   - Base URL（OpenAI兼容，如 https://api.openai.com/v1 或中转/v1）
   - Model（如 grok-4.5 / gpt-4o-mini / deepseek-chat）
   - API Key
   点「测试连通」→「保存并启用」
   （多用户各自填写，不使用共享 env 密钥）

三、日常使用
1) 双击「启动小面.bat」并保持黑窗口不关
2) 双击「打开小面网页.url」
3) 上传直播视频 → 自动听写 → LLM排片（或规则回退）→ 成片
4) 支持多任务并发；听写阶段会排队，LLM/渲染并行
5) 可在「逻辑成片」里改时间、删小句、重排后「保存并重剪成片」

四、目录说明
- clothing-live-clipper/  主程序
- models/                 Whisper 模型（medium/small/tiny）
- tools/ffmpeg/bin/       便携 ffmpeg
- 启动小面.bat            启动服务
- 打开小面网页.url        访问入口（根目录快捷方式）
- 安装依赖.bat            首次装 Python 包
- clothing-live-clipper/output/user_config/llm.json
  你的 LLM 配置（本机私有，勿分享）

五、注意
- LLM 配置由网页填写，不读环境变量密钥（便于推广多用户）
- 改口播文字不会自动改原片声音；改时间/删段/重排后重剪才会改变成片
- 第一次 GPU 听写会稍慢（加载 medium 模型）
""".strip()
        + "\n",
    )

    # 5) zip for sharing
    print("zip…", ZIP_PATH)
    zip_dir(OUT, ZIP_PATH)

    # summary
    def du(path: Path) -> int:
        total = 0
        for r, _, files in os.walk(path):
            for f in files:
                try:
                    total += (Path(r) / f).stat().st_size
                except OSError:
                    pass
        return total

    print("DONE")
    print("folder", OUT, "size_mb", round(du(OUT) / 1024 / 1024, 1))
    print("zip", ZIP_PATH, "size_mb", round(ZIP_PATH.stat().st_size / 1024 / 1024, 1) if ZIP_PATH.exists() else 0)
    print("root files:")
    for p in sorted(OUT.iterdir()):
        print(" -", p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
