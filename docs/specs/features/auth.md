# 认证与授权(Auth)功能 Spec

> **所属层**:基础能力层(横切所有模块的安全基础设施)。
> **依赖的其他 Spec**:
> - `workspace.md`:`api_tokens.workspace_id`、`audit_logs.workspace_id` 外键;邀请接受调用本模块的注册/登录态。
> - `member.md`:统一名册 `members`(`member_type=human|agent`)是 principal 的落地;RBAC 的角色取自 `members.role`;agent 成员资格同样走 `members`(**不再单独设 `workspace_agents` 表**)。
> **被依赖方**:所有受保护端点的鉴权/授权/限流/审计均由本 Spec 提供。

---

## 1. 功能描述

### 1.1 模块定位

本模块提供 Mesh 的**认证(Authentication)** 与**授权(Authorization)** 基础设施:

- **认证**:注册/登录/邮箱验证/密码重置、第三方 OAuth 登录、会话(短期 access JWT + 可撤销 refresh)、API token(个人/agent 访问令牌)、可选 2FA、登录保护。
- **授权**:工作区角色 RBAC、资源级权限、权限校验中间件、最小权限的 agent 角色、审计日志、速率限制。

**统一 principal 模型(Mesh 特色)**:人类用户与 AI agent 都是 principal,RBAC 与审计对二者一致处理,仅以 `actor_type`/`owner_type` 区分。agent runtime 与 CLI 通过 agent 专属 API token 代表 agent 读写资源,所有动作留痕审计。

**会话模型取舍**:采用"短期无状态 access JWT(便于横向扩展)+ 服务端可撤销 refresh token(支撑撤销与多设备管理)"混合模型,比纯 JWT 或纯 session cookie 更平衡。access TTL 短(如 15min),使撤销最长延迟 = access TTL。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 典型用户场景 |
|---|--------|--------------|
| A1 | 邮箱+密码注册 | 新用户创建账号,发验证邮件 |
| A2 | 邮箱验证 | 点链接确认邮箱,未验证账号受限 |
| A3 | 邮箱+密码登录 | 校验 argon2id 哈希,颁发 access+refresh |
| A4 | 忘记密码/重置 | 短时效重置链接,重置并使旧会话失效 |
| A5 | 第三方 OAuth 登录 | 首次登录自动建号并绑定,后续免密 |
| A6 | OAuth 绑定/解绑 | 已有密码账号绑定第三方;保留至少一种登录方式 |
| A7 | 会话管理 | 记住我、有效期、多设备并存 |
| A8 | 登出/全端登出 | 单端登出;登出所有设备 |
| A9 | 会话列表与撤销 | 查看活跃会话(设备/IP/最近活跃),可撤销 |
| A10 | refresh 续期 | access 过期后用 refresh 静默续期,可轮换 |
| A11 | API token(个人访问令牌) **[Mesh 特色]** | 成员创建命名 token 供 CLI/脚本;明文仅一次 |
| A12 | token scope 与过期 | 最小权限 scope + 过期时间 |
| A13 | token 撤销 | 随时撤销,立即失效 |
| A14 | agent 身份凭证 **[Mesh 特色]** | 每个 agent 有专属 token,runtime 代表 agent 读写,受角色约束 |
| A15 | 2FA(TOTP,可选) | 密码之外再加一次性验证码 |
| A16 | 登录保护 | 失败计数锁定、异常登录提醒、凭据填充防护 |
| Z1 | 工作区角色 RBAC | owner/admin/member/guest 决定全局能力 |
| Z2 | 资源级权限 | 角色 × 资源矩阵 + guest 共享可见性 |
| Z3 | 权限校验中间件 | 端点声明所需权限,统一拦截 |
| Z4 | 最小权限 agent 角色 **[Mesh 特色]** | agent 默认仅完成工作所需最小权限 |
| Z5 | 防回环 **[Mesh 特色]** | agent token 默认不可触发其他/自身 agent |
| Z6 | 审计日志 | 登录/token/角色/敏感写,append-only,可查询 |
| Z7 | 速率限制 | 按 IP/账号/token 限流防暴力破解 |

