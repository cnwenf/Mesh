# 工作区(Workspace)功能 Spec

> **所属层**:基础能力层(多租户隔离与组织管理)。
> **依赖的其他 Spec**:
> - `member.md`(成员名册):工作区 1—N 成员;本 Spec 定义 `workspaces` 主表,成员名册表 `members` 由 member Spec 定义并外键回 `workspaces.id`。
> - `auth.md`(认证与授权):所有工作区端点的鉴权、角色校验、审计日志、限流由 auth Spec 统一提供;邀请的接受依赖 auth 的注册/登录态。
> **被依赖方**:`project.md`、`issue.md`、`label-property.md`、`agent.md` 等所有业务模块均以 `workspace_id` 作为隔离外键。

---

## 1. 功能描述

### 1.1 模块定位

工作区(Workspace)是 Mesh 的**顶层租户隔离单元**。一个工作区代表一个团队/组织的独立数据空间:其下的项目、issue、成员、标签、自定义字段全部隶属于某个工作区,跨工作区默认不可见。

工作区采用**软多租户**模型——共享数据库、按 `workspace_id` 列隔离,而非每租户独立库,兼顾运维成本与隔离强度。几乎所有业务表都携带 `workspace_id` 外键并建索引,作为隔离与查询的第一过滤条件;鉴权中间件在解析 token 后,先校验"当前 principal(人类用户或 AI agent)是否为该 workspace 成员",再放行对该工作区资源的访问。

在 Mesh 中,工作区同时是"人类 + AI agent 混合团队"的容器:agent 与人类一样,作为成员名册条目存在于工作区内(见 member.md)。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| W1 | 创建工作区 | 登录用户创建工作区,自动成为 `owner` | 创始人新建团队空间,所有数据与其它团队隔离 |
| W2 | 列出我的工作区 | 同一自然人可属于多个工作区,登录后列出并可切换 | 外包工程师同时服务 A、B 两客户,一键切换 |
| W3 | 获取单个工作区 | 支持 UUID 或 slug 两种寻址 | 通过收藏的 `/<slug>/board` 链接进入 |
| W4 | 更新工作区设置 | 名称、Logo、slug、时区、默认 locale(`settings.default_locale`,唯一 locale 真源)、杂项配置 | 管理员上传公司 Logo、修改显示名 |
| W5 | 数据强隔离 | 所有业务查询隐式带 `workspace_id` 过滤 | 即使猜到别工作区某 issue 的 UUID 也无法读取 |
| W6 | slug 标识与重定向 | 全局唯一可读标识;改名保留旧 slug → 新 id 映射 | 公司更名后旧收藏链接 301 重定向 |
| W7 | 邮箱/链接邀请 | 管理员发起邀请,带 token、有效期、次数、预设角色 | 一次性邀请链接贴群里,新人点击即加入 |
| W8 | 接受邀请 | 被邀请人注册/登录后接受,生成成员记录 | 新用户点链接 → 注册 → 自动成为 `member` |
| W9 | 撤销邀请 | 未接受的邀请可被管理员撤销 | 发错邮箱,撤回邀请 |
| W10 | 软删除/归档工作区 | 仅 owner,二次确认,软删除 + 保留期 | 项目结束后删除整个工作区 |
| W11 | 工作区级配置 | 默认状态集、默认优先级、时区、默认 locale(`settings.default_locale`)、功能开关 | 管理员自定义本工作区的流程开关 |

### 1.3 边界与非目标(明确不做什么)

- **不**定义成员角色权限矩阵、成员增删改查、资料/头像维护——归 `member.md`(本 Spec 仅给出邀请如何落地为名册条目的衔接)。
- **不**定义认证、会话、API token、RBAC 校验、审计、限流的实现——归 `auth.md`(本 Spec 仅声明各端点所需角色)。
- **不**定义 agent 的运行时/技能/模型配置——归 `agent.md`。
- **不**定义项目、issue、标签、自定义字段的业务逻辑——归各自 Spec。
- **不**实现计费/套餐结算系统——仅预留 `settings` JSONB 字段存放席位上限、功能开关等只读展示信息。
- **不**支持工作区之间的数据迁移/合并(YAGNI)。
- **不**提供独立数据库级的硬多租户隔离。

---

## 2. 数据模型

> **全局契约引用**:本模块的 schema、同租户约束、成员模型、编号、实时、API 包络/错误/分页一律以 [README.md](../README.md) §6「全局权威契约」为准,本 Spec 仅引用、不重复定义(成员模型 README §6.1、同租户复合 FK README §6.2、编号与前缀 README §6.3、实时 README §6.7、API/错误/分页 README §6.14)。

### 2.1 ER 概览(文字图)

```
                         ┌──────────────────────────────────────────┐
                         │                workspaces                 │
                         │  (顶层租户;name/slug/settings/软删除)     │
                         └───────────────┬──────────────────────────┘
                                         │ 1
                ┌────────────────────────┼─────────────────────────┐
                │ N                      │ N                       │ N
        ┌───────▼────────┐      ┌────────▼──────────┐     ┌────────▼──────────────┐
        │ workspace_     │      │ workspace_        │     │ workspace_slug_history│
        │ invitations    │      │ members(名册,     │     │ (旧 slug 重定向)       │
        │ (邀请)         │      │  见 member.md)    │     └───────────────────────┘
        └────────────────┘      └───────────────────┘
                                         │ 1
                                         ▼ N
                              projects / issues / labels …(其它模块)

users(人类登录身份,auth.md)──┐
                              ├──► members(统一名册,member.md)◄── agents(AI,agent.md)
                              └──────────── via workspace_id ─────────┘
```

要点:
- `workspaces` 是隔离根。所有业务表携带 `workspace_id` 外键。
- `users` 与 `workspaces` 是 **N—N**,通过统一名册表 `members`(member.md)落地;关联表上携带角色等信息。
- AI agent 与人类对称:agent 同样通过 `members` 条目隶属于工作区。

### 2.2 表:`workspaces`(工作区)

