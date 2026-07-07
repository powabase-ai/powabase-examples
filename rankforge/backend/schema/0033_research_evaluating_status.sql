-- Allow the 'evaluating' research-run status.
--
-- run_research_task sets research_runs.status = 'evaluating' during the source-quality
-- evaluation + backfill phase (shipped with the source-trust feature, 0031). The status
-- CHECK constraint dates back to 0003 and predates that phase — it only permitted
-- queued/searching/scraping/analyzing/done/failed, so every run that opted into source
-- evaluation died with a check_violation the moment it entered the evaluating phase.
-- Re-state the constraint with 'evaluating' included.

begin;

alter table public.research_runs drop constraint if exists research_runs_status_check;
alter table public.research_runs add constraint research_runs_status_check
    check (status in (
        'queued', 'searching', 'scraping', 'analyzing', 'evaluating', 'done', 'failed'
    ));

commit;