### 1.3 边界与非目标(明确不做什么)

- **不**定义成员名册的增删改查 UI——归 `member.md`(本 Spec 只消费 `members.role` 做 RBAC)。
- **不**定义工作区/邀请的业务流程——归 `workspace.md`。
- **不**定义 agent 的运行时/技能/调度——归 `agent.md`(本 Spec 只为 agent 颁发凭证并约束其权限)。
- **不**实现计费结算。
- **不**支持自定义角色(YAGNI;角色为固定枚举,权限矩阵声明式维护)。
- **不**自建第三方 OAuth 提供商;以中性"第三方 OAuth 提供商"对接,不绑定具体厂商。

---

## 2. 数据模型

### 2.1 ER 概览(文字图)

```
users 1─* oauth_identities          (第三方登录绑定)
users 1─* sessions                  (refresh token / 会话,可撤销)
users 1─* members *─1 workspaces    (统一名册,member.md;角色来源)
agents 1─* members *─1 workspaces   (AI 成员资格同样走 members,不单设表)
workspaces 1─* api_tokens           (owner_type=member|agent;agent 运行凭证)
roles *─* permissions               (可选自定义 RBAC;内置角色硬编码)
(所有敏感动作) ─→ audit_logs        (append-only)
```

### 2.2 表:`users`(全局用户 / 跨工作区登录身份)

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK,`gen_random_uuid()` | 用户 ID |
| `email` | TEXT | NOT NULL,UNIQUE(小写归一,等价 citext) | 邮箱 |
| `email_verified_at` | TIMESTAMPTZ | NULL | 邮箱验证时间 |
| `password_hash` | TEXT | NULL | argon2id 哈希(OAuth-only 用户可为 NULL) |
| `password_changed_at` | TIMESTAMPTZ | NULL | 用于使旧会话失效 |
| `display_name` | TEXT | NOT NULL | 显示名 |
| `avatar_url` | TEXT | NULL | 头像 |
| `status` | TEXT | NOT NULL DEFAULT 'active',CHECK IN ('active','invited','disabled','deleted') | 账号状态 |
| `mfa_secret` | TEXT | NULL | TOTP 密钥(加密存储) |
| `mfa_enabled_at` | TIMESTAMPTZ | NULL | |
| `last_login_at` | TIMESTAMPTZ | NULL | |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL,`now()` | |

索引:`uq_users_email`(唯一);`idx_users_status`。

### 2.3 表:`oauth_identities`(第三方登录绑定)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `user_id` | UUID | NOT NULL,FK→users(id) ON DELETE CASCADE | |
| `provider` | TEXT | NOT NULL | 提供商标识(中性枚举,不绑定具体厂商) |
| `provider_subject` | TEXT | NOT NULL | 提供商侧唯一用户标识(sub) |
| `provider_email` | TEXT | NULL | 提供商返回的邮箱 |
| `access_token_ref` | TEXT | NULL | 加密存储的提供商令牌引用(如需调用其 API) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | |

约束:`UNIQUE (provider, provider_subject)`。索引:`idx_oauth_user (user_id)`。

### 2.4 表:`sessions`(会话 / refresh token,可撤销)

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK(即 refresh 的 `jti`) | |
| `user_id` | UUID | NOT NULL,FK→users(id) ON DELETE CASCADE | |
| `token_hash` | TEXT | NOT NULL,UNIQUE | refresh token 的 SHA-256 哈希(不存明文) |
| `type` | TEXT | NOT NULL DEFAULT 'web',CHECK IN ('web','cli','api') | 会话来源 |
| `user_agent` | TEXT | NULL | 客户端 UA |
| `ip_address` | INET | NULL | 创建时 IP |
| `created_at` | TIMESTAMPTZ | NOT NULL | |
| `last_active_at` | TIMESTAMPTZ | NULL | 最近活跃 |
| `expires_at` | TIMESTAMPTZ | NOT NULL | 过期时间 |
| `revoked_at` | TIMESTAMPTZ | NULL | 撤销时间(登出/全端登出/密码变更) |

