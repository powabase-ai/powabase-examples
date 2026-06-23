# RankForge — Product Requirements (living doc)

> **Status: strawman v0.** This is the document we co-develop. Sections marked
> **OPEN** are unresolved; sections marked **ASSUMPTION** are defaults I picked that
> we can change. Nothing here is locked until we agree.

## 1. Summary

RankForge is an internal, production content platform that takes a topic and
produces a publish-ready, **SEO- and GEO-optimized** long-form blog article,
grounded in our brand voice and facts, with a human editor in the loop. It is built
on the Powabase AI BaaS and is also released as a flagship open-source example.

**GEO = Generative Engine Optimization**: making content structured, citable, and
authoritative enough that AI answer engines (ChatGPT, Perplexity, Google AI
Overviews, etc.) surface and cite it — a distinct discipline from classic SEO, and
RankForge's core differentiator.

## 2. Goals / non-goals

**Goals**
- Cut time-to-publish for a high-quality, optimized article from days to hours.
- Make GEO a first-class, measurable dimension — not an afterthought.
- Keep a human editor in control: nothing auto-publishes without approval.
- Demonstrate a real, non-toy Powabase integration (agents, workflows, RAG, BaaS).

**Non-goals (for now)**
- Full social/newsletter repurposing (later).
- Rank tracking / analytics dashboards beyond storing published URLs (later).
- A hard multi-tenant SaaS — **DECIDED**: single internal team, one shared
  workspace, but the workspace manages **multiple brands/businesses**
  (`business_profiles`) without per-tenant data isolation (see §3, §6).

## 3. Users & roles

**ASSUMPTION (confirmed): multi-user team, shared workspace.** Everyone on the
internal team works in one shared workspace; articles carry an author/owner.

Roles (start simple, enforce later):
- **Writer** — create research/briefs/drafts, edit own + shared articles.
- **Editor** — everything a writer can, plus approve/publish.
- **Admin** — manage team, integrations, KB sources, settings.

**OPEN**: Do we need per-user privacy on drafts, or is everything team-visible?
(Affects RLS — default below is team-visible, author tracked.)

**Brands / business profiles (DECIDED — multiple).** The workspace manages several
brands/clients. A `business_profile` captures one brand's niche: domain, description,
seed topics & target keywords, competitors, audience, and a linked brand KB. Scouts,
briefs, articles, and the internal-link graph are all scoped to a business profile
(soft scoping via a `business_id` column, not hard RLS tenancy). This satisfies
"the client's business" while keeping one shared team workspace.

## 4. Capability areas & functional requirements

Phased. Each phase is independently shippable.

### Phase 1 — Research & SERP intelligence
- **FR-1.1** Input a seed topic and/or seed keyword(s) + optional target locale/lang.
- **FR-1.2** Run SERP analysis via a Powabase agent with `web_search` (Exa): top
  results, titles, URLs, snippets, "People Also Ask" questions.
- **FR-1.3** Scrape & tear down the top N competitor pages via `web_scrape`
  (Firecrawl): heading outline, word count, entities/topics covered, schema present.
- **FR-1.4** Cluster keywords and classify **search intent** (informational /
  commercial / transactional / navigational).
- **FR-1.5** Persist research as a reusable artifact (`research_runs`) linked to the
  topic, so briefs and drafts can reference it without re-running.

### Phase 2 — Content briefs
- **FR-2.1** Generate a brief from a research run: primary keyword, secondary
  keywords, recommended word-count range (derived from SERP), required H2/H3
  headings, must-cover entities, questions to answer, internal + external link
  suggestions, suggested title + meta description.
- **FR-2.2** Briefs are editable by the user before generation (human-in-the-loop).
- **FR-2.3** A brief can be created standalone (skip auto-research) by an editor.