> SQLAlchemy 2.x 声明式约定;字段名 snake_case。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `name` | TEXT | NOT NULL,CHECK (char_length BETWEEN 1 AND 80) | — | 显示名 |
| `slug` | TEXT | NOT NULL,UNIQUE(见部分索引) | — | 全局唯一 URL 标识,`^[a-z0-9-]{2,32}$` |
| `logo_url` | TEXT | NULL | NULL | Logo 对象存储地址 |
| `timezone` | TEXT | NOT NULL | `'UTC'` | IANA 时区名 |
| `settings` | JSONB | NOT NULL | `'{"default_locale": "en"}'` | 杂项配置,见下方已知键约定;**`settings.default_locale` 是工作区 locale 的唯一真源(R3 立约,R4 收口)** |

> **Migration note(R4:旧 `default_language` 列的一次性迁移,独立于当前模型)**:当前模型**不含** `default_language` 列(R4 已从模型与全部响应示例移除,运行时代码不读不写、无长期双写)。历史上该列曾与 `settings.default_locale` 构成双真源且默认值冲突(列 `'en'` vs 键 `'zh-CN'`);升级部署执行**一次性迁移**:把存量 `workspaces.default_language` 值写入 `settings.default_locale`(仅键缺失时),随后 `ALTER TABLE workspaces DROP COLUMN default_language`;新建库直接以 `settings DEFAULT '{"default_locale": "en"}'` 建表,无迁移步骤。locale 协商一律只走 `settings.default_locale`(README §6.18 / i18n.md §2.3)。
| `inbox_issue_seq` | BIGINT | NOT NULL,CHECK (inbox_issue_seq >= 0) | `0` | 工作区级"无项目 issue"编号计数器(行锁自增,同 `projects.issue_seq`;README §6.3) |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间(NULL=未删除) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | 触发器自动维护 |

> **无项目 issue 编号**(README §6.3):无项目 issue 的 `number` 由本表 `inbox_issue_seq` **行锁自增**(`UPDATE workspaces SET inbox_issue_seq = inbox_issue_seq + 1 WHERE id=$1 RETURNING inbox_issue_seq`,与 `projects.issue_seq` 同一机制),`identifier = <工作区保留前缀> || '-' || number`;保留前缀默认 `WS`(可经 `settings.inbox_issue_prefix` 配置)。**前缀的占用/变更/保留语义见 §2.6 `identifier_prefix_registry`**(README §6.3)。工作区级 `UNIQUE(workspace_id, identifier)` 在 `issues` 上兜住一切重号(见 issue.md / README §6.3)。

**`settings` JSONB 已知键约定**(非穷尽,缺失键取默认;读写均按 key 校验类型):

| 键 | 类型 | 默认 | 说明 |
|----|------|------|------|
| `default_status_set` | string | `"basic"` | 新项目的默认状态集标识 |
| `default_priorities` | string[] | `["none","low","medium","high","urgent"]` | 默认优先级枚举 |
| `default_project_visibility` | string | `"private"` | 新建项目默认可见性 |
| `new_member_default_role` | string | `"member"` | 邀请/加入成员的默认角色 |
| `inbox_issue_prefix` | string | `"WS"` | 无项目 issue 编号保留前缀(README §6.3,大写,格式同项目前缀);**变更经 §2.6 前缀注册表:旧前缀置 `retired` 永久保留**,历史 identifier 不重编号 |
| `invitation_max_uses_cap` | int | `100` | 邀请 `max_uses` 可配置上限(LOW-2 硬化:显式值超过上限拒绝,见 §2.3) |
| `invitation_max_lifetime_hours_cap` | int | `720` | 邀请有效期小时数可配置上限(LOW-2 硬化,默认 30 天:显式 `expires_in_hours` 超过上限拒绝,见 §2.3) |
| `default_locale` | string | `"en"` | **工作区默认 locale(唯一真源,R3)**:BCP-47,README §6.18 / i18n.md locale 协商链的第三级(用户偏好 `users.settings.locale` 缺失时回退到本键,再回退系统 `en`)。R3:默认值由 `"zh-CN"` 统一为 **`"en"`**(与 i18n.md §2.1/§2.3 及 README §6.18 系统回退一致;首发语言 `zh-CN`/`en` 指支持清单,不等于默认值);既有 `default_language` 列仅迁移后弃用,协商一律只走本键,**不长期双写** |
| `default_theme` | string | `"system"` | 工作区默认主题模式 `light`/`dark`/`system`(README §6.12 主题契约:用户账号偏好 absent/`null` 时生效;三值语义与协商链见 theme.md §2.1/§2.2);非法值 → `422 invalid_theme_mode`(与 theme.md §3.3 / auth.md §3.5 统一,§3.3 已登记) |
| `seat_limit` | int \| null | `null` | 席位上限(null=不限,供计费展示) |
| `feature_flags` | object | `{}` | 工作区级产品功能开关。已知键 `autopilot` 为 boolean，缺失时按 `true` 兼容旧工作区；显式 `false` 时前端隐藏桌面/手机导航、命令与快捷键入口，直达路由显示可读的“功能未开启”态。此开关只管呈现，后端 RBAC 仍是授权边界；未知键依 PATCH 前向兼容规则透传 |

> 写入 `settings` 采用**按键浅合并**(PATCH 语义):仅覆盖请求中出现的键,未出现的键保持原值;未知键允许透传以支持前向兼容,但服务端对已知键做类型校验——**已登记具名错误码的已知键从其具名码**(`default_theme` → `422 invalid_theme_mode`、`default_locale` → `422 unsupported_locale`,与 auth.md §3.1 canonical 一致),其余已知键类型非法返回 `400 validation_error`(§3.3)。
> `feature_flags` 的已知键也在服务端校验：`autopilot` 非 boolean 返回
> `400 validation_error`。管理员在“工作区设置 → 常规”修改开关，保存时必须保留
> `settings` 和 `feature_flags` 中其他已有键，不得用整体替换丢失前向兼容配置。

