"""README §6.13 unique priority matrix — derivation rules (comment-inbox.md)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time

import pytest

from mesh.comment_inbox.notifications import (
    _email_body,
    _render_notification_frame,
    in_quiet_hours,
    inbox_channel,
    policy_for,
)
from mesh.db.models.notification import Notification

pytestmark = pytest.mark.unit


def test_assigned_is_critical_piercing_resetting():
    policy = policy_for("assigned")
    assert policy.priority == "critical"
    assert policy.default_inbox is True
    assert policy.pierce_quiet_hours is True
    assert policy.reset_unread is True
    assert policy.email_default == "digest"  # assigned 可配 digest (§6.13)


def test_mentioned_is_critical_realtime_email():
    policy = policy_for("mentioned")
    assert policy.priority == "critical"
    assert policy.pierce_quiet_hours is True
    assert policy.reset_unread is True
    assert policy.email_default == "realtime"


def test_review_requested_is_critical():
    assert policy_for("review_requested").priority == "critical"
    assert policy_for("review_requested").reset_unread is True


def test_execution_success_is_normal_and_out_of_inbox_by_default():
    policy = policy_for("execution_finished", execution_status="completed")
    assert policy.priority == "normal"
    assert policy.default_inbox is False  # §6.13 R2: 留运行页
    assert policy.pierce_quiet_hours is False
    assert policy.reset_unread is False  # 执行成功不重置未读
    assert policy.email_default == "none"


def test_execution_failure_and_timeout_are_critical():
    for status in ("failed", "timeout"):
        policy = policy_for("execution_finished", execution_status=status)
        assert policy.priority == "critical"
        assert policy.default_inbox is True
        assert policy.pierce_quiet_hours is True
        assert policy.reset_unread is True
        assert policy.email_default == "realtime"


@pytest.mark.parametrize(
    "notification_type",
    ["comment_created", "status_changed", "subscribed_update", "due_soon"],
)
def test_normal_event_types(notification_type):
    policy = policy_for(notification_type)
    assert policy.priority == "normal"
    assert policy.default_inbox is True
    assert policy.pierce_quiet_hours is False
    assert policy.reset_unread is False  # 计数累加不重置未读
    assert policy.email_default == "digest"


def test_unknown_type_is_producer_bug():
    with pytest.raises(ValueError, match="unknown notification type"):
        policy_for("execution_cancelled")  # cancelled 不通知 (§6.13) — no type at all


def test_quiet_hours_simple_window():
    now = datetime(2026, 7, 27, 23, 30, tzinfo=UTC)
    assert in_quiet_hours(time(22, 0), time(23, 59), now) is True
    assert in_quiet_hours(time(0, 0), time(22, 0), now) is False


def test_quiet_hours_wrap_midnight():
    late = datetime(2026, 7, 27, 23, 30, tzinfo=UTC)
    early = datetime(2026, 7, 27, 6, 30, tzinfo=UTC)
    assert in_quiet_hours(time(22, 0), time(7, 0), late) is True
    assert in_quiet_hours(time(22, 0), time(7, 0), early) is True
    midday = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    assert in_quiet_hours(time(22, 0), time(7, 0), midday) is False


def test_quiet_hours_unset_means_never():
    now = datetime(2026, 7, 27, 3, 0, tzinfo=UTC)
    assert in_quiet_hours(None, None, now) is False
    assert in_quiet_hours(time(1, 0), None, now) is False


def test_inbox_channel_shape():
    member_id = uuid.uuid4()
    assert inbox_channel(member_id) == f"member:{member_id}:inbox"


def test_email_body_html_escapes_previews():
    notification = Notification(
        type="comment_created",
        priority="normal",
        payload={
            "actor_name": "<b>Mallory</b>",
            "title": 'T "quoted"',
            "preview": "<script>alert(1)</script>",
            "count": 1,
        },
    )
    body = _email_body(notification)
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "&lt;b&gt;Mallory&lt;/b&gt;" in body
    assert "comment_created" in body


def test_notification_wire_actor_is_null_when_producer_has_no_actor():
    notification = Notification(
        id=uuid.uuid4(),
        type="execution_finished",
        priority="critical",
        actor_id=None,
        payload={
            "actor_name": None,
            "actor_member_type": None,
            "title": "Agent run needs attention",
            "preview": "executor_unavailable",
        },
        created_at=datetime.now(UTC),
    )

    assert _render_notification_frame(notification)["actor"] is None


def test_notification_wire_actor_keeps_complete_member_snapshot():
    actor_id = uuid.uuid4()
    notification = Notification(
        id=uuid.uuid4(),
        type="comment_created",
        priority="normal",
        actor_id=actor_id,
        payload={"actor_name": "Alice", "actor_member_type": "human"},
        created_at=datetime.now(UTC),
    )

    assert _render_notification_frame(notification)["actor"] == {
        "id": str(actor_id),
        "member_type": "human",
        "name": "Alice",
    }
