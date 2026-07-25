"""Audit log model — append-only trail (auth.md §2.6/§5.5, owns the DDL).

Actor is de-polymorphised (README §6.1): ``actor_member_id`` + ``actor_kind``
only — human vs agent is derived by JOINing ``members.member_type``, never a
stored discriminator. ``actor_kind='system'`` rows carry NULL ``actor_member_id``;
account-level events carry NULL ``workspace_id`` (the composite FK is then
unchecked per SQL semantics, auth.md §2.6 note).

Append-only is enforced at the database level: the ``mesh_audit_append_only()``
trigger (migration 0003) rejects UPDATE/DELETE and the app role additionally
lacks those privileges.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, text
from sqlalchemy.dialects.postgresql import INET, JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

ACTOR_KIND_VALUES = ("member", "system")


class AuditLog(Base):
    """One audited action. INSERT-only; never UPDATE or DELETE."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), default=None
    )
    actor_member_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    actor_kind: Mapped[str] = mapped_column(TEXT, nullable=False)
    action: Mapped[str] = mapped_column(TEXT, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(TEXT, default=None)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    ip_address: Mapped[str | None] = mapped_column(INET, default=None)
    user_agent: Mapped[str | None] = mapped_column(TEXT, default=None)
    # ``metadata`` is a reserved attribute name on declarative models — the
    # column keeps its canonical name, the ORM attribute is ``metadata_``.
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"actor_kind IN {ACTOR_KIND_VALUES!r}", name="audit_logs_actor_kind"),
        # Same-tenant actor when workspace-scoped (README §6.2; unchecked when
        # workspace_id is NULL per composite-FK semantics).
        ForeignKeyConstraint(
            ("workspace_id", "actor_member_id"),
            ("members.workspace_id", "members.id"),
            name="audit_logs_actor_member_id_members",
        ),
        Index("idx_audit_ws_time", "workspace_id", text("created_at DESC")),
        Index("idx_audit_actor", "workspace_id", "actor_member_id", text("created_at DESC")),
        Index("idx_audit_action", "workspace_id", "action", text("created_at DESC")),
    )
