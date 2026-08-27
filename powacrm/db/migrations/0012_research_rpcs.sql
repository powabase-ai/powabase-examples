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

COMMIT;
