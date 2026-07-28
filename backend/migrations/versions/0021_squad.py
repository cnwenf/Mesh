"""squad: squads, squad_members, squad_tasks, issue_squad_assignments,
squad_task_dependencies, squad_messages, squad_activity

Stage-7 agent-layer increment A (squad.md §2 / README §6.1 / §6.2 / §6.9 /
§6.10). The multi-agent orchestration unit: a leader decomposes an issue into a
dependency-DAG of subtasks, dispatches them to agent/human members, and
aggregates results. ``issue_squad_assignments`` is the authoritative
"which squad carries this issue" identity — each issue has AT MOST one
``status='active'`` row (partial unique index), and reassignment is judged by
this row, never by ``issues.assignee_id`` (one leader may lead several squads).

Deferred composite FK landed here (reserved bare since 0019):
- ``approvals.(workspace_id, subject_task_id) → squad_tasks(workspace_id, id)``
  for ``subject_type='squad_plan'`` (README §6.10; the partial unique
  ``uq_approvals_pending_task`` already exists from 0019).

All tables tenant-scoped, RLS fail-closed (README §6.2 rule 5). Per README §6.1
NO ``*_type``/``*_kind`` discriminator columns are stored — human/agent resolves
by JOINing ``members.member_type``; system actors use the ``('member','system')``
null-FK pattern (messages ``kind='system'`` / activity ``actor_kind='system'``).

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-28
"""
from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "squads",
    "squad_members",
    "squad_tasks",
    "issue_squad_assignments",
    "squad_task_dependencies",
    "squad_messages",
    "squad_activity",
)

DML_TABLES = ", ".join(TENANT_TABLES)


