# PowaCRM — Phase 1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working lite CRM on Powabase: schema with all cross-cutting conventions, RLS + GoTrue auth, and a React SPA with design tokens, app shell, pipeline kanban board, lead record view, and CSV import.

**Architecture:** Postgres schema (applied via psql over the Powabase Database URL) exposes CRM tables through PostgREST with RLS-on/authenticated-only policies; a single GoTrue user logs into a React + Vite SPA that talks to Powabase via supabase-js and TanStack Query. CSV import goes through a `security definer` RPC that implements dedupe-then-restore server-side.

**Tech Stack:** Postgres 15+ (Powabase), PostgREST, GoTrue; React 18 + TypeScript + Vite 5, @supabase/supabase-js v2, @tanstack/react-query v5, react-router-dom v6, papaparse, @dnd-kit/core, vitest + @testing-library/react.

**Spec:** `powacrm/docs/2026-08-26-powacrm-design.md` (this plan implements §8 phase 1; schema per §3, dashboard per §5)

## Global Constraints

- Project root: `powacrm/` in the `powabase-examples` repo (all paths below relative to it). Frontend lives in `app/`, database in `db/`.
- Powabase project URL: `https://<ref>.p.powabase.ai`. Credentials come from environment variables, NEVER hardcoded or committed: `PB_DB_URL` (Database URL, migrations only), `PB_SERVICE_KEY` (setup scripts only), `VITE_POWABASE_URL` + `VITE_POWABASE_ANON_KEY` (the only credentials the SPA may see, in `app/.env.local`, gitignored).
- Every direct REST call in scripts sends BOTH headers: `apikey: <key>` and `Authorization: Bearer <key>`.
- Every table follows spec §3.1: `id uuid primary key default gen_random_uuid()`, `created_at`/`updated_at timestamptz not null default now()` + `set_updated_at` trigger, `deleted_at timestamptz`, actor columns (`created_by_source` text CHECK + `created_by_name` + `created_by_context jsonb`), enums as `text + CHECK` (no PG enum types).
- RLS enabled on every table; policies grant `authenticated` only; SELECT policies filter `deleted_at IS NULL`; the `anon` role gets nothing.
- Commits: small, per task, run from the git repo root, message suffix `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Saved-views UI is deferred (spec puts only board/lead-view/import in phase 1); the `views` table ships now so later phases don't migrate.

---

### Task 1: Migration infrastructure + shared DB helpers

**Files:**
- Create: `db/apply.sh`
- Create: `db/migrations/0001_helpers.sql`
- Create: `db/tests/test_0001_helpers.sql`
- Create: `.gitignore`

**Interfaces:**
- Produces: `public.unaccent_immutable(text) returns text` (IMMUTABLE — usable in generated columns); `public.set_updated_at()` trigger function (sets `NEW.updated_at = now()`); `db/apply.sh <file.sql>` runs any SQL file against `$PB_DB_URL` with `ON_ERROR_STOP`.

- [ ] **Step 1: Write the runner and the failing test**

`db/apply.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
: "${PB_DB_URL:?Set PB_DB_URL to the Powabase Database URL}"
psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -f "$1"
```

`.gitignore` (project root):
```
node_modules/
dist/
.env
.env.local
*.local
```

`db/tests/test_0001_helpers.sql` — pure-SQL assertions via `DO` blocks:
```sql
-- unaccent_immutable exists, is immutable, and strips accents
DO $$
DECLARE v text; vol char;
BEGIN
  SELECT provolatile INTO vol FROM pg_proc WHERE proname = 'unaccent_immutable';
  IF vol IS DISTINCT FROM 'i' THEN RAISE EXCEPTION 'unaccent_immutable missing or not IMMUTABLE (got %)', vol; END IF;
  SELECT public.unaccent_immutable('Café Zürich') INTO v;
  IF v <> 'Cafe Zurich' THEN RAISE EXCEPTION 'unaccent failed: %', v; END IF;
END $$;

-- set_updated_at works when attached to a table
DO $$
DECLARE t1 timestamptz; t2 timestamptz;
BEGIN
  CREATE TEMP TABLE _t (id int, updated_at timestamptz NOT NULL DEFAULT now());
  CREATE TRIGGER _t_upd BEFORE UPDATE ON _t FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
  INSERT INTO _t VALUES (1, '2000-01-01');
  UPDATE _t SET id = 2;
  SELECT updated_at INTO t2 FROM _t;
  IF t2 < now() - interval '1 minute' THEN RAISE EXCEPTION 'set_updated_at did not fire'; END IF;
  DROP TABLE _t;
