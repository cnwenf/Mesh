"""Inbox operations & notification preferences (comment-inbox.md §3.2).

The inbox is the human-facing read side of the notification system:
cursor-paginated listing (flat or grouped by ``group_key`` with the §6.14
overall-cursor contract), unread counting on the partial index, read /
unread / archive state flips (broadcast for multi-end sync), per-issue
mute, and the preference matrix CRUD.

Every state change emits the §3.6 realtime events (``notification.read`` /
``inbox.unread_count``) through the outbox projector path — never directly.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.comment_inbox import subscriptions
from mesh.comment_inbox.notifications import _render_notification_frame, inbox_channel
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.notification import (
    EMAIL_POLICY_VALUES,
    Notification,
    NotificationPreference,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import NotFoundError, ValidationError
from mesh.outbox.service import emit_realtime

_NOTIFICATION_NOT_FOUND = "notification not found"

INBOX_FILTERS = ("all", "unread", "mentions", "assigned", "agent")


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _encode_group_cursor(latest: datetime, group_key: str) -> str:
    payload = json.dumps(
        {"t": latest.isoformat(), "g": group_key}, separators=(",", ":"), sort_keys=True
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_group_cursor(raw: str) -> tuple[datetime, str]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return datetime.fromisoformat(payload["t"]), str(payload["g"])
    except Exception as exc:
        raise ValidationError(
            "invalid pagination cursor", details={"cursor": raw[:64]}, code="invalid_cursor"
        ) from exc


class InboxService:
    """Read/mutate the caller's inbox within one workspace."""

    def __init__(
        self,
        session_factory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # listing
    # ------------------------------------------------------------------

    async def list_notifications(
        self,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        limit: int = 30,
        cursor: str | None = None,
        inbox_filter: str = "all",
        notification_type: str | None = None,
        grouped: bool = False,
        include_archived: bool = False,
    ) -> dict:
        if inbox_filter not in INBOX_FILTERS:
            raise ValidationError(
                "invalid filter", details={"filter": inbox_filter[:32]}
            )
        if limit < 1:
            raise ValidationError("limit must be >= 1", code="invalid_limit")
        limit = min(limit, 100)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            if grouped:
                return await self._list_grouped(
                    session,
                    workspace_id=workspace_id,
                    member=member,
                    limit=limit,
                    cursor=cursor,
                    inbox_filter=inbox_filter,
                    notification_type=notification_type,
                    include_archived=include_archived,
                )
            return await self._list_flat(
                session,
                workspace_id=workspace_id,
                member=member,
                limit=limit,
                cursor=cursor,
                inbox_filter=inbox_filter,
                notification_type=notification_type,
                include_archived=include_archived,
            )

    def _apply_filters(
        self,
        stmt,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        inbox_filter: str,
        notification_type: str | None,
        include_archived: bool,
    ):
        stmt = stmt.where(
            Notification.workspace_id == workspace_id,
            Notification.recipient_id == member.id,
        )
        if not include_archived:
            stmt = stmt.where(Notification.archived_at.is_(None))
        if inbox_filter == "unread":
            stmt = stmt.where(Notification.read_at.is_(None))
        elif inbox_filter == "mentions":
            stmt = stmt.where(Notification.type == "mentioned")
        elif inbox_filter == "assigned":
            stmt = stmt.where(Notification.type == "assigned")
        elif inbox_filter == "agent":
            stmt = stmt.where(Notification.payload["actor_member_type"].astext == "agent")
        if notification_type is not None:
            stmt = stmt.where(Notification.type == notification_type)
        return stmt

    async def _list_flat(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        limit: int,
        cursor: str | None,
        inbox_filter: str,
        notification_type: str | None,
        include_archived: bool,
    ) -> dict:
        from mesh.api.pagination import paginate

        stmt = select(Notification)
        stmt = self._apply_filters(
            stmt,
            workspace_id=workspace_id,
            member=member,
            inbox_filter=inbox_filter,
            notification_type=notification_type,
            include_archived=include_archived,
        )
        page = await paginate(
            session,
            stmt,
            sort_column=Notification.created_at,
            id_column=Notification.id,
            sort_value_of=lambda row: row.created_at,
            id_of=lambda row: row.id,
            cursor=cursor,
            limit=limit,
            descending=True,
        )
        return {
            "data": [_render_notification_frame(row) for row in page.items],
            "next_cursor": page.next_cursor,
        }

    async def _list_grouped(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        limit: int,
        cursor: str | None,
        inbox_filter: str,
        notification_type: str | None,
        include_archived: bool,
    ) -> dict:
        """Group by ``group_key`` (ungrouped rows group by their id).

        Overall-cursor contract (README §6.14): one ``next_cursor`` for the
        whole response, keyed on (latest activity DESC, group key DESC).
        """
        group_key_expr = func.coalesce(
            Notification.group_key, func.concat("notification:", Notification.id)
        )
        latest_agg = func.max(Notification.created_at)
        stmt = select(
            group_key_expr.label("group_key"),
            latest_agg.label("latest"),
            func.count().label("count"),
        )
        stmt = self._apply_filters(
            stmt,
            workspace_id=workspace_id,
            member=member,
            inbox_filter=inbox_filter,
            notification_type=notification_type,
            include_archived=include_archived,
        )
        stmt = stmt.group_by(group_key_expr)
        if cursor is not None:
            latest_cursor, group_cursor = _decode_group_cursor(cursor)
            stmt = stmt.having(
                or_(
                    latest_agg < latest_cursor,
                    and_(latest_agg == latest_cursor, group_key_expr < group_cursor),
                )
            )
        stmt = stmt.order_by(latest_agg.desc(), group_key_expr.desc())
        rows = (await session.execute(stmt.limit(limit + 1))).all()
        has_next = len(rows) > limit
        kept = rows[:limit]
        groups: list[dict] = []
        for row in kept:
            representative = await session.scalar(
                select(Notification)
                .where(
                    Notification.workspace_id == workspace_id,
                    Notification.recipient_id == member.id,
                    func.coalesce(
                        Notification.group_key,
                        func.concat("notification:", Notification.id),
                    ) == row.group_key,
                )
                .order_by(Notification.created_at.desc())
            )
            frame = _render_notification_frame(representative)
            frame["count"] = int(row.count)
            frame["group_key"] = row.group_key
            groups.append(frame)
        next_cursor = None
        if has_next and kept:
            last = kept[-1]
            next_cursor = _encode_group_cursor(last.latest, last.group_key)
        return {"data": groups, "next_cursor": next_cursor}

    # ------------------------------------------------------------------
    # unread count
    # ------------------------------------------------------------------

    async def unread_count(
        self, *, workspace_id: uuid.UUID, member: Member
    ) -> int:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            count = await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.workspace_id == workspace_id,
                    Notification.recipient_id == member.id,
                    Notification.read_at.is_(None),
                    Notification.archived_at.is_(None),
                )
            )
            return int(count or 0)

    # ------------------------------------------------------------------
    # state flips
    # ------------------------------------------------------------------

    async def _load_own_notification(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        notification_id: uuid.UUID,
    ) -> Notification:
        notification = await session.scalar(
            select(Notification).where(
                Notification.workspace_id == workspace_id,
                Notification.id == notification_id,
                Notification.recipient_id == member.id,
            )
        )
        if notification is None:
            raise NotFoundError(_NOTIFICATION_NOT_FOUND)
        return notification

    async def mark_read(
        self, *, workspace_id: uuid.UUID, member: Member, notification_id: uuid.UUID, read: bool
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            notification = await self._load_own_notification(
                session, workspace_id=workspace_id, member=member,
                notification_id=notification_id,
            )
            now = self._clock()
            notification.read_at = now if read else None
            notification.updated_at = now
            await session.flush()
            await self._broadcast_state(session, workspace_id=workspace_id, member=member,
                                         notification=notification)
            return _render_notification_frame(notification)

    async def read_all(
        self,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        inbox_filter: str | None = None,
    ) -> int:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            stmt = (
                update(Notification)
                .where(
                    Notification.workspace_id == workspace_id,
                    Notification.recipient_id == member.id,
                    Notification.read_at.is_(None),
                    Notification.archived_at.is_(None),
                )
                .values(read_at=self._clock(), updated_at=self._clock())
            )
            if inbox_filter == "mentions":
                stmt = stmt.where(Notification.type == "mentioned")
            elif inbox_filter == "assigned":
                stmt = stmt.where(Notification.type == "assigned")
            result = await session.execute(stmt)
            await self._broadcast_unread_count(
                session, workspace_id=workspace_id, member=member
            )
            return int(result.rowcount or 0)

    async def set_archived(
        self, *, workspace_id: uuid.UUID, member: Member, notification_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            notification = await self._load_own_notification(
                session, workspace_id=workspace_id, member=member,
                notification_id=notification_id,
            )
            now = self._clock()
            notification.archived_at = now
            notification.updated_at = now
            await session.flush()
            await self._broadcast_unread_count(
                session, workspace_id=workspace_id, member=member
            )
            return _render_notification_frame(notification)

    async def archive_read(self, *, workspace_id: uuid.UUID, member: Member) -> int:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = self._clock()
            result = await session.execute(
                update(Notification)
                .where(
                    Notification.workspace_id == workspace_id,
                    Notification.recipient_id == member.id,
                    Notification.read_at.is_not(None),
                    Notification.archived_at.is_(None),
                )
                .values(archived_at=now, updated_at=now)
            )
            return int(result.rowcount or 0)

    async def _broadcast_state(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        notification: Notification,
    ) -> None:
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=inbox_channel(member.id),
            event="notification.read",
            data={
                "id": str(notification.id),
                "read_at": _isoformat(notification.read_at),
            },
        )
        await self._broadcast_unread_count(session, workspace_id=workspace_id, member=member)

    async def _broadcast_unread_count(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, member: Member
    ) -> None:
        count = await session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.workspace_id == workspace_id,
                Notification.recipient_id == member.id,
                Notification.read_at.is_(None),
                Notification.archived_at.is_(None),
            )
        )
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=inbox_channel(member.id),
            event="inbox.unread_count",
            data={"count": int(count or 0)},
        )

    # ------------------------------------------------------------------
    # per-issue mute (§6.13 一键静音)
    # ------------------------------------------------------------------

    async def set_issue_muted(
        self,
        *,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        member: Member,
        muted: bool,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            issue = await session.scalar(
                select(Issue).where(
                    Issue.workspace_id == workspace_id,
                    Issue.id == issue_id,
                    Issue.deleted_at.is_(None),
                )
            )
            if issue is None:
                raise NotFoundError("issue not found")
            subscription = await subscriptions.set_muted(
                session,
                workspace_id=workspace_id,
                issue_id=issue_id,
                subscriber_id=member.id,
                muted=muted,
            )
            return {
                "issue_id": str(issue_id),
                "muted": subscription.muted,
                "reason": subscription.reason,
            }

    # ------------------------------------------------------------------
    # preferences (§2.7)
    # ------------------------------------------------------------------

    async def get_preferences(
        self, *, workspace_id: uuid.UUID, member: Member
    ) -> list[dict]:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            rows = (
                await session.execute(
                    select(NotificationPreference)
                    .where(
                        NotificationPreference.workspace_id == workspace_id,
                        NotificationPreference.member_id == member.id,
                    )
                    .order_by(NotificationPreference.event_type)
                )
            ).scalars().all()
            return [_render_preference(row) for row in rows]

    async def put_preferences(
        self,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        entries: list[dict],
    ) -> list[dict]:
        for entry in entries:
            event_type = entry.get("event_type")
            if not event_type or not isinstance(event_type, str) or len(event_type) > 64:
                raise ValidationError("invalid event_type", code="validation_error")
            email = entry.get("email", "digest")
            if email not in EMAIL_POLICY_VALUES:
                raise ValidationError(
                    "invalid email policy", details={"email": str(email)[:16]}
                )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = self._clock()
            for entry in entries:
                existing = await session.scalar(
                    select(NotificationPreference).where(
                        NotificationPreference.workspace_id == workspace_id,
                        NotificationPreference.member_id == member.id,
                        NotificationPreference.event_type == entry["event_type"],
                    )
                )
                quiet_start = _parse_time(entry.get("quiet_hours_start"))
                quiet_end = _parse_time(entry.get("quiet_hours_end"))
                if (quiet_start is None) != (quiet_end is None):
                    raise ValidationError(
                        "quiet hours require both start and end", code="validation_error"
                    )
                if existing is None:
                    session.add(
                        NotificationPreference(
                            workspace_id=workspace_id,
                            member_id=member.id,
                            event_type=entry["event_type"],
                            in_app=bool(entry.get("in_app", True)),
                            email=entry.get("email", "digest"),
                            quiet_hours_start=quiet_start,
                            quiet_hours_end=quiet_end,
                        )
                    )
                else:
                    existing.in_app = bool(entry.get("in_app", existing.in_app))
                    existing.email = entry.get("email", existing.email)
                    existing.quiet_hours_start = quiet_start
                    existing.quiet_hours_end = quiet_end
                    existing.updated_at = now
            await session.flush()
            rows = (
                await session.execute(
                    select(NotificationPreference)
                    .where(
                        NotificationPreference.workspace_id == workspace_id,
                        NotificationPreference.member_id == member.id,
                    )
                    .order_by(NotificationPreference.event_type)
                )
            ).scalars().all()
            return [_render_preference(row) for row in rows]


def _render_preference(row: NotificationPreference) -> dict:
    return {
        "id": str(row.id),
        "event_type": row.event_type,
        "in_app": row.in_app,
        "email": row.email,
        "quiet_hours_start": row.quiet_hours_start.strftime("%H:%M:%S")
        if row.quiet_hours_start
        else None,
        "quiet_hours_end": row.quiet_hours_end.strftime("%H:%M:%S")
        if row.quiet_hours_end
        else None,
    }


def _parse_time(raw):
    if raw is None:
        return None
    from datetime import time as time_type

    if isinstance(raw, time_type):
        return raw
    try:
        return time_type.fromisoformat(str(raw))
    except ValueError as exc:
        raise ValidationError(
            "invalid quiet-hours time", details={"value": str(raw)[:16]}
        ) from exc


__all__ = ["INBOX_FILTERS", "InboxService"]
