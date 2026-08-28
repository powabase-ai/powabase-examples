# PowaCRM Phase 2b — Move the worker into the database

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Delete the Powabase workflow entirely and run the research worker inside Postgres, scheduled by `pg_cron` and calling the agent over `/run/stream` via the `http` extension.

**Architecture:** One `SECURITY DEFINER` function, `run_research_tick()`, scheduled every minute. It requeues stalled jobs, claims exactly one, calls the agent, extracts the JSON, and writes back through the existing `complete_research_job` / `fail_research_job`. Secrets live in `vault`. No workflow, no server.

**Spec:** `powacrm/docs/2026-08-27-phase2-research-design.md` (rev 2 — read §2 "Why the worker is not a Powabase workflow" and §6)

## Global Constraints

- Repo root `/home/zipeng/Agentic/Codebase/example-apps`, app in `powacrm/`, branch `feat/powacrm`. PUBLIC repo: no credentials, no absolute paths, no project ref in anything committed.
- Migrations wrapped in `BEGIN; … COMMIT;`; `SECURITY DEFINER` functions declared `LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp`, then `REVOKE ALL … FROM PUBLIC, anon, authenticated`.
- `PB_DB_URL` is a **connection pooler** that does not reset session GUCs — never a bare `SET`; use `SET LOCAL` inside a transaction. No scratch schemas on the live project.
- Commits carry `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Do not push.
- Verified live and not to be re-derived: `pg_cron`/`pg_net`/`http` preloaded or available; `http` installed; Postgres has outbound egress (an unauthenticated GET returned 401 from the API); `pg_cron` jobs fire (a probe ran within 20 s, `status: succeeded`); `current_database()` is `postgres`, matching `cron.database_name`; only `/run/stream` runs tools; the agent returns ~1 k of narration then a fenced JSON object, so extraction is first `{` to last `}`.

---

### Task 1: Migration 0013 — in-database worker

**Files:** create `db/migrations/0013_inline_worker.sql`, `db/tests/test_0013_worker.sql`, `db/setup/set_worker_config.sh`; modify `db/tests/run_all.sh`.

**Produces:** `public.run_research_tick() RETURNS jsonb` (service-role only); worker config in `vault`; a `pg_cron` job named `powacrm-research-tick`; a fixed `claim_research_jobs`.

- [ ] **Step 1: Fix the claim bug first, with a test that fails against the current definition.** `claim_research_jobs(1)` currently claims *N* jobs: the `LIMIT … FOR UPDATE SKIP LOCKED` subquery sits on the inner side of a Nested Loop Semi Join and is rescanned per outer row, worsened by `request_research` giving a batch identical `created_at`. Reproduced live — one tick claimed all four queued jobs and stranded three. Rewrite it with a CTE so the limit is materialised once. Prove the test fails against the old form and passes against the new one, and put both outputs in your report.

- [ ] **Step 2: Store the worker's config in `vault`, not in a table.** `supabase_vault`/`pgsodium` are preloaded. The worker needs the project URL, the service key, and the agent id. `db/setup/set_worker_config.sh` reads `VITE_POWABASE_URL`, `PB_SERVICE_KEY` and the agent id (resolve it by name via `GET /api/agents`, which paginates) from the environment and stores them. Nothing secret may be committed, and no client-reachable table may hold the key. Verify an `authenticated` token cannot read them.

- [ ] **Step 3: Write `run_research_tick()`.** Per tick: `requeue_stalled_research_jobs()` → claim exactly one job → build the prompt from the company, the brand's `icp_notes`/`product_description`, and the people to score → `http` POST to `{url}/api/agents/{agent}/run/stream` with both `apikey` and `Authorization: Bearer` headers → take the terminal event's content → extract first `{` to last `}` → `complete_research_job` or `fail_research_job`. Every failure path (HTTP error, timeout, no terminal event, unparseable JSON) must call `fail_research_job` with a readable error; a job must never be left `running`. Return a small jsonb summary of what the tick did. Raise the statement timeout locally with `SET LOCAL` — a run takes 35–50 s.

- [ ] **Step 4: Schedule it.** `cron.schedule_in_database('powacrm-research-tick', '* * * * *', $$SELECT public.run_research_tick()$$, current_database())`, made idempotent (unschedule first if present). Confirm `cron.job` shows it active.

- [ ] **Step 5: Verify end to end and commit.** Create a company with a real domain and a person, enqueue via `request_research` as the owner, wait for the tick, and confirm the job reaches `done` with `research_data` populated, a `fit_score` set, and a `researched` event — **and that the run actually called tools** (an ungrounded result is a failure even if the row looks right). Then delete every row you created. Wire `test_0013_worker.sql` into `run_all.sh`, run the full suite, and commit.

---

### Task 2: Delete the workflow and reconcile the docs

**Files:** delete `platform/wf-research-tick.json`; modify `platform/provision.sh`, `platform/README.md`, `README.md`, `CLAUDE.md`, `db/migrate.sh`.

- [ ] **Step 1: Remove the live workflow.** Undeploy and delete `wf-research-tick` on the project, and confirm `GET /api/workflows` no longer lists it. The agent stays — it is the good part.
- [ ] **Step 2: Strip `provision.sh` back to the agent.** Keep the create-or-update, the tool reconciliation, the pagination walk and `curl_checked`. Remove the workflow half and the graph substitution. `provision.sh` should now do one thing.
- [ ] **Step 3: Docs.** `README.md`: research runs from `pg_cron` inside the database, one job per minute, no workflow; setup is `./platform/provision.sh` then `./db/setup/set_worker_config.sh`; migration range `0001`→`0013`. `platform/README.md`: keep the trap note about `agent` blocks not running tools — it is why the worker is not a workflow — and say plainly that the app no longer uses workflows. `CLAUDE.md`: agents bypass RLS so an agent reading untrusted web content must never hold write tools; platform resources are defined in `platform/*.json` and applied by `provision.sh`; and `PB_DB_URL` is a pooler where a bare `SET` leaks onto a shared backend (it silently disabled FK enforcement project-wide earlier in this phase).
- [ ] **Step 4: Full suite, secret scan, commit.** `./db/tests/run_all.sh` → `ALL DB TESTS OK`; `cd app && npm test && npx tsc -b && npm run build`; `git grep -nE "eyJhbGciOi|5c0cec181406a05fc355" -- powacrm` empty.
