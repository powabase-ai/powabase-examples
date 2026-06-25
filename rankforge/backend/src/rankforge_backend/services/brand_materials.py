"""M6 — brand materials.

Per brand, a SEPARATE Powabase Knowledge Base built from the brand's OWN pages —
discovered by crawling its site, parsing its sitemap, or from manually-added URLs —
so generation can later ground drafts in the brand's real capabilities and link to
its own docs.

Page discovery + import is delegated to the platform's `/api/sources/import-url`
(the same crawl/sitemap/urls modes Powabase BaaS exposes), so a site without a
sitemap can still be crawled. This module owns ONLY the ingestion orchestration +
the data access the routes need; generation consumes the KB elsewhere.
"""

import asyncio
import ipaddress
import logging
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

import httpx
from psycopg.types.json import Json

from ..db import Database
from ..powabase import PowabaseClient, PowabaseError
from . import business_profiles as brands
from . import grounding

log = logging.getLogger("rankforge.brand_materials")

# Default cap on pages discovered per ingest (the platform clamps further).
DEFAULT_MAX_PAGES = 30

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


def _track_source(
    db: Database, business_id: UUID, *, url: str, source_id: str | None, origin: str
) -> None:
    """Record one imported page as a brand_sources row (or adopt its source_id onto
    an existing row). Dedup is on the unique lower(url) index, so `on conflict do
    nothing` is used WITHOUT a column target (it can't reference an expression
    index). Skips empty URLs."""
    url = (url or "").strip()
    if not url:
        return
    row = db.fetch_one(
        "insert into public.brand_sources (business_id, url, origin, source_id, status) "
        "values (%s, %s, %s, %s, 'pending') on conflict do nothing returning id",
        (business_id, url, origin, source_id),
    )
    if row is None and source_id:
        # URL already tracked — backfill the source_id if it wasn't set yet.
        db.execute(
            "update public.brand_sources set source_id = coalesce(source_id, %s) "
            "where business_id = %s and lower(url) = lower(%s)",
            (source_id, business_id, url),
        )


def _reason(e: Exception) -> str:
    """A short, UI-safe failure reason. Prefers a Powabase API body's `error`
    message (e.g. 'URL import is currently unavailable'), else the exception text,
    capped so the progress banner stays readable."""
    if isinstance(e, PowabaseError):
        body = e.body if isinstance(e.body, dict) else {}
        msg = body.get("error") or body.get("message") or str(e.body) or str(e)
        msg = f"{msg} (HTTP {e.status_code})"
    else:
        msg = str(e) or e.__class__.__name__
    msg = " ".join(msg.split())  # collapse whitespace/newlines
    return msg[:160] + ("…" if len(msg) > 160 else "")


def _set_progress(
    db: Database, business_id: UUID, phase: str, message: str, **extra: Any
) -> None:
    """Narrate the ingest so the UI can show it live (jsonb on the brand row)."""
    db.execute(
        "update public.business_profiles set materials_progress = %s where id = %s",
        (Json({"phase": phase, "message": message, **extra}), business_id),
    )


# --- crawl preview (read-only discovery, no import) ---
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
# Asset/non-content extensions and path fragments to drop from discovery so the
# preview lists real pages, not bundled JS/CSS/fonts/images.
_ASSET_EXT = (
    ".css", ".js", ".mjs", ".map", ".json", ".xml", ".rss", ".atom",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".zip", ".gz", ".mp4", ".webm", ".mp3", ".pdf",
)
_ASSET_SEGMENTS = ("/_next/", "/static/", "/assets/", "/_nuxt/", "/cdn-cgi/")


def _is_asset(path: str) -> bool:
    low = path.lower()
    return low.endswith(_ASSET_EXT) or any(seg in low for seg in _ASSET_SEGMENTS)


def _registrable(host: str) -> str:
    """Best-effort eTLD+1 (last two labels). Good enough to keep a crawl on the
    brand's own domain while allowing its subdomains (docs./app./blog.)."""
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "")


def _is_public_host(host: str) -> bool:
    """SSRF guard: True only if `host` resolves exclusively to public IPs.

    discover_pages fetches a user-supplied URL server-side, so without this an
    editor could point it at loopback / private / link-local addresses (e.g. the
    cloud metadata endpoint 169.254.169.254) and use the server as a proxy. Rejects
    obvious internal names and any host that resolves to a non-public address.
    """
    host = (host or "").split(":")[0].strip().lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal")):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return False
    return True


