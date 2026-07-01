"""Article (Stage C) schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ArticleGenerate(BaseModel):
    brief_id: UUID


class RefineRequest(BaseModel):
    """Which flagged issues the user picked to fix. Each selector is `axis:signal_key`
    (e.g. `readability:em_dashes`, `seo:internal_links`) or `grounding:<index>`. When
    omitted (None), refine drives every below-target axis automatically (legacy / the
    post-generation auto-refine)."""

    targets: list[str] | None = None


class ArticleUpdate(BaseModel):
    title: str | None = None
    content_md: str | None = None
    meta_title: str | None = None
    meta_description: str | None = None
    status: str | None = None  # draft|in_review|approved|published|archived
    canonical_url: str | None = None  # override for where this article lives
    author: str | None = None  # per-article override of the brand's default author


class Article(BaseModel):
    id: UUID
    business_id: UUID | None = None
    brief_id: UUID | None = None
    research_run_id: UUID | None = None
    title: str
    slug: str | None = None
    status: str
    generation_status: str
    generation_error: str | None = None
    progress: dict = Field(default_factory=dict)
    content_md: str = ""
    meta_title: str | None = None
    meta_description: str | None = None
    seo_score: dict | None = None
    geo_score: dict | None = None
    readability_score: dict | None = None
    json_ld: dict | None = None
    grounding_report: dict | None = None
    # Server-rendered, ref-resolved, nh3-sanitized HTML — IDENTICAL to what the public
    # /p/{id} page ships. Populated on the single-article GET so the in-app preview
    # shows exactly what publishes (embedded HTML from a scraped source renders live
    # here too, not as inert markdown text the reviewer can't catch).
    content_html: str | None = None
    canonical_url: str | None = None
    author: str | None = None
    cluster_id: UUID | None = None
    cluster_role: str | None = None
    created_at: datetime
    updated_at: datetime


class ArticleSummary(BaseModel):
    id: UUID
    title: str
    status: str
    generation_status: str
    progress: dict = Field(default_factory=dict)
    updated_at: datetime


class ArticleVersion(BaseModel):
    id: UUID
    article_id: UUID
    created_at: datetime
    word_count: int | None = None


class RemoveLinkResult(BaseModel):
    """Result of a one-click broken-link removal. `repaired` tells the UI HOW the prose
    was mended, so a mechanical strip can be flagged for a human to eyeball:
      'unlinked'   — anchor words kept, URL dropped (keep_text); nothing to mend.
      'llm'        — the copy-editor rewrote the affected paragraph(s) cleanly.
      'mechanical' — the LLM was unavailable/failed on >=1 block, so a regex strip
                     removed the link; it can leave a rough seam worth a human read.
      'none'       — the URL wasn't in the body (stale finding); article unchanged."""

    article: Article
    repaired: Literal["unlinked", "llm", "mechanical", "none"]
