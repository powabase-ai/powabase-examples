"""Research (Stage A) schemas.

Flow: the SERP agent (Exa web_search) returns `SearchResult`; the backend then
imports each top competitor URL as a Powabase Source and builds `CompetitorTeardown`
entries deterministically from the scraped markdown. `ResearchRun` is the stored row
(async — carries status/progress); `ResearchSource` links a run to a Powabase source.
"""

from datetime import datetime
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
    topic: str
    locale: str = "en-US"
    depth: str = "deep"  # quick | standard | deep


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
    created_at: datetime
