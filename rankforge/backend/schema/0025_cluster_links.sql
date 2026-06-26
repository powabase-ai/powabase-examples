-- 0025 — cluster-aware internal links.
--
-- Internal linking now respects clusters: structural UP-links (member → its pillar)
-- and DOWN-links (pillar → its members) are prioritized over incidental mention-based
-- links. When a structural link has no natural anchor in the prose, we stage a GAP
-- (anchor_text NULL) the editor can fill with an opt-in LLM-written contextual link.
--
-- Apply: uv run python scripts/apply_schema.py schema/0025_cluster_links.sql

begin;

-- 'mention' (incidental), 'pillar' (a member's up-link), 'member' (a pillar's down-link)
alter table public.link_suggestions
    add column if not exists kind text not null default 'mention';

-- A gap suggestion has no anchor yet (the prose doesn't mention the target).
alter table public.link_suggestions alter column anchor_text drop not null;

-- One suggestion per (article, target, anchor); coalesce so a single anchor-less gap
-- per (article, target) is enforced too.
drop index if exists link_suggestions_unique_idx;
create unique index if not exists link_suggestions_unique_idx
    on public.link_suggestions
       (article_id, target_article_id, lower(coalesce(anchor_text, '')));

notify pgrst, 'reload schema';

commit;
