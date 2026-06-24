-- 0016 — live progress narration for an opportunity being auto-drafted.
--
-- "Draft this" (auto_draft) runs research → brief → generate, which takes minutes.
-- Today the card only shows a bare "Drafting…". Add a `progress` jsonb the worker
-- updates per phase so the card can narrate what's happening (mirrors
-- scout_runs.progress / research_runs.progress / articles.progress).
--
-- Apply: uv run python scripts/apply_schema.py schema/0016_opportunity_progress.sql

begin;

alter table public.opportunities
    add column if not exists progress jsonb not null default '{}'::jsonb;

notify pgrst, 'reload schema';

commit;
