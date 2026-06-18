"""Live smoke test of business_profiles CRUD against the real project DB.

Usage (from backend/): uv run python scripts/verify_business_profiles.py
Creates a throwaway profile, exercises CRUD, then deletes it.
"""

from rankforge_backend.config import get_settings
from rankforge_backend.db import Database
from rankforge_backend.models.business import (
    BusinessProfileCreate,
    BusinessProfileUpdate,
)
from rankforge_backend.services import business_profiles as svc


def main() -> None:
    db = Database(get_settings().powabase_database_url)
    db.open()
    try:
        created = svc.create_profile(
            db,
            BusinessProfileCreate(
                name="__smoke__ RankForge Test Brand",
                domain="example.com",
                niche="SEO tooling",
                seed_topics=["generative engine optimization", "geo"],
                competitors=[{"name": "Rival", "domain": "rival.com"}],
            ),
        )
        pid = created["id"]
        print("created:", pid, created["name"], "competitors=", created["competitors"])

        fetched = svc.get_profile(db, pid)
        assert fetched and fetched["niche"] == "SEO tooling"

        updated = svc.update_profile(
            db, pid, BusinessProfileUpdate(niche="GEO/SEO content")
        )
        assert updated["niche"] == "GEO/SEO content"
        print("updated niche ->", updated["niche"])

        listed = svc.list_profiles(db)
        print("list count:", len(listed))

        svc.delete_profile(db, pid)
        assert svc.get_profile(db, pid) is None
        print("deleted ok — CRUD verified against live DB")
    finally:
        db.close()


if __name__ == "__main__":
    main()
