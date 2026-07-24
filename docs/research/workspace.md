# 工作区 / 组织（Workspace / Organization）调研记录

> 调研对象:主流团队协作 / 项目管理产品在【工作区 / 组织】模块上的通用设计模式(已匿名化,不指向任何具体产品)。
> 数据模型基准约定:PostgreSQL、UUID 主键、`created_at` / `updated_at` 时间戳、REST + JSON、游标分页、Bearer token 鉴权、实时走 WebSocket。

---

## 1. 功能清单

### 1.1 多租户模型

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 工作区作为顶层租户隔离单元 | 一个工作区(workspace)是一个团队/组织的独立数据空间,所有项目、issue、成员、标签都隶属于某个工作区 | 一家创业公司创建一个工作区,所有团队数据彼此隔离,不与其它公司混在一起 |
| 单用户可属于多个工作区 | 同一个自然人账号可被邀请进多个工作区,登录后在工作区之间切换 | 一名外包工程师同时服务 A、B 两家客户,在两个工作区间一键切换 |
| 数据强隔离 | 所有业务查询都隐式带 `workspace_id` 过滤,跨工作区不可见 | 用户即使猜到其它工作区某 issue 的 UUID,也无法读取(鉴权层校验归属) |
| 工作区级配置 | 默认状态集、默认优先级、工作日/时区、默认语言等按工作区配置 | 管理员把本工作区的"完成"状态自定义命名为"已上线" |

**关键设计点(业界标准做法)**:
- 工作区是**软多租户**(共享数据库、按 `workspace_id` 列隔离)而非每租户独立库,兼顾成本与隔离。
- 几乎所有业务表都携带 `workspace_id` 外键并建索引,作为隔离与查询的第一过滤条件。
- 鉴权中间件在解析 token 后,校验"当前用户是否为该 workspace 成员",再放行对该 workspace 资源的访问。

### 1.2 工作区设置

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 基本信息维护 | 名称、Logo、URL slug、时区、默认语言 | 管理员上传公司 Logo、修改显示名 |
| slug 修改 | 修改 URL 标识(需校验唯一、保留旧 slug 重定向) | 公司更名后把 `acme` 改成 `acme-corp`,旧链接 301 重定向 |
| 默认配置 | 新成员默认角色、issue 默认状态、默认项目可见性 | 设置新邀请成员默认为 `member` 角色 |
| 危险操作区 | 删除/归档工作区(需二次确认、仅 owner) | owner 在项目结束后删除整个工作区(软删除 + 保留期) |
| 计费/套餐信息(可选) | 席位上限、功能开关 | 管理员查看当前套餐还能邀请多少人 |

### 1.3 URL slug / 标识

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 全局唯一 slug | 工作区拥有一个全局唯一的可读标识,用于 URL 与 API 寻址 | 形如 `/<slug>/board` 的路径中,`acme` 即为 slug |
| slug 规范 | 仅小写字母/数字/连字符,长度 2–32,创建时校验占用 | 输入 `Acme Team` 自动建议 `acme-team` |
| 双标识并存 | 内部一律用 UUID 做外键,slug 仅用于人类可读寻址 | API 既能用 `GET /workspaces/{uuid}` 也能用 `GET /workspaces/by-slug/{slug}` |
| slug 历史/重定向 | 改名后保留旧 slug → 新 id 的映射,避免死链 | 收藏的旧链接仍可访问 |

### 1.4 邀请机制

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 邮箱邀请 | 管理员输入邮箱(可批量)发起邀请,系统发送邀请邮件/链接 | 管理员一次性邀请 5 名新同事 |
| 邀请链接 | 生成可分享的邀请 URL(可设有效期、使用次数、预设角色) | 在群里贴一个邀请链接,新人点击即加入 |
| 邀请令牌与有效期 | 邀请记录带 token、过期时间、最大使用次数、是否单次 | 一次性链接被使用后即失效 |
| 接受邀请 | 被邀请人注册/登录后接受,生成成员记录 | 新用户点链接 → 注册 → 自动成为 `member` |
| 撤销邀请 | 未接受的邀请可被管理员撤销 | 发错邮箱,撤回邀请 |
| 域名自动加入(可选) | 配置企业邮箱域名后,该域名注册者自动成为成员 | 配置 `@acme.com` 域名,同事用企业邮箱注册即自动入组 |

