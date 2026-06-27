-- 0027 — record article takedowns in the publications audit trail.
-- unpublish() now writes a publications row (audit symmetry with publish()), so the
-- target_type and status CHECKs are widened to allow the takedown values. Both new
-- constraint sets are strict supersets of the old ones, so existing rows stay valid.
--
-- Apply: psql "$POWABASE_DATABASE_URL" -f backend/schema/0027_publications_unpublish.sql

begin;

alter table public.publications drop constraint if exists publications_target_type_check;
alter table public.publications add constraint publications_target_type_check
    check (target_type in
        ('export', 'wordpress', 'webflow', 'webhook', 'unpublish'));

alter table public.publications drop constraint if exists publications_status_check;
alter table public.publications add constraint publications_status_check
    check (status in ('pending', 'success', 'failed', 'unpublished'));

notify pgrst, 'reload schema';

commit;
