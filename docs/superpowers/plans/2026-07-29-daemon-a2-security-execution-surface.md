# Daemon A2 安全执行面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 A1 骨架（PR #71）之上交付 daemon A2 安全执行面：真实 Linux namespace/cgroup 沙箱（fail-closed）、S-01 不可信配置隔离、S-02 唯一 ToolBroker 闸门、S-04 egress gateway、checkout helper、redactor 扩面、S-08 清理清单、ISO-01~14 真实负向矩阵，并与已合入 main 的 MES-98 server P0 契约做真实联调。

**Architecture:** 每个 attempt 由 `RuntimeApp._spawn_attempt` 装配完整安全栈：`CheckoutHelper`（沙箱外、只读凭证、精确 SHA）→ `EgressGateway`（宿主侧每 attempt 代理，沙箱 netns 无默认路由）→ `ToolBroker`（宿主侧 Unix socket，SO_PEERCRED+cgroup+nonce 三重校验，代持 task token）→ `SandboxManager`（unshare user/mount/pid/net/ipc/uts + cgroup2 限额 + pivot_root 最小根 + 降权 nobody，fail-closed）→ fake provider 在沙箱内执行 → 脱敏日志/结果/diff 回流 → `AttemptCleaner` 幂等清理。A3 的真实 Claude Code provider 复用同一安全栈（argv/env/config 生成器本阶段就绪）。

**Tech Stack:** Python 3.12 标准库（`os.unshare`/mounts via `mount(8)` 子进程/cgroup2 fs/sqlite/asyncio），httpx，pytest + pytest-asyncio + pytest-cov，真实 Linux 内核隔离（禁 mock）。

## Global Constraints

- UT 覆盖率 ≥90%：pytest-cov 实测整体、新增代码、daemon 包分支覆盖率均达标（`fail_under = 90` 已在 pyproject）。
- S-01/S-02/S-03/S-04 真实实现 + 负向测试全绿；ISO 矩阵真实 Linux namespace/cgroup/network，禁 mock/skip，不得降级为告警。
- 沙箱 fail-closed：沙箱未就绪绝不降级裸跑；attempt 以 `failed` + `failure_reason="sandbox_violation"` 终结（TERMINATIONS 已含 `sandbox_violation`，server failure_reason 词表已含）。
- server 契约以 main 已合入 MES-98 实现为准（`/api/v1/daemon/*`，task token 随 claim/renew 一次性下发并轮换；日志 offset 为跨流累计字节；approvals 由 server 置 `cancelled(awaiting_approval)`）。
- 代码/注释/文档/提交/分支名不得暴露任何对标产品字样（Mesh 独立原创）。
- Git：author/committer 均为 `cnwenf <cnwenf@outlook.com>`；提交信息无任何 co-author 行；`core.hooksPath=/dev/null`。
- DRY/YAGNI/KISS；文件 <800 行、函数 <50 行；不可变数据优先；错误显式处理；不在日志中泄露 secret/token/路径细节。

## File Structure

**Create:**

