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
    db.fetch_one.return_value = None  # brand without a url_pattern → /blog/{slug}/
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
    from pathlib import Path

    seed_path = Path(__file__).resolve().parents[1] / "scripts" / "seed_powabase_blog_profile.py"
    spec = importlib.util.spec_from_file_location(
        "seed_powabase_blog_profile",
        seed_path,
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
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG), "url_pattern": None}
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


# --- review round 1 (I9): published articles only, real article URLs ---
_PROFILE = {"categories": [{"key": "rag", "label": "R"}]}
_BRAND = {"id": BID, "org_id": UUID(ADMIN_ORG), "domain": "x.ai",
          "url_pattern": "https://x.ai/blog/{slug}", "blog_profile": _PROFILE}


def _note_row(slug, **over):
    return {"id": f"{slug}-id", "slug": slug, "canonical_url": None,
            "anchor_text": None, "target_url": "https://x.ai/blog/b/",
            "target_title": "B", "content_md": "x", **over}


def test_patch_notes_only_cover_published_articles():
    db = MagicMock()
    db.fetch_one.return_value = _BRAND
    db.fetch_all.return_value = []
    relink.patch_notes(db, UUID(BID))
    q = db.fetch_all.call_args.args[0]
    assert "a.status = 'published'" in q


def test_patch_notes_heading_uses_the_brand_url_pattern():
    db = MagicMock()
    db.fetch_one.return_value = _BRAND
    db.fetch_all.return_value = [_note_row("a")]
    out = relink.patch_notes(db, UUID(BID))
    assert out.startswith("### https://x.ai/blog/a/\n")  # profile → trailing slash


def test_patch_notes_heading_prefers_the_article_canonical_override():
    db = MagicMock()
    db.fetch_one.return_value = _BRAND
    db.fetch_all.return_value = [
        _note_row("a", canonical_url="https://x.ai/guides/a"),
        _note_row("b"),
    ]
    out = relink.patch_notes(db, UUID(BID))
    assert "### https://x.ai/guides/a/\n" in out  # override, slash rule applied
    assert "### https://x.ai/blog/b/\n" in out


def test_patch_notes_route_404s_for_another_orgs_brand(monkeypatch):
    app = with_auth(create_app())
    db = MagicMock()
    db.fetch_one.return_value = {
        "org_id": UUID("00000000-0000-0000-0000-0000000000ff")
    }
    notes = MagicMock(return_value="leak")
    monkeypatch.setattr(relink, "patch_notes", notes)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    resp = TestClient(app).get(f"/api/business-profiles/{BID}/relink/patch-notes")
    assert resp.status_code in (403, 404)
    notes.assert_not_called()
