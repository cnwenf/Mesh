"""Pydantic request schemas for the project module (project.md §3).

Request-only schemas (member/workspace convention): UUIDs travel as strings
and are parsed in the routes (path → 404, body → 400); the service layer
re-validates every domain rule and raises the canonical error codes.
Tri-state PATCH semantics (omit / null / value) are resolved in the routes
via ``model_fields_set``.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class CreateProjectRequest(BaseModel):
    name: str
    key: str
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    status: str = "planning"
    visibility: str = "public"
    lead_member_id: str | None = None
    start_date: date | None = None
    target_date: date | None = None


class UpdateProjectRequest(BaseModel):
    """PATCH fields/status/visibility; tri-state resolved via model_fields_set."""

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    status: str | None = None
    health: str | None = None
    visibility: str | None = None
    lead_member_id: str | None = None
    start_date: date | None = None
    target_date: date | None = None


class AddProjectUpdateRequest(BaseModel):
    health: str | None = None
    status: str | None = None
    message: str | None = None


class CreateMilestoneRequest(BaseModel):
    title: str
    description: str | None = None
    target_date: date | None = None


class UpdateMilestoneRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    target_date: date | None = None
    state: str | None = None


class CreateCycleRequest(BaseModel):
    name: str
    starts_at: date
    ends_at: date
    project_id: str | None = None
    state: str = "planned"
    auto_roll: bool = False


class UpdateCycleRequest(BaseModel):
    name: str | None = None
    starts_at: date | None = None
    ends_at: date | None = None
    state: str | None = None
    auto_roll: bool | None = None


class AddProjectMemberRequest(BaseModel):
    member_id: str
    role: str = "member"


class UpdateProjectMemberRequest(BaseModel):
    role: str


class CreateProjectTemplateRequest(BaseModel):
    name: str
    template_body: dict


class UpdateProjectTemplateRequest(BaseModel):
    name: str | None = None
    template_body: dict | None = None


class InstantiateProjectTemplateRequest(BaseModel):
    name: str
    key: str
    overrides: dict | None = None
