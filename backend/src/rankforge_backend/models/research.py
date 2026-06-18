"""Research (Stage A) schemas.

`ResearchResult` is what the research agent returns (parsed from its final JSON).
`ResearchRun` is the stored row. Sub-fields are lenient so partial agent output
still parses.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SerpResult(BaseModel):
    rank: int | None = None
    title: str | None = None
    url: str | None = None
    snippet: str | None = None


class CompetitorTeardown(BaseModel):
    url: str | None = None
    title: str | None = None
    word_count: int | None = None
    headings: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    has_schema: bool | None = None
    published_at: str | None = None


class KeywordCluster(BaseModel):
    label: str | None = None
    keywords: list[str] = Field(default_factory=list)
    intent: str | None = None


class ResearchResult(BaseModel):
    """Structured output the research agent produces."""

    topic: str | None = None
    locale: str | None = None
    intent: str | None = None
    serp: list[SerpResult] = Field(default_factory=list)
    paa: list[str] = Field(default_factory=list)
    related_queries: list[str] = Field(default_factory=list)
    competitors: list[CompetitorTeardown] = Field(default_factory=list)
    keyword_clusters: list[KeywordCluster] = Field(default_factory=list)


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
    serp: dict = Field(default_factory=dict)
    competitors: list = Field(default_factory=list)
    clusters: list = Field(default_factory=list)
    intent: str | None = None
    agent_run_id: str | None = None
    created_by: UUID | None = None
    created_at: datetime
