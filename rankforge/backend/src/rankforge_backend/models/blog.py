"""Per-brand blog profile: the target blog's publishing conventions.

Nullable on the brand. When absent, RankForge behaves exactly as before. When set,
generation, linking, scoring, export and refine follow these rules so an exported
post passes the target blog's build unedited."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_KEY = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class BlogCategory(BaseModel):
    key: str = Field(pattern=_KEY, max_length=60)
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    # Technical categories are the default link targets and the fallback category.
    technical: bool = True


class SummaryRule(BaseModel):
    enabled: bool = True
    min_words: int = Field(default=40, ge=1, le=200)
    max_words: int = Field(default=60, ge=1, le=200)

    @model_validator(mode="after")
    def _order(self):
        if self.min_words > self.max_words:
            raise ValueError("summary.min_words must be <= max_words")
        return self


class FaqRule(BaseModel):
    enabled: bool = True
    min: int = Field(default=3, ge=1, le=20)
    max: int = Field(default=6, ge=1, le=20)

    @model_validator(mode="after")
    def _order(self):
        if self.min > self.max:
            raise ValueError("faq.min must be <= max")
        return self


class MetaRule(BaseModel):
    title_max: int = Field(default=60, ge=20, le=200)
    description_max: int = Field(default=160, ge=50, le=400)


class HubPage(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=200)
    topics: list[str] = Field(min_length=1, max_length=10)

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("hub path must start with '/'")
        return v

    @field_validator("topics")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        out = [t.strip() for t in v]
        if any(len(t) < 4 or len(t) > 80 for t in out):
            raise ValueError("each hub topic must be 4-80 characters")
        return out


class LinkRule(BaseModel):
    min: int = Field(default=3, ge=0, le=20)
    max: int = Field(default=5, ge=1, le=20)
    trailing_slash: bool = True
    hub_pages: list[HubPage] = Field(default=[], max_length=50)

    @model_validator(mode="after")
    def _order(self):
        if self.min > self.max:
            raise ValueError("links.min must be <= max")
        return self


class BlogProfile(BaseModel):
    categories: list[BlogCategory] = Field(min_length=1, max_length=20)
    summary: SummaryRule = SummaryRule()
    faq: FaqRule = FaqRule()
    meta: MetaRule = MetaRule()
    links: LinkRule = LinkRule()
    stance: Literal["neutral", "favor_brand"] = "neutral"

    @field_validator("categories")
    @classmethod
    def _unique(cls, v: list[BlogCategory]) -> list[BlogCategory]:
        keys = [c.key for c in v]
        if len(keys) != len(set(keys)):
            raise ValueError("category keys must be unique")
        return v


class FaqItem(BaseModel):
    q: str = Field(min_length=1, max_length=300)
    a: str = Field(min_length=1, max_length=1200)
