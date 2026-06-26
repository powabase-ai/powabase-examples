"""M6 / Phase 12.3 — broken-link detection (the "fix broken links" half).

Validates each article's outbound links and records the broken ones for review:
  - INTERNAL `/p/{id}` links — the target article must still be PUBLISHED (a cheap
    DB check; a target that was unpublished/deleted breaks the link).
  - EXTERNAL http(s) links — must not 4xx/5xx or fail to resolve (an SSRF-guarded
    HEAD/GET, redirects NOT followed so a 3xx counts as healthy and a redirect can't
    bounce the check to an internal host).

Findings are surfaced (status 'open') for an editor to fix in the prose or mark
'ignored'. We never auto-edit published content.
"""

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx

from ..db import Database
from . import generation as gen_svc
from .brand_materials import _is_public_host

log = logging.getLogger("rankforge.linkcheck")

_COLUMNS = (
    "id, business_id, article_id, url, anchor_text, kind, http_status, reason, "
    "status, checked_at, created_at"
)

# [text](url) — tolerate an optional <...> around the URL; stop at whitespace/paren.
_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*<?([^\s)>]+)>?\s*\)")
_FENCED = re.compile(r"```.*?```", re.S)
_INTERNAL_RE = re.compile(r"^/p/([0-9a-fA-F-]{36})/?$")
# Internal-link refs (resolved to live URLs at render time) — check the TARGET's
# integrity (exists + published), not an HTTP URL that may not be live on the blog yet.
_REF_RE = re.compile(r"^rf:article/([0-9a-fA-F-]{36})$")

_CHECK_CONCURRENCY = 6
_TIMEOUT = 10.0
_MAX_ARTICLES_PER_RUN = 300


def _extract_links(md: str) -> list[tuple[str, str]]:
    """(anchor, url) for every markdown link, skipping fenced code blocks."""
    body = _FENCED.sub("", md or "")
    return [
        (m.group(1).strip(), m.group(2).strip()) for m in _LINK_RE.finditer(body)
    ]


def _internal_reason(db: Database, target_id: str) -> str | None:
    """None if the internal target is a published article; else why it's broken."""
    row = db.fetch_one(
        "select status from public.articles where id = %s", (target_id,)
    )
    if row is None:
        return "linked article no longer exists"
    if row.get("status") != "published":
        return "linked article is no longer published"
    return None


async def _external_reason(
    client: httpx.AsyncClient, url: str
) -> tuple[int | None, str | None]:
    """(http_status, reason). reason None = healthy. Skips non-public hosts (returns
    healthy) to avoid both SSRF and false positives on hosts we won't fetch."""
    p = urlparse(url)
    if not _is_public_host(p.netloc):
        return None, None
    try:
        resp = await client.head(url)
        if resp.status_code in (403, 405, 501):  # some servers reject HEAD
            resp = await client.get(url)
        # redirects are not followed → 3xx means the link resolves (healthy).
        if resp.status_code >= 400:
            return resp.status_code, f"HTTP {resp.status_code}"
        return resp.status_code, None
    except httpx.HTTPError:
        return None, "unreachable"


def list_findings(
    db: Database, article_id: UUID, status: str = "open"
) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_COLUMNS} from public.link_health "
        "where article_id = %s and status = %s order by created_at",
        (article_id, status),
    )


def ignore_finding(
    db: Database, business_id: UUID, finding_id: UUID
) -> dict[str, Any] | None:
    return db.fetch_one(
        f"update public.link_health set status = 'ignored', updated_at = now() "
        f"where id = %s and business_id = %s returning {_COLUMNS}",
        (finding_id, business_id),
    )


def _record_broken(
    db: Database, business_id: UUID, article_id: UUID, f: dict[str, Any]
) -> dict[str, Any]:
    """Upsert a broken finding to 'open' — but never resurrect one the user 'ignored'."""
    return db.fetch_one(
        "insert into public.link_health "
        "(business_id, article_id, url, anchor_text, kind, http_status, reason) "
        "values (%s, %s, %s, %s, %s, %s, %s) "
        "on conflict (article_id, lower(url)) do update set "
        "  anchor_text = excluded.anchor_text, http_status = excluded.http_status, "
        "  reason = excluded.reason, checked_at = now(), updated_at = now(), "
        "  status = case when public.link_health.status = 'ignored' "
        "                then 'ignored' else 'open' end "
        f"returning {_COLUMNS}",
        (
            business_id, article_id, f["url"], f["anchor"], f["kind"],
            f["http_status"], f["reason"],
        ),
    )


def _resolve(db: Database, article_id: UUID, url: str, http_status: int | None) -> None:
    """A previously-open link that now checks out → mark resolved (leave 'ignored')."""
    db.execute(
        "update public.link_health set status = 'resolved', http_status = %s, "
        "checked_at = now(), updated_at = now() "
        "where article_id = %s and lower(url) = lower(%s) and status = 'open'",
        (http_status, article_id, url),
    )


async def check_article(
    db: Database, business_id: UUID, article_id: UUID
) -> list[dict[str, Any]]:
    """Check every outbound link in one article; record broken ones, resolve fixed
    ones. Returns the currently-open (broken) findings."""
    art = gen_svc.get_article(db, article_id)
    md = (art or {}).get("content_md") or ""
    # De-dupe by url (one finding per distinct target), keeping the first anchor seen.
    by_url: dict[str, str] = {}
    for anchor, url in _extract_links(md):
        by_url.setdefault(url, anchor)

    sem = asyncio.Semaphore(_CHECK_CONCURRENCY)
    open_findings: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        async def _one(url: str, anchor: str) -> None:
            internal = _INTERNAL_RE.match(url) or _REF_RE.match(url)
            if internal:
                reason, status, kind = (
                    _internal_reason(db, internal.group(1)), None, "internal",
                )
            elif url.startswith(("http://", "https://")):
                async with sem:
                    status, reason = await _external_reason(client, url)
                kind = "external"
            else:
                return  # mailto:, in-page #anchor, other relative paths — skip
            if reason:
                open_findings.append(
                    _record_broken(
                        db, business_id, article_id,
                        {"url": url, "anchor": anchor, "kind": kind,
                         "http_status": status, "reason": reason},
                    )
                )
            else:
                _resolve(db, article_id, url, status)

        await asyncio.gather(*[_one(u, a) for u, a in by_url.items()])
    return open_findings


async def check_business(db: Database, business_id: UUID) -> int:
    """Check every published article's links (used by the re-linking scout). Returns
    the number of broken links found across the library."""
    published = db.fetch_all(
        "select id from public.articles "
        "where business_id = %s and status = 'published' "
        f"order by updated_at desc limit {_MAX_ARTICLES_PER_RUN}",
        (business_id,),
    )
    total = 0
    for row in published:
        try:
            total += len(await check_article(db, business_id, row["id"]))
        except Exception:  # noqa: BLE001 — one article shouldn't fail the sweep
            log.exception("linkcheck failed for article %s", row["id"])
    return total
