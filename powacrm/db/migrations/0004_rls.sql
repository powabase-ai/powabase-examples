-- ============================================================================
-- SUPERSEDED BY 0009_access_control.sql -- READ THAT FILE FIRST.
--
-- The policies created below grant the `authenticated` role full
-- SELECT/INSERT/UPDATE across all eight tables with NO per-user scoping: the
-- only thing they check is "are you logged in at all". Because the SPA ships
-- the project's Anon key in its browser bundle and public signup is open, that
-- meant any stranger who loaded the app could self-register and then read and
-- write every row in the schema -- everyone else's leads included.
--
-- 0009 replaces every policy in this file with per-owner ones built on
-- `brands.owner_id` and `owns_brand(uuid)`. This file is kept unchanged because
-- applied migrations are history, not a place to edit; a from-scratch build
-- passes through these permissive policies for the few seconds between 0004 and
-- 0009, on a database that has no users yet.
--
-- Do not copy the policy shape below into anything real. Copy 0009's.
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
