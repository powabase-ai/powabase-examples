"""Organization + invite reads/writes (direct Postgres).

An org is the tenant boundary (see schema/0011). Admins invite teammates by email;
a pending invite is claimed on that teammate's first sign-in (see auth.ensure_profile).
"""

from typing import Any
from uuid import UUID

from ..db import Database

_INVITE_COLS = (
    "id, org_id, email, role, invited_by, created_at, accepted_at"
)


def get_org(db: Database, org_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        "select id, name, created_at from public.organizations where id = %s",
        (org_id,),
    )


def rename_org(db: Database, org_id: UUID, name: str) -> dict[str, Any] | None:
    return db.fetch_one(
        "update public.organizations set name = %s where id = %s "
        "returning id, name, created_at",
        (name, org_id),
    )


def list_invites(db: Database, org_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_INVITE_COLS} from public.org_invites "
        "where org_id = %s order by created_at desc",
        (org_id,),
    )


class InviteConflict(ValueError):
    """A pending invite already exists for this email (across any org)."""


class MemberExists(ValueError):
    """A user with this email is already a member of some org."""


def create_invite(
    db: Database, org_id: UUID, email: str, role: str, invited_by: UUID
) -> dict[str, Any]:
    email = email.strip()
    # Already a member somewhere? Inviting them would never be claimed.
    if db.fetch_one(
        "select 1 from public.profiles where lower(email) = lower(%s) limit 1",
        (email,),
    ):
        raise MemberExists(f"{email} already has an account")
    # The partial unique index enforces one pending invite per email install-wide.
    row = db.fetch_one(
        f"""insert into public.org_invites (org_id, email, role, invited_by)
            values (%s, %s, %s, %s) returning {_INVITE_COLS}""",
        (org_id, email, role, invited_by),
    )
    return row


def revoke_invite(db: Database, org_id: UUID, invite_id: UUID) -> bool:
    row = db.fetch_one(
        "delete from public.org_invites where id = %s and org_id = %s "
        "and accepted_at is null returning id",
        (invite_id, org_id),
    )
    return row is not None