### Phase 3 — Generation pipeline
- **FR-3.1** Implement as a **Powabase Workflow** DAG: `research → brief → outline →
  draft → optimize`, so each stage is inspectable, re-runnable, and streamable to
  the UI. **ASSUMPTION**; alternative is a single agent — see [architecture.md](architecture.md#generation).
- **FR-3.2** The draft stage produces structured Markdown with a real heading
  hierarchy, intro/conclusion, and inline citations to sources gathered in research.
- **FR-3.3** Generation is **grounded** in the brand KB (§Phase 6) and the research
  run's sources — no unsourced factual claims where a source exists.
- **FR-3.4** Each run records its Powabase `workflow_run_id` / `run_id` for replay
  and debugging.
- **FR-3.5** Regeneration of a single section without redoing the whole article.

### Phase 4 — GEO optimization (differentiator)
- **FR-4.1** Restructure the draft for answer engines: concise lead answers, Q&A
  blocks aligned to PAA, scannable lists, clear entity definitions.
- **FR-4.2** Emit valid **schema.org JSON-LD** (Article + FAQPage where relevant).
- **FR-4.3** Compute a **GEO / answer-engine readiness score** with sub-signals:
  citable-claim density, source authority, entity coverage vs. brief, direct-answer
  presence, structured-data completeness. Each sub-signal is explainable in the UI.
- **FR-4.4** Suggest concrete fixes for the lowest sub-signals.

### Phase 5 — SEO on-page scoring
- **FR-5.1** Score: primary/secondary keyword usage & placement, readability,
  heading hierarchy validity, title/meta length & keyword presence, internal-link
  density, word-count gap vs. SERP median, image alt coverage.
- **FR-5.2** Per-issue, actionable recommendations, surfaced inline in the editor.
- **OPEN**: build scoring in-house vs. integrate an external API. Default: in-house
  (deterministic, no per-call cost, fully ours), LLM only for fuzzy judgments.

### Phase 6 — Grounding Knowledge Base
- **FR-6.1** Maintain a Powabase **Knowledge Base** of brand voice / style guide /
  product docs / approved facts.
- **FR-6.2** Admins upload/manage KB sources from the UI (proxied to Powabase
  `/api/sources` + `/api/knowledge-bases`).
- **FR-6.3** The generation pipeline attaches this KB so drafts stay on-brand.

### Phase 7 — Editorial workflow & management
- **FR-7.1** Article lifecycle: `draft → in_review → approved → published` (+
  `archived`).
- **FR-7.2** Rich editor (Markdown-first) with live SEO/GEO score panels.
- **FR-7.3** Versioning: every save snapshots; diff and restore.
- **FR-7.4** Library: list/search/filter by status, keyword, author, score, date.
- **FR-7.5** Comments / review notes. **OPEN** (could defer).

### Phase 8 — Publishing & export
- **FR-8.1** Export Markdown + clean HTML + JSON-LD (baseline; copy or download).
- **FR-8.2** **WordPress** adapter — push as draft/post via WP REST API.
- **FR-8.3** **Webflow** adapter — push to a CMS collection.
- **FR-8.4** **Custom webhook** adapter — POST the article payload to a configured
  endpoint (generic destination).
- **FR-8.5** Record each publication (target, external id, URL, status) on the
  article; never auto-publish without an editor action.
- **OPEN**: scheduled/queued publishing (Powabase Workflow cron) — later.

### Phase 9 — Autonomous content scouts (semi-long-running agents)
Scheduled agents that monitor a brand's niche and opportunistically draft content.
**Default autonomy = L3** (configurable per scout): L1 propose → inbox only; L2 draft
→ review queue; **L3 draft + run full SEO/GEO scoring → stage as `in_review`**. No
level ever auto-publishes — a human always publishes.
- **FR-9.1** Per-business `scouts`: cron schedule, signal sources, score thresholds,
  autonomy level, target article types, enabled/paused.
- **FR-9.2 Signal sources are native-only (DECIDED):** "what's hot" is derived from
  **Google News + SERP volatility + competitor new-content / sitemap diffs**, gathered
  via Powabase's built-in **Exa `web_search`** and **Firecrawl `web_scrape`** tools.
  No external Google Trends API dependency.
- **FR-9.3** Discovery run = a **Powabase Workflow with a cron trigger**: gather
  signals → dedupe against existing articles + past opportunities → score
  (niche-relevance × momentum × competition-gap × intent) → write `opportunities` →
  per autonomy level, kick the generation workflow → notify editors.
- **FR-9.4** **Opportunity inbox** UX: cards with topic, "why now" signal, score,
  suggested article type, source evidence; actions Draft · Dismiss · Snooze · Open.
- **FR-9.5** **Scout dashboard**: per-scout health, last run, found-vs-converted, and
  a content calendar of scheduled / auto-drafted pieces.
- **FR-9.6** Notifications: in-app first; email/Slack later.
- This is shared infrastructure — **News** (Phase-1 adjacent) and **monthly
  re-linking** (FR-12.3) are scouts too. Build a general *scheduled-job + opportunity
  queue* framework, not one-off crons.

### Phase 10 — Programmatic SEO
Generate many pages at scale from a dataset + template.
- **FR-10.1** Upload/connect a dataset (CSV → a `public` table); **FR-10.2** map
  columns to a content template's variables; **FR-10.3** batch-generate (**one
  Powabase workflow run per row**); **FR-10.4** review grid (status, scores, flags);
  **FR-10.5** bulk publish. Guardrails: thin-content / near-duplicate detection
  before publish.

