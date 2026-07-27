"""Import pipeline tests — async state machine + approval gate (skill.md §3.1/§3.5/§5.3).

Real DB with an injected fetcher (the SSRF-guarded network path is covered
by test_skill_ssrf.py; e2e exercises the real HTTP fetch). Covers:
instructions-only import → ready + published; script import →
awaiting_review + skill.approval_required; approve/reject; grant subset
422; manifest failures; re-import idempotence + version_conflict;
marketplace listing.
"""

from __future__ import annotations

import json
import uuid

import pytest
from skill_test_support import make_member, make_workspace
from sqlalchemy import select

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.skill import Skill, SkillVersion
from mesh.errors import BusinessRuleError, ConflictError, ValidationError
from mesh.skill.content_store import InMemoryContentStore
from mesh.skill.importer import ImportService, ImportSettings
from mesh.skill.installations import InstallationService


def _fetcher_for(files: dict[str, bytes]):
    async def fetch(url: str, allowlist: frozenset[str]) -> bytes:
        # Match on suffix — the manifest URL base is prepended for children.
        for suffix, body in files.items():
            if url.endswith(suffix):
                return body
        raise AssertionError(f"unexpected fetch: {url}")

    return fetch


def _manifest_files(**overrides) -> dict[str, bytes]:
    manifest = {
        "name": "发布检查清单",
        "version": "1.3.0",
        "summary": "发布前的标准检查流程",
        "instructions": "## 发布前检查\n1. 运行回归测试",
        "scripts": [
            {
                "path": "scripts/check.sh",
                "runtime": "shell",
                "entrypoint": True,
                "required_capabilities": ["exec:shell", "net:outbound"],
            }
        ],
        "references": [{"path": "docs/runbook.md", "media_type": "text/markdown"}],
        "required_capabilities": ["exec:shell", "net:outbound"],
    }
    manifest.update(overrides)
    return {
        "manifest.json": json.dumps(manifest).encode(),
        "scripts/check.sh": b"#!/bin/sh\necho checking",
        "docs/runbook.md": b"# Runbook",
    }


@pytest.fixture
def content_store() -> InMemoryContentStore:
    return InMemoryContentStore()


def _import_service(session_factory, content_store, files: dict[str, bytes]) -> ImportService:
    return ImportService(
        session_factory,
        content_store=content_store,
        settings=ImportSettings(
            fetcher=_fetcher_for(files),
            # The fixture registry host is explicitly trusted — the injected
            # fetcher replaces the network, the allowlist short-circuits the
            # DNS step of the SSRF guard (still exercised in test_skill_ssrf).
            host_allowlist=frozenset({"reg.example.com", "m.example.com",
                                      "market.example.com"}),
        ),
        installation_service=InstallationService(session_factory),
    )


async def _events(session_factory, name: str) -> list:
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEvent))).scalars().all()
    return [
        e for e in rows if e.event_type == "realtime.publish" and e.payload["event"] == name
    ]


