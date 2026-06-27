"""Powabase `/api/*` client package + shared source/KB-flow helpers.

The terminal-status sets and the two polling helpers below were duplicated across
several services (brand materials, clusters, grounding, research). They live here, next
to the client, as the single source of truth.
"""

import asyncio

from .client import PowabaseClient, PowabaseError

__all__ = [
    "PowabaseClient",
    "PowabaseError",
    "EXTRACTION_TERMINAL",
    "INDEX_TERMINAL",
    "indexed_source_id",
    "wait_for_kb_index",
]

# A Source's extraction (and a KB source's indexing) is finished — for better or
# worse — once it reaches one of these. Poll until then.
EXTRACTION_TERMINAL = frozenset(
    {"extracted", "attention_required", "failed", "cancelled"}
)
INDEX_TERMINAL = frozenset({"indexed", "failed", "cancelled"})


async def indexed_source_id(
    client: PowabaseClient, kb_id: str, source_id: str
) -> str:
    """Resolve a raw `source_id` to the KB's INDEXED-source id (what de-indexing needs
    — it can differ from the raw id). Falls back to the raw id on any lookup failure."""
    try:
        listing = await client.list_kb_sources(kb_id)
        items = listing.get("items", []) if isinstance(listing, dict) else []
        for it in items:
            if it.get("source_id") == source_id:
                return it.get("id") or it.get("indexed_source_id") or source_id
    except Exception:  # noqa: BLE001 — fall back to the raw id
        pass
    return source_id


async def wait_for_kb_index(
    client: PowabaseClient,
    kb_id: str,
    *,
    source_id: str | None = None,
    attempts: int = 45,
    delay: float = 2.0,
) -> None:
    """Poll `list_kb_sources` until all sources (or just `source_id`, if given) reach a
    terminal index_status, or the attempt bound is hit. Best-effort: a freshly-added
    source is searchable once this returns. Indexing runs async after add_source_to_kb."""
    for _ in range(attempts):
        listing = await client.list_kb_sources(kb_id)
        items = listing.get("items", []) if isinstance(listing, dict) else []
        statuses = [
            i.get("index_status")
            for i in items
            if source_id is None or i.get("source_id") == source_id
        ]
        if statuses and all(s in INDEX_TERMINAL for s in statuses):
            break
        await asyncio.sleep(delay)
