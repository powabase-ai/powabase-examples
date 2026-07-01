"""Content-cluster schemas (topical authority)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ClusterMember(BaseModel):
    id: UUID
    title: str
    slug: str | None = None
    status: str
    cluster_role: str | None = None
    canonical_url: str | None = None


class ContentCluster(BaseModel):
    id: UUID
    business_id: UUID
    label: str
    theme: str | None = None
    pillar_article_id: UUID | None = None
    pillar_locked: bool = False
    pillar_title: str | None = None
    member_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ClusterDetail(ContentCluster):
    members: list[ClusterMember] = Field(default_factory=list)


class NewCluster(BaseModel):
    """Manually found a cluster: a human label + a one-paragraph theme (which subtopics
    belong in it) that future topics get matched against."""

    label: str = Field(min_length=1, max_length=120)
    theme: str | None = Field(default=None, max_length=2000)


class SetPillar(BaseModel):
    article_id: UUID


class MoveMember(BaseModel):
    """Move an article into a cluster as a member (from wherever it currently sits)."""

    article_id: UUID
