# -*- coding: utf-8 -*-
"""
Xiaomian CapCut launcher (sidecar EXE).

Default: start backend (if needed) → open app window → close window auto-stops
ONLY if this launcher started the backend.

Usage:
  小面CapCut.exe                 → start/reuse + app window + auto-stop if we started
  小面CapCut.exe --stay          → start/reuse, keep service after close
  小面CapCut.exe --stop          → stop service
  小面CapCut.exe --open          → open UI only
  小面CapCut.exe --no-browser    → start service only
  小面CapCut.exe --force-restart → kill 8787 and cold start
"""
from __future__ import annotations

import argparse
import atexit
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

PORT = 8787
HOME_URL = f"http://127.0.0.1:{PORT}/"
MUTEX_NAME = "Global\\XiaomianCapCutLauncher_SingleInstance"


def _msg(title: str, text: str, error: bool = False) -> None:
    try:
        import ctypes

        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        stream = sys.stderr if error else sys.stdout
        print(f"[{title}] {text}", file=stream)


def acquire_single_instance() -> object | None:
    """Return mutex handle if we own it; None if another launcher is active."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        last = ctypes.get_last_error()
        # ERROR_ALREADY_EXISTS = 183
        if last == 183:
            if handle:
                kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        return object()  # best-effort: allow run if mutex fails


def release_mutex(handle: object | None) -> None:
    if handle is None or handle is object:
        return
    try:
        import ctypes

        ctypes.WinDLL("kernel32").CloseHandle(handle)
    except Exception:
        pass


def app_root_from_exe() -> Path:
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
    else:
        here = Path.cwd().resolve()

    candidates: list[Path] = []
    p = here
    for _ in range(5):
        candidates.append(p)
        p = p.parent

    desk = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    for name in (
        "小面CapCut-EXE启动版",
        "小面CapCut-EXE完整可运行",
        "小面CapCut-便携版",
    ):
        candidates.append(desk / name)

    seen: set[Path] = set()
    for c in candidates:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c in seen:
            continue
        seen.add(c)
        portable = c / "pack" / "portable"
        app = c / "clothing-live-clipper"
        if portable.is_dir() and (portable / "start_service.ps1").is_file():
            if app.is_dir() or (c / "src" / "clipper").is_dir():
                return c
    return here


def run_ps1(script: Path, app_root: Path, timeout: int | None = None) -> int:
    if not script.is_file():
        _msg("小面 CapCut", f"找不到脚本：\n{script}", error=True)
        return 2
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    try:
        r = subprocess.run(cmd, cwd=str(app_root), timeout=timeout)
        return int(r.returncode)
    except subprocess.TimeoutExpired:
        _msg("小面 CapCut", f"脚本超时：{script.name}", error=True)
        return 3
    except Exception as e:
        _msg("小面 CapCut", f"启动脚本失败：{e}", error=True)
        return 4


def http_ok(url: str, timeout: float = 2.0) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_http(url: str, seconds: int = 45) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if http_ok(url, timeout=2.0):
            return True
        time.sleep(0.8)
    return False


def find_browser() -> tuple[str, str] | None:
    env = os.environ
    candidates = [
        ("edge", env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)") + r"\Microsoft\Edge\Application\msedge.exe"),
        ("edge", env.get("PROGRAMFILES", r"C:\Program Files") + r"\Microsoft\Edge\Application\msedge.exe"),
        ("edge", env.get("LOCALAPPDATA", "") + r"\Microsoft\Edge\Application\msedge.exe"),
        ("chrome", env.get("PROGRAMFILES", r"C:\Program Files") + r"\Google\Chrome\Application\chrome.exe"),
        ("chrome", env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)") + r"\Google\Chrome\Application\chrome.exe"),
        ("chrome", env.get("LOCALAPPDATA", "") + r"\Google\Chrome\Application\chrome.exe"),
    ]
    for kind, path in candidates:
        if path and Path(path).is_file():
            return kind, path
    return None


def open_app_window(app_root: Path) -> subprocess.Popen | None:
    found = find_browser()
    if not found:
        return None
    _kind, browser = found
    profile = app_root / "tools" / "app_browser_profile"
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        browser,
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--app={HOME_URL}",
    ]
    try:
        return subprocess.Popen(
            args,
            cwd=str(app_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None


def wait_keeper_window() -> None:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("小面 CapCut 运行中")
        root.geometry("440x170")
        root.resizable(False, False)
        tk.Label(
            root,
            text=(
                "小面服务运行中（http://127.0.0.1:8787）\n\n"
                "请在浏览器中使用小面。\n"
                "关闭本窗口 = 自动停止后台（若本次是由本程序启动的服务）。"
            ),
            justify="left",
            padx=16,
            pady=16,
        ).pack(fill="both", expand=True)
        tk.Button(root, text="停止并退出", command=root.destroy).pack(pady=(0, 12))
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        try:
            webbrowser.open(HOME_URL)
        except Exception:
            pass
        root.mainloop()
    except Exception:
        try:
            webbrowser.open(HOME_URL)
        except Exception:
            pass
        print("小面运行中。按 Enter 或关闭窗口将尝试停止后台…")
        try:
            input()
        except EOFError:
            while True:
                time.sleep(3600)


def cmd_stop(app_root: Path, quiet: bool = False) -> int:
    stop = app_root / "pack" / "portable" / "stop_service.ps1"
    code = run_ps1(stop, app_root, timeout=60)
    if code != 0 and not quiet:
        _msg("小面 CapCut", "停止失败，可尝试结束占用 8787 的进程。", error=True)
    return code


def ensure_and_start(app_root: Path, *, force: bool) -> tuple[int, bool]:
    """
    Returns (exit_code, we_started_service).
    If service already healthy and not force → reuse, we_started=False.
    """
    if not force and http_ok(HOME_URL, timeout=1.5):
        print("reuse existing service on", HOME_URL)
        return 0, False

    if force:
        cmd_stop(app_root, quiet=True)
        time.sleep(0.5)

    portable = app_root / "pack" / "portable"
    ensure = portable / "ensure_ready.ps1"
    start = portable / "start_service.ps1"
    logs = app_root / "tools" / "logs"

    code = run_ps1(ensure, app_root, timeout=None)
    if code != 0:
        _msg(
            "小面 CapCut",
            "环境安装/修复失败。\n"
            f"请查看日志：\n{logs}\n\n"
            "建议路径 D:\\xiaomian，并保持联网。",
            error=True,
        )
        return code, False

    # start_service already Free-Port8787 — safe if leftover dead listeners
    code = run_ps1(start, app_root, timeout=180)
    if code != 0:
        # race: another instance may have bound 8787 successfully
        if http_ok(HOME_URL, timeout=2.0):
            print("start script failed but service is healthy — reusing")
            return 0, False
        _msg(
            "小面 CapCut",
            "服务启动失败（可能端口被占用或重复启动）。\n"
            f"请先运行：小面CapCut.exe --stop\n"
            f"日志：\n{logs / 'start_error.txt'}\n{logs / 'uvicorn.err.log'}",
            error=True,
        )
        return code, False

    if not wait_http(HOME_URL, seconds=50):
        if http_ok(HOME_URL, timeout=2.0):
            return 0, True
        _msg(
            "小面 CapCut",
            f"服务未就绪：{HOME_URL}\n请查看：\n{logs}",
            error=True,
        )
        return 5, False

    return 0, True


def cmd_start(
    app_root: Path,
    *,
    open_browser: bool,
    auto_stop: bool,
    force: bool,
) -> int:
    code, we_started = ensure_and_start(app_root, force=force)
    if code != 0:
        return code

    if not open_browser:
        print("OK service", HOME_URL, "started_by_us=" + str(we_started))
        return 0

    if auto_stop:
        proc = open_app_window(app_root)
        try:
            if proc is not None:
                proc.wait()
            else:
                wait_keeper_window()
        except KeyboardInterrupt:
            pass
        finally:
            # Only stop if THIS session started the backend — avoid killing
            # service still used by LAN / another session.
            if we_started:
                cmd_stop(app_root, quiet=True)
            else:
                print("UI closed; service left running (was already up / shared).")
                print("To stop: 小面CapCut.exe --stop")
        return 0

    try:
        webbrowser.open(HOME_URL)
    except Exception:
        pass
    print("OK", HOME_URL, "(--stay: service keeps running)")
    return 0


def cmd_open(app_root: Path) -> int:
    if not http_ok(HOME_URL):
        code, _ = ensure_and_start(app_root, force=False)
        if code != 0:
            return code
    proc = open_app_window(app_root)
    if proc is None:
        try:
            webbrowser.open(HOME_URL)
        except Exception as e:
            _msg("小面 CapCut", f"无法打开浏览器：{e}", error=True)
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Xiaomian CapCut launcher")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stay", action="store_true", help="do not auto-stop on UI close")
    parser.add_argument("--force-restart", action="store_true", help="stop then cold start")
    parser.add_argument("--root", type=str, default="")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else app_root_from_exe()
    if not (root / "pack" / "portable" / "start_service.ps1").exists():
        _msg(
            "小面 CapCut",
            "未找到便携包目录（缺少 pack\\portable）。\n"
            "请把 EXE 放在完整包根目录再运行。\n"
            f"当前：{root}",
            error=True,
        )
        return 2

    if args.stop:
        return cmd_stop(root)

    # stop does not need single-instance; start/open do
    mutex = None
    if not args.stop:
        mutex = acquire_single_instance()
        if mutex is None:
            # Another launcher is already managing UI/session.
            if http_ok(HOME_URL):
                # Just focus UI without second session fighting port
                _msg(
                    "小面 CapCut",
                    "小面已在运行。\n"
                    f"请使用已打开的窗口，或浏览器打开：\n{HOME_URL}\n\n"
                    "若要彻底重启：先点确定后运行\n小面CapCut.exe --stop\n再重新打开。",
                )
                try:
                    webbrowser.open(HOME_URL)
                except Exception:
                    pass
                return 0
            _msg(
                "小面 CapCut",
                "检测到另一个启动器进程未退出。\n"
                "请结束任务管理器中的「小面CapCut.exe」后重试，\n"
                "或运行：小面CapCut.exe --stop",
                error=True,
            )
            return 6
        atexit.register(lambda: release_mutex(mutex))

    if args.open:
        return cmd_open(root)

    auto_stop = not args.stay and not args.no_browser
    return cmd_start(
        root,
        open_browser=not args.no_browser,
        auto_stop=auto_stop,
        force=bool(args.force_restart),
    )


if __name__ == "__main__":
    raise SystemExit(main())
