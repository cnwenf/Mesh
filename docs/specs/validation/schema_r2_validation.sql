-- ============================================================================
-- Mesh Spec R2+R3+R4+R5 — PostgreSQL 16 全量 DDL 可执行性 + 行为验证脚本
-- 依据:docs/specs/README.md(Draft v3 / R2 + R3 + R4 + R5 修订)§6 全局权威契约 + 23 份功能 Spec
-- MES-76 R2/R3(MES-74 架构/UX 评审三轮收口):auth sessions(workspace/scope/device_authz +
--   cli 必绑 CHECK)+ device_authorizations(状态机 CHECK + user_code_hash 部分唯一 R3-M3 +
--   approve/consume 原子条件更新)+ mesh_search_norm 归一函数(public schema + 显式
--   regdictionary + IMMUTABLE,R2-H3/R3-M1)+ members.search_name 投影与五实体 trigram/prefix
--   索引实跑 + 前缀查询表达式匹配断言 + identifier upper() 规范化等值(R3-M4)+
--   runtime_token_hash UNIQUE 与停用置 NULL(R3-H4)+ T36/T37 正负行为断言。
-- MES-76 R4(MES-74 第四轮收口):sessions 补 previous_token_hash/rotated_at(R4-M4 有界
--   幂等轮换,T36 宽限窗语义断言);T37 改精确 11 条索引集合 + 关键 pg_get_indexdef +
--   真实 1/2 字符前缀用例(强制关 seqscan 仅命名「表达式兼容性」,另保留自然规划 EXPLAIN);
--   T38 词典升级路径**完整** smoke test(R5-H3 扩充):可观察行为差异的版本化函数(词典版本
--   共存,新版新增连字符折叠)→ 回补 + 双写 → 事务外建完全部 11 条 _next 索引(9 表达式 + 2 投影)→
--   单事务原子改名切换 → pg_depend/pg_get_expr 逐条断言 9 条表达式索引绑定新 OID、旧函数
--   零依赖 → 实际删除旧函数/列/索引 → 前缀与 trigram 查询命中新索引
--   (R4-H4:REINDEX CONCURRENTLY 不可在事务内,以分阶段在线迁移替代;不只空库建表)。
-- R3(MES-7,HIGH-1～HIGH-9 + 3 建议):agent_config_versions 同租户/重叠 FK(T27);
--   能力字段严格类型与归一(T28);集成外部身份全局唯一 + scope 异或 + vcs_links(T29);
--   IM 投递台账多目的地(T30);data job RESTRICT/checkpoint/行台账恢复协议(T31);
--   users.settings 偏好真源 + locale 单一权威(T32);analytics 可见性缓存键(T33);
--   onboarding evidence(T34);chat_sessions.is_pinned 快照删除(建议-2)。
-- R4(MES-8,第四轮架构/UX 复审 HIGH×6 收口):capability_grants 的 permission 必须存在/
--   字符串/枚举合法 + 归一算法唯一实现 normalize_capability_declarations() 实测(T28 扩展);
--   data job 单调 lease_seq fencing + row_key 原子占用/预分配 target_id + 实体创建幂等,
--   过期旧 worker 重新提交整批被拒(T31 扩展);locale 单一真源(default_language 列不存在,
--   响应只返回 settings.default_locale,T32 扩展);onboarding 入册播种/成熟工作区 reconcile/
--   未读不得完成/错误 trigger member 不得完成四场景(T34 扩展);external_identities 身份键
--   纳入 provider tenant + 映射全局 users.id、回调按集成解析工作区再 JOIN 成员(T29 扩展);
--   analytics execution 指标统一可见性 scope(关联 issue 继承项目可见性;private agent 先过
--   agent 可见性),workload/agent stats/workspace dashboard 共用并入缓存键(T33 扩展)。
-- R5(MES-9,第五轮架构/UX 复审 HIGH×3 收口):external_identities 真正全局化——移除
--   workspace_id 所有权/RLS 键,建链来源仅 created_in_workspace_id 可空审计列(ON DELETE
--   SET NULL);删除建链工作区后映射仍存在且其他工作区回调仍可解析 + 全局表结构/RLS 负向 +
--   解链仅所属 users.id 本人(external_identity_unlink_allowed() 可执行参照,admin 无旁路,
--   T29 扩展);analytics 统一可见性 CTE visible_executions 直接写入 workload-B / agent 主统计 /
--   retry / token 四段权威聚合 SQL,T33 以同一聚合 SQL 对普通成员/项目成员/private agent
--   owner/admin 断言最终统计值(T33 扩展,不再只测 helper)。
-- 用法:psql -v ON_ERROR_STOP=1 -f schema_r2_validation.sql(空库执行)
-- 期望失败断言以 EXCEPTION 块包裹(拒绝即 PASS);ASSERT 失败 = Spec/DDL 缺陷,脚本中止。
-- ============================================================================
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS btree_gin;
-- MES-76 R2/R3:搜索检索扩展(trigram 模糊 + 去重音归一),search-command-palette.md §2.2
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ----------------------------------------------------------------------------
-- MES-76 R2-H3/R3-M1:检索归一唯一函数(索引 / 查询 / 回填同一入口)
-- NFKD + 去重音 + 小写;IMMUTABLE 方可入表达式索引。**固定 schema(public)与显式
-- regdictionary(public.unaccent)**:unaccent(text) 单参形式为 STABLE(读词典),
-- 此处以显式词典双参形式包装并声明 IMMUTABLE——词典/扩展升级会使既有表达式索引陈旧,
-- 迁移台账须记录 unaccent extversion 与本函数测试向量结果,词典变更后 REINDEX 全部
-- mesh_search_norm 表达式索引 + 回补 members.search_name(search-command-palette.md §2.2)。
-- ----------------------------------------------------------------------------
-- 语言刻意选 plpgsql(永不内联):表达式索引的 indexprs 存「CREATE INDEX 时被规划器简化后的
-- 表达式」——LANGUAGE sql 函数的简化形态随规划器内联行为而定(版本间可变),一旦查询表达式
-- 的简化结果与 indexprs 分叉,索引匹配静默失效;plpgsql 两侧恒保持原函数调用,匹配跨版本
-- 稳定(单次调用开销相对 trigram 运算可忽略)。
CREATE OR REPLACE FUNCTION public.mesh_search_norm(t TEXT) RETURNS TEXT
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE RETURNS NULL ON NULL INPUT AS
$$ BEGIN RETURN lower(public.unaccent('public.unaccent'::regdictionary, normalize(t, NFKD))); END $$;

-- ----------------------------------------------------------------------------
-- R3 辅助函数(HIGH-2:调度能力与授权能力严格类型校验,供 CHECK 约束引用)
-- ----------------------------------------------------------------------------
-- 调度字段 required_capabilities 必须为「纯字符串数组」(capability key 集合,README §6.4 R3)
CREATE OR REPLACE FUNCTION jsonb_is_string_array(v JSONB) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_typeof(v) = 'array'
     AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v) e WHERE jsonb_typeof(e) <> 'string')
$$;
-- 授权快照 capability_grants 必须为「严格 [{capability, permission}] 对象数组」(README §6.11 R3/R4):
-- R4(HIGH-1):permission **必须存在**、必须为字符串、取值必须为合法枚举——归一后快照不允许缺失 permission
CREATE OR REPLACE FUNCTION jsonb_is_capability_grants(v JSONB) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_typeof(v) = 'array'
     AND NOT EXISTS (
       SELECT 1 FROM jsonb_array_elements(v) e
        WHERE jsonb_typeof(e) <> 'object'
           OR jsonb_typeof(e->'capability') <> 'string'
           OR (e->'permission') IS NULL                          -- R4:permission 必须存在
           OR jsonb_typeof(e->'permission') <> 'string'          -- R4:permission 必须为字符串
           OR NOT (e->>'permission' IN ('read_only','write','confirm_required'))
     )
$$;

-- ----------------------------------------------------------------------------
-- R4 辅助函数(HIGH-1:入队能力归一算法的**唯一可执行实现**,agent.md §3.3 / README §6.4·§6.11)
-- ----------------------------------------------------------------------------
-- 输入:声明层混合数组(字符串 key 或 {capability, permission?} 对象,skill.md 声明形态)。
-- 输出:{"required": [capability key 字符串数组:去重 + 字典序排序],
--        "grants":   [{capability, permission} 对象数组:按 capability 字典序排序,
--                     同一 capability 取声明中最严格 permission(confirm_required > write > read_only)]}
-- 规则:字符串条目 → grants 补默认 permission=confirm_required(未标注默认高风险闸门);
--       对象条目缺 permission → 同样补 confirm_required;permission 非字符串/非法枚举 → 抛
--       capability_invalid(422,声明层校验应已拦截);其他形态条目一律拒绝。
-- T28 以混合声明调用本函数并断言全部归一语义;schema CHECK(jsonb_is_capability_grants)
-- 兜底归一产物的严格类型;后端编排入口实现与本函数逐条等价(同一算法,单一权威)。
CREATE OR REPLACE FUNCTION normalize_capability_declarations(declared JSONB) RETURNS JSONB
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  item       JSONB;
  k          TEXT;
  p          TEXT;
  r          INT;
  pos        INT;
  caps       TEXT[] := '{}';   -- grants 的 capability 序(去重)
  ranks      INT[]  := '{}';   -- 各 capability 的最严格 permission 秩(3=confirm_required > 2=write > 1=read_only)
  reqs       TEXT[] := '{}';   -- required keys(含重复,输出去重)
  out_req    JSONB;
  out_grants JSONB;
BEGIN
  IF jsonb_typeof(declared) <> 'array' THEN
    RAISE EXCEPTION 'capability_invalid: declarations must be a JSON array';
  END IF;
  FOR item IN SELECT jsonb_array_elements(declared)
  LOOP
    IF jsonb_typeof(item) = 'string' THEN
      k := item #>> '{}';
      p := 'confirm_required';                                  -- 字符串条目默认高风险闸门
    ELSIF jsonb_typeof(item) = 'object' AND jsonb_typeof(item->'capability') = 'string' THEN
      k := item->>'capability';
      IF (item->'permission') IS NULL THEN
        p := 'confirm_required';                                -- 对象条目未标注 permission → 默认最严格
      ELSIF jsonb_typeof(item->'permission') = 'string'
            AND (item->>'permission') IN ('read_only','write','confirm_required') THEN
        p := item->>'permission';
      ELSE
        RAISE EXCEPTION 'capability_invalid: permission must be read_only|write|confirm_required (%)', k;
      END IF;
    ELSE
      RAISE EXCEPTION 'capability_invalid: entry must be a string key or a {capability, permission?} object';
    END IF;
    r := CASE p WHEN 'confirm_required' THEN 3 WHEN 'write' THEN 2 ELSE 1 END;
    reqs := array_append(reqs, k);
    pos := array_position(caps, k);
    IF pos IS NULL THEN
      caps  := array_append(caps, k);
      ranks := array_append(ranks, r);
    ELSIF ranks[pos] < r THEN
      ranks[pos] := r;                                          -- 同一 capability 取最严格 permission
    END IF;
  END LOOP;
  SELECT COALESCE(jsonb_agg(x ORDER BY x), '[]'::jsonb) INTO out_req
    FROM (SELECT DISTINCT unnest(reqs) AS x) t;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('capability', cap, 'permission', perm) ORDER BY cap),
                  '[]'::jsonb) INTO out_grants
    FROM (SELECT caps[i] AS cap,
                 CASE ranks[i] WHEN 3 THEN 'confirm_required' WHEN 2 THEN 'write' ELSE 'read_only' END AS perm
            FROM generate_subscripts(caps, 1) AS i) t;
  RETURN jsonb_build_object('required', out_req, 'grants', out_grants);
END
$$;

-- ----------------------------------------------------------------------------
-- 基础层:workspaces / users / agents / members
-- ----------------------------------------------------------------------------
CREATE TABLE workspaces (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name               TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  slug               TEXT NOT NULL,
  logo_url           TEXT NULL,
  timezone           TEXT NOT NULL DEFAULT 'UTC',
  -- R3(HIGH-7):default_language 列已弃用删除——locale 唯一真源为 settings.default_locale(默认 'en',
  -- 与 i18n.md §2.1/§2.3 一致;存量值经迁移一次性写入 settings 后删列,不长期双写)
  settings           JSONB NOT NULL DEFAULT '{"default_locale": "en"}',
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
  -- MES-76 R3-M2 收口:users 无 full_name / bio 列(auth.md §2.2 / member.md §2.4 权威模型;
  -- 显示名链为 members.display_override → users.display_name → users.email,README §6.1)
  avatar_url          TEXT NULL,
  timezone            TEXT NULL,                            -- 展示层时区(IANA;R3 于 auth.md §2.2 登记,存储仍 UTC)
  settings            JSONB NOT NULL DEFAULT '{}',          -- R3(HIGH-7):账号级展示偏好真源 {locale, theme}(PATCH /api/v1/users/me 写入)
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

-- agent_config_versions:R3 修订(HIGH-1)——补 workspace_id + 同租户复合 FK + 重叠唯一键,
-- 审计成员 changed_by 同租户复合 FK;active 指针以重叠复合 FK 强制同 agent 同租户(README §6.2 第 2/7 条)
CREATE TABLE agent_config_versions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  agent_id       UUID NOT NULL,
  snapshot       JSONB NOT NULL,
  change_summary TEXT NULL,
  changed_by     UUID NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_config_versions_ws_id UNIQUE (workspace_id, id),
  -- 重叠唯一键:供 agents.active_config_version_id 的同 agent 重叠复合 FK 引用(README §6.2 第 7 条)
  CONSTRAINT uq_config_versions_ws_agent_id UNIQUE (workspace_id, agent_id, id),
  CONSTRAINT fk_config_versions_agent FOREIGN KEY (workspace_id, agent_id)
    REFERENCES agents(workspace_id, id) ON DELETE CASCADE,
  -- 审计成员必须与版本同属一个工作区(跨租户 changed_by 在 INSERT 即被拒绝)
  CONSTRAINT fk_config_versions_changed_by FOREIGN KEY (workspace_id, changed_by)
    REFERENCES members(workspace_id, id)
);
CREATE INDEX idx_config_versions_agent_time ON agent_config_versions(agent_id, created_at DESC);
-- agents.active_config_version_id → agent_config_versions(R3:重叠复合 FK,active 指针只能指向本 agent 的版本)
ALTER TABLE agents ADD CONSTRAINT fk_agents_active_config
  FOREIGN KEY (workspace_id, id, active_config_version_id)
  REFERENCES agent_config_versions(workspace_id, agent_id, id)
  ON DELETE SET NULL (active_config_version_id);

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
  allowed_transitions JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(allowed_transitions) = 'array'),
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
  runtime_token_hash         TEXT NULL UNIQUE,              -- MES-76 R2-H2/R3-H4:mesh_rt_ 机器令牌唯一存储真源(不入 api_tokens;停用置 NULL)
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
-- R3(HIGH-2):调度字段严格字符串数组(对象进调度字段 → claim 的 <@ 永不命中、任务永久无法领取);
-- 授权快照 capability_grants 严格对象数组(README §6.4/§6.11,集成测试 T28)
ALTER TABLE task_executions ADD CONSTRAINT ck_executions_required_capabilities
  CHECK (jsonb_is_string_array(required_capabilities));
ALTER TABLE task_executions ADD CONSTRAINT ck_executions_capability_grants
  CHECK ((config_snapshot->'capability_grants') IS NULL
      OR jsonb_is_capability_grants(config_snapshot->'capability_grants'));

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

