"""
User-provided LLM config (frontend-filled), OpenAI-compatible.

Does NOT read CLIPPER_LLM_* / OPENAI_* from environment for runtime calls.
Stored in output/user_config/llm.json so each machine/user can fill their own key.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[2]
USER_CFG_PATH = _PKG_ROOT / "output" / "user_config" / "llm.json"

_lock = threading.Lock()
_CACHE: dict[str, Any] = {}


def _default() -> dict[str, Any]:
    return {
        "enabled": True,
        "plan_enabled": True,
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "api_key": "",
        # optional OpenAI-compatible extras used by many gateways
        "api_style": "openai_chat",  # openai_chat
        "organization": "",
        "extra_headers": {},
    }


def _normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return "https://api.openai.com/v1"
    # accept roots without /v1 (common for some gateways)
    if not u.endswith("/v1") and not re.search(r"/v\d+$", u):
        # keep as-is if user already has full path like .../v1
        pass
    return u


def load_user_llm() -> dict[str, Any]:
    with _lock:
        if _CACHE:
            return dict(_CACHE)
        data = _default()
        if USER_CFG_PATH.exists():
            try:
                raw = json.loads(USER_CFG_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data.update({k: raw.get(k, data.get(k)) for k in data.keys()})
            except Exception:
                pass
        _CACHE.clear()
        _CACHE.update(data)
        return dict(data)


def save_user_llm(payload: dict[str, Any], *, keep_old_key_if_blank: bool = True) -> dict[str, Any]:
    cur = load_user_llm()
    nxt = dict(cur)

    if "enabled" in payload and payload.get("enabled") is not None:
        nxt["enabled"] = bool(payload.get("enabled"))
    if "plan_enabled" in payload and payload.get("plan_enabled") is not None:
        nxt["plan_enabled"] = bool(payload.get("plan_enabled"))
    # aliases from frontend
    if "llm_plan" in payload and payload.get("llm_plan") is not None:
        nxt["plan_enabled"] = bool(payload.get("llm_plan"))
    if "llm_enabled" in payload and payload.get("llm_enabled") is not None:
        nxt["enabled"] = bool(payload.get("llm_enabled"))

    if payload.get("base_url") is not None or payload.get("llm_base_url") is not None:
        bu = payload.get("llm_base_url", payload.get("base_url"))
        nxt["base_url"] = _normalize_base_url(str(bu or ""))

    if payload.get("model") is not None or payload.get("llm_model") is not None:
        m = payload.get("llm_model", payload.get("model"))
        nxt["model"] = str(m or "").strip()

    key_in = payload.get("api_key", payload.get("llm_api_key"))
    if key_in is not None:
        k = str(key_in or "").strip()
        if k:
            nxt["api_key"] = k
        elif not keep_old_key_if_blank:
            nxt["api_key"] = ""

    if payload.get("organization") is not None:
        nxt["organization"] = str(payload.get("organization") or "").strip()

    if isinstance(payload.get("extra_headers"), dict):
        # only string values
        eh = {}
        for kk, vv in payload["extra_headers"].items():
            if vv is None:
                continue
            eh[str(kk)] = str(vv)
        nxt["extra_headers"] = eh

    USER_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # write without dumping secrets to logs
    with _lock:
        USER_CFG_PATH.write_text(json.dumps(nxt, ensure_ascii=False, indent=2), encoding="utf-8")
        _CACHE.clear()
        _CACHE.update(nxt)
    return public_user_llm()


def public_user_llm() -> dict[str, Any]:
    d = load_user_llm()
    key = str(d.get("api_key") or "").strip()
    hint = key[-4:] if len(key) >= 4 else (key or None)
    ready = bool(d.get("enabled") and d.get("plan_enabled") and key and d.get("model") and d.get("base_url"))
    return {
        "enabled": bool(d.get("enabled")),
        "plan_enabled": bool(d.get("plan_enabled")),
        "base_url": d.get("base_url") or "",
        "model": d.get("model") or "",
        "has_key": bool(key),
        "key_hint": hint,
        "organization": d.get("organization") or "",
        "plan_ready": ready,
        "store": str(USER_CFG_PATH),
        "source": "user_ui",
        "api_style": d.get("api_style") or "openai_chat",
    }


def runtime_llm() -> dict[str, Any]:
    """Values used by llm_plan caller (includes secret key)."""
    d = load_user_llm()
    return {
        "enabled": bool(d.get("enabled", True)),
        "plan_enabled": bool(d.get("plan_enabled", True)),
        "api_key": str(d.get("api_key") or "").strip(),
        "base_url": _normalize_base_url(str(d.get("base_url") or "")),
        "model": str(d.get("model") or "").strip(),
        "organization": str(d.get("organization") or "").strip(),
        "extra_headers": dict(d.get("extra_headers") or {}),
    }


def build_openai_headers(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """OpenAI-compatible headers used by ~90% gateways."""
    cfg = cfg or runtime_llm()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.get('api_key') or ''}",
    }
    # common alternates some providers accept
    if cfg.get("api_key"):
        headers["api-key"] = str(cfg["api_key"])  # Azure-style sometimes
        headers["x-api-key"] = str(cfg["api_key"])  # anthropic-style proxies
    org = (cfg.get("organization") or "").strip()
    if org:
        headers["OpenAI-Organization"] = org
    for k, v in (cfg.get("extra_headers") or {}).items():
        if k and v is not None:
            headers[str(k)] = str(v)
    return headers
