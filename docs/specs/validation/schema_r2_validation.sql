-- ============================================================================
-- Mesh Spec R2 — PostgreSQL 16 全量 DDL 可执行性 + 行为验证脚本
-- 依据:docs/specs/README.md(Draft v3 / R2)§6 全局权威契约 + 15 份功能 Spec
-- 用法:psql -v ON_ERROR_STOP=1 -f schema_r2_validation.sql
-- 期望失败断言以 EXCEPTION 块包裹(拒绝即 PASS);ASSERT 失败 = Spec/DDL 缺陷,脚本中止。
-- ============================================================================
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- ----------------------------------------------------------------------------
-- 基础层:workspaces / users / agents / members
-- ----------------------------------------------------------------------------
CREATE TABLE workspaces (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name               TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  slug               TEXT NOT NULL,
  logo_url           TEXT NULL,
  timezone           TEXT NOT NULL DEFAULT 'UTC',
  default_language   TEXT NOT NULL DEFAULT 'en',
  settings           JSONB NOT NULL DEFAULT '{}',
  inbox_issue_seq    BIGINT NOT NULL DEFAULT 0 CHECK (inbox_issue_seq >= 0),
  deleted_at         TIMESTAMPTZ NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_workspaces_slug ON workspaces(slug) WHERE deleted_at IS NULL;

CREATE TABLE users (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email               TEXT NOT NULL UNIQUE,
  email_verified_at   TIMESTAMPTZ NULL,
  password_hash       TEXT NULL,
  password_changed_at TIMESTAMPTZ NULL,
  display_name        TEXT NOT NULL,
  full_name           TEXT NULL,
  avatar_url          TEXT NULL,
  bio                 TEXT NULL,
  timezone            TEXT NULL,
  status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','invited','disabled','deleted')),
  mfa_secret          TEXT NULL,
  mfa_enabled_at      TIMESTAMPTZ NULL,
  last_login_at       TIMESTAMPTZ NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agents (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id             UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name                     VARCHAR(120) NOT NULL,
  avatar_url               TEXT NULL,
  role_tag                 VARCHAR(64) NULL,
  owner_user_id            UUID NOT NULL REFERENCES users(id),
  slug                     VARCHAR(64) NULL,
  bio                      TEXT NULL,
  badge_kind               VARCHAR(32) NOT NULL DEFAULT 'ai',
  lifecycle_status         VARCHAR(16) NOT NULL DEFAULT 'active'
                           CHECK (lifecycle_status IN ('active','paused','disabled','archived')),
  visibility               VARCHAR(16) NOT NULL DEFAULT 'workspace' CHECK (visibility IN ('workspace','private')),
  system_instructions      TEXT NULL,
  model_config             JSONB NOT NULL DEFAULT '{}'::jsonb,
  default_runtime_id       UUID NULL,   -- 复合 FK 于 runtimes 建表后 ALTER 添加(列级 SET NULL)
  trigger_on_assign        BOOLEAN NOT NULL DEFAULT true,
  active_config_version_id UUID NULL,   -- FK 于 agent_config_versions 建表后 ALTER 添加
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at               TIMESTAMPTZ NULL
);
CREATE UNIQUE INDEX uq_agents_ws_id ON agents(workspace_id, id);   -- 供复合 FK 引用(README §6.2)

CREATE TABLE members (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  member_type      TEXT NOT NULL CHECK (member_type IN ('human','agent')),
  user_id          UUID NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_id         UUID NULL,
  role             TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','admin','member','guest')),
  status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','removed')),
  display_override TEXT NULL,
  joined_at        TIMESTAMPTZ NULL,
  disabled_at      TIMESTAMPTZ NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    (member_type = 'human' AND user_id IS NOT NULL AND agent_id IS NULL)
    OR (member_type = 'agent' AND agent_id IS NOT NULL AND user_id IS NULL)
  ),
  CHECK (member_type = 'human' OR role <> 'owner'),
  FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_members_ws_id ON members(workspace_id, id);           -- 供全局复合 FK 引用
CREATE UNIQUE INDEX uq_members_ws_user ON members(workspace_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_members_ws_agent ON members(workspace_id, agent_id) WHERE agent_id IS NOT NULL;

-- agent_config_versions:agent 下模块内叶表,隔离经 agent 父链传递;changed_by → members.id(agent.md §2.7)
CREATE TABLE agent_config_versions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id       UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  snapshot       JSONB NOT NULL,
  change_summary TEXT NULL,
  changed_by     UUID NOT NULL REFERENCES members(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_config_versions_agent_time ON agent_config_versions(agent_id, created_at DESC);
-- agents.active_config_version_id → agent_config_versions(回指,建表后 ALTER)
ALTER TABLE agents ADD CONSTRAINT fk_agents_active_config
  FOREIGN KEY (active_config_version_id) REFERENCES agent_config_versions(id);

-- ----------------------------------------------------------------------------
-- 项目管理层:projects / milestones / cycles / issue_statuses / issues
-- ----------------------------------------------------------------------------
CREATE TABLE projects (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name           TEXT NOT NULL,
  key            TEXT NOT NULL,
  description    TEXT NULL,
  icon           TEXT NULL,
  color          TEXT NULL,
  status         TEXT NOT NULL DEFAULT 'planning'
                 CHECK (status IN ('planning','active','paused','completed','cancelled')),
  health         TEXT NULL CHECK (health IN ('on_track','at_risk','off_track')),
  visibility     TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public','private')),
  lead_member_id UUID NULL,
  start_date     DATE NULL,
  target_date    DATE NULL,
  progress_cache REAL NULL,
  issue_seq      BIGINT NOT NULL DEFAULT 0 CHECK (issue_seq >= 0),
  archived_at    TIMESTAMPTZ NULL,
  deleted_at     TIMESTAMPTZ NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (target_date IS NULL OR start_date IS NULL OR target_date >= start_date),
  -- lead_member 复合 FK + PG16 列级 SET NULL(README §6.2 第 6 条)
  FOREIGN KEY (workspace_id, lead_member_id) REFERENCES members(workspace_id, id)
    ON DELETE SET NULL (lead_member_id)
);
CREATE UNIQUE INDEX uq_projects_key ON projects(workspace_id, key);          -- 前缀永久保留(非部分)
CREATE UNIQUE INDEX uq_projects_ws_id ON projects(workspace_id, id);         -- 供复合 FK 引用
CREATE UNIQUE INDEX uq_projects_name ON projects(workspace_id, name) WHERE deleted_at IS NULL;

-- 工作区级 identifier 前缀注册表(workspace.md owns,README §6.3)
CREATE TABLE identifier_prefix_registry (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  key          TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('project','inbox','retired')),
  project_id   UUID NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- kind='project' 且 project_id 为 NULL = 项目已物理清理、前缀永久保留(列级 SET NULL 后态)
  CHECK (kind IN ('project','inbox','retired')),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
    ON DELETE SET NULL (project_id)
);
CREATE UNIQUE INDEX uq_prefix_registry_ws_key ON identifier_prefix_registry(workspace_id, key);
CREATE INDEX idx_prefix_registry_ws ON identifier_prefix_registry(workspace_id, kind);

CREATE TABLE milestones (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id   UUID NOT NULL,
  title        TEXT NOT NULL,
  description  TEXT NULL,
  target_date  DATE NULL,
  state        TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','closed')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_milestones_ws_id ON milestones(workspace_id, id);

CREATE TABLE cycles (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id   UUID NULL,
  name         TEXT NOT NULL,
  starts_at    DATE NOT NULL,
  ends_at      DATE NOT NULL,
  state        TEXT NOT NULL DEFAULT 'planned' CHECK (state IN ('planned','active','completed')),
  auto_roll    BOOLEAN NOT NULL DEFAULT false,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (ends_at >= starts_at),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_cycles_ws_id ON cycles(workspace_id, id);

CREATE TABLE project_updates (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id       UUID NOT NULL,
  author_member_id UUID NOT NULL,
  health           TEXT NULL CHECK (health IN ('on_track','at_risk','off_track')),
  status           TEXT NULL CHECK (status IN ('planning','active','paused','completed','cancelled')),
  message          TEXT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE,
  -- 留痕作者不可悬空:成员软删除 + RESTRICT(README §6.2 第 6 条)
  FOREIGN KEY (workspace_id, author_member_id) REFERENCES members(workspace_id, id) ON DELETE RESTRICT
);

CREATE TABLE project_members (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id   UUID NOT NULL,
  member_id    UUID NOT NULL,
  role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('lead','member','viewer')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, member_id),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE member_project_access (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  member_id    UUID NOT NULL,
  project_id   UUID NOT NULL,
  permission   TEXT NOT NULL DEFAULT 'read' CHECK (permission IN ('read','write')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (member_id, project_id),
  FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE
);

-- 邀请:生命周期与兑换分离(workspace.md §2.3/§2.4;max_uses/expires_at 恒 NOT NULL)
CREATE TABLE workspace_invitations (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  email        TEXT NULL,
  token_hash   TEXT NOT NULL UNIQUE,
  token_prefix TEXT NOT NULL,
  role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member','guest')),
  invited_by   UUID NOT NULL,
  max_uses     INT NOT NULL CHECK (max_uses > 0),
  used_count   INT NOT NULL DEFAULT 0 CHECK (used_count >= 0),
  expires_at   TIMESTAMPTZ NOT NULL,
  status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked','expired','exhausted')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, invited_by) REFERENCES members(workspace_id, id)
);
CREATE UNIQUE INDEX uq_ws_invitations_ws_id ON workspace_invitations(workspace_id, id);  -- 供 redemptions 复合 FK
CREATE UNIQUE INDEX uq_ws_invitations_active_email
  ON workspace_invitations(workspace_id, email) WHERE email IS NOT NULL AND status = 'active';

CREATE TABLE workspace_invitation_redemptions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  invitation_id UUID NOT NULL,
  user_id       UUID NOT NULL REFERENCES users(id),
  member_id     UUID NOT NULL,
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  redeemed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (invitation_id, user_id),
  FOREIGN KEY (workspace_id, invitation_id) REFERENCES workspace_invitations(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id)
);

CREATE TABLE workspace_slug_history (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  old_slug     TEXT NOT NULL UNIQUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE issue_statuses (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id   UUID NULL,
  name         TEXT NOT NULL,
  category     TEXT NOT NULL CHECK (category IN ('backlog','todo','in_progress','in_review','blocked','done','cancelled')),
  color        TEXT NULL,
  position     REAL NOT NULL DEFAULT 0,
  is_default   BOOLEAN NOT NULL DEFAULT false,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_issue_statuses_name
  ON issue_statuses (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), name);
CREATE UNIQUE INDEX uq_issue_statuses_default
  ON issue_statuses (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000')) WHERE is_default;
CREATE UNIQUE INDEX uq_issue_statuses_ws_id ON issue_statuses(workspace_id, id);

-- issues:R2 不可变编号命名空间 + 列级 SET NULL + 复合自引用 parent
CREATE TABLE issues (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id             UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id               UUID NULL,
  identifier_namespace_key TEXT NOT NULL,
  number                   BIGINT NOT NULL,
  identifier               TEXT NOT NULL,
  title                    TEXT NOT NULL,
  description              TEXT NULL,
  status_id                UUID NOT NULL,
  state_category           TEXT NOT NULL CHECK (state_category IN ('backlog','todo','in_progress','in_review','blocked','done','cancelled')),
  priority                 TEXT NOT NULL DEFAULT 'none' CHECK (priority IN ('none','low','medium','urgent','high')),
  assignee_id              UUID NULL,
  reporter_id              UUID NULL,
  estimate                 NUMERIC NULL,
  estimate_unit            TEXT NULL CHECK (estimate_unit IN ('points','hours')),
  due_date                 DATE NULL,
  start_date               DATE NULL,
  milestone_id             UUID NULL,
  cycle_id                 UUID NULL,
  parent_id                UUID NULL,
  position                 REAL NOT NULL DEFAULT 0,
  completed_at             TIMESTAMPTZ NULL,
  version                  INT NOT NULL DEFAULT 1,
  deleted_at               TIMESTAMPTZ NULL,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, identifier_namespace_key, number),   -- 命名空间级(取代已废除的 UNIQUE(project_id, number))
  UNIQUE (workspace_id, identifier),                         -- 工作区级兜底
  UNIQUE (workspace_id, id),                                 -- 供复合 FK 引用
  CHECK (parent_id <> id),
  CHECK (due_date IS NULL OR start_date IS NULL OR due_date >= start_date),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
    ON DELETE SET NULL (project_id),
  FOREIGN KEY (workspace_id, status_id) REFERENCES issue_statuses(workspace_id, id)
    ON DELETE RESTRICT,
  FOREIGN KEY (workspace_id, assignee_id) REFERENCES members(workspace_id, id)
    ON DELETE SET NULL (assignee_id),
  FOREIGN KEY (workspace_id, reporter_id) REFERENCES members(workspace_id, id)
    ON DELETE SET NULL (reporter_id),
  FOREIGN KEY (workspace_id, milestone_id) REFERENCES milestones(workspace_id, id)
    ON DELETE SET NULL (milestone_id),
  FOREIGN KEY (workspace_id, cycle_id) REFERENCES cycles(workspace_id, id)
    ON DELETE SET NULL (cycle_id),
  -- parent 复合自引用 FK(README §6.2 第 7 条:显式同租户,不靠"天然")
  FOREIGN KEY (workspace_id, parent_id) REFERENCES issues(workspace_id, id)
    ON DELETE CASCADE
);

CREATE TABLE issue_dependencies (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  issue_id      UUID NOT NULL,
  depends_on_id UUID NOT NULL,
  type          TEXT NOT NULL DEFAULT 'relates_to' CHECK (type IN ('blocks','blocked_by','relates_to','duplicates')),
  created_by    UUID NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (issue_id, depends_on_id, type),
  CHECK (issue_id <> depends_on_id),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, depends_on_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, created_by) REFERENCES members(workspace_id, id) ON DELETE SET NULL (created_by)
);

CREATE TABLE issue_activity (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  issue_id       UUID NOT NULL,
  actor_member_id UUID NULL,
  field          TEXT NOT NULL,
  old_value      JSONB NULL,
  new_value      JSONB NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, actor_member_id) REFERENCES members(workspace_id, id) ON DELETE SET NULL (actor_member_id)
);

-- ----------------------------------------------------------------------------
-- 分类层:labels / custom fields;视图层:views / positions / cursors
-- ----------------------------------------------------------------------------
CREATE TABLE labels (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id   UUID NULL,
  name         TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 50),
  color        TEXT NOT NULL CHECK (color ~ '^#[0-9a-fA-F]{6}$'),
  description  TEXT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_labels_name
  ON labels(workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), name);
CREATE UNIQUE INDEX uq_labels_ws_id ON labels(workspace_id, id);

CREATE TABLE issue_labels (
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  issue_id     UUID NOT NULL,
  label_id     UUID NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (issue_id, label_id),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, label_id) REFERENCES labels(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE custom_field_defs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id   UUID NULL,
  name         TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
  field_key    TEXT NOT NULL CHECK (field_key ~ '^[a-z][a-z0-9_]{0,49}$'),
  type         TEXT NOT NULL CHECK (type IN ('text','textarea','number','date','datetime','single_select','multi_select','member','boolean','url')),
  is_required  BOOLEAN NOT NULL DEFAULT false,
  required_on  JSONB NOT NULL DEFAULT '[]',
  default_value JSONB NULL,
  config       JSONB NOT NULL DEFAULT '{}',
  position     REAL NOT NULL DEFAULT 0,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_cfdefs_key
  ON custom_field_defs(workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), field_key);
CREATE UNIQUE INDEX uq_cfdefs_ws_id ON custom_field_defs(workspace_id, id);

CREATE TABLE custom_field_options (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  field_def_id UUID NOT NULL,
  name         TEXT NOT NULL,
  color        TEXT NULL,
  position     REAL NOT NULL DEFAULT 0,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (field_def_id, name),
  FOREIGN KEY (workspace_id, field_def_id) REFERENCES custom_field_defs(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE issue_custom_field_values (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  issue_id        UUID NOT NULL,
  field_def_id    UUID NOT NULL,
  value_text      TEXT NULL,
  value_number    NUMERIC NULL,
  value_date      TIMESTAMPTZ NULL,
  value_member_id UUID NULL,
  value_boolean   BOOLEAN NULL,
  value_json      JSONB NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (issue_id, field_def_id),
  CHECK (num_nonnulls(value_text, value_number, value_date, value_member_id, value_boolean, value_json) <= 1),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, field_def_id) REFERENCES custom_field_defs(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, value_member_id) REFERENCES members(workspace_id, id)
    ON DELETE SET NULL (value_member_id)
);
CREATE INDEX idx_icfv_number ON issue_custom_field_values (field_def_id, value_number) WHERE value_number IS NOT NULL;
CREATE INDEX idx_icfv_member ON issue_custom_field_values (field_def_id, value_member_id) WHERE value_member_id IS NOT NULL;
CREATE INDEX idx_icfv_value_json ON issue_custom_field_values USING GIN (field_def_id, value_json);

CREATE TABLE views (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id      UUID NULL,
  owner_member_id UUID NOT NULL,
  name            TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
  layout          TEXT NOT NULL DEFAULT 'board' CHECK (layout IN ('board','list','timeline','table')),
  visibility      TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','shared')),
  filters         JSONB NOT NULL DEFAULT '{}',
  group_by        TEXT NULL,
  sub_group_by    TEXT NULL,
  sort            JSONB NOT NULL DEFAULT '[]',
  display_fields  JSONB NOT NULL DEFAULT '[]',
  board_settings  JSONB NOT NULL DEFAULT '{}',
  position        REAL NOT NULL DEFAULT 0,
  is_default      BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, owner_member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_views_default
  ON views(workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000')) WHERE is_default;
CREATE UNIQUE INDEX uq_views_ws_id ON views(workspace_id, id);

CREATE TABLE board_wip_limits (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  view_id     UUID NOT NULL REFERENCES views(id) ON DELETE CASCADE,
  group_key   TEXT NOT NULL,
  "limit"     INT NOT NULL CHECK ("limit" > 0),
  enforcement TEXT NOT NULL DEFAULT 'warn' CHECK (enforcement IN ('warn','block')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (view_id, group_key)
);

CREATE TABLE view_issue_positions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  view_id      UUID NOT NULL,
  issue_id     UUID NOT NULL,
  group_key    TEXT NOT NULL DEFAULT '',
  position     REAL NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (view_id, issue_id),
  FOREIGN KEY (workspace_id, view_id) REFERENCES views(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE
);

-- R2:每频道游标(取代已删除的 view_subscriptions.last_seen_seq;kanban.md §2.6)
CREATE TABLE realtime_channel_cursors (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  member_id    UUID NOT NULL,
  channel      TEXT NOT NULL,
  last_seq     BIGINT NOT NULL DEFAULT 0,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, member_id, channel),
  FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 协作层:comments / notifications(comment-inbox.md owns)
-- ----------------------------------------------------------------------------
CREATE TABLE comments (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  issue_id       UUID NOT NULL,
  parent_id      UUID NULL,
  thread_root_id UUID NULL,
  author_kind    TEXT NOT NULL CHECK (author_kind IN ('member','system')),
  author_id      UUID NULL,
  body_markdown  TEXT NOT NULL CHECK (char_length(body_markdown) > 0),
  body_html      TEXT NULL,
  body_text      TEXT NULL,
  edited_at      TIMESTAMPTZ NULL,
  resolved_at    TIMESTAMPTZ NULL,
  resolved_by_id UUID NULL,
  deleted_at     TIMESTAMPTZ NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, issue_id, id),   -- 供同 issue 重叠复合 FK 引用(README §6.2 第 7 条)
  CHECK (author_kind = 'member' AND author_id IS NOT NULL OR author_kind = 'system' AND author_id IS NULL),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  -- 父评论/线程根必须同 issue(重叠复合 FK,数据库层强制)
  FOREIGN KEY (workspace_id, issue_id, parent_id) REFERENCES comments(workspace_id, issue_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, issue_id, thread_root_id) REFERENCES comments(workspace_id, issue_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, author_id) REFERENCES members(workspace_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (workspace_id, resolved_by_id) REFERENCES members(workspace_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_comments_issue_created ON comments(workspace_id, issue_id, created_at);

-- runtimes / task_executions 需先于 comment_mentions(triggered_execution_id),此处前移建立
CREATE TABLE runtimes (
  id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id               UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name                       TEXT NOT NULL,
  kind                       TEXT NOT NULL DEFAULT 'self_hosted' CHECK (kind IN ('platform_managed','self_hosted')),
  status                     TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending','online','unavailable','paused','draining','decommissioned')),
  activation_token_hash      TEXT NULL,
  activation_expires_at      TIMESTAMPTZ NULL,
  activated_at               TIMESTAMPTZ NULL,
  runtime_token_hash         TEXT NULL,
  capabilities               JSONB NOT NULL DEFAULT '[]',
  labels                     JSONB NOT NULL DEFAULT '{}',
  hostname                   TEXT NULL,
  os                         TEXT NULL,
  cpu_cores                  INT NULL,
  memory_mb                  INT NULL,
  max_concurrent             INT NOT NULL DEFAULT 1 CHECK (max_concurrent >= 0),
  current_load               INT NOT NULL DEFAULT 0 CHECK (current_load >= 0),
  last_heartbeat_at          TIMESTAMPTZ NULL,
  heartbeat_interval_seconds INT NOT NULL DEFAULT 15,
  lease_grace_seconds        INT NOT NULL DEFAULT 45,
  version                    TEXT NULL,
  created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at                 TIMESTAMPTZ NULL
);
CREATE UNIQUE INDEX uq_runtimes_ws_id ON runtimes(workspace_id, id);
-- agents.default_runtime_id 复合 FK + 列级 SET NULL(R2)
ALTER TABLE agents ADD CONSTRAINT fk_agents_default_runtime
  FOREIGN KEY (workspace_id, default_runtime_id) REFERENCES runtimes(workspace_id, id)
  ON DELETE SET NULL (default_runtime_id);

CREATE TABLE task_executions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  agent_id              UUID NULL,
  issue_id              UUID NULL,
  trigger               TEXT NOT NULL DEFAULT 'assign' CHECK (trigger IN ('assign','mention','autopilot','manual','chat')),
  status                TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','claimed','running','cancelling','awaiting_approval','completed','failed','timeout','cancelled')),
  idempotency_key       TEXT NULL UNIQUE,
  priority              INT NOT NULL DEFAULT 100,
  task_spec             JSONB NOT NULL DEFAULT '{}',
  label_requirements    JSONB NOT NULL DEFAULT '{}',
  required_capabilities JSONB NOT NULL DEFAULT '[]',   -- R2:权威能力需求字段(§6.4)
  trigger_event_id      UUID NULL,
  config_snapshot       JSONB NOT NULL DEFAULT '{}',
  max_attempts          INT NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
  queued_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at           TIMESTAMPTZ NULL,
  timeout_seconds       INT NOT NULL DEFAULT 1800,
  cancel_requested_by   UUID NULL,
  cancel_requested_at   TIMESTAMPTZ NULL,
  result                JSONB NULL,
  failure_reason        TEXT NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, cancel_requested_by) REFERENCES members(workspace_id, id)
);
CREATE UNIQUE INDEX uq_task_executions_ws_id ON task_executions(workspace_id, id);
CREATE INDEX idx_executions_claimable ON task_executions (workspace_id, priority, queued_at) WHERE status = 'queued';

CREATE TABLE execution_attempts (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  execution_id          UUID NOT NULL,
  attempt_number        INT NOT NULL CHECK (attempt_number >= 1),
  runtime_id            UUID NULL,
  claimed_by_runtime_id UUID NULL,
  status                TEXT NOT NULL DEFAULT 'claimed'
                        CHECK (status IN ('claimed','running','cancelling','completed','failed','timeout','cancelled','reclaimed')),
  lease_expires_at      TIMESTAMPTZ NULL,
  lease_seq             INT NOT NULL DEFAULT 0,
  claimed_at            TIMESTAMPTZ NULL,
  started_at            TIMESTAMPTZ NULL,
  finished_at           TIMESTAMPTZ NULL,
  working_branch        TEXT NULL,
  result                JSONB NULL,
  failure_reason        TEXT NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (execution_id, attempt_number),
  FOREIGN KEY (workspace_id, execution_id) REFERENCES task_executions(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, runtime_id) REFERENCES runtimes(workspace_id, id)
);
CREATE UNIQUE INDEX uq_attempts_ws_id ON execution_attempts(workspace_id, id);
CREATE INDEX idx_attempts_lease_expired ON execution_attempts (lease_expires_at)
  WHERE status IN ('claimed','running','cancelling');

CREATE TABLE comment_mentions (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id           UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  comment_id             UUID NOT NULL,
  mentioned_id           UUID NOT NULL,
  triggered_execution_id UUID NULL,
  deleted_at             TIMESTAMPTZ NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (comment_id, mentioned_id),
  FOREIGN KEY (workspace_id, comment_id) REFERENCES comments(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, mentioned_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, triggered_execution_id) REFERENCES task_executions(workspace_id, id)
);

CREATE TABLE comment_reactions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  comment_id   UUID NOT NULL,
  actor_id     UUID NOT NULL,
  emoji        TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (comment_id, actor_id, emoji),
  FOREIGN KEY (workspace_id, comment_id) REFERENCES comments(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, actor_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE issue_subscriptions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  issue_id      UUID NOT NULL,
  subscriber_id UUID NOT NULL,
  reason        TEXT NOT NULL CHECK (reason IN ('creator','assignee','mentioned','participated','manual')),
  muted         BOOLEAN NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (issue_id, subscriber_id),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, subscriber_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);

-- notifications:R2 加 priority(README §6.13 唯一优先级矩阵)
CREATE TABLE notifications (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  recipient_id UUID NOT NULL,
  type         TEXT NOT NULL,
  priority     TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('critical','normal')),
  actor_kind   TEXT NULL CHECK (actor_kind IN ('member','system')),
  actor_id     UUID NULL,
  issue_id     UUID NULL,
  comment_id   UUID NULL,
  execution_id UUID NULL,
  payload      JSONB NOT NULL DEFAULT '{}',
  group_key    TEXT NULL,
  read_at      TIMESTAMPTZ NULL,
  archived_at  TIMESTAMPTZ NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, recipient_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, actor_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, comment_id) REFERENCES comments(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, execution_id) REFERENCES task_executions(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE notification_preferences (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  member_id         UUID NOT NULL,
  event_type        TEXT NOT NULL,
  in_app            BOOLEAN NOT NULL DEFAULT true,
  email             TEXT NOT NULL DEFAULT 'digest' CHECK (email IN ('none','realtime','digest')),
  quiet_hours_start TIME NULL,
  quiet_hours_end   TIME NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, member_id, event_type),
  FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE notification_delivery (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  notification_id UUID NOT NULL,
  channel         TEXT NOT NULL CHECK (channel IN ('in_app','email','websocket')),
  state           TEXT NOT NULL CHECK (state IN ('pending','sent','failed')),
  sent_at         TIMESTAMPTZ NULL,
  error           TEXT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (notification_id, channel),
  FOREIGN KEY (workspace_id, notification_id) REFERENCES notifications(workspace_id, id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 附件:attachment_blobs 真源(R2)+ attachments / links
-- ----------------------------------------------------------------------------
CREATE TABLE attachment_blobs (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  content_hash     TEXT NOT NULL,
  storage_provider TEXT NOT NULL DEFAULT 's3',
  storage_bucket   TEXT NOT NULL,
  storage_key      TEXT NOT NULL,
  file_size        BIGINT NOT NULL CHECK (file_size > 0),
  mime_type        TEXT NULL,
  extension        TEXT NULL,
  is_image         BOOLEAN NOT NULL DEFAULT false,
  image_width      INT NULL,
  image_height     INT NULL,
  thumbnail_keys   JSONB NULL,
  scan_status      TEXT NOT NULL DEFAULT 'pending'
                   CHECK (scan_status IN ('pending','clean','infected','error','skipped')),
  scan_detail      JSONB NULL,
  ref_count        INT NOT NULL DEFAULT 0 CHECK (ref_count >= 0),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, content_hash),   -- 并发去重串行化(T24)
  UNIQUE (workspace_id, id)              -- 供 attachments.blob_id 复合 FK
);
CREATE INDEX idx_blobs_quarantine ON attachment_blobs(created_at) WHERE scan_status = 'pending';

CREATE TABLE attachments (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  uploader_id   UUID NOT NULL,
  blob_id       UUID NOT NULL,           -- R2:blob 真源引用
  file_name     TEXT NOT NULL,
  file_size     BIGINT NOT NULL CHECK (file_size > 0),
  upload_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (upload_status IN ('pending','uploading','completed','failed','expired')),
  expires_at    TIMESTAMPTZ NULL,
  deleted_at    TIMESTAMPTZ NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, uploader_id) REFERENCES members(workspace_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (workspace_id, blob_id) REFERENCES attachment_blobs(workspace_id, id) ON DELETE RESTRICT
);

CREATE TABLE attachment_links (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  attachment_id UUID NOT NULL,
  linked_type   TEXT NOT NULL CHECK (linked_type IN ('issue','comment','chat_message')),
  linked_id     UUID NOT NULL,           -- 多态逻辑外键(README §6.2 第 4 条)
  display       TEXT NOT NULL DEFAULT 'card' CHECK (display IN ('inline','card')),
  position      INT NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (attachment_id, linked_type, linked_id),
  FOREIGN KEY (workspace_id, attachment_id) REFERENCES attachments(workspace_id, id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 聊天:chat_sessions / chat_messages(同会话重叠复合 FK,R2)
-- ----------------------------------------------------------------------------
CREATE TABLE chat_sessions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  owner_id              UUID NOT NULL,
  agent_id              UUID NOT NULL,
  title                 TEXT NOT NULL DEFAULT '新对话',
  title_is_auto         BOOLEAN NOT NULL DEFAULT true,
  context_issue_id      UUID NULL,
  context_project_id    UUID NULL,
  status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','deleted')),
  is_pinned             BOOLEAN NOT NULL DEFAULT false,
  last_message_at       TIMESTAMPTZ NULL,
  last_message_preview  TEXT NULL,
  message_count         INT NOT NULL DEFAULT 0 CHECK (message_count >= 0),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at            TIMESTAMPTZ NULL,
  UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, owner_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id),
  FOREIGN KEY (workspace_id, context_issue_id) REFERENCES issues(workspace_id, id),
  FOREIGN KEY (workspace_id, context_project_id) REFERENCES projects(workspace_id, id)
);

CREATE TABLE chat_messages (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  session_id        UUID NOT NULL,
  role              TEXT NOT NULL CHECK (role IN ('user','agent','system')),
  content           TEXT NOT NULL DEFAULT '',
  generation_id     UUID NULL,
  generation_status TEXT NOT NULL DEFAULT 'done'
                    CHECK (generation_status IN ('streaming','done','failed','interrupted')),
  parent_id         UUID NULL,
  selected_candidate BOOLEAN NOT NULL DEFAULT true,
  quote_message_id  UUID NULL,
  prompt_tokens     INT NULL,
  completion_tokens INT NULL,
  error_message     TEXT NULL,
  started_at        TIMESTAMPTZ NULL,
  finished_at       TIMESTAMPTZ NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, session_id, id),   -- 供同会话重叠复合 FK 引用(README §6.2 第 7 条)
  FOREIGN KEY (workspace_id, session_id) REFERENCES chat_sessions(workspace_id, id) ON DELETE CASCADE,
  -- 父消息/引用消息必须同会话(重叠复合 FK,数据库层强制;列级 SET NULL)
  FOREIGN KEY (workspace_id, session_id, parent_id)
    REFERENCES chat_messages(workspace_id, session_id, id) ON DELETE SET NULL (parent_id),
  FOREIGN KEY (workspace_id, session_id, quote_message_id)
    REFERENCES chat_messages(workspace_id, session_id, id) ON DELETE SET NULL (quote_message_id)
);
CREATE INDEX idx_chat_messages_session_time ON chat_messages(session_id, created_at DESC);

-- ----------------------------------------------------------------------------
-- 技能:四层解耦 + 同 skill 重叠复合 FK(R2)
-- ----------------------------------------------------------------------------
CREATE TABLE skill_sources (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  source_type  TEXT NOT NULL DEFAULT 'user' CHECK (source_type IN ('builtin','user','marketplace','url')),
  name         TEXT NOT NULL,
  uri          TEXT NULL,
  trust_level  TEXT NOT NULL DEFAULT 'untrusted' CHECK (trust_level IN ('trusted','reviewed','untrusted')),
  auth_ref     TEXT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at   TIMESTAMPTZ NULL
);
CREATE UNIQUE INDEX uq_skill_source_ws_id ON skill_sources(workspace_id, id);

CREATE TABLE skills (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  source_id             UUID NOT NULL,
  name                  TEXT NOT NULL,
  slug                  TEXT NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$'),
  summary               TEXT NOT NULL,
  status                TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','deprecated','disabled')),
  current_version_id    UUID NULL,      -- 同 skill 重叠复合 FK 于 skill_versions 后 ALTER
  required_capabilities JSONB NOT NULL DEFAULT '[]',
  tags                  TEXT[] NOT NULL DEFAULT '{}',
  icon                  TEXT NULL,
  created_by            UUID NOT NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at            TIMESTAMPTZ NULL,
  FOREIGN KEY (workspace_id, source_id) REFERENCES skill_sources(workspace_id, id),
  FOREIGN KEY (workspace_id, created_by) REFERENCES members(workspace_id, id)
);
CREATE UNIQUE INDEX uq_skill_ws_id ON skills(workspace_id, id);
CREATE UNIQUE INDEX uq_skill_workspace_slug ON skills(workspace_id, slug) WHERE deleted_at IS NULL;

CREATE TABLE skill_versions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  skill_id              UUID NOT NULL,
  version               TEXT NOT NULL,
  instructions          TEXT NOT NULL,
  status                TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','deprecated')),
  changelog             TEXT NULL,
  io_contract           JSONB NULL,
  required_capabilities JSONB NOT NULL DEFAULT '[]',
  manifest              JSONB NOT NULL DEFAULT '{}',
  content_hash          TEXT NOT NULL,
  created_by            UUID NOT NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (skill_id, version),
  FOREIGN KEY (workspace_id, skill_id) REFERENCES skills(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, created_by) REFERENCES members(workspace_id, id)
);
CREATE UNIQUE INDEX uq_skill_version_ws_id ON skill_versions(workspace_id, id);
CREATE UNIQUE INDEX uq_skill_version_ws_skill_id ON skill_versions(workspace_id, skill_id, id);  -- R2 重叠引用前提

-- skills.current_version_id 必须属于同一 skill(README §6.2 第 7 条)
ALTER TABLE skills ADD CONSTRAINT fk_skills_current_version
  FOREIGN KEY (workspace_id, id, current_version_id)
  REFERENCES skill_versions(workspace_id, skill_id, id)
  ON DELETE SET NULL (current_version_id);

CREATE TABLE skill_installations (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  skill_id             UUID NOT NULL,
  skill_version_id     UUID NOT NULL,
  scope                TEXT NOT NULL DEFAULT 'workspace' CHECK (scope IN ('workspace','agent')),
  agent_id             UUID NULL,
  install_status       TEXT NOT NULL DEFAULT 'installed' CHECK (install_status IN ('installed','updated_available','disabled')),
  auto_update          BOOLEAN NOT NULL DEFAULT false,
  granted_capabilities JSONB NOT NULL DEFAULT '[]',
  installed_by         UUID NOT NULL,
  installed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at           TIMESTAMPTZ NULL,
  CHECK (scope = 'workspace' OR agent_id IS NOT NULL),
  FOREIGN KEY (workspace_id, skill_id) REFERENCES skills(workspace_id, id) ON DELETE CASCADE,
  -- 安装版本必须属于所装 skill(重叠复合 FK,R2)
  FOREIGN KEY (workspace_id, skill_id, skill_version_id)
    REFERENCES skill_versions(workspace_id, skill_id, id),
  FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id),
  FOREIGN KEY (workspace_id, installed_by) REFERENCES members(workspace_id, id)
);
CREATE UNIQUE INDEX uq_skill_installation_ws_id ON skill_installations(workspace_id, id);
CREATE UNIQUE INDEX uq_skill_installation_ws_skill_id ON skill_installations(workspace_id, id, skill_id);
CREATE UNIQUE INDEX uq_install_scope
  ON skill_installations(workspace_id, skill_id, scope, agent_id) WHERE deleted_at IS NULL;

CREATE TABLE agent_skills (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  agent_id              UUID NOT NULL,
  skill_id              UUID NOT NULL,
  skill_installation_id UUID NOT NULL,
  skill_version_id      UUID NOT NULL,
  enabled               BOOLEAN NOT NULL DEFAULT true,
  auto_trigger          BOOLEAN NOT NULL DEFAULT true,
  priority              INT NOT NULL DEFAULT 100 CHECK (priority BETWEEN 0 AND 1000),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (agent_id, skill_installation_id),
  FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, skill_id) REFERENCES skills(workspace_id, id),
  -- 安装与版本必须属于同一 skill(重叠复合 FK 链,R2)
  FOREIGN KEY (workspace_id, skill_installation_id, skill_id)
    REFERENCES skill_installations(workspace_id, id, skill_id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, skill_id, skill_version_id)
    REFERENCES skill_versions(workspace_id, skill_id, id)
);

-- ----------------------------------------------------------------------------
-- runtime 支持表
-- ----------------------------------------------------------------------------
CREATE TABLE task_log_segments (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  attempt_id   UUID NOT NULL,
  start_offset BIGINT NOT NULL,
  end_offset   BIGINT NOT NULL,
  storage_ref  TEXT NOT NULL,
  line_count   INT NOT NULL DEFAULT 0,
  sealed       BOOLEAN NOT NULL DEFAULT false,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (attempt_id, start_offset),
  FOREIGN KEY (workspace_id, attempt_id) REFERENCES execution_attempts(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE repo_checkouts (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  attempt_id     UUID NOT NULL UNIQUE,
  repo_url       TEXT NOT NULL,
  base_ref       TEXT NOT NULL,
  working_branch TEXT NOT NULL,
  commit_sha     TEXT NULL,
  local_path     TEXT NULL,
  status         TEXT NOT NULL DEFAULT 'cloning',
  diff_ref       TEXT NULL,
  recycled_at    TIMESTAMPTZ NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, attempt_id) REFERENCES execution_attempts(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE runtime_credentials (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  kind            TEXT NOT NULL DEFAULT 'env' CHECK (kind IN ('env','file','repo_token','ssh_key')),
  scope           TEXT NOT NULL DEFAULT 'execution',
  encrypted_value TEXT NOT NULL,
  redact_in_logs BOOLEAN NOT NULL DEFAULT true,
  expires_at      TIMESTAMPTZ NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ NULL
);

CREATE TABLE execution_credentials (
  attempt_id    UUID NOT NULL,
  credential_id UUID NOT NULL REFERENCES runtime_credentials(id),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  envelope_ref  TEXT NOT NULL,
  injected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at    TIMESTAMPTZ NULL,
  PRIMARY KEY (attempt_id, credential_id),
  FOREIGN KEY (workspace_id, attempt_id) REFERENCES execution_attempts(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE runtime_heartbeats (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  runtime_id   UUID NOT NULL REFERENCES runtimes(id) ON DELETE CASCADE,
  current_load INT NOT NULL DEFAULT 0,
  metrics      JSONB NOT NULL DEFAULT '{}',
  health       TEXT NOT NULL DEFAULT 'healthy' CHECK (health IN ('healthy','degraded')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 小队:squads / squad_tasks / issue_squad_assignments(R2 唯一 active 身份)
-- ----------------------------------------------------------------------------
CREATE TABLE squads (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name                  TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  description           TEXT NULL,
  avatar_url            TEXT NULL,
  kind                  TEXT NOT NULL DEFAULT 'standing' CHECK (kind IN ('standing','adhoc','task_scoped')),
  status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  leader_mode           TEXT NOT NULL DEFAULT 'single' CHECK (leader_mode IN ('single','multi')),
  primary_leader_id     UUID NULL,
  require_plan_approval BOOLEAN NOT NULL DEFAULT false,
  max_decompose_depth   SMALLINT NOT NULL DEFAULT 2 CHECK (max_decompose_depth BETWEEN 1 AND 4),
  creator_id            UUID NOT NULL,
  archived_at           TIMESTAMPTZ NULL,
  archived_by_id        UUID NULL,
  deleted_at            TIMESTAMPTZ NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, primary_leader_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, creator_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, archived_by_id) REFERENCES members(workspace_id, id)
);
CREATE UNIQUE INDEX uq_squads_ws_id ON squads(workspace_id, id);

CREATE TABLE squad_members (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  squad_id     UUID NOT NULL,
  member_id    UUID NOT NULL,
  role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('leader','member','observer')),
  joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  left_at      TIMESTAMPTZ NULL,
  added_by_id  UUID NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, squad_id) REFERENCES squads(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, added_by_id) REFERENCES members(workspace_id, id)
);
CREATE UNIQUE INDEX uq_squad_member_active ON squad_members(squad_id, member_id) WHERE left_at IS NULL;

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
                  CHECK (status IN ('pending','decomposing','awaiting_plan_approval','dispatching','in_progress','blocked','aggregating','done','failed','cancelled')),
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
  UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, squad_id) REFERENCES squads(workspace_id, id),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id),
  FOREIGN KEY (workspace_id, parent_task_id) REFERENCES squad_tasks(workspace_id, id),
  FOREIGN KEY (workspace_id, root_task_id) REFERENCES squad_tasks(workspace_id, id),
  FOREIGN KEY (workspace_id, orchestrator_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, assignee_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, execution_id) REFERENCES task_executions(workspace_id, id)
);

-- R2:小队分派唯一 active 身份(B2)
CREATE TABLE issue_squad_assignments (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  issue_id         UUID NOT NULL,
  squad_id         UUID NOT NULL,
  root_task_id     UUID NULL,
  leader_member_id UUID NOT NULL,
  status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled','completed')),
  cancel_reason    TEXT NULL,
  assigned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  cancelled_at     TIMESTAMPTZ NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, squad_id) REFERENCES squads(workspace_id, id),
  FOREIGN KEY (workspace_id, root_task_id) REFERENCES squad_tasks(workspace_id, id),
  FOREIGN KEY (workspace_id, leader_member_id) REFERENCES members(workspace_id, id)
);
-- 每 issue 至多一条 active 分派(唯一身份保证,T23)
CREATE UNIQUE INDEX uq_issue_squad_active ON issue_squad_assignments(issue_id) WHERE status = 'active';
CREATE INDEX idx_issue_squad_assignments_squad ON issue_squad_assignments(squad_id, status);

CREATE TABLE squad_task_dependencies (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id       UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  task_id            UUID NOT NULL,
  depends_on_task_id UUID NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (task_id, depends_on_task_id),
  CHECK (task_id <> depends_on_task_id),
  FOREIGN KEY (workspace_id, task_id) REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, depends_on_task_id) REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE squad_messages (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  squad_id      UUID NOT NULL,
  task_id       UUID NULL,
  sender_id     UUID NULL,
  recipient_id  UUID NULL,
  kind          TEXT NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat','instruction','report','system','context')),
  body_markdown TEXT NOT NULL,
  body_html     TEXT NULL,
  body_text     TEXT NULL,
  pinned        BOOLEAN NOT NULL DEFAULT false,
  attachment_ids JSONB NOT NULL DEFAULT '[]',
  deleted_at    TIMESTAMPTZ NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (kind = 'system' OR sender_id IS NOT NULL),
  FOREIGN KEY (workspace_id, squad_id) REFERENCES squads(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, task_id) REFERENCES squad_tasks(workspace_id, id),
  FOREIGN KEY (workspace_id, sender_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, recipient_id) REFERENCES members(workspace_id, id)
);

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
  payload      JSONB NOT NULL DEFAULT '{}',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (actor_kind = 'system' OR actor_id IS NOT NULL),
  FOREIGN KEY (workspace_id, squad_id) REFERENCES squads(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, task_id) REFERENCES squad_tasks(workspace_id, id),
  FOREIGN KEY (workspace_id, actor_id) REFERENCES members(workspace_id, id)
);

-- ----------------------------------------------------------------------------
-- 自动化:autopilots / runs / webhook_events
-- ----------------------------------------------------------------------------
CREATE TABLE autopilots (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id             UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name                     TEXT NOT NULL,
  description              TEXT NULL,
  trigger_type             TEXT NOT NULL CHECK (trigger_type IN ('schedule','issue_status_changed','issue_created','issue_field_changed','comment_created','agent_mentioned','webhook_received')),
  trigger_config           JSONB NOT NULL DEFAULT '{}',
  filter_config            JSONB NOT NULL DEFAULT '{}',
  action_config            JSONB NOT NULL DEFAULT '[]',
  executor_agent_id        UUID NULL,
  status                   TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','archived')),
  guardrails               JSONB NOT NULL DEFAULT '{}',
  max_retries              INT NOT NULL DEFAULT 3 CHECK (max_retries >= 0),
  retry_backoff            TEXT NOT NULL DEFAULT 'exponential' CHECK (retry_backoff IN ('fixed','linear','exponential')),
  retry_base_seconds       INT NOT NULL DEFAULT 30 CHECK (retry_base_seconds > 0),
  retry_max_seconds        INT NOT NULL DEFAULT 1800 CHECK (retry_max_seconds > 0),
  rate_limit_max           INT NOT NULL DEFAULT 10 CHECK (rate_limit_max >= 0),
  rate_limit_window_seconds INT NOT NULL DEFAULT 3600 CHECK (rate_limit_window_seconds > 0),
  concurrency_limit        INT NOT NULL DEFAULT 1 CHECK (concurrency_limit >= 1),
  require_approval         BOOLEAN NOT NULL DEFAULT false,
  next_run_at              TIMESTAMPTZ NULL,
  last_run_at              TIMESTAMPTZ NULL,
  created_by               UUID NOT NULL,
  deleted_at               TIMESTAMPTZ NULL,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, executor_agent_id) REFERENCES agents(workspace_id, id),
  FOREIGN KEY (workspace_id, created_by) REFERENCES members(workspace_id, id)
);
CREATE UNIQUE INDEX uq_autopilot_ws_id ON autopilots(workspace_id, id);
CREATE UNIQUE INDEX uq_autopilot_ws_name ON autopilots(workspace_id, name) WHERE deleted_at IS NULL;

CREATE TABLE webhook_events (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  autopilot_id     UUID NULL,
  idempotency_key  TEXT NOT NULL,
  event_type       TEXT NOT NULL,
  headers          JSONB NULL,
  payload          JSONB NOT NULL,
  signature_status TEXT NOT NULL CHECK (signature_status IN ('valid','invalid','missing','skipped')),
  process_status   TEXT NOT NULL DEFAULT 'received'
                   CHECK (process_status IN ('received','matched','dispatched','deduped','rejected','processed','failed')),
  received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, idempotency_key),
  UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, autopilot_id) REFERENCES autopilots(workspace_id, id)
);

