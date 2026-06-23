-- 0007 — editorial collaboration: review comments.
-- profiles (with role writer|editor|admin) and the article status lifecycle
-- already exist (0001). This adds inline review comments on articles.
--
-- Apply: psql "$POWABASE_DATABASE_URL" -f backend/schema/0007_authz_comments.sql

begin;

create table if not exists public.article_comments (
    id          uuid primary key default gen_random_uuid(),
    article_id  uuid not null references public.articles (id) on delete cascade,
    author_id   uuid references auth.users (id) on delete set null,
    body        text not null,
    anchor      text,                                    -- quoted text / section
    resolved    boolean not null default false,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists article_comments_article_idx
    on public.article_comments (article_id, created_at);

-- Team-visible reads, authenticated writes (the trusted backend uses the Service
-- Role connection and bypasses RLS; these guard any direct PostgREST access).
alter table public.article_comments enable row level security;
drop policy if exists article_comments_read on public.article_comments;
create policy article_comments_read on public.article_comments
    for select to authenticated using (true);
drop policy if exists article_comments_write on public.article_comments;
create policy article_comments_write on public.article_comments
    for all to authenticated using (true) with check (true);

notify pgrst, 'reload schema';

commit;
