"""business_profiles schemas (multi-brand)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

from .blog import BlogProfile

# Bounded so a typo/hostile client can't store unbounded blobs (cost/DoS/bloat) —
# mirrors the ScoutPlan max_length precedent.
_Tag = Annotated[str, Field(max_length=120)]


class Competitor(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    domain: str = Field(max_length=253)


class BusinessProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=253)
    description: str | None = Field(default=None, max_length=2_000)
    niche: str | None = Field(default=None, max_length=200)
    audience: str | None = Field(default=None, max_length=400)
    seed_topics: list[_Tag] = Field(default=[], max_length=100)
    target_keywords: list[_Tag] = Field(default=[], max_length=100)
    competitors: list[Competitor] = Field(default=[], max_length=50)
    brand_kb_id: str | None = Field(default=None, max_length=200)
    sitemap_url: str | None = Field(default=None, max_length=2_000)
    url_pattern: str | None = Field(default=None, max_length=2_000)
    default_author: str | None = Field(default=None, max_length=200)
    # Public storage URL for the brand logo (set via POST /{id}/logo). Client can also
    # clear it by PATCHing null.
    logo_url: str | None = Field(default=None, max_length=2_000)
    # Target-blog conventions (see models/blog.py). None = legacy behavior.
    blog_profile: BlogProfile | None = None


class BusinessProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=253)
    description: str | None = Field(default=None, max_length=2_000)
    niche: str | None = Field(default=None, max_length=200)
    audience: str | None = Field(default=None, max_length=400)
    seed_topics: list[_Tag] | None = Field(default=None, max_length=100)
    target_keywords: list[_Tag] | None = Field(default=None, max_length=100)
    competitors: list[Competitor] | None = Field(default=None, max_length=50)
    brand_kb_id: str | None = Field(default=None, max_length=200)
    sitemap_url: str | None = Field(default=None, max_length=2_000)
    url_pattern: str | None = Field(default=None, max_length=2_000)
    default_author: str | None = Field(default=None, max_length=200)
    # Public storage URL for the brand logo (set via POST /{id}/logo). Client can also
    # clear it by PATCHing null.
    logo_url: str | None = Field(default=None, max_length=2_000)
    # Target-blog conventions (see models/blog.py). None = legacy behavior.
    blog_profile: BlogProfile | None = None


class BusinessProfile(BaseModel):
    id: UUID
    name: str
    domain: str | None = None
    description: str | None = None
    niche: str | None = None
    audience: str | None = None
    seed_topics: list = []
    target_keywords: list = []
    competitors: list = []
    brand_kb_id: str | None = None
    sitemap_url: str | None = None
    url_pattern: str | None = None
    default_author: str | None = None
    logo_url: str | None = None
    # Target-blog conventions (see models/blog.py). None = legacy behavior. Typed
    # loosely on the RESPONSE so one brand with a stored profile that no longer
    # validates can't 500 the brand list; the request models stay strict, and
    # export/publish refuse an invalid profile (services/publishing.py).
    blog_profile: dict | None = None
    materials_progress: dict = {}
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
