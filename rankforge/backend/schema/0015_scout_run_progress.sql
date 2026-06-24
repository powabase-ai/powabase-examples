-- 0015 — live progress narration for scout runs.
--
-- A scout run goes through sequential phases (discover via web search → filter
-- against existing coverage → score/store → optionally draft). Add a `progress`
-- jsonb the worker updates per phase so the UI can show what's happening live,
-- mirroring research_runs.progress / articles.progress.
--
-- Apply: uv run python scripts/apply_schema.py schema/0015_scout_run_progress.sql

begin;

alter table public.scout_runs
    add column if not exists progress jsonb not null default '{}'::jsonb;

notify pgrst, 'reload schema';

commit;
