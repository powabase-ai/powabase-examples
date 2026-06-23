-- 0008 — autonomous content scouts (M5 / Phase 9).
-- Per-brand scout config + run history + an opportunity inbox. Scouts discover
-- timely topics (Exa news/SERP + competitor signals), score them, and — at the
-- auto_draft autonomy level — kick off the generation pipeline and stage the
-- result as `in_review` (never auto-published).
--
-- Apply: psql "$POWABASE_DATABASE_URL" -f backend/schema/0008_scouts.sql

begin;

create table if not exists public.scout_configs (
    business_id        uuid primary key
                       references public.business_profiles (id) on delete cascade,
    enabled            boolean not null default false,
    cadence            text not null default 'daily'
                       check (cadence in ('daily', 'weekly')),
    autonomy           text not null default 'suggest'
                       check (autonomy in ('suggest', 'auto_draft')),
    min_score          int not null default 70,
    max_drafts_per_run int not null default 1,
    focus              jsonb not null default '[]'::jsonb,  -- optional topic override
    last_run_at        timestamptz,
    next_run_at        timestamptz,
    updated_at         timestamptz not null default now()
);

create table if not exists public.scout_runs (
    id           uuid primary key default gen_random_uuid(),
    business_id  uuid not null
                 references public.business_profiles (id) on delete cascade,
    status       text not null default 'running'
                 check (status in ('running', 'done', 'failed')),
    trigger      text not null default 'schedule'
                 check (trigger in ('schedule', 'manual')),
    found        int not null default 0,
    drafted      int not null default 0,
    error        text,
    created_at   timestamptz not null default now()
);
create index if not exists scout_runs_business_idx
    on public.scout_runs (business_id, created_at desc);

create table if not exists public.opportunities (
    id            uuid primary key default gen_random_uuid(),
    business_id   uuid not null
                  references public.business_profiles (id) on delete cascade,
    scout_run_id  uuid references public.scout_runs (id) on delete set null,
    title         text not null,
    angle         text,                                  -- recommended take
    why_now       text,                                  -- timeliness rationale
    keyword       text,
    source_type   text,                                  -- news|serp|competitor
    source_url    text,
    evidence      jsonb not null default '{}'::jsonb,
    score         int not null default 0,
    scores        jsonb not null default '{}'::jsonb,    -- per-signal breakdown
    status        text not null default 'new'
                  check (status in
                      ('new', 'queued', 'drafting', 'drafted', 'dismissed')),
    article_id    uuid references public.articles (id) on delete set null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create index if not exists opportunities_business_idx
    on public.opportunities (business_id, created_at desc);

alter table public.scout_configs enable row level security;
alter table public.scout_runs    enable row level security;
alter table public.opportunities enable row level security;

do $$
declare t text;
begin
    foreach t in array array['scout_configs', 'scout_runs', 'opportunities'] loop
        execute format(
            'create policy %I on public.%I for select to authenticated using (true)',
            t || '_read', t
        );
        execute format(
            'create policy %I on public.%I for all to authenticated using (true) with check (true)',
            t || '_write', t
        );
    end loop;
end $$;

notify pgrst, 'reload schema';

commit;