### 2.3 表:`workspace_invitations`(邀请)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | |
| `email` | TEXT | NULL | NULL | 定向邀请邮箱(与 link 模式二选一,小写归一) |
| `token_hash` | TEXT | NOT NULL,UNIQUE | — | 邀请令牌的 SHA-256 哈希(不存明文) |
| `token_prefix` | TEXT | NOT NULL | — | 令牌前缀,用于列表展示与快速定位(不含秘密) |
| `role` | TEXT | NOT NULL,CHECK IN ('admin','member','guest') | `'member'` | 接受后赋予的角色(不可直接邀请为 owner) |
| `invited_by` | UUID | NOT NULL,复合 FK `(workspace_id, invited_by) → members(workspace_id, id)` | — | 邀请人(统一名册条目;README §6.2) |
| `max_uses` | INT | NOT NULL,CHECK (max_uses > 0) | `10` | 最大使用次数(**创建时未指定默认 10,不允许 NULL 不限次**——链接一旦泄漏即有次数上限,MES-4 安全约束)。**显式值受工作区可配置上限约束**(`settings.invitation_max_uses_cap`,默认 100 次):超限创建返回 422 `invitation_limits_exceeded`(LOW-2 硬化;NOT NULL 语义不变,不存在"无限") |
| `used_count` | INT | NOT NULL,CHECK (used_count >= 0) | `0` | 已使用次数(由数据库原子递增,见 §3.2) |
| `expires_at` | TIMESTAMPTZ | NOT NULL | `now() + interval '7 days'` | 过期时间(**创建时未指定默认 7 天后过期,不允许 NULL 永不过期**——链接泄漏后有失效兜底,MES-4 安全约束)。**显式 `expires_in_hours` 受工作区可配置上限约束**(`settings.invitation_max_lifetime_hours_cap`,默认 720 小时=30 天):超限创建返回 422 `invitation_limits_exceeded`(LOW-2 硬化;NOT NULL 语义不变,不存在"无限") |
| `status` | TEXT | NOT NULL,CHECK IN ('active','revoked','expired','exhausted') | `'active'` | **链接生命周期**状态(见 §4.4):`active`=可用(含原"pending"语义,创建即可用)、`revoked`=管理员撤销、`expired`=到期、`exhausted`=`used_count` 达 `max_uses`。**不再使用 `accepted`/`pending`**——多次使用链接不得翻转为单一终态(兑换记录另见 §2.4) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> 邀请令牌仅存哈希,与 auth.md 的"长期凭证只存哈希"原则一致;明文仅在创建响应/邮件链接中短暂存在。

### 2.4 表:`workspace_invitation_redemptions`(邀请兑换记录)

> **链接生命周期与兑换记录分离**(README §9 T11):`workspace_invitations` 只承载链接自身的生命周期(`active`/`revoked`/`expired`/`exhausted`)与用量计数;谁在何时凭链接入册,逐条记录在本表。多次使用链接**不会**翻转为单一终态,每个用户每个链接至多一行。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `invitation_id` | UUID | NOT NULL,**复合 FK `(workspace_id, invitation_id) → workspace_invitations(workspace_id, id)`** ON DELETE CASCADE | — | 被兑换的邀请链接(同租户复合 FK,引用 `uq_ws_invitations_ws_id`,见 §2.7;README §6.2) |
| `user_id` | UUID | NOT NULL,FK→users(id) | — | 兑换者(人类登录身份) |
| `member_id` | UUID | NOT NULL,复合 FK `(workspace_id, member_id) → members(workspace_id, id)` | — | 兑换生成的名册条目(README §6.2) |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | 冗余存储以满足复合 FK 同租户约束(README §6.2) |
| `redeemed_at` | TIMESTAMPTZ | NOT NULL | `now()` | 兑换时间 |

**表级约束**:`UNIQUE (invitation_id, user_id)` —— 同一用户同一链接至多一行;同一用户重复接受 = no-op,返回既有名册条目(见 §3.2 幂等性)。

### 2.5 表:`workspace_slug_history`(slug 重定向)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | |
| `old_slug` | TEXT | NOT NULL,UNIQUE | — | 被释放的旧 slug |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | 释放时间 |

### 2.6 表:`identifier_prefix_registry`(工作区级 identifier 前缀注册表)

> **本表由本 Spec owns**,是 README §6.3「前缀注册(工作区级排他)」的实现载体:一切 identifier 前缀——项目 `key`、**当前与历史的**收件箱前缀——统一登记于此,工作区级永久排他。如此 `issues` 上 `UNIQUE(workspace_id, identifier)` 不再在创建 issue 时被"随机"违反(README §6.3)。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | 所属工作区 |
| `key` | TEXT | NOT NULL | — | 前缀,格式同项目前缀 `^[A-Z][A-Z0-9_]{1,11}$` |
| `kind` | TEXT | NOT NULL,CHECK IN ('project','inbox','retired') | — | `project`=项目前缀;`inbox`=当前收件箱前缀;`retired`=已退役前缀(永久保留) |
| `project_id` | UUID | NULL,复合 FK `(workspace_id, project_id) → projects(workspace_id, id)` ON DELETE SET NULL (project_id) | — | 前缀当前/历史归属项目(**创建 `kind='project'` 行时由服务层保证非空**;项目物理清理后由列级 `ON DELETE SET NULL (project_id)` 置空,前缀仍永久占用——此时 `kind='project' AND project_id IS NULL` 即"前缀随项目清理转为永久保留"后态;列级置空见 README §6.2 第 6 条) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | 占用时间 |

**表级约束**:`CHECK (kind IN ('project','inbox','retired'))`(不用 `kind='project' → project_id NOT NULL` 的强 CHECK——项目物理清理时列级 `ON DELETE SET NULL (project_id)` 会把 `project_id` 置空,该后态合法且前缀依旧永久占用;创建时 `kind='project'` 行 `project_id` 非空由服务层保证,见语义 ①);`UNIQUE (workspace_id, key)`——前缀工作区级**永久排他**(DDL 见 §2.7)。

