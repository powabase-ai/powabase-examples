-- RankForge 0005 — article-type templates + brief.article_type.
-- Apply: scripts/apply_schema.py schema/0005_content_templates.sql

begin;

create table if not exists public.content_templates (
    id                 uuid primary key default gen_random_uuid(),
    type               text unique not null,
    label              text not null,
    outline_guidance   text not null,           -- injected into the brief prompt
    schema_org_type    text not null default 'BlogPosting',
    default_word_count int,
    geo_target         int not null default 85,
    enabled            boolean not null default true,
    created_at         timestamptz not null default now()
);

alter table public.briefs add column if not exists article_type text;

alter table public.content_templates enable row level security;
do $$
begin
    if not exists (select 1 from pg_policies where schemaname='public'
        and tablename='content_templates' and policyname='content_templates_read') then
        create policy content_templates_read on public.content_templates
            for select to authenticated using (true);
    end if;
    if not exists (select 1 from pg_policies where schemaname='public'
        and tablename='content_templates' and policyname='content_templates_write') then
        create policy content_templates_write on public.content_templates
            for all to authenticated using (true) with check (true);
    end if;
end $$;

insert into public.content_templates
    (type, label, outline_guidance, schema_org_type, default_word_count, geo_target)
values
('general', 'General article',
 'Use the most natural structure for the topic: a clear intro, well-organized H2 sections with H3 subsections where useful, and a conclusion.',
 'BlogPosting', 2200, 85),
('listicle', 'Listicle',
 'Structure as a numbered list: a short intro, then each item as its own H2 (e.g. "1. ...", "2. ..."), each with a consistent shape (what it is, pros/cons, who it''s for), then a brief conclusion.',
 'ItemList', 2500, 85),
('how_to', 'How-to guide',
 'Structure as a step-by-step guide: an intro with the outcome, an optional "What you''ll need" section, sequential H2 steps in order, a tips section, and a short FAQ.',
 'HowTo', 2000, 86),
('checklist', 'Checklist',
 'Structure as an actionable checklist: an intro, grouped checklist items as H2 groups with H3 or bulleted check items, and a concise summary the reader can act on.',
 'ItemList', 1800, 85),
('qa', 'Q&A article',
 'Structure as questions and answers: each H2 is a real question the reader asks, answered with a concise extractable lead then expanded; include a dedicated FAQ section.',
 'FAQPage', 2000, 88),
('versus', 'Versus / comparison',
 'Structure as a head-to-head comparison: an intro, an at-a-glance comparison table, H2 sections per dimension (pricing, features, performance, support), and a clear verdict.',
 'Article', 2200, 85),
('roundup', 'Roundup',
 'Structure as an expert/product roundup: an intro with the selection criteria, one H2 per entry evaluated against the same criteria, and a summary "best for" pick.',
 'ItemList', 2500, 84),
('ultimate_guide', 'Ultimate guide',
 'Structure as a comprehensive pillar guide: an intro, broad H2 sections covering the whole topic each with H3 subsections, and a conclusion. Aim for depth and completeness.',
 'Article', 3500, 86),
('news', 'News article',
 'Structure as a news piece using the inverted pyramid: the key facts first, then context and background, relevant detail/quotes, and implications.',
 'NewsArticle', 1200, 82),
('youtube_article', 'YouTube-to-article',
 'Structure as an article derived from a video: a summary, key takeaways as H2 sections, and a deeper walk-through; reference the video where relevant.',
 'Article', 1800, 83)
on conflict (type) do nothing;

commit;
