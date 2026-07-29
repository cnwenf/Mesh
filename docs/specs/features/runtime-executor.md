# Runtime 本地执行体架构 Spec

> 状态：安全复评候选版
>
> 所属模块：`runtime` 的本地执行子系统；服务端调度、数据模型和机器 API 仍以 `runtime.md` 为权威。
>
> 适用二进制：`mesh-runtime`。本文不引入 `mesh-daemon` 别名。
>
> 首个 provider：固定版本 Claude Code CLI；后续 provider 必须实现同一适配契约并通过同等级安全门禁。

---

## 1. 功能描述与总体架构

### 1.1 目标与边界

`mesh-runtime` 把服务端已经入队、已经冻结配置的 `task_execution` 变成一次可审计的真实本地执行。它负责：

1. 激活、心跳、claim、续租、终态上报和崩溃对账；
2. checkout、provider 启动、日志流、预算、取消和清理；
3. attempt 级文件、进程、网络、凭证隔离；
4. 通过 task broker 暴露最小化的 Mesh 工具能力；
5. 在所有输出离开本机前完成首层脱敏。

它不负责重新解释 agent 配置、不接受仓库提供的 provider 配置、不自行扩大执行权限，也不把长期 `mesh_rt_` runtime token 交给任务沙箱。

### 1.2 进程与信任边界

```text
┌──────────────────────────── mesh-runtime（受信） ────────────────────────────┐
│ Runtime API client   Lease reconciler   Provider supervisor   Redactor       │
│         │                    │                    │                 │           │
│         ├──────── attempt journal（仅元数据，0600） ───────────────┤           │
│         │                    │                    │                 │           │
│         └────────────── Task broker（持 task token） ──────────────┐           │
│                                                                   │           │
│ Checkout helper（只读凭证）        Action broker（审批后一次性动作）│           │
└──────────────┬──────────────────────────┬──────────────────────────┘           │
               │                          │                                      │
        独立 Unix socket              受控 egress gateway                         │
               │                          │                                      │
┌──────────────▼──────── attempt sandbox（不可信） ─────────────────────────────┐
│ 非 root UID / 独立 user+mount+pid+net+ipc namespace / cgroup                  │
│ 空白 HOME、空白 XDG、只读 provider 配置、可写 worktree、tmpfs /tmp            │
│ 固定版本 provider CLI；无 runtime token、无 task token、无通用 git 写凭证     │
└──────────────────────────────────────────────────────────────────────────────┘
```

信任规则：

- checkout 仓库、issue 上下文、skill 内容、agent 输出和 provider 输出均为不可信数据；
- `mesh-runtime`、平台生成且只读挂载的 provider 配置、task broker、egress gateway 为受信控制面；
- 沙箱到 daemon 不存在通用控制 API，只存在 attempt 绑定、类型化、最小权限的 broker socket；
- `bypassPermissions` 仅关闭 provider 自身的交互确认，绝不绕过内核隔离、网络网关、凭证边界或 Mesh 审批。

### 1.3 组件职责

| 组件 | 职责 | 失败语义 |
| --- | --- | --- |
| Runtime API client | 激活、心跳、claim、renew、日志和终态上报 | TLS、鉴权或协议版本不满足即停止 claim |
| Lease reconciler | 按 `lease_seq` 续租、轮换 task token、取消过期 attempt | 无法确认租约时先停 provider，禁止离线继续产生副作用 |
| Checkout helper | 在 provider 启动前完成精确 repo/base SHA checkout | 只持仓库只读凭证；URL、解析 IP 或 SHA 不合规即失败 |
| Provider supervisor | 版本/能力自检、启动、超时、取消、usage 采集 | 版本或硬预算能力不匹配即 fail-closed |
| Task broker | 代持 task token，提供当前 attempt 的类型化 Mesh 工具 | peer、attempt、资源或速率不匹配即拒绝 |
| Action broker | 执行已经人工批准的高风险动作 | 仅接受服务端签名的一次性 action grant |
| Egress gateway | 默认拒绝、域名校验、可信解析、IP 过滤、建连钉死 | 任一跳无法验证即拒绝，不降级直连 |
| Redactor | 日志、结果、diff、附件元数据出机前脱敏 | 规则加载失败或通道未接入即禁止运行 |
| Attempt journal | 崩溃恢复所需的租约和上传水位 | 只记元数据，不记 prompt、输出、token 或 secret |

### 1.4 Provider 适配契约

每个 provider 版本随发布包携带不可变 capability manifest：

```yaml
provider: claude-code
version: "<pinned-version>"
binary_sha256: "<release-pinned-sha256>"
required_flags:
  - --print
  - --output-format
  - --input-format
  - --bare
  - --disable-slash-commands
  - --no-session-persistence
  - --setting-sources
  - --strict-mcp-config
  - --mcp-config
  - --settings
  - --system-prompt-file
  - --tools
  - --disallowed-tools
  - --permission-mode
  - --max-budget-usd
hard_limits:
  usd_budget: true
  wall_timeout: true
```

