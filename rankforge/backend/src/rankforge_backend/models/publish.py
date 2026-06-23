"""Publishing / export schemas (M8)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PublishRequest(BaseModel):
    target_type: Literal["export", "webhook"] = "export"
    config: dict = Field(default_factory=dict)  # webhook: {"url": "https://..."}


class Publication(BaseModel):
    id: UUID
    article_id: UUID
    target_type: str
    target_id: UUID | None = None
    external_id: str | None = None
    url: str | None = None
    status: str
    published_at: datetime | None = None
    created_at: datetime


class PublicArticle(BaseModel):
    """The public, server-rendered view of a published article."""

    id: UUID
    title: str
    slug: str | None = None
    meta_title: str | None = None
    meta_description: str | None = None
    content_html: str | None = None
    json_ld: dict | None = None
    updated_at: datetime
