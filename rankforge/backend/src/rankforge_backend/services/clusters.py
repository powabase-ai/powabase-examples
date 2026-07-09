"""Content clusters — topical-authority architecture.

Every opportunity/article belongs to exactly one cluster: it JOINS the best-matching
existing cluster as a supplementary member, or FOUNDS a new cluster as its permanent
authority PILLAR. A dedicated LLM agent (rankforge-cluster-architect) makes the call,
using a per-brand "cluster index" KB (full_doc → one embedding per cluster) to retrieve
candidate clusters by semantic similarity. The pillar, once set, is never auto-replaced.

This module owns: the cluster-index KB, the architect agent, the assignment engine,
and cluster reads/writes. Cluster-aware LINKING and pillar-aware GENERATION consume it.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from ..db import Database
from ..powabase import (
    EXTRACTION_TERMINAL,
    PowabaseClient,
    indexed_source_id,
    wait_for_kb_index,
)
from ..util import extract_json
from . import business_profiles as brands
from . import source_refs
from .agents import ensure_agent

log = logging.getLogger("rankforge.clusters")

_COLUMNS = (
    "id, business_id, label, theme, pillar_article_id, pillar_locked, "
    "index_doc_id, created_at, updated_at"
)

# full_document: each cluster's pillar summary is ONE short doc → one embedding, so
# search returns nearest CLUSTERS (not chunk fragments). The docs are tiny, so the
# usual long-doc/token downsides of whole-doc indexing don't apply here.
CLUSTER_INDEXING = {"strategy": "full_document"}
# Below this many clusters we skip retrieval and just hand them all to the agent.
_RETRIEVE_THRESHOLD = 6


# --- cluster-index KB (get-or-create; mirrors grounding.ensure_brand_kb) ---
async def ensure_cluster_kb(
    client: PowabaseClient, db: Database, business_id: UUID
) -> str:
    brand = brands.get_profile(db, business_id)
    if brand is None:
        raise ValueError("business profile not found")
    if brand.get("cluster_kb_id"):
        return brand["cluster_kb_id"]
    kb = await client.create_kb(
        f"{brand['name']} — clusters",
        description="One doc per content cluster, for topical-similarity retrieval.",
        indexing_config=CLUSTER_INDEXING,
    )
    kb_id = kb.get("id") or kb.get("knowledge_base", {}).get("id")
    won = db.fetch_one(
        "update public.business_profiles set cluster_kb_id = %s "
        "where id = %s and cluster_kb_id is null returning cluster_kb_id",
        (kb_id, business_id),
    )
    if won is not None:
        return kb_id
    try:
        await client.delete_kb(kb_id)
    except Exception:  # noqa: BLE001
        pass
    return brands.get_profile(db, business_id)["cluster_kb_id"]


async def _index_doc(
    client: PowabaseClient, kb_id: str, *, label: str, theme: str, pillar_title: str
) -> str | None:
    """Upload a cluster's one-doc representation and index it. Returns the source id."""
    text = (
        f"# {label}\n\nTheme: {theme or label}\n\n"
        f"Pillar article: {pillar_title or label}\n"
    )
    try:
        up = await client.upload_source(
            f"cluster-{(label or 'cluster')[:40]}.md",
            text.encode("utf-8"),
            "text/markdown",
        )
        sid = up.get("id") or (up.get("source") or {}).get("id")
        if not sid:
            return None
        for _ in range(20):  # tiny doc — extraction is near-instant, poll briefly
            src = await client.get_source(sid)
            if src.get("extraction_status") in EXTRACTION_TERMINAL:
                break
            await asyncio.sleep(1)
        await client.add_source_to_kb(kb_id, sid)
        # add_source_to_kb triggers indexing ASYNC — wait for it to settle so the
        # freshly-founded cluster is immediately retrievable by the next assignment.
        await wait_for_kb_index(client, kb_id, source_id=sid, attempts=30, delay=1)
        return sid
    except Exception:  # noqa: BLE001 — retrieval degrades to pass-all without the doc
        log.exception("cluster index-doc upload failed for kb %s", kb_id)
        return None


# --- reads ---
def list_clusters(db: Database, business_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_COLUMNS} from public.content_clusters "
        "where business_id = %s order by created_at",
        (business_id,),
    )


