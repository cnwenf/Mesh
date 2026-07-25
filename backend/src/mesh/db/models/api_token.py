"""API token model — personal / agent access tokens (auth.md §2.5, owns DDL).

Holder is de-polymorphised (README §6.1): a single ``owner_member_id`` roster
reference — a human PAT points at the owner's own member row, an agent runtime
credential points at the agent's member row (``members.member_type='agent'``).
Human vs agent is derived by JOINing ``members.member_type``, never stored.

The plaintext token exists only in the create response; the database stores the
SHA-256 ``token_hash``. ``prefix`` (e.g. ``mesh_pat_Ab3…``) is the non-secret
display fragment. ``role_override`` may NOT exceed the holder's current role —
enforced both at creation and at use (auth.md §5.5 double validation).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, text
from sqlalchemy.dialects.postgresql import ARRAY, INET, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

# Token kind prefixes — self-describing so the authenticator can route by type
# and the UI can label PAT vs agent credential (auth.md §2.5 design note).
PAT_TOKEN_PREFIX = "mesh_pat_"
AGENT_TOKEN_PREFIX = "mesh_agt_"
TOKEN_PREFIXES = (PAT_TOKEN_PREFIX, AGENT_TOKEN_PREFIX)
# Display prefix length stored for listing (non-secret fragment).
DISPLAY_PREFIX_LEN = 12


class ApiToken(Base):
    """A revocable workspace access token. Hash-only; plaintext shown once."""

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    token_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    prefix: Mapped[str] = mapped_column(TEXT, nullable=False)
    scopes: Mapped[list] = mapped_column(
        ARRAY(TEXT), nullable=False, server_default=text("'{}'")
    )
    role_override: Mapped[str | None] = mapped_column(TEXT, default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    last_used_ip: Mapped[str | None] = mapped_column(INET, default=None)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Same-tenant holder (README §6.2): rejects a foreign-workspace member.
        ForeignKeyConstraint(
            ("workspace_id", "owner_member_id"),
            ("members.workspace_id", "members.id"),
            name="api_tokens_owner_member_id_members",
        ),
        Index("uq_api_token_hash", "token_hash", unique=True),
        Index(
            "idx_api_tokens_owner",
            "workspace_id",
            "owner_member_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        # Composite-FK reference target (README §6.2).
        Index("uq_api_tokens_ws_id", "workspace_id", "id", unique=True),
    )
