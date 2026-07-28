# 自动化(Autopilot)功能 Spec

> **所属层**:智能体编排层("触发器 + 条件 + 动作"的自动化规则引擎 —— AI 队友的值班表)。
> **依赖的其他 Spec**:
> - `workspace.md`:`autopilots.workspace_id` 等外键回 `workspaces.id`;kill switch 为 workspace 级。
> - `member.md`:规则创建者、run 触发者引用 `members.id`(复合 FK,README §6.1/§6.2);执行 agent 引用 `agents.id`(复合 FK)。
> - `agent.md`:执行者 agent 的运行时/技能/权限决定动作能力边界;规则只"派单",不持有执行能力。
> - `issue.md`:事件触发器监听 issue 状态/字段变更、新评论、@提及;动作可改 issue 字段、创建 issue。
> - `comment-inbox.md` / `chat-session.md`:`add_comment` 动作落评论;`agent_mentioned` 触发器来自提及管线;通知经统一收件箱。
> - `runtime.md`:`run_agent_prompt` 动作派发给 agent,落地为运行记录 `task_executions`(README §6.4,经 `execution_id` 关联),其生命周期遵循全系统统一长任务状态机 `queued→claimed→running→completed|failed|timeout|cancelled`(另有 requeued/cancelling/awaiting_approval);本 Spec 经 execution 关联观察其终态。
> - `auth.md`:RBAC、审计、限流;入站 Webhook 用 HMAC 签名校验(非 Bearer)。
> **被依赖方**:`squad.md`(自动化可把任务派给整个小队)、运营/监控集成。

---

## 全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)

1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `UUID`(`gen_random_uuid()`);所有表含 `created_at` / `updated_at`(`TIMESTAMPTZ`,默认 `now()`,UTC);软删除统一 `deleted_at TIMESTAMPTZ NULL`。
2. **成员**:成员模型以 README §6.1 为唯一权威——规则创建者、run 触发者引用 `members.id`(复合 FK);执行者明确指向某 agent 定义,引用 `agents.id`(复合 FK)。**本模块各表不存 `*_type`/`*_kind` 判别列**,人类/agent 判别一律 JOIN `members.member_type`,API 响应可携带计算 `member_type` 快照。
3. **多租户**:跨模块外键一律按 README §6.2 建复合 FK + 目标表 `UNIQUE(workspace_id, id)`;`workspace_id` 冗余列保留并建 FK。
4. **接口**:基础路径 `/api/v1`;包络 / 分页 / 错误信封 / 幂等写 / HTTP 语义见 README §6.14;**入站 Webhook 端点除外**(HMAC 签名校验,非 Bearer)。
5. **实时**:统一实时契约见 README §6.7(频道内 `seq`、`realtime_events` 持久重放、`resume_from`/`resync_required`);事件名 `<entity>.<action>`。
6. **队列 / 投递**:事件触发**消费 transactional outbox**(README §6.6:业务模块同事务写 outbox,autopilot 调度器/事件匹配器是 relay 消费方,**不是进程内事件总线**);动作副作用(发评论 / 出向 HTTP / 执行入队)携带稳定幂等键(README §6.5);执行落地为 `task_executions`(README §6.4)。
7. **审批**:高风险动作审批统一走 `approvals` 实体(`subject_type='autopilot_action'`,README §6.10),本模块的 approve/reject 端点为其薄封装。
8. **ORM**:SQLAlchemy 2.x 约定。

---

## 1. 功能描述

### 1.1 模块定位

自动化(Autopilot)是 Mesh 的**规则引擎**,把重复性的运营、监控、响应工作交给 agent 自动完成。一条规则 = **何时触发(trigger)+ 是否满足条件(filter)+ 做什么(action,通常是把一段 prompt 交给指定 agent 执行)**。在 Mesh 语境里,autopilot 是"AI 队友的值班表":agent 不必等人召唤,而是按约定(定时)或按事件(状态变更、被 @、外部回调)自动上岗。

**核心设计:派单与执行解耦**。规则只描述"触发器+条件+动作",执行能力完全来自被指派的 agent(其 runtime / 技能 / 权限)。这样 autopilot 模块保持纯粹,agent 能力升级即自动惠及所有规则,无需改动规则引擎。`executor_agent_id` 是一等公民,agent 权限是动作的硬边界(最小权限)。

**防失控护栏是默认开启的一等公民,而非可选项**。失控的自动化比没有自动化更危险 —— 尤其当事件源是外部 Webhook(可能重复/被刷)或 agent 之间互相 @ 触发(可能成环)时。频率上限、事件去重、并发上限、人工确认点、全局 kill switch、预算上限六件套,在创建规则时即以合理默认值生效。

