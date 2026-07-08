"""M8 — publishing & export.

Renders a finished article to crawlable HTML (with inline JSON-LD + meta) and to
portable Markdown, ships it to a target (webhook now; CMS adapters later), and
records the publication. HTML is rendered on read, fresh from `content_md`, every
time it is needed (public SSR page, webhook payload, export) and is never persisted
— so there is no cached HTML that can go stale or skip sanitization.
"""

import html as _html
import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
import markdown as md
import nh3

from ..db import Database
from . import generation as gen_svc
from . import linking

_MD_EXTENSIONS = ["extra", "sane_lists", "toc"]

# A single leading '# H1' line (generation prepends the title to content_md). Not '## '/
# '### ' — the '[ \t]+' after '#' can't match a second '#'. Used where the renderer emits
# the title ITSELF (MDX frontmatter, standalone <h1>) so the body H1 doesn't duplicate it.
_LEADING_H1_RE = re.compile(r"\A\s*#[ \t]+[^\n]*(?:\n+|\Z)")
# Article statuses whose export should go live immediately (draft: false). Genuine
# drafts / in-review pieces stay hidden (draft: true).
_PUBLISHABLE_STATUSES = frozenset({"approved", "published"})


def _strip_leading_h1(content_md: str) -> str:
    """Drop the single leading '# Title' line from a body when the title is rendered
    separately, so exports don't show two H1s (bad for SEO, visually redundant)."""
    return _LEADING_H1_RE.sub("", content_md or "", count=1)

_PUBLICATION_COLUMNS = (
    "id, article_id, target_type, target_id, external_id, url, status, "
    "published_at, created_at"
)


# --- rendering ---
def render_body_html(content_md: str) -> str:
    """Markdown → sanitized HTML fragment (tables, fenced code, lists).

    Sanitized because this HTML is served on a PUBLIC page — a scraped source the
    writer echoed could otherwise smuggle a <script>/onerror payload through."""
    raw = md.markdown(
        content_md or "", extensions=_MD_EXTENSIONS, output_format="html"
    )
    return nh3.clean(raw)


def _meta_tags(article: dict[str, Any]) -> str:
    title = _html.escape(article.get("meta_title") or article.get("title") or "")
    desc = _html.escape(article.get("meta_description") or "")
    tags = [f"<title>{title}</title>"]
    if desc:
        tags.append(f'<meta name="description" content="{desc}">')
    tags.append(f'<meta property="og:title" content="{title}">')
    if desc:
        tags.append(f'<meta property="og:description" content="{desc}">')
    tags.append('<meta property="og:type" content="article">')
    return "\n  ".join(tags)


def _jsonld_script(article: dict[str, Any]) -> str:
    if not article.get("json_ld"):
        return ""
    # Escape "<" so content can't break out of the <script> element.
    payload = json.dumps(article["json_ld"]).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{payload}</script>'


def render_standalone_html(article: dict[str, Any]) -> str:
    """A complete, self-contained HTML document with JSON-LD + meta in <head> —
    crawlable as-is when exported or served."""
    # Strip the body's leading '# Title' — this document renders <h1>{title}</h1> itself,
    # so keeping it would emit two H1s.
    body = render_body_html(_strip_leading_h1(article.get("content_md") or ""))
    title = _html.escape(article.get("title") or "")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {_meta_tags(article)}
  {_jsonld_script(article)}
