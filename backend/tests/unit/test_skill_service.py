"""Skill service tests — definitions, sources, immutable versions (skill.md §2-§4).

Real PostgreSQL, nothing mocked. Covers the first two decoupling layers:
skill CRUD + lifecycle (§4.4), user-source auto-provisioning (K2), slug
uniqueness, immutable version minting with the same-parent current_version
pointer, duplicate-version 409s, history listing and the §3.5 events.
"""

from __future__ import annotations

import uuid

import pytest
from skill_test_support import make_member, make_workspace
from sqlalchemy import select

from mesh.db.models.audit import AuditLog
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.skill import Skill, SkillSource
from mesh.errors import (
    ConflictError,
    ForbiddenError,
    LockedError,
    NotFoundError,
    ValidationError,
)
from mesh.skill.content_store import InMemoryContentStore
from mesh.skill.service import SkillPatch, SkillService, slugify


@pytest.fixture
def skill_service(session_factory) -> SkillService:
    return SkillService(session_factory)


@pytest.fixture
def content_store() -> InMemoryContentStore:
    return InMemoryContentStore()


async def _events(session_factory, name: str) -> list:
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEvent))).scalars().all()
    return [
        e for e in rows if e.event_type == "realtime.publish" and e.payload["event"] == name
    ]


def _version_payload(**overrides) -> dict:
    payload = {
        "version": "1.0.0",
        "instructions": "## SOP\n1. review\n2. merge",
        "scripts": [
            {
                "path": "scripts/check.sh",
                "runtime": "shell",
                "entrypoint": True,
                "required_capabilities": ["exec:shell"],
            }
        ],
        "references": [{"path": "docs/runbook.md", "media_type": "text/markdown",
                        "summary": "runbook"}],
        "triggers": [{"trigger_type": "keyword", "pattern": "评审 review", "weight": 1.5}],
        "required_capabilities": ["exec:shell", "read:code"],
        "changelog": "first release",
    }
    payload.update(overrides)
    return payload


