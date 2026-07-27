"""label-property issue-association layer: issue_labels, issue_custom_field_values

MES-32 remainder increment (label-property.md §2.3 / §2.6 association layer;
the definition layer landed in 0008). DDL mirrors the reference contract in
docs/specs/validation/schema_r2_validation.sql:

- issue_labels: issue ↔ label M2M join, composite PK (issue_id, label_id),
  same-tenant composite FKs into issues / labels (README §6.2 — a
  cross-workspace pairing is rejected at INSERT), cascade on both sides.
- issue_custom_field_values: per-issue EAV rows with typed columns + JSONB;
  ``num_nonnulls(...) <= 1`` DB backstop keeps at most one value column
  populated; ``value_member_id`` uses the PG16 column-level
  ``ON DELETE SET NULL (value_member_id)`` so deleting a member nulls only
  the reference column (README §6.2 rule 6, §9 T18).
- §2.7 value indexes lead with field_def_id (filters are always "one field
  = value/range"): partial B-Trees for number/date/member and a btree_gin
  composite GIN for (field_def_id, value_json) enum containment scans.
- RLS defense-in-depth (README §6.2 rule 5) on both tables.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "issue_labels",
    "issue_custom_field_values",
)


def upgrade() -> None:
    # -- issue_labels: issue ↔ label M2M (label-property.md §2.3) ----------------
    op.execute(
        """
        CREATE TABLE issue_labels (
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          issue_id     UUID NOT NULL,
          label_id     UUID NOT NULL,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (issue_id, label_id),
          FOREIGN KEY (workspace_id, issue_id)
            REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, label_id)
            REFERENCES labels(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    # §2.7: PK covers issue-leading access; the label-leading index serves the
    # reverse lookup "which issues carry this label".
    op.execute("CREATE INDEX idx_issue_labels_issue ON issue_labels(issue_id)")
    op.execute("CREATE INDEX idx_issue_labels_label ON issue_labels(label_id)")

    # -- issue_custom_field_values: per-issue EAV (§2.6) -------------------------
    op.execute(
        """
        CREATE TABLE issue_custom_field_values (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          issue_id        UUID NOT NULL,
          field_def_id    UUID NOT NULL,
          value_text      TEXT NULL,
          value_number    NUMERIC NULL,
          value_date      TIMESTAMPTZ NULL,
          value_member_id UUID NULL,
          value_boolean   BOOLEAN NULL,
          value_json      JSONB NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (issue_id, field_def_id),
          CHECK (num_nonnulls(value_text, value_number, value_date,
                              value_member_id, value_boolean, value_json) <= 1),
          FOREIGN KEY (workspace_id, issue_id)
            REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, field_def_id)
            REFERENCES custom_field_defs(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, value_member_id)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (value_member_id)
        )
        """
    )
    # §2.7 value indexes — field_def_id leads so "one field = value/range"
    # scans narrow to that field's rows before touching values.
    op.execute("CREATE INDEX idx_icfv_issue ON issue_custom_field_values(issue_id)")
    op.execute(
        "CREATE INDEX idx_icfv_number ON issue_custom_field_values "
        "(field_def_id, value_number) WHERE value_number IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_icfv_date ON issue_custom_field_values "
        "(field_def_id, value_date) WHERE value_date IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_icfv_member ON issue_custom_field_values "
        "(field_def_id, value_member_id) WHERE value_member_id IS NOT NULL"
    )
    # Enum/structured filter path (field_def_id = … AND value_json @> …): the
    # scalar+JSONB composite GIN needs btree_gin (§2.7 final block).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")
    op.execute(
        "CREATE INDEX idx_icfv_value_json ON issue_custom_field_values "
        "USING GIN (field_def_id, value_json)"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) ------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- app-role privileges ----------------------------------------------------
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"issue_labels, issue_custom_field_values TO {APP_ROLE}"
    )


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS issue_custom_field_values")
    op.execute("DROP TABLE IF EXISTS issue_labels")
    # btree_gin is left in place: shared extension, never dropped on a path
    # that may still serve other consumers.
