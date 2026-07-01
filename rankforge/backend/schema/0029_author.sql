-- Brand-level default author byline + per-article override.
-- Export frontmatter `author` resolves: article.author → brand.default_author → fallback.
alter table public.business_profiles
    add column if not exists default_author text;

alter table public.articles
    add column if not exists author text;
