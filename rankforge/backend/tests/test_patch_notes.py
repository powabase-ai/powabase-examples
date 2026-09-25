"""Relink patch notes — pending suggestions as Markdown, grouped by article."""

from unittest.mock import MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import relink


def test_patch_notes_groups_and_quotes_sentence():
    db = MagicMock()
    db.fetch_all.return_value = [
        {"slug": "a", "anchor_text": "pgvector", "target_url": "https://x.ai/vector-database/",
         "content_md": "Intro.\n\nWe index with pgvector here. Next.", "target_title": None},
        {"slug": "a", "anchor_text": None, "target_url": "https://x.ai/blog/b/",
         "content_md": "x", "target_title": "B"},
    ]
    out = relink.patch_notes(db, UUID("11111111-1111-1111-1111-111111111111"))
    assert out.startswith("### /blog/a/")
    assert '- "pgvector" → https://x.ai/vector-database/  (in: "We index with pgvector here.")' in out
    assert '- (new sentence) → https://x.ai/blog/b/  — "B"' in out


def test_patch_notes_empty():
    db = MagicMock()
    db.fetch_all.return_value = []
    assert relink.patch_notes(db, UUID("11111111-1111-1111-1111-111111111111")) == "No pending link suggestions.\n"


def test_seed_profile_validates():
    """Seed profile must be valid BlogProfile."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "seed_powabase_blog_profile",
        "/home/zipeng/worktrees/rankforge-blog-profile/rankforge/backend/scripts/seed_powabase_blog_profile.py"
    )
    seed = importlib.util.module_from_spec(spec)
    sys.modules["seed_powabase_blog_profile"] = seed
    spec.loader.exec_module(seed)

    from rankforge_backend.models.blog import BlogProfile
    BlogProfile.model_validate(seed.PROFILE)


# --- route test ---
BID = "11111111-1111-1111-1111-111111111111"


def test_relink_patch_notes_route():
    app = create_app()
    app = with_auth(app)
    db = MagicMock()
    # fetch_one is called by assert_brand_access to check org_id
    # Must return UUID object to match the user's org_id type
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    # fetch_all is called by patch_notes
    db.fetch_all.return_value = [
        {"slug": "a", "anchor_text": "pgvector", "target_url": "https://x.ai/vector-database/",
         "content_md": "We index with pgvector here.", "target_title": None},
    ]
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()

    client = TestClient(app)
    response = client.get(f"/api/business-profiles/{BID}/relink/patch-notes")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.text.startswith("### /blog/a/")
    assert '- "pgvector" → https://x.ai/vector-database/  (in: "We index with pgvector here.")' in response.text
