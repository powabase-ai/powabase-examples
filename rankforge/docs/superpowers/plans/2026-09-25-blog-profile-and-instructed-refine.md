# Blog Profile + Instructed Refine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RankForge articles for a brand with a `blog_profile` export as `.mdx` that pass the Powabase website build unedited, and editors can refine or rework any article from free-text instructions with one-click revert.

**Architecture:** A nullable per-brand `blog_profile` jsonb drives everything. A pure `blog_rules` module owns every rule check (word counts, clamps, export checks, URL shape). Services consult it only when a profile exists, so brands without a profile keep today's behavior byte-for-byte. A new `frontmatter` service writes category, summary and FAQ. The linker gains hub pages and a candidate pool. `revise` gains a single-pass instructed refine. Frontmatter is snapshotted with every version so revert restores it.

**Tech Stack:** FastAPI · psycopg3 · Pydantic v2 · `uv run pytest` (hermetic, `MagicMock` DB) · Next.js 16 App Router · TanStack Query · shadcn/ui.

**Spec:** `rankforge/docs/superpowers/specs/2026-09-25-blog-profile-and-instructed-refine-design.md`. Read it before starting any task.

## Global Constraints

- Work in worktree `/home/zipeng/worktrees/rankforge-blog-profile`, branch `feat/rankforge-blog-profile`. All paths below are relative to `rankforge/`.
- Backend tests: `cd backend && uv run pytest` — **never bare `pytest`**. Tests are hermetic: mock at the `Database` / Powabase client boundary. Baseline: **586 passed**.
- Ruff line length 88. Pydantic v2. psycopg v3 (`Json(...)` wrapper for jsonb params).
- **No profile → no behavior change.** Every new branch is gated on `brand.get("blog_profile")`. Existing tests must pass unchanged.
- Length limits count **code points** (`len(str)` in Python), matching the website's `[...s].length`.
- Word counts use whitespace splitting (`len(s.split())`), matching the website's `wordCount`.
- Powabase defaults: categories `rag, agents, backend, coding-agents, models, enterprise` (first four `technical`); summary 40–60 words; FAQ 3–6; title ≤60; description ≤160; links 3–5; trailing slash on; stance `favor_brand`.
- Agent models: new small agents use `"claude-sonnet-4-6"`, the same as `META_MODEL`. The instructed pass uses the existing reviser agent (`ensure_reviser_agent`).
- Frontend has no test runner. Verify with `cd frontend && npx tsc --noEmit && npm run lint && npm run build`.
- Commit after every task. End every commit message with:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`

## Review Focus

1. **A summary or FAQ answer containing `"`, `:`, `#`, newlines or non-ASCII must export as YAML the website parses.** JSON-quoting every scalar handles it. Test: `test_render_markdown_profile_yaml_safety` (Task 7).
2. **Instructions that are whitespace-only, or over 4000 characters, are rejected with 422 before claiming the article.** A claimed-then-failed article would be left in `refining`. Tests: `test_refine_rejects_blank_instructions` and `test_refine_rejects_long_instructions` (Task 10).
3. **Revert on an article whose only version equals the current body returns 404, not a no-op "success".** Test: `test_revert_404_when_no_differing_version` (Task 9).
4. **A hub path with a query or fragment (`/free-mvp/?ref=x`) or a file (`/llms.txt`) must not get a slash appended in the wrong place.** Test: `test_with_trailing_slash_edge_cases` (Task 2).
5. **A brand whose profile sets FAQ off must still get extracted FAQPage JSON-LD and no `faq:` frontmatter, while the writer keeps its FAQ-section rule.** Tests: `test_optimize_extracts_when_faq_disabled` (Task 6) and `test_render_markdown_faq_disabled` (Task 7).

---

## File Structure

**Backend — create**
- `backend/schema/0036_blog_profile.sql`: all DDL for this feature.
- `backend/src/rankforge_backend/models/blog.py`: `BlogProfile` and its parts, `FaqItem`.
- `backend/src/rankforge_backend/services/blog_rules.py`: **pure** rule helpers (no DB, no network).
- `backend/src/rankforge_backend/services/frontmatter.py`: agent that writes category, summary and FAQ, plus the meta-limit wrapper.
- `backend/scripts/seed_powabase_blog_profile.py`: Powabase profile seed.
- Tests: `backend/tests/test_blog_rules.py`, `test_blog_profile_model.py`, `test_frontmatter.py`, `test_instructed_refine.py`, `test_revert.py`, `test_patch_notes.py`.

**Backend — modify**
- `models/business.py`, `services/business_profiles.py`: `blog_profile` field.
- `models/article.py`: new fields, `RefineRequest`, `ArticleUpdate`.
- `models/linking.py`: nullable `target_article_id`.
- `models/clusters.py`, `services/clusters.py`, `routes/clusters.py`: cluster `category`.
- `services/generation.py`: columns, `_update` jsonb, `update_article` versioning, writer prompt, pipeline step.
- `services/scoring.py`: `_sentences`, profile limits, `internal_links`.
- `services/linking.py`: trailing slash, hubs, `link_candidates`, minimum gaps.
- `services/geo_optimize.py`: FAQ from stored field.
- `services/publishing.py`, `routes/publish.py`: profile frontmatter, export check.
- `services/revise.py`, `routes/articles.py`: instructed pass, revert, frontmatter endpoint.
- `services/relink.py`, `routes/relink.py`: patch notes.

**Frontend — modify/create**
- `src/lib/api.ts`, `src/lib/hooks/useArticles.ts`, `src/lib/hooks/useBrands.ts`.
- Create `src/components/brand/BlogProfileForm.tsx`, `src/components/PostPanel.tsx`.
- Modify `src/app/brands/[id]/settings/page.tsx`, `src/app/brands/[id]/articles/[articleId]/page.tsx`, `src/components/PublishDialog.tsx`, `src/app/brands/[id]/clusters/page.tsx`, `src/app/brands/[id]/scouts/page.tsx`.

---

### Task 1: Schema, BlogProfile model, brand + article fields

**Files:**
- Create: `backend/schema/0036_blog_profile.sql`
- Create: `backend/src/rankforge_backend/models/blog.py`
- Modify: `backend/src/rankforge_backend/models/business.py`
- Modify: `backend/src/rankforge_backend/services/business_profiles.py:11-21,59-89,98-104`
- Modify: `backend/src/rankforge_backend/models/article.py`
- Modify: `backend/src/rankforge_backend/models/linking.py:14`
- Modify: `backend/src/rankforge_backend/services/generation.py:157-163` (`_ARTICLE_COLUMNS`), `:247-261` (`_update`)
- Test: `backend/tests/test_blog_profile_model.py`

**Interfaces:**
- Produces:
  - `models.blog.BlogProfile` (fields `categories: list[BlogCategory]`, `summary: SummaryRule`, `faq: FaqRule`, `meta: MetaRule`, `links: LinkRule`, `stance: Literal["neutral","favor_brand"]`).
  - `BlogCategory{key,label,description,technical}`, `HubPage{path,title,topics}`, `FaqItem{q,a}`.
  - Article dicts now carry `category`, `summary`, `faq`.
  - `generation._update` wraps `faq` as jsonb.

- [ ] **Step 1: Write the migration**

`backend/schema/0036_blog_profile.sql`:

```sql
-- Per-brand blog profile (target-blog conventions) + the article fields it drives.
-- All nullable: a brand with no profile behaves exactly as before.
alter table public.business_profiles add column if not exists blog_profile jsonb;
alter table public.content_clusters  add column if not exists category text;
alter table public.articles
    add column if not exists category text,
    add column if not exists summary  text,
    add column if not exists faq      jsonb;          -- [{ "q": str, "a": str }]
alter table public.article_versions
    add column if not exists frontmatter jsonb;       -- snapshot restored by revert

-- Hub-page link suggestions have no target ARTICLE. Relax the not-null and dedupe
-- them on (article, url, anchor) instead of (article, target_article, anchor).
alter table public.link_suggestions alter column target_article_id drop not null;
create unique index if not exists link_suggestions_hub_uniq
    on public.link_suggestions (article_id, target_url, lower(coalesce(anchor_text, '')))
    where target_article_id is null;
```

- [ ] **Step 2: Write the failing model tests**

`backend/tests/test_blog_profile_model.py`:

```python
"""BlogProfile validation (pure Pydantic)."""

import pytest
from pydantic import ValidationError

from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.models.business import BusinessProfileUpdate

CATS = [
    {"key": "rag", "label": "RAG", "description": "d", "technical": True},
    {"key": "enterprise", "label": "Ent", "description": "d", "technical": False},
]


def _profile(**over):
    return {"categories": CATS, **over}


def test_defaults_fill_in():
    p = BlogProfile.model_validate(_profile())
    assert p.summary.min_words == 40 and p.summary.max_words == 60
    assert p.faq.min == 3 and p.faq.max == 6
    assert p.meta.title_max == 60 and p.meta.description_max == 160
    assert p.links.min == 3 and p.links.max == 5 and p.links.trailing_slash
    assert p.stance == "neutral"


def test_rejects_duplicate_category_keys():
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(categories=[CATS[0], CATS[0]]))


def test_rejects_bad_category_key_shape():
    bad = [{**CATS[0], "key": "RAG Stuff"}]
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(categories=bad))


def test_rejects_min_over_max():
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(summary={"min_words": 70, "max_words": 60}))
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(links={"min": 6, "max": 5}))


def test_hub_path_must_start_with_slash():
    hub = {"path": "vector-database/", "title": "t", "topics": ["vector database"]}
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(links={"hub_pages": [hub]}))


def test_hub_topics_bounds():
    short = {"path": "/x/", "title": "t", "topics": ["db"]}
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(links={"hub_pages": [short]}))
    none = {"path": "/x/", "title": "t", "topics": []}
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(links={"hub_pages": [none]}))


def test_requires_at_least_one_category():
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(categories=[]))


def test_update_accepts_null_to_disable():
    u = BusinessProfileUpdate.model_validate({"blog_profile": None})
    assert "blog_profile" in u.model_fields_set and u.blog_profile is None
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `cd backend && uv run pytest tests/test_blog_profile_model.py -q`
Expected: FAIL. `ModuleNotFoundError: rankforge_backend.models.blog`.

- [ ] **Step 4: Implement `models/blog.py`**

```python
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
```

- [ ] **Step 5: Wire `blog_profile` into brand models and the service**

In `models/business.py`, add `from .blog import BlogProfile` at the top. Add this line after `logo_url` in **all three** classes (`BusinessProfileCreate`, `BusinessProfileUpdate`, `BusinessProfile`):

```python
    # Target-blog conventions (see models/blog.py). None = legacy behavior.
    blog_profile: BlogProfile | None = None
```

In `services/business_profiles.py`:
- Add `blog_profile` to `_COLUMNS`, after `logo_url, `.
- Leave `_JSONB_FIELDS` unchanged. `blog_profile` is serialized explicitly below because it's a model, not a list.
- In `create_profile`, add `blog_profile` to the column list and a 15th `%s`. Pass:

```python
            Json(data.blog_profile.model_dump()) if data.blog_profile else None,
```

In `update_profile`, replace the loop body:

```python
    for key, value in fields.items():
        # keys come from a fixed Pydantic model → safe to interpolate as column names
        set_clauses.append(f"{key} = %s")
        if key == "blog_profile":
            params.append(Json(value) if value is not None else None)
        else:
            params.append(Json(value) if key in _JSONB_FIELDS else value)
```

(`model_dump(exclude_unset=True)` has already turned the nested model into a dict.)

- [ ] **Step 6: Article model + columns**

In `models/article.py`:
- Add `from .blog import FaqItem`.
- Add to `ArticleUpdate`:

```python
    category: str | None = Field(default=None, max_length=60)
    summary: str | None = Field(default=None, max_length=2000)
    faq: list[FaqItem] | None = Field(default=None, max_length=20)
```

- Add to `Article` (after `cluster_role`):

```python
    category: str | None = None
    summary: str | None = None
    faq: list[dict] | None = None
```

In `models/linking.py`, change `target_article_id: UUID` to `target_article_id: UUID | None = None` and add the comment `# None = a hub-page link (no target article)`.

In `services/generation.py`, append `category, summary, faq, ` before `created_at` in `_ARTICLE_COLUMNS`. Add `"faq"` to the `jsonb` set in `_update`.

- [ ] **Step 7: Run the tests**

Run: `cd backend && uv run pytest -q`
Expected: all pass (586 + 8 new).

- [ ] **Step 8: Commit**

```bash
git add backend/schema/0036_blog_profile.sql backend/src/rankforge_backend/models backend/src/rankforge_backend/services/business_profiles.py backend/src/rankforge_backend/services/generation.py backend/tests/test_blog_profile_model.py
git commit -m "feat(rankforge): per-brand blog_profile model + article frontmatter columns"
```

---

### Task 2: Pure blog rules module

**Files:**
- Create: `backend/src/rankforge_backend/services/blog_rules.py`
- Test: `backend/tests/test_blog_rules.py`

**Interfaces:**
- Consumes: `models.blog.BlogProfile`.
- Produces (all pure):
  - `profile_of(brand: dict | None) -> BlogProfile | None`
  - `word_count(s: str) -> int`
  - `trim_to_words(s: str, max_words: int) -> str`
  - `clamp_chars(s: str, limit: int) -> str`
  - `with_trailing_slash(url: str) -> str`
  - `hub_url(domain: str | None, path: str, trailing_slash: bool) -> str`
  - `fallback_category(profile, cluster_category: str | None) -> str`
  - `validate_frontmatter(raw: dict, profile, *, cluster_category: str | None) -> tuple[dict, list[str]]`. Returns `(clean, flags)`, where `clean` has keys `category`, `summary`, `faq`.
  - `export_issues(article: dict, profile) -> list[str]`
  - `BODY_FAQ_RE` (compiled regex)
  - `strip_body_faq(md: str) -> str`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_blog_rules.py`:

```python
"""blog_rules — pure rule helpers."""

from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.services import blog_rules as br

P = BlogProfile.model_validate({
    "categories": [
        {"key": "enterprise", "label": "E", "technical": False},
        {"key": "rag", "label": "R", "technical": True},
    ],
})
S45 = " ".join(["word"] * 44) + " end."


def test_profile_of_none_and_valid():
    assert br.profile_of(None) is None
    assert br.profile_of({"blog_profile": None}) is None
    assert br.profile_of({"blog_profile": P.model_dump()}).faq.max == 6


def test_profile_of_tolerates_corrupt_json():
    assert br.profile_of({"blog_profile": {"categories": "nope"}}) is None


def test_trim_to_words_prefers_sentence_boundary():
    s = "One two three. Four five six seven. Eight nine."
    assert br.trim_to_words(s, 7) == "One two three. Four five six seven."
    assert br.trim_to_words(s, 2) == "One two"  # no boundary fits → hard cut
    assert br.trim_to_words(s, 50) == s


def test_clamp_chars_word_boundary_and_codepoints():
    assert br.clamp_chars("hello brave new world", 15) == "hello brave new"
    assert br.clamp_chars("short", 60) == "short"
    assert len(br.clamp_chars("é" * 70, 60)) == 60


def test_with_trailing_slash_edge_cases():
    assert br.with_trailing_slash("https://x.ai/blog/a") == "https://x.ai/blog/a/"
    assert br.with_trailing_slash("https://x.ai/blog/a/") == "https://x.ai/blog/a/"
    assert br.with_trailing_slash("/free-mvp?ref=x") == "/free-mvp/?ref=x"
    assert br.with_trailing_slash("/free-mvp/#top") == "/free-mvp/#top"
    assert br.with_trailing_slash("/llms.txt") == "/llms.txt"
    assert br.with_trailing_slash("https://x.ai") == "https://x.ai/"