def get_cluster(db: Database, cluster_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_COLUMNS} from public.content_clusters where id = %s", (cluster_id,)
    )


def list_clusters_view(db: Database, business_id: UUID) -> list[dict[str, Any]]:
    """Clusters enriched with pillar title + member count (one query) for the UI."""
    return db.fetch_all(
        f"select {_COLUMNS}, "
        "(select count(*) from public.articles a "
        "   where a.cluster_id = content_clusters.id "
        "     and a.cluster_role = 'member') as member_count, "
        "(select title from public.articles p "
        "   where p.id = content_clusters.pillar_article_id) as pillar_title "
        "from public.content_clusters where business_id = %s order by created_at",
        (business_id,),
    )


def get_cluster_detail(db: Database, cluster_id: UUID) -> dict[str, Any] | None:
    c = get_cluster(db, cluster_id)
    if c is None:
        return None
    c["pillar_title"] = _pillar_title(db, c)
    c["members"] = list_members(db, cluster_id)
    return c


def list_members(db: Database, cluster_id: UUID) -> list[dict[str, Any]]:
    """Articles in a cluster (pillar first, then members), for the cluster view."""
    return db.fetch_all(
        "select id, title, slug, status, cluster_role, canonical_url "
        "from public.articles where cluster_id = %s "
        "order by (cluster_role = 'pillar') desc, created_at",
        (cluster_id,),
    )


def _pillar_title(db: Database, cluster: dict[str, Any]) -> str:
    pid = cluster.get("pillar_article_id")
    if pid:
        row = db.fetch_one("select title from public.articles where id = %s", (pid,))
        if row and row.get("title"):
            return row["title"]
    return cluster.get("label") or ""


# --- writes ---
def attach_article(
    db: Database, article_id: UUID, cluster_id: UUID, role: str
) -> None:
    """Link an article to its cluster. If it's the pillar and the cluster has no pillar
    article yet, claim the slot (permanent — only filled while still empty). Claim the
    slot FIRST: if a concurrent draft already took it, attach this article as a member so
    its cluster_role can never disagree with the cluster's pillar_article_id (no phantom
    second pillar)."""
    if role == "pillar":
        # Claim the slot only if it's empty OR already held by THIS article — so an
        # idempotent re-attach of the current pillar stays pillar; a genuinely
        # contended slot (held by another article) matches 0 rows and demotes below.
        claimed = db.fetch_one(
            "update public.content_clusters set pillar_article_id = %s, "
            "updated_at = now() where id = %s and (pillar_article_id is null "
            "or pillar_article_id = %s) returning id",
            (article_id, cluster_id, article_id),
        )
        if claimed is None:
            role = "member"  # slot already filled → don't record a second pillar
    db.execute(
        "update public.articles set cluster_id = %s, cluster_role = %s where id = %s",
        (cluster_id, role, article_id),
    )


def set_pillar(
    db: Database, business_id: UUID, cluster_id: UUID, article_id: UUID
) -> dict[str, Any] | None:
    """Manual override: make `article_id` the cluster's pillar (and lock it). Demotes
    the previous pillar to a member. The only way a pillar ever changes."""
    cluster = db.fetch_one(
        "select id from public.content_clusters where id = %s and business_id = %s",
        (cluster_id, business_id),
    )
    if cluster is None:
        return None
    # The new pillar must be one of THIS brand's articles — never pull in a foreign
    # article (the request only carries an article id).
    art = db.fetch_one(
        "select id from public.articles where id = %s and business_id = %s",
        (article_id, business_id),
    )
    if art is None:
        return None
    # One transaction so the pillar swap can't half-apply. FIRST vacate any OTHER cluster
    # this article currently anchors, so promoting it here never leaves a dangling
    # pillar_article_id pointing at an article that moved (mirrors move_article).
    with db.connection() as conn:
        conn.execute(
            "update public.content_clusters set pillar_article_id = null, "
            "updated_at = now() where pillar_article_id = %s and id <> %s",
            (article_id, cluster_id),
        )
        conn.execute(
            "update public.articles set cluster_role = 'member' "
            "where cluster_id = %s and cluster_role = 'pillar'",
            (cluster_id,),
        )
        conn.execute(
            "update public.articles set cluster_id = %s, cluster_role = 'pillar' "
            "where id = %s",
            (cluster_id, article_id),
        )
        row = conn.execute(
            "update public.content_clusters set pillar_article_id = %s, "
            f"pillar_locked = true, updated_at = now() where id = %s returning {_COLUMNS}",
            (article_id, cluster_id),
        ).fetchone()
    return row