class TestPipeline:
    async def test_script_import_waits_for_review(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        service = _import_service(session_factory, content_store, _manifest_files())
        task = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/skills/release-checklist/manifest.json",
        )
        assert task["status"] == "awaiting_review"
        assert task["requires_approval"] is True
        preview = task["preview"]
        assert preview["version"] == "1.3.0"
        assert preview["scripts"][0]["path"] == "scripts/check.sh"
        assert set(preview["requested_capabilities"]) == {"exec:shell", "net:outbound"}
        # Skill + version rows exist but stay DRAFT until approval.
        async with session_factory() as session:
            skill = await session.scalar(select(Skill))
            version = await session.scalar(select(SkillVersion))
        assert skill.status == "draft"
        assert version.status == "draft"
        # skill.approval_required was broadcast (§3.5).
        events = await _events(session_factory, "skill.approval_required")
        assert len(events) == 1
        assert events[0].payload["data"]["task_id"] == task["task_id"]
        # Progress events were emitted per stage.
        progress = await _events(session_factory, "skill_import.progress")
        assert len(progress) >= 3

    async def test_instructions_only_import_is_ready(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        files = _manifest_files(scripts=[], required_capabilities=[])
        service = _import_service(session_factory, content_store, files)
        task = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/docs-skill/manifest.json",
        )
        assert task["status"] == "ready"
        assert task["requires_approval"] is False
        async with session_factory() as session:
            skill = await session.scalar(select(Skill))
        assert skill.status == "published"

    async def test_approve_publishes_with_minimized_grants(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        service = _import_service(session_factory, content_store, _manifest_files())
        task = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        approved = await service.approve(
            actor=admin, workspace_id=workspace.id,
            skill_id=uuid.UUID(task["skill_id"]),
            task_id=uuid.UUID(task["task_id"]),
            granted_capabilities=["exec:shell"],  # net:outbound refused
            decision="approve",
            comment="拒绝出站网络,仅允许只读 shell",
        )
        assert approved["status"] == "ready"
        assert approved["granted_capabilities"] == ["exec:shell"]
        assert approved["reviewed_by"] == str(admin.id)
        async with session_factory() as session:
            skill = await session.scalar(select(Skill))
            version = await session.scalar(select(SkillVersion))
        assert skill.status == "published"
        assert version.status == "published"
        assert skill.current_version_id == version.id

    async def test_approve_undeclared_grant_refused(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        service = _import_service(session_factory, content_store, _manifest_files())
        task = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        with pytest.raises(BusinessRuleError) as exc_info:
            await service.approve(
                actor=admin, workspace_id=workspace.id,
                skill_id=uuid.UUID(task["skill_id"]),
                task_id=uuid.UUID(task["task_id"]),
                granted_capabilities=["exec:shell", "root:everything"],
            )
        assert exc_info.value.code == "capability_not_declared"

    async def test_reject_terminates_task(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        service = _import_service(session_factory, content_store, _manifest_files())
        task = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        rejected = await service.approve(
            actor=admin, workspace_id=workspace.id,
            skill_id=uuid.UUID(task["skill_id"]),
            task_id=uuid.UUID(task["task_id"]),
            granted_capabilities=[],
            decision="reject",
            comment="malicious script",
        )
        assert rejected["status"] == "rejected"
        async with session_factory() as session:
            skill = await session.scalar(select(Skill))
        assert skill.status == "draft"  # never installable

    async def test_approve_twice_conflict(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        service = _import_service(session_factory, content_store, _manifest_files())
        task = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        await service.approve(
            actor=admin, workspace_id=workspace.id,
            skill_id=uuid.UUID(task["skill_id"]),
            task_id=uuid.UUID(task["task_id"]),
            granted_capabilities=["exec:shell"],
        )
        with pytest.raises(ConflictError):
            await service.approve(
                actor=admin, workspace_id=workspace.id,
                skill_id=uuid.UUID(task["skill_id"]),
                task_id=uuid.UUID(task["task_id"]),
                granted_capabilities=["exec:shell"],
            )


class TestFailures:
    async def test_invalid_json_fails_task(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        service = _import_service(
            session_factory, content_store, {"manifest.json": b"not json {"}
        )
        task = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        assert task["status"] == "failed"
        assert "manifest_invalid" in task["error"]

    async def test_semantic_manifest_failure(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        bad = {"manifest.json": json.dumps(
            {"name": "x", "version": "1.0.0",
             "scripts": [{"path": "a.sh", "runtime": "cobol"}]}
        ).encode()}
        service = _import_service(session_factory, content_store, bad)
        task = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        assert task["status"] == "failed"
        assert "manifest_invalid" in task["error"]

    async def test_ssrf_refusal_fails_task(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        service = _import_service(session_factory, content_store, _manifest_files())
        # start_import validates the URI up front → 502 source_unreachable.
        from mesh.skill.ssrf import SourceUnreachableError

        with pytest.raises(SourceUnreachableError):
            await service.start_import(
                actor=admin, workspace_id=workspace.id, source_type="url",
                uri="http://169.254.169.254/latest/meta-data/manifest.json",
            )

    async def test_child_fetch_failure_fails_task(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        files = _manifest_files()
        del files["scripts/check.sh"]  # script body fetch will raise
        service = _import_service(session_factory, content_store, files)
        task = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        assert task["status"] == "failed"

    async def test_bad_source_type_rejected(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        service = _import_service(session_factory, content_store, _manifest_files())
        with pytest.raises(ValidationError):
            await service.start_import(
                actor=admin, workspace_id=workspace.id, source_type="builtin",
                uri="https://reg.example.com/x/manifest.json",
            )


class TestReimport:
    async def test_identical_reimport_is_idempotent(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        files = _manifest_files(scripts=[], required_capabilities=[])
        service = _import_service(session_factory, content_store, files)
        first = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        second = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        assert first["status"] == "ready"
        assert second["status"] == "ready"
        assert second["skill_id"] == first["skill_id"]
        async with session_factory() as session:
            count = len((await session.execute(select(SkillVersion))).scalars().all())
        assert count == 1  # de-duplicated by content hash

    async def test_same_version_different_content_conflict(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        files = _manifest_files(scripts=[], required_capabilities=[])
        service = _import_service(session_factory, content_store, files)
        await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        changed = _manifest_files(
            scripts=[], required_capabilities=[], instructions="DIFFERENT"
        )
        service2 = _import_service(session_factory, content_store, changed)
        task = await service2.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        assert task["status"] == "failed"
        assert "version_conflict" in task["error"]

    async def test_new_version_reuses_skill(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        files_v1 = _manifest_files(scripts=[], required_capabilities=[])
        service = _import_service(session_factory, content_store, files_v1)
        first = await service.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        files_v2 = _manifest_files(
            scripts=[], required_capabilities=[], version="1.3.1"
        )
        service2 = _import_service(session_factory, content_store, files_v2)
        second = await service2.start_import(
            actor=admin, workspace_id=workspace.id, source_type="url",
            uri="https://reg.example.com/x/manifest.json",
        )
        assert second["skill_id"] == first["skill_id"]
        async with session_factory() as session:
            versions = (await session.execute(select(SkillVersion))).scalars().all()
        assert {v.version for v in versions} == {"1.3.0", "1.3.1"}


class TestMarketplace:
    async def test_listings_fetched_and_filtered(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        listings = {
            "listings": [
                {"id": "1", "name": "接口文档生成", "summary": "OpenAPI 生成",
                 "version": "2.0.0", "manifest_url": "https://m.example.com/1.json",
                 "downloads": 500, "rating": 4.8, "certified": True,
                 "has_scripts": False, "tags": ["docs"]},
                {"id": "2", "name": "依赖扫描", "summary": "CVE scan",
                 "version": "1.1.0", "manifest_url": "https://m.example.com/2.json",
                 "downloads": 1200, "rating": 4.2, "certified": False,
                 "has_scripts": True, "tags": ["security"]},
            ]
        }

        async def fetch(url: str, allowlist: frozenset[str]) -> bytes:
            assert url.endswith("/listings")
            return json.dumps(listings).encode()

        service = ImportService(
            session_factory,
            content_store=content_store,
            settings=ImportSettings(
                marketplace_url="https://market.example.com", fetcher=fetch
            ),
        )
        items, cursor = await service.list_marketplace(workspace_id=workspace.id)
        assert cursor is None
        assert [i["name"] for i in items] == ["依赖扫描", "接口文档生成"]  # by downloads
        filtered, _ = await service.list_marketplace(
            workspace_id=workspace.id, q="接口"
        )
        assert [i["name"] for i in filtered] == ["接口文档生成"]

    async def test_unconfigured_marketplace_is_empty(
        self, session_factory, content_store
    ) -> None:
        workspace = await make_workspace(session_factory)
        service = ImportService(session_factory, content_store=content_store)
        items, cursor = await service.list_marketplace(workspace_id=workspace.id)
        assert items == []
        assert cursor is None
