"""Request schemas for the workspace REST API (workspace.md §3).

Field-level format checks mirror the service-layer validators; the service
re-validates so direct (non-route) callers get identical error codes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from mesh.workspace.invitations import MAX_BATCH_EMAILS
from mesh.workspace.service import SLUG_PATTERN


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(pattern=SLUG_PATTERN)
    timezone: str = "UTC"
    logo_url: str | None = None
    settings: dict | None = None


class UpdateWorkspaceRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    slug: str | None = Field(default=None, pattern=SLUG_PATTERN)
    logo_url: str | None = None
    timezone: str | None = None
    settings: dict | None = None


class DeleteWorkspaceRequest(BaseModel):
    """Dangerous action second confirmation (workspace.md §4.2/§5.3)."""

    confirm_slug: str


class CreateInvitationRequest(BaseModel):
    emails: list[str] | None = Field(default=None, max_length=MAX_BATCH_EMAILS)
    role: str = "member"
    max_uses: int | None = Field(default=None, gt=0)
    expires_in_hours: int | None = Field(default=None, gt=0)


class AcceptInvitationRequest(BaseModel):
    token: str


class ChangeRoleRequest(BaseModel):
    role: str
