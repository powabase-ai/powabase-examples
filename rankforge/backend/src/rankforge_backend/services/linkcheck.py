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
import ipaddress
import logging
import re
import socket
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx

from ..db import Database
from ..powabase import PowabaseClient
from . import generation as gen_svc
from .agents import ensure_agent

log = logging.getLogger("rankforge.linkcheck")

# Surgical, paragraph-scoped copy editor for removing a broken link and mending the
# sentence. A small fast model is plenty — it edits one paragraph, not the article.
_LINK_EDITOR_AGENT = "rankforge-link-editor"
_LINK_EDITOR_MODEL = "claude-sonnet-4-6"
_LINK_EDITOR_SYSTEM = """\
You are a careful copy editor. A markdown link in the given paragraph is broken and \
must be removed. Remove that link AND its anchor words, then repair the text so it \
reads naturally and grammatically — as if the link had never been there. Do NOT add a \
replacement link, a new URL, new facts, or any commentary. Preserve every OTHER link, \
citation, number, name, and the original meaning and roughly the length. Output ONLY \
the revised paragraph in Markdown — no preamble, no code fences."""

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


def _host_fetch_state(host: str) -> str:
    """Classify an external host for link checking:
      'fetch' — resolves to a public IP: go verify the URL.
      'dead'  — the hostname does NOT resolve (NXDOMAIN / no address): a broken link.
                This is the common hallucination — a fabricated host like a made-up docs
                subdomain — which the old "skip non-public" rule silently passed.
      'skip'  — internal/private/loopback or an internal-only name, OR a transient
                resolver failure (EAI_AGAIN): don't fetch (SSRF) and don't flag — we
                genuinely can't reach/judge it, and a DNS hiccup isn't a broken link."""
    host = (host or "").strip().lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal")):
        return "skip"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        # NXDOMAIN / no-address → the host genuinely doesn't exist (a broken link).
        # A transient resolver failure (EAI_AGAIN and friends) is NOT evidence the
        # link is dead — skip it rather than flag a healthy link off a DNS hiccup.
        return (
            "dead"
            if e.errno in (socket.EAI_NONAME, socket.EAI_NODATA)
            else "skip"
        )
    except OSError:
        return "skip"  # any other resolver error → can't judge, don't flag
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return "skip"
        if not ip.is_global:  # private / loopback / link-local / CGNAT
            return "skip"
    return "fetch"


async def _external_reason(
    client: httpx.AsyncClient, url: str
) -> tuple[int | None, str | None]:
    """(http_status, reason). reason None = healthy. A host that doesn't resolve is a
    broken link; private/internal hosts are skipped (healthy) to avoid SSRF and false
    positives on hosts we can't reach."""
    state = _host_fetch_state(urlparse(url).hostname or "")
    if state == "dead":
        return None, "host does not resolve"
    if state == "skip":
        return None, None
    try:
        resp = await client.head(url)
        if resp.status_code in (403, 405, 501):  # some servers reject HEAD
            resp = await client.get(url)
        # redirects are not followed → 3xx means the link resolves (healthy).
        # Only a definitively-GONE resource is a broken link: 404/410. Any other
        # response means the host resolved AND the server answered — a 401/403/429
        # (auth wall / bot-block / rate-limit) or a transient 5xx is NOT evidence the
        # page is fabricated or dead, and flagging it just cries wolf on real links
        # (e.g. npmjs.com / nvd.nist.gov 403 their HEAD+GET to non-browser clients).
        if resp.status_code in (404, 410):
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


async def ensure_link_editor_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=_LINK_EDITOR_AGENT,
        model=_LINK_EDITOR_MODEL,
        system_prompt=_LINK_EDITOR_SYSTEM,
        settings={"temperature": 0.2},
    )


def _mechanical_strip(text: str, link_re: "re.Pattern[str]") -> str:
    """Delete the link markup and tidy the spacing — the no-LLM fallback."""
    out = link_re.sub("", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r" +([.,;:!?])", r"\1", out)


