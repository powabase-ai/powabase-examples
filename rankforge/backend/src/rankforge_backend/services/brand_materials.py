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
from ..powabase import (
    EXTRACTION_TERMINAL,
    PowabaseClient,
    PowabaseError,
    indexed_source_id,
    wait_for_kb_index,
)
from . import business_profiles as brands
from . import grounding, source_refs

log = logging.getLogger("rankforge.brand_materials")

# Default cap on pages discovered per ingest (the platform clamps further).
DEFAULT_MAX_PAGES = 30

# Bound import/poll concurrency like research — each page can poll for ~80s, so this
# turns a sequential crawl (≈ sum) into ≈ the slowest single page.
SCRAPE_CONCURRENCY = 5

_SOURCE_COLUMNS = "id, source_id, url, title, status, origin, created_at"

# Brand pages are short, self-contained docs — we want each kept whole (not chopped
# mid-section) so the writer describes the brand accurately and links to the right
# page. Bigger chunks + more overlap than the platform default (2000/50) means most
# pages land in a single coherent chunk while keeping cheap, no-LLM chunk_embed
# indexing + granular citation + BM25. Merged over the chunk_embed defaults on
# create (so strategy stays chunk_embed). See ensure_materials_kb / _ensure_indexing.
MATERIALS_CHUNK_SIZE = 3500
MATERIALS_OVERLAP = 200
MATERIALS_INDEXING = {"chunk_size": MATERIALS_CHUNK_SIZE, "overlap": MATERIALS_OVERLAP}


async def _ensure_indexing(client: PowabaseClient, kb_id: str) -> None:
    """Bring an EXISTING materials KB onto the larger chunk config, one-time.

    If the KB's current chunk_size already matches, do nothing. Otherwise PATCH the
    full indexing_config (read-modify-write — PATCH replaces, doesn't merge) and
    reindex so the new chunking takes effect. Self-heals KBs created before this
    config; best-effort (a failure just leaves the old chunking in place)."""
    try:
        kb = await client.get_kb(kb_id)
    except Exception:  # noqa: BLE001
        return
    cfg = (kb or {}).get("indexing_config") or {}
    if cfg.get("chunk_size") == MATERIALS_CHUNK_SIZE:
        return  # already on the desired config
    new_cfg = {**cfg, "chunk_size": MATERIALS_CHUNK_SIZE, "overlap": MATERIALS_OVERLAP}
    new_cfg.setdefault("strategy", "chunk_embed")
    try:
        await client.update_kb(kb_id, indexing_config=new_cfg)
        await client.reindex_kb(kb_id)
        log.info("materials KB %s reindexed at chunk_size=%s", kb_id, MATERIALS_CHUNK_SIZE)
    except Exception:  # noqa: BLE001 — keep ingesting even if the reindex didn't take
        log.exception("materials KB %s reindex failed", kb_id)


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
        kb_id = brand["materials_kb_id"]
        # one-time: migrate an existing KB onto the larger brand-page chunking
        await _ensure_indexing(client, kb_id)
        # keep retrieval config (reranker/top_k) current — query-time, no reindex
        try:
            await client.update_kb(
                kb_id, retrieval_config=grounding.RETRIEVAL_CONFIG
            )
        except Exception:  # noqa: BLE001
            pass
        return kb_id

    kb = await client.create_kb(
        f"{brand['name']} — materials",
        description=(
            "The brand's own pages for grounded, on-brand drafting and "
            "internal links."
        ),
        retrieval_config=grounding.RETRIEVAL_CONFIG,
        indexing_config=MATERIALS_INDEXING,
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


def _dedup_id(body: Any) -> tuple[str | None, str | None]:
    """From a Powabase 'duplicate' payload (the 409 body when a Source already exists
    project-wide for this content/URL), pull (existing_source_id, extraction_status).

    Powabase dedups Sources across the WHOLE project, so a second workspace uploading
    the same file gets a 409 carrying the existing Source — reuse it instead of
    surfacing a "duplicate source" error the user can't act on. Returns (None, None)
    on any unexpected shape so the caller can fall back to treating it as a failure.
    """
    dup = body.get("duplicate") if isinstance(body, dict) else None
    if not isinstance(dup, dict):
        return None, None
    return (dup.get("id") or None), (dup.get("extraction_status") or None)


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


def mark_starting(db: Database, business_id: UUID, message: str) -> None:
    """Record a 'starting' phase SYNCHRONOUSLY (committed) before the route returns
    202. The spawned worker's own first _set_progress races the client's immediate
    poll — if the poll wins, it sees idle progress and never starts polling. Writing
    here first guarantees the next GET observes a running phase."""
    _set_progress(db, business_id, "starting", message)


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
# Cap bytes read per discovery fetch so a huge (or malicious) page/sitemap body
# can't exhaust memory — time is bounded by the httpx timeout, size by this.
_MAX_DISCOVER_BYTES = 4_000_000


def _is_asset(path: str) -> bool:
    low = path.lower()
    return low.endswith(_ASSET_EXT) or any(seg in low for seg in _ASSET_SEGMENTS)


def _registrable(host: str) -> str:
    """Best-effort eTLD+1 (last two labels). Good enough to keep a crawl on the
    brand's own domain while allowing its subdomains (docs./app./blog.)."""
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "")


