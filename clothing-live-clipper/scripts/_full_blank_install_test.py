"""
Full-package blank-PC simulation (keeps prebundled python/ffmpeg/models).

Unlike _blank_install_test.py which strips models/ffmpeg to force redownload,
this verifies the deliverable full zip as shipped:
  - no .venv on first run
  - uses tools/python embed + preloaded models + ffmpeg
  - install_all creates venv + pip deps (needs network)
  - start_service + HTTP /api/health + stop
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DESKTOP = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
SRC_PKG = DESKTOP / "小面CapCut-便携版"
TEST_ROOT = Path(os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp"))) / "XiaomianFullBlankTest"
TEST_PKG = TEST_ROOT / "XiaomianCapCut"


def section(t: str) -> None:
    print(f"\n==== {t} ====\n", flush=True)


def ok(m: str) -> None:
    print("OK:", m, flush=True)


def fail(m: str) -> None:
    print("FAIL:", m, flush=True)
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


def main() -> int:
    section("0 precheck full package on Desktop")
    if not SRC_PKG.exists():
        fail(f"missing full package: {SRC_PKG}")
    for need in (
        SRC_PKG / "启动小面.bat",
        SRC_PKG / "tools" / "python" / "python.exe",
        SRC_PKG / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        SRC_PKG / "models" / "whisper-medium" / "model.bin",
        SRC_PKG / "pack" / "portable" / "install_all.ps1",
        SRC_PKG / "clothing-live-clipper" / "scripts" / "agent_clip_video.py",
    ):
        if not need.exists():
            fail(f"incomplete package: {need}")
    ok(f"source={SRC_PKG}")

    section("1 recreate clean copy (keep models/ffmpeg/python, drop venv/jobs)")
    kill_8787()
    if TEST_ROOT.exists():
        print("removing old test root...", flush=True)
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True)

    def ignore(dirpath, names):
        drop = set()
        for n in names:
            if n in {".venv", "venv", "__pycache__", ".git", "web_jobs"}:
                drop.add(n)
            if n.endswith((".pyc", ".pyo", ".log")):
                drop.add(n)
        # drop logs content but keep tree via later mkdir
        base = Path(dirpath)
        try:
            rel = base.relative_to(SRC_PKG)
        except ValueError:
            rel = Path(".")
        if rel.parts[:2] == ("tools", "logs"):
            return set(names)
        if rel.parts[:1] == ("output",) or (
            rel.parts[:2] == ("clothing-live-clipper", "output")
        ):
            # keep empty skeleton only — skip files under output
            return set(names)
        return drop

    print("copying package (large models, may take a few minutes)...", flush=True)
    t_copy = time.time()
    shutil.copytree(SRC_PKG, TEST_PKG, ignore=ignore)
    print(f"copy done in {int(time.time()-t_copy)}s", flush=True)

    # ensure empty output / logs
    for p in (
        TEST_PKG / "tools" / "logs",
        TEST_PKG / "output" / "web_jobs",
        TEST_PKG / "output" / "user_config",
        TEST_PKG / "clothing-live-clipper" / "output" / "web_jobs",
        TEST_PKG / "clothing-live-clipper" / "output" / "user_config",
    ):
        p.mkdir(parents=True, exist_ok=True)

    if (TEST_PKG / ".venv").exists():
        fail("venv should not exist after clean copy")
    if not (TEST_PKG / "tools" / "python" / "python.exe").exists():
        fail("bundled python missing in test copy")
    if not (TEST_PKG / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").exists():
        fail("ffmpeg missing in test copy")
    if not (TEST_PKG / "models" / "whisper-medium" / "model.bin").exists():
        fail("medium model missing in test copy")
    ok(f"clean full package at {TEST_PKG}")

    section("2 run install_all.ps1 (pip deps + health)")
    installer = TEST_PKG / "pack" / "portable" / "install_all.ps1"
    logs = TEST_PKG / "tools" / "logs"
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
    print(f"install exit={r.returncode} elapsed_sec={dt}", flush=True)
    if r.returncode != 0:
        lr = logs / "last_repair.txt"
        if lr.exists():
            print("---- last_repair.txt ----")
            print(lr.read_text(encoding="utf-8", errors="replace"))
        install_logs = sorted(logs.glob("install_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if install_logs:
            print("---- install log tail ----")
            print("\n".join(install_logs[0].read_text(encoding="utf-8", errors="replace").splitlines()[-80:]))
        fail("install_all failed")
    ok(f"install_all succeeded in {dt}s")

    section("3 verify artifacts")
    py = TEST_PKG / ".venv" / "Scripts" / "python.exe"
    ff = TEST_PKG / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    marker = TEST_PKG / "tools" / "install_ok.txt"
    if not py.exists():
        fail("missing venv python")
    if not ff.exists():
        fail("missing ffmpeg")
    if not marker.exists():
        fail("missing install_ok.txt")
    imp = subprocess.run(
        [str(py), "-c", "import fastapi,uvicorn,faster_whisper; print('IMPORT_OK')"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if imp.returncode != 0 or "IMPORT_OK" not in (imp.stdout + imp.stderr):
        fail(f"import failed: {imp.stdout}\n{imp.stderr}")
    ok("venv imports fastapi/uvicorn/faster_whisper")

    # bundled python was used?
    install_logs = sorted(logs.glob("install_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if install_logs:
        text = install_logs[0].read_text(encoding="utf-8", errors="replace")
        if "bundled portable Python" in text or "tools\\python" in text or "tools/python" in text:
            ok("install log shows bundled portable Python")
        else:
            print("WARN: install log did not clearly mark bundled Python (may still be OK)")
        if "INSTALL_OK" in text:
            ok("INSTALL_OK in log")

    section("4 start_service")
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
            print(se.read_text(encoding="utf-8", errors="replace"))
        if ue.exists():
            print("\n".join(ue.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]))
        fail("start_service failed")
    ok("start_service exit 0")

    section("5 HTTP readiness")
    ready = False
    for i in range(45):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8787/", timeout=3) as resp:
                body = resp.read().decode("utf-8", "replace")
                print(f"home HTTP {resp.status} len={len(body)}")
                ready = resp.status == 200
                break
        except Exception as e:
            if i % 5 == 0:
                print(f"  wait... {e}")
            time.sleep(1)
    if not ready:
        ue = logs / "uvicorn.err.log"
        if ue.exists():
            print("\n".join(ue.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]))
        fail("HTTP home not ready")
    ok("web home OK")

    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/api/health", timeout=8) as resp:
            raw = resp.read().decode("utf-8", "replace")
            print(f"health HTTP {resp.status} {raw[:300]}")
            if resp.status != 200:
                fail("health not 200")
            ok("health API OK")
    except Exception as e:
        fail(f"health API failed: {e}")

    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/api/system/config", timeout=8) as resp:
            print(f"config HTTP {resp.status} len={len(resp.read())}")
            ok("config API OK")
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
    print("FULL BLANK INSTALL TEST PASSED")
    print("Test package:", TEST_PKG)
    print("Logs:", logs)
    print("Install seconds:", dt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
