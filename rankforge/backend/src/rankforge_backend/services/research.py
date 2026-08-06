"""Stage A — research (async, Sources-backed).

1. A SERP agent (Exa web_search) returns the search landscape (no scraping).
2. The backend imports each top competitor URL as a Powabase Source (Firecrawl),
   so the raw scraped markdown is stored + reviewable + KB-indexable (approach 2).
3. Competitor teardowns are built deterministically from each source's markdown.

Runs in the background; the research_run row carries status/progress for polling.
"""

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from psycopg.types.json import Json

from ..db import Database
from ..models.research import CompetitorTeardown, SearchResult
from ..powabase import EXTRACTION_TERMINAL, PowabaseClient, PowabaseError
from ..util import extract_json
from . import business_profiles as brands
from . import source_refs
from .agents import ensure_agent

log = logging.getLogger("rankforge.research")

SERP_AGENT_NAME = "rankforge-serp"
SERP_MODEL = "claude-sonnet-4-6"

SOURCE_JUDGE_AGENT_NAME = "rankforge-source-judge"
SOURCE_JUDGE_MODEL = "claude-sonnet-4-6"
# A source scoring below this (0-100 authority/trust) is pruned and, where the SERP has
# spare pages, replaced with a higher-authority one.
MIN_TRUST = 50
# Cap backfill scrapes so a run that prunes many weak sources can't balloon into an
# unbounded scrape (each page still polls up to ~80s).
MAX_BACKFILL = 12

# depth → (serp results to analyze, competitor pages to scrape). Generous counts so,
# after the trust/quality gate drops thin or failed pages, the article still has many
# DISTINCT sources to cite (otherwise the writer re-cites the same one or two).
DEPTH_PRESETS = {"quick": (10, 6), "standard": (20, 12), "deep": (40, 22)}

# How many competitor pages to import/poll/extract concurrently. Concurrency exists at
# all because each page polls up to ~80s, so sequential scraping ≈ the sum; bounded
# concurrency ≈ the slowest single page. Kept moderate (not the original 8) so the
# simultaneous Firecrawl dispatch doesn't trip its rate limit; the pacer below further
# spreads scrape starts.
SCRAPE_CONCURRENCY = 5

# --- scrape resilience (Firecrawl rate limits) -------------------------------------
# Powabase runs each scrape through Firecrawl in a background job, so a burst of
# concurrent imports rate-limits it and the source ends `failed` with a 429/5xx
# error_message — a failure RankForge never sees as an HTTP status. We dispatch gently
# (see _ScrapePacer) and retry the TRANSIENT failures. A retry re-imports the URL. Dedup
# is by URL only against a source that already EXTRACTED content; a FAILED source has no
# content to dedup against, so the re-import is a real re-scrape that runs a new
# extraction (a fresh source). The stale failed source is then deleted (ref-count-guarded)
# to avoid orphaning it. If a re-import ever 409-dedups back to the same source instead,
# `_scrape_attempt` reuses that id and the retry is a safe (budget-capped) no-op. Correct
# under both behaviors.
MIN_SCRAPE_INTERVAL = 0.75   # min seconds between scrape dispatches (_ScrapePacer)
MAX_SCRAPE_RETRIES = 2       # extra attempts after the first, for transient failures
# Backoff before retry 1, retry 2, … The index is clamped to the last element, so this
# tuple may be shorter than MAX_SCRAPE_RETRIES without an IndexError.
RETRY_BACKOFF = (5.0, 15.0)
# Cap total retries across a whole run so a run that is being broadly rate-limited can't
# balloon unbounded (each retry adds a poll budget + backoff). Shared by the main scrape
# loop and the sequential backfill.
MAX_RUN_RETRIES = 25

# Substrings marking a TRANSIENT extraction failure worth retrying (rate limit / upstream
# blip / our own poll timeout). Anything else (404, blocked, parse error, no message) is
# permanent and never retried, so a genuinely dead page is not re-scraped.
_TRANSIENT_MARKERS = (
    "429", "too many requests", "rate limit", "502", "bad gateway",
    "503", "service unavailable", "504", "gateway timeout",
    "timeout", "timed out",
    # Our own synthetic marker, emitted by _scrape_attempt ONLY when a poll-time error is
    # actually retryable (a retryable upstream status or a raw transport blip). A
    # permanent poll error (402/401/404) uses a different message and stays non-transient,
    # so it is never re-scraped (CLAUDE.md: 402 → no retry).
    "poll error (transient)",
)

# Upstream statuses on a poll worth retrying. Everything else (402 quota, 401/403 auth,
# 404 gone) is permanent — a paid re-scrape wouldn't help and 402 must never retry.
_RETRYABLE_POLL_STATUSES = frozenset({429, 502, 503, 504})


def _is_transient_failure(error_message: str | None) -> bool:
    """True if a failed extraction is worth retrying; False for a permanent failure."""
    if not error_message:
        return False
    low = error_message.lower()
    return any(m in low for m in _TRANSIENT_MARKERS)


class _RetryBudget:
    """A run-scoped cap on total scrape retries, so a broadly rate-limited run can't
    balloon into an unbounded chain of poll-budget + backoff waits. `take()` returns
    False once the budget is spent. Not thread-shared — one run, one event loop."""

    def __init__(self, total: int) -> None:
        self._remaining = total

    def take(self) -> bool:
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        return True


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