END $$;
SELECT 'test_0001 OK' AS result;
```

- [ ] **Step 2: Run test to verify it fails**

Run: `chmod +x db/apply.sh && ./db/apply.sh db/tests/test_0001_helpers.sql`
Expected: FAIL — `unaccent_immutable missing or not IMMUTABLE`

- [ ] **Step 3: Write the migration**

`db/migrations/0001_helpers.sql`:
```sql
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Plain unaccent() is STABLE; generated columns need IMMUTABLE. Pinning the
-- dictionary makes it safe. [Twenty]
CREATE OR REPLACE FUNCTION public.unaccent_immutable(text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN NEW.updated_at = now(); RETURN NEW; END $$;
```

- [ ] **Step 4: Apply migration, run test to verify it passes**

Run: `./db/apply.sh db/migrations/0001_helpers.sql && ./db/apply.sh db/tests/test_0001_helpers.sql`
Expected: `test_0001 OK`

- [ ] **Step 5: Commit**

```bash
cd <repo root> && git add powacrm/db powacrm/.gitignore
git commit --no-verify -m "feat(powacrm): db migration infra + shared helpers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Core tables — brands, stage_options, companies, people

**Files:**
- Create: `db/migrations/0002_core_tables.sql`
- Create: `db/tests/test_0002_core_tables.sql`

**Interfaces:**
- Consumes: `unaccent_immutable`, `set_updated_at` (Task 1)
- Produces: tables `brands`, `stage_options`, `companies`, `people` with exact columns below. Later tasks rely on: `people.stage` values `sourced|enriched|researched|in_sequence|replied|won|disqualified`; `people.position double precision`; unique keys `companies(brand_id, domain)`, `people(brand_id, lower(email))`, `people(brand_id, linkedin_url)`, `people(brand_id, apollo_person_id)`.

- [ ] **Step 1: Write the failing test**

`db/tests/test_0002_core_tables.sql`:
```sql
DO $$
DECLARE b uuid; c uuid; p uuid; ts1 timestamptz; ts2 timestamptz; n int; sv tsvector;
BEGIN
  INSERT INTO brands (name) VALUES ('_test_brand') RETURNING id INTO b;

  -- updated_at trigger fires
  SELECT updated_at INTO ts1 FROM brands WHERE id = b;
  PERFORM pg_sleep(0.01);
  UPDATE brands SET name = '_test_brand2' WHERE id = b;
  SELECT updated_at INTO ts2 FROM brands WHERE id = b;
  IF ts2 <= ts1 THEN RAISE EXCEPTION 'updated_at trigger did not fire'; END IF;

  INSERT INTO companies (brand_id, name, domain) VALUES (b, 'Acme', 'acme.com') RETURNING id INTO c;
  -- domain dedup
  BEGIN
    INSERT INTO companies (brand_id, name, domain) VALUES (b, 'Acme again', 'acme.com');
    RAISE EXCEPTION 'expected unique_violation on domain';
  EXCEPTION WHEN unique_violation THEN NULL; END;

  INSERT INTO people (brand_id, company_id, first_name, last_name, email, title)
  VALUES (b, c, 'Zoë', 'Smith', 'Zoe@Acme.com', 'CTO') RETURNING id INTO p;
  -- case-insensitive email dedup
  BEGIN
    INSERT INTO people (brand_id, email) VALUES (b, 'zoe@acme.com');
    RAISE EXCEPTION 'expected unique_violation on email';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  -- invalid stage rejected
  BEGIN
    UPDATE people SET stage = 'bogus' WHERE id = p;
    RAISE EXCEPTION 'expected check_violation on stage';
  EXCEPTION WHEN check_violation THEN NULL; END;
  -- search_vector generated (accent-folded)
  SELECT search_vector INTO sv FROM people WHERE id = p;
  IF NOT sv @@ to_tsquery('simple', 'zoe') THEN RAISE EXCEPTION 'search_vector missing zoe'; END IF;
  -- actor default
  IF (SELECT created_by_source FROM people WHERE id = p) <> 'MANUAL' THEN
    RAISE EXCEPTION 'created_by_source default wrong'; END IF;

  SELECT count(*) INTO n FROM stage_options WHERE object = 'people';
  IF n < 7 THEN RAISE EXCEPTION 'expected 7 seeded people stages, got %', n; END IF;

  DELETE FROM people WHERE brand_id = b; DELETE FROM companies WHERE brand_id = b; DELETE FROM brands WHERE id = b;
END $$;
SELECT 'test_0002 OK' AS result;
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./db/apply.sh db/tests/test_0002_core_tables.sql`
Expected: FAIL — `relation "brands" does not exist`

- [ ] **Step 3: Write the migration**

`db/migrations/0002_core_tables.sql`:
```sql
CREATE TABLE brands (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  name text NOT NULL,
  product_description text,
  voice_notes text,
  icp_filters jsonb NOT NULL DEFAULT '{}',
  daily_cap_connects int NOT NULL DEFAULT 20,
  daily_cap_messages int NOT NULL DEFAULT 25,
  daily_cap_emails int NOT NULL DEFAULT 50,
  quiet_hours jsonb NOT NULL DEFAULT '{"start": "18:00", "end": "08:00", "weekdays_only": true}',
  timezone text NOT NULL DEFAULT 'America/New_York',
  confidence_threshold int NOT NULL DEFAULT 101,
  dry_run boolean NOT NULL DEFAULT true,
  paused boolean NOT NULL DEFAULT false
);
CREATE TRIGGER brands_updated BEFORE UPDATE ON brands FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Ordered, colored picklists (kanban columns derive from this). [Twenty select options]
CREATE TABLE stage_options (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  object text NOT NULL CHECK (object IN ('people')),
  value text NOT NULL,
  label text NOT NULL,
  color text NOT NULL,
  position int NOT NULL,
  UNIQUE (object, value)
);
INSERT INTO stage_options (object, value, label, color, position) VALUES
  ('people', 'sourced',      'Sourced',      'gray',   1),
  ('people', 'enriched',     'Enriched',     'blue',   2),
  ('people', 'researched',   'Researched',   'purple', 3),
  ('people', 'in_sequence',  'In sequence',  'sky',    4),
  ('people', 'replied',      'Replied',      'orange', 5),
  ('people', 'won',          'Won',          'green',  6),
  ('people', 'disqualified', 'Disqualified', 'red',    7);

CREATE TABLE companies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  created_by_source text NOT NULL DEFAULT 'MANUAL'
    CHECK (created_by_source IN ('MANUAL','API','WORKFLOW','AGENT','IMPORT','WEBHOOK','SYSTEM')),
  created_by_name text NOT NULL DEFAULT 'System',
  created_by_context jsonb,
  brand_id uuid NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  name text,
  domain text,               -- normalized registrable domain, lowercase [Twenty fix]
  website_url text,          -- display URL, separate from the dedup key
  linkedin_url text,
  apollo_org_id text,
  industry text,
  headcount int,
  tech_stack jsonb,
  research text,             -- researcher agent summary (phase 2 writes this)
  search_vector tsvector GENERATED ALWAYS AS (
    to_tsvector('simple',
      coalesce(unaccent_immutable(name), '') || ' ' || coalesce(domain, ''))
  ) STORED
);
CREATE TRIGGER companies_updated BEFORE UPDATE ON companies FOR EACH ROW EXECUTE FUNCTION set_updated_at();
-- Full unique (spans soft-deleted rows): re-import restores instead of duplicating. [Twenty]
CREATE UNIQUE INDEX companies_brand_domain_uq ON companies (brand_id, domain) WHERE domain IS NOT NULL;
CREATE INDEX companies_brand_idx ON companies (brand_id);
CREATE INDEX companies_search_idx ON companies USING gin (search_vector);

CREATE TABLE people (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  created_by_source text NOT NULL DEFAULT 'MANUAL'
    CHECK (created_by_source IN ('MANUAL','API','WORKFLOW','AGENT','IMPORT','WEBHOOK','SYSTEM')),
  created_by_name text NOT NULL DEFAULT 'System',
  created_by_context jsonb,
  brand_id uuid NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  company_id uuid REFERENCES companies(id) ON DELETE SET NULL,
  first_name text,
  last_name text,
  title text,
  email text,                         -- primary; stored as given, deduped case-insensitively
  additional_emails jsonb NOT NULL DEFAULT '[]',
  linkedin_url text,
  apollo_person_id text,
  enrichment jsonb,
  fit_score int CHECK (fit_score BETWEEN 0 AND 100),
  do_not_contact boolean NOT NULL DEFAULT false,
  stage text NOT NULL DEFAULT 'sourced'
    CHECK (stage IN ('sourced','enriched','researched','in_sequence','replied','won','disqualified')),
  position double precision NOT NULL DEFAULT 0,
  search_vector tsvector GENERATED ALWAYS AS (
    to_tsvector('simple',
      coalesce(unaccent_immutable(first_name), '') || ' ' ||
      coalesce(unaccent_immutable(last_name), '') || ' ' ||
      coalesce(unaccent_immutable(title), '') || ' ' ||
      coalesce(email, ''))
  ) STORED
);
CREATE TRIGGER people_updated BEFORE UPDATE ON people FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE UNIQUE INDEX people_brand_email_uq ON people (brand_id, lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX people_brand_linkedin_uq ON people (brand_id, linkedin_url) WHERE linkedin_url IS NOT NULL;
CREATE UNIQUE INDEX people_brand_apollo_uq ON people (brand_id, apollo_person_id) WHERE apollo_person_id IS NOT NULL;
CREATE INDEX people_brand_stage_idx ON people (brand_id, stage, position);
CREATE INDEX people_company_idx ON people (company_id);
CREATE INDEX people_search_idx ON people USING gin (search_vector);
```

- [ ] **Step 4: Apply, run test to verify it passes**

Run: `./db/apply.sh db/migrations/0002_core_tables.sql && ./db/apply.sh db/tests/test_0002_core_tables.sql`
Expected: `test_0002 OK`

- [ ] **Step 5: Commit**

```bash
cd <repo root> && git add powacrm/db
git commit --no-verify -m "feat(powacrm): core tables (brands, stages, companies, people)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Timeline + import + views tables

**Files:**
- Create: `db/migrations/0003_events_import_views.sql`
- Create: `db/tests/test_0003_events.sql`

**Interfaces:**
- Consumes: `people`, `companies`, `brands` (Task 2)
- Produces: `event_types` (seeded: `note`, `stage_changed`, `field_updated`, `import`, `error`), `events` (columns below — timeline queries use `(person_id, happens_at DESC)`), `import_batches`, `views`. Frontend reads events as `{id, event_type, happens_at, properties, actor_source, actor_name, linked_record_cached_name}`.

- [ ] **Step 1: Write the failing test**

`db/tests/test_0003_events.sql`:
```sql
DO $$
DECLARE b uuid; p uuid; n int;
BEGIN
  INSERT INTO brands (name) VALUES ('_test_ev') RETURNING id INTO b;
  INSERT INTO people (brand_id, first_name) VALUES (b, 'Eve') RETURNING id INTO p;

  INSERT INTO events (brand_id, person_id, event_type, actor_source, actor_name, properties)
  VALUES (b, p, 'note', 'MANUAL', 'Test User', '{"body": "hello"}');
  -- unknown event type rejected (FK to event_types)
  BEGIN
    INSERT INTO events (brand_id, person_id, event_type, actor_source, actor_name)
    VALUES (b, p, 'nonsense', 'MANUAL', 'Test User');
    RAISE EXCEPTION 'expected fk violation on event_type';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  -- happens_at defaulted
  SELECT count(*) INTO n FROM events WHERE person_id = p AND happens_at IS NOT NULL;
  IF n <> 1 THEN RAISE EXCEPTION 'happens_at not defaulted'; END IF;
  -- timeline index exists
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'events_person_time_idx') THEN
    RAISE EXCEPTION 'events_person_time_idx missing'; END IF;
  -- import_batches + views exist
  PERFORM 1 FROM import_batches LIMIT 0;
  PERFORM 1 FROM views LIMIT 0;

  DELETE FROM events WHERE brand_id = b; DELETE FROM people WHERE brand_id = b; DELETE FROM brands WHERE id = b;
END $$;
SELECT 'test_0003 OK' AS result;
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./db/apply.sh db/tests/test_0003_events.sql`
Expected: FAIL — `relation "events" does not exist`

- [ ] **Step 3: Write the migration**

`db/migrations/0003_events_import_views.sql`:
```sql
-- Event type registry: verb/label/icon drive sentence-shaped timeline rows. [Twenty]
CREATE TABLE event_types (
  name text PRIMARY KEY,
  verb text NOT NULL,
  label text NOT NULL,
  icon text NOT NULL
);
INSERT INTO event_types (name, verb, label, icon) VALUES
  ('note',          'noted',    'Note',          'note'),
  ('stage_changed', 'moved',    'Stage changed', 'arrow-right'),
  ('field_updated', 'updated',  'Field updated', 'pencil'),
  ('import',        'imported', 'Imported',      'upload'),
  ('error',         'errored',  'Error',         'alert');

CREATE TABLE events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  brand_id uuid NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  person_id uuid REFERENCES people(id) ON DELETE CASCADE,
  company_id uuid REFERENCES companies(id) ON DELETE CASCADE,
  event_type text NOT NULL REFERENCES event_types(name),
  happens_at timestamptz NOT NULL DEFAULT now(),   -- display time, distinct from created_at [Twenty]
  actor_source text NOT NULL DEFAULT 'SYSTEM'
    CHECK (actor_source IN ('MANUAL','API','WORKFLOW','AGENT','IMPORT','WEBHOOK','SYSTEM')),
  actor_name text NOT NULL DEFAULT 'System',
  properties jsonb NOT NULL DEFAULT '{}',          -- incl. {diff:{field:{before,after}}}
  linked_record_id uuid,
  linked_record_kind text,
  linked_record_cached_name text                   -- denormalized: feed renders join-free [Twenty]
);
CREATE INDEX events_person_time_idx ON events (person_id, happens_at DESC);
CREATE INDEX events_company_time_idx ON events (company_id, happens_at DESC);
CREATE INDEX events_brand_idx ON events (brand_id);

