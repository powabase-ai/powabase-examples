-- 0028 — index articles.cluster_id.
-- 0024 added articles.cluster_id (+ cluster_role) but no index. Pure cluster_id
-- predicates run on hot paths — _structural_targets (every suggest_links, i.e. after
-- each generation/refine and across the relink sweep), list_members (cluster detail),
-- set_pillar — and articles_business_idx can't serve a cluster_id-only filter, so they
-- seq-scan and degrade as the article count grows. A partial index (cluster_id is not
-- null) keeps it small since most articles are unclustered.
--
-- Apply: psql "$POWABASE_DATABASE_URL" -f backend/schema/0028_articles_cluster_idx.sql

begin;

create index if not exists articles_cluster_idx
    on public.articles (cluster_id)
    where cluster_id is not null;

notify pgrst, 'reload schema';

commit;
