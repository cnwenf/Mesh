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

| 路径 / 入口(真实 method + 完整 `/api/v1` 路径,R6-H2 逐条穷举) | 读写目的 |
|------|------|
| `POST /api/v1/auth/register` | **写**:注册成功**自动登录**(§4.1)——创建 `type='web'` 会话行、签发 refresh(Set-Cookie);凭据校验成功 → `authenticated_at=now()`(R6-H3) |
| `POST /api/v1/auth/login` | **写**:密码校验成功——创建 web 会话、签发 refresh(Set-Cookie);`authenticated_at=now()`(R6-H3) |
| `GET /api/v1/auth/oauth/{provider}/start` | **读 + 写**:发起 OAuth 往返——**仅 `purpose ∈ {login,link}`**(reauth 事务唯一入口为 `POST /auth/reauth`,R8-H1);创建 `oauth_transactions` + 下发浏览器绑定 locator cookie(§2.4.3);`link` 需 web 会话 + freshness 闸门,`login` 公开 |
| `GET /api/v1/auth/oauth/{provider}/callback` | **读 + 写**:**GET-only(R9-H1:SameSite=Lax locator 仅随跨站顶层安全导航(GET)发送,form_post 跨站 POST 不携带,故固定 query response mode、不收 POST)**;不要求 Bearer;按 §2.4.3 浏览器绑定链路:state 定位 → **provider mix-up 校验** → **locator cookie 哈希匹配(防 login CSRF:截获的 callback URL 在另一浏览器打开被拒,不建 session)** → 原子消费并清理 locator → 按 `purpose` 分支:`login` 登录/注册并建 web 会话(新鲜交互 → `authenticated_at=now()`,静默 SSO → NULL);`link`/**`reauth` 以不变量重校验发起会话 `initiating_sid`** 后绑定身份 / 仅更新该会话 `authenticated_at`,**不创建会话/账号**;**`POST` 同路径 → `405 method_not_allowed`** |
| `POST /api/v1/auth/device/token` | **写**:设备码消费**创建 `type='cli'` 会话**(§3.1.1 INSERT sessions);`authenticated_at` **继承批准记录 `approved_authenticated_at`**(批准时从批准者浏览器会话经锁定读取,R6-H3;不得以消费时刻冒充认证),可为 NULL |
| `POST /api/v1/auth/device/approve` | **读**(R7-H1 登记):按请求 Bearer access 的 **`sid` 经会话定位不变量**(仅 `web` 类型、未撤销、未过期、`user_id=sub`)定位批准者会话并读 `authenticated_at`(复制进 `approved_authenticated_at`);写目标为 `device_authorizations`(不写 sessions);0 行 → `401 unauthorized` |
| `POST /api/v1/auth/device/deny` | **读**(R7-H1 登记):同上按 `sid` 经不变量定位批准者会话(仅 `web`);写目标为 `device_authorizations` |
| `POST /api/v1/auth/refresh` | **读 + 写**:校验会话未撤销/未过期 + 轮换仲裁 / 有界宽限(§3.8) |
| `POST /api/v1/auth/logout` | **读 + 写**:定位当前会话(Web 按 cookie / CLI 按 Bearer)并撤销 |
| `POST /api/v1/auth/logout-all` | **读 + 写**:**批量撤销**该用户全部未撤销会话 |
| `POST /api/v1/auth/reset-password` | **读 + 写**:凭重置令牌定位 user 后**批量撤销全部会话,不建立新会话**——响应 `200 {"data":{"status":"ok"}}`,**无 Set-Cookie、无 access 正文**,用户回登录页以新密码重新登录(R7-M3 选定口径:重置后回登录页,非自动登录;无新会话即无 `authenticated_at` 赋值) |
| `POST /api/v1/auth/change-password` | **读 + 写**:按 `sid` 经不变量定位发起 web 会话 → **事务内校验 `old_password`(即主动再认证本身,不经 `authenticated_at` 预闸门,R7-M1)→ 成功后更新该会话 `authenticated_at=now()`**、撤销其它会话 |
| `POST /api/v1/auth/reauth` | **读 + 写**:**step-up 再认证(R6-H3 新增;R8-H1 恢复操作豁免 freshness 预闸门)**——按会话定位不变量定位 web 会话(NULL/超窗 `authenticated_at` 的会话恰为服务对象);密码 `{password}`(**仅未启用 TOTP 的用户**)/ TOTP `{totp_code}`(**启用 TOTP 的用户必须呈递,密码单独 → 422 `totp_required`,MES-78 LOW-2**)校验成功 → 该会话 `authenticated_at=now()`;OAuth-only `{method:"oauth"}` → **建 `purpose='reauth'` transaction(reauth 事务唯一入口)** 返回授权 URL,callback 重校验不变量后仅更新该会话;**仅 web 会话,PAT/agent → `403 reauth_required`** |
| `GET /api/v1/auth/token` | **读**:自省当前凭证的会话元数据 |
| `DELETE /api/v1/auth/token` | **写**:自撤销当前会话 |
| `GET /api/v1/sessions` | **读**:会话列表 |
| `DELETE /api/v1/sessions/{id}` | **写**:指定撤销会话 |
| `/ws` 握手鉴权(§3.7) | **读**:连接建立时校验 token、订阅逐资源授权;`session.revoked` 广播触发主动断开 |
| 个性化 HTML 入口中间件(theme.md §2.3 精确注入) | **读**:`mesh_session` cookie → 会话 → 请求者 `users.settings.theme` / 路由工作区默认,注入 `__MESH_APPEARANCE__`(**只读不写;响应 `Cache-Control: private, no-store`**) |
| step-up 闸门中间件(§5.5,R6-H3;R7-H2 按路由矩阵) | **读**:按 §1.1 凭证矩阵对受保护路由施加 `authenticated_at` 窗口判定(`IS NOT NULL AND now() - authenticated_at ≤ MESH_STEP_UP_WINDOW_SECONDS`,默认 900s),会话先经定位不变量取得——**`POST /api/v1/workspaces/{ws}/api-tokens`(创建 PAT)与 `DELETE /api/v1/workspaces/{ws}/api-tokens/{id}`(撤销 PAT):`web` 或 `cli` 会话 JWT,各自检查本会话 `authenticated_at`;`POST /api/v1/agents/{agent_id}/tokens`(签发 agent 运行凭证):同款 `web` 或 `cli` 会话 JWT + 本会话 `authenticated_at` 窗口(MES-78 MEDIUM-1:与 PAT 创建对称——同属「签发长期凭证」风险类,陈旧超窗会话不得直接签发)**;**`POST /api/v1/auth/2fa/setup`、`POST /api/v1/auth/2fa/disable`、OAuth 换绑/解绑(`GET /api/v1/auth/oauth/{provider}/start?purpose=link`、`DELETE /api/v1/auth/oauth/{provider}`):仅 `web` 会话**;**`change-password` 不在预闸门集合**(其 `old_password` 校验即再认证,R7-M1);**`mesh_pat_`/`mesh_agt_` 令牌(无会话)调用上述路由一律 `403 reauth_required`(`details.reason='interactive_session_required'`)**;cli 会话无新鲜认证时按 §1.1 恢复路径提示(Web reauth 后重走设备批准) |

<!-- sessions-registry:end -->

  **登记表之外的任何路径不得查/写 `sessions` 表**;新增会话生命周期操作**必须先更新本登记表(路径 + method + 读写目的)再实现**——语义校验脚本(`tests/docs/check_semantic_consistency.py` 规则 Z)以 `sessions-registry` 标记块为锚,断言 method/path/purpose 三元组完整性,注入缺项/坏 purpose 坏样例必失败;
- **会话定位统一不变量(R7-H1 写死,适用于一切按 `sid` 定位会话的敏感操作——device/approve·deny、reauth、step-up 闸门、change-password 发起会话保留、sessions 列表/指定撤销、WS 握手等)**:
  ```sql
  SELECT … FROM sessions
   WHERE id = $sid                  -- 来自请求所呈递 Bearer access JWT 的 sid 声明
                                    -- (Web SPA 的 API 调用一律呈递 Bearer access,按 sid 定位;
                                    --  cookie 仅承载 refresh,不作为敏感操作的会话定位依据)
     AND user_id = $sub             -- 凭证主体与会话属主必须一致(防跨账号)
     AND type = ANY($allowed_types) -- 按路由凭证矩阵(下表)限定允许类型
     AND revoked_at IS NULL         -- 未撤销
     AND expires_at > now()         -- 未过期
   FOR UPDATE                       -- 敏感写操作持行锁(读操作可省锁)
  ```
  **0 行一律 `401 unauthorized`**(不区分撤销/过期/类型不符,防枚举)。**关键语义**:常规资源路由的 Bearer 中间件不查表(撤销后 TTL 窗口内 access 仍可通过,上条不变量),但**一切敏感操作永远查表且永远施加本谓词**——**已撤销会话即使在 access TTL 窗口内也不能批准设备码铸造新 CLI refresh、不能 reauth、不能创建 PAT**(两不变量各司其职、互不冲突);
- **敏感操作凭证矩阵(R7-H2 写死,按路由;各端点措辞与 e2e 必须与本表逐条一致,不得「矩阵说 A、端点说 B」)**:

  | 路由 | 允许的凭证 | step-up 判据 |
  |------|-----------|--------------|
  | `POST /api/v1/workspaces/{ws}/api-tokens`(创建 PAT) | **`web` 或 `cli` 会话 JWT** | 各自会话 `authenticated_at` 窗口 |
  | `DELETE /api/v1/workspaces/{ws}/api-tokens/{id}`(撤销 PAT) | **`web` 或 `cli` 会话 JWT** | 各自会话 `authenticated_at` 窗口 |
  | `POST /api/v1/agents/{agent_id}/tokens`(签发 agent 运行凭证,MES-78 MEDIUM-1 纳入) | **`web` 或 `cli` 会话 JWT** | 各自会话 `authenticated_at` 窗口(与 PAT 创建对称:同属「签发长期凭证」风险类) |
  | `POST /api/v1/auth/2fa/setup` / `POST /api/v1/auth/2fa/disable` | **仅 `web` 会话 JWT** | 窗口 |
  | OAuth 换绑/解绑(`start?purpose=link` / `DELETE /api/v1/auth/oauth/{provider}`) | **仅 `web` 会话 JWT** | 窗口(`start` 建 transaction 前过闸门) |
  | `POST /api/v1/auth/change-password` | **仅 `web` 会话 JWT** | **不经 `authenticated_at` 预闸门**:事务内校验 `old_password` 即主动再认证本身,成功后更新 `authenticated_at=now()`(R7-M1) |
  | `POST /api/v1/auth/reauth` | **仅 `web` 会话 JWT** | 恢复操作本身 |
  | `POST /api/v1/auth/device/approve` / `deny`(批准者侧) | **仅 `web` 会话 JWT**(按 Bearer `sid` 定位) | 会话定位不变量 |
  | `POST /api/v1/auth/reset-password` | 公开(重置令牌) | 撤销全部会话,**不建会话**,回登录页(R7-M3) |
  | `mesh_pat_` / `mesh_agt_` 令牌(无会话)对上述受保护路由 | **一律拒绝 `403 reauth_required`**(`details.reason='interactive_session_required'`) | — |

  **旧 `cli` 会话无法 reauth 的恢复路径(写死)**:`reauth` 仅 web 可调,cli 会话 `authenticated_at` 为 NULL/超窗时**不能在 CLI 侧恢复**——CLI 受保护操作收到 `403 reauth_required` 时明确提示「**本会话无近期主动认证证明;请用户在 Web 完成 `POST /auth/reauth` 后重新执行 `mesh auth login`(设备批准),新 CLI 会话将继承批准会话的新鲜认证时刻**」(cli.md §4.3 同步),退码 2;不存在实现侧绕过,亦无永久死结;
- **撤销语义**:会话撤销(登出/自撤销/指定撤销/改密撤销其它会话/**成员移除/停用撤销其该工作区绑定 cli 会话(MES-78 LOW-1)**)→ refresh **立即失效**(登记表路径命中 `revoked_at` 即拒),**已签发 access 最迟于 TTL(≤15min)自然失效**,窗口内已撤销会话的 access 在常规路由**仍可通过**(不变量使然)——但**敏感操作按会话定位不变量即时拒绝**(R7-H1),不得铸造新凭证;验收不得要求会话撤销对常规路由即时生效(PAT 无此窗口:`api_tokens.revoked_at` 逐请求查,撤销即时 401,长令牌对逐请求查表的负载可接受);WebSocket 连接经 `session.revoked` 实时广播主动失效(§3.7),不等 TTL。**成员移除/停用联动(MES-78 LOW-1 立约)**:成员移除(`DELETE /workspaces/{ws}/members/{id}`,软删除 `status='removed'`)或停用(`PATCH` 状态 → `disabled`)事务内**同事务撤销该成员(人类)该工作区绑定的全部 cli 会话**——`UPDATE sessions SET revoked_at=now() WHERE user_id=$member.user_id AND workspace_id=$ws AND type='cli' AND revoked_at IS NULL`,经 outbox 广播 `session.revoked`(不依赖「每请求角色拒绝 + 续签取交收窄」兜底——否则空壳会话可续签到 `expires_at`,且**重新受邀时旧固化 scope ∩ 新角色会静默恢复能力**);**重新受邀必须重走设备批准流建立新会话,旧会话不复用**(旧 refresh 保持 revoked,以其续签 → 401)。全链不得出现「常规请求按 `sid` 逐请求查 session 即时 401」的表述(与不变量互斥)。

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
| `authenticated_at` | TIMESTAMPTZ | **NULL**(R6-H3:取消无条件默认——建 session ≠ 主动认证) | **最近一次主动认证时刻(step-up 再认证状态唯一真源)**,按会话来源**显式赋值**:① 密码登录/注册——凭据校验成功时 `now()`;② OAuth 回调——**仅当本次往返发生新鲜交互登录**(提供商 `auth_time` 满足 `max_age` 约束)时 `now()`,静默 SSO 复用保持 `NULL`;③ 设备 cli 会话——**继承批准记录 `approved_authenticated_at`**(批准事务从批准者浏览器会话经锁定读取,§3.1.1;批准会话无新鲜认证则 `NULL`)——**绝不以 token 消费时刻冒充认证**;④ step-up 再认证(`POST /auth/reauth`、改密保留发起会话)——更新为 `now()`。**step-up 判定 = `authenticated_at IS NOT NULL AND now() - authenticated_at ≤ MESH_STEP_UP_WINDOW_SECONDS`(默认 900s,§5.5)**;NULL 或超窗 → 闸门不通过(经 reauth 恢复);会话撤销后自然失效 |
| `type` | TEXT | NOT NULL DEFAULT 'web',CHECK IN ('web','cli','api') | 会话来源(`cli` = 设备码登录) |
| `workspace_id` | UUID | NULL,FK→workspaces(id) ON DELETE CASCADE | **CLI/设备会话绑定的工作区**(批准页显式选定,§3.1.1);`web` 会话为 NULL(多工作区交互式会话按请求路径解析工作区)。**CHECK:`type='cli'` 时 `workspace_id` 必须非空**——设备会话的后续请求与 refresh 续签一律以此列为工作区真源,不重新选择 |
| `granted_scopes` | TEXT[] | NOT NULL DEFAULT '{}' | **会话固化的签发 scope**(登录/批准时取交结果:请求 scope ∩ 当时角色权限)。**refresh 续签时从此列取固化 scope 并与当前角色权限再次取交**(角色降权后旧 scope 不延续);`web` 会话为空数组(权限按角色实时计算) |
| `device_authorization_id` | UUID | NULL,**UNIQUE**,FK→device_authorizations(id) ON DELETE SET NULL | 产生本会话的设备授权记录(§2.4.2,**单次消费 → 至多一个会话**,UNIQUE 保证);供审计回溯与撤销联动 |
| `user_agent` | TEXT | NULL | 客户端 UA |
| `ip_address` | INET | NULL | 创建时 IP |
| `created_at` | TIMESTAMPTZ | NOT NULL | |
| `last_active_at` | TIMESTAMPTZ | NULL | 最近活跃 |
| `expires_at` | TIMESTAMPTZ | NOT NULL | 过期时间(refresh 生命周期) |
| `revoked_at` | TIMESTAMPTZ | NULL | 撤销时间(登出/全端登出/密码变更/自撤销/**成员移除或停用撤销其工作区绑定 cli 会话**,§1.1 撤销语义) |

约束 / 索引:
- `CHECK (type <> 'cli' OR workspace_id IS NOT NULL)`(设备会话必有绑定工作区);
- `uq_token_hash (token_hash)`;`uq_sessions_device_auth (device_authorization_id)`(NULL 不冲突);
- `idx_sessions_user (user_id) WHERE revoked_at IS NULL`。

> **access JWT 声明(写死)**:`{sub: user_id, sid: session.id, jti: <本枚 access 唯一>, workspace_id?: <设备会话绑定值>, scope?: <固化 scope>, exp, iat}`。**常规路由只验签 + `exp` + claims,不按 `sid` 查表**(§1.1 不变量);`sid` 查表仅限 §1.1 **会话生命周期操作登记表**(自省/自撤销/续期/登出/改密保留发起会话/会话列表与指定撤销);`/auth/refresh` 按 refresh `jti`(= session.id)校验会话**未撤销且未过期**(`revoked_at IS NULL AND expires_at > now()`);`jti` 仅用于单枚 access 的审计/去重,不承担会话关联。撤销 session 后 refresh 立即失效,已签发 access 最迟 TTL 自然失效(窗口内常规路由仍可通过,§1.1/§3.7/§5.5);WS 连接经 `session.revoked` 广播主动断开。

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
| `approved_authenticated_at` | TIMESTAMPTZ | NULL | **批准者浏览器会话的 `authenticated_at` 快照(R6-H3)**:approve 事务内对批准者当前 web 会话行 `FOR UPDATE` 读取后复制;consume 时复制进新 cli 会话的 `authenticated_at`——**设备会话的 step-up 资格只能来自批准者真实认证时刻,不得以消费时刻冒充** |
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

### 2.4.3 表:`oauth_transactions`(一次性 OAuth 事务,R7-H3 建立,R8-H2 浏览器绑定链路)

> login / link / reauth 三目的共用同一 callback,**必须**经本表区分目的、绑定发起浏览器与发起会话、防重放/跨账号串用/**login CSRF**。选 **DB 实现**(与会话/审计同库同事务,可审计;Redis 替代方案放弃)。

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | UUID | PK,`gen_random_uuid()` | |
| `state_hash` | TEXT | NOT NULL,UNIQUE | `state` 的 SHA-256 哈希;`state` 明文仅在发起响应的 302 Location / `authorization_url` 中出现一次,callback 按哈希定位 |
| `browser_locator_hash` | TEXT | **NOT NULL** | **浏览器绑定 locator 的 SHA-256 哈希(R8-H2)**:发起时生成 ≥128bit 随机 locator,经 `mesh_oauth_locus` cookie 下发至发起浏览器,哈希入本行;callback 必须匹配(证明回调到达发起浏览器,login CSRF 防护),见「浏览器绑定链路」 |
| `purpose` | TEXT | NOT NULL,CHECK IN ('login','link','reauth') | 往返目的:登录 / 换绑 / step-up 再认证 |
| `provider` | TEXT | NOT NULL | 提供商标识(与 `oauth_identities.provider` 同枚举;callback 校验 URL `{provider}` 与本字段一致,provider mix-up 防护) |
| `user_id` | UUID | NULL,FK→users(id) ON DELETE CASCADE | `link`/`reauth`:发起用户(**条件 CHECK 必填**);`login`:**条件 CHECK 必空** |
| `initiating_sid` | UUID | NULL,**FK→sessions(id) ON DELETE CASCADE** | 发起会话 id(**`link`/`reauth` 条件 CHECK 必填,`login` 必空**):callback 以 §1.1 会话定位不变量**重校验**此会话(未撤销/未过期/属主 == `user_id`/`type='web'`)后执行分支;会话被删 → 事务级联删除 |
| `code_verifier` | TEXT | NOT NULL | PKCE verifier,**加密存储**(同 `runtime_credentials.encrypted_value` 契约),callback 取出随 `code` 提交提供商;随本行一次性消费 |
| `max_age` | INT | NULL,**CHECK (max_age IS NULL OR max_age >= 0)** | 新鲜性约束秒数;**`link`/`reauth` 条件 CHECK 必填**(默认 0 = 强制交互),`login` 可空;callback 据此校验提供商 `auth_time` |
| `safe_next` | TEXT | NULL | 回调成功后回跳目标(建事务时经 `safeNextPath` 守卫,§4.1) |
| `expires_at` | TIMESTAMPTZ | NOT NULL | TTL(默认 10 分钟) |
| `consumed_at` | TIMESTAMPTZ | NULL | 一次性消费时刻 |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT `now()` | |

索引:`uq_oauth_tx_state_hash (state_hash)`;`idx_oauth_tx_expires (expires_at) WHERE consumed_at IS NULL`(过期清理)。

**条件 CHECK(DB 层强制,malformed transaction 建表即拒,不只靠应用 fail-closed;R8-M1)**:

```sql
CHECK (
     (purpose = 'login' AND user_id IS NULL AND initiating_sid IS NULL)
  OR (purpose IN ('link','reauth') AND user_id IS NOT NULL
                                     AND initiating_sid IS NOT NULL
                                     AND max_age IS NOT NULL)
)
```

**浏览器绑定链路(R8-H2 写死,start → provider → callback 凭证传递全图)**:

- **发起(`GET /auth/oauth/{provider}/start` 或 `POST /auth/reauth` 的 OAuth 分支)**:服务端生成 `state`(≥128bit)+ PKCE verifier + **浏览器绑定 locator(≥128bit 随机)**;locator 经 **`Set-Cookie: mesh_oauth_locus=<locator>; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth/oauth; Max-Age=600`** 下发——**必须 `SameSite=Lax` 而非 Strict**:提供商顶层回跳是跨站顶层安全导航,Strict cookie 不携带,callback 将永远拿不到 locator;**Lax 的覆盖边界写死(R9-H1):仅跨站顶层安全导航(典型 GET)携带;跨站 `form_post`(POST callback)不携带 Lax cookie**——故授权请求**固定 `response_mode=query`(提供商一律顶层 GET 重定向回 callback),明确不支持 `form_post`**;**`Path` 限定 callback 路由**(不泄漏至其它路径);**该 cookie 无任何 API 能力**——仅 callback 消费时做哈希比对,不被任何其它端点读取/接受;`browser_locator_hash` 入 transaction;**多 tab 策略**:单 cookie 后发起覆盖先发起(**最后发起者胜**)——先发起 tab 的 callback 将因 locator 不匹配被拒为过期态(错误页提示重试),可接受且显式文档化;
- **callback(`GET /api/v1/auth/oauth/{provider}/callback`,**GET-only,R9-H1**)不要求 Bearer**(提供商顶层 GET 回跳不携带;transaction 即一次性授权上下文;`mesh_session` 为 `SameSite=Strict` 不随跨站回跳携带,本就不作为 callback 凭证);**`POST` 同路径一律 `405 method_not_allowed`**(不支持 `form_post` response mode:Lax locator 不随跨站 POST 发送,POST callback 链路不可执行),按序执行:
  1. `state` → `state_hash` 定位 transaction;不存在/过期/已消费 → 统一错误重定向(不区分原因,防枚举);
  2. **provider mix-up 防护**:断言 URL 路径 `{provider}` **== `transaction.provider`**,不符即拒(分支路由完全以 transaction.provider 为准,禁止二者分叉);
  3. **浏览器绑定校验**:读 `mesh_oauth_locus` cookie;缺失 → 拒;`SHA-256(cookie) == transaction.browser_locator_hash` 方可继续,**不符即拒**——攻击者在自己浏览器发起 login 后截获有效 callback URL 诱导受害者打开:受害者浏览器无攻击者的 locator cookie → 拒,**不建 session**(login CSRF 防护);
  4. **原子消费**:`UPDATE oauth_transactions SET consumed_at=now() WHERE id=$tx AND consumed_at IS NULL AND expires_at > now()`,**影响行数恰为 1 方可继续**(重放/并发/过期命中 0 行即拒,统一错误重定向);同响应**清理 locator cookie**(`Set-Cookie: mesh_oauth_locus=; Max-Age=0; Path=/api/v1/auth/oauth`);
  5. 用 `code` + 存储的 `code_verifier` 向 **transaction.provider** 换取 token;
  6. **按 purpose 分支(严格隔离)**:
     - `login`:解析 sub+email → 登录/注册/绑定 → 创建 web 会话(新鲜性赋值规则见下);
     - `link`:以 `SELECT … FROM sessions WHERE id=transaction.initiating_sid AND user_id=transaction.user_id AND type='web' AND revoked_at IS NULL AND expires_at > now() FOR UPDATE` **重校验发起会话**——「当前会话」= 经不变量重校验的**发起会话**,**不是从 callback 请求新派生**(callback 无 Bearer,cookie 不随跨站回跳;0 行 → 拒,往返期间会话被撤销/过期/属主不符即失败)→ 绑定 `oauth_identities` 到 `transaction.user_id`,**不建会话/账号**;
     - `reauth`:同款不变量重校验 `initiating_sid` → **仅更新该会话 `authenticated_at=now()`**,不建/绑账号;
  7. 按 `safe_next`(经守卫)重定向。
- **新鲜性 fail-closed(R7-H3)**:`reauth` 与 `link` 要求提供商 `id_token` 携带**可验证签名的 `auth_time`** 且满足 `max_age` 约束;**提供商不返回可验证签名 `auth_time` 或不支持 `max_age` → step-up 判定失败(reauth 拒绝、link 闸门不通过),绝不允许以 callback 到达时间代替主动认证**;`login` 目的不受此约束(新鲜性仅决定 `authenticated_at` 赋值,不阻断登录);
- **过期清理**:reaper/惰性扫描将 `expires_at < now()` 且 `consumed_at IS NULL` 的行删除(短 TTL,无需保留)。

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
| POST | `/api/v1/auth/refresh` | refresh 换新 access(可轮换 refresh)。**传输形态由会话签发来源决定(R4-H1,非调用方自报)**:Web 密码/OAuth 来源会话认 HttpOnly cookie `mesh_session`(SameSite=Strict + Origin/Referer 同源校验,轮换经 `Set-Cookie` 下发,**多 tab 竞态按 §3.8 有界幂等轮换**);设备授权来源会话认 `Authorization: Bearer mesh_rft_…`;同请求只认其一。**新 access 的 scope = 会话 `granted_scopes` 固化值 ∩ 持有者当前角色权限**(R2-H1:角色降权后旧 scope 不延续;`web` 会话 granted_scopes 为空,按角色实时计算);新 access 继承 `sid`、另发逐枚唯一 `jti`;设备会话 access 继承 `workspace_id` 声明;**校验按 refresh `jti`(= session.id)命中 sessions 行且会话未撤销/未过期(`revoked_at IS NULL AND expires_at > now()`——refresh 为不透明随机串无内嵌 exp,`sessions.expires_at` 是 refresh 生命周期到期的唯一真源;已过期会话经任何路径均不得轮换出新凭证)**(属 §1.1 会话生命周期操作登记表) | ✅ |
| POST | `/api/v1/auth/logout` | 登出当前会话(撤销 refresh) | |
| POST | `/api/v1/auth/logout-all` | 撤销该用户全部会话 | |
| POST | `/api/v1/auth/forgot-password` | 发起重置(恒返回成功,防枚举) | ✅ |
| POST | `/api/v1/auth/reset-password` | 凭重置令牌设新密码:**使该用户全部会话失效,不建立新会话(R7-M3 选定口径)**——成功 `200 {"data": {"status": "ok"}}`,**无 Set-Cookie、无 access 正文**,前端回登录页以新密码重新登录(不自动登录,无新会话即无 `authenticated_at` 赋值;重置令牌单次消费,§2.4.1) | ✅ |
| POST | `/api/v1/auth/change-password` | **已登录态修改密码(§4.2,仅 web 会话)**:body `{old_password, new_password}`——先按 `sid` 经**会话定位不变量**(§1.1)定位发起 web 会话(0 行 → `401 unauthorized`),再校验旧密码(argon2id 恒定时间比较;错误 → `422 invalid_credentials`)。**本端点不经 `authenticated_at` 预闸门——事务内 `old_password` 校验通过即主动再认证本身(R7-M1 选定口径,删除与登记表互斥的「预闸门」表述)** → 校验新密码强度(复用注册策略 §5.1;弱 → `400 weak_password`,`details.reason ∈ too_short/needs_letter_and_digit/too_common`)→ 更新 `password_hash` + `password_changed_at=now()` + **发起会话 `authenticated_at=now()`**(step-up 唯一真源,§2.4)→ **使该用户其它 refresh 会话失效**(撤销经 §3.7/§5.6 outbox→realtime 广播);PAT 不满足本端点凭证要求(仅 web 会话,矩阵 §1.1)。限流同登录类(§3.6,(IP, 邮箱) 5 次/分钟);成功 `200 {"data": {"status": "ok"}}` | |
| POST | `/api/v1/auth/reauth` | **step-up 再认证(R6-H3 新增,仅 web 会话;R8-H1:恢复操作本身,豁免 freshness 预闸门)**:按 `sid` 经**会话定位不变量**定位当前 web 会话(**0 行 → `401 unauthorized`——撤销/过期会话不可 reauth;`authenticated_at` 为 NULL/超窗的会话恰是本端点的服务对象,不以 freshness 闸门自锁**)。① 持密码**且未启用 TOTP** 用户 body `{password}` → argon2id 校验成功 → 该会话 `authenticated_at=now()`;② **启用 TOTP 的用户必须呈递 body `{totp_code}`(分支排他,MES-78 LOW-2:2FA 用户仅呈递 `{password}` → `422 invalid_credentials`(`details.reason='totp_required'`),密码单独不能完成 step-up——否则仅持有密码者(钓鱼捕获密码场景)即可刷新 `authenticated_at` 越过闸门)** → 校验成功 → `authenticated_at=now()`;③ OAuth-only 用户 body `{method:"oauth"}` → **创建 `purpose='reauth'` 的 `oauth_transactions`(reauth 事务唯一创建入口,绑定发起 `sid`,§2.4.3)** → `200 {"data": {"authorization_url": …}}`(URL 强制 `max_age=0`/`prompt=login`),**callback 重校验会话不变量后仅更新该会话 `authenticated_at`**;**PAT/agent 凭证调用 → `403 reauth_required`(`details.reason='interactive_session_required'`)**;限流同登录类(§3.6);失败 `422 invalid_credentials`;成功 `200 {"data": {"status": "ok", "authenticated_at": …}}` | |
| POST | `/api/v1/auth/2fa/setup` | 启用 TOTP(下发密钥 + 二维码,**验证码确认后方置 `mfa_enabled_at`**);**step-up 闸门保护(§5.5)**:仅 web 会话 + 近期 `authenticated_at` 窗口内可调用,PAT/agent → `403 reauth_required` | |
| POST | `/api/v1/auth/2fa/disable` | 停用 TOTP(呈递验证码确认);**step-up 闸门保护**,同上 | |
| DELETE | `/api/v1/auth/oauth/{provider}` | 解绑第三方账号(保留至少一种登录方式);**step-up 闸门保护**,同上 | |
| POST | `/api/v1/auth/verify-email` | 验证邮箱 | ✅ |
| GET | `/api/v1/auth/oauth/{provider}/start` | 发起第三方 OAuth 往返(**仅 `purpose ∈ {login, link}`;`reauth` 事务唯一入口为 `POST /auth/reauth`,R8-H1 单一路径**):**创建一次性 `oauth_transactions` 行(§2.4.3)** 绑定 purpose、`state_hash`、**`browser_locator_hash`(下发 `mesh_oauth_locus` HttpOnly/Secure/**SameSite=Lax**/Path=/api/v1/auth/oauth/TTL 10min 的浏览器绑定 cookie,无 API 能力)**、PKCE verifier、发起 `sid`/`user_id`(`purpose=link` 必填,经会话定位不变量 + **step-up freshness 闸门**;`purpose=login` 必空)、`safe_next`(经守卫)→ 302 提供商(`state` 明文仅此处下发;`purpose=link` 强制 `max_age=0`/`prompt=login` 保证新鲜交互) | login 公开;link 需 web 会话 + freshness |
| GET | `/api/v1/auth/oauth/{provider}/callback` | 回调(**GET-only,R9-H1**:授权请求固定 `response_mode=query`,不支持 `form_post`——Lax locator 不随跨站 POST 发送;**`POST` 同路径 → `405 method_not_allowed`**;**不要求 Bearer**,transaction 即一次性授权上下文;按 §2.4.3 浏览器绑定链路按序执行):① `state_hash` 定位 transaction(不存在/过期/已消费 → 统一错误重定向);② **URL `{provider}` == `transaction.provider` 断言(provider mix-up 防护)**;③ **`mesh_oauth_locus` cookie(Lax,随跨站顶层 GET 携带)哈希与 `browser_locator_hash` 匹配(缺失/不符即拒——防 login CSRF:截获的 callback URL 在另一浏览器打开不建 session)**;④ 原子消费(`consumed_at IS NULL AND expires_at>now()` 条件更新,行数 1)+ 清理 locator cookie;⑤ `code` + 存存的 `code_verifier` 向 transaction.provider 换 token;⑥ **按 purpose 分支**:`login` → 登录/注册绑定 + 建 web 会话(新鲜性赋值规则,§2.4.3);`link` → **以不变量重校验 `initiating_sid` 发起会话后**绑定 `oauth_identities`,不建会话/账号;`reauth` → **重校验 `initiating_sid` 后仅更新该会话 `authenticated_at`**,不建/绑账号;⑦ `safe_next` 守卫重定向 | login 公开;link/reauth 由 transaction 绑定 + 会话不变量校验 |
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
| POST | `/api/v1/auth/device/token` | 轮询换令牌(量化爆破防护,§2.4.2):请求 `{grant_type: "urn:ietf:params:oauth:grant-type:device_code", device_code, client_id}`。`pending` → `400 authorization_pending`(具名 code,§6.14 信封);限速违规 → `429 slow_down`(`Retry-After`,客户端间隔 +5s);拒绝 → `400 access_denied`(终止);过期/作废 → `400 expired_token`/`invalid_grant`(重新发起)。**成功 200**——消费为**单事务固定锁序(R4-H3 写死,消除 R3-H5 残留 TOCTOU)**,步骤严格按序:① **`SELECT … FROM device_authorizations WHERE device_code_hash=$h FOR UPDATE` 锁授权行**(校验 `status='approved'` 且未过期,否则按状态回对应错误);② **`SELECT role FROM members WHERE workspace_id=authz.workspace_id AND user_id=authz.approved_by_user_id AND status='active' FOR UPDATE` 锁对应名册行**——0 行(批准后、消费前被移除/停用)→ **作废授权(`status='invalidated'` + 审计 `auth.device_invalidated`)并返回 `400 access_denied`,绝不签发**;③ **`签发 scope = authz.granted_scopes ∩ 该名册行当前角色权限`(只收窄不放宽)**;④ 条件消费更新(`SET status='consumed', consumed_at=now() WHERE id=authz.id AND status='approved'`,行数 1);⑤ 建 `sessions` 行(`type='cli'`,`workspace_id`/`granted_scopes` 取最终签发值,`device_authorization_id` 指回授权记录——UNIQUE 保证单码至多一会话,§2.4;**`authenticated_at` 继承 `authz.approved_authenticated_at`——可为 NULL(批准会话无新鲜认证),绝不以消费时刻冒充(R6-H3)**);⑥ 审计 `auth.device_consumed`——**一次提交**。**成员移除 / 角色变更必须走同一锁协议**:member.md 的移除(`DELETE /workspaces/{ws}/members/{id}`)与改角色(`PATCH …/members/{id}`)事务内更新 `members` 行即持该行排他锁,与本事务 ② 的 `FOR UPDATE` **在同一行上线性化**——consume 先持锁则移除/降权等待至会话签发完成,移除先提交则 consume 在 ② 读到 0 行或新角色(按锁后结果签发或拒绝,不存在「读到 active 后移除插入提交」间隙):`{data: {access_token(会话 access JWT,含 sid/workspace_id/scope 声明), refresh_token(mesh_rft_…), token_type: "Bearer", expires_in, scope(= 实际签发值), workspace: {id, slug}(批准绑定工作区,CLI 直接采用为默认)}}` | ✅ |
| GET | `/api/v1/auth/device?user_code=` | 确认页数据(Web 登录态):校验 `user_code` 命中 `status='pending'` 且未过期 → 返回 `{data: {client_name, requested_scopes(人类可读全量枚举), workspaces: [{id, slug, name, my_role}](批准者所属工作区列表,供 0/1/多分流)}};命中失败/过期返回通用 `404 not_found`(**不区分不存在/已消费/过期,防码探测**)。**限流并入「设备确认页」类(§3.6:登录态用户 + IP,10 次/分钟,MES-78 LOW-3——user_code 命中判定构成在线探测 oracle,不止落通用 API 读限流)** | |
| POST | `/api/v1/auth/device/approve` | 批准(Web 登录态 + **同源 CSRF 防护**):body `{user_code, workspace_id}`——**`user_code` 必须为确认页手工录入值,批准仅绑定所录入的码**(防 RFC 8628 §5.5 钓鱼:攻击者诱使受害者批准攻击者的码);`workspace_id` 由批准者显式选定(多工作区用户不默认)。**事务内先锁定批准者在该工作区的名册行(R3-H5,防篡改 body 绑定非成员工作区)**:`SELECT role FROM members WHERE workspace_id=$ws AND user_id=$u AND status='active' FOR UPDATE`——**0 行 → `403 forbidden`**(批准者非该工作区活跃成员;仅 FK 到 workspaces 不足以授权,名册行才是授权依据);`granted_scopes = 请求 scope ∩ 该名册行角色权限`(服务端强制取交);**同事务按请求 Bearer access 的 `sid` 经会话定位不变量(§1.1)识别批准者会话**——`SELECT authenticated_at FROM sessions WHERE id=$sid AND user_id=$sub AND type='web' AND revoked_at IS NULL AND expires_at > now() FOR UPDATE`,**0 行 → `401 unauthorized`**(R7-H1:**已撤销会话即使在 access TTL 窗口内也不能批准设备码铸造新 CLI refresh**;批准按 Bearer `sid` 定位,cookie 仅承载 refresh),识别后复制 `authenticated_at` 进 `approved_authenticated_at`(R6-H3:设备会话的 step-up 资格只能继承批准者真实认证时刻;批准会话无新鲜认证则复制 NULL)。状态迁移为**原子条件更新**:`UPDATE device_authorizations SET status='approved', granted_scopes=<取交值>, approved_by_user_id=$u, workspace_id=$ws, approved_authenticated_at=<锁定读取值>, approved_at=now() WHERE user_code_hash=$h AND status='pending' AND expires_at > now()`,**影响行数恰为 1 方可继续**(并发批/拒/过期竞争下恰一方成功,0 行 → 当前状态回显,不覆盖他方迁移);token 端点消费时兜底重校验(见下行);写审计 `auth.device_approved`(含取交前后 scope 与名册行 id);非法 user_code → `404 not_found` | |
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
| POST | `/api/v1/agents/{agent_id}/tokens` | 为 agent 创建运行凭证(**step-up 闸门保护(§5.5,MES-78 MEDIUM-1):仅 `web` 或 `cli` 会话 JWT 且本会话 `authenticated_at` 在近期窗口内可调用,与 PAT 创建对称;`mesh_pat_`/`mesh_agt_` 令牌 → `403 reauth_required`**) | `agent:manage` |

### 3.3 成员/角色/审计端点(衔接 member.md / workspace.md)

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| PATCH | `/api/v1/workspaces/{ws}/members/{id}` | 改角色(不可降唯一 owner) | admin |
| DELETE | `/api/v1/workspaces/{ws}/members/{id}` | 移除成员 | admin |
| GET | `/api/v1/workspaces/{ws}/audit-logs` | 查审计日志(过滤 + 游标分页) | admin |

### 3.4 请求/响应 JSON 示例

**登录(Web 密码形态,cookie-only)** `POST /api/v1/auth/login`
```json
// Request
{ "email": "li@corp.com", "password": "...", "remember": true }
// 200 Response —— refresh 仅经响应头 Set-Cookie 下发(HttpOnly,JS 不可读),
// 响应体绝无 refresh 明文(R4-H1/R6-H1;CLI 非浏览器密码流不存在,§3)
// Set-Cookie: mesh_session=mesh_rft_…; Secure; HttpOnly; SameSite=Strict; Path=/
{ "data": { "access_token": "eyJhbGci...", "token_type": "Bearer", "expires_in": 900 } }
```

**注册(仅 Web 形态,注册成功自动登录)** `POST /api/v1/auth/register`
```json
// Request
{ "email": "li@corp.com", "password": "...", "display_name": "李四" }
// 201 Response —— 建 users(status=active)+ 发验证邮件;密码强度校验(≥8 位含字母数字,拒常见弱密码)
// 注册成功自动登录(§4.1):创建 web 会话(authenticated_at=now())并经响应头下发 refresh,
// 响应体绝无 refresh 明文(与登录示例同口径,R6-H1/R7-L1)
// Set-Cookie: mesh_session=mesh_rft_…; Secure; HttpOnly; SameSite=Strict; Path=/
{ "data": { "access_token": "eyJhbGci...", "token_type": "Bearer", "expires_in": 900 } }
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
| 422 | `invalid_credentials` | 邮箱或密码错误(**统一文案,防枚举**);reauth 时启用 TOTP 的用户仅呈递密码(`details.reason='totp_required'`,§3.1 LOW-2 分支排他) |
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
| **设备确认页**(`GET /api/v1/auth/device?user_code=` 确认页数据 / `device/approve` / `deny`;MES-78 LOW-3:user_code 命中 oracle 与批/拒同属登录态录入面,并入本类,不止落通用 API 读 300/min) | 10 次/分钟 | 登录态用户 + IP |
| 通用 API 读 | 300 req/分钟 | token / 用户 |
| 通用 API 写 | 120 req/分钟 | token / 用户 |
| 附件上传/下载 | 60 req/分钟 | token / IP |
| WebSocket 消息 | 60 msg/分钟 | 连接 |
| 入站集成回调(integrations.md §3.2 非 Bearer 入站端点与 Stream 摄取入口) | 120 req/分钟 | **(集成, IP) 二元组** |

