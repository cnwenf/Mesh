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
| W4 | 更新工作区设置 | 名称、Logo、slug、时区、默认语言、杂项配置 | 管理员上传公司 Logo、修改显示名 |
| W5 | 数据强隔离 | 所有业务查询隐式带 `workspace_id` 过滤 | 即使猜到别工作区某 issue 的 UUID 也无法读取 |
| W6 | slug 标识与重定向 | 全局唯一可读标识;改名保留旧 slug → 新 id 映射 | 公司更名后旧收藏链接 301 重定向 |
| W7 | 邮箱/链接邀请 | 管理员发起邀请,带 token、有效期、次数、预设角色 | 一次性邀请链接贴群里,新人点击即加入 |
| W8 | 接受邀请 | 被邀请人注册/登录后接受,生成成员记录 | 新用户点链接 → 注册 → 自动成为 `member` |
| W9 | 撤销邀请 | 未接受的邀请可被管理员撤销 | 发错邮箱,撤回邀请 |
| W10 | 软删除/归档工作区 | 仅 owner,二次确认,软删除 + 保留期 | 项目结束后删除整个工作区 |
| W11 | 工作区级配置 | 默认状态集、默认优先级、时区、默认语言、功能开关 | 管理员自定义本工作区的流程开关 |

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
| `default_language` | TEXT | NOT NULL | `'en'` | 默认语言(BCP-47 短码) |
| `settings` | JSONB | NOT NULL | `'{}'` | 杂项配置,见下方已知键约定 |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间(NULL=未删除) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | 触发器自动维护 |

**`settings` JSONB 已知键约定**(非穷尽,缺失键取默认;读写均按 key 校验类型):

| 键 | 类型 | 默认 | 说明 |
|----|------|------|------|
| `default_status_set` | string | `"basic"` | 新项目的默认状态集标识 |
| `default_priorities` | string[] | `["none","low","medium","high","urgent"]` | 默认优先级枚举 |
| `default_project_visibility` | string | `"private"` | 新建项目默认可见性 |
| `new_member_default_role` | string | `"member"` | 邀请/加入成员的默认角色 |
| `seat_limit` | int \| null | `null` | 席位上限(null=不限,供计费展示) |
| `feature_flags` | object | `{}` | 功能开关位,如 `{"autopilot": true}` |

> 写入 `settings` 采用**按键浅合并**(PATCH 语义):仅覆盖请求中出现的键,未出现的键保持原值;未知键允许透传以支持前向兼容,但服务端对已知键做类型校验,非法返回 400。

### 2.3 表:`workspace_invitations`(邀请)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | |
| `email` | TEXT | NULL | NULL | 定向邀请邮箱(与 link 模式二选一,小写归一) |
| `token_hash` | TEXT | NOT NULL,UNIQUE | — | 邀请令牌的 SHA-256 哈希(不存明文) |
| `token_prefix` | TEXT | NOT NULL | — | 令牌前缀,用于列表展示与快速定位(不含秘密) |
| `role` | TEXT | NOT NULL,CHECK IN ('admin','member','guest') | `'member'` | 接受后赋予的角色(不可直接邀请为 owner) |
| `invited_by` | UUID | NOT NULL,FK→members(id) | — | 邀请人(统一名册条目) |
| `max_uses` | INT | NULL,CHECK (max_uses > 0) | `10` | 最大使用次数(**创建时未指定则默认 10,不允许 NULL 不限次**;链接一旦泄漏即有次数上限) |
| `used_count` | INT | NOT NULL,CHECK (used_count >= 0) | `0` | 已使用次数 |
| `expires_at` | TIMESTAMPTZ | NULL | `now() + 7 days` | 过期时间(**创建时未指定则默认 7 天后过期,不允许 NULL 永不过期**;链接泄漏后有失效兜底) |
| `status` | TEXT | NOT NULL,CHECK IN ('pending','accepted','revoked','expired') | `'pending'` | |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> 邀请令牌仅存哈希,与 auth.md 的"长期凭证只存哈希"原则一致;明文仅在创建响应/邮件链接中短暂存在。

### 2.4 表:`workspace_slug_history`(slug 重定向)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | |
| `old_slug` | TEXT | NOT NULL,UNIQUE | — | 被释放的旧 slug |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | 释放时间 |

### 2.5 索引与约束

```sql
-- slug 唯一性只在未软删除时生效,允许删除后释放
CREATE UNIQUE INDEX uq_workspaces_slug ON workspaces(slug) WHERE deleted_at IS NULL;
CREATE INDEX idx_workspaces_deleted_at ON workspaces(deleted_at);

CREATE INDEX idx_ws_invitations_workspace ON workspace_invitations(workspace_id, status);
CREATE UNIQUE INDEX uq_ws_invitations_token_hash ON workspace_invitations(token_hash);
CREATE INDEX idx_ws_invitations_email ON workspace_invitations(workspace_id, email)
  WHERE email IS NOT NULL;

CREATE UNIQUE INDEX uq_slug_history_old_slug ON workspace_slug_history(old_slug);
```

