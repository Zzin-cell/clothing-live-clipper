"""Blank-machine simulation for portable package install + start."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DESKTOP = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
TEST_ROOT = Path(os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp"))) / "XiaomianBlankTest"
TEST_PKG = TEST_ROOT / "XiaomianCapCut"


def section(t: str) -> None:
    print(f"\n==== {t} ====\n")


def ok(m: str) -> None:
    print("OK:", m)


def fail(m: str) -> None:
    print("FAIL:", m)
    raise SystemExit(1)


def kill_8787() -> None:
    ps = (
        "Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue | "
        "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
    )


def resolve_src_pkg() -> Path:
    preferred = DESKTOP / "小面CapCut-便携版"
    if preferred.exists():
        return preferred
    cands = [p for p in DESKTOP.iterdir() if p.is_dir() and "CapCut" in p.name]
    if cands:
        return cands[0]
    return preferred


def main() -> int:
    section("0 precheck source package")
    src_pkg = resolve_src_pkg()
    print("SRC_PKG=", src_pkg)
    if not src_pkg.exists():
        fail(f"source package missing: {src_pkg}")
    ok("source package exists")

    section("1 recreate clean blank package")
    kill_8787()
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True)

    # Copy package but exclude ready env bits so install does real work
    def ignore(dirpath, names):
        base = Path(dirpath)
        rel = base.relative_to(src_pkg) if base != src_pkg else Path(".")
        drop = set()
        # always drop venv/pyc
        for n in names:
            if n in {".venv", "__pycache__", ".git"}:
                drop.add(n)
            if n.endswith((".pyc", ".pyo")):
                drop.add(n)
        # drop models content and ffmpeg bin + logs
        parts = rel.parts
        if parts[:1] == ("models",):
            return set(names)
        if parts[:2] == ("tools", "ffmpeg") or parts[:2] == ("tools", "logs"):
            return set(names)
        if parts == ("tools",) and "logs" in names:
            drop.add("logs")
        return drop

    print("copying...")
    shutil.copytree(src_pkg, TEST_PKG, ignore=ignore, dirs_exist_ok=True)
    # force empty models/ffmpeg
    models = TEST_PKG / "models"
    if models.exists():
        shutil.rmtree(models, ignore_errors=True)
    models.mkdir(parents=True, exist_ok=True)
    ffbin = TEST_PKG / "tools" / "ffmpeg" / "bin"
    if ffbin.exists():
        shutil.rmtree(ffbin, ignore_errors=True)
    ffbin.mkdir(parents=True, exist_ok=True)
    logs = TEST_PKG / "tools" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    marker = TEST_PKG / "tools" / "install_ok.txt"
    if marker.exists():
        marker.unlink()
    venv = TEST_PKG / ".venv"
    if venv.exists():
        shutil.rmtree(venv, ignore_errors=True)

    bats = list(TEST_PKG.glob("*.bat"))
    print("root bats:", [b.name for b in bats])
    if not bats:
        fail("copy incomplete: no bat launchers")
    if (TEST_PKG / ".venv").exists():
        fail("venv should not exist")
    if (TEST_PKG / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").exists():
        fail("ffmpeg should not exist")
    if list((TEST_PKG / "models").rglob("model.bin")):
        fail("models should be empty")
    ok(f"clean blank package at {TEST_PKG}")

    section("2 run install_all.ps1")
    installer = TEST_PKG / "pack" / "portable" / "install_all.ps1"
    if not installer.exists():
        fail(f"missing {installer}")
    t0 = time.time()
    r = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
        ],
        cwd=str(TEST_PKG),
    )
    dt = int(time.time() - t0)
    print(f"install exit={r.returncode} elapsed_sec={dt}")
    if r.returncode != 0:
        lr = logs / "last_repair.txt"
        if lr.exists():
            print("---- last_repair.txt ----")
            print(lr.read_text(encoding="utf-8", errors="replace"))
        install_logs = sorted(logs.glob("install_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if install_logs:
            print("---- install log tail ----")
            print("\n".join(install_logs[0].read_text(encoding="utf-8", errors="replace").splitlines()[-50:]))
        fail("install_all failed")
    ok("install_all succeeded")

    section("3 verify artifacts")
    py = TEST_PKG / ".venv" / "Scripts" / "python.exe"
    ff = TEST_PKG / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if not py.exists():
        fail("missing venv python")
    if not ff.exists():
        fail("missing ffmpeg")
    if not marker.exists():
        # installer writes tools/install_ok.txt
        if not (TEST_PKG / "tools" / "install_ok.txt").exists():
            fail("missing install_ok marker")
    model = None
    for n in ("whisper-medium", "whisper-small", "whisper-tiny"):
        b = TEST_PKG / "models" / n / "model.bin"
        if b.exists() and b.stat().st_size > 1_000_000:
            model = b
            break
    if not model:
        fail("missing model.bin after install")
    ok(f"python={py}")
    ok(f"ffmpeg={ff}")
    ok(f"model={model} size_mb={round(model.stat().st_size/1024/1024,1)}")
    imp = subprocess.run(
        [str(py), "-c", "import fastapi,uvicorn,faster_whisper; print('IMPORT_OK')"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if imp.returncode != 0 or "IMPORT_OK" not in (imp.stdout + imp.stderr):
        fail(f"import failed: {imp.stdout}\n{imp.stderr}")
    ok("python imports OK")
    ffv = subprocess.run([str(ff), "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok("ffmpeg: " + (ffv.stdout.splitlines()[0] if ffv.stdout else "unknown"))

    section("4 start service")
    kill_8787()
    time.sleep(1)
    starter = TEST_PKG / "pack" / "portable" / "start_service.ps1"
    r2 = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(starter)],
        cwd=str(TEST_PKG),
    )
    if r2.returncode != 0:
        se = logs / "start_error.txt"
        ue = logs / "uvicorn.err.log"
        if se.exists():
            print("---- start_error ----")
            print(se.read_text(encoding="utf-8", errors="replace"))
        if ue.exists():
            print("---- uvicorn.err tail ----")
            print("\n".join(ue.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]))
        fail("start_service failed")
    ok("start_service exit 0")

    section("5 HTTP readiness")
    ready = False
    body = ""
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8787/", timeout=3) as resp:
                body = resp.read().decode("utf-8", "replace")
                print(f"HTTP {resp.status} body_len={len(body)}")
                ready = True
                break
        except Exception:
            time.sleep(1)
    if not ready:
        ue = logs / "uvicorn.err.log"
        if ue.exists():
            print("\n".join(ue.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]))
        fail("HTTP not ready")
    ok("web home OK")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/api/system/config", timeout=5) as resp:
            c = resp.read()
            print(f"config status={resp.status} len={len(c)}")
            ok("config API reachable")
    except Exception as e:
        print("WARN config API:", e)

    section("6 stop service")
    stopper = TEST_PKG / "pack" / "portable" / "stop_service.ps1"
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(stopper)],
        cwd=str(TEST_PKG),
    )
    time.sleep(1)
    kill_8787()
    ok("service stopped")

    section("SUMMARY")
    print("BLANK INSTALL TEST PASSED")
    print("Test package:", TEST_PKG)
    print("Logs:", TEST_PKG / "tools" / "logs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
