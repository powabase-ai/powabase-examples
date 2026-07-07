"""Profile / membership schemas.

`public.profiles` mirrors `auth.users` and carries the app role
(`writer` < `editor` < `admin`). Roles drive the editorial workflow: only
editors/admins may approve or publish; only admins may change roles.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ROLES = ("writer", "editor", "admin")


class CurrentUser(BaseModel):
    """The authenticated caller, resolved from a verified GoTrue token.

    `org_id` is the tenant boundary: the caller may only ever see/touch rows in
    their own organization. It is resolved (and provisioned on first sign-in) in
    `auth.ensure_profile`.
    """

    id: UUID
    email: str | None = None
    role: str
    org_id: UUID
    # Whether this account has cleared the signup invite gate. Defaults True so any code
    # path that builds a CurrentUser without it (and dev with no gate) is never blocked.
    invite_verified: bool = True


class Profile(BaseModel):
    id: UUID
    email: str | None = None
    display_name: str | None = None
    role: str
    # Effective gate status the frontend reads: true when verified OR the gate is off.
    invite_verified: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InviteRedeem(BaseModel):
    code: str = Field(min_length=1, max_length=200)


class RoleUpdate(BaseModel):
    role: str  # writer|editor|admin


class Organization(BaseModel):
    id: UUID
    name: str
    created_at: datetime | None = None


class OrgInviteCreate(BaseModel):
    # Basic shape check (no email-validator dep) so a malformed value is a 422, not a
    # row that can never sign in. role is constrained instead of a free string.
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["writer", "editor", "admin"] = "writer"


class OrgInviteAccept(BaseModel):
    token: str


class OrgInvite(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    role: str
    # The secret join token — returned to the admin who created it (to share with
    # the invitee). Whoever holds it can join the org, so treat it like a password.
    token: str | None = None
    invited_by: UUID | None = None
    created_at: datetime | None = None
    accepted_at: datetime | None = None