索引:`idx_sessions_user (user_id) WHERE revoked_at IS NULL`;`uq_token_hash (token_hash)`。

### 2.5 表:`api_tokens`(个人 / agent 访问令牌)**[Mesh 特色]**

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK | token ID |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | 归属工作区 |
| `owner_type` | TEXT | NOT NULL,CHECK IN ('member','agent') | 持有者类型;agent 令牌即 agent 身份凭证 |
| `owner_id` | UUID | NOT NULL | 持有者 ID(`owner_type=member`→`members.id`;`agent`→`agents.id`) |
| `name` | TEXT | NOT NULL | 人类可读名称 |
| `token_hash` | TEXT | NOT NULL,UNIQUE | 明文令牌的 SHA-256 哈希(**仅存哈希**) |
| `prefix` | TEXT | NOT NULL | 令牌前缀(如 `mesh_pat_` 前 8~12 位,列表展示,不含秘密) |
| `scopes` | TEXT[] | NOT NULL DEFAULT '{}' | 权限范围(最小权限),如 `issue:read`、`comment:write` |
| `role_override` | TEXT | NULL | 可选:等效角色(不高于持有者角色) |
| `last_used_at` | TIMESTAMPTZ | NULL | 最近使用 |
| `last_used_ip` | INET | NULL | |
| `expires_at` | TIMESTAMPTZ | NULL | 过期时间(建议强制设置) |
| `revoked_at` | TIMESTAMPTZ | NULL | 撤销时间 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | |

索引:`uq_api_token_hash (token_hash)`;`idx_api_tokens_owner (workspace_id, owner_type, owner_id) WHERE revoked_at IS NULL`。

**设计要点**:
- 创建时生成高熵随机串(`mesh_pat_` + ≥32 字节 base64url),**只在创建响应里返回一次明文**,之后数据库仅存 `token_hash`,UI 只显示 `prefix` + 掩码。
- 校验:客户端 `Authorization: Bearer <明文>` → 服务端算哈希 → 查 `token_hash` → 命中且未撤销未过期 → 解析 `owner_type/owner_id/scopes/workspace_id` 注入请求上下文。
- 令牌自带可校验前缀/类型位,便于区分 PAT / agent token / refresh token。

### 2.6 表:`audit_logs`(审计日志,append-only)

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK | |
| `workspace_id` | UUID | NULL,FK→workspaces(id) | 账号级事件可为 NULL |
| `actor_type` | TEXT | NOT NULL,CHECK IN ('member','agent','system') | 行为者类型 |
| `actor_id` | UUID | NULL | 行为者 ID(member→`members.id`,agent→`agents.id`) |
| `action` | TEXT | NOT NULL | 如 `auth.login`、`auth.login_failed`、`token.created`、`token.revoked`、`member.role_changed`、`member.removed`、`issue.deleted` |
| `resource_type` | TEXT | NULL | 目标资源类型 |
| `resource_id` | UUID | NULL | 目标资源 ID |
| `ip_address` | INET | NULL | |
| `user_agent` | TEXT | NULL | |
| `metadata` | JSONB | NOT NULL DEFAULT '{}' | 变更前后值等上下文 |
| `created_at` | TIMESTAMPTZ | NOT NULL | |

索引:`idx_audit_ws_time (workspace_id, created_at DESC)`;`idx_audit_actor (actor_type, actor_id, created_at DESC)`;`idx_audit_action (workspace_id, action, created_at DESC)`。
**只追加**:不允许 UPDATE/DELETE(合规);可定期归档冷存储。

### 2.7 RBAC(角色 / 权限)

简化方案(默认):角色为固定枚举(owner/admin/member/guest),权限矩阵在代码里声明式维护。可扩展方案(自定义角色时才建表):

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `roles` | `id, workspace_id(null=系统内置), name, is_system` | 角色定义 |
| `permissions` | `id, resource, action` | 权限原子,如 `issue:read`、`agent:trigger`、`workspace:manage_members` |
| `role_permissions` | `role_id, permission_id` | 角色↔权限多对多 |