def test_hub_url():
    assert br.hub_url("powabase.ai", "/vector-database", True) == (
        "https://powabase.ai/vector-database/"
    )
    assert br.hub_url("https://powabase.ai/", "/x/", False) == "https://powabase.ai/x/"
    assert br.hub_url(None, "/x/", True) == "/x/"


def test_fallback_category_order():
    assert br.fallback_category(P, "enterprise") == "enterprise"
    assert br.fallback_category(P, "bogus") == "rag"  # first technical
    assert br.fallback_category(P, None) == "rag"


def test_validate_frontmatter_happy():
    faq = [{"q": f"Q{i}?", "a": "A."} for i in range(4)]
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": S45, "faq": faq}, P, cluster_category=None
    )
    assert clean["category"] == "rag" and clean["summary"] == S45
    assert len(clean["faq"]) == 4 and flags == []


def test_validate_frontmatter_cluster_category_wins():
    clean, _ = br.validate_frontmatter(
        {"category": "rag", "summary": S45, "faq": []}, P, cluster_category="enterprise"
    )
    assert clean["category"] == "enterprise"


def test_validate_frontmatter_unknown_category_falls_back():
    clean, flags = br.validate_frontmatter(
        {"category": "nope", "summary": S45, "faq": []}, P, cluster_category=None
    )
    assert clean["category"] == "rag"
    assert not any("category" in f for f in flags)


def test_validate_frontmatter_summary_trimmed_and_flagged_short():
    long = " ".join(["w"] * 80) + "."
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": long, "faq": []}, P, cluster_category=None
    )
    assert br.word_count(clean["summary"]) <= 60
    assert any("summary trimmed" in f for f in flags)
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": "Too short.", "faq": []}, P, cluster_category=None
    )
    assert any("summary is 2 words" in f for f in flags)


def test_validate_frontmatter_faq_truncate_drop_empty_and_flag():
    faq = [{"q": f"Q{i}?", "a": "A."} for i in range(9)] + [{"q": "", "a": "x"}]
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": S45, "faq": faq}, P, cluster_category=None
    )
    assert len(clean["faq"]) == 6
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": S45, "faq": [{"q": "Q?", "a": "A"}]},
        P, cluster_category=None,
    )
    assert any("faq has 1" in f for f in flags)


def test_validate_frontmatter_respects_disabled_rules():
    off = P.model_copy(update={
        "summary": P.summary.model_copy(update={"enabled": False}),
        "faq": P.faq.model_copy(update={"enabled": False}),
    })
    clean, flags = br.validate_frontmatter({"category": "rag"}, off, cluster_category=None)
    assert clean["summary"] is None and clean["faq"] is None and flags == []


def _art(**over):
    faq = [{"q": f"Q{i}?", "a": "A."} for i in range(3)]
    return {
        "title": "Short title", "meta_title": None, "meta_description": "d" * 100,
        "category": "rag", "summary": S45, "faq": faq,
        "content_md": "# T\n\n## Intro\n\nBody.", **over,
    }


def test_export_issues_clean():
    assert br.export_issues(_art(), P) == []


def test_export_issues_each_rule():
    issues = br.export_issues(_art(category=None), P)
    assert any("category" in i for i in issues)
    issues = br.export_issues(_art(category="nope"), P)
    assert any("unknown category" in i for i in issues)
    issues = br.export_issues(_art(title="x" * 70), P)
    assert any("title is 70" in i for i in issues)
    # A long H1 is fine when metaTitle fits.
    assert br.export_issues(_art(title="x" * 70, meta_title="short"), P) == []
    issues = br.export_issues(_art(meta_description="d" * 161), P)
    assert any("description is 161" in i for i in issues)
    issues = br.export_issues(_art(summary=None), P)
    assert any("summary" in i for i in issues)
    issues = br.export_issues(_art(faq=[]), P)
    assert any("faq" in i for i in issues)
    issues = br.export_issues(
        _art(content_md="# T\n\n## Frequently asked questions\n\n### Q?\n\nA."), P
    )
    assert any("FAQ section in the body" in i for i in issues)


def test_strip_body_faq_removes_section_until_next_h2():
    md = "# T\n\n## A\n\ntext\n\n## FAQ\n\n### Q?\n\nA.\n\n## Conclusion\n\nend"
    out = br.strip_body_faq(md)
    assert "## FAQ" not in out and "### Q?" not in out
    assert "## A" in out and "## Conclusion" in out
    assert br.strip_body_faq("# T\n\n## FAQs\n\n### Q\n\nA") == "# T"
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd backend && uv run pytest tests/test_blog_rules.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `services/blog_rules.py`**

```python
"""Pure blog-profile rules: word counts, clamps, URL shape, frontmatter and export
validation. No DB, no network, so every rule is unit-testable and shared by
generation, refine, linking and export."""

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from ..models.blog import BlogProfile

# An H2 whose text starts "FAQ"/"FAQs"/"Frequently asked…"/"Common questions".
BODY_FAQ_RE = re.compile(
    r"(?im)^##[ \t]+(?:faqs?\b|frequently[ \t]+asked|common[ \t]+questions)[^\n]*$"
)
_H2_RE = re.compile(r"(?m)^##[ \t]+")


def profile_of(brand: dict[str, Any] | None) -> BlogProfile | None:
    """The brand's parsed blog profile, or None (absent or unparseable → legacy)."""
    raw = (brand or {}).get("blog_profile")
    if not raw:
        return None
    try:
        return BlogProfile.model_validate(raw)
    except ValidationError:
        return None


def word_count(s: str | None) -> int:
    return len((s or "").split())


def trim_to_words(s: str, max_words: int) -> str:
    """At most `max_words` words, ending on the last sentence boundary that fits;
    a hard word cut when no boundary fits."""
    words = s.split()
    if len(words) <= max_words:
        return s
    cut = " ".join(words[:max_words])
    m = list(re.finditer(r"[.!?](?=\s|$)", cut))
    return cut[: m[-1].end()] if m else cut


def clamp_chars(s: str, limit: int) -> str:
    """At most `limit` code points, cut at a word boundary when possible."""
    s = s.strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if s[limit] == " ":  # the cut already ends on a word boundary
        return cut.rstrip(" ,;:-")
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit // 2 else cut).rstrip(" ,;:-")


def with_trailing_slash(url: str) -> str:
    """Append '/' to the PATH (not the query/fragment); leave file-like paths alone."""
    p = urlsplit(url)
    path = p.path or "/"
    last = path.rsplit("/", 1)[-1]
    if not path.endswith("/") and "." not in last:
        path += "/"
    return urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))


def hub_url(domain: str | None, path: str, trailing_slash: bool) -> str:
    base = (domain or "").strip().rstrip("/")
    if base and "//" not in base:
        base = f"https://{base}"
    url = f"{base}{path}" if base else path
    return with_trailing_slash(url) if trailing_slash else url


def fallback_category(profile: BlogProfile, cluster_category: str | None) -> str:
    keys = [c.key for c in profile.categories]
    if cluster_category in keys:
        return cluster_category  # type: ignore[return-value]
    tech = [c.key for c in profile.categories if c.technical]
    return (tech or keys)[0]


def _clean_faq(raw: Any) -> list[dict[str, str]]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or item.get("question") or "").strip()
        a = str(item.get("a") or item.get("answer") or "").strip()
        if q and a:
            out.append({"q": q, "a": a})
    return out


def validate_frontmatter(
    raw: dict[str, Any], profile: BlogProfile, *, cluster_category: str | None
) -> tuple[dict[str, Any], list[str]]:
    """Coerce model output into valid frontmatter. Returns (clean, flags); a flag is
    a human-readable problem that still blocks export."""
    flags: list[str] = []
    keys = {c.key for c in profile.categories}
    if cluster_category in keys:
        category = cluster_category
    elif raw.get("category") in keys:
        category = raw["category"]
    else:
        category = fallback_category(profile, None)

    summary = None
    if profile.summary.enabled:
        summary = " ".join(str(raw.get("summary") or "").split())
        n = word_count(summary)
        if n > profile.summary.max_words:
            summary = trim_to_words(summary, profile.summary.max_words)
            flags.append("summary trimmed to fit; review it")
            n = word_count(summary)
        if n < profile.summary.min_words:
            flags.append(
                f"summary is {n} words (needs {profile.summary.min_words}-"
                f"{profile.summary.max_words})"
            )

    faq = None
    if profile.faq.enabled:
        faq = _clean_faq(raw.get("faq"))[: profile.faq.max]
        if len(faq) < profile.faq.min:
            flags.append(
                f"faq has {len(faq)} item(s) (needs {profile.faq.min}-{profile.faq.max})"
            )
    return {"category": category, "summary": summary, "faq": faq}, flags


def export_issues(article: dict[str, Any], profile: BlogProfile) -> list[str]:
    """Everything that would fail the target blog's build. Empty = exportable."""
    issues: list[str] = []
    keys = {c.key for c in profile.categories}
    cat = article.get("category")
    if not cat:
        issues.append("category is missing")
    elif cat not in keys:
        issues.append(f'unknown category "{cat}"')
    title = article.get("title") or ""
    shown = article.get("meta_title") if len(title) > profile.meta.title_max else None
    effective = shown or title
    if len(effective) > profile.meta.title_max:
        issues.append(
            f"title is {len(effective)} characters (max {profile.meta.title_max}); "
            "set a shorter meta title"
        )
    desc = article.get("meta_description") or ""
    if len(desc) > profile.meta.description_max:
        issues.append(
            f"description is {len(desc)} characters (max {profile.meta.description_max})"
        )
    if profile.summary.enabled:
        n = word_count(article.get("summary"))
        if not (profile.summary.min_words <= n <= profile.summary.max_words):
            issues.append(
                f"summary is {n} words (needs {profile.summary.min_words}-"
                f"{profile.summary.max_words})"
            )
    if profile.faq.enabled:
        n = len(_clean_faq(article.get("faq")))
        if not (profile.faq.min <= n <= profile.faq.max):
            issues.append(f"faq has {n} item(s) (needs {profile.faq.min}-{profile.faq.max})")
        if BODY_FAQ_RE.search(article.get("content_md") or ""):
            issues.append("remove the FAQ section in the body (the FAQ is frontmatter)")
    return issues


def strip_body_faq(md: str) -> str:
    """Drop an FAQ H2 section (heading through the line before the next H2)."""
    m = BODY_FAQ_RE.search(md)
    if not m:
        return md
    nxt = _H2_RE.search(md, m.end())
    end = nxt.start() if nxt else len(md)
    return (md[: m.start()].rstrip() + "\n\n" + md[end:].lstrip()).strip()
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_blog_rules.py -q`
Expected: PASS. If `test_clamp_chars_word_boundary_and_codepoints` fails on the `é` case (no spaces → hard cut), check that the hard cut keeps exactly 60 characters.

- [ ] **Step 5: Commit**

```bash
git add backend/src/rankforge_backend/services/blog_rules.py backend/tests/test_blog_rules.py
git commit -m "feat(rankforge): pure blog_rules — frontmatter/export validation, URL shape"
```

---

### Task 3: Scorer — sentence splitter, profile limits, internal_links signal

**Files:**
- Modify: `backend/src/rankforge_backend/services/scoring.py:78-79` (`_sentences`), `:141-280` (`score_seo`), `:615-657` (`score_and_store`)
- Test: `backend/tests/test_scoring.py` (append)

**Interfaces:**
- Consumes: `blog_rules.profile_of`, `linking.canonical_url` (unchanged in this task).
- Produces: `score_seo(content_md, title, meta, brief, competitor_hosts=None, *, profile: BlogProfile | None = None, internal_hosts: set[str] | None = None) -> dict`. With a profile, it adds a signal with key `internal_links`.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_scoring.py`)

```python
from rankforge_backend.models.blog import BlogProfile  # noqa: E402

_P = BlogProfile.model_validate({
    "categories": [{"key": "rag", "label": "R"}],
    "meta": {"title_max": 50, "description_max": 150},
    "links": {"min": 3, "max": 5},
})


def test_sentences_split_on_lines():
    md = "| A | B |\n|---|---|\n| one two | three |\n\n- item one\n- item two\n\n## Head"
    sents = scoring._sentences(scoring._clean(md))
    assert len(sents) >= 5  # header row, data row, 2 items, heading


def test_table_heavy_article_keeps_rhythm_score():
    rows = "\n".join(f"| Feature {i} | Supported in plan {i} |" for i in range(30))
    md = (
        "# T\n\nShort intro. Another line here.\n\n| F | P |\n|---|---|\n"
        f"{rows}\n\nA closing sentence that is fine."
    )
    r = scoring.score_readability(md, None)
    rhythm = next(s for s in r["signals"] if s["key"] == "rhythm")
    assert rhythm["score"] >= 60


def test_seo_title_band_uses_profile_limit():
    title = "x" * 55  # fits 60 default, exceeds profile 50
    base = scoring.score_seo("# T\n\nbody", title, "m" * 130, {})
    prof = scoring.score_seo("# T\n\nbody", title, "m" * 130, {}, profile=_P)
    tl = lambda s: next(x for x in s["signals"] if x["key"] == "title_length")  # noqa: E731
    assert tl(base)["score"] == 100 and tl(prof)["score"] < 100


def test_internal_links_signal_only_with_profile():
    md = (
        "# T\n\n[a](https://powabase.ai/blog/a/) [b](https://powabase.ai/vector-database/)"
        " [c](https://www.powabase.ai/blog/c/) [ext](https://example.com/x)"
    )
    none = scoring.score_seo(md, "t", "m", {})
    assert not any(s["key"] == "internal_links" for s in none["signals"])
    s = scoring.score_seo(md, "t", "m", {}, profile=_P, internal_hosts={"powabase.ai"})
    il = next(x for x in s["signals"] if x["key"] == "internal_links")
    assert il["score"] == 100 and "3 internal" in il["explanation"]
    one = scoring.score_seo(
        "# T\n\n[a](https://powabase.ai/blog/a/)", "t", "m", {},
        profile=_P, internal_hosts={"powabase.ai"},
    )
    il1 = next(x for x in one["signals"] if x["key"] == "internal_links")
    assert il1["score"] < 100 and il1["fixes"]
```

(`rhythm` is the "Sentence-length variety" signal, `scoring.py:437`.)

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd backend && uv run pytest tests/test_scoring.py -q`
Expected: FAIL. The split count and the table-rhythm test fail, and `score_seo` rejects the `profile` kwarg.

- [ ] **Step 3: Implement**

Replace `_sentences`:

```python
def _sentences(text: str) -> list[str]:
    # Newlines are boundaries too: after _clean, a table row, list item or heading
    # is its own line with no terminal punctuation, and without this a comparison
    # table fused into one giant "sentence" and tanked the rhythm/Flesch signals.
    # (LLM markdown doesn't hard-wrap prose, so a newline inside a paragraph is rare.)
    return [s for s in re.split(r"[.!?]+|\n", text) if s.strip()]
```

Extend `score_seo`'s signature:

```python
def score_seo(
    content_md: str,
    title: str,
    meta: str | None,
    brief: dict,
    competitor_hosts: set[str] | None = None,
    *,
    profile: "BlogProfile | None" = None,
    internal_hosts: set[str] | None = None,
) -> dict:
```

Add `from ..models.blog import BlogProfile` at the top of `scoring.py`. It's a models import, so there's no cycle. Then drop the quotes on the annotation.

