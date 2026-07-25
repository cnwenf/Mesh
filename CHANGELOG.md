# Changelog

Mesh 项目的所有重要变更都记录于此文件。
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.10.0] - 2026-07-25

project 项目模块(MES-30,阶段 4·核心工作与协作首个模块):project.md 五章全量落地——项目/健康度留痕/里程碑/迭代周期/前缀计数器,后端 + 前端 + 真实 e2e。

### Added

- **数据模型(§2)**:`projects`(含 `issue_seq` 项目级编号计数器、`key` 前缀)、`project_updates`(追加式健康度/状态留痕,作者 NOT NULL + ON DELETE RESTRICT,成员软删除保历史署名)、`milestones`、`cycles`、`project_members`、`project_templates`;全表 `UNIQUE(workspace_id, id)` + 同租户复合 FK(README §6.2),`lead_member_id` 采用 PG16 列级 `ON DELETE SET NULL (lead_member_id)`;迁移 0006 含 fail-closed RLS 策略与 `mesh_app` 授权,并补齐 0004 延迟登记的 `identifier_prefix_registry.project_id` / `member_project_access.project_id` → `projects` 复合 FK(前者列级 SET NULL:物理删项目后注册行保留、前缀永久占用)。
- **前缀永久保留与注册表排他(§6.3)**:`uq_projects_key` 为**普通(非部分)唯一索引**,软删除/归档后前缀不可复用;创建项目在同事务内经 `identifier_prefix_registry` 排他登记 `kind='project'`,与任一在册前缀(含 inbox 当前前缀与 retired 历史前缀)冲突 → 409 `project_key_taken`(README §9 T19 实测)。
- **接口(§3.1 全部端点)**:项目 CRUD / 归档恢复 / 软删除、健康度留痕端点(写入同时回写 `projects.health/status`)、里程碑 CRUD(逾期为派生态:`open` 且过 target_date)、周期 CRUD(状态切换;`auto_roll` 周期完成时同事务生成下一周期)、项目成员管理、模板 CRUD 与实例化(§3.2b:同事务建项目 + 初始里程碑/周期,issue 状态集/默认视图等待建模块项优雅降级入 `skipped`);§6.14 成功包络 / 游标分页 / `If-Match` 乐观并发(409 `conflict`)/ 错误码表(`project_key_taken` / `project_name_taken` / `project_archived` / `project_member_exists` / `template_name_taken`);归档项目写入 422;workspace-less 路径(`/projects/{id}` 等)经窄 SECURITY DEFINER 函数解析租户后走成员资格 + 资源级授权闸门;写端点限流(120/min)。
- **鉴权与可见性(§3.4)**:公开项目工作区成员可读;私有项目仅 `project_members` 命中者或 admin 可见(其他成员 403、guest 无授权 404);写入需项目成员/lead 或 admin,删除/归档/成员管理需 lead 或 admin;创建者自动成为项目 lead 成员。
- **实时(§3.5/§6.7)**:`project.created/updated/archived/unarchived/deleted` · `project_update.added` · `milestone.created/updated/deleted` · `cycle.updated` 全经 outbox → projector 唯一写入路径;**私有项目事件仅进 `project:{id}` 频道**(不广播 `workspace:{ws}:projects`);`project:{id}` 订阅经资源级授权 checker 每次订阅重验可见性;实时投影经 worker 实机验证(seq 频道内单调)。
- **前端页面(§4,v0.3.0 脚手架)**:项目列表(状态/已归档/我参与筛选、新建对话框含 key 自动建议与格式校验、状态徽章 + 健康度灯 + 进度条 + 负责人头像卡片、游标加载、实时增量合并)、项目详情(头部状态/健康度灯/进度 + 状态更新留痕对话框、概览/里程碑/更新动态 Tab、里程碑逾期标红与开合删除、归档/删除二次确认)、项目设置(字段编辑经 `useOptimisticMutation` 乐观更新 + 409 收敛、成员管理、危险区)、周期页(创建/状态切换/自动滚动提示);文案全量 i18n 外部化(en + zh-CN)。

### Deferred(随后续增量)

- 进度实时聚合(`GET /projects/{id}` 的 `progress/open_issues/done_issues` 现回退 `progress_cache` 或 0)与删除项目置空 `issues.project_id`(列级 SET NULL,identifier 不变,T18②)随 issue.md 增量接通——DDL 与验证脚本(`schema_r2_validation.sql` T18-2/2b)已按同款列级 SET NULL 实跑通过;周期未完成 issue 顺延/退回待办与相关成员通知随 issue.md / comment-inbox.md 增量;模板 `status_set_seed` / `default_view_config` 预置随 issue.md / kanban.md 增量(实例化时入 `skipped` 优雅降级)。

### Quality

- 后端单测(服务层直调 + 进程内 API)+ 真实 e2e(uvicorn 子进程以受限 `mesh_app` 角色连接、RLS 生效,真实 PostgreSQL 16 + Redis,真实 API 调用与落库校验)全绿;pytest-cov **94.57%**(≥90% 门禁;project 模块 schemas 100%、routes 98%、channels 97%、service 91%,整体与新增代码双达标);ruff 全绿。
- README §9 集成测试实测:**T1** 跨租户复合 FK(milestones/cycles/lead)INSERT 被数据库拒绝 + 跨工作区 API 同一 404;**T18** 真实 DELETE 语义(lead_member_id 列级 SET NULL 且 workspace_id 保持非空、物理删项目注册行 project_id 列级置空前缀保留、子表 CASCADE、留痕作者 RESTRICT);**T19** 前缀注册表排他(项目 key 撞 inbox/retired 前缀拒绝、软删除/归档后前缀不可复用)。`schema_r2_validation.sql` 100 项断言在 PostgreSQL 16 实跑全绿。
- docker compose Quick Start 实机验证:`alembic upgrade head` 应用 0006,注册/登录 → 建区 → 建项目 → 409 冲突 → 归档只读 422 全链路通过,`project.created` 经 outbox → projector 投影至双频道(seq 单调)。
- 前端质量 FRONTEND_QUALITY_PLACEHOLDER。
- 文档门 `check_event_vocab.py`(§6.7,事件零漂移)与 `check_roster_entry.py`(§6.12/T35)继续全绿。

