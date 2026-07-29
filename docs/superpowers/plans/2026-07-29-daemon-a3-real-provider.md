# Plan: daemon A3 — 钉死版本 Claude Code 适配 + 预算/流式/回流 + 真实 LLM e2e (MES-101)

> 权威文档：`docs/specs/features/runtime-executor.md` §1.4～1.5（provider 契约 / S-01）、
> §3.5（S-07 预算）、§3.9（流式解析与回流、result schema v1）、§5.4（供应链与真实 LLM 门禁）、
> §4.4（A3 放行条件）。基线：A1（PR #71）+ A2（PR #74，分支 `agent/mesh/018456f5`）。

## 目标

把 A2 的安全执行面（真实沙箱 / broker / egress / redactor / cleanup）接上**首个真实
provider**：钉死版本 Claude Code CLI，完成 §5.4 供应链门禁（capability manifest +
SHA-256 校验、固定 argv、prompt 只走 stdin、禁 shell）、S-07 三层预算的 provider/daemon
两层执法、stream-json 严格解析与 session/usage/result 回流（schema v1），并以**真实
LLM e2e**（真实二进制注册 online → 真实 claim → 沙箱内真实执行 → 日志/会话/token 回流）
作为本阶段核心门禁。

## 设计要点

### 1. capability manifest（§1.4）—— `manifest.py`

- `ProviderManifest`（frozen）：`provider/version/binary_sha256/required_flags/
  hard_limits(usd_budget, wall_timeout)`，字段与 §1.4 YAML 示例一一对应。
- 编码采用 **TOML**（stdlib `tomllib`，不引入新依赖 → 不扩大供应链面）；字段语义与
  §1.4 YAML 完全一致（在 Spec/README 中注明编码映射）。
- 严格校验：版本精确钉死；sha256 为 64 位十六进制；`required_flags` 必须是 §1.4 固定
  argv 集合的子集且不得含 §1.5 扩面参数（`FORBIDDEN_ESCALATION_ARGS`）；
  `hard_limits.usd_budget/wall_timeout` 必须为 true（缺硬预算能力即 fail-closed，§3.5）。

### 2. provider 适配 —— `providers/claude_code.py`

- `ClaudeCodeAdapter` 实现 `ExecutorAdapter`。
- **probe()（fail-closed，§1.4 探测流程）**：manifest → 复用 `inventory.probe_binary`
  （绝对路径、非 symlink、owner/mode、SHA-256）→ digest/version 与 manifest 精确比对 →
  空 HOME、最小 env 读 `--help`，逐一核对 `required_flags` 出现 → 探测缓存以
  `(path, dev, ino, mtime_ns, size, sha256)` 为键，inode/mtime/hash 任一变化即失效重探
  （§1.4 末段）。心跳只上报通过探测的 `{provider, version, capabilities, binary_sha256}`。
- **run()（固定 argv + stdin + 禁 shell，§1.4/§1.5）**：
  1. `provider_env.write_provider_configs` 在 attempt 私有 run 目录生成
     `settings.json/mcp.json/system.md`（0444，随沙箱只读挂载；mcp.json 仅登记平台 broker）；
  2. `build_provider_argv` 产出 §1.4 固定 argv（不经 shell，`asyncio` exec 直起）；
  3. 沙箱 env 由 `build_sandbox_env` 从空构造 + egress proxy 指针 + 管理员配置的 provider
     凭据（`provider_env_file`，0600 门禁、逐名 `validate_env_name` + `scrub_env` 复查；
     凭据值全量进入 RedactionPipeline —— §5.4.7「provider 凭证只进入受信启动边界，
     日志/产物经同一脱敏器」）；
  4. prompt 只走 stdin：单条 stream-json `user` 消息，不可信上下文以随机边界标记包裹
     （§3.7；server 快照提供边界时优先用快照边界），随后关闭 stdin；
  5. 进程在 A2 真实沙箱内执行（`SandboxManager.provision`，fail-closed 不降级）。
- **进程启动接缝可注入**（单测用假 claude 脚本走本机 subprocess，不经沙箱；真实沙箱路径
  由 root 标记的全栈测试与真实 e2e 覆盖），解析/预算/argv 逻辑 100% 密封单测覆盖。

### 3. stream-json 严格解析（§3.9）—— `stream_json.py`

- 固定 schema 白名单：`system/init` → `SessionStarted`；`assistant`（text→`TextDelta`、
  tool_use→`ToolRequested`、usage→`UsageObserved` 累计）；`user` tool_result →
  `ToolCompleted`；`result` → 终局 `UsageObserved(turns=num_turns, cost=total_cost_usd)` +
  `FinalResult`。
- 未知/畸形/超大（>1 MiB 行、字段深度/条数上限）记录：**丢弃并计入诊断**
  （`ProtocolWarning(raw_type)`），绝不落盘原始流。
- `thinking` 块永不产生事件（§3.7：thinking 不入日志/结果/回流）。

### 4. S-07 预算（§3.5）—— `budget.py` + supervisor

