"""Request schemas for the member REST API (member.md §3).

Field-level format checks mirror the service-layer validators; the service
re-validates so direct (non-route) callers get identical error codes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from mesh.db.models.member import MEMBER_ROLE_VALUES, MEMBER_STATUS_VALUES


class AddMemberRequest(BaseModel):
    member_type: str = Field(pattern="^(human|agent)$")
    user_id: str | None = None
    agent_id: str | None = None
    role: str = "member"


class UpdateMemberRequest(BaseModel):
    """PATCH members/{id} — role, status and/or display_override (any subset).

    ``display_override`` is tri-state: omitted = keep, ``null`` = clear the
    override, a string = set it. Pydantic cannot express that with a default of
    ``None`` (which would read as "clear" when omitted), so the route reads the
    raw payload via ``model_fields_set``.
    """

    role: str | None = None
    status: str | None = None
    display_override: str | None = None


class ReassignRequest(BaseModel):
    from_member_id: str
    to_member_id: str
    statuses: list[str] | None = None


class GrantProjectAccessRequest(BaseModel):
    project_id: str
    permission: str = "read"


__all__ = [
    "AddMemberRequest",
    "GrantProjectAccessRequest",
    "MEMBER_ROLE_VALUES",
    "MEMBER_STATUS_VALUES",
    "ReassignRequest",
    "UpdateMemberRequest",
]