## [0.9.1] - 2026-07-25

安全硬化(MES-37,MES-36 v0.8.0 增量安全审核闭环):修复 CRITICAL RT-C1——realtime 网关缺生产 JWT 签名密钥 fail-safe,连同其根因 MEDIUM RT-M2 一并修复。

### Security

- **RT-C1 realtime 网关生产 JWT 签名密钥 fail-safe(auth.md §5.5,README §2.2/§6.16)**:v0.8.0 增量把网关接到真实会话 JWT 验签路径,却未镜像 API 工厂已有的生产守卫——网关以独立部署单元单独启动时,`auth_mode=production` 漏配 `MESH_JWT_SECRET` 会静默启动并以仓库公开的默认开发密钥验签,攻击者可以公开密钥自签 JWT 冒充任意活跃用户的 realtime 身份(v0.7.0 网关 production 拒绝一切鉴权属 fail-closed,该误配置于 v0.8.0 翻转为 fail-open)。现 `mesh.realtime.app.create_app` 在 production + 默认开发密钥时拒启动(`ConfigError`),恢复 fail-closed;其余鉴权行为(首帧认证 / 算法固定 / fail-closed 语义)保持现状。
- **RT-M2 守卫共享化 + 注释对齐(根因修复)**:「production + 公开默认密钥 → 拒启动」抽为单一共享校验 `mesh.config.validate_auth_settings`,`mesh.api.app` 与 `mesh.realtime.app` 两个工厂启动时均调用,消除 `api/app.py` 内联复制,杜绝两个工厂再漂移;`config.py` 中 `DEV_JWT_SECRET` 与 `jwt_secret` 字段注释改为与实现一致(原注释声称 `load_settings` 负责该校验,实现中并不存在)。

### Quality

- TDD:两工厂 production + 默认开发密钥 → `ConfigError`(断言 `missing_fields` 与 detail)单测、dev 路径不受影响回归测试(默认密钥 dev 模式正常启动)、共享校验函数四分支全覆盖;真实 e2e 89 项全绿(生产网关进程以真实密钥启动正常、漏配密钥拒启动均实测);609 项全绿,pytest-cov **95.89%**(≥90% 门禁;config 100% / realtime.app 100%,整体与新增代码双达标);ruff 全绿。
- 文档同步:auth.md 安全清单新增「生产拒用公开默认签名密钥(fail-closed)」项;backend README 安全说明明确守卫为两工厂共享且网关独立校验自身配置。

## [0.9.0] - 2026-07-25

auth 增量 2 第二切片(MES-12):OAuth 提供商往返(auth.md §1.2 A5/A6)、会话/令牌撤销 realtime 广播(§3.7/§5.6,C4)、生产 SMTP mailer(A1/A4)、审计时间范围过滤(§5.3)。至此 auth.md **后端**全量落地(余 §4 前端页面与 `POST /agents/{id}/tokens` 便捷端点)。

### Added

- **OAuth 提供商往返(§1.2 A5/A6,§3.1,§4.5)**:vendor 中立 `OAuthProvider` 接口(authorization-code + **PKCE S256**,RFC 7636);`GET /auth/oauth/{provider}/start`·`/bind` 302 携一次性 `state`(Redis,TTL 600s,防 CSRF)+ `code_challenge`;`GET/POST /auth/oauth/{provider}/callback` 校验 state、换 code——**首登自动建号并绑定**(`password_hash=NULL`、邮箱视为已验证)、已知邮箱绑定既有账号、二次登录复用;`GET /auth/oauth/identities` 列绑定;`DELETE /auth/oauth/{provider}` 解绑(**删最后一种登录方式 → 422 `last_login_method`**,绑定至他人身份 → 409)。dev 内置 `MockOAuthProvider` 使完整 code+PKCE 往返 e2e 真实跑通;**零厂商绑定、零外部出处**,生产提供商运营方配置。
- **C4 撤销广播(§3.7/§5.6)**:登出/全端登出/撤销会话/refresh 重放/密码重置/PAT 撤销,均于**同事务**经 outbox → projector 唯一写入路径发新登记事件 `session.revoked`(词汇 96→97,§6.7 注册表 + CI 校验同步),于持有者活跃工作区频道(`workspace:{id}`,经 SECURITY DEFINER `mesh_my_workspaces` 解析)广播使相关连接下次心跳鉴权失败重连被拒;不用进程内事件总线;access 撤销延迟 ≤ 其 TTL。
- **生产 SMTP mailer(A1/A4)**:`auth/mailer.py` 统一 `Delivery`——dev 走 Redis dev-mailbox(测试路径,键格式不变)、production 走真实 SMTP(`MESH_SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/USE_TLS`,阻塞 `smtplib` 经 `asyncio.to_thread` 不卡事件循环)、未配置则日志 no-op(API 仍可启动,运营方配置后闭环);邮件正文 vendor 中立;compose 透传 + `.env.example` 说明 + `MESH_APP_BASE_URL` 验证/重置链接。
- **§5.3 审计时间范围**:`GET /workspaces/{ws}/audit-logs` 增 `before`/`after`(RFC3339 半开区间 `(after, before)`,naive 输入归一 UTC),非法时间戳 400,供 §4.4 审计页消费。

### Quality

- 后端单测 + 进程内路由 + 真实 e2e(uvicorn 子进程、mesh_app 受限角色 RLS live)全绿;pytest-cov **95.88%**(≥90% 门禁;oauth_routes/mailer/realtime 100%、oauth 92%,整体与新增代码双达标);ruff 全绿;main CI 六项全绿。
- 验收独立实测(真实 API + psql 核对,35 项 + worker 投影验证):OAuth 全往返(首登建号/复用/已知邮箱绑定/state 一次性/坏 state 400/未知 provider 404)、bind/unbind(鉴权门控/409 冲突/422 最后登录方式保护)、`session.revoked` 同事务落 outbox 并经 worker 投影至 `realtime_events`(logout/logout-all/PAT 撤销三路均验)、审计 before/after 半开区间与 400、dev-mailbox 无回归。

### Fixed

- `backend/README.md` 事件词汇计数 96→97(随 `session.revoked` 登记,验收时一并修正)。

