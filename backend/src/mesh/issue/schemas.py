"""Pydantic request models for the issue API (issue.md §3).

Responses are plain dicts rendered by the service (§6.14 envelopes are
assembled in the routes). Field names mirror the API contract verbatim
(snake_case).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


def _uuid_or_none(raw: str | None, *, field: str) -> str | None:  # pragma: no cover
    return raw


class CreateIssueRequest(BaseModel):
    """POST /workspaces/{ws}/issues (issue.md §3.3)."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    project_id: str | None = None
    status_id: str | None = None
    priority: str = "none"
    assignee_id: str | None = None
    reporter_id: str | None = None
    estimate: Decimal | None = None
    estimate_unit: str | None = None
    due_date: date | None = None
    start_date: date | None = None
    milestone_id: str | None = None
    cycle_id: str | None = None
    parent_id: str | None = None
    position: float | None = None


class UpdateIssueRequest(BaseModel):
    """PATCH /issues/{id} — every field optional; presence via model_fields_set.

    ``version`` carries the optimistic-concurrency expectation (issue.md
    §3.4); ``If-Match: <updated_at>`` is also honored at the route level
    (README §6.14).
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    project_id: str | None = None
    status_id: str | None = None
    priority: str | None = None
    assignee_id: str | None = None
    reporter_id: str | None = None
    estimate: Decimal | None = None
    estimate_unit: str | None = None
    due_date: date | None = None
    start_date: date | None = None
    milestone_id: str | None = None
    cycle_id: str | None = None
    parent_id: str | None = None
    position: float | None = None
    version: int | None = None


class CreateDependencyRequest(BaseModel):
    """POST /issues/{id}/dependencies (issue.md §3.3)."""

    depends_on_id: str
    type: str = "relates_to"


class MovePreviewRequest(BaseModel):
    """POST /issues/{id}/move-preview (issue.md §3.8)."""

    target_project_id: str | None = None


class MoveRequest(BaseModel):
    """POST /issues/{id}/move — requires ``confirm: true`` (issue.md §3.8)."""

    target_project_id: str | None = None
    confirm: bool = False
    version: int | None = None


class BulkChanges(BaseModel):
    """Field set applicable in bulk (issue.md §1.2.5 / §5.5)."""

    status_id: str | None = None
    priority: str | None = None
    assignee_id: str | None = None
    cycle_id: str | None = None
    project_id: str | None = None


class BulkRequest(BaseModel):
    """POST /issues/bulk.

    ``delete: true`` bulk soft-deletes; otherwise ``changes`` is applied to
    every issue. A ``project_id`` change is a cross-project move and requires
    ``confirm: true`` (the §3.8 two-step contract applies per issue).
    """

    issue_ids: list[str] = Field(min_length=1, max_length=100)
    changes: BulkChanges | None = None
    delete: bool = False
    confirm: bool = False


class CreateStatusRequest(BaseModel):
    """POST /workspaces/{ws}/statuses."""

    name: str = Field(min_length=1, max_length=50)
    category: str
    color: str | None = None
    position: float = 0.0
    is_default: bool = False
    project_id: str | None = None
    # 严格模式「允许的下一步」目标状态 id 列表(§4.4,迁移 0009)
    allowed_transitions: list[str] = Field(default_factory=list)


class UpdateStatusRequest(BaseModel):
    """PATCH /statuses/{id}."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = None
    position: float | None = None
    category: str | None = None
    is_default: bool | None = None
    allowed_transitions: list[str] | None = None


class CreateIssueTemplateRequest(BaseModel):
    """POST /workspaces/{ws}/issue-templates (issue.md §3.9)."""

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    template_body: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None


class UpdateIssueTemplateRequest(BaseModel):
    """PATCH /issue-templates/{id}."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    template_body: dict[str, Any] | None = None


class InstantiateIssueTemplateRequest(BaseModel):
    """POST /issue-templates/{id}/instantiate (issue.md §3.9)."""

    title: str = Field(min_length=1, max_length=255)
    overrides: dict[str, Any] | None = None


__all__ = [
    "BulkChanges",
    "BulkRequest",
    "CreateDependencyRequest",
    "CreateIssueRequest",
    "CreateIssueTemplateRequest",
    "CreateStatusRequest",
    "InstantiateIssueTemplateRequest",
    "MovePreviewRequest",
    "MoveRequest",
    "UpdateIssueRequest",
    "UpdateIssueTemplateRequest",
    "UpdateStatusRequest",
]
