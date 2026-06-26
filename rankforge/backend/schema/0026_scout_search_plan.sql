-- 0026 — two-phase scouting: a reviewable Search Plan.
-- A scout run can now start in a 'planned' state carrying a `plan` (the trending
-- search queries + sources the scout will run). The user reviews/edits the plan,
-- then executes it; scheduled runs auto-plan and execute in one go.
--
-- Apply: psql "$POWABASE_DATABASE_URL" -f backend/schema/0026_scout_search_plan.sql

begin;

alter table public.scout_runs add column if not exists plan jsonb;

-- Allow the new 'planned' state (run exists, plan generated, awaiting execution).
alter table public.scout_runs drop constraint if exists scout_runs_status_check;
alter table public.scout_runs add constraint scout_runs_status_check
    check (status in ('planned', 'running', 'done', 'failed'));

notify pgrst, 'reload schema';

commit;
