# LinkedIn Post Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any workspace member generate insightful, brand-voice, anti-AI-tell LinkedIn posts (multiple angle-preset variants) from a blog article, then edit / copy / delete them.

**Architecture:** A per-article child resource (`linkedin_posts`, mirrors `article_comments`) plus one synchronous LLM endpoint (mirrors the single-shot FAQ/meta pattern). CRUD + generation live under `/api/articles/{id}/linkedin-posts`; the frontend adds a "LinkedIn" tab to the article page. Everything is org-scoped via the existing `_guard_article`.

**Tech Stack:** Backend FastAPI + psycopg3 + Pydantic v2, `uv run pytest` / `uv run ruff` (line-length 88). Frontend Next.js 16 (App Router) + React 19 + TanStack Query v5 + Tailwind + shadcn/ui. LLM via Powabase agents (`ensure_agent` + `run_agent`).

## Global Constraints

- Repo root of this worktree: `/home/zipeng/worktrees/rankforge-linkedin`. The app lives under `rankforge/` (`rankforge/backend`, `rankforge/frontend`).
- Backend commands run from `rankforge/backend`; use `uv run <cmd>`. Lint: `uv run ruff check <paths>` (line-length 88). Tests: `uv run pytest`.
- Frontend commands run from `rankforge/frontend`. No frontend test runner exists — verify via `./node_modules/.bin/next dev --webpack` build, `node node_modules/typescript/bin/tsc --noEmit`, and `npm run lint`.
- Env: `.env` files already copied into the worktree. Backend reads `POWABASE_DATABASE_URL` from `rankforge/backend/.env`. Node is at `/home/zipeng/.nvm/versions/node/v22.20.0/bin`; `uv` at `/home/zipeng/.local/bin`.
- Migrations: apply with `uv run python scripts/apply_schema.py` (idempotent, tracked in `public.schema_migrations`). Apply the migration BEFORE running code that reads the new table.
- Never `git add .` (core.autocrlf phantom files) — add explicit paths. `GIT_LITERAL_PATHSPECS=1` is required to `git add` bracket paths like `src/app/brands/[id]/...`.
- LinkedIn hard limit: **3,000 characters**. Above-the-fold marker: **210 characters**.
- Angle slugs (single source of truth, used verbatim everywhere): `key_insight`, `lesson`, `contrarian`, `story`, `stat`.
- Permissions: any authenticated workspace member can GET/POST/PATCH/DELETE (no `require_editor`); org access is enforced by `_guard_article`.
- Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Model: `claude-opus-4-7` for generation (the writer model — voice fidelity).

---

### Task 1: Database migration `linkedin_posts`

**Files:**
- Create: `rankforge/backend/schema/0035_linkedin_posts.sql`

**Interfaces:**
- Produces: table `public.linkedin_posts(id, article_id, business_id, angle, body, created_by, created_at, updated_at)` with org-scoped RLS and index `linkedin_posts_article_idx`.

- [ ] **Step 1: Write the migration**

Create `rankforge/backend/schema/0035_linkedin_posts.sql`:

```sql
-- Per-article LinkedIn post variants. Each row is one generated (then editable) post,
-- attached to an article and owned by its brand's org. Deleting the article cascades.
create table if not exists public.linkedin_posts (
    id           uuid primary key default gen_random_uuid(),
    article_id   uuid not null references public.articles (id) on delete cascade,
    -- Denormalized brand id so RLS can scope directly by org (mirrors articles/
    -- opportunities); the app layer still guards via _guard_article.
    business_id  uuid not null references public.business_profiles (id) on delete cascade,
    angle        text not null,          -- key_insight|lesson|contrarian|story|stat (app-enforced)
    body         text not null default '',
    created_by   uuid references auth.users (id) on delete set null,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index if not exists linkedin_posts_article_idx
    on public.linkedin_posts (article_id, created_at desc);

alter table public.linkedin_posts enable row level security;
-- Org-scoped (defense-in-depth; the app layer is primary). Mirrors other content tables.
create policy linkedin_posts_rw on public.linkedin_posts
    for all to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()))
    with check (business_id in (select id from public.business_profiles
                                where org_id = public.current_org_id()));
```

- [ ] **Step 2: Apply the migration**

Run (from `rankforge/backend`):
```bash
cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run python scripts/apply_schema.py
```
Expected: output includes `applying 0035_linkedin_posts.sql ...` then `ok: 0035_linkedin_posts.sql` and `schema up to date.`

- [ ] **Step 3: Verify the table exists**

Run:
```bash
cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run python -c "from rankforge_backend.db import Database; from rankforge_backend.config import get_settings; d=Database(get_settings().powabase_database_url); print(d.fetch_all(\"select column_name from information_schema.columns where table_name='linkedin_posts' order by ordinal_position\"))"
```
Expected: a list of dicts with column_name in order: id, article_id, business_id, angle, body, created_by, created_at, updated_at.

- [ ] **Step 4: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-linkedin && git add rankforge/backend/schema/0035_linkedin_posts.sql && git commit -m "feat(linkedin): linkedin_posts table (migration 0035)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Pydantic models

**Files:**
- Create: `rankforge/backend/src/rankforge_backend/models/linkedin.py`
- Test: `rankforge/backend/tests/test_linkedin.py`

**Interfaces:**
- Produces: `ANGLE_SLUGS: tuple[str, ...]`, `Angle` (Literal), `LinkedInGenerate{angle: Angle = "key_insight"}`, `LinkedInUpdate{body: str (1..3000)}`, `LinkedInPost{id, article_id, angle, body, created_by, created_at, updated_at}`.

- [ ] **Step 1: Write the failing test**

Create `rankforge/backend/tests/test_linkedin.py`:

```python
"""LinkedIn post generator — models, prompt builder, CRUD service, routes (hermetic)."""

import pytest
from pydantic import ValidationError

from rankforge_backend.models.linkedin import (
    ANGLE_SLUGS,
    LinkedInGenerate,
    LinkedInUpdate,
)


def test_angle_slugs_are_the_five_presets():
    assert ANGLE_SLUGS == ("key_insight", "lesson", "contrarian", "story", "stat")


def test_generate_defaults_to_key_insight():
    assert LinkedInGenerate().angle == "key_insight"


def test_generate_rejects_unknown_angle():
    with pytest.raises(ValidationError):
        LinkedInGenerate(angle="spicy")


def test_update_rejects_empty_and_overlong_body():
    with pytest.raises(ValidationError):
        LinkedInUpdate(body="")
    with pytest.raises(ValidationError):
        LinkedInUpdate(body="x" * 3001)
    assert LinkedInUpdate(body="ok").body == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run pytest tests/test_linkedin.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rankforge_backend.models.linkedin'`.

- [ ] **Step 3: Write the models**

Create `rankforge/backend/src/rankforge_backend/models/linkedin.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run pytest tests/test_linkedin.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-linkedin && git add rankforge/backend/src/rankforge_backend/models/linkedin.py rankforge/backend/tests/test_linkedin.py && git commit -m "feat(linkedin): request/response models + angle presets

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: CRUD service

**Files:**
- Create: `rankforge/backend/src/rankforge_backend/services/linkedin_posts.py`
- Test: `rankforge/backend/tests/test_linkedin.py` (append)

**Interfaces:**
- Consumes: `Database` (`db.fetch_all`, `db.fetch_one`).
- Produces: `list_posts(db, article_id) -> list[dict]`, `get_post(db, post_id) -> dict | None`, `create_post(db, *, article_id, business_id, angle, body, author_id) -> dict`, `update_post(db, post_id, body) -> dict | None`, `delete_post(db, post_id) -> bool`.

- [ ] **Step 1: Write the failing test (append to `tests/test_linkedin.py`)**

```python
from unittest.mock import MagicMock

from rankforge_backend.services import linkedin_posts as li_svc

AID = "55555555-5555-5555-5555-555555555555"
BID = "11111111-1111-1111-1111-111111111111"
PID = "66666666-6666-6666-6666-666666666666"


def test_create_post_inserts_with_angle_and_author():
    db = MagicMock()
    db.fetch_one.return_value = {"id": PID, "article_id": AID, "angle": "story", "body": "hi"}
    out = li_svc.create_post(
        db, article_id=AID, business_id=BID, angle="story", body="hi", author_id=AID
    )
    sql = db.fetch_one.call_args.args[0].lower()
    assert "insert into public.linkedin_posts" in sql
    assert db.fetch_one.call_args.args[1] == (AID, BID, "story", "hi", AID)
    assert out["angle"] == "story"


def test_list_posts_orders_newest_first():
    db = MagicMock()
    db.fetch_all.return_value = []
    li_svc.list_posts(db, AID)
    sql = db.fetch_all.call_args.args[0].lower()
    assert "where article_id = %s" in sql
    assert "order by created_at desc" in sql


def test_delete_post_returns_bool():
    db = MagicMock()
    db.fetch_one.return_value = {"id": PID}
    assert li_svc.delete_post(db, PID) is True
    db.fetch_one.return_value = None
    assert li_svc.delete_post(db, PID) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run pytest tests/test_linkedin.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rankforge_backend.services.linkedin_posts'`.

- [ ] **Step 3: Write the service**

Create `rankforge/backend/src/rankforge_backend/services/linkedin_posts.py`:

```python
"""CRUD over public.linkedin_posts. Org-scoping is enforced by the route (_guard_article),
not here — this layer is pure data access (mirrors services/comments.py)."""

from typing import Any
from uuid import UUID

from ..db import Database

_COLS = "id, article_id, angle, body, created_by, created_at, updated_at"


def list_posts(db: Database, article_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_COLS} from public.linkedin_posts "
        "where article_id = %s order by created_at desc",
        (article_id,),
    )


def get_post(db: Database, post_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_COLS} from public.linkedin_posts where id = %s", (post_id,)
    )


def create_post(
    db: Database,
    *,
    article_id: UUID,
    business_id: UUID,
    angle: str,
    body: str,
    author_id: Any = None,
) -> dict[str, Any]:
    return db.fetch_one(
        "insert into public.linkedin_posts "
        "(article_id, business_id, angle, body, created_by) "
        f"values (%s, %s, %s, %s, %s) returning {_COLS}",
        (article_id, business_id, angle, body, author_id),
    )


def update_post(db: Database, post_id: UUID, body: str) -> dict[str, Any] | None:
    return db.fetch_one(
        f"update public.linkedin_posts set body = %s, updated_at = now() "
        f"where id = %s returning {_COLS}",
        (body, post_id),
    )


def delete_post(db: Database, post_id: UUID) -> bool:
    row = db.fetch_one(
        "delete from public.linkedin_posts where id = %s returning id", (post_id,)
    )
    return row is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run pytest tests/test_linkedin.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-linkedin && git add rankforge/backend/src/rankforge_backend/services/linkedin_posts.py rankforge/backend/tests/test_linkedin.py && git commit -m "feat(linkedin): CRUD service over linkedin_posts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Generation service (prompt builder + LLM call)

**Files:**
- Create: `rankforge/backend/src/rankforge_backend/services/linkedin_gen.py`
- Test: `rankforge/backend/tests/test_linkedin.py` (append)

**Interfaces:**
- Consumes: `PowabaseClient.run_agent`, `ensure_agent`, `generation.get_article`, `business_profiles.get_profile`, `linking.canonical_url`, `get_settings().public_base_url`.
- Produces:
  - `build_linkedin_prompt(*, title: str, content_md: str, brand: dict, angle: str, article_url: str | None) -> str`
  - `async generate_post(client, db, article_id, angle) -> str` (raises `ValueError` when the article has no content, `RuntimeError` on empty/failed generation).
  - Constants `LINKEDIN_MODEL = "claude-opus-4-7"`, `LINKEDIN_AGENT_NAME = "rankforge-linkedin"`.

- [ ] **Step 1: Write the failing test (append to `tests/test_linkedin.py`)**

```python
from unittest.mock import AsyncMock