| 文件 | 职责 |
| --- | --- |
| `daemon/src/mesh_runtime/netguard.py` | URL 规范化（拒 userinfo/混淆 IP/控制字符）、IP 分类（loopback/private/link-local/multicast/reserved/benchmarking/documentation/云元数据，IPv4-mapped 归一化）、`assert_public_url`（checkout SSRF 闸门） |
| `daemon/src/mesh_runtime/egress.py` | S-04：`NetworkPolicy`（冻结快照映射）、`EgressGateway` 每 attempt asyncio 代理（HTTP absolute-form + CONNECT）、可信解析（可注入 resolver）→全 IP 过滤→钉死建连、逐请求重验（3xx 不自动跟随）、`egress_enforced` 能力位 |
| `daemon/src/mesh_runtime/sandbox.py` | `SandboxSpec`/`SandboxHandle`/`SandboxManager`：attempt 根布局、cgroup2 创建与限额、经 `sandbox_init` 子进程建立 namespace+mounts+降权、fail-closed、TERM/KILL 整个 cgroup |
| `daemon/src/mesh_runtime/sandbox_init.py` | 沙箱子进程内执行：私有 mount 传播、tmpfs、只读 bind（provider 镜像/配置）、worktree rw、`/run/mesh`（broker socket+配置）、pivot_root、setgroups/setgid/setuid、空 HOME/XDG env、execve provider |
| `daemon/src/mesh_runtime/provider_env.py` | S-01/S-10：`RESERVED_ENV_NAMES`/`validate_env_name`/`scrub_env`、§1.4 固定 argv 生成器、attempt 私有配置写入（settings.json/mcp.json/system.md，root 所有 0400 只读挂载）、恶意 repo fixture 负向校验函数（ISO-09） |
| `daemon/src/mesh_runtime/broker.py` | S-02：`GATE_TABLE`（动作→闸门唯一映射 §3.3）、`ToolBrokerServer`（unix socket 长度前缀 JSON、SO_PEERCRED uid + /proc/<pid>/cgroup 成员 + attempt nonce）、task token 代持与 scope 校验、限速、`ConfirmRequired` → `api.request_approval` 协议（取消+新 attempt 续跑）、`ActionBroker` 一次性 git 写 grant |
| `daemon/src/mesh_runtime/checkout.py` | `CheckoutHelper`：冻结 URL/SHA 校验 + allowed_repos + assert_public_url、git clone/fetch 精确 SHA（只读凭证经 `GIT_CONFIG_COUNT` 临时配置，不进 remote URL/git config/env）、cloning/ready 上报、diff 生成与 diff_ready 上报、凭证用后即清零 |
| `daemon/src/mesh_runtime/cleanup.py` | S-08：`AttemptCleaner` 白名单资源清单、幂等、不跟随 symlink、按 §3.6 顺序（broker→token/grant 吊销→cgroup KILL→tmpfs 卸载清零→socket/ns/veth/cgroup/worktree→spool 门禁→journal 位） |
| `daemon/tests/unit/test_netguard.py` | netguard 单测（含 IPv4-mapped/rebinding 答案混合/元数据主机名负向） |
| `daemon/tests/unit/test_egress.py` | egress 单测 + 真实 loopback socket（允许列表放行、私网拒绝、CONNECT 钉死、重定向逐跳、policy 默认拒绝） |
| `daemon/tests/unit/test_sandbox.py` | 真实 ns/cgroup 单测：root 下起真沙箱跑小进程，断言 uid/mounts/net/cgroup；非 root 环境 fail（不 skip） |
| `daemon/tests/unit/test_provider_env.py` | argv/env/config 生成 + reserved env 拒绝 + 恶意 fixture 负向 |
| `daemon/tests/unit/test_broker.py` | 真实 unix socket：peer 校验、闸门映射、scope 拒绝、限速、confirm_required 协议、action grant 一次性 |
| `daemon/tests/unit/test_checkout.py` | 真实本地 git 仓库：精确 SHA checkout、凭证不落 remote URL、diff、URL 白名单/SSRF 拒绝 |
| `daemon/tests/unit/test_cleanup.py` | 幂等清理、symlink 不跟随、白名单外路径拒绝 |
| `daemon/tests/isolation/conftest.py` | ISO 矩阵共享夹具（双 attempt 并发装配、攻击载荷执行器）；非 root → `pytest.fail`（禁 skip） |
| `daemon/tests/isolation/test_iso_matrix.py` | ISO-01~14 真实负向矩阵（max_concurrent≥2） |
| `daemon/tests/integration/real_server_e2e.py` | 真实联调脚本：对本地真实 server 走 activate→online→claim→执行→回流，输出证据 JSON |
| `docs/evidence/mes-100/` | 联调与矩阵证据（脱敏后） |

**Modify:**

| 文件 | 变更 |
| --- | --- |
| `daemon/src/mesh_runtime/api.py` | `ClaimResponse`: `task_token`/`task_token_expires_at`/`resume_context` 属性；`LeaseInfo`: `task_token`/`task_token_expires_at`；`activate()` 增 `protocol_version`/`provider_manifest`/`daemon_features`；`heartbeat()` 增 `protocol_version` |
| `daemon/src/mesh_runtime/logs.py` | offset 改为**跨流单一递增水位**（server 语义：offset 为 attempt 跨 stdout/stderr 累计字节）；journal 同步写两字段为同一水位 |
| `daemon/src/mesh_runtime/journal.py` | 增列 `cleanup_state TEXT NOT NULL DEFAULT ''` + sandbox handle 元数据字段（仅 IDs/路径，无 secret）；`_UPDATABLE_FIELDS` 扩展；打开时幂等 `ALTER TABLE` 迁移 |
| `daemon/src/mesh_runtime/attempt.py` | 注入 `security: AttemptSecurity`（sandbox/broker/egress/checkout/cleaner 束）；`supervise` 顺序：journal→checkout→egress→broker→sandbox→report running→provider；sandbox 失败→`failed/sandbox_violation`；`ToolRequested`→broker 代执行；`ConfirmRequired`→`api.request_approval` 后终止本 attempt（server 置 awaiting_approval）；renew 轮换 task token→broker |
| `daemon/src/mesh_runtime/app.py` | `_spawn_attempt` 装配安全栈；redaction secrets = 凭证值+task token（只在内存，不进日志）；`heartbeat_metadata` 增 sandbox/egress/broker 能力位与 `egress_enforced`；`build_run_request` 读取冻结 grants→tools_allowlist |
| `daemon/src/mesh_runtime/config.py` | 新增键：`sandbox_uid`/`sandbox_gid`（默认 nobody 65534）、`provider_image_dir`、`egress_listen_host`（默认 127.0.0.1）、`trusted_resolvers`（默认系统）、`git_bin`（默认 git）；全部有默认值，保持最小配置可用 |
| `daemon/src/mesh_runtime/doctor.py` | 新增检查：sandbox 能力（unshare/cgroup2 可写）、egress 绑定、git 可用 |
| `daemon/src/mesh_runtime/inventory.py` | 能力键纳入 `sandbox.linux_ns`/`egress.gateway`/`broker.unix`（探测通过才报） |
| `.github/workflows/daemon-ci.yml` | 增 isolation matrix job（ubuntu-latest sudo 运行，真实 ns/cgroup） |
| `docs/specs/features/runtime-executor.md` | 头部进度：A2 已落地（真实沙箱/broker/egress/矩阵全绿 + 真实联调证据） |
| `README.md` | daemon 段落同步 A2 状态 |

---

## Task 1: 契约对齐——api 客户端扩展 + 跨流日志水位 + journal 迁移

