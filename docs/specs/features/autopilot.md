# 自动化(Autopilot)功能 Spec

> **所属层**:智能体编排层("触发器 + 条件 + 动作"的自动化规则引擎 —— AI 队友的值班表)。
> **依赖的其他 Spec**:
> - `workspace.md`:`autopilot.workspace_id` 等外键回 `workspaces.id`;kill switch 为 workspace 级。
> - `member.md`:规则创建者、run 触发者引用 `members.id`;执行 agent 引用 `agents.id`。
> - `agent.md`:执行者 agent 的运行时/技能/权限决定动作能力边界;规则只"派单",不持有执行能力。
> - `issue.md`:事件触发器监听 issue 状态/字段变更、新评论、@提及;动作可改 issue 字段、创建 issue。
> - `comment-inbox.md` / `chat-session.md`:`add_comment` 动作落评论;`agent_mentioned` 触发器来自提及管线;通知经统一收件箱。
> - `runtime.md`:`run_agent_prompt` 动作派发给 agent,落地为运行记录(`agent_runs`),其生命周期遵循 runtime 长任务状态机 `queued→claimed→running→completed|failed|cancelled`;本 Spec 经 run 关联观察其终态。
> - `auth.md`:RBAC、审计、限流;入站 Webhook 用 HMAC 签名校验(非 Bearer)。
> **被依赖方**:`squad.md`(自动化可把任务派给整个小队)、运营/监控集成。

---

## 1. 功能描述

### 1.1 模块定位

自动化(Autopilot)是 Mesh 的**规则引擎**,把重复性的运营、监控、响应工作交给 agent 自动完成。一条规则 = **何时触发(trigger)+ 是否满足条件(filter)+ 做什么(action,通常是把一段 prompt 交给指定 agent 执行)**。在 Mesh 语境里,autopilot 是"AI 队友的值班表":agent 不必等人召唤,而是按约定(定时)或按事件(状态变更、被 @、外部回调)自动上岗。

**核心设计:派单与执行解耦**。规则只描述"触发器+条件+动作",执行能力完全来自被指派的 agent(其 runtime / 技能 / 权限)。这样 autopilot 模块保持纯粹,agent 能力升级即自动惠及所有规则,无需改动规则引擎。`executor_agent_id` 是一等公民,agent 权限是动作的硬边界(最小权限)。

**防失控护栏是默认开启的一等公民,而非可选项**。失控的自动化比没有自动化更危险 —— 尤其当事件源是外部 Webhook(可能重复/被刷)或 agent 之间互相 @ 触发(可能成环)时。频率上限、事件去重、并发上限、人工确认点、全局 kill switch、预算上限六件套,在创建规则时即以合理默认值生效。

> **全局名册约定(与 member.md 一致)**:规则创建者、run 触发者为成员,引用 `members.id`(`member_type ∈ {human, agent}`);执行者明确指向某 agent 定义,引用 `agents.id`。

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
- **不**实现调度中间件(独立 broker / 定时器服务)—— 以 PostgreSQL 为唯一调度事实源,原子抢占实现可扩展定时。
- **不**定义出向 HTTP 动作的目标系统协议 —— 仅声明出向请求受 agent 权限与人工确认门约束。
- **不**支持可视化拖拽编排复杂 DAG 工作流(YAGNI;多动作顺序执行 + 模板变量已覆盖主流场景)。
- **不**做跨 workspace 的全局规则(YAGNI;kill switch 仅 workspace 级)。

---

## 2. 数据模型

### 2.1 ER 概览(文字图)

```
workspaces ──隔离──► autopilot(规则)──1:N──► autopilot_run(每次执行)
                       │  (trigger/filter/action JSONB)        │  ├─1:N─► autopilot_run_attempt(重试明细)
                       │  executor_agent_id ──► agents         │  ├─1:N─► autopilot_artifact(产物引用)
                       │  created_by ──► members               │  └─自引用─► parent_run_id / cascade_depth(级联)
                       │                                        │
                       └──1:N──► webhook_event(外部事件接收: 签名/去重/审计)──► 路由匹配规则
                                                                        ▲
agents(agent.md):executor_agent_id;members(member.md):created_by / triggered_by
runtime.md:run_agent_prompt 动作派发为 agent_runs(经 run 关联),终态 completed|failed|cancelled
```