from rankforge_backend.services import linkedin_gen as li_gen


def test_build_prompt_includes_angle_clause_and_truncates():
    long_body = "word " * 5000  # ~25k chars
    msg = li_gen.build_linkedin_prompt(
        title="Governed BaaS",
        content_md=long_body,
        brand={"description": "We build a governed BaaS.", "niche": "devtools"},
        angle="contrarian",
        article_url=None,
    )
    assert "Governed BaaS" in msg
    assert li_gen._ANGLE_CLAUSES["contrarian"] in msg
    # content is truncated to 16k chars of the article body
    assert long_body[:16000] in msg
    assert len(long_body) > 16000 and long_body not in msg
    # no link instruction when there's no url
    assert "Full write-up" not in msg


def test_build_prompt_adds_link_line_when_url_present():
    msg = li_gen.build_linkedin_prompt(
        title="T", content_md="body", brand={}, angle="stat",
        article_url="https://blog.acme.com/governed-baas",
    )
    assert "https://blog.acme.com/governed-baas" in msg
    assert "Full write-up" in msg


async def test_generate_post_returns_agent_text(monkeypatch):
    article = {"id": AID, "business_id": BID, "title": "T",
               "content_md": "# T\n\nreal content", "status": "draft"}
    monkeypatch.setattr(li_gen.gen, "get_article", lambda db, aid: article)
    monkeypatch.setattr(li_gen.brands, "get_profile", lambda db, bid: {"name": "Acme"})
    monkeypatch.setattr(li_gen, "ensure_linkedin_agent", AsyncMock(return_value="agent1"))
    client = MagicMock()
    client.run_agent = AsyncMock(return_value={"content": "  A great hook.\n\n#Dev  "})
    out = await li_gen.generate_post(client, MagicMock(), AID, "key_insight")
    assert out == "A great hook.\n\n#Dev"


async def test_generate_post_raises_valueerror_when_no_content(monkeypatch):
    monkeypatch.setattr(
        li_gen.gen, "get_article",
        lambda db, aid: {"id": AID, "business_id": BID, "content_md": "   "},
    )
    with pytest.raises(ValueError):
        await li_gen.generate_post(MagicMock(), MagicMock(), AID, "key_insight")


async def test_generate_post_raises_runtimeerror_on_empty(monkeypatch):
    monkeypatch.setattr(
        li_gen.gen, "get_article",
        lambda db, aid: {"id": AID, "business_id": BID, "content_md": "real", "status": "draft"},
    )
    monkeypatch.setattr(li_gen.brands, "get_profile", lambda db, bid: {})
    monkeypatch.setattr(li_gen, "ensure_linkedin_agent", AsyncMock(return_value="a"))
    client = MagicMock()
    client.run_agent = AsyncMock(return_value={"content": "   "})
    with pytest.raises(RuntimeError):
        await li_gen.generate_post(client, MagicMock(), AID, "key_insight")
```

Note: async tests use the repo's existing `asyncio_mode = "auto"` (see `pyproject.toml`), so no decorator is needed — matches the other `async def test_*` in the suite.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run pytest tests/test_linkedin.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rankforge_backend.services.linkedin_gen'`.

- [ ] **Step 3: Write the generation service**

Create `rankforge/backend/src/rankforge_backend/services/linkedin_gen.py`:

