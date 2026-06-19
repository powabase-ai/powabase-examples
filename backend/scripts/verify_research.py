"""Live Stage A verification (async Sources-backed flow): create a run, execute the
research task, then inspect status, competitors (with source ids), the research_sources
link rows, and one source's scraped markdown.

Usage (from backend/): uv run python scripts/verify_research.py
"""

import asyncio

from rankforge_backend.config import get_settings
from rankforge_backend.db import Database
from rankforge_backend.models.business import BusinessProfileCreate
from rankforge_backend.powabase import PowabaseClient
from rankforge_backend.services import business_profiles as brands
from rankforge_backend.services import research as svc


async def main() -> None:
    s = get_settings()
    db = Database(s.powabase_database_url)
    db.open()
    client = PowabaseClient(s.powabase_base_url, s.powabase_service_role_key, timeout=240)
    try:
        existing = brands.list_profiles(db)
        brand = existing[0] if existing else brands.create_profile(
            db, BusinessProfileCreate(name="Petal SEO", niche="GEO/SEO content tooling")
        )
        run = svc.create_research_run(
            db, business_id=brand["id"], topic="best backend as a service", locale="en-US"
        )
        print(f"brand: {brand['name']}  run: {run['id']}  status: {run['status']}")

        print("running research task (quick depth)…")
        await svc.run_research_task(
            client, db, run_id=run["id"], brand=brand,
            topic="best backend as a service", locale="en-US", depth="quick",
        )

        final = svc.get_run(db, run["id"])
        print("--- final run ---")
        print("status:", final["status"], "| error:", final["error"])
        print("intent:", final["intent"], "| serp:", len(final["serp"].get("results", [])))
        print("competitors:", len(final["competitors"]))
        for c in final["competitors"]:
            print(f"   - {c['url']}  words={c['word_count']}  headings={len(c['headings'])}  source={c['source_id']}")

        sources = svc.list_sources(db, run["id"])
        print("research_sources rows:", len(sources))
        if sources and sources[0]["source_id"]:
            md = await client.get_source_markdown(sources[0]["source_id"])
            print(f"first source markdown: {len(md)} chars; starts: {md[:120]!r}")
    finally:
        await client.aclose()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
