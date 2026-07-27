"""Label & custom-field models (label-property.md §2 — both layers).

Definition layer (MES-42): workspace/project-scoped label definitions,
custom-field definitions and the enum option rows attached to select-type
fields. Association layer (MES-32 remainder): the ``issue_labels`` M2M join
table and the ``issue_custom_field_values`` EAV table that hang labels and
typed field values off issues.

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
from decimal import Decimal

from sqlalchemy import (
    REAL,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
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


class IssueLabel(Base):
    """The issue ↔ label many-to-many join row (label-property.md §2.3).

    Pure association row: composite PK ``(issue_id, label_id)``, same-tenant
    composite FKs into ``issues`` / ``labels`` (README §6.2 — a cross-workspace
    pairing is rejected at INSERT), cascade-delete on either side. Label
    scope (project-level labels only on same-project issues) is enforced by
    the service layer, not here.
    """

    __tablename__ = "issue_labels"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    label_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Reverse lookup "which issues carry this label" (§2.7); the PK already
        # covers issue_id-leading access.
        Index("idx_issue_labels_issue", "issue_id"),
        Index("idx_issue_labels_label", "label_id"),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="issue_labels_issue_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "label_id"),
            ("labels.workspace_id", "labels.id"),
            ondelete="CASCADE",
            name="issue_labels_label_id_labels",
        ),
    )


class IssueCustomFieldValue(Base):
    """One typed field value on one issue — the EAV row (§2.6).

    Hybrid "typed column + JSONB" storage: exactly one value column may be
    non-NULL (``num_nonnulls(...) <= 1`` DB backstop); which column is legal
    for a given row is decided by ``custom_field_defs.type`` and enforced by
    the service layer (adding cross-column CHECKs per type would make type
    evolution painful). ``value_member_id`` references ``members`` with the
    PG16 column-level ``ON DELETE SET NULL (value_member_id)`` — deleting a
    member nulls only the reference column, the row survives (README §6.2
    rule 6 / §9 T18).
    """

    __tablename__ = "issue_custom_field_values"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_def_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    value_text: Mapped[str | None] = mapped_column(TEXT, default=None)
    value_number: Mapped[Decimal | None] = mapped_column(Numeric, default=None)
    value_date: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    value_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    value_boolean: Mapped[bool | None] = mapped_column(Boolean, default=None)
    value_json: Mapped[object | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # One value per (issue, field) — PUT is an upsert on this key.
        UniqueConstraint("issue_id", "field_def_id", name="uq_icfv_issue_field"),
        # DB backstop against cross-typed dirty rows (§2.6 note): at most one
        # value column non-NULL (boolean false counts as non-NULL, hence
        # num_nonnulls rather than a pairwise mutual exclusion).
        CheckConstraint(
            "num_nonnulls(value_text, value_number, value_date, "
            "value_member_id, value_boolean, value_json) <= 1",
            name="ck_icfv_single_value_col",
        ),
        # §2.7 value indexes lead with field_def_id — filters are always
        # "one field = value/range", so the scan narrows to that field's rows.
        Index("idx_icfv_issue", "issue_id"),
        Index(
            "idx_icfv_number",
            "field_def_id",
            "value_number",
            postgresql_where=text("value_number IS NOT NULL"),
        ),
        Index(
            "idx_icfv_date",
            "field_def_id",
            "value_date",
            postgresql_where=text("value_date IS NOT NULL"),
        ),
        Index(
            "idx_icfv_member",
            "field_def_id",
            "value_member_id",
            postgresql_where=text("value_member_id IS NOT NULL"),
        ),
        # Enum/structured filter path (field_def_id = … AND value_json @> …);
        # the scalar+JSONB mix needs the btree_gin extension (migration 0014).
        Index(
            "idx_icfv_value_json",
            "field_def_id",
            "value_json",
            postgresql_using="gin",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="issue_custom_field_values_issue_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "field_def_id"),
            ("custom_field_defs.workspace_id", "custom_field_defs.id"),
            ondelete="CASCADE",
            name="issue_custom_field_values_field_def_id_custom_field_defs",
        ),
        # PG16 column-level SET NULL: deleting a member nulls ONLY the
        # reference column; workspace_id stays NOT NULL, the row survives
        # (README §6.2 rule 6, §9 T18).
        ForeignKeyConstraint(
            ("workspace_id", "value_member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (value_member_id)",
            name="issue_custom_field_values_value_member_id_members",
        ),
    )
