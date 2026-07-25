"""
Full OpenAI-compatible API client (Agent-style).

Goal: work with ~90% of API keys / gateways that speak OpenAI protocol.

Supports:
- OpenAI official
- Most CN/OpenAI-compatible proxies (/v1/chat/completions)
- Azure-style api-key header
- Alternate auth headers (Bearer / api-key / x-api-key)
- Endpoint auto-discovery (/v1, without /v1, /openai/v1)
- Request body fallbacks (response_format / max_tokens / top_p)
- Robust content extraction from chat.completions responses
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urlparse

from clipper.user_llm import (
    auth_header_variants,
    candidate_chat_endpoints,
    runtime_llm,
)


class OpenAICompatError(RuntimeError):
    pass


def _http_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    *,
    method: str = "POST",
    timeout: int = 180,
) -> dict[str, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        raise OpenAICompatError(f"HTTP {e.code} {url}: {body[:500]}") from e
    except Exception as e:
        raise OpenAICompatError(f"{type(e).__name__}: {e}") from e


def normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return "https://api.openai.com/v1"
    # if user pasted full endpoint, strip it
    for suf in ("/chat/completions", "/completions", "/responses", "/models"):
        if u.lower().endswith(suf):
            u = u[: -len(suf)].rstrip("/")
            break
    # azure deployment path -> keep up to /openai
    if "/openai/deployments/" in u.lower():
        i = u.lower().find("/openai")
        if i > 0:
            u = u[: i + len("/openai")]
    if not re.search(r"/v\d+$", u, flags=re.I) and not u.lower().endswith("/openai"):
        u = u + "/v1"
    return u


def extract_chat_text(resp: dict[str, Any]) -> str:
    """Extract assistant text from many OpenAI-compatible response shapes."""
    if not isinstance(resp, dict):
        raise OpenAICompatError("response is not object")

    # standard chat.completions
    try:
        choices = resp.get("choices") or []
        if choices:
            c0 = choices[0] or {}
            msg = c0.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
            # content as list of parts
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, str):
                        parts.append(p)
                    elif isinstance(p, dict):
                        if isinstance(p.get("text"), str):
                            parts.append(p["text"])
                        elif isinstance(p.get("content"), str):
                            parts.append(p["content"])
                joined = "".join(parts).strip()
                if joined:
                    return joined
            # some providers put text at choice level
            if isinstance(c0.get("text"), str) and c0["text"].strip():
                return c0["text"]
            # delta stream-like object mistakenly returned
            delta = c0.get("delta") or {}
            if isinstance(delta.get("content"), str) and delta["content"].strip():
                return delta["content"]
    except Exception:
        pass

    # output_text shortcuts used by some gateways
    for k in ("output_text", "content", "result", "answer", "data"):
        v = resp.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            for kk in ("content", "text", "message"):
                if isinstance(v.get(kk), str) and v[kk].strip():
                    return v[kk]

    raise OpenAICompatError(f"cannot extract chat text: {str(resp)[:400]}")


def build_payload_variants(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int = 4096,
    force_json: bool = True,
) -> list[dict[str, Any]]:
    """Request bodies ordered from preferred -> more compatible."""
    base_msg = {"model": model, "messages": messages}
    variants: list[dict[str, Any]] = []

    if force_json:
        variants.append(
            {
                **base_msg,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "max_tokens": max_tokens,
            }
        )
        variants.append(
            {
                **base_msg,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
        )

    variants.extend(
        [
            {**base_msg, "temperature": temperature, "max_tokens": max_tokens},
            {**base_msg, "temperature": temperature},
            {**base_msg, "top_p": 0.9, "max_tokens": max_tokens},
            {**base_msg, "max_tokens": max_tokens},
            dict(base_msg),  # minimal
        ]
    )

    # de-dup while preserving order
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in variants:
        key = json.dumps(p, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def chat_completions(
    *,
    messages: list[dict[str, Any]],
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    force_json: bool = False,
    timeout: int = 180,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Full Agent-style OpenAI chat.completions call with wide compatibility.

    Returns:
      {
        "content": str,
        "raw": dict,
        "model": str,
        "base_url": str,
        "endpoint": str,
        "auth_variant": int,
        "payload_variant": int,
      }
    """
    rt = cfg or runtime_llm()
    key = (api_key if api_key is not None else rt.get("api_key") or "").strip()
    base = normalize_base_url(base_url if base_url is not None else (rt.get("base_url") or ""))
    mdl = (model if model is not None else rt.get("model") or "").strip()
    if not key:
        raise OpenAICompatError("missing_api_key")
    if not base:
        raise OpenAICompatError("missing_base_url")
    if not mdl:
        raise OpenAICompatError("missing_model")

    # ensure runtime key is present for header builders
    local_cfg = dict(rt)
    local_cfg["api_key"] = key
    local_cfg["base_url"] = base
    local_cfg["model"] = mdl

    endpoints = candidate_chat_endpoints(base)
    headers_list = auth_header_variants(local_cfg)
    payloads = build_payload_variants(
        model=mdl,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        force_json=force_json,
    )

    errors: list[str] = []
    for url in endpoints:
        for hi, headers in enumerate(headers_list):
            for pi, payload in enumerate(payloads):
                try:
                    raw = _http_json(url, headers, payload, method="POST", timeout=timeout)
                    content = extract_chat_text(raw)
                    return {
                        "content": content,
                        "raw": raw,
                        "model": mdl,
                        "base_url": base,
                        "endpoint": url,
                        "auth_variant": hi,
                        "payload_variant": pi,
                    }
                except OpenAICompatError as e:
                    msg = str(e)
                    errors.append(msg)
                    # auth issues -> next auth style
                    if "HTTP 401" in msg or "HTTP 403" in msg:
                        break
                    # endpoint missing -> next endpoint
                    if "HTTP 404" in msg or "HTTP 405" in msg:
                        # force next url
                        hi = len(headers_list)
                        break
                    # bad request due to response_format etc -> next payload
                    continue
                except Exception as e:
                    errors.append(f"{type(e).__name__}:{e}")
                    continue
    detail = " | ".join(errors[-6:]) if errors else "unknown"
    raise OpenAICompatError(f"all_compat_attempts_failed: {detail}")


