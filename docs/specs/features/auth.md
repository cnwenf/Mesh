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

**统一 principal 模型(Mesh 特色)**:人类用户与 AI agent 都是 principal,RBAC 与审计对二者一致处理。人类/agent 判别**一律 JOIN `members.member_type`**(README §6.1),**存储层不设 `owner_type`/`actor_type` 之类冗余判别列**:API token 的持有者统一为 `owner_member_id`(指向 agent 的 member 行),审计行为者统一为 `actor_member_id` + `actor_kind∈('member','system')`。agent runtime 与 CLI 通过 agent 专属 API token 代表 agent 读写资源,所有动作留痕审计。

**会话模型取舍**:采用"短期无状态 access JWT(便于横向扩展)+ 服务端可撤销 refresh token(支撑撤销与多设备管理)"混合模型,比纯 JWT 或纯 session cookie 更平衡。access TTL 短(如 15min),使撤销最长延迟 = access TTL。

**无状态 access 的执行边界(评审 R3-H1 建立,R4-H2 写死为不变量 + 登记表,R5-H2 闭合,全链唯一口径)**:
- **不变量**:**常规资源路由的 Bearer 中间件不逐请求查 `sessions` 表**——对会话 access JWT 只做验签 + `exp` + claims 解析(横向扩展与低延迟的既定取舍)。这是唯一的硬边界;不以「仅 N 处查表」式绝对枚举表述(会掩盖正常会话管理路径,R4-H2 收口)。
- **会话生命周期操作登记表(查/写 `sessions` 的全部操作,按路径 + 读写目的完整登记;R4-H2 建立,R5-H2 闭合——硬边界只约束常规资源中间件,生命周期操作经本表显式授权,避免「白名单不闭合使已登记端点/首帧链路无法实现」)**:

<!-- sessions-registry:start -->

| 路径 / 入口 | 读写目的 |
|------|------|
| `POST /auth/login` | **写**:创建会话行、签发 refresh(Web 经 Set-Cookie;密码/OAuth 仅 Web 形态,§3) |
| `POST /auth/logout` | **读 + 写**:定位当前会话(Web 按 cookie / CLI 按 Bearer)并撤销 |
| `POST /auth/logout-all` | **读 + 写**:**批量撤销**该用户全部未撤销会话 |
| `POST /auth/refresh` | **读 + 写**:校验会话未撤销/未过期 + 轮换仲裁 / 有界宽限(§3.8) |
| `GET /auth/token` | **读**:自省当前凭证的会话元数据 |
| `DELETE /auth/token` | **写**:自撤销当前会话 |
| `POST /auth/reset-password` | **读 + 写**:密码重置后使该用户**全部会话失效**(凭重置令牌定位 user 后批量撤销) |
| `POST /auth/change-password` | **读 + 写**:按当前 access `sid` 识别并保留发起会话、更新 `authenticated_at`(R5-M1)、撤销其它会话 |
| `GET /sessions` / `DELETE /sessions/{id}` | **读 / 写**:会话列表与指定撤销 |
| `/ws` 握手鉴权(§3.7) | **读**:连接建立时校验 token、订阅逐资源授权;`session.revoked` 广播触发主动断开 |
| 个性化 HTML 入口中间件(theme.md §2.3 精确注入) | **读**:`mesh_session` cookie → 会话 → 请求者 `users.settings.theme` / 路由工作区默认,注入 `__MESH_APPEARANCE__`(**只读不写;响应 `Cache-Control: private, no-store`**) |

<!-- sessions-registry:end -->

  **登记表之外的任何路径不得查/写 `sessions` 表**;新增会话生命周期操作**必须先更新本登记表(路径 + 读写目的)再实现**——语义校验脚本(`tests/docs/check_semantic_consistency.py` 规则 Z)以 `sessions-registry` 标记块为锚,断言登记完整性(必需路径齐全:login / logout / logout-all / refresh / token / reset-password / change-password / sessions / WS / 个性化 HTML 入口),注入缺项坏样例必失败;
- **撤销语义**:会话撤销(登出/自撤销/指定撤销/改密撤销其它会话)→ refresh **立即失效**(登记表路径命中 `revoked_at` 即拒),**已签发 access 最迟于 TTL(≤15min)自然失效**,窗口内已撤销会话的 access 在常规路由**仍可通过**(不变量使然)——验收不得要求会话撤销对常规路由即时生效(PAT 无此窗口:`api_tokens.revoked_at` 逐请求查,撤销即时 401,长令牌对逐请求查表的负载可接受);WebSocket 连接经 `session.revoked` 实时广播主动失效(§3.7),不等 TTL。全链不得出现「常规请求按 `sid` 逐请求查 session 即时 401」的表述(与不变量互斥)。

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

> **全局契约引用**:本模块的成员模型、同租户约束、实时、API 包络/错误/分页一律以 [README.md](../README.md) §6「全局权威契约」为准,本 Spec 仅引用、不重复定义(成员模型 README §6.1、同租户复合 FK README §6.2、实时 README §6.7、API/错误/分页 README §6.14)。

### 2.1 ER 概览(文字图)

