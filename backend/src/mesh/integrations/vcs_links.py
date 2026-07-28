"""VCS object ↔ Mesh entity links (integrations.md §2.8 / §3.3).

``vcs_links`` is the single truth source for commit/PR/branch ↔ issue
association. Links are created explicitly (API) or automatically from
ingested VCS events: identifiers like ``WEB-123`` are resolved through
``UNIQUE(workspace_id, identifier)``; merge/close events drive issue status
flow through ``match_config.auto_status_map`` (validated transition, system
comment trail, idempotent per ingestion event).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.integration import Integration, IntegrationBinding, IntegrationEvent, VcsLink
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.workspace import Workspace
from mesh.errors import BusinessRuleError, NotFoundError
from mesh.integrations.connectors import NormalizedEvent
from mesh.issue.service import _workspace_issues_channel
from mesh.outbox.service import emit_realtime

logger = logging.getLogger("mesh.integrations.vcs_links")

# issue.md identifier: uppercase prefix + number (WEB-123).
IDENTIFIER_RE = re.compile(r"\b([A-Z][A-Z0-9_]+-\d+)\b")

VCS_ACTION_MERGED = "merged"
VCS_ACTION_CLOSED = "closed"


def extract_identifiers(*texts: str | None) -> list[str]:
    """Distinct issue identifiers mentioned in any text, order-preserving."""
    seen: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in IDENTIFIER_RE.findall(str(text)):
            if match not in seen:
                seen.append(match)
    return seen


def vcs_action(provider: str, event: NormalizedEvent) -> str | None:
    """Canonical VCS action for auto_status_map keys (merged/closed/…)."""
    if provider == "github":
        if event.extra.get("pr_merged"):
            return VCS_ACTION_MERGED
        action = str(event.extra.get("action") or "")
        return action or None
    if provider == "gitlab":
        state = str(event.extra.get("mr_state") or "")
        if state == "merged":
            return VCS_ACTION_MERGED
        action = str(event.extra.get("action") or "")
        if action == "close":
            return VCS_ACTION_CLOSED
        return state or action or None
    return None


def external_object_for(provider: str, event: NormalizedEvent) -> tuple[str, str] | None:
    """(external_object_type, external_object_ref) for the event's object."""
    repo = event.external_ref
    if provider == "github":
        if event.event_type == "pull_request":
            number = event.extra.get("pr_number")
            if number:
                return "pull_request", f"{repo}#{number}"
        if event.event_type == "push":
            ref = str(event.extra.get("ref") or "")
            if ref:
                return "branch", f"{repo}@{ref.removeprefix('refs/heads/')}"
        if repo:
            return "repository", repo
    if provider == "gitlab":
        iid = event.extra.get("mr_iid")
        if iid:
            return "merge_request", f"{repo}#{iid}"
        ref = str(event.extra.get("ref") or "")
        if ref and "push" in event.event_type.lower():
            return "branch", f"{repo}@{ref.removeprefix('refs/heads/')}"
        if repo:
            return "repository", repo
    return None


# ---------------------------------------------------------------------------
# Link CRUD
# ---------------------------------------------------------------------------


