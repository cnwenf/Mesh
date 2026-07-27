"""Installation service tests — the third decoupling layer (skill.md §2.4/§4.3/§4.4).

Real DB. Covers the install gate matrix (draft/disabled → 423, unapproved
untrusted scripts → 422 approval_required, agent-scope 400, duplicate 409),
grants ⊆ required (422 capability_not_declared), rollback to historic
versions, the update_available / auto_update sweep (§4.4) and
skill.update_available broadcasts.
"""

from __future__ import annotations

import uuid

import pytest
from skill_test_support import make_member, make_workspace
from sqlalchemy import select

from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.skill import Skill, SkillInstallation, SkillSource, SkillVersion
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    LockedError,
    NotFoundError,
    ValidationError,
)
from mesh.skill.installations import InstallationService
from mesh.skill.service import SkillService


@pytest.fixture
def skill_service(session_factory) -> SkillService:
    return SkillService(session_factory)


@pytest.fixture
def installation_service(session_factory) -> InstallationService:
    return InstallationService(session_factory)


async def _events(session_factory, name: str) -> list:
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEvent))).scalars().all()
    return [
        e for e in rows if e.event_type == "realtime.publish" and e.payload["event"] == name
    ]


async def _make_skill(
    session_factory,
    workspace,
    member: Member,
    *,
    status: str = "published",
    source_type: str = "user",
    trust_level: str | None = None,
) -> tuple[Skill, SkillVersion]:
    """Seed a skill + one published version directly (raw rows, full control)."""
    from mesh.db.models.skill import TRUST_LEVEL_BY_SOURCE_TYPE

    async with session_factory() as session, session.begin():
        source = SkillSource(
            workspace_id=workspace.id,
            source_type=source_type,
            name=f"src-{uuid.uuid4().hex[:8]}",
            uri=f"https://reg.example.com/{uuid.uuid4().hex}" if source_type != "user" else None,
            trust_level=trust_level or TRUST_LEVEL_BY_SOURCE_TYPE[source_type],
        )
        session.add(source)
        await session.flush()
        skill = Skill(
            workspace_id=workspace.id,
            source_id=source.id,
            name=f"skill-{uuid.uuid4().hex[:6]}",
            slug=f"sk-{uuid.uuid4().hex[:10]}",
            summary="s",
            status=status,
            required_capabilities=["exec:shell", "net:outbound"],
            created_by=member.id,
        )
        session.add(skill)
        await session.flush()
        version = SkillVersion(
            workspace_id=workspace.id,
            skill_id=skill.id,
            version="1.0.0",
            instructions="do it",
            status="published" if status == "published" else "draft",
            required_capabilities=["exec:shell", "net:outbound"],
            content_hash="a" * 64,
            created_by=member.id,
        )
        session.add(version)
        await session.flush()
        if status == "published":
            skill.current_version_id = version.id
    return skill, version


async def _add_version(
    session_factory, workspace, skill: Skill, member: Member, *,
    version: str, instructions: str = "do it v2",
    script_hashes: list[tuple[str, str]] | None = None,
    status: str = "published",
) -> SkillVersion:
    from mesh.db.models.skill import SkillScript

    async with session_factory() as session, session.begin():
        row = SkillVersion(
            workspace_id=workspace.id,
            skill_id=skill.id,
            version=version,
            instructions=instructions,
            status=status,
            required_capabilities=["exec:shell", "net:outbound"],
            content_hash=uuid.uuid4().hex * 2,
            created_by=member.id,
        )
        session.add(row)
        await session.flush()
        for path, content_hash in script_hashes or []:
            session.add(
                SkillScript(
                    skill_version_id=row.id,
                    path=path,
                    runtime="shell",
                    content_ref=f"mem:{path}",
                    content_hash=content_hash,
                )
            )
        skill.current_version_id = row.id
    return row


