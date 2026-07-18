from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    target_duration_s: int = 60
    golden_s: int = 20
    cta_s: int = 10
    min_clip_ms: int = 500
    max_clip_ms: int = 15_000
    golden_weight_ratio: float = 0.60
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm_api_key=os.getenv("CLIPPER_LLM_API_KEY") or os.getenv("OPENAI_API_KEY"),
            llm_base_url=os.getenv("CLIPPER_LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_model=os.getenv("CLIPPER_LLM_MODEL", "gpt-4o-mini"),
        )


def asr_status() -> dict:
    """Read-only ASR probe. Never claim ready if provider not implemented."""
    enabled = (os.getenv("CLIPPER_ASR_ENABLED") or "false").lower() in {"1", "true", "yes"}
    provider = (os.getenv("CLIPPER_ASR_PROVIDER") or "none").strip().lower()
    # v1: no real provider implemented
    implemented = False
    configured = bool(enabled and provider not in {"", "none"} and implemented)
    note = None
    if enabled and provider not in {"", "none"} and not implemented:
        note = "not_implemented"
    return {"asr_configured": configured, "asr_note": note, "asr_provider": provider}
