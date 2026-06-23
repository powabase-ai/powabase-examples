"""End-user authentication.

The frontend signs in against the Powabase project's GoTrue endpoint with the
Anon key and sends the resulting access token as `Authorization: Bearer <jwt>`
on every backend call. We verify that token here (HS256, signed with the
project's JWT secret) and resolve the caller's app role from `public.profiles`,
JIT-provisioning a profile row the first time we see a user.

The very first profile created in a fresh workspace is promoted to `admin` so
there is always someone who can assign roles. The GoTrue `role` claim
("authenticated") is the Postgres role — NOT our app role; the app role lives in
`public.profiles.role`.
"""

import jwt
from fastapi import Depends, Header, HTTPException, status

from .config import get_settings
from .db import Database
from .models.profile import CurrentUser
from .routes.deps import get_db


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
    """Return the caller's profile, creating it on first sight."""
    row = db.fetch_one(
        "select id, email, display_name, role from public.profiles where id = %s",
        (user_id,),
    )
    if row:
        return row
    first = db.fetch_one(
        "select not exists (select 1 from public.profiles) as is_first"
    )
    role = "admin" if (first and first["is_first"]) else "writer"
    return db.fetch_one(
        "insert into public.profiles (id, email, role) values (%s, %s, %s) "
        "on conflict (id) do update set email = excluded.email "
        "returning id, email, display_name, role",
        (user_id, email, role),
    )


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Database = Depends(get_db),
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    claims = _decode(token)
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing subject")
    profile = ensure_profile(db, user_id, claims.get("email"))
    return CurrentUser(
        id=profile["id"], email=profile.get("email"), role=profile["role"]
    )


def require_editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Gate actions that move an article forward (approve / publish)."""
    if user.role not in ("editor", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "editor role required")
    return user


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return user