**语义(逐条,README §6.3)**:
1. **创建项目占用 `key`**:同事务 INSERT 一条 `kind='project'` 注册行;与任一在册 `key`(含 `retired` 与当前 `inbox` 前缀)冲突 → 409 `project_key_taken`(project.md §3.3)。
2. **变更 `settings.inbox_issue_prefix`**:旧 inbox 行 `UPDATE ... SET kind='retired'`(**永久保留,历史 identifier 不重编号**)+ INSERT 新 `kind='inbox'` 行;新 `key` 与任一在册行(含 `retired`)冲突 → 422 `prefix_reserved`(§3.3)。
3. **工作区创建时**:以默认/配置的收件箱前缀(默认 `WS`)播种首行 `kind='inbox'`(随工作区创建事务同提交)。
4. **软删除/归档项目**:注册行保留(或置 `retired` 并保留 `project_id`),`key` 永不释放——与 `projects` 上**非部分唯一索引** `uq_projects_key`(即 `UNIQUE (workspace_id, key)`,不带 `WHERE deleted_at IS NULL`,project.md §2.3)**双重保证**前缀不可复用(README §6.3)。

### 2.7 索引与约束

```sql
-- slug 唯一性只在未软删除时生效,允许删除后释放
CREATE UNIQUE INDEX uq_workspaces_slug ON workspaces(slug) WHERE deleted_at IS NULL;
CREATE INDEX idx_workspaces_deleted_at ON workspaces(deleted_at);

CREATE INDEX idx_ws_invitations_workspace ON workspace_invitations(workspace_id, status);
CREATE UNIQUE INDEX uq_ws_invitations_token_hash ON workspace_invitations(token_hash);
CREATE UNIQUE INDEX uq_ws_invitations_ws_id ON workspace_invitations(workspace_id, id);  -- 供 redemptions 复合 FK 引用(README §6.2)
CREATE INDEX idx_ws_invitations_email ON workspace_invitations(workspace_id, email)
  WHERE email IS NOT NULL;

-- 邀请兑换记录:同一用户同一链接至多一行(接受邀请幂等的数据库基础)
CREATE UNIQUE INDEX uq_ws_inv_redemptions_inv_user
  ON workspace_invitation_redemptions(invitation_id, user_id);
CREATE INDEX idx_ws_inv_redemptions_member
  ON workspace_invitation_redemptions(workspace_id, member_id);

CREATE UNIQUE INDEX uq_slug_history_old_slug ON workspace_slug_history(old_slug);

-- 前缀注册表(§2.6):前缀工作区级永久排他 + 按工作区/kind 检索(README §6.3)
CREATE UNIQUE INDEX uq_prefix_registry_ws_key ON identifier_prefix_registry(workspace_id, key);
CREATE INDEX idx_prefix_registry_ws ON identifier_prefix_registry(workspace_id, kind);
```

应用层 CHECK(亦可建 partial unique 防止同邮箱重复 active):同一 `workspace_id + email` 不允许同时存在多条 `status='active'` 的邀请(应用层校验 + 事务,冲突返回 409):
```sql
CREATE UNIQUE INDEX uq_ws_invitations_active_email
  ON workspace_invitations(workspace_id, email)
  WHERE email IS NOT NULL AND status = 'active';
```

### 2.8 与其他模块的外键关系

| 来源表 | 外键 | 目标 | 说明 |
|--------|------|------|------|
| `members`(member.md) | `workspace_id` | `workspaces.id` | 名册隶属工作区 |
| `workspace_invitations.invited_by` | 复合 FK `(workspace_id, invited_by)` → `members(workspace_id, id)` | 统一名册 | 邀请人(README §6.2) |
| `workspace_invitation_redemptions.invitation_id` | 复合 FK `(workspace_id, invitation_id)` → `workspace_invitations(workspace_id, id)` | 邀请链接 | 被兑换邀请(同租户复合 FK,README §6.2) |
| `workspace_invitation_redemptions.member_id` | 复合 FK `(workspace_id, member_id)` → `members(workspace_id, id)` | 统一名册 | 兑换生成的名册条目(README §6.2) |
| `workspace_invitation_redemptions.user_id` | → `users.id` | auth.md | 兑换者登录身份 |
| `identifier_prefix_registry.project_id` | 复合 FK `(workspace_id, project_id)` → `projects(workspace_id, id)` ON DELETE SET NULL (project_id) | projects(project.md) | 前缀归属项目(列级置空,README §6.2 第 6 条) |
| `projects` / `issues` / `labels` / `custom_field_defs` | `workspace_id` | `workspaces.id` | 业务隔离 |
| `api_tokens`(auth.md) | `workspace_id` | `workspaces.id` | 令牌归属工作区 |
| `audit_logs`(auth.md) | `workspace_id` | `workspaces.id` | 工作区级审计 |

> `workspaces.id` 的删除策略:业务表多为 `ON DELETE CASCADE`(随软删除后的硬清理一并清除);`members` 为 `ON DELETE CASCADE`。软删除期间外键依旧有效。

---

## 3. 接口设计

REST 基础路径 `/api/v1`;鉴权 `Authorization: Bearer <token>`(会话 JWT 或 API token,见 auth.md)。时间一律 RFC3339 UTC。**成功包络、游标分页、错误信封、HTTP 语义、幂等写一律以 README §6.14 为权威**,本 Spec 仅列模块专属错误码,不重复定义公共契约。

