-- RankForge app schema — first cut (strawman; see docs/PRD.md §6).
-- Apply against the Powabase project's Database URL:
--   psql "$POWABASE_DATABASE_URL" -f backend/schema/0001_init.sql
--
-- These are RankForge's OWN tables in the project's `public` schema. They live
-- alongside Powabase's `ai.*` tables in the same database. RLS is enabled from
-- the start: new public tables default to RLS OFF (world read/write with the
-- Anon key), which we never want for app data.
--
-- Auth model: multi-user team, shared workspace. Rows are team-visible to any
-- authenticated user; writes are attributed to the author. The trusted backend
-- uses the Service Role connection (RLS bypassed) for its own authz; these
-- policies guard any direct PostgREST access with the Anon/authenticated key.

begin;

-- ---------------------------------------------------------------------------
-- profiles: mirrors auth.users, holds role + display info
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
    id          uuid primary key references auth.users (id) on delete cascade,
    email       text,
    display_name text,
    role        text not null default 'writer'
                check (role in ('writer', 'editor', 'admin')),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- research_runs: SERP + competitor intelligence (Phase 1)
-- ---------------------------------------------------------------------------
create table if not exists public.research_runs (
    id          uuid primary key default gen_random_uuid(),
    topic       text not null,
    locale      text not null default 'en-US',
    serp        jsonb not null default '{}'::jsonb,   -- results, PAA, snippets
    competitors jsonb not null default '[]'::jsonb,   -- per-URL teardown
    clusters    jsonb not null default '[]'::jsonb,   -- keyword clusters
    intent      text,                                  -- info/commercial/...
    agent_run_id text,                                  -- Powabase run id
    created_by  uuid references auth.users (id) on delete set null,
    created_at  timestamptz not null default now()
);
create index if not exists research_runs_created_at_idx
    on public.research_runs (created_at desc);

-- ---------------------------------------------------------------------------
-- briefs: content brief derived from research (Phase 2)
-- ---------------------------------------------------------------------------
create table if not exists public.briefs (
    id               uuid primary key default gen_random_uuid(),
    research_run_id  uuid references public.research_runs (id) on delete set null,
    topic            text not null,
    primary_keyword  text,
    secondary_keywords jsonb not null default '[]'::jsonb,
    target_word_count int,
    headings         jsonb not null default '[]'::jsonb,   -- suggested H2/H3
    entities         jsonb not null default '[]'::jsonb,   -- must-cover
    questions        jsonb not null default '[]'::jsonb,   -- answer these
    link_suggestions jsonb not null default '{}'::jsonb,   -- internal/external
    suggested_title  text,
    suggested_meta   text,
    created_by       uuid references auth.users (id) on delete set null,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- articles: the long-form output + scores (Phases 3-5, 7)
-- ---------------------------------------------------------------------------
create table if not exists public.articles (
    id            uuid primary key default gen_random_uuid(),
    brief_id      uuid references public.briefs (id) on delete set null,
    title         text not null default 'Untitled',
    slug          text,
    status        text not null default 'draft'
                  check (status in ('draft','in_review','approved','published','archived')),
    content_md    text not null default '',
    content_html  text,
    meta_title    text,
    meta_description text,
    json_ld       jsonb,                                 -- schema.org (GEO)
    keywords      jsonb not null default '[]'::jsonb,
    seo_score     jsonb,                                  -- sub-signals + total
    geo_score     jsonb,                                  -- sub-signals + total
    workflow_run_id text,                                 -- Powabase workflow run
    author_id     uuid references auth.users (id) on delete set null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create index if not exists articles_status_idx on public.articles (status);
create index if not exists articles_updated_at_idx on public.articles (updated_at desc);

-- ---------------------------------------------------------------------------
-- article_versions: snapshot on every save (Phase 7)
-- ---------------------------------------------------------------------------
create table if not exists public.article_versions (
    id          uuid primary key default gen_random_uuid(),
    article_id  uuid not null references public.articles (id) on delete cascade,
    content_md  text not null,
    created_by  uuid references auth.users (id) on delete set null,
    created_at  timestamptz not null default now()
);
create index if not exists article_versions_article_idx
    on public.article_versions (article_id, created_at desc);

-- ---------------------------------------------------------------------------
-- publish_targets + publications (Phase 8)
-- NOTE: do NOT store raw CMS credentials here — see PRD §7 open question.
-- ---------------------------------------------------------------------------
create table if not exists public.publish_targets (
    id          uuid primary key default gen_random_uuid(),
    target_type text not null check (target_type in ('export','wordpress','webflow','webhook')),
    name        text not null,
    config      jsonb not null default '{}'::jsonb,      -- non-secret config only
    created_at  timestamptz not null default now()
);

create table if not exists public.publications (
    id           uuid primary key default gen_random_uuid(),
    article_id   uuid not null references public.articles (id) on delete cascade,
    target_type  text not null check (target_type in ('export','wordpress','webflow','webhook')),
    target_id    uuid references public.publish_targets (id) on delete set null,
    external_id  text,
    url          text,
    status       text not null default 'pending'
                 check (status in ('pending','success','failed')),
    published_at timestamptz,
    created_at   timestamptz not null default now()
);
create index if not exists publications_article_idx on public.publications (article_id);

-- ---------------------------------------------------------------------------
-- RLS: enable on every app table; team-visible reads, author-attributed writes.
-- ---------------------------------------------------------------------------
alter table public.profiles          enable row level security;
alter table public.research_runs     enable row level security;
alter table public.briefs            enable row level security;
alter table public.articles          enable row level security;
alter table public.article_versions  enable row level security;
alter table public.publish_targets   enable row level security;
alter table public.publications      enable row level security;

-- Any authenticated team member can read everything in the workspace.
do $$
declare t text;
begin
    foreach t in array array[
        'profiles','research_runs','briefs','articles',
        'article_versions','publish_targets','publications'
    ] loop
        execute format(
            'create policy %I on public.%I for select to authenticated using (true)',
            t || '_read', t
        );
        -- Authenticated members can write; backend (service role) bypasses RLS.
        execute format(
            'create policy %I on public.%I for all to authenticated using (true) with check (true)',
            t || '_write', t
        );
    end loop;
end $$;

commit;
