-- ============================================================================
-- PREFLIGHT: does this project have what the migrations need?
--
-- Read-only. db/migrate.sh runs it BEFORE applying anything, so an installer on
-- an image without pg_cron or http finds out in the first second, from a
-- message naming both the extension and the fix, instead of watching twelve
-- migrations apply and the thirteenth die on `schema "cron" does not exist`
-- with 0013 rolled back and no worker.
--
-- Safe to run on its own at any time:
--   ./db/apply.sh db/setup/preflight.sql
--
-- Why availability and not just "is it installed": 0013 creates both
-- extensions itself. This only has to establish that CREATE EXTENSION has a
-- chance of succeeding. It cannot prove it will -- pg_cron additionally
-- requires shared_preload_libraries, which is not visible as a per-extension
-- fact -- so this is a fast, honest early filter, not a guarantee. 0013 keeps
-- its own guard for what is left.
-- ============================================================================
DO $preflight$
DECLARE
  v_missing text[] := '{}';
  v_http_schema text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron')
     AND NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_cron') THEN
    v_missing := array_append(v_missing, 'pg_cron (schedules the research worker; 0013)');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'http')
     AND NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'http') THEN
    v_missing := array_append(v_missing, 'http (the worker''s outbound call to the agent; 0013)');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'unaccent')
     AND NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'unaccent') THEN
    v_missing := array_append(v_missing, 'unaccent (name normalisation for dedupe; 0001)');
  END IF;

  IF cardinality(v_missing) > 0 THEN
    RAISE EXCEPTION 'powacrm preflight: this database is missing % required extension(s): %',
      cardinality(v_missing), array_to_string(v_missing, '; ')
      USING HINT = 'Enable them in Studio -> Database -> Extensions and run ./db/migrate.sh again. pg_cron additionally has to be in the server''s shared_preload_libraries. If you only want the phase-1 CRM and no AI research, apply db/migrations/0001..0012 individually with ./db/apply.sh and skip 0013 -- nothing before it needs pg_cron or http.';
  END IF;

  -- Only meaningful when http is ALREADY installed: 0013 creates it with
  -- SCHEMA public, but `CREATE EXTENSION IF NOT EXISTS ... SCHEMA public` does
  -- not relocate one that already exists somewhere else, and
  -- run_research_tick() pins search_path = public, pg_temp.
  SELECT extnamespace::regnamespace::text INTO v_http_schema FROM pg_extension WHERE extname = 'http';
  IF v_http_schema IS NOT NULL AND v_http_schema <> 'public' THEN
    RAISE EXCEPTION 'powacrm preflight: the http extension is installed in schema "%", but 0013''s worker needs it in "public"', v_http_schema
      USING HINT = 'run_research_tick() pins search_path = public, pg_temp, so http anywhere else fails at run time with "function http(http_request) does not exist". Fix it with: ALTER EXTENSION http SET SCHEMA public;';
  END IF;

  RAISE NOTICE 'powacrm preflight OK: pg_cron, http and unaccent are all installed or available.';
END $preflight$;
