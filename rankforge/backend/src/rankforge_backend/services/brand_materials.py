"""M6 — brand materials.

Per brand, a SEPARATE Powabase Knowledge Base built from the brand's OWN pages —
crawled from its sitemap plus manually-added URLs — so generation can later ground
drafts in the brand's real capabilities and link to its own docs.

This module owns ONLY the ingestion + the data access the routes need; generation
consumes the KB elsewhere. The flow mirrors research (import_url → poll get_source
→ add_source_to_kb) and grounding (ensure_brand_kb's compare-and-set), kept thin on
purpose.
"""

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse
from uuid import UUID
from xml.etree import ElementTree as ET

import httpx
from psycopg.types.json import Json

from ..db import Database
from ..powabase import PowabaseClient, PowabaseError
from . import business_profiles as brands
from . import grounding

log = logging.getLogger("rankforge.brand_materials")

# Same terminal sets as research/grounding so we stop polling once a source/index
# can't make further progress.
_EXTRACTION_TERMINAL = {"extracted", "attention_required", "failed", "cancelled"}
_INDEX_TERMINAL = {"indexed", "failed", "cancelled"}

# Bound import/poll concurrency like research — each page can poll for ~80s, so this
# turns a sequential crawl (≈ sum) into ≈ the slowest single page.
SCRAPE_CONCURRENCY = 5

_SOURCE_COLUMNS = "id, source_id, url, title, status, origin, created_at"


# --- KB get-or-create (compare-and-set, mirrors grounding.ensure_brand_kb) ---
async def ensure_materials_kb(
    client: PowabaseClient, db: Database, business_id: UUID
) -> str:
    """Get-or-create the brand's MATERIALS KB; cache its id on the brand.

    Separate from the grounding KB (brand_kb_id): this one holds the brand's OWN
    pages, not scraped competitor research.
    """
    brand = brands.get_profile(db, business_id)
    if brand is None:
        raise ValueError("business profile not found")
    if brand.get("materials_kb_id"):
        # keep retrieval config (reranker/top_k) current — query-time, no reindex
        try:
            await client.update_kb(
                brand["materials_kb_id"],
                retrieval_config=grounding.RETRIEVAL_CONFIG,
            )
        except Exception:  # noqa: BLE001
            pass
        return brand["materials_kb_id"]

    kb = await client.create_kb(
        f"{brand['name']} — materials",
        description=(
            "The brand's own pages for grounded, on-brand drafting and "
            "internal links."
        ),
        retrieval_config=grounding.RETRIEVAL_CONFIG,
    )
    kb_id = kb.get("id") or kb.get("knowledge_base", {}).get("id")

    # Compare-and-set: only the first concurrent writer wins. A loser (its UPDATE
    # matches 0 rows) discards the KB it just created and uses the winner's, so we
    # never leak a second KB or overwrite an existing mapping.
    won = db.fetch_one(
        "update public.business_profiles set materials_kb_id = %s "
        "where id = %s and materials_kb_id is null returning materials_kb_id",
        (kb_id, business_id),
    )
    if won is not None:
        return kb_id
    try:
        await client.delete_kb(kb_id)
    except Exception:  # noqa: BLE001
        pass
    fresh = brands.get_profile(db, business_id)
    return fresh["materials_kb_id"]


# --- brand_sources reads/writes ---
def list_sources(db: Database, business_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_SOURCE_COLUMNS} from public.brand_sources "
        "where business_id = %s order by created_at desc",
        (business_id,),
    )


def add_urls(
    db: Database, business_id: UUID, urls: list[str], origin: str
) -> int:
    """Insert a brand_sources row per URL; dedup on the unique lower(url) index.

    The unique index is on the expression `lower(url)`, so `on conflict do nothing`
    is used WITHOUT naming columns (a column-list conflict target can't reference an
    expression index). Normalizes/trims and skips empties. Returns rows inserted.
    """
    inserted = 0
    for raw in urls:
        url = (raw or "").strip()
        if not url:
            continue
        row = db.fetch_one(
            "insert into public.brand_sources (business_id, url, origin) "
            "values (%s, %s, %s) on conflict do nothing returning id",
            (business_id, url, origin),
        )
        if row is not None:
            inserted += 1
    return inserted


