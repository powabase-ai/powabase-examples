"""Research (Stage A) schemas.

Flow: the SERP agent (Exa web_search) returns `SearchResult`; the backend then
imports each top competitor URL as a Powabase Source and builds `CompetitorTeardown`
entries deterministically from the scraped markdown. `ResearchRun` is the stored row
(async — carries status/progress); `ResearchSource` links a run to a Powabase source.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SerpResult(BaseModel):
    rank: int | None = None
    title: str | None = None
    url: str | None = None
    snippet: str | None = None


class KeywordCluster(BaseModel):
    label: str | None = None
    keywords: list[str] = Field(default_factory=list)
    intent: str | None = None


class SearchResult(BaseModel):
    """SERP-agent output (search only — no scraping)."""

    intent: str | None = None
    serp: list[SerpResult] = Field(default_factory=list)
    paa: list[str] = Field(default_factory=list)
    related_queries: list[str] = Field(default_factory=list)
    keyword_clusters: list[KeywordCluster] = Field(default_factory=list)


class CompetitorTeardown(BaseModel):
    """Built by the backend from a scraped Source's markdown."""

    url: str | None = None
    title: str | None = None
    word_count: int | None = None
    headings: list[str] = Field(default_factory=list)
    source_id: str | None = None


class ResearchRunCreate(BaseModel):
    business_id: UUID
    topic: str = Field(min_length=1, max_length=300)
    locale: str = Field(default="en-US", max_length=20)
    # Validate at the boundary: an invalid/stale value used to silently fall through
    # to the most expensive preset instead of being rejected.
    depth: Literal["quick", "standard", "deep"] = "deep"
    # Score every scraped source for authority/trust, prune weak ones, and backfill
    # higher-authority replacements. Costs extra credits (an LLM judge pass + extra
    # scrapes); opt out for a cheaper, unfiltered run.
    evaluate_sources: bool = True


class ResearchRun(BaseModel):
    id: UUID
    business_id: UUID | None = None
    topic: str
    locale: str
    status: str = "done"
    error: str | None = None
    progress: dict = Field(default_factory=dict)
    serp: dict = Field(default_factory=dict)
    competitors: list = Field(default_factory=list)
    clusters: list = Field(default_factory=list)
    intent: str | None = None
    agent_run_id: str | None = None
    created_by: UUID | None = None
    created_at: datetime


class ResearchSource(BaseModel):
    id: UUID
    research_run_id: UUID
    source_id: str
    url: str | None = None
    title: str | None = None
    word_count: int | None = None
    status: str | None = None
    trust_score: int | None = None
    trust_reason: str | None = None
    created_at: datetime


class BrandSource(BaseModel):
    """A scraped source for the centralized library — with its run association."""

    id: UUID
    source_id: str
    url: str | None = None
    title: str | None = None
    word_count: int | None = None
    status: str | None = None
    trust_score: int | None = None
    trust_reason: str | None = None
    created_at: datetime
    research_run_id: UUID
    run_topic: str | None = None


class SourceBulkDelete(BaseModel):
    business_id: UUID
    row_ids: list[UUID] = Field(min_length=1, max_length=500)


class SourcePageMeta(BaseModel):
    # position in the source's image-derivative list (download key)
    index: int = Field(ge=0)
    page: int = Field(ge=1)  # 1-indexed page number (display order)
    width: int | None = None
    height: int | None = None


class SourceMeta(BaseModel):
    """'Original page' availability for a source — true page renders exist only for
    uploaded documents (PDFs), never for scraped URLs (see services/source_view)."""

    source_id: str
    has_page_images: bool
    page_count: int
    pages: list[SourcePageMeta] = Field(default_factory=list)
