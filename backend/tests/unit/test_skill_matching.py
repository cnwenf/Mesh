"""Auto-trigger matching tests (skill.md §4.5: explainable, trimmable, switchable)."""

from __future__ import annotations

import uuid

from skill_test_support import make_agent, make_member, make_workspace

from mesh.db.models.skill import (
    AgentSkill,
    Skill,
    SkillInstallation,
    SkillSource,
    SkillTrigger,
    SkillVersion,
)
from mesh.skill.matching import match_skills_for_task


async def _skill_with_trigger(
    session_factory, workspace, member, agent, *, name: str, slug: str,
    triggers: list[tuple[str, str, float]], tags: list[str] | None = None,
    enabled: bool = True, auto_trigger: bool = True, priority: int = 100,
    install_status: str = "installed",
) -> Skill:
    """Seed a published skill + version + triggers + installation + binding."""
    async with session_factory() as session, session.begin():
        source = SkillSource(
            workspace_id=workspace.id, source_type="user", name="u",
            trust_level="reviewed",
        )
        session.add(source)
        await session.flush()
        skill = Skill(
            workspace_id=workspace.id, source_id=source.id, name=name,
            slug=slug, summary=f"{name} summary", status="published",
            tags=tags or [], created_by=member.id,
        )
        session.add(skill)
        await session.flush()
        version = SkillVersion(
            workspace_id=workspace.id, skill_id=skill.id, version="1.0.0",
            instructions=f"{name} instructions", status="published",
            content_hash=uuid.uuid4().hex * 2, created_by=member.id,
        )
        session.add(version)
        await session.flush()
        for trigger_type, pattern, weight in triggers:
            session.add(
                SkillTrigger(
                    skill_version_id=version.id, trigger_type=trigger_type,
                    pattern=pattern, weight=weight,
                )
            )
        installation = SkillInstallation(
            workspace_id=workspace.id, skill_id=skill.id,
            skill_version_id=version.id, installed_by=member.id,
            install_status=install_status,
        )
        session.add(installation)
        await session.flush()
        session.add(
            AgentSkill(
                workspace_id=workspace.id, agent_id=agent.id, skill_id=skill.id,
                skill_installation_id=installation.id,
                skill_version_id=version.id, enabled=enabled,
                auto_trigger=auto_trigger, priority=priority,
            )
        )
    return skill


async def _world(session_factory):
    workspace = await make_workspace(session_factory)
    member = await make_member(session_factory, workspace, role="admin")
    agent = await make_agent(session_factory, workspace, member.user_id)
    return workspace, member, agent


