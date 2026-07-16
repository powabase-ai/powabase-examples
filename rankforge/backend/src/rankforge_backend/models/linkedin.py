"""LinkedIn post schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Single source of truth for the angle presets. Keep in sync with the frontend ANGLES.
ANGLE_SLUGS = ("key_insight", "lesson", "contrarian", "story", "stat")
Angle = Literal["key_insight", "lesson", "contrarian", "story", "stat"]


class LinkedInGenerate(BaseModel):
    angle: Angle = "key_insight"


class LinkedInUpdate(BaseModel):
    # LinkedIn's hard limit is 3000 chars; a post can run long, but not empty.
    body: str = Field(min_length=1, max_length=3000)


class LinkedInPost(BaseModel):
    id: UUID
    article_id: UUID
    angle: str
    body: str
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class LinkedInPostWithArticle(LinkedInPost):
    """A post enriched with its source article, for the brand-wide Social view
    (the article association is the organizing principle of that page)."""

    article_title: str
    article_status: str
