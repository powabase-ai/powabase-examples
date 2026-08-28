-- ============================================================================
-- PHASE 2: RESEARCH SCHEMA.
--
-- Research belongs to the COMPANY, not the person: ten leads at one company
-- cost one research pass. The fit score lands on each person, because fit
-- depends on their title.
--
-- research_jobs is a queue, and it is the whole authorization story. The browser
-- cannot hold the service key (it would be readable in the bundle) and a webhook
-- carries no user identity, so neither can be used to start a run on behalf of a
-- specific user. Instead the SPA calls request_research() (0012), which decides
-- ownership the same way every other write in this schema does, and the worker
-- reads the brand off the claimed row. No caller-supplied brand_id is trusted --
-- that was the 0010 bug, and this is the structural version of that fix.
-- ============================================================================

BEGIN;

ALTER TABLE brands
  ADD COLUMN IF NOT EXISTS icp_notes text,
  ADD COLUMN IF NOT EXISTS research_daily_cap int NOT NULL DEFAULT 25;

COMMENT ON COLUMN brands.icp_notes IS
  'Plain-English ICP the researcher scores against. Per-brand, so this example app is not hardcoded to one company.';
COMMENT ON COLUMN brands.research_daily_cap IS
  'Max research jobs this brand may enqueue per UTC day. Every run spends credits; enforced inside request_research().';

ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS research_data jsonb,
  ADD COLUMN IF NOT EXISTS researched_at timestamptz;

COMMENT ON COLUMN companies.research_data IS
  'Validated structured output: why_now, hooks[] with evidence and source_url, sources[], injection_observed.';

CREATE TABLE IF NOT EXISTS research_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  created_by_source text NOT NULL DEFAULT 'MANUAL'
    CHECK (created_by_source IN ('MANUAL','API','WORKFLOW','AGENT','IMPORT','WEBHOOK','SYSTEM')),
  created_by_name text NOT NULL DEFAULT 'System',
  created_by_context jsonb,
  brand_id uuid NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  requested_by uuid,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','running','done','failed','skipped')),
  attempts int NOT NULL DEFAULT 0,
  error text,
  started_at timestamptz,
  finished_at timestamptz
);
DROP TRIGGER IF EXISTS research_jobs_updated ON research_jobs;
CREATE TRIGGER research_jobs_updated BEFORE UPDATE ON research_jobs
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Double-spend is prevented by the database, not by a check someone can forget.
CREATE UNIQUE INDEX IF NOT EXISTS research_jobs_one_active
  ON research_jobs (company_id) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS research_jobs_queue_idx
  ON research_jobs (status, created_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS research_jobs_brand_idx ON research_jobs (brand_id, created_at DESC);

ALTER TABLE research_jobs ENABLE ROW LEVEL SECURITY;
-- Read-only for owners. There is deliberately NO insert/update/delete policy:
-- the RPCs in 0012 are the only writers, so the daily cap cannot be sidestepped
-- by crafting an insert.
DROP POLICY IF EXISTS research_jobs_sel ON research_jobs;
CREATE POLICY research_jobs_sel ON research_jobs
  FOR SELECT TO authenticated USING (owns_brand(brand_id));

INSERT INTO event_types (name, verb, label, icon)
SELECT 'researched', 'researched', 'Researched', 'sparkles'
WHERE NOT EXISTS (SELECT 1 FROM event_types WHERE name = 'researched');

COMMIT;
