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
    """
    Normalize many provider URL styles to an OpenAI-compatible base.
    Accepts:
      https://api.openai.com
      https://api.openai.com/v1
      https://xxx.com/v1/
      https://xxx.com/openai/v1
      https://xxx.openai.azure.com/
    """
    u = (url or "").strip()
    if not u:
        return "https://api.openai.com/v1"
    u = u.rstrip("/")
    # strip trailing endpoint if user pasted full chat url
    for suf in (
        "/chat/completions",
        "/completions",
        "/responses",
        "/models",
    ):
        if u.lower().endswith(suf):
            u = u[: -len(suf)].rstrip("/")
            break
    # azure often uses .../openai/deployments/{name}
    if "/openai/deployments/" in u.lower():
        # keep up to /openai
        idx = u.lower().find("/openai")
        if idx > 0:
            u = u[: idx + len("/openai")]
    # if no version suffix, append /v1 (covers ~most openers/gateways)
    if not re.search(r"/v\d+$", u, flags=re.I) and not u.lower().endswith("/openai"):
        u = u + "/v1"
    return u


def candidate_chat_endpoints(base_url: str) -> list[str]:
    """Generate common OpenAI-compatible chat endpoints for broad gateway support."""
    base = _normalize_base_url(base_url)
    cands: list[str] = []
    def add(x: str) -> None:
        x = x.rstrip("/")
        if x not in cands:
            cands.append(x)

    add(f"{base}/chat/completions")
    # without /v1
    if base.endswith("/v1"):
        add(f"{base[:-3]}/chat/completions")
        add(f"{base}/v1/chat/completions")  # rare double
    else:
        add(f"{base}/v1/chat/completions")
    # some CN gateways
    add(f"{base}/openai/chat/completions")
    if base.endswith("/v1"):
        add(f"{base[:-3]}/openai/v1/chat/completions")
    return cands


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


def _looks_like_url(s: str) -> bool:
    t = (s or "").strip().lower()
    return t.startswith("http://") or t.startswith("https://") or "://" in t


def validate_user_llm_fields(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    require_key: bool = False,
) -> list[str]:
    """Return human-readable validation errors (empty = ok)."""
    errs: list[str] = []
    if base_url is not None:
        bu = str(base_url or "").strip()
        if not bu:
            errs.append("Base URL 不能为空")
        elif not _looks_like_url(bu):
            errs.append("Base URL 需以 http:// 或 https:// 开头")
    if api_key is not None:
        k = str(api_key or "").strip()
        if require_key and not k:
            errs.append("API Key 不能为空")
        if k and _looks_like_url(k):
            errs.append("API Key 填成网址了。Key 一般是 sk-... 字符串，网址请填到 Base URL")
        if k and (" " in k or "\n" in k or "\t" in k):
            errs.append("API Key 不能包含空格/换行")
        if k and len(k) < 8:
            errs.append("API Key 过短，请检查是否粘贴完整")
    if model is not None:
        m = str(model or "").strip()
        if m and _looks_like_url(m):
            errs.append("Model 不能填网址")
    return errs


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
        bu_s = str(bu or "").strip()
        if bu_s and not _looks_like_url(bu_s):
            raise ValueError("Base URL 需以 http:// 或 https:// 开头")
        nxt["base_url"] = _normalize_base_url(bu_s)

    if payload.get("model") is not None or payload.get("llm_model") is not None:
        m = payload.get("llm_model", payload.get("model"))
        m_s = str(m or "").strip()
        if m_s and _looks_like_url(m_s):
            raise ValueError("Model 不能填网址")
        nxt["model"] = m_s

    key_in = payload.get("api_key", payload.get("llm_api_key"))
    if key_in is not None:
        k = str(key_in or "").strip()
        if k:
            if _looks_like_url(k):
                raise ValueError("API Key 填成网址了。请把网址填到 Base URL，Key 填 sk-... 字符串")
            if " " in k or "\n" in k or "\t" in k:
                raise ValueError("API Key 不能包含空格/换行")
            if len(k) < 8:
                raise ValueError("API Key 过短，请检查是否粘贴完整")
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
    """
    OpenAI-compatible auth headers covering most distributors:
    - Authorization: Bearer <key>   (OpenAI / most gateways)
    - api-key: <key>                (Azure OpenAI)
    - x-api-key: <key>              (some Anthropic-style proxies)
    """
    cfg = cfg or runtime_llm()
    key = str(cfg.get("api_key") or "").strip()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "xiaomian-capcut/1.0",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["api-key"] = key
        headers["x-api-key"] = key
        # a few gateways use these
        headers["Token"] = key
        headers["X-Token"] = key
    org = (cfg.get("organization") or "").strip()
    if org:
        headers["OpenAI-Organization"] = org
        headers["OpenAI-Project"] = org
    for k, v in (cfg.get("extra_headers") or {}).items():
        if k and v is not None and str(k).strip():
            headers[str(k)] = str(v)
    return headers


def auth_header_variants(cfg: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """
    Try a few auth styles if the first fails (401/403).
    Order matters: most common first.
    """
    cfg = cfg or runtime_llm()
    key = str(cfg.get("api_key") or "").strip()
    base = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "xiaomian-capcut/1.0",
    }
    org = (cfg.get("organization") or "").strip()
    if org:
        base["OpenAI-Organization"] = org

    variants: list[dict[str, str]] = []
    if not key:
        return [base]

    # 1) OpenAI standard
    h1 = dict(base)
    h1["Authorization"] = f"Bearer {key}"
    variants.append(h1)

    # 2) Azure style
    h2 = dict(base)
    h2["api-key"] = key
    variants.append(h2)

    # 3) Bearer + api-key together (many CN gateways)
    h3 = dict(base)
    h3["Authorization"] = f"Bearer {key}"
    h3["api-key"] = key
    h3["x-api-key"] = key
    variants.append(h3)

    # 4) raw Authorization without Bearer
    h4 = dict(base)
    h4["Authorization"] = key
    variants.append(h4)

    # 5) full multi-header set
    variants.append(build_openai_headers(cfg))
    return variants
