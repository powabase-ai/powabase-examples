-- ============================================================================
-- PHASE 2 RPCs, PART 1: enqueueing.
--
-- This is where authorization for research happens. The SPA passes person ids
-- and nothing else -- no brand_id, because a caller-supplied brand id is exactly
-- what 0010 had to stop trusting. The function resolves person -> company ->
-- brand and checks ownership itself, since SECURITY DEFINER bypasses RLS.
--
-- The daily cap is evaluated in here rather than in the client because every run
-- spends credits. `authenticated` has no INSERT policy on research_jobs at all,
-- so there is no second path that skips this check.
--
-- Fixed in review: people.company_id was a bare FK with no same-brand
-- constraint, and the people_ins policy only checks owns_brand(brand_id) --
-- never the company being linked. That meant a caller could insert a person in
-- their OWN brand whose company_id pointed at a company in a DIFFERENT brand,
-- straight through PostgREST. request_research() resolves brand_id off the
-- person row but writes the job against company_id, so that cross-brand link
-- was a path to enqueueing research (and later, once Task 3 lands, reading
-- job ids and scraped data) against a brand the caller does not own. The fix
-- is structural, not just a query check: a composite FK ties company_id to a
-- company in the SAME brand as the person, so the mismatched row cannot be
-- inserted at all, by this RPC or by anything written later. The query below
-- also checks brand agreement explicitly, as defense in depth for any data
-- that predates this migration.
-- ============================================================================

BEGIN;

-- Structural guard, see note above: without this, cross-brand people/company
-- links are insertable over plain PostgREST (people_ins only checks
-- owns_brand(brand_id), never the company being linked).
--
-- ADD/DROP CONSTRAINT has no IF NOT EXISTS form, unlike every other statement
-- in this migration set (ADD COLUMN, CREATE INDEX, CREATE TABLE all use it) --
-- so this is wrapped to stay re-applicable like the rest.
DO $$ BEGIN
  ALTER TABLE companies ADD CONSTRAINT companies_id_brand_uq UNIQUE (id, brand_id);
-- A UNIQUE constraint's backing index is a relation in its own right, so a
-- re-apply raises duplicate_table (42P07), not duplicate_object (42710).
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE people DROP CONSTRAINT people_company_id_fkey;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE people ADD CONSTRAINT people_company_same_brand_fkey
    FOREIGN KEY (company_id, brand_id) REFERENCES companies (id, brand_id)
    ON DELETE SET NULL (company_id);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

COMMENT ON CONSTRAINT people_company_same_brand_fkey ON people IS
  'Composite FK: company_id must reference a company in the SAME brand_id as this person, so a cross-brand link cannot be inserted at all. ON DELETE SET NULL (company_id) preserves the pre-existing behaviour of the single-column FK it replaces -- deleting a company clears the person''s company_id but leaves the NOT NULL brand_id untouched.';

CREATE OR REPLACE FUNCTION public.request_research(_person_ids uuid[])
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  pid uuid; results jsonb := '[]';
  v_brand uuid; v_company uuid; v_company_brand uuid; v_domain text; v_researched timestamptz;
  v_cap int; v_used int; v_job uuid; v_uid uuid := auth.uid();