```python
"""Generate a LinkedIn post from a blog article — one synchronous LLM call (opus-4-7,
the writer model, for voice fidelity). Reuses the article writer's anti-AI-tell,
brand-champion guidance, adapted for LinkedIn: an above-the-fold hook is the top
priority, the post may run long, and it closes with a soft discussion question."""

import logging
from typing import Any
from uuid import UUID

from ..config import get_settings
from ..db import Database
from ..powabase import PowabaseClient
from . import business_profiles as brands
from . import generation as gen
from . import linking
from .agents import ensure_agent

log = logging.getLogger("rankforge.linkedin")

LINKEDIN_MODEL = "claude-opus-4-7"
LINKEDIN_AGENT_NAME = "rankforge-linkedin"

# Article body is truncated to bound token cost (mirrors the FAQ generator).
_MAX_ARTICLE_CHARS = 16000

_ANGLE_CLAUSES = {
    "key_insight": (
        "Lead with the single most valuable, non-obvious takeaway from the article."
    ),
    "lesson": (
        "Frame it as a hard-won lesson — a mistake or misconception people have, and "
        "what to do instead."
    ),
    "contrarian": (
        "Take a contrarian angle: challenge a common assumption the article pushes "
        "back on. Stake out the unpopular-but-right position."
    ),
    "story": (
        "Open with a concrete moment or scenario (a real situation, a decision, a "
        "before/after), then draw out the point."
    ),
    "stat": (
        "Anchor the post on a specific number or finding from the article and unpack "
        "why it matters."
    ),
}

_SYSTEM = """You write LinkedIn posts for a brand, repurposing one of its own blog \
articles. Your job: an insightful post that makes the brand look sharp and credible, \
and that people actually stop to read and share. You are NOT a marketer — you are the \
brand's smartest engineer thinking out loud.

ABOVE THE FOLD IS EVERYTHING. LinkedIn hides everything after ~210 characters behind \
"…see more". The opening must stop the scroll and earn the click on its own:
- Open with ONE scroll-stopper: a counterintuitive or bold claim, a specific number or \
surprising result, a sharp question, or a concrete first-person moment.
- The first ~210 characters must land a complete, curiosity-driving thought (an open \
loop the reader wants closed) — never a fragment cut off mid-idea.
- NO throat-clearing ("In today's world…", "As engineers, we all know…", "I've been \
thinking about…"). NO links or hashtags in the first two lines.

STRUCTURE FOR RETENTION:
- Deliver on the hook's promise fast; front-load the payoff.
- One idea per line. Generous whitespace. 1-2 line paragraphs. Built to be skimmed on a \
phone.
- Length is flexible: run as long as every line keeps pulling the reader down, up to \
LinkedIn's 3000-character limit. No filler to hit a length; no cutting a strong point \
short.
- End the body with a genuine discussion question that invites comments (a peer \
question) — NOT a product pitch, NOT "DM us", NOT "Learn more". Comments drive reach.

VOICE (this is the brand's own post):
- Speak AS the brand, first person ("we"/"our"), never as a detached third party. Name \
the brand directly. Never hedge the brand's own capabilities with "claims/asserts/\
purports".
- You may name competitors, but never praise or showcase them.
- Insightful and useful throughout. The brand earns authority by being worth reading, \
not by selling. Not salesy.
- Ground every claim in the article. Invent nothing.

NEVER USE these AI-tell words: delve, tapestry, realm, landscape (as metaphor), \
leverage, robust, seamless, navigate (as metaphor), underscore, foster, harness, \
elevate, unlock, embark, testament, pivotal, crucial, vibrant, boasts, nestled, \
genuinely (as intensifier).

NEVER USE these AI-tell constructions:
- "It's not just X, it's Y" / "This isn't merely X, it's Y".
- The antithesis reframe ("The way forward isn't X. It's Y").
- "Whether you're a beginner or a seasoned pro…".
- "Let's dive in / Let's explore / Buckle up".
- Reflexive rule-of-three triads ("fast, reliable, and scalable").
- "From X to Y" framing. Empty transitions (Moreover, Furthermore, Additionally, That \
said). Use em-dashes sparingly. Vary sentence length. Generic, specificity-free prose \
is the clearest AI tell — be concrete.

OUTPUT: only the post text itself, ready to paste — no preamble, no quotes, no \
"Here's your post". Fixed trailing order: hook + body, then the discussion question as \
the last body line, then (only if a link is provided in the instructions) a blank line \
and a soft "Full write-up → {url}" line, then a blank line and 3-5 specific, relevant \
hashtags (never generic spam)."""


def _brand_voice_block(brand: dict[str, Any]) -> str:
    parts = []
    if brand.get("description"):
        parts.append(f"Brand voice / positioning: {brand['description']}")
    if brand.get("niche"):
        parts.append(f"Niche: {brand['niche']}")
    if brand.get("audience"):
        parts.append(f"Audience: {brand['audience']}")
    name = brand.get("name")
    if name:
        parts.append(f"Brand name: {name}")
    return "\n".join(parts) or "(no brand voice provided — infer from the article)"


def build_linkedin_prompt(
    *,
    title: str,
    content_md: str,
    brand: dict[str, Any],
    angle: str,
    article_url: str | None,
) -> str:
    clause = _ANGLE_CLAUSES.get(angle, _ANGLE_CLAUSES["key_insight"])
    link_line = (
        f'End with a soft "Full write-up → {article_url}" line (after the discussion '
        f"question, before the hashtags)."
        if article_url
        else "Do NOT include any link — the article is not published yet."
    )
    return (
        f"Write a LinkedIn post repurposing this article.\n\n"
        f"ANGLE: {clause}\n\n"
        f"{link_line}\n\n"
        f"{_brand_voice_block(brand)}\n\n"
        f"ARTICLE TITLE: {title}\n\n"
        f"ARTICLE (Markdown):\n{content_md[:_MAX_ARTICLE_CHARS]}"
    )


async def ensure_linkedin_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=LINKEDIN_AGENT_NAME,
        model=LINKEDIN_MODEL,
        system_prompt=_SYSTEM,
        settings={"temperature": 0.5, "max_tokens": 1200},
    )


def _resolve_article_url(
    db: Database, brand: dict[str, Any] | None, article: dict[str, Any]
) -> str | None:
    """The article's live URL, only if it's published — else None (no link line)."""
    if article.get("status") != "published":
        return None
    url = linking.canonical_url(brand, article)
    if url:
        return url
    base = get_settings().public_base_url
    return f"{base.rstrip('/')}/p/{article['id']}" if base else None


async def generate_post(
    client: PowabaseClient, db: Database, article_id: UUID, angle: str
) -> str:
    article = gen.get_article(db, article_id)
    if article is None:
        raise ValueError("article not found")
    content_md = (article.get("content_md") or "").strip()
    if not content_md:
        # Can't repurpose an empty article — the route maps this to 409.
        raise ValueError("article has no content yet")
    brand = (
        brands.get_profile(db, article["business_id"])
        if article.get("business_id")
        else None
    )
    msg = build_linkedin_prompt(
        title=article.get("title") or "",
        content_md=content_md,
        brand=brand or {},
        angle=angle,
        article_url=_resolve_article_url(db, brand, article),
    )
    agent_id = await ensure_linkedin_agent(client)
    try:
        res = await client.run_agent(agent_id, msg)
    except Exception as e:  # noqa: BLE001 — upstream failure → 502 at the route
        log.exception("linkedin generation failed for %s", article_id)
        raise RuntimeError("generation failed") from e
    text = (res.get("content") or "").strip()
    if not text:
        raise RuntimeError("empty generation")
    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run pytest tests/test_linkedin.py -q`
Expected: PASS (12 passed).

