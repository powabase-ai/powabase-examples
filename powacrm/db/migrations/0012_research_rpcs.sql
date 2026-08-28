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

CREATE OR REPLACE FUNCTION public.claim_research_jobs(_limit int)
RETURNS SETOF research_jobs LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  -- SKIP LOCKED is what makes overlapping ticks safe: two workers cannot claim
  -- the same row, and neither blocks on the other.
  UPDATE research_jobs SET status = 'running', started_at = now(), attempts = attempts + 1
   WHERE id IN (SELECT id FROM research_jobs WHERE status = 'queued'
                 ORDER BY created_at LIMIT greatest(_limit, 0) FOR UPDATE SKIP LOCKED)
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
  IF jsonb_typeof(_payload) IS DISTINCT FROM 'object'
     OR nullif(trim(coalesce(_payload->>'summary','')), '') IS NULL
     OR jsonb_typeof(_payload->'fit') IS DISTINCT FROM 'array'
    THEN RAISE EXCEPTION 'research payload is malformed: need an object with a non-empty summary and a fit array';
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
  v_injection_raw := _payload->'injection_observed';
  BEGIN
    v_injection := CASE
      WHEN v_injection_raw IS NULL OR jsonb_typeof(v_injection_raw) = 'null' THEN false
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
  -- a month either, and the daily cap remains the real spend control.
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

CREATE OR REPLACE FUNCTION public.requeue_stalled_research_jobs()
RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE n int;
BEGIN
  -- A worker can die mid-run. Without this the job sits in `running` forever and
  -- the partial unique index blocks the company from ever being researched again.
  UPDATE research_jobs SET status='queued', started_at=NULL
   WHERE status='running' AND started_at < now() - interval '15 minutes';
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
REVOKE ALL ON FUNCTION public.requeue_stalled_research_jobs() FROM PUBLIC, anon, authenticated;

COMMIT;
