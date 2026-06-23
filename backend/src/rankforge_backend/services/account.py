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


def set_role(db: Database, user_id: UUID, role: str) -> dict[str, Any] | None:
    return db.fetch_one(
        f"update public.profiles set role = %s, updated_at = now() "
        f"where id = %s returning {_PROFILE_COLS}",
        (role, user_id),
    )