daemon 启动时校验二进制绝对路径、文件摘要、版本和必需 flags；任一不符，runtime 报 `degraded` 且不领取要求该 provider 的任务。禁止 PATH 搜索、自动升级、插件自动发现和运行时下载 provider。

本地探测由 `mesh-runtime doctor` 和 daemon 启动自检共用同一实现：

1. 仅扫描管理员配置的绝对路径和签名发布包默认目录，不运行仓库内、HOME 内或 PATH 中偶然同名的二进制；
2. 先校验 owner/mode、签名和 SHA-256，再用无网络、空 HOME 的探测沙箱读取 `--version` 与帮助能力；
3. 以 capability manifest 对照必需 flags，并运行一个只调用平台 fake broker 的正向 probe；
4. 同时运行 §1.5 的恶意配置负向 fixture；正向 broker 不通或任一恶意 fixture 生效，都把该 provider 标记为 unavailable；
5. 心跳只上报通过探测的 `{provider, version, capabilities, binary_sha256}`，server 据此匹配任务；探测结果在二进制 inode/mtime/hash 改变后立即失效。

首版调用由 daemon 构造固定 argv，prompt 只走 stdin，不经过 shell：

```text
/opt/mesh/providers/claude/<version>/claude
  --print
  --input-format stream-json
  --output-format stream-json
  --bare
  --disable-slash-commands
  --no-session-persistence
  --setting-sources ""
  --strict-mcp-config
  --mcp-config /run/mesh-attempt/mcp.json
  --settings /run/mesh-attempt/settings.json
  --system-prompt-file /run/mesh-attempt/system.md
  --tools <daemon-generated-allowlist>
  --disallowed-tools <daemon-generated-denylist>
  --permission-mode bypassPermissions
  --max-budget-usd <frozen-budget>
```

`mcp.json` 只登记平台 task broker；`settings.json` 只来自冻结快照和 daemon 默认值；`system.md` 只含可信平台策略和冻结 AgentConfig 指令，不拼接 issue/仓库/member output。三者由 daemon 在 attempt 私有 tmpfs 中创建、root/daemon UID 所有、只读挂载到沙箱，任务不能改写。provider 组合必须先通过“平台 broker 可用”的正向 probe；不能用会同时禁掉显式 broker 的模式冒充隔离。

### 1.5 S-01：不可信 Claude 配置隔离

以下要求同时成立，不能只依赖其中一个开关：

1. 沙箱使用 attempt 私有空白 `HOME`，并清空/重定向 `XDG_CONFIG_HOME`、`XDG_DATA_HOME`、`XDG_CACHE_HOME`；不挂载宿主用户目录、daemon HOME 或历史 provider 状态；
2. `--bare --disable-slash-commands --no-session-persistence --setting-sources ""` 禁止自动加载用户级、项目级、本地级设置、记忆、skill、plugin、hook、自定义命令、项目指令和历史 session；sandbox image 不包含 provider 的 admin-managed policy/config，根文件系统只读；
3. `--strict-mcp-config` 配合显式 `--mcp-config`，MCP 唯一来源是平台 task broker；
4. 禁止任务覆盖 argv，禁止 `--add-dir`、`--plugin-dir`、`--plugin-url`、`--agent` 等扩大加载面的参数；
5. checkout 内 `.mcp.json`、`.claude/settings.json`、`.claude/settings.local.json`、hooks、`CLAUDE.md` 仅作为普通仓库文件可见，**不得被自动解释、加载或执行**；
6. provider 版本升级前运行恶意 fixture：上述文件分别尝试启动 beacon MCP、执行 hook、改写 settings 和注入项目指令；只要发生进程启动、socket 建连、指令生效或 broker 外工具出现，升级门禁失败。

安全性不依赖 provider 提示词遵从。即使 provider 把恶意文件当作指令主动执行，沙箱仍无长期 token、通用写凭证、直连网络和 daemon 控制能力。

---

## 2. 本地状态与服务端契约

### 2.1 冻结的 AttemptSpec

claim 成功响应必须包含版本化、不可变的 `AttemptSpec`，至少冻结：

| 分组 | 字段 |
| --- | --- |
| 身份 | workspace、execution、attempt、agent、runtime、`lease_seq` |
| provider | provider、固定版本、model、effort、system instructions |
| 上下文 | repo URL、base SHA、trigger、issue/project 资源边界、resume context |
| 工具 | skill versions、`capability_grants`、允许的 task broker 方法 |
| 预算 | USD、token、turn、wall time、idle time、最大日志/结果/diff/附件大小 |
| 网络 | 允许的 scheme/host/port/method、重定向上限、上传大小 |
| 数据处理 | redaction rule version、保留策略、敏感输出处置 |

server 以 `config_snapshot` 为唯一冻结真源；daemon 不从 agent 当前配置重新拼装，不接受心跳请求体覆盖。AttemptSpec 带 schema version 和服务端签名/摘要，未知版本或摘要不一致即拒绝执行。

### 2.2 S-05：task token 与 broker