### 3.1 REST 端点清单

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| POST | `/workspaces` | 创建工作区(创建者成 owner) | 已登录 |
| GET | `/workspaces` | 列出当前 principal 所属工作区 | 已登录 |
| GET | `/workspaces/{id}` | 获取单个工作区(UUID) | 成员 |
| GET | `/workspaces/by-slug/{slug}` | 按 slug 解析工作区 | 成员 |
| PATCH | `/workspaces/{id}` | 更新名称/slug/Logo/时区/设置 | admin |
| DELETE | `/workspaces/{id}` | 软删除工作区 | owner |
| POST | `/workspaces/{id}/restore` | 恢复软删除(保留期内) | owner |
| POST | `/workspaces/{id}/invitations` | 创建邀请(邮箱批量 / 链接) | admin |
| GET | `/workspaces/{id}/invitations` | 列出邀请 | admin |
| DELETE | `/workspaces/{id}/invitations/{inv_id}` | 撤销邀请 | admin |
| POST | `/invitations/accept` | 凭 token 接受邀请(入册事务内**同事务为人类新成员播种 onboarding 清单**,onboarding.md §3.5,R3) | 已登录 |
| GET | `/invitations/preview?token=` | 预览邀请(工作区名/角色/是否有效;**MES-76 H2:返回 `appearance.default_theme`(工作区默认主题,供未登录邀请接受页主题协商,theme.md §2.2/§3.1)——非敏感展示偏好,与既有工作区名同暴露面**;**仍仅返回有限公开字段,不开放完整 workspace detail,防工作区信息枚举**) | 公开(凭不可枚举邀请 token) |

> 成员名册的读写端点见 member.md(`GET/PATCH/DELETE /workspaces/{id}/members`)。

### 3.2 请求/响应 JSON 示例

**创建工作区** `POST /api/v1/workspaces`
```json
// Request
{ "name": "Acme Team", "slug": "acme", "timezone": "Asia/Shanghai" }

// 201 Response(R4:响应不含 default_language 字段——locale 唯一真源为 settings.default_locale)
{
  "id": "0d6f1c2a-0000-4000-8000-0000000000e2",
  "name": "Acme Team",
  "slug": "acme",
  "logo_url": null,
  "timezone": "Asia/Shanghai",
  "settings": { "default_locale": "en", "default_theme": "system" },
  "my_role": "owner",
  "created_at": "2026-07-24T10:00:00Z",
  "updated_at": "2026-07-24T10:00:00Z"
}
```

**列出工作区(游标分页)** `GET /api/v1/workspaces?limit=20&cursor=eyJpZCI6...`
```json
{
  "data": [
    { "id": "0d6f...e2", "name": "Acme Team", "slug": "acme", "my_role": "owner",
      "logo_url": null, "created_at": "2026-07-24T10:00:00Z" }
  ],
  "next_cursor": "eyJpZCI6IjBkNmY..."
}
```

**更新 slug** `PATCH /api/v1/workspaces/{id}`
```json
// Request
{ "slug": "acme-corp" }
// 200 Response:返回更新后的工作区对象;旧 slug 自动写入 workspace_slug_history
```

**创建邀请** `POST /api/v1/workspaces/{id}/invitations`
```json
// Request(邮箱批量)
{ "emails": ["jane@acme.com", "john@acme.com"], "role": "member", "expires_in_hours": 72 }
// Request(链接模式)
{ "role": "member", "max_uses": 10, "expires_in_hours": 168 }
// 显式 max_uses / expires_in_hours 受工作区可配置上限约束(settings.invitation_max_uses_cap 默认 100、
// invitation_max_lifetime_hours_cap 默认 720 小时);超限返回 422 invitation_limits_exceeded(§2.3/§3.3)

// 201 Response
{
  "data": [
    { "id": "inv-uuid-1", "email": "jane@acme.com", "role": "member", "status": "active",
      "invite_link": "/invite/invtk_Ab3Xy9...", "expires_at": "2026-07-27T10:00:00Z" }
  ],
  "next_cursor": null
}
```
> `invite_link` 中的明文 token 仅在创建响应与邀请邮件中出现;数据库仅存 `token_hash`。

**接受邀请** `POST /api/v1/invitations/accept`
```json
// Request
{ "token": "invtk_Ab3Xy9..." }
// 200 Response:返回新创建(或既有)名册条目与所属工作区
{ "member": { "id": "mem-uuid", "role": "member", "status": "active" },
  "workspace": { "id": "0d6f...e2", "name": "Acme Team", "slug": "acme" } }
```

> **接受的原子用量递增(数据库强制,无应用层 check-then-write)**:接受邀请在**单一事务**内执行如下条件 UPDATE,把"是否可用 / 是否还有余量 / 是否过期"全部下推到 WHERE,杜绝并发超卖(README §9 T11):
>
> ```sql
> UPDATE workspace_invitations
>    SET used_count = used_count + 1, updated_at = now()
>  WHERE id = $invitation_id AND status = 'active'
>    AND used_count < max_uses
>    AND expires_at > now()
> RETURNING used_count, max_uses, workspace_id, role;
> ```
>
> - **0 行返回** → 邀请不可用(已撤销 / 已过期 / 已用尽 / 不存在),返回 `422 invitation_invalid`。
> - **递增成功** → 同事务内 INSERT `workspace_invitation_redemptions`(一行)与 `members`(一行,角色取邀请预设值);`UNIQUE(invitation_id, user_id)` 命中冲突即视为**同一用户重复接受**,回滚本次递增并返回既有名册条目(no-op,见下"幂等性")。
> - **递增后 `used_count = max_uses`** → 同事务把 `status` 惰性/显式置为 `exhausted`(链接生命周期终态,见 §4.4)。`max_uses`/`expires_at` 恒 NOT NULL,WHERE 不含 `IS NULL` 死分支(§2.3)。

**获取单个工作区** `GET /api/v1/workspaces/{id}`(UUID 或 `by-slug/{slug}` 等价)
```json
{
  "id": "0d6f...e2",
  "name": "Acme Team",
  "slug": "acme",
  "logo_url": "https://cdn.example/logo.png",
  "timezone": "Asia/Shanghai",
  "settings": { "default_locale": "en", "default_theme": "dark", "default_status_set": "basic", "new_member_default_role": "member",
                "seat_limit": 50, "feature_flags": { "autopilot": true } },
  "my_role": "admin",
  "created_at": "2026-07-24T10:00:00Z",
  "updated_at": "2026-07-24T11:30:00Z"
}
```