async def create_link(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    provider: str,
    provider_tenant_key: str,
    external_object_type: str,
    external_object_ref: str,
    mesh_entity_type: str,
    mesh_entity_id: uuid.UUID,
    link_source: str,
    created_by: uuid.UUID | None = None,
    external_state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> VcsLink | None:
    """Insert an ACTIVE link; partial-unique hit → idempotent skip (None)."""
    moment = now or datetime.now(UTC)
    link = VcsLink(
        workspace_id=workspace_id,
        integration_id=integration_id,
        provider=provider,
        provider_tenant_key=provider_tenant_key,
        external_object_type=external_object_type,
        external_object_ref=external_object_ref,
        mesh_entity_type=mesh_entity_type,
        mesh_entity_id=mesh_entity_id,
        link_source=link_source,
        status="active",
        external_state=external_state or {},
        created_by=created_by,
        created_at=moment,
        updated_at=moment,
    )
    session.add(link)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        # Existing active link for the same external object (partial unique
        # index) — idempotent skip; repeated events never double-link (§3.3).
        return None
    return link


async def list_issue_links(
    session: AsyncSession, *, workspace_id: uuid.UUID, issue_id: uuid.UUID
) -> list[VcsLink]:
    rows = (await session.execute(
        select(VcsLink).where(
            VcsLink.workspace_id == workspace_id,
            VcsLink.mesh_entity_type == "issue",
            VcsLink.mesh_entity_id == issue_id,
            VcsLink.status != "deleted",
        ).order_by(VcsLink.created_at.desc())
    )).scalars().all()
    return list(rows)


async def delete_link(
    session: AsyncSession, *, workspace_id: uuid.UUID, link_id: uuid.UUID, now: datetime
) -> VcsLink:
    """Mark ``status='deleted'`` (audit kept; partial unique slot freed)."""
    link = await session.scalar(
        select(VcsLink).where(VcsLink.id == link_id, VcsLink.workspace_id == workspace_id)
    )
    if link is None:
        raise NotFoundError("vcs link not found")
    link.status = "deleted"
    link.updated_at = now
    await session.flush()
    return link


def render_link(link: VcsLink) -> dict[str, Any]:
    return {
        "id": str(link.id),
        "integration_id": str(link.integration_id),
        "provider": link.provider,
        "external_object_type": link.external_object_type,
        "external_object_ref": link.external_object_ref,
        "mesh_entity_type": link.mesh_entity_type,
        "mesh_entity_id": str(link.mesh_entity_id),
        "link_source": link.link_source,
        "status": link.status,
        "external_state": link.external_state,
        "created_by": str(link.created_by) if link.created_by else None,
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


# ---------------------------------------------------------------------------
# Identifier resolution (UNIQUE(workspace_id, identifier))
# ---------------------------------------------------------------------------


async def resolve_issue_by_identifier(
    session: AsyncSession, *, workspace_id: uuid.UUID, identifier: str
) -> Issue | None:
    return await session.scalar(
        select(Issue).where(
            Issue.workspace_id == workspace_id,
            Issue.identifier == identifier,
            Issue.deleted_at.is_(None),
        )
    )


# ---------------------------------------------------------------------------
# Auto status flow (match_config.auto_status_map)
# ---------------------------------------------------------------------------


async def _find_target_status(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID | None, target: str
) -> IssueStatus | None:
    """Status by exact name (project scope first) else category default."""
    rows = (await session.execute(
        select(IssueStatus).where(
            IssueStatus.workspace_id == workspace_id,
            (IssueStatus.project_id == project_id) if project_id else IssueStatus.project_id.is_(None),
        )
    )).scalars().all()
    for status in rows:
        if status.name.lower() == target.lower():
            return status
    for status in rows:
        if status.category == target and status.is_default:
            return status
    # Fall back to workspace-level statuses for project-scoped issues.
    if project_id is not None:
        rows = (await session.execute(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace_id,
                IssueStatus.project_id.is_(None),
            )
        )).scalars().all()
        for status in rows:
            if status.name.lower() == target.lower():
                return status
        for status in rows:
            if status.category == target and status.is_default:
                return status
    return None


async def _transition_allowed(
    session: AsyncSession, *, workspace_id: uuid.UUID, issue: Issue, target: IssueStatus
) -> bool:
    """issue.md strict-mode guard (status_strict_mode + allowed_transitions)."""
    workspace = await session.get(Workspace, workspace_id)
    settings = (workspace.settings if workspace else None) or {}
    if not bool(settings.get("status_strict_mode", False)):
        return True
    current = await session.get(IssueStatus, issue.status_id)
    if current is None:
        return True
    allowed = [str(t) for t in (current.allowed_transitions or [])]
    if not allowed:
        return True  # unconfigured = unrestricted (issue.md §4.4)
    return str(target.id) in allowed


async def apply_auto_status(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue: Issue,
    target: str,
    action: str,
    external_ref: str,
    event_row: IntegrationEvent,
    now: datetime,
) -> bool:
    """Idempotent status flow: transition + system comment + realtime.

    Returns True when the issue is in the target state afterwards (already
    there counts). False = target missing / illegal transition (audit only).
    """
    status = await _find_target_status(
        session, workspace_id=workspace_id, project_id=issue.project_id, target=target
    )
    if status is None:
        logger.info(
            "vcs auto status skipped: no status %r for issue %s", target, issue.id
        )
        return False
    if issue.status_id == status.id:
        return True  # repeat event: already in target state — idempotent no-op
    if not await _transition_allowed(
        session, workspace_id=workspace_id, issue=issue, target=status
    ):
        logger.info(
            "vcs auto status skipped: transition to %r not allowed for issue %s",
            target, issue.id,
        )
        return False

    issue.status_id = status.id
    issue.state_category = status.category
    issue.completed_at = now if status.category == "done" else None
    issue.updated_at = now
    issue.version = (issue.version or 0) + 1
    await session.flush()

    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=_workspace_issues_channel(workspace_id),
        event="issue.updated",
        data={
            "id": str(issue.id),
            "status_id": str(status.id),
            "state_category": status.category,
            "updated_at": issue.updated_at.isoformat(),
            "version": issue.version,
            "source": "vcs_auto_flow",
        },
        idempotency_key=(
            f"vcs-auto-status:{event_row.id}:{issue.id}:{action}"
        ),
    )
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=f"issue:{issue.id}",
        event="issue.updated",
        data={
            "id": str(issue.id),
            "status_id": str(status.id),
            "state_category": status.category,
            "updated_at": issue.updated_at.isoformat(),
            "version": issue.version,
            "source": "vcs_auto_flow",
        },
        idempotency_key=(
            f"vcs-auto-status:{event_row.id}:{issue.id}:{action}:detail"
        ),
    )

    # System-activity comment trail (idempotent per event×issue×action).
    from mesh.comment_inbox.markdown import render_body
    from mesh.db.models.comment import Comment

    comment_body = (
        f"VCS 自动流转:{external_ref} {action} → 状态自动置为 **{status.name}**"
    )
    rendered = render_body(comment_body)
    comment = Comment(
        workspace_id=workspace_id,
        issue_id=issue.id,
        author_kind="system",
        author_id=None,
        body_markdown=comment_body,
        body_html=rendered.html,
        body_text=rendered.text,
        idempotency_key=f"vcs-auto-comment:{event_row.id}:{issue.id}:{action}",
    )
    session.add(comment)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        pass  # duplicate event — comment already written
    return True


