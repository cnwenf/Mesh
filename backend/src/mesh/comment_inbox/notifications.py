"""Notification generation & fan-out (README §6.13 唯一权威矩阵).

Business transactions emit a ``notification.fanout`` outbox event (README
§6.6 — never block the request, never lose a notification); the relay
handler here resolves recipients, applies the ONE priority matrix, honors
preferences / quiet hours / per-issue mute, aggregates same-group bursts
inside the 60 s window, writes the delivery ledger, and publishes the
realtime events (``notification.created`` / ``inbox.unread_count``) through
the outbox projector path (README §6.7 — never directly).

Matrix rows (README §6.13 — no module defines its own tiers):

* ``assigned`` / ``mentioned`` → critical: inbox, pierce quiet hours, reset
  the group's read state;
* ``execution_finished`` failed/timeout → critical (same); ``completed`` →
  normal and NOT delivered unless the recipient explicitly subscribes
  ``execution_finished`` (then inbox, no read-reset, digest email);
  ``cancelled`` has no notification type at all (the initiator is never
  notified — producers simply do not fan out);
* ``review_requested`` → critical;
* everything else (``comment_created`` / ``status_changed`` /
  ``subscribed_update`` / ``due_soon``) → normal: inbox via group
  aggregation, digest email, never resets read state.

Self-suppression: the actor never receives their own notification; agent
recipients are skipped entirely (the inbox serves humans; agent re-trigger
loops are cut at the source, §6.13).
"""

from __future__ import annotations

