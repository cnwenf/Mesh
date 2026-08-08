"""Enqueue reproducible snapshot assembly (README §6.11, §2.1 AttemptSpec).

Freezing the configuration at enqueue time makes a run reproducible and
auditable: later edits to the agent's configuration, skill bindings or
capability grants do NOT affect in-flight executions.

§2.1 P0: the snapshot is the full AttemptSpec — provider, model, effort,
system instructions, budget, network policy, data policy — not just a
version id. The daemon receives everything it needs from this frozen
snapshot and never reassembles from the agent's current config.

Fields frozen per §6.11 + §2.1:

* ``schema_version`` — snapshot schema version (currently 1);
* ``agent_config_version_id`` — the agent's active immutable version at
  enqueue time (agent.md §2.7);
* ``provider`` / ``model`` / ``effort`` / ``system_instructions`` — the
  actual provider config resolved from the agent config version;
* ``budget`` — frozen USD/token/turn/wall-time/idle-time limits;
* ``network_policy`` — allowed scheme/host/port/method, redirect cap;
* ``data_policy`` — redaction rule version, retention, sensitive output;
* ``skill_versions`` — ``{skill_id: version_id}`` from the skill bindings;
* ``capability_grants`` — strict ``[{capability, permission}]`` array;
* ``repo`` — ``{url, base_ref, base_sha}`` when bound;
* ``trigger_event_id`` — the domain/outbox event that caused the enqueue.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any

from mesh.agent.capabilities import normalize_capability_declarations

if TYPE_CHECKING:
    from mesh.db.models.agent import Agent

# §2.1: snapshot schema version — bump when the AttemptSpec shape changes.
SNAPSHOT_SCHEMA_VERSION = 1

# §2.1: default budget limits (workspace admin overrides via agent config).
DEFAULT_BUDGET: dict[str, Any] = {
    "max_cost_usd": None,
    "max_tokens": None,
    "max_turns": None,
    "max_wall_time_seconds": None,
    "max_idle_time_seconds": None,
    "max_log_bytes": 10 * 1024 * 1024,
    "max_result_bytes": 1024 * 1024,
    "max_diff_bytes": 2 * 1024 * 1024,
    "max_attachment_bytes": 10 * 1024 * 1024,
}

# §2.1: default network policy (deny-all baseline).
DEFAULT_NETWORK_POLICY: dict[str, Any] = {
    "allowed_schemes": ["https"],
    "allowed_hosts": [],
    "allowed_ports": [443],
    "allowed_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"],
    "max_redirects": 5,
    "max_upload_bytes": 10 * 1024 * 1024,
}

# §2.1: default data processing policy.
DEFAULT_DATA_POLICY: dict[str, Any] = {
    "redaction_rule_version": 1,
    "retention_policy": "default",
    "sensitive_output_handling": "redact",
}


def build_config_snapshot(
    *,
    agent_config_version_id: uuid.UUID | None,
    trigger_event_id: uuid.UUID,
    skill_versions: dict[str, str] | None = None,
    declared_capabilities: list[Any] | None = None,
    repo: dict[str, str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    system_instructions: str | None = None,
    budget: dict[str, Any] | None = None,
    network_policy: dict[str, Any] | None = None,
    data_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the §6.11/§2.1 snapshot and strict ``required_capabilities``.

    Returns ``{"config_snapshot": {...}, "required_capabilities": [...]}`` —
    the authorization snapshot (object array) and the scheduling field
    (string array) are derived from the SAME normalization call so the two
    can never drift apart.

    §2.1 P0: the snapshot now includes the full AttemptSpec (provider, model,
    effort, system instructions, budget, network/data policy) — not just a
    version id. The daemon receives everything from this frozen snapshot.
    """
    normalized = normalize_capability_declarations(declared_capabilities or [])
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "agent_config_version_id": (
            str(agent_config_version_id) if agent_config_version_id is not None else None
        ),
        # §2.1: actual provider config (not just a version reference).
        "provider": provider,
        "model": model,
        "effort": effort,
        "system_instructions": system_instructions,
        # §2.1: frozen budget/network/data policies.
        "budget": {**DEFAULT_BUDGET, **(budget or {})},
        "network_policy": {**DEFAULT_NETWORK_POLICY, **(network_policy or {})},
        "data_policy": {**DEFAULT_DATA_POLICY, **(data_policy or {})},
        "skill_versions": dict(skill_versions or {}),
        "capability_grants": normalized["grants"],
        "repo": repo,
        "trigger_event_id": str(trigger_event_id),
    }
    # §2.1: server-side digest — daemon rejects execution if the digest
    # doesn't match (tamper detection / version drift guard).
    snapshot["digest"] = compute_snapshot_digest(snapshot)
    return {
        "config_snapshot": snapshot,
        "required_capabilities": normalized["required"],
    }


def compute_snapshot_digest(snapshot: dict[str, Any]) -> str:
    """§2.1: SHA-256 digest of the AttemptSpec content (excluding the
    digest field itself). The daemon verifies this before execution;
    unknown version or digest mismatch → refuse execution."""
    # Exclude 'digest' key from the content being hashed.
    content = {k: v for k, v in sorted(snapshot.items()) if k != "digest"}
    serialized = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def agent_model_config(agent: Agent) -> dict[str, Any]:
    """The agent's ``model_config`` JSONB, guarded to a dict.

    A corrupted / non-dict persisted value degrades to an empty config
    instead of raising — trigger paths must never crash on bad stored data
    (the §6.11 defaults inside :func:`build_config_snapshot` then apply and
    the daemon fail-closes on the missing provider for real providers).
    """
    model_config = agent.model_config
    return model_config if isinstance(model_config, dict) else {}


def snapshot_from_agent(
    agent: Agent,
    *,
    trigger_event_id: uuid.UUID,
    skill_versions: dict[str, str] | None = None,
    declared_capabilities: list[Any] | None = None,
    repo: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The SINGLE §6.11 snapshot builder for every trigger path.

    runtime-executor.md §3.7 S-09: assign, mention, chat, autopilot,
    integration and squad must share one snapshot builder — this helper is
    that builder. It resolves the full AttemptSpec field set (provider /
    model / effort / system instructions / budget / network policy / active
    config version) from the agent row and delegates to
    :func:`build_config_snapshot`, so no trigger path can drift to a partial
    snapshot again (F-BUDGET-SNAPSHOT: mention / autopilot / integration
    paths used to assemble these arguments by hand and dropped budget and
    network policy — and sometimes provider/model too — which made the
    daemon fail-closed on every real-provider execution from those paths).

    ``budget`` / ``network_policy`` are read with an ``isinstance(dict)``
    guard (a malformed stored value degrades to the §2.1 DEFAULT_* baseline,
    matching the assign path's long-standing semantics). Callers keep their
    own degrade-don't-drop exception policy around this call.
    """
    model_config = agent_model_config(agent)
    return build_config_snapshot(
        agent_config_version_id=agent.active_config_version_id,
        trigger_event_id=trigger_event_id,
        skill_versions=skill_versions,
        declared_capabilities=declared_capabilities,
        repo=repo,
        provider=model_config.get("provider"),
        model=model_config.get("model"),
        effort=model_config.get("reasoning_effort"),
        system_instructions=agent.system_instructions,
        budget=(
            model_config.get("budget")
            if isinstance(model_config.get("budget"), dict) else None
        ),
        network_policy=(
            model_config.get("network_policy")
            if isinstance(model_config.get("network_policy"), dict) else None
        ),
    )


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "agent_model_config",
    "build_config_snapshot",
    "compute_snapshot_digest",
    "snapshot_from_agent",
]