应用层 CHECK(亦可建 partial unique 防止同邮箱重复 pending):同一 `workspace_id + email` 不允许同时存在多条 `status='pending'` 的邀请(应用层校验 + 事务,冲突返回 409)。

### 2.6 与其他模块的外键关系

| 来源表 | 外键 | 目标 | 说明 |
|--------|------|------|------|
| `members`(member.md) | `workspace_id` | `workspaces.id` | 名册隶属工作区 |
| `workspace_invitations.invited_by` | → `members.id` | 统一名册 | 邀请人 |
| `projects` / `issues` / `labels` / `custom_field_defs` | `workspace_id` | `workspaces.id` | 业务隔离 |
| `api_tokens`(auth.md) | `workspace_id` | `workspaces.id` | 令牌归属工作区 |
| `audit_logs`(auth.md) | `workspace_id` | `workspaces.id` | 工作区级审计 |

> `workspaces.id` 的删除策略:业务表多为 `ON DELETE CASCADE`(随软删除后的硬清理一并清除);`members` 为 `ON DELETE CASCADE`。软删除期间外键依旧有效。

---

## 3. 接口设计

REST 基础路径 `/api/v1`;鉴权 `Authorization: Bearer <token>`(会话 JWT 或 API token,见 auth.md)。时间一律 RFC3339 UTC。统一错误信封 `{"error":{"code","message","details"}}`。

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
| POST | `/invitations/accept` | 凭 token 接受邀请 | 已登录 |
| GET | `/invitations/preview?token=` | 预览邀请(工作区名/角色/是否有效) | 公开(仅返回有限字段) |

> 成员名册的读写端点见 member.md(`GET/PATCH/DELETE /workspaces/{id}/members`)。

### 3.2 请求/响应 JSON 示例

**创建工作区** `POST /api/v1/workspaces`
```json
// Request
{ "name": "Acme Team", "slug": "acme", "timezone": "Asia/Shanghai" }

// 201 Response
{
  "id": "0d6f1c2a-0000-4000-8000-0000000000e2",
  "name": "Acme Team",
  "slug": "acme",
  "logo_url": null,
  "timezone": "Asia/Shanghai",
  "default_language": "en",
  "settings": {},
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

// 201 Response
{
  "data": [
    { "id": "inv-uuid-1", "email": "jane@acme.com", "role": "member", "status": "pending",
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
// 200 Response:返回新创建的名册条目与所属工作区
{ "member": { "id": "mem-uuid", "role": "member", "status": "active" },
  "workspace": { "id": "0d6f...e2", "name": "Acme Team", "slug": "acme" } }
```

**获取单个工作区** `GET /api/v1/workspaces/{id}`(UUID 或 `by-slug/{slug}` 等价)
```json
{
  "id": "0d6f...e2",
  "name": "Acme Team",
  "slug": "acme",
  "logo_url": "https://cdn.example/logo.png",
  "timezone": "Asia/Shanghai",
  "default_language": "en",
  "settings": { "default_status_set": "basic", "new_member_default_role": "member",
                "seat_limit": 50, "feature_flags": { "autopilot": true } },
  "my_role": "admin",
  "created_at": "2026-07-24T10:00:00Z",
  "updated_at": "2026-07-24T11:30:00Z"
}
```

**预览邀请(公开,仅有限字段)** `GET /api/v1/invitations/preview?token=invtk_Ab3...`
```json
// 200 Response(未登录亦可,用于落地页展示;不暴露内部 id 之外敏感信息)
{ "valid": true, "workspace_name": "Acme Team", "workspace_logo_url": "...",
  "role": "member", "expires_at": "2026-07-27T10:00:00Z" }
// 无效/过期/撤销时:
{ "valid": false, "reason": "expired" }   // reason ∈ {expired, revoked, used_up, not_found}
```

**恢复软删除** `POST /api/v1/workspaces/{id}/restore`(仅 owner,保留期内)
```json
// 200 Response:返回工作区对象,deleted_at 置回 null
```

**幂等性**:接受邀请与创建邀请均做幂等保护——同一 token 重复接受不产生重复名册(命中已 `accepted` 直接返回既有条目);同工作区同邮箱重复 pending 邀请返回 409。

### 3.3 错误码表

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | slug 含大写/超长、name 超长、非法时区 |
| 401 | `unauthorized` | token 缺失/失效 |
| 403 | `forbidden` | 非成员访问 / 角色不足(如非 owner 删除) |
| 404 | `not_found` | 工作区不存在或对当前 principal 不可见 |
| 409 | `slug_taken` | slug 已被占用 |
| 409 | `conflict` | 同邮箱已存在 pending 邀请 |
| 422 | `invitation_invalid` | 邀请已过期/已撤销/超使用次数 |
| 429 | `rate_limited` | 触发限流(见 auth.md) |

### 3.4 分页 / 鉴权 / 限流