Right after `meta = meta or ""`, add:

```python
    title_max = profile.meta.title_max if profile else 60
    desc_max = profile.meta.description_max if profile else 160
```

In the `title_length` signal, replace `60` with `title_max` in the `_band` call, the condition and the fix text (f-string `f"Target 30–{title_max} characters."`). In `meta_length`, replace `160` with `desc_max` the same way, and change the lower bound to `min(120, desc_max - 20)`.

Before the `fl = _flesch(text)` line, add:

```python
    if profile is not None:
        hosts = {h.removeprefix("www.") for h in (internal_hosts or set())}
        n_int = sum(1 for u in links if _link_host(u) in hosts)
        lo, hi = profile.links.min, profile.links.max
        sig.append(_signal(
            "internal_links", "Internal links", _band(n_int, lo, hi, 3), 0.06,
            f"{n_int} internal link(s); target {lo}-{hi}.",
            [f"Add {lo - n_int} more contextual link(s) to the brand's own articles "
             "or hub pages."] if n_int < lo else
            [f"Cut to at most {hi} internal links."] if n_int > hi else []))
```

In `score_and_store`, replace the `seo = score_seo(...)` call:

```python
    from . import blog_rules

    profile = blog_rules.profile_of(brand)
    dom = linking._bare_host((brand or {}).get("domain") or "")
    seo = score_seo(md, article.get("meta_title") or article.get("title") or "",
                    article.get("meta_description"), brief,
                    competitor_hosts=linking.competitor_hosts(brand),
                    profile=profile, internal_hosts={dom} if dom else set())
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest -q`
Expected: all pass. If an existing readability test asserted an exact sentence count or Flesch value on multi-line text, update the expectation and note it in the commit body. The new split is the intended behavior.

- [ ] **Step 5: Commit**

```bash
git add backend/src/rankforge_backend/services/scoring.py backend/tests/test_scoring.py
git commit -m "fix(rankforge): scorer treats lines as sentence boundaries; profile meta limits + internal_links signal"
```

---

### Task 4: Linking — trailing slash, hub pages, candidate pool, minimum gaps

**Files:**
- Modify: `backend/src/rankforge_backend/services/linking.py`
- Test: `backend/tests/test_linking.py` (append)

**Interfaces:**
- Consumes: `blog_rules.profile_of`, `with_trailing_slash`, `hub_url`.
- Produces:
  - `canonical_url(brand, article)`, now slash-normalized for profile brands.
  - `hub_targets(brand) -> list[dict]`, where each dict has `{"id": None, "hub": True, "title", "url", "topics"}`.
  - `link_candidates(db, brand, article, brief: dict | None, limit=8) -> list[dict]`, where each dict has `{"title", "target": "rf:article/<id>" | "<hub url>", "category": str | None}`.
  - `max_links(brand) -> int`.
  - `_TARGET_COLS` now includes `category`.
  - `suggest_links` stages hub mentions and minimum-link gaps.
  - `apply_suggestion` and `generate_gap_link` accept hub suggestions.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_linking.py`; reuse that file's existing `MagicMock` db pattern)

```python
from unittest.mock import MagicMock  # noqa: E402

from rankforge_backend.models.blog import BlogProfile  # noqa: E402
from rankforge_backend.services import linking as lk  # noqa: E402

_PROF = BlogProfile.model_validate({
    "categories": [
        {"key": "rag", "label": "R", "technical": True},
        {"key": "agents", "label": "A", "technical": True},
        {"key": "enterprise", "label": "E", "technical": False},
    ],
    "links": {"min": 3, "max": 5, "hub_pages": [
        {"path": "/vector-database/", "title": "Vector DB",
         "topics": ["vector database", "pgvector"]},
        {"path": "/free-mvp/", "title": "Free MVP", "topics": ["free mvp"]},
    ]},
}).model_dump()
BRAND = {"id": "b", "domain": "powabase.ai",
         "url_pattern": "https://powabase.ai/blog/{slug}", "blog_profile": _PROF}


def test_canonical_url_slash_only_with_profile():
    art = {"slug": "a"}
    assert lk.canonical_url(BRAND, art) == "https://powabase.ai/blog/a/"
    legacy = {**BRAND, "blog_profile": None}
    assert lk.canonical_url(legacy, art) == "https://powabase.ai/blog/a"


def test_hub_targets():
    hubs = lk.hub_targets(BRAND)
    assert [h["url"] for h in hubs] == [
        "https://powabase.ai/vector-database/", "https://powabase.ai/free-mvp/",
    ]
    assert lk.hub_targets({**BRAND, "blog_profile": None}) == []


def _row(i, cat, kw):
    return {"id": f"00000000-0000-0000-0000-00000000000{i}", "title": f"T{i}",
            "slug": f"t{i}", "keywords": kw, "canonical_url": None, "category": cat}


def test_link_candidates_order_and_caps():
    db = MagicMock()
    art = {"id": "00000000-0000-0000-0000-0000000000aa", "business_id": "b",
           "cluster_id": None, "cluster_role": None}
    db.fetch_all.return_value = [
        _row(1, "rag", ["pgvector index"]), _row(2, "rag", ["pgvector tuning"]),
        _row(3, "rag", ["pgvector hnsw"]), _row(4, "agents", ["pgvector agents"]),
        _row(5, "enterprise", ["pgvector buying"]),
    ]
    brief = {"primary_keyword": "pgvector", "secondary_keywords": []}
    c = lk.link_candidates(db, BRAND, art, brief)
    targets = [x["target"] for x in c]
    assert targets[0] == "https://powabase.ai/vector-database/"  # hub topic match
    rag = [x for x in c if x.get("category") == "rag"]
    assert len(rag) == 2  # per-category cap
    assert not any(x.get("category") == "enterprise" for x in c)  # non-technical
    assert any(t.startswith("rf:article/") for t in targets)


def test_max_links():
    assert lk.max_links(BRAND) == 5
    assert lk.max_links({**BRAND, "blog_profile": None}) == lk._MAX_PER_ARTICLE
```

Also add these two DB-mocked `suggest_links` tests, following the fixture style this test file already uses for `suggest_links`:
- `test_suggest_links_stages_hub_mention`: the body contains "pgvector". Assert that an `insert into public.link_suggestions` call has `target_article_id` None and `target_url` = the hub URL.
- `test_suggest_links_stages_min_gaps`: the body has no anchors and there are 2 published technical articles. With `links.min=3`, assert gap inserts (anchor None) are staged for those candidates.

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd backend && uv run pytest tests/test_linking.py -q`
Expected: FAIL (`hub_targets` and the others are undefined; canonical_url has no slash).

- [ ] **Step 3: Implement in `linking.py`**

Add `from . import blog_rules` to the imports.

`_TARGET_COLS = "id, title, slug, keywords, canonical_url, category"`

Replace the end of `canonical_url`:

```python
    pattern = (brand or {}).get("url_pattern")
    url = _render_pattern(pattern, article) if pattern else None
    prof = blog_rules.profile_of(brand)
    if url and prof and prof.links.trailing_slash:
        url = blog_rules.with_trailing_slash(url)
    return url
```

Add after `canonical_url`:

```python
def max_links(brand: dict[str, Any] | None) -> int:
    prof = blog_rules.profile_of(brand)
    return prof.links.max if prof else _MAX_PER_ARTICLE


def hub_targets(brand: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The brand's hub pages as link targets (not articles: id is None)."""
    prof = blog_rules.profile_of(brand)
    if not prof:
        return []
    dom = (brand or {}).get("domain")
    return [
        {"id": None, "hub": True, "title": h.title, "topics": h.topics,
         "keywords": h.topics,
         "url": blog_rules.hub_url(dom, h.path, prof.links.trailing_slash)}
        for h in prof.links.hub_pages
    ]


def _overlap(terms: list[str], text: str) -> int:
    low = text.lower()
    return sum(1 for t in terms if t and t.lower() in low)


def link_candidates(
    db: Database, brand: dict[str, Any] | None, article: dict[str, Any],
    brief: dict[str, Any] | None, limit: int = 8,
) -> list[dict[str, Any]]:
    """Internal-link targets offered to the writer: structural first, then hub pages
    matching the brief, then published technical-category articles ranked by keyword
    overlap, at most 2 per category."""
    prof = blog_rules.profile_of(brand)
    if not prof:
        return []
    brief = brief or {}
    terms = [brief.get("primary_keyword") or "", *(brief.get("secondary_keywords") or [])]
    probe = " ".join([*terms, article.get("title") or "", brief.get("topic") or ""])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(title: str, target: str, category: str | None) -> None:
        if target not in seen and len(out) < limit:
            seen.add(target)
            out.append({"title": title, "target": target, "category": category})

    if article.get("id") is not None:
        for t, _kind in _structural_targets(db, article):
            _add(t.get("title") or "", link_ref(t["id"]), t.get("category"))
    for h in hub_targets(brand):
        if _overlap(h["topics"], probe) or _overlap(terms, " ".join(h["topics"])):
            _add(h["title"], h["url"], None)
    technical = {c.key for c in prof.categories if c.technical}
    rows = [
        r for r in _link_targets(db, article.get("business_id"), article.get("id"))
        if r.get("category") in technical
    ]
    rows.sort(
        key=lambda r: _overlap(terms, " ".join(map(str, r.get("keywords") or []))
                               + " " + (r.get("title") or "")),
        reverse=True,
    )
    per_cat: dict[str, int] = {}
    for r in rows:
        cat = r.get("category")
        if per_cat.get(cat, 0) >= 2:
            continue
        per_cat[cat] = per_cat.get(cat, 0) + 1
        _add(r.get("title") or "", link_ref(r["id"]), cat)
    return out
```

`_link_targets` must accept `exclude_id=None`. Change its SQL to `... and id is distinct from %s`.

In `_structural_targets`, replace `members[:_MAX_PER_ARTICLE]` with `members[:3]`. The structural member list is a small seed; the cap is applied in `_consider`.

`_insert_suggestion`: the supersede-delete must handle hubs. Replace its body's first block:

```python
    tid = target.get("id")
    if anchor is not None:
        if tid is not None:
            db.execute(
                "delete from public.link_suggestions where article_id = %s "
                "and target_article_id = %s and anchor_text is null "
                "and status = 'pending'",
                (article_id, tid),
            )
        else:
            db.execute(
                "delete from public.link_suggestions where article_id = %s "
                "and target_article_id is null and target_url = %s "
                "and anchor_text is null and status = 'pending'",
                (article_id, target_url),
            )
```

In the insert params, pass `tid` in place of `target["id"]`. `on conflict do nothing` already covers both unique indexes.

In `suggest_links`:
- Replace `_MAX_PER_ARTICLE` in `_consider` with `cap = max_links(brand)`, defined once after `brand` loads.
- `done` must key by `target["id"] or target["url"]`.
- `_consider` must use `target.get("url") or canonical_url(brand, target)` for `target_url`.
- After the mention loop, append:

```python
    # 3) Hub pages: verbatim mentions of a hub topic (profile brands only).
    for hub in hub_targets(brand):
        _consider(hub, _MENTION)
    # 4) Minimum: if the body still has fewer internal links than the profile asks,
    # stage GAPS (LLM-filled on accept) toward the best remaining candidates.
    prof = blog_rules.profile_of(brand)
    if prof:
        have = len(_LINK_REF_RE.findall(md)) + sum(
            1 for h in hub_targets(brand) if h["url"] in md
        )
        need = prof.links.min - have - len([r for r in out if r.get("anchor_text")])
        brief = gen_svc.get_brief(db, art["brief_id"]) if art.get("brief_id") else {}
        for c in link_candidates(db, brand, art, brief or {}):
            if need <= 0 or len(out) >= cap:
                break
            key = c["target"]
            if key in {r.get("target_url") for r in out} or key in md:
                continue
            is_hub = not key.startswith("rf:article/")
            tgt = (
                {"id": None, "title": c["title"], "url": key} if is_hub
                else {"id": key.removeprefix("rf:article/"), "title": c["title"]}
            )
            url = key if is_hub else canonical_url(brand, _published(db, tgt["id"]) or {})
            if not url:
                continue
            row = _insert_suggestion(
                db, business_id, article_id, tgt, None, url, _MENTION,
                f'Below the {prof.links.min}-link minimum — add a contextual link to '
                f'"{c["title"]}".',
            )
            if row:
                out.append(row)
                need -= 1
```

`apply_suggestion`: replace the `target_article_id` guard and the ref line:

```python
    href = (
        link_ref(s["target_article_id"]) if s.get("target_article_id")
        else s["target_url"]  # hub page: a real URL, not an article ref
    )
    new_md = f"{md[:a]}[{span_text}]({href}){md[b:]}"
```

(Delete the `if not s.get("target_article_id"): return None` line.)

`generate_gap_link`: replace the ref swap with:

```python
    if s.get("target_article_id"):
        sentence = sentence.replace(s["target_url"], link_ref(s["target_article_id"]))
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest -q`
Expected: all pass. Existing linking tests use brands with no `blog_profile`, so the URLs and cap are unchanged. If a relink or linkcheck test builds rows without `category`, `.get` handles it.

- [ ] **Step 5: Commit**

```bash
git add backend/src/rankforge_backend/services/linking.py backend/tests/test_linking.py
git commit -m "feat(rankforge): hub pages, trailing slash, candidate pool and min-link gaps in the linker"
```

---

### Task 5: Frontmatter service + meta limits + generation wiring + writer prompt

**Files:**
- Create: `backend/src/rankforge_backend/services/frontmatter.py`
- Modify: `backend/src/rankforge_backend/services/revise.py:175-209` (`fix_meta`)
- Modify: `backend/src/rankforge_backend/services/generation.py` (`_SYSTEM_PROMPT` rule, `_draft_article`, `run_generation_task`)
- Modify: `backend/src/rankforge_backend/routes/articles.py` (new `POST /{id}/frontmatter`)
- Test: `backend/tests/test_frontmatter.py`, `backend/tests/test_generation.py` (append)

