"""Pure transform tests (import-export.md §2.4 — DB-free row conversion)."""

import uuid
from datetime import date

import pytest

from mesh.data_jobs.transforms import (
    CustomFieldInfo,
    RowError,
    StatusInfo,
    TransformContext,
    parse_date_value,
    transform_row,
)

pytestmark = pytest.mark.unit

_TODO = StatusInfo(id=uuid.uuid4(), category="todo")
_IN_PROGRESS = StatusInfo(id=uuid.uuid4(), category="in_progress")
_MEMBER_ID = uuid.uuid4()


def _issue_context(**overrides) -> TransformContext:
    base = dict(
        entity_type="issues",
        statuses_by_name={"Todo": _TODO, "In Progress": _IN_PROGRESS},
        default_status=_TODO,
        members_by_email={"zhang@mesh.test": _MEMBER_ID},
        human_member_ids=frozenset({_MEMBER_ID}),
        labels_by_name={"bug": uuid.uuid4()},
        custom_fields_by_key={"severity": CustomFieldInfo(id=uuid.uuid4(), type="text")},
        projects_by_key={"APP": uuid.uuid4()},
        milestones_by_name={},
        cycles_by_name={},
        options={},
    )
    base.update(overrides)
    return TransformContext(**base)


def _mapping(*columns) -> dict:
    return {"columns": list(columns)}


def _col(source, target, transform_type="direct", **extra):
    return {"source": source, "target": target, "transform": {"type": transform_type, **extra}}


class TestRowError:
    def test_rejects_unknown_code(self):
        with pytest.raises(ValueError):
            RowError(1, "title", "not_a_code", "x")

    def test_as_dict_shape(self):
        entry = RowError(7, "assignee", "unknown_member", "no member").as_dict()
        assert entry == {"row": 7, "field": "assignee", "code": "unknown_member", "message": "no member"}


class TestParseDate:
    def test_iso(self):
        assert parse_date_value("2026-07-25") == date(2026, 7, 25)

    def test_iso_z_datetime(self):
        assert parse_date_value("2026-07-25T10:00:00Z") == date(2026, 7, 25)

    def test_slash_mdy(self):
        assert parse_date_value("07/25/2026") == date(2026, 7, 25)

    def test_unambiguous_dmy(self):
        assert parse_date_value("25/07/2026") == date(2026, 7, 25)

    def test_ambiguous_defaults_to_mdy(self):
        # 05/06/2026 is ambiguous → treated as May 6 (no silent d/m swap).
        assert parse_date_value("05/06/2026") == date(2026, 5, 6)

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            parse_date_value("someday")


