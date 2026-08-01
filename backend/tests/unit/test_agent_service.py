"""Agent service unit tests — real PostgreSQL, nothing mocked (agent.md §3/§4).

Covers creation atomicity (agents + members + first config version),
model_config validation (§2.4), configuration versioning + rollback
(§2.7), the §4.8 lifecycle state machine (incl. 409 on illegal
transitions and members.status linkage), visibility (§3.5), ownership
transfer, soft delete, and the realtime/audit side effects.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.agent.service import AgentProfilePatch, AgentService
from mesh.db.models.agent import Agent, AgentConfigVersion
from mesh.db.models.audit import AuditLog
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)


@pytest.fixture
def agent_service(session_factory) -> AgentService:
    return AgentService(session_factory)


async def _make_workspace(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Agent WS", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _make_member(session_factory, workspace, *, role="member", name="Person") -> Member:
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name=name
        )
        session.add(user)
        await session.flush()
        member = Member(workspace_id=workspace.id, member_type="human", user_id=user.id, role=role)
        session.add(member)
    return member


async def _events(session_factory, name: str) -> list:
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEvent))).scalars().all()
    return [
        e for e in rows if e.event_type == "realtime.publish" and e.payload["event"] == name
    ]


async def _audits(session_factory, action: str) -> list:
    async with session_factory() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    return [a for a in rows if a.action == action]


async def _create_agent(service, actor, workspace, **overrides):
    defaults = {
        "name": "小测",
        "role_tag": "测试工程师",
        "bio": "回归测试",
        "visibility": "workspace",
        "system_instructions": "你是测试工程师。",
        "model_config": {"model_tier": "balanced", "temperature": 0.2},
    }
    defaults.update(overrides)
    return await service.create_agent(actor=actor, workspace_id=workspace.id, **defaults)


# --- creation (§5.1 atomicity) -------------------------------------------------


@pytest.mark.unit
async def test_create_agent_writes_agents_member_version_atomically(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")

    created = await _create_agent(agent_service, owner, workspace)

    agent_id = uuid.UUID(created["id"])
    async with session_factory() as session:
        agent = await session.get(Agent, agent_id)
        member = await session.scalar(
            select(Member).where(Member.workspace_id == workspace.id, Member.agent_id == agent_id)
        )
        versions = (
            (
                await session.execute(
                    select(AgentConfigVersion).where(
                        AgentConfigVersion.workspace_id == workspace.id,
                        AgentConfigVersion.agent_id == agent_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    # agents row
    assert agent.name == "小测"
    assert agent.lifecycle_status == "active"
    assert agent.owner_user_id == owner.user_id
    assert agent.active_config_version_id is not None
    # members row — the single roster (§6.1)
    assert member is not None
    assert member.member_type == "agent"
    assert member.role == "member"
    assert member.status == "active"
    # first immutable config version; the active pointer targets it
    assert len(versions) == 1
    assert versions[0].id == agent.active_config_version_id
    assert versions[0].snapshot["model_config"]["temperature"] == 0.2
    assert versions[0].changed_by == owner.id
    # response shape (§3.2)
    assert created["member"]["id"] == str(member.id)
    assert created["member"]["display_name"] == "小测"
    assert created["lifecycle_status"] == "active"
    assert created["badge_kind"] == "ai"


@pytest.mark.unit
async def test_create_agent_emits_agent_created_and_member_added(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="admin")
    created = await _create_agent(agent_service, owner, workspace)

    agent_created = await _events(session_factory, "agent.created")
    member_added = await _events(session_factory, "member.added")
    assert [e for e in agent_created if e.payload["data"]["id"] == created["id"]]
    agent_channel_events = [
        e for e in agent_created
        if e.payload["channel"] == f"workspace:{workspace.id}:agents"
    ]
    assert agent_channel_events
    assert [
        e for e in member_added
        if e.payload["data"]["member_id"] == created["member"]["id"]
        and e.payload["data"]["member_type"] == "agent"
    ]
    assert len(await _audits(session_factory, "agent.created")) == 1


@pytest.mark.unit
async def test_private_agent_events_keep_detail_payloads_on_the_protected_resource_channel(
    session_factory, agent_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(
        agent_service,
        owner,
        workspace,
        visibility="private",
        system_instructions="private instructions",
        model_config={"model": "private-model", "temperature": 0.2},
    )
    agent_id = uuid.UUID(created["id"])

    updated = await agent_service.update_config(
        actor=owner,
        workspace_id=workspace.id,
        agent_id=agent_id,
        system_instructions="rotated private instructions",
    )
    await agent_service.rollback_config(
        actor=owner,
        workspace_id=workspace.id,
        agent_id=agent_id,
        version_id=uuid.UUID(created["active_config_version_id"]),
    )
    await agent_service.delete_agent(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id
    )

    detail_events = [
        *(await _events(session_factory, "agent.created")),
        *(await _events(session_factory, "agent.updated")),
    ]
    own_events = [event for event in detail_events if event.payload["data"]["id"] == created["id"]]
    assert len(own_events) == 3
    assert all(event.payload["channel"] == f"agent:{agent_id}" for event in own_events)
    assert not [
        event
        for event in own_events
        if event.payload["channel"] == f"workspace:{workspace.id}:agents"
    ]
    deleted = [
        event
        for event in await _events(session_factory, "agent.deleted")
        if event.payload["data"]["id"] == created["id"]
    ]
    assert len(deleted) == 1
    assert deleted[0].payload["channel"] == f"agent:{agent_id}"
    assert deleted[0].payload["data"]["visibility"] == "private"
    assert deleted[0].payload["data"]["updated_at"]

    private_roster_events = [
        *(await _events(session_factory, "member.added")),
        *(await _events(session_factory, "member.removed")),
    ]
    private_roster_events = [
        event
        for event in private_roster_events
        if event.payload["data"]["member_id"] == created["member"]["id"]
    ]
    assert len(private_roster_events) == 2
    assert all(
        event.payload["channel"] == f"agent:{agent_id}"
        for event in private_roster_events
    )
    assert updated["system_instructions"] == "rotated private instructions"


@pytest.mark.unit
async def test_create_agent_is_member_self_service_but_not_guest(session_factory, agent_service):
    """§4.4/§4.5/F7: any non-guest member may create (becoming owner); guests read-only."""
    workspace = await _make_workspace(session_factory)
    plain = await _make_member(session_factory, workspace, role="member")
    # A plain member creates and becomes the owner.
    created = await _create_agent(agent_service, plain, workspace, name="自建")
    async with session_factory() as session:
        agent = await session.get(Agent, uuid.UUID(created["id"]))
    assert agent.owner_user_id == plain.user_id

    # A guest is denied.
    guest = await _make_member(session_factory, workspace, role="guest")
    with pytest.raises(ForbiddenError):
        await _create_agent(agent_service, guest, workspace)


@pytest.mark.unit
async def test_create_agent_rejects_out_of_range_temperature(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    with pytest.raises(BusinessRuleError) as excinfo:
        await _create_agent(
            agent_service, owner, workspace, model_config={"temperature": 2.5}
        )
    assert excinfo.value.code == "validation_error"
    fields = excinfo.value.details["fields"]
    assert {"field": "model_config.temperature", "issue": "out_of_range"} in fields


@pytest.mark.unit
async def test_create_agent_rejects_invalid_enums_and_types(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    with pytest.raises(BusinessRuleError) as excinfo:
        await _create_agent(
            agent_service,
            owner,
            workspace,
            model_config={
                "model_tier": "giga",
                "reasoning_effort": "extreme",
                "max_tokens": 0,
                "top_p": 1.5,
            },
        )
    issues = {f["field"] for f in excinfo.value.details["fields"]}
    assert issues == {
        "model_config.model_tier",
        "model_config.reasoning_effort",
        "model_config.max_tokens",
        "model_config.top_p",
    }


@pytest.mark.unit
async def test_create_agent_avatar_url_must_be_https(session_factory, agent_service):
    """§6.16 https-only; §3.4 maps this business check to 422 (M-F4), not 400."""
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    for bad in ("javascript:alert(1)", "data:text/html,x", "http://insecure.example/a.png"):
        with pytest.raises(BusinessRuleError) as excinfo:
            await _create_agent(agent_service, owner, workspace, avatar_url=bad)
        assert excinfo.value.code == "validation_error"
        assert excinfo.value.details["fields"] == [
            {"field": "avatar_url", "issue": "invalid_scheme"}
        ]


# --- read / visibility (§3.5) ----------------------------------------------------


@pytest.mark.unit
async def test_private_agent_hidden_from_plain_member(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    plain = await _make_member(session_factory, workspace, role="member")
    created = await _create_agent(agent_service, owner, workspace, visibility="private")
    agent_id = uuid.UUID(created["id"])

    # Owner sees it…
    detail = await agent_service.get_agent(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id
    )
    assert detail["visibility"] == "private"
    # …a plain member gets the same 404 as a missing agent (§5.3 no leak).
    with pytest.raises(NotFoundError):
        await agent_service.get_agent(actor=plain, workspace_id=workspace.id, agent_id=agent_id)
    # And it is absent from their list projection.
    items, _ = await agent_service.list_agents(actor=plain, workspace_id=workspace.id)
    assert all(item["id"] != created["id"] for item in items)
    # Admins see private agents too.
    admin = await _make_member(session_factory, workspace, role="admin")
    admin_items, _ = await agent_service.list_agents(actor=admin, workspace_id=workspace.id)
    assert any(item["id"] == created["id"] for item in admin_items)


@pytest.mark.unit
async def test_explicit_private_filter_cannot_enumerate_others_private(
    session_factory, agent_service
):
    """C2 regression: ``visibility='private'`` must NOT bypass the §3.5 gate.

    The original implementation applied the non-admin owner restriction in an
    ``elif`` branch, so an explicit ``visibility != 'all'`` filter skipped it
    and let any member enumerate everyone's private agents. The gate must
    apply to every branch of the filter (§3.5 / §5.1).
    """
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    alice = await _make_member(session_factory, workspace, role="member")
    bob = await _make_member(session_factory, workspace, role="member")
    owner_priv = await _create_agent(
        agent_service, owner, workspace, visibility="private", name="OwnerPriv"
    )
    alice_priv = await _create_agent(
        agent_service, alice, workspace, visibility="private", name="AlicePriv"
    )

    # Bob sees nobody's private agents through the explicit private filter.
    bob_items, _ = await agent_service.list_agents(
        actor=bob, workspace_id=workspace.id, visibility="private"
    )
    bob_ids = {i["id"] for i in bob_items}
    assert owner_priv["id"] not in bob_ids
    assert alice_priv["id"] not in bob_ids

    # Alice sees ONLY her own private agent, never the owner's.
    alice_items, _ = await agent_service.list_agents(
        actor=alice, workspace_id=workspace.id, visibility="private"
    )
    assert {i["id"] for i in alice_items} == {alice_priv["id"]}

    # Admins (owner role satisfies agent:manage) still see them all.
    admin_items, _ = await agent_service.list_agents(
        actor=owner, workspace_id=workspace.id, visibility="private"
    )
    assert {owner_priv["id"], alice_priv["id"]} <= {i["id"] for i in admin_items}


@pytest.mark.unit
async def test_list_agents_filters_and_search(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    a1 = await _create_agent(agent_service, owner, workspace, name="小测", role_tag="测试")
    await _create_agent(agent_service, owner, workspace, name="文档助手", role_tag="文档")
    agent_id = uuid.UUID(a1["id"])
    await agent_service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="pause"
    )

    active_items, _ = await agent_service.list_agents(
        actor=owner, workspace_id=workspace.id, status="active"
    )
    assert {i["lifecycle_status"] for i in active_items} == {"active"}
    paused_items, _ = await agent_service.list_agents(
        actor=owner, workspace_id=workspace.id, status="paused"
    )
    assert [i["id"] for i in paused_items] == [a1["id"]]

    searched, _ = await agent_service.list_agents(actor=owner, workspace_id=workspace.id, q="文档")
    assert [i["name"] for i in searched] == ["文档助手"]

    by_owner, _ = await agent_service.list_agents(
        actor=owner, workspace_id=workspace.id, owner_id=owner.user_id
    )
    assert len(by_owner) == 2


@pytest.mark.unit
async def test_list_agents_pagination_walks_all_once(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created_ids = []
    for i in range(5):
        created = await _create_agent(agent_service, owner, workspace, name=f"Agent {i}")
        created_ids.append(created["id"])

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        items, cursor = await agent_service.list_agents(
            actor=owner, workspace_id=workspace.id, limit=2, cursor=cursor
        )
        seen.extend(i["id"] for i in items)
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 5


@pytest.mark.unit
async def test_list_agents_invalid_cursor_rejected(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    with pytest.raises(ValidationError) as excinfo:
        await agent_service.list_agents(
            actor=owner, workspace_id=workspace.id, cursor="not-a-cursor"
        )
    assert excinfo.value.code == "invalid_cursor"


# --- profile update ---------------------------------------------------------------


@pytest.mark.unit
async def test_update_agent_profile_emits_event(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace)
    agent_id = uuid.UUID(created["id"])

    updated = await agent_service.update_agent(
        actor=owner,
        workspace_id=workspace.id,
        agent_id=agent_id,
        patch=AgentProfilePatch(name="小测Pro", trigger_on_assign=False),
    )
    assert updated["name"] == "小测Pro"
    assert updated["trigger_on_assign"] is False
    assert [
        e for e in await _events(session_factory, "agent.updated")
        if e.payload["data"]["id"] == created["id"]
    ]


@pytest.mark.unit
async def test_update_agent_noop_diff_emits_nothing(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace)
    agent_id = uuid.UUID(created["id"])
    before = len(await _events(session_factory, "agent.updated"))

    # Same values → empty diff → no event (§6.9 no-op semantics).
    await agent_service.update_agent(
        actor=owner,
        workspace_id=workspace.id,
        agent_id=agent_id,
        patch=AgentProfilePatch(name="小测"),
    )
    assert len(await _events(session_factory, "agent.updated")) == before


@pytest.mark.unit
async def test_update_agent_avatar_scheme_validated(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace)
    with pytest.raises(BusinessRuleError) as excinfo:
        await agent_service.update_agent(
            actor=owner,
            workspace_id=workspace.id,
            agent_id=uuid.UUID(created["id"]),
            patch=AgentProfilePatch(avatar_url="javascript:alert(1)"),
        )
    assert excinfo.value.code == "validation_error"  # M-F4: 422, not 400


# --- configuration versions (§2.4 / §2.7) -----------------------------------------


@pytest.mark.unit
async def test_update_config_mints_immutable_version_and_merges(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(
        agent_service, owner, workspace,
        model_config={"model_tier": "balanced", "temperature": 0.2, "max_tokens": 8192},
    )
    agent_id = uuid.UUID(created["id"])
    first_version_id = created["active_config_version_id"]

    updated = await agent_service.update_config(
        actor=owner,
        workspace_id=workspace.id,
        agent_id=agent_id,
        model_config={"temperature": 0.7, "reasoning_effort": "high"},
        system_instructions="更新后的岗位说明书",
    )
    new_version_id = updated["active_config_version_id"]
    assert new_version_id != first_version_id
    # Partial merge: untouched keys survive.
    assert updated["model_config"]["temperature"] == 0.7
    assert updated["model_config"]["max_tokens"] == 8192
    assert updated["model_config"]["reasoning_effort"] == "high"
    assert updated["system_instructions"] == "更新后的岗位说明书"

    async with session_factory() as session:
        versions = (
            (
                await session.execute(
                    select(AgentConfigVersion).where(
                        AgentConfigVersion.agent_id == agent_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(versions) == 2
    # Immutable: the first snapshot is untouched.
    first = next(v for v in versions if str(v.id) == first_version_id)
    assert first.snapshot["model_config"]["temperature"] == 0.2


@pytest.mark.unit
async def test_update_config_rejects_out_of_range(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace)
    with pytest.raises(BusinessRuleError) as excinfo:
        await agent_service.update_config(
            actor=owner,
            workspace_id=workspace.id,
            agent_id=uuid.UUID(created["id"]),
            model_config={"temperature": 3},
        )
    assert excinfo.value.code == "validation_error"


@pytest.mark.unit
async def test_update_config_requires_a_change(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace)
    with pytest.raises(ValidationError):
        await agent_service.update_config(
            actor=owner, workspace_id=workspace.id, agent_id=uuid.UUID(created["id"])
        )


@pytest.mark.unit
async def test_config_version_history_and_rollback(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(
        agent_service, owner, workspace, system_instructions="v1 instructions"
    )
    agent_id = uuid.UUID(created["id"])
    v1 = created["active_config_version_id"]
    updated = await agent_service.update_config(
        actor=owner,
        workspace_id=workspace.id,
        agent_id=agent_id,
        system_instructions="v2 instructions",
    )
    assert updated["system_instructions"] == "v2 instructions"

    history, cursor = await agent_service.list_config_versions(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id
    )
    assert cursor is None
    assert [h["change_summary"] for h in history] == [
        "configuration updated",
        "initial configuration",
    ]  # newest first

    # Rollback = COPY v1 snapshot into a NEW version (immutable history).
    rolled = await agent_service.rollback_config(
        actor=owner,
        workspace_id=workspace.id,
        agent_id=agent_id,
        version_id=uuid.UUID(v1),
    )
    assert rolled["system_instructions"] == "v1 instructions"
    assert rolled["active_config_version_id"] not in (v1, updated["active_config_version_id"])
    history2, _ = await agent_service.list_config_versions(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id
    )
    assert len(history2) == 3
    assert history2[0]["change_summary"].startswith("rollback to version")
    assert [
        e for e in await _audits(session_factory, "agent.config_rollback")
    ]


@pytest.mark.unit
async def test_rollback_to_other_agents_version_not_found(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    a1 = await _create_agent(agent_service, owner, workspace, name="A1")
    a2 = await _create_agent(agent_service, owner, workspace, name="A2")
    a2_version = a2["active_config_version_id"]
    # Service-level same-agent check (DB overlap FK is the backstop, T27).
    with pytest.raises(NotFoundError):
        await agent_service.rollback_config(
            actor=owner,
            workspace_id=workspace.id,
            agent_id=uuid.UUID(a1["id"]),
            version_id=uuid.UUID(a2_version),
        )


# --- lifecycle state machine (§4.8) ------------------------------------------------


@pytest.mark.unit
async def test_full_lifecycle_machine(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace)
    agent_id = uuid.UUID(created["id"])

    # active → paused
    paused = await agent_service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id,
        action="pause", reason="维护", in_flight_policy="cancel_current",
    )
    assert paused["lifecycle_status"] == "paused"
    assert paused["previous_lifecycle_status"] == "active"
    assert paused["affected_executions"] == 0
    # paused → active
    resumed = await agent_service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="resume"
    )
    assert resumed["lifecycle_status"] == "active"
    # active → disabled (members.status linkage)
    await agent_service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="disable"
    )
    async with session_factory() as session:
        member = await session.scalar(
            select(Member).where(Member.workspace_id == workspace.id, Member.agent_id == agent_id)
        )
        assert member.status == "disabled"
        assert member.disabled_at is not None
    # disabled → active (enable restores roster status)
    await agent_service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="enable"
    )
    async with session_factory() as session:
        member = await session.scalar(
            select(Member).where(Member.workspace_id == workspace.id, Member.agent_id == agent_id)
        )
        assert member.status == "active"
    # active → archived → active (restore)
    await agent_service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="archive"
    )
    restored = await agent_service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="restore"
    )
    assert restored["lifecycle_status"] == "active"

    events = await _events(session_factory, "agent.lifecycle_changed")
    assert len(events) == 6
    first = events[0].payload["data"]
    assert first["from"] == "active" and first["to"] == "paused"
    assert first["in_flight_policy"] == "cancel_current"


@pytest.mark.unit
async def test_illegal_lifecycle_transition_conflicts(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace)
    agent_id = uuid.UUID(created["id"])
    await agent_service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="archive"
    )
    # archived → pause is not in the §4.8 machine.
    with pytest.raises(ConflictError) as excinfo:
        await agent_service.transition_lifecycle(
            actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="pause"
        )
    assert excinfo.value.code == "conflict"  # §3.4: illegal transition = 409 conflict (L1)
    assert excinfo.value.details["from"] == "archived"
    # resume from active is equally illegal.
    await agent_service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="restore"
    )
    with pytest.raises(ConflictError):
        await agent_service.transition_lifecycle(
            actor=owner, workspace_id=workspace.id, agent_id=agent_id, action="resume"
        )


# --- soft delete ---------------------------------------------------------------------


@pytest.mark.unit
async def test_soft_delete_hides_agent_and_removes_roster(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace)
    agent_id = uuid.UUID(created["id"])

    await agent_service.delete_agent(actor=owner, workspace_id=workspace.id, agent_id=agent_id)

    with pytest.raises(NotFoundError):
        await agent_service.get_agent(actor=owner, workspace_id=workspace.id, agent_id=agent_id)
    async with session_factory() as session:
        agent = await session.get(Agent, agent_id)
        assert agent.deleted_at is not None
        member = await session.scalar(
            select(Member).where(Member.workspace_id == workspace.id, Member.agent_id == agent_id)
        )
        assert member.status == "removed"
    items, _ = await agent_service.list_agents(actor=owner, workspace_id=workspace.id)
    assert items == []
    assert [
        e for e in await _events(session_factory, "agent.deleted")
        if e.payload["data"]["id"] == created["id"]
    ]
    assert await _events(session_factory, "member.removed")


# --- ownership transfer ----------------------------------------------------------------


@pytest.mark.unit
async def test_transfer_ownership_to_active_human_member(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    other = await _make_member(session_factory, workspace, role="member")
    created = await _create_agent(agent_service, owner, workspace)
    agent_id = uuid.UUID(created["id"])

    updated = await agent_service.transfer_ownership(
        actor=owner,
        workspace_id=workspace.id,
        agent_id=agent_id,
        new_owner_user_id=other.user_id,
    )
    assert updated["owner_user_id"] == str(other.user_id)
    assert await _audits(session_factory, "agent.transferred")


@pytest.mark.unit
async def test_transfer_rejects_non_member_and_unauthorized(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    plain = await _make_member(session_factory, workspace, role="member")
    created = await _create_agent(agent_service, owner, workspace)
    agent_id = uuid.UUID(created["id"])

    # A plain member (not the owner, not admin) cannot transfer.
    with pytest.raises(ForbiddenError):
        await agent_service.transfer_ownership(
            actor=plain,
            workspace_id=workspace.id,
            agent_id=agent_id,
            new_owner_user_id=plain.user_id,
        )
    # A user who is not a member of THIS workspace is an invalid target.
    async with session_factory() as session, session.begin():
        stranger = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name="Out"
        )
        session.add(stranger)
    with pytest.raises(BusinessRuleError) as excinfo:
        await agent_service.transfer_ownership(
            actor=owner,
            workspace_id=workspace.id,
            agent_id=agent_id,
            new_owner_user_id=stranger.id,
        )
    assert excinfo.value.code == "transfer_target_invalid"


@pytest.mark.unit
async def test_agent_not_found_paths(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    missing = uuid.uuid4()
    with pytest.raises(NotFoundError):
        await agent_service.get_agent(actor=owner, workspace_id=workspace.id, agent_id=missing)
    with pytest.raises(NotFoundError):
        await agent_service.delete_agent(
            actor=owner, workspace_id=workspace.id, agent_id=missing
        )
    with pytest.raises(NotFoundError):
        await agent_service.update_config(
            actor=owner, workspace_id=workspace.id, agent_id=missing,
            system_instructions="x",
        )


@pytest.mark.unit
async def test_update_agent_unset_fields_keep_values(session_factory, agent_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace, role_tag="测试", slug="xiaoce")
    updated = await agent_service.update_agent(
        actor=owner,
        workspace_id=workspace.id,
        agent_id=uuid.UUID(created["id"]),
        patch=AgentProfilePatch(name="新名字"),  # everything else UNSET
    )
    assert updated["name"] == "新名字"
    assert updated["role_tag"] == "测试"  # kept


# --- M1: owner-member manage / non-owner denial (§3.5) ----------------------------


@pytest.mark.unit
async def test_owner_member_manages_own_agent_other_member_denied(
    session_factory, agent_service
):
    workspace = await _make_workspace(session_factory)
    alice = await _make_member(session_factory, workspace, role="member")
    bob = await _make_member(session_factory, workspace, role="member")
    created = await _create_agent(agent_service, alice, workspace, name="AliceBot")
    agent_id = uuid.UUID(created["id"])

    # Alice (member role, the owner) can update + transition her own agent.
    await agent_service.update_config(
        actor=alice,
        workspace_id=workspace.id,
        agent_id=agent_id,
        system_instructions="Alice 改的",
    )
    paused = await agent_service.transition_lifecycle(
        actor=alice, workspace_id=workspace.id, agent_id=agent_id, action="pause"
    )
    assert paused["lifecycle_status"] == "paused"

    # Bob (member, not owner, not admin) is denied.
    with pytest.raises(ForbiddenError):
        await agent_service.update_config(
            actor=bob,
            workspace_id=workspace.id,
            agent_id=agent_id,
            system_instructions="Bob 想改",
        )
    with pytest.raises(ForbiddenError):
        await agent_service.delete_agent(
            actor=bob, workspace_id=workspace.id, agent_id=agent_id
        )


@pytest.mark.unit
async def test_display_override_resolves_in_agent_render(session_factory, agent_service):
    """M2 (§2.1/§6.1): members.display_override wins over agents.name."""
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await _create_agent(agent_service, owner, workspace, name="原名")
    agent_id = uuid.UUID(created["id"])
    # Apply a roster display override on the agent's member row.
    async with session_factory() as session, session.begin():
        member = await session.scalar(
            select(Member).where(Member.agent_id == agent_id)
        )
        member.display_override = "覆盖名"
    rendered = await agent_service.get_agent(
        actor=owner, workspace_id=workspace.id, agent_id=agent_id
    )
    assert rendered["display_name"] == "覆盖名"
    assert rendered["name"] == "原名"  # canonical name unchanged
    assert rendered["member"]["display_name"] == "覆盖名"


# --- M3: issue-context enricher seam (§3.3 step 3) ------------------------------


@pytest.mark.unit
async def test_issue_context_enricher_seam_wraps_categories():
    """Comments/labels/attachments come from registered enrichers, untrusted-wrapped."""
    from mesh.agent import triggers

    async def fake_enricher(_session, _ws, _issue):
        return {
            "comments": ["请复现一下"],
            "labels": ["bug"],
            "attachments": ["log.txt"],
        }

    triggers.register_issue_context_enricher(fake_enricher)
    try:
        # Build a minimal fake issue row via a stub session.scalar.
        class _Issue:
            id = uuid.uuid4()
            identifier = "WS-1"
            title = "标题"
            description = "描述"

        class _Session:
            async def scalar(self, _stmt):
                return _Issue()

        ctx = await triggers._issue_context(
            _Session(), workspace_id=uuid.uuid4(), issue_id=uuid.uuid4()
        )
    finally:
        triggers._ISSUE_CONTEXT_ENRICHERS.remove(fake_enricher)

    assert ctx["comments"] == [f"{triggers.UNTRUSTED_BEGIN}请复现一下{triggers.UNTRUSTED_END}"]
    assert ctx["labels"] == [f"{triggers.UNTRUSTED_BEGIN}bug{triggers.UNTRUSTED_END}"]
    assert ctx["attachments"] == [f"{triggers.UNTRUSTED_BEGIN}log.txt{triggers.UNTRUSTED_END}"]
    assert triggers.UNTRUSTED_BEGIN in ctx["issue"]["title"]


@pytest.mark.unit
async def test_issue_context_default_slots_empty_and_enricher_error_isolates():
    """No enricher → empty slots; a failing enricher degrades to empty (no crash)."""
    from mesh.agent import triggers

    async def boom(_session, _ws, _issue):
        raise RuntimeError("enricher down")

    triggers.register_issue_context_enricher(boom)
    try:

        class _Issue:
            id = uuid.uuid4()
            identifier = "WS-2"
            title = "t"
            description = None

        class _Session:
            async def scalar(self, _stmt):
                return _Issue()

        ctx = await triggers._issue_context(
            _Session(), workspace_id=uuid.uuid4(), issue_id=uuid.uuid4()
        )
    finally:
        triggers._ISSUE_CONTEXT_ENRICHERS.remove(boom)

    assert ctx["comments"] == []
    assert ctx["labels"] == []
    assert ctx["attachments"] == []
