# Source Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user re-scrape a failed research source on demand — per-source and "retry all failed" — from both the Sources library page and the Research tab.

**Architecture:** A single bulk endpoint `POST /api/sources/retry` (mirrors `bulk-delete`) takes `{business_id, row_ids}`. It atomically claims retryable rows (`status='retrying'`, the double-submit guard), spawns a background worker that re-scrapes each URL via the existing `_scrape_one`, updates the row to the fresh Powabase source, and deletes the old orphan (ref-count-guarded). Both frontends already poll their source list; they watch `retrying → extracted/failed`.

**Tech Stack:** Backend — Python 3.13, FastAPI, psycopg3, `uv run pytest` / `uv run ruff check`. Frontend — Next.js 16, React Query, verify via `npx tsc --noEmit` + `npm run lint`.

## Global Constraints

- Backend line length 88 (ruff `E501` ignored — long lines OK, but keep the style).
- Frontend: **NEVER** run `next build` (crashes WSL). Verify with `npx tsc --noEmit` and `npm run lint` only.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Push with `/usr/bin/git`. Never `git add .` — add explicit paths. Use `GIT_LITERAL_PATHSPECS=1` for any bracketed `[id]` path.
- Working tree: `/home/zipeng/worktrees/rankforge-retry`, branch `feat/source-retry`. Repo root is `example-apps`; app code is under `rankforge/`.
- Retryable = any status that is not `extracted` and not `retrying` (covers `failed`, `attention_required`, `cancelled`, and — server-side only — NULL). Frontend treats a NULL/absent status as **not** retryable (conservative).
- Reuse the ref-count orphan-cleanup contract already in this service: delete a Powabase Source only when `source_refs.source_reference_count(db, sid) == 0`.

---

### Task 1: `mark_sources_retrying` — atomic claim/guard (backend service)

Claims the retryable rows for a brand in one `UPDATE ... RETURNING`. The update IS the claim: a duplicate/concurrent request finds rows already `retrying` and gets nothing back. Org scoping is the join to `research_runs.business_id`.

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/research.py` (add function near `bulk_delete_brand_sources`, ~line 540)
- Test: `rankforge/backend/tests/test_research.py`

**Interfaces:**
- Consumes: `Database`, `UUID` (already imported in research.py).
- Produces: `mark_sources_retrying(db: Database, business_id: UUID, row_ids: list[UUID]) -> list[dict[str, Any]]` — returns claimed rows each with keys `id, source_id, url, title`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_research.py` (near the other service tests, after `test_delete_run_dedupes_same_source`):

```python
def test_mark_sources_retrying_empty_returns_early():
    db = MagicMock()
    assert svc.mark_sources_retrying(db, UUID(BID), []) == []
    db.fetch_all.assert_not_called()


def test_mark_sources_retrying_claims_and_returns_rows():
    db = MagicMock()
    claimed = [{"id": RID, "source_id": "old", "url": "https://x.com/a", "title": "A"}]
    db.fetch_all.return_value = claimed
    out = svc.mark_sources_retrying(db, UUID(BID), [UUID(RID)])
    assert out == claimed
    sql, params = db.fetch_all.call_args[0]
    assert "set status = 'retrying'" in sql
    assert "rr.business_id = %s" in sql            # brand-scoped
    assert "is distinct from 'extracted'" in sql   # never re-scrape a good source
    assert "is distinct from 'retrying'" in sql    # double-submit guard
    assert params == (UUID(BID), [UUID(RID)])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd rankforge/backend && uv run pytest tests/test_research.py -k mark_sources_retrying -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'mark_sources_retrying'`.

- [ ] **Step 3: Implement**

Add to `services/research.py`:

```python
def mark_sources_retrying(
    db: Database, business_id: UUID, row_ids: list[UUID]
) -> list[dict[str, Any]]:
    """Atomically CLAIM the retryable rows for a brand: set status='retrying' for rows
    that belong to `business_id` and are neither already 'extracted' nor already
    'retrying'. The UPDATE ... RETURNING is the claim — a duplicate/concurrent request
    finds the rows already 'retrying' and gets nothing back (double-submit guard).
    Returns the claimed rows (id, source_id, url, title) for the worker to re-scrape."""
    if not row_ids:
        return []
    return db.fetch_all(
        "update public.research_sources rs set status = 'retrying' "
        "from public.research_runs rr "
        "where rr.id = rs.research_run_id and rr.business_id = %s "
        "and rs.id = any(%s) "
        "and rs.status is distinct from 'extracted' "
        "and rs.status is distinct from 'retrying' "
        "returning rs.id, rs.source_id, rs.url, rs.title",
        (business_id, list(row_ids)),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rankforge/backend && uv run pytest tests/test_research.py -k mark_sources_retrying -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd rankforge/backend && uv run ruff check src/rankforge_backend/services/research.py tests/test_research.py
GIT_LITERAL_PATHSPECS=1 /usr/bin/git add rankforge/backend/src/rankforge_backend/services/research.py rankforge/backend/tests/test_research.py
/usr/bin/git commit -m "feat(research): mark_sources_retrying — atomic claim for source retry

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `retry_brand_sources` — background re-scrape worker (backend service)

Re-scrapes each claimed row and updates it in place: adopt the fresh Source, delete the old orphan (ref-count-guarded), or restore `failed` if nothing scraped. Bounded concurrency + shared retry budget, mirroring the main run scrape. One row's failure never sinks the batch.

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/research.py` (add after `mark_sources_retrying`)
- Test: `rankforge/backend/tests/test_research.py`

**Interfaces:**
- Consumes: `_scrape_one`, `_RetryBudget`, `SCRAPE_CONCURRENCY`, `MAX_RUN_RETRIES`, `source_refs.source_reference_count`, `CompetitorTeardown` (all already in research.py). Rows are the dicts returned by `mark_sources_retrying` (keys `id, source_id, url, title`).
- Produces: `async retry_brand_sources(client: PowabaseClient, db: Database, business_id: UUID, rows: list[dict[str, Any]]) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_research.py`. (`RID` and `BID` already defined at module top; `AsyncMock`, `MagicMock` already imported.)

```python
FAILED_ROW = {"id": RID, "source_id": "old", "url": "https://x.com/a", "title": "A"}


def _scrape_result(source_id, status, word_count):
    td = svc.CompetitorTeardown(
        url="https://x.com/a", title="A", word_count=word_count, source_id=source_id
    )
    return {
        "teardown": td, "status": status, "source_id": source_id,
        "url": "https://x.com/a", "attempts": 1, "excerpt": "",
    }


async def test_retry_brand_sources_success_adopts_and_deletes_old(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, sid, **k: 0)
    monkeypatch.setattr(
        svc, "_scrape_one",
        AsyncMock(return_value=_scrape_result("new", "extracted", 321)),
    )
    client = MagicMock()
    client.delete_source = AsyncMock()

    await svc.retry_brand_sources(client, db, UUID(BID), [dict(FAILED_ROW)])

    upd = db.aexecute.await_args_list[0].args
    assert "set source_id = %s" in upd[0]
    assert upd[1] == ("new", 321, "extracted", RID)   # row now points at the fresh Source
    client.delete_source.assert_awaited_once_with("old")  # stale orphan removed


async def test_retry_brand_sources_keeps_shared_old_source(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, sid, **k: 1)
    monkeypatch.setattr(
        svc, "_scrape_one",
        AsyncMock(return_value=_scrape_result("new", "extracted", 200)),
    )
    client = MagicMock()
    client.delete_source = AsyncMock()

    await svc.retry_brand_sources(client, db, UUID(BID), [dict(FAILED_ROW)])

    client.delete_source.assert_not_awaited()  # old Source still referenced → kept


async def test_retry_brand_sources_none_restores_failed(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    monkeypatch.setattr(svc, "_scrape_one", AsyncMock(return_value=None))
    client = MagicMock()
    client.delete_source = AsyncMock()

    await svc.retry_brand_sources(client, db, UUID(BID), [dict(FAILED_ROW)])

    upd = db.aexecute.await_args_list[0].args
    assert "set status = 'failed'" in upd[0]
    assert upd[1] == (RID,)                       # linkage unchanged, back to failed
    client.delete_source.assert_not_awaited()


async def test_retry_brand_sources_one_failure_does_not_sink_batch(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, sid, **k: 0)

    async def scrape(client, d, url, tbu, budget=None):
        if url.endswith("boom"):
            raise RuntimeError("kaboom")
        return _scrape_result("new2", "extracted", 250)

    monkeypatch.setattr(svc, "_scrape_one", scrape)
    client = MagicMock()
    client.delete_source = AsyncMock()
    boom = {"id": "44444444-4444-4444-4444-444444444444",
            "source_id": "o1", "url": "https://x.com/boom", "title": "b"}
    ok = {"id": RID, "source_id": "o2", "url": "https://x.com/ok", "title": "ok"}

    await svc.retry_brand_sources(client, db, UUID(BID), [boom, ok])

    calls = [c.args for c in db.aexecute.await_args_list]
    # the good row was updated to extracted, the boom row restored to failed
    assert any(a[1] == ("new2", 250, "extracted", RID) for a in calls)
    assert any("set status = 'failed'" in a[0] and a[1] == (boom["id"],) for a in calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd rankforge/backend && uv run pytest tests/test_research.py -k retry_brand_sources -v`
