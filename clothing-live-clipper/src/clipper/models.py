from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ClaimType(str, Enum):
    FIT = "fit"
    FABRIC = "fabric"
    SELLING_POINT = "selling_point"
    DETAIL = "detail"
    SCENE = "scene"
    PRICE = "price"
    SIZE = "size"
    OUTFIT = "outfit"
    CHITCHAT = "chitchat"


class TranscriptUtterance(BaseModel):
    utt_id: str
    text: str
    t0_ms: int
    t1_ms: int
    confidence: float | None = None


class Claim(BaseModel):
    claim_id: str
    type: ClaimType
    text: str
    t0_ms: int
    t1_ms: int


class Clip(BaseModel):
    clip_id: str
    t0_ms: int
    t1_ms: int
    text: str
    claim_types: list[ClaimType] = Field(default_factory=list)
    score: float = 0.0
    weight: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return max(0, self.t1_ms - self.t0_ms)


class PlanSlot(BaseModel):
    clip_id: str
    role: str  # hook | trust | cta
    t0_ms: int
    t1_ms: int
    text: str
    score: float = 0.0


class TimelinePlan(BaseModel):
    target_duration_s: int = 60
    golden: list[PlanSlot] = Field(default_factory=list)
    trust: list[PlanSlot] = Field(default_factory=list)
    cta: list[PlanSlot] = Field(default_factory=list)
    total_duration_ms: int = 0
    golden_weight_ratio: float = 0.0
    golden20_passed: bool = False
    warnings: list[str] = Field(default_factory=list)

    def all_slots(self) -> list[PlanSlot]:
        return [*self.golden, *self.trust, *self.cta]


class JobResult(BaseModel):
    video: str | None = None
    transcript: list[TranscriptUtterance] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
    plan: TimelinePlan | None = None
    output_mp4: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