```
users 1─* oauth_identities          (第三方登录绑定)
users 1─* sessions                  (refresh token / 会话,可撤销)
users 1─* members *─1 workspaces    (统一名册,member.md;角色来源)
agents 1─* members *─1 workspaces   (AI 成员资格同样走 members,不单设表)
workspaces 1─* api_tokens           (owner_member_id → 持有者 member 行;agent 运行凭证)
roles *─* permissions               (可选自定义 RBAC;内置角色硬编码)
(所有敏感动作) ─→ audit_logs        (append-only;actor_member_id + actor_kind)
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
| `timezone` | TEXT | NULL | **用户展示层时区**(IANA 名,如 `Asia/Shanghai`;R3 补登记——此前 README §6.18/i18n.md 依赖本列而 users 模型未登记)。**仅影响展示层本地化渲染**,一切时间戳存储/传输仍 UTC RFC3339(README §6.18);NULL 时回退工作区 `timezone` 再回退 `UTC` |
| `settings` | JSONB | NOT NULL DEFAULT `'{}'` | **账号级展示偏好真源(R3 新增,此前 README §6.12/§6.18 与 i18n.md 依赖本字段而 users 模型缺失)**:`{"locale": "<BCP-47>", "theme": "light\|dark\|system\|null"}`——`locale` 为用户界面语言偏好(取值 BCP-47,首发 `zh-CN`/`en`,应用层校验在支持清单内,非法值 422);`theme` 为主题模式(README §6.12 主题切换契约,**类型写死 `light\|dark\|system\|null/absent`,默认 absent/null = 继承工作区默认**;显式 `system` = 忽略工作区、跟随 `prefers-color-scheme`;显式 `null` = 清除、恢复跟随工作区默认,theme.md §2.1 三值语义写死);未设置/为 `null` 的键走 locale 协商回退链(README §6.18)与主题协商链(工作区 `settings.default_theme` → `system`)。**写接口为 `PATCH /api/v1/users/me`(§3.1)** |
| `mfa_secret` | TEXT | NULL | TOTP 密钥(加密存储) |
| `mfa_enabled_at` | TIMESTAMPTZ | NULL | |
| `last_login_at` | TIMESTAMPTZ | NULL | |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL,`now()` | |

索引:`uq_users_email`(唯一);`idx_users_status`。

> **`users` 不设 `member_id` 列(README §6.1)**:`users` 是全局登录身份,**不含任何 `member_id` 反向列**(尤其禁止 `member_id UNIQUE` 这类 1:1 关联——它会令同一用户无法加入多个工作区)。工作区成员资格与角色**完全落在 `members`**:关联方向恒为 `members.user_id → users.id`,一个 `users.id` 可在多个工作区各有一条 `members` 行(每区角色独立)。

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

### 2.4 表:`sessions`(会话 / refresh token,可撤销;CLI/设备会话的 workspace/scope 真源)

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK | 会话 ID。**即 access JWT 的 `sid` 声明值与 refresh token 的 `jti` 值**(二者同指本行;access JWT 另有**逐枚唯一**的 `jti` 做单枚令牌标识,不与 `sid` 混用——评审 R2-H1 收口:此前「sessions.id = refresh jti」与「自省按 access jti 查 sessions」无可执行关联) |
| `user_id` | UUID | NOT NULL,FK→users(id) ON DELETE CASCADE | |
| `token_hash` | TEXT | NOT NULL,UNIQUE | 当前 refresh token 的 SHA-256 哈希(不存明文) |
| `previous_token_hash` | TEXT | NULL | **轮换前的上一枚 refresh 哈希(§3.8 有界幂等轮换)**:仅用于宽限窗内**识别**「被轮换掉的旧凭证」——宽限命中时**只发 access,不返回任何 refresh 明文、不二次轮换**(R5-H1:仅存哈希模型下无法还原胜者明文);宽限过后清空(NULL);UNIQUE(NULL 不冲突) |
| `rotated_at` | TIMESTAMPTZ | NULL | 最近一次轮换时刻(与 `previous_token_hash` 同事务置位;`now() - rotated_at ≤ 宽限窗` 时旧凭证走宽限路径) |
| `authenticated_at` | TIMESTAMPTZ | NOT NULL DEFAULT `now()` | **最近一次主动认证时刻(R5-M1:step-up 再认证状态唯一真源)**:会话创建(登录)时置位;用户在会话上完成 step-up 再认证时更新为 `now()`(如改密保留发起会话 §3.1、TOTP 再校验);**step-up 有效期判定 = `now() - authenticated_at ≤ MESH_STEP_UP_WINDOW_SECONDS`(默认 900s,§5.5)**,无独立过期列(窗口比对即判据);会话撤销后自然失效 |
| `type` | TEXT | NOT NULL DEFAULT 'web',CHECK IN ('web','cli','api') | 会话来源(`cli` = 设备码登录) |
| `workspace_id` | UUID | NULL,FK→workspaces(id) ON DELETE CASCADE | **CLI/设备会话绑定的工作区**(批准页显式选定,§3.1.1);`web` 会话为 NULL(多工作区交互式会话按请求路径解析工作区)。**CHECK:`type='cli'` 时 `workspace_id` 必须非空**——设备会话的后续请求与 refresh 续签一律以此列为工作区真源,不重新选择 |
| `granted_scopes` | TEXT[] | NOT NULL DEFAULT '{}' | **会话固化的签发 scope**(登录/批准时取交结果:请求 scope ∩ 当时角色权限)。**refresh 续签时从此列取固化 scope 并与当前角色权限再次取交**(角色降权后旧 scope 不延续);`web` 会话为空数组(权限按角色实时计算) |
| `device_authorization_id` | UUID | NULL,**UNIQUE**,FK→device_authorizations(id) ON DELETE SET NULL | 产生本会话的设备授权记录(§2.4.2,**单次消费 → 至多一个会话**,UNIQUE 保证);供审计回溯与撤销联动 |
| `user_agent` | TEXT | NULL | 客户端 UA |
| `ip_address` | INET | NULL | 创建时 IP |
| `created_at` | TIMESTAMPTZ | NOT NULL | |
| `last_active_at` | TIMESTAMPTZ | NULL | 最近活跃 |
| `expires_at` | TIMESTAMPTZ | NOT NULL | 过期时间(refresh 生命周期) |
| `revoked_at` | TIMESTAMPTZ | NULL | 撤销时间(登出/全端登出/密码变更/自撤销) |

约束 / 索引:
- `CHECK (type <> 'cli' OR workspace_id IS NOT NULL)`(设备会话必有绑定工作区);
- `uq_token_hash (token_hash)`;`uq_sessions_device_auth (device_authorization_id)`(NULL 不冲突);
- `idx_sessions_user (user_id) WHERE revoked_at IS NULL`。

> **access JWT 声明(写死)**:`{sub: user_id, sid: session.id, jti: <本枚 access 唯一>, workspace_id?: <设备会话绑定值>, scope?: <固化 scope>, exp, iat}`。**常规路由只验签 + `exp` + claims,不按 `sid` 查表**(§1.1 不变量);`sid` 查表仅限 §1.1 **会话生命周期操作登记表**(自省/自撤销/续期/登出/改密保留发起会话/会话列表与指定撤销);`/auth/refresh` 按 refresh `jti`(= session.id)校验会话未撤销;`jti` 仅用于单枚 access 的审计/去重,不承担会话关联。撤销 session 后 refresh 立即失效,已签发 access 最迟 TTL 自然失效(窗口内常规路由仍可通过,§1.1/§3.7/§5.5);WS 连接经 `session.revoked` 广播主动断开。

### 2.4.1 表:`password_reset_tokens` 与 `email_verification_tokens`(一次性令牌)

> 密码重置令牌与邮箱验证令牌同样**仅存哈希**,需独立落库表支撑 TTL 与单次消费约束。

**`password_reset_tokens`**:

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK | |
| `user_id` | UUID | NOT NULL,FK→users(id) ON DELETE CASCADE | |
| `token_hash` | TEXT | NOT NULL,UNIQUE | 重置令牌的 SHA-256 哈希(不存明文) |
| `expires_at` | TIMESTAMPTZ | NOT NULL | 过期时间(默认创建后 1 小时) |
| `consumed_at` | TIMESTAMPTZ | NULL | 消费时间(单次消费,消费后不可重用) |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | |

**`email_verification_tokens`**:结构同上(`user_id`、`token_hash` UNIQUE、`expires_at` 默认 24 小时、`consumed_at`、`created_at`)。

> **约束**:两类令牌均为**单次消费**(`consumed_at IS NULL` 方可使用,消费即置位);过期(`expires_at < now()`)或已消费的令牌一律拒绝;创建新令牌时作废旧的同类未完成令牌。

### 2.4.2 表:`device_authorizations`(OAuth 设备码授权,CLI 登录)**[auth.md 增量,MES-76 评审 H7 闭环]**

> CLI 设备码登录(cli.md §3.2 定义**流程契约**)的**服务端权威落地**:表结构、状态机、确认/轮询端点、限流、审计与码爆破防护全部在本 Spec 闭环(cli.md 不再重复定义,只引用本节)。与 MES-75 安全评审 H2(设备码爆破防护量化)在本节合并为唯一落点。

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK,`gen_random_uuid()` | 授权记录 ID |
| `device_code_hash` | TEXT | NOT NULL,UNIQUE | `device_code` 的 **HMAC-SHA256(服务端 pepper)** 哈希(不存明文;高熵码亦不以裸 SHA-256 落库,统一 keyed hash)。128bit 熵空间无耗尽问题,**全历史 UNIQUE** |
| `user_code_hash` | TEXT | NOT NULL | `user_code` 的 **HMAC-SHA256(服务端 pepper)** 哈希——`user_code` 低熵(≥20bit),**裸 SHA-256 不足以抵御离线/在线爆破,必须 keyed hash**;pepper 为独立服务端密钥(`MESH_DEVICE_CODE_PEPPER`,不进仓库,生产缺失 fail-closed,同 §5.5 签名密钥基线)。**唯一性为部分唯一索引,仅覆盖 active 码**(见下,评审 R2-M3) |
| `status` | TEXT | NOT NULL DEFAULT 'pending',CHECK IN ('pending','approved','denied','consumed','expired','invalidated') | 授权状态机(见下) |
| `requested_scopes` | TEXT[] | NOT NULL DEFAULT '{}' | 客户端请求的 scope 集合 |
| `granted_scopes` | TEXT[] | NULL | **批准时固化的实际签发 scope = 请求 scope ∩ 批准用户角色权限**(服务端强制取交,§3.2) |
| `approved_by_user_id` | UUID | NULL,FK→users(id) ON DELETE SET NULL | 批准者(浏览器登录态用户);denied 时为拒绝者 |
| `workspace_id` | UUID | NULL,FK→workspaces(id) ON DELETE SET NULL | 批准所绑定的工作区(批准者在确认页选定;签发会话/令牌归属之;**多工作区用户必须显式选择**,不默认首个) |
| `failed_attempts` | INT | NOT NULL DEFAULT 0 | 轮询端点针对本码的累计违规/猜错计数(爆破防护,见下) |
| `request_ip` | INET | NULL | 取码请求来源 IP(审计 + 限流维度) |
| `approved_at` | TIMESTAMPTZ | NULL | 批准时间 |
| `denied_at` | TIMESTAMPTZ | NULL | 拒绝时间 |
| `consumed_at` | TIMESTAMPTZ | NULL | 单次消费时间(token 端点换取会话时置位) |
| `invalidated_at` | TIMESTAMPTZ | NULL | 作废时间(猜错超限/滥用) |
| `expires_at` | TIMESTAMPTZ | NOT NULL | 过期时间(默认创建后 **15 分钟**) |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | |

索引:
- `uq_device_auth_device_code (device_code_hash)`(全表 UNIQUE,128bit 无空间耗尽);
- **`uq_device_auth_user_code_active (user_code_hash) WHERE status IN ('pending','approved')`——部分唯一索引(评审 R2-M3)**:仅约束**活跃码**(pending/approved)互斥,终态(consumed/expired/denied/invalidated)行的哈希**允许复用**——20bit 短码空间若对全历史永久 UNIQUE,会随累计记录最终耗尽取码;active 集合受 TTL(15min)与限速天然有界,部分唯一既防活跃期碰撞又不堵死码空间;
- `idx_device_auth_pending (expires_at) WHERE status = 'pending'`(过期清理扫描)。

**状态机(写死,终态不可逆)**:

```
pending ──浏览器确认页批准(登录态 + CSRF + 手工录入 user_code)──► approved
pending ──浏览器确认页拒绝──► denied(终态)
pending ──TTL 过期(reaper/惰性)──► expired(终态)
pending ──单码连续猜错/限速违规超限──► invalidated(终态,+ 审计)
approved ──token 端点原子单次消费──► consumed(终态;同事务创建 sessions 行)
approved ──TTL 过期──► expired(未被消费即过期)
```

**约束**:
- **单次消费原子性**:token 端点以条件更新消费——`UPDATE device_authorizations SET status='consumed', consumed_at=now() WHERE id=$1 AND status='approved' AND consumed_at IS NULL AND expires_at > now()`,**影响行数恰为 1 方可继续**(同事务创建 `sessions` 行,type='cli',复用 §3.7 撤销链路);并发/重复消费命中 0 行即拒;
- **码生成(量化,可验收)**:`user_code` 熵 **≥20bit**(RFC 8628 §6.1 基线)+ **去歧义字符集**(剔除 `0/O/1/I/L`,分组展示如 `XXXX-XXXX`);`device_code` 熵 **≥128bit**(密码学安全随机源);两码明文仅在取码响应中出现一次,落库仅存 HMAC 哈希;**`user_code` 生成时若命中部分唯一索引冲突(与活跃码碰撞)则重新生成(重试上限 5 次,超限 `500 internal_error` 并告警)**——active 集合受 15min TTL 有界,碰撞率极低但必须可恢复;
- **爆破防护(量化)**:轮询端点**双重限速**——按来源 IP 全局限速 + 按 `device_code` 限速(阈值见 §3.6),违规返回 `429 slow_down`(携带 `Retry-After`,客户端间隔 +5s);**累计违规超限(单码 `failed_attempts > 5`)→ 立即作废该记录(`status='invalidated`)+ 审计 `auth.device_invalidated`**;`device_code` 命中后须比对 `status='approved'`(消费阶段)且未过期方可推进,pending 返回 `authorization_pending` 继续轮询;
- **过期清理**:reaper/惰性扫描将 `expires_at < now()` 且 `status IN ('pending','approved')` 的行置 `expired`。

### 2.5 表:`api_tokens`(个人 / agent 访问令牌)**[Mesh 特色]**

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK | token ID |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | 归属工作区 |
| `owner_member_id` | UUID | NOT NULL,复合 FK `(workspace_id, owner_member_id) → members(workspace_id, id)` | 持有者名册条目(统一 `members.id`;agent 令牌由 agent 的 member 行持有,README §6.1/§6.2) |
| `name` | TEXT | NOT NULL | 人类可读名称 |
| `token_hash` | TEXT | NOT NULL,UNIQUE | 明文令牌的 SHA-256 哈希(**仅存哈希**) |
| `prefix` | TEXT | NOT NULL | 令牌前缀(如 `mesh_pat_` 前 8~12 位,列表展示,不含秘密) |
| `scopes` | TEXT[] | NOT NULL DEFAULT '{}' | 权限范围(最小权限),如 `issue:read`、`comment:write` |
| `role_override` | TEXT | NULL | 可选:等效角色(**服务端强校验:不得高于持有者当前角色,创建/使用时双重校验**,违反返回 422) |
| `last_used_at` | TIMESTAMPTZ | NULL | 最近使用 |
| `last_used_ip` | INET | NULL | |
| `expires_at` | TIMESTAMPTZ | NULL | 过期时间(建议强制设置) |
| `revoked_at` | TIMESTAMPTZ | NULL | 撤销时间 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | |

索引:`uq_api_token_hash (token_hash)`;`idx_api_tokens_owner (workspace_id, owner_member_id) WHERE revoked_at IS NULL`。

**设计要点**:
- 创建时生成高熵随机串(**前缀随持有者类型:人类成员 `mesh_pat_`、agent 成员 `mesh_agt_`**,§2.5.1;+ ≥32 字节 base64url),**只在创建响应里返回一次明文**,之后数据库仅存 `token_hash`,UI 只显示 `prefix` + 掩码。
- **持有者统一为 `owner_member_id`(去多态,README §6.1)**:不再用 `owner_type/owner_id` 二元组。人类 PAT 指向本人的 member 行;**agent 运行凭证指向该 agent 的 member 行**(`members.member_type='agent'`),由复合 FK `(workspace_id, owner_member_id) → members(workspace_id, id)` 保证同租户;人类/agent 判别一律 JOIN `members.member_type`,**不存冗余 `owner_type` 列**。
- 校验:客户端 `Authorization: Bearer <明文>` → 服务端算哈希 → 查 `token_hash` → 命中且未撤销未过期 → 解析 `owner_member_id/scopes/workspace_id` 注入请求上下文(经 `members` 解析 principal 类型与角色)。
- 令牌自带可校验前缀/类型位,便于区分 PAT / agent token / refresh token——**前缀取值以 §2.5.1 注册表为唯一权威**。

#### 2.5.1 令牌前缀注册表(唯一权威,评审 H7 收口)

一切 Mesh 签发的凭证明文前缀**统一登记于本表**(auth.md owns),各 Spec 与代码示例**只可引用,不得新造前缀**(此前 `mesh_pat_`/`mesh_agt_`/`mesh_rt_` 与 runtime.md 示例 `rt_live_`、会话示例 `rt_` 多处冲突,本表收口为唯一来源):

| 前缀 | 凭证类型 | 存储 / 载体(唯一真源) | 持有者 | 使用边界 | **类型语义(校验时强制)** |
|------|----------|-------------|--------|----------|----------|
| `mesh_pat_` | 个人访问令牌(PAT) | `api_tokens.token_hash`(SHA-256) | 人类成员的 member 行 | 任意 `/api/v1`(权限 = scopes ∩ 角色);CLI / 脚本 / CI | 命中行的 `owner_member_id` JOIN `members.member_type='human'`,否则拒绝 |
| `mesh_agt_` | agent 运行凭证 | `api_tokens.token_hash`(SHA-256) | agent 的 member 行 | 任意 `/api/v1`(权限 = scopes ∩ 角色;默认不含 `agent:trigger` 防回环) | 命中行 JOIN `members.member_type='agent'`,否则拒绝 |
| `mesh_rt_` | runtime 守护进程令牌 | **`runtimes.runtime_token_hash`(SHA-256,runtime.md §2 owns;R2-H2 写死:不入 `api_tokens`——runtime 非名册成员,`owner_member_id NOT NULL` 无法承载)** | runtime(机器) | **仅 `/api/v1/daemon/*` 命名空间**(runtime.md §3.2),不得调控制台 API | 仅以 `runtimes` 表校验(哈希 + runtime_id 匹配);常规路由的 Bearer 依赖对 `mesh_rt_` 一律拒绝 |
| `mesh_rft_` | 会话 refresh token | `sessions.token_hash`(SHA-256) | 用户会话 | **仅 `POST /api/v1/auth/refresh`** | 仅 refresh 端点受理;其他端点出现即拒绝 |
| (无前缀,JWT 格式) | 会话 access JWT | 无状态验签(**常规路由不查表**,R3-H1 不变量) | 用户会话 | 任意 `/api/v1`(会话权限),TTL ≤15min | 验签 + exp + claims 有效;**`sid` 查表仅限 §1.1 会话生命周期操作登记表**(R4-H2/R5-H2,不以「三处」绝对枚举表述) |

> **注册表校验 = 词形 + 类型语义(R2-H2 写死)**:校验链先按前缀路由到**对应存储表**(词形),再断言**持有者类型与使用边界**(类型语义)——`mesh_agt_` 前缀的令牌命中 human 成员行、`mesh_rt_` 出现在常规路由、`mesh_rft_` 出现在 refresh 以外端点,一律拒绝并告警。扫描/测试不止检查「前缀字符串存在」,还断言示例与实现中**前缀 ⇄ 存储表 ⇄ 持有者类型**三者绑定一致(§5.2)。

> **非 Bearer 凭证不进本表**:一次性激活码(`ACT-XXXX-XXXX-XXXX` 分组码,runtime.md)、设备码 `user_code`(分组短码)/`device_code`(高熵,仅存 HMAC 哈希)、密码重置/邮箱验证令牌均非 `Authorization: Bearer` 凭证,各自形态见所属 Spec。
>
> **统一 Bearer 鉴权依赖(评审 H7 收口,写死)**:常规 REST 路由的鉴权依赖**按前缀路由到统一校验链**——JWT(验签,固定 alg)→ `mesh_pat_`/`mesh_agt_`(查 `api_tokens` 哈希,取 scopes/workspace)→ 其余前缀(含 `mesh_rt_`/`mesh_rft_`)在常规路由一律拒绝(daemon 命名空间单独只认 `mesh_rt_`);**有效权限恒为「scopes ∩ 持有者角色权限」**。「持 PAT 调用任意 `/api/v1` 端点」不再依赖个别端点单独解析 PAT;该依赖的代表性端点集成测试见 §5.2。

### 2.6 表:`audit_logs`(审计日志,append-only)

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK | |
| `workspace_id` | UUID | NULL,FK→workspaces(id) | 账号级事件可为 NULL |
| `actor_member_id` | UUID | NULL,复合 FK `(workspace_id, actor_member_id) → members(workspace_id, id)`(非空时校验) | 行为者名册条目(人或 agent;系统动作为 NULL,README §6.1/§6.2) |
| `actor_kind` | TEXT | NOT NULL,CHECK IN ('member','system') | 行为者类别:`member`=名册成员(人/agent 由 JOIN `members.member_type` 判别,**不存冗余 `actor_type`**),`system`=系统(允许 `actor_member_id` 为 NULL) |
| `action` | TEXT | NOT NULL | 如 `auth.login`、`auth.login_failed`、`auth.logout`、`auth.device_code_issued`、`auth.device_approved`、`auth.device_denied`、`auth.device_consumed`、`auth.device_invalidated`(设备码爆破防护作废,§2.4.2)、`token.created`、`token.revoked`、`user.password_changed`(已登录态修改密码,账号级事件:`workspace_id` 为 NULL,行为者落 `metadata.user_id`)、`member.role_changed`、`member.removed`、`issue.deleted` |
| `resource_type` | TEXT | NULL | 目标资源类型 |
| `resource_id` | UUID | NULL | 目标资源 ID |
| `ip_address` | INET | NULL | |
| `user_agent` | TEXT | NULL | |
| `metadata` | JSONB | NOT NULL DEFAULT '{}' | 变更前后值等上下文 |
| `created_at` | TIMESTAMPTZ | NOT NULL | |

索引:`idx_audit_ws_time (workspace_id, created_at DESC)`;`idx_audit_actor (workspace_id, actor_member_id, created_at DESC)`;`idx_audit_action (workspace_id, action, created_at DESC)`。

> **行为者去多态(README §6.1)**:不再用 `actor_type('member','agent','system') + actor_id`。`actor_kind` 仅二值——`member`(名册成员,人类/agent 之分由 JOIN `members.member_type` 得出,不另存)与 `system`(系统动作,`actor_member_id` 为 NULL)。`actor_member_id` 的复合 FK 在 `workspace_id` 非空时生效;账号级事件(如登录,`workspace_id` 为 NULL)按 SQL 复合 FK 语义不校验,行为者信息落在 `metadata`。
>
> **只追加**:不允许 UPDATE/DELETE(合规);可定期归档冷存储。

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
| `chat:write`(与 agent 实时聊天;发消息即触发执行,MES-67 L3) | ✅ | ✅ | ✅ | ❌ | ❌ |
| `agent:trigger`(@提及触发运行)**[Mesh 特色]** | ✅ | ✅ | ✅ | ❌ | ⚠️受限(防回环) |
| `agent:manage` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `autopilot:manage`(自动化规则管理 / kill switch) | ✅ | ✅ | ❌ | ❌ | ❌ |
| `token:manage`(创建/撤销 token) | ✅ | ✅ | ✅(仅自己) | ❌ | ❌ |

### 2.8 与其他模块的外键关系

| 来源 | 外键 | 目标 | 说明 |
|------|------|------|------|
| `oauth_identities.user_id` | → `users.id` | 本模块 | |
| `sessions.user_id` | → `users.id` | 本模块 | |
| `api_tokens.workspace_id` | → `workspaces.id` | workspace.md | |
| `api_tokens.owner_member_id` | 复合 FK `(workspace_id, owner_member_id)` → `members(workspace_id, id)` | member.md | 持有者名册条目(人或 agent 的 member 行;README §6.1/§6.2) |
| `audit_logs.actor_member_id` | 复合 FK `(workspace_id, actor_member_id)` → `members(workspace_id, id)`(非空时) | member.md | 行为者名册条目;`system` 动作为 NULL |
| `members.user_id` | `members.user_id → users.id`(关联方向恒为 members → users,**`users` 不设 `member_id` 反向列**) | member.md / 本模块 | RBAC 角色来源(README §6.1) |
| `audit_logs.workspace_id` | → `workspaces.id` | workspace.md | |

> agent 在 workspace 的成员资格与角色统一落在 `members`(`member_type='agent'`),**不再单独维护 `workspace_agents` 表**;agent 的 `role` 受 `member.md` 的"agent 不可为 owner、通常 ≤ member"约束。
>
> **同租户复合 FK 约定(README §6.2)**:本模块凡引用 `members` 的列(`api_tokens.owner_member_id`、`audit_logs.actor_member_id`)均**同时存 `workspace_id` 并建复合 FK** `(workspace_id, <ref>_id) → members(workspace_id, id)`,使"引用别的工作区的成员"在 INSERT 时即被数据库拒绝(集成测试 T1);`members` 表建有 `UNIQUE(workspace_id, id)` 供此引用(member.md)。

