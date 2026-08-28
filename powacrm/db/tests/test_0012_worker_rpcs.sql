DO $$
DECLARE
  b uuid; c uuid; p1 uuid; p2 uuid; p3 uuid; p4 uuid; p_other uuid; b2 uuid;
  j uuid; j2 uuid; r jsonb; n int; st text;
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
  -- Fixture for the brand-boundary assertion below: a person who belongs to a
  -- DIFFERENT brand entirely, so a fit entry naming them must be refused by
  -- complete_research_job's own `AND brand_id = v_brand` guard, not merely by
  -- there being no such person at all.
  INSERT INTO brands (name, owner_id) VALUES ('_t12w_other_brand', (SELECT owner_id FROM brands WHERE name='gpt-trainer')) RETURNING id INTO b2;
  INSERT INTO people (brand_id, first_name, email, stage)
    VALUES (b2,'XBrand','_t12w_xbrand@example.com','sourced') RETURNING id INTO p_other;
  INSERT INTO research_jobs (brand_id, company_id) VALUES (b,c) RETURNING id INTO j;

  -- claim moves it to running and stamps the attempt
  PERFORM claim_research_jobs(5);
  SELECT status, attempts INTO st, n FROM research_jobs WHERE id=j;
  IF st <> 'running' THEN RAISE EXCEPTION 'claim did not set running (got %)', st; END IF;
  IF n <> 1 THEN RAISE EXCEPTION 'claim did not increment attempts (got %)', n; END IF;

  -- a malformed payload must be refused
  BEGIN
    PERFORM complete_research_job(j, '{"summary":"x"}'::jsonb);
    RAISE EXCEPTION 'expected a validation failure for a payload with no fit array';
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = 'P0001' AND SQLERRM LIKE 'expected a validation failure%' THEN RAISE; END IF;
  END;
  -- NOTE: a "did this write anything" check does NOT belong here. A caught
  -- BEGIN...EXCEPTION block is a PL/pgSQL subtransaction: whatever the failed
  -- call inside it wrote is unconditionally rolled back to the pre-BEGIN
  -- savepoint the instant the exception is caught, REGARDLESS of whether
  -- validation ran before or after that write. So a post-check here can never
  -- fail -- it is not a meaningful test, it was removed. What IS falsifiable
  -- without any exception/rollback masking the result: complete_research_job
  -- called again on a job that is no longer 'running' must be a genuine no-op
  -- (see the re-complete/re-fail assertions below, which get a normal,
  -- non-raising return and inspect real, un-rolled-back state).

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

  -- Fixed in review: complete_research_job/fail_research_job had no status
  -- precondition, so re-completing (or re-failing) an already-`done` job would
  -- write duplicate side effects -- exactly what a worker slower than
  -- requeue_stalled_research_jobs' 15-minute window can trigger (job swept,
  -- re-claimed and re-completed by someone else, then the original slow
  -- worker finally returns and calls this on what is now a done job). Both
  -- must be a genuine, observable no-op -- no exception is raised here, so
  -- nothing masks the result the way the removed check above was masked.
  r := complete_research_job(j, jsonb_build_object(
    'summary', 'A different, later report -- must never be applied.',
    'fit', jsonb_build_array(jsonb_build_object('person_id', p1, 'score', 1, 'rationale', 'x'))));
  IF (r->>'ok')::boolean <> false THEN RAISE EXCEPTION 're-completing a done job did not report ok:false, got %', r; END IF;
  IF (SELECT research FROM companies WHERE id=c) <> 'Acme sells widgets.'
    THEN RAISE EXCEPTION 're-completing a done job overwrote companies.research'; END IF;
  IF (SELECT fit_score FROM people WHERE id=p1) <> 82
    THEN RAISE EXCEPTION 're-completing a done job overwrote p1''s fit_score'; END IF;
  SELECT count(*) INTO n FROM events WHERE person_id IN (p1,p2) AND event_type='researched';
  IF n <> 2 THEN RAISE EXCEPTION 're-completing a done job wrote a duplicate event (count=%)', n; END IF;

  r := fail_research_job(j, 'should be ignored -- job already done');
  IF (r->>'ok')::boolean <> false THEN RAISE EXCEPTION 'fail_research_job on a done job did not report ok:false, got %', r; END IF;
  IF (SELECT status FROM research_jobs WHERE id=j) <> 'done'
    THEN RAISE EXCEPTION 'fail_research_job flipped a done job out of done (got %)', (SELECT status FROM research_jobs WHERE id=j); END IF;

  -- Fit-array edge cases: a fractional score, a non-numeric score, a garbage
  -- (non-uuid) person_id, a person in a DIFFERENT brand, and a non-boolean
  -- injection_observed -- all in one payload, all malformed in a different
  -- way, none of them may abort the call or corrupt another entry.
  INSERT INTO people (brand_id, company_id, first_name, email, stage)
    VALUES (b,c,'W3','_t12w_3@example.com','sourced') RETURNING id INTO p3;
  INSERT INTO people (brand_id, company_id, first_name, email, stage)
    VALUES (b,c,'W4','_t12w_4@example.com','sourced') RETURNING id INTO p4;
  INSERT INTO research_jobs (brand_id, company_id) VALUES (b,c) RETURNING id INTO j2;
  PERFORM claim_research_jobs(5);

  r := complete_research_job(j2, jsonb_build_object(
    'summary', 'Edge-case payload.',
    'injection_observed', 'maybe',
    'fit', jsonb_build_array(
      jsonb_build_object('person_id', p3, 'score', 82.5, 'rationale', 'fractional'),
      jsonb_build_object('person_id', p4, 'score', 'not-a-number', 'rationale', 'garbage score'),
      jsonb_build_object('person_id', 'not-a-uuid', 'score', 50, 'rationale', 'garbage id'),
      jsonb_build_object('person_id', p_other, 'score', 99, 'rationale', 'wrong brand'))));
  IF (r->>'people_scored')::int <> 2 THEN RAISE EXCEPTION 'expected 2 people scored in the edge-case payload, got %', r; END IF;
  -- round() is "round half away from zero": 82.5 -> 83.
  IF (SELECT fit_score FROM people WHERE id=p3) <> 83
    THEN RAISE EXCEPTION 'fractional score 82.5 was not rounded to 83, got %', (SELECT fit_score FROM people WHERE id=p3); END IF;
  IF (SELECT fit_score FROM people WHERE id=p4) <> 0
    THEN RAISE EXCEPTION 'non-numeric score did not default to 0, got %', (SELECT fit_score FROM people WHERE id=p4); END IF;
  -- the brand-boundary guard: a fit entry naming a real person in a DIFFERENT
  -- brand must not be scored and must not produce an event.
  IF (SELECT fit_score FROM people WHERE id=p_other) IS NOT NULL
    THEN RAISE EXCEPTION 'a cross-brand fit entry scored a person outside the job''s brand'; END IF;
  SELECT count(*) INTO n FROM events WHERE person_id=p_other AND event_type='researched';
  IF n <> 0 THEN RAISE EXCEPTION 'a cross-brand fit entry produced an event for a person outside the job''s brand'; END IF;
  IF (SELECT status FROM research_jobs WHERE id=j2) <> 'done' THEN RAISE EXCEPTION 'edge-case job not marked done'; END IF;

  -- hooks and sources must be arrays. Blocker B3's server half: they were stored
  -- verbatim, and a string `hooks` threw `hooks.map is not a function` in the SPA
  -- and (with no error boundary at the time) blanked the whole app. tech_stack --
  -- the one field that WAS validated -- is the one the client already guarded.
  INSERT INTO research_jobs (brand_id, company_id) VALUES (b,c) RETURNING id INTO j2;
  PERFORM claim_research_jobs(5);
  BEGIN
    PERFORM complete_research_job(j2, jsonb_build_object(
      'summary', 'x', 'fit', '[]'::jsonb, 'hooks', 'none found'));
    RAISE EXCEPTION 'a string `hooks` was accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = 'P0001' AND SQLERRM = 'a string `hooks` was accepted' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM complete_research_job(j2, jsonb_build_object(
      'summary', 'x', 'fit', '[]'::jsonb, 'sources', to_jsonb('https://one.example'::text)));
    RAISE EXCEPTION 'a string `sources` was accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = 'P0001' AND SQLERRM = 'a string `sources` was accepted' THEN RAISE; END IF;
  END;


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

  DELETE FROM events WHERE person_id IN (p1,p2,p3,p4,p_other);
  DELETE FROM research_jobs WHERE company_id=c;
  DELETE FROM people WHERE company_id=c;
  DELETE FROM people WHERE id=p_other;
  DELETE FROM companies WHERE id=c;
  DELETE FROM brands WHERE id=b2;
END $$;
SELECT 'test_0012_worker OK' AS result;
