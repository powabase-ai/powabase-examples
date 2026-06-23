"""Research — JSON extraction (unit) + async route wiring (hermetic)."""

from unittest.mock import MagicMock
from uuid import UUID

import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import research as svc
from rankforge_backend.util import extract_json

BID = "11111111-1111-1111-1111-111111111111"
ROW = {
    "id": "33333333-3333-3333-3333-333333333333",
    "business_id": BID,
    "topic": "generative engine optimization",
    "locale": "en-US",
    "status": "searching",
    "error": None,
    "progress": {},
    "serp": {"results": [], "paa": [], "related_queries": []},
    "competitors": [],
    "clusters": [],
    "intent": None,
    "agent_run_id": None,
    "created_by": None,
    "created_at": "2026-06-18T00:00:00Z",
}


def test_extract_json_fenced():
    assert extract_json('x\n```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_bare():
    assert extract_json('note {"a": 2} end') == {"a": 2}


def test_extract_json_missing_raises():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_extract_json_fenced_keeps_nested_braces():
    # The fenced capture must be greedy or it truncates at the first "}".
    assert extract_json('```json\n{"a": {"b": 1}, "c": [1, 2]}\n```') == {
        "a": {"b": 1},
        "c": [1, 2],
    }


def test_extract_json_falls_back_to_bare_object():
    assert extract_json('chatter {"ok": true} trailing') == {"ok": True}


def test_diverse_urls_prefers_distinct_domains():
    urls = [
        "https://a.com/1",
        "https://www.a.com/2",
        "https://b.com/x",
        "https://c.com/y",
    ]
    out = svc.diverse_urls(urls, 3)
    assert [svc._domain(u) for u in out] == ["a.com", "b.com", "c.com"]


def test_diverse_urls_backfills_when_too_few_domains():
    urls = ["https://a.com/1", "https://a.com/2", "https://a.com/3"]
    assert len(svc.diverse_urls(urls, 2)) == 2


def make_client() -> TestClient:
    app = create_app()
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}  # assert_brand_access
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app))


def test_create_research_returns_searching(monkeypatch):
    async def fake_task(*args, **kwargs):
        return None

    monkeypatch.setattr(svc, "get_brand", lambda db, bid: {"id": BID, "niche": "x"})
    monkeypatch.setattr(svc, "create_research_run", lambda db, **kw: ROW)
    monkeypatch.setattr(svc, "run_research_task", fake_task)

    client = make_client()
    resp = client.post("/api/research", json={"business_id": BID, "topic": "geo"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "searching"


def test_create_research_unknown_brand_404(monkeypatch):
    monkeypatch.setattr(svc, "get_brand", lambda db, bid: None)
    client = make_client()
    resp = client.post("/api/research", json={"business_id": BID, "topic": "geo"})
    assert resp.status_code == 404
