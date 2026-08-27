-- ============================================================================
-- TRUST BOUNDARY -- READ THIS BEFORE PUTTING REAL DATA IN A PROJECT USING THESE
-- POLICIES.
--
-- Phase 1 of PowaCRM is SINGLE-TENANT BY DESIGN. Every policy below grants the
-- `authenticated` role full SELECT/INSERT/UPDATE across all eight tables, with
-- no per-user, per-org or per-brand scoping. The only thing these policies
-- check is "are you logged in at all"; `WITH CHECK (true)` on the INSERT
-- policies means an authenticated client can write ANY `brand_id`.
--
-- That matters because the SPA ships the project's Anon key in its browser
-- bundle, so ANYONE who can load the app can reach GoTrue's signup endpoint. If
-- the project is left with signups enabled and mailer autoconfirm on (the
-- Powabase default), a stranger can self-register, be confirmed instantly, land
-- in `authenticated`, and read and write every row in this schema. RLS is the
-- only gate here, and it trusts any authenticated user.
--
-- So, before pointing a project holding real data at this schema:
--   1. Disable or gate signups in Studio (Authentication -> Providers/Settings:
--      turn off "Allow new users to sign up", or require email confirmation and
--      an invite flow), and create the operator account yourself -- see
--      `db/setup/create_user.sh`.
--   2. Treat the Database URL and Service Role key as server-side-only secrets.
--      The browser must only ever see the Anon key.
--   3. A multi-user or multi-tenant phase MUST replace these policies with ones
--      scoped against a membership table, and must scope `_brand_id` inside
--      `public.import_people` (0007_import_company_by_name.sql) the same way.
--
-- The README's "Security and the trust boundary" section says the same thing in
-- prose.
-- ============================================================================

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['brands','stage_options','companies','people','event_types','events','import_batches','views'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- Soft-deletable tables: SELECT hides tombstones; writes allowed to authenticated.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['brands','companies','people','views'] LOOP
    EXECUTE format('CREATE POLICY %I_sel ON %I FOR SELECT TO authenticated USING (deleted_at IS NULL)', t, t);
    EXECUTE format('CREATE POLICY %I_ins ON %I FOR INSERT TO authenticated WITH CHECK (true)', t, t);
    EXECUTE format('CREATE POLICY %I_upd ON %I FOR UPDATE TO authenticated USING (deleted_at IS NULL) WITH CHECK (true)', t, t);
  END LOOP;
END $$;

-- Append-only / lookup tables.
CREATE POLICY stage_options_sel ON stage_options FOR SELECT TO authenticated USING (true);
CREATE POLICY event_types_sel ON event_types FOR SELECT TO authenticated USING (true);
CREATE POLICY events_sel ON events FOR SELECT TO authenticated USING (true);
CREATE POLICY events_ins ON events FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY import_batches_sel ON import_batches FOR SELECT TO authenticated USING (true);
CREATE POLICY import_batches_ins ON import_batches FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY import_batches_upd ON import_batches FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
