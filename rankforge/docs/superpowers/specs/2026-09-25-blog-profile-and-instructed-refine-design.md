# Per-brand blog profile + instruction-driven refine — Design

**Date:** 2026-09-25
**Status:** Approved (design); awaiting spec review
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

### `blog_profile` shape (Pydantic `BlogProfile`, `models/business.py`)

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
- `min ≤ max` everywhere.
- `hub_pages[].path` starts with `/`.
- `topics` holds 1–10 phrases, each at least 4 characters. The same minimum as
  the linker's `_MIN_ANCHOR_LEN` keeps generic matches out.

Hub paths render against the brand's `domain`. A hub is a site page, not an
article, so `url_pattern` doesn't apply.

The profile is set through the existing business-profile create/update routes,
where `blog_profile` is optional and nullable.

### Powabase seed (`scripts/seed_powabase_blog_profile.py`)

A script that writes the Powabase profile to the named brand:
- the six website categories, with `rag`, `agents`, `backend` and `coding-agents`
  marked `technical`;
- the ten hub pages: `/supabase-alternative/`, `/firebase-alternative/`,
  `/convex-alternative/`, `/neon-alternative/`, `/pinecone-alternative/`,
  `/langchain-alternative/`, `/backend-as-a-service/`, `/self-hosted-supabase/`,
  `/vector-database/`, `/free-mvp/`, each with topic phrases;
- summary 40–60 words, FAQ 3–6, meta 60/160, links 3–5 with a trailing slash;
- stance `favor_brand`.

It is idempotent: it overwrites only `blog_profile`.

## 2. Generation

All of the changes below apply **only when the brand has a `blog_profile`**.
Without one, the pipeline is unchanged.

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

### Frontmatter step (new `services/frontmatter.py`)

This runs after the body is written and before fact-check and GEO, as one agent
call (`rankforge-frontmatter`, Sonnet-class, JSON output). It receives the title,
body, brand name, stance, profile categories, and the cluster category if one
exists. It returns:

```json
{ "category": "rag", "summary": "…", "faq": [{ "q": "…", "a": "…" }] }
```

Prompt rules:
- The summary answers the title's question in its first sentence, stands on its
  own, and names the brand when the body discusses it.
- FAQ questions are phrased as searches, with answers first. At least one is
  "How does {brand} handle X?". Answers are ≤60 words.
- Under `favor_brand`, no brand limitation appears in the summary or FAQ.

Category: **the cluster's `category` wins** when set. Otherwise the agent picks a
key from the profile.

**Checks and repair** (`validate_frontmatter`, pure, unit-tested):

1. `category` must be a known key. If not, use the cluster category, then the
   first `technical` category.
2. Summary word count must be within `[min_words, max_words]`. If not, retry once
   with the specific error. If it still fails, trim to `max_words` at a sentence
   boundary when over, or keep it and flag it when under.
3. FAQ count must be within `[min, max]`. Extra items are truncated. With too few,
   retry once, then flag.
4. Items with an empty `q` or `a` are dropped before counting.

A flagged field shows as a warning on the article and **blocks export** (§4). It
never blocks generation.

### Meta (`revise.fix_meta`)

- `fix_meta` receives the profile's `title_max` and `description_max` and treats
  them as hard limits.
- When the H1 `title` exceeds `title_max`, it writes `meta_title` of at most
  `title_max` characters and leaves `title` as is.
- A deterministic backstop truncates at a word boundary if the model still
  overruns.
- Lengths are counted in code points, matching `check-meta.ts`.

### GEO (`geo_optimize.py`)

- **FAQ enabled and `articles.faq` present:** FAQPage JSON-LD is built from the
  stored `faq`, with no extraction agent call.
- **Otherwise:** extraction runs as today.

### Scorer (`scoring.py`)

- **`_sentences` fix:** before splitting on `[.!?]`, also split on newlines that
  begin a table row (`|`), a list item (`-`, `*`, `+`, `\d+.`) or a heading
  (`#`). Table separator rows are dropped.
  - Fixes: comparison tables currently cost about 10 readability points.
  - Applies to every brand. It is a bug fix.
- **Title/meta length signals:** the upper bound comes from the profile when
  present, and the defaults (60/160) stay.
- **New `internal_links` SEO signal** (profile brands only):
  - It counts links in the body that resolve to the brand's own articles or hub
    pages. `rf:article/{id}` tokens are resolved first, the same way the existing
    link resolution in `score_and_store` does.
  - Score band: `links.min`–`links.max`.
  - Weight is taken proportionally from the other SEO signals, so totals stay
    comparable.

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

`GET /api/relink/{brand_id}/patch-notes` returns the brand's pending suggestions
as Markdown, grouped by article slug:

```
### /blog/<slug>/
- "<anchor>" → <url>  (in: "…sentence containing the anchor…")
```

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

**Pre-export check** (`validate_for_export(article, profile) -> list[str]`):
- category missing or unknown;
- title and meta over limits (checking `metaTitle` when present);
- summary or FAQ outside the bounds when enabled;
- the body contains an `## FAQ`- or `## Frequently asked`-style heading while FAQ
  is enabled.

