-- 0011 — multi-org tenancy (HARD isolation).
--
-- Until now the workspace was a single shared team: any authenticated user could
-- read/write every brand and article (RLS policies were `using (true)`). This
-- migration makes `organizations` the tenant boundary:
--   * every profile belongs to exactly one org;
--   * every business_profile belongs to one org;
--   * all content (research_runs, briefs, articles, scouts, …) inherits its org
--     transitively through `business_id`.
--
-- Existing data is backfilled into a single "Default Workspace" org so the current
-- install keeps working after apply.
--
-- Enforcement is two-layer:
--   1. APPLICATION layer (primary): the backend connects as the RLS-bypassing
--      owner role, so it scopes every query/route to the caller's org itself
--      (auth.require_member / org-scoped service queries).
--   2. RLS (defense-in-depth): for any direct PostgREST access with the
--      authenticated key, the policies below restrict every row to the caller's
--      own org via public.current_org_id().
--
-- Apply: uv run python scripts/apply_schema.py schema/0011_multi_org.sql

begin;

-- ---------------------------------------------------------------------------
-- organizations: the tenant boundary
-- ---------------------------------------------------------------------------
create table if not exists public.organizations (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    created_at  timestamptz not null default now()
);

alter table public.profiles
    add column if not exists org_id uuid
        references public.organizations (id) on delete cascade;
alter table public.business_profiles
    add column if not exists org_id uuid
        references public.organizations (id) on delete cascade;

create index if not exists profiles_org_idx on public.profiles (org_id);
create index if not exists business_profiles_org_idx
    on public.business_profiles (org_id);

-- ---------------------------------------------------------------------------
-- org_invites: an admin adds a teammate by email before they first sign in.
-- On first sign-in, a pending invite (matched case-insensitively on email) joins
-- the user to that org with the invited role instead of creating a new org.
-- ---------------------------------------------------------------------------
create table if not exists public.org_invites (
    id          uuid primary key default gen_random_uuid(),
    org_id      uuid not null references public.organizations (id) on delete cascade,
    email       text not null,
    role        text not null default 'writer'
                check (role in ('writer', 'editor', 'admin')),
    invited_by  uuid references auth.users (id) on delete set null,
    created_at  timestamptz not null default now(),
    accepted_at timestamptz
);
-- At most one pending invite per email across the whole install.
create unique index if not exists org_invites_pending_email_idx
    on public.org_invites (lower(email)) where accepted_at is null;

-- ---------------------------------------------------------------------------
-- Backfill: fold all existing rows into one default org.
-- ---------------------------------------------------------------------------
do $$
declare default_org uuid;
begin
    if exists (select 1 from public.profiles where org_id is null)
       or exists (select 1 from public.business_profiles where org_id is null) then
        -- Reuse an org if 0011 partially applied before; else create one.
        select id into default_org from public.organizations
            order by created_at limit 1;
        if default_org is null then
            insert into public.organizations (name)
                values ('Default Workspace') returning id into default_org;
        end if;
        update public.profiles set org_id = default_org where org_id is null;
        update public.business_profiles set org_id = default_org where org_id is null;
    end if;
end $$;

-- ---------------------------------------------------------------------------
-- current_org_id(): the caller's org, resolved bypassing RLS (SECURITY DEFINER)
-- so the org-scoped policies below can reference profiles without recursing.
-- auth.uid() is the GoTrue/Supabase JWT subject.
-- ---------------------------------------------------------------------------
create or replace function public.current_org_id() returns uuid
    language sql stable security definer set search_path = public as $$
    select org_id from public.profiles where id = auth.uid()
$$;

-- ---------------------------------------------------------------------------
-- RLS: replace the permissive `using (true)` policies with org-scoped ones.
-- Org-anchored tables filter on org_id; content tables filter through
-- business_id; child tables filter through their parent.
-- ---------------------------------------------------------------------------
alter table public.organizations enable row level security;
alter table public.org_invites   enable row level security;

do $$
declare
    -- table -> the boolean predicate that scopes a row to the caller's org
    rec record;
    -- (table_name, scope_predicate)
    defs text[][] := array[
        -- org-anchored
        ['organizations',     'id = public.current_org_id()'],
        ['org_invites',       'org_id = public.current_org_id()'],
        ['profiles',          'org_id = public.current_org_id()'],
        ['business_profiles', 'org_id = public.current_org_id()'],
        -- content anchored directly on business_id
        ['research_runs',     'business_id in (select id from public.business_profiles where org_id = public.current_org_id())'],
        ['briefs',            'business_id in (select id from public.business_profiles where org_id = public.current_org_id())'],
        ['articles',          'business_id in (select id from public.business_profiles where org_id = public.current_org_id())'],
        ['scout_configs',     'business_id in (select id from public.business_profiles where org_id = public.current_org_id())'],
        ['scout_runs',        'business_id in (select id from public.business_profiles where org_id = public.current_org_id())'],
        ['opportunities',     'business_id in (select id from public.business_profiles where org_id = public.current_org_id())'],
        -- child tables anchored through their parent
        ['research_sources',  'research_run_id in (select id from public.research_runs where business_id in (select id from public.business_profiles where org_id = public.current_org_id()))'],
        ['article_versions',  'article_id in (select id from public.articles where business_id in (select id from public.business_profiles where org_id = public.current_org_id()))'],
        ['article_comments',  'article_id in (select id from public.articles where business_id in (select id from public.business_profiles where org_id = public.current_org_id()))'],
        ['publications',      'article_id in (select id from public.articles where business_id in (select id from public.business_profiles where org_id = public.current_org_id()))']
    ];
    i int;
    tname text;
    pred text;
begin
    for i in 1 .. array_length(defs, 1) loop
        tname := defs[i][1];
        pred  := defs[i][2];
        execute format('drop policy if exists %I on public.%I', tname || '_read', tname);
        execute format('drop policy if exists %I on public.%I', tname || '_write', tname);
        execute format(
            'create policy %I on public.%I for select to authenticated using (%s)',
            tname || '_read', tname, pred
        );
        execute format(
            'create policy %I on public.%I for all to authenticated using (%s) with check (%s)',
            tname || '_write', tname, pred, pred
        );
    end loop;
end $$;

notify pgrst, 'reload schema';

commit;
