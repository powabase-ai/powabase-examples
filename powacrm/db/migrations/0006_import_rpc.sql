-- SUPERSEDED by 0007_import_company_by_name.sql, which fixes a silent data-loss
-- bug in this version (a CSV with a Company column but no Website column
-- imported everyone with `company_id = NULL` and reported success) and hardens
-- `search_path`. This file is kept byte-identical to what was applied; apply
-- both, in order.
--
-- SECURITY DEFINER: must see soft-deleted rows (RLS SELECT policy hides them)
-- to implement dedupe-then-restore. Grant to authenticated only.
--
-- apply.sh runs statements in autocommit, so without an explicit transaction
-- there would be a brief window between CREATE OR REPLACE FUNCTION and the
-- REVOKE below where PUBLIC holds EXECUTE (same gap noted in
-- 0005_soft_delete.sql's review). Wrapping the whole migration in
-- BEGIN/COMMIT closes that window.
--
-- Trust boundary: `_brand_id` is taken from the caller as-is and is NOT
-- scoped against the caller's identity. This is acceptable only because
-- phase 1 is single-tenant (one authenticated login, full access to every
-- brand) -- and it is not a regression introduced here: 0004_rls.sql's
-- `people`/`companies` INSERT policies are already `WITH CHECK (true)`, so a
-- plain `POST /rest/v1/people` can already write any brand_id. A future
-- multi-user phase MUST scope `_brand_id` against a membership table before
-- this function (and those INSERT policies) can be trusted across tenants.
BEGIN;

CREATE OR REPLACE FUNCTION public.import_people(_brand_id uuid, _import_id uuid, _rows jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  row_j jsonb; i int := 0;
  n_ins int := 0; n_res int := 0; n_skip int := 0; errs jsonb := '[]';
  v_email text; v_domain text; v_company uuid; v_person uuid; v_deleted timestamptz;
  ctx jsonb;
BEGIN
  FOR row_j IN SELECT * FROM jsonb_array_elements(_rows) LOOP
    i := i + 1;
    IF jsonb_typeof(row_j) <> 'object' THEN
      errs := errs || jsonb_build_object('row', i, 'message', 'row is not a JSON object');
      CONTINUE;
    END IF;
    BEGIN
      v_email := nullif(trim(row_j->>'email'), '');
      v_domain := nullif(lower(trim(row_j->>'company_domain')), '');
      ctx := jsonb_build_object('import_id', _import_id, 'row', i);

      -- company: upsert-restore by (brand, domain)
      v_company := NULL;
      IF v_domain IS NOT NULL THEN
        SELECT id, deleted_at INTO v_company, v_deleted FROM companies
          WHERE brand_id = _brand_id AND domain = v_domain;
        IF v_company IS NULL THEN
          INSERT INTO companies (brand_id, name, domain, created_by_source, created_by_name, created_by_context)
          VALUES (_brand_id, nullif(trim(row_j->>'company_name'), ''), v_domain, 'IMPORT', 'CSV import', ctx)
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
      errs := errs || jsonb_build_object('row', i, 'message', SQLERRM);
    END;
  END LOOP;

  UPDATE import_batches SET
    status = CASE
      WHEN jsonb_array_length(errs) > 0 AND n_ins = 0 AND n_res = 0 AND n_skip = 0 THEN 'failed'
      ELSE 'completed'
    END,
    inserted_count = n_ins, restored_count = n_res, skipped_count = n_skip, errors = errs
  WHERE id = _import_id;
  RETURN jsonb_build_object('inserted', n_ins, 'restored', n_res, 'skipped', n_skip, 'errors', errs);
END $$;

REVOKE ALL ON FUNCTION public.import_people(uuid, uuid, jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.import_people(uuid, uuid, jsonb) TO authenticated;

COMMIT;
