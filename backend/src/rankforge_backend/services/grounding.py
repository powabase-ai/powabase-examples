"""Grounding — a per-brand Knowledge Base built from research Sources.

The scraped competitor pages are already Powabase Sources (approach 2), so grounding
is just: ensure the brand has a KB, add the run's sources to it, and let generation
retrieve from it for cited, factual writing.
"""

import asyncio
from typing import Any
from uuid import UUID

from ..db import Database
from ..powabase import PowabaseClient
from . import business_profiles as brands
from . import research as research_svc

_INDEX_TERMINAL = {"indexed", "failed", "cancelled"}


async def ensure_brand_kb(
    client: PowabaseClient, db: Database, business_id: UUID
) -> str:
    """Get-or-create the brand's grounding KB; cache its id on the brand."""
    brand = brands.get_profile(db, business_id)
    if brand is None:
        raise ValueError("business profile not found")
    if brand.get("brand_kb_id"):
        return brand["brand_kb_id"]

    kb = await client.create_kb(
        f"{brand['name']} — grounding",
        description="Scraped research sources for grounded, cited drafting.",
        retrieval_config={"method": "hybrid", "top_k": 6},
    )
    kb_id = kb.get("id") or kb.get("knowledge_base", {}).get("id")

    # Compare-and-set: only the first concurrent writer wins. A loser (its UPDATE
    # matches 0 rows) discards the KB it just created and uses the winner's, so we
    # never leak a second KB or overwrite an existing mapping.
    won = db.fetch_one(
        "update public.business_profiles set brand_kb_id = %s "
        "where id = %s and brand_kb_id is null returning brand_kb_id",
        (kb_id, business_id),
    )
    if won is not None:
        return kb_id
    try:
        await client.delete_kb(kb_id)
    except Exception:  # noqa: BLE001
        pass
    fresh = brands.get_profile(db, business_id)
    return fresh["brand_kb_id"]


async def index_run_sources(
    client: PowabaseClient, db: Database, kb_id: str, run_id: UUID
) -> int:
    """Add a research run's scraped sources to the KB and wait for indexing."""
    sources = research_svc.list_sources(db, run_id)
    source_ids = [
        s["source_id"] for s in sources if s.get("status") == "extracted" and s["source_id"]
    ]
    for sid in source_ids:
        try:
            await client.add_source_to_kb(kb_id, sid)
        except Exception:  # noqa: BLE001 — skip a source that won't index, keep going
            continue

    # poll until all sources reach a terminal index_status (bounded)
    for _ in range(45):
        listing = await client.list_kb_sources(kb_id)
        items = listing.get("items", []) if isinstance(listing, dict) else []
        statuses = [i.get("index_status") for i in items]
        if statuses and all(s in _INDEX_TERMINAL for s in statuses):
            break
        await asyncio.sleep(2)

    return len(source_ids)


async def search(
    client: PowabaseClient,
    kb_id: str,
    query: str,
    *,
    top_k: int = 6,
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    # Resilient: an empty/partial KB should degrade to ungrounded drafting, not fail.
    try:
        return await client.search_kb(
            kb_id, query, top_k=top_k, source_ids=source_ids
        )
    except Exception:  # noqa: BLE001
        return []
