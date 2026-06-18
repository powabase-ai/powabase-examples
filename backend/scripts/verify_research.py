"""Live Stage A verification: provision the research agent and run research for a
real brand + topic, then print the stored research_run.

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
        if existing:
            brand = existing[0]
        else:
            brand = brands.create_profile(
                db,
                BusinessProfileCreate(
                    name="Petal SEO",
                    domain="petal.org",
                    niche="GEO/SEO content tooling",
                    competitors=[{"domain": "surferseo.com"}],
                ),
            )
        print(f"brand: {brand['name']} ({brand['id']})")

        print("running research (quick depth) — this calls web_search + web_scrape…")
        run = await svc.run_research(
            client,
            db,
            business_id=brand["id"],
            topic="what is generative engine optimization",
            depth="quick",
        )
        serp = run["serp"].get("results", [])
        print("--- stored research_run ---")
        print("id:", run["id"])
        print("intent:", run["intent"])
        print("serp results:", len(serp))
        if serp:
            print("  top:", serp[0].get("title"), "—", serp[0].get("url"))
        print("paa:", len(run["serp"].get("paa", [])))
        print("competitors scraped:", len(run["competitors"]))
        if run["competitors"]:
            c = run["competitors"][0]
            print("  ex:", c.get("url"), "words=", c.get("word_count"),
                  "headings=", len(c.get("headings", [])))
        print("keyword clusters:", len(run["clusters"]))
        print("agent_run_id:", run["agent_run_id"])
    finally:
        await client.aclose()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
