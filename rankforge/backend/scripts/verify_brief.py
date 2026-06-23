"""Live Stage B verification: generate a brief from the latest research_run.

Usage (from backend/): uv run python scripts/verify_brief.py
"""

import asyncio

from rankforge_backend.config import get_settings
from rankforge_backend.db import Database
from rankforge_backend.powabase import PowabaseClient
from rankforge_backend.services import brief as brief_svc
from rankforge_backend.services import business_profiles as brands
from rankforge_backend.services import research as research_svc


async def main() -> None:
    s = get_settings()
    db = Database(s.powabase_database_url)
    db.open()
    client = PowabaseClient(s.powabase_base_url, s.powabase_service_role_key, timeout=180)
    try:
        brand = brands.list_profiles(db)[0]
        runs = research_svc.list_runs(db, brand["id"])
        if not runs:
            print("no research runs — run verify_research.py first")
            return
        run = runs[0]
        print(f"brand: {brand['name']}  research_run: {run['id']}  topic: {run['topic']}")

        print("generating brief…")
        b = await brief_svc.generate_brief(client, db, research_run_id=run["id"])
        print("--- stored brief ---")
        print("id:", b["id"])
        print("primary_keyword:", b["primary_keyword"])
        print("secondary_keywords:", len(b["secondary_keywords"]), "->", b["secondary_keywords"][:5])
        print("target_word_count:", b["target_word_count"])
        print("headings:", len(b["headings"]))
        for h in b["headings"][:6]:
            print("   -", h)
        print("entities:", len(b["entities"]), "questions:", len(b["questions"]))
        print("suggested_title:", b["suggested_title"])
        print("suggested_meta:", b["suggested_meta"])
    finally:
        await client.aclose()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