---

## 3. 接口设计

鉴权:除登录/注册/重置等公开端点外均需 `Authorization: Bearer <token>`。token 可为 ① 会话 access JWT(短期);② API token(长期,供 **CLI/agent**,`mesh_pat_`/`mesh_agt_`);③ refresh(仅 `/auth/refresh`,`mesh_rft_`)。**runtime 机器令牌(`mesh_rt_`)不经本模块 API token 体系——仅存 `runtimes.runtime_token_hash`,仅 daemon 命名空间校验(runtime.md §3.5,R2-H2)**。服务端按令牌格式/前缀路由到对应校验逻辑(§2.5.1 类型语义)。游标分页,统一错误信封。

> **登录形态固定 + refresh 凭证定性(R4-H1 写死,取代 R3-H2 自报形态)**:
> - **登录形态由服务端固定,调用方不得自报**:**密码 / OAuth 登录仅 Web 形态**——成功后经 `Set-Cookie: mesh_session=mesh_rft_…; Secure; HttpOnly; SameSite=Strict; Path=/` 下发 refresh,响应体**只有 access JWT + 元数据,绝无 refresh 明文**。**CLI / API 非浏览器客户端只走设备授权流(§3.1.1,token 端点返回 `mesh_rft_…` Bearer)或 PAT(`mesh_pat_`)**——**不提供非浏览器密码流**:服务端没有可信客户端身份时,由请求参数自报形态(此前 `Accept: text/html` 协商 / `client=web` 参数)即绕过面——同源脚本可自报非 Web 形态从响应体取 refresh,该分支已删除;
> - **refresh 是完整会话凭证(威胁模型修正)**:refresh 能换出 access,即持有该会话的**完整账号能力(scope 内)**——`SameSite=Strict` + `Origin`/`Referer` 同源校验是 **CSRF 防护,不是被盗 cookie 的持有证明**(经 XSS 之外渠道窃取的 cookie 仍可换出 access);且该 cookie 因 `Path=/` 亦随 HTML 入口请求携带、用于入口主题注入(theme.md §2.3),**能力面不止 refresh**。此前「cookie 泄漏不构成 API 能力」表述已撤销。故纵深防御为:HttpOnly 堵 XSS 读取路径 + **仅 refresh 路由认 cookie**(其余 `/api/v1` 只认 Bearer,把 cookie 爆炸半径限制在会话生命周期与 scope 内)+ 短 access TTL + 撤销联动 + 敏感操作 step-up 再认证(§5.5)+ 异常登录检测;
> - **传输形态 ↔ 会话来源(合法性来自签发来源,非调用方声明)**:`/auth/refresh` 接受两种传输——cookie `mesh_session`(Web 密码/OAuth 来源会话,`type='web'`)与 `Authorization: Bearer mesh_rft_…`(设备授权来源会话,`type='cli'`)。**Web 会话永不签发 Bearer refresh(JS 无从获得),设备会话永不签发 cookie(无浏览器)**;同一请求只认一种传输。CSRF 防护:`SameSite=Strict` + `Origin`/`Referer` 与本站同源校验(缺失/跨源 → 403)。轮换经 `Set-Cookie`/响应体下发新 refresh 并撤销旧的(**多 tab 轮换竞态按 §3.8 有界幂等轮换契约**,防误登出);前端 access 只存**内存**(不进 localStorage);
> - **个性化入口缓存边界(R3-H2)**:HTML 入口文档凡含 `__MESH_APPEARANCE__` 或任何按请求者定制的内容(已登录入口 / 邀请入口,theme.md §2.3 精确注入链路),响应头**必须 `Cache-Control: private, no-store`**;**可缓存的静态 shell**(无个性化骨架 HTML、内容哈希命名的 JS/CSS/字体,长效缓存)与**个性化 HTML 物理分离**(入口文档仅含 shell 引用 + 注入脚本,不含可长期缓存的公共内容),杜绝共享 CDN/代理把 A 的注入结果发给 B;邀请预览为公开数据但按 token 个性化,同样 `no-store`。

### 3.1 认证端点

| 方法 | 路径 | 说明 | 公开 |
|------|------|------|:---:|
| POST | `/api/v1/auth/register` | 邮箱+密码注册 | ✅ |
| POST | `/api/v1/auth/login` | 密码登录(**仅 Web 形态,R4-H1**):校验成功 → 响应体仅 access JWT + 元数据,refresh **仅经 `Set-Cookie mesh_session`(HttpOnly/Secure/SameSite=Strict/Path=/)下发,绝不进响应体**;**不接受调用方自报客户端形态,不提供非浏览器密码流**(CLI 走设备授权 §3.1.1 / PAT) | ✅ |
| POST | `/api/v1/auth/refresh` | refresh 换新 access(可轮换 refresh)。**传输形态由会话签发来源决定(R4-H1,非调用方自报)**:Web 密码/OAuth 来源会话认 HttpOnly cookie `mesh_session`(SameSite=Strict + Origin/Referer 同源校验,轮换经 `Set-Cookie` 下发,**多 tab 竞态按 §3.8 有界幂等轮换**);设备授权来源会话认 `Authorization: Bearer mesh_rft_…`;同请求只认其一。**新 access 的 scope = 会话 `granted_scopes` 固化值 ∩ 持有者当前角色权限**(R2-H1:角色降权后旧 scope 不延续;`web` 会话 granted_scopes 为空,按角色实时计算);新 access 继承 `sid`、另发逐枚唯一 `jti`;设备会话 access 继承 `workspace_id` 声明;**校验按 refresh `jti`(= session.id)命中 sessions 行且 `revoked_at` 为空**(属 §1.1 会话生命周期操作登记表) | ✅ |
| POST | `/api/v1/auth/logout` | 登出当前会话(撤销 refresh) | |
| POST | `/api/v1/auth/logout-all` | 撤销该用户全部会话 | |
| POST | `/api/v1/auth/forgot-password` | 发起重置(恒返回成功,防枚举) | ✅ |
| POST | `/api/v1/auth/reset-password` | 凭重置令牌设新密码并使旧会话失效 | ✅ |
| POST | `/api/v1/auth/change-password` | **已登录态修改密码(§4.2)**:body `{old_password, new_password}`——校验旧密码(argon2id 恒定时间比较;错误 → `422 invalid_credentials`)。**旧密码重输即 §5.5 敏感操作 step-up 再认证**(「近期重新输入密码」由本表单天然满足,不另设再认证门槛)→ 校验新密码强度(复用注册策略 §5.1;弱 → `400 weak_password`,`details.reason ∈ too_short/needs_letter_and_digit/too_common`)→ 更新 `password_hash` + `password_changed_at=now()` → **使该用户其它 refresh 会话失效**(**发起会话以当前请求 access JWT 的 `sid` 识别并保留**(R4-H1:Web JS 读不到 refresh,body 不再传 `refresh_token?`;`sid` 属 §1.1 会话生命周期登记表操作),刷新其 `authenticated_at=now()`(step-up 状态唯一真源,§2.4,R5-M1);`sid` 缺失/会话已撤销则全部失效;PAT 单独管理;撤销经 §3.7/§5.6 outbox→realtime 广播)→ 写账号级审计 `user.password_changed`(§2.6)。限流同登录类(§3.6,(IP, 邮箱) 5 次/分钟);成功 `200 {"data": {"status": "ok"}}` | |
| POST | `/api/v1/auth/verify-email` | 验证邮箱 | ✅ |
| GET | `/api/v1/auth/oauth/{provider}/start` | 发起第三方登录(302,state + PKCE) | ✅ |
| GET/POST | `/api/v1/auth/oauth/{provider}/callback` | 回调:登录或自动注册并绑定 | ✅ |
| GET | `/api/v1/sessions` | 列出我的活跃会话 | |
| DELETE | `/api/v1/sessions/{id}` | 撤销指定会话 | |
| GET | `/api/v1/me` | 当前用户与所属工作区列表 | |
| PATCH | `/api/v1/users/me` | **修改当前账号资料与展示偏好(R3 新增)**:`{display_name?, avatar_url?, timezone?, settings?: {locale?, theme?}}`——仅接受列出字段(未知字段 `400 invalid_request`);`settings` 为**键级浅合并**(只覆盖传入键,其余保留);校验:`display_name` 1–80 字符;`avatar_url` 仅 `https` scheme(README §6.16);`timezone` 为合法 IANA 名(否则 `422 invalid_timezone`);`settings.locale` 在首发支持清单(`zh-CN`/`en`,扩展经 i18n.md 消息目录注册)内(否则 `422 unsupported_locale`);**`settings.theme ∈ {light, dark, system}` 或显式 `null`**(显式 `null` = 清除账号偏好、恢复继承工作区默认,theme.md §2.1 三值语义;非法值 → **`422 invalid_theme_mode`**,与 theme.md §3.3 / workspace.md 错误码统一);成功 `200` 返回更新后的完整用户对象(`{"data": {...}}`,含合并后 `settings`);变更写 `audit_logs`。**迁移(R3)**:存量用户的 locale/theme 偏好由迁移脚本一次性写入 `users.settings`(无旧列双写——本字段为新增真源,不存在长期双写期);**存量「默认 system」语义迁移**:旧实现若把「未设置」落为字符串 `"system"`,迁移时区分「用户显式选择 system」与「从未选择」——无法区分的存量值保留 `"system"`(跟随 OS),新建账号一律 absent/null 默认 | |
| GET | `/api/v1/auth/token` | **当前 Bearer 自省(评审 H7 新增)**:返回**当前请求所携凭证**的元数据——`{data: {kind: "pat"\|"agent"\|"session", token_id, prefix(掩码展示前缀), name, scopes, workspace_id, member_id, expires_at, last_used_at}}`;PAT/agent token 按 `token_hash` 取 `api_tokens` 行;**会话 access JWT 按 `sid` 声明定位 `sessions` 行**(R2-H1:不再按 access 自身 `jti`),`workspace_id`/`scopes` 对设备会话取会话固化值;**不返回明文任何片段**;支撑 CLI `auth status` 展示 scope/过期/last_used(此前 `GET /me` 与本地凭证结构均不能提供) | |
| DELETE | `/api/v1/auth/token` | **当前 Bearer 自撤销(评审 H7 新增)**:撤销**当前请求所携凭证**——PAT/agent token 按 `token_hash` 置 `revoked_at=now()`(即时 401,§5.5);会话凭证**按 `sid` 定位 `sessions` 行**置 `revoked_at`(refresh 即刻失效,access 按 TTL 过期,§3.7);`200 {"data": {"status": "ok"}}`;支撑 CLI `logout --revoke` 无需另行持有 token id。撤销写 `audit_logs`(`token.revoked` / `auth.logout`) | |

