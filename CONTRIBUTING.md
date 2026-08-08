# 贡献指南

感谢你考虑为 Mesh 做贡献。Mesh 是 Spec 驱动的项目:**先有 `docs/specs/`,后有代码**。开始动工前,请先读 [docs/specs/README.md](docs/specs/README.md) 建立全局认知,再进入对应的功能 Spec。

## 开发流程

1. **对齐 Spec**:功能行为、数据模型、接口设计、UI/UX 都以 `docs/specs/` 为准。发现 Spec 缺漏或与实现冲突,先提 issue 讨论,不要擅自改需求。
2. **测试先行**:先写测试,再写实现。单元测试覆盖率门禁 **≥ 90%**(整体与新增代码均须达标)。
3. **真实端到端验证**:后端接口要真实启动服务、发起真实调用、校验响应与落库;前端要真实打开页面、点击、填表、验证交互,不做 mock 走过场。
4. **提交与 PR**:提交信息遵循 Conventional Commits(`feat` / `fix` / `docs` / `test` / `chore` / `perf` / `ci`);PR 描述写清动机、改动点与测试计划。

## 本地开发

前置条件:Python 3.12+、Node.js 22.22+、Docker。

```bash
# 一键拉起全栈(前端 :3001 / API :8000 / WS :8081)
./scripts/gen-dev-secrets.sh
docker compose up --build -d
```

分工程开发见 [README 的本地开发一节](README.md#本地开发) 与各子目录 README([backend](backend/README.md) / [frontend](frontend/README.md) / [daemon](daemon/README.md) / [cli](cli/README.md))。

## 文档规范(硬性约定)

仓库文档分三层,各司其职:

| 层 | 位置 | 定位 |
| --- | --- | --- |
| 产品门面 | 根 `README.md` | 产品定位、特性、用法、架构、部署、贡献 |
| 实现依据 | `docs/specs/` | 所有实现的唯一依据;修订走 Spec 评审流程 |
| 调研记录 | `docs/research/` | 各模块设计调研的原始记录,仅供溯源 |

其中关于 README 的红线,后续所有批次必须遵守:

- **README 不做项目进度跟踪。** 根 README 只承载产品门面内容:产品定位、核心特性、快速开始、架构、部署与贡献指引。
- **严禁进入 README 的内容**:实现状态表、逐 issue 的进度/验收/审计记录、覆盖率与测试计数、版本变更叙事、任何随开发批次反复增删的段落。
- **进度与变更的正确去处**:issue 系统跟踪进展,[GitHub Releases](https://github.com/cnwenf/Mesh/releases) 承载版本记录;跟踪性质的计划、审计、证据类文档不进仓库主干文档。
- 修改 README 时保持全部链接有效;引用截图或示例时必须真实可复现,不得虚构。

## 安全注意事项

- 不得在代码、文档、提交信息中硬编码任何密钥、口令或令牌;凭据一律走环境变量或 secret 管理。
- 数据存储与中间件(PostgreSQL / Redis / 对象存储)绝不向公网暴露;本地 compose 栈仅限本机开发。
- 发现安全漏洞请勿公开提 issue,先通过私密渠道告知维护者。

## 许可证

Mesh 采用 **[GNU AGPL-3.0](LICENSE)** 许可。提交 PR 即表示你同意你的贡献以 AGPL-3.0 发布。引入第三方代码或依赖前,请先确认其许可与 AGPL-3.0 兼容(注意 AGPL 对网络服务场景的源代码提供义务)。