### 2.2 表:`autopilot`(自动化规则定义)

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
| `executor_agent_id` | UUID | NULL,FK→agents(id) | NULL | 执行者 agent;`run_agent_prompt` 必填 |
| `status` | TEXT | NOT NULL,CHECK IN ('active','paused','archived') | `'active'` | 规则状态 |
| `guardrails` | JSONB | NOT NULL | 见 §2.6 | 护栏配置 |
| `max_retries` | INT | NOT NULL,CHECK (>= 0) | `3` | 最大重试次数 |
| `retry_backoff` | TEXT | NOT NULL,CHECK IN ('fixed','linear','exponential') | `'exponential'` | 退避策略 |
| `retry_base_seconds` | INT | NOT NULL,CHECK (> 0) | `30` | 退避基数 |
| `retry_max_seconds` | INT | NOT NULL,CHECK (> 0) | `1800` | 退避封顶 |
| `rate_limit_max` | INT | NOT NULL,CHECK (>= 0) | `10` | 窗口内最大触发数 |
| `rate_limit_window_seconds` | INT | NOT NULL,CHECK (> 0) | `3600` | 频率窗口(秒) |
| `concurrency_limit` | INT | NOT NULL,CHECK (>= 1) | `1` | 并发 run 上限(默认串行) |
| `require_approval` | BOOLEAN | NOT NULL | `false` | 是否需人工确认点 |
| `next_run_at` | TIMESTAMPTZ | NULL | NULL | 下次定时触发时刻(调度索引用) |
| `last_run_at` | TIMESTAMPTZ | NULL | NULL | 上次运行时刻 |
| `created_by` | UUID | NOT NULL,FK→members(id) | — | 创建者(人或 agent) |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**唯一约束**:`UNIQUE (workspace_id, name) WHERE deleted_at IS NULL`(软删除范围内名称唯一)。

### 2.3 表:`autopilot_run`(每次执行记录)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `autopilot_id` | UUID | NOT NULL,FK→autopilot(id) | — | 所属规则 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 冗余,便于按 workspace 查询 |
| `trigger_type` | TEXT | NOT NULL | — | 触发类型快照 |
| `trigger_snapshot` | JSONB | NOT NULL | `'{}'` | 触发事件输入快照(可重放) |
| `webhook_event_id` | UUID | NULL,FK→webhook_event(id) | NULL | 关联入站事件(若适用) |
| `agent_run_id` | UUID | NULL | NULL | `run_agent_prompt` 派发的运行记录(→ runtime.md `agent_runs`) |
| `parent_run_id` | UUID | NULL,FK→autopilot_run(id) | NULL | 触发本 run 的上游 run(agent→agent 级联溯源) |
| `cascade_depth` | INT | NOT NULL,CHECK (>= 0) | `0` | agent→agent 级联深度(超阈值截断,见 §2.6) |
| `status` | TEXT | NOT NULL,CHECK IN ('pending','running','waiting_approval','retrying','succeeded','failed','cancelled') | `'pending'` | 运行状态(见 §4.4) |
| `started_at` | TIMESTAMPTZ | NULL | NULL | 开始时间 |
| `finished_at` | TIMESTAMPTZ | NULL | NULL | 结束时间 |
| `duration_ms` | INT | NULL | NULL | 耗时(毫秒) |
| `retry_count` | INT | NOT NULL,CHECK (>= 0) | `0` | 已重试次数 |
| `error` | JSONB | NULL | NULL | 错误信息 `{code,message,retryable,detail}` |
| `prompt_tokens` | INT | NULL,CHECK (>= 0) | NULL | 输入 token |
| `completion_tokens` | INT | NULL,CHECK (>= 0) | NULL | 输出 token |
| `total_tokens` | INT | GENERATED ALWAYS AS (COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)) STORED | — | 合计 token |
| `triggered_by` | UUID | NULL,FK→members(id) | NULL | 触发者(手动 test run 时为操作者;定时/事件可空) |
| `is_test` | BOOLEAN | NOT NULL | `false` | 是否手动测试运行 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

