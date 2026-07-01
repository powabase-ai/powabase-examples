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
        "rs.trust_score, rs.trust_reason, rs.created_at, rs.research_run_id, "
        "rr.topic as run_topic "
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
        # A short excerpt of the real content so the source-quality judge can assess more
        # than the domain name (a thin SEO blog and an authoritative guide look identical
        # from URL + title alone).
        "excerpt": md[:600] if md else "",
    }


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
            res = await _scrape_one(client, u, title_by_url)
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
