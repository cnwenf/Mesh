"""Regression tests for code-review findings M1–M3 / L5 / L7.

M1: accept/preview must reject links of soft-deleted workspaces.
M2: accepting with a removed/disabled roster row reactivates it (a consumed
    use must always yield access).
M3: a same-user concurrent accept that loses the race after the winner
    exhausted the link is a no-op, not a 422.
L5: slug history follows the most recent releaser of a slug.
L7: workspace creation writes an audit row.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from mesh.auth.rbac import resolve_workspace_by_slug
from mesh.db.models.audit import AuditLog
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.workspace import WorkspaceInvitation
from mesh.errors import BusinessRuleError
from mesh.workspace.invitations import InvitationService
from mesh.workspace.service import WorkspaceService

pytestmark = pytest.mark.unit


async def _seed_user(session_factory, email: str) -> User:
    async with session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'U') RETURNING id"),
                {"e": email},
            )
        ).scalar_one()
    return User(id=user_id, email=email, display_name="U")


async def _workspace_with_owner(session_factory, slug: str):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, f"{slug}-{uuid.uuid4().hex[:8]}@corp.com")
    created = await service.create_workspace(user=user, name="Reg", slug=slug)
    async with session_factory() as session:
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == created["id"], Member.user_id == user.id
            )
        )
    return service, user, created, member


# --- M1: soft-deleted workspace links are unusable --------------------------------


async def test_accept_and_preview_reject_deleted_workspace_links(session_factory):
    ws_service, owner, created, member = await _workspace_with_owner(
        session_factory, "m1-deleted"
    )
    inv_service = InvitationService(session_factory)
    invitation = (
        await inv_service.create_invitations(
            actor=member, workspace_id=created["id"], role="member"
        )
    )[0]
    token = invitation["invite_link"].rsplit("/", 1)[1]

    # Soft-delete the workspace.
    await ws_service.delete_workspace(
        actor=member, workspace=_ws_stub(created), confirm_slug="m1-deleted"
    )

    joiner = await _seed_user(session_factory, "m1-joiner@corp.com")
    with pytest.raises(BusinessRuleError) as excinfo:
        await inv_service.accept_invitation(user=joiner, token=token)
    assert excinfo.value.code == "invitation_invalid"
    assert excinfo.value.details == {"reason": "not_found"}

    preview = await inv_service.preview_invitation(token=token)
    assert preview == {"valid": False, "reason": "not_found"}

    # No use was consumed and no member row created in the deleted tenant.
    async with session_factory() as session:
        row = await session.scalar(
            select(WorkspaceInvitation).where(WorkspaceInvitation.id == invitation["id"])
        )
        members = (
            await session.execute(
                select(Member).where(Member.workspace_id == created["id"])
            )
        ).scalars().all()
    assert row.used_count == 0
    assert [m.user_id for m in members] == [owner.id]  # only the original owner


# --- M2: removed/disabled members rejoin on a fresh link ---------------------------


async def test_accept_reactivates_removed_member(session_factory):
    ws_service, owner, created, member = await _workspace_with_owner(
        session_factory, "m2-rejoin"
    )
    inv_service = InvitationService(session_factory)
    joiner = await _seed_user(session_factory, "m2-joiner@corp.com")

    first = (
        await inv_service.create_invitations(
            actor=member, workspace_id=created["id"], role="member"
        )
    )[0]
    accepted = await inv_service.accept_invitation(
        user=joiner, token=first["invite_link"].rsplit("/", 1)[1]
    )
    member_id = accepted["member"]["id"]

    # Remove the member (soft terminal state per member.md §4.4).
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE members SET status = 'removed' WHERE id = :id"),
            {"id": member_id},
        )

    # A fresh admin-issued link reactivates with the invited role.
    second = (
        await inv_service.create_invitations(
            actor=member, workspace_id=created["id"], role="admin", max_uses=5
        )
    )[0]
    rejoined = await inv_service.accept_invitation(
        user=joiner, token=second["invite_link"].rsplit("/", 1)[1]
    )
    assert rejoined["member"]["id"] == member_id
    assert rejoined["member"]["status"] == "active"
    assert rejoined["member"]["role"] == "admin"

    async with session_factory() as session:
        fresh = await session.scalar(select(Member).where(Member.id == member_id))
        link_row = await session.scalar(
            select(WorkspaceInvitation).where(WorkspaceInvitation.id == second["id"])
        )
        events = (await session.execute(select(OutboxEvent))).scalars().all()
    assert fresh.status == "active"
    assert fresh.disabled_at is None
    assert link_row.used_count == 1  # exactly one consumed use
    added = [
        e
        for e in events
        if e.event_type == "realtime.publish"
        and e.payload["event"] == "member.added"
        and e.payload["data"]["member_id"] == str(member_id)
    ]
    assert len(added) == 2  # original join + rejoin


# --- M3: same-user race loser gets a no-op, not a 422 -------------------------------


async def test_concurrent_same_user_accept_on_exhausted_link_is_noop(
    session_factory, monkeypatch
):
    _ws_service, _owner, created, member = await _workspace_with_owner(
        session_factory, "m3-race"
    )
    inv_service = InvitationService(session_factory)
    joiner = await _seed_user(session_factory, "m3-joiner@corp.com")

    invitation = (
        await inv_service.create_invitations(
            actor=member, workspace_id=created["id"], max_uses=1
        )
    )[0]
    token = invitation["invite_link"].rsplit("/", 1)[1]
    winner = await inv_service.accept_invitation(user=joiner, token=token)

    # Simulate the race: the loser's fast-path check ran before the winner
    # committed (misses the redemption), then the conditional UPDATE sees the
    # exhausted link (0 rows) — the post-UPDATE recheck must turn this into a
    # no-op returning the winner's member.
    original = InvitationService._existing_redemption_member
    calls = {"n": 0}

    async def _race_sim(self, session, *, invitation_id, user_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # fast path missed (winner had not committed yet)
        return await original(self, session, invitation_id=invitation_id, user_id=user_id)

    monkeypatch.setattr(InvitationService, "_existing_redemption_member", _race_sim)
    loser = await inv_service.accept_invitation(user=joiner, token=token)
    assert loser["member"]["id"] == winner["member"]["id"]

    async with session_factory() as session:
        row = await session.scalar(
            select(WorkspaceInvitation).where(WorkspaceInvitation.id == invitation["id"])
        )
    assert row.used_count == 1  # the loser did not consume a second use


# --- L5: slug history follows the most recent releaser --------------------------------


async def test_slug_history_follows_most_recent_releaser(session_factory):
    ws_service, owner_a, created_a, member_a = await _workspace_with_owner(
        session_factory, "l5-shared"
    )
    # A releases "l5-shared" by renaming.
    async with session_factory() as session:
        ws_a = await session.get(_ws_model(), _uuid(created_a["id"]))
    await ws_service.update_workspace(
        actor=member_a, workspace=ws_a, patch=_patch(slug="l5-a-renamed")
    )
    # A is deleted (confirm against the CURRENT slug), freeing "l5-shared";
    # B takes it.
    async with session_factory() as session:
        ws_a = await session.get(_ws_model(), _uuid(created_a["id"]))
    await ws_service.delete_workspace(
        actor=member_a, workspace=ws_a, confirm_slug="l5-a-renamed"
    )
    _, owner_b, created_b, member_b = await _workspace_with_owner(session_factory, "l5-shared")
    async with session_factory() as session:
        ws_b = await session.get(_ws_model(), _uuid(created_b["id"]))
    # B releases it too — the mapping must now point at B.
    await ws_service.update_workspace(
        actor=member_b, workspace=ws_b, patch=_patch(slug="l5-b-renamed")
    )
    async with session_factory() as session:
        mapped = (
            await session.execute(
                text("SELECT workspace_id FROM workspace_slug_history WHERE old_slug = :s"),
                {"s": "l5-shared"},
            )
        ).scalar_one()
    assert mapped == created_b["id"]

    # And by-slug resolution reaches B through the historic slug.
    async with session_factory() as session:
        context = await resolve_workspace_by_slug(session, user=owner_b, slug="l5-shared")
    assert context.workspace.id == created_b["id"]


# --- L7: workspace creation is audited ------------------------------------------------


async def test_create_workspace_writes_audit_row(session_factory):
    _service, _owner, created, _member = await _workspace_with_owner(
        session_factory, "l7-audit"
    )
    async with session_factory() as session:
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "workspace.created")
            )
        ).scalars().all()
    assert len(audits) == 1
    assert audits[0].workspace_id == created["id"]
    assert audits[0].metadata_ == {"slug": "l7-audit"}


# --- helpers ---------------------------------------------------------------------------


def _uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _ws_model():
    from mesh.db.models.workspace import Workspace

    return Workspace


def _ws_stub(created: dict):
    from mesh.db.models.workspace import Workspace

    return Workspace(
        id=_uuid(created["id"]),
        name=created["name"],
        slug=created["slug"],
        timezone=created.get("timezone", "UTC"),
        settings=created.get("settings", {}),
    )


def _patch(**kwargs):
    from mesh.workspace.service import WorkspacePatch

    return WorkspacePatch(**kwargs)
