# 调研记录：认证与授权（Auth）

> 模块簇：协作与基础能力
> 调研对象：业界主流团队工作区产品在「认证（注册/登录/会话/令牌）」与「授权（RBAC/审计/限流）」上的成熟设计。
> 说明：本文仅记录中性化的设计模式与业界标准做法，用于指导 Mesh 的 Spec 撰写；不指向任何具体产品。第三方登录统一以「第三方 OAuth 提供商」指代。
> Mesh 特色标注：`[Mesh 特色]` 表示需要特别为「AI agent 作为队友」这一核心范式做的设计（如供 agent runtime 与 CLI 调用的 API token）。

---

## 一、功能清单

### 1.1 认证（Authentication）

| # | 功能点 | 典型用户场景 |
|---|--------|--------------|
| A1 | 邮箱 + 密码注册 | 新用户用邮箱和密码创建账号，发验证邮件确认邮箱归属 |
| A2 | 邮箱验证 | 点击邮件链接确认邮箱，未验证账号受限（如不能创建 workspace） |
| A3 | 邮箱 + 密码登录 | 已注册用户登录，校验密码（bcrypt/argon2） |
| A4 | 忘记密码 / 重置 | 邮件发短时效重置链接，重置密码并使旧会话失效 |
| A5 | 第三方 OAuth 登录 | 「使用第三方账号登录」，首次登录自动建账号并绑定，后续免密登录 |
| A6 | OAuth 账号绑定 / 解绑 | 已有密码账号可绑定第三方身份；解绑（保留至少一种登录方式） |
| A7 | 会话管理 | 登录后颁发会话凭证；记住我 / 会话有效期；多设备并存 |
| A8 | 登出 / 全端登出 | 单端登出；「登出所有设备」使全部会话失效 |
| A9 | 会话列表与撤销 | 在设置里查看活跃会话（设备/IP/最近活跃），可撤销指定会话 |
| A10 | 刷新令牌（refresh） | 短期 access token 过期后用 refresh token 静默续期，refresh 可轮换 |
| A11 | API token（个人访问令牌）`[Mesh 特色]` | 成员在设置里创建命名 token，供 CLI / agent runtime / 自动化脚本调用；创建时仅明文展示一次，之后只存哈希 |
| A12 | API token 作用域与过期 | token 可设权限范围（scope）与过期时间，最小权限原则 |
| A13 | API token 撤销 | 随时撤销某个 token，立即失效 |
| A14 | agent 身份凭证 `[Mesh 特色]` | 每个 agent 有专属 API token，runtime 用它代表 agent 读写 issue/评论/附件，权限受 agent 角色约束 |
| A15 | 双因素认证（2FA / TOTP） | 可选增强：密码之外再加一次性验证码（企业/高安全场景） |
| A16 | 登录保护 | 失败计数锁定、异常登录提醒、凭据填充防护 |

### 1.2 授权（Authorization）

| # | 功能点 | 典型用户场景 |
|---|--------|--------------|
| Z1 | workspace 角色 | owner / admin / member / guest（可选），决定全局能力 |
| Z2 | 资源级权限 | 对 project / issue / agent 等的读/写/管理权限，角色 × 资源矩阵 |
| Z3 | 邀请成员 | owner/admin 邮件邀请，受邀者接受后按角色加入 |
| Z4 | 角色变更 / 移除成员 | admin 调整成员角色或移出 workspace |
| Z5 | 权限校验中间件 | 每个受保护端点声明所需权限，统一拦截 |
| Z6 | 最小权限的 agent 角色 `[Mesh 特色]` | agent 默认仅被授予完成工作所需的最小资源权限 |
| Z7 | 审计日志 | 记录登录、token 创建/撤销、角色变更、敏感写操作；可按人/时间/动作查询 |
| Z8 | 速率限制 | 按 IP/账号/token 对登录、注册、重置、API 调用限流，防暴力破解与滥用 |

---

## 二、数据模型

