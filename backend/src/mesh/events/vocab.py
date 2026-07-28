"""Canonical realtime event vocabulary (README §6.7 注册表 — 唯一权威).

Every WebSocket/SSE event name must come from this registry. The registry is
kept in sync with the §6.7 table in docs/specs/README.md by
``tests/unit/test_vocab.py`` (which parses the README table and compares) and
the docs-level CI gate ``tests/docs/check_event_vocab.py``.

New events MUST be registered here (and in README §6.7) before any module
references them — zero tolerance for vocabulary drift.
"""

from __future__ import annotations

# workspace / member / invitation
WORKSPACE_EVENTS: frozenset[str] = frozenset(
    {
        "workspace.updated",
        "workspace.deleted",
        "member.added",
        "member.updated",
        "member.removed",
        "member.role_changed",
        "member.presence",
        "invitation.redeemed",
    }
)

# project / milestone / cycle
PROJECT_EVENTS: frozenset[str] = frozenset(
    {
        "project.created",
        "project.updated",
        "project.archived",
        "project.unarchived",
        "project.deleted",
        "project_update.added",
        "milestone.created",
        "milestone.updated",
        "milestone.deleted",
        "cycle.updated",
    }
)

# issue / dependency / view
ISSUE_EVENTS: frozenset[str] = frozenset(
    {
        "issue.created",
        "issue.updated",
        "issue.deleted",
        "issue.moved",
        "issue.project_changed",
        "issue.labels_changed",
        "issue.custom_field_changed",
        "dependency.changed",
        "view.updated",
        "view.presence",
        "view.wip_exceeded",
    }
)

# comment / reaction / notification
COMMENT_EVENTS: frozenset[str] = frozenset(
    {
        "comment.created",
        "comment.updated",
        "comment.deleted",
        "comment.resolved",
        "reaction.changed",
        "notification.created",
        "notification.read",
        "inbox.unread_count",
    }
)

# label / custom field
LABEL_EVENTS: frozenset[str] = frozenset(
    {
        "label.created",
        "label.updated",
        "label.deleted",
        "custom_field.updated",
        "custom_field_option.updated",
    }
)

# agent
AGENT_EVENTS: frozenset[str] = frozenset(
    {
        "agent.created",
        "agent.updated",
        "agent.deleted",
        "agent.lifecycle_changed",
        "agent.presence",
        "agent.trigger_skipped",
    }
)

# execution / approval / queue / runtime
EXECUTION_EVENTS: frozenset[str] = frozenset(
    {
        "execution.queued",
        "execution.claimed",
        "execution.started",
        "execution.progress",
        "execution.completed",
        "execution.failed",
        "execution.timeout",
        "execution.cancelled",
        "execution.requeued",
        "execution.awaiting_approval",
        "execution.log",
        "approval.created",
        "approval.decided",
        "queue.depth_changed",
        "runtime.activated",
        "runtime.online",
        "runtime.offline",
        "runtime.degraded",
        "runtime.paused",
    }
)

# skill / attachment
SKILL_ATTACHMENT_EVENTS: frozenset[str] = frozenset(
    {
        "skill_import.progress",
        "skill.changed",
        "skill.update_available",
        "skill.approval_required",
        "attachment.processed",
        "attachment.deleted",
    }
)

# squad
SQUAD_EVENTS: frozenset[str] = frozenset(
    {
        "squad.updated",
        "squad.archived",
        "squad_member.changed",
        "squad_task.status_changed",
        "squad_activity.created",
        "squad_message.created",
        "squad_assignment.changed",
        # SSE orchestration progress stream frames (squad.md §3.2 / §3.5),
        # persisted on the per-task channel ``squad_task:{id}``.
        "task.status",
        "subtask.created",
        "subtask.assigned",
        "plan.submitted",
        "task.aggregated",
    }
)

# autopilot / webhook
AUTOPILOT_EVENTS: frozenset[str] = frozenset(
    {
        "autopilot.updated",
        "autopilot.rate_limited",
        "autopilot_runs.status_changed",
        "autopilot_runs.approval_required",
        "webhook_events.received",
    }
)

# platform capabilities
PLATFORM_EVENTS: frozenset[str] = frozenset(
    {
        "onboarding.progress",
        "onboarding.completed",
        "integration.updated",
        "integration.event_ingested",
        "data_job.updated",
        "favorites.changed",
    }
)

# chat streaming in-stream frames (§6.8)
CHAT_STREAM_EVENTS: frozenset[str] = frozenset(
    {
        "message.created",
        "message.delta",
        "message.done",
        "message.interrupted",
        "error",
        "ping",
    }
)

# session / auth (auth.md §3.7/§5.6 — revocation broadcast)
SESSION_AUTH_EVENTS: frozenset[str] = frozenset(
    {
        "session.revoked",
    }
)

EVENT_VOCABULARY: frozenset[str] = frozenset().union(
    WORKSPACE_EVENTS,
    PROJECT_EVENTS,
    ISSUE_EVENTS,
    COMMENT_EVENTS,
    LABEL_EVENTS,
    AGENT_EVENTS,
    EXECUTION_EVENTS,
    SKILL_ATTACHMENT_EVENTS,
    SQUAD_EVENTS,
    AUTOPILOT_EVENTS,
    PLATFORM_EVENTS,
    CHAT_STREAM_EVENTS,
    SESSION_AUTH_EVENTS,
)

# Internal outbox event_type vocabulary (README §6.6 domain events — these are
# NOT realtime event names; keep in sync with the OUTBOX_EVENT_TYPES whitelist
# in tests/docs/check_event_vocab.py).
OUTBOX_INTERNAL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "issue.assigned",
        "issue.status_changed",
        "execution.enqueue",
        "execution.finished",
        "notification.fanout",
        "attachment.scan_requested",
        "data_job.enqueue",
        "data_job.resume",
        "squad.plan_decided",
    }
)

# The single marker event_type that carries realtime payloads through the outbox
# (README §6.6 unique write path).
REALTIME_PUBLISH = "realtime.publish"


class UnregisteredEventError(ValueError):
    """Raised when an event name is not in the §6.7 registry."""

    def __init__(self, event: str) -> None:
        self.event = event
        super().__init__(f"unregistered realtime event name: {event!r} (README §6.7)")


def is_realtime_event(name: str) -> bool:
    """True when ``name`` is a registered realtime event."""
    return name in EVENT_VOCABULARY


def require_realtime_event(name: str) -> str:
    """Return ``name`` if registered, else raise :class:`UnregisteredEventError`."""
    if name not in EVENT_VOCABULARY:
        raise UnregisteredEventError(name)
    return name
