-- The people _sel policy filters `deleted_at IS NULL`, and that USING clause
-- is evaluated against the NEW row on UPDATE (PostgreSQL re-checks the
-- SELECT-side USING policy for the post-update row), so an authenticated
-- client PATCHing deleted_at to a non-null value is rejected even though
-- the _upd policy's WITH CHECK is `true`. The SELECT policy must keep
-- hiding tombstones (spec: clients never see soft-deleted rows), so the fix
-- is a SECURITY DEFINER escape hatch scoped to exactly the one write the
-- policy can't otherwise allow -- not a relaxed policy.
--
-- Phase 1 only manages `people` as a soft-deletable entity from the UI, so
-- this function is scoped there; `companies`/`views` get their own version
-- of this when a later phase needs client-driven soft delete for them too.
CREATE OR REPLACE FUNCTION public.soft_delete_person(_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
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