> 约定：PostgreSQL；UUID 主键；`created_at`/`updated_at`；密码用 **argon2id**（首选）或 **bcrypt** 加盐哈希；所有长期凭证（refresh token、API token）**只存哈希**，不存明文；REST + JSON；游标分页；实时走 WebSocket。

### 2.1 `users` — 全局用户（跨 workspace 的身份）

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 用户 ID |
| `email` | citext | NOT NULL, UNIQUE | 邮箱（citext 大小写不敏感唯一） |
| `email_verified_at` | timestamptz | NULL | 邮箱验证时间 |
| `password_hash` | text | NULL | argon2id/bcrypt 哈希（OAuth-only 用户可为 NULL） |
| `password_changed_at` | timestamptz | NULL | 用于使旧会话/令牌失效 |
| `display_name` | text | NOT NULL | 显示名 |
| `avatar_url` | text | NULL | 头像 |
| `status` | text | NOT NULL default 'active', CHECK in ('active','invited','disabled','deleted') | 账号状态 |
| `mfa_secret` | text | NULL | TOTP 密钥（加密存储，启用 2FA 时） |
| `mfa_enabled_at` | timestamptz | NULL | |
| `last_login_at` | timestamptz | NULL | |
| `created_at` / `updated_at` | timestamptz | NOT NULL | |

**关键索引：** `uq_users_email`（唯一）；`idx_users_status`。

### 2.2 `oauth_identities` — 第三方登录绑定

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `user_id` | uuid | NOT NULL, FK→users | |
| `provider` | text | NOT NULL | 提供商标识（中性枚举，如 `oauth_provider_a`，不绑定具体厂商） |
| `provider_subject` | text | NOT NULL | 提供商侧唯一用户标识（sub） |
| `provider_email` | text | NULL | 提供商返回的邮箱 |
| `access_token_ref` | text | NULL | （如需调用提供商 API）加密存储的令牌引用 |
| `created_at` / `updated_at` | timestamptz | NOT NULL | |

**唯一约束：** `uq_oauth (provider, provider_subject)` —— 同一提供商同一身份只绑一个账号。
**关键索引：** `idx_oauth_user (user_id)`。

### 2.3 `sessions` / `refresh_tokens` — 会话与刷新令牌

> 采用「短期 access token（JWT）+ 可撤销 refresh token（服务端记录）」混合模型时，需要这张表支撑撤销与多设备管理。

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 会话 / refresh token ID（即 `jti`） |
| `user_id` | uuid | NOT NULL, FK→users | |
| `token_hash` | text | NOT NULL | refresh token 的 SHA-256 哈希（不存明文） |
| `type` | text | NOT NULL default 'web', CHECK in ('web','cli','api') | 会话来源 |
| `user_agent` | text | NULL | 客户端 UA |
| `ip_address` | inet | NULL | 创建时 IP |
| `created_at` | timestamptz | NOT NULL | |
| `last_active_at` | timestamptz | NULL | 最近活跃 |
| `expires_at` | timestamptz | NOT NULL | 过期时间 |
| `revoked_at` | timestamptz | NULL | 撤销时间（登出/全端登出/密码变更） |

**关键索引：** `idx_sessions_user (user_id) WHERE revoked_at IS NULL`；唯一 `uq_token_hash (token_hash)`。

### 2.4 `api_tokens` — 个人/agent 访问令牌 `[Mesh 特色]`

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | token ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces | 令牌归属的 workspace |
| `owner_type` | text | NOT NULL, CHECK in ('member','agent') | 持有者类型；agent 令牌即 agent 身份凭证 |
| `owner_id` | uuid | NOT NULL | 持有者 ID |
| `name` | text | NOT NULL | 人类可读名称（如 "CI bot"、"code-reviewer runtime"） |
| `token_hash` | text | NOT NULL | 令牌明文的 SHA-256 哈希（**仅存哈希**） |
| `prefix` | text | NOT NULL | 令牌前缀（如 `mesh_pat_…` 的前 8~12 位，用于列表展示与快速定位，不含秘密） |
| `scopes` | text[] | NOT NULL default '{}' | 权限范围数组（最小权限），如 `issue:read`、`issue:write`、`comment:write`、`attachment:write` |
| `role_override` | text | NULL | 可选：限定该 token 等效角色（不高于持有者角色） |
| `last_used_at` | timestamptz | NULL | 最近使用 |
| `last_used_ip` | inet | NULL | |
| `expires_at` | timestamptz | NULL | 过期时间（NULL=不过期，但建议强制设置） |
| `revoked_at` | timestamptz | NULL | 撤销时间 |
| `created_at` / `updated_at` | timestamptz | NOT NULL | |

