# Design 07 — Programmatic SEO

Status: **agreed** (2026-06-18). PRD Phase 10. M7. Generate many pages from a
dataset × template (one row → one page targeting a long-tail keyword).

## Flow

```
dataset (CSV upload OR live table/API) → map columns to a content_template's variables
→ shared batch research (once) + per-row light lookup → one Stage C run per row
→ review grid (scores + flags) → bulk publish (human)
```

## Decisions

- **Shared research + per-row light** — one topic-level research run for the batch, then a
  light per-row lookup (dataset row + Quick depth). Cost-efficient at scale, still
  differentiated per page. The duplicate detector below guards thin-content risk.
- **Dataset source = both from the start** — CSV upload (→ loaded into a `public` table)
  AND connect an existing Postgres table / external API (new rows auto-generate pages).
- **Scale confirm + throttled queue** — before a batch runs, show projected count / est.
  cost / est. time and require confirmation; then process through a rate-limited queue
  respecting Powabase's 20/min workflow-execute cap and credit limits (`402` → pause).
- **Thin / near-duplicate detector** runs across the generated set, flags rows in the
  review grid; publish always needs a human (no auto-publish).

## Data model

- `pseo_campaigns` — `business_id`, name, `template_id`, dataset_ref (table/source),
  column_mapping jsonb, status.
- `pseo_rows` — `campaign_id`, row_data jsonb, `article_id?`, status, flags jsonb.
- Dataset table itself lives in `public` (uploaded) or is an external binding.

## Open / deferred

- Throttle/queue implementation (backend worker vs Powabase workflow concurrency).
- Duplicate-detection thresholds (embedding similarity across the batch).
- Live-API binding refresh semantics (poll vs webhook).
