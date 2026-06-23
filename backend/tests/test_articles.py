"""Articles — section parsing (unit) + async route wiring (hermetic)."""

from unittest.mock import MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import generation as svc

BRIEF_ID = "44444444-4444-4444-4444-444444444444"
BID = "11111111-1111-1111-1111-111111111111"
ARTICLE = {
    "id": "55555555-5555-5555-5555-555555555555",
    "business_id": BID,
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


def _brand_db() -> MagicMock:
    """A db mock whose fetch_one satisfies assert_brand_access (org match)."""
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def make_client(db: MagicMock | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db or _brand_db()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app))


def test_generate_article_201(monkeypatch):
    async def fake_task(*args, **kwargs):
        return None

    monkeypatch.setattr(svc, "get_brief", lambda db, bid: {"id": BRIEF_ID, "business_id": BID})
    monkeypatch.setattr(
        svc, "create_article", lambda db, brief, author_id=None: ARTICLE
    )
    monkeypatch.setattr(svc, "run_generation_task", fake_task)

    resp = make_client().post("/api/articles", json={"brief_id": BRIEF_ID})
    assert resp.status_code == 201
    assert resp.json()["generation_status"] == "grounding"


def test_generate_article_unknown_brief_404(monkeypatch):
    monkeypatch.setattr(svc, "get_brief", lambda db, bid: None)
    resp = make_client().post("/api/articles", json={"brief_id": BRIEF_ID})
    assert resp.status_code == 404


def test_update_article_patches():
    db = MagicMock()
    db.fetch_one.return_value = {**ARTICLE, "title": "Edited", "org_id": UUID(ADMIN_ORG)}
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    resp = TestClient(with_auth(app)).patch(
        f"/api/articles/{ARTICLE['id']}", json={"title": "Edited"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Edited"
    sql = db.fetch_one.call_args.args[0].lower()
    assert "update public.articles" in sql