def _is_public_host(host: str) -> bool:
    """SSRF guard: True only if `host` resolves exclusively to GLOBALLY-routable IPs.

    discover_pages fetches a user-supplied URL server-side, so without this an
    editor could point it at loopback / private / link-local / CGNAT addresses
    (e.g. the cloud metadata endpoint 169.254.169.254) and use the server as a
    proxy. `is_global` is False for every reserved/private/loopback/link-local/
    CGNAT(100.64/10)/IPv4-mapped-private range, which is exactly what we want to
    reject — broader and simpler than enumerating the special ranges by hand.
    """
    host = (host or "").strip()
    if host.startswith("["):  # IPv6 literal, e.g. [::1]:443 → ::1
        host = host[1:].split("]", 1)[0]
    else:
        host = host.split(":", 1)[0]  # strip :port
    host = host.lower()
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
            return False  # fail closed: can't classify it → don't fetch it
        if not addr.is_global:  # rejects if ANY resolved address is non-public
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

    def _fetch(c: httpx.Client, url: str) -> str | None:
        """Return the text of a successful, public, html/xml page — following
        redirects manually so EVERY hop is re-validated (httpx auto-redirects would
        bypass the SSRF check) and reading at most _MAX_DISCOVER_BYTES. None on any
        non-http(s) scheme, non-public host, redirect-with-no-location, >=400, a
        non-text content-type, or transport error."""
        for _ in range(4):  # initial + up to 3 redirects
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not _is_public_host(p.netloc):
                return None
            try:
                with c.stream("GET", url) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            return None
                        url = urljoin(url, loc)
                        continue
                    if resp.status_code >= 400:
                        return None
                    ctype = resp.headers.get("content-type", "").lower()
                    if ctype and not any(
                        t in ctype for t in ("html", "xml", "text")
                    ):
                        return None  # not a page/sitemap — don't buffer it
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in resp.iter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= _MAX_DISCOVER_BYTES:
                            break
                    return b"".join(chunks).decode(
                        resp.encoding or "utf-8", "replace"
                    )
            except httpx.HTTPError:
                return None
        return None

    _add(raw)
    try:
        # follow_redirects=False: _fetch follows them manually, re-validating each
        # hop's host (an auto-redirect could bounce to an internal host).
        with httpx.Client(timeout=15.0, follow_redirects=False) as c:
            root_text = _fetch(c, raw)
            if root_text:
                for href in _HREF_RE.findall(root_text):
                    _add(urljoin(raw, href))
            # sitemap (one level of sitemap-index), bounded
            if len(found) < max_pages:
                sm_text = _fetch(c, f"{base.scheme}://{base.netloc}/sitemap.xml")
                if sm_text:
                    locs = _LOC_RE.findall(sm_text)
                    children = [u for u in locs if u.lower().endswith(".xml")]
                    for u in locs:
                        if not u.lower().endswith(".xml"):
                            _add(u)
                    for child in children[:5]:
                        if len(found) >= max_pages:
                            break
                        # only same-site children (host re-checked inside _fetch)
                        if _registrable(urlparse(child).netloc) != site:
                            continue
                        child_text = _fetch(c, child)
                        if child_text:
                            for loc in _LOC_RE.findall(child_text):
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
            # Same URL already a Source project-wide → reuse the existing one.
            source_id, _ = _dedup_id(e.body)
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
        if status in EXTRACTION_TERMINAL:
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
        await wait_for_kb_index(client, kb_id)

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

        try:
            uploaded = await client.upload_source(file_name, content, mime)
            source_id = uploaded.get("id") or (uploaded.get("source") or {}).get("id")
            init_status = uploaded.get("extraction_status")
            init_title = uploaded.get("title") or uploaded.get("name")
        except PowabaseError as e:
            # Another workspace already uploaded this exact file — Powabase dedups
            # Sources project-wide and 409s with the existing one. Reuse it so this
            # user never hits a "duplicate source" error for content they can't see.
            source_id, init_status = _dedup_id(e.body)
            init_title = None
            if not source_id:
                raise  # a genuine upload failure → outer handler marks 'failed'
            log.info("upload dedup: reusing source %s for %s", source_id, file_name)
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
            (business_id, file_name, source_id, init_status or "pending"),
        )
        if row is None:
            # A row for this file_name already exists — adopt this source on it.
            row = db.fetch_one(
                "update public.brand_sources set source_id = %s "
                "where business_id = %s and lower(url) = lower(%s) returning id",
                (source_id, business_id, file_name),
            )
        row_id = row["id"] if row else None

        status = init_status
        title = init_title
        _set_progress(
            db, business_id, "scraping", f"Extracting {file_name}…"
        )
        for _ in range(40):  # poll up to ~80s
            src = await client.get_source(source_id)
            status = src.get("extraction_status")
            title = title or src.get("title") or src.get("name")
            if status in EXTRACTION_TERMINAL:
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
            await wait_for_kb_index(client, kb_id)
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
            indexed_id = await indexed_source_id(client, kb_id, source_id)
            try:
                await client.remove_source_from_kb(kb_id, indexed_id)
            except Exception:  # noqa: BLE001 — de-index failure must not block deletion
                log.exception(
                    "remove_source_from_kb failed for %s/%s", kb_id, indexed_id
                )

    # Drop this workspace's tracking row FIRST, then decide on the project-wide Source.
    # Removing our reference before counting makes the decision orphan-safe under
    # concurrency: two removals of rows sharing one Source can't both see the other and
    # both skip (the worst case is a harmless double delete_source that 404s). The
    # Source is only deleted once nothing — anywhere — still references it.
    deleted = await db.afetch_one(
        "delete from public.brand_sources where id = %s and business_id = %s "
        "returning id",
        (row_id, business_id),
    )
    if (
        deleted is not None
        and source_id
        and source_refs.source_reference_count(db, source_id) == 0
    ):
        try:
            await client.delete_source(source_id)
        except Exception:  # noqa: BLE001 — Source delete must not block the row delete
            log.exception("delete_source failed for %s", source_id)
    return deleted is not None


