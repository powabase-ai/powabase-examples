"""Brief (Stage B) schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BriefResult(BaseModel):
    """Structured output the brief agent produces from a research run."""

    primary_keyword: str | None = None
    secondary_keywords: list[str] = Field(default_factory=list)
    target_word_count: int | None = None
    headings: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    link_suggestions: dict = Field(default_factory=dict)  # {internal:[], external:[]}
    suggested_title: str | None = None
    suggested_meta: str | None = None


class BriefGenerate(BaseModel):
    research_run_id: UUID
    article_type: str | None = None  # content_templates.type


class BriefUpdate(BaseModel):
    primary_keyword: str | None = None
    secondary_keywords: list[str] | None = None
    target_word_count: int | None = Field(default=None, ge=0)
    headings: list[str] | None = None
    entities: list[str] | None = None
    questions: list[str] | None = None
    link_suggestions: dict | None = None
    suggested_title: str | None = None
    suggested_meta: str | None = None


class Brief(BaseModel):
    id: UUID
    business_id: UUID | None = None
    research_run_id: UUID | None = None
    article_type: str | None = None
    topic: str
    primary_keyword: str | None = None
    secondary_keywords: list = Field(default_factory=list)
    target_word_count: int | None = None
    headings: list = Field(default_factory=list)
    entities: list = Field(default_factory=list)
    questions: list = Field(default_factory=list)
    link_suggestions: dict = Field(default_factory=dict)
    suggested_title: str | None = None
    suggested_meta: str | None = None
    created_at: datetime
    updated_at: datetime
