"""Invitation service tests (workspace.md §2.3/§2.4/§3.2/§4.4, README §9 T1/T11).

Real PostgreSQL. Covers the link lifecycle (active/revoked/expired/exhausted —
no pending/accepted), hash-only tokens, the workspace-configurable caps
hardening (LOW-2), the atomic single-transaction accept with its concurrency
guarantees, redemption separation and idempotency.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.workspace import (
    WorkspaceInvitation,
    WorkspaceInvitationRedemption,
)
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
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


async def _workspace_with_admin(session_factory, slug: str = "inv-ws"):
    """Create a workspace; return (workspace_id, admin Member, admin User)."""
    ws_service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, f"admin-{uuid.uuid4().hex[:8]}@corp.com")
    created = await ws_service.create_workspace(user=user, name="Inv WS", slug=slug)
    workspace_id = created["id"]
    async with session_factory() as session:
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id, Member.user_id == user.id
            )
        )
    return workspace_id, member, user


async def _invitation_row(session_factory, invitation_id):
    async with session_factory() as session:
        return await session.scalar(
            select(WorkspaceInvitation).where(WorkspaceInvitation.id == invitation_id)
        )


async def _realtime_events(session_factory, event_name: str) -> list[OutboxEvent]:
    async with session_factory() as session:
        events = (await session.execute(select(OutboxEvent))).scalars().all()
    return [
        e
        for e in events
        if e.event_type == "realtime.publish" and e.payload["event"] == event_name
    ]


def _token_from_link(invite_link: str) -> str:
    assert invite_link.startswith("/invite/")
    return invite_link[len("/invite/") :]


# --- create -------------------------------------------------------------------


async def test_create_link_invitation_defaults(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "create-defaults")
    service = InvitationService(session_factory)
    results = await service.create_invitations(
        actor=admin, workspace_id=workspace_id, role="member"
    )
    assert len(results) == 1
    created = results[0]
    assert created["role"] == "member"
    assert created["status"] == "active"
    assert created["max_uses"] == 10  # default, never NULL (MES-4)
    assert created["used_count"] == 0
    assert created["email"] is None
    expires_at = created["expires_at"]
    delta = expires_at - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7, hours=1)  # default 7 days

    # Plaintext token appears ONLY in the create response; DB stores the hash.
    token = _token_from_link(created["invite_link"])
    assert token.startswith("invtk_")
    row = await _invitation_row(session_factory, created["id"])
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.token_prefix == token[:14]
    assert token not in (row.token_prefix + row.token_hash)


async def test_create_email_batch_lowercases_and_one_row_each(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "create-batch")
    service = InvitationService(session_factory)
    results = await service.create_invitations(
        actor=admin,
        workspace_id=workspace_id,
        emails=["Jane@Acme.com", "john@acme.com"],
        role="member",
        expires_in_hours=72,
    )
    assert [r["email"] for r in results] == ["jane@acme.com", "john@acme.com"]
    for result in results:
        assert "invite_link" in result
        delta = result["expires_at"] - datetime.now(UTC)
        assert timedelta(hours=71) < delta <= timedelta(hours=73)


async def test_create_owner_role_rejected(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "role-owner")
    service = InvitationService(session_factory)
    with pytest.raises(ValidationError):
        await service.create_invitations(
            actor=admin, workspace_id=workspace_id, role="owner"
        )


async def test_create_batch_over_50_emails_rejected(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "batch-51")
    service = InvitationService(session_factory)
    emails = [f"user{i}@acme.com" for i in range(51)]
    with pytest.raises(ValidationError):
        await service.create_invitations(
            actor=admin, workspace_id=workspace_id, emails=emails
        )


async def test_create_invalid_email_rejected(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "bad-email")
    service = InvitationService(session_factory)
    with pytest.raises(ValidationError):
        await service.create_invitations(
            actor=admin, workspace_id=workspace_id, emails=["not-an-email"]
        )


async def test_create_duplicate_active_email_409(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "dup-email")
    service = InvitationService(session_factory)
    await service.create_invitations(
        actor=admin, workspace_id=workspace_id, emails=["dup@acme.com"]
    )
    with pytest.raises(ConflictError) as excinfo:
        await service.create_invitations(
            actor=admin, workspace_id=workspace_id, emails=["dup@acme.com"]
        )
    assert excinfo.value.code == "conflict"


async def test_create_over_caps_rejected_422(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "caps-default")
    service = InvitationService(session_factory)
    with pytest.raises(BusinessRuleError) as uses:
        await service.create_invitations(
            actor=admin, workspace_id=workspace_id, role="member", max_uses=101
        )
    assert uses.value.code == "invitation_limits_exceeded"
    with pytest.raises(BusinessRuleError) as hours:
        await service.create_invitations(
            actor=admin, workspace_id=workspace_id, role="member", expires_in_hours=721
        )
    assert hours.value.code == "invitation_limits_exceeded"


async def test_caps_are_workspace_configurable_and_defaults_exempt(session_factory):
    """LOW-2: caps read from settings; UNSPECIFIED values take defaults and are
    never cap-rejected (workspace.md §5.1)."""
    ws_service = WorkspaceService(session_factory)
    inv_service = InvitationService(session_factory)
    user = await _seed_user(session_factory, "caps-cfg@corp.com")
    created = await ws_service.create_workspace(
        user=user,
        name="Caps",
        slug="caps-cfg",
        settings={"invitation_max_uses_cap": 5, "invitation_max_lifetime_hours_cap": 24},
    )
    workspace_id = created["id"]
    async with session_factory() as session:
        admin = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id, Member.user_id == user.id
            )
        )
    with pytest.raises(BusinessRuleError):
        await inv_service.create_invitations(
            actor=admin, workspace_id=workspace_id, max_uses=6
        )
    # Unspecified → default 10, NOT rejected despite the 5 cap.
    results = await inv_service.create_invitations(actor=admin, workspace_id=workspace_id)
    assert results[0]["max_uses"] == 10


# --- list ---------------------------------------------------------------------


async def test_list_invitations_never_leaks_tokens_and_renders_lazy_expiry(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "list-inv")
    service = InvitationService(session_factory)
    results = await service.create_invitations(actor=admin, workspace_id=workspace_id)
    invitation_id = results[0]["id"]

    # Backdate expiry to exercise the lazy expired rendering.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE workspace_invitations "
                "SET expires_at = now() - interval '1 hour' WHERE id = :id"
            ),
            {"id": invitation_id},
        )
    items, next_cursor = await service.list_invitations(workspace_id=workspace_id)
    assert next_cursor is None
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "expired"  # lazily computed
    assert "invite_link" not in item
    assert "token_hash" not in item
    assert "token" not in item


# --- revoke -------------------------------------------------------------------


async def test_revoke_invitation(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "revoke-inv")
    service = InvitationService(session_factory)
    created = (
        await service.create_invitations(actor=admin, workspace_id=workspace_id)
    )[0]
    revoked = await service.revoke_invitation(
        actor=admin, workspace_id=workspace_id, invitation_id=created["id"]
    )
    assert revoked["status"] == "revoked"
    # Terminal states cannot be revoked again.
    with pytest.raises(ConflictError):
        await service.revoke_invitation(
            actor=admin, workspace_id=workspace_id, invitation_id=created["id"]
        )
    # Unknown invitation → 404.
    with pytest.raises(NotFoundError):
        await service.revoke_invitation(
            actor=admin, workspace_id=workspace_id, invitation_id=uuid.uuid4()
        )


# --- preview ------------------------------------------------------------------


async def test_preview_valid_and_invalid_reasons(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "preview-inv")
    service = InvitationService(session_factory)

    valid = (await service.create_invitations(actor=admin, workspace_id=workspace_id))[0]
    token = _token_from_link(valid["invite_link"])
    preview = await service.preview_invitation(token=token)
    assert preview["valid"] is True
    assert preview["workspace_name"] == "Inv WS"
    assert preview["role"] == "member"
    assert "expires_at" in preview
    assert "id" not in preview  # limited fields only

    assert (await service.preview_invitation(token="invtk_unknown")) == {
        "valid": False,
        "reason": "not_found",
    }

    revoked = (await service.create_invitations(actor=admin, workspace_id=workspace_id))[0]
    await service.revoke_invitation(
        actor=admin, workspace_id=workspace_id, invitation_id=revoked["id"]
    )
    preview = await service.preview_invitation(token=_token_from_link(revoked["invite_link"]))
    assert preview == {"valid": False, "reason": "revoked"}

    expired = (await service.create_invitations(actor=admin, workspace_id=workspace_id))[0]
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE workspace_invitations SET expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": expired["id"]},
        )
    preview = await service.preview_invitation(token=_token_from_link(expired["invite_link"]))
    assert preview == {"valid": False, "reason": "expired"}


# --- accept -------------------------------------------------------------------


async def test_accept_creates_member_and_redemption(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "accept-basic")
    service = InvitationService(session_factory)
    created = (
        await service.create_invitations(actor=admin, workspace_id=workspace_id, role="guest")
    )[0]
    token = _token_from_link(created["invite_link"])
    joiner = await _seed_user(session_factory, "joiner@corp.com")

    result = await service.accept_invitation(user=joiner, token=token)
    assert result["workspace"]["id"] == workspace_id
    assert result["workspace"]["name"] == "Inv WS"
    assert result["member"]["role"] == "guest"
    assert result["member"]["status"] == "active"

    row = await _invitation_row(session_factory, created["id"])
    assert row.used_count == 1
    assert row.status == "active"  # 1/10 uses — still active

    async with session_factory() as session:
        redemptions = (
            await session.execute(select(WorkspaceInvitationRedemption))
        ).scalars().all()
    assert len(redemptions) == 1
    assert redemptions[0].invitation_id == created["id"]
    assert redemptions[0].user_id == joiner.id
    assert redemptions[0].member_id == result["member"]["id"]

    # Events: member.added + invitation.redeemed via the outbox (§6.6).
    redeemed = await _realtime_events(session_factory, "invitation.redeemed")
    assert len(redeemed) == 1
    assert redeemed[0].payload["data"]["used_count"] == 1
    assert redeemed[0].payload["data"]["invitation_id"] == str(created["id"])
    assert await _realtime_events(session_factory, "member.added")


async def test_accept_is_idempotent_per_user_per_link(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "accept-idem")
    service = InvitationService(session_factory)
    created = (
        await service.create_invitations(actor=admin, workspace_id=workspace_id)
    )[0]
    token = _token_from_link(created["invite_link"])
    joiner = await _seed_user(session_factory, "idem@corp.com")

    first = await service.accept_invitation(user=joiner, token=token)
    second = await service.accept_invitation(user=joiner, token=token)
    assert second["member"]["id"] == first["member"]["id"]
    row = await _invitation_row(session_factory, created["id"])
    assert row.used_count == 1  # no double increment


async def test_accept_concurrent_last_slot_t11(session_factory):
    """T11: max_uses=1 link, two concurrent accepters — exactly one wins."""
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "accept-race")
    service = InvitationService(session_factory)
    created = (
        await service.create_invitations(
            actor=admin, workspace_id=workspace_id, max_uses=1
        )
    )[0]
    token = _token_from_link(created["invite_link"])
    user_a = await _seed_user(session_factory, "racer-a@corp.com")
    user_b = await _seed_user(session_factory, "racer-b@corp.com")

    async def _accept(user):
        try:
            return ("ok", await service.accept_invitation(user=user, token=token))
        except BusinessRuleError as exc:
            return ("fail", exc.code)

    outcomes = await asyncio.gather(_accept(user_a), _accept(user_b))
    statuses = sorted(status for status, _ in outcomes)
    assert statuses == ["fail", "ok"]
    failing = next(payload for status, payload in outcomes if status == "fail")
    assert failing == "invitation_invalid"

    row = await _invitation_row(session_factory, created["id"])
    assert row.used_count == 1  # never over max_uses
    assert row.status == "exhausted"
    async with session_factory() as session:
        members = (
            await session.execute(
                select(Member).where(Member.workspace_id == workspace_id)
            )
        ).scalars().all()
    # admin + exactly one joiner.
    assert len(members) == 2


async def test_accept_flips_to_exhausted_at_max_uses(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "accept-exhaust")
    service = InvitationService(session_factory)
    created = (
        await service.create_invitations(actor=admin, workspace_id=workspace_id, max_uses=2)
    )[0]
    token = _token_from_link(created["invite_link"])
    await service.accept_invitation(
        user=await _seed_user(session_factory, "ex1@corp.com"), token=token
    )
    row = await _invitation_row(session_factory, created["id"])
    assert row.status == "active"  # 1/2
    await service.accept_invitation(
        user=await _seed_user(session_factory, "ex2@corp.com"), token=token
    )
    row = await _invitation_row(session_factory, created["id"])
    assert row.used_count == 2
    assert row.status == "exhausted"
    # Third user is rejected.
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.accept_invitation(
            user=await _seed_user(session_factory, "ex3@corp.com"), token=token
        )
    assert excinfo.value.code == "invitation_invalid"
    assert excinfo.value.details == {"reason": "exhausted"}


async def test_accept_revoked_and_expired_and_unknown(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "accept-invalid")
    service = InvitationService(session_factory)
    joiner = await _seed_user(session_factory, "invalid@corp.com")

    revoked = (await service.create_invitations(actor=admin, workspace_id=workspace_id))[0]
    await service.revoke_invitation(
        actor=admin, workspace_id=workspace_id, invitation_id=revoked["id"]
    )
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.accept_invitation(
            user=joiner, token=_token_from_link(revoked["invite_link"])
        )
    assert excinfo.value.details == {"reason": "revoked"}

    expired = (await service.create_invitations(actor=admin, workspace_id=workspace_id))[0]
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE workspace_invitations SET expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": expired["id"]},
        )
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.accept_invitation(
            user=joiner, token=_token_from_link(expired["invite_link"])
        )
    assert excinfo.value.details == {"reason": "expired"}

    with pytest.raises(BusinessRuleError) as excinfo:
        await service.accept_invitation(user=joiner, token="invtk_never-existed")
    assert excinfo.value.code == "invitation_invalid"
    assert excinfo.value.details == {"reason": "not_found"}


async def test_accept_by_existing_member_reuses_roster_row(session_factory):
    """A user already in the roster (e.g. removed row aside) consuming a fresh
    link does not create a second member row (uq_members_ws_user)."""
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "accept-member")
    service = InvitationService(session_factory)
    created = (
        await service.create_invitations(actor=admin, workspace_id=workspace_id)
    )[0]
    # The admin accepts their own workspace's link — already a member.
    result = await service.accept_invitation(
        user=await admin_user(session_factory, admin),
        token=_token_from_link(created["invite_link"]),
    )
    assert result["member"]["id"] == admin.id
    row = await _invitation_row(session_factory, created["id"])
    assert row.used_count == 1
    async with session_factory() as session:
        member_count = (
            await session.execute(
                select(Member).where(Member.workspace_id == workspace_id)
            )
        ).scalars().all()
    assert len(member_count) == 1  # still exactly one roster row


async def admin_user(session_factory, member: Member) -> User:
    async with session_factory() as session:
        return await session.get(User, member.user_id)


# --- sweep --------------------------------------------------------------------


async def test_sweep_expires_only_active_past_due(session_factory):
    workspace_id, admin, _ = await _workspace_with_admin(session_factory, "sweep-inv")
    service = InvitationService(session_factory)
    due = (await service.create_invitations(actor=admin, workspace_id=workspace_id))[0]
    fresh = (await service.create_invitations(actor=admin, workspace_id=workspace_id))[0]
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE workspace_invitations SET expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": due["id"]},
        )
    swept = await service.sweep_expired()
    assert swept == 1
    assert (await _invitation_row(session_factory, due["id"])).status == "expired"
    assert (await _invitation_row(session_factory, fresh["id"])).status == "active"