async def create_cluster(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    *,
    label: str,
    theme: str = "",
) -> dict[str, Any]:
    """Manually found a new (empty) cluster: insert the row + build its one-doc index
    entry so future topics can be matched to it by the architect. Mirrors assign()'s
    'found' branch without the agent decision — the user is explicitly founding. The
    cluster starts with no pillar; populate it by moving articles in and designating one
    (move_article / set_pillar)."""
    kb_id = await ensure_cluster_kb(client, db, business_id)
    cluster = db.fetch_one(
        "insert into public.content_clusters (business_id, label, theme) "
        f"values (%s, %s, %s) returning {_COLUMNS}",
        (business_id, label[:120], theme or ""),
    )
    # pillar_title = label: there's no pillar article yet, so the index doc describes the
    # cluster by its label/theme (enough for topical-similarity retrieval).
    doc_id = await _index_doc(client, kb_id, label=label, theme=theme, pillar_title=label)
    if doc_id:
        db.execute(
            "update public.content_clusters set index_doc_id = %s where id = %s",
            (doc_id, cluster["id"]),
        )
        cluster["index_doc_id"] = doc_id
    return cluster


async def update_cluster(
    client: PowabaseClient,
    db: Database,
    cluster_id: UUID,
    *,
    label: str | None = None,
    theme: str | None = None,
) -> dict[str, Any] | None:
    """Edit a cluster's label/theme (partial: an omitted field is left as-is; an empty
    theme clears it). When the text actually changes, refresh the cluster-index doc so
    the architect keeps matching future topics on the CURRENT label/theme, not a stale
    embedding. Remote index steps are best-effort (retrieval degrades to pass-all
    without the doc). Returns the updated row, or None if the cluster is gone."""
    current = get_cluster(db, cluster_id)
    if current is None:
        return None
    new_label = current["label"] if label is None else label[:120]
    new_theme = (current.get("theme") or "") if theme is None else (theme or "")
    if new_label == current["label"] and new_theme == (current.get("theme") or ""):
        return current  # nothing changed → skip the write + re-index entirely

    row = db.fetch_one(
        "update public.content_clusters set label = %s, theme = %s, "
        f"updated_at = now() where id = %s returning {_COLUMNS}",
        (new_label, new_theme, cluster_id),
    )
    if row is None:
        return None

    # Refresh the index doc: build a fresh one, point the cluster at it, then retire the
    # stale doc (de-index + delete the Source if nothing else references it — same
    # orphan-safe pattern as delete_cluster). Best-effort throughout.
    old_doc = current.get("index_doc_id")
    try:
        kb_id = await ensure_cluster_kb(client, db, row["business_id"])
        new_doc = await _index_doc(
            client, kb_id,
            label=new_label, theme=new_theme,
            pillar_title=_pillar_title(db, row),
        )
        if new_doc:
            db.execute(
                "update public.content_clusters set index_doc_id = %s where id = %s",
                (new_doc, cluster_id),
            )
            row["index_doc_id"] = new_doc
            if old_doc and old_doc != new_doc:
                indexed = await indexed_source_id(client, kb_id, old_doc)
                try:
                    await client.remove_source_from_kb(kb_id, indexed)
                except Exception:  # noqa: BLE001 — de-index failure isn't fatal
                    log.exception("stale cluster de-index failed for %s/%s", kb_id, indexed)
                if source_refs.source_reference_count(db, old_doc) == 0:
                    try:
                        await client.delete_source(old_doc)
                    except Exception:  # noqa: BLE001 — Source delete isn't fatal
                        log.exception("stale cluster source delete failed for %s", old_doc)
    except Exception:  # noqa: BLE001 — a re-index failure must not fail the metadata edit
        log.exception("cluster re-index failed for %s", cluster_id)
    return row


