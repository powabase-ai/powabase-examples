"""Membership / role management — last-admin guard."""

from unittest.mock import MagicMock

import pytest

from rankforge_backend.services import account

UID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ORG = "00000000-0000-0000-0000-0000000000a0"


def _demote_cursor(db: MagicMock) -> MagicMock:
    """The cursor used by the demote path's locking transaction."""
    return (
        db.connection.return_value.__enter__.return_value
        .cursor.return_value.__enter__.return_value
    )


def test_set_role_blocks_demoting_the_last_admin():
    db = MagicMock()
    cur = _demote_cursor(db)
    cur.fetchall.return_value = [{"id": UID, "role": "admin"}]  # the only admin
    with pytest.raises(account.LastAdminError):
        account.set_role(db, UID, "writer", ORG)
    # the guard locks the admin rows so a concurrent demotion can't race it
    assert "for update" in cur.execute.call_args_list[0].args[0].lower()


def test_set_role_allows_demotion_when_another_admin_exists():
    db = MagicMock()
    cur = _demote_cursor(db)
    cur.fetchall.return_value = [
        {"id": UID, "role": "admin"},
        {"id": OTHER, "role": "admin"},
    ]
    cur.fetchone.return_value = {"id": UID, "role": "writer"}
    assert account.set_role(db, UID, "writer", ORG)["role"] == "writer"
    sqls = " ".join(c.args[0].lower() for c in cur.execute.call_args_list)
    assert "for update" in sqls and "update public.profiles" in sqls


def test_set_role_404_when_target_not_in_org():
    db = MagicMock()
    cur = _demote_cursor(db)
    cur.fetchall.return_value = [{"id": OTHER, "role": "admin"}]  # target absent
    assert account.set_role(db, UID, "writer", ORG) is None


def test_set_role_to_admin_skips_the_guard():
    db = MagicMock()
    db.fetch_one.return_value = {"id": UID, "role": "admin"}
    assert account.set_role(db, UID, "admin", ORG)["role"] == "admin"
    db.fetch_one.assert_called_once()  # one UPDATE, no guard
    db.connection.assert_not_called()  # promotion opens no locking transaction