# A source is only trustworthy enough to cite if it actually extracted real content.
MIN_SOURCE_WORDS = 200

# Domains that aren't citable authority for an editorial article — social, video,
# Q&A/forums, and aggregators. We skip these when picking competitor pages to scrape
# so grounding/citations come from real articles, docs, and reporting.
_JUNK_DOMAINS = {
    "youtube.com", "m.youtube.com", "youtu.be", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "tiktok.com", "pinterest.com", "reddit.com",
    "quora.com", "linkedin.com", "medium.com", "amazon.com", "ebay.com",
    "play.google.com", "apps.apple.com",
}


def _is_junk_domain(url: str) -> bool:
    d = _domain(url)
    return any(d == j or d.endswith("." + j) for j in _JUNK_DOMAINS)


def is_usable_source(s: dict[str, Any]) -> bool:
    """A scraped source good enough to ground on: it extracted, and it has enough
    real content to cite (drops failed scrapes and thin/boilerplate pages)."""
    return (
        s.get("status") == "extracted"
        and (s.get("word_count") or 0) >= MIN_SOURCE_WORDS
    )

_SYSTEM_PROMPT = """\
You are RankForge's **SERP analyst**. Given a topic, you map its search landscape \
with the `web_search` (Exa) tool and return a structured analysis. Your output is \
the raw material for the whole pipeline: the backend scrapes the competitor URLs \
you surface, and the strategist builds the content brief from your clusters, PAA, \
and intent. Excellent output faithfully mirrors what a searcher actually sees and \
gives the strategist a clear, well-grouped picture of the topic — not a guess from \
memory.

## Your task
- Run one or more `web_search` queries on the topic, then derive every field from \
the returned results — not from prior knowledge. Vary the query wording if a single \
search returns a thin or one-sided set.
- Capture, all grounded in the results:
  - **serp** — the organic ranking results in order, with rank, title, url, snippet.
  - **paa** — the People-Also-Ask / common follow-up questions searchers ask.
  - **related_queries** — adjacent searches and query reformulations.
  - **keyword_clusters** — the result terms grouped into labelled sub-topics, each \
with its own dominant intent.
  - **intent** — the dominant search intent across the whole SERP.

## How to read intent
- `informational` — the searcher wants to learn/understand (guides, explainers, "how/what/why").
- `commercial` — comparing options before a purchase (reviews, "best", "vs", alternatives).
- `transactional` — ready to act/buy/sign up (pricing, "buy", "download", tools).
- `navigational` — looking for a specific site, brand, or page.

## Rules
- Search only — never open, scrape, or read the full body of a page. Work from \
titles, URLs, and snippets.
- Prefer a diverse result set: span different domains and source types (official \
docs, independent analyses, reputable news, practitioner blogs) over many pages \
from one site — the backend scrapes one page per domain, so duplicates from a \
single site waste a slot.
- Keep `serp` results in their natural ranking order; do not reorder by opinion. \
Number `rank` from 1 in that order.
- Group clusters so each label owns a distinct sub-topic (no two clusters overlap); \
keep cluster keywords drawn from the actual results.
- Leave a field empty (`[]` or `null`) when the search does not support it; never \
fabricate a result, question, or URL.

## Output
- Your final message must be exactly one JSON object in a single ```json fenced \
block, with nothing after it.
"""

_SCHEMA_HINT = """{
  "intent": "informational|commercial|transactional|navigational",
  "serp": [{"rank": 1, "title": "...", "url": "...", "snippet": "..."}],
  "paa": ["question?"],
  "related_queries": ["..."],
  "keyword_clusters": [{"label": "...", "keywords": ["..."], "intent": "informational"}]
}"""

_RESEARCH_COLUMNS = (
    "id, business_id, topic, locale, status, error, progress, serp, competitors, "
    "clusters, intent, agent_run_id, created_by, created_at"
)
_SOURCE_COLUMNS = (
    "id, research_run_id, source_id, url, title, word_count, status, "
    "trust_score, trust_reason, created_at"
)

async def ensure_serp_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=SERP_AGENT_NAME,
        model=SERP_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        settings={"temperature": 0},
        builtin_tools=("web_search",),
    )


_JUDGE_SYSTEM = """\
You are RankForge's **source-quality auditor**. Given the web sources a research pass \
collected to ground an editorial article, you rate each one for how AUTHORITATIVE and \
TRUSTWORTHY it is to CITE — a proxy for domain authority plus editorial trust.

Score each source 0-100:
- **85-100 — primary / high authority:** official documentation, standards bodies, \
academic or peer-reviewed sources, a vendor's own site for its OWN product, major \
reputable publications and established industry outlets.
- **60-84 — solid secondary:** well-known practitioner or engineering blogs and \
community sites with a real track record and named expertise.
- **40-59 — mixed / thin:** generic blogs, listicles, and SEO content with little \
demonstrated authority; usable only as color.
- **0-39 — low quality:** content farms, thin affiliate/SEO blogs, keyword-stuffed \
pages, anonymous low-DA sites, link-bait.

## How to judge
- Weigh the DOMAIN/URL (domain authority is a property of the domain — a recognizable, \
reputable domain scores higher; an unknown domain that reads like an SEO play, e.g. \
generic ".blog"/".dev" personal sites or keyword-stuffed titles, scores lower) TOGETHER \
with the content **excerpt** when one is provided.
- Use the excerpt to tell a thin SEO/affiliate page (keyword-stuffed, vague, listicle \
filler, thin rewording) from genuinely substantive writing (specific facts, data, named \
expertise, primary detail) on an unfamiliar domain — the excerpt is how you avoid \
judging by domain name alone.
- A high word count does NOT make a thin SEO blog authoritative — do not reward length.
- When the domain is unknown AND the excerpt is thin/generic, lean skeptical (mid-to-low). \
A strong excerpt can lift an unknown domain into the solid-secondary band.

## Output
- Return exactly ONE JSON array, one object per source in the SAME order you were \
given, and nothing else (no prose, no code fences):
[{"index": 0, "score": 0-100, "reason": "<short phrase>"}]
"""