**关键索引：** 唯一 `uq_api_token_hash (token_hash)`；`idx_api_tokens_owner (workspace_id, owner_type, owner_id) WHERE revoked_at IS NULL`。
**设计要点：**
- 创建时生成高熵随机串（如 `mesh_pat_` + 32+ 字节 base62/base64url），**只在创建响应里返回一次明文**，之后数据库仅存 `token_hash`，UI 只显示 `prefix` + 掩码。
- 校验时：客户端带 `Authorization: Bearer <明文>` → 服务端算哈希 → 查 `token_hash` → 命中且未撤销未过期 → 解析 `owner_type/owner_id/scopes/workspace_id` 注入请求上下文。
- 建议令牌自带可校验前缀/类型位，便于区分 PAT / agent token / refresh token。

### 2.5 `workspaces` 与 `workspace_members` — 成员关系与角色

| `workspace_members` 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces | |
| `user_id` | uuid | NOT NULL, FK→users | |
| `role` | text | NOT NULL default 'member', CHECK in ('owner','admin','member','guest') | workspace 角色 |
| `status` | text | NOT NULL default 'active', CHECK in ('invited','active','disabled') | |
| `invited_by` | uuid | NULL | |
| `joined_at` | timestamptz | NULL | |
| `created_at` / `updated_at` | timestamptz | NOT NULL | |

**唯一约束：** `uq_member (workspace_id, user_id)`。

### 2.6 `workspace_agents` — agent 在 workspace 的成员关系 `[Mesh 特色]`

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL | |
| `agent_id` | uuid | NOT NULL | agent 主体 ID |
| `role` | text | NOT NULL default 'member' | agent 在 workspace 的角色（通常 ≤ member，最小权限） |
| `status` | text | NOT NULL default 'active' | |
| `created_at` / `updated_at` | timestamptz | NOT NULL | |

**唯一约束：** `uq_workspace_agent (workspace_id, agent_id)`。

### 2.7 `roles` / `permissions` / `role_permissions` — RBAC（可选：内置角色可硬编码，自定义角色才建表）

> 简化方案：角色为固定枚举（owner/admin/member/guest），权限矩阵在代码里维护（声明式）。可扩展方案如下：

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `roles` | `id, workspace_id(null=系统内置), name, is_system` | 角色定义 |
| `permissions` | `id, resource, action` | 权限原子，如 `issue:read`、`agent:trigger`、`workspace:manage_members` |
| `role_permissions` | `role_id, permission_id` | 角色↔权限多对多 |

**资源 × 角色权限矩阵（示例，内置）：**

| 权限 \ 角色 | owner | admin | member | guest | agent(默认) |
|-------------|:---:|:---:|:---:|:---:|:---:|
| `workspace:settings` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `workspace:manage_members` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `workspace:billing` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `project:manage` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `issue:read` | ✅ | ✅ | ✅ | ✅(受限) | ✅(受限) |
| `issue:write` | ✅ | ✅ | ✅ | ❌ | ✅(按 scope) |
| `comment:write` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `agent:trigger`（@提及触发运行）`[Mesh 特色]` | ✅ | ✅ | ✅ | ❌ | ⚠️受限(防回环) |
| `agent:manage` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `token:manage`（创建/撤销 API token） | ✅ | ✅ | ✅(仅自己) | ❌ | ❌ |

