"""Content-scout schemas (M5) — config, run history, opportunity inbox."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ScoutConfig(BaseModel):
    business_id: UUID
    enabled: bool = False
    cadence: str = "daily"  # daily|weekly
    autonomy: str = "suggest"  # suggest|auto_draft
    min_score: int = 70
    max_drafts_per_run: int = 1
    focus: list[str] = Field(default_factory=list)
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    updated_at: datetime | None = None


class ScoutConfigUpdate(BaseModel):
    enabled: bool | None = None
    cadence: str | None = None
    autonomy: str | None = None
    min_score: int | None = None
    max_drafts_per_run: int | None = None
    focus: list[str] | None = None


class ScoutRun(BaseModel):
    id: UUID
    business_id: UUID
    status: str
    trigger: str
    found: int = 0
    drafted: int = 0
    error: str | None = None
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
    created_at: datetime
    updated_at: datetime