**Files:**
- Modify: `daemon/src/mesh_runtime/api.py`, `daemon/src/mesh_runtime/logs.py`, `daemon/src/mesh_runtime/journal.py`
- Test: `daemon/tests/unit/test_api_coverage.py`（扩展）, `daemon/tests/unit/test_logs.py`（扩展）, `daemon/tests/unit/test_journal.py`（扩展）

**Interfaces:**
- Consumes: MES-98 契约（claim 响应 `attempt.task_token`/`task_token_expires_at`，renew 响应同名轮换字段，execution `resume_context`；server 日志 offset 跨流累计）。
- Produces: `ClaimResponse.task_token -> str|None`, `ClaimResponse.task_token_expires_at -> str|None`, `ClaimResponse.resume_context -> dict|None`, `LeaseInfo.task_token -> str|None`, `LeaseInfo.task_token_expires_at -> str|None`, `JournalEntry.cleanup_state -> str`。

- [ ] **Step 1: 写失败测试**（api 新字段 + 跨流水位 + journal 迁移，AAA 命名）
- [ ] **Step 2: 运行确认失败**：`../.venv-daemon/bin/python -m pytest tests/unit/test_api_coverage.py tests/unit/test_logs.py tests/unit/test_journal.py -q`
- [ ] **Step 3: 实现**：`ClaimResponse`/`LeaseInfo` 属性；`activate(..., protocol_version, provider_manifest, daemon_features)` 与 `heartbeat(..., protocol_version=None)` body 扩展；`LogUploader._flush_stream` 起始 offset = `max(entry.log_offset_stdout, entry.log_offset_stderr)`，成功后两字段同写 `ack.accepted_end_offset`；journal `_SCHEMA` 增 `cleanup_state`，`open` 内 `PRAGMA table_info` 检测缺列则 `ALTER TABLE ... ADD COLUMN`。
- [ ] **Step 4: 全量测试绿**：`../.venv-daemon/bin/python -m pytest tests/ -q --cov=mesh_runtime`
- [ ] **Step 5: 提交**：`feat(daemon): 对齐 MES-98 P0 契约——task token 字段/跨流日志水位/journal 迁移(MES-100)`

## Task 2: netguard——URL 规范化与 IP 过滤（S-04 基础件）

**Files:**
- Create: `daemon/src/mesh_runtime/netguard.py`, `daemon/tests/unit/test_netguard.py`

**Interfaces:**
- Produces:
  - `normalize_url(raw: str) -> NormalizedUrl`（frozen: `scheme, host, port, path, query`；拒 userinfo/控制字符/空白/`\\` 混淆/十进制或八进制 IP 变体；非显式端口按 scheme 默认）
  - `classify_ip(ip: str) -> IpVerdict`（frozen: `allowed: bool, reason: str|None`；`ipaddress` 归一化后判 `is_loopback/is_private/is_link_local/is_multicast/is_reserved/is_unspecified`，IPv4-mapped 取 `.ipv4_mapped` 再判，额外拒 `169.254.169.254`/`fd00:ec2::254`/`100.100.100.200` 元数据、`192.0.2.0/24` 文档、`198.18.0.0/15` benchmarking）
  - `filter_answer_set(ips: list[str]) -> list[str]`（任一 IP 禁用 → 抛 `ForbiddenAddressError`，整次拒绝 §3.4 第 4 条）
  - `assert_public_url(url: str)`（checkout SSRF 闸门：scheme∈{https,http,git,ssh}，host 非 IP 时解析后同样过滤，拒元数据主机名 `metadata.google.internal`/`metadata.goog`/`instance-data`/`localhost`）
  - `ForbiddenAddressError(DaemonError)`

- [ ] **Step 1: 写失败测试**：公网 IP 放行；loopback/私有/link-local/多播/保留/文档/benchmark/元数据全拒；`::ffff:127.0.0.1` 与 `::ffff:10.0.0.1`（IPv4-mapped）拒；混合答案 `[93.184.216.34, 127.0.0.1]` 整体拒；userinfo `http://u:p@host/` 拒；`http://2130706433/`（十进制 127.0.0.1）与 `http://0x7f000001/` 拒；控制字符 `http://evil\@good/` 拒；`assert_public_url("https://metadata.google.internal/x")` 拒。
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现 netguard.py**（纯函数，无 I/O 除 assert_public_url 的可选解析注入点）
- [ ] **Step 4: 测试绿 + 覆盖率检查**
- [ ] **Step 5: 提交**：`feat(daemon): netguard——URL 规范化 + 全 IP 过滤 + SSRF 闸门(MES-100 S-04)`

## Task 3: egress gateway（S-04）

**Files:**
- Create: `daemon/src/mesh_runtime/egress.py`, `daemon/tests/unit/test_egress.py`
- Consumes: `netguard.normalize_url/classify_ip/filter_answer_set`、冻结 `network_policy`。
- Produces:
  - `NetworkPolicy.from_snapshot(dict) -> NetworkPolicy`（frozen: allowed_schemes/hosts/ports/methods、max_redirects、max_upload_bytes；默认拒绝基线）
  - `EgressGateway(policy, *, resolver=None, listen_host="127.0.0.1")`：`async start() -> int`（返回端口）、`async stop()`、`proxy_url` 属性
  - resolver 协议：`async def resolve(host: str) -> list[str]`（默认线程内 `socket.getaddrinfo`，测试注入）
  - 行为：HTTP absolute-form 与 CONNECT；每请求执行 URL allowlist→可信解析→`filter_answer_set`→向选定 IP 直连（保留 Host/SNI 原 host）；DNS TTL 内连接复用，超 TTL 关闭重验；3xx 原样返回不跟随（客户端每跳重新过网关即逐跳重验）；CONNECT 仅允许 approved host:port；任一校验失败 → 403/502 且审计计数，绝不降级直连。