async def ensure_source_judge_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=SOURCE_JUDGE_AGENT_NAME,
        model=SOURCE_JUDGE_MODEL,
        system_prompt=_JUDGE_SYSTEM,
        settings={"temperature": 0},
    )


def _extract_json_array(content: str) -> list[Any]:
    """Pull a JSON array from an LLM message (```fenced or bare) — the object-only
    `extract_json` can't, and the judge returns a top-level array. Returns [] if none
    parses."""
    fenced = re.search(r"```(?:json)?\s*(\[.*\])\s*```", content or "", re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start, end = (content or "").find("["), (content or "").rfind("]")
        raw = content[start : end + 1] if start != -1 and end > start else ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


async def score_sources(
    client: PowabaseClient, sources: list[dict[str, Any]]
) -> dict[str, tuple[int, str]]:
    """Rate each source 0-100 for authority/trust. Returns url → (score, reason). On any
    failure (agent error, unparseable output) returns {} so the caller keeps every
    source rather than pruning blind — evaluation is a quality boost, never a data-loss
    risk."""
    scorable = [s for s in sources if s.get("url")]
    if not scorable:
        return {}
    lines = []
    for i, s in enumerate(scorable):
        line = (
            f"{i}. domain: {_domain(s['url'])} | url: {s['url']} | "
            f"title: {s.get('title') or '(none)'} | words: {s.get('word_count') or 0}"
        )
        excerpt = " ".join((s.get("excerpt") or "").split())[:400]
        if excerpt:
            line += f"\n   excerpt: {excerpt}"
        lines.append(line)
    msg = (
        "## Sources to rate\n" + "\n".join(lines) + "\n\n"
        "## Task\nRate every source 0-100 for authority/trust as an editorial "
        "citation. Return ONLY the JSON array, one object per source, in order."
    )
    try:
        agent_id = await ensure_source_judge_agent(client)
        run = await client.run_agent_collect(agent_id, msg)
        if run.get("error"):
            log.warning("source judge failed: %s", run["error"])
            return {}
        data = _extract_json_array(run.get("content") or "")
    except Exception:  # noqa: BLE001 — never let scoring break the research run
        log.exception("source scoring failed")
        return {}
    out: dict[str, tuple[int, str]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(scorable)):
            continue
        try:
            score = max(0, min(100, int(item.get("score"))))
        except (TypeError, ValueError):
            continue
        reason = str(item.get("reason") or "")[:300]
        out[scorable[idx]["url"]] = (score, reason)
    return out


# --- markdown teardown helpers ---
def _extract_headings(md: str, limit: int = 40) -> list[str]:
    out = []
    for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", md, re.MULTILINE):
        out.append(f"H{len(m.group(1))}: {m.group(2).strip()}")
        if len(out) >= limit:
            break
    return out


def _first_title(md: str) -> str | None:
    m = re.search(r"^#\s+(.+?)\s*$", md, re.MULTILINE)
    return m.group(1).strip() if m else None


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "").lower() or url.lower()
    except ValueError:
        return url.lower()


def diverse_urls(urls: list[str], n: int, per_domain: int = 1) -> list[str]:
    """Pick up to n URLs preferring distinct domains (so grounding/citations don't
    all trace back to one parent source); backfill from remaining if too few.
    Skips non-citable junk domains (social/video/forums/aggregators)."""
    urls = [u for u in urls if not _is_junk_domain(u)]
    out: list[str] = []
    counts: dict[str, int] = {}
    for u in urls:
        d = _domain(u)
        if counts.get(d, 0) >= per_domain:
            continue
        counts[d] = counts.get(d, 0) + 1
        out.append(u)
        if len(out) >= n:
            return out
    for u in urls:  # backfill to reach n if there weren't enough distinct domains
        if u not in out:
            out.append(u)
            if len(out) >= n:
                break
    return out


# --- row helpers ---
def create_research_run(
    db: Database,
    *,
    business_id: UUID,
    topic: str,
    locale: str,
    created_by: UUID | None = None,
) -> dict[str, Any]:
    return db.fetch_one(
        "insert into public.research_runs "
        "(business_id, topic, locale, status, created_by) "
        f"values (%s, %s, %s, 'searching', %s) returning {_RESEARCH_COLUMNS}",
        (business_id, topic, locale, created_by),
    )


def _update(db: Database, run_id: UUID, **fields: Any) -> None:
    jsonb = {"progress", "serp", "competitors", "clusters"}
    sets, params = [], []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        params.append(Json(v) if k in jsonb else v)
    params.append(run_id)
    db.execute(
        f"update public.research_runs set {', '.join(sets)} where id = %s",
        tuple(params),
    )


