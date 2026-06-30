"""Review-comment schemas (editorial collaboration)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    anchor: str | None = Field(default=None, max_length=2_000)


class CommentUpdate(BaseModel):
    body: str | None = Field(default=None, max_length=10_000)
    resolved: bool | None = None


class Comment(BaseModel):
    id: UUID
    article_id: UUID
    author_id: UUID | None = None
    author_email: str | None = None
    author_name: str | None = None
    body: str
    anchor: str | None = None
    resolved: bool = False
    created_at: datetime
    updated_at: datetime
