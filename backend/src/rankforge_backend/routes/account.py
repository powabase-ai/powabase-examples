"""Account + membership endpoints — current profile and role management."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user, require_admin
from ..db import Database
from ..models.profile import ROLES, CurrentUser, Profile, RoleUpdate
from ..services import account as svc
from .deps import get_db

router = APIRouter(prefix="/api", tags=["account"])


@router.get("/me", response_model=Profile)
def me(
    user: CurrentUser = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """The caller's profile (JIT-provisioned on first authenticated request)."""
    return svc.get_profile(db, user.id)


@router.get("/members", response_model=list[Profile])
def members(
    _: CurrentUser = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    return svc.list_members(db)


@router.patch("/members/{user_id}", response_model=Profile)
def set_member_role(
    user_id: UUID,
    payload: RoleUpdate,
    _: CurrentUser = Depends(require_admin),
    db: Database = Depends(get_db),
):
    if payload.role not in ROLES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"role must be one of {ROLES}"
        )
    row = svc.set_role(db, user_id, payload.role)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    return row
