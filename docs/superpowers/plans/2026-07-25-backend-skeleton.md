# Mesh 后端基础脚手架 + 全局契约基础设施 实施计划(MES-11,阶段1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 Mesh 后端工程骨架(Python + FastAPI + SQLAlchemy 2.x async + Alembic + PostgreSQL 16 + Redis)与 README §6 全局契约基础设施(错误信封/分页包络/事件词汇注册表/outbox+relay+realtime projector/多租户构件/realtime gateway 骨架),docker compose 一键可跑,UT 覆盖率 ≥90% 且真实 e2e 全绿。

**Architecture:** 按 README §2–§3 分层:API 层(FastAPI 工厂 + 错误/分页契约 + 健康检查)、领域服务层(outbox 发送服务、realtime 发布助手)、数据访问层(SQLAlchemy 声明式模型 + 租户基础设施 + Alembic 迁移)、独立进程入口(API / worker / realtime gateway 三个可独立部署单元)。业务事务只写 `outbox_events`;relay worker `FOR UPDATE SKIP LOCKED` 抢占分发;realtime projector 以 `outbox_event_id` 去重落 `realtime_events` 并在同事务分配频道 seq,经 Redis pub/sub 仅做 fan-out;gateway 从 `realtime_events` 重放 + `resync_required` 兜底。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / SQLAlchemy 2.x(async, asyncpg)/ Alembic / PostgreSQL 16 / Redis 7(redis-py asyncio)/ pytest + pytest-asyncio + pytest-cov / httpx + websockets(e2e)/ docker compose。

## Global Constraints

- 契约唯一权威为 `docs/specs/README.md` §6:错误信封严格为 `{"error": {"code": "<snake_case>", "message": "...", "details": {...}}}`(§6.14;leader issue 所写 `type` 字段不在 canonical 信封内,以 Spec 为准,完工评论说明);成功包络单对象 `{"data": {...}}`、列表 `{"data": [...], "next_cursor": <opaque|null>}`;游标为 base64 编码的 keyset `(sort_key, id)`。
- 实时事件名必须命中 §6.7 注册表(96 个,基线);代码注册表与 README 注册表一致性由单测强制(复用 `tests/docs/check_event_vocab.py` 的解析逻辑)。
- outbox → realtime 唯一写入路径(§6.6):业务事务只写 `outbox_events(event_type='realtime.publish')`;projector 同事务分配频道 seq;`realtime_events.UNIQUE(outbox_event_id)` 去重;禁止业务事务直接写 `realtime_events`。
- 多租户(§6.2):业务表 `UNIQUE(workspace_id, id)` + 复合 FK 迁移模板;`realtime_channels/realtime_events` 带 `workspace_id` 租户键 + RLS(`mesh.workspace_id` GUC);全局表豁免清单 = `{users, external_identities}`(§6.1/§6.2 第5条)。
- WebSocket 鉴权禁止 URL query 传 token(§6.16):连接建立后首帧认证;订阅逐频道资源级授权,以 `realtime_channels.workspace_id` 数据库层校验频道归属;`resume_from` 早于保留窗口 → `{"op":"resync_required","watermark":<当前最大 seq>,"rest":"<对账 REST URL>"}`。
- 部署形态(§2.2):API / worker(relay 等独立 asyncio 任务,看门狗 + 独立取消域)/ realtime gateway 三个独立入口;Redis 仅 fan-out,非持久真源;`realtime_events` 默认保留 7 天(可配)。
- 配置:secrets 一律环境变量,启动校验必需项(`DATABASE_URL`/`REDIS_URL` 等),缺失即快速失败并给出清晰错误。
- 提交规范:author/committer = `cnwenf <cnwenf@outlook.com>`;绝无 `Co-Authored-By`;conventional commits。
- 覆盖率:pytest-cov 实测整体与新增代码 ≥90%。
- 绝不暴露参考来源(代码/注释/文档/提交/分支)。

## 文件结构