BEGIN
  IF v_uid IS NULL THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = 'insufficient_privilege';
  END IF;

  FOREACH pid IN ARRAY coalesce(_person_ids, ARRAY[]::uuid[]) LOOP
    v_brand := NULL; v_company := NULL; v_company_brand := NULL; v_domain := NULL; v_job := NULL; v_researched := NULL;

    SELECT p.brand_id, p.company_id, c.brand_id, c.domain, c.researched_at
      INTO v_brand, v_company, v_company_brand, v_domain, v_researched
      FROM people p LEFT JOIN companies c ON c.id = p.company_id
     WHERE p.id = pid AND p.deleted_at IS NULL;

    -- "not yours" covers three cases that must be indistinguishable: the person
    -- does not exist, the person exists but the caller doesn't own its brand, or
    -- its company belongs to a DIFFERENT brand than the person does (the
    -- composite FK above should make that last case impossible going forward,
    -- but this is the defense-in-depth check for anything that predates it).
    -- None of these may leak a different verdict, or the verdict becomes an
    -- oracle for facts about rows the caller cannot read.
    IF v_brand IS NULL OR NOT owns_brand(v_brand)
       OR (v_company IS NOT NULL AND v_company_brand IS DISTINCT FROM v_brand) THEN
      results := results || jsonb_build_object('person_id', pid, 'verdict', 'not_yours',
        'job_id', NULL, 'detail', 'no such lead, or not yours');
      CONTINUE;
    END IF;

    IF v_company IS NULL THEN
      results := results || jsonb_build_object('person_id', pid, 'verdict', 'skipped',
        'job_id', NULL, 'detail', 'lead has no company');
      CONTINUE;
    END IF;

    IF nullif(trim(v_domain), '') IS NULL THEN
      results := results || jsonb_build_object('person_id', pid, 'verdict', 'skipped',
        'job_id', NULL, 'detail', 'company has no domain to research');
      CONTINUE;
    END IF;

    IF v_researched IS NOT NULL AND v_researched > now() - interval '30 days' THEN
      results := results || jsonb_build_object('person_id', pid, 'verdict', 'skipped',
        'job_id', NULL, 'detail', 'researched within the last 30 days');
      CONTINUE;
    END IF;

    -- The short cooldown, and it exists because of the asymmetry the 30-day
    -- lock leaves behind. complete_research_job stamps researched_at only when
    -- a run actually scored somebody (see its note): a run whose `fit` came back
    -- empty -- the agent read the company and honestly found nobody worth
    -- scoring -- deliberately does NOT lock the company for a month, because the
    -- write-up is worth keeping but is not worth blocking a retry over. Without
    -- something here, though, that company can be re-requested every minute
    -- forever, each request a paid agent run that will find the same nobody.
    -- Fixed in review (round 2).
    --
    -- Keyed on a COMPLETED job rather than on researched_at precisely because
    -- researched_at is the field the empty run declines to set. An ordinary
    -- successful run is caught by the 30-day check above long before this one,
    -- so in practice this only ever bites the empty-fit case. The daily cap
    -- remains the real spend control; this is the per-company one.
    IF EXISTS (SELECT 1 FROM research_jobs rj
                WHERE rj.company_id = v_company AND rj.status = 'done'
                  AND rj.finished_at > now() - interval '24 hours') THEN
      results := results || jsonb_build_object('person_id', pid, 'verdict', 'skipped',
        'job_id', NULL, 'detail', 'a research run finished for this company within the last 24 hours and scored nobody; it can be requested again after that');
      CONTINUE;
    END IF;

    -- SUPERSEDED IN PART by 0014_research_cap_bound.sql, which is what makes
    -- this check worth anything. As written here the cap is read straight off
    -- brands.research_daily_cap, and that column shipped with NO constraint:
    -- `brands_upd` (0009) lets an owner update their own brand row, and the
    -- Settings form validated the field in the browser only -- so one PostgREST
    -- PATCH raised a brand's own ceiling to anything it liked, and the worker
    -- converts that directly into paid agent runs on the PROJECT owner's
    -- credits. Reproduced live at 100000 and at -5. 0014 adds the server-side
    -- CHECK (1..100). Read both; the check below is only a spend control
    -- because of it.
    SELECT research_daily_cap INTO v_cap FROM brands WHERE id = v_brand;
    SELECT count(*) INTO v_used FROM research_jobs
     WHERE brand_id = v_brand AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC');
    IF v_used >= v_cap THEN
      results := results || jsonb_build_object('person_id', pid, 'verdict', 'capped',
        'job_id', NULL, 'detail', format('daily cap of %s reached (%s used today)', v_cap, v_used));
      CONTINUE;
    END IF;

    -- The partial unique index is the real guard against double-spend; this is
    -- just how we report it.
    INSERT INTO research_jobs (brand_id, company_id, requested_by, created_by_source, created_by_name)
    VALUES (v_brand, v_company, v_uid, 'API', 'request_research')
    ON CONFLICT DO NOTHING
    RETURNING id INTO v_job;

    IF v_job IS NULL THEN
      SELECT id INTO v_job FROM research_jobs
       WHERE company_id = v_company AND status IN ('queued','running') LIMIT 1;
      results := results || jsonb_build_object('person_id', pid, 'verdict', 'already_queued',
        'job_id', v_job, 'detail', 'a job for this company is already in flight');
    ELSE
      results := results || jsonb_build_object('person_id', pid, 'verdict', 'queued',
        'job_id', v_job, 'detail', NULL);
    END IF;
  END LOOP;

  RETURN jsonb_build_object('results', results);
END $$;

