# Mesh

Mesh 是一个 **AI 原生的团队工作区**:AI agent 被当作真正的队友——像人类成员一样在看板上被分派 issue、在讨论里发评论、修改状态、运行代码,与人类协作完成目标。

## 文档导航

| 文档 | 说明 |
| --- | --- |
| [docs/specs/README.md](docs/specs/README.md) | **整体项目 Spec**:产品定位、整体架构、技术栈、模块总览,并索引全部功能 Spec |
| [docs/specs/features/](docs/specs/features/) | 每个功能一份 Spec:功能描述、数据模型、接口设计、UI/UX、验收标准 |
| [backend/README.md](backend/README.md) | 后端工程骨架与全局契约基础设施(分层、outbox/realtime 唯一写入路径、多租户构件) |
| [docs/research/](docs/research/) | 各模块的设计调研记录(功能 / 数据模型 / 接口 / UI / UX 四维度) |

## 技术栈

- **后端**:Python(FastAPI + SQLAlchemy 2.x + Alembic + PostgreSQL 16 + Redis),实时通信走 WebSocket
- **前端**:React 18 + TypeScript + Vite 单页应用,契约层 / 实时客户端 / 设计系统 / i18n 基线见 [frontend/](frontend/)(选型理由、Quick Start、目录结构见 [frontend/README.md](frontend/README.md))
- **Agent 运行时**:可自托管的执行环境,负责任务领取、代码执行与日志回传

## 仓库结构

| 目录 | 说明 |
| --- | --- |
| [docs/specs/](docs/specs/) | Spec 文档(所有实现的唯一依据) |
| [backend/](backend/) | Python 后端(FastAPI REST + realtime 网关 + outbox/projector worker) |
| [frontend/](frontend/) | Web 前端(阶段 1·B 骨架:工程脚手架、API/实时契约层、设计系统与体验基线、i18n 基线) |
| [tests/](tests/) | 文档级校验脚本(事件词汇、名册入口等) |

开发任何功能前,请先阅读对应的功能 Spec;Spec 是本仓库所有实现的唯一依据。

## 实现状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 工程骨架与全局契约(§6) | ✅ v0.1.0 | 错误信封/分页包络、事件词汇注册表、outbox → realtime 唯一写入路径、多租户构件、realtime 网关骨架 |
| auth 认证核心(auth.md 增量 1) | ✅ v0.2.0 | 注册/登录/会话/MFA/一次性令牌/账号偏好 + 应用路径 RLS 加固 |
| workspace 工作区与多租户基础(workspace.md) | ✅ v0.4.0 | 工作区 CRUD 与 slug 重定向、邀请全生命周期与兑换分离、RBAC 裁决与审计、前缀注册表、租户表 fail-closed RLS;前端设置/邀请页面待前端脚手架增量接通 |

## Quick Start

前置条件:Docker + Docker Compose。

```bash
docker compose up --build -d
```

服务与端口(可用 `.env` 覆盖,见 [.env.example](.env.example)):

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 前端占位页 | http://localhost:3001 | nginx 静态页,反代 `/api` 与 `/ws` |
| API | http://localhost:8000 | FastAPI REST(`/api/v1`),健康检查 `/healthz`、`/readyz` |
| Realtime 网关 | ws://localhost:8081/ws | WebSocket(连接后首帧认证,详见 docs/specs/README.md §6.16) |
| PostgreSQL 16 / Redis 7 | 内部网络 | 数据与 fan-out(Redis 非持久真源) |

> **安全提示(务必阅读)**:本 compose 栈**仅限本机开发**。
>
> - 对外端口(8000 / 8081 / 3001)默认绑定 `127.0.0.1`,**仅本机可达**,不暴露到网络;`.env` 只能改端口号,无法改绑定地址——如需对外暴露必须刻意修改 `docker-compose.yml`。
> - `MESH_AUTH_MODE` 默认 `dev`(任意 `mesh-dev:<workspace-id>` 即获该工作区完全访问),这**仅在端口只绑回环时安全**。任何**非本机/生产**使用必须显式设置 `MESH_AUTH_MODE=production`,并提供真实的数据库/Redis 凭据。
> - API 与 realtime 网关以**受限非 owner 角色 `mesh_app`** 连接数据库,使 PostgreSQL RLS 租户兜底在应用连接路径真实生效(§6.2 第 5 条);worker 保留 owner 角色做跨租户 relay/projector/retention。

冒烟验证:

```bash
curl http://localhost:8000/healthz          # {"data":{"status":"ok"}}
curl http://localhost:8000/readyz           # {"data":{"status":"ready","checks":{"database":"ok","redis":"ok"}}}
curl http://localhost:8000/api/v1/ping      # {"data":{"pong":true}}
curl http://localhost:3001/                 # 前端占位页
curl http://localhost:3001/api/v1/ping      # 经 nginx 代理到 API
```

API 启动时自动执行 `alembic upgrade head`(建表 + RLS 策略);worker 进程运行 outbox relay、realtime projector 与保留期清理。

本地开发(不依赖 compose)见 [backend/README.md](backend/README.md);测试:

```bash
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest --cov=mesh --cov-report=term-missing   # 单测 + 真实 e2e(需本地 PostgreSQL 16 与 Redis)
```