## [0.8.0] - 2026-07-25

workspace §4 前端 UI 接通(MES-26):把已合入 main 的 workspace 后端 v0.4.0 与前端脚手架 v0.3.0 连接到真实 UI,完成 MES-13 的 UI 收尾;并补齐 realtime 会话 JWT 鉴权管道使前端能以真实登录消费实时事件。

### Added

- **工作区上下文路由与切换器**(workspace.md §4.1/§4.2):`/w/:workspaceSlug/*` 路由 + `WorkspaceProvider`(by-slug 加载、历史 slug 规范化重定向 W6、非成员与不存在同一 404 无泄漏、PATCH 就地更新);TopBar 工作区切换器(列全部工作区 + 当前标记)+ 三步创建向导(名称 → slug 实时格式校验与占用探测 → 可选邮箱邀请,409 `slug_taken`/400 `validation_error` 具名呈现)。
- **工作区设置页**(§4.1/§4.2,admin+ 门控,member 直达呈「无权限」态 §6.12):基本信息表单(名称 / slug「旧链接自动重定向」提示 / logo https-only 即时校验 §6.16 / 时区 / 工作区默认 locale,422 `unsupported_locale`/`invalid_timezone`、409 `slug_taken` 具名呈现);邀请面板(邮箱 chip 批量 / 链接模式、角色预设、`max_uses`/`expires_in_hours` 上限提示与 422 `invitation_limits_exceeded` 具名呈现、一次性 `invite_link` 复制卡);邀请列表(四状态徽标 + 用量 + 时区化过期时间 + 撤销 + realtime `invitation.redeemed` 合并);角色能力矩阵(owner/admin/member/guest × 设置/邀请/成员/删除)+ 名册消费(member.md §3 契约,端点缺失优雅降级,行内角色变更 `last_owner`/`agent_owner_not_allowed` 具名呈现);危险区(owner-only,slug 二次确认删除 W10)。
- **邀请接受页**(§4.3/§4.4):公开 `preview` → 登录门控(`?next=` 回跳,防开放重定向)→ `accept`;四 reason(`not_found`/`expired`/`exhausted`/`revoked`)各呈 UI 态;重加入同成功态(Leader 裁决 pin@MES-14);token 仅经路径传递,不落入 UI 文案。
- **账号登录接通**(auth.md §3.1,auth v0.2.0):邮箱/密码登录 + 注册切换,具名错误(`invalid_credentials` / `weak_password` 三 reason / `conflict` / MFA 质询);保留 dev-token 直填入口(默认折叠,联调/CI 兼容)。
- **realtime 会话 JWT 鉴权管道**(backend,README §6.16):`JwtPrincipalAuthenticator` + `ChainedAuthenticator`,经 `mesh_my_workspaces` 引导函数取 active 名册构建 principal;api/gateway 两端 production = JWT、dev = JWT + dev-token 链——使前端能以真实会话经首帧鉴权消费 WS 实时事件(§6.16 单机制不变,生产 placeholder 被替换)。
- **实时与降级消费**(§4.5/§6.7):设置页/工作区页订阅 `workspace:{id}`,`workspace.updated` 浅合并、`workspace.deleted` 回首页;WS 未连通时按频道水位轮询 REST 对账端点降级。
- **i18n 文案**(i18n.md §2.4):zh-CN + en 消息目录各 +167 键(键集一致、内容哈希 version 重算),覆盖全部 §4 文案;locale 协商链「工作区默认」级经 v0.8.0 设置页写入生效。

### Verified

- 单测 729 项全绿;覆盖率 97.7% 行 / 92.3% 分支 / 96.8% 函数(全局 90/90/90/90 门槛 + 变更行 97.9% 全过)。
- 真实后端 e2e(workspace-flow)15 项全绿:注册/登录、向导建区、设置保存、邀请创建/复制/接受(登录回跳)/次数耗尽/过期/撤销/伪造 token、重加入、越权无权限态、跨租户 404、工作区默认 locale 协商、zh-CN/en 切换、realtime 用量更新、危险区删除;MES-16 实时契约 e2e 3 项全绿;mock e2e 23 项全绿。

## [0.7.0] - 2026-07-25

auth 增量 2 第一切片(MES-12):PAT / API token(auth.md §2.5/§3.2)+ 审计查询端点(§3.3),复用 v0.4.0 的 RBAC 裁决器 / append-only `audit_logs` 与 v0.6.0 的统一名册。

### Added

- **`api_tokens` 表(迁移 0005,auth.md §2.5)**:持有者统一 `owner_member_id` **复合 FK→`members(workspace_id, id)`**(README §6.1 去多态 + §6.2 同租户,跨工作区持有者数据库层拒绝);**仅存 SHA-256 哈希**,明文仅创建响应返回一次;`mesh_pat_` / `mesh_agt_` 可区分前缀 + 非秘密 `prefix` 展示;fail-closed RLS + `mesh_app` 授权;SECURITY DEFINER `mesh_api_token_by_hash()` 做先于租户上下文的 bootstrap 查询(对齐邀请链路,EXECUTE 仅授 mesh_app)。
- **TokenService(§5.2/§5.5)**:创建(明文一次性)/ 列出(member 仅自己、admin/owner 全部,不含哈希与明文)/ 撤销(即时失效);**`role_override` 创建 + 使用双重强校验**——高于持有者当前角色 → `422 role_override_too_high`,持有者事后被降级则使用时拒绝而非提权;**scope∩角色矩阵最小权限**(token 永不越权);**agent 运行凭证默认剥离 `agent:trigger`**(Z5 防回环);创建/撤销同事务写 append-only 审计(`token.created` / `token.revoked`,`actor_kind='member'`)。
- **端点(§3.2/§3.3)**:`GET/POST/DELETE /workspaces/{ws}/api-tokens[/{id}]`(`token:manage` 门控,跨持有者创建需 admin+)、`GET /api-tokens/whoami`(PAT/agent 凭证自身鉴权,解析有效 principal:工作区/角色/scopes/成员类型)、`GET /workspaces/{ws}/audit-logs`(admin+,action/actor 过滤 + keyset 游标分页)。写端点按 principal+IP 限流(120/min,§3.6)。

