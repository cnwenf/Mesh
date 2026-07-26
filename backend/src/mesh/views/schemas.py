"""Request schemas for the views API (kanban.md §3, README §6.14).

Pydantic only handles JSON shape here — the whitelist config validation
(fields/ops/depth limits) lives in ``mesh.views.config`` and runs in the
service layer before anything is stored (kanban §2.9 closing note).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateViewRequest(BaseModel):
    """POST /workspaces/{ws}/views body (kanban §3.2)."""

    name: str = Field(min_length=1, max_length=100)
    layout: str = "board"
    visibility: str = "private"
    project_id: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    group_by: str | None = None
    sub_group_by: str | None = None
    sort: list[Any] = Field(default_factory=list)
    display_fields: list[Any] = Field(default_factory=list)
    board_settings: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class UpdateViewRequest(BaseModel):
    """PATCH /views/{id} body — every field optional (tri-state via model_fields_set)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    layout: str | None = None
    visibility: str | None = None
    project_id: str | None = None
    filters: dict[str, Any] | None = None
    group_by: str | None = None
    sub_group_by: str | None = None
    sort: list[Any] | None = None
    display_fields: list[Any] | None = None
    board_settings: dict[str, Any] | None = None
    is_default: bool | None = None


class WipRequest(BaseModel):
    """PATCH /views/{id}/wip body (kanban §3.2).

    ``limit=None`` removes the WIP rule for ``group_key``.
    """

    group_key: str = Field(min_length=1, max_length=120)
    limit: int | None = Field(default=None, ge=1)
    enforcement: str = "warn"


class ReorderViewsRequest(BaseModel):
    """PATCH /workspaces/{ws}/views/reorder body — ordered view ids."""

    view_ids: list[str] = Field(min_length=1)
