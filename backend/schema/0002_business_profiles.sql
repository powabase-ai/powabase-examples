-- RankForge 0002 — business_profiles (multi-brand) + brand scoping.
-- Apply after 0001:
--   psql "$POWABASE_DATABASE_URL" -f backend/schema/0002_business_profiles.sql
--
-- The workspace manages multiple brands/businesses. A business_profile captures one
-- brand's niche; content rows are soft-scoped by business_id (not hard RLS tenancy).
-- See docs/design/10-editorial-and-multibrand.md and docs/PRD.md §3.

begin;

create table if not exists public.business_profiles (
    id              uuid primary key default gen_random_uuid(),
    name            text not null,
    domain          text,
    description     text,
    niche           text,
    audience        text,
    seed_topics     jsonb not null default '[]'::jsonb,
    target_keywords jsonb not null default '[]'::jsonb,
    competitors     jsonb not null default '[]'::jsonb,   -- [{name?, domain}]
    brand_kb_id     text,                                  -- Powabase KB id (grounding)
    sitemap_url     text,
    created_by      uuid references auth.users (id) on delete set null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- Soft brand scoping on the content tables created in 0001.
alter table public.research_runs add column if not exists business_id uuid
    references public.business_profiles (id) on delete cascade;
alter table public.briefs add column if not exists business_id uuid
    references public.business_profiles (id) on delete cascade;
alter table public.articles add column if not exists business_id uuid
    references public.business_profiles (id) on delete cascade;

create index if not exists research_runs_business_idx on public.research_runs (business_id);
create index if not exists briefs_business_idx on public.briefs (business_id);
create index if not exists articles_business_idx on public.articles (business_id);

-- RLS: team-visible reads, authenticated writes (backend uses service role / bypasses).
alter table public.business_profiles enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'business_profiles'
          and policyname = 'business_profiles_read'
    ) then
        create policy business_profiles_read on public.business_profiles
            for select to authenticated using (true);
    end if;
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'business_profiles'
          and policyname = 'business_profiles_write'
    ) then
        create policy business_profiles_write on public.business_profiles
            for all to authenticated using (true) with check (true);
    end if;
end $$;

commit;