**Interfaces:**
- Consumes: `blog_rules.*`, `linking.link_candidates`, `revise.fix_meta`.
- Produces:
  - `frontmatter.generate(client, db, article_id) -> list[str]` (flags). It writes `category`, `summary`, `faq` and returns `[]` when there's no profile.
  - `frontmatter.enforce_meta(db, article_id, article, profile) -> None`, a deterministic clamp.
  - `frontmatter.complete(client, db, article_id) -> list[str]`, which runs `fix_meta` → `enforce_meta` → `generate`.
  - `fix_meta(..., *, title_max=60, description_max=160)`.
  - `generation._writer_rules(profile, brand_name) -> str`.
  - `generation._link_block(candidates, profile) -> str`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_frontmatter.py`:

```python
"""frontmatter — category/summary/FAQ generation, meta enforcement."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.services import frontmatter as fm

PROF = BlogProfile.model_validate({
    "categories": [{"key": "rag", "label": "R"}, {"key": "agents", "label": "A"}],
    "stance": "favor_brand",
}).model_dump()
BRAND = {"id": "b", "name": "Powabase", "blog_profile": PROF}
ART = {"id": "a", "business_id": "b", "title": "How to build RAG", "cluster_id": None,
       "content_md": "# T\n\nbody", "meta_title": None, "meta_description": "d"}
S45 = " ".join(["word"] * 44) + " end."
GOOD = {"category": "agents", "summary": S45,
        "faq": [{"q": f"Q{i}?", "a": "A."} for i in range(4)]}


def _client(*payloads):
    c = MagicMock()
    c.run_agent = AsyncMock(side_effect=[{"content": json.dumps(p)} for p in payloads])
    return c


@pytest.fixture
def deps():
    with patch.object(fm.gen_svc, "get_article", return_value=dict(ART)), \
         patch.object(fm.brands, "get_profile", return_value=BRAND), \
         patch.object(fm.gen_svc, "_update") as upd, \
         patch.object(fm, "ensure_agent", AsyncMock(return_value="agent")):
        yield upd


async def test_generate_writes_fields(deps):
    flags = await fm.generate(_client(GOOD), MagicMock(), "a")
    assert flags == []
    kw = deps.call_args.kwargs
    assert kw["category"] == "agents" and kw["summary"] == S45 and len(kw["faq"]) == 4


async def test_generate_retries_once_on_flags(deps):
    bad = {**GOOD, "summary": "too short"}
    c = _client(bad, GOOD)
    flags = await fm.generate(c, MagicMock(), "a")
    assert flags == [] and c.run_agent.await_count == 2
    assert "summary is 2 words" in c.run_agent.await_args_list[1].args[1]


async def test_generate_noop_without_profile(deps):
    with patch.object(fm.brands, "get_profile", return_value={"blog_profile": None}):
        c = _client()
        assert await fm.generate(c, MagicMock(), "a") == []
        c.run_agent.assert_not_called()


def test_enforce_meta_sets_meta_title_for_long_h1():
    with patch.object(fm.gen_svc, "_update") as upd:
        art = {**ART, "title": "x " * 40, "meta_title": "y" * 80,
               "meta_description": "z" * 200}
        fm.enforce_meta(MagicMock(), "a", art, BlogProfile.model_validate(PROF))
        kw = upd.call_args.kwargs
        assert len(kw["meta_title"]) <= 60 and len(kw["meta_description"]) <= 160


def test_enforce_meta_derives_meta_title_from_title_when_missing():
    with patch.object(fm.gen_svc, "_update") as upd:
        art = {**ART, "title": "word " * 20, "meta_title": None}
        fm.enforce_meta(MagicMock(), "a", art, BlogProfile.model_validate(PROF))
        assert 0 < len(upd.call_args.kwargs["meta_title"]) <= 60
```

`pyproject.toml` sets `asyncio_mode = "auto"`, so `async def` tests need no marker.

Append to `backend/tests/test_generation.py`:

```python
from rankforge_backend.models.blog import BlogProfile  # noqa: E402
from rankforge_backend.services import generation as _g  # noqa: E402

_BP = BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}],
                                  "stance": "favor_brand"})


def test_writer_rules_profile_blocks():
    r = _g._writer_rules(_BP, "Powabase")
    assert "Do not write an FAQ" in r
    assert "Never state a Powabase gap" in r
    assert _g._writer_rules(None, "Powabase") == ""


def test_outline_drops_faq_heading_with_profile():
    heads = ["H2: Intro", "H2: FAQ", "H3: Is it safe?", "H2: Conclusion"]
    assert _g._outline_for(heads, _BP) == ["H2: Intro", "H2: Conclusion"]
    assert _g._outline_for(heads, None) == heads


def test_link_block_lists_targets():
    c = [{"title": "Vector DB", "target": "https://powabase.ai/vector-database/",
          "category": None}]
    b = _g._link_block(c, _BP)
    assert "https://powabase.ai/vector-database/" in b and "3-5" in b
    assert _g._link_block([], _BP) == ""
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd backend && uv run pytest tests/test_frontmatter.py tests/test_generation.py -q`
Expected: FAIL (module and functions missing).

- [ ] **Step 3: Implement `services/frontmatter.py`**

```python
"""Frontmatter step: category, answer-first summary and FAQ for brands with a blog
profile, plus deterministic meta-length enforcement. Runs after the body is written
and on demand ("Generate summary & FAQ")."""

import logging
from typing import Any
from uuid import UUID

from ..db import Database
from ..models.blog import BlogProfile
from ..powabase import PowabaseClient
from ..util import extract_json
from . import blog_rules
from . import business_profiles as brands
from . import clusters as clusters_svc
from . import generation as gen_svc
from .agents import ensure_agent

log = logging.getLogger("rankforge.frontmatter")

AGENT_NAME = "rankforge-frontmatter"
MODEL = "claude-sonnet-4-6"
_SYSTEM = """\
You write the structured frontmatter a blog post needs for search and AI answer \
engines: a category, a short answer-first summary, and an FAQ. You return only JSON.
"""


def _prompt(article: dict, brand_name: str, profile: BlogProfile) -> str:
    cats = "\n".join(
        f"- {c.key}: {c.label} ({c.description})" for c in profile.categories
    )
    stance = (
        f"- Never state a {brand_name} gap, limitation or missing feature in the "
        "summary or FAQ. Position it as equal to or ahead of alternatives.\n"
        if profile.stance == "favor_brand" else ""
    )
    return (
        f"Write frontmatter for this {brand_name} blog post.\n\n"
        "## Categories (pick exactly one key)\n"
        f"{cats}\n\n"
        "## Rules\n"
        f"- summary: {profile.summary.min_words}-{profile.summary.max_words} words. "
        "Its first sentence directly answers the question the title asks. "
        f"Self-contained. Names {brand_name} where the post discusses it.\n"
        f"- faq: {profile.faq.min}-{profile.faq.max} items. Each q is phrased the way "
        "someone would search it; each a answers first, at most 60 words. At least "
        f'one q is "How does {brand_name} handle …?".\n'
        f"{stance}\n"
        "## Output\n"
        'Return ONLY {"category": str, "summary": str, "faq": [{"q": str, "a": str}]}\n\n'
        f"Title: {article.get('title') or ''}\n\n"
        f"---ARTICLE---\n{(article.get('content_md') or '')[:16000]}"
    )


async def _ask(client: PowabaseClient, msg: str) -> dict[str, Any]:
    agent_id = await ensure_agent(
        client, name=AGENT_NAME, model=MODEL, system_prompt=_SYSTEM,
        settings={"temperature": 0.2},
    )
    res = await client.run_agent(agent_id, msg)
    return extract_json(res.get("content") or "") or {}


def _cluster_category(db: Database, article: dict) -> str | None:
    cid = article.get("cluster_id")
    if not cid:
        return None
    cl = clusters_svc.get_cluster(db, cid)
    return (cl or {}).get("category")


async def generate(
    client: PowabaseClient, db: Database, article_id: UUID
) -> list[str]:
    """Write category/summary/FAQ. Returns flags that still block export; [] when the
    brand has no profile (nothing written)."""
    article = gen_svc.get_article(db, article_id)
    if not article or not article.get("business_id"):
        return []
    brand = brands.get_profile(db, article["business_id"])
    profile = blog_rules.profile_of(brand)
    if not profile:
        return []
    name = (brand or {}).get("name") or "the brand"
    cc = _cluster_category(db, article)
    msg = _prompt(article, name, profile)
    clean, flags = blog_rules.validate_frontmatter(
        await _ask(client, msg), profile, cluster_category=cc
    )
    if flags:
        retry = (
            f"{msg}\n\n## Your previous answer had these problems — fix them\n"
            + "\n".join(f"- {f}" for f in flags)
        )
        clean, flags = blog_rules.validate_frontmatter(
            await _ask(client, retry), profile, cluster_category=cc
        )
    gen_svc._update(db, article_id, **clean)
    return flags


def enforce_meta(
    db: Database, article_id: UUID, article: dict, profile: BlogProfile
) -> None:
    """Deterministic backstop: meta_title/description within the profile limits. A
    long H1 always gets a meta_title (derived from the title if the model gave none)."""
    tmax, dmax = profile.meta.title_max, profile.meta.description_max
    title = article.get("title") or ""
    mt = (article.get("meta_title") or "").strip()
    fields: dict[str, Any] = {}
    if len(title) > tmax or (mt and len(mt) > tmax):
        new_mt = blog_rules.clamp_chars(mt or title, tmax)
        if new_mt != mt:
            fields["meta_title"] = new_mt
    desc = article.get("meta_description") or ""
    if len(desc) > dmax:
        fields["meta_description"] = blog_rules.clamp_chars(desc, dmax)
    if fields:
        gen_svc._update(db, article_id, **fields)


async def complete(
    client: PowabaseClient, db: Database, article_id: UUID
) -> list[str]:
    """Meta (model, then clamp) + category/summary/FAQ. Used after generation and by
    the "Generate summary & FAQ" / "Fix automatically" actions."""
    from . import brief as brief_svc
    from . import revise

    article = gen_svc.get_article(db, article_id)
    if not article or not article.get("business_id"):
        return []
    profile = blog_rules.profile_of(brands.get_profile(db, article["business_id"]))
    if not profile:
        return []
    brief = (
        brief_svc.get_brief(db, article["brief_id"]) if article.get("brief_id") else {}
    ) or {}
    await revise.fix_meta(
        client, db, article_id, article, brief,
        title_max=profile.meta.title_max, description_max=profile.meta.description_max,
    )
    enforce_meta(db, article_id, gen_svc.get_article(db, article_id) or article, profile)
    return await generate(client, db, article_id)

```

`clusters_svc.get_cluster` must return `category`. Task 12 adds it to `_COLUMNS`. Until then `.get("category")` returns None, which is fine.

- [ ] **Step 4: `fix_meta` limits**

Change the signature to `async def fix_meta(client, db, article_id, article, brief, *, title_max: int = 60, description_max: int = 160) -> None:`. In the Requirements lines, use `f"- \`meta_title\`: at most {title_max} characters, …"` and `f"- \`meta_description\`: {min(120, description_max - 20)}–{description_max} characters, …"`. Behavior with the defaults is unchanged.

- [ ] **Step 5: Writer prompt + pipeline in `generation.py`**

Add these module-level helpers (after `_cluster_block`):

```python
def _writer_rules(profile: "BlogProfile | None", brand_name: str) -> str:
    """Blog-profile overrides appended to the per-article message."""
    if profile is None:
        return ""
    lines = ["\n\n## This blog's publishing rules (override anything above)"]
    if profile.faq.enabled:
        lines.append(
            "- Do not write an FAQ or Q&A section. The FAQ is generated separately "
            "and a body FAQ would appear twice on the page."
        )
    if profile.stance == "favor_brand":
        lines.append(
            f"- Position {brand_name} as equal to or ahead of the alternatives. Never "
            f"state a {brand_name} gap, limitation or missing feature. Where a "
            "competitor genuinely fits a different need, describe that need neutrally. "
            "(Still never hyperlink a competitor.)"
        )
    lines.append(
        "- Do not add a 'Related reading' or 'Further reading' section; the site adds "
        "one."
    )
    return "\n".join(lines)


_FAQ_HEADING_RE = re.compile(
    r"^h2:\s*(faqs?\b|frequently asked|common questions)", re.I
)


def _outline_for(headings: list[str], profile: "BlogProfile | None") -> list[str]:
    """Drop an FAQ H2 (and its H3s) from the outline when the FAQ is frontmatter."""
    if profile is None or not profile.faq.enabled:
        return headings
    out, skipping = [], False
    for h in headings:
        low = h.lower().lstrip()
        if low.startswith("h2"):
            skipping = bool(_FAQ_HEADING_RE.match(low))
        if not skipping:
            out.append(h)
    return out


def _link_block(candidates: list[dict[str, Any]], profile: "BlogProfile | None") -> str:
    if profile is None or not candidates:
        return ""
    rows = "\n".join(f'- "{c["title"]}": {c["target"]}' for c in candidates)
    return (
        "\n\n## Internal links you may use\n"
        f"- Place {profile.links.min}-{profile.links.max} of these as contextual links "
        "with natural in-sentence anchors, where they genuinely help the reader. Use "
        "the link target exactly as written.\n"
        f"{rows}"
    )
```

Add `from ..models.blog import BlogProfile` and `from . import blog_rules` to the imports, and drop the quotes on the annotations.

In `_draft_article`:
- Add keyword params `profile: BlogProfile | None = None` and `link_candidates: list[dict[str, Any]] | None = None`.
- Change `headings = brief.get("headings") or []` to `headings = _outline_for(brief.get("headings") or [], profile)`.
- In `msg`, after `f"{brand_block}"`, add `f"{_link_block(link_candidates or [], profile)}"` and `f"{_writer_rules(profile, (brand or {}).get('name') or 'the brand')}"`.

In `run_generation_task`, before the `_draft_article` call:

```python
        profile = blog_rules.profile_of(brand_profile)
        from . import linking as _lk

        candidates = (
            _lk.link_candidates(db, brand_profile, get_article(db, article_id) or {}, brief)
            if profile else []
        )
```

Pass `profile=profile, link_candidates=candidates` to `_draft_article`.

After the competitor-strip `_update(...)` that writes `content_md`, and before `quality.reflect`, add:

```python
        if profile:
            from . import frontmatter

            try:
                await frontmatter.complete(client, db, article_id)
            except Exception:  # noqa: BLE001 — never block generation; export checks it
                log.exception("frontmatter step failed for %s", article_id)
```

- [ ] **Step 6: On-demand endpoint in `routes/articles.py`**

```python
@router.post(
    "/{article_id}/frontmatter",
    response_model=Article,
    dependencies=[Depends(rate_limit("article:optimize"))],
)
async def generate_frontmatter(
    article_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Generate summary + FAQ + category (and fit meta) for a blog-profile brand."""
    article = _guard_article(db, article_id, user)
    if not blog_rules.profile_of(brands_svc.get_profile(db, article["business_id"])):
        raise HTTPException(status.HTTP_409_CONFLICT, "brand has no blog profile")
    await frontmatter_svc.complete(pb, db, article_id)
    return svc.get_article(db, article_id)
```

Imports: `from ..services import blog_rules`, `from ..services import business_profiles as brands_svc`, `from ..services import frontmatter as frontmatter_svc`.

Add a route test to `tests/test_frontmatter.py`: POST to a brand with no profile → 409. Use the `_client(db)` / `with_auth` pattern from `test_publishing.py`, and patch `brands_svc.get_profile` to return `{"blog_profile": None}`.

- [ ] **Step 7: Run the tests**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/src/rankforge_backend/services/frontmatter.py backend/src/rankforge_backend/services/revise.py backend/src/rankforge_backend/services/generation.py backend/src/rankforge_backend/routes/articles.py backend/tests/test_frontmatter.py backend/tests/test_generation.py
git commit -m "feat(rankforge): frontmatter step (category/summary/FAQ), meta limits, profile-aware writer"
```

---

### Task 6: GEO — FAQPage JSON-LD from stored FAQ

**Files:**
- Modify: `backend/src/rankforge_backend/services/geo_optimize.py:135-168`
- Test: `backend/tests/test_geo_optimize.py` (append)

