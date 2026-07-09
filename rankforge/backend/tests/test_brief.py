"""Brief — route wiring + update (hermetic)."""

from unittest.mock import MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import brief as svc

RID = "33333333-3333-3333-3333-333333333333"
BID = "11111111-1111-1111-1111-111111111111"


def test_direction_block_carries_title_angle_keyword():
    block = svc._direction_block(
        {"title": "Cursor breaks Supabase", "angle": "honest critique", "keyword": "backend for Claude Code"}
    )
    assert "Editorial direction" in block
    assert "Cursor breaks Supabase" in block
    assert "honest critique" in block
    assert "backend for Claude Code" in block


def test_direction_block_empty_without_an_angle_or_title():
    # a bare keyword is not an editorial direction — the brief stays SERP-driven
    assert svc._direction_block(None) == ""
    assert svc._direction_block({"keyword": "k"}) == ""
BRIEF = {
    "id": "44444444-4444-4444-4444-444444444444",
    "business_id": BID,
    "research_run_id": RID,
    "topic": "generative engine optimization",
    "primary_keyword": "generative engine optimization",
    "secondary_keywords": ["geo", "ai search"],
    "target_word_count": 2200,
    "headings": ["H2: What is GEO"],
    "entities": ["Perplexity"],
    "questions": ["What is GEO?"],
    "link_suggestions": {"internal": [], "external": []},
    "suggested_title": "GEO: The Complete Guide",
    "suggested_meta": "Learn GEO.",
    "created_at": "2026-06-18T00:00:00Z",
    "updated_at": "2026-06-18T00:00:00Z",
}


def make_client(db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app))


def test_generate_brief_201(monkeypatch):
    async def fake_generate(*args, **kwargs):
        return BRIEF

    monkeypatch.setattr(svc, "generate_brief", fake_generate)
    db = MagicMock()
    # get_run (resolve brand) → assert_brand_access (org match)
    db.fetch_one.side_effect = [{"business_id": BID}, {"org_id": UUID(ADMIN_ORG)}]
    client = make_client(db)
    resp = client.post("/api/briefs", json={"research_run_id": RID})
    assert resp.status_code == 201
    assert resp.json()["target_word_count"] == 2200


def test_update_brief_partial():
    db = MagicMock()
    # get_brief → assert_brand_access → update_brief all hit fetch_one; the dict
    # carries both business_id (for the brand lookup) and org_id (for access).
    db.fetch_one.return_value = {
        **BRIEF,
        "target_word_count": 3000,
        "org_id": UUID(ADMIN_ORG),
    }
    client = make_client(db)
    resp = client.patch(
        "/api/briefs/44444444-4444-4444-4444-444444444444",
        json={"target_word_count": 3000},
    )
    assert resp.status_code == 200
    assert resp.json()["target_word_count"] == 3000
    sql = db.fetch_one.call_args.args[0].lower()
    assert "target_word_count = %s" in sql
    assert "primary_keyword = %s" not in sql


def test_get_brief_404():
    db = MagicMock()
    db.fetch_one.return_value = None
    client = make_client(db)
    resp = client.get("/api/briefs/44444444-4444-4444-4444-444444444444")
    assert resp.status_code == 404


def test_update_brief_requires_editor():
    # A writer must NOT be able to rewrite a brief via the API — the UI gates the Edit
    # button on editor/admin, and the server must enforce the same (mirrors clusters).
    db = MagicMock()
    db.fetch_one.return_value = {**BRIEF, "org_id": UUID(ADMIN_ORG)}
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    resp = TestClient(with_auth(app, writer)).patch(
        "/api/briefs/44444444-4444-4444-4444-444444444444",
        json={"suggested_title": "rewritten"},
    )
    assert resp.status_code == 403


def test_update_brief_rejects_negative_word_count():
    db = MagicMock()
    db.fetch_one.return_value = {**BRIEF, "org_id": UUID(ADMIN_ORG)}
    client = make_client(db)
    resp = client.patch(
        "/api/briefs/44444444-4444-4444-4444-444444444444",
        json={"target_word_count": -5},
    )
    assert resp.status_code == 422  # ge=0 on the schema