实现:令牌桶/滑动窗口(Redis),响应头 `X-RateLimit-Limit/Remaining/Reset`;超限 429 + `Retry-After`。登录类叠加失败计数锁定与凭据填充防护。**入站集成回调行为签名校验**前**的粗粒度防刷**:超限对平台侧**静默 200**(非 2xx 会触发外部平台重推放大),仅审计 + 告警;签名**后**的语义级护栏(每身份/每会话频率、会话排队深度、文本长度上限)见 integrations.md §2.10「入站频率护栏」,两层分层互补(MES-82)。

### 3.7 WebSocket 鉴权与实时

- `/ws` 连接建立时用 token 鉴权(握手携带或首条消息认证),服务端校验后按 `workspace_id + principal` 注册频道。**统一实时契约见 README §6.7**:`seq` 一律为**频道内**单调递增(持久化于 `realtime_events`,无"全局 seq");断线重连带 `resume_from`、游标过旧收 `resync_required`;**每次订阅频道时重新做资源级授权**(见各模块 Spec 事件表)。
- **会话/token 撤销实时生效**:撤销落库后同事务写 outbox(README §6.6),经 realtime 网关广播使相关连接失效或下次心跳鉴权失败重连被拒(**不用进程内事件总线**);access JWT 短期,撤销最长延迟 = 其 TTL。**广播事件名写死为 `session.revoked`**(README §6.7 注册表「会话 / 鉴权」域已登记;MES-77 事实核查建议项补注:事件名字面如此前仅存于后端代码,本节为 Spec 侧权威落点),按该用户所属各工作区频道逐一 fan-out。**触发集(写死)**:登出 / 全端登出 / 改密撤销其它会话 / 自撤销(`DELETE /auth/token`) / 指定撤销(`DELETE /sessions/{id}`) / **成员移除/停用撤销其该工作区绑定 cli 会话(MES-78 LOW-1:member.md 移除/停用事务内同事务条件批量撤销 `type='cli' AND workspace_id=$ws AND revoked_at IS NULL`,复用本广播链路,member.md §3 行锁协议条款同侧登记)**。
- 异常登录提醒经 WebSocket 站内 + 邮件双通道。

