"""Internal-link suggestion schemas (M6 / Phase 12.1) + re-link schedule (12.3)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class LinkSuggestion(BaseModel):
    id: UUID
    business_id: UUID
    article_id: UUID
    target_article_id: UUID
    anchor_text: str
    target_url: str
    target_title: str | None = None
    reason: str | None = None
    status: str
    created_at: datetime | None = None


class BrokenLink(BaseModel):
    id: UUID
    business_id: UUID
    article_id: UUID
    url: str
    anchor_text: str | None = None
    kind: str
    http_status: int | None = None
    reason: str | None = None
    status: str
    checked_at: datetime | None = None
    created_at: datetime | None = None


class RelinkConfig(BaseModel):
    business_id: UUID
    enabled: bool
    cadence: str
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_found: int = 0
    updated_at: datetime | None = None


class RelinkConfigUpdate(BaseModel):
    enabled: bool | None = None
    cadence: Literal["weekly", "monthly"] | None = None
