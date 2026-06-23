"""Review-comment schemas (editorial collaboration)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    body: str = Field(min_length=1)
    anchor: str | None = None  # optional quoted text / section the note refers to


class CommentUpdate(BaseModel):
    body: str | None = None
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