- server 为每个 attempt 签发 `mesh_task_` 短期 token，scope 精确到 workspace、attempt、agent、当前 issue/project 和允许方法；
- TTL 取 `min(租约剩余时间 + 续租宽限, 5 分钟)`；每次 renew 返回新 token，server 在新 token 生效后立即吊销上一枚；
- task token 只进入 daemon 内的 task broker，**不进入沙箱 env、文件、stdin、provider settings 或日志**；
- broker socket 按 attempt 隔离。daemon 以 peer UID、sandbox identity、attempt nonce 三者校验调用者；
- server 每次调用同时校验 attempt 仍在途、`lease_seq`、runtime 归属和资源 scope，并按 token + attempt 双维度限速；
- terminal、reclaim、freeze、审批挂起或 runtime 下线时，token 吊销与状态迁移同事务完成；daemon 先关闭 broker，再结束沙箱。

`attempt_task_tokens` 由 runtime 模块 owns：`id/workspace_id/attempt_id/runtime_id/lease_seq/token_hash/scopes JSONB/expires_at/revoked_at/created_at`，带同租户复合 FK 和 fail-closed RLS；同一 attempt 只允许一枚 active token 的部分唯一索引。明文只在 claim/renew 响应中交给 daemon 一次，表内只存 SHA-256。该类型已登记于 auth.md §2.5.1，绝不复用 `api_tokens` 或成员角色。

### 2.3 本地文件与权限

| 对象 | 位置/介质 | 权限与生命周期 |
| --- | --- | --- |
| runtime token | OS keyring；无 keyring 时专用文件 | `O_NOFOLLOW|O_CLOEXEC` 打开，普通文件、daemon UID、0600，父目录 0700 |
| attempt journal | daemon 私有 state 目录 | 0600；只含 IDs、lease、offset、sandbox handle、cleanup 状态 |
| broker socket | attempt 私有 runtime 目录 | 父目录 0700、socket 0600；沙箱只挂载自己的 socket |
| provider 配置 | attempt tmpfs | daemon 所有、只读挂载；终态卸载并清零 |
| 短期凭证 | broker 内存或 attempt tmpfs | 0600、无符号链接；动作结束立即清零，终态兜底清理 |
| worktree | attempt 专属目录 | 仅该沙箱可写；结束按产物策略导出后销毁 |
| WAL/spool | attempt tmpfs 优先 | **先脱敏再落盘**；磁盘后备仅允许密文和 0600 |

daemon 读取 token 文件时必须 `lstat/open/fstat` 交叉确认：无 symlink、regular file、owner 精确匹配、mode 精确 0600、父目录 mode 0700。任一不符立即退出，不“尽力修复”后继续。

### 2.4 S-11：runtime token 单一真源迁移

服务端 P0 契约必须先完成以下迁移，daemon 开发不得依赖双写状态：

1. `runtimes.runtime_token_hash` 是唯一真源；激活、轮换、暂停、decommission、软删除只更新/清空该字段；
2. 迁移前先吊销并删除 runtime 关联的旧 `api_tokens` 行，再删除 `runtime_token_id` 外键和列；
3. `daemon_auth` 只校验 runtime hash，不回查 `api_tokens`，bootstrap/响应也不返回 `runtime_token_id`；
4. 生命周期迁移与 hash 清空同事务；旧 token 在 commit 后立即得到 401；
5. 回归测试必须证明 `mesh_rt_` 永不进入 `api_tokens`，存量迁移后无孤儿 token，暂停/轮换/删除均即时吊销。

### 2.5 S-06：终态结果与全通道脱敏

所有出机通道统一走同一个 `RedactionPipeline`：

```text
provider stream / tool output / diff / result / attachment metadata
  → exact secret matcher
  → encoding/分片边界 matcher
  → 结构化敏感字段过滤
  → size/schema 校验
  → redacted payload
  → upload / persist
```

daemon 先脱敏；server 再兜底，不能相信 daemon 已处理：

- `backend/src/mesh/runtime/attempts.py` 写终态 `result` 前调用统一 `redact_text/redact_json`；
- `backend/src/mesh/runtime/checkout.py` 写 `diff` 前调用同一服务端脱敏器；
- 日志命中替换为 `***` 并记录不含原文的 hit count；
- result/diff 命中后只持久化脱敏值并产生安全告警；评论、附件发布通道仍按 README §6.16 拦截；
- 测试覆盖跨 chunk、base64/URL 编码、JSON 深层字段、result、diff、日志、评论和附件，数据库与对象存储中均不得出现原 secret。

### 2.6 Server P0 协议与模型变更

现有 `/api/v1/daemon/*` 路径和 execution/attempt 状态机保持不变；下表是 daemon 开发前必须补齐的兼容升级，不另造平行调度 API：

