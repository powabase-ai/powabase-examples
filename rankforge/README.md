# RankForge

**Open-source SEO/GEO blog-article platform, built on [Powabase](https://docs.powabase.ai).**

RankForge researches, drafts, and optimizes long-form blog articles for both
classic **SEO** (search engines) and **GEO** (Generative Engine Optimization —
being cited by AI answer engines like ChatGPT, Perplexity, and Google AI
Overviews). It's a real content tool *and* a flagship open-source example of building
a production app on the Powabase AI BaaS.

RankForge is **not a standalone app** — it's a consumer of a **Powabase project**,
which provides its database, auth, agents, workflows, and knowledge bases. This guide
walks you through standing up that project and connecting your own self-hosted
RankForge to it. Jump to **[Setup](#setup)**.

## What it does

1. **Research & SERP intelligence** — give it a topic; it analyzes the SERP (Powabase
   agent + `web_search`/Exa), scrapes and tears down competitor pages
   (`web_scrape`/Firecrawl), clusters keywords, and classifies search intent.
2. **Content briefs** — auto-generates primary/secondary keywords, target word count
   vs. competitors, required headings & entities, questions to answer, link
   suggestions, and a suggested title + meta.
3. **Generation pipeline** — research → brief → outline → draft → optimize turns a
   brief into a structured long-form draft.
4. **GEO optimization** — restructures for AI answer engines: citable claims, Q&A
   blocks, entity coverage, authoritative sourcing, and schema.org JSON-LD.
5. **SEO & readability scoring** — on-page analysis (keyword usage, heading hierarchy,
   meta, internal linking, word-count gap) plus AI-tell/human-voice scoring, with
   targeted and objective refine loops.
6. **Grounding** — a per-brand Powabase **Knowledge Base** (brand voice / style guide
   / product docs) keeps drafts on-brand and factually grounded; a fact-check pass
   flags unsupported claims.
7. **Content clusters** — pillar/member topic clustering with internal-link building
   and broken-link remedies.
8. **Editorial workflow** — article lifecycle (draft → review → approved → published),
   versioning, comments, and a multi-user team per organization.
9. **Publishing** — Markdown/MDX + JSON-LD export, plus WordPress, Webflow, and
   custom-webhook adapters.
10. **Autonomous content scouts** — scheduled agents that monitor a brand's niche,
    surface content opportunities, and auto-draft + score them for editor review
    (never auto-publish). Manages **multiple brands** in one workspace.

See [`docs/PRD.md`](docs/PRD.md) for the full feature breakdown and
[`docs/architecture.md`](docs/architecture.md) for how it's built.

## Architecture

A **FastAPI backend + Next.js frontend** split that consumes a single Powabase
project. The backend talks to the project's Postgres **directly** (psycopg) for app
data and to the Powabase `/api/*` surface (with the **Service Role** key) for agents,
workflows, and knowledge bases. The frontend only ever holds the **Anon** key.

```
Next.js frontend ──HTTP──▶ FastAPI backend ──┬── psycopg ────▶ Powabase Postgres (public.* app tables + read ai.*)
      │                                       └── /api/* ─────▶ Powabase (agents, workflows, KBs, sources)
      └── GoTrue auth (Anon key) ────────────────────────────▶ Powabase Auth
```

| Dir | Stack | Purpose |
|---|---|---|
| [`backend/`](backend/) | FastAPI · psycopg3 · Pydantic v2 · `uv` | API: research, briefs, generation, scoring, publishing, CRUD |
| [`frontend/`](frontend/) | Next.js 16 · App Router · TypeScript · Tailwind · shadcn/ui | Editorial UI |
| [`docs/`](docs/) | — | PRD + architecture |

> **RankForge auto-provisions everything it needs inside your Powabase project at
> runtime** — its agents, per-brand knowledge bases, and the generation pipeline are
> created on first use. **There is no seed step.** You do not pre-create any agents,
> workflows, or KBs; you only create the *project*, set its keys, and apply the app
> schema. (The `RESEARCH_AGENT_ID` / `GENERATION_WORKFLOW_ID` / `BRAND_KB_ID` env vars
> are vestigial — leave them blank.)

---

# Setup

Three things to stand up, in order:

1. **[A Powabase project](#1-create--configure-a-powabase-project)** — the backend RankForge runs on.
2. **[RankForge env files](#2-configure-rankforge)** — pointing the app at that project.
3. **[The app schema + run](#3-apply-the-database-schema)** — migrate the DB and boot.

## Prerequisites

- A **Powabase account** — sign in at the [Studio](https://app.powabase.ai) and you can
  create projects. (Powabase is the managed AI BaaS today; self-hosting is on the way
  as it goes open-source.)
- **Docker + Docker Compose** for the one-command stack. *(Or, for hot-reload dev:
  Python 3.13 + [`uv`](https://docs.astral.sh/uv/) and Node 20+.)*
- API keys you'll paste **into the Powabase project** (not into RankForge):
  - An **LLM provider key** — RankForge's agents default to **Anthropic Claude**
    models, so an **Anthropic API key**. (If you re-point the models in
    `backend/src/rankforge_backend/services/*`, set the matching provider's key
    instead.)
  - **`EXA_API_KEY`** — powers `web_search` (SERP/research/scouts).
  - **`FIRECRAWL_API_KEY`** — powers `web_scrape` (competitor teardown, source capture).

## 1. Create & configure a Powabase project

In the [Powabase Studio](https://app.powabase.ai):

1. **Create a new project.** Note its name — it's your "current project" for the rest
   of these steps. Everything below happens inside it.

2. **Add your LLM provider key (BYOK).** In the project's **AI Provider Keys** settings,
   add an **Anthropic** API key — RankForge's agents call Claude by bare model IDs
   (`claude-opus-4-8`, `claude-opus-4-7`, `claude-sonnet-4-6`). Powabase supports BYOK
   for `openai` / `anthropic` / `google` / `openrouter`; RankForge defaults to Anthropic
   (re-point the `*_MODEL` constants in `backend/src/rankforge_backend/services/` to use a
   different provider). Without a valid key, agents can be created but every
   generation/research/scoring call fails at runtime.

3. **Set the research tool keys.** In the project's **Tools** settings, set
   **`EXA_API_KEY`** (powers `web_search`) and **`FIRECRAWL_API_KEY`** (powers
   `web_scrape`). Research and scouts won't function without these.

4. **Check the Auth email setting.** Powabase **auto-confirms email by default**, so
   sign-up returns an active session immediately — which is what RankForge needs. If
   you've enabled email confirmation on the project, either turn it back off for
   local/testing or configure an SMTP provider; otherwise sign-up returns a "check your
   email" state and login can't complete.

5. **Collect the project credentials** for RankForge's env files. Click **Connect** in
   your project header (top-right) — everything is in that modal:

   | Value | Where in the Connect modal | Used by |
   |---|---|---|
   | **Project URL** | API Keys tab | backend + frontend |
   | **Anon / Publishable key** | API Keys tab | frontend (browser-safe) |
   | **Service Role (Secret) key** | API Keys tab (reveal) | backend only — **secret** |
   | **JWT Secret** | API Keys tab | backend only — **secret** |
   | **Database URL** (direct / psql connection string) | Connection Strings tab | backend + migrations — **secret** |

   > ⚠️ The **Service Role key**, **JWT Secret**, and **Database URL** are
   > **server-side only** — never put them in the frontend or commit them. The frontend
   > gets only the **Anon** key. See [Security](#security-notes).

## 2. Configure RankForge

RankForge lives in the `rankforge/` subdirectory of the `powabase-examples` repo. Clone
it and enter the app, then create the three env files from their examples:

```bash
git clone https://github.com/powabase-ai/powabase-examples
cd powabase-examples/rankforge           # all commands below run from here

cp .env.example .env                     # root: Docker Compose build args
cp backend/.env.example backend/.env     # backend: all the secrets live here
cp frontend/.env.example frontend/.env   # frontend: browser-safe values only
```

Fill them in from the table above. The full mapping:

**`backend/.env`** — the backend holds every secret:

| Variable | Value |
|---|---|
| `POWABASE_BASE_URL` | Project URL |
| `POWABASE_SERVICE_ROLE_KEY` | Service Role key *(secret)* |
| `POWABASE_DATABASE_URL` | Database URL *(secret)* |
| `POWABASE_JWT_SECRET` | JWT Secret *(secret)* |
| `CORS_ALLOW_ORIGINS` | Frontend origin(s), e.g. `http://localhost:3000` |
| `PUBLIC_BASE_URL` | Frontend base URL (builds crawlable `/p/{id}` links) |
| `SIGNUP_INVITE_CODE` | *(optional)* leave empty for open signup — see [below](#optional-close-signups-with-an-invite-code) |
| `RESEARCH_AGENT_ID` / `GENERATION_WORKFLOW_ID` / `BRAND_KB_ID` | **Leave blank** — auto-provisioned |

**`frontend/.env`** — browser-safe only:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | RankForge **backend** URL (`http://localhost:8000` in dev) |
| `NEXT_PUBLIC_SUPABASE_URL` | Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Anon / Publishable key |

**`.env`** (repo root) — read by `docker-compose.yml` for the frontend build args;
set `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, and
`NEXT_PUBLIC_SUPABASE_ANON_KEY` to the same values as `frontend/.env`.

> ⚠️ **Don't wrap values in quotes.** dotenv parses unquoted values cleanly; surrounding
> quotes get included literally (a common cause of `psql` and auth failures).

## 3. Apply the database schema

RankForge's app tables (`public.*`) live in the **same** database as Powabase's
`ai.*` tables. Apply all migrations, in order, to your project's Database URL:

```bash
cd backend
uv sync                                   # install deps
uv run python scripts/apply_schema.py     # applies schema/*.sql in order
cd ..
```

`apply_schema.py` reads `POWABASE_DATABASE_URL` from `backend/.env`, runs every
migration idempotently, and tracks applied ones in `public.schema_migrations`.

> Running `psql -f schema/0001_init.sql` alone is **not** enough — the app needs
> organizations, content clusters, scouts, link health, the signup gate, and more,
> all in later migrations. No `uv`? Apply them all by hand in order:
> ```bash
> for f in backend/schema/0*.sql; do psql "$POWABASE_DATABASE_URL" -f "$f"; done
> ```

## 4. Run the stack

**Docker Compose (recommended):**

```bash
docker compose up --build
# frontend → http://localhost:3000    backend → http://localhost:8000  (/docs for OpenAPI)
```

**Hot-reload dev (no Docker):** run the two services separately —
see [`backend/README.md`](backend/README.md) (`uv run uvicorn … --reload`, port 8000)
and [`frontend/README.md`](frontend/README.md) (`npm run dev`, port 3000).

## 5. Verify

1. Open **http://localhost:3000** → you're redirected to sign in.
2. **Create an account** (any email + password). With auto-confirm email on, you land
   straight in the app; if you set an invite code, you'll be asked for it once.
3. **Create a brand** — name, domain, niche, seed topics, competitors.
4. **Run research** on a topic. You should see it move through
   `searching → scraping → evaluating → done`, then a brief you can generate an article
   from. (Watch it live from **Studio → Runs** in your project.)

If research fails immediately, it's almost always a missing project key — recheck
steps 2–3 of [§1](#1-create--configure-a-powabase-project).

## (Optional) Close signups with an invite code

By default anyone can register. To gate a public deployment behind a shared code, set
**`SIGNUP_INVITE_CODE`** in `backend/.env` to any secret string. New accounts must then
enter that code once, after registering, before they can use the app (existing accounts
are grandfathered). Leaving it empty keeps signup open. Treat the code like a password;
rotate it by changing the value.

---

## Security notes

RankForge calls Powabase from a **trusted backend** with the **Service Role key** —
never ship that key, the **JWT secret**, or the **Database URL** to the browser.
Powabase runs agent DB tools as the DB superuser (**RLS bypassed**), so the
agent/generation endpoints must stay server-side. End-user auth uses GoTrue; the
frontend gets only the **Anon (Publishable)** key. App tables enable RLS from
`0001_init.sql`. See [`docs/architecture.md`](docs/architecture.md#security).

## Troubleshooting

- **`401 Unauthorized` from Powabase `/api/*`.** Every call needs *both* an `apikey`
  and an `Authorization: Bearer` header — the client sends both; a 401 usually means
  the **Service Role key is wrong** or you swapped it with the Anon key.
- **`psql: password authentication failed`.** Studio's connection string sometimes
  shows `[YOUR-PASSWORD]` as a placeholder — replace it with the real database password
  from **Project Settings → Database**.
- **Connection string ignored / falls back to a local socket.** You wrapped
  `POWABASE_DATABASE_URL` in quotes — remove them.
- **Sign-up doesn't return a session / login hangs.** Email confirmation is enabled on
  your project (auto-confirm is the default) — turn it back off in the Auth settings, or
  configure an SMTP provider so confirmation emails actually send.
- **Research/generation fails at runtime** (agents created, then error). A project key
  is missing: **LLM Provider Key** (Anthropic), **`EXA_API_KEY`**, or
  **`FIRECRAWL_API_KEY`**.
- **CORS errors in the browser.** `CORS_ALLOW_ORIGINS` (backend) must include the exact
  frontend origin, and `NEXT_PUBLIC_API_BASE_URL` (frontend) must point at the backend.
- **`402 insufficient_credits`.** Your Powabase project is out of credits — top up;
  RankForge surfaces this rather than retrying.

## License

**MIT** — an open-source Powabase example app, free to clone, modify, and ship.
