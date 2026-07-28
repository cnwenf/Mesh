"""Mapping validation + auto-inference tests (import-export.md §2.4 / §3.2)."""

import pytest

from mesh.data_jobs.mapping import (
    infer_mapping,
    validate_export_mapping,
    validate_import_mapping,
)
from mesh.errors import ValidationError

pytestmark = pytest.mark.unit


def _column(source, target, transform_type="direct", **extra):
    transform = {"type": transform_type, **extra}
    return {"source": source, "target": target, "transform": transform}


class TestValidateImportMapping:
    def test_accepts_spec_example_mapping(self):
        mapping = {
            "columns": [
                _column("Summary", "title"),
                _column("State", "status", "status_by_name", fallback="default"),
                _column("Priority", "priority", "value_map", map={"High": "high"}, default="none"),
                _column("Assignee Email", "assignee", "member_by_email", on_missing="null"),
                _column("Due", "due_date", "date_parse", format="auto"),
                _column("Labels", "labels", "list_split", delimiter=";"),
                _column("Key", "external_ref"),
                _column("Parent Key", "parent", "parent_by_external_ref"),
                _column("Ext", "custom_field_values.severity"),
            ],
            "defaults": {"state_category_fallback": "todo"},
            "options": {"strict": False},
        }
        assert validate_import_mapping(mapping, entity_type="issues") is mapping

    def test_rejects_empty_columns(self):
        with pytest.raises(ValidationError) as exc:
            validate_import_mapping({"columns": []}, entity_type="issues")
        assert exc.value.code == "mapping_invalid"

    def test_rejects_unknown_target(self):
        with pytest.raises(ValidationError) as exc:
            validate_import_mapping(
                {"columns": [_column("a", "bogus"), _column("t", "title")]},
                entity_type="issues",
            )
        assert exc.value.code == "mapping_invalid"

    def test_rejects_missing_title_mapping(self):
        with pytest.raises(ValidationError) as exc:
            validate_import_mapping({"columns": [_column("d", "description")]}, entity_type="issues")
        assert exc.value.code == "mapping_invalid"
        assert "title" in str(exc.value)

    def test_rejects_duplicate_target(self):
        with pytest.raises(ValidationError) as exc:
            validate_import_mapping(
                {"columns": [_column("a", "title"), _column("b", "title")]},
                entity_type="issues",
            )
        assert exc.value.code == "mapping_invalid"

    def test_rejects_unknown_transform_type(self):
        with pytest.raises(ValidationError) as exc:
            validate_import_mapping({"columns": [_column("a", "title", "warp_drive")]}, entity_type="issues")
        assert exc.value.code == "mapping_invalid"

    def test_rejects_missing_transform(self):
        with pytest.raises(ValidationError):
            validate_import_mapping({"columns": [{"source": "a", "target": "title"}]}, entity_type="issues")

    def test_value_map_requires_map(self):
        with pytest.raises(ValidationError):
            validate_import_mapping(
                {"columns": [_column("a", "title"), _column("p", "priority", "value_map")]},
                entity_type="issues",
            )

    def test_status_by_name_fallback_values(self):
        with pytest.raises(ValidationError):
            validate_import_mapping(
                {
                    "columns": [
                        _column("a", "title"),
                        _column("s", "status", "status_by_name", fallback="maybe"),
                    ]
                },
                entity_type="issues",
            )

    def test_member_by_email_on_missing_values(self):
        with pytest.raises(ValidationError):
            validate_import_mapping(
                {
                    "columns": [
                        _column("a", "title"),
                        _column("m", "assignee", "member_by_email", on_missing="cry"),
                    ]
                },
                entity_type="issues",
            )

    def test_project_targets_require_name_and_key(self):
        with pytest.raises(ValidationError) as exc:
            validate_import_mapping({"columns": [_column("n", "name")]}, entity_type="projects")
        assert exc.value.code == "mapping_invalid"
        assert "key" in str(exc.value)

    def test_project_mapping_accepts_spec_fields(self):
        mapping = {
            "columns": [
                _column("Name", "name"),
                _column("Key", "key"),
                _column("Status", "status"),
                _column("Health", "health"),
                _column("Lead", "lead", "member_by_email", on_missing="null"),
            ]
        }
        assert validate_import_mapping(mapping, entity_type="projects") is mapping

    def test_rejects_non_object_defaults_and_options(self):
        with pytest.raises(ValidationError):
            validate_import_mapping(
                {"columns": [_column("a", "title")], "defaults": "nope"}, entity_type="issues"
            )
        with pytest.raises(ValidationError):
            validate_import_mapping(
                {"columns": [_column("a", "title")], "options": [1]}, entity_type="issues"
            )


class TestValidateExportMapping:
    def test_absent_mapping_uses_defaults(self):
        columns = validate_export_mapping(None, entity_type="issues")
        targets = [c["target"] for c in columns]
        assert "identifier" in targets and "title" in targets and "status_category" in targets

    def test_accepts_spec_example_without_transforms(self):
        # §3.5: export columns need NO transform and may use read-only
        # fields like identifier / created_at (review HIGH-3 regression).
        columns = validate_export_mapping(
            {
                "columns": [
                    {"target": "identifier", "source": "编号"},
                    {"target": "created_at", "source": "createdAt"},
                ]
            },
            entity_type="issues",
        )
        assert columns == [
            {"target": "identifier", "source": "编号"},
            {"target": "created_at", "source": "createdAt"},
        ]

    def test_rejects_unknown_export_field(self):
        with pytest.raises(ValidationError) as exc:
            validate_export_mapping({"columns": [{"target": "bogus"}]}, entity_type="issues")
        assert exc.value.code == "mapping_invalid"

    def test_rejects_duplicate_export_field(self):
        with pytest.raises(ValidationError):
            validate_export_mapping(
                {"columns": [{"target": "title"}, {"target": "title", "source": "T2"}]},
                entity_type="issues",
            )

    def test_project_export_fields(self):
        columns = validate_export_mapping(
            {"columns": [{"target": "key", "source": "项目Key"}]}, entity_type="projects"
        )
        assert columns[0]["source"] == "项目Key"


class TestInferMapping:
    def test_infers_common_headers(self):
        mapping = infer_mapping(
            ["Title", "State", "Priority", "Assignee Email", "Due", "Labels", "Key"],
            entity_type="issues",
        )
        by_target = {c["target"]: c for c in mapping["columns"]}
        assert by_target["title"]["source"] == "Title"
        assert by_target["status"]["transform"]["type"] == "status_by_name"
        assert by_target["priority"]["transform"]["type"] == "value_map"
        assert by_target["assignee"]["transform"]["type"] == "member_by_email"
        assert by_target["due_date"]["transform"]["type"] == "date_parse"
        assert by_target["labels"]["transform"]["type"] == "list_split"
        assert by_target["external_ref"]["source"] == "Key"
        # The inferred draft passes validation.
        assert validate_import_mapping(mapping, entity_type="issues") is mapping

    def test_infers_chinese_headers(self):
        mapping = infer_mapping(["标题", "状态", "优先级"], entity_type="issues")
        targets = {c["target"] for c in mapping["columns"]}
        assert {"title", "status", "priority"} <= targets

    def test_unknown_headers_left_unmapped(self):
        mapping = infer_mapping(["Xyzzy", "Frobnicator"], entity_type="issues")
        assert mapping["columns"] == []

    def test_no_duplicate_targets_on_similar_headers(self):
        mapping = infer_mapping(["Title", "Summary", "Name"], entity_type="issues")
        titles = [c for c in mapping["columns"] if c["target"] == "title"]
        assert len(titles) == 1
