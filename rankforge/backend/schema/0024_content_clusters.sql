-- 0024 — content clusters (topical authority architecture).
--
-- Every opportunity/article belongs to exactly one content cluster: it either JOINS
-- an existing cluster as a supplementary member, or FOUNDS a new cluster as its
-- permanent authority PILLAR. A dedicated LLM (rankforge-cluster-architect) makes the
-- call, using a per-brand "cluster index" KB (full_doc) to retrieve candidate clusters
-- by semantic similarity. Internal links then concentrate on the pillar.
--
-- Apply: uv run python scripts/apply_schema.py schema/0024_content_clusters.sql

begin;

-- Per-brand cluster-index KB (full_doc: one embedding per cluster pillar) — used to
-- retrieve nearby clusters for the architect agent. Mirrors brand_kb_id/materials_kb_id.
alter table public.business_profiles add column if not exists cluster_kb_id text;

create table if not exists public.content_clusters (
    id                uuid primary key default gen_random_uuid(),
    business_id       uuid not null
                      references public.business_profiles (id) on delete cascade,
    label             text not null,
    theme             text,                  -- the cluster's scope (for retrieval + UI)
    -- The PERMANENT authority article. Set when the founding opportunity is drafted;
    -- never auto-replaced (only a manual override changes it). on delete set null so
    -- deleting the pillar doesn't drop the cluster.
    pillar_article_id uuid references public.articles (id) on delete set null,
    pillar_locked     boolean not null default false,
    index_doc_id      text,                  -- the cluster-index KB source id
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);
create index if not exists content_clusters_business_idx
    on public.content_clusters (business_id, created_at desc);

-- Membership is 1:1 — a column, not a join table.
alter table public.articles
    add column if not exists cluster_id uuid
        references public.content_clusters (id) on delete set null;
alter table public.articles
    add column if not exists cluster_role text
        check (cluster_role in ('pillar', 'member'));
alter table public.opportunities
    add column if not exists cluster_id uuid
        references public.content_clusters (id) on delete set null;
alter table public.opportunities
    add column if not exists cluster_role text
        check (cluster_role in ('pillar', 'member'));

alter table public.content_clusters enable row level security;
drop policy if exists content_clusters_read on public.content_clusters;
create policy content_clusters_read on public.content_clusters for select to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()));
drop policy if exists content_clusters_write on public.content_clusters;
create policy content_clusters_write on public.content_clusters for all to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()))
    with check (business_id in (select id from public.business_profiles
                                where org_id = public.current_org_id()));

notify pgrst, 'reload schema';

commit;