-- notification_delivery:R3 修订(HIGH-4)——结构化多目的地(IM 平台/绑定/外部目标独立成列),
-- 唯一键到目的地粒度 (notification_id, channel, destination_key);error 只记失败原因
CREATE TABLE notification_delivery (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  notification_id UUID NOT NULL,
  channel         TEXT NOT NULL CHECK (channel IN ('in_app','email','websocket')),
  destination_key TEXT NOT NULL DEFAULT '',                 -- R3:稳定目的地键(in_app/websocket 恒 '';im = provider:binding_id:external_target)
  provider        TEXT NULL CHECK (provider IN ('feishu','slack','email_smtp')),  -- R3:结构化路由(不再塞 error)
  external_target TEXT NULL,                                -- R3:外部目标身份(飞书 chat_id/open_id、Slack channel_id/user_id、邮件地址)
  integration_id  UUID NULL,                                -- R3:IM 集成实例(composite FK 于 integrations 建表后 ALTER 添加)
  binding_id      UUID NULL,                                -- R3:IM 绑定(composite FK 于 integration_bindings 建表后 ALTER 添加)
  state           TEXT NOT NULL CHECK (state IN ('pending','sent','failed')),
  sent_at         TIMESTAMPTZ NULL,
  error           TEXT NULL,                                -- R3:仅失败原因,不混入路由数据
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (notification_id, channel, destination_key),       -- R3:多目的地幂等(取代 (notification_id, channel))
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
  -- R3(建议-2):is_pinned 快照列已删除——置顶唯一真源为 README §6.19 favorites(target_type='chat_session')
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
-- 幂等:角色为集群级,不 DROP(跨库重跑时旧库对象可能仍依赖该角色);存在即复用,授权幂等
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mesh_app') THEN
    CREATE ROLE mesh_app NOLOGIN;
  END IF;
END $$;
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
  evidence      JSONB NOT NULL DEFAULT '{}',   -- R3(HIGH-9):完成证据 {execution_id?, comment_id?, notification_id?, trigger_member_id?, ...}
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

-- ============ integration_bindings(R3 修订 HIGH-3:规范化外部身份 + 全局唯一 + scope 精确异或)============
CREATE TABLE integration_bindings (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id      UUID NOT NULL,
  provider            TEXT NOT NULL CHECK (provider IN ('feishu','slack','github','gitlab','webhook')),
  provider_tenant_key TEXT NOT NULL DEFAULT '',                                       -- R3:规范化外部平台租户(team_id/tenant_key/installation_id/实例主机)
  scope               TEXT NOT NULL DEFAULT 'workspace' CHECK (scope IN ('workspace','project')),
  project_id          UUID NULL,
  external_ref        TEXT NOT NULL,                                                 -- R3:规范化外部对象 ID(chat_id/channel_id/owner/repo)
  match_config        JSONB NOT NULL DEFAULT '{}',
  bound_agent_id      UUID NULL,
  status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_integration_bindings_ws_id UNIQUE (workspace_id, id),                -- 复合 FK 引用前提(§6.2)
  -- R3:外部身份跨 workspace 唯一绑定(全局键,取代仅 integration 实例内的 UNIQUE(integration_id, external_ref))
  CONSTRAINT uq_binding_external_identity UNIQUE (provider, provider_tenant_key, external_ref),
  -- R3:scope/project 精确异或(workspace 不得带 project;project 必带 project)
  CONSTRAINT ck_binding_scope CHECK ((scope = 'workspace' AND project_id IS NULL)
                                  OR (scope = 'project' AND project_id IS NOT NULL)),
  CONSTRAINT fk_binding_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE CASCADE,
  -- R3:项目级绑定随项目物理删除级联(不再 SET NULL——置空会违反上面的精确异或 CHECK)
  CONSTRAINT fk_binding_project FOREIGN KEY (workspace_id, project_id)
    REFERENCES projects(workspace_id, id) ON DELETE CASCADE,
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

-- ============ external_identities(R3 协同 MES-4 HIGH-1;R4 HIGH-5:映射全局 users.id + 身份键含平台租户;R5 HIGH-2:真正的全局身份表)============
-- R5 修订:既然映射目标为全局 users.id,本表即与 users 同级的**全局身份表**——移除租户所有权 / RLS 键
-- (原 workspace_id NOT NULL ... ON DELETE CASCADE:删除建链工作区 A 会级联删除全局映射,使工作区 B 的审批失效;
-- 且 §6.2 workspace RLS 口径下 B 无法读取归属 A 的映射)。建链来源仅以可空审计列 created_in_workspace_id
-- (ON DELETE SET NULL)记录,**不级联控制映射生命周期**。全局解链仅映射所属 users.id 本人(无 admin 旁路;
-- 可执行参照 external_identity_unlink_allowed(),T29);工作区管理员只能撤销本工作区使用权/成员资格。
-- 卡片回调鉴权:集成实例解析 workspace → 本表查 (provider, provider_tenant_key, external_user_key)
-- → users.id → JOIN 该 workspace 的 members(workspace_id, user_id) → README §6.10 权限再校验。
CREATE TABLE external_identities (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider              TEXT NOT NULL CHECK (provider IN ('feishu','slack','github','gitlab')),
  provider_tenant_key   TEXT NOT NULL DEFAULT '',                -- R4:平台租户(飞书 tenant_key / Slack team_id / GitHub installation 或 org / GitLab 实例主机),纳入身份键
  external_user_key     TEXT NOT NULL,
  user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- R4:映射到全局登录身份(用户注销 → 映射级联删除,生命周期唯一级联来源)
  created_in_workspace_id UUID NULL REFERENCES workspaces(id)
                          ON DELETE SET NULL (created_in_workspace_id),         -- R5:建链发起工作区(仅审计;删除该工作区仅置空本列,映射保留)
  verified_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- R4:身份键 = 平台 + 平台租户 + 外部用户(全局):一个外部平台账号至多映射一个 Mesh 用户;
  -- 不同外部租户同 user key 可并存;同一账号跨多 Mesh 工作区参与 = 单映射行 + 按工作区 JOIN member
  CONSTRAINT uq_external_identity UNIQUE (provider, provider_tenant_key, external_user_key)
);
CREATE INDEX idx_external_identities_user ON external_identities(user_id);
CREATE INDEX idx_external_identities_created_in_ws ON external_identities(created_in_workspace_id)
  WHERE created_in_workspace_id IS NOT NULL;

-- R5(HIGH-2):全局解链授权的可执行参照实现——仅映射所属 users.id 本人可解链;角色列(admin/owner)
-- 不参与判定,工作区管理员无旁路(后端解链端点的服务层实现须与本函数逐条等价,T29 实测)
CREATE OR REPLACE FUNCTION external_identity_unlink_allowed(p_identity UUID, p_member UUID) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1
      FROM external_identities ei
      JOIN members m ON m.id = p_member
     WHERE ei.id = p_identity
       AND m.user_id = ei.user_id          -- 请求者解析出的全局身份 == 映射所属用户(唯一授权条件)
       AND m.status = 'active'
  )
$$;

-- ============ vcs_links(R3 新增 HIGH-3:VCS 对象 ↔ Mesh 实体 关联真源表)============
CREATE TABLE vcs_links (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id       UUID NOT NULL,
  provider             TEXT NOT NULL CHECK (provider IN ('github','gitlab')),
  provider_tenant_key  TEXT NOT NULL DEFAULT '',
  external_object_type TEXT NOT NULL
                       CHECK (external_object_type IN ('repository','pull_request','merge_request','issue','commit','branch')),
  external_object_ref  TEXT NOT NULL,
  mesh_entity_type     TEXT NOT NULL CHECK (mesh_entity_type IN ('issue','project')),
  mesh_entity_id       UUID NOT NULL,
  link_source          TEXT NOT NULL DEFAULT 'manual'
                       CHECK (link_source IN ('manual','auto_keyword','auto_branch','auto_commit')),
  status               TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','stale','deleted')),
  external_state       JSONB NOT NULL DEFAULT '{}',
  created_by           UUID NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_vcs_links_ws_id UNIQUE (workspace_id, id),                           -- 复合 FK 引用前提(§6.2)
  -- R3:同租户复合 FK(集成删除 → 其 VCS 关联级联删除)
  CONSTRAINT fk_vcs_links_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_vcs_links_created_by FOREIGN KEY (workspace_id, created_by)
    REFERENCES members(workspace_id, id) ON DELETE SET NULL (created_by)
);
-- R3:外部对象唯一键(active 部分唯一:外部对象至多一条 active 关联;stale/deleted 允许历史重关联)
CREATE UNIQUE INDEX uq_vcs_links_external_object
  ON vcs_links(provider, provider_tenant_key, external_object_type, external_object_ref)
  WHERE status = 'active';
CREATE UNIQUE INDEX uq_vcs_links_mesh_entity
  ON vcs_links(workspace_id, mesh_entity_type, mesh_entity_id, external_object_ref)
  WHERE status = 'active';
-- R3:状态索引(issue 侧栏「关联的 PR/MR」与陈旧关联清理扫描)
CREATE INDEX idx_vcs_links_entity_status
  ON vcs_links(workspace_id, mesh_entity_type, mesh_entity_id, status);
CREATE INDEX idx_vcs_links_integration_status ON vcs_links(integration_id, status);

-- R3(HIGH-4):notification_delivery 的 IM 路由列复合 FK(integrations/integration_bindings 建表后补挂;
-- 集成/绑定删除仅置空路由列,台账保留供排障——列级 SET NULL,README §6.2 第 6 条)
ALTER TABLE notification_delivery ADD CONSTRAINT fk_delivery_integration
  FOREIGN KEY (workspace_id, integration_id) REFERENCES integrations(workspace_id, id)
  ON DELETE SET NULL (integration_id);
ALTER TABLE notification_delivery ADD CONSTRAINT fk_delivery_binding
  FOREIGN KEY (workspace_id, binding_id) REFERENCES integration_bindings(workspace_id, id)
  ON DELETE SET NULL (binding_id);

-- ---- import-export.md DDL(R3 修订 HIGH-5:源附件 RESTRICT + 源哈希 + 检查点/租约 + 行台账)----
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
  source_content_hash  TEXT NULL,                          -- R3:源文件 sha256(validate 冻结;续跑前校验源未变)
  result_attachment_id UUID NULL,
  total_rows           INT NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
  succeeded_rows       INT NOT NULL DEFAULT 0 CHECK (succeeded_rows >= 0),
  failed_rows          INT NOT NULL DEFAULT 0 CHECK (failed_rows >= 0),
  error_report         JSONB NOT NULL DEFAULT '[]',
  checkpoint           JSONB NOT NULL DEFAULT '{}',        -- R3:持久恢复点 {last_committed_batch,last_row_key,batch_size,resumed_count,resumed_at}
  lease_owner          TEXT NULL,                          -- R3:在途 worker 租约(消除 running 守卫永久卡住)
  lease_seq            BIGINT NOT NULL DEFAULT 0,          -- R4(HIGH-2):单调 fencing token——每次领取/恢复 +1,旧 worker 的一切批提交因 seq 不匹配被拒(同 §6.4 lease_seq 范式)
  lease_expires_at     TIMESTAMPTZ NULL,
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
  -- R3:源附件 RESTRICT——作业存续期间源文件不可物理删除(此前 SET NULL 与上面的 CHECK 互斥,删除永远被 CHECK 拒绝)
  FOREIGN KEY (workspace_id, source_attachment_id)
      REFERENCES attachments(workspace_id, id) ON DELETE RESTRICT,
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
-- R3:租约过期作业回收扫描(reaper)
CREATE INDEX idx_data_jobs_lease_expired ON data_jobs (lease_expires_at)
  WHERE status = 'running';

-- ---- data_job_rows(R3 新增 HIGH-5:逐行结果台账——行级幂等键 + 崩溃恢复真源)----
CREATE TABLE data_job_rows (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  job_id       UUID NOT NULL,
  row_number   INT NOT NULL CHECK (row_number >= 1),
  row_key      TEXT NOT NULL,                              -- 行级稳定幂等键(external_ref 或 row:<n>:<sha256(行内容)>)
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','created','updated','skipped','failed')),
  target_type  TEXT NULL CHECK (target_type IN ('issue','project')),
  target_id    UUID NULL,
  error        JSONB NULL,
  attempts     INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  CONSTRAINT uq_data_job_rows_job_row_key UNIQUE (job_id, row_key),   -- R3:重放已提交批次不重复建实体
  CHECK ((status IN ('created','updated') AND target_type IS NOT NULL AND target_id IS NOT NULL)
      OR (status = 'failed' AND error IS NOT NULL)
      OR (status IN ('pending','skipped'))),
  CONSTRAINT fk_data_job_rows_job FOREIGN KEY (workspace_id, job_id)
    REFERENCES data_jobs(workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_data_job_rows_job_status ON data_job_rows (job_id, status);

-- ---- analytics.md DDL ----
CREATE TABLE analytics_snapshots (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  metric_key   TEXT NOT NULL,                 -- 'cycle_time'/'velocity'/'throughput'/'workload'/'burndown'/'agent_stats'
  scope_key    TEXT NOT NULL DEFAULT 'ws_admin',  -- R3(HIGH-8):可见性集合指纹(缓存键一部分,禁跨权限缓存)
  dimensions   JSONB NOT NULL DEFAULT '{}',   -- {project_id?, cycle_id?, milestone_id?, agent_id?, granularity?, from_category?, calendar_timezone?, scope_caliber?}
  dim_hash     TEXT GENERATED ALWAYS AS (md5(dimensions::text)) STORED,  -- 维度指纹,供唯一键/查找(避免 JSONB 直接入唯一索引)
  window_start TIMESTAMPTZ NOT NULL,          -- UTC
  window_end   TIMESTAMPTZ NOT NULL,          -- UTC
  value        JSONB NOT NULL,                -- 聚合结果(指标值 + 必要 meta,如 sample_size/token_coverage)
  computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- R3:同一 (工作区, 指标, 可见性集合, 维度, 窗) 仅一份快照——scope_key 入键即"跨权限不共享"
  UNIQUE (workspace_id, metric_key, scope_key, dim_hash, window_start, window_end)
);

CREATE INDEX idx_snapshots_lookup
  ON analytics_snapshots (workspace_id, metric_key, scope_key, dim_hash, window_start, window_end);
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

  -- 绑定外部身份全局唯一(R3:同 provider+tenant+external_ref 不可重复绑定)
  INSERT INTO integration_bindings (workspace_id, integration_id, provider, provider_tenant_key, external_ref, status)
  VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-1111-0000-0000-000000000001', 'feishu', 'tenant-a', 'chat-oc-1', 'active');
  BEGIN
    INSERT INTO integration_bindings (workspace_id, integration_id, provider, provider_tenant_key, external_ref, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-1111-0000-0000-000000000001', 'feishu', 'tenant-a', 'chat-oc-1', 'active');
    RAISE EXCEPTION 'P2 FAIL: 同外部身份重复绑定未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS P2-3: 外部身份唯一绑定 UNIQUE(provider, provider_tenant_key, external_ref)(R3 全局键)';
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

-- ============================================================================
-- MES-76 R2/R3 修订 DDL:auth sessions / device_authorizations + 搜索归一与索引
-- (auth.md §2.4/§2.4.2/§3.1.1、search-command-palette.md §2.2/member.md §2.2;
--  R3-H4:本轮表/CHECK/FK/部分唯一/前缀与 GIN 索引真正纳入 PG16 验证,不再只跑旧脚本)
-- ============================================================================

-- sessions:refresh token / 会话,可撤销;CLI/设备会话的 workspace/scope 真源(auth.md §2.4)
CREATE TABLE sessions (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- = access JWT 的 sid 与 refresh 的 jti(R2-H1)
  user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash              TEXT NOT NULL UNIQUE,                        -- 当前 refresh token SHA-256(不存明文)
  previous_token_hash     TEXT NULL UNIQUE,                            -- R4-M4 有界幂等轮换:上一枚 refresh 哈希(宽限窗内识别被轮换的旧凭证)
  rotated_at              TIMESTAMPTZ NULL,                            -- 最近轮换时刻(now()-rotated_at ≤ 宽限窗时旧凭证走宽限路径)
  authenticated_at        TIMESTAMPTZ NULL,                            -- R6-H3:最近主动认证时刻(step-up 唯一真源)。NULL = 无主动认证证明(静默 SSO / 设备会话继承批准会话 NULL);建 session ≠ 主动认证,取消无条件默认;按来源显式赋值,窗口判据 IS NOT NULL AND now()-authenticated_at ≤ MESH_STEP_UP_WINDOW_SECONDS
  type                    TEXT NOT NULL DEFAULT 'web' CHECK (type IN ('web','cli','api')),
  workspace_id            UUID NULL REFERENCES workspaces(id) ON DELETE CASCADE,   -- CLI/设备会话绑定工作区
  granted_scopes          TEXT[] NOT NULL DEFAULT '{}',                -- 会话固化签发 scope(refresh 续签再与当前角色取交)
  device_authorization_id UUID NULL,                                   -- FK 在 device_authorizations 建表后补(UNIQUE)
  user_agent              TEXT NULL,
  ip_address              INET NULL,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_active_at          TIMESTAMPTZ NULL,
  expires_at              TIMESTAMPTZ NOT NULL,
  revoked_at              TIMESTAMPTZ NULL,
  CHECK (type <> 'cli' OR workspace_id IS NOT NULL)                    -- R2-H1:设备会话必有绑定工作区
);
CREATE INDEX idx_sessions_user ON sessions (user_id) WHERE revoked_at IS NULL;

-- device_authorizations:OAuth 设备码授权(auth.md §2.4.2;状态机 + 单次消费 + 短码复用安全)
CREATE TABLE device_authorizations (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_code_hash    TEXT NOT NULL UNIQUE,                            -- HMAC-SHA256(pepper);128bit 无空间耗尽 → 全表 UNIQUE
  user_code_hash      TEXT NOT NULL,                                   -- HMAC-SHA256(pepper);唯一性见部分唯一索引(R3-M3)
  status              TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','approved','denied','consumed','expired','invalidated')),
  requested_scopes    TEXT[] NOT NULL DEFAULT '{}',
  granted_scopes      TEXT[] NULL,                                     -- 批准时固化:请求 scope ∩ 名册行角色权限(R3-H5)
  approved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
  approved_authenticated_at TIMESTAMPTZ NULL,                           -- R6-H3:批准者浏览器会话 authenticated_at 快照(approve 事务 FOR UPDATE 锁定读取;consume 时复制进 cli 会话,绝不以消费时刻冒充)
  workspace_id        UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
  failed_attempts     INT NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
  request_ip          INET NULL,
  approved_at         TIMESTAMPTZ NULL,
  denied_at           TIMESTAMPTZ NULL,
  consumed_at         TIMESTAMPTZ NULL,
  invalidated_at      TIMESTAMPTZ NULL,
  expires_at          TIMESTAMPTZ NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- R3-M3:user_code 低熵短码(≥20bit),全历史 UNIQUE 会使码空间随累计耗尽;
-- 部分唯一仅约束活跃码(pending/approved),终态后允许安全复用
CREATE UNIQUE INDEX uq_device_auth_user_code_active ON device_authorizations (user_code_hash)
  WHERE status IN ('pending','approved');
CREATE INDEX idx_device_auth_pending ON device_authorizations (expires_at) WHERE status = 'pending';

-- sessions.device_authorization_id → device_authorizations(单码至多一会话)
ALTER TABLE sessions
  ADD CONSTRAINT fk_sessions_device_auth
  FOREIGN KEY (device_authorization_id) REFERENCES device_authorizations(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX uq_sessions_device_auth ON sessions (device_authorization_id);

-- ----------------------------------------------------------------------------
-- 搜索投影 + 归一表达式索引(search-command-palette.md §2.2 DDL 实跑,R2-H3/R3-M1)
-- mesh_search_norm 见文件头部(public schema + 显式 regdictionary,IMMUTABLE)
-- ----------------------------------------------------------------------------
ALTER TABLE members ADD COLUMN IF NOT EXISTS search_name TEXT NOT NULL DEFAULT '';

-- members:trigram(≥3 字符)+ workspace-scoped pattern(1–2 字符前缀)
CREATE INDEX idx_members_search_name_trgm ON members USING gin (search_name gin_trgm_ops);
CREATE INDEX idx_members_search_name_prefix ON members (workspace_id, search_name text_pattern_ops)
  WHERE status <> 'removed';

-- issues:identifier 等值快路径已有 UNIQUE(workspace_id, identifier);title 归一 trigram/pattern + 租户软删组合
CREATE INDEX idx_issues_title_trgm ON issues USING gin ((public.mesh_search_norm(title)) gin_trgm_ops)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_title_prefix ON issues (workspace_id, (public.mesh_search_norm(title)) text_pattern_ops)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_identifier_prefix ON issues (workspace_id, (public.mesh_search_norm(identifier)) text_pattern_ops)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_ws_not_deleted ON issues (workspace_id, project_id)
  WHERE deleted_at IS NULL;

-- projects / views / chat_sessions
CREATE INDEX idx_projects_name_trgm ON projects USING gin ((public.mesh_search_norm(name)) gin_trgm_ops)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_projects_name_prefix ON projects (workspace_id, (public.mesh_search_norm(name)) text_pattern_ops)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_views_name_trgm ON views USING gin ((public.mesh_search_norm(name)) gin_trgm_ops);
CREATE INDEX idx_views_name_prefix ON views (workspace_id, (public.mesh_search_norm(name)) text_pattern_ops);
CREATE INDEX idx_chat_sessions_title_trgm ON chat_sessions USING gin ((public.mesh_search_norm(title)) gin_trgm_ops);
CREATE INDEX idx_chat_sessions_title_prefix ON chat_sessions (workspace_id, (public.mesh_search_norm(title)) text_pattern_ops);

-- ============================================================================
-- R3 修订行为验证(HIGH-1～HIGH-9 + 建议项;测试编号顺延 T27～T34,README §9)
-- ============================================================================

-- ===================== T27:agent 配置版本同租户/同 agent 约束(HIGH-1)=====================
DO $$
DECLARE
  v_ver UUID;
BEGIN
  -- 合法:本工作区 agent 的版本 + 本工作区 changed_by + active 指针指回本 agent
  INSERT INTO agent_config_versions (id, workspace_id, agent_id, snapshot, changed_by)
  VALUES ('e1e1e1e1-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
          'bbbbbbbb-0000-0000-0000-000000000001', '{}', 'cccccccc-0000-0000-0000-000000000001')
  RETURNING id INTO v_ver;
  UPDATE agents SET active_config_version_id = v_ver WHERE id = 'bbbbbbbb-0000-0000-0000-000000000001';
  RAISE NOTICE 'PASS T27-1: 同租户同 agent 配置版本与 active 指针正常写入';

  -- ① 跨租户 changed_by(别工作区成员)→ 复合 FK 拒绝
  BEGIN
    INSERT INTO agent_config_versions (workspace_id, agent_id, snapshot, changed_by)
    VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001', '{}',
            'cccccccc-0000-0000-0000-000000000009');   -- WS-B 的成员
    RAISE EXCEPTION 'T27 FAIL: 跨租户 changed_by 未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T27-2: changed_by 跨租户被复合 FK (workspace_id, changed_by) 拒绝';
  END;

  -- ② 跨租户 agent_id → 复合 FK 拒绝
  BEGIN
    INSERT INTO agent_config_versions (workspace_id, agent_id, snapshot, changed_by)
    VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000002', '{}',
            'cccccccc-0000-0000-0000-000000000001');   -- agent 属于 WS-B
    RAISE EXCEPTION 'T27 FAIL: 跨租户 agent_id 未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T27-3: agent_id 跨租户被复合 FK (workspace_id, agent_id) 拒绝';
  END;

  -- ③ active 指针指向别 agent 的版本 → 重叠复合 FK 拒绝(同父域,README §6.2 第 7 条)
  INSERT INTO agent_config_versions (id, workspace_id, agent_id, snapshot, changed_by)
  VALUES ('e1e1e1e1-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
          'bbbbbbbb-0000-0000-0000-000000000001', '{}', 'cccccccc-0000-0000-0000-000000000001');
  BEGIN
    -- 造一个 WS-B 的 agent 版本,再让 WS-A 的 agent 指过去
    INSERT INTO agent_config_versions (id, workspace_id, agent_id, snapshot, changed_by)
    VALUES ('e1e1e1e1-0000-0000-0000-000000000009', '22222222-2222-2222-2222-222222222222',
            'bbbbbbbb-0000-0000-0000-000000000002', '{}', 'cccccccc-0000-0000-0000-000000000009');
    UPDATE agents SET active_config_version_id = 'e1e1e1e1-0000-0000-0000-000000000009'
     WHERE id = 'bbbbbbbb-0000-0000-0000-000000000001';
    RAISE EXCEPTION 'T27 FAIL: active 指针跨 agent/跨租户串指未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T27-4: active_config_version_id 重叠复合 FK 拒绝跨 agent/跨租户串指';
  END;
  -- 同工作区但别 agent 的版本(非跨租户)同样被拒:重叠键要求 agent_id 一致
  BEGIN
    UPDATE agents SET active_config_version_id = 'e1e1e1e1-0000-0000-0000-000000000002'
     WHERE id = 'bbbbbbbb-0000-0000-0000-000000000002';  -- WS-B 的 agent 指 WS-A agent 的版本
    RAISE EXCEPTION 'T27 FAIL: active 指针指向别 agent 版本未被拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T27-5: active 指针指向别 agent 的版本被重叠复合 FK (ws, id, active) → (ws, agent_id, id) 拒绝';
  END;
END $$;

-- ===================== T28:能力字段严格类型 + 归一(HIGH-2;R4 HIGH-1 扩展:permission 必填 + 同一归一实现实测)=====================
DO $$
DECLARE
  v_norm JSONB;
BEGIN
  -- ① 严格快照合法(调度字段纯字符串数组 + 授权快照 [{capability,permission}] 且 permission 均存在)
  INSERT INTO task_executions (workspace_id, agent_id, required_capabilities, config_snapshot, status)
  VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001',
          '["ffmpeg","version_control"]',
          '{"capability_grants":[{"capability":"ffmpeg","permission":"write"},{"capability":"version_control","permission":"confirm_required"}]}',
          'queued');
  RAISE NOTICE 'PASS T28-1: 调度字段纯字符串数组 + 授权快照严格 [{capability,permission}] 写入合法';

  -- ② 对象混入调度字段 → CHECK 拒绝(否则 claim 的 <@ 永不命中,任务永久无法领取)
  BEGIN
    INSERT INTO task_executions (workspace_id, agent_id, required_capabilities, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001',
            '[{"capability":"exec:shell","permission":"write"}]', 'queued');
    RAISE EXCEPTION 'T28 FAIL: 对象进入调度字段未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T28-2: required_capabilities 拒绝对象元素(jsonb_is_string_array CHECK)';
  END;

  -- ③ 授权快照非对象数组(字符串)→ CHECK 拒绝
  BEGIN
    INSERT INTO task_executions (workspace_id, agent_id, required_capabilities, config_snapshot, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001',
            '["exec:shell"]', '{"capability_grants":["exec:shell"]}', 'queued');
    RAISE EXCEPTION 'T28 FAIL: capability_grants 字符串数组未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T28-3: config_snapshot.capability_grants 拒绝非 {capability,permission} 对象元素';
  END;

  -- ④ 非法 permission 值 → CHECK 拒绝
  BEGIN
    INSERT INTO task_executions (workspace_id, agent_id, required_capabilities, config_snapshot, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001',
            '["net:outbound"]', '{"capability_grants":[{"capability":"net:outbound","permission":"admin"}]}', 'queued');
    RAISE EXCEPTION 'T28 FAIL: 非法 permission 未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T28-4: capability_grants.permission 取值受 read_only|write|confirm_required 约束';
  END;

  -- ⑤ R4(HIGH-1):授权快照 **缺失 permission** → CHECK 必拒绝(归一后快照不允许缺 permission)
  BEGIN
    INSERT INTO task_executions (workspace_id, agent_id, required_capabilities, config_snapshot, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001',
            '["version_control"]', '{"capability_grants":[{"capability":"version_control"}]}', 'queued');
    RAISE EXCEPTION 'T28 FAIL: capability_grants 缺失 permission 未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T28-5: capability_grants 条目缺失 permission 必被 CHECK 拒绝(R4:permission 必填)';
  END;

  -- ⑥ R4(HIGH-1):permission 非字符串类型(如数字)→ CHECK 拒绝
  BEGIN
    INSERT INTO task_executions (workspace_id, agent_id, required_capabilities, config_snapshot, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001',
            '["version_control"]', '{"capability_grants":[{"capability":"version_control","permission":2}]}', 'queued');
    RAISE EXCEPTION 'T28 FAIL: permission 非字符串未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T28-6: capability_grants.permission 必须为字符串(R4:类型严格)';
  END;

  -- ⑦ R4(HIGH-1):**以混合字符串/对象声明调用同一归一实现** normalize_capability_declarations(),
  --    断言字符串自动补 confirm_required、未标注 permission 补 confirm_required、去重、最严格权限、字典序排序
  v_norm := normalize_capability_declarations(
    '["ffmpeg",
      {"capability":"version_control"},
      {"capability":"exec:shell","permission":"read_only"},
      "exec:shell",
      {"capability":"net:fetch","permission":"write"},
      {"capability":"net:fetch","permission":"read_only"},
      {"capability":"data:read","permission":"read_only"},
      {"capability":"data:read","permission":"write"}]'::jsonb);
  -- 调度字段:纯 key 字符串数组,去重 + 字典序排序
  ASSERT v_norm->'required' = '["data:read","exec:shell","ffmpeg","net:fetch","version_control"]'::jsonb,
         'T28 FAIL: 归一 required 应为去重 + 字典序排序的纯字符串数组';
  -- 授权快照:严格 [{capability,permission}];字符串/未标注补 confirm_required;同 capability 取最严格;字典序
  ASSERT v_norm->'grants' = '[{"capability":"data:read","permission":"write"},
                              {"capability":"exec:shell","permission":"confirm_required"},
                              {"capability":"ffmpeg","permission":"confirm_required"},
                              {"capability":"net:fetch","permission":"write"},
                              {"capability":"version_control","permission":"confirm_required"}]'::jsonb,
         'T28 FAIL: 归一 grants 应补默认 confirm_required、同 capability 取最严格权限、字典序排序';
  ASSERT jsonb_is_string_array(v_norm->'required') AND jsonb_is_capability_grants(v_norm->'grants'),
         'T28 FAIL: 归一产物必须通过 schema 严格类型校验';
  -- 空声明归一为两个空数组(合法入队形态)
  ASSERT normalize_capability_declarations('[]'::jsonb) = '{"required": [], "grants": []}'::jsonb,
         'T28 FAIL: 空声明应归一为 {"required":[],"grants":[]}';
  RAISE NOTICE 'PASS T28-7: 同一归一实现处理混合声明(字符串补 confirm_required / 去重 / 最严格权限 / 排序),产物通过严格类型校验';

  -- ⑧ R4:归一产物直接写入 task_executions(调度字段 + 授权快照)并通过 CHECK,claim 能力匹配 (<@) 命中
  INSERT INTO task_executions (workspace_id, agent_id, required_capabilities, config_snapshot, status)
  VALUES ('11111111-1111-1111-1111-111111111111', 'bbbbbbbb-0000-0000-0000-000000000001',
          v_norm->'required', jsonb_build_object('capability_grants', v_norm->'grants'), 'queued');
  INSERT INTO runtimes (id, workspace_id, name, status, capabilities, max_concurrent)
  VALUES ('abababab-0000-0000-0000-000000000099', '11111111-1111-1111-1111-111111111111', 'rt-t28', 'online',
          '["data:read","exec:shell","ffmpeg","net:fetch","version_control"]', 1);
  PERFORM 1
    FROM task_executions e
   WHERE e.workspace_id = '11111111-1111-1111-1111-111111111111'
     AND e.status = 'queued'
     AND e.required_capabilities <@ (SELECT capabilities FROM runtimes WHERE id = 'abababab-0000-0000-0000-000000000099')
     AND e.required_capabilities @> '["ffmpeg"]'::jsonb;
  ASSERT FOUND, 'T28 FAIL: 归一后的字符串数组应可被 claim <@ 匹配命中';
  RAISE NOTICE 'PASS T28-8: 归一产物入队合法且 claim 能力匹配 (<@) 命中(调度/授权两套字段联动)';

  -- ⑨ R4:归一实现对非法声明拒绝入队(422 capability_invalid):非法 permission 值 / 非字符串非对象条目 / 非数组输入
  DECLARE
    v_rejected BOOLEAN;
  BEGIN
    v_rejected := FALSE;
    BEGIN
      PERFORM normalize_capability_declarations('[{"capability":"x","permission":"admin"}]'::jsonb);
    EXCEPTION WHEN raise_exception THEN
      v_rejected := (SQLERRM LIKE '%capability_invalid%');
    END;
    ASSERT v_rejected, 'T28 FAIL: 非法 permission 声明未被归一实现拒绝(应抛 capability_invalid)';

    v_rejected := FALSE;
    BEGIN
      PERFORM normalize_capability_declarations('[42]'::jsonb);
    EXCEPTION WHEN raise_exception THEN
      v_rejected := (SQLERRM LIKE '%capability_invalid%');
    END;
    ASSERT v_rejected, 'T28 FAIL: 数字条目未被归一实现拒绝';

    v_rejected := FALSE;
    BEGIN
      PERFORM normalize_capability_declarations('{"capability":"x"}'::jsonb);
    EXCEPTION WHEN raise_exception THEN
      v_rejected := (SQLERRM LIKE '%capability_invalid%');
    END;
    ASSERT v_rejected, 'T28 FAIL: 非数组输入未被归一实现拒绝';
    RAISE NOTICE 'PASS T28-9: 归一实现拒绝非法 permission / 非法条目形态 / 非数组输入(capability_invalid,422)';
  END;
END $$;

-- ===================== T29:集成外部身份全局唯一 + scope 异或 + vcs_links(HIGH-3)=====================
DO $$
BEGIN
  -- 夹具:WS-B 的另一个 feishu 集成实例(模拟两工作区各装一个飞书应用)
  INSERT INTO integrations (id, workspace_id, kind, name, status, created_by)
  VALUES ('abababab-2222-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'im_feishu', 'feishu-b', 'active',
          'cccccccc-0000-0000-0000-000000000009');

  -- ① 跨 workspace 抢绑同一外部身份 → 全局唯一键拒绝
  BEGIN
    INSERT INTO integration_bindings (workspace_id, integration_id, provider, provider_tenant_key, external_ref, status)
    VALUES ('22222222-2222-2222-2222-222222222222', 'abababab-2222-0000-0000-000000000001', 'feishu', 'tenant-a', 'chat-oc-1', 'active');
    RAISE EXCEPTION 'T29 FAIL: 跨 workspace 重复绑定同一外部身份未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T29-1: 外部身份跨 workspace 唯一 UNIQUE(provider, provider_tenant_key, external_ref)';
  END;

  -- ② workspace scope 携带 project_id → 精确异或 CHECK 拒绝
  BEGIN
    INSERT INTO integration_bindings (workspace_id, integration_id, provider, provider_tenant_key, external_ref, scope, project_id, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-1111-0000-0000-000000000001', 'feishu', 'tenant-a', 'chat-oc-2',
            'workspace', 'dddddddd-0000-0000-0000-000000000001', 'active');
    RAISE EXCEPTION 'T29 FAIL: workspace scope 带 project_id 未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T29-2: scope/project 精确异或(workspace 不得带 project)';
  END;

  -- ③ project scope 缺 project_id → CHECK 拒绝
  BEGIN
    INSERT INTO integration_bindings (workspace_id, integration_id, provider, provider_tenant_key, external_ref, scope, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-1111-0000-0000-000000000001', 'feishu', 'tenant-a', 'chat-oc-3',
            'project', 'active');
    RAISE EXCEPTION 'T29 FAIL: project scope 缺 project_id 未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T29-3: scope/project 精确异或(project 必带 project)';
  END;

  -- ④ 项目级绑定:删除项目 → 绑定经 CASCADE 一并删除(不产生违反 CHECK 的置空行)
  INSERT INTO projects (id, workspace_id, name, key) VALUES
    ('dddddddd-0000-0000-0000-000000000099', '11111111-1111-1111-1111-111111111111', 'T29 项目', 'T29');
  INSERT INTO integration_bindings (workspace_id, integration_id, provider, provider_tenant_key, external_ref, scope, project_id, status)
  VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-1111-0000-0000-000000000001', 'feishu', 'tenant-a', 'chat-oc-4',
          'project', 'dddddddd-0000-0000-0000-000000000099', 'active');
  DELETE FROM projects WHERE id = 'dddddddd-0000-0000-0000-000000000099';
  ASSERT NOT EXISTS (SELECT 1 FROM integration_bindings WHERE external_ref = 'chat-oc-4'),
         'T29 FAIL: 项目删除应级联删除项目级绑定';
  RAISE NOTICE 'PASS T29-4: 删除项目 → 项目级绑定 ON DELETE CASCADE(无 SET NULL 违反 CHECK 的不可达态)';

  -- ⑤ vcs_links:外部对象 active 唯一 + 同租户复合 FK + 集成删除级联
  INSERT INTO integrations (id, workspace_id, kind, name, status, created_by)
  VALUES ('abababab-3333-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'vcs_github', 'gh-1', 'active',
          'cccccccc-0000-0000-0000-000000000001');
  INSERT INTO vcs_links (workspace_id, integration_id, provider, provider_tenant_key, external_object_type,
                         external_object_ref, mesh_entity_type, mesh_entity_id, link_source, status)
  VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-3333-0000-0000-000000000001', 'github', 'inst-1',
          'pull_request', 'acme/web#123', 'issue', '99999999-0000-0000-0000-000000000001', 'auto_keyword', 'active');
  BEGIN
    INSERT INTO vcs_links (workspace_id, integration_id, provider, provider_tenant_key, external_object_type,
                           external_object_ref, mesh_entity_type, mesh_entity_id, status)
    VALUES ('11111111-1111-1111-1111-111111111111', 'abababab-3333-0000-0000-000000000001', 'github', 'inst-1',
            'pull_request', 'acme/web#123', 'issue', '99999999-0000-0000-0000-000000000002', 'active');
    RAISE EXCEPTION 'T29 FAIL: 同一外部 PR 重复 active 关联未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T29-5: vcs_links 外部对象 active 部分唯一键生效';
  END;
  DELETE FROM integrations WHERE id = 'abababab-3333-0000-0000-000000000001';
  ASSERT NOT EXISTS (SELECT 1 FROM vcs_links WHERE external_object_ref = 'acme/web#123'),
         'T29 FAIL: 集成删除应级联删除其 vcs_links';
  RAISE NOTICE 'PASS T29-6: vcs_links 同租户复合 FK,集成删除级联删关联';

  -- ⑦ R4(HIGH-5;R5 全局化):external_identities 映射**全局 users.id**、身份键含平台租户(卡片回调点击者鉴权真源);
  --   R5:建链发起工作区仅作 created_in_workspace_id 审计列(全局表,无 workspace_id 所有权列)
  INSERT INTO external_identities (provider, provider_tenant_key, external_user_key, user_id, created_in_workspace_id)
  VALUES ('feishu', 'tenant-a', 'ou_user_1',
          'aaaaaaaa-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111');
  RAISE NOTICE 'PASS T29-7: 认证外部账号映射全局 users.id(身份键 = provider + provider_tenant_key + external_user_key;建链工作区仅审计)';

  -- ⑧ R4(HIGH-5):**同一已认证外部账号跨两个 Mesh 工作区参与**——单映射行,回调按集成解析工作区后
  --    JOIN 该工作区的 members(workspace_id, user_id),两个工作区各自的 member 行均可解析(§6.1 核心模型)
  ASSERT (SELECT COUNT(*) = 1 FROM external_identities
           WHERE provider = 'feishu' AND provider_tenant_key = 'tenant-a' AND external_user_key = 'ou_user_1'),
         'T29 FAIL: 同一外部账号应仅一条全局映射行';
  -- 回调链:WS-A 集成 → users.id → WS-A 名册成员 cccccccc-...-0001
  ASSERT EXISTS (
    SELECT 1
      FROM external_identities ei
      JOIN members m ON m.workspace_id = '11111111-1111-1111-1111-111111111111' AND m.user_id = ei.user_id
     WHERE ei.provider = 'feishu' AND ei.provider_tenant_key = 'tenant-a' AND ei.external_user_key = 'ou_user_1'
       AND m.id = 'cccccccc-0000-0000-0000-000000000001' AND m.status = 'active'),
         'T29 FAIL: WS-A 回调应经映射 JOIN 到本工作区 member';
  -- 回调链:WS-B 集成 → 同一 users.id → WS-B 名册成员 cccccccc-...-0009(同一自然人的另一 member 行)
  ASSERT EXISTS (
    SELECT 1
      FROM external_identities ei
      JOIN members m ON m.workspace_id = '22222222-2222-2222-2222-222222222222' AND m.user_id = ei.user_id
     WHERE ei.provider = 'feishu' AND ei.provider_tenant_key = 'tenant-a' AND ei.external_user_key = 'ou_user_1'
       AND m.id = 'cccccccc-0000-0000-0000-000000000009' AND m.status = 'active'),
         'T29 FAIL: 同一外部账号应可跨两个 Mesh 工作区解析各自 member(不再被锁到单个 member_id)';
  RAISE NOTICE 'PASS T29-8: 同一认证外部账号跨两个 Mesh 工作区参与(单映射 + 按工作区 JOIN member)';

  -- ⑨ R4(HIGH-5):**不同外部租户同 user key 并存**——身份键含 provider_tenant_key,不再全局误撞
  INSERT INTO external_identities (provider, provider_tenant_key, external_user_key, user_id, created_in_workspace_id)
  VALUES ('feishu', 'tenant-b', 'ou_user_1',
          'aaaaaaaa-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222');
  ASSERT (SELECT COUNT(*) = 2 FROM external_identities WHERE external_user_key = 'ou_user_1'),
         'T29 FAIL: 不同外部租户的同名 user key 应可并存';
  RAISE NOTICE 'PASS T29-9: 不同外部租户同 user key 不冲突(身份键纳入 provider tenant)';

  -- ⑩ R4(HIGH-5):同一外部账号(provider+tenant+user key)重复映射 → 全局唯一键拒绝(即使指向不同用户)
  BEGIN
    INSERT INTO external_identities (provider, provider_tenant_key, external_user_key, user_id, created_in_workspace_id)
    VALUES ('feishu', 'tenant-a', 'ou_user_1',
            'aaaaaaaa-0000-0000-0000-000000000003', '22222222-2222-2222-2222-222222222222');
    RAISE EXCEPTION 'T29 FAIL: 同一外部账号重复映射未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T29-10: UNIQUE(provider, provider_tenant_key, external_user_key) 拒绝同一外部账号重复映射';
  END;

  -- ⑪ R4(HIGH-5):用户注销 → 映射级联删除(映射真源挂全局 users.id,ON DELETE CASCADE——生命周期唯一级联来源)
  INSERT INTO users (id, email, display_name) VALUES
    ('aaaaaaaa-0000-0000-0000-000000000099', 'u99@example.test', 'U99');
  INSERT INTO external_identities (provider, provider_tenant_key, external_user_key, user_id, created_in_workspace_id)
  VALUES ('github', '', 'dev-u99',
          'aaaaaaaa-0000-0000-0000-000000000099', '11111111-1111-1111-1111-111111111111');
  DELETE FROM users WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099';
  ASSERT NOT EXISTS (SELECT 1 FROM external_identities WHERE external_user_key = 'dev-u99'),
         'T29 FAIL: 用户注销应级联删除其外部身份映射';
  RAISE NOTICE 'PASS T29-11: external_identities.user_id ON DELETE CASCADE(用户注销 → 映射删除,卡片点击回落 403)';

  -- ⑫ R5(HIGH-2):**删除建链工作区后全局映射仍存在,其他工作区回调仍可解析**——
  --    临时工作区 WS-C 内建链,物理删除 WS-C:映射行保留(created_in_workspace_id 列级 SET NULL),
  --    同一用户在工作区 B 的回调链(JOIN members)照常解析成功
  INSERT INTO workspaces (id, name, slug) VALUES
    ('33333333-3333-3333-3333-333333333333', 'WS C', 'ws-c');
  INSERT INTO members (id, workspace_id, member_type, user_id, role) VALUES
    ('cccccccc-0000-0000-0000-00000000000c', '33333333-3333-3333-3333-333333333333', 'human',
     'aaaaaaaa-0000-0000-0000-000000000001', 'member');
  INSERT INTO external_identities (id, provider, provider_tenant_key, external_user_key, user_id, created_in_workspace_id)
  VALUES ('44444444-0000-0000-0000-000000000001', 'slack', 'team-c', 'ext-u1-c',
          'aaaaaaaa-0000-0000-0000-000000000001', '33333333-3333-3333-3333-333333333333');
  DELETE FROM workspaces WHERE id = '33333333-3333-3333-3333-333333333333';
  ASSERT EXISTS (SELECT 1 FROM external_identities
                  WHERE id = '44444444-0000-0000-0000-000000000001' AND created_in_workspace_id IS NULL),
         'T29 FAIL: 删除建链工作区后全局映射应保留,created_in_workspace_id 经 SET NULL 置空(不得级联删除映射)';
  -- 该用户(u1)在 WS-B 的卡片回调链仍解析成功:映射 → users.id → WS-B 名册行 cccccccc-...-0009
  ASSERT EXISTS (
    SELECT 1
      FROM external_identities ei
      JOIN members m ON m.workspace_id = '22222222-2222-2222-2222-222222222222' AND m.user_id = ei.user_id
     WHERE ei.id = '44444444-0000-0000-0000-000000000001'
       AND m.id = 'cccccccc-0000-0000-0000-000000000009' AND m.status = 'active'),
         'T29 FAIL: 删除建链工作区后,其余工作区回调仍应经全局映射 JOIN 名册行解析成功';
  RAISE NOTICE 'PASS T29-12: 删除建链工作区 → 映射保留(SET NULL 审计列)且其他工作区回调仍可解析(全局表不受工作区删除级联)';

  -- ⑬ R5(HIGH-2):**全局表结构 + RLS 负向测试**——无 workspace_id 列、无对工作区的 CASCADE FK、无 workspace RLS 策略
  ASSERT NOT EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'external_identities' AND column_name = 'workspace_id'),
         'T29 FAIL: 全局身份表不应携带 workspace_id 所有权列(否则回到单工作区拥有全局身份的旧模型)';
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_name = 'external_identities' AND column_name = 'created_in_workspace_id'
                    AND is_nullable = 'YES'),
         'T29 FAIL: created_in_workspace_id 应为可空审计列';
  ASSERT NOT EXISTS (
    SELECT 1
      FROM information_schema.referential_constraints rc
      JOIN information_schema.table_constraints tc
        ON tc.constraint_name = rc.constraint_name AND tc.constraint_schema = rc.constraint_schema
      JOIN information_schema.constraint_column_usage ccu
        ON ccu.constraint_name = rc.constraint_name AND ccu.constraint_schema = rc.constraint_schema
     WHERE tc.table_name = 'external_identities' AND tc.constraint_type = 'FOREIGN KEY'
       AND ccu.table_name = 'workspaces' AND rc.delete_rule = 'CASCADE'),
         'T29 FAIL: external_identities 对工作区不得有 ON DELETE CASCADE 外键(映射生命周期不受任何工作区删除控制)';
  ASSERT EXISTS (
    SELECT 1
      FROM information_schema.referential_constraints rc
      JOIN information_schema.table_constraints tc
        ON tc.constraint_name = rc.constraint_name AND tc.constraint_schema = rc.constraint_schema
      JOIN information_schema.constraint_column_usage ccu
        ON ccu.constraint_name = rc.constraint_name AND ccu.constraint_schema = rc.constraint_schema
     WHERE tc.table_name = 'external_identities' AND tc.constraint_type = 'FOREIGN KEY'
       AND ccu.table_name = 'workspaces' AND rc.delete_rule = 'SET NULL'),
         'T29 FAIL: created_in_workspace_id 应为 ON DELETE SET NULL 审计外键';
  ASSERT NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'external_identities'),
         'T29 FAIL: 全局身份表不适用 workspace RLS(无 workspace_id 可建 mesh.workspace_id 策略;行级边界为所属 user_id)';
  RAISE NOTICE 'PASS T29-13: 全局表结构负向测试(无 workspace_id 列 / 无 CASCADE 工作区 FK / 无 workspace RLS 策略;created_in_workspace_id 可空 SET NULL)';

  -- ⑭ R5(HIGH-2):**全局解链权限负向测试**——仅映射所属 users.id 本人可解链,admin/owner 角色无旁路
  --    (映射 44444444-...-0001 属 u1;成员角色不参与 external_identity_unlink_allowed 判定)
  INSERT INTO members (id, workspace_id, member_type, user_id, role) VALUES
    ('cccccccc-0000-0000-0000-00000000000b', '22222222-2222-2222-2222-222222222222', 'human',
     'aaaaaaaa-0000-0000-0000-000000000002', 'admin');                       -- u2:WS-B 的 admin,非映射所属用户
  ASSERT external_identity_unlink_allowed('44444444-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-000000000001')
     AND external_identity_unlink_allowed('44444444-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-000000000009'),
         'T29 FAIL: 映射所属用户本人(经任一工作区成员行解析)应可解链自己的全局身份';
  ASSERT NOT external_identity_unlink_allowed('44444444-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-00000000000b'),
         'T29 FAIL: 工作区 admin(非映射所属用户)不得解链他人全局身份(无 admin 旁路,403 identity_unlink_forbidden)';
  ASSERT NOT external_identity_unlink_allowed('44444444-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-000000000003'),
         'T29 FAIL: 其他普通成员不得解链他人全局身份';
  -- 管理员的可及手段仅「撤销本工作区使用权/成员资格」:把 u1 在 WS-B 的名册行置软终态 removed 后
  -- (README §6.1 名册软终态;物理删除被 created_by RESTRICT 拦阻属预期——审计引用不悬空),
  -- WS-B 回调链 JOIN active 名册行失败(该工作区审批回落 403),但全局映射与其他工作区不受影响
  UPDATE members SET status = 'removed' WHERE id = 'cccccccc-0000-0000-0000-000000000009';
  ASSERT NOT EXISTS (
    SELECT 1
      FROM external_identities ei
      JOIN members m ON m.workspace_id = '22222222-2222-2222-2222-222222222222' AND m.user_id = ei.user_id
     WHERE ei.id = '44444444-0000-0000-0000-000000000001' AND m.status = 'active'),
         'T29 FAIL: 撤销成员资格后该工作区回调应 JOIN active 名册行失败(回落 403)';
  ASSERT EXISTS (SELECT 1 FROM external_identities WHERE id = '44444444-0000-0000-0000-000000000001'),
         'T29 FAIL: 撤销工作区成员资格不得删除全局映射(仅解链所属用户本人/用户注销可删)';
  ASSERT EXISTS (
    SELECT 1
      FROM external_identities ei
      JOIN members m ON m.workspace_id = '11111111-1111-1111-1111-111111111111' AND m.user_id = ei.user_id
     WHERE ei.id = '44444444-0000-0000-0000-000000000001'
       AND m.id = 'cccccccc-0000-0000-0000-000000000001' AND m.status = 'active'),
         'T29 FAIL: 一个工作区的成员资格撤销不得影响其余工作区的回调解析';
  -- 恢复 WS-B 名册行状态(保持后续测试夹具完整)
  UPDATE members SET status = 'active' WHERE id = 'cccccccc-0000-0000-0000-000000000009';
  RAISE NOTICE 'PASS T29-14: 全局解链权限负向(仅所属 users.id 本人;admin 无旁路;撤销成员资格仅使本工作区回落 403,全局映射不动)';

  -- 清理 R5 夹具
  DELETE FROM members WHERE id IN ('cccccccc-0000-0000-0000-00000000000b');
  DELETE FROM external_identities WHERE id = '44444444-0000-0000-0000-000000000001';
