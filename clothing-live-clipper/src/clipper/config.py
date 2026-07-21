from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Process-level overrides (session-only settings from UI)
_SESSION: dict[str, str] = {}

# Project root: clothing-live-clipper/
_PKG_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = _PKG_ROOT / ".env"

# Always load project .env (not only CWD)
load_dotenv(DEFAULT_ENV_PATH, override=False)
load_dotenv(override=False)

_ENV_KEYS = {
    "api_key": ("CLIPPER_ASR_API_KEY", "OPENAI_API_KEY"),
    "base_url": ("CLIPPER_ASR_BASE_URL", "CLIPPER_LLM_BASE_URL"),
    "asr_model": ("CLIPPER_ASR_MODEL",),
    "llm_model": ("CLIPPER_LLM_MODEL",),
    "llm_api_key": ("CLIPPER_LLM_API_KEY",),
    "llm_base_url": ("CLIPPER_LLM_BASE_URL",),
    "asr_enabled": ("CLIPPER_ASR_ENABLED",),
    "asr_provider": ("CLIPPER_ASR_PROVIDER",),
}


def _get(name: str, default: str | None = None) -> str | None:
    if name in _SESSION and _SESSION[name] != "":
        return _SESSION[name]
    return os.getenv(name, default)


def session_clear() -> None:
    _SESSION.clear()


def session_update(values: dict[str, str]) -> None:
    for k, v in values.items():
        if v is None:
            continue
        _SESSION[str(k)] = str(v)


def get_session_snapshot() -> dict[str, str]:
    return dict(_SESSION)


def mask_key(key: str | None) -> dict[str, Any]:
    k = (key or "").strip()
    if not k:
        return {"has_key": False, "key_hint": None}
    hint = k[-4:] if len(k) >= 4 else k
    return {"has_key": True, "key_hint": hint}


def resolve_api_key() -> str:
    return (
        _get("CLIPPER_ASR_API_KEY")
        or _get("OPENAI_API_KEY")
        or _get("CLIPPER_LLM_API_KEY")
        or ""
    ).strip()