Expected: FAIL — `AttributeError: ... 'retry_brand_sources'`.

- [ ] **Step 3: Implement**

Add to `services/research.py` (after `mark_sources_retrying`):

```python
async def retry_brand_sources(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    rows: list[dict[str, Any]],
) -> None:
    """Re-scrape each claimed source row (already marked 'retrying' by
    mark_sources_retrying) and update it in place. Bounded concurrency + a shared retry
    budget, mirroring the main run scrape. Per row: re-run _scrape_one on its URL, then
    ADOPT the fresh Source (new source_id/word_count/status) and delete the OLD Source
    when nothing else references it — so a retry never orphans a Source or leaves two per
    row. If the re-import yields no Source at all (or the row has no URL), restore the row
    to 'failed' with its original linkage. One row's failure never sinks the batch.
    `business_id` is unused in the query bodies (rows were already brand-scoped by the
    claim) but kept in the signature so the worker's provenance is explicit at the call
    site."""
    sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
    budget = _RetryBudget(MAX_RUN_RETRIES)

    async def _one(row: dict[str, Any]) -> None:
        row_id = row["id"]
        old_sid = row.get("source_id")
        url = row.get("url")
        title_by_url = {url: row.get("title") or ""} if url else {}
        async with sem:
            try:
                res = (
                    await _scrape_one(client, db, url, title_by_url, budget)
                    if url else None
                )
            except Exception:  # noqa: BLE001 — one row must not sink the batch
                log.exception("source retry failed for row %s", row_id)
                res = None
        if res is None:
            # Nothing scraped — leave 'retrying' for 'failed' so the row stays retryable;
            # keep its original linkage.
            await db.aexecute(
                "update public.research_sources set status = 'failed' where id = %s",
                (row_id,),
            )
            return
        new_sid = res["source_id"]
        t = res["teardown"]
        await db.aexecute(
            "update public.research_sources "
            "set source_id = %s, word_count = %s, status = %s where id = %s",
            (new_sid, t.word_count, res["status"], row_id),
        )
        # Adopt the fresh Source; delete the stale one if nothing else references it
        # (same ref-count contract as every other delete in this service). The count is a
        # sync DB call — offload it so it doesn't block the gather loop.
        if old_sid and new_sid != old_sid:
            try:
                refs = await asyncio.to_thread(
                    source_refs.source_reference_count, db, old_sid
                )
                if refs == 0:
                    await client.delete_source(old_sid)
            except Exception:  # noqa: BLE001 — remote cleanup is best-effort
                log.exception("retry: could not delete old source %s", old_sid)

    await asyncio.gather(*[_one(r) for r in rows])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rankforge/backend && uv run pytest tests/test_research.py -k retry_brand_sources -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd rankforge/backend && uv run ruff check src/rankforge_backend/services/research.py tests/test_research.py
GIT_LITERAL_PATHSPECS=1 /usr/bin/git add rankforge/backend/src/rankforge_backend/services/research.py rankforge/backend/tests/test_research.py
/usr/bin/git commit -m "feat(research): retry_brand_sources — background re-scrape worker

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `POST /api/sources/retry` route

Editor-gated. Brand-scopes + claims via `mark_sources_retrying`, spawns `retry_brand_sources` when rows were claimed, returns `{queued: n}` (202).

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/routes/sources.py` (add import + route near `bulk_delete_sources`, ~line 47)
- Test: `rankforge/backend/tests/test_research.py`