class TestTransformRow:
    def test_happy_path_all_transforms(self):
        mapping = _mapping(
            _col("Summary", "title"),
            _col("State", "status", "status_by_name", fallback="default"),
            _col("Priority", "priority", "value_map", map={"High": "high"}, default="none"),
            _col("Assignee", "assignee", "member_by_email", on_missing="null"),
            _col("Due", "due_date", "date_parse", format="auto"),
            _col("Labels", "labels", "list_split", delimiter=";"),
            _col("Key", "external_ref"),
            _col("Parent", "parent", "parent_by_external_ref"),
            _col("Severity", "custom_field_values.severity"),
        )
        values, errors, warnings = transform_row(
            3,
            {
                "Summary": "登录崩溃",
                "State": "In Progress",
                "Priority": "High",
                "Assignee": "zhang@mesh.test",
                "Due": "2026-07-25",
                "Labels": "bug;ui",
                "Key": "EXT-9",
                "Parent": "EXT-1",
                "Severity": "critical",
            },
            mapping,
            _issue_context(),
        )
        assert errors == []
        assert values["title"] == "登录崩溃"
        assert values["status"] == _IN_PROGRESS
        assert values["priority"] == "high"
        assert values["assignee_id"] == _MEMBER_ID
        assert values["due_date"] == date(2026, 7, 25)
        assert values["labels"] == ["bug", "ui"]
        assert values["external_ref"] == "EXT-9"
        assert values["parent_external_ref"] == "EXT-1"
        assert values["custom_field_values"] == {"severity": "critical"}

    def test_missing_title_is_required_error(self):
        mapping = _mapping(_col("Summary", "title"))
        _values, errors, _w = transform_row(1, {"Summary": "  "}, mapping, _issue_context())
        assert [e.code for e in errors] == ["required_field_missing"]
        assert errors[0].field == "title"

    def test_unknown_status_fallback_default_warns(self):
        mapping = _mapping(_col("T", "title"), _col("S", "status", "status_by_name", fallback="default"))
        values, errors, warnings = transform_row(1, {"T": "x", "S": "Nonexistent"}, mapping, _issue_context())
        assert errors == []
        assert values["status"] == _TODO
        assert [w.code for w in warnings] == ["unknown_status"]

    def test_unknown_status_fallback_error(self):
        mapping = _mapping(_col("T", "title"), _col("S", "status", "status_by_name", fallback="error"))
        _v, errors, _w = transform_row(1, {"T": "x", "S": "Nope"}, mapping, _issue_context())
        assert [e.code for e in errors] == ["unknown_status"]

    def test_member_by_email_null_vs_error(self):
        null_mapping = _mapping(
            _col("T", "title"), _col("A", "assignee", "member_by_email", on_missing="null")
        )
        values, errors, _w = transform_row(
            1, {"T": "x", "A": "ghost@mesh.test"}, null_mapping, _issue_context()
        )
        assert errors == [] and values["assignee_id"] is None
        err_mapping = _mapping(
            _col("T", "title"), _col("A", "assignee", "member_by_email", on_missing="error")
        )
        _v, errors, _w = transform_row(1, {"T": "x", "A": "ghost@mesh.test"}, err_mapping, _issue_context())
        assert [e.code for e in errors] == ["unknown_member"]

    def test_direct_assignee_must_be_human_member(self):
        mapping = _mapping(_col("T", "title"), _col("A", "assignee"))
        agent_id = uuid.uuid4()
        _v, errors, _w = transform_row(1, {"T": "x", "A": str(agent_id)}, mapping, _issue_context())
        assert [e.code for e in errors] == ["unknown_member"]
        values, errors, _w = transform_row(2, {"T": "x", "A": str(_MEMBER_ID)}, mapping, _issue_context())
        assert errors == [] and values["assignee_id"] == _MEMBER_ID

    def test_invalid_priority_and_value_map_default(self):
        mapping = _mapping(
            _col("T", "title"), _col("P", "priority", "value_map", map={"Hot": "scorching"}, default="none")
        )
        values, errors, _w = transform_row(1, {"T": "x", "P": "Hot"}, mapping, _issue_context())
        # 'scorching' is not a valid priority → invalid_value; not silently kept
        assert [e.code for e in errors] == ["invalid_value"]
        values, errors, _w = transform_row(2, {"T": "x", "P": "zzz"}, mapping, _issue_context())
        assert errors == [] and values["priority"] == "none"

    def test_invalid_date_code(self):
        mapping = _mapping(_col("T", "title"), _col("D", "due_date", "date_parse"))
        _v, errors, _w = transform_row(1, {"T": "x", "D": "whenever"}, mapping, _issue_context())
        assert [e.code for e in errors] == ["invalid_date"]

    def test_estimate_numeric_validation(self):
        mapping = _mapping(_col("T", "title"), _col("E", "estimate"))
        values, errors, _w = transform_row(1, {"T": "x", "E": "3.5"}, mapping, _issue_context())
        assert errors == [] and values["estimate"] == __import__("decimal").Decimal("3.5")
        _v, errors, _w = transform_row(2, {"T": "x", "E": "big"}, mapping, _issue_context())
        assert [e.code for e in errors] == ["invalid_value"]

    def test_unknown_custom_field_unsupported(self):
        mapping = _mapping(_col("T", "title"), _col("X", "custom_field_values.ghost"))
        _v, errors, _w = transform_row(1, {"T": "x", "X": "1"}, mapping, _issue_context())
        assert [e.code for e in errors] == ["unsupported_value"]

    def test_labels_create_missing_false_unknown_label(self):
        mapping = _mapping(
            _col("T", "title"),
            _col("L", "labels", "list_split", delimiter=",", create_missing=False),
        )
        _v, errors, _w = transform_row(1, {"T": "x", "L": "bug,newone"}, mapping, _issue_context())
        assert [e.code for e in errors] == ["unknown_label"]

    def test_project_required_fields_and_key_format(self):
        mapping = _mapping(_col("N", "name"), _col("K", "key"))
        context = TransformContext(entity_type="projects", members_by_email={}, human_member_ids=frozenset())
        _v, errors, _w = transform_row(1, {"N": "Proj", "K": "bad key"}, mapping, context)
        assert "invalid_value" in [e.code for e in errors]
        values, errors, _w = transform_row(2, {"N": "Proj", "K": "APP"}, mapping, context)
        assert errors == [] and values["key"] == "APP"
        _v, errors, _w = transform_row(3, {"N": "", "K": "APP"}, mapping, context)
        assert "required_field_missing" in [e.code for e in errors]

    def test_empty_cell_treated_as_missing_not_error(self):
        mapping = _mapping(_col("T", "title"), _col("D", "description"))
        values, errors, _w = transform_row(1, {"T": "x", "D": "   "}, mapping, _issue_context())
        assert errors == []
        assert "description" not in values
