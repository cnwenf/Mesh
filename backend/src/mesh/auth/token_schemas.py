"""Request schemas for the API-token endpoints (auth.md §3.2).

Response payloads are built by :class:`~mesh.auth.tokens.TokenService`; the
plaintext ``token`` field appears only in the create response.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateTokenRequest(BaseModel):
    """POST /workspaces/{ws}/api-tokens.

    ``owner_member_id`` omitted → a PAT for the caller's own member row. When
    set (admin creating for another member / an agent), the route additionally
    requires an admin-or-owner role.
    """

    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    role_override: str | None = None
    expires_at: datetime | None = None
    owner_member_id: str | None = None
