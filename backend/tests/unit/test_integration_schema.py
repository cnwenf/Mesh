"""Integrations schema contract tests (integrations.md §2 / §5.4, T29).

Real PostgreSQL assertions for the data-model red lines:

* T29⑩ structure negatives — ``external_identities`` is a GLOBAL identity
  table: no ``workspace_id`` column, no CASCADE FK to workspaces, no
  workspace RLS policy (information_schema / pg_policies).
* T29① global binding key — two workspaces' integration instances cannot
  bind the same external identity (INSERT rejected).
* T29② scope exact XOR — workspace+project and project-without-project
  bindings are both CHECK-rejected.
* T29③ project deletion cascades project-scoped bindings (no unreachable
  SET-NULL state, the delete succeeds).
* T29④ vcs_links partial-unique active key + integration-delete cascade.
* §5.4 real DELETE behaviors — agent delete SET NULLs ``bound_agent_id``
  (workspace_id stays NOT NULL); integration hard-delete cascades
  bindings/events/vcs_links; deleting the link-origin workspace only NULLs
  ``external_identities.created_in_workspace_id`` (mapping survives, R5).
* T29⑪ ``external_identity_unlink_allowed`` — owner-only; role columns do
  not participate (admin who is not the owner is denied — no bypass).

pytest.mark.unit (real PostgreSQL, no mocks on the contract path).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.agent import Agent
from mesh.db.models.integration import (
    ExternalIdentity,
    Integration,
    IntegrationBinding,
    IntegrationEvent,
    VcsLink,
    WebhookSubscription,
    WebhookSubscriptionDelivery,
)
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace

pytestmark = pytest.mark.unit


async def _seed_two_worlds(session_factory) -> dict:
    """Two workspaces, each with an admin member + agent + an integration."""
    ids = {k: uuid.uuid4() for k in (
        "ws_a", "user_a", "member_a", "agent_a", "integ_a",
        "ws_b", "user_b", "member_b", "agent_b", "integ_b",
    )}
    async with session_factory() as session, session.begin():
        for ws, tag in (("ws_a", "a"), ("ws_b", "b")):
            session.add(Workspace(id=ids[ws], name=f"WS {tag}", slug=f"intg-{tag}-{ids[ws].hex[:8]}"))
            session.add(User(
                id=ids[f"user_{tag}"],
                email=f"intg-{tag}-{ids[f'user_{tag}'].hex[:8]}@mesh.test",
                display_name=f"Owner {tag}",
                password_hash="unused-in-tests",
            ))
        await session.flush()
        for tag in ("a", "b"):
            session.add(Agent(
                id=ids[f"agent_{tag}"], workspace_id=ids[f"ws_{tag}"],
                name=f"Agent {tag}", owner_user_id=ids[f"user_{tag}"],
                lifecycle_status="active",
            ))
            await session.flush()
            session.add(Member(
                id=ids[f"member_{tag}"], workspace_id=ids[f"ws_{tag}"],
                member_type="human", user_id=ids[f"user_{tag}"],
                role="admin", status="active",
            ))
            session.add(Member(
                id=uuid.uuid4(), workspace_id=ids[f"ws_{tag}"],
                member_type="agent", agent_id=ids[f"agent_{tag}"],
                role="member", status="active",
            ))
            await session.flush()
            session.add(Integration(
                id=ids[f"integ_{tag}"], workspace_id=ids[f"ws_{tag}"],
                kind="im_slack", name=f"slack-{tag}",
                config={"team_id": f"T{tag.upper()}000"},
                created_by=ids[f"member_{tag}"],
            ))
    return ids


# ---------------------------------------------------------------------------
# T29⑩ — global identity table structure negatives
# ---------------------------------------------------------------------------


async def test_external_identities_has_no_workspace_column(session_factory):
    async with session_factory() as session:
        column = (await session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'external_identities' AND column_name = 'workspace_id'"
        ))).scalar_one_or_none()
    assert column is None, "external_identities must not carry a workspace_id ownership column (R5)"


async def test_external_identities_has_no_cascade_fk_to_workspaces(session_factory):
    """T29⑩: no FK from external_identities to WORKSPACES may cascade —
    workspace deletion must never control the global mapping lifecycle.
    (The user_id → users cascade IS the sole lifecycle cascade, by design.)"""
    async with session_factory() as session:
        cascade_fk = (await session.execute(text("""
            SELECT c.conname
              FROM pg_constraint c
             WHERE c.conrelid = 'external_identities'::regclass
               AND c.confrelid = 'workspaces'::regclass
               AND c.confdeltype = 'c'
        """))).all()
    assert cascade_fk == [], "no FK from external_identities to workspaces may cascade (R5)"


async def test_external_identities_origin_workspace_fk_sets_null(session_factory):
    async with session_factory() as session:
        rule = (await session.execute(text("""
            SELECT rc.delete_rule
              FROM information_schema.referential_constraints rc
              JOIN information_schema.key_column_usage kcu
                ON kcu.constraint_name = rc.constraint_name
             WHERE kcu.table_name = 'external_identities'
               AND kcu.column_name = 'created_in_workspace_id'
        """))).scalar_one()
    assert rule == "SET NULL"


async def test_external_identities_has_no_workspace_rls_policy(session_factory):
    async with session_factory() as session:
        policies = (await session.execute(text(
            "SELECT policyname FROM pg_policies WHERE tablename = 'external_identities'"
        ))).all()
    assert policies == [], "external_identities is a global table — no workspace RLS (R5/T29⑩)"


async def test_tenant_tables_have_fail_closed_rls(session_factory):
    async with session_factory() as session:
        tables = (await session.execute(text(
            "SELECT tablename FROM pg_policies "
            "WHERE policyname = 'mesh_' || tablename || '_tenant' "
            "  AND tablename IN ('integrations','integration_bindings','integration_events',"
            "'webhook_subscriptions','webhook_subscription_deliveries','vcs_links')"
        ))).scalars().all()
    assert set(tables) == {
        "integrations", "integration_bindings", "integration_events",
        "webhook_subscriptions", "webhook_subscription_deliveries", "vcs_links",
    }


# ---------------------------------------------------------------------------
# T29① — global external-identity binding key
# ---------------------------------------------------------------------------


async def test_cross_workspace_binding_conflict_rejected(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(IntegrationBinding(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="slack", provider_tenant_key="TSHARED",
            external_ref="C_SHARED_CHANNEL",
        ))
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(IntegrationBinding(
                workspace_id=ids["ws_b"], integration_id=ids["integ_b"],
                provider="slack", provider_tenant_key="TSHARED",
                external_ref="C_SHARED_CHANNEL",
            ))


async def test_disabled_binding_still_occupies_external_key(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(IntegrationBinding(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="slack", provider_tenant_key="T1",
            external_ref="C_OCCUPIED", status="disabled",
        ))
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(IntegrationBinding(
                workspace_id=ids["ws_b"], integration_id=ids["integ_b"],
                provider="slack", provider_tenant_key="T1",
                external_ref="C_OCCUPIED",
            ))


async def test_different_tenants_same_external_ref_coexist(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(IntegrationBinding(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="slack", provider_tenant_key="TENANT_1", external_ref="C_GENERAL",
        ))
        session.add(IntegrationBinding(
            workspace_id=ids["ws_b"], integration_id=ids["integ_b"],
            provider="slack", provider_tenant_key="TENANT_2", external_ref="C_GENERAL",
        ))


# ---------------------------------------------------------------------------
# T29② — scope exact XOR
# ---------------------------------------------------------------------------


async def test_workspace_scope_with_project_rejected(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        project = Project(workspace_id=ids["ws_a"], name="P1", key="IP1", visibility="public")
        session.add(project)
        await session.flush()
        session.add(IntegrationBinding(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="slack", external_ref="C_XOR_1",
            scope="workspace", project_id=project.id,
        ))
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_project_scope_without_project_rejected(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(IntegrationBinding(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="slack", external_ref="C_XOR_2",
            scope="project", project_id=None,
        ))
        with pytest.raises(IntegrityError):
            await session.flush()


# ---------------------------------------------------------------------------
# T29③ — project deletion cascades project-scoped bindings
# ---------------------------------------------------------------------------


async def test_project_delete_cascades_project_bindings(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        project = Project(workspace_id=ids["ws_a"], name="P2", key="IP2", visibility="public")
        session.add(project)
        await session.flush()
        session.add(IntegrationBinding(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="slack", external_ref="C_PROJ_BIND",
            scope="project", project_id=project.id,
        ))
        project_id = project.id
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": project_id})
    async with session_factory() as session:
        remaining = (await session.execute(text(
            "SELECT count(*) FROM integration_bindings WHERE project_id = :pid"
        ), {"pid": project_id})).scalar_one()
    assert remaining == 0


# ---------------------------------------------------------------------------
# T29④ — vcs_links partial unique + integration cascade
# ---------------------------------------------------------------------------


async def test_vcs_links_active_unique_and_relink_after_delete(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(VcsLink(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="github", provider_tenant_key="12345",
            external_object_type="pull_request", external_object_ref="acme/web#42",
            mesh_entity_type="issue", mesh_entity_id=uuid.uuid4(),
            link_source="manual",
        ))
    # Duplicate ACTIVE link for the same external object → rejected.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(VcsLink(
                workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
                provider="github", provider_tenant_key="12345",
                external_object_type="pull_request", external_object_ref="acme/web#42",
                mesh_entity_type="issue", mesh_entity_id=uuid.uuid4(),
            ))
    # Mark the first link deleted → the external object slot is released.
    async with session_factory() as session, session.begin():
        await session.execute(text(
            "UPDATE vcs_links SET status = 'deleted' "
            "WHERE external_object_ref = 'acme/web#42' AND status = 'active'"
        ))
    async with session_factory() as session, session.begin():
        session.add(VcsLink(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="github", provider_tenant_key="12345",
            external_object_type="pull_request", external_object_ref="acme/web#42",
            mesh_entity_type="issue", mesh_entity_id=uuid.uuid4(),
        ))


async def test_integration_hard_delete_cascades(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(IntegrationBinding(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="slack", external_ref="C_CASCADE",
        ))
        session.add(IntegrationEvent(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            external_event_id="evt-cascade-1", event_type="message.channels",
            payload={}, signature_status="valid",
        ))
        session.add(VcsLink(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="github", external_object_type="repository",
            external_object_ref="acme/cascade",
            mesh_entity_type="issue", mesh_entity_id=uuid.uuid4(),
        ))
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM integrations WHERE id = :iid"),
                              {"iid": ids["integ_a"]})
    async with session_factory() as session:
        for table in ("integration_bindings", "integration_events", "vcs_links"):
            count = (await session.execute(text(
                f"SELECT count(*) FROM {table} WHERE integration_id = :iid"
            ), {"iid": ids["integ_a"]})).scalar_one()
            assert count == 0, f"{table} must cascade on integration hard delete"


# ---------------------------------------------------------------------------
# §5.4 / T29⑨ — real DELETE behaviors
# ---------------------------------------------------------------------------


async def test_agent_delete_sets_null_bound_agent(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(IntegrationBinding(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            provider="slack", external_ref="C_AGENT_NULL",
            bound_agent_id=ids["agent_a"],
        ))
    async with session_factory() as session, session.begin():
        # The roster row is RESTRICT-protected; delete the agent's member row
        # then the agent itself (agent.md lifecycle — physical delete path).
        await session.execute(text(
            "DELETE FROM members WHERE workspace_id = :ws AND agent_id = :ag"
        ), {"ws": ids["ws_a"], "ag": ids["agent_a"]})
        await session.execute(text("DELETE FROM agents WHERE id = :ag"), {"ag": ids["agent_a"]})
    async with session_factory() as session:
        row = (await session.execute(text(
            "SELECT bound_agent_id, workspace_id FROM integration_bindings "
            "WHERE external_ref = 'C_AGENT_NULL'"
        ))).one()
    assert row[0] is None, "bound_agent_id must be SET NULL (column-level)"
    assert row[1] == ids["ws_a"], "workspace_id must stay NOT NULL"


async def test_origin_workspace_delete_nulls_audit_column_only(session_factory):
    """T29⑨ (R5): deleting the link-origin workspace must NOT cascade the
    global mapping — only ``created_in_workspace_id`` is nulled."""
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(ExternalIdentity(
            provider="slack", provider_tenant_key="T_A",
            external_user_key="U_ORIGIN",
            user_id=ids["user_b"],  # owner lives in workspace B
            created_in_workspace_id=ids["ws_a"],
        ))
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM workspaces WHERE id = :ws"),
                              {"ws": ids["ws_a"]})
    async with session_factory() as session:
        row = (await session.execute(text(
            "SELECT user_id, created_in_workspace_id FROM external_identities "
            "WHERE external_user_key = 'U_ORIGIN'"
        ))).one_or_none()
    assert row is not None, "global mapping must survive origin-workspace deletion (R5)"
    assert row[0] == ids["user_b"]
    assert row[1] is None


async def test_user_delete_cascades_mapping(session_factory):
    """T29⑧: user deactivation (users row delete) cascades the mapping."""
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(ExternalIdentity(
            provider="slack", provider_tenant_key="T_A",
            external_user_key="U_GONE", user_id=ids["user_a"],
            created_in_workspace_id=ids["ws_a"],
        ))
    async with session_factory() as session, session.begin():
        # RESTRICT-protected references must go first: the integration
        # (created_by → members), the agent (owner_user_id → users) with
        # its roster row, then the human member, then the user.
        await session.execute(text("DELETE FROM integrations WHERE id = :i"),
                              {"i": ids["integ_a"]})
        await session.execute(text(
            "DELETE FROM members WHERE workspace_id = :ws AND agent_id = :ag"
        ), {"ws": ids["ws_a"], "ag": ids["agent_a"]})
        await session.execute(text("DELETE FROM agents WHERE id = :ag"),
                              {"ag": ids["agent_a"]})
        await session.execute(text("DELETE FROM members WHERE user_id = :u"),
                              {"u": ids["user_a"]})
        await session.execute(text("DELETE FROM users WHERE id = :u"),
                              {"u": ids["user_a"]})
    async with session_factory() as session:
        remaining = (await session.execute(text(
            "SELECT count(*) FROM external_identities WHERE external_user_key = 'U_GONE'"
        ))).scalar_one()
    assert remaining == 0


async def test_external_identity_global_key_rejects_remap(session_factory):
    """T29⑦: the same external account cannot map to a second user."""
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(ExternalIdentity(
            provider="feishu", provider_tenant_key="TK1",
            external_user_key="ou_123", user_id=ids["user_a"],
        ))
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(ExternalIdentity(
                provider="feishu", provider_tenant_key="TK1",
                external_user_key="ou_123", user_id=ids["user_b"],
            ))


async def test_different_tenant_same_user_key_coexist(session_factory):
    """T29⑥: identity key includes the platform tenant — same user key in
    different external tenants coexists."""
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(ExternalIdentity(
            provider="feishu", provider_tenant_key="TK_X",
            external_user_key="ou_same", user_id=ids["user_a"],
        ))
        session.add(ExternalIdentity(
            provider="feishu", provider_tenant_key="TK_Y",
            external_user_key="ou_same", user_id=ids["user_b"],
        ))


# ---------------------------------------------------------------------------
# T29⑪ — external_identity_unlink_allowed executable reference
# ---------------------------------------------------------------------------


async def test_unlink_allowed_owner_only_no_admin_bypass(session_factory):
    ids = await _seed_two_worlds(session_factory)
    # user_b is the mapping owner; member_b is ws-B ADMIN but a different
    # user than the owner would be in the cross-user case below.
    async with session_factory() as session, session.begin():
        session.add(ExternalIdentity(
            provider="github", provider_tenant_key="",
            external_user_key="octocat", user_id=ids["user_a"],  # owner = user A
            created_in_workspace_id=ids["ws_a"],
        ))
        await session.flush()
        identity_id = (await session.execute(text(
            "SELECT id FROM external_identities WHERE external_user_key = 'octocat'"
        ))).scalar_one()
        # member_a: owner's member row in ws A (role admin here — irrelevant).
        owner_allowed = (await session.execute(text(
            "SELECT external_identity_unlink_allowed(:i, :m)"
        ), {"i": identity_id, "m": ids["member_a"]})).scalar_one()
        # member_b: a workspace ADMIN who is NOT the mapping owner.
        admin_bypass = (await session.execute(text(
            "SELECT external_identity_unlink_allowed(:i, :m)"
        ), {"i": identity_id, "m": ids["member_b"]})).scalar_one()
    assert owner_allowed is True, "owner via any of their member rows → allowed"
    assert admin_bypass is False, "admin role must NOT constitute unlink authorization (R5)"


# ---------------------------------------------------------------------------
# §6.5 — delivery ledger idempotency key
# ---------------------------------------------------------------------------


async def test_delivery_unique_subscription_event(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        subscription = WebhookSubscription(
            workspace_id=ids["ws_a"], url="https://example.com/hook",
            secret_ref="cred-ciphertext", created_by=ids["member_a"],
        )
        session.add(subscription)
        await session.flush()
        session.add(WebhookSubscriptionDelivery(
            workspace_id=ids["ws_a"], subscription_id=subscription.id,
            event_ref="outbox-event-1",
        ))
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(WebhookSubscriptionDelivery(
                workspace_id=ids["ws_a"], subscription_id=subscription.id,
                event_ref="outbox-event-1",
            ))


async def test_integration_event_dedup_key(session_factory):
    ids = await _seed_two_worlds(session_factory)
    async with session_factory() as session, session.begin():
        session.add(IntegrationEvent(
            workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
            external_event_id="evt-dup-1", event_type="message.channels",
            payload={}, signature_status="valid",
        ))
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(IntegrationEvent(
                workspace_id=ids["ws_a"], integration_id=ids["integ_a"],
                external_event_id="evt-dup-1", event_type="message.channels",
                payload={}, signature_status="valid",
            ))
