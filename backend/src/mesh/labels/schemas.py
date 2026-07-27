"""Request schemas for the label-property definition layer (label-property.md §3).

UUIDs travel as strings and are parsed in the routes (project-module
convention). PATCH bodies are tri-state: a field absent from
``model_fields_set`` is left untouched; an explicit ``null`` clears.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateLabelRequest(BaseModel):
    """POST /workspaces/{ws}/labels — name + color, optional project scope."""

    model_config = ConfigDict(extra="forbid")

    name: str
    color: str
    description: str | None = None
    project_id: str | None = None


class UpdateLabelRequest(BaseModel):
    """PATCH /labels/{id} — rename / recolor / describe (tri-state)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    color: str | None = None
    description: str | None = None


class OptionInput(BaseModel):
    """Inline option payload for field-definition creation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    color: str | None = None
    position: float = 0


class CreateCustomFieldRequest(BaseModel):
    """POST /workspaces/{ws}/custom-fields — may carry initial options."""

    model_config = ConfigDict(extra="forbid")

    name: str
    field_key: str
    type: str
    project_id: str | None = None
    is_required: bool = False
    required_on: list[Any] = Field(default_factory=list)
    default_value: Any | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    position: float = 0
    options: list[OptionInput] = Field(default_factory=list)


class UpdateCustomFieldRequest(BaseModel):
    """PATCH /custom-fields/{id} — everything except type/field_key (tri-state).

    ``type`` and ``field_key`` are immutable after creation: the stable key is
    referenced by filters/views, and changing the type would invalidate stored
    values (value-column semantics, §2.6).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    is_required: bool | None = None
    required_on: list[Any] | None = None
    default_value: Any | None = None
    config: dict[str, Any] | None = None
    position: float | None = None
    is_active: bool | None = None


class CreateOptionRequest(BaseModel):
    """POST /custom-fields/{id}/options."""

    model_config = ConfigDict(extra="forbid")

    name: str
    color: str | None = None
    position: float = 0


class UpdateOptionRequest(BaseModel):
    """PATCH /custom-fields/{id}/options/{opt_id} (tri-state)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    color: str | None = None
    position: float | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# issue-association layer (MES-32 remainder — §3.1 issue-side endpoints)
# ---------------------------------------------------------------------------


class ReplaceIssueLabelsRequest(BaseModel):
    """PUT /issues/{id}/labels — whole-set replacement of the issue's labels."""

    model_config = ConfigDict(extra="forbid")

    label_ids: list[str] = Field(default_factory=list)


class MergeLabelRequest(BaseModel):
    """POST /labels/{id}/merge — migrate source issues onto the target (§3.2)."""

    model_config = ConfigDict(extra="forbid")

    target_label_id: str


class CustomFieldValueInput(BaseModel):
    """One entry of PUT /issues/{id}/custom-field-values.

    Exactly one ``value_*`` column must be present (routes check
    ``model_fields_set``); the service layer validates the column against the
    field definition's type (422 ``invalid_field_value``). An explicit
    ``null`` value clears the field.
    """

    model_config = ConfigDict(extra="forbid")

    field_def_id: str
    value_text: str | None = None
    value_number: float | None = None
    value_date: str | None = None
    value_member_id: str | None = None
    value_boolean: bool | None = None
    value_json: Any | None = None


class SetCustomFieldValuesRequest(BaseModel):
    """PUT /issues/{id}/custom-field-values — whole-form submission (§3.2)."""

    model_config = ConfigDict(extra="forbid")

    values: list[CustomFieldValueInput] = Field(default_factory=list)
