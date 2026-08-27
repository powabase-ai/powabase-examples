-- ============================================================================
-- CROSS-TENANT WRITE IN import_people, AND AN OVER-BROAD ERROR HANDLER.
--
-- 0009 scoped the RPC's reads and writes to `_brand_id`, but the closing
--
--     UPDATE import_batches SET ... WHERE id = _import_id;
--
-- had no brand predicate, and `_import_id` was never validated against
-- `_brand_id`. So a signed-up user could pass their OWN brand (clearing the
-- guard at the top) together with SOMEONE ELSE'S batch id, and overwrite that
-- row: status flipped to 'completed', counters zeroed, `errors` emptied.
-- Confirmed against a live project before writing this migration -- a batch
-- that genuinely read {status: failed, errors: [...]} came back
-- {status: completed, errors: []} after another tenant's call.
--
-- Nothing is read back, so this leaked nothing. It was an integrity write, in
-- the one migration whose entire purpose was to remove them. A batch id is an
-- identifier, not a capability; it authorizes nothing on its own.
--
-- The fix is the missing predicate. A batch belonging to another brand now
-- matches no row, which is the same outcome an unknown id already had -- and
-- deliberately not an error, so the RPC still cannot be used to test whether
-- some other tenant's batch id exists.
--
-- While recreating the function, the per-row `WHEN OTHERS` is narrowed too. It
-- recorded EVERY failure as if it were bad data in that row, so a
-- statement_timeout or a deadlock (both reachable now that concurrent imports
-- contend on companies_brand_lower_name_uq) would be reported as a CSV problem
-- and the batch would still end 'completed'. Only SQLSTATE classes 22 and 23 --
-- data exceptions and integrity violations, i.e. what a bad row actually
-- produces -- are recorded per row now. Anything else aborts the import.
--
-- Third: a row carrying no email, linkedin_url or name is rejected instead of
-- inserted. Papa's non-greedy skipEmptyLines kept lines that were only commas,
-- those mapped to {}, and the ELSE branch wrote an all-NULL lead and counted it
-- as inserted. The client now skips them too, but this function is callable
-- directly, so the guard belongs on this side of the wire.
-- ============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.import_people(_brand_id uuid, _import_id uuid, _rows jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  row_j jsonb; i int := 0;
  n_ins int := 0; n_res int := 0; n_skip int := 0; errs jsonb := '[]';
  v_email text; v_domain text; v_name text; v_company uuid; v_person uuid; v_deleted timestamptz;
  ctx jsonb; v_state text;
BEGIN
  IF auth.uid() IS NOT NULL AND NOT public.owns_brand(_brand_id) THEN
    RAISE EXCEPTION 'not your brand'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  FOR row_j IN SELECT * FROM jsonb_array_elements(_rows) LOOP
    i := i + 1;
    IF jsonb_typeof(row_j) <> 'object' THEN
      errs := errs || jsonb_build_object('row', i, 'message', 'row is not a JSON object');
      CONTINUE;
    END IF;

    -- A row with nothing to identify a person by is not a person. A blank CSV
    -- line (",,,") maps to {} and used to fall through to the INSERT below,
    -- creating an all-NULL lead that counted as a success -- a board full of
    -- "Unknown" cards with no email, so they can never be deduped or matched.
    -- The client drops these too, but the guard belongs here: this function is
    -- callable directly, and the client is not the only caller.
    IF coalesce(nullif(trim(row_j->>'email'), ''),
                nullif(trim(row_j->>'linkedin_url'), ''),
                nullif(trim(row_j->>'first_name'), ''),
                nullif(trim(row_j->>'last_name'), '')) IS NULL THEN
      errs := errs || jsonb_build_object('row', i,
        'message', 'row has no email, linkedin_url or name, so there is nothing to identify a person by');
      CONTINUE;
    END IF;
    BEGIN
      v_email := nullif(trim(row_j->>'email'), '');
      v_domain := nullif(lower(trim(row_j->>'company_domain')), '');
      v_name := nullif(trim(row_j->>'company_name'), '');
      ctx := jsonb_build_object('import_id', _import_id, 'row', i);

      -- company: upsert-restore by (brand, domain) when a domain is present,
      -- else by (brand, lower(name)) among the brand's domain-less companies.
      v_company := NULL; v_deleted := NULL;
      IF v_domain IS NOT NULL THEN
        SELECT id, deleted_at INTO v_company, v_deleted FROM companies
          WHERE brand_id = _brand_id AND domain = v_domain;
        IF v_company IS NULL THEN
          INSERT INTO companies (brand_id, name, domain, created_by_source, created_by_name, created_by_context)
          VALUES (_brand_id, v_name, v_domain, 'IMPORT', 'CSV import', ctx)
          RETURNING id INTO v_company;
        ELSIF v_deleted IS NOT NULL THEN
          UPDATE companies SET deleted_at = NULL WHERE id = v_company;
        END IF;
      ELSIF v_name IS NOT NULL THEN
        SELECT id, deleted_at INTO v_company, v_deleted FROM companies
          WHERE brand_id = _brand_id AND domain IS NULL AND lower(name) = lower(v_name)
          ORDER BY created_at LIMIT 1;
        IF v_company IS NULL THEN
          INSERT INTO companies (brand_id, name, domain, created_by_source, created_by_name, created_by_context)
          VALUES (_brand_id, v_name, NULL, 'IMPORT', 'CSV import', ctx)
          RETURNING id INTO v_company;
        ELSIF v_deleted IS NOT NULL THEN
          UPDATE companies SET deleted_at = NULL WHERE id = v_company;
        END IF;
      END IF;

      -- person: dedupe by (brand, lower(email)); restore if soft-deleted; skip live dups
      v_person := NULL; v_deleted := NULL;
      IF v_email IS NOT NULL THEN
        SELECT id, deleted_at INTO v_person, v_deleted FROM people
          WHERE brand_id = _brand_id AND lower(email) = lower(v_email);
      END IF;
      IF v_person IS NOT NULL AND v_deleted IS NULL THEN
        n_skip := n_skip + 1;
      ELSIF v_person IS NOT NULL THEN
        UPDATE people SET deleted_at = NULL, company_id = coalesce(v_company, company_id) WHERE id = v_person;
        n_res := n_res + 1;
      ELSE
        INSERT INTO people (brand_id, company_id, first_name, last_name, title, email, linkedin_url,
                            created_by_source, created_by_name, created_by_context)
        VALUES (_brand_id, v_company,
                nullif(trim(row_j->>'first_name'), ''), nullif(trim(row_j->>'last_name'), ''),
                nullif(trim(row_j->>'title'), ''), v_email,
                nullif(trim(row_j->>'linkedin_url'), ''),
                'IMPORT', 'CSV import', ctx)
        RETURNING id INTO v_person;
        n_ins := n_ins + 1;
        INSERT INTO events (brand_id, person_id, company_id, event_type, actor_source, actor_name, properties)
        VALUES (_brand_id, v_person, v_company, 'import', 'IMPORT', 'CSV import', ctx);
      END IF;
    EXCEPTION WHEN OTHERS THEN
      GET STACKED DIAGNOSTICS v_state = RETURNED_SQLSTATE;
      -- Class 22 (data exception) and class 23 (integrity violation) are what
      -- bad CSV data looks like: an unparseable value, a duplicate key. Those
      -- belong to the row, so record them and keep going.
      --
      -- Everything else does NOT belong to the row. A statement_timeout, a
      -- deadlock, or an undefined_column after schema drift would otherwise be
      -- filed as if the operator's CSV were malformed, and the batch would
      -- still finish 'completed'. Re-raise those so the import fails loudly.
      IF left(v_state, 2) IN ('22', '23') THEN
        errs := errs || jsonb_build_object('row', i, 'message', SQLERRM, 'sqlstate', v_state);
      ELSE
        RAISE;
      END IF;
    END;
  END LOOP;

  UPDATE import_batches SET
    status = CASE
      WHEN jsonb_array_length(errs) > 0 AND n_ins = 0 AND n_res = 0 AND n_skip = 0 THEN 'failed'
      ELSE 'completed'
    END,
    inserted_count = n_ins, restored_count = n_res, skipped_count = n_skip, errors = errs
  WHERE id = _import_id
    AND brand_id = _brand_id;
  RETURN jsonb_build_object('inserted', n_ins, 'restored', n_res, 'skipped', n_skip, 'errors', errs);
END $$;

REVOKE ALL ON FUNCTION public.import_people(uuid, uuid, jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.import_people(uuid, uuid, jsonb) TO authenticated;

COMMIT;
