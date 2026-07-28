"""vcs_links unit tests (integrations.md §2.8 / §3.3 / §5.2).

Covers: identifier extraction/resolution, explicit links (idempotent
re-link, cross-issue conflict), auto-link from ingested VCS events, auto
status flow (validated transition + system comment + idempotency), stale
marking on merge/close.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.comment import Comment
from mesh.db.models.integration import IntegrationEvent, VcsLink
from mesh.db.models.issue import Issue, IssueStatus
from mesh.errors import BusinessRuleError
from mesh.integrations import vcs_links as vl
from mesh.integrations.connectors import NormalizedEvent
from tests.unit.integrations_support import seed_world

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


async def make_statuses(session_factory, world):
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        todo = IssueStatus(
            workspace_id=world["ws"], name="待办", category="todo", is_default=True
        )
        done = IssueStatus(workspace_id=world["ws"], name="已完成", category="done")
        cancelled = IssueStatus(workspace_id=world["ws"], name="已取消", category="cancelled")
        session.add_all([todo, done, cancelled])
    return {"todo": todo, "done": done, "cancelled": cancelled}


async def make_issue(session_factory, world, statuses, *, identifier="WEB-123", number=123):
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        issue = Issue(
            workspace_id=world["ws"],
            identifier_namespace_key="WEB",
            number=number,
            identifier=identifier,
            title=f"issue {identifier}",
            status_id=statuses["todo"].id,
            state_category="todo",
        )
        session.add(issue)
    return issue


def pr_event(**extra) -> NormalizedEvent:
    base = {
        "action": "closed", "pr_number": 123, "pr_title": "WEB-123 fix",
        "pr_state": "closed", "pr_merged": True, "source_branch": "fix/x",
    }
    base.update(extra)
    return NormalizedEvent(
        external_event_id=f"del-{uuid.uuid4().hex[:8]}",
        event_type="pull_request",
        external_ref="acme/web",
        actor_key="octocat",
        tenant_key="1234567",
        text="WEB-123 fix the thing",
        extra=base,
    )


async def make_event_row(session_factory, world, event):
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        row = IntegrationEvent(
            workspace_id=world["ws"], integration_id=world["integ_github"],
            external_event_id=event.external_event_id, event_type=event.event_type,
            payload={}, signature_status="valid", process_status="received",
        )
        session.add(row)
    return row


async def make_repo_binding(session_factory, world, *, match_config=None):
    from mesh.db.models.integration import IntegrationBinding

    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        binding = IntegrationBinding(
            workspace_id=world["ws"], integration_id=world["integ_github"],
            provider="github", provider_tenant_key="1234567",
            external_ref="acme/web", match_config=match_config or {},
            bound_agent_id=world["agent"],
        )
        session.add(binding)
    return binding


# ---------------------------------------------------------------------------
# Identifier extraction / resolution
# ---------------------------------------------------------------------------


def test_extract_identifiers_dedup_and_order():
    assert vl.extract_identifiers("WEB-1 x APP-2 x WEB-1", None, "WEB-3") == [
        "WEB-1", "APP-2", "WEB-3"
    ]
    assert vl.extract_identifiers("no identifiers here") == []
    assert vl.extract_identifiers("lower-web-1 nope") == []  # uppercase prefix required


async def test_resolve_issue_by_identifier(session_factory):
    world = await seed_world(session_factory)
    statuses = await make_statuses(session_factory, world)
    issue = await make_issue(session_factory, world, statuses)
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        found = await vl.resolve_issue_by_identifier(
            session, workspace_id=world["ws"], identifier="WEB-123"
        )
        assert found is not None and found.id == issue.id
        missing = await vl.resolve_issue_by_identifier(
            session, workspace_id=world["ws"], identifier="WEB-999"
        )
        assert missing is None


# ---------------------------------------------------------------------------
# Explicit links
# ---------------------------------------------------------------------------


async def test_explicit_link_idempotent_and_conflict(session_factory):
    world = await seed_world(session_factory)
    statuses = await make_statuses(session_factory, world)
    issue = await make_issue(session_factory, world, statuses)
    issue2 = await make_issue(
        session_factory, world, statuses, identifier="WEB-124", number=124
    )
    integration = await _integration(session_factory, world["integ_github"])
    vcs_ref = {"type": "pull_request", "id": "acme/web#9"}
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        first = await vl.explicit_link(
            session, workspace_id=world["ws"], integration=integration,
            provider="github", provider_tenant_key="1234567",
            vcs_ref=vcs_ref, issue_id=issue.id, created_by=world["member"], now=NOW,
        )
        # Same issue + same object → idempotent (returns existing).
        again = await vl.explicit_link(
            session, workspace_id=world["ws"], integration=integration,
            provider="github", provider_tenant_key="1234567",
            vcs_ref=vcs_ref, issue_id=issue.id, created_by=world["member"], now=NOW,
        )
        assert again["id"] == first["id"]
        # Another issue stealing the same external object → 409.
        with pytest.raises(BusinessRuleError) as excinfo:
            await vl.explicit_link(
                session, workspace_id=world["ws"], integration=integration,
                provider="github", provider_tenant_key="1234567",
                vcs_ref=vcs_ref, issue_id=issue2.id, created_by=world["member"],
                now=NOW,
            )
        assert excinfo.value.code == "conflict"


async def test_explicit_link_invalid_ref(session_factory):
    world = await seed_world(session_factory)
    statuses = await make_statuses(session_factory, world)
    issue = await make_issue(session_factory, world, statuses)
    integration = await _integration(session_factory, world["integ_github"])
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        with pytest.raises(BusinessRuleError) as excinfo:
            await vl.explicit_link(
                session, workspace_id=world["ws"], integration=integration,
                provider="github", provider_tenant_key="1234567",
                vcs_ref={"type": "weird", "id": "x"}, issue_id=issue.id,
                created_by=world["member"], now=NOW,
            )
        assert excinfo.value.code == "vcs_link_invalid"


async def test_delete_link_frees_slot(session_factory):
    world = await seed_world(session_factory)
    statuses = await make_statuses(session_factory, world)
    issue = await make_issue(session_factory, world, statuses)
    integration = await _integration(session_factory, world["integ_github"])
    vcs_ref = {"type": "pull_request", "id": "acme/web#77"}
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        link = await vl.explicit_link(
            session, workspace_id=world["ws"], integration=integration,
            provider="github", provider_tenant_key="1234567",
            vcs_ref=vcs_ref, issue_id=issue.id, created_by=world["member"], now=NOW,
        )
        await vl.delete_link(
            session, workspace_id=world["ws"],
            link_id=uuid.UUID(link["id"]), now=NOW,
        )
        # Slot free → re-link OK, history row kept as deleted.
        relink = await vl.explicit_link(
            session, workspace_id=world["ws"], integration=integration,
            provider="github", provider_tenant_key="1234567",
            vcs_ref=vcs_ref, issue_id=issue.id, created_by=world["member"], now=NOW,
        )
        assert relink["id"] != link["id"]
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        rows = (await session.execute(select(VcsLink))).scalars().all()
        assert {r.status for r in rows} == {"active", "deleted"}


# ---------------------------------------------------------------------------
# Ingestion: auto link + auto status flow
# ---------------------------------------------------------------------------


async def test_ingest_pr_merged_links_and_transitions(session_factory):
    world = await seed_world(session_factory)
    statuses = await make_statuses(session_factory, world)
    issue = await make_issue(session_factory, world, statuses)
    await make_repo_binding(
        session_factory, world,
        match_config={
            "vcs_events": ["pull_request.merged"],
            "auto_status_map": {"merged": "done", "closed": "cancelled"},
        },
    )
    event = pr_event()
    event_row = await make_event_row(session_factory, world, event)
    integration = await _integration(session_factory, world["integ_github"])
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        result = await vl.ingest_vcs_event(
            session, workspace_id=world["ws"], integration=integration,
            provider="github", event=event, event_row=event_row, now=NOW,
        )
    assert result["links_created"] == 1
    assert result["issues_transitioned"] == 1
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        reloaded = await session.get(Issue, issue.id)
        assert reloaded.state_category == "done"
        assert reloaded.completed_at is not None
        links = (await session.execute(select(VcsLink))).scalars().all()
        assert len(links) == 1
        assert links[0].status == "stale", "merged → stale link (§3.3)"
        assert links[0].external_state.get("pr_state") == "merged"
        comments = (await session.execute(
            select(Comment).where(Comment.issue_id == issue.id)
        )).scalars().all()
        assert len(comments) == 1
        assert comments[0].author_kind == "system"


async def test_ingest_repeat_event_idempotent(session_factory):
    world = await seed_world(session_factory)
    statuses = await make_statuses(session_factory, world)
    await make_issue(session_factory, world, statuses)
    await make_repo_binding(
        session_factory, world,
        match_config={"auto_status_map": {"merged": "done"}},
    )
    event = pr_event()
    integration = await _integration(session_factory, world["integ_github"])
    event_row = await make_event_row(session_factory, world, event)
    for _ in range(2):
        # Same event redelivered (at-least-once) — must stay idempotent.
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, world["ws"])
            await vl.ingest_vcs_event(
                session, workspace_id=world["ws"], integration=integration,
                provider="github", event=event, event_row=event_row, now=NOW,
            )
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        links = (await session.execute(select(VcsLink))).scalars().all()
        assert len(links) == 1, "partial unique → one active link"
        comments = (await session.execute(select(Comment))).scalars().all()
        assert len(comments) == 1, "idempotency key → one comment"


async def test_ingest_unresolved_identifier_audits_only(session_factory):
    world = await seed_world(session_factory)
    await make_repo_binding(session_factory, world)
    event = pr_event()  # WEB-123 does not exist
    event_row = await make_event_row(session_factory, world, event)
    integration = await _integration(session_factory, world["integ_github"])
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        result = await vl.ingest_vcs_event(
            session, workspace_id=world["ws"], integration=integration,
            provider="github", event=event, event_row=event_row, now=NOW,
        )
    assert result["links_created"] == 0
    assert result["issues_transitioned"] == 0  # never blocks ingestion (§3.5)


async def test_resolve_from_text_endpoint_logic(session_factory):
    world = await seed_world(session_factory)
    statuses = await make_statuses(session_factory, world)
    await make_issue(session_factory, world, statuses)
    integration = await _integration(session_factory, world["integ_github"])
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        result = await vl.resolve_from_text(
            session, workspace_id=world["ws"], integration=integration,
            provider="github", provider_tenant_key="1234567",
            source_text="merging WEB-123 today",
            vcs_ref={"type": "commit", "id": "sha-abc"},
            now=NOW,
        )
        assert result["identifiers"] == ["WEB-123"]
        assert len(result["links"]) == 1
        # Unresolvable identifier → identifier_not_resolved (§3.5).
        with pytest.raises(BusinessRuleError) as excinfo:
            await vl.resolve_from_text(
                session, workspace_id=world["ws"], integration=integration,
                provider="github", provider_tenant_key="1234567",
                source_text="see NOWEB-9",
                vcs_ref={"type": "commit", "id": "sha-def"},
                now=NOW,
            )
        assert excinfo.value.code == "identifier_not_resolved"


async def _integration(session_factory, integration_id):
    from mesh.db.models.integration import Integration

    async with session_factory() as session:
        return await session.get(Integration, integration_id)
