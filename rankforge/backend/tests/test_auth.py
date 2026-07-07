"""Auth — JWT verification, JIT profile provisioning, and role gating."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import jwt
import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend import auth
from rankforge_backend.auth import get_current_user  # noqa: F401  (re-exported)
from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.business_profiles import get_db

SECRET = "test-secret"
UID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ORG = UUID(ADMIN_ORG)
NEW_ORG = "00000000-0000-0000-0000-0000000000b0"
INVITE_ORG = "00000000-0000-0000-0000-0000000000c0"


def _token(secret: str = SECRET, **claims) -> str:
    payload = {"sub": UID, "email": "u@test", "aud": "authenticated", **claims}
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(powabase_jwt_secret=SECRET, signup_invite_code=""),
    )


def _full_profile(role: str, org_id=ORG) -> dict:
    return {
        "id": UID,
        "email": "u@test",
        "display_name": None,
        "role": role,
        "org_id": org_id,
        "created_at": "2026-06-20T00:00:00Z",
        "updated_at": "2026-06-20T00:00:00Z",
    }


def _cursor_db(fetchone_seq: list) -> tuple[MagicMock, MagicMock]:
    """Build a db whose connection().cursor() yields a cursor returning
    `fetchone_seq` in order. Returns (db, cursor)."""
    db = MagicMock()
    db.fetch_one.return_value = None  # no provisioned profile yet
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetchone_seq)
    conn = db.connection.return_value.__enter__.return_value
    conn.cursor.return_value.__enter__.return_value = cur
    return db, cur


# --- ensure_profile (unit) ---
def test_ensure_profile_returns_existing():
    # A profile that already has an org short-circuits via the first fetch_one —
    # no transaction / advisory lock is taken.
    db = MagicMock()
    db.fetch_one.return_value = _full_profile("editor")
    prof = auth.ensure_profile(db, UID, "u@test")
    assert prof["role"] == "editor"
    assert prof["org_id"] == ORG
    db.fetch_one.assert_called_once()
    db.connection.assert_not_called()


def test_ensure_profile_new_user_creates_solo_org_as_admin():
    # A brand-new user: inside the advisory-locked txn we re-check (no profile),
    # create a fresh org, and upsert the profile as that org's admin. Crucially we
    # NEVER consult org_invites — email is not trusted for cross-org placement
    # (joining another org is an explicit, token-authorized accept; see test_org).
    db, cur = _cursor_db(
        [
            None,  # re-select inside lock: still no profile
            {"id": NEW_ORG},  # insert organizations ... returning id
            _full_profile("admin", NEW_ORG),  # final profile upsert RETURNING
        ]
    )
    prof = auth.ensure_profile(db, UID, "u@test")

    assert prof["role"] == "admin"
    assert prof["org_id"] == NEW_ORG
    executed = " ".join(c.args[0].lower() for c in cur.execute.call_args_list)
    assert "pg_advisory_xact_lock" in executed
    assert "insert into public.organizations" in executed
    assert "insert into public.profiles" in executed
    # Security: provisioning must not auto-claim invites by email.
    assert "org_invites" not in executed


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
    writer = CurrentUser(id=UID, email="w@test", role="writer", org_id=ORG)
    resp = _as(MagicMock(), writer).patch(f"/api/members/{UID}", json={"role": "editor"})
    assert resp.status_code == 403


def test_set_role_admin_ok():
    db = MagicMock()
    # Demotion runs in a locking transaction: lock+read the org's admin rows, then
    # UPDATE ... returning.
    cur = (
        db.connection.return_value.__enter__.return_value
        .cursor.return_value.__enter__.return_value
    )
    cur.fetchall.return_value = [{"id": UID, "role": "editor"}]  # target, not an admin
    cur.fetchone.return_value = _full_profile("editor")
    admin = CurrentUser(id=UID, email="a@test", role="admin", org_id=ORG)
    resp = _as(db, admin).patch(f"/api/members/{UID}", json={"role": "editor"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"


def test_writer_cannot_approve_article():
    # The org guard passes (the brand is in the caller's org); the 403 is the
    # editorial gate on the approve transition.
    db = MagicMock()
    db.fetch_one.return_value = {"id": UID, "business_id": UID, "org_id": ORG}
    writer = CurrentUser(id=UID, email="w@test", role="writer", org_id=ORG)
    resp = _as(db, writer).patch(
        f"/api/articles/{UID}", json={"status": "approved"}
    )
    assert resp.status_code == 403


def test_editor_can_approve_article():
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": UID,
        "business_id": UID,
        "org_id": ORG,
        "title": "T",
        "status": "approved",
        "generation_status": "done",
        "created_at": "2026-06-20T00:00:00Z",
        "updated_at": "2026-06-20T00:00:00Z",
    }
    editor = CurrentUser(id=UID, email="e@test", role="editor", org_id=ORG)
    resp = _as(db, editor).patch(f"/api/articles/{UID}", json={"status": "approved"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