### 2.4 表:`autopilot_run_attempt`(重试明细)与 `autopilot_artifact`(产物)

**`autopilot_run_attempt`**(每次尝试一行,便于精确统计与排障):

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `run_id` | UUID | NOT NULL,FK→autopilot_run(id) | — | 所属 run |
| `attempt_number` | INT | NOT NULL,CHECK (>= 1) | — | 第几次尝试 |
| `status` | TEXT | NOT NULL | — | 本次尝试结果 |
| `agent_run_id` | UUID | NULL | NULL | 本次尝试派发的运行记录 |
| `started_at` / `finished_at` | TIMESTAMPTZ | NULL | NULL | 起止 |
| `error` | JSONB | NULL | NULL | 本次错误 |
| `prompt_tokens` / `completion_tokens` | INT | NULL | NULL | 本次 token |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

约束:`UNIQUE (run_id, attempt_number)`。

**`autopilot_artifact`**(产物引用,把 run 与它产生的对象解耦关联):

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `run_id` | UUID | NOT NULL,FK→autopilot_run(id) | — | 所属 run |
| `artifact_type` | TEXT | NOT NULL,CHECK IN ('comment','issue','notification','agent_output','http_response') | — | 产物类型 |
| `ref_table` | TEXT | NOT NULL | — | 被引用对象所在表 |
| `ref_id` | UUID | NOT NULL | — | 被引用对象 id |
| `summary` | TEXT | NULL | NULL | 产物摘要 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

### 2.5 表:`webhook_event`(外部事件接收记录:去重与审计)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 归属 |
| `autopilot_id` | UUID | NULL,FK→autopilot(id) | NULL | 路由到的规则(空=未匹配) |
| `idempotency_key` | TEXT | NOT NULL | — | 去重键(事件 ID 或签名/内容哈希) |
| `event_type` | TEXT | NOT NULL | — | 事件类型(由来源/载荷解析) |
| `headers` | JSONB | NULL | NULL | 入站请求头(脱敏后) |
| `payload` | JSONB | NOT NULL | — | 原始载荷 |
| `signature_status` | TEXT | NOT NULL,CHECK IN ('valid','invalid','missing','skipped') | — | 签名校验结果(**`invalid`/`missing` 一律 `rejected` + 401,绝不分发**;`skipped` 仅限 test-run 场景,不产生 `autopilot_run`) |
| `process_status` | TEXT | NOT NULL,CHECK IN ('received','matched','dispatched','deduped','rejected','processed','failed') | `'received'` | 处理状态 |
| `received_at` | TIMESTAMPTZ | NOT NULL | `now()` | 接收时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**去重唯一键**:`UNIQUE (workspace_id, idempotency_key)`。入站时先尝试插入,命中唯一冲突即视为重复,直接返回成功(幂等)但不再分发。

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
> - `agent_loop_detection` + `agent_loop_window_seconds`:同一 `(executor_agent, 触发对象)` 对在时间窗内 run 去重,防 agent 互提成环。

### 2.7 索引与约束

