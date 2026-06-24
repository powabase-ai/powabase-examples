-- 0018 — brand materials (M6, first cut): ground drafts in the brand's OWN pages.
--
-- Until now the grounding KB held only scraped COMPETITOR research pages, so drafts
-- never described the brand or linked to its docs. This adds a SEPARATE per-brand
-- "materials" KB built from the brand's own URLs (sitemap-crawled + manually added),
-- which generation retrieves brand-wide for accurate brand narrative + internal links.
--
-- Apply: uv run python scripts/apply_schema.py schema/0018_brand_materials.sql

begin;

alter table public.business_profiles
    add column if not exists materials_kb_id text;
alter table public.business_profiles
    add column if not exists materials_progress jsonb not null default '{}'::jsonb;

-- One row per ingested brand page (mirrors research_sources). `origin` distinguishes
-- sitemap-crawled from manually-added URLs so the UI can manage them.
create table if not exists public.brand_sources (
    id           uuid primary key default gen_random_uuid(),
    business_id  uuid not null
                 references public.business_profiles (id) on delete cascade,
    source_id    text,                 -- Powabase ai.sources id (null until imported)
    url          text not null,
    title        text,
    status       text,                 -- extraction_status at capture time
    origin       text not null default 'manual'
                 check (origin in ('sitemap', 'manual')),
    created_at   timestamptz not null default now()
);
create index if not exists brand_sources_business_idx
    on public.brand_sources (business_id, created_at desc);
-- Dedup a brand's pages case-insensitively by URL.
create unique index if not exists brand_sources_business_url_idx
    on public.brand_sources (business_id, lower(url));

-- RLS: org-scoped through the owning business (the backend bypasses RLS and enforces
-- in-app; this guards any direct PostgREST access). Mirrors 0011's child-table pattern.
alter table public.brand_sources enable row level security;
drop policy if exists brand_sources_read on public.brand_sources;
create policy brand_sources_read on public.brand_sources for select to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()));
drop policy if exists brand_sources_write on public.brand_sources;
create policy brand_sources_write on public.brand_sources for all to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()))
    with check (business_id in (select id from public.business_profiles
                                where org_id = public.current_org_id()));

notify pgrst, 'reload schema';

commit;
