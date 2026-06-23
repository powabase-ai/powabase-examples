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
    """The authenticated caller, resolved from a verified GoTrue token."""

    id: UUID
    email: str | None = None
    role: str


class Profile(BaseModel):
    id: UUID
    email: str | None = None
    display_name: str | None = None
    role: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RoleUpdate(BaseModel):
    role: str  # writer|editor|admin