Export and publish return **422 with the list** when it's non-empty. The publish
dialog shows the list, with a "Fix automatically" action that runs the
frontmatter step plus `fix_meta`.

**Existing articles:** `POST /api/articles/{id}/frontmatter` runs the frontmatter
step and `fix_meta` on demand. This is the "Generate summary & FAQ" button.

## 5. Instructed refine / rework

### API

- `POST /api/articles/{id}/refine` accepts an optional JSON body:
  `{ "targets"?: [...], "instructions"?: str, "mode"?: "refine" | "rework" }`.
  - `instructions`: 1–4000 characters after trim. `mode` defaults to `refine` and
    is valid only with `instructions`.
  - `targets` together with `instructions` → **422**.
  - Uses the same `try_begin_refine` claim (409 when busy), `article:refine` rate
    limit, background task, progress steps and post-refine link check as today.
- `POST /api/articles/{id}/revert`:
  - Restores the most recent `article_versions` row whose content differs from
    the current body. The current body is versioned first, so a revert can itself
    be reverted.
  - Then re-runs fact-check, GEO, scoring and link check.
  - Returns 409 if a refine is running, 404 if there is no earlier version.
  - Frontmatter fields (title, meta, summary, FAQ) are **not** versioned today.
    `article_versions` gains a nullable `frontmatter jsonb` snapshot, written with
    each version, so revert restores them too.

### Service (`revise._instructed_pass`)

This is a single pass, not a loop:

1. Load the article, brief, blog profile, and grounding excerpts
   (`_diverse_excerpts`).
2. Call the reviser agent with:
   - brand name and stance, and the profile's link and frontmatter rules;
   - the current frontmatter `{title, meta_title, meta_description, summary, faq}`;
   - the current body;
   - excerpts;
   - the mode rule and your instructions, delimited as data.
3. **Mode rules:**
   - `refine`: "Apply only what the instructions ask. Keep the outline, heading
     text, and existing links unless the instruction targets them."
   - `rework`: "You may restructure, re-outline, change the angle or rewrite
     sections. Keep every factual claim supported by the excerpts. Keep internal
     links within the link rules."
4. **Output:** JSON
   `{ "content_md": str, "frontmatter"?: { changed fields only } }`.
5. **Checks** (failure → no write, and the refine reports an error):
   - `content_md` is non-empty.
   - In `refine` mode it is ≥60% of the current length. `rework` has no floor.
   - Returned frontmatter goes through `validate_frontmatter` and the meta
     backstop.
   - Competitor links are stripped deterministically (`strip_competitor_links`).
   - Under an FAQ-enabled profile, a body FAQ section is removed.
6. **Write:** body and frontmatter together (versioned). Then fact-check, GEO,
   scoring, and link check. **No score veto.** The previous scores are stored on
   the progress record so the UI can show the change.
7. **Failure semantics** match `_targeted_loop`:
   - an infrastructure failure before the agent responds raises, and the article
     is marked with a refine error;
   - a failure after the write restores the previous body and score snapshot.

## 6. Frontend

- **Article page:** a "Refine with instructions" card with:
  - a textarea (4000-character counter);
  - a Refine/Rework segmented toggle with one-line help for each;
  - a Run button, disabled while a refine is running.

  After a run, a banner shows score changes (SEO/GEO/Readability before → after)
  and a **Revert to previous version** button.
- **Article page, frontmatter panel** (profile brands only):
  - category select;
  - summary textarea with a live word count against the bounds;
  - FAQ list editor (add, remove, reorder, 3–6);
  - `metaTitle` field with a character count;
  - "Generate summary & FAQ" button;
  - export warnings from `validate_for_export`.
- **Brand settings → "Blog profile":** a structured form with category rows
  (key, label, description, technical), hub-page rows (path, title, topics),
  summary and FAQ toggles with bounds, meta limits, link bounds and trailing
  slash, and stance. A "Disable blog profile" action sets it to null.
- **Clusters page:** a category select per cluster, from the profile's
  categories.
- **Relink UI:** a "Copy as patch notes" button.
- **Publish dialog:** shows the pre-export check list and a "Fix automatically"
  action.

## 7. Testing

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
- `validate_for_export`: each rule, including the body-FAQ heading detection.
- `link_candidates`: ordering, per-category cap of 2, non-technical exclusion,
  hub topic match.
- Trailing-slash rendering: with and without an extension, idempotence.
- Mention layer with hubs: `target_article_id` null, cap from profile.
- `_sentences`: table rows, lists and headings as boundaries. A regression test
  that a table-heavy fixture no longer loses readability points.
- `internal_links` signal: counts resolved `rf:` tokens and hub URLs, ignores
  external links.
- Instructed refine:
  - both modes;
  - `targets`+`instructions` → 422, and mode without instructions → 422;
  - refine length floor, rework no floor;
  - frontmatter changes validated;
  - rollback on a failure after the write;
  - raise on agent failure before any work.
- Revert: restores body and frontmatter, versions current first, 404 with no
  earlier version, 409 when busy.
- No-profile brands: existing tests pass unchanged, which is the regression guard
  for goal 2.

**End-to-end acceptance (manual, before PR):**
1. Seed the Powabase profile.
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
