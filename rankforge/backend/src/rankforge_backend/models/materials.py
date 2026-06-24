"""Brand-materials schemas (M6) — the brand's own pages → a grounding KB."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BrandSource(BaseModel):
    id: UUID
    url: str
    title: str | None = None
    status: str | None = None
    origin: str
    source_id: str | None = None
    created_at: datetime | None = None


class MaterialsIngest(BaseModel):
    urls: list[str] = Field(default_factory=list)


class MaterialsView(BaseModel):
    sources: list[BrandSource] = Field(default_factory=list)
    progress: dict = Field(default_factory=dict)
    kb_ready: bool = False
