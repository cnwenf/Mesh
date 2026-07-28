"""Pydantic request models for the autopilot console API (autopilot.md §3.1).

Field bounds mirror the table CHECKs (§2.2) so request-level validation
fails with the §6.14 envelope before anything reaches the database.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TriggerType = Literal[
    "schedule",
    "issue_status_changed",
    "issue_created",
    "issue_field_changed",
    "comment_created",
    "agent_mentioned",
    "webhook_received",
]

RetryBackoff = Literal["fixed", "linear", "exponential"]


class CreateAutopilotRequest(BaseModel):
    """POST /workspaces/{ws}/autopilots (§3.2 request example)."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    trigger_type: TriggerType
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    filter_config: dict[str, Any] = Field(default_factory=dict)
    action_config: list[Any] = Field(default_factory=list)
    executor_agent_id: str | None = None
    status: Literal["active", "paused"] = "active"
    guardrails: dict[str, Any] | None = None
    max_retries: int = Field(default=3, ge=0, le=20)
    retry_backoff: RetryBackoff = "exponential"
    retry_base_seconds: int = Field(default=30, ge=1)
    retry_max_seconds: int = Field(default=1800, ge=1)
    rate_limit_max: int = Field(default=10, ge=0)
    rate_limit_window_seconds: int = Field(default=3600, ge=1)
    concurrency_limit: int = Field(default=1, ge=1)
    require_approval: bool = False


class PatchAutopilotRequest(BaseModel):
    """PATCH /workspaces/{ws}/autopilots/{id} — every field optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    trigger_type: TriggerType | None = None
    trigger_config: dict[str, Any] | None = None
    filter_config: dict[str, Any] | None = None
    action_config: list[Any] | None = None
    executor_agent_id: str | None = None
    guardrails: dict[str, Any] | None = None
    max_retries: int | None = Field(default=None, ge=0, le=20)
    retry_backoff: RetryBackoff | None = None
    retry_base_seconds: int | None = Field(default=None, ge=1)
    retry_max_seconds: int | None = Field(default=None, ge=1)
    rate_limit_max: int | None = Field(default=None, ge=0)
    rate_limit_window_seconds: int | None = Field(default=None, ge=1)
    concurrency_limit: int | None = Field(default=None, ge=1)
    require_approval: bool | None = None


class TestRunRequest(BaseModel):
    """POST /workspaces/{ws}/autopilots/{id}/test-run (§3.2)."""

    simulate_trigger_payload: dict[str, Any] | None = None
    dry_run: bool = False


class KillSwitchRequest(BaseModel):
    """POST /workspaces/{ws}/autopilots/kill-switch (§3.2)."""

    enabled: bool
    reason: str | None = Field(default=None, max_length=500)


class CreateWebhookSecretRequest(BaseModel):
    """POST /workspaces/{ws}/webhook-secrets."""

    label: str = Field(default="default", min_length=1, max_length=120)


class RunDecisionRequest(BaseModel):
    """runs/{run_id}/approve|reject body — mirrors ApprovalDecideRequest."""

    comment: str | None = Field(default=None, max_length=2000)


__all__ = [
    "CreateAutopilotRequest",
    "CreateWebhookSecretRequest",
    "KillSwitchRequest",
    "PatchAutopilotRequest",
    "RunDecisionRequest",
    "TestRunRequest",
]
