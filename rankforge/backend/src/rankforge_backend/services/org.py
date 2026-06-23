"""Organization + invite reads/writes (direct Postgres).

An org is the tenant boundary (see schema/0011). Admins create an invite (which
mints a secret token); the invited person signs up normally (landing in their own
org) and then JOINS by presenting the token (`accept_invite`). The token — not the
email — is the authorization, so the flow is safe even when GoTrue auto-confirms
signups (see schema/0013).
"""

import secrets
from typing import Any
from uuid import UUID

from ..db import Database

_INVITE_COLS = (
    "id, org_id, email, role, token, invited_by, created_at, accepted_at"
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


class InviteInvalid(ValueError):
    """The presented invite token is unknown or already used."""


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
    token = secrets.token_urlsafe(24)
    row = db.fetch_one(
        f"""insert into public.org_invites (org_id, email, role, token, invited_by)
            values (%s, %s, %s, %s, %s) returning {_INVITE_COLS}""",
        (org_id, email, role, token, invited_by),
    )
    return row


def accept_invite(db: Database, user_id: UUID, token: str) -> dict[str, Any]:
    """Join the org named by a valid, unused invite token. Moves the caller into
    that org with the invited role (out of their own auto-created solo org), marks
    the invite accepted, and tidies up the now-empty solo org. Returns the joined
    organization. Possession of the token — not the email — is the authorization.

    Done in one transaction so a partial join can't strand the user."""
    token = (token or "").strip()
    if not token:
        raise InviteInvalid("invalid or expired invite")
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, org_id, role from public.org_invites "
            "where token = %s and accepted_at is null for update",
            (token,),
        )
        invite = cur.fetchone()
        if invite is None:
            raise InviteInvalid("invalid or expired invite")
        cur.execute(
            "select org_id from public.profiles where id = %s", (user_id,)
        )
        prof = cur.fetchone()
        old_org = prof["org_id"] if prof else None
        new_org = invite["org_id"]
        cur.execute(
            "update public.profiles set org_id = %s, role = %s, updated_at = now() "
            "where id = %s",
            (new_org, invite["role"], user_id),
        )
        cur.execute(
            "update public.org_invites set accepted_at = now() where id = %s",
            (invite["id"],),
        )
        # Best-effort: drop the caller's old solo org if it's now empty (no other
        # members, no brands) so abandoned single-user orgs don't pile up.
        if old_org and old_org != new_org:
            cur.execute(
                "select not exists(select 1 from public.profiles where org_id = %s) "
                "and not exists(select 1 from public.business_profiles "
                "where org_id = %s) as empty",
                (old_org, old_org),
            )
            if (cur.fetchone() or {}).get("empty"):
                cur.execute(
                    "delete from public.organizations where id = %s", (old_org,)
                )
        cur.execute(
            "select id, name, created_at from public.organizations where id = %s",
            (new_org,),
        )
        return cur.fetchone()


def revoke_invite(db: Database, org_id: UUID, invite_id: UUID) -> bool:
    row = db.fetch_one(
        "delete from public.org_invites where id = %s and org_id = %s "
        "and accepted_at is null returning id",
        (invite_id, org_id),
    )
    return row is not None
