"""Article (Stage C) schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ArticleGenerate(BaseModel):
    brief_id: UUID


class ArticleUpdate(BaseModel):
    title: str | None = None
    content_md: str | None = None
    meta_title: str | None = None
    meta_description: str | None = None
    status: str | None = None  # draft|in_review|approved|published|archived


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
    created_at: datetime
    updated_at: datetime


class ArticleSummary(BaseModel):
    id: UUID
    title: str
    status: str
    generation_status: str
    progress: dict = Field(default_factory=dict)
    updated_at: datetime