- **分页**:游标分页。请求 `?limit=N&cursor=<opaque>`;响应 `{"data":[...],"next_cursor"}`(`next_cursor` 为 null 表示末页)。游标内部为 base64 编码的 `(sort_key, id)`,默认按 `created_at DESC, id` 排序,保证稳定无重复。
- **鉴权**:中间件链路:解析 token → 得 principal(user 或 agent)→ 校验该 principal 对路径中 workspace 的成员资格与角色 → 放行。写操作端点额外做角色校验(删除工作区需 `owner`,设置/邀请需 `admin`)。
- **限流**:写端点(创建/邀请)按 principal + IP 限流;邀请创建额外限制单次批量邮箱数(≤ 50)。具体阈值与响应头见 auth.md §限流。

### 3.5 WebSocket 实时事件

连接 `/ws`(握手鉴权见 auth.md),客户端订阅频道 `workspace:{id}`。事件命名 `<entity>.<action>`,每事件携带频道内单调递增 `seq`,客户端断线后凭最后 `seq` 请求重放。

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `workspace.updated` | 设置/名称/slug 变更 | `workspace_id`, `changes` |
| `workspace.deleted` | 工作区被软删除 | `workspace_id` |
| `member.added` | 新成员(人或 agent)入册 | `member_id`, `member_type`, `role` |
| `member.removed` | 成员被移除 | `member_id` |
| `member.role_changed` | 角色变更 | `member_id`, `old_role`, `new_role` |
| `invitation.accepted` | 邀请被接受(管理员侧) | `invitation_id`, `member_id` |

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
- **创建向导**:模态框,步骤 名称 → slug(实时校验占用,绿勾/红叉)→ 邀请成员(可跳过)→ 完成。
- **基本信息表单**:名称、Logo 上传、slug 输入框(带可用性校验与"旧链接将自动重定向"提示)、时区下拉、语言下拉。
- **邀请面板**:多邮箱输入 chip(回车成 chip,支持粘贴批量)、角色选择、"生成邀请链接"按钮;下方待处理邀请列表(邮箱/角色/状态/过期时间/撤销按钮)。
- **危险操作区**:删除/归档需输入工作区 slug 二次确认,仅 owner 可见可操作。

### 4.3 关键交互流程

**创建工作区**:点击切换器 → "新建" → 输入名称(自动 slug 建议)→ slug 实时去重校验 → (可选)邀请 → 完成,自动进入新工作区,当前用户成为 `owner`。

**邀请成员**:设置 → 邀请 → 输入邮箱(或生成链接)→ 选角色与有效期 → 发送;即时生成邀请行(`status=pending`)并触发邮件。被邀请人邮件中点击链接 → 未注册则走注册流(auth.md)→ 注册/登录后自动接受 → 出现在成员名册。

**slug 修改**:输入新 slug → 实时校验 → 保存 → 提示"已保留旧链接重定向"。

**删除工作区**:危险操作区 → 输入 slug 确认 → 软删除 → 全员收到 `workspace.deleted` 事件与通知;保留期内 owner 可恢复。

### 4.4 状态流转(邀请)

```
pending ──接受(被邀请人)──► accepted(终态,生成名册条目)
pending ──撤销(管理员)────► revoked(终态)
pending ──到期(定时/惰性)─► expired(终态)
```
> 链接模式下 `accepted` 不代表终态(可多次使用直到 `max_uses`);`used_count == max_uses` 后惰性置为 `expired`。

工作区自身:`active`(默认)→ `deleted`(软删除,`deleted_at` 非空)→ 保留期内 `restore` 回到 `active`,超保留期硬删除。

### 4.5 实时性与通知

- **实时**:走 WebSocket(§3.5)。名册变更、设置变更、邀请被接受均实时推送;降级 30s 轮询。
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
- [ ] 邮箱邀请:同工作区同邮箱已有 pending 邀请时返回 409 `conflict`。
- [ ] 邀请令牌仅存 SHA-256 哈希,创建响应/邮件返回明文;`token_prefix` 可展示。
- [ ] 接受有效邀请后生成 `members` 条目,角色为邀请预设值;`used_count` 自增。
- [ ] 过期/已撤销/超次数邀请接受返回 422 `invitation_invalid`。
- [ ] 撤销邀请后该 token 立即失效。
- [ ] 邀请的 `role` 不可为 `owner`。
- [ ] **邀请链接默认过期与次数上限**:链接模式邀请创建时 `max_uses` 未指定默认 10、`expires_at` 未指定默认 7 天;不允许创建不限次 + 永不过期的邀请链接,链接泄漏后有失效兜底。
- [ ] 非成员访问任意工作区资源返回 404(不泄露存在性)。
- [ ] 所有业务查询隐式按 `workspace_id` 过滤,跨工作区不可读。

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
- [ ] **用户可控 URL scheme 校验**:`logo_url` 等用户可控 URL 字段服务端校验 scheme,禁止 `javascript:`/`data:`,仅允许 `https`。

### 5.4 实时

- [ ] 工作区设置变更后,在线成员 1s 内收到 `workspace.updated`。
- [ ] 成员入册/移除/角色变更触发对应 `member.*` 事件(与 member.md 一致)。
- [ ] 邀请被接受,管理员侧实时收到 `invitation.accepted`。
- [ ] 客户端断线重连后,凭最后 `seq` 可重放缺失事件,无丢失无重复。
- [ ] WebSocket 不可用时,30s 轮询降级路径功能等价。
