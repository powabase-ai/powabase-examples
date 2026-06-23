# RankForge — Architecture

How RankForge is built and how it consumes Powabase. Pairs with [`PRD.md`](PRD.md).

## 1. Topology

```
┌─────────────────┐      HTTP/JSON       ┌──────────────────────┐
│  Next.js (3000) │ ───────────────────▶ │   FastAPI backend     │
│  App Router UI  │ ◀─────────────────── │   (8000)              │
└─────────────────┘   SSE (gen stream)   └─────────┬────────────┘
        │                                          │
        │ GoTrue auth (Anon key)                   │ two paths into Powabase:
        ▼                                          │
┌─────────────────┐                                ├── psycopg pool ──▶ per-project Postgres
│ Powabase GoTrue │                                │      (public.* app tables, read ai.*)
│  /auth/v1/*     │                                │
└─────────────────┘                                └── /api/* (Service Role) ──▶ agents,
                                                         workflows, knowledge bases, sources
```

**Why a split (not Next-only):** matches the judocu-ai production pattern, keeps the
Service Role key and all generation logic server-side (security, see §5), and lets
the generation pipeline run as long-lived server work independent of the UI.

## 2. Backend ↔ Powabase: two access paths

RankForge is a **consumer** of a Powabase project, exactly like judocu-ai. The
project owns the per-project Postgres; RankForge's tables (`public.articles`, …) and
Powabase's tables (`ai.knowledge_bases`, `ai.runs`, …) live in the **same database**,
different schemas.

**Direct Postgres (psycopg) — the default for app data.**
- All RankForge `public.*` CRUD goes through a pooled `Database()` (see
  `backend/src/rankforge_backend/db.py`), parameterized SQL.
- Read Powabase `ai.*` state directly when convenient (e.g. run status) instead of
  polling HTTP — same DB, role has access.
- Connection string = the project's **Database URL** (`POWABASE_DATABASE_URL`).

**`/api/*` (HTTP, Service Role key) — for platform-managed work.**
Use HTTP, not raw SQL, for anything the platform coordinates:
- **Agents**: research (SERP/competitor) via `POST /api/agents/{id}/run/stream`
  (the tool-bearing path — `web_search`/`web_scrape` only run here, not `/run`).
- **Workflows**: the generation pipeline via `POST /api/workflows/{id}/execute`.
- **Knowledge bases / sources**: brand-grounding KB management
  (`/api/knowledge-bases`, `/api/sources`).
- Mutations to platform-owned resources go through the API (it validates, enforces
  constraints, triggers Celery) — don't sidestep with raw INSERTs into `ai.*`.

**The rule (from judocu-ai):** default to direct DB; HTTP is the deviation that
needs a reason (it's needed when the platform owns the side effects / async work).

## 3. Powabase client

`backend/src/rankforge_backend/powabase/client.py` wraps the `/api/*` surface:
- Always sends **both** headers — `apikey` and `Authorization: Bearer <key>` (one
  header → 401, the #1 Powabase footgun).
- Base URL = **Project URL** (`POWABASE_BASE_URL`), key = **Service Role**
  (`POWABASE_SERVICE_ROLE_KEY`), server-side only.
- Methods scoped to what we use: `run_agent_stream`, `execute_workflow`,
  `upload_source`, `create_kb`, `add_source_to_kb`, `get_run`.
- Treats the `/api/*` surface as evolving — shapes verified against
  `https://docs.powabase.ai` (append `.md` to a page path), not assumed.

Known gotchas baked into the client / services:
- Agent `temperature` and tuning fields **must nest in `settings`** (top-level is
  silently dropped).
- Source extraction is a **barrier**: poll `GET /api/sources/{id}` to `extracted`
  before adding to a KB; re-upload of identical bytes → `409 duplicate_source`
  (reuse it).
- Billing: `402 insufficient_credits` → don't retry, surface `renews_at`; `503` →
  retry with backoff. Workflow `/execute` is 20/min/user → `429`, back off.

## 4. Generation pipeline {#generation}

**Decision (default, OPEN in PRD §7.1): a Powabase Workflow DAG.**

```
starter → [research agent] → [build brief] → [outline] → [draft] → [GEO optimize] → response
```

Rationale: a Workflow gives inspectable, individually re-runnable, streamable stages
— a better fit for a multi-stage editorial pipeline than one opaque agent loop. The
**research** stage is itself an agent block (needs the ReAct loop + web tools); the
deterministic stages (brief assembly, scoring) are code/LLM blocks.

Alternatives considered:
- **Single ReAct agent** with web tools — simpler, conversational, but the whole
  multi-stage process collapses into one loop that's hard to inspect/resume.
- **Orchestration** (supervisor + specialist agents) — overkill until stages need
  genuinely independent multi-domain reasoning.

The backend kicks off the workflow, persists `workflow_run_id`, and streams run
events (SSE) to the UI for per-stage progress. Stage outputs land in `research_runs`
/ `briefs` / `articles`.

> Powabase workflows have exactly **10 block types**; `input`/`output`/`llm` are not
> real (use `starter` / `response`). Confirm the block graph against the live docs
> when we build M2.

## 5. Security {#security}

- **Service Role key, JWT secret, Database URL: server-side only.** Never reach the
  browser. The frontend gets only the **Anon (Publishable)** key, used for GoTrue
  sign-in and any RLS-respecting PostgREST reads.
- **Agent/generation endpoints stay server-side.** Powabase does **not** forward
  end-user JWTs to agent tools; `database_query`/`database_write` run on the DB
  **superuser** connection (RLS bypassed). Exposing the tool-bearing run path to
  clients = full project DB access. RankForge only calls these from the trusted
  FastAPI backend, after its own authz check.
- **`ai.*` RLS is project-wide**, not per-user — don't rely on it to isolate team
  members; do app-level authz in the backend.
- **App tables: RLS ON from day one.** New `public` tables default to **RLS OFF**
  (world read/write with the Anon key) — `0001_init.sql` enables RLS and adds
  team-visible policies as step one.

## 6. Local dev & deployment

- **Docker Compose** (`docker-compose.yml`) runs `backend` + `frontend` for an
  integrated stack; mirrors judocu-ai's layout (sans LanguageTool).
- **Hot-reload dev**: `uv run uvicorn ... --reload` (backend) and `next dev`
  (frontend) without Docker.
- **Migrations**: app schema in `backend/schema/*.sql`, applied to the project's
  Database URL. (We may move to Alembic once the schema stabilizes — **OPEN**.)
- Powabase itself is **not** run by this repo — it's the managed BaaS we point at.

## 7. Repo conventions

- Python: `uv` for everything (`uv run pytest`, never bare `pytest`); Pydantic v2
  (`pydantic_settings.BaseSettings`); psycopg **v3** (not psycopg2); Ruff.
- Frontend: Next.js **16** (App Router), TypeScript, Tailwind v4, shadcn/ui,
  TanStack Query for server state.
- Tests run hermetically — mock at the `Database` / Powabase `client` boundary; no
  live DB or network in unit tests.
