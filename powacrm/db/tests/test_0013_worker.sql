-- ============================================================================
-- 0013: the in-database research worker.
--
-- THE WHOLE FILE RUNS IN ONE TRANSACTION AND ENDS IN ROLLBACK. That is not
-- tidiness, it is the only way this test is safe to run against a live
-- project: `powacrm-research-tick` is firing every minute while the suite
-- runs, and a committed fixture job would be claimed by the real worker, which
-- would call the agent and spend credits on a company that does not exist.
-- Uncommitted rows are invisible to it. The failures below all raise, and
-- apply.sh runs psql with ON_ERROR_STOP=1, so a red test still stops the suite.
--
-- Not covered here, by design: the agent call itself. Everything below stops
-- short of an HTTP request on purpose -- the grounded end-to-end run is
-- db/tests/test_0012_injection.sh (a real agent run) plus the manual
-- verification recorded in the phase 2b task report. A unit test that spends
-- platform credits and takes 45 seconds is not a unit test.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. claim_research_jobs(1) claims ONE job, whatever plan the planner picks.
--
-- This is the regression test for a real defect: one tick claimed all four
-- queued jobs and stranded three in `running` until the 15-minute sweep. The
-- old body put the limit inside an IN-subquery --
--
--   WHERE id IN (SELECT id ... ORDER BY created_at LIMIT _limit FOR UPDATE SKIP LOCKED)
--
-- -- which bounds the subquery, not the UPDATE. Given a Nested Loop Semi Join
-- with that subquery on the inner side and no Materialize above it, the
-- subquery is re-executed per outer row and hands back a different queued job
-- every time, so the UPDATE claims all of them.
--
-- WHY THE PLANNER GUCs. With four rows and no statistics the planner picks a
-- Hash Semi Join, which evaluates the subquery once and hides the bug -- so a
-- test run under default settings would pass against the BROKEN function and
-- prove nothing. These five SET LOCALs force the adverse shape, which is not a
-- contrived one: research_jobs_queue_idx already covers
-- `status='queued' ORDER BY created_at`, so the subquery needs no sort, and a
-- rescan is exactly what looks cheap to the planner on a real queue. Verified
-- both ways on the live project: the old definition claims 4 here, the CTE
-- claims 1.
--
-- SET LOCAL, never a bare SET: PB_DB_URL is a connection pooler and a bare SET
-- rides the backend back into the pool. Turning off the planner's join
-- strategies for every later connection would be a memorable way to find out.
-- ---------------------------------------------------------------------------
SET LOCAL enable_hashjoin = off;
SET LOCAL enable_mergejoin = off;
SET LOCAL enable_material = off;
SET LOCAL enable_hashagg = off;
SET LOCAL enable_sort = off;