```
Mesh/
├── .github/workflows/backend-ci.yml          # 后端 CI:doc 词汇校验 + unit/e2e(cov≥90)+ schema_r2_validation.sql(PG16)
├── docker-compose.yml                        # postgres16 + redis7 + api + worker + gateway + frontend 占位
├── .env.example                              # 环境变量样例(无真实 secret)
├── README.md                                 # 更新 Quick Start
├── frontend/placeholder/
│   ├── index.html                            # 前端占位页
│   └── nginx.conf                            # 静态页 + /api、/ws 反向代理
└── backend/
    ├── Dockerfile
    ├── pyproject.toml                        # 包定义 + pytest/coverage/ruff 配置
    ├── README.md                             # 后端分层说明 + 本地开发指南
    ├── alembic.ini
    ├── migrations/
    │   ├── env.py                            # async 引擎 + 从 mesh.db.base.metadata 生成
    │   ├── script.py.mako
    │   └── versions/0001_baseline.py         # workspaces / outbox_events / realtime_channels / realtime_events + 索引 + RLS
    ├── src/mesh/
    │   ├── config.py                         # Settings(pydantic-settings),启动校验
    │   ├── errors.py                         # MeshError 体系 + §6.14 错误码映射
    │   ├── api/
    │   │   ├── app.py                        # create_app 工厂(注册 handlers/routers/生命周期)
    │   │   ├── deps.py                       # get_session、current_principal(可插拔 Authenticator)
    │   │   ├── envelope.py                   # data/next_cursor 包络模型
    │   │   ├── pagination.py                 # keyset 游标编解码 + paginate()
    │   │   ├── error_handlers.py             # 异常 → §6.14 信封(500 不泄漏内部)
    │   │   ├── health.py                     # /healthz、/readyz
    │   │   └── realtime_routes.py            # GET /api/v1/realtime/events 对账端点
    │   ├── db/
    │   │   ├── base.py                       # DeclarativeBase + 命名约定
    │   │   ├── engine.py                     # async engine / session_factory
    │   │   ├── tenant.py                     # TenantMixin、复合 FK/UNIQUE 助手、RLS GUC、GLOBAL_TABLES
    │   │   └── models/{workspace,outbox,realtime}.py
    │   ├── events/vocab.py                   # EVENT_VOCABULARY(96)+ is_realtime_event()
    │   ├── outbox/
    │   │   ├── service.py                    # emit_event() / emit_realtime()(业务事务内调用)
    │   │   ├── relay.py                      # OutboxRelay:SKIP LOCKED 抢占 + handler 注册 + 重试/failed
    │   │   └── projector.py                  # realtime.publish handler:落库 + 分配 seq + pubsub 通知
    │   ├── realtime/
    │   │   ├── channels.py                   # 频道名解析/校验
    │   │   ├── auth.py                       # Principal + Authenticator/ChannelAuthorizer 协议 + dev 实现
    │   │   ├── pubsub.py                     # Redis fan-out(publish / subscriber)
    │   │   ├── session.py                    # 连接状态机:auth → subscribe → replay/resync → live
    │   │   ├── gateway.py                    # WebSocket 端点装配
    │   │   └── app.py                        # gateway FastAPI 工厂(独立入口)
    │   └── workers/
    │       ├── supervisor.py                 # 独立取消域 + 看门狗重启
    │       ├── retention.py                  # realtime_events 保留期清理循环
    │       └── main.py                       # worker 进程入口(python -m mesh.workers)
    └── tests/
        ├── conftest.py                       # PG 测试库 provision + alembic upgrade + 逐测试清理
        ├── unit/  (config/errors/pagination/vocab/tenant/outbox/relay/projector/auth/supervisor/channels)
        └── e2e/   (health/envelope/outbox-relay-崩溃恢复/realtime-订阅重放resync-跨租户拒绝/schema_validation 100 PASS)
```

---

## Task 1: 工程骨架与配置(config + db base/engine + pytest 骨架)

**Files:**
- Create: `backend/pyproject.toml`, `backend/src/mesh/__init__.py`, `backend/src/mesh/config.py`, `backend/src/mesh/db/base.py`, `backend/src/mesh/db/engine.py`, `backend/tests/conftest.py`(最小版),`backend/README.md`
- Test: `backend/tests/unit/test_config.py`, `backend/tests/unit/test_db_engine.py`

**Interfaces:**
- Produces: `Settings`(frozen pydantic-settings:`database_url: str`(必需)、`redis_url: str`(必需)、`auth_mode: Literal["dev","production"]="dev"`、`realtime_event_retention: timedelta=7d`、`outbox_batch_size=50`、`outbox_poll_interval: float=1.0`、`outbox_max_attempts=5`、`api_host/api_port/ws_port`);`load_settings(**overrides) -> Settings`(缺必需项 → `ConfigError` 列出缺失键);`Base`(DeclarativeBase,蛇形命名约定);`create_async_engine(settings)`、`create_session_factory(engine) -> async_sessionmaker`。