**预览邀请(公开,仅有限字段)** `GET /api/v1/invitations/preview?token=invtk_Ab3...`
```json
// 200 Response(未登录亦可,用于落地页展示;不暴露内部 id 之外敏感信息)
// appearance.default_theme(MES-76 H2/R2-H5):供未登录邀请接受页主题协商链第 2 级读取
// (theme.md §2.2/§2.3 首帧「精确注入」链路),非敏感展示偏好,与工作区名同暴露面
{ "valid": true, "workspace_name": "Acme Team", "workspace_logo_url": "...",
  "role": "member", "expires_at": "2026-07-27T10:00:00Z",
  "appearance": { "default_theme": "dark" } }
// 无效/过期/撤销时:
{ "valid": false, "reason": "expired" }   // reason ∈ {expired, revoked, exhausted, not_found}
```

**恢复软删除** `POST /api/v1/workspaces/{id}/restore`(仅 owner,保留期内)
```json
// 200 Response:返回工作区对象,deleted_at 置回 null
```

**幂等性**:接受邀请与创建邀请均做幂等保护——
- **接受邀请**:`workspace_invitation_redemptions.UNIQUE(invitation_id, user_id)` 保证同一用户对同一链接至多一行;并发或重复接受同一链接,先成者建名册,后成者命中唯一约束 → **no-op,直接返回既有名册条目**(不重复递增 `used_count`、不重复建名册)。
- **创建邀请**:同工作区同邮箱重复 `active` 邀请返回 409(见 §2.7 partial unique)。

### 3.3 错误码表

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | slug 含大写/超长、name 超长等请求级格式错误 |
| 422 | `invalid_timezone` | `timezone` 非合法 IANA 时区(与 auth.md §3.1 canonical 对齐,README §6.18) |
| 422 | `unsupported_locale` | `settings.default_locale` 不在受支持 locale 清单内(与 auth.md §3.1 / i18n.md §3.5 对齐;**R4:locale 写入校验统一用具名 422,不再用 400 validation_error**) |
| 422 | `invalid_theme_mode` | `settings.default_theme` 不在 `{light,dark,system}`(theme.md §3.3 唯一权威,本表同步登记;MES-76 H1 三处 owner 契约统一码) |
| 401 | `unauthorized` | token 缺失/失效 |
| 403 | `forbidden` | 非成员访问 / 角色不足(如非 owner 删除) |
| 404 | `not_found` | 工作区不存在或对当前 principal 不可见 |
| 409 | `slug_taken` | slug 已被占用 |
| 409 | `conflict` | 同邮箱已存在 `active` 邀请 |
| 422 | `invitation_invalid` | 邀请不可用:已过期(`expired`)/已撤销(`revoked`)/已用尽(`exhausted`,即原子递增 0 行)/不存在 |
| 422 | `invitation_limits_exceeded` | 显式 `max_uses`/`expires_in_hours` 超过工作区可配置上限(`settings.invitation_max_uses_cap`/`invitation_max_lifetime_hours_cap`,默认 100 / 720;LOW-2 硬化,§2.3) |
| 422 | `prefix_reserved` | 收件箱前缀变更与在册前缀(含 `retired` 历史前缀)冲突(§2.6 注册表,README §6.3) |
| 429 | `rate_limited` | 触发限流(见 auth.md) |

### 3.4 分页 / 鉴权 / 限流

- **分页**:游标分页。请求 `?limit=N&cursor=<opaque>`;响应 `{"data":[...],"next_cursor"}`(`next_cursor` 为 null 表示末页)。游标内部为 base64 编码的 `(sort_key, id)`,默认按 `created_at DESC, id` 排序,保证稳定无重复。
- **鉴权**:中间件链路:解析 token → 得 principal(user 或 agent)→ 校验该 principal 对路径中 workspace 的成员资格与角色 → 放行。写操作端点额外做角色校验(删除工作区需 `owner`,设置/邀请需 `admin`)。
- **限流**:写端点(创建/邀请)按 principal + IP 限流;邀请创建额外限制单次批量邮箱数(≤ 50)。具体阈值与响应头见 auth.md §限流。

### 3.5 WebSocket 实时事件

> **统一实时契约见 README §6.7**(本 Spec 不重复定义):`seq` **一律为频道内单调递增**(持久化于 `realtime_events`,无"全局 seq");客户端断线重连带 `resume_from=<last_seq+1>` 补发;游标过旧(早于保留窗口)收 `resync_required` + REST 对账水位;每次订阅 `workspace:{id}` 频道时**重新做资源级授权**。

连接 `/ws`(握手鉴权见 auth.md),客户端订阅频道 `workspace:{id}`。事件命名 `<entity>.<action>`,每事件携带频道内单调递增 `seq`,客户端断线后凭 `resume_from` 请求重放(README §6.7)。

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `workspace.updated` | 设置/名称/slug 变更 | `workspace_id`, `changes` |
| `workspace.deleted` | 工作区被软删除 | `workspace_id` |
| `member.added` | 新成员(人或 agent)入册 | `member_id`, `member_type`, `role` |
| `member.removed` | 成员被移除 | `member_id` |
| `member.role_changed` | 角色变更 | `member_id`, `old_role`, `new_role` |
| `invitation.redeemed` | 邀请链接被兑换(管理员侧;对应一条 redemption 记录) | `invitation_id`, `member_id`, `used_count` |

**降级方案**:WebSocket 不可用时,退化为 30s 轮询 `GET /workspaces/{id}` 与名册接口。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
[工作区切换器(左上角下拉)]
   └── 当前工作区
        ├── 收件箱 / 我的任务
        ├── 项目(列表)
        ├── 看板 / 视图
        ├── 成员(人类与 AI agent 同列,见 member.md)
        └── 设置(admin 可见)
             ├── 基本信息(名称/Logo/slug/时区/语言)
             ├── 成员与角色(→ member.md)
             ├── 邀请
             ├── 状态 / 标签 / 自定义字段(→ 其它 Spec)
             └── 危险操作(归档/删除)