### 3.8 refresh 轮换竞态:有界幂等轮换 + 胜者唯一下发(R4-M4 建立,R5-H1 写死——仅存哈希模型下可实现的唯一闭合方案)

多 tab 共用同一 HttpOnly cookie、各自在内存持有 access;**同时过期时会并发 `/auth/refresh`**——若「轮换即撤销旧 refresh」无条件生效,其余 tab 在途请求携带的旧 refresh 已失效 → 401 → 正常 tab 被误判登出。

**核心约束(R5-H1 收口)**:`token_hash` / `previous_token_hash` **均只存 SHA-256 哈希,服务端无法从哈希还原胜者生成的当前 refresh 明文**——故宽限路径**不返回、也不需要返回 refresh 明文**(此前「宽限返回当前 refresh + 重新 Set-Cookie」表述已撤销,其在仅存哈希模型下不可实现)。写死**方案 A:胜者唯一下发 + 宽限只发 access**:

- **轮换仲裁(条件更新,行数控裁)**:refresh 请求执行条件轮换——`UPDATE sessions SET token_hash=$new, previous_token_hash=token_hash, rotated_at=now() WHERE id=$sid AND token_hash=$presented AND revoked_at IS NULL AND expires_at > now()`(先由 `$presented` 的哈希定位候选行;**`expires_at > now()` 与 `revoked_at IS NULL` 同为仲裁硬谓词——refresh 无内嵌 exp,`sessions.expires_at` 是到期唯一真源,已过期会话不得经轮换复活**):
  - **影响行数 = 1 → 本请求为唯一胜者**:响应下发**本次生成的新 refresh 明文** + 新 access(Web 经 `Set-Cookie: mesh_session=<新值>`;CLI/设备会话经响应体);
  - **影响行数 = 0 → 重读会话行判定**:① `$presented` 匹配当前 `token_hash` → **必须重走未撤销/未过期校验(`revoked_at IS NULL AND expires_at > now()`),不满足 → `401 unauthorized`**(UPDATE 0 行而 presented 恰为当前哈希的现实成因恰恰是会话已撤销/已过期;仅并发下他人刚轮换为同一呈现值的极端情形在校验通过后按当前凭证正常处理);② `$presented` 匹配 `previous_token_hash` 且满足宽限条件 → 宽限路径;③ 均不匹配 → `401 unauthorized`;
