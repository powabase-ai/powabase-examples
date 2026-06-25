"""Internal-link suggestion schemas (M6 / Phase 12.1)."""

from datetime import datetime
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
