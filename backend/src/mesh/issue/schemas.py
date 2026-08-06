"""Pydantic request models for the issue API (issue.md §3).

Responses are plain dicts rendered by the service (§6.14 envelopes are
assembled in the routes). Field names mirror the API contract verbatim
(snake_case).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from mesh.errors import BusinessRuleError

# Storage-DoS guard (issue.md §3.3 / §3.9): long-text and JSON body fields
# carry a byte ceiling at the schema boundary — 1 MiB each. Bytes (not
# characters) so multibyte payloads cannot outgrow the storage budget, and
# a named-code 422 (not a request-shape 400) because the oversize body is a
# business-limit violation (§6.14 vocabulary). The envelope echoes ONLY the
# field name + limit, never the offending content.
LONG_TEXT_MAX_BYTES = 1_048_576
TEMPLATE_BODY_MAX_BYTES = 1_048_576


def _check_text_bytes(value: str | None, *, field: str, max_bytes: int) -> None:
    """Raise 422 ``field_too_large`` when ``value`` exceeds ``max_bytes`` UTF-8."""
    if value is not None and len(value.encode("utf-8")) > max_bytes:
        raise BusinessRuleError(
            f"{field} exceeds the {max_bytes}-byte limit",
            code="field_too_large",
            details={"field": field, "max_bytes": max_bytes},
        )


def _check_json_bytes(value: dict | None, *, field: str, max_bytes: int) -> None:
    """Raise 422 ``field_too_large`` when the canonical JSON encoding of
    ``value`` exceeds ``max_bytes`` (JSONB bodies are capped by their
    serialized size)."""
    if value is None:
        return
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > max_bytes:
        raise BusinessRuleError(
            f"{field} exceeds the {max_bytes}-byte limit",
            code="field_too_large",
            details={"field": field, "max_bytes": max_bytes},
        )


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

    @model_validator(mode="after")
    def _check_field_limits(self) -> CreateIssueRequest:
        _check_text_bytes(
            self.description, field="description", max_bytes=LONG_TEXT_MAX_BYTES
        )
        return self


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
    review_execution_id: str | None = None
    review_decision: str | None = None
    version: int | None = None

    @model_validator(mode="after")
    def _check_field_limits(self) -> UpdateIssueRequest:
        _check_text_bytes(
            self.description, field="description", max_bytes=LONG_TEXT_MAX_BYTES
        )
        review_fields = ("review_execution_id", "review_decision")
        present = [field for field in review_fields if field in self.model_fields_set]
        if present and len(present) != len(review_fields):
            raise BusinessRuleError(
                "execution output review requires an execution and decision",
                code="invalid_execution_output_review",
            )
        if self.review_decision is not None and self.review_decision not in {
            "approved",
            "rejected",
        }:
            raise BusinessRuleError(
                "invalid execution output review decision",
                code="invalid_execution_output_review",
            )
        return self


class CreateDependencyRequest(BaseModel):
    """POST /issues/{id}/dependencies (issue.md §3.3)."""

    depends_on_id: str
    type: str = "relates_to"


class MovePreviewRequest(BaseModel):
    """POST /issues/{id}/move-preview (issue.md §3.8)."""

    target_project_id: str | None = None


class MoveRequest(BaseModel):
    """POST /issues/{id}/move — requires ``confirm: true`` (issue.md §3.8).

    A CONFIRMED move must carry the current ``version`` (§3.8 step 2 乐观锁);
    the schema boundary enforces it (422 ``move_version_required``) so the
    OCC expectation can never be silently omitted. The unconfirmed path
    stays version-free on purpose: ``confirm`` defaulted away is the §3.8
    422-preview fallback (auth-first, returns ``details.preview``), and that
    envelope is exactly how clients that skipped step 1 learn the version to
    echo back.
    """

    target_project_id: str | None = None
    confirm: bool = False
    version: int | None = None

    @model_validator(mode="after")
    def _confirmed_move_requires_version(self) -> MoveRequest:
        if self.confirm and self.version is None:
            raise BusinessRuleError(
                "confirmed move requires the current version",
                code="move_version_required",
                details={"field": "version", "hint": "echo preview.version back"},
            )
        return self


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

    @model_validator(mode="after")
    def _check_field_limits(self) -> CreateIssueTemplateRequest:
        _check_text_bytes(
            self.description, field="description", max_bytes=LONG_TEXT_MAX_BYTES
        )
        _check_json_bytes(
            self.template_body, field="template_body", max_bytes=TEMPLATE_BODY_MAX_BYTES
        )
        return self


class UpdateIssueTemplateRequest(BaseModel):
    """PATCH /issue-templates/{id}."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    template_body: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_field_limits(self) -> UpdateIssueTemplateRequest:
        _check_text_bytes(
            self.description, field="description", max_bytes=LONG_TEXT_MAX_BYTES
        )
        _check_json_bytes(
            self.template_body, field="template_body", max_bytes=TEMPLATE_BODY_MAX_BYTES
        )
        return self


class InstantiateIssueTemplateRequest(BaseModel):
    """POST /issue-templates/{id}/instantiate (issue.md §3.9)."""

    title: str = Field(min_length=1, max_length=255)
    overrides: dict[str, Any] | None = None


__all__ = [
    "LONG_TEXT_MAX_BYTES",
    "TEMPLATE_BODY_MAX_BYTES",
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