### Quality

- 后端单测 + 进程内路由 + 真实 e2e(uvicorn 子进程以受限 `mesh_app` 角色连接、RLS live)全绿;pytest-cov **95.91%**(≥90% 门禁;token_routes 95% / tokens 98% / token_schemas 100%,整体与新增代码双达标);ruff 全绿;main CI 三 job 全绿。
- 验收独立实测(真实 API + psql 落库核对,50 项):明文仅一次与行内零明文、list 无明文、whoami 鉴权(JWT/伪 token 拒绝)、scope∩角色最小权限、role_override 创建与使用双路径 422(降级后使用不提权)、撤销即时 401 与持有者/admin 权限、跨持有者创建门控、agent 凭证前缀与 `agent:trigger` 剥离、过期即 401、审计落库 + 过滤 + 权限门控 + UPDATE/DELETE 触发器拒绝、RLS fail-closed(无 GUC 拒绝 / 异租户 0 行)、跨工作区持有者复合 FK 拒绝。

### Deferred(增量 2 余项,本 Issue 续做)

- OAuth 提供商往返(§1.2 A5/A6)、C4 会话撤销 outbox→realtime 广播、生产 SMTP mailer、auth 前端页面(§4,含 step-up 再认证交互与审计页**时间范围**过滤——审计端点现支持 action/actor + 游标分页,§5.3 时间范围随审计 UI 补齐)、`POST /agents/{agent_id}/tokens` 便捷端点(待 agents 表;agent 凭证逻辑已在 service 层落地并经 seeded agent member 验证)。

## [0.6.0] - 2026-07-25

member 统一成员名册(MES-14,阶段 3):member.md 五章在 v0.4.0 已落地的 `members` / `member_project_access` 表之上全量落地功能层 + 名册前端页面。`members.id` 作为全系统统一引用键(README §6.1),人与 agent 对称同册。

### Added

- **名册查询与筛选投影(member.md §3.1/§3.2)**:`GET /workspaces/{ws}/members` 人与 agent 同册返回,支持 `member_type`(all/human/agent)、`status`(默认隐藏 removed 软终态)、`role`、`q` 模糊搜索(命中 display_override / users.display_name / email)与 `(joined_at,id)` keyset 游标分页;`member_type=agent` 是同一端点的**筛选投影**,非第二套名册。`GET /workspaces/{ws}/members/{id}` 返回 profile + `counts.open_issues_assigned`(issue 模块落地前为 0)。
- **显示名权威解析(member.md §2.4)**:服务端单一 `resolve_display_name`,`display_override` → `users.display_name`(auth.md 单一名字段,即 spec 的 full_name)→ 邮箱本地段 / agent 短 id 兜底,接口统一返回单一 `display_name`,前端仅渲染并叠加 AI 徽章,杜绝各处漂移。
- **成员管理(§3.3/§3.4)**:`POST /members`(admin;人类按 user_id 入册,已存在 active → 409 `already_member`,disabled/removed 行以新授予角色复活,镜像邀请兑换;agent 入册 422 `agents_not_available`——agents 表与创建流程随 agent.md 增量)、`PATCH /members/{id}`(role 复用 workspace 既有审计+事件路径,status 仅 active↔disabled、`removed` 仅经 DELETE,`display_override` 支持本人自助或 admin;no-change 不发事件/不写审计 §6.9)、`DELETE /members/{id}?reassign_to=`(软删 `status='removed'` 保留历史引用,可选转派,目标须同工作区活跃成员否则 422 `reassign_target_invalid`)、`POST /members/reassign`(批量转派钩子)。last_owner / agent_owner_not_allowed 服务端强校验(不信任前端禁用)。
- **guest 项目级可见性(M12)**:`member_project_access` 授予/变更(ON CONFLICT upsert)/撤销,仅对 `role='guest'` 生效(其它角色 422 `not_guest_member`),permission 限 read/write;撤销经 `assert_guest_project_visible` 即时生效。
- **实时事件(§3.5/§6.6/§6.7)**:`member.added` / `member.updated`(changes) / `member.removed` / `member.role_changed` 全经 outbox → realtime 唯一写入路径(词汇注册表已登记);角色变更、状态变更、移除、转派、入册均写 append-only 审计(`actor_member_id` + `actor_kind='member'`)。
- **`GET /users/me`**:返回当前登录用户 + 其在各工作区的成员身份(经 `mesh_my_workspaces` definer 函数)。
- **名册前端页面(§4,README §6.12/T35)**:`/members` 单一页面,人与 agent 同表 + AI 徽章;「仅 Agent」为 `?member_type=agent` 的**同路由/同组件筛选投影**(同一 `[ + 新建 Agent ]` 入口,不形成独立 Agents 列表页/第二导航/第二创建入口,`check_roster_entry.py` 继续通过);角色行内下拉(agent 行 owner 选项禁用)、停用/启用、移除(带转派目标选择器)、邀请人类 Tab、成员详情抽屉;文案经 i18n 外部化(en + zh-CN,目录重算版本哈希)。
- **REST 端点(§3.1)**:名册列表/详情/加入/更新/移除/批量转派/可用 agent/项目共享四端点 + `GET /users/me`;写端点按 principal+IP 限流(120/min)。

### Deferred(随后续增量)

- `members.agent_id → agents` 复合 FK 与 agent 实际创建(POST agent 入册现返回 422 占位、前端 `[ + 新建 Agent ]` 为「即将上线」占位态),待 agent.md 增量;issue 转派实际落库与 `counts.open_issues_assigned` 真实计数,待 issue.md 增量(现经 `NullReassigner` 钩子返回 0);`member_project_access.project_id → projects` 复合 FK 与项目存在性校验,待 project.md 增量;`/members/{id}/presence` 为 spec 可选项,无在线态来源前暂不实现(`member.presence` 词汇保留)。

### Quality