### 3.1.1 设备授权端点(auth.md 增量,评审 H7 闭环;流程契约见 cli.md §3.2)

> 数据模型与状态机见 §2.4.2;以下端点为本增量的**服务端权威定义**,cli.md 仅引用。限流阈值见 §3.6,审计动作见 §2.6。

| 方法 | 路径 | 说明 | 公开 |
|------|------|------|:---:|
| POST | `/api/v1/auth/device/code` | 取码:`{client_id: "mesh-cli", scope: "<space-joined>"}` → `200 {"data": {device_code, user_code, verification_uri, verification_uri_complete, expires_in(默认 900), interval(默认 5)}}`;同事务落 `device_authorizations`(`status='pending'`,仅存 HMAC 哈希);写审计 `auth.device_code_issued`(account-less,`metadata` 落 client_id/request_ip/scope) | ✅ |
| POST | `/api/v1/auth/device/token` | 轮询换令牌(量化爆破防护,§2.4.2):请求 `{grant_type: "urn:ietf:params:oauth:grant-type:device_code", device_code, client_id}`。`pending` → `400 authorization_pending`(具名 code,§6.14 信封);限速违规 → `429 slow_down`(`Retry-After`,客户端间隔 +5s);拒绝 → `400 access_denied`(终止);过期/作废 → `400 expired_token`/`invalid_grant`(重新发起)。**成功 200**——消费为**单事务固定锁序(R4-H3 写死,消除 R3-H5 残留 TOCTOU)**,步骤严格按序:① **`SELECT … FROM device_authorizations WHERE device_code_hash=$h FOR UPDATE` 锁授权行**(校验 `status='approved'` 且未过期,否则按状态回对应错误);② **`SELECT role FROM members WHERE workspace_id=authz.workspace_id AND user_id=authz.approved_by_user_id AND status='active' FOR UPDATE` 锁对应名册行**——0 行(批准后、消费前被移除/停用)→ **作废授权(`status='invalidated'` + 审计 `auth.device_invalidated`)并返回 `400 access_denied`,绝不签发**;③ **`签发 scope = authz.granted_scopes ∩ 该名册行当前角色权限`(只收窄不放宽)**;④ 条件消费更新(`SET status='consumed', consumed_at=now() WHERE id=authz.id AND status='approved'`,行数 1);⑤ 建 `sessions` 行(`type='cli'`,`workspace_id`/`granted_scopes` 取最终签发值,`device_authorization_id` 指回授权记录——UNIQUE 保证单码至多一会话,§2.4);⑥ 审计 `auth.device_consumed`——**一次提交**。**成员移除 / 角色变更必须走同一锁协议**:member.md 的移除(`DELETE /workspaces/{ws}/members/{id}`)与改角色(`PATCH …/members/{id}`)事务内更新 `members` 行即持该行排他锁,与本事务 ② 的 `FOR UPDATE` **在同一行上线性化**——consume 先持锁则移除/降权等待至会话签发完成,移除先提交则 consume 在 ② 读到 0 行或新角色(按锁后结果签发或拒绝,不存在「读到 active 后移除插入提交」间隙):`{data: {access_token(会话 access JWT,含 sid/workspace_id/scope 声明), refresh_token(mesh_rft_…), token_type: "Bearer", expires_in, scope(= 实际签发值), workspace: {id, slug}(批准绑定工作区,CLI 直接采用为默认)}}` | ✅ |
| GET | `/api/v1/auth/device?user_code=` | 确认页数据(Web 登录态):校验 `user_code` 命中 `status='pending'` 且未过期 → 返回 `{data: {client_name, requested_scopes(人类可读全量枚举), workspaces: [{id, slug, name, my_role}](批准者所属工作区列表,供 0/1/多分流)}};命中失败/过期返回通用 `404 not_found`(**不区分不存在/已消费/过期,防码探测**) | |
| POST | `/api/v1/auth/device/approve` | 批准(Web 登录态 + **同源 CSRF 防护**):body `{user_code, workspace_id}`——**`user_code` 必须为确认页手工录入值,批准仅绑定所录入的码**(防 RFC 8628 §5.5 钓鱼:攻击者诱使受害者批准攻击者的码);`workspace_id` 由批准者显式选定(多工作区用户不默认)。**事务内先锁定批准者在该工作区的名册行(R3-H5,防篡改 body 绑定非成员工作区)**:`SELECT role FROM members WHERE workspace_id=$ws AND user_id=$u AND status='active' FOR UPDATE`——**0 行 → `403 forbidden`**(批准者非该工作区活跃成员;仅 FK 到 workspaces 不足以授权,名册行才是授权依据);`granted_scopes = 请求 scope ∩ 该名册行角色权限`(服务端强制取交)。状态迁移为**原子条件更新**:`UPDATE device_authorizations SET status='approved', granted_scopes=<取交值>, approved_by_user_id=$u, workspace_id=$ws, approved_at=now() WHERE user_code_hash=$h AND status='pending' AND expires_at > now()`,**影响行数恰为 1 方可继续**(并发批/拒/过期竞争下恰一方成功,0 行 → 当前状态回显,不覆盖他方迁移);token 端点消费时兜底重校验(见下行);写审计 `auth.device_approved`(含取交前后 scope 与名册行 id);非法 user_code → `404 not_found` | |
| POST | `/api/v1/auth/device/deny` | 拒绝(Web 登录态 + CSRF):body `{user_code}` → **同款原子条件更新**(`SET status='denied', denied_at=now(), approved_by_user_id=$u WHERE user_code_hash=$h AND status='pending' AND expires_at > now()`,行数 1 方为本次拒绝);写审计 `auth.device_denied`;已终态时幂等返回当前状态(0 行不报错、不覆盖) | |

**授权确认页 UX(auth.md UI 增量,§4 衔接;0/1/多工作区分流在此完成,评审 R2-H1)**:
- 入口 `verification_uri`(如 `/device`)要求**手工录入 `user_code`**(`verification_uri_complete` 可携带预填参数,但**提交仍要求页面上存在可见的码输入控件且值经校验与预填一致**,防受害者无意识一键批准);
- 批准前展示:`client_id` 名称、**取交后**的 scope 人类可读全量枚举(逐条说明文案)、醒目安全提示「仅在你本人发起的 CLI 登录时批准」;
- **工作区分流(写死)**:**0 个工作区** → 批准按钮禁用,提示「CLI 会话需绑定一个工作区,请先在 Web 端创建或加入工作区」;**1 个** → 自动绑定并在页面明示;**多个** → 选择器**必选**(无默认项,未选不可提交)。批准绑定的 workspace 即 CLI 会话的默认工作区,**CLI 成功后不再二次选择**(cli.md §4.2);
- 显式「批准 / 拒绝」双按钮,批准为默认焦点**非默认确认**(防回车误批)。

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
            "expires_in": 900, "refresh_token": "mesh_rft_..." } }
```

**注册** `POST /api/v1/auth/register`
```json
{ "email": "li@corp.com", "password": "...", "display_name": "李四" }
// 201:建 users(status=active),发验证邮件;密码强度校验(≥8 位含字母数字,拒常见弱密码)
```

**创建 API token** `POST /api/v1/workspaces/{ws}/api-tokens`
```json
// Request(agent 运行凭证:owner_member_id 指向该 agent 的 member 行,README §6.1)
{ "name": "code-reviewer agent", "scopes": ["issue:read","comment:write","attachment:write"],
  "expires_at": "2027-01-01T00:00:00Z", "owner_member_id": "mem-agent-222" }
// 201 Response(明文仅此一次;前缀随持有者类型:agent 成员 → mesh_agt_,人类成员 → mesh_pat_,R2-H2)
{ "data": { "id": "tok-1", "name": "code-reviewer agent", "prefix": "mesh_agt_Ab3",
            "token": "mesh_agt_Ab3Xy9...完整明文...", "scopes": ["issue:read","comment:write","attachment:write"],
            "expires_at": "2027-01-01T00:00:00Z" } }
```

### 3.5 错误码表

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 字段非法 |
| 400 | `weak_password` | 密码强度不足(`details.reason ∈ too_short/needs_letter_and_digit/too_common`;注册/重置/修改密码共用) |
| 400 | `authorization_pending` | 设备码轮询:尚未批准(继续轮询,§3.1.1) |
| 400 | `access_denied` | 设备码被拒绝(终止轮询) |
| 400 | `expired_token` / `invalid_grant` | 设备码过期 / 已消费 / 已作废(重新发起) |
| 401 | `unauthorized` | 凭证缺失/无效/过期(README §6.14 canonical code) |
| 401 | `unauthorized` | 邮箱未验证(`details.reason='email_not_verified'`;README §6.14 canonical code) |
| 403 | `forbidden` | 角色/scope 不足 |
| 404 | `not_found` | 资源不存在(设备确认页 user_code 命中失败统一此码,防码探测) |
| 409 | `conflict` | 邮箱已注册、唯一 owner 不可移除 |
| 422 | `invalid_credentials` | 邮箱或密码错误(**统一文案,防枚举**) |
| 422 | `invalid_theme_mode` | `settings.theme` 非法(既非 `light\|dark\|system` 也非显式 `null`;theme.md §3.3 唯一权威,本表同步登记) |
| 423 | `account_locked` | 失败次数过多被临时锁定 |
| 429 | `rate_limited` | 触发限流,含 `Retry-After` |
| 429 | `slow_down` | 设备码轮询超速(§3.1.1/§3.6),客户端间隔 +5s;累计违规超限码作废 |

### 3.6 速率限制(阈值示例,可调)

