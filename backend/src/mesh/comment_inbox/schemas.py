"""Request schemas for the comment & inbox API (comment-inbox.md §3)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class CreateCommentRequest(BaseModel):
    """POST /issues/{issue_id}/comments body (§3.1).

    Mentions are parsed SERVER-SIDE from ``body_markdown`` (§3.5) — there is
    deliberately no client mention field. ``suppress_triggers: true`` =
    notify only, never run (README §6.9 explicit suppression).
    """

    body_markdown: str = Field(min_length=1)
    parent_id: uuid.UUID | None = None
    attachment_ids: list[uuid.UUID] = Field(default_factory=list)
    suppress_triggers: bool = False


class UpdateCommentRequest(BaseModel):
    """PATCH /comments/{comment_id} body (optimistic lock via If-Match)."""

    body_markdown: str = Field(min_length=1)


class AddReactionRequest(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)


class ReadAllRequest(BaseModel):
    filter: str | None = None


class PreferenceEntry(BaseModel):
    event_type: str = Field(min_length=1, max_length=64)
    in_app: bool = True
    email: str = "digest"
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


class PutPreferencesRequest(BaseModel):
    preferences: list[PreferenceEntry] = Field(default_factory=list)


__all__ = [
    "AddReactionRequest",
    "CreateCommentRequest",
    "PreferenceEntry",
    "PutPreferencesRequest",
    "ReadAllRequest",
    "UpdateCommentRequest",
]