# ---------------------------------------------------------------------------
# Ingestion hook — auto link + auto status flow (best effort, audit only)
# ---------------------------------------------------------------------------


async def ingest_vcs_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration: Integration,
    provider: str,
    event: NormalizedEvent,
    event_row: IntegrationEvent,
    now: datetime,
) -> dict[str, Any]:
    """Auto-link identifiers + apply auto_status_map for one ingested event.

    Never raises for resolution misses (``identifier_not_resolved`` is an
    audit outcome, not an ingestion failure — §3.3/§3.5).
    """
    result: dict[str, Any] = {"links_created": 0, "issues_transitioned": 0}
    bindings = (await session.execute(
        select(IntegrationBinding).where(
            IntegrationBinding.workspace_id == workspace_id,
            IntegrationBinding.integration_id == integration.id,
            IntegrationBinding.external_ref == event.external_ref,
            IntegrationBinding.status == "active",
        )
    )).scalars().all()

    obj = external_object_for(provider, event)
    branch = str(event.extra.get("source_branch") or event.extra.get("ref") or "")
    identifiers = extract_identifiers(
        event.text, branch, str(event.extra.get("pr_title") or event.extra.get("mr_title") or "")
    )
    issues: list[Issue] = []
    for identifier in identifiers:
        issue = await resolve_issue_by_identifier(
            session, workspace_id=workspace_id, identifier=identifier
        )
        if issue is None:
            logger.info(
                "vcs identifier_not_resolved: %s (event %s)", identifier, event_row.id
            )
            continue
        issues.append(issue)
        if obj is not None:
            link_source = "auto_branch" if branch and identifier in branch else "auto_keyword"
            created = await create_link(
                session,
                workspace_id=workspace_id,
                integration_id=integration.id,
                provider=provider,
                provider_tenant_key=event.tenant_key,
                external_object_type=obj[0],
                external_object_ref=obj[1],
                mesh_entity_type="issue",
                mesh_entity_id=issue.id,
                link_source=link_source,
                now=now,
            )
            if created is not None:
                result["links_created"] += 1

    action = vcs_action(provider, event)
    if action is None or not issues:
        return result
    for binding in bindings:
        auto_status_map = (binding.match_config or {}).get("auto_status_map") or {}
        target = auto_status_map.get(action)
        if not target:
            continue
        for issue in issues:
            transitioned = await apply_auto_status(
                session,
                workspace_id=workspace_id,
                issue=issue,
                target=str(target),
                action=action,
                external_ref=event.external_ref,
                event_row=event_row,
                now=now,
            )
            if transitioned:
                result["issues_transitioned"] += 1
        # Refresh link state / staleness for this event's object.
        if obj is not None:
            links = (await session.execute(
                select(VcsLink).where(
                    VcsLink.workspace_id == workspace_id,
                    VcsLink.integration_id == integration.id,
                    VcsLink.external_object_ref == obj[1],
                    VcsLink.status == "active",
                )
            )).scalars().all()
            stale = action in (VCS_ACTION_MERGED, VCS_ACTION_CLOSED)
            state_key = {
                "pull_request": "pr_state",
                "merge_request": "mr_state",
                "branch": "branch_state",
                "repository": "repository_state",
            }.get(obj[0], "object_state")
            for link in links:
                link.external_state = {
                    **(link.external_state or {}),
                    state_key: action,
                }
                if stale:
                    link.status = "stale"
                link.updated_at = now
            await session.flush()
        break  # first binding with a map wins (bindings share the repo)
    return result


