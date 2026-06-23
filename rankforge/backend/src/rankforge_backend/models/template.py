"""Article-type template schema."""

from uuid import UUID

from pydantic import BaseModel


class ContentTemplate(BaseModel):
    id: UUID
    type: str
    label: str
    outline_guidance: str
    schema_org_type: str
    default_word_count: int | None = None
    geo_target: int
    enabled: bool