END $$;

-- ===================== T30:IM 投递台账多目的地 + error 分离(HIGH-4)=====================
DO $$
DECLARE
  v_notif UUID;
BEGIN
  INSERT INTO notifications (id, workspace_id, recipient_id, type, priority)
  VALUES ('16161616-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
          'cccccccc-0000-0000-0000-000000000001', 'comment_created', 'normal')
  RETURNING id INTO v_notif;

  -- ① 同一通知并发投递两个 IM 目的地(不同 destination_key)→ 两行台账并存
  INSERT INTO notification_delivery (workspace_id, notification_id, channel, destination_key, provider, external_target, state)
  VALUES ('11111111-1111-1111-1111-111111111111', v_notif, 'im', 'feishu:bind-1:oc_1', 'feishu', 'oc_1', 'pending'),
         ('11111111-1111-1111-1111-111111111111', v_notif, 'im', 'slack:bind-2:C01', 'slack', 'C01', 'pending');
  ASSERT (SELECT COUNT(*) = 2 FROM notification_delivery WHERE notification_id = v_notif AND channel = 'im'),
         'T30 FAIL: 同一通知应可并发投递多个 IM 目的地';
  RAISE NOTICE 'PASS T30-1: 同一通知多 IM 目的地并存((notification_id, channel, destination_key) 唯一)';

  -- ② 同目的地重复投递 → 唯一键拒绝(幂等)
  BEGIN
    INSERT INTO notification_delivery (workspace_id, notification_id, channel, destination_key, provider, external_target, state)
    VALUES ('11111111-1111-1111-1111-111111111111', v_notif, 'im', 'feishu:bind-1:oc_1', 'feishu', 'oc_1', 'pending');
    RAISE EXCEPTION 'T30 FAIL: 同目的地重复投递未被唯一键拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T30-2: 同目的地重投经 UNIQUE(notification_id, channel, destination_key) 幂等';
  END;

  -- ③ in_app/websocket 单一目的地 destination_key='' 行为与旧键等价
  INSERT INTO notification_delivery (workspace_id, notification_id, channel, state)
  VALUES ('11111111-1111-1111-1111-111111111111', v_notif, 'in_app', 'sent');
  BEGIN
    INSERT INTO notification_delivery (workspace_id, notification_id, channel, state)
    VALUES ('11111111-1111-1111-1111-111111111111', v_notif, 'in_app', 'sent');
    RAISE EXCEPTION 'T30 FAIL: in_app 重复投递未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T30-3: in_app/websocket destination_key=空串,每通知每渠道一行(旧语义保持)';
  END;

  -- ④ 路由数据不进 error:provider/external_target 为独立列,失败只记原因
  UPDATE notification_delivery SET state = 'failed', error = 'timeout'
   WHERE notification_id = v_notif AND destination_key = 'slack:bind-2:C01';
  ASSERT (SELECT provider = 'slack' AND external_target = 'C01' AND error = 'timeout'
            FROM notification_delivery WHERE notification_id = v_notif AND destination_key = 'slack:bind-2:C01'),
         'T30 FAIL: 路由字段应结构化独立,error 只记失败原因';
  RAISE NOTICE 'PASS T30-4: 路由数据结构化(provider/external_target 列),error 只记失败原因';
