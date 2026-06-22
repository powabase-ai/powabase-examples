-- RankForge 0006 — fact-check / grounding report on articles.
-- Apply: scripts/apply_schema.py schema/0006_grounding_report.sql

begin;

alter table public.articles
    add column if not exists grounding_report jsonb;

commit;
