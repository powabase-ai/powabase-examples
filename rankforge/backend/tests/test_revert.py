"""Versions carry frontmatter; revert restores the latest differing version."""

from unittest.mock import MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import generation as g

CUR = {"id": "a", "content_md": "# T\n\nnew", "title": "T", "meta_title": None,
       "meta_description": "d", "category": "rag", "summary": "s", "faq": None}

AID = "55555555-5555-5555-5555-555555555555"
BID = "11111111-1111-1111-1111-111111111111"


def test_update_article_snapshots_frontmatter_change(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    g.update_article(db, "a", {"summary": "changed"})
    ins = db.execute.call_args_list[0].args
    assert "insert into public.article_versions" in ins[0]
    assert "frontmatter" in ins[0]


def test_update_article_wraps_faq_json(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    g.update_article(db, "a", {"faq": [{"q": "Q", "a": "A"}]})
    params = db.fetch_one.call_args.args[1]
    assert any(isinstance(p, Json) for p in params)


def test_revert_restores_latest_differing(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    db.fetch_all.return_value = [
        {"id": "v2", "content_md": "# T\n\nnew", "frontmatter": None},  # same body
        {"id": "v1", "content_md": "# T\n\nold",
         "frontmatter": {"summary": "old s", "faq": [{"q": "Q", "a": "A"}]}},
    ]
    called = {}
    monkeypatch.setattr(g, "update_article",
                        lambda _db, _id, f: called.setdefault("f", f) or CUR)
    g.revert_last(db, "a")
    assert called["f"]["content_md"] == "# T\n\nold"
    assert called["f"]["summary"] == "old s"


def test_revert_404_when_no_differing_version(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    db.fetch_all.return_value = [{"id": "v", "content_md": CUR["content_md"],
                                  "frontmatter": None}]
    assert g.revert_last(db, "a") is None


# --- route ---
def _brand_db() -> MagicMock:
    """db whose fetch_one satisfies assert_brand_access (org match)."""
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def _client(db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app))


def test_revert_route_404_when_nothing_to_revert(monkeypatch):
    monkeypatch.setattr(
        g, "get_article", lambda db, aid: dict(CUR, id=AID, business_id=BID)
    )
    monkeypatch.setattr(g, "try_begin_refine", lambda db, aid, total: True)
    monkeypatch.setattr(g, "revert_last", lambda db, aid: None)
    resp = _client(_brand_db()).post(f"/api/articles/{AID}/revert")
    assert resp.status_code == 404


def test_revert_route_409_when_already_in_progress(monkeypatch):
    monkeypatch.setattr(
        g, "get_article", lambda db, aid: dict(CUR, id=AID, business_id=BID)
    )
    monkeypatch.setattr(g, "try_begin_refine", lambda db, aid, total: False)
    resp = _client(_brand_db()).post(f"/api/articles/{AID}/revert")
    assert resp.status_code == 409
