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
- **Agent 运行时**:可自托管的执行环境,负责任务领取、代码执行与日志回传

开发任何功能前,请先阅读对应的功能 Spec;Spec 是本仓库所有实现的唯一依据。

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