def get_run(db: Database, run_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_RESEARCH_COLUMNS} from public.research_runs where id = %s",
        (run_id,),
    )


def list_runs(db: Database, business_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_RESEARCH_COLUMNS} from public.research_runs "
        "where business_id = %s order by created_at desc",
        (business_id,),
    )


async def delete_run(client: PowabaseClient, db: Database, run_id: UUID) -> bool:
    """Delete a research run; its source rows cascade. Each scraped page's Powabase
    Source is deleted too — but only when no OTHER workspace (another run, a brand
    material, or a cluster doc) still references it (imports dedupe by URL project-wide,
    so a Source can be shared, and deleting a shared one would break the other
    consumer). Remote deletes are best-effort. Returns whether a run was deleted."""
    # Gather the run's Source ids BEFORE deleting it (the rows cascade away with the
    # run). Dedupe — two URLs in one run can dedupe to the same Powabase Source.
    sids = {
        s["source_id"]
        for s in db.fetch_all(
            "select source_id from public.research_sources where research_run_id = %s",
            (run_id,),
        )
        if s.get("source_id")
    }
    deleted = db.fetch_one(
        "delete from public.research_runs where id = %s returning id", (run_id,)
    )
    if deleted is None:
        return False
    # Run (and its source rows) are gone — now delete each Source nothing else uses.
    # Counting AFTER the rows are gone is orphan-safe under concurrent deletes.
    for sid in sids:
        if source_refs.source_reference_count(db, sid) == 0:
            try:
                await client.delete_source(sid)
            except Exception:  # noqa: BLE001 — remote cleanup is best-effort
                log.exception("delete_source failed for research source %s", sid)
    return True


def source_in_org(db: Database, source_id: str, org_id: UUID) -> bool:
    """True if this Powabase source_id is referenced by the caller's org — via a
    scraped research source OR a brand material. Gates the source proxies (markdown /
    meta / page image) so a caller can't read arbitrary project-wide Powabase sources
    from other orgs."""
    return (
        db.fetch_one(
            "select 1 from public.research_sources rs "
            "join public.research_runs rr on rr.id = rs.research_run_id "
            "join public.business_profiles bp on bp.id = rr.business_id "
            "where rs.source_id = %s and bp.org_id = %s "
            "union all "
            "select 1 from public.brand_sources bs "
            "join public.business_profiles bp2 on bp2.id = bs.business_id "
            "where bs.source_id = %s and bp2.org_id = %s "
            "limit 1",
            (source_id, org_id, source_id, org_id),
        )
        is not None
    )


async def bulk_delete_brand_sources(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    row_ids: list[UUID],
) -> int:
    """Delete selected scraped-source rows from a brand's library. The WHERE join on
    `business_id` is the authorization boundary — ids belonging to another brand are
    silently ignored. Each removed row's Powabase Source is then deleted iff nothing
    else references it (orphan-safe ref-count check; remote delete best-effort).
    Returns the number of rows deleted."""
    if not row_ids:
        return 0
    rows = db.fetch_all(
        "select rs.id, rs.source_id from public.research_sources rs "
        "join public.research_runs rr on rr.id = rs.research_run_id "
        "where rr.business_id = %s and rs.id = any(%s)",
        (business_id, list(row_ids)),
    )
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    sids = {r["source_id"] for r in rows if r.get("source_id")}
    # `returning id` so the count reflects rows ACTUALLY deleted (not the pre-delete
    # SELECT, which overcounts if a concurrent delete raced us).
    deleted = db.fetch_all(
        "delete from public.research_sources where id = any(%s) returning id", (ids,)
    )
    # The rows are committed now. The remote-Source cleanup below is best-effort and must
    # NEVER turn a successful delete into an error — a failing ref-count query or remote
    # delete would otherwise 500 the route AFTER the rows are already gone, leaving the UI
    # showing rows that no longer exist. Guard the whole per-source step, not just the
    # delete call.
    for sid in sids:
        try:
            if source_refs.source_reference_count(db, sid) == 0:
                await client.delete_source(sid)
        except Exception:  # noqa: BLE001 — remote cleanup is best-effort
            log.exception("bulk delete: cleanup failed for source %s", sid)
    return len(deleted)


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


async def _best_effort_delete_source(
    client: PowabaseClient, db: Database, source_id: str
) -> None:
    """Delete a Powabase Source iff nothing else references it (the service-wide ref-count
    contract). Best-effort — never raises, since a failed cleanup must not turn a
    completed retry into an error. The ref-count read is a sync DB call, so it is
    offloaded off the event loop."""
    try:
        refs = await asyncio.to_thread(
            source_refs.source_reference_count, db, source_id
        )
        if refs == 0:
            await client.delete_source(source_id)
        else:
            log.info("retry: source %s still referenced (refs=%s) — kept", source_id, refs)
    except Exception:  # noqa: BLE001 — remote cleanup is best-effort
        log.exception("retry: could not delete source %s", source_id)


async def _best_effort_reset_failed(db: Database, row_id: Any) -> None:
    """Move a claimed row from 'retrying' back to 'failed' so it stays retryable. Guarded
    on the current status so it never clobbers a row that already reached a terminal state.
    Best-effort; the startup reconciler is the guaranteed backstop for a row this couldn't
    reach."""
    try:
        await db.aexecute(
            "update public.research_sources set status = 'failed' "
            "where id = %s and status = 'retrying'",
            (row_id,),
        )
    except Exception:  # noqa: BLE001 — best-effort; reconciler backstops
        log.exception("source retry: could not reset row %s", row_id)


