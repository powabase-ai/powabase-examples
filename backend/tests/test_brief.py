"""Brief — route wiring + update (hermetic)."""

from unittest.mock import MagicMock

from conftest import with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import brief as svc

RID = "33333333-3333-3333-3333-333333333333"
BRIEF = {
    "id": "44444444-4444-4444-4444-444444444444",
    "business_id": "11111111-1111-1111-1111-111111111111",
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
    client = make_client(MagicMock())
    resp = client.post("/api/briefs", json={"research_run_id": RID})
    assert resp.status_code == 201
    assert resp.json()["target_word_count"] == 2200


def test_update_brief_partial():
    db = MagicMock()
    db.fetch_one.return_value = {**BRIEF, "target_word_count": 3000}
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