**Interfaces:**
- Consumes: `svc.mark_sources_retrying`, `svc.retry_brand_sources` (Tasks 1–2); `spawn` from `..tasks`; `SourceBulkDelete` (already imported in sources.py); `require_editor`, `assert_brand_access` (already imported).
- Produces: `POST /api/sources/retry` → `{"queued": int}`, status 202.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_research.py` (near the other route tests, after `test_delete_research_route`). Note the route calls `spawn` imported into the **route module** — patch it there.

```python
def test_retry_sources_route_queues_claimed_rows(monkeypatch):
    claimed = [{"id": RID, "source_id": "old", "url": "https://x.com/a", "title": "A"}]
    monkeypatch.setattr(svc, "mark_sources_retrying", lambda db, bid, ids: claimed)
    monkeypatch.setattr(svc, "retry_brand_sources", AsyncMock())
    spawned = []

    def fake_spawn(coro):
        spawned.append(coro)
        coro.close()  # we asserted dispatch; don't actually run the worker

    monkeypatch.setattr("rankforge_backend.routes.sources.spawn", fake_spawn)
    resp = make_client().post(
        "/api/sources/retry", json={"business_id": BID, "row_ids": [RID]}
    )
    assert resp.status_code == 202
    assert resp.json() == {"queued": 1}
    assert len(spawned) == 1


