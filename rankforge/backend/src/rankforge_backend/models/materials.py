"""Brand-materials schemas (M6) — the brand's own pages → a grounding KB."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class BrandSource(BaseModel):
    id: UUID
    url: str
    title: str | None = None
    status: str | None = None
    origin: str
    source_id: str | None = None
    created_at: datetime | None = None


class MaterialsIngest(BaseModel):
    """How to discover the brand's pages.

    - `sitemap`: parse `url` (or the brand's saved sitemap_url if omitted).
    - `crawl`:   crawl from `url` to discover pages (for sites with no sitemap).
    - `urls`:    import the exact `urls` list.
    """

    mode: Literal["sitemap", "crawl", "urls"] = "sitemap"
    url: str | None = None
    urls: list[str] = Field(default_factory=list)
    max_pages: int | None = Field(default=None, ge=1, le=200)


class MaterialsView(BaseModel):
    sources: list[BrandSource] = Field(default_factory=list)
    progress: dict = Field(default_factory=dict)
    kb_ready: bool = False