- 后端单测 + 真实 e2e(uvicorn 子进程以受限 `mesh_app` 角色连接、RLS 生效,真实 PostgreSQL 16 + Redis,真实 API 调用与落库校验)全绿;pytest-cov **95.75%**(≥90% 门禁;member 模块 display/reassign/schemas 100%、routes 98%、service 97%,整体与新增代码双达标);ruff 全绿。
- 真实 DELETE 行为与约束负向(T18/T1):审计 actor 成员物理删除被 RESTRICT(NO ACTION)拒绝、guest 项目共享行随成员物理删除 CASCADE、多态身份 CHECK(user_id/agent_id 恰一非空)与 agent-owner CHECK 经原始 SQL 负向验证;跨工作区成员读取/变更同一 404(无存在性泄漏);停用成员被成员资格门拦出、启用恢复。
- 前端 lint / typecheck / prettier 全绿,574 项单测通过,生产构建成功,新增代码覆盖率 **96.2%**(verify-coverage 门禁 ≥90%);真实浏览器 e2e(Chromium → vite → 真实 API/RLS → mesh_test)走查名册渲染 + AI 徽章 + 仅 Agent 同路由投影 + 单一新建入口 + 角色/停用/移除真实落库。
- 文档门 `check_roster_entry.py`(§6.12/T35)与 `check_event_vocab.py`(§6.7)继续全绿;`schema_r2_validation.sql` 无新增 DDL 不受影响。

## [0.5.0] - 2026-07-25

阶段 2 前端延后接通项全量落地(MES-24):i18n 协商链「工作区默认」级接通、账号偏好写入服务端同步、§6.16 WebSocket 鉴权收紧为首帧单一机制(前后端事实上收敛 + Spec 明文收口)。本版本包含此前随主干合入但尚未打标的 [0.4.1] 安全修复(MES-28 cryptography 升级)。

### Added

- **i18n 协商链「工作区默认」级接通(README §6.18 第三级)**:新增 `api/workspace.ts` 两步获取——列表 `GET /workspaces`(list_view 按 workspace.md §3.2 不含 settings)→ 单对象 `GET /workspaces/{id}` 读 `settings.default_locale`,经 `useWorkspaceLocale` 注入 `I18nProvider` 的 `workspaceDefaultLocale`(骨架期传 null,本级正式生效)。协商链端到端:**用户无偏好 + 工作区默认 zh-CN → UI 中文**;用户偏好优先于工作区默认(显式参数 → `users.settings.locale` → 工作区默认 → 系统候选 → `en`)。工作区 API 不可达/无工作区静默降级(协商链跳过本级)。
- **账号偏好写入接通 `PATCH /api/v1/users/me`(auth.md §3.1)**:`settingsStore` 的 theme/locale/timezone 写入经 `preferencesSync` fire-and-forget 同步服务端(乐观更新,本地状态即时生效);网络错误静默降级、本地持久化作为降级镜像(离线可用)。
- **偏好清除语义(前后端协同)**:「跟随工作区默认」(locale 置 null)发送**显式 null**,后端 `update_user` 对显式 null 执行 `merged.pop`(此前 null 被忽略保留旧值);theme 同款语义。
- **422 具名错误 UI 可见(§6.14 → §6.18 前端渲染)**:`SettingsPage` 消费 `lastSyncError`,`unsupported_locale` / `invalid_timezone` / network / server 四类按 error code 渲染 i18n 文案的 `role="alert"` danger 横幅,可关闭(`clearSyncError`)。
- **全局 API 客户端单例**(`api/instance.ts`):组装 `MeshApiClient`(env + authStore token),供 Provider 树与偏好同步共用。

### Changed

- **§6.16 WebSocket 鉴权收紧为「连接建立后首帧认证」单一机制**(Leader 决策):删除「子协议(Sec-WebSocket-Protocol)」可选项,注明 v0.1.0 起实现基线(前后端已于 MES-11/MES-16 收敛于首帧 `{op:'auth',token}` → `auth_ok`);README §6.16 正文修订随代码同 PR,下游 `kanban.md` / `auth.md` / `chat-session.md` 及 `RealtimeClient.ts` / `types/realtime.ts` 注释同步对齐,全项目无旧双选项表述残留。

### Quality

- **真实后端 + 真实浏览器验收(非 mock)**:docker compose 实机起服(v0.4.0 后端 + 本 PR 构建),真实 API 链路实测(locale 设 zh-CN → 显式 null 清除 → 回读 `{}`;fr-FR → 422 `unsupported_locale`;工作区 `default_locale` PATCH/回读/两步读取);Chromium 实操验证协商链端到端与 422 alert 横幅(附截图证据)。
- 前端 UT **601 全量通过**,整体覆盖率 **99.23%**;`SettingsPage.tsx` 100%(含 6 个 banner 场景)、新增模块均 ≥97.97%。后端 service 层直调补测覆盖 null-pop 分支(`auth/service.py` L636/L642 覆盖率实测入账),路由层 in-process + 真实 HTTP 子进程 e2e 双护栏;auth 相关用例 73 项通过,后端整体 pytest-cov **95.33%**(≥90% 门禁)。CI 8 项全绿(quality/e2e/backend-ci/spec-checks/DDL/词汇校验)。
- 验收过程三轮打回闭环:① 真实 e2e 发现「locale 清除必 422」「列表响应无 settings 致协商链死代码」「422 无 UI 提示」+ §6.16 下游残留;② 覆盖率必查项打回(SettingsPage 新代码 83%、后端 pop 分支 0 覆盖);③ 终验补回 service 层直调用例后入账;合并时另发现并补回一处被误删的既有断言(`test_settings_invalid_theme` status_code,行为在路由/e2e 层仍有断言,不阻断)。

## [0.4.1] - 2026-07-25

### Security

- **升级 `cryptography` 至 >=48.0.1**(backend 直接依赖,用于 MFA 密钥的 Fernet 静态加密):修复 **GHSA-537c-gmf6-5ccf**(CVSS 7.5 HIGH,cryptography wheel 静态链接的 OpenSSL 越界读,影响 `>=0.5.0, <48.0.1`)。`backend/pyproject.toml` 依赖下限由 `>=42.0` 提升至 `>=48.0.1`(实际解析安装 49.0.0)。MES-27 安全审核全量 `pip-audit` 发现并立项(MES-28);升级后 backend 依赖图 `pip-audit` **零已知漏洞**(setuptools 82.0.1 的 PYSEC-2026-3447 为 MEDIUM 构建期问题,已移交 MES-23 排期池,不阻塞本项)。
- **全量回归**:单测 + 真实 e2e(真实 PostgreSQL 16 + Redis,真实 API 调用)共 **417 项全绿**,pytest-cov **95.44%**(≥90% 门禁);Fernet/MFA 相关 30 项用例重点确认通过(密钥派生、加解密往返、篡改/换钥拒绝、TOTP 全流程)。

