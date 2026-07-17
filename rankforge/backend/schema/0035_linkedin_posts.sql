-- Per-article LinkedIn post variants. Each row is one generated (then editable) post,
-- attached to an article and owned by its brand's org. Deleting the article cascades.
create table if not exists public.linkedin_posts (
    id           uuid primary key default gen_random_uuid(),
    article_id   uuid not null references public.articles (id) on delete cascade,
    -- Denormalized brand id so RLS can scope directly by org (mirrors articles/
    -- opportunities); the app layer still guards via _guard_article.
    business_id  uuid not null references public.business_profiles (id) on delete cascade,
    angle        text not null,          -- one of the presets; enforced by the CHECK below
    body         text not null default '',
    created_by   uuid references auth.users (id) on delete set null,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index if not exists linkedin_posts_article_idx
    on public.linkedin_posts (article_id, created_at desc);

-- Constrain angle to the five presets (matches models/linkedin.py ANGLE_SLUGS).
-- Named + guarded so it applies once to both fresh and already-created tables.
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'linkedin_posts_angle_check'
    ) then
        alter table public.linkedin_posts
            add constraint linkedin_posts_angle_check
            check (angle in ('key_insight', 'lesson', 'contrarian', 'story', 'stat'));
    end if;
end $$;

alter table public.linkedin_posts enable row level security;
-- Org-scoped (defense-in-depth; the app layer is primary). Mirrors other content tables.
create policy linkedin_posts_rw on public.linkedin_posts
    for all to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()))
    with check (business_id in (select id from public.business_profiles
                                where org_id = public.current_org_id()));