| 端点类 | 限制 | 维度 |
|--------|------|------|
| 登录 / 注册 / 重置 | 5 次/分钟,超出锁定 15 分钟 | **(IP, 邮箱) 二元组**(避免按纯邮箱维度锁定导致攻击者对任意受害者账号刷失败造成锁定 DoS;保留验证码解锁路径) |
| **设备码取码**(`device/code`) | 10 次/分钟(登录类基线) | 来源 IP |
| **设备码轮询**(`device/token`,§3.1.1 爆破防护) | **双重限速**:① 按来源 IP 全局 30 次/分钟;② 按 `device_code` ≤ `1/interval`(interval 取码时下发,默认 5s)——违规回 `429 slow_down`,**单码累计违规/猜错 > 5 次即作废该码**(`status='invalidated'`)+ 审计 | 来源 IP + `device_code` 双维度 |
| **设备确认页**(`device/approve`/`deny`) | 10 次/分钟 | 登录态用户 + IP |
| 通用 API 读 | 300 req/分钟 | token / 用户 |
| 通用 API 写 | 120 req/分钟 | token / 用户 |
| 附件上传/下载 | 60 req/分钟 | token / IP |
| WebSocket 消息 | 60 msg/分钟 | 连接 |

实现:令牌桶/滑动窗口(Redis),响应头 `X-RateLimit-Limit/Remaining/Reset`;超限 429 + `Retry-After`。登录类叠加失败计数锁定与凭据填充防护。

### 3.7 WebSocket 鉴权与实时

- `/ws` 连接建立时用 token 鉴权(握手携带或首条消息认证),服务端校验后按 `workspace_id + principal` 注册频道。**统一实时契约见 README §6.7**:`seq` 一律为**频道内**单调递增(持久化于 `realtime_events`,无"全局 seq");断线重连带 `resume_from`、游标过旧收 `resync_required`;**每次订阅频道时重新做资源级授权**(见各模块 Spec 事件表)。
- **会话/token 撤销实时生效**:撤销落库后同事务写 outbox(README §6.6),经 realtime 网关广播使相关连接失效或下次心跳鉴权失败重连被拒(**不用进程内事件总线**);access JWT 短期,撤销最长延迟 = 其 TTL。**广播事件名写死为 `session.revoked`**(README §6.7 注册表「会话 / 鉴权」域已登记;MES-77 事实核查建议项补注:事件名字面如此前仅存于后端代码,本节为 Spec 侧权威落点),按该用户所属各工作区频道逐一 fan-out。
- 异常登录提醒经 WebSocket 站内 + 邮件双通道。

### 3.8 refresh 轮换竞态:有界幂等轮换 + 胜者唯一下发(R4-M4 建立,R5-H1 写死——仅存哈希模型下可实现的唯一闭合方案)

多 tab 共用同一 HttpOnly cookie、各自在内存持有 access;**同时过期时会并发 `/auth/refresh`**——若「轮换即撤销旧 refresh」无条件生效,其余 tab 在途请求携带的旧 refresh 已失效 → 401 → 正常 tab 被误判登出。

**核心约束(R5-H1 收口)**:`token_hash` / `previous_token_hash` **均只存 SHA-256 哈希,服务端无法从哈希还原胜者生成的当前 refresh 明文**——故宽限路径**不返回、也不需要返回 refresh 明文**(此前「宽限返回当前 refresh + 重新 Set-Cookie」表述已撤销,其在仅存哈希模型下不可实现)。写死**方案 A:胜者唯一下发 + 宽限只发 access**:

- **轮换仲裁(条件更新,行数控裁)**:refresh 请求执行条件轮换——`UPDATE sessions SET token_hash=$new, previous_token_hash=token_hash, rotated_at=now() WHERE id=$sid AND token_hash=$presented AND revoked_at IS NULL`(先由 `$presented` 的哈希定位候选行):
  - **影响行数 = 1 → 本请求为唯一胜者**:响应下发**本次生成的新 refresh 明文** + 新 access(Web 经 `Set-Cookie: mesh_session=<新值>`;CLI/设备会话经响应体);
  - **影响行数 = 0 → 重读会话行判定**:① `$presented` 匹配当前 `token_hash`(并发下他人刚轮换为同一呈现值的极端情形)→ 按当前凭证正常处理;② `$presented` 匹配 `previous_token_hash` 且满足宽限条件 → 宽限路径;③ 均不匹配 → `401 unauthorized`;
- **宽限路径(只发 access,绝不下发 refresh 明文,绝不二次轮换)**:`$presented` 匹配 `previous_token_hash` 且 `now() - rotated_at ≤ MESH_REFRESH_ROTATION_GRACE_SECONDS`(默认 30s)且 `revoked_at IS NULL` → **仅签发新 access JWT;无 `Set-Cookie`、响应体不含任何 refresh**;**宽限路径不写库**(`token_hash`/`previous_token_hash`/`rotated_at` 一律不动——无链式放大)。机理:新 refresh 的**唯一下发通道是胜者响应**,后来者只需拿到 access 即可延续会话,凭证收敛由下述客户端机制保证;
- **Web 收敛(共享 cookie jar)**:浏览器 cookie jar **按 origin 共享、跨 tab 一致**——胜者响应的 `Set-Cookie` 把 jar 更新为新 refresh;**胜者/后来者响应任意乱序(后来者先到达亦然),结果相同**:两请求均 200(胜者 = 新 refresh + access;后来者 = 仅 access),此后**任一 tab 的后续请求自动携带 jar 中的新 refresh** → 收敛到单一当前凭证,**不误登出**。陈旧窗口仅覆盖「后来者 in-flight 请求发出 ↔ 胜者 Set-Cookie 到达 jar」之间已派发的请求,由宽限路径兜住;
- **CLI / 设备客户端 single-flight(写死)**:CLI/设备端无共享 cookie jar,客户端协调写死为:**同一凭证存储(凭证文件)单元内至多一个 in-flight refresh**(进程内锁 + 等待队列,并发调用方共享同一请求结果);**命中宽限路径或收到 401 时,必须先重读凭证文件再宣告失败**(胜者进程可能已将新 refresh 写入文件)——重读成功 → 以新 refresh 重试;重读后仍失败且已超宽限窗 → 退码 2 重新登录。多进程共用凭证文件时经「胜者进程写文件 + 后来进程强制重读」收敛;**响应乱序不破坏收敛**(真源在凭证文件与 sessions 表,不在单进程内存副本);
- **宽限外 / 会话已撤销**:`previous_token_hash` 匹配但超窗或 `revoked_at` 非空 → `401 unauthorized`(重放/窃取按正常失效处理);宽限窗结束后下一次会话写操作顺带清空 `previous_token_hash`(防旧哈希长期留存);
- **安全性**:宽限路径**不下发任何 refresh 明文**(新 refresh 的唯一获取通道是胜者响应)、**不轮换**(无链式放大)、不产生新会话 / 不延长会话生命 / 不放宽 scope;窗口默认 30s 远小于 refresh 寿命,重放面有界;`previous_token_hash` 仅存哈希;
- **验收(真实并发 e2e,R5-H1 断言清单——不得以哈希查询测试替代)**:① 携带同一旧 refresh 的**两个并发 refresh(真并行,非串行模拟)→ 恰一个响应携带新 refresh(胜者)、另一个仅 access**,两者均 200;② **响应乱序**(后来者响应先于胜者到达)→ 最终 cookie jar / 凭证文件持有胜者新 refresh,两 tab/进程后续请求均通过,**无误登出**;③ 宽限窗外重放旧 refresh → 401;④ 会话已撤销 → 胜者路径与宽限路径均 401;⑤ **宽限响应无 refresh 明文**(响应体与 Set-Cookie 抓包断言);⑥ 终态 `sessions` 仅单一当前 `token_hash`,宽限路径未变更 `token_hash`/`previous_token_hash`/`rotated_at`;⑦ CLI 双进程共用凭证文件并发 refresh → 文件收敛为胜者新 refresh,后来进程重读后重试成功。**T36(PG16)以串行等价验证协议判定逻辑**(行数仲裁 / 宽限条件 / 不二次轮换 / 窗口 / 撤销);本真并行 e2e 在后端实现期落实并纳入 CI。

---

## 4. UI/UX 设计

### 4.1 认证页面

