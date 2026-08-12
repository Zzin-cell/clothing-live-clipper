"""Virtual-machine style smoke test for the FULL portable package.

Simulates a blank PC that:
  - unzips the 超全量便携版 (models + ffmpeg already present)
  - has system Python available (uses host)
  - has NO pre-created .venv for the package location
  - must install deps, start web, serve UI, and keep LLM config blank-machine friendly

Does NOT delete whisper-medium (that would make the test download ~1.5GB).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DESKTOP = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
SRC_PKG = DESKTOP / "小面CapCut-便携版"
TEST_ROOT = Path(os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp"))) / "XiaomianVmSmoke"
TEST_PKG = TEST_ROOT / "XiaomianCapCutFull"
LOGS: list[str] = []
FAILS: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    LOGS.append(msg)


def ok(msg: str) -> None:
    log("OK: " + msg)


def fail(msg: str) -> None:
    log("FAIL: " + msg)
    FAILS.append(msg)


def section(t: str) -> None:
    log(f"\n==== {t} ====\n")


def kill_8787() -> None:
    ps = (
        "Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue | "
        "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; "
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { "
        "$_.CommandLine -and ($_.CommandLine -like '*uvicorn*clipper.web*' -or $_.CommandLine -like '*XiaomianVmSmoke*') "
        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)


def http_json(method: str, url: str, body: dict | None = None, timeout: float = 30.0) -> tuple[int, dict | list | str]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw) if raw.strip() else {}
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def main() -> int:
    section("0 precheck source full package")
    if not SRC_PKG.exists():
        fail(f"source package missing: {SRC_PKG}")
        return 1
    need = [
        SRC_PKG / "启动小面.bat",
        SRC_PKG / "pack" / "portable" / "install_all.ps1",
        SRC_PKG / "pack" / "portable" / "start_service.ps1",
        SRC_PKG / "clothing-live-clipper" / "src" / "clipper" / "web.py",
        SRC_PKG / "models" / "whisper-medium" / "model.bin",
        SRC_PKG / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    for p in need:
        if not p.exists():
            fail(f"source missing {p.relative_to(SRC_PKG)}")
        else:
            ok(f"source has {p.relative_to(SRC_PKG)}")
    if FAILS:
        return 1

    section("1 recreate clean VM-like package (keep models+ffmpeg, drop venv)")
    kill_8787()
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True)

    def ignore(dirpath, names):
        drop = set()
        for n in names:
            if n in {".venv", "__pycache__", ".git"}:
                drop.add(n)
            if n.endswith((".pyc", ".pyo")):
                drop.add(n)
        # strip logs and user_config secrets; keep models/ffmpeg
        base = Path(dirpath)
        try:
            rel = base.relative_to(SRC_PKG)
        except Exception:
            rel = Path(".")
        parts = rel.parts
        if parts[:2] == ("tools", "logs"):
            return set(names)
        if parts[:2] == ("output", "user_config") or parts[:3] == (
            "clothing-live-clipper",
            "output",
            "user_config",
        ):
            return set(names)
        if parts[:2] == ("output", "web_jobs") or parts[:3] == (
            "clothing-live-clipper",
            "output",
            "web_jobs",
        ):
            return set(names)
        return drop

    log(f"copy {SRC_PKG} -> {TEST_PKG}")
    shutil.copytree(SRC_PKG, TEST_PKG, ignore=ignore)
    # ensure clean install markers / empty user config
    for p in [
        TEST_PKG / ".venv",
        TEST_PKG / "tools" / "install_ok.txt",
        TEST_PKG / "tools" / "uvicorn.pid",
        TEST_PKG / "tools" / "logs",
    ]:
        if p.is_file():
            p.unlink(missing_ok=True)
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    (TEST_PKG / "tools" / "logs").mkdir(parents=True, exist_ok=True)
    for uc in [
        TEST_PKG / "output" / "user_config",
        TEST_PKG / "clothing-live-clipper" / "output" / "user_config",
    ]:
        uc.mkdir(parents=True, exist_ok=True)
        for f in uc.glob("*.json"):
            f.unlink(missing_ok=True)

    if (TEST_PKG / ".venv").exists():
        fail("venv should not exist before install")
    if not (TEST_PKG / "models" / "whisper-medium" / "model.bin").exists():
        fail("medium model missing after copy")
    if not (TEST_PKG / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").exists():
        fail("ffmpeg missing after copy")
    if FAILS:
        return 1
    ok(f"clean VM package at {TEST_PKG}")

    section("2 install deps (venv) with models already present")
    installer = TEST_PKG / "pack" / "portable" / "install_all.ps1"
    t0 = time.time()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)],
        cwd=str(TEST_PKG),
    )
    dt = int(time.time() - t0)
    log(f"install exit={r.returncode} elapsed_sec={dt}")
    if r.returncode != 0:
        for name in ("last_repair.txt",):
            p = TEST_PKG / "tools" / "logs" / name
            if p.exists():
                log("---- " + name + " ----")
                log(p.read_text(encoding="utf-8", errors="replace")[:4000])
        logs = sorted((TEST_PKG / "tools" / "logs").glob("install_*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
        if logs:
            log("---- install log tail ----")
            log("\n".join(logs[0].read_text(encoding="utf-8", errors="replace").splitlines()[-60:]))
        fail("install_all failed")
        return 1
    ok("install_all succeeded")

    py = TEST_PKG / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        fail("missing venv python after install")
        return 1
    imp = subprocess.run(
        [str(py), "-c", "import fastapi,uvicorn,faster_whisper; print('IMPORT_OK')"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if imp.returncode != 0 or "IMPORT_OK" not in (imp.stdout + imp.stderr):
        fail(f"import failed: {imp.stdout}\n{imp.stderr}")
        return 1
    ok("venv imports fastapi/uvicorn/faster_whisper")

    # package import + default llm config
    env = os.environ.copy()
    env["PYTHONPATH"] = str(TEST_PKG / "clothing-live-clipper" / "src")
    rcfg = subprocess.run(
        [
            str(py),
            "-c",
            "from clipper.user_llm import public_user_llm, provider_mismatch_hint; "
            "from clipper.openai_compat import ping, normalize_base_url; "
            "p=public_user_llm(); "
            "print('base', p.get('base_url')); print('model', p.get('model')); "
            "print('has_key', p.get('has_key')); "
            "print('norm', normalize_base_url('')); "
            "print('mismatch', bool(provider_mismatch_hint(base_url='https://api.openai.com/v1', model='Qwen/Qwen2.5-7B-Instruct'))); "
            "out=ping(base_url='https://api.openai.com/v1', api_key='sk-test-xxxxxxxx', model='Qwen/Qwen2.5-7B-Instruct', timeout=10, auto_pick_model=False); "
            "print('ping_class', out.get('error_class'), 'ok', out.get('ok'), 'ms', out.get('latency_ms'))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(TEST_PKG / "clothing-live-clipper"),
    )
    log(rcfg.stdout)
    if rcfg.returncode != 0:
        fail("code-level probe failed: " + rcfg.stderr)
        return 1
    out = rcfg.stdout
    if "siliconflow" not in out.lower():
        fail("default base_url is not siliconflow")
    if "ping_class provider_mismatch" not in out and "ping_class', 'provider_mismatch'" not in out:
        # tolerate plain print format
        if "provider_mismatch" not in out:
            fail("provider mismatch fast-fail not working in package")
        else:
            ok("provider mismatch fast-fail present")
    else:
        ok("provider mismatch fast-fail present")
    if "has_key False" not in out and "has_key False".lower() not in out.lower():
        # print is `has_key False`
        if "has_key True" in out:
            fail("blank package unexpectedly has saved API key")
        else:
            ok("blank package has no key")
    else:
        ok("blank package has no key")

    section("3 start service")
    kill_8787()
    time.sleep(1)
    starter = TEST_PKG / "pack" / "portable" / "start_service.ps1"
    r2 = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(starter)],
        cwd=str(TEST_PKG),
    )
    if r2.returncode != 0:
        for name in ("start_error.txt", "uvicorn.err.log"):
            p = TEST_PKG / "tools" / "logs" / name
            if p.exists():
                log("---- " + name + " ----")
                log("\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]))
        fail("start_service failed")
        return 1
    ok("start_service exit 0")

    section("4 HTTP / API checks")
    ready = False
    body = ""
    for i in range(40):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8787/", timeout=3) as resp:
                body = resp.read().decode("utf-8", "replace")
                log(f"HTTP home {resp.status} len={len(body)}")
                ready = True
                break
        except Exception:
            time.sleep(0.5)
    if not ready:
        fail("web home not ready")
        return 1
    ok("web home OK")
    if "siliconflow.cn" not in body and "硅基" not in body:
        fail("index.html may not show SiliconFlow default hint")
    else:
        ok("UI mentions SiliconFlow / recommended base")

    code, health = http_json("GET", "http://127.0.0.1:8787/api/health", timeout=10)
    log(f"health {code} {health if isinstance(health, dict) else str(health)[:200]}")
    if code != 200 or not isinstance(health, dict) or not health.get("ok"):
        fail("health API failed")
    else:
        ok("health API OK")

    code, cfg = http_json("GET", "http://127.0.0.1:8787/api/system/config", timeout=10)
    log(f"config {code} base={cfg.get('llm_base_url') if isinstance(cfg, dict) else cfg} model={cfg.get('llm_model') if isinstance(cfg, dict) else ''}")
    if code != 200 or not isinstance(cfg, dict):
        fail("config API failed")
    else:
        base = str(cfg.get("llm_base_url") or "")
        model = str(cfg.get("llm_model") or "")
        if "siliconflow" not in base.lower():
            fail(f"config default base is not siliconflow: {base}")
        else:
            ok(f"config default base={base}")
        if "qwen" not in model.lower() and model:
            log("WARN model default not qwen: " + model)
        if cfg.get("has_llm_key"):
            fail("blank machine config reports has_llm_key=True")
        else:
            ok("blank machine has_llm_key=False")

    # provider mismatch via API should fail fast (no 24s hang)
    code, saved = http_json(
        "PUT",
        "http://127.0.0.1:8787/api/system/config",
        {
            "persist": True,
            "llm_enabled": True,
            "llm_plan": True,
            "llm_base_url": "https://api.openai.com/v1",
            "llm_model": "Qwen/Qwen2.5-7B-Instruct",
            "llm_api_key": "sk-vm-smoke-test-key-xxxx",
        },
        timeout=15,
    )
    log(f"save mismatch config status={code}")
    t0 = time.time()
    code, probe = http_json(
        "POST",
        "http://127.0.0.1:8787/api/system/probe",
        {"target": "llm"},
        timeout=20,
    )
    elapsed_ms = int((time.time() - t0) * 1000)
    log(f"probe mismatch status={code} elapsed_ms={elapsed_ms} body={str(probe)[:300]}")
    probe_obj = probe.get("probe") if isinstance(probe, dict) else None
    if not isinstance(probe_obj, dict):
        probe_obj = probe if isinstance(probe, dict) else {}
    if probe_obj.get("ok") is True:
        fail("mismatch probe unexpectedly ok")
    elif probe_obj.get("error_class") != "provider_mismatch" and "OpenAI" not in str(probe_obj.get("error") or ""):
        # still acceptable if frontend/server maps timeout with OpenAI tip, but should be fast
        if elapsed_ms > 5000:
            fail(f"mismatch probe too slow ({elapsed_ms}ms); expected fast fail")
        else:
            ok(f"mismatch probe failed fast ({elapsed_ms}ms) without provider_mismatch class")
    else:
        if elapsed_ms > 3000:
            fail(f"provider_mismatch not fast enough: {elapsed_ms}ms")
        else:
            ok(f"provider_mismatch fast-fail via API ({elapsed_ms}ms)")

    # restore recommended base (no real external dependency asserted if offline)
    http_json(
        "PUT",
        "http://127.0.0.1:8787/api/system/config",
        {
            "persist": True,
            "llm_base_url": "https://api.siliconflow.cn/v1",
            "llm_model": "Qwen/Qwen2.5-7B-Instruct",
            "llm_api_key": "",
            "llm_plan": True,
            "llm_enabled": True,
        },
        timeout=10,
    )

    # jobs list should work empty
    code, jobs = http_json("GET", "http://127.0.0.1:8787/api/jobs?limit=5", timeout=10)
    if code != 200:
        fail(f"jobs API failed {code}")
    else:
        ok("jobs API OK")

    section("5 stop service")
    stopper = TEST_PKG / "pack" / "portable" / "stop_service.ps1"
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(stopper)],
        cwd=str(TEST_PKG),
    )
    time.sleep(1)
    kill_8787()
    ok("service stopped")

    section("SUMMARY")
    if FAILS:
        log(f"VM FULLPACK SMOKE FAILED ({len(FAILS)} fails)")
        for f in FAILS:
            log(" - " + f)
        log(f"Test package kept at: {TEST_PKG}")
        return 1
    log("VM FULLPACK SMOKE PASSED")
    log(f"Test package: {TEST_PKG}")
    log(f"Logs: {TEST_PKG / 'tools' / 'logs'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
