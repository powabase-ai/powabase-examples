# LinkedIn Post Generator — Design Spec

**Date:** 2026-07-15
**Branch:** `feat/linkedin-post`
**Status:** Approved (brainstorming) — pending spec review

## Summary

Let a workspace generate insightful **LinkedIn posts** from a selected blog article. Each
post is written in the brand's voice — insightful, positive-but-not-salesy, and free of
AI tells (reusing the article writer's anti-AI-tell guidance). Users pick an **angle**,
generate a **variant**, **edit** the text, **copy** it to the clipboard, and **delete**
variants they don't want. Posts are per-article and shared across the workspace (org).

## Decisions (from brainstorming)

1. **Post model:** *Multiple variants per article* — a `linkedin_posts` child table; each
   Generate adds a new row; edit/delete/copy each independently.
2. **Steering:** *Preset angle picker* — a fixed set of angles shapes the prompt; the
   chosen angle is stored on the variant and shown as a badge.
3. **Content:** posts include *a few relevant hashtags* **and** *a soft link to the
   published article* (link only when the article is actually published).
4. **Permissions:** *any workspace member* can generate / edit / delete; everyone in the
   org can view and copy. (No editor gate — the most collaborative option.)

## Scope

**In scope:** generate (LLM), list, edit body, delete, copy-to-clipboard; a new
"LinkedIn" tab on the article page.

**Out of scope (explicit):** real LinkedIn publishing / OAuth / their API. A "post" is
text drafted here and pasted into LinkedIn manually. Sharing = belonging to the org (all
content is already org-scoped); no separate sharing mechanism. No scheduling, no
analytics, no images.

## Architecture

Standard per-article child resource (mirrors `article_comments`) plus one synchronous
LLM generation endpoint (mirrors the single-shot FAQ/meta pattern, not the async article
pipeline).

```
[Article page ▸ "LinkedIn" tab]
   └─ LinkedInPanel (angle picker + Generate; variant cards: edit/copy/delete)
        └─ linkedInApi / useLinkedIn* hooks  (query key ["linkedin", articleId])
             └─ /api/articles/{id}/linkedin-posts   (routes/articles.py, _guard_article)
                  ├─ services/linkedin_posts.py      (CRUD over public.linkedin_posts)
                  └─ services/linkedin_gen.py        (prompt build + run_agent, opus-4-7)
                       ├─ generation.get_article      (content_md, title, business_id)
                       ├─ business_profiles.get_profile (voice: description/niche/audience/author)
                       └─ linking.canonical_url / public_base_url (published article link)
```

## Data model

New migration `backend/schema/0035_linkedin_posts.sql`:

```sql
create table if not exists public.linkedin_posts (
    id           uuid primary key default gen_random_uuid(),
    article_id   uuid not null references public.articles (id) on delete cascade,
    -- Denormalized for direct org-scoped RLS (mirrors articles/opportunities); the app
    -- layer still guards via _guard_article. Kept consistent with the article's brand.
    business_id  uuid not null references public.business_profiles (id) on delete cascade,
    angle        text not null,          -- slug; validated in app (see Angles)
    body         text not null default '',
    created_by   uuid references auth.users (id) on delete set null,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists linkedin_posts_article_idx
    on public.linkedin_posts (article_id, created_at desc);

alter table public.linkedin_posts enable row level security;
-- Org-scoped (defense-in-depth; app layer is primary). Mirrors other content tables:
create policy linkedin_posts_rw on public.linkedin_posts
    for all to authenticated
    using (business_id in (select id from public.business_profiles
                           where org_id = public.current_org_id()))
    with check (business_id in (select id from public.business_profiles
                                where org_id = public.current_org_id()));
```

Deleting the article cascades its posts (add `linkedin_posts` to the cascade note in
`generation.delete_article`'s docstring).

## Angles

Five presets. Stored as the slug; label shown as a badge; each maps to a prompt clause.

| Slug | Label | Prompt intent |
|---|---|---|
| `key_insight` | Key insight | Lead with the single most valuable, non-obvious takeaway. |
| `lesson` | Lesson learned | Frame as a hard-won lesson / mistake-to-fix. |
| `contrarian` | Contrarian take | Challenge a common assumption the article pushes back on. |
| `story` | Story / behind-the-scenes | Open with a concrete moment or scenario, then the point. |
| `stat` | Stat highlight | Anchor on a specific number/finding from the article. |

Default in the UI: `key_insight`. The set is a shared constant used by both the Pydantic
`Literal` and the frontend picker.

## Generation

**Service** `services/linkedin_gen.py`:
- Constants: `LINKEDIN_MODEL = "claude-opus-4-7"` (the writer model — chosen for voice
  fidelity, the top requirement), `LINKEDIN_AGENT_NAME = "rankforge-linkedin"`.
- `ensure_linkedin_agent(client)` → `ensure_agent(..., system_prompt=_SYSTEM, settings={"temperature": 0.5, "max_tokens": 1200})`.
- `generate_post(client, db, article_id, angle) -> str`:
  1. `article = gen.get_article(db, article_id)`; require non-empty `content_md` (else the
     route 409s). `brand = business_profiles.get_profile(db, article["business_id"])`.
  2. Resolve the published article URL: `linking.canonical_url(brand, article)` →
     else `"{public_base_url}/p/{id}"` **only if** `article["status"] == "published"`;
     otherwise `None` (no link line).
  3. Build the user message from: title, `content_md[:16000]`, brand
     `description`/`niche`/`audience`, resolved author, the angle clause, and — when
     present — the article URL to append.
  4. `res = await client.run_agent(agent_id, msg)`; `text = (res.get("content") or "").strip()`.
  5. Guard: if `text` is empty → raise `RuntimeError` (surfaces as 502). Return `text`.

**System prompt** `_SYSTEM` reuses the writer's guidance (condensed), adapted to LinkedIn:
- Speak **as** the brand, first person ("we"/"our"), never a detached third party; never
  hedge the brand's own capabilities.
- Name competitors if needed but never praise/showcase them.
- Anti-AI-tell: the banned-words list and banned-constructions list from
  `generation._SYSTEM_PROMPT` (delve/leverage/robust/seamless/…; "it's not just X, it's
  Y"; the antithesis reframe; "Let's dive in"; rule-of-three; empty transitions), em-dash
  restraint, varied sentence length, concrete specifics over generic filler.
- LinkedIn format: a strong **hook first line**, short lines / whitespace between
  thoughts, ~1,300-character sweet spot (hard cap ~3,000), insightful and useful — **no
  hard CTA, not salesy**. Ground every claim in the article; invent nothing.
- **Fixed trailing order** (so every post is consistent): post body → blank line →
  (if an article URL is provided) a soft `Full write-up → {url}` line → blank line →
  **3–5** relevant, specific hashtags (no generic spam). When no URL is provided, the
  link line is omitted and the hashtags follow the body directly.

Non-goals: no separate fact-check pipeline (the source is our own vetted article); no
streaming (short output).

## API & permissions

All under `routes/articles.py`, prefix `/api/articles/{article_id}/linkedin-posts`, each
calling `_guard_article(db, article_id, user)` first (org access → **404** cross-org).
Auth = `get_current_user` only (any workspace member), matching the collaborative choice.

| Method | Path | Body | Returns | Notes |
|---|---|---|---|---|
| GET | `` | — | `list[LinkedInPost]` | newest first |
| POST | `` | `LinkedInGenerate{angle}` | `LinkedInPost` (201) | LLM; `rate_limit("linkedin:generate")`; 409 if article has no content; 502 on upstream failure |
| PATCH | `/{post_id}` | `LinkedInUpdate{body}` | `LinkedInPost` | edit text |
| DELETE | `/{post_id}` | — | 204 | |

Pydantic (`models/linkedin.py`):
- `Angle = Literal["key_insight","lesson","contrarian","story","stat"]`
- `LinkedInGenerate{ angle: Angle }`
- `LinkedInUpdate{ body: str = Field(min_length=1, max_length=3000) }`
- `LinkedInPost{ id, article_id, angle, body, created_by, created_at, updated_at }`

Service `services/linkedin_posts.py` (mirrors `comments.py`): `list_posts`, `get_post`,
`create_post(article_id, business_id, angle, body, author_id)`, `update_post(id, body)`,
`delete_post(id)`. No org logic in the service (guarded in the route).

Post-`post_id` mutations verify the post belongs to the guarded article (404 otherwise),
mirroring the comment endpoints.

## Frontend

**Tab:** add `"LinkedIn"` to the eval-sidebar tab tuple and state union in
`articles/[articleId]/page.tsx`, plus a body branch rendering `<LinkedInPanel articleId brandId />` alongside `CommentsPanel`.

**`components/LinkedInPanel.tsx`:**
- Header: angle `<select>` (5 presets) + **Generate variant** button (spinner while
  pending; subtle "uses credits" hint). Disabled with a hint if the article isn't `done`
  / has no content.
- List of variant cards (newest first), each:
  - angle **badge** + created date/author,
  - editable `<Textarea>` seeded from `body`, **Save** (dirty-tracked) via PATCH,
  - live **char count** with ~1,300 sweet-spot and 3,000 hard-limit hints,
  - **Copy** (`navigator.clipboard.writeText` + sonner toast),
  - **Delete** (with confirm) via DELETE.
- Empty state: "No posts yet — pick an angle and generate one."

**`lib/api.ts`** `linkedInApi`: `list(articleId)`, `generate(articleId, angle)`,
`update(articleId, postId, body)`, `remove(articleId, postId)` + `LinkedInPost` /
`Angle` types + an `ANGLES` label map.

**`lib/hooks/useLinkedIn.ts`:** `useLinkedInPosts` (query `["linkedin", articleId]`),
`useGenerateLinkedInPost`, `useUpdateLinkedInPost`, `useDeleteLinkedInPost` — mutations
invalidate/patch the list cache.

## Error handling

- Cross-org access → 404 (via `_guard_article`), never 403 (no id probing).
- Article has no `content_md` yet → **409** ("generate the article first").
- LLM upstream failure or empty output → **502** + friendly toast.
- Rate limit exceeded → 429 + `Retry-After` (existing `rate_limit` machinery).
- Edit body empty or >3,000 chars → 422 (schema), surfaced as a toast.
- Clipboard failure (permissions) → toast; the text stays editable to copy manually.

## Testing

**Backend (hermetic, mock at the agent/db boundary):**
- `generate` happy path (mock `run_agent` → text; asserts a row is inserted with the
  angle and returned).
- `generate` 409 when the article has no content.
- `generate` 502 when the agent returns empty / raises.
- angle validation → 422 for an unknown angle.
- list / edit / delete happy paths; edit body length 422.
- org guard → 404 for a cross-org article; `_guard`-style 404 when the post isn't on the
  named article.
- The **prompt/link/hashtag assembly** extracted as a pure function
  (`build_linkedin_prompt(...)`) with unit tests: angle clause present; link line present
  only when a URL is supplied; input truncated to 16k.

**Frontend:** no test runner exists (documented gap). The pure prompt-assembly lives
server-side so it is covered; UI is manually verified.

## File-by-file change list

Backend:
- `schema/0035_linkedin_posts.sql` (new)
- `models/linkedin.py` (new)
- `services/linkedin_posts.py` (new — CRUD)
- `services/linkedin_gen.py` (new — prompt + generation)
- `routes/articles.py` (add the 4 endpoints + constants)
- `services/generation.py` (docstring note: linkedin_posts cascades on article delete)
- `tests/test_linkedin.py` (new)

Frontend:
- `lib/api.ts` (`linkedInApi`, types, `ANGLES`)
- `lib/hooks/useLinkedIn.ts` (new)
- `components/LinkedInPanel.tsx` (new)
- `app/brands/[id]/articles/[articleId]/page.tsx` (add the tab + body branch)

## Open questions

None blocking. Model tier (`opus-4-7`) and copy-to-clipboard scope confirmed during
brainstorming.
