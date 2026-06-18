"""business_profiles schemas (multi-brand)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class Competitor(BaseModel):
    name: str | None = None
    domain: str


class BusinessProfileCreate(BaseModel):
    name: str
    domain: str | None = None
    description: str | None = None
    niche: str | None = None
    audience: str | None = None
    seed_topics: list[str] = []
    target_keywords: list[str] = []
    competitors: list[Competitor] = []
    brand_kb_id: str | None = None
    sitemap_url: str | None = None


class BusinessProfileUpdate(BaseModel):
    name: str | None = None
    domain: str | None = None
    description: str | None = None
    niche: str | None = None
    audience: str | None = None
    seed_topics: list[str] | None = None
    target_keywords: list[str] | None = None
    competitors: list[Competitor] | None = None
    brand_kb_id: str | None = None
    sitemap_url: str | None = None


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
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
