"""Content-scout schemas (M5) — config, run history, opportunity inbox."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Cadence = Literal["twice_daily", "daily", "weekly"]
Autonomy = Literal["suggest", "auto_draft"]


class ScoutConfig(BaseModel):
    business_id: UUID
    enabled: bool = False
    cadence: Cadence = "daily"
    autonomy: Autonomy = "suggest"
    min_score: int = 70
    max_drafts_per_run: int = 1
    focus: list[str] = Field(default_factory=list)
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    updated_at: datetime | None = None


class ScoutConfigUpdate(BaseModel):
    # Bounded so a typo or hostile value can't trigger a runaway autonomous-spend
    # loop: max_drafts_per_run directly caps how many full generation pipelines a
    # single tick kicks off.
    enabled: bool | None = None
    cadence: Cadence | None = None
    autonomy: Autonomy | None = None
    min_score: int | None = Field(default=None, ge=0, le=100)
    max_drafts_per_run: int | None = Field(default=None, ge=1, le=10)
    focus: list[str] | None = None


class ScoutRun(BaseModel):
    id: UUID
    business_id: UUID
    status: str
    trigger: str
    found: int = 0
    drafted: int = 0
    error: str | None = None
    progress: dict = Field(default_factory=dict)
    created_at: datetime


class Opportunity(BaseModel):
    id: UUID
    business_id: UUID
    scout_run_id: UUID | None = None
    title: str
    angle: str | None = None
    why_now: str | None = None
    keyword: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    evidence: dict = Field(default_factory=dict)
    score: int = 0
    scores: dict = Field(default_factory=dict)
    status: str
    article_id: UUID | None = None
    cluster_id: UUID | None = None
    cluster_role: str | None = None
    progress: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
