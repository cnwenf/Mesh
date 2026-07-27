"""Resolver adapters registered into the agent orchestration entry point.

The agent module (``agent/triggers.py``) owns the enqueue path and exposes
two seams; the skill module plugs its producers into them here so the two
modules stay decoupled (skill.md §2.5 / §4.5 / §6.11):

* :func:`build_enqueue_context` → ``register_skill_context_resolver``:
  the §6.11 ``skill_versions`` map + granted capability declarations;
* :func:`make_matching_resolver` → ``register_skill_matching_resolver``:
  the §4.5 auto-trigger matcher (raw issue title / description / labels in,
  ranked injection list out).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from mesh.skill.bindings import BindingService
from mesh.skill.matching import match_skills_for_task


async def build_enqueue_context(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> dict:
    """§6.11 producer: bound versions + granted declarations for the snapshot."""
    service = BindingService.__new__(BindingService)
    return await service.collect_enqueue_context(session, workspace_id, agent_id)


def make_matching_resolver():
    """Return the §4.5 matcher bound to the ``match_skills_for_task`` impl."""

    async def resolve(
        session: AsyncSession,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        title: str,
        description: str | None,
        tags: list[str],
    ) -> list[dict]:
        return await match_skills_for_task(
            session,
            workspace_id=workspace_id,
            agent_id=agent_id,
            title=title,
            description=description or "",
            tags=tags,
        )

    return resolve


__all__ = ["build_enqueue_context", "make_matching_resolver"]
