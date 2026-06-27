"""M6 — internal linking between the brand's own published articles, cluster-aware.

Two layers:
- **Structural cluster links** (the priority): a member article links UP to its
  cluster's pillar; a pillar links DOWN to its members. When the prose has no natural
  anchor for a structural link, we stage a GAP that an editor fills with an opt-in,
  LLM-written contextual sentence.
- **Mention-based links**: scan the body for verbatim, unlinked mentions of any other
  published article's keyword/title (deterministic, cross-cluster).

The mention layer is deterministic — fast, explainable, free, and safe to run across
the whole library in the monthly re-linking scout. Suggestions are staged (never
auto-applied to published content); accepting one inserts the link and re-scores.
"""

import re
from typing import Any
from uuid import UUID

from ..db import Database
from . import business_profiles as brands
from . import generation as gen_svc
from .agents import ensure_agent

_COLUMNS = (
    "id, business_id, article_id, target_article_id, anchor_text, target_url, "
    "target_title, reason, kind, status, created_at"
)
# How a suggestion relates to the cluster: a member's up-link, a pillar's down-link,
# or an incidental cross-article mention.
_PILLAR, _MEMBER, _MENTION = "pillar", "member", "mention"

# Cap links added per source article so a draft doesn't turn into a link farm; one
# anchor per target so we never link to the same page twice.
_MAX_PER_ARTICLE = 5
# Anchors shorter than this are too generic to be useful (and risk odd matches).
_MIN_ANCHOR_LEN = 4


def _render_pattern(pattern: str, article: dict[str, Any]) -> str | None:
    """Render a brand URL pattern for one article. Tokens: {slug}, {id}. Returns None
    when the pattern can't yield a per-article URL: it has no token at all (would map
    every article to the SAME url), or it needs a slug the article doesn't have (would
    emit an empty path segment)."""
    if "{slug}" not in pattern and "{id}" not in pattern:
        return None
    slug = (article.get("slug") or "").strip()
    if "{slug}" in pattern and not slug:
        return None
    return (
        pattern.replace("{slug}", slug).replace("{id}", str(article.get("id") or ""))
    )


def canonical_url(brand: dict[str, Any] | None, article: dict[str, Any]) -> str | None:
    """Where this article actually lives, or None if undeterminable.

    Resolution: the article's explicit canonical_url override → the brand's url_pattern
    rendered with the article's tokens → None (we REQUIRE a pattern, so there is no
    /p/{id} fallback for internal-link targets)."""
    override = (article.get("canonical_url") or "").strip()
    if override:
        return override
    pattern = (brand or {}).get("url_pattern")
    return _render_pattern(pattern, article) if pattern else None


_TARGET_COLS = "id, title, slug, keywords, canonical_url"


# --- stable internal-link references (resolved to live URLs only at render time) ---
# Internal links are stored in the body as `rf:article/{id}`, NOT as a baked URL, so
# editing a target's slug/url_pattern later updates EVERY citing link for free — the
# ref is resolved to the target's current canonical URL at each export/render boundary.
_LINK_REF_RE = re.compile(r"rf:article/([0-9a-fA-F-]{36})")


def link_ref(target_id: Any) -> str:
    return f"rf:article/{target_id}"


def mask_refs(md: str) -> tuple[str, dict[str, str]]:
    """Replace internal-link refs with opaque sentinels (`rfref:N`) so an LLM full-body
    rewrite can't 'fix' or mangle the UUIDs (or drop the link while reformatting the
    URL). Returns (masked_md, sentinel→ref). Pair with restore_refs around any LLM pass
    over the body (see revise.py)."""
    mapping: dict[str, str] = {}

    def _repl(m: "re.Match[str]") -> str:
        token = f"rfref:{len(mapping)}"
        mapping[token] = m.group(0)
        return token

    return _LINK_REF_RE.sub(_repl, md), mapping


def restore_refs(md: str, mapping: dict[str, str]) -> str:
    """Put the real `rf:article/{id}` refs back after an LLM pass (see mask_refs).

    Restore longest sentinel first: `rfref:1` is a textual prefix of `rfref:10`, so a
    naive insertion-order pass would rewrite the `rfref:1` inside `rfref:10` and corrupt
    the link (silently breaks every article with ≥11 internal links)."""
    for token, ref in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        md = md.replace(token, ref)
    return md


