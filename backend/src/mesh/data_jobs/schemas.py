"""Request schemas for the data-jobs API (import-export.md §3.1–§3.6)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateImportJobRequest(BaseModel):
    """POST /data-jobs/import (§3.2)."""

    workspace_id: str
    entity_type: Literal["issues", "projects"] = "issues"
    format: Literal["csv", "json"] = "csv"
    source_attachment_id: str
    mapping: dict[str, Any] | None = None
    auto_infer: bool = False
    target_project_id: str | None = None


class CreateExportJobRequest(BaseModel):
    """POST /data-jobs/export (§3.5)."""

    workspace_id: str
    entity_type: Literal["issues", "projects"] = "issues"
    format: Literal["csv", "json"] = "csv"
    scope: Literal["project", "workspace", "view"] = "workspace"
    project_id: str | None = None
    filters: dict[str, Any] | None = None
    mapping: dict[str, Any] | None = None
    locale: str | None = Field(default=None, max_length=35)
