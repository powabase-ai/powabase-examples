-- Idempotent by construction: brands.name has no unique constraint, so a bare
-- `ON CONFLICT DO NOTHING` (no arbiter index/constraint to match) would not
-- stop a second run from inserting a duplicate 'gpt-trainer' row. Guard with
-- an explicit existence check instead -- safe for this single-writer,
-- sequential seed script (no concurrent-insert race to worry about).
--
-- Since 0009_access_control.sql a brand needs an owner -- `brands.owner_id` is
-- NOT NULL, and a brand nobody owns is a brand nobody can see. This script runs
-- as a superuser over the Database URL, so there is no `auth.uid()` to default
-- from; the demo brand goes to the project's first account, which is why the
-- login has to exist before the seed. README's setup section runs them in that
-- order. The explicit check beats letting the NOT NULL fire, which would say
-- only "null value in column owner_id violates not-null constraint".
DO $$
DECLARE v_owner uuid;
BEGIN
  SELECT id INTO v_owner FROM auth.users ORDER BY created_at, id LIMIT 1;
  IF v_owner IS NULL THEN
    RAISE EXCEPTION 'this project has no accounts yet, so the demo brand would have no owner and nobody could see it -- run ./db/setup/create_user.sh first, then re-run this seed';
  END IF;

  INSERT INTO brands (name, product_description, voice_notes, owner_id)
  SELECT 'gpt-trainer',
         'gpt-trainer: no-code platform to build, train, and deploy custom AI chatbots and agents on your own data.',
         'Founder voice: direct, technical, helpful. No competitor bashing. No hype-chains.',
         v_owner
  WHERE NOT EXISTS (SELECT 1 FROM brands WHERE name = 'gpt-trainer');
END $$;
