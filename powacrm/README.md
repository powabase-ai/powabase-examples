# PowaCRM

**Open-source outbound sales automation platform, built on [Powabase](https://docs.powabase.ai).**

PowaCRM is a lite CRM plus an outbound engine: it sources leads from Apollo (or
CSV), researches every company with AI agents, drafts hyperpersonalized
LinkedIn + email touches, and runs them through multi-step sequences under
human-controlled guardrails — auto-stopping the moment a lead replies so a
human takes over. It's a real outbound tool *and* an open-source example of
building a **backend-less** product on the Powabase AI BaaS.

PowaCRM is **not a standalone app** — it's a consumer of a **Powabase project**,
which provides its database, auth, agents, workflows, and webhook endpoints.
Unlike its sibling [`rankforge/`](../rankforge), PowaCRM ships **no server of its
own**: the React SPA talks straight to the project's PostgREST API (Anon key +
RLS), and every pipeline job — Apollo sourcing, AI research, message drafting,
the sequence-engine tick, Dux Soup / SendGrid webhook ingestion — runs inside
Powabase workflows and agents.

## What it does

1. **Lead sourcing** — Apollo ICP search (per-brand filters) and CSV import with
   column auto-mapping and dedupe-then-restore semantics.
2. **AI research** — a researcher agent (`web_scrape`/Firecrawl +
   `web_search`/Exa) profiles each company: stack signals, why-now angle,
   personalization hooks, and a 0–100 fit score.
3. **Hyperpersonalized drafting** — a copywriter agent turns brand voice +
   research + thread history into per-lead messages with a self-scored
   confidence; low-confidence drafts route to a human review queue.
4. **Sequences** — versioned multi-step LinkedIn + email sequences (steps are
   drafting prompts, not templates); enrollments freeze a snapshot of the steps
   so mid-flight edits are safe.
5. **Execution** — LinkedIn via the Dux Soup Turbo remote-control queue API,
   email via SendGrid, under daily caps, quiet hours, and a per-brand
   dry-run/pause switch.
6. **Reply handling** — Dux Soup / SendGrid webhooks feed a unified activity
   timeline; any reply halts the sequence and flags the lead for takeover. The
   AI never auto-replies.
7. **Lite CRM** — pipeline kanban, lead records with inline editing and a
   month-grouped timeline, multi-brand workspace. Schema and UX conventions
   are borrowed from [Twenty](https://github.com/twentyhq/twenty) (documented
   per-pattern in the design spec).

See [`docs/2026-08-26-powacrm-design.md`](docs/2026-08-26-powacrm-design.md)
for the full design and
[`docs/2026-08-26-phase1-foundation-plan.md`](docs/2026-08-26-phase1-foundation-plan.md)
for the phase 1 implementation plan.

## Architecture

```
React SPA (app/) ── Anon key + GoTrue JWT ──▶ Powabase PostgREST (public.* CRM tables, RLS)
      │                                        Powabase Auth (GoTrue)
      │                                        Powabase Realtime (events/touches)
      └── deployed workflow webhooks ────────▶ Powabase workflows ──▶ Apollo · Dux Soup · SendGrid
                                               Powabase agents (researcher, copywriter)
```

| Dir | Stack | Purpose |
|---|---|---|
| `app/` | React 19 · Vite · TypeScript · TanStack Query · supabase-js | The dashboard / lite CRM (the only code that runs outside Powabase) |
| `db/` | SQL migrations + psql test scripts | Schema, RLS, RPCs — applied over the project's Database URL |
| `docs/` | — | Design spec + implementation plans |

Secrets live in per-app `.env` files (gitignored); third-party API keys
(Apollo, Dux Soup, SendGrid) live only in Powabase workflow/tool configs,
never in this repo or the browser bundle. The SPA holds the Anon key only.

## Setup

### Prerequisites