### 1.5 工作区与成员 / 项目的关系

- 工作区 1—N 成员(通过成员名册表关联,见 `member.md`)。
- 工作区 1—N 项目(见 `project.md`)。
- 工作区 1—N 标签 / 自定义字段定义(见 `label-property.md`)。
- 工作区 1—N issue(issue 必属于某工作区,通常再属于某项目,见 `issue.md`)。
- 用户(user)与 工作区(workspace)是 **N—N** 关系,通过 `workspace_members` 关联表落地,关联表上携带角色等信息。

---

## 2. 数据模型

### 2.1 核心实体

#### `workspaces`(工作区)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `name` | TEXT | NOT NULL | — | 显示名,1–80 字符 |
| `slug` | CITEXT/TEXT | NOT NULL, UNIQUE | — | 全局唯一 URL 标识,小写,`^[a-z0-9-]{2,32}$` |
| `logo_url` | TEXT | NULL | NULL | Logo 地址 |
| `timezone` | TEXT | NOT NULL | `'UTC'` | IANA 时区名 |
| `default_language` | TEXT | NOT NULL | `'en'` | 默认语言 |
| `settings` | JSONB | NOT NULL | `'{}'` | 杂项配置(默认状态集开关、功能开关等) |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

#### `users`(自然人账号 / 登录身份)

> 与成员名册分离:`users` 是跨工作区的登录身份;`workspace_members` 是某工作区内的名册条目。详见 `member.md`。

| 字段 | 类型 | 约束 | 默认值 |
|------|------|------|--------|
| `id` | UUID | PK | `gen_random_uuid()` |
| `email` | CITEXT | NOT NULL, UNIQUE | — |
| `full_name` | TEXT | NOT NULL | — |
| `avatar_url` | TEXT | NULL | NULL |
| `auth_hash` | TEXT | NOT NULL | — | 密码哈希(或外部登录绑定) |
| `is_active` | BOOLEAN | NOT NULL | `true` |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |

#### `workspace_members`(工作区成员名册 / 用户-工作区关联)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | |
| `user_id` | UUID | NULL, FK→users(id) ON DELETE CASCADE | — | 人类成员指向 user;AI agent 成员则为 NULL |
| `agent_id` | UUID | NULL, FK→agents(id) ON DELETE CASCADE | — | AI agent 成员指向 agent(见 member.md) |
| `member_type` | TEXT | NOT NULL, CHECK IN ('user','agent') | `'user'` | 成员类型判别器 |
| `role` | TEXT | NOT NULL, CHECK IN ('owner','admin','member','guest') | `'member'` | 角色 |
| `status` | TEXT | NOT NULL, CHECK IN ('active','disabled') | `'active'` | 启用/停用 |
| `joined_at` | TIMESTAMPTZ | NULL | NULL | 正式加入时间 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:
- `UNIQUE (workspace_id, user_id)`(同一 user 在同一工作区仅一条);agent 同理 `UNIQUE (workspace_id, agent_id)`。
- `CHECK ((member_type='user' AND user_id IS NOT NULL AND agent_id IS NULL) OR (member_type='agent' AND agent_id IS NOT NULL AND user_id IS NULL))` —— 保证多态外键的一致性。

#### `workspace_invitations`(邀请)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK | — | |
| `email` | CITEXT | NULL | — | 定向邀请邮箱(与 link 模式二选一) |
| `token` | TEXT | NOT NULL, UNIQUE | — | 邀请令牌(哈希存储更佳) |
| `role` | TEXT | NOT NULL | `'member'` | 接受后赋予的角色 |
| `invited_by` | UUID | NOT NULL, FK→workspace_members(id) | — | 邀请人 |
| `max_uses` | INT | NULL | NULL | 最大使用次数(NULL=不限) |
| `used_count` | INT | NOT NULL | `0` | 已使用次数 |
| `expires_at` | TIMESTAMPTZ | NULL | NULL | 过期时间(NULL=永不过期) |
| `accepted_at` | TIMESTAMPTZ | NULL | NULL | 接受时间(单次邀请) |
| `status` | TEXT | NOT NULL, CHECK IN ('pending','accepted','revoked','expired') | `'pending'` | |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