**资源 × 角色权限矩阵(内置示例)**:

| 权限 \ 角色 | owner | admin | member | guest | agent(默认) |
|-------------|:---:|:---:|:---:|:---:|:---:|
| `workspace:settings` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `workspace:manage_members` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `workspace:billing` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `project:manage` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `issue:read` | ✅ | ✅ | ✅ | ✅(受限) | ✅(受限) |
| `issue:write` | ✅ | ✅ | ✅ | ❌ | ✅(按 scope) |
| `comment:write` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `agent:trigger`(@提及触发运行)**[Mesh 特色]** | ✅ | ✅ | ✅ | ❌ | ⚠️受限(防回环) |
| `agent:manage` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `token:manage`(创建/撤销 token) | ✅ | ✅ | ✅(仅自己) | ❌ | ❌ |

### 2.8 与其他模块的外键关系

| 来源 | 外键 | 目标 | 说明 |
|------|------|------|------|
| `oauth_identities.user_id` | → `users.id` | 本模块 | |
| `sessions.user_id` | → `users.id` | 本模块 | |
| `api_tokens.workspace_id` | → `workspaces.id` | workspace.md | |
| `api_tokens.owner_id` | → `members.id` / `agents.id` | member.md / agent.md | 多态,由 `owner_type` 判别 |
| `members.user_id` / `role` | ← `users.id` | member.md | RBAC 角色来源 |
| `audit_logs.workspace_id` | → `workspaces.id` | workspace.md | |

> agent 在 workspace 的成员资格与角色统一落在 `members`(`member_type='agent'`),**不再单独维护 `workspace_agents` 表**;agent 的 `role` 受 `member.md` 的"agent 不可为 owner、通常 ≤ member"约束。

---

## 3. 接口设计

鉴权:除登录/注册/重置等公开端点外均需 `Authorization: Bearer <token>`。token 可为 ① 会话 access JWT(短期);② API token(长期,供 CLI/runtime);③ refresh(仅 `/auth/refresh`)。服务端按令牌格式/前缀路由到对应校验逻辑。游标分页,统一错误信封。

### 3.1 认证端点

| 方法 | 路径 | 说明 | 公开 |
|------|------|------|:---:|
| POST | `/api/v1/auth/register` | 邮箱+密码注册 | ✅ |
| POST | `/api/v1/auth/login` | 登录,返回 access + refresh | ✅ |
| POST | `/api/v1/auth/refresh` | refresh 换新 access(可轮换 refresh) | ✅ |
| POST | `/api/v1/auth/logout` | 登出当前会话(撤销 refresh) | |
| POST | `/api/v1/auth/logout-all` | 撤销该用户全部会话 | |
| POST | `/api/v1/auth/forgot-password` | 发起重置(恒返回成功,防枚举) | ✅ |
| POST | `/api/v1/auth/reset-password` | 凭重置令牌设新密码并使旧会话失效 | ✅ |
| POST | `/api/v1/auth/verify-email` | 验证邮箱 | ✅ |
| GET | `/api/v1/auth/oauth/{provider}/start` | 发起第三方登录(302,state + PKCE) | ✅ |
| GET/POST | `/api/v1/auth/oauth/{provider}/callback` | 回调:登录或自动注册并绑定 | ✅ |
| GET | `/api/v1/sessions` | 列出我的活跃会话 | |
| DELETE | `/api/v1/sessions/{id}` | 撤销指定会话 | |
| GET | `/api/v1/me` | 当前用户与所属工作区列表 | |