```sql
-- 调度器扫描:按下次运行时间取出到期的 active 定时规则
CREATE INDEX idx_autopilot_schedule ON autopilot(next_run_at)
  WHERE status = 'active' AND trigger_type = 'schedule' AND deleted_at IS NULL;
-- 事件匹配:按触发类型 + 状态找候选规则
CREATE INDEX idx_autopilot_trigger ON autopilot(trigger_type, status) WHERE deleted_at IS NULL;
-- 名称唯一(软删除范围内)
CREATE UNIQUE INDEX uq_autopilot_ws_name ON autopilot(workspace_id, name) WHERE deleted_at IS NULL;

-- 执行历史:某规则 run 时间线 / workspace 维度 / 状态过滤
CREATE INDEX idx_run_autopilot_started ON autopilot_run(autopilot_id, started_at DESC);
CREATE INDEX idx_run_workspace_started ON autopilot_run(workspace_id, created_at DESC);
CREATE INDEX idx_run_status ON autopilot_run(status)
  WHERE status IN ('running','retrying','waiting_approval','pending');
-- 级联溯源:由某 run 触发的下游 run
CREATE INDEX idx_run_parent ON autopilot_run(parent_run_id) WHERE parent_run_id IS NOT NULL;

CREATE UNIQUE INDEX uq_run_attempt ON autopilot_run_attempt(run_id, attempt_number);
CREATE INDEX idx_artifact_run ON autopilot_artifact(run_id);

-- 事件去重唯一键 / 按规则与处理状态查询
CREATE UNIQUE INDEX uq_webhook_event_idem ON webhook_event(workspace_id, idempotency_key);
CREATE INDEX idx_webhook_event_route ON webhook_event(autopilot_id, process_status, received_at DESC);
```

### 2.8 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `autopilot.workspace_id` 等 | → `workspaces.id` | workspace.md | 隔离 |
| `autopilot.created_by` / `autopilot_run.triggered_by` | → `members.id` | member.md | 创建者/触发者(人或 agent) |
| `autopilot.executor_agent_id` | → `agents.id` | agent.md | 执行者 agent(动作能力边界) |
| `autopilot_run.agent_run_id` | → `agent_runs.id` | runtime.md | `run_agent_prompt` 派发的运行实例 |
| `autopilot_artifact.ref_id`(`ref_table='comments'/'issues'`) | → 评论/issue | comment-inbox.md / issue.md | 动作产物 |

---

## 3. 接口设计

REST 基础路径 `/api/v1`,集合嵌套于 `/workspaces/{ws}/`;鉴权 `Authorization: Bearer <token>`(**入站 Webhook 端点除外**,用 HMAC 签名校验);时间 RFC3339 UTC,id 均为 UUID。统一错误信封 `{"error":{"code","message","details"}}`;列表游标分页 `{"data":[...],"next_cursor"}`(`next_cursor` 为 null 表示末页);单资源端点直接返回资源对象本体。

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
| POST | `/workspaces/{ws}/autopilot-runs/{run_id}/approve` | 人工确认通过(审批门) | 审批人 / admin |
| POST | `/workspaces/{ws}/autopilot-runs/{run_id}/reject` | 人工确认拒绝 | 审批人 / admin |
| POST | `/workspaces/{ws}/autopilots/kill-switch` | 全局暂停/恢复所有 autopilot | admin |
| POST | `/webhooks/inbound/{token}` | 接收外部 Webhook(HMAC 签名校验,非 Bearer) | 签名校验 |
| POST | `/workspaces/{ws}/webhook-secrets` | 创建/轮换 Webhook 密钥 | admin |
| GET | `/workspaces/{ws}/webhook-secrets` | 列出密钥(不返回明文) | admin |

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
{ "id": "3b7d1f0e-2c4a-4e1b-9f8a-1d2e3f4a5b6c", "workspace_id": "7ea1...",
  "name": "每日站会前汇总进展", "trigger_type": "schedule",
  "trigger_config": {"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai", "misfire_policy": "run_once"},
  "filter_config": {"project_ids": ["6f1c..."]}, "action_config": [ ],
  "executor_agent_id": "a9e2...", "status": "active",
  "guardrails": {"rate_limit_overflow": "drop", "dedup_window_seconds": 300, "cascade_max_depth": 3, "agent_loop_detection": true},
  "max_retries": 3, "retry_backoff": "exponential", "rate_limit_max": 5,
  "rate_limit_window_seconds": 3600, "concurrency_limit": 1, "require_approval": false,
  "next_run_at": "2026-07-27T01:00:00Z",
  "created_at": "2026-07-24T12:00:00Z", "updated_at": "2026-07-24T12:00:00Z" }
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
{ "run_id": "c0a8...", "status": "pending", "autopilot_id": "3b7d...", "is_test": true }
// dry_run=true → 200:{"would_run": true, "matched_filters": {"labels": ["bug"]}}
```

**单次运行详情** `GET /api/v1/workspaces/{ws}/autopilot-runs/{run_id}`
```json
{ "id": "c0a8...", "autopilot_id": "3b7d...", "status": "succeeded",
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
  "error": null }