def reset_sources_to_failed(db: Database, row_ids: list[UUID]) -> None:
    """Revert a claim: move rows from 'retrying' back to 'failed'. Used when the worker
    could not be scheduled after the claim committed, so the rows aren't stranded in
    'retrying' with nothing behind them."""
    if not row_ids:
        return
    db.execute(
        "update public.research_sources set status = 'failed' "
        "where id = any(%s) and status = 'retrying'",
        (list(row_ids),),
    )


async def retry_brand_sources(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    rows: list[dict[str, Any]],
) -> None:
    """Re-scrape each claimed source row (already marked 'retrying' by
    mark_sources_retrying) and update it in place. Bounded concurrency + a shared retry
    budget, mirroring the main run scrape.

    Per row: re-run _scrape_one on its URL. If it yields no Source (no URL, or the import
    produced nothing), restore the row to 'failed' with its original linkage. Otherwise
    ADOPT the fresh Source (new source_id/word_count/status) and delete the OLD Source when
    nothing else references it. If the adopt-UPDATE matches no row (the run was deleted
    mid-retry) or fails, the freshly created Source is cleaned up so a retry never leaks an
    orphan. One row's failure never sinks the batch; a CancelledError (shutdown drain)
    resets the row and re-raises so it is not stranded in 'retrying' (the startup
    reconciler is the guaranteed backstop).

    `business_id` is not used in the query bodies (rows were already brand-scoped by the
    claim) but kept in the signature so the worker's provenance is explicit at the call
    site."""
    sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
    budget = _RetryBudget(MAX_RUN_RETRIES)

    async def _one(row: dict[str, Any]) -> None:
        row_id = row["id"]
        old_sid = row.get("source_id")
        url = row.get("url")
        title_by_url = {url: row.get("title") or ""} if url else {}
        new_sid: str | None = None
        adopted = False
        try:
            async with sem:
                res = (
                    await _scrape_one(client, db, url, title_by_url, budget)
                    if url else None
                )
            if res is None:
                # Nothing re-scraped — move 'retrying' → 'failed' (keeps old linkage).
                await _best_effort_reset_failed(db, row_id)
                return
            new_sid = res["source_id"]
            t = res["teardown"]
            # `returning id`: if the row vanished mid-retry (its run was deleted), the
            # UPDATE matches nothing — the fresh Source we just created would then be an
            # orphan, so we detect that and clean it up rather than leak it.
            updated = await db.afetch_one(
                "update public.research_sources "
                "set source_id = %s, word_count = %s, status = %s where id = %s "
                "returning id",
                (new_sid, t.word_count, res["status"], row_id),
            )
            adopted = updated is not None
            if not adopted:
                if new_sid:
                    await _best_effort_delete_source(client, db, new_sid)
                return
            # Adopt succeeded — retire the stale Source if nothing else references it.
            if old_sid and new_sid != old_sid:
                await _best_effort_delete_source(client, db, old_sid)
        except asyncio.CancelledError:
            # Shutdown drain / explicit cancel interrupted this row mid-flight. Reset it
            # (best-effort; the reconciler guarantees it) and drop any un-adopted fresh
            # Source, then re-raise so cancellation still propagates.
            await _best_effort_reset_failed(db, row_id)
            if new_sid and not adopted:
                await _best_effort_delete_source(client, db, new_sid)
            raise
        except Exception:  # noqa: BLE001 — one row must never sink the batch
            log.exception("source retry failed for row %s", row_id)
            if not adopted:
                await _best_effort_reset_failed(db, row_id)
                if new_sid:
                    # adopt-UPDATE failed after a fresh Source was created — don't leak it.
                    await _best_effort_delete_source(client, db, new_sid)

    await asyncio.gather(*[_one(r) for r in rows])


def list_sources(db: Database, run_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_SOURCE_COLUMNS} from public.research_sources "
        "where research_run_id = %s order by created_at",
        (run_id,),
    )


def list_brand_sources(db: Database, business_id: UUID) -> list[dict[str, Any]]:
    """All scraped sources for a brand, joined to their research run (for the library)."""
    return db.fetch_all(
        "select rs.id, rs.source_id, rs.url, rs.title, rs.word_count, rs.status, "
        "rs.trust_score, rs.trust_reason, rs.created_at, rs.research_run_id, "
        "rr.topic as run_topic "
        "from public.research_sources rs "
        "join public.research_runs rr on rr.id = rs.research_run_id "
        "where rr.business_id = %s order by rs.created_at desc",
        (business_id,),
    )


