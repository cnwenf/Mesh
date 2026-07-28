"""Chat request schemas (chat-session.md §3.1-§3.2)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

MESSAGE_CONTENT_MAX_CHARS = 20_000
SESSION_TITLE_MAX_CHARS = 120


class CreateChatSessionRequest(BaseModel):
    """POST /workspaces/{ws}/chat-sessions (§3.2)."""

    agent_id: UUID
    context_issue_id: UUID | None = None
    context_project_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=SESSION_TITLE_MAX_CHARS)


class PatchChatSessionRequest(BaseModel):
    """PATCH body; omitted fields stay untouched (UNSET in the service)."""

    title: str | None = Field(default=None, min_length=1, max_length=SESSION_TITLE_MAX_CHARS)
    status: str | None = Field(default=None, pattern=r"^(active|archived)$")
    context_issue_id: UUID | None = None
    context_project_id: UUID | None = None


class SendMessageRequest(BaseModel):
    """POST .../messages (§3.3 step 1 — creates a generation)."""

    content: str = Field(min_length=1, max_length=MESSAGE_CONTENT_MAX_CHARS)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=20)
    quote_message_id: UUID | None = None


class SelectCandidateRequest(BaseModel):
    """POST .../messages/{msg_id}/select (§3.3)."""

    selected_message_id: UUID


class DistillPreviewRequest(BaseModel):
    """POST .../distill-preview — render the 沉淀为评论 confirmation payload."""

    body_markdown: str = Field(min_length=1, max_length=MESSAGE_CONTENT_MAX_CHARS)
    target_issue_id: UUID | None = None
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=20)