| 现有接口/模型 | P0 变更 | 兼容与失败语义 |
| --- | --- | --- |
| activate/heartbeat | 加 `protocol_version`、provider manifest、sandbox/broker/egress/budget 能力 | server 只派发协议交集内任务；缺安全能力为 degraded |
| claim 响应 | `config_snapshot` 补完整 AgentConfig、provider/model/effort/system instructions、冻结预算/网络/数据策略；attempt 补 daemon-only task token、到期时间和批准后的 action grants | 新 daemon 对缺字段 fail-closed；server 按 runtime protocol version 决定是否可 claim |
| renew 响应 | 除新 `lease_seq/expires_at` 外原子返回轮换 task token | 旧 task token 同事务吊销；daemon 更新 broker 后继续 |
| attempt transition | 把松散 `result` 收紧为版本化 schema，含 provider session id、model、usage、outcome、artifact refs 和 redaction summary | 大小/schema 不合法 422；旧 lease 409；server 持久化前兜底脱敏 |
| logs | 保留 `lease_seq + stream + start_offset + lines + sealed` | daemon 以脱敏后 UTF-8 bytes 计算 offset；409 时停止上传并对账 |
| checkout | 保留 checkout 状态与 diff 上报 | server 在对象存储写入前兜底脱敏；diff 超限转 artifact summary |
| task token auth | 新增 `mesh_task_` principal 和 route/method/resource scope 校验 | 不映射为 member/PAT；不能调用 daemon API、token 管理或 scope 外资源 |
| `task_executions.config_snapshot` | 冻结 `AttemptSpec` 所需字段、预算水位与 snapshot schema version | 在途不受 agent 后续改配影响 |
| runtime token 存储 | 执行 §2.4 单真源迁移 | migration 完成前真实 daemon 不启用 |

当前 claim 里把通用 credential value 映射成任意 env 的契约对真实 provider 不再适用：repo 只读凭证交 checkout helper，task token 交 broker，高危 secret 交 action broker；只有明确声明为低敏、任务内必需且通过 reserved-env 检查的值才允许进入沙箱。

---

## 3. 接口与执行流程

### 3.1 启动、claim 与续租

注册与保活复用 `runtime.md` 既有三步链路：

1. 人类或 `mesh runtime register` 调控制台 API 创建 pending runtime，取得一次性激活码和签名发布包信息；
2. 用户验签安装 `mesh-runtime`，激活码只从 0600 文件或 stdin 读取；
3. daemon 调 `POST /api/v1/daemon/runtimes:activate`，一次性取得 `runtime_id`、`mesh_rt_` token 和服务端给定的 heartbeat interval；
4. token 按 §2.3 持久化后立即清零激活输入；激活响应未安全落盘则必须用新激活码重来，不把旧响应写日志；
5. 独立 heartbeat loop 默认按服务端返回的 15 秒间隔运行并带抖动，不被 claim/provider 阻塞；连续 45 秒无有效心跳由 server 判 offline；
6. heartbeat 上报 daemon/provider/sandbox 能力与真实本地负载，接收 cancel、freeze、凭证轮换和 context append 下行指令；服务端负载仍为调度真源，不信 daemon 自报扩大容量。

claim 与执行时序：

1. daemon 校验安装包签名、provider manifest、token 文件和沙箱能力；
2. 仅当状态 online 且本地 semaphore 有空槽时调用既有 `executions:claim`；同一 runtime 的 claim 调度器串行填槽，实际容量仍由 server 锁行裁决；
3. 领到任务后先持久化最小 journal，再创建 checkout、broker、gateway policy 和 sandbox；
4. provider 启动成功才上报 running；claim 到 running 超时则失败清理；
5. renew 携带 `attempt_id + lease_seq`，原子获得新 lease 和轮换后的 task token；
6. renew 连续失败或 lease 安全窗口耗尽，立即冻结 broker/egress，终止 provider，等待 server 对账；
7. terminal 上报使用 fencing；server 成功确认后再移除 journal。重启发现 journal 时先向 server 对账，不凭本地状态猜测续跑。

空队列 204 采用 full-jitter 指数退避：1 秒起、15 秒封顶；成功 claim 立即重置，直到填满本地槽位。网络/5xx 从 2 秒退避到 60 秒，429 严格遵守 `Retry-After`，401 停止 claim 并进入 isolated。heartbeat loop 和 renew loop 不使用 claim 退避。显式取消优先于新 claim；daemon 退出先停止 claim，再在宽限期内终止/上报在途 attempt。

失败重试不在 daemon 内原地重跑：可归因的 provider/任务失败按 fenced terminal 上报；进程崩溃或失联由 server reaper 回收 execution，再由后续 claim 创建新的 attempt。`max_attempts`、优先级和是否可重试只由 server 决定。

### 3.2 checkout 与 Git 凭证分离

- repo URL、base SHA 和 allowed repos 来自冻结快照；
- checkout helper 位于沙箱外，只使用目标仓库、只读、短期凭证；凭证不进入 worktree 的 remote URL、git config 或 provider env；
- provider 启动时 checkout 已完成，沙箱没有仓库写凭证；
- `git diff/status` 可在 worktree 内执行；直接 `git push` 即使 provider 允许 shell，也因无凭证、无直连网络而失败；
- push/建 PR 只由 action broker 在人工批准后执行：校验 exact repo、base、target ref、commit、diff digest 和幂等键，换取一次性写 grant，动作完成即吊销；
- 读/写凭证由不同签发路径、不同 scope、不同存储句柄承载，不允许把只读 token 升级或复用为写 token。