def resolve_asr_base_url() -> str:
    return (
        _get("CLIPPER_ASR_BASE_URL")
        or _get("CLIPPER_LLM_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")


def resolve_asr_model() -> str:
    return (_get("CLIPPER_ASR_MODEL") or "whisper-1").strip()


def resolve_llm_key() -> str:
    return (
        _get("CLIPPER_LLM_API_KEY")
        or _get("OPENAI_API_KEY")
        or _get("CLIPPER_ASR_API_KEY")
        or ""
    ).strip()


def resolve_llm_base_url() -> str:
    return (
        _get("CLIPPER_LLM_BASE_URL")
        or _get("CLIPPER_ASR_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")


def resolve_llm_model() -> str:
    return (_get("CLIPPER_LLM_MODEL") or "gpt-4o-mini").strip()


def _key_source() -> str:
    for name in ("CLIPPER_ASR_API_KEY", "OPENAI_API_KEY", "CLIPPER_LLM_API_KEY"):
        if name in _SESSION and _SESSION[name].strip():
            return "session"
        if (os.getenv(name) or "").strip():
            return "env"
    return "none"


@dataclass(frozen=True)
class Settings:
    target_duration_s: int = 60
    golden_s: int = 20
    cta_s: int = 10
    min_clip_ms: int = 500
    max_clip_ms: int = 15_000
    # Prefer filling plan toward target; allow slight overshoot in picker
    min_plan_ms: int = 55_000
    max_plan_ms: int = 65_000
    # Playback speed of final cut. Plan selects longer source so final ≈ target after speed.
    # e.g. speed=1.3 → select ~78s source for ~60s output.
    playback_speed: float = 1.3
    golden_weight_ratio: float = 0.60
    # ---- GLOBAL product policy (all jobs: Web / CLI / reclip) ----
    # First ~20s final (golden): ONLY clothing features/selling (面料/显瘦/版型…).
    # Outfit / change-clothes / try-on ("换装/搭配/穿一下") must go AFTER golden.
    golden_features_only: bool = True
    demote_outfit_change_from_golden: bool = True
    exclude_price_from_cut: bool = True
    clothing_only: bool = True
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    @property
    def source_select_duration_s(self) -> int:
        """How many seconds of source timeline to select before speed-up."""
        sp = self.playback_speed if self.playback_speed and self.playback_speed > 0 else 1.0
        return max(self.target_duration_s, int(round(self.target_duration_s * sp)))

    @property
    def source_min_plan_ms(self) -> int:
        sp = self.playback_speed if self.playback_speed and self.playback_speed > 0 else 1.0
        return int(round(self.min_plan_ms * sp))

    @property
    def source_max_plan_ms(self) -> int:
        sp = self.playback_speed if self.playback_speed and self.playback_speed > 0 else 1.0
        return int(round(self.max_plan_ms * sp))

    @classmethod
    def from_env(cls) -> "Settings":
        speed_raw = _get("CLIPPER_PLAYBACK_SPEED") or "1.3"
        try:
            speed = float(speed_raw)
        except ValueError:
            speed = 1.3
        if speed < 0.8 or speed > 2.5:
            speed = 1.3
        def _flag(name: str, default: bool = True) -> bool:
            raw = (_get(name) or ("true" if default else "false")).strip().lower()
            return raw in {"1", "true", "yes", "on"}

        return cls(
            playback_speed=speed,
            golden_features_only=_flag("CLIPPER_GOLDEN_FEATURES_ONLY", True),
            demote_outfit_change_from_golden=_flag(
                "CLIPPER_DEMOTE_OUTFIT_FROM_GOLDEN", True
            ),
            exclude_price_from_cut=_flag("CLIPPER_EXCLUDE_PRICE", True),
            clothing_only=_flag("CLIPPER_CLOTHING_ONLY", True),
            llm_api_key=resolve_llm_key() or None,
            llm_base_url=resolve_llm_base_url(),
            llm_model=resolve_llm_model(),
        )


def asr_status() -> dict:
    """Whether OpenAI-compatible Whisper ASR can run (key present)."""
    enabled_raw = (_get("CLIPPER_ASR_ENABLED") or "true").lower()
    enabled = enabled_raw in {"1", "true", "yes"}
    provider = (_get("CLIPPER_ASR_PROVIDER") or "openai_whisper").strip().lower()
    key = resolve_api_key()
    base = resolve_asr_base_url()
    model = resolve_asr_model()

    if not enabled or provider in {"", "none"}:
        return {
            "asr_configured": False,
            "asr_note": "disabled",
            "asr_provider": provider or "none",
            "asr_model": model,
            "asr_base_url": base,
            "source": _key_source(),
            **mask_key(None),
        }
    if not key:
        return {
            "asr_configured": False,
            "asr_note": "missing_api_key",
            "asr_provider": provider,
            "asr_model": model,
            "asr_base_url": base,
            "source": _key_source(),
            **mask_key(None),
        }
    return {
        "asr_configured": True,
        "asr_note": None,
        "asr_provider": provider,
        "asr_model": model,
        "asr_base_url": base,
        "source": _key_source(),
        **mask_key(key),
    }


def llm_status() -> dict:
    key = resolve_llm_key()
    enabled_raw = (_get("CLIPPER_LLM_ENABLED") or "true").lower()
    enabled = enabled_raw in {"1", "true", "yes"}
    base = resolve_llm_base_url()
    model = resolve_llm_model()
    src = "session" if any(
        k in _SESSION for k in ("CLIPPER_LLM_API_KEY", "OPENAI_API_KEY", "CLIPPER_ASR_API_KEY")
    ) else ("env" if key else "none")
    if not enabled:
        return {
            "configured": False,
            "optional": True,
            "note": "disabled",
            "model": model,
            "base_url": base,
            "source": src,
            **mask_key(None),
        }
    if not key:
        return {
            "configured": False,
            "optional": True,
            "note": "missing_api_key",
            "model": model,
            "base_url": base,
            "source": src,
            **mask_key(None),
        }
    return {
        "configured": True,
        "optional": True,
        "note": None,
        "model": model,
        "base_url": base,
        "source": src,
        **mask_key(key),
    }


def public_config() -> dict[str, Any]:
    a = asr_status()
    l = llm_status()
    return {
        "asr_enabled": (_get("CLIPPER_ASR_ENABLED") or "true").lower() in {"1", "true", "yes"},
        "asr_provider": a.get("asr_provider"),
        "base_url": a.get("asr_base_url"),
        "asr_model": a.get("asr_model"),
        "llm_enabled": (_get("CLIPPER_LLM_ENABLED") or "true").lower() in {"1", "true", "yes"},
        "llm_base_url": l.get("base_url"),
        "llm_model": l.get("model"),
        "api_key_hint": a.get("key_hint"),
        "has_api_key": bool(a.get("has_key")),
        "has_llm_key": bool(l.get("has_key")),
        "source": a.get("source"),
        "env_path": str(DEFAULT_ENV_PATH),
    }


def _merge_env_file(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    order: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in line:
                order.append(line)
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            existing[k] = v
            order.append(f"__KEY__{k}")

    for k, v in updates.items():
        if k not in existing and f"__KEY__{k}" not in order:
            order.append(f"__KEY__{k}")
        existing[k] = v

    out_lines: list[str] = []
    seen: set[str] = set()
    for item in order:
        if item.startswith("__KEY__"):
            k = item[len("__KEY__") :]
            if k in seen:
                continue
            seen.add(k)
            out_lines.append(f"{k}={existing[k]}")
        else:
            out_lines.append(item)
    for k, v in existing.items():
        if k not in seen:
            out_lines.append(f"{k}={v}")

    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def apply_config_update(payload: dict[str, Any], *, env_path: Path | None = None) -> dict[str, Any]:
    """Apply UI config. persist=True writes .env; always can set session overlay."""
    persist = bool(payload.get("persist", True))
    path = env_path or DEFAULT_ENV_PATH

    env_updates: dict[str, str] = {}
    session_vals: dict[str, str] = {}

    def put(env_key: str, value: str) -> None:
        session_vals[env_key] = value
        env_updates[env_key] = value

    if "api_key" in payload and str(payload.get("api_key") or "").strip():
        put("CLIPPER_ASR_API_KEY", str(payload["api_key"]).strip())
        put("OPENAI_API_KEY", str(payload["api_key"]).strip())

    if "base_url" in payload and payload.get("base_url") is not None:
        bu = str(payload.get("base_url") or "").strip().rstrip("/")
        if bu:
            put("CLIPPER_ASR_BASE_URL", bu)
            put("CLIPPER_LLM_BASE_URL", bu)

    if "asr_model" in payload and payload.get("asr_model") is not None:
        m = str(payload.get("asr_model") or "").strip()
        if m:
            put("CLIPPER_ASR_MODEL", m)

    if "llm_model" in payload and payload.get("llm_model") is not None:
        m = str(payload.get("llm_model") or "").strip()
        if m:
            put("CLIPPER_LLM_MODEL", m)

    if "llm_enabled" in payload:
        put("CLIPPER_LLM_ENABLED", "true" if payload.get("llm_enabled") else "false")

    if "asr_enabled" in payload:
        put("CLIPPER_ASR_ENABLED", "true" if payload.get("asr_enabled") else "false")

    if "asr_provider" in payload and payload.get("asr_provider") is not None:
        put("CLIPPER_ASR_PROVIDER", str(payload.get("asr_provider") or "openai_whisper"))

    # Always update session so current process sees changes immediately
    session_update(session_vals)

    if persist and env_updates:
        _merge_env_file(path, env_updates)
        load_dotenv(path, override=True)

    return public_config()
