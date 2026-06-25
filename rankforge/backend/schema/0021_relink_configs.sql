-- 0021 — monthly re-linking maintenance schedule (M6 / Phase 12.3).
--
-- A per-brand schedule for the re-linking scout: it periodically re-runs the
-- internal-link suggester across the brand's published library so links get woven
-- between NEW and OLD content as the blog grows, staging suggestions for review
-- (it never edits published content directly). Mirrors scout_configs' shape; the
-- existing APScheduler tick drives it.
--
-- Apply: uv run python scripts/apply_schema.py schema/0021_relink_configs.sql

begin;

create table if not exists public.relink_configs (
    business_id  uuid primary key
                 references public.business_profiles (id) on delete cascade,
    enabled      boolean not null default false,
    cadence      text not null default 'monthly'
                 check (cadence in ('weekly', 'monthly')),
    last_run_at  timestamptz,
    next_run_at  timestamptz,
    last_found   integer not null default 0,   -- suggestions surfaced on the last run
    updated_at   timestamptz not null default now()
);

alter table public.relink_configs enable row level security;
drop policy if exists relink_configs_read on public.relink_configs;
create policy relink_configs_read on public.relink_configs for select to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()));
drop policy if exists relink_configs_write on public.relink_configs;
create policy relink_configs_write on public.relink_configs for all to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()))
    with check (business_id in (select id from public.business_profiles
                                where org_id = public.current_org_id()));

notify pgrst, 'reload schema';

commit;
