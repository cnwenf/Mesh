"""Outbox relay consumer — domain events → autopilot triggers (autopilot.md §4.5 / README §6.6 / §6.9).

Business modules write ``realtime.publish`` outbox rows inside their own
transactions; this matcher is the RELAY CONSUMER side (not an in-process
event bus — §6.6 hard constraint). It is chained onto the relay's
``realtime.publish`` handler AFTER the realtime projector, so a single
claim delivers both projections and trigger matching; relay redelivery
(crash between business commit and publish) re-runs the matcher, and
run creation is idempotent through the guardrail dedup window.

Event → trigger mapping:

* ``issue.created`` → ``issue_created``;
* ``issue.updated`` → ``issue_status_changed`` when status changed
  (``to_status`` from the new status name / state category; the internal
  event carries no prior status, so a configured ``from_status`` matches
  only when the event itself supplies one — ``issue.moved`` does), and
  ``issue_field_changed`` when watched fields changed;
* ``issue.moved`` → ``issue_status_changed`` WITH from/to state categories;
* ``comment.created`` → ``comment_created``, plus ``agent_mentioned`` when
  the comment mentions an agent member.

Cascade tracing: when the triggering comment was PRODUCED by an autopilot
run (its artifacts reference the comment) — or the triggering issue was
CREATED by an autopilot run (its artifacts reference the issue) — the new
run links ``parent_run_id`` / ``cascade_depth + 1`` — the guardrail gate
cuts the chain at ``cascade_max_depth`` (agent↔agent loop protection,
§2.6 / §5.3). Tracing issue artifacts is what closes the
``create_issue ↔ issue_created`` self-loop: every issue the action
creates carries its lineage, so the depth accumulates across fresh issues
instead of resetting to zero each round.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.autopilot import runs as runs_mod
from mesh.autopilot.filters import match_filter_config
from mesh.autopilot.guardrails import evaluate_trigger
from mesh.db.models.autopilot import Autopilot, AutopilotArtifact, AutopilotRun
from mesh.db.models.issue import Issue
from mesh.db.models.label import IssueLabel, Label
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.tenant import set_tenant_context

logger = logging.getLogger("mesh.autopilot.matcher")

# Realtime event names that can trigger rules (mapped to autopilot trigger
# types — all registered in the §6.7 vocabulary).
_HANDLED_EVENTS = frozenset({"issue.created", "issue.updated", "issue.moved", "comment.created"})

# Trigger types the matcher can produce.
_MATCHER_TRIGGER_TYPES = frozenset(
    {"issue_created", "issue_status_changed", "issue_field_changed", "comment_created", "agent_mentioned"}
)

# issue.updated change keys that are NOT generic field changes.
_STATUS_CHANGE_KEYS = ("status_id", "state_category", "status")
_RENDERED_EXTRA_KEYS = ("status", "assignee")


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


async def _candidate_rules(
    session: AsyncSession, *, workspace_id: uuid.UUID, trigger_types: frozenset[str]
) -> list[Autopilot]:
    return list(
        (
            await session.execute(
                select(Autopilot).where(
                    Autopilot.workspace_id == workspace_id,
                    Autopilot.trigger_type.in_(tuple(trigger_types)),
                    Autopilot.status == "active",
                    Autopilot.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


async def _issue_filter_context(
    session: AsyncSession, *, workspace_id: uuid.UUID, issue_id: uuid.UUID
) -> dict[str, Any] | None:
    """Load the issue + label names the filter dimensions match against."""
    issue = await session.scalar(
        select(Issue).where(Issue.workspace_id == workspace_id, Issue.id == issue_id)
    )
    if issue is None:
        return None
    label_names = (
        (
            await session.execute(
                select(Label.name)
                .join(IssueLabel, IssueLabel.label_id == Label.id)
                .where(
                    IssueLabel.workspace_id == workspace_id,
                    IssueLabel.issue_id == issue_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "project_id": str(issue.project_id) if issue.project_id else None,
        "labels": list(label_names),
        "priority": issue.priority,
        "title": issue.title,
        "body": issue.description or "",
        "issue": issue,
    }


async def _parent_run_for_artifact(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact_type: str,
    ref_table: str,
    ref_id: uuid.UUID,
) -> tuple[uuid.UUID | None, int, bool]:
    """(parent_run_id, cascade_depth, produced_by_run) for a resource that
    an autopilot run produced — the cascade lineage anchor (§5.3 防回环)."""
    row = (
        await session.execute(
            select(AutopilotArtifact.run_id, AutopilotRun.cascade_depth)
            .join(AutopilotRun, AutopilotRun.id == AutopilotArtifact.run_id)
            .where(
                AutopilotArtifact.workspace_id == workspace_id,
                AutopilotArtifact.artifact_type == artifact_type,
                AutopilotArtifact.ref_table == ref_table,
                AutopilotArtifact.ref_id == ref_id,
            )
            .order_by(AutopilotArtifact.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None, 0, False
    return row[0], row[1] + 1, True


async def _parent_run_for_comment(
    session: AsyncSession, *, workspace_id: uuid.UUID, comment_id: uuid.UUID
) -> tuple[uuid.UUID | None, int, bool]:
    """Lineage anchor for comment triggers (agent-produced comments)."""
    return await _parent_run_for_artifact(
        session,
        workspace_id=workspace_id,
        artifact_type="comment",
        ref_table="comments",
        ref_id=comment_id,
    )


async def _parent_run_for_issue(
    session: AsyncSession, *, workspace_id: uuid.UUID, issue_id: uuid.UUID
) -> tuple[uuid.UUID | None, int, bool]:
    """Lineage anchor for issue triggers (autopilot-created issues).

    Closes the ``create_issue ↔ issue_created`` self-loop: the action
    records an ``issue`` artifact for every issue it creates, so issue
    triggers trace back to the creating run and ``cascade_depth``
    accumulates until the guardrail gate cuts the chain.
    """
    return await _parent_run_for_artifact(
        session,
        workspace_id=workspace_id,
        artifact_type="issue",
        ref_table="issues",
        ref_id=issue_id,
    )


def _matches_trigger_config(
    rule: Autopilot,
    *,
    trigger_type: str,
    issue: Issue | None,
    changes: dict[str, Any] | None,
    from_category: str | None,
    to_category: str | None,
    mentioned_agent_ids: list[uuid.UUID] | None,
) -> bool:
    """trigger_config scoping (§2.6 event trigger shape)."""
    config = rule.trigger_config or {}

    scope_projects = config.get("scope_project_ids")
    if scope_projects:
        project_str = str(issue.project_id) if issue is not None and issue.project_id else ""
        if project_str not in [str(pid) for pid in scope_projects]:
            return False

    if trigger_type == "issue_status_changed":
        to_status = [str(s) for s in (config.get("to_status") or [])]
        from_status = [str(s) for s in (config.get("from_status") or [])]
        candidates: set[str] = set()
        if to_category:
            candidates.add(to_category)
        if changes is not None:
            status_obj = changes.get("status")
            if isinstance(status_obj, dict) and status_obj.get("name"):
                candidates.add(str(status_obj["name"]))
        if to_status and not candidates.intersection(to_status):
            return False
        if from_status:
            # The internal issue.updated event carries no prior status —
            # only issue.moved supplies from_category.
            if from_category is None:
                return False
            if from_category not in from_status:
                return False

    if trigger_type == "issue_field_changed":
        watch_fields = [str(f) for f in (config.get("watch_fields") or [])]
        changed_keys = {key for key in (changes or {}) if not key.startswith("_")}
        if watch_fields and not changed_keys.intersection(watch_fields):
            return False

    if trigger_type == "agent_mentioned":
        target_agents = [str(a) for a in (config.get("target_agent_ids") or [])]
        if target_agents:
            if not mentioned_agent_ids:
                return False
            if not {str(agent_id) for agent_id in mentioned_agent_ids}.intersection(target_agents):
                return False
        elif not mentioned_agent_ids:
            return False

    return True


async def _mentioned_agent_ids(
    session: AsyncSession, *, workspace_id: uuid.UUID, data: dict[str, Any]
) -> list[uuid.UUID]:
    """Agent member ids mentioned by a comment event."""
    mention_ids: list[uuid.UUID] = []
    for mention in data.get("mentions") or []:
        if isinstance(mention, dict):
            member_id = _uuid_or_none(mention.get("id") or mention.get("member_id"))
            if member_id is not None:
                mention_ids.append(member_id)
    if not mention_ids:
        return []
    rows = (
        await session.execute(
            select(Member.agent_id).where(
                Member.workspace_id == workspace_id,
                Member.id.in_(mention_ids),
                Member.agent_id.is_not(None),
            )
        )
    ).all()
    return [row[0] for row in rows]


async def match_domain_event(session: AsyncSession, event: OutboxEvent) -> None:
    """Relay handler body: map one realtime.publish event to rule triggers.

    Runs in the relay's savepoint; every created run commits atomically
    with the outbox row's ``published`` mark (at-least-once + dedup window
    = effectively-once run creation, §5.1 crash-recovery acceptance).
    """
    payload = event.payload or {}
    event_name = str(payload.get("event") or "")
    if event_name not in _HANDLED_EVENTS:
        return
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return

    await set_tenant_context(session, event.workspace_id)

    # Fast path: no candidate rules in this workspace → do nothing.
    candidates_exist = await session.scalar(
        select(Autopilot.id)
        .where(
            Autopilot.workspace_id == event.workspace_id,
            Autopilot.trigger_type.in_(tuple(_MATCHER_TRIGGER_TYPES)),
            Autopilot.status == "active",
            Autopilot.deleted_at.is_(None),
        )
        .limit(1)
    )
    if candidates_exist is None:
        return

    # Issue id location differs per event shape: issue.created nests the
    # rendered issue under data.issue; issue.updated/moved carry data.id;
    # comment.created carries the comment's data.id but the ISSUE id under
    # data.issue_id.
    nested_issue = data.get("issue")
    if event_name == "comment.created":
        issue_id = _uuid_or_none(data.get("issue_id"))
    elif isinstance(nested_issue, dict):
        issue_id = _uuid_or_none(nested_issue.get("id"))
    else:
        issue_id = _uuid_or_none(data.get("id"))
    issue_context = None
    if issue_id is not None:
        issue_context = await _issue_filter_context(
            session, workspace_id=event.workspace_id, issue_id=issue_id
        )

    # Build the list of (trigger_type, rule, snapshot) to consider.
    occurrences: list[tuple[str, dict[str, Any], str | None, str | None]] = []
    if event_name == "issue.created":
        occurrences.append(("issue_created", {}, None, None))
    elif event_name == "issue.updated":
        changes = data.get("changes") or {}
        status_changed = any(key in changes for key in _STATUS_CHANGE_KEYS)
        if status_changed:
            occurrences.append(("issue_status_changed", changes, None, None))
        field_keys = {
            key
            for key in changes
            if not key.startswith("_")
            and key not in _STATUS_CHANGE_KEYS
            and key not in _RENDERED_EXTRA_KEYS
        }
        if field_keys:
            occurrences.append(("issue_field_changed", changes, None, None))
    elif event_name == "issue.moved":
        occurrences.append(
            (
                "issue_status_changed",
                {},
                str((data.get("from") or {}).get("state_category") or ""),
                str((data.get("to") or {}).get("state_category") or ""),
            )
        )
    elif event_name == "comment.created":
        occurrences.append(("comment_created", {}, None, None))
        agent_ids = await _mentioned_agent_ids(session, workspace_id=event.workspace_id, data=data)
        if agent_ids:
            occurrences.append(("agent_mentioned", {"_mentioned": agent_ids}, None, None))

    for trigger_type, changes, from_category, to_category in occurrences:
        rules = await _candidate_rules(
            session, workspace_id=event.workspace_id, trigger_types=frozenset({trigger_type})
        )
        if not rules:
            continue
        mentioned = changes.get("_mentioned") if isinstance(changes, dict) else None
        for rule in rules:
            issue = issue_context["issue"] if issue_context else None
            if not _matches_trigger_config(
                rule,
                trigger_type=trigger_type,
                issue=issue,
                changes=changes if trigger_type != "agent_mentioned" else None,
                from_category=from_category,
                to_category=to_category,
                mentioned_agent_ids=mentioned,
            ):
                continue
            context = dict(issue_context or {})
            if trigger_type == "comment_created" or trigger_type == "agent_mentioned":
                context["body"] = str(data.get("body_text") or data.get("body_markdown") or "")
                context["title"] = context.get("title") or ""
                author = data.get("author") or {}
                context["actor_id"] = str(author.get("id") or "")
            if not match_filter_config(rule.filter_config, context):
                continue

            # Cascade lineage for agent-produced comments AND autopilot-
            # created issues — both anchor the loop cut (§5.3): the depth
            # accumulates along the artifact chain until cascade_max_depth
            # refuses the downstream run.
            parent_run_id: uuid.UUID | None = None
            cascade_depth = 0
            comment_id = _uuid_or_none(data.get("id"))
            loop_target = ""
            if trigger_type in ("comment_created", "agent_mentioned") and comment_id:
                parent_run_id, cascade_depth, _produced = await _parent_run_for_comment(
                    session, workspace_id=event.workspace_id, comment_id=comment_id
                )
                loop_target = str(comment_id)
            elif issue_id is not None:
                parent_run_id, cascade_depth, _produced = await _parent_run_for_issue(
                    session, workspace_id=event.workspace_id, issue_id=issue_id
                )
                loop_target = str(issue_id)

            dedup_key = f"{event.id}:{rule.id}"
            decision = await evaluate_trigger(
                session,
                rule=rule,
                dedup_key=dedup_key,
                trigger_target_ref=loop_target,
                cascade_depth=cascade_depth,
            )
            if not decision.allowed:
                continue

            snapshot = _trigger_snapshot(
                event=event,
                event_name=event_name,
                data=data,
                trigger_type=trigger_type,
                issue_context=issue_context,
                dedup_key=dedup_key,
                loop_target=loop_target,
            )
            await runs_mod.create_run(
                session,
                rule=rule,
                trigger_snapshot=snapshot,
                parent_run_id=parent_run_id,
                cascade_depth=cascade_depth,
            )


def _trigger_snapshot(
    *,
    event: OutboxEvent,
    event_name: str,
    data: dict[str, Any],
    trigger_type: str,
    issue_context: dict[str, Any] | None,
    dedup_key: str,
    loop_target: str,
) -> dict[str, Any]:
    """The replayable trigger input (§5.1: 触发快照可回溯可重放)."""
    snapshot: dict[str, Any] = {
        "event_id": str(event.id),
        "event": event_name,
        "trigger_type": trigger_type,
        "dedup_key": dedup_key,
        "loop_target": loop_target,
    }
    if issue_context is not None:
        issue = issue_context["issue"]
        snapshot["issue"] = {
            "id": str(issue.id),
            "title": issue.title,
            "priority": issue.priority,
            "project_id": str(issue.project_id) if issue.project_id else None,
            "labels": issue_context.get("labels") or [],
        }
    if trigger_type in ("comment_created", "agent_mentioned"):
        author = data.get("author") or {}
        snapshot["comment"] = {
            "id": str(data.get("id") or ""),
            "body": str(data.get("body_text") or data.get("body_markdown") or ""),
        }
        snapshot["actor"] = {
            "id": str(author.get("id") or ""),
            "name": str(author.get("name") or ""),
        }
    if data.get("changes"):
        snapshot["changes"] = {
            key: value for key, value in data["changes"].items() if not key.startswith("_")
        }
    return snapshot


async def realtime_publish_with_autopilot(projector_handler):
    """Build the chained relay handler: project first, then match triggers.

    The projector's return value (Redis fan-out frames) is preserved; the
    matcher adds nothing to fan-out (its realtime events are outbox rows
    the projector picks up on a later pass).
    """

    async def handler(session: AsyncSession, event: OutboxEvent):
        frames = await projector_handler(session, event)
        try:
            await match_domain_event(session, event)
        except Exception:  # noqa: BLE001 — trigger matching must not break projection
            logger.exception("autopilot event matching failed for %s", event.id)
        return frames

    return handler


__all__ = ["match_domain_event", "realtime_publish_with_autopilot"]