END $$;

-- ===================== T31:data job 删除/恢复协议(HIGH-5;R4 HIGH-2 扩展:fencing + 实体副作用幂等)=====================
-- R4 协议(与 import-export.md §3.4/§3.8 逐条对应):
--  (a) claim 领取即单调 fencing:lease_seq + 1,worker 记住领取序号;
--  (b) 每批事务先锁 job 行(SELECT … FOR UPDATE)并校验 owner + lease_seq + 未过期,不符即整批拒绝回滚;
--  (c) 行台账先原子占用 row_key(ON CONFLICT DO NOTHING + 预分配 target_id),占用成功者才创建实体;
--  (d) checkpoint/计数/续租与实体同事务推进。旧 worker「复活」提交因 (b) 被拒;合法重放因 (c) 不重复建实体。
DO $$
DECLARE
  v_job    UUID;
  v_owner  TEXT;
  v_seq    BIGINT;
  v_exp    TIMESTAMPTZ;
  v_n      INT;
  v_reject BOOLEAN;
BEGIN
  -- 夹具:导入源附件(clean blob)+ 两行导入作业
  INSERT INTO attachment_blobs (id, workspace_id, content_hash, storage_bucket, storage_key, file_size, scan_status, ref_count)
  VALUES ('14141414-0000-0000-0000-000000000099', '11111111-1111-1111-1111-111111111111', 'sha256:src-t31', 'bkt', 'ws/11/src.csv', 200, 'clean', 1);
  INSERT INTO attachments (id, workspace_id, uploader_id, blob_id, file_name, file_size, upload_status)
  VALUES ('8c8c8c8c-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'cccccccc-0000-0000-0000-000000000001',
          '14141414-0000-0000-0000-000000000099', 'src.csv', 200, 'completed');
  INSERT INTO data_jobs (id, workspace_id, kind, entity_type, format, status, source_attachment_id, source_content_hash,
                         total_rows, requested_by)
  VALUES ('d1d1d1d1-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'import', 'issues', 'csv', 'running',
          '8c8c8c8c-0000-0000-0000-000000000001', 'sha256:src-t31', 2, 'cccccccc-0000-0000-0000-000000000001')
  RETURNING id INTO v_job;

  -- ① 源附件 RESTRICT:作业存续期间删除源附件被拒
  BEGIN
    DELETE FROM attachments WHERE id = '8c8c8c8c-0000-0000-0000-000000000001';
    RAISE EXCEPTION 'T31 FAIL: 作业存续期间源附件删除应被 RESTRICT 拒绝';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T31-1: 源附件 ON DELETE RESTRICT(作业存续期间不可物理删,消除 SET NULL 与 CHECK 互斥)';
  END;

  -- ② worker-1 领取:lease_seq 单调 +1(fencing token),记下领取序号 1
  UPDATE data_jobs SET lease_owner = 'worker-1', lease_seq = lease_seq + 1,
                       lease_expires_at = now() + interval '5 minutes', started_at = now()
   WHERE id = v_job;
  ASSERT (SELECT lease_seq = 1 AND lease_owner = 'worker-1' FROM data_jobs WHERE id = v_job),
         'T31 FAIL: 领取应使 lease_seq 单调递增至 1';

  -- ③ worker-1 批 1(行 1 → 真实 issue WEB-101):锁 job 校验 fencing → 原子占用 row_key + 预分配 target_id
  --   → 仅占用成功者创建实体 → 台账/计数/checkpoint/续租同事务推进
  BEGIN
    SELECT lease_owner, lease_seq, lease_expires_at INTO v_owner, v_seq, v_exp
      FROM data_jobs WHERE id = v_job FOR UPDATE;
    ASSERT v_owner = 'worker-1' AND v_seq = 1 AND v_exp > now(), 'T31 FAIL: worker-1 批 1 fencing 应校验通过';
    INSERT INTO data_job_rows (workspace_id, job_id, row_number, row_key, status, target_type, target_id)
    VALUES ('11111111-1111-1111-1111-111111111111', v_job, 1, 'row:1:h1', 'pending', 'issue',
            '99999999-0000-0000-0000-000000000031')
    ON CONFLICT (job_id, row_key) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    ASSERT v_n = 1, 'T31 FAIL: 行 1 首次占用应成功';
    INSERT INTO issues (id, workspace_id, project_id, identifier_namespace_key, number, identifier, title, status_id, state_category)
    VALUES ('99999999-0000-0000-0000-000000000031', '11111111-1111-1111-1111-111111111111',
            'dddddddd-0000-0000-0000-000000000001', 'WEB', 101, 'WEB-101', '导入行 1',
            'eeeeeeee-0000-0000-0000-000000000001', 'todo');
    UPDATE data_job_rows SET status = 'created' WHERE job_id = v_job AND row_key = 'row:1:h1';
    UPDATE data_jobs SET checkpoint = '{"last_committed_batch": 1, "last_row_key": "row:1:h1", "batch_size": 1}'::jsonb,
                         succeeded_rows = succeeded_rows + 1,
                         lease_expires_at = now() + interval '5 minutes'
     WHERE id = v_job;
  END;
  ASSERT (SELECT COUNT(*) = 1 FROM issues WHERE id = '99999999-0000-0000-0000-000000000031'),
         'T31 FAIL: 批 1 应创建真实 issue WEB-101';
  RAISE NOTICE 'PASS T31-2: worker-1 批事务(fencing 校验 → row_key 原子占用 + 预分配 target_id → 建实体 → 同事务推进 checkpoint/计数)';

  -- ④ worker-1 卡死:租约过期 → reaper 回收(置空 owner,不回退计数)→ worker-2 领取(lease_seq +1 = 2)
  UPDATE data_jobs SET lease_expires_at = now() - interval '1 minute' WHERE id = v_job;
  UPDATE data_jobs SET lease_owner = NULL WHERE id = v_job AND status = 'running' AND lease_expires_at < now();
  ASSERT (SELECT lease_owner IS NULL AND status = 'running' AND succeeded_rows = 1
            AND checkpoint->>'last_committed_batch' = '1' FROM data_jobs WHERE id = v_job),
         'T31 FAIL: reaper 回收租约且计数/checkpoint 不回退';
  UPDATE data_jobs SET lease_owner = 'worker-2', lease_seq = lease_seq + 1,
                       lease_expires_at = now() + interval '5 minutes',
                       checkpoint = checkpoint || '{"resumed_count": 1}'::jsonb
   WHERE id = v_job;
  ASSERT (SELECT lease_seq = 2 AND lease_owner = 'worker-2' FROM data_jobs WHERE id = v_job),
         'T31 FAIL: worker-2 领取后 lease_seq 应为 2';
  RAISE NOTICE 'PASS T31-3: 租约过期回收 + 新 worker 领取(lease_seq 2);checkpoint 保留,续跑不重跑已提交批';

  -- ⑤ **过期旧 worker「复活」提交批 2(持过期 fencing token seq=1)→ 整批拒绝回滚,不产生重复实体**
  v_reject := FALSE;
  BEGIN
    BEGIN
      SELECT lease_owner, lease_seq, lease_expires_at INTO v_owner, v_seq, v_exp
        FROM data_jobs WHERE id = v_job FOR UPDATE;
      IF NOT (v_owner = 'worker-1' AND v_seq = 1 AND v_exp > now()) THEN
        RAISE EXCEPTION 'stale_lease: 过期 worker 的批提交被 fencing 拒绝';
      END IF;
      -- 下述写入在真实协议中不会到达(fencing 先于副作用);此处证明即便到达亦随事务回滚
      INSERT INTO issues (id, workspace_id, project_id, identifier_namespace_key, number, identifier, title, status_id, state_category)
      VALUES ('99999999-0000-0000-0000-000000000033', '11111111-1111-1111-1111-111111111111',
              'dddddddd-0000-0000-0000-000000000001', 'WEB', 103, 'WEB-103', '陈旧 worker 的重复实体',
              'eeeeeeee-0000-0000-0000-000000000001', 'todo');
    EXCEPTION WHEN raise_exception THEN
      v_reject := (SQLERRM LIKE '%stale_lease%');
    END;
  END;
  ASSERT v_reject, 'T31 FAIL: 过期 worker-1(持 seq=1)的批提交应被 fencing(owner/seq/过期校验)拒绝';
  ASSERT NOT EXISTS (SELECT 1 FROM issues WHERE id = '99999999-0000-0000-0000-000000000033'),
         'T31 FAIL: 被拒批事务应整体回滚(不产生重复实体)';
  ASSERT (SELECT succeeded_rows = 1 AND checkpoint->>'last_committed_batch' = '1' FROM data_jobs WHERE id = v_job),
         'T31 FAIL: 被拒批不得推进计数/checkpoint';
  RAISE NOTICE 'PASS T31-4: 过期旧 worker 重新提交被 fencing 拒绝并整批回锁(单调 lease_seq 杜绝双 worker 并发提交)';

  -- ⑥ worker-2 合法重放批 1(已提交行):row_key 占用冲突 → 跳过实体创建,台账 target_id 不变(幂等)
  BEGIN
    SELECT lease_owner, lease_seq INTO v_owner, v_seq FROM data_jobs WHERE id = v_job FOR UPDATE;
    ASSERT v_owner = 'worker-2' AND v_seq = 2, 'T31 FAIL: worker-2 fencing 应通过';
    INSERT INTO data_job_rows (workspace_id, job_id, row_number, row_key, status, target_type, target_id)
    VALUES ('11111111-1111-1111-1111-111111111111', v_job, 1, 'row:1:h1', 'pending', 'issue',
            '99999999-0000-0000-0000-000000000099')   -- 故意不同的预分配 id:占用冲突 → 不建实体、不覆盖 target
    ON CONFLICT (job_id, row_key) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    ASSERT v_n = 0, 'T31 FAIL: 已提交行的 row_key 占用应冲突(0 行)';
  END;
  ASSERT (SELECT COUNT(*) = 1 FROM issues WHERE identifier = 'WEB-101'),
         'T31 FAIL: 重放已提交批不得重复创建 issue';
  ASSERT (SELECT target_id = '99999999-0000-0000-0000-000000000031' FROM data_job_rows WHERE job_id = v_job AND row_key = 'row:1:h1'),
         'T31 FAIL: 重放不得覆盖已落库的 target_id';
  RAISE NOTICE 'PASS T31-5: 合法重放经 row_key 原子占用冲突跳过实体创建(重放已提交批次 = 幂等)';

  -- ⑦ worker-2 批 2(行 2 → 真实 issue WEB-102):fencing 通过 → 占用成功 → 建实体 → 推进 checkpoint/计数
  BEGIN
    SELECT lease_owner, lease_seq, lease_expires_at INTO v_owner, v_seq, v_exp
      FROM data_jobs WHERE id = v_job FOR UPDATE;
    ASSERT v_owner = 'worker-2' AND v_seq = 2 AND v_exp > now(), 'T31 FAIL: worker-2 批 2 fencing 应通过';
    INSERT INTO data_job_rows (workspace_id, job_id, row_number, row_key, status, target_type, target_id)
    VALUES ('11111111-1111-1111-1111-111111111111', v_job, 2, 'row:2:h2', 'pending', 'issue',
            '99999999-0000-0000-0000-000000000032')
    ON CONFLICT (job_id, row_key) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    ASSERT v_n = 1, 'T31 FAIL: 行 2 占用应成功';
    INSERT INTO issues (id, workspace_id, project_id, identifier_namespace_key, number, identifier, title, status_id, state_category)
    VALUES ('99999999-0000-0000-0000-000000000032', '11111111-1111-1111-1111-111111111111',
            'dddddddd-0000-0000-0000-000000000001', 'WEB', 102, 'WEB-102', '导入行 2',
            'eeeeeeee-0000-0000-0000-000000000001', 'todo');
    UPDATE data_job_rows SET status = 'created' WHERE job_id = v_job AND row_key = 'row:2:h2';
    UPDATE data_jobs SET checkpoint = '{"last_committed_batch": 2, "last_row_key": "row:2:h2", "batch_size": 1, "resumed_count": 1}'::jsonb,
                         succeeded_rows = succeeded_rows + 1,
                         lease_expires_at = now() + interval '5 minutes'
     WHERE id = v_job;
  END;

  -- ⑧ 终局对账:真实 issue 最终恰每行一条,计数/checkpoint/台账三方一致
  ASSERT (SELECT COUNT(*) = 1 FROM issues WHERE identifier = 'WEB-101')
     AND (SELECT COUNT(*) = 1 FROM issues WHERE identifier = 'WEB-102'),
         'T31 FAIL: 两导入行应各恰有一条真实 issue(无重复、无丢失)';
  ASSERT (SELECT COUNT(*) = 2 FROM data_job_rows WHERE job_id = v_job AND status = 'created'),
         'T31 FAIL: 台账应为两条 created';
  ASSERT (SELECT succeeded_rows = 2 AND checkpoint->>'last_committed_batch' = '2'
            FROM data_jobs WHERE id = v_job),
         'T31 FAIL: succeeded_rows/checkpoint 应与台账、实体一致';
  ASSERT (SELECT COUNT(*) = (SELECT COUNT(DISTINCT target_id) FROM data_job_rows WHERE job_id = v_job)
            FROM data_job_rows WHERE job_id = v_job),
         'T31 FAIL: 台账 target_id 应与实体一一对应';
  RAISE NOTICE 'PASS T31-6: 崩溃/复活/重放后真实 issue 每行恰一条,计数/checkpoint/台账一致(实体副作用幂等)';

  -- ⑨ 行台账 CHECK:created 必带 target;failed 必带 error
  BEGIN
    INSERT INTO data_job_rows (workspace_id, job_id, row_number, row_key, status)
    VALUES ('11111111-1111-1111-1111-111111111111', v_job, 3, 'row:3:h3', 'created');
    RAISE EXCEPTION 'T31 FAIL: created 行缺 target 未被 CHECK 拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T31-7: 台账行 CHECK(created/updated 必带 target;failed 必带 error)';
  END;

  -- 清理:作业级联删台账;测试 issue 删除;源附件在作业删除后可删
  DELETE FROM data_jobs WHERE id = v_job;
  DELETE FROM issues WHERE id IN ('99999999-0000-0000-0000-000000000031', '99999999-0000-0000-0000-000000000032');
  DELETE FROM attachments WHERE id = '8c8c8c8c-0000-0000-0000-000000000001';
  RAISE NOTICE 'PASS T31-8: data_jobs 删除级联 data_job_rows;作业删除后源附件 RESTRICT 解除';
END $$;

-- ===================== T32:偏好真源 + locale 单一权威(HIGH-7;R4 HIGH-3 扩展:模型/响应无双真源)=====================
DO $$
DECLARE
  v_ws UUID := '11111111-1111-1111-1111-111111111111';
BEGIN
  -- ① users.settings 真源存在且可写(locale/theme)
  UPDATE users SET settings = '{"locale":"zh-CN","theme":"dark"}'::jsonb, timezone = 'Asia/Shanghai'
   WHERE id = 'aaaaaaaa-0000-0000-0000-000000000001';
  ASSERT (SELECT settings->>'locale' = 'zh-CN' AND settings->>'theme' = 'dark' AND timezone = 'Asia/Shanghai'
            FROM users WHERE id = 'aaaaaaaa-0000-0000-0000-000000000001'),
         'T32 FAIL: users.settings(locale/theme)与 timezone 应可写可回读';
  RAISE NOTICE 'PASS T32-1: users.settings locale/theme 真源列存在(PATCH /api/v1/users/me 的落库字段)';

  -- ② workspace locale 单一真源:settings.default_locale 默认 en;default_language 列已不存在
  ASSERT (SELECT settings->>'default_locale' = 'en' FROM workspaces WHERE id = v_ws),
         'T32 FAIL: workspaces.settings.default_locale 默认应为 en(与 i18n.md 一致)';
  ASSERT NOT EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'workspaces' AND column_name = 'default_language'),
         'T32 FAIL: default_language 双真源列应已删除(只迁移/弃用,不长期双写)';
  RAISE NOTICE 'PASS T32-2: workspace locale 唯一真源 settings.default_locale(默认 en);default_language 列已删';

  -- ③ R4(HIGH-3):default_locale 经 settings 按键浅合并写入并可回读(响应只返回 settings.default_locale,
  --    无任何顶层 default_language 字段;非法 locale/timezone 的错误码 422 unsupported_locale /
  --    422 invalid_timezone 属 API 层校验,与 auth.md §3.1 canonical 一致,本脚本验证模型层单一真源)
  UPDATE workspaces SET settings = settings || '{"default_locale":"zh-CN"}'::jsonb WHERE id = v_ws;
  ASSERT (SELECT settings->>'default_locale' = 'zh-CN' FROM workspaces WHERE id = v_ws),
         'T32 FAIL: settings.default_locale 应可经浅合并写入并回读';
  ASSERT NOT EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'workspaces' AND column_name IN ('default_language','language','locale')),
         'T32 FAIL: workspaces 不得存在任何顶层 locale 列(响应只返回 settings.default_locale)';
  RAISE NOTICE 'PASS T32-3: workspace locale 写入只经 settings.default_locale(无旧列、无双写,错误码与 auth canonical 对齐)';
END $$;

