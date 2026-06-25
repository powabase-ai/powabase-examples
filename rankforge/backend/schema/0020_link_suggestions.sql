-- 0020 — internal-link suggestions (M6, Phase 12.1): weave links between the brand's
-- own published articles.
--
-- Drafts already link to brand MATERIALS at write time; this adds cross-article
-- INTERNAL linking — a staged suggestion that an anchor span in one article should
-- link to another of the brand's published articles. Suggestions are reviewed (never
-- auto-applied to published content); accepting one inserts the markdown link and
-- re-scores the article.
--
-- Apply: uv run python scripts/apply_schema.py schema/0020_link_suggestions.sql

begin;

create table if not exists public.link_suggestions (
    id                uuid primary key default gen_random_uuid(),
    business_id       uuid not null
                      references public.business_profiles (id) on delete cascade,
    article_id        uuid not null
                      references public.articles (id) on delete cascade,
    target_article_id uuid not null
                      references public.articles (id) on delete cascade,
    anchor_text       text not null,        -- the verbatim span in article_id to link
    target_url        text not null,        -- public URL of target_article_id (/p/{id})
    target_title      text,
    reason            text,
    status            text not null default 'pending'
                      check (status in ('pending', 'accepted', 'dismissed')),
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);
create index if not exists link_suggestions_article_idx
    on public.link_suggestions (article_id, status);
create index if not exists link_suggestions_business_idx
    on public.link_suggestions (business_id, status, created_at desc);
-- One suggestion per (article, target, anchor) — re-running the suggester is
-- idempotent instead of piling up duplicates.
create unique index if not exists link_suggestions_unique_idx
    on public.link_suggestions (article_id, target_article_id, lower(anchor_text));

-- RLS: org-scoped through the owning business (the backend bypasses RLS and enforces
-- in-app; this guards any direct PostgREST access). Mirrors 0018's child-table pattern.
alter table public.link_suggestions enable row level security;
drop policy if exists link_suggestions_read on public.link_suggestions;
create policy link_suggestions_read on public.link_suggestions for select to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()));
drop policy if exists link_suggestions_write on public.link_suggestions;
create policy link_suggestions_write on public.link_suggestions for all to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()))
    with check (business_id in (select id from public.business_profiles
                                where org_id = public.current_org_id()));

notify pgrst, 'reload schema';

commit;
