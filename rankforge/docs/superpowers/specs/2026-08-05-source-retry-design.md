# User-triggered retry for failed sources — Design

**Date:** 2026-08-05
**Status:** Approved
**Repo:** `example-apps/rankforge`

## Problem

When Research scrapes competitor URLs, some sources fail extraction (commonly
Firecrawl `429 Too Many Requests`, but also `attention_required`/`cancelled`).
Today a failed source is a dead end: the run's automatic retry/backoff (PR #19)
has already been exhausted, and the only user recourse is to re-run the entire
research run. There is no way to re-scrape a single source that failed.

## Goal

Give the user an explicit, per-source **Retry** action (plus a **Retry all
failed** convenience) so a failed source can be re-scraped on demand, wherever a
failed source is visible.

## Scope boundary

Retry only fixes a source's **scraped content**. If a source is retried and
succeeds *before* article generation, it is naturally picked up at generation
time (grounding reads the run's usable sources then). Retry does **not**
retroactively re-ground or regenerate an already-written article — that remains a
separate, explicit action. Out of scope.

## Approach (chosen: A — one bulk, row-id endpoint)

A single bulk endpoint mirroring the existing `bulk-delete` shape. Single-source
retry, "retry all failed in this run," and "retry all failed in the library" are
all just different `row_ids` sets the frontend assembles. Rejected alternatives:
resource-scoped per-run/per-source endpoints (two endpoints; library "retry all"
doesn't map to one run), and generalizing the Materials `refresh_sources`
machinery (entangles two independent flows / tables).

## Flow

```
User clicks "Retry" (one row) or "Retry all failed"
  → POST /api/sources/retry {business_id, row_ids}
  → backend authorizes rows in org, filters to retryable, marks each
    status='retrying', spawns background task, returns {queued: n}
  → background: for each row (bounded concurrency) re-run _scrape_one(url)
       success → UPDATE row source_id/word_count/status='extracted';
                 delete old orphan source (ref-count-guarded)
       fail    → UPDATE row status='failed' (linkage unchanged)
  → frontend already polls the sources list; row flips
    retrying → extracted/failed live
```

## Backend

### Route (`routes/sources.py`)
- `POST /api/sources/retry`, gated by `require_editor` (same as `bulk-delete`).
- Body: `{business_id, row_ids}` (reuse `SourceBulkDelete` or a sibling model).
- Validates each row is in the caller's org (`source_in_org`) and currently
  **retryable** (status is not `extracted` and not already `retrying`).
- Marks qualifying rows `status='retrying'`, launches the background task via the
  same background-task mechanism the research route uses, returns `{"queued": n}`.

### Service (`services/research.py`)
- New `retry_sources(client, db, business_id, row_ids)` beside
  `_scrape_one` / `_drop_source`.
- Per row, bounded by a semaphore like the main scrape loop:
  1. Capture `old_source_id`, `url`, `title`.
  2. `await _scrape_one(client, url, {url: title})`.
  3. On `extracted`: `UPDATE research_sources SET source_id, word_count,
     status='extracted' WHERE id = row`, then delete `old_source_id`
     **only if** `source_reference_count(db, old_source_id) == 0` (a fresh
     source id comes back on re-import; ref-count guard preserves shared
     sources — the orphan-cleanup rule validated in the PR #19 work).
  4. On non-`extracted`: `UPDATE ... SET status='failed'` (linkage unchanged).
- Wrap each row so one failure (or exception) cannot sink the batch; log and
  continue.

### Status marker
`research_sources.status` is freeform text with **no CHECK constraint**, so
`retrying` needs **no migration**. `is_usable_source` already treats non-
`extracted` as unusable, so a `retrying`/`failed` row is correctly excluded from
grounding until it succeeds.

### Retryable statuses
Any non-`extracted` terminal status is retryable: `failed`,
`attention_required`, `cancelled` — all left the source without usable content.
`retrying` is excluded (already in flight). `extracted` is excluded.

## Frontend

### Render status (currently absent in both views)
A small state chip on each source row: `failed` (red), `retrying…` (spinner);
an `extracted` source shows the normal word-count/trust badge as today.
- Sources library page: `app/brands/[id]/sources/page.tsx`
- Research tab per-run list: `app/brands/[id]/page.tsx`

### Actions (editor-gated via `canApprove`)
- Per-row **Retry** button, shown when the row is retryable (non-`extracted`,
  non-`retrying`).
- **Retry all failed** button: run header on the Research tab; toolbar on the
  Sources library page. Scoped to what's **visible** (this run on the Research
  tab; this brand's library list on the Sources page) — not a global sweep.
- Both call `sourcesApi.retry(business_id, row_ids)` — a single row vs. all
  failed row_ids in that scope.

### Live updates
`useRetrySources` hook. While any visible row is `retrying`, poll the existing
`useBrandSources` / run query on an interval (stop when none remain), mirroring
how the research run itself is polled.

### API client (`lib/api.ts`)
Add `sourcesApi.retry(business_id, row_ids)`. `BrandSource.status` already exists
in the TS type.

## Error handling / edge cases

- **Double-submit:** marking `retrying` before dispatch means a second click (or
  a second user) finds no qualifying rows → no-op; button disabled while
  `retrying`.
- **Persistent 429:** a retry that still fails just returns to `failed`; retry is
  available again. No infinite auto-loop — user-driven only.
- **Orphan safety:** old source deleted only when ref-count is 0 (shared sources
  preserved), reusing the guard validated in PR #19.
- **Row deleted mid-retry / run gone:** cascade + `WHERE id =` update affects 0
  rows; task logs and moves on.
- **Non-retryable input:** rows already `extracted` or `retrying` are filtered
  server-side, not merely hidden in the UI.

## Testing

### Backend (`tests/test_research.py` + a route test)
- retry-then-succeed updates linkage (`source_id`/`word_count`/`status`) and
  deletes the old orphan;
- retry-still-fails restores `status='failed'`, linkage unchanged;
- orphan **not** deleted when `source_reference_count > 0`;
- already-`extracted` / `retrying` rows filtered out of a retry request;
- org-authorization rejects foreign rows;
- batch survives one row throwing (others still processed).

### Frontend
- `tsc --noEmit` + `eslint` only. **Never** `next build` (crashes WSL).

## Non-goals

- Retroactive re-grounding / article regeneration after a late retry success.
- Global "retry every failed source across all brands" sweep.
- Changing the automatic in-run retry/backoff behavior (PR #19) — this is purely
  an additional user-driven path.
