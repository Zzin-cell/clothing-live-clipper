"""Full ops status, config probes for the settings drawer."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

from clipper.config import (
    asr_status,
    llm_status,
    public_config,
    resolve_api_key,
    resolve_asr_base_url,
    resolve_asr_model,
    resolve_llm_base_url,
    resolve_llm_key,
    resolve_llm_model,
)
from clipper.media import which_ffmpeg, which_ffprobe

APP_ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = APP_ROOT / "output" / "web_jobs"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pkg_ver(name: str) -> str | None:
    try:
        return metadata.version(name)
    except Exception:
        return None


def _ffmpeg_info() -> dict[str, Any]:
    path = which_ffmpeg()
    if not path:
        return {"ok": False, "path": None, "version": None}
    version = None
    try:
        proc = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        line = (proc.stdout or "").splitlines()[:1]
        version = line[0] if line else None
    except Exception as e:  # noqa: BLE001
        version = f"error: {e}"
    return {"ok": True, "path": path, "version": version}


def _ffprobe_info() -> dict[str, Any]:
    path = which_ffprobe()
    return {"ok": bool(path), "path": path}


def _storage_info(path: Path) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    writable = os.access(path, os.W_OK)
    free_gb = None
    try:
        usage = shutil.disk_usage(path)
        free_gb = round(usage.free / (1024**3), 2)
    except Exception:
        free_gb = None
    level = "ok"
    if not writable:
        level = "error"
    elif free_gb is not None and free_gb < 1.0:
        level = "warn"
    return {
        "path": str(path.resolve()),
        "writable": writable,
        "free_gb": free_gb,
        "level": level,
        "ok": level == "ok",
    }


def _recent_jobs(limit: int = 10) -> list[dict[str, Any]]:
    if not JOBS_DIR.exists():
        return []
    dirs = sorted(
        [p for p in JOBS_DIR.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for d in dirs[:limit]:
        meta_path = d / "job_meta.json"
        if not meta_path.exists():
            out.append({"job_id": d.name, "status": "unknown", "error": None, "created_at": None})
            continue
        try:
            import json

            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {"job_id": d.name, "status": "unknown"}
        out.append(
            {
                "job_id": meta.get("job_id") or d.name,
                "status": meta.get("status"),
                "error": meta.get("error"),
                "created_at": meta.get("created_at"),
                "has_final": meta.get("has_final"),
            }
        )
    return out


def build_status(*, host: str = "127.0.0.1", port: int = 8787) -> dict[str, Any]:
    a = asr_status()
    l = llm_status()
    ff = _ffmpeg_info()
    fp = _ffprobe_info()
    storage = _storage_info(JOBS_DIR)
    recent = _recent_jobs()
    failed = [j for j in recent if j.get("status") == "failed"]

    return {
        "service": {"ok": True, "host": host, "port": port},
        "ffmpeg": ff,
        "ffprobe": fp,
        "extract_audio": {"ok": bool(ff.get("ok")), "note": None if ff.get("ok") else "need_ffmpeg"},
        "asr": {
            "configured": a.get("asr_configured"),
            "ok": None,
            "provider": a.get("asr_provider"),
            "model": a.get("asr_model"),
            "base_url": a.get("asr_base_url"),
            "key_hint": a.get("key_hint"),
            "has_key": a.get("has_key"),
            "source": a.get("source"),
            "note": a.get("asr_note"),
        },
        "llm": {
            "configured": l.get("configured"),
            "ok": None,
            "optional": True,
            "model": l.get("model"),
            "base_url": l.get("base_url"),
            "key_hint": l.get("key_hint"),
            "has_key": l.get("has_key"),
            "source": l.get("source"),
            "note": l.get("note"),
        },
        "storage": storage,
        "deps": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {
                "fastapi": _pkg_ver("fastapi"),
                "httpx": _pkg_ver("httpx"),
                "pydantic": _pkg_ver("pydantic"),
                "uvicorn": _pkg_ver("uvicorn"),
            },
        },
        "recent_jobs": recent,
        "recent_health": {
            "ok": len(failed) == 0,
            "failed_count": len(failed),
            "level": "error" if failed else "ok",
        },
        "compat": {
            "asr": [
                "OpenAI whisper-1",
                "Compatible POST {base}/audio/transcriptions",
                "Prefer response_format=verbose_json with segments (sentence timestamps)",
                "中转: 填 Base URL 如 https://your-proxy/v1 ，模型名以服务商为准",
            ],
            "llm": [
                "gpt-4o-mini / gpt-4o",
                "Any OpenAI-compatible chat model via POST {base}/chat/completions",
                "未配置 LLM 时卖点抽取走本地规则，不阻断只传视频主路径",
            ],
        },
        "config": public_config(),
        "lights": _lights(ff, fp, a, l, storage, failed),
        "checked_at": _utc_now(),
    }


def _lights(ff, fp, a, l, storage, failed) -> dict[str, str]:
    def asr_light() -> str:
        if not a.get("asr_configured"):
            return "red"
        return "yellow"  # configured but not probed

    def llm_light() -> str:
        if not l.get("configured"):
            return "yellow"  # optional
        return "yellow"

    def disk_light() -> str:
        return {"ok": "green", "warn": "yellow", "error": "red"}.get(
            storage.get("level") or "error", "red"
        )

    return {
        "service": "green",
        "ffmpeg": "green" if ff.get("ok") else "red",
        "asr": asr_light(),
        "llm": llm_light(),
        "disk": disk_light(),
        "recent": "red" if failed else "green",
    }


def probe_whisper(timeout_s: float = 30.0) -> dict[str, Any]:
    key = resolve_api_key()
    if not key:
        return {"target": "whisper", "ok": False, "error": "missing_api_key"}
    base = resolve_asr_base_url()
    model = resolve_asr_model()
    # Lightweight auth check via models list (widely supported)
    url = f"{base}/models"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.get(url, headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 401:
            return {"target": "whisper", "ok": False, "error": "unauthorized", "status_code": 401}
        if r.status_code >= 400:
            # Some gateways block /models — still may allow transcriptions
            return {
                "target": "whisper",
                "ok": False,
                "error": f"HTTP {r.status_code}",
                "detail": r.text[:300],
                "hint": "若中转不支持 /models，保存后直接上传短视频试听写",
            }
        return {
            "target": "whisper",
            "ok": True,
            "model": model,
            "base_url": base,
            "status_code": r.status_code,
        }
    except httpx.HTTPError as e:
        return {"target": "whisper", "ok": False, "error": str(e)}


def probe_llm(timeout_s: float = 20.0) -> dict[str, Any]:
    """Fast probe of user UI LLM config (chat only, with latency)."""
    try:
        from clipper.openai_compat import ping
        from clipper.user_llm import runtime_llm

        cfg = runtime_llm()
        out = ping(
            base_url=cfg.get("base_url"),
            api_key=cfg.get("api_key"),
            model=cfg.get("model"),
            timeout=int(min(timeout_s, 20)),
            auto_pick_model=False,
        )
        out["target"] = "llm"
        out["optional"] = True
        return out
    except Exception as e:
        return {"target": "llm", "ok": False, "error": str(e), "optional": True}


def run_probe(target: str) -> dict[str, Any]:
    t = (target or "all").lower()
    if t == "whisper":
        return probe_whisper()
    if t == "llm":
        return probe_llm()
    if t == "all":
        return {"target": "all", "whisper": probe_whisper(), "llm": probe_llm()}
    return {"target": t, "ok": False, "error": "unknown_target"}