### 2.8 `audit_logs` — 审计日志

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NULL | 关联 workspace（账号级事件可为 NULL） |
| `actor_type` | text | NOT NULL, CHECK in ('member','agent','system') | 行为者类型 |
| `actor_id` | uuid | NULL | 行为者 ID |
| `action` | text | NOT NULL | 动作枚举，如 `auth.login`、`auth.login_failed`、`token.created`、`token.revoked`、`member.role_changed`、`member.removed`、`issue.deleted` |
| `resource_type` | text | NULL | 目标资源类型 |
| `resource_id` | uuid | NULL | 目标资源 ID |
| `ip_address` | inet | NULL | |
| `user_agent` | text | NULL | |
| `metadata` | jsonb | NOT NULL default '{}' | 变更前后值等上下文 |
| `created_at` | timestamptz | NOT NULL | |

**关键索引：** `idx_audit_ws_time (workspace_id, created_at DESC)`；`idx_audit_actor (actor_type, actor_id, created_at DESC)`；`idx_audit_action (workspace_id, action, created_at DESC)`。
**设计要点：**审计日志**只追加（append-only）**，不允许更新/删除（合规需要）；可定期归档冷存储。

### 2.9 ER 关系总结

```
users 1─* oauth_identities
users 1─* sessions
users 1─* workspace_members *─1 workspaces
agents 1─* workspace_agents *─1 workspaces
workspaces 1─* api_tokens（owner_type=member|agent）
roles *─* permissions（可选自定义 RBAC）
（所有敏感动作）─→ audit_logs（append-only）
```

---

## 三、接口设计

> 鉴权：除登录/注册/重置等公开端点外，均需 `Authorization: Bearer <token>`。token 可为：① 会话 access token（JWT，短期）；② API token（长期，供 CLI/runtime）。服务端按令牌格式/前缀路由到对应校验逻辑。游标分页。

### 3.1 认证端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/auth/register` | 邮箱+密码注册 |
| POST | `/api/v1/auth/login` | 邮箱+密码登录，返回 access + refresh token |
| POST | `/api/v1/auth/refresh` | 用 refresh token 换新 access（可轮换 refresh） |
| POST | `/api/v1/auth/logout` | 登出当前会话（撤销 refresh） |
| POST | `/api/v1/auth/logout-all` | 撤销该用户全部会话 |
| POST | `/api/v1/auth/forgot-password` | 发起重置（恒返回成功，防账号枚举） |
| POST | `/api/v1/auth/reset-password` | 凭重置令牌设新密码并使旧会话失效 |
| POST | `/api/v1/auth/verify-email` | 验证邮箱 |
| GET | `/api/v1/auth/oauth/{provider}/start` | 发起第三方登录（302 到提供商授权页，含 state + PKCE） |
| GET/POST | `/api/v1/auth/oauth/{provider}/callback` | 回调：换取身份、登录或自动注册并绑定 |
| GET | `/api/v1/sessions` | 列出我的活跃会话 |
| DELETE | `/api/v1/sessions/{id}` | 撤销指定会话 |
| GET | `/api/v1/me` | 当前用户与所属 workspace 列表 |

**登录请求/响应：**
```json
// POST /auth/login 请求
{"email": "li@corp.com", "password": "...", "remember": true}

// 200 响应
{
  "data": {
    "access_token": "eyJhbGci...",   // 短期 JWT（如 15min）
    "token_type": "Bearer",
    "expires_in": 900,
    "refresh_token": "rt_..."        // 长期，仅 httpOnly cookie 或安全返回
  }
}
```

**注册请求：**
```json
{"email": "li@corp.com", "password": "...", "display_name": "李四"}
```
> 密码强度校验：≥8 位、含字母数字（可选符号），对照常见弱密码/泄露密码库拒绝。

