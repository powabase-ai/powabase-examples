"""End-user authentication and tenant resolution.

The frontend signs in against the Powabase project's GoTrue endpoint with the
Anon key and sends the resulting access token as `Authorization: Bearer <jwt>`
on every backend call. We verify that token here (HS256, signed with the
project's JWT secret) and resolve the caller's app role AND organization from
`public.profiles`, JIT-provisioning a profile + org the first time we see a user.

Tenancy: every user belongs to exactly one organization (`profiles.org_id`).
On first sign-in we either (a) accept a pending `org_invites` row matched on the
user's email — joining that org with the invited role — or (b) create a fresh
org and make the user its `admin`. The org creator is therefore always an admin.

The GoTrue `role` claim ("authenticated") is the Postgres role — NOT our app
role; the app role lives in `public.profiles.role`.
"""

from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, status

from .config import get_settings
from .db import Database
from .models.profile import CurrentUser
from .routes.deps import get_db

_PROFILE_COLS = "id, email, display_name, role, org_id, invite_verified"


def _decode(token: str) -> dict:
    secret = get_settings().powabase_jwt_secret
    if not secret:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "auth not configured"
        )
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            leeway=10,  # tolerate minor clock skew
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from e


def ensure_profile(db: Database, user_id: str, email: str | None) -> dict:
    """Return the caller's profile (with org), provisioning it on first sight.

    On first sign-in the user gets their OWN fresh organization and becomes its
    `admin`. Joining someone else's org is a separate, explicit, token-authorized
    step (`POST /api/org/invites/accept`) — we never place a user in another org
    based on their (unverified) email claim. See schema/0013.

    Provisioning is serialized per-user with a transaction advisory lock and the
    org is re-checked inside the lock, so two concurrent first sign-ins can't
    create two orgs for the same user. (Legacy rows from before multi-org may have
    a NULL org_id; they are healed through the same path.)"""
    row = db.fetch_one(
        f"select {_PROFILE_COLS} from public.profiles where id = %s",
        (user_id,),
    )
    if row and row.get("org_id"):
        return row
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"prov:{user_id}",))
        cur.execute(
            f"select {_PROFILE_COLS} from public.profiles where id = %s", (user_id,)
        )
        existing = cur.fetchone()
        if existing and existing.get("org_id"):
            return existing

        org_name = (
            f"{email.split('@', 1)[0]}'s workspace" if email else "My workspace"
        )
        cur.execute(
            "insert into public.organizations (name) values (%s) returning id",
            (org_name,),
        )
        org_id = cur.fetchone()["id"]
        cur.execute(
            f"""insert into public.profiles (id, email, org_id, role)
                values (%s, %s, %s, 'admin')
                on conflict (id) do update set
                    email = excluded.email,
                    org_id = coalesce(profiles.org_id, excluded.org_id),
                    role = case when profiles.org_id is null
                                then excluded.role else profiles.role end
                returning {_PROFILE_COLS}""",
            (user_id, email, org_id),
        )
        return cur.fetchone()


def get_current_user_unverified(
    authorization: str | None = Header(default=None),
    db: Database = Depends(get_db),
) -> CurrentUser:
    """Resolve the caller from a valid GoTrue token WITHOUT enforcing the signup invite
    gate. Only the two endpoints that must work before verification depend on this:
    `GET /api/me` (so the frontend can learn it needs a code) and the redeem endpoint."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    claims = _decode(token)
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing subject")
    profile = ensure_profile(db, user_id, claims.get("email"))
    return CurrentUser(
        id=profile["id"],
        email=profile.get("email"),
        role=profile["role"],
        org_id=profile["org_id"],
        invite_verified=bool(profile.get("invite_verified", True)),
    )


def signup_gate_active() -> bool:
    """The invite gate is enforced only when a shared code is configured (SIGNUP_INVITE_
    CODE). Empty = open signup (dev/default), so unset envs keep working as before."""
    return bool(get_settings().signup_invite_code)


def get_current_user(
    user: CurrentUser = Depends(get_current_user_unverified),
) -> CurrentUser:
    """The default auth dependency for every feature route. Enforces the signup invite
    gate: a registered-but-unredeemed account is refused until it redeems the code once.
    403 (not 401) so the client keeps its session and can show the code screen."""
    if signup_gate_active() and not user.invite_verified:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "invite code required to complete signup",
        )
    return user


def require_editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Editor-or-admin gate for destructive/editorial actions (approve, publish,
    delete sources)."""
    if user.role not in ("editor", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "editor role required")
    return user


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return user


def assert_brand_access(db: Database, business_id: UUID, user: CurrentUser) -> None:
    """Guard a business-scoped route: 404 unless the brand is in the caller's org.

    We return 404 (not 403) so a caller can't probe which brand ids exist in other
    orgs. Routes that take a `business_id` call this before doing any work; routes
    keyed by a content id resolve the owning business first (see the per-entity
    `assert_*_access` helpers in the route modules)."""
    row = db.fetch_one(
        "select org_id from public.business_profiles where id = %s",
        (business_id,),
    )
    if row is None or row["org_id"] != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
