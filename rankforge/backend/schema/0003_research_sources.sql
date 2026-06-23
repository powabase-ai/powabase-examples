-- RankForge 0003 — async research status + research_sources (Powabase Sources link).
-- Apply: psql / scripts/apply_schema.py schema/0003_research_sources.sql
--
-- Research now runs asynchronously (status polling) and stores each scraped
-- competitor page as a Powabase Source (approach 2). research_sources links a
-- research_run to those sources; the raw markdown lives in Powabase, fetched on
-- demand via the source's markdown derivative.

begin;

alter table public.research_runs
    add column if not exists status text not null default 'done'
        check (status in ('queued', 'searching', 'scraping', 'analyzing', 'done', 'failed'));
alter table public.research_runs add column if not exists error text;
alter table public.research_runs
    add column if not exists progress jsonb not null default '{}'::jsonb;

create table if not exists public.research_sources (
    id               uuid primary key default gen_random_uuid(),
    research_run_id  uuid not null references public.research_runs (id) on delete cascade,
    source_id        text not null,              -- Powabase ai.sources id
    url              text,
    title            text,
    word_count       int,
    status           text,                       -- extraction_status at capture time
    created_at       timestamptz not null default now()
);
create index if not exists research_sources_run_idx
    on public.research_sources (research_run_id);

alter table public.research_sources enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies where schemaname = 'public'
          and tablename = 'research_sources' and policyname = 'research_sources_read'
    ) then
        create policy research_sources_read on public.research_sources
            for select to authenticated using (true);
    end if;
    if not exists (
        select 1 from pg_policies where schemaname = 'public'
          and tablename = 'research_sources' and policyname = 'research_sources_write'
    ) then
        create policy research_sources_write on public.research_sources
            for all to authenticated using (true) with check (true);
    end if;
end $$;

commit;