-- ----------------------------------------------------------------------------
-- R4 辅助函数(HIGH-6:execution 指标统一可见性 scope,workload-B / agent stats / workspace dashboard 共用;
-- R5 HIGH-3:聚合落地的权威形态为 analytics.md §2.3.1 的 visible_executions 统一 CTE——本函数是该谓词的
-- 逐执行布尔形态(单行判定/结构负向用),两者语义逐条等价;T33 ⑥–⑨ 以同一聚合 SQL(内联 CTE)对
-- 普通成员/项目成员/private agent owner/admin 断言最终统计值,不再只测本 helper)
-- ----------------------------------------------------------------------------
-- 判定一次执行对某成员是否可见(两层串联):
--   ① **agent 可见性先行**:private agent 的运行统计仅其 owner 与 admin/owner 角色可见;
--   ② **关联 issue 继承项目可见性**:execution 关联 issue 时按 issue 当前所属 project 的可见性过滤
--      (private 项目 = 项目成员/admin/owner 可见);**无 issue 的执行(manual/chat/integration)归属 agent**,
--      经 ① 即可见,不携带任何项目侧信道(普通成员无法经执行计数/成本推断不可见 private project 活动)。
-- 缓存键协同:execution 类指标(工作区仪表盘 agent 统计区 / agent stats / workload 执行部分)的
-- analytics_snapshots.scope_key 在 admin/owner 全量时为 'ws_admin',普通成员为
-- 'exec:p<sha256(可见项目 id 排序)>:a<sha256(可见 agent id 排序)>'——跨权限物理分行、绝不共享(§2.5 R4)。
CREATE OR REPLACE FUNCTION analytics_exec_visible_to(p_exec UUID, p_member UUID) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1
      FROM task_executions e
      JOIN agents  a ON a.id = e.agent_id AND a.workspace_id = e.workspace_id
      JOIN members m ON m.id = p_member   AND m.workspace_id = e.workspace_id
      LEFT JOIN issues   i ON i.id = e.issue_id   AND i.workspace_id = e.workspace_id
      LEFT JOIN projects p ON p.id = i.project_id AND p.workspace_id = i.workspace_id
     WHERE e.id = p_exec
       -- ① agent 可见性(private 仅 owner/admin)
       AND (a.visibility = 'workspace'
            OR (a.visibility = 'private'
                AND (a.owner_user_id = m.user_id OR m.role IN ('owner','admin'))))
       -- ② 关联 issue 继承项目可见性;无 issue 的执行归属 agent(无项目侧信道)
       AND (i.id IS NULL
            OR p.id IS NULL
            OR p.visibility = 'public'
            OR m.role IN ('owner','admin')
            OR EXISTS (SELECT 1 FROM project_members pm
                        WHERE pm.workspace_id = e.workspace_id AND pm.project_id = p.id
                          AND pm.member_id = m.id)
            OR EXISTS (SELECT 1 FROM member_project_access mx
                        WHERE mx.workspace_id = e.workspace_id AND mx.project_id = p.id
                          AND mx.member_id = m.id))
  )
$$;

-- ===================== T33:Analytics 可见性缓存键 + 当前归属口径(HIGH-8;R4 HIGH-6 扩展:execution 可见性 scope;R5 HIGH-3 扩展:同一聚合 SQL 真实统计值断言)=====================
DO $$
DECLARE
  v_ws UUID := '11111111-1111-1111-1111-111111111111';
  v_cte TEXT;            -- R5:visible_executions 统一 CTE(analytics.md §2.3.1 权威构件,四段聚合 SQL 逐字复用)
  v_sql TEXT;
  v_a BIGINT; v_b BIGINT; v_c BIGINT;
  v_rate NUMERIC; v_tokens BIGINT; v_runs BIGINT;
BEGIN
  -- ① 同指标同维度、不同可见性集合 → scope_key 不同可并存(跨权限不共享)
  INSERT INTO analytics_snapshots (workspace_id, metric_key, scope_key, dimensions, window_start, window_end, value)
  VALUES (v_ws, 'throughput', 'ws_admin', '{"granularity":"day","calendar_timezone":"UTC"}',
          '2026-07-01', '2026-07-02', '{"created": 5, "completed": 3}'),
         (v_ws, 'throughput', 'projects:7f2a', '{"granularity":"day","calendar_timezone":"UTC"}',
          '2026-07-01', '2026-07-02', '{"created": 2, "completed": 1}');
  ASSERT (SELECT COUNT(*) = 2 FROM analytics_snapshots
           WHERE workspace_id = v_ws AND metric_key = 'throughput'
             AND dim_hash = md5('{"granularity":"day","calendar_timezone":"UTC"}'::jsonb::text)),
         'T33 FAIL: 不同 scope_key 的快照应并存(可见性版本纳入缓存键)';
  RAISE NOTICE 'PASS T33-1: analytics_snapshots.scope_key 纳入缓存唯一键(跨权限缓存不共享)';

  -- ② 同 scope_key 同维度同窗覆盖式刷新(唯一键)
  BEGIN
    INSERT INTO analytics_snapshots (workspace_id, metric_key, scope_key, dimensions, window_start, window_end, value)
    VALUES (v_ws, 'throughput', 'ws_admin', '{"granularity":"day","calendar_timezone":"UTC"}',
            '2026-07-01', '2026-07-02', '{"created": 9, "completed": 9}');
    RAISE EXCEPTION 'T33 FAIL: 同 (ws, metric, scope, dim, 窗) 重复快照未被唯一键拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T33-2: 同权限同维度同窗仅一份快照(覆盖式刷新语义)';
  END;

  -- ③ calendar_timezone 分桶:不同时区入维度 → 不同 dim_hash(时区切换后边界随变,不错位)
  INSERT INTO analytics_snapshots (workspace_id, metric_key, scope_key, dimensions, window_start, window_end, value)
  VALUES (v_ws, 'throughput', 'ws_admin', '{"granularity":"day","calendar_timezone":"Asia/Shanghai"}',
          '2026-06-30T16:00:00Z', '2026-07-01T16:00:00Z', '{"created": 1}');
  ASSERT (SELECT dim_hash <> md5('{"granularity":"day","calendar_timezone":"UTC"}'::jsonb::text)
            FROM analytics_snapshots
           WHERE workspace_id = v_ws AND dimensions->>'calendar_timezone' = 'Asia/Shanghai' LIMIT 1),
         'T33 FAIL: 不同 calendar_timezone 应产生不同 dim_hash(缓存不跨时区共享)';
  RAISE NOTICE 'PASS T33-3: calendar_timezone 入维度指纹(本地日历分桶,标签与边界一致,建议-3)';

  -- ④ R4(HIGH-6):execution 指标统一可见性 scope——私有项目执行与 private agent 的负向测试
  -- 夹具:私有项目 SEC + 其 issue 上的执行 e-priv;无 issue 的 manual 执行 e-manual(workspace 可见 agent);
  --       private agent 及其 manual 执行 e-pagent(private agent 的 owner 为 u2);两名人类成员 u2/u3
  --       (T18 已物理删除种子成员 u2/u3,此处重建本测试专用成员行)
  INSERT INTO projects (id, workspace_id, name, key, visibility) VALUES
    ('dddddddd-0000-0000-0000-000000000003', v_ws, 'Sec 项目', 'SEC', 'private');
  INSERT INTO identifier_prefix_registry (workspace_id, key, kind, project_id) VALUES
    (v_ws, 'SEC', 'project', 'dddddddd-0000-0000-0000-000000000003');
  INSERT INTO issues (id, workspace_id, project_id, identifier_namespace_key, number, identifier, title, status_id, state_category)
  VALUES ('99999999-0000-0000-0000-000000000041', v_ws, 'dddddddd-0000-0000-0000-000000000003', 'SEC', 1, 'SEC-1', '私有项目 issue',
          'eeeeeeee-0000-0000-0000-000000000001', 'todo');
  INSERT INTO agents (id, workspace_id, name, owner_user_id, visibility) VALUES
    ('bbbbbbbb-0000-0000-0000-000000000003', v_ws, 'agent-priv', 'aaaaaaaa-0000-0000-0000-000000000002', 'private');
  INSERT INTO members (id, workspace_id, member_type, user_id, role) VALUES
    ('cccccccc-0000-0000-0000-00000000000d', v_ws, 'human', 'aaaaaaaa-0000-0000-0000-000000000002', 'member'),  -- u2:private agent 的 owner
    ('cccccccc-0000-0000-0000-00000000000e', v_ws, 'human', 'aaaaaaaa-0000-0000-0000-000000000003', 'member');  -- u3:普通成员
  INSERT INTO task_executions (id, workspace_id, agent_id, issue_id, trigger, status)
  VALUES ('e2e2e2e2-0000-0000-0000-000000000001', v_ws, 'bbbbbbbb-0000-0000-0000-000000000001',
          '99999999-0000-0000-0000-000000000041', 'assign', 'completed'),        -- 关联私有项目 issue
         ('e2e2e2e2-0000-0000-0000-000000000002', v_ws, 'bbbbbbbb-0000-0000-0000-000000000001',
          NULL, 'manual', 'completed'),                                          -- 无 issue:归属 agent
         ('e2e2e2e2-0000-0000-0000-000000000003', v_ws, 'bbbbbbbb-0000-0000-0000-000000000003',
          NULL, 'manual', 'completed');                                          -- private agent 的执行
  -- admin/owner(owner 成员 cccccccc-...-0001):全量可见(ws_admin 口径)
  ASSERT (SELECT COUNT(*) = 3 FROM task_executions e
           WHERE e.id IN ('e2e2e2e2-0000-0000-0000-000000000001','e2e2e2e2-0000-0000-0000-000000000002',
                          'e2e2e2e2-0000-0000-0000-000000000003')
             AND analytics_exec_visible_to(e.id, 'cccccccc-0000-0000-0000-000000000001')),
         'T33 FAIL: admin/owner 应见全工作区执行(含私有项目与 private agent)';
  -- 普通成员 u3(cccccccc-...-000e,非私有项目成员、非 private agent owner):仅 e-manual 可见
  ASSERT analytics_exec_visible_to('e2e2e2e2-0000-0000-0000-000000000002', 'cccccccc-0000-0000-0000-00000000000e')
     AND NOT analytics_exec_visible_to('e2e2e2e2-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-00000000000e')
     AND NOT analytics_exec_visible_to('e2e2e2e2-0000-0000-0000-000000000003', 'cccccccc-0000-0000-0000-00000000000e'),
         'T33 FAIL: 普通成员不得经执行统计推断不可见私有项目活动,private agent 执行先过 agent 可见性';
  -- private agent 的 owner(u2,cccccccc-...-000d):可见自家 private agent 执行,仍不可见私有项目执行
  ASSERT analytics_exec_visible_to('e2e2e2e2-0000-0000-0000-000000000003', 'cccccccc-0000-0000-0000-00000000000d')
     AND analytics_exec_visible_to('e2e2e2e2-0000-0000-0000-000000000002', 'cccccccc-0000-0000-0000-00000000000d')
     AND NOT analytics_exec_visible_to('e2e2e2e2-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-00000000000d'),
         'T33 FAIL: private agent owner 可见自家 agent 执行;项目可见性独立于 agent 可见性';
  RAISE NOTICE 'PASS T33-4: execution 统一可见性 scope(关联 issue 继承项目可见性;无 issue 归属 agent;private agent 先行)';

  -- ⑤ R4(HIGH-6):execution 类指标缓存键纳入同一 scope——'ws_admin' 与 'exec:p<hash>:a<hash>' 物理分行
  INSERT INTO analytics_snapshots (workspace_id, metric_key, scope_key, dimensions, window_start, window_end, value)
  VALUES (v_ws, 'agent_stats', 'ws_admin', '{"agent_id":"bbbbbbbb-0000-0000-0000-000000000001"}',
          '2026-07-10', '2026-07-11', '{"executions": 3}'),
         (v_ws, 'agent_stats', 'exec:p3f8a:a91c2', '{"agent_id":"bbbbbbbb-0000-0000-0000-000000000001"}',
          '2026-07-10', '2026-07-11', '{"executions": 1}');
  ASSERT (SELECT COUNT(*) = 2 FROM analytics_snapshots
           WHERE workspace_id = v_ws AND metric_key = 'agent_stats'
             AND dim_hash = md5('{"agent_id":"bbbbbbbb-0000-0000-0000-000000000001"}'::jsonb::text)),
         'T33 FAIL: execution 指标不同可见性 scope 的快照应物理分行(ws_admin 与成员 scope 绝不共享)';
  RAISE NOTICE 'PASS T33-5: execution 指标缓存键纳入统一可见性 scope(exec:p<项目集>:a<agent 集>),跨权限缓存不共享';

  -- ⑥–⑨ R5(HIGH-3):**同一权威聚合 SQL 的真实统计值断言**——四段落地 SQL(workload-B / agent 主统计 /
  --   retry 子查询 / token 聚合)一律内联 §2.3.1 visible_executions 统一 CTE;以同一 SQL 文本(仅代入
  --   请求者参数)对 普通成员 u3 / 项目成员 u4 / private agent owner u2 / admin 四类请求者断言最终统计值。
  -- 夹具扩展:u4 = 私有项目 SEC 的成员(项目成员 persona);请求者 user_id/role 经其成员行解析(同服务层口径)
  -- 前置清理:此前测试(T20 claim 容量 / T21 审批等)在 agent-a1 上遗留的执行行已完成其断言使命,
  -- 先行清除(子表 attempts/approvals 均 ON DELETE CASCADE),保证下述权威聚合在受控夹具集上断言最终值
  DELETE FROM task_executions
   WHERE workspace_id = v_ws AND agent_id = 'bbbbbbbb-0000-0000-0000-000000000001'
     AND id NOT IN ('e2e2e2e2-0000-0000-0000-000000000001',
                    'e2e2e2e2-0000-0000-0000-000000000002',
                    'e2e2e2e2-0000-0000-0000-000000000003');
  INSERT INTO users (id, email, display_name) VALUES
    ('aaaaaaaa-0000-0000-0000-000000000004', 'u4@example.test', 'U4');
  INSERT INTO members (id, workspace_id, member_type, user_id, role) VALUES
    ('cccccccc-0000-0000-0000-00000000000f', v_ws, 'human', 'aaaaaaaa-0000-0000-0000-000000000004', 'member');
  INSERT INTO project_members (workspace_id, project_id, member_id) VALUES
    (v_ws, 'dddddddd-0000-0000-0000-000000000003', 'cccccccc-0000-0000-0000-00000000000f');

  -- 权威构件:visible_executions 统一 CTE($M$ = 请求者成员 id 占位;与 analytics.md §2.3.1 逐条等价)
  v_cte := $cte$
