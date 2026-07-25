# Changelog

Mesh 项目的所有重要变更都记录于此文件。
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