**Interfaces:**
- Consumes: `blog_rules.profile_of`; article `faq`.
- Produces: `faq_jsonld_from_items(items: list[dict]) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from rankforge_backend.models.blog import BlogProfile  # noqa: E402
from rankforge_backend.services import geo_optimize as geo  # noqa: E402

_PROF = BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}]})


def test_faq_jsonld_from_items():
    d = geo.faq_jsonld_from_items([{"q": "Q?", "a": "A."}, {"q": "", "a": "x"}])
    assert d["@type"] == "FAQPage" and len(d["mainEntity"]) == 1
    assert geo.faq_jsonld_from_items([]) is None


async def test_optimize_uses_stored_faq_with_profile():
    art = {"id": "a", "business_id": "b", "title": "T", "content_md": "# T\n\nx",
           "faq": [{"q": "Q?", "a": "A."}], "meta_description": "d"}
    with patch.object(geo.gen_svc, "get_article", return_value=art), \
         patch.object(geo, "_brand", return_value={"blog_profile": _PROF.model_dump()}), \
         patch.object(geo, "build_faq_jsonld", AsyncMock()) as extract, \
         patch.object(geo.gen_svc, "_update") as upd:
        await geo.optimize_and_store(MagicMock(), MagicMock(), "a")
        extract.assert_not_called()
        graph = upd.call_args.kwargs["json_ld"]["@graph"]
        assert any(n.get("@type") == "FAQPage" for n in graph)


async def test_optimize_extracts_when_faq_disabled():
    off = _PROF.model_copy(update={"faq": _PROF.faq.model_copy(update={"enabled": False})})
    art = {"id": "a", "business_id": "b", "title": "T", "content_md": "# T\n\nx",
           "faq": None, "meta_description": "d"}
    with patch.object(geo.gen_svc, "get_article", return_value=art), \
         patch.object(geo, "_brand", return_value={"blog_profile": off.model_dump()}), \
         patch.object(geo, "build_faq_jsonld", AsyncMock(return_value=None)) as extract, \
         patch.object(geo.gen_svc, "_update"):
        await geo.optimize_and_store(MagicMock(), MagicMock(), "a")
        extract.assert_awaited_once()
```

Before writing these, read `geo_optimize.py:135-168` to see how `optimize_and_store` builds the graph and whether it stores `{"@context", "@graph"}`. Adjust the `@graph` access to match what it actually writes.

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd backend && uv run pytest tests/test_geo_optimize.py -q`
Expected: FAIL (`faq_jsonld_from_items` and `_brand` are undefined).

- [ ] **Step 3: Implement**

Add:

```python
def faq_jsonld_from_items(items: list[dict] | None) -> dict[str, Any] | None:
    entities = [
        {"@type": "Question", "name": i["q"],
         "acceptedAnswer": {"@type": "Answer", "text": i["a"]}}
        for i in (items or []) if isinstance(i, dict) and i.get("q") and i.get("a")
    ]
    return {"@type": "FAQPage", "mainEntity": entities} if entities else None


def _brand(db: Database, article: dict) -> dict | None:
    bid = article.get("business_id")
    return brands.get_profile(db, bid) if bid else None
```

In `optimize_and_store`, replace `faq = await build_faq_jsonld(client, content_md)` with:

```python
    from . import blog_rules

    prof = blog_rules.profile_of(_brand(db, article))
    if prof and prof.faq.enabled:
        faq = faq_jsonld_from_items(article.get("faq"))
    else:
        faq = await build_faq_jsonld(client, content_md)
```

- [ ] **Step 4: Run the tests** — `cd backend && uv run pytest -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/rankforge_backend/services/geo_optimize.py backend/tests/test_geo_optimize.py
git commit -m "feat(rankforge): FAQPage JSON-LD from frontmatter FAQ for blog-profile brands"
```

---

### Task 7: Export — profile frontmatter + pre-export check

**Files:**
- Modify: `backend/src/rankforge_backend/services/publishing.py:118-148` (`render_markdown`), `:430-474` (`export`), `publish`
- Modify: `backend/src/rankforge_backend/routes/publish.py`
- Test: `backend/tests/test_publishing.py` (append)

**Interfaces:**
- Consumes: `blog_rules.profile_of`, `export_issues`.
- Produces:
  - `render_markdown(article, profile: BlogProfile | None = None) -> str`.
  - `class ExportBlocked(Exception)` with an `issues: list[str]` attribute. `export()` and `publish()` raise it; routes map it to **422 `{"detail": {"export_issues": [...]}}`**.

- [ ] **Step 1: Write the failing tests**

```python
from rankforge_backend.models.blog import BlogProfile  # noqa: E402

_BP = BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}]})
_S = " ".join(["word"] * 44) + " end."
_FAQ = [{"q": f"Q{i}?", "a": "A."} for i in range(3)]


def _pa(**over):
    return {**ARTICLE, "category": "rag", "summary": _S, "faq": _FAQ,
            "status": "approved", **over}


def test_render_markdown_profile_fields_in_order():
    out = svc.render_markdown(_pa(), _BP)
    fm = out.split("---")[1]
    keys = ["title:", "description:", "category:", "summary:", "faq:", "draft:"]
    positions = [fm.index(k) for k in keys]
    assert positions == sorted(positions)
    assert '  - q: "Q0?"\n    a: "A."' in fm
    assert "metaTitle" not in fm  # short title


def test_render_markdown_meta_title_only_when_title_too_long():
    out = svc.render_markdown(_pa(title="x" * 70, meta_title="Short one"), _BP)
    assert 'metaTitle: "Short one"' in out
    out = svc.render_markdown(_pa(title="Fits", meta_title="Other"), _BP)
    assert "metaTitle" not in out


def test_render_markdown_profile_yaml_safety():
    tricky = 'Line: "quoted" # not a comment\nnext — ünïcode'
    out = svc.render_markdown(
        _pa(summary=tricky, faq=[{"q": 'What: "x"?', "a": "a # b"}] * 3), _BP
    )
    import json as _j

    assert f"summary: {_j.dumps(tricky)}" in out
    assert "\\n" in out.split("summary:")[1].split("\n")[0]  # newline escaped


def test_render_markdown_faq_disabled():
    off = _BP.model_copy(update={"faq": _BP.faq.model_copy(update={"enabled": False})})
    out = svc.render_markdown(_pa(), off)
    assert "faq:" not in out and "summary:" in out


def test_render_markdown_no_profile_unchanged():
    assert svc.render_markdown(_pa()) == svc.render_markdown(_pa(), None)
    assert "category:" not in svc.render_markdown(_pa())


