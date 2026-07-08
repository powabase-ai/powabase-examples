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
    """The public, server-rendered view of a published article — everything the SSR
    page needs to build crawlable, share-ready metadata (OG/Twitter/canonical/JSON-LD)."""

    id: UUID
    title: str
    slug: str | None = None
    meta_title: str | None = None
    meta_description: str | None = None
    # A guaranteed non-empty social description: meta_description, else an excerpt
    # derived from the body (so a share card / <meta name=description> is never blank).
    description: str | None = None
    content_html: str | None = None
    json_ld: dict | None = None
    # Where the article actually lives (canonical_url override → brand url_pattern),
    # for <link rel=canonical> and og:url. None if the brand has no url_pattern.
    canonical_url: str | None = None
    # Uploaded social-share image; when null the page uses the generated OG card.
    og_image_url: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    updated_at: datetime
