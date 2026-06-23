"""Membership / role management — last-admin guard."""

from unittest.mock import MagicMock

import pytest

from rankforge_backend.services import account

UID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ORG = "00000000-0000-0000-0000-0000000000a0"


def test_set_role_blocks_demoting_the_last_admin():
    db = MagicMock()
    db.fetch_one.return_value = {"cur": "admin", "admins": 1}
    with pytest.raises(account.LastAdminError):
        account.set_role(db, UID, "writer", ORG)


def test_set_role_allows_demotion_when_another_admin_exists():
    db = MagicMock()
    db.fetch_one.side_effect = [
        {"cur": "admin", "admins": 2},
        {"id": UID, "role": "writer"},
    ]
    assert account.set_role(db, UID, "writer", ORG)["role"] == "writer"


def test_set_role_to_admin_skips_the_guard():
    db = MagicMock()
    db.fetch_one.return_value = {"id": UID, "role": "admin"}
    assert account.set_role(db, UID, "admin", ORG)["role"] == "admin"
    db.fetch_one.assert_called_once()  # no guard query for a promotion
