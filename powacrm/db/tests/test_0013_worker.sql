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
DECLARE b uuid; c uuid; i int; n int; claimed int;
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
    INSERT INTO research_jobs (brand_id, company_id, created_at)
      VALUES (b, c, timestamptz '2000-01-01 00:00:00+00');
  END LOOP;

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
  INSERT INTO research_jobs (brand_id, company_id, created_at)
    VALUES (b, c, timestamptz '2000-01-01 00:00:00+00') RETURNING id INTO j;

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

  -- (b) a company with a domain but nobody to score. Scoring is per person, so
  --     there is nothing for the agent to return -- fail before spending a run.
  INSERT INTO companies (brand_id, name, domain)
    VALUES (b, '_t13_no_people', '_t13-no-people.invalid') RETURNING id INTO c;
  INSERT INTO research_jobs (brand_id, company_id, created_at)
    VALUES (b, c, timestamptz '2000-01-01 00:00:00+00') RETURNING id INTO j;

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
-- 3. Posture. The worker holds the Service Role key and bypasses RLS, so the
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