async def _rephrase_block(
    client: PowabaseClient, agent_id: str, block: str, url: str
) -> str:
    """LLM-rewrite one paragraph to drop the broken link and mend the sentence."""
    from . import linking  # local: avoid import cycle

    # Mask OTHER internal-link refs so the rewrite can't mangle their UUIDs — unless the
    # link we're removing IS a ref (then it must stay visible for the model to remove).
    is_ref = url.startswith(("rf:article/", "/p/"))
    masked, refmap = (block, {}) if is_ref else linking.mask_refs(block)
    msg = (
        "Remove the broken markdown link from the paragraph below — both the link and "
        "its anchor words — and repair the text so it reads naturally, as if the link "
        "had never been there. Do not add a replacement link or new facts.\n\n"
        f"Broken link to remove (this exact URL): {url}\n\n"
        "Output ONLY the revised paragraph in Markdown.\n\n"
        f"---PARAGRAPH---\n{masked}"
    )
    res = await client.run_agent(agent_id, msg)
    out = (res.get("content") or "").strip()
    if out.startswith("```"):  # strip an accidental code fence
        out = re.sub(r"^```[a-z]*\n?|\n?```$", "", out).strip()
    if not out:
        raise RuntimeError("empty rephrase")
    return linking.restore_refs(out, refmap)


async def _excise_link(
    client: PowabaseClient, md: str, url: str, link_re: "re.Pattern[str]"
) -> tuple[str, int]:
    """Remove every `[text](url)` for `url`, repairing each affected paragraph with an
    LLM so the prose still flows. Per-paragraph fallback to a mechanical strip if the
    model is unavailable or leaves the link in. Returns (new_md, occurrences_removed)."""
    blocks = md.split("\n\n")
    total = 0
    agent_id: str | None = None
    for i, block in enumerate(blocks):
        if not link_re.search(block):
            continue
        total += len(link_re.findall(block))
        rewritten: str | None = None
        try:
            if agent_id is None:
                agent_id = await ensure_link_editor_agent(client)
            rewritten = await _rephrase_block(client, agent_id, block, url)
        except Exception:  # noqa: BLE001 — never wedge on a model/parse failure
            log.exception("link-removal rephrase failed; using mechanical strip")
        # Accept the rewrite only if it actually dropped the link; else mechanical strip.
        blocks[i] = (
            rewritten
            if rewritten and not link_re.search(rewritten)
            else _mechanical_strip(block, link_re)
        )
    return "\n\n".join(blocks), total


async def remove_link(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    article_id: UUID,
    finding_id: UUID,
    *,
    keep_text: bool,
) -> dict[str, Any] | None:
    """One-click remedy for a broken link. `keep_text=True` UNLINKS instantly (keeps the
    anchor words, drops the URL). `False` REMOVES the link and uses an LLM to excise the
    phrase and repair the sentence so it reads naturally. The edit is versioned (undoable
    via history) and the finding is closed. Returns the updated article, or None if the
    finding doesn't belong to this article/brand."""
    finding = db.fetch_one(
        f"select {_COLUMNS} from public.link_health "
        "where id = %s and business_id = %s and article_id = %s",
        (finding_id, business_id, article_id),
    )
    if finding is None:
        return None
    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    url = finding["url"]
    md = article.get("content_md") or ""
    # Match every [text](url) for this EXACT url (tolerating <...>/surrounding spaces,
    # exactly as _extract_links found it). re.escape so the url can't act as a pattern.
    link_re = re.compile(r"\[([^\]]*)\]\(\s*<?" + re.escape(url) + r">?\s*\)")
    if keep_text:
        new_md, n = link_re.subn(r"\1", md)  # instant unlink — keep the words
    else:
        new_md, n = await _excise_link(client, md, url, link_re)  # LLM rephrase
    if n:
        gen_svc.update_article(db, article_id, {"content_md": new_md})
        # The link was actually removed → close the finding.
        db.execute(
            "update public.link_health set status = 'resolved', updated_at = now() "
            "where id = %s and business_id = %s",
            (finding_id, business_id),
        )
    else:
        # The URL isn't in the body (a stale finding, an out-of-band edit, or a URL-form
        # mismatch after ref resolution). Do NOT close it — marking a still-broken link
        # 'resolved' would drop it from the flagged list without fixing anything. Leave it
        # open so it stays visible, and log the desync so it's discoverable.
        log.warning(
            "remove_link: no occurrence of %s in article %s — finding %s left open",
            url, article_id, finding_id,
        )
    return gen_svc.get_article(db, article_id)


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