- [ ] **Step 5: Run ruff**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run ruff check src/rankforge_backend/services/linkedin_gen.py src/rankforge_backend/services/linkedin_posts.py src/rankforge_backend/models/linkedin.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-linkedin && git add rankforge/backend/src/rankforge_backend/services/linkedin_gen.py rankforge/backend/tests/test_linkedin.py && git commit -m "feat(linkedin): generation service (prompt builder + opus-4-7 call)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: API routes

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/routes/articles.py` (imports near top; new endpoints appended after the comments endpoints, end of file)
- Modify: `rankforge/backend/src/rankforge_backend/services/generation.py` (docstring note in `delete_article`)
- Test: `rankforge/backend/tests/test_linkedin.py` (append)

**Interfaces:**
- Consumes: `_guard_article(db, article_id, user) -> dict` (existing, returns the article), `rate_limit`, `li_svc.*`, `li_gen.generate_post`.
- Produces: 4 endpoints under `/api/articles/{article_id}/linkedin-posts`.

- [ ] **Step 1: Write the failing route tests (append to `tests/test_linkedin.py`)**

```python
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.deps import get_db, get_powabase
from rankforge_backend.services import generation as gen_svc


def _brand_db():
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": __import__("uuid").UUID(ADMIN_ORG)}
    return db


def _client(db=None, pb=None, user=None):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db if db is not None else _brand_db()
    app.dependency_overrides[get_powabase] = lambda: pb if pb is not None else MagicMock()
    return TestClient(with_auth(app, user) if user else with_auth(app))


def test_list_linkedin_posts_route(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_svc, "list_posts", lambda db, aid: [
        {"id": PID, "article_id": AID, "angle": "story", "body": "hi",
         "created_by": None, "created_at": "2026-07-16T00:00:00Z",
         "updated_at": "2026-07-16T00:00:00Z"}
    ])
    resp = _client().get(f"/api/articles/{AID}/linkedin-posts")
    assert resp.status_code == 200
    assert resp.json()[0]["angle"] == "story"


def test_generate_linkedin_post_route(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_gen, "generate_post", AsyncMock(return_value="Hook line.\n\n#Dev"))
    monkeypatch.setattr(li_svc, "create_post", lambda db, **k: {
        "id": PID, "article_id": AID, "angle": k["angle"], "body": k["body"],
        "created_by": None, "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z"})
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "key_insight"})
    assert resp.status_code == 201
    assert resp.json()["body"] == "Hook line.\n\n#Dev"


def test_generate_409_when_article_has_no_content(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    async def boom(*a, **k):
        raise ValueError("article has no content yet")
    monkeypatch.setattr(li_gen, "generate_post", boom)
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "key_insight"})
    assert resp.status_code == 409


def test_generate_502_on_upstream_failure(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    async def boom(*a, **k):
        raise RuntimeError("generation failed")
    monkeypatch.setattr(li_gen, "generate_post", boom)
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "key_insight"})
    assert resp.status_code == 502


def test_generate_422_on_bad_angle(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "spicy"})
    assert resp.status_code == 422


def test_update_linkedin_post_route(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_svc, "get_post", lambda db, pid: {"id": PID, "article_id": AID})
    monkeypatch.setattr(li_svc, "update_post", lambda db, pid, body: {
        "id": PID, "article_id": AID, "angle": "story", "body": body,
        "created_by": None, "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z"})
    resp = _client().patch(f"/api/articles/{AID}/linkedin-posts/{PID}", json={"body": "edited"})
    assert resp.status_code == 200
    assert resp.json()["body"] == "edited"


def test_update_404_when_post_not_on_article(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_svc, "get_post", lambda db, pid: {"id": PID, "article_id": "99999999-9999-9999-9999-999999999999"})
    resp = _client().patch(f"/api/articles/{AID}/linkedin-posts/{PID}", json={"body": "edited"})
    assert resp.status_code == 404


def test_delete_linkedin_post_route(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_svc, "get_post", lambda db, pid: {"id": PID, "article_id": AID})
    monkeypatch.setattr(li_svc, "delete_post", lambda db, pid: True)
    resp = _client().delete(f"/api/articles/{AID}/linkedin-posts/{PID}")
    assert resp.status_code == 204


def test_cross_org_404(monkeypatch):
    # Article's brand is in another org → _guard_article 404s before any work.
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": __import__("uuid").UUID("77777777-7777-7777-7777-777777777777")}
    resp = _client(db).get(f"/api/articles/{AID}/linkedin-posts")
    assert resp.status_code == 404
