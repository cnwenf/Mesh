# MES-151 合规与容器运行时返修计划

> 基线：远端 `main` 的 `7841b281`；分支：`agent/mesh/mes-151-compliance`。
> 方法：writing-plans、test-driven-development、systematic-debugging、verification-before-completion、requesting-code-review。

## 1. 验收问题与根因假设

1. `docs/specs/frontend/clean-room-rules.md` 的示例把应由受控环境维护的匹配规则直接写入受管文档，规则自身因此违反其声明的隔离要求。
2. `frontend/Dockerfile` 的 builder 使用 Node 20，而 `frontend/package.json` 声明 Node ≥22.22.0；容器构建会产生引擎不兼容告警，属于偶然成功而非受支持路径。
3. 上轮缺少五项开发方法的仓库内过程证据。本次把计划、RED/GREEN、调试、完工验证与 review 结论分别落盘，交付时引用明确命令和结果。

## 2. 实施步骤（writing-plans）

### Task A：先建立失败契约（TDD RED）

- 新增 `backend/tests/unit/test_source_provenance.py`。
- 断言 Docker builder 的 Node 版本不低于 `package.json#engines.node`。
- 断言隔离规范不再内嵌匹配词表，且明确要求从仓库外注入。
- 断言新的扫描器必须在缺少外部配置时失败，并覆盖受管文本、完整提交信息与 refs；输出只报告规则编号和位置，不回显匹配内容。
- 运行：`cd backend && pytest tests/unit/test_source_provenance.py -q`。
- 预期 RED：Node 20 < 22.22.0；规范仍含内嵌数组；扫描模块尚不存在。

### Task B：实现外置规则扫描（TDD GREEN）

- 新增 `backend/src/mesh/compliance/source_provenance.py` 与包入口。
- 匹配规则仅从 `--patterns-file` 或 `MESH_FORBIDDEN_SOURCE_PATTERNS` 读取；空配置、非法正则均 fail closed。
- 扫描 `git ls-files` 返回的全部 UTF-8 受管文本，以及 `git log` 的完整提交信息/作者和 `git for-each-ref` 的 refs。
- 诊断输出不包含规则正文或命中行正文，避免 CI 日志二次暴露。
- 新增 `.github/workflows/source-provenance.yml`，以 `fetch-depth: 0` 检出；规则由 repository Actions secret 注入。

### Task C：修复文档与 Node builder

- `frontend/Dockerfile` 改为满足项目声明的精确 Node 22.22.0 builder。
- 重写 `docs/specs/frontend/clean-room-rules.md` 的扫描章节：删除所有内嵌真实标识，仅保留外置配置接口、fail-closed 行为与命令。
- 更新根 `README.md`、`frontend/README.md`，说明 Docker 与本地 Node 下限一致，并记录全仓扫描门禁。

### Task D：系统化调试与验证

- RED 后逐项最小修复，定向测试至 GREEN。
- 用仓库外临时规则输入执行当前受管文本 + 完整 commit messages + refs 扫描，确认零命中。
- 运行 targeted Python coverage，要求新扫描模块 statements/branches/functions/lines 均 ≥90%。
- 运行 backend 相关单测/ruff、frontend lint/typecheck/unit coverage/build。
- 通过 `scripts/gen-dev-secrets.sh` 生成强随机且隔离的 Compose 环境，执行 `docker compose up --build -d`，确认 builder 为 Node 22.22.0、无引擎不兼容告警、7 个服务 healthy/running；真实请求 API health/ready/ping、前端首页和同源反代 ping；最后完整清理隔离资源。

### Task E：review、发布与复验

- 将 requesting-code-review 结论写入独立 review 文档，逐项检查安全、扫描边界、fail-closed、日志脱敏、容器一致性和测试充分性。
- 执行 verification-before-completion 全量命令，以新鲜输出为准。
- 检查 diff、身份、提交信息和 refs；提交并推送新分支，创建关联 MES-151 的 PR。
- 等待 CI 全绿，合并 main 后再跑主干检查；最终线程回复附 PR、扫描、Compose、五项方法证据。

## 3. 过程证据

本节随实施更新，命令均在上述基线分支执行。

- writing-plans：本文件先于实现建立 Task A-E，明确测试契约、最小实现、真实 Compose、
  review 与发布顺序。
- systematic-debugging 复现：`frontend/Dockerfile` builder 为 Node 20，项目引擎下限为
  22.22.0，真实构建出现 `EBADENGINE`；隔离规范同时声明“词表不得包含真实标识”并内嵌
  匹配数组。首次外置规则扫描产生 11 个无关命中，逐项核对后确认是短词规则误伤标准 API
  标识，规则在仓库外改为 token 边界匹配后零命中。直接运行定向 pytest 又受本地旧测试库
  迁移状态干扰，因此把纯单测与项目数据库 fixture 隔离，以 `--confcutdir=tests/unit` 获得
  可复现的真实 RED/GREEN；未修改生产迁移来掩盖环境污染。
- TDD RED：实现前运行
  `pytest --confcutdir=tests/unit tests/unit/test_source_provenance.py -q`，7/7 失败；失败点为
  Node builder 版本、规范仍内嵌规则、扫描模块不存在。
- TDD GREEN：最小实现后同一契约 9/9 通过；补齐不可读规则文件、环境变量加载、非法仓库、
  已删除受管文件和符号链接边界测试后 12/12 通过。定向 branch coverage 为 statements/lines/
  branches/functions 100%，高于 90% 门禁。
- Compose 实机：使用每项独立生成的强随机凭据和隔离 project 启动 7 个服务；显式 builder
  镜像输出 Node `v22.22.0`，构建无 `EBADENGINE`；PostgreSQL、Redis、MinIO 无宿主端口；
  API `healthz`、`readyz`、`ping`、前端首页与前端同源反代 `ping` 均成功。随后执行
  `down -v --remove-orphans --rmi local` 并核对容器、网络、卷、临时镜像均为空。
- verification-before-completion：新鲜执行 backend ruff/format 与定向覆盖率、frontend lint
  （0 error）、typecheck、全量 unit/component coverage、build，三项 Spec 一致性检查、workflow
  YAML 解析和 `git diff --check`；均通过。前端覆盖率 statements/lines 98.21%、branches
  92.30%、functions 96.46%，178 个受管源文件 per-file 四项均 ≥90%。最终提交后的禁源
  扫描与 GitHub CI 结果在 PR 中追加。
- requesting-code-review：结论见
  `docs/superpowers/plans/2026-08-01-mes151-code-review.md`。
