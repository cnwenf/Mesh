"""Skill models — the four-layer "definition → version → installation → binding" decoupling (skill.md §2).

A *skill* packages domain knowledge / SOPs / reusable workflows for agents:
markdown instructions + executable scripts + reference material.

Layer separation (skill.md §1.1):

* ``skill_sources`` — where a skill came from and its trust level
  (``builtin > user > marketplace > url``); lower trust means stricter review.
* ``skills`` — the logical definition (name, summary, lifecycle status) plus
  the pointer to the currently effective version.
* ``skill_versions`` — IMMUTABLE snapshots (no ``updated_at``): instructions,
  scripts, references, triggers and the declared capability requirements.
  Changes always mint a new version; history is never rewritten.
* ``skill_installations`` — a version brought into a scope
  (``workspace`` / ``agent``) with the granted (approved) capability subset.
* ``agent_skills`` — the binding that actually makes an installed skill
  usable by a concrete agent; bindings may pin ANY historic version
  (canary / rollback), independent of the installation's current version.

Same-tenant / same-parent constraints (README §6.2 rules 1/2/7):

* Every workspace-level table carries ``UNIQUE(workspace_id, id)`` and every
  cross reference is a composite FK including ``workspace_id``.
* ``skill_versions`` additionally builds the OVERLAP unique key
  ``UNIQUE(workspace_id, skill_id, id)``; ``skills.current_version_id``,
  ``skill_installations.skill_version_id`` and ``agent_skills.skill_version_id``
  reference it through overlapping composite FKs so a version of ANOTHER
  skill can never be pointed at — the database rejects it at INSERT.
* ``skill_installations`` builds ``UNIQUE(workspace_id, id, skill_id)`` so
  ``agent_skills.skill_installation_id`` can share the same ``skill_id``
  across both of its overlapping FKs, guaranteeing installation and bound
  version belong to the SAME skill.
* ``skills.current_version_id`` uses PostgreSQL 16 column-level
  ``ON DELETE SET NULL (current_version_id)`` (README §6.2 rule 6) so a
  version cleanup nulls only the pointer, never the tenant key.

Roster linkage (README §6.1): creators / installers / reviewers are HUMAN
members referenced through composite FKs to ``members(workspace_id, id)``;
agent targets reference ``agents(workspace_id, id)``. NO ``*_type`` /
``*_kind`` discriminator columns live on these tables — human/agent
distinction is always a JOIN against ``members.member_type``.

``skill_scripts`` / ``skill_references`` / ``skill_triggers`` are leaf
tables under a version: reachable only through the ``skill_version_id``
parent chain, never referenced cross-module, hence no own ``workspace_id``
(their isolation inherits through the parent chain, README §6.2).

``skill_import_tasks`` is the module-internal ledger for the asynchronous
import pipeline (skill.md §3.1 ``import`` / ``GET import/{task_id}``, §3.5
``skill_import.progress``): parse → validate → sandbox preview → approval
→ install. It does NOT reuse the ``approvals`` table — skill import review
is a gate inside the import state machine (skill.md top anchor note).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

SKILL_STATUS_VALUES = ("draft", "published", "deprecated", "disabled")
SKILL_VERSION_STATUS_VALUES = ("draft", "published", "deprecated")
SKILL_INSTALLATION_STATUS_VALUES = ("installed", "updated_available", "disabled")
SKILL_SCOPE_VALUES = ("workspace", "agent")
SKILL_SOURCE_TYPE_VALUES = ("builtin", "user", "marketplace", "url")
SKILL_TRUST_LEVEL_VALUES = ("trusted", "reviewed", "untrusted")
SKILL_TRIGGER_TYPE_VALUES = ("keyword", "semantic", "tag")

# Import pipeline state machine (skill.md §3.1/§3.5/§4.3): parsing →
# validating → sandbox_preview → (untrusted-with-scripts: awaiting_review)
# → ready → installing → installed. Terminal failures: failed / rejected.
SKILL_IMPORT_STATUS_VALUES = (
    "parsing",
    "validating",
    "sandbox_preview",
    "awaiting_review",
    "ready",
    "installing",
    "installed",
    "failed",
    "rejected",
)

# Trust levels by source type (skill.md §2.6). builtin is implicitly trusted
# (platform injected); user uploads are reviewed at creation; marketplace and
# url sources are untrusted until a human approves scripts/permissions.
TRUST_LEVEL_BY_SOURCE_TYPE: dict[str, str] = {
    "builtin": "trusted",
    "user": "reviewed",
    "marketplace": "untrusted",
    "url": "untrusted",
}


class SkillSource(Base):
    """A skill provenance record with its trust level (skill.md §2.6).

    ``auth_ref`` holds ONLY a secret-manager reference key — never a raw
    credential (skill.md §5.3, README §6.16).
    """

    __tablename__ = "skill_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'user'")
    )
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    uri: Mapped[str | None] = mapped_column(TEXT, default=None)
    trust_level: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'untrusted'")
    )
    auth_ref: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(
            f"source_type IN {SKILL_SOURCE_TYPE_VALUES!r}", name="skill_sources_source_type"
        ),
        CheckConstraint(
            f"trust_level IN {SKILL_TRUST_LEVEL_VALUES!r}", name="skill_sources_trust_level"
        ),
        CheckConstraint("char_length(name) BETWEEN 1 AND 300", name="skill_sources_name_len"),
        # Composite-FK reference target for skills.source_id (README §6.2).
        Index("uq_skill_source_ws_id", "workspace_id", "id", unique=True),
        Index("idx_source_workspace_type", "workspace_id", "source_type"),
    )


class Skill(Base):
    """A skill definition — the logical entity (skill.md §2.2)."""

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    slug: Mapped[str] = mapped_column(TEXT, nullable=False)
    summary: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'draft'")
    )
    # Overlapping composite FK → skill_versions(workspace_id, skill_id, id):
    # the current version MUST belong to THIS skill (README §6.2 rule 7).
    # Column-level SET NULL (PG16, README §6.2 rule 6) keeps workspace_id.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    required_capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(TEXT), nullable=False, server_default=text("'{}'::text[]")
    )
    icon: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(f"status IN {SKILL_STATUS_VALUES!r}", name="skills_status"),
        CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]*$'", name="skills_slug_format"
        ),
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="skills_name_len"),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 1000", name="skills_summary_len"),
        # Composite FK → skill_sources(workspace_id, id) (README §6.2 rule 2).
        ForeignKeyConstraint(
            ("workspace_id", "source_id"),
            ("skill_sources.workspace_id", "skill_sources.id"),
            ondelete="RESTRICT",
            name="skills_source_id_skill_sources",
        ),
        # Creator is a roster member of the SAME workspace (README §6.1/§6.2).
        ForeignKeyConstraint(
            ("workspace_id", "created_by"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="skills_created_by_members",
        ),
        # Same-parent overlap: current version must belong to THIS skill.
        ForeignKeyConstraint(
            ("workspace_id", "id", "current_version_id"),
            ("skill_versions.workspace_id", "skill_versions.skill_id", "skill_versions.id"),
            ondelete="SET NULL (current_version_id)",
            name="skills_current_version_id_skill_versions",
        ),
        # Composite-FK reference target (README §6.2 rule 1).
        Index("uq_skill_ws_id", "workspace_id", "id", unique=True),
        # Slug unique within the workspace (soft-delete scoped).
        Index(
            "uq_skill_workspace_slug",
            "workspace_id",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_skill_workspace_status", "workspace_id", "status"),
        Index("idx_skill_sources", "source_id"),
        Index("idx_skill_tags", "tags", postgresql_using="gin"),
    )


class SkillVersion(Base):
    """An immutable version snapshot (skill.md §2.3).

    No ``updated_at``: once ``status='published'`` the row is frozen and any
    change must mint a new version. ``content_hash`` covers instructions +
    script contents for de-duplication / change detection (e.g. whether an
    auto-update PATCH is non-breaking — skill.md §4.4).
    """

    __tablename__ = "skill_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Redundant tenant column — same workspace as the owning skill; required
    # for the composite FKs and overlap unique keys (README §6.2).
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[str] = mapped_column(TEXT, nullable=False)
    instructions: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'draft'")
    )
    changelog: Mapped[str | None] = mapped_column(TEXT, default=None)
    io_contract: Mapped[dict | None] = mapped_column(JSONB, default=None)
    required_capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    manifest: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    content_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN {SKILL_VERSION_STATUS_VALUES!r}", name="skill_versions_status"
        ),
        # SemVer-ish guard: digits and dots only (e.g. 1.2.0, 2.0.0-beta.1
        # pre-release suffixes allowed via the service validator, not here).
        CheckConstraint("char_length(version) BETWEEN 1 AND 64", name="skill_versions_version_len"),
        CheckConstraint(
            "content_hash ~ '^[a-f0-9]{64}$'", name="skill_versions_content_hash"
        ),
        # Owning skill must live in the SAME workspace (README §6.2 rule 2).
        ForeignKeyConstraint(
            ("workspace_id", "skill_id"),
            ("skills.workspace_id", "skills.id"),
            ondelete="CASCADE",
            name="skill_versions_skill_id_skills",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "created_by"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="skill_versions_created_by_members",
        ),
        # OVERLAP unique key — target of the same-skill overlapping composite
        # FKs from skills / skill_installations / agent_skills (README §6.2
        # rule 7): referenced version is guaranteed to belong to the SAME skill.
        Index("uq_skill_version_ws_skill_id", "workspace_id", "skill_id", "id", unique=True),
        # Generic composite-FK reference target (README §6.2 rule 1).
        Index("uq_skill_version_ws_id", "workspace_id", "id", unique=True),
        # A skill's version number is unique (immutable snapshots).
        Index("uq_skill_versions", "skill_id", "version", unique=True),
        Index("idx_skill_version_skill", "skill_id", text("created_at DESC")),
    )


class SkillInstallation(Base):
    """A version brought into a workspace/agent scope (skill.md §2.4).

    ``granted_capabilities`` is the APPROVED subset of the version's
    ``required_capabilities`` (skill.md §5.3: granting anything undeclared
    returns 422 ``capability_not_declared``). ``scope='agent'`` requires a
    non-null ``agent_id`` (CHECK below + service validation → 400).
    """

    __tablename__ = "skill_installations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Same-skill overlapping composite FK (README §6.2 rule 7): the installed
    # version MUST belong to the installed skill.
    skill_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scope: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'workspace'")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    install_status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'installed'")
    )
    auto_update: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    granted_capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    installed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(
            f"install_status IN {SKILL_INSTALLATION_STATUS_VALUES!r}",
            name="skill_installations_install_status",
        ),
        CheckConstraint(f"scope IN {SKILL_SCOPE_VALUES!r}", name="skill_installations_scope"),
        # Agent-scoped installations must name the agent (skill.md §2.4).
        CheckConstraint(
            "scope = 'workspace' OR agent_id IS NOT NULL",
            name="skill_installations_agent_scope_requires_agent",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "skill_id"),
            ("skills.workspace_id", "skills.id"),
            ondelete="CASCADE",
            name="skill_installations_skill_id_skills",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "skill_id", "skill_version_id"),
            (
                "skill_versions.workspace_id",
                "skill_versions.skill_id",
                "skill_versions.id",
            ),
            ondelete="RESTRICT",
            name="skill_installations_skill_version_id_skill_versions",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "agent_id"),
            ("agents.workspace_id", "agents.id"),
            ondelete="CASCADE",
            name="skill_installations_agent_id_agents",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "installed_by"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="skill_installations_installed_by_members",
        ),
        # Composite-FK reference targets (README §6.2 rules 1/7).
        Index("uq_skill_installation_ws_id", "workspace_id", "id", unique=True),
        Index(
            "uq_skill_installation_ws_skill_id", "workspace_id", "id", "skill_id", unique=True
        ),
        # One installation per scope (soft-delete scoped). NULLs in agent_id
        # are treated as a single group by the NULLS NOT DISTINCT semantics
        # of PostgreSQL 16 partial unique indexes.
        Index(
            "uq_install_scope",
            "workspace_id",
            "skill_id",
            "scope",
            "agent_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_install_workspace", "workspace_id", "install_status"),
        Index("idx_install_skill_versions", "skill_version_id"),
        Index(
            "idx_install_updated",
            "install_status",
            postgresql_where=text("install_status = 'updated_available'"),
        ),
    )


class AgentSkill(Base):
    """A binding: agent ↔ installed skill version (skill.md §2.5).

    The redundant ``skill_id`` parent key lets BOTH overlapping composite FKs
    share it — the database therefore guarantees the bound installation and
    the bound version belong to the SAME skill (README §6.2 rule 7): binding
    another skill's version, or a version of a different skill than the
    installation's, is rejected at INSERT.
    """

    __tablename__ = "agent_skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    skill_installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    skill_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    auto_trigger: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("priority BETWEEN 0 AND 1000", name="agent_skills_priority_range"),
        ForeignKeyConstraint(
            ("workspace_id", "agent_id"),
            ("agents.workspace_id", "agents.id"),
            ondelete="CASCADE",
            name="agent_skills_agent_id_agents",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "skill_id"),
            ("skills.workspace_id", "skills.id"),
            ondelete="CASCADE",
            name="agent_skills_skill_id_skills",
        ),
        # Installation belongs to the SAME skill (shared skill_id).
        ForeignKeyConstraint(
            ("workspace_id", "skill_installation_id", "skill_id"),
            ("skill_installations.workspace_id", "skill_installations.id",
             "skill_installations.skill_id"),
            ondelete="CASCADE",
            name="agent_skills_skill_installation_id_skill_installations",
        ),
        # Bound version belongs to the SAME skill (README §6.2 rule 7).
        ForeignKeyConstraint(
            ("workspace_id", "skill_id", "skill_version_id"),
            ("skill_versions.workspace_id", "skill_versions.skill_id", "skill_versions.id"),
            ondelete="RESTRICT",
            name="agent_skills_skill_version_id_skill_versions",
        ),
        # An agent binds a given installation only once.
        Index("uq_agent_skills", "agent_id", "skill_installation_id", unique=True),
        Index("idx_agent_skill_agent", "agent_id", "enabled"),
        Index("idx_agent_skill_install", "skill_installation_id"),
    )


class SkillScript(Base):
    """An executable script under a version (skill.md §2.7).

    ``content_ref`` points at object storage (attachment.md) — large bodies
    are never stored inline. Leaf table: isolation inherits through the
    ``skill_versions → skills`` parent chain.
    """

    __tablename__ = "skill_scripts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    skill_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(TEXT, nullable=False)
    runtime: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'shell'")
    )
    entrypoint: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    content_ref: Mapped[str] = mapped_column(TEXT, nullable=False)
    content_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    required_capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("char_length(path) BETWEEN 1 AND 512", name="skill_scripts_path_len"),
        CheckConstraint(
            "content_hash ~ '^[a-f0-9]{64}$'", name="skill_scripts_content_hash"
        ),
        Index("uq_script_version_path", "skill_version_id", "path", unique=True),
    )


class SkillReference(Base):
    """A reference document under a version (skill.md §2.7)."""

    __tablename__ = "skill_references"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    skill_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(TEXT, nullable=False)
    media_type: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'text/markdown'")
    )
    content_ref: Mapped[str] = mapped_column(TEXT, nullable=False)
    summary: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(path) BETWEEN 1 AND 512", name="skill_references_path_len"
        ),
        Index("idx_reference_version", "skill_version_id"),
    )


class SkillTrigger(Base):
    """An auto-trigger rule under a version (skill.md §2.7 / §4.5)."""

    __tablename__ = "skill_triggers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    skill_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger_type: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'keyword'")
    )
    pattern: Mapped[str] = mapped_column(TEXT, nullable=False)
    weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("1.0")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"trigger_type IN {SKILL_TRIGGER_TYPE_VALUES!r}", name="skill_triggers_trigger_type"
        ),
        CheckConstraint("weight >= 0", name="skill_triggers_weight_non_negative"),
        Index("idx_trigger_version", "skill_version_id"),
        # Keyword full-text index (skill.md §2.8 / §5.2).
        Index(
            "idx_trigger_keyword",
            text("to_tsvector('simple', pattern)"),
            postgresql_using="gin",
            postgresql_where=text("trigger_type = 'keyword'"),
        ),
    )


class SkillImportTask(Base):
    """The asynchronous import pipeline ledger (skill.md §3.1 / §3.5).

    Stages: ``parsing`` (source fetch + manifest parse) → ``validating``
    (schema + semantic checks) → ``sandbox_preview`` (preview assembly, no
    execution — execution belongs to runtime.md) → ``awaiting_review``
    (untrusted sources WITH scripts require human approval, skill.md §5.3)
    → ``ready`` → ``installing`` → ``installed``. ``failed`` / ``rejected``
    are terminal.

    Progress is broadcast through ``skill_import.progress`` (skill.md §3.5)
    and polling ``GET /skills/import/{task_id}`` is the documented fallback
    when no WebSocket is connected.
    """

    __tablename__ = "skill_import_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    uri: Mapped[str | None] = mapped_column(TEXT, default=None)
    ref: Mapped[str | None] = mapped_column(TEXT, default=None)
    status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'parsing'")
    )
    stage: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'manifest_parse'")
    )
    percent: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    preview: Mapped[dict | None] = mapped_column(JSONB, default=None)
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    skill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    skill_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    granted_capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(TEXT, default=None)
    decision_comment: Mapped[str | None] = mapped_column(TEXT, default=None)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN {SKILL_IMPORT_STATUS_VALUES!r}", name="skill_import_tasks_status"
        ),
        CheckConstraint(
            f"source_type IN {SKILL_SOURCE_TYPE_VALUES!r}",
            name="skill_import_tasks_source_type",
        ),
        CheckConstraint("percent BETWEEN 0 AND 100", name="skill_import_tasks_percent_range"),
        ForeignKeyConstraint(
            ("workspace_id", "created_by"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="skill_import_tasks_created_by_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "reviewed_by"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="skill_import_tasks_reviewed_by_members",
        ),
        Index("uq_skill_import_task_ws_id", "workspace_id", "id", unique=True),
        Index(
            "idx_skill_import_tasks_status",
            "status",
            postgresql_where=text("status IN ('parsing', 'validating', 'sandbox_preview')"),
        ),
    )


def installation_matches_binding_agent():
    """SQL predicate enforcing ownership of agent-scoped installations.

    Workspace installations may be shared by any binding. An agent-scoped
    installation is private state and is valid only when its ``agent_id``
    matches the binding's target. Keeping this relational invariant in one
    expression lets every legacy-row consumer fail closed consistently.
    """
    return (SkillInstallation.scope == "workspace") | (
        SkillInstallation.agent_id == AgentSkill.agent_id
    )