```

Add these imports at the top of the appended block (next to the other test imports already in the file): `from rankforge_backend.services import linkedin_gen as li_gen`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run pytest tests/test_linkedin.py -q`
Expected: FAIL (404s / 405s — the routes don't exist yet).

- [ ] **Step 3: Add imports to `routes/articles.py`**

In `rankforge/backend/src/rankforge_backend/routes/articles.py`, add to the model imports block (the `from ..models.article import (...)` area) a new import line, and add the service imports near the other `from ..services import ... as ...` lines:

```python
from ..models.linkedin import LinkedInGenerate, LinkedInPost, LinkedInUpdate
```
```python
from ..services import linkedin_gen as li_gen
from ..services import linkedin_posts as li_svc
```

- [ ] **Step 4: Append the endpoints to `routes/articles.py`**

Add at the end of the file (after the comment endpoints):

```python
# --- LinkedIn posts (repurpose an article into shareable LinkedIn variants) ---
def _guard_li_post(db: Database, article_id: UUID, post_id: UUID) -> dict:
    """Load a post and confirm it belongs to the named (already org-guarded) article.
    Mirrors the comment endpoints' 404 so we don't leak which post ids exist elsewhere."""
    post = li_svc.get_post(db, post_id)
    if post is None or str(post["article_id"]) != str(article_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    return post


@router.get("/{article_id}/linkedin-posts", response_model=list[LinkedInPost])
def list_linkedin_posts(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """All LinkedIn post variants for this article (newest first). Any workspace member."""
    _guard_article(db, article_id, user)
    return li_svc.list_posts(db, article_id)


@router.post(
    "/{article_id}/linkedin-posts",
    response_model=LinkedInPost,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("linkedin:generate"))],
)
async def generate_linkedin_post(
    article_id: UUID,
    payload: LinkedInGenerate,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Generate a new LinkedIn variant from the article (synchronous LLM call)."""
    article = _guard_article(db, article_id, user)
    try:
        body = await li_gen.generate_post(pb, db, article_id, payload.angle)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return li_svc.create_post(
        db,
        article_id=article_id,
        business_id=article["business_id"],
        angle=payload.angle,
        body=body,
        author_id=user.id,
    )


@router.patch(
    "/{article_id}/linkedin-posts/{post_id}", response_model=LinkedInPost
)
def update_linkedin_post(
    article_id: UUID,
    post_id: UUID,
    payload: LinkedInUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Edit a variant's text. Any workspace member."""
    _guard_article(db, article_id, user)
    _guard_li_post(db, article_id, post_id)
    return li_svc.update_post(db, post_id, payload.body)


@router.delete(
    "/{article_id}/linkedin-posts/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_linkedin_post(
    article_id: UUID,
    post_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _guard_article(db, article_id, user)
    _guard_li_post(db, article_id, post_id)
    li_svc.delete_post(db, post_id)
```

- [ ] **Step 5: Update the `delete_article` docstring note**

In `rankforge/backend/src/rankforge_backend/services/generation.py`, find `delete_article`'s docstring listing cascaded children (comments, versions, link suggestions, broken-link findings, publication records) and add `LinkedIn posts` to that list so the cascade is documented.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run pytest tests/test_linkedin.py -q`
Expected: PASS (21 passed).

- [ ] **Step 7: Run the full backend suite + ruff**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/backend && /home/zipeng/.local/bin/uv run pytest -q && /home/zipeng/.local/bin/uv run ruff check src/rankforge_backend tests/test_linkedin.py`
Expected: all pass, `All checks passed!`.

- [ ] **Step 8: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-linkedin && git add rankforge/backend/src/rankforge_backend/routes/articles.py rankforge/backend/src/rankforge_backend/services/generation.py rankforge/backend/tests/test_linkedin.py && git commit -m "feat(linkedin): CRUD + generate endpoints on the article router

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Frontend API client

**Files:**
- Modify: `rankforge/frontend/src/lib/api.ts` (append a new section before the closing of the file, after `briefsApi`)

**Interfaces:**
- Consumes: existing `request<T>` helper, `API_BASE_URL`.
- Produces: `type Angle`, `ANGLES: { slug: Angle; label: string }[]`, `interface LinkedInPost`, `linkedInApi.{list,generate,update,remove}`.

- [ ] **Step 0: Install frontend deps (this worktree is fresh — no `node_modules` yet)**

Run:
```bash
cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/frontend && export PATH="/home/zipeng/.nvm/versions/node/v22.20.0/bin:$PATH" && npm install 2>&1 | tail -3
```
Expected: `added N packages` / `found 0 vulnerabilities`. (Needed before any tsc/lint/build step below.)

- [ ] **Step 1: Add the types + client to `api.ts`**

Append to `rankforge/frontend/src/lib/api.ts`:

```ts
// --- LinkedIn posts (repurpose an article) ---
export type Angle = "key_insight" | "lesson" | "contrarian" | "story" | "stat";

/** Slug → label. Keep in sync with backend models/linkedin.py ANGLE_SLUGS. */
export const ANGLES: { slug: Angle; label: string }[] = [
  { slug: "key_insight", label: "Key insight" },
  { slug: "lesson", label: "Lesson learned" },
  { slug: "contrarian", label: "Contrarian take" },
  { slug: "story", label: "Story / behind-the-scenes" },
  { slug: "stat", label: "Stat highlight" },
];

export interface LinkedInPost {
  id: string;
  article_id: string;
  angle: string;
  body: string;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export const linkedInApi = {
  list: (articleId: string) =>
    request<LinkedInPost[]>(`/api/articles/${articleId}/linkedin-posts`),
  generate: (articleId: string, angle: Angle) =>
    request<LinkedInPost>(`/api/articles/${articleId}/linkedin-posts`, {
      method: "POST",
      body: JSON.stringify({ angle }),
    }),
  update: (articleId: string, postId: string, body: string) =>
    request<LinkedInPost>(
      `/api/articles/${articleId}/linkedin-posts/${postId}`,
      { method: "PATCH", body: JSON.stringify({ body }) }
    ),
  remove: (articleId: string, postId: string) =>
    request<void>(`/api/articles/${articleId}/linkedin-posts/${postId}`, {
      method: "DELETE",
    }),
};
```

- [ ] **Step 2: Type-check**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/frontend && node node_modules/typescript/bin/tsc --noEmit`
Expected: exit 0, no output.

- [ ] **Step 3: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-linkedin && git add rankforge/frontend/src/lib/api.ts && git commit -m "feat(linkedin): api client + angle presets

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Frontend hooks

**Files:**
- Create: `rankforge/frontend/src/lib/hooks/useLinkedIn.ts`

**Interfaces:**
- Consumes: `linkedInApi`, `Angle` from `@/lib/api`.
- Produces: `useLinkedInPosts(articleId)`, `useGenerateLinkedInPost(articleId)`, `useUpdateLinkedInPost(articleId)`, `useDeleteLinkedInPost(articleId)` — query key `["linkedin", articleId]`.

- [ ] **Step 1: Write the hooks**

Create `rankforge/frontend/src/lib/hooks/useLinkedIn.ts`:

```ts
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { linkedInApi, type Angle } from "@/lib/api";

export function useLinkedInPosts(articleId: string) {
  return useQuery({
    queryKey: ["linkedin", articleId],
    queryFn: () => linkedInApi.list(articleId),
    enabled: !!articleId,
  });
}

export function useGenerateLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (angle: Angle) => linkedInApi.generate(articleId, angle),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["linkedin", articleId] }),
  });
}

export function useUpdateLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ postId, body }: { postId: string; body: string }) =>
      linkedInApi.update(articleId, postId, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["linkedin", articleId] }),
  });
}

export function useDeleteLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (postId: string) => linkedInApi.remove(articleId, postId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["linkedin", articleId] }),
  });
}
```

- [ ] **Step 2: Type-check**

Run: `cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/frontend && node node_modules/typescript/bin/tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-linkedin && git add rankforge/frontend/src/lib/hooks/useLinkedIn.ts && git commit -m "feat(linkedin): react-query hooks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: LinkedInPanel component

**Files:**
- Create: `rankforge/frontend/src/components/LinkedInPanel.tsx`

**Interfaces:**
- Consumes: `useLinkedInPosts/useGenerate…/useUpdate…/useDelete…`, `ANGLES`, `type LinkedInPost` from `@/lib/api`; shadcn `Button`, `Textarea`; `sonner` toast; `lucide-react` icons.
- Produces: `export function LinkedInPanel({ articleId, articleReady }: { articleId: string; articleReady: boolean })`.

- [ ] **Step 1: Write the component**

Create `rankforge/frontend/src/components/LinkedInPanel.tsx`:

```tsx
"use client";

import * as React from "react";
// Share2 (not a brand "Linkedin" icon — lucide has been deprecating brand icons).
import { Copy, Loader2, Share2, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ANGLES, type Angle, type LinkedInPost } from "@/lib/api";
import {
  useDeleteLinkedInPost,
  useGenerateLinkedInPost,
  useLinkedInPosts,
  useUpdateLinkedInPost,
} from "@/lib/hooks/useLinkedIn";

const FOLD_CHARS = 210; // LinkedIn hides everything after ~this behind "…see more"
const MAX_CHARS = 3000;

const angleLabel = (slug: string) =>
  ANGLES.find((a) => a.slug === slug)?.label ?? slug;

export function LinkedInPanel({
  articleId,
  articleReady,
}: {
  articleId: string;
  articleReady: boolean;
}) {
  const posts = useLinkedInPosts(articleId);
  const generate = useGenerateLinkedInPost(articleId);
  const [angle, setAngle] = React.useState<Angle>("key_insight");

  function onGenerate() {
    generate.mutate(angle, {
      onSuccess: () => toast.success("LinkedIn post generated"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Generation failed"),
    });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2 rounded-md border border-border p-3">
        <label className="text-xs font-medium text-muted-foreground">
          Generate a LinkedIn post
        </label>
        <div className="flex gap-2">
          <select
            value={angle}
            onChange={(e) => setAngle(e.target.value as Angle)}
            className="h-9 flex-1 rounded-md border border-input bg-background px-2 text-sm"
          >
            {ANGLES.map((a) => (
              <option key={a.slug} value={a.slug}>
                {a.label}
              </option>
            ))}
          </select>
          <Button
            variant="gold"
            size="sm"
            onClick={onGenerate}
            disabled={generate.isPending || !articleReady}
          >
            {generate.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Sparkles />
            )}
            Generate variant
          </Button>
        </div>
        <p className="text-[11px] text-muted-foreground">
          {articleReady
            ? "Uses credits. Each generation adds a new variant you can edit or delete."
            : "Generate the article draft first — there's no content to repurpose yet."}
        </p>
      </div>

      {posts.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (posts.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No posts yet — pick an angle and generate one.
        </p>
      ) : (
        <ul className="space-y-3">
          {(posts.data ?? []).map((p) => (
            <li key={p.id}>
              <PostCard articleId={articleId} post={p} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PostCard({
  articleId,
  post,
}: {
  articleId: string;
  post: LinkedInPost;
}) {
  const update = useUpdateLinkedInPost(articleId);
  const del = useDeleteLinkedInPost(articleId);
  const [body, setBody] = React.useState(post.body);
  React.useEffect(() => setBody(post.body), [post.body]);

  const dirty = body !== post.body;
  const overFold = body.length > FOLD_CHARS;
  const overMax = body.length > MAX_CHARS;

  function onCopy() {
    navigator.clipboard.writeText(body).then(
      () => toast.success("Copied to clipboard"),
      () => toast.error("Couldn't copy — select the text and copy manually")
    );
  }

  function onSave() {
    update.mutate(
      { postId: post.id, body },
      {
        onSuccess: () => toast.success("Saved"),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Save failed"),
      }
    );
  }

  function onDelete() {
    if (!window.confirm("Delete this variant?")) return;
    del.mutate(post.id, {
      onSuccess: () => toast.success("Deleted"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Delete failed"),
    });
  }

  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <div className="flex items-center gap-2">
        <Share2 className="size-3.5 text-[rgb(var(--ember))]" />
        <span className="rounded bg-secondary px-1.5 py-0.5 text-xs text-muted-foreground">
          {angleLabel(post.angle)}
        </span>
      </div>

      {/* Above-the-fold preview: what shows before "…see more". */}
      <div className="rounded border border-dashed border-border bg-muted/40 p-2 text-xs">
        <span className="text-foreground">{body.slice(0, FOLD_CHARS)}</span>
        {overFold && <span className="text-muted-foreground">… see more</span>}
        <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          Above the fold ({Math.min(body.length, FOLD_CHARS)}/{FOLD_CHARS})
        </div>
      </div>

      <Textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={10}
        className="text-sm"
      />

      <div className="flex items-center gap-2">
        <span
          className={
            overMax
              ? "text-[11px] text-destructive"
              : "text-[11px] text-muted-foreground"
          }
        >
          {body.length}/{MAX_CHARS}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="outline" size="sm" onClick={onCopy}>
            <Copy /> Copy
          </Button>
          <Button
            variant="gold"
            size="sm"
            onClick={onSave}
            disabled={!dirty || overMax || body.trim().length === 0 || update.isPending}
          >
            {update.isPending && <Loader2 className="animate-spin" />}
            Save
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-destructive"
            onClick={onDelete}
            disabled={del.isPending}
          >
            {del.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
          </Button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Type-check + lint**

Run:
```bash
cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/frontend && node node_modules/typescript/bin/tsc --noEmit && npm run lint -- src/components/LinkedInPanel.tsx
```
Expected: tsc exit 0; eslint no errors (0 problems). (`Copy`, `Share2`, `Sparkles`, `Trash2`, `Loader2` are all core lucide-react icons; the Task 9 build is the final confirmation.)

- [ ] **Step 3: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-linkedin && git add rankforge/frontend/src/components/LinkedInPanel.tsx && git commit -m "feat(linkedin): LinkedInPanel (angle picker, variant cards, above-the-fold preview)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Wire the "LinkedIn" tab into the article page

**Files:**
- Modify: `rankforge/frontend/src/app/brands/[id]/articles/[articleId]/page.tsx`

**Interfaces:**
- Consumes: `LinkedInPanel` from `@/components/LinkedInPanel`.

- [ ] **Step 1: Import the panel**

In `page.tsx`, add near the other component imports:
```tsx
import { LinkedInPanel } from "@/components/LinkedInPanel";
```

- [ ] **Step 2: Add "LinkedIn" to the tab state union and the tab tuple**

Find the tab state:
```tsx
const [tab, setTab] = useState<
  "SEO" | "GEO" | "Readability" | "Grounding" | "Links" | "Comments"
>("SEO");
```
Change the union to include `"LinkedIn"`:
```tsx
const [tab, setTab] = useState<
  "SEO" | "GEO" | "Readability" | "Grounding" | "Links" | "Comments" | "LinkedIn"
>("SEO");
```
Find the tab tuple that is `.map`ped for the tab buttons:
```tsx
{(["SEO", "GEO", "Readability", "Grounding", "Links", "Comments"] as const).map((t) => {
```
Add `"LinkedIn"`:
```tsx
{(["SEO", "GEO", "Readability", "Grounding", "Links", "Comments", "LinkedIn"] as const).map((t) => {
```

- [ ] **Step 3: Add the tab body branch**

The tab body is a ternary chain. The relevant part reads:
```tsx
        {tab === "Comments" ? (
          <div className="min-h-0 flex-1 p-4">
            <CommentsPanel articleId={articleId} />
          </div>
        ) : tab === "Links" ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <InternalLinksPanel
              articleId={articleId}
              brandId={id}
              onLocate={locate}
            />
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {/* …eval bodies (Grounding / EvalBody)… */}
```
Insert a **LinkedIn** branch between the Links branch and the final `: (` eval fallback. Use the in-scope `articleId` (from params, same as the neighbors) and the loaded article `a` (from `useArticle(articleId)`; may be undefined while loading → use `a?.`):
```tsx
        ) : tab === "Links" ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <InternalLinksPanel
              articleId={articleId}
              brandId={id}
              onLocate={locate}
            />
          </div>
        ) : tab === "LinkedIn" ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <LinkedInPanel
              articleId={articleId}
              articleReady={a?.generation_status === "done" && !!a?.content_md}
            />
          </div>
        ) : (
```

- [ ] **Step 4: Type-check, lint, and build**

Run:
```bash
cd /home/zipeng/worktrees/rankforge-linkedin/rankforge/frontend && node node_modules/typescript/bin/tsc --noEmit && npm run lint -- "src/app/brands/[id]/articles/[articleId]/page.tsx" src/components/LinkedInPanel.tsx && NEXT_PUBLIC_SITE_URL=http://localhost:3007 npm run build 2>&1 | tail -8
```
Expected: tsc exit 0; eslint 0 problems; `next build` exits 0 and lists routes including `/brands/[id]/articles/[articleId]`.

- [ ] **Step 5: Manual end-to-end verification**

Boot local (backend on 8088, frontend on 3007) from this worktree, open a brand → Articles → a **done** article → the **LinkedIn** tab. Pick an angle → **Generate variant** → confirm a post appears with an angle badge, an above-the-fold preview, editable text, **Copy** (clipboard), and **Delete**. Confirm the char counter and the "…see more" cutoff behave. Regenerate with a different angle → a second variant appears.

- [ ] **Step 6: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-linkedin && GIT_LITERAL_PATHSPECS=1 git add "rankforge/frontend/src/app/brands/[id]/articles/[articleId]/page.tsx" && git commit -m "feat(linkedin): add the LinkedIn tab to the article page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Data model (`linkedin_posts`, org RLS, cascade) → Task 1. ✓
- Angles (5 presets, single source) → Task 2 (`ANGLE_SLUGS`/`Angle`), Task 4 (`_ANGLE_CLAUSES`), Task 6 (`ANGLES`). ✓
- Generation (sync, opus-4-7, reused anti-AI-tell + above-the-fold hook + flexible length + engagement close + hashtags + published-only link) → Task 4 (`_SYSTEM`, `build_linkedin_prompt`, `_resolve_article_url`). ✓
- API + permissions (4 endpoints, any member, org guard, 409/502/422/404) → Task 5. ✓
- Frontend (tab, panel, angle picker, editable variant cards, copy, delete, char count, above-the-fold marker) → Tasks 6-9. ✓
- Error handling (409 no content, 502 upstream/empty, 422 angle/body, 404 cross-org/post) → Tasks 4-5 tests. ✓
- Testing (generate happy/409/502, angle 422, list/edit/delete, org 404, pure prompt-builder units) → Tasks 2-5. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `generate_post`/`build_linkedin_prompt`/`_ANGLE_CLAUSES`/`ensure_linkedin_agent` names match across Tasks 4-5 tests and impl; `linkedInApi`/`ANGLES`/`LinkedInPost`/`Angle` match across Tasks 6-8; query key `["linkedin", articleId]` consistent Tasks 7-8; angle slugs identical in models, gen clauses, and frontend ANGLES.

**Note for the implementer:** In Task 5 the appended test block reuses `AID`/`BID`/`PID`/`li_svc`/`li_gen`/`MagicMock` from earlier in the same test file — they're already defined by Tasks 3-4. Only `li_gen` needs the extra import if not already present.
