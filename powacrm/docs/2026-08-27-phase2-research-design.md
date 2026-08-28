# PowaCRM Phase 2 — Research — Design Spec

**Date:** 2026-08-27 (rev 2 — the worker moved into the database; Powabase workflows removed)
**Status:** Approved design; phase 2 implemented, worker re-hosted in the database per rev 2 (`db/migrations/0013_inline_worker.sql`). This spec is kept in sync with the migrations — unlike the dated plan documents beside it, which are historical records.
**Repo:** `powabase-ai/powabase-examples` → `powacrm/`
**Builds on:** phase 1 (`docs/2026-08-26-phase1-foundation-plan.md`), migrations `0001`–`0010`

## 1. Purpose

Phase 2 is where PowaCRM stops being a database with a UI and becomes an AI
backend consumer — the point of the example app. A **researcher agent** profiles
a lead's company from its website and the open web, and writes back a summary,
detected tech stack, a why-now angle, personalization hooks with evidence, and a
0–100 fit score judged against the brand's own ICP.

Phase 1 already laid the sockets: `companies.research` and `companies.tech_stack`
exist unused, `people.fit_score` carries its 0–100 constraint, `researched` is a
valid stage with a board column, and the lead view's Research tab renders a
placeholder saying this phase would arrive.

### Verified before design (do not re-litigate)

- `web_scrape` (Firecrawl) and `web_search` (Exa) **work on Powabase platform
  credits**. No `FIRECRAWL_API_KEY`/`EXA_API_KEY` is needed — the project's tool
  settings expose only rate limits (Firecrawl 30/min, Exa 60/min), not key
  fields. Confirmed by running a live agent against `gpt-trainer.com`.
- Builtin tools are **not** attachable on agent create: a `tools` array in the
  create body is silently dropped. They attach via
  `POST /api/agents/{id}/tools` with `{"tool_type":"builtin","tool_name":"..."}`.
- Available builtins: `database_write`, `database_query`, `http_request`,
  `code_execute`, `storage_read`, `storage_write`, `web_search`, `web_scrape`.
- `pg_cron`, `pg_net` and `http` are preloaded/available on a Powabase project,
  Postgres has outbound network egress, and `cron.database_name` is `postgres` —
  which is also what `current_database()` returns over the pooler, so a cron job
  runs in the same database as the app's tables. All verified live (rev 2).
- Only `POST /api/agents/{id}/run/stream` executes the ReAct tool loop. The
  non-streaming `/run`, and a workflow `agent` block, run the agent with no tools
  and no system prompt while still returning success.

### Decisions locked during brainstorming

| Question | Decision |
| --- | --- |
| Who triggers research | On-demand per lead + opt-in batch. Nothing runs automatically. |
| What fit is scored against | Per-brand plain-English `icp_notes`, editable by each owner |
| Low score behaviour | Score only — never auto-move a lead's stage |
| Trigger architecture | Queue table + `SECURITY DEFINER` enqueue RPC + scheduled worker |
| Throughput | One job per minute (`pg_cron` tick; a run takes 35-50 s) |

## 2. The architectural problem, and why the queue exists

Running an agent needs the Service Role key. Triggering a workflow webhook needs
a shared secret. **Neither can reach the browser** — the SPA holds only the anon
key. And a webhook carries no user identity, so a workflow triggered that way
cannot tell who asked: any signed-up user could research against any brand.

Meanwhile agents bypass RLS entirely (their DB tools run on the superuser
connection), so the app's entire phase-1 security model does not apply to them.

The queue resolves this without inventing a second auth system:

```
browser (anon key + user JWT)
    │  rpc request_research([person_ids])      ← RLS/ownership decided HERE
    ▼
research_jobs  (owner may READ; nobody may INSERT/UPDATE directly)
    ▲
    │  pg_cron every minute, inside the database
public.run_research_tick()                     ← service-role only
    ├── http POST /api/agents/{id}/run/stream  (the ONLY path that runs tools)
    └── complete_research_job() / fail_research_job()
```

The browser never holds a secret and never names a brand. It passes person ids;
the RPC derives brand from person → company → brand and checks ownership; the
worker reads the brand off the claimed job row. **No caller-supplied `brand_id`
is ever trusted** — that is precisely the class of bug `0010` fixed.

### Why the worker is not a Powabase workflow (rev 2)

The first implementation ran the worker as a scheduled Powabase workflow. That
is no longer the design. Every serious defect in phase 2 came from the workflow
layer, and the evidence was gathered the expensive way:

- A workflow **`agent` block runs the agent with no tools and no system prompt**,
  returning `status: success` — so every research result was ungrounded model
  recall carrying a fabricated `sources` array. Only `/api/agents/{id}/run/stream`
  executes the ReAct tool loop. Three different block config shapes for
  re-attaching tools all failed.