- `BudgetLimits.from_snapshot`：USD（Decimal）/token/turn/wall/idle，取冻结快照与 daemon
  上限**更严格者**（§4.3）；manifest 无 `hard_limits.usd_budget` → 拒绝运行。
- `BudgetGuard`：每条 usage/turn 事件与 wall/idle 时钟校验 → 违例即 TERM→KILL provider，
  终结语义 `budget_exceeded` / `timeout`（result schema v1 TERMINATIONS 已含）。
- provider 层：`--max-budget-usd <frozen>` 已在固定 argv；daemon 层实时截断 + 服务端核账
  材料（usage 回流）。

### 5. result 回流（§3.9 schema v1）

- `base.UsageObserved` 增 `turns: int = 0`；`FinalResult` 增 `termination: str = ""`
  （adapter 已知精确终结时设置）。`attempt.py`：turns 流入 `Usage`；`termination` 映射
  `failure_reason` 与 `result.outcome.termination`（默认路径行为不变，合同测试保持绿）。
- session_id/model/usage/cost 均随 `build_result` v1 上报；脱敏走既有全通道管线。

### 6. 配置与装配

- `DaemonConfig` 新增（全部绝对路径、fail-closed）：`provider_manifest`、
  `provider_env_file`、`sandbox_memory_bytes/sandbox_cpu_quota_us/sandbox_pids_max/
  sandbox_tmp_bytes`（默认保持 A2 常量，真实 provider 可调高）。
- `app._select_adapter`：配置了 manifest → 每 attempt 构造 `ClaudeCodeAdapter`
  （run 目录/broker socket/egress proxy/凭据/预算来自冻结快照）；provider 凭据值并入
  全局脱敏集。`cli.build_adapters`：manifest 配置时以 probe 实例进 Inventory（心跳上报）。
- `doctor`：manifest/二进制/flags 检查给出精确原因与修复动作（§4.1）。
- 新增运维辅助：`mesh-runtime manifest hash <binary>` 输出钉死用 sha256 + 版本。

### 7. 测试策略（TDD，覆盖率 ≥90%，branch ≥90%）

| 层 | 内容 |
| --- | --- |
| unit（密封） | manifest 解析/负向；stream_json 全记录类型/丢弃/上限/thinking 不入流；budget 更严格者/五种违例/Decimal；provider_env 凭据文件 0600 负向 + stdin 边界包裹；claude_code probe 负向矩阵（symlink/sha/版本/flags/缓存失效）+ run 事件序列/预算截断/argv 逐 flag/config 文件 0444 & mcp 仅 broker；supervisor budget_exceeded/timeout/turns 回流 |
| sandbox（root 真环境） | 全栈：真沙箱 + 假 claude 脚本（发 stream-json）→ 固定 argv/只读配置挂载/事件回流 |
| integration（真实 LLM 门禁） | `tests/integration/real_llm_e2e.py`：本地真 server（公开 API，禁 psql seed）+ 真 Claude Code 二进制（钉死 sha256）注册 online → 真实 claim → 沙箱内真实 LLM 执行 → 断言 completed、日志/会话/token 回流、usage>0、密钥零泄漏；附预算截断负向一次；证据 `docs/evidence/mes-101/` |

## 实施顺序（RED→GREEN→REFACTOR）

1. `stream_json.py` + `test_stream_json.py`
2. `budget.py` + `test_budget.py`
3. `manifest.py` + `test_manifest.py`
4. `provider_env.py` 扩展（凭据文件加载 + stdin 组装）+ 测试
5. `base.py`/`attempt.py` 终结语义与 turns + 测试
6. `providers/claude_code.py` + 密封单测
7. `config.py`/`app.py`/`cli.py`/`doctor.py` 装配 + 测试
8. root 全栈沙箱测试（假 stream-json 脚本）
9. 全量套件 + 覆盖率门槛 + ruff
10. 真实 LLM e2e + 证据留存
11. 同步 `runtime-executor.md`/daemon README/根 README/CHANGELOG
12. code-review（security 视角）→ 提交（cnwenf，无 co-author）→ PR → Issue 回报

## 风险与对策

- **真实二进制 flag 兼容**：已实测本机 Claude Code 2.1.218 支持 §1.4 全部钉死 flag
  （`--bare/--disable-slash-commands/--no-session-persistence/--setting-sources/
  --strict-mcp-config/--settings/--system-prompt-file/--tools/--disallowed-tools/
  --permission-mode/--max-budget-usd/--print/--input-format/--output-format/--mcp-config`）。
- **沙箱内网络**：已实测 claude 走 `HTTPS_PROXY` CONNECT（egress gateway 同构），
  策略仅放行 API host，其余（如 registry.npmjs.org）被拒且不影响执行。
- **A2 未合入 main**：本分支基于 A2 分支 `agent/mesh/018456f5`；A2（PR #74）合入后
  rebase，PR diff 自动收敛到 A3。
- **凭据安全**：provider 凭据仅来自管理员 0600 文件、只进沙箱启动 env、值全量入脱敏集；
  不进 journal/日志/result/argv/提交。