- **宽限路径(只发 access,绝不下发 refresh 明文,绝不二次轮换)**:`$presented` 匹配 `previous_token_hash` 且 `now() - rotated_at ≤ MESH_REFRESH_ROTATION_GRACE_SECONDS`(默认 30s)且 `revoked_at IS NULL AND expires_at > now()`(**过期会话不进入宽限——宽限只延续活会话的在途请求,不延续会话生命**) → **仅签发新 access JWT;无 `Set-Cookie`、响应体不含任何 refresh**;**宽限路径不写库**(`token_hash`/`previous_token_hash`/`rotated_at` 一律不动——无链式放大)。机理:新 refresh 的**唯一下发通道是胜者响应**,后来者只需拿到 access 即可延续会话,凭证收敛由下述客户端机制保证;
- **Web 收敛(共享 cookie jar)**:浏览器 cookie jar **按 origin 共享、跨 tab 一致**——胜者响应的 `Set-Cookie` 把 jar 更新为新 refresh;**胜者/后来者响应任意乱序(后来者先到达亦然),结果相同**:两请求均 200(胜者 = 新 refresh + access;后来者 = 仅 access),此后**任一 tab 的后续请求自动携带 jar 中的新 refresh** → 收敛到单一当前凭证,**不误登出**。陈旧窗口仅覆盖「后来者 in-flight 请求发出 ↔ 胜者 Set-Cookie 到达 jar」之间已派发的请求,由宽限路径兜住;
- **CLI / 设备客户端 single-flight(写死)**:CLI/设备端无共享 cookie jar,客户端协调写死为:**同一凭证存储(凭证文件)单元内至多一个 in-flight refresh**(进程内锁 + 等待队列,并发调用方共享同一请求结果);**命中宽限路径或收到 401 时,必须先重读凭证文件再宣告失败**(胜者进程可能已将新 refresh 写入文件)——重读成功 → 以新 refresh 重试;重读后仍失败且已超宽限窗 → 退码 2 重新登录。多进程共用凭证文件时经「胜者进程写文件 + 后来进程强制重读」收敛;**响应乱序不破坏收敛**(真源在凭证文件与 sessions 表,不在单进程内存副本);
- **宽限外 / 会话已撤销/已过期**:`previous_token_hash` 匹配但超窗、或 `revoked_at` 非空、**或 `expires_at ≤ now()`** → `401 unauthorized`(重放/窃取/**过期会话复活企图**一律按正常失效处理;**已过期会话经胜者路径与宽限路径均不得产生新 `token_hash` 或新 access**);宽限窗结束后下一次会话写操作顺带清空 `previous_token_hash`(防旧哈希长期留存);
- **安全性**:宽限路径**不下发任何 refresh 明文**(新 refresh 的唯一获取通道是胜者响应)、**不轮换**(无链式放大)、不产生新会话 / 不延长会话生命 / 不放宽 scope;窗口默认 30s 远小于 refresh 寿命,重放面有界;`previous_token_hash` 仅存哈希;
- **验收(真实并发 e2e,R5-H1 断言清单——不得以哈希查询测试替代)**:① 携带同一旧 refresh 的**两个并发 refresh(真并行,非串行模拟)→ 恰一个响应携带新 refresh(胜者)、另一个仅 access**,两者均 200;② **响应乱序**(后来者响应先于胜者到达)→ 最终 cookie jar / 凭证文件持有胜者新 refresh,两 tab/进程后续请求均通过,**无误登出**;③ 宽限窗外重放旧 refresh → 401;④ 会话已撤销**或已过期(`expires_at < now()`)** → 胜者路径与宽限路径均 401,**不产生新 `token_hash`**(过期会话不得经轮换复活);⑤ **宽限响应无 refresh 明文**(响应体与 Set-Cookie 抓包断言);⑥ 终态 `sessions` 仅单一当前 `token_hash`,宽限路径未变更 `token_hash`/`previous_token_hash`/`rotated_at`;⑦ CLI 双进程共用凭证文件并发 refresh → 文件收敛为胜者新 refresh,后来进程重读后重试成功。**T36(PG16)以串行等价验证协议判定逻辑**(行数仲裁 / 宽限条件 / 不二次轮换 / 窗口 / 撤销 / **过期**);本真并行 e2e 在后端实现期落实并纳入 CI。

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

1. **注册**:校验强度与唯一性 → argon2id 哈希 → 建 `users` → 发验证邮件;**注册成功自动登录(§4.1)——创建 web 会话并 `authenticated_at=now()`(凭据刚校验,R6-H3)**;未验证可登录但受限。
2. **登录(仅 Web 形态,R4-H1)**:恒定时间比较哈希 → 失败计数(达阈值锁定+可选验证码)→ 成功创建 `sessions` 行(`type='web'`,**`authenticated_at=now()`——密码凭据校验成功即主动认证,R6-H3**)并颁发短期 access JWT(含 `sub`/`exp`/**逐枚唯一 `jti`**/`sid=session.id`,§2.4)+ 长期 refresh(存哈希入 `sessions`);**refresh 仅经 `Set-Cookie: mesh_session=…; Secure; HttpOnly; SameSite=Strict; Path=/` 下发,响应体绝无 refresh 明文;不接受调用方自报客户端形态,不提供非浏览器密码流**(CLI/API 非浏览器客户端走设备授权流 §3.1.1——token 端点返回 `mesh_rft_…` Bearer——或 PAT `mesh_pat_`)。`remember=true` 延长 refresh。
3. **静默续期**:access 过期 → 用 refresh 调 `/auth/refresh`(Web 经 cookie,设备会话经 Bearer)→ 校验哈希未撤销未过期(**或命中 §3.8 有界幂等轮换宽限**:已被轮换的旧 refresh 在宽限窗内**仅获发新 access,不下发 refresh 明文、不二次轮换**,凭证经胜者响应 + 共享 cookie jar / CLI 重读收敛,多 tab 不误登出)→ **从会话行取固化 `granted_scopes` 与当前角色权限取交**作为新 access 的 scope(R2-H1)→ 颁新 access(继承 `sid`、新 `jti`;轮换 refresh 并撤销旧的,防重放)→ 更新 `last_active_at`。
4. **登出**:撤销当前 refresh(Web 按 cookie 定位会话,CLI 按 Bearer 或自撤销端点);「登出所有」批量撤销;**密码变更**(重置 / 已登录态修改)使该用户**其它** refresh 会话失效——**修改密码时发起会话以当前 access `sid` 识别并保留**(R4-H1,body 不传 refresh),刷新其 `authenticated_at=now()`(§2.4);无有效 `sid` 则全部失效(PAT 单独管理)。
5. **OAuth(授权码 + PKCE,一次性事务 + 浏览器绑定链路,R7-H3/R8)**:**发起入口按 purpose 单一化(R8-H1)**——`login`/`link` 经 `GET /auth/oauth/{provider}/start`(`link` 需 web 会话 + freshness 闸门);**`reauth` 事务仅经 `POST /auth/reauth` 创建(只要求会话定位不变量,豁免 freshness 预闸门——恢复操作不自锁)**。发起时生成 `state`(防 CSRF)+ PKCE + **浏览器绑定 locator(HttpOnly/Secure/SameSite=Lax cookie,哈希入事务,§2.4.3)**,创建 `oauth_transactions` 行 → 302/授权 URL 至提供商(`max_age=0`/`prompt=login` 保证新鲜交互)→ 回调**不要求 Bearer**,按序:state 定位 → **URL provider == 事务 provider 断言** → **locator cookie 匹配(防 login CSRF)** → 原子消费 + 清理 locator → `code` + 存储的 `code_verifier` 换 token → **按 purpose 分支**:`login` → 解析 sub+email(命中已有绑定→登录;email 已存在→绑定;全新→建 `users(password_hash=NULL)`+`oauth_identities`)并建 web 会话;`link`/`reauth` → **以会话定位不变量重校验发起会话 `initiating_sid`(未撤销/未过期/属主一致/type='web')**,link 绑定身份(不建会话/账号)、reauth 仅更新该会话 `authenticated_at`(不建/绑账号)。**`authenticated_at` 赋值(R6-H3/R7-H3):仅当提供商 `id_token` 携带可验证签名 `auth_time` 且满足 `max_age`(本次往返为新鲜交互登录)→ `now()`;静默 SSO 复用 → `NULL`;提供商无可验证 `auth_time`/不支持 `max_age` → step-up fail-closed(reauth 拒绝,绝不以 callback 到达时间代替主动认证;login 仍可登录但 `authenticated_at=NULL`)**。
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
- [ ] access 过期可用 refresh 静默续期;refresh 轮换后旧的立即失效(防重放);**会话已过期(`sessions.expires_at < now()`)→ refresh 一律 401,胜者路径与宽限路径均不产生新凭证(过期会话不可复活,§3.8)**。
- [ ] 登出撤销当前 refresh;登出所有批量撤销;密码变更使其它 refresh 会话失效(**已登录态修改密码时发起会话以当前 access `sid` 识别并保留(R4-H1),无有效 sid 则全部失效**);登出/改密/会话列表/指定撤销均属 §1.1 会话生命周期操作登记表。
- [ ] **登录形态固定(R4-H1)**:密码/OAuth 登录响应体**绝无 refresh 明文**(refresh 仅 `Set-Cookie mesh_session` HttpOnly 下发;断言响应 JSON 无 refresh 字段、`document.cookie` 读不到);**登录端点不接受客户端自报形态参数**(`Accept` 协商 / `client=` 一律不影响响应形态);CLI 非浏览器密码流不存在(CLI 仅设备授权/PAT,§3.1.1/§3.2)。
- [ ] **已登录态修改密码(§4.2)**:`POST /api/v1/auth/change-password`(鉴权态)校验旧密码(错误 → `422 invalid_credentials`)与新密码强度(弱 → `400 weak_password`,三 reason 复用注册策略),成功更新 `password_hash` + `password_changed_at` 并使其它会话失效、写审计 `user.password_changed`;前端「设置 → 安全」提供旧+新+确认+强度条的实时校验表单。
- [ ] 会话列表展示设备/UA/IP/最近活跃,可撤销指定会话。
- [ ] 忘记密码恒返回成功(防枚举);重置链接短时效,重置后旧会话失效。
- [ ] OAuth 登录用 state + PKCE;首次自动建号并绑定;解绑保留至少一种登录方式。
- [ ] 可选 2FA(TOTP)启用需验证码确认,并提供备用码;**启用 TOTP 的用户 step-up reauth 必须呈递 TOTP,仅呈递密码 → `422 invalid_credentials`(`details.reason='totp_required'`)且 `authenticated_at` 不刷新(MES-78 LOW-2 分支排他)**。
- [ ] **账号展示偏好真源(R3;MES-76 H1 修订)**:`users` 登记 `timezone`(IANA)与 `settings` JSONB(`locale` BCP-47 / `theme` **`light|dark|system|null/absent`,默认 absent/null = 继承工作区默认**),为 README §6.12/§6.18 与 i18n.md/theme.md 的偏好真源;`PATCH /api/v1/users/me` 可写 `display_name`/`avatar_url`/`timezone`/`settings.locale`/`settings.theme`(键级浅合并),非法 timezone → `422 invalid_timezone`、不支持 locale → `422 unsupported_locale`、**非法 theme → `422 invalid_theme_mode`(三处 owner 契约统一码,与 theme.md §3.3 / workspace.md 一致)**、未知字段 → `400`;**显式 `theme: null` 为合法清除(不报 422),回读 `settings.theme` 为 null,协商落工作区默认**;`GET /api/v1/me` 返回合并后 `settings`;偏好变更写 `audit_logs`;迁移脚本一次性补登记存量偏好,无双写期(集成测试 T32)。
- [ ] **设备码授权全链路(MES-76 H7 新增,§2.4.2/§3.1.1)**:取码 → 确认页手工录入 `user_code` + 选定工作区 + 批准(取交后 scope 展示)→ 轮询 `authorization_pending` → `200` 换取会话凭证(`granted_scopes` 为取交值,响应含绑定 `workspace`);拒绝分支 `access_denied`、过期分支 `expired_token` 各有 e2e;消费原子性:同一 approved 码并发/重复消费**恰好一次成功**(第二次 `invalid_grant`,不建第二条 sessions);批准绑定:录入 A 码的确认页提交 B 码被拒;`workspace_id` 未显式选择的多工作区批准被拒。
- [ ] **批准绑定越权与批准—消费竞争(R3-H5;R4-H3 真并发)**:**篡改 approve body `workspace_id` 为批准者非成员的工作区 → `403`**(名册行锁定校验,负向 e2e;仅工作区存在的 FK 不构成授权);**consume ↔ remove 真并发**:两事务并发(consume 事务与移除该成员事务同时开始)→ **结果按成员行锁线性化**:移除先提交则 consume 作废授权(`access_denied` + `status='invalidated'` + 审计,不建 sessions)、consume 先持锁则会话签发后移除生效——**断言两事务并发终态必为二者之一(MES-78 LOW-4 正向化):(a) 会话已建且其 scope = 锁后角色取交值、授权 consumed;(b) 授权 invalidated + access_denied、无 sessions 行;绝不出现按陈旧 active 读取的签发**;**consume ↔ role change 真并发**:降权事务与消费并发 → 签发 scope 为**锁后角色**的取交值(只收窄;断言签发 scope 与最终名册角色一致,不含已收回权限)。
- [ ] **设备会话持久化与续签边界(R2-H1)**:设备登录产生的 `sessions` 行携带 `workspace_id`/`granted_scopes`/`device_authorization_id`(`type='cli'` 且 workspace 为空的插入被 CHECK 拒绝;`device_authorization_id` UNIQUE 使单码至多一条会话);access JWT 含 `sid=session.id` + 逐枚唯一 `jti`,**自省/自撤销按 `sid` 命中会话行**(断言撤销后自省/自撤销/refresh 命中 revoked 行即拒);**无状态边界断言(R3-H1)**:撤销会话后,**已签发 access 在 TTL 窗口内调常规 `/api/v1` 路由仍 200**(常规中间件不查 sessions 表——以中间件无 DB 调用的测试断言),窗口过后 401,且 `session.revoked` 广播使 WS 连接主动断开(不等 TTL);**refresh 续签 scope = 会话固化值 ∩ 当前角色权限**——批准后将用户角色降权,续签得到的 scope 相应收窄(e2e 断言);approve/deny 并发竞争(同码同时批准 + 拒绝)**恰一方成功、另一方不覆盖**(原子条件更新行数断言);确认页 0/1/多工作区分流各有一条用例(0 → 批准禁用、1 → 自动绑定、多 → 未选不可提交)。
- [ ] **成员移除/停用联动撤销 cli 会话(MES-78 LOW-1)**:成员被移除或停用 → 其该工作区绑定 cli 会话(`type='cli' AND workspace_id=$ws AND revoked_at IS NULL`)在**同一事务内**被批量撤销并经 `session.revoked` 广播主动断开 WS;撤销后旧 refresh 续签 → 401;**重新受邀后以旧 refresh 续签仍 401(旧会话不复用,须重走设备批准),不存在「旧固化 scope ∩ 新角色静默恢复能力」路径**。