</head>
<body>
<article>
<h1>{title}</h1>
{body}
</article>
</body>
</html>
"""


def _fm_date(val: Any) -> str:
    """YYYY-MM-DD from a datetime (psycopg) or an ISO-ish string."""
    if hasattr(val, "date"):
        return val.date().isoformat()
    return str(val or "")[:10]


def render_markdown(article: dict[str, Any]) -> str:
    """Export as a blog `.mdx`: YAML frontmatter + the Markdown body.

    Matches the target blog's `content/blog/<slug>.mdx` shape — title, description,
    publishedDate, author, tags, draft — followed by a blank line and the body. Strings
    are JSON-quoted so a title/description containing a colon, `#`, etc. stays valid YAML.

    The title is the frontmatter `title` (the blog renders it as the page <h1>), so the
    body's own leading '# Title' is stripped — otherwise the page shows two H1s. `draft`
    is false once the article is approved/published (so an export actually seeds the live
    blog), true only for genuine drafts/in-review pieces."""
    fm = [
        "---",
        f"title: {json.dumps(article.get('title') or '')}",
        f"description: {json.dumps(article.get('meta_description') or '')}",
    ]
    published = _fm_date(
        article.get("published_date")
        or article.get("updated_at")
        or article.get("created_at")
    )
    if published:
        fm.append(f"publishedDate: {published}")
    if article.get("author"):
        fm.append(f"author: {json.dumps(article['author'])}")
    tags = article.get("keywords") or []
    if tags:
        fm.append("tags:")
        fm.extend(f"  - {json.dumps(t)}" for t in tags)
    is_draft = article.get("status") not in _PUBLISHABLE_STATUSES
    fm.append(f"draft: {'true' if is_draft else 'false'}")
    fm.append("---")
    body = _strip_leading_h1(article.get("content_md") or "")
    return "\n".join(fm) + "\n\n" + body


# --- publishing ---
def validate_webhook_url(url: str) -> None:
    """SSRF guard: only http(s) to a public host. Rejects loopback/private/
    link-local/reserved addresses so a webhook can't reach internal services or a
    cloud metadata endpoint. (DNS-rebinding is a residual window; redirects are
    disabled by the caller.)"""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("webhook URL must be http or https")
    host = (p.hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal")):
        raise ValueError("webhook URL has no host or targets an internal name")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
    except OSError as e:
        raise ValueError("webhook host does not resolve") from e
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError as e:
            raise ValueError("webhook host has an unclassifiable address") from e
        # is_global is False for every private/loopback/link-local/reserved/CGNAT
        # (100.64/10)/IPv4-mapped range — broader than enumerating them by hand, and
        # matches brand_materials._is_public_host so the two SSRF guards can't drift.
        if not ip.is_global:
            raise ValueError("webhook URL resolves to a blocked address")


def list_publications(db: Database, article_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_PUBLICATION_COLUMNS} from public.publications "
        "where article_id = %s order by created_at desc",
        (article_id,),
    )


def _record(
    db: Database,
    article_id: UUID,
    target_type: str,
    *,
    status: str,
    url: str | None = None,
    external_id: str | None = None,
) -> dict[str, Any]:
    return db.fetch_one(
        "insert into public.publications "
        "(article_id, target_type, status, url, external_id, published_at) "
        "values (%s, %s, %s, %s, %s, case when %s = 'success' then now() end) "
        f"returning {_PUBLICATION_COLUMNS}",
        (article_id, target_type, status, url, external_id, status),
    )


async def publish(
    db: Database,
    article_id: UUID,
    *,
    target_type: str,
    config: dict[str, Any] | None = None,
    public_base_url: str | None = None,
) -> dict[str, Any] | None:
    """Publish an article. 'export' just marks it published + crawlable; 'webhook'
    POSTs the payload to config.url. The webhook is SSRF-validated AND delivered BEFORE
    status is flipped to 'published', so neither an invalid URL nor a failed delivery
    can leave the article live at /p/{id} while recording 'failed'."""
    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    config = config or {}

    # Resolve where the article will live — its canonical_url override or the brand's
    # url_pattern — falling back to RankForge's own SSR page. Independent of status, so
    # it's safe to compute before publishing. (`linking` is module-level; importing
    # `business_profiles` locally avoids an import cycle.)
    from . import business_profiles as brands_svc

    brand = (
        brands_svc.get_profile(db, article["business_id"])
        if article.get("business_id")
        else None
    )
    public_url = linking.canonical_url(brand, article) or (
        f"{public_base_url.rstrip('/')}/p/{article_id}" if public_base_url else None
    )

    def _go_live() -> None:
        # The public page renders fresh from content_md at read time, so there's no
        # stale cached HTML to invalidate.
        db.execute(
            "update public.articles set status = 'published', updated_at = now() "
            "where id = %s",
            (article_id,),
        )

    if target_type == "webhook":
        url = (config.get("url") or "").strip()
        # Validate (SSRF guard), then DELIVER, and only flip to 'published' once delivery
        # succeeds — so a bad/SSRF URL or a failed POST records 'failed' without ever
        # making the content public.
        try:
            validate_webhook_url(url)
        except ValueError:
            return _record(db, article_id, "webhook", status="failed", url=url or None)
        resolved_md = linking.strip_competitor_links(
            linking.resolve_links(
                db,
                article["business_id"],
                article.get("content_md") or "",
                fallback_base=public_base_url,
            ),
            linking.competitor_hosts(brand),
        )
        payload = {
            "id": str(article_id),
            "title": article.get("title"),
            "slug": article.get("slug"),
            "meta_title": article.get("meta_title"),
            "meta_description": article.get("meta_description"),
            "content_md": resolved_md,
            "content_html": render_body_html(resolved_md),
            "json_ld": article.get("json_ld"),
            "public_url": public_url,
        }
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        except Exception:  # noqa: BLE001 — record the failure, don't publish
            return _record(db, article_id, "webhook", status="failed", url=url)
        _go_live()
        return _record(db, article_id, "webhook", status="success", url=url)

    # 'export' (and default): just mark it published + crawlable at its public URL.
    _go_live()
    return _record(db, article_id, "export", status="success", url=public_url)


def unpublish(db: Database, article_id: UUID) -> dict[str, Any] | None:
    """Revert a published article to draft — for when it's been taken down from the blog.

    Cluster MEMBERSHIP is retained (cluster_id/cluster_role kept) so a later republish
    rejoins the same cluster instead of silently dropping out. If it was the cluster's
    authority PILLAR, only the cluster's pillar slot is vacated (so the slot can be
    reclaimed) — the article stays a member of the cluster. All changes, plus an
    'unpublished' audit row, commit together. Returns the updated article, or None if
    it doesn't exist."""
    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    with db.connection() as conn:
        # A pillar coming off the blog vacates the cluster's pillar slot (so a new
        # pillar can be designated) but keeps its own cluster membership — demote it
        # to a member rather than detaching, so republish doesn't lose the cluster.
        if article.get("cluster_role") == "pillar" and article.get("cluster_id"):
            conn.execute(
                "update public.content_clusters set pillar_article_id = null, "
                "pillar_locked = false where id = %s and pillar_article_id = %s",
                (article["cluster_id"], article_id),
            )
            conn.execute(
                "update public.articles set status = 'draft', cluster_role = 'member', "
                "updated_at = now() where id = %s",
                (article_id,),
            )
        else:
            conn.execute(
                "update public.articles set status = 'draft', updated_at = now() "
                "where id = %s",
                (article_id,),
            )
        # Audit-trail symmetry with publish()'s _record() — log the takedown.
        conn.execute(
            "insert into public.publications "
            "(article_id, target_type, status, url, external_id, published_at) "
            "values (%s, 'unpublish', 'unpublished', null, null, null)",
            (article_id,),
        )
    return gen_svc.get_article(db, article_id)


