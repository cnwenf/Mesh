"""analytics.scope pure helpers (analytics.md §2.4/§2.5/§3.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from mesh.analytics.scope import (
    assert_valid_timezone,
    hash_id_set,
    parse_time_window,
    resolve_display_timezone,
    single_project_scope_key,
    validate_from_category,
    validate_granularity,
    validate_member_type,
    validate_metric,
)
from mesh.errors import ValidationError

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


class _User:
    def __init__(self, timezone_=None):
        self.timezone = timezone_


class _Workspace:
    def __init__(self, timezone_="UTC"):
        self.timezone = timezone_


def test_parse_window_explicit_ok():
    start, end = parse_time_window("2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z", now=NOW)
    assert start == datetime(2026, 7, 1, tzinfo=UTC)
    assert end == datetime(2026, 7, 8, tzinfo=UTC)


def test_parse_window_from_ge_to_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_time_window("2026-07-08T00:00:00Z", "2026-07-01T00:00:00Z", now=NOW)
    assert exc.value.code == "invalid_time_range"


def test_parse_window_equal_bounds_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_time_window("2026-07-01T00:00:00Z", "2026-07-01T00:00:00Z", now=NOW)
    assert exc.value.code == "invalid_time_range"


def test_parse_window_non_utc_offset_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_time_window("2026-07-01T08:00:00+08:00", "2026-07-08T00:00:00Z", now=NOW)
    assert exc.value.code == "invalid_time_range"


def test_parse_window_naive_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_time_window("2026-07-01T00:00:00", "2026-07-08T00:00:00Z", now=NOW)
    assert exc.value.code == "invalid_time_range"


def test_parse_window_malformed_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_time_window("not-a-date", "2026-07-08T00:00:00Z", now=NOW)
    assert exc.value.code == "invalid_time_range"


def test_parse_window_half_open_default_last_30_days():
    start, end = parse_time_window(None, None, now=NOW)
    assert end == NOW
    assert (end - start).days == 30


def test_parse_window_partial_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_time_window("2026-07-01T00:00:00Z", None, now=NOW)
    assert exc.value.code == "invalid_time_range"


def test_timezone_validation():
    assert_valid_timezone("UTC")
    assert_valid_timezone("Asia/Shanghai")
    assert_valid_timezone("America/New_York")
    with pytest.raises(ValidationError) as exc:
        assert_valid_timezone("Mars/Olympus")
    assert exc.value.code == "invalid_timezone"


def test_display_timezone_fallback_chain():
    assert resolve_display_timezone(_User(None), _Workspace("UTC"), None) == "UTC"
    assert (
        resolve_display_timezone(_User("Asia/Shanghai"), _Workspace("UTC"), None)
        == "Asia/Shanghai"
    )
    assert (
        resolve_display_timezone(_User("Asia/Shanghai"), _Workspace("UTC"), "America/New_York")
        == "America/New_York"
    )
    assert (
        resolve_display_timezone(_User(None), _Workspace("Asia/Shanghai"), None)
        == "Asia/Shanghai"
    )
    with pytest.raises(ValidationError) as exc:
        resolve_display_timezone(_User(None), _Workspace("UTC"), "Bad/Zone")
    assert exc.value.code == "invalid_timezone"


def test_hash_id_set_order_insensitive_and_stable():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert hash_id_set([a, b]) == hash_id_set([b, a])
    assert hash_id_set([a, b]) == hash_id_set([a, b])
    assert len(hash_id_set([a])) == 64
    assert hash_id_set([]) == hash_id_set([])


def test_single_project_scope_key():
    pid = uuid.uuid4()
    assert single_project_scope_key(pid) == f"project:{pid}"


def test_granularity_validator():
    assert validate_granularity(None) == "day"
    assert validate_granularity("week") == "week"
    assert validate_granularity("month") == "month"
    with pytest.raises(ValidationError) as exc:
        validate_granularity("year")
    assert exc.value.code == "validation_error"


def test_from_category_validator():
    assert validate_from_category(None) == "in_progress"
    assert validate_from_category("done") == "done"
    with pytest.raises(ValidationError) as exc:
        validate_from_category("flying")
    assert exc.value.code == "validation_error"


def test_metric_validator():
    assert validate_metric(None) == "points"
    assert validate_metric("count") == "count"
    with pytest.raises(ValidationError) as exc:
        validate_metric("story-points")
    assert exc.value.code == "validation_error"


def test_member_type_validator():
    assert validate_member_type(None) is None
    assert validate_member_type("agent") == "agent"
    with pytest.raises(ValidationError) as exc:
        validate_member_type("robot")
    assert exc.value.code == "validation_error"
