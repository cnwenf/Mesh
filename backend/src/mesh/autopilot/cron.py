"""Schedule math for the ``schedule`` trigger (autopilot.md §2.6 / §4.5).

A schedule trigger carries a 5-field standard cron expression AND an
explicit IANA timezone — server UTC must never silently shift a user's
intended wall-clock time (autopilot.md P2). ``misfire_policy`` decides how
missed slots are treated when the scheduler catches up after downtime:

* ``skip`` — advance to the next future slot, fire nothing;
* ``run_once`` — fire exactly once for the whole missed window;
* ``run_all`` — fire one run per missed slot (capped by the caller).

Next-run computation is croniter-backed (battle-tested cron engine); the
thin wrappers here own validation + timezone handling so the rest of the
module never touches croniter directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, available_timezones

from croniter import croniter

from mesh.errors import ValidationError

MISFIRE_POLICIES = ("skip", "run_once", "run_all")

_CRON_FIELD_COUNT = 5


def validate_timezone(name: str) -> ZoneInfo:
    """Resolve an explicit IANA timezone; unknown → 400 ``invalid_trigger_config``."""
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise ValidationError(
            "unknown IANA timezone",
            code="invalid_trigger_config",
            details={"timezone": name},
        ) from exc


def is_known_timezone(name: str) -> bool:
    """Cheap membership check against the IANA database."""
    return name in available_timezones()


def validate_cron(expression: str) -> None:
    """Validate a 5-field cron expression; malformed → 400 ``invalid_cron``."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValidationError(
            "cron expression required", code="invalid_cron", details={"cron": expression}
        )
    fields = expression.split()
    if len(fields) != _CRON_FIELD_COUNT:
        raise ValidationError(
            "cron must have exactly 5 fields (minute hour day-of-month month day-of-week)",
            code="invalid_cron",
            details={"cron": expression},
        )
    try:
        croniter(expression)
    except (ValueError, KeyError) as exc:
        raise ValidationError(
            "invalid cron expression",
            code="invalid_cron",
            details={"cron": expression, "reason": str(exc)[:200]},
        ) from exc


def next_fire_time(
    cron_expression: str, tz_name: str, *, after: datetime | None = None
) -> datetime:
    """The next fire time AFTER ``after`` (default now), returned in UTC.

    Computation happens in the rule's wall-clock timezone, then converts to
    UTC for storage/comparison (README §6.18: storage is always UTC).
    """
    tz = validate_timezone(tz_name)
    base = after if after is not None else datetime.now(UTC)
    local_base = base.astimezone(tz)
    local_next = croniter(cron_expression, local_base).get_next(datetime)
    if local_next.tzinfo is None:
        local_next = local_next.replace(tzinfo=tz)
    return local_next.astimezone(UTC)


def preview_schedule(
    cron_expression: str, tz_name: str, *, count: int = 5, after: datetime | None = None
) -> list[datetime]:
    """The next ``count`` fire times (UTC) — the editor's live preview (§4.2)."""
    validate_cron(cron_expression)
    tz = validate_timezone(tz_name)
    base = after if after is not None else datetime.now(UTC)
    iterator = croniter(cron_expression, base.astimezone(tz))
    result: list[datetime] = []
    for _ in range(max(1, count)):
        local_next = iterator.get_next(datetime)
        if local_next.tzinfo is None:
            local_next = local_next.replace(tzinfo=tz)
        result.append(local_next.astimezone(UTC))
    return result


def missed_slots(
    cron_expression: str, tz_name: str, *, since: datetime, until: datetime, cap: int
) -> list[datetime]:
    """Every fire time in (since, until] — the ``run_all`` catch-up list.

    Bounded by ``cap`` (the caller logs when the cap truncates — no silent
    truncation). Slots are returned oldest-first in UTC.
    """
    tz = validate_timezone(tz_name)
    iterator = croniter(cron_expression, since.astimezone(tz))
    slots: list[datetime] = []
    for _ in range(max(0, cap)):
        local_next = iterator.get_next(datetime)
        if local_next.tzinfo is None:
            local_next = local_next.replace(tzinfo=tz)
        utc_next = local_next.astimezone(UTC)
        if utc_next > until:
            break
        slots.append(utc_next)
    return slots


def as_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC (naive values are assumed UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "MISFIRE_POLICIES",
    "as_utc",
    "is_known_timezone",
    "missed_slots",
    "next_fire_time",
    "preview_schedule",
    "validate_cron",
    "validate_timezone",
]
