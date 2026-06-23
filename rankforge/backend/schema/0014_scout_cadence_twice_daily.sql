-- 0014 — add a 'twice_daily' scout cadence.
--
-- Scout cadence was daily | weekly (0008). Add twice_daily (every 12h). "Off" is
-- not a cadence value — it's `enabled = false` (the scheduler skips disabled
-- configs; manual "Run now" still works).
--
-- Apply: uv run python scripts/apply_schema.py schema/0014_scout_cadence_twice_daily.sql

begin;

alter table public.scout_configs
    drop constraint if exists scout_configs_cadence_check;
alter table public.scout_configs
    add constraint scout_configs_cadence_check
    check (cadence in ('twice_daily', 'daily', 'weekly'));

notify pgrst, 'reload schema';

commit;
