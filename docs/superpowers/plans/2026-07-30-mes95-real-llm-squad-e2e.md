# MES-95 真 LLM 全链路 e2e（多 agent 组队真实完成 issue · 产品命脉测试）实施计划

日期：2026-07-30
状态：阶段 2（MES-94 daemon A1–A3 / MES-98 server P0 契约）已合入 main，阶段 3 授权启动。

## 1. 现状核查结论（实测，非臆测）

1. 单 agent 真 LLM 链路已可用：`daemon/tests/integration/real_llm_e2e.py`（MES-101）经公开 API 注册/登录 → 建 workspace/agent/runtime → daemon 激活 → 真实 claim → 钉死 Claude Code 2.1.218 真跑 → usage/session/result 回流，证据 `docs/evidence/mes-101/real-llm-e2e.json` PASS。
2. **缺口 A（leader 拆解路径不存在）**：squad 编排为「leader agent 持 API token 代 leader 调用拆解/分派/汇报端点」（squad.md §5.3、锚点 line 10），但 daemon broker 闸门表（§3.3）只有 `issue.read/comment/status`、`project.read`，task token 默认 scope 无「当前 squad task 操作」（runtime-executor §2.2 S-05 承诺但未落地）。沙箱无直达 Mesh 网络 → leader LLM 在真实运行中**无任何路径**创建子任务。服务端 `observe_execution_finished_tx` 以 child_count 判定 leader_evaluated 三值，子任务只能由编排者身份经 `POST .../tasks/{id}/subtasks` 创建。
3. **缺口 B（broker MCP 传输无效）**：`provider_env.write_provider_configs` 以 `{"type": "unix-socket"}` 注册 MCP 服务；实测钉死 2.1.218 仅支持 `stdio|sse|http`（`claude mcp add --help` 证实），init 事件 `mcp_servers: []`——真实运行中 LLM 无法触达 broker（MES-101 以「Do not use any tools」规避）。命脉层断言（aggregator 真实评论、issue.status 流转、tool broker 真实链路——runtime-executor §5.4.6）要求 broker 在真实运行可达。

## 2. 设计（补齐 Spec 承诺的缺口，不改需求）

### 2.1 Server（backend）

- **task principal squad 路由**（`mesh/runtime/task_routes.py`，仅显式声明 task principal 的路由接受 `mesh_task_`，auth.md §2.5.1）：
  - `GET /api/v1/task/squad/members`（scope `squad:task:read`）：返回当前 squad task 所属小队成员（member_id/name/role/member_type）。
  - `POST /api/v1/task/squad/subtasks`（scope `squad:task:decompose`）：body `{plan_markdown?, subtasks:[{title, description?, assignee_member_id?, depends_on?[]}]}`。经 attempt → execution.task_spec 解析 squad_task_id/squad_role；**要求 squad_role == orchestrator**；actor = token 所属 agent member；委派既有 `SquadTaskService.create_subtasks`（复用 `_assert_can_orchestrate`、状态机、深度/环校验、dispatch_ready）。
- **task token 角色化 scope**（`mesh/runtime/task_tokens.py`）：`issue_task_token(..., squad_role=None)`；`squad_role == "orchestrator"` 时 methods 追加 `squad:task:read`、`squad:task:decompose`。两处签发点（claim.py / attempts.py 续租轮换）从 `execution.task_spec` 传入。
- **冻结 grants 角色化**（`mesh/agent/triggers.py`）：enqueue 时为快照 `capability_grants` 追加默认 broker 授权（`issue.read` read_only、`issue.comment` write、`issue.status` write、`project.read` read_only；orchestrator 追加 `squad.members` read_only、`squad.subtasks` write），**在 digest 计算前**注入（daemon 校验 digest）。

### 2.2 Daemon（mesh_runtime）

- **MCP 传输修复**（`provider_env.py`）：mcp.json 改为 stdio 传输：`{"type":"stdio","command":"/usr/bin/python3","args":["/run/mesh_task_broker_mcp.py"]}`；同目录写平台属主 0444 桥接脚本 `mesh_task_broker_mcp.py`（随 run dir 只读绑定进沙箱，信任级同 mcp.json）。沙箱已绑定宿主 `/usr`（`_SYSTEM_DIRS=("/usr",)`）只读，python3 可用。
  - 桥接：stdio MCP JSON-RPC（initialize / notifications/initialized / tools/list / tools/call）↔ broker unix socket（先投 nonce，再 `{"id","method","params"}`）。socket/nonce 取自已注入的 `MESH_BROKER_SOCKET`/`MESH_BROKER_NONCE`。task token 仍只在 daemon 侧 broker，**不进沙箱**（S-05 不破）。
  - 工具名（下划线）→ 动作映射：`issue_read→issue.read`、`issue_comment→issue.comment`、`issue_status→issue.status`、`project_read→project.read`、`squad_members→squad.members`、`squad_subtasks→squad.subtasks`。
- **broker 闸门表扩充**（`broker.py`，§3.3 同构追加，fail-closed 不变）：
  - `squad.members`: `GateSpec("read_only", broker, scope="squad:task:read")` → `GET /api/v1/task/squad/members`
  - `squad.subtasks`: `GateSpec("write", broker, scope="squad:task:decompose")` → `POST /api/v1/task/squad/subtasks`；纳入 `_IDEMPOTENT_ACTIONS`（强制调用方幂等键）。
  - peer/cgroup/nonce/grant/scope 四闸原样执法；task token scope 由服务端 `resolve_task_principal(required_scope=...)` 二次执法。

