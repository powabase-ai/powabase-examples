"""M6 / Phase 12.1 — internal linking between the brand's own published articles.

Drafts already link to brand MATERIALS at write time (generation). This adds
cross-article INTERNAL linking: scan an article's body for verbatim, currently
UNLINKED mentions of another published article's keyword/title, and stage a
suggestion to link that span to it.

Deterministic on purpose — no LLM. That keeps it fast, explainable, free, and able
to run across the WHOLE library for the monthly re-linking scout without per-article
model cost. Suggestions are staged (never auto-applied to published content);
accepting one inserts the markdown link and re-scores the article.
"""

import re
from typing import Any
from uuid import UUID

from ..db import Database
from . import generation as gen_svc

_COLUMNS = (
    "id, business_id, article_id, target_article_id, anchor_text, target_url, "
    "target_title, reason, status, created_at"
)

# Cap links added per source article so a draft doesn't turn into a link farm; one
# anchor per target so we never link to the same page twice.
_MAX_PER_ARTICLE = 5
# Anchors shorter than this are too generic to be useful (and risk odd matches).
_MIN_ANCHOR_LEN = 4


def _public_url(article_id: Any) -> str:
    """Internal link target — the brand's public SSR page (served at /p/{id})."""
    return f"/p/{article_id}"


def _link_targets(
    db: Database, business_id: UUID, exclude_id: UUID
) -> list[dict[str, Any]]:
    """The brand's OTHER published articles — the candidate link targets."""
    return db.fetch_all(
        "select id, title, slug, keywords from public.articles "
        "where business_id = %s and status = 'published' and id <> %s",
        (business_id, exclude_id),
    )


def _anchor_candidates(target: dict[str, Any]) -> list[str]:
    """Phrases worth linking on for one target: its keywords (the SEO terms) plus the
    title as a fallback. Longest first, so a specific multi-word phrase wins over a
    generic single word."""
    cands: list[str] = []
    for k in target.get("keywords") or []:
        k = (str(k) if k is not None else "").strip()
        if len(k) >= _MIN_ANCHOR_LEN:
            cands.append(k)
    title = (target.get("title") or "").strip()
    if len(title) >= 8:
        cands.append(title)
    # de-dupe (case-insensitive) preserving the first spelling; longest first
    seen: set[str] = set()
    uniq: list[str] = []
    for c in cands:
        low = c.lower()
        if low not in seen:
            seen.add(low)
            uniq.append(c)
    return sorted(uniq, key=len, reverse=True)


# Regions of the markdown we must NOT touch: existing links (no nesting), code, and
# heading lines (linking a heading is wrong). We build a per-char "linkable" mask once.
_FENCED = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`[^`]*`")
_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_HEADING = re.compile(r"(?m)^[ \t]*#{1,6} .*$")


def _linkable_mask(md: str) -> bytearray:
    """1 where a character may become part of a new link; 0 inside code/links/headings."""
    mask = bytearray(b"\x01" * len(md))
    for rx in (_FENCED, _INLINE_CODE, _MD_LINK, _HEADING):
        for m in rx.finditer(md):
            for i in range(m.start(), m.end()):
                mask[i] = 0
    return mask


def _find_anchor(
    md: str, mask: bytearray, anchor: str
) -> tuple[tuple[int, int], str] | None:
    """First whole-word, case-insensitive occurrence of `anchor` that lies entirely in
    a linkable region. Returns ((start, end), the verbatim matched text) or None."""
    pat = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(anchor)}(?![A-Za-z0-9])", re.IGNORECASE
    )
    for m in pat.finditer(md):
        if all(mask[i] for i in range(m.start(), m.end())):
            return (m.start(), m.end()), md[m.start():m.end()]
    return None


def list_suggestions(
    db: Database, article_id: UUID, status: str = "pending"
) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_COLUMNS} from public.link_suggestions "
        "where article_id = %s and status = %s order by created_at",
        (article_id, status),
    )


def _set_status(
    db: Database, business_id: UUID, suggestion_id: UUID, status: str
) -> dict[str, Any] | None:
    return db.fetch_one(
        f"update public.link_suggestions set status = %s, updated_at = now() "
        f"where id = %s and business_id = %s returning {_COLUMNS}",
        (status, suggestion_id, business_id),
    )


def dismiss_suggestion(
    db: Database, business_id: UUID, suggestion_id: UUID
) -> dict[str, Any] | None:
    return _set_status(db, business_id, suggestion_id, "dismissed")


def suggest_links(
    db: Database, business_id: UUID, article_id: UUID
) -> list[dict[str, Any]]:
    """Find unlinked mentions of the brand's OTHER published articles in this article
    and stage them as pending suggestions. Idempotent: re-running won't duplicate a
    suggestion or resurface a dismissed/accepted one (unique index + on-conflict)."""
    art = gen_svc.get_article(db, article_id)
    if not art:
        return []
    md = art.get("content_md") or ""
    mask = _linkable_mask(md)
    chosen: list[tuple[int, int]] = []  # spans already claimed (avoid overlaps)
    out: list[dict[str, Any]] = []
    for t in _link_targets(db, business_id, article_id):
        if len(out) >= _MAX_PER_ARTICLE:
            break
        for anchor in _anchor_candidates(t):
            found = _find_anchor(md, mask, anchor)
            if not found:
                continue
            (a, b), span_text = found
            if any(a < e and s < b for s, e in chosen):  # overlaps a claimed span
                continue
            chosen.append((a, b))
            row = db.fetch_one(
                "insert into public.link_suggestions "
                "(business_id, article_id, target_article_id, anchor_text, "
                " target_url, target_title, reason) "
                "values (%s, %s, %s, %s, %s, %s, %s) "
                f"on conflict do nothing returning {_COLUMNS}",
                (
                    business_id, article_id, t["id"], span_text,
                    _public_url(t["id"]), t.get("title"),
                    f'Mentions "{span_text}" — links to your article '
                    f'"{t.get("title") or t["id"]}".',
                ),
            )
            if row:  # None when a dismissed/accepted suggestion already exists
                out.append(row)
            break  # at most one anchor per target
    return out


async def apply_suggestion(
    client: Any, db: Database, business_id: UUID, suggestion_id: UUID
) -> dict[str, Any] | None:
    """Insert the link into the article body, re-score, mark the suggestion accepted.

    Returns the updated suggestion. If the anchor no longer exists (the article was
    edited since), the suggestion is stale — dismiss it and return that.
    """
    s = db.fetch_one(
        "select id, article_id, anchor_text, target_url, status "
        "from public.link_suggestions where id = %s and business_id = %s",
        (suggestion_id, business_id),
    )
    if s is None or s["status"] != "pending":
        return None
    art = gen_svc.get_article(db, s["article_id"])
    if not art:
        return None
    md = art.get("content_md") or ""
    found = _find_anchor(md, _linkable_mask(md), s["anchor_text"])
    if not found:
        return _set_status(db, business_id, suggestion_id, "dismissed")
    (a, b), span_text = found
    new_md = f"{md[:a]}[{span_text}]({s['target_url']}){md[b:]}"
    gen_svc._update(db, s["article_id"], content_md=new_md)
    # A link changes the on-page signals (outbound/internal links) — re-score so the
    # editor sees the effect. Local import avoids a service import cycle.
    from . import scoring

    await scoring.score_and_store(client, db, s["article_id"])
    return _set_status(db, business_id, suggestion_id, "accepted")