# ---------------------------------------------------------------------------
# API-level helpers (§3.3 explicit endpoints)
# ---------------------------------------------------------------------------


async def explicit_link(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration: Integration,
    provider: str,
    provider_tenant_key: str,
    vcs_ref: dict[str, Any],
    issue_id: uuid.UUID,
    created_by: uuid.UUID,
    now: datetime,
) -> dict[str, Any]:
    """Manual PR/commit/branch ↔ issue link (POST /integrations/vcs/links)."""
    object_type = str(vcs_ref.get("type") or "")
    object_ref = str(vcs_ref.get("id") or vcs_ref.get("url") or "")
    allowed_types = {"repository", "pull_request", "merge_request", "issue", "commit", "branch"}
    if object_type not in allowed_types or not object_ref:
        raise BusinessRuleError(
            "invalid vcs_ref", code="vcs_link_invalid",
            details={"type": object_type, "id": object_ref},
        )
    issue = await resolve_issue(session, workspace_id=workspace_id, issue_id=issue_id)
    existing = await session.scalar(
        select(VcsLink).where(
            VcsLink.workspace_id == workspace_id,
            VcsLink.provider == provider,
            VcsLink.provider_tenant_key == provider_tenant_key,
            VcsLink.external_object_type == object_type,
            VcsLink.external_object_ref == object_ref,
            VcsLink.status == "active",
        )
    )
    if existing is not None:
        if existing.mesh_entity_id == issue.id:
            return render_link(existing)  # idempotent same-issue re-link
        raise BusinessRuleError(
            "external object already linked to another issue",
            code="conflict",
            details={"existing_issue_id": str(existing.mesh_entity_id)},
        )
    link = await create_link(
        session,
        workspace_id=workspace_id,
        integration_id=integration.id,
        provider=provider,
        provider_tenant_key=provider_tenant_key,
        external_object_type=object_type,
        external_object_ref=object_ref,
        mesh_entity_type="issue",
        mesh_entity_id=issue.id,
        link_source="manual",
        created_by=created_by,
        now=now,
    )
    if link is None:  # race: another writer won the slot
        raise BusinessRuleError(
            "external object already linked", code="conflict"
        )
    return render_link(link)


async def resolve_issue(
    session: AsyncSession, *, workspace_id: uuid.UUID, issue_id: uuid.UUID
) -> Issue:
    issue = await session.scalar(
        select(Issue).where(Issue.id == issue_id, Issue.workspace_id == workspace_id)
    )
    if issue is None:
        raise BusinessRuleError(
            "issue not found in workspace", code="vcs_link_invalid"
        )
    return issue


async def resolve_from_text(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration: Integration,
    provider: str,
    provider_tenant_key: str,
    source_text: str,
    vcs_ref: dict[str, Any],
    link_source: str = "auto_keyword",
    now: datetime,
) -> dict[str, Any]:
    """Identifier extraction endpoint (POST /integrations/vcs/resolve)."""
    object_type = str(vcs_ref.get("type") or "commit")
    object_ref = str(vcs_ref.get("id") or vcs_ref.get("url") or source_text[:200])
    identifiers = extract_identifiers(source_text)
    linked: list[dict[str, Any]] = []
    for identifier in identifiers:
        issue = await resolve_issue_by_identifier(
            session, workspace_id=workspace_id, identifier=identifier
        )
        if issue is None:
            raise BusinessRuleError(
                "identifier not resolved",
                code="identifier_not_resolved",
                details={"identifier": identifier},
            )
        link = await create_link(
            session,
            workspace_id=workspace_id,
            integration_id=integration.id,
            provider=provider,
            provider_tenant_key=provider_tenant_key,
            external_object_type=object_type,
            external_object_ref=object_ref,
            mesh_entity_type="issue",
            mesh_entity_id=issue.id,
            link_source=link_source,
            now=now,
        )
        if link is not None:
            linked.append(render_link(link))
    return {"identifiers": identifiers, "links": linked}


__all__ = [
    "IDENTIFIER_RE",
    "apply_auto_status",
    "create_link",
    "delete_link",
    "explicit_link",
    "external_object_for",
    "extract_identifiers",
    "ingest_vcs_event",
    "list_issue_links",
    "render_link",
    "resolve_from_text",
    "resolve_issue_by_identifier",
    "vcs_action",
]
