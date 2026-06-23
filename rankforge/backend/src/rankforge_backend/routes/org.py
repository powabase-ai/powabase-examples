"""Organization + invite endpoints.

`GET /api/org` is readable by any member; invite management requires admin. An
invited teammate joins the org with the invited role on their first sign-in.
"""

from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user, require_admin
from ..db import Database
from ..models.profile import (
    ROLES,
    CurrentUser,
    Organization,
    OrgInvite,
    OrgInviteAccept,
    OrgInviteCreate,
)
from ..services import org as svc
from .deps import get_db

router = APIRouter(prefix="/api/org", tags=["org"])


@router.get("", response_model=Organization)
def get_org(
    user: CurrentUser = Depends(get_current_user), db: Database = Depends(get_db)
):
    row = svc.get_org(db, user.org_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
    return row


@router.post("/invites/accept", response_model=Organization)
def accept_invite(
    payload: OrgInviteAccept,
    user: CurrentUser = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Join an org by presenting a valid invite token (the token authorizes the
    join — the email is never trusted). The caller leaves their own solo org."""
    try:
        return svc.accept_invite(db, user.id, payload.token)
    except svc.InviteInvalid as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.get("/invites", response_model=list[OrgInvite])
def list_invites(
    admin: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)
):
    return svc.list_invites(db, admin.org_id)


@router.post(
    "/invites", response_model=OrgInvite, status_code=status.HTTP_201_CREATED
)
def create_invite(
    payload: OrgInviteCreate,
    admin: CurrentUser = Depends(require_admin),
    db: Database = Depends(get_db),
):
    if payload.role not in ROLES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"role must be one of {ROLES}"
        )
    try:
        return svc.create_invite(
            db, admin.org_id, payload.email, payload.role, admin.id
        )
    except svc.MemberExists as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except psycopg.errors.UniqueViolation as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a pending invite for this email already exists"
        ) from e


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(
    invite_id: UUID,
    admin: CurrentUser = Depends(require_admin),
    db: Database = Depends(get_db),
):
    if not svc.revoke_invite(db, admin.org_id, invite_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invite not found")
