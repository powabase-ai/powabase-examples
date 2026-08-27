-- Covers 0007_import_company_by_name.sql: a CSV with a Company column but no
-- Website column must still create and link a company, idempotently, without
-- disturbing the domain-keyed path from 0006.
DO $$
DECLARE b uuid; imp uuid; r jsonb; c1 uuid; c2 uuid; n int; sp text;
BEGIN
  INSERT INTO brands (name) VALUES ('_test_imp_name') RETURNING id INTO b;
  INSERT INTO import_batches (brand_id, filename, row_count) VALUES (b, 'no-website.csv', 2) RETURNING id INTO imp;

  -- 1. name-only company is created and linked (the 0006 bug: this was silently NULL)
  r := import_people(b, imp, '[
    {"first_name":"Cara","last_name":"Vance","email":"cara@northwind.test","company_name":"Northwind Traders"}
  ]'::jsonb);
  IF (r->>'inserted')::int <> 1 THEN RAISE EXCEPTION 'expected 1 inserted, got %', r; END IF;
  IF jsonb_array_length(r->'errors') <> 0 THEN RAISE EXCEPTION 'unexpected errors: %', r; END IF;

  SELECT company_id INTO c1 FROM people WHERE brand_id = b AND lower(email) = 'cara@northwind.test';
  IF c1 IS NULL THEN RAISE EXCEPTION 'name-only company not linked to person'; END IF;
  IF (SELECT name FROM companies WHERE id = c1) IS DISTINCT FROM 'Northwind Traders'
    THEN RAISE EXCEPTION 'company name not stored'; END IF;
  IF (SELECT domain FROM companies WHERE id = c1) IS NOT NULL
    THEN RAISE EXCEPTION 'name-only company should have a NULL domain'; END IF;
  IF (SELECT created_by_source FROM companies WHERE id = c1) IS DISTINCT FROM 'IMPORT'
    THEN RAISE EXCEPTION 'company provenance not stamped'; END IF;

  -- 2. re-importing the same CSV creates no second company (and no second person)
  r := import_people(b, imp, '[
    {"first_name":"Cara","last_name":"Vance","email":"cara@northwind.test","company_name":"Northwind Traders"}
  ]'::jsonb);
  IF (r->>'skipped')::int <> 1 THEN RAISE EXCEPTION 'expected 1 skipped dup person, got %', r; END IF;
  SELECT count(*) INTO n FROM companies WHERE brand_id = b AND lower(name) = 'northwind traders';
  IF n <> 1 THEN RAISE EXCEPTION 'expected 1 company after re-import, got %', n; END IF;

  -- 3. a second person at the same company (case-insensitive name) reuses that company
  r := import_people(b, imp, '[
    {"first_name":"Dev","email":"dev@northwind.test","company_name":"northwind traders"}
  ]'::jsonb);
  IF (r->>'inserted')::int <> 1 THEN RAISE EXCEPTION 'expected 1 inserted, got %', r; END IF;
  SELECT company_id INTO c2 FROM people WHERE brand_id = b AND lower(email) = 'dev@northwind.test';
  IF c2 IS DISTINCT FROM c1 THEN RAISE EXCEPTION 'case-variant company name created a duplicate'; END IF;

  -- 4. a soft-deleted name-only company is restored, not duplicated
  UPDATE companies SET deleted_at = now() WHERE id = c1;
  r := import_people(b, imp, '[
    {"first_name":"Eve","email":"eve@northwind.test","company_name":"Northwind Traders"}
  ]'::jsonb);
  IF (SELECT deleted_at FROM companies WHERE id = c1) IS NOT NULL
    THEN RAISE EXCEPTION 'soft-deleted company not restored'; END IF;
  SELECT count(*) INTO n FROM companies WHERE brand_id = b AND lower(name) = 'northwind traders';
  IF n <> 1 THEN RAISE EXCEPTION 'restore duplicated the company, got % rows', n; END IF;

  -- 5. rows WITH a domain behave exactly as 0006 did: keyed on the domain,
  --    normalized to lowercase, and never merged into the name-only company
  r := import_people(b, imp, '[
    {"first_name":"Fay","email":"fay@acme.test","company_name":"Northwind Traders","company_domain":"ACME.test"}
  ]'::jsonb);
  SELECT company_id INTO c2 FROM people WHERE brand_id = b AND lower(email) = 'fay@acme.test';
  IF c2 IS NULL OR c2 = c1 THEN RAISE EXCEPTION 'domain row should key on the domain, not the name'; END IF;
  IF (SELECT domain FROM companies WHERE id = c2) IS DISTINCT FROM 'acme.test'
    THEN RAISE EXCEPTION 'domain not normalized'; END IF;

  -- 6. no company name and no domain: person still imports, company stays NULL
  r := import_people(b, imp, '[{"first_name":"Gus","email":"gus@nowhere.test"}]'::jsonb);
  IF (r->>'inserted')::int <> 1 THEN RAISE EXCEPTION 'expected 1 inserted, got %', r; END IF;
  IF (SELECT company_id FROM people WHERE brand_id = b AND lower(email) = 'gus@nowhere.test') IS NOT NULL
    THEN RAISE EXCEPTION 'person with no company data should have company_id NULL'; END IF;

  -- 7. both SECURITY DEFINER functions name pg_temp explicitly (0007 / 0008)
  FOREACH sp IN ARRAY ARRAY['import_people', 'soft_delete_person'] LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
      WHERE ns.nspname = 'public' AND p.proname = sp
        AND 'search_path=public, pg_temp' = ANY (p.proconfig)
    ) THEN RAISE EXCEPTION '%: search_path is not "public, pg_temp"', sp; END IF;
  END LOOP;

  DELETE FROM events WHERE brand_id = b; DELETE FROM people WHERE brand_id = b;
  DELETE FROM companies WHERE brand_id = b; DELETE FROM import_batches WHERE brand_id = b;
  DELETE FROM brands WHERE id = b;
END $$;
SELECT 'test_0007 OK' AS result;
