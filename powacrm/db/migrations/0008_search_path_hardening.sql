-- NOTE (added later): this file's header describes the SINGLE-TENANT model that
-- 0009_access_control.sql replaced. Data is now isolated per owner via
-- brands.owner_id / owns_brand(), and 0009 re-creates the function(s) defined
-- below with an ownership guard; 0010 then fixes a cross-tenant write in the
-- import batch UPDATE. Read 0009 and 0010 before trusting anything below about
-- who can see or write what.
--
-- Supersedes the function created in 0005_soft_delete.sql. Two changes, no
-- behaviour change:
--
-- 1. `SET search_path = public` becomes `SET search_path = public, pg_temp`.
--    The function is SECURITY DEFINER and owned by a superuser; with `pg_temp`
--    unnamed, PostgreSQL still searches the CALLER's temp schema first for
--    relations, so a caller who can create `pg_temp.people` / `pg_temp.events`
--    can make the definer read and write those instead (the CVE-2018-1058
--    shape). Naming `pg_temp` explicitly puts it LAST in the path, which is the
--    whole point -- an empty `search_path` would be safer still but would force
--    every reference to be schema-qualified. Not exploitable on this schema
--    (neither `anon` nor `authenticated` holds CREATE on `public`, and the only
--    role that reaches this function is `authenticated`), but this repo is an
--    example other people copy, so it should ship the correct pattern.
--
-- 2. The whole migration is wrapped in BEGIN/COMMIT. `apply.sh` runs statements
--    in autocommit, so 0005 left a window between CREATE OR REPLACE FUNCTION
--    and its REVOKE during which PUBLIC held EXECUTE -- the gap 0006's header
--    already called out for itself. 0005 is left byte-identical to what was
--    applied (applied migrations are immutable here); this file closes the gap
--    for anyone building the schema from scratch, since it runs last.
BEGIN;

CREATE OR REPLACE FUNCTION public.soft_delete_person(_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  affected_brand_id uuid;
  n int;
BEGIN
  UPDATE people
  SET deleted_at = now()
  WHERE id = _id AND deleted_at IS NULL
  RETURNING brand_id INTO affected_brand_id;

  GET DIAGNOSTICS n = ROW_COUNT;

  -- Record the deletion on the timeline, consistent with how every other
  -- field change is logged. Reusing 'field_updated' (already seeded) rather
  -- than adding a new 'deleted' event_type row keeps this a single,
  -- self-contained migration -- there's no per-type rendering logic yet
  -- that a dedicated type would need to hook into, and the diff shape below
  -- already carries everything the timeline needs to describe what changed.
  IF n > 0 THEN
    INSERT INTO events (brand_id, person_id, event_type, actor_source, properties)
    VALUES (
      affected_brand_id,
      _id,
      'field_updated',
      'MANUAL',
      jsonb_build_object('diff', jsonb_build_object(
        'deleted_at', jsonb_build_object('before', null, 'after', 'now')
      ))
    );
  END IF;

  RETURN n > 0;
END;
$$;

REVOKE ALL ON FUNCTION public.soft_delete_person(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.soft_delete_person(uuid) TO authenticated;

COMMIT;