### Phase 11 — Media & rich enrichment
- **FR-11.1 Images:** per-section suggestions + auto alt-text; stored in Powabase
  **Storage**. Source is **OPEN** (AI-gen vs stock vs both — §7).
- **FR-11.2 YouTube → article:** paste URL → fetch transcript → generate (how-to /
  summary) → embed the video. Transcript fetch has no native tool (§7).
- **FR-11.3 Related YouTube videos:** suggest + embed relevant videos.
- **FR-11.4 Interactive SEO mini-tools:** generate a self-contained embeddable widget
  (calculator/quiz/checklist) inside the article from a tool-template library. Doubles
  as a **linkable asset** for backlinks (FR-12.4).

### Phase 12 — Authority & linking
- **FR-12.1 Internal linking:** ingest the brand's **sitemap** → `site_pages` index →
  suggest/insert internal links in new and existing articles.
- **FR-12.2 External linking:** insert authoritative external links from the research
  run's sources.
- **FR-12.3 Monthly re-linking:** a scheduled maintenance scout that revisits
  published articles, weaves links between new and old content, and fixes broken links;
  surfaces changes for approval.
- **FR-12.4 Backlinks (DECIDED — both):** (a) **now** — create linkable assets
  (interactive tools, data studies) + a strong internal-link graph; (b) **later** —
  backlink-opportunity finder (competitor gap, resource pages, HARO-style) + outreach
  drafting. We do **not** auto-generate spammy backlinks.

### Phase 13 — Editorial quality gates (run inside the Phase-3 pipeline)
- **FR-13.1 Fact-checking:** a pass that flags unsupported claims, attaches sources,
  and lets the editor accept/fix (agent + `web_search` verify against the brand KB).
- **FR-13.2 Anti-hallucination reflection:** a **reflection block** in the generation
  workflow where the model critiques its own draft against gathered sources, removes
  unsupported claims, and emits a grounding/confidence report shown in the editor.
- Both feed the GEO citability score (FR-4.3).

### Article-type template system
The 10 article types are **one extensible template registry** (`content_templates`),
not 10 features. Each type → outline pattern, schema.org type, default length, and the
pipeline stages it enables. Picked at article/brief/programmatic-campaign creation.

