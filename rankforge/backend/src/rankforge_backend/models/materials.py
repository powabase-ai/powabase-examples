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

    `origin` optionally overrides the provenance tag stored on each page (e.g. the
    crawl-preview ingests its confirmed URLs as `urls` but tags them `crawl`).
    """

    mode: Literal["sitemap", "crawl", "urls"] = "sitemap"
    url: str | None = None
    urls: list[str] = Field(default_factory=list)
    max_pages: int | None = Field(default=None, ge=1, le=200)
    origin: Literal["sitemap", "manual", "crawl"] | None = None


class MaterialsDiscover(BaseModel):
    """Preview a crawl: discover the brand's pages WITHOUT importing them."""

    url: str
    max_pages: int | None = Field(default=None, ge=1, le=200)


class MaterialsSelection(BaseModel):
    """Selected brand-source rows for a bulk action (refresh / delete)."""

    row_ids: list[UUID] = Field(min_length=1, max_length=200)


class DiscoveredHost(BaseModel):
    host: str
    urls: list[str]


class MaterialsDiscovery(BaseModel):
    hosts: list[DiscoveredHost] = Field(default_factory=list)
    total: int = 0


class MaterialsView(BaseModel):
    sources: list[BrandSource] = Field(default_factory=list)
    progress: dict = Field(default_factory=dict)
    kb_ready: bool = False
