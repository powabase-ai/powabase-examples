"""Auth — JWT verification, JIT profile provisioning, and role gating."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest
from conftest import with_auth
from fastapi.testclient import TestClient

from rankforge_backend import auth
from rankforge_backend.auth import get_current_user  # noqa: F401  (re-exported)
from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.business_profiles import get_db

SECRET = "test-secret"
UID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _token(secret: str = SECRET, **claims) -> str:
    payload = {"sub": UID, "email": "u@test", "aud": "authenticated", **claims}
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(powabase_jwt_secret=SECRET)
    )


def _full_profile(role: str) -> dict:
    return {
        "id": UID,
        "email": "u@test",
        "display_name": None,
        "role": role,
        "created_at": "2026-06-20T00:00:00Z",
        "updated_at": "2026-06-20T00:00:00Z",
    }


# --- ensure_profile (unit) ---
def test_ensure_profile_returns_existing():
    db = MagicMock()
    db.fetch_one.return_value = _full_profile("editor")
    prof = auth.ensure_profile(db, UID, "u@test")
    assert prof["role"] == "editor"
    db.fetch_one.assert_called_once()


def test_ensure_profile_creates_new_via_locked_insert():
    # The admin-vs-writer decision now lives in SQL (atomic, advisory-locked), so
    # the unit test verifies the new-user path takes the lock + insert and returns
    # the cursor row; the role itself is a live-DB concern.
    db = MagicMock()
    db.fetch_one.return_value = None  # no existing profile
    cur = MagicMock()
    cur.fetchone.return_value = _full_profile("admin")
    conn = db.connection.return_value.__enter__.return_value
    conn.cursor.return_value.__enter__.return_value = cur

    prof = auth.ensure_profile(db, UID, "u@test")

    assert prof["role"] == "admin"
    executed = " ".join(c.args[0].lower() for c in cur.execute.call_args_list)
    assert "pg_advisory_xact_lock" in executed
    assert "insert into public.profiles" in executed


# --- token verification via /api/me ---
def _client(db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_me_with_valid_token():
    db = MagicMock()
    db.fetch_one.side_effect = [_full_profile("writer"), _full_profile("writer")]
    resp = _client(db).get("/api/me", headers={"Authorization": f"Bearer {_token()}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "writer"


def test_me_missing_token_401():
    assert _client(MagicMock()).get("/api/me").status_code == 401


def test_me_bad_signature_401():
    resp = _client(MagicMock()).get(
        "/api/me", headers={"Authorization": f"Bearer {_token(secret='wrong')}"}
    )
    assert resp.status_code == 401


# --- role gating (auth overridden; no token needed) ---
def _as(db, user: CurrentUser) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(with_auth(app, user))


def test_set_role_requires_admin():
    writer = CurrentUser(id=UID, email="w@test", role="writer")
    resp = _as(MagicMock(), writer).patch(f"/api/members/{UID}", json={"role": "editor"})
    assert resp.status_code == 403


def test_set_role_admin_ok():
    db = MagicMock()
    # set_role first runs a last-admin guard query, then the UPDATE.
    db.fetch_one.side_effect = [
        {"cur": "editor", "admins": 2},
        _full_profile("editor"),
    ]
    admin = CurrentUser(id=UID, email="a@test", role="admin")
    resp = _as(db, admin).patch(f"/api/members/{UID}", json={"role": "editor"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"


def test_writer_cannot_approve_article():
    writer = CurrentUser(id=UID, email="w@test", role="writer")
    resp = _as(MagicMock(), writer).patch(
        f"/api/articles/{UID}", json={"status": "approved"}
    )
    assert resp.status_code == 403


def test_editor_can_approve_article():
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": UID,
        "title": "T",
        "status": "approved",
        "generation_status": "done",
        "created_at": "2026-06-20T00:00:00Z",
        "updated_at": "2026-06-20T00:00:00Z",
    }
    editor = CurrentUser(id=UID, email="e@test", role="editor")
    resp = _as(db, editor).patch(f"/api/articles/{UID}", json={"status": "approved"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
