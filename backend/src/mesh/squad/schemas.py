"""Pydantic v2 request models for the squad module (squad.md §3).

IDs travel as strings (parsed to UUID in the route layer so malformed ids map to
the correct 404/400). Enums are constrained with ``Field(pattern=...)``. The
service layer re-validates everything so direct (non-HTTP) callers get identical
error codes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

_KIND = r"^(standing|adhoc|task_scoped)$"
_LEADER_MODE = r"^(single|multi)$"
_ROLE = r"^(leader|member|observer)$"
_MSG_KIND = r"^(chat|instruction|report|system|context)$"


class SquadMemberInput(BaseModel):
    """One member entry when creating / adding to a squad."""

    member_id: str
    role: str = Field(default="member", pattern=_ROLE)
    # member_type is an OPTIONAL client hint; the server resolves the truth via
    # members.member_type (README §6.1 — never trusted, never stored).
    member_type: str | None = None


class CreateSquadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = None
    instructions: str | None = None
    avatar_url: str | None = None
    kind: str = Field(default="standing", pattern=_KIND)
    leader_mode: str = Field(default="single", pattern=_LEADER_MODE)
    require_plan_approval: bool = False
    max_decompose_depth: int = Field(default=2, ge=1, le=4)
    members: list[SquadMemberInput] = Field(default_factory=list)


class UpdateSquadRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None
    instructions: str | None = None
    avatar_url: str | None = None
    kind: str | None = Field(default=None, pattern=_KIND)
    leader_mode: str | None = Field(default=None, pattern=_LEADER_MODE)
    require_plan_approval: bool | None = None
    max_decompose_depth: int | None = Field(default=None, ge=1, le=4)
    primary_leader_id: str | None = None


class AddMembersRequest(BaseModel):
    members: list[SquadMemberInput] = Field(min_length=1)


class ChangeRoleRequest(BaseModel):
    role: str = Field(pattern=_ROLE)


class AssignTaskRequest(BaseModel):
    issue_id: str
    brief: str | None = None
    priority: str | None = None
    due_date: str | None = None


class SubtaskInput(BaseModel):
    title: str = Field(min_length=1)
    assignee: SquadMemberInput | None = None
    stage: int | None = None
    depends_on: list[str] = Field(default_factory=list)


class CreateSubtasksRequest(BaseModel):
    plan_markdown: str | None = None
    subtasks: list[SubtaskInput] = Field(min_length=1)


class DispatchRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list)


class CancelRequest(BaseModel):
    reason: str | None = None


_TASK_STATUS = (
    r"^(pending|decomposing|awaiting_plan_approval|dispatching|in_progress|"
    r"blocked|aggregating|done|failed|cancelled)$"
)


class TaskStatusUpdateRequest(BaseModel):
    """Manual kanban status move (§4.2): server validates the transition."""

    status: str = Field(pattern=_TASK_STATUS)
    result_summary: str | None = None


class PlanDecisionRequest(BaseModel):
    comment: str | None = None


class SendMessageRequest(BaseModel):
    task_id: str | None = None
    recipient: SquadMemberInput | None = None
    kind: str = Field(default="chat", pattern=_MSG_KIND)
    body_markdown: str = Field(min_length=1)
    attachment_ids: list[str] = Field(default_factory=list)
    pinned: bool = False


__all__ = [
    "SquadMemberInput",
    "CreateSquadRequest",
    "UpdateSquadRequest",
    "AddMembersRequest",
    "ChangeRoleRequest",
    "AssignTaskRequest",
    "SubtaskInput",
    "CreateSubtasksRequest",
    "DispatchRequest",
    "CancelRequest",
    "TaskStatusUpdateRequest",
    "PlanDecisionRequest",
    "SendMessageRequest",
]