- [ ] Step 1: 写 `pyproject.toml`(setuptools src-layout;deps: fastapi, uvicorn[standard], sqlalchemy[asyncio]>=2.0, asyncpg, alembic, pydantic>=2.7, pydantic-settings, redis>=5;dev: pytest, pytest-asyncio, pytest-cov, httpx, websockets, ruff;`[tool.pytest.ini_options] asyncio_mode="auto" testpaths=["tests"]`;`[tool.coverage.run] source=["mesh"]`;`[tool.coverage.report] fail_under=90`)
- [ ] Step 2: 写失败测试 `test_config.py`:必需项缺失 → `ConfigError` 含缺失键名;全部提供 → 字段解析正确(redis_url/database_url 原样;retention 默认 7 天)。
- [ ] Step 3: 实现 `config.py`(`BaseSettings` + `model_config=SettingsConfigDict(env_prefix="MESH_", ...)`,`@classmethod load` 捕获 ValidationError → 抛 `ConfigError`)。
- [ ] Step 4: 写 `db/base.py`(naming convention: `ck_%(table_name)s_%(constraint_name)s` 等)+ `db/engine.py`(`create_async_engine`、`create_session_factory`)。
- [ ] Step 5: 写 `tests/conftest.py` 最小版:`DATABASE_URL` 取自环境(默认 `postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test`);session 级 fixture 建库(连 `postgres` 库 `CREATE DATABASE`)、跑 alembic `upgrade head`(Task 2 之后生效,先占位 try/except 跳过);function 级 `db_session` fixture(事务 + TRUNCATE 清理)。
- [ ] Step 6: 跑 `cd backend && pip install -e ".[dev]" && pytest tests/unit -q` → PASS。
- [ ] Step 7: Commit `feat(backend): project skeleton with validated settings and db engine`。

## Task 2: Alembic 基线迁移(workspaces / outbox / realtime + RLS)

**Files:**
- Create: `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`, `backend/migrations/versions/0001_baseline.py`
- Create: `backend/src/mesh/db/models/workspace.py`, `outbox.py`, `realtime.py`, `models/__init__.py`
- Test: `backend/tests/unit/test_models_schema.py`(模型元数据与迁移 DDL 一致性要点:表/约束存在)

