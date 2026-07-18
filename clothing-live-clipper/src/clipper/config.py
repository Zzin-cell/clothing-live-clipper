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
    """Whether OpenAI-compatible Whisper ASR can run (key present)."""
    enabled_raw = (os.getenv("CLIPPER_ASR_ENABLED") or "true").lower()
    enabled = enabled_raw in {"1", "true", "yes"}
    provider = (os.getenv("CLIPPER_ASR_PROVIDER") or "openai_whisper").strip().lower()
    key = (
        os.getenv("CLIPPER_ASR_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("CLIPPER_LLM_API_KEY")
        or ""
    ).strip()
    base = (
        os.getenv("CLIPPER_ASR_BASE_URL")
        or os.getenv("CLIPPER_LLM_BASE_URL")
        or "https://api.openai.com/v1"
    )
    model = os.getenv("CLIPPER_ASR_MODEL") or "whisper-1"

    if not enabled or provider in {"", "none"}:
        return {
            "asr_configured": False,
            "asr_note": "disabled",
            "asr_provider": provider or "none",
            "asr_model": model,
            "asr_base_url": base,
        }
    if not key:
        return {
            "asr_configured": False,
            "asr_note": "missing_api_key",
            "asr_provider": provider,
            "asr_model": model,
            "asr_base_url": base,
        }
    # Implemented: openai-compatible /audio/transcriptions
    return {
        "asr_configured": True,
        "asr_note": None,
        "asr_provider": provider,
        "asr_model": model,
        "asr_base_url": base,
    }
