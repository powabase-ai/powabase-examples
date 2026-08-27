# PowaCRM

**Open-source outbound sales automation platform, built on [Powabase](https://docs.powabase.ai).**

PowaCRM is a lite CRM plus an outbound engine: it sources leads from Apollo (or
CSV), researches every company with AI agents, drafts hyperpersonalized
LinkedIn + email touches, and runs them through multi-step sequences under
human-controlled guardrails — auto-stopping the moment a lead replies so a
human takes over. It's a real outbound tool *and* an open-source example of
building a **backend-less** product on the Powabase AI BaaS.

PowaCRM is **not a standalone app** — it's a consumer of a **Powabase project**,
which provides its database, auth, agents, and webhook endpoints. Unlike its
sibling [`rankforge/`](../rankforge), PowaCRM ships **no server of its own**:
the React SPA talks straight to the project's PostgREST API (Anon key + RLS).
AI research runs from a `pg_cron` job inside the database calling the
researcher agent directly — no Powabase workflow is involved (see
[Research](#research) below and [`platform/README.md`](platform/README.md)
for why). Apollo sourcing, message drafting, the sequence-engine tick, and
Dux Soup / SendGrid webhook ingestion are later-phase pipeline jobs and not
yet built.

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

pg_cron (inside Postgres) ── http extension ──▶ POST /api/agents/{id}/run/stream (researcher agent)
                                                 writes back via complete_research_job / fail_research_job
```

AI research does **not** go through a Powabase workflow — see
[Research](#research). The workflow row above is for later-phase pipeline
jobs (Apollo sourcing, drafting, sequencing, webhook ingestion), none of
which are built yet.

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
- **Two Postgres extensions on the project: `pg_cron` and `http`.** Neither is
  enabled by default on a Powabase project. `0013` creates both itself
  (`CREATE EXTENSION IF NOT EXISTS`), which needs them to be *available* on the
  image and creatable by your database role; `pg_cron` additionally has to be
  in the server's `shared_preload_libraries`. Enable them in Studio →
  **Database → Extensions**. `http` must land in schema **`public`** —
  `run_research_tick()` pins `search_path = public, pg_temp`, so an `http`
  installed into an `extensions` schema fails at run time with `function
  http(http_request) does not exist`. `db/migrate.sh` checks all of this before
  it applies anything (`db/setup/preflight.sql`), and `0013` re-checks it
  itself. Only research needs them; `0001`–`0012` (the whole CRM) do not.
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
`0014`** — later ones depend on tables, policies and functions the earlier ones
create, `0007`/`0008` supersede functions first defined in `0006`/`0005`, `0009`
replaces every policy from `0004` with per-owner ones, `0011`/`0012` add the
research queue and its RPCs, `0013` adds the in-database worker
(`run_research_tick()` and its `pg_cron` schedule) on top of all of it, and
`0014` bounds `brands.research_daily_cap` in the schema.
One command does all of them:

```bash
./db/migrate.sh          # preflight, then db/migrations/0001…0014 in order
```

`db/migrate.sh` runs `db/setup/preflight.sql` first. It is read-only and it
fails fast, by name, if `pg_cron`, `http` or `unaccent` is missing — otherwise
the first sign of trouble is `0013` dying on `schema "cron" does not exist`
after twelve migrations have already applied. Run it on its own any time with
`./db/apply.sh db/setup/preflight.sql`.

Or apply them one at a time if you prefer to read as you go:

```bash
for f in db/migrations/0*.sql; do ./db/apply.sh "$f"; done
```

There is no migration version table: `db/migrate.sh` is a
**build-from-scratch** command for a fresh project, not an incremental
"migrate to latest" — `0002`–`0004` will fail against a database that already
has the tables and policies. To apply a single later migration to an existing
database, use `./db/apply.sh db/migrations/<file>.sql`.

### 3. Create the login, then seed

**Order matters.** Since `0009`, `brands.owner_id` is NOT NULL and the demo brand
is handed to the project's first account — so the account has to exist before the
seed runs. The seed fails with a clear message if you do it the other way round.

```bash
# create_user.sh talks to GoTrue over HTTP, so it needs the project URL and both
# keys in the shell — not just in app/.env.local.
export VITE_POWABASE_URL='https://<ref>.p.powabase.ai'
export VITE_POWABASE_ANON_KEY='...'
export PB_TEST_EMAIL='you@example.com' PB_TEST_PASSWORD='<a strong password>'
./db/setup/create_user.sh                    # creates the GoTrue login (idempotent)

./db/apply.sh db/seed/seed_gpt_trainer.sql   # the demo brand, owned by that
                                             # account (stage/event lookups come
                                             # from 0002/0003)
```

Signing up already gives every account its own empty starter brand, so the app
works without the seed. The seed only adds the populated `gpt-trainer` demo
brand — skip it if you would rather start clean.

### 4. Provision the research agent and the worker's config

The researcher agent is a **platform** resource, not a database one, so the
migrations do not create it. Two scripts finish the setup, in this order:

```bash
export VITE_POWABASE_URL='https://<ref>.p.powabase.ai'
export PB_SERVICE_KEY='...'                 # server-side only
./platform/provision.sh                     # creates/updates the agent + its tools

export PB_DB_URL='postgresql://...'         # Studio → Connect → Database URL
./db/setup/set_worker_config.sh             # stores the project URL, service key and
                                             # agent id in vault, for run_research_tick()
```

`platform/provision.sh` creates or updates the agent from
`platform/researcher-agent.json` and reconciles its attached tools to exactly
the two that file lists (`web_scrape`, `web_search`) — attaching anything
missing, removing anything extra. It is idempotent — re-run it after editing
the JSON file. See [`platform/README.md`](platform/README.md).

`db/setup/set_worker_config.sh` looks the agent up by name (paginating
`GET /api/agents`) and calls `set_research_worker_config()` to store the
project URL, the Service Role key, and the agent's id in `vault` — nothing a
client-reachable table or PostgREST could ever expose. It must run *after*
`provision.sh`, since it fails if the agent does not exist yet, and it needs
`PB_DB_URL` on top of the two platform variables. Re-running it overwrites
the three values in place.

Skip either step and everything else still works; you just get no research —
the `pg_cron` job schedule from `0013` is already in place, but
`run_research_tick()` has nothing in `vault` to call the agent with.

### 5. Run it

```bash
cd app && npm install && npm run dev     # http://localhost:5173
```

Sign in with the email and password from step 3. You should land on the
pipeline board; **Import CSV** takes any people export (Company and Website
columns are both optional), and clicking a card opens the lead record.

### 6. Verify

```bash
./db/tests/run_all.sh    # schema, RLS, per-owner isolation, and RPC assertions
                         # — ends "ALL DB TESTS OK"
cd app && npm test       # vitest unit + jsdom component tests
cd app && npm run build   # type-check + production build
```

`run_all.sh` needs `PB_DB_URL`, `VITE_POWABASE_URL`, `VITE_POWABASE_ANON_KEY`,
`PB_TEST_EMAIL`/`PB_TEST_PASSWORD` **and** `PB_SERVICE_KEY` — the last one for
`test_0012_injection.sh`, which makes one real agent run (about 30 seconds, and
it spends platform credits) against a page carrying a prompt-injection payload
and asserts the researcher holds no write-capable tool, still returns a valid
report, and flags the attempt.

## Research

Clicking **Research** on a lead — or **Research all** on the board — profiles
that lead's *company* and scores every known contact at it. Research belongs to
the company, so ten leads at one employer cost one pass; the fit score lands on
each person, because fit depends on their title.

A pass produces a short summary a seller can read before a call, an observed
tech stack, a why-now angle, up to three personalization hooks each carrying the
evidence and the **source URL it came from**, and a 0–100 fit score per person
with the rationale that produced it. The score is judged against the brand's
**ICP notes** — plain English you write in **Settings** — not against the
model's own taste, so two brands selling different things score the same company
differently. Every hook is shown with its source so a seller can check the claim
before repeating it.

It runs entirely on **Powabase platform credits**: `web_scrape` is Firecrawl and
`web_search` is Exa, both built in. There are **no external API keys** to obtain
or configure for research.

**On demand, and capped.** Nothing is researched until someone asks. Each brand
has a `research_daily_cap` (25/day by default, editable in Settings, **hard
ceiling 100**) enforced inside the `request_research` RPC, and a company
researched in the last 30 days is skipped rather than re-run. Requests become
rows in a `research_jobs` queue that only the RPCs may write, so the cap cannot
be sidestepped from the browser. The ceiling itself is a CHECK constraint
(`brands_research_daily_cap_range`, `db/migrations/0014_research_cap_bound.sql`)
and not a form validation, because the form is one `PATCH /rest/v1/brands` away
from being ignored — see [Security](#security-and-the-trust-boundary).

**The worker is a `pg_cron` job inside the database — not a workflow, and not
a server.** `run_research_tick()` (`db/migrations/0013_inline_worker.sql`) is
scheduled by `pg_cron` every minute. Each tick requeues any job stalled over
15 minutes, claims exactly one queued job, calls `powacrm-researcher` directly
over `POST /api/agents/{id}/run/stream` via the `http` extension, extracts the
JSON result, and writes it back through the same `complete_research_job` /
`fail_research_job` RPCs the rest of the app uses. Setup needs both
`./platform/provision.sh` (creates the agent) and `./db/setup/set_worker_config.sh`
(puts the project URL, service key, and agent id in `vault`, which is what
`run_research_tick()` reads) — see [Setup](#setup) step 4. There used to be a
scheduled Powabase workflow doing this instead; it produced ungrounded,
fabricated research and has been removed — see
[`platform/README.md`](platform/README.md) for why.

**Throughput is deliberately modest: one job per minute.** A research run
takes 35–50 s and the tick is scheduled every 60 s, so the worker turns over
roughly one company a minute. Selecting ten companies on the board and
hitting **Research all** takes about ten minutes to drain. The daily cap
binds long before throughput does.

The researcher agent holds **exactly two tools, `web_scrape` and `web_search`**,
and that is a security boundary rather than a minimalism preference — see below.

## Security and the trust boundary

**Public signup is supported, and it isolates data completely. Since phase 2 it
does not isolate *spend* — if you have enabled research, close signups.**

An account gets you your own data and nothing else: the isolation below is
airtight and was verified against a live second account. But research runs on
the **project owner's** Powabase platform credits (`web_scrape` is Firecrawl,
`web_search` is Exa, plus the model run), and every account can enqueue
research against its own brand. That is a stranger spending your money, not
reading your data. The `research_daily_cap` ceiling in
`db/migrations/0014_research_cap_bound.sql` bounds it — a brand may not be set
above **100 jobs/day**, enforced by a CHECK constraint rather than by the
Settings form, which an account can simply skip — but an account may create
more than one brand, so the bound is per brand and not per person. If research
is on and you do not want strangers holding accounts, turn signups off (point 2
below). In phase 1, before research existed, a stranger's account really was
harmless; that is no longer the whole story.

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
2. **Turn signups off unless you actually want them — and treat that as
   required once research is enabled.** Isolation keeps a stranger out of your
   data; it does not keep them out of your credits, because their own brand is
   a legitimate place to enqueue research that you pay for. The 0–100 cap
   constraint bounds each brand's daily spend, not the number of brands an
   account can make. Studio → Authentication → "Allow new users to sign up".
   There is no API for this; it is a Studio-only setting.
3. **Remember Powabase agents bypass RLS.** Agent database tools run on the
   superuser connection, so anything agent-driven must be driven from a trusted
   backend, never from an end-user's token. That is why the researcher — whose
   whole job is reading pages an outsider can edit — is given **only**
   `web_scrape` and `web_search`, and why `run_research_tick()`, not the
   agent, writes the result back through `complete_research_job`. Adding
   `database_write`, `http_request` or `code_execute` to that agent would put
   the entire schema one prompt injection away, and no policy would stop it.
   `db/tests/test_0012_injection.sh` feeds the agent a real injection payload
   and asserts both halves: the tool list is exactly those two, and the agent
   reports the attempt instead of obeying it.

Verify any of the above yourself with `./db/tests/test_0009_access_control.sh`,
which signs up a second account over the public endpoint and asserts it can
neither read, write, import into, nor delete from the first account's data.

## Status

Under active development.

- **Phase 1 — implemented.** Schema + RLS + auth + the lite CRM: kanban board,
  lead view, CSV import. See `db/migrations/0001`–`0010`, `app/src/`, and
  [`docs/2026-08-26-phase1-foundation-plan.md`](docs/2026-08-26-phase1-foundation-plan.md).
- **Phase 2 — implemented.** AI research: the queue and RPCs
  (`db/migrations/0011`–`0012`), the researcher agent (`platform/`), the
  in-database `pg_cron` worker (`db/migrations/0013`), the spend ceiling on
  `research_daily_cap` (`db/migrations/0014`), and the trigger, job status and
  rendering in the app. Design:
  [`docs/2026-08-27-phase2-research-design.md`](docs/2026-08-27-phase2-research-design.md)
  (rev 2 — the worker moved out of a Powabase workflow and into the database;
  see its §2). Original plan (superseded on the worker's architecture):
  [`docs/2026-08-27-phase2-research-plan.md`](docs/2026-08-27-phase2-research-plan.md).
- **Later phases** add Apollo sourcing, the copywriter and drafting queue, the
  sequence engine, and the LinkedIn/email execution legs.
