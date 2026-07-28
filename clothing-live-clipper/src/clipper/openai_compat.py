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


def is_auth_invalid_error(message: str) -> bool:
    m = (message or "").lower()
    if "http 401" in m or "http 403" in m:
        if any(
            k in m
            for k in (
                "token is invalid",
                "invalid api key",
                "incorrect api key",
                "unauthorized",
                "30014",
                "authentication",
                "invalid_api_key",
            )
        ):
            return True
        # bare 401/403 still counts as auth for stop purposes
        return "http 401" in m
    return False


def classify_llm_error(message: str, *, base_url: str = "") -> dict[str, str]:
    msg = message or ""
    base = (base_url or "").lower()
    provider = "siliconflow" if "siliconflow" in base or "siliconflow" in msg.lower() else ""
    if is_auth_invalid_error(msg) or "token is invalid" in msg.lower() or "30014" in msg:
        user = (
            "Token 无效：请到 SiliconFlow 控制台重新复制 API Key 后保存并重试"
            if provider == "siliconflow"
            else "API Key 无效或无权限，请检查 Key 后重试"
        )
        return {"error_class": "auth_invalid", "message": user, "provider_hint": provider}
    if "http 404" in msg.lower() or "http 405" in msg.lower():
        return {
            "error_class": "endpoint",
            "message": "接口地址不可用，请检查 Base URL",
            "provider_hint": provider,
        }
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return {
            "error_class": "timeout",
            "message": "LLM 请求超时，将回退规则排片",
            "provider_hint": provider,
        }
    return {"error_class": "request_failed", "message": msg[:300], "provider_hint": provider}


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
    # IMPORTANT: urllib on this runtime expects a numeric timeout (float/int).
    # Passing a (connect, read) tuple raised:
    #   TypeError: 'tuple' object cannot be interpreted as an integer
    # which made EVERY cloud plan call fail and fall back to short local plans.
    try:
        to = float(timeout) if not isinstance(timeout, (int, float)) else float(timeout)
    except Exception:
        to = 90.0
    if to < 5:
        to = 5.0
    try:
        with urllib.request.urlopen(req, timeout=to) as resp:
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


