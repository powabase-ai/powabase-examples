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