- **登录页**:邮箱+密码、「记住我」、「忘记密码?」、「使用第三方账号登录」按钮组;注册入口。失败统一提示「邮箱或密码不正确」,不暴露账号是否存在。
- **第三方登录按钮组**:提供商列表由前端运行时配置(env 逗号分隔 ID;dev 默认 mock,生产默认空、运营方启用),vendor 中立不绑定厂商;点击经后端 `start` 302 往返,回跳前端回调路由交换会话凭证;`redirect_uri` 为每提供商固定的前端回调 URI(与后端精确白名单协同,不携带易变查询串,登录前回跳目标经会话存储携带)。
- **回跳目标守卫(防开放重定向)**:登录页 `?next=` 与 OAuth 往返会话存储中的回跳目标共用同一守卫(`safeNextPath`),对**浏览器 URL 解析器将如何解析目标**做等价校验(CVE-2025-68470 的根本教训:校验「浏览器如何解析」,而非对原始串枚举黑字符):①控制字符/空白预检——WHATWG 解析器会从 special-scheme 输入串的任意位置删除 TAB/LF/CR(`/<TAB>/evil.example` 删除后即协议相对 `//evil.example`),凡含任何控制字符或空白的目标视为异常载荷拒绝;②解析器等价——以站点 origin 为 base 解析,仅放行 origin 与本站一致且路径以 `/` 开头者(返回解析器归一化后的 pathname+search+hash);协议相对 `//`、归一化后成外站的反斜杠变体、绝对 URL、`javascript:` 伪协议一律拒绝,回落首页。两页面共享单一实现,守卫策略不漂移。
- **注册页**:邮箱+显示名+密码(强度条 + 实时校验);提交后跳「已发验证邮件」页(注册成功自动登录态不阻塞,结果页提供「继续」入口回跳)。
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
2. **登录(仅 Web 形态,R4-H1)**:恒定时间比较哈希 → 失败计数(达阈值锁定+可选验证码)→ 成功创建 `sessions` 行(`type='web'`)并颁发短期 access JWT(含 `sub`/`exp`/**逐枚唯一 `jti`**/`sid=session.id`,§2.4)+ 长期 refresh(存哈希入 `sessions`);**refresh 仅经 `Set-Cookie: mesh_session=…; Secure; HttpOnly; SameSite=Strict; Path=/` 下发,响应体绝无 refresh 明文;不接受调用方自报客户端形态,不提供非浏览器密码流**(CLI/API 非浏览器客户端走设备授权流 §3.1.1——token 端点返回 `mesh_rft_…` Bearer——或 PAT `mesh_pat_`)。`remember=true` 延长 refresh。
3. **静默续期**:access 过期 → 用 refresh 调 `/auth/refresh`(Web 经 cookie,设备会话经 Bearer)→ 校验哈希未撤销未过期(**或命中 §3.8 有界幂等轮换宽限**:已被轮换的旧 refresh 在宽限窗内**仅获发新 access,不下发 refresh 明文、不二次轮换**,凭证经胜者响应 + 共享 cookie jar / CLI 重读收敛,多 tab 不误登出)→ **从会话行取固化 `granted_scopes` 与当前角色权限取交**作为新 access 的 scope(R2-H1)→ 颁新 access(继承 `sid`、新 `jti`;轮换 refresh 并撤销旧的,防重放)→ 更新 `last_active_at`。
4. **登出**:撤销当前 refresh(Web 按 cookie 定位会话,CLI 按 Bearer 或自撤销端点);「登出所有」批量撤销;**密码变更**(重置 / 已登录态修改)使该用户**其它** refresh 会话失效——**修改密码时发起会话以当前 access `sid` 识别并保留**(R4-H1,body 不传 refresh),刷新其 `authenticated_at=now()`(§2.4);无有效 `sid` 则全部失效(PAT 单独管理)。
5. **OAuth(授权码 + PKCE)**:`start` 生成 `state`(防 CSRF)+ PKCE → 302 提供商 → 回调校验 `state`、用 `code`+`code_verifier` 换 token → 解析 sub+email:命中已有绑定→登录;email 已存在→绑定;全新→建 `users(password_hash=NULL)`+`oauth_identities`。
6. **API token / agent 凭证**:创建→存哈希、一次性明文→**CLI 从环境变量读取 API token(`mesh_pat_`/`mesh_agt_`,绝不硬编码**;**runtime 机器令牌 `mesh_rt_` 不经 api_tokens,由 mesh-runtime daemon 激活后持有,runtime.md §3.5,R4-M3**)→请求带 Bearer→服务端查哈希、取上下文→scope ∩ 角色做 RBAC→agent 动作以 `actor_member_id`(指向其 member 行)留痕(`actor_kind='member'`,人类/agent 经 JOIN `members.member_type` 判别);agent token 默认不可 `agent:trigger`(防回环);撤销→`revoked_at` 立即生效→后续 401。

### 4.6 每请求授权校验流程

1. 解析 Bearer → 区分 JWT / API token / refresh。
2. 认证:验签(JWT,**必须固定 `alg` 为预期算法(如 HS256 或 RS256),显式拒绝 `alg=none`,防 HS/RS 混淆攻击**)或查哈希(PAT)→ 得 principal(user 或 agent)+ 工作区角色 + scopes。
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
- [ ] **401 canonical code(README §6.14)**:凭证缺失/无效/过期与邮箱未验证统一返回 401 `unauthorized`(未验证以 `details.reason='email_not_verified'` 区分,不另立 code)。
- [ ] 失败计数达阈值返回 423 `account_locked`。
- [ ] access 过期可用 refresh 静默续期;refresh 轮换后旧的立即失效(防重放)。
- [ ] 登出撤销当前 refresh;登出所有批量撤销;密码变更使其它 refresh 会话失效(**已登录态修改密码时发起会话以当前 access `sid` 识别并保留(R4-H1),无有效 sid 则全部失效**);登出/改密/会话列表/指定撤销均属 §1.1 会话生命周期操作登记表。
- [ ] **登录形态固定(R4-H1)**:密码/OAuth 登录响应体**绝无 refresh 明文**(refresh 仅 `Set-Cookie mesh_session` HttpOnly 下发;断言响应 JSON 无 refresh 字段、`document.cookie` 读不到);**登录端点不接受客户端自报形态参数**(`Accept` 协商 / `client=` 一律不影响响应形态);CLI 非浏览器密码流不存在(CLI 仅设备授权/PAT,§3.1.1/§3.2)。
- [ ] **已登录态修改密码(§4.2)**:`POST /api/v1/auth/change-password`(鉴权态)校验旧密码(错误 → `422 invalid_credentials`)与新密码强度(弱 → `400 weak_password`,三 reason 复用注册策略),成功更新 `password_hash` + `password_changed_at` 并使其它会话失效、写审计 `user.password_changed`;前端「设置 → 安全」提供旧+新+确认+强度条的实时校验表单。
- [ ] 会话列表展示设备/UA/IP/最近活跃,可撤销指定会话。
- [ ] 忘记密码恒返回成功(防枚举);重置链接短时效,重置后旧会话失效。
- [ ] OAuth 登录用 state + PKCE;首次自动建号并绑定;解绑保留至少一种登录方式。
- [ ] 可选 2FA(TOTP)启用需验证码确认,并提供备用码。
- [ ] **账号展示偏好真源(R3;MES-76 H1 修订)**:`users` 登记 `timezone`(IANA)与 `settings` JSONB(`locale` BCP-47 / `theme` **`light|dark|system|null/absent`,默认 absent/null = 继承工作区默认**),为 README §6.12/§6.18 与 i18n.md/theme.md 的偏好真源;`PATCH /api/v1/users/me` 可写 `display_name`/`avatar_url`/`timezone`/`settings.locale`/`settings.theme`(键级浅合并),非法 timezone → `422 invalid_timezone`、不支持 locale → `422 unsupported_locale`、**非法 theme → `422 invalid_theme_mode`(三处 owner 契约统一码,与 theme.md §3.3 / workspace.md 一致)**、未知字段 → `400`;**显式 `theme: null` 为合法清除(不报 422),回读 `settings.theme` 为 null,协商落工作区默认**;`GET /api/v1/me` 返回合并后 `settings`;偏好变更写 `audit_logs`;迁移脚本一次性补登记存量偏好,无双写期(集成测试 T32)。
- [ ] **设备码授权全链路(MES-76 H7 新增,§2.4.2/§3.1.1)**:取码 → 确认页手工录入 `user_code` + 选定工作区 + 批准(取交后 scope 展示)→ 轮询 `authorization_pending` → `200` 换取会话凭证(`granted_scopes` 为取交值,响应含绑定 `workspace`);拒绝分支 `access_denied`、过期分支 `expired_token` 各有 e2e;消费原子性:同一 approved 码并发/重复消费**恰好一次成功**(第二次 `invalid_grant`,不建第二条 sessions);批准绑定:录入 A 码的确认页提交 B 码被拒;`workspace_id` 未显式选择的多工作区批准被拒。
- [ ] **批准绑定越权与批准—消费竞争(R3-H5;R4-H3 真并发)**:**篡改 approve body `workspace_id` 为批准者非成员的工作区 → `403`**(名册行锁定校验,负向 e2e;仅工作区存在的 FK 不构成授权);**consume ↔ remove 真并发**:两事务并发(consume 事务与移除该成员事务同时开始)→ **结果按成员行锁线性化**:移除先提交则 consume 作废授权(`access_denied` + `status='invalidated'` + 审计,不建 sessions)、consume 先持锁则会话签发后移除生效——**断言不存在「会话已建且 scope 为移除前角色、但授权未消费」之外的中间态,绝无按陈旧 active 读取签发**;**consume ↔ role change 真并发**:降权事务与消费并发 → 签发 scope 为**锁后角色**的取交值(只收窄;断言签发 scope 与最终名册角色一致,不含已收回权限)。
- [ ] **设备会话持久化与续签边界(R2-H1)**:设备登录产生的 `sessions` 行携带 `workspace_id`/`granted_scopes`/`device_authorization_id`(`type='cli'` 且 workspace 为空的插入被 CHECK 拒绝;`device_authorization_id` UNIQUE 使单码至多一条会话);access JWT 含 `sid=session.id` + 逐枚唯一 `jti`,**自省/自撤销按 `sid` 命中会话行**(断言撤销后自省/自撤销/refresh 命中 revoked 行即拒);**无状态边界断言(R3-H1)**:撤销会话后,**已签发 access 在 TTL 窗口内调常规 `/api/v1` 路由仍 200**(常规中间件不查 sessions 表——以中间件无 DB 调用的测试断言),窗口过后 401,且 `session.revoked` 广播使 WS 连接主动断开(不等 TTL);**refresh 续签 scope = 会话固化值 ∩ 当前角色权限**——批准后将用户角色降权,续签得到的 scope 相应收窄(e2e 断言);approve/deny 并发竞争(同码同时批准 + 拒绝)**恰一方成功、另一方不覆盖**(原子条件更新行数断言);确认页 0/1/多工作区分流各有一条用例(0 → 批准禁用、1 → 自动绑定、多 → 未选不可提交)。

### 5.2 功能性(API token / agent)**[Mesh 特色]**