DO $$
DECLARE b uuid; c uuid; i int; n int; claimed int; v_line text; v_plan text := '';
BEGIN
  SELECT id INTO b FROM brands WHERE name = 'gpt-trainer';
  IF b IS NULL THEN RAISE EXCEPTION 'no gpt-trainer brand -- apply db/seed/seed_gpt_trainer.sql first'; END IF;

  -- Determinism: claim_research_jobs works on the queue PROJECT-WIDE, so any
  -- real queued job would compete with these fixtures. Park them for the
  -- duration; the ROLLBACK at the end of the file puts them back untouched.
  UPDATE research_jobs SET status = 'skipped' WHERE status = 'queued';

  FOR i IN 1..4 LOOP
    -- One job per company: research_jobs_one_active is a per-company partial
    -- unique index, so four queued jobs need four companies. `.invalid` is
    -- reserved by RFC 2606 and can never resolve, which is the belt to the
    -- ROLLBACK's braces.
    INSERT INTO companies (brand_id, name, domain)
      VALUES (b, '_t13_claim_' || i, '_t13-claim-' || i || '.invalid') RETURNING id INTO c;
    -- Identical created_at on purpose: request_research stamps a whole batch
    -- with the same timestamp, so ORDER BY created_at is a four-way tie -- part
    -- of what made the original bug bite.
    --
    -- 59 MINUTES, NOT AN ARBITRARY LONG TIME AGO. These fixtures used to be
    -- dated 2000-01-01, which the age backstop in
    -- requeue_stalled_research_jobs() now (correctly) treats as abandoned: a job
    -- older than one hour is failed rather than claimed. Section 1 calls
    -- claim_research_jobs directly and never sweeps, but section 2 goes through
    -- run_research_tick(), which sweeps BEFORE it claims -- and with a 26-year
    -- old fixture that tick found an empty queue. Any fixture that must still be
    -- claimable belongs inside the window.
    INSERT INTO research_jobs (brand_id, company_id, created_at)
      VALUES (b, c, now() - interval '59 minutes');
  END LOOP;

  -- Pin the plan before relying on it. Without this the guard is a hope: if a
  -- future planner finds some other one-shot shape under these GUCs, the
  -- subquery is evaluated once anyway, the claim is correct for the wrong
  -- reason, and this test goes green having proved nothing about the fix. Two
  -- tests in this project have already shipped in that state. EXPLAIN without
  -- ANALYZE plans but does not execute, so this changes nothing.
  FOR v_line IN EXECUTE
    'EXPLAIN (COSTS OFF) UPDATE research_jobs SET status = ''running''
      WHERE id IN (SELECT id FROM research_jobs WHERE status = ''queued''
                    ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)'
  LOOP
    v_plan := v_plan || v_line || E'\n';
  END LOOP;

  IF v_plan NOT LIKE '%Nested Loop Semi Join%' THEN
    RAISE EXCEPTION 'the adverse plan is no longer reachable under these GUCs, so the claim assertion below proves nothing. Plan was:%', E'\n' || v_plan;
  END IF;
  -- Any of these above the subquery means it is evaluated once regardless of
  -- the join, which is exactly the situation that hides the bug.
  IF v_plan LIKE '%Materialize%' OR v_plan LIKE '%HashAggregate%' OR v_plan LIKE '%->  Unique%' THEN
    RAISE EXCEPTION 'the subquery is being evaluated once anyway (Materialize/HashAggregate/Unique in the plan), so this test cannot see the bug. Plan was:%', E'\n' || v_plan;
  END IF;

  SELECT count(*) INTO claimed FROM claim_research_jobs(1);
  IF claimed <> 1 THEN
    RAISE EXCEPTION 'claim_research_jobs(1) returned % rows, expected 1 -- the limit is not being materialised once', claimed;
  END IF;

  SELECT count(*) INTO n FROM research_jobs WHERE status = 'running';
  IF n <> 1 THEN
    RAISE EXCEPTION 'claim_research_jobs(1) left % jobs running, expected 1 -- the other ticks'' jobs are stranded until the 15-minute sweep', n;
  END IF;

  SELECT count(*) INTO n FROM research_jobs WHERE status = 'queued';
  IF n <> 3 THEN
    RAISE EXCEPTION 'expected 3 jobs still queued after claiming 1 of 4, got %', n;
  END IF;

  -- The claimed job is a real claim, not just a status flip.
  IF NOT EXISTS (SELECT 1 FROM research_jobs WHERE status = 'running' AND attempts = 1 AND started_at IS NOT NULL) THEN
    RAISE EXCEPTION 'the claimed job has no attempt count or no started_at';
  END IF;
END $$;

RESET enable_hashjoin;
RESET enable_mergejoin;
RESET enable_material;
RESET enable_hashagg;
RESET enable_sort;