- A `condition` block **gates nothing** on its own; the routing lives on the
  edge's `condition` field, and without it both branches run.
- Blocks execute **strictly sequentially**, so parallel branches buy no throughput.
- A block `id` that is not a UUID returns an opaque **HTTP 500**, not a
  validation error.
- An unresolvable `agent_id` **silently degrades** to `gpt-4o-mini` with no
  system prompt rather than failing.

Powabase's own primitives are fine — the *agent* is excellent, and calling it
directly over `/run/stream` produces genuinely grounded research. It is the
workflow graph as a place to put logic that proved unsound. So the logic moves
into the database, which this project already depends on and already tests:

- **`pg_cron`** (preloaded on Powabase projects) schedules `run_research_tick()`
  every minute. Verified firing on a live project.
- **The `http` extension** (available; Postgres has outbound egress — verified)
  calls the agent's streaming endpoint from SQL.
- The service key and project URL live in **`vault`** (`supabase_vault`/`pgsodium`
  are preloaded), never in a table a client can read and never in the repo.

The result has no workflow, no server, and no second scheduler: one SQL function,
scheduled by the database, calling one agent endpoint, writing through the same
RPCs the rest of the app already uses. Note `current_database()` on a Powabase
pooler connection is `postgres`, which matches `cron.database_name` — so cron
jobs run in the same database as the application's tables.

**Research is per-company.** Ten leads at one company cost one research pass.
The fit score is computed per person in that pass, because fit depends on title.

## 3. Data model — migration `0011_research.sql`

Wrapped in `BEGIN; … COMMIT;` like `0007`/`0008`/`0010`.

**Columns:**
- `brands.icp_notes text` — plain-English ICP the score is judged against.
- `brands.research_daily_cap int NOT NULL DEFAULT 25` — credit ceiling per brand
  per UTC day.
- `companies.research_data jsonb` — the validated structured output (why-now,
  hooks with evidence + source URLs, sources, fit rationale).
- `companies.researched_at timestamptz` — freshness, so re-research is a
  deliberate choice rather than an accident.
- Existing and now populated: `companies.research` (rendered summary the
  Research tab already displays), `companies.tech_stack`, `people.fit_score`.

**`research_jobs`** — follows spec §3.1 conventions (uuid PK, timestamps +
`set_updated_at`, actor provenance):
- `brand_id` → `brands` CASCADE, `company_id` → `companies` CASCADE
- `requested_by uuid` (the `auth.users` id that asked)
- `status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','skipped'))`
- `attempts int NOT NULL DEFAULT 0`, `error text`
- `started_at`, `finished_at timestamptz`
- `CREATE UNIQUE INDEX research_jobs_one_active ON research_jobs (company_id) WHERE status IN ('queued','running')` — double-spend is structurally impossible, not merely checked.
- RLS: SELECT gated on `owns_brand(brand_id)`. **No INSERT/UPDATE/DELETE policy
  for `authenticated`** — the RPCs are the only writers.

**Event type:** add `('researched', 'researched', 'Researched', 'sparkles')` to
`event_types` so the timeline reads "Researcher scored Acme 82" rather than a
generic `field_updated`.

## 4. RPCs

`SECURITY DEFINER SET search_path = public, pg_temp`, matching the posture
`0007`–`0010` established. `request_research` is `plpgsql`;
`claim_research_jobs` is a single statement and stays `LANGUAGE sql`.

### `request_research(_person_ids uuid[]) RETURNS jsonb`
`REVOKE ALL … FROM PUBLIC, anon; GRANT EXECUTE … TO authenticated;`

Per person, in order, returning a per-id verdict so the UI can explain itself:

| Condition | Verdict |
| --- | --- |
| caller does not own the brand | `not_yours` (no existence oracle — same answer as a nonexistent id) |
| person has no company | `skipped: no company` |
| company has no `domain` | `skipped: no domain to research` |
| company researched within 30 days | `skipped: already researched` (re-research is a separate explicit action) |
| brand at its daily cap | `capped` — with the cap and today's count |
| an active job already exists | `already_queued` + job id |
| otherwise | `queued` + job id |

The cap counts jobs created today for that brand and is evaluated **inside** the
function, so it cannot be bypassed by crafting inserts — `authenticated` has no
INSERT policy on `research_jobs` at all.

### `claim_research_jobs(_limit int) RETURNS SETOF research_jobs`
`REVOKE ALL … FROM PUBLIC, anon, authenticated;` — service-role only.

```sql
WITH picked AS MATERIALIZED (
  SELECT id FROM research_jobs
   WHERE status = 'queued'
   ORDER BY created_at, id
   LIMIT greatest(_limit, 0)
   FOR UPDATE SKIP LOCKED
)
UPDATE research_jobs j
   SET status = 'running', started_at = now(), attempts = j.attempts + 1
  FROM picked
 WHERE j.id = picked.id
RETURNING j.*;
```