### 3.3 S-02：动作到闸门的闭环

| 动作 | 冻结权限 | 不可绕过的闸门 | 无 broker 时 |
| --- | --- | --- | --- |
| 读当前 worktree、生成 diff | `read_only` | mount scope + cgroup | 只可读本 attempt |
| 修改当前 worktree | `write` | mount scope；不可触达宿主/其他 attempt | 只可写本 attempt |
| 读取当前 issue/project | `read_only` | task broker + task token 资源 scope | 无 token，401/连接失败 |
| 评论/更新当前 issue | `write` | task broker schema、scope、限速和幂等键 | 无 token，无法调用 Mesh API |
| 跨 issue/跨项目或批量写 | `confirm_required` | 人工 approval → exact targets 的一次性 delegation grant | task token scope 拒绝 |
| push/建 PR | `confirm_required` | 人工 approval → action broker 校验并代执行 | 无 git 写凭证且网络策略拒绝 |
| 非白名单出站/上传 | `confirm_required` | 人工 approval → 新 attempt 的精确临时 egress grant | egress gateway 默认拒绝 |
| 使用敏感凭证完成指定动作 | `confirm_required` | broker 代持 secret，仅返回动作结果 | 永不返回 secret 明文 |
| mount、提权、宿主进程、daemon 控制面、云元数据 | 永久禁止 | kernel policy + socket/网络隔离 | approval 也不能放行 |
| 未知 capability/未知工具 | 永久禁止 | allowlist fail-closed | 拒绝 |

`confirm_required` 的唯一协议沿用 README §6.10：当前 attempt 进入 `cancelled(awaiting_approval)`，租约结束、task token 吊销、容量释放；批准后创建新 attempt，凭结构化 `resume_context` 续跑。禁止把高权限沙箱挂起等待批准。

### 3.4 S-04：可信解析、IP 过滤与建连钉死

所有沙箱出站流量强制进入独立 network namespace 外的 egress gateway；沙箱没有默认直连路由，修改 `/etc/hosts`、自带 DNS 或 raw socket 均不能绕过。

每次连接执行：

1. 规范化 URL，拒绝 userinfo、混淆 IP、非白名单 scheme/host/port/method；
2. 只用 gateway 配置的可信 resolver 解析完整 CNAME 链；
3. 收集全部 A/AAAA，并把 IPv4-mapped IPv6 归一化；
4. 对**每一个**候选 IP 过滤 loopback、private、link-local、multicast、reserved、benchmark、文档网段和云元数据网段；答案中只要混入一个禁用 IP，整次请求拒绝；
5. 从已验证集合选 IP 并直接向该 IP 建连，保留原 host 作为 TLS SNI/证书校验和 HTTP Host；DNS 结果不再交给下层库重解析；
6. pin 仅在该连接和 DNS TTL 内有效；连接池超过 TTL 关闭并重新走全流程；
7. 3xx 不自动跟随。每一跳重新执行 URL allowlist、可信解析、全 IP 过滤和建连，且受冻结的最大跳数限制；
8. CONNECT 只允许批准的 host/port，仍由 gateway 选择并钉死 IP；禁止任意 TCP tunnel。

自托管 runtime 也必须提供同等 gateway，无法证明强制路由时报告 `egress_enforced=false`，server 不向其派发要求网络能力的执行。

### 3.5 S-07：三层预算

预算在入队时冻结，三层同时执行：

1. **server 层**：claim 事务锁定工作区/agent 预算水位，剩余额度不足不派发；并发 claim 不能超卖；
2. **provider 层**：固定版本能力清单证明支持硬 USD/token/turn 限制，启动时传入对应 flag；缺能力则 fail-closed；
3. **daemon 层**：wall/idle timeout、cgroup CPU/memory/pids/IO、日志/结果/diff/附件字节上限，先 TERM 后 KILL。

provider usage 用于实时截断和服务端核账。最终上报的 usage 是审计材料，不是唯一执法点；异常偏差触发 runtime 隔离和告警。

### 3.6 S-08：清理清单

每个终态、取消、reclaim、daemon 重启对账都执行幂等 cleanup：

1. 关闭 task broker 与 egress grant；
2. 吊销 task token、action grant、repo credentials；
3. TERM/KILL 整个 cgroup，确认无遗留进程；
4. 卸载并清零 secret/provider tmpfs；
5. 删除 socket、namespace、veth、cgroup、worktree 和临时 remote config；
6. 只在 redacted spool 全部确认上传后清除 spool；
7. 更新 journal cleanup 位；全部资源销毁后才删除 journal。

清理器以白名单资源清单工作，不接受 provider 提供的路径；symlink 不跟随，删除目标必须位于 attempt 根且 inode/owner 匹配。

### 3.7 S-09：不可信上下文与 squad 聚合