-- ---------------------------------------------------------------------------
-- 2. run_research_tick()'s cheap paths: an empty queue, and the two context
--    faults that must fail a job in words rather than reach the agent.
--
-- Every assertion here stops before the HTTP call, so this costs nothing and
-- takes milliseconds. What it pins down is the rule from the spec: a job is
-- never left `running`, and every failure carries an error a human can act on.
-- ---------------------------------------------------------------------------
DO $$
DECLARE b uuid; c uuid; j uuid; r jsonb; st text; e text; att int;
BEGIN
  SELECT id INTO b FROM brands WHERE name = 'gpt-trainer';
  -- Retire section 1's fixtures so the queue is genuinely empty.
  UPDATE research_jobs SET status = 'skipped' WHERE status IN ('queued', 'running');

  r := run_research_tick();
  IF coalesce((r->>'claimed')::int, -1) <> 0 THEN
    RAISE EXCEPTION 'a tick on an empty queue should claim nothing, got %', r;
  END IF;
  IF (r->>'ok')::boolean IS NOT TRUE THEN
    RAISE EXCEPTION 'a tick on an empty queue should report ok, got %', r;
  END IF;

  -- (a) the company lost its domain between enqueue and claim. request_research
  --     refuses domainless companies, so this is only reachable by editing the
  --     company afterwards -- and it must not become an agent call on
  --     "https://".
  INSERT INTO companies (brand_id, name) VALUES (b, '_t13_no_domain') RETURNING id INTO c;
  -- 59 minutes, not an arbitrary long time ago -- see section 1's note: the
  -- tick sweeps before it claims, and the sweep abandons anything over an hour.
  INSERT INTO research_jobs (brand_id, company_id, created_at)
    VALUES (b, c, now() - interval '59 minutes') RETURNING id INTO j;

  r := run_research_tick();
  IF (r->>'job_id') IS DISTINCT FROM j::text THEN
    RAISE EXCEPTION 'the tick claimed a different job than the one queued: %', r;
  END IF;
  IF (r->>'ok')::boolean IS NOT FALSE THEN
    RAISE EXCEPTION 'the tick reported ok for a company with no domain: %', r;
  END IF;
  SELECT status, error, attempts INTO st, e, att FROM research_jobs WHERE id = j;
  IF st <> 'queued' THEN
    RAISE EXCEPTION 'a first failure must hand the job back to queued, got % (a job must never be left running)', st;
  END IF;
  IF att <> 1 THEN RAISE EXCEPTION 'the failed job did not record its attempt (attempts=%)', att; END IF;
  IF coalesce(e, '') NOT LIKE '%no domain%' THEN
    RAISE EXCEPTION 'the failure error is not readable: %', coalesce(e, '(null)');
  END IF;
  UPDATE research_jobs SET status = 'skipped' WHERE id = j;

  -- (a2) the company was soft-deleted after the job was queued. Companies are
  --      soft-deleted (0005), so without a `deleted_at IS NULL` filter the
  --      worker happily scrapes it, spends the credits, and writes research
  --      onto a row the owner believes they removed.
  INSERT INTO companies (brand_id, name, domain)
    VALUES (b, '_t13_deleted', '_t13-deleted.invalid') RETURNING id INTO c;
  -- 59 minutes, not an arbitrary long time ago -- see section 1's note: the
  -- tick sweeps before it claims, and the sweep abandons anything over an hour.
  INSERT INTO research_jobs (brand_id, company_id, created_at)
    VALUES (b, c, now() - interval '59 minutes') RETURNING id INTO j;
  UPDATE companies SET deleted_at = now() WHERE id = c;

  r := run_research_tick();
  IF (r->>'job_id') IS DISTINCT FROM j::text THEN
    RAISE EXCEPTION 'the tick claimed a different job than the one queued: %', r;
  END IF;
  SELECT status, error INTO st, e FROM research_jobs WHERE id = j;
  IF st <> 'queued' THEN RAISE EXCEPTION 'a first failure must hand the job back to queued, got %', st; END IF;
  IF coalesce(e, '') NOT LIKE '%deleted%' THEN
    RAISE EXCEPTION 'a soft-deleted company was not reported as such: %', coalesce(e, '(null)');
  END IF;
  IF (SELECT researched_at FROM companies WHERE id = c) IS NOT NULL THEN
    RAISE EXCEPTION 'a soft-deleted company was researched and written back';
  END IF;
  UPDATE research_jobs SET status = 'skipped' WHERE id = j;

  -- (b) a company with a domain but nobody to score. Scoring is per person, so
  --     there is nothing for the agent to return -- fail before spending a run.
  INSERT INTO companies (brand_id, name, domain)
    VALUES (b, '_t13_no_people', '_t13-no-people.invalid') RETURNING id INTO c;
  -- 59 minutes, not an arbitrary long time ago -- see section 1's note: the
  -- tick sweeps before it claims, and the sweep abandons anything over an hour.
  INSERT INTO research_jobs (brand_id, company_id, created_at)
    VALUES (b, c, now() - interval '59 minutes') RETURNING id INTO j;

  r := run_research_tick();
  IF (r->>'job_id') IS DISTINCT FROM j::text THEN
    RAISE EXCEPTION 'the tick claimed a different job than the one queued: %', r;
  END IF;
  SELECT status, error, attempts INTO st, e, att FROM research_jobs WHERE id = j;
  IF st <> 'queued' THEN RAISE EXCEPTION 'a first failure must hand the job back to queued, got %', st; END IF;
  IF coalesce(e, '') NOT LIKE '%no people%' THEN
    RAISE EXCEPTION 'the failure error is not readable: %', coalesce(e, '(null)');
  END IF;

  -- Third strike is terminal, and it is still not left running.
  UPDATE research_jobs SET status = 'running', attempts = 3 WHERE id = j;
  r := run_research_tick();
  UPDATE research_jobs SET status = 'queued', attempts = 2 WHERE id = j;
  r := run_research_tick();
  SELECT status INTO st FROM research_jobs WHERE id = j;
  IF st <> 'failed' THEN RAISE EXCEPTION 'the third attempt should be terminal, got %', st; END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. A cancelled tick must fail its job, not silently retry it forever.