- **Node 20+** (the app is built with Vite 8 / React 19) and npm.
- **A Powabase project** — sign up at [powabase.ai](https://powabase.ai) and
  create one. PowaCRM has no backend of its own; the project *is* the backend.
- **`psql`**, or **Docker** if you'd rather not install it. `db/apply.sh` uses
  `psql` when it's on your `PATH` and otherwise falls back to
  `docker run --rm -i postgres:16-alpine psql`.
  ⚠️ That fallback runs on Docker's default bridge network, so a `PB_DB_URL`
  pointing at **localhost** (a Postgres on your own machine, or an SSH tunnel)
  will not resolve from inside the container. For a hosted Powabase project the
  Database URL is a public host and the fallback works fine; for a local
  Postgres, install `psql` or add `--network host` yourself.

### 1. Get the project's credentials

In Studio, open your project and click **Connect**. You need four values — all
of them are environment variables, none of them belong in git:

| Variable | From | Used by | Secret? |
|---|---|---|---|
| `VITE_POWABASE_URL` | Connect → Project URL (`https://<ref>.p.powabase.ai`) | the SPA | no |
| `VITE_POWABASE_ANON_KEY` | Connect → Anon / Publishable key | the SPA | no — it ships in the browser bundle |
| `PB_DB_URL` | Connect → Database URL (replace the `[YOUR-PASSWORD]` placeholder with the real database password from **Project Settings → Database**) | `db/*.sh` | **yes** |
| `PB_SERVICE_KEY` | Connect → Service Role key | `db/setup/create_user.sh` | **yes** |

```bash
cp app/.env.example app/.env.local     # then fill in the two VITE_* values
export PB_DB_URL='postgresql://...'    # shell only — never committed, never read by app/
export PB_SERVICE_KEY='...'
```

`app/.env.local` is gitignored and must contain **only** the two `VITE_*`
values. `PB_DB_URL` and `PB_SERVICE_KEY` are shell environment for the setup
scripts and must never reach `app/` or the browser.

### 2. Build the schema

Migrations are plain SQL and **must be applied in numeric order, `0001` →
`0009`** — later ones depend on tables, policies and functions the earlier ones
create, `0007`/`0008` supersede functions first defined in `0006`/`0005`, and `0009`
replaces every policy from `0004` with per-owner ones.
One command does all of them:

```bash
./db/migrate.sh          # applies db/migrations/0001…0009 in order
```

Or apply them one at a time if you prefer to read as you go:

```bash
for f in db/migrations/0*.sql; do ./db/apply.sh "$f"; done
```

There is no migration version table: `db/migrate.sh` is a
**build-from-scratch** command for a fresh project, not an incremental
"migrate to latest" — `0002`–`0004` will fail against a database that already
has the tables and policies. To apply a single later migration to an existing
database, use `./db/apply.sh db/migrations/<file>.sql`.

### 3. Seed and create the login

```bash
./db/apply.sh db/seed/seed_gpt_trainer.sql   # the one demo brand (stage/event
                                             # lookups come from 0002/0003)

# create_user.sh talks to GoTrue over HTTP, so it needs the project URL and both
# keys in the shell — not just in app/.env.local.
export VITE_POWABASE_URL='https://<ref>.p.powabase.ai'
export VITE_POWABASE_ANON_KEY='...'
export PB_TEST_EMAIL='you@example.com' PB_TEST_PASSWORD='<a strong password>'
./db/setup/create_user.sh                    # creates the GoTrue login (idempotent)
```

The seed is what puts the first row in `brands`. Skip it and the app stops at
"No brands found".

### 4. Run it

```bash
cd app && npm install && npm run dev     # http://localhost:5173
```

Sign in with the email and password from step 3. You should land on the
pipeline board; **Import CSV** takes any people export (Company and Website
columns are both optional), and clicking a card opens the lead record.

### 5. Verify

```bash
./db/tests/run_all.sh    # schema, RLS, per-owner isolation, and RPC assertions
                         # — ends "ALL DB TESTS OK"
cd app && npm test       # vitest unit + jsdom component tests
cd app && npm run build   # type-check + production build
```

## Security and the trust boundary

**Public signup is supported. What an account gets you is your own data, and
nothing else.**

Every table hangs off a brand, and `brands.owner_id` names the single
`auth.users` row that owns it. `db/migrations/0009_access_control.sql` scopes
every policy to that: `brands` on `owner_id = auth.uid()`, and everything else
through `owns_brand(brand_id)`. A stranger who signs up gets a starter brand of
their own and sees zero rows of yours — not your leads, not your companies, not
your timelines, and not your user id.

The two `SECURITY DEFINER` functions matter here more than the policies do.
`public.import_people` and `public.soft_delete_person` run as the database
superuser and **bypass RLS entirely**, so fixing the policies alone would have
left them wide open — any signed-up user could have passed someone else's
`_brand_id` and written into their data. Both now check ownership themselves
before touching a row. If you add a `SECURITY DEFINER` function to this schema,
it needs its own authorization check; RLS will not do it for you.

What this is **not**: teams, sharing, or roles. There is exactly one owner per
brand and no way to grant a second person access to it. A collaborative phase
needs a real membership table, which would replace `owns_brand`, not sit beside
it.

Still worth doing, whatever your setup:

1. **Keep the Database URL and Service Role key server-side.** Only the Anon key
   may reach the browser — that is what it is for. Both of the other two bypass
   RLS completely.
2. **Turn off signups if you don't want them.** Isolation means a stranger's
   account is harmless, not that you want the accounts. Studio →
   Authentication → "Allow new users to sign up". There is no API for this; it
   is a Studio-only setting.
3. **Remember Powabase agents bypass RLS.** Agent database tools run on the
   superuser connection, so anything agent-driven must be driven from a trusted
   backend, never from an end-user's token.

Verify any of the above yourself with `./db/tests/test_0009_access_control.sh`,
which signs up a second account over the public endpoint and asserts it can
neither read, write, import into, nor delete from the first account's data.

## Status

Under active development. Phase 1 (schema + RLS + auth + lite CRM: kanban
board, lead view, CSV import) is **implemented** — see `db/migrations/` and
`app/src/`, with the plan behind it in
[`docs/2026-08-26-phase1-foundation-plan.md`](docs/2026-08-26-phase1-foundation-plan.md).
Later phases add the research agents, Apollo sourcing, the sequence engine, and
the LinkedIn/email execution legs.
