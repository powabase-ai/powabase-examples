DO $$
DECLARE b uuid; p uuid; n int;
BEGIN
  -- brands.owner_id is NOT NULL since 0009_access_control.sql, and this script
  -- runs as a superuser with no auth.uid() to default from, so name an owner
  -- explicitly. Any account will do -- these fixtures are torn down below.
  INSERT INTO brands (name, owner_id) VALUES ('_test_ev', (SELECT id FROM auth.users ORDER BY created_at, id LIMIT 1)) RETURNING id INTO b;
  INSERT INTO people (brand_id, first_name) VALUES (b, 'Eve') RETURNING id INTO p;

  INSERT INTO events (brand_id, person_id, event_type, actor_source, actor_name, properties)
  VALUES (b, p, 'note', 'MANUAL', 'Test User', '{"body": "hello"}');
  -- unknown event type rejected (FK to event_types)
  BEGIN
    INSERT INTO events (brand_id, person_id, event_type, actor_source, actor_name)
    VALUES (b, p, 'nonsense', 'MANUAL', 'Test User');
    RAISE EXCEPTION 'expected fk violation on event_type';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  -- happens_at defaulted
  SELECT count(*) INTO n FROM events WHERE person_id = p AND happens_at IS NOT NULL;
  IF n <> 1 THEN RAISE EXCEPTION 'happens_at not defaulted'; END IF;
  -- timeline index exists
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'events_person_time_idx') THEN
    RAISE EXCEPTION 'events_person_time_idx missing'; END IF;
  -- import_batches + views exist
  PERFORM 1 FROM import_batches LIMIT 0;
  PERFORM 1 FROM views LIMIT 0;

  DELETE FROM events WHERE brand_id = b; DELETE FROM people WHERE brand_id = b; DELETE FROM brands WHERE id = b;
END $$;
SELECT 'test_0003 OK' AS result;
