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
import socket
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
import markdown as md
import nh3

from ..db import Database
from . import generation as gen_svc

_MD_EXTENSIONS = ["extra", "sane_lists", "toc"]

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
    body = render_body_html(article.get("content_md") or "")
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


def render_markdown(article: dict[str, Any]) -> str:
    """Portable Markdown with YAML front matter (title/description/keywords)."""
    fm_lines = ["---", f"title: {json.dumps(article.get('title') or '')}"]
    if article.get("meta_description"):
        fm_lines.append(f"description: {json.dumps(article['meta_description'])}")
    kws = article.get("keywords") or []
    if kws:
        fm_lines.append(f"keywords: {json.dumps(kws)}")
    fm_lines.append("---\n")
    return "\n".join(fm_lines) + (article.get("content_md") or "")


# --- publishing ---
def validate_webhook_url(url: str) -> None:
    """SSRF guard: only http(s) to a public host. Rejects loopback/private/
    link-local/reserved addresses so a webhook can't reach internal services or a
    cloud metadata endpoint. (DNS-rebinding is a residual window; redirects are
    disabled by the caller.)"""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("webhook URL must be http or https")
    host = p.hostname
    if not host:
        raise ValueError("webhook URL has no host")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
    except OSError as e:
        raise ValueError("webhook host does not resolve") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
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
    POSTs the payload to config.url. Caches rendered HTML and sets status=published."""
    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    config = config or {}

    # Flip to published. The public page renders fresh from content_md at read time,
    # so there's no stale cached HTML to keep in sync.
    db.execute(
        "update public.articles set status = 'published', updated_at = now() "
        "where id = %s",
        (article_id,),
    )

    # Prefer where the article actually lives — its canonical_url override or the
    # brand's url_pattern — falling back to RankForge's own SSR page.
    from . import business_profiles as brands_svc
    from . import linking

    brand = (
        brands_svc.get_profile(db, article["business_id"])
        if article.get("business_id")
        else None
    )
    public_url = linking.canonical_url(brand, article) or (
        f"{public_base_url.rstrip('/')}/p/{article_id}" if public_base_url else None
    )

    if target_type == "webhook":
        url = (config.get("url") or "").strip()
        try:
            validate_webhook_url(url)
        except ValueError:
            return _record(db, article_id, "webhook", status="failed", url=url or None)
        payload = {
            "id": str(article_id),
            "title": article.get("title"),
            "slug": article.get("slug"),
            "meta_title": article.get("meta_title"),
            "meta_description": article.get("meta_description"),
            "content_md": article.get("content_md"),
            "content_html": render_body_html(article.get("content_md") or ""),
            "json_ld": article.get("json_ld"),
            "public_url": public_url,
        }
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            return _record(db, article_id, "webhook", status="success", url=url)
        except Exception:  # noqa: BLE001 — record the failure, don't crash
            return _record(db, article_id, "webhook", status="failed", url=url)

    # 'export' (and default): the article is now published + crawlable at its public URL.
    return _record(db, article_id, "export", status="success", url=public_url)


# --- public read (no auth) ---
def get_published(db: Database, article_id: UUID) -> dict[str, Any] | None:
    """A published article for the public SSR page (None unless published).

    Returns content_md; the route renders + sanitizes it fresh, so the public page
    never serves stale HTML and sanitization is guaranteed at render time."""
    return db.fetch_one(
        "select id, title, slug, meta_title, meta_description, content_md, "
        "json_ld, updated_at from public.articles "
        "where id = %s and status = 'published'",
        (article_id,),
    )


def export(db: Database, article_id: UUID, fmt: str) -> tuple[str, str] | None:
    """Return (content, media_type) for a download, or None if the article is gone."""
    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    if fmt == "markdown":
        return render_markdown(article), "text/markdown"
    if fmt == "html":
        return render_standalone_html(article), "text/html"
    return None