| Type | Outline | schema.org | Note |
|---|---|---|---|
| Listicle | numbered items | `ItemList` | strong for AI answers |
| How-to Guide | sequential steps | `HowTo` | very citable |
| Checklist | checklist (interactive option) | `HowTo`/`ItemList` | ties to mini-tools |
| QA Article | Q&A blocks | `FAQPage` | **best GEO format** |
| Versus (X vs Y) | comparison table + verdict | `Article` | high commercial intent |
| Roundup | curated entries | `ItemList` | |
| Ultimate Guide | pillar + sections | `Article` | pillar/cluster hub |
| News | inverted pyramid | `NewsArticle` | timely; scout-fed |
| YouTube article | from transcript | `Article` + `VideoObject` | |
| Interactive tool | tool + supporting copy | `WebApplication` | linkable asset |

Types are **data, not code** — new types added without a deploy.

## 5. Powabase modules used

| Capability | Powabase module | Notes |
|---|---|---|
| SERP research, competitor teardown, news/trend signals | **Agent** + `web_search` (Exa), `web_scrape` (Firecrawl) | Needs `EXA_API_KEY` / `FIRECRAWL_API_KEY` in project **Settings → Tools**. Trend signal = News + SERP + competitor diffs (no external trends API) |
| Generation pipeline | **Workflow** (DAG) | research → brief → outline → draft → reflect/fact-check → optimize |
| Autonomous scouts & monthly re-linking | **Workflow + cron trigger** | scheduled discovery/maintenance; agent blocks call Exa/Firecrawl; writes `opportunities` |
| Programmatic SEO | **Workflow** (one run per dataset row) | batch generation from a `public` data table |
| Brand grounding | **Knowledge Base** (RAG) | per-brand KB; `chunk_embed` + `hybrid` default |
| App data | **Custom `public` tables** via direct Postgres + PostgREST | business_profiles, scouts, opportunities, briefs, articles, versions, site_pages, publications |
| Team auth | **GoTrue** | shared workspace; Anon key client-side only |
| Images & tool assets | **Storage** | article images + interactive-tool bundles |

## 6. Data model (strawman)

App tables in the project's `public` schema (RLS **ON**, team-visible policies):

- `profiles` — mirrors `auth.users`; `role` (writer/editor/admin), display name.
- `research_runs` — topic, locale, `serp` jsonb, `competitors` jsonb, `clusters`
  jsonb, `intent`, `agent_run_id`, `created_by`, timestamps.
- `briefs` — `research_run_id?`, primary/secondary keywords, target word count,
  `headings` jsonb, `entities` jsonb, `questions` jsonb, `link_suggestions` jsonb,
  suggested title/meta, `created_by`, timestamps.
- `articles` — `brief_id?`, title, slug, status, `content_md`, `content_html`,
  meta title/description, `json_ld` jsonb, `seo_score` jsonb, `geo_score` jsonb,
  `keywords` jsonb, `author_id`, `workflow_run_id`, timestamps.
- `article_versions` — `article_id`, `content_md`, `created_by`, `created_at`.
- `publications` — `article_id`, `target_type` (wordpress|webflow|webhook|export),
  `config_ref`, `external_id`, `url`, `status`, `published_at`.
- `publish_targets` — `target_type`, `name`, `config` jsonb (creds via secret ref,
  **not** raw in DB — **OPEN**: where to store CMS credentials safely).

Added for the brand + autonomous-scout + enrichment work (Phases 9–13). All
content rows gain a `business_id` for soft per-brand scoping:

- `business_profiles` — name, domain, description, niche, seed_topics jsonb,
  target_keywords jsonb, competitors jsonb, audience, `brand_kb_id` (Powabase KB).
- `scouts` — `business_id`, name, `cron`, `signal_sources` jsonb (news/serp/competitor),
  `autonomy_level` (l1|l2|l3, default l3), `thresholds` jsonb, `article_types` jsonb,
  `workflow_id`, `enabled`, `last_run_at`.
- `opportunities` — `business_id`, `scout_id?`, topic, `why_now` (signal), `score`,
  `suggested_type`, `evidence` jsonb, `status` (new|drafting|drafted|dismissed|snoozed),
  `article_id?`.
- `content_templates` — `type` (listicle|how_to|…), `outline` jsonb, `schema_org_type`,
  `default_word_count`, `pipeline_stages` jsonb, `enabled`.
