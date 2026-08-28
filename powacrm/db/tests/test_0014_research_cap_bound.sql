-- brands.research_daily_cap must be bounded IN THE DATABASE, not in the form.
-- The whole point of 0014 is that the browser is not the enforcement point, so
-- this asserts against the constraint directly. The client half of the same
-- fix (a matching `max` and error message on the Settings form) is covered by
-- app/src/settings/capField.test.ts.
--
-- Runs inside one DO block, so an assertion failure rolls the whole thing back
-- and the seed brand keeps its real cap.
DO $$
DECLARE b uuid; v int;
BEGIN
  SELECT id INTO b FROM brands WHERE name = 'gpt-trainer';
  IF b IS NULL THEN RAISE EXCEPTION 'seed brand missing; run db/seed/seed_gpt_trainer.sql'; END IF;
  SELECT research_daily_cap INTO v FROM brands WHERE id = b;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'brands_research_daily_cap_range'
                    AND conrelid = 'public.brands'::regclass)
    THEN RAISE EXCEPTION 'brands_research_daily_cap_range constraint missing -- apply 0014'; END IF;

  -- above the ceiling: this is the exact write a stranger's PATCH performed
  BEGIN
    UPDATE brands SET research_daily_cap = 100000 WHERE id = b;
    RAISE EXCEPTION 'expected check_violation raising the cap to 100000';
  EXCEPTION WHEN check_violation THEN NULL; END;

  BEGIN
    UPDATE brands SET research_daily_cap = 101 WHERE id = b;
    RAISE EXCEPTION 'expected check_violation at 101 (one past the ceiling)';
  EXCEPTION WHEN check_violation THEN NULL; END;

  -- below the floor
  BEGIN
    UPDATE brands SET research_daily_cap = -5 WHERE id = b;
    RAISE EXCEPTION 'expected check_violation at -5';
  EXCEPTION WHEN check_violation THEN NULL; END;

  -- both endpoints stay legal: 0 is how you pause a brand's research, 100 is
  -- the documented ceiling and must not be off by one.
  UPDATE brands SET research_daily_cap = 0 WHERE id = b;
  UPDATE brands SET research_daily_cap = 100 WHERE id = b;

  -- and a new brand cannot be inserted over the ceiling either
  BEGIN
    INSERT INTO brands (name, owner_id, research_daily_cap)
      VALUES ('_t14_over_cap', (SELECT owner_id FROM brands WHERE id = b), 5000);
    RAISE EXCEPTION 'expected check_violation inserting a brand with cap 5000';
  EXCEPTION WHEN check_violation THEN NULL; END;

  UPDATE brands SET research_daily_cap = v WHERE id = b;
  IF (SELECT research_daily_cap FROM brands WHERE id = b) <> v
    THEN RAISE EXCEPTION 'failed to restore the seed brand cap to %', v; END IF;
END $$;
SELECT 'test_0014_cap_bound OK' AS result;