#### `workspace_slug_history`(slug 重定向)

| 字段 | 类型 | 约束 |
|------|------|------|
| `id` | UUID | PK |
| `workspace_id` | UUID | NOT NULL, FK |
| `old_slug` | TEXT | NOT NULL, UNIQUE |
| `created_at` | TIMESTAMPTZ | NOT NULL |

### 2.2 实体关系(ER)

```
users ──┐                          ┌── agents
        │ N:N (via workspace_members)│
        └─────────── workspace_members ───────────┘
                       │ N:1
                       ▼
                  workspaces ──1:N──► projects ──1:N──► issues
                       │
                       ├──1:N──► workspace_invitations
                       ├──1:N──► labels / custom_field_defs
                       └──1:N──► workspace_slug_history
```

### 2.3 关键索引

```sql
CREATE UNIQUE INDEX uq_workspaces_slug ON workspaces(slug) WHERE deleted_at IS NULL;
CREATE INDEX idx_workspaces_deleted_at ON workspaces(deleted_at);

CREATE INDEX idx_wm_workspace ON workspace_members(workspace_id);
CREATE INDEX idx_wm_user ON workspace_members(user_id);
CREATE INDEX idx_wm_agent ON workspace_members(agent_id);
CREATE UNIQUE INDEX uq_wm_workspace_user ON workspace_members(workspace_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_wm_workspace_agent ON workspace_members(workspace_id, agent_id) WHERE agent_id IS NOT NULL;

CREATE INDEX idx_invitations_workspace ON workspace_invitations(workspace_id, status);
CREATE UNIQUE INDEX uq_invitations_token ON workspace_invitations(token);
```

---

## 3. 接口设计

REST 基础路径:`/api/v1`。鉴权:`Authorization: Bearer <token>`。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/workspaces` | 创建工作区 |
| GET | `/workspaces` | 列出当前用户所属工作区 |
| GET | `/workspaces/{id}` | 获取单个工作区(支持 UUID 或 `by-slug/{slug}`) |
| PATCH | `/workspaces/{id}` | 更新名称/slug/Logo/时区等 |
| DELETE | `/workspaces/{id}` | 软删除工作区(仅 owner) |
| GET | `/workspaces/{id}/members` | 成员名册(见 member.md) |
| POST | `/workspaces/{id}/invitations` | 创建邀请 |
| GET | `/workspaces/{id}/invitations` | 列出邀请 |
| DELETE | `/workspaces/{id}/invitations/{inv_id}` | 撤销邀请 |
| POST | `/invitations/accept` | 接受邀请(凭 token) |
| GET | `/workspaces/by-slug/{slug}` | 按 slug 解析工作区 |

### 3.2 请求/响应示例

**创建工作区** `POST /api/v1/workspaces`
```json
// Request
{ "name": "Acme Team", "slug": "acme" }

// 201 Response
{
  "id": "0d6f...e2",
  "name": "Acme Team",
  "slug": "acme",
  "timezone": "UTC",
  "role": "owner",
  "created_at": "2026-07-24T10:00:00Z",
  "updated_at": "2026-07-24T10:00:00Z"
}
```

**列出工作区** `GET /api/v1/workspaces?limit=20&cursor=eyJpZCI6...`
```json
{
  "data": [ { "id": "0d6f...e2", "name": "Acme Team", "slug": "acme", "role": "owner" } ],
  "next_cursor": "eyJpZCI6IjBkNmY..."
}
```

**创建邀请** `POST /api/v1/workspaces/{id}/invitations`
```json
// Request
{ "emails": ["jane@acme.com", "john@acme.com"], "role": "member", "expires_in_hours": 72 }

