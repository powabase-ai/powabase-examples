"""Profile / membership reads + role management."""

from typing import Any
from uuid import UUID

from ..db import Database

_PROFILE_COLS = "id, email, display_name, role, created_at, updated_at"


def list_members(db: Database) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_PROFILE_COLS} from public.profiles order by created_at"
    )


def get_profile(db: Database, user_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_PROFILE_COLS} from public.profiles where id = %s", (user_id,)
    )


class LastAdminError(ValueError):
    """Raised when a role change would leave the workspace with no admin."""


def set_role(db: Database, user_id: UUID, role: str) -> dict[str, Any] | None:
    if role != "admin":
        guard = db.fetch_one(
            "select (select role from public.profiles where id = %s) as cur, "
            "(select count(*) from public.profiles where role = 'admin') as admins",
            (user_id,),
        )
        if guard and guard["cur"] == "admin" and (guard["admins"] or 0) <= 1:
            raise LastAdminError("cannot demote the last remaining admin")
    return db.fetch_one(
        f"update public.profiles set role = %s, updated_at = now() "
        f"where id = %s returning {_PROFILE_COLS}",
        (role, user_id),
    )