```

**全局 kill switch** `POST /api/v1/workspaces/{ws}/autopilots/kill-switch`
```json
// Request
{ "enabled": true, "reason": "紧急止血:批量异常" }
// 200 Response
{ "kill_switch": true, "paused_autopilots": 14, "updated_at": "2026-07-24T04:00:00Z" }
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
// 200(幂等,重复事件同样 200)
{ "received": true, "event_id": "evt_9f2a...", "process_status": "dispatched", "run_id": "c0a8..." }
// 重复事件
{ "received": true, "event_id": "evt_9f2a...", "process_status": "deduped", "run_id": null }
// 签名失败 401
{ "error": {"code": "invalid_signature", "message": "Webhook 签名校验失败", "details": {}} }
```
**处理流程**:接收 → 落库 `webhook_event`(`received`)→ 校验签名(`signature_status`)→ **签名 `invalid`/`missing` 一律置 `process_status='rejected'` 并返回 401,绝不分发不路由** → 计算/读取 `idempotency_key` 尝试去重插入(命中则 `deduped` 直接返回)→ 路由匹配规则 → 频率/护栏检查 → 创建 `autopilot_run`(`dispatched`)→ 异步执行。
> **Webhook 触发器强制配置签名密钥**:创建 `trigger_type='webhook_received'` 的规则时,必须已配置有效的签名密钥(服务端创建时校验,未配置返回 422);`skipped` 状态仅限 `is_test=true` 的 test-run 场景;**无有效签名的事件永不产生 `autopilot_run`**。

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

连接 `/ws`(握手鉴权见 auth.md),订阅频道 `workspace:{ws}:autopilots` 或 `autopilot:{id}`。事件命名 `<entity>.<action>`,携带频道内单调递增 `seq`,断线凭 `seq` 重放。

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `autopilot_run.status_changed` | run 状态流转(驱动详情/列表实时刷新) | `run_id`, `autopilot_id`, `old_status`, `new_status` |
| `autopilot_run.approval_required` | run 命中人工确认点 | `run_id`, `autopilot_id` |
| `autopilot.rate_limited` | 规则触发频率超限被熔断 | `autopilot_id`, `window`, `dropped` |
| `autopilot.updated` | 规则配置/状态变更 | `autopilot_id`, `status` |
| `webhook_event.received` | 入站事件落库 | `event_id`, `process_status`, `signature_status` |

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

**流程 C:配置 Webhook 接收外部事件**:触发器选"外部 Webhook",系统生成入站 URL 与签名密钥(密钥仅显示一次,提示妥善保存)→ 把 URL 与密钥配到外部系统 → 设 `payload_match`(如仅 `severity=critical`)与去重键模板 → 动作(建 issue + 值班 agent 诊断 + 发评论 + 通知)→ 外部事件到达 → 签名校验 → 去重 → 匹配 → run 执行,`webhook_event` 全程留痕。

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
pending ──命中人工确认点──► waiting_approval ──人工 approve──► running;waiting_approval ──人工 reject──► cancelled
running ──全部动作成功──► succeeded
running ──可重试错误且未达上限──► retrying ──退避后重试──► running;retrying ──达最大重试──► failed
running ──不可重试错误──► failed
running ──用户取消 / kill switch──► cancelled
succeeded / failed / cancelled ──► [*]
```
> **与 runtime 长任务状态机的衔接**:`run_agent_prompt` 动作派发为 runtime 运行记录(`agent_runs`,经 `agent_run_id` 关联),其生命周期遵循 runtime 长任务状态机 `queued→claimed→running→completed|failed|cancelled`。autopilot_run 是上层记录,观察底层 agent_run 终态:agent_run `completed` → 该动作步骤成功;agent_run `failed` 且错误可重试 → autopilot_run 进 `retrying`。autopilot_run 的 `succeeded` 对应底层 agent_run 的 `completed`(两层状态机命名不同但语义对齐)。