- [ ] **Step 1: 写失败测试**（真实 socket）：起本地 HTTP 服务作为“公网目标”（注入 resolver 返回 127.0.0.1 伪装公网 + 覆盖 policy 允许该 host:port 仅用于测试通道）；① 允许列表内 GET 200 透传；② 非允许 host 403；③ 非允许 scheme（ftp）拒；④ resolver 返回混合公私 IP → 拒绝且未建连（断言目标服务器零请求）；⑤ CONNECT 允许 host:443 → 隧道建立（真实 TLS 环回自签可选，最小断言字节透传）；⑥ CONNECT 未授权端口拒；⑦ 3xx 不跟随（返回原 302，客户端二次请求才再验）；⑧ 重定向目标是私网 → 第二跳拒；⑨ 默认构造（空 allowed_hosts）一切拒绝。
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现 egress.py**（asyncio.start_server；HTTP 解析最小充分：请求行+头透传，body 按 Content-Length 转发，超限 max_upload_bytes 拒；CONNECT：校验→钉死 IP 建连→双向 pipe）
- [ ] **Step 4: 测试绿 + 覆盖率**
- [ ] **Step 5: 提交**：`feat(daemon): egress gateway——可信解析/全 IP 过滤/钉死建连/逐跳重验(MES-100 S-04)`

## Task 4: S-01 不可信配置隔离与 reserved env（provider_env）

**Files:**
- Create: `daemon/src/mesh_runtime/provider_env.py`, `daemon/tests/unit/test_provider_env.py`
- Consumes: 冻结 AttemptSpec（provider/model/effort/system_instructions/budget/capability_grants）。
- Produces:
  - `RESERVED_ENV_NAMES`（`LD_*`/`DYLD_*`/`PYTHON*`/`NODE_OPTIONS`/`PATH` 精确/`MESH_DAEMON_*`/`MESH_INTERNAL_*`/`HOME`/`XDG_*`/云凭证常见名 `AWS_*`/`GOOGLE_APPLICATION_CREDENTIALS`/`AZURE_*` 等前缀表）
  - `validate_env_name(name: str) -> None`（`^[A-Z][A-Z0-9_]{0,63}$` + reserved 拒绝 → `ReservedEnvError(DaemonError)`）
  - `scrub_env(merged: dict) -> dict`（白名单合并后二次删除 reserved，§3.8）
  - `build_provider_argv(spec: ProviderLaunchSpec) -> list[str]`（§1.4 固定 flags：`--print --input-format stream-json --output-format stream-json --bare --disable-slash-commands --no-session-persistence --setting-sources "" --strict-mcp-config --mcp-config <p> --settings <p> --system-prompt-file <p> --tools <allow> --disallowed-tools <deny> --permission-mode bypassPermissions --max-budget-usd <frozen>`；显式拒绝 `--add-dir/--plugin-dir/--plugin-url/--agent` 等扩面参数出现在任何来源）
  - `write_provider_configs(root: Path, *, system_md: str, broker_socket: str, settings: dict) -> ProviderConfigPaths`（settings.json/mcp.json/system.md 写入 attempt tmpfs 目录，0400 root 所有；mcp.json 只登记平台 broker）
  - `scan_repo_for_hostile_files(worktree: Path) -> list[HostileFinding]`（ISO-09 负向：枚举 `.mcp.json`/`.claude/settings*.json`/hooks/`CLAUDE.md` 仅作普通文件报告，绝不解释执行；供矩阵断言“未被加载”）

- [ ] **Step 1: 写失败测试**：argv 与 §1.4 逐 flag 断言；reserved env（`LD_PRELOAD`/`PYTHONPATH`/`MESH_DAEMON_X`/`AWS_SECRET_ACCESS_KEY`）拒绝；合法 `CI_API_KEY` 通过；scrub 二次清洗；配置文件权限/属主断言（root 运行下）；恶意 fixture 扫描器对含 `.mcp.json`+hooks 的样例 worktree 返回 findings 且无进程/网络副作用。
- [ ] **Step 2-4: RED→GREEN→覆盖率**
- [ ] **Step 5: 提交**：`feat(daemon): S-01 不可信配置隔离——固定 argv/reserved env/私有只读配置(MES-100)`

## Task 5: namespace/cgroup 沙箱（fail-closed）