def discover_pages(root_url: str, max_pages: int = DEFAULT_MAX_PAGES) -> list[str]:
    """Shallow, READ-ONLY page discovery for the crawl preview — collect same-site
    links (including subdomains) from the root page and its sitemap, so the user can
    see which subdomains/pages were found and confirm BEFORE anything is imported.

    Best-effort: returns [] on failure. Nothing here touches Powabase or the DB; the
    actual import still runs on the platform once the user confirms a URL set. Only
    fetches public, same-registrable-domain hosts (SSRF guard).
    """
    raw = (root_url or "").strip()
    if not raw:
        return []
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    base = urlparse(raw)
    site = _registrable(base.netloc)
    if not _is_public_host(base.netloc):
        return []
    found: dict[str, None] = {}

    def _add(u: str) -> None:
        u = (u or "").split("#")[0].strip()
        if not u:
            return
        p = urlparse(u)
        if p.scheme not in ("http", "https") or _registrable(p.netloc) != site:
            return
        if _is_asset(p.path):
            return
        found.setdefault(u.rstrip("/") or u, None)

    def _fetch(c: httpx.Client, url: str) -> httpx.Response | None:
        """GET a URL, following redirects manually so EVERY hop's host is
        re-validated as public (httpx auto-redirects would bypass the SSRF check)."""
        for _ in range(4):  # initial + up to 3 redirects
            if not _is_public_host(urlparse(url).netloc):
                return None
            try:
                resp = c.get(url)
            except httpx.HTTPError:
                return None
            loc = resp.headers.get("location")
            if resp.is_redirect and loc:
                url = urljoin(url, loc)
                continue
            return resp
        return None

    _add(raw)
    try:
        # No auto-redirects: a redirect could bounce to an internal host, bypassing
        # the per-URL public-host check above.
        with httpx.Client(timeout=15.0, follow_redirects=False) as c:
            r = _fetch(c, raw)
            if r is not None and r.status_code < 400:
                for href in _HREF_RE.findall(r.text):
                    _add(urljoin(raw, href))
            # sitemap (one level of sitemap-index), bounded
            if len(found) < max_pages:
                sm = _fetch(c, f"{base.scheme}://{base.netloc}/sitemap.xml")
                if sm is not None and sm.status_code < 400:
                    locs = _LOC_RE.findall(sm.text)
                    children = [u for u in locs if u.lower().endswith(".xml")]
                    for u in locs:
                        if not u.lower().endswith(".xml"):
                            _add(u)
                    for child in children[:5]:
                        if len(found) >= max_pages:
                            break
                        # only same-site children, re-checked for public host
                        if _registrable(urlparse(child).netloc) != site:
                            continue
                        cr = _fetch(c, child)
                        if cr is not None and cr.status_code < 400:
                            for loc in _LOC_RE.findall(cr.text):
                                _add(loc)
    except Exception:  # noqa: BLE001 — discovery must never raise into the route
        log.exception("discover_pages failed for %s", raw)

    return sorted(found)[:max_pages]


# --- page discovery (delegated to the platform) ---
# What the UI sends; "sitemap" defaults to the brand's configured sitemap_url.
_DISCOVERY_MODES = ("sitemap", "crawl", "urls")


async def _discover_and_track(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    brand: dict[str, Any] | None,
    *,
    mode: str,
    url: str | None,
    extra_urls: tuple[str, ...],
    max_pages: int,
    origin: str | None = None,
) -> int:
    """Discover the brand's pages via the platform's import-url (crawl/sitemap/urls),
    importing each as a Source, and track them as brand_sources rows. Returns the
    number of pages the platform imported (0 if there was nothing to discover)."""
    if mode == "crawl":
        root = (url or "").strip()
        if not root:
            return 0
        _set_progress(db, business_id, "scraping", "Crawling your site for pages…")
        sources = await client.import_urls("crawl", url=root, max_pages=max_pages)
        row_origin = origin or "crawl"
    elif mode == "urls":
        urls = [u.strip() for u in extra_urls if u and u.strip()]
        if not urls:
            return 0
        n = len(urls)
        _set_progress(
            db, business_id, "scraping",
            f"Importing {n} page{'' if n == 1 else 's'}…",
        )
        sources = await client.import_urls("urls", urls=urls, max_pages=max_pages)
        row_origin = origin or "manual"
    else:  # sitemap
        sm = (url or (brand or {}).get("sitemap_url") or "").strip()
        if not sm:
            return 0
        _set_progress(db, business_id, "scraping", "Reading your sitemap…")
        sources = await client.import_urls("sitemap", url=sm, max_pages=max_pages)
        row_origin = origin or "sitemap"

    for s in sources:
        _track_source(
            db, business_id,
            url=s.get("url") or s.get("name") or "",
            source_id=s.get("id"),
            origin=row_origin,
        )
    return len(sources)


