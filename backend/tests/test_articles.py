"""Articles — section parsing (unit) + async route wiring (hermetic)."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import generation as svc

BRIEF_ID = "44444444-4444-4444-4444-444444444444"
ARTICLE = {
    "id": "55555555-5555-5555-5555-555555555555",
    "business_id": "11111111-1111-1111-1111-111111111111",
    "brief_id": BRIEF_ID,
    "research_run_id": None,
    "title": "Headless CMS Comparison",
    "slug": "headless-cms-comparison",
    "status": "draft",
    "generation_status": "grounding",
    "generation_error": None,
    "progress": {},
    "content_md": "",
    "meta_title": None,
    "meta_description": None,
    "seo_score": None,
    "geo_score": None,
    "created_at": "2026-06-19T00:00:00Z",
    "updated_at": "2026-06-19T00:00:00Z",
}


def test_parse_sections_groups_h3_under_h2():
    secs = svc.parse_sections(["H2: A", "H3: a1", "H3: a2", "H2: B"])
    assert len(secs) == 2
    assert secs[0] == {"h2": "A", "subs": ["a1", "a2"]}
    assert secs[1] == {"h2": "B", "subs": []}


def test_parse_sections_handles_unprefixed():
    secs = svc.parse_sections(["Intro", "Details"])
    assert [s["h2"] for s in secs] == ["Intro", "Details"]


def make_client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(app)


def test_generate_article_201(monkeypatch):
    async def fake_task(*args, **kwargs):
        return None

    monkeypatch.setattr(svc, "get_brief", lambda db, bid: {"id": BRIEF_ID})
    monkeypatch.setattr(svc, "create_article", lambda db, brief: ARTICLE)
    monkeypatch.setattr(svc, "run_generation_task", fake_task)

    resp = make_client().post("/api/articles", json={"brief_id": BRIEF_ID})
    assert resp.status_code == 201
    assert resp.json()["generation_status"] == "grounding"


def test_generate_article_unknown_brief_404(monkeypatch):
    monkeypatch.setattr(svc, "get_brief", lambda db, bid: None)
    resp = make_client().post("/api/articles", json={"brief_id": BRIEF_ID})
    assert resp.status_code == 404
