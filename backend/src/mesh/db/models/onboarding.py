"""Onboarding models (onboarding.md §2 — this module owns these tables).

``onboarding_states`` is the per-member per-workspace per-checklist progress
record; ``onboarding_state_steps`` is the step detail child table (one row per
step). Auto-completion is driven by outbox domain events (README §6.6) with
idempotent conditional-UPDATE guards (§3.5); the step child table exists so
domain events can target one ``step_key`` row precisely and leave a per-step
``completed_at`` / ``evidence`` audit trail (a JSONB blob rewrite would be
hostile to concurrent auto-detection and step-level audit).

The module holds NO foreign keys to business entities (issues / agents /
executions / comments): auto-completion consumes outbox events plus
workspace-scoped queries (§3.6). Cross-module relations are the tenant root
(``workspaces``) and the member owner (composite FK → ``members(workspace_id,
id)``, README §6.1 / §6.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, text
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

# Built-in checklist identifier (§1.2.1 — only ``activation`` is built in;
# the column is reserved for future checklists, YAGNI until then).
ACTIVATION_CHECKLIST = "activation"

# Activation path steps in order (§1.2.1): create workspace → invite member /
# add agent → create first issue → dispatch / @-mention trigger first run →
# see the agent reply in the inbox (aha moment).
STEP_CREATE_WORKSPACE = "create_workspace"
STEP_INVITE_MEMBER_OR_ADD_AGENT = "invite_member_or_add_agent"
STEP_CREATE_FIRST_ISSUE = "create_first_issue"
STEP_DISPATCH_OR_MENTION_AGENT = "dispatch_or_mention_agent"
STEP_SEE_AGENT_REPLY_IN_INBOX = "see_agent_reply_in_inbox"

ACTIVATION_STEP_KEYS: tuple[str, ...] = (
    STEP_CREATE_WORKSPACE,
    STEP_INVITE_MEMBER_OR_ADD_AGENT,
    STEP_CREATE_FIRST_ISSUE,
    STEP_DISPATCH_OR_MENTION_AGENT,
    STEP_SEE_AGENT_REPLY_IN_INBOX,
)

STEP_STATUS_PENDING = "pending"
STEP_STATUS_COMPLETED = "completed"
STEP_STATUS_SKIPPED = "skipped"
STEP_STATUS_VALUES: tuple[str, ...] = (
    STEP_STATUS_PENDING,
    STEP_STATUS_COMPLETED,
    STEP_STATUS_SKIPPED,
)

COMPLETED_VIA_AUTO = "auto"
COMPLETED_VIA_MANUAL = "manual"
COMPLETED_VIA_VALUES: tuple[str, ...] = (COMPLETED_VIA_AUTO, COMPLETED_VIA_MANUAL)


class OnboardingState(Base):
    """One checklist progress record per member × workspace × checklist (§2.2)."""

    __tablename__ = "onboarding_states"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    checklist: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text(f"'{ACTIVATION_CHECKLIST}'")
    )
    # Aha moment reached at — set exactly once when the final step completes
    # (§3.5 conditional UPDATE on aha_reached_at IS NULL).
    aha_reached_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(checklist) BETWEEN 1 AND 40", name="onboarding_states_checklist_len"
        ),
        # One record per member per workspace per checklist — the database
        # basis for idempotent create/get (§3.5).
        Index(
            "uq_onboarding_states_ws_member_checklist",
            "workspace_id",
            "member_id",
            "checklist",
            unique=True,
        ),
        # Composite-FK reference target for onboarding_state_steps (README §6.2).
        Index("uq_onboarding_states_ws_id", "workspace_id", "id", unique=True),
        # Admin reset / funnel inspection: checklists without aha, per workspace.
        Index(
            "idx_onboarding_states_ws_aha",
            "workspace_id",
            "created_at",
            postgresql_where=text("aha_reached_at IS NULL"),
        ),
        ForeignKeyConstraint(
            ("workspace_id", "member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="CASCADE",
            name="onboarding_states_member_id_members",
        ),
    )


class OnboardingStateStep(Base):
    """One step detail row per checklist (§2.3)."""

    __tablename__ = "onboarding_state_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    state_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text(f"'{STEP_STATUS_PENDING}'")
    )
    completed_via: Mapped[str | None] = mapped_column(TEXT, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    # R3 completion evidence (§2.3): {member_added_id?} (step 2) /
    # {issue_id?, reporter_member_id?} (step 3) / {execution_id?,
    # trigger_member_id?} (step 4) / {execution_id?, comment_id?,
    # notification_id?, trigger_member_id?} (step 5); {"manual_by": ...} when
    # completed_via='manual'.
    evidence: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"step_key IN {ACTIVATION_STEP_KEYS!r}", name="onboarding_state_steps_step_key"
        ),
        CheckConstraint(
            f"status IN {STEP_STATUS_VALUES!r}", name="onboarding_state_steps_status"
        ),
        CheckConstraint(
            f"completed_via IS NULL OR completed_via IN {COMPLETED_VIA_VALUES!r}",
            name="onboarding_state_steps_completed_via",
        ),
        # Completion state and completion time are consistent (completed ⇔
        # completed_at present) — §2.3 table-level CHECK.
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="onboarding_state_steps_completed_consistency",
        ),
        # One row per step per checklist.
        Index(
            "uq_onboarding_steps_ws_state_step",
            "workspace_id",
            "state_id",
            "step_key",
            unique=True,
        ),
        # Composite-FK reference target shape (README §6.2).
        Index("uq_onboarding_steps_ws_id", "workspace_id", "id", unique=True),
        # Auto-detection: locate incomplete steps of one key within a
        # workspace (precise UPDATE scope for domain-event consumption).
        Index(
            "idx_onboarding_steps_pending",
            "workspace_id",
            "step_key",
            postgresql_where=text("status <> 'completed'"),
        ),
        ForeignKeyConstraint(
            ("workspace_id", "state_id"),
            ("onboarding_states.workspace_id", "onboarding_states.id"),
            ondelete="CASCADE",
            name="onboarding_state_steps_state_id_onboarding_states",
        ),
    )