def list_models(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: int = 30,
    cfg: dict[str, Any] | None = None,
) -> list[str]:
    rt = cfg or runtime_llm()
    key = (api_key if api_key is not None else rt.get("api_key") or "").strip()
    base = normalize_base_url(base_url if base_url is not None else (rt.get("base_url") or ""))
    if not key or not base:
        return []
    local_cfg = dict(rt)
    local_cfg["api_key"] = key
    local_cfg["base_url"] = base
    urls = [f"{base}/models"]
    if base.endswith("/v1"):
        urls.append(f"{base[:-3]}/models")
    else:
        urls.append(f"{base}/v1/models")
    for url in urls:
        for headers in auth_header_variants(local_cfg):
            try:
                data = _http_json(url, headers, None, method="GET", timeout=timeout)
                ids = []
                for m in data.get("data") or []:
                    if isinstance(m, dict) and m.get("id"):
                        ids.append(str(m["id"]))
                if ids:
                    return ids
            except Exception:
                continue
    return []


def ping(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: int = 40,
) -> dict[str, Any]:
    """Connectivity probe used by frontend '测试连通'."""
    try:
        out = chat_completions(
            messages=[{"role": "user", "content": "reply with ok only"}],
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0,
            max_tokens=8,
            force_json=False,
            timeout=timeout,
        )
        return {
            "ok": True,
            "model": out.get("model"),
            "base_url": out.get("base_url"),
            "endpoint": out.get("endpoint"),
            "content": (out.get("content") or "")[:80],
            "source": "user_ui",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:500], "source": "user_ui"}
