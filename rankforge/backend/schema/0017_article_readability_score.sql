-- 0017 — Readability as a first-class evaluation dimension.
--
-- Alongside SEO / GEO / Grounding, store a Readability score that judges how human
-- the article reads (search engines now penalize AI-generated-sounding content).
-- Same shape as seo_score/geo_score: { total, target, met, signals: [...] }.
--
-- Apply: uv run python scripts/apply_schema.py schema/0017_article_readability_score.sql

begin;

alter table public.articles
    add column if not exists readability_score jsonb;

notify pgrst, 'reload schema';

commit;
