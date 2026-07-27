"""Pydantic request schemas for attachment endpoints (attachment.md §3).

UUIDs travel as strings (house convention); routes parse them and map bad
shapes to 404/400 per §5.3. ``extra="forbid"`` rejects unknown fields at the
boundary (§6.14).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LinkTarget(BaseModel):
    """Host entity an attachment is associated with (§3.2 link_to)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(description="issue | comment | chat_message")
    id: str
    display: str | None = Field(default=None, description="inline | card")
    position: int = 0


class UploadRequestBody(BaseModel):
    """POST /attachments/upload-requests (§3.2)."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(
        default=None,
        description="Required when link_to is absent (agent/API-token uploads may omit it).",
    )
    file_name: str = Field(min_length=1, max_length=255)
    file_size: int = Field(gt=0)
    mime_type: str = Field(min_length=1, max_length=127)
    content_hash: str | None = Field(default=None, max_length=128)
    link_to: LinkTarget | None = None


class CompleteBody(BaseModel):
    """POST /attachments/{id}/complete (§3.3)."""

    model_config = ConfigDict(extra="forbid")

    file_size: int | None = Field(default=None, gt=0)
    content_hash: str | None = Field(default=None, max_length=128)


class MultipartPartsBody(BaseModel):
    """POST /multipart/{id}/parts — sign the next batch of parts."""

    model_config = ConfigDict(extra="forbid")

    part_numbers: list[int] = Field(min_length=1, max_length=100)


class MultipartPartEtag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_number: int = Field(ge=1)
    etag: str = Field(min_length=1, max_length=256)


class MultipartCompleteBody(BaseModel):
    """POST /multipart/{id}/complete — merge parts and finish the upload."""

    model_config = ConfigDict(extra="forbid")

    parts: list[MultipartPartEtag] = Field(min_length=1)
    content_hash: str | None = Field(default=None, max_length=128)