# --- public read (no auth) ---
def get_published(db: Database, article_id: UUID) -> dict[str, Any] | None:
    """A published article for the public SSR page (None unless published).

    Returns content_md; the route renders + sanitizes it fresh, so the public page
    never serves stale HTML and sanitization is guaranteed at render time."""
    return db.fetch_one(
        "select id, business_id, title, slug, meta_title, meta_description, "
        "content_md, json_ld, canonical_url, og_image_url, author, updated_at "
        "from public.articles where id = %s and status = 'published'",
        (article_id,),
    )


# Markdown noise to strip when deriving a plain-text social description from the body.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")  # keep the anchor text, drop the URL
_MD_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
# Strip structural markers at line start (headings, list bullets, blockquotes) and
# inline emphasis/code marks anywhere — but NOT '_' or a mid-word '#', so identifiers
# like snake_case and C# survive in the excerpt.
_MD_INLINE_RE = re.compile(
    r"^\s{0,3}#{1,6}\s+|^\s*[-*+]\s+|^\s*>\s?|[`*~]",
    re.MULTILINE,
)
_WS_RE = re.compile(r"\s+")


def _excerpt(content_md: str, limit: int = 155) -> str:
    """A plain-text, single-line excerpt from a Markdown body — used as the social /
    meta description fallback when meta_description is empty. Strips the leading '# H1'
    (the title, rendered separately), code fences, images, and inline formatting, then
    truncates on a word boundary with an ellipsis."""
    text = _strip_leading_h1(content_md or "")
    text = _MD_FENCE_RE.sub(" ", text)
    text = _MD_IMAGE_RE.sub(" ", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_INLINE_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:—- ")
    return f"{clipped}…"


def public_article_view(db: Database, row: dict[str, Any]) -> dict[str, Any]:
    """Enrich a published-article row into the public SSR payload: sanitized HTML (refs
    resolved), a guaranteed description, the live canonical URL, a resolved byline, and
    the first-published date — everything the page's OG/Twitter/canonical metadata needs."""
    from . import business_profiles as brands_svc

    business_id = row.get("business_id")
    brand = brands_svc.get_profile(db, business_id) if business_id else None
    resolved = linking.resolve_links(db, business_id, row.get("content_md") or "")
    # Guaranteed non-empty: meta_description → a body excerpt → the title. The final
    # title fallback matters when the body is only an H1 / images / code, so the SSR
    # page never ships an empty <meta description>/og:description.
    description = (
        (row.get("meta_description") or "").strip()
        or _excerpt(row.get("content_md") or "")
        or row.get("title")
    )
    brand_name = (brand or {}).get("name")
    author = (
        row.get("author")
        or (brand or {}).get("default_author")
        or (f"{brand_name} Team" if brand_name else None)
    )
    first = db.fetch_one(
        "select min(published_at) as first from public.publications "
        "where article_id = %s and published_at is not null",
        (row["id"],),
    )
    published_at = (first or {}).get("first") or row.get("updated_at")
    return {
        **row,
        "content_html": render_body_html(resolved),
        "description": description,  # non-empty (title is the final fallback)
        "canonical_url": linking.canonical_url(brand, row),
        "author": author,
        "published_at": published_at,
    }


def _export_published_date(db: Database, article_id: UUID, status: str | None) -> str:
    """The date to stamp on the export. For a PUBLISHED article, the first successful
    publication's date (stable across re-exports); otherwise today (first export)."""
    if status == "published":
        first = db.fetch_one(
            "select min(published_at) as first from public.publications "
            "where article_id = %s and published_at is not null",
            (article_id,),
        )
        if first and first.get("first"):
            return _fm_date(first["first"])
    return datetime.now(UTC).date().isoformat()


def export(db: Database, article_id: UUID, fmt: str) -> tuple[str, str] | None:
    """Return (content, media_type) for a download, or None if the article is gone.
    Internal-link refs are resolved to live canonical URLs in the exported file."""
    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    # Enrich for the frontmatter: keywords → tags (not in the default article select),
    # and an author byline derived from the brand name. Local import avoids a cycle.
    from . import business_profiles as brands_svc

    brand = (
        brands_svc.get_profile(db, article["business_id"])
        if article.get("business_id")
        else None
    )
    kw_row = db.fetch_one(
        "select keywords from public.articles where id = %s", (article_id,)
    )
    brand_name = (brand or {}).get("name")
    # Author resolves: per-article override → brand default → "<Brand> Team" fallback.
    author = (
        article.get("author")
        or (brand or {}).get("default_author")
        or (f"{brand_name} Team" if brand_name else None)
    )
    article = {
        **article,
        "content_md": linking.strip_competitor_links(
            linking.resolve_links(
                db, article["business_id"], article.get("content_md") or ""
            ),
            linking.competitor_hosts(brand),
        ),
        "keywords": (kw_row or {}).get("keywords") or [],
        "author": author,
        # publishedDate: for a live post, keep it STABLE across re-exports — use the first
        # successful publication's date so re-exporting to update a post doesn't churn its
        # date. Only a not-yet-published article (first export) defaults to today.
        "published_date": _export_published_date(db, article_id, article.get("status")),
    }
    if fmt == "markdown":
        return render_markdown(article), "text/markdown"
    if fmt == "html":
        return render_standalone_html(article), "text/html"
    return None
