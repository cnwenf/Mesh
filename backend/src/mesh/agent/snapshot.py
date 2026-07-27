"""Enqueue reproducible snapshot assembly (README §6.11).

Freezing the configuration at enqueue time makes a run reproducible and
auditable: later edits to the agent's configuration, skill bindings or
capability grants do NOT affect in-flight executions.

Fields frozen per §6.11:

* ``agent_config_version_id`` — the agent's active immutable version at
  enqueue time (agent.md §2.7);
* ``skill_versions`` — ``{skill_id: version_id}`` from the skill bindings
  (skill.md owns the bindings; empty until that increment lands);
* ``capability_grants`` — strict ``[{capability, permission}]`` array
  derived from the declared capabilities by
  :func:`normalize_capability_declarations` (permission REQUIRED, R4);
* ``repo`` — ``{url, base_ref, base_sha}`` when the workspace/project repo
  binding exists (``None`` until the runtime increment supplies it);
* ``trigger_event_id`` — the domain/outbox event that caused the enqueue
  (audit anchor, also embedded in the §6.5 idempotency key).
"""

from __future__ import annotations

import uuid
from typing import Any

from mesh.agent.capabilities import normalize_capability_declarations


def build_config_snapshot(
    *,
    agent_config_version_id: uuid.UUID | None,
    trigger_event_id: uuid.UUID,
    skill_versions: dict[str, str] | None = None,
    declared_capabilities: list[Any] | None = None,
    repo: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assemble the §6.11 snapshot and the strict ``required_capabilities``.

    Returns ``{"config_snapshot": {...}, "required_capabilities": [...]}`` —
    the authorization snapshot (object array) and the scheduling field
    (string array) are derived from the SAME normalization call so the two
    can never drift apart.
    """
    normalized = normalize_capability_declarations(declared_capabilities or [])
    return {
        "config_snapshot": {
            "agent_config_version_id": (
                str(agent_config_version_id) if agent_config_version_id is not None else None
            ),
            "skill_versions": dict(skill_versions or {}),
            "capability_grants": normalized["grants"],
            "repo": repo,
            "trigger_event_id": str(trigger_event_id),
        },
        "required_capabilities": normalized["required"],
    }


__all__ = ["build_config_snapshot"]
