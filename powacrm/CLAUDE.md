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
GoTrue JWT, RLS on every table). Research runs from a `pg_cron` job inside the
database (`run_research_tick()`, `db/migrations/0013_inline_worker.sql`) that
calls the researcher agent directly over `/run/stream` via the `http`
extension — **not** a Powabase workflow (see below). Later-phase pipeline
logic (sourcing, drafting, sequence tick, webhook ingestion) is not yet built;
platform resources it needs are configured via the `/api/*` surface with the
Service Role key — server-side only, never in `app/`.

## Layout

| Dir | Stack | Purpose |
|---|---|---|
| `app/` | React 19 · Vite · TS · TanStack Query · supabase-js | Dashboard / lite CRM |
| `db/` | SQL migrations + psql test scripts | Schema, RLS, RPCs — including the `pg_cron` research worker (`0013`) |
| `platform/` | JSON definitions + `provision.sh` | The researcher agent |
| `docs/` | — | Design spec + phase plans |

## Running

- Migrations/tests: `export PB_DB_URL=<Database URL>` then
  `./db/apply.sh db/migrations/<file>.sql`; run all checks with
  `./db/tests/run_all.sh` (which also needs `PB_SERVICE_KEY` — the injection
  probe makes a real agent run and spends platform credits).
- Platform resources: `PB_SERVICE_KEY=... ./platform/provision.sh` (agent +
  tools), then `./db/setup/set_worker_config.sh` (stores the project URL,
  service key and agent id in `vault` for `run_research_tick()`).
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
- **Platform resources are files, not clicks.** The researcher agent is
  defined in `platform/*.json` and applied by `platform/provision.sh`. Do not
  create or edit it by hand in Studio: the script reconciles the live agent
  to the file (it *removes* attached tools the JSON does not list), so a
  hand-made change is silently reverted on the next provision and is
  invisible in review. Changing the agent's prompt, its model, or its tool
  list means editing the JSON and re-running the script. The worker itself
  (`run_research_tick()`) is a database migration, not a platform resource —
  there is no workflow here to define; see "Agents bypass RLS" below.
- Kanban ordering is fractional-float `position` (midpoint on drop, one PATCH).
- Data layer is TanStack Query + supabase-js: optimistic writes via
  `onMutate`/`onError` snapshot-rollback, `onSettled` invalidate. No extra
  state libraries.

## Powabase footguns

- **Two headers or 401** on `/api/*` and `/rest/v1/*`: both `apikey` and
  `Authorization: Bearer <key>`.
- New `public` tables ship with **RLS OFF** — enabling it is part of every
  table migration, not an afterthought.
- Workflow webhook auth (for whichever later phase first deploys one) is
  `Authorization: Bearer <webhook_secret>` — never send a `Bearer ` header
  with an empty token (401, and `?token=` fallback is skipped).
- **Agents bypass RLS entirely.** Agent `database_query`/`database_write`
  tools run as **DB superuser (RLS bypassed)**, and agents are never exposed
  to end-user tokens. It follows that **an agent that reads untrusted content
  must hold no write-capable tool at all** — the caller does the writing, not
  the agent: `powacrm-researcher` scrapes prospect websites, which anyone
  with access to them can edit, so it gets exactly `web_scrape` + `web_search`
  and nothing else, and `run_research_tick()` (the `SECURITY DEFINER`,
  service-role-only `pg_cron` worker) does the writing via
  `complete_research_job`. Give the agent `database_write`, `http_request` or
  `code_execute` and one poisoned page can write anywhere in the schema, with
  no policy in the way. If a change seems to need a write tool on the
  researcher, the write belongs in the caller instead.
  `db/tests/test_0012_injection.sh` asserts the tool list, not a comment
  about it.
- **`PB_DB_URL` is a connection pooler, and a pooler does not reset session
  GUCs.** A bare `SET` (`session_replication_role`, `role`, `search_path`,
  `statement_timeout`, …) leaks onto the pooled backend and changes behaviour
  for every later connection that lands on it — for every user of the project,
  not just you. Always `BEGIN; SET LOCAL ...; ...; COMMIT;`. This is not
  theoretical: a bare `SET session_replication_role = replica` in a test
  silently disabled foreign-key enforcement project-wide earlier in phase 2 and
  produced orphaned rows before anyone noticed. See the comment above the
  `SET LOCAL` in `db/tests/test_0012_request_research.sh`.