**Files:**
- Create: `daemon/src/mesh_runtime/sandbox.py`, `daemon/src/mesh_runtime/sandbox_init.py`, `daemon/tests/unit/test_sandbox.py`
- Consumes: `provider_env`（env/argv/config 路径）、config（`sandbox_uid/gid`）。
- Produces:
  - `SandboxSpec`（frozen: attempt_id, root: Path（worktree/tmp/run/socket 布局）, uid/gid, cgroup_path, limits（memory_bytes/cpu_quota/pids_max）, argv, env, mounts（ro binds: provider 镜像目录、/usr /lib /lib64 /bin /sbin 只读；rw: worktree；tmpfs: /tmp、/run/mesh）, broker_socket_host_path, netns_gateway_ip/port）
  - `SandboxHandle`（frozen: pid, cgroup_path, netns_id, veth_host, root；`async kill_all()`：cgroup kill + 回收 + 校验无残留 pid）
  - `SandboxManager(config)`：`async provision(spec) -> SandboxHandle`（建目录布局 0700/0755 精确模式 → cgroup2 `mesh/<attempt_id>` 写 limits → veth pair + 地址 + 仅到网关的 /32 路由 → 经 `unshare -m -p -i -u -n --fork --kill-child` 起 `sandbox_init`，传 spec JSON 于 stdin → 等待就绪哨兵（init 写入 ready fd）→ 校验子进程 uid/cgroup 成员 → 返回 handle）；**任一步失败 → 清理已建资源 → 抛 `SandboxUnavailableError(DaemonError)`，绝不回退裸跑**
  - `sandbox_init.py`：读 spec → `mount --make-rprivate /` → 构造新根（bind 镜像目录 + ro 系统目录 + tmpfs + worktree rw + /run/mesh 含 broker socket bind）→ `pivot_root` → 挂 /proc（新 pidns）→ 丢弃补充组/setgid/setuid → 空 HOME/XDG env → `os.execve` provider。**init 自身不持任何 token；就绪前任何失败以非零退出（fail-closed）**
  - 能力探测：`async sandbox_capabilities() -> dict`（unshare 可用、cgroup2 可写、可建 veth、可降权 → `{"sandbox": "linux_ns", "egress_enforced": True}`；任一不满足 → 报 degraded，不 claim）

- [ ] **Step 1: 写失败测试**（真实环境，root；非 root `pytest.fail("requires root + Linux ns")`）：① provision 起 `/usr/bin/id -u` 输出 == sandbox uid；② 沙箱内 `cat /proc/self/cgroup` 含 `mesh/<attempt_id>`；③ 沙箱内 `ip route` 无默认路由、仅网关 /32；④ 沙箱内写 `/tmp/x` 成功（tmpfs）、写 `/etc/x` EROFS/EACCES；⑤ 沙箱内看不到宿主 `work_dir` 其他 attempt 目录（新根不含）；⑥ limits 生效：`pids.max=64` 下 fork bomb 被杀而宿主无事；⑦ provision 中途失败（坏 uid）→ `SandboxUnavailableError` 且 cgroup/veth/目录全回收（`assert_no_leftovers`）；⑧ `kill_all` 后 cgroup 消失、无残留进程。
- [ ] **Step 2-4: RED→GREEN→覆盖率**（sandbox_init 的分支以失败注入测试覆盖：只读 bind 失败等经 spec 畸形触发）
- [ ] **Step 5: 提交**：`feat(daemon): namespace/cgroup 沙箱——fail-closed 真实隔离(MES-100 S-03 基座)`

## Task 6: S-02 唯一 ToolBroker 闸门

**Files:**
- Create: `daemon/src/mesh_runtime/broker.py`, `daemon/tests/unit/test_broker.py`
- Consumes: `api.RuntimeApiClient.request_approval`、task token（claim/renew 下发）、`GATE_TABLE` 依据 §3.3。
- Produces:
  - `GATE_TABLE: dict[str, GateSpec]`（动作→闸门唯一映射：`worktree.read`/`worktree.write`（mount scope，不经 broker）；`issue.read`/`issue.comment`/`issue.status`（task broker + scope）；`cross_issue.write`/`git.push`/`egress.grant`/`secret.use`（confirm_required）；`mount`/`privilege`/`daemon_control`/`cloud_metadata`（永久禁止）；未知动作 fail-closed 拒绝）
  - `ConfirmRequiredSignal`（frozen: action, targets, resume_context）——broker 抛出，supervisor 捕获后走 approvals 协议
  - `ToolBrokerServer(*, attempt_id, socket_path, sandbox_uid, cgroup_path, nonce, task_token, grants, api, clock)`：`async start()`（socket 0600，父目录 0700）/`async stop()`/`async rotate_task_token(token, expires_at)`/`async freeze()`（租约丢失时先关 broker）；连接校验：`SO_PEERCRED` uid==sandbox_uid → `/proc/<pid>/cgroup` 含本 attempt cgroup → 握手首帧 nonce 匹配 → 每方法查 GATE_TABLE + grants.permission + task token scope（`issue:read`/`issue:comment:write`/`issue:status:write`）+ 令牌桶限速（冻结速率）；`confirm_required` 动作 → 抛 `ConfirmRequiredSignal`（绝不挂起沙箱等待）；`secret.use` 仅返回动作结果，明文永不回沙箱
  - `ActionBroker`（git 写 grant：校验 exact repo/base/target ref/commit/diff digest/幂等键 → 换取一次性写凭证 → 代执行 push → 立即吊销）

