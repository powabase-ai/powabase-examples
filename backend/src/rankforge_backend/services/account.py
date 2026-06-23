"""Profile / membership reads + role management."""

from typing import Any
from uuid import UUID

from ..db import Database

_PROFILE_COLS = "id, email, display_name, role, created_at, updated_at"


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


class LastAdminError(ValueError):
    """Raised when a role change would leave the org with no admin."""


def set_role(
    db: Database, user_id: UUID, role: str, org_id: UUID
) -> dict[str, Any] | None:
    """Change a member's role within the caller's org. The target must be in the
    same org (cross-org targets resolve to None → 404), and an org must always
    keep at least one admin."""
    if role != "admin":
        guard = db.fetch_one(
            "select (select role from public.profiles where id = %s and org_id = %s) "
            "as cur, (select count(*) from public.profiles "
            "where role = 'admin' and org_id = %s) as admins",
            (user_id, org_id, org_id),
        )
        if guard and guard["cur"] == "admin" and (guard["admins"] or 0) <= 1:
            raise LastAdminError("cannot demote the last remaining admin")
    return db.fetch_one(
        f"update public.profiles set role = %s, updated_at = now() "
        f"where id = %s and org_id = %s returning {_PROFILE_COLS}",
        (role, user_id, org_id),
    )
