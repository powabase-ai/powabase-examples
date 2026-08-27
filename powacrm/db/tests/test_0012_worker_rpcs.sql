DO $$
DECLARE b uuid; c uuid; p1 uuid; p2 uuid; j uuid; r jsonb; n int; st text;
BEGIN
  SELECT id INTO b FROM brands WHERE name='gpt-trainer';

  -- Determinism guard: claim_research_jobs(5) claims the oldest queued jobs
  -- PROJECT-WIDE, not just this test's. A stale queued row left behind by an
  -- earlier failed run of this same test (for this same brand) would crowd out
  -- our own job and make the claim assertion below fail for a misleading
  -- reason. Clear any such leftovers before creating our own job.
  DELETE FROM research_jobs WHERE brand_id = b AND status = 'queued';

  INSERT INTO companies (brand_id, name, domain) VALUES (b,'_t12w_co','t12w.example') RETURNING id INTO c;
  INSERT INTO people (brand_id, company_id, first_name, email, stage)
    VALUES (b,c,'W1','_t12w_1@example.com','sourced') RETURNING id INTO p1;
  INSERT INTO people (brand_id, company_id, first_name, email, stage)
    VALUES (b,c,'W2','_t12w_2@example.com','enriched') RETURNING id INTO p2;
  INSERT INTO research_jobs (brand_id, company_id) VALUES (b,c) RETURNING id INTO j;

  -- claim moves it to running and stamps the attempt
  PERFORM claim_research_jobs(5);
  SELECT status, attempts INTO st, n FROM research_jobs WHERE id=j;
  IF st <> 'running' THEN RAISE EXCEPTION 'claim did not set running (got %)', st; END IF;
  IF n <> 1 THEN RAISE EXCEPTION 'claim did not increment attempts (got %)', n; END IF;

  -- a malformed payload must be refused, and must write nothing
  BEGIN
    PERFORM complete_research_job(j, '{"summary":"x"}'::jsonb);
    RAISE EXCEPTION 'expected a validation failure for a payload with no fit array';
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = 'P0001' AND SQLERRM LIKE 'expected a validation failure%' THEN RAISE; END IF;
  END;
  IF (SELECT research FROM companies WHERE id=c) IS NOT NULL
    THEN RAISE EXCEPTION 'a rejected payload still wrote to companies.research'; END IF;

  -- a valid payload writes everything, once
  r := complete_research_job(j, jsonb_build_object(
    'summary', 'Acme sells widgets.',
    'tech_stack', jsonb_build_array('Intercom','Segment'),
    'why_now', 'They just raised.',
    'hooks', jsonb_build_array(jsonb_build_object('hook','h','evidence','e','source_url','https://x.test')),
    'sources', jsonb_build_array('https://x.test'),
    'injection_observed', false,
    'fit', jsonb_build_array(
      jsonb_build_object('person_id', p1, 'score', 82, 'rationale', 'fits'),
      jsonb_build_object('person_id', p2, 'score', 140, 'rationale', 'over-range on purpose'))));
  IF (r->>'people_scored')::int <> 2 THEN RAISE EXCEPTION 'expected 2 people scored, got %', r; END IF;
  IF (SELECT fit_score FROM people WHERE id=p1) <> 82 THEN RAISE EXCEPTION 'p1 score not written'; END IF;
  -- out-of-range scores are clamped, not rejected: the CHECK would abort the batch
  IF (SELECT fit_score FROM people WHERE id=p2) <> 100 THEN RAISE EXCEPTION 'score 140 was not clamped to 100'; END IF;
  IF (SELECT stage FROM people WHERE id=p1) <> 'researched' THEN RAISE EXCEPTION 'p1 stage not advanced'; END IF;
  IF (SELECT researched_at FROM companies WHERE id=c) IS NULL THEN RAISE EXCEPTION 'researched_at not set'; END IF;
  IF (SELECT tech_stack FROM companies WHERE id=c) IS NULL THEN RAISE EXCEPTION 'tech_stack not written'; END IF;
  SELECT count(*) INTO n FROM events WHERE person_id IN (p1,p2) AND event_type='researched';
  IF n <> 2 THEN RAISE EXCEPTION 'expected 2 researched events, got %', n; END IF;
  IF (SELECT status FROM research_jobs WHERE id=j) <> 'done' THEN RAISE EXCEPTION 'job not marked done'; END IF;

  -- fail path: under 3 attempts it goes back to the queue, at 3 it stops
  INSERT INTO research_jobs (brand_id, company_id) VALUES (b,c) RETURNING id INTO j;
  PERFORM claim_research_jobs(5);
  PERFORM fail_research_job(j, 'scrape timed out');
  IF (SELECT status FROM research_jobs WHERE id=j) <> 'queued'
    THEN RAISE EXCEPTION 'first failure should return the job to queued'; END IF;
  UPDATE research_jobs SET attempts = 3, status='running' WHERE id=j;
  PERFORM fail_research_job(j, 'scrape timed out again');
  SELECT status INTO st FROM research_jobs WHERE id=j;
  IF st <> 'failed' THEN RAISE EXCEPTION 'third failure should be terminal (got %)', st; END IF;

  -- stalled jobs are recovered, or nothing would ever free them
  UPDATE research_jobs SET status='running', started_at = now() - interval '20 minutes' WHERE id=j;
  IF requeue_stalled_research_jobs() < 1 THEN RAISE EXCEPTION 'a 20-minute-old running job was not requeued'; END IF;
  IF (SELECT status FROM research_jobs WHERE id=j) <> 'queued' THEN RAISE EXCEPTION 'stalled job not returned to queued'; END IF;

  DELETE FROM events WHERE person_id IN (p1,p2);
  DELETE FROM research_jobs WHERE company_id=c;
  DELETE FROM people WHERE company_id=c;
  DELETE FROM companies WHERE id=c;
END $$;
SELECT 'test_0012_worker OK' AS result;