- [ ] **Step 1: 写失败测试**（真实 unix socket + SO_PEERCRED；以当前 uid 模拟沙箱 peer，cgroup 校验用 `/proc/self/cgroup` 真值构造夹具）：① 正确 peer+nonce → `issue.read` 200（task token 经 httpx MockTransport 打 Mesh API 的桩）；② 错 nonce → 连接拒绝并审计；③ 非沙箱 uid → 拒绝；④ scope 外（他 issue）→ 403 语义 `resource_scope_mismatch`；⑤ 未声明 capability → `capability_not_granted`；⑥ `confirm_required` 动作返回 `CONFIRM_REQUIRED` + supervisor 侧 `api.request_approval` 被调且本 attempt 不再发 running 之外状态（server 置 awaiting_approval）；⑦ 限速：超频 → `rate_limited`；⑧ freeze 后一切调用连接失败；⑨ rotate 后旧 token 调用失败新 token 成功（桩 server 校验 Authorization）；⑩ ActionBroker 一次性：同 grant 二次用 → 拒。
- [ ] **Step 2-4: RED→GREEN→覆盖率**
- [ ] **Step 5: 提交**：`feat(daemon): S-02 唯一 ToolBroker 闸门——动作闸门映射/peer 三重校验/审批协议(MES-100)`

## Task 7: checkout helper 与 Git 凭证分离（§3.2）

**Files:**
- Create: `daemon/src/mesh_runtime/checkout.py`, `daemon/tests/unit/test_checkout.py`
- Consumes: `netguard.assert_public_url`、`api.report_checkout`、冻结 `config_snapshot.repo`。
- Produces:
  - `CheckoutHelper(*, git_bin, worktree: Path, clock)`：`async prepare(repo: FrozenRepo, allowed_repos: list[str], credentials: list[dict], *, is_platform_managed: bool) -> CheckoutResult`（URL 精确匹配冻结值 + allowed_repos 前缀规则 + platform_managed 时 assert_public_url → `git init` + 临时 remote（URL 不含凭证）+ `GIT_CONFIG_COUNT/KEY/VALUE` 注入 `http.extraheader` 只读凭证（env 级，不落盘/不进 remote URL）+ fetch 精确 base_sha + `reset --hard` + 本地 worktree 分支 `working_branch`；凭证使用后 env 清零）→ 上报 `cloning`→`ready`（commit_sha）
  - `async export_diff(api, *, lease_seq, max_bytes) -> str|None`（`git diff` 限长，先本地脱敏由 supervisor 传入的 redactor 执行，再 `diff_ready` 上报，返回 `diff_ref`）
  - `FrozenRepo.from_snapshot(dict)`（frozen: url/base_ref/base_sha）
  - `CheckoutError(DaemonError)`（reason code：`repo_not_allowed`/`private_address_forbidden`/`sha_mismatch`/`clone_failed`）

- [ ] **Step 1: 写失败测试**（真实 git，tmp 裸仓库做上游）：① 精确 SHA checkout 成功且 HEAD == base_sha；② remote URL 不含凭证（断言 `.git/config` 字节）；③ URL 不在 allowed_repos → `repo_not_allowed` 且无 git 进程；④ platform_managed + loopback URL → `private_address_forbidden`；⑤ base_sha 不存在 → `clone_failed`；⑥ diff 生成与上报（桩 api 收到 diff_ready + diff 文本）；⑦ 恶意 worktree 内 `.git/config` 篡改不影响后续 diff（helper 只读自有配置）。
- [ ] **Step 2-4: RED→GREEN→覆盖率**
- [ ] **Step 5: 提交**：`feat(daemon): checkout helper——精确 SHA/只读凭证分离/diff 上报(MES-100 §3.2)`

## Task 8: S-08 清理清单（cleanup）

**Files:**
- Create: `daemon/src/mesh_runtime/cleanup.py`, `daemon/tests/unit/test_cleanup.py`
- Consumes: journal `cleanup_state`、sandbox handle、broker/egress handle。
- Produces:
  - `ResourceManifest`（frozen 白名单：socket_path, cgroup_path, veth_host, mount_points, worktree, tmp_dir, run_dir；**仅接受 daemon 自己生成的路径**，拒绝 provider 提供值）
  - `AttemptCleaner(*, journal, clock)`：`async cleanup(attempt_id, manifest, handles) -> CleanupReport`——按 §3.6 顺序幂等执行，每步完成写 `cleanup_state`（`broker_closed|tokens_revoked|cgroup_killed|tmpfs_unmounted|artifacts_removed|spool_flushed|done`）；删除前校验：路径在 attempt 根内（`commonpath`）、`lstat` 非 symlink、owner 匹配；spool 未确认上传不清；全部销毁后才允许 `journal.delete`
  - `CleanupError(DaemonError)`（失败不吞：返回 report 并置 attempt isolated 语义上报）

- [ ] **Step 1: 写失败测试**：① 全资源清理后 manifest 路径全不存在、cgroup 消失、journal cleanup_state=done；② 幂等：二次调用 report 全 already_clean；③ symlink 指向日志外 → 拒绝删除且 report 记录；④ 白名单外路径（`/etc`）注入 → ValueError；⑤ spool 未 flush → 跳过 spool 删除；⑥ 中途失败（卸载 EBUSY）→ report 标失败位且不清 journal。
- [ ] **Step 2-4: RED→GREEN→覆盖率**
- [ ] **Step 5: 提交**：`feat(daemon): S-08 幂等清理清单——白名单/不跟随 symlink/journal 清理位(MES-100)`

## Task 9: 安全栈装配（attempt/app 接线 + redaction 扩面 + 能力上报）