- system policy 与不可信内容使用结构化字段传入，不把 issue、评论、仓库文件或成员输出拼进 system 指令；
- 每段不可信内容带来源、资源 ID、大小和随机边界标记；边界由 server 生成，不允许内容自选；
- squad member 输出进入 leader/aggregator 前再次包为 `untrusted_member_output`，不得作为工具授权、审批结果或 system 指令解释；
- assign、mention、chat、autopilot、integration、squad 必须共用唯一 snapshot builder；mention 路径不得直接以空 config enqueue；
- parser 对 tool call、result、resume context 做 schema、深度、条数和字节上限校验，未知字段/畸形对象丢弃并审计；
- provider thinking 不入日志、结果、resume context 或 squad 聚合。

### 3.8 S-10：socket 与 reserved env

daemon 从空 env 构造 provider 环境，只允许固定 locale、attempt IDs、无敏感值的 provider 变量和 action-specific handle。客户端配置合并后再次删除/拒绝所有保留前缀和敏感名称，包括 token、credential、proxy、HOME/XDG、provider settings、动态加载和云凭证变量。

broker socket 的父目录 0700、socket 0600；服务端用 `SO_PEERCRED` 校验 UID，并校验该 UID 所属 sandbox/cgroup 与 attempt nonce。socket 不暴露 runtime token、任意 HTTP 转发、文件读取、shell 或凭证读取方法。

### 3.9 输出、会话与 usage 回流

provider supervisor 逐条解析 `stream-json`，只接受固定 schema 的文本、tool、usage、session 和 terminal 记录；未知/超大记录丢弃并计入诊断。原始 provider stream 不落盘：

1. 文本/tool 摘要经 RedactionPipeline 后，按 stream 分批调用现有 logs 接口；
2. 批次满足任一条件即发送：64 行、256 KiB 或 500 ms；`start_offset` 按**脱敏后的 UTF-8 bytes**单调计算；
3. 断网时只把已经脱敏的 batch 写入 tmpfs spool，按 `(attempt, stream, start_offset)` 幂等补传；spool 达冻结上限就背压 provider，超时后终止；
4. server 的 WS/SSE 主/降级通道继续读取同一日志 offset，不让 daemon 直接面向浏览器；
5. provider session id 只作审计和诊断，不是权限凭证。MVP 的审批/重试使用 server 的结构化 `resume_context`，不跨 attempt 恢复 provider HOME；未来若恢复原生 session，必须把加密 session artifact 纳入相同 fencing/脱敏/大小门禁。

终态 `result` schema：

```json
{
  "schema_version": 1,
  "provider": {
    "name": "claude-code",
    "version": "<pinned-version>",
    "model": "<frozen-model>",
    "session_id": "<provider-session-id>"
  },
  "usage": {
    "input_tokens": 0,
    "cache_creation_tokens": 0,
    "cache_read_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "turns": 0,
    "cost_usd": "0.000000"
  },
  "outcome": {
    "exit_code": 0,
    "summary": "<redacted>",
    "termination": "completed"
  },
  "artifacts": {
    "checkout_id": "<id>",
    "diff_ref": "<server-issued-ref-or-null>"
  },
  "redaction": {
    "rule_version": "<version>",
    "hit_count": 0
  }
}
```

金额使用 decimal string，token/turn 为非负整数；server 以 provider usage 与工作区聚合台账交叉核账，不能信任 daemon 自报放宽预算。

---

## 4. UI/UX 与部署

### 4.1 注册和诊断

注册向导仍是“创建 runtime → 复制一次性安装命令 → 等待上线”。上线前执行本机能力检查，详情页用可行动的状态展示：

| 状态 | 用户看到的原因 | 主操作 |
| --- | --- | --- |
| Online | 沙箱、provider、broker、egress、预算能力均通过 | 查看执行 |
| Degraded | 精确列出缺失能力和受影响任务类型 | 查看修复命令 |
| Paused | 管理员暂停，不 claim 新任务 | 恢复 |
| Isolated | 安全异常、usage 偏差或清理失败 | 导出脱敏诊断 / 重新注册 |

不得用“运行失败”一个泛化状态掩盖 provider 版本不兼容、egress 不可强制、token 文件权限错误或 sandbox 不可用。

### 4.2 执行详情

执行详情按 attempt 展示：

- provider/version/model、冻结预算和实际 usage；
- claim/running/approval/requeue/terminal 时间线；
- 日志续传水位、取消原因、租约回收原因；
- 高风险动作的“请求内容—审批人—精确 grant—执行结果”；
- 产出 diff/result 的脱敏标识和安全告警，不显示 secret 原文；
- 安全失败给出固定 reason code，不回显内部路径、token、IP 解析详情或 provider 原始 thinking。

### 4.3 S-12：发布与运行

- 唯一二进制和服务名均为 `mesh-runtime`；
- 发布物必须带版本、SHA-256 和签名，安装器先验签再原子替换；
- daemon 非 root 运行；需要创建 namespace/cgroup/网络策略的最小特权 helper 独立进程、固定 RPC、固定 allowlist；
- provider、sandbox runtime 和 helper 版本均随 heartbeat 上报，server 可以按最低安全版本拒绝 claim；
- 升级失败回滚到上一签名版本，不保留未签名二进制。