**重试与退避**:可重试错误(超时、限流、瞬时网络)走指数退避 + 抖动自动重试;不可重试错误(配置、鉴权、参数)直接 `failed` 并告警。退避 `delay = min(retry_base × 2^n, retry_max) × jitter`;每次尝试记入 `autopilot_run_attempt`,run 上累加 `retry_count`。

### 4.5 调度实时性方案

**定时调度(扫描式 + 数据库为唯一事实源)**:
- 常驻调度协程按固定节拍(10~30s)扫描 `idx_autopilot_schedule`,取出 `next_run_at <= now()` 的 active 规则;
- 取出后用**行级锁/原子更新**(`UPDATE ... SET next_run_at=<下次> WHERE id=? AND next_run_at=? RETURNING *`)抢占,避免多实例重复触发(乐观并发,谁更新成功谁执行);
- 立即重算并写回 `next_run_at`(基于 cron + 时区);按 `misfire_policy` 处理错过触发;
- 优势:无需额外调度中间件,PostgreSQL 即调度状态机,天然支持水平扩展与故障转移。

**事件驱动(发布订阅)**:
- 业务侧(issue 状态变更、评论、@提及)在事务提交后向进程内事件总线发布领域事件(outbox 模式保证"业务写入"与"事件发布"一致);
- 订阅方加载 `trigger_type` 匹配且 active 的规则,逐条做 filter 匹配与护栏检查,命中则创建 run 入队;
- run 实际执行交给异步 worker 池(受 `concurrency_limit` 与 agent 运行时容量约束),状态经 WebSocket 实时推送;
- Webhook 入站走同一事件管线,事件来源是 HTTP 而非内部领域事件。

### 4.6 实时性与通知

| 时机 | 通知对象 | 通道 |
|------|----------|------|
| 运行成功(可选) | 规则所有者 | 站内 inbox |
| 运行失败 / 连续失败 | 所有者 + 配置接收人 | inbox + 可选出向 Webhook / 邮件 |
| 命中频率上限被熔断 | 所有者 | inbox + 告警 |
| 需人工确认(审批门) | 所有者 / 指定审批人 | inbox + 推送,run 进 `waiting_approval` |
| kill switch 触发 | workspace 管理员 | inbox |

> 通知带深链直达 run 详情页。**告警去重与静默窗口**:同规则同类告警在窗口内合并,避免风暴;连续失败计数在成功后清零。

---

## 5. 验收标准

### 5.1 功能性