def upgrade() -> None:
    # -- squads (小队主表, squad.md §2.2) --------------------------------------
    op.execute(
        """
        CREATE TABLE squads (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          name                  TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
          description           TEXT NULL,
          avatar_url            TEXT NULL,
          kind                  TEXT NOT NULL DEFAULT 'standing'
                                CHECK (kind IN ('standing','adhoc','task_scoped')),
          status                TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active','archived')),
          leader_mode           TEXT NOT NULL DEFAULT 'single'
                                CHECK (leader_mode IN ('single','multi')),
          primary_leader_id     UUID NULL,
          require_plan_approval BOOLEAN NOT NULL DEFAULT false,
          max_decompose_depth   SMALLINT NOT NULL DEFAULT 2
                                CHECK (max_decompose_depth BETWEEN 1 AND 4),
          creator_id            UUID NOT NULL,
          archived_at           TIMESTAMPTZ NULL,
          archived_by_id        UUID NULL,
          deleted_at            TIMESTAMPTZ NULL,
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_squads_ws_id UNIQUE (workspace_id, id),
          FOREIGN KEY (workspace_id, primary_leader_id)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (primary_leader_id),
          FOREIGN KEY (workspace_id, creator_id)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT,
          FOREIGN KEY (workspace_id, archived_by_id)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (archived_by_id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_squads_name ON squads(workspace_id, name) "
        "WHERE deleted_at IS NULL AND status = 'active'"
    )
    op.execute(
        "CREATE INDEX idx_squads_list ON squads(workspace_id, status, created_at DESC) "
        "WHERE deleted_at IS NULL"
    )
    op.execute("CREATE INDEX idx_squads_kind ON squads(workspace_id, kind, status)")

    # -- squad_members (成员关系, squad.md §2.3) -------------------------------
    op.execute(
        """
        CREATE TABLE squad_members (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          squad_id     UUID NOT NULL,
          member_id    UUID NOT NULL,
          role         TEXT NOT NULL DEFAULT 'member'
                       CHECK (role IN ('leader','member','observer')),
          joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          left_at      TIMESTAMPTZ NULL,
          added_by_id  UUID NULL,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, squad_id)
            REFERENCES squads(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, member_id)
            REFERENCES members(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, added_by_id)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (added_by_id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_squad_member_active ON squad_members(squad_id, member_id) "
        "WHERE left_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_squad_members_active ON squad_members(squad_id, role) "
        "WHERE left_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_squad_members_member ON squad_members(member_id) WHERE left_at IS NULL"
    )

    # -- squad_tasks (编排核心, squad.md §2.4) ---------------------------------
    op.execute(
        """
        CREATE TABLE squad_tasks (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          squad_id        UUID NOT NULL,
          issue_id        UUID NOT NULL,
          parent_task_id  UUID NULL,
          root_task_id    UUID NULL,
          depth           SMALLINT NOT NULL DEFAULT 0 CHECK (depth BETWEEN 0 AND 4),
          title_snapshot  TEXT NOT NULL,
          status          TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','decomposing','awaiting_plan_approval',
                                            'dispatching','in_progress','blocked','aggregating',
                                            'done','failed','cancelled')),
          orchestrator_id UUID NULL,
          assignee_id     UUID NULL,
          stage           SMALLINT NULL,
          execution_id    UUID NULL,
          plan_markdown   TEXT NULL,
          result_summary  TEXT NULL,
          dispatched_at   TIMESTAMPTZ NULL,
          started_at      TIMESTAMPTZ NULL,
          finished_at     TIMESTAMPTZ NULL,
          failure_reason  TEXT NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_squad_tasks_ws_id UNIQUE (workspace_id, id),
          FOREIGN KEY (workspace_id, squad_id)
            REFERENCES squads(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, issue_id)
            REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, parent_task_id)
            REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, root_task_id)
            REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, orchestrator_id)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (orchestrator_id),
          FOREIGN KEY (workspace_id, assignee_id)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (assignee_id),
          FOREIGN KEY (workspace_id, execution_id)
            REFERENCES task_executions(workspace_id, id) ON DELETE SET NULL (execution_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_squad_tasks_squad ON squad_tasks(workspace_id, squad_id, status, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_squad_tasks_tree ON squad_tasks(root_task_id, depth, created_at)")
    op.execute("CREATE INDEX idx_squad_tasks_parent ON squad_tasks(parent_task_id, status)")
    op.execute("CREATE INDEX idx_squad_tasks_assignee ON squad_tasks(assignee_id, status)")
    op.execute("CREATE INDEX idx_squad_tasks_issue ON squad_tasks(workspace_id, issue_id)")
    op.execute(
        "CREATE INDEX idx_squad_tasks_active ON squad_tasks(squad_id) "
        "WHERE status NOT IN ('done','failed','cancelled')"
    )

    # -- issue_squad_assignments (唯一 active 身份, squad.md §2.5 / T23) --------
    op.execute(
        """
        CREATE TABLE issue_squad_assignments (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          issue_id         UUID NOT NULL,
          squad_id         UUID NOT NULL,
          root_task_id     UUID NULL,
          leader_member_id UUID NOT NULL,
          status           TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active','cancelled','completed')),
          cancel_reason    TEXT NULL,
          assigned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          cancelled_at     TIMESTAMPTZ NULL,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_issue_squad_assignments_ws_id UNIQUE (workspace_id, id),
          FOREIGN KEY (workspace_id, issue_id)
            REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, squad_id)
            REFERENCES squads(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, root_task_id)
            REFERENCES squad_tasks(workspace_id, id) ON DELETE SET NULL (root_task_id),
          FOREIGN KEY (workspace_id, leader_member_id)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    # THE unique-identity guarantee: at most one active assignment per issue
    # (database-level guard against concurrent double-dispatch; exactly one wins).
    op.execute(
        "CREATE UNIQUE INDEX uq_issue_squad_active ON issue_squad_assignments(issue_id) "
        "WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX idx_issue_squad_assignments_squad ON issue_squad_assignments(squad_id, status)"
    )
    op.execute(
        "CREATE INDEX idx_issue_squad_assignments_issue "
        "ON issue_squad_assignments(issue_id, assigned_at DESC)"
    )

    # -- squad_task_dependencies (依赖 DAG, squad.md §2.6) ----------------------
    op.execute(
        """
        CREATE TABLE squad_task_dependencies (
          id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id       UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          task_id            UUID NOT NULL,
          depends_on_task_id UUID NOT NULL,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (task_id <> depends_on_task_id),
          FOREIGN KEY (workspace_id, task_id)
            REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, depends_on_task_id)
            REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_task_dep ON squad_task_dependencies(task_id, depends_on_task_id)"
    )
    op.execute("CREATE INDEX idx_dep_task ON squad_task_dependencies(task_id)")
    op.execute("CREATE INDEX idx_dep_blocker ON squad_task_dependencies(depends_on_task_id)")

    # -- squad_messages (小队内消息, squad.md §2.7) -----------------------------
    op.execute(
        """
        CREATE TABLE squad_messages (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          squad_id        UUID NOT NULL,
          task_id         UUID NULL,
          sender_id       UUID NULL,
          recipient_id    UUID NULL,
          kind            TEXT NOT NULL DEFAULT 'chat'
                          CHECK (kind IN ('chat','instruction','report','system','context')),
          body_markdown   TEXT NOT NULL,
          body_html       TEXT NULL,
          body_text       TEXT NULL,
          pinned          BOOLEAN NOT NULL DEFAULT false,
          attachment_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
          deleted_at      TIMESTAMPTZ NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (kind = 'system' OR sender_id IS NOT NULL),
          FOREIGN KEY (workspace_id, squad_id)
            REFERENCES squads(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, task_id)
            REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, sender_id)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (sender_id),
          FOREIGN KEY (workspace_id, recipient_id)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (recipient_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_messages_squad ON squad_messages(workspace_id, squad_id, created_at) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_messages_task ON squad_messages(squad_id, task_id, created_at) "
        "WHERE task_id IS NOT NULL"
    )
    op.execute("CREATE INDEX idx_messages_recipient ON squad_messages(recipient_id, created_at)")
    op.execute("CREATE INDEX idx_messages_pinned ON squad_messages(squad_id) WHERE pinned = true")

    # -- squad_activity (协作时间线 / 审计, squad.md §2.8) ----------------------
    op.execute(
        """
        CREATE TABLE squad_activity (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          squad_id     UUID NOT NULL,
          task_id      UUID NULL,
          actor_kind   TEXT NOT NULL CHECK (actor_kind IN ('member','system')),
          actor_id     UUID NULL,
          action       TEXT NOT NULL,
          target_type  TEXT NULL,
          target_id    UUID NULL,
          payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (actor_kind = 'system' OR actor_id IS NOT NULL),
          FOREIGN KEY (workspace_id, squad_id)
            REFERENCES squads(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, task_id)
            REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, actor_id)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (actor_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_activity_squad ON squad_activity(workspace_id, squad_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_activity_task ON squad_activity(squad_id, task_id, created_at) "
        "WHERE task_id IS NOT NULL"
    )
    op.execute("CREATE INDEX idx_activity_actor ON squad_activity(actor_kind, actor_id, created_at)")

    # -- deferred composite FK: approvals.subject_task_id → squad_tasks --------
    # (README §6.10; subject_type='squad_plan'. The partial unique
    # uq_approvals_pending_task already guards one-pending-per-root-task.)
    op.execute(
        "ALTER TABLE approvals ADD CONSTRAINT approvals_subject_task_id_squad_tasks "
        "FOREIGN KEY (workspace_id, subject_task_id) "
        "REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) -----------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- app-role privileges ----------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DML_TABLES} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_subject_task_id_squad_tasks")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