def move_article(
    db: Database, business_id: UUID, article_id: UUID, target_cluster_id: UUID
) -> dict[str, Any] | None:
    """Move an article into another cluster as a MEMBER. Both the article and the target
    cluster must belong to the brand (returns None otherwise). If the article was the
    pillar of whatever cluster it currently anchors, that slot is vacated — the old
    cluster is left pillar-less until a new pillar is designated (a member move must
    never leave content_clusters.pillar_article_id dangling at an article that left).
    Idempotent-safe: moving into the cluster it's already in just re-asserts membership.
    Returns the target cluster's fresh row."""
    target = db.fetch_one(
        "select id from public.content_clusters where id = %s and business_id = %s",
        (target_cluster_id, business_id),
    )
    if target is None:
        return None
    art = db.fetch_one(
        "select id from public.articles where id = %s and business_id = %s",
        (article_id, business_id),
    )
    if art is None:
        return None
    # One transaction: vacate any pillar slot this article holds, then re-home it. If it
    # anchored its old cluster, that cluster becomes pillar-less (not half-detached).
    with db.connection() as conn:
        conn.execute(
            "update public.content_clusters set pillar_article_id = null, "
            "updated_at = now() where pillar_article_id = %s",
            (article_id,),
        )
        conn.execute(
            "update public.articles set cluster_id = %s, cluster_role = 'member' "
            "where id = %s",
            (target_cluster_id, article_id),
        )
    return get_cluster(db, target_cluster_id)


# --- the architect agent ---
CLUSTER_AGENT_NAME = "rankforge-cluster-architect"
# A bounded join-vs-found classification over a handful of candidate clusters — Sonnet at
# medium effort handles it as well as Opus did, at a fraction of the cost (this runs per
# opportunity/article and in a 25-item backfill loop).
CLUSTER_MODEL = "claude-sonnet-4-6"

_SYSTEM = """\
You are RankForge's **content-cluster architect**. A brand's blog is organized into \
topic clusters for topical authority: each cluster has ONE authoritative **pillar** \
article (the broad, central piece the brand promotes) and several **member** articles \
(each focused on a subtopic, all linking up to the pillar).

Given a NEW topic and the brand's existing clusters, decide ONE of:
- **join** — the topic is a facet, subtopic, comparison, or how-to OF an existing \
cluster's theme → it becomes a member of that cluster.
- **found** — the topic is a genuinely distinct theme that deserves its own authority \
pillar → it founds a new cluster (and becomes that cluster's pillar).

## How to decide
- **Bias toward joining.** Found a new cluster ONLY when no existing cluster's theme \
genuinely covers the topic — a distinct area the brand should build separate authority \
around, not a near-duplicate of an existing cluster.
- Judge by topical relationship, not raw keyword overlap: a narrower piece within a \
broader theme JOINS it; it does not found a competing cluster.
- When you **found**, give the cluster a short human label (a few words) and a \
one-paragraph **theme** describing its scope — which subtopics belong in it — so future \
topics can be matched to it.

## Output discipline
- Return exactly one JSON object — no prose, no commentary, no code fences.
"""

_SCHEMA = (
    '{"decision": "join" | "found", "cluster_id": <id from the list, or null>, '
    '"label": <new cluster label, or null>, "theme": <new cluster scope, or null>, '
    '"rationale": <one sentence>}'
)


async def ensure_cluster_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=CLUSTER_AGENT_NAME,
        model=CLUSTER_MODEL,
        system_prompt=_SYSTEM,
        settings={"reasoning_effort": "medium"},
    )