CREATE TABLE autopilot_runs (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  autopilot_id     UUID NOT NULL,
  workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  trigger_type     TEXT NOT NULL,
  trigger_snapshot JSONB NOT NULL DEFAULT '{}',
  webhook_event_id UUID NULL,
  execution_id     UUID NULL,
  parent_run_id    UUID NULL,
  cascade_depth    INT NOT NULL DEFAULT 0 CHECK (cascade_depth >= 0),
  status           TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','running','waiting_approval','retrying','succeeded','failed','cancelled')),
  started_at       TIMESTAMPTZ NULL,
  finished_at      TIMESTAMPTZ NULL,
  duration_ms      INT NULL,
  retry_count      INT NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
  error            JSONB NULL,
  prompt_tokens    INT NULL CHECK (prompt_tokens >= 0),
  completion_tokens INT NULL CHECK (completion_tokens >= 0),
  total_tokens     INT GENERATED ALWAYS AS (COALESCE(prompt_tokens,0) + COALESCE(completion_tokens,0)) STORED,
  triggered_by     UUID NULL,
  is_test          BOOLEAN NOT NULL DEFAULT false,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, autopilot_id) REFERENCES autopilots(workspace_id, id),
  FOREIGN KEY (workspace_id, webhook_event_id) REFERENCES webhook_events(workspace_id, id),
  FOREIGN KEY (workspace_id, execution_id) REFERENCES task_executions(workspace_id, id),
  FOREIGN KEY (workspace_id, parent_run_id) REFERENCES autopilot_runs(workspace_id, id),
  FOREIGN KEY (workspace_id, triggered_by) REFERENCES members(workspace_id, id)
);