REVOKE ALL ON FUNCTION public.request_research(uuid[]) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.request_research(uuid[]) TO authenticated;

-- ---------------------------------------------------------------------------
-- PART 2: the worker side. None of these are granted to `authenticated`: they
-- are called by the scheduled workflow with the service role key. A user token
-- must never be able to mark its own job done with a payload of its choosing.
-- ---------------------------------------------------------------------------

-- SUPERSEDED by 0013_inline_worker.sql -- READ THAT FILE BEFORE COPYING THIS
-- ONE. The body below puts the LIMIT inside an IN-subquery, which bounds the
-- subquery and not the UPDATE: under a Nested Loop Semi Join with no
-- Materialize above it, the subquery is re-executed per outer row and the
-- UPDATE claims the WHOLE QUEUE. Observed live -- one tick claimed all four
-- queued jobs and stranded three in `running` until the 15-minute sweep. 0013
-- replaces it with a MATERIALIZED CTE, which is evaluated exactly once. This
-- file is kept as applied, with only the ordering key corrected (see below,
-- and note that a fresh install applies this file first); apply both, in order.
CREATE OR REPLACE FUNCTION public.claim_research_jobs(_limit int)
RETURNS SETOF research_jobs LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  -- SKIP LOCKED is what makes overlapping ticks safe: two workers cannot claim
  -- the same row, and neither blocks on the other.
  -- (created_at, id), not created_at alone: request_research stamps a whole
  -- batch with one created_at, so created_at on its own leaves the order of a
  -- batch undefined. requeue_stalled_research_jobs' head-of-queue test uses the
  -- same key, and the two have to agree on which job is next or the backstop
  -- would judge "head" by an order the claim does not follow. 0013 replaces
  -- this body (the IN-subquery LIMIT is a plan-dependent bug); the ordering is
  -- corrected here too because a fresh install applies this file first.
  UPDATE research_jobs SET status = 'running', started_at = now(), attempts = attempts + 1
   WHERE id IN (SELECT id FROM research_jobs WHERE status = 'queued'
                 ORDER BY created_at, id LIMIT greatest(_limit, 0) FOR UPDATE SKIP LOCKED)
  RETURNING *;