```

### 4.2 关键组件

- **工作区切换器**:左上角下拉,列出当前用户所有工作区,顶部"创建工作区"。切换后整页上下文(项目、成员、看板)随之刷新。
- **工作区首页**:欢迎信息、工作区元数据、issue 摘要与快捷动作通过共享 PageHeader/Card/Button/Input/Skeleton 组合；不得在页面内重建基础控件。路由中的 `workspaceSlug` 是页面数据作用域真源，多工作区场景不得回退到错误 membership。
- **创建向导**:模态框,步骤 名称 → slug(实时校验占用,绿勾/红叉)→ 邀请成员(可跳过)→ 完成。
- **基本信息表单**:名称、Logo 上传、slug 输入框(带可用性校验与"旧链接将自动重定向"提示)、时区下拉、语言下拉。
- **邀请面板**:多邮箱输入 chip(回车成 chip,支持粘贴批量)、角色选择、"生成邀请链接"按钮;下方待处理邀请列表(邮箱/角色/状态/过期时间/撤销按钮)。
- **危险操作区**:删除/归档需输入工作区 slug 二次确认,仅 owner 可见可操作。

### 4.3 关键交互流程

**创建工作区**:点击切换器 → "新建" → 输入名称(自动 slug 建议)→ slug 实时去重校验 → (可选)邀请 → 完成,自动进入新工作区,当前用户成为 `owner`。

**邀请成员**:设置 → 邀请 → 输入邮箱(或生成链接)→ 选角色与有效期 → 发送;即时生成邀请行(`status=active`,创建即可用)并触发邮件。被邀请人邮件中点击链接 → 未注册则走注册流(auth.md)→ 注册/登录后接受(原子递增用量并落 redemption 记录,§3.2)→ 出现在成员名册。

**slug 修改**:输入新 slug → 实时校验 → 保存 → 提示"已保留旧链接重定向"。

**删除工作区**:危险操作区 → 输入 slug 确认 → 软删除 → 全员收到 `workspace.deleted` 事件与通知;保留期内 owner 可恢复。

### 4.4 状态流转(邀请链接生命周期)

> 链接生命周期(`workspace_invitations.status`)与兑换记录(`workspace_invitation_redemptions`,§2.4)**分离**:链接只在 `active`/`revoked`/`expired`/`exhausted` 间迁移;**每次被接受只新增一条 redemption 记录并原子递增 `used_count`,不把链接翻转为单一"已接受"终态**(README §9 T11)。

```
active ──被兑换(接受,写 redemption + used_count+1)──► active(仍可用,直到余量耗尽)
active ──递增后 used_count = max_uses──► exhausted(终态,惰性/显式置位)
active ──撤销(管理员)──────────────► revoked(终态)
active ──到期(定时/惰性)───────────► expired(终态)
```
> - `active` = 可用(覆盖旧"pending"语义,创建即可用);**已无 `pending`/`accepted` 状态**。
> - `max_uses`/`expires_at` 恒 NOT NULL(§2.3):链接仅在 `exhausted`(用尽)/`expired`(到期)/`revoked`(撤销)进入终态,不存在"无限"分支。
> - 定向邮箱邀请(`max_uses` 通常为 1)被兑换一次后即 `exhausted`,效果等价于旧"已接受",但语义统一为"用量耗尽"。

工作区自身:`active`(默认)→ `deleted`(软删除,`deleted_at` 非空)→ 保留期内 `restore` 回到 `active`,超保留期硬删除。

### 4.5 实时性与通知

- **实时**:走 WebSocket(§3.5,统一契约 README §6.7)。名册变更、设置变更、邀请被兑换(`invitation.redeemed`)均实时推送;降级 30s 轮询。
- **通知触发点**:
  - 被邀请:邮件 + 站内通知("X 邀请你加入 Acme Team")。
  - 角色变更:站内通知(见 member.md)。
  - 邀请即将过期(可选):提醒邀请人。
  - 工作区被删除/归档:全员站内 + 邮件通知。

---

## 5. 验收标准

### 5.1 功能性

- [ ] 登录用户可创建工作区,创建成功后其在 `members` 中的角色为 `owner`。
- [ ] 同一用户可属于多个工作区,`GET /workspaces` 正确列出全部且携带 `my_role`。
- [ ] slug 创建/修改时实时校验唯一性;非法格式(大写、超长、特殊字符)返回 400 `validation_error`。
- [ ] slug 被占用返回 409 `slug_taken`。
- [ ] 修改 slug 后,旧 slug 写入 `workspace_slug_history`,`GET /workspaces/by-slug/{旧slug}` 解析到新工作区(或 301 重定向)。
- [ ] 软删除的工作区不出现在列表;保留期内 owner 可 `restore`。
- [ ] 仅 `owner` 可删除工作区;`admin`/`member` 删除返回 403。
- [ ] 邮箱邀请:同工作区同邮箱已有 `active` 邀请时返回 409 `conflict`(§2.7 partial unique 兜底)。
- [ ] 邀请令牌仅存 SHA-256 哈希,创建响应/邮件返回明文;`token_prefix` 可展示。
- [ ] 邀请 `status` 枚举为 `active`/`revoked`/`expired`/`exhausted`,创建即为 `active`;**不存在 `pending`/`accepted` 状态**(链接生命周期与兑换记录分离,§2.3/§2.4/§4.4)。
- [ ] 接受有效邀请在**单一事务**内:条件 UPDATE 原子递增 `used_count`(§3.2 SQL,无应用层 check-then-write)+ INSERT 一条 `workspace_invitation_redemptions` + INSERT 一条 `members`(角色为邀请预设值)。
- [ ] **接受邀请幂等**:`UNIQUE(invitation_id, user_id)` 下,同一用户重复/并发接受同一链接 = no-op,返回既有名册条目,`used_count` 不重复递增。
- [ ] **并发最后一名额(README §9 T11)**:`max_uses=1` 链接被两用户同时接受,恰一人成功入册、另一人 `422 invitation_invalid`;`used_count` 永不超 `max_uses`。
- [ ] `used_count` 递增到 `max_uses` 后链接 `status` 惰性/显式置 `exhausted`;`max_uses`/`expires_at` 恒 NOT NULL,无"无限"分支;接受 SQL 的 `IS NULL` 死分支已删除(§2.3/§3.2)。
- [ ] 不可用邀请(已 `expired`/`revoked`/`exhausted`/不存在,即原子递增返回 0 行)接受返回 422 `invitation_invalid`。
- [ ] **邀请上限硬化(LOW-2)**:显式 `max_uses`/`expires_in_hours` 超过 `settings.invitation_max_uses_cap`/`invitation_max_lifetime_hours_cap`(默认 100 / 720,可调)时创建返回 422 `invitation_limits_exceeded`;未指定时取默认值(10 次 / 7 天)且不被上限拒绝。
- [ ] 撤销邀请(`revoked`)后该 token 立即失效。
- [ ] 邀请的 `role` 不可为 `owner`。
- [ ] `workspaces.inbox_issue_seq` 行锁自增,并发创建无项目 issue(≥10)在 `UNIQUE(workspace_id, identifier)` 下无重号(README §6.3 / §9 T15);保留前缀默认 `WS`。
- [ ] `workspace_invitations.invited_by` 与 `workspace_invitation_redemptions.member_id` 为复合 FK → `members(workspace_id, id)`,跨工作区引用被数据库拒绝(README §6.2 / §9 T1)。
- [ ] **redemption 同租户复合 FK(README §6.2 / §9 T1)**:`workspace_invitation_redemptions.invitation_id` 以复合 FK `(workspace_id, invitation_id) → workspace_invitations(workspace_id, id)` 引用(§2.4,引用 `uq_ws_invitations_ws_id`),跨工作区兑换插入被数据库拒绝。
- [ ] **前缀注册表(README §6.3 / §9 T19)**:`identifier_prefix_registry.UNIQUE(workspace_id, key)` 使项目 `key` 与收件箱前缀(含 `retired` 历史前缀)工作区级排他——冲突分别返回 409 `project_key_taken`(project.md §3.3)/ 422 `prefix_reserved`;变更收件箱前缀后旧前缀置 `retired` 永久保留、历史 identifier 不重编号;工作区创建时播种默认 `WS` 的 `kind='inbox'` 首行。
- [ ] 非成员访问任意工作区资源返回 404(不泄露存在性)。
- [ ] 所有业务查询隐式按 `workspace_id` 过滤,跨工作区不可读。
- [ ] **workspace locale 单一真源(R4,HIGH-3,集成测试 T32)**:当前模型**无 `default_language` 列**(迁移说明见 §2.2 migration note:存量值一次性写入 `settings.default_locale` 后删列,无双写期);创建/读取响应**只返回 `settings.default_locale`**(默认 `en`),不含任何 `default_language` 字段;`PATCH` 写 `settings.default_locale` 按键浅合并生效,非法 locale → `422 unsupported_locale`、非法时区 → `422 invalid_timezone`(与 auth.md §3.1 canonical 对齐);locale 协商链按 README §6.18 只走本键。

### 5.2 性能

- [ ] `GET /workspaces`(含 100 个工作区账号)P95 < 200ms。
- [ ] 单个工作区设置读/写 P95 < 150ms。
- [ ] slug 唯一性校验在 `uq_workspaces_slug` 部分索引上完成,无全表扫描。
- [ ] 游标分页在百万级业务行下保持稳定(无 OFFSET 深翻页)。

### 5.3 安全

- [ ] 鉴权中间件对每个工作区端点校验成员资格与角色,缺一返回 401/403。
- [ ] 邀请 token、所有长期凭证仅存哈希,日志与响应不回显明文(除创建一次性返回)。
- [ ] 删除/归档等危险操作仅 owner 可触发,且需二次确认。
- [ ] 邀请创建、工作区创建受 auth.md 限流约束,超限返回 429 + `Retry-After`。
- [ ] 错误信息不泄露其它工作区存在性或内部细节。
- [ ] **无前缀端点 404 口径统一(产品级)**:所有经 SECURITY DEFINER 解析租户的无前缀资源端点(`/issues/{id}`、`/statuses/{id}`、`/issue-templates/{id}`、`/projects/{id}`、`/milestones/{id}`、`/cycles/{id}`、`/project-templates/{id}`、`/labels/{id}`、`/custom-fields/{id}[/options[/{opt_id}]]`、`/views/{id}`、`/attachments/{id}`(及 `/complete`、`/abort`、`/download`、`/thumbnail` 子路径)、`/multipart/{id}/parts|complete`、`/issues|comments/{id}/attachments`;`POST /attachments/upload-requests` 的 `link_to` 派生租户分支同口径,取宿主资源消息)对「id 不存在」「存在但非成员」「软删除」三态返回**同一资源级 404 消息**(如 `project not found`);成员门的 `workspace not found` 在路由层转写为资源消息,两态不可区分,消除任意 UUID 的资源存在性 oracle。**例外**:调用方指名工作区的路径(带 `/workspaces/{id}` 前缀、`upload-requests` 显式 `workspace_id` 分支、token 自身工作区)保持 `workspace not found` 口径(与 `require_workspace` 一致,指名即无存在性推断)。
- [ ] **用户可控 URL scheme 校验**:`logo_url` 等用户可控 URL 字段服务端校验 scheme,禁止 `javascript:`/`data:`,**仅允许 `https`**(README §6.16:统一 https-only)。

### 5.4 实时

- [ ] 工作区设置变更后,在线成员 1s 内收到 `workspace.updated`。
- [ ] 成员入册/移除/角色变更触发对应 `member.*` 事件(与 member.md 一致)。
- [ ] 邀请被兑换,管理员侧实时收到 `invitation.redeemed`(含 `used_count`)。
- [ ] 客户端断线重连后,凭 `resume_from` 可重放缺失事件,无丢失无重复;游标过旧收 `resync_required` 并对账恢复(README §6.7)。
- [ ] WebSocket 不可用时,30s 轮询降级路径功能等价。
