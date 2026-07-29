"""Integrations platform data model (docs/specs/features/integrations.md §2).

Tables:

* ``integrations`` — connector instances (``kind`` + NON-secret ``config``
  JSONB + ``secret_ref`` ciphertext, README §6.16 contract — same as
  ``runtime_credentials.encrypted_value``); tenant-scoped, soft delete.
* ``integration_bindings`` — external identity ↔ workspace/project binding
  with match rules and target agent. GLOBAL external-identity key
  ``UNIQUE(provider, provider_tenant_key, external_ref)`` (R3/§6.17) and
  exact-XOR scope CHECK (workspace scope carries no project; project scope
  requires one).
* ``integration_events`` — inbound ingestion ledger (signature result +
  ``UNIQUE(integration_id, external_event_id)`` dedup + full audit),
  isomorphic to autopilot ``webhook_events`` but independent.
* ``external_identities`` — GLOBAL identity table (R5/README §6.1): external
  platform account ↔ ``users.id``. NO ``workspace_id`` ownership column,
  exempt from workspace RLS; link origin is only the nullable audit column
  ``created_in_workspace_id`` (``ON DELETE SET NULL`` — deleting the origin
  workspace never cascades into the global mapping).
* ``webhook_subscriptions`` / ``webhook_subscription_deliveries`` — outbound
  developer webhooks: subscription state + circuit breaker, delivery ledger
  with ``UNIQUE(subscription_id, event_ref)`` idempotency (README §6.5).
* ``vcs_links`` — VCS object ↔ Mesh entity link truth source (R3) with
  partial-unique active indexes.

Composite FKs follow the README §6.2 same-tenant pattern; nullable composite
references use the PG16 column-level ``ON DELETE SET NULL (<column>)`` form
(§6.2 rule 6).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

# integrations.md §2.2 — connector kinds (decide the adapter).
INTEGRATION_KIND_VALUES = (
    "im_feishu",
    "im_slack",
    "im_dingtalk",
    "vcs_github",
    "vcs_gitlab",
    "webhook_outbound",
)

# Normalized provider identifiers (R3): bindings/identities/vcs_links use the
# provider dimension of the global external-identity key.
BINDING_PROVIDER_VALUES = ("feishu", "slack", "dingtalk", "github", "gitlab", "webhook")
IDENTITY_PROVIDER_VALUES = ("feishu", "slack", "dingtalk", "github", "gitlab")
VCS_PROVIDER_VALUES = ("github", "gitlab")

INTEGRATION_STATUS_VALUES = ("active", "disabled")

# Connector health (independent of the manual active/disabled status):
# driven by the connectivity test endpoint, outbound credential-refresh
# failures (``auth_failed`` → "re-authorize" banner) and delivery outcomes.
INTEGRATION_HEALTH_VALUES = ("unknown", "healthy", "auth_failed", "unreachable")
BINDING_SCOPE_VALUES = ("workspace", "project")

# integrations.md §2.4 — signature outcomes; invalid/missing are ALWAYS
# rejected + 401, never dispatched.
SIGNATURE_STATUS_VALUES = ("valid", "invalid", "missing")

# integrations.md §2.4 — ingestion lifecycle (autopilot webhook_events vocab).
EVENT_PROCESS_STATUS_VALUES = (
    "received",
    "matched",
    "dispatched",
    "deduped",
    "rejected",
    "processed",
    "failed",
)

# integrations.md §2.5 — subscription states: paused = human pause,
# disabled = circuit breaker tripped (consecutive failures over threshold).
SUBSCRIPTION_STATUS_VALUES = ("active", "paused", "disabled")
DELIVERY_STATE_VALUES = ("pending", "sent", "failed")

VCS_OBJECT_TYPE_VALUES = (
    "repository",
    "pull_request",
    "merge_request",
    "issue",
    "commit",
    "branch",
)
VCS_MESH_ENTITY_VALUES = ("issue", "project")
VCS_LINK_SOURCE_VALUES = ("manual", "auto_keyword", "auto_branch", "auto_commit")
VCS_LINK_STATUS_VALUES = ("active", "stale", "deleted")


class Integration(Base):
    """A connector instance (integrations.md §2.2).

    ``config`` is NON-secret platform configuration (app_id, external tenant
    identifiers, callback base, card template); secrets are stored only as
    Fernet ciphertext in ``secret_ref`` (README §6.16 — responses/logs never
    echo plaintext).
    """

    __tablename__ = "integrations"
    __table_args__ = (
        CheckConstraint(f"kind IN {INTEGRATION_KIND_VALUES!r}", name="integrations_kind"),
        CheckConstraint(
            f"health_state IN {INTEGRATION_HEALTH_VALUES!r}", name="integrations_health_state"
        ),
        CheckConstraint(
            f"status IN {INTEGRATION_STATUS_VALUES!r}", name="integrations_status"
        ),
        ForeignKeyConstraint(
            ("workspace_id", "created_by"),
            ("members.workspace_id", "members.id"),
            name="fk_integrations_created_by",
            ondelete="RESTRICT",
        ),
        Index("uq_integrations_ws_id", "workspace_id", "id", unique=True),
        Index(
            "uq_integrations_ws_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_integrations_ws_kind",
            "workspace_id",
            "kind",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(TEXT, nullable=False)
    # MES-82: DingTalk Stream connection-state persistence truth source
    # (integrations.md §2.2/§3.9; only used by kind='im_dingtalk' stream
    # mode — the connector behavior ships in the MES-82 slices).
    stream_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    # Connector health (§2.2): independent of the manual status; driven by
    # the connectivity test endpoint and outbound credential-refresh /
    # delivery outcomes (auth_failed → "re-authorize" banner, §4.1 badge).
    health_state: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'unknown'")
    )
    last_error: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    secret_ref: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class IntegrationBinding(Base):
    """External identity ↔ workspace/project binding (integrations.md §2.3).

    The GLOBAL key ``UNIQUE(provider, provider_tenant_key, external_ref)``
    guarantees one external identity (group/channel/repo on a given platform
    tenant) binds to at most ONE workspace (R3/§6.17). Scope is an exact
    XOR: workspace scope carries no project; project scope requires one —
    project deletion cascades the binding (no unreachable SET-NULL state).
    """

    __tablename__ = "integration_bindings"
    __table_args__ = (
        CheckConstraint(
            f"provider IN {BINDING_PROVIDER_VALUES!r}",
            name="integration_bindings_provider",
        ),
        CheckConstraint(
            f"scope IN {BINDING_SCOPE_VALUES!r}", name="integration_bindings_scope"
        ),
        CheckConstraint(
            f"status IN {INTEGRATION_STATUS_VALUES!r}", name="integration_bindings_status"
        ),
        CheckConstraint(
            "(scope = 'workspace' AND project_id IS NULL) "
            "OR (scope = 'project' AND project_id IS NOT NULL)",
            name="ck_binding_scope",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "integration_id"),
            ("integrations.workspace_id", "integrations.id"),
            name="fk_binding_integration",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            name="fk_binding_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "bound_agent_id"),
            ("agents.workspace_id", "agents.id"),
            name="fk_binding_agent",
            ondelete="SET NULL (bound_agent_id)",
        ),
        Index("uq_integration_bindings_ws_id", "workspace_id", "id", unique=True),
        Index(
            "uq_binding_external_identity",
            "provider",
            "provider_tenant_key",
            "external_ref",
            unique=True,
        ),
        Index("idx_binding_integration", "integration_id", "status"),
        Index(
            "idx_binding_agent",
            "workspace_id",
            "bound_agent_id",
            postgresql_where=text("bound_agent_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(TEXT, nullable=False)
    provider_tenant_key: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("''")
    )
    scope: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'workspace'"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    external_ref: Mapped[str] = mapped_column(TEXT, nullable=False)
    match_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    bound_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class IntegrationEvent(Base):
    """Inbound event ledger (integrations.md §2.4).

    Isomorphic to autopilot ``webhook_events`` but independent. Dedup via
    ``UNIQUE(integration_id, external_event_id)`` — duplicates return an
    idempotent 200 ``deduped`` and are never dispatched twice. Rejected
    events use the ``rejected:<raw-hash>`` external_event_id namespace so
    unsigned forgeries cannot pre-occupy legitimate event ids.
    """

    __tablename__ = "integration_events"
    __table_args__ = (
        CheckConstraint(
            f"signature_status IN {SIGNATURE_STATUS_VALUES!r}",
            name="integration_events_signature_status",
        ),
        CheckConstraint(
            f"process_status IN {EVENT_PROCESS_STATUS_VALUES!r}",
            name="integration_events_process_status",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "integration_id"),
            ("integrations.workspace_id", "integrations.id"),
            name="fk_event_integration",
            ondelete="CASCADE",
        ),
        Index("uq_integration_events_ws_id", "workspace_id", "id", unique=True),
        Index(
            "uq_integration_event_dedup", "integration_id", "external_event_id", unique=True
        ),
        Index(
            "idx_event_integration_status",
            "integration_id",
            "process_status",
            text("received_at DESC"),
        ),
        Index("idx_event_ws_received", "workspace_id", text("received_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    external_event_id: Mapped[str] = mapped_column(TEXT, nullable=False)
    event_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    signature_status: Mapped[str] = mapped_column(TEXT, nullable=False)
    process_status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'received'")
    )
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class ExternalIdentity(Base):
    """External platform account ↔ Mesh user mapping (integrations.md §2.4.1).

    GLOBAL identity table (R5): same tier as ``users`` — NO ``workspace_id``
    ownership column, exempt from workspace RLS. The identity key
    ``UNIQUE(provider, provider_tenant_key, external_user_key)`` maps one
    external account to at most one ``users.id``; the same mapping row serves
    card approvals across ALL workspaces (each resolves its own member row).
    Link origin is only the nullable audit column ``created_in_workspace_id``
    (``ON DELETE SET NULL`` — never cascades into the mapping). Unlink is
    owner-only (``external_identity_unlink_allowed`` — no admin bypass).
    """

    __tablename__ = "external_identities"
    __table_args__ = (
        CheckConstraint(
            f"provider IN {IDENTITY_PROVIDER_VALUES!r}",
            name="external_identities_provider",
        ),
        Index(
            "uq_external_identity",
            "provider",
            "provider_tenant_key",
            "external_user_key",
            unique=True,
        ),
        Index("idx_external_identities_user", "user_id"),
        Index(
            "idx_external_identities_created_in_ws",
            "created_in_workspace_id",
            postgresql_where=text("created_in_workspace_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    provider: Mapped[str] = mapped_column(TEXT, nullable=False)
    provider_tenant_key: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("''")
    )
    external_user_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_in_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL (created_in_workspace_id)"),
        nullable=True,
    )
    verified_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class WebhookSubscription(Base):
    """Outbound developer webhook subscription (integrations.md §2.5).

    ``url`` is https-only and SSRF-checked (README §6.16); ``secret_ref``
    holds the HMAC-SHA256 signing key ciphertext (plaintext shown exactly
    once at creation). ``fail_count`` drives the subscription-level circuit
    breaker (``disabled`` past threshold, manual resume).
    """

    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        CheckConstraint(
            f"status IN {SUBSCRIPTION_STATUS_VALUES!r}", name="webhook_subscriptions_status"
        ),
        CheckConstraint("fail_count >= 0", name="webhook_subscriptions_fail_count_nonneg"),
        ForeignKeyConstraint(
            ("workspace_id", "integration_id"),
            ("integrations.workspace_id", "integrations.id"),
            name="fk_subscription_integration",
            ondelete="SET NULL (integration_id)",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "created_by"),
            ("members.workspace_id", "members.id"),
            name="fk_subscription_created_by",
            ondelete="RESTRICT",
        ),
        Index("uq_webhook_subscriptions_ws_id", "workspace_id", "id", unique=True),
        Index("idx_subscription_ws_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    url: Mapped[str] = mapped_column(TEXT, nullable=False)
    secret_ref: Mapped[str] = mapped_column(TEXT, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(
        ARRAY(TEXT), nullable=False, server_default=text("'{}'")
    )
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class WebhookSubscriptionDelivery(Base):
    """Outbound delivery ledger (integrations.md §2.6).

    ``UNIQUE(subscription_id, event_ref)`` makes delivery idempotent
    (README §6.5): duplicate outbox dequeues never produce a second ledger
    row. ``next_retry_at`` carries the exponential backoff schedule
    (NULL = terminal).
    """

    __tablename__ = "webhook_subscription_deliveries"
    __table_args__ = (
        CheckConstraint(
            f"state IN {DELIVERY_STATE_VALUES!r}",
            name="webhook_subscription_deliveries_state",
        ),
        CheckConstraint("attempts >= 0", name="webhook_subscription_deliveries_attempts_nonneg"),
        ForeignKeyConstraint(
            ("workspace_id", "subscription_id"),
            ("webhook_subscriptions.workspace_id", "webhook_subscriptions.id"),
            name="fk_delivery_subscription",
            ondelete="CASCADE",
        ),
        Index(
            "uq_delivery_subscription_event", "subscription_id", "event_ref", unique=True
        ),
        Index(
            "idx_delivery_retry",
            "next_retry_at",
            postgresql_where=text("state = 'pending'"),
        ),
        Index(
            "idx_delivery_subscription", "subscription_id", text("created_at DESC")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_ref: Mapped[str] = mapped_column(TEXT, nullable=False)
    # Event type + payload captured at derivation time (§3.4/P8): the
    # delivery worker sends the real event type in the ``Mesh-Event``
    # header and the full payload in the body — subscribers must be able
    # to reconstruct e.g. ``issue.updated`` and its data from a delivery.
    event_type: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("''"))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    state: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_retry_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class VcsLink(Base):
    """VCS object ↔ Mesh entity link truth source (integrations.md §2.8, R3).

    Partial-unique ACTIVE indexes: one external object has at most one
    active link (stale/deleted rows keep history and allow re-linking);
    integration deletion cascades its links. ``mesh_entity_id`` is a
    polymorphic logical FK (README §6.2 rule 4 — the row carries
    ``workspace_id``; soft-delete consistency is a service-layer concern).
    """

    __tablename__ = "vcs_links"
    __table_args__ = (
        CheckConstraint(f"provider IN {VCS_PROVIDER_VALUES!r}", name="vcs_links_provider"),
        CheckConstraint(
            f"external_object_type IN {VCS_OBJECT_TYPE_VALUES!r}",
            name="vcs_links_external_object_type",
        ),
        CheckConstraint(
            f"mesh_entity_type IN {VCS_MESH_ENTITY_VALUES!r}", name="vcs_links_mesh_entity_type"
        ),
        CheckConstraint(
            f"link_source IN {VCS_LINK_SOURCE_VALUES!r}", name="vcs_links_link_source"
        ),
        CheckConstraint(f"status IN {VCS_LINK_STATUS_VALUES!r}", name="vcs_links_status"),
        ForeignKeyConstraint(
            ("workspace_id", "integration_id"),
            ("integrations.workspace_id", "integrations.id"),
            name="fk_vcs_links_integration",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "created_by"),
            ("members.workspace_id", "members.id"),
            name="fk_vcs_links_created_by",
            ondelete="SET NULL (created_by)",
        ),
        Index("uq_vcs_links_ws_id", "workspace_id", "id", unique=True),
        Index(
            "uq_vcs_links_external_object",
            "provider",
            "provider_tenant_key",
            "external_object_type",
            "external_object_ref",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_vcs_links_mesh_entity",
            "workspace_id",
            "mesh_entity_type",
            "mesh_entity_id",
            "external_object_ref",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_vcs_links_entity_status",
            "workspace_id",
            "mesh_entity_type",
            "mesh_entity_id",
            "status",
        ),
        Index("idx_vcs_links_integration_status", "integration_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(TEXT, nullable=False)
    provider_tenant_key: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("''")
    )
    external_object_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    external_object_ref: Mapped[str] = mapped_column(TEXT, nullable=False)
    mesh_entity_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    mesh_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    link_source: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'manual'")
    )
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    external_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# integrations.md §2.10 (MES-82) — inbound-message conversation FIFO queue.
QUEUE_DISPATCH_MODE_VALUES = ("serial_conversation", "parallel")
QUEUE_STATE_VALUES = (
    "pending",
    "dispatching",
    "processing",
    "cancelling",
    "done",
    "failed",
    "cancelled",
)
# In-flight states occupying the serial conversation lane (the partial
# unique index covers exactly these; ``cancelling`` keeps the lane until
# terminal — /stop does not early-release the next item, §2.10/T39-4).
QUEUE_INFLIGHT_STATES = ("dispatching", "processing", "cancelling")
QUEUE_TERMINAL_STATES = ("done", "failed", "cancelled")

# runtime.md runtime context appends (MES-82): registered sources.
CONTEXT_APPEND_SOURCE_VALUES = ("im_btw",)


class IntegrationMessageQueue(Base):
    """Inbound-message conversation-level FIFO queue (integrations.md §2.10).

    MES-82 "new messages auto-queue" truth source. Serial conversations
    admit at most ONE in-flight item (partial unique index over
    dispatching/processing/cancelling, parallel mode exempt); ``seq`` per
    ``conversation_key`` is the FIFO truth. Delete protection is
    fail-closed: parent SET NULL + ``ck_imq_orphan_terminal`` rejects a
    non-terminal item losing its parent (the DELETE rolls back entirely),
    so messages are never silently orphaned; surviving orphan rows are
    self-describing terminal audit rows (``binding_display`` snapshot).
    ``ack_*`` columns carry the emoji-acknowledgement window protocol
    (§3.8: leader/follower suppression, single outbound per window).
    """

    __tablename__ = "integration_message_queue"
    __table_args__ = (
        CheckConstraint("seq > 0", name="integration_message_queue_seq_positive"),
        CheckConstraint(
            f"dispatch_mode IN {QUEUE_DISPATCH_MODE_VALUES!r}",
            name="integration_message_queue_dispatch_mode",
        ),
        CheckConstraint(f"state IN {QUEUE_STATE_VALUES!r}", name="integration_message_queue_state"),
        # Fail-closed orphan protection: SET NULL parents require terminal
        # state (T39-5 — DELETE of a parent with an in-flight item is
        # rejected wholesale, never silently orphaning a live message).
        CheckConstraint(
            "(integration_id IS NOT NULL OR state IN ('done','failed','cancelled')) "
            "AND (binding_id IS NOT NULL OR state IN ('done','failed','cancelled'))",
            name="ck_imq_orphan_terminal",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "integration_id"),
            ("integrations.workspace_id", "integrations.id"),
            name="fk_imq_integration",
            ondelete="SET NULL (integration_id)",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "binding_id"),
            ("integration_bindings.workspace_id", "integration_bindings.id"),
            name="fk_imq_binding",
            ondelete="SET NULL (binding_id)",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "integration_event_id"),
            ("integration_events.workspace_id", "integration_events.id"),
            name="fk_imq_event",
            ondelete="SET NULL (integration_event_id)",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "execution_id"),
            ("task_executions.workspace_id", "task_executions.id"),
            name="fk_imq_execution",
            ondelete="SET NULL (execution_id)",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "target_agent_id"),
            ("agents.workspace_id", "agents.id"),
            name="fk_imq_target_agent",
            ondelete="SET NULL (target_agent_id)",
        ),
        Index("uq_imq_ws_id", "workspace_id", "id", unique=True),
        # NULL integration_id never conflicts (orphan audit rows).
        Index("uq_imq_event", "integration_id", "integration_event_id", unique=True),
        Index("uq_imq_conversation_seq", "conversation_key", "seq", unique=True),
        # Serial in-flight exclusion (parallel exempt).
        Index(
            "uq_imq_conversation_active",
            "conversation_key",
            unique=True,
            postgresql_where=text(
                "state IN ('dispatching','processing','cancelling') "
                "AND dispatch_mode = 'serial_conversation'"
            ),
        ),
        Index(
            "idx_imq_conversation_pending",
            "conversation_key",
            "seq",
            postgresql_where=text("state = 'pending'"),
        ),
        Index(
            "idx_imq_lease",
            "lease_expires_at",
            postgresql_where=text("state IN ('dispatching','processing','cancelling')"),
        ),
        Index(
            "idx_imq_integration_state",
            "integration_id",
            "state",
            text("enqueued_at DESC"),
            "id",
        ),
        Index("idx_imq_ws_state", "workspace_id", "state"),
        Index("idx_imq_binding_state", "binding_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    integration_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Binding display snapshot — orphan audit rows stay self-describing.
    binding_display: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("''")
    )
    # scope='project' capture at enqueue time (orphan-audit visibility).
    project_id_snapshot: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    conversation_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dispatch_mode: Mapped[str] = mapped_column(TEXT, nullable=False)
    state: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'pending'"))
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    target_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    message_excerpt: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("''"))
    sender_identity_key: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("''")
    )
    # Emoji-ack window protocol (§3.8): leader self-references; followers
    # point at the leader that represents them.
    ack_leader_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ack_window_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    ack_attempted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    ack_sent_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    ack_represented_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    ack_merged_into: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    enqueued_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class ExecutionContextAppend(Base):
    """Runtime context-append ledger (runtime.md, MES-82).

    /btw side messages queued against a RUNNING execution (§3.7): each
    append carries a per-execution monotonic ``seq`` (the daemon dedup
    key together with ``execution_id``); ``injected_attempt_id`` is the
    single-pointer injection receipt (attempt scope — requeue clears it,
    so the pending set is ``injected_attempt_id IS NULL``). Per-execution
    append caps (count/chars) are service-layer constants, not DB
    constraints (§2.10 M3).
    """

    __tablename__ = "execution_context_appends"
    __table_args__ = (
        CheckConstraint("seq > 0", name="execution_context_appends_seq_positive"),
        CheckConstraint(
            f"source IN {CONTEXT_APPEND_SOURCE_VALUES!r}",
            name="execution_context_appends_source",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "execution_id"),
            ("task_executions.workspace_id", "task_executions.id"),
            name="fk_eca_execution",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "injected_attempt_id"),
            ("execution_attempts.workspace_id", "execution_attempts.id"),
            name="fk_eca_injected_attempt",
            ondelete="SET NULL (injected_attempt_id)",
        ),
        Index("uq_eca_ws_id", "workspace_id", "id", unique=True),
        Index("uq_eca_execution_seq", "execution_id", "seq", unique=True),
        # Attempt-scoped pending set (matches the daemon GET filter).
        Index(
            "idx_eca_execution_pending",
            "execution_id",
            "seq",
            postgresql_where=text("injected_attempt_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(TEXT, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    injected_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    injected_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