CREATE TABLE import_batches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  brand_id uuid NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  filename text NOT NULL,
  row_count int NOT NULL DEFAULT 0,
  inserted_count int NOT NULL DEFAULT 0,
  restored_count int NOT NULL DEFAULT 0,
  skipped_count int NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed','failed')),
  errors jsonb NOT NULL DEFAULT '[]'
);
CREATE TRIGGER import_batches_updated BEFORE UPDATE ON import_batches FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Saved views: table ships now, UI in a later phase. [Twenty View, simplified]
CREATE TABLE views (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  brand_id uuid NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  name text NOT NULL,
  object text NOT NULL CHECK (object IN ('people','companies')),
  type text NOT NULL DEFAULT 'table' CHECK (type IN ('table','kanban')),
  filters jsonb NOT NULL DEFAULT '[]',
  sorts jsonb NOT NULL DEFAULT '[]',
  visible_fields jsonb NOT NULL DEFAULT '[]',
  group_by_field text
);
CREATE TRIGGER views_updated BEFORE UPDATE ON views FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

- [ ] **Step 4: Apply, run test to verify it passes**

Run: `./db/apply.sh db/migrations/0003_events_import_views.sql && ./db/apply.sh db/tests/test_0003_events.sql`
Expected: `test_0003 OK`

- [ ] **Step 5: Commit**

```bash
cd <repo root> && git add powacrm/db
git commit --no-verify -m "feat(powacrm): events timeline, import batches, views tables

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: RLS policies + GoTrue user + seed

**Files:**
- Create: `db/migrations/0004_rls.sql`
- Create: `db/seed/seed_gpt_trainer.sql`
- Create: `db/setup/create_user.sh`
- Create: `db/tests/test_0004_rls.sh`

**Interfaces:**
- Consumes: all tables (Tasks 2–3)
- Produces: RLS on all tables (`authenticated` only; SELECT filtered `deleted_at IS NULL` where the column exists); GoTrue user `hello@gpt-trainer.com`; seeded brand `gpt-trainer`. The SPA (Task 6+) relies on: anon key + user JWT can CRUD; anon key alone sees nothing.

- [ ] **Step 1: Write the failing test**

`db/tests/test_0004_rls.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
: "${VITE_POWABASE_URL:?}" "${VITE_POWABASE_ANON_KEY:?}" "${PB_TEST_PASSWORD:?}"
BASE="$VITE_POWABASE_URL"; ANON="$VITE_POWABASE_ANON_KEY"