WITH visible_executions AS (
  SELECT e.*
  FROM task_executions e
  JOIN agents a        ON a.id = e.agent_id AND a.workspace_id = e.workspace_id
  LEFT JOIN issues i   ON i.id = e.issue_id AND i.workspace_id = e.workspace_id
  LEFT JOIN projects p ON p.id = i.project_id AND p.workspace_id = i.workspace_id
  WHERE e.workspace_id = '11111111-1111-1111-1111-111111111111'
    AND (a.visibility = 'workspace'
         OR (a.visibility = 'private'
             AND (a.owner_user_id = (SELECT m0.user_id FROM members m0 WHERE m0.id = $M$)
                  OR (SELECT m0.role FROM members m0 WHERE m0.id = $M$) IN ('owner','admin'))))
    AND (i.id IS NULL
         OR p.id IS NULL
         OR p.visibility = 'public'
         OR (SELECT m0.role FROM members m0 WHERE m0.id = $M$) IN ('owner','admin')
         OR EXISTS (SELECT 1 FROM project_members pm
                     WHERE pm.workspace_id = e.workspace_id AND pm.project_id = p.id AND pm.member_id = $M$)
         OR EXISTS (SELECT 1 FROM member_project_access mx
                     WHERE mx.workspace_id = e.workspace_id AND mx.project_id = p.id AND mx.member_id = $M$))
)
$cte$;

  -- ⑥ agent 主统计(§2.3 同一 SQL:executions / succeeded 最终值,逐请求者断言)
  -- 普通成员 u3:私有项目 SEC 的执行 e-0001 与 private agent 的执行 e-0003 均被剔除,agent1 仅余 e-0002
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000e')) ||
    'SELECT COUNT(*), COUNT(*) FILTER (WHERE e.status=''completed'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001''';
  EXECUTE v_sql INTO v_a, v_b;
  ASSERT v_a = 1 AND v_b = 1, 'T33 FAIL: 普通成员 agent 主统计应剔除私有项目执行(仅余无 issue 执行 e-0002)';
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000e')) ||
    'SELECT COUNT(*), COUNT(*) FILTER (WHERE e.status=''completed'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000003''';
  EXECUTE v_sql INTO v_a, v_b;
  ASSERT v_a = 0 AND v_b = 0, 'T33 FAIL: 普通成员对 private agent 的聚合应为空(先过 agent 可见性)';
  -- 项目成员 u4:含私有项目 SEC 执行 e-0001 + 无 issue 执行 e-0002
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000f')) ||
    'SELECT COUNT(*), COUNT(*) FILTER (WHERE e.status=''completed'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001''';
  EXECUTE v_sql INTO v_a, v_b;
  ASSERT v_a = 2 AND v_b = 2, 'T33 FAIL: 项目成员 agent 主统计应含私有项目执行(e-0001 + e-0002)';
  -- private agent owner u2:自家 private agent 执行可见;但不可见私有项目执行(项目可见性独立于 agent 可见性)
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000d')) ||
    'SELECT COUNT(*), COUNT(*) FILTER (WHERE e.status=''completed'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000003''';
  EXECUTE v_sql INTO v_a, v_b;
  ASSERT v_a = 1 AND v_b = 1, 'T33 FAIL: private agent owner 应见自家 agent 执行';
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000d')) ||
    'SELECT COUNT(*), COUNT(*) FILTER (WHERE e.status=''completed'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001''';
  EXECUTE v_sql INTO v_a, v_b;
  ASSERT v_a = 1 AND v_b = 1, 'T33 FAIL: private agent owner 非私有项目成员时仍不得见私有项目执行';
  -- admin/owner:全量(agent1 = e-0001 + e-0002;agent3 = e-0003)
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-000000000001')) ||
    'SELECT COUNT(*), COUNT(*) FILTER (WHERE e.status=''completed'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001''';
  EXECUTE v_sql INTO v_a, v_b;
  ASSERT v_a = 2 AND v_b = 2, 'T33 FAIL: admin 应见全工作区 agent1 执行(含私有项目)';
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-000000000001')) ||
    'SELECT COUNT(*), COUNT(*) FILTER (WHERE e.status=''completed'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000003''';
  EXECUTE v_sql INTO v_a, v_b;
  ASSERT v_a = 1 AND v_b = 1, 'T33 FAIL: admin 应见 private agent 执行';
  RAISE NOTICE 'PASS T33-6: agent 主统计权威 SQL(内联 visible_executions CTE)逐请求者最终统计值断言(u3/u4/u2/admin)';

  -- ⑦ workload-B(§2.2.4 同一 SQL:在途 running / queued / awaiting_approval 计数,堵执行计数侧信道)
  INSERT INTO task_executions (id, workspace_id, agent_id, issue_id, trigger, status)
  VALUES ('e2e2e2e2-0000-0000-0000-000000000004', v_ws, 'bbbbbbbb-0000-0000-0000-000000000001',
          '99999999-0000-0000-0000-000000000041', 'assign', 'running'),          -- 私有项目 issue 的在途执行
         ('e2e2e2e2-0000-0000-0000-000000000005', v_ws, 'bbbbbbbb-0000-0000-0000-000000000001',
          NULL, 'manual', 'queued');                                              -- 无 issue 的在途执行(归属 agent)
  -- 普通成员 u3:私有项目在途执行 e-0004 被剔除,仅余 e-0005 queued
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000e')) ||
    'SELECT COUNT(*) FILTER (WHERE e.status IN (''claimed'',''running'',''cancelling'')), COUNT(*) FILTER (WHERE e.status = ''queued''), COUNT(*) FILTER (WHERE e.status = ''awaiting_approval'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001'' AND e.status IN (''queued'',''claimed'',''running'',''cancelling'',''awaiting_approval'')';
  EXECUTE v_sql INTO v_a, v_b, v_c;
  ASSERT v_a = 0 AND v_b = 1 AND v_c = 0, 'T33 FAIL: 普通成员 workload-B 应剔除私有项目在途执行(无法经执行计数推断私有项目活动)';
  -- admin:两条在途均可见
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-000000000001')) ||
    'SELECT COUNT(*) FILTER (WHERE e.status IN (''claimed'',''running'',''cancelling'')), COUNT(*) FILTER (WHERE e.status = ''queued''), COUNT(*) FILTER (WHERE e.status = ''awaiting_approval'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001'' AND e.status IN (''queued'',''claimed'',''running'',''cancelling'',''awaiting_approval'')';
  EXECUTE v_sql INTO v_a, v_b, v_c;
  ASSERT v_a = 1 AND v_b = 1 AND v_c = 0, 'T33 FAIL: admin workload-B 应见全量在途执行';
  -- 项目成员 u4:含私有项目在途执行
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000f')) ||
    'SELECT COUNT(*) FILTER (WHERE e.status IN (''claimed'',''running'',''cancelling'')), COUNT(*) FILTER (WHERE e.status = ''queued''), COUNT(*) FILTER (WHERE e.status = ''awaiting_approval'') FROM visible_executions e WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001'' AND e.status IN (''queued'',''claimed'',''running'',''cancelling'',''awaiting_approval'')';
  EXECUTE v_sql INTO v_a, v_b, v_c;
  ASSERT v_a = 1 AND v_b = 1 AND v_c = 0, 'T33 FAIL: 项目成员 workload-B 应含私有项目在途执行';
  DELETE FROM task_executions WHERE id IN ('e2e2e2e2-0000-0000-0000-000000000004', 'e2e2e2e2-0000-0000-0000-000000000005');
  RAISE NOTICE 'PASS T33-7: workload-B 权威 SQL(内联 CTE)在途计数逐请求者断言(私有项目在途执行对普通成员剔除)';

  -- ⑧ retry 子查询(§2.3 同一 SQL:attempts 关联先过 CTE,retry_rate 最终值)
  INSERT INTO execution_attempts (workspace_id, execution_id, attempt_number, status)
  VALUES (v_ws, 'e2e2e2e2-0000-0000-0000-000000000001', 1, 'completed'),
         (v_ws, 'e2e2e2e2-0000-0000-0000-000000000001', 2, 'completed'),   -- e-0001 重试 1 次(n=2)
         (v_ws, 'e2e2e2e2-0000-0000-0000-000000000002', 1, 'completed');   -- e-0002 无重试(n=1)
  -- 普通成员 u3:仅余 e-0002(n=1)→ retry_rate = 0(私有项目执行 e-0001 的重试不泄露)
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000e')) ||
    'SELECT COALESCE(ROUND(COUNT(*) FILTER (WHERE n > 1) * 1.0 / NULLIF(COUNT(*),0), 4), 0) FROM (SELECT e.id, COUNT(att.id) AS n FROM visible_executions e LEFT JOIN execution_attempts att ON att.execution_id = e.id AND att.workspace_id = e.workspace_id WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001'' GROUP BY e.id) r';
  EXECUTE v_sql INTO v_rate;
  ASSERT v_rate = 0, 'T33 FAIL: 普通成员 retry_rate 应不含私有项目执行的重试(仅 e-0002,n=1)';
  -- admin:e-0001(n=2)+ e-0002(n=1)→ 1/2 = 0.5
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-000000000001')) ||
    'SELECT COALESCE(ROUND(COUNT(*) FILTER (WHERE n > 1) * 1.0 / NULLIF(COUNT(*),0), 4), 0) FROM (SELECT e.id, COUNT(att.id) AS n FROM visible_executions e LEFT JOIN execution_attempts att ON att.execution_id = e.id AND att.workspace_id = e.workspace_id WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001'' GROUP BY e.id) r';
  EXECUTE v_sql INTO v_rate;
  ASSERT v_rate = 0.5, 'T33 FAIL: admin retry_rate 应含私有项目执行重试(1/2)';
  -- 项目成员 u4:同 admin 口径(1/2)
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000f')) ||
    'SELECT COALESCE(ROUND(COUNT(*) FILTER (WHERE n > 1) * 1.0 / NULLIF(COUNT(*),0), 4), 0) FROM (SELECT e.id, COUNT(att.id) AS n FROM visible_executions e LEFT JOIN execution_attempts att ON att.execution_id = e.id AND att.workspace_id = e.workspace_id WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001'' GROUP BY e.id) r';
  EXECUTE v_sql INTO v_rate;
  ASSERT v_rate = 0.5, 'T33 FAIL: 项目成员 retry_rate 应含私有项目执行重试(1/2)';
  RAISE NOTICE 'PASS T33-8: retry 权威 SQL(attempts 关联先过 CTE)逐请求者最终 retry_rate 断言';

  -- ⑨ token 聚合(§2.3 同一 SQL:autopilot_runs 关联先过 CTE,total_tokens 最终值)
  INSERT INTO autopilots (id, workspace_id, name, trigger_type, created_by) VALUES
    ('55555555-0000-0000-0000-000000000001', v_ws, 'T33-ap', 'schedule', 'cccccccc-0000-0000-0000-000000000001');
  INSERT INTO autopilot_runs (autopilot_id, workspace_id, trigger_type, execution_id, status, started_at,
                              prompt_tokens, completion_tokens)
  VALUES ('55555555-0000-0000-0000-000000000001', v_ws, 'schedule', 'e2e2e2e2-0000-0000-0000-000000000001',
          'succeeded', '2026-07-20 10:00:00+00', 600, 300),                  -- e-0001(私有项目)token 900
         ('55555555-0000-0000-0000-000000000001', v_ws, 'schedule', 'e2e2e2e2-0000-0000-0000-000000000002',
          'succeeded', '2026-07-20 11:00:00+00', 60, 40);                    -- e-0002(无 issue)token 100
  -- 普通成员 u3:仅 e-0002 的 token(私有项目 token 成本不泄露)
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000e')) ||
    'SELECT COALESCE(SUM(r.total_tokens),0), COUNT(r.id) FROM autopilot_runs r JOIN visible_executions e ON e.id = r.execution_id AND e.workspace_id = r.workspace_id WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001'' AND r.started_at >= ''2026-07-01'' AND r.started_at < ''2026-08-01''';
  EXECUTE v_sql INTO v_tokens, v_runs;
  ASSERT v_tokens = 100 AND v_runs = 1, 'T33 FAIL: 普通成员 token 聚合应剔除私有项目执行 token(仅 e-0002 的 100)';
  -- admin:两条执行的 token 全量(1000)
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-000000000001')) ||
    'SELECT COALESCE(SUM(r.total_tokens),0), COUNT(r.id) FROM autopilot_runs r JOIN visible_executions e ON e.id = r.execution_id AND e.workspace_id = r.workspace_id WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001'' AND r.started_at >= ''2026-07-01'' AND r.started_at < ''2026-08-01''';
  EXECUTE v_sql INTO v_tokens, v_runs;
  ASSERT v_tokens = 1000 AND v_runs = 2, 'T33 FAIL: admin token 聚合应见全量(900 + 100)';
  -- 项目成员 u4:含私有项目执行 token(1000)
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000f')) ||
    'SELECT COALESCE(SUM(r.total_tokens),0), COUNT(r.id) FROM autopilot_runs r JOIN visible_executions e ON e.id = r.execution_id AND e.workspace_id = r.workspace_id WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000001'' AND r.started_at >= ''2026-07-01'' AND r.started_at < ''2026-08-01''';
  EXECUTE v_sql INTO v_tokens, v_runs;
  ASSERT v_tokens = 1000 AND v_runs = 2, 'T33 FAIL: 项目成员 token 聚合应含私有项目执行 token';
  -- private agent owner u2 对 agent3:无 token 数据(0,诚实口径)
  v_sql := replace(v_cte, '$M$', quote_literal('cccccccc-0000-0000-0000-00000000000d')) ||
    'SELECT COALESCE(SUM(r.total_tokens),0), COUNT(r.id) FROM autopilot_runs r JOIN visible_executions e ON e.id = r.execution_id AND e.workspace_id = r.workspace_id WHERE e.agent_id = ''bbbbbbbb-0000-0000-0000-000000000003'' AND r.started_at >= ''2026-07-01'' AND r.started_at < ''2026-08-01''';
  EXECUTE v_sql INTO v_tokens, v_runs;
  ASSERT v_tokens = 0 AND v_runs = 0, 'T33 FAIL: private agent owner 对无 token 数据的 agent 聚合应为 0(诚实口径)';
  RAISE NOTICE 'PASS T33-9: token 权威 SQL(autopilot_runs 关联先过 CTE)逐请求者最终 token 值断言';

  -- 清理 R4/R5 夹具(保持后续测试环境干净)
  DELETE FROM autopilot_runs WHERE autopilot_id = '55555555-0000-0000-0000-000000000001';
  DELETE FROM autopilots WHERE id = '55555555-0000-0000-0000-000000000001';
  DELETE FROM execution_attempts WHERE execution_id IN ('e2e2e2e2-0000-0000-0000-000000000001',
                                                        'e2e2e2e2-0000-0000-0000-000000000002');
  DELETE FROM analytics_snapshots WHERE workspace_id = v_ws AND metric_key = 'agent_stats';
  DELETE FROM task_executions WHERE id IN ('e2e2e2e2-0000-0000-0000-000000000001',
                                           'e2e2e2e2-0000-0000-0000-000000000002',
                                           'e2e2e2e2-0000-0000-0000-000000000003');
  DELETE FROM agents WHERE id = 'bbbbbbbb-0000-0000-0000-000000000003';
  DELETE FROM project_members WHERE member_id = 'cccccccc-0000-0000-0000-00000000000f';
  DELETE FROM members WHERE id IN ('cccccccc-0000-0000-0000-00000000000d', 'cccccccc-0000-0000-0000-00000000000e',
                                   'cccccccc-0000-0000-0000-00000000000f');
  DELETE FROM users WHERE id = 'aaaaaaaa-0000-0000-0000-000000000004';
  DELETE FROM issues WHERE id = '99999999-0000-0000-0000-000000000041';
  DELETE FROM identifier_prefix_registry WHERE workspace_id = v_ws AND key = 'SEC';
  DELETE FROM projects WHERE id = 'dddddddd-0000-0000-0000-000000000003';
END $$;

-- ===================== T34:Onboarding 证据与末步判定(HIGH-9;R4 HIGH-4 扩展:四真实场景)=====================
-- R4 四场景(与 onboarding.md §3.5/§3.6 逐条对应):
--   ① 入册播种:人类成员入册事务同事务播种清单 + 五步,步骤 1 即完成;agent 成员不播种;
--   ② 成熟工作区 reconcile:受邀进入成熟工作区(已有 agent 成员/issue/历史执行)→ 建状态全量回查,
--      步骤 2–4 按成员自身历史事实带证据完成,**不永久 pending**;未触发执行的成员步骤 4 保持 pending;
--   ③ 未读不得完成:末步仅由 notification.read 驱动,相关通知未读 → 末步保持 pending,aha 不置位;
--   ④ 错误 trigger member 不得完成:末步严格按 trigger_member_id 完成——读了「他人触发的执行」的 agent 回评
--      通知不得完成本人末步(不给未触发者伪造证据);触发者本人阅读后完成并置 aha。
DO $$
DECLARE
  v_ws       UUID := '22222222-2222-2222-2222-222222222222';
  v_agentm   UUID := 'cccccccc-0000-0000-0000-00000000000a';   -- WS-B 的 agent 成员(agent-b1)
  v_ma       UUID := 'cccccccc-0000-0000-0000-00000000000b';   -- 人类成员 A(u2,触发者)
  v_mb       UUID := 'cccccccc-0000-0000-0000-00000000000c';   -- 人类成员 B(u3,非触发者)
  v_state_a  UUID := 'cdcdcdcd-3333-0000-0000-000000000091';
  v_state_b  UUID := 'cdcdcdcd-3333-0000-0000-000000000092';
  v_exec     UUID := 'abababab-7777-0000-0000-000000000001';
  v_comment  UUID := '66666666-7777-0000-0000-000000000001';
  v_notif_a  UUID := '16161616-7777-0000-0000-000000000001';
  v_notif_b  UUID := '16161616-7777-0000-0000-000000000002';
  v_n        INT;
BEGIN
  -- 夹具:WS-B 成熟化——agent 入册 + 两名人类成员 + 一次 assign 执行(成员 A 分派)+ agent 回评 + 两条收件箱通知
  INSERT INTO members (id, workspace_id, member_type, agent_id, role) VALUES
    (v_agentm, v_ws, 'agent', 'bbbbbbbb-0000-0000-0000-000000000002', 'member');
  INSERT INTO members (id, workspace_id, member_type, user_id, role) VALUES
    (v_ma, v_ws, 'human', 'aaaaaaaa-0000-0000-0000-000000000002', 'member'),
    (v_mb, v_ws, 'human', 'aaaaaaaa-0000-0000-0000-000000000003', 'member');
  INSERT INTO task_executions (id, workspace_id, agent_id, issue_id, trigger, status, finished_at)
  VALUES (v_exec, v_ws, 'bbbbbbbb-0000-0000-0000-000000000002', '99999999-0000-0000-0000-000000000009',
          'assign', 'completed', now());
  INSERT INTO issue_activity (workspace_id, issue_id, actor_member_id, field, new_value)
  VALUES (v_ws, '99999999-0000-0000-0000-000000000009', v_ma, 'assignee_id', '"cccccccc-0000-0000-0000-00000000000a"'::jsonb);
  INSERT INTO comments (id, workspace_id, issue_id, author_kind, author_id, body_markdown)
  VALUES (v_comment, v_ws, '99999999-0000-0000-0000-000000000009', 'member', v_agentm, 'agent 回评:问题已修复');
  INSERT INTO notifications (id, workspace_id, recipient_id, type, priority, comment_id, execution_id, read_at)
  VALUES (v_notif_a, v_ws, v_ma, 'comment_created', 'normal', v_comment, v_exec, NULL),
         (v_notif_b, v_ws, v_mb, 'comment_created', 'normal', v_comment, v_exec, NULL);

  -- ① 入册播种(主路径):人类成员入册事务同事务播种清单 + 五步,步骤 1 即 completed(auto);agent 成员不播种
  INSERT INTO onboarding_states (id, workspace_id, member_id, checklist) VALUES (v_state_a, v_ws, v_ma, 'activation');
  INSERT INTO onboarding_state_steps (workspace_id, state_id, step_key, status)
  VALUES (v_ws, v_state_a, 'create_workspace', 'pending'),
         (v_ws, v_state_a, 'invite_member_or_add_agent', 'pending'),
         (v_ws, v_state_a, 'create_first_issue', 'pending'),
         (v_ws, v_state_a, 'dispatch_or_mention_agent', 'pending'),
         (v_ws, v_state_a, 'see_agent_reply_in_inbox', 'pending');
  UPDATE onboarding_state_steps
     SET status = 'completed', completed_via = 'auto', completed_at = now()
   WHERE workspace_id = v_ws AND state_id = v_state_a AND step_key = 'create_workspace';
  ASSERT (SELECT COUNT(*) = 5 FROM onboarding_state_steps WHERE workspace_id = v_ws AND state_id = v_state_a),
         'T34 FAIL: 入册应播种五步';
  ASSERT (SELECT status = 'completed' FROM onboarding_state_steps
           WHERE workspace_id = v_ws AND state_id = v_state_a AND step_key = 'create_workspace'),
         'T34 FAIL: 步骤 1 应在建状态事务内即完成(工作区既已存在)';
  -- agent 成员不建清单(清单是人类成员的上手路径)
  ASSERT NOT EXISTS (SELECT 1 FROM onboarding_states WHERE workspace_id = v_ws AND member_id = v_agentm),
         'T34 FAIL: agent 成员不应播种清单';
  RAISE NOTICE 'PASS T34-1: 入册事务同事务播种清单 + 五步(步骤 1 即完成);agent 成员不播种';

  -- ② 成熟工作区 reconcile:成员 B 随后入册;对 A/B 建状态全量回查历史事实
  INSERT INTO onboarding_states (id, workspace_id, member_id, checklist) VALUES (v_state_b, v_ws, v_mb, 'activation');
  INSERT INTO onboarding_state_steps (workspace_id, state_id, step_key, status)
  VALUES (v_ws, v_state_b, 'create_workspace', 'pending'),
         (v_ws, v_state_b, 'invite_member_or_add_agent', 'pending'),
         (v_ws, v_state_b, 'create_first_issue', 'pending'),
         (v_ws, v_state_b, 'dispatch_or_mention_agent', 'pending'),
         (v_ws, v_state_b, 'see_agent_reply_in_inbox', 'pending');
  -- reconcile 步骤 1/2/3(工作区级事实:成员已在册、已有 agent 成员、已有 issue)——A/B 同路径
  UPDATE onboarding_state_steps
     SET status = 'completed', completed_via = 'auto', completed_at = now(),
         evidence = jsonb_build_object('member_added_id', v_agentm)
   WHERE workspace_id = v_ws AND state_id IN (v_state_a, v_state_b)
     AND step_key = 'invite_member_or_add_agent' AND status = 'pending'
     AND EXISTS (SELECT 1 FROM members mm WHERE mm.workspace_id = v_ws AND mm.member_type = 'agent');
  UPDATE onboarding_state_steps
     SET status = 'completed', completed_via = 'auto', completed_at = now(),
         evidence = jsonb_build_object('issue_id', '99999999-0000-0000-0000-000000000009')
   WHERE workspace_id = v_ws AND state_id IN (v_state_a, v_state_b)
     AND step_key = 'create_first_issue' AND status = 'pending'
     AND EXISTS (SELECT 1 FROM issues ii WHERE ii.workspace_id = v_ws AND ii.deleted_at IS NULL);
  -- reconcile 步骤 4:严格按成员自身历史——仅「该成员触发过 assign/mention 执行」者完成(经分派留痕)
  UPDATE onboarding_state_steps st
     SET status = 'completed', completed_via = 'auto', completed_at = now(),
         evidence = jsonb_build_object('execution_id', v_exec, 'trigger_member_id', st2.member_id)
    FROM onboarding_states st2
   WHERE st.workspace_id = v_ws AND st.state_id IN (v_state_a, v_state_b)
     AND st.step_key = 'dispatch_or_mention_agent' AND st.status = 'pending'
     AND st2.id = st.state_id
     AND EXISTS (SELECT 1 FROM task_executions e
                  JOIN issue_activity ia ON ia.workspace_id = e.workspace_id AND ia.issue_id = e.issue_id
                   AND ia.field = 'assignee_id' AND ia.actor_member_id = st2.member_id
                  WHERE e.workspace_id = v_ws AND e.trigger IN ('assign','mention'));
  -- A:步骤 2–4 带证据完成;步骤 5 保持 pending(未读通知)
  ASSERT (SELECT COUNT(*) = 4 FROM onboarding_state_steps
           WHERE workspace_id = v_ws AND state_id = v_state_a AND status = 'completed'),
         'T34 FAIL: 成员 A 入册 reconcile 后步骤 1–4 应带证据完成,不再永久 pending';
  ASSERT (SELECT evidence ? 'trigger_member_id' AND evidence->>'trigger_member_id' = v_ma::text
            FROM onboarding_state_steps
           WHERE workspace_id = v_ws AND state_id = v_state_a AND step_key = 'dispatch_or_mention_agent'),
         'T34 FAIL: 步骤 4 evidence 应记 trigger_member_id = 成员 A';
  ASSERT (SELECT status = 'pending' FROM onboarding_state_steps
           WHERE workspace_id = v_ws AND state_id = v_state_a AND step_key = 'see_agent_reply_in_inbox'),
         'T34 FAIL: 成员 A 未读通知,步骤 5 reconcile 后应保持 pending';
  -- B:步骤 2/3 完成;**B 从未触发执行 → 步骤 4 保持 pending(不拿工作区首个执行给未触发者伪造证据)**
  ASSERT (SELECT status = 'pending' FROM onboarding_state_steps
           WHERE workspace_id = v_ws AND state_id = v_state_b AND step_key = 'dispatch_or_mention_agent'),
         'T34 FAIL: 成员 B 未触发过执行,步骤 4 不得按工作区首个执行批量完成';
  RAISE NOTICE 'PASS T34-2: 成熟工作区 reconcile(步骤 2–4 按成员自身历史带证据完成;未触发者步骤 4 保持 pending)';

  -- ③ 未读不得完成:A 的相关通知未读 → 末步完成守卫 0 行(不再凭 completed 执行 + agent 评论批量完成)
  UPDATE onboarding_state_steps
     SET status = 'completed', completed_via = 'auto', completed_at = now()
   WHERE workspace_id = v_ws AND state_id = v_state_a AND step_key = 'see_agent_reply_in_inbox'
     AND status = 'pending'
     AND EXISTS (SELECT 1 FROM notifications n
                  JOIN comments c        ON c.workspace_id = n.workspace_id AND c.id = n.comment_id
                  JOIN members  am       ON am.workspace_id = c.workspace_id AND am.id = c.author_id
                                        AND am.member_type = 'agent'
                  JOIN task_executions e ON e.workspace_id = n.workspace_id AND e.id = n.execution_id
                  WHERE n.workspace_id = v_ws AND n.recipient_id = v_ma AND n.read_at IS NOT NULL
                    AND e.status = 'completed'
                    AND EXISTS (SELECT 1 FROM issue_activity ia
                                 WHERE ia.workspace_id = e.workspace_id AND ia.issue_id = e.issue_id
                                   AND ia.field = 'assignee_id' AND ia.actor_member_id = v_ma));
  ASSERT (SELECT status = 'pending' FROM onboarding_state_steps
           WHERE workspace_id = v_ws AND state_id = v_state_a AND step_key = 'see_agent_reply_in_inbox')
     AND (SELECT aha_reached_at IS NULL FROM onboarding_states WHERE id = v_state_a),
         'T34 FAIL: 通知未读不得完成末步、不得宣告 aha';
  RAISE NOTICE 'PASS T34-3: 未读不得完成——末步仅由 notification.read 驱动(未读 → pending,aha 不置位)';

  -- ④ 错误 trigger member 不得完成:B 读了「A 触发的执行」的 agent 回评通知 → B 的末步守卫仍 0 行
  UPDATE notifications SET read_at = now() WHERE id = v_notif_b;
  UPDATE onboarding_state_steps
     SET status = 'completed', completed_via = 'auto', completed_at = now()
   WHERE workspace_id = v_ws AND state_id = v_state_b AND step_key = 'see_agent_reply_in_inbox'
     AND status = 'pending'
     AND EXISTS (SELECT 1 FROM notifications n
                  JOIN comments c        ON c.workspace_id = n.workspace_id AND c.id = n.comment_id
                  JOIN members  am       ON am.workspace_id = c.workspace_id AND am.id = c.author_id
                                        AND am.member_type = 'agent'
                  JOIN task_executions e ON e.workspace_id = n.workspace_id AND e.id = n.execution_id
                  WHERE n.workspace_id = v_ws AND n.recipient_id = v_mb AND n.read_at IS NOT NULL
                    AND e.status = 'completed'
                    AND EXISTS (SELECT 1 FROM issue_activity ia
                                 WHERE ia.workspace_id = e.workspace_id AND ia.issue_id = e.issue_id
                                   AND ia.field = 'assignee_id' AND ia.actor_member_id = v_mb));
  ASSERT (SELECT status = 'pending' FROM onboarding_state_steps
           WHERE workspace_id = v_ws AND state_id = v_state_b AND step_key = 'see_agent_reply_in_inbox')
     AND (SELECT aha_reached_at IS NULL FROM onboarding_states WHERE id = v_state_b),
         'T34 FAIL: 读了他人触发执行的回评通知不得完成本人末步(不给未触发者伪造证据)';
  -- 触发者 A 本人阅读自己的通知 → A 的末步完成,evidence 持久化四元组 + aha 置位
  UPDATE notifications SET read_at = now() WHERE id = v_notif_a;
  UPDATE onboarding_state_steps
     SET status = 'completed', completed_via = 'auto', completed_at = now(),
         evidence = jsonb_build_object('execution_id', v_exec, 'comment_id', v_comment,
                                       'notification_id', v_notif_a, 'trigger_member_id', v_ma)
   WHERE workspace_id = v_ws AND state_id = v_state_a AND step_key = 'see_agent_reply_in_inbox'
     AND status = 'pending'
     AND EXISTS (SELECT 1 FROM notifications n
                  JOIN comments c        ON c.workspace_id = n.workspace_id AND c.id = n.comment_id
                  JOIN members  am       ON am.workspace_id = c.workspace_id AND am.id = c.author_id
                                        AND am.member_type = 'agent'
                  JOIN task_executions e ON e.workspace_id = n.workspace_id AND e.id = n.execution_id
                  WHERE n.workspace_id = v_ws AND n.recipient_id = v_ma AND n.read_at IS NOT NULL
                    AND e.status = 'completed'
                    AND EXISTS (SELECT 1 FROM issue_activity ia
                                 WHERE ia.workspace_id = e.workspace_id AND ia.issue_id = e.issue_id
                                   AND ia.field = 'assignee_id' AND ia.actor_member_id = v_ma));
  UPDATE onboarding_states SET aha_reached_at = now() WHERE id = v_state_a AND aha_reached_at IS NULL;
  ASSERT (SELECT evidence ?& ARRAY['execution_id','comment_id','notification_id','trigger_member_id']
            FROM onboarding_state_steps
           WHERE workspace_id = v_ws AND state_id = v_state_a AND step_key = 'see_agent_reply_in_inbox'),
         'T34 FAIL: 末步完成应持久化 execution/comment/notification/trigger_member 四元证据';
  ASSERT (SELECT aha_reached_at IS NOT NULL FROM onboarding_states WHERE id = v_state_a)
     AND (SELECT aha_reached_at IS NULL FROM onboarding_states WHERE id = v_state_b),
         'T34 FAIL: aha 仅为触发者 A 置位,B 不因 A 的事实完成';
  RAISE NOTICE 'PASS T34-4: 末步严格按 trigger_member_id 完成(错误 trigger member 不得完成;触发者阅读后完成 + aha)';

  -- ⑤ CHECK 一致性:completed 必有 completed_at(既有约束不被 evidence 破坏)
  ASSERT NOT EXISTS (SELECT 1 FROM onboarding_state_steps
                      WHERE workspace_id = v_ws AND state_id IN (v_state_a, v_state_b)
                        AND (status = 'completed') <> (completed_at IS NOT NULL)),
         'T34 FAIL: 状态与完成时间一致性 CHECK';
  RAISE NOTICE 'PASS T34-5: 步骤状态机 CHECK 与 evidence 共存';
