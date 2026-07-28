"""autopilot.cron — schedule math (5-field cron + IANA tz + misfire)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mesh.autopilot.cron import (
    MISFIRE_POLICIES,
    as_utc,
    is_known_timezone,
    missed_slots,
    next_fire_time,
    preview_schedule,
    validate_cron,
    validate_timezone,
)
from mesh.errors import ValidationError

BASE = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)  # a Monday


def test_validate_cron_accepts_standard_expression() -> None:
    validate_cron("0 9 * * 1-5")
    validate_cron("*/5 * * * *")


def test_validate_cron_rejects_malformed() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_cron("not a cron")
    assert excinfo.value.code == "invalid_cron"
    with pytest.raises(ValidationError):
        validate_cron("")
    with pytest.raises(ValidationError):
        validate_cron("61 9 * * *")  # minute out of range
    with pytest.raises(ValidationError):
        validate_cron("0 9 * *")  # 4 fields


def test_validate_timezone_known_and_unknown() -> None:
    assert is_known_timezone("Asia/Shanghai")
    zone = validate_timezone("Asia/Shanghai")
    assert str(zone) == "Asia/Shanghai"
    with pytest.raises(ValidationError) as excinfo:
        validate_timezone("Mars/Olympus_Mons")
    assert excinfo.value.code == "invalid_trigger_config"


def test_next_fire_time_respects_wall_clock_timezone() -> None:
    # 09:00 Shanghai = 01:00 UTC. From Monday 00:00 UTC the next weekday
    # 09:00 (Shanghai) is Monday 01:00 UTC.
    result = next_fire_time("0 9 * * 1-5", "Asia/Shanghai", after=BASE)
    assert result == datetime(2026, 7, 27, 1, 0, tzinfo=UTC)


def test_next_fire_time_weekday_skip() -> None:
    # Saturday 2026-07-25 02:00 UTC → next weekday fire is Monday 01:00 UTC.
    saturday = datetime(2026, 7, 25, 2, 0, tzinfo=UTC)
    result = next_fire_time("0 9 * * 1-5", "Asia/Shanghai", after=saturday)
    assert result == datetime(2026, 7, 27, 1, 0, tzinfo=UTC)


def test_preview_schedule_returns_count_ascending() -> None:
    upcoming = preview_schedule("0 9 * * *", "UTC", count=5, after=BASE)
    assert len(upcoming) == 5
    assert upcoming == sorted(upcoming)
    assert upcoming[0] == datetime(2026, 7, 27, 9, 0, tzinfo=UTC)


def test_preview_schedule_validates_inputs() -> None:
    with pytest.raises(ValidationError):
        preview_schedule("bogus", "UTC", count=3)
    # count clamps to >=1
    assert len(preview_schedule("0 0 * * *", "UTC", count=0, after=BASE)) == 1


def test_missed_slots_enumerates_window_and_caps() -> None:
    slots = missed_slots(
        "0 * * * *", "UTC", since=BASE, until=BASE + timedelta(hours=3, minutes=30), cap=50
    )
    assert slots == [BASE + timedelta(hours=n) for n in (1, 2, 3)]
    capped = missed_slots("0 * * * *", "UTC", since=BASE, until=BASE + timedelta(hours=10), cap=2)
    assert len(capped) == 2
    assert missed_slots("0 * * * *", "UTC", since=BASE, until=BASE, cap=10) == []


def test_as_utc_handles_naive_and_aware() -> None:
    naive = datetime(2026, 7, 27, 5, 0)
    assert as_utc(naive) == datetime(2026, 7, 27, 5, 0, tzinfo=UTC)
    aware = datetime(2026, 7, 27, 13, 0, tzinfo=validate_timezone("Asia/Shanghai"))
    assert as_utc(aware) == datetime(2026, 7, 27, 5, 0, tzinfo=UTC)


def test_misfire_policies_registry() -> None:
    assert MISFIRE_POLICIES == ("skip", "run_once", "run_all")
