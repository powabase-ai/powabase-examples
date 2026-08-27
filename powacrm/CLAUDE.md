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
  spec §3.1. New migrations are numbered `db/migrations/NNNN_*.sql` and every
  one gets coverage — usually a matching `db/tests/test_NNNN_*`, or an existing
  test that names it (0005's RPC is exercised by `test_0004_rls.sh`). Anything
  touching RLS or a `SECURITY DEFINER` function needs an HTTP test with a real
  token: `db/apply.sh` connects as a superuser, so a SQL test of a policy passes
  vacuously.
- **RLS is per-owner, not per-role.** `brands.owner_id` names the one account
  that owns a brand; every other table is gated on `owns_brand(brand_id)`
  (`db/migrations/0009_access_control.sql`). A new table holding user data joins
  that scheme — a policy of `TO authenticated USING (true)` reintroduces exactly
  the cross-tenant read 0009 exists to close. Only the shared lookups
  (`stage_options`, `event_types`) are readable by everyone, because they hold
  labels and icons rather than anyone's data.
- **A `SECURITY DEFINER` function needs its own authorization check.** It runs as
  the superuser and bypasses RLS entirely, so the policies do nothing for it —
  see `import_people` / `soft_delete_person`, which call `owns_brand` themselves.
  Getting this wrong is not theoretical: 0009 shipped with a batch UPDATE keyed
  on `_import_id` alone, which let one tenant overwrite another's row, and 0010
  fixes it. Grant these to `authenticated` and revoke from `PUBLIC, anon`.
- Soft delete: SELECT policies filter `deleted_at IS NULL`, so anything that must
  see tombstones (dedupe-then-restore) goes through a `SECURITY DEFINER` RPC.
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
