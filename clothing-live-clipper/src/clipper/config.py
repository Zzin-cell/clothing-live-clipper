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
    # default grok-4.5 for current distributor gateway; override via env/UI
    return (_get("CLIPPER_LLM_MODEL") or "grok-4.5").strip()


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
    # Prefer filling plan toward ~60s final; allow slight overshoot in picker.
    # Soft floor remains ~50s so thin ASR does not force empty padding.
    min_plan_ms: int = 55_000
    max_plan_ms: int = 65_000
    # Playback speed of final cut. Plan selects longer source so final ≈ target after speed.
    # e.g. speed=1.4 → select ~84s source for ~60s output.
    playback_speed: float = 1.4
    golden_weight_ratio: float = 0.60
    # ---- GLOBAL product policy (all jobs: Web / CLI / reclip) ----
    # Goal: short video should NOT feel like a livestream room.
    # First ~20s: strongest unique product features only (attract attention).
    # Outfit / change-clothes / try-on go AFTER golden. Size/price never keep.
    golden_features_only: bool = True
    demote_outfit_change_from_golden: bool = True
    exclude_price_from_cut: bool = True
    clothing_only: bool = True
    de_live_room_feel: bool = True
    unique_features_first: bool = True
    # ASR transcript -> LLM logic plan -> reverse cut (optional; falls back to rules)
    llm_plan_enabled: bool = True
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
        speed_raw = _get("CLIPPER_PLAYBACK_SPEED") or "1.4"
        try:
            speed = float(speed_raw)
        except ValueError:
            speed = 1.4
        if speed < 0.8 or speed > 2.5:
            speed = 1.4
        def _flag(name: str, default: bool = True) -> bool:
            raw = (_get(name) or ("true" if default else "false")).strip().lower()
            return raw in {"1", "true", "yes", "on"}

        # Multi-user: LLM credentials never come from env. Frontend user config only.
        llm_plan = True
        llm_key = None
        llm_base = ""
        llm_model = ""
        try:
            from clipper.user_llm import runtime_llm

            rt = runtime_llm()
            llm_plan = bool(rt.get("enabled", True) and rt.get("plan_enabled", True))
            llm_key = (rt.get("api_key") or None)
            llm_base = str(rt.get("base_url") or "")
            llm_model = str(rt.get("model") or "")
        except Exception:
            pass

        return cls(
            playback_speed=speed,
            golden_features_only=_flag("CLIPPER_GOLDEN_FEATURES_ONLY", True),
            demote_outfit_change_from_golden=_flag(
                "CLIPPER_DEMOTE_OUTFIT_FROM_GOLDEN", True
            ),
            exclude_price_from_cut=_flag("CLIPPER_EXCLUDE_PRICE", True),
            clothing_only=_flag("CLIPPER_CLOTHING_ONLY", True),
            llm_plan_enabled=llm_plan,
            llm_api_key=llm_key,
            llm_base_url=llm_base,
            llm_model=llm_model,
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
    """LLM status from user UI config only (not process env secrets)."""
    try:
        from clipper.user_llm import public_user_llm

        u = public_user_llm()
        return {
            "configured": bool(u.get("has_key") and u.get("model") and u.get("base_url")),
            "optional": True,
            "note": None if u.get("plan_ready") else ("disabled" if not u.get("enabled") else "missing_user_config"),
            "plan_enabled": bool(u.get("plan_enabled")),
            "plan_ready": bool(u.get("plan_ready")),
            "model": u.get("model") or "",
            "base_url": u.get("base_url") or "",
            "source": "user_ui",
            "has_key": bool(u.get("has_key")),
            "key_hint": u.get("key_hint"),
            "store": u.get("store"),
        }
    except Exception as e:
        return {
            "configured": False,
            "optional": True,
            "note": f"user_config_error:{e}",
            "plan_enabled": False,
            "plan_ready": False,
            "model": "",
            "base_url": "",
            "source": "user_ui",
            "has_key": False,
            "key_hint": None,
        }


def public_config() -> dict[str, Any]:
    a = asr_status()
    l = llm_status()
    return {
        "asr_enabled": (_get("CLIPPER_ASR_ENABLED") or "true").lower() in {"1", "true", "yes"},
        "asr_provider": a.get("asr_provider"),
        "base_url": a.get("asr_base_url"),
        "asr_model": a.get("asr_model"),
        "llm_enabled": bool(l.get("plan_enabled") or l.get("configured")),
        "llm_plan_enabled": bool(l.get("plan_enabled")),
        "llm_plan_ready": bool(l.get("plan_ready")),
        "llm_base_url": l.get("base_url") or "",
        "llm_model": l.get("model") or "",
        "api_key_hint": l.get("key_hint") or a.get("key_hint"),
        "has_api_key": bool(a.get("has_key")),
        "has_llm_key": bool(l.get("has_key")),
        "source": "user_ui",
        "llm_source": "user_ui",
        "llm_store": l.get("store"),
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
    """Apply UI config.

    LLM fields are saved to user_config (NOT env).
    ASR fields may still use session/.env for local whisper runtime flags.
    """
    del env_path  # kept for API compatibility

    # ---- LLM: user UI config only ----
    llm_payload = {
        "llm_enabled": payload.get("llm_enabled"),
        "llm_plan": payload.get("llm_plan", payload.get("plan_enabled")),
        "llm_base_url": payload.get("llm_base_url", payload.get("base_url")),
        "llm_model": payload.get("llm_model", payload.get("model")),
        "llm_api_key": payload.get("llm_api_key", payload.get("api_key")),
        "organization": payload.get("organization"),
        "extra_headers": payload.get("extra_headers"),
    }
    # only touch user llm store when any llm field present
    if any(v is not None and v != "" for v in llm_payload.values()):
        try:
            from clipper.user_llm import save_user_llm

            save_user_llm(llm_payload, keep_old_key_if_blank=True)
        except Exception:
            pass

    # ---- ASR optional session flags (non-secret runtime) ----
    session_vals: dict[str, str] = {}
    if "asr_enabled" in payload and payload.get("asr_enabled") is not None:
        session_vals["CLIPPER_ASR_ENABLED"] = "true" if payload.get("asr_enabled") else "false"
    if "asr_provider" in payload and payload.get("asr_provider") is not None:
        session_vals["CLIPPER_ASR_PROVIDER"] = str(payload.get("asr_provider") or "openai_whisper")
    if "asr_model" in payload and payload.get("asr_model") is not None:
        m = str(payload.get("asr_model") or "").strip()
        if m:
            session_vals["CLIPPER_ASR_MODEL"] = m
    if session_vals:
        session_update(session_vals)

    return public_config()
