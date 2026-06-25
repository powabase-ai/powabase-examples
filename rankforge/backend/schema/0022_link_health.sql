-- 0022 — link health / broken-link findings (M6 / Phase 12.3, the "fix broken
-- links" half).
--
-- The re-linking scout (and an on-demand check) validates each article's outbound
-- links: INTERNAL /p/{id} links must still point at a PUBLISHED article, and
-- EXTERNAL http(s) links must not 4xx/5xx or fail to resolve. Broken links are
-- surfaced for review (status 'open'); the editor fixes the prose and/or marks a
-- finding 'ignored'. We never auto-edit published content.
--
-- Apply: uv run python scripts/apply_schema.py schema/0022_link_health.sql

begin;

create table if not exists public.link_health (
    id           uuid primary key default gen_random_uuid(),
    business_id  uuid not null
                 references public.business_profiles (id) on delete cascade,
    article_id   uuid not null
                 references public.articles (id) on delete cascade,
    url          text not null,               -- the link target that was checked
    anchor_text  text,
    kind         text not null
                 check (kind in ('internal', 'external')),
    http_status  integer,                      -- status code, or null on no-response
    reason       text,                         -- short, UI-safe failure reason
    status       text not null default 'open'
                 check (status in ('open', 'ignored', 'resolved')),
    checked_at   timestamptz not null default now(),
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists link_health_article_idx
    on public.link_health (article_id, status);
create index if not exists link_health_business_idx
    on public.link_health (business_id, status);
-- One finding per (article, url) — re-checking updates it in place.
create unique index if not exists link_health_unique_idx
    on public.link_health (article_id, lower(url));

alter table public.link_health enable row level security;
drop policy if exists link_health_read on public.link_health;
create policy link_health_read on public.link_health for select to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()));
drop policy if exists link_health_write on public.link_health;
create policy link_health_write on public.link_health for all to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()))
    with check (business_id in (select id from public.business_profiles
                                where org_id = public.current_org_id()));

notify pgrst, 'reload schema';

commit;
