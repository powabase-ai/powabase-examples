-- Supersedes the import RPC created in 0006_import_rpc.sql.
--
-- Bug being fixed: 0006 gated ALL company handling on `IF v_domain IS NOT NULL`.
-- A CSV that has a Company column but no Website column -- a very common export
-- shape -- therefore imported every person with `company_id = NULL`, created
-- zero companies, reported zero errors, and showed a green success banner. The
-- data loss was completely silent.
--
-- Fix: when the row carries no usable domain but does carry a company name,
-- dedupe on `(brand_id, lower(name))` among the brand's domain-less companies,
-- restoring a soft-deleted match instead of duplicating it -- the same
-- dedupe-then-restore rule 0006 already applied to the domain key. Rows that DO
-- carry a domain keep the exact behaviour they had before: the domain remains
-- the stronger identity, and a name-only company is never merged into a
-- domain-bearing one.
--
-- Also hardens `search_path` to `public, pg_temp`. The function is SECURITY
-- DEFINER and owned by a superuser, and with `SET search_path = public` alone
-- PostgreSQL still searches the caller's `pg_temp` FIRST for relations -- the
-- CVE-2018-1058 shape, where a caller pre-creates `pg_temp.companies` and the
-- definer writes there instead. Not exploitable on this schema (neither `anon`
-- nor `authenticated` holds CREATE on `public`, and phase 1 has no
-- non-superuser able to shadow these tables in a way that matters), but naming
-- `pg_temp` explicitly at the END of the path is the pattern this repo should
-- be teaching. 0008 does the same for `soft_delete_person`.
--
-- Trust boundary (unchanged from 0006): `_brand_id` is taken from the caller
-- as-is and is NOT scoped against the caller's identity. Phase 1 is
-- single-tenant by design -- see the header of 0004_rls.sql. A multi-user phase
-- MUST scope `_brand_id` against a membership table before this function (and
-- 0004's `WITH CHECK (true)` INSERT policies) can be trusted across tenants.
--
-- apply.sh runs statements in autocommit, so the whole migration is wrapped in
-- BEGIN/COMMIT to close the window between CREATE OR REPLACE FUNCTION and the
-- REVOKE below, during which PUBLIC would otherwise hold EXECUTE.
BEGIN;

-- Makes the name-only dedupe above an actual guarantee rather than a
-- read-then-write race between two concurrent imports. Mirrors
-- companies_brand_domain_uq: full unique, spanning soft-deleted rows, so a
-- re-import restores instead of duplicating. [Twenty dedupe-then-restore]
CREATE UNIQUE INDEX IF NOT EXISTS companies_brand_lower_name_uq
  ON companies (brand_id, lower(name))
  WHERE domain IS NULL AND name IS NOT NULL;

CREATE OR REPLACE FUNCTION public.import_people(_brand_id uuid, _import_id uuid, _rows jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  row_j jsonb; i int := 0;
  n_ins int := 0; n_res int := 0; n_skip int := 0; errs jsonb := '[]';
  v_email text; v_domain text; v_name text; v_company uuid; v_person uuid; v_deleted timestamptz;
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