async def source_content(
    client: PowabaseClient, db: Database, business_id: UUID, row_id: UUID
) -> str | None:
    """Return a brand source's extracted markdown.

    Returns None only when the row has no linked Source yet (nothing to show — a 404).
    A platform/upstream failure (e.g. the source service returning 502 for a broken or
    stale source) propagates as PowabaseError so the route can report it honestly
    instead of masking it as 'not found' — the user can then refresh/re-scrape it.
    """
    row = db.fetch_one(
        "select source_id from public.brand_sources "
        "where id = %s and business_id = %s",
        (row_id, business_id),
    )
    source_id = (row or {}).get("source_id")
    if not source_id:
        return None
    return await client.get_source_markdown(source_id)


# --- bulk actions over selected rows (refresh / delete) ---
def _is_refreshable_url(url: str | None) -> bool:
    """Only http(s) pages can be re-scraped. File uploads (origin='manual', url=the
    file name) have no live URL — they must be re-uploaded, not refreshed."""
    u = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


async def _refresh_one(
    client: PowabaseClient,
    db: Database,
    kb_id: str,
    row: dict[str, Any],
    indexed_ids: dict[str, str],
) -> bool:
    """Force a fresh re-scrape of one URL-backed brand page.

    The platform dedups Sources by URL, so simply re-importing returns the SAME stale
    content. To actually pick up a changed page we de-index + delete the old Source
    first (clearing the dedup), then re-import the URL and re-index. Returns False for
    a non-URL row (an uploaded file — nothing to re-scrape).
    """
    url = row.get("url") or ""
    if not _is_refreshable_url(url):
        return False
    old_sid = row.get("source_id")
    if old_sid:
        indexed_id = indexed_ids.get(old_sid) or old_sid
        try:
            await client.remove_source_from_kb(kb_id, indexed_id)
        except Exception:  # noqa: BLE001 — de-index failure must not block the refresh
            log.exception("refresh de-index failed for %s", old_sid)
    # Clear this row's reference up front, THEN decide on the old Source — so a refresh
    # racing another remove/refresh of a co-referencing row can't orphan it.
    await db.aexecute(
        "update public.brand_sources set source_id = null, status = 'pending' "
        "where id = %s",
        (row["id"],),
    )
    # Refresh works by deleting the old Source so the re-import isn't deduped back to
    # stale content — but only when this workspace is its sole owner. If another shares
    # it, leave it (the re-import returns the same content) rather than breaking them.
    if old_sid and source_refs.source_reference_count(db, old_sid) == 0:
        try:
            await client.delete_source(old_sid)
        except Exception:  # noqa: BLE001 — stale Source delete is best-effort
            log.exception("refresh delete_source failed for %s", old_sid)
    # Re-import fresh (no existing Source for this URL now → a real re-scrape), poll
    # extraction, re-index. Empty already_indexed so _import_one re-adds it to the KB.
    await _import_one(client, db, kb_id, {**row, "source_id": None}, set())
    return True