**Interfaces:**
- Produces: 模型 `Workspace`(DDL 与 validation SQL 126–140 行一致:settings JSONB default `{"default_locale":"en"}`、`inbox_issue_seq`、partial unique slug)、`OutboxEvent`(§6.6 DDL 逐字一致:`UNIQUE(idempotency_key)`、status CHECK、`idx_outbox_pending` 部分索引)、`RealtimeChannel`(`PK(channel)` + `UNIQUE(workspace_id,channel)` + workspaces CASCADE)、`RealtimeEvent`(`BIGINT GENERATED ALWAYS AS IDENTITY`、`UNIQUE(channel,seq)`、`UNIQUE(outbox_event_id)`、复合 FK `(workspace_id,channel)` CASCADE、`idx_realtime_events_replay`、`idx_realtime_events_ws_created`);迁移含 `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + 两条 `mesh_rt_*_tenant` 策略(`current_setting('mesh.workspace_id')::uuid`)。
- 迁移以 raw DDL(`op.execute`)书写核心约束,保证与 `docs/specs/validation/schema_r2_validation.sql` 1459–1501 行逐字对齐;SQLAlchemy 模型用 `__table_args__` 表达同等约束供 ORM 使用。

- [ ] Step 1: 写 `alembic.ini` + async `env.py`(`run_async` → `run_sync`,target_metadata=`Base.metadata`)。
- [ ] Step 2: 写三个模型文件 + `models/__init__.py`(导出全部模型)。
- [ ] Step 3: 手写 `0001_baseline.py`(upgrade: 建表/索引/RLS;downgrade: drop)。
- [ ] Step 4: 起本地 PG16 容器(`docker run -d --name mesh-pg-dev -e POSTGRES_USER=mesh -e POSTGRES_PASSWORD=mesh -e POSTGRES_DB=mesh -p 5432:5432 postgres:16`)与 Redis(`docker run -d --name mesh-redis-dev -p 6379:6379 redis:7`)。
- [ ] Step 5: `cd backend && alembic upgrade head` → 成功;`\d outbox_events` 核对约束。
- [ ] Step 6: 写 `test_models_schema.py`(反射 information_schema:三表 + 唯一键 + RLS 启用 + 策略存在)并跑通。
- [ ] Step 7: Commit `feat(backend): baseline migration — workspaces, outbox, realtime tables with RLS`。

## Task 3: 错误信封与 API 应用工厂(§6.14)

**Files:**
- Create: `backend/src/mesh/errors.py`, `backend/src/mesh/api/error_handlers.py`, `backend/src/mesh/api/app.py`, `backend/src/mesh/api/envelope.py`
- Test: `backend/tests/unit/test_errors.py`, `backend/tests/e2e/test_envelope_errors_e2e.py`(经真实 HTTP)

**Interfaces:**
- Produces: `MeshError(Exception)`(`code: str`、`status_code: int`、`message: str`、`details: dict|None`、可选 `headers`);具名子类覆盖 §6.14 全表:`ValidationError(400)`、`UnauthorizedError(401)`、`ForbiddenError(403)`、`NotFoundError(404)`、`ConflictError(409)`、`GoneError(410)`、`PayloadTooLargeError(413)`、`UnsupportedMediaTypeError(415)`、`BusinessRuleError(422)`(具名 code)、`LockedError(423)`、`RateLimitedError(429, retry_after)`、`StorageError(502)`;`install_error_handlers(app)` 将 `MeshError`/`RequestValidationError`/未知异常统一渲染为 `{"error": {...}}`(500 固定 `internal_error` + 中性 message,detail 不外泄);`DataEnvelope[T]`(`data: T`)、`ListEnvelope[T]`(`data: list[T]`、`next_cursor: str|None`)。
- API 暴露一个骨架端点 `GET /api/v1/ping` 返回 `{"data":{"pong":true}}`(用于包络冒烟;供 401/403/404/422/500 演练的 `/_debug/error?code=...` 仅在 `auth_mode=dev` 挂载)。

- [ ] Step 1: 写失败测试 `test_errors.py`:每个错误类 → 信封 dict 精确匹配 §6.14(`code` snake_case、`message`、`details`);`RateLimitedError` 带 `Retry-After` 头;500 渲染不泄漏异常字符串。
- [ ] Step 2: 实现 `errors.py` + `error_handlers.py`(FastAPI exception_handlers + RequestValidationError → `validation_error` 400 + 字段 details)。
- [ ] Step 3: 实现 `envelope.py` + `app.py`(`create_app(settings=None)`,挂载 ping 与 dev 错误演练路由)。
- [ ] Step 4: 单测通过。
- [ ] Step 5: 写 e2e(`test_envelope_errors_e2e.py`):真实 uvicorn 子进程(复用 Task 7 的 server fixture,先以 TestClient 级 httpx.ASGITransport 起真 app + 真 DB;完整子进程版在 Task 7 统一)→ 断言 400/404/422/500 信封。
- [ ] Step 6: Commit `feat(api): unified error envelope and success envelopes per §6.14`。

## Task 4: 游标分页(§6.14 keyset)

**Files:**
- Create: `backend/src/mesh/api/pagination.py`
- Test: `backend/tests/unit/test_pagination.py`

**Interfaces:**
- Produces: `encode_cursor(sort_value, id) -> str`(base64url JSON `{"s": <iso/num>, "i": <uuid-str>}`)、`decode_cursor(str) -> CursorPosition`(非法/篡改 → `ValidationError("invalid_cursor", 400)`)、`async def paginate(session, stmt, *, order_columns, cursor, limit) -> Page`(Page: `items: Sequence[Row]`、`next_cursor: str|None`;取 limit+1 判定末页;`next_cursor=null` 表示末页)。

- [ ] Step 1: 失败测试:编解码往返;篡改 base64 → `invalid_cursor`;limit+1 末页判定(用真实 DB 的 `realtime_events` 或 `workspaces` 小数据集)。
- [ ] Step 2: 实现 → PASS。
- [ ] Step 3: Commit `feat(api): keyset cursor pagination with opaque base64 cursors`。

## Task 5: 事件词汇注册表(§6.7,96 事件)

**Files:**
- Create: `backend/src/mesh/events/vocab.py`
- Test: `backend/tests/unit/test_vocab.py`

**Interfaces:**
- Produces: `EVENT_VOCABULARY: frozenset[str]`(96 个,按 §6.7 表逐域常量拼接:`WORKSPACE_EVENTS`…`CHAT_STREAM_EVENTS`,含流内帧 `error`/`ping`)、`OUTBOX_INTERNAL_EVENT_TYPES: frozenset`(与 check_event_vocab.py 白名单同源:`issue.assigned` 等领域事件)与 `is_realtime_event(name) -> bool`、`require_registered(name)`(未登记 → `UnregisteredEventError`)。

- [ ] Step 1: 失败测试:① 词汇总数 == 96;② 复用 `tests/docs/check_event_vocab.py::parse_registry` 解析 README §6.7 → 与 `EVENT_VOCABULARY` 完全相等(双向 set 比较);③ `require_registered("agent.run_started")` 抛错(词汇漂移零容忍)。
- [ ] Step 2: 实现 `vocab.py` → PASS。
- [ ] Step 3: Commit `feat(events): canonical realtime event vocabulary registry (96 events, §6.7)`。

## Task 6: 多租户基础构件(§6.2)

**Files:**
- Create: `backend/src/mesh/db/tenant.py`
- Modify: `backend/migrations/versions/0001_baseline.py`(如需补 RLS 辅助函数)
- Test: `backend/tests/unit/test_tenant.py`, `backend/tests/e2e/test_tenant_rls_e2e.py`

**Interfaces:**
- Produces: `GLOBAL_TABLES = frozenset({"users", "external_identities"})`(豁免清单 + 注释引 §6.1/§6.2-5);`class TenantMixin`(声明 `workspace_id: Mapped[uuid]` NOT NULL,`declares (workspace_id,id)` 复合唯一约束助手 `tenant_unique_constraint()`);`composite_fk_columns(ref_table, ref_column)` 生成 `(workspace_id, <ref>_id) → ref(workspace_id, id)` 的 `ForeignKeyConstraint`(迁移模板助手,供后续模块用);`async def set_tenant_context(conn, workspace_id)`(`set_config('mesh.workspace_id', ..., local=True)`);`tenant_scope(stmt, workspace_id)` 为任意 select 追加 `workspace_id =` 过滤(查询约定助手)。

- [ ] Step 1: 失败测试(真实 DB):① 设置 GUC 后以非 superuser 角色(迁移创建 `mesh_app` 只读角色并 GRANT)查询 `realtime_events` 仅见本租户行(RLS 生效);未设 GUC → 0 行;② `tenant_scope` 生成的 SQL 含过滤;③ GLOBAL_TABLES 内容断言;④ 复合 FK 助手生成的约束 DDL 字符串断言(迁移模板)。
- [ ] Step 2: 实现 → PASS;补迁移(`CREATE ROLE mesh_app NOLOGIN`(IF NOT EXISTS 包裹)+ GRANT)。
- [ ] Step 3: Commit `feat(db): multi-tenant infrastructure — RLS GUC, composite FK templates, global-table exemption list`。

## Task 7: e2e 基础设施(真实服务夹具)+ 健康检查

**Files:**
- Create: `backend/src/mesh/api/health.py`, `backend/src/mesh/api/deps.py`
- Create: `backend/tests/e2e/conftest.py`(真实 uvicorn 子进程 + httpx + redis 隔离 db)
- Test: `backend/tests/e2e/test_health_e2e.py`

**Interfaces:**
- Produces: `/healthz`(liveness,200 `{"data":{"status":"ok"}}`)、`/readyz`(DB `SELECT 1` + Redis `PING`,任一失败 503 `storage_error`/`service_unavailable`);e2e fixture:`api_server`(uvicorn 子进程指向测试库/测试 redis db,轮询 `/healthz` 就绪,teardown 杀进程)、`api_client`(httpx.AsyncClient base_url)、`workspace_factory`(直接落库建测试 workspace)。

- [ ] Step 1: 实现 `health.py` + `deps.py`(`get_session` 依赖;`current_principal` 占位——Task 9 接入 dev 认证)。
- [ ] Step 2: 写 e2e conftest(server fixture:`sys.executable -m uvicorn mesh.api.app:create_app --factory ...` 子进程,env 指向测试资源;就绪轮询 10s)。
- [ ] Step 3: `test_health_e2e.py`:`/healthz` 200;停 redis 不可行则断言 readyz 正常路径 + DB 断开模拟用错误 URL 起第二个短命进程断言 503。
- [ ] Step 4: Commit `feat(api): health endpoints and real-service e2e harness`。

## Task 8: outbox 服务 + relay worker(§6.6)

**Files:**
- Create: `backend/src/mesh/outbox/service.py`, `backend/src/mesh/outbox/relay.py`
- Test: `backend/tests/unit/test_outbox_service.py`, `backend/tests/unit/test_relay.py`, `backend/tests/e2e/test_outbox_relay_e2e.py`

**Interfaces:**
- Produces: `async def emit_event(session, *, workspace_id, event_type, payload, idempotency_key=None) -> OutboxEvent`(业务事务内同事务 INSERT;同键重复 → 返回既有行,不抛错);`Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]`;`OutboxRelay(session_factory, *, handlers: Mapping[str, Handler], batch_size, max_attempts, poll_interval, clock)`:`claim_batch()`(`SELECT ... WHERE status='pending' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT n`)、`dispatch_one()`(handler 成功 → `published`+`published_at`;抛错 → `delivery_attempts+1`,达 `max_attempts` → `failed`;未注册 event_type 视同失败路径)、`run_once() -> int`(处理行数)、`run_forever()`(循环 + 退避 sleep)。

- [ ] Step 1: 单测(service):同事务插入可见;`idempotency_key` 重复返回既有行(不新增);(relay,真 DB):两个 relay 并发 claim 同一批 → 每行恰被一个 relay 处理(SKIP LOCKED);handler 抛错 → attempts 递增 → `failed`;未注册类型 → failed。
- [ ] Step 2: 实现 → PASS。
- [ ] Step 3: e2e(§9 T5 形态):业务事务写 outbox → 杀 relay(不启动 relay 进程)→ 启动 relay `run_once` → 事件被分发(handler 落一条可断言的 DB 记录)、outbox `published`、无丢失。
- [ ] Step 4: Commit `feat(outbox): transactional outbox service and SKIP LOCKED relay worker`。

## Task 9: realtime projector + Redis fan-out(§6.6/§6.7 唯一写入路径)

**Files:**
- Create: `backend/src/mesh/outbox/projector.py`, `backend/src/mesh/realtime/pubsub.py`, `backend/src/mesh/realtime/channels.py`
- Modify: Task 8 relay(注册 `realtime.publish` handler)
- Test: `backend/tests/unit/test_projector.py`, `backend/tests/unit/test_channels.py`, `backend/tests/e2e/test_projector_e2e.py`

**Interfaces:**
- Produces: `async def project_realtime_event(session, outbox_event)`(同事务:校验 payload 含 `channel/event/data` → `event` 命中词汇表(否则 `failed` 并记录)→ `INSERT realtime_channels ... ON CONFLICT DO NOTHING` → `UPDATE realtime_channels SET last_seq=last_seq+1 RETURNING` → `INSERT realtime_events`(UniqueViolation(outbox_event_id) → 幂等视为成功)→ 返回待发布帧);`publish_frames(frames)`(commit 后 Redis `PUBLISH mesh:rt:<channel>`);`RedisPubSub.publish(channel, frame)` / `subscribe(pattern)`;`channels.py`:频道名语法校验 + `parse_channel(name) -> (entity, entity_id)`。
- `emit_realtime(session, *, workspace_id, channel, event, data)` 助手(写 outbox `realtime.publish`;未登记事件名 → 抛错,业务侧前置拦截)。

- [ ] Step 1: 单测:词汇表外事件 → outbox `failed`(不污染 realtime_events);正常路径 → `realtime_events` 落库且 `seq` 频道内单调(连发 3 条 → 1/2/3);重复 outbox_event_id 直接构造冲突 → 幂等不重复、不产生 seq 缺口;频道自动注册带正确 `workspace_id`;跨租户复合 FK 拒绝(事件 workspace 与频道 workspace 不一致 → 约束拒绝,§9 T26-②)。
- [ ] Step 2: 实现 → PASS。
- [ ] Step 3: e2e(§9 T26-① 形态):写 outbox → projector 处理前杀进程(不起 relay)→ 重启 relay 一轮 → 事件已登记、`seq` 无缺口无重复;Redis 收到 fan-out 帧(真实 redis 订阅断言)。
- [ ] Step 4: Commit `feat(realtime): projector — outbox-only write path with per-channel seq and Redis fan-out`。

## Task 10: realtime gateway 骨架(WS 首帧认证 / 订阅授权 / resume_from / resync_required)

**Files:**
- Create: `backend/src/mesh/realtime/auth.py`, `backend/src/mesh/realtime/session.py`, `backend/src/mesh/realtime/gateway.py`, `backend/src/mesh/realtime/app.py`
- Create: `backend/src/mesh/api/realtime_routes.py`(对账 REST:`GET /api/v1/realtime/events?channel=&since=&limit=`)
- Test: `backend/tests/unit/test_realtime_auth.py`, `backend/tests/unit/test_gateway_session.py`, `backend/tests/e2e/test_realtime_gateway_e2e.py`

**Interfaces:**
- Produces: `Principal(subject: str, workspace_ids: frozenset[UUID])`;`Authenticator(Protocol)`(`async authenticate(token) -> Principal | None`)+ `DevTokenAuthenticator`(仅 `auth_mode=dev`;token 形如 `mesh-dev:<workspace_id>` → 该工作区 principal;production 模式 → 一律 None);`ChannelAuthorizer(Protocol)`(`async authorize(principal, channel) -> bool`)+ `DefaultChannelAuthorizer`(DB 校验 `realtime_channels.workspace_id ∈ principal.workspace_ids`;前缀扩展钩子注册表 `register_prefix_checker(prefix, fn)` 供后续模块挂资源级检查);WS 协议帧(服务端):`auth_ok`/`error`/`subscribed`/`event`/`resync_required`/`ping`;(客户端):`auth`/`subscribe`/`unsubscribe`/`ping`;`resync_required` 帧 `{"op":"resync_required","channel","watermark":<last_seq>,"rest":"/api/v1/realtime/events?channel=<ch>&since=<resume_from>"}`;触发条件:`resume_from < min(available seq)`(频道无事件可重放且 `resume_from <= last_seq - 保留窗口` 语义:有已清理事件或 `resume_from < min_seq`)。
- gateway 装配:首帧必须为 `auth`(否则 `error unauthorized` 关闭);订阅时逐频道授权(拒绝 → `error forbidden`,连接保持);重放 `seq >= resume_from` 顺序下发后 `subscribed(last_seq)`;live 帧经 Redis 订阅 `mesh:rt:*` 分发到本连接已订阅频道;30s `ping` 心跳;对账 REST 端点复用同一 authorizer,返回 `ListEnvelope`。

- [ ] Step 1: 单测(auth):dev token 解析;production 模式拒绝;`DefaultChannelAuthorizer`:频道属 A 区,principal 仅 B 区 → False;属 A 区且 principal 含 A → True;前缀钩子被调用并可否决。
- [ ] Step 2: 单测(session 状态机,以 fake WebSocket 通道驱动):未认证 subscribe → error;已认证订阅 → 重放历史(造 3 条 realtime_events,resume_from=2 → 收 seq 2、3 + subscribed last_seq=3);`resume_from` 过旧(清理后 min_seq 大于 resume_from)→ `resync_required` + watermark=last_seq + rest URL。
- [ ] Step 3: 实现 gateway/app/对账路由 → 单测 PASS。
- [ ] Step 4: e2e(真实 gateway uvicorn 子进程 + websockets 客户端,§9 T6 形态):认证 → 订阅 → 业务经 API/直接 outbox + relay 产生实时事件 → WS 收到 `event`;持过旧游标重连 → `resync_required` → 走对账 REST 拉齐;跨租户订阅(持 B 区 token 订阅 A 区频道)→ `error forbidden`(T26-②)。
- [ ] Step 5: Commit `feat(realtime): websocket gateway — first-frame auth, per-channel authorization, resume_from replay, resync_required`。

## Task 11: worker 进程入口(独立取消域 + 看门狗 + 保留期清理)

**Files:**
- Create: `backend/src/mesh/workers/supervisor.py`, `backend/src/mesh/workers/retention.py`, `backend/src/mesh/workers/main.py`
- Test: `backend/tests/unit/test_supervisor.py`, `backend/tests/unit/test_retention.py`

**Interfaces:**
- Produces: `Supervisor(tasks: Sequence[TaskSpec])`(`TaskSpec(name, factory)`;每任务独立 `asyncio.Task` + 崩溃指数退避重启(1s→30s)+ 结构化日志;`run()` 直到收到停止信号;单任务死循环/崩溃不影响其他任务——单测以"崩溃任务 + 健康任务"断言健康任务继续推进);`retention_purge(session_factory, retention, *, now) -> int`(按 `(workspace_id, created_at)` 删除过期 `realtime_events`)+ `retention_loop`;`main.py`:`python -m mesh.workers` 装配 relay(含 projector handler)+ retention_loop,`ConfigError` → 非零退出。

- [ ] Step 1: 失败测试:崩溃任务重启且健康任务计数继续增长;`retention_purge` 只删过期行(真 DB,造新旧两批)。
- [ ] Step 2: 实现 → PASS。
- [ ] Step 3: Commit `feat(workers): supervisor with isolated cancel domains, relay entrypoint and retention purge`。

## Task 12: 部署(docker-compose + Dockerfile + frontend 占位 + README Quick Start + .env.example)

**Files:**
- Create: `backend/Dockerfile`, `docker-compose.yml`, `frontend/placeholder/index.html`, `frontend/placeholder/nginx.conf`, `.env.example`
- Modify: `README.md`(Quick Start)

**Interfaces:**
- Produces: compose 服务 `postgres`(16, healthcheck pg_isready)、`redis`(7, healthcheck)、`api`(build backend;`alembic upgrade head && uvicorn ... --port 8000`;8000)、`worker`(`python -m mesh.workers`)、`gateway`(uvicorn realtime app 8081;8081)、`frontend`(nginx:alpine,3000;静态占位页 + `/api`→api:8000、`/ws`→gateway:8081 代理);`Dockerfile`(python:3.12-slim,`pip install .`,非 root 用户)。

- [ ] Step 1: 写 Dockerfile/compose/占位页/nginx.conf/.env.example(无真实 secret;密码仅本地 dev 值并注明)。
- [ ] Step 2: 更新 `README.md`:Quick Start(`docker compose up --build` → `curl localhost:8000/healthz` → `curl localhost:3000` 见占位页 → WS `ws://localhost:8081/ws`)+ 本地开发/测试指引。
- [ ] Step 3: `docker compose up --build -d` → 逐个 curl 断言(healthz/readyz/ping/占位页/经 nginx 代理的 /api/v1/ping);`docker compose down -v`。
- [ ] Step 4: Commit `feat(deploy): docker compose stack — postgres, redis, api, worker, gateway, frontend placeholder`。