`SKIP LOCKED` is what makes overlapping ticks safe: two workers cannot claim the
same job.

**The `MATERIALIZED` CTE is load-bearing, and this spec used to prescribe the
broken shape.** Rev 1 put the `LIMIT` inside an `IN`-subquery — which bounds the
*subquery*, not the `UPDATE`. Given a Nested Loop Semi Join with that subquery
on the inner side and no `Materialize` above it, the subquery is re-executed
once per outer row, each execution locks and returns a different queued job
(the previous one is no longer `queued`), and the `UPDATE` claims **the entire
queue**. That plan is not exotic: `research_jobs_queue_idx` already covers
`status='queued' ORDER BY created_at`, so the subquery needs no sort, which is
exactly when a rescan looks cheap. Observed live: one tick claimed all four
queued jobs and stranded three in `running` until the 15-minute sweep. A
`MATERIALIZED` CTE is evaluated exactly once, so `LIMIT 1` means one row under
every plan. `db/tests/test_0013_worker.sql` §1 forces the adverse plan with
planner GUCs so the assertion does not depend on the planner's mood.

`ORDER BY created_at, id`, not `created_at` alone: `request_research` stamps a
whole batch with one `created_at`, so `created_at` on its own leaves a batch's
order undefined — and the age backstop's head-of-queue probe sorts by the same
key, so the two have to agree on which job is next.

### `requeue_stalled_research_jobs() RETURNS (requeued int, abandoned int)`
`REVOKE ALL … FROM PUBLIC, anon, authenticated;` — service-role only.

Two sweeps, in this order:

1. **The age backstop.** A job is abandoned (`failed`, with an error naming the
   age and the attempt count) only when all three hold: it is older than an
   hour, it is at the `(created_at, id)` head of the queue, and *nothing in the
   project has finished in the last hour*. All three are needed. The
   three-strike ceiling in `fail_research_job` cannot bound a worker that
   **dies** rather than fails, because `attempts = attempts + 1` is written
   inside the transaction that rolls back — but age alone mass-failed legitimate
   batches, since `request_research` stamps a whole batch with one `created_at`.
2. **The 15-minute stall sweep**, returning a long-`running` job to `queued`.

`abandoned` is not a report — it is a signal. `run_research_tick()` returns
immediately when it is non-zero, so the abandonment **commits in a transaction
of its own** rather than sharing one with an agent run that may terminate the
backend and roll it back. See §6.

## 5. The agent — `powacrm-researcher`

- **Model:** `claude-sonnet-4-6`. **Tools:** `web_scrape`, `web_search`,
  attached via `POST /api/agents/{id}/tools` (see §1 — the create-body `tools`
  field is silently dropped).
- **Deliberately NO `database_query` / `database_write` / `http_request` /
  `code_execute`.**

**Why that matters more than anything else in this phase.** The agent's entire
job is to read attacker-controlled text: a prospect's website, which anyone can
edit. Powabase agent DB tools run on the superuser connection with RLS bypassed.
An agent holding write tools while reading untrusted pages is one prompt
injection away from writing anywhere in the schema — and no policy would stop
it, because policies do not apply to it. So the agent **reads and returns**; the
workflow does every write. The system prompt states that scraped content is data
and never instructions, and that any instruction found inside fetched content is
to be reported as a finding rather than followed.

**Structured output.** The agent returns a single JSON object:

```jsonc
{
  "summary": "2-4 sentences a salesperson could read before a call",
  "tech_stack": ["Intercom", "Segment"],          // observed, not guessed
  "why_now": "a timely reason to reach out, or null",
  "hooks": [ { "hook": "...", "evidence": "...", "source_url": "https://..." } ],
  "sources": ["https://..."],
  "fit": [ { "person_id": "uuid", "score": 0, "rationale": "which ICP criteria matched" } ],
  "injection_observed": false                      // true if the page tried to instruct the agent
}
```

Whether Powabase agents support a native structured-output/schema parameter is
**to be verified during implementation**; if not, the agent is instructed to emit
JSON only and the workflow parses and validates it. Either way validation is the
workflow's job — an unparseable or schema-violating response fails the job and
writes nothing. Scores are clamped to 0–100 to satisfy the existing CHECK.

## 6. The worker — `run_research_tick()`, scheduled by `pg_cron`

A single `SECURITY DEFINER` SQL function, scheduled every minute by `pg_cron`,
service-role only and never granted to `authenticated`. One job per tick.