### 2.3 命脉 e2e（`daemon/tests/integration/real_llm_squad_e2e.py`）

全程公开 API（§5.4.5，禁 psql seed）：register/login → workspace（allowed_repos）→ 3 agents（leader + worker-a + worker-b，model_config 冻结 budget + network_policy）→ squad（leader 角色）→ pending runtime + 激活码 → daemon（真实沙箱 + 钉死 manifest Claude Code）activate → online → 建**动态 nonce** issue → `POST /squads/{sid}/tasks` 派给小队 →
断言：
1. runtime online；
2. leader orchestrator 执行 completed，真实 usage（tokens>0 / session / cost>0），**经 broker 工具真实创建 2 个子任务**（child_count==2，leader_evaluated=action）；
3. 两名成员 executor 执行分别 completed，各自真实 session/usage，session 互异；
4. 成员经 broker `issue.comment` 真实产出含 nonce 的评论（作者=成员 member）；
5. 子任务全终态 → aggregating → leader aggregator 执行 completed（真实 usage）→ root done、assignment completed；
6. 父 issue 有 leader 署名的汇总结论评论（relay 回写 + leader 自身 broker 评论），issue 状态经 leader broker `issue.status` 置 done；
7. 全执行日志经 REST 可读、非空；API key 在日志/result/评论中零泄漏（同一脱敏器）；
8. 总 token/cost > 0（聚合全部执行 usage）。
证据脱敏后写 `docs/evidence/mes-95/real-llm-squad-e2e.json`。

### 2.4 CI 命脉 workflow（`.github/workflows/real-llm.yml`）

- 仅 `workflow_dispatch`（显式预算/模型入参）+ `schedule`（nightly）；**无 pull_request 触发** → 外部 PR 永不执行、不注入凭证（§5.4.4）。
- `concurrency: group=real-llm, cancel-in-progress: false`（concurrency=1）。
- runs-on 受保护自托管 runner 标签；凭证仅来自仓库 secrets（provider.env / manifest / 钉死二进制校验）。
- 步骤：起本地栈（compose 生成强随机 secret）→ 装 daemon → 跑 `real_llm_e2e.py` + `real_llm_squad_e2e.py` → 脱敏证据作 artifact。

### 2.5 文档

- `docs/specs/features/runtime-executor.md`：§3.3 表追加两行动作；§2.2 task token scope 补「orchestrator 角色化 squad scope」；§4.4.1 台账登记 B 命脉层落地；§3.9/§1.5 补 MCP stdio 桥接说明。
- `docs/specs/features/squad.md` §5.3：将「agent runtime 持 API token 代 leader 调用」细化为「经 task broker 持短期 task token 代 leader 调用拆解端点（squad.subtasks），服务端校验调用 attempt 的 agent 即该任务 orchestrator」。
- `daemon/README.md` + 根 `README.md`：两层测试策略与命脉 e2e 运行方式（操作者前置：钉死 provider + manifest + provider.env 0600 + root + 本地栈）。

## 3. 任务切分（TDD，RED→GREEN→REFACTOR）

- T1 backend：task token 角色化 scope（含两签发点）+ 单测（正/负）。
- T2 backend：task principal squad 路由 + 单测（正；非 orchestrator 403、scope 缺失 401、跨任务资源越权拒、非法状态 409）。
- T3 backend：triggers 冻结 grants（digest 前注入）+ 单测。
- T4 daemon：broker 两动作 + 单测（gate/scope/grant/幂等/负向）。
- T5 daemon：MCP stdio 桥接脚本 + provider_env mcp.json 变更 + 单测（对假 broker socket 跑 initialize/tools-list/tools-call；run dir 文件属主/权限）。
- T6 本地真实栈联调：compose（独立项目名/端口 + gen-dev-secrets）起 server+worker+gateway → fake provider 冒烟（real_server_e2e）确认无回归 → 真 LLM 单 agent 复跑确认 MCP 桥接真实可用（leader 之外的工具链路）。
- T7 命脉 e2e 脚本入库 + **真实完整跑通一次**（证据落盘）。
- T8 CI workflow + 文档/README + 覆盖率实测（backend/daemon ≥90%，新增代码 ≥90%）。
- T9 PR 合入 main + MES-95 报告（PR 链接 + 真实证据）。

## 4. 风险与对策

- LLM 输出不稳定（leader 未按指令调用工具）：system_instructions 强约束 + 预算内 max_turns 余量；失败即证据留档重跑（命脉测试可复跑）。
- MCP stdio 桥接为新代码面：hermetic 单测（假 socket）+ ISO 矩阵真实复跑（跨 attempt 不可达、nonce 缺失拒）。
- 安全面变更（§3.3 追加行 / §2.2 scope）：同构扩展、fail-closed 不变；MES-97 安全复测将覆盖，交付说明中明示变更点。
- 并发执行：runtime max_concurrent=2+，daemon ClaimScheduler 多 slot；成员任务串行可接受但断言要求「分别产生」，故给足并发。
