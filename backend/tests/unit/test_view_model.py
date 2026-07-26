"""View model metadata tests — kanban.md §2.2/§2.8, README §6.2/§6.3.

Asserts the ORM metadata carries every constraint the spec DDL requires:
CHECKs, composite FKs, the ``UNIQUE(workspace_id, id)`` reference target and
the two partial EXPRESSION unique indexes (name / default) that COALESCE
project_id for NULL-scope uniqueness (README §6.3 forbids table-level UNIQUE
with COALESCE expressions).
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, Index
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.schema import CreateTable

from mesh.db.models.view import VIEW_LAYOUT_VALUES, VIEW_VISIBILITY_VALUES, View

pytestmark = pytest.mark.unit


def _indexes() -> dict[str, Index]:
    return {index.name: index for index in View.__table__.indexes}


def _check_constraints() -> dict[str, CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in View.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_tablename_and_enum_constants() -> None:
    assert View.__tablename__ == "views"
    assert VIEW_LAYOUT_VALUES == ("board", "list", "timeline", "table")
    assert VIEW_VISIBILITY_VALUES == ("private", "shared")


def test_columns_and_defaults() -> None:
    columns = View.__table__.columns
    for name in (
        "id",
        "workspace_id",
        "project_id",
        "owner_member_id",
        "name",
        "layout",
        "visibility",
        "filters",
        "group_by",
        "sub_group_by",
        "sort",
        "display_fields",
        "board_settings",
        "position",
        "is_default",
        "created_at",
        "updated_at",
    ):
        assert name in columns, name
    assert columns["project_id"].nullable is True
    assert columns["owner_member_id"].nullable is False
    assert columns["group_by"].nullable is True
    for json_column in ("filters", "sort", "display_fields", "board_settings"):
        assert columns[json_column].nullable is False


def test_check_constraints() -> None:
    checks = _check_constraints()
    # The Base naming convention renders ck_%(table_name)s_%(constraint_name)s.
    assert "ck_views_views_layout" in checks
    assert "ck_views_views_visibility" in checks
    assert "ck_views_views_name_length" in checks
    layout_sql = str(checks["ck_views_views_layout"].sqltext)
    for value in VIEW_LAYOUT_VALUES:
        assert value in layout_sql
    assert "char_length(name) BETWEEN 1 AND 100" in str(
        checks["ck_views_views_name_length"].sqltext
    )


def test_composite_fks_to_projects_and_members() -> None:
    ddl = str(CreateTable(View.__table__).compile(dialect=postgresql_dialect())).replace(
        "\n", " "
    )
    for fragment in (
        "FOREIGN KEY(workspace_id, project_id) REFERENCES projects (workspace_id, id) "
        "ON DELETE CASCADE",
        "FOREIGN KEY(workspace_id, owner_member_id) REFERENCES members (workspace_id, id) "
        "ON DELETE CASCADE",
    ):
        assert fragment in ddl, fragment


def test_unique_workspace_id_reference_target() -> None:
    indexes = _indexes()
    assert "uq_views_ws_id" in indexes
    assert indexes["uq_views_ws_id"].unique is True


def test_partial_expression_unique_indexes() -> None:
    indexes = _indexes()
    for name, where_fragment in (
        ("uq_views_name", None),
        ("uq_views_default", "is_default"),
    ):
        index = indexes[name]
        assert index.unique is True
        expressions = [str(expression) for expression in index.expressions]
        assert any("coalesce(project_id" in expression.lower() for expression in expressions)
        if where_fragment is not None:
            assert index.dialect_kwargs["postgresql_where"] is not None
            assert where_fragment in str(index.dialect_kwargs["postgresql_where"])


def test_secondary_indexes() -> None:
    indexes = _indexes()
    for name in (
        "idx_views_workspace",
        "idx_views_project",
        "idx_views_owner",
        "idx_views_visibility",
    ):
        assert name in indexes, name
    assert indexes["idx_views_project"].dialect_kwargs["postgresql_where"] is not None
