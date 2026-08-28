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
  v_http_open int;
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
      USING HINT = 'Enable them in Studio -> Database -> Extensions and run ./db/migrate.sh again. That is the right command HERE because migrate.sh runs this file before it applies anything, so nothing has been created yet -- but if you are reading this from a standalone ./db/apply.sh db/setup/preflight.sql on a database that already has 0001-0012, apply the remaining files individually with ./db/apply.sh instead: migrate.sh starts at 0001 and 0002-0004 are bare CREATE TABLEs that abort on a database which already has them. pg_cron additionally has to be in the server''s shared_preload_libraries. If you only want the CRM and no AI research, apply every migration EXCEPT 0013 individually with ./db/apply.sh: 0001-0010 are the CRM, 0011/0012 add the research schema and its RPCs (harmless with no worker to run them), 0014 bounds the daily spend cap, and 0013 -- the pg_cron worker -- is the only file that needs pg_cron or http.';
  END IF;

  -- Only meaningful when http is ALREADY installed. 0013 wants it in
  -- `extensions` and NOT in `public`: `public` is PostgREST's exposed schema,
  -- Postgres grants EXECUTE on new functions to PUBLIC by default, and http_get
  -- / http_post / http_set_curlopt reachable with any signed-up account's JWT is
  -- an SSRF primitive against anything routable from the database host. This was
  -- live on the project this app was developed against; see 0013 section 0.
  --
  -- This is a NOTICE, not an exception, because 0013 fixes it: it relocates an
  -- existing http and then revokes every function the extension owns from
  -- PUBLIC, anon and authenticated. Failing here would block the migration that
  -- performs the repair. If 0013 cannot do the move (not superuser, not the
  -- extension's owner, or something in the database depends on an http object)
  -- it raises there, with the command to run by hand.
  --
  -- The relocation is DROP + CREATE, not ALTER EXTENSION ... SET SCHEMA:
  -- pgsql-http ships relocatable = false, so SET SCHEMA always errors with
  -- `extension "http" does not support SET SCHEMA`. Do not suggest it here or
  -- anywhere else -- an operator who follows that advice gets nothing but the
  -- error, and concludes the instructions are wrong rather than the command.
  SELECT extnamespace::regnamespace::text INTO v_http_schema FROM pg_extension WHERE extname = 'http';
  IF v_http_schema IS NOT NULL AND v_http_schema <> 'extensions' THEN
    RAISE NOTICE 'powacrm preflight: the http extension is installed in schema "%". 0013 will move it to "extensions" (by dropping and re-creating it -- pgsql-http does not support ALTER EXTENSION ... SET SCHEMA) and revoke its functions from PUBLIC, anon and authenticated; in "public" they are callable over PostgREST by every signed-up account (SSRF). If 0013 cannot move it, do it by hand as a superuser: DROP EXTENSION http; CREATE EXTENSION http SCHEMA extensions;', v_http_schema;
  END IF;

  -- Independent of placement: are the extension's functions client-callable
  -- right now? A project that ran an older 0013 has them in `public` AND
  -- granted; one that only ever had http installed by hand may have them
  -- granted in `extensions` too, since CREATE EXTENSION grants EXECUTE to
  -- PUBLIC wherever it lands. Reported here so an installer sees it before the
  -- migration, and asserted for good in db/tests/test_0013_worker.sql section 4.
  SELECT count(*) INTO v_http_open
    FROM pg_proc p
    JOIN pg_depend d ON d.objid = p.oid AND d.classid = 'pg_proc'::regclass AND d.deptype = 'e'
    JOIN pg_extension e ON e.oid = d.refobjid
   WHERE e.extname = 'http'
     AND (has_function_privilege('authenticated', p.oid, 'EXECUTE')
       OR has_function_privilege('anon', p.oid, 'EXECUTE'));
  IF v_http_open > 0 THEN
    RAISE NOTICE 'powacrm preflight: % http extension function(s) are currently executable by anon or authenticated. 0013 revokes them.', v_http_open;
  END IF;

  RAISE NOTICE 'powacrm preflight OK: pg_cron, http and unaccent are all installed or available.';
END $preflight$;
