-- RankForge 0004 — async article generation status.
-- Apply: scripts/apply_schema.py schema/0004_article_generation.sql
--
-- The editorial `status` (draft/in_review/...) is separate from the generation
-- pipeline state tracked here for polling.

begin;

alter table public.articles
    add column if not exists generation_status text not null default 'done'
        check (generation_status in
            ('queued', 'grounding', 'outlining', 'drafting', 'optimizing', 'done', 'failed'));
alter table public.articles add column if not exists generation_error text;
alter table public.articles
    add column if not exists progress jsonb not null default '{}'::jsonb;
alter table public.articles
    add column if not exists research_run_id uuid
        references public.research_runs (id) on delete set null;

commit;