def test_export_blocked_on_issues(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(svc.gen_svc, "get_article",
                        lambda _db, _id: {**_pa(category=None), "business_id": BID})
    from rankforge_backend.services import business_profiles as bp

    monkeypatch.setattr(bp, "get_profile",
                        lambda _db, _id: {"name": "B", "blog_profile": _BP.model_dump()})
    db.fetch_one.return_value = {"keywords": []}
    with pytest.raises(svc.ExportBlocked) as e:
        svc.export(db, AID, "markdown")
    assert any("category" in i for i in e.value.issues)
```

Add a route test: GET `/api/articles/{AID}/export` with `svc.export` patched to raise `ExportBlocked(["x"])` → status 422 and `r.json()["detail"]["export_issues"] == ["x"]`. Do the same for POST `/publish`.

- [ ] **Step 2: Run the tests to confirm they fail** — `cd backend && uv run pytest tests/test_publishing.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement**

In `publishing.py`, import `from ..models.blog import BlogProfile` and `from . import blog_rules`, then add:

```python
class ExportBlocked(Exception):
    """The article would fail the target blog's build — carries the issue list."""

    def __init__(self, issues: list[str]):
        super().__init__("; ".join(issues))
        self.issues = issues
```

Rewrite `render_markdown(article, profile=None)`. It keeps the legacy block exactly when `profile is None`. With a profile:

```python
    fm = ["---", f"title: {json.dumps(article.get('title') or '')}"]
    if profile is not None:
        title = article.get("title") or ""
        mt = (article.get("meta_title") or "").strip()
        if len(title) > profile.meta.title_max and mt and mt != title:
            fm.append(f"metaTitle: {json.dumps(mt)}")
    fm.append(f"description: {json.dumps(article.get('meta_description') or '')}")
    if profile is not None and article.get("category"):
        fm.append(f"category: {article['category']}")
    # … publishedDate / author / tags exactly as today …
    if profile is not None:
        if profile.summary.enabled and article.get("summary"):
            fm.append(f"summary: {json.dumps(article['summary'])}")
        if profile.faq.enabled and article.get("faq"):
            fm.append("faq:")
            for it in article["faq"]:
                fm.append(f"  - q: {json.dumps(it['q'])}")
                fm.append(f"    a: {json.dumps(it['a'])}")
    # … draft + closing '---' + body exactly as today …
```

Keep `category`'s position after `description` and before `publishedDate`, which matches the spec's order. The `test_render_markdown_profile_fields_in_order` test pins it.

The body: with a profile that has FAQ enabled, run `blog_rules.strip_body_faq` on the body before emitting. That's a belt-and-braces measure; the export check blocks earlier anyway.

In `export()`:
- After building the enriched `article`, set `profile = blog_rules.profile_of(brand)`.
- If `profile` is set and `fmt == "markdown"`, compute `issues = blog_rules.export_issues(article, profile)` and raise `ExportBlocked(issues)` if there are any.
- Call `render_markdown(article, profile)`.

In `publish()`, after computing `brand`:

```python
    profile = blog_rules.profile_of(brand)
    if profile and (issues := blog_rules.export_issues(article, profile)):
        raise ExportBlocked(issues)
```

Also add `"category"`, `"summary"` and `"faq"` to the webhook `payload`.

In `routes/publish.py`, wrap both `svc.export(...)` and `await svc.publish(...)`:

```python
    try:
        result = svc.export(db, article_id, format)
    except svc.ExportBlocked as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, {"export_issues": e.issues}
        ) from e
```

- [ ] **Step 4: Run the tests** — `cd backend && uv run pytest -q`. Expected: all pass. `test_render_markdown_frontmatter_shape` and the other existing tests are unchanged because no profile is passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/rankforge_backend/services/publishing.py backend/src/rankforge_backend/routes/publish.py backend/tests/test_publishing.py
git commit -m "feat(rankforge): blog-profile .mdx frontmatter + 422 pre-export check"
```

---

### Task 8: Cluster category

**Files:**
- Modify: `backend/src/rankforge_backend/models/clusters.py` (`ContentCluster`, `ClusterUpdate`)
- Modify: `backend/src/rankforge_backend/services/clusters.py:33` (`_COLUMNS`), `:267-300` (`update_cluster`)
- Modify: `backend/src/rankforge_backend/routes/clusters.py:111-127`
- Test: `backend/tests/test_clusters.py` (append)

**Interfaces:**
- Produces: `update_cluster(..., label=None, theme=None, category: str | None | object = _UNSET)`. `ContentCluster.category: str | None`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_update_cluster_category_only_skips_reindex(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from rankforge_backend.services import clusters as cs

    db = MagicMock()
    cur = {"id": "c", "label": "L", "theme": "T", "category": None}
    monkeypatch.setattr(cs, "get_cluster", lambda _db, _id: cur)
    db.fetch_one.return_value = {**cur, "category": "rag"}
    reindex = AsyncMock()
    monkeypatch.setattr(cs, "_reindex_cluster", reindex, raising=False)
    row = await cs.update_cluster(MagicMock(), db, "c", category="rag")
    assert row["category"] == "rag"
    sql = db.fetch_one.call_args.args[0]
    assert "category = %s" in sql
    reindex.assert_not_called()

```

Add a route test, `test_patch_cluster_rejects_unknown_category`, using the client/`with_auth` helper already in `test_clusters.py`:
- Patch `routes.clusters._guard_cluster` to return `{"id": "c", "business_id": BID}`.
- Patch `business_profiles.get_profile` to return `{"blog_profile": {"categories": [{"key": "rag", "label": "R"}]}}`.
- PATCH `/api/clusters/c` with `{"category": "nope"}`. Assert status 422 and `"unknown category"` in `r.json()["detail"]`.
- Assert `services.clusters.update_cluster` was not awaited.

Before writing the first test, read `services/clusters.py:267-330` for the real name of the re-index step, and replace `_reindex_cluster` with it.

- [ ] **Step 2: Run the tests to confirm they fail.**

Run: `cd backend && uv run pytest tests/test_clusters.py -q`

- [ ] **Step 3: Implement**

- `models/clusters.py`:
  - add `category: str | None = None` to `ContentCluster`;
  - add `category: str | None = Field(default=None, max_length=60)` to `ClusterUpdate`.
- `services/clusters.py`: add `category` to `_COLUMNS`. In `update_cluster`:
  - add the keyword `category: Any = _UNSET` with a module-level `_UNSET = object()`;
  - compute `new_cat = current.get("category") if category is _UNSET else category`;
  - include `category = %s` in the UPDATE;
  - if only the category changed (label and theme equal), write it and **return before the re-index block**;
  - update the early-return condition so a category change isn't skipped.
- `routes/clusters.py`: pass `category=payload.category if "category" in payload.model_fields_set else _UNSET`. Before calling, when a category is set, load the brand (`_guard_cluster` returns the cluster, and its `business_id` gives the brand). Validate with `blog_rules.profile_of(brand)`: no profile, or a key not in its categories → 422 `"unknown category"`.

- [ ] **Step 4: Run the tests** — `cd backend && uv run pytest -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/rankforge_backend/models/clusters.py backend/src/rankforge_backend/services/clusters.py backend/src/rankforge_backend/routes/clusters.py backend/tests/test_clusters.py
git commit -m "feat(rankforge): per-cluster blog category"
```

---

### Task 9: Frontmatter-aware versions + revert endpoint

**Files:**
- Modify: `backend/src/rankforge_backend/services/generation.py:767-822` (`list_versions`, `restore_version`, `update_article`)
- Modify: `backend/src/rankforge_backend/routes/articles.py`
- Test: `backend/tests/test_revert.py`

**Interfaces:**
- Produces:
  - `FRONTMATTER_FIELDS = ("title", "meta_title", "meta_description", "category", "summary", "faq")`.
  - `snapshot_version(db, article) -> None`: inserts `content_md` plus a `frontmatter` jsonb.
  - `update_article` snapshots when `content_md` **or any frontmatter field** changes, and wraps `faq` in `Json`.
  - `restore_version` restores frontmatter when the version has it.
  - `revert_last(db, article_id) -> dict | None`.
  - Route `POST /api/articles/{id}/revert`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_revert.py`:

```python
"""Versions carry frontmatter; revert restores the latest differing version."""

from unittest.mock import MagicMock

from psycopg.types.json import Json

from rankforge_backend.services import generation as g

CUR = {"id": "a", "content_md": "# T\n\nnew", "title": "T", "meta_title": None,
       "meta_description": "d", "category": "rag", "summary": "s", "faq": None}


def test_update_article_snapshots_frontmatter_change(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    g.update_article(db, "a", {"summary": "changed"})
    ins = db.execute.call_args_list[0].args
    assert "insert into public.article_versions" in ins[0]
    assert "frontmatter" in ins[0]


def test_update_article_wraps_faq_json(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    g.update_article(db, "a", {"faq": [{"q": "Q", "a": "A"}]})
    params = db.fetch_one.call_args.args[1]
    assert any(isinstance(p, Json) for p in params)


def test_revert_restores_latest_differing(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    db.fetch_all.return_value = [
        {"id": "v2", "content_md": "# T\n\nnew", "frontmatter": None},  # same body
        {"id": "v1", "content_md": "# T\n\nold",
         "frontmatter": {"summary": "old s", "faq": [{"q": "Q", "a": "A"}]}},
    ]
    called = {}
    monkeypatch.setattr(g, "update_article",
                        lambda _db, _id, f: called.setdefault("f", f) or CUR)
    g.revert_last(db, "a")
    assert called["f"]["content_md"] == "# T\n\nold"
    assert called["f"]["summary"] == "old s"


def test_revert_404_when_no_differing_version(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    db.fetch_all.return_value = [{"id": "v", "content_md": CUR["content_md"],
                                  "frontmatter": None}]
    assert g.revert_last(db, "a") is None
```

Add route tests using the `test_publishing.py` client pattern:
- `POST /revert` → 404 when `revert_last` returns None.
- `POST /revert` → 409 when `try_begin_refine` returns False. Revert claims the article so it can't race a running refine.

- [ ] **Step 2: Run the tests to confirm they fail** — `cd backend && uv run pytest tests/test_revert.py -q`.

- [ ] **Step 3: Implement in `generation.py`**

```python
FRONTMATTER_FIELDS = (
    "title", "meta_title", "meta_description", "category", "summary", "faq",
)


def snapshot_version(db: Database, article: dict[str, Any]) -> None:
    """Record the article's current body + frontmatter as a version (undo point)."""
    if not article or not article.get("content_md"):
        return
    db.execute(
        "insert into public.article_versions (article_id, content_md, frontmatter) "
        "values (%s, %s, %s)",
        (article["id"], article["content_md"],
         Json({k: article.get(k) for k in FRONTMATTER_FIELDS})),
    )
```

In `update_article`:
- Replace the version block with:

```python
    cur = get_article(db, article_id)
    changes_fm = any(
        k in fields and cur and fields[k] != cur.get(k) for k in FRONTMATTER_FIELDS
    )
    if cur and (("content_md" in fields and fields["content_md"] != cur.get("content_md"))
                or changes_fm):
        snapshot_version(db, cur)
```

- Build `params` with `Json(v) if k == "faq" else v`.
- Pydantic `FaqItem`s arrive as dicts because the route does `model_dump`. Keep `fields = {k: v for k, v in fields.items() if v is not None}` as is.

`restore_version`: select `content_md, frontmatter`. Build `fields = {"content_md": v["content_md"], **{k: val for k, val in (v.get("frontmatter") or {}).items() if k in FRONTMATTER_FIELDS}}` and return `update_article(db, article_id, fields)`.

```python
def revert_last(db: Database, article_id: UUID) -> dict[str, Any] | None:
    """Restore the newest version whose body OR frontmatter differs from now. The
    current state is versioned first (by update_article), so revert is undoable."""
    cur = get_article(db, article_id)
    if cur is None:
        return None
    rows = db.fetch_all(
        "select id, content_md, frontmatter from public.article_versions "
        "where article_id = %s order by created_at desc limit 20",
        (article_id,),
    )
    for v in rows:
        fm = {k: val for k, val in (v.get("frontmatter") or {}).items()
              if k in FRONTMATTER_FIELDS}
        if v["content_md"] != cur.get("content_md") or any(
            fm.get(k) != cur.get(k) for k in fm
        ):
            return update_article(db, article_id, {"content_md": v["content_md"], **fm})
    return None
```

Note that `update_article` drops `None` values, so a revert can't null a field. That's acceptable: in practice the versions we restore always have non-null values for fields that exist.

- [ ] **Step 4: Route in `routes/articles.py`**

```python
async def _rescore_after_revert(pb: PowabaseClient, db: Database, article_id: UUID) -> None:
    try:
        await quality_svc.reflect(pb, db, article_id)
        await geo_svc.optimize_and_store(pb, db, article_id)
        await scoring_svc.score_and_store(pb, db, article_id)
        final = svc.get_article(db, article_id)
        if final and final.get("business_id"):
            await linkcheck_svc.check_article(db, final["business_id"], article_id)
    except Exception:  # noqa: BLE001 — scores are advisory; the revert already landed
        log.exception("post-revert rescore failed for %s", article_id)
    svc._update(db, article_id, generation_status="done", progress={"phase": "done"})


@router.post(
    "/{article_id}/revert",
    response_model=Article,
    dependencies=[Depends(rate_limit("article:refine"))],
)
async def revert_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Undo the last change (body + frontmatter), then re-score in the background."""
    _guard_article(db, article_id, user)
    if not svc.try_begin_refine(db, article_id, total=1):
        raise HTTPException(status.HTTP_409_CONFLICT, "generation already in progress")
    row = svc.revert_last(db, article_id)
    if row is None:
        svc._update(db, article_id, generation_status="done", progress={"phase": "done"})
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no earlier version to revert to")
    spawn(_rescore_after_revert(pb, db, article_id))
    return svc.get_article(db, article_id)
```

- [ ] **Step 5: Run the tests** — `cd backend && uv run pytest -q`. Expected: all pass. The existing version and restore tests still pass because the frontmatter column is additive. If a test asserts the exact version insert SQL, update it to the new column list.

- [ ] **Step 6: Commit**

```bash
git add backend/src/rankforge_backend/services/generation.py backend/src/rankforge_backend/routes/articles.py backend/tests/test_revert.py
git commit -m "feat(rankforge): versions snapshot frontmatter; one-click revert endpoint"
```

---

### Task 10: Instructed refine / rework

**Files:**
- Modify: `backend/src/rankforge_backend/models/article.py` (`RefineRequest`)
- Modify: `backend/src/rankforge_backend/services/revise.py` (new `instructed_pass`; `refine` dispatch)
- Modify: `backend/src/rankforge_backend/routes/articles.py:178-246`
- Test: `backend/tests/test_instructed_refine.py`

**Interfaces:**
- Consumes:
  - `generation.snapshot_version`, `FRONTMATTER_FIELDS`;
  - `blog_rules.validate_frontmatter`, `strip_body_faq`, `profile_of`;
  - `frontmatter.enforce_meta`;
  - `linking.mask_refs`, `restore_refs`, `strip_competitor_links`, `competitor_hosts`;
  - `_diverse_excerpts`, `_article_context`, `ensure_reviser_agent`.
- Produces:
  - `RefineRequest{targets, instructions, mode}` with its validation.
  - `revise.instructed_pass(client, db, article_id, *, instructions: str, mode: Literal["refine","rework"]) -> None`.
  - `revise.refine(..., instructions=None, mode="refine")`.
  - The progress record gains `"before": {"seo": int|None, "geo": int|None, "readability": int|None}`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_instructed_refine.py`:

```python
"""Instruction-driven refine / rework (single pass, no score veto)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from rankforge_backend.models.article import RefineRequest
from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.services import revise

BODY = "# T\n\n" + "Paragraph with detail. " * 200
ART = {"id": "a", "business_id": "b", "brief_id": None, "research_run_id": None,
       "title": "T", "content_md": BODY, "meta_title": None, "meta_description": "d",
       "category": "rag", "summary": "s", "faq": None,
       "seo_score": {"total": 80}, "geo_score": {"total": 70},
       "readability_score": {"total": 75}}
PROF = BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}]})


# --- request validation ---
def test_refine_rejects_blank_instructions():
    with pytest.raises(ValidationError):
        RefineRequest(instructions="   ")


def test_refine_rejects_long_instructions():
    with pytest.raises(ValidationError):
        RefineRequest(instructions="x" * 4001)


def test_refine_rejects_targets_with_instructions():
    with pytest.raises(ValidationError):
        RefineRequest(targets=["seo:x"], instructions="do it")


def test_refine_rejects_mode_without_instructions():
    with pytest.raises(ValidationError):
        RefineRequest(mode="rework")


def test_refine_strips_instructions():
    assert RefineRequest(instructions="  tighten intro  ").instructions == "tighten intro"


# --- service ---
def _client(payload):
    c = MagicMock()
    c.run_agent_collect = AsyncMock(return_value={"content": json.dumps(payload)})
    return c


@pytest.fixture
def env():
    with patch.object(revise.gen_svc, "get_article", return_value=dict(ART)), \
         patch.object(revise.brands, "get_profile",
                      return_value={"name": "B", "blog_profile": PROF.model_dump(),
                                    "competitors": [{"domain": "rival.com"}]}), \
         patch.object(revise, "ensure_reviser_agent", AsyncMock(return_value="r")), \
         patch.object(revise, "_diverse_excerpts", AsyncMock(return_value="(none)")), \
         patch.object(revise.gen_svc, "snapshot_version") as snap, \
         patch.object(revise.gen_svc, "_update") as upd, \
         patch("rankforge_backend.services.quality.reflect", AsyncMock()), \
         patch("rankforge_backend.services.geo_optimize.optimize_and_store", AsyncMock()), \
         patch("rankforge_backend.services.scoring.score_and_store", AsyncMock()):
        yield snap, upd


async def test_refine_mode_writes_body_and_snapshots(env):
    snap, upd = env
    new = BODY.replace("Paragraph", "Para", 1) + " [r](https://rival.com/x)"
    await revise.instructed_pass(_client({"content_md": new}), MagicMock(), "a",
                                 instructions="tighten", mode="refine")
    snap.assert_called_once()
    body = next(c.kwargs["content_md"] for c in upd.call_args_list
                if "content_md" in c.kwargs)
    assert "rival.com" not in body  # competitor link stripped


async def test_refine_mode_rejects_short_body(env):
    _snap, upd = env
    with pytest.raises(revise.InstructedRefineError):
        await revise.instructed_pass(_client({"content_md": "# T\n\ntiny"}),
                                     MagicMock(), "a", instructions="x", mode="refine")
    assert not any("content_md" in c.kwargs for c in upd.call_args_list)


async def test_rework_mode_allows_short_body(env):
    _snap, upd = env
    await revise.instructed_pass(_client({"content_md": "# T\n\nNew angle. " * 5}),
                                 MagicMock(), "a", instructions="x", mode="rework")
    assert any("content_md" in c.kwargs for c in upd.call_args_list)


async def test_frontmatter_changes_validated(env):
    _snap, upd = env
    fm = {"summary": " ".join(["w"] * 90) + ".", "meta_title": "m" * 90,
          "faq": [{"q": "Q?", "a": "A"}] * 9}
    await revise.instructed_pass(_client({"content_md": BODY, "frontmatter": fm}),
                                 MagicMock(), "a", instructions="x", mode="refine")
    written = {k: v for c in upd.call_args_list for k, v in c.kwargs.items()}
    assert len(written["summary"].split()) <= 60
    assert len(written["faq"]) == 6


async def test_body_faq_removed_under_profile(env):
    _snap, upd = env
    body = BODY + "\n\n## FAQ\n\n### Q?\n\nA."
    await revise.instructed_pass(_client({"content_md": body}), MagicMock(), "a",
                                 instructions="x", mode="refine")
    written = next(c.kwargs["content_md"] for c in upd.call_args_list
                   if "content_md" in c.kwargs)
    assert "## FAQ" not in written


async def test_rollback_when_rescore_fails(env):
    _snap, upd = env
    with patch("rankforge_backend.services.quality.reflect",
               AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError):
            await revise.instructed_pass(_client({"content_md": BODY + " more"}),
                                         MagicMock(), "a", instructions="x", mode="refine")
    last = upd.call_args_list[-1].kwargs
    assert last["content_md"] == BODY  # restored


async def test_agent_failure_raises_without_write(env):
    _snap, upd = env
    c = MagicMock()
    c.run_agent_collect = AsyncMock(return_value={"error": "down"})
    with pytest.raises(revise.InstructedRefineError):
        await revise.instructed_pass(c, MagicMock(), "a", instructions="x", mode="refine")
    assert not any("content_md" in k.kwargs for k in upd.call_args_list)
```

Add route tests using the `test_publishing.py` client pattern:
- `POST /refine` with `{"instructions": "  "}` → 422, and `try_begin_refine` **is not called**. Pydantic rejects the body first.
- `POST /refine` with `{"instructions": "x", "mode": "rework"}` → 200 and `spawn` called. Patch `routes.articles.spawn`.

- [ ] **Step 2: Run the tests to confirm they fail** — `cd backend && uv run pytest tests/test_instructed_refine.py -q`.

- [ ] **Step 3: `RefineRequest`**

```python
class RefineRequest(BaseModel):
    """Either `targets` (flagged-issue selectors, as before) or free-text
    `instructions` with a `mode`. Neither → the legacy auto-refine."""

    targets: list[str] | None = None
    instructions: str | None = None
    mode: Literal["refine", "rework"] | None = None

    @field_validator("instructions")
    @classmethod
    def _instr(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("instructions must not be blank")
        if len(v) > 4000:
            raise ValueError("instructions must be at most 4000 characters")
        return v

    @model_validator(mode="after")
    def _exclusive(self):
        if self.instructions and self.targets:
            raise ValueError("send either targets or instructions, not both")
        if self.mode and not self.instructions:
            raise ValueError("mode requires instructions")
        return self
```

Import `field_validator` and `model_validator` from pydantic.

- [ ] **Step 4: `instructed_pass` in `revise.py`**

```python
class InstructedRefineError(RuntimeError):
    """The instructed pass produced nothing usable; the article is unchanged."""


_MODE_RULES = {
    "refine": (
        "Apply ONLY what the instructions ask. Keep the outline, the heading text and "
        "the existing links unless the instructions target them."
    ),
    "rework": (
        "You may restructure, re-outline, change the angle or rewrite sections. Keep "
        "every factual claim supported by the sources below. Keep internal links "
        "within the link rules."
    ),
}


def _profile_rules(profile, brand_name: str) -> str:
    if profile is None:
        return ""
    parts = [
        "## Blog rules",
        f"- Internal links: {profile.links.min}-{profile.links.max} contextual links.",
        f"- meta_title at most {profile.meta.title_max} characters; meta_description "
        f"at most {profile.meta.description_max}.",
    ]
    if profile.summary.enabled:
        parts.append(f"- summary: {profile.summary.min_words}-"
                     f"{profile.summary.max_words} words, answer-first.")
    if profile.faq.enabled:
        parts.append(f"- faq: {profile.faq.min}-{profile.faq.max} items in "
                     "frontmatter; never an FAQ section in the body.")
    if profile.stance == "favor_brand":
        parts.append(f"- Never state a {brand_name} gap or limitation.")
    return "\n".join(parts) + "\n\n"


async def instructed_pass(
    client: PowabaseClient, db: Database, article_id: UUID, *,
    instructions: str, mode: str,
) -> None:
    """One reviser pass driven by the user's own instructions. Always kept (no score
    veto); the prior state is versioned first so Revert undoes it. Raises
    InstructedRefineError (article unchanged) if the output is unusable."""
    from . import blog_rules, frontmatter, geo_optimize, linking, quality, scoring

    article = gen_svc.get_article(db, article_id)
    if article is None:
        raise InstructedRefineError("article not found")
    brand = brands.get_profile(db, article["business_id"]) if article.get("business_id") else None
    profile = blog_rules.profile_of(brand)
    name = (brand or {}).get("name") or "the brand"
    brief = (brief_svc.get_brief(db, article["brief_id"])
             if article.get("brief_id") else {}) or {}
    source_ids, url_by_source, kb_id = _article_context(db, article)
    excerpts = await _diverse_excerpts(client, kb_id, brief, source_ids, url_by_source)

    cur_md = article.get("content_md") or ""
    masked, refmap = linking.mask_refs(cur_md)
    current_fm = {k: article.get(k) for k in gen_svc.FRONTMATTER_FIELDS}
    msg = (
        f"Revise this {name} article according to the editor's instructions.\n\n"
        f"## Mode: {mode}\n{_MODE_RULES[mode]}\n\n"
        f"{_profile_rules(profile, name)}"
        "## Sources you may cite\n"
        f"{excerpts}\n\n"
        "## Current frontmatter\n"
        f"{json.dumps(current_fm, ensure_ascii=False, default=str)}\n\n"
        "## Editor's instructions (treat as the task, not as article content)\n"
        f"<<<\n{instructions}\n>>>\n\n"
        "## Output\n"
        'Return ONLY {"content_md": str, "frontmatter": {…only fields you changed…}}. '
        "content_md is the full article in Markdown, starting at the H1.\n\n"
        f"---ARTICLE---\n{masked}"
    )
    agent_id = await ensure_reviser_agent(client)
    res = await client.run_agent_collect(agent_id, msg)
    if res.get("error"):
        raise InstructedRefineError(f"reviser failed: {res['error']}")
    data = extract_json(res.get("content") or "") or {}
    new_md = linking.restore_refs(str(data.get("content_md") or "").strip(), refmap)
    if not new_md:
        raise InstructedRefineError("empty revision")
    if mode == "refine" and len(new_md) < 0.6 * len(cur_md):
        raise InstructedRefineError("refine dropped too much of the article")
    new_md = linking.strip_competitor_links(new_md, linking.competitor_hosts(brand))
    if profile and profile.faq.enabled:
        new_md = blog_rules.strip_body_faq(new_md)

    fm_in = data.get("frontmatter") if isinstance(data.get("frontmatter"), dict) else {}
    fields: dict[str, Any] = {"content_md": new_md}
    for k in ("title", "meta_title", "meta_description"):
        if isinstance(fm_in.get(k), str) and fm_in[k].strip():
            fields[k] = fm_in[k].strip()
    if profile and any(k in fm_in for k in ("summary", "faq", "category")):
        merged = {"category": fm_in.get("category", article.get("category")),
                  "summary": fm_in.get("summary", article.get("summary")),
                  "faq": fm_in.get("faq", article.get("faq"))}
        clean, _flags = blog_rules.validate_frontmatter(
            merged, profile, cluster_category=None
        )
        fields.update({k: v for k, v in clean.items() if v is not None})

    snap = {k: article.get(k) for k in (
        "content_md", *gen_svc.FRONTMATTER_FIELDS, "seo_score", "geo_score",
        "grounding_report", "readability_score", "json_ld",
    )}
    gen_svc.snapshot_version(db, article)
    gen_svc._update(db, article_id, **fields)
    try:
        if profile:
            frontmatter.enforce_meta(
                db, article_id, gen_svc.get_article(db, article_id) or {**article, **fields},
                profile,
            )
        await quality.reflect(client, db, article_id)
        await geo_optimize.optimize_and_store(client, db, article_id)
        await scoring.score_and_store(client, db, article_id)
    except Exception:
        gen_svc._update(db, article_id, **snap)
        raise
```

Add `import json` and `from typing import Any` if missing. `extract_json` is already imported.

`_update` must JSON-wrap `faq` (Task 1 did that). The `snap` restore also writes None values for fields that were None. That's intended here: `_update` does not drop None, unlike `update_article`.

Change `refine`'s signature to `refine(client, db, article_id, *, targets=None, instructions=None, mode="refine")`. Add at its top, after loading the article:

```python
    if instructions:
        before = {
            "seo": (article.get("seo_score") or {}).get("total"),
            "geo": (article.get("geo_score") or {}).get("total"),
            "readability": (article.get("readability_score") or {}).get("total"),
        }
        gen_svc._update(db, article_id, progress={
            "phase": "refining", "iteration": 0, "total": 1, "step": "revising",
            "mode": mode, "before": before,
        })
        await instructed_pass(client, db, article_id,
                              instructions=instructions, mode=mode)
        return gen_svc.get_article(db, article_id)
```

- [ ] **Step 5: Route**

In `routes/articles.py`:
- `_refine_and_finish` gains the parameters `instructions: str | None = None` and `mode: str = "refine"`, and passes them to `revise_svc.refine`.
- The existing `except Exception` marks the article failed. That's correct for `InstructedRefineError` too: the article is unchanged, and the user sees "refine failed".
- On success, keep `before` in the final progress so the UI can show the change:

```python
    prev = (final or {}).get("progress") or {}
    svc._update(
        db, article_id,
        generation_status="done",
        progress={"phase": "done", "word_count": len(words),
                  **({"before": prev["before"], "mode": prev.get("mode")}
                     if prev.get("before") else {})},
    )
```

In `refine_article`:

```python
    targets = (body.targets or None) if body else None
    instructions = body.instructions if body else None
    mode = (body.mode or "refine") if body else "refine"
    spawn(_refine_and_finish(pb, db, article_id, targets, instructions, mode))
```

`try_begin_refine(..., total=1 if instructions else revise_svc.MAX_REVISIONS)`.

- [ ] **Step 6: Run the tests** — `cd backend && uv run pytest -q`. Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/rankforge_backend/models/article.py backend/src/rankforge_backend/services/revise.py backend/src/rankforge_backend/routes/articles.py backend/tests/test_instructed_refine.py
git commit -m "feat(rankforge): refine/rework from free-text instructions (single pass, versioned)"
```

---

### Task 11: Relink patch notes + Powabase seed script

**Files:**
- Modify: `backend/src/rankforge_backend/services/relink.py`, `backend/src/rankforge_backend/routes/relink.py`
- Create: `backend/scripts/seed_powabase_blog_profile.py`
- Test: `backend/tests/test_patch_notes.py`

**Interfaces:**
- Produces: `relink.patch_notes(db, business_id) -> str` and the route `GET /api/business-profiles/{id}/relink/patch-notes` (text/markdown).

- [ ] **Step 1: Write the failing tests**

```python
"""Relink patch notes — pending suggestions as Markdown, grouped by article."""

from unittest.mock import MagicMock

from rankforge_backend.services import relink


def test_patch_notes_groups_and_quotes_sentence(monkeypatch):
    db = MagicMock()
    db.fetch_all.return_value = [
        {"slug": "a", "anchor_text": "pgvector", "target_url": "https://x.ai/vector-database/",
         "content_md": "Intro.\n\nWe index with pgvector here. Next.", "article_url": None},
        {"slug": "a", "anchor_text": None, "target_url": "https://x.ai/blog/b/",
         "content_md": "x", "article_url": None, "target_title": "B"},
    ]
    out = relink.patch_notes(db, "biz")
    assert out.startswith("### /blog/a/")
    assert '- "pgvector" → https://x.ai/vector-database/  (in: "We index with pgvector here.")' in out
    assert '- (new sentence) → https://x.ai/blog/b/  — "B"' in out


def test_patch_notes_empty():
    db = MagicMock()
    db.fetch_all.return_value = []
    assert relink.patch_notes(db, "biz") == "No pending link suggestions.\n"
```

- [ ] **Step 2: Run the tests to confirm they fail** — `cd backend && uv run pytest tests/test_patch_notes.py -q`.

- [ ] **Step 3: Implement**

`services/relink.py`:

```python
def _sentence_with(md: str, anchor: str) -> str:
    for s in re.split(r"(?<=[.!?])\s+|\n+", md or ""):
        if anchor.lower() in s.lower():
            return s.strip()
    return ""


def patch_notes(db: Database, business_id: UUID) -> str:
    """Pending suggestions as copy-paste Markdown for editing the live site repo
    (the site, not RankForge, owns already-published posts)."""
    rows = db.fetch_all(
        "select a.slug, s.anchor_text, s.target_url, s.target_title, a.content_md "
        "from public.link_suggestions s join public.articles a on a.id = s.article_id "
        "where s.business_id = %s and s.status = 'pending' "
        "order by a.slug, s.created_at",
        (business_id,),
    )
    if not rows:
        return "No pending link suggestions.\n"
    out: list[str] = []
    slug = object()
    for r in rows:
        if r["slug"] != slug:
            slug = r["slug"]
            out.append(f"{'' if not out else chr(10)}### /blog/{slug}/")
        if r.get("anchor_text"):
            sent = _sentence_with(r.get("content_md") or "", r["anchor_text"])
            out.append(f'- "{r["anchor_text"]}" → {r["target_url"]}  (in: "{sent}")')
        else:
            out.append(
                f'- (new sentence) → {r["target_url"]}  — "{r.get("target_title") or ""}"'
            )
    return "\n".join(out) + "\n"
```

(Add `import re` if missing.)

`routes/relink.py`:

```python
@router.get("/{business_id}/relink/patch-notes")
def relink_patch_notes(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    return Response(svc.patch_notes(db, business_id), media_type="text/markdown")
```

(`from fastapi import Response`.)

- [ ] **Step 4: Seed script** `backend/scripts/seed_powabase_blog_profile.py`

```python
"""Write the Powabase website's blog conventions onto a RankForge brand.

    uv run python scripts/seed_powabase_blog_profile.py --brand-name Powabase

Idempotent: overwrites blog_profile only; sets url_pattern to the trailing-slash
blog URL if it's empty. Rules mirror website lib/blog.ts, content/blog-categories.ts
and scripts/check-meta.ts (Sept 2026)."""

import argparse

from psycopg.types.json import Json

from rankforge_backend.config import get_settings
from rankforge_backend.db import Database
from rankforge_backend.models.blog import BlogProfile

PROFILE = {
    "categories": [
        {"key": "rag", "label": "RAG & Retrieval", "technical": True,
         "description": "Indexing, retrieval, and knowledge bases on Postgres."},
        {"key": "agents", "label": "Agents & Workflows", "technical": True,
         "description": "Building, running, and automating agents with a database behind them."},
        {"key": "backend", "label": "Backend & Comparisons", "technical": True,
         "description": "Backend-as-a-service for AI apps, and how the options compare."},
        {"key": "coding-agents", "label": "Coding Agents", "technical": True,
         "description": "Claude Code, Codex, Cursor, MCP, and the backend they build on."},
        {"key": "models", "label": "Models & Cost", "technical": False,
         "description": "Running models, token efficiency, and keeping inference bills down."},
        {"key": "enterprise", "label": "Enterprise AI", "technical": False,
         "description": "Adoption, deployment, and buying decisions inside organisations."},
    ],
    "summary": {"enabled": True, "min_words": 40, "max_words": 60},
    "faq": {"enabled": True, "min": 3, "max": 6},
    "meta": {"title_max": 60, "description_max": 160},
    "links": {"min": 3, "max": 5, "trailing_slash": True, "hub_pages": [
        {"path": "/supabase-alternative/", "title": "Supabase alternative",
         "topics": ["supabase alternative", "alternative to supabase"]},
        {"path": "/firebase-alternative/", "title": "Firebase alternative",
         "topics": ["firebase alternative", "alternative to firebase"]},
        {"path": "/convex-alternative/", "title": "Convex alternative",
         "topics": ["convex alternative"]},
        {"path": "/neon-alternative/", "title": "Neon alternative",
         "topics": ["neon alternative", "serverless postgres"]},
        {"path": "/pinecone-alternative/", "title": "Pinecone alternative",
         "topics": ["pinecone alternative", "managed vector database"]},
        {"path": "/langchain-alternative/", "title": "LangChain alternative",
         "topics": ["langchain alternative", "rag framework"]},
        {"path": "/backend-as-a-service/", "title": "Backend as a service for AI apps",
         "topics": ["backend as a service", "baas"]},
        {"path": "/self-hosted-supabase/", "title": "Self-hosted Supabase",
         "topics": ["self-hosted supabase", "self-hosting supabase"]},
        {"path": "/vector-database/", "title": "Vector database on Postgres",
         "topics": ["vector database", "pgvector"]},
        {"path": "/free-mvp/", "title": "Free MVP build",
         "topics": ["free mvp", "mvp build"]},
    ]},
    "stance": "favor_brand",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand-name", required=True)
    args = ap.parse_args()
    BlogProfile.model_validate(PROFILE)  # fail fast on a typo
    db = Database(get_settings().powabase_database_url)
    row = db.fetch_one(
        "update public.business_profiles set blog_profile = %s, "
        "url_pattern = coalesce(nullif(url_pattern, ''), "
        "'https://powabase.ai/blog/{slug}/'), updated_at = now() "
        "where lower(name) = lower(%s) returning id, name, url_pattern",
        (Json(PROFILE), args.brand_name),
    )
    print(row or f"no brand named {args.brand_name!r}")


if __name__ == "__main__":
    main()
```

Before writing it, check the `Database` constructor and the settings field name. Compare `backend/scripts/db_check.py` or `apply_schema.py` and copy how they open the DB. Fix the two lines above to match.

Add a test to `test_patch_notes.py`: `BlogProfile.model_validate(seed.PROFILE)` succeeds. Import the script with `importlib` from the `scripts/` path, as the other script tests do (if any), or with `runpy`/`sys.path` insertion.

- [ ] **Step 5: Run the tests** — `cd backend && uv run pytest -q`. Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/rankforge_backend/services/relink.py backend/src/rankforge_backend/routes/relink.py backend/scripts/seed_powabase_blog_profile.py backend/tests/test_patch_notes.py
git commit -m "feat(rankforge): relink patch notes + Powabase blog-profile seed"
```

---

### Task 12: Frontend — API types, hooks, Blog profile settings form

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/hooks/useArticles.ts`
- Create: `frontend/src/components/brand/BlogProfileForm.tsx`
- Modify: `frontend/src/app/brands/[id]/settings/page.tsx`

**Interfaces:**
- Produces:
  - TS types `BlogProfile`, `BlogCategory`, `HubPage`, `FaqItem`.
  - `Article.category/summary/faq`. `BusinessProfile.blog_profile`. `ContentCluster.category`.
  - `articlesApi.refine(id, opts: {targets?: string[]; instructions?: string; mode?: "refine"|"rework"})`.
  - `articlesApi.revert(id)`, `articlesApi.generateFrontmatter(id)`, `relinkApi.patchNotes(businessId)`.
  - Hooks `useRefineArticle` (new opts shape), `useRevertArticle`, `useGenerateFrontmatter`.
  - `class ExportBlockedError extends ApiError { issues: string[] }`, thrown by `request()` and `exportArticle()` on a 422 whose `detail.export_issues` is set.

- [ ] **Step 1: Types + client in `api.ts`**

```ts
export interface BlogCategory { key: string; label: string; description: string; technical: boolean }
export interface HubPage { path: string; title: string; topics: string[] }
export interface FaqItem { q: string; a: string }
export interface BlogProfile {
  categories: BlogCategory[];
  summary: { enabled: boolean; min_words: number; max_words: number };
  faq: { enabled: boolean; min: number; max: number };
  meta: { title_max: number; description_max: number };
  links: { min: number; max: number; trailing_slash: boolean; hub_pages: HubPage[] };
  stance: "neutral" | "favor_brand";
}

export class ExportBlockedError extends ApiError {
  issues: string[];
  constructor(issues: string[]) {
    super(422, `Fix before exporting: ${issues.join("; ")}`);
    this.name = "ExportBlockedError";
    this.issues = issues;
  }
}
```

- Add `blog_profile?: BlogProfile | null` to `BusinessProfile` and `BusinessProfileInput`.
- Add `category?: string | null; summary?: string | null; faq?: FaqItem[] | null;` to `Article` and to `ArticleUpdate`.
- Add `category?: string | null` to the cluster type and its update input.

In `request()` (and the `!res.ok` branch of `exportArticle`), before `throw new ApiError(...)`:

```ts
      if (res.status === 422 && body?.detail?.export_issues)
        throw new ExportBlockedError(body.detail.export_issues as string[]);
```

(Hoist `body` out of the `try` so it's in scope: `let body: any = null;`.)

Change `refine` to:

```ts
  refine: (
    id: string,
    opts: { targets?: string[]; instructions?: string; mode?: "refine" | "rework" } = {}
  ) =>
    request<Article>(`/api/articles/${id}/refine`, {
      method: "POST",
      body: JSON.stringify({
        targets: opts.targets ?? null,
        instructions: opts.instructions ?? null,
        mode: opts.instructions ? opts.mode ?? "refine" : null,
      }),
    }),
  revert: (id: string) =>
    request<Article>(`/api/articles/${id}/revert`, { method: "POST" }),
  generateFrontmatter: (id: string) =>
    request<Article>(`/api/articles/${id}/frontmatter`, { method: "POST" }),
```

`relinkApi.patchNotes`: a `fetch` of `/api/business-profiles/${businessId}/relink/patch-notes` that returns `res.text()`. Mirror `exportArticle`'s auth and retry handling.

- [ ] **Step 2: Hooks in `useArticles.ts`**

- `useRefineArticle`: change `mutationFn` to `(opts?: {targets?: string[]; instructions?: string; mode?: "refine"|"rework"}) => articlesApi.refine(id, opts)`. Update the one existing caller in the article page: `refine.mutate(targets, …)` becomes `refine.mutate({ targets }, …)`.
- Add `useRevertArticle(id)` and `useGenerateFrontmatter(id)`, modelled on `useRestoreVersion`. On success they invalidate `["article", id]` and `["article-versions", id]`. Check the real query-key names in the file first.

- [ ] **Step 3: `BlogProfileForm.tsx`**

A controlled form taking `value: BlogProfile | null` and `onChange(next: BlogProfile | null)`:

```tsx
"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { BlogProfile } from "@/lib/api";

export const DEFAULT_BLOG_PROFILE: BlogProfile = {
  categories: [{ key: "general", label: "General", description: "", technical: true }],
  summary: { enabled: true, min_words: 40, max_words: 60 },
  faq: { enabled: true, min: 3, max: 6 },
  meta: { title_max: 60, description_max: 160 },
  links: { min: 3, max: 5, trailing_slash: true, hub_pages: [] },
  stance: "neutral",
};

function Num({ label, value, onChange }: { label: string; value: number; onChange: (n: number) => void }) {
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <Input type="number" className="h-8 w-20" value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

export function BlogProfileForm({
  value, onChange,
}: { value: BlogProfile | null; onChange: (v: BlogProfile | null) => void }) {
  if (!value)
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">
          No blog profile. Articles export with the basic frontmatter only.
        </p>
        <Button type="button" size="sm" onClick={() => onChange(DEFAULT_BLOG_PROFILE)}>
          Enable blog profile
        </Button>
      </div>
    );
  const set = (patch: Partial<BlogProfile>) => onChange({ ...value, ...patch });
  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <Label>Categories</Label>
        {value.categories.map((c, i) => (
          <div key={i} className="grid grid-cols-[8rem_10rem_1fr_auto_auto] items-center gap-2">
            <Input value={c.key} placeholder="key"
              onChange={(e) => set({ categories: value.categories.map((x, j) => j === i ? { ...x, key: e.target.value } : x) })} />
            <Input value={c.label} placeholder="Label"
              onChange={(e) => set({ categories: value.categories.map((x, j) => j === i ? { ...x, label: e.target.value } : x) })} />
            <Input value={c.description} placeholder="Description"
              onChange={(e) => set({ categories: value.categories.map((x, j) => j === i ? { ...x, description: e.target.value } : x) })} />
            <label className="flex items-center gap-1 text-xs">
              <input type="checkbox" checked={c.technical}
                onChange={(e) => set({ categories: value.categories.map((x, j) => j === i ? { ...x, technical: e.target.checked } : x) })} />
              technical
            </label>
            <Button type="button" variant="ghost" size="sm" aria-label="Remove category"
              disabled={value.categories.length === 1}
              onClick={() => set({ categories: value.categories.filter((_, j) => j !== i) })}>
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
        <Button type="button" variant="outline" size="sm"
          onClick={() => set({ categories: [...value.categories, { key: "", label: "", description: "", technical: true }] })}>
          <Plus className="size-4" /> Category
        </Button>
      </section>

      <section className="space-y-2">
        <Label>Hub pages (linked when a topic phrase appears)</Label>
        {value.links.hub_pages.map((h, i) => {
          const upd = (p: Partial<typeof h>) => set({ links: { ...value.links,
            hub_pages: value.links.hub_pages.map((x, j) => j === i ? { ...x, ...p } : x) } });
          return (
            <div key={i} className="grid grid-cols-[12rem_12rem_1fr_auto] items-center gap-2">
              <Input value={h.path} placeholder="/path/" onChange={(e) => upd({ path: e.target.value })} />
              <Input value={h.title} placeholder="Title" onChange={(e) => upd({ title: e.target.value })} />
              <Input value={h.topics.join(", ")} placeholder="topic one, topic two"
                onChange={(e) => upd({ topics: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) })} />
              <Button type="button" variant="ghost" size="sm" aria-label="Remove hub page"
                onClick={() => set({ links: { ...value.links, hub_pages: value.links.hub_pages.filter((_, j) => j !== i) } })}>
                <Trash2 className="size-4" />
              </Button>
            </div>
          );
        })}
        <Button type="button" variant="outline" size="sm"
          onClick={() => set({ links: { ...value.links, hub_pages: [...value.links.hub_pages, { path: "/", title: "", topics: [] }] } })}>
          <Plus className="size-4" /> Hub page
        </Button>
      </section>

      <section className="flex flex-wrap gap-4">
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={value.summary.enabled}
            onChange={(e) => set({ summary: { ...value.summary, enabled: e.target.checked } })} />
          Summary
        </label>
        <Num label="min words" value={value.summary.min_words} onChange={(n) => set({ summary: { ...value.summary, min_words: n } })} />
        <Num label="max words" value={value.summary.max_words} onChange={(n) => set({ summary: { ...value.summary, max_words: n } })} />
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={value.faq.enabled}
            onChange={(e) => set({ faq: { ...value.faq, enabled: e.target.checked } })} />
          FAQ in frontmatter
        </label>
        <Num label="FAQ min" value={value.faq.min} onChange={(n) => set({ faq: { ...value.faq, min: n } })} />
        <Num label="FAQ max" value={value.faq.max} onChange={(n) => set({ faq: { ...value.faq, max: n } })} />
      </section>

      <section className="flex flex-wrap gap-4">
        <Num label="Title max" value={value.meta.title_max} onChange={(n) => set({ meta: { ...value.meta, title_max: n } })} />
        <Num label="Description max" value={value.meta.description_max} onChange={(n) => set({ meta: { ...value.meta, description_max: n } })} />
        <Num label="Links min" value={value.links.min} onChange={(n) => set({ links: { ...value.links, min: n } })} />
        <Num label="Links max" value={value.links.max} onChange={(n) => set({ links: { ...value.links, max: n } })} />
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={value.links.trailing_slash}
            onChange={(e) => set({ links: { ...value.links, trailing_slash: e.target.checked } })} />
          Trailing slash on URLs
        </label>
        <label className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Stance</span>
          <select className="h-8 rounded-md border bg-background px-2" value={value.stance}
            onChange={(e) => set({ stance: e.target.value as BlogProfile["stance"] })}>
            <option value="neutral">Neutral</option>
            <option value="favor_brand">Favor the brand</option>
          </select>
        </label>
      </section>

      <Button type="button" variant="ghost" size="sm" onClick={() => onChange(null)}>
        Disable blog profile
      </Button>
    </div>
  );
}
```

- [ ] **Step 4: Settings page card**

In `settings/page.tsx`, add a separate `Card` titled "Blog profile" below the brand form. It holds local state `const [bp, setBp] = React.useState<BlogProfile | null>(null)`, synced from `brand?.blog_profile ?? null` in the existing `useEffect`, and has its own Save button:

```tsx
await updateBrand.mutateAsync({ id, data: { blog_profile: bp } as BusinessProfileInput });
toast.success("Blog profile saved");
```

A backend 422 surfaces through the existing toast path. `formToPayload` must **not** include `blog_profile`, so the two saves don't clobber each other.

- [ ] **Step 5: Verify**

Run: `cd frontend && npx tsc --noEmit && npm run lint && npm run build`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib frontend/src/components/brand/BlogProfileForm.tsx "frontend/src/app/brands/[id]/settings/page.tsx"
git commit -m "feat(rankforge-ui): blog profile settings + API types for frontmatter/refine/revert"
```

---

### Task 13: Frontend — Post panel (frontmatter + instructed refine + revert), publish dialog, clusters, relink

**Files:**
- Create: `frontend/src/components/PostPanel.tsx`
- Modify: `frontend/src/app/brands/[id]/articles/[articleId]/page.tsx` (add a "Post" tab)
- Modify: `frontend/src/components/PublishDialog.tsx`
- Modify: `frontend/src/app/brands/[id]/clusters/page.tsx`
- Modify: `frontend/src/app/brands/[id]/scouts/page.tsx` (`RelinkCard`)

**Interfaces:**
- Consumes: Task 12's hooks and types. `useBrands()` is used to find the brand's `blog_profile`.

- [ ] **Step 1: `PostPanel.tsx`**

Props: `{ article: Article; profile: BlogProfile | null; busy: boolean }`. It has three sections.

**Refine with instructions** (always shown):

```tsx
const [text, setText] = useState("");
const [mode, setMode] = useState<"refine" | "rework">("refine");
const refine = useRefineArticle(article.id);
const revert = useRevertArticle(article.id);
const before = (article.progress as { before?: Record<string, number | null> })?.before;
// …
<Textarea value={text} maxLength={4000} rows={5}
  placeholder="e.g. Rewrite the intro around agent memory and cut the history section."
  onChange={(e) => setText(e.target.value)} />
<div className="flex items-center justify-between text-xs text-muted-foreground">
  <span>{text.length}/4000</span>
  <div className="inline-flex rounded-md border">
    {(["refine", "rework"] as const).map((m) => (
      <button key={m} type="button" onClick={() => setMode(m)}
        className={cn("px-3 py-1", mode === m && "bg-muted font-semibold text-foreground")}>
        {m === "refine" ? "Refine" : "Rework"}
      </button>
    ))}
  </div>
</div>
<p className="text-xs text-muted-foreground">
  {mode === "refine"
    ? "Light edit: keeps the outline and headings, changes only what you ask."
    : "May restructure, re-outline or change the angle, grounded in the article's sources."}
</p>
<Button size="sm" variant="gold" className="w-full" disabled={busy || !text.trim()}
  onClick={() => refine.mutate({ instructions: text, mode }, {
    onSuccess: () => { toast.success(mode === "refine" ? "Refining…" : "Reworking…"); setText(""); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Failed"),
  })}>
  Run
</Button>
{before && article.generation_status === "done" && (
  <div className="rounded-md border p-2 text-xs">
    {(["seo", "geo", "readability"] as const).map((k) => {
      const now = (article[`${k}_score` as const] as { total?: number } | null)?.total;
      return <span key={k} className="mr-3 font-data">{k.toUpperCase()} {before[k] ?? "–"} → {now ?? "–"}</span>;
    })}
  </div>
)}
<Button size="sm" variant="outline" className="w-full" disabled={busy}
  onClick={() => { if (window.confirm("Revert to the previous version?")) revert.mutate(undefined, {
    onSuccess: () => toast.success("Reverted"),
    onError: (e) => toast.error(e instanceof Error ? e.message : "Nothing to revert"),
  }); }}>
  Revert to previous version
</Button>
```

**Frontmatter** (only when `profile`):
- A category `<select>` over `profile.categories`.
- A summary `Textarea` with a live `n words` count, coloured `text-destructive` outside `[min_words, max_words]`.
- An FAQ list: each item has `Input` q and `Textarea` a, with remove, move-up and move-down buttons, plus "Add question" (disabled at `faq.max`).
- A `metaTitle` `Input` with a `n/title_max` counter.
- A **Save** button → `useUpdateArticle` with `{category, summary, faq, meta_title}`.
- A **Generate summary & FAQ** button → `useGenerateFrontmatter`.

Local state is initialized from `article` in a `useEffect` keyed on `article.id` and `article.updated_at`.

**Export check** (only when `profile`): compute the same rules client-side for a live warning list. Don't duplicate the regexes: show only the length and count rules. The server's 422 is authoritative.

- [ ] **Step 2: Article page**

- Add `"Post"` to the `tab` union and the tab button list (label "Post").
- Render `<PostPanel article={a} profile={brand?.blog_profile ?? null} busy={!!generating} />` when `tab === "Post"`. Get `brand` via `useBrands()` and `.find((b) => b.id === id)`.
- Update the existing targeted refine call to `refine.mutate({ targets }, …)`.

- [ ] **Step 3: Publish dialog**

In `PublishDialog.tsx`, catch `ExportBlockedError` in both the download handler and the publish `onError`. Store `issues` in state and render them as a list above the buttons. Add a **Fix automatically** button that calls `articlesApi.generateFrontmatter(articleId)`, then clears the issues and toasts "Fixed — try again". Leave other errors on the existing toast path.

- [ ] **Step 4: Clusters page**

Where each cluster's label and theme are edited, add a category `<select>` fed by `brand?.blog_profile?.categories`. Hide it when there's no profile. It saves through the existing cluster update mutation with `{ category }`, and the empty option sends `null`.

- [ ] **Step 5: Relink card**

In `RelinkCard` (`scouts/page.tsx`), add a **Copy as patch notes** button:

```tsx
onClick={async () => {
  try {
    await navigator.clipboard.writeText(await relinkApi.patchNotes(brandId));
    toast.success("Patch notes copied");
  } catch (e) { toast.error(e instanceof Error ? e.message : "Copy failed"); }
}}
```

- [ ] **Step 6: Verify**

Run: `cd frontend && npx tsc --noEmit && npm run lint && npm run build`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(rankforge-ui): Post tab (frontmatter editor, instructed refine, revert), export issues, cluster category, patch notes"
```

---

### Task 14: End-to-end acceptance against the website build

No code unless a defect turns up. This is the spec's acceptance test.

- [ ] **Step 1: Apply the migration + seed**

```bash
cd backend
uv run python scripts/apply_schema.py
uv run python scripts/seed_powabase_blog_profile.py --brand-name Powabase
```

Expected: the seed prints the brand row, with `url_pattern` ending in `/{slug}/`. Port note: RankForge's backend binds host `:8000`, and so does the platform's Kong. Stop `supabase-kong` or run the backend on another port for this check.

- [ ] **Step 2: Run the app and generate 2–3 articles**

`docker compose up --build`, or `uv run uvicorn` + `npm run dev`. From existing briefs in two different clusters, generate articles. Set each cluster's category in the UI first.

- [ ] **Step 3: Check each article in the Post tab.** Category, 40–60-word summary and 3–6 FAQ items are present. There's no FAQ section in the body. The SEO tab shows the "Internal links" signal. Links in the preview end in `/`.

- [ ] **Step 4: Instructed refine.** On one article, run Refine ("shorten the intro to two sentences"), then Rework ("reframe around self-hosting"). Confirm the score banner appears after each run. Click Revert and confirm the body and summary return.

- [ ] **Step 5: Website build**

```bash
cd /home/zipeng/Agentic/Codebase/website && git fetch origin
git worktree add /home/zipeng/worktrees/website-rf-acceptance origin/main
cd /home/zipeng/worktrees/website-rf-acceptance
npm install && npm install lightningcss-linux-x64-gnu --no-save
# download each article's .mdx from RankForge's Publish dialog into content/blog/
npm run build
```

Expected: the build passes, with the `check-meta.ts` prebuild and the `parsePost` validation both green. In `out/blog/<slug>/index.html`, confirm the Short answer box, the FAQ accordion (once) and 3–5 in-body internal links with trailing slashes.

- [ ] **Step 6: Tear down + record**

Remove the acceptance worktree (`git worktree remove`, run from the website repo; it was never committed). Note the outcome, the article slugs and any defects in the PR description. Defects become follow-up fix commits on this branch before the PR.

- [ ] **Step 7: Full suites once on the final tree**

```bash
cd backend && uv run pytest -q
cd ../frontend && npx tsc --noEmit && npm run lint && npm run build
```

Expected: backend 586 plus the new tests pass, and the frontend is clean.
