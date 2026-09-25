-- Per-brand blog profile (target-blog conventions) + the article fields it drives.
-- All nullable: a brand with no profile behaves exactly as before.
alter table public.business_profiles add column if not exists blog_profile jsonb;
alter table public.content_clusters  add column if not exists category text;
alter table public.articles
    add column if not exists category text,
    add column if not exists summary  text,
    add column if not exists faq      jsonb;          -- [{ "q": str, "a": str }]
alter table public.article_versions
    add column if not exists frontmatter jsonb;       -- snapshot restored by revert

-- Hub-page link suggestions have no target ARTICLE. Relax the not-null and dedupe
-- them on (article, url, anchor) instead of (article, target_article, anchor).
alter table public.link_suggestions alter column target_article_id drop not null;
create unique index if not exists link_suggestions_hub_uniq
    on public.link_suggestions (article_id, target_url, lower(coalesce(anchor_text, '')))
    where target_article_id is null;
