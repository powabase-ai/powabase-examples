# CLAUDE.md — PowaCRM

Guidance for Claude Code working in the PowaCRM app. PowaCRM is an outbound
sales automation platform built **on top of Powabase** (the AI BaaS), and an
open-source example app. Read
[`docs/2026-08-26-powacrm-design.md`](docs/2026-08-26-powacrm-design.md) first —
it is the source of truth for scope, schema, and every `[Twenty]`-tagged
convention.

## What this is

A **consumer** of a Powabase project — not part of the Powabase platform, and
(unlike sibling `rankforge/`) **backend-less**: there is no app server. The
React SPA talks directly to the project's PostgREST (`/rest/v1`, Anon key +
GoTrue JWT, RLS on every table); all pipeline logic (sourcing, research,
drafting, sequence tick, webhook ingestion) lives in Powabase workflows and
agents, configured via the `/api/*` surface with the Service Role key —
server-side only, never in `app/`.

## Layout

| Dir | Stack | Purpose |
|---|---|---|
| `app/` | React 19 · Vite · TS · TanStack Query · supabase-js | Dashboard / lite CRM |
| `db/` | SQL migrations + psql test scripts | Schema, RLS, RPCs |
| `docs/` | — | Design spec + phase plans |

## Running

- Migrations/tests: `export PB_DB_URL=<Database URL>` then
  `./db/apply.sh db/migrations/<file>.sql`; run all checks with
  `./db/tests/run_all.sh`.
- Frontend: `cd app && npm run dev`; unit tests `npm test` (vitest).
- Env: `app/.env.local` (gitignored) holds `VITE_POWABASE_URL` +
  `VITE_POWABASE_ANON_KEY` only. `PB_SERVICE_KEY`/`PB_DB_URL` are shell env for
  scripts, never committed, never referenced from `app/`.

## Conventions (don't drift)

- Every table: uuid PK, `created_at`/`updated_at` (+`set_updated_at` trigger),
  `deleted_at` soft delete, actor provenance columns, `text + CHECK` enums —
  spec §3.1. New migrations are numbered `db/migrations/NNNN_*.sql` and get a
  matching `db/tests/test_NNNN_*.sql`.
- RLS: `authenticated` only; SELECT filters `deleted_at IS NULL`; anything that
  must see tombstones (dedupe-then-restore) is a `SECURITY DEFINER` RPC granted
  to `authenticated`, revoked from `anon`.
- Kanban ordering is fractional-float `position` (midpoint on drop, one PATCH).
- Data layer is TanStack Query + supabase-js: optimistic writes via
  `onMutate`/`onError` snapshot-rollback, `onSettled` invalidate. No extra
  state libraries.

## Powabase footguns

- **Two headers or 401** on `/api/*` and `/rest/v1/*`: both `apikey` and
  `Authorization: Bearer <key>`.
- New `public` tables ship with **RLS OFF** — enabling it is part of every
  table migration, not an afterthought.
- Workflow webhook auth is `Authorization: Bearer <webhook_secret>` — never
  send a `Bearer ` header with an empty token (401, and `?token=` fallback is
  skipped).
- Agent `database_query`/`database_write` tools run as **DB superuser (RLS
  bypassed)** — agents are driven only from workflows, never exposed to
  end-user tokens.