- [ ] 创建 token 仅在响应中返回一次明文,数据库只存哈希,UI 仅显示 prefix+掩码。
- [ ] **token 持有者统一为 `owner_member_id`**(无 `owner_type/owner_id` 二元组):人类 PAT 指向本人 member 行;agent 运行凭证指向该 agent 的 member 行(README §6.1)。
- [ ] **`owner_member_id` 为复合 FK** `(workspace_id, owner_member_id) → members(workspace_id, id)`,跨工作区指定持有者被数据库拒绝(README §6.2 / §9 T1)。
- [ ] token 可设 scope 与过期时间;撤销立即生效,后续请求 401。
- [ ] token scope 与持有者角色权限**取交集**,不能超越角色权限(最小权限)。
- [ ] 可为 agent 创建运行凭证;agent 用其代表自身读写,所有动作以 `actor_member_id`(指向其 member 行)留痕(`actor_kind='member'`)。
- [ ] agent token 默认不授予 `agent:trigger`(防 agent-to-agent 回环),除非显式授权。
- [ ] token 前缀/类型位可区分 PAT / agent token / refresh——**取值以 §2.5.1 前缀注册表为唯一权威**(`mesh_pat_`/`mesh_agt_`/`mesh_rt_`/`mesh_rft_`),全仓库 Spec 与代码示例无注册表外前缀(文档扫描断言)。**类型语义校验(R2-H2)**:① 为 agent 成员创建 token 签发 `mesh_agt_` 前缀、为人类成员签发 `mesh_pat_`(响应断言);② `mesh_agt_` 令牌伪造/错配到 human 成员行 → 校验拒绝(构造用例);③ **`mesh_rt_` 令牌仅存 `runtimes.runtime_token_hash`,`api_tokens` 无任何 runtime 令牌行**(information_schema + 表查询断言),且 `mesh_rt_` 凭证调常规 `/api/v1` 路由 → 401;④ `mesh_rft_` 凭证调 refresh 以外端点 → 401;⑤ 语义级文档校验脚本(`tests/docs/check_semantic_consistency.py`,CI 硬关卡)断言各 Spec 示例「前缀 ⇄ 存储表 ⇄ 持有者类型」绑定一致与默认值语义,不止词形扫描。
- [ ] **统一 Bearer 鉴权依赖(评审 H7)**:常规 `/api/v1` 路由对会话 JWT / `mesh_pat_` / `mesh_agt_` 三类 Bearer 一致放行,权限恒为 scopes ∩ 角色;**代表性端点集成测试**(至少覆盖 `GET /workspaces/{ws}/issues`、`POST /workspaces/{ws}/issues`、`POST /issues/{id}/comments`、`GET /api/v1/me` 四类读/写/评论/自省端点)分别以 PAT 与 agent token 调用通过,越权 scope 403;`mesh_rt_`/`mesh_rft_` 前缀凭证调常规路由一律 401(daemon 命名空间只认 `mesh_rt_`,refresh 只认 `/auth/refresh`)。
- [ ] **当前 Bearer 自省/自撤销(评审 H7)**:`GET /api/v1/auth/token` 返回 kind/token_id/prefix/scopes/expires_at/last_used_at(无明文字段),支撑 CLI `auth status`;`DELETE /api/v1/auth/token` 撤销 PAT 后下次调用即时 401,撤销会话后 refresh 不可续期。

### 5.3 功能性(授权 / 审计)

- [ ] RBAC 角色取自 `members.role`;权限矩阵声明式维护;端点用 `@require` 声明权限。
- [ ] 非授权访问返回 403;guest 仅可见被显式共享资源。
- [ ] 唯一 owner 不可移除/降级(409,衔接 member.md)。
- [ ] 登录/token 创建撤销/角色变更/敏感写均写 append-only `audit_logs`;审计表禁止 UPDATE/DELETE。
- [ ] **审计行为者去多态**:`audit_logs` 仅存 `actor_member_id`(复合 FK → `members(workspace_id, id)`,非空时校验)+ `actor_kind∈('member','system')`;**无 `actor_type`/`actor_id` 列**,人类/agent 之分由 JOIN `members.member_type` 得出;`system` 动作 `actor_member_id` 为 NULL(README §6.1)。
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
- [ ] 防 CSRF:OAuth 用 state + PKCE;**Web cookie 会话用 `SameSite=Strict` + `Origin`/`Referer` 同源校验(R3-H2/R4-H1:无独立 CSRF token**;注意此为 CSRF 防护而非被盗 cookie 的持有证明,XSS 外渠道窃取由 HttpOnly + step-up 再认证 + 短 TTL + 撤销联动纵深防御)。
- [ ] 防 XSS 窃取:**Web refresh 仅 httpOnly + Secure cookie**(响应体绝无 refresh 明文),access 放内存;**API token(`mesh_pat_`/`mesh_agt_`)由 CLI 从环境变量读取;runtime 机器令牌 `mesh_rt_` 由 mesh-runtime daemon 持有,不入 `api_tokens`,不经环境变量通道(runtime.md §3.5,R4-M3)**。
- [ ] **Web refresh cookie 契约(R3-H2/R4-H1)**:Web 登录响应体**绝无 refresh 明文**(断言 `document.cookie` 读不到 `mesh_session`、响应 JSON 无 refresh 字段、登录端点不接受任何客户端形态自报参数);`/auth/refresh` Web 来源会话凭 cookie 成功续期并轮换 `Set-Cookie`;**跨源 Origin/Referer(或缺失)的 cookie refresh 请求 → 403**(CSRF 防护——注意这不是被盗 cookie 的持有证明,refresh 按完整会话凭证定性,纵深防御为 HttpOnly + 短 TTL + 撤销联动 + step-up,§3);`/api/v1` 其余路由仅携带 cookie 无 Bearer → 401(API 路由不接受 cookie 鉴权);设备来源会话 `Bearer mesh_rft_…` 续期成功;**个性化入口 HTML(含 `__MESH_APPEARANCE__`)响应头为 `Cache-Control: private, no-store`**(已登录入口与邀请入口均断言),静态 shell/资产与个性化 HTML 分离。
- [ ] **多 tab 轮换竞态(R4-M4;R5-H1 真实并发断言清单)**:**双 tab 同时过期并发 `/auth/refresh`(真并行)→ 两个请求均 200**(其一为胜者:新 refresh + Set-Cookie;**其一命中 §3.8 宽限路径:仅获发新 access,响应无 refresh 明文、无 Set-Cookie、不二次轮换**);**响应乱序**下两 tab 最终 cookie/凭证收敛为胜者新值、后续请求均通过,**无误登出**;宽限窗(`MESH_REFRESH_ROTATION_GRACE_SECONDS`,默认 30s)外重放旧 refresh → 401;会话已撤销时胜者路径与宽限路径均 401;**CLI 双进程共用凭证文件并发 refresh → 文件收敛为胜者新 refresh,后来进程重读后重试成功**;完整断言清单见 §3.8。
- [ ] **设备码消费锁序(R4-H3)**:consume 事务按「锁授权行 → `FOR UPDATE` 锁名册行 → 条件消费 + 建 session + 审计」单事务固定锁序执行;名册移除/改角色事务与 consume 在同名册行上线性化(并发用例断言按锁后结果签发或拒绝,无 TOCTOU 间隙)。
- [ ] 全站 HTTPS/HSTS;签名 URL 短时效。
- [ ] 支持 JWT 签名密钥与加密密钥轮换;密钥不出现在代码/仓库。
- [ ] **生产拒用公开默认签名密钥(fail-closed)**:一切签名/验签令牌的应用工厂(API 与 realtime 网关两个独立部署单元)必须在启动时共享同一校验(`validate_auth_settings`)拒绝 `auth_mode=production` + 仓库公开的默认开发密钥,违者拒启动(`ConfigError`);网关不得依赖 API 侧配置,漏配 `MESH_JWT_SECRET` 必须 fail-closed 而非以公开默认密钥验签。
- [ ] **JWT 验签固定 `alg`**:验签时必须固定预期算法(如 HS256 或 RS256),显式拒绝 `alg=none`,防 HS/RS 混淆攻击(服务端不使用 token 头部声明的算法,仅用配置的固定算法验签)。
- [ ] **密码重置/邮箱验证令牌落库**:两类令牌均有独立表(`password_reset_tokens`/`email_verification_tokens`),仅存 SHA-256 哈希,带 TTL(重置 1h / 验证 24h)与单次消费约束(`consumed_at`)。
- [ ] **设备码爆破防护(MES-76 H7 / MES-75 安全 H2 合并落点,逐条量化)**:① `user_code` 熵 ≥20bit + 去歧义字符集(剔除 0/O/1/I/L,分组展示);`device_code` 熵 ≥128bit(密码学安全随机源);② 两码仅存 **HMAC-SHA256(服务端 pepper `MESH_DEVICE_CODE_PEPPER`)** 哈希——**不以裸 SHA-256 落库**(低熵 user_code 裸哈希可离线字典爆破),pepper 缺失时生产启动 fail-closed(同 JWT 密钥校验 §5.5);③ TTL 15min + 单次消费(原子条件更新,并发消费恰一次成功);④ 轮询端点双重限速(IP 全局 + 单码 `1/interval`),`slow_down` 累计违规超限即作废该码;⑤ 单码连续猜错/违规 ≤5 次 → `status='invalidated'` + 审计 `auth.device_invalidated`(e2e 触发并核验作废与审计行);⑥ 确认页手工录入 `user_code` + 批准仅绑定所录码 + 同源 CSRF + scope 全量人类可读枚举(构造跨码 CSRF / 钓鱼批准用例被拒);⑦ 签发 scope = 请求 scope ∩ 批准用户角色权限(服务端强制,token 端点兜底重算;构造越权 scope 请求被收窄),确认页展示取交后 scope。
- [ ] **`role_override` 服务端强校验**:创建 token 时与每次请求鉴权时均校验 `role_override` 不高于持有者当前角色,违反返回 422;不能仅靠文字描述。
- [ ] **登录锁定维度为 (IP, 邮箱) 二元组**:避免纯邮箱维度锁定导致 DoS;保留验证码解锁路径。
- [ ] **审计 append-only DB 级 enforcement**:应用数据库账号对 `audit_logs` 仅授 `INSERT`+`SELECT`,或触发器拒绝 `UPDATE`/`DELETE`。
- [ ] **禁止 query 参数传 token**:WebSocket 连接不得在 URL query 中携带 JWT(防落入访问日志/代理),使用连接建立后首帧认证单一机制(README §6.16,v0.1.0 起实现基线)。
- [ ] 各端点限流生效,超限 429 + `Retry-After`;登录类叠加失败锁定。
- [ ] **敏感操作 step-up 再认证**:修改密码、换绑/解绑 OAuth、创建/撤销 PAT、启用/禁用 2FA 等高危操作要求**近期再认证**(**窗口判据为 `now() - sessions.authenticated_at ≤ MESH_STEP_UP_WINDOW_SECONDS`(默认 900s,R5-M1 唯一真源**——如最近窗口内重新输入密码或 TOTP 验证码),否则返回 `403 reauth_required`;防止会话被劫持后直接执行敏感操作。

### 5.6 实时

- [ ] WebSocket 连接握手鉴权,按 `workspace_id + principal` 注册频道;**每次订阅频道重新做资源级授权**(README §6.7)。
- [ ] 会话/token 撤销后,相关连接在下次心跳被拒;access 撤销延迟 ≤ 其 TTL(15min);撤销经 outbox→realtime 广播,不用进程内事件总线(README §6.6)。
- [ ] 异常登录提醒经站内 + 邮件双通道送达。
- [ ] 频道事件携带**频道内**单调递增 `seq`(无"全局 seq"),断线凭 `resume_from` 重放、游标过旧收 `resync_required`,无丢失无重复(README §6.7)。