## [0.4.0] - 2026-07-25

workspace 工作区与多租户基础(MES-13,阶段 2):workspace.md 五章后端全量落地(前端脚手架已随 v0.3.0 合入 main,设置/邀请 UI 页于后续增量接通)。

### Added

- **工作区 CRUD 与 slug 重定向**(workspace.md §1–§3):创建即成 `owner`(同事务播种名册条目与默认收件箱前缀 `WS`)、列出我的工作区(keyset 游标、携带 `my_role`)、UUID / slug 双寻址;改名自动写 `workspace_slug_history`,旧 slug 经 `GET /workspaces/by-slug/{旧slug}` 解析到新工作区(W6);软删除仅 owner + 输入 slug 二次确认,保留期内 owner 可 `restore`(slug 被占则 409)。
- **settings 单一真源(R3/R4,T32)**:`settings.default_locale` 是工作区 locale 的**唯一真源**(默认 `en`,与 i18n.md/README §6.18 一致);模型与响应**不含 `default_language` 列/字段**,无双写;已知键类型校验(非法 locale → 422 `unsupported_locale`、非法时区 → 422 `invalid_timezone`),未知键透传前向兼容,按键浅合并(PATCH 语义)。
- **邀请体系**(§2.3/§2.4/§3.2/§4.4):`workspace_invitations` 链接生命周期 `active`/`revoked`/`expired`/`exhausted`(**无 pending/accepted**——与兑换记录分离,README §9 T11);`max_uses`/`expires_at` 恒 NOT NULL(默认 10 次 / 7 天,不存在"不限次/永不过期",MES-4);token 仅存 SHA-256 哈希,明文仅在创建响应 `invite_link` 一次性出现;显式上限受工作区可配置 caps 约束(默认 100 次 / 720 小时,超限 422 `invitation_limits_exceeded`;未指定取默认且不受 caps 拒绝,LOW-2);定向邮箱批量(≤50、小写归一、同工作区同邮箱 active 唯一 → 409)。
- **接受邀请(原子 + 幂等)**:`§3.2` 条件 UPDATE 单事务原子递增 `used_count`(可用性/余量/过期全部下推 WHERE,无 check-then-write),同事务落 `workspace_invitation_redemptions` + `members`;`UNIQUE(invitation_id,user_id)` 使重复/并发同用户接受为 no-op;并发最后一名额恰一人成功(T11);用尽惰性/显式置 `exhausted`;公开 preview 仅返回有限字段(原因 `not_found`/`revoked`/`expired`/`exhausted`)。
- **RBAC 裁决构件(auth.md §2.7)**:声明式角色×权限矩阵 + 工作区成员资格门(一切不可见情形——不存在/非成员/已删除/已停用——统一同一 404,不泄漏存在性,§5.3)+ guest 项目级可见性钩子(`member_project_access`,供 project 模块消费)。
- **统一名册 `members` 落表(member.md §2.2)**:多态 CHECK(恰一个身份指针)、agent 不可为 owner(DB CHECK 兜底)、`UNIQUE(workspace_id,id)` 供全系统复合 FK 引用;角色变更端点(admin 强校验、last_owner 保护、409 `last_owner`/`agent_owner_not_allowed`)。
- **审计落表(auth.md §2.6)**:`audit_logs` append-only(挂载 0003 触发器 + 应用角色禁 UPDATE/DELETE),行为者 `actor_member_id` + `actor_kind∈(member,system)`(去多态,人/agent 经 JOIN `members.member_type` 判别);工作区更新/删除、邀请创建/撤销/接受、角色变更均留痕。
- **前缀注册表(§2.6,README §6.3/T19)**:`identifier_prefix_registry` 工作区级永久排他(`UNIQUE(workspace_id,key)`);工作区创建播种 `WS`;变更收件箱前缀旧键置 `retired` 永久保留(历史 identifier 不重编号),冲突 422 `prefix_reserved`;`occupy_project_prefix` 钩子供 project 模块占用项目 key(冲突 409 `project_key_taken`);`workspaces.inbox_issue_seq` 行锁自增助手(T15 并发无重号)。
- **多租户强约束(§6.2)**:全部新租户表启用 fail-closed RLS 租户策略;`invited_by` / `member_id` / `invitation_id` 同租户复合 FK(跨工作区引用 INSERT 即被数据库拒绝,T1);三个窄 `SECURITY DEFINER` 引导函数(token 解析、我的工作台列表、旧 slug 解析,PUBLIC 已回收)保证"工作区未知"流程下策略仍然 fail-closed。
- **实时事件(§3.5/§6.6/§6.7)**:`workspace.updated` / `workspace.deleted` / `member.added` / `member.role_changed` / `invitation.redeemed` 全部经 outbox → realtime 唯一写入路径(词汇注册表已登记)。
- **定时过期清扫**:worker 监督循环 `invitation-sweep`(可配置间隔,默认 5 分钟),与接受/预览的惰性判定互补。
- **REST 端点(§3.1)**:`POST/GET /workspaces`、`GET /workspaces/{id}`、`GET /workspaces/by-slug/{slug}`、`PATCH /workspaces/{id}`(admin)、`DELETE /workspaces/{id}`(owner + 确认)、`POST /workspaces/{id}/restore`(owner)、邀请三端点(admin)、`POST /invitations/accept`(登录)、`GET /invitations/preview`(公开)、`PATCH /workspaces/{ws}/members/{id}` 角色变更(admin)。写端点按 principal+IP 限流(§3.6 通用写 120/min)。

### Deferred(随后续增量)

- `members.agent_id → agents` 与 `identifier_prefix_registry.project_id → projects`、`member_project_access.project_id → projects` 的复合 FK,待 agents / projects 表随各自 owner 增量落地后以 ALTER 补齐(验证脚本同款延期模式);前端设置/邀请页面于后续增量接通(脚手架已随 v0.3.0 合入 main)。