Per tick: sweep (`requeue_stalled_research_jobs`) → **if it abandoned anything,
return here** → claim exactly one queued job → read the company, the brand's
`icp_notes`/`product_description`, and the people to score → `POST
/api/agents/{id}/run/stream` via the `http` extension → extract the JSON from
the reply → `complete_research_job` or `fail_research_job`.

**Why the tick stops after an abandonment.** One tick is one transaction. If the
tick swept and then ran a job in the same transaction, an uncatchable
termination of the backend during that run — the *only* failure the age backstop
exists for — would roll the abandonment back with it. Nothing would ever be
abandoned, and the queue would be retried every minute forever: precisely the
unbounded paid-run loop the backstop is there to stop. Returning early commits
the abandonment on its own; the next tick, a minute later, does the running.
The alternative is a second `cron.schedule` entry for the sweep, which commits
independently and costs no throughput, but doubles what an operator has to
reason about, pause during the test suite and unschedule on teardown. The
backstop self-disarms to at most one abandonment per hour project-wide, so the
early return costs at most a minute an hour.

**An ungrounded run is a failed run.** If the terminal `complete` event carries
an empty `tool_calls` list, the tick refuses it rather than writing it: a report
produced without reading anything is model recall wearing a fabricated `sources`
array, and once stored it is indistinguishable from real research and locks the
company for thirty days. This check is the reason the worker is a database
function and not a workflow (§2).

**Extraction, not fence-stripping.** The agent returns narration, then a fenced
block, then the object — roughly a thousand characters of prose precede the JSON.
Taking the first `{` through the last `}` parses correctly and survives both the
prose prefix and the fence. Anything unparseable fails the job with a readable
error rather than reaching `complete_research_job`.

**Failure handling.** An HTTP error, a timeout, a reply with no terminal event,
or unparseable output all route to `fail_research_job`, which returns the job to
`queued` under three attempts and marks it `failed` at three. A job can never be
left `running`: the stall sweep is the second layer.

**Throughput** is one job per minute, since a research run takes 35–50 s and the
tick is scheduled every 60 s. A batch of ten companies drains in roughly ten
minutes. Research is per company, so ten leads at one company cost one job.

**Stage:** the job advances `sourced`/`enriched` → `researched`, and never moves
a lead to `disqualified` — a bad scrape must not silently bury a real prospect.

## 7. SPA surface

- **Lead page:** a Research button (disabled while a job is active), live status
  `Queued → Researching… → Done/Failed` by watching the job row, and the Research
  tab rendering the real summary, hooks with their evidence and source links,
  detected stack, and `researched_at`. A failed job shows its error and offers
  retry.
- **Board:** fit score already renders on cards; add a brand-level "Research N
  unresearched leads" action that calls `request_research` with a selection and
  reports the per-id verdicts (queued / skipped / capped) rather than a bare
  count.
- **New `/settings` route:** brand name, product description, voice notes,
  `icp_notes`, and `research_daily_cap`. Phase 1 never built a settings screen;
  ICP text needs somewhere to live.
- Data layer unchanged: TanStack Query, optimistic writes with
  `onMutate`/`onError`/`onSettled`, error branches gated on `error && !data`.

## 8. Testing

- **`test_0011_research.sh`** (HTTP, two real accounts — `db/apply.sh` runs as
  superuser and would pass vacuously): a non-owner's `request_research` is
  refused; the daily cap holds and reports its count; a second request for the
  same company returns `already_queued` rather than double-queueing; a
  domainless company is `skipped`; `claim_research_jobs` is unreachable with a
  user token and reachable with service-role; `research_jobs` cannot be inserted
  or updated directly by `authenticated`.
- **Falsifiability:** as with `test_0010`, prove the suite fails against the
  pre-fix behaviour before trusting it — temporarily drop the ownership check
  and confirm the test goes red.
- **Golden set:** 3–5 known companies (including one deliberately outside the
  ICP) run end to end; scoring quality and source accuracy eyeballed before the
  agent is considered done.
- **Prompt-injection probe:** point the agent at a page containing an explicit
  "ignore previous instructions" payload; assert the run completes without any
  write attempt (the agent holds no write tools), that validation still passes or
  fails cleanly, and that `injection_observed` is set.
- **Frontend:** vitest for the verdict-rendering logic and job-status state
  machine. No browser is available, so anything visual stays explicitly
  unverified, as in phase 1.

## 9. Cost

Every run spends credits: one scrape, one to three searches, and a Sonnet call
per company. Controls: nothing runs automatically, the per-brand daily cap is
enforced inside the RPC, research is per-company rather than per-lead, and a
company researched within 30 days is skipped by default.

## 10. Out of scope

Apollo sourcing and enrichment (phase 3), the copywriter and sequence engine
(phase 4), LinkedIn and email execution (phase 5). Re-research scheduling,
multi-page site crawls, and PDF/deck ingestion are deliberately deferred.