### 3.2 API token 端点 **[Mesh 特色]**

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/api/v1/workspaces/{ws}/api-tokens` | 列出我的/agent 的 token(仅 prefix + 元数据) | member(仅自己)/admin |
| POST | `/api/v1/workspaces/{ws}/api-tokens` | 创建 token(**响应仅一次返回明文**) | member(仅自己)/admin |
| DELETE | `/api/v1/workspaces/{ws}/api-tokens/{id}` | 撤销 token | 持有者/admin |
| POST | `/api/v1/agents/{agent_id}/tokens` | 为 agent 创建运行凭证 | `agent:manage` |

### 3.3 成员/角色/审计端点(衔接 member.md / workspace.md)

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| PATCH | `/api/v1/workspaces/{ws}/members/{id}` | 改角色(不可降唯一 owner) | admin |
| DELETE | `/api/v1/workspaces/{ws}/members/{id}` | 移除成员 | admin |
| GET | `/api/v1/workspaces/{ws}/audit-logs` | 查审计日志(过滤 + 游标分页) | admin |

### 3.4 请求/响应 JSON 示例

**登录** `POST /api/v1/auth/login`
```json
// Request
{ "email": "li@corp.com", "password": "...", "remember": true }
// 200 Response
{ "data": { "access_token": "eyJhbGci...", "token_type": "Bearer",
            "expires_in": 900, "refresh_token": "rt_..." } }
```

**注册** `POST /api/v1/auth/register`
```json
{ "email": "li@corp.com", "password": "...", "display_name": "李四" }
// 201:建 users(status=active),发验证邮件;密码强度校验(≥8 位含字母数字,拒常见弱密码)
```

**创建 API token** `POST /api/v1/workspaces/{ws}/api-tokens`
```json
// Request
{ "name": "code-reviewer runtime", "scopes": ["issue:read","comment:write","attachment:write"],
  "expires_at": "2027-01-01T00:00:00Z", "owner_type": "agent", "owner_id": "agt-222" }
// 201 Response(明文仅此一次)
{ "data": { "id": "tok-1", "name": "code-reviewer runtime", "prefix": "mesh_pat_Ab3",
            "token": "mesh_pat_Ab3Xy9...完整明文...", "scopes": ["issue:read","comment:write","attachment:write"],
            "expires_at": "2027-01-01T00:00:00Z" } }
