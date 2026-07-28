"""Chat service (chat-session.md §3 — sessions, messages, candidates).

Stateless orchestrator over the session factory (house pattern). Ownership
is owner-only: every non-list lookup 404s for non-owners without leaking
existence (§5.3). Generation lifecycle (enqueue + engine scheduling) is
wired through ``send_message`` / ``regenerate``; the streaming wire itself
lives in ``engine.py`` / ``stream.py``.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError

from mesh.auth.rbac import assert_guest_project_visible, role_satisfies
from mesh.chat.engine import (
    chat_execution_idempotency_key,
    chat_list_channel,
    chat_session_channel,
)
from mesh.db.constraints import violates
from mesh.db.models.agent import Agent
from mesh.db.models.chat import ChatMessage, ChatSession, Favorite
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from mesh.outbox.service import emit_event, emit_realtime
from mesh.runtime.enqueue import CHAT_GENERATION_FINISHED_EVENT

_SESSION_NOT_FOUND = "chat session not found"
_MESSAGE_NOT_FOUND = "message not found"
_AGENT_NOT_FOUND = "agent not found"
_CONTEXT_NOT_ALLOWED = "context_not_allowed"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Deterministic ordering between a user message and its pre-created agent
# reply (same transaction timestamp; ids are random UUIDs).
_REPLY_ORDER_EPSILON = timedelta(milliseconds=1)
# M4: single-concurrency invariant enforced by a UNIQUE partial index; a
# concurrent second streaming insert raises IntegrityError on this constraint.
_STREAMING_UNIQUE_INDEX = "uq_chat_messages_one_streaming"


def _is_streaming_conflict(exc: IntegrityError) -> bool:
    # UNIQUE INDEX violations carry no constraint name (only the index name in
    # the error text), so match via ``violates`` (name OR text scan).
    return violates(exc, _STREAMING_UNIQUE_INDEX)

DEFAULT_SESSION_LIMIT = 20
MAX_SESSION_LIMIT = 100
DEFAULT_MESSAGE_LIMIT = 30
MAX_MESSAGE_LIMIT = 100


class _Unset:
    """PATCH sentinel: omitted vs explicitly-null must be distinguishable."""


UNSET = _Unset()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _session_sort_ts(row: ChatSession) -> datetime:
    """List sort timestamp: last activity, falling back to creation time."""
    return row.last_message_at or row.created_at


def _encode_session_cursor(*, pinned: bool, sort_ts: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps(
        {"p": 1 if pinned else 0, "t": _iso(sort_ts), "i": str(row_id)}
    ).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_session_cursor(raw: str) -> tuple[bool, datetime, uuid.UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return (
            bool(int(payload["p"])),
            datetime.fromisoformat(payload["t"]),
            uuid.UUID(payload["i"]),
        )
    except (ValueError, KeyError, binascii.Error, TypeError) as exc:
        raise ValidationError("invalid cursor", code="invalid_cursor") from exc


def _encode_message_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps({"t": _iso(created_at), "i": str(row_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_message_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return datetime.fromisoformat(payload["t"]), uuid.UUID(payload["i"])
    except (ValueError, KeyError, binascii.Error, TypeError) as exc:
        raise ValidationError("invalid cursor", code="invalid_cursor") from exc


def _stream_url(workspace_id: uuid.UUID, session_id: uuid.UUID, generation_id: uuid.UUID) -> str:
    return (
        f"/api/v1/workspaces/{workspace_id}/chat-sessions/{session_id}"
        f"/generations/{generation_id}/stream"
    )


class ChatService:
    """Session / message / candidate orchestration."""

    def __init__(
        self,
        session_factory,
        *,
        comment_service=None,
        attachment_service=None,
        favorites_service=None,
        streaming_stale_seconds: int = 600,
    ) -> None:
        self._session_factory = session_factory
        self.comment_service = comment_service
        self.attachment_service = attachment_service
        self.favorites_service = favorites_service
        self._streaming_stale_seconds = streaming_stale_seconds
        self.engine = None  # attached by create_app after the engine exists

    # -- rendering -----------------------------------------------------------

    async def _pinned_ids(self, session, *, workspace_id: uuid.UUID, member_id: uuid.UUID,
                          target_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not target_ids:
            return set()
        rows = (
            await session.execute(
                select(Favorite.target_id).where(
                    Favorite.workspace_id == workspace_id,
                    Favorite.member_id == member_id,
                    Favorite.target_type == "chat_session",
                    Favorite.target_id.in_(target_ids),
                )
            )
        ).scalars().all()
        return set(rows)

    async def _agent_snapshot(self, session, *, workspace_id: uuid.UUID, agent_id: uuid.UUID):
        agent = await session.scalar(
            select(Agent).where(Agent.workspace_id == workspace_id, Agent.id == agent_id)
        )
        if agent is None:
            return {"id": str(agent_id), "name": "agent"}
        return {"id": str(agent.id), "name": agent.name, "avatar_url": agent.avatar_url}

    async def _is_pinned(self, session, *, workspace_id: uuid.UUID,
                         member_id: uuid.UUID, target_id: uuid.UUID) -> bool:
        return (
            await session.scalar(
                select(Favorite.id).where(
                    Favorite.workspace_id == workspace_id,
                    Favorite.member_id == member_id,
                    Favorite.target_type == "chat_session",
                    Favorite.target_id == target_id,
                )
            )
        ) is not None

    async def render_session(
        self, session, row: ChatSession, *, member_id: uuid.UUID, pinned: bool | None = None
    ) -> dict:
        if pinned is None:
            pinned = await self._is_pinned(
                session, workspace_id=row.workspace_id, member_id=member_id, target_id=row.id
            )
        return {
            "id": str(row.id),
            "workspace_id": str(row.workspace_id),
            "owner_id": str(row.owner_id),
            "agent_id": str(row.agent_id),
            "agent": await self._agent_snapshot(
                session, workspace_id=row.workspace_id, agent_id=row.agent_id
            ),
            "title": row.title,
            "title_is_auto": row.title_is_auto,
            "context_issue_id": str(row.context_issue_id) if row.context_issue_id else None,
            "context_project_id": str(row.context_project_id) if row.context_project_id else None,
            "status": row.status,
            # Server-computed snapshot — truth source: favorites (README §6.19).
            "pinned": pinned,
            "last_message_at": _iso(row.last_message_at),
            "last_message_preview": row.last_message_preview,
            "message_count": row.message_count,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    async def _message_attachments(self, session, *, workspace_id: uuid.UUID,
                                   message_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
        """Inline attachment snapshots via the unified attachment module."""
        from mesh.db.models.attachment import Attachment, AttachmentLink

        if not message_ids:
            return {}
        rows = (
            await session.execute(
                select(AttachmentLink.linked_id, Attachment)
                .join(Attachment, Attachment.id == AttachmentLink.attachment_id)
                .where(
                    AttachmentLink.workspace_id == workspace_id,
                    AttachmentLink.linked_type == "chat_message",
                    AttachmentLink.linked_id.in_(message_ids),
                    Attachment.deleted_at.is_(None),
                )
                .order_by(AttachmentLink.position, AttachmentLink.id)
            )
        ).all()
        grouped: dict[uuid.UUID, list[dict]] = {}
        for linked_id, attachment in rows:
            grouped.setdefault(linked_id, []).append(
                {
                    "id": str(attachment.id),
                    "file_name": attachment.file_name,
                    "mime_type": attachment.mime_type,
                    "byte_size": attachment.byte_size,
                    "scan_status": attachment.scan_status,
                }
            )
        return grouped

    async def render_message(self, session, row: ChatMessage, *,
                             attachments: list[dict] | None = None,
                             candidate_count: int | None = None,
                             candidate_index: int | None = None) -> dict:
        return {
            "id": str(row.id),
            "session_id": str(row.session_id),
            "role": row.role,
            "content": row.content,
            "generation_id": str(row.generation_id) if row.generation_id else None,
            "generation_status": row.generation_status,
            "parent_id": str(row.parent_id) if row.parent_id else None,
            "selected_candidate": row.selected_candidate,
            "quote_message_id": str(row.quote_message_id) if row.quote_message_id else None,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "error_message": row.error_message,
            "started_at": _iso(row.started_at),
            "finished_at": _iso(row.finished_at),
            "created_at": _iso(row.created_at),
            "attachments": attachments or [],
            "candidate_count": candidate_count,
            "candidate_index": candidate_index,
        }

    # -- sessions --------------------------------------------------------------

    async def _load_owned(self, session, *, workspace_id: uuid.UUID, session_id: uuid.UUID,
                          actor: Member) -> ChatSession:
        row = await session.scalar(
            select(ChatSession).where(
                ChatSession.workspace_id == workspace_id,
                ChatSession.id == session_id,
                ChatSession.status != "deleted",
            )
        )
        # Same 404 for unknown and foreign sessions — existence must not leak.
        if row is None or row.owner_id != actor.id:
            raise NotFoundError(_SESSION_NOT_FOUND)
        return row

    async def _validate_context_issue(self, session, *, workspace_id: uuid.UUID,
                                      actor: Member, issue_id: uuid.UUID) -> None:
        issue = await session.scalar(
            select(Issue).where(
                Issue.workspace_id == workspace_id,
                Issue.id == issue_id,
                Issue.deleted_at.is_(None),
            )
        )
        if issue is None or not role_satisfies(actor.role, "issue:read"):
            raise ValidationError(
                "context issue is not accessible",
                code=_CONTEXT_NOT_ALLOWED,
                details={"field": "context_issue_id"},
            )
        # M3: a guest may only attach issues from projects explicitly shared
        # with them; otherwise the engine would inject private-project content
        # into the guest-readable session. Denial surfaces as a uniform 404
        # (assert_guest_project_visible) so project existence never leaks.
        if issue.project_id is not None:
            await assert_guest_project_visible(
                session, member=actor, project_id=issue.project_id
            )

    async def _validate_context_project(self, session, *, workspace_id: uuid.UUID,
                                        project_id: uuid.UUID) -> None:
        project = await session.scalar(
            select(Project.id).where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
                Project.deleted_at.is_(None),
            )
        )
        if project is None:
            raise ValidationError(
                "context project is not accessible",
                code="context_not_allowed",
                details={"field": "context_project_id"},
            )

    async def create_session(self, *, actor: Member, workspace_id: uuid.UUID,
                             agent_id: uuid.UUID, context_issue_id: uuid.UUID | None = None,
                             context_project_id: uuid.UUID | None = None,
                             title: str | None = None) -> dict:
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await session.scalar(
                select(Agent).where(
                    Agent.workspace_id == workspace_id,
                    Agent.id == agent_id,
                    Agent.deleted_at.is_(None),
                )
            )
            if agent is None:
                raise NotFoundError(_AGENT_NOT_FOUND)
            if agent.lifecycle_status != "active":
                raise ServiceUnavailableError(
                    "agent is not available for chat", code="agent_unavailable"
                )
            if context_issue_id is not None:
                await self._validate_context_issue(
                    session, workspace_id=workspace_id, actor=actor, issue_id=context_issue_id
                )
            if context_project_id is not None:
                await self._validate_context_project(
                    session, workspace_id=workspace_id, project_id=context_project_id
                )
            row = ChatSession(
                workspace_id=workspace_id,
                owner_id=actor.id,
                agent_id=agent_id,
                context_issue_id=context_issue_id,
                context_project_id=context_project_id,
            )
            if title is not None:
                row.title = title
                row.title_is_auto = False
            session.add(row)
            await session.flush()
            rendered = await self.render_session(session, row, member_id=actor.id)
        return rendered

    async def list_sessions(self, *, actor: Member, workspace_id: uuid.UUID,
                            agent_id: uuid.UUID | None = None, status: str = "active",
                            cursor: str | None = None,
                            limit: int = DEFAULT_SESSION_LIMIT) -> dict:
        # M7: ordering + keyset pagination pushed to the DB (no full load +
        # in-memory sort). Pinned is a correlated EXISTS over the requester's
        # favorites (§2.8: "服务层 LEFT JOIN/EXISTS 在 DB 排序"); the sort key
        # is (pinned DESC, COALESCE(last_message_at, created_at) DESC, id DESC).
        if status not in ("active", "archived"):
            raise ValidationError("invalid status", details={"status": status[:32]})
        limit = max(1, min(int(limit), MAX_SESSION_LIMIT))
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            fav_exists = select(Favorite.id).where(
                Favorite.workspace_id == ChatSession.workspace_id,
                Favorite.member_id == actor.id,
                Favorite.target_type == "chat_session",
                Favorite.target_id == ChatSession.id,
            ).exists()
            pinned_int = case((fav_exists, 1), else_=0)
            sort_ts = func.coalesce(ChatSession.last_message_at, ChatSession.created_at)
            stmt = select(ChatSession, pinned_int.label("pinned_int")).where(
                ChatSession.workspace_id == workspace_id,
                ChatSession.owner_id == actor.id,
                ChatSession.status == status,
            )
            if agent_id is not None:
                stmt = stmt.where(ChatSession.agent_id == agent_id)
            if cursor is not None:
                cursor_pinned, cursor_ts, cursor_id = _decode_session_cursor(cursor)
                cpi = 1 if cursor_pinned else 0
                stmt = stmt.where(
                    or_(
                        pinned_int < cpi,
                        and_(pinned_int == cpi, sort_ts < cursor_ts),
                        and_(pinned_int == cpi, sort_ts == cursor_ts, ChatSession.id < cursor_id),
                    )
                )
            stmt = stmt.order_by(
                pinned_int.desc(), sort_ts.desc(), ChatSession.id.desc()
            ).limit(limit + 1)
            rows = (await session.execute(stmt)).all()
            has_more = len(rows) > limit
            page = rows[:limit]
            items = [
                await self.render_session(
                    session, row, member_id=actor.id, pinned=bool(pinned)
                )
                for row, pinned in page
            ]
            next_cursor = None
            if has_more and page:
                last_row, last_pinned = page[-1]
                next_cursor = _encode_session_cursor(
                    pinned=bool(last_pinned),
                    sort_ts=_session_sort_ts(last_row),
                    row_id=last_row.id,
                )
        return {"items": items, "next_cursor": next_cursor}

    async def get_session(self, *, actor: Member, workspace_id: uuid.UUID,
                          session_id: uuid.UUID) -> dict:
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            row = await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            return await self.render_session(session, row, member_id=actor.id)

    async def patch_session(self, *, actor: Member, workspace_id: uuid.UUID,
                            session_id: uuid.UUID, title: str | object = UNSET,
                            status: str | None | object = UNSET,
                            context_issue_id: uuid.UUID | None | object = UNSET,
                            context_project_id: uuid.UUID | None | object = UNSET) -> dict:
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            row = await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            if not isinstance(title, _Unset) and title is not None:
                row.title = title
                row.title_is_auto = False  # manual rename wins over auto title
            if not isinstance(status, _Unset) and status is not None:
                row.status = status
            if not isinstance(context_issue_id, _Unset):
                if context_issue_id is not None:
                    await self._validate_context_issue(
                        session, workspace_id=workspace_id, actor=actor,
                        issue_id=context_issue_id,
                    )
                row.context_issue_id = context_issue_id
            if not isinstance(context_project_id, _Unset):
                if context_project_id is not None:
                    await self._validate_context_project(
                        session, workspace_id=workspace_id, project_id=context_project_id
                    )
                row.context_project_id = context_project_id
            row.updated_at = _utcnow()
            await session.flush()
            rendered = await self.render_session(session, row, member_id=actor.id)
        return rendered

    async def delete_session(self, *, actor: Member, workspace_id: uuid.UUID,
                             session_id: uuid.UUID) -> None:
        """Soft delete (§3.1) + favorites cleanup (§6.19 target deletion)."""
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            row = await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            now = _utcnow()
            row.status = "deleted"
            row.deleted_at = now
            row.updated_at = now
            # Reuse the favorites cleanup helper (no dead inline DELETE).
            if self.favorites_service is not None:
                await self.favorites_service.cleanup_for_target(
                    session, workspace_id=workspace_id,
                    target_type="chat_session", target_id=session_id,
                )
            else:  # pragma: no cover — favorites_service always wired in app.py
                await session.execute(
                    sa_delete(Favorite).where(
                        Favorite.workspace_id == workspace_id,
                        Favorite.target_type == "chat_session",
                        Favorite.target_id == session_id,
                    )
                )

    # -- messages ----------------------------------------------------------------

    async def list_messages(self, *, actor: Member, workspace_id: uuid.UUID,
                            session_id: uuid.UUID, cursor: str | None = None,
                            limit: int = DEFAULT_MESSAGE_LIMIT,
                            parent_id: uuid.UUID | None = None) -> dict:
        limit = max(1, min(int(limit), MAX_MESSAGE_LIMIT))
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            base = select(ChatMessage).where(
                ChatMessage.workspace_id == workspace_id,
                ChatMessage.session_id == session_id,
            )
            if parent_id is not None:
                # Candidate mode: every reply to the given parent, oldest first.
                stmt = (
                    base.where(ChatMessage.parent_id == parent_id)
                    .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                    .limit(limit + 1)
                )
                rows = list((await session.execute(stmt)).scalars().all())
                attachments = await self._message_attachments(
                    session, workspace_id=workspace_id,
                    message_ids=[row.id for row in rows[:limit]],
                )
                items = [
                    await self.render_message(
                        session, row, attachments=attachments.get(row.id),
                        candidate_count=len(rows[:limit]),
                        candidate_index=index + 1,
                    )
                    for index, row in enumerate(rows[:limit])
                ]
                next_cursor = None
                if len(rows) > limit and rows[:limit]:
                    last = rows[limit - 1]
                    next_cursor = _encode_message_cursor(last.created_at, last.id)
                return {"items": items, "next_cursor": next_cursor}
            # Timeline mode: user/system turns + the selected candidate only.
            stmt = base.where(
                or_(
                    ChatMessage.parent_id.is_(None),
                    ChatMessage.selected_candidate.is_(True),
                )
            ).order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            if cursor is not None:
                cursor_ts, cursor_id = _decode_message_cursor(cursor)
                # asyncpg rejects anonymous composite parameters — expand the
                # keyset comparison into its OR form.
                stmt = stmt.where(
                    or_(
                        ChatMessage.created_at < cursor_ts,
                        (ChatMessage.created_at == cursor_ts) & (ChatMessage.id < cursor_id),
                    )
                )
            rows = list((await session.execute(stmt.limit(limit + 1))).scalars().all())
            page = rows[:limit]
            attachments = await self._message_attachments(
                session, workspace_id=workspace_id, message_ids=[row.id for row in page]
            )
            # Candidate metadata for selected agent replies (‹ 1/3 › paging).
            # M7: a single window-function query yields both the sibling count
            # and the position (no per-row N+1).
            candidate_meta: dict[uuid.UUID, tuple[int, int]] = {}
            parent_ids = [row.parent_id for row in page if row.parent_id is not None]
            if parent_ids:
                meta_rows = (
                    await session.execute(
                        select(
                            ChatMessage.id,
                            func.count(ChatMessage.id)
                            .over(partition_by=ChatMessage.parent_id)
                            .label("cnt"),
                            func.row_number()
                            .over(
                                partition_by=ChatMessage.parent_id,
                                order_by=[ChatMessage.created_at, ChatMessage.id],
                            )
                            .label("idx"),
                        ).where(
                            ChatMessage.workspace_id == workspace_id,
                            ChatMessage.session_id == session_id,
                            ChatMessage.parent_id.in_(parent_ids),
                        )
                    )
                ).all()
                candidate_meta = {mid: (int(cnt), int(idx)) for mid, cnt, idx in meta_rows}
            items = [
                await self.render_message(
                    session, row,
                    attachments=attachments.get(row.id),
                    candidate_count=candidate_meta.get(row.id, (None, None))[0],
                    candidate_index=candidate_meta.get(row.id, (None, None))[1],
                )
                for row in page
            ]
            next_cursor = None
            if len(rows) > limit and page:
                last = page[-1]
                next_cursor = _encode_message_cursor(last.created_at, last.id)
        return {"items": items, "next_cursor": next_cursor}

    # -- generation lifecycle ------------------------------------------------------

    async def _enqueue_execution(self, session, *, workspace_id: uuid.UUID,
                                 chat_session: ChatSession, agent_message: ChatMessage,
                                 trigger_event_id: uuid.UUID) -> str:
        """Register the agent reply execution (README §6.9 chat trigger, §6.5 key)."""
        agent_member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.agent_id == chat_session.agent_id,
            )
        )
        idem_key = chat_execution_idempotency_key(
            agent_id=chat_session.agent_id,
            issue_id=chat_session.context_issue_id,
            trigger_event_id=trigger_event_id,
        )
        await emit_event(
            session,
            workspace_id=workspace_id,
            event_type="execution.enqueue",
            payload={
                "intent": "enqueue",
                "agent_id": str(chat_session.agent_id),
                "agent_member_id": str(agent_member.id) if agent_member is not None else None,
                "issue_id": str(chat_session.context_issue_id)
                if chat_session.context_issue_id
                else None,
                "trigger": "chat",
                "trigger_event_id": str(trigger_event_id),
                "idempotency_key": idem_key,
                "task_spec": {
                    "kind": "chat_generation",
                    "session_id": str(chat_session.id),
                    "message_id": str(agent_message.id),
                    "generation_id": str(agent_message.generation_id),
                },
                "config_snapshot": {},
                "required_capabilities": [],
                "label_requirements": {},
            },
            idempotency_key=idem_key,
        )
        return idem_key

    async def _assert_generation_slot(
        self, session, *, workspace_id: uuid.UUID, session_id: uuid.UUID,
        chat_session: ChatSession,
    ) -> None:
        """Single-concurrency guard (§3.5 / §5.3).

        The UNIQUE partial streaming index is the authoritative guard; this
        probe gives a clean 409 on the happy path. A streaming row whose
        ``started_at`` exceeds the stale threshold is a stuck generation
        (engine crash / lost task) and is reclaimed here — flipped to
        ``failed`` with its execution finalized via the outbox — so the slot
        frees instead of returning 409 forever.
        """
        busy = await session.execute(
            select(ChatMessage.id, ChatMessage.generation_id, ChatMessage.started_at).where(
                ChatMessage.workspace_id == workspace_id,
                ChatMessage.session_id == session_id,
                ChatMessage.generation_status == "streaming",
            )
        )
        row = busy.first()
        if row is None:
            return
        busy_id, busy_generation_id, started_at = row
        stale = started_at is not None and (
            _utcnow() - started_at
        ).total_seconds() > self._streaming_stale_seconds
        if not stale:
            raise ConflictError(
                "a generation is already in progress for this session",
                code="generation_in_progress",
            )
        # Reclaim the stuck generation so the new one may proceed.
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.id == busy_id, ChatMessage.generation_status == "streaming")
            .values(
                generation_status="failed",
                error_message="generation timed out",
                finished_at=_utcnow(),
                updated_at=_utcnow(),
            )
        )
        await emit_event(
            session,
            workspace_id=workspace_id,
            event_type=CHAT_GENERATION_FINISHED_EVENT,
            payload={
                "idempotency_key": chat_execution_idempotency_key(
                    agent_id=chat_session.agent_id,
                    issue_id=chat_session.context_issue_id,
                    trigger_event_id=busy_id,
                ),
                "status": "failed",
                "message_id": str(busy_id),
                "generation_id": str(busy_generation_id) if busy_generation_id else None,
            },
            idempotency_key=f"chat-finish:{busy_generation_id}:reclaim",
        )

    async def send_message(self, *, actor: Member, workspace_id: uuid.UUID,
                           session_id: uuid.UUID, content: str,
                           attachment_ids: list[uuid.UUID] | None = None,
                           quote_message_id: uuid.UUID | None = None,
                           idempotency_key: str | None = None) -> dict:
        attachment_ids = attachment_ids or []
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            # Idempotent write (§3.5): a duplicate key returns the first result.
            # M1: scope the lookup to THIS session so another user reusing the
            # same Idempotency-Key on their own session cannot read/overwrite
            # this session's resources (the unique index is session-scoped too).
            if idempotency_key is not None:
                existing = await session.scalar(
                    select(ChatMessage).where(
                        ChatMessage.workspace_id == workspace_id,
                        ChatMessage.session_id == session_id,
                        ChatMessage.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    chat_session = await self._load_owned(
                        session, workspace_id=workspace_id, session_id=session_id, actor=actor
                    )
                    return {
                        "message_id": str(existing.id),
                        "generation_id": str(existing.generation_id),
                        "stream_url": _stream_url(workspace_id, chat_session.id,
                                                  existing.generation_id),
                    }
            chat_session = await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            if chat_session.status != "active":
                raise BusinessRuleError(
                    "session is not active", code="session_not_active"
                )
            await self._assert_generation_slot(
                session, workspace_id=workspace_id, session_id=session_id,
                chat_session=chat_session,
            )
            if quote_message_id is not None:
                quoted = await session.scalar(
                    select(ChatMessage.id).where(
                        ChatMessage.workspace_id == workspace_id,
                        ChatMessage.session_id == session_id,
                        ChatMessage.id == quote_message_id,
                    )
                )
                if quoted is None:
                    raise NotFoundError("quoted message not found")
            now = _utcnow()
            user_message = ChatMessage(
                workspace_id=workspace_id,
                session_id=session_id,
                role="user",
                content=content,
                generation_status="done",
                quote_message_id=quote_message_id,
                created_at=now,
                updated_at=now,
            )
            session.add(user_message)
            await session.flush()
            generation_id = uuid.uuid4()
            # +1 ms so the timeline order (created_at DESC) is deterministic
            # even though both rows share the transaction timestamp.
            agent_ts = now + _REPLY_ORDER_EPSILON
            agent_message = ChatMessage(
                workspace_id=workspace_id,
                session_id=session_id,
                role="agent",
                content="",
                generation_id=generation_id,
                generation_status="streaming",
                parent_id=user_message.id,
                selected_candidate=True,
                started_at=now,
                idempotency_key=idempotency_key,
                created_at=agent_ts,
                updated_at=agent_ts,
            )
            session.add(agent_message)
            # M4: the UNIQUE partial streaming index is the authoritative
            # single-concurrency guard (the SELECT probe above only shortens the
            # happy path). A losing concurrent insert maps to 409; a losing
            # idempotency-key race returns the winner (idempotent).
            try:
                await session.flush()
            except IntegrityError as exc:
                if _is_streaming_conflict(exc):
                    raise ConflictError(
                        "a generation is already in progress for this session",
                        code="generation_in_progress",
                    ) from exc
                if violates(exc, "uq_chat_messages_idempotency"):
                    winner = await session.scalar(
                        select(ChatMessage).where(
                            ChatMessage.workspace_id == workspace_id,
                            ChatMessage.session_id == session_id,
                            ChatMessage.idempotency_key == idempotency_key,
                        )
                    )
                    if winner is not None:
                        return {
                            "message_id": str(winner.id),
                            "generation_id": str(winner.generation_id),
                            "stream_url": _stream_url(
                                workspace_id, session_id, winner.generation_id
                            ),
                        }
                raise
            await self._link_attachments(
                session, actor=actor, workspace_id=workspace_id,
                attachment_ids=attachment_ids, linked_id=user_message.id,
            )
            chat_session.message_count = (chat_session.message_count or 0) + 2
            chat_session.last_message_at = now
            chat_session.last_message_preview = content[:120]
            chat_session.updated_at = now
            execution_key = await self._enqueue_execution(
                session, workspace_id=workspace_id, chat_session=chat_session,
                agent_message=agent_message, trigger_event_id=agent_message.id,
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=chat_session_channel(session_id),
                event="message.created",
                data={
                    "message_id": str(agent_message.id),
                    "role": "agent",
                    "generation_status": "streaming",
                },
                idempotency_key=f"chat:{generation_id}:created",
            )
            # H1: live list refresh on the OWNER's private channel (safe payload).
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=chat_list_channel(actor.id),
                event="message.created",
                data={
                    "session_id": str(session_id),
                    "generation_status": "streaming",
                    "last_message_preview": content[:120],
                    "last_message_at": now.isoformat(),
                    "message_count": chat_session.message_count,
                },
                idempotency_key=f"chat:{generation_id}:created:list",
            )
        if self.engine is not None:
            self.engine.schedule(
                workspace_id=workspace_id,
                session_id=session_id,
                message_id=agent_message.id,
                generation_id=generation_id,
                execution_idempotency_key=execution_key,
            )
        return {
            "message_id": str(agent_message.id),
            "generation_id": str(generation_id),
            "stream_url": _stream_url(workspace_id, session_id, generation_id),
        }

    async def _link_attachments(self, session, *, actor: Member, workspace_id: uuid.UUID,
                                attachment_ids: list[uuid.UUID], linked_id: uuid.UUID) -> None:
        if not attachment_ids:
            return
        if self.attachment_service is None:
            raise ServiceUnavailableError("attachment module unavailable")
        for attachment_id in attachment_ids:
            await self.attachment_service.link_attachment(
                actor=actor,
                workspace_id=workspace_id,
                attachment_id=attachment_id,
                linked_type="chat_message",
                linked_id=linked_id,
            )

    async def regenerate(self, *, actor: Member, workspace_id: uuid.UUID,
                         session_id: uuid.UUID, message_id: uuid.UUID,
                         idempotency_key: str | None = None) -> dict:
        """New agent candidate under the user message (§3.3 / A6)."""
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            chat_session = await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            if chat_session.status != "active":
                raise BusinessRuleError("session is not active", code="session_not_active")
            # §3.3 addresses the user message; an agent message id is accepted
            # as a convenience and resolved to the parent it answers (the UI
            # places the regenerate action on the agent bubble).
            target = await session.scalar(
                select(ChatMessage).where(
                    ChatMessage.workspace_id == workspace_id,
                    ChatMessage.session_id == session_id,
                    ChatMessage.id == message_id,
                )
            )
            if target is None:
                raise NotFoundError(_MESSAGE_NOT_FOUND)
            user_message = target
            if target.role == "agent":
                if target.parent_id is None:
                    raise NotFoundError(_MESSAGE_NOT_FOUND)
                user_message = await session.scalar(
                    select(ChatMessage).where(
                        ChatMessage.workspace_id == workspace_id,
                        ChatMessage.session_id == session_id,
                        ChatMessage.id == target.parent_id,
                    )
                )
                if user_message is None or user_message.role != "user":
                    raise NotFoundError(_MESSAGE_NOT_FOUND)
            elif target.role != "user":
                raise NotFoundError(_MESSAGE_NOT_FOUND)
            if idempotency_key is not None:
                existing = await session.scalar(
                    select(ChatMessage).where(
                        ChatMessage.workspace_id == workspace_id,
                        ChatMessage.session_id == session_id,
                        ChatMessage.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return {
                        "message_id": str(existing.id),
                        "generation_id": str(existing.generation_id),
                        "stream_url": _stream_url(workspace_id, session_id,
                                                  existing.generation_id),
                    }
            await self._assert_generation_slot(
                session, workspace_id=workspace_id, session_id=session_id,
                chat_session=chat_session,
            )
            # Old candidates are preserved and deselected — never rewritten (A6).
            await session.execute(
                update(ChatMessage)
                .where(
                    ChatMessage.workspace_id == workspace_id,
                    ChatMessage.session_id == session_id,
                    ChatMessage.parent_id == user_message.id,
                )
                .values(selected_candidate=False)
            )
            now = _utcnow()
            generation_id = uuid.uuid4()
            candidate = ChatMessage(
                workspace_id=workspace_id,
                session_id=session_id,
                role="agent",
                content="",
                generation_id=generation_id,
                generation_status="streaming",
                parent_id=user_message.id,
                selected_candidate=True,
                started_at=now,
                idempotency_key=idempotency_key,
            )
            session.add(candidate)
            try:
                await session.flush()
            except IntegrityError as exc:
                if _is_streaming_conflict(exc):
                    raise ConflictError(
                        "a generation is already in progress for this session",
                        code="generation_in_progress",
                    ) from exc
                if violates(exc, "uq_chat_messages_idempotency"):
                    winner = await session.scalar(
                        select(ChatMessage).where(
                            ChatMessage.workspace_id == workspace_id,
                            ChatMessage.session_id == session_id,
                            ChatMessage.idempotency_key == idempotency_key,
                        )
                    )
                    if winner is not None:
                        return {
                            "message_id": str(winner.id),
                            "generation_id": str(winner.generation_id),
                            "stream_url": _stream_url(
                                workspace_id, session_id, winner.generation_id
                            ),
                        }
                raise
            chat_session.message_count = (chat_session.message_count or 0) + 1
            chat_session.last_message_at = now
            chat_session.updated_at = now
            execution_key = await self._enqueue_execution(
                session, workspace_id=workspace_id, chat_session=chat_session,
                agent_message=candidate, trigger_event_id=candidate.id,
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=chat_session_channel(session_id),
                event="message.created",
                data={
                    "message_id": str(candidate.id),
                    "role": "agent",
                    "generation_status": "streaming",
                },
                idempotency_key=f"chat:{generation_id}:created",
            )
        if self.engine is not None:
            self.engine.schedule(
                workspace_id=workspace_id,
                session_id=session_id,
                message_id=candidate.id,
                generation_id=generation_id,
                execution_idempotency_key=execution_key,
            )
        return {
            "message_id": str(candidate.id),
            "generation_id": str(generation_id),
            "stream_url": _stream_url(workspace_id, session_id, generation_id),
        }

    async def stop_generation(self, *, actor: Member, workspace_id: uuid.UUID,
                              session_id: uuid.UUID, generation_id: uuid.UUID) -> dict:
        """Idempotent stop (§3.3): repeated stops are side-effect-free 202s."""
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            chat_session = await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            message = await session.scalar(
                select(ChatMessage).where(
                    ChatMessage.workspace_id == workspace_id,
                    ChatMessage.session_id == session_id,
                    ChatMessage.generation_id == generation_id,
                )
            )
            if message is None:
                raise NotFoundError("generation not found")
        # Signal the engine first so an in-flight stream terminates with its
        # partial content; the conditional DB flip below covers an engine that
        # never picks the flag up (crash / restart — "流断了也能停"). The flip
        # persists the content reconstructed from the delta buffer so the
        # partial reply survives whoever wins the race.
        if self.engine is not None:
            await self.engine.request_stop(generation_id)
            partial = await self.engine.buffered_content(generation_id)
        else:
            partial = message.content or ""
        # L4: never overwrite a longer already-persisted body with an empty or
        # truncated buffer (buffer eviction / MAXLEN truncation / engine crash).
        existing_content = message.content or ""
        final_content = partial if len(partial) >= len(existing_content) else existing_content
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = _utcnow()
            result = await session.execute(
                update(ChatMessage)
                .where(
                    ChatMessage.id == message.id,
                    ChatMessage.generation_status == "streaming",
                )
                .values(
                    content=final_content,
                    generation_status="interrupted",
                    finished_at=now,
                    updated_at=now,
                )
            )
            if result.rowcount > 0:
                await session.execute(
                    update(ChatSession)
                    .where(ChatSession.id == session_id)
                    .values(
                        last_message_at=now,
                        last_message_preview=final_content[:120],
                        updated_at=now,
                    )
                )
                # The engine skipped its finalization — emit the terminal
                # realtime event and the execution completion through the
                # outbox here instead (§4.4 衔接, relay finalizes the row).
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=chat_session_channel(session_id),
                    event="message.interrupted",
                    data={
                        "message_id": str(message.id),
                        "partial_content": final_content,
                        "generation_status": "interrupted",
                    },
                    idempotency_key=f"chat:{generation_id}:message.interrupted:session",
                )
                # H1: list-refresh goes to the OWNER's private channel with a
                # safe payload (no partial_content, never workspace-wide).
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=chat_list_channel(chat_session.owner_id),
                    event="message.interrupted",
                    data={
                        "session_id": str(session_id),
                        "generation_status": "interrupted",
                        "last_message_preview": final_content[:120],
                        "last_message_at": now.isoformat(),
                        "message_count": chat_session.message_count,
                    },
                    idempotency_key=f"chat:{generation_id}:message.interrupted:list",
                )
                await emit_event(
                    session,
                    workspace_id=workspace_id,
                    event_type=CHAT_GENERATION_FINISHED_EVENT,
                    payload={
                        "idempotency_key": chat_execution_idempotency_key(
                            agent_id=chat_session.agent_id,
                            issue_id=chat_session.context_issue_id,
                            trigger_event_id=message.id,
                        ),
                        "status": "interrupted",
                        "message_id": str(message.id),
                        "generation_id": str(generation_id),
                    },
                    idempotency_key=f"chat-finish:{generation_id}",
                )
            current = await session.scalar(
                select(ChatMessage.generation_status).where(ChatMessage.id == message.id)
            )
        return {
            "generation_id": str(generation_id),
            "message_id": str(message.id),
            "generation_status": current,
        }

    async def select_candidate(self, *, actor: Member, workspace_id: uuid.UUID,
                               session_id: uuid.UUID, message_id: uuid.UUID,
                               selected_message_id: uuid.UUID) -> dict:
        """Switch the selected candidate under a parent message (§3.3)."""
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            parent = await session.scalar(
                select(ChatMessage.id).where(
                    ChatMessage.workspace_id == workspace_id,
                    ChatMessage.session_id == session_id,
                    ChatMessage.id == message_id,
                )
            )
            if parent is None:
                raise NotFoundError(_MESSAGE_NOT_FOUND)
            target = await session.scalar(
                select(ChatMessage).where(
                    ChatMessage.workspace_id == workspace_id,
                    ChatMessage.session_id == session_id,
                    ChatMessage.id == selected_message_id,
                    ChatMessage.parent_id == message_id,
                )
            )
            if target is None:
                raise ValidationError(
                    "selected message is not a candidate of this parent",
                    code="validation_error",
                )
            await session.execute(
                update(ChatMessage)
                .where(
                    ChatMessage.workspace_id == workspace_id,
                    ChatMessage.session_id == session_id,
                    ChatMessage.parent_id == message_id,
                )
                .values(selected_candidate=False)
            )
            await session.execute(
                update(ChatMessage)
                .where(ChatMessage.id == selected_message_id)
                .values(selected_candidate=True)
            )
        return {"parent_id": str(message_id), "selected_message_id": str(selected_message_id)}

    # -- distillation (沉淀为评论, README §6.9) ------------------------------------

    async def distill_preview(self, *, actor: Member, workspace_id: uuid.UUID,
                              session_id: uuid.UUID, body_markdown: str,
                              target_issue_id: uuid.UUID | None = None,
                              attachment_ids: list[uuid.UUID] | None = None) -> dict:
        """Render the one-submit confirmation payload (no side effects)."""
        attachment_ids = attachment_ids or []
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            chat_session = await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            resolved = target_issue_id or chat_session.context_issue_id
            if resolved is None:
                raise ValidationError(
                    "no target issue for distillation",
                    code="context_not_allowed",
                    details={"field": "target_issue_id"},
                )
            issue = await session.scalar(
                select(Issue).where(
                    Issue.workspace_id == workspace_id,
                    Issue.id == resolved,
                    Issue.deleted_at.is_(None),
                )
            )
            if issue is None or not role_satisfies(actor.role, "issue:read"):
                raise ValidationError(
                    "target issue is not accessible",
                    code="context_not_allowed",
                    details={"field": "target_issue_id"},
                )
            attachments: list[dict] = []
            if attachment_ids and self.attachment_service is not None:
                rows = (
                    await session.execute(
                        text(
                            "SELECT id, file_name, mime_type, byte_size FROM attachments "
                            "WHERE workspace_id = :ws AND id = ANY(:ids) "
                            "AND deleted_at IS NULL"
                        ),
                        {"ws": workspace_id, "ids": attachment_ids},
                    )
                ).all()
                attachments = [
                    {
                        "id": str(att_id),
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "byte_size": byte_size,
                    }
                    for att_id, file_name, mime_type, byte_size in rows
                ]
        triggers = {"mentions": [], "agent_triggers": []}
        if self.comment_service is not None:
            triggers = await self.comment_service.preview_triggers(
                workspace_id=workspace_id, body_markdown=body_markdown
            )
        return {
            "target_issue": {
                "id": str(issue.id),
                "identifier": issue.identifier,
                "title": issue.title,
            },
            "body_markdown": body_markdown,
            "attachments": attachments,
            "triggered_agents": triggers["agent_triggers"],
            "mentions": triggers["mentions"],
            "can_trigger_agents": role_satisfies(actor.role, "agent:trigger"),
            "suppress_triggers_supported": True,
        }

    # -- stream helpers -------------------------------------------------------------

    async def authorize_stream(self, *, actor: Member, workspace_id: uuid.UUID,
                               session_id: uuid.UUID, generation_id: uuid.UUID) -> dict:
        """Owner + generation-existence gate for the SSE endpoint."""
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            await self._load_owned(
                session, workspace_id=workspace_id, session_id=session_id, actor=actor
            )
            message = await session.scalar(
                select(ChatMessage).where(
                    ChatMessage.workspace_id == workspace_id,
                    ChatMessage.session_id == session_id,
                    ChatMessage.generation_id == generation_id,
                )
            )
            if message is None:
                raise NotFoundError("generation not found")
            return {
                "message_id": message.id,
                "generation_status": message.generation_status,
                "content": message.content,
                "error_message": message.error_message,
            }

    async def load_message_state(self, *, workspace_id: uuid.UUID, session_id: uuid.UUID,
                                 generation_id: uuid.UUID) -> dict | None:
        """REST-degradation state snapshot for late stream subscribers."""
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            message = await session.scalar(
                select(ChatMessage).where(
                    ChatMessage.workspace_id == workspace_id,
                    ChatMessage.session_id == session_id,
                    ChatMessage.generation_id == generation_id,
                )
            )
            if message is None:
                return None
            return {
                "message_id": str(message.id),
                "generation_status": message.generation_status,
                "content": message.content,
                "error_message": message.error_message,
            }