### 3.2 API token 端点 `[Mesh 特色]`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/workspaces/{ws}/api-tokens` | 列出我的/agent 的 token（仅 prefix + 元数据，无明文） |
| POST | `/api/v1/workspaces/{ws}/api-tokens` | 创建 token（**响应仅一次返回明文**） |
| DELETE | `/api/v1/workspaces/{ws}/api-tokens/{id}` | 撤销 token |
| POST | `/api/v1/agents/{agent_id}/tokens` | 为 agent 创建运行凭证（需 `agent:manage`） |

**创建 token 请求/响应：**
```json
// 请求
{"name": "code-reviewer runtime", "scopes": ["issue:read","comment:write","attachment:write"], "expires_at": "2027-01-01T00:00:00Z", "owner_type": "agent", "owner_id": "a-222"}

// 201 响应（明文仅此一次）
{"data": {"id": "tok-1", "name": "...", "prefix": "mesh_pat_Ab3", "token": "mesh_pat_Ab3Xy9...完整明文...", "scopes": [...], "expires_at": "..."}}
```

### 3.3 成员与角色端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/workspaces/{ws}/invitations` | 邀请成员（发邮件） |
| GET | `/api/v1/workspaces/{ws}/members` | 列出成员（游标分页） |
| PATCH | `/api/v1/workspaces/{ws}/members/{user_id}` | 改角色（需 `manage_members`，且不可降唯一 owner） |
| DELETE | `/api/v1/workspaces/{ws}/members/{user_id}` | 移除成员 |
| GET | `/api/v1/workspaces/{ws}/audit-logs` | 查审计日志（需 admin/owner，游标分页 + 过滤） |

### 3.4 错误码体系

| HTTP | code | 场景 |
|------|------|------|
| 400 | `VALIDATION_ERROR` | 字段非法、密码太弱 |
| 401 | `UNAUTHENTICATED` | 凭证缺失/无效/过期 |
| 401 | `EMAIL_NOT_VERIFIED` | 需先验证邮箱 |
| 403 | `FORBIDDEN` | 角色/scope 不足 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 409 | `CONFLICT` | 邮箱已注册、唯一 owner 不可移除 |
| 422 | `INVALID_CREDENTIALS` | 邮箱或密码错误（**统一文案，不区分**，防枚举） |
| 423 | `ACCOUNT_LOCKED` | 失败次数过多被临时锁定 |
| 429 | `RATE_LIMITED` | 触发限流，含 `Retry-After` |

### 3.5 速率限制（具体阈值示例，可调）

| 端点类 | 限制 | 维度 |
|--------|------|------|
| 登录 / 注册 / 重置 | 5 次 / 分钟，超出锁定 15 分钟 | 按 IP + 邮箱 |
| 通用 API 读 | 300 req / 分钟 | 按 token / 用户 |
| 通用 API 写 | 120 req / 分钟 | 按 token / 用户 |
| 附件上传申请 / 下载 | 60 req / 分钟 | 按 token / IP |
| WebSocket 消息 | 60 msg / 分钟 | 按连接 |

实现：令牌桶/滑动窗口（Redis），响应头 `X-RateLimit-Limit/Remaining/Reset`；超限 `429` + `Retry-After`。登录类叠加**失败计数锁定**与验证码/凭据填充防护。

---

## 四、UI 设计

### 4.1 认证页面

- **登录页**：邮箱 + 密码输入框、「记住我」、「忘记密码？」链接、「使用第三方账号登录」按钮组；注册入口。
- **注册页**：邮箱 + 显示名 + 密码（带强度条与实时校验提示）；提交后跳转「已发验证邮件」页。
- **忘记密码 / 重置**：单输入框发起；邮件链接进入重置页（新密码 + 确认 + 强度条）。
- **错误提示**：登录失败统一「邮箱或密码不正确」，不暴露账号是否存在。

### 4.2 设置 → 安全（Settings → Security）

