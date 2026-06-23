"""Profile / membership schemas.

`public.profiles` mirrors `auth.users` and carries the app role
(`writer` < `editor` < `admin`). Roles drive the editorial workflow: only
editors/admins may approve or publish; only admins may change roles.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

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


class Profile(BaseModel):
    id: UUID
    email: str | None = None
    display_name: str | None = None
    role: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RoleUpdate(BaseModel):
    role: str  # writer|editor|admin


class Organization(BaseModel):
    id: UUID
    name: str
    created_at: datetime | None = None


class OrgInviteCreate(BaseModel):
    email: str
    role: str = "writer"  # writer|editor|admin


class OrgInvite(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    role: str
    invited_by: UUID | None = None
    created_at: datetime | None = None
    accepted_at: datetime | None = None
