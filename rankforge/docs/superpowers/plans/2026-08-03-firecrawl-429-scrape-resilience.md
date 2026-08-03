# Research scrape resilience (Firecrawl 429/5xx) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop research scrapes from silently dropping URLs to Firecrawl rate limits — dispatch gentler, and retry the transiently-failed sources instead of losing them.

**Architecture:** All changes live in `services/research.py`. A run-global pacer spaces scrape dispatches; `_scrape_one` is refactored into a poll-and-classify attempt wrapped in a bounded retry loop that re-imports (re-runs extraction) only on transient failures, identified from the source's `error_message`. Concurrency drops from 8 to 3. No client change, no migration, no frontend.

**Tech Stack:** Python 3.13, asyncio, pytest (`asyncio_mode = "auto"` — async tests need no decorator), ruff (line-length is convention; E501 is ignored in config), `uv run` from `rankforge/backend`.

## Global Constraints

- Backend only. No `PowabaseClient` change, no DB migration, no API change, no frontend change.
- Recovery re-runs extraction by calling `import_url(url)` again (re-import re-dispatches extraction on the deduped source) — no new client method.
- A failure is retried ONLY when transient (429/5xx/timeout markers in `error_message`); permanent failures (404, blocked, empty message) are never retried.
- Conservative constants, no env config knob, no exact Firecrawl rate number.
- `_scrape_one`'s existing return shape is preserved and extended with one field (`attempts`); all existing keys (`teardown`, `status`, `source_id`, `url`, `excerpt`) keep their meaning.
- Every `import_url` (first attempt and each retry) is preceded by `await _scrape_pacer.wait()`.
- Run all commands with `uv run` from `rankforge/backend`. Use `/usr/bin/git` from the repo root `/home/zipeng/worktrees/rankforge-scrape`.
- Test imports at the top of the file (mid-file imports trip ruff E402). `tests/test_research.py` already imports `AsyncMock, MagicMock`, `pytest`, and `from rankforge_backend.services import research as svc`.
- Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `services/research.py` (modify) | Resilience constants; `_is_transient_failure`; `_ScrapePacer` + `_scrape_pacer`; `_scrape_attempt` + `_scrape_one` retry loop; lower `SCRAPE_CONCURRENCY`; `_scrape_summary` + run summary log |
| `tests/test_research.py` (modify) | Classifier truth table, pacer spacing, `_scrape_one` retry behaviors, summary counts |

## Context the implementer needs

Current `_scrape_one` (`services/research.py:474-518`) does: `import_url` → poll `get_source` up to 40×2s until `extraction_status` is in `EXTRACTION_TERMINAL` (`{"extracted", "attention_required", "failed", "cancelled"}`) → on `extracted`, fetch markdown → build a `CompetitorTeardown`. The module already has `import asyncio`, `log = logging.getLogger("rankforge.research")`, `from ..powabase import EXTRACTION_TERMINAL, PowabaseClient, PowabaseError`, and `from ..models.research import CompetitorTeardown`. A failed source carries the reason in `src["error_message"]` (e.g. `"Client error '429 Too Many Requests' for url 'https://api.firecrawl.dev/v1/scrape'"`).

---

### Task 1: Transient-failure classifier + resilience constants

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/research.py` (add constants + `_is_transient_failure` near `SCRAPE_CONCURRENCY`, ~line 54)
- Test: `rankforge/backend/tests/test_research.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `MAX_SCRAPE_RETRIES: int` (2), `RETRY_BACKOFF: tuple[float, ...]` (`(5.0, 15.0)`), `MIN_SCRAPE_INTERVAL: float` (0.75), `_TRANSIENT_MARKERS: tuple[str, ...]`
  - `_is_transient_failure(error_message: str | None) -> bool`

- [ ] **Step 1: Write the failing test**

Append to `rankforge/backend/tests/test_research.py`:

```python
def test_is_transient_failure_classifies_by_error_message():
    for msg in (
        "Client error '429 Too Many Requests' for url 'https://api.firecrawl.dev/v1/scrape'",
        "Server error '502 Bad Gateway' for url 'https://api.firecrawl.dev/v1/scrape'",
        "503 Service Unavailable",
        "Gateway Timeout",
        "read timed out",
    ):
        assert svc._is_transient_failure(msg), msg
    for msg in (
        "Client error '404 Not Found'",
        "blocked by robots.txt",
        "could not parse document",
        "",
        None,
    ):
        assert not svc._is_transient_failure(msg), msg
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/zipeng/worktrees/rankforge-scrape/rankforge/backend
uv run pytest tests/test_research.py::test_is_transient_failure_classifies_by_error_message -q
```

Expected: FAIL — `AttributeError: module ... has no attribute '_is_transient_failure'`

- [ ] **Step 3: Add the constants and classifier**

In `services/research.py`, immediately after the `SCRAPE_CONCURRENCY = 8` block (before `MIN_SOURCE_WORDS`), insert:

```python
# --- scrape resilience (Firecrawl rate limits) -------------------------------------
# Powabase runs each scrape through Firecrawl in a background job, so a burst of
# concurrent imports rate-limits it and the source ends `failed` with a 429/5xx
# error_message — a failure RankForge never sees as an HTTP status. We dispatch gently
# (see _ScrapePacer) and retry the TRANSIENT failures (re-import re-runs extraction).
MIN_SCRAPE_INTERVAL = 0.75   # min seconds between scrape dispatches (_ScrapePacer)
MAX_SCRAPE_RETRIES = 2       # extra attempts after the first, for transient failures
RETRY_BACKOFF = (5.0, 15.0)  # backoff before retry 1, retry 2 (len == MAX_SCRAPE_RETRIES)

# Substrings marking a TRANSIENT extraction failure worth retrying (rate limit / upstream
# blip). Anything else (404, blocked, parse error, no message) is permanent and never
# retried, so a genuinely dead page is not re-scraped.
_TRANSIENT_MARKERS = (
    "429", "too many requests", "502", "bad gateway",
    "503", "service unavailable", "504", "gateway timeout",
    "timeout", "timed out",
)


def _is_transient_failure(error_message: str | None) -> bool:
    """True if a failed extraction is worth retrying; False for a permanent failure."""
    if not error_message:
        return False
    low = error_message.lower()
    return any(m in low for m in _TRANSIENT_MARKERS)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/zipeng/worktrees/rankforge-scrape/rankforge/backend
uv run pytest tests/test_research.py::test_is_transient_failure_classifies_by_error_message -q && uv run ruff check src tests
```

Expected: PASS, then `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-scrape
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/research.py \
  rankforge/backend/tests/test_research.py
/usr/bin/git commit -m "feat(research): classify transient vs permanent scrape failures

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Dispatch pacer + lower concurrency

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/research.py` (add `_ScrapePacer` + `_scrape_pacer`; change `SCRAPE_CONCURRENCY`)
- Test: `rankforge/backend/tests/test_research.py`

**Interfaces:**
- Consumes: `MIN_SCRAPE_INTERVAL` from Task 1.
- Produces:
  - `class _ScrapePacer` with `__init__(self, interval: float)` and `async def wait(self) -> None`
  - `_scrape_pacer: _ScrapePacer` (module-level instance)
  - `SCRAPE_CONCURRENCY` is now `3`

- [ ] **Step 1: Write the failing test**

Append to `rankforge/backend/tests/test_research.py`:

```python
async def test_scrape_pacer_spaces_consecutive_starts():
    import asyncio as aio

    pacer = svc._ScrapePacer(0.05)
    loop = aio.get_running_loop()
    start = loop.time()
    await pacer.wait()  # first call returns immediately
    mid = loop.time()
    await pacer.wait()  # second call waits out the interval
    end = loop.time()
    assert mid - start < 0.05          # first didn't block
    assert end - start >= 0.05         # second was spaced by >= interval


def test_scrape_concurrency_lowered():
    assert svc.SCRAPE_CONCURRENCY == 3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/zipeng/worktrees/rankforge-scrape/rankforge/backend
uv run pytest tests/test_research.py -k "scrape_pacer or concurrency_lowered" -q
```

Expected: FAIL — `AttributeError: ... '_ScrapePacer'` and the concurrency assert fails (still 8).

- [ ] **Step 3: Lower concurrency and add the pacer**

In `services/research.py`, change the `SCRAPE_CONCURRENCY` block to:

```python
# How many competitor pages to import/poll/extract concurrently. Kept low (was 8) so the
# simultaneous Firecrawl dispatch doesn't trip its rate limit; combined with the pacer
# below, scrape starts stay spread out. Each page still polls up to ~80s.
SCRAPE_CONCURRENCY = 3
```

Then, right after the `_is_transient_failure` function added in Task 1, add:

```python
class _ScrapePacer:
    """Serialize scrape STARTS to at most one per `interval` seconds, so scrapes spread
    over time even when several concurrency slots free at the same instant. Uses the
    event loop clock (monotonic; every scrape shares the app's loop)."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = loop.time() + self._interval


# Module-level so it also paces across concurrent research runs — the rate Firecrawl
# actually cares about.
_scrape_pacer = _ScrapePacer(MIN_SCRAPE_INTERVAL)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/zipeng/worktrees/rankforge-scrape/rankforge/backend
uv run pytest tests/test_research.py -k "scrape_pacer or concurrency_lowered" -q && uv run ruff check src tests
```

Expected: PASS, then `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-scrape
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/research.py \
  rankforge/backend/tests/test_research.py
/usr/bin/git commit -m "feat(research): pace scrape dispatch and lower concurrency to 3

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Retry transient scrape failures in `_scrape_one`

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/research.py:474-518` (replace `_scrape_one`; add `_scrape_attempt`)
- Test: `rankforge/backend/tests/test_research.py`

**Interfaces:**
- Consumes: `_is_transient_failure`, `MAX_SCRAPE_RETRIES`, `RETRY_BACKOFF` (Task 1); `_scrape_pacer` (Task 2); `EXTRACTION_TERMINAL`, `CompetitorTeardown`, `_first_title`, `_extract_headings` (existing).
- Produces:
  - `async def _scrape_attempt(client, url) -> tuple[str | None, str | None, str, str | None]` returning `(source_id, status, markdown, error_message)`
  - `_scrape_one` returns the same dict as before **plus** `"attempts": int` (number of import attempts made). Returns `None` only when the first import yields no source id.

- [ ] **Step 1: Write the failing tests**

Append to `rankforge/backend/tests/test_research.py`:

```python
def _failed(msg):
    return {"extraction_status": "failed", "error_message": msg}


_EXTRACTED = {"extraction_status": "extracted", "error_message": None}


async def test_scrape_one_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(svc.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(svc._scrape_pacer, "wait", AsyncMock())
    client = MagicMock()
    client.import_url = AsyncMock(return_value={"sources": [{"id": "s1"}]})
    # attempt 1 polls once → failed(429); attempt 2 polls once → extracted
    client.get_source = AsyncMock(
        side_effect=[_failed("Client error '429 Too Many Requests'"), _EXTRACTED]
    )
    client.get_source_markdown = AsyncMock(return_value="# Title\n\nreal content here")
    out = await svc._scrape_one(client, "https://x.com/a", {})
    assert client.import_url.await_count == 2
    assert out["status"] == "extracted"
    assert out["attempts"] == 2
    assert (out["teardown"].word_count or 0) > 0


async def test_scrape_one_does_not_retry_permanent_failure(monkeypatch):
    monkeypatch.setattr(svc.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(svc._scrape_pacer, "wait", AsyncMock())
    client = MagicMock()
    client.import_url = AsyncMock(return_value={"sources": [{"id": "s1"}]})
    client.get_source = AsyncMock(return_value=_failed("Client error '404 Not Found'"))
    out = await svc._scrape_one(client, "https://x.com/dead", {})
    assert client.import_url.await_count == 1
    assert out["status"] == "failed"
    assert out["attempts"] == 1


async def test_scrape_one_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(svc.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(svc._scrape_pacer, "wait", AsyncMock())
    client = MagicMock()
    client.import_url = AsyncMock(return_value={"sources": [{"id": "s1"}]})
    client.get_source = AsyncMock(return_value=_failed("429 Too Many Requests"))
    out = await svc._scrape_one(client, "https://x.com/rl", {})
    assert client.import_url.await_count == 1 + svc.MAX_SCRAPE_RETRIES
    assert out["status"] == "failed"
    assert out["attempts"] == 1 + svc.MAX_SCRAPE_RETRIES
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/zipeng/worktrees/rankforge-scrape/rankforge/backend
uv run pytest tests/test_research.py -k "scrape_one" -q
```

Expected: FAIL — current `_scrape_one` calls `import_url` once and returns no `attempts` key (KeyError / AssertionError on `await_count == 2`).

- [ ] **Step 3: Replace `_scrape_one` and add `_scrape_attempt`**

Replace the whole current `_scrape_one` function (`services/research.py:474-518`) with:

```python
async def _scrape_attempt(
    client: PowabaseClient, url: str
) -> tuple[str | None, str | None, str, str | None]:
    """One import + poll cycle. Returns (source_id, status, markdown, error_message).
    source_id is None only if the import produced no source at all. Paced so scrape
    starts stay spread out."""
    await _scrape_pacer.wait()
    try:
        imp = await client.import_url(url)
        source_id = (imp.get("sources") or [{}])[0].get("id")
    except PowabaseError as e:
        body = e.body if isinstance(e.body, dict) else {}
        source_id = (body.get("duplicate") or {}).get("id")
    if not source_id:
        return None, None, "", None

    status = None
    error_message = None
    for _ in range(40):  # poll up to ~80s
        src = await client.get_source(source_id)
        status = src.get("extraction_status")
        error_message = src.get("error_message")
        if status in EXTRACTION_TERMINAL:
            break
        await asyncio.sleep(2)

    md = ""
    if status == "extracted":
        try:
            md = await client.get_source_markdown(source_id)
        except PowabaseError:
            md = ""
    return source_id, status, md, error_message


async def _scrape_one(
    client: PowabaseClient, url: str, title_by_url: dict[str, str]
) -> dict[str, Any] | None:
    """Import one competitor URL as a Source, wait for extraction, build a teardown.

    A TRANSIENT extraction failure (Firecrawl 429/5xx — see _is_transient_failure) is
    retried up to MAX_SCRAPE_RETRIES times with backoff, re-importing the URL to
    re-dispatch extraction. Permanent failures (dead/blocked page) are not retried."""
    source_id, status, md, error_message = await _scrape_attempt(client, url)
    if source_id is None:
        return None
    attempts = 1

    for i in range(MAX_SCRAPE_RETRIES):
        if status != "failed" or not _is_transient_failure(error_message):
            break
        delay = RETRY_BACKOFF[i]
        log.info(
            "transient scrape failure — retrying %s in %.0fs (attempt %d/%d)",
            url, delay, i + 1, MAX_SCRAPE_RETRIES,
        )
        await asyncio.sleep(delay)
        # Re-import re-dispatches extraction on the same (URL-deduped) source.
        new_sid, status, md, error_message = await _scrape_attempt(client, url)
        source_id = new_sid or source_id
        attempts += 1

    teardown = CompetitorTeardown(
        url=url,
        title=_first_title(md) or title_by_url.get(url) or url,
        word_count=len(md.split()) if md else None,
        headings=_extract_headings(md),
        source_id=source_id,
    )
    return {
        "teardown": teardown,
        "status": status,
        "source_id": source_id,
        "url": url,
        "attempts": attempts,
        # A short excerpt of the real content so the source-quality judge can assess more
        # than the domain name (a thin SEO blog and an authoritative guide look identical
        # from URL + title alone).
        "excerpt": md[:600] if md else "",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/zipeng/worktrees/rankforge-scrape/rankforge/backend
uv run pytest tests/test_research.py -k "scrape_one" -q && uv run ruff check src tests
```