- **密码**：修改密码（旧密码 + 新密码 + 强度条）。
- **2FA**：启用 TOTP（展示密钥 + 二维码 + 验证码确认）；生成备用码。
- **活跃会话列表**：每行 = 设备/浏览器图标 + UA + IP + 最近活跃 + 「当前」标记 + 「撤销」按钮；顶部「登出所有其他会话」。
- **第三方账号绑定**：已绑定提供商列表（含解绑，禁用唯一登录方式时灰化解绑）。

### 4.3 设置 → API Tokens `[Mesh 特色]`

- token 列表：名称 / prefix + 掩码（`mesh_pat_Ab3…****`）/ scopes 标签 / 过期时间 / 最近使用 / 撤销按钮。
- 「新建 token」对话框：名称、scope 多选（按资源分组）、过期时间（建议必选）；创建后弹出**一次性明文**展示框（带复制按钮 + 醒目「关闭后无法再次查看」提示）。
- agent token 区 `[Mesh 特色]`：在 agent 设置里管理其运行凭证，标注「最小权限」与「防回环」说明。

### 4.4 设置 → 成员 / 审计（Settings → Members / Audit，admin+）

- 成员表：头像 + 名称 + 邮箱 + 角色下拉（owner/admin/member/guest）+ 状态 + 移除；顶部「邀请成员」。
- 审计日志页：表格 = 时间 / 行为者 / 动作 / 资源 / IP；按动作类型、行为者、时间范围筛选；只读、不可删。

---

## 五、UX 设计

### 5.1 注册 / 登录流程

1. **注册**：填邮箱+密码 → 服务端校验强度与唯一性 → argon2id 哈希密码 → 建 `users(status=active/invited)` → 发验证邮件（短时效签名链接）→ 前端提示「去邮箱验证」。未验证可登录但受限。
2. **登录**：提交凭据 → 服务端恒定时间比较密码哈希（防时序攻击）→ 失败计数（达阈值锁定 + 可选验证码）→ 成功生成短期 access JWT（含 `sub`、`workspace 角色` 按需、`exp`、`jti`）+ 长期 refresh（存哈希入 `sessions`）→ 返回。`remember=true` 延长 refresh 有效期。
3. **静默续期**：access 过期 → 前端用 refresh 调 `/auth/refresh` → 服务端校验 refresh 哈希、未撤销未过期 → 颁发新 access（可轮换 refresh 并撤销旧的，防重放）→ 更新 `last_active_at`。
4. **登出**：撤销当前 refresh（`revoked_at`）；「登出所有」批量撤销；**密码变更**时使该用户全部 refresh 与 PAT 之外的会话失效（PAT 单独管理）。

### 5.2 第三方 OAuth 登录流程（授权码 + PKCE）

1. 点「使用第三方账号登录」→ `GET /oauth/{provider}/start` 生成 `state`（防 CSRF）+ PKCE `code_verifier/challenge` → 302 到提供商授权页。
2. 用户授权 → 回调带 `code + state` → 服务端校验 `state`、用 `code` + `code_verifier` 换 access/id token。
3. 解析提供商 `sub + email`：
   - 命中已有 `oauth_identities` → 直接登录该 user。
   - 未命中但 email 已存在 `users` → 自动绑定到该账号（或要求先用密码登录再绑定，依安全策略）。
   - 全新 → 自动建 `users`（`password_hash=NULL`）+ `oauth_identities`，进入新用户引导。
4. 之后同密码登录一样颁发 access + refresh。

### 5.3 API token / agent 凭证使用流程 `[Mesh 特色]`

1. **创建**：成员或 admin 在设置里创建命名 token，选 scope 与过期 → 服务端生成高熵明文 + 存哈希 → **响应一次性返回明文** → UI 提示复制保存。
2. **CLI / runtime 调用**：把明文存入环境变量或密钥管理器（绝不硬编码进代码/仓库）→ 请求带 `Authorization: Bearer <token>`。
3. **服务端校验**：算哈希查表 → 校验未撤销/未过期 → 取 `workspace_id/owner_type/owner_id/scopes` 注入上下文 → 端点再按 scope + 角色做 RBAC 校验（scope 是角色的子集，取交集，最小权限）。
4. **agent 运行**：agent runtime 用 agent token 代表 agent 发评论、改状态、传附件；所有动作 `actor_type=agent`，留痕审计；agent token 默认不可 `agent:trigger` 他人或自身（防 agent-to-agent 回环），除非显式授权。
5. **撤销**：设置里撤销 → `revoked_at` 立即生效 → 后续请求 `401`。