**Files:**
- Modify: `daemon/src/mesh_runtime/attempt.py`, `daemon/src/mesh_runtime/app.py`, `daemon/src/mesh_runtime/redaction.py`, `daemon/src/mesh_runtime/doctor.py`, `daemon/src/mesh_runtime/inventory.py`, `daemon/src/mesh_runtime/config.py`, `daemon/src/mesh_runtime/cli.py`
- Test: `daemon/tests/unit/test_app.py`, `test_attempt.py`, `test_config.py`, `test_doctor.py`（扩展）+ `daemon/tests/contract/test_state_machine.py`（fake provider 经沙箱执行路径的合同测试；root 环境真实沙箱，非 root 以显式 `SandboxBackend.inline`（仅测试注入、心跳报 degraded、非运行时降级）走合同）

**Interfaces:**
- `AttemptSecurity`（frozen 束：sandbox_mgr, broker_factory, egress_factory, checkout, cleaner, redactor, secrets_holder）
- `attempt.supervise` 新时序：journal.put → checkout.prepare（repo 存在时）→ egress.start → broker.start → sandbox.provision（**失败 → `_send_terminal(failed, failure_reason="sandbox_violation")` + cleanup，不启动 provider**）→ report running → provider（A2 仍为 fake，其“进程”即沙箱内执行体；合同测试用沙箱内小脚本模拟）→ ToolRequested 事件经 broker → ConfirmRequired → `api.request_approval` 后本 attempt 交 server 处置（server 置 cancelled(awaiting_approval)，daemon 收到 409 attempt_terminal 即停）→ 终态：flush logs(sealed) + export_diff + build_result（summary/diff 经 redactor）→ cleanup（全量）→ journal.delete
- renew 成功 → `broker.rotate_task_token(info.task_token, ...)`
- `heartbeat_metadata`：增 `sandbox`/`egress`/`broker` 能力与 `egress_enforced` 布尔；health 依 doctor 探测降级
- `redaction.RedactionPipeline.redact_json(doc) -> (doc, hits)`（result/diff/附件元数据通道统一）

- [ ] **Step 1: 写失败测试**：app 级——claim 后 attempt 目录布局生成、沙箱真起（root）/合同模式（非 root）、broker socket 存在于 attempt run 目录、egress 端口监听、终态后全资源回收且 journal 空；sandbox 注入失败 → transition `failed/sandbox_violation` 且无 provider 启动；ToolRequested→broker 调用链；confirm_required→request_approval 被调且本 attempt 以 cancelled 收尾（桩 server 返 attempt_terminal 409）；redact_json 深层字段脱敏 + hit count；heartbeat metadata 能力位；doctor 沙箱检查 fail 路径；config 新键默认值与非法值。
- [ ] **Step 2-4: RED→GREEN→全量测试绿**
- [ ] **Step 5: 提交**：`feat(daemon): 安全栈装配——claim→checkout→sandbox→broker/egress→回流→清理(MES-100 A2)`

## Task 10: ISO-01~14 真实负向矩阵（S-03）

**Files:**
- Create: `daemon/tests/isolation/conftest.py`, `daemon/tests/isolation/test_iso_matrix.py`
- pyproject markers 增 `isolation`；CI workflow 增 matrix job（`sudo` 真跑）。

**要求（逐条对应用例，真实环境 max_concurrent=2，禁 mock/skip；非 root → `pytest.fail`）：**
- ISO-01: A 沙箱内读写 B 的 worktree/tmp/run/socket → ENOENT/EACCES，B inode 内容前后一致；
- ISO-02: A 读 `/proc/<B-pid>/environ|mem|fd` → 不可见（pidns 隔离）；
- ISO-03: A 经 localhost/unix socket 访问 B broker → 连接失败（netns+socket 不在 A 挂载）且 B broker 审计零合法调用；
- ISO-04: 沙箱读 daemon `/proc/<daemon-pid>/environ|mem|fd` 与 token 目录 → EACCES/不可见，输出与磁盘 `mesh_rt_`/`mesh_task_` 零命中；
- ISO-05: 沙箱扫 daemon 控制 socket/docker/ssh agent → 无挂载；
- ISO-06: 沙箱读宿主 HOME、云凭证路径（`~/.aws` 等夹具）、其他 workspace checkout → 不可见；
- ISO-07: A 终态后槽位复用起 C → C 的 worktree/tmp/socket/WAL/凭证目录不含 A 数据（字节扫描）；
- ISO-08: daemon 崩溃重启且 A 租约被 server 回收 → A 不恢复副作用（reconcile 报 daemon_restart），旧 lease_seq 上报 409（桩 server）；
- ISO-09: 仓库放置恶意 `.mcp.json`/`.claude/settings.json`/hooks/`CLAUDE.md`（beacon 文件+试图建连脚本）→ 沙箱执行后无额外进程/无 beacon 文件被创建/无 broker 外工具（扫描沙箱内监听端口与进程表）；
- ISO-10: provider 直接 `git push`、跨 issue 写、非白名单上传 → 无凭证+egress 拒绝，全部失败；
- ISO-11: 沙箱直连公网 IP/私有 DNS/raw socket/IPv4-mapped → 无路由 ENETUNREACH 或 EPERM（非 root 无 CAP_NET_RAW）；
- ISO-12: 经网关：先公网后私网答案/混合答案/CNAME 私网/重定向到元数据 → 每种拒绝，目标侧零 SYN（以本地计数服务器断言）；
- ISO-13: secret 跨日志 chunk/result/diff/附件元数据 → daemon 出口与（桩）server 落库只见 `***`；
- ISO-14: 并发 CPU/pid/mem/IO 洪泛与 fork bomb → 仅攻击 attempt 被 cgroup 杀死并以 timeout/failed 终结，daemon 与另一 attempt 心跳/续租正常（真实并发）。

