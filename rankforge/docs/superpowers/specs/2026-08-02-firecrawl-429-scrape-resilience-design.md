# Research scrape resilience (Firecrawl 429/5xx) — design

**Date:** 2026-08-02
**Branch:** `fix/firecrawl-429-backoff`
**Scope:** backend only. No client change, no migration, no API change, no frontend.

## Problem

During a research run, `run_research_task` imports competitor URLs as Powabase
Sources and polls each one's extraction. Powabase runs the scrape through Firecrawl in
a background job. When RankForge dispatches its scrapes as a burst — up to
`SCRAPE_CONCURRENCY = 8` at once — Firecrawl rate-limits, and the source ends
`extraction_status: failed` with an `error_message` like:

```
Client error '429 Too Many Requests' for url 'https://api.firecrawl.dev/v1/scrape'
```

RankForge never sees this as an HTTP status: `import_url` returns quickly (extraction is
async on Powabase's side), so the client's existing 429 backoff on `_request` doesn't
apply. `_scrape_one` polls, sees `failed`, and moves on with no retry. The URL is
dropped from the run's grounding for what is a transient, recoverable error. Sampling
prod, both `429 Too Many Requests` and `502 Bad Gateway` from Firecrawl show up this
way — a meaningful share of "failed" sources are recoverable, not dead pages.

## Goals

1. Stop provoking the 429: dispatch scrapes at a gentler rate.
2. Recover the transiently-failed scrapes instead of dropping them.
3. Leave genuinely-dead pages alone — no wasted Firecrawl calls, no slower runs for them.

## Non-goals (YAGNI)

- No env-configurable rate knob and no exact Firecrawl requests/minute number — the fix
  uses conservative constants.
- No schema change to persist `error_message` on `research_sources`. The prior PR
  already renders a failed source as "not scraped"; surfacing the reason in the UI is
  deferred.
- No Powabase-side change. The BaaS ideally backs off Firecrawl itself, but that is a
  different repo. This is the RankForge-side mitigation.

## Key finding

A failed source exposes the failure reason in `error_message` (confirmed against live
data). That lets RankForge classify a failure as transient (retry) vs permanent (leave
it) without guessing, so retries never re-hammer a dead page.

The user confirmed a second fact: **re-importing the same URL re-runs extraction** on the
existing source. Recovery therefore needs no new client method — a retry is another
`import_url(url)` call plus a re-poll.

## Design

All four mechanisms live inside `_scrape_one` (and a small module-level pacer), so both
the main scrape loop and the backfill scrapes in `evaluate_and_prune` get them with no
change to the gather/loop structure.

### 1. Smaller burst

`SCRAPE_CONCURRENCY: 8 → 3`. Caps how many scrapes are in flight (polling) at once,
which caps the initial simultaneous Firecrawl dispatch.

### 2. Spacing (delays between scrapes)

A run-global async pacer enforces a minimum gap between scrape *starts*:

```python
MIN_SCRAPE_INTERVAL = 0.75  # seconds between scrape dispatches

class _ScrapePacer:
    """Serializes scrape STARTS to at most one per MIN_SCRAPE_INTERVAL, so scrapes are
    spread over time even when several concurrency slots free at the same instant.
    Uses the event loop clock (monotonic; available since scrapes share the app loop)."""
    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._next = 0.0
    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            delay = max(0.0, self._next - now)
            if delay:
                await asyncio.sleep(delay)
            self._next = loop.time() + self._interval
```

A module-level instance `_scrape_pacer = _ScrapePacer(MIN_SCRAPE_INTERVAL)` is awaited
before every `import_url` (initial attempt and every retry). Module-level so it also
paces across concurrent research runs — the thing Firecrawl's limit actually cares about.

### 3. Transient classification

```python
_TRANSIENT_MARKERS = (
    "429", "too many requests", "502", "bad gateway",
    "503", "service unavailable", "504", "gateway timeout",
    "timeout", "timed out",
)

def _is_transient_failure(error_message: str | None) -> bool:
    """True if a failed extraction is worth retrying (rate limit / upstream blip),
    False for a permanent failure (404, blocked, parse error, or no message) so a dead
    page is never re-scraped."""
    if not error_message:
        return False
    low = error_message.lower()
    return any(m in low for m in _TRANSIENT_MARKERS)
```

### 4. Retry with backoff

`_scrape_one` is refactored so its import-and-poll body is one attempt that also returns
the polled `error_message`. A retry loop wraps it:

- On `status == "failed"` with `_is_transient_failure(error_message)` and retries left:
  log at INFO, sleep the backoff, and re-run the attempt (re-`import_url` + re-poll).
- Backoff is an increasing, capped schedule: `RETRY_BACKOFF = (5.0, 15.0)` seconds, i.e.
  `MAX_SCRAPE_RETRIES = 2` (≤3 attempts total). The sleep happens while the scrape holds
  its concurrency slot, so retry pressure is naturally throttled rather than re-bursting.
- Any non-failed terminal status, or a permanent failure, returns immediately.

Every `import_url` — first attempt and retries — goes through `_scrape_pacer.wait()`.

### Observability

- INFO on each retry: `transient scrape failure — retrying <url> in <delay>s (attempt n/N)`.
- One INFO summary line per run: `scrape summary: <ok> ok, <recovered> recovered on retry, <failed> failed permanently`, computed from the per-URL outcomes already collected in `run_research_task`.

## Risk

The retry trusts that re-import re-dispatches extraction. If a re-import ever returns the
already-failed source without re-scraping, the retry is a harmless no-op: the attempt
re-polls, still sees `failed`, and the loop stops at `MAX_SCRAPE_RETRIES`. No incorrect
state, just no recovery in that edge case.

Latency: a persistently-transient URL now costs up to 3 poll budgets plus ~20s of
backoff (~4 min worst case) instead of one, but only for URLs that keep getting
rate-limited, and it runs in the background. Concurrency 3 + pacing lengthens a clean
"deep" run modestly; that is the intended trade for not getting rate-limited.

## Testing

Hermetic, mocked at the `PowabaseClient` boundary, `asyncio.sleep` patched so tests are
fast.

- `_is_transient_failure`: truth table — 429 / 502 / 503 / 504 / timeout → True;
  404 / "blocked" / "" / None → False.
- `_scrape_one` retry-then-succeed: first poll returns `failed` + a 429 message, second
  attempt returns `extracted`; assert `import_url` called twice and the final teardown
  reflects the extracted content.
- `_scrape_one` permanent failure: `failed` + a 404 message; assert `import_url` called
  once (no retry).
- `_scrape_one` persistent transient: always `failed` + 429; assert exactly
  `1 + MAX_SCRAPE_RETRIES` attempts, then gives up.
- `_ScrapePacer`: two back-to-back `wait()` calls are spaced by at least the interval
  (inject a small interval; assert the second returns no earlier than the first + interval
  using the loop clock).

## Files touched

- `services/research.py` — `SCRAPE_CONCURRENCY` value; `MIN_SCRAPE_INTERVAL`,
  `MAX_SCRAPE_RETRIES`, `RETRY_BACKOFF`, `_TRANSIENT_MARKERS` constants; `_ScrapePacer`
  + `_scrape_pacer`; `_is_transient_failure`; `_scrape_one` refactor (attempt body +
  retry loop, capture `error_message`); run summary log in `run_research_task`.
- `tests/test_research.py` — the tests above.
