"""Research — JSON extraction (unit) + route wiring (hermetic)."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import research as svc

BID = "11111111-1111-1111-1111-111111111111"
ROW = {
    "id": "33333333-3333-3333-3333-333333333333",
    "business_id": BID,
    "topic": "generative engine optimization",
    "locale": "en-US",
    "serp": {"results": [], "paa": [], "related_queries": []},
    "competitors": [],
    "clusters": [],
    "intent": "informational",
    "agent_run_id": "run_abc",
    "created_by": None,
    "created_at": "2026-06-18T00:00:00Z",
}


def test_extract_json_fenced():
    out = svc._extract_json('blah\n```json\n{"topic": "x", "intent": "info"}\n```\ndone')
    assert out == {"topic": "x", "intent": "info"}


def test_extract_json_bare():
    out = svc._extract_json('here it is: {"topic": "y"} trailing')
    assert out == {"topic": "y"}


def test_extract_json_missing_raises():
    with pytest.raises(ValueError):
        svc._extract_json("no json here")


def make_client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(app)


def test_create_research_returns_201(monkeypatch):
    async def fake_run_research(*args, **kwargs):
        return ROW

    monkeypatch.setattr(svc, "run_research", fake_run_research)
    client = make_client()
    resp = client.post("/api/research", json={"business_id": BID, "topic": "geo"})
    assert resp.status_code == 201
    assert resp.json()["intent"] == "informational"


def test_create_research_unknown_brand_404(monkeypatch):
    async def fake_run_research(*args, **kwargs):
        raise ValueError("business profile not found")

    monkeypatch.setattr(svc, "run_research", fake_run_research)
    client = make_client()
    resp = client.post("/api/research", json={"business_id": BID, "topic": "geo"})
    assert resp.status_code == 404
