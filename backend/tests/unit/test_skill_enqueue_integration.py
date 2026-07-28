"""§6.11 / §4.5 enqueue integration — handler-level (M8).

Drives the REAL ``assign_orchestration_handler`` with the skill module's
resolvers registered, and asserts that:

* the matched skill's instructions are injected into ``task_spec`` and the
  bound version is frozen into ``config_snapshot.skill_versions`` (+ the
  injection list into ``config_snapshot.injected_skills``) — the §6.11
  freeze verified through the handler, not by calling the hook directly;
* a definition-level disabled skill (``skills.status='disabled'``) is NOT
  injected / frozen (the three-tier kill switch's definition tier, the spec's
  "头号约束").
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from mesh.agent.triggers import (
    ENQUEUE_EVENT_TYPE,
    assign_orchestration_handler,
    register_skill_context_resolver,
    register_skill_matching_resolver,
)
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.skill import (
    AgentSkill,
    Skill,
    SkillInstallation,
    SkillSource,
    SkillTrigger,
    SkillVersion,
)
from mesh.db.models.user import User
from mesh.skill.resolvers import build_enqueue_context, make_matching_resolver


async def _seed_world(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Skill Enq", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
        await session.flush()
        user = User(
            email=f"{uuid.uuid4().hex[:10]}@corp.com", password_hash="x", display_name="Owner"
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id, role="owner"
        )
        session.add(member)
        await session.flush()
        agent = Agent(workspace_id=workspace.id, name="Bot", owner_user_id=user.id)
        session.add(agent)
        await session.flush()
        session.add(
            Member(
                workspace_id=workspace.id, member_type="agent", agent_id=agent.id,
                role="member", status="active",
            )
        )
    return workspace, member, agent


async def _seed_skill_binding(
    session_factory, workspace, member, agent, *, skill_status: str = "published",
    pattern: str = "deploy",
) -> dict:
    async with session_factory() as session, session.begin():
        source = SkillSource(
            workspace_id=workspace.id, source_type="user", name="u", trust_level="reviewed"
        )
        session.add(source)
        await session.flush()
        skill = Skill(
            workspace_id=workspace.id, source_id=source.id, name="Deploy SOP",
            slug=f"deploy-{uuid.uuid4().hex[:6]}", summary="deploy sop",
            status=skill_status, required_capabilities=["exec:shell"],
            created_by=member.id,
        )
        session.add(skill)
        await session.flush()
        version = SkillVersion(
            workspace_id=workspace.id, skill_id=skill.id, version="1.0.0",
            instructions="# Deploy SOP\nrun checks", status="published",
            required_capabilities=["exec:shell"], content_hash="a" * 64,
            created_by=member.id,
        )
        session.add(version)
        await session.flush()
        skill.current_version_id = version.id
        session.add(
            SkillTrigger(
                skill_version_id=version.id, trigger_type="keyword",
                pattern=pattern, weight=1.0,
            )
        )
        installation = SkillInstallation(
            workspace_id=workspace.id, skill_id=skill.id, skill_version_id=version.id,
            installed_by=member.id, granted_capabilities=["exec:shell"],
        )
        session.add(installation)
        await session.flush()
        session.add(
            AgentSkill(
                workspace_id=workspace.id, agent_id=agent.id, skill_id=skill.id,
                skill_installation_id=installation.id, skill_version_id=version.id,
                enabled=True, auto_trigger=True, priority=100,
            )
        )
    return {"skill_id": skill.id, "version_id": version.id}


async def _make_issue(session_factory, workspace, member, title: str):
    from mesh.issue.schemas import CreateIssueRequest
    from mesh.issue.service import IssueService

    return await IssueService(session_factory).create_issue(
        actor=member, workspace_id=workspace.id, body=CreateIssueRequest(title=title)
    )


async def _run(session_factory, workspace, issue, agent):
    async with session_factory() as session, session.begin():
        event = OutboxEvent(
            workspace_id=workspace.id,
            event_type="issue.assigned",
            payload={
                "issue_id": issue["id"],
                "agent_id": str(agent.id),
                "agent_member_id": str(agent.id),
                "trigger": "assign",
                "action": "enqueue",
                "trigger_event_id": str(uuid.uuid4()),
            },
        )
        session.add(event)
        await session.flush()
        event_id = event.id
    async with session_factory() as session, session.begin():
        row = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.id == event_id)
        )
        await assign_orchestration_handler(session, row)


def _register():
    register_skill_context_resolver(build_enqueue_context)
    register_skill_matching_resolver(make_matching_resolver())


async def _enqueue_payload(session_factory, workspace):
    async with session_factory() as session:
        row = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.workspace_id == workspace.id,
                    OutboxEvent.event_type == ENQUEUE_EVENT_TYPE,
                )
            )
        ).scalar()
    return row.payload


async def test_handler_freezes_bound_version_and_injects_instructions(
    session_factory,
) -> None:
    _register()
    workspace, member, agent = await _seed_world(session_factory)
    seeded = await _seed_skill_binding(session_factory, workspace, member, agent)
    issue = await _make_issue(session_factory, workspace, member, "please deploy now")

    await _run(session_factory, workspace, issue, agent)
    payload = await _enqueue_payload(session_factory, workspace)

    # §6.11 freeze: the bound version is in the snapshot.
    assert payload["config_snapshot"]["skill_versions"] == {
        str(seeded["skill_id"]): str(seeded["version_id"])
    }
    # §4.5 step 5: injection list persisted for audit.
    injected = payload["config_snapshot"]["injected_skills"]
    assert len(injected) == 1
    assert injected[0]["skill_id"] == str(seeded["skill_id"])
    assert "keyword:deploy" in injected[0]["matched_by"]
    # §4.5: matched SOP injected into the (trusted) task_spec, separate from
    # the §6.15 untrusted issue context.
    assert "Deploy SOP" in payload["task_spec"]["skill_instructions"]
    assert payload["task_spec"]["injected_skills"][0]["skill_id"] == str(seeded["skill_id"])


async def test_definition_disabled_skill_not_injected(session_factory) -> None:
    _register()
    workspace, member, agent = await _seed_world(session_factory)
    await _seed_skill_binding(
        session_factory, workspace, member, agent, skill_status="disabled"
    )
    issue = await _make_issue(session_factory, workspace, member, "please deploy now")

    await _run(session_factory, workspace, issue, agent)
    payload = await _enqueue_payload(session_factory, workspace)

    # definition-tier kill switch: nothing frozen, nothing injected.
    assert payload["config_snapshot"]["skill_versions"] == {}
    assert payload["config_snapshot"].get("injected_skills", []) == []
    assert "skill_instructions" not in payload["task_spec"]


async def test_malformed_grant_does_not_crash_handler(session_factory) -> None:
    """HIGH-2: a poisoned granted_capabilities must NOT stall the handler."""
    _register()
    workspace, member, agent = await _seed_world(session_factory)
    seeded = await _seed_skill_binding(session_factory, workspace, member, agent)
    # Poison the installation's grants with a malformed entry.
    from mesh.db.models.skill import SkillInstallation

    async with session_factory() as session, session.begin():
        inst = (
            await session.execute(
                select(SkillInstallation).where(
                    SkillInstallation.skill_id == seeded["skill_id"]
                )
            )
        ).scalar()
        inst.granted_capabilities = [{"capability": "exec:shell", "permission": "bogus"}]
    issue = await _make_issue(session_factory, workspace, member, "please deploy now")
    # Must NOT raise (degrade path); enqueue still written.
    await _run(session_factory, workspace, issue, agent)
    payload = await _enqueue_payload(session_factory, workspace)
    assert payload["intent"] == "enqueue"