关键配置项：

| 配置 | 来源 | 规则 |
| --- | --- | --- |
| server URL | 管理员配置 | HTTPS；host allowlist；禁止任务覆盖 |
| state/work root | 安装配置 | 绝对路径、daemon UID 所有、分别 0700 |
| max concurrent | server runtime 配置 | daemon 可因本机资源下调，不能上调 |
| provider path/version | 签名发布配置 | 绝对路径 + manifest + SHA-256 |
| sandbox backend | 安装配置 | 仅登记并通过 doctor 的 backend |
| egress resolver/policy | 管理员/平台策略 | daemon 与任务只读；任务不能自定义 resolver |
| heartbeat/lease/poll | server 响应 | 本地仅应用边界和 jitter，不放宽 |
| log/spool limits | 冻结 AttemptSpec 与 daemon 上限 | 取二者更严格值 |

### 4.4 分阶段实现建议

| 阶段 | 范围 | 放行条件 |
| --- | --- | --- |
| P0 Server 契约 | 完整 AgentConfig/AttemptSpec、task token、预算核账、result/diff 兜底脱敏、runtime token 单真源迁移 | 合同测试、迁移/吊销负向测试通过 |
| A1 daemon 骨架 | 激活/心跳/claim/renew/对账、doctor、journal、fake provider | 无真实 LLM/secret，状态机和崩溃恢复通过 |
| A2 安全执行面 | namespace/cgroup、task/action broker、egress gateway、checkout helper、redactor、cleanup | §5.2 全部红线通过 |
| A3 真实 provider | 固定 Claude Code 适配、预算、流式解析、session/usage/result | provider manifest 与恶意 fixture 通过 |
| B 真 LLM e2e | assign/mention → 真 claim → 真调用 → tool/approval → diff/result/comment/status | 受保护 workflow 全绿 |
| 最终放行 | 安全复测、运维手册、回滚演练、成本告警 | 明确安全审核通过后启用生产 |

---

## 5. 安全要求与验收门禁

### 5.1 SEC-A～K 检查表

| 域 | 必须满足的要求 | 验证入口 |
| --- | --- | --- |
| SEC-A 令牌 | runtime/token 哈希单一真源；task token 短 TTL、轮换、限速、即时吊销；沙箱不可见长期/任务 token；TLS | §2.2、§2.4、T36-R1/R2 |
| SEC-B 沙箱 | 非 root、独立 namespace/cgroup、fail-closed；固定 provider；不加载仓库配置；跨 attempt 隔离 | §1.4～§1.5、§5.2 |
| SEC-C secret | 空 env allowlist、reserved env 二次检查、短期凭证、全通道双层脱敏、诊断无 secret | §2.3、§2.5、§3.8 |
| SEC-D Git | 冻结 URL/SHA、allowlist、IP 重校验；读写凭证分离；push 走审批 broker | §3.2～§3.3 |
| SEC-E 网络 | 默认 deny、强制 gateway、可信解析、全 IP 过滤、建连钉死、逐跳重校验 | §3.4 |
| SEC-F attempt | 文件/进程/网络/credential 隔离；崩溃对账；journal 仅元数据；完整清理 | §1.2、§3.1、§3.6 |
| SEC-G 预算 | config snapshot 冻结；server/provider/daemon 三层限制；异常核账 | §2.1、§3.5 |
| SEC-H 上下文 | 不可信内容结构隔离；member output 不可信；高风险人工闸门；parser 限制；无 thinking | §3.3、§3.7 |
| SEC-I 供应链 | 签名发布、摘要、固定版本、依赖/镜像扫描、禁止自动升级 | §1.4、§4.3、§5.4 |
| SEC-J 工具 | task token 最小资源 scope；MCP 仅 task broker；daemon 非 root；socket peer 校验 | §2.2、§3.8 |
| SEC-K 审计 | daemon 与 server 两侧 append-only 安全审计、server 时间为准、无敏感正文 | §2.3、§4.2 |

### 5.2 S-03：隔离红线负向测试矩阵

本矩阵必须在真实 Linux namespace/cgroup/network 环境、`max_concurrent >= 2` 下执行，禁止 mock、禁止 skip。任一用例失败即阻断受保护分支和发布：

