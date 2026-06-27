# RankForge

**Production SEO/GEO blog-article platform, built on [Powabase](https://docs.powabase.ai).**

RankForge researches, drafts, and optimizes long-form blog articles for both
classic **SEO** (search engines) and **GEO** (Generative Engine Optimization —
being cited by AI answer engines like ChatGPT, Perplexity, and Google AI
Overviews). It is an internal content tool first, and a flagship open-source
example of building a real app on the Powabase AI BaaS second.

> Status: **scaffold + planning**. See [`docs/PRD.md`](docs/PRD.md) for the living
> product requirements and [`docs/architecture.md`](docs/architecture.md) for how
> the pieces fit together. Feature work tracks the phases in the PRD.

## What it does

1. **Research & SERP intelligence** — give it a topic; it analyzes the SERP
   (Powabase agent + `web_search`/Exa), scrapes and tears down competitor pages
   (`web_scrape`/Firecrawl), clusters keywords, and classifies search intent.
2. **Content briefs** — auto-generates a brief: primary/secondary keywords, target
   word count vs. competitors, required headings & entities, questions to answer,
   internal/external link suggestions, and a suggested title + meta.
3. **Generation pipeline** — a Powabase **Workflow** (research → brief → outline →
   draft → optimize) turns a brief into a structured long-form draft.
4. **GEO optimization** — restructures for AI answer engines: citable claims, Q&A
   blocks, entity coverage, authoritative sourcing, and schema.org JSON-LD; scores
   answer-engine readiness.
5. **SEO scoring** — on-page analysis: keyword usage, readability, heading
   hierarchy, meta tags, internal linking, word-count gap vs. the SERP.
6. **Grounding** — a Powabase **Knowledge Base** (brand voice / style guide /
   product docs) keeps drafts on-brand and factually grounded.
7. **Editorial workflow** — article lifecycle (draft → review → approved →
   published), versioning, list/search/filter, multi-user team.
8. **Publishing** — Markdown/HTML + JSON-LD export, plus WordPress, Webflow, and
   custom-webhook adapters.
9. **Autonomous content scouts** — scheduled, semi-autonomous agents that monitor a
   brand's niche (Google News + SERP + competitor diffs via Exa/Firecrawl), surface
   content opportunities, and auto-draft + score them for editor review (never
   auto-publish). Manages **multiple brands** in one workspace.

Plus a growing catalog of article types (listicles, how-to, QA, versus, ultimate
guides, news, YouTube-to-article, interactive tools), programmatic SEO at scale,
fact-checking + anti-hallucination reflection, and internal/external link building.
See [`docs/PRD.md`](docs/PRD.md) for the full feature breakdown.

## Architecture

A FastAPI backend + Next.js frontend split that consumes a Powabase project. The
backend talks to the project's per-project Postgres **directly** (psycopg) for app
data, and to the Powabase `/api/*` surface for agents, workflows, and knowledge
bases. See [`docs/architecture.md`](docs/architecture.md).

```
Next.js frontend ──HTTP──▶ FastAPI backend ──┬── psycopg ──▶ Powabase per-project Postgres (app tables: ai.* + public.*)
                                             └── /api/* ───▶ Powabase (agents, workflows, KBs, sources)
```

| Dir | Stack | Purpose |
|---|---|---|
| [`backend/`](backend/) | FastAPI · psycopg3 pool · Pydantic v2 · `uv` | API: research, briefs, generation, scoring, publishing, article CRUD |
| [`frontend/`](frontend/) | Next.js 16 · App Router · TypeScript · Tailwind · shadcn/ui | Editorial UI |
| [`docs/`](docs/) | — | PRD + architecture |

## Prerequisites

- A **Powabase project** (sign in at the Studio, create a project). You'll need the
  **Project URL** and **Service Role (Secret) Key** from the project's **Connect**
  modal, plus the **Database URL** for migrations.
- In the project's Studio: **Settings → Tools** set `EXA_API_KEY` (for `web_search`)
  and `FIRECRAWL_API_KEY` (for `web_scrape`); **Settings → LLM Provider Keys** set
  your model provider key (BYOK).
- Docker + Docker Compose. Python 3.13 + [`uv`](https://docs.astral.sh/uv/) and
  Node 20+ for local hot-reload dev.

## Quick start

```bash
# 1. Configure
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
# Fill in POWABASE_* (Project URL, Service Role Key, Database URL) in backend/.env
# and NEXT_PUBLIC_* in frontend/.env

# 2. Apply the app schema to your Powabase project's Postgres
#    (uses the Database URL from backend/.env). This runs ALL migrations in order
#    and tracks them in public.schema_migrations — 0001_init.sql alone is NOT enough
#    (the app needs organizations, content_clusters, brand_sources, scout plan, …).
(cd backend && uv run python scripts/apply_schema.py)
#    No uv? Apply every file in order by hand instead:
#    for f in backend/schema/0*.sql; do psql "$POWABASE_DATABASE_URL" -f "$f"; done

# 3. Run the stack
docker compose up --build
# frontend → http://localhost:3000   backend → http://localhost:8000
```

For local hot-reload dev (no Docker), see [`backend/README.md`](backend/README.md)
and [`frontend/README.md`](frontend/README.md).

## Security notes (read before deploying)

RankForge calls Powabase from a **trusted backend** with the **Service Role key** —
never ship that key, the JWT secret, or the Database URL to the browser. Powabase
runs agent DB tools as the DB superuser (RLS bypassed), so the agent/generation
endpoints must stay server-side. End-user auth uses GoTrue; the frontend gets only
the **Anon (Publishable)** key. See [`docs/architecture.md`](docs/architecture.md#security).

## License

MIT (intended — open-source example). See `LICENSE` (TODO).
