-- ============================================================================
-- PER-OWNER ISOLATION -- supersedes the trust boundary described in 0004_rls.sql.
--
-- The problem this fixes: 0004 granted `authenticated` full SELECT/INSERT/
-- UPDATE on all eight tables, so the ONLY check was "are you signed in". The
-- SPA ships the project's Anon key in its browser bundle, and the project
-- allows public signup with mailer autoconfirm on, so a stranger could
-- self-register, land in `authenticated`, and read and write every row --
-- everyone else's leads, companies and timelines included.
--
-- The fix is NOT to close signups. Public signup stays open, on purpose. What
-- changes is what an account gets you: your own data, and nothing else.
--
--   brands.owner_id  -- a brand belongs to exactly one auth.users row.
--   owns_brand(uuid) -- every other table hangs off a brand, so ownership of
--                       the brand is the whole authorization question.
--
-- A signup now gets a starter brand of its own (see section 6), so a new user
-- lands in a working, empty app rather than one that looks broken.
--
-- WHAT THIS IS NOT. This is single-owner isolation, not teams and not sharing.
-- There is exactly one owner per brand, no way to grant a second person access
-- to a brand, no roles, and no org layer. Anything collaborative is a later
-- phase that needs a real membership table -- which would replace `owns_brand`,
-- not sit beside it.
--
-- The two lookup tables (`stage_options`, `event_types`) stay readable by every
-- authenticated user. That is deliberate, not an oversight -- see section 4.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Brands get an owner.
-- ---------------------------------------------------------------------------
-- ON DELETE CASCADE: deleting the account deletes its brands, and every other
-- table already cascades from `brands`, so a deleted user leaves nothing behind.
ALTER TABLE public.brands
  ADD COLUMN IF NOT EXISTS owner_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;

-- Backfill, generically. No uuid and no email is hardcoded (this repo is
-- public), and it degenerates correctly in both directions: on an existing
-- project every brand goes to the account that has been there longest -- the
-- operator, on a project that has only ever had one login -- and on a fresh
-- project both tables are empty and this does nothing.
--
-- If a project somehow has brands but no accounts at all, this leaves owner_id
-- NULL and the NOT NULL below fails loudly. That is the right outcome: an
-- unowned brand under these policies is invisible to everyone, and silently
-- shipping one would be worse than refusing to migrate.
UPDATE public.brands
SET owner_id = (SELECT u.id FROM auth.users u ORDER BY u.created_at, u.id LIMIT 1)
WHERE owner_id IS NULL;

ALTER TABLE public.brands ALTER COLUMN owner_id SET NOT NULL;

-- So a client `POST /rest/v1/brands` is owned by whoever posted it without the
-- SPA having to say so. The INSERT policy still checks it -- the default is a
-- convenience, not the control; a caller can send any owner_id it likes and
-- the policy is what refuses it.
ALTER TABLE public.brands ALTER COLUMN owner_id SET DEFAULT auth.uid();

CREATE INDEX IF NOT EXISTS brands_owner_idx ON public.brands (owner_id);

COMMENT ON COLUMN public.brands.owner_id IS
  'The one account that can see this brand and everything hanging off it. Every RLS policy in this schema resolves to this column.';

-- ---------------------------------------------------------------------------
-- 2. The ownership predicate.
-- ---------------------------------------------------------------------------
-- SECURITY DEFINER for two reasons, both load-bearing:
--
--   1. It reads `brands`, and it is called FROM the policies on `companies`,
--      `people`, `views`, `events` and `import_batches`. As SECURITY INVOKER it
--      would re-enter the `brands` SELECT policy on every row of every one of
--      those queries -- policy evaluation inside policy evaluation.
--   2. Correctness must not depend on what the caller can see. Ownership is a
--      fact about the brand, not about the caller's current visibility of it.
--
-- `SET search_path = public, pg_temp` matches the hardening 0007/0008 applied:
-- with pg_temp unnamed it is searched FIRST for relations, so a caller able to
-- create `pg_temp.brands` could make the definer read that instead
-- (CVE-2018-1058). Naming it puts it last.
--
-- Soft-deleted brands still count as owned. Each table keeps its own
-- `deleted_at IS NULL` filter (or lack of one) exactly as 0004 had it, and
-- tombstoning a brand has never cascaded to its children -- making ownership
-- depend on the parent's tombstone would be a new behaviour smuggled in here.
--
-- auth.uid() is NULL for a session with no JWT (psql over the Database URL), so
-- this returns false there -- harmless, since such a session is a superuser
-- that bypasses RLS anyway.
CREATE OR REPLACE FUNCTION public.owns_brand(_brand_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.brands b
    WHERE b.id = _brand_id AND b.owner_id = auth.uid()
  );
$$;