### Quality

- 单测 + 真实 e2e(uvicorn 子进程以受限 `mesh_app` 角色连接,RLS 在应用路径真实生效 + 真实 PostgreSQL 16 + Redis,真实 API 调用与落库校验)共 **417 项全绿**;pytest-cov **95.44%**(≥90% 门禁,新增模块 ≥92%、多数 97–100%,整体与新增代码双达标);ruff 全绿。
- 跨租户负向测试:猜测 UUID 跨工作区访问与不存在资源返回**同一 404 信封**(无存在性泄漏);邀请 token 哈希不可逆(数据库无明文);超上限/过期/撤销邀请被拒;`max_uses=1` 并发接受恰一人成功(T11);RLS 无 GUC 即不可读、错租户写入被策略拒绝。
- `schema_r2_validation.sql` DDL 与行为验证(PG16,100 条断言)继续全绿;`docker compose up --build` 一键可跑(冒烟:建区 → 改名重定向 → 邀请创建/预览/接受/用尽 → 跨租户 404 → 角色变更审计,全部通过)。

## [0.3.0] - 2026-07-25

前端从 0 到 1:SPA 工程脚手架、API/实时客户端契约层、设计系统与体验基线、i18n 基线(MES-16,阶段 1·B)。契约语义与 docs/specs/README.md §3.2/§6.7/§6.12/§6.14/§6.16/§6.18 一致,实时线缆协议与已发版后端 v0.1.0 逐帧对齐(连接后首帧鉴权,token 不入 URL)。

### Added

- **SPA 工程脚手架**(§3.2):React 18 + TypeScript 5 + Vite 6 + react-router-dom 6 + zustand 5 + react-intl 7(选型理由见 frontend/README.md);乐观更新 + 服务端版本校验、WebSocket 增量合并、离线降级轮询三套机制骨架(均含测试)。
- **API 客户端契约层**(§6.14/§6.5):Bearer 鉴权;三类成功包络解析(单对象 / 列表 `next_cursor` / 分组整体游标);keyset 游标分页 hook;`version`/`If-Match` 乐观并发与 409 收敛;创建/动作类请求自动 `Idempotency-Key`;统一错误信封按 `code` 具名分发;过滤限制(深度 3 / 条件 20)预校验与 `filter_too_complex`/`query_cost_exceeded` 归类。
- **实时客户端**(§6.7/§6.16):**首帧鉴权** `{op:'auth',token}` → `{op:'auth_ok'}`(token 绝不进 URL query,对齐已发版后端 v0.1.0);每频道 `last_seq` 持久化;`resume_from` 重放与 seq 幂等去重;`resync_required` → REST `/api/v1/realtime/events` 对账(Bearer + 游标翻页)→ 无感恢复;指数退避重连;浏览器 online/offline 感知;离线降级轮询编排(`useOfflinePolling`,WS 未连通时按频道水位轮询并经实时同路径注入);增量合并按完整变更字段 + `visibility` 归属 + `updated_at`/`version` 防回退(payload 浅拷贝,纯函数不可变)。
- **设计系统与体验基线**(§6.12):语义 token 亮/暗两套(单一事实源 + 防漂移测试,均经 WCAG 2.1 AA 4.5:1 自证);light/dark/system 即时切换(无刷新、防闪烁);焦点可见/reduced-motion/prefers-contrast;12 个插槽化基线组件(Dialog 焦点圈养+焦点归还、Toast live region、StatusDot 文本+色点等);快捷键体系(Ctrl/Cmd+K 命令面板、? 帮助层、G→I/B/M/A 序列键、输入框豁免、等价鼠标路径);异常态组件矩阵(loading/empty/error/offline/重新同步)。
- **i18n 基线**(§6.18):ICU MessageFormat 消息目录(en 权威源 + zh-CN,key 集合一致性/可渲染性/匿名化测试);协商链(`?locale=` 显式参数 → `users.settings.locale` → 工作区默认 → `navigator.languages` 系统级 → en,Accept-Language q 值 + BCP-47 主干回退);缺 key 三级回退 + 开发期可见标记与去重上报;ETag 版本缓存;日期/数字/相对时间本地化 + 时区化展示与输入解析回 UTC(原生 Intl)。
- **App shell 与占位页**:Provider 树 + 路由(登录占位/设置框架/导航占位/404/ErrorBoundary)、顶栏连接状态(颜色非唯一信号)、离线/重新同步横幅、首页骨架演示区(主题/语言/快捷键/异常态/实时增量合并);文案一律经消息目录外部化。
- **前端 CI**:`.github/workflows/frontend.yml`(lint → typecheck → test:coverage(≥90% 门禁)→ 新增代码覆盖率校验 → build → Playwright 真实浏览器 e2e)。

### Quality

- 单元/组件测试 546 项全绿;整体覆盖率 lines 99.23% / branches 95.82% / functions 99.25%(v8,四项均 ≥90% 门禁);新增代码覆盖率 91.4%(scripts/verify-coverage.mjs,≥90%)。
- Playwright 真实浏览器 e2e:对契约 mock 服务端 23/23;**真实后端 v0.1.0 联调 3/3**——首帧鉴权握手、outbox→relay→projector→Redis fan-out 实时帧增量合并、断线重连 `resume_from` 重放、游标过旧 `resync_required` → REST 对账 → 无感恢复(验收员独立复现,非仅审截图)。
- tsc / ESLint(0 错误)/ 生产构建(gzip ~94KB)全绿;匿名化扫描干净(无外部出处暴露)。

## [0.2.0] - 2026-07-25

auth 鉴权体系核心(MES-12,阶段 2 增量 1)+ 应用数据库角色 RLS 加固(M1/M2)。auth 依赖 members 表的余项(PAT/api_tokens、audit_logs 落表与端点、RBAC 角色矩阵端点、OAuth 往返、RLS 运行态 GUC、auth 前端页面、会话撤销 realtime 广播、生产 SMTP 投递)随 workspace/member 增量续做。

### Added