import html
import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.comment import CommentMention
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.notification import (
    IssueSubscription,
    Notification,
    NotificationDelivery,
    NotificationPreference,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.outbox.service import emit_event, emit_realtime

logger = logging.getLogger("mesh.comment_inbox.notifications")

FANOUT_EVENT_TYPE = "notification.fanout"

DEFAULT_AGGREGATION_WINDOW_SECONDS = 60.0

# Email kinds routed through the auth mailer backends (dev-mailbox / SMTP).
EMAIL_KIND_REALTIME = "notification_realtime"
EMAIL_KIND_DIGEST = "notification_digest"

Priority = Literal["critical", "normal"]
EmailPolicy = Literal["none", "realtime", "digest"]


@dataclass(frozen=True)
class NotificationPolicy:
    """One row of the README §6.13 unique priority matrix."""

    priority: Priority
    default_inbox: bool
    pierce_quiet_hours: bool
    reset_unread: bool
    email_default: EmailPolicy


_CRITICAL_INBOX = dict(pierce_quiet_hours=True, reset_unread=True)


def policy_for(notification_type: str, *, execution_status: str | None = None) -> NotificationPolicy:
    """Derive the §6.13 policy for a notification type.

    ``execution_finished`` branches on the execution's terminal status:
    failed/timeout → critical; completed → normal and default-OFF for the
    inbox (explicit preference subscription required). Unknown types are a
    producer bug and raise ``ValueError``.
    """
    if notification_type == "assigned":
        return NotificationPolicy(
            priority="critical", default_inbox=True, email_default="digest", **_CRITICAL_INBOX
        )
    if notification_type == "mentioned":
        return NotificationPolicy(
            priority="critical", default_inbox=True, email_default="realtime", **_CRITICAL_INBOX
        )
    if notification_type == "review_requested":
        return NotificationPolicy(
            priority="critical", default_inbox=True, email_default="realtime", **_CRITICAL_INBOX
        )
    if notification_type == "execution_finished":
        if execution_status in ("failed", "timeout"):
            return NotificationPolicy(
                priority="critical",
                default_inbox=True,
                email_default="realtime",
                **_CRITICAL_INBOX,
            )
        # completed (or unset): normal, stays on the run page / timeline
        # unless explicitly subscribed; never resets the read group.
        return NotificationPolicy(
            priority="normal",
            default_inbox=False,
            pierce_quiet_hours=False,
            reset_unread=False,
            email_default="none",
        )
    if notification_type in ("comment_created", "status_changed", "subscribed_update", "due_soon"):
        return NotificationPolicy(
            priority="normal",
            default_inbox=True,
            pierce_quiet_hours=False,
            reset_unread=False,
            email_default="digest",
        )
    raise ValueError(f"unknown notification type: {notification_type!r} (README §6.13)")


def in_quiet_hours(start: time | None, end: time | None, now: datetime) -> bool:
    """True when ``now`` (UTC) falls inside the quiet window (may wrap midnight)."""
    if start is None or end is None:
        return False
    current = now.time()
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def inbox_channel(recipient_id: uuid.UUID) -> str:
    """The per-member inbox realtime channel (README §6.7 / §3.6)."""
    return f"member:{recipient_id}:inbox"


async def emit_notification_fanout(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    notification_type: str,
    actor_member_id: uuid.UUID | None = None,
    actor_kind: str = "member",
    actor_name: str | None = None,
    actor_member_type: str | None = None,
    issue_id: uuid.UUID | None = None,
    comment_id: uuid.UUID | None = None,
    execution_id: uuid.UUID | None = None,
    execution_status: str | None = None,
    recipient_ids: Sequence[uuid.UUID] | None = None,
    group_key: str | None = None,
    title: str | None = None,
    preview: str | None = None,
    extra: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> OutboxEvent:
    """Write the fan-out request into the caller's business transaction."""
    payload: dict[str, Any] = {
        "type": notification_type,
        "actor_member_id": str(actor_member_id) if actor_member_id else None,
        "actor_kind": actor_kind,
        "actor_name": actor_name,
        "actor_member_type": actor_member_type,
        "issue_id": str(issue_id) if issue_id else None,
        "comment_id": str(comment_id) if comment_id else None,
        "execution_id": str(execution_id) if execution_id else None,
        "execution_status": execution_status,
        "recipient_ids": [str(r) for r in (recipient_ids or ())],
        "group_key": group_key,
        "title": title,
        "preview": preview,
    }
    if extra:
        payload.update(extra)
    return await emit_event(
        session,
        workspace_id=workspace_id,
        event_type=FANOUT_EVENT_TYPE,
        payload=payload,
        idempotency_key=idempotency_key,
    )


# ---------------------------------------------------------------------------
# fan-out handler
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PreferenceView:
    in_app: bool
    email: EmailPolicy
    quiet_start: time | None
    quiet_end: time | None
    explicit_event_row: bool


async def _load_preference_view(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    notification_type: str,
    default_email: EmailPolicy,
) -> _PreferenceView:
    rows = (
        await session.execute(
            select(NotificationPreference).where(
                NotificationPreference.workspace_id == workspace_id,
                NotificationPreference.member_id == member_id,
            )
        )
    ).scalars().all()
    by_type = {row.event_type: row for row in rows}
    event_row = by_type.get(notification_type)
    all_row = by_type.get("all")
    chosen = event_row or all_row
    in_app = chosen.in_app if chosen is not None else True
    email: EmailPolicy = (
        chosen.email if chosen is not None else (default_email or "digest")
    )
    quiet_start: time | None = None
    quiet_end: time | None = None
    for row in rows:
        if row.quiet_hours_start is not None and row.quiet_hours_end is not None:
            quiet_start, quiet_end = row.quiet_hours_start, row.quiet_hours_end
            break
    return _PreferenceView(
        in_app=in_app,
        email=email,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        explicit_event_row=event_row is not None,
    )


async def _candidate_recipient_ids(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID | None,
    comment_id: uuid.UUID | None,
    explicit: Iterable[uuid.UUID],
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """(candidates, muted) — muted subscription rows suppress even implicit routing."""
    candidates: set[uuid.UUID] = set(explicit)
    muted: set[uuid.UUID] = set()
    if issue_id is not None:
        issue = await session.scalar(
            select(Issue).where(Issue.workspace_id == workspace_id, Issue.id == issue_id)
        )
        if issue is not None:
            # Default subscriptions (README §6.13): creator + assignee routed
            # implicitly; explicit subscription rows add the rest.
            for implicit in (issue.reporter_id, issue.assignee_id):
                if implicit is not None:
                    candidates.add(implicit)
        subscriptions = (
            await session.execute(
                select(IssueSubscription).where(
                    IssueSubscription.workspace_id == workspace_id,
                    IssueSubscription.issue_id == issue_id,
                )
            )
        ).scalars().all()
        for subscription in subscriptions:
            if subscription.muted:
                muted.add(subscription.subscriber_id)
            else:
                candidates.add(subscription.subscriber_id)
    if comment_id is not None:
        mentioned = (
            await session.execute(
                select(CommentMention.mentioned_id).where(
                    CommentMention.workspace_id == workspace_id,
                    CommentMention.comment_id == comment_id,
                    CommentMention.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        candidates.update(mentioned)
    return candidates - muted, muted


async def _human_active_members(
    session: AsyncSession, *, workspace_id: uuid.UUID, member_ids: set[uuid.UUID]
) -> list[Member]:
    if not member_ids:
        return []
    rows = (
        await session.execute(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.id.in_(member_ids),
                Member.status == "active",
                Member.member_type == "human",
            )
        )
    ).scalars().all()
    return list(rows)


async def _unread_count(
    session: AsyncSession, *, workspace_id: uuid.UUID, recipient_id: uuid.UUID
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.workspace_id == workspace_id,
            Notification.recipient_id == recipient_id,
            Notification.read_at.is_(None),
            Notification.archived_at.is_(None),
        )
    )
    return int(count or 0)


def _notification_payload(fanout: dict[str, Any]) -> dict[str, Any]:
    """The renderable snapshot stored on the notification row (§2.6)."""
    return {
        "actor_name": fanout.get("actor_name"),
        "actor_avatar_url": fanout.get("actor_avatar_url"),
        "actor_member_type": fanout.get("actor_member_type"),
        "title": fanout.get("title"),
        "issue_identifier": fanout.get("issue_identifier"),
        "preview": fanout.get("preview"),
        "count": 1,
        "changes": fanout.get("changes"),
        "execution_status": fanout.get("execution_status"),
    }


class NotificationFanoutHandler:
    """Relay handler for ``notification.fanout`` outbox events.

    Constructed with settings (+ optional mailer for email channels) and
    registered in ``workers/main.py::build_relay``. The relay runs the
    handler in a savepoint and marks the event published in the same
    transaction, so the whole fan-out is effectively exactly-once per event
    (a failed fan-out rolls back and retries — no partial notifications).
    """

    def __init__(
        self,
        *,
        aggregation_window_seconds: float = DEFAULT_AGGREGATION_WINDOW_SECONDS,
        mailer: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self._window = timedelta(seconds=aggregation_window_seconds)
        self._mailer = mailer
        self._clock = clock or (lambda: datetime.now(UTC))

    async def __call__(self, session: AsyncSession, event: OutboxEvent) -> None:
        await self.handle(session, event)

    async def handle(self, session: AsyncSession, event: OutboxEvent) -> None:
        fanout = event.payload or {}
        notification_type = fanout.get("type")
        if notification_type is None:
            raise ValueError("notification.fanout payload missing 'type'")
        policy = policy_for(
            notification_type, execution_status=fanout.get("execution_status")
        )
        workspace_id = event.workspace_id
        issue_id = _uuid_or_none(fanout.get("issue_id"))
        comment_id = _uuid_or_none(fanout.get("comment_id"))
        execution_id = _uuid_or_none(fanout.get("execution_id"))
        actor_raw = fanout.get("actor_member_id")
        actor_id = uuid.UUID(actor_raw) if actor_raw else None
        explicit = {uuid.UUID(raw) for raw in fanout.get("recipient_ids") or ()}
        group_key = fanout.get("group_key")

        candidates, _muted = await _candidate_recipient_ids(
            session,
            workspace_id=workspace_id,
            issue_id=issue_id,
            comment_id=comment_id,
            explicit=explicit,
        )
        if actor_id is not None:
            candidates.discard(actor_id)  # self-suppression (§6.13)
        recipients = await _human_active_members(
            session, workspace_id=workspace_id, member_ids=candidates
        )

        now = self._clock()
        for recipient in recipients:
            await self._deliver_one(
                session,
                fanout=fanout,
                policy=policy,
                workspace_id=workspace_id,
                notification_type=notification_type,
                recipient=recipient,
                actor_id=actor_id,
                issue_id=issue_id,
                comment_id=comment_id,
                execution_id=execution_id,
                group_key=group_key,
                now=now,
            )

    async def _deliver_one(
        self,
        session: AsyncSession,
        *,
        fanout: dict[str, Any],
        policy: NotificationPolicy,
        workspace_id: uuid.UUID,
        notification_type: str,
        recipient: Member,
        actor_id: uuid.UUID | None,
        issue_id: uuid.UUID | None,
        comment_id: uuid.UUID | None,
        execution_id: uuid.UUID | None,
        group_key: str | None,
        now: datetime,
    ) -> None:
        prefs = await _load_preference_view(
            session,
            workspace_id=workspace_id,
            member_id=recipient.id,
            notification_type=notification_type,
            default_email=policy.email_default,
        )
        # execution success enters the inbox ONLY on explicit subscription
        # (README §6.13 R2) — and then behaves like a normal event.
        if not policy.default_inbox:
            if not (prefs.explicit_event_row and prefs.in_app):
                return
        if not prefs.in_app and prefs.email == "none":
            return

        quiet = in_quiet_hours(prefs.quiet_start, prefs.quiet_end, now)
        suppressed_push = quiet and not policy.pierce_quiet_hours

        notification, unread_changed = await self._store_or_aggregate(
            session,
            policy=policy,
            workspace_id=workspace_id,
            notification_type=notification_type,
            recipient_id=recipient.id,
            actor_id=actor_id,
            actor_kind=fanout.get("actor_kind") or "member",
            issue_id=issue_id,
            comment_id=comment_id,
            execution_id=execution_id,
            group_key=group_key,
            fanout=fanout,
            now=now,
        )

        if prefs.in_app:
            await self._record_delivery(
                session,
                workspace_id=workspace_id,
                notification_id=notification.id,
                channel="in_app",
                destination_key="",
            )
        if prefs.email != "none":
            await self._handle_email(
                session,
                recipient=recipient,
                notification=notification,
                prefs=prefs,
                policy=policy,
                quiet=quiet,
            )

        if suppressed_push:
            return  # quiet hours: badge/inbox truth persists, no popup/push
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=inbox_channel(recipient.id),
            event="notification.created",
            data=_render_notification_frame(notification),
        )
        if unread_changed:
            count = await _unread_count(
                session, workspace_id=workspace_id, recipient_id=recipient.id
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=inbox_channel(recipient.id),
                event="inbox.unread_count",
                data={"count": count},
            )

    async def _store_or_aggregate(
        self,
        session: AsyncSession,
        *,
        policy: NotificationPolicy,
        workspace_id: uuid.UUID,
        notification_type: str,
        recipient_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        actor_kind: str,
        issue_id: uuid.UUID | None,
        comment_id: uuid.UUID | None,
        execution_id: uuid.UUID | None,
        group_key: str | None,
        fanout: dict[str, Any],
        now: datetime,
    ) -> tuple[Notification, bool]:
        """Insert a new notification or merge into the 60 s aggregation window.

        Returns (notification, unread_changed). Critical events reset a read
        group to unread (README §6.13 重新置未读); normal count increments
        never do.
        """
        if group_key:
            window_start = now - self._window
            existing = await session.scalar(
                select(Notification)
                .where(
                    Notification.workspace_id == workspace_id,
                    Notification.recipient_id == recipient_id,
                    Notification.group_key == group_key,
                    Notification.created_at >= window_start,
                )
                .order_by(Notification.created_at.desc())
            )
            if existing is not None:
                payload = dict(existing.payload or {})
                payload["count"] = int(payload.get("count") or 1) + 1
                for key in ("preview", "title", "actor_name", "actor_member_type"):
                    if fanout.get(key) is not None:
                        payload[key] = fanout[key]
                if comment_id is not None:
                    payload["latest_comment_id"] = str(comment_id)
                unread_changed = False
                if policy.reset_unread and existing.read_at is not None:
                    existing.read_at = None
                    unread_changed = True
                existing.payload = payload
                existing.updated_at = now
                await session.flush()
                return existing, unread_changed

        notification = Notification(
            workspace_id=workspace_id,
            recipient_id=recipient_id,
            type=notification_type,
            priority=policy.priority,
            actor_kind=actor_kind if actor_id is not None else None,
            actor_id=actor_id,
            issue_id=issue_id,
            comment_id=comment_id,
            execution_id=execution_id,
            payload=_notification_payload(fanout),
            group_key=group_key,
        )
        session.add(notification)
        await session.flush()
        return notification, True

    async def _record_delivery(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        notification_id: uuid.UUID,
        channel: str,
        destination_key: str,
        state: str = "sent",
        provider: str | None = None,
        external_target: str | None = None,
        error: str | None = None,
    ) -> None:
        """Insert one ledger row; ``uq_delivery`` makes retries idempotent."""
        existing = await session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notification_id,
                NotificationDelivery.channel == channel,
                NotificationDelivery.destination_key == destination_key,
            )
        )
        if existing is not None:
            return
        session.add(
            NotificationDelivery(
                workspace_id=workspace_id,
                notification_id=notification_id,
                channel=channel,
                destination_key=destination_key,
                provider=provider,
                external_target=external_target,
                state=state,
                sent_at=datetime.now(UTC) if state == "sent" else None,
                error=error,
            )
        )
        await session.flush()

    async def _handle_email(
        self,
        session: AsyncSession,
        *,
        recipient: Member,
        notification: Notification,
        prefs: _PreferenceView,
        policy: NotificationPolicy,
        quiet: bool,
    ) -> None:
        send_now = prefs.email == "realtime" and (policy.pierce_quiet_hours or not quiet)
        email_address = await self._recipient_email(session, recipient)
        if email_address is None:
            return
        if send_now and self._mailer is not None:
            body = _email_body(notification)
            try:
                await self._mailer.deliver(email_address, EMAIL_KIND_REALTIME, body)
                await self._record_delivery(
                    session,
                    workspace_id=notification.workspace_id,
                    notification_id=notification.id,
                    channel="email",
                    destination_key=email_address,
                    provider="email_smtp",
                    external_target=email_address,
                    state="sent",
                )
                return
            except Exception as exc:  # ledger keeps the failure reason ONLY
                logger.warning("realtime notification email failed: %s", exc)
                await self._record_delivery(
                    session,
                    workspace_id=notification.workspace_id,
                    notification_id=notification.id,
                    channel="email",
                    destination_key=email_address,
                    provider="email_smtp",
                    external_target=email_address,
                    state="failed",
                    error=type(exc).__name__,
                )
                return
        # digest (or realtime suppressed by quiet hours): pending row for the
        # digest sweep.
        await self._record_delivery(
            session,
            workspace_id=notification.workspace_id,
            notification_id=notification.id,
            channel="email",
            destination_key=email_address,
            provider="email_smtp",
            external_target=email_address,
            state="pending",
        )

    async def _recipient_email(
        self, session: AsyncSession, recipient: Member
    ) -> str | None:
        if recipient.user_id is None:
            return None
        return await session.scalar(select(User.email).where(User.id == recipient.user_id))


def _uuid_or_none(raw: Any) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _render_notification_frame(notification: Notification) -> dict[str, Any]:
    """The wire shape for notification.created / inbox list rows.

    ``issue`` is rendered from the payload snapshot (§2.6 — stays readable
    after the issue is deleted); ``identifier``/``title`` may be None for
    legacy rows or non-issue notifications.
    """
    payload = notification.payload or {}
    issue: dict[str, Any] | None = None
    if notification.issue_id is not None:
        issue = {
            "id": str(notification.issue_id),
            "identifier": payload.get("issue_identifier"),
            "title": payload.get("title"),
        }
    return {
        "id": str(notification.id),
        "type": notification.type,
        "priority": notification.priority,
        "issue_id": str(notification.issue_id) if notification.issue_id else None,
        "issue": issue,
        "comment_id": str(notification.comment_id) if notification.comment_id else None,
        "execution_id": str(notification.execution_id) if notification.execution_id else None,
        "group_key": notification.group_key,
        "actor": {
            "id": str(notification.actor_id) if notification.actor_id else None,
            "member_type": payload.get("actor_member_type"),
            "name": payload.get("actor_name"),
        },
        "preview": payload.get("preview"),
        "title": payload.get("title"),
        "count": payload.get("count") or 1,
        "read_at": notification.read_at.isoformat().replace("+00:00", "Z")
        if notification.read_at
        else None,
        "archived_at": notification.archived_at.isoformat().replace("+00:00", "Z")
        if notification.archived_at
        else None,
        "created_at": notification.created_at.isoformat().replace("+00:00", "Z"),
        "latest_comment_id": payload.get("latest_comment_id"),
    }


def _email_body(notification: Notification) -> str:
    """Plain-text email body; previews are HTML-escaped (§4.4 injection guard)."""
    payload = notification.payload or {}
    actor = html.escape(str(payload.get("actor_name") or ""), quote=True)
    title = html.escape(str(payload.get("title") or ""), quote=True)
    preview = html.escape(str(payload.get("preview") or ""), quote=True)
    lines = [f"Mesh notification: {notification.type}"]
    if actor:
        lines.append(f"From: {actor}")
    if title:
        lines.append(f"Issue: {title}")
    if preview:
        lines.append(f"Preview: {preview}")
    return "\n".join(lines) + "\n"


async def send_digest_emails(
    session: AsyncSession,
    *,
    mailer: Any,
    batch_size: int = 200,
) -> int:
    """One digest sweep: aggregate pending email rows per recipient, send,
    and mark the ledger sent. Returns the number of emails sent.

    ``uq_delivery`` keeps the sweep idempotent across crashes; failures are
    recorded as ``failed`` with the reason only (R3 — never routing data).
    """
    pending = (
        await session.execute(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.channel == "email",
                NotificationDelivery.state == "pending",
            )
            .order_by(NotificationDelivery.created_at.asc())
            .limit(batch_size)
        )
    ).scalars().all()
    if not pending:
        return 0

    by_target: dict[str, list[NotificationDelivery]] = {}
    for row in pending:
        target = row.external_target or row.destination_key
        by_target.setdefault(target, []).append(row)

    sent_emails = 0
    now = datetime.now(UTC)
    for target, rows in by_target.items():
        notifications = (
            await session.execute(
                select(Notification).where(
                    Notification.id.in_([row.notification_id for row in rows])
                )
            )
        ).scalars().all()
        rendered = [_email_body(notification) for notification in notifications]
        body = f"Mesh notification digest ({len(rendered)} items)\n\n" + "\n---\n".join(rendered)
        try:
            await mailer.deliver(target, EMAIL_KIND_DIGEST, body)
            for row in rows:
                row.state = "sent"
                row.sent_at = now
            sent_emails += 1
        except Exception as exc:
            logger.warning("digest email to %s failed: %s", target, exc)
            for row in rows:
                row.state = "failed"
                row.error = type(exc).__name__
    await session.flush()
    return sent_emails


__all__ = [
    "EMAIL_KIND_DIGEST",
    "EMAIL_KIND_REALTIME",
    "FANOUT_EVENT_TYPE",
    "NotificationFanoutHandler",
    "NotificationPolicy",
    "emit_notification_fanout",
    "in_quiet_hours",
    "inbox_channel",
    "policy_for",
    "send_digest_emails",
]
