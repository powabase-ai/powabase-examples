"""Profile / membership reads + role management."""

from typing import Any
from uuid import UUID

from ..db import Database

_PROFILE_COLS = (
    "id, email, display_name, role, invite_verified, created_at, updated_at"
)


def list_members(db: Database, org_id: UUID) -> list[dict[str, Any]]:
    """Members of the caller's org only."""
    return db.fetch_all(
        f"select {_PROFILE_COLS} from public.profiles "
        "where org_id = %s order by created_at",
        (org_id,),
    )


def get_profile(db: Database, user_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_PROFILE_COLS} from public.profiles where id = %s", (user_id,)
    )


def mark_invite_verified(db: Database, user_id: UUID) -> dict[str, Any] | None:
    """Flip the caller's signup gate to verified (idempotent). Called once, after the
    account presents the correct shared invite code."""
    return db.fetch_one(
        f"update public.profiles set invite_verified = true, updated_at = now() "
        f"where id = %s returning {_PROFILE_COLS}",
        (user_id,),
    )


class LastAdminError(ValueError):
    """Raised when a role change would leave the org with no admin."""


def set_role(
    db: Database, user_id: UUID, role: str, org_id: UUID
) -> dict[str, Any] | None:
    """Change a member's role within the caller's org. The target must be in the
    same org (cross-org targets resolve to None → 404), and an org must always
    keep at least one admin."""
    if role == "admin":
        # A promotion can never reduce the admin count — no guard, single statement.
        return db.fetch_one(
            f"update public.profiles set role = %s, updated_at = now() "
            f"where id = %s and org_id = %s returning {_PROFILE_COLS}",
            (role, user_id, org_id),
        )
    # Demotion: do the last-admin check AND the write in one transaction, locking the
    # org's admin rows (stable id order → no deadlock). This serializes concurrent
    # demotions of different admins so they can't each read admins>1 and both commit,
    # stranding the org with zero admins (a permanent lockout — require_admin could
    # never be satisfied again). A single conditional UPDATE wouldn't suffice: under
    # READ COMMITTED each statement's count subquery sees its own pre-commit snapshot.
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, role from public.profiles "
            "where org_id = %s and (id = %s or role = 'admin') "
            "order by id for update",
            (org_id, user_id),
        )
        rows = cur.fetchall()
        target = next((r for r in rows if str(r["id"]) == str(user_id)), None)
        if target is None:
            return None  # target not in this org → 404
        admins = sum(1 for r in rows if r["role"] == "admin")
        if target["role"] == "admin" and admins <= 1:
            raise LastAdminError("cannot demote the last remaining admin")
        cur.execute(
            f"update public.profiles set role = %s, updated_at = now() "
            f"where id = %s and org_id = %s returning {_PROFILE_COLS}",
            (role, user_id, org_id),
        )
        return cur.fetchone()