- [ ] 规则 = 触发器 + 过滤 + 动作 + 执行者;`run_agent_prompt` 必须绑定 `executor_agent_id`,缺失返回 422 `executor_required`。
- [ ] 定时触发器要求显式 IANA 时区;`preview-schedule` 返回未来 N 次运行;非法 cron 返回 400 `invalid_cron`;支持一次性定时与 misfire 策略。
- [ ] 事件触发器声明关注事件类型与对象范围;事件载荷快照进 run(`trigger_snapshot`),可回溯可重放。
- [ ] 过滤维度间 AND、同类多值 OR;`payload_match` 支持对 Webhook 载荷做 JSONPath/键匹配。
- [ ] 多动作顺序执行;prompt 模板变量运行时填充;`{{steps.N.output}}` 引用前序动作产物。
- [ ] 每次触发生成 `autopilot_run`,含触发快照/状态/耗时/产物/token/错误/重试计数;`autopilot_run_attempt` 记录每次尝试明细;`autopilot_artifact` 解耦关联产物。
- [ ] 失败重试区分可重试/不可重试错误;指数退避 + 抖动 + 封顶;达最大重试标记 `failed` 并告警。
- [ ] 入站 Webhook:HMAC 签名恒定时间比较 + 时间戳防重放;去重唯一键命中返回 200 `deduped` 不再分发;`webhook_event` 全程留痕。**签名 `invalid`/`missing` 一律 `rejected` + 401,不分发不路由;`skipped` 仅限 test-run;Webhook 触发器创建时强制配置签名密钥(未配置返回 422);无有效签名的事件永不产生 `autopilot_run`。**
- [ ] 调度以 PostgreSQL 为唯一事实源,原子抢占(`UPDATE ... WHERE next_run_at=? RETURNING`)杜绝多实例重复触发。

### 5.2 性能

- [ ] 调度扫描走 `idx_autopilot_schedule` 部分索引,无全表扫描;百万级规则下取出到期规则 P95 < 200ms。
- [ ] 执行历史列表走 `idx_run_autopilot_started`;运行详情 P95 < 150ms。
- [ ] 事件去重走 `uq_webhook_event_idem` 唯一索引,高并发入站幂等无重复执行。
- [ ] 游标分页在百万级 run 行下稳定(无 OFFSET 深翻页)。

### 5.3 安全

- [ ] **频率上限(默认开启)**:单规则窗口内超限按 `rate_limit_overflow` 处理(默认 `drop` + 告警),并产生审计记录通知所有者。
- [ ] **去重/幂等(默认开启)**:同一事件(`idempotency_key`)在去重窗口内只执行一次,防重复投递/回调。
- [ ] **并发上限**:单规则同时运行 run 数受 `concurrency_limit` 约束(默认 1 串行),防慢任务堆积。
- [ ] **人工确认点**:`require_approval=true` 或动作命中 `approval_required_actions`(出向 HTTP、建 issue)时,run 停在 `waiting_approval`,approve 才继续、reject 取消。
- [ ] **全局 kill switch**:workspace 管理员一键暂停所有 autopilot(紧急止血),恢复时按各规则原状态还原;规则级、agent 级暂停同样可用。
- [ ] **防回环(agent↔agent)**:`agent_loop_detection` + `agent_loop_window_seconds` 对同一 `(executor_agent, 触发对象)` 对在时间窗内 run 去重;`cascade_depth` 超 `cascade_max_depth` 拒绝创建下游 run(返回 422 `cascade_depth_exceeded`),切断 agent 互提成环。
- [ ] **预算护栏**:单 run/单规则/单日 token 与运行次数预算,超限熔断,防 agent 失控刷量。
- [ ] **作用域/最小权限**:规则只能在创建者权限范围内操作;agent 动作受 agent 自身权限约束。
- [ ] Webhook 密钥仅存哈希/引用,创建后仅显示一次,响应/日志不回显;签名失败返回 401;状态切换与护栏命中均写 auth.md 审计日志。
- [ ] **出向 HTTP 动作 SSRF 防护**:`http_request` 类出向动作禁止访问私网地址段(RFC1918 / link-local / 云元数据),仅允许公网地址或配置的主机白名单;出向请求受 agent 权限与 `confirm_required` 人工确认门约束。

### 5.4 实时

- [ ] run 状态变化经 `autopilot_run.status_changed` 实时推送(带 `seq`),运行中/成功/失败即时刷新,无需手动刷新。
- [ ] 命中人工确认点推送 `autopilot_run.approval_required` 并落 inbox;熔断推送 `autopilot.rate_limited`。
- [ ] 入站事件落库推送 `webhook_event.received`(含签名/处理状态)。
- [ ] 客户端断线重连凭 `seq` 重放,无丢失无重复;WebSocket 不可用时降级轮询 run 详情(3~5s),功能等价。
