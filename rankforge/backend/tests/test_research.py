"""Research — JSON extraction (unit) + async route wiring (hermetic)."""

from unittest.mock import AsyncMock, MagicMock
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


def test_diverse_urls_skips_junk_domains():
    urls = [
        "https://youtube.com/watch?v=1",
        "https://reddit.com/r/x",
        "https://realsite.com/guide",
        "https://docs.example.com/api",
    ]
    out = svc.diverse_urls(urls, 4)
    domains = [svc._domain(u) for u in out]
    assert "youtube.com" not in domains and "reddit.com" not in domains
    assert "realsite.com" in domains and "docs.example.com" in domains


def test_is_usable_source():
    assert svc.is_usable_source({"status": "extracted", "word_count": 800})
    # failed/thin pages are not citable
    assert not svc.is_usable_source({"status": "failed", "word_count": 800})
    assert not svc.is_usable_source({"status": "extracted", "word_count": 30})
    assert not svc.is_usable_source({"status": "extracted", "word_count": None})


def make_client() -> TestClient:
    app = create_app()
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}  # assert_brand_access
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app))


RID = ROW["id"]


async def test_delete_run_skips_shared_source(monkeypatch):
    """Unshared scraped Sources are deleted from Powabase; a Source still referenced
    by another workspace (another run / brand material / cluster) is left intact."""
    db = MagicMock()
    monkeypatch.setattr(svc, "get_run", lambda d, rid: {"id": RID})
    db.fetch_all.return_value = [{"source_id": "shared"}, {"source_id": "solo"}]
    db.fetch_one.return_value = {"id": RID}  # the final run delete
    # 'shared' still referenced elsewhere (>0) → kept; 'solo' (0) → deleted.
    monkeypatch.setattr(
        svc.source_refs, "source_reference_count",
        lambda d, sid, **k: 1 if sid == "shared" else 0,
    )
    client = MagicMock()
    client.delete_source = AsyncMock()

    assert await svc.delete_run(client, db, RID) is True
    client.delete_source.assert_awaited_once_with("solo")


async def test_delete_run_dedupes_same_source(monkeypatch):
    """Two URLs in one run that dedupe to the same Powabase Source delete it once."""
    db = MagicMock()
    monkeypatch.setattr(svc, "get_run", lambda d, rid: {"id": RID})
    db.fetch_all.return_value = [{"source_id": "dup"}, {"source_id": "dup"}]
    db.fetch_one.return_value = {"id": RID}
    monkeypatch.setattr(
        svc.source_refs, "source_reference_count", lambda d, sid, **k: 0
    )
    client = MagicMock()
    client.delete_source = AsyncMock()

    assert await svc.delete_run(client, db, RID) is True
    client.delete_source.assert_awaited_once_with("dup")


def test_delete_research_route(monkeypatch):
    monkeypatch.setattr(svc, "get_run", lambda db, rid: {"id": RID, "business_id": BID})
    monkeypatch.setattr(svc, "delete_run", AsyncMock(return_value=True))
    resp = make_client().delete(f"/api/research/{RID}")
    assert resp.status_code == 204


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