def _candidates_block(db: Database, candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "(none yet — this would be the brand's first cluster)"
    lines = []
    for c in candidates:
        lines.append(
            f'- [{c["id"]}] "{c.get("label")}" — pillar: '
            f'"{_pillar_title(db, c)}" — theme: {c.get("theme") or "(n/a)"}'
        )
    return "\n".join(lines)


async def _run_agent(
    client: PowabaseClient,
    db: Database,
    brand: dict[str, Any] | None,
    *,
    title: str,
    keyword: str | None,
    angle: str | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    brand = brand or {}
    msg = (
        "## Brand\n"
        f"- Name: {brand.get('name')}\n"
        f"- Niche: {brand.get('niche') or 'n/a'}\n"
        f"- Audience: {brand.get('audience') or 'n/a'}\n"
        f"- Seed topics: {', '.join(brand.get('seed_topics') or []) or 'n/a'}\n\n"
        "## New topic\n"
        f"- Title: {title}\n"
        f"- Primary keyword: {keyword or 'n/a'}\n"
        f"- Angle: {angle or 'n/a'}\n\n"
        "## Existing clusters (candidates — join one of these by its id, or found a new one)\n"
        f"{_candidates_block(db, candidates)}\n\n"
        "## Task\n"
        "- Decide join or found per the rules. If joining, `cluster_id` MUST be one of "
        "the ids above.\n\n"
        f"## Output\nReturn ONLY this JSON object:\n{_SCHEMA}"
    )
    agent_id = await ensure_cluster_agent(client)
    res = await client.run_agent(agent_id, msg)
    data = extract_json(res.get("content") or "")
    return data if isinstance(data, dict) else {}


# --- the assignment engine ---
async def _retrieve_candidates(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    kb_id: str,
    query: str,
    *,
    limit: int = _RETRIEVE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Nearest existing clusters for `query`. With few clusters, pass them all (no
    retrieval); otherwise search the cluster-index KB and map hits back to clusters."""
    clusters = list_clusters(db, business_id)
    if len(clusters) <= limit:
        return clusters
    by_doc = {c["index_doc_id"]: c for c in clusters if c.get("index_doc_id")}
    # Clusters with no index doc (upload failed) can't be retrieved by search — always
    # include them so a new topic can still join one instead of founding a duplicate.
    out: list[dict[str, Any]] = [c for c in clusters if not c.get("index_doc_id")]
    seen: set[Any] = {c["id"] for c in out}
    try:
        hits = await client.search_kb(kb_id, query, top_k=limit)
    except Exception:  # noqa: BLE001 — fall back to the most recent clusters
        return out + [c for c in clusters[-limit:] if c["id"] not in seen]
    for h in hits:
        c = by_doc.get(h.get("source_id"))
        if c and c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return out or clusters[-limit:]


async def assign(
    client: PowabaseClient,
    db: Database,
    business_id: UUID,
    *,
    title: str,
    keyword: str | None = None,
    angle: str | None = None,
    extra_candidates: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Assign a topic (opportunity or article) to a cluster. Returns (cluster_id, role).

    Retrieves nearby clusters, lets the architect agent decide join-vs-found, and on
    'found' creates the cluster + its index doc. `extra_candidates` lets a caller pass
    clusters just founded in the same batch (not yet searchable) to avoid duplicates.
    """
    brand = brands.get_profile(db, business_id)
    kb_id = await ensure_cluster_kb(client, db, business_id)
    query = " ".join(p for p in (title, keyword, angle) if p)
    candidates = await _retrieve_candidates(client, db, business_id, kb_id, query)
    if extra_candidates:
        ids = {c["id"] for c in candidates}
        candidates = candidates + [c for c in extra_candidates if c["id"] not in ids]

    decision = await _run_agent(
        client, db, brand, title=title, keyword=keyword, angle=angle,
        candidates=candidates,
    )
    cand_by_id = {str(c["id"]): c for c in candidates}
    if decision.get("decision") == "join":
        cid = str(decision.get("cluster_id") or "")
        if cid in cand_by_id:
            return cid, "member"
        if candidates:  # wanted to join but named a bad id → join the nearest
            return str(candidates[0]["id"]), "member"
        # else: nothing to join → fall through and found

    label = (decision.get("label") or title or "Cluster")[:120]
    theme = decision.get("theme") or ""
    cluster = db.fetch_one(
        "insert into public.content_clusters (business_id, label, theme) "
        f"values (%s, %s, %s) returning {_COLUMNS}",
        (business_id, label, theme),
    )
    doc_id = await _index_doc(client, kb_id, label=label, theme=theme, pillar_title=title)
    if doc_id:
        db.execute(
            "update public.content_clusters set index_doc_id = %s where id = %s",
            (doc_id, cluster["id"]),
        )
    return str(cluster["id"]), "pillar"


async def delete_cluster(
    client: PowabaseClient, db: Database, cluster_id: UUID
) -> bool:
    """Delete a cluster. Removes its one cluster-index doc from Powabase (de-index +
    Source delete), clears members' role, then drops the row — members' cluster_id is
    nulled by the FK (ON DELETE SET NULL), so they become unclustered (Backfill can
    re-cluster them). Remote steps are best-effort. Returns whether a row was deleted."""
    cluster = get_cluster(db, cluster_id)
    if cluster is None:
        return False
    doc_id = cluster.get("index_doc_id")
    if doc_id:
        brand = brands.get_profile(db, cluster["business_id"])
        kb_id = (brand or {}).get("cluster_kb_id")
        if kb_id:
            indexed = await indexed_source_id(client, kb_id, doc_id)
            try:
                await client.remove_source_from_kb(kb_id, indexed)
            except Exception:  # noqa: BLE001 — de-index failure must not block deletion
                log.exception("cluster de-index failed for %s/%s", kb_id, indexed)

    # cluster_id FK is ON DELETE SET NULL, but cluster_role is a plain text column —
    # clear it on members BEFORE the delete, while cluster_id still matches. One
    # transaction so members can't be left half-detached (role cleared, row still there).
    with db.connection() as conn:
        conn.execute(
            "update public.articles set cluster_role = null where cluster_id = %s",
            (cluster_id,),
        )
        conn.execute(
            "update public.opportunities set cluster_role = null where cluster_id = %s",
            (cluster_id,),
        )
        deleted = conn.execute(
            "delete from public.content_clusters where id = %s returning id",
            (cluster_id,),
        ).fetchone()

    # The index doc is a project-wide Source too. Decide AFTER the cluster row is gone
    # (orphan-safe under concurrency) — delete it only if nothing else references it
    # (cluster docs are distinct content, so this is near-always 0, but the guard keeps
    # a content collision from nuking another cluster's Source).
    if (
        deleted is not None
        and doc_id
        and source_refs.source_reference_count(db, doc_id) == 0
    ):
        try:
            await client.delete_source(doc_id)
        except Exception:  # noqa: BLE001 — Source delete must not block deletion
            log.exception("cluster source delete failed for %s", doc_id)
    return deleted is not None


# Max articles clustered per backfill call. Each assign() can poll Powabase for tens
# of seconds, so an unbounded sweep over a large unclustered set blows the request
# timeout; the route runs this inline and reports whether more remain so the UI can
# re-invoke until drained.
BACKFILL_BATCH = 25


async def backfill(
    client: PowabaseClient, db: Database, business_id: UUID
) -> tuple[int, bool]:
    """Assign any not-yet-clustered articles to clusters (one-time seed for articles
    that pre-date clustering + ongoing maintenance). Returns (assigned, remaining):
    how many were assigned this call, and whether more unclustered articles remain.

    Bounded to BACKFILL_BATCH per call (each assign() can poll for tens of seconds, so
    an unbounded sweep would blow the request timeout) — the caller re-invokes until
    `remaining` is False.

    Clusters are an editorial/topical-authority concept that applies regardless of
    publish state — new drafts are clustered at generation time, so the unclustered
    set is just pre-feature articles in any status (archived excluded)."""
    # Fetch one past the batch so we can report whether more remain without a 2nd query.
    rows = db.fetch_all(
        "select id, title, keywords from public.articles "
        "where business_id = %s and cluster_id is null and status <> 'archived' "
        "order by created_at limit %s",
        (business_id, BACKFILL_BATCH + 1),
    )
    remaining = len(rows) > BACKFILL_BATCH
    rows = rows[:BACKFILL_BATCH]
    founded: list[dict[str, Any]] = []
    n = 0
    for r in rows:
        try:
            kw = next((k for k in (r.get("keywords") or []) if k), None)
            cid, role = await assign(
                client, db, business_id, title=r["title"], keyword=kw,
                extra_candidates=founded,
            )
            attach_article(db, r["id"], cid, role)
            n += 1
            if role == "pillar" and (c := get_cluster(db, cid)):
                founded.append(c)
        except Exception:  # noqa: BLE001 — one article shouldn't fail the backfill
            log.exception("backfill cluster failed for article %s", r["id"])
    return n, remaining
