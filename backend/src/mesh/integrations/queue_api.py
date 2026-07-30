"""Queue query / operation endpoints (integrations.md §3.9).

Read + command surface over ``integration_message_queue``:

* ``GET  .../integrations/{id}/queue`` — the authorized queue slice: per-item
  ``position`` (pending items only), resolved ``sender`` / ``target_agent``,
  sanitized ``message_excerpt`` (full text is never exposed), ack/execution/
  timestamp fields. Fixed ``WHERE binding_id IS NOT NULL`` — orphan audit rows
  are excluded from the normal endpoints (the audit endpoint is their only read
  path). Project-scoped items (``project_id_snapshot IS NOT NULL``) are filtered
  by the requester's project visibility.
* ``GET  .../integrations/{id}/queue/summary`` — per-conversation pending count
  + current in-flight summaries (panel badges; same visibility + orphan
  exclusion).
* ``POST .../integrations/{id}/queue/{item_id}:cancel`` — cancel a PENDING item
  via an atomic conditional UPDATE (0 rows → 422 ``queue_item_not_cancellable``,
  closing the dispatcher TOCTOU race). Authorization is the full identity triple
  resolved to ``users.id`` (§2.10): the item's ``sender_identity_key`` third
  segment composed with the ``(provider, provider_tenant_key)`` derived from its
  ``conversation_key`` is resolved through ``external_identities`` and compared
  against the requester's ``users.id``; bare ``external_user_key`` resolution is
  forbidden (cross-provider keys may map different users). ``integration:manage``
  may cancel anything. Forbidden → 403 ``command_forbidden``.
* ``GET  .../integration-queue-audit`` — orphan audit rows only
  (``binding_id IS NULL``, terminal). Visibility: ``project_id_snapshot`` set →
  project-visible (a physically-deleted snapshot project is admin/owner-only);
  unset (former workspace-scoped binding) → every member.

This router carries NO prefix: it is included into ``integrations.routes.router``
which already supplies ``/api/v1`` (avoids a doubled prefix).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from mesh.api.pagination import Page, paginate
from mesh.auth.rbac import WorkspaceContext, require_workspace, role_satisfies
from mesh.db.models.agent import Agent
from mesh.db.models.integration import (
    QUEUE_INFLIGHT_STATES,
    QUEUE_TERMINAL_STATES,
    ExternalIdentity,
    IntegrationMessageQueue,
)
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ForbiddenError, NotFoundError
from mesh.integrations.queue_events import emit_queue_updated

logger = logging.getLogger("mesh.integrations.queue_api")

router = APIRouter(tags=["integrations-queue"])

# §3.9: ``position`` counts pending items ahead in the same conversation.
_PENDING_STATE = "pending"


# ---------------------------------------------------------------------------
# Identity-triple helpers (§2.10 — never resolve by bare external_user_key)
# ---------------------------------------------------------------------------


def split_identity_triple(key: str) -> tuple[str, str, str] | None:
    """Split ``<provider>:<provider_tenant_key>:<external_user_key>``.

    Returns ``None`` for a malformed key (wrong segment count, an empty
    segment, or a ``:`` in the third segment — the user/conversation ref
    segment never contains the separator). The third segment is opaque
    elsewhere; here we only structural-validate it.
    """
    if not key:
        return None
    parts = key.split(":", 2)
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2]:
        return None
    if ":" in parts[2]:
        return None
    return parts[0], parts[1], parts[2]


async def resolve_triple_user_id(
    session: AsyncSession, *, provider: str, tenant_key: str, external_user_key: str
) -> uuid.UUID | None:
    """Resolve a FULL identity triple to ``users.id`` (never a bare key)."""
    return await session.scalar(
        select(ExternalIdentity.user_id).where(
            ExternalIdentity.provider == provider,
            ExternalIdentity.provider_tenant_key == tenant_key,
            ExternalIdentity.external_user_key == external_user_key,
        )
    )


async def resolve_item_sender_user_id(
    session: AsyncSession, item: IntegrationMessageQueue
) -> uuid.UUID | None:
    """The item sender's ``users.id`` via the full triple (§3.9 授权).

    ``(provider, provider_tenant_key)`` come from the item's
    ``conversation_key``; the ``external_user_key`` is the third segment of the
    item's ``sender_identity_key``. The composed triple resolves through
    ``external_identities`` — a bare ``external_user_key`` is never queried.
    """
    conversation = split_identity_triple(item.conversation_key)
    sender = split_identity_triple(item.sender_identity_key)
    if conversation is None or sender is None:
        return None
    provider, tenant_key, _conversation_ref = conversation
    _provider, _tenant, external_user_key = sender
    return await resolve_triple_user_id(
        session, provider=provider, tenant_key=tenant_key, external_user_key=external_user_key
    )


# ---------------------------------------------------------------------------
# Project visibility (§3.9 — project-scoped items follow project.md rules)
# ---------------------------------------------------------------------------


def project_visibility_clause(viewer: Member):
    """SQL filter for ``project_id_snapshot`` visibility, or ``None`` = all.

    Workspace-scoped items (``project_id_snapshot IS NULL``) are visible to
    every member. Project-scoped items require project visibility: managers
    (``project:manage``) see all (incl. physically-deleted snapshot projects —
    the audit-endpoint admin fallback); guests see only granted live projects;
    other members see live public projects or projects they belong to. Filtering
    the subqueries by ``deleted_at IS NULL`` makes a deleted snapshot project's
    orphans invisible to non-managers (audit endpoint §3.9 写死).
    """
    item_table = IntegrationMessageQueue
    if role_satisfies(viewer.role, "project:manage"):
        return None
    if viewer.role == "guest":
        visible_projects = select(Project.id).where(
            Project.deleted_at.is_(None),
            Project.id.in_(
                select(MemberProjectAccess.project_id).where(
                    MemberProjectAccess.member_id == viewer.id
                )
            ),
        )
        return or_(
            item_table.project_id_snapshot.is_(None),
            item_table.project_id_snapshot.in_(visible_projects),
        )
    member_projects = select(Project.id).where(
        Project.deleted_at.is_(None),
        Project.id.in_(
            select(ProjectMember.project_id).where(ProjectMember.member_id == viewer.id)
        ),
    )
    public_projects = select(Project.id).where(
        Project.deleted_at.is_(None), Project.visibility == "public"
    )
    return or_(
        item_table.project_id_snapshot.is_(None),
        item_table.project_id_snapshot.in_(member_projects),
        item_table.project_id_snapshot.in_(public_projects),
    )


# ---------------------------------------------------------------------------
# Batched enrichment (sender display / target agent / position)
# ---------------------------------------------------------------------------


async def _resolve_senders(
    session: AsyncSession, items: list[IntegrationMessageQueue]
) -> dict[uuid.UUID, dict]:
    """Resolve ``sender = {identity_key, display_name, linked}`` per item.

    Linked senders resolve their triple through ``external_identities`` to the
    Mesh user's display name; unlinked senders fall back to the identity key
    string with ``linked=False`` (§3.9 — full text never exposed; display only).
    """
    result: dict[uuid.UUID, dict] = {}
    triples: dict[tuple[str, str, str], list[uuid.UUID]] = {}
    for item in items:
        parsed = split_identity_triple(item.sender_identity_key)
        result[item.id] = {
            "identity_key": item.sender_identity_key,
            "display_name": item.sender_identity_key,
            "linked": False,
        }
        if parsed is not None:
            triples.setdefault(parsed, []).append(item.id)
    if not triples:
        return result
    conditions = [
        (ExternalIdentity.provider == provider)
        & (ExternalIdentity.provider_tenant_key == tenant_key)
        & (ExternalIdentity.external_user_key == user_key)
        for provider, tenant_key, user_key in triples
    ]
    rows = (
        (await session.execute(select(ExternalIdentity).where(or_(*conditions)))).scalars().all()
    )
    triple_to_user = {
        (row.provider, row.provider_tenant_key, row.external_user_key): row.user_id for row in rows
    }
    user_names: dict[uuid.UUID, str] = {}
    user_ids = {row.user_id for row in rows}
    if user_ids:
        user_rows = (
            (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        )
        user_names = {user.id: user.display_name for user in user_rows}
    for triple, item_ids in triples.items():
        user_id = triple_to_user.get(triple)
        if user_id is None:
            continue
        display = user_names.get(user_id, ":".join(triple))
        for item_id in item_ids:
            result[item_id] = {
                "identity_key": result[item_id]["identity_key"],
                "display_name": display,
                "linked": True,
            }
    return result


async def _resolve_target_agents(
    session: AsyncSession, items: list[IntegrationMessageQueue]
) -> dict[uuid.UUID, dict]:
    """Resolve ``target_agent = {id, name}`` for snapshotted agent ids.

    ``target_agent_id`` is an enqueue-time snapshot with ``ON DELETE SET NULL``;
    a deleted agent leaves NULL, rendered as ``target_agent: null`` (§3.9 —
    retargeting is never retroactive).
    """
    agent_ids = {item.target_agent_id for item in items if item.target_agent_id is not None}
    if not agent_ids:
        return {}
    rows = (await session.execute(select(Agent).where(Agent.id.in_(agent_ids)))).scalars().all()
    return {agent.id: {"id": str(agent.id), "name": agent.name} for agent in rows}


async def _compute_positions(
    session: AsyncSession, items: list[IntegrationMessageQueue]
) -> dict[uuid.UUID, int | None]:
    """``position`` for pending items: pending siblings with a smaller seq + 1.

    Non-pending items have no position (``None``). A correlated count over the
    pending partial index (``idx_imq_conversation_pending``) keeps it cheap; the
    count is global to the conversation, not the page, so positions stay correct
    across page boundaries.
    """
    positions: dict[uuid.UUID, int | None] = {item.id: None for item in items}
    pending_ids = [item.id for item in items if item.state == _PENDING_STATE]
    if not pending_ids:
        return positions
    outer = IntegrationMessageQueue
    inner = aliased(IntegrationMessageQueue)
    # Correlated count of pending siblings ahead in the same conversation;
    # ``inner`` is an alias so the subquery's FROM is distinct from the outer
    # row it correlates against (the pending partial index serves the count).
    preceding = (
        select(func.count())
        .select_from(inner)
        .where(inner.conversation_key == outer.conversation_key)
        .where(inner.state == _PENDING_STATE)
        .where(inner.seq < outer.seq)
        .correlate(outer)
        .scalar_subquery()
    )
    stmt = select(outer.id, (preceding + 1).label("position")).where(outer.id.in_(pending_ids))
    for row in (await session.execute(stmt)).all():
        positions[row[0]] = int(row[1])
    return positions


# ---------------------------------------------------------------------------
# Renderers (§6.14 envelopes applied at the route layer)
# ---------------------------------------------------------------------------


def _isoformat(value) -> str | None:
    return value.isoformat() if value is not None else None


def render_queue_item(
    item: IntegrationMessageQueue,
    *,
    position: int | None,
    sender: dict | None,
    target_agent: dict | None,
) -> dict:
    return {
        "id": str(item.id),
        "conversation_key": item.conversation_key,
        "seq": item.seq,
        "state": item.state,
        "dispatch_mode": item.dispatch_mode,
        "position": position,
        "sender": sender
        or {
                "identity_key": item.sender_identity_key,
                "display_name": item.sender_identity_key,
                "linked": False,
            },
        "target_agent": target_agent,
        "message_excerpt": item.message_excerpt,
        "ack_sent_at": _isoformat(item.ack_sent_at),
        "ack_merged_into": str(item.ack_merged_into) if item.ack_merged_into else None,
        "execution_id": str(item.execution_id) if item.execution_id else None,
        "enqueued_at": _isoformat(item.enqueued_at),
        "started_at": _isoformat(item.started_at),
        "finished_at": _isoformat(item.finished_at),
    }


def render_audit_row(item: IntegrationMessageQueue, *, sender: dict | None) -> dict:
    """Self-describing orphan audit row (§3.9 audit endpoint)."""
    return {
        "id": str(item.id),
        "binding_display": item.binding_display,
        "conversation_key": item.conversation_key,
        "sender_identity_key": item.sender_identity_key,
        "sender": sender
        or {
                "identity_key": item.sender_identity_key,
                "display_name": item.sender_identity_key,
                "linked": False,
            },
        "state": item.state,
        "project_id_snapshot": str(item.project_id_snapshot) if item.project_id_snapshot else None,
        "message_excerpt": item.message_excerpt,
        "enqueued_at": _isoformat(item.enqueued_at),
        "started_at": _isoformat(item.started_at),
        "finished_at": _isoformat(item.finished_at),
    }


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


async def list_queue_items(
    session_factory,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    viewer: Member,
    state: str | None = None,
    conversation_key: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    """§3.9 queue list: authorized slice, orphan-excluded, project-filtered."""
    async with session_factory() as session:
        await set_tenant_context(session, workspace_id)
        queue = IntegrationMessageQueue
        conditions = [
            queue.workspace_id == workspace_id,
            queue.integration_id == integration_id,
            queue.binding_id.is_not(None),  # fixed orphan exclusion (§3.9)
        ]
        if state:
            conditions.append(queue.state == state)
        if conversation_key:
            conditions.append(queue.conversation_key == conversation_key)
        visibility = project_visibility_clause(viewer)
        if visibility is not None:
            conditions.append(visibility)
        stmt = select(queue).where(*conditions)
        page: Page = await paginate(
            session,
            stmt,
            sort_column=queue.enqueued_at,
            id_column=queue.id,
            sort_value_of=lambda row: row.enqueued_at,
            id_of=lambda row: row.id,
            cursor=cursor,
            limit=limit,
            descending=True,
        )
        items = list(page.items)
        positions = await _compute_positions(session, items)
        senders = await _resolve_senders(session, items)
        agents = await _resolve_target_agents(session, items)
    return {
        "data": [
            render_queue_item(
                item,
                position=positions.get(item.id),
                sender=senders.get(item.id),
                target_agent=agents.get(item.target_agent_id) if item.target_agent_id else None,
            )
            for item in items
        ],
        "next_cursor": page.next_cursor,
    }


async def queue_summary(
    session_factory,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    viewer: Member,
) -> dict:
    """§3.9 summary: per-conversation pending count + in-flight summaries."""
    async with session_factory() as session:
        await set_tenant_context(session, workspace_id)
        queue = IntegrationMessageQueue
        conditions = [
            queue.workspace_id == workspace_id,
            queue.integration_id == integration_id,
            queue.binding_id.is_not(None),
        ]
        visibility = project_visibility_clause(viewer)
        if visibility is not None:
            conditions.append(visibility)
        pending_rows = (
            await session.execute(
                select(queue.conversation_key, func.count())
                .where(*conditions, queue.state == _PENDING_STATE)
                .group_by(queue.conversation_key)
            )
        ).all()
        inflight_rows = (
            (
                await session.execute(
                    select(queue).where(*conditions, queue.state.in_(QUEUE_INFLIGHT_STATES))
                )
            )
            .scalars()
            .all()
        )
    by_conversation: dict[str, dict] = {}
    for conversation_key, count in pending_rows:
        entry = by_conversation.setdefault(
            conversation_key,
            {"conversation_key": conversation_key, "pending_count": 0, "in_flight": []},
        )
        entry["pending_count"] = int(count)
    for item in inflight_rows:
        entry = by_conversation.setdefault(
            item.conversation_key,
            {"conversation_key": item.conversation_key, "pending_count": 0, "in_flight": []},
        )
        entry["in_flight"].append({"id": str(item.id), "state": item.state, "seq": item.seq})
    data = [by_conversation[key] for key in sorted(by_conversation)]
    for entry in data:
        entry["in_flight"].sort(key=lambda summary: summary["seq"])
    return {"data": data}


async def cancel_queue_item(
    session_factory,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    item_id: uuid.UUID,
    requester: Member,
) -> dict:
    """§3.9 ``:cancel`` — atomic pending cancel with triple-based authz.

    Authorization is checked first (uniform 403 for unauthorized callers — no
    cancellability/state leak); the conditional UPDATE is the authoritative
    pending guard (0 rows → 422). On success the invalidation notice is emitted
    in the same transaction.
    """
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        queue = IntegrationMessageQueue
        item = (
            await session.execute(
                select(queue)
                .where(
                    queue.id == item_id,
                    queue.integration_id == integration_id,
                    queue.workspace_id == workspace_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if item is None:
            raise NotFoundError("queue item not found")
        if not role_satisfies(requester.role, "integration:manage"):
            sender_user_id = await resolve_item_sender_user_id(session, item)
            if requester.user_id is None or sender_user_id != requester.user_id:
                raise ForbiddenError(
                    "not authorized to cancel this queue item",
                    code="command_forbidden",
                )
        result = await session.execute(
            update(queue)
            .where(queue.id == item_id, queue.state == _PENDING_STATE)
            .values(state="cancelled", finished_at=func.now(), updated_at=func.now())
        )
        if result.rowcount == 0:
            raise BusinessRuleError(
                "queue item is not cancellable",
                code="queue_item_not_cancellable",
            )
        item.state = "cancelled"  # in-memory view for the invalidation payload
        await emit_queue_updated(session, item=item, idempotency_key=f"imq-updated:{item.id}:cancelled")
    return {"data": {"id": str(item_id), "state": "cancelled"}}


async def list_queue_audit(
    session_factory,
    *,
    workspace_id: uuid.UUID,
    viewer: Member,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    """§3.9 audit endpoint: the ONLY read path for orphan queue rows."""
    async with session_factory() as session:
        await set_tenant_context(session, workspace_id)
        queue = IntegrationMessageQueue
        conditions = [
            queue.workspace_id == workspace_id,
            queue.binding_id.is_(None),  # orphans only
            queue.state.in_(QUEUE_TERMINAL_STATES),  # terminal only
        ]
        visibility = project_visibility_clause(viewer)
        if visibility is not None:
            conditions.append(visibility)
        stmt = select(queue).where(*conditions)
        page: Page = await paginate(
            session,
            stmt,
            sort_column=queue.enqueued_at,
            id_column=queue.id,
            sort_value_of=lambda row: row.enqueued_at,
            id_of=lambda row: row.id,
            cursor=cursor,
            limit=limit,
            descending=True,
        )
        items = list(page.items)
        senders = await _resolve_senders(session, items)
    return {
        "data": [render_audit_row(item, sender=senders.get(item.id)) for item in items],
        "next_cursor": page.next_cursor,
    }


# ---------------------------------------------------------------------------
# Routes (included into integrations.routes.router → /api/v1 prefix)
# ---------------------------------------------------------------------------


def _path_uuid(value: str, *, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(f"{what} not found") from exc


@router.get("/workspaces/{workspace_id}/integrations/{integration_id}/queue")
async def get_integration_queue(
    request: Request,
    workspace_id: str,
    integration_id: str,
    state: str | None = None,
    conversation_key: str | None = None,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await list_queue_items(
        request.app.state.session_factory,
        workspace_id=context.workspace.id,
        integration_id=_path_uuid(integration_id, what="integration"),
        viewer=context.member,
        state=state,
        conversation_key=conversation_key,
        cursor=cursor,
        limit=limit,
    )


@router.get("/workspaces/{workspace_id}/integrations/{integration_id}/queue/summary")
async def get_integration_queue_summary(
    request: Request,
    workspace_id: str,
    integration_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await queue_summary(
        request.app.state.session_factory,
        workspace_id=context.workspace.id,
        integration_id=_path_uuid(integration_id, what="integration"),
        viewer=context.member,
    )


@router.post("/workspaces/{workspace_id}/integrations/{integration_id}/queue/{item_id}:cancel")
async def cancel_integration_queue_item(
    request: Request,
    workspace_id: str,
    integration_id: str,
    item_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await cancel_queue_item(
        request.app.state.session_factory,
        workspace_id=context.workspace.id,
        integration_id=_path_uuid(integration_id, what="integration"),
        item_id=_path_uuid(item_id, what="queue item"),
        requester=context.member,
    )


@router.get("/workspaces/{workspace_id}/integration-queue-audit")
async def get_integration_queue_audit(
    request: Request,
    workspace_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await list_queue_audit(
        request.app.state.session_factory,
        workspace_id=context.workspace.id,
        viewer=context.member,
        cursor=cursor,
        limit=limit,
    )


__all__ = [
    "cancel_queue_item",
    "list_queue_audit",
    "list_queue_items",
    "project_visibility_clause",
    "queue_summary",
    "render_audit_row",
    "render_queue_item",
    "resolve_item_sender_user_id",
    "resolve_triple_user_id",
    "router",
    "split_identity_triple",
]
