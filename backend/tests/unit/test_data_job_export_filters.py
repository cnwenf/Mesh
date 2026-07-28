"""Unit tests for the export filter translation helpers (import-export.md §3.5/E3).

``_translate_export_filters`` maps the flat export filter dict (§2.4) onto
``IssueService.list_issues`` typed flat keyword args plus a structured
``state_category`` ``in`` tree node. This is the regression guard for the HIGH
bug where the raw flat dict was passed straight through as ``filters=`` —
``compile_filter_tree`` rejected it (no ``field``/``op``) and every filtered
export failed at runtime as ``storage_error``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from mesh.data_jobs.exporter import _coerce_filter_date, _translate_export_filters


class TestCoerceFilterDate:
    def test_date_passthrough(self):
        day = date(2026, 7, 1)
        assert _coerce_filter_date(day) == day

    def test_datetime_to_date(self):
        assert _coerce_filter_date(datetime(2026, 7, 1, 12, 30)) == date(2026, 7, 1)

    def test_iso_date_string(self):
        assert _coerce_filter_date("2026-07-01") == date(2026, 7, 1)

    def test_iso_datetime_string_with_z(self):
        assert _coerce_filter_date("2026-07-01T08:00:00Z") == date(2026, 7, 1)

    def test_blank_string_is_none(self):
        assert _coerce_filter_date("   ") is None

    def test_invalid_string_is_none(self):
        assert _coerce_filter_date("not-a-date") is None

    def test_non_date_value_is_none(self):
        assert _coerce_filter_date(12345) is None


class TestTranslateExportFilters:
    def test_none_filters_yields_empty(self):
        flat, tree = _translate_export_filters(None, None)
        assert flat == {}
        assert tree is None

    def test_scope_project_id_seeds_flat_kwargs(self):
        project_id = uuid.uuid4()
        flat, tree = _translate_export_filters({}, project_id)
        assert flat == {"project_id": project_id}
        assert tree is None

    def test_state_category_list_becomes_in_tree(self):
        flat, tree = _translate_export_filters({"state_category": ["todo", "in_progress"]}, None)
        assert flat == {}
        assert tree == {"field": "state_category", "op": "in", "value": ["todo", "in_progress"]}

    def test_state_category_scalar_wrapped_into_list(self):
        _flat, tree = _translate_export_filters({"state_category": "done"}, None)
        assert tree == {"field": "state_category", "op": "in", "value": ["done"]}

    def test_state_category_empty_values_dropped(self):
        _flat, tree = _translate_export_filters({"state_category": []}, None)
        assert tree is None

    def test_uuid_keys_parsed_to_uuid(self):
        assignee_id = uuid.uuid4()
        flat, tree = _translate_export_filters({"assignee_id": str(assignee_id)}, None)
        assert flat == {"assignee_id": assignee_id}
        assert tree is None

    def test_invalid_uuid_dropped_not_crashed(self):
        flat, _tree = _translate_export_filters({"assignee_id": "not-a-uuid"}, None)
        assert flat == {}

    def test_priority_and_q_passthrough(self):
        flat, _tree = _translate_export_filters({"priority": "high", "q": "login"}, None)
        assert flat == {"priority": "high", "q": "login"}

    def test_date_keys_coerced_to_date(self):
        flat, _tree = _translate_export_filters({"due_before": "2026-07-01"}, None)
        assert flat == {"due_before": date(2026, 7, 1)}

    def test_invalid_date_dropped(self):
        flat, _tree = _translate_export_filters({"due_after": "garbage"}, None)
        assert flat == {}

    def test_none_value_skipped(self):
        flat, _tree = _translate_export_filters({"priority": None}, None)
        assert flat == {}

    def test_scope_project_wins_over_filter_project(self):
        scope_id = uuid.uuid4()
        filter_id = uuid.uuid4()
        flat, _tree = _translate_export_filters({"project_id": str(filter_id)}, scope_id)
        assert flat["project_id"] == scope_id

    def test_filter_project_used_when_no_scope(self):
        filter_id = uuid.uuid4()
        flat, _tree = _translate_export_filters({"project_id": str(filter_id)}, None)
        assert flat["project_id"] == filter_id