```

### 3.5 错误码表

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 字段非法、密码太弱 |
| 401 | `unauthenticated` | 凭证缺失/无效/过期 |
| 401 | `email_not_verified` | 需先验证邮箱 |
| 403 | `forbidden` | 角色/scope 不足 |
| 404 | `not_found` | 资源不存在 |
| 409 | `conflict` | 邮箱已注册、唯一 owner 不可移除 |
| 422 | `invalid_credentials` | 邮箱或密码错误(**统一文案,防枚举**) |
| 423 | `account_locked` | 失败次数过多被临时锁定 |
| 429 | `rate_limited` | 触发限流,含 `Retry-After` |

### 3.6 速率限制(阈值示例,可调)

| 端点类 | 限制 | 维度 |
|--------|------|------|
| 登录 / 注册 / 重置 | 5 次/分钟,超出锁定 15 分钟 | IP + 邮箱 |
| 通用 API 读 | 300 req/分钟 | token / 用户 |
| 通用 API 写 | 120 req/分钟 | token / 用户 |
| 附件上传/下载 | 60 req/分钟 | token / IP |
| WebSocket 消息 | 60 msg/分钟 | 连接 |

实现:令牌桶/滑动窗口(Redis),响应头 `X-RateLimit-Limit/Remaining/Reset`;超限 429 + `Retry-After`。登录类叠加失败计数锁定与凭据填充防护。

### 3.7 WebSocket 鉴权与实时

- `/ws` 连接建立时用 token 鉴权(握手携带或首条消息认证),服务端校验后按 `workspace_id + principal` 注册频道;频道订阅 + 单调递增 `seq` + 断线重放(见各模块 Spec 事件表)。
- **会话/token 撤销实时生效**:撤销落库后经内部事件总线通知网关,使相关连接失效或下次心跳鉴权失败重连被拒;access JWT 短期,撤销最长延迟 = 其 TTL。
- 异常登录提醒经 WebSocket 站内 + 邮件双通道。

---

## 4. UI/UX 设计

### 4.1 认证页面

- **登录页**:邮箱+密码、「记住我」、「忘记密码?」、「使用第三方账号登录」按钮组;注册入口。失败统一提示「邮箱或密码不正确」,不暴露账号是否存在。
- **注册页**:邮箱+显示名+密码(强度条 + 实时校验);提交后跳「已发验证邮件」页。
- **忘记密码/重置**:单输入框发起;邮件链接进入重置页(新密码+确认+强度条)。

### 4.2 设置 → 安全(Settings → Security)

- **密码**:修改密码(旧+新+强度条)。
- **2FA**:启用 TOTP(密钥+二维码+验证码确认);生成备用码。
- **活跃会话列表**:每行 = 设备图标 + UA + IP + 最近活跃 +「当前」标记 +「撤销」;顶部「登出所有其他会话」。
- **第三方账号绑定**:已绑定列表(含解绑;唯一登录方式时灰化解绑)。

### 4.3 设置 → API Tokens **[Mesh 特色]**

- token 列表:名称 / prefix+掩码(`mesh_pat_Ab3…****`)/ scopes 标签 / 过期时间 / 最近使用 / 撤销按钮。
- 「新建 token」对话框:名称、scope 多选(按资源分组)、过期时间(建议必选);创建后弹出**一次性明文**展示框(复制按钮 + 醒目「关闭后无法再次查看」)。
- agent token 区:在 agent 设置里管理其运行凭证,标注「最小权限」与「防回环」说明。

### 4.4 设置 → 成员 / 审计(admin+)

- 成员表:头像+名称+邮箱+角色下拉+状态+移除;顶部「邀请成员」(衔接 workspace.md / member.md)。
- 审计日志页:时间/行为者/动作/资源/IP;按动作类型、行为者、时间范围筛选;只读、不可删。

### 4.5 关键流程(UX)

1. **注册**:校验强度与唯一性 → argon2id 哈希 → 建 `users` → 发验证邮件;未验证可登录但受限。
2. **登录**:恒定时间比较哈希 → 失败计数(达阈值锁定+可选验证码)→ 成功颁发短期 access JWT(含 `sub`/`exp`/`jti`)+ 长期 refresh(存哈希入 `sessions`)。`remember=true` 延长 refresh。
3. **静默续期**:access 过期 → 用 refresh 调 `/auth/refresh` → 校验哈希未撤销未过期 → 颁新 access(可轮换 refresh 并撤销旧的,防重放)→ 更新 `last_active_at`。
4. **登出**:撤销当前 refresh;「登出所有」批量撤销;**密码变更**使该用户全部 refresh 会话失效(PAT 单独管理)。
5. **OAuth(授权码 + PKCE)**:`start` 生成 `state`(防 CSRF)+ PKCE → 302 提供商 → 回调校验 `state`、用 `code`+`code_verifier` 换 token → 解析 sub+email:命中已有绑定→登录;email 已存在→绑定;全新→建 `users(password_hash=NULL)`+`oauth_identities`。
6. **API token / agent 凭证**:创建→存哈希、一次性明文→CLI/runtime 从环境变量读取(绝不硬编码)→请求带 Bearer→服务端查哈希、取上下文→scope ∩ 角色做 RBAC→agent 动作 `actor_type=agent` 留痕;agent token 默认不可 `agent:trigger`(防回环);撤销→`revoked_at` 立即生效→后续 401。

### 4.6 每请求授权校验流程

1. 解析 Bearer → 区分 JWT / API token / refresh。
2. 认证:验签(JWT)或查哈希(PAT)→ 得 principal(user 或 agent)+ 工作区角色 + scopes。
3. 授权:端点声明所需权限(如 `@require("issue:write")`)→ 比对「角色权限矩阵 ∩ token scopes」→ 不足 403。
4. 资源级:校验对具体 issue/project 的可见性(guest 仅可见被共享资源)。
5. 审计:敏感写操作与认证事件异步写 `audit_logs`(不阻塞主流程)。

---

## 5. 验收标准

### 5.1 功能性(认证)

- [ ] 注册校验密码强度(≥8 位含字母数字,拒常见弱密码/泄露密码),argon2id 哈希存储。
- [ ] 未验证邮箱账号登录受限(如不可创建工作区),验证后解除。
- [ ] 登录成功颁发短期 access JWT + 长期 refresh;refresh 仅存 SHA-256 哈希。
- [ ] 登录失败统一返回 422 `invalid_credentials`,不区分邮箱是否存在;恒定时间比较防时序攻击。
- [ ] 失败计数达阈值返回 423 `account_locked`。
- [ ] access 过期可用 refresh 静默续期;refresh 轮换后旧的立即失效(防重放)。
- [ ] 登出撤销当前 refresh;登出所有批量撤销;密码变更使全部 refresh 会话失效。
- [ ] 会话列表展示设备/UA/IP/最近活跃,可撤销指定会话。
- [ ] 忘记密码恒返回成功(防枚举);重置链接短时效,重置后旧会话失效。
- [ ] OAuth 登录用 state + PKCE;首次自动建号并绑定;解绑保留至少一种登录方式。
- [ ] 可选 2FA(TOTP)启用需验证码确认,并提供备用码。

### 5.2 功能性(API token / agent)**[Mesh 特色]**

- [ ] 创建 token 仅在响应中返回一次明文,数据库只存哈希,UI 仅显示 prefix+掩码。
- [ ] token 可设 scope 与过期时间;撤销立即生效,后续请求 401。
- [ ] token scope 与持有者角色权限**取交集**,不能超越角色权限(最小权限)。
- [ ] 可为 agent 创建运行凭证;agent 用其代表自身读写,所有动作 `actor_type=agent` 留痕。
- [ ] agent token 默认不授予 `agent:trigger`(防 agent-to-agent 回环),除非显式授权。
- [ ] token 前缀/类型位可区分 PAT / agent token / refresh。

### 5.3 功能性(授权 / 审计)

- [ ] RBAC 角色取自 `members.role`;权限矩阵声明式维护;端点用 `@require` 声明权限。
- [ ] 非授权访问返回 403;guest 仅可见被显式共享资源。
- [ ] 唯一 owner 不可移除/降级(409,衔接 member.md)。
- [ ] 登录/token 创建撤销/角色变更/敏感写均写 append-only `audit_logs`;审计表禁止 UPDATE/DELETE。
- [ ] 审计日志可按行为者、动作、时间范围查询(游标分页)。

### 5.4 性能

- [ ] 登录(含 argon2id 校验)P95 < 500ms。
- [ ] API token 哈希查表校验 P95 < 50ms(命中 `uq_api_token_hash`)。
- [ ] 每请求授权中间件开销 P95 < 10ms(权限矩阵内存化)。
- [ ] 限流判定走 Redis,P95 < 5ms。

### 5.5 安全

- [ ] 密码用 argon2id(salt + 时间/内存成本参数),恒定时间比较;禁用明文与可逆加密。
- [ ] refresh / API token / 重置令牌 / 验证令牌均只存 SHA-256 哈希;明文仅创建/发送时短暂存在。
- [ ] 防枚举:登录/忘记密码统一文案与耗时;注册可加人机校验。
- [ ] 防 CSRF:OAuth 用 state + PKCE;cookie 会话用 `SameSite` + CSRF token。
- [ ] 防 XSS 窃取:refresh 优先 httpOnly + Secure cookie,access 放内存;API token 由 CLI/runtime 从环境变量读取。
- [ ] 全站 HTTPS/HSTS;签名 URL 短时效。
- [ ] 支持 JWT 签名密钥与加密密钥轮换;密钥不出现在代码/仓库。
- [ ] 各端点限流生效,超限 429 + `Retry-After`;登录类叠加失败锁定。

### 5.6 实时

- [ ] WebSocket 连接握手鉴权,按 `workspace_id + principal` 注册频道。
- [ ] 会话/token 撤销后,相关连接在下次心跳被拒;access 撤销延迟 ≤ 其 TTL(15min)。
- [ ] 异常登录提醒经站内 + 邮件双通道送达。
- [ ] 频道事件携带单调递增 `seq`,断线重放无丢失无重复。
