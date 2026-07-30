"""Request schemas for integration management endpoints (§3.1)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateIntegrationRequest(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=64 * 1024)


class PatchIntegrationRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None
    config: dict[str, Any] | None = None


class CreateBindingRequest(BaseModel):
    external_ref: str = Field(min_length=1, max_length=500)
    scope: str = "workspace"
    project_id: str | None = None
    match_config: dict[str, Any] = Field(default_factory=dict)
    bound_agent_id: str | None = None


class PatchBindingRequest(BaseModel):
    match_config: dict[str, Any] | None = None
    bound_agent_id: str | None = None
    clear_bound_agent: bool = False
    status: str | None = None


class CreateSubscriptionRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    event_types: list[str] = Field(default_factory=list)
    integration_id: str | None = None


class PatchSubscriptionRequest(BaseModel):
    url: str | None = None
    event_types: list[str] | None = None
    status: str | None = None


class RotateSecretRequest(BaseModel):
    secret: str = Field(min_length=1, max_length=64 * 1024)


class LinkIdentityRequest(BaseModel):
    provider: str
    integration_id: str
    external_user_key: str = Field(min_length=1, max_length=500)


class LinkConfirmRequest(BaseModel):
    provider: str
    integration_id: str
    code: str = Field(min_length=1, max_length=32)


class VcsRefSpec(BaseModel):
    type: str
    url: str | None = None
    id: str | None = None


class VcsLinkCreateRequest(BaseModel):
    integration_id: str
    vcs_ref: VcsRefSpec
    mesh_entity_type: str = "issue"
    issue_id: str


class VcsResolveRequest(BaseModel):
    integration_id: str
    source_text: str = Field(max_length=8000)
    vcs_ref: VcsRefSpec


class TestSendRequest(BaseModel):
    """POST .../integrations/{id}/test-send (§3.9)."""

    conversation_ref: str = Field(min_length=1, max_length=512)
    conversation_type: str = Field(default="group", pattern=r"^(group|direct)$")
    user_key: str = Field(default="", max_length=512)


__all__ = [
    "CreateBindingRequest",
    "CreateIntegrationRequest",
    "CreateSubscriptionRequest",
    "LinkConfirmRequest",
    "LinkIdentityRequest",
    "PatchBindingRequest",
    "PatchIntegrationRequest",
    "PatchSubscriptionRequest",
    "RotateSecretRequest",
    "TestSendRequest",
    "VcsLinkCreateRequest",
    "VcsResolveRequest",
    "VcsRefSpec",
]
