"""Database-level skill constraint tests (README §6.2 rules 1/2/6/7, §9 T1).

Real PostgreSQL, raw SQL: cross-workspace composite FKs, the same-skill
OVERLAPPING FKs (current_version / installation version / binding chain),
column-level ``ON DELETE SET NULL``, and the scope-uniqueness indexes must
all be enforced by the database itself.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


async def _seed(db_session) -> dict:
    """Two workspaces; ws1 has member + agent + source + skills A/B + versions."""
    ids = {k: uuid.uuid4() for k in (
        "ws1", "ws2", "user", "member1", "member2", "agent1",
        "src1", "src2", "a", "b", "va", "vb",
    )}
    await db_session.execute(
        text("INSERT INTO workspaces (id, name, slug) VALUES "
             "(:w1, 'WS1', :s1), (:w2, 'WS2', :s2)"),
        {"w1": ids["ws1"], "s1": f"w1-{ids['ws1'].hex[:8]}",
         "w2": ids["ws2"], "s2": f"w2-{ids['ws2'].hex[:8]}"},
    )
    await db_session.execute(
        text("INSERT INTO users (id, email, display_name) VALUES (:u, :e, 'T')"),
        {"u": ids["user"], "e": f"t-{ids['user'].hex[:8]}@corp.com"},
    )
    await db_session.execute(
        text("INSERT INTO members (id, workspace_id, member_type, user_id) VALUES "
             "(:m1, :w1, 'human', :u), (:m2, :w2, 'human', :u)"),
        {"m1": ids["member1"], "m2": ids["member2"], "w1": ids["ws1"],
         "w2": ids["ws2"], "u": ids["user"]},
    )
    await db_session.execute(
        text("INSERT INTO agents (id, workspace_id, name, owner_user_id) VALUES "
             "(:ag, :w1, 'Bot', :u)"),
        {"ag": ids["agent1"], "w1": ids["ws1"], "u": ids["user"]},
    )
    await db_session.execute(
        text("INSERT INTO skill_sources (id, workspace_id, source_type, name, trust_level) "
             "VALUES (:s1, :w1, 'user', 'src', 'reviewed'), "
             "(:s2, :w2, 'user', 'src', 'reviewed')"),
        {"s1": ids["src1"], "s2": ids["src2"], "w1": ids["ws1"], "w2": ids["ws2"]},
    )
    for key, slug in (("a", "sk-a"), ("b", "sk-b")):
        await db_session.execute(
            text("INSERT INTO skills (id, workspace_id, source_id, name, slug, summary, "
                 "created_by) VALUES (:sk, :w1, :src, :n, :slug, 's', :m)"),
            {"sk": ids[key], "w1": ids["ws1"], "src": ids["src1"],
             "n": f"skill-{key}", "slug": slug, "m": ids["member1"]},
        )
    for v_key, s_key, version in (("va", "a", "1.0.0"), ("vb", "b", "1.0.0")):
        await db_session.execute(
            text("INSERT INTO skill_versions (id, workspace_id, skill_id, version, "
                 "instructions, content_hash, created_by) VALUES "
                 "(:v, :w1, :sk, :ver, 'do', :h, :m)"),
            {"v": ids[v_key], "w1": ids["ws1"], "sk": ids[s_key],
             "ver": version, "h": "a" * 64, "m": ids["member1"]},
        )
    return ids


class TestSameSkillOverlapFK:
    """README §6.2 rule 7 — version pointers must belong to the same skill."""

    async def test_current_version_of_own_skill_ok(self, db_session) -> None:
        ids = await _seed(db_session)
        await db_session.execute(
            text("UPDATE skills SET current_version_id = :v WHERE id = :sk"),
            {"v": ids["va"], "sk": ids["a"]},
        )

    async def test_current_version_of_other_skill_rejected(self, db_session) -> None:
        ids = await _seed(db_session)
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("UPDATE skills SET current_version_id = :vb WHERE id = :a"),
                {"vb": ids["vb"], "a": ids["a"]},
            )

    async def test_install_version_of_other_skill_rejected(self, db_session) -> None:
        ids = await _seed(db_session)
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO skill_installations (id, workspace_id, skill_id, "
                     "skill_version_id, installed_by) VALUES (:i, :w, :a, :vb, :m)"),
                {"i": uuid.uuid4(), "w": ids["ws1"], "a": ids["a"],
                 "vb": ids["vb"], "m": ids["member1"]},
            )

    async def test_binding_version_of_other_skill_rejected(self, db_session) -> None:
        ids = await _seed(db_session)
        inst = uuid.uuid4()
        await db_session.execute(
            text("INSERT INTO skill_installations (id, workspace_id, skill_id, "
                 "skill_version_id, installed_by) VALUES (:i, :w, :a, :va, :m)"),
            {"i": inst, "w": ids["ws1"], "a": ids["a"],
             "va": ids["va"], "m": ids["member1"]},
        )
        # Binding skill A's installation to skill B's version → rejected.
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO agent_skills (id, workspace_id, agent_id, skill_id, "
                     "skill_installation_id, skill_version_id) VALUES "
                     "(:b, :w, :ag, :a, :i, :vb)"),
                {"b": uuid.uuid4(), "w": ids["ws1"], "ag": ids["agent1"],
                 "a": ids["a"], "i": inst, "vb": ids["vb"]},
            )

    async def test_binding_installation_of_other_skill_rejected(self, db_session) -> None:
        ids = await _seed(db_session)
        # Installation of skill B, but the binding claims skill A.
        inst_b = uuid.uuid4()
        await db_session.execute(
            text("INSERT INTO skill_installations (id, workspace_id, skill_id, "
                 "skill_version_id, installed_by) VALUES (:i, :w, :b, :vb, :m)"),
            {"i": inst_b, "w": ids["ws1"], "b": ids["b"],
             "vb": ids["vb"], "m": ids["member1"]},
        )
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO agent_skills (id, workspace_id, agent_id, skill_id, "
                     "skill_installation_id, skill_version_id) VALUES "
                     "(:x, :w, :ag, :a, :i, :va)"),
                {"x": uuid.uuid4(), "w": ids["ws1"], "ag": ids["agent1"],
                 "a": ids["a"], "i": inst_b, "va": ids["va"]},
            )


class TestCrossWorkspace:
    """README §6.2 rules 1/2 — composite FKs refuse cross-tenant references."""

    async def test_skill_source_cross_workspace_rejected(self, db_session) -> None:
        ids = await _seed(db_session)
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO skills (id, workspace_id, source_id, name, slug, "
                     "summary, created_by) VALUES (:sk, :w2, :src1, 'x', 'sk-x', 's', :m2)"),
                {"sk": uuid.uuid4(), "w2": ids["ws2"], "src1": ids["src1"],
                 "m2": ids["member2"]},
            )

    async def test_version_cross_workspace_rejected(self, db_session) -> None:
        ids = await _seed(db_session)
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO skill_versions (id, workspace_id, skill_id, version, "
                     "instructions, content_hash, created_by) VALUES "
                     "(:v, :w2, :a, '9.9.9', 'x', :h, :m2)"),
                {"v": uuid.uuid4(), "w2": ids["ws2"], "a": ids["a"],
                 "h": "b" * 64, "m2": ids["member2"]},
            )

    async def test_creator_must_be_same_workspace_member(self, db_session) -> None:
        ids = await _seed(db_session)
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO skills (id, workspace_id, source_id, name, slug, "
                     "summary, created_by) VALUES (:sk, :w1, :src1, 'x', 'sk-y', 's', :m2)"),
                {"sk": uuid.uuid4(), "w1": ids["ws1"], "src1": ids["src1"],
                 "m2": ids["member2"]},  # ws2 member
            )


class TestDeleteSemantics:
    """README §6.2 rule 6 — column-level SET NULL keeps the tenant key."""

    async def test_version_delete_nulls_only_pointer(self, db_session) -> None:
        ids = await _seed(db_session)
        await db_session.execute(
            text("UPDATE skills SET current_version_id = :v WHERE id = :sk"),
            {"v": ids["va"], "sk": ids["a"]},
        )
        # Installations hold the version with RESTRICT — remove them first.
        await db_session.execute(
            text("DELETE FROM skill_versions WHERE id = :v"), {"v": ids["va"]}
        )
        row = (
            await db_session.execute(
                text("SELECT current_version_id, workspace_id FROM skills WHERE id = :sk"),
                {"sk": ids["a"]},
            )
        ).one()
        assert row[0] is None  # pointer nulled…
        assert row[1] == ids["ws1"]  # …workspace_id untouched

    async def test_duplicate_version_number_rejected(self, db_session) -> None:
        ids = await _seed(db_session)
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO skill_versions (id, workspace_id, skill_id, version, "
                     "instructions, content_hash, created_by) VALUES "
                     "(:v, :w, :a, '1.0.0', 'dup', :h, :m)"),
                {"v": uuid.uuid4(), "w": ids["ws1"], "a": ids["a"],
                 "h": "c" * 64, "m": ids["member1"]},
            )


class TestUniquenessIndexes:
    async def test_duplicate_slug_rejected(self, db_session) -> None:
        ids = await _seed(db_session)
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO skills (id, workspace_id, source_id, name, slug, "
                     "summary, created_by) VALUES (:sk, :w, :src, 'x', 'sk-a', 's', :m)"),
                {"sk": uuid.uuid4(), "w": ids["ws1"], "src": ids["src1"],
                 "m": ids["member1"]},
            )

    async def test_agent_scope_requires_agent_id(self, db_session) -> None:
        ids = await _seed(db_session)
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO skill_installations (id, workspace_id, skill_id, "
                     "skill_version_id, scope, agent_id, installed_by) VALUES "
                     "(:i, :w, :a, :va, 'agent', NULL, :m)"),
                {"i": uuid.uuid4(), "w": ids["ws1"], "a": ids["a"],
                 "va": ids["va"], "m": ids["member1"]},
            )

    async def test_duplicate_binding_rejected(self, db_session) -> None:
        ids = await _seed(db_session)
        inst = uuid.uuid4()
        await db_session.execute(
            text("INSERT INTO skill_installations (id, workspace_id, skill_id, "
                 "skill_version_id, installed_by) VALUES (:i, :w, :a, :va, :m)"),
            {"i": inst, "w": ids["ws1"], "a": ids["a"],
             "va": ids["va"], "m": ids["member1"]},
        )
        await db_session.execute(
            text("INSERT INTO agent_skills (id, workspace_id, agent_id, skill_id, "
                 "skill_installation_id, skill_version_id) VALUES "
                 "(:b, :w, :ag, :a, :i, :va)"),
            {"b": uuid.uuid4(), "w": ids["ws1"], "ag": ids["agent1"],
             "a": ids["a"], "i": inst, "va": ids["va"]},
        )
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("INSERT INTO agent_skills (id, workspace_id, agent_id, skill_id, "
                     "skill_installation_id, skill_version_id) VALUES "
                     "(:b2, :w, :ag, :a, :i, :va)"),
                {"b2": uuid.uuid4(), "w": ids["ws1"], "ag": ids["agent1"],
                 "a": ids["a"], "i": inst, "va": ids["va"]},
            )