# --- the background worker ---
async def _import_one(
    client: PowabaseClient,
    db: Database,
    kb_id: str,
    row: dict[str, Any],
    already_indexed: set[str],
) -> None:
    """Import one brand page as a Source, wait for extraction, add to the KB.

    Skips the KB add for a source already indexed in this KB (`already_indexed`),
    so re-ingesting duplicate material doesn't trigger a wasteful re-index.
    """
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
    if status == "extracted" and source_id not in already_indexed:
        # Powabase dedups Source content by hash (no re-extraction); we additionally
        # skip re-indexing a source that's already in this KB.
        try:
            await client.add_source_to_kb(kb_id, source_id)
        except Exception:  # noqa: BLE001 — a source that won't index shouldn't fail all
            log.exception("add_source_to_kb failed for %s", source_id)


async def ingest(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    *,
    mode: str = "sitemap",
    url: str | None = None,
    extra_urls: tuple[str, ...] = (),
    max_pages: int = DEFAULT_MAX_PAGES,
    origin: str | None = None,
) -> None:
    """Build/refresh the brand's materials KB.

    `mode` picks how pages are discovered (crawl / sitemap / urls); discovery +
    import run on the platform. Then every tracked page that isn't indexed yet is
    polled to extraction and added to the KB. `origin` overrides the row provenance
    tag (used when ingesting crawl-discovered URLs the user confirmed).
    """
    try:
        _set_progress(db, business_id, "starting", "Gathering brand pages…")
        kb_id = await ensure_materials_kb(client, db, business_id)

        brand = brands.get_profile(db, business_id)
        await _discover_and_track(
            client, db, business_id, brand,
            mode=mode, url=url, extra_urls=extra_urls, max_pages=max_pages,
            origin=origin,
        )

        # Sources already indexed in this KB — skip re-indexing them (dedup).
        existing = await client.list_kb_sources(kb_id)
        e_items = existing.get("items", []) if isinstance(existing, dict) else []
        already_indexed = {
            i.get("source_id")
            for i in e_items
            if i.get("index_status") == "indexed" and i.get("source_id")
        }

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
            db, business_id, "indexing",
            f"Indexing {total} brand page{'' if total == 1 else 's'}…",
            total=total, done=0,
        )

        sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
        done = 0

        async def _bounded(row: dict[str, Any]) -> None:
            nonlocal done
            async with sem:
                await _import_one(client, db, kb_id, row, already_indexed)
            done += 1  # advisory live progress (racy writes are fine)
            try:
                await asyncio.to_thread(
                    _set_progress, db, business_id, "indexing",
                    f"Indexed {done}/{total} brand pages…", total=total, done=done,
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

        # New chunks landed — rebuild BM25 so hybrid's keyword half covers them.
        if total:
            await grounding.rebuild_bm25(client, kb_id)

        n = len(list_sources(db, business_id))
        _set_progress(
            db, business_id, "done",
            f"{n} brand page{'' if n == 1 else 's'} indexed.",
        )
    except Exception as e:  # noqa: BLE001 — surface a safe failure on the brand row
        log.exception("brand-materials ingest failed for business %s", business_id)
        _set_progress(
            db, business_id, "failed",
            f"Couldn't finish ingest: {_reason(e)}",
        )


async def ingest_file(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    *,
    file_name: str,
    content: bytes,
    mime: str,
) -> None:
    """Background worker for ONE uploaded file (mirrors `ingest` for a single page).

    Uploads the file as a Powabase Source, tracks it as a brand_sources row
    (origin='manual', url=file_name), polls extraction, then indexes it into the
    materials KB. Narrates progress on the brand row; any failure is surfaced as a
    safe 'failed' phase rather than bubbling out of the detached task.
    """
    try:
        _set_progress(db, business_id, "uploading", f"Uploading {file_name}…")
        kb_id = await ensure_materials_kb(client, db, business_id)

        uploaded = await client.upload_source(file_name, content, mime)
        source_id = uploaded.get("id") or (uploaded.get("source") or {}).get("id")
        if not source_id:
            _set_progress(
                db, business_id, "failed", f"Upload failed for {file_name}."
            )
            return

        # Track it. Dedup on the unique lower(url) index (url == file_name here).
        row = db.fetch_one(
            "insert into public.brand_sources "
            "(business_id, url, origin, source_id, status) "
            "values (%s, %s, 'manual', %s, %s) "
            "on conflict do nothing returning id",
            (
                business_id,
                file_name,
                source_id,
                uploaded.get("extraction_status") or "pending",
            ),
        )
        if row is None:
            # A row for this file_name already exists — adopt this source on it.
            row = db.fetch_one(
                "update public.brand_sources set source_id = %s "
                "where business_id = %s and lower(url) = lower(%s) returning id",
                (source_id, business_id, file_name),
            )
        row_id = row["id"] if row else None

        status = uploaded.get("extraction_status")
        title = uploaded.get("title") or uploaded.get("name")
        _set_progress(
            db, business_id, "scraping", f"Extracting {file_name}…"
        )
        for _ in range(40):  # poll up to ~80s
            src = await client.get_source(source_id)
            status = src.get("extraction_status")
            title = title or src.get("title") or src.get("name")
            if status in _EXTRACTION_TERMINAL:
                break
            await asyncio.sleep(2)

        if row_id is not None:
            await db.aexecute(
                "update public.brand_sources set status = %s, "
                "title = coalesce(%s, title) where id = %s",
                (status, title, row_id),
            )

        if status == "extracted":
            try:
                await client.add_source_to_kb(kb_id, source_id)
            except Exception:  # noqa: BLE001 — indexing failure shouldn't fail the upload
                log.exception("add_source_to_kb failed for %s", source_id)
            # Wait for indexing to settle (bounded), like `ingest`.
            for _ in range(45):
                listing = await client.list_kb_sources(kb_id)
                items = listing.get("items", []) if isinstance(listing, dict) else []
                statuses = [i.get("index_status") for i in items]
                if statuses and all(s in _INDEX_TERMINAL for s in statuses):
                    break
                await asyncio.sleep(2)
            # New chunks landed — rebuild BM25 so hybrid's keyword half covers them.
            await grounding.rebuild_bm25(client, kb_id)

        n = len(list_sources(db, business_id))
        _set_progress(
            db, business_id, "done",
            f"{file_name} added — {n} brand page{'' if n == 1 else 's'} total.",
        )
    except Exception as e:  # noqa: BLE001 — surface a safe failure on the brand row
        log.exception(
            "brand-materials file ingest failed for business %s", business_id
        )
        _set_progress(
            db, business_id, "failed",
            f"Couldn't finish upload: {_reason(e)}",
        )


async def _indexed_id(
    client: PowabaseClient, kb_id: str, source_id: str
) -> str:
    """Resolve a raw source_id to the KB's INDEXED-source id used by the de-index
    path (DELETE /knowledge-bases/{id}/sources/{indexed_source_id}).

    The KB's source listing keys each entry by its own id; match the entry whose
    source_id is ours and return its id. Best-effort — falls back to the raw
    source_id (the historical behavior) if the lookup can't resolve it.
    """
    try:
        listing = await client.list_kb_sources(kb_id)
        items = listing.get("items", []) if isinstance(listing, dict) else []
        for it in items:
            if it.get("source_id") == source_id:
                return it.get("id") or it.get("indexed_source_id") or source_id
    except Exception:  # noqa: BLE001 — fall back to the raw id on any lookup failure
        pass
    return source_id


async def remove_source(
    client: PowabaseClient, db: Database, business_id: UUID, row_id: UUID
) -> bool:
    """Cascade-delete one brand source: KB de-index → Source delete → tracking row.

    Each remote step is best-effort and isolated (a KB-removal or Source-delete
    failure must not block dropping the local row). Idempotent — returns whether a
    tracking row was actually deleted.
    """
    row = await db.afetch_one(
        "select id, source_id from public.brand_sources "
        "where id = %s and business_id = %s",
        (row_id, business_id),
    )
    if row is None:
        return False

    source_id = row.get("source_id")
    if source_id:
        brand = brands.get_profile(db, business_id)
        kb_id = (brand or {}).get("materials_kb_id")
        if kb_id:
            # De-index uses the KB's INDEXED-source id, which can differ from the raw
            # source_id — resolve it from the KB's source listing (fall back to the
            # raw id). Best-effort: a de-index failure must not block deletion.
            indexed_id = await _indexed_id(client, kb_id, source_id)
            try:
                await client.remove_source_from_kb(kb_id, indexed_id)
            except Exception:  # noqa: BLE001 — de-index failure must not block deletion
                log.exception(
                    "remove_source_from_kb failed for %s/%s", kb_id, indexed_id
                )
        try:
            await client.delete_source(source_id)
        except Exception:  # noqa: BLE001 — Source delete failure must not block row delete
            log.exception("delete_source failed for %s", source_id)

    deleted = await db.afetch_one(
        "delete from public.brand_sources where id = %s and business_id = %s "
        "returning id",
        (row_id, business_id),
    )
    return deleted is not None


async def source_content(
    client: PowabaseClient, db: Database, business_id: UUID, row_id: UUID
) -> str | None:
    """Return a brand source's extracted markdown, or None if unavailable."""
    row = db.fetch_one(
        "select source_id from public.brand_sources "
        "where id = %s and business_id = %s",
        (row_id, business_id),
    )
    source_id = (row or {}).get("source_id")
    if not source_id:
        return None
    try:
        return await client.get_source_markdown(source_id)
    except PowabaseError:
        return None
