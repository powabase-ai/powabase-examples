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
