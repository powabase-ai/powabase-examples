-- Source trust/authority scoring for research sources.
--
-- On each research run the source-quality judge rates every scraped source 0-100 for
-- authority + trustworthiness as a citable editorial source (high = primary sources,
-- official docs, reputable publications; low = thin/low-DA SEO or affiliate blogs).
-- Low-scoring sources are pruned and the run backfills higher-authority replacements,
-- so the writer grounds on solid sources rather than content farms. Both columns are
-- nullable — a source scored before this feature (or with evaluation disabled) simply
-- carries no score.
alter table public.research_sources
    add column if not exists trust_score smallint,
    add column if not exists trust_reason text;
