# Per-brand blog profile + instruction-driven refine — Design

**Date:** 2026-09-25
**Status:** Implemented (PR #26)
**Repo:** `example-apps/rankforge`

## Problem

The Powabase website changed how its blog works (website PRs #65–#75, Sept 2026):

- Every post needs a `category:` from a fixed list, or the build fails.
- Titles must be ≤60 characters and descriptions ≤160, checked at prebuild by
  `scripts/check-meta.ts`. A longer H1 needs a `metaTitle:`.
- Posts carry a 40–60 word, answer-first `summary:`, shown as the "Short answer"
  box. It is the block AI answer engines quote.
- Posts carry 3–6 `faq:` items in frontmatter. The site renders them as an
  accordion and emits FAQPage JSON-LD itself, so an FAQ section in the body shows
  twice.
- The site adds "Keep reading" on its own. Posts should have 3–5 contextual
  in-body links spread across the technical categories, plus links to the site's
  hub pages. Internal URLs use a trailing slash.
- The copy favors Powabase: no stated gaps or limitations in the summary or FAQ.

RankForge's `.mdx` export has none of this, so every exported article needs manual
prep before the website builds. Separately, refine can only fix score signals. You
can't tell it in your own words what to change.

## Goals

1. A RankForge article exported as `.mdx` drops into the website's `content/blog/`
   and passes `npm run build` with no manual edits.
2. These rules are **per-brand configuration**, not Powabase-specific code.
   RankForge is a multi-brand, open-source example app. A brand with no blog
   profile behaves exactly as today.
3. You can refine or rework an article from free-text instructions, and undo it.

## Decisions (from brainstorming)

- **Config shape:** a per-brand `blog_profile`. We rejected an export-format
  preset and hardcoding for Powabase.
- **Instructed refine:** two modes, `refine` (light edit) and `rework` (may
  restructure). No score veto. Undo goes through version history, with a
  one-click revert. Instructions may also change title, meta, summary and FAQ.
- **Existing posts:** the website repo owns them. New fields apply to new
  articles and to articles you touch from now on. Existing articles get
  summary/FAQ only when you ask, per article. Relink output is staged and exported
  as patch notes, never applied or re-exported automatically.

## Out of scope

- RankForge Task 14: creating the four technical clusters and pillars and doing a
  relink run. That's data entry and operation inside RankForge once this ships.
- Importing website `.mdx` back into RankForge.
- A GitHub-PR publish target. Export stays download/webhook.
- A diff preview or accept/reject UI for instructed refine.

---

## 1. Blog profile

### Data model (migration `0036_blog_profile.sql`)

```sql
alter table public.business_profiles add column if not exists blog_profile jsonb;
alter table public.content_clusters  add column if not exists category text;
alter table public.articles
  add column if not exists category text,
  add column if not exists summary  text,
  add column if not exists faq      jsonb;   -- [{ "q": str, "a": str }]
alter table public.article_versions
  add column if not exists frontmatter jsonb;  -- revert restores it (§5)
-- + link_suggestions changes (§3)
```

No new tables, so RLS is unchanged. `_ARTICLE_COLUMNS` and the article models gain
the three new fields.

### `blog_profile` shape (Pydantic `BlogProfile`, `models/blog.py`)

```jsonc
{
  "categories": [                       // 1..20, keys unique, slug-shaped
    { "key": "rag", "label": "RAG & Retrieval",
      "description": "…", "technical": true }
  ],
  "summary": { "enabled": true, "min_words": 40, "max_words": 60 },
  "faq":     { "enabled": true, "min": 3, "max": 6 },
  "meta":    { "title_max": 60, "description_max": 160 },
  "links": {
    "min": 3, "max": 5, "trailing_slash": true,
    "hub_pages": [                      // 0..50
      { "path": "/vector-database/", "title": "Vector database on Postgres",
        "topics": ["vector database", "pgvector"] }
    ]
  },
  "stance": "favor_brand"               // "neutral" | "favor_brand"
}
```

Validation:
- Every model is `extra="forbid"` (`_Strict` base): a misspelled key (`link` for
  `links`) raises instead of silently falling back to defaults with the export
  rules switched off.
- `min ≤ max` everywhere.
- `hub_pages[].path` starts with `/` and is rejected if it starts with `//` or
  `/\` (protocol-relative — the link would leave the brand's site).
- `topics` holds 1–10 phrases, each 4–80 characters. The same minimum as the
  linker's `_MIN_ANCHOR_LEN` keeps generic matches out.

Hub paths render against the brand's `domain`. A hub is a site page, not an
article, so `url_pattern` doesn't apply.

The profile is set through the existing business-profile create/update routes,
where `blog_profile` is optional and nullable.

### Powabase seed (`scripts/seed_powabase_blog_profile.py`)

`uv run python scripts/seed_powabase_blog_profile.py --brand-id <uuid>` writes the
Powabase profile to exactly one brand, by id (names aren't unique across orgs):
- the six website categories, with `rag`, `agents`, `backend` and `coding-agents`
  marked `technical`, `models` and `enterprise` not;
- the ten hub pages: `/supabase-alternative/`, `/firebase-alternative/`,
  `/convex-alternative/`, `/neon-alternative/`, `/pinecone-alternative/`,
  `/langchain-alternative/`, `/backend-as-a-service/`, `/self-hosted-supabase/`,
  `/vector-database/`, `/free-mvp/`, each with topic phrases;
- summary 40–60 words, FAQ 3–6, meta 60/160, links 3–5 with a trailing slash;
- stance `favor_brand`.

It validates the profile through `BlogProfile` and stores the validated
`model_dump()` (defaults filled in), not the raw literal. It also sets
`url_pattern` to `https://powabase.ai/blog/{slug}/` if the brand's is empty. One
transaction: it rolls back and exits non-zero unless exactly one row matched the
given id, so it can never silently update zero or several brands.

## 2. Generation

All of the changes below apply **only when the brand has a `blog_profile`**.
Without one, the pipeline is unchanged. A stored profile that fails validation
counts as none for the rules, but generation still reports it: the final
`progress.frontmatter_flags` includes `"blog profile is invalid: <reason>"`
(`blog_rules.invalid_profile_reason`), so the UI shows why the rules were off.

### Writer prompt (`generation.py`)

- **FAQ enabled:** the "FAQ / Q&A" formatting rule is replaced with: "Do not write
  an FAQ or Q&A section. The FAQ is generated separately." Brief outlines that
  contain an FAQ section have it removed before the writer sees them.
- **`favor_brand` stance:** this block replaces "never claim a capability the
  materials don't support":

  > Position {brand} as equal to or ahead of the alternatives. Never state a
  > {brand} gap, limitation or missing feature. Where a competitor genuinely
  > fits a different need, describe that need neutrally.

  The competitor no-hyperlink rule stays.
- **Internal-link block:** see §3.

### Frontmatter step (`services/frontmatter.py`)

This runs after the body is written and before fact-check and GEO, as one agent
call (`rankforge-frontmatter`, Sonnet-class, JSON output). `generate()` asks for
the title, body, brand name, stance, profile categories, and the cluster category
if one exists, and gets back:

```json
{ "category": "rag", "summary": "…", "faq": [{ "q": "…", "a": "…" }] }
```

Prompt rules:
- The summary answers the title's question in its first sentence, stands on its
  own, and names the brand when the body discusses it.
- FAQ questions are phrased as searches, with answers first. At least one is
  "How does {brand} handle X?". Answers are ≤60 words.
- Under `favor_brand`, no brand limitation appears in the summary or FAQ.

Category: **the cluster's `category` wins** when set (`blog_rules.validate_frontmatter`
checks it ahead of the model's own answer). Otherwise the agent picks a key from
the profile.

**Checks and repair** (`blog_rules.validate_frontmatter`, pure, unit-tested):

1. `category` must be a known key. If not, use the cluster category, then the
   first `technical` category.
2. Summary word count must be within `[min_words, max_words]`. If not, retry once
   with the specific error. If it still fails, trim to `max_words` at a sentence
   boundary when over, or keep it and flag it when under.
3. FAQ count must be within `[min, max]`. Extra items are truncated. With too few,
   retry once, then flag.
4. Items with an empty `q` or `a` are dropped before counting.

**Retry safety:** `generate()` asks once, and only retries (with the specific
problems named) when that attempt has flags. It keeps whichever of the two
attempts has **fewer** flags (the first on a tie), and — per field — writes only
values that pass the rules (`field_ok`). An unparseable reply, or a retry that's
worse than the first attempt, never overwrites a stored value. `failing_fields()`
limits a call to the fields that currently fail, so "Fix automatically" and the
post-generation step never touch a field that already passes. When neither the
cluster nor the model names a real key, the fallback category is written (if the
category is being regenerated) with the flag `category defaulted to <key>` — but
a valid stored category is never swapped for the fallback: it is kept and
flagged `category kept as <key> (the model's was not a listed key)`.

A flagged field shows as a warning on the article and **blocks export** (§4). It
never blocks generation.

`complete()` is the entry point used after generation and by "Generate summary &
FAQ" / "Fix automatically": it touches only what's wrong — `fix_meta` +
`enforce_meta` when the title/meta exceed the profile's limits, `generate()` for
`failing_fields()`, and stripping a body FAQ section when one exists under an
FAQ-enabled profile — and snapshots the article once, right before its first
write, so the whole fix is one undo point. With `force=True` ("Generate summary
& FAQ") it also regenerates the summary and FAQ (when enabled) even if they pass,
and the category unless the cluster's category (a profile key) fixes it; a new
value is still written only when it passes the rules.

### Meta (`revise.fix_meta` + `frontmatter.enforce_meta`)

- `fix_meta` receives the profile's `title_max` and `description_max` and treats
  them as hard limits. `refine()` (§5) now passes the same limits when the
  brand has a profile, instead of falling back to the 60/160 defaults.
- When the H1 `title` exceeds `title_max`, it writes `meta_title` of at most
  `title_max` characters and leaves `title` as is.
- `enforce_meta` is the deterministic backstop, run right after `fix_meta` at
  every call site (generation, `complete()`, refine): it clamps `meta_title` /
  `meta_description` to the limits at a word boundary if the model still
  overran, and always derives a `meta_title` when the H1 is long and the model
  gave none.
- Lengths are counted in code points, matching `check-meta.ts`.

### GEO (`geo_optimize.py`)

- **FAQ enabled and `articles.faq` present:** FAQPage JSON-LD is built from the
  stored `faq`, with no extraction agent call.
- **Otherwise:** extraction runs as today.

### Scorer (`scoring.py`)

- **`_sentences` fix:** splits on `[.!?]+` **and every newline**, not only ones
  that open a table row, list item or heading. This is deliberate, not an
  under-scoping: after `_clean` strips markdown, a table row, list item or
  heading is already its own line with no terminal punctuation, and an LLM's
  markdown prose doesn't hard-wrap — so a bare newline inside a real paragraph
  is rare enough that splitting on every newline is the simpler, equally safe
  rule.
  - Fixes: comparison tables currently cost about 10 readability points.
  - Applies to every brand. It is a bug fix.
- **Title/meta length signals:** the upper bound comes from the profile when
  present, and the defaults (60/160) stay.
- **New `internal_links` SEO signal** (profile brands only, `seo_kwargs_for` /
  `seo_brand_kwargs` in `scoring.py`):
  - It counts links whose target falls under the brand's blog URL prefix
    (derived from `url_pattern`) or exactly matches a hub page URL — by
    **target**, not by host, so `/pricing` on the brand's own domain doesn't
    count and a blog on a subdomain still scores. A brand with no `url_pattern`
    falls back to counting by host. `rf:article/{id}` tokens are resolved to
    real URLs first, the same resolution `score_and_store` uses everywhere else.
  - Score band: `links.min`–`links.max`.
  - Weight is taken proportionally from the other SEO signals, so totals stay
    comparable.
  - `scoring.score_seo_for(db, article, resolved_md, *, brief=None)` is the
    entry point that assembles these brand-derived kwargs and scores an
    article's resolved body; it's used by full scoring, the revise commit gate
    (`revise._det_scores`), and the per-link-accept rescore (§3), so all three
    agree on what counts as an internal link.

## 3. Linking

### Candidate pool (new `linking.link_candidates(db, brand, article, limit=8)`)

Candidates are drawn in this order and de-duplicated:

1. Structural: the pillar for a member, or up to 3 members for a pillar.
2. Hub pages whose `topics` overlap the brief's keywords or title (case-insensitive
   phrase match).
3. Published articles in `technical` categories, ranked by keyword overlap with
   the brief. **At most 2 per category**, to spread links across clusters.

Articles in non-technical categories are included only when structural (their own
cluster). This stops the linker from aiming everything at the enterprise pillars.

### Writer link block

Profile brands get an "Internal links you may use" block listing each
candidate's title and link target:
- `rf:article/{id}` for articles, which resolve at render as today;
- the rendered absolute URL for hubs.

Instruction: place `links.min`–`links.max` of them with natural in-sentence
anchors, and add no "related reading" or "further reading" section. This replaces
the one-pillar-link instruction for profile brands, and the pillar link stays
mandatory.

### Suggestion linker (`linking.py`)

- **Mention layer:** also scans for verbatim, unlinked occurrences of hub
  `topics` phrases. Such a suggestion gets `kind='mention'` and
  `target_article_id=null`, with `target_url` set to the hub URL.
  - Migration change: `link_suggestions.target_article_id` is currently
    `not null`, and de-duplication relies on the unique index
    `(article_id, target_article_id, lower(anchor_text))`. `0036` drops the
    `not null` and adds a partial unique index
    `(article_id, target_url, lower(anchor_text)) where target_article_id is null`.
    The staging insert's conflict handling must cover both indexes.
  - Accepting a hub suggestion inserts the rendered hub URL (not an `rf:` token).
- **Cap:** `_MAX_PER_ARTICLE` becomes `links.max` for profile brands. It stays 5
  otherwise.
- **Minimum:** when an article's internal links after mention matching are below
  `links.min`, the linker stages gap suggestions for the top remaining candidates
  from `link_candidates`. Gaps reuse the existing LLM contextual-sentence path
  (opt-in on accept, as today).
- **Trailing slash:** `canonical_url` and hub rendering append `/` to the path
  when `trailing_slash` is on and the path has no file extension.
  - Link check and relink use the same functions, so they stay consistent.
  - The Powabase `url_pattern` should be `https://powabase.ai/blog/{slug}/`. The
    seed script sets it if missing.

### Relink patch notes

`GET /api/business-profiles/{business_id}/relink/patch-notes` returns the pending
suggestions on **published** articles only (drafts are excluded — the site repo
only has published posts to patch), as Markdown grouped by article:

```
### <canonical URL>
- "<anchor>" → <url>  (in: "…sentence containing the anchor…")
```

Each heading is the article's real canonical URL (`linking.canonical_url`, built
from the brand's `url_pattern`), falling back to `/blog/<slug>/` only when no
canonical URL can be resolved — not a hardcoded `/blog/{slug}/` for every brand.

A "Copy as patch notes" button on the relink UI calls it. Suggestions stay staged,
and nothing is written to published content.

## 4. Export (`publishing.render_markdown`)

For profile brands, frontmatter is emitted in this order:

```yaml
title: "…"
metaTitle: "…"        # only when meta_title is set, differs from title, and title > title_max
description: "…"      # meta_description
category: rag
publishedDate: 2026-09-25
author: "…"
tags: […]
summary: "…"          # when summary.enabled
faq:                  # when faq.enabled
  - q: "…"
    a: "…"
draft: false
```

- Strings are JSON-quoted, as today.
- Brands without a profile get today's exact output.

**Pre-export check** (`blog_rules.export_issues(article, profile) -> list[str]`):
- category missing or unknown;
- title and meta over limits (checking `metaTitle` when present);
- summary or FAQ outside the bounds when enabled;
- the body contains an `## FAQ`- or `## Frequently asked`-style heading while FAQ
  is enabled.

Export and publish return **422** (`{"export_issues": [...]}`) when the list is
non-empty. `export()` only runs this gate for the `markdown` format — an HTML
export is never blocked, since it isn't going into the target blog's build.
`publish()` runs it unconditionally (any target type), before any side effect
(webhook delivery, flipping status), regardless of export format. The publish
dialog shows the list, with a "Fix automatically" action that calls
`POST /frontmatter`.

**Invalid stored profile:** if `blog_profile` fails `BlogProfile` validation (a
hand-edited row, or a schema change), `blog_rules.profile_of` logs a warning and
returns `None` everywhere else (generation, scoring, linking — legacy behavior).
Export and publish are the exception: they call `invalid_profile_reason` and
raise `ExportBlocked(["blog profile is invalid: …"])` for markdown/publish rather
than silently falling through to legacy mode, which would also switch the export
rules off. HTML export is unaffected either way.

**Existing articles:** `POST /api/articles/{id}/frontmatter` runs the frontmatter
step and `fix_meta` on demand. This is the "Generate summary & FAQ" button; see
§5 for its response shape.

## 5. Instructed refine / rework

### API

- `POST /api/articles/{id}/refine` accepts an optional JSON body:
  `{ "targets"?: [...], "instructions"?: str, "mode"?: "refine" | "rework" }`.
  - `instructions`: 1–4000 characters after trim. `mode` defaults to `refine` and
    is valid only with `instructions`.
  - `targets` together with `instructions` → **422**.
  - Uses the same `try_begin_refine` claim (409 when busy), `article:refine` rate
    limit, background task, progress steps and post-refine link check as today.
- `POST /api/articles/{id}/frontmatter` (profile brands only): 409
  `"brand has no blog profile"` when the brand has none, 409
  `"blog profile is invalid: <reason>"` when the stored profile fails
  validation (`blog_rules.invalid_profile_reason`). Optional JSON body
  `{ "force": bool }` (default `false`; an empty/absent body is `false`):
  `false` is "Fix automatically" (only failing fields), `true` is "Generate
  summary & FAQ" (`complete(force=True)`, §2). It claims the article
  (`try_begin_refine(total=1)`, 409 if a generation/refine is already
  running), runs `frontmatter.complete()`, releases the claim in a `finally`
  (a previously `failed` article stays `failed`, so "Retry generation" is
  still offered for an empty draft), and returns
  `{ "article": <Article>, "export_issues": [str, ...], "changed": [str, ...] }`
  — the issues still open **after** the fix, computed from the fresh row, so
  the UI never claims "fixed" over a step that left problems; and the fields
  whose value actually changed (compared by value, a subset of `category`,
  `summary`, `faq`, `meta_title`, `meta_description`, `content_md`). An empty
  `changed` means the UI says "Nothing changed".
- `POST /api/articles/{id}/revert`:
  - Restores the newest `article_versions` row whose body **or** frontmatter
    (title, meta, category, summary, FAQ — nulls included) differs from the
    current article. The current state is versioned first, so a revert can
    itself be reverted — and reverting twice in a row **toggles**: the second
    revert's newest-differing row is the snapshot the first revert just took.
  - Only the most recent 20 versions are scanned; an older differing version is
    not reached.
  - Then re-runs fact-check, GEO, scoring and link check in the background
    (the route returns as soon as the version is restored).
  - Returns 409 if a refine is running, 404 if none of the scanned versions
    differs.
  - `article_versions.frontmatter jsonb` (migration `0036`) is written with
    every version — including a version taken for a plain frontmatter edit
    (`PATCH /api/articles/{id}` changing category/summary/FAQ/meta), not only
    body rewrites — so revert restores frontmatter, not just the body.
- `PATCH /api/articles/{id}`: `category`, `summary`, `faq` and `meta_title` are
  **clearable** — an explicit `null` for one of these writes `NULL`, instead of
  being dropped like every other omitted-vs-`null` field. This is what lets the
  frontmatter editor's "— none —" category and an emptied summary/FAQ actually
  save.

### Service (`revise.instructed_pass`)

This is a single pass, not a loop:

1. Load the article, its brand's blog profile, the brief, and grounding
   excerpts (`_article_context` + `_diverse_excerpts`). Mask any `rf:article/…`
   refs already in the body (`linking.mask_refs`) so a full-body LLM rewrite
   can't mangle or drop them; they're restored (`restore_refs`) after.
2. Call the reviser agent with:
   - the mode rule and the profile's link/meta/summary/FAQ/stance rules
     (`_profile_rules`);
   - the sources it may cite;
   - the current frontmatter `{title, meta_title, meta_description, summary, faq}`;
   - the editor's instructions, delimited `<<< … >>>` and never interpolated
     anywhere else in the prompt (they're untrusted user text);
   - the masked body.
3. **Mode rules:**
   - `refine`: "Apply ONLY what the instructions ask. Keep the outline, the
     heading text and the existing links unless the instructions target them."
   - `rework`: "You may restructure, re-outline, change the angle or rewrite
     sections. Keep every factual claim supported by the sources. Keep
     internal links within the link rules." — and it explicitly overrides the
     reviser's own system-prompt instinct to preserve structure, for this pass
     only, while still holding the brand's stance and the no-competitor-link
     rule.
4. **Output:** JSON `{ "content_md": str, "frontmatter"?: { changed fields
   only } }`. If the reply isn't that JSON shape but looks like a full article
   (starts with `#`, optionally fenced), it's accepted as `content_md` with no
   frontmatter change — the reviser's own system prompt sometimes wins out over
   the JSON ask, and a good revision shouldn't be discarded for a formatting
   miss.
5. **Checks** (failure → `InstructedRefineError`, article left unchanged):
   - the agent errored, or the stream had no `complete` event (cut off) — this
     catches a truncated `rework` reply, which has no length floor to catch it
     otherwise;
   - `content_md` is non-empty after unwrapping;
   - in `refine` mode it is ≥60% of the current length; `rework` has no floor;
   - competitor links are stripped deterministically (`strip_competitor_links`);
   - under an FAQ-enabled profile, a body FAQ section is removed.
6. **Frontmatter overwrite protection:** a blank `category`, an empty `""`
   `summary`, or an empty `faq` list in the model's reply is **ignored** — it
   never overwrites a stored value. Only non-blank incoming fields are merged
   with what's currently stored and run through `blog_rules.validate_frontmatter`
   (which — per the cluster's `category` wins) prefers the cluster's category
   over anything the model returned.
7. **Write:** the article is versioned first (so the whole pass is one undo
   point), then body + any validated frontmatter fields are written together,
   `enforce_meta` clamps meta to the profile's limits, and fact-check, GEO and
   scoring re-run. **No score veto.** A snapshot of body, frontmatter and scores
   taken before the write is restored, and the exception re-raised, if anything
   in that post-write pipeline fails. The scores from before the pass are
   carried on `progress.before` so the UI can show the change; any frontmatter
   flags land in `progress.frontmatter_flags`.
8. **Failure semantics** (`routes/articles.py:_refine_and_finish`):
   - `InstructedRefineError` (nothing usable came back) → the article is
     **unchanged**, `generation_status` becomes `"done"`, and
     `progress = {"phase": "done", "refine_error": "<reason>", "mode": …,
     "word_count": …}`. The UI reports the reason and never offers "Retry
     generation" over an article that's actually fine.
   - Any other exception (an infrastructure failure before the agent could even
     respond) sets `generation_status = "failed"`.
   - "Retry generation" itself only makes sense when there's nothing to lose:
     the button is shown only when `generation_status === "failed"` **and**
     `content_md` is empty/whitespace, and `run_generation_task`'s re-draft
     path now versions a non-empty `content_md` before overwriting it either
     way, so a stray retry can't destroy hand edits or an earlier refine.

## 6. Frontend

- **Article page, "Post" tab** (`PostPanel.tsx`) — a tab alongside Comments,
  Links, SEO, GEO and Readability, always shown (not a card on the main body):
  - "Refine with instructions": a textarea (4000-character counter), a
    Refine/Rework segmented toggle with one-line help for each, and a Run
    button, disabled while a refine or revert is in flight.
  - After a run, a banner shows score changes (SEO/GEO/Readability before →
    after) from `progress.before`.
  - A **Revert to previous version** button (confirm dialog), always shown.
  - The frontmatter editor (`PostFrontmatterEditor.tsx`), rendered inside the
    same tab **only when the brand has a blog profile**:
    - category select (with a "— none —" option that clears it);
    - summary textarea with a live word count against the bounds;
    - FAQ list editor (add, remove, reorder, capped at `faq.max`);
    - meta title field with a character count against `meta.title_max`;
    - "Generate summary & FAQ" button (calls `POST /frontmatter`);
    - a client-side echo of the export-check warnings (length/count rules only
      — the body-FAQ-heading check stays server-side, authoritative on 422);
    - Save sends explicit `null` for an emptied category/summary/meta title so
      the clear actually persists (§5).
- **Article page, failed generation:** "Retry generation" is offered only when
  `generation_status === "failed"` **and** `content_md` is empty/whitespace —
  never over a draft that already has content, instructed-refine failure or not.
- **Brand settings → "Blog profile":** a structured form with category rows
  (key, label, description, technical), hub-page rows (path, title, topics),
  summary and FAQ toggles with bounds, meta limits, link bounds and trailing
  slash, and stance. A "Disable blog profile" action sets it to null.
- **Clusters page:** a category select per cluster, from the profile's
  categories.
- **Relink UI:** a "Copy as patch notes" button.
- **Publish dialog:** shows the pre-export check list and a "Fix automatically"
  action.

## 7. No-profile brands

Goal 2 is that a brand with no `blog_profile` behaves exactly as before this
work — legacy `render_markdown` output is byte-identical, and every prior test
passes unchanged. A few small, deliberate exceptions apply to every brand,
profile or not, because they're general fixes or shared machinery:

- `restore_version` / revert now also restore frontmatter (title, meta,
  category, summary, FAQ) from a version that recorded it, not only the body.
- A version is now also taken on a plain frontmatter edit (`PATCH` changing
  category/summary/FAQ/meta), not only on a body change.
- The per-link-accept rescore (§3, `apply_suggestion`) now scores the
  link-resolved body and includes competitor-host detection
  (`scoring.score_seo_for`), where it previously scored the raw body with no
  brand context.
- A re-draft (`run_generation_task` re-running over an existing article, e.g.
  via "Retry generation") now versions a non-empty `content_md` first.
- The `_sentences` readability fix (§2) and the linking-suggestion de-duplication
  fix (§3) apply to every brand — they're bug fixes, not profile behavior.

## 8. Testing

Hermetic `uv run pytest`, mocking at the `Database` / Powabase client boundary as
existing tests do:

- `BlogProfile` validation: bounds, duplicate keys, hub path shape, topic length.
- `validate_frontmatter`: unknown category fallback order, summary trim at a
  sentence boundary, FAQ truncate and too-few flag, empty-item drop.
- `fix_meta` backstop: code-point counting, `meta_title` only when title > max.
- `render_markdown`:
  - profile vs. no-profile golden outputs;
  - `metaTitle` emission rules;
  - YAML quoting of summary/FAQ containing `:` or `#`.
- `export_issues`: each rule, including the body-FAQ heading detection.
- `link_candidates`: ordering, per-category cap of 2, non-technical exclusion
  (except structural), hub topic match.
- Trailing-slash rendering: with and without an extension, idempotence.
- Mention layer with hubs: `target_article_id` null, cap from profile.
- `_sentences`: table rows, lists and headings (every newline) as boundaries. A
  regression test that a table-heavy fixture no longer loses readability points.
- `internal_links` signal: counts links by blog-prefix/hub target, not host;
  ignores external links.
- Frontmatter step (`generate`/`complete`): retry only on flags, keep the
  attempt with fewer flags, write only fields that pass, `failing_fields` scope.
- Instructed refine:
  - both modes, including the `rework` structure-override wording;
  - `targets`+`instructions` → 422, and mode without instructions → 422;
  - refine length floor, rework's incomplete-stream check instead of a floor;
  - a blank/empty model frontmatter value never overwrites a stored one; the
    cluster's category wins over the model's;
  - rollback on a failure after the write;
  - raise on agent failure before any work;
  - `POST /frontmatter` response shape and article-claiming (409 when busy).
- Revert: restores body and frontmatter (including explicit nulls), versions
  current first, chooses the newest differing version among the last 20, 404
  with no differing version, 409 when busy.
- No-profile brands: existing tests pass unchanged (the regression guard for
  goal 2), except the handful of shared-machinery differences in §7.

**Deploy order:** migration `0036_blog_profile.sql` applies before the backend
that reads/writes the new columns.

**End-to-end acceptance (manual, before PR):**
1. Seed the Powabase profile (`--brand-id <uuid>`).
2. Generate 2–3 articles.
3. Export them into a website worktree's `content/blog/`.
4. Run `npm run build`. This runs the website's own `parsePost` validation and
   `check-meta.ts`, and it must pass with no edits.
5. Check the built HTML shows the Short answer box, the FAQ accordion (and no
   duplicate body FAQ), and 3–5 in-body links with trailing slashes.

## Risks

- **Stance vs. grounding:** `favor_brand` tells the writer not to state brand
  gaps, while the fact-checker still flags unsupported claims. That is the
  intended balance: the stance changes framing, and grounding still reports
  claims with no source.
- **Summary quality under trimming:** a trimmed summary can end abruptly. Trimming
  happens only after a failed retry, and a trimmed summary is flagged for review.
- **Hub topic matching** is phrase-based and can miss synonyms. Structural and
  category candidates cover most cases, and the gap path covers the rest.
