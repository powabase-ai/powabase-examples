DO $$
DECLARE b uuid; imp uuid; r jsonb; pid uuid;
BEGIN
  INSERT INTO brands (name) VALUES ('_test_imp') RETURNING id INTO b;
  INSERT INTO import_batches (brand_id, filename, row_count) VALUES (b, 't.csv', 3) RETURNING id INTO imp;

  r := import_people(b, imp, '[
    {"first_name":"Ana","last_name":"Lee","email":"ana@acme.com","title":"CEO","company_name":"Acme","company_domain":"ACME.com"},
    {"first_name":"Bob","last_name":"Ray","email":"bob@beta.io","company_name":"Beta","company_domain":"beta.io"},
    {"first_name":"Ana","last_name":"Dup","email":"ANA@acme.com"}
  ]'::jsonb);
  IF (r->>'inserted')::int <> 2 THEN RAISE EXCEPTION 'expected 2 inserted, got %', r; END IF;
  IF (r->>'skipped')::int <> 1 THEN RAISE EXCEPTION 'expected 1 skipped dup, got %', r; END IF;

  -- domain normalized to lowercase; company linked
  SELECT company_id INTO pid FROM people WHERE brand_id = b AND lower(email) = 'ana@acme.com';
  IF pid IS NULL THEN RAISE EXCEPTION 'company not linked'; END IF;
  IF (SELECT domain FROM companies WHERE id = pid) IS DISTINCT FROM 'acme.com' THEN RAISE EXCEPTION 'domain not normalized'; END IF;
  -- provenance stamped
  IF (SELECT created_by_source FROM people WHERE brand_id = b AND lower(email)='ana@acme.com') IS DISTINCT FROM 'IMPORT'
    THEN RAISE EXCEPTION 'created_by_source not IMPORT'; END IF;

  -- soft-delete then re-import restores [Twenty dedupe-then-restore]
  UPDATE people SET deleted_at = now() WHERE brand_id = b AND lower(email) = 'ana@acme.com';
  r := import_people(b, imp, '[{"first_name":"Ana","email":"ana@acme.com"}]'::jsonb);
  IF (r->>'restored')::int <> 1 THEN RAISE EXCEPTION 'expected restore, got %', r; END IF;
  IF EXISTS (SELECT 1 FROM people WHERE brand_id = b AND lower(email)='ana@acme.com' AND deleted_at IS NOT NULL)
    THEN RAISE EXCEPTION 'row not restored'; END IF;

  DELETE FROM events WHERE brand_id = b; DELETE FROM people WHERE brand_id = b;
  DELETE FROM companies WHERE brand_id = b; DELETE FROM import_batches WHERE brand_id = b;
  DELETE FROM brands WHERE id = b;
END $$;
SELECT 'test_0006 OK' AS result;
