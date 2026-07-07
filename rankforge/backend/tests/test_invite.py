"""Signup invite-code gate — the shared-code redemption that completes registration."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rankforge_backend import auth, ratelimit
from rankforge_backend.auth import get_current_user_unverified
from rankforge_backend.config import get_settings
from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.deps import get_db

UID = "00000000-0000-0000-0000-000000000009"
ORG = "00000000-0000-0000-0000-0000000000a0"
CODE = "forge-s3cret"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # These tests drive the REAL config.get_settings (shared by auth/account/ratelimit),
    # toggled via the env var + cache clear — so leave the app's env otherwise hermetic.
    monkeypatch.setenv("POWABASE_DATABASE_URL", "")
    ratelimit.reset()
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    ratelimit.reset()


def _user(verified: bool) -> CurrentUser:
    return CurrentUser(
        id=UID, email="new@test", role="admin", org_id=ORG, invite_verified=verified
    )


def _profile(verified: bool) -> dict:
    return {
        "id": UID, "email": "new@test", "display_name": None, "role": "admin",
        "invite_verified": verified,
        "created_at": "2026-07-06T00:00:00Z", "updated_at": "2026-07-06T00:00:00Z",
    }


def _client(db, user, monkeypatch, code: str = CODE) -> TestClient:
    monkeypatch.setenv("SIGNUP_INVITE_CODE", code)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_unverified] = lambda: user
    return TestClient(app)


# --- the gate itself (unit) ---
def test_gate_blocks_unverified_when_code_configured(monkeypatch):
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(signup_invite_code=CODE)
    )
    with pytest.raises(HTTPException) as ei:
        auth.get_current_user(_user(verified=False))
    assert ei.value.status_code == 403


def test_gate_allows_verified_account(monkeypatch):
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(signup_invite_code=CODE)
    )
    u = _user(verified=True)
    assert auth.get_current_user(u) is u


def test_gate_disabled_allows_everyone(monkeypatch):
    # No code configured → open signup; an unverified account passes straight through.
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(signup_invite_code="")
    )
    u = _user(verified=False)
    assert auth.get_current_user(u) is u


# --- redeem endpoint ---
def test_redeem_correct_code_verifies(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = _profile(True)  # mark_invite_verified RETURNING
    client = _client(db, _user(verified=False), monkeypatch)
    resp = client.post("/api/auth/redeem-invite", json={"code": CODE})
    assert resp.status_code == 200
    assert resp.json()["invite_verified"] is True


def test_redeem_wrong_code_403_and_no_write(monkeypatch):
    db = MagicMock()
    client = _client(db, _user(verified=False), monkeypatch)
    resp = client.post("/api/auth/redeem-invite", json={"code": "not-the-code"})
    assert resp.status_code == 403
    db.fetch_one.assert_not_called()  # never flipped the flag


def test_redeem_is_idempotent_when_already_verified(monkeypatch):
    # "Just once": an already-verified account short-circuits — no code needed, no error.
    db = MagicMock()
    db.fetch_one.return_value = _profile(True)  # get_profile
    client = _client(db, _user(verified=True), monkeypatch)
    resp = client.post("/api/auth/redeem-invite", json={"code": "anything"})
    assert resp.status_code == 200
    assert resp.json()["invite_verified"] is True


# --- /api/me reports the effective gate so the frontend knows to show the code screen ---
def test_me_reports_unverified_when_gated(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = _profile(False)
    client = _client(db, _user(verified=False), monkeypatch)
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["invite_verified"] is False


def test_me_effective_verified_when_gate_off(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = _profile(False)  # raw false in DB...
    client = _client(db, _user(verified=False), monkeypatch, code="")  # ...but gate off
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["invite_verified"] is True  # effective = verified OR gate-off


# --- the gate actually protects a feature route ---
def test_feature_route_403_for_unverified(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": ORG}
    client = _client(db, _user(verified=False), monkeypatch)
    # /api/business-profiles depends on the GATED get_current_user (which chains off the
    # overridden unverified dep) → 403 before any handler work.
    resp = client.get("/api/business-profiles")
    assert resp.status_code == 403