> **全局名册约定(以 README §6.1 为唯一权威)**:规则创建者、run 触发者为成员,引用 `members.id`(复合 FK;人类/agent 由 `members.member_type` 判别);执行者明确指向某 agent 定义,引用 `agents.id`(复合 FK)。本模块各表不存 `*_type`/`*_kind` 判别列(见顶部锚点 §2)。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| P1 | 规则构成 | 名称/描述/触发器/过滤/动作/执行者/护栏/启用状态 | 运营建"每天 9 点汇总进展";研发建"被 @ 时值班 agent 介入" |
| P2 | 定时触发器(cron) | 5 段 cron + **显式时区**;下次运行预览;一次性定时;错过补偿策略 | 每周一 09:00(沪)生成上周复盘草稿;每月 1 号跑成本审计 |
| P3 | 事件触发器 | issue 状态/创建/字段变更、新评论、@提及、Webhook | 监控告警经 Webhook 推入 → 自动建 issue 并让值班 agent 初诊 |
| P4 | 触发过滤(filter) | 项目/标签/优先级/作者/关键词/状态 from-to/Webhook 载荷匹配;维度间 AND,同类多值 OR | 只在 high/critical 且带 `bug` 标签的 issue 被 @ 时才介入 |
| P5 | 动作(action) | `run_agent_prompt`(核心)/改字段/发评论/发通知/建 issue/出向 HTTP;顺序执行;模板变量 | 告警进入 → agent 诊断 → 结论发评论 → 通知负责人 |
| P6 | 启用/暂停/归档 | active/paused/archived;切换留审计 | 节假日暂停"每日站会汇总";旧规则归档 |
| P7 | 执行历史(run) | 触发快照/状态/耗时/产物/token 消耗/错误/重试计数 | 所有者看最近 20 次运行,点失败那次看原因与输入快照 |
| P8 | 失败重试 | 最大重试次数;fixed/linear/**exponential** 退避 + 抖动;区分可重试/不可重试错误 | agent 被限流,指数退避第 2 次成功;3 次都失败标记 failed 并告警 |
| P9 | 告警 | 连续失败阈值/单次失败/熔断/待审批;站内 + 出向 Webhook + 邮件;静默窗口去重 | agent token 耗尽连续失败 3 次,所有者收告警含最近错误与跳转 |
| P10 | 防失控护栏 | 频率上限/去重幂等/并发上限/人工确认点/kill switch/作用域/预算 | 外部 1 分钟重复推同一告警 500 次,去重命中只执行 1 次,超限熔断告警 |
| P11 | agent 成环与级联防护 | agent↔agent 触发链成环检测 + 级联深度限制 | agent A 触发规则唤起 B,B 回评又触发 A,超深度阈值后截断 |
| P12 | 入站 Webhook | HMAC 签名校验 + 去重 + 审计;专属 URL 或共享端点 + 路由键 | 代码合并事件 → 自动更新 issue 状态并通知 |

### 1.3 边界与非目标(明确不做什么)

- **不**定义 agent 的运行时/技能/权限实现 —— 归 `agent.md` / `runtime.md`(本 Spec 仅"派单",执行能力来自 agent)。
- **不**定义 issue/评论/通知的领域逻辑 —— 归 `issue.md` / `comment-inbox.md`(本 Spec 仅作为事件消费方与动作发起方)。
- **不**实现调度中间件(独立 broker / 定时器服务)—— 以 PostgreSQL 为唯一调度事实源,原子抢占实现可扩展定时;事件触发消费业务侧的 transactional outbox(README §6.6),本模块是 relay 消费方而非事件总线。
- **不**定义出向 HTTP 动作的目标系统协议 —— 仅声明出向请求受 agent 权限与人工确认门约束。
- **不**支持可视化拖拽编排复杂 DAG 工作流(YAGNI;多动作顺序执行 + 模板变量已覆盖主流场景)。
- **不**做跨 workspace 的全局规则(YAGNI;kill switch 仅 workspace 级)。

---

## 2. 数据模型

### 2.1 ER 概览(文字图)

```
workspaces ──隔离──► autopilot(规则)──1:N──► autopilot_runs(每次执行)
                       │  (trigger/filter/action JSONB)        │  ├─1:N─► autopilot_run_attempts(重试明细)
                       │  executor_agent_id ──► agents         │  ├─1:N─► autopilot_artifacts(产物引用)
                       │  created_by ──► members               │  └─自引用─► parent_run_id / cascade_depth(级联)
                       │                                        │
                       └──1:N──► webhook_events(外部事件接收: 签名/去重/审计)──► 路由匹配规则
                                                                        ▲
agents(agent.md):executor_agent_id(复合 FK);members(member.md):created_by / triggered_by(复合 FK)
runtime.md:run_agent_prompt 动作派发为 task_executions(经 execution_id 关联,README §6.4),终态 completed|failed|timeout|cancelled
approvals(README §6.10):subject_type='autopilot_action',经 approvals.subject_run_id 关联 autopilot_runs
```

### 2.2 表:`autopilots`(自动化规则定义)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 归属 workspace |
| `name` | TEXT | NOT NULL | — | 规则名(workspace 内唯一,见唯一索引) |
| `description` | TEXT | NULL | NULL | 描述 |
| `trigger_type` | TEXT | NOT NULL,CHECK IN ('schedule','issue_status_changed','issue_created','issue_field_changed','comment_created','agent_mentioned','webhook_received') | — | 触发器类型 |
| `trigger_config` | JSONB | NOT NULL | `'{}'` | 触发器配置(见 §2.6) |
| `filter_config` | JSONB | NOT NULL | `'{}'` | 过滤条件(见 §2.6) |
| `action_config` | JSONB | NOT NULL | `'[]'` | 动作列表(数组,顺序执行,见 §2.6) |
| `executor_agent_id` | UUID | NULL,**复合 FK `(workspace_id, executor_agent_id) → agents(workspace_id, id)`** | NULL | 执行者 agent;`run_agent_prompt` 必填(README §6.2) |
| `status` | TEXT | NOT NULL,CHECK IN ('active','paused','archived') | `'active'` | 规则状态 |
| `guardrails` | JSONB | NOT NULL | 见 §2.6 | 护栏配置 |
| `max_retries` | INT | NOT NULL,CHECK (>= 0) | `3` | 最大重试次数 |
| `retry_backoff` | TEXT | NOT NULL,CHECK IN ('fixed','linear','exponential') | `'exponential'` | 退避策略 |
| `retry_base_seconds` | INT | NOT NULL,CHECK (> 0) | `30` | 退避基数 |
| `retry_max_seconds` | INT | NOT NULL,CHECK (> 0) | `1800` | 退避封顶 |
| `rate_limit_max` | INT | NOT NULL,CHECK (>= 0) | `10` | 窗口内最大触发数 |
| `rate_limit_window_seconds` | INT | NOT NULL,CHECK (> 0) | `3600` | 频率窗口(秒) |
| `concurrency_limit` | INT | NOT NULL,CHECK (>= 1) | `1` | 并发 run 上限(默认串行) |
| `require_approval` | BOOLEAN | NOT NULL | `false` | 是否需人工确认点;命中即在统一 `approvals` 实体建 `subject_type='autopilot_action'` 行(README §6.10) |
| `next_run_at` | TIMESTAMPTZ | NULL | NULL | 下次定时触发时刻(调度索引用) |
| `last_run_at` | TIMESTAMPTZ | NULL | NULL | 上次运行时刻 |
| `created_by` | UUID | NOT NULL,**复合 FK `(workspace_id, created_by) → members(workspace_id, id)`** | — | 创建者(人或 agent;判别 JOIN members,README §6.1/§6.2) |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**唯一约束**:`UNIQUE (workspace_id, name) WHERE deleted_at IS NULL`(软删除范围内名称唯一)。
**复合 FK 引用前提(README §6.2)**:`autopilots` 被 `autopilot_runs.autopilot_id`、`webhook_events.autopilot_id` 复合引用,除 `PK(id)` 外建 **`UNIQUE (workspace_id, id)`**。

### 2.3 表:`autopilot_runs`(每次执行记录)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `autopilot_id` | UUID | NOT NULL,**复合 FK `(workspace_id, autopilot_id) → autopilot(workspace_id, id)`** | — | 所属规则(README §6.2) |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 冗余,便于按 workspace 查询(保留 + FK) |
| `trigger_type` | TEXT | NOT NULL | — | 触发类型快照 |
| `trigger_snapshot` | JSONB | NOT NULL | `'{}'` | 触发事件输入快照(可重放) |
| `webhook_event_id` | UUID | NULL,**复合 FK `(workspace_id, webhook_event_id) → webhook_events(workspace_id, id)`** | NULL | 关联入站事件(若适用) |
| `execution_id` | UUID | NULL,**复合 FK `(workspace_id, execution_id) → task_executions(workspace_id, id)`** | NULL | `run_agent_prompt` 派发的逻辑执行(→ runtime.md `task_executions`,README §6.4) |
| `parent_run_id` | UUID | NULL,复合 FK `(workspace_id, parent_run_id) → autopilot_runs(workspace_id, id)` | NULL | 触发本 run 的上游 run(agent→agent 级联溯源;同 workspace 内自引用) |
| `cascade_depth` | INT | NOT NULL,CHECK (>= 0) | `0` | agent→agent 级联深度(超阈值截断,见 §2.6) |
| `status` | TEXT | NOT NULL,CHECK IN ('pending','running','waiting_approval','retrying','succeeded','failed','cancelled') | `'pending'` | 运行状态(见 §4.4);`waiting_approval` 表示有一条 `approvals` 待决(README §6.10) |
| `started_at` | TIMESTAMPTZ | NULL | NULL | 开始时间 |
| `finished_at` | TIMESTAMPTZ | NULL | NULL | 结束时间 |
| `duration_ms` | INT | NULL | NULL | 耗时(毫秒) |
| `retry_count` | INT | NOT NULL,CHECK (>= 0) | `0` | 已重试次数 |
| `error` | JSONB | NULL | NULL | 错误信息 `{code,message,retryable,detail}` |
| `prompt_tokens` | INT | NULL,CHECK (>= 0) | NULL | 输入 token |
| `completion_tokens` | INT | NULL,CHECK (>= 0) | NULL | 输出 token |
| `total_tokens` | INT | GENERATED ALWAYS AS (COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)) STORED | — | 合计 token |
| `triggered_by` | UUID | NULL,**复合 FK `(workspace_id, triggered_by) → members(workspace_id, id)`** | NULL | 触发者(手动 test run 时为操作者;定时/事件可空;判别 JOIN members,README §6.1/§6.2) |
| `is_test` | BOOLEAN | NOT NULL | `false` | 是否手动测试运行 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> **审批关联(取此方案,README §6.10)**:autopilot_runs **不冗余 approval 列**;待决审批经统一 `approvals` 实体的 **`approvals.subject_run_id` 复合 FK `(workspace_id, subject_run_id) → autopilot_runs(workspace_id, id)`** 反查,`subject_type='autopilot_action'`(README §6.10 R2:**已由逻辑关联升级为物理复合 FK**,并有「按 `subject_type` 恰好一个 subject 列非空」CHECK 与「同 subject 仅一个 pending」的部分唯一索引 `uq_approvals_pending_run`)。当存在 `status='pending'` 的关联 approval 时,`autopilot_runs.status='waiting_approval'`;**同一 run 仅一个 pending 审批**(重复发起取既有 pending 返回);approve/reject 经 `POST /api/v1/approvals/{id}/approve|reject` 收口(本模块 `runs/{run_id}/approve|reject` 为其薄封装,见 §3.1)。

### 2.4 表:`autopilot_run_attempts`(重试明细)与 `autopilot_artifacts`(产物)

**`autopilot_run_attempts`**(每次尝试一行,便于精确统计与排障):

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `run_id` | UUID | NOT NULL,复合 FK `(workspace_id, run_id) → autopilot_runs(workspace_id, id)` | — | 所属 run |
| `attempt_number` | INT | NOT NULL,CHECK (>= 1) | — | 第几次尝试 |
| `status` | TEXT | NOT NULL | — | 本次尝试结果 |
| `execution_id` | UUID | NULL | NULL | 本次尝试派发的逻辑执行(→ `task_executions.id`,README §6.4;经所属 run 隶属同一 workspace) |
| `started_at` / `finished_at` | TIMESTAMPTZ | NULL | NULL | 起止 |
| `error` | JSONB | NULL | NULL | 本次错误 |
| `prompt_tokens` / `completion_tokens` | INT | NULL | NULL | 本次 token |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

约束:`UNIQUE (run_id, attempt_number)`。

**`autopilot_artifacts`**(产物引用,把 run 与它产生的对象解耦关联):

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `run_id` | UUID | NOT NULL,FK→autopilot_runs(id) | — | 所属 run |
| `artifact_type` | TEXT | NOT NULL,CHECK IN ('comment','issue','notification','agent_output','http_response') | — | 产物类型 |
| `ref_table` | TEXT | NOT NULL | — | 被引用对象所在表 |
| `ref_id` | UUID | NOT NULL | — | 被引用对象 id |
| `summary` | TEXT | NULL | NULL | 产物摘要 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

### 2.5 表:`webhook_events`(外部事件接收记录:去重与审计)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 归属 |
| `autopilot_id` | UUID | NULL,**复合 FK `(workspace_id, autopilot_id) → autopilot(workspace_id, id)`** | NULL | 路由到的规则(空=未匹配,README §6.2) |
| `idempotency_key` | TEXT | NOT NULL | — | 去重键(**签名校验通过的事件**:用事件 ID 或内容哈希;**被拒事件**:用 `rejected:<raw-hash>` 前缀,不占用合法事件的去重命名空间,防未签名请求预占键导致合法事件被静默去重) |
| `event_type` | TEXT | NOT NULL | — | 事件类型(由来源/载荷解析) |
| `headers` | JSONB | NULL | NULL | 入站请求头(脱敏后) |
| `payload` | JSONB | NOT NULL | — | 原始载荷 |
| `signature_status` | TEXT | NOT NULL,CHECK IN ('valid','invalid','missing','skipped') | — | 签名校验结果(**`invalid`/`missing` 一律 `rejected` + 401,绝不分发**;`skipped` 仅限 test-run 场景,不产生 `autopilot_run`) |
| `process_status` | TEXT | NOT NULL,CHECK IN ('received','matched','dispatched','deduped','rejected','processed','failed') | `'received'` | 处理状态 |
| `received_at` | TIMESTAMPTZ | NOT NULL | `now()` | 接收时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**去重唯一键**:`UNIQUE (workspace_id, idempotency_key)`。入站时先尝试插入,命中唯一冲突即视为重复,直接返回成功(幂等)但不再分发。
**复合 FK 引用前提(README §6.2)**:`webhook_events` 被 `autopilot_runs.webhook_event_id` 复合引用,除 `PK(id)` 外建 **`UNIQUE (workspace_id, id)`**。

### 2.5.1 表:`webhook_secrets`(入站凭据,§3.1 / §5.3 配套实现)

§3.1 的 `POST/GET /workspaces/{ws}/webhook-secrets` 与入站端点的签名校验所需凭据存储(§5.3「Webhook 密钥仅存哈希/引用,创建后仅显示一次」的落地形态):

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | 归属 |
| `label` | TEXT | NOT NULL,默认 `'default'` | 凭据标签 |
| `token_hash` | TEXT | NOT NULL,`UNIQUE` | 入站 URL `/webhooks/inbound/{token}` 的 token 仅存 **SHA-256 哈希**(查询用,明文不落库) |
| `encrypted_secret` | TEXT | NOT NULL | HMAC 签名密钥的 **Fernet 密文**(与 `runtime_credentials` 同契约,README §6.16;校验时需解密密重算签名,故非哈希) |
| `status` | TEXT | NOT NULL,CHECK IN ('active','revoked') | 轮换即 revoke 旧凭据 |
| `created_by` | UUID | NOT NULL,复合 FK→members | 创建者 |
| `revoked_at` / `created_at` / `updated_at` | TIMESTAMPTZ | | |

明文 token + secret **仅在创建/轮换响应出现一次**,列表端点绝不回显。入站端点无 Bearer 身份,RLS fail-closed 下经 SECURITY DEFINER 引导函数 `mesh_webhook_secret_by_token_hash(token_hash)` 先查凭据(得 workspace)再设租户 GUC——与 runtime 的 `mesh_runtime_by_token_hash` 同构。规则经 `trigger_config.secret_id` 绑定凭据;轮换保持行 id 不变(旧 token 立即失效,绑定规则继续可用)。

### 2.6 JSONB 配置结构

**`trigger_config`(定时)**:
```json
{ "cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai",
  "misfire_policy": "run_once", "one_time_at": null }
```
> cron 为 5 段标准式;**必须显式携带 IANA 时区**,避免服务器 UTC 与用户预期错位。`misfire_policy ∈ {skip, run_once, run_all}`。`one_time_at` 非空表示一次性定时(运行后自动归档)。

**`trigger_config`(事件)**:
```json
{ "event": "issue_status_changed", "scope_project_ids": ["<uuid>"],
  "from_status": ["todo"], "to_status": ["in_progress"],
  "watch_fields": ["priority", "assignee_id"] }
```

**`filter_config`**(维度间 AND,同类多值 OR):
```json
{ "project_ids": ["<uuid>"], "labels": ["bug"],
  "priorities": ["high", "critical"], "actor_ids": [],
  "keyword_include": ["回归", "线上"], "keyword_exclude": ["忽略"],
  "payload_match": [{"path": "alert.severity", "op": "in", "value": ["critical"]}] }
```

**`action_config`**(数组,顺序执行;prompt 支持模板变量 `{{trigger.issue.title}}` / `{{trigger.comment.body}}` / `{{trigger.actor.name}}` / `{{trigger.webhook.payload.*}}` / `{{steps.N.output}}` / `{{run.id}}` / `{{now}}`):
> **不可信内容处理(见 README §6)**:`{{trigger.webhook.payload.*}}` 等来自外部的模板变量插值进 agent prompt 时,必须显式标记为不可信数据并做结构隔离,防止外部 Webhook 载荷中的恶意指令劫持 agent。
```json
[
  {"type": "run_agent_prompt", "executor_agent_id": "<uuid>",
   "prompt": "请诊断 issue {{trigger.issue.title}}:{{trigger.comment.body}}"},
  {"type": "add_comment", "target": "trigger.issue", "content": "自动诊断结论:{{steps.0.output}}"},
  {"type": "send_notification", "to": ["owner"], "template": "autopilot_done"}
]
```

**`guardrails`**(创建时即以下默认值生效):
```json
{ "rate_limit_overflow": "drop", "dedup_window_seconds": 300,
  "dedup_key_template": "{{trigger.event_id}}",
  "daily_run_budget": 200, "daily_token_budget": 2000000,
  "approval_required_actions": ["http_request", "create_issue"],
  "kill_switch_paused": false,
  "agent_loop_detection": true, "cascade_max_depth": 3,
  "agent_loop_window_seconds": 60 }
```
> - `rate_limit_overflow ∈ {drop, queue, alert_only}`(默认 `drop` + 告警);
> - `cascade_max_depth`:agent→agent 级联深度上限,`run.cascade_depth` 超过即拒绝创建下游 run;
> - `agent_loop_detection` + `agent_loop_window_seconds`:同一 `(executor_agent, 触发对象)` 对在时间窗内 run 去重,防 agent 互提成环;
> - **`approval_required_actions` / `require_approval`(统一审批,README §6.10)**:run 命中人工确认点(规则 `require_approval=true` 或动作类型在 `approval_required_actions`,如出向 HTTP、建 issue)时,**在统一 `approvals` 实体创建一行**(`subject_type='autopilot_action'`、`subject_run_id=run.id`、`requested_by_member_id`=触发者/创建者、`action_summary` 含动作/影响范围/预估成本/过期时间),并把 `autopilot_runs.status` 置 `waiting_approval`;批准/拒绝/过期经 `approvals` 收口(见 §3.1 与 §4.4),过期 → run `cancelled(approval_expired)`。

### 2.7 索引与约束

```sql
-- 复合 FK 引用前提(README §6.2):被跨表引用的工作区级表建 UNIQUE(workspace_id, id)
ALTER TABLE autopilots ADD CONSTRAINT uq_autopilot_ws_id UNIQUE (workspace_id, id);
ALTER TABLE webhook_events ADD CONSTRAINT uq_webhook_event_ws_id UNIQUE (workspace_id, id);
-- autopilot_runs 被 approvals.subject_run_id(README §6.10)与 attempt 表复合引用
ALTER TABLE autopilot_runs ADD CONSTRAINT uq_autopilot_run_ws_id UNIQUE (workspace_id, id);
ALTER TABLE autopilot_run_attempts ADD CONSTRAINT uq_autopilot_run_attempts_ws_id UNIQUE (workspace_id, id);

-- 调度器扫描:按下次运行时间取出到期的 active 定时规则
CREATE INDEX idx_autopilot_schedule ON autopilots(next_run_at)
  WHERE status = 'active' AND trigger_type = 'schedule' AND deleted_at IS NULL;
-- 事件匹配:按触发类型 + 状态找候选规则
CREATE INDEX idx_autopilot_trigger ON autopilots(trigger_type, status) WHERE deleted_at IS NULL;
-- 名称唯一(软删除范围内)
CREATE UNIQUE INDEX uq_autopilot_ws_name ON autopilots(workspace_id, name) WHERE deleted_at IS NULL;

-- 执行历史:某规则 run 时间线 / workspace 维度 / 状态过滤
CREATE INDEX idx_run_autopilot_started ON autopilot_runs(autopilot_id, started_at DESC);
CREATE INDEX idx_run_workspace_started ON autopilot_runs(workspace_id, created_at DESC);
CREATE INDEX idx_run_status ON autopilot_runs(status)
  WHERE status IN ('running','retrying','waiting_approval','pending');
-- 级联溯源:由某 run 触发的下游 run
CREATE INDEX idx_run_parent ON autopilot_runs(parent_run_id) WHERE parent_run_id IS NOT NULL;

CREATE UNIQUE INDEX uq_run_attempt ON autopilot_run_attempts(run_id, attempt_number);
CREATE INDEX idx_artifact_run ON autopilot_artifacts(run_id);

-- 事件去重唯一键 / 按规则与处理状态查询
CREATE UNIQUE INDEX uq_webhook_event_idem ON webhook_events(workspace_id, idempotency_key);
CREATE INDEX idx_webhook_event_route ON webhook_events(autopilot_id, process_status, received_at DESC);
```

### 2.8 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `autopilots.workspace_id` 等 | → `workspaces.id` | workspace.md | 隔离(冗余列保留 + FK) |
| `autopilot.created_by` / `autopilot_runs.triggered_by` | 复合 FK → `members(workspace_id, id)` | member.md | 创建者/触发者(人或 agent;判别 JOIN members,README §6.1/§6.2) |
| `autopilot.executor_agent_id` | 复合 FK → `agents(workspace_id, id)` | agent.md | 执行者 agent(动作能力边界,README §6.2) |
| `autopilot_runs.autopilot_id` / `webhook_events.autopilot_id` | 复合 FK → `autopilot(workspace_id, id)` | 本模块 | 规则归属(README §6.2) |
| `autopilot_runs.webhook_event_id` | 复合 FK → `webhook_events(workspace_id, id)` | 本模块 | 关联入站事件(README §6.2) |
| `autopilot_runs.execution_id` / `autopilot_run_attempts.execution_id` | 复合 FK / 逻辑关联 → `task_executions(workspace_id, id)` | runtime.md | `run_agent_prompt` 派发的逻辑执行(README §6.4) |
| `approvals.subject_run_id` | 复合 FK → `autopilot_runs(workspace_id, id)` | README §6.10 | `subject_type='autopilot_action'` 的高风险动作审批(R2:已升为物理复合 FK,不再是逻辑关联;同 subject 仅一个 pending,部分唯一索引 `uq_approvals_pending_run`) |
| `autopilot_artifacts.ref_id`(`ref_table='comments'/'issues'`) | 多态逻辑外键(行带 `workspace_id`) | comment-inbox.md / issue.md | 动作产物(README §6.2 逻辑外键规则) |

---

## 3. 接口设计

REST 基础路径 `/api/v1`,集合嵌套于 `/workspaces/{ws}/`;鉴权 `Authorization: Bearer <token>`(**入站 Webhook 端点除外**,用 HMAC 签名校验)。**成功包络 / 游标分页 / 错误信封 / 乐观并发 / 幂等写 / 过滤限制一律以 README §6.14 为唯一权威**(单对象 `{"data":{...}}`、列表 `{"data":[...],"next_cursor":<opaque|null>}`,`next_cursor=null` 表示末页;错误 `{"error":{"code","message","details"}}`,code 为 snake_case),本 Spec 不重复定义,仅列本模块具名错误码。

### 3.1 REST 端点清单

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| POST | `/workspaces/{ws}/autopilots` | 创建规则 | admin / `autopilot:manage` |
| GET | `/workspaces/{ws}/autopilots` | 列表(分页 + `status`/`trigger_type` 过滤) | 成员 |
| GET | `/workspaces/{ws}/autopilots/{id}` | 详情 | 成员 |
| PATCH | `/workspaces/{ws}/autopilots/{id}` | 更新配置 | admin / `autopilot:manage` |
| DELETE | `/workspaces/{ws}/autopilots/{id}` | 软删除(归档) | admin / `autopilot:manage` |
| POST | `/workspaces/{ws}/autopilots/{id}/pause` | 暂停 | admin / `autopilot:manage` |
| POST | `/workspaces/{ws}/autopilots/{id}/resume` | 启用(重算 `next_run_at`) | admin / `autopilot:manage` |
| POST | `/workspaces/{ws}/autopilots/{id}/test-run` | 手动触发一次(可 dry_run) | admin / `autopilot:manage` |
| GET | `/workspaces/{ws}/autopilots/{id}/runs` | 该规则执行历史 | 成员 |
| GET | `/workspaces/{ws}/autopilots/{id}/preview-schedule` | cron 下次运行预览 | 成员 |
| GET | `/workspaces/{ws}/autopilot-runs/{run_id}` | 单次运行详情(含产物/尝试明细) | 成员 |
| GET | `/workspaces/{ws}/autopilot-runs/{run_id}/artifacts` | 运行产物列表 | 成员 |
| POST | `/workspaces/{ws}/autopilot-runs/{run_id}/cancel` | 取消正在运行的 run | admin / `autopilot:manage` |
| POST | `/workspaces/{ws}/autopilot-runs/{run_id}/approve` | 人工确认通过(审批门)。**`POST /api/v1/approvals/{id}/approve` 的薄封装**:定位该 run 的待决 `approvals`(`subject_run_id=run_id`)后转发(README §6.10);保留便捷路径 | 审批人 / admin |
| POST | `/workspaces/{ws}/autopilot-runs/{run_id}/reject` | 人工确认拒绝。**`POST /api/v1/approvals/{id}/reject` 的薄封装**(README §6.10) | 审批人 / admin |
| POST | `/workspaces/{ws}/autopilots/kill-switch` | 全局暂停/恢复所有 autopilot | admin |
| POST | `/webhooks/inbound/{token}` | 接收外部 Webhook(HMAC 签名校验,非 Bearer) | 签名校验 |
| POST | `/workspaces/{ws}/webhook-secrets` | 创建/轮换 Webhook 密钥 | admin |
| GET | `/workspaces/{ws}/webhook-secrets` | 列出密钥(不返回明文) | admin |

> **统一审批入口(README §6.10)**:autopilot 高风险动作审批与高风险工具确认、squad 计划审批共用 `approvals` 实体与统一收件箱端点 `GET /api/v1/approvals?role=mine` / `GET /approvals/{id}` / `POST /approvals/{id}/approve` / `POST /approvals/{id}/reject`(全局定义,本模块不重复)。上方 `runs/{run_id}/approve|reject` 仅为面向 autopilot 运行详情页的便捷薄封装。

### 3.2 请求/响应 JSON 示例

**创建规则** `POST /api/v1/workspaces/{ws}/autopilots`
```json
// Request
{ "name": "每日站会前汇总进展", "description": "工作日 09:00 自动汇总各成员昨日进展",
  "trigger_type": "schedule",
  "trigger_config": {"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai", "misfire_policy": "run_once"},
  "filter_config": {"project_ids": ["6f1c..."]},
  "action_config": [
    {"type": "run_agent_prompt", "executor_agent_id": "a9e2...",
     "prompt": "汇总项目 {{filter.project_ids[0]}} 各成员昨日进展,输出 markdown"},
    {"type": "send_notification", "to": ["owner"], "template": "daily_summary"}
  ],
  "executor_agent_id": "a9e2...", "max_retries": 3, "retry_backoff": "exponential",
  "rate_limit_max": 5, "rate_limit_window_seconds": 3600, "require_approval": false }
// 201 Response
{ "data": {
    "id": "3b7d1f0e-2c4a-4e1b-9f8a-1d2e3f4a5b6c", "workspace_id": "7ea1...",
    "name": "每日站会前汇总进展", "trigger_type": "schedule",
    "trigger_config": {"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai", "misfire_policy": "run_once"},
    "filter_config": {"project_ids": ["6f1c..."]}, "action_config": [ ],
    "executor_agent_id": "a9e2...", "status": "active",
    "guardrails": {"rate_limit_overflow": "drop", "dedup_window_seconds": 300, "cascade_max_depth": 3, "agent_loop_detection": true},
    "max_retries": 3, "retry_backoff": "exponential", "rate_limit_max": 5,
    "rate_limit_window_seconds": 3600, "concurrency_limit": 1, "require_approval": false,
    "next_run_at": "2026-07-27T01:00:00Z",
    "created_at": "2026-07-24T12:00:00Z", "updated_at": "2026-07-24T12:00:00Z" } }
```

**列表(分页 + 过滤)** `GET /api/v1/workspaces/{ws}/autopilots?status=active&trigger_type=schedule&limit=20`
```json
{ "data": [
    { "id": "3b7d...", "name": "每日站会前汇总进展", "trigger_type": "schedule", "status": "active",
      "last_run_at": "2026-07-24T01:00:00Z", "next_run_at": "2026-07-27T01:00:00Z",
      "stats": {"runs_30d": 22, "success_rate": 0.95} }
  ],
  "next_cursor": "eyJpZCI6IjNiN2Qu" }
```

**手动触发一次(test run)** `POST /api/v1/workspaces/{ws}/autopilots/{id}/test-run`
```json
// Request
{ "simulate_trigger_payload": {"issue": {"title": "登录报错"}}, "dry_run": false }
// 202 Response
{ "data": { "run_id": "c0a8...", "status": "pending", "autopilot_id": "3b7d...", "is_test": true } }
// dry_run=true → 200:{"data": {"would_run": true, "matched_filters": {"labels": ["bug"]}}}
```

**单次运行详情** `GET /api/v1/workspaces/{ws}/autopilot-runs/{run_id}`
```json
{ "data": {
    "id": "c0a8...", "autopilot_id": "3b7d...", "status": "succeeded",
    "trigger_type": "agent_mentioned", "cascade_depth": 0,
    "trigger_snapshot": { "event_id": "evt_9f2...",
      "issue": {"id": "i1", "title": "登录报错"},
      "comment": {"id": "cm1", "body": "@值班agent 帮忙看下"},
      "actor": {"id": "mem-u7", "name": "张三"} },
    "started_at": "2026-07-24T03:12:00Z", "finished_at": "2026-07-24T03:12:35Z",
    "duration_ms": 35000, "retry_count": 0,
    "prompt_tokens": 8200, "completion_tokens": 1300, "total_tokens": 9500,
    "attempts": [ {"attempt_number": 1, "status": "succeeded", "started_at": "2026-07-24T03:12:00Z",
                   "finished_at": "2026-07-24T03:12:35Z", "error": null} ],
    "artifacts": [ {"artifact_type": "comment", "ref_table": "comments", "ref_id": "cm9", "summary": "已发布诊断结论"} ],
    "error": null } }
```

**全局 kill switch** `POST /api/v1/workspaces/{ws}/autopilots/kill-switch`
```json
// Request
{ "enabled": true, "reason": "紧急止血:批量异常" }
// 200 Response
{ "data": { "kill_switch": true, "paused_autopilots": 14, "updated_at": "2026-07-24T04:00:00Z" } }
```
> 恢复时 `enabled: false`,逐条恢复原状态(active 的重新参与调度)。

**入站 Webhook(HMAC 签名校验)** `POST /api/v1/webhooks/inbound/{token}`(无需 Bearer)
```
Content-Type: application/json
X-Signature: t=1721808000,v1=5d41402abc4b2a76b9719d911017c592...
X-Event-Type: alert.triggered
X-Event-Id: evt_9f2a...
```
> 签名计算 `v1 = HMAC_SHA256(secret, "{t}.{raw_body}")`,服务端用密钥重算并**恒定时间比较**;同时校验时间戳 `t` 在容差窗口(±300s)内防重放。
```json
// 200(幂等,重复事件同样 200;**入站 Webhook 为非 Bearer 外部端点,以下响应为与外部系统约定的裸 JSON,不套 README §6.14 成功包络**)
{ "received": true, "event_id": "evt_9f2a...", "process_status": "dispatched", "run_id": "c0a8..." }
// 重复事件
{ "received": true, "event_id": "evt_9f2a...", "process_status": "deduped", "run_id": null }
// 签名失败 401
{ "error": {"code": "invalid_signature", "message": "Webhook 签名校验失败", "details": {}} }
```
**处理流程**:接收 → 校验签名(`signature_status`)→ **签名 `invalid`/`missing` 一律落库 `webhook_events`(`process_status='rejected'`,`idempotency_key='rejected:<raw-body-hash>'`——使用独立前缀命名空间,不占用合法事件的去重键)并返回 401,绝不分发不路由** → 签名通过后落库(`received`)→ 计算/读取 `idempotency_key`(事件 ID 或内容哈希)尝试去重插入(命中则 `deduped` 直接返回)→ 路由匹配规则 → 频率/护栏检查 → 创建 `autopilot_runs`(`dispatched`)→ 异步执行。
> **去重防预占(可用性保护)**:被拒(签名无效/缺失)事件的 `idempotency_key` 使用 `rejected:` 前缀 + 原始请求体哈希,与合法事件的去重键命名空间隔离——攻击者无法用伪造的未签名请求预占 `X-Event-Id`,使后续同 ID 的合法签名事件被静默去重丢弃。
> **Webhook 触发器强制配置签名密钥**:创建 `trigger_type='webhook_received'` 的规则时,必须已配置有效的签名密钥(服务端创建时校验,未配置返回 422);`skipped` 状态仅限 `is_test=true` 的 test-run 场景;**无有效签名的事件永不产生 `autopilot_runs`**。

### 3.3 错误码表

| HTTP | code | 场景 |
|------|------|------|
| 400 | `invalid_request` | 请求体/参数非法 |
| 400 | `invalid_cron` | cron 表达式不合法 |
| 400 | `invalid_trigger_config` | 触发器配置与类型不匹配 |
| 401 | `unauthorized` | 缺少/无效 Bearer token |
| 401 | `invalid_signature` | Webhook 签名校验失败 |
| 403 | `forbidden` | 无权限操作该规则/run |
| 404 | `not_found` | 规则/run/事件不存在 |
| 409 | `conflict` | 名称重复;对非允许状态的 run 操作(如取消已结束 run) |
| 409 | `duplicate_event` | 事件去重命中(通常作为 200 `deduped` 返回,内部用) |
| 422 | `executor_required` | `run_agent_prompt` 缺少 `executor_agent_id` |
| 422 | `agent_unavailable` | 执行 agent 不存在/不可用 |
| 422 | `cascade_depth_exceeded` | agent→agent 级联深度超过 `cascade_max_depth` |
| 429 | `rate_limited` | 触发频率超限 / API 限流 |
| 500 | `internal_error` | 服务内部错误 |
| 503 | `executor_busy` | agent 运行时繁忙且并发已满 |

### 3.4 分页 / 鉴权 / 限流

- **分页**:游标分页 `?cursor=<opaque>&limit=<int>`(默认 20,上限 100),响应 `{"data":[...],"next_cursor"}`。
- **鉴权**:读需 workspace 成员;创建/更新/暂停/删除/test-run/取消/审批/kill switch 需 `admin` 或 `autopilot:manage`;kill switch 需 `admin`。规则只能在创建者权限范围内操作;agent 动作受 agent 自身权限约束(最小权限)。入站 Webhook 用 HMAC 签名校验,不消费 Bearer。
- **限流**:**频率上限是本模块核心护栏而非普通 API 限流** —— 单条规则单位时间内最多触发 `rate_limit_max` 次,超限按 `rate_limit_overflow` 处理(`drop` 默认 / `queue` / `alert_only`)并触发熔断告警;写端点另按 principal 限流(见 auth.md)。

### 3.5 WebSocket 实时事件

连接 `/ws`(握手鉴权见 auth.md),订阅频道 `workspace:{ws}:autopilots` 或 `autopilot:{id}`。**实时契约以 README §6.7 为唯一权威**:事件命名 `<entity>.<action>`,携带**频道内**单调递增 `seq`(业务事务内自 `realtime_channels.last_seq` 分配),断线凭 `resume_from=<last_seq+1>` 从 `realtime_events` 重放,游标过旧下发 `resync_required`;Redis 仅做 fan-out。

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `autopilot_runs.status_changed` | run 状态流转(驱动详情/列表实时刷新) | `run_id`, `autopilot_id`, `old_status`, `new_status` |
| `autopilot_runs.approval_required` | run 命中人工确认点 | `run_id`, `autopilot_id` |
| `autopilot.rate_limited` | 规则触发频率超限被熔断 | `autopilot_id`, `window`, `dropped` |
| `autopilot.updated` | 规则配置/状态变更 | `autopilot_id`, `status` |
| `webhook_events.received` | 入站事件落库 | `event_id`, `process_status`, `signature_status` |

**降级**:WebSocket 不可用时,run 状态退化为轮询 `GET .../autopilot-runs/{run_id}`(3~5s)。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
自动化页(/autopilots)
   ├── 顶部:[状态▾] [类型▾] [搜索]   ⚠ 全局开关:● 已开启   [+ 新建 autopilot]
   └── 表格:名称 | 触发器(图标+文案) | 状态徽章 | 上次运行时间与结果 | 近30天成功率 | 下次运行(定时) | 操作(⏸ ▶ ⋯)
规则编辑器(分区式,可折叠):① 触发器 → ② 过滤 → ③ 动作 → ④ 护栏与重试,底部固定 [取消][保存草稿][保存并启用]
规则详情页:上半只读配置卡片([编辑][暂停][手动运行]);下半执行历史时间线(按状态过滤)
单次运行详情页:[输入快照][产物][尝试明细/日志]
Webhook 配置页:入站端点 + 签名密钥(创建后仅显示一次)+ 最近事件
```

### 4.2 关键组件

- **规则编辑器**:四个可折叠区块。cron 输入提供可视化(下拉选常用周期 + 高级手填)与"下次 5 次运行预览"实时刷新;动作区可增删排序,每个动作选类型并配置;`run_agent_prompt` 强制选执行者 agent + prompt(支持模板变量插入)。
- **护栏区(默认展开提示)**:频率上限(次/窗口 + 超限行为)、并发上限、重试次数与退避、人工确认开关、每日预算(次/token)。新建时以推荐默认值预填,体现"护栏默认开启"。
- **执行历史时间线**:每行 状态图标 + 时间 + 耗时 + token + 重试次数 + 错误摘要;点击进入运行详情。
- **运行详情页**:输入快照(JSON,只读)、产物列表(带跳转)、尝试明细(每次尝试的起止/错误/token)。
- **kill switch 全局开关**:列表顶部常驻,显示"● 已开启 / ○ 已暂停",点击需二次确认并填理由。

### 4.3 关键交互流程

**流程 A:创建"每日站会前自动汇总"(定时)**:列表 → 新建 → 填名称 → 触发器选"定时"(可视化选"每个工作日 09:00",时区选本地)→ 即时展示"下次 5 次运行预览"确认 → 过滤限定项目 → 动作选"交给 agent 执行 prompt"(选执行者,写汇总 prompt)→ 追加"发送通知" → 护栏用默认值 → 保存并启用,`next_run_at` 已计算。

**流程 B:创建"@提及某 agent 时自动响应"(事件)**:新建 → 触发器选"事件 → @提及某 agent",选目标 agent → 过滤(仅 `bug` 标签 + high/critical,排除"忽略")→ 动作(评论上下文模板化进 prompt 交给该 agent;产物回发评论;可选通知)→ 护栏(开启去重 + 频率上限防刷屏)→ 保存启用。被 @ 时事件总线匹配 → 创建 run → agent 介入。

**流程 C:配置 Webhook 接收外部事件**:触发器选"外部 Webhook",系统生成入站 URL 与签名密钥(密钥仅显示一次,提示妥善保存)→ 把 URL 与密钥配到外部系统 → 设 `payload_match`(如仅 `severity=critical`)与去重键模板 → 动作(建 issue + 值班 agent 诊断 + 发评论 + 通知)→ 外部事件到达 → 签名校验 → 去重 → 匹配 → run 执行,`webhook_events` 全程留痕。

### 4.4 状态流转

**规则状态机**:
```
[*] ──创建并启用──► active;[*] ──创建为草稿/暂停──► paused
active ──暂停 / kill switch──► paused ──启用 / kill switch 恢复──► active
active / paused ──删除(软删除)──► archived ──恢复(可选)──► active
archived ──定期物理清理──► [*]
```

**Run 状态机**:
```
[*] ──触发创建──► pending
pending ──通过护栏检查并派发──► running;pending ──创建后即取消──► cancelled
pending ──命中人工确认点(建 approvals,subject_type='autopilot_action')──► waiting_approval
waiting_approval ──approvals 批准──► running;waiting_approval ──approvals 拒绝/过期──► cancelled(approval_expired)
running ──全部动作成功──► succeeded
running ──可重试错误且未达上限──► retrying ──退避后重试──► running;retrying ──达最大重试──► failed
running ──不可重试错误──► failed
running ──用户取消 / kill switch──► cancelled
succeeded / failed / cancelled ──► [*]
```
> **与全系统统一长任务状态机的衔接(README §6.4)**:`run_agent_prompt` 动作派发为逻辑执行 `task_executions`(经 `autopilot_runs.execution_id` 复合关联),其生命周期遵循 README §6.4 统一词汇 `queued→claimed→running→completed|failed|timeout|cancelled`(另有 `requeued`/`cancelling`/`awaiting_approval` 等恢复/审批态)。**autopilot_runs 是上层编排记录,task_executions 是下层执行真源**:autopilot_runs 观察底层 execution 终态——execution `completed` → 该动作步骤成功;execution `failed`/`timeout` 且错误可重试 → autopilot_runs 进 `retrying`(退避后入队新 execution 或新 attempt);不可重试 → autopilot_runs `failed`。autopilot_runs 的 `succeeded` 对应底层 execution 的 `completed`(两层状态机命名不同但语义对齐)。需审批时:autopilot 在 `approvals` 建 `subject_type='autopilot_action'` 行,`autopilot_runs.status='waiting_approval'`,批准后续跑、拒绝/过期 → `cancelled(approval_expired)`(README §6.10)。

**重试与退避**:可重试错误(超时、限流、瞬时网络)走指数退避 + 抖动自动重试;不可重试错误(配置、鉴权、参数)直接 `failed` 并告警。退避 `delay = min(retry_base × 2^n, retry_max) × jitter`;每次尝试记入 `autopilot_run_attempts`,run 上累加 `retry_count`。

### 4.5 调度实时性方案

**定时调度(扫描式 + 数据库为唯一事实源,调度 worker 见 README §2.2)**:
- 调度 worker 按固定节拍(10~30s)以 `FOR UPDATE SKIP LOCKED` 扫描 `idx_autopilot_schedule`,取出 `next_run_at <= now()` 的 active 规则;
- 取出后用**行级锁/原子更新**(`UPDATE ... SET next_run_at=<下次> WHERE id=? AND next_run_at=? RETURNING *`)抢占,避免多副本重复触发(乐观并发,谁更新成功谁执行);
- 立即重算并写回 `next_run_at`(基于 cron + 时区);按 `misfire_policy` 处理错过触发;
- 优势:无需额外调度中间件,PostgreSQL 即调度状态机,天然支持水平扩展与故障转移;调度 worker 崩溃后下一扫描周期补发(README §2.2)。

**事件驱动(transactional outbox 消费,README §6.6)**:
- 业务侧(issue 状态变更、评论、@提及)在**同一业务事务内**写 `outbox_events`(`event_type` 如 `issue.status_changed` / `comment.created`,携带 §6.5 幂等键);**禁止进程内事件总线或事务外直接派生**(README §6.6 硬约束);
- **autopilot 调度器/事件匹配器是 outbox 的 relay 消费方**:以 `FOR UPDATE SKIP LOCKED` 领取 `outbox_events(status='pending')`,加载 `trigger_type` 匹配且 active 的规则,逐条做 filter 匹配与护栏检查,命中则在创建 run 的事务内同事务写新的 outbox 事件(执行入队 / 通知),处理成功后置原事件 `published`;
- run 实际执行交给异步 worker 池(受 `concurrency_limit` 与 agent 运行时容量约束),`run_agent_prompt` 落地为 `task_executions`(README §6.4),状态经 WebSocket 实时推送;
- **动作副作用幂等(README §6.5)**:`add_comment`、`http_request`(出向 Webhook)、执行入队均携带稳定幂等键(如执行入队 `sha256(agent_id|issue_id|trigger_event_id)`、出向 `sha256(execution_id|attempt_number|target|event)`),接收方以 `Idempotency-Key` 去重,at-least-once 投递对客户端表现为恰好一次;
- Webhook 入站走同一 outbox 消费管线,事件来源是 HTTP(落 `webhook_events`,其自有 `UNIQUE(workspace_id, idempotency_key)` 去重保留)而非内部领域事件。

### 4.6 实时性与通知

> **通知分发一律按 README §6.13 唯一通知优先级矩阵**(本表为其在 autopilot 域的对齐,不另行定义分级;`notifications.priority` 由服务端按矩阵派生)。

| 时机 | priority | 通知对象 | 通道 / 行为(README §6.13) |
|------|----------|----------|------|
| 运行成功(可选) | **normal** | 规则所有者 | **默认留运行页/时间线,不进收件箱**;仅当所有者在 `notification_preferences` 显式订阅"执行结果"时才进收件箱;**不穿透 quiet hours、不重置同组未读**;邮件默认 none(订阅后 digest) |
| 运行失败 / 连续失败 | **critical** | 所有者 + 配置接收人 | **进收件箱 + 穿透 quiet hours + 重置同组未读**;可选出向 Webhook / 邮件(realtime) |
| 命中频率上限被熔断 | **critical** | 所有者 | 进收件箱 + 告警(穿透 quiet hours、重置未读) |
| 需人工确认(审批门) | **critical** | 所有者 / 指定审批人 | **进统一"待我审批"收件箱 + 穿透 quiet hours + 重置同组未读**(realtime 邮件),run 进 `waiting_approval`(README §6.10) |
| kill switch 触发 | normal | workspace 管理员 | 站内 inbox(管理员操作回执,不穿透 quiet hours) |

> 通知带深链直达 run 详情页。**告警去重与静默窗口**:同规则同类告警在窗口内合并,避免风暴;连续失败计数在成功后清零。**自我抑制**:动作发起者不给自己生成通知(README §6.13);**执行成功不重置未读**,仅 critical 事件(失败/熔断/审批)重新置未读。

---

## 5. 验收标准

### 5.1 功能性

- [ ] 规则 = 触发器 + 过滤 + 动作 + 执行者;`run_agent_prompt` 必须绑定 `executor_agent_id`,缺失返回 422 `executor_required`。
- [ ] 定时触发器要求显式 IANA 时区;`preview-schedule` 返回未来 N 次运行;非法 cron 返回 400 `invalid_cron`;支持一次性定时与 misfire 策略。
- [ ] 事件触发器声明关注事件类型与对象范围;事件载荷快照进 run(`trigger_snapshot`),可回溯可重放。
- [ ] 过滤维度间 AND、同类多值 OR;`payload_match` 支持对 Webhook 载荷做 JSONPath/键匹配。
- [ ] 多动作顺序执行;prompt 模板变量运行时填充;`{{steps.N.output}}` 引用前序动作产物。
- [ ] 每次触发生成 `autopilot_runs`,含触发快照/状态/耗时/产物/token/错误/重试计数;`autopilot_run_attempts` 记录每次尝试明细;`autopilot_artifacts` 解耦关联产物。
- [ ] 失败重试区分可重试/不可重试错误;指数退避 + 抖动 + 封顶;达最大重试标记 `failed` 并告警。
- [ ] 入站 Webhook:HMAC 签名恒定时间比较 + 时间戳防重放;去重唯一键命中返回 200 `deduped` 不再分发;`webhook_events` 全程留痕。
- [ ] 调度以 PostgreSQL 为唯一事实源,原子抢占(`UPDATE ... WHERE next_run_at=? RETURNING`)杜绝多实例重复触发。
- [ ] **事件触发出自 transactional outbox(README §6.6 / §9 T5)**:业务侧同事务写 `outbox_events`,autopilot 事件匹配器以 SKIP LOCKED relay 消费;**业务提交后、relay 分发前杀 relay 进程 → 重启后触发事件仍被投递(run 被创建),无丢失**;不存在进程内事件总线。
- [ ] **动作副作用幂等(README §6.5 / §9 T13/T7 式)**:`add_comment` / `http_request` / 执行入队携带稳定幂等键,接收方以 `Idempotency-Key` 去重;同一触发事件重复投递,执行只入队一次(`sha256(agent_id|issue_id|trigger_event_id)`);重复出向请求只产生一次副作用。
- [ ] **多租户复合 FK(README §6.2 / §9 T1)**:`autopilots`/`webhook_events` 建 `UNIQUE(workspace_id, id)`;`executor_agent_id`→`agents(workspace_id,id)`、`created_by`/`triggered_by`→`members(workspace_id,id)`、`autopilot_runs.autopilot_id`/`webhook_events.autopilot_id`→`autopilot(workspace_id,id)`、`execution_id`→`task_executions(workspace_id,id)` 均为复合 FK;构造跨 workspace 复合 FK 插入被数据库约束拒绝,A 区凭证访问 B 区规则/run → 403/404。

### 5.2 性能

- [ ] 调度扫描走 `idx_autopilot_schedule` 部分索引,无全表扫描;百万级规则下取出到期规则 P95 < 200ms。
- [ ] 执行历史列表走 `idx_run_autopilot_started`;运行详情 P95 < 150ms。
- [ ] 事件去重走 `uq_webhook_event_idem` 唯一索引,高并发入站幂等无重复执行。
- [ ] 游标分页在百万级 run 行下稳定(无 OFFSET 深翻页)。

### 5.3 安全

- [ ] **频率上限(默认开启)**:单规则窗口内超限按 `rate_limit_overflow` 处理(默认 `drop` + 告警),并产生审计记录通知所有者。
- [ ] **去重/幂等(默认开启)**:同一事件(`idempotency_key`)在去重窗口内只执行一次,防重复投递/回调。
- [ ] **并发上限**:单规则同时运行 run 数受 `concurrency_limit` 约束(默认 1 串行),防慢任务堆积。
- [ ] **人工确认点(统一审批,README §6.10)**:`require_approval=true` 或动作命中 `approval_required_actions`(出向 HTTP、建 issue)时,在 `approvals` 建 `subject_type='autopilot_action'` 行(经 `approvals.subject_run_id` **复合 FK `(workspace_id, subject_run_id) → autopilot_runs(workspace_id, id)`** 关联,R2 已升为物理复合 FK),run 停在 `waiting_approval`;**同一 run 仅一个 pending approval**(README §6.10 部分唯一索引 `uq_approvals_pending_run`,重复发起取既有 pending 返回);approve/reject 经 `POST /api/v1/approvals/{id}/approve|reject` 收口(`runs/{run_id}/approve|reject` 为薄封装),批准续跑、拒绝 → `cancelled`。
- [ ] **审批过期(README §9 T8)**:创建 approval → 到期 → 关联 run 转 `cancelled(approval_expired)` + 触发者/创建者收通知;过期后再 approve → no-op/`410 gone`;重复 approve/reject 对同一 approval 幂等(返回当前状态)。
- [ ] **通知按唯一优先级矩阵分发(README §6.13 / §9 T25)**:运行**成功**为 normal、默认留运行页(仅订阅时进收件箱,**不穿透 quiet hours、不重置未读**);运行**失败/连续失败**与**审批门**为 critical(进收件箱 + **穿透 quiet hours** + **重置同组未读**);熔断告警为 critical;`notifications.priority` 由服务端按矩阵派生,与 README §6.13 逐事件一致,不另行定义分级。
- [ ] **全局 kill switch**:workspace 管理员一键暂停所有 autopilot(紧急止血),恢复时按各规则原状态还原;规则级、agent 级暂停同样可用。
- [ ] **防回环(agent↔agent)**:`agent_loop_detection` + `agent_loop_window_seconds` 对同一 `(executor_agent, 触发对象)` 对在时间窗内 run 去重;`cascade_depth` 超 `cascade_max_depth` 拒绝创建下游 run(返回 422 `cascade_depth_exceeded`),切断 agent 互提成环。
- [ ] **预算护栏**:单 run/单规则/单日 token 与运行次数预算,超限熔断,防 agent 失控刷量。
- [ ] **作用域/最小权限**:规则只能在创建者权限范围内操作;agent 动作受 agent 自身权限约束。
- [ ] Webhook 密钥仅存哈希/引用,创建后仅显示一次,响应/日志不回显;签名失败返回 401;状态切换与护栏命中均写 auth.md 审计日志。
- [ ] **出向 HTTP 动作 SSRF 防护**:`http_request` 类出向动作禁止访问私网地址段(RFC1918 / link-local / 云元数据),仅允许公网地址或配置的主机白名单;出向请求受 agent 权限与 `confirm_required` 人工确认门约束。

### 5.4 实时

- [ ] run 状态变化经 `autopilot_runs.status_changed` 实时推送(带 `seq`),运行中/成功/失败即时刷新,无需手动刷新。
- [ ] 命中人工确认点推送 `autopilot_runs.approval_required` 并落 inbox;熔断推送 `autopilot.rate_limited`。
- [ ] 入站事件落库推送 `webhook_events.received`(含签名/处理状态)。
- [ ] 客户端断线重连凭 `seq` 重放,无丢失无重复;WebSocket 不可用时降级轮询 run 详情(3~5s),功能等价。