END $$;

-- ===================== 建议-2:is_pinned 快照删除验证 =====================
DO $$
BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'chat_sessions' AND column_name = 'is_pinned'),
         'S2 FAIL: chat_sessions.is_pinned 快照列应已删除(置顶唯一真源为 favorites)';
  INSERT INTO favorites (workspace_id, member_id, target_type, target_id)
  VALUES ('11111111-1111-1111-1111-111111111111', 'cccccccc-0000-0000-0000-000000000001', 'chat_session',
          '44444444-0000-0000-0000-000000000001');
  RAISE NOTICE 'PASS S2: chat_sessions.is_pinned 已删除;会话置顶经 favorites(target_type=chat_session)唯一表达';
END $$;

-- ===================== T36:auth sessions / 设备授权(MES-76 R2-H1/R3-H5/R3-M3)=====================
DO $$
DECLARE
  v_authz UUID;
  v_rows  INT;
  v_user  UUID;
BEGIN
  SELECT id INTO v_user FROM users LIMIT 1;

  -- ① 正例:web 会话 workspace 可为 NULL
  INSERT INTO sessions (id, user_id, token_hash, type, expires_at)
  VALUES ('abababab-0000-0000-0000-000000000001', v_user, 'mesh_rft_hash_web_1', 'web', now() + interval '30 days');
  RAISE NOTICE 'PASS T36-1: web 会话 workspace_id 可为 NULL';

  -- ② 负例:cli 会话必绑工作区(CHECK)
  BEGIN
    INSERT INTO sessions (user_id, token_hash, type, expires_at)
    VALUES (v_user, 'mesh_rft_hash_cli_1', 'cli', now() + interval '30 days');
    RAISE EXCEPTION 'T36 FAIL: cli 会话未绑工作区未被拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T36-2: CHECK 拒绝 cli 会话 workspace_id IS NULL(R2-H1 设备会话必绑)';
  END;

  -- ③ 状态机 CHECK
  BEGIN
    INSERT INTO device_authorizations (device_code_hash, user_code_hash, status, expires_at)
    VALUES ('dc-bad', 'uc-bad', 'weird_state', now() + interval '15 minutes');
    RAISE EXCEPTION 'T36 FAIL: 非法状态未被拒绝';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS T36-3: device_authorizations 状态枚举 CHECK';
  END;

  -- ④ R3-M3 部分唯一:活跃码(pending/approved)user_code_hash 冲突;终态后允许安全复用
  INSERT INTO device_authorizations (id, device_code_hash, user_code_hash, status, expires_at)
  VALUES ('adadadad-0000-0000-0000-000000000001', 'dc-1', 'uc-shared', 'pending', now() + interval '15 minutes')
  RETURNING id INTO v_authz;
  BEGIN
    INSERT INTO device_authorizations (device_code_hash, user_code_hash, status, expires_at)
    VALUES ('dc-2', 'uc-shared', 'pending', now() + interval '15 minutes');
    RAISE EXCEPTION 'T36 FAIL: 活跃期重复 user_code_hash 未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T36-4: 活跃(pending/approved)user_code_hash 部分唯一';
  END;
  UPDATE device_authorizations SET status='consumed', consumed_at=now() WHERE id = v_authz;
  INSERT INTO device_authorizations (device_code_hash, user_code_hash, status, expires_at)
  VALUES ('dc-3', 'uc-shared', 'pending', now() + interval '15 minutes');
  RAISE NOTICE 'PASS T36-5: 终态后 user_code_hash 允许安全复用(20bit 码空间不随历史耗尽)';

  -- ⑤ approve/consume 条件更新原子性:首次恰 1 行,重复 0 行(并发批/拒不覆盖)
  UPDATE device_authorizations SET status='approved', approved_at=now()
   WHERE device_code_hash='dc-3' AND status='pending' AND expires_at > now();
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  ASSERT v_rows = 1, 'T36 FAIL: approve 条件更新应恰影响 1 行';
  UPDATE device_authorizations SET status='approved', approved_at=now()
   WHERE device_code_hash='dc-3' AND status='pending' AND expires_at > now();
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  ASSERT v_rows = 0, 'T36 FAIL: 重复 approve 应影响 0 行(原子迁移)';
  RAISE NOTICE 'PASS T36-6: approve 条件更新原子性(WHERE pending AND expires_at>now(),1 行/0 行)';

  -- ⑥ 单码至多一会话(sessions.device_authorization_id UNIQUE)
  INSERT INTO sessions (user_id, token_hash, type, workspace_id, device_authorization_id, expires_at)
  VALUES (v_user, 'mesh_rft_hash_cli_2', 'cli', '11111111-1111-1111-1111-111111111111',
          (SELECT id FROM device_authorizations WHERE device_code_hash='dc-3'), now() + interval '30 days');
  BEGIN
    INSERT INTO sessions (user_id, token_hash, type, workspace_id, device_authorization_id, expires_at)
    VALUES (v_user, 'mesh_rft_hash_cli_3', 'cli', '11111111-1111-1111-1111-111111111111',
            (SELECT id FROM device_authorizations WHERE device_code_hash='dc-3'), now() + interval '30 days');
    RAISE EXCEPTION 'T36 FAIL: 单码第二条会话未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T36-7: device_authorization_id UNIQUE(单码至多一会话)';
  END;

  -- ⑧ R5-H1 轮换仲裁 + 宽限「只发 access」协议(串行等价判定逻辑;真并行 e2e 在后端实现期,§3.8 断言清单)
  -- 初始态:会话持有 refresh R0
  UPDATE sessions SET token_hash='R0', previous_token_hash=NULL, rotated_at=NULL, revoked_at=NULL
   WHERE id='abababab-0000-0000-0000-000000000001';

  -- 胜者请求:条件轮换 WHERE token_hash=R0 → 影响 1 行(仲裁胜出),previous=R0、rotated_at=now()
  UPDATE sessions SET token_hash='R1', previous_token_hash=token_hash, rotated_at=now()
   WHERE id='abababab-0000-0000-0000-000000000001' AND token_hash='R0' AND revoked_at IS NULL;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  ASSERT v_rows = 1, 'T36 FAIL: 胜者条件轮换应影响 1 行(行数控裁)';

  -- 后来者请求(携带 R0):条件轮换 WHERE token_hash='R0' → 影响 0 行(已轮换,仲裁败北)
  UPDATE sessions SET token_hash='R2', previous_token_hash=token_hash, rotated_at=now()
   WHERE id='abababab-0000-0000-0000-000000000001' AND token_hash='R0' AND revoked_at IS NULL;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  ASSERT v_rows = 0, 'T36 FAIL: 后来者对已轮换 token 的条件轮换应影响 0 行(不得二次轮换)';

  -- 后来者落宽限路径:previous_token_hash=R0 匹配 + 窗内 + 未撤销 → 只发 access(协议上不写库)
  ASSERT EXISTS (SELECT 1 FROM sessions
                  WHERE id='abababab-0000-0000-0000-000000000001'
                    AND previous_token_hash='R0' AND revoked_at IS NULL
                    AND rotated_at >= now() - interval '30 seconds'),
         'T36 FAIL: 窗内旧 refresh 应命中宽限路径条件(只发 access,不下发 refresh 明文)';
  -- 宽限路径不写库的断言:token_hash/previous/rotated_at 保持胜者写入值
  ASSERT (SELECT token_hash FROM sessions WHERE id='abababab-0000-0000-0000-000000000001') = 'R1'
     AND (SELECT previous_token_hash FROM sessions WHERE id='abababab-0000-0000-0000-000000000001') = 'R0',
         'T36 FAIL: 宽限路径不得变更 token_hash/previous_token_hash(不二次轮换、不放大)';

  -- 超窗:rotated_at 移出 30s 窗 → 宽限条件不成立(重放按 401 失效)
  UPDATE sessions SET rotated_at=now() - interval '120 seconds'
   WHERE id='abababab-0000-0000-0000-000000000001';
  ASSERT NOT EXISTS (SELECT 1 FROM sessions
                      WHERE id='abababab-0000-0000-0000-000000000001'
                        AND previous_token_hash='R0' AND rotated_at >= now() - interval '30 seconds'),
         'T36 FAIL: 超出宽限窗(120s > 30s)的旧 refresh 不应命中宽限路径';

  -- 撤销后:revoked_at 非空 → 胜者路径(条件轮换 WHERE revoked_at IS NULL)与宽限路径(同条件)均拒绝
  UPDATE sessions SET revoked_at=now(), rotated_at=now()
   WHERE id='abababab-0000-0000-0000-000000000001';
  UPDATE sessions SET token_hash='R3', previous_token_hash=token_hash, rotated_at=now()
   WHERE id='abababab-0000-0000-0000-000000000001' AND token_hash='R1' AND revoked_at IS NULL;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  ASSERT v_rows = 0, 'T36 FAIL: 已撤销会话的条件轮换应影响 0 行';
  ASSERT NOT EXISTS (SELECT 1 FROM sessions
                      WHERE id='abababab-0000-0000-0000-000000000001'
                        AND previous_token_hash='R0' AND revoked_at IS NULL),
         'T36 FAIL: 已撤销会话不应命中宽限路径';
  RAISE NOTICE 'PASS T36-8: R5-H1 轮换协议判定(胜者行数控裁/后来者 0 行不二次轮换/宽限只发 access 不写库/超窗失效/撤销双路拒绝)';

  -- ⑨ R6-H3 authenticated_at step-up 状态机(取消无条件默认,按来源显式赋值)
  -- 建 session ≠ 主动认证:新建会话 authenticated_at 可为 NULL(闸门不通过)
  INSERT INTO sessions (id, user_id, token_hash, type, workspace_id, expires_at, authenticated_at)
  VALUES ('abababab-0000-0000-0000-000000000002', v_user, 'mesh_rft_cli_null', 'cli',
          '11111111-1111-1111-1111-111111111111', now() + interval '30 days', NULL);
  ASSERT NOT (SELECT authenticated_at IS NOT NULL AND now() - authenticated_at <= interval '900 seconds'
                FROM sessions WHERE id='abababab-0000-0000-0000-000000000002'),
         'T36 FAIL: authenticated_at=NULL 的会话(静默 SSO / 批准会话无新鲜认证继承)step-up 闸门不应通过';

  -- 密码登录/注册:凭据校验成功显式置 now() → 窗口内通过
  UPDATE sessions SET authenticated_at=now() WHERE id='abababab-0000-0000-0000-000000000001';
  ASSERT (SELECT authenticated_at IS NOT NULL AND now() - authenticated_at <= interval '900 seconds'
            FROM sessions WHERE id='abababab-0000-0000-0000-000000000001'),
         'T36 FAIL: 凭据校验成功置位后应在 900s 窗口内';

  -- 设备会话继承:approved_authenticated_at 经锁定读取后复制进 cli 会话(不以消费时刻冒充)
  UPDATE device_authorizations SET approved_authenticated_at=now() - interval '100 seconds'
   WHERE device_code_hash='dc-3';
  UPDATE sessions SET authenticated_at=(SELECT approved_authenticated_at FROM device_authorizations WHERE device_code_hash='dc-3')
   WHERE id='abababab-0000-0000-0000-000000000002';
  ASSERT (SELECT authenticated_at = (SELECT approved_authenticated_at FROM device_authorizations WHERE device_code_hash='dc-3')
            FROM sessions WHERE id='abababab-0000-0000-0000-000000000002'),
         'T36 FAIL: cli 会话 authenticated_at 应精确继承批准记录快照(非消费时刻)';
  -- 批准会话无新鲜认证(NULL)→ 继承 NULL → 闸门不通过(旧 Web 会话批准设备码负向路径)
  UPDATE device_authorizations SET approved_authenticated_at=NULL WHERE device_code_hash='dc-3';
  UPDATE sessions SET authenticated_at=(SELECT approved_authenticated_at FROM device_authorizations WHERE device_code_hash='dc-3')
   WHERE id='abababab-0000-0000-0000-000000000002';
  ASSERT (SELECT authenticated_at IS NULL FROM sessions WHERE id='abababab-0000-0000-0000-000000000002'),
         'T36 FAIL: 批准会话无新鲜认证时 cli 会话应继承 NULL(闸门不通过 → CLI 敏感操作 403 reauth_required)';

  -- 超窗:1000s 前的认证超出窗口 → 闸门不通过(需经 POST /auth/reauth 恢复)
  UPDATE sessions SET authenticated_at=now() - interval '1000 seconds'
   WHERE id='abababab-0000-0000-0000-000000000001';
  ASSERT NOT (SELECT authenticated_at IS NOT NULL AND now() - authenticated_at <= interval '900 seconds'
                FROM sessions WHERE id='abababab-0000-0000-0000-000000000001'),
         'T36 FAIL: 1000s 前的认证应超出 step-up 窗口(需再认证)';
  -- reauth 恢复:更新为 now() → 窗口内通过
  UPDATE sessions SET authenticated_at=now() WHERE id='abababab-0000-0000-0000-000000000001';
  ASSERT (SELECT authenticated_at IS NOT NULL AND now() - authenticated_at <= interval '900 seconds'
            FROM sessions WHERE id='abababab-0000-0000-0000-000000000001'),
         'T36 FAIL: reauth 后应重新在窗口内';
  RAISE NOTICE 'PASS T36-9: authenticated_at step-up 状态机(NULL 默认/来源显式赋值/设备继承批准快照非消费时刻/窗口判据/reauth 恢复)';
END $$;

-- T37 前置:批量行 + ANALYZE,使前缀路径 EXPLAIN 断言稳定(选择率接近生产形态,
-- 小表统计下规划器可能误选工作区唯一索引,非索引不可用)
INSERT INTO users (id, email, display_name)
SELECT gen_random_uuid(), 'bulk-'||g||'@x.dev', 'Bulk User '||g FROM generate_series(1,3000) g;
INSERT INTO members (workspace_id, member_type, user_id, role, display_override, search_name)
SELECT '11111111-1111-1111-1111-111111111111', 'human', u.id, 'member', 'Bulk '||u.email, public.mesh_search_norm('Bulk '||u.email)
FROM (SELECT id, email FROM users WHERE email LIKE 'bulk-%') u;
ANALYZE members;

-- ===================== T37:搜索归一函数 + 索引 + identifier 快路径 + runtime 令牌真源(MES-76 R2-H3/R3-M1/R3-M4/R3-H4)=====================
DO $$
DECLARE
  v_plan   TEXT := '';
  v_rec    RECORD;
  v_member UUID;
  v_rt1    UUID;
  v_rt2    UUID;