def _set_progress(
    db: Database, business_id: UUID, phase: str, message: str, **extra: Any
) -> None:
    """Narrate the ingest so the UI can show it live (jsonb on the brand row)."""
    db.execute(
        "update public.business_profiles set materials_progress = %s where id = %s",
        (Json({"phase": phase, "message": message, **extra}), business_id),
    )


# --- sitemap discovery ---
# Paths that are almost never standalone content worth grounding on.
_NOISE_SEGMENTS = ("/tag/", "/tags/", "/category/", "/categories/", "/author/")
# Asset/feed extensions to drop (note: .xml is allowed only for child sitemaps,
# handled separately before this filter runs).
_NOISE_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".zip", ".css", ".js", ".json", ".rss", ".atom",
)
# Path hints that a URL is a real content page — preferred when capping.
_CONTENT_HINTS = ("docs", "doc", "blog", "product", "products", "guide", "guides",
                  "feature", "features", "use-case", "use-cases", "solution",
                  "solutions", "help", "support", "learn", "article", "articles")


def _is_noise(url: str) -> bool:
    low = url.lower()
    path = urlparse(low).path
    if any(seg in low for seg in _NOISE_SEGMENTS):
        return True
    return any(path.endswith(ext) for ext in _NOISE_EXT)


def _is_content(url: str) -> bool:
    segs = {s for s in urlparse(url.lower()).path.split("/") if s}
    return bool(segs & set(_CONTENT_HINTS))


