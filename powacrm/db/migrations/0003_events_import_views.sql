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
