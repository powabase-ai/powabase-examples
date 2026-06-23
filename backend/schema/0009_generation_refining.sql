-- 0009 — allow the 'refining' generation status (auto-revision loop).
-- The generation pipeline now iterates the draft against the SEO/GEO/Grounding
-- evaluators ("refining") before it finishes.
--
-- Apply: psql "$POWABASE_DATABASE_URL" -f backend/schema/0009_generation_refining.sql

begin;

alter table public.articles
    drop constraint if exists articles_generation_status_check;
alter table public.articles
    add constraint articles_generation_status_check
    check (generation_status in
        ('queued', 'grounding', 'outlining', 'drafting', 'optimizing',
         'refining', 'done', 'failed'));

commit;
