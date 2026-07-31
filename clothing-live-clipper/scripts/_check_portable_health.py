"""Sanity-check portable package + script sources for beginner feasibility."""
from __future__ import annotations

import ast
import compileall
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
PKG = DESKTOP / "小面CapCut-便携版"
ZIP = DESKTOP / "小面CapCut-便携版.zip"

errors: list[str] = []
warns: list[str] = []


def ok(msg: str) -> None:
    print("[OK]", msg)


def bad(msg: str) -> None:
    print("[FAIL]", msg)
    errors.append(msg)


def warn(msg: str) -> None:
    print("[WARN]", msg)
    warns.append(msg)


def parse_ps1(path: Path) -> None:
    # Use PowerShell parser via temp script file to avoid quoting hell
    ps = f"""
$e=$null; $t=$null
[void][System.Management.Automation.Language.Parser]::ParseFile('{path.as_posix()}', [ref]$t, [ref]$e)
if($e) {{ $e | ForEach-Object {{ $_.ToString() }}; exit 1 }}
exit 0
"""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        bad(f"PowerShell parse fail {path.name}: {r.stdout or r.stderr}")
    else:
        ok(f"PowerShell parse {path.name}")


def main() -> int:
    print("=== 1) unit tests already run externally; compile python package ===")
    # syntax compile main sources
    for p in (ROOT / "src" / "clipper").rglob("*.py"):
        try:
            src = p.read_text(encoding="utf-8")
            ast.parse(src, filename=str(p))
        except SyntaxError as e:
            bad(f"SyntaxError {p}: {e}")
    if not errors:
        ok("clipper package AST parse clean")

    print("=== 2) portable scripts present & parse ===")
    portable = ROOT / "pack" / "portable"
    required_scripts = [
        "install_all.ps1",
        "start_service.ps1",
        "stop_service.ps1",
        "ensure_ready.ps1",
        "启动小面.bat",
        "首次安装配置.bat",
        "停止小面.bat",
        "打开网页.bat",
        "使用说明-操作指南.txt",
        "注意事项.txt",
    ]
    for name in required_scripts:
        p = portable / name
        if not p.exists():
            bad(f"missing script {p}")
        else:
            ok(f"exists {name}")
            if p.suffix.lower() == ".ps1":
                parse_ps1(p)

    # ensure key functions exist in install/start
    inst = (portable / "install_all.ps1").read_text(encoding="utf-8", errors="replace")
    for key in (
        "Get-PythonCommand",
        "Ensure-Venv",
        "Ensure-Deps",
        "Ensure-Ffmpeg",
        "Ensure-Model",
        "Install-PipPackages",
        "Download-File",
        "HardFail",
        "SoftFail",
    ):
        if key not in inst:
            bad(f"install_all.ps1 missing {key}")
        else:
            ok(f"install has {key}")
    st = (portable / "start_service.ps1").read_text(encoding="utf-8", errors="replace")
    for key in ("Free-Port8787", "Start-Uvicorn", "Wait-Ready", "Read-DeviceHint"):
        if key not in st:
            bad(f"start_service.ps1 missing {key}")
        else:
            ok(f"start has {key}")
    if "fallback CPU" in st or "useCpu" in st or "device = \"cpu\"" in st or "device=cpu" in st:
        ok("start has CPU fallback")
    else:
        bad("start_service.ps1 missing CPU fallback")

    start_bat = (portable / "启动小面.bat").read_text(encoding="utf-8", errors="replace")
    if "ensure_ready.ps1" not in start_bat:
        bad("启动小面.bat does not call ensure_ready")
    else:
        ok("启动小面 auto-installs via ensure_ready")
    if "start_service.ps1" not in start_bat:
        bad("启动小面.bat does not call start_service")
    else:
        ok("启动小面 starts service")

    print("=== 3) built package on Desktop ===")
    if not PKG.exists():
        bad(f"package folder missing: {PKG}")
    else:
        ok(f"package folder: {PKG}")
        root_need = [
            "启动小面.bat",
            "停止小面.bat",
            "打开网页.bat",
            "首次安装配置.bat",
            "先读我.txt",
            "使用说明-操作指南.txt",
            "注意事项.txt",
            "clothing-live-clipper",
            "pack",
            "models",
            "tools",
        ]
        for n in root_need:
            p = PKG / n
            if not p.exists():
                bad(f"package missing {n}")
            else:
                ok(f"package has {n}")

        # critical nested
        checks = [
            PKG / "pack" / "portable" / "install_all.ps1",
            PKG / "pack" / "portable" / "ensure_ready.ps1",
            PKG / "pack" / "portable" / "start_service.ps1",
            PKG / "clothing-live-clipper" / "src" / "clipper" / "web.py",
            PKG / "clothing-live-clipper" / "src" / "clipper" / "job_worker.py",
            PKG / "clothing-live-clipper" / "scripts" / "agent_clip_video.py",
            PKG / "clothing-live-clipper" / "scripts" / "filter_transcript_v2.py",
            PKG / "clothing-live-clipper" / "scripts" / "asr_enhance.py",
            PKG / "clothing-live-clipper" / "requirements.txt",
            PKG / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        ]
        for p in checks:
            if not p.exists():
                bad(f"package nested missing {p.relative_to(PKG)}")
            else:
                ok(f"nested ok {p.relative_to(PKG)}")

        # model presence (small or tiny)
        model_ok = False
        for n in ("whisper-medium", "whisper-small", "whisper-tiny"):
            b = PKG / "models" / n / "model.bin"
            if b.exists() and b.stat().st_size > 1_000_000:
                ok(f"model present {n} size_mb={round(b.stat().st_size/1024/1024,1)}")
                model_ok = True
        if not model_ok:
            warn("no large model.bin in package; first run must download (needs network)")

        # root bat should auto ensure
        rb = (PKG / "启动小面.bat").read_text(encoding="utf-8", errors="replace")
        if "ensure_ready.ps1" not in rb:
            bad("root 启动小面.bat missing ensure_ready")
        else:
            ok("root 启动小面.bat auto-ensure")

    if ZIP.exists():
        ok(f"zip exists {ZIP.name} size_mb={round(ZIP.stat().st_size/1024/1024,1)}")
    else:
        warn(f"zip missing: {ZIP}")

    print("=== 4) import clipper with package layout simulation ===")
    # simulate: PYTHONPATH=package/clothing-live-clipper/src
    if PKG.exists():
        src = PKG / "clothing-live-clipper" / "src"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(src)
        r = subprocess.run(
            [sys.executable, "-c", "from clipper.web import app; from clipper import media, llm_plan; print('APP_OK', app.title if hasattr(app,'title') else type(app))"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(PKG / "clothing-live-clipper"),
        )
        if r.returncode != 0 or "APP_OK" not in (r.stdout + r.stderr):
            bad(f"import from package failed: {r.stdout}\n{r.stderr}")
        else:
            ok(f"package import ok: {r.stdout.strip()}")

        # render profile / size helpers
        r2 = subprocess.run(
            [
                sys.executable,
                "-c",
                "from clipper.media import get_render_profile; p=get_render_profile('final'); print(p.force_height, p.max_edge, p.fps, p.video_bitrate)",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(PKG / "clothing-live-clipper"),
        )
        if r2.returncode != 0:
            bad(f"get_render_profile fail: {r2.stderr}")
        else:
            ok(f"final profile {r2.stdout.strip()}")

        r3 = subprocess.run(
            [
                sys.executable,
                "-c",
                "from clipper.llm_plan import _is_size,_is_onbody_effect,_is_price_or_shipping; "
                "assert _is_size('建议穿M码'); assert not _is_size('不显肚子'); "
                "assert _is_onbody_effect('不走光'); assert _is_price_or_shipping('加一单'); print('POLICY_OK')",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if r3.returncode != 0 or "POLICY_OK" not in r3.stdout:
            bad(f"policy checks fail: {r3.stdout} {r3.stderr}")
        else:
            ok("policy markers ok (size/onbody/deal)")

    print("=== 5) build script compile ===")
    build = ROOT / "scripts" / "build_portable_package.py"
    try:
        ast.parse(build.read_text(encoding="utf-8"))
        ok("build_portable_package.py syntax ok")
    except Exception as e:
        bad(str(e))

    print("\n======== SUMMARY ========")
    print("errors", len(errors), "warnings", len(warns))
    for e in errors:
        print(" -", e)
    for w in warns:
        print(" ~", w)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