- [ ] **Step 1: conftest**（双沙箱装配夹具、攻击载荷 = 沙箱内可执行小脚本、证据收集器）
- [ ] **Step 2: 逐用例 RED→GREEN**（14 个测试函数，每通过一组提交）
- [ ] **Step 3: 全矩阵跑通并导出证据**：`../.venv-daemon/bin/python -m pytest tests/isolation -v --junit-xml=../docs/evidence/mes-100/iso-matrix.xml`
- [ ] **Step 4: 提交**：`test(daemon): ISO-01~14 隔离红线真实负向矩阵全绿(MES-100 S-03)`

## Task 11: 真实联调（MES-98 server 已合入）

**Files:**
- Create: `daemon/tests/integration/real_server_e2e.py`, `docs/evidence/mes-100/integration-*.json`
- 前置：本地 compose 栈（127.0.0.1:8000，`MESH_DAEMON_TLS_REQUIRED=false`，dev auth）；公开 API 路径（§5.4.5 禁 psql seed）：建 workspace（onboarding/console）→ agent + 配置版本 → 设 `workspaces.settings.allowed_repos`（经 console API；无端点则以 runtime 注册同级的 settings PATCH）→ 建 pending runtime 取激活码 → daemon `activate`（metadata.capabilities 含 sandbox/egress/broker）→ 心跳 online → issue assign 触发 enqueue（§6.9 outbox）→ daemon claim → 沙箱执行 fake provider → 日志/终态/result 回流。
- 断言：runtime 状态 online；execution queued→claimed→running→completed；attempt lease_seq 递增；日志段经 SSE 可读且 secret 值为 `***`；DB（经 console API 读执行详情，不 psql）result schema_version=1、redaction.hit_count≥1（注入测试 secret 于凭证）；checkout 行 ready/diff_ready；task token 终态后吊销（再请求 401）。
- [ ] **Step 1: 脚本化 e2e（httpx 直连 + daemon 进程内运行）**
- [ ] **Step 2: 真跑并捕获证据 JSON/日志片段（脱敏）**
- [ ] **Step 3: 提交**：`test(daemon): 真实联调——激活→online→claim→沙箱执行→回流(MES-100)`

## Task 12: 覆盖率/文档/CI/PR

- [ ] **Step 1: 覆盖率实测**：`../.venv-daemon/bin/python -m pytest tests/unit tests/contract --cov=mesh_runtime --cov-report=term-missing`（整体与分支 ≥90%；新增文件逐行核对 <90% 补测试）
- [ ] **Step 2: ruff 与全量矩阵复跑**：`../.venv-daemon/bin/ruff check . && ../.venv-daemon/bin/python -m pytest -q`（unit+contract）+ isolation 复跑
- [ ] **Step 3: 文档**：`runtime-executor.md` 头部进度改“A2 已落地 + 矩阵/联调证据路径”；README daemon 段落同步；修正任何过时安全关卡表述
- [ ] **Step 4: CI**：`daemon-ci.yml` 增 isolation job（ubuntu-latest，`sudo -E .venv/bin/python -m pytest tests/isolation`）与 integration（需要栈，manual/受保护触发，外部 PR 不跑）
- [ ] **Step 5: 提交身份终检**：`git log @{u}..HEAD --format='%an <%ae> | %cn <%ce>'` 全为 cnwenf；`git log --format=%B | grep -i co-authored-by` 无输出
- [ ] **Step 6: push + PR**（PR 描述含：S-01~S-04 负向证据、ISO 矩阵 junit、联调证据摘要、覆盖率表）

## Self-Review

- **Spec 覆盖**：S-01（Task 4/5/9/10-ISO09）、S-02（Task 6/9/10-ISO10）、S-03（Task 5/10 全表）、S-04（Task 2/3/10-ISO11/12）、checkout（Task 7）、redactor 扩面（Task 9）、cleanup S-08（Task 8）、真实联调（Task 11）、契约对齐 MES-98（Task 1）、文档/CI/覆盖率（Task 12）。§1.4 provider manifest/probe 属 A3（inventory 已实现 probe 基座，本阶段仅补能力位）。§3.5 预算三层：server/provider 层属 P0/A3，daemon 层 cgroup/wall 限额本阶段随沙箱落地（Task 5 limits + Task 9 supervisor wall timeout 复用 A1 renew 语义）。§3.7/§3.9 的 spool 背压仍为 A3 增量（A1 已记注释；本阶段不扩）。
- **占位符扫描**：无 TBD/“类似 Task N”；每个 Task 含接口签名、测试清单、命令。
- **类型一致**：`SandboxSpec/SandboxHandle/AttemptSecurity/NetworkPolicy/ResourceManifest/ConfirmRequiredSignal` 命名在 Task 5/9/3/8/6 间一致；`task_token` 字段名与 MES-98 响应一致。