-- ----------------------------------------------------------------------------
-- approvals 统一审批(README §6.10 R2:复合 FK + 恰好一个 subject + pending 部分唯一)
-- ----------------------------------------------------------------------------
CREATE TABLE approvals (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  subject_type         TEXT NOT NULL CHECK (subject_type IN ('tool_call','autopilot_action','squad_plan')),
  subject_execution_id UUID NULL,
  subject_run_id       UUID NULL,
  subject_task_id      UUID NULL,
  requested_by_member_id UUID NOT NULL,
  action_summary       JSONB NOT NULL,
  status               TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','approved','rejected','expired','cancelled')),
  requested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at           TIMESTAMPTZ NOT NULL,
  decided_by_member_id UUID NULL,
  decided_at           TIMESTAMPTZ NULL,
  decision_comment     TEXT NULL,
  idempotency_key      TEXT NULL UNIQUE,
  FOREIGN KEY (workspace_id, subject_execution_id) REFERENCES task_executions(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, subject_run_id) REFERENCES autopilot_runs(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, subject_task_id) REFERENCES squad_tasks(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, requested_by_member_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, decided_by_member_id) REFERENCES members(workspace_id, id),
  CHECK (
       (subject_type = 'tool_call'        AND subject_execution_id IS NOT NULL
                                         AND subject_run_id IS NULL AND subject_task_id IS NULL)
    OR (subject_type = 'autopilot_action' AND subject_run_id IS NOT NULL
                                         AND subject_execution_id IS NULL AND subject_task_id IS NULL)
    OR (subject_type = 'squad_plan'       AND subject_task_id IS NOT NULL
                                         AND subject_execution_id IS NULL AND subject_run_id IS NULL)
  )
);
CREATE INDEX idx_approvals_pending ON approvals (workspace_id, requested_at) WHERE status = 'pending';
CREATE UNIQUE INDEX uq_approvals_pending_execution
  ON approvals (workspace_id, subject_execution_id) WHERE status = 'pending' AND subject_type = 'tool_call';
