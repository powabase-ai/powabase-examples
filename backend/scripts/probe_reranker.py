"""Apply the reranked retrieval config (zerank-2, candidate_count 100, top_k 20) to
the brand KB and confirm it's accepted + search still returns results.

Reranking fails open (no ZeroEntropy key → base hybrid), so this confirms acceptance,
not that reranking is definitely active. Usage (from backend/): uv run python scripts/probe_reranker.py
"""

import asyncio
import json

from rankforge_backend.config import get_settings
from rankforge_backend.db import Database
from rankforge_backend.powabase import PowabaseClient
from rankforge_backend.services import business_profiles as brands
from rankforge_backend.services.grounding import RETRIEVAL_CONFIG


async def main() -> None:
    s = get_settings()
    db = Database(s.powabase_database_url)
    db.open()
    client = PowabaseClient(s.powabase_base_url, s.powabase_service_role_key, timeout=120)
    try:
        brand = brands.list_profiles(db)[0]
        kb_id = brand.get("brand_kb_id")
        print("brand:", brand["name"], "| kb:", kb_id)
        if not kb_id:
            print("no brand KB yet — generate an article first")
            return

        await client.update_kb(kb_id, retrieval_config=RETRIEVAL_CONFIG)
        print("PATCH retrieval_config accepted:", json.dumps(RETRIEVAL_CONFIG))

        res = await client.search_kb(
            kb_id, "headless CMS pricing and content modeling", top_k=20
        )
        print("search results:", len(res))
        if res:
            print("first-result meta:", json.dumps(res[0].get("meta", {})))
        for r in res[:5]:
            print(f"  score={r.get('score'):.3f} src={str(r.get('source_id'))[:8]} {r.get('text','')[:64]!r}")
    finally:
        await client.aclose()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