BEGIN
  -- ① 归一行为:NFKD + 去重音 + 小写(R3-M1)
  ASSERT public.mesh_search_norm('José') = 'jose', 'T37 FAIL: mesh_search_norm(José) 应为 jose';
  ASSERT public.mesh_search_norm('ZHANG Wei') = 'zhang wei', 'T37 FAIL: 大写应归一小写';
  ASSERT public.mesh_search_norm(NULL) IS NULL, 'T37 FAIL: NULL 输入应返回 NULL';
  RAISE NOTICE 'PASS T37-1: mesh_search_norm 归一行为(José→jose、大写→小写、NULL→NULL)';

  -- ② IMMUTABLE 声明(表达式索引前提)+ 固定 schema
  ASSERT (SELECT provolatile FROM pg_proc WHERE proname='mesh_search_norm' AND pronamespace='public'::regnamespace LIMIT 1) = 'i',
         'T37 FAIL: public.mesh_search_norm 应为 IMMUTABLE';
  RAISE NOTICE 'PASS T37-2: public.mesh_search_norm 为 IMMUTABLE(表达式索引前提)';

  -- ③ 搜索索引精确集合(R4-M1:11 条精确断言,缺一即失败;并校验关键 indexdef)
  ASSERT (SELECT count(*) FROM pg_indexes
           WHERE indexname IN ('idx_members_search_name_trgm','idx_members_search_name_prefix',
                               'idx_issues_title_trgm','idx_issues_title_prefix','idx_issues_identifier_prefix',
                               'idx_projects_name_trgm','idx_projects_name_prefix',
                               'idx_views_name_trgm','idx_views_name_prefix',
                               'idx_chat_sessions_title_trgm','idx_chat_sessions_title_prefix')) = 11,
         'T37 FAIL: 搜索索引应为精确 11 条(9 条 mesh_search_norm 表达式索引 + 2 条成员投影索引)';
  -- 注:pg_get_indexdef 按 search_path 归一显示(public. 缺省即省略),故断言匹配无 schema 前缀形;
  -- DDL 源文本一律 public. 限定(与 Spec §2.2 逐字一致),二者不矛盾
  ASSERT (SELECT indexdef FROM pg_indexes WHERE indexname='idx_members_search_name_prefix')
         LIKE '%text_pattern_ops%' AND
         (SELECT indexdef FROM pg_indexes WHERE indexname='idx_members_search_name_prefix')
         LIKE '%WHERE (status <> ''removed''::text)%',
         'T37 FAIL: 成员前缀索引应为 text_pattern_ops + 部分谓词 status<>removed';
  ASSERT (SELECT indexdef FROM pg_indexes WHERE indexname='idx_issues_title_trgm')
         LIKE '%gin_trgm_ops%' AND
         (SELECT indexdef FROM pg_indexes WHERE indexname='idx_issues_title_trgm')
         LIKE '%mesh_search_norm(title)%',
         'T37 FAIL: issue title trigram 索引应为 mesh_search_norm(title) 表达式 + gin_trgm_ops';
  ASSERT (SELECT indexdef FROM pg_indexes WHERE indexname='idx_issues_identifier_prefix')
         LIKE '%mesh_search_norm(identifier)%' AND
         (SELECT indexdef FROM pg_indexes WHERE indexname='idx_issues_identifier_prefix')
         LIKE '%text_pattern_ops%',
         'T37 FAIL: identifier 前缀索引应为 mesh_search_norm(identifier) 表达式 + text_pattern_ops';
  RAISE NOTICE 'PASS T37-3: 11 条搜索索引精确集合 + 关键 indexdef(表达式/算子/部分谓词)校验';

  -- ④ 投影回补与索引同一函数(一致性)
  INSERT INTO members (id, workspace_id, member_type, user_id, role, display_override)
  VALUES ('eeeeeeee-7777-0000-0000-000000000076', '11111111-1111-1111-1111-111111111111', 'human',
          (SELECT id FROM users LIMIT 1), 'member', 'José 管理员')
  RETURNING id INTO v_member;
  UPDATE members SET search_name = public.mesh_search_norm(display_override) WHERE id = v_member;
  ASSERT (SELECT search_name FROM members WHERE id = v_member) = 'jose 管理员',
         'T37 FAIL: search_name 投影应与 mesh_search_norm 一致';
  RAISE NOTICE 'PASS T37-4: members.search_name 投影与归一函数一致(José 管理员→jose 管理员)';

  -- ⑤ 表达式兼容性断言(R4-M1:真实 1/2 字符用例;强制关 seqscan 只为证明「查询表达式与
  --    索引表达式逐字匹配、pattern 索引可用」,不代表规划器选择;查询携带 status<>'removed'
  --    可见性谓词,与部分索引谓词一致,§3.3 名册可见性同口径)
  SET LOCAL enable_seqscan = off;
  FOR v_rec IN EXECUTE
    'EXPLAIN SELECT id FROM members
       WHERE workspace_id = ''11111111-1111-1111-1111-111111111111''
         AND status <> ''removed''
         AND search_name LIKE public.mesh_search_norm(''j'') || ''%'''
  LOOP
    v_plan := v_plan || v_rec."QUERY PLAN" || E'\n';
  END LOOP;
  ASSERT v_plan LIKE '%idx_members_search_name_prefix%',
         'T37 FAIL: 1 字符前缀查询与 pattern 索引表达式不匹配(关 seqscan 后仍不可用)';
  v_plan := '';
  FOR v_rec IN EXECUTE
    'EXPLAIN SELECT id FROM members
       WHERE workspace_id = ''11111111-1111-1111-1111-111111111111''
         AND status <> ''removed''
         AND search_name LIKE public.mesh_search_norm(''jo'') || ''%'''
  LOOP
    v_plan := v_plan || v_rec."QUERY PLAN" || E'\n';
  END LOOP;
  ASSERT v_plan LIKE '%idx_members_search_name_prefix%',
         'T37 FAIL: 2 字符前缀查询与 pattern 索引表达式不匹配(关 seqscan 后仍不可用)';
  RAISE NOTICE 'PASS T37-5: 表达式兼容性(真实 1/2 字符前缀查询均可用 pattern 索引,关 seqscan 下命中)';

  -- ⑤b 真实规模自然规划断言(R4-M1:不强制 planner——批量行 + ANALYZE 后选择性前缀
  --     应由规划器自然选中 pattern 索引;此项失败 = 索引对真实查询无效,而非规划器偏好)
  SET LOCAL enable_seqscan = on;
  v_plan := '';
  FOR v_rec IN EXECUTE
    'EXPLAIN SELECT id FROM members
       WHERE workspace_id = ''11111111-1111-1111-1111-111111111111''
         AND status <> ''removed''
         AND search_name LIKE public.mesh_search_norm(''jo'') || ''%'''
  LOOP
    v_plan := v_plan || v_rec."QUERY PLAN" || E'\n';
  END LOOP;
  ASSERT v_plan LIKE '%idx_members_search_name_prefix%',
         'T37 FAIL: 真实规模自然规划下 2 字符前缀查询未选中 pattern 索引(索引对真实查询无效)';
  RAISE NOTICE 'PASS T37-5b: 真实规模不强制 planner,前缀查询自然命中 idx_members_search_name_prefix';

  -- ⑥ identifier 快路径 canonical uppercase(R3-M4:web-1 命中 WEB-1)
  ASSERT EXISTS (SELECT 1 FROM issues
                  WHERE workspace_id = '11111111-1111-1111-1111-111111111111'
                    AND identifier = upper('web-1')),
         'T37 FAIL: 小写输入 web-1 应经 upper() 规范化等值命中 WEB-1';
  RAISE NOTICE 'PASS T37-6: identifier 快路径 upper() 规范化等值(web-1 → WEB-1)';

  -- ⑦ runtime_token_hash UNIQUE(R3-H4:mesh_rt_ 唯一真源)
  INSERT INTO runtimes (id, workspace_id, name, runtime_token_hash)
  VALUES ('a1a1a1a1-7777-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 't37-rt-1', 'mesh_rt_dup_hash')
  RETURNING id INTO v_rt1;
  INSERT INTO runtimes (id, workspace_id, name, runtime_token_hash)
  VALUES ('a1a1a1a1-7777-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 't37-rt-2', NULL)
  RETURNING id INTO v_rt2;
  BEGIN
    UPDATE runtimes SET runtime_token_hash='mesh_rt_dup_hash' WHERE id = v_rt2;
    RAISE EXCEPTION 'T37 FAIL: 重复 runtime_token_hash 未被拒绝';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS T37-7: runtime_token_hash UNIQUE(mesh_rt_ 唯一真源,重复哈希被拒)';
  END;
  -- 停用 = 置 NULL(允许多行 NULL,不冲突)
  UPDATE runtimes SET runtime_token_hash=NULL WHERE id = v_rt1;
  ASSERT (SELECT count(*) FROM runtimes WHERE name IN ('t37-rt-1','t37-rt-2') AND runtime_token_hash IS NULL) = 2,
         'T37 FAIL: 停用清除哈希应允许多行 NULL';
  RAISE NOTICE 'PASS T37-8: 停用置 NULL 合法(多行 NULL 不冲突,令牌即失效)';
END $$;

-- ===================== T38:词典升级路径完整 smoke test(R4-H4 建立,R5-H3 扩充,R6-H4 真实事务边界)
-- 结构与生产分阶段迁移逐段对应:阶段 1 事务外建行为差异新版函数(词典版本共存)→
-- 阶段 2 事务外加列 + 回补 + 双写 → 阶段 3 事务外 CREATE INDEX CONCURRENTLY ×11 →
-- 阶段 4 单一快速事务只做改名(清理前断言新函数精确绑定 9 条规范索引、旧函数精确绑定
-- 9 条 _prev)→ COMMIT → 阶段 5/6 COMMIT 后可见状态行为验证 → 阶段 7 逐条事务外
-- DROP INDEX CONCURRENTLY → 阶段 8 断言旧函数零依赖并删旧函数/列 → 阶段 9 清理后行为验证 =====================

-- 阶段 1(事务外):新版归一函数——与旧版**可观察行为不同**(模拟词典/规则升级:连字符折叠为空格)
CREATE FUNCTION public.mesh_search_norm_next(t TEXT) RETURNS TEXT
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE RETURNS NULL ON NULL INPUT AS
$$ BEGIN RETURN lower(public.unaccent('public.unaccent'::regdictionary, normalize(replace(t, '-', ' '), NFKD))); END $$;

DO $$
BEGIN
  ASSERT public.mesh_search_norm('alpha-beta') = 'alpha-beta', 'T38 FAIL: 旧版函数应保留连字符';
  ASSERT public.mesh_search_norm_next('alpha-beta') = 'alpha beta', 'T38 FAIL: 新版函数应折叠连字符为空格(可观察行为差异)';
  ASSERT public.mesh_search_norm_next('José') = 'jose', 'T38 FAIL: 新版基础归一行为不变(José→jose)';
  RAISE NOTICE 'PASS T38-1: 词典版本共存(新旧函数并存,行为可观察差异:连字符 alpha-beta vs alpha beta)';
END $$;

-- 阶段 2(事务外):新增投影列 + 回补(生产按 spec §2.2 分批 ≤1 万行 + 双写;smoke test 单批)
ALTER TABLE members ADD COLUMN search_name_next TEXT NOT NULL DEFAULT '';
UPDATE members SET search_name_next = public.mesh_search_norm_next(COALESCE(display_override, ''));

-- 阶段 3(事务外,逐条 CREATE INDEX CONCURRENTLY):切换前建完全部 11 条 _next 索引
CREATE INDEX CONCURRENTLY idx_issues_title_trgm_next ON issues USING gin ((public.mesh_search_norm_next(title)) gin_trgm_ops) WHERE deleted_at IS NULL;
CREATE INDEX CONCURRENTLY idx_issues_title_prefix_next ON issues (workspace_id, (public.mesh_search_norm_next(title)) text_pattern_ops) WHERE deleted_at IS NULL;
CREATE INDEX CONCURRENTLY idx_issues_identifier_prefix_next ON issues (workspace_id, (public.mesh_search_norm_next(identifier)) text_pattern_ops) WHERE deleted_at IS NULL;
CREATE INDEX CONCURRENTLY idx_projects_name_trgm_next ON projects USING gin ((public.mesh_search_norm_next(name)) gin_trgm_ops) WHERE deleted_at IS NULL;
CREATE INDEX CONCURRENTLY idx_projects_name_prefix_next ON projects (workspace_id, (public.mesh_search_norm_next(name)) text_pattern_ops) WHERE deleted_at IS NULL;
CREATE INDEX CONCURRENTLY idx_views_name_trgm_next ON views USING gin ((public.mesh_search_norm_next(name)) gin_trgm_ops);
CREATE INDEX CONCURRENTLY idx_views_name_prefix_next ON views (workspace_id, (public.mesh_search_norm_next(name)) text_pattern_ops);
CREATE INDEX CONCURRENTLY idx_chat_sessions_title_trgm_next ON chat_sessions USING gin ((public.mesh_search_norm_next(title)) gin_trgm_ops);
CREATE INDEX CONCURRENTLY idx_chat_sessions_title_prefix_next ON chat_sessions (workspace_id, (public.mesh_search_norm_next(title)) text_pattern_ops);
CREATE INDEX CONCURRENTLY idx_members_search_name_trgm_next ON members USING gin (search_name_next gin_trgm_ops);
CREATE INDEX CONCURRENTLY idx_members_search_name_prefix_next ON members (workspace_id, search_name_next text_pattern_ops) WHERE status <> 'removed';

-- 阶段 4(单一快速事务,只做改名 + 清理前双侧精确绑定断言;本 DO 结束即 COMMIT)
DO $$
DECLARE
  v_old_oid OID;
  v_new_oid OID;
  v_new_bound INT;
  v_old_bound INT;
BEGIN
  SELECT oid INTO v_old_oid FROM pg_proc WHERE proname='mesh_search_norm';
  SELECT oid INTO v_new_oid FROM pg_proc WHERE proname='mesh_search_norm_next';

  -- 4a. 原子改名:函数 / 投影列 / 11 条索引(改名不更 OID)
  ALTER FUNCTION public.mesh_search_norm RENAME TO mesh_search_norm_prev;
  ALTER FUNCTION public.mesh_search_norm_next RENAME TO mesh_search_norm;
  ALTER TABLE members RENAME COLUMN search_name TO search_name_prev;
  ALTER TABLE members RENAME COLUMN search_name_next TO search_name;
  ALTER INDEX idx_issues_title_trgm RENAME TO idx_issues_title_trgm_prev;
  ALTER INDEX idx_issues_title_trgm_next RENAME TO idx_issues_title_trgm;
  ALTER INDEX idx_issues_title_prefix RENAME TO idx_issues_title_prefix_prev;
  ALTER INDEX idx_issues_title_prefix_next RENAME TO idx_issues_title_prefix;
  ALTER INDEX idx_issues_identifier_prefix RENAME TO idx_issues_identifier_prefix_prev;
  ALTER INDEX idx_issues_identifier_prefix_next RENAME TO idx_issues_identifier_prefix;
  ALTER INDEX idx_projects_name_trgm RENAME TO idx_projects_name_trgm_prev;
  ALTER INDEX idx_projects_name_trgm_next RENAME TO idx_projects_name_trgm;
  ALTER INDEX idx_projects_name_prefix RENAME TO idx_projects_name_prefix_prev;
  ALTER INDEX idx_projects_name_prefix_next RENAME TO idx_projects_name_prefix;
  ALTER INDEX idx_views_name_trgm RENAME TO idx_views_name_trgm_prev;
  ALTER INDEX idx_views_name_trgm_next RENAME TO idx_views_name_trgm;
  ALTER INDEX idx_views_name_prefix RENAME TO idx_views_name_prefix_prev;
  ALTER INDEX idx_views_name_prefix_next RENAME TO idx_views_name_prefix;
  ALTER INDEX idx_chat_sessions_title_trgm RENAME TO idx_chat_sessions_title_trgm_prev;
  ALTER INDEX idx_chat_sessions_title_trgm_next RENAME TO idx_chat_sessions_title_trgm;
  ALTER INDEX idx_chat_sessions_title_prefix RENAME TO idx_chat_sessions_title_prefix_prev;
  ALTER INDEX idx_chat_sessions_title_prefix_next RENAME TO idx_chat_sessions_title_prefix;
  ALTER INDEX idx_members_search_name_trgm RENAME TO idx_members_search_name_trgm_prev;
  ALTER INDEX idx_members_search_name_trgm_next RENAME TO idx_members_search_name_trgm;
  ALTER INDEX idx_members_search_name_prefix RENAME TO idx_members_search_name_prefix_prev;
  ALTER INDEX idx_members_search_name_prefix_next RENAME TO idx_members_search_name_prefix;

  -- 4b. 清理前断言(此时 11 条 _prev 仍在):新函数精确绑定 9 条规范表达式索引
  SELECT count(DISTINCT c.relname) INTO v_new_bound
  FROM pg_depend d JOIN pg_class c ON c.oid = d.objid
  WHERE d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_proc'::regclass
    AND d.refobjid = v_new_oid
    AND c.relname IN ('idx_issues_title_trgm','idx_issues_title_prefix','idx_issues_identifier_prefix',
                      'idx_projects_name_trgm','idx_projects_name_prefix',
                      'idx_views_name_trgm','idx_views_name_prefix',
                      'idx_chat_sessions_title_trgm','idx_chat_sessions_title_prefix');
  ASSERT v_new_bound = 9, 'T38 FAIL: 切换后新函数应精确绑定 9 条规范表达式索引(pg_depend 计数 = 9)';

  -- 4c. 清理前断言:旧函数精确绑定 9 条 _prev 表达式索引(_prev 未删,依赖理应仍在)
  SELECT count(DISTINCT c.relname) INTO v_old_bound
  FROM pg_depend d JOIN pg_class c ON c.oid = d.objid
  WHERE d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_proc'::regclass
    AND d.refobjid = v_old_oid
    AND c.relname IN ('idx_issues_title_trgm_prev','idx_issues_title_prefix_prev','idx_issues_identifier_prefix_prev',
                      'idx_projects_name_trgm_prev','idx_projects_name_prefix_prev',
                      'idx_views_name_trgm_prev','idx_views_name_prefix_prev',
                      'idx_chat_sessions_title_trgm_prev','idx_chat_sessions_title_prefix_prev');
  ASSERT v_old_bound = 9, 'T38 FAIL: 清理前旧函数应精确绑定 9 条 _prev 表达式索引(改名不更 OID)';

  -- 4d. 规范函数名指向新实现 + indexdef 表达式文本校验
  ASSERT (SELECT oid FROM pg_proc WHERE proname='mesh_search_norm') = v_new_oid,
         'T38 FAIL: 切换后 public.mesh_search_norm 规范名应指向新版实现';
  ASSERT (SELECT pg_get_indexdef(c.oid) FROM pg_class c WHERE c.relname='idx_issues_title_trgm')
         LIKE '%mesh_search_norm(title)%' AND
         (SELECT pg_get_indexdef(c.oid) FROM pg_class c WHERE c.relname='idx_issues_title_trgm')
         NOT LIKE '%mesh_search_norm_prev%',
         'T38 FAIL: 规范表达式索引 indexdef 应显示 mesh_search_norm(绑定新实现)';
  RAISE NOTICE 'PASS T38-2: 单一快速事务原子改名(新函数绑定 9 条规范索引/旧函数绑定 9 条 _prev,indexdef 校验)→ COMMIT';
END $$;

-- 阶段 5(COMMIT 后可见状态,事务外):批量行使规划器在真实选择率下自然选中索引
INSERT INTO issues (id, workspace_id, identifier_namespace_key, number, identifier, title, status_id, state_category)
SELECT gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'T38', g, 'T38-'||g,
       'Gamma Noise Title '||g, 'eeeeeeee-0000-0000-0000-000000000001', 'todo'
FROM generate_series(1,3000) g;
ANALYZE issues;

-- 阶段 6(COMMIT 后行为验证事务):新折叠行为落投影 + 前缀自然命中 + trigram 表达式可用
DO $$
DECLARE
  v_plan TEXT := '';
  v_rec  RECORD;
BEGIN
  ASSERT public.mesh_search_norm('alpha-beta') = 'alpha beta', 'T38 FAIL: 切换后规范函数应具新版连字符折叠行为';
  ASSERT public.mesh_search_norm('José') = 'jose', 'T38 FAIL: 基础归一行为不变';
  -- 回补一致性:新投影 = 以新函数重归一旧投影(行为变更迁移的正确不变量——
  -- 连字符折叠使含连字符的值合法地不同,严格相等不是回补契约;须在新成员插入前断言)
  ASSERT NOT EXISTS (SELECT 1 FROM members
                      WHERE search_name IS DISTINCT FROM public.mesh_search_norm(search_name_prev)),
         'T38 FAIL: 新投影应等于以新函数重归一旧投影(回补遗漏/错误)';
  INSERT INTO users (id, email, display_name) VALUES ('aaaaaaaa-7777-0000-0000-000000000076', 't38-user@x.dev', 'T38 User');
  INSERT INTO members (id, workspace_id, member_type, user_id, role, display_override, search_name)
  VALUES ('dddddddd-7777-0000-0000-000000000076', '11111111-1111-1111-1111-111111111111', 'human',
          'aaaaaaaa-7777-0000-0000-000000000076', 'member', 'Alpha-Beta Tester', public.mesh_search_norm('Alpha-Beta Tester'));
  ASSERT (SELECT search_name FROM members WHERE id='dddddddd-7777-0000-0000-000000000076') = 'alpha beta tester',
         'T38 FAIL: 切换后新折叠行为(连字符→空格)应落入投影';
  -- 前缀查询自然命中规范 pattern 索引(不强制 planner)
  FOR v_rec IN EXECUTE
    'EXPLAIN SELECT id FROM members
       WHERE workspace_id = ''11111111-1111-1111-1111-111111111111''
         AND status <> ''removed''
         AND search_name LIKE public.mesh_search_norm(''alpha b'') || ''%'''
  LOOP
    v_plan := v_plan || v_rec."QUERY PLAN" || E'\n';
  END LOOP;
  ASSERT v_plan LIKE '%idx_members_search_name_prefix%',
         'T38 FAIL: COMMIT 后前缀查询应自然命中规范 pattern 索引';
  -- trigram 表达式索引可用(强制 bitmap 路径验证表达式匹配,自然选择由 §10 真实规模验收)
  SET LOCAL enable_seqscan = off;
  SET LOCAL enable_indexscan = off;
  v_plan := '';
  FOR v_rec IN EXECUTE
    'EXPLAIN SELECT id FROM issues
       WHERE workspace_id = ''11111111-1111-1111-1111-111111111111''
         AND deleted_at IS NULL
         AND public.mesh_search_norm(title) % public.mesh_search_norm(''zebra'')'
  LOOP
    v_plan := v_plan || v_rec."QUERY PLAN" || E'\n';
  END LOOP;
  ASSERT v_plan LIKE '%idx_issues_title_trgm%',
         'T38 FAIL: COMMIT 后 trigram 表达式查询应可走规范 GIN 索引(表达式不匹配则 bitmap 不可用)';
  RAISE NOTICE 'PASS T38-3: COMMIT 后可见状态行为验证(新折叠落投影/前缀自然命中/trigram 表达式可用)';
END $$;

-- 阶段 7(事务外,逐条 DROP INDEX CONCURRENTLY:生产契约要求的并发安全删除)
DROP INDEX CONCURRENTLY idx_issues_title_trgm_prev;
DROP INDEX CONCURRENTLY idx_issues_title_prefix_prev;
DROP INDEX CONCURRENTLY idx_issues_identifier_prefix_prev;
DROP INDEX CONCURRENTLY idx_projects_name_trgm_prev;
DROP INDEX CONCURRENTLY idx_projects_name_prefix_prev;
DROP INDEX CONCURRENTLY idx_views_name_trgm_prev;
DROP INDEX CONCURRENTLY idx_views_name_prefix_prev;
DROP INDEX CONCURRENTLY idx_chat_sessions_title_trgm_prev;
DROP INDEX CONCURRENTLY idx_chat_sessions_title_prefix_prev;
DROP INDEX CONCURRENTLY idx_members_search_name_trgm_prev;
DROP INDEX CONCURRENTLY idx_members_search_name_prefix_prev;

-- 阶段 8(事务):旧函数零依赖断言(_prev 索引已全部删除)→ 删旧函数/旧列
DO $$
DECLARE
  v_old_deps INT;
BEGIN
  SELECT count(*) INTO v_old_deps FROM pg_depend
   WHERE refobjid = (SELECT oid FROM pg_proc WHERE proname='mesh_search_norm_prev')
     AND refclassid = 'pg_proc'::regclass AND classid = 'pg_class'::regclass;
  ASSERT v_old_deps = 0, 'T38 FAIL: _prev 索引删除后旧函数应零索引依赖(9 条表达式索引已全迁移)';
  DROP FUNCTION public.mesh_search_norm_prev(TEXT);
  ASSERT NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname='mesh_search_norm_prev'),
         'T38 FAIL: 旧版函数应已实际删除';
  ALTER TABLE members DROP COLUMN search_name_prev;
  ASSERT NOT EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name='members' AND column_name='search_name_prev'),
         'T38 FAIL: 旧投影列应已删除';
  RAISE NOTICE 'PASS T38-4: 旧函数零依赖 → 实际删除旧函数/旧列(删除成功即切换完整性证明)';
END $$;

-- 阶段 9(清理后行为验证):规范索引接管全部查询,新函数行为生效
DO $$
DECLARE
  v_plan TEXT := '';
  v_rec  RECORD;
BEGIN
  FOR v_rec IN EXECUTE
    'EXPLAIN SELECT id FROM members
       WHERE workspace_id = ''11111111-1111-1111-1111-111111111111''
         AND status <> ''removed''
         AND search_name LIKE public.mesh_search_norm(''alpha b'') || ''%'''
  LOOP
    v_plan := v_plan || v_rec."QUERY PLAN" || E'\n';
  END LOOP;
  ASSERT v_plan LIKE '%idx_members_search_name_prefix%',
         'T38 FAIL: 清理后前缀查询应命中规范 pattern 索引';
  SET LOCAL enable_seqscan = off;
  SET LOCAL enable_indexscan = off;
  v_plan := '';
  FOR v_rec IN EXECUTE
    'EXPLAIN SELECT id FROM issues
       WHERE workspace_id = ''11111111-1111-1111-1111-111111111111''
         AND deleted_at IS NULL
         AND public.mesh_search_norm(title) % public.mesh_search_norm(''zebra'')'
  LOOP
    v_plan := v_plan || v_rec."QUERY PLAN" || E'\n';
  END LOOP;
  ASSERT v_plan LIKE '%idx_issues_title_trgm%',
         'T38 FAIL: 清理后 trigram 查询应可走规范 GIN 索引';
  RAISE NOTICE 'PASS T38: 词典升级完整迁移(真实事务边界:事务外建 → 快速改名事务 COMMIT → 提交后验证 → 事务外 DROP INDEX CONCURRENTLY → 零依赖删旧 → 清理后验证)';
END $$;

\echo '============================================================'
\echo 'ALL R2+R3+R4+R5+MES-76(R2/R3/R4) SCHEMA + BEHAVIOR VALIDATIONS PASSED (PostgreSQL 16)'
\echo '============================================================'
