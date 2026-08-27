-- ============================================================================
-- PHASE 2 FIX: brands.research_daily_cap GETS A SERVER-SIDE CEILING.
--
-- `research_daily_cap` is the only number in this schema that decides how much
-- money the PROJECT OWNER spends. request_research() reads it and stops
-- inserting jobs once the brand has hit it for the UTC day; the worker then
-- drains that queue by calling the researcher agent, and every run bills
-- Firecrawl (`web_scrape`) and Exa (`web_search`) and a Sonnet run to the
-- project's platform credits.
--
-- Until this migration the column had NO constraint at all. `brands_upd`
-- (0009) lets an owner update their own brand row, and the Settings form
-- validated the field in the browser only -- so any signed-up account could
-- send one PostgREST request:
--
--   PATCH /rest/v1/brands?id=eq.<their own brand>  {"research_daily_cap": 100000}
--
-- and lift its own ceiling to whatever it liked. This was reproduced live with
-- a freshly signed-up account, at 100000 and at -5. Phase 1's isolation held
-- perfectly -- the stranger still saw only their own rows -- but from phase 2
-- onward "only your own rows" stopped being the whole story, because their own
-- rows are now a spending instruction aimed at someone else's wallet. The
-- worker drains one job a minute PROJECT-WIDE, so an unbounded cap converts
-- directly into roughly 1,440 paid agent runs a day on the owner's credits.
--
-- WHY 100. It is four times the shipped default of 25, which is already more
-- companies than a seller reads through in a day, so the ceiling never gets in
-- the way of real use. It is far enough below the worker's physical throughput
-- (~1,440/day) that one rogue brand cannot monopolise the queue. And it makes
-- the worst case a bounded, affordable number instead of an unbounded one.
-- Raise it deliberately in a migration if a real deployment needs more; that is
-- the point of putting the ceiling in the schema rather than in the form.
-- 0 stays legal: it is how you pause research for a brand without deleting it.
--
-- WHAT THIS DOES NOT FIX. `brands_ins` lets an account create as many brands as
-- it likes, each with its own cap, so this bounds per-brand spend, not
-- per-account spend. There is no cheap CHECK-shaped answer to that -- a
-- constraint cannot count rows. The real control is the one the README now
-- states plainly: if research is enabled, close public signups unless you
-- actually want strangers holding accounts on your project.
-- ============================================================================

BEGIN;

-- Any row already outside the range (e.g. one an account raised before this
-- migration existed) is clamped first -- ADD CONSTRAINT validates existing rows
-- and would otherwise fail on exactly the databases that most need this.
UPDATE brands
   SET research_daily_cap = least(greatest(research_daily_cap, 0), 100)
 WHERE research_daily_cap < 0 OR research_daily_cap > 100;

ALTER TABLE brands DROP CONSTRAINT IF EXISTS brands_research_daily_cap_range;
ALTER TABLE brands ADD CONSTRAINT brands_research_daily_cap_range
  CHECK (research_daily_cap BETWEEN 0 AND 100);

COMMENT ON COLUMN brands.research_daily_cap IS
  'Max research jobs this brand may enqueue per UTC day. Every run spends the PROJECT OWNER''s platform credits, so the value is bounded 0-100 by brands_research_daily_cap_range -- the browser form is a convenience, not the enforcement point. Enforced per-request inside request_research().';

COMMENT ON CONSTRAINT brands_research_daily_cap_range ON brands IS
  'Ceiling on owner-billed research spend. Without it any signed-up account could PATCH its own brand to an arbitrary cap and drain the project''s credits.';

COMMIT;
