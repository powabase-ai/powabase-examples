# CLAUDE.md — RankForge

Guidance for Claude Code working in the RankForge repo. RankForge is a production
SEO/GEO blog-article platform built **on top of Powabase** (the AI BaaS), and an
open-source example app. Read [`docs/PRD.md`](docs/PRD.md) and
[`docs/architecture.md`](docs/architecture.md) first.

## What this is

A **consumer** of a Powabase project — not part of the Powabase platform itself.
The project owns the per-project Postgres; RankForge's `public.*` tables and
Powabase's `ai.*` tables live in the same DB. Two access paths:

1. **Direct psycopg** (`backend/src/rankforge_backend/db.py`) for all `public.*` app
   data and read-only `ai.*` — the **default**.
2. **`/api/*` HTTP** (`backend/src/rankforge_backend/powabase/client.py`, Service
   Role key) for platform-managed work: agents, workflows, knowledge bases, sources.

HTTP is the deviation that needs justification (the platform owns async work /
side effects). When in doubt, direct DB.

## Layout

| Dir | Stack | Purpose |
|---|---|---|
| `backend/` | FastAPI · psycopg3 pool · Pydantic v2 · `uv` | API surface |
| `frontend/` | Next.js 16 · App Router · TS · Tailwind · shadcn/ui | Editorial UI |
| `docs/` | — | PRD + architecture (the source of truth for scope) |

## Running

- Python: **`uv run`** is canonical. **`uv run pytest`, never bare `pytest`.**
- Tests are hermetic — mock at the `Database` / Powabase `client` boundary, no live
  DB or network.
- Full stack: `docker compose up --build`. Hot-reload: `uv run uvicorn` + `next dev`.

## Powabase footguns (don't relearn these the hard way)

- **Two headers or 401.** Every `/api/*` call needs both `apikey` **and**
  `Authorization: Bearer <key>`.
- **Service Role key / JWT secret / Database URL are server-side only.** Frontend
  gets only the **Anon** key. Agent/generation endpoints never go client-side
  (agent DB tools run as RLS-bypassing superuser).
- **App tables: enable RLS.** New `public` tables default to RLS **OFF**.
- Agent `temperature`/tuning → nest in `settings` (top-level dropped). Tool use
  (incl. KB search) requires `/run/stream`, not `/run`. Source extraction is a
  barrier before KB add. Workflows have **10** block types (`starter`/`response`,
  not `input`/`output`/`llm`). Billing `402` → no retry; `503`/`429` → backoff.
- The `/api/*` surface is evolving — **verify shapes against
  `https://docs.powabase.ai`** (append `.md` to a page path) before trusting a field.
- There is a **`powabase` skill** available — use it for any Powabase integration
  work; it has the depth references.

## Conventions

- Pydantic v2 (`pydantic_settings`), psycopg **v3**, Ruff (line-length 88).
- Next.js **16** App Router, TanStack Query for server state.
- Follow the PRD phases/milestones; surface tradeoffs before implementing; keep
  changes surgical.
