"""Skill API request schemas (skill.md §3).

Route-level shapes; the services re-validate so direct callers get the
same error codes (member/agent module convention).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

DECISION_VALUES = ("approve", "reject")


class CreateSkillRequest(BaseModel):
    """POST /workspaces/{ws}/skills — a user-sourced definition."""

    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=96, pattern=r"^[a-z0-9][a-z0-9-]*$")
    summary: str = Field(min_length=1, max_length=1000)
    tags: list[str] | None = None
    icon: str | None = None
    required_capabilities: list[Any] | None = None


class PatchSkillRequest(BaseModel):
    """PATCH /workspaces/{ws}/skills/{id} — metadata / lifecycle status."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, min_length=1, max_length=1000)
    tags: list[str] | None = None
    icon: str | None = None
    status: str | None = None
    required_capabilities: list[Any] | None = None


class ScriptInput(BaseModel):
    """One script in a version payload — ``content`` is the inline body."""

    path: str = Field(min_length=1, max_length=512)
    runtime: str = "shell"
    entrypoint: bool = False
    required_capabilities: list[Any] | None = None
    content: str = ""


class ReferenceInput(BaseModel):
    """One reference document in a version payload."""

    path: str = Field(min_length=1, max_length=512)
    media_type: str = "text/markdown"
    summary: str | None = None
    content: str = ""


class TriggerInput(BaseModel):
    """One auto-trigger rule in a version payload."""

    trigger_type: str = Field(default="keyword", pattern=r"^(keyword|semantic|tag)$")
    pattern: str = Field(min_length=1)
    weight: float = Field(default=1.0, ge=0, le=999)


class CreateVersionRequest(BaseModel):
    """POST /workspaces/{ws}/skills/{id}/versions — mint an immutable version."""

    version: str = Field(min_length=1, max_length=64)
    instructions: str = Field(min_length=1)
    scripts: list[ScriptInput] | None = None
    references: list[ReferenceInput] | None = None
    triggers: list[TriggerInput] | None = None
    changelog: str | None = None
    io_contract: dict | None = None
    required_capabilities: list[Any] | None = None
    publish: bool = False


class ImportRequest(BaseModel):
    """POST /workspaces/{ws}/skills/import — marketplace / url import."""

    source_type: str = Field(pattern=r"^(marketplace|url)$")
    uri: str = Field(min_length=1, max_length=2000)
    ref: str | None = None


class ApproveRequest(BaseModel):
    """POST /workspaces/{ws}/skills/{id}/approve — third-party review gate."""

    task_id: str
    granted_capabilities: list[Any] = Field(default_factory=list)
    decision: str = Field(default="approve", pattern=r"^(approve|reject)$")
    comment: str | None = None


class InstallRequest(BaseModel):
    """POST /workspaces/{ws}/skill-installations — install into a scope."""

    skill_id: str
    skill_version_id: str
    scope: str = Field(default="workspace", pattern=r"^(workspace|agent)$")
    agent_id: str | None = None
    auto_update: bool = False


class PatchInstallationRequest(BaseModel):
    """PATCH /workspaces/{ws}/skill-installations/{id} — upgrade/toggle."""

    skill_version_id: str | None = None
    install_status: str | None = Field(
        default=None, pattern=r"^(installed|disabled)$"
    )
    auto_update: bool | None = None


class RollbackRequest(BaseModel):
    """POST /workspaces/{ws}/skill-installations/{id}/rollback."""

    target_version_id: str
    reason: str | None = None


class BindRequest(BaseModel):
    """POST /workspaces/{ws}/agents/{agent_id}/skills."""

    skill_installation_id: str
    skill_version_id: str | None = None
    auto_trigger: bool = True
    priority: int = Field(default=100, ge=0, le=1000)


class PatchBindingRequest(BaseModel):
    """PATCH /workspaces/{ws}/agents/{agent_id}/skills/{binding_id}."""

    enabled: bool | None = None
    auto_trigger: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)


__all__ = [
    "ApproveRequest",
    "BindRequest",
    "CreateSkillRequest",
    "CreateVersionRequest",
    "ImportRequest",
    "InstallRequest",
    "PatchBindingRequest",
    "PatchInstallationRequest",
    "PatchSkillRequest",
    "ReferenceInput",
    "RollbackRequest",
    "ScriptInput",
    "TriggerInput",
]