--
-- PL/pgSQL's `WHEN OTHERS` does not match ERRCODE_QUERY_CANCELED. That is not
-- folklore, it is asserted here against this server, because the worker's
-- three-strike guarantee depends on it: if a statement_timeout cancel escaped
-- run_research_tick()'s handler, the transaction would abort, the claim and the
-- attempts increment would roll back together, and the job would be retried
-- every minute forever -- one agent run per minute, never reaching `failed`.
--
-- The tick's own cancel path cannot be driven from here without making a real
-- agent call and then cutting it off, so this asserts the two halves that
-- compose it: that the platform behaves as the design assumes, and that the
-- worker actually carries the named handler.
-- ---------------------------------------------------------------------------
SET LOCAL statement_timeout = '1s';

DO $$
DECLARE caught_by_others boolean := false; caught_by_name boolean := false; src text;
BEGIN
  BEGIN
    PERFORM pg_sleep(3);
  EXCEPTION
    WHEN query_canceled THEN caught_by_name := true;
    WHEN OTHERS THEN caught_by_others := true;
  END;

  IF caught_by_others THEN
    RAISE EXCEPTION 'WHEN OTHERS caught a statement_timeout cancel on this server -- the named handler in run_research_tick() may now be redundant, but check before removing it';
  END IF;
  IF NOT caught_by_name THEN
    RAISE EXCEPTION 'a statement_timeout cancel was matched by neither handler -- run_research_tick() cannot report a cancelled tick at all';
  END IF;

  -- And the worker carries it. This is a source assertion on purpose: the
  -- handler is one line that looks removable during a tidy-up, and losing it
  -- turns a slow tick into an unbounded credit leak with no other symptom.
  src := pg_get_functiondef('public.run_research_tick()'::regprocedure);
  IF src NOT LIKE '%WHEN query_canceled%' THEN
    RAISE EXCEPTION 'run_research_tick() has no `WHEN query_canceled` handler -- a tick that outruns statement_timeout would roll back its own claim and be retried every minute forever';
  END IF;
END $$;

RESET statement_timeout;

