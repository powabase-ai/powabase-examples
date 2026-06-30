"""Stage A — research (async, Sources-backed).

1. A SERP agent (Exa web_search) returns the search landscape (no scraping).
2. The backend imports each top competitor URL as a Powabase Source (Firecrawl),
   so the raw scraped markdown is stored + reviewable + KB-indexable (approach 2).
3. Competitor teardowns are built deterministically from each source's markdown.

Runs in the background; the research_run row carries status/progress for polling.
"""

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

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

# depth → (serp results to analyze, competitor pages to scrape). Generous counts so,
# after the trust/quality gate drops thin or failed pages, the article still has many
# DISTINCT sources to cite (otherwise the writer re-cites the same one or two).
DEPTH_PRESETS = {"quick": (10, 6), "standard": (20, 12), "deep": (40, 22)}

# How many competitor pages to import/poll/extract concurrently. Each page can poll
# up to ~80s, so sequential scraping was the research bottleneck (≈ sum); bounded
# concurrency makes it ≈ the slowest single page.
SCRAPE_CONCURRENCY = 8

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
    "id, research_run_id, source_id, url, title, word_count, status, created_at"
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
    """True if a research_sources row with this Powabase source_id belongs to a
    research run whose business is in the caller's org. Gates the markdown proxy
    so a caller can't read arbitrary Powabase sources from other orgs."""
    return (
        db.fetch_one(
            "select 1 from public.research_sources rs "
            "join public.research_runs rr on rr.id = rs.research_run_id "
            "join public.business_profiles bp on bp.id = rr.business_id "
            "where rs.source_id = %s and bp.org_id = %s limit 1",
            (source_id, org_id),
        )
        is not None
    )


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
        "rs.created_at, rs.research_run_id, rr.topic as run_topic "
        "from public.research_sources rs "
        "join public.research_runs rr on rr.id = rs.research_run_id "
        "where rr.business_id = %s order by rs.created_at desc",
        (business_id,),
    )


async def _scrape_one(
    client: PowabaseClient, url: str, title_by_url: dict[str, str]
) -> dict[str, Any] | None:
    """Import one competitor URL as a Source, wait for extraction, build a teardown."""
    try:
        imp = await client.import_url(url)
        source_id = (imp.get("sources") or [{}])[0].get("id")
    except PowabaseError as e:
        body = e.body if isinstance(e.body, dict) else {}
        source_id = (body.get("duplicate") or {}).get("id")
    if not source_id:
        return None

    status = None
    for _ in range(40):  # poll up to ~80s
        src = await client.get_source(source_id)
        status = src.get("extraction_status")
        if status in EXTRACTION_TERMINAL:
            break
        await asyncio.sleep(2)

    md = ""
    if status == "extracted":
        try:
            md = await client.get_source_markdown(source_id)
        except PowabaseError:
            md = ""

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
        done = 0

        async def _scrape_bounded(u: str) -> dict[str, Any] | None:
            nonlocal done
            async with sem:
                result = await _scrape_one(client, u, title_by_url)
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

        competitors: list[dict[str, Any]] = []
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
            competitors.append(t.model_dump())

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
