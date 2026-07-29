"""Request-scope derivation for analytics (analytics.md §2.4/§2.5/§3.1/§3.2).

Pure helpers: time-window parsing (RFC3339 UTC, half-open ``[from, to)``),
IANA timezone validation + the display-timezone fallback chain, metric
parameter validators, and the visibility-set fingerprint hash used to build
``analytics_snapshots.scope_key`` values (§2.5 R3/R4).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mesh.errors import ValidationError

DEFAULT_WINDOW_DAYS = 30
MAX_CYCLE_IDS = 20
MAX_PROJECT_IDS = 20

GRANULARITY_VALUES = ("day", "week", "month")
STATE_CATEGORY_VALUES = (
    "backlog",
    "todo",
    "in_progress",
    "in_review",
    "blocked",
    "done",
    "cancelled",
)
BURNDOWN_METRIC_VALUES = ("count", "points")
GRANULARITY_BUCKET_STEP = {"day": "1 day", "week": "1 week", "month": "1 month"}


def hash_id_set(ids: Iterable[uuid.UUID | str]) -> str:
    """Fingerprint of an id set: sha256 over the sorted string forms."""
    joined = ",".join(sorted(str(i) for i in ids))
    return hashlib.sha256(joined.encode()).hexdigest()


def assert_valid_timezone(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError, TypeError) as exc:
        raise ValidationError(
            "invalid IANA timezone", details={"tz": str(tz)[:64]}, code="invalid_timezone"
        ) from exc


def resolve_display_timezone(user, workspace, tz_param: str | None) -> str:
    """Fallback chain: request ``?tz=`` → users.timezone → workspace → UTC (§2.4)."""
    candidates = (
        tz_param,
        getattr(user, "timezone", None),
        getattr(workspace, "timezone", None),
        "UTC",
    )
    for candidate in candidates:
        if candidate:
            assert_valid_timezone(candidate)
            return candidate
    return "UTC"


def _parse_rfc3339_utc(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            "invalid RFC3339 timestamp", details={"value": str(raw)[:64]}, code="invalid_time_range"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValidationError(
            "timestamps must be RFC3339 UTC",
            details={"value": str(raw)[:64]},
            code="invalid_time_range",
        )
    return parsed.astimezone(UTC)


def parse_time_window(
    from_s: str | None, to_s: str | None, *, now: datetime
) -> tuple[datetime, datetime]:
    """Half-open ``[from, to)``; defaults to the trailing 30 days (§2.4)."""
    if from_s is None and to_s is None:
        return now - timedelta(days=DEFAULT_WINDOW_DAYS), now
    if from_s is None or to_s is None:
        raise ValidationError("from and to must be provided together", code="invalid_time_range")
    start, end = _parse_rfc3339_utc(from_s), _parse_rfc3339_utc(to_s)
    if start >= end:
        raise ValidationError(
            "from must be earlier than to",
            details={"from": from_s, "to": to_s},
            code="invalid_time_range",
        )
    return start, end


def validate_granularity(raw: str | None) -> str:
    value = raw or "day"
    if value not in GRANULARITY_VALUES:
        raise ValidationError(
            "granularity must be day, week or month",
            details={"granularity": value},
            code="validation_error",
        )
    return value


def validate_from_category(raw: str | None) -> str:
    value = raw or "in_progress"
    if value not in STATE_CATEGORY_VALUES:
        raise ValidationError(
            "from_category must be a valid state category",
            details={"from_category": value},
            code="validation_error",
        )
    return value


def validate_metric(raw: str | None) -> str:
    value = raw or "points"
    if value not in BURNDOWN_METRIC_VALUES:
        raise ValidationError(
            "metric must be count or points", details={"metric": value}, code="validation_error"
        )
    return value


def validate_member_type(raw: str | None) -> str | None:
    if raw is None:
        return None
    if raw not in ("human", "agent"):
        raise ValidationError(
            "member_type must be human or agent",
            details={"member_type": raw},
            code="validation_error",
        )
    return raw


def single_project_scope_key(project_id: uuid.UUID) -> str:
    return f"project:{project_id}"