-- ---------------------------------------------------------------------------
-- 3b. A tick's diagnostics survive it, and an ungrounded run is a failure.
--
-- pg_cron discards the value a `SELECT run_research_tick()` returns, so before
-- research_jobs.diagnostics existed, `tools_used: []` -- an agent that completed
-- having called NO tools, i.e. the ungrounded run this whole migration exists to
-- prevent -- looked exactly like a real research run afterwards. So did
-- "truncated to the first 25 of 60 people", while the other 35 leads kept their
-- stage and could not be re-researched for thirty days.
--
-- The ungrounded-run refusal itself needs a real agent call to drive, so it is
-- asserted on the source, the same way the query_canceled handler above is: it
-- is a guard that reads like a nice-to-have and whose removal has no symptom
-- other than fabricated research being stored as fact.
-- ---------------------------------------------------------------------------
DO $$
DECLARE b uuid; c uuid; j uuid; src text; d jsonb;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema='public' AND table_name='research_jobs' AND column_name='diagnostics') THEN
    RAISE EXCEPTION 'research_jobs has no diagnostics column -- every tick result would be returned to pg_cron and discarded';
  END IF;

  src := pg_get_functiondef('public.run_research_tick()'::regprocedure);
  IF src NOT LIKE '%jsonb_array_length(v_tools) = 0%' THEN
    RAISE EXCEPTION 'run_research_tick() no longer refuses a run that called no tools -- an ungrounded report would be written as research and would lock the company for 30 days';
  END IF;
  IF src NOT LIKE '%record_research_tick(v_job.id%' THEN
    RAISE EXCEPTION 'run_research_tick() no longer records its result on the job row';
  END IF;

  -- And the recorder actually writes. Rolled back with the rest of the file.
  SELECT id INTO b FROM brands WHERE name = 'gpt-trainer';
  INSERT INTO companies (brand_id, name, domain) VALUES (b, '_t13_diag_co', 't13diag.example') RETURNING id INTO c;
  INSERT INTO research_jobs (brand_id, company_id) VALUES (b, c) RETURNING id INTO j;
  PERFORM record_research_tick(j, jsonb_build_object('ok', false, 'tools_used', '[]'::jsonb, 'note', 'probe'));
  SELECT diagnostics INTO d FROM research_jobs WHERE id = j;
  IF d IS NULL OR d->>'note' <> 'probe' THEN
    RAISE EXCEPTION 'record_research_tick did not persist the tick result (got %)', coalesce(d::text, 'null');
  END IF;

  IF has_function_privilege('authenticated', 'public.record_research_tick(uuid,jsonb)', 'EXECUTE')
     OR has_function_privilege('anon', 'public.record_research_tick(uuid,jsonb)', 'EXECUTE') THEN
    RAISE EXCEPTION 'record_research_tick is callable by a client role -- it writes an arbitrary jsonb onto any job row';
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3c. The crash-loop backstop: a job that never gets old enough to fail is a
--     standing charge on the owner's credits.
--
-- fail_research_job stops at three attempts, and that ceiling CANNOT bound a
-- worker that dies rather than fails. claim_research_jobs writes
-- `attempts = attempts + 1` inside the same transaction as the run, so a backend
-- kill, an OOM, a restart or a pooler drop rolls back the claim and the
-- increment together: the job returns to `queued` with attempts still 0, the
-- next tick claims it a minute later, and it dies again -- one paid agent run
-- per minute, forever, never reaching `failed`.
--
-- The fixture below is exactly that state and it cannot be faked any other way:
-- an old job with NO recorded attempts. Every other column a sweep might key on
-- (attempts, started_at, updated_at) is written inside the transaction that
-- rolls back, so created_at is the only thing left standing.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  b uuid; c_old uuid; c_young uuid; c_run uuid;
  j_old uuid; j_young uuid; j_running uuid;
  st text; err text; att int;