def _join_content_parts(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for p in content:
        if isinstance(p, str):
            parts.append(p)
        elif isinstance(p, dict):
            if isinstance(p.get("text"), str):
                parts.append(p["text"])
            elif isinstance(p.get("content"), str):
                parts.append(p["content"])
    return "".join(parts)


def _prefer_jsonish(*candidates: str) -> str:
    """Prefer a candidate that looks like JSON over plain reasoning prose."""
    nonempty = [c.strip() for c in candidates if isinstance(c, str) and c.strip()]
    if not nonempty:
        return ""
    for c in nonempty:
        if "{" in c and "}" in c:
            return c
    return nonempty[0]


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
            content_s = _join_content_parts(content).strip() if content is not None else ""
            # Prefer real answer content over reasoning_content (Qwen3 / reasoners).
            reasoning_bits: list[str] = []
            for rk in ("reasoning_content", "reasoning", "refusal"):
                rv = msg.get(rk)
                if isinstance(rv, str) and rv.strip():
                    reasoning_bits.append(rv.strip())
            if content_s:
                # If content itself is empty-looking but reasoning has JSON, prefer JSON later.
                if "{" in content_s and "}" in content_s:
                    return content_s
                if not reasoning_bits:
                    return content_s
                return _prefer_jsonish(content_s, *reasoning_bits)
            if reasoning_bits:
                # Keep full reasoning when it may embed JSON (plan path needs it);
                # probe callers only display a short preview anyway.
                return _prefer_jsonish(*reasoning_bits)
            # some providers put text at choice level
            if isinstance(c0.get("text"), str) and c0["text"].strip():
                return c0["text"]
            # delta stream-like object mistakenly returned
            delta = c0.get("delta") or {}
            if isinstance(delta.get("content"), str) and delta["content"].strip():
                return delta["content"]
            # valid chat completion with empty content still means API is reachable
            if c0.get("finish_reason") or msg.get("role") == "assistant":
                return ""
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


def _thinking_off_fields(model: str) -> dict[str, Any]:
    """
    Disable chain-of-thought / reasoning for models that default to thinking.
    Qwen3.x on SiliconFlow often spends tokens in reasoning_content and returns
    empty or non-JSON content unless thinking is turned off.
    """
    m = (model or "").lower()
    if any(k in m for k in ("qwen3", "qwen/qwen3", "deepseek-r1", "reasoner")):
        return {
            "enable_thinking": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    return {}


def build_payload_variants(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int = 4096,
    force_json: bool = True,
) -> list[dict[str, Any]]:
    """Request bodies ordered from preferred -> more compatible."""
    think_off = _thinking_off_fields(model)
    base_msg = {"model": model, "messages": messages, **think_off}
    variants: list[dict[str, Any]] = []

    if force_json:
        # Prefer JSON + max_tokens + thinking off first (speed + parseability)
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
                "model": model,
                "messages": messages,
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
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            {**base_msg, "temperature": temperature},
            {**base_msg, "top_p": 0.9, "max_tokens": max_tokens},
            {**base_msg, "max_tokens": max_tokens},
            {"model": model, "messages": messages},  # minimal
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
    fast: bool = False,
) -> dict[str, Any]:
    """
    Full Agent-style OpenAI chat.completions call with wide compatibility.

    fast=True: prefer last successful route and minimal retries (for probe/speed).
    """
    import time

    from clipper.user_llm import remember_successful_route

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

    # Prefer last known-good route first (big speed win after first success)
    last_ep = str(rt.get("last_endpoint") or "")
    last_auth = int(rt.get("last_auth_variant") or 0)
    last_payload = int(rt.get("last_payload_variant") or 1)
    if last_ep:
        endpoints = [last_ep] + [u for u in endpoints if u != last_ep]
    if 0 <= last_auth < len(headers_list):
        headers_list = [headers_list[last_auth]] + [
            h for i, h in enumerate(headers_list) if i != last_auth
        ]
    if 0 <= last_payload < len(payloads):
        payloads = [payloads[last_payload]] + [
            p for i, p in enumerate(payloads) if i != last_payload
        ]

    # Fast mode for latency:
    # - prefer last route + fewer auth/payload variants
    # - do NOT brutally clamp long plan timeouts: planning with 60–80 clauses
    #   routinely needs 15–40s; clamping to 20–30s causes false "LLM failed"
    #   while ping still succeeds (ping only sends "1").
    has_last_route = bool(last_ep)
    if fast:
        endpoints = endpoints[:1]
        if has_last_route:
            headers_list = headers_list[:1]
            payloads = payloads[:1]
            # Keep caller budget for real plan jobs; only trim absurd values
            timeout = min(max(int(timeout), 45), 90)
        else:
            headers_list = headers_list[:1]
            payloads = payloads[:2]
            timeout = min(max(int(timeout), 35), 75)

    errors: list[str] = []
    t0 = time.perf_counter()
    for url in endpoints:
        for hi, headers in enumerate(headers_list):
            for pi, payload in enumerate(payloads):
                try:
                    raw = _http_json(url, headers, payload, method="POST", timeout=timeout)
                    content = extract_chat_text(raw)
                    # For force_json planning: empty content is unusable, try next variant.
                    # For probe/fast path: empty-but-valid assistant response still means connected.
                    if force_json and not (content or "").strip():
                        errors.append(f"empty_content@{url}")
                        continue
                    ms = int((time.perf_counter() - t0) * 1000)
                    try:
                        remember_successful_route(
                            endpoint=url,
                            auth_variant=hi if not last_ep else last_auth,
                            payload_variant=pi if not last_ep else last_payload,
                            latency_ms=ms,
                        )
                    except Exception:
                        pass
                    return {
                        "content": content,
                        "raw": raw,
                        "model": mdl,
                        "base_url": base,
                        "endpoint": url,
                        "auth_variant": hi,
                        "payload_variant": pi,
                        "latency_ms": ms,
                    }
                except OpenAICompatError as e:
                    msg = str(e)
                    errors.append(msg)
                    # Auth invalid: try at most one alternate auth on this endpoint, then abort ALL retries
                    if "HTTP 401" in msg or "HTTP 403" in msg:
                        # if this is already the second auth attempt (hi>=1) or message clearly invalid token → stop hard
                        if hi >= 1 or is_auth_invalid_error(msg):
                            info = classify_llm_error(msg, base_url=base)
                            raise OpenAICompatError(
                                f"auth_invalid: {info['message']} | {msg[:200]}"
                            ) from e
                        break  # next auth variant only
                    if "HTTP 404" in msg or "HTTP 405" in msg:
                        hi = len(headers_list)
                        break
                    continue
                except Exception as e:
                    errors.append(f"{type(e).__name__}:{e}")
                    continue
    detail = " | ".join(errors[-6:]) if errors else "unknown"
    info = classify_llm_error(detail, base_url=base)
    if info["error_class"] == "auth_invalid":
        raise OpenAICompatError(f"auth_invalid: {info['message']} | {detail}")
    raise OpenAICompatError(f"all_compat_attempts_failed: {detail}")


def list_models(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: int = 30,
    cfg: dict[str, Any] | None = None,
) -> list[str]:
    """GET {base}/models  (OpenAI compatible)."""
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
                # common shapes: {data:[{id:...}]} or {models:[...]} or [..]
                arr = data.get("data") if isinstance(data, dict) else None
                if arr is None and isinstance(data, dict):
                    arr = data.get("models")
                if arr is None and isinstance(data, list):
                    arr = data
                for m in arr or []:
                    if isinstance(m, str):
                        ids.append(m)
                    elif isinstance(m, dict) and m.get("id"):
                        ids.append(str(m["id"]))
                # unique preserve order
                out = []
                seen = set()
                for x in ids:
                    if x and x not in seen:
                        seen.add(x)
                        out.append(x)
                if out:
                    return out
            except Exception:
                continue
    return []


def pick_default_model(models: list[str], preferred: str | None = None) -> str | None:
    """Auto-match a sensible chat model from /models list."""
    if not models:
        return preferred or None
    pref = (preferred or "").strip()
    if pref and pref in models:
        return pref
    lower_map = {m.lower(): m for m in models}
    if pref and pref.lower() in lower_map:
        return lower_map[pref.lower()]

    aliases = [
        pref,
        "Qwen/Qwen2.5-7B-Instruct",
        "THUDM/glm-4-9b-chat",
        "Qwen/Qwen2.5-14B-Instruct",
        "gpt-4o-mini",
        "deepseek-chat",
        "qwen-turbo",
        "grok-4.5",
        "gpt-4o",
    ]
    for a in aliases:
        if not a:
            continue
        if a in models:
            return a
        if a.lower() in lower_map:
            return lower_map[a.lower()]

    # Prefer smaller instruct/chat before huge reasoning models
    rank_keys = [
        "qwen2.5-7b-instruct",
        "7b-instruct",
        "qwen2.5-7b",
        "7b",
        "9b-chat",
        "9b-instruct",
        "glm-4-9b",
        "14b-instruct",
        "4o-mini",
        "turbo",
        "mini",
        "qwen2.5",
        "deepseek-chat",
        "instruct",
        "chat",
        "gpt-4o",
        "grok",
        # keep qwen3 lower than qwen2.5; thinking models are slower by default
        "qwen3",
        "qwen",
    ]
    for k in rank_keys:
        for m in models:
            ml = m.lower()
            if k in ml and "whisper" not in ml and "embed" not in ml:
                # skip obvious heavy reasoning if lighter exists later in rank — first match is light-first
                if "deepseek-r1" in ml or "72b" in ml or "671b" in ml:
                    continue
                return m
    for m in models:
        ml = m.lower()
        if "whisper" in ml or "embed" in ml:
            continue
        return m
    return models[0]


def discover_models_and_pick(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    preferred: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    models = list_models(base_url=base_url, api_key=api_key, timeout=timeout)
    picked = pick_default_model(models, preferred=preferred)
    return {
        "ok": bool(models),
        "models": models,
        "picked": picked,
        "count": len(models),
        "base_url": normalize_base_url(base_url or ""),
    }


def ping(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: int = 20,
    auto_pick_model: bool = False,
) -> dict[str, Any]:
    """Fast connectivity probe: chat only by default (includes latency)."""
    import time

    t_all = time.perf_counter()
    models_ms = None
    chat_ms = None
    try:
        key = (api_key or "").strip()
        bu = (base_url or "").strip()
        if key.lower().startswith("http://") or key.lower().startswith("https://"):
            return {
                "ok": False,
                "error": "API Key 填成网址了。请把网址放到 Base URL，Key 填 sk-... 字符串",
                "source": "user_ui",
                "latency_ms": 0,
            }
        if bu and not (bu.lower().startswith("http://") or bu.lower().startswith("https://")):
            return {
                "ok": False,
                "error": "Base URL 需以 http:// 或 https:// 开头",
                "source": "user_ui",
                "latency_ms": 0,
            }

        mdl = (model or "").strip() or None
        models: list[str] = []
        picked = mdl
        # Only fetch /models when model empty or explicitly requested
        if auto_pick_model or not picked:
            t0 = time.perf_counter()
            disc = discover_models_and_pick(
                base_url=base_url, api_key=api_key, preferred=mdl, timeout=min(12, timeout)
            )
            models_ms = int((time.perf_counter() - t0) * 1000)
            models = disc.get("models") or []
            if not picked:
                picked = disc.get("picked")
            elif models and picked not in models:
                picked = disc.get("picked") or picked
        if not picked:
            return {
                "ok": False,
                "error": "未指定模型，且 /models 未返回可用模型",
                "source": "user_ui",
                "latency_ms": int((time.perf_counter() - t_all) * 1000),
                "models": models[:100],
                "model_count": len(models),
            }

        t1 = time.perf_counter()
        # ultra-light probe body; accept empty assistant content as long as HTTP 200
        out = chat_completions(
            messages=[{"role": "user", "content": "1"}],
            model=picked,
            base_url=base_url,
            api_key=api_key,
            temperature=0,
            max_tokens=1,
            force_json=False,
            timeout=min(timeout, 12),
            fast=True,
        )
        chat_ms = int((time.perf_counter() - t1) * 1000)
        total_ms = int((time.perf_counter() - t_all) * 1000)
        return {
            "ok": True,
            "model": out.get("model") or picked,
            "base_url": out.get("base_url"),
            "endpoint": out.get("endpoint"),
            "content": (out.get("content") or "ok")[:80],
            "source": "user_ui",
            "models": models[:100],
            "model_count": len(models),
            "auto_picked": bool(picked and picked != (model or "").strip()),
            "latency_ms": total_ms,
            "latency": {
                "total_ms": total_ms,
                "models_ms": models_ms,
                "chat_ms": chat_ms if chat_ms is not None else out.get("latency_ms"),
            },
        }
    except Exception as e:
        total_ms = int((time.perf_counter() - t_all) * 1000)
        raw = str(e)[:500]
        info = classify_llm_error(raw, base_url=base_url or "")
        err = info["message"] if info["error_class"] == "auth_invalid" else raw
        return {
            "ok": False,
            "error": err,
            "error_class": info["error_class"],
            "source": "user_ui",
            "models": [],
            "model_count": 0,
            "picked": model,
            "latency_ms": total_ms,
            "latency": {
                "total_ms": total_ms,
                "models_ms": models_ms,
                "chat_ms": chat_ms,
            },
        }
