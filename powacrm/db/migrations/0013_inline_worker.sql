-- ============================================================================
-- PHASE 2b: THE RESEARCH WORKER MOVES INTO THE DATABASE.
--
-- Phase 2 ran the worker as a scheduled Powabase workflow. It is now one SQL
-- function scheduled by pg_cron. The reasons are in
-- docs/2026-08-27-phase2-research-design.md section 2, and the short version is
-- that a workflow `agent` block runs the agent with NO TOOLS and NO SYSTEM
-- PROMPT while still reporting success, so every research result it produced
-- was ungrounded model recall carrying a fabricated `sources` array. Only
-- POST /api/agents/{id}/run/stream executes the ReAct tool loop. The agent is
-- the good part of the platform; the workflow graph as a place to put logic is
-- what did not hold up.
--
-- This migration produces:
--   * a FIXED claim_research_jobs() -- see the bug note below, it is the
--     reason this file leads with a function that already existed;
--   * set_research_worker_config(), which puts the project url, the service
--     key and the agent id in `vault` (never a table a client can read, never
--     in this repo);
--   * run_research_tick(), the worker;
--   * a pg_cron job, `powacrm-research-tick`, running it every minute.
--
-- ONE TICK IS ONE TRANSACTION, and that has two consequences worth knowing
-- before reading the code:
--   * The claim is not visible to anyone else until the tick commits ~45 s
--     later, so a watching UI sees `queued` and then `done` rather than
--     passing through `running`. That is a cosmetic loss and it buys a real
--     guarantee: a worker that dies mid-run rolls its own claim back, so a job
--     cannot be stranded in `running` at all. requeue_stalled_research_jobs()
--     stays as the second layer for anything that outlives its transaction.
--   * Overlapping ticks are still safe. The claim holds a row lock for the
--     whole run, and claim_research_jobs uses FOR UPDATE SKIP LOCKED, so a
--     second tick steps over the in-flight job and takes the next one.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. THE EXTENSIONS THIS MIGRATION DEPENDS ON, AND WHERE THEY GO.
--
-- 0013 is the first migration that needs anything beyond core Postgres and the
-- `unaccent` 0001 creates: `cron.*` at the bottom of this file, and `http(...)`
-- inside run_research_tick(). NEITHER IS ENABLED BY DEFAULT on a Powabase
-- project. On the project this app was developed against both had been
-- installed by hand -- pg_extension shows `unaccent` (created by 0001) with a
-- much lower OID than `http` and `pg_cron`, which were created afterwards --
-- and that hid the gap for the whole of phase 2. On a fresh project this file
-- died at `schema "cron" does not exist`; because db/apply.sh runs with
-- ON_ERROR_STOP=1 and db/migrate.sh with `set -e`, ALL of 0013 rolled back:
-- no claim fix, no vault function, no worker, and a migrate.sh that stopped
-- with a Postgres error and no explanation.
--
-- `http` GOES IN `extensions`, NEVER IN `public`. Earlier revisions of this
-- file installed it into `public` and enforced that placement, and that was a
-- security hole rather than a detail. `public` is the schema PostgREST exposes;
-- PostgreSQL grants EXECUTE on new functions to PUBLIC by default; nothing
-- revoked it. Verified live against this project before the fix: with an
-- ordinary signed-up user's JWT,
--
--   POST /rest/v1/rpc/http_get  {"uri":"https://example.com"}
--
-- returned HTTP 200 with the response body. Eleven of the extension's functions
-- were reachable that way -- http_get/post/put/patch/delete/head, http(),
-- http_header, and http_set_curlopt, which mutates libcurl options on a POOLED
-- backend and so persists to whoever lands on that backend next. That is a full
-- SSRF primitive, held by every account, reaching anything routable from the
-- database host -- including link-local metadata endpoints and any service
-- listening on localhost -- on a project whose public signup is a documented
-- feature. It is strictly worse than the 0004 hole 0009 exists to close, and it
-- was created by the migration that hardens everything else.
--
-- The reason the old comment gave for `public` was real, but it had the wrong
-- answer. run_research_tick() pins its search_path (0008's hardening rule), so
-- an http living in `extensions` is invisible to it and the worker dies at RUN
-- time with `function http(http_request) does not exist`, long after the
-- migration reported success. The fix is to NAME the schema in the search_path
-- -- `SET search_path = public, extensions, pg_temp`, with pg_temp still last
-- -- not to move the extension into client reach.
--
-- Two things happen below and BOTH are needed:
--
--   * RELOCATE, and it cannot be done the obvious way. `CREATE EXTENSION IF NOT
--     EXISTS ... SCHEMA extensions` does not move one that already exists
--     (verified: it emits `extension "http" already exists, skipping` and
--     ignores the SCHEMA clause), and pgsql-http ships `relocatable = false`, so
--     ALTER EXTENSION ... SET SCHEMA fails outright -- verified on this project:
--     `ERROR: extension "http" does not support SET SCHEMA`. The only move
--     available is DROP + CREATE, which is what runs below, WITHOUT `CASCADE`
--     and inside this migration's single transaction. No CASCADE is the safety
--     interlock: if anything in the database genuinely depends on an http object
--     (a column of type http_response, a function taking http_request, a cast),
--     Postgres refuses the drop, the whole migration rolls back, and the
--     installer is told rather than silently losing their objects. Postgres
--     computes that far better than a hand-written dependency query would.
--     The cost is real and stated plainly: code elsewhere in this database that
--     calls `public.http_get(...)` with a bare `public` search_path stops
--     resolving. That is the same reachability being removed on purpose.
--
--   * REVOKE, because relocation alone fixes nothing about privileges. Verified
--     in a rolled-back transaction on this project: immediately after
--     `DROP EXTENSION http; CREATE EXTENSION http SCHEMA extensions`, all 19 of
--     its functions were still executable by `anon` and `authenticated` -- that
--     is base PostgreSQL behaviour, EXECUTE on a new function is granted to
--     PUBLIC with no ACL entry needed. `public` is worse still: this project's
--     default privileges (pg_default_acl, roles postgres and supabase_admin,
--     schema public) hand `EXECUTE ... TO anon, authenticated` to every function
--     created there, so in `public` the grant is re-applied by the project's own
--     policy every time the extension is created. In `extensions` the default
--     ACL grants to `postgres` only. `anon`/`authenticated` do hold USAGE on
--     `extensions` here, so the move is PostgREST-exposure defence and the
--     revoke loop is the privilege one. db/tests/test_0013_worker.sql section 4
--     asserts the revoke standing rather than trusting this one-time fix,
--     because any later CREATE EXTENSION re-grants.
--
-- Failures here are caught and re-raised with a message that names the
-- extension and what to do about it, because `permission denied to create
-- extension "pg_cron"` on its own does not tell an installer that the CRM
-- itself (0001-0012) is already applied and working, and that only research
-- is missing.
-- ---------------------------------------------------------------------------
DO $ext$
DECLARE
  v_schema text;
  f record;
BEGIN
  BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_cron;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'powacrm 0013 requires the pg_cron extension and could not create it: %', SQLERRM
      USING HINT = 'Enable pg_cron on the project (Studio -> Database -> Extensions); it also has to be in the server''s shared_preload_libraries, which is not something this migration can do. Everything before 0013 has already been applied, so the CRM works -- only AI research is missing. Afterwards apply this file and the one after it: ./db/apply.sh db/migrations/0013_inline_worker.sql && ./db/apply.sh db/migrations/0014_research_cap_bound.sql (not ./db/migrate.sh: it starts at 0001, and 0002-0004 are bare CREATE TABLEs that abort on a database which already has them -- every database that reaches this message does. Do not stop at 0013 either: 0014 is the spend ceiling.)';
  END;

  -- `extensions` is the Supabase-lineage convention and already holds pgcrypto,
  -- pg_net and uuid-ossp on a Powabase project. Created here anyway, so this
  -- migration also works on an image that has no such schema yet.
  BEGIN
    CREATE SCHEMA IF NOT EXISTS extensions;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'powacrm 0013 needs a schema named "extensions" to keep the http extension out of PostgREST''s reach, and could not create it: %', SQLERRM
      USING HINT = 'Create it as a superuser (CREATE SCHEMA extensions;) and then re-apply: ./db/apply.sh db/migrations/0013_inline_worker.sql && ./db/apply.sh db/migrations/0014_research_cap_bound.sql (not ./db/migrate.sh: it starts at 0001, and 0002-0004 are bare CREATE TABLEs that abort on a database which already has them -- every database that reaches this message does. Do not stop at 0013 either: 0014 is the spend ceiling.) Do NOT work around this by installing http into public: public is the schema PostgREST exposes, and every function the extension owns would become callable by any signed-up account.';
  END;

  BEGIN
    CREATE EXTENSION IF NOT EXISTS http SCHEMA extensions;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'powacrm 0013 requires the http extension (pgsql-http) and could not create it: %', SQLERRM
      USING HINT = 'Enable "http" on the project (Studio -> Database -> Extensions) with schema `extensions`, then re-apply: ./db/apply.sh db/migrations/0013_inline_worker.sql && ./db/apply.sh db/migrations/0014_research_cap_bound.sql (not ./db/migrate.sh: it starts at 0001, and 0002-0004 are bare CREATE TABLEs that abort on a database which already has them -- every database that reaches this message does. Do not stop at 0013 either: 0014 is the spend ceiling.)';
  END;

  -- Relocate an http that predates this migration -- including one an earlier
  -- revision of this very file put in `public`. See the note above for why this
  -- is DROP + CREATE and not ALTER EXTENSION ... SET SCHEMA, and why there is
  -- deliberately no CASCADE.
  SELECT extnamespace::regnamespace::text INTO v_schema FROM pg_extension WHERE extname = 'http';
  IF v_schema IS DISTINCT FROM 'extensions' THEN
    RAISE NOTICE 'powacrm 0013: moving the http extension out of schema "%" into "extensions" (drop and re-create -- pgsql-http does not support SET SCHEMA). Anything in this database that calls http functions with a bare `public` search_path will need `extensions` added to its own search_path.', v_schema;
    BEGIN
      DROP EXTENSION http;
      CREATE EXTENSION http SCHEMA extensions;
    EXCEPTION WHEN OTHERS THEN
      RAISE EXCEPTION 'powacrm 0013 found the http extension in schema "%" and could not move it to "extensions": %', v_schema, SQLERRM
        USING HINT = 'http in `public` is callable over PostgREST by every signed-up account, which is an SSRF primitive against anything routable from the database host. If the drop was refused, some object in this database depends on an http type or function -- find it with `DROP EXTENSION http;` in psql, which names the dependents, move or drop those, and re-apply: ./db/apply.sh db/migrations/0013_inline_worker.sql && ./db/apply.sh db/migrations/0014_research_cap_bound.sql (not ./db/migrate.sh: it starts at 0001, and 0002-0004 are bare CREATE TABLEs that abort on a database which already has them -- every database that reaches this message does. Do not stop at 0013 either: 0014 is the spend ceiling.) Do not reach for CASCADE: it would drop them.';
    END;
  END IF;

  -- Re-read rather than trust the statement above.
  SELECT extnamespace::regnamespace::text INTO v_schema FROM pg_extension WHERE extname = 'http';
  IF v_schema IS DISTINCT FROM 'extensions' THEN
    RAISE EXCEPTION 'powacrm 0013 requires the http extension in schema "extensions"; it is installed in %', coalesce(v_schema, '(nowhere -- not installed)')
      USING HINT = 'run_research_tick() pins search_path = public, extensions, pg_temp. pgsql-http is non-relocatable (pg_extension.extrelocatable is false), so ALTER EXTENSION ... SET SCHEMA cannot move it -- it errors with "extension "http" does not support SET SCHEMA". The only move is drop and re-create, as a superuser and with nothing depending on it: DROP EXTENSION http; CREATE EXTENSION http SCHEMA extensions; then re-apply so the grants are revoked again: ./db/apply.sh db/migrations/0013_inline_worker.sql && ./db/apply.sh db/migrations/0014_research_cap_bound.sql (not ./db/migrate.sh: it starts at 0001, and 0002-0004 are bare CREATE TABLEs that abort on a database which already has them -- every database that reaches this message does. Do not stop at 0013 either: 0014 is the spend ceiling.)';
  END IF;

  IF to_regnamespace('cron') IS NULL THEN
    RAISE EXCEPTION 'powacrm 0013 requires pg_cron, but schema "cron" does not exist even after CREATE EXTENSION pg_cron'
      USING HINT = 'pg_cron installs its objects into a `cron` schema. If this fires, pg_cron is present under a name or layout this migration does not understand -- schedule public.run_research_tick() every minute by hand and delete the DO block at the end of this file.';
  END IF;

  -- Enumerated from the catalogue, not from a hand-written list: pgsql-http
  -- ships a different set of functions in different versions, and a list is one
  -- release away from being incomplete. `deptype = 'e'` is the extension
  -- membership edge, so this covers exactly what CREATE EXTENSION created.
  FOR f IN
    SELECT p.oid::regprocedure AS sig
      FROM pg_proc p
      JOIN pg_depend d ON d.objid = p.oid AND d.classid = 'pg_proc'::regclass AND d.deptype = 'e'
      JOIN pg_extension e ON e.oid = d.refobjid
     WHERE e.extname = 'http'
  LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated', f.sig);
  END LOOP;
END $ext$;

-- ---------------------------------------------------------------------------
-- 1. THE CLAIM BUG.
--
-- claim_research_jobs(1) could claim N jobs, not one. Reproduced live: a single
-- tick claimed all four queued jobs, ran one, and stranded the other three
-- until the 15-minute sweep.
--
-- The old body was:
--
--   UPDATE research_jobs SET status='running', ...
--    WHERE id IN (SELECT id FROM research_jobs WHERE status='queued'
--                  ORDER BY created_at LIMIT greatest(_limit,0)
--                  FOR UPDATE SKIP LOCKED)
--
-- `LIMIT` inside an IN-subquery constrains the SUBQUERY, not the UPDATE. Which
-- rows the UPDATE touches therefore depends on the plan the planner happens to
-- pick. Given a Nested Loop Semi Join with the subquery on the inner side and
-- no Materialize above it, the subquery -- LIMIT, locking and all -- is
-- re-executed once per outer row, and each execution locks and returns a
-- different queued job because the previous one is no longer `queued`. Every
-- queued job ends up claimed. That plan is not exotic: research_jobs_queue_idx
-- covers `status='queued' ORDER BY created_at`, so the subquery needs no sort,
-- which is exactly when a rescan looks cheap to the planner. Identical
-- created_at values across a batch from request_research make the ordering a
-- tie, so the rows it walks are effectively arbitrary.
--
-- The fix makes the limit a fact rather than a plan detail: a MATERIALIZED CTE
-- is evaluated exactly once, and the UPDATE joins to its output. `LIMIT 1`
-- now means one row no matter which plan is chosen.
-- `, id` breaks the created_at tie deterministically.
--
-- db/tests/test_0013_worker.sql pins this down by forcing the adverse plan with
-- SET LOCAL planner GUCs, so the assertion does not depend on the planner's
-- mood on the day it runs.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.claim_research_jobs(_limit int)
RETURNS SETOF research_jobs LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
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
$$;
REVOKE ALL ON FUNCTION public.claim_research_jobs(int) FROM PUBLIC, anon, authenticated;

COMMENT ON FUNCTION public.claim_research_jobs(int) IS
  'Claims at most _limit queued research jobs. The MATERIALIZED CTE is load-bearing: with the limit inside an IN-subquery instead, a Nested Loop Semi Join re-executes it per outer row and claims the whole queue.';

-- ---------------------------------------------------------------------------
-- 2. WORKER CONFIG LIVES IN `vault`.
--
-- The worker needs the project url, the Service Role key and the agent id.
-- None of the three may sit in a table under `public`: everything there is one
-- PostgREST request away from a client, and a leaked service key is a total
-- compromise of the project (it bypasses RLS everywhere). `vault` is not in
-- PostgREST's exposed schemas and `authenticated`/`anon` hold no USAGE on the
-- schema at all, so there is no request that reaches it.
--
-- There is deliberately no read-side function. run_research_tick() selects
-- from vault.decrypted_secrets itself; a `get_worker_config()` would be a
-- service-role-callable endpoint that hands back the service key, which is
-- exactly the shape we are avoiding.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.set_research_worker_config(
  _project_url text, _service_key text, _agent_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_url text := nullif(rtrim(trim(coalesce(_project_url, '')), '/'), '');
  v_key text := nullif(trim(coalesce(_service_key, '')), '');
  v_agent text := nullif(trim(coalesce(_agent_id, '')), '');
  v_names text[] := ARRAY['powacrm_project_url', 'powacrm_service_key', 'powacrm_agent_id'];
  v_vals text[];
  v_id uuid; i int;
BEGIN
  IF v_url IS NULL OR v_key IS NULL OR v_agent IS NULL THEN
    RAISE EXCEPTION 'project url, service key and agent id are all required';
  END IF;
  -- The url and the agent id are concatenated into a request URI below, so they
  -- are validated here rather than trusted: a url with whitespace or a
  -- non-uuid agent id would let a careless setup point the worker's Service
  -- Role key at somebody else's host.
  IF v_url !~ '^https://[A-Za-z0-9._~:/?#\[\]@!$&''()*+,;=%-]+$' THEN
    RAISE EXCEPTION 'project url must be an https URL with no whitespace';
  END IF;
  v_agent := v_agent::uuid::text;   -- raises on anything that is not a uuid

  v_vals := ARRAY[v_url, v_key, v_agent];
  FOR i IN 1 .. array_length(v_names, 1) LOOP
    SELECT s.id INTO v_id FROM vault.secrets s WHERE s.name = v_names[i];
    IF v_id IS NULL THEN
      PERFORM vault.create_secret(v_vals[i], v_names[i], 'PowaCRM research worker config');
    ELSE
      PERFORM vault.update_secret(v_id, v_vals[i], v_names[i], 'PowaCRM research worker config');
    END IF;
  END LOOP;

  -- Names only. Never echo a value: this return travels back through whatever
  -- ran the setup script, and one of the three is the Service Role key.
  RETURN jsonb_build_object('stored', to_jsonb(v_names), 'agent_id', v_agent);
END $$;
REVOKE ALL ON FUNCTION public.set_research_worker_config(text, text, text) FROM PUBLIC, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2b. WHERE A TICK'S DIAGNOSTICS GO.
--
-- run_research_tick() returns a jsonb result on every path -- people_scored,
-- tools_used, the truncation note, the error text -- and pg_cron THROWS IT AWAY.
-- cron.job_run_details records the command's status and, at best, the last
-- notice; the return value of a `SELECT run_research_tick()` is not retained
-- anywhere. Two things were invisible as a result:
--
--   * `tools_used: []` -- an UNGROUNDED run, which is the exact failure this
--     whole migration exists to prevent (see the file header: the workflow
--     `agent` block reported success while executing no tools, so every result
--     it produced was model recall with a fabricated `sources` array). An
--     ungrounded run and a real one were indistinguishable after the fact.
--   * "truncated to the first 25 of 60 people" -- the other 35 leads keep their
--     stage and cannot be re-researched for 30 days, and nobody was told.
--
-- The result now lands on the job row it describes. research_jobs is already
-- readable by the brand's owner and by nobody else (0011: SELECT policy on
-- owns_brand, no write policy at all), which is the right audience: it is their
-- run and their credits. It carries no secret -- the vault values never enter
-- this object, and the error strings are the same ones already stored in
-- research_jobs.error.
-- ---------------------------------------------------------------------------
ALTER TABLE research_jobs ADD COLUMN IF NOT EXISTS diagnostics jsonb;

COMMENT ON COLUMN research_jobs.diagnostics IS
  'What run_research_tick() saw on the tick that touched this job: people_scored, people_total, tools_used (an empty array means the agent ran ungrounded), and any note or error. pg_cron discards the function''s return value, so this is the only durable copy.';

-- Called on every exit path after the claim. A separate function rather than an
-- UPDATE at each `RETURN` for one reason: there are eleven of those returns, and
-- the one that gets forgotten is always the failure path nobody expected.
CREATE OR REPLACE FUNCTION public.record_research_tick(_job_id uuid, _result jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF _job_id IS NOT NULL THEN
    UPDATE research_jobs SET diagnostics = _result WHERE id = _job_id;
  END IF;
  RETURN _result;
END $$;
REVOKE ALL ON FUNCTION public.record_research_tick(uuid, jsonb) FROM PUBLIC, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. THE WORKER.
--
-- Per tick: sweep stalled jobs -> claim exactly one -> build the prompt from
-- the company, the brand's product_description/icp_notes and the people to
-- score -> POST /api/agents/{id}/run/stream -> pull the terminal event out of
-- the SSE body -> extract the JSON object -> complete_research_job, or
-- fail_research_job with something a human can act on.
--
-- EVERY exit after the claim goes through complete_research_job or
-- fail_research_job. The outer exception handler is what makes that true for
-- the paths nobody thought of: without it an unexpected error would roll the
-- whole transaction back, including the attempts increment, and a job that
-- reliably breaks the worker would retry forever instead of failing at three.
--
-- THAT PROMISE NEEDS TWO HANDLERS, NOT ONE. PL/pgSQL's `WHEN OTHERS` does not
-- match ERRCODE_QUERY_CANCELED -- verified on this project (PG 15.8): a
-- statement_timeout cancel inside a block with `EXCEPTION WHEN OTHERS` still
-- escapes as an ERROR. So a tick that outran its statement_timeout would abort
-- the whole transaction, rolling back the claim AND the attempts increment,
-- and the job would come back queued and be retried every single minute
-- forever -- spending an agent run each time and never reaching `failed`. An
-- unbounded credit leak dressed up as a retry. A cancel is therefore caught by
-- name, and a handler for it can still write and commit (also verified: an
-- INSERT made inside a `WHEN query_canceled` handler survives COMMIT).
--
-- Catching a cancel does mean an operator's pg_cancel_backend() is absorbed
-- rather than propagated. That is a deliberate trade for a cron-invoked,
-- self-contained statement with no interactive user behind it: the handler
-- records the failure and returns immediately, so the cancel still ends the
-- tick, it just ends it having told the truth about the job.
--
-- The numbers are set so the cancel path stays theoretical. The HTTP call is
-- hard-bounded at 180 s by http.timeout_msec, against a 300 s statement
-- timeout in the cron command: 120 s of slack for everything else in the tick,
-- which is the SSE split, at most 50 JSON parse attempts, and three small
-- writes -- all measured in milliseconds, never seconds. Observed real runs
-- are 28-45 s, so 180 s is already 4x the worst one seen.
-- ---------------------------------------------------------------------------
-- `extensions` is in the search_path, and it is why the http extension can live
-- outside PostgREST's reach (section 0). Order matters twice over: `public`
-- stays first so this app's own tables and functions resolve to themselves, and
-- `pg_temp` stays LAST, which is 0008's rule -- a temp object cannot shadow a
-- real one from a position nothing resolves to first.
CREATE OR REPLACE FUNCTION public.run_research_tick()
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, extensions, pg_temp AS $$
DECLARE
  -- A company with more contacts than this gets its first MAX_PEOPLE scored;
  -- the rest keep their stage until a later pass. Reported, never silent.
  MAX_PEOPLE constant int := 25;
  MAX_STARTS constant int := 50;

  v_requeued int := 0;
  v_abandoned int := 0;
  v_job research_jobs;
  v_url text; v_key text; v_agent text;
  v_name text; v_domain text; v_product text; v_icp text;
  v_roster text; v_people_n int := 0; v_people_total int := 0;
  v_prompt text; v_note text := '';
  v_http_status int; v_content text;
  v_line text; v_evt jsonb; v_final jsonb; v_stream_error text;
  v_answer text := ''; v_payload jsonb; v_tools jsonb := '[]'::jsonb;
  v_start int; v_end int; v_next int; v_tries int := 0;
  v_res jsonb; v_err text;
BEGIN
  -- Pooler safety: SET LOCAL, never a bare SET. A bare SET survives on the
  -- pooled backend and changes behaviour for every later connection that lands
  -- on it -- earlier in this phase that is exactly how foreign-key enforcement
  -- got disabled project-wide.
  --
  -- statement_timeout is armed when the ENCLOSING statement starts, so this
  -- cannot extend the timer already running on the `SELECT run_research_tick()`
  -- that got us here; it covers later statements in the same transaction. The
  -- cron command therefore sets it too, ahead of the call. Verified: with
  -- statement_timeout 2s, a SET LOCAL to 30s inside the function did not save a
  -- 4-second sleep.
  SET LOCAL statement_timeout = '300s';
  -- pgsql-http's own timeout. It is read when the request is built, so this one
  -- does bite. Without it the extension's default cuts the agent off long
  -- before a 35-50 s research run finishes -- verified: with the GUC unset, an
  -- http_get against an 8-second endpoint fails with "Operation timed out
  -- after 5002 milliseconds".
  --
  -- 180 s, deliberately well under the 300 s statement timeout rather than
  -- close to it. This is the only unbounded step in the tick, so it is what
  -- decides whether a slow run ends as a clean `fail_research_job` (curl gives
  -- up, we report it, three strikes apply) or as a statement cancel. 120 s of
  -- slack is absurd headroom for the millisecond-scale work that follows it,
  -- and that is the point: the cancel path should never be the one that fires.
  SET LOCAL http.timeout_msec = 180000;

  -- --- the sweep, and then STOP if it abandoned anything ------------------
  -- Found in review (round 3), and it is the hole the round-2 backstop fix
  -- opened. One tick is one transaction, so the abandonment this sweep writes
  -- and the agent run below it commit or roll back TOGETHER. The only thing the
  -- backstop exists to bound is a job that terminates the worker backend
  -- uncatchably -- and in exactly that case the claim below kills the backend
  -- and takes the abandonment with it. Nothing is ever recorded, the head is
  -- re-abandoned and re-rolled-back every minute, and the unbounded paid-run
  -- loop the backstop was written to stop runs forever.
  --
  -- The age-only version was accidentally immune: it failed EVERY old job, so
  -- the claim found an empty queue and the tick always committed. Abandoning
  -- only the head is what leaves something behind to claim.
  --
  -- So: when the sweep abandoned a job, this tick's only job is to commit that.
  -- Returning here ends the transaction with the abandonment durable, and the
  -- next tick -- one minute later -- claims and runs. The cost is one tick of
  -- throughput, and only on a tick where the queue was already stuck; the
  -- backstop self-disarms to at most one abandonment per hour project-wide (see
  -- 0012), so it is bounded at a minute an hour in the worst case.
  --
  -- The alternative shape is a second cron.schedule entry running the sweep on
  -- its own. It commits independently by construction and costs no throughput,
  -- but it doubles what an operator has to reason about, pause during the test
  -- suite and unschedule on teardown -- for an example app, and for a minute an
  -- hour, that is the more expensive side of the trade.
  --
  -- A requeue does NOT stop the tick. If a 15-minute stall requeue rolls back,
  -- the job simply stays `running` and the next sweep finds it again; no agent
  -- run is spent in the meantime. Only the abandonment must survive.
  SELECT r.requeued, r.abandoned INTO v_requeued, v_abandoned
    FROM requeue_stalled_research_jobs() r;
  IF v_abandoned > 0 THEN
    -- No job was claimed, so there is nothing to record this against with
    -- record_research_tick -- same reason as the empty-queue return below.
    RETURN jsonb_build_object('ok', true, 'requeued', v_requeued, 'abandoned', v_abandoned,
      'claimed', 0, 'note', 'the age backstop abandoned a job; committing that before anything else runs in this transaction');
  END IF;

  SELECT * INTO v_job FROM claim_research_jobs(1);
  IF v_job.id IS NULL THEN
    -- Nothing was claimed, so there is no job row to record this against.
    RETURN jsonb_build_object('ok', true, 'requeued', v_requeued, 'claimed', 0, 'note', 'queue empty');
  END IF;

  BEGIN
    -- --- config ------------------------------------------------------------
    SELECT s.decrypted_secret INTO v_url FROM vault.decrypted_secrets s WHERE s.name = 'powacrm_project_url';
    SELECT s.decrypted_secret INTO v_key FROM vault.decrypted_secrets s WHERE s.name = 'powacrm_service_key';
    SELECT s.decrypted_secret INTO v_agent FROM vault.decrypted_secrets s WHERE s.name = 'powacrm_agent_id';
    IF v_url IS NULL OR v_key IS NULL OR v_agent IS NULL THEN
      PERFORM fail_research_job(v_job.id,
        'worker is not configured: run db/setup/set_worker_config.sh to store the project url, service key and agent id in vault');
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'note', 'worker not configured'));
    END IF;

    -- --- context -----------------------------------------------------------
    -- `deleted_at IS NULL` matters: companies are soft-deleted (0005), so
    -- without it a company deleted between enqueue and claim is still scraped,
    -- still charged for, and still written back -- research appearing on a row
    -- the owner believes they removed. 0012 filters people this way but not
    -- companies; fixed here.
    SELECT c.name, c.domain INTO v_name, v_domain
      FROM companies c WHERE c.id = v_job.company_id AND c.deleted_at IS NULL;
    IF NOT FOUND THEN
      PERFORM fail_research_job(v_job.id, 'company row not found, or deleted since this job was queued');
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'note', 'company missing'));
    END IF;
    v_domain := nullif(trim(coalesce(v_domain, '')), '');
    IF v_domain IS NULL THEN
      -- request_research refuses these at enqueue time, so this only fires when
      -- the domain was cleared between enqueue and claim.
      PERFORM fail_research_job(v_job.id, 'company has no domain to research');
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'note', 'no domain'));
    END IF;

    SELECT b.product_description, b.icp_notes INTO v_product, v_icp
      FROM brands b WHERE b.id = v_job.brand_id;

    -- brand_id is pinned as well as company_id: a person mislinked across
    -- brands must never be smuggled into this brand's prompt.
    SELECT string_agg(format('- person_id: %s | title: %s',
                             p.id, coalesce(nullif(trim(p.title), ''), 'unknown')),
                      E'\n' ORDER BY p.created_at, p.id),
           count(*)
      INTO v_roster, v_people_n
      FROM (SELECT id, title, created_at FROM people
             WHERE company_id = v_job.company_id AND brand_id = v_job.brand_id
               AND deleted_at IS NULL
             ORDER BY created_at, id LIMIT MAX_PEOPLE) p;
    SELECT count(*) INTO v_people_total FROM people
     WHERE company_id = v_job.company_id AND brand_id = v_job.brand_id AND deleted_at IS NULL;

    IF coalesce(v_people_n, 0) = 0 THEN
      PERFORM fail_research_job(v_job.id, 'no people at this company to score');
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'note', 'no people to score'));
    END IF;
    IF v_people_total > v_people_n THEN
      v_note := format('truncated to the first %s of %s people; the rest keep their stage until a later pass',
                       v_people_n, v_people_total);
    END IF;

    v_prompt := format(
      E'Research this company and score the people listed below.\n\nCompany name: %s\nCompany domain: %s\nCompany homepage: https://%s\n\n--- SELLER PRODUCT DESCRIPTION ---\n%s\n\n--- SELLER ICP NOTES ---\n%s\n\n--- PEOPLE TO SCORE ---\n%s\n\nScore every person_id above exactly once, copying each uuid verbatim.',
      coalesce(nullif(trim(coalesce(v_name, '')), ''), v_domain), v_domain, v_domain,
      coalesce(nullif(trim(coalesce(v_product, '')), ''), '(not provided)'),
      coalesce(nullif(trim(coalesce(v_icp, '')), ''), '(not provided)'),
      v_roster);

    -- --- run the agent -----------------------------------------------------
    -- /run/stream and nothing else. The non-streaming /run returns success
    -- having executed no tools; see the header of this file.
    BEGIN
      SELECT r.status, r.content INTO v_http_status, v_content
        FROM http((
          'POST',
          v_url || '/api/agents/' || v_agent || '/run/stream',
          ARRAY[http_header('apikey', v_key),
                http_header('Authorization', 'Bearer ' || v_key),
                http_header('Accept', 'text/event-stream')],
          'application/json',
          jsonb_build_object('message', v_prompt)::text
        )::http_request) r;
    EXCEPTION WHEN OTHERS THEN
      -- Covers the transport: DNS, TLS, connection reset, and the curl timeout
      -- set above. Never let the service key reach the error text.
      v_err := left(SQLERRM, 400);
      PERFORM fail_research_job(v_job.id, 'agent request failed: ' || v_err);
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'note', 'agent request failed', 'error', v_err));
    END;

    IF v_http_status IS NULL OR v_http_status >= 300 THEN
      -- The upstream body goes to the SERVER LOG, not onto the job row. Fixed in
      -- review (round 2): research_jobs.error is readable by the brand's owner
      -- (0011's SELECT policy), and this is the one place a verbatim response
      -- from another service was copied into it. Nothing secret can reach that
      -- body today -- but "today" is doing the work in that sentence, and the
      -- audience for an upstream 5xx body is the operator, who has the log.
      -- diagnostics keeps the status code, which is the part the owner needs to
      -- tell "the agent is down" from "my company has no domain".
      RAISE WARNING 'powacrm worker: agent stream returned HTTP % for job %; first 300 bytes of the body: %',
        v_http_status, v_job.id, left(coalesce(v_content, ''), 300);
      PERFORM fail_research_job(v_job.id,
        format('agent stream HTTP %s. The response body is in the Postgres server log for this tick (cron.job_run_details names the run), not here.', v_http_status));
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'http_status', v_http_status, 'note', 'agent stream returned an error status'));
    END IF;

    -- --- find the terminal event in the SSE body ---------------------------
    -- The body is the whole text/event-stream, one JSON object per `data:`
    -- line. Only lines that actually declare themselves complete/error are
    -- parsed: a run emits hundreds of content_delta frames and each parse
    -- attempt costs a subtransaction.
    FOR v_line IN
      SELECT l FROM regexp_split_to_table(coalesce(v_content, ''), E'\r?\n') AS l
       WHERE l LIKE 'data:%' AND l ~ '"(event|type)"\s*:\s*"(complete|error)"'
    LOOP
      BEGIN
        v_evt := substr(v_line, 6)::jsonb;
      EXCEPTION WHEN OTHERS THEN
        v_evt := NULL;   -- one unreadable frame is not worth losing the run over
      END;
      CONTINUE WHEN v_evt IS NULL OR jsonb_typeof(v_evt) IS DISTINCT FROM 'object';
      IF coalesce(v_evt->>'event', v_evt->>'type') = 'complete' THEN
        v_final := v_evt;
      ELSIF coalesce(v_evt->>'event', v_evt->>'type') = 'error' THEN
        v_stream_error := left(coalesce(v_evt->>'error', v_evt->>'message', v_evt::text), 300);
      END IF;
    END LOOP;

    IF v_final IS NULL THEN
      -- Deliberately NOT falling back to the accumulated content_delta text: a
      -- truncated stream means a truncated JSON object, and completing a job
      -- from half a report is worse than failing it.
      --
      -- The upstream `error` frame goes to the SERVER LOG, not onto the job row.
      -- Same rule as the HTTP body above (fixed in review round 2, extended to
      -- the remaining three sites in round 3): research_jobs.error is readable
      -- by the brand's owner under 0011's SELECT policy, and every one of these
      -- strings is written by another service. Nothing secret can reach them
      -- TODAY -- and "today" is the whole load-bearing word. The audience for a
      -- verbatim upstream string is the operator, who has the log.
      IF v_stream_error IS NOT NULL THEN
        RAISE WARNING 'powacrm worker: agent stream carried an error frame for job %: %', v_job.id, v_stream_error;
      END IF;
      PERFORM fail_research_job(v_job.id,
        'agent stream ended with no complete event'
        || CASE WHEN v_stream_error IS NULL THEN ''
                ELSE '. The stream carried an error frame; its text is in the Postgres server log for this tick (cron.job_run_details names the run), not here.' END);
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'note', 'no terminal event in the agent stream'));
    END IF;
    IF coalesce(v_final->>'status', '') <> 'completed' OR nullif(v_final->>'error', '') IS NOT NULL THEN
      -- The upstream error text to the log, the status code to the row: same
      -- split as the HTTP failure above. The status is the part the owner needs
      -- in order to tell "the agent is down" from "my data is wrong"; the
      -- message is another service's prose and belongs to the operator.
      RAISE WARNING 'powacrm worker: agent run did not complete for job % (status %); error: %',
        v_job.id, coalesce(v_final->>'status', 'null'), left(coalesce(v_final->>'error', ''), 300);
      PERFORM fail_research_job(v_job.id,
        format('agent run did not complete: status=%s. The error text is in the Postgres server log for this tick (cron.job_run_details names the run), not here.',
               coalesce(v_final->>'status', 'null')));
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'note', 'agent run did not complete'));
    END IF;

    v_answer := coalesce(v_final->>'content', '');
    -- Tool names only -- the full tool_calls carry every scrape verbatim, tens
    -- of KB each. An ungrounded run shows up here as an empty list, which is
    -- the one thing worth being able to see from outside.
    --
    -- The FILTER is load-bearing, and it was missing. jsonb_agg over elements
    -- that carry no `tool_name` produces `[null]` -- length 1, not 0 -- which
    -- sails through the grounding gate immediately below and lets exactly the
    -- ungrounded run this file exists to stop be written as research. Elements
    -- without a usable name are not evidence that a tool ran.
    IF jsonb_typeof(v_final->'tool_calls') = 'array' THEN
      SELECT coalesce(jsonb_agg(t->>'tool_name') FILTER (WHERE nullif(t->>'tool_name', '') IS NOT NULL), '[]'::jsonb)
        INTO v_tools
        FROM jsonb_array_elements(v_final->'tool_calls') t;
    END IF;

    -- AN UNGROUNDED RUN IS A FAILED RUN, and this is the one place it can be
    -- detected. The header of this file is the whole argument: phase 2's
    -- workflow `agent` block executed NO TOOLS while reporting success, so every
    -- research result it produced was model recall wearing a fabricated
    -- `sources` array -- confident, plausible, and about a company nobody read.
    -- That is worse than an error, because an error is visible. An empty
    -- tool_calls list on the complete event is exactly that failure, and it must
    -- not be allowed to reach complete_research_job: once written, a fabricated
    -- report is indistinguishable from a real one and it locks the company for
    -- thirty days.
    --
    -- Failing hands it to fail_research_job, so it retries and gives up at three
    -- attempts rather than looping.
    IF jsonb_array_length(v_tools) = 0 THEN
      PERFORM fail_research_job(v_job.id,
        'agent completed without calling any tool -- the report would be ungrounded model recall, not research. Check the agent has web_scrape and web_search attached (platform/provision.sh) and that this is the /run/stream endpoint.');
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'tools_used', v_tools, 'note', 'ungrounded run: the agent called no tools'));
    END IF;

    -- --- extract the JSON object -------------------------------------------
    -- The agent narrates for roughly a thousand characters, then emits a
    -- ```json fence, then the object. Stripping fences is not enough because
    -- prose comes first: first `{` to last `}` is what parses. The loop only
    -- matters when the narration itself contains a brace, in which case each
    -- later `{` is retried against the same final `}`.
    v_end := CASE WHEN position('}' in reverse(v_answer)) = 0
                  THEN 0 ELSE length(v_answer) - position('}' in reverse(v_answer)) + 1 END;
    v_start := position('{' in v_answer);
    WHILE v_payload IS NULL AND v_start > 0 AND v_start < v_end AND v_tries < MAX_STARTS LOOP
      BEGIN
        v_evt := substr(v_answer, v_start, v_end - v_start + 1)::jsonb;
        IF jsonb_typeof(v_evt) = 'object' THEN v_payload := v_evt; END IF;
      EXCEPTION WHEN OTHERS THEN
        NULL;
      END;
      v_tries := v_tries + 1;
      v_next := position('{' in substr(v_answer, v_start + 1));
      v_start := CASE WHEN v_next = 0 THEN 0 ELSE v_start + v_next END;
    END LOOP;

    IF v_payload IS NULL THEN
      -- The reply itself is the most verbatim of the three: it is whatever the
      -- model wrote after reading attacker-controlled web pages. It goes to the
      -- log. The owner gets the fact and the length, which is enough to tell an
      -- empty reply from a long one that would not parse.
      RAISE WARNING 'powacrm worker: no parseable JSON in the agent reply for job %; first 300 characters: %',
        v_job.id, left(v_answer, 300);
      PERFORM fail_research_job(v_job.id,
        format('agent returned no parseable JSON object (the reply was %s characters). The reply is in the Postgres server log for this tick (cron.job_run_details names the run), not here.',
               length(v_answer)));
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'tools_used', v_tools, 'note', 'no parseable JSON in the agent reply'));
    END IF;

    -- --- write it back -----------------------------------------------------
    -- complete_research_job validates the payload itself and RAISES on a
    -- malformed one; that is a real failure, so hand the job back to retry or
    -- give up at three attempts.
    BEGIN
      v_res := complete_research_job(v_job.id, v_payload);
    EXCEPTION WHEN OTHERS THEN
      v_err := left(SQLERRM, 400);
      PERFORM fail_research_job(v_job.id, 'complete_research_job rejected the payload: ' || v_err);
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'tools_used', v_tools, 'note', 'complete rejected', 'error', v_err));
    END;

    -- ok:false from complete_research_job is its status guard, not an error:
    -- the job was no longer `running`, i.e. somebody else already finished it.
    -- Failing it here would flip finished work backwards, so this is a no-op.
    RETURN record_research_tick(v_job.id, jsonb_build_object(
      'ok', coalesce((v_res->>'ok')::boolean, false),
      'requeued', v_requeued, 'claimed', 1, 'job_id', v_job.id,
      'people_scored', coalesce((v_res->>'people_scored')::int, 0),
      'people_total', v_people_total, 'tools_used', v_tools,
      'note', coalesce(nullif(v_res->>'reason', ''), v_note)));

  EXCEPTION
    -- Listed by name because `OTHERS` does not match it -- see the header. This
    -- is the handler that keeps the three-strike promise honest when a tick
    -- outruns its statement_timeout: without it the abort would roll back the
    -- claim and the attempts increment together, and the job would be retried
    -- every minute forever at the price of an agent run each time.
    WHEN query_canceled THEN
      v_err := left(SQLERRM, 400);
      PERFORM fail_research_job(v_job.id,
        'worker was cancelled mid-tick (statement timeout or pg_cancel_backend): ' || v_err);
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'note', 'worker cancelled', 'error', v_err));

    WHEN OTHERS THEN
      -- The backstop. The claim above happened before this subtransaction
      -- opened, so it survives the rollback and fail_research_job can still
      -- record why.
      v_err := left(SQLERRM, 400);
      PERFORM fail_research_job(v_job.id, 'worker error: ' || v_err);
      RETURN record_research_tick(v_job.id, jsonb_build_object('ok', false, 'requeued', v_requeued, 'claimed', 1,
        'job_id', v_job.id, 'note', 'worker error', 'error', v_err));
  END;
END $$;
REVOKE ALL ON FUNCTION public.run_research_tick() FROM PUBLIC, anon, authenticated;

COMMENT ON FUNCTION public.run_research_tick() IS
  'Research worker: one job per tick, called by the pg_cron job powacrm-research-tick. Service-role/superuser only -- it holds the Service Role key from vault and bypasses RLS.';

-- ---------------------------------------------------------------------------
-- 4. SCHEDULE IT.
--
-- Idempotent: unschedule first if it is already there, so re-applying this
-- migration does not leave two workers racing each other.
--
-- The command sets statement_timeout ahead of the call because a statement's
-- timer is armed before the function body runs -- the SET LOCAL inside
-- run_research_tick() cannot extend it. SET LOCAL, not SET: pg_cron runs each
-- job in its own transaction, and a bare SET is a habit worth not having on a
-- project reached through a connection pooler.
--
-- 300 s against the tick's own 180 s HTTP ceiling. The gap is the budget for
-- everything that is not the agent call, and it is deliberately enormous
-- relative to that work (milliseconds) so the statement timeout is a backstop
-- rather than a mechanism the worker relies on. Shrinking it below the HTTP
-- ceiling would invert the two and make the cancel path routine.
--
-- cron.database_name on a Powabase project is `postgres`, which is what
-- current_database() returns over the pooler too, so the job runs against the
-- same database as these tables.
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'powacrm-research-tick') THEN
    PERFORM cron.unschedule('powacrm-research-tick');
  END IF;
  PERFORM cron.schedule_in_database(
    'powacrm-research-tick', '* * * * *',
    $cmd$SET LOCAL statement_timeout = '300s'; SELECT public.run_research_tick();$cmd$,
    current_database());
END $do$;

COMMIT;