def resolve_links(
    db: Database, business_id: UUID, md: str, *, fallback_base: str | None = None
) -> str:
    """Replace internal-link refs (`rf:article/{id}`) with each target's LIVE canonical
    URL. The one place stored references become real URLs — every export/render path
    runs the body through here. A target that's gone, or whose canonical URL can't be
    resolved (pattern removed), falls back to its RankForge preview path."""
    if not md or "rf:article/" not in md:
        return md
    ids = set(_LINK_REF_RE.findall(md))
    if not ids:
        return md
    brand = brands.get_profile(db, business_id)
    # Scope to THIS business: canonical_url is built from this brand's profile, so a ref
    # to another tenant's article id must NOT resolve to a wrong-brand URL — it falls
    # through to the /p/{id} preview instead (defense-in-depth tenant isolation).
    rows = db.fetch_all(
        f"select {_TARGET_COLS} from public.articles "
        "where id::text = any(%s) and business_id = %s",
        (list(ids), business_id),
    )
    by_id = {str(r["id"]): r for r in rows}

    def _url(ref_id: str) -> str:
        target = by_id.get(ref_id)
        if target is not None and (u := canonical_url(brand, target)):
            return u
        base = (fallback_base or "").rstrip("/")
        return f"{base}/p/{ref_id}" if base else f"/p/{ref_id}"

    return _LINK_REF_RE.sub(lambda m: _url(m.group(1)), md)


def _link_targets(
    db: Database, business_id: UUID, exclude_id: UUID
) -> list[dict[str, Any]]:
    """The brand's OTHER published articles — the candidate link targets."""
    return db.fetch_all(
        f"select {_TARGET_COLS} from public.articles "
        "where business_id = %s and status = 'published' and id <> %s",
        (business_id, exclude_id),
    )


def _published(db: Database, article_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_TARGET_COLS} from public.articles "
        "where id = %s and status = 'published'",
        (article_id,),
    )


def _structural_targets(
    db: Database, art: dict[str, Any]
) -> list[tuple[dict[str, Any], str]]:
    """The cluster's structural link targets for `art`, each as (target, kind):
    its pillar (kind='pillar', an up-link) if `art` is a member, or its published
    members (kind='member', down-links) if `art` is the pillar."""
    cid, role = art.get("cluster_id"), art.get("cluster_role")
    if not cid:
        return []
    if role == _MEMBER:
        cl = db.fetch_one(
            "select pillar_article_id from public.content_clusters where id = %s",
            (cid,),
        )
        pid = (cl or {}).get("pillar_article_id")
        if pid and pid != art["id"] and (p := _published(db, pid)):
            return [(p, _PILLAR)]
        return []
    if role == _PILLAR:
        members = db.fetch_all(
            f"select {_TARGET_COLS} from public.articles "
            "where cluster_id = %s and cluster_role = 'member' "
            "and status = 'published' and id <> %s order by created_at",
            (cid, art["id"]),
        )
        return [(m, _MEMBER) for m in members[:_MAX_PER_ARTICLE]]
    return []


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


def _first_anchor(
    md: str, mask: bytearray, target: dict[str, Any], chosen: list[tuple[int, int]]
) -> tuple[tuple[int, int], str] | None:
    """The first usable, non-overlapping anchor for `target` in the body, or None."""
    for anchor in _anchor_candidates(target):
        found = _find_anchor(md, mask, anchor)
        if not found:
            continue
        (a, b), span = found
        if any(a < e and s < b for s, e in chosen):
            continue
        return (a, b), span
    return None


def _insert_suggestion(
    db: Database,
    business_id: UUID,
    article_id: UUID,
    target: dict[str, Any],
    anchor: str | None,
    target_url: str,
    kind: str,
    reason: str,
) -> dict[str, Any] | None:
    """Stage one suggestion (anchor=None → a gap). None on conflict (already staged
    or dismissed/accepted)."""
    if anchor is not None:
        # A real anchored link supersedes any still-pending GAP to the same target
        # (the prose now mentions it), so the editor doesn't see both.
        db.execute(
            "delete from public.link_suggestions where article_id = %s "
            "and target_article_id = %s and anchor_text is null and status = 'pending'",
            (article_id, target["id"]),
        )
    return db.fetch_one(
        "insert into public.link_suggestions "
        "(business_id, article_id, target_article_id, anchor_text, target_url, "
        " target_title, reason, kind) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s) "
        f"on conflict do nothing returning {_COLUMNS}",
        (business_id, article_id, target["id"], anchor, target_url,
         target.get("title"), reason, kind),
    )