### 5.2 功能性(API token / agent)**[Mesh 特色]**

- [ ] 创建 token 仅在响应中返回一次明文,数据库只存哈希,UI 仅显示 prefix+掩码。
- [ ] **token 持有者统一为 `owner_member_id`**(无 `owner_type/owner_id` 二元组):人类 PAT 指向本人 member 行;agent 运行凭证指向该 agent 的 member 行(README §6.1)。
- [ ] **`owner_member_id` 为复合 FK** `(workspace_id, owner_member_id) → members(workspace_id, id)`,跨工作区指定持有者被数据库拒绝(README §6.2 / §9 T1)。
- [ ] token 可设 scope 与过期时间;撤销立即生效,后续请求 401。
- [ ] token scope 与持有者角色权限**取交集**,不能超越角色权限(最小权限)。
- [ ] 可为 agent 创建运行凭证;agent 用其代表自身读写,所有动作以 `actor_member_id`(指向其 member 行)留痕(`actor_kind='member'`)。**签发路由 `POST /api/v1/agents/{agent_id}/tokens` 受 step-up 闸门保护(§1.1 凭证矩阵 / §5.5 ⑤,与 PAT 创建对称):超窗陈旧会话 → `403 reauth_required`,`mesh_pat_`/`mesh_agt_` 令牌 → `403 reauth_required`**。
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
- [ ] **多 tab 轮换竞态(R4-M4;R5-H1 真实并发断言清单)**:**双 tab 同时过期并发 `/auth/refresh`(真并行)→ 两个请求均 200**(其一为胜者:新 refresh + Set-Cookie;**其一命中 §3.8 宽限路径:仅获发新 access,响应无 refresh 明文、无 Set-Cookie、不二次轮换**);**响应乱序**下两 tab 最终 cookie/凭证收敛为胜者新值、后续请求均通过,**无误登出**;宽限窗(`MESH_REFRESH_ROTATION_GRACE_SECONDS`,默认 30s)外重放旧 refresh → 401;**会话已撤销或已过期(`expires_at < now()`)时胜者路径与宽限路径均 401,且不产生新 `token_hash`**(过期会话不得经轮换复活——`sessions.expires_at` 是 refresh 到期唯一真源);**CLI 双进程共用凭证文件并发 refresh → 文件收敛为胜者新 refresh,后来进程重读后重试成功**;完整断言清单见 §3.8。
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
- [ ] **敏感操作 step-up 再认证(R6-H3 状态机;R7-H1/H2 安全收口)**:**按 §1.1 凭证矩阵逐路由断言**——① **PAT 创建/撤销:`web` 与 `cli` 会话 JWT 均允许,各自检查本会话 `authenticated_at` 窗口**(判据 `authenticated_at IS NOT NULL AND now() - authenticated_at ≤ MESH_STEP_UP_WINDOW_SECONDS`,默认 900s),窗口外 `403 reauth_required`;② **2FA 启停 / OAuth 换绑解绑:仅 `web` 会话**(cli 会话调用 → `403`,即使窗口内);③ **change-password 不经 `authenticated_at` 预闸门**——事务内 `old_password` 校验即再认证,成功后更新 `authenticated_at`(断言:超窗 web 会话持正确旧密码改密成功,且 `authenticated_at` 刷新);④ **`mesh_pat_`/`mesh_agt_` 令牌调用 §1.1 凭证矩阵任一受保护路由(PAT 创建/撤销、**agent 运行凭证签发(见⑤)**、2FA 启停、OAuth 换绑/解绑)→ `403 reauth_required`(`details.reason='interactive_session_required'`)**,不存在实现侧绕过;⑤ **agent 运行凭证签发 `POST /api/v1/agents/{agent_id}/tokens`(MES-78 MEDIUM-1):与 PAT 创建对称——`web` 与 `cli` 会话 JWT 均允许,各自检查本会话 `authenticated_at` 窗口,窗口外 `403 reauth_required`**(断言:`authenticated_at` 超窗的陈旧 web 会话(如无人值守端末)签发 agent token 被拒;Web 完成 reauth 刷新 `authenticated_at` 后签发成功)。**来源赋值断言**:密码登录/注册后 `authenticated_at=now()`;OAuth **静默 SSO 复用 → NULL**(新鲜交互登录(可验证签名 `auth_time` 满足 `max_age`)才置位;**提供商无可验证 `auth_time` → step-up fail-closed,login 可登录但 `authenticated_at=NULL`,绝不以 callback 到达时间赋值**);**设备 cli 会话继承批准记录 `approved_authenticated_at`(批准事务经会话定位不变量读取),绝不以消费时刻置位**。**恢复入口**:`POST /auth/reauth`(密码/TOTP/OAuth 新鲜往返,仅 web);**旧 cli 会话无法 reauth → CLI 明确提示「Web 完成 reauth 后重新执行 `mesh auth login`(设备批准)」(cli.md §4.3 同口径),退码 2,无永久死结**。**负向 e2e(R7-H1 撤销窗口,关键)**:**撤销某 web 会话后,持该会话未过期 access 调 `device/approve`、`reauth`、PAT 创建 → 均 `401 unauthorized`(会话定位不变量),且断言不产生新 sessions 行 / api_tokens 行**;旧 Web 会话(`authenticated_at` 超窗/NULL)批准设备码 → CLI 会话继承 NULL/超窗值 → CLI 创建 PAT → `403 reauth_required` → Web 完成 reauth → 批准新设备码(继承新鲜时刻)→ CLI 创建 PAT 成功。**OAuth transaction e2e(R7-H3/R8)**:同一 `state` 重放 callback → 第二次拒绝(已消费);`purpose=reauth` 事务的 callback 不创建/换绑账号(断言 users/oauth_identities 无新行,仅该会话 `authenticated_at` 更新);`link` 事务 callback 时发起会话已不匹配 → 拒绝。**reauth 恢复流 e2e(R8-H1)**:`authenticated_at` 超窗/NULL 的 OAuth-only web 会话**可发起** `POST /auth/reauth`(不被 freshness 闸门自锁)→ 完成 OAuth 新鲜往返 → 仅原会话 `authenticated_at` 刷新;**撤销/过期会话调 reauth → `401 unauthorized`**;`start?purpose=reauth` 入口不存在(单一入口断言)。**2FA 分支排他 e2e(MES-78 LOW-2)**:启用 TOTP 的用户仅以 `{password}` 调 reauth → `422 invalid_credentials`(`details.reason='totp_required'`)且该会话 `authenticated_at` 未刷新(随后调 step-up 受保护路由仍 `403 reauth_required`);呈递有效 `{totp_code}` → 成功且 `authenticated_at` 刷新。**浏览器绑定 e2e(R8-H2,login CSRF 防护)**:① **攻击者在自己浏览器发起 login、截获有效 callback URL 诱导受害者浏览器打开 → 拒绝且受害者浏览器不建 session**(locator cookie 不匹配);② **provider mix-up**:callback URL 的 `{provider}` 与 transaction.provider 不符 → 拒绝;③ **往返期间发起会话被撤销** → `link`/`reauth` callback 拒绝(不变量 0 行),`login` 不受影响;④ locator cookie 属性断言:`HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth/oauth`,消费后被清理;⑤ 多 tab 并发往返:后发起者胜,先发起 tab 的 callback 被拒为过期态;⑥ **GET-only 链路(R9-H1)**:**真实跨站顶层 GET e2e**——从提供商域顶层 GET 重定向回 callback,Lax locator 随请求携带 → 匹配 → 登录成功(真实浏览器 e2e,非 mock);**`POST /api/v1/auth/oauth/{provider}/callback` → `405 method_not_allowed`**(任何 state 均不消费);**授权请求固定 `response_mode=query`**(断言 start 重定向 URL 携带该参数,**form_post 永不下发**——链路推演:Lax 不随跨站 POST 发送,POST callback 必缺 locator,故整条 POST 链路不可执行,从发起侧即关闭)。

### 5.6 实时

- [ ] WebSocket 连接握手鉴权,按 `workspace_id + principal` 注册频道;**每次订阅频道重新做资源级授权**(README §6.7)。
- [ ] 会话/token 撤销后,相关连接在下次心跳被拒;access 撤销延迟 ≤ 其 TTL(15min);撤销经 outbox→realtime 广播,不用进程内事件总线(README §6.6)。
- [ ] 异常登录提醒经站内 + 邮件双通道送达。
- [ ] 频道事件携带**频道内**单调递增 `seq`(无"全局 seq"),断线凭 `resume_from` 重放、游标过旧收 `resync_required`,无丢失无重复(README §6.7)。