$$;
REVOKE ALL ON FUNCTION public.claim_research_jobs(int) FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.complete_research_job(_job_id uuid, _payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_brand uuid; v_company uuid; v_status text; f jsonb; n int := 0;
  v_person uuid; v_score int; v_injection boolean; v_injection_raw jsonb;
  v_fit_entries int := 0;
BEGIN
  -- FOR UPDATE holds the row lock for the rest of this call, so a concurrent
  -- complete/fail call on the SAME job (see the status guard below) can't race
  -- past the status check before this one commits its own status change.
  SELECT brand_id, company_id, status INTO v_brand, v_company, v_status
    FROM research_jobs WHERE id = _job_id FOR UPDATE;
  IF v_brand IS NULL THEN RAISE EXCEPTION 'no such research job'; END IF;

  -- Fixed in review: this function had no status precondition, so completing an
  -- already-`done` job -- or one that was swept back to `queued` and re-claimed
  -- by someone else while this call was still in flight -- would write a SECOND
  -- `researched` event per person and overwrite companies.research with
  -- whatever this call happened to be holding. Not hypothetical:
  -- requeue_stalled_research_jobs() exists precisely because a worker CAN run
  -- past its 15-minute window, at which point the job is swept, re-claimed, and
  -- re-completed by someone else -- and then the original, slow worker finally
  -- returns and calls this. A no-op is the correct response, not an exception:
  -- "someone else already finished this" is the outcome the caller wanted, not
  -- a failure worth retrying.
  IF v_status IS DISTINCT FROM 'running' THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'job is not running', 'status', v_status, 'people_scored', 0);
  END IF;

  -- Validate before writing anything. The payload came from an agent that just
  -- read an attacker-controlled web page, so it is untrusted input: a malformed
  -- or injected response must fail the job, not half-write a record.
  --
  -- Fixed in review: `jsonb_typeof(_payload->'fit') <> 'array'` is NULL (not
  -- TRUE) when the 'fit' key is absent, because `->` returns SQL NULL for a
  -- missing key and `jsonb_typeof(NULL)` is itself NULL -- so `NULL <> 'array'`
  -- is NULL, the OR chain evaluates to NULL, and `IF NULL THEN` never fires.
  -- A payload with no fit array at all would have sailed through un-rejected.
  -- `IS DISTINCT FROM` is NULL-safe and never itself returns NULL.
  --
  -- Fixed in review (round 2): `summary` was validated with
  -- `nullif(trim(coalesce(_payload->>'summary','')),'')`, and `->>` on a JSON
  -- OBJECT returns that object's text -- which is not empty. So
  -- `{"summary":{"toString":"x"},"fit":[]}` passed validation and was stored
  -- verbatim, and the panel's own defence could not render it: JavaScript's
  -- `String(x)` runs ToPrimitive, and an object carrying a non-function
  -- `toString` key throws `TypeError: Cannot convert object to primitive
  -- value`. The client now try/catches that (ResearchPanel.asText), but the type
  -- belongs here: `companies.research` is a text column and every reader treats
  -- summary as a string, so a non-string one is a malformed payload, which is
  -- the thing this block exists to refuse.
  IF jsonb_typeof(_payload) IS DISTINCT FROM 'object'
     OR jsonb_typeof(_payload->'summary') IS DISTINCT FROM 'string'
     OR nullif(trim(coalesce(_payload->>'summary','')), '') IS NULL
     OR jsonb_typeof(_payload->'fit') IS DISTINCT FROM 'array'
    THEN RAISE EXCEPTION 'research payload is malformed: need an object with a non-empty string summary and a fit array (summary was %)', coalesce(jsonb_typeof(_payload->'summary'), 'absent');
  END IF;
  -- Fixed in review: `tech_stack` was the ONLY optional array checked, and it is
  -- the one the client already guarded. `hooks` and `sources` were stored
  -- verbatim and rendered straight -- so `{"hooks":"none found"}`, ordinary model
  -- output and steerable by the prompt injection this feature anticipates, threw
  -- `hooks.map is not a function` during render and (with no error boundary in
  -- the app at the time) blanked the entire SPA. The client is hardened too, but
  -- the write side is where a malformed payload should die: it is one row that
  -- every later reader has to cope with otherwise.
  IF _payload ? 'tech_stack' AND jsonb_typeof(_payload->'tech_stack') IS DISTINCT FROM 'array'
    THEN RAISE EXCEPTION 'research payload tech_stack must be an array'; END IF;
  IF _payload ? 'hooks' AND jsonb_typeof(_payload->'hooks') IS DISTINCT FROM 'array'
    THEN RAISE EXCEPTION 'research payload hooks must be an array, got %', jsonb_typeof(_payload->'hooks'); END IF;
  IF _payload ? 'sources' AND jsonb_typeof(_payload->'sources') IS DISTINCT FROM 'array'
    THEN RAISE EXCEPTION 'research payload sources must be an array, got %', jsonb_typeof(_payload->'sources'); END IF;

  -- injection_observed is a cosmetic flag, not something worth failing a good
  -- report over, so an unparseable value is coerced rather than raised on.
  -- Fixed twice in review. First: a bare `::boolean` cast raised on any
  -- non-boolean-shaped string (e.g. "maybe") and aborted the whole call. Then
  -- the coercion fell back to FALSE -- which is the wrong direction. The panel
  -- gates its "a page this agent read attempted to instruct it" banner on this
  -- value, and the single input most likely to be unparseable is a model that
  -- DID detect an injection and described it in words ("detected on the pricing
  -- page"). Falling back to false silently withheld the warning in exactly the
  -- case it exists for. A spurious banner costs a moment of scrutiny; a missing
  -- one costs the thing the flag is for. So: an explicit false is false, absent
  -- is false, and ANYTHING ELSE the agent chose to put here is true.
  --
  -- The raw value is kept alongside the boolean, because "true" and "the model
  -- wrote a sentence we could not parse" are different facts and the second one
  -- is the one a human wants to read.
  --
  -- One carve-out, and it exists so the client and the server cannot disagree:
  -- an empty or whitespace-only string is treated as ABSENT, not as a detection.
  -- It carries no claim -- a model that spotted an injection does not report it
  -- by writing "" -- and without this the two sides diverged, since
  -- `''::boolean` raises here (so it read as true) while the panel's
  -- `v.trim() !== ''` read it as false. ResearchPanel.injectionObserved()
  -- implements the identical rule: explicit false is false, absent or empty is
  -- false, everything else is true.
  v_injection_raw := _payload->'injection_observed';
  BEGIN
    v_injection := CASE
      WHEN v_injection_raw IS NULL OR jsonb_typeof(v_injection_raw) = 'null' THEN false
      WHEN jsonb_typeof(v_injection_raw) = 'string'
           AND nullif(trim(_payload->>'injection_observed'), '') IS NULL THEN false
      ELSE coalesce((_payload->>'injection_observed')::boolean, true)
    END;
  EXCEPTION WHEN invalid_text_representation THEN
    v_injection := true;
  END;

  SELECT count(*) INTO v_fit_entries FROM jsonb_array_elements(_payload->'fit');

  FOR f IN SELECT * FROM jsonb_array_elements(_payload->'fit') LOOP
    -- Fixed in review: a bare `::uuid` cast raised on any non-uuid string and
    -- aborted the ENTIRE call, losing every other person's score along with
    -- it. person_id can't be substituted for -- there's no fallback identifier
    -- -- so a malformed one is simply skipped, the same as a missing one.
    BEGIN
      v_person := nullif(f->>'person_id','')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
      v_person := NULL;
    END;
    CONTINUE WHEN v_person IS NULL;

    -- Fixed in review: `(f->>'score')::int` raised on a fractional score
    -- (live example: "82.5" -> invalid input syntax for type integer) and
    -- aborted the whole call the same way. Unlike person_id, a score IS
    -- substitutable -- fall back to 0, the same default already used for a
    -- missing score, rather than dropping a person who otherwise has a valid
    -- id and rationale. Cast through numeric first so a fractional value
    -- rounds instead of erroring; round() is "round half away from zero", so
    -- 82.5 -> 83. Then clamp: people.fit_score has a 0-100 CHECK, and an
    -- out-of-range number from the model should not throw away a good report
    -- either.
    BEGIN
      v_score := least(100, greatest(0, round(coalesce((f->>'score')::numeric, 0))))::int;
    EXCEPTION WHEN invalid_text_representation THEN
      v_score := 0;
    END;

    UPDATE people SET
      fit_score = v_score,
      stage = CASE WHEN stage IN ('sourced','enriched') THEN 'researched' ELSE stage END
    WHERE id = v_person AND brand_id = v_brand;   -- never cross a brand boundary

    IF FOUND THEN
      n := n + 1;
      INSERT INTO events (brand_id, person_id, company_id, event_type, actor_source, actor_name, properties)
      VALUES (v_brand, v_person, v_company, 'researched', 'AGENT', 'Researcher',
              jsonb_build_object('score', v_score, 'rationale', f->>'rationale',
                                 'injection_observed', v_injection)
              -- Only when it was not already a clean boolean, so the ordinary
              -- event stays the shape every existing reader expects.
              || CASE WHEN v_injection_raw IS NOT NULL
                       AND jsonb_typeof(v_injection_raw) NOT IN ('boolean','null')
                      THEN jsonb_build_object('injection_observed_raw', v_injection_raw)
                      ELSE '{}'::jsonb END);
    END IF;
  END LOOP;

  -- Fixed in review: A RUN THAT SCORED NOBODY USED TO REPORT SUCCESS AND THEN
  -- LOCK THE COMPANY FOR THIRTY DAYS.
  --
  -- Hallucinated person_ids are dropped one at a time above (correctly -- one bad
  -- uuid should not throw away everyone else's score), `n` counts the survivors,
  -- and this used to set status='done' unconditionally with researched_at already
  -- stamped. The user saw a green "Research complete", a populated Research tab,
  -- no score change anywhere, and an empty timeline -- and from then on
  -- request_research answered `skipped / researched within the last 30 days`. A
  -- paid agent run produced nothing and the system refused to retry it.
  --
  -- So: fit entries but nobody matched means the agent invented its ids, which is
  -- a failed run. Raising hands it to fail_research_job in the worker, which
  -- retries it (three strikes) instead of burying it. Raising also rolls back the
  -- people/events writes and the companies UPDATE below never happens, so no
  -- half-written record survives.
  IF v_fit_entries > 0 AND n = 0 THEN
    RAISE EXCEPTION 'research payload scored none of its % fit entries: not one person_id matched a person in this brand (the agent invented them)', v_fit_entries;
  END IF;

  -- researched_at is the 30-day freshness lock, so it is stamped only when the
  -- run actually scored someone. An empty `fit` is not a failure -- the company
  -- write-up is still worth keeping -- but it is not worth blocking a retry for
  -- a month either. That leaves an asymmetry: with researched_at unset the
  -- company is re-requestable immediately. request_research therefore applies a
  -- 24-hour cooldown keyed on the completed job itself (see its note), so an
  -- empty run costs a day rather than a month or nothing at all.
  UPDATE companies SET
    research = _payload->>'summary',
    research_data = _payload,
    tech_stack = coalesce(_payload->'tech_stack', tech_stack),
    researched_at = CASE WHEN n > 0 THEN now() ELSE researched_at END
  WHERE id = v_company;

  UPDATE research_jobs SET status='done', finished_at=now(), error=NULL WHERE id=_job_id;
  RETURN jsonb_build_object('ok', true, 'people_scored', n,
    'fit_entries', v_fit_entries, 'people_dropped', v_fit_entries - n,
    'researched_at_stamped', n > 0);
END $$;
REVOKE ALL ON FUNCTION public.complete_research_job(uuid, jsonb) FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.fail_research_job(_job_id uuid, _error text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_attempts int; v_current text; v_status text;
BEGIN
  SELECT attempts, status INTO v_attempts, v_current FROM research_jobs WHERE id = _job_id FOR UPDATE;
  IF v_attempts IS NULL THEN RAISE EXCEPTION 'no such research job'; END IF;

  -- Fixed in review: same missing status precondition as complete_research_job,
  -- and for the same reason -- without it, a stale failure report from a slow
  -- worker could flip an already-`done` (or already-requeued-and-reclaimed)
  -- job's status backwards, re-queuing finished work.
  IF v_current IS DISTINCT FROM 'running' THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'job is not running', 'status', v_current);
  END IF;

  -- Three strikes. Retrying forever would spend credits on a site that is never
  -- going to load.
  v_status := CASE WHEN v_attempts >= 3 THEN 'failed' ELSE 'queued' END;
  UPDATE research_jobs SET status = v_status, error = _error,
         finished_at = CASE WHEN v_status = 'failed' THEN now() ELSE NULL END
   WHERE id = _job_id;
  RETURN jsonb_build_object('ok', true, 'status', v_status, 'attempts', v_attempts);
END $$;
REVOKE ALL ON FUNCTION public.fail_research_job(uuid, text) FROM PUBLIC, anon, authenticated;

-- TWO COUNTS OUT, NOT ONE, AND THE SECOND ONE IS LOAD-BEARING. `abandoned` is
-- what lets run_research_tick() commit this sweep before it runs anything --
-- see the transaction note on the backstop below, and 0013's call site. The
-- DROP is what makes the change of return type possible at all: CREATE OR
-- REPLACE cannot turn `RETURNS int` into a record, and this migration is
-- re-runnable, so it has to cope with the int version already being there.
-- Nothing outside this repo calls it -- it is revoked from every client role.
DROP FUNCTION IF EXISTS public.requeue_stalled_research_jobs();
CREATE FUNCTION public.requeue_stalled_research_jobs(OUT requeued int, OUT abandoned int)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  -- Old enough to be a candidate for abandonment, and also the window over
  -- which "has anything finished?" is judged. See the note below.
  MAX_AGE constant interval := interval '1 hour';
  n int;
BEGIN
  -- ---------------------------------------------------------------------
  -- THE AGE BACKSTOP, and it is the only thing that bounds a crash loop.
  --
  -- The three-strike ceiling in fail_research_job cannot bound a worker that
  -- DIES rather than fails: `attempts = attempts + 1` is written by
  -- claim_research_jobs inside the same transaction as the run, so any
  -- non-catchable termination -- backend killed, OOM, server restart, the pooler
  -- dropping the connection -- rolls back the claim AND the increment together.
  -- The job comes back `queued` with `attempts` unchanged (0, forever), the next
  -- tick claims it a minute later, and it dies again. Deterministic crashers
  -- retry every minute for as long as the project exists, and every one of those
  -- retries is a paid agent run. run_research_tick()'s two exception handlers
  -- cover everything PL/pgSQL can catch; this covers what it cannot.
  --
  -- created_at is the age anchor because NOTHING ELSE SURVIVES. attempts and
  -- started_at are both written inside the doomed transaction and both roll back
  -- with it, and updated_at is written by a trigger on the same UPDATE, so it
  -- rolls back too. A crash-looper is therefore indistinguishable from a
  -- freshly-queued job by every column except how long it has existed.
  --
  -- AGE ALONE IS NOT ENOUGH, and shipping it alone was a bug. Fixed in review
  -- (round 2): request_research inserts a whole batch in ONE transaction, so
  -- every job in it carries the SAME created_at, and the worker drains one job
  -- per minute while research_daily_cap is legal up to 100 (0014). A user who
  -- selects 100 leads therefore has ~40 jobs cross a one-hour threshold on the
  -- SAME tick, and an age-only backstop failed all forty at once -- inside the
  -- documented limits, with an error blaming a crash loop that had not happened,
  -- and with the daily cap already spent (the cap counts rows created today
  -- whatever their outcome). The same thing happened to an entire queue after
  -- any hour the worker was not running: cron deactivated for maintenance,
  -- pg_cron down, a restore.
  --
  -- So a job is abandoned only when all three hold:
  --
  --   1. It is older than MAX_AGE.
  --   2. It is at the HEAD of the queue -- no queued job sorts before it under
  --      claim_research_jobs' own (created_at, id) order. A crash-looper always
  --      is, because the claim takes the head and the crash puts it straight
  --      back; a job merely waiting behind a backlog never is.
  --   3. NOTHING HAS FINISHED in the last MAX_AGE. A worker that is draining a
  --      backlog stamps finished_at once a minute, which is durable, committed
  --      evidence that the queue is moving and that this job is waiting rather
  --      than killing anything. A worker crash-looping on the head job finishes
  --      nothing, because the head is all it ever reaches.
  --
  -- Condition 3 is what keeps 2 from becoming a slow version of the same bug:
  -- head-of-queue on its own would abandon the head of a healthy backlog every
  -- tick, losing half the batch instead of all of it.
  --
  -- RESIDUAL, and named rather than hidden: the first tick after the worker has
  -- been stopped for more than an hour can still abandon ONE job -- the head --
  -- because a stopped worker and a worker being killed by the head job leave
  -- exactly the same evidence behind (nothing). That tick then runs the next
  -- job, whose finished_at re-establishes condition 3, so the cost of a pause is
  -- one job rather than the queue. The error text says both things could be
  -- true instead of asserting the crash loop.
  --
  -- ONE HOUR. The designed worst case for an honest job is 45 minutes: three
  -- attempts, each of which can sit up to the 15-minute stall window before the
  -- sweep below returns it to the queue. An hour clears that with headroom.
  --
  -- WHAT THIS DOES NOT BOUND. An earlier draft of this comment claimed the
  -- backstop caps a crash-looper at roughly 60 paid runs. That is only true when
  -- the crash-looper is the ONLY thing in the queue. Condition 3 asks whether
  -- anything finished in the window, and a healthy job finishing alongside the
  -- looper re-arms it -- so a looper sharing a queue with steady traffic is never
  -- abandoned by age. That is the deliberate cost of distinguishing "a job that
  -- kills the worker" from "a worker that is simply stopped" using only evidence
  -- that survives a backend kill: an in-transaction heartbeat rolls back with the
  -- tick, so the sweep has nothing else durable to read. The three-strike counter
  -- in fail_research_job still bounds every failure the worker can actually
  -- catch; this backstop only covers terminations it cannot. Closing the gap
  -- properly means reading cron.job_run_details (guarded on to_regnamespace('cron'),
  -- as 0013 does elsewhere), which a CRM-only install would not have.
  --
  -- THE ABANDONMENT HAS TO COMMIT ON ITS OWN, and that is why this function
  -- reports `abandoned` instead of keeping the number to itself. Found in
  -- review (round 3), and it is a hole the round-2 fix opened rather than one
  -- that was always there.
  --
  -- One tick is one transaction: run_research_tick() calls this sweep and then
  -- claims and runs a job in the SAME transaction. The only failure this
  -- backstop exists for is an UNCATCHABLE termination of the worker backend --
  -- so if the job claimed microseconds after this UPDATE kills the backend, the
  -- abandonment written here rolls back with it. Nothing is ever recorded, and
  -- the queue is retried every minute forever: precisely the unbounded paid-run
  -- loop the backstop was written to stop.
  --
  -- The age-only version was accidentally immune, which is why this only shows
  -- up now. It failed EVERY old job, so the claim that followed found an empty
  -- queue and the tick always committed. Abandoning only the head deliberately
  -- leaves work behind to claim, and that is what makes the shared transaction
  -- reachable.
  --
  -- The fix is at the call site, not here: 0013's tick returns as soon as this
  -- reports a non-zero `abandoned`, so the abandonment commits by itself and
  -- the next tick, a minute later, does the running. The alternative -- a second
  -- cron.schedule entry for the sweep -- buys back that one tick of throughput
  -- at the price of a second scheduled job to reason about, to pause during the
  -- tests, and to tear down. A tick is a minute, abandonment is capped at one
  -- per hour project-wide (see the self-disarm note above), and the tick being
  -- skipped is one that was about to run a job in a transaction we have just
  -- used to record something that must survive. It is the cheap side of the
  -- trade.
  --
  -- FOR UPDATE SKIP LOCKED is load-bearing, not decoration. One tick is one
  -- transaction, so a job another worker is CURRENTLY running still reads as
  -- `queued` to this session -- its claim is uncommitted -- and it is old enough
  -- to match if it waited in a backlog. Without SKIP LOCKED this UPDATE would
  -- block on that worker's row lock for the length of its agent call, inside
  -- every tick. Skipping locked rows means an in-flight job is never touched and
  -- nothing ever waits. The head-of-queue probe deliberately does NOT skip
  -- locked rows: an in-flight job still reads as queued here, so its successor
  -- correctly does not count as the head.
  -- ---------------------------------------------------------------------
  -- The CTE is `to_abandon`, not `abandoned`: `abandoned` is now an OUT
  -- parameter of this function, and a plpgsql variable sharing a name with a
  -- range table entry is an ambiguity error, not a silent shadow.
  WITH to_abandon AS MATERIALIZED (
    SELECT j.id, j.attempts, j.created_at
      FROM research_jobs j
     WHERE j.status IN ('queued','running')
       AND j.created_at < now() - MAX_AGE
       AND NOT EXISTS (SELECT 1 FROM research_jobs o
                        WHERE o.status = 'queued'
                          AND (o.created_at, o.id) < (j.created_at, j.id))
       AND NOT EXISTS (SELECT 1 FROM research_jobs p
                        WHERE p.finished_at > now() - MAX_AGE)
     ORDER BY j.created_at, j.id
     FOR UPDATE SKIP LOCKED
  )
  UPDATE research_jobs j
     SET status = 'failed',
         finished_at = now(),
         -- Written so an operator can tell this apart from an ordinary failure
         -- at a glance: an ordinary one carries the agent's own error text, this
         -- one names the age, the attempts and the two states that produce it.
         -- It no longer asserts a crash loop, because condition 3 cannot tell a
         -- crash loop from a worker that was switched off.
         error = format(
           'abandoned by the age backstop: still unfinished %s after it was queued, at the head of the queue, and no research job in this project has finished in the last hour. Two situations look like this from the outside and this job is one of them: (a) it kills the worker mid-tick, which rolls back its own claim AND the attempts counter -- %s recorded attempt(s) here -- so it never reaches the three-strike ceiling and would otherwise be retried every minute forever; or (b) the worker has not been running at all. Look at cron.job_run_details and the server log around %s. Nothing was written for this company and researched_at was not stamped, so it can be requested again.',
           justify_interval(date_trunc('second', now() - a.created_at)), a.attempts, a.created_at)
    FROM to_abandon a
   WHERE j.id = a.id;
  GET DIAGNOSTICS abandoned = ROW_COUNT;

  -- A worker can die mid-run. Without this the job sits in `running` forever and
  -- the partial unique index blocks the company from ever being researched again.
  -- Runs after the backstop, so a job old enough to be abandoned is failed rather
  -- than handed back to the queue one more time.
  UPDATE research_jobs SET status='queued', started_at=NULL
   WHERE status='running' AND started_at < now() - interval '15 minutes';
  GET DIAGNOSTICS n = ROW_COUNT;

  -- Two separate counts, deliberately not summed: the worker reports `requeued`
  -- in its tick result, and an abandonment is not a requeue. `abandoned` is not
  -- a report -- it is the signal that this transaction now holds something that
  -- must commit before anything else runs in it. The abandonment stays
  -- observable where it belongs as well: on the job row, in `error`, readable by
  -- the brand's owner.
  --
  -- Only the abandonment forces the early return. A requeue that rolls back is
  -- harmless: the job stays `running`, the next sweep finds it again fifteen
  -- minutes later, and no agent run is spent in between.
  requeued := n;
  RETURN;
END $$;
REVOKE ALL ON FUNCTION public.requeue_stalled_research_jobs() FROM PUBLIC, anon, authenticated;

COMMIT;
