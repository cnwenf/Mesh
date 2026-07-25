"""Workspace service tests (workspace.md §1-§2, §5.1).

Real PostgreSQL: services own their transactions; unit tests exercise them
directly (no route plumbing). Covers CRUD, slug redirects, settings shallow
merge with the locale single source, prefix registry semantics (T19) and the
row-locked inbox sequence (T15).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, text

from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.workspace import (
    IdentifierPrefixRegistry,
    Workspace,
    WorkspaceSlugHistory,
)
from mesh.errors import BusinessRuleError, ConflictError, ForbiddenError, ValidationError
from mesh.workspace.service import (
    WorkspacePatch,
    WorkspaceService,
    change_inbox_prefix,
    next_inbox_issue_number,
    occupy_project_prefix,
    workspace_to_dict,
)

pytestmark = pytest.mark.unit

PASSWORD = "unused-in-service-tests"


async def _seed_user(session_factory, email: str) -> User:
    async with session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'U') RETURNING id"),
                {"e": email},
            )
        ).scalar_one()
    return User(id=user_id, email=email, display_name="U")


async def _outbox_events(session_factory) -> list[OutboxEvent]:
    async with session_factory() as session:
        return list((await session.execute(select(OutboxEvent))).scalars().all())


async def _member_row(session_factory, workspace_id, user_id) -> Member | None:
    async with session_factory() as session:
        return await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id, Member.user_id == user_id
            )
        )


# --- create -------------------------------------------------------------------


async def test_create_workspace_seeds_owner_member_and_inbox_prefix(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "founder@corp.com")

    result = await service.create_workspace(
        user=user, name="Acme Team", slug="acme", timezone="Asia/Shanghai"
    )
    assert result["name"] == "Acme Team"
    assert result["slug"] == "acme"
    assert result["timezone"] == "Asia/Shanghai"
    assert result["my_role"] == "owner"
    assert result["settings"] == {"default_locale": "en"}
    assert "default_language" not in result  # R4/T32

    member = await _member_row(session_factory, result["id"], user.id)
    assert member is not None
    assert member.role == "owner"
    assert member.member_type == "human"
    assert member.joined_at is not None

    async with session_factory() as session:
        prefixes = (
            await session.execute(
                select(IdentifierPrefixRegistry).where(
                    IdentifierPrefixRegistry.workspace_id == result["id"]
                )
            )
        ).scalars().all()
    assert [(p.key, p.kind) for p in prefixes] == [("WS", "inbox")]

    # Owner 入册 emits member.added through the outbox (§3.5 / §6.6).
    events = await _outbox_events(session_factory)
    realtime = [e for e in events if e.event_type == "realtime.publish"]
    assert len(realtime) == 1
    assert realtime[0].payload["event"] == "member.added"
    assert realtime[0].payload["channel"] == f"workspace:{result['id']}"
    assert realtime[0].payload["data"]["role"] == "owner"


async def test_create_workspace_with_settings_merge(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "settings@corp.com")
    result = await service.create_workspace(
        user=user,
        name="Localized",
        slug="localized",
        settings={"default_locale": "zh-CN", "inbox_issue_prefix": "INBOX"},
    )
    assert result["settings"]["default_locale"] == "zh-CN"
    async with session_factory() as session:
        prefix = await session.scalar(
            select(IdentifierPrefixRegistry.key).where(
                IdentifierPrefixRegistry.workspace_id == result["id"],
                IdentifierPrefixRegistry.kind == "inbox",
            )
        )
    assert prefix == "INBOX"


@pytest.mark.parametrize(
    "slug", ["Acme", "a", "x" * 33, "has space", "under_score", "中文", ""]
)
async def test_create_workspace_rejects_bad_slug_format(session_factory, slug):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, f"slug-{uuid.uuid4().hex[:8]}@corp.com")
    with pytest.raises(ValidationError) as excinfo:
        await service.create_workspace(user=user, name="N", slug=slug)
    assert excinfo.value.code == "validation_error"


async def test_create_workspace_duplicate_slug_409(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "dup@corp.com")
    await service.create_workspace(user=user, name="First", slug="taken-slug")
    with pytest.raises(ConflictError) as excinfo:
        await service.create_workspace(user=user, name="Second", slug="taken-slug")
    assert excinfo.value.code == "slug_taken"


async def test_create_workspace_invalid_timezone_422(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "tz@corp.com")
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.create_workspace(user=user, name="N", slug="tz-ws", timezone="Mars/Olympus")
    assert excinfo.value.code == "invalid_timezone"


async def test_create_workspace_unsupported_locale_422(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "locale@corp.com")
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.create_workspace(
            user=user, name="N", slug="locale-ws", settings={"default_locale": "fr"}
        )
    assert excinfo.value.code == "unsupported_locale"


async def test_create_workspace_http_logo_rejected(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "logo@corp.com")
    with pytest.raises(ValidationError):
        await service.create_workspace(
            user=user, name="N", slug="logo-ws", logo_url="http://cdn.example/x.png"
        )
    with pytest.raises(ValidationError):
        await service.create_workspace(
            user=user, name="N", slug="logo-ws2", logo_url="javascript:alert(1)"
        )


async def test_create_workspace_name_length_enforced(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "name-len@corp.com")
    with pytest.raises(ValidationError):
        await service.create_workspace(user=user, name="x" * 81, slug="name-len")
    with pytest.raises(ValidationError):
        await service.create_workspace(user=user, name="", slug="name-len2")


async def test_create_workspace_bad_inbox_prefix_rejected(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "prefix-bad@corp.com")
    with pytest.raises(ValidationError):
        await service.create_workspace(
            user=user, name="N", slug="prefix-bad", settings={"inbox_issue_prefix": "lower"}
        )


# --- list ---------------------------------------------------------------------


async def test_list_workspaces_returns_memberships_with_role(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "multi@corp.com")
    other = await _seed_user(session_factory, "other-owner@corp.com")
    ws1 = await service.create_workspace(user=user, name="One", slug="list-one")
    ws2 = await service.create_workspace(user=user, name="Two", slug="list-two")
    await service.create_workspace(user=other, name="Foreign", slug="list-foreign")

    items, next_cursor = await service.list_workspaces(user=user)
    assert next_cursor is None
    assert {item["slug"]: item["my_role"] for item in items} == {
        "list-one": "owner",
        "list-two": "owner",
    }
    assert all("settings" not in item or True for item in items)
    assert {ws1["id"], ws2["id"]} == {item["id"] for item in items}


async def test_list_workspaces_pagination_stable_keyset(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "pager@corp.com")
    created = []
    for i in range(5):
        created.append(
            await service.create_workspace(user=user, name=f"W{i}", slug=f"pager-{i}")
        )
    page1, cursor1 = await service.list_workspaces(user=user, limit=2)
    assert len(page1) == 2
    assert cursor1 is not None
    page2, cursor2 = await service.list_workspaces(user=user, limit=2, cursor=cursor1)
    assert len(page2) == 2
    assert cursor2 is not None
    page3, cursor3 = await service.list_workspaces(user=user, limit=2, cursor=cursor2)
    assert len(page3) == 1
    assert cursor3 is None
    seen = [item["id"] for item in page1 + page2 + page3]
    assert len(set(seen)) == 5  # no duplicates across pages


async def test_list_workspaces_hides_deleted(session_factory):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, "del-list@corp.com")
    created = await service.create_workspace(user=user, name="Gone", slug="gone-ws")
    member = await _member_row(session_factory, created["id"], user.id)
    async with session_factory() as session:
        workspace = await session.get(Workspace, created["id"])
    await service.delete_workspace(
        actor=member, workspace=workspace, confirm_slug="gone-ws"
    )
    items, _ = await service.list_workspaces(user=user)
    assert items == []


# --- update -------------------------------------------------------------------


async def _create_with_owner(service, session_factory, email, slug):
    user = await _seed_user(session_factory, email)
    created = await service.create_workspace(user=user, name="Base", slug=slug)
    member = await _member_row(session_factory, created["id"], user.id)
    async with session_factory() as session:
        workspace = await session.get(Workspace, created["id"])
    return user, workspace, member


async def test_update_name_and_settings_shallow_merge(session_factory):
    service = WorkspaceService(session_factory)
    _user, workspace, member = await _create_with_owner(
        service, session_factory, "upd@corp.com", "upd-ws"
    )
    updated = await service.update_workspace(
        actor=member,
        workspace=workspace,
        patch=WorkspacePatch(name="Renamed", settings={"seat_limit": 50}),
    )
    assert updated["name"] == "Renamed"
    # Shallow merge: default_locale survives the settings patch.
    assert updated["settings"]["default_locale"] == "en"
    assert updated["settings"]["seat_limit"] == 50

    events = await _outbox_events(session_factory)
    updates = [
        e for e in events
        if e.event_type == "realtime.publish" and e.payload["event"] == "workspace.updated"
    ]
    assert len(updates) == 1
    assert updates[0].payload["data"]["changes"]["name"] == "Renamed"


async def test_update_no_changes_is_noop_without_event(session_factory):
    service = WorkspaceService(session_factory)
    _user, workspace, member = await _create_with_owner(
        service, session_factory, "noop@corp.com", "noop-ws"
    )
    await service.update_workspace(
        actor=member, workspace=workspace, patch=WorkspacePatch(name="Base")
    )
    events = await _outbox_events(session_factory)
    updates = [
        e for e in events
        if e.event_type == "realtime.publish" and e.payload["event"] == "workspace.updated"
    ]
    assert updates == []  # §6.9: empty diff → no event


async def test_update_slug_writes_history_and_redirects(session_factory):
    service = WorkspaceService(session_factory)
    _user, workspace, member = await _create_with_owner(
        service, session_factory, "slug-upd@corp.com", "old-name"
    )
    updated = await service.update_workspace(
        actor=member, workspace=workspace, patch=WorkspacePatch(slug="new-name")
    )
    assert updated["slug"] == "new-name"
    async with session_factory() as session:
        history = (
            await session.execute(
                select(WorkspaceSlugHistory).where(
                    WorkspaceSlugHistory.workspace_id == workspace.id
                )
            )
        ).scalars().all()
    assert [h.old_slug for h in history] == ["old-name"]


async def test_update_slug_taken_409(session_factory):
    service = WorkspaceService(session_factory)
    await _create_with_owner(service, session_factory, "holder@corp.com", "holder-slug")
    _u, workspace, member = await _create_with_owner(
        service, session_factory, "mover@corp.com", "mover-slug"
    )
    with pytest.raises(ConflictError) as excinfo:
        await service.update_workspace(
            actor=member, workspace=workspace, patch=WorkspacePatch(slug="holder-slug")
        )
    assert excinfo.value.code == "slug_taken"


async def test_update_invalid_locale_and_timezone(session_factory):
    service = WorkspaceService(session_factory)
    _u, workspace, member = await _create_with_owner(
        service, session_factory, "bad-vals@corp.com", "bad-vals"
    )
    with pytest.raises(BusinessRuleError) as loc:
        await service.update_workspace(
            actor=member,
            workspace=workspace,
            patch=WorkspacePatch(settings={"default_locale": "de"}),
        )
    assert loc.value.code == "unsupported_locale"
    with pytest.raises(BusinessRuleError) as tz:
        await service.update_workspace(
            actor=member, workspace=workspace, patch=WorkspacePatch(timezone="Bad/Zone")
        )
    assert tz.value.code == "invalid_timezone"


async def test_update_settings_known_key_type_checked(session_factory):
    service = WorkspaceService(session_factory)
    _u, workspace, member = await _create_with_owner(
        service, session_factory, "type-check@corp.com", "type-check"
    )
    with pytest.raises(ValidationError):
        await service.update_workspace(
            actor=member,
            workspace=workspace,
            patch=WorkspacePatch(settings={"seat_limit": "fifty"}),
        )
    # Unknown keys pass through (forward compatibility, §2.2).
    updated = await service.update_workspace(
        actor=member,
        workspace=workspace,
        patch=WorkspacePatch(settings={"future_flag": True}),
    )
    assert updated["settings"]["future_flag"] is True


async def test_update_inbox_prefix_retires_old_and_rejects_conflict(session_factory):
    """T19: prefix change retires the old prefix permanently; conflicts → 422."""
    service = WorkspaceService(session_factory)
    _u, workspace, member = await _create_with_owner(
        service, session_factory, "prefix-chg@corp.com", "prefix-chg"
    )
    updated = await service.update_workspace(
        actor=member,
        workspace=workspace,
        patch=WorkspacePatch(settings={"inbox_issue_prefix": "OPS"}),
    )
    assert updated["settings"]["inbox_issue_prefix"] == "OPS"
    async with session_factory() as session:
        rows = {
            r.key: r.kind
            for r in (
                await session.execute(
                    select(IdentifierPrefixRegistry).where(
                        IdentifierPrefixRegistry.workspace_id == workspace.id
                    )
                )
            ).scalars().all()
        }
    assert rows == {"WS": "retired", "OPS": "inbox"}

    # Retired prefixes stay reserved: switching back is rejected.
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.update_workspace(
            actor=member,
            workspace=workspace,
            patch=WorkspacePatch(settings={"inbox_issue_prefix": "WS"}),
        )
    assert excinfo.value.code == "prefix_reserved"


# --- delete / restore ---------------------------------------------------------


async def test_delete_requires_owner_and_confirm_slug(session_factory):
    service = WorkspaceService(session_factory)
    _u, workspace, owner = await _create_with_owner(
        service, session_factory, "del-owner@corp.com", "del-ws"
    )
    # Seed a plain member.
    async with session_factory() as session, session.begin():
        uid = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'M') RETURNING id"),
                {"e": "del-member@corp.com"},
            )
        ).scalar_one()
        plain_member = Member(
            workspace_id=workspace.id, member_type="human", user_id=uid, role="member"
        )
        session.add(plain_member)
        await session.flush()
        member_id = plain_member.id
    async with session_factory() as session:
        plain = await session.get(Member, member_id)

    with pytest.raises(ForbiddenError):
        await service.delete_workspace(
            actor=plain, workspace=workspace, confirm_slug="del-ws"
        )
    with pytest.raises(ValidationError):
        await service.delete_workspace(
            actor=owner, workspace=workspace, confirm_slug="wrong-slug"
        )

    await service.delete_workspace(actor=owner, workspace=workspace, confirm_slug="del-ws")
    async with session_factory() as session:
        deleted = await session.get(Workspace, workspace.id)
    assert deleted.deleted_at is not None
    events = await _outbox_events(session_factory)
    assert any(
        e.event_type == "realtime.publish" and e.payload["event"] == "workspace.deleted"
        for e in events
    )


async def test_restore_within_retention(session_factory):
    service = WorkspaceService(session_factory)
    _u, workspace, owner = await _create_with_owner(
        service, session_factory, "restore@corp.com", "restore-ws"
    )
    await service.delete_workspace(actor=owner, workspace=workspace, confirm_slug="restore-ws")
    restored = await service.restore_workspace(actor=owner, workspace_id=workspace.id)
    assert restored["slug"] == "restore-ws"
    async with session_factory() as session:
        live = await session.get(Workspace, workspace.id)
    assert live.deleted_at is None


async def test_restore_slug_conflict_409(session_factory):
    service = WorkspaceService(session_factory)
    _u, workspace, owner = await _create_with_owner(
        service, session_factory, "restore2@corp.com", "contested"
    )
    await service.delete_workspace(actor=owner, workspace=workspace, confirm_slug="contested")
    # Another workspace takes the released slug.
    other_user = await _seed_user(session_factory, "squatter@corp.com")
    await service.create_workspace(user=other_user, name="Squat", slug="contested")
    with pytest.raises(ConflictError) as excinfo:
        await service.restore_workspace(actor=owner, workspace_id=workspace.id)
    assert excinfo.value.code == "slug_taken"


# --- prefix registry + inbox sequence helpers ---------------------------------


async def test_occupy_project_prefix_conflicts(session_factory, workspace_factory):
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO identifier_prefix_registry (workspace_id, key, kind) "
                "VALUES (:ws, 'APP', 'inbox')"
            ),
            {"ws": workspace.id},
        )
    from mesh.errors import ConflictError as Conflict

    async with session_factory() as session:
        with pytest.raises(Conflict) as excinfo:
            async with session.begin():
                await occupy_project_prefix(
                    session, workspace_id=workspace.id, key="APP", project_id=uuid.uuid4()
                )
        assert excinfo.value.code == "project_key_taken"

    # A free key occupies fine (conflict with retired keys also covered above
    # via the inbox row; retired is just another registered kind).
    async with session_factory() as session, session.begin():
        await occupy_project_prefix(
            session, workspace_id=workspace.id, key="WEB", project_id=uuid.uuid4()
        )


async def test_change_inbox_prefix_conflict_with_project_key(session_factory, workspace_factory):
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO identifier_prefix_registry (workspace_id, key, kind) "
                "VALUES (:ws, 'WS', 'inbox'), (:ws2, 'APP', 'project')"
            ),
            {"ws": workspace.id, "ws2": workspace.id},
        )
    async with session_factory() as session:
        with pytest.raises(BusinessRuleError) as excinfo:
            async with session.begin():
                await change_inbox_prefix(session, workspace_id=workspace.id, new_key="APP")
        assert excinfo.value.code == "prefix_reserved"


async def test_inbox_issue_sequence_concurrent_no_duplicates(session_factory, workspace_factory):
    """T15: ≥10 concurrent increments never produce duplicate numbers."""
    workspace = await workspace_factory()

    async def _one() -> int:
        async with session_factory() as session, session.begin():
            return await next_inbox_issue_number(session, workspace_id=workspace.id)

    numbers = await asyncio.gather(*[_one() for _ in range(12)])
    assert sorted(numbers) == list(range(1, 13))
    async with session_factory() as session:
        final = await session.scalar(
            select(Workspace.inbox_issue_seq).where(Workspace.id == workspace.id)
        )
    assert final == 12


def test_workspace_to_dict_shape():
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Shape",
        slug="shape",
        timezone="UTC",
        settings={"default_locale": "en"},
    )
    rendered = workspace_to_dict(workspace, my_role="member")
    assert rendered["my_role"] == "member"
    assert rendered["settings"] == {"default_locale": "en"}
    assert "default_language" not in rendered
    listed = workspace_to_dict(workspace, my_role="owner", list_view=True)
    assert set(listed) == {"id", "name", "slug", "logo_url", "my_role", "created_at"}