Expected: 3 passed, then `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-scrape
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/research.py \
  rankforge/backend/tests/test_research.py
/usr/bin/git commit -m "feat(research): retry transient scrape failures with backoff

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Run scrape summary

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/research.py` (add `_scrape_summary`; log it in `run_research_task` after the scrape gather)
- Test: `rankforge/backend/tests/test_research.py`

**Interfaces:**
- Consumes: the `_scrape_one` result dicts (with `status` and `attempts`) from Task 3.
- Produces: `_scrape_summary(results: list[dict[str, Any] | None]) -> tuple[int, int, int]` returning `(ok, recovered_on_retry, failed)`.

- [ ] **Step 1: Write the failing test**

Append to `rankforge/backend/tests/test_research.py`:

```python
def test_scrape_summary_counts_ok_recovered_failed():
    results = [
        {"status": "extracted", "attempts": 1},
        {"status": "extracted", "attempts": 3},   # recovered on retry
        {"status": "failed", "attempts": 3},
        None,                                       # import produced no source
    ]
    assert svc._scrape_summary(results) == (2, 1, 2)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/zipeng/worktrees/rankforge-scrape/rankforge/backend
uv run pytest tests/test_research.py::test_scrape_summary_counts_ok_recovered_failed -q
```

Expected: FAIL — `AttributeError: ... '_scrape_summary'`

- [ ] **Step 3: Add the helper and log it**

In `services/research.py`, add near `_scrape_one` (e.g. right after it):

```python
def _scrape_summary(
    results: list[dict[str, Any] | None],
) -> tuple[int, int, int]:
    """(ok, recovered_on_retry, failed) counts for a run's scrape results."""
    ok = recovered = 0
    for r in results:
        if r and r.get("status") == "extracted":
            ok += 1
            if (r.get("attempts") or 1) > 1:
                recovered += 1
    return ok, recovered, len(results) - ok
```

Then in `run_research_task`, immediately AFTER the `results = await asyncio.gather(...)` line and its following `by_url` insert loop (i.e. right before the `# 3) evaluate source quality` comment), insert:

```python
        ok, recovered, failed = _scrape_summary(results)
        log.info(
            "research %s scrape summary: %d ok (%d recovered on retry), %d failed",
            run_id, ok, recovered, failed,
        )
```

(Indentation is 8 spaces — this is inside the `try` block of `run_research_task`.)

- [ ] **Step 4: Run the full research suite**

```bash
cd /home/zipeng/worktrees/rankforge-scrape/rankforge/backend
uv run pytest tests/test_research.py -q && uv run ruff check src tests
```

Expected: all pass, `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-scrape
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/research.py \
  rankforge/backend/tests/test_research.py
/usr/bin/git commit -m "feat(research): log a per-run scrape summary (ok/recovered/failed)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] Full backend suite green: `cd rankforge/backend && uv run pytest -q`
- [ ] Lint clean: `uv run ruff check src tests`
- [ ] Sanity: `grep -n "SCRAPE_CONCURRENCY = " src/rankforge_backend/services/research.py` shows `3`
- [ ] Sanity: `grep -c "_scrape_pacer.wait()" src/rankforge_backend/services/research.py` shows the pacer is awaited in `_scrape_attempt`

No `next build` — there is no frontend change in this plan.