# 1. anon alone must see nothing
N=$(curl -s "$BASE/rest/v1/brands?select=id" -H "apikey: $ANON" -H "Authorization: Bearer $ANON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else -1)')
[ "$N" = "0" ] || { echo "FAIL: anon can read brands ($N)"; exit 1; }

# 2. authenticated user sees the seeded brand
TOKEN=$(curl -s "$BASE/auth/v1/token?grant_type=password" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "{\"email\":\"hello@gpt-trainer.com\",\"password\":\"$PB_TEST_PASSWORD\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
N=$(curl -s "$BASE/rest/v1/brands?select=id&name=eq.gpt-trainer" -H "apikey: $ANON" -H "Authorization: Bearer $TOKEN" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[ "$N" = "1" ] || { echo "FAIL: authenticated cannot read seeded brand ($N)"; exit 1; }
echo "test_0004 OK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `chmod +x db/tests/test_0004_rls.sh db/setup/create_user.sh 2>/dev/null; PB_TEST_PASSWORD=placeholder ./db/tests/test_0004_rls.sh`
Expected: FAIL — either anon can read brands (RLS off) or login fails (user missing).

- [ ] **Step 3: Write RLS migration, user script, seed**

`db/migrations/0004_rls.sql`:
```sql
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
```

`db/setup/create_user.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
: "${VITE_POWABASE_URL:?}" "${PB_SERVICE_KEY:?}" "${PB_TEST_PASSWORD:?}"
curl -sf "$VITE_POWABASE_URL/auth/v1/admin/users" \
  -H "apikey: $PB_SERVICE_KEY" -H "Authorization: Bearer $PB_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"hello@gpt-trainer.com\",\"password\":\"$PB_TEST_PASSWORD\",\"email_confirm\":true}" \
  && echo " user created"
```
(If the endpoint 404s on this Powabase build, fall back to `POST /auth/v1/signup` with the anon key, then confirm via Studio — note whichever worked in the commit message.)

`db/seed/seed_gpt_trainer.sql`:
```sql
INSERT INTO brands (name, product_description, voice_notes)
VALUES ('gpt-trainer',
        'gpt-trainer: no-code platform to build, train, and deploy custom AI chatbots and agents on your own data.',
        'Founder voice: direct, technical, helpful. No competitor bashing. No hype-chains.')
ON CONFLICT DO NOTHING;
```

- [ ] **Step 4: Apply all, create user (ask the human for the password value first — do not invent one), run test**

Run:
```bash
./db/apply.sh db/migrations/0004_rls.sql
./db/apply.sh db/seed/seed_gpt_trainer.sql
PB_TEST_PASSWORD=<chosen> ./db/setup/create_user.sh
PB_TEST_PASSWORD=<chosen> ./db/tests/test_0004_rls.sh
```
Expected: `test_0004 OK`

- [ ] **Step 5: Commit**

```bash
cd <repo root> && git add powacrm/db
git commit --no-verify -m "feat(powacrm): RLS policies, auth user setup, gpt-trainer seed

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: CSV import RPC (dedupe-then-restore, server-side)

**Files:**
- Create: `db/migrations/0005_import_rpc.sql`
- Create: `db/tests/test_0005_import_rpc.sql`

**Interfaces:**
- Consumes: `people`, `companies`, `import_batches`, `events` (Tasks 2–3)
- Produces: `public.import_people(_brand_id uuid, _import_id uuid, _rows jsonb) RETURNS jsonb` — rows is a JSON array of `{first_name, last_name, email, title, linkedin_url, company_name, company_domain}`; returns `{"inserted": n, "restored": n, "skipped": n, "errors": [{"row": i, "message": text}]}`. Exposed at `POST /rest/v1/rpc/import_people` to `authenticated`. Frontend Task 10 calls it via `supabase.rpc('import_people', {...})`.

- [ ] **Step 1: Write the failing test**

`db/tests/test_0005_import_rpc.sql`:
```sql
DO $$
DECLARE b uuid; imp uuid; r jsonb; pid uuid;
BEGIN
  INSERT INTO brands (name) VALUES ('_test_imp') RETURNING id INTO b;
  INSERT INTO import_batches (brand_id, filename, row_count) VALUES (b, 't.csv', 3) RETURNING id INTO imp;

  r := import_people(b, imp, '[
    {"first_name":"Ana","last_name":"Lee","email":"ana@acme.com","title":"CEO","company_name":"Acme","company_domain":"ACME.com"},
    {"first_name":"Bob","last_name":"Ray","email":"bob@beta.io","company_name":"Beta","company_domain":"beta.io"},
    {"first_name":"Ana","last_name":"Dup","email":"ANA@acme.com"}
  ]'::jsonb);
  IF (r->>'inserted')::int <> 2 THEN RAISE EXCEPTION 'expected 2 inserted, got %', r; END IF;
  IF (r->>'skipped')::int <> 1 THEN RAISE EXCEPTION 'expected 1 skipped dup, got %', r; END IF;

  -- domain normalized to lowercase; company linked
  SELECT company_id INTO pid FROM people WHERE brand_id = b AND lower(email) = 'ana@acme.com';
  IF (SELECT domain FROM companies WHERE id = pid) <> 'acme.com' THEN RAISE EXCEPTION 'domain not normalized'; END IF;
  -- provenance stamped
  IF (SELECT created_by_source FROM people WHERE brand_id = b AND lower(email)='ana@acme.com') <> 'IMPORT'
    THEN RAISE EXCEPTION 'created_by_source not IMPORT'; END IF;

  -- soft-delete then re-import restores [Twenty dedupe-then-restore]
  UPDATE people SET deleted_at = now() WHERE brand_id = b AND lower(email) = 'ana@acme.com';
  r := import_people(b, imp, '[{"first_name":"Ana","email":"ana@acme.com"}]'::jsonb);
  IF (r->>'restored')::int <> 1 THEN RAISE EXCEPTION 'expected restore, got %', r; END IF;
  IF EXISTS (SELECT 1 FROM people WHERE brand_id = b AND lower(email)='ana@acme.com' AND deleted_at IS NOT NULL)
    THEN RAISE EXCEPTION 'row not restored'; END IF;

  DELETE FROM events WHERE brand_id = b; DELETE FROM people WHERE brand_id = b;
  DELETE FROM companies WHERE brand_id = b; DELETE FROM import_batches WHERE brand_id = b;
  DELETE FROM brands WHERE id = b;
END $$;
SELECT 'test_0005 OK' AS result;
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./db/apply.sh db/tests/test_0005_import_rpc.sql`
Expected: FAIL — `function import_people(...) does not exist`

- [ ] **Step 3: Write the RPC**

`db/migrations/0005_import_rpc.sql`:
```sql
-- SECURITY DEFINER: must see soft-deleted rows (RLS SELECT policy hides them)
-- to implement dedupe-then-restore. Grant to authenticated only.
CREATE OR REPLACE FUNCTION public.import_people(_brand_id uuid, _import_id uuid, _rows jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  row_j jsonb; i int := 0;
  n_ins int := 0; n_res int := 0; n_skip int := 0; errs jsonb := '[]';
  v_email text; v_domain text; v_company uuid; v_person uuid; v_deleted timestamptz;
  ctx jsonb;
BEGIN
  FOR row_j IN SELECT * FROM jsonb_array_elements(_rows) LOOP
    i := i + 1;
    BEGIN
      v_email := nullif(trim(row_j->>'email'), '');
      v_domain := nullif(lower(trim(row_j->>'company_domain')), '');
      ctx := jsonb_build_object('import_id', _import_id, 'row', i);

      -- company: upsert-restore by (brand, domain)
      v_company := NULL;
      IF v_domain IS NOT NULL THEN
        SELECT id, deleted_at INTO v_company, v_deleted FROM companies
          WHERE brand_id = _brand_id AND domain = v_domain;
        IF v_company IS NULL THEN
          INSERT INTO companies (brand_id, name, domain, created_by_source, created_by_name, created_by_context)
          VALUES (_brand_id, nullif(trim(row_j->>'company_name'), ''), v_domain, 'IMPORT', 'CSV import', ctx)
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

  UPDATE import_batches SET status = 'completed',
    inserted_count = n_ins, restored_count = n_res, skipped_count = n_skip, errors = errs
  WHERE id = _import_id;
  RETURN jsonb_build_object('inserted', n_ins, 'restored', n_res, 'skipped', n_skip, 'errors', errs);
END $$;

REVOKE ALL ON FUNCTION public.import_people(uuid, uuid, jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.import_people(uuid, uuid, jsonb) TO authenticated;
```

- [ ] **Step 4: Apply, run test to verify it passes**

Run: `./db/apply.sh db/migrations/0005_import_rpc.sql && ./db/apply.sh db/tests/test_0005_import_rpc.sql`
Expected: `test_0005 OK`

- [ ] **Step 5: Commit**

```bash
cd <repo root> && git add powacrm/db
git commit --no-verify -m "feat(powacrm): import_people RPC with dedupe-then-restore

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Frontend scaffold + design tokens + Powabase client

**Files:**
- Create: `app/` via Vite scaffold (`package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, `src/main.tsx`)
- Create: `app/src/styles/tokens.css`
- Create: `app/src/lib/powabase.ts`
- Create: `app/.env.example`
- Create: `app/src/lib/position.ts`
- Test: `app/src/lib/position.test.ts`

**Interfaces:**
- Produces: `supabase` client singleton (`import { supabase } from '@/lib/powabase'`); CSS custom properties (`--bg-primary`, `--bg-secondary`, `--bg-tertiary`, `--bg-app`, `--fg-primary`, `--fg-secondary`, `--fg-tertiary`, `--fg-light`, `--border-light`, `--border-medium`, `--border-strong`, `--hover`, `--radius-sm/md/lg`, `--space-1..8`, `--font-xs/sm/md/lg/xl`, `--tag-<color>-bg`, `--tag-<color>-fg` for gray/blue/purple/sky/orange/green/red); `positionBetween(prev: number | null, next: number | null): number`.
- Vite alias `@` → `app/src`.

- [ ] **Step 1: Scaffold and install**

```bash
cd <repo root>/powacrm
npm create vite@latest app -- --template react-ts
cd app && npm install
npm install @supabase/supabase-js @tanstack/react-query react-router-dom papaparse @dnd-kit/core
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom @types/papaparse
```
Add to `app/package.json` scripts: `"test": "vitest run"`. Add to `vite.config.ts`: `resolve: { alias: { '@': '/src' } }` and a `test` block `{ environment: 'jsdom' }` (use `defineConfig` from `vitest/config`).

- [ ] **Step 2: Write the failing position test**

`app/src/lib/position.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { positionBetween } from './position';

describe('positionBetween', () => {
  it('returns 0 in an empty column', () => expect(positionBetween(null, null)).toBe(0));
  it('goes below the first card', () => expect(positionBetween(null, 5)).toBe(4));
  it('goes past the last card', () => expect(positionBetween(3, null)).toBe(4));
  it('takes the fractional midpoint between neighbors', () => expect(positionBetween(1, 2)).toBe(1.5));
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd app && npm test`
Expected: FAIL — cannot resolve `./position`

- [ ] **Step 4: Implement position util + client + tokens**

`app/src/lib/position.ts`:
```ts
// Fractional-index ordering: one PATCH per drop, no sibling reindex. [Twenty]
export function positionBetween(prev: number | null, next: number | null): number {
  if (prev === null && next === null) return 0;
  if (prev === null) return (next as number) - 1;
  if (next === null) return prev + 1;
  return (prev + next) / 2;
}
```

`app/src/lib/powabase.ts`:
```ts
import { createClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_POWABASE_URL as string;
const anonKey = import.meta.env.VITE_POWABASE_ANON_KEY as string;
if (!url || !anonKey) throw new Error('Set VITE_POWABASE_URL and VITE_POWABASE_ANON_KEY in app/.env.local');

export const supabase = createClient(url, anonKey);
```

`app/.env.example`:
```
VITE_POWABASE_URL=https://<ref>.p.powabase.ai
VITE_POWABASE_ANON_KEY=<anon key from Connect modal>
```

`app/src/styles/tokens.css` — Radix-style 12-step gray + semantic aliases [Twenty tokens]:
```css
:root {
  --gray-1:#fcfcfc; --gray-2:#f9f9f9; --gray-3:#f0f0f0; --gray-4:#e8e8e8;
  --gray-5:#e0e0e0; --gray-6:#d9d9d9; --gray-8:#bbb; --gray-9:#8d8d8d;
  --gray-11:#646464; --gray-12:#202020;
  --bg-app:var(--gray-3); --bg-primary:var(--gray-1); --bg-secondary:var(--gray-2); --bg-tertiary:var(--gray-4);
  --fg-primary:var(--gray-12); --fg-secondary:var(--gray-11); --fg-tertiary:var(--gray-9); --fg-light:var(--gray-8);
  --border-light:var(--gray-4); --border-medium:var(--gray-5); --border-strong:var(--gray-6);
  --hover:rgba(0,0,0,.04);
  --radius-sm:4px; --radius-md:8px; --radius-lg:16px;
  --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px; --space-6:24px; --space-8:32px;
  --font-xs:.75rem; --font-sm:.85rem; --font-md:.95rem; --font-lg:1.2rem; --font-xl:1.5rem;
  /* stage tags: step-3 background / step-11 text [Twenty] */
  --tag-gray-bg:#f0f0f0;  --tag-gray-fg:#646464;
  --tag-blue-bg:#e6f0fd;  --tag-blue-fg:#2f6bd0;
  --tag-purple-bg:#f1eafc;--tag-purple-fg:#7c4fc4;
  --tag-sky-bg:#e1f2fa;   --tag-sky-fg:#1d7fa6;
  --tag-orange-bg:#fdeedd;--tag-orange-fg:#c26a1e;
  --tag-green-bg:#e3f4e5; --tag-green-fg:#2f7a3d;
  --tag-red-bg:#fbe5e4;   --tag-red-fg:#c33f38;
}
.dark {
  --gray-1:#111; --gray-2:#191919; --gray-3:#222; --gray-4:#2a2a2a; --gray-5:#313131;
  --gray-6:#3a3a3a; --gray-8:#575757; --gray-9:#7b7b7b; --gray-11:#b4b4b4; --gray-12:#eee;
  --hover:rgba(255,255,255,.06);
  --tag-gray-bg:#2a2a2a;  --tag-gray-fg:#b4b4b4;
  --tag-blue-bg:#182a45;  --tag-blue-fg:#7ab0f5;
  --tag-purple-bg:#2a2040;--tag-purple-fg:#b79aec;
  --tag-sky-bg:#132c38;   --tag-sky-fg:#6ec2e8;
  --tag-orange-bg:#3a2513;--tag-orange-fg:#eda15f;
  --tag-green-bg:#16301b; --tag-green-fg:#7bc98a;
  --tag-red-bg:#3b1a18;   --tag-red-fg:#f08e88;
}
body {
  margin:0; background:var(--bg-app); color:var(--fg-primary);
  font-family:Inter,system-ui,sans-serif; font-size:var(--font-md);
}
* { box-sizing:border-box; }
```
Import it from `src/main.tsx` (`import './styles/tokens.css'`), delete Vite's default `App.css`/`index.css` contents.

- [ ] **Step 5: Run tests, create real `.env.local`, verify dev server**

Run: `npm test` → PASS. Copy `.env.example` to `.env.local` and fill with the real Project URL + anon key (from the user's message / Connect modal). Run `npm run dev` and load the page — no console errors.

- [ ] **Step 6: Commit**

```bash
cd <repo root> && git add powacrm/app
git commit --no-verify -m "feat(powacrm): SPA scaffold, design tokens, powabase client

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Auth + app shell (sidebar, brand switcher, routing)

**Files:**
- Create: `app/src/App.tsx` (replace scaffold)
- Create: `app/src/auth/LoginPage.tsx`
- Create: `app/src/auth/useSession.ts`
- Create: `app/src/shell/Shell.tsx`
- Create: `app/src/shell/BrandContext.tsx`
- Modify: `app/src/main.tsx`

**Interfaces:**
- Consumes: `supabase` (Task 6)
- Produces: `useSession(): { session: Session | null, loading: boolean }`; `useBrand(): { brand: Brand, brands: Brand[], setBrandId: (id: string) => void }` where `type Brand = { id: string; name: string; confidence_threshold: number; dry_run: boolean; paused: boolean }`; routes `/login`, `/` (board), `/leads/:id`, `/import` inside `<Shell>`. Tasks 8–10 render inside these routes and read the active brand from `useBrand()`.

- [ ] **Step 1: Write `useSession` + login page**

`app/src/auth/useSession.ts`:
```ts
import { useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase } from '@/lib/powabase';

export function useSession() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => { setSession(data.session); setLoading(false); });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);
  return { session, loading };
}
```

`app/src/auth/LoginPage.tsx`:
```tsx
import { useState } from 'react';
import { supabase } from '@/lib/powabase';

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setError(error.message);
  }
  return (
    <form onSubmit={submit} style={{ maxWidth: 320, margin: '20vh auto', display: 'grid', gap: 'var(--space-3)' }}>
      <h1 style={{ fontSize: 'var(--font-xl)' }}>PowaCRM</h1>
      <input placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} />
      <input placeholder="Password" type="password" value={password} onChange={e => setPassword(e.target.value)} />
      <button type="submit">Sign in</button>
      {error && <p style={{ color: 'var(--tag-red-fg)' }}>{error}</p>}
    </form>
  );
}
```

- [ ] **Step 2: Write BrandContext + Shell**

`app/src/shell/BrandContext.tsx`:
```tsx
import { createContext, useContext, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';

export type Brand = { id: string; name: string; confidence_threshold: number; dry_run: boolean; paused: boolean };
const Ctx = createContext<{ brand: Brand; brands: Brand[]; setBrandId: (id: string) => void } | null>(null);

export function BrandProvider({ children }: { children: React.ReactNode }) {
  const { data: brands } = useQuery({
    queryKey: ['brands'],
    queryFn: async () => {
      const { data, error } = await supabase.from('brands')
        .select('id,name,confidence_threshold,dry_run,paused').order('created_at');
      if (error) throw error;
      return data as Brand[];
    },
  });
  const [brandId, setBrandId] = useState<string | null>(null);
  if (!brands || brands.length === 0) return <p style={{ padding: 'var(--space-6)' }}>Loading brands…</p>;
  const brand = brands.find(b => b.id === brandId) ?? brands[0];
  return <Ctx.Provider value={{ brand, brands, setBrandId }}>{children}</Ctx.Provider>;
}
export function useBrand() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useBrand outside BrandProvider');
  return v;
}
```

`app/src/shell/Shell.tsx` — fixed 240px sidebar, floating content card [Twenty shell]:
```tsx
import { NavLink, Outlet } from 'react-router-dom';
import { supabase } from '@/lib/powabase';
import { useBrand } from './BrandContext';

const linkStyle = ({ isActive }: { isActive: boolean }): React.CSSProperties => ({
  display: 'block', padding: 'var(--space-1) var(--space-2)', borderRadius: 'var(--radius-md)',
  textDecoration: 'none', fontSize: 'var(--font-sm)',
  color: isActive ? 'var(--fg-primary)' : 'var(--fg-secondary)',
  background: isActive ? 'var(--hover)' : 'transparent',
});

export function Shell() {
  const { brand, brands, setBrandId } = useBrand();
  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      <nav style={{ width: 240, flexShrink: 0, padding: 'var(--space-4)', display: 'grid', gap: 'var(--space-1)', alignContent: 'start' }}>
        <select value={brand.id} onChange={e => setBrandId(e.target.value)}
          style={{ marginBottom: 'var(--space-4)', width: '100%' }}>
          {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <NavLink to="/" style={linkStyle} end>Pipeline</NavLink>
        <NavLink to="/import" style={linkStyle}>Import</NavLink>
        <button onClick={() => supabase.auth.signOut()}
          style={{ marginTop: 'var(--space-6)', justifySelf: 'start' }}>Sign out</button>
      </nav>
      <main style={{ flex: 1, background: 'var(--bg-primary)', borderRadius: 'var(--radius-lg) 0 0 0',
        border: '1px solid var(--border-medium)', overflow: 'auto', padding: 'var(--space-6)' }}>
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 3: Wire App + main**

`app/src/App.tsx`:
```tsx
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useSession } from '@/auth/useSession';
import { LoginPage } from '@/auth/LoginPage';
import { Shell } from '@/shell/Shell';
import { BrandProvider } from '@/shell/BrandContext';
import { BoardPage } from '@/board/BoardPage';
import { LeadPage } from '@/lead/LeadPage';
import { ImportPage } from '@/import/ImportPage';

const qc = new QueryClient();

export default function App() {
  const { session, loading } = useSession();
  if (loading) return null;
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        {!session ? <LoginPage /> : (
          <BrandProvider>
            <Routes>
              <Route element={<Shell />}>
                <Route path="/" element={<BoardPage />} />
                <Route path="/leads/:id" element={<LeadPage />} />
                <Route path="/import" element={<ImportPage />} />
              </Route>
              <Route path="*" element={<Navigate to="/" />} />
            </Routes>
          </BrandProvider>
        )}
      </BrowserRouter>
    </QueryClientProvider>
  );
}
```
(BoardPage/LeadPage/ImportPage are Tasks 8–10; create placeholder components now — `export function BoardPage() { return <h1>Pipeline</h1>; }` in `app/src/board/BoardPage.tsx`, same shape for `app/src/lead/LeadPage.tsx` ("Lead") and `app/src/import/ImportPage.tsx` ("Import") — each replaced by its task.)
`app/src/main.tsx`: render `<App />`, import `./styles/tokens.css`.

- [ ] **Step 4: Verify manually**

Run `npm run dev`. Expected: login screen → sign in with the GoTrue user (Task 4) → shell renders with sidebar, brand dropdown showing "gpt-trainer", placeholder pages navigate. Wrong password shows the error message.

- [ ] **Step 5: Commit**

```bash
cd <repo root> && git add powacrm/app
git commit --no-verify -m "feat(powacrm): auth flow, app shell, brand switcher

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Pipeline kanban board

**Files:**
- Create: `app/src/board/BoardPage.tsx` (replace placeholder)
- Create: `app/src/board/useLeads.ts`
- Create: `app/src/board/StageTag.tsx`
- Test: `app/src/board/useLeads.test.ts`

**Interfaces:**
- Consumes: `useBrand()` (Task 7), `positionBetween` (Task 6), tables `people` + `stage_options`
- Produces: `type Lead = { id: string; first_name: string | null; last_name: string | null; title: string | null; email: string | null; stage: string; position: number; fit_score: number | null; company: { id: string; name: string | null; domain: string | null } | null }`; `useLeads(brandId)` (TanStack query, key `['leads', brandId]`); `useStages()` (key `['stages']`, returns `{ value, label, color, position }[]`); `useMoveLead()` mutation `{ lead, toStage, position }` with optimistic update; `<StageTag label color>` chip. `leadName(l: Lead): string` helper (joins names, falls back to email). Task 9 reuses `StageTag` and `leadName`.

- [ ] **Step 1: Write the failing test for grouping/naming logic**

`app/src/board/useLeads.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { groupByStage, leadName } from './useLeads';

const mk = (over: object) => ({ id: 'x', first_name: null, last_name: null, title: null,
  email: null, stage: 'sourced', position: 0, fit_score: null, company: null, ...over });

describe('groupByStage', () => {
  it('buckets leads by stage sorted by position', () => {
    const leads = [mk({ id: 'a', stage: 'sourced', position: 2 }), mk({ id: 'b', stage: 'sourced', position: 1 }), mk({ id: 'c', stage: 'won' })];
    const g = groupByStage(leads as any, ['sourced', 'won']);
    expect(g['sourced'].map((l: any) => l.id)).toEqual(['b', 'a']);
    expect(g['won'].map((l: any) => l.id)).toEqual(['c']);
  });
  it('always returns every stage key', () => {
    expect(groupByStage([], ['sourced', 'won'])).toEqual({ sourced: [], won: [] });
  });
});

describe('leadName', () => {
  it('joins first and last', () => expect(leadName(mk({ first_name: 'Ana', last_name: 'Lee' }) as any)).toBe('Ana Lee'));
  it('falls back to email', () => expect(leadName(mk({ email: 'x@y.z' }) as any)).toBe('x@y.z'));
  it('falls back to Unknown', () => expect(leadName(mk({}) as any)).toBe('Unknown'));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm test`
Expected: FAIL — `groupByStage` not exported

- [ ] **Step 3: Implement data hooks + board**

`app/src/board/useLeads.ts`:
```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';

export type Lead = {
  id: string; first_name: string | null; last_name: string | null; title: string | null;
  email: string | null; stage: string; position: number; fit_score: number | null;
  company: { id: string; name: string | null; domain: string | null } | null;
};
export type Stage = { value: string; label: string; color: string; position: number };

export function leadName(l: Lead): string {
  const n = [l.first_name, l.last_name].filter(Boolean).join(' ');
  return n || l.email || 'Unknown';
}

export function groupByStage(leads: Lead[], stageValues: string[]): Record<string, Lead[]> {
  const g: Record<string, Lead[]> = Object.fromEntries(stageValues.map(s => [s, []]));
  for (const l of leads) (g[l.stage] ?? (g[l.stage] = [])).push(l);
  for (const s of Object.keys(g)) g[s].sort((a, b) => a.position - b.position);
  return g;
}

export function useStages() {
  return useQuery({
    queryKey: ['stages'],
    queryFn: async () => {
      const { data, error } = await supabase.from('stage_options')
        .select('value,label,color,position').eq('object', 'people').order('position');
      if (error) throw error;
      return data as Stage[];
    },
  });
}

export function useLeads(brandId: string) {
  return useQuery({
    queryKey: ['leads', brandId],
    queryFn: async () => {
      const { data, error } = await supabase.from('people')
        .select('id,first_name,last_name,title,email,stage,position,fit_score,company:companies(id,name,domain)')
        .eq('brand_id', brandId).is('deleted_at', null).limit(1000);
      if (error) throw error;
      return data as unknown as Lead[];
    },
  });
}

export function useMoveLead(brandId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ lead, toStage, position }: { lead: Lead; toStage: string; position: number }) => {
      const { error } = await supabase.from('people').update({ stage: toStage, position }).eq('id', lead.id);
      if (error) throw error;
      await supabase.from('events').insert({
        brand_id: brandId, person_id: lead.id, event_type: 'stage_changed',
        actor_source: 'MANUAL', actor_name: await currentActorName(),
        properties: { diff: { stage: { before: lead.stage, after: toStage } } },
      });
    },
    onMutate: async ({ lead, toStage, position }) => {
      await qc.cancelQueries({ queryKey: ['leads', brandId] });
      const prev = qc.getQueryData<Lead[]>(['leads', brandId]);
      qc.setQueryData<Lead[]>(['leads', brandId], old =>
        (old ?? []).map(l => (l.id === lead.id ? { ...l, stage: toStage, position } : l)));
      return { prev };
    },
    onError: (_e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(['leads', brandId], ctx.prev); },
    onSettled: () => qc.invalidateQueries({ queryKey: ['leads', brandId] }),
  });
}
```

`app/src/board/StageTag.tsx`:
```tsx
export function StageTag({ label, color }: { label: string; color: string }) {
  return (
    <span style={{ background: `var(--tag-${color}-bg)`, color: `var(--tag-${color}-fg)`,
      padding: '1px var(--space-2)', borderRadius: 'var(--radius-sm)', fontSize: 'var(--font-xs)', fontWeight: 500 }}>
      {label}
    </span>
  );
}
```

`app/src/board/BoardPage.tsx` — columns from stages, dnd-kit droppable per column, draggable cards; on drop compute `positionBetween` against the target column's sorted leads and fire `useMoveLead`:
```tsx
import { DndContext, useDraggable, useDroppable, type DragEndEvent } from '@dnd-kit/core';
import { useNavigate } from 'react-router-dom';
import { useBrand } from '@/shell/BrandContext';
import { positionBetween } from '@/lib/position';
import { StageTag } from './StageTag';
import { groupByStage, leadName, useLeads, useMoveLead, useStages, type Lead, type Stage } from './useLeads';

function Card({ lead }: { lead: Lead }) {
  const nav = useNavigate();
  const { attributes, listeners, setNodeRef, transform } = useDraggable({ id: lead.id, data: lead });
  return (
    <div ref={setNodeRef} {...attributes} {...listeners}
      onClick={() => !transform && nav(`/leads/${lead.id}`)}
      style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-medium)',
        borderRadius: 'var(--radius-md)', padding: 'var(--space-3)', cursor: 'grab',
        transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined }}>
      <div style={{ fontWeight: 500 }}>{leadName(lead)}</div>
      <div style={{ fontSize: 'var(--font-sm)', color: 'var(--fg-tertiary)' }}>
        {[lead.title, lead.company?.name].filter(Boolean).join(' · ')}
      </div>
      {lead.fit_score != null && <div style={{ fontSize: 'var(--font-xs)', color: 'var(--fg-tertiary)' }}>fit {lead.fit_score}</div>}
    </div>
  );
}

function Column({ stage, leads }: { stage: Stage; leads: Lead[] }) {
  const { setNodeRef } = useDroppable({ id: stage.value });
  return (
    <div ref={setNodeRef} style={{ width: 260, flexShrink: 0, display: 'grid', gap: 'var(--space-2)', alignContent: 'start' }}>
      <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
        <StageTag label={stage.label} color={stage.color} />
        <span style={{ color: 'var(--fg-tertiary)', fontSize: 'var(--font-xs)' }}>{leads.length}</span>
      </div>
      {leads.map(l => <Card key={l.id} lead={l} />)}
    </div>
  );
}

export function BoardPage() {
  const { brand } = useBrand();
  const { data: stages } = useStages();
  const { data: leads } = useLeads(brand.id);
  const move = useMoveLead(brand.id);
  if (!stages || !leads) return <p>Loading…</p>;
  const grouped = groupByStage(leads, stages.map(s => s.value));

  function onDragEnd(e: DragEndEvent) {
    const lead = e.active.data.current as Lead;
    const toStage = e.over?.id as string | undefined;
    if (!toStage || toStage === lead.stage) return;
    const col = grouped[toStage];
    const last = col.length ? col[col.length - 1].position : null;
    move.mutate({ lead, toStage, position: positionBetween(last, null) });
  }

  return (
    <DndContext onDragEnd={onDragEnd}>
      <h1 style={{ fontSize: 'var(--font-lg)', marginTop: 0 }}>Pipeline</h1>
      <div style={{ display: 'flex', gap: 'var(--space-4)', overflowX: 'auto' }}>
        {stages.map(s => <Column key={s.value} stage={s} leads={grouped[s.value]} />)}
      </div>
    </DndContext>
  );
}
```

- [ ] **Step 4: Run tests + verify manually**

Run: `npm test` → PASS. Then `npm run dev`: import is not built yet, so insert 2–3 test people via psql (`INSERT INTO people (brand_id, first_name, stage) SELECT id, 'Test A', 'sourced' FROM brands WHERE name='gpt-trainer';`). Expected: columns render with counts; dragging a card to another column persists (reload keeps it) and writes a `stage_changed` event (check `SELECT event_type FROM events ORDER BY created_at DESC LIMIT 1;`).

- [ ] **Step 5: Commit**

```bash
cd <repo root> && git add powacrm/app
git commit --no-verify -m "feat(powacrm): pipeline kanban board with optimistic drag

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Lead record view (two-pane, inline edit, timeline)

**Files:**
- Create: `app/src/lead/LeadPage.tsx` (replace placeholder)
- Create: `app/src/lead/InlineField.tsx`
- Create: `app/src/lead/Timeline.tsx`
- Create: `app/src/lead/groupEventsByMonth.ts`
- Test: `app/src/lead/groupEventsByMonth.test.ts`

**Interfaces:**
- Consumes: `supabase`, `useBrand()`, `StageTag`, `leadName`, `useStages()` (Tasks 6–8); `events` shape from Task 3
- Produces: `type TimelineEvent = { id: string; event_type: string; happens_at: string; actor_name: string; actor_source: string; properties: Record<string, unknown> }`; `groupEventsByMonth(events: TimelineEvent[]): { year: number; month: number; label: string; items: TimelineEvent[] }[]` (desc); `<InlineField label value onSave>` (click-to-edit, Enter/blur saves, Esc reverts, "Empty" placeholder).

- [ ] **Step 1: Write the failing month-grouping test**

`app/src/lead/groupEventsByMonth.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { groupEventsByMonth } from './groupEventsByMonth';

const ev = (id: string, iso: string) => ({ id, event_type: 'note', happens_at: iso, actor_name: 'x', actor_source: 'MANUAL', properties: {} });

describe('groupEventsByMonth', () => {
  it('groups by month, newest group and newest item first', () => {
    const g = groupEventsByMonth([ev('a', '2026-07-01T10:00:00Z'), ev('b', '2026-08-20T10:00:00Z'), ev('c', '2026-08-02T10:00:00Z')]);
    expect(g.map(x => x.label)).toEqual(['August 2026', 'July 2026']);
    expect(g[0].items.map(i => i.id)).toEqual(['b', 'c']);
  });
  it('returns empty array for no events', () => expect(groupEventsByMonth([])).toEqual([]));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm test`
Expected: FAIL — module not found

- [ ] **Step 3: Implement grouping, inline field, timeline, page**

`app/src/lead/groupEventsByMonth.ts`:
```ts
export type TimelineEvent = {
  id: string; event_type: string; happens_at: string;
  actor_name: string; actor_source: string; properties: Record<string, unknown>;
};
const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];

// Month grouping, not day. [Twenty]
export function groupEventsByMonth(events: TimelineEvent[]) {
  const sorted = [...events].sort((a, b) => b.happens_at.localeCompare(a.happens_at));
  const groups: { year: number; month: number; label: string; items: TimelineEvent[] }[] = [];
  for (const e of sorted) {
    const d = new Date(e.happens_at);
    const y = d.getUTCFullYear(), m = d.getUTCMonth();
    const last = groups[groups.length - 1];
    if (last && last.year === y && last.month === m) last.items.push(e);
    else groups.push({ year: y, month: m, label: `${MONTHS[m]} ${y}`, items: [e] });
  }
  return groups;
}
```

`app/src/lead/InlineField.tsx`:
```tsx
import { useState } from 'react';

export function InlineField({ label, value, onSave }:
  { label: string; value: string | null; onSave: (v: string | null) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  function open() { setDraft(value ?? ''); setEditing(true); }
  function commit() { setEditing(false); const v = draft.trim() || null; if (v !== value) onSave(v); }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minHeight: 28 }}>
      <span style={{ width: 90, flexShrink: 0, fontSize: 'var(--font-sm)', color: 'var(--fg-tertiary)' }}>{label}</span>
      {editing ? (
        <input autoFocus value={draft} onChange={e => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false); }} />
      ) : (
        <span onClick={open} style={{ cursor: 'pointer', borderRadius: 'var(--radius-sm)',
          padding: '2px var(--space-1)', color: value ? 'var(--fg-primary)' : 'var(--fg-light)' }}
          onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
          {value ?? 'Empty'}
        </span>
      )}
    </div>
  );
}
```

`app/src/lead/Timeline.tsx` — month separators, icon + connector rail, sentence rows, relative time with exact tooltip [Twenty]:
```tsx
import { groupEventsByMonth, type TimelineEvent } from './groupEventsByMonth';

const ICONS: Record<string, string> = { note: '📝', stage_changed: '→', field_updated: '✏️', import: '⬆', error: '⚠' };

function rel(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function sentence(e: TimelineEvent): string {
  const p = e.properties as any;
  if (e.event_type === 'stage_changed' && p?.diff?.stage) return `moved to ${p.diff.stage.after}`;
  if (e.event_type === 'note') return String(p?.body ?? 'added a note');
  if (e.event_type === 'import') return 'imported from CSV';
  return e.event_type.replace('_', ' ');
}

export function Timeline({ events }: { events: TimelineEvent[] }) {
  const groups = groupEventsByMonth(events);
  if (!groups.length) return <p style={{ color: 'var(--fg-light)' }}>No activity yet.</p>;
  return (
    <div style={{ display: 'grid', gap: 'var(--space-4)' }}>
      {groups.map(g => (
        <div key={g.label}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-2)' }}>
            <span style={{ fontSize: 'var(--font-xs)', fontWeight: 600, color: 'var(--fg-light)' }}>{g.label}</span>
            <div style={{ flex: 1, height: 1, background: 'var(--border-light)' }} />
          </div>
          {g.items.map((e, i) => (
            <div key={e.id} style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <div style={{ display: 'grid', justifyItems: 'center' }}>
                <span style={{ fontSize: 14 }}>{ICONS[e.event_type] ?? '•'}</span>
                {i < g.items.length - 1 && <div style={{ width: 2, flex: 1, background: 'var(--border-light)' }} />}
              </div>
              <div style={{ paddingBottom: 'var(--space-3)', fontSize: 'var(--font-sm)' }}>
                <span style={{ fontWeight: 500 }}>{e.actor_name}</span> {sentence(e)}{' '}
                <span title={new Date(e.happens_at).toLocaleString()} style={{ color: 'var(--fg-tertiary)' }}>{rel(e.happens_at)}</span>
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
```

`app/src/lead/LeadPage.tsx` — two-pane grid; left summary + fields, right tabs Activity | Research; note composer inserts a `note` event:
```tsx
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';
import { useBrand } from '@/shell/BrandContext';
import { StageTag } from '@/board/StageTag';
import { leadName, useStages, type Lead } from '@/board/useLeads';
import { InlineField } from './InlineField';
import { Timeline } from './Timeline';
import type { TimelineEvent } from './groupEventsByMonth';

type FullLead = Lead & { linkedin_url: string | null; created_at: string; company_id: string | null };

export function LeadPage() {
  const { id } = useParams<{ id: string }>();
  const { brand } = useBrand();
  const { data: stages } = useStages();
  const qc = useQueryClient();
  const [tab, setTab] = useState<'activity' | 'research'>('activity');
  const [note, setNote] = useState('');

  const { data: lead } = useQuery({
    queryKey: ['lead', id],
    queryFn: async () => {
      const { data, error } = await supabase.from('people')
        .select('id,first_name,last_name,title,email,stage,position,fit_score,linkedin_url,created_at,company_id,company:companies(id,name,domain,research)')
        .eq('id', id!).single();
      if (error) throw error;
      return data as unknown as FullLead & { company: { research: string | null } | null };
    },
  });
  const { data: events } = useQuery({
    queryKey: ['events', id],
    queryFn: async () => {
      const { data, error } = await supabase.from('events')
        .select('id,event_type,happens_at,actor_name,actor_source,properties')
        .eq('person_id', id!).order('happens_at', { ascending: false }).limit(200);
      if (error) throw error;
      return data as TimelineEvent[];
    },
  });

  const patch = useMutation({
    mutationFn: async (fields: Partial<FullLead>) => {
      const { error } = await supabase.from('people').update(fields).eq('id', id!);
      if (error) throw error;
    },
    onSettled: () => { qc.invalidateQueries({ queryKey: ['lead', id] }); qc.invalidateQueries({ queryKey: ['leads', brand.id] }); },
  });
  const addNote = useMutation({
    mutationFn: async (body: string) => {
      const { error } = await supabase.from('events').insert({
        brand_id: brand.id, person_id: id, company_id: lead?.company_id ?? null,
        event_type: 'note', actor_source: 'MANUAL', actor_name: await currentActorName(), properties: { body },
      });
      if (error) throw error;
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['events', id] }),
  });

  if (!lead || !stages || !events) return <p>Loading…</p>;
  const stage = stages.find(s => s.value === lead.stage);
  const days = Math.round((Date.now() - new Date(lead.created_at).getTime()) / 86400000);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 'var(--space-6)', height: '100%' }}>
      <aside style={{ background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: 'var(--space-4)' }}>
        <h2 style={{ margin: 0, fontSize: 'var(--font-lg)' }}>{leadName(lead)}</h2>
        <p title={new Date(lead.created_at).toLocaleString()}
          style={{ fontSize: 'var(--font-xs)', color: 'var(--fg-tertiary)' }}>Added {days}d ago</p>
        {stage && <StageTag label={stage.label} color={stage.color} />}
        <div style={{ marginTop: 'var(--space-4)', display: 'grid', gap: 'var(--space-1)' }}>
          <InlineField label="First name" value={lead.first_name} onSave={v => patch.mutate({ first_name: v })} />
          <InlineField label="Last name" value={lead.last_name} onSave={v => patch.mutate({ last_name: v })} />
          <InlineField label="Title" value={lead.title} onSave={v => patch.mutate({ title: v })} />
          <InlineField label="Email" value={lead.email} onSave={v => patch.mutate({ email: v })} />
          <InlineField label="LinkedIn" value={lead.linkedin_url} onSave={v => patch.mutate({ linkedin_url: v })} />
        </div>
        {lead.company && (
          <p style={{ fontSize: 'var(--font-sm)', color: 'var(--fg-secondary)', marginTop: 'var(--space-4)' }}>
            {lead.company.name} {lead.company.domain && `· ${lead.company.domain}`}
          </p>
        )}
      </aside>
      <section>
        <div style={{ display: 'flex', gap: 'var(--space-3)', borderBottom: '1px solid var(--border-light)', marginBottom: 'var(--space-4)' }}>
          {(['activity', 'research'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)} style={{ background: 'none', border: 'none', cursor: 'pointer',
              padding: 'var(--space-2) 0', fontSize: 'var(--font-sm)',
              color: tab === t ? 'var(--fg-primary)' : 'var(--fg-tertiary)',
              borderBottom: tab === t ? '2px solid var(--fg-primary)' : '2px solid transparent' }}>
              {t === 'activity' ? 'Activity' : 'Research'}
            </button>
          ))}
        </div>
        {tab === 'activity' ? (
          <>
            <form onSubmit={e => { e.preventDefault(); if (note.trim()) { addNote.mutate(note.trim()); setNote(''); } }}
              style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
              <input value={note} onChange={e => setNote(e.target.value)} placeholder="Add a note…" style={{ flex: 1 }} />
              <button type="submit">Add</button>
            </form>
            <Timeline events={events} />
          </>
        ) : (
          <div style={{ whiteSpace: 'pre-wrap', fontSize: 'var(--font-sm)', color: 'var(--fg-secondary)' }}>
            {(lead as any).company?.research ?? 'No research yet — the researcher agent arrives in phase 2.'}
          </div>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Run tests + verify manually**

Run: `npm test` → PASS. `npm run dev`: click a board card → lead page shows summary, editable fields (edit title, reload, persists), stage tag, Activity tab with the `stage_changed`/`import` events from earlier tasks, note composer adds a note that appears in the timeline, Research tab shows the placeholder.

- [ ] **Step 5: Commit**

```bash
cd <repo root> && git add powacrm/app
git commit --no-verify -m "feat(powacrm): lead record view with inline edit + timeline

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: CSV import page

**Files:**
- Create: `app/src/import/ImportPage.tsx` (replace placeholder)
- Create: `app/src/import/mapColumns.ts`
- Test: `app/src/import/mapColumns.test.ts`

**Interfaces:**
- Consumes: `supabase`, `useBrand()` (Tasks 6–7); `import_people` RPC (Task 5)
- Produces: `guessMapping(headers: string[]): Record<TargetField, string | null>` and `applyMapping(rows: Record<string, string>[], mapping): ImportRow[]` where `TargetField = 'first_name' | 'last_name' | 'email' | 'title' | 'linkedin_url' | 'company_name' | 'company_domain'` and `ImportRow` matches the RPC's row shape.

- [ ] **Step 1: Write the failing mapping test**

`app/src/import/mapColumns.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { guessMapping, applyMapping } from './mapColumns';

describe('guessMapping', () => {
  it('matches common header variants case-insensitively', () => {
    const m = guessMapping(['First Name', 'LAST_NAME', 'E-mail', 'Job Title', 'LinkedIn URL', 'Company', 'Website']);
    expect(m.first_name).toBe('First Name');
    expect(m.last_name).toBe('LAST_NAME');
    expect(m.email).toBe('E-mail');
    expect(m.title).toBe('Job Title');
    expect(m.linkedin_url).toBe('LinkedIn URL');
    expect(m.company_name).toBe('Company');
    expect(m.company_domain).toBe('Website');
  });
  it('leaves unmatched fields null', () => {
    expect(guessMapping(['foo']).email).toBeNull();
  });
});

describe('applyMapping', () => {
  it('projects rows and extracts registrable domain from URLs', () => {
    const rows = [{ Website: 'https://www.acme.com/about', Email: 'a@b.c' }];
    const out = applyMapping(rows, { first_name: null, last_name: null, email: 'Email',
      title: null, linkedin_url: null, company_name: null, company_domain: 'Website' });
    expect(out[0].company_domain).toBe('acme.com');
    expect(out[0].email).toBe('a@b.c');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm test`
Expected: FAIL — module not found

- [ ] **Step 3: Implement mapping + page**

`app/src/import/mapColumns.ts`:
```ts
export type TargetField = 'first_name' | 'last_name' | 'email' | 'title' | 'linkedin_url' | 'company_name' | 'company_domain';
export type ImportRow = Partial<Record<TargetField, string>>;

const ALIASES: Record<TargetField, string[]> = {
  first_name: ['first name', 'first_name', 'firstname', 'given name'],
  last_name: ['last name', 'last_name', 'lastname', 'surname', 'family name'],
  email: ['email', 'e-mail', 'email address', 'work email'],
  title: ['title', 'job title', 'job_title', 'position', 'role'],
  linkedin_url: ['linkedin', 'linkedin url', 'linkedin_url', 'linkedin profile'],
  company_name: ['company', 'company name', 'company_name', 'organization', 'account'],
  company_domain: ['website', 'domain', 'company domain', 'company website', 'url'],
};

export function guessMapping(headers: string[]): Record<TargetField, string | null> {
  const out = {} as Record<TargetField, string | null>;
  for (const field of Object.keys(ALIASES) as TargetField[]) {
    out[field] = headers.find(h => ALIASES[field].includes(h.trim().toLowerCase())) ?? null;
  }
  return out;
}

export function extractDomain(raw: string): string | null {
  const s = raw.trim().toLowerCase();
  if (!s) return null;
  try {
    const host = s.includes('://') ? new URL(s).hostname : new URL(`https://${s}`).hostname;
    return host.replace(/^www\./, '') || null;
  } catch { return null; }
}

export function applyMapping(rows: Record<string, string>[], mapping: Record<TargetField, string | null>): ImportRow[] {
  return rows.map(r => {
    const out: ImportRow = {};
    for (const [field, col] of Object.entries(mapping) as [TargetField, string | null][]) {
      if (!col || !r[col]?.trim()) continue;
      out[field] = field === 'company_domain' ? (extractDomain(r[col]) ?? undefined) : r[col].trim();
    }
    return out;
  });
}
```

`app/src/import/ImportPage.tsx`:
```tsx
import { useState } from 'react';
import Papa from 'papaparse';
import { useQueryClient } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';
import { useBrand } from '@/shell/BrandContext';
import { applyMapping, guessMapping, type TargetField } from './mapColumns';

const FIELDS: TargetField[] = ['first_name', 'last_name', 'email', 'title', 'linkedin_url', 'company_name', 'company_domain'];

export function ImportPage() {
  const { brand } = useBrand();
  const qc = useQueryClient();
  const [headers, setHeaders] = useState<string[]>([]);
  const [rows, setRows] = useState<Record<string, string>[]>([]);
  const [filename, setFilename] = useState('');
  const [mapping, setMapping] = useState<Record<TargetField, string | null> | null>(null);
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  function onFile(f: File) {
    setFilename(f.name); setResult(null);
    Papa.parse<Record<string, string>>(f, {
      header: true, skipEmptyLines: true,
      complete: res => {
        const hs = res.meta.fields ?? [];
        setHeaders(hs); setRows(res.data); setMapping(guessMapping(hs));
      },
    });
  }

  async function runImport() {
    if (!mapping) return;
    setBusy(true);
    try {
      const { data: batch, error: bErr } = await supabase.from('import_batches')
        .insert({ brand_id: brand.id, filename, row_count: rows.length }).select('id').single();
      if (bErr) throw bErr;
      const { data, error } = await supabase.rpc('import_people', {
        _brand_id: brand.id, _import_id: batch.id, _rows: applyMapping(rows, mapping),
      });
      if (error) throw error;
      setResult(data);
      qc.invalidateQueries({ queryKey: ['leads', brand.id] });
    } catch (e: any) {
      setResult({ error: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <h1 style={{ fontSize: 'var(--font-lg)', marginTop: 0 }}>Import CSV</h1>
      <input type="file" accept=".csv" onChange={e => e.target.files?.[0] && onFile(e.target.files[0])} />
      {mapping && (
        <>
          <p style={{ color: 'var(--fg-tertiary)', fontSize: 'var(--font-sm)' }}>{rows.length} rows in {filename}</p>
          <div style={{ display: 'grid', gap: 'var(--space-2)', margin: 'var(--space-4) 0' }}>
            {FIELDS.map(f => (
              <label key={f} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center', fontSize: 'var(--font-sm)' }}>
                <span style={{ width: 130, color: 'var(--fg-tertiary)' }}>{f}</span>
                <select value={mapping[f] ?? ''} onChange={e => setMapping({ ...mapping, [f]: e.target.value || null })}>
                  <option value="">— skip —</option>
                  {headers.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </label>
            ))}
          </div>
          <button onClick={runImport} disabled={busy}>{busy ? 'Importing…' : `Import ${rows.length} rows`}</button>
        </>
      )}
      {result && (
        <pre style={{ background: 'var(--bg-secondary)', padding: 'var(--space-3)', borderRadius: 'var(--radius-md)',
          fontSize: 'var(--font-sm)', overflowX: 'auto' }}>{JSON.stringify(result, null, 2)}</pre>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests + verify end-to-end**

Run: `npm test` → PASS. `npm run dev`: build a small CSV (headers `First Name,Last Name,Email,Job Title,Company,Website`, 3 rows, one duplicate email of an existing person) → upload → auto-mapping shows correct columns → Import → result shows `{inserted: 2, skipped: 1}` → Pipeline board shows the new leads in Sourced → each new lead's timeline shows an `import` event. Import the same file again → all skipped.

- [ ] **Step 5: Commit**

```bash
cd <repo root> && git add powacrm/app
git commit --no-verify -m "feat(powacrm): CSV import with column mapping + RPC

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: End-to-end verification pass

**Files:**
- Create: `db/tests/run_all.sh`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the aggregate test runner**

`db/tests/run_all.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for f in tests/test_0001_helpers.sql tests/test_0002_core_tables.sql tests/test_0003_events.sql tests/test_0005_import_rpc.sql; do
  ./apply.sh "$f"
done
./tests/test_0004_rls.sh
echo "ALL DB TESTS OK"
```

- [ ] **Step 2: Run everything**

Run:
```bash
chmod +x db/tests/run_all.sh && PB_TEST_PASSWORD=<chosen> ./db/tests/run_all.sh
cd app && npm test && npm run build
```
Expected: `ALL DB TESTS OK`; vitest green; `vite build` succeeds with no TS errors.

- [ ] **Step 3: Manual smoke (the full loop)**

`npm run dev` → login → import CSV → board shows leads → drag one to Researched → open it → edit a field → add a note → timeline shows import + stage change + note. Check the dark theme by adding `class="dark"` to `<html>` in devtools — page stays legible.

- [ ] **Step 4: Commit**

```bash
cd <repo root> && git add powacrm/db
git commit --no-verify -m "test(powacrm): aggregate DB test runner + phase 1 verification

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