### 5.4 授权校验流程（每请求）

1. 解析 Bearer token → 区分会话 JWT / API token / refresh。
2. 认证：验签（JWT）或查哈希（PAT）→ 得 `principal`（user 或 agent）+ `workspace 角色` + `scopes`。
3. 授权：端点声明所需权限（如 `@require("issue:write")`）→ 中间件比对「角色权限矩阵 ∩ token scopes」→ 不足则 `403`。
4. 资源级：进一步校验对该具体 issue/project 的可见性（如 guest 仅可见被共享资源）。
5. 审计：敏感写操作与认证事件写 `audit_logs`（异步落库，不阻塞主流程）。

### 5.5 实时性方案

- 认证体系本身基于 HTTP；WebSocket 连接建立时用 token 鉴权（连接握手携带或首条消息认证），服务端校验后按 `workspace_id + principal` 注册频道。
- **会话/token 撤销的实时生效**：撤销操作落库后，经内部事件总线通知网关，使相关 WebSocket 连接失效或下次心跳鉴权失败重连被拒；access JWT 因短期，撤销最长延迟 = 其 TTL（故 access 设短，如 15min）。
- 异常登录提醒可经 WebSocket 站内 + 邮件双通道。

### 5.6 安全细节汇总

- **密码哈希**：argon2id（首选，含 salt + 时间/内存成本参数）或 bcrypt（cost ≥ 12）；恒定时间比较；禁用明文与可逆加密。
- **凭证只存哈希**：refresh token、API token、重置令牌、邮箱验证令牌均存 SHA-256 哈希；明文只在创建/发送时短暂存在。
- **最小权限**：PAT/agent token 必须显式 scope，且不超过持有者角色权限；agent 默认最小角色。
- **防枚举**：登录/忘记密码统一响应文案与耗时；注册可加人机校验。
- **防 CSRF**：OAuth 用 `state` + PKCE；cookie 会话用 `SameSite=Lax/Strict` + CSRF token（若用 cookie 存 refresh）。
- **防 XSS 窃取 token**：优先把 refresh 放 httpOnly + Secure cookie，access 放内存（非 localStorage）；API token 由 CLI/runtime 从环境变量读取。
- **传输安全**：全站 HTTPS/HSTS；签名 URL 短时效。
- **审计不可篡改**：`audit_logs` append-only，含 IP/UA/前后值。
- **密钥轮换**：支持 JWT 签名密钥与加密密钥轮换；密码变更联动会话失效。

---

## 六、关键设计取舍小结（供 Spec 参考）

1. **JWT(access) + 服务端 refresh 混合**：access 短期无状态便于横向扩展，refresh 服务端记录支撑撤销与多设备管理——比纯 JWT 或纯 session cookie 更平衡。
2. **API token 是 Mesh 一等公民**：CLI 与 agent runtime 都靠它；只存哈希、一次性明文、显式 scope、可过期可撤销是底线。
3. **统一 principal 模型**：user 与 agent 都是 principal，RBAC 与审计对二者一致处理，仅以 `actor_type/owner_type` 区分 `[Mesh 特色]`。
4. **角色 × scope 取交集**：token scope 不能超越角色权限，落实最小权限。
5. **防回环是 agent 授权特有命题**：agent token 默认不授予「触发其他/自身 agent」权限，配合评论模块的「不提及即结束」共同终止 agent 间循环 `[Mesh 特色]`。
6. **审计 append-only + 限流前置**：合规与抗滥用的两道基础设施。
</antParameter>
</invoke>