// 201 Response
{
  "data": [
    { "id": "inv_1", "email": "jane@acme.com", "role": "member", "status": "pending",
      "invite_link": "/invite/<token>", "expires_at": "2026-07-27T10:00:00Z" }
  ]
}
```

**更新 slug** `PATCH /api/v1/workspaces/{id}`
```json
{ "slug": "acme-corp" }
// 200:返回更新后的工作区;旧 slug 自动写入 slug_history 做重定向
```

### 3.3 错误码体系

统一错误体:`{ "error": { "code": "...", "message": "...", "details": {...} } }`

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 字段格式非法(slug 含大写、name 超长) |
| 401 | `unauthorized` | token 缺失/失效 |
| 403 | `forbidden` | 非成员访问 / 角色不足(如非 owner 删除) |
| 404 | `not_found` | 工作区不存在或不可见 |
| 409 | `slug_taken` | slug 已被占用 |
| 409 | `conflict` | 重复邀请同一邮箱(已有 pending) |
| 422 | `unprocessable` | 邀请已过期/已撤销/超使用次数 |
| 429 | `rate_limited` | 触发限流 |

### 3.4 分页与鉴权

- **分页**:游标分页。请求 `?limit=N&cursor=<opaque>`;响应 `next_cursor`(为空表示末页)。游标内部为 base64 编码的 `(sort_key, id)`,默认按 `created_at DESC, id` 排序,保证稳定。
- **鉴权**:Bearer token(JWT 或 opaque + 服务端会话)。中间件链路:解析 token → 取 user → 校验该 user 对路径中 workspace 的成员资格与角色 → 放行。涉及写操作的端点额外做角色校验(如删除工作区需 `owner`)。

---

## 4. UI 设计

### 4.1 信息架构(导航层级)

```
[工作区切换器(左上角)]
   └── 当前工作区
        ├── 收件箱 / 我的任务
        ├── 项目(列表)
        ├── 看板 / 视图
        ├── 成员(AI agent 与人类同列)
        └── 设置(管理员可见)
             ├── 基本信息(名称/Logo/slug/时区)
             ├── 成员与角色
             ├── 邀请
             ├── 状态 / 标签 / 自定义字段
             └── 危险操作(归档/删除)
```

### 4.2 关键页面与组件

- **工作区切换器**:左上角下拉,列出用户所有工作区,顶部"创建工作区"。切换后整页上下文(项目、成员、看板)随之刷新。
- **创建向导**:模态框,步骤为 名称 → slug(实时校验占用,绿勾/红叉)→ 邀请成员(可跳过)。
- **设置 → 基本信息**:表单(名称、Logo 上传、slug 输入框带可用性校验、时区下拉、语言下拉)。slug 修改处提示"旧链接将自动重定向"。
- **设置 → 邀请**:邀请表单(多邮箱输入 chip、角色选择、生成邀请链接按钮);下方为待处理邀请列表(邮箱/角色/状态/过期时间/撤销按钮)。
- **成员名册页**:表格,列含 头像+姓名、邮箱、角色下拉、状态(启用/停用)、加入时间。AI agent 与人类同表展示(见 member.md)。

---

## 5. UX 设计

### 5.1 关键交互流程

**创建工作区**:点击切换器 → "新建" → 输入名称(自动 slug 建议)→ slug 实时去重校验 → (可选)邀请 → 完成,自动进入新工作区,当前用户成为 `owner`。

**邀请成员**:设置 → 邀请 → 输入邮箱(回车成 chip,支持粘贴批量)→ 选角色 → 发送;系统即时生成邀请行(status=pending)并触发邮件。被邀请人邮件中点击链接 → 未注册则走注册流 → 注册/登录后自动接受 → 出现在成员名册。

**slug 修改**:输入新 slug → 实时校验 → 保存 → 提示"已保留旧链接重定向"。

### 5.2 状态流转(邀请)

```
pending ──接受──► accepted
pending ──撤销──► revoked
pending ──到期──► expired(定时任务/惰性判定)
```

### 5.3 实时性方案

- 走 **WebSocket** 长连接,客户端订阅 `workspace:{id}` 频道。
- 关键事件:
  - `workspace.updated`(设置变更,所有在线成员刷新)
  - `member.added` / `member.removed` / `member.role_changed`(名册实时刷新)
  - `invitation.accepted`(邀请被接受,管理员侧实时更新)
- 降级方案:WebSocket 不可用时退化为 30s 轮询 `GET /workspaces/{id}` + `GET /members`。

### 5.4 通知触发点

- 被邀请:邮件 + 站内通知("X 邀请你加入 Acme Team")。
- 角色变更:站内通知。
- 邀请即将过期(可选):提醒邀请人。
- 工作区被删除/归档:全员站内 + 邮件通知。