BEGIN
  SELECT id INTO b FROM brands WHERE name = 'gpt-trainer';

  INSERT INTO companies (brand_id, name, domain) VALUES (b,'_t13_old_co','t13old.example') RETURNING id INTO c_old;
  INSERT INTO companies (brand_id, name, domain) VALUES (b,'_t13_young_co','t13young.example') RETURNING id INTO c_young;
  INSERT INTO companies (brand_id, name, domain) VALUES (b,'_t13_run_co','t13run.example') RETURNING id INTO c_run;

  -- The crash-looper: queued, three hours old, attempts 0 because every
  -- increment rolled back with the worker that died.
  INSERT INTO research_jobs (brand_id, company_id, created_at, attempts)
    VALUES (b, c_old, now() - interval '3 hours', 0) RETURNING id INTO j_old;
  -- An ordinary job waiting its turn. Must NOT be touched.
  INSERT INTO research_jobs (brand_id, company_id, created_at, attempts)
    VALUES (b, c_young, now() - interval '5 minutes', 0) RETURNING id INTO j_young;
  -- A genuinely stalled RUNNING job, young enough to deserve another go: the
  -- pre-existing 15-minute sweep must still return it to the queue, so the
  -- backstop cannot have swallowed that behaviour.
  INSERT INTO research_jobs (brand_id, company_id, created_at, status, started_at, attempts)
    VALUES (b, c_run, now() - interval '20 minutes', 'running', now() - interval '20 minutes', 1)
    RETURNING id INTO j_running;

  PERFORM requeue_stalled_research_jobs();

  SELECT status, error, attempts INTO st, err, att FROM research_jobs WHERE id = j_old;
  IF st <> 'failed' THEN
    RAISE EXCEPTION 'a 3-hour-old queued job with 0 attempts was left as % -- nothing bounds a crash loop, and each retry is a paid agent run', st;
  END IF;
  IF (SELECT finished_at FROM research_jobs WHERE id = j_old) IS NULL THEN
    RAISE EXCEPTION 'the abandoned job was failed without a finished_at';
  END IF;
  -- The error text is the whole point of the finding: an operator has to be able
  -- to tell a crash-looper from an ordinary agent failure, and the low attempts
  -- count on an old job is the signature.
  IF coalesce(err, '') NOT LIKE 'abandoned by the age backstop%' THEN
    RAISE EXCEPTION 'the abandoned job does not say why it was abandoned: %', coalesce(err, '(null)');
  END IF;
  IF err NOT LIKE '%0 recorded attempt(s)%' THEN
    RAISE EXCEPTION 'the abandonment error does not record the attempt count, which is what distinguishes a crash loop: %', err;
  END IF;

  IF (SELECT status FROM research_jobs WHERE id = j_young) <> 'queued' THEN
    RAISE EXCEPTION 'the backstop failed a 5-minute-old queued job -- it must only reach jobs that have outlived the entire three-strike ladder';
  END IF;

  IF (SELECT status FROM research_jobs WHERE id = j_running) <> 'queued' THEN
    RAISE EXCEPTION 'the 20-minute stalled running job was not requeued -- the age backstop displaced the stall sweep instead of preceding it';
  END IF;

  -- And the company is free again: nothing was written for it, so the owner can
  -- ask for it once the cause is fixed.
  IF (SELECT researched_at FROM companies WHERE id = c_old) IS NOT NULL THEN
    RAISE EXCEPTION 'an abandoned job left researched_at stamped, locking the company for 30 days';
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 4. Posture. The worker holds the Service Role key and bypasses RLS, so the
--    only thing standing between a signed-in user and it is these grants.
--    db/apply.sh connects as a superuser, so this asserts the catalogue rather
--    than trying the calls -- test_0012_request_research.sh is where the real
--    tokens live. The HTTP half was checked by hand during phase 2b: as
--    `authenticated`, /rest/v1/rpc/run_research_tick is 403 and the vault
--    schema is not one PostgREST exposes at all (PGRST106).
-- ---------------------------------------------------------------------------
DO $$
DECLARE n int; f text;
BEGIN
  FOREACH f IN ARRAY ARRAY['public.run_research_tick()',
                           'public.set_research_worker_config(text,text,text)',
                           'public.claim_research_jobs(int)'] LOOP
    IF has_function_privilege('authenticated', f, 'EXECUTE') THEN
      RAISE EXCEPTION '`authenticated` can execute % -- it is a worker function and must be service-role only', f;
    END IF;
    IF has_function_privilege('anon', f, 'EXECUTE') THEN
      RAISE EXCEPTION '`anon` can execute %', f;
    END IF;
  END LOOP;

  -- The service key lives in vault precisely because nothing a client reaches
  -- may hold it. A grant of USAGE on the schema would undo that in one line.
  IF has_schema_privilege('authenticated', 'vault', 'USAGE') THEN
    RAISE EXCEPTION '`authenticated` has USAGE on the vault schema -- the Service Role key is in there';
  END IF;
  IF has_schema_privilege('anon', 'vault', 'USAGE') THEN
    RAISE EXCEPTION '`anon` has USAGE on the vault schema';
  END IF;

  -- No table under `public` may be holding the config either: that is the shape
  -- this design exists to avoid.
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'public' AND column_name ILIKE '%service_key%') THEN
    RAISE EXCEPTION 'a public table has a service_key column -- worker config belongs in vault';
  END IF;

  -- THE http EXTENSION TRIPWIRE.
  --
  -- Standing assertion, not a one-time check. 0013 used to install pgsql-http
  -- into `public` -- PostgREST's exposed schema -- and Postgres grants EXECUTE
  -- on new functions to PUBLIC by default, so every signed-up account could
  -- call http_get, http_post and http_set_curlopt over /rest/v1/rpc/. Verified
  -- live before the fix: an ordinary user's JWT got HTTP 200 and the response
  -- body back from
  --   POST /rest/v1/rpc/http_get {"uri":"https://example.com"}
  -- which is server-side request forgery reaching anything routable from the
  -- database host, plus libcurl options mutated on a POOLED backend.
  --
  -- 0013 now installs into `extensions`, relocates an existing one, and revokes
  -- the lot. This assertion exists because none of that is durable on its own:
  -- one `CREATE EXTENSION http` -- or a `DROP EXTENSION` / re-create during an
  -- upgrade, or an operator enabling it from Studio -- re-grants EXECUTE to
  -- PUBLIC and silently reopens the hole. The catalogue is read rather than a
  -- name list being enumerated, so a pgsql-http release that adds a function
  -- is covered the day it lands.
  --
  -- The schema is asserted too. The revoke is what makes it unreachable, but
  -- `public` is also where an accidental GRANT does the most damage, so both
  -- halves of 0013's posture are pinned.
  SELECT extnamespace::regnamespace::text INTO f FROM pg_extension WHERE extname = 'http';
  IF f IS DISTINCT FROM 'extensions' THEN
    RAISE EXCEPTION 'the http extension is in schema % -- 0013 installs it into "extensions" precisely so PostgREST cannot reach it; re-apply db/migrations/0013_inline_worker.sql', coalesce(f, '(not installed)');
  END IF;

  SELECT count(*) INTO n
    FROM pg_proc p
    JOIN pg_depend d ON d.objid = p.oid AND d.classid = 'pg_proc'::regclass AND d.deptype = 'e'
    JOIN pg_extension e ON e.oid = d.refobjid
   WHERE e.extname = 'http'
     AND (has_function_privilege('authenticated', p.oid, 'EXECUTE')
       OR has_function_privilege('anon', p.oid, 'EXECUTE'));
  IF n > 0 THEN
    SELECT string_agg(p.oid::regprocedure::text, ', ' ORDER BY p.oid::regprocedure::text) INTO f
      FROM pg_proc p
      JOIN pg_depend d ON d.objid = p.oid AND d.classid = 'pg_proc'::regclass AND d.deptype = 'e'
      JOIN pg_extension e ON e.oid = d.refobjid
     WHERE e.extname = 'http'
       AND (has_function_privilege('authenticated', p.oid, 'EXECUTE')
         OR has_function_privilege('anon', p.oid, 'EXECUTE'));
    RAISE EXCEPTION '% function(s) owned by the http extension are executable by anon or authenticated -- that is an SSRF grant to every account on this project: %', n, f
      USING HINT = 'Re-apply db/migrations/0013_inline_worker.sql, which revokes every http function from PUBLIC, anon and authenticated. A CREATE EXTENSION anywhere re-grants EXECUTE to PUBLIC, which is why this test asserts it every run.';
  END IF;

  -- The schedule is part of the migration, so its absence is a real failure.
  SELECT count(*) INTO n FROM cron.job
   WHERE jobname = 'powacrm-research-tick' AND active
     AND schedule = '* * * * *'
     AND command LIKE '%run_research_tick%'
     AND database = current_database();
  IF n <> 1 THEN
    RAISE EXCEPTION 'expected exactly 1 active powacrm-research-tick cron job in this database, found % -- re-apply db/migrations/0013_inline_worker.sql', n;
  END IF;
END $$;

ROLLBACK;

SELECT 'test_0013_worker OK' AS result;
