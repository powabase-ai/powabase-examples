"""Stage A — research (async, Sources-backed).

1. A SERP agent (Exa web_search) returns the search landscape (no scraping).
2. The backend imports each top competitor URL as a Powabase Source (Firecrawl),
   so the raw scraped markdown is stored + reviewable + KB-indexable (approach 2).
3. Competitor teardowns are built deterministically from each source's markdown.

Runs in the background; the research_run row carries status/progress for polling.
"""

import asyncio
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from psycopg.types.json import Json

from ..db import Database
from ..models.research import CompetitorTeardown, SearchResult
from ..powabase import PowabaseClient, PowabaseError
from ..util import extract_json
from . import business_profiles as brands
from .agents import ensure_agent

SERP_AGENT_NAME = "rankforge-serp"
SERP_MODEL = "claude-sonnet-4-6"
TERMINAL = {"extracted", "attention_required", "failed", "cancelled"}

# depth → (serp results to analyze, competitor pages to scrape)
DEPTH_PRESETS = {"quick": (5, 3), "standard": (10, 5), "deep": (20, 10)}

# How many competitor pages to import/poll/extract concurrently. Each page can poll
# up to ~80s, so sequential scraping was the research bottleneck (≈ sum); bounded
# concurrency makes it ≈ the slowest single page.
SCRAPE_CONCURRENCY = 5

_SYSTEM_PROMPT = """\
You are RankForge's **SERP analyst**. Given a topic, you map its search landscape \
with the `web_search` (Exa) tool and return a structured analysis.

## Your task
- Search the topic with `web_search`, then derive every field from the returned \
results — not from prior knowledge.
- Capture: the organic ranking results, the questions searchers ask, adjacent \
queries, keyword groupings, and the dominant search intent.

## Rules
- Search only — never open, scrape, or read the full body of a page.
- Prefer a diverse result set: span different domains and source types (official \
docs, independent analyses, reputable news, practitioner blogs) over many pages \
from one site.
- Keep results in their natural ranking order; do not reorder by opinion.
- Leave a field empty (`[]` or `null`) when the search does not support it; never \
fabricate.

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
    all trace back to one parent source); backfill from remaining if too few."""
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
    db: Database, *, business_id: UUID, topic: str, locale: str
) -> dict[str, Any]:
    return db.fetch_one(
        f"insert into public.research_runs (business_id, topic, locale, status) "
        f"values (%s, %s, %s, 'searching') returning {_RESEARCH_COLUMNS}",
        (business_id, topic, locale),
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
        if status in TERMINAL:
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
    except Exception as e:  # noqa: BLE001 — surface any failure to the row
        _update(db, run_id, status="failed", error=str(e))


def get_brand(db: Database, business_id: UUID) -> dict[str, Any] | None:
    return brands.get_profile(db, business_id)