class TestCreate:
    async def test_create_provisions_user_source_and_draft(
        self, skill_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin,
            workspace_id=workspace.id,
            name="代码评审规范",
            summary="评审 SOP",
            tags=["review"],
            required_capabilities=["read:code"],
        )
        assert created["status"] == "draft"
        assert created["source_type"] == "user"
        assert created["trust_level"] == "reviewed"
        assert created["slug"] == "代码评审规范" or created["slug"].startswith("skill-")
        assert created["current_version_id"] is None
        # The per-workspace user source exists exactly once.
        async with session_factory() as session:
            sources = (
                await session.execute(
                    select(SkillSource).where(SkillSource.workspace_id == workspace.id)
                )
            ).scalars().all()
        assert len(sources) == 1
        assert sources[0].source_type == "user"

    async def test_create_emits_skill_changed_and_audits(
        self, skill_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="S", summary="s"
        )
        events = await _events(session_factory, "skill.changed")
        assert len(events) == 1
        assert events[0].payload["data"]["change_type"] == "created"
        assert events[0].payload["data"]["skill_id"] == created["id"]
        async with session_factory() as session:
            audits = (await session.execute(select(AuditLog))).scalars().all()
        assert any(a.action == "skill.created" for a in audits)

    async def test_explicit_slug_kept(self, skill_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s",
            slug="code-review-sop",
        )
        assert created["slug"] == "code-review-sop"

    async def test_invalid_slug_rejected(self, skill_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        with pytest.raises(ValidationError):
            await skill_service.create_skill(
                actor=admin, workspace_id=workspace.id, name="N", summary="s",
                slug="Not A Slug",
            )

    async def test_slug_collision_gets_suffix(self, skill_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        first = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="A", summary="s", slug="dup"
        )
        second = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="B", summary="s", slug="dup"
        )
        assert first["slug"] == "dup"
        assert second["slug"] == "dup-2"

    async def test_member_cannot_create(self, skill_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        member = await make_member(session_factory, workspace, role="member")
        with pytest.raises(ForbiddenError):
            await skill_service.create_skill(
                actor=member, workspace_id=workspace.id, name="N", summary="s"
            )


class TestUpdate:
    async def test_metadata_update_and_noop(self, skill_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s"
        )
        updated = await skill_service.update_skill(
            actor=admin,
            workspace_id=workspace.id,
            skill_id=uuid.UUID(created["id"]),
            patch=SkillPatch(name="Renamed", tags=["a", "b"]),
        )
        assert updated["name"] == "Renamed"
        assert updated["tags"] == ["a", "b"]
        # No-op patch returns the same shape without a new event.
        before = len(await _events(session_factory, "skill.changed"))
        again = await skill_service.update_skill(
            actor=admin,
            workspace_id=workspace.id,
            skill_id=uuid.UUID(created["id"]),
            patch=SkillPatch(name="Renamed"),
        )
        assert again["name"] == "Renamed"
        assert len(await _events(session_factory, "skill.changed")) == before

    async def test_lifecycle_published_requires_version_flow(
        self, skill_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s"
        )
        # draft → published is legal via PATCH too (§4.4 edge).
        published = await skill_service.update_skill(
            actor=admin, workspace_id=workspace.id, skill_id=uuid.UUID(created["id"]),
            patch=SkillPatch(status="published"),
        )
        assert published["status"] == "published"
        # published → draft is NOT a legal edge → 409.
        with pytest.raises(ConflictError):
            await skill_service.update_skill(
                actor=admin, workspace_id=workspace.id,
                skill_id=uuid.UUID(created["id"]), patch=SkillPatch(status="draft"),
            )

    async def test_invalid_status_value(self, skill_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s"
        )
        with pytest.raises(ValidationError):
            await skill_service.update_skill(
                actor=admin, workspace_id=workspace.id,
                skill_id=uuid.UUID(created["id"]), patch=SkillPatch(status="exploded"),
            )


class TestDelete:
    async def test_delete_requires_deprecated_or_disabled(
        self, skill_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s"
        )
        with pytest.raises(LockedError):
            await skill_service.delete_skill(
                actor=admin, workspace_id=workspace.id, skill_id=uuid.UUID(created["id"])
            )
        await skill_service.update_skill(
            actor=admin, workspace_id=workspace.id, skill_id=uuid.UUID(created["id"]),
            patch=SkillPatch(status="published"),
        )
        await skill_service.update_skill(
            actor=admin, workspace_id=workspace.id, skill_id=uuid.UUID(created["id"]),
            patch=SkillPatch(status="disabled"),
        )
        await skill_service.delete_skill(
            actor=admin, workspace_id=workspace.id, skill_id=uuid.UUID(created["id"])
        )
        async with session_factory() as session:
            skill = await session.scalar(select(Skill))
        assert skill.deleted_at is not None


class TestList:
    async def test_filters_and_pagination(self, skill_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        for i in range(3):
            await skill_service.create_skill(
                actor=admin, workspace_id=workspace.id, name=f"skill-{i}",
                summary=f"sum {i}", slug=f"sk-{i}",
            )
        items, cursor = await skill_service.list_skills(
            workspace_id=workspace.id, limit=2
        )
        assert len(items) == 2
        assert cursor is not None
        rest, next_cursor = await skill_service.list_skills(
            workspace_id=workspace.id, limit=2, cursor=cursor
        )
        assert len(rest) == 1
        assert next_cursor is None
        # q filter matches name/summary.
        hits, _ = await skill_service.list_skills(workspace_id=workspace.id, q="skill-1")
        assert [h["slug"] for h in hits] == ["sk-1"]
        # invalid status filter → 400.
        with pytest.raises(ValidationError):
            await skill_service.list_skills(workspace_id=workspace.id, status="bogus")

    async def test_other_workspace_invisible(self, skill_service, session_factory) -> None:
        ws_a = await make_workspace(session_factory)
        ws_b = await make_workspace(session_factory)
        admin = await make_member(session_factory, ws_a, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=ws_a.id, name="N", summary="s"
        )
        items, _ = await skill_service.list_skills(workspace_id=ws_b.id)
        assert items == []
        with pytest.raises(NotFoundError):
            await skill_service.get_skill(
                workspace_id=ws_b.id, skill_id=uuid.UUID(created["id"])
            )


class TestVersions:
    async def test_create_and_publish_version(
        self, skill_service, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s",
            required_capabilities=["exec:shell", "read:code"],
        )
        version = await skill_service.create_version(
            actor=admin,
            workspace_id=workspace.id,
            skill_id=uuid.UUID(created["id"]),
            manifest=_version_payload(),
            script_bodies={"scripts/check.sh": b"#!/bin/sh\necho ok"},
            reference_bodies={"docs/runbook.md": b"# runbook"},
            content_store=content_store,
            publish=True,
        )
        assert version["status"] == "published"
        assert len(version["content_hash"]) == 64
        # Skill moved to published and points at the version (same-parent FK).
        detail = await skill_service.get_skill(
            workspace_id=workspace.id, skill_id=uuid.UUID(created["id"])
        )
        assert detail["status"] == "published"
        assert detail["current_version_id"] == version["id"]
        assert detail["current_version"] == "1.0.0"
        assert detail["has_scripts"] is True

    async def test_draft_version_does_not_publish_skill(
        self, skill_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s"
        )
        version = await skill_service.create_version(
            actor=admin, workspace_id=workspace.id, skill_id=uuid.UUID(created["id"]),
            manifest=_version_payload(),
        )
        assert version["status"] == "draft"
        detail = await skill_service.get_skill(
            workspace_id=workspace.id, skill_id=uuid.UUID(created["id"])
        )
        assert detail["status"] == "draft"
        assert detail["current_version_id"] is None

    async def test_duplicate_version_number_conflict(
        self, skill_service, session_factory
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s"
        )
        await skill_service.create_version(
            actor=admin, workspace_id=workspace.id, skill_id=uuid.UUID(created["id"]),
            manifest=_version_payload(), publish=True,
        )
        with pytest.raises(ConflictError) as exc_info:
            await skill_service.create_version(
                actor=admin, workspace_id=workspace.id,
                skill_id=uuid.UUID(created["id"]),
                manifest=_version_payload(instructions="different"), publish=True,
            )
        assert exc_info.value.code == "version_conflict"

    async def test_invalid_manifest_rejected(self, skill_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s"
        )
        from mesh.errors import BusinessRuleError

        with pytest.raises(BusinessRuleError):
            await skill_service.create_version(
                actor=admin, workspace_id=workspace.id,
                skill_id=uuid.UUID(created["id"]),
                manifest={"version": "1.0.0", "instructions": "x",
                          "scripts": [{"path": "a.sh", "runtime": "cobol"}]},
            )

    async def test_version_history_and_detail(
        self, skill_service, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        created = await skill_service.create_skill(
            actor=admin, workspace_id=workspace.id, name="N", summary="s",
            required_capabilities=["exec:shell", "read:code"],
        )
        v1 = await skill_service.create_version(
            actor=admin, workspace_id=workspace.id, skill_id=uuid.UUID(created["id"]),
            manifest=_version_payload(version="1.0.0"),
            script_bodies={"scripts/check.sh": b"v1"},
            content_store=content_store, publish=True,
        )
        v2 = await skill_service.create_version(
            actor=admin, workspace_id=workspace.id, skill_id=uuid.UUID(created["id"]),
            manifest=_version_payload(version="1.1.0"),
            script_bodies={"scripts/check.sh": b"v2"},
            content_store=content_store, publish=True,
        )
        items, cursor = await skill_service.list_versions(
            workspace_id=workspace.id, skill_id=uuid.UUID(created["id"])
        )
        assert cursor is None
        # Newest first; the current flag tracks the pointer.
        assert [v["version"] for v in items] == ["1.1.0", "1.0.0"]
        assert items[0]["is_current"] is True
        assert items[1]["is_current"] is False

        detail = await skill_service.get_version(
            workspace_id=workspace.id,
            skill_id=uuid.UUID(created["id"]),
            version_id=uuid.UUID(v2["id"]),
            include_content=True,
            content_store=content_store,
        )
        assert detail["scripts"][0]["path"] == "scripts/check.sh"
        assert detail["scripts"][0]["content"] == "v2"
        assert detail["references"][0]["path"] == "docs/runbook.md"
        assert detail["triggers"][0]["pattern"] == "评审 review"
        # v1 history is intact (immutable, never deleted).
        detail_v1 = await skill_service.get_version(
            workspace_id=workspace.id,
            skill_id=uuid.UUID(created["id"]),
            version_id=uuid.UUID(v1["id"]),
        )
        assert detail_v1["version"] == "1.0.0"


class TestSlugify:
    def test_ascii_name(self) -> None:
        assert slugify("Code Review SOP") == "code-review-sop"

    def test_non_ascii_fallback(self) -> None:
        slug = slugify("代码评审")
        assert slug.startswith("skill-")

    def test_empty_fallback(self) -> None:
        assert slugify("!!!").startswith("skill-")