| ID | 并发攻击 | 必须断言 |
| --- | --- | --- |
| ISO-01 | attempt A 读/写 B 的 worktree、tmp、provider 配置、socket | ENOENT/EACCES；B 内容和 inode 无变化 |
| ISO-02 | A 访问 `/proc/<pid-B>/environ`、`mem`、`fd`、cgroup | 不可见或 EACCES；无环境/内存字节泄漏 |
| ISO-03 | A 通过 localhost、Unix socket、共享 IPC 访问 B broker/provider | 连接失败；B 审计无合法调用 |
| ISO-04 | 沙箱访问 daemon `/proc/.../environ`、`mem`、fd、keyring/token 目录 | 不可见/EACCES；`mesh_rt_`、`mesh_task_` 零命中 |
| ISO-05 | 沙箱扫描/调用 daemon 控制 socket、helper socket、Docker/SSH agent | 无挂载或 peer 校验拒绝 |
| ISO-06 | 沙箱读取宿主 HOME、云凭证路径、其他 workspace checkout | 不可见；输出与磁盘零命中 |
| ISO-07 | A 终态后复用并发槽启动 C | C 的 worktree/tmp/socket/WAL/credential 不含 A 数据 |
| ISO-08 | daemon 崩溃重启，A 租约已被 server 回收 | A 不恢复副作用；旧 task token/lease_seq 被拒 |
| ISO-09 | 仓库放置恶意 MCP/settings/hooks/CLAUDE.md | 无额外进程/网络/工具/指令加载 |
| ISO-10 | provider 直接 push、跨 issue 写、非白名单上传 | 无 broker/approval 时全部失败 |
| ISO-11 | 沙箱绕过 gateway 直连 IP、私有 DNS、raw socket、IPv4-mapped 地址 | 无直连路由；gateway 拒绝 |
| ISO-12 | DNS 先公网后私网、混合答案、CNAME 私网、重定向元数据 | 每种均拒绝，未向禁用 IP 发 SYN |
| ISO-13 | secret 跨日志 chunk、result、diff、评论、附件输出 | daemon/server/DB/对象存储只见脱敏值或被拦截 |
| ISO-14 | 并发 CPU/pid/memory/IO 洪泛与 fork bomb | 只终止攻击 attempt，daemon 和另一 attempt 正常续租 |

### 5.3 S-01～S-13 关闭表

| 项 | 设计回答 | 开发/测试门禁 |
| --- | --- | --- |
| S-01 HIGH | 空 HOME/XDG + bare/no sources/no session + strict MCP + 恶意 fixture | §1.5、ISO-09 |
| S-02 HIGH | 动作—权限—硬闸门映射；git 读写凭证分离；broker 代执行 | §3.2～§3.3、ISO-10 |
| S-03 HIGH | A→B、沙箱→daemon、并发/清理真实负向矩阵 | §5.2 全表 |
| S-04 HIGH | 可信解析→全 IP 过滤→直连钉死；redirect 逐跳重验 | §3.4、ISO-11/12 |
| S-05 MEDIUM | broker 代持 task token；短 TTL、续租轮换、限速、即时吊销 | §2.2 |
| S-06 MEDIUM | daemon 首层 + result/diff 服务端兜底 | §2.5、ISO-13 |
| S-07 MEDIUM | 预算冻结；server/provider/daemon 三层硬限制 | §2.1、§3.5 |
| S-08 MEDIUM | 先脱敏后 WAL；tmpfs/0600；完整清理清单 | §2.3、§3.6 |
| S-09 MEDIUM | agent/member 输出不可信；统一 snapshot builder；parser 限制 | §3.7 |
| S-10 MEDIUM | reserved env 复查；token file 和 socket owner/mode/peer 校验 | §2.3、§3.8 |
| S-11 MEDIUM | 删除 `api_tokens` 双写和 `runtime_token_id` 的迁移闭环 | §2.4 |
| S-12 LOW | 唯一名称 `mesh-runtime` | 文档、包名、service、进程扫描 |
| S-13 MEDIUM | 依赖扫描、签名发布、真实 LLM workflow 保护 | §5.4 |

### 5.4 S-13：CI、供应链与真实 LLM 门禁

1. Python 依赖锁文件扫描和 `pip-audit` 为阻断检查；容器/二进制做漏洞、许可证和 secret 扫描；
2. 发布只接受受保护分支、固定依赖、可复现构建、SHA-256、签名和 SBOM；安装/升级必须验签；
3. 红线隔离矩阵在受控 Linux runner 常跑，不允许外部 PR 提供的 runner、镜像、DNS 或 secret；
4. `real_llm` 全链路 workflow 仅允许人工批准的内部受保护分支、受信 runner、nightly/manual 触发；外部 PR 不执行且不注入 provider/repo token；
5. e2e 通过公开 API 创建 workspace/agent/issue/runtime 和触发执行，禁止直接 `psql` seed 绕过产品契约；
6. 真实链路至少验证 assign、mention、日志、tool broker、审批续跑、diff/result、预算截断、取消、重试和清理；费用、token 和产物均断言；
7. provider 凭证只进入受信 broker/provider 启动边界，日志和测试产物经过同一脱敏器。

### 5.5 开发放行条件

- [ ] 安全复评确认 S-01～S-04 的设计回答可验证；
- [ ] S-05～S-13 均进入实现任务和自动化门禁；
- [ ] server P0 先完成冻结 AgentConfig、task token、result/diff 脱敏、预算核账和 runtime token 单真源；
- [ ] daemon fake-provider 合同测试通过；
- [ ] T36 隔离红线矩阵全部通过；
- [ ] 受保护的真实 LLM e2e 全链路通过；
- [ ] 最终安全复测通过后才允许生产启用真实 provider。
