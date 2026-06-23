"""Review comments — CRUD wiring + authz (hermetic)."""

from unittest.mock import MagicMock

from conftest import with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.services import comments as csvc

AID = "55555555-5555-5555-5555-555555555555"
CID = "66666666-6666-6666-6666-666666666666"
AUTHOR = "00000000-0000-0000-0000-000000000001"  # == ADMIN_USER id
OTHER = CurrentUser(id="99999999-9999-9999-9999-999999999999", role="writer")
COMMENT = {
    "id": CID,
    "article_id": AID,
    "author_id": AUTHOR,
    "author_email": "a@test",
    "author_name": None,
    "body": "looks good",
    "anchor": None,
    "resolved": False,
    "created_at": "2026-06-20T00:00:00Z",
    "updated_at": "2026-06-20T00:00:00Z",
}


def _client(db, user: CurrentUser | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    with_auth(app, user) if user else with_auth(app)
    return TestClient(app)


def test_add_comment_201(monkeypatch):
    monkeypatch.setattr(csvc, "create_comment", lambda *a, **k: COMMENT)
    resp = _client(MagicMock()).post(
        f"/api/articles/{AID}/comments", json={"body": "looks good"}
    )
    assert resp.status_code == 201
    assert resp.json()["body"] == "looks good"


def test_list_comments(monkeypatch):
    monkeypatch.setattr(csvc, "list_comments", lambda d, aid: [COMMENT])
    resp = _client(MagicMock()).get(f"/api/articles/{AID}/comments")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_edit_body_is_author_only(monkeypatch):
    monkeypatch.setattr(csvc, "get_comment", lambda d, cid: COMMENT)
    resp = _client(MagicMock(), OTHER).patch(
        f"/api/articles/{AID}/comments/{CID}", json={"body": "edited"}
    )
    assert resp.status_code == 403


def test_resolve_open_to_any_reviewer(monkeypatch):
    monkeypatch.setattr(csvc, "get_comment", lambda d, cid: COMMENT)
    monkeypatch.setattr(
        csvc, "update_comment", lambda d, cid, f: {**COMMENT, "resolved": True}
    )
    resp = _client(MagicMock(), OTHER).patch(
        f"/api/articles/{AID}/comments/{CID}", json={"resolved": True}
    )
    assert resp.status_code == 200
    assert resp.json()["resolved"] is True


def test_delete_by_author(monkeypatch):
    monkeypatch.setattr(csvc, "get_comment", lambda d, cid: COMMENT)
    monkeypatch.setattr(csvc, "delete_comment", lambda d, cid: True)
    author = CurrentUser(id=AUTHOR, role="writer")
    resp = _client(MagicMock(), author).delete(f"/api/articles/{AID}/comments/{CID}")
    assert resp.status_code == 204


def test_delete_forbidden_for_other_writer(monkeypatch):
    monkeypatch.setattr(csvc, "get_comment", lambda d, cid: COMMENT)
    resp = _client(MagicMock(), OTHER).delete(f"/api/articles/{AID}/comments/{CID}")
    assert resp.status_code == 403


def test_comment_wrong_article_404(monkeypatch):
    monkeypatch.setattr(csvc, "get_comment", lambda d, cid: COMMENT)
    wrong = "11111111-1111-1111-1111-111111111111"
    resp = _client(MagicMock(), OTHER).delete(
        f"/api/articles/{wrong}/comments/{CID}"
    )
    assert resp.status_code == 404
