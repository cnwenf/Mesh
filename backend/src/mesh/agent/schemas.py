"""Agent API request schemas (agent.md §3).

Field-level format checks mirror the service-layer validators; the service
re-validates so direct (non-route) callers get identical error codes
(same convention as member/schemas.py).
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from mesh.db.models.agent import AGENT_LIFECYCLE_VALUES, AGENT_VISIBILITY_VALUES

IN_FLIGHT_POLICY_VALUES = ("finish_current", "cancel_current")


class CreateAgentRequest(BaseModel):
    """POST /workspaces/{ws}/agents — profile + initial configuration.

    ``name`` is canonical (agents.name, agent.md §2.3); ``display_name`` is
    accepted as an alias because the §3.2 worked example uses it. The
    ``model_config`` field is named ``agent_model_config`` internally
    because Pydantic v2 reserves ``model_config`` for class configuration;
    the JSON surface stays ``model_config`` via the alias.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        validation_alias=AliasChoices("name", "display_name"), min_length=1, max_length=120
    )
    avatar_url: str | None = None
    role_tag: str | None = Field(default=None, max_length=64)
    slug: str | None = Field(default=None, max_length=64)
    bio: str | None = None
    visibility: str = Field(default="workspace", pattern="^(workspace|private)$")
    system_instructions: str | None = None
    agent_model_config: dict[str, Any] | None = Field(
        default=None, validation_alias="model_config"
    )
    trigger_on_assign: bool = True
    # Reserved field (agent.md §2.3): the runtimes table lands with the
    # runtime.md increment — non-null values are rejected with 422 until
    # then (same staged-availability pattern the member module uses).
    default_runtime_id: str | None = None
    # Skill bindings are owned by skill.md; the wizard ships a placeholder
    # step, and non-empty values are rejected with 422 until that increment.
    skill_ids: list[str] | None = None
    capabilities: list[Any] | None = None


class PatchAgentRequest(BaseModel):
    """PATCH /workspaces/{ws}/agents/{id} — profile fields (unset = keep)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    avatar_url: str | None = None
    role_tag: str | None = Field(default=None, max_length=64)
    slug: str | None = Field(default=None, max_length=64)
    bio: str | None = None
    visibility: str | None = Field(default=None, pattern="^(workspace|private)$")
    trigger_on_assign: bool | None = None


class UpdateConfigRequest(BaseModel):
    """PATCH /workspaces/{ws}/agents/{id}/config — new configuration version.

    ``model_config`` merges over the active configuration (partial update);
    ``system_instructions`` replaces. Either or both may be present.
    """

    model_config = ConfigDict(populate_by_name=True)

    agent_model_config: dict[str, Any] | None = Field(
        default=None, validation_alias="model_config"
    )
    system_instructions: str | None = None
    change_summary: str | None = None


class LifecycleRequest(BaseModel):
    """POST /agents/{id}:pause — the only lifecycle verb carrying a body."""

    reason: str | None = None
    in_flight_policy: str = Field(
        default="finish_current", pattern="^(finish_current|cancel_current)$"
    )


class TransferRequest(BaseModel):
    """POST /agents/{id}:transfer — new owner (an active human member)."""

    new_owner_user_id: str


AGENT_LIFECYCLE_FILTERS = ("default", "all", *AGENT_LIFECYCLE_VALUES)
AGENT_VISIBILITY_FILTERS = ("all", *AGENT_VISIBILITY_VALUES)

__all__ = [
    "AGENT_LIFECYCLE_FILTERS",
    "AGENT_VISIBILITY_FILTERS",
    "IN_FLIGHT_POLICY_VALUES",
    "CreateAgentRequest",
    "LifecycleRequest",
    "PatchAgentRequest",
    "TransferRequest",
    "UpdateConfigRequest",
]
