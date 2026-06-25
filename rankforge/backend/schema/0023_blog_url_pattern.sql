-- 0023 — canonical published-article URLs (M6 / Phase 12.1, generalized).
--
-- Internal links must point at where an article ACTUALLY lives, which is rarely
-- RankForge's own /p/{id} page once a brand runs its own blog (Keystatic, Astro,
-- Hugo, WordPress, …). Rather than per-CMS adapters, a brand declares a URL PATTERN
-- ("https://blog.example.com/{slug}") and we resolve every published article's URL
-- from it — with a per-article override for one-offs / exact CMS URLs.
--
--   resolution: articles.canonical_url  ->  business_profiles.url_pattern rendered
--   tokens: {slug}, {id}
--
-- Apply: uv run python scripts/apply_schema.py schema/0023_blog_url_pattern.sql

begin;

-- The brand's published-blog URL template, e.g. 'https://blog.powabase.ai/{slug}'.
-- NULL means internal linking is not yet enabled for the brand (we require a pattern).
alter table public.business_profiles
    add column if not exists url_pattern text;

-- Per-article canonical URL: an explicit override that wins over the pattern (manual
-- correction, or auto-filled by a future publish adapter that returns the real URL).
alter table public.articles
    add column if not exists canonical_url text;

notify pgrst, 'reload schema';

commit;
