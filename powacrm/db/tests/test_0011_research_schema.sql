DO $$
DECLARE b uuid; c uuid; j uuid; n int;
BEGIN
  SELECT id INTO b FROM brands WHERE name = 'gpt-trainer';
  IF b IS NULL THEN RAISE EXCEPTION 'seed brand missing; run db/seed/seed_gpt_trainer.sql'; END IF;

  -- new columns exist with the right defaults
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='brands' AND column_name='icp_notes')
    THEN RAISE EXCEPTION 'brands.icp_notes missing'; END IF;
  IF (SELECT research_daily_cap FROM brands WHERE id=b) <> 25
    THEN RAISE EXCEPTION 'research_daily_cap default is not 25'; END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='companies' AND column_name='research_data')
    THEN RAISE EXCEPTION 'companies.research_data missing'; END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='companies' AND column_name='researched_at')
    THEN RAISE EXCEPTION 'companies.researched_at missing'; END IF;

  -- the researched event type is seeded, or the worker's event insert FKs out
  IF NOT EXISTS (SELECT 1 FROM event_types WHERE name='researched')
    THEN RAISE EXCEPTION 'event_types is missing the researched row'; END IF;

  INSERT INTO companies (brand_id, name, domain) VALUES (b, '_t11_co', 't11.example')
    RETURNING id INTO c;

  -- an invalid status is rejected
  BEGIN
    INSERT INTO research_jobs (brand_id, company_id, status) VALUES (b, c, 'bogus');
    RAISE EXCEPTION 'expected check_violation on research_jobs.status';
  EXCEPTION WHEN check_violation THEN NULL; END;

  INSERT INTO research_jobs (brand_id, company_id) VALUES (b, c) RETURNING id INTO j;
  IF (SELECT status FROM research_jobs WHERE id=j) <> 'queued'
    THEN RAISE EXCEPTION 'research_jobs.status should default to queued'; END IF;

  -- one active job per company: double-spend must be structurally impossible
  BEGIN
    INSERT INTO research_jobs (brand_id, company_id) VALUES (b, c);
    RAISE EXCEPTION 'expected unique_violation: a second active job for one company';
  EXCEPTION WHEN unique_violation THEN NULL; END;

  -- but a finished job frees the company for a later one
  UPDATE research_jobs SET status='done' WHERE id=j;
  INSERT INTO research_jobs (brand_id, company_id) VALUES (b, c);
  SELECT count(*) INTO n FROM research_jobs WHERE company_id=c;
  IF n <> 2 THEN RAISE EXCEPTION 'expected 2 job rows after completing the first, got %', n; END IF;

  DELETE FROM research_jobs WHERE company_id=c;
  DELETE FROM companies WHERE id=c;
END $$;
SELECT 'test_0011_schema OK' AS result;