CREATE UNIQUE INDEX uq_approvals_pending_run
  ON approvals (workspace_id, subject_run_id) WHERE status = 'pending' AND subject_type = 'autopilot_action';
CREATE UNIQUE INDEX uq_approvals_pending_task
  ON approvals (workspace_id, subject_task_id) WHERE status = 'pending' AND subject_type = 'squad_plan';

-- ----------------------------------------------------------------------------
-- outbox 与 realtime(README §6.6/§6.7 R2:租户键 + 唯一写入路径 + RLS)
-- ----------------------------------------------------------------------------
CREATE TABLE outbox_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id),
  event_type      TEXT NOT NULL,
  payload         JSONB NOT NULL,
  idempotency_key TEXT NULL UNIQUE,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','published','failed')),
  delivery_attempts INT NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at    TIMESTAMPTZ NULL
);
CREATE INDEX idx_outbox_pending ON outbox_events (created_at) WHERE status = 'pending';

CREATE TABLE realtime_channels (
  channel      TEXT NOT NULL,
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  last_seq     BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (channel),
  UNIQUE (workspace_id, channel)
);

CREATE TABLE realtime_events (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  channel       TEXT NOT NULL,
  seq           BIGINT NOT NULL,
  event         TEXT NOT NULL,
  payload       JSONB NOT NULL,
  outbox_event_id UUID NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at  TIMESTAMPTZ NULL,
  UNIQUE (channel, seq),
  UNIQUE (outbox_event_id),          -- at-least-once 投递 → 恰好一次登记(T26)
  FOREIGN KEY (workspace_id, channel) REFERENCES realtime_channels(workspace_id, channel) ON DELETE CASCADE
);
CREATE INDEX idx_realtime_events_replay ON realtime_events (channel, seq);

ALTER TABLE realtime_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE realtime_events  ENABLE ROW LEVEL SECURITY;
CREATE POLICY mesh_rt_channels_tenant ON realtime_channels
  USING (workspace_id = current_setting('mesh.workspace_id')::uuid);
CREATE POLICY mesh_rt_events_tenant ON realtime_events
  USING (workspace_id = current_setting('mesh.workspace_id')::uuid);

-- ============================================================================
-- 行为验证(真实 DELETE / 跨租户 / 同父域 / 协议约束)
-- ============================================================================

