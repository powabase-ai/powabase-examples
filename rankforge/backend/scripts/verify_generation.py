"""Live Stage C verification (grounded): pick a research run that has scraped
sources, generate a brief, then generate a grounded draft.

Usage (from backend/): uv run python scripts/verify_generation.py
"""

import asyncio

from rankforge_backend.config import get_settings
from rankforge_backend.db import Database
from rankforge_backend.powabase import PowabaseClient
from rankforge_backend.services import brief as brief_svc
from rankforge_backend.services import business_profiles as brands
from rankforge_backend.services import generation as gen
from rankforge_backend.services import research as research_svc


async def main() -> None:
    s = get_settings()
    db = Database(s.powabase_database_url)
    db.open()
    client = PowabaseClient(s.powabase_base_url, s.powabase_service_role_key, timeout=300)
    try:
        brand = brands.list_profiles(db)[0]
        run = next(
            (r for r in research_svc.list_runs(db, brand["id"])
             if research_svc.list_sources(db, r["id"])),
            None,
        )
        if not run:
            print("no research run with sources — run research first")
            return
        n_sources = len(research_svc.list_sources(db, run["id"]))
        print(f"brand: {brand['name']}  run: {run['topic']}  sources: {n_sources}")

        print("generating brief…")
        brief = await brief_svc.generate_brief(client, db, research_run_id=run["id"])
        print(f"brief: {brief['id']}  headings: {len(brief['headings'])}")

        article = gen.create_article(db, brief)
        print("generating grounded draft (ground -> outline -> per-section)…")
        await gen.run_generation_task(client, db, article_id=article["id"], brief=brief)

        final = gen.get_article(db, article["id"])
        print("--- final ---")
        print("status:", final["generation_status"], "| error:", final["generation_error"])
        print("progress:", final["progress"])
        print("word count:", len(final["content_md"].split()))
        md = final["content_md"]
        # show whether it cited sources (markdown links)
        import re
        links = re.findall(r"\]\((https?://[^)]+)\)", md)
        print("inline source links:", len(links), "| sample:", links[:3])
        print("--- first 800 chars ---")
        print(md[:800])
    finally:
        await client.aclose()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
