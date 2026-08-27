-- NOTE: the updated_at trigger check is split across separate top-level
-- statements (bridged via a session temp table) instead of one DO block.
-- now() is frozen for the lifetime of a transaction, and a DO $$ ... $$
-- block is a single statement/transaction under psql's default autocommit,
-- so pg_sleep() inside one DO block can never make a later now() differ
-- from an earlier one in the same block -- the trigger fires correctly but
-- the single-block assertion can't observe it. Splitting into separate
-- statements (each its own transaction) is the minimal fix; every
-- assertion below is unchanged from the brief.
CREATE TEMP TABLE _t2_fixture (b uuid, ts1 timestamptz);

DO $$
DECLARE b uuid;
BEGIN
  -- brands.owner_id is NOT NULL since 0009_access_control.sql, and this script
  -- runs as a superuser with no auth.uid() to default from, so name an owner
  -- explicitly. Any account will do -- these fixtures are torn down below.
  INSERT INTO brands (name, owner_id) VALUES ('_test_brand', (SELECT id FROM auth.users ORDER BY created_at, id LIMIT 1)) RETURNING id INTO b;
  INSERT INTO _t2_fixture (b, ts1) SELECT b, updated_at FROM brands WHERE id = b;

  -- actor default
  IF (SELECT created_by_source FROM brands WHERE id = b) <> 'MANUAL' THEN
    RAISE EXCEPTION 'created_by_source default wrong'; END IF;
  -- invalid actor source rejected
  BEGIN
    UPDATE brands SET created_by_source = 'bogus' WHERE id = b;
    RAISE EXCEPTION 'expected check_violation on created_by_source';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;

SELECT pg_sleep(0.05);

DO $$
DECLARE b uuid; ts1 timestamptz; ts2 timestamptz;
BEGIN
  SELECT f.b, f.ts1 INTO b, ts1 FROM _t2_fixture f;
  UPDATE brands SET name = '_test_brand2' WHERE id = b;
  SELECT updated_at INTO ts2 FROM brands WHERE id = b;
  IF ts2 <= ts1 THEN RAISE EXCEPTION 'updated_at trigger did not fire'; END IF;
END $$;

DO $$
DECLARE b uuid; c uuid; p uuid; n int; sv tsvector;
BEGIN
  SELECT f.b INTO b FROM _t2_fixture f;

  INSERT INTO companies (brand_id, name, domain) VALUES (b, 'Acme', 'acme.com') RETURNING id INTO c;
  -- domain dedup
  BEGIN
    INSERT INTO companies (brand_id, name, domain) VALUES (b, 'Acme again', 'acme.com');
    RAISE EXCEPTION 'expected unique_violation on domain';
  EXCEPTION WHEN unique_violation THEN NULL; END;

  INSERT INTO people (brand_id, company_id, first_name, last_name, email, title)
  VALUES (b, c, 'Zoë', 'Smith', 'Zoe@Acme.com', 'CTO') RETURNING id INTO p;
  -- case-insensitive email dedup
  BEGIN
    INSERT INTO people (brand_id, email) VALUES (b, 'zoe@acme.com');
    RAISE EXCEPTION 'expected unique_violation on email';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  -- invalid stage rejected
  BEGIN
    UPDATE people SET stage = 'bogus' WHERE id = p;
    RAISE EXCEPTION 'expected check_violation on stage';
  EXCEPTION WHEN check_violation THEN NULL; END;
  -- search_vector generated (accent-folded)
  SELECT search_vector INTO sv FROM people WHERE id = p;
  IF NOT sv @@ to_tsquery('simple', 'zoe') THEN RAISE EXCEPTION 'search_vector missing zoe'; END IF;
  -- actor default
  IF (SELECT created_by_source FROM people WHERE id = p) <> 'MANUAL' THEN
    RAISE EXCEPTION 'created_by_source default wrong'; END IF;

  SELECT count(*) INTO n FROM stage_options WHERE object = 'people';
  IF n < 7 THEN RAISE EXCEPTION 'expected 7 seeded people stages, got %', n; END IF;

  DELETE FROM people WHERE brand_id = b; DELETE FROM companies WHERE brand_id = b; DELETE FROM brands WHERE id = b;
END $$;

DROP TABLE _t2_fixture;
SELECT 'test_0002 OK' AS result;
