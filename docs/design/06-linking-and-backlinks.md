# Design 06 — Internal/external linking + backlinks

Status: **agreed** (2026-06-18). PRD Phase 12. All white-hat. M6.

## Internal linking

- **Site index** — ingest the brand's `sitemap.xml` → `site_pages` (url, title, summary,
  embedding). **Full-page scrape + embedding** for best semantic matching (accept the
  scrape cost/storage; refresh periodically).
- **At generation/edit** — suggest internal links by similarity to `site_pages`, natural
  keyword anchors, capped per 1000 words (avoid over-linking).
- **Reverse linking** — on publish, find older pages that should link to the new one.

## External linking

Insert authoritative outbound links from the research run's high-authority sources
(citable domains → SEO trust + GEO).

## Backlinks (white-hat, "both")

- **Now**: (a) linkable assets — interactive tools, original-data studies (ties to
  Feature 8); (b) strong internal-link graph via pillar/cluster architecture.
- **Later**: backlink-opportunity finder (competitor gap, resource pages, HARO-style) +
  outreach drafting.

## Decisions

- **Full-page scrape + embedding** for `site_pages` (not metadata-only).
- **Pillar/cluster modeled now, enforced in M6** — capture pillar→cluster relationships
  in the data model; build gap-analysis + enforced interlinking in the linking milestone.
- **Re-linking = on-publish + monthly sweep** — immediate reverse-link suggestions on
  publish, plus a scheduled monthly maintenance scout (fix broken links, weave new↔old).
  The monthly sweep is a scout (Design 04 infra).

## Data model additions

- `site_pages` (gets `is_pillar`, `cluster_id`).
- `clusters` — `business_id`, label, pillar_page_id.
- `internal_links` (optional) — source_article/page → target, anchor, status.

## Open / deferred

- Anchor-text generation rules + per-1000-word link caps (tune at build).
- Backlink-opportunity finder + outreach (later sub-phase; needs a backlinks data source).
- Sitemap refresh cadence.
