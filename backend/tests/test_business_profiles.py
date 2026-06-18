"""business_profiles routes — hermetic, Database mocked at the boundary."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db

ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Acme",
    "domain": "acme.com",
    "description": None,
    "niche": "SaaS",
    "audience": None,
    "seed_topics": ["seo"],
    "target_keywords": [],
    "competitors": [{"name": "Rival", "domain": "rival.com"}],
    "brand_kb_id": None,
    "sitemap_url": None,
    "created_by": None,
    "created_at": "2026-06-18T00:00:00Z",
    "updated_at": "2026-06-18T00:00:00Z",
}


def make_client(db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_create_inserts_and_returns_201():
    db = MagicMock()
    db.fetch_one.return_value = ROW
    client = make_client(db)

    resp = client.post(
        "/api/business-profiles",
        json={
            "name": "Acme",
            "domain": "acme.com",
            "niche": "SaaS",
            "seed_topics": ["seo"],
            "competitors": [{"name": "Rival", "domain": "rival.com"}],
        },
    )

    assert resp.status_code == 201
    assert resp.json()["name"] == "Acme"
    assert resp.json()["competitors"][0]["domain"] == "rival.com"
    sql = db.fetch_one.call_args.args[0].lower()
    assert "insert into public.business_profiles" in sql


def test_list_returns_profiles():
    db = MagicMock()
    db.fetch_all.return_value = [ROW]
    client = make_client(db)

    resp = client.get("/api/business-profiles")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == ROW["id"]


def test_get_missing_returns_404():
    db = MagicMock()
    db.fetch_one.return_value = None
    client = make_client(db)

    resp = client.get(
        "/api/business-profiles/22222222-2222-2222-2222-222222222222"
    )
    assert resp.status_code == 404


def test_update_sends_only_changed_fields():
    db = MagicMock()
    db.fetch_one.return_value = {**ROW, "niche": "Fintech"}
    client = make_client(db)

    resp = client.patch(
        "/api/business-profiles/11111111-1111-1111-1111-111111111111",
        json={"niche": "Fintech"},
    )
    assert resp.status_code == 200
    assert resp.json()["niche"] == "Fintech"
    sql = db.fetch_one.call_args.args[0].lower()
    assert "niche = %s" in sql
    assert "name = %s" not in sql  # unchanged field not in the UPDATE


def test_db_unconfigured_returns_503():
    # No dependency override → get_db sees app.state.db is None (hermetic lifespan).
    with TestClient(create_app()) as client:
        resp = client.get("/api/business-profiles")
    assert resp.status_code == 503