REVOKE ALL ON FUNCTION public.owns_brand(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.owns_brand(uuid) TO authenticated;

-- ---------------------------------------------------------------------------
-- 3. Re-write every policy to resolve to ownership.
-- ---------------------------------------------------------------------------
-- Permissive policies OR together, so ONE surviving `USING (true)` would defeat
-- the entire migration. Rather than dropping 0004's 19 policies by name and
-- trusting that list to be complete, drop whatever is actually on these eight
-- tables, then create the scoped set. That is also what makes this migration
-- safe to re-run.
DO $$
DECLARE p record;
BEGIN
  FOR p IN
    SELECT policyname, tablename FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = ANY (ARRAY['brands','stage_options','companies','people',
                                 'event_types','events','import_batches','views'])
  LOOP
    EXECUTE format('DROP POLICY %I ON public.%I', p.policyname, p.tablename);
  END LOOP;
END $$;

-- RLS was enabled by 0004; re-assert it so this migration is self-contained.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['brands','stage_options','companies','people',
                           'event_types','events','import_batches','views'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- `brands` is the root, so it tests the column directly rather than calling
-- owns_brand() on itself. The INSERT check is what stops a signed-up user
-- planting a brand under someone else's id, and the UPDATE check -- `true` in
-- 0004 -- is what stops them handing one of their own brands to a stranger, or
-- taking one.
CREATE POLICY brands_sel ON public.brands
  FOR SELECT TO authenticated USING (owner_id = auth.uid() AND deleted_at IS NULL);
CREATE POLICY brands_ins ON public.brands
  FOR INSERT TO authenticated WITH CHECK (owner_id = auth.uid());
CREATE POLICY brands_upd ON public.brands
  FOR UPDATE TO authenticated USING (owner_id = auth.uid() AND deleted_at IS NULL)
  WITH CHECK (owner_id = auth.uid());

-- Soft-deletable children: SELECT still hides tombstones, and now also requires
-- owning the brand. The UPDATE policy's USING is re-checked against the NEW row
-- on the SELECT side, which is why direct PATCHes of deleted_at stay rejected
-- and soft_delete_person remains the sanctioned path (see 0005/0008).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['companies','people','views'] LOOP
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING (public.owns_brand(brand_id) AND deleted_at IS NULL)', t || '_sel', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT TO authenticated WITH CHECK (public.owns_brand(brand_id))', t || '_ins', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE TO authenticated USING (public.owns_brand(brand_id) AND deleted_at IS NULL) WITH CHECK (public.owns_brand(brand_id))', t || '_upd', t);
  END LOOP;
END $$;

-- Append-only timeline: SELECT + INSERT, still no UPDATE/DELETE for clients.
CREATE POLICY events_sel ON public.events FOR SELECT TO authenticated USING (public.owns_brand(brand_id));
CREATE POLICY events_ins ON public.events FOR INSERT TO authenticated WITH CHECK (public.owns_brand(brand_id));

CREATE POLICY import_batches_sel ON public.import_batches FOR SELECT TO authenticated USING (public.owns_brand(brand_id));
CREATE POLICY import_batches_ins ON public.import_batches FOR INSERT TO authenticated WITH CHECK (public.owns_brand(brand_id));
CREATE POLICY import_batches_upd ON public.import_batches FOR UPDATE TO authenticated USING (public.owns_brand(brand_id)) WITH CHECK (public.owns_brand(brand_id));

-- ---------------------------------------------------------------------------
-- 4. Lookup tables stay shared, ON PURPOSE.
-- ---------------------------------------------------------------------------
-- `stage_options` and `event_types` hold no user data: pipeline stage labels
-- with their colours and sort order, and event verbs with their icons. They are
-- seeded by 0002/0003, identical for everyone, and the UI cannot render a board
-- or a timeline without them. Scoping them per owner would mean copying five
-- rows into every signup for no privacy gain -- the contents are in this repo.
--
-- So: readable by any authenticated user, and writable by none (no INSERT,
-- UPDATE or DELETE policy exists for `authenticated` on either table, exactly
-- as in 0004). If a later phase lets a user define custom stages, THAT is when
-- these need a brand_id and the same owns_brand() treatment as everything else.
CREATE POLICY stage_options_sel ON public.stage_options FOR SELECT TO authenticated USING (true);
CREATE POLICY event_types_sel   ON public.event_types   FOR SELECT TO authenticated USING (true);

-- ---------------------------------------------------------------------------
-- 5. Close the SECURITY DEFINER back doors.
-- ---------------------------------------------------------------------------
-- Policies alone are not enough. `import_people` and `soft_delete_person` are
-- SECURITY DEFINER, owned by a superuser, and granted to `authenticated` --
-- they bypass RLS by construction. Without the guards below, a signed-in user
-- could pass someone else's `_brand_id` and write companies, people and events
-- straight into their workspace, or pass someone else's person id and delete
-- it, straight past every policy above. `import_people`'s own header has warned
-- about exactly this since 0006 ("`_brand_id` is taken from the caller as-is").
--
-- Both raise `insufficient_privilege` (42501), which PostgREST renders as a 403
-- with the message below -- the same shape as an RLS refusal, so a client sees
-- one consistent answer whichever path it took. They deliberately do NOT say
-- whether the id exists: "not yours" and "not real" are the same answer, or the
-- error becomes an existence oracle for other people's rows.
--
-- The guard is `auth.uid() IS NOT NULL AND NOT owns_brand(...)`, not a bare
-- `NOT owns_brand(...)`, because a session with no JWT at all (psql over the
-- Database URL -- how db/tests/test_0006 and test_0007 call these) is already a
-- superuser that bypasses RLS; refusing it would only break the tests without
-- protecting anything. Every HTTP caller that reaches these functions is
-- `authenticated`, and a GoTrue-issued token always carries `sub`.
CREATE OR REPLACE FUNCTION public.soft_delete_person(_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  affected_brand_id uuid;
  target_brand_id uuid;
  n int;
BEGIN
  -- Read the person's brand FIRST: the caller supplies a person id, so the
  -- ownership question is about that row's brand, not about anything passed in.
  SELECT brand_id INTO target_brand_id FROM people WHERE id = _id;

  IF auth.uid() IS NOT NULL AND NOT public.owns_brand(target_brand_id) THEN
    RAISE EXCEPTION 'not your record'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  UPDATE people
  SET deleted_at = now()
  WHERE id = _id AND deleted_at IS NULL
  RETURNING brand_id INTO affected_brand_id;

  GET DIAGNOSTICS n = ROW_COUNT;

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

-- `_import_id` is not checked separately: an import_batches row belongs to a
-- brand, and the only thing the function does with it is stamp counters on it.
-- The batch a caller can create in the first place is one in a brand it owns
-- (import_batches_ins), and a caller passing someone else's batch id alongside
-- its own brand can only mis-stamp counters on a row it could not read -- noise,
-- not disclosure. Worth tightening when import gets a real audit trail.
CREATE OR REPLACE FUNCTION public.import_people(_brand_id uuid, _import_id uuid, _rows jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  row_j jsonb; i int := 0;
  n_ins int := 0; n_res int := 0; n_skip int := 0; errs jsonb := '[]';
  v_email text; v_domain text; v_name text; v_company uuid; v_person uuid; v_deleted timestamptz;
  ctx jsonb;
BEGIN
  IF auth.uid() IS NOT NULL AND NOT public.owns_brand(_brand_id) THEN
    RAISE EXCEPTION 'not your brand'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  FOR row_j IN SELECT * FROM jsonb_array_elements(_rows) LOOP
    i := i + 1;
    IF jsonb_typeof(row_j) <> 'object' THEN
      errs := errs || jsonb_build_object('row', i, 'message', 'row is not a JSON object');
      CONTINUE;
    END IF;
    BEGIN
      v_email := nullif(trim(row_j->>'email'), '');
      v_domain := nullif(lower(trim(row_j->>'company_domain')), '');
      v_name := nullif(trim(row_j->>'company_name'), '');
      ctx := jsonb_build_object('import_id', _import_id, 'row', i);

      -- company: upsert-restore by (brand, domain) when a domain is present,
      -- else by (brand, lower(name)) among the brand's domain-less companies.
      v_company := NULL; v_deleted := NULL;
      IF v_domain IS NOT NULL THEN
        SELECT id, deleted_at INTO v_company, v_deleted FROM companies
          WHERE brand_id = _brand_id AND domain = v_domain;
        IF v_company IS NULL THEN
          INSERT INTO companies (brand_id, name, domain, created_by_source, created_by_name, created_by_context)
          VALUES (_brand_id, v_name, v_domain, 'IMPORT', 'CSV import', ctx)
          RETURNING id INTO v_company;
        ELSIF v_deleted IS NOT NULL THEN
          UPDATE companies SET deleted_at = NULL WHERE id = v_company;
        END IF;
      ELSIF v_name IS NOT NULL THEN
        SELECT id, deleted_at INTO v_company, v_deleted FROM companies
          WHERE brand_id = _brand_id AND domain IS NULL AND lower(name) = lower(v_name)
          ORDER BY created_at LIMIT 1;
        IF v_company IS NULL THEN
          INSERT INTO companies (brand_id, name, domain, created_by_source, created_by_name, created_by_context)
          VALUES (_brand_id, v_name, NULL, 'IMPORT', 'CSV import', ctx)
          RETURNING id INTO v_company;
        ELSIF v_deleted IS NOT NULL THEN
          UPDATE companies SET deleted_at = NULL WHERE id = v_company;
        END IF;
      END IF;

      -- person: dedupe by (brand, lower(email)); restore if soft-deleted; skip live dups
      v_person := NULL; v_deleted := NULL;
      IF v_email IS NOT NULL THEN
        SELECT id, deleted_at INTO v_person, v_deleted FROM people
          WHERE brand_id = _brand_id AND lower(email) = lower(v_email);
      END IF;
      IF v_person IS NOT NULL AND v_deleted IS NULL THEN
        n_skip := n_skip + 1;
      ELSIF v_person IS NOT NULL THEN
        UPDATE people SET deleted_at = NULL, company_id = coalesce(v_company, company_id) WHERE id = v_person;
        n_res := n_res + 1;
      ELSE
        INSERT INTO people (brand_id, company_id, first_name, last_name, title, email, linkedin_url,
                            created_by_source, created_by_name, created_by_context)
        VALUES (_brand_id, v_company,
                nullif(trim(row_j->>'first_name'), ''), nullif(trim(row_j->>'last_name'), ''),
                nullif(trim(row_j->>'title'), ''), v_email,
                nullif(trim(row_j->>'linkedin_url'), ''),
                'IMPORT', 'CSV import', ctx)
        RETURNING id INTO v_person;
        n_ins := n_ins + 1;
        INSERT INTO events (brand_id, person_id, company_id, event_type, actor_source, actor_name, properties)
        VALUES (_brand_id, v_person, v_company, 'import', 'IMPORT', 'CSV import', ctx);
      END IF;
    EXCEPTION WHEN OTHERS THEN
      errs := errs || jsonb_build_object('row', i, 'message', SQLERRM);
    END;
  END LOOP;

  UPDATE import_batches SET
    status = CASE
      WHEN jsonb_array_length(errs) > 0 AND n_ins = 0 AND n_res = 0 AND n_skip = 0 THEN 'failed'
      ELSE 'completed'
    END,
    inserted_count = n_ins, restored_count = n_res, skipped_count = n_skip, errors = errs
  WHERE id = _import_id;
  RETURN jsonb_build_object('inserted', n_ins, 'restored', n_res, 'skipped', n_skip, 'errors', errs);
END $$;

REVOKE ALL ON FUNCTION public.import_people(uuid, uuid, jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.import_people(uuid, uuid, jsonb) TO authenticated;

-- ---------------------------------------------------------------------------
-- 6. A new signup lands in a working app.
-- ---------------------------------------------------------------------------
-- Without this, a self-signed-up user owns no brand, so every table reads empty
-- and the SPA shows "No brands found -- did you run the seed?" -- a message
-- about the operator's setup, shown to someone who has no way to act on it. The
-- brand also cannot be created by the client on first load: `brands_ins` would
-- allow it, but "the app silently creates a tenant for you" is a worse place for
-- that rule to live than the database.
--
-- SECURITY DEFINER because GoTrue inserts as `supabase_auth_admin`, which holds
-- no privileges on public.*.
--
-- ON THE FAILURE ORDERING, which is the part worth getting right:
--
-- This is an AFTER INSERT trigger, so it runs inside GoTrue's own transaction.
-- If the INSERT below fails, the transaction rolls back, `auth.users` keeps no
-- row, and the signup returns an error. The user can retry. The state the
-- requirement warns about -- an account that exists but has no workspace -- is
-- therefore unreachable: account and workspace commit together or not at all.
--
-- That is why there is deliberately NO `EXCEPTION WHEN OTHERS THEN RETURN NEW`
-- here. Swallowing the error is the ONLY thing that could produce that broken
-- state, and it would produce it silently. A signup that fails loudly is
-- strictly better than an account that can never see anything.
--
-- The cost is that a bug here takes signup down with it, so the statement is
-- kept as small as it can be: one INSERT, no unique constraint to collide with
-- (brands.name is not unique, so every user can have a "My workspace"), no
-- lookups, nothing that can conflict with a concurrent signup.
CREATE OR REPLACE FUNCTION public.create_starter_brand()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  INSERT INTO public.brands (name, owner_id, created_by_source, created_by_name)
  VALUES ('My workspace', NEW.id, 'SYSTEM', 'Signup');
  RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.create_starter_brand() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS on_auth_user_created_starter_brand ON auth.users;
CREATE TRIGGER on_auth_user_created_starter_brand
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.create_starter_brand();

-- Existing accounts are NOT given a starter brand: they already own everything
-- the backfill in section 1 handed them, and manufacturing an empty second
-- workspace for them would be a surprise, not a fix.

-- The new column has to be in PostgREST's schema cache before a client can
-- select or insert it. Supabase ships a DDL event trigger that does this, but
-- say it explicitly so the migration does not depend on that being installed.
NOTIFY pgrst, 'reload schema';

COMMIT;
