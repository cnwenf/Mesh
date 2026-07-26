"""Label & custom-field definition models (label-property.md §2, definition layer).

This increment owns the DEFINITION layer only: workspace/project-scoped label
definitions, custom-field definitions and the enum option rows attached to
select-type fields. The issue-association tables (``issue_labels`` /
``issue_custom_field_values``) land with the issue-module increment.

Naming uniqueness is "workspace-level OR project-level" (§2.2/§2.4): a
``COALESCE`` expression cannot appear in a table-level ``UNIQUE`` constraint,
so both tables carry the partial-EXPRESSION unique index mandated by README
§6.3 — ``UNIQUE (workspace_id, COALESCE(project_id, '0000…'), name|field_key)``.
Every referenceable table exposes ``UNIQUE (workspace_id, id)`` for composite
FK referencing (README §6.2); the ``project_id`` scope columns are same-tenant
composite FKs into ``projects``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    REAL,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

# label-property.md §1.3 — the closed field-type registry (formula/rollup are
# explicitly out of scope and get NO reserved value).
CUSTOM_FIELD_TYPE_VALUES = (
    "text",
    "textarea",
    "number",
    "date",
    "datetime",
    "single_select",
    "multi_select",
    "member",
    "boolean",
    "url",
)

# Types whose values are drawn from custom_field_options (§1.3).
SELECT_FIELD_TYPES = ("single_select", "multi_select")

# Sentinel folded into the uniqueness expression for workspace-level rows:
# COALESCE(project_id, <nil-uuid>) so one expression covers both scopes
# (README §6.3 — the nil UUID can never be a real project id).
NULL_SCOPE_SENTINEL = "00000000-0000-0000-0000-000000000000"


class Label(Base):
    """A lightweight visual classification tag (label-property.md §2.2).

    ``project_id = NULL`` → workspace-level (usable across the workspace);
    non-NULL → project-scoped. Name uniqueness is per scope via the
    partial-expression unique index ``uq_labels_name`` (README §6.3).
    """

    __tablename__ = "labels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    color: Mapped[str] = mapped_column(TEXT, nullable=False)
    description: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 50", name="labels_name_len"
        ),
        CheckConstraint(
            "color ~ '^#[0-9a-fA-F]{6}$'", name="labels_color_hex"
        ),
        # Scope-internal name uniqueness: workspace-level OR project-level.
        # COALESCE cannot sit in a table-level UNIQUE — README §6.3 mandates
        # this partial-expression unique index spelling.
        Index(
            "uq_labels_name",
            "workspace_id",
            text(f"COALESCE(project_id, '{NULL_SCOPE_SENTINEL}')"),
            "name",
            unique=True,
        ),
        # Composite-FK reference target for issue_labels.label_id (README §6.2).
        Index("uq_labels_ws_id", "workspace_id", "id", unique=True),
        Index("idx_labels_workspace", "workspace_id"),
        # Scope column: same-tenant composite FK (README §6.2).
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="CASCADE",
            name="labels_project_id_projects",
        ),
    )


class CustomFieldDef(Base):
    """A typed extension-field definition (label-property.md §2.4).

    Ten closed types (§1.3); type-specific knobs live in ``config`` (JSONB)
    and are validated by the service layer per type. ``is_active=false``
    retires the field without dropping data (reactivation restores it).
    ``field_key`` uniqueness is per scope via ``uq_cfdefs_key`` (README §6.3).
    """

    __tablename__ = "custom_field_defs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    field_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    type: Mapped[str] = mapped_column(TEXT, nullable=False)
    is_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    required_on: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    default_value: Mapped[object | None] = mapped_column(JSONB, default=None)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    position: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100", name="custom_field_defs_name_len"
        ),
        CheckConstraint(
            "field_key ~ '^[a-z][a-z0-9_]{0,49}$'", name="custom_field_defs_field_key_fmt"
        ),
        CheckConstraint(
            f"type IN {CUSTOM_FIELD_TYPE_VALUES!r}", name="custom_field_defs_type"
        ),
        # Scope-internal field_key uniqueness (README §6.3 expression index).
        Index(
            "uq_cfdefs_key",
            "workspace_id",
            text(f"COALESCE(project_id, '{NULL_SCOPE_SENTINEL}')"),
            "field_key",
            unique=True,
        ),
        # Composite-FK reference target for custom_field_options /
        # issue_custom_field_values (README §6.2).
        Index("uq_cfdefs_ws_id", "workspace_id", "id", unique=True),
        Index(
            "idx_cfdefs_workspace_active",
            "workspace_id",
            postgresql_where=text("is_active"),
        ),
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="CASCADE",
            name="custom_field_defs_project_id_projects",
        ),
    )


class CustomFieldOption(Base):
    """An enum option for single_select / multi_select fields (§2.5)."""

    __tablename__ = "custom_field_options"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_def_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    color: Mapped[str | None] = mapped_column(TEXT, default=None)
    position: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # §2.5 table-level UNIQUE — option names are unique within one field.
        UniqueConstraint("field_def_id", "name", name="uq_cfopts_def_name"),
        Index("idx_cfopts_def", "field_def_id", "position"),
        ForeignKeyConstraint(
            ("workspace_id", "field_def_id"),
            ("custom_field_defs.workspace_id", "custom_field_defs.id"),
            ondelete="CASCADE",
            name="custom_field_options_field_def_id_custom_field_defs",
        ),
    )