-- 测试夹具:两个工作区
INSERT INTO workspaces (id, name, slug) VALUES
  ('11111111-1111-1111-1111-111111111111', 'WS A', 'ws-a'),
  ('22222222-2222-2222-2222-222222222222', 'WS B', 'ws-b');
INSERT INTO users (id, email, display_name) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', 'u1@example.test', 'U1'),
  ('aaaaaaaa-0000-0000-0000-000000000002', 'u2@example.test', 'U2'),
  ('aaaaaaaa-0000-0000-0000-000000000003', 'u3@example.test', 'U3');
INSERT INTO agents (id, workspace_id, name, owner_user_id) VALUES
  ('bbbbbbbb-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'agent-a1', 'aaaaaaaa-0000-0000-0000-000000000001'),
  ('bbbbbbbb-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'agent-b1', 'aaaaaaaa-0000-0000-0000-000000000001');
INSERT INTO members (id, workspace_id, member_type, user_id, role) VALUES
  ('cccccccc-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'human', 'aaaaaaaa-0000-0000-0000-000000000001', 'owner'),
  ('cccccccc-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'human', 'aaaaaaaa-0000-0000-0000-000000000002', 'member'),
  ('cccccccc-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'human', 'aaaaaaaa-0000-0000-0000-000000000003', 'member'),
  ('cccccccc-0000-0000-0000-000000000009', '22222222-2222-2222-2222-222222222222', 'human', 'aaaaaaaa-0000-0000-0000-000000000001', 'owner');
INSERT INTO members (id, workspace_id, member_type, agent_id, role) VALUES
  ('cccccccc-0000-0000-0000-000000000004', '11111111-1111-1111-1111-111111111111', 'agent', 'bbbbbbbb-0000-0000-0000-000000000001', 'member');

-- 项目与编号:WS-A 有 WEB/APP 两个项目;WEB-1 与 APP-1 并存(迁移冲突场景)
INSERT INTO identifier_prefix_registry (workspace_id, key, kind) VALUES
  ('11111111-1111-1111-1111-111111111111', 'WS', 'inbox');
INSERT INTO projects (id, workspace_id, name, key) VALUES
  ('dddddddd-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Web 项目', 'WEB'),
  ('dddddddd-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'App 项目', 'APP'),
  ('dddddddd-0000-0000-0000-000000000009', '22222222-2222-2222-2222-222222222222', 'B 项目', 'BEE');
INSERT INTO identifier_prefix_registry (workspace_id, key, kind, project_id) VALUES
  ('11111111-1111-1111-1111-111111111111', 'WEB', 'project', 'dddddddd-0000-0000-0000-000000000001'),
  ('11111111-1111-1111-1111-111111111111', 'APP', 'project', 'dddddddd-0000-0000-0000-000000000002');
INSERT INTO issue_statuses (id, workspace_id, project_id, name, category, is_default) VALUES
  ('eeeeeeee-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'dddddddd-0000-0000-0000-000000000001', 'WEB-Todo', 'todo', true),
  ('eeeeeeee-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'dddddddd-0000-0000-0000-000000000001', 'WEB-Done', 'done', false),
  ('eeeeeeee-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'dddddddd-0000-0000-0000-000000000002', 'APP-Todo', 'todo', true),
  ('eeeeeeee-0000-0000-0000-000000000009', '22222222-2222-2222-2222-222222222222', NULL, 'B-Todo', 'todo', true);
INSERT INTO milestones (id, workspace_id, project_id, title) VALUES
  ('ffffffff-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'dddddddd-0000-0000-0000-000000000001', 'WEB 里程碑');
INSERT INTO issues (id, workspace_id, project_id, identifier_namespace_key, number, identifier, title, status_id, state_category, assignee_id, milestone_id)
VALUES
  ('99999999-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'dddddddd-0000-0000-0000-000000000001', 'WEB', 1, 'WEB-1', 'WEB 的一号', 'eeeeeeee-0000-0000-0000-000000000001', 'todo', 'cccccccc-0000-0000-0000-000000000002', 'ffffffff-0000-0000-0000-000000000001'),
  ('99999999-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'dddddddd-0000-0000-0000-000000000002', 'APP', 1, 'APP-1', 'APP 的一号', 'eeeeeeee-0000-0000-0000-000000000003', 'todo', NULL, NULL),
  ('99999999-0000-0000-0000-000000000009', '22222222-2222-2222-2222-222222222222', 'dddddddd-0000-0000-0000-000000000009', 'BEE', 1, 'BEE-1', 'B 的一号', 'eeeeeeee-0000-0000-0000-000000000009', 'todo', NULL, NULL);

-- ===================== T19:不可变编号与跨项目迁移 =====================
DO $$
BEGIN
  UPDATE issues SET project_id = 'dddddddd-0000-0000-0000-000000000002',
                    status_id = 'eeeeeeee-0000-0000-0000-000000000003'
   WHERE id = '99999999-0000-0000-0000-000000000001';
  ASSERT (SELECT identifier = 'WEB-1' AND identifier_namespace_key = 'WEB' AND number = 1
            FROM issues WHERE id = '99999999-0000-0000-0000-000000000001'),
         'T19 FAIL: 迁移后 identifier/namespace/number 应保持不变';
  RAISE NOTICE 'PASS T19-1: WEB-1 迁入 APP 项目不违反 UNIQUE(ws, namespace_key, number)';
  BEGIN
    INSERT INTO identifier_prefix_registry (workspace_id, key, kind)
    VALUES ('11111111-1111-1111-1111-111111111111', 'WEB', 'inbox');
    RAISE EXCEPTION 'T19 FAIL: 前缀注册表未拒绝与在册 key 冲突的新前缀';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T19-2: 前缀注册表排他(新前缀与在册项目 key 冲突被拒)';
  END;
  UPDATE projects SET deleted_at = now() WHERE id = 'dddddddd-0000-0000-0000-000000000001';
  BEGIN
    INSERT INTO projects (workspace_id, name, key)
    VALUES ('11111111-1111-1111-1111-111111111111', 'WEB 再建', 'WEB');
    RAISE EXCEPTION 'T19 FAIL: 软删除项目后前缀被复用';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T19-3: 项目前缀永久保留(软删除后不可复用)';
  END;
  UPDATE projects SET deleted_at = NULL WHERE id = 'dddddddd-0000-0000-0000-000000000001';
END $$;

-- ===================== T18:真实 DELETE 行为(核心) =====================
DO $$
DECLARE
  v_ws UUID; v_assignee UUID; v_ident TEXT;
BEGIN
  -- ① 删除成员 → issues.assignee_id 仅置空引用列,workspace_id 保持非空(列级 SET NULL)
  UPDATE issues SET assignee_id = 'cccccccc-0000-0000-0000-000000000003'
   WHERE id = '99999999-0000-0000-0000-000000000002';
  DELETE FROM members WHERE id = 'cccccccc-0000-0000-0000-000000000003';
  SELECT workspace_id, assignee_id INTO v_ws, v_assignee
    FROM issues WHERE id = '99999999-0000-0000-0000-000000000002';
  ASSERT v_ws = '11111111-1111-1111-1111-111111111111' AND v_assignee IS NULL,
         'T18 FAIL: SET NULL (assignee_id) 应仅置空引用列且 workspace_id 不变';
  RAISE NOTICE 'PASS T18-1: 删除成员 → assignee_id 置空、workspace_id 保持(列级 SET NULL 生效)';

  -- ② 删除项目 → issues.project_id 置空,identifier 不变(列级 SET NULL (project_id))
  --    真实运维:项目物理清理前先把其 issue 的项目私有状态改指到存活状态
  --    (issues.status_id RESTRICT 禁止悬空引用,服务层保证;此处模拟该前置步骤)
  UPDATE issues SET status_id = 'eeeeeeee-0000-0000-0000-000000000001'
   WHERE project_id = 'dddddddd-0000-0000-0000-000000000002';
  SELECT identifier INTO v_ident FROM issues WHERE id = '99999999-0000-0000-0000-000000000002';
  DELETE FROM projects WHERE id = 'dddddddd-0000-0000-0000-000000000002';
  ASSERT (SELECT project_id IS NULL AND identifier = v_ident AND identifier_namespace_key = 'APP'
            FROM issues WHERE id = '99999999-0000-0000-0000-000000000002'),
         'T18 FAIL: 删除项目应仅置空 project_id 且 identifier 不变';
  RAISE NOTICE 'PASS T18-2: 删除项目 → project_id 置空、identifier % 不变', v_ident;
  -- 前缀注册表:项目清理后 APP 前缀仍被永久占用(project_id 列级置空,kind 不变)
  ASSERT (SELECT COUNT(*) = 1 FROM identifier_prefix_registry
           WHERE workspace_id = '11111111-1111-1111-1111-111111111111' AND key = 'APP' AND project_id IS NULL),
         'T18 FAIL: 项目清理后前缀注册行应保留且 project_id 列级置空';
  RAISE NOTICE 'PASS T18-2b: 前缀注册行保留(列级 SET NULL (project_id),前缀永久占用)';

  -- ③ 删除仍被 issue 引用的状态(e1,两个 issue 当前状态)→ RESTRICT 拒绝
  BEGIN
    DELETE FROM issue_statuses WHERE id = 'eeeeeeee-0000-0000-0000-000000000001';
    RAISE EXCEPTION 'T18 FAIL: 被引用的 status 应被 RESTRICT 拒绝删除';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T18-3: 删除被引用 status 被 RESTRICT 拒绝';
  END;

  -- ④ 父 issue 删除 → 子 issue 级联(复合自引用 FK ON DELETE CASCADE)
  INSERT INTO issues (id, workspace_id, identifier_namespace_key, number, identifier, title, status_id, state_category, parent_id)
  VALUES ('99999999-0000-0000-0000-0000000000a1', '11111111-1111-1111-1111-111111111111', 'WS', 1, 'WS-1', '父', 'eeeeeeee-0000-0000-0000-000000000001', 'todo', NULL);
  INSERT INTO issues (id, workspace_id, identifier_namespace_key, number, identifier, title, status_id, state_category, parent_id)
  VALUES ('99999999-0000-0000-0000-0000000000a2', '11111111-1111-1111-1111-111111111111', 'WS', 2, 'WS-2', '子', 'eeeeeeee-0000-0000-0000-000000000001', 'todo', '99999999-0000-0000-0000-0000000000a1');
  DELETE FROM issues WHERE id = '99999999-0000-0000-0000-0000000000a1';
  ASSERT NOT EXISTS (SELECT 1 FROM issues WHERE id = '99999999-0000-0000-0000-0000000000a2'),
         'T18 FAIL: 父 issue 删除应级联子 issue';
  RAISE NOTICE 'PASS T18-4: 删除父 issue → 子 issue 级联删除';

  -- ⑤ 删除成员 → 自定义字段 member 值仅置空引用列(列级 SET NULL (value_member_id))
  INSERT INTO custom_field_defs (id, workspace_id, name, field_key, type)
  VALUES ('12121212-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', '验收人', 'acceptor', 'member');
  INSERT INTO issue_custom_field_values (workspace_id, issue_id, field_def_id, value_member_id)
  VALUES ('11111111-1111-1111-1111-111111111111', '99999999-0000-0000-0000-000000000001', '12121212-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-000000000002');
  DELETE FROM members WHERE id = 'cccccccc-0000-0000-0000-000000000002';
  ASSERT (SELECT value_member_id IS NULL AND workspace_id = '11111111-1111-1111-1111-111111111111'
            FROM issue_custom_field_values WHERE field_def_id = '12121212-0000-0000-0000-000000000001'),
         'T18 FAIL: SET NULL (value_member_id) 应仅置空引用列';
  RAISE NOTICE 'PASS T18-5: 删除成员 → 自定义字段 value_member_id 置空、行保留';

  -- ⑥ 留痕作者 RESTRICT:删除 project_updates 作者成员 → 拒绝
  INSERT INTO project_updates (workspace_id, project_id, author_member_id, message)
  VALUES ('11111111-1111-1111-1111-111111111111', 'dddddddd-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-000000000001', '健康度留痕');
  BEGIN
    DELETE FROM members WHERE id = 'cccccccc-0000-0000-0000-000000000001';
    RAISE EXCEPTION 'T18 FAIL: 留痕作者应被 RESTRICT 保护(成员软删除,不物理删)';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T18-6: 删除留痕作者被 RESTRICT 拒绝';
  END;
END $$;

-- ===================== T1:跨租户复合 FK 拒绝 =====================
DO $$
BEGIN
  BEGIN
    INSERT INTO issues (workspace_id, identifier_namespace_key, number, identifier, title, status_id, state_category)
    VALUES ('22222222-2222-2222-2222-222222222222', 'BEE', 2, 'BEE-2', '跨区 status', 'eeeeeeee-0000-0000-0000-000000000001', 'todo');
    RAISE EXCEPTION 'T1 FAIL: 跨租户 status 引用未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T1-1: 跨工作区 status 引用被复合 FK 拒绝';
  END;
  BEGIN
    INSERT INTO issues (workspace_id, identifier_namespace_key, number, identifier, title, status_id, state_category, assignee_id)
    VALUES ('22222222-2222-2222-2222-222222222222', 'BEE', 3, 'BEE-3', '跨区 assignee', 'eeeeeeee-0000-0000-0000-000000000009', 'todo', 'cccccccc-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'T1 FAIL: 跨租户 assignee 引用未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T1-2: 跨工作区 assignee 引用被复合 FK 拒绝';
  END;
  INSERT INTO workspace_invitations (id, workspace_id, token_hash, token_prefix, invited_by, max_uses, expires_at)
  VALUES ('33333333-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'hash-1', 'pfx', 'cccccccc-0000-0000-0000-000000000001', 10, now() + interval '7 days');
  BEGIN
    INSERT INTO workspace_invitation_redemptions (invitation_id, user_id, member_id, workspace_id)
    VALUES ('33333333-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-000000000009', '22222222-2222-2222-2222-222222222222');
    RAISE EXCEPTION 'T1 FAIL: 跨租户邀请兑换未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T1-3: 跨工作区邀请兑换(redemption→invitation 复合 FK)被拒绝';
  END;
END $$;

-- ===================== 同父域约束(README §6.2 第 7 条) =====================
DO $$
BEGIN
  INSERT INTO chat_sessions (id, workspace_id, owner_id, agent_id) VALUES
    ('44444444-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'cccccccc-0000-0000-0000-000000000001', 'bbbbbbbb-0000-0000-0000-000000000001'),
    ('44444444-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'cccccccc-0000-0000-0000-000000000001', 'bbbbbbbb-0000-0000-0000-000000000001');
  INSERT INTO chat_messages (id, workspace_id, session_id, role, content)
  VALUES ('55555555-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', '44444444-0000-0000-0000-000000000001', 'user', '会话1的消息');
  BEGIN
    INSERT INTO chat_messages (workspace_id, session_id, role, content, parent_id)
    VALUES ('11111111-1111-1111-1111-111111111111', '44444444-0000-0000-0000-000000000002', 'agent', '跨会话回复', '55555555-0000-0000-0000-000000000001');
    RAISE EXCEPTION '同父域 FAIL: 跨会话父消息未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS 同父域-1: 跨会话 parent_id 被重叠复合 FK 拒绝';
  END;
  INSERT INTO chat_messages (workspace_id, session_id, role, content, parent_id)
  VALUES ('11111111-1111-1111-1111-111111111111', '44444444-0000-0000-0000-000000000001', 'agent', '同会话回复', '55555555-0000-0000-0000-000000000001');
  RAISE NOTICE 'PASS 同父域-2: 同会话 parent_id 正常写入';
  INSERT INTO comments (id, workspace_id, issue_id, author_kind, author_id, body_markdown)
  VALUES ('66666666-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', '99999999-0000-0000-0000-000000000001', 'member', 'cccccccc-0000-0000-0000-000000000001', 'issue1 的评论');
  BEGIN
    INSERT INTO comments (workspace_id, issue_id, author_kind, author_id, body_markdown, parent_id)
    VALUES ('11111111-1111-1111-1111-111111111111', '99999999-0000-0000-0000-000000000002', 'member', 'cccccccc-0000-0000-0000-000000000001', '跨 issue 回复', '66666666-0000-0000-0000-000000000001');
    RAISE EXCEPTION '同父域 FAIL: 跨 issue 父评论未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS 同父域-3: 跨 issue parent_id 被重叠复合 FK 拒绝';
  END;
  INSERT INTO skill_sources (id, workspace_id, source_type, name) VALUES
    ('77777777-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'user', 'src');
  INSERT INTO skills (id, workspace_id, source_id, name, slug, summary, created_by) VALUES
    ('88888888-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', '77777777-0000-0000-0000-000000000001', 'skill-1', 'skill-1', 's1', 'cccccccc-0000-0000-0000-000000000001'),
    ('88888888-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', '77777777-0000-0000-0000-000000000001', 'skill-2', 'skill-2', 's2', 'cccccccc-0000-0000-0000-000000000001');
  INSERT INTO skill_versions (id, workspace_id, skill_id, version, instructions, content_hash, created_by) VALUES
    ('aaaaaaaa-1111-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', '88888888-0000-0000-0000-000000000001', '1.0.0', 'inst', 'h1', 'cccccccc-0000-0000-0000-000000000001');
  BEGIN
    UPDATE skills SET current_version_id = 'aaaaaaaa-1111-0000-0000-000000000001'
     WHERE id = '88888888-0000-0000-0000-000000000002';
    RAISE EXCEPTION '同父域 FAIL: current_version 指向别 skill 的版本未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS 同父域-4: skills.current_version_id 跨 skill 被重叠复合 FK 拒绝';
  END;
  UPDATE skills SET current_version_id = 'aaaaaaaa-1111-0000-0000-000000000001'
   WHERE id = '88888888-0000-0000-0000-000000000001';
  RAISE NOTICE 'PASS 同父域-5: 同 skill current_version 正常写入';
  BEGIN
    INSERT INTO skill_installations (workspace_id, skill_id, skill_version_id, scope, installed_by)
    VALUES ('11111111-1111-1111-1111-111111111111', '88888888-0000-0000-0000-000000000002', 'aaaaaaaa-1111-0000-0000-000000000001', 'workspace', 'cccccccc-0000-0000-0000-000000000001');
    RAISE EXCEPTION '同父域 FAIL: 安装别 skill 的版本未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS 同父域-6: skill_installations 跨 skill 版本被重叠复合 FK 拒绝';
  END;
END $$;

-- ===================== T20:claim 容量回滚 + capability 匹配 =====================
DO $$
DECLARE
  v_load INT; v_picked UUID;
BEGIN
  INSERT INTO runtimes (id, workspace_id, name, status, capabilities, labels, max_concurrent, current_load)
  VALUES ('abababab-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'rt-1', 'online',
          '["python","version_control"]', '{"region":"intranet"}', 2, 0);
  INSERT INTO task_executions (id, workspace_id, agent_id, label_requirements, required_capabilities, status)
  VALUES ('cdcdcdcd-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001',
          '{"region":"intranet"}', '["ffmpeg"]', 'queued');   -- 需要 ffmpeg,rt-1 没有

  -- ① 有容量但无匹配任务(能力不满足)→ 事务整体回滚,current_load 不变(runtime.md §2.5 R2 权威 claim)
  BEGIN
    -- 锁定 runtime 行(仅校验,不预扣)
    PERFORM 1 FROM runtimes
     WHERE id = 'abababab-0000-0000-0000-000000000001' AND workspace_id = '11111111-1111-1111-1111-111111111111'
       AND status = 'online' AND deleted_at IS NULL AND current_load < max_concurrent FOR UPDATE;
    SELECT e.id INTO v_picked
      FROM task_executions e JOIN agents a ON a.id = e.agent_id
     WHERE e.status = 'queued' AND e.workspace_id = '11111111-1111-1111-1111-111111111111'
       AND e.label_requirements <@ '{"region":"intranet"}'::jsonb
       AND e.required_capabilities <@ '["python","version_control"]'::jsonb
       AND (a.default_runtime_id IS NULL OR a.default_runtime_id = 'abababab-0000-0000-0000-000000000001')
     ORDER BY e.priority ASC, e.queued_at ASC LIMIT 1 FOR UPDATE OF e SKIP LOCKED;
    IF v_picked IS NULL THEN
      RAISE EXCEPTION 'ROLLBACK_CLAIM';   -- 无匹配任务 → 整体回滚,不预扣容量
    END IF;
  EXCEPTION WHEN OTHERS THEN
    -- 异常使当前(子)事务回滚:容量未变
    NULL;
  END;
  SELECT current_load INTO v_load FROM runtimes WHERE id = 'abababab-0000-0000-0000-000000000001';
  ASSERT v_load = 0, 'T20 FAIL: 无匹配任务时 current_load 必须保持不变(不得泄漏)';
  RAISE NOTICE 'PASS T20-1: 有容量但无匹配任务 → 回滚,current_load=0 不变';

  -- ② 能力匹配:把任务能力需求改为 rt-1 具备 → 可领取;原子扣容量 + claimed + attempt
  UPDATE task_executions SET required_capabilities = '["python"]' WHERE id = 'cdcdcdcd-0000-0000-0000-000000000001';
  SELECT e.id INTO v_picked
    FROM task_executions e JOIN agents a ON a.id = e.agent_id
   WHERE e.status = 'queued' AND e.workspace_id = '11111111-1111-1111-1111-111111111111'
     AND e.label_requirements <@ '{"region":"intranet"}'::jsonb
     AND e.required_capabilities <@ '["python","version_control"]'::jsonb
   ORDER BY e.priority ASC, e.queued_at ASC LIMIT 1 FOR UPDATE OF e SKIP LOCKED;
  ASSERT v_picked IS NOT NULL, 'T20 FAIL: 能力满足的任务应被选中';
  UPDATE runtimes SET current_load = current_load + 1 WHERE id = 'abababab-0000-0000-0000-000000000001';
  UPDATE task_executions SET status = 'claimed' WHERE id = v_picked;
  INSERT INTO execution_attempts (workspace_id, execution_id, attempt_number, runtime_id, claimed_by_runtime_id, status, lease_expires_at, lease_seq, claimed_at)
  VALUES ('11111111-1111-1111-1111-111111111111', v_picked, 1, 'abababab-0000-0000-0000-000000000001', 'abababab-0000-0000-0000-000000000001', 'claimed', now() + interval '120 seconds', 1, now());
  SELECT current_load INTO v_load FROM runtimes WHERE id = 'abababab-0000-0000-0000-000000000001';
  ASSERT v_load = 1, 'T20 FAIL: 领取成功后 current_load 应为 1';
  RAISE NOTICE 'PASS T20-2: 能力匹配领取成功,容量原子 +1,attempt #1 建立';

  -- ③ attempt 终态幂等释放
  UPDATE execution_attempts SET status = 'completed', finished_at = now()
   WHERE execution_id = v_picked AND attempt_number = 1;
  UPDATE runtimes SET current_load = GREATEST(current_load - 1, 0) WHERE id = 'abababab-0000-0000-0000-000000000001';
  UPDATE task_executions SET status = 'completed', finished_at = now() WHERE id = v_picked;
  SELECT current_load INTO v_load FROM runtimes WHERE id = 'abababab-0000-0000-0000-000000000001';
  ASSERT v_load = 0, 'T20 FAIL: 终态后容量应幂等归零';
  RAISE NOTICE 'PASS T20-3: attempt 终态 → current_load 幂等归零';
END $$;

-- ===================== T21:approvals 强约束 + 唯一续跑协议 =====================
DO $$
DECLARE
  v_exec UUID := 'cdcdcdcd-0000-0000-0000-000000000001';
BEGIN
  -- ① 先建合法 pending approval(需存活,供 ②/③ 使用)
  INSERT INTO approvals (workspace_id, subject_type, subject_execution_id, subject_run_id, requested_by_member_id, action_summary, expires_at)
  VALUES ('11111111-1111-1111-1111-111111111111', 'tool_call', v_exec, NULL, 'cccccccc-0000-0000-0000-000000000001',
          '{"action":"exec:shell","resume_context":{"checkpoint_ref":"oss://chk/1"}}', now() + interval '1 hour');
  -- 再造一条两个 subject 都非空 → 必须被拒(注意:EXCEPTION 块内异常会回滚整个块,故合法行建在块外)
  BEGIN
    INSERT INTO approvals (workspace_id, subject_type, subject_execution_id, subject_task_id, requested_by_member_id, action_summary, expires_at)
    VALUES ('11111111-1111-1111-1111-111111111111', 'tool_call', v_exec, '00000000-0000-0000-0000-0000000000ff', 'cccccccc-0000-0000-0000-000000000001', '{}', now() + interval '1 hour');
    RAISE EXCEPTION 'T21 FAIL: 两个 subject 列非空未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation OR foreign_key_violation THEN
    RAISE NOTICE 'PASS T21-1: 恰好一个 subject 列非空(CHECK/复合 FK 拒绝非法 subject)';
  END;

  -- ② 同 subject 仅一个 pending(部分唯一索引)
  BEGIN
    INSERT INTO approvals (workspace_id, subject_type, subject_execution_id, requested_by_member_id, action_summary, expires_at)
    VALUES ('11111111-1111-1111-1111-111111111111', 'tool_call', v_exec, 'cccccccc-0000-0000-0000-000000000001', '{}', now() + interval '1 hour');
    RAISE EXCEPTION 'T21 FAIL: 同 subject 第二个 pending approval 未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T21-2: 同 subject 仅一个 pending(uq_approvals_pending_execution)';
  END;

  -- ③ 唯一续跑协议:当前 attempt cancelled(awaiting_approval) + 容量释放 + 批准后新 attempt
  INSERT INTO execution_attempts (workspace_id, execution_id, attempt_number, runtime_id, claimed_by_runtime_id, status, lease_seq, claimed_at, started_at)
  VALUES ('11111111-1111-1111-1111-111111111111', v_exec, 2, 'abababab-0000-0000-0000-000000000001', 'abababab-0000-0000-0000-000000000001', 'running', 1, now(), now());
  UPDATE runtimes SET current_load = current_load + 1 WHERE id = 'abababab-0000-0000-0000-000000000001';
  UPDATE task_executions SET status = 'running' WHERE id = v_exec;
  -- 工具命中 confirm_required:attempt 置 cancelled(awaiting_approval)、容量释放、执行 awaiting_approval
  UPDATE execution_attempts SET status = 'cancelled', failure_reason = 'awaiting_approval', finished_at = now()
   WHERE execution_id = v_exec AND attempt_number = 2;
  UPDATE runtimes SET current_load = GREATEST(current_load - 1, 0) WHERE id = 'abababab-0000-0000-0000-000000000001';
  UPDATE task_executions SET status = 'awaiting_approval' WHERE id = v_exec;
  ASSERT (SELECT current_load = 0 FROM runtimes WHERE id = 'abababab-0000-0000-0000-000000000001'),
         'T21 FAIL: 审批挂起时容量必须释放';
  -- 批准 → queued → 新 attempt #3 凭 resume_context 续跑
  UPDATE approvals SET status = 'approved', decided_at = now()
   WHERE workspace_id = '11111111-1111-1111-1111-111111111111' AND subject_execution_id = v_exec AND status = 'pending';
  UPDATE task_executions SET status = 'queued' WHERE id = v_exec;
  INSERT INTO execution_attempts (workspace_id, execution_id, attempt_number, runtime_id, claimed_by_runtime_id, status, lease_seq, claimed_at)
  VALUES ('11111111-1111-1111-1111-111111111111', v_exec, 3, 'abababab-0000-0000-0000-000000000001', 'abababab-0000-0000-0000-000000000001', 'claimed', 1, now());
  ASSERT (SELECT COUNT(*) = 3 FROM execution_attempts WHERE execution_id = v_exec),
         'T21 FAIL: 审批挂起 attempt 审计行应保留,批准后建新 attempt';
  ASSERT (SELECT failure_reason = 'awaiting_approval' FROM execution_attempts WHERE execution_id = v_exec AND attempt_number = 2),
         'T21 FAIL: attempt #2 应保留 cancelled(awaiting_approval) 审计';
  RAISE NOTICE 'PASS T21-3: 唯一续跑协议(attempt cancelled + 容量释放 + 批准后 attempt #3 续跑,审计保留)';
END $$;

-- ===================== T23:小队 active assignment 唯一身份 =====================
DO $$
DECLARE
  v_issue UUID := '99999999-0000-0000-0000-000000000001';
  v_leader UUID := 'cccccccc-0000-0000-0000-000000000001';
BEGIN
  INSERT INTO squads (id, workspace_id, name, primary_leader_id, creator_id) VALUES
    ('fefefefe-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'S1 小队', v_leader, v_leader),
    ('fefefefe-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'S2 小队(同 leader)', v_leader, v_leader);
  INSERT INTO squad_tasks (id, workspace_id, squad_id, issue_id, title_snapshot, status)
  VALUES ('13131313-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'fefefefe-0000-0000-0000-000000000001', v_issue, 'S1 根任务', 'in_progress');
  INSERT INTO issue_squad_assignments (workspace_id, issue_id, squad_id, root_task_id, leader_member_id, status)
  VALUES ('11111111-1111-1111-1111-111111111111', v_issue, 'fefefefe-0000-0000-0000-000000000001', '13131313-0000-0000-0000-000000000001', v_leader, 'active');

  -- ① 每 issue 至多一条 active
  BEGIN
    INSERT INTO issue_squad_assignments (workspace_id, issue_id, squad_id, leader_member_id, status)
    VALUES ('11111111-1111-1111-1111-111111111111', v_issue, 'fefefefe-0000-0000-0000-000000000002', v_leader, 'active');
    RAISE EXCEPTION 'T23 FAIL: 同 issue 第二条 active 分派未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T23-1: 每 issue 至多一条 active 分派(uq_issue_squad_active)';
  END;

  -- ② 同 leader 跨 squad 改派:先取消旧分派(不为 no-op),再建新 active
  UPDATE issue_squad_assignments SET status = 'cancelled', cancel_reason = 'reassigned', cancelled_at = now()
   WHERE issue_id = v_issue AND status = 'active';
  UPDATE squad_tasks SET status = 'cancelled', failure_reason = 'reassigned' WHERE id = '13131313-0000-0000-0000-000000000001';
  INSERT INTO squad_tasks (id, workspace_id, squad_id, issue_id, title_snapshot, status)
  VALUES ('13131313-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'fefefefe-0000-0000-0000-000000000002', v_issue, 'S2 根任务', 'pending');
  INSERT INTO issue_squad_assignments (workspace_id, issue_id, squad_id, root_task_id, leader_member_id, status)
  VALUES ('11111111-1111-1111-1111-111111111111', v_issue, 'fefefefe-0000-0000-0000-000000000002', '13131313-0000-0000-0000-000000000002', v_leader, 'active');
  ASSERT (SELECT COUNT(*) = 1 FROM issue_squad_assignments WHERE issue_id = v_issue AND status = 'active'
           AND squad_id = 'fefefefe-0000-0000-0000-000000000002'), 'T23 FAIL: 新分派应为 S2';
  ASSERT (SELECT COUNT(*) = 1 FROM issue_squad_assignments WHERE issue_id = v_issue AND status = 'cancelled'),
         'T23 FAIL: 旧分派历史应保留(cancelled)';
  RAISE NOTICE 'PASS T23-2: 同 leader 跨 squad 改派成功(旧根任务取消、历史保留、新 active 建立)';
END $$;

-- ===================== T24:blob 真源唯一性 =====================
DO $$
BEGIN
  INSERT INTO attachment_blobs (id, workspace_id, content_hash, storage_bucket, storage_key, file_size, scan_status, ref_count)
  VALUES ('14141414-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'sha256:aaaa', 'bkt', 'ws/11/aa/obj1', 100, 'clean', 1);
  BEGIN
    INSERT INTO attachment_blobs (workspace_id, content_hash, storage_bucket, storage_key, file_size)
    VALUES ('11111111-1111-1111-1111-111111111111', 'sha256:aaaa', 'bkt', 'ws/11/aa/obj2', 100);
    RAISE EXCEPTION 'T24 FAIL: 同 workspace 同 hash 第二个 blob 未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T24-1: UNIQUE(workspace_id, content_hash) 串行化并发去重';
  END;
  -- 不同 workspace 同 hash 允许(租户隔离)
  INSERT INTO attachment_blobs (workspace_id, content_hash, storage_bucket, storage_key, file_size)
  VALUES ('22222222-2222-2222-2222-222222222222', 'sha256:aaaa', 'bkt', 'ws/22/aa/obj3', 100);
  RAISE NOTICE 'PASS T24-2: 不同 workspace 同 hash 各自独立 blob(租户隔离)';
  -- attachments.blob_id 复合 FK:跨 workspace 引用 blob 被拒
  BEGIN
    INSERT INTO attachments (workspace_id, uploader_id, blob_id, file_name, file_size, upload_status)
    VALUES ('22222222-2222-2222-2222-222222222222', 'cccccccc-0000-0000-0000-000000000009', '14141414-0000-0000-0000-000000000001', 'x.png', 100, 'completed');
    RAISE EXCEPTION 'T24 FAIL: 跨 workspace blob 引用未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T24-3: attachments.blob_id 复合 FK 拒绝跨租户 blob 引用';
  END;
END $$;

-- ===================== T26:realtime 租户键 + 唯一登记键 + RLS =====================
DO $$
BEGIN
  INSERT INTO outbox_events (id, workspace_id, event_type, payload, idempotency_key)
  VALUES ('15151515-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'realtime.publish', '{"event":"issue.updated"}', 'idem-1');
  INSERT INTO realtime_channels (channel, workspace_id) VALUES ('issue:i-1', '11111111-1111-1111-1111-111111111111');
  INSERT INTO realtime_events (workspace_id, channel, seq, event, payload, outbox_event_id)
  VALUES ('11111111-1111-1111-1111-111111111111', 'issue:i-1', 1, 'issue.updated', '{}', '15151515-0000-0000-0000-000000000001');
  -- ① 同 outbox 事件重复登记 → 拒绝(恰好一次登记)
  BEGIN
    INSERT INTO realtime_events (workspace_id, channel, seq, event, payload, outbox_event_id)
    VALUES ('11111111-1111-1111-1111-111111111111', 'issue:i-1', 2, 'issue.updated', '{}', '15151515-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'T26 FAIL: 重复 outbox_event_id 登记未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T26-1: UNIQUE(outbox_event_id) 保证恰好一次登记';
  END;
  -- ② 事件 workspace 与频道归属不一致 → 复合 FK 拒绝
  BEGIN
    INSERT INTO realtime_events (workspace_id, channel, seq, event, payload, outbox_event_id)
    VALUES ('22222222-2222-2222-2222-222222222222', 'issue:i-1', 99, 'issue.updated', '{}', gen_random_uuid());
    RAISE EXCEPTION 'T26 FAIL: 频道归属不一致未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T26-2: realtime_events 复合 FK(workspace_id, channel)拒绝租户错配';
  END;
END $$;

-- ③ RLS:非 owner 角色仅见当前 mesh.workspace_id 的频道与事件
DROP ROLE IF EXISTS mesh_app;   -- 幂等:角色为集群级,跨库重跑先清理
CREATE ROLE mesh_app NOLOGIN;
GRANT SELECT ON realtime_channels, realtime_events TO mesh_app;
SET mesh.workspace_id = '11111111-1111-1111-1111-111111111111';
SET ROLE mesh_app;
DO $$
DECLARE n_ch INT; n_ev INT;
BEGIN
  SELECT COUNT(*) INTO n_ch FROM realtime_channels;
  SELECT COUNT(*) INTO n_ev FROM realtime_events;
  ASSERT n_ch = 1 AND n_ev = 1, 'T26 FAIL: RLS 应仅放行当前 mesh.workspace_id 的行';
  RAISE NOTICE 'PASS T26-3: RLS 按 mesh.workspace_id 过滤(仅见本租户 % 频道 / % 事件)', n_ch, n_ev;
END $$;
RESET ROLE;
SET mesh.workspace_id = '22222222-2222-2222-2222-222222222222';
SET ROLE mesh_app;
DO $$
DECLARE n_ev INT;
BEGIN
  SELECT COUNT(*) INTO n_ev FROM realtime_events;
  ASSERT n_ev = 0, 'T26 FAIL: 切换到 WS-B 后不应见 WS-A 的事件';
  RAISE NOTICE 'PASS T26-4: 切换租户 GUC 后跨租户事件不可见';
END $$;
RESET ROLE;


-- ============================================================================
-- R2 第二阶段(MES-2 强化轮必修 A–E)新表与枚举扩展验证
-- ============================================================================

-- ---- onboarding.md DDL ----
CREATE TABLE onboarding_states (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  member_id      UUID NOT NULL,
  checklist      TEXT NOT NULL DEFAULT 'activation' CHECK (char_length(checklist) BETWEEN 1 AND 40),
  aha_reached_at TIMESTAMPTZ NULL,
  dismissed_at   TIMESTAMPTZ NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, member_id, checklist),
  FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE onboarding_state_steps (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  state_id      UUID NOT NULL,
  step_key      TEXT NOT NULL CHECK (step_key IN ('create_workspace','invite_member_or_add_agent','create_first_issue','dispatch_or_mention_agent','see_agent_reply_in_inbox')),
  status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed','skipped')),
  completed_via TEXT NULL CHECK (completed_via IN ('auto','manual')),
  completed_at  TIMESTAMPTZ NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, state_id, step_key),
  UNIQUE (workspace_id, id),
  CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
  FOREIGN KEY (workspace_id, state_id) REFERENCES onboarding_states(workspace_id, id) ON DELETE CASCADE
);
-- 主记录:每成员每工作区每清单唯一(幂等创建/获取基础);供步骤子表复合 FK 引用
CREATE UNIQUE INDEX uq_onboarding_states_ws_member_checklist
  ON onboarding_states(workspace_id, member_id, checklist);
-- 管理员重置/统计:按工作区检索未达成 aha 的清单
CREATE INDEX idx_onboarding_states_ws_aha
  ON onboarding_states(workspace_id, created_at) WHERE aha_reached_at IS NULL;

-- 步骤子表:供复合 FK 引用;每清单一行一步骤;
CREATE UNIQUE INDEX uq_onboarding_steps_ws_state_step
  ON onboarding_state_steps(workspace_id, state_id, step_key);
-- 自动检测:定位工作区内某步骤未完成的清单(领域事件消费时的精准 UPDATE 范围)
CREATE INDEX idx_onboarding_steps_pending
  ON onboarding_state_steps(workspace_id, step_key) WHERE status <> 'completed';

-- ---- integrations.md DDL ----
-- ============ integrations ============
CREATE TABLE integrations (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('im_feishu','im_slack','vcs_github','vcs_gitlab','webhook_outbound')),
  name         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  config       JSONB NOT NULL DEFAULT '{}',
  secret_ref   TEXT NULL,
  created_by   UUID NOT NULL,
  deleted_at   TIMESTAMPTZ NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_integrations_ws_id UNIQUE (workspace_id, id),                       -- 复合 FK 引用前提(§6.2)
  CONSTRAINT fk_integrations_created_by FOREIGN KEY (workspace_id, created_by)
    REFERENCES members(workspace_id, id) ON DELETE RESTRICT                          -- 作者不悬空(软删除)
);
CREATE UNIQUE INDEX uq_integrations_ws_name ON integrations(workspace_id, name) WHERE deleted_at IS NULL;
CREATE INDEX idx_integrations_ws_kind ON integrations(workspace_id, kind) WHERE deleted_at IS NULL;

-- ============ integration_bindings ============
CREATE TABLE integration_bindings (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id UUID NOT NULL,
  scope          TEXT NOT NULL DEFAULT 'workspace' CHECK (scope IN ('workspace','project')),
  project_id     UUID NULL,
  external_ref   TEXT NOT NULL,
  match_config   JSONB NOT NULL DEFAULT '{}',
  bound_agent_id UUID NULL,
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_integration_bindings_ws_id UNIQUE (workspace_id, id),                -- 复合 FK 引用前提(§6.2)
  CONSTRAINT uq_binding_external_ref UNIQUE (integration_id, external_ref),          -- 外部侧唯一绑定
  CONSTRAINT ck_binding_scope CHECK (scope = 'workspace' OR project_id IS NOT NULL),
  CONSTRAINT fk_binding_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_binding_project FOREIGN KEY (workspace_id, project_id)
    REFERENCES projects(workspace_id, id) ON DELETE SET NULL (project_id),           -- §6.2 第 6 条列级 SET NULL
  CONSTRAINT fk_binding_agent FOREIGN KEY (workspace_id, bound_agent_id)
    REFERENCES agents(workspace_id, id) ON DELETE SET NULL (bound_agent_id)
);
CREATE INDEX idx_binding_integration ON integration_bindings(integration_id, status);
CREATE INDEX idx_binding_agent ON integration_bindings(workspace_id, bound_agent_id) WHERE bound_agent_id IS NOT NULL;

-- ============ integration_events(同构 autopilot.webhook_events)============
CREATE TABLE integration_events (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id    UUID NOT NULL,
  external_event_id TEXT NOT NULL,
  event_type        TEXT NOT NULL,
  payload           JSONB NOT NULL,
  signature_status  TEXT NOT NULL CHECK (signature_status IN ('valid','invalid','missing')),
  process_status    TEXT NOT NULL DEFAULT 'received'
                    CHECK (process_status IN ('received','matched','dispatched','deduped','rejected','processed','failed')),
  received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_integration_events_ws_id UNIQUE (workspace_id, id),                  -- 供 trigger_event_id 引用(§6.2)
  CONSTRAINT uq_integration_event_dedup UNIQUE (integration_id, external_event_id),  -- 入站去重(§6.9)
  CONSTRAINT fk_event_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_event_integration_status ON integration_events(integration_id, process_status, received_at DESC);
CREATE INDEX idx_event_ws_received ON integration_events(workspace_id, received_at DESC);

-- ============ webhook_subscriptions ============
CREATE TABLE webhook_subscriptions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id UUID NULL,
  url            TEXT NOT NULL,
  secret_ref     TEXT NOT NULL,
  event_types    TEXT[] NOT NULL DEFAULT '{}',
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','disabled')),
  fail_count     INT NOT NULL DEFAULT 0 CHECK (fail_count >= 0),
  created_by     UUID NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_webhook_subscriptions_ws_id UNIQUE (workspace_id, id),               -- 复合 FK 引用前提(§6.2)
  CONSTRAINT fk_subscription_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE SET NULL (integration_id),
  CONSTRAINT fk_subscription_created_by FOREIGN KEY (workspace_id, created_by)
    REFERENCES members(workspace_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_subscription_ws_status ON webhook_subscriptions(workspace_id, status);

-- ============ webhook_subscription_deliveries ============
CREATE TABLE webhook_subscription_deliveries (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  subscription_id UUID NOT NULL,
  event_ref       TEXT NOT NULL,
  state           TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sent','failed')),
  attempts        INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_retry_at   TIMESTAMPTZ NULL,
  response_status INT NULL,
  last_error      TEXT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_delivery_subscription_event UNIQUE (subscription_id, event_ref),     -- 出向投递幂等(§6.5)
  CONSTRAINT fk_delivery_subscription FOREIGN KEY (workspace_id, subscription_id)
    REFERENCES webhook_subscriptions(workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_delivery_retry ON webhook_subscription_deliveries(next_retry_at)
  WHERE state = 'pending';
CREATE INDEX idx_delivery_subscription ON webhook_subscription_deliveries(subscription_id, created_at DESC);

-- ---- import-export.md DDL ----
CREATE TABLE data_jobs (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind                 TEXT NOT NULL CHECK (kind IN ('import','export')),
  entity_type          TEXT NOT NULL CHECK (entity_type IN ('issues','projects')),
  format               TEXT NOT NULL CHECK (format IN ('csv','json')),
  status               TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','validating','running','completed','completed_with_errors','failed')),
  mapping              JSONB NOT NULL DEFAULT '{}',
  params               JSONB NOT NULL DEFAULT '{}',
  source_attachment_id UUID NULL,
  result_attachment_id UUID NULL,
  total_rows           INT NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
  succeeded_rows       INT NOT NULL DEFAULT 0 CHECK (succeeded_rows >= 0),
  failed_rows          INT NOT NULL DEFAULT 0 CHECK (failed_rows >= 0),
  error_report         JSONB NOT NULL DEFAULT '[]',
  requested_by         UUID NOT NULL,
  started_at           TIMESTAMPTZ NULL,
  finished_at          TIMESTAMPTZ NULL,
  failure_reason       TEXT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  CHECK (succeeded_rows + failed_rows <= total_rows),
  CHECK ((kind = 'import' AND source_attachment_id IS NOT NULL)
      OR (kind = 'export' AND source_attachment_id IS NULL)),
  FOREIGN KEY (workspace_id, source_attachment_id)
      REFERENCES attachments(workspace_id, id) ON DELETE SET NULL (source_attachment_id),
  FOREIGN KEY (workspace_id, result_attachment_id)
      REFERENCES attachments(workspace_id, id) ON DELETE SET NULL (result_attachment_id),
  FOREIGN KEY (workspace_id, requested_by)
      REFERENCES members(workspace_id, id) ON DELETE RESTRICT
);
-- 我的作业 / 工作区作业列表
CREATE INDEX idx_data_jobs_ws_created   ON data_jobs (workspace_id, created_at DESC);
CREATE INDEX idx_data_jobs_requester    ON data_jobs (workspace_id, requested_by, created_at DESC);
-- 在途作业(监控/补偿扫描,非 worker 领取路径——领取经 outbox)
CREATE INDEX idx_data_jobs_active       ON data_jobs (created_at)
  WHERE status NOT IN ('completed','completed_with_errors','failed');

-- ---- analytics.md DDL ----
CREATE TABLE analytics_snapshots (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  metric_key   TEXT NOT NULL,                 -- 'cycle_time'/'velocity'/'throughput'/'workload'/'burndown'/'agent_stats'
  dimensions   JSONB NOT NULL DEFAULT '{}',   -- {project_id?, cycle_id?, milestone_id?, agent_id?, granularity?, from_category?, tz?}
  dim_hash     TEXT GENERATED ALWAYS AS (md5(dimensions::text)) STORED,  -- 维度指纹,供唯一键/查找(避免 JSONB 直接入唯一索引)
  window_start TIMESTAMPTZ NOT NULL,          -- UTC
  window_end   TIMESTAMPTZ NOT NULL,          -- UTC
  value        JSONB NOT NULL,                -- 聚合结果(指标值 + 必要 meta,如 sample_size/token_coverage)
  computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- 同一 (工作区, 指标, 维度, 窗) 仅一份快照(覆盖式刷新)
  UNIQUE (workspace_id, metric_key, dim_hash, window_start, window_end)
);

CREATE INDEX idx_snapshots_lookup
  ON analytics_snapshots (workspace_id, metric_key, dim_hash, window_start, window_end);
CREATE INDEX idx_snapshots_stale
  ON analytics_snapshots (computed_at);        -- 供 worker 找过期快照重算

-- ---- README §6.19 favorites ----
CREATE TABLE favorites (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  member_id    UUID NOT NULL,
  target_type  TEXT NOT NULL CHECK (target_type IN ('issue','project','view','chat_session')),
  target_id    UUID NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (member_id, target_type, target_id),
  FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_favorites_member ON favorites (workspace_id, member_id, created_at DESC);

-- ---- R2 第二阶段枚举更新 ----
-- task_executions.trigger 扩 'integration'(README §6.4/§6.9、runtime.md)
ALTER TABLE task_executions DROP CONSTRAINT IF EXISTS task_executions_trigger_check;
ALTER TABLE task_executions ADD CONSTRAINT task_executions_trigger_check
  CHECK (trigger IN ('assign','mention','autopilot','manual','chat','integration'));
-- notification_delivery.channel 扩 'im'(README §6.13、comment-inbox.md)
ALTER TABLE notification_delivery DROP CONSTRAINT IF EXISTS notification_delivery_channel_check;
ALTER TABLE notification_delivery ADD CONSTRAINT notification_delivery_channel_check
  CHECK (channel IN ('in_app','email','websocket','im'));

-- ---- 第二阶段行为验证 ----
DO $$
BEGIN
  -- trigger='integration' 入队合法(外部 IM 触发,README §6.9)
  INSERT INTO task_executions (workspace_id, agent_id, trigger, status)
  VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001', 'integration', 'queued');
  RAISE NOTICE 'PASS P2-1: trigger=integration 合法(外部 IM 触发源)';

  -- 入站事件去重:integration_events.UNIQUE(integration_id, external_event_id)
  INSERT INTO integrations (id, workspace_id, kind, name, status, created_by)
  VALUES ('abababab-1111-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'im_feishu', 'feishu-1', 'active', 'cccccccc-0000-0000-0000-000000000001');
  INSERT INTO integration_events (workspace_id, integration_id, external_event_id, event_type, payload, signature_status, process_status)
  VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-1111-0000-0000-000000000001', 'evt-ext-1', 'im.message', '{}', 'valid', 'processed');
  BEGIN
    INSERT INTO integration_events (workspace_id, integration_id, external_event_id, event_type, payload, signature_status, process_status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-1111-0000-0000-000000000001', 'evt-ext-1', 'im.message', '{}', 'valid', 'processed');
    RAISE EXCEPTION 'P2 FAIL: 重复 external_event_id 未被去重拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS P2-2: integration_events 按 (integration_id, external_event_id) 去重';
  END;

  -- 绑定外部侧唯一:同 integration 同 external_ref 不可重复绑定
  INSERT INTO integration_bindings (workspace_id, integration_id, external_ref, status)
  VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-1111-0000-0000-000000000001', 'chat-oc-1', 'active');
  BEGIN
    INSERT INTO integration_bindings (workspace_id, integration_id, external_ref, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-1111-0000-0000-000000000001', 'chat-oc-1', 'active');
    RAISE EXCEPTION 'P2 FAIL: 同外部身份重复绑定未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS P2-3: 外部侧唯一绑定 UNIQUE(integration_id, external_ref)';
  END;

  -- favorites:同成员同目标至多一条;跨租户 member 复合 FK 拒绝
  INSERT INTO favorites (workspace_id, member_id, target_type, target_id)
  VALUES ('11111111-1111-1111-1111-111111111111', 'cccccccc-0000-0000-0000-000000000001', 'issue', '99999999-0000-0000-0000-000000000001');
  BEGIN
    INSERT INTO favorites (workspace_id, member_id, target_type, target_id)
    VALUES ('11111111-1111-1111-1111-111111111111', 'cccccccc-0000-0000-0000-000000000001', 'issue', '99999999-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'P2 FAIL: 重复收藏未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS P2-4: favorites 幂等唯一(member, target_type, target_id)';
  END;
  BEGIN
    INSERT INTO favorites (workspace_id, member_id, target_type, target_id)
    VALUES ('22222222-2222-2222-2222-222222222222', 'cccccccc-0000-0000-0000-000000000001', 'issue', '99999999-0000-0000-0000-000000000009');
    RAISE EXCEPTION 'P2 FAIL: 跨租户 member 收藏未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS P2-5: favorites 复合 FK 拒绝跨租户 member';
  END;

  -- onboarding:同成员同清单至多一条 + 步骤状态机 CHECK
  INSERT INTO onboarding_states (id, workspace_id, member_id, checklist)
  VALUES ('cdcdcdcd-2222-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'cccccccc-0000-0000-0000-000000000001', 'activation');
  BEGIN
    INSERT INTO onboarding_states (workspace_id, member_id, checklist)
    VALUES ('11111111-1111-1111-1111-111111111111', 'cccccccc-0000-0000-0000-000000000001', 'activation');
    RAISE EXCEPTION 'P2 FAIL: 重复 onboarding 主记录未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS P2-6: onboarding_states UNIQUE(workspace_id, member_id, checklist)';
  END;

  -- data_jobs:导入必带源附件 CHECK
  INSERT INTO data_jobs (workspace_id, kind, entity_type, format, status, requested_by)
  VALUES ('11111111-1111-1111-1111-111111111111', 'export', 'issues', 'csv', 'pending', 'cccccccc-0000-0000-0000-000000000001');
  RAISE NOTICE 'PASS P2-7: data_jobs export 无源附件合法';
  BEGIN
    INSERT INTO data_jobs (workspace_id, kind, entity_type, format, status, requested_by)
    VALUES ('11111111-1111-1111-1111-111111111111', 'import', 'issues', 'csv', 'pending', 'cccccccc-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'P2 FAIL: import 无源附件未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS P2-8: data_jobs import 必带 source_attachment_id CHECK 生效';
  END;
END $$;

\echo '============================================================'
\echo 'ALL R2 SCHEMA + BEHAVIOR VALIDATIONS PASSED (PostgreSQL 16)'
\echo '============================================================'