def suggest_links(
    db: Database, business_id: UUID, article_id: UUID
) -> list[dict[str, Any]]:
    """Stage internal-link suggestions, cluster-aware. Structural cluster links
    (member→pillar, pillar→members) come first — with a GAP staged when there's no
    natural anchor — then incidental cross-article mentions. Idempotent."""
    art = gen_svc.get_article(db, article_id)
    if not art:
        return []
    # We REQUIRE the brand to declare where its blog lives (a url_pattern) before
    # suggesting links — otherwise a link would point at a URL we can't know.
    brand = brands.get_profile(db, business_id)
    if not (brand and brand.get("url_pattern")):
        return []
    md = art.get("content_md") or ""
    mask = _linkable_mask(md)
    chosen: list[tuple[int, int]] = []  # spans already claimed (avoid overlaps)
    out: list[dict[str, Any]] = []
    done: set[Any] = set()

    def _consider(target: dict[str, Any], kind: str) -> None:
        if len(out) >= _MAX_PER_ARTICLE or target["id"] in done:
            return
        target_url = canonical_url(brand, target)
        if not target_url:
            return
        done.add(target["id"])
        anchor = _first_anchor(md, mask, target, chosen)
        title = target.get("title") or target["id"]
        if anchor:
            (a, b), span = anchor
            chosen.append((a, b))
            reason = f'Mentions "{span}" — links to "{title}".'
            row = _insert_suggestion(
                db, business_id, article_id, target, span, target_url, kind, reason
            )
        elif kind != _MENTION:
            # A structural link with no natural anchor → a gap to fill with an LLM link.
            verb = "link up to its pillar" if kind == _PILLAR else "link down to"
            reason = (
                f'This article should {verb} "{title}" but never mentions it — '
                "generate a contextual link."
            )
            row = _insert_suggestion(
                db, business_id, article_id, target, None, target_url, kind, reason
            )
        else:
            row = None
        if row:
            out.append(row)

    # 1) Structural cluster links first (they earn the gap treatment).
    for target, kind in _structural_targets(db, art):
        _consider(target, kind)
    # 2) Incidental cross-article mentions.
    for target in _link_targets(db, business_id, article_id):
        _consider(target, _MENTION)
    return out


def apply_suggestion(
    db: Database, business_id: UUID, suggestion_id: UUID
) -> dict[str, Any] | None:
    """Insert the link into the article body, re-score, mark the suggestion accepted.

    Returns the updated suggestion. If the anchor no longer exists (the article was
    edited since), the suggestion is stale — dismiss it and return that.
    """
    s = db.fetch_one(
        "select id, article_id, target_article_id, anchor_text, target_url, status "
        "from public.link_suggestions where id = %s and business_id = %s",
        (suggestion_id, business_id),
    )
    if s is None or s["status"] != "pending" or not s.get("anchor_text"):
        return None  # a gap (null anchor) has nothing to apply — use generate_gap_link
    if not s.get("target_article_id"):
        return None  # defensive: a null target would store a `rf:article/None` ref
    art = gen_svc.get_article(db, s["article_id"])
    if not art:
        return None
    md = art.get("content_md") or ""
    found = _find_anchor(md, _linkable_mask(md), s["anchor_text"])
    if not found:
        return _set_status(db, business_id, suggestion_id, "dismissed")
    (a, b), span_text = found
    # Store a stable REF, not the URL — so the link follows the target's slug forever.
    new_md = f"{md[:a]}[{span_text}]({link_ref(s['target_article_id'])}){md[b:]}"
    gen_svc._update(db, s["article_id"], content_md=new_md)
    # Re-score SEO DETERMINISTICALLY (no LLM): a single internal link only moves the
    # on-page link signals, so re-judging GEO/readability with the model on every
    # "Add" click would be pure latency + token cost. Local imports avoid a cycle.
    from . import brief as brief_svc
    from . import scoring

    brief = (
        brief_svc.get_brief(db, art["brief_id"]) if art.get("brief_id") else {}
    ) or {}
    # Score the RESOLVED body so link signals see real URLs, not the ref token.
    seo = scoring.score_seo(
        resolve_links(db, business_id, new_md),
        art.get("meta_title") or art.get("title") or "",
        art.get("meta_description"),
        brief,
    )
    gen_svc._update(db, s["article_id"], seo_score=seo)
    return _set_status(db, business_id, suggestion_id, "accepted")