async def _scrape_attempt(
    client: PowabaseClient, url: str
) -> tuple[str | None, str | None, str, str | None]:
    """One import + poll cycle. Returns (source_id, status, markdown, error_message).

    source_id is None only when the import produced no source (logged). A poll-time
    upstream error or a poll timeout is degraded to a synthetic transient `failed` so the
    caller's retry machinery can pick it up — never raised, so one bad poll can't fail the
    whole `asyncio.gather`. Paced so scrape starts stay spread out."""
    await _scrape_pacer.wait()
    try:
        imp = await client.import_url(url)
        source_id = (imp.get("sources") or [{}])[0].get("id")
    except (PowabaseError, httpx.HTTPError) as e:
        # httpx.HTTPError too: a raw transport error (ConnectError/ReadTimeout/…) isn't a
        # PowabaseError and would otherwise escape and kill the whole run via gather.
        body = e.body if isinstance(e, PowabaseError) and isinstance(e.body, dict) else {}
        source_id = (body.get("duplicate") or {}).get("id")
        if not source_id:
            # The import itself failed with no reusable source (quota/auth/rate limit
            # after the client's own retries, a transport blip, …). Log it — otherwise a
            # run that imports nothing reports "done" with zero sources and no trace.
            log.warning("scrape import failed for %s: %s", url, e)
    if not source_id:
        return None, None, "", None

    status = None
    error_message = None
    for _ in range(40):  # poll up to ~80s
        try:
            src = await client.get_source(source_id)
        except (PowabaseError, httpx.HTTPError) as e:
            # A poll-time upstream/transport error must NOT propagate into asyncio.gather
            # and fail the whole run. Degrade to a `failed` — but only RETRYABLE when the
            # cause is transient (a retryable upstream status, or any raw transport blip).
            # A permanent status (402 quota, 401/403 auth, 404 gone) stays non-transient
            # so it isn't re-scraped for paid nothing.
            log.warning("scrape poll error for %s (%s): %s", url, source_id, e)
            permanent = (
                isinstance(e, PowabaseError)
                and e.status_code not in _RETRYABLE_POLL_STATUSES
            )
            msg = (
                f"poll error {e.status_code} (permanent)" if permanent
                else f"poll error (transient): {e}"
            )
            return source_id, "failed", "", msg
        status = src.get("extraction_status")
        error_message = src.get("error_message")
        if status in EXTRACTION_TERMINAL:
            break
        await asyncio.sleep(2)
    else:
        # Never reached a terminal status within the poll budget. The extraction is still
        # running remotely and would likely finish — so do NOT mark it a transient
        # failure (which would delete + re-scrape a nearly-done page). Return the last
        # non-terminal status (not "failed"), so the caller does not retry it; just log.
        log.warning(
            "scrape poll budget exhausted for %s (%s), last status=%s",
            url, source_id, status,
        )
        # Coerce a missing status to a non-terminal placeholder — never None, which would
        # persist as a NULL research_sources.status row.
        return source_id, status or "extracting", "", "poll budget exhausted"

    if status == "failed":
        log.info("scrape failed for %s (%s): %s", url, source_id, error_message)
    md = ""
    if status == "extracted":
        try:
            md = await client.get_source_markdown(source_id)
        except (PowabaseError, httpx.HTTPError) as e:
            # Extraction succeeded but the markdown fetch didn't — the teardown ends
            # empty. Log it so the run summary's "ok" count isn't silently inflated.
            log.warning(
                "markdown fetch failed for extracted source %s (%s): %s",
                url, source_id, e,
            )
            md = ""
    return source_id, status, md, error_message


async def _scrape_one(
    client: PowabaseClient,
    db: Database,
    url: str,
    title_by_url: dict[str, str],
    retry_budget: "_RetryBudget | None" = None,
) -> dict[str, Any] | None:
    """Import one competitor URL as a Source, wait for extraction, build a teardown.

    A TRANSIENT failure (Firecrawl 429/5xx — see _is_transient_failure) is retried up to
    MAX_SCRAPE_RETRIES times with backoff. A retry re-imports the URL, which creates a
    FRESH source and re-runs extraction; the stale failed source is then deleted —
    ref-count-guarded, so a Source with a committed reference from another run is never
    removed — so retries don't orphan sources project-wide. Permanent failures
    (dead/blocked page) and pages still extracting at the poll deadline are not retried.
    `retry_budget`, when given, caps total retries across the run."""
    source_id, status, md, error_message = await _scrape_attempt(client, url)
    if source_id is None:
        return None
    attempts = 1

    for i in range(MAX_SCRAPE_RETRIES):
        if status != "failed" or not _is_transient_failure(error_message):
            break
        if retry_budget is not None and not retry_budget.take():
            log.info("scrape retry budget exhausted — not retrying %s", url)
            break
        delay = RETRY_BACKOFF[min(i, len(RETRY_BACKOFF) - 1)]
        log.info(
            "transient scrape failure for %s (%s) — retrying in %.0fs (attempt %d/%d)",
            url, error_message, delay, i + 1, MAX_SCRAPE_RETRIES,
        )
        await asyncio.sleep(delay)
        new_sid, new_status, new_md, new_err = await _scrape_attempt(client, url)
        attempts += 1
        if new_sid is None:
            # The retry's import produced no source — keep the prior failed diagnosis
            # (status + error_message) and its still-existing source rather than blanking
            # everything to None. Stop retrying; a failing import won't fix itself here.
            break
        if new_sid != source_id:
            # The re-import created a fresh source. Delete the stale one so the run
            # doesn't orphan it — but honor the same ref-count contract as every other
            # delete in this service (a re-import that 409-dedups could hand back a Source
            # another run still references). Best-effort. The ref-count read is a sync DB
            # call, so offload it (like the progress write) rather than block this loop.
            try:
                refs = await asyncio.to_thread(
                    source_refs.source_reference_count, db, source_id
                )
                if refs == 0:
                    await client.delete_source(source_id)
            except Exception:  # noqa: BLE001 — cleanup is best-effort
                log.exception("could not delete stale source %s before retry", source_id)
        source_id, status, md, error_message = new_sid, new_status, new_md, new_err

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