class TestInstallGate:
    async def test_install_workspace_scope(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, version = await _make_skill(session_factory, workspace, admin)
        installed = await installation_service.install(
            actor=admin,
            workspace_id=workspace.id,
            skill_id=skill.id,
            skill_version_id=version.id,
        )
        assert installed["install_status"] == "installed"
        assert installed["scope"] == "workspace"
        # Reviewed source grants the full declared surface.
        assert set(installed["granted_capabilities"]) == {"exec:shell", "net:outbound"}
        events = await _events(session_factory, "skill.changed")
        assert any(
            e.payload["data"]["change_type"] == "installed" for e in events
        )

    async def test_draft_skill_locked(self, installation_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, version = await _make_skill(
            session_factory, workspace, admin, status="draft"
        )
        with pytest.raises(LockedError):
            await installation_service.install(
                actor=admin, workspace_id=workspace.id,
                skill_id=skill.id, skill_version_id=version.id,
            )

    async def test_disabled_skill_locked(self, installation_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, version = await _make_skill(
            session_factory, workspace, admin, status="disabled"
        )
        with pytest.raises(LockedError):
            await installation_service.install(
                actor=admin, workspace_id=workspace.id,
                skill_id=skill.id, skill_version_id=version.id,
            )

    async def test_untrusted_scripts_require_approval(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, version = await _make_skill(
            session_factory, workspace, admin, status="draft", source_type="url"
        )
        # Seed one script row so the version "contains scripts".
        from mesh.db.models.skill import SkillScript

        async with session_factory() as session, session.begin():
            session.add(
                SkillScript(
                    skill_version_id=version.id, path="s.sh", runtime="shell",
                    content_ref="mem:s.sh", content_hash="b" * 64,
                )
            )
        # No approved import task → 422 approval_required (before the 423).
        with pytest.raises(BusinessRuleError) as exc_info:
            await installation_service.install(
                actor=admin, workspace_id=workspace.id,
                skill_id=skill.id, skill_version_id=version.id,
            )
        assert exc_info.value.code == "approval_required"

    async def test_agent_scope_requires_agent_id(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, version = await _make_skill(session_factory, workspace, admin)
        with pytest.raises(ValidationError):
            await installation_service.install(
                actor=admin, workspace_id=workspace.id,
                skill_id=skill.id, skill_version_id=version.id, scope="agent",
            )

    async def test_duplicate_scope_conflict(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, version = await _make_skill(session_factory, workspace, admin)
        await installation_service.install(
            actor=admin, workspace_id=workspace.id,
            skill_id=skill.id, skill_version_id=version.id,
        )
        with pytest.raises(ConflictError):
            await installation_service.install(
                actor=admin, workspace_id=workspace.id,
                skill_id=skill.id, skill_version_id=version.id,
            )

    async def test_grants_subset_enforced(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, version = await _make_skill(
            session_factory, workspace, admin, source_type="url"
        )
        # Forge an import task granting an undeclared capability — the subset
        # check must catch it even though a (bogus) approval exists.
        from datetime import UTC, datetime

        from mesh.db.models.skill import SkillImportTask, SkillScript

        async with session_factory() as session, session.begin():
            session.add(
                SkillScript(
                    skill_version_id=version.id, path="s.sh", runtime="shell",
                    content_ref="mem:s.sh", content_hash="c" * 64,
                )
            )
            session.add(
                SkillImportTask(
                    workspace_id=workspace.id,
                    created_by=admin.id,
                    source_type="url",
                    status="ready",
                    requires_approval=True,
                    reviewed_by=admin.id,
                    reviewed_at=datetime.now(UTC),
                    skill_id=skill.id,
                    skill_version_id=version.id,
                    granted_capabilities=["exec:shell", "root:everything"],
                )
            )
        with pytest.raises(BusinessRuleError) as exc_info:
            await installation_service.install(
                actor=admin, workspace_id=workspace.id,
                skill_id=skill.id, skill_version_id=version.id,
            )
        assert exc_info.value.code == "capability_not_declared"

    async def test_member_cannot_install(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        member = await make_member(session_factory, workspace, role="member")
        skill, version = await _make_skill(session_factory, workspace, admin)
        from mesh.errors import ForbiddenError

        with pytest.raises(ForbiddenError):
            await installation_service.install(
                actor=member, workspace_id=workspace.id,
                skill_id=skill.id, skill_version_id=version.id,
            )


class TestRollbackAndToggle:
    async def test_rollback_to_historic_version(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, v1 = await _make_skill(session_factory, workspace, admin)
        v2 = await _add_version(session_factory, workspace, skill, admin, version="1.1.0")
        installed = await installation_service.install(
            actor=admin, workspace_id=workspace.id,
            skill_id=skill.id, skill_version_id=v2.id,
        )
        rolled = await installation_service.rollback(
            actor=admin,
            workspace_id=workspace.id,
            installation_id=uuid.UUID(installed["id"]),
            target_version_id=v1.id,
            reason="regression",
        )
        assert rolled["skill_version_id"] == str(v1.id)
        assert rolled["previous_version_id"] == str(v2.id)
        assert rolled["install_status"] == "installed"

    async def test_rollback_foreign_version_rejected(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, _ = await _make_skill(session_factory, workspace, admin)
        other_skill, other_version = await _make_skill(session_factory, workspace, admin)
        own_new = await _add_version(
            session_factory, workspace, skill, admin, version="1.0.1"
        )
        installed = await installation_service.install(
            actor=admin, workspace_id=workspace.id,
            skill_id=skill.id, skill_version_id=own_new.id,
        )
        with pytest.raises(NotFoundError):
            await installation_service.rollback(
                actor=admin, workspace_id=workspace.id,
                installation_id=uuid.UUID(installed["id"]),
                target_version_id=other_version.id,
            )

    async def test_disable_and_reenable(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, version = await _make_skill(session_factory, workspace, admin)
        installed = await installation_service.install(
            actor=admin, workspace_id=workspace.id,
            skill_id=skill.id, skill_version_id=version.id,
        )
        disabled = await installation_service.update_installation(
            actor=admin, workspace_id=workspace.id,
            installation_id=uuid.UUID(installed["id"]), install_status="disabled",
        )
        assert disabled["install_status"] == "disabled"
        enabled = await installation_service.update_installation(
            actor=admin, workspace_id=workspace.id,
            installation_id=uuid.UUID(installed["id"]), install_status="installed",
        )
        assert enabled["install_status"] == "installed"

    async def test_uninstall_soft_deletes(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, version = await _make_skill(session_factory, workspace, admin)
        installed = await installation_service.install(
            actor=admin, workspace_id=workspace.id,
            skill_id=skill.id, skill_version_id=version.id,
        )
        await installation_service.uninstall(
            actor=admin, workspace_id=workspace.id,
            installation_id=uuid.UUID(installed["id"]),
        )
        async with session_factory() as session:
            row = await session.scalar(select(SkillInstallation))
        assert row.deleted_at is not None
        # Re-install into the freed scope now succeeds.
        again = await installation_service.install(
            actor=admin, workspace_id=workspace.id,
            skill_id=skill.id, skill_version_id=version.id,
        )
        assert again["install_status"] == "installed"


class TestUpdateSweep:
    async def test_new_version_marks_updated_available(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, v1 = await _make_skill(session_factory, workspace, admin)
        await installation_service.install(
            actor=admin, workspace_id=workspace.id,
            skill_id=skill.id, skill_version_id=v1.id,
        )
        v2 = await _add_version(session_factory, workspace, skill, admin, version="1.1.0")
        from datetime import UTC, datetime

        async with session_factory() as session, session.begin():
            await installation_service.notify_new_version(
                session, workspace_id=workspace.id, skill=skill,
                new_version=v2, now=datetime.now(UTC),
            )
        async with session_factory() as session:
            row = await session.scalar(select(SkillInstallation))
        assert row.install_status == "updated_available"
        events = await _events(session_factory, "skill.update_available")
        assert len(events) == 1
        assert events[0].payload["data"]["new_version"] == "1.1.0"

    async def test_auto_update_follows_non_breaking_patch(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, v1 = await _make_skill(session_factory, workspace, admin)
        # Identical script hashes across versions → non-breaking.
        hashes = [("s.sh", "d" * 64)]
        from mesh.db.models.skill import SkillScript

        async with session_factory() as session, session.begin():
            session.add(
                SkillScript(
                    skill_version_id=v1.id, path="s.sh", runtime="shell",
                    content_ref="mem:s.sh", content_hash="d" * 64,
                )
            )
        await installation_service.install(
            actor=admin, workspace_id=workspace.id,
            skill_id=skill.id, skill_version_id=v1.id, auto_update=True,
        )
        v101 = await _add_version(
            session_factory, workspace, skill, admin, version="1.0.1",
            script_hashes=hashes,
        )
        from datetime import UTC, datetime

        async with session_factory() as session, session.begin():
            await installation_service.notify_new_version(
                session, workspace_id=workspace.id, skill=skill,
                new_version=v101, now=datetime.now(UTC),
            )
        async with session_factory() as session:
            row = await session.scalar(select(SkillInstallation))
        assert row.skill_version_id == v101.id
        assert row.install_status == "installed"
        events = await _events(session_factory, "skill.changed")
        assert any(
            e.payload["data"]["change_type"] == "auto_updated" for e in events
        )

    async def test_auto_update_refuses_changed_scripts(
        self, installation_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        skill, v1 = await _make_skill(session_factory, workspace, admin)
        from mesh.db.models.skill import SkillScript

        async with session_factory() as session, session.begin():
            session.add(
                SkillScript(
                    skill_version_id=v1.id, path="s.sh", runtime="shell",
                    content_ref="mem:s.sh", content_hash="e" * 64,
                )
            )
        await installation_service.install(
            actor=admin, workspace_id=workspace.id,
            skill_id=skill.id, skill_version_id=v1.id, auto_update=True,
        )
        # PATCH bump but the script CHANGED — must NOT auto-follow.
        v101 = await _add_version(
            session_factory, workspace, skill, admin, version="1.0.1",
            script_hashes=[("s.sh", "f" * 64)],
        )
        from datetime import UTC, datetime

        async with session_factory() as session, session.begin():
            await installation_service.notify_new_version(
                session, workspace_id=workspace.id, skill=skill,
                new_version=v101, now=datetime.now(UTC),
            )
        async with session_factory() as session:
            row = await session.scalar(select(SkillInstallation))
        assert row.skill_version_id == v1.id
        assert row.install_status == "updated_available"
