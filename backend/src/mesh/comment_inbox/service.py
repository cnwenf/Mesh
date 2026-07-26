"""Comment service (comment-inbox.md §3.1 / §3.5).

Owns the comment lifecycle: create / edit / soft-delete / resolve / react /
list, server-side mention parsing with the §6.9 trigger semantics, the
notification fan-out emission (§6.13 — routing happens in the relay
handler), and the realtime events (§3.6 — through the outbox, never
directly).

Threading model: single-level folded replies. ``parent_id`` must reference a
TOP-LEVEL comment of the SAME issue (the overlapping composite FK enforces
same-issue; depth=1 is enforced here). Replies carry ``thread_root_id`` for
recursion-free aggregation.

Authorization (service layer, per §3.4): workspace membership + issue
visibility is gated by the routes via ``resolve_workspace_context`` /
``require_workspace``; edit/delete require authorship or a manager role;
system-activity comments are read-only through the API.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.auth.rbac import assert_guest_project_visible
from mesh.comment_inbox import subscriptions
from mesh.comment_inbox.markdown import RenderedBody, preview_of, render_body
from mesh.comment_inbox.mentions import enqueue_agent_executions
from mesh.comment_inbox.notifications import emit_notification_fanout
from mesh.db.models.comment import Comment, CommentMention, CommentReaction
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    GoneError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
)
from mesh.member.display import resolve_display_name
from mesh.outbox.service import emit_realtime

_ISSUE_NOT_FOUND = "issue not found"
_COMMENT_NOT_FOUND = "comment not found"
_COMMENT_GONE = "comment has been deleted"
_REACTION_NOT_FOUND = "reaction not found"

LONG_TEXT_MAX_BYTES = 1024 * 1024  # 1 MiB body ceiling (storage-DoS guard)
PREVIEW_REPLY_COUNT = 3


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _issue_channel(issue_id: uuid.UUID) -> str:
    return f"issue:{issue_id}"


class CommentService:
    """Stateless orchestrator over a session factory (house pattern)."""

    def __init__(
        self,
        session_factory,
        *,
        clock: Callable[[], datetime] | None = None,
        max_agent_chain_depth: int = 5,
    ) -> None:
        self._factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_agent_chain_depth = max_agent_chain_depth

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def create_comment(
        self,
        *,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        author_member: Member,
        body_markdown: str,
        parent_id: uuid.UUID | None = None,
        suppress_triggers: bool = False,
        idempotency_key: str | None = None,
        can_trigger_agents: bool = True,
    ) -> dict:
        _check_body_bytes(body_markdown)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            issue = await self._load_issue(session, workspace_id, issue_id)
            await assert_guest_project_visible(
                session, member=author_member, project_id=issue.project_id
            ) if issue.project_id is not None else None

            if idempotency_key is not None:
                existing = await session.scalar(
                    select(Comment).where(
                        Comment.workspace_id == workspace_id,
                        Comment.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    # §6.14 幂等写: a duplicate key returns the first result.
                    return await self._render_comment(
                        session, existing, viewer_member_id=author_member.id
                    )

            rendered = render_body(body_markdown)
            mentioned = await self._resolve_mentions(
                session, workspace_id=workspace_id, rendered=rendered
            )
            agent_mentions = [m for m in mentioned if m.member_type == "agent"]
            if agent_mentions and not can_trigger_agents:
                raise BusinessRuleError(
                    "no permission to trigger agent executions",
                    code="mention_invalid",
                    details={"permission": "agent:trigger"},
                )

            thread_root_id: uuid.UUID | None = None
            if parent_id is not None:
                parent = await self._load_active_comment(
                    session, workspace_id, parent_id, issue_id=issue_id
                )
                if parent.parent_id is not None:
                    # Depth is exactly 1: replies attach to the thread root.
                    raise BusinessRuleError(
                        "cannot reply to a reply (thread depth is 1)",
                        code="reply_depth_exceeded",
                    )
                thread_root_id = parent.id

            comment = Comment(
                workspace_id=workspace_id,
                issue_id=issue_id,
                parent_id=parent_id,
                thread_root_id=thread_root_id,
                author_kind="member",
                author_id=author_member.id,
                body_markdown=body_markdown,
                body_html=rendered.html,
                body_text=rendered.text,
                idempotency_key=idempotency_key,
            )
            session.add(comment)
            # L3 / §6.14: the SELECT-then-INSERT above is not race-free; a
            # concurrent create with the same Idempotency-Key can lose the
            # race on uq_comments_idempotency. Catch it in a savepoint and
            # return the winner's row instead of surfacing a 500.
            try:
                async with session.begin_nested():
                    await session.flush()
            except IntegrityError:
                if idempotency_key is None:
                    raise
                winner = await session.scalar(
                    select(Comment).where(
                        Comment.workspace_id == workspace_id,
                        Comment.idempotency_key == idempotency_key,
                    )
                )
                if winner is None:
                    raise  # some other integrity violation
                return await self._render_comment(
                    session, winner, viewer_member_id=author_member.id
                )

            await self._insert_mentions(
                session, workspace_id=workspace_id, comment_id=comment.id, members=mentioned
            )
            # Default subscriptions (§6.13): author participates, mentioned
            # humans subscribe (agents have no inbox).
            await subscriptions.ensure_subscription(
                session,
                workspace_id=workspace_id,
                issue_id=issue_id,
                subscriber_id=author_member.id,
                reason="participated",
            )
            for member in mentioned:
                if member.member_type == "human":
                    await subscriptions.ensure_subscription(
                        session,
                        workspace_id=workspace_id,
                        issue_id=issue_id,
                        subscriber_id=member.id,
                        reason="mentioned",
                    )

            if agent_mentions and not suppress_triggers:
                result = await enqueue_agent_executions(
                    session,
                    workspace_id=workspace_id,
                    issue_id=issue_id,
                    comment=comment,
                    author_member=author_member,
                    agent_mentions=agent_mentions,
                    trigger_event_id=comment.id,
                    max_chain_depth=self._max_agent_chain_depth,
                )
                await self._record_triggered_executions(
                    session, comment_id=comment.id, triggered=result.triggered_by_member
                )

            rendered_dict = await self._render_comment(
                session,
                comment,
                viewer_member_id=author_member.id,
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(issue_id),
                event="comment.created",
                data=rendered_dict,
            )
            await self._emit_comment_fanouts(
                session,
                workspace_id=workspace_id,
                issue=issue,
                comment=comment,
                author_member=author_member,
                mentioned=mentioned,
                newly_mentioned=mentioned,
            )
            return rendered_dict

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------

    async def list_comments(
        self,
        *,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        viewer_member_id: uuid.UUID,
        member: Member,
        limit: int = 50,
        cursor: str | None = None,
        include: str = "replies",
        descending: bool = False,
    ) -> tuple[list[dict], str | None]:
        from mesh.api.pagination import Page, paginate  # local import: route helper

        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            issue = await self._load_issue(session, workspace_id, issue_id)
            if issue.project_id is not None:
                await assert_guest_project_visible(
                    session, member=member, project_id=issue.project_id
                )
            stmt = select(Comment).where(
                Comment.workspace_id == workspace_id,
                Comment.issue_id == issue_id,
                Comment.parent_id.is_(None),
            )
            page: Page = await paginate(
                session,
                stmt,
                sort_column=Comment.created_at,
                id_column=Comment.id,
                sort_value_of=lambda row: row.created_at,
                id_of=lambda row: row.id,
                cursor=cursor,
                limit=limit,
                descending=descending,
            )
            items = [
                await self._render_comment(
                    session, comment, viewer_member_id=viewer_member_id, with_counts=True
                )
                for comment in page.items
            ]
            if include == "replies" and items:
                await self._attach_preview_replies(
                    session,
                    workspace_id=workspace_id,
                    items=items,
                    viewer_member_id=viewer_member_id,
                )
            return items, page.next_cursor

    async def get_comment(
        self,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        viewer_member_id: uuid.UUID,
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            comment = await self._find_comment(session, workspace_id, comment_id)
            return await self._render_comment(
                session, comment, viewer_member_id=viewer_member_id, with_counts=True
            )

    async def list_replies(
        self,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        viewer_member_id: uuid.UUID,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        from mesh.api.pagination import paginate

        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            root = await self._find_comment(session, workspace_id, comment_id)
            if root.parent_id is not None:
                raise BusinessRuleError(
                    "replies can only be listed for a thread root", code="not_thread_root"
                )
            stmt = select(Comment).where(
                Comment.workspace_id == workspace_id,
                Comment.thread_root_id == root.id,
            )
            page = await paginate(
                session,
                stmt,
                sort_column=Comment.created_at,
                id_column=Comment.id,
                sort_value_of=lambda row: row.created_at,
                id_of=lambda row: row.id,
                cursor=cursor,
                limit=limit,
            )
            items = [
                await self._render_comment(
                    session, reply, viewer_member_id=viewer_member_id
                )
                for reply in page.items
            ]
            return items, page.next_cursor

    # ------------------------------------------------------------------
    # update / delete / resolve
    # ------------------------------------------------------------------

    async def update_comment(
        self,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        editor_member: Member,
        is_manager: bool,
        body_markdown: str,
        expected_updated_at: str | None = None,
        suppress_triggers: bool = False,
        can_trigger_agents: bool = True,
    ) -> dict:
        _check_body_bytes(body_markdown)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            comment = await self._load_active_comment(session, workspace_id, comment_id)
            if comment.author_kind == "system":
                raise ForbiddenError("system comments are read-only")
            if comment.author_id != editor_member.id and not is_manager:
                raise ForbiddenError("only the author or a manager can edit this comment")
            if expected_updated_at is not None:
                _assert_version_match(comment.updated_at, expected_updated_at)

            rendered = render_body(body_markdown)
            mentioned = await self._resolve_mentions(
                session, workspace_id=workspace_id, rendered=rendered
            )

            # §6.9 edit semantics: diff the mention sets.
            previous = (
                await session.execute(
                    select(CommentMention).where(
                        CommentMention.workspace_id == workspace_id,
                        CommentMention.comment_id == comment.id,
                    )
                )
            ).scalars().all()
            previous_by_member = {row.mentioned_id: row for row in previous}
            previous_active = {
                member_id for member_id, row in previous_by_member.items()
                if row.deleted_at is None
            }
            new_ids = {member.id for member in mentioned}
            added = [member for member in mentioned if member.id not in previous_active]
            removed_ids = previous_active - new_ids

            added_agents = [m for m in added if m.member_type == "agent"]
            if added_agents and not can_trigger_agents:
                raise BusinessRuleError(
                    "no permission to trigger agent executions",
                    code="mention_invalid",
                    details={"permission": "agent:trigger"},
                )

            now = self._clock()
            comment.body_markdown = body_markdown
            comment.body_html = rendered.html
            comment.body_text = rendered.text
            comment.edited_at = now
            comment.updated_at = now
            await session.flush()

            # Removed mentions soft-delete; in-flight executions are NOT
            # cancelled (future-only effect, §6.9).
            for member_id in removed_ids:
                row = previous_by_member[member_id]
                row.deleted_at = now
            # Added mentions: resurrect a soft-deleted row or insert fresh.
            for member in added:
                existing_row = previous_by_member.get(member.id)
                if existing_row is not None:
                    existing_row.deleted_at = None
                else:
                    await self._insert_mentions(
                        session,
                        workspace_id=workspace_id,
                        comment_id=comment.id,
                        members=[member],
                    )
            await session.flush()

            triggered_ids: tuple[uuid.UUID, ...] = ()
            if added_agents and not suppress_triggers:
                result = await enqueue_agent_executions(
                    session,
                    workspace_id=workspace_id,
                    issue_id=comment.issue_id,
                    comment=comment,
                    author_member=editor_member,
                    agent_mentions=added_agents,
                    trigger_event_id=comment.id,
                    max_chain_depth=self._max_agent_chain_depth,
                )
                triggered_ids = result.triggered_execution_ids
                await self._record_triggered_executions(
                    session, comment_id=comment.id, triggered=result.triggered_by_member
                )

            for member in added:
                if member.member_type == "human":
                    await subscriptions.ensure_subscription(
                        session,
                        workspace_id=workspace_id,
                        issue_id=comment.issue_id,
                        subscriber_id=member.id,
                        reason="mentioned",
                    )

            rendered_dict = await self._render_comment(
                session, comment, viewer_member_id=editor_member.id
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(comment.issue_id),
                event="comment.updated",
                data={
                    "id": str(comment.id),
                    "issue_id": str(comment.issue_id),
                    "body_markdown": comment.body_markdown,
                    "body_html": comment.body_html,
                    "body_text": comment.body_text,
                    "edited_at": rendered_dict["edited_at"],
                    "updated_at": rendered_dict["updated_at"],
                    "mentions": rendered_dict["mentions"],
                    "triggered_execution_ids": [str(x) for x in triggered_ids],
                },
            )
            # Only NEWLY mentioned humans get the critical mention
            # notification — unrelated text edits never re-notify (§6.9).
            if added:
                issue = await self._load_issue(session, workspace_id, comment.issue_id)
                await self._emit_comment_fanouts(
                    session,
                    workspace_id=workspace_id,
                    issue=issue,
                    comment=comment,
                    author_member=editor_member,
                    mentioned=added,
                    newly_mentioned=added,
                    skip_comment_created=True,
                )
            return rendered_dict

    async def delete_comment(
        self,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        actor_member: Member,
        is_manager: bool,
    ) -> None:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            comment = await self._load_active_comment(session, workspace_id, comment_id)
            if comment.author_kind == "system":
                raise ForbiddenError("system comments are read-only")
            if comment.author_id != actor_member.id and not is_manager:
                raise ForbiddenError("only the author or a manager can delete this comment")
            comment.deleted_at = self._clock()
            comment.updated_at = comment.deleted_at
            await session.flush()
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(comment.issue_id),
                event="comment.deleted",
                data={"id": str(comment.id), "issue_id": str(comment.issue_id)},
            )

    async def set_thread_resolved(
        self,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        actor_member: Member,
        resolved: bool,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            comment = await self._load_active_comment(session, workspace_id, comment_id)
            if comment.parent_id is not None or comment.thread_root_id is not None:
                raise BusinessRuleError(
                    "only a thread root can be resolved", code="not_thread_root"
                )
            now = self._clock()
            if resolved:
                comment.resolved_at = now
                comment.resolved_by_id = actor_member.id
            else:
                comment.resolved_at = None
                comment.resolved_by_id = None
            comment.updated_at = now
            await session.flush()
            rendered_dict = await self._render_comment(
                session, comment, viewer_member_id=actor_member.id
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(comment.issue_id),
                event="comment.resolved",
                data={
                    "id": str(comment.id),
                    "issue_id": str(comment.issue_id),
                    "resolved": resolved,
                    "resolved_at": rendered_dict["resolved_at"],
                    "resolved_by": rendered_dict["resolved_by"],
                },
            )
            return rendered_dict

    # ------------------------------------------------------------------
    # reactions
    # ------------------------------------------------------------------

    async def add_reaction(
        self,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        actor_member: Member,
        emoji: str,
    ) -> list[dict]:
        emoji = (emoji or "").strip()
        if not emoji or len(emoji) > 32:
            raise ValidationError("invalid emoji", code="validation_error")
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            comment = await self._load_active_comment(session, workspace_id, comment_id)
            insert_stmt = (
                pg_insert(CommentReaction)
                .values(
                    workspace_id=workspace_id,
                    comment_id=comment.id,
                    actor_id=actor_member.id,
                    emoji=emoji,
                )
                .on_conflict_do_nothing(
                    index_elements=["comment_id", "actor_id", "emoji"]
                )
            )
            result = await session.execute(insert_stmt)
            if result.rowcount == 0:
                raise ConflictError("reaction already exists")
            aggregation = await self._reaction_aggregation(
                session, workspace_id=workspace_id, comment_id=comment.id,
                viewer_member_id=actor_member.id,
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(comment.issue_id),
                event="reaction.changed",
                data={
                    "comment_id": str(comment.id),
                    "issue_id": str(comment.issue_id),
                    "reactions": aggregation,
                },
            )
            return aggregation

    async def remove_reaction(
        self,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        actor_member: Member,
        emoji: str,
    ) -> list[dict]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            comment = await self._load_active_comment(session, workspace_id, comment_id)
            result = await session.execute(
                sa_delete(CommentReaction).where(
                    CommentReaction.workspace_id == workspace_id,
                    CommentReaction.comment_id == comment.id,
                    CommentReaction.actor_id == actor_member.id,
                    CommentReaction.emoji == emoji,
                )
            )
            if result.rowcount == 0:
                raise NotFoundError(_REACTION_NOT_FOUND)
            aggregation = await self._reaction_aggregation(
                session, workspace_id=workspace_id, comment_id=comment.id,
                viewer_member_id=actor_member.id,
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(comment.issue_id),
                event="reaction.changed",
                data={
                    "comment_id": str(comment.id),
                    "issue_id": str(comment.issue_id),
                    "reactions": aggregation,
                },
            )
            return aggregation

    async def list_reactions(
        self,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        viewer_member_id: uuid.UUID,
    ) -> list[dict]:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            await self._find_comment(session, workspace_id, comment_id)
            return await self._reaction_aggregation(
                session, workspace_id=workspace_id, comment_id=comment_id,
                viewer_member_id=viewer_member_id,
            )

    # ------------------------------------------------------------------
    # system activity (internal — never exposed to the API, §C13)
    # ------------------------------------------------------------------

    async def create_system_comment(
        self,
        *,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        body_markdown: str,
    ) -> dict:
        """Write a read-only system-activity comment (author_kind='system',
        NULL author). Called by module internals on field/status changes."""
        _check_body_bytes(body_markdown)
        rendered = render_body(body_markdown)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await self._load_issue(session, workspace_id, issue_id)
            comment = Comment(
                workspace_id=workspace_id,
                issue_id=issue_id,
                author_kind="system",
                author_id=None,
                body_markdown=body_markdown,
                body_html=rendered.html,
                body_text=rendered.text,
            )
            session.add(comment)
            await session.flush()
            rendered_dict = await self._render_comment(
                session, comment, viewer_member_id=None
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(issue_id),
                event="comment.created",
                data=rendered_dict,
            )
            return rendered_dict

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _load_issue(
        self, session: AsyncSession, workspace_id: uuid.UUID, issue_id: uuid.UUID
    ) -> Issue:
        issue = await session.scalar(
            select(Issue).where(
                Issue.workspace_id == workspace_id,
                Issue.id == issue_id,
                Issue.deleted_at.is_(None),
            )
        )
        if issue is None:
            raise NotFoundError(_ISSUE_NOT_FOUND)
        return issue

    async def _find_comment(
        self, session: AsyncSession, workspace_id: uuid.UUID, comment_id: uuid.UUID
    ) -> Comment:
        comment = await session.scalar(
            select(Comment).where(
                Comment.workspace_id == workspace_id, Comment.id == comment_id
            )
        )
        if comment is None:
            raise NotFoundError(_COMMENT_NOT_FOUND)
        return comment

    async def _load_active_comment(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        issue_id: uuid.UUID | None = None,
    ) -> Comment:
        comment = await self._find_comment(session, workspace_id, comment_id)
        if issue_id is not None and comment.issue_id != issue_id:
            raise NotFoundError(_COMMENT_NOT_FOUND)
        if comment.deleted_at is not None:
            raise GoneError(_COMMENT_GONE)
        return comment

    async def _resolve_mentions(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        rendered: RenderedBody,
    ) -> list[Member]:
        """Resolve mention links (@uuid) + @Name tokens to active members.

        Unknown structural ids → 422 ``mention_invalid`` (forgery guard);
        ambiguous plain names resolve to nothing (deterministic).
        """
        resolved: dict[uuid.UUID, Member] = {}
        if rendered.mention_ids:
            rows = (
                await session.execute(
                    select(Member).where(
                        Member.workspace_id == workspace_id,
                        Member.id.in_(rendered.mention_ids),
                        Member.status == "active",
                    )
                )
            ).scalars().all()
            found = {row.id: row for row in rows}
            missing = [mid for mid in rendered.mention_ids if mid not in found]
            if missing:
                raise BusinessRuleError(
                    "mention references an unknown member",
                    code="mention_invalid",
                    details={"mention_ids": [str(mid) for mid in missing]},
                )
            resolved.update(found)
        for name in rendered.mention_names:
            matches = await self._members_by_display_name(
                session, workspace_id=workspace_id, name=name
            )
            if len(matches) == 1:
                resolved.setdefault(matches[0].id, matches[0])
        return list(resolved.values())

    async def _members_by_display_name(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, name: str
    ) -> list[Member]:
        """Exact display-name match (member.md §2.4 order: override → user name)."""
        members = (
            await session.execute(
                select(Member, User)
                .outerjoin(User, Member.user_id == User.id)
                .where(Member.workspace_id == workspace_id, Member.status == "active")
            )
        ).all()
        matched: list[Member] = []
        for member, user in members:
            if resolve_display_name(member=member, user=user) == name:
                matched.append(member)
        return matched

    async def _insert_mentions(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        members: Sequence[Member],
    ) -> None:
        for member in members:
            insert_stmt = (
                pg_insert(CommentMention)
                .values(
                    workspace_id=workspace_id,
                    comment_id=comment_id,
                    mentioned_id=member.id,
                )
                .on_conflict_do_nothing(index_elements=["comment_id", "mentioned_id"])
            )
            await session.execute(insert_stmt)
        await session.flush()

    async def _emit_comment_fanouts(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        issue: Issue,
        comment: Comment,
        author_member: Member,
        mentioned: Sequence[Member],
        newly_mentioned: Sequence[Member],
        skip_comment_created: bool = False,
    ) -> None:
        author_user = None
        if author_member.user_id is not None:
            author_user = await session.scalar(
                select(User).where(User.id == author_member.user_id)
            )
        actor_name = resolve_display_name(member=author_member, user=author_user)
        preview = preview_of(comment.body_text or "")
        if not skip_comment_created:
            await emit_notification_fanout(
                session,
                workspace_id=workspace_id,
                notification_type="comment_created",
                actor_member_id=author_member.id,
                actor_name=actor_name,
                actor_member_type=author_member.member_type,
                issue_id=issue.id,
                comment_id=comment.id,
                group_key=f"issue:{issue.id}:comment_created",
                title=issue.title,
                preview=preview,
                extra={"issue_identifier": issue.identifier},
            )
        human_mentioned = [m for m in newly_mentioned if m.member_type == "human"]
        if human_mentioned:
            await emit_notification_fanout(
                session,
                workspace_id=workspace_id,
                notification_type="mentioned",
                actor_member_id=author_member.id,
                actor_name=actor_name,
                actor_member_type=author_member.member_type,
                issue_id=issue.id,
                comment_id=comment.id,
                recipient_ids=[m.id for m in human_mentioned],
                group_key=f"issue:{issue.id}:mentioned",
                title=issue.title,
                preview=preview,
                extra={"issue_identifier": issue.identifier},
            )
        # I4: the reporter/creator is a seeded subscriber (reason=creator, see
        # issue service _create_issue_tx), so they receive comment_created like
        # every other subscriber — the handler's author self-suppression keeps
        # the author from notifying themselves. subscribed_update is reserved
        # for status/field changes (emit_issue_change_notifications).

    async def _reaction_aggregation(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        viewer_member_id: uuid.UUID | None,
    ) -> list[dict]:
        rows = (
            await session.execute(
                select(CommentReaction, Member, User)
                .join(
                    Member,
                    (Member.workspace_id == CommentReaction.workspace_id)
                    & (Member.id == CommentReaction.actor_id),
                )
                .outerjoin(User, Member.user_id == User.id)
                .where(
                    CommentReaction.workspace_id == workspace_id,
                    CommentReaction.comment_id == comment_id,
                )
                .order_by(CommentReaction.emoji, CommentReaction.created_at)
            )
        ).all()
        grouped: dict[str, list[tuple[CommentReaction, Member, User | None]]] = defaultdict(list)
        for reaction, member, user in rows:
            grouped[reaction.emoji].append((reaction, member, user))
        aggregation: list[dict] = []
        for emoji, entries in grouped.items():
            actors = [
                {
                    "id": str(member.id),
                    "member_type": member.member_type,
                    "name": resolve_display_name(member=member, user=user),
                }
                for _reaction, member, user in entries
            ]
            aggregation.append(
                {
                    "emoji": emoji,
                    "count": len(entries),
                    "reacted_by_me": any(
                        reaction.actor_id == viewer_member_id
                        for reaction, _member, _user in entries
                    ),
                    "actors": actors,
                }
            )
        return aggregation

    async def _attach_preview_replies(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        items: list[dict],
        viewer_member_id: uuid.UUID | None,
    ) -> None:
        root_ids = [uuid.UUID(item["id"]) for item in items]
        rows = (
            await session.execute(
                select(Comment)
                .where(
                    Comment.workspace_id == workspace_id,
                    Comment.thread_root_id.in_(root_ids),
                )
                .order_by(Comment.created_at.asc(), Comment.id.asc())
            )
        ).scalars().all()
        by_root: dict[uuid.UUID, list[Comment]] = defaultdict(list)
        for reply in rows:
            by_root[reply.thread_root_id].append(reply)
        for item in items:
            replies = by_root.get(uuid.UUID(item["id"]), [])
            preview_rows = replies[:PREVIEW_REPLY_COUNT]
            item["preview_replies"] = [
                await self._render_comment(
                    session, reply, viewer_member_id=viewer_member_id
                )
                for reply in preview_rows
            ]

    async def _record_triggered_executions(
        self,
        session: AsyncSession,
        *,
        comment_id: uuid.UUID,
        triggered: dict[uuid.UUID, uuid.UUID],
    ) -> None:
        """Persist the skeleton execution ids on the mention rows (§3.5)."""
        for mentioned_id, execution_id in triggered.items():
            await session.execute(
                CommentMention.__table__.update()
                .where(
                    CommentMention.comment_id == comment_id,
                    CommentMention.mentioned_id == mentioned_id,
                )
                .values(triggered_execution_id=execution_id)
            )

    async def _render_comment(
        self,
        session: AsyncSession,
        comment: Comment,
        *,
        viewer_member_id: uuid.UUID | None,
        with_counts: bool = False,
    ) -> dict:
        author: dict | None = None
        if comment.author_kind == "member" and comment.author_id is not None:
            member = await session.scalar(
                select(Member).where(
                    Member.workspace_id == comment.workspace_id,
                    Member.id == comment.author_id,
                )
            )
            if member is not None:
                user = None
                if member.user_id is not None:
                    user = await session.scalar(select(User).where(User.id == member.user_id))
                author = {
                    "id": str(member.id),
                    "member_type": member.member_type,  # snapshot; source: members
                    "name": resolve_display_name(member=member, user=user),
                }
        resolved_by: str | None = None
        if comment.resolved_by_id is not None:
            resolved_by = str(comment.resolved_by_id)

        deleted = comment.deleted_at is not None
        reactions = await self._reaction_aggregation(
            session,
            workspace_id=comment.workspace_id,
            comment_id=comment.id,
            viewer_member_id=viewer_member_id,
        )
        mentions = await self._mention_snapshots(session, comment)

        reply_count: int | None = None
        if with_counts:
            reply_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Comment)
                    .where(
                        Comment.workspace_id == comment.workspace_id,
                        Comment.thread_root_id == comment.id,
                    )
                )
                or 0
            )

        triggered_rows = (
            await session.execute(
                select(CommentMention.triggered_execution_id).where(
                    CommentMention.workspace_id == comment.workspace_id,
                    CommentMention.comment_id == comment.id,
                    CommentMention.triggered_execution_id.is_not(None),
                    CommentMention.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        triggered = [str(row) for row in triggered_rows]

        return {
            "id": str(comment.id),
            "issue_id": str(comment.issue_id),
            "parent_id": str(comment.parent_id) if comment.parent_id else None,
            "thread_root_id": str(comment.thread_root_id) if comment.thread_root_id else None,
            "author_kind": comment.author_kind,
            "author": author,
            "body_markdown": "" if deleted else comment.body_markdown,
            "body_html": "" if deleted else comment.body_html,
            "body_text": "" if deleted else comment.body_text,
            "reactions": reactions,
            "reply_count": reply_count,
            "resolved_at": _isoformat(comment.resolved_at),
            "resolved_by": resolved_by,
            "mentions": mentions,
            "triggered_execution_ids": triggered,
            "deleted_at": _isoformat(comment.deleted_at),
            "created_at": _isoformat(comment.created_at),
            "updated_at": _isoformat(comment.updated_at),
            "edited_at": _isoformat(comment.edited_at),
        }

    async def _mention_snapshots(
        self, session: AsyncSession, comment: Comment
    ) -> list[dict]:
        rows = (
            await session.execute(
                select(CommentMention, Member, User)
                .join(
                    Member,
                    (Member.workspace_id == CommentMention.workspace_id)
                    & (Member.id == CommentMention.mentioned_id),
                )
                .outerjoin(User, Member.user_id == User.id)
                .where(
                    CommentMention.workspace_id == comment.workspace_id,
                    CommentMention.comment_id == comment.id,
                    CommentMention.deleted_at.is_(None),
                )
            )
        ).all()
        return [
            {
                "id": str(member.id),
                "member_type": member.member_type,  # snapshot; source: members
                "name": resolve_display_name(member=member, user=user),
            }
            for _mention, member, user in rows
        ]


def _check_body_bytes(body: str) -> None:
    # L1: align with README §6.14 (413 payload_too_large) / §3.3 vocabulary.
    if len(body.encode("utf-8")) > LONG_TEXT_MAX_BYTES:
        raise PayloadTooLargeError(
            "comment body exceeds the 1 MiB limit",
            details={"max_bytes": LONG_TEXT_MAX_BYTES},
        )


def _assert_version_match(updated_at: datetime, expected: str) -> None:
    """If-Match optimistic concurrency on updated_at (README §6.14)."""
    actual = _isoformat(updated_at)
    if expected not in (actual, updated_at.isoformat(), str(updated_at)):
        raise ConflictError("comment was modified concurrently")


__all__ = ["CommentService", "LONG_TEXT_MAX_BYTES", "PREVIEW_REPLY_COUNT"]