def _parse_locs(xml_text: str) -> list[str]:
    """Pull every <loc> out of a sitemap or sitemap-index, namespace-agnostic."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]  # strip namespace
        if tag == "loc" and el.text and el.text.strip():
            out.append(el.text.strip())
    return out


def sitemap_urls(sitemap_url: str, limit: int = 30) -> list[str]:
    """Fetch a sitemap (resolving one level of sitemap-index) and return content
    page URLs, descaling obvious noise and capping to `limit`.

    Resilient by design — any failure returns []; the ingest must still proceed on
    just the manual + existing URLs.
    """
    if not sitemap_url:
        return []
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(sitemap_url)
            resp.raise_for_status()
            locs = _parse_locs(resp.text)

            # Sitemap-index: <loc>s point at child sitemaps. Fetch one level down
            # (a bounded few) and gather their page <loc>s instead.
            child_sitemaps = [u for u in locs if u.lower().endswith(".xml")]
            if child_sitemaps and all(u.lower().endswith(".xml") for u in locs):
                pages: list[str] = []
                for child in child_sitemaps[:5]:
                    try:
                        cr = client.get(child)
                        cr.raise_for_status()
                        pages.extend(_parse_locs(cr.text))
                    except httpx.HTTPError:
                        continue
                locs = pages
    except httpx.HTTPError:
        return []
    except Exception:  # noqa: BLE001 — never let a malformed sitemap break ingest
        log.exception("sitemap fetch/parse failed for %s", sitemap_url)
        return []

    seen: set[str] = set()
    cleaned: list[str] = []
    for u in locs:
        u = u.strip()
        if not u or u in seen or _is_noise(u):
            continue
        seen.add(u)
        cleaned.append(u)

    # Prefer obvious content pages, then backfill with the rest, capped to `limit`.
    content = [u for u in cleaned if _is_content(u)]
    rest = [u for u in cleaned if not _is_content(u)]
    return (content + rest)[:limit]


# --- the background worker ---
async def _import_one(
    client: PowabaseClient, db: Database, kb_id: str, row: dict[str, Any]
) -> None:
    """Import one brand page as a Source, wait for extraction, add to the KB."""
    row_id = row["id"]
    source_id = row.get("source_id")
    if not source_id:
        try:
            imp = await client.import_url(row["url"])
            source_id = (imp.get("sources") or [{}])[0].get("id")
        except PowabaseError as e:
            body = e.body if isinstance(e.body, dict) else {}
            source_id = (body.get("duplicate") or {}).get("id")
        if not source_id:
            await db.aexecute(
                "update public.brand_sources set status = 'failed' where id = %s",
                (row_id,),
            )
            return
        await db.aexecute(
            "update public.brand_sources set source_id = %s where id = %s",
            (source_id, row_id),
        )

    status = None
    title = row.get("title")
    for _ in range(40):  # poll up to ~80s
        src = await client.get_source(source_id)
        status = src.get("extraction_status")
        title = title or src.get("title") or src.get("name")
        if status in _EXTRACTION_TERMINAL:
            break
        await asyncio.sleep(2)

    await db.aexecute(
        "update public.brand_sources set status = %s, "
        "title = coalesce(%s, title) where id = %s",
        (status, title, row_id),
    )
    if status == "extracted":
        try:
            await client.add_source_to_kb(kb_id, source_id)
        except Exception:  # noqa: BLE001 — a source that won't index shouldn't fail all
            log.exception("add_source_to_kb failed for %s", source_id)


async def ingest(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    *,
    extra_urls: tuple[str, ...] = (),
) -> None:
    """Build/refresh the brand's materials KB from its sitemap + manual URLs."""
    try:
        _set_progress(db, business_id, "starting", "Gathering brand pages…")
        kb_id = await ensure_materials_kb(client, db, business_id)

        brand = brands.get_profile(db, business_id)
        sitemap = (brand or {}).get("sitemap_url") if brand else None
        if sitemap:
            add_urls(db, business_id, sitemap_urls(sitemap), "sitemap")
        if extra_urls:
            add_urls(db, business_id, list(extra_urls), "manual")

        # Everything not yet successfully indexed: never imported, or import/extract
        # didn't reach 'extracted'.
        pending = db.fetch_all(
            f"select {_SOURCE_COLUMNS} from public.brand_sources "
            "where business_id = %s and (source_id is null or status is null "
            "or status <> 'extracted') order by created_at",
            (business_id,),
        )
        total = len(pending)
        _set_progress(
            db, business_id, "scraping",
            f"Importing {total} brand page{'' if total == 1 else 's'}…",
            total=total, done=0,
        )

        sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
        done = 0

        async def _bounded(row: dict[str, Any]) -> None:
            nonlocal done
            async with sem:
                await _import_one(client, db, kb_id, row)
            done += 1  # advisory live progress (racy writes are fine)
            try:
                await asyncio.to_thread(
                    _set_progress, db, business_id, "scraping",
                    f"Imported {done}/{total} brand pages…", total=total, done=done,
                )
            except Exception:  # noqa: BLE001
                pass

        await asyncio.gather(*[_bounded(r) for r in pending])

        # Wait for indexing to settle (bounded), then finish.
        for _ in range(45):
            listing = await client.list_kb_sources(kb_id)
            items = listing.get("items", []) if isinstance(listing, dict) else []
            statuses = [i.get("index_status") for i in items]
            if statuses and all(s in _INDEX_TERMINAL for s in statuses):
                break
            await asyncio.sleep(2)

        n = len(list_sources(db, business_id))
        _set_progress(
            db, business_id, "done",
            f"{n} brand page{'' if n == 1 else 's'} indexed.",
        )
    except Exception:  # noqa: BLE001 — surface a safe failure on the brand row
        log.exception("brand-materials ingest failed for business %s", business_id)
        _set_progress(
            db, business_id, "failed",
            "Brand-materials ingest failed — see server logs.",
        )


def remove_source(
    client: PowabaseClient, db: Database, business_id: UUID, row_id: UUID
) -> bool:
    """Delete one brand_sources row. Returns whether a row was deleted.

    v1 keeps the KB (and its other sources) intact — removing a single source from
    the KB isn't critical, so we only drop the tracking row. `client` is accepted
    for parity/future use.
    """
    row = db.fetch_one(
        "delete from public.brand_sources where id = %s and business_id = %s "
        "returning id",
        (row_id, business_id),
    )
    return row is not None