## Task 13: CI(backend-ci.yml)+ schema_r2_validation.sql 实跑 100 PASS

**Files:**
- Create: `.github/workflows/backend-ci.yml`
- Test: `backend/tests/e2e/test_schema_validation.py`(本地以 psycopg 在一次性库实跑验证脚本,断言无异常 + `PASS` NOTICE == 100)

**Interfaces:**
- Produces: workflow jobs:① `docs-checks`(python3.12,跑 `tests/docs/check_event_vocab.py` 与 `tests/docs/check_roster_entry.py`)② `test`(services postgres:16 + redis:7;`pip install -e backend/[dev]`;`pytest backend/tests --cov=mesh --cov-report=term-missing --cov-fail-under=90`)③ `schema-validation`(services postgres:16;`psql -v ON_ERROR_STOP=1 -f docs/specs/validation/schema_r2_validation.sql`,与既有 spec-checks 同构,保证后端 PR 必跑);触发:push/PR 命中 `backend/**`、`.github/workflows/backend-ci.yml`、`docs/specs/validation/**`。

- [ ] Step 1: 写 `test_schema_validation.py`(建一次性库 `mesh_validation_<pid>`、psycopg 执行整份 SQL、收集 notices、断言 100 条 PASS 且零 EXCEPTION)并本地跑通。
- [ ] Step 2: 写 workflow;`git push` 后在 PR 上确认三个 job 全绿(gh 轮询;CI 为外部系统,绿否在完工评论如实报告)。
- [ ] Step 3: Commit `ci: backend pipeline — unit/e2e with coverage gate, event vocab, schema validation on PG16`。