class TestMatching:
    async def test_keyword_match_injects_with_evidence(
        self, session_factory, db_session
    ) -> None:
        workspace, member, agent = await _world(session_factory)
        skill = await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="代码评审规范", slug="review-sop",
            triggers=[("keyword", "review pr", 1.0)],
        )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="please review this PR now", description="",
        )
        assert len(result) == 1
        item = result[0]
        assert item["skill_id"] == str(skill.id)
        assert item["instructions"] == "代码评审规范 instructions"
        assert item["matched_by"] == ["keyword:review pr"]
        assert item["forced"] is False

    async def test_no_match_yields_empty(self, session_factory, db_session) -> None:
        workspace, member, agent = await _world(session_factory)
        await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="S", slug="s-1", triggers=[("keyword", "review", 1.0)],
        )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="grocery list only", description="",
        )
        assert result == []

    async def test_tag_matching_skill_and_trigger(
        self, session_factory, db_session
    ) -> None:
        workspace, member, agent = await _world(session_factory)
        await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="S", slug="s-2",
            triggers=[("tag", "security", 2.0)], tags=["security", "lint"],
        )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="task", tags=["SECURITY", "lint"],
        )
        assert len(result) == 1
        assert "tag:security" in result[0]["matched_by"]
        assert "skill_tag:lint" in result[0]["matched_by"]
        assert "skill_tag:security" in result[0]["matched_by"]

    async def test_priority_weights_ranking(self, session_factory, db_session) -> None:
        workspace, member, agent = await _world(session_factory)
        await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="low", slug="s-low", triggers=[("keyword", "deploy", 1.0)],
            priority=10,
        )
        high = await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="high", slug="s-high", triggers=[("keyword", "deploy", 1.0)],
            priority=500,
        )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="deploy now",
        )
        assert [r["skill_id"] for r in result][0] == str(high.id)

    async def test_top_n_trim(self, session_factory, db_session) -> None:
        workspace, member, agent = await _world(session_factory)
        for i in range(8):
            await _skill_with_trigger(
                session_factory, workspace, member, agent,
                name=f"s{i}", slug=f"s-{i}",
                triggers=[("keyword", "deploy", 1.0)],
            )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="deploy", top_n=3,
        )
        assert len(result) == 3

    async def test_auto_trigger_off_excluded(self, session_factory, db_session) -> None:
        workspace, member, agent = await _world(session_factory)
        await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="S", slug="s-3", triggers=[("keyword", "deploy", 1.0)],
            auto_trigger=False,
        )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="deploy",
        )
        assert result == []

    async def test_disabled_binding_excluded(self, session_factory, db_session) -> None:
        workspace, member, agent = await _world(session_factory)
        await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="S", slug="s-4", triggers=[("keyword", "deploy", 1.0)],
            enabled=False,
        )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="deploy",
        )
        assert result == []

    async def test_disabled_installation_excluded(
        self, session_factory, db_session
    ) -> None:
        workspace, member, agent = await _world(session_factory)
        await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="S", slug="s-5", triggers=[("keyword", "deploy", 1.0)],
            install_status="disabled",
        )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="deploy",
        )
        assert result == []

    async def test_explicit_skill_forced_and_never_trimmed(
        self, session_factory, db_session
    ) -> None:
        workspace, member, agent = await _world(session_factory)
        forced = await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="forced", slug="s-forced", triggers=[],  # no triggers at all
            auto_trigger=False,  # even with auto_trigger off
        )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="unrelated", explicit_skill_ids=[forced.id], top_n=0,
        )
        assert len(result) == 1
        assert result[0]["skill_id"] == str(forced.id)
        assert result[0]["forced"] is True
        assert result[0]["matched_by"] == ["explicit"]

    async def test_weight_affects_score(self, session_factory, db_session) -> None:
        workspace, member, agent = await _world(session_factory)
        await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="S", slug="s-6", triggers=[("keyword", "deploy", 2.5)],
        )
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="deploy",
        )
        # score = hits(1) × weight(2.5) × priority(100)
        assert result[0]["score"] == 250.0

    async def test_keyword_is_lexeme_not_substring(self, session_factory, db_session) -> None:
        # M7 false-positive regression: trigger "deploy" must NOT match a task
        # whose only token is "undeployable" (the old substring match did).
        workspace, member, agent = await _world(session_factory)
        await _skill_with_trigger(
            session_factory, workspace, member, agent,
            name="S", slug="s-lex", triggers=[("keyword", "deploy", 1.0)],
        )
        assert (
            await match_skills_for_task(
                db_session, workspace_id=workspace.id, agent_id=agent.id,
                title="this is undeployable code",
            )
            == []
        )
        # ...but the bare lexeme still matches.
        result = await match_skills_for_task(
            db_session, workspace_id=workspace.id, agent_id=agent.id,
            title="please deploy now",
        )
        assert len(result) == 1

    async def test_matching_uses_two_queries_not_n_plus_one(
        self, session_factory, db_session
    ) -> None:
        # Structural guard: matching issues a bounded number of statements
        # regardless of candidate count (no per-candidate trigger query).
        from sqlalchemy import event

        workspace, member, agent = await _world(session_factory)
        for i in range(6):
            await _skill_with_trigger(
                session_factory, workspace, member, agent,
                name=f"s{i}", slug=f"sn-{i}", triggers=[("keyword", "deploy", 1.0)],
            )
        counts = {"n": 0}

        @event.listens_for(db_session.sync_session.bind, "before_cursor_execute")
        def _count(conn, cursor, statement, *args):  # noqa: ARG001
            counts["n"] += 1

        try:
            await match_skills_for_task(
                db_session, workspace_id=workspace.id, agent_id=agent.id, title="deploy"
            )
        finally:
            event.remove(db_session.sync_session.bind, "before_cursor_execute", _count)
        # 2 statements: candidates + triggers-in (NOT 1 + N).
        assert counts["n"] <= 3