def test_retry_sources_route_no_claimed_rows_no_spawn(monkeypatch):
    monkeypatch.setattr(svc, "mark_sources_retrying", lambda db, bid, ids: [])
    spawned = []
    monkeypatch.setattr(
        "rankforge_backend.routes.sources.spawn", lambda c: spawned.append(c)
    )
    resp = make_client().post(
        "/api/sources/retry", json={"business_id": BID, "row_ids": [RID]}
    )
    assert resp.status_code == 202
    assert resp.json() == {"queued": 0}
    assert not spawned
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd rankforge/backend && uv run pytest tests/test_research.py -k retry_sources_route -v`
Expected: FAIL — 404 (route not registered) or import error for `spawn`.

- [ ] **Step 3: Implement**

In `routes/sources.py`, add the import near the other `..` imports (after line 11 `from ..powabase import ...`):

```python
from ..tasks import spawn
```

Add the route right after `bulk_delete_sources` (after line 46):

```python
@router.post("/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_sources(
    payload: SourceBulkDelete,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Re-scrape the selected failed / non-extracted sources. Editor/admin only. Rows are
    brand-scoped and claimed atomically (already-extracted or in-flight rows are ignored),
    then re-scraped in the background — poll GET /api/sources for each row's status
    (retrying → extracted/failed). Returns how many rows were queued."""
    assert_brand_access(db, payload.business_id, user)
    rows = svc.mark_sources_retrying(db, payload.business_id, payload.row_ids)
    if rows:
        spawn(svc.retry_brand_sources(pb, db, payload.business_id, rows))
    return {"queued": len(rows)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rankforge/backend && uv run pytest tests/test_research.py -k "retry_sources_route or retry_brand_sources or mark_sources_retrying" -v`
Expected: PASS (all 8 backend retry tests).

- [ ] **Step 5: Full backend suite + lint + commit**

```bash
cd rankforge/backend && uv run pytest -q && uv run ruff check src/rankforge_backend/routes/sources.py
GIT_LITERAL_PATHSPECS=1 /usr/bin/git add rankforge/backend/src/rankforge_backend/routes/sources.py rankforge/backend/tests/test_research.py
/usr/bin/git commit -m "feat(sources): POST /api/sources/retry to re-scrape failed sources

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Frontend data layer — API client + hooks

Add the `ResearchSource` type, a `researchApi.sources(runId)` fetch (existing endpoint the UI didn't call), a `sourcesApi.retry(...)` POST helper (mirrors `bulkDelete`), a `useRetrySources` mutation, a `useRunSources` polling query, and retry-polling on `useBrandSources`.

**Files:**
- Modify: `rankforge/frontend/src/lib/api.ts`
- Modify: `rankforge/frontend/src/lib/hooks/useResearch.ts`

**Interfaces:**
- Consumes: `request<T>`, `researchApi`, `sourcesApi`, `BrandSource` (all in api.ts); `useQuery`, `useMutation`, `useQueryClient` (already in the hooks file).
- Produces:
  - `interface ResearchSource { id; research_run_id; source_id; url?; title?; word_count?; status?; trust_score?; trust_reason?; created_at }`
  - `researchApi.sources(runId: string): Promise<ResearchSource[]>`
  - `sourcesApi.retry(businessId: string, rowIds: string[]): Promise<{ queued: number }>`
  - `useRetrySources(businessId: string)` — mutation over `string[]` row ids.
  - `useRunSources(runId: string | null)` — query returning `ResearchSource[]`, polls while any row is `retrying`.

- [ ] **Step 1: Add the `ResearchSource` interface**

In `api.ts`, add right after the `CompetitorTeardown` interface (ends ~line 179):

```ts
export interface ResearchSource {
  id: string;
  research_run_id: string;
  source_id: string;
  url?: string | null;
  title?: string | null;
  word_count?: number | null;
  status?: string | null;
  trust_score?: number | null;
  trust_reason?: string | null;
  created_at: string;
}
```

- [ ] **Step 2: Add `researchApi.sources` and `sourcesApi.retry`**

In `researchApi` (the object at ~lines 210-227), add a method (after `get`):

```ts
  sources: (runId: string) =>
    request<ResearchSource[]>(`/api/research/${runId}/sources`),
```

In `sourcesApi` (ends ~line 311), add after `bulkDelete` (mirrors its batching — `row_ids` is capped at 500/request server-side):

```ts
  retry: async (
    businessId: string,
    rowIds: string[]
  ): Promise<{ queued: number }> => {
    const BATCH = 500;
    let queued = 0;
    for (let i = 0; i < rowIds.length; i += BATCH) {
      const res = await request<{ queued: number }>(`/api/sources/retry`, {
        method: "POST",
        body: JSON.stringify({
          business_id: businessId,
          row_ids: rowIds.slice(i, i + BATCH),
        }),
      });
      queued += res.queued;
    }
    return { queued };
  },
```

- [ ] **Step 3: Wire the hooks**

In `useResearch.ts`, ensure the `@/lib/api` import group includes `researchApi`, `sourcesApi`, `type BrandSource`, and `type ResearchSource` (add the missing names to the existing grouped import — first run `grep -n "from \"@/lib/api\"" src/lib/hooks/useResearch.ts` and add to that line's `{ ... }`).

Replace the existing `useBrandSources` (lines 23-29) with a polling version:

```ts
export function useBrandSources(businessId: string) {
  return useQuery({
    queryKey: ["sources", businessId],
    queryFn: () => sourcesApi.listByBrand(businessId),
    enabled: !!businessId,
    // Poll while any source is mid-retry so its chip flips to extracted/failed live.
    refetchInterval: (query) => {
      const rows = query.state.data as BrandSource[] | undefined;
      return rows?.some((s) => s.status === "retrying") ? 2500 : false;
    },
  });
}
```

Add two new hooks (place after `useDeleteBrandSources`, ~line 119):

```ts
export function useRunSources(runId: string | null) {
  return useQuery({
    queryKey: ["run-sources", runId],
    queryFn: () => researchApi.sources(runId as string),
    enabled: !!runId,
    // Poll while any of the run's sources is being retried, so the row flips live.
    refetchInterval: (query) => {
      const rows = query.state.data as ResearchSource[] | undefined;
      return rows?.some((s) => s.status === "retrying") ? 2500 : false;
    },
  });
}

export function useRetrySources(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rowIds: string[]) => sourcesApi.retry(businessId, rowIds),
    // Rows are now 'retrying' server-side; refetch both source views so the chip flips
    // and polling engages. Invalidate on settle — a partial error still claimed rows.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["sources", businessId] });
      qc.invalidateQueries({ queryKey: ["research", businessId] });
      qc.invalidateQueries({ queryKey: ["run-sources"] });
    },
  });
}
```

- [ ] **Step 4: Verify types + lint**

Run: `cd rankforge/frontend && npx tsc --noEmit && npm run lint`
Expected: no errors. (Do NOT run `next build`.)

- [ ] **Step 5: Commit**

```bash
GIT_LITERAL_PATHSPECS=1 /usr/bin/git add rankforge/frontend/src/lib/api.ts rankforge/frontend/src/lib/hooks/useResearch.ts
/usr/bin/git commit -m "feat(frontend): source-retry API client + hooks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Shared `SourceStatus` + Sources library retry UI

Create a shared `StatusChip` + `isRetryable` (used by this page and the Research tab), then render a status chip, a per-row Retry button, and a "Retry all failed" toolbar button on the library page.

**Files:**
- Create: `rankforge/frontend/src/components/SourceStatus.tsx`
- Modify: `rankforge/frontend/src/app/brands/[id]/sources/page.tsx`

**Interfaces:**
- Consumes: `useRetrySources` (Task 4); `Button` (already imported in the page); `Loader2`, `RotateCw` from `lucide-react`; `toast` from `sonner` (already imported).
- Produces: `SourceStatus.tsx` exporting `StatusChip({ status }: { status?: string | null })` and `isRetryable(status?: string | null): boolean`.

- [ ] **Step 1: Create the shared component**

Create `rankforge/frontend/src/components/SourceStatus.tsx`:

```tsx
import { Loader2 } from "lucide-react";

/** A source row is retryable when its scrape did NOT leave usable content and it isn't
 *  already in flight. A NULL/absent status (legacy rows) is treated as not retryable. */
export const isRetryable = (status?: string | null): boolean =>
  !!status && status !== "extracted" && status !== "retrying";

/** Scrape state chip for a source row. 'extracted' renders nothing (word-count + trust
 *  badge already convey success); only the not-yet-usable states show a chip. */
export function StatusChip({ status }: { status?: string | null }) {
  if (!status || status === "extracted") return null;
  if (status === "retrying")
    return (
      <span className="inline-flex items-center gap-1 rounded bg-[rgb(var(--gold))]/15 px-1.5 py-0.5 text-[rgb(var(--accent-gold-hover))]">
        <Loader2 className="size-3 animate-spin" /> retrying
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 rounded bg-destructive/15 px-1.5 py-0.5 text-destructive">
      failed
    </span>
  );
}
```

- [ ] **Step 2: Wire the library page — imports + hook + handler**

In `src/app/brands/[id]/sources/page.tsx`:

Add to the `lucide-react` import: `RotateCw` (and `Loader2` if not already imported — it is used by the delete button, so it's present).

Add a new import:

```tsx
import { StatusChip, isRetryable } from "@/components/SourceStatus";
```

Inside `SourcesLibrary`, after `const del = useDeleteBrandSources(id);` (line 50), add:

```tsx
  const retry = useRetrySources(id);
```

(and add `useRetrySources` to the existing `@/lib/hooks/useResearch` import.)

After the `onDelete` function (ends ~line 106), add:

```tsx
  function onRetry(ids: string[]) {
    if (!ids.length) return;
    retry.mutate(ids, {
      onSuccess: (r) =>
        toast.success(
          r.queued
            ? `Retrying ${r.queued} source${r.queued === 1 ? "" : "s"}…`
            : "Nothing to retry"
        ),
      onError: (e) => toast.error(e instanceof Error ? e.message : "Retry failed"),
    });
  }

  const failedIds = (sources ?? []).filter((s) => isRetryable(s.status)).map((s) => s.id);
```

- [ ] **Step 3: Wire the toolbar "Retry all failed" button**

In the `canEdit && !!sources?.length` toolbar (lines 119-147), add — right after the `<label>…Select all…</label>` block and before the `checked.size > 0 && (<Button …Delete>)` block:

```tsx
              {failedIds.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-muted-foreground hover:text-foreground"
                  onClick={() => onRetry(failedIds)}
                  disabled={retry.isPending}
                >
                  {retry.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <RotateCw />
                  )}
                  Retry {failedIds.length} failed
                </Button>
              )}
```

(The existing Delete button keeps its `ml-auto`, so it stays right-aligned; the retry button sits left of it.)

- [ ] **Step 4: Wire the per-row chip + Retry button**

In the row `<li>` (lines 165-203):

(a) Add the chip inside the meta flex row, right after `<TrustBadge ... />`:

```tsx
                      <StatusChip status={s.status} />
```

(b) Add a per-row Retry button as a **sibling** of the big `<button>` (outside it, so its click doesn't trigger `setSelected`), right before the closing `</li>`:

```tsx
                  {canEdit && isRetryable(s.status) && (
                    <div className="flex items-center border-b border-border pr-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-muted-foreground hover:text-foreground"
                        onClick={() => onRetry([s.id])}
                        disabled={retry.isPending}
                      >
                        <RotateCw className="size-3.5" /> Retry
                      </Button>
                    </div>
                  )}
```

- [ ] **Step 5: Verify types + lint**

Run: `cd rankforge/frontend && npx tsc --noEmit && npm run lint`
Expected: no errors. (Never `next build`.)

- [ ] **Step 6: Commit**

```bash
GIT_LITERAL_PATHSPECS=1 /usr/bin/git add rankforge/frontend/src/components/SourceStatus.tsx "rankforge/frontend/src/app/brands/[id]/sources/page.tsx"
/usr/bin/git commit -m "feat(frontend): retry failed sources on the library page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Research tab — "Failed scrapes" retry block

Inside `RunDetail`, fetch the run's sources (which carry row `id` + `status`, unlike `run.competitors`) and render a compact failed-scrapes panel with per-row Retry + "Retry all". Pure addition — the existing "Scraped sources (Powabase)" list is untouched.

**Files:**
- Modify: `rankforge/frontend/src/app/brands/[id]/page.tsx`

**Interfaces:**
- Consumes: `useRunSources`, `useRetrySources` (Task 4); `StatusChip`, `isRetryable` (Task 5); `Button` (already imported); `RotateCw` from `lucide-react`; `toast` from `sonner` (already imported, line 16). `RunDetail` already receives `run` (has `run.id`), `brandId`, `canEdit`.
- Produces: no new exports (UI only).

- [ ] **Step 1: Add imports**

In `src/app/brands/[id]/page.tsx`:
- Add `RotateCw` to the `lucide-react` import.
- Add: `import { StatusChip, isRetryable } from "@/components/SourceStatus";`
- Add `useRunSources` and `useRetrySources` to the existing `@/lib/hooks/useResearch` import.

- [ ] **Step 2: Fetch run sources + handler in `RunDetail`**

Inside `RunDetail` (after `const del = useDeleteResearchRun(brandId);`, line 335), add:

```tsx
  const { data: runSources } = useRunSources(run.id);
  const retry = useRetrySources(brandId);
  const retryable = (runSources ?? []).filter((s) => isRetryable(s.status));
  const anyRetrying = (runSources ?? []).some((s) => s.status === "retrying");

  function onRetry(ids: string[]) {
    if (!ids.length) return;
    retry.mutate(ids, {
      onSuccess: (r) =>
        toast.success(
          r.queued
            ? `Retrying ${r.queued} source${r.queued === 1 ? "" : "s"}…`
            : "Nothing to retry"
        ),
      onError: (e) => toast.error(e instanceof Error ? e.message : "Retry failed"),
    });
  }
```

- [ ] **Step 3: Render the "Failed scrapes" panel**

In `RunDetail`'s JSX, add this block right after the existing "Scraped sources (Powabase)" `{run.competitors.length > 0 && (…)}` block (ends ~line 438). It shows only when the run has non-extracted sources and the user can edit:

```tsx
        {canEdit && (retryable.length > 0 || anyRetrying) && (
          <div>
            <div className="mb-1.5 flex items-center gap-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Failed scrapes
              </p>
              {retryable.length > 1 && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto h-7 text-muted-foreground hover:text-foreground"
                  onClick={() => onRetry(retryable.map((s) => s.id))}
                  disabled={retry.isPending}
                >
                  <RotateCw className="size-3.5" /> Retry all {retryable.length}
                </Button>
              )}
            </div>
            <div className="grid gap-2">
              {(runSources ?? [])
                .filter((s) => s.status !== "extracted")
                .map((s) => (
                  <div
                    key={s.id}
                    className="flex items-center gap-3 rounded-md border border-border p-2.5"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="line-clamp-1 text-sm font-medium">
                        {s.title || s.url}
                      </div>
                      <div className="line-clamp-1 text-xs text-muted-foreground">
                        {s.url}
                      </div>
                    </div>
                    <StatusChip status={s.status} />
                    {isRetryable(s.status) && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-muted-foreground hover:text-foreground"
                        onClick={() => onRetry([s.id])}
                        disabled={retry.isPending}
                      >
                        <RotateCw className="size-3.5" /> Retry
                      </Button>
                    )}
                  </div>
                ))}
            </div>
          </div>
        )}
```

- [ ] **Step 4: Verify types + lint**

Run: `cd rankforge/frontend && npx tsc --noEmit && npm run lint`
Expected: no errors. (Never `next build`.)

- [ ] **Step 5: Commit**

```bash
GIT_LITERAL_PATHSPECS=1 /usr/bin/git add "rankforge/frontend/src/app/brands/[id]/page.tsx"
/usr/bin/git commit -m "feat(frontend): retry failed scrapes from the Research tab

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] Backend: `cd rankforge/backend && uv run pytest -q && uv run ruff check src tests`
- [ ] Frontend: `cd rankforge/frontend && npx tsc --noEmit && npm run lint`
- [ ] Manual smoke (optional, local): trigger a research run that fails a source (or use an existing run with a `failed` source), click **Retry** on the library page and the Research tab, confirm the chip goes `failed → retrying → extracted/failed`, and that a successful retry makes "view scraped text" work.

## Notes / deviations from the spec

- **Research-tab wiring:** the spec assumed a per-source retry could hang off the tab's existing source list, but that list renders `run.competitors` (`CompetitorTeardown`), which has **no row `id` and no `status`**. Rather than change that payload, the tab now fetches the run's sources from the **existing** `GET /api/research/{run_id}/sources` endpoint (returns `ResearchSource` with `id` + `status`) and renders a dedicated "Failed scrapes" panel. Same user-facing outcome ("both surfaces"), no backend payload change.
- **Retryable statuses:** server-side, any non-`extracted`/non-`retrying` status (incl. NULL) is claimable; the frontend is conservative and hides retry for NULL/absent status (legacy rows).
