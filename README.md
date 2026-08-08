<div align="center">

# Mesh

**出现在看板上的 AI 队友。**

Mesh 是一个开源的 AI 原生团队工作区:AI agent 不是侧边栏里的聊天机器人,而是与人类完全对称的一等队友——出现在成员名册里,被分派 issue、在看板上移动、在讨论里发评论、在沙箱里运行代码,最后把工作交回给你审查。自托管部署,Spec 驱动开发,人类监督全程在线。

[![CI](https://github.com/cnwenf/Mesh/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/cnwenf/Mesh/actions/workflows/backend-ci.yml)
[![Release](https://img.shields.io/github/v/release/cnwenf/Mesh?style=flat)](https://github.com/cnwenf/Mesh/releases)
[![GitHub stars](https://img.shields.io/github/stars/cnwenf/Mesh?style=flat)](https://github.com/cnwenf/Mesh/stargazers)

[产品 Spec](docs/specs/README.md) · [功能 Spec](docs/specs/features/) · [快速开始](#快速开始) · [mesh CLI](cli/README.md) · [mesh-runtime](daemon/README.md) · [贡献指南](CONTRIBUTING.md)

</div>

---

## 什么是 Mesh?

你可能已经在用好几个 AI 编码工具。但它们各自困在自己的终端会话里:上下文随会话消失,同一份背景你要解释第四遍,工具越多,你花在盯进度和搬运上下文上的时间越多。

Mesh 把这些 agent 和你的团队成员放进同一个工作区。agent 被分派一个 issue,就会自己领取任务、在你控制的运行时上工作、边做边评论,完成后把工作交回审查。意图、执行、决策和产出都挂在同一个 issue 上——没有人需要重建上下文,也没有任何东西能绕过人类审查直接上线。

---

## 组建你的团队。

*不是挑一个 AI 工具,而是把 agent 招进团队。*

- **[Agent 即一等成员](docs/specs/features/agent.md) →** 每个 agent 有自己的配置、指令与可见性,出现在成员名册里,和人类共享同一套分派、评论与通知机制。
- **[人机混编小队](docs/specs/features/squad.md) →** 把人和 agent 编进同一个 squad,leader 拆解任务、按依赖批次推进,计划可设人工审批闸门。
- **[技能包](docs/specs/features/skill.md) →** 把解决过的问题沉淀成结构化指令包,版本化、可安装,让每个 agent 复用同一份经验。
- **[你自己的运行时](docs/specs/features/runtime.md) →** agent 的工位是你的机器——`mesh-runtime` daemon 跑在你的笔记本或云服务器上,代码不出机器。

## 把工作派出去。

*开始时是 issue 里的三句话,结束时是一次待审查的产出。*

- **[分派即开工](docs/specs/features/issue.md) →** 把 issue 的 assignee 设为某个 agent,它自动领取任务、checkout 仓库、执行并回传进展。
- **[@ 提及即派活](docs/specs/features/comment-inbox.md) →** 在评论区 @ 一个 agent,等同于给它派一次活;它回复评论、补充上下文、推进任务。
- **[Autopilot 自动值守](docs/specs/features/autopilot.md) →** 定时(cron)或事件驱动地把工作派给合适的 agent,内置频率上限、去重与 kill switch 护栏。
- **[随时对话](docs/specs/features/chat-session.md) →** 与任意 agent 开聊天会话,流式输出,可携带 issue 上下文,也可以直接中断或重新生成。

## 进展看得见。

*谁在做?跑到哪了?花了多少?打开 issue 就知道。*

- **[看板与视图](docs/specs/features/kanban.md) →** issue 的实时投影:拖拽、筛选、泳道、WIP 限制,人与 agent 的工作一眼可辨。
- **[执行记录与日志流](docs/specs/features/runtime.md) →** 每一次运行都可追溯:任务领取、实时日志流、token 消耗与最终产物,全部回流到 issue。
- **[收件箱](docs/specs/features/comment-inbox.md) →** agent 需要你拍板时才通知你,而不是每一步都打扰你。
- **[统计报表](docs/specs/features/analytics.md) →** cycle time、吞吐量、burndown,还有按 agent 维度的运行统计。
- **[命令面板](docs/specs/features/search-command-palette.md) →** `Ctrl/Cmd+K` 全局搜索与快捷操作,一切资源都有规范深链。

## 自托管,可管控。

*你的基础设施,你的规则——关键节点永远有人类闸门。*

- **[整套自托管](#快速开始) →** 一份 Docker Compose 拉起全栈;凭据无默认值,数据存储不对公网暴露。
- **[多工作区与角色](docs/specs/features/workspace.md) →** 软多租户隔离,`owner/admin/member/guest` 角色与细粒度 RBAC。
- **[沙箱执行与护栏](docs/specs/features/runtime-executor.md) →** 真实 Linux namespace/cgroup 沙箱、出站网关、工具审批闸门与预算熔断,fail-closed 不降级。
- **[IM 与 Git 集成](docs/specs/features/integrations.md) →** 钉钉、飞书、Slack 消息渠道与 GitHub/GitLab 代码托管集成,在团队说话的地方跟进 agent 工作。
- **[主题、暗色与多语言](docs/specs/features/theme.md) →** `light/dark/system` 三态主题与 locale 协商,两套主题 WCAG AA 校准。
- **[CLI 与 API](docs/specs/features/cli.md) →** `mesh` 命令行与 `/api/v1` 全覆盖,每个界面都可以脚本化;agent 也经同一套接口工作。

---

## 快速开始

前置条件:Docker 与 Docker Compose。

```bash
./scripts/gen-dev-secrets.sh   # 首次:生成强随机密码的本地 .env(compose 不内置任何默认口令)
docker compose up --build -d
```

打开 http://localhost:3001 即可使用。`docker-compose.yml` 中**所有凭据都是必填项、无默认值**:缺失任一口令时 `docker compose up` 直接报错,而不是以弱口令启动;如需轮换,`./scripts/gen-dev-secrets.sh --force` 重新生成。

<details>
<summary><b>服务与端口</b></summary>

<br/>

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 前端 SPA | http://localhost:3001 | nginx 承载构建产物,**同源**反代 `/api` 与 `/ws`,无需 CORS |
| API | http://localhost:8000 | FastAPI REST(`/api/v1`),健康检查 `/healthz`、`/readyz` |
| Realtime 网关 | ws://localhost:8081/ws | WebSocket 实时事件与日志流 |
| MinIO 对象存储 | http://localhost:9000(控制台 :9001) | 附件私有桶,仅经短时效预签名 URL 访问 |
| PostgreSQL 16 / Redis 7 | 仅内部网络 | 数据与事件 fan-out,**不发布任何宿主端口** |

对外端口(3001 / 8000 / 8081)默认绑定 `127.0.0.1`,仅本机可达。

</details>

冒烟验证:

```bash
curl http://localhost:8000/healthz      # {"data":{"status":"ok"}}
curl http://localhost:8000/readyz       # 数据库与 Redis 就绪检查
curl http://localhost:3001/api/v1/ping  # 经 nginx 同源代理到 API
```

> **安全提示**:本 compose 栈**仅限本机开发**。`MESH_AUTH_MODE` 默认 `dev`(开发用宽松认证),仅在端口只绑回环时安全;任何非本机使用必须显式设置 `MESH_AUTH_MODE=production` 并提供真实强凭据。

## 生产部署

任何非本机部署(生产 / 预发 / 共享环境)必须满足:

1. **强唯一口令**:PostgreSQL、Redis、对象存储与三个生产签名密钥(`MESH_JWT_SECRET`、`MESH_DEVICE_CODE_PEPPER`、`MESH_SEARCH_CURSOR_SECRET`)均使用强随机、互不相同的值(如 `openssl rand -base64 32`),严禁可猜测默认值。
2. **不对公网暴露**:数据存储与中间件绝不发布到公网或宿主非回环网卡,仅经内网 / 服务网格可达。
3. **启用保护机制**:Redis 必须 `requirepass` + `protected-mode yes` 并绑定内网网卡;对外端点一律置于 TLS 之后。
4. **生产认证模式**:`MESH_AUTH_MODE=production`;后端在启动期校验凭据强度,基础设施口令缺失/过弱,或签名密钥缺失/仍是公开 dev 值,即 fail-closed 拒绝启动。
5. **部署前自检**:用 `ss -tlnp` 或云安全组核对,确认没有数据存储端口对公网开放。

---

## 五分钟迎来第一位 AI 队友

**1. 启动栈。** 按上面的[快速开始](#快速开始)拉起全栈,打开 http://localhost:3001。

**2. 创建工作区。** 跟随上手引导创建你的第一个工作区——它是团队、项目与 agent 的隔离边界。

**3. 接入运行时,创建 agent。** 在要执行代码的机器上安装并注册 [`mesh-runtime`](daemon/README.md),然后在成员页创建你的第一个 agent:选择 provider、写好指令,它就会以队友身份出现在名册里。

**4. 派一个 issue。** 新建 issue 并把 assignee 设为这个 agent——它会领取任务、在沙箱里执行、边做边评论,完成后把 issue 交回审查。也可以直接打开 `/chat` 和它对话。

---

## 运行时与 Provider

Mesh 服务端不绑定特定模型供应商,也不直接内置模型。agent 的实际执行发生在 **`mesh-runtime`**——一个独立的自托管 daemon:注册、心跳、领取任务、沙箱执行、脱敏日志回流,全部经版本化的 runtime 协议完成。

- **钉死版本的 provider 适配**:provider 二进制必须匹配随发布包携带的 capability manifest(SHA-256 + 版本 + 必需 flags),探测不符即拒绝领取任务;禁止 PATH 搜索与运行时自动下载。
- **首个官方 provider 为 Claude Code**;后续 provider 实现同一适配契约并通过同等级安全门禁即可接入。
- **沙箱与边界**:Linux namespace/cgroup 隔离、空白 HOME、唯一工具审批闸门(task broker)、出站网关与预算熔断;任务拿不到长期凭据。
- **机器令牌 `mesh_rt_`** 哈希存储,只属于 runtime 本身,不进任务沙箱。

安装、激活与故障排查见 [`daemon/README.md`](daemon/README.md),权威设计见 [runtime.md](docs/specs/features/runtime.md) 与 [runtime-executor.md](docs/specs/features/runtime-executor.md)。

---

## 文档

| 我想要…… | 从这里开始 |
| --- | --- |
| 了解整体设计与全局契约 | [产品 Spec](docs/specs/README.md) |
| 查某个功能的契约(数据模型 / 接口 / UI / 验收) | [功能 Spec 索引](docs/specs/features/) |
| 用命令行驱动 Mesh | [cli/README.md](cli/README.md) · [CLI Spec](docs/specs/features/cli.md) |
| 把自己的机器接入为运行时 | [daemon/README.md](daemon/README.md) · [runtime Spec](docs/specs/features/runtime.md) |
| 后端本地开发 | [backend/README.md](backend/README.md) |
| 前端本地开发 | [frontend/README.md](frontend/README.md) |
| 界面设计的精确基线 | [interface-design-baseline.md](docs/specs/frontend/interface-design-baseline.md) |
| 追溯某个模块的设计调研 | [docs/research/](docs/research/) |

**文档分工(硬性约定)**:本 README 只做**产品门面**——产品定位、特性、用法、架构、部署与贡献;项目进度、实现状态、逐 issue 的验收与审计记录**一律不进 README**。规格以 `docs/specs/` 为唯一实现依据;版本与变更记录见 [GitHub Releases](https://github.com/cnwenf/Mesh/releases)。完整规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 架构

```
            Web SPA(React 19 + TypeScript + Vite)
       看板 / Issue / 收件箱 / 成员 / Agent / 聊天 / 设置
                            │
            REST /api/v1    │    WebSocket /ws
    ┌───────────────────────┴────────────────────────┐
    │               API 层(FastAPI)                  │
    │     认证(Bearer/JWT)→ 成员资格 → RBAC → 限流    │
    ├────────────────────────────────────────────────┤
    │                   领域服务层                     │
    │  workspace/member · project/issue/kanban       │
    │  comment/inbox · agent 编排 · autopilot · 通知  │
    ├────────────────────────────────────────────────┤
    │  PostgreSQL 16+(主存储 + outbox + 任务队列)     │
    │  Redis(缓存 / 限流 / 事件 fan-out,非持久真源)   │
    │  S3 兼容对象存储(附件 / 日志段)                 │
    └───────────────────────┬────────────────────────┘
                            │ runtime 协议(机器令牌 mesh_rt_)
    ┌───────────────────────┴────────────────────────┐
    │      Agent Runtime(mesh-runtime,可自托管)      │
    │  领取任务 → checkout 仓库 → 沙箱执行 → 流式日志   │
    │  → 回传结果(钉死版本 provider 适配层)           │
    └────────────────────────────────────────────────┘
```

| 层 | 技术栈 |
| --- | --- |
| Web 前端 | React 19 + TypeScript + Vite SPA,nginx 同源承载 |
| 后端 | Python 3.12+(FastAPI + Pydantic v2 + SQLAlchemy 2.x + Alembic) |
| 数据库 | PostgreSQL 16+(主存储、outbox 与任务队列 `FOR UPDATE SKIP LOCKED`) |
| 缓存与实时 fan-out | Redis |
| 对象存储 | S3 兼容(本机栈为 MinIO) |
| 实时通道 | WebSocket(增量事件、日志流、聊天流式) |
| Agent 运行时 | `mesh-runtime` daemon(namespace/cgroup 沙箱 + provider 适配) |
| CLI | `mesh`(Python,与 Web 同源 `/api/v1` 的瘦客户端) |

后台由 outbox relay、调度 worker、租约 reaper、通知 fan-out、附件处理与 realtime projector 组成,均为无状态进程,可独立扩容;详见 [Spec §2.2](docs/specs/README.md)。

---

## 本地开发

贡献请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

**前置条件**:[Python](https://www.python.org/) 3.12+、[Node.js](https://nodejs.org/) 22.22+、[Docker](https://www.docker.com/)。

```bash
# 后端:单测 + 真实 e2e(需本地 PostgreSQL 16 与 Redis)
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.lock && pip install -e . --no-deps
pytest --cov=mesh --cov-report=term-missing

# 前端:契约 mock 门禁与生产构建预览
cd frontend
npm ci
npm run test:e2e

# 运行时 daemon
cd daemon
pip install -e .
pytest
```

后端工程结构见 [backend/README.md](backend/README.md),前端见 [frontend/README.md](frontend/README.md),daemon 见 [daemon/README.md](daemon/README.md)。

---

## 为什么叫 Mesh?

**Mesh,网格,交织。** 传统协作工具把「信息」组织进项目;Mesh 把「人和 agent」交织进同一张协作网——同一份成员名册、同一套分派与通知规则、同一块看板。当 agent 像同事一样被派活、被 @、被审查,团队的产能就不再受限于人数。
