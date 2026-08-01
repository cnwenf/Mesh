"""Auto-trigger matching — task-context → skill injection (skill.md §4.5).

Contract: EXPLAINABLE, trimmable, switchable-off, and DB-efficient.

1. candidates — the agent's enabled + auto_trigger bindings whose
   installation and skill are live, fetched in ONE query (the bound version
   is the one injected — supports canary / rollback pins);
2. the candidates' triggers are fetched in ONE second query (``IN`` over the
   version ids) — NO per-candidate ``skill_triggers`` round-trip (the prior
   N+1);
3. keyword scoring uses LEXEME equality (``simple``-config tokenisation, the
   same scheme as the §2.8 GIN expression), so a trigger ``deploy`` matches
   the task lexeme ``deploy`` but NOT ``undeployable`` (the prior substring
   match was a false-positive); tag triggers and skill tags use set
   intersection; semantic similarity stays a declared-but-unimplemented
   strategy (§1.3) with the breakdown left open for it;
4. trim — total score × binding priority, Top-N cut to avoid context
   overload;
5. mutex — one injection per skill. The data model carries no mutex-group
   column in v0.1, so the de-facto mutex is per-skill (see skill.md §4.5
   step 4 note);
6. injection record — the returned list names every injected skill with its
   matched-by evidence for audit / ``config_snapshot`` persistence.

Explicitly requested skills bypass scoring and trimming (§4.5 兜底).
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.skill import (
    AgentSkill,
    Skill,
    SkillInstallation,
    SkillTrigger,
    SkillVersion,
    installation_matches_binding_agent,
)

DEFAULT_TOP_N = 5

# ``simple`` config tokenisation: lower-cased alphanumeric runs. Mirrors
# PostgreSQL ``to_tsvector('simple', ...)`` so lexeme equality matches the
# §2.8 GIN expression's vocabulary exactly.
_LEXEME_RE = re.compile(r"[a-z0-9]+")


def lexemes(text: str) -> set[str]:
    """Tokenise ``text`` into ``simple``-config lexemes (lower-cased)."""
    if not text:
        return set()
    return set(_LEXEME_RE.findall(text.lower()))


def _tsquery_or(lex: set[str]) -> str:
    """Build a ``simple``-config OR tsquery *literal* from lexemes.

    The ``|`` (OR) is part of tsquery's text grammar (parsed by the cast),
    NOT a SQL operator — so the whole OR expression is one quoted literal
    cast to ``tsquery`` once at the call site. Drives the §2.8 GIN index
    (``to_tsvector('simple', pattern)``): a stored keyword pattern matches
    when any of its lexemes equals a task lexeme. Python scoring below
    remains authoritative for final correctness. Lexemes come from
    :func:`lexemes` (``[a-z0-9]+``) and are escaped so no injection is
    possible even though the string is inlined.
    """
    inner = " | ".join(
        f"'{token.replace(chr(92), chr(92) * 2).replace(chr(39), chr(92) + chr(39))}'"
        for token in sorted(lex)
    )
    # SQL single-quote the whole tsquery text (double any inner single quotes).
    return "'" + inner.replace("'", "''") + "'"


def _uuid_list(ids: set[uuid.UUID]) -> str:
    """Inline a set of type-validated UUIDs as a SQL list literal."""
    return ", ".join(f"'{u}'" for u in ids)


def _keyword_score(pattern: str, task_lexemes: set[str]) -> int:
    """Lexeme-overlap count between a keyword pattern and the task lexemes.

    A multi-word pattern (e.g. ``code review``) fires only when ALL of its
    lexemes appear in the task (AND semantics, matching ``plainto_tsquery``).
    """
    pattern_lexemes = lexemes(pattern)
    if not pattern_lexemes:
        return 0
    if pattern_lexemes <= task_lexemes:
        return len(pattern_lexemes)
    return 0


async def match_skills_for_task(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    title: str = "",
    description: str = "",
    tags: list[str] | None = None,
    explicit_skill_ids: list[uuid.UUID] | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> list[dict]:
    """Return the skills to inject for this task, ranked and trimmed.

    Each item: ``{"skill_id", "skill_version_id", "instructions", "score",
    "priority", "matched_by", "forced"}`` — ``matched_by`` carries the
    human-readable evidence (可解释) and ``forced`` marks explicitly
    requested skills (强制注入, never trimmed).
    """
    task_tags = {tag.lower() for tag in (tags or [])}
    task_lexemes = lexemes(f"{title}\n{description}")
    explicit = set(explicit_skill_ids or [])

    # Query 1: candidate bindings (+ skill + bound version), one round-trip.
    rows = (
        await session.execute(
            select(AgentSkill, Skill, SkillVersion)
            .join(
                SkillInstallation,
                (SkillInstallation.workspace_id == AgentSkill.workspace_id)
                & (SkillInstallation.id == AgentSkill.skill_installation_id)
                & (SkillInstallation.deleted_at.is_(None))
                & installation_matches_binding_agent(),
            )
            .join(
                Skill,
                (Skill.workspace_id == AgentSkill.workspace_id)
                & (Skill.id == AgentSkill.skill_id)
                & (Skill.deleted_at.is_(None)),
            )
            .join(
                SkillVersion,
                (SkillVersion.workspace_id == AgentSkill.workspace_id)
                & (SkillVersion.id == AgentSkill.skill_version_id),
            )
            .where(
                AgentSkill.workspace_id == workspace_id,
                AgentSkill.agent_id == agent_id,
                AgentSkill.enabled.is_(True),
                SkillInstallation.install_status != "disabled",
                Skill.status.notin_(["disabled"]),
            )
        )
    ).all()

    if not rows:
        return []

    # Query 2: triggers for the candidate versions, one round-trip (replaces
    # the per-candidate N+1). Keyword triggers are pre-filtered through the
    # §2.8 GIN index (``to_tsvector('simple', pattern) @@ <task lexemes>``) so
    # the DB prunes non-matching patterns; tag triggers are always loaded and
    # matched in Python. Python scoring below is authoritative for final
    # correctness (lexeme equality), so any tokenizer edge case between PG's
    # ``simple`` config and :func:`lexemes` cannot produce a wrong match.
    version_ids = {version.id for _, _, version in rows}
    ids_list = _uuid_list(version_ids)
    if task_lexemes:
        tsq = _tsquery_or(task_lexemes)
        # GIN prefilter on keyword patterns (drives idx_trigger_keyword) OR
        # any non-keyword trigger (matched in Python). Inlined literals are
        # safe: UUIDs are type-validated, the tsquery is escaped above.
        where_clause = text(
            f"skill_version_id IN ({ids_list}) AND ("
            f"(trigger_type = 'keyword' AND "
            f"to_tsvector('simple', pattern) @@ {tsq}::tsquery) "
            f"OR trigger_type <> 'keyword')"
        )
    else:
        where_clause = text(f"skill_version_id IN ({ids_list})")
    trigger_rows = (
        await session.execute(select(SkillTrigger).where(where_clause))
    ).scalars().all()
    triggers_by_version: dict[uuid.UUID, list[SkillTrigger]] = {}
    for trigger in trigger_rows:
        triggers_by_version.setdefault(trigger.skill_version_id, []).append(trigger)

    forced: list[dict] = []
    scored: list[dict] = []
    for binding, skill, version in rows:
        if skill.id in explicit:
            forced.append(
                {
                    "skill_id": str(skill.id),
                    "skill_version_id": str(version.id),
                    "instructions": version.instructions,
                    "score": None,
                    "priority": binding.priority,
                    "matched_by": ["explicit"],
                    "forced": True,
                }
            )
            continue
        if not binding.auto_trigger:
            continue

        matched_by: list[str] = []
        score = 0.0
        for trigger in triggers_by_version.get(version.id, []):
            weight = float(trigger.weight)
            if trigger.trigger_type == "keyword":
                hits = _keyword_score(trigger.pattern, task_lexemes)
                if hits:
                    score += hits * weight
                    matched_by.append(f"keyword:{trigger.pattern}")
            elif trigger.trigger_type == "tag":
                if trigger.pattern.lower() in task_tags:
                    score += weight
                    matched_by.append(f"tag:{trigger.pattern}")
            # 'semantic' triggers are declared but not scored in v0.1 (§1.3).
        skill_tag_hits = task_tags & {tag.lower() for tag in (skill.tags or [])}
        if skill_tag_hits:
            score += len(skill_tag_hits)
            matched_by.extend(f"skill_tag:{tag}" for tag in sorted(skill_tag_hits))

        if score > 0:
            scored.append(
                {
                    "skill_id": str(skill.id),
                    "skill_version_id": str(version.id),
                    "instructions": version.instructions,
                    "score": round(score * binding.priority, 4),
                    "priority": binding.priority,
                    "matched_by": matched_by,
                    "forced": False,
                }
            )

    # Rank by score (priority-weighted); keep the best per skill (per-skill
    # mutex) and trim to Top-N.
    best_per_skill: dict[str, dict] = {}
    for item in sorted(scored, key=lambda entry: entry["score"], reverse=True):
        best_per_skill.setdefault(item["skill_id"], item)
    trimmed = sorted(
        best_per_skill.values(), key=lambda entry: entry["score"], reverse=True
    )[: max(0, top_n)]
    return forced + trimmed


__all__ = ["DEFAULT_TOP_N", "lexemes", "match_skills_for_task"]