- `site_pages` — `business_id`, url, title, summary, `embedding?`, `last_seen_at`
  (ingested from the brand's sitemap; powers internal linking + re-linking).
- (later) `backlink_opportunities` — for FR-12.4(b).

See `backend/schema/0001_init.sql` for the first cut (Phases 1–8 tables); the
Phase 9–13 tables above get DDL as M2+ starts.

## 7. Key open questions (to resolve together)

Detailed per-feature design is in [`design/`](design/) (one doc per feature, 01–10).
Resolved in the 2026-06-18 design pass:

1. ~~Generation: Workflow vs Agent vs Orchestration~~ → **Segmented; Workflow DAG for
   the autonomous stretch** (Design 01).
2. ~~Trend data source~~ → **native-only** (News + SERP + competitor diffs via Exa/Firecrawl).
3. ~~Internal linking via sitemap~~ → **yes**, full-page scrape + embedding → `site_pages` (Design 06).
4. ~~Single vs multi-brand~~ → **multiple brands** in shared workspace.
5. ~~Scout autonomy default~~ → **L3** (draft + score + stage; configurable).
6. ~~Image source~~ → **user-uploaded brand image library**; AI-gen/stock deferred (Design 08).
7. ~~YouTube fetch~~ → **official YouTube Data API + transcript lib** (Design 08).
8. ~~SEO/GEO scoring~~ → **hybrid deterministic + LLM, in-house**; advisory + per-type
   targets (Designs 03, 05).
9. ~~Per-user draft privacy~~ → **team-visible** (author tracked).
10. ~~Research depth / keyword metrics~~ → **Deep default; native proxies + pluggable
    metrics provider** (Design 02).
11. ~~Publish permission, comments~~ → **editors+admins publish; lightweight comments
    in v1** (Design 10).

Still open:
- **CMS credential storage** — Powabase secrets? env? vault? (security-sensitive)
- **Multilingual** — English-only v1, or multilingual from the start?
- **Volume/scale** — articles/day expected? Drives workflow concurrency & cost.
- Build-time tuning items live in each design doc's "Open / deferred" section
  (keyword-metrics API, AE-recon engine, SERP-volatility build, score weights, etc.).

## 8. Milestones (proposed)

- **M0 (this session)** — repo scaffold, Powabase client wiring, schema strawman,
  this PRD + architecture. ✅
- **M1** — Brands + Research + brief (Phases 1–2, business_profiles) end-to-end on a
  real Powabase project.
- **M2** — Generation pipeline + grounding KB + quality gates (Phases 3, 6, 13);
  article-type templates.
- **M3** — SEO + GEO scoring (Phases 4–5) in the editor. ✅
- **M4** — Editorial workflow + library (Phase 7). ✅ Status lifecycle, library
  filters, version history/restore, **GoTrue auth** (Anon-key client + JWT-verified
  backend, JIT profiles, first user → admin), **roles** (writer/editor/admin gating
  approve/publish + role management), and inline **review comments**.
- **M5** — Autonomous content scouts + opportunity inbox (Phase 9). ✅
  In-process APScheduler tick polls per-brand `scout_configs` (durable `next_run_at`);
  a tool-using `rankforge-scout` agent (Exa) discovers timely topics, scored against
  the brand into an `opportunities` inbox. `suggest` surfaces only; `auto_draft`
  promotes top picks (≥min_score, capped) through research→brief→draft and stages them
  `in_review` (never auto-publishes). Scouts UI: config + inbox + manual "Run now".
- **M6** — Authority & linking: sitemap ingestion, internal/external links, monthly
  re-linking, linkable assets (Phase 12 + FR-11.4).
- **M7** — Programmatic SEO (Phase 10) + media enrichment (Phase 11).
- **M8** — Publishing adapters (Phase 8); backlink opportunity finder (FR-12.4b).

Sequencing rationale: prove single-article quality (M1–M4) before automating it at
scale (M5–M7). Scouts reuse the generation pipeline, so they come after it works.
