# Changelog

Mesh 项目的所有重要变更都记录于此文件。
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
