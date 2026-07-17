"""LinkedIn post schemas."""

from datetime import datetime
from typing import Annotated, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, StringConstraints

# `Angle` is the single source of truth for the angle presets; ANGLE_SLUGS is derived
# from it (keep in sync with the frontend ANGLES). _ANGLE_CLAUSES in services/linkedin_gen
# is validated against these by the test suite.
Angle = Literal["key_insight", "lesson", "contrarian", "story", "stat"]
ANGLE_SLUGS = get_args(Angle)


class LinkedInGenerate(BaseModel):
    angle: Angle = "key_insight"


class LinkedInUpdate(BaseModel):
    # LinkedIn's hard limit is 3000 chars; a post can run long, but not empty — and a
    # whitespace-only body is empty (strip before the length check, matching the UI).
    body: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3000)
    ]


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
