"""Article (Stage C) schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from .blog import FaqItem


class ArticleGenerate(BaseModel):
    brief_id: UUID


class RefineRequest(BaseModel):
    """Which flagged issues the user picked to fix — OR free-text `instructions` with
    a `mode`, for an instruction-driven refine/rework. Each `targets` selector is
    `axis:signal_key` (e.g. `readability:em_dashes`, `seo:internal_links`) or
    `grounding:<index>`. When both are omitted, refine drives every below-target axis
    automatically (legacy / the post-generation auto-refine)."""

    targets: list[str] | None = None
    instructions: str | None = None
    mode: Literal["refine", "rework"] | None = None

    @field_validator("instructions")
    @classmethod
    def _instr(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("instructions must not be blank")
        if len(v) > 4000:
            raise ValueError("instructions must be at most 4000 characters")
        return v

    @model_validator(mode="after")
    def _exclusive(self):
        if self.instructions and self.targets:
            raise ValueError("send either targets or instructions, not both")
        if self.mode and not self.instructions:
            raise ValueError("mode requires instructions")
        return self


class ArticleUpdate(BaseModel):
    title: str | None = None
    content_md: str | None = None
    meta_title: str | None = None
    meta_description: str | None = None
    status: str | None = None  # draft|in_review|approved|published|archived
    canonical_url: str | None = None  # override for where this article lives
    author: str | None = None  # per-article override of the brand's default author
    category: str | None = Field(default=None, max_length=60)
    summary: str | None = Field(default=None, max_length=2000)
    faq: list[FaqItem] | None = Field(default=None, max_length=20)


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
    # Uploaded per-article social-share image (Powabase public storage). Overrides the
    # dynamically-generated OG card on the public page.
    og_image_url: str | None = None
    cluster_id: UUID | None = None
    cluster_role: str | None = None
    category: str | None = None
    summary: str | None = None
    faq: list[dict] | None = None
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


class FrontmatterRequest(BaseModel):
    """POST /api/articles/{id}/frontmatter body (optional). `force` ("Generate
    summary & FAQ") regenerates the summary and FAQ even when they pass the rules;
    false ("Fix automatically") touches only failing fields."""

    # Strict: "yes"/1/"true" are a 422, not silently coerced to a forced run.
    force: StrictBool = False


FRONTMATTER_CHANGE_FIELDS = (
    "category", "summary", "faq", "meta_title", "meta_description", "content_md",
)


class FrontmatterResult(BaseModel):
    """POST /api/articles/{id}/frontmatter: the article after the fix, the export
    issues still open (`blog_rules.export_issues`; [] = exportable), the fields
    the fix actually changed (subset of FRONTMATTER_CHANGE_FIELDS; [] = nothing;
    `content_md` = a body FAQ section was removed), and the frontmatter step's
    flags (`frontmatter.complete`; e.g. "category defaulted to rag", "model's
    summary was 3 words (needs 40-60) — kept the stored one"; [] = none)."""

    article: Article
    export_issues: list[str]
    changed: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


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