# --- gap fill: an opt-in LLM-written contextual link for a structural gap ---
LINKER_AGENT_NAME = "rankforge-linker"
LINKER_MODEL = "claude-sonnet-4-6"
_LINKER_SYSTEM = """\
You are RankForge's internal-link writer. Given an article and ONE related article to \
link to, write a single short, natural sentence to weave into the article that links \
to the related piece. Use descriptive anchor text on a few words of the sentence — \
never "click here", never the bare URL, never marketing fluff. The sentence must read \
like it belongs in the article. Output ONLY that one Markdown sentence, containing \
exactly one [anchor](url) link.\
"""


async def _ensure_linker(client: Any) -> str:
    return await ensure_agent(
        client,
        name=LINKER_AGENT_NAME,
        model=LINKER_MODEL,
        system_prompt=_LINKER_SYSTEM,
        settings={"temperature": 0.3},
    )


def _insert_after_intro(md: str, sentence: str) -> str:
    """Place `sentence` as its own paragraph right after the article's intro (the first
    body paragraph, past any H1) — or append if there's no clear paragraph break."""
    m = re.match(r"(?s)^(#[^\n]*\n+)?.*?\n\n", md)
    if m:
        return f"{md[:m.end()]}{sentence.strip()}\n\n{md[m.end():]}"
    return f"{md.rstrip()}\n\n{sentence.strip()}\n"


async def generate_gap_link(
    client: Any, db: Database, business_id: UUID, suggestion_id: UUID
) -> dict[str, Any] | None:
    """Fill a structural GAP (a pending, anchor-less suggestion): an LLM writes one
    contextual sentence linking to the target, we insert it after the intro, re-score
    (SEO, deterministic), and mark accepted. None if it isn't a fillable gap or the
    model didn't produce a usable link."""
    s = db.fetch_one(
        "select id, article_id, target_article_id, anchor_text, target_url, "
        "target_title, status "
        "from public.link_suggestions where id = %s and business_id = %s",
        (suggestion_id, business_id),
    )
    if s is None or s["status"] != "pending" or s.get("anchor_text"):
        return None  # only pending gaps (null anchor)
    art = gen_svc.get_article(db, s["article_id"])
    if not art:
        return None
    md = art.get("content_md") or ""
    agent_id = await _ensure_linker(client)
    msg = (
        "Write ONE sentence to add to the article below that links to this related "
        "article.\n\n"
        f'Related article: "{s["target_title"]}"\n'
        f"Link URL (use exactly): {s['target_url']}\n\n"
        "## Output\n"
        f"- ONE Markdown sentence containing exactly one [anchor]({s['target_url']}) "
        "link.\n\n"
        f"---ARTICLE---\n{md[:8000]}"
    )
    res = await client.run_agent(agent_id, msg)
    sentence = (res.get("content") or "").strip()
    # Sanity: it must actually contain the target link, else leave the gap pending.
    if not sentence or s["target_url"] not in sentence:
        return None
    # The model writes a real URL (natural); swap it for the stable ref before storing
    # so the link follows the target's slug forever.
    sentence = sentence.replace(s["target_url"], link_ref(s["target_article_id"]))
    new_md = _insert_after_intro(md, sentence)
    gen_svc._update(db, s["article_id"], content_md=new_md)
    from . import brief as brief_svc
    from . import scoring

    brief = (
        brief_svc.get_brief(db, art["brief_id"]) if art.get("brief_id") else {}
    ) or {}
    seo = scoring.score_seo(
        resolve_links(db, business_id, new_md),
        art.get("meta_title") or art.get("title") or "",
        art.get("meta_description"), brief,
    )
    gen_svc._update(db, s["article_id"], seo_score=seo)
    return _set_status(db, business_id, suggestion_id, "accepted")
