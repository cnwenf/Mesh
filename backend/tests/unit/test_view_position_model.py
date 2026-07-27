"""ViewIssuePosition model metadata tests — kanban.md §2.7/§2.8, README §6.2.

Per-view manual card order: a single ``issues.position`` would leak one view's
drag order into every other view, so each view keeps its own (view_id, issue_id)
row. Asserts the ORM metadata carries the same-tenant composite FKs to
views/issues (both ``UNIQUE(workspace_id, id)`` reference targets), the
``UNIQUE(view_id, issue_id)`` per-view uniqueness and the
``(view_id, group_key, position)`` ordering index the spec DDL requires.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.schema import CreateTable

from mesh.db.models.view_position import ViewIssuePosition

pytestmark = pytest.mark.unit


def _indexes() -> dict[str, Index]:
    return {index.name: index for index in ViewIssuePosition.__table__.indexes}


def test_tablename() -> None:
    assert ViewIssuePosition.__tablename__ == "view_issue_positions"


def test_columns_and_defaults() -> None:
    columns = ViewIssuePosition.__table__.columns
    for name in (
        "id",
        "workspace_id",
        "view_id",
        "issue_id",
        "group_key",
        "position",
        "created_at",
        "updated_at",
    ):
        assert name in columns, name
    assert columns["workspace_id"].nullable is False
    assert columns["view_id"].nullable is False
    assert columns["issue_id"].nullable is False
    assert columns["group_key"].nullable is False
    assert columns["position"].nullable is False


def test_composite_fks_to_views_and_issues() -> None:
    ddl = str(
        CreateTable(ViewIssuePosition.__table__).compile(dialect=postgresql_dialect())
    ).replace("\n", " ")
    for fragment in (
        "FOREIGN KEY(workspace_id, view_id) REFERENCES views (workspace_id, id) "
        "ON DELETE CASCADE",
        "FOREIGN KEY(workspace_id, issue_id) REFERENCES issues (workspace_id, id) "
        "ON DELETE CASCADE",
    ):
        assert fragment in ddl, fragment


def test_per_view_issue_uniqueness() -> None:
    indexes = _indexes()
    assert "uq_vip_view_issue" in indexes
    assert indexes["uq_vip_view_issue"].unique is True
    columns = [column.name for column in indexes["uq_vip_view_issue"].columns]
    assert columns == ["view_id", "issue_id"]


def test_group_position_ordering_index() -> None:
    indexes = _indexes()
    assert "idx_vip_view_group_pos" in indexes
    columns = [column.name for column in indexes["idx_vip_view_group_pos"].columns]
    assert columns == ["view_id", "group_key", "position"]
