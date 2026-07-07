"""Account + membership endpoints — current profile and role management."""

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import (
    get_current_user,
    get_current_user_unverified,
    require_admin,
    signup_gate_active,
)
from ..config import get_settings
from ..db import Database
from ..models.profile import (
    ROLES,
    CurrentUser,
    InviteRedeem,
    Profile,
    RoleUpdate,
)
from ..ratelimit import check as rate_limit_check
from ..services import account as svc
from .deps import get_db

router = APIRouter(prefix="/api", tags=["account"])


def _with_effective_gate(profile: dict) -> dict:
    """Report invite_verified as the EFFECTIVE access state the frontend gates on: true
    when the account is verified OR the gate is disabled (no code configured)."""
    profile["invite_verified"] = bool(profile.get("invite_verified")) or (
        not signup_gate_active()
    )
    return profile


@router.get("/me", response_model=Profile)
def me(
    user: CurrentUser = Depends(get_current_user_unverified),
    db: Database = Depends(get_db),
):
    """The caller's profile (JIT-provisioned on first authenticated request). Uses the
    UNVERIFIED dep so a gated (unredeemed) account can still fetch its profile and learn
    it must enter an invite code — every other route stays behind the verified gate."""
    profile = svc.get_profile(db, user.id)
    return _with_effective_gate(profile) if profile else profile


@router.post("/auth/redeem-invite", response_model=Profile)
def redeem_invite(
    payload: InviteRedeem,
    user: CurrentUser = Depends(get_current_user_unverified),
    db: Database = Depends(get_db),
):
    """Complete signup by redeeming the shared invite code (once). Requires a valid
    session but not prior verification. Rate-limited per account against brute force."""
    rate_limit_check("redeem_invite", str(user.id), get_settings().rate_limit_invite)
    # Gate off, or already verified → idempotent success (no code needed).
    if not signup_gate_active() or user.invite_verified:
        return _with_effective_gate(svc.get_profile(db, user.id))
    if not hmac.compare_digest(
        payload.code.strip(), get_settings().signup_invite_code
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid invite code")
    row = svc.mark_invite_verified(db, user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
    return _with_effective_gate(row)


@router.get("/members", response_model=list[Profile])
def members(
    user: CurrentUser = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    return svc.list_members(db, user.org_id)


@router.patch("/members/{user_id}", response_model=Profile)
def set_member_role(
    user_id: UUID,
    payload: RoleUpdate,
    admin: CurrentUser = Depends(require_admin),
    db: Database = Depends(get_db),
):
    if payload.role not in ROLES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"role must be one of {ROLES}"
        )
    try:
        row = svc.set_role(db, user_id, payload.role, admin.org_id)
    except svc.LastAdminError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    return row