async def _drop_source(
    client: PowabaseClient, db: Database, run_id: UUID, source_id: str
) -> None:
    """Prune one scraped source from a run: delete its research_sources row, then delete
    the Powabase Source itself when nothing else references it (best-effort — imports
    dedupe by URL project-wide, so a shared Source is left for its other consumer)."""
    await db.aexecute(
        "delete from public.research_sources "
        "where research_run_id = %s and source_id = %s",
        (run_id, source_id),
    )
    if source_refs.source_reference_count(db, source_id) == 0:
        try:
            await client.delete_source(source_id)
        except Exception:  # noqa: BLE001 — remote cleanup is best-effort
            log.exception("prune: delete_source failed for %s", source_id)


def _usable_for_scoring(by_url: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The subset worth spending judge tokens on: sources that actually extracted enough
    content to cite (a failed/thin scrape is dropped by is_usable_source anyway)."""
    return [
        {"url": u, "title": v["teardown"].title, "word_count": v["teardown"].word_count,
         "excerpt": v.get("excerpt", "")}
        for u, v in by_url.items()
        if v["status"] == "extracted"
        and (v["teardown"].word_count or 0) >= MIN_SOURCE_WORDS
    ]


async def evaluate_and_prune(
    client: PowabaseClient,
    db: Database,
    run_id: UUID,
    *,
    by_url: dict[str, dict[str, Any]],
    backfill_pool: list[str],
    title_by_url: dict[str, str],
    retry_budget: "_RetryBudget | None" = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Score the run's sources for authority/trust and SWAP each weak source for a
    higher-authority replacement scraped from spare SERP pages (one bounded round).

    Non-regressive by construction: the destructive delete of a weak source is deferred
    until a stronger replacement is CONFIRMED in hand, so a failed or also-weak backfill
    can never leave the article with fewer usable sources than skipping evaluation (a
    45-score source still beats no source). `dropped == added` always. `by_url` maps
    url → {teardown, source_id, status, excerpt} and is mutated in place to the final set.
    Returns (final teardown dicts, stats). Scoring failure is non-fatal: with no scores
    nothing changes and every source is kept."""
    scorable = _usable_for_scoring(by_url)
    scores = await score_sources(client, scorable)
    if scorable and not scores:
        # We HAD sources to rate but got zero usable scores back — the judge errored or
        # returned malformed output (score_sources logged the cause). Keeping all sources
        # is the safe behavior, but the user opted into (and was billed extra credits for)
        # evaluation that effectively didn't run, so this must NOT look like "ran fine,
        # kept all". Surface it distinctly from the nothing-to-prune case below.
        log.warning(
            "research %s: source evaluation requested but the judge returned no usable "
            "scores for %d source(s) — all kept, UNEVALUATED",
            run_id, len(scorable),
        )
    for u, (score, reason) in scores.items():
        await db.aexecute(
            "update public.research_sources set trust_score = %s, trust_reason = %s "
            "where research_run_id = %s and url = %s",
            (score, reason, run_id, u),
        )
    used_domains = {_domain(u) for u in by_url}
    # Weakest-first sub-threshold sources are candidates for replacement (only scored
    # sources qualify — an unscored source is kept, never dropped blind).
    weak = sorted(
        (u for u in by_url if (sc := scores.get(u)) and sc[0] < MIN_TRUST),
        key=lambda u: scores[u][0],
    )
    dropped = added = 0
    if weak:
        # 1) Scrape + score replacements FIRST — nothing is deleted yet. New domains only,
        #    bounded so a harsh judge can't trigger an unbounded scrape.
        candidates = diverse_urls(
            [u for u in backfill_pool
             if u not in by_url and _domain(u) not in used_domains],
            min(len(weak), MAX_BACKFILL),
        )
        fresh: list[dict[str, Any]] = []
        for u in candidates:
            res = await _scrape_one(client, db, u, title_by_url, retry_budget)
            if res is None:
                continue
            t = res["teardown"]
            await db.aexecute(
                "insert into public.research_sources "
                "(research_run_id, source_id, url, title, word_count, status) "
                "values (%s, %s, %s, %s, %s, %s)",
                (run_id, res["source_id"], u, t.title, t.word_count, res["status"]),
            )
            fresh.append(
                {"url": u, "teardown": t, "source_id": res["source_id"],
                 "status": res["status"], "excerpt": res.get("excerpt", "")}
            )
        new_scores = await score_sources(
            client, _usable_for_scoring({b["url"]: b for b in fresh})
        )
        confirmed: list[dict[str, Any]] = []
        for b in fresh:
            sc = new_scores.get(b["url"])
            if sc:
                await db.aexecute(
                    "update public.research_sources set trust_score = %s, "
                    "trust_reason = %s where research_run_id = %s and url = %s",
                    (sc[0], sc[1], run_id, b["url"]),
                )
            if sc and sc[0] >= MIN_TRUST:
                confirmed.append(b)
            else:
                # The replacement is itself weak/unusable — drop it, don't hoard junk.
                await _drop_source(client, db, run_id, b["source_id"])
        # 2) SWAP: only NOW drop the worst weak originals, one per confirmed replacement,
        #    so the usable-source count never decreases.
        for u in weak[: len(confirmed)]:
            await _drop_source(client, db, run_id, by_url[u]["source_id"])
            del by_url[u]
            dropped += 1
        for b in confirmed:
            by_url[b["url"]] = {
                "teardown": b["teardown"], "source_id": b["source_id"],
                "status": b["status"], "excerpt": b.get("excerpt", ""),
            }
            added += 1

    teardowns = [v["teardown"].model_dump() for v in by_url.values()]
    return teardowns, {
        "scorable": len(scorable), "scored": len(scores),
        "dropped": dropped, "added": added,
    }


# --- the background worker ---
async def run_research_task(
    client: PowabaseClient,
    db: Database,
    *,
    run_id: UUID,
    brand: dict[str, Any],
    topic: str,
    locale: str,
    depth: str,
    evaluate_sources: bool = True,
) -> None:
    serp_n, scrape_n = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["deep"])
    try:
        # 1) SERP via agent (search only)
        agent_id = await ensure_serp_agent(client)
        msg = (
            "## Context\n"
            f"- Topic: {topic}\n"
            f"- Locale: {locale}\n"
            f"- Brand niche: {brand.get('niche') or 'n/a'}\n\n"
            "## Task\n"
            f"- Use `web_search` to analyze the top {serp_n} organic results.\n"
            "- Collect: ranked results (rank, title, url, snippet), People-Also-Ask, "
            "related queries, keyword clusters, and overall intent.\n"
            "- Search only — do not scrape.\n\n"
            "## Output\n"
            "- Output ONLY a single ```json block matching this shape:\n"
            f"{_SCHEMA_HINT}"
        )
        run = await client.run_agent_collect(agent_id, msg)
        if run["error"]:
            raise RuntimeError(f"SERP search failed: {run['error']}")
        search = SearchResult.model_validate(extract_json(run["content"]))

        serp = {
            "results": [r.model_dump() for r in search.serp],
            "paa": search.paa,
            "related_queries": search.related_queries,
        }
        title_by_url = {r.url: r.title for r in search.serp if r.url}
        # Prefer distinct domains so the article's grounding spans many parent sources.
        urls = diverse_urls([r.url for r in search.serp if r.url], scrape_n)
        _update(
            db,
            run_id,
            serp=serp,
            clusters=[c.model_dump() for c in search.keyword_clusters],
            intent=search.intent,
            agent_run_id=run["run_id"],
            status="scraping",
            progress={"phase": "scraping", "total": len(urls), "done": 0},
        )

        # 2) import competitor URLs as Sources concurrently (bounded), then teardown
        sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
        # One retry budget for the whole run (main scrape + backfill), so a broadly
        # rate-limited run can't balloon into an unbounded chain of retries.
        retry_budget = _RetryBudget(MAX_RUN_RETRIES)
        done = 0

        async def _scrape_bounded(u: str) -> dict[str, Any] | None:
            nonlocal done
            async with sem:
                result = await _scrape_one(client, db, u, title_by_url, retry_budget)
            done += 1  # advisory live progress (racy writes are fine)
            # Offload the DB write so it doesn't block the loop these run on.
            try:
                await asyncio.to_thread(
                    _update, db, run_id,
                    progress={"phase": "scraping", "total": len(urls), "done": done},
                )
            except Exception:  # noqa: BLE001
                pass
            return result

        results = await asyncio.gather(*[_scrape_bounded(u) for u in urls])

        by_url: dict[str, dict[str, Any]] = {}
        for r in results:
            if r is None:
                continue
            t = r["teardown"]
            await db.aexecute(
                "insert into public.research_sources "
                "(research_run_id, source_id, url, title, word_count, status) "
                "values (%s, %s, %s, %s, %s, %s)",
                (run_id, r["source_id"], r["url"], t.title, t.word_count, r["status"]),
            )
            by_url[r["url"]] = {
                "teardown": t, "source_id": r["source_id"], "status": r["status"],
                "excerpt": r.get("excerpt", ""),
            }

        ok, recovered, failed = _scrape_summary(results)
        log.info(
            "research %s scrape summary: %d ok (%d recovered on retry), %d not extracted",
            run_id, ok, recovered, failed,
        )

        # 3) evaluate source quality: score each source, prune weak ones, and backfill
        #    higher-authority replacements from spare SERP pages (opt-out, extra credits).
        if evaluate_sources and by_url:
            _update(
                db, run_id, status="evaluating",
                progress={"phase": "evaluating", "total": len(by_url), "done": 0},
            )
            competitors, stats = await evaluate_and_prune(
                client, db, run_id,
                by_url=by_url,
                backfill_pool=[
                    r.url for r in search.serp if r.url and r.url not in by_url
                ],
                title_by_url=title_by_url,
                retry_budget=retry_budget,
            )
            log.info("research %s source evaluation: %s", run_id, stats)
        else:
            competitors = [v["teardown"].model_dump() for v in by_url.values()]

        _update(
            db,
            run_id,
            status="done",
            competitors=competitors,
            progress={"phase": "done", "total": len(urls), "done": len(competitors)},
        )
    except Exception:  # noqa: BLE001 — surface a safe failure to the row
        log.exception("research run %s failed", run_id)
        _update(db, run_id, status="failed", error="research failed — see server logs")


def get_brand(db: Database, business_id: UUID) -> dict[str, Any] | None:
    return brands.get_profile(db, business_id)