## Task 14: 覆盖率收口 + 代码评审 + PR + 完工评论

- [ ] Step 1: `pytest --cov=mesh --cov-report=term-missing` 全绿且 ≥90%;缺口补测试(分支/异常路径)。
- [ ] Step 2: 全仓扫描参考来源痕迹(品牌词/URL)清零;`git log --format=%B` 无 co-author;`git log --format='%an <%ae> | %cn <%ce>'` 均为 `cnwenf <cnwenf@outlook.com>`。
- [ ] Step 3: 用 requesting-code-review / code-reviewer agent 评审,修 CRITICAL/HIGH。
- [ ] Step 4: `gh pr create` 合入 main;完工评论(覆盖率报告 + e2e 结果 + schema 100 PASS 证据 + PR 链接),状态置 `in_review`。

## Self-Review

1. **Spec 覆盖**:分层骨架(§2–§3)→ T1/T2/T7;错误信封/分页(§6.14)→ T3/T4;事件词汇(§6.7)→ T5(+既有 CI);outbox/relay/projector(§6.6/§6.7)→ T8/T9;多租户构件(§6.2)→ T2(RLS)/T6;realtime gateway(resume_from/resync_required/授权钩子,§6.7/§6.16)→ T10;worker 独立取消域/保留期(§2.2)→ T11;部署/健康检查/Quick Start → T7/T12;schema_r2_validation 100 PASS in CI → T13;覆盖率 ≥90 → T14;词汇 CI 常跑 → T13。
2. **占位扫描**:无 TBD;各 task 含具体文件/接口/命令。
3. **类型一致**:`emit_event`/`OutboxRelay`/`project_realtime_event`/`Principal`/`Authenticator`/`ChannelAuthorizer` 签名跨 task 一致。

**执行方式**:内联执行(executing-plans)——各 task 顺序 TDD、频繁提交;本运行环境不支持"背景派发后等待",不采用 subagent 逐任务派发。
