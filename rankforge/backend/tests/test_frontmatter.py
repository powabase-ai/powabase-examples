"""frontmatter — category/summary/FAQ generation, meta enforcement."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.routes.deps import get_db, get_powabase
from rankforge_backend.services import business_profiles as brands_svc
from rankforge_backend.services import frontmatter as fm

PROF = BlogProfile.model_validate({
    "categories": [{"key": "rag", "label": "R"}, {"key": "agents", "label": "A"}],
    "stance": "favor_brand",
}).model_dump()
BRAND = {"id": "b", "name": "Powabase", "blog_profile": PROF}
ART = {"id": "a", "business_id": "b", "title": "How to build RAG", "cluster_id": None,
       "content_md": "# T\n\nbody", "meta_title": None, "meta_description": "d"}
S45 = " ".join(["word"] * 44) + " end."
GOOD = {"category": "agents", "summary": S45,
        "faq": [{"q": f"Q{i}?", "a": "A."} for i in range(4)]}


def _client(*payloads):
    c = MagicMock()
    c.run_agent = AsyncMock(side_effect=[{"content": json.dumps(p)} for p in payloads])
    return c


@pytest.fixture
def deps():
    with patch.object(fm.gen_svc, "get_article", return_value=dict(ART)), \
         patch.object(fm.brands, "get_profile", return_value=BRAND), \
         patch.object(fm.gen_svc, "_update") as upd, \
         patch.object(fm, "ensure_agent", AsyncMock(return_value="agent")):
        yield upd


async def test_generate_writes_fields(deps):
    flags = await fm.generate(_client(GOOD), MagicMock(), "a")
    assert flags == []
    kw = deps.call_args.kwargs
    assert kw["category"] == "agents" and kw["summary"] == S45 and len(kw["faq"]) == 4


async def test_generate_retries_once_on_flags(deps):
    bad = {**GOOD, "summary": "too short"}
    c = _client(bad, GOOD)
    flags = await fm.generate(c, MagicMock(), "a")
    assert flags == [] and c.run_agent.await_count == 2
    assert "summary is 2 words" in c.run_agent.await_args_list[1].args[1]


async def test_generate_noop_without_profile(deps):
    with patch.object(fm.brands, "get_profile", return_value={"blog_profile": None}):
        c = _client()
        assert await fm.generate(c, MagicMock(), "a") == []
        c.run_agent.assert_not_called()


def test_enforce_meta_sets_meta_title_for_long_h1():
    with patch.object(fm.gen_svc, "_update") as upd:
        art = {**ART, "title": "x " * 40, "meta_title": "y" * 80,
               "meta_description": "z" * 200}
        fm.enforce_meta(MagicMock(), "a", art, BlogProfile.model_validate(PROF))
        kw = upd.call_args.kwargs
        assert len(kw["meta_title"]) <= 60 and len(kw["meta_description"]) <= 160


def test_enforce_meta_derives_meta_title_from_title_when_missing():
    with patch.object(fm.gen_svc, "_update") as upd:
        art = {**ART, "title": "word " * 20, "meta_title": None}
        fm.enforce_meta(MagicMock(), "a", art, BlogProfile.model_validate(PROF))
        assert 0 < len(upd.call_args.kwargs["meta_title"]) <= 60


# --- route ---
AID = "55555555-5555-5555-5555-555555555555"
BID = "11111111-1111-1111-1111-111111111111"


def _route_client(db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    with_auth(app)
    return TestClient(app)


def test_frontmatter_route_409_without_profile(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": AID, "business_id": BID, "org_id": UUID(ADMIN_ORG),
    }
    monkeypatch.setattr(
        brands_svc, "get_profile", lambda d, bid: {"blog_profile": None}
    )
    resp = _route_client(db).post(f"/api/articles/{AID}/frontmatter")
    assert resp.status_code == 409
