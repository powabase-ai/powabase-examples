-- 0010 — composite indexes covering the per-brand list queries' sort order.
-- briefs/research_runs are listed `where business_id = %s order by created_at desc`
-- and the opportunity inbox sorts by score; the prior single-column indexes didn't
-- cover the ORDER BY. Cheap and idempotent.
--
-- Apply: psql "$POWABASE_DATABASE_URL" -f backend/schema/0010_list_indexes.sql

begin;

create index if not exists briefs_business_created_idx
    on public.briefs (business_id, created_at desc);
create index if not exists research_runs_business_created_idx
    on public.research_runs (business_id, created_at desc);
create index if not exists opportunities_business_score_idx
    on public.opportunities (business_id, score desc);

commit;
