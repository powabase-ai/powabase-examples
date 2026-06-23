"""Org + invite endpoints — hermetic, Database mocked at the boundary."""

from unittest.mock import MagicMock

from conftest import with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db

NEW_ORG = "00000000-0000-0000-0000-0000000000d0"
OLD_ORG = "00000000-0000-0000-0000-0000000000e0"


def _client(db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(with_auth(app))


def _cursor_db(fetchone_seq: list) -> tuple[MagicMock, MagicMock]:
    db = MagicMock()
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetchone_seq)
    conn = db.connection.return_value.__enter__.return_value
    conn.cursor.return_value.__enter__.return_value = cur
    return db, cur


def test_create_invite_returns_token():
    db = MagicMock()
    invite_row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "org_id": NEW_ORG,
        "email": "teammate@acme.com",
        "role": "editor",
        "token": "s3cr3t-token",
        "invited_by": None,
        "created_at": "2026-06-23T00:00:00Z",
        "accepted_at": None,
    }
    # create_invite: member-exists check (None) then the insert RETURNING row.
    db.fetch_one.side_effect = [None, invite_row]
    resp = _client(db).post(
        "/api/org/invites", json={"email": "teammate@acme.com", "role": "editor"}
    )
    assert resp.status_code == 201
    assert resp.json()["token"] == "s3cr3t-token"  # token returned to the admin


def test_create_invite_existing_member_409():
    db = MagicMock()
    db.fetch_one.return_value = {"exists": 1}  # member-exists check truthy
    resp = _client(db).post("/api/org/invites", json={"email": "dup@acme.com"})
    assert resp.status_code == 409


def test_accept_invite_moves_user_into_org():
    db, _cur = _cursor_db(
        [
            {"id": "inv-1", "org_id": NEW_ORG, "role": "editor"},  # invite (FOR UPDATE)
            {"org_id": OLD_ORG},  # caller's current (solo) org
            {"empty": True},  # old org now empty -> cleaned up
            {"id": NEW_ORG, "name": "Acme", "created_at": "2026-06-23T00:00:00Z"},
        ]
    )
    resp = _client(db).post("/api/org/invites/accept", json={"token": "good-token"})
    assert resp.status_code == 200
    assert resp.json()["id"] == NEW_ORG


def test_accept_invite_bad_token_404():
    db, _cur = _cursor_db([None])  # no matching unused invite
    resp = _client(db).post("/api/org/invites/accept", json={"token": "nope"})
    assert resp.status_code == 404