async def refresh_sources(
    client: PowabaseClient, db: Database, business_id: UUID, row_ids: list[UUID]
) -> None:
    """Background worker: re-scrape the selected URL-backed brand pages so changed
    content is picked up. Uploaded files are skipped (no URL to re-fetch). Narrates
    progress on the brand row; never raises out of the detached task.
    """
    try:
        _set_progress(db, business_id, "starting", "Refreshing selected pages…")
        kb_id = await ensure_materials_kb(client, db, business_id)
        rows = db.fetch_all(
            f"select {_SOURCE_COLUMNS} from public.brand_sources "
            "where business_id = %s and id = any(%s)",
            (business_id, list(row_ids)),
        )
        refreshable = [r for r in rows if _is_refreshable_url(r.get("url"))]
        skipped = len(rows) - len(refreshable)
        total = len(refreshable)
        if not total:
            _set_progress(
                db, business_id, "done",
                "Nothing to refresh — the selected items are uploaded files "
                "(re-upload them to update).",
            )
            return

        # Resolve indexed-source ids once (one KB listing) for de-indexing.
        indexed_ids: dict[str, str] = {}
        try:
            listing = await client.list_kb_sources(kb_id)
            for it in listing.get("items", []) if isinstance(listing, dict) else []:
                sid = it.get("source_id")
                if sid:
                    indexed_ids[sid] = it.get("id") or sid
        except Exception:  # noqa: BLE001 — fall back to raw ids per source
            log.exception("refresh: list_kb_sources failed")

        _set_progress(
            db, business_id, "scraping",
            f"Re-scraping {total} page{'' if total == 1 else 's'}…",
            total=total, done=0,
        )
        sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
        done = 0

        async def _bounded(r: dict[str, Any]) -> None:
            nonlocal done
            async with sem:
                try:
                    await _refresh_one(client, db, kb_id, r, indexed_ids)
                except Exception:  # noqa: BLE001 — one page's failure shouldn't fail all
                    log.exception("refresh failed for %s", r.get("id"))
            done += 1
            try:
                await asyncio.to_thread(
                    _set_progress, db, business_id, "indexing",
                    f"Refreshed {done}/{total}…", total=total, done=done,
                )
            except Exception:  # noqa: BLE001
                pass

        await asyncio.gather(*[_bounded(r) for r in refreshable])

        # Wait for indexing to settle (bounded), then rebuild BM25 over new chunks.
        await wait_for_kb_index(client, kb_id)
        await grounding.rebuild_bm25(client, kb_id)

        msg = f"Refreshed {total} page{'' if total == 1 else 's'}."
        if skipped:
            msg += f" Skipped {skipped} uploaded file{'' if skipped == 1 else 's'}."
        _set_progress(db, business_id, "done", msg)
    except Exception as e:  # noqa: BLE001 — surface a safe failure on the brand row
        log.exception("brand-materials refresh failed for business %s", business_id)
        _set_progress(db, business_id, "failed", f"Couldn't refresh pages: {_reason(e)}")


async def remove_sources(
    client: PowabaseClient, db: Database, business_id: UUID, row_ids: list[UUID]
) -> None:
    """Background worker: cascade-delete the selected brand sources (mass deletion).
    Narrates progress on the brand row; never raises out of the detached task."""
    try:
        n = len(row_ids)
        _set_progress(
            db, business_id, "deleting",
            f"Removing {n} page{'' if n == 1 else 's'}…", total=n, done=0,
        )
        sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
        removed = 0
        done = 0

        async def _bounded(rid: UUID) -> None:
            nonlocal removed, done
            async with sem:
                try:
                    if await remove_source(client, db, business_id, rid):
                        removed += 1
                except Exception:  # noqa: BLE001 — one failure shouldn't block the rest
                    log.exception("bulk remove failed for %s", rid)
            done += 1
            try:
                await asyncio.to_thread(
                    _set_progress, db, business_id, "deleting",
                    f"Removed {done}/{n}…", total=n, done=done,
                )
            except Exception:  # noqa: BLE001
                pass

        await asyncio.gather(*[_bounded(r) for r in row_ids])
        _set_progress(
            db, business_id, "done",
            f"Removed {removed} page{'' if removed == 1 else 's'}.",
        )
    except Exception as e:  # noqa: BLE001 — surface a safe failure on the brand row
        log.exception("brand-materials bulk delete failed for business %s", business_id)
        _set_progress(db, business_id, "failed", f"Couldn't remove pages: {_reason(e)}")