- **auth 认证核心**(auth.md §2.2–§2.4.1/§3.1/§4.5/§5.x):全局身份表 `users` / `sessions` / `password_reset_tokens` / `email_verification_tokens` / `oauth_identities` / `login_attempts` + Alembic 迁移 0003(含 append-only 审计触发器函数 `mesh_audit_append_only()`,供后续 `audit_logs` 表挂载);`users` 不含 `member_id` 反向列(§6.1)。
- **密码与登录**:argon2id(OWASP 下限成本参数)+ 恒定时间校验 + 强度策略(≥8 位含字母数字、拒常见弱密码);注册/登录/登出/全端登出;防账号枚举统一 422 `invalid_credentials`(账号不存在走哑哈希,文案与耗时一致)。
- **会话体系**:短期 access JWT(15min,验签固定 `alg`、显式拒 `none`、防 HS/RS 混淆、`typ=access` 限定)+ 可撤销 refresh(仅存 SHA-256、轮换防重放、重放即撤销该用户全部会话);会话列表与按 ID 撤销(限本人)。
- **一次性令牌**:密码重置(1h)/邮箱验证(24h)独立落表,仅存哈希、TTL、单次消费、新建作废旧令牌。
- **MFA**:TOTP(密钥 Fernet 加密存储)+ 10 个一次性备用码 + 登录二步校验(`mfa_required` → `/auth/mfa/verify`)。
- **登录保护**:`(IP, 邮箱)` 二元组失败锁定(423 `account_locked`,避免纯邮箱维度锁定 DoS)+ Redis 滑动窗口限流(登录/注册/重置均按 §3.6 `(IP, 邮箱)` 维度,429 + `Retry-After` + `X-RateLimit-*`)。
- **账号偏好真源(R3)**:`users.settings`(locale/theme)+ `timezone`;`PATCH /api/v1/users/me` 键级浅合并;非法 timezone → 422 `invalid_timezone`、不支持 locale → 422 `unsupported_locale`、非法 theme → 422 `validation_error`(auth canonical,README §9 T32)、未知字段 → 400、`avatar_url` 仅 https(§6.16)。
- **安全红线**:生产环境拒用 dev 签名密钥(`create_app` fail-safe);令牌不落 URL query(WS 首帧认证沿用骨架)。

### Security

- **应用路径 RLS 生效(M1/M2)**:API 与 realtime 网关以受限非 owner 角色 `mesh_app` 连接(迁移 0002 创建,`ALTER DEFAULT PRIVILEGES` 为后续模块表自动授权),使 `realtime_channels`/`realtime_events` 的租户策略对应用路径真正生效;worker 保留 owner 角色跑跨租户 relay/projector/retention;compose 服务端口绑定 loopback(仅本地开发)。

### Quality

- 单测 + 真实 e2e(uvicorn 子进程 + 真实 PostgreSQL 16 + Redis,真实 API 调用与落库)共 272 项全绿;pytest-cov **95.52%**(≥90% 门禁,auth 各模块 ≥92%,整体与新增代码双达标);ruff 全绿。
- `schema_r2_validation.sql` DDL 与行为验证(PG16,100 条断言)随 main CI 持续通过;main 三 job 全绿。

## [0.1.0] - 2026-07-25

首个版本:后端工程骨架与 README §6 全局契约基础设施(MES-11,阶段 1)。后续所有功能模块都建在这套骨架与契约之上。

### Added

- **工程骨架**(docs/specs/README.md §2–§3):Python 3.12 + FastAPI + SQLAlchemy 2.x(async) + Alembic + PostgreSQL 16 + Redis;API / worker / realtime 网关三个可独立部署的进程入口,模块边界清晰,后续功能模块可直接挂载;配置 secrets 一律环境变量,启动校验必需项(fail-fast);`auth_mode` 默认 `production`(fail-safe)。
- **统一错误信封与分页包络(§6.14)**:`{"error":{"code","message","details"}}`(具名 snake_case code,500 脱敏不泄漏内部结构)+ 成功包络 `{"data":...}` / 列表 `{"data":[...],"next_cursor"}`(keyset 游标)。
- **事件词汇注册表(§6.7)**:96 个注册实时事件为基线,代码注册表与 README 注册表一致性由单测与 CI(`tests/docs/check_event_vocab.py`)强制,新事件必须先登记。
- **transactional outbox 与唯一写入路径(§6.6)**:业务事务同事务写 `outbox_events`;relay 以 `FOR UPDATE SKIP LOCKED` 抢占、逐事件 SAVEPOINT(毒事件不阻塞批次);realtime projector 是 `realtime_events` 的唯一写入者(`outbox_event_id` 去重、同事务分配频道内单调 seq);Redis 仅 fan-out,非持久真源。
- **多租户基础构件(§6.2)**:`UNIQUE(workspace_id,id)` + 复合 FK 迁移/ORM 模板、`realtime_channels`/`realtime_events` 租户键 + RLS 策略(`mesh.workspace_id` GUC)、全局表豁免清单(`users` / `external_identities`)。
- **realtime 网关骨架(§6.7/§6.16)**:WebSocket 首帧认证(token 不入 URL)、逐频道资源级授权钩子、`resume_from` 全量分页重放、游标过旧 `resync_required` + 对账 REST 端点;fan-out 故障显式下发错误并关闭连接(客户端凭 `resume_from` 重连重放)。
- **一键部署**:`docker compose up --build` 拉起 PostgreSQL 16 + Redis 7 + api + worker + gateway + 前端占位(nginx 反代 `/api`、`/ws`);健康检查 `/healthz`、`/readyz`;README Quick Start 可跑通。
- **CI 流水线**:`backend-ci` 三个 job——文档词汇/结构校验、单测 + 真实 e2e(真实服务进程/真实 API 调用/真实落库,pytest-cov ≥90% 门禁,ruff)、`schema_r2_validation.sql` 在 PostgreSQL 16 一次性实例实跑(100 条断言)。

### Quality

- 单测 + 真实 e2e 共 150 项全绿,pytest-cov 95.34%(≥90% 门禁,整体与新增代码双达标)。
- `schema_r2_validation.sql` 在 PostgreSQL 16 实跑:100 条断言全部 PASS、退出 0。
- 模型 ↔ 迁移漂移守卫测试(alembic `compare_metadata`),防止 ORM 与迁移后的 schema 静默漂移。
