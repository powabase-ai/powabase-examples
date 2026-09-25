"""Per-brand blog profile: the target blog's publishing conventions.

Nullable on the brand. When absent, RankForge behaves exactly as before. When set,
generation, linking, scoring, export and refine follow these rules so an exported
post passes the target blog's build unedited."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_KEY = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class _Strict(BaseModel):
    # A misspelled key (`link` for `links`) must fail loudly, not fall back to the
    # defaults and silently change the export rules.
    model_config = ConfigDict(extra="forbid")


class BlogCategory(_Strict):
    key: str = Field(pattern=_KEY, max_length=60)
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    # Technical categories are the default link targets and the fallback category.
    technical: bool = True


class SummaryRule(_Strict):
    enabled: bool = True
    min_words: int = Field(default=40, ge=1, le=200)
    max_words: int = Field(default=60, ge=1, le=200)

    @model_validator(mode="after")
    def _order(self):
        if self.min_words > self.max_words:
            raise ValueError("summary.min_words must be <= max_words")
        return self


class FaqRule(_Strict):
    enabled: bool = True
    min: int = Field(default=3, ge=1, le=20)
    max: int = Field(default=6, ge=1, le=20)

    @model_validator(mode="after")
    def _order(self):
        if self.min > self.max:
            raise ValueError("faq.min must be <= max")
        return self


class MetaRule(_Strict):
    title_max: int = Field(default=60, ge=20, le=200)
    description_max: int = Field(default=160, ge=50, le=400)


class HubPage(_Strict):
    path: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=200)
    topics: list[str] = Field(min_length=1, max_length=10)

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("hub path must start with '/'")
        # A leading '//' (or '/' plus a backslash, which browsers treat the same) is
        # protocol-relative: the link would leave the brand's site.
        if v.startswith(("//", "/\\")):
            raise ValueError("hub path must be a path on the brand's site, not a host")
        return v

    @field_validator("topics")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        out = [t.strip() for t in v]
        if any(len(t) < 4 or len(t) > 80 for t in out):
            raise ValueError("each hub topic must be 4-80 characters")
        return out


class LinkRule(_Strict):
    min: int = Field(default=3, ge=0, le=20)
    max: int = Field(default=5, ge=1, le=20)
    trailing_slash: bool = True
    hub_pages: list[HubPage] = Field(default=[], max_length=50)

    @model_validator(mode="after")
    def _order(self):
        if self.min > self.max:
            raise ValueError("links.min must be <= max")
        return self


class BlogProfile(_Strict):
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
    # Stripped before the length check, so a whitespace-only q/a is rejected
    # instead of being stored and breaking the target blog's build.
    model_config = ConfigDict(str_strip_whitespace=True)

    q: str = Field(min_length=1, max_length=300)
    a: str = Field(min_length=1, max_length=1200)
