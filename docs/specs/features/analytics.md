# Analytics(统计报表与仪表盘)功能 Spec

> **所属层**:平台能力层 —— **只读聚合分析**。本模块不拥有任何业务真源数据,而是消费 `issues` / `task_executions` / `execution_attempts` / `autopilot_runs` 等真源表做聚合,产出 cycle time / velocity / 吞吐量 / workload / burndown 与按 agent 维度的运行统计,服务**项目仪表盘**与**工作区仪表盘**。它是终端只读消费层:写路径仍在各 owner Spec,本模块绝不回写源表。
>
> **依赖的其他 Spec(均为只读数据源)**:
> - `issue.md`(工作项,核心数据源):`issues.state_category`(双层状态的稳定类别)、`issues.completed_at`(进入 done 的时间)、`issues.estimate`/`estimate_unit`(点数/工时)、`issues.assignee_id`(workload 维度)、`issues.created_at`(吞吐量)、`issue_activity`(状态迁移留痕,**cycle time 首次进入 in_progress 的取数源**,issue.md §2.2)。
> - `project.md`(项目/里程碑/周期):`projects`(进度聚合口径,project.md §2.4)、`milestones`(`target_date`/`state`,burndown 窗)、`cycles`(`starts_at`/`ends_at`/`state`,velocity 与 burndown 的周期窗)。
> - `runtime.md`(agent 运行统计数据源,§2.2/§2.4):`task_executions`(逻辑执行:`agent_id`/`status`/`queued_at`/`finished_at`/`timeout_seconds`)、`execution_attempts`(物理尝试:`started_at`/`finished_at`/`attempt_number`,重试率派生源)。**`task_executions` 无 token 字段**。
> - `autopilot.md`(token 消耗唯一来源,§2.3):`autopilot_runs.prompt_tokens`/`completion_tokens`/`total_tokens`,经 `autopilot_runs.execution_id → task_executions.id` 关联;**仅 autopilot 触发的执行有 token 数据**(口径差异见 §2.3)。
> - `member.md`(成员维度,README §6.1):统一名册 `members`(`member_type ∈ {human,agent}`),workload 与 agent 统计的展示维度;人类/agent 判别 JOIN `members.member_type`。
>
> **被依赖(下游)**:无后端 Spec 依赖本模块;仪表盘前端为唯一消费方,经 §3 接口取数。
>
> **文档性质**:可直接指导开发的实现规格。所有指标口径、约束、端点、事件以此为准则;与全局约定冲突时以 [README.md](../README.md) §6「全局权威契约」为准。

---

## 全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)

1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `id UUID`(默认 `gen_random_uuid()`);所有表含 `created_at`/`updated_at`(`TIMESTAMPTZ NOT NULL DEFAULT now()`,UTC)。本模块仅新增**物化缓存表** `analytics_snapshots`(§2.5),不新增任何业务真源表。
2. **API / 错误 / 分页**:基础路径 `/api/v1`;成功包络(单对象 `{"data":{...}}`、列表 `{"data":[...],"next_cursor":<opaque|null>}`)、错误信封 `{"error":{"code","message","details"}}`(code snake_case,message 不泄漏 SQL/堆栈)、**过滤限制(嵌套深度 ≤3、条件数 ≤20、`statement_timeout` 兜底、超限 `filter_too_complex`/`query_cost_exceeded``)一律以 README §6.14 为唯一权威**,本 Spec 仅列具名错误码(§3.4)。
3. **时区与本地化**:一切时间戳**存储与传输一律 UTC RFC3339**;`users.timezone`(IANA)是**展示层偏好**,时间值解析回 UTC 存储,**桶边界以 UTC 计算**,展示层时区化(响应附 `display_timezone` 提示)。详见 README §6.18,本 Spec 不重复定义,仅在 §2.4 给出本模块的取数/分桶落地约定。
4. **成员**:`assignee_id` 引用 `members.id`(README §6.1 统一名册,`member_type ∈ {human,agent}`);存储层不设 `assignee_type` 等冗余判别列,人类/agent 判别 JOIN `members.member_type`;API 响应可携带服务端计算的 `member_type` 快照(标注「快照,真源为 members」)。
5. **多租户**:一切查询以 `workspace_id` 为隔离键并经 README §6.14 鉴权中间件(解析 → 工作区成员资格 → RBAC → 限流);引用 `projects`/`cycles`/`milestones`/`agents` 时按其 owner Spec 的可见性校验(项目仪表盘须过 `project_members` 可见性,project.md)。
6. **设计系统与图表配色**:颜色一律经语义 token 引用(status/danger/warn/success/info),文本对比度 ≥ WCAG 2.1 AA;**图表/状态色在亮/暗两套主题下各有校准取值**,暗色模式以暗色 token 集整体替换实现(README §6.12 主题契约);脉冲动画/颜色不得作为唯一状态信号。
7. **通知**:本模块**不产生任何通知**(分析结果是按需查询的只读视图,非通知事件;通知优先级矩阵见 README §6.13,本模块不在其列)。
8. **性能基准**:一切 P95/时延指标仅在 README §10 基准下构成验收标准(单工作区 issue 10 万、task_executions 100 万含 attempts 300 万),本 Spec 引用而非自定;关键聚合查询须命中源表既有索引(issue.md §2.3 / runtime.md §2.4),必要时附 `EXPLAIN (ANALYZE, BUFFERS)`。

---

## 1. 功能描述

### 1.1 模块定位

团队需要一个统一的「数据视角」回答:交付快不快(cycle time)、产能稳不稳(velocity)、需求进出是否平衡(吞吐量)、谁在超负荷(workload)、本迭代还剩多少(burndown)、AI 队友跑得可不可靠(agent 运行统计)。本模块以**只读聚合**方式从既有真源表计算这些指标,呈现于项目仪表盘与工作区仪表盘,并在 agent 详情页提供统计卡。

**核心原则**:指标真源永远是 `issues` / `task_executions` 等业务表;本模块默认**按需查询(直接聚合 SQL)**,仅对高频/重计算指标提供**可选物化缓存** `analytics_snapshots` 加速——**缓存非真源**,失效或与真源不一致时以重算为准(§2.5/§2.6)。

### 1.2 功能点与指标定义

| 功能点 | 定义 | 数据源 | 呈现位置 |
|--------|------|--------|----------|
| **Cycle Time(交付周期)** | issue 自**首次进入起始 category**(默认 `in_progress`,可配)到**进入 done** 的时长;按项目/工作区给出 P50/P90 分布 | `issues.completed_at` + `issue_activity`(首次 in_progress 留痕) | 项目/工作区仪表盘 |
| **Velocity(产能)** | 每个周期(cycles)完成的 issue 数与点数(`estimate` 求和),完成以 `state_category='done'` 且 `completed_at` 落在周期窗内计 | `issues` + `cycles` | 项目仪表盘 |
| **吞吐量(Throughput)** | 单位时间内 `created` vs `completed` 的 issue 数,按日/周/月桶 | `issues.created_at` / `completed_at` | 工作区仪表盘 |
| **Workload(负荷)** | 每成员/agent 当前 open issue 数(`assignee_id` + `state_category NOT IN done/cancelled`)+ agent 运行中/排队/需审批执行数 | `issues` + `task_executions` + `members` | 工作区仪表盘 |
| **Burndown(燃尽)** | 周期/里程碑内**剩余工作量随时间**的曲线:理想线(线性递减至 0)vs 实际线 | `issues` + `cycles`/`milestones` | 项目仪表盘 |
| **Agent 运行统计** | 按 agent 维度:执行数/成功率/平均时长/超时率/重试率/token 消耗 | `task_executions` + `execution_attempts` + `autopilot_runs`(token) | 工作区仪表盘 + agent 详情卡 |
| **项目仪表盘** | 聚合 velocity + burndown + cycle time 卡片,带时间范围选择器 | 上述端点组合 | 项目详情页签 |
| **工作区仪表盘** | 吞吐量趋势 + workload 排行 + agent 统计区 | 上述端点组合 | 工作区独立页 |

### 1.3 边界与非目标

**本 Spec 范围内**:六类指标的口径定义与按需聚合查询、可选物化缓存、项目/工作区仪表盘与 agent 统计卡的取数接口与呈现约定。

**非目标(明确不做 / 由他处承担)**:
- **不写源表**:本模块一切计算为只读聚合,**绝不**回写 `issues`/`task_executions` 等真源表(进度增量缓存 `projects.progress_cache` 归 project.md §2.4,不在本模块)。
- **不做实时流式分析**:指标以**周期聚合 + 按需查询**为准,不提供逐事件流式实时大盘(实时事件契约见 README §6.7,本模块不订阅推送指标变化)。
- **不做自定义报表生成器**:不提供拖拽建报表/自定义指标 DSL;指标集为本 Spec 冻结的六类(可扩展但需改 Spec)。
- **不做数据导出**:报表导出(CSV/JSON)归 `import-export.md`,本模块仅提供查询接口供其复用口径。
- **不做跨工作区汇总**:一切指标以单工作区为域(多租户隔离,README §6.2/§6.14)。

---

## 2. 数据模型与指标口径(核心章节)

### 2.1 设计原则与取数总则

1. **真源不可变**:所有指标的真源是业务表;本模块不创建业务实体,只读不写。
2. **时间一律 UTC**:`from`/`to`/桶边界/`completed_at`/`queued_at` 等一律 UTC RFC3339;时区仅作用于展示层与「日界展开」(§2.4)。
3. **软删除过滤**:涉及 `issues` 的统计一律 `deleted_at IS NULL`(编号墓碑不参与统计)。
4. **窗口语义统一为左闭右开** `[from, to)`,避免边界重复计数。
5. **诚实口径**:样本不足/数据缺失时**不臆造**(如无状态留痕的 issue 不计入 cycle time 分布,而在 metadata 标注 `insufficient_data` 计数;token 覆盖率显式返回)。

### 2.2 指标口径表(逐指标:定义 / 数据源 / 计算口径 / 时间窗 / 时区处理)

#### 2.2.1 Cycle Time(交付周期)

| 项 | 口径 |
|----|------|
| 定义 | issue 自**首次进入起始 category** 到**进入 done** 的时长(秒)。起始 category 默认 `in_progress`,可由参数 `from_category` 指定为任一 category。 |
| 完成时间 | `issues.completed_at`(进入 done 的时间,issue.md §2.2)。 |
| 起始时间(取数口径) | 取自 `issue_activity` 状态类别迁移留痕中**目标 category = 起始 category 的最早一条** `created_at`。留痕约定:`field='state_category'`,`new_value` 为目标 category 的 JSONB 标量,取值路径 `new_value #>> '{}'`(实现前须与 issue.md §2.2 实际留痕 schema 单点对齐;本 Spec 不修改 issue.md)。**无相应留痕的 issue 视为数据不足,不计入分布**,计入响应 `meta.insufficient_data`。 |
| 分布 | 按项目(`project_id`)或工作区(不传 `project_id`)对样本时长求 **P50/P90**(`percentile_cont`),并返回 `sample_size`。 |
| 时间窗 | 按**完成时间** `completed_at ∈ [from, to)` 落窗(统计「这段时间交付的 issue 的周期」,而非这段时间内开始的)。 |
| 时区处理 | `completed_at`/留痕 `created_at` 均为 UTC,时长为两者之差,**与时区无关**;仅展示层把锚点时间时区化(§2.4)。 |
| 防负时长 | `first_started_at < completed_at` 作为样本有效性条件(留痕乱序/补录兜底);不满足的样本计入 `insufficient_data`。 |

```sql
-- cycle time:P50/P90(按完成时间落窗,起始时间取首次进入起始 category 的留痕)
WITH first_start AS (
  SELECT a.issue_id, MIN(a.created_at) AS started_at
  FROM issue_activity a
  WHERE a.workspace_id = $ws
    AND a.field = 'state_category'
    AND (a.new_value #>> '{}') = $from_category      -- 默认 'in_progress'
  GROUP BY a.issue_id
)
SELECT
  percentile_cont(0.5) WITHIN GROUP
    (ORDER BY EXTRACT(EPOCH FROM (i.completed_at - f.started_at))) AS p50_seconds,
  percentile_cont(0.9) WITHIN GROUP
    (ORDER BY EXTRACT(EPOCH FROM (i.completed_at - f.started_at))) AS p90_seconds,
  COUNT(*)                                                          AS sample_size
FROM issues i
JOIN first_start f ON f.issue_id = i.id
WHERE i.workspace_id = $ws
  AND i.deleted_at IS NULL
  AND ($project_id IS NULL OR i.project_id = $project_id)
  AND i.state_category = 'done'
  AND i.completed_at IS NOT NULL
  AND i.completed_at >= $from AND i.completed_at < $to
  AND f.started_at < i.completed_at;
-- insufficient_data = 同窗内 done 但无 first_start 命中(或 started_at>=completed_at)的 issue 数,单独 COUNT。
```

#### 2.2.2 Velocity(产能)

| 项 | 口径 |
|----|------|
| 定义 | 每个周期(cycles)**完成**的 issue 数与点数。 |
| 完成判定 | `issues.state_category = 'done'` 且 `issues.completed_at` 落在该周期窗内。 |
| 周期窗 | `cycles.starts_at`/`ends_at`(均为 `DATE`,project.md §2.2)。窗为 DATE,落窗判定将 DATE 边界按 `display_timezone`(默认工作区时区)展开为 UTC 区间 `[starts_at 00:00, (ends_at+1) 00:00)`(含末日全天)。 |
| 点数 | `SUM(issues.estimate)`(`estimate_unit` 为 `points`/`hours`,响应分别标注单位;NULL estimate 计 0)。 |
| 时间窗 | 查询参数 `cycle_ids`(显式周期列表)或 `from`/`to`(筛 `starts_at` 与之相交的周期)。 |
| 归属(**当前归属口径,R3 写死**) | issue 与 cycle 经 `issues.cycle_id` 关联(查询时刻的当前归属);**未挂 cycle 的 done issue 不计入任何周期 velocity**(诚实口径,可在工作区仪表盘单列「未规划周期」)。**issue 在周期间移动会改变移出/移入双方的历史 velocity**(与 burndown §2.2.5 同口径:按当前归属重算,不还原历史归属);响应 meta 标注 `"scope_caliber": "current_attribution"`。 |

```sql
-- velocity:逐周期完成数与点数(cycle_ids 显式给定)
SELECT c.id AS cycle_id, c.name, c.starts_at, c.ends_at, c.state,
       COUNT(i.id)                AS completed_issues,
       COALESCE(SUM(i.estimate),0) AS completed_points
FROM cycles c
LEFT JOIN issues i
  ON  i.cycle_id = c.id
  AND i.workspace_id = c.workspace_id
  AND i.deleted_at IS NULL
  AND i.state_category = 'done'
  AND i.completed_at >= (c.starts_at       AT TIME ZONE $tz)   -- DATE→UTC 下界
  AND i.completed_at <  ((c.ends_at + 1)   AT TIME ZONE $tz)   -- DATE→UTC 上界(含末日)
WHERE c.workspace_id = $ws AND c.id = ANY($cycle_ids)
GROUP BY c.id, c.name, c.starts_at, c.ends_at, c.state
ORDER BY c.starts_at;
```

#### 2.2.3 吞吐量(Throughput)

| 项 | 口径 |
|----|------|
| 定义 | 单位时间内 `created` 与 `completed` 的 issue 数(两条序列)。 |
| 桶粒度 | `granularity ∈ {day, week, month}`,对应 PG `date_trunc`。 |
| 桶边界(**按 `calendar_timezone` 本地日历分桶,R3 写死**) | 桶边界**按 `calendar_timezone` 的本地日历对齐**(§2.4):day 桶 = 该时区当地 `[00:00, 次日00:00)`、week 桶 = 当地周一 00:00 起、month 桶 = 当地月初 00:00 起,经 `date_trunc($g, ts AT TIME ZONE $cal_tz)` 计算;每个桶以**本地日历周期标签**(如 `2026-07-25`)+ 其对应的 **UTC 瞬间区间**(`window_start`/`window_end`,= 本地边界 `AT TIME ZONE $cal_tz` 转 UTC)一并返回——**日期标签与统计边界恒一致,本地自然日不跨桶**。R3 前的「UTC 分桶 + 展示层换时区标签」做法废弃:那会让 UTC+8 用户的"7 月 25 日"桶实际覆盖当地 7/25 08:00–7/26 08:00,时区切换后标签与边界错位。`calendar_timezone` 缺省取请求者 `users.timezone` → 工作区 `timezone` → `UTC`(§2.4);显式 `calendar_timezone='UTC'` 即 UTC 分桶(供跨时区统一报表);不同 `calendar_timezone` 的分桶结果维度不同(`dimensions.calendar_timezone` 入 `dim_hash`,缓存不跨时区共享,§2.5)。 |
| created 序列 | `issues.created_at ∈ [from, to)` 且 `deleted_at IS NULL`。 |
| completed 序列 | `state_category='done'` 且 `completed_at ∈ [from, to)`。 |
| 净流量 | 响应附 `net = created - completed`(派生,前端可绘积压趋势)。 |

```sql
-- throughput:created vs completed 按 calendar_timezone 本地日历分桶(R3)
SELECT bucket_local,
       (bucket_local AT TIME ZONE $cal_tz)            AS window_start_utc,  -- 本地桶起点的 UTC 瞬间
       COUNT(*) FILTER (WHERE kind='created')   AS created,
       COUNT(*) FILTER (WHERE kind='completed') AS completed
FROM (
  SELECT date_trunc($granularity, created_at AT TIME ZONE $cal_tz) AS bucket_local, 'created' AS kind
    FROM issues
   WHERE workspace_id=$ws AND deleted_at IS NULL
     AND created_at >= $from AND created_at < $to
  UNION ALL
  SELECT date_trunc($granularity, completed_at AT TIME ZONE $cal_tz) AS bucket_local, 'completed' AS kind
    FROM issues
   WHERE workspace_id=$ws AND deleted_at IS NULL
     AND state_category='done' AND completed_at IS NOT NULL
     AND completed_at >= $from AND completed_at < $to
) t
GROUP BY bucket_local
ORDER BY bucket_local;
-- 响应每桶:{label: <bucket_local 本地日历标签>, window_start/window_end: <UTC 瞬间>, created, completed}
-- 跨 DST 的时区/日期由 AT TIME ZONE 按当日实际偏移处理,桶宽可能为 23h/25h(day 粒度),标签与边界仍一致(§5.2)。
```

#### 2.2.4 Workload(负荷)

| 项 | 口径 |
|----|------|
| 定义 | 每成员/agent 的**当前 open issue 数** + agent 的**运行中/排队/需审批执行数**。 |
| open issue | `assignee_id IS NOT NULL` 且 `state_category NOT IN ('done','cancelled')` 且 `deleted_at IS NULL`,按 `assignee_id`(→ `members.id`)分组计数。 |
| agent 执行(呼应 README §6.12「运行中 N / 排队 M / 需审批 K」) | 按 `task_executions.agent_id` 聚合在途执行:**运行中** = `status IN ('claimed','running','cancelling')`;**排队** = `status='queued'`;**需审批** = `status='awaiting_approval'`。**R4(HIGH-6):workload-B 与 agent stats / workspace dashboard 共用统一 execution 可见性 scope(§2.3.1)**——聚合 `task_executions` 前先过 `analytics_exec_visible_to(execution, 请求者)` 谓词:关联 issue 的执行继承项目可见性(普通成员看不到不可见 private project 的执行数,堵执行计数侧信道),private agent 的执行先过 agent 可见性(仅 owner/admin)。 |
| 维度统一 | open issue 以 `assignee_id`(members)为键;agent 执行以 `task_executions.agent_id`(agents)为键,经 `agents.id → members.agent_id` JOIN 统一到成员维度。响应每行携带服务端计算的 `member_type` 快照与 `display_name`(README §6.1);人类行 executions 字段为 null。 |
| 时间窗 | **当前快照**,无时间窗(反映此刻状态);可选 `project_id` 收窄到项目内 issue。 |
| 排行 | 服务端按 `open_issues DESC, running DESC` 排序返回(列表分页见 §6.14 整体游标)。 |

```sql
-- workload-A:每成员 open issue 数
SELECT i.assignee_id AS member_id, COUNT(*) AS open_issues
FROM issues i
WHERE i.workspace_id=$ws AND i.deleted_at IS NULL
  AND i.assignee_id IS NOT NULL
  AND i.state_category NOT IN ('done','cancelled')
  AND ($project_id IS NULL OR i.project_id = $project_id)
GROUP BY i.assignee_id;

-- workload-B:每 agent 在途执行(运行中/排队/需审批,呼应 §6.12)
SELECT e.agent_id,
  COUNT(*) FILTER (WHERE e.status IN ('claimed','running','cancelling')) AS running,
  COUNT(*) FILTER (WHERE e.status = 'queued')                            AS queued,
  COUNT(*) FILTER (WHERE e.status = 'awaiting_approval')                 AS awaiting_approval
FROM task_executions e
WHERE e.workspace_id=$ws AND e.agent_id IS NOT NULL
  AND e.status IN ('queued','claimed','running','cancelling','awaiting_approval')
GROUP BY e.agent_id;
-- 服务层:workload-B 经 agents→members 并入 workload-A 的成员维度;member_type 经 JOIN members 取得(快照)。
```

#### 2.2.5 Burndown(燃尽)

| 项 | 口径 |
|----|------|
| 定义 | 周期或里程碑内**剩余工作量随时间**的曲线,含**理想线**(线性递减至 0)与**实际线**。 |
| 作用域(二选一) | `cycle_id`(窗 = `cycles.starts_at..ends_at`)或 `milestone_id`(窗 = 里程碑创建/起始日至 `milestones.target_date`;起始日取该项目 `min(start_date)` 或里程碑 `created_at`,实现按 project.md 校准)。两者**恰好一个**,同传或皆缺为 `400`。 |
| 工作量度量 | `metric ∈ {count, points}`:count = issue 数;points = `SUM(estimate)`(NULL 计 0)。 |
| scope 集合(**当前归属口径,R3 写死**) | 周期:`issues.cycle_id = $cycle_id`;里程碑:`issues.milestone_id = $milestone_id`(`deleted_at IS NULL`)。**scope = 查询时刻归属该 cycle/milestone 的 issue 集合**——R3 明确降级为「当前归属」口径(不再声称「曾进入 scope 全部计入」:本 SQL 只读当前 `cycle_id`/`milestone_id`,无法还原历史归属,继续声称"曾进入"会让移入/移出 issue 静默改写历史曲线、口径不可复核)。据此口径:**issue 移入/移出 cycle/milestone 会改变该 scope 的历史燃尽曲线**(总量与已完成量随当前集合重算),这是口径的明确语义而非缺陷;响应 meta 标注 `"scope_caliber": "current_attribution"` 供 UI 提示「曲线按当前归属计算」。未来如需「曾进入 scope」历史口径,须消费 `issue_activity` 的规范化 scope 变更事实(cycle/milestone 字段变更留痕,issue.md owns)重建历史归属,立项后再扩展,不在本期声称。 |
| 剩余量(实际线) | 截至日期 d 的剩余 = scope 总量 − 在 d 日界(按 `display_timezone` 展开为 UTC)之前(含)完成的工作量,即 `completed_at < (d+1) AT TIME ZONE $tz` 视为「d 日及以前已完成」。仅输出 `d < today` 的过去日(未来日无实际值)。 |
| 理想线 | 从窗起点总量 `total` 线性递减到窗终点 `0`:第 d 天理想剩余 = `total × (剩余日历天数 / 总日历天数)`。 |
| 时区处理 | DATE 日界按 `display_timezone` 展开为 UTC 比较;曲线点以 UTC 日锚点返回,展示层时区化。跨 DST 时日界仍按该时区当日 00:00 的 UTC 瞬间计算,不错位(§5.2 验收)。 |

```sql
-- burndown 实际线(以周期为例,逐日剩余点数;milestone 同构,换作用域与窗)
WITH RECURSIVE days(d) AS (
  SELECT starts_at FROM cycles WHERE id=$cycle_id AND workspace_id=$ws
  UNION ALL
  SELECT days.d + 1 FROM days
   JOIN cycles c ON c.id=$cycle_id
   WHERE days.d < c.ends_at
),
scope AS (
  SELECT COALESCE(estimate,0) AS pts, completed_at
    FROM issues
   WHERE cycle_id=$cycle_id AND workspace_id=$ws AND deleted_at IS NULL
),
total AS (SELECT COALESCE(SUM(pts),0) AS v FROM scope)
SELECT days.d AS date,
       (SELECT v FROM total)
         - COALESCE(SUM(scope.pts) FILTER (
             WHERE scope.completed_at IS NOT NULL
               AND scope.completed_at < (days.d + 1) AT TIME ZONE $tz), 0) AS remaining
FROM days LEFT JOIN scope ON TRUE
GROUP BY days.d
ORDER BY days.d;
-- 理想线由服务端按 total 与窗天数线性生成,与剩余实际线同响应返回。
```

### 2.3 Agent 运行统计口径

| 指标 | 口径 | 数据源 |
|------|------|--------|
| 执行数 `executions` | 时间窗内 `COUNT(task_executions)`,按 `queued_at ∈ [from, to)` 落窗 | `task_executions` |
| 成功数 `succeeded` | `status='completed'` | `task_executions` |
| 成功率 `success_rate` | `completed / (completed + failed + timeout)`;**cancelled(用户/系统取消)不计入分母**——取消非执行成败(诚实口径;响应附 `cancelled_count` 透明披露) | `task_executions` |
| 超时率 `timeout_rate` | `timeout / (completed + failed + timeout)`(`timeout` 为失败类独立终态,runtime.md §4.7) | `task_executions` |
| 平均时长 `avg_duration_seconds` | `AVG(EXTRACT(EPOCH FROM (finished_at - queued_at)))`,仅终态(`completed/failed/timeout`)且 `finished_at IS NOT NULL`;此为**逻辑执行端到端时长**(含排队)。可另提供纯执行时长 `AVG(attempt.finished_at - attempt.started_at)`(取成功 attempt) | `task_executions`(端到端)/`execution_attempts`(纯执行) |
| 重试率 `retry_rate` | 含重试的执行占比;`retry_count = COUNT(execution_attempts) - 1`(runtime.md §2.2,不存冗余列),`retry_rate = COUNT(retry_count>0 的执行) / COUNT(执行)` | `task_executions` + `execution_attempts` |
| token 消耗 `total_tokens` 等 | **仅 autopilot 触发的执行有 token 数据**:`SUM(autopilot_runs.prompt_tokens/completion_tokens/total_tokens)`,经 `autopilot_runs.execution_id → task_executions.id` 关联。**`task_executions` 本身无 token 字段**;非 autopilot 触发(`assign`/`mention`/`manual`/`chat`/`integration` 直派)的执行 token **未知**,不估算。响应必返回 `token_coverage = runs_with_token_data / executions`,口径诚实(coverage<1 时 UI 标注「token 仅覆盖 autopilot 运行」) | `autopilot_runs`(autopilot.md §2.3) |

```sql
-- agent 统计主查询(执行数/成功率/超时率/平均端到端时长)
SELECT
  e.agent_id,
  COUNT(*)                                                              AS executions,
  COUNT(*) FILTER (WHERE e.status='completed')                          AS succeeded,
  COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout'))  AS terminal,
  COUNT(*) FILTER (WHERE e.status='cancelled')                          AS cancelled_count,
  ROUND(COUNT(*) FILTER (WHERE e.status='completed') * 1.0
        / NULLIF(COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout')),0), 4) AS success_rate,
  ROUND(COUNT(*) FILTER (WHERE e.status='timeout') * 1.0
        / NULLIF(COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout')),0), 4) AS timeout_rate,
  AVG(EXTRACT(EPOCH FROM (e.finished_at - e.queued_at)))
        FILTER (WHERE e.status IN ('completed','failed','timeout')
                AND e.finished_at IS NOT NULL)                          AS avg_duration_seconds
FROM task_executions e
WHERE e.workspace_id=$ws AND e.agent_id=$agent_id
  AND e.queued_at >= $from AND e.queued_at < $to
GROUP BY e.agent_id;

-- 重试率(retry_count = COUNT(attempts)-1,派生自 execution_attempts)
SELECT ROUND(COUNT(*) FILTER (WHERE n > 1) * 1.0 / NULLIF(COUNT(*),0), 4) AS retry_rate
FROM (
  SELECT e.id, COUNT(att.id) AS n
  FROM task_executions e
  LEFT JOIN execution_attempts att
    ON att.execution_id = e.id AND att.workspace_id = e.workspace_id
  WHERE e.workspace_id=$ws AND e.agent_id=$agent_id
    AND e.queued_at >= $from AND e.queued_at < $to
  GROUP BY e.id
) r;

-- token 消耗(仅 autopilot 触发执行有数据;coverage 诚实披露)
SELECT SUM(r.prompt_tokens)     AS prompt_tokens,
       SUM(r.completion_tokens) AS completion_tokens,
       SUM(r.total_tokens)      AS total_tokens,
       COUNT(r.id)              AS runs_with_token_data
FROM autopilot_runs r
JOIN task_executions e
  ON e.id = r.execution_id AND e.workspace_id = r.workspace_id
WHERE e.workspace_id=$ws AND e.agent_id=$agent_id
  AND r.started_at >= $from AND r.started_at < $to;
-- token_coverage = runs_with_token_data / executions(主查询)。
```

> **口径诚实性声明**:执行时长/成功率/超时率/重试率来自 `task_executions`/`execution_attempts`,**覆盖全部执行**;token 消耗**仅覆盖 autopilot 触发的执行**(`autopilot_runs` 是唯一 token 真源),二者口径不同源,响应分别标注覆盖范围,不得把 token 数据外推到无 token 的执行。

### 2.3.1 execution 指标统一可见性 scope(R4 写死,HIGH-6)

**问题**:R3 的私有项目可见性过滤只覆盖 issue 型工作区聚合与 cycle/milestone;workload-B 与 `/analytics/agents/stats` 仍按 `workspace_id + agent_id` 聚合**全部** `task_executions`(执行数/失败率/时长/token),而 `/dashboards/workspace` 含 agent 统计区——普通成员可经执行计数/成本**侧信道推断不可见 private project 的活动**(如某 private 项目下 agent 执行量突增)。R4 为**一切 execution 指标**定义统一可见性 scope,workload-B / agent stats / workspace dashboard agent 统计区**共用同一谓词**,不得各端点各写一套。

**谓词 `analytics_exec_visible_to(execution e, 请求者 m)`(两层串联,validation 脚本同名函数为可执行参照,T33)**:

```text
visible(e, m) :=
  -- ① agent 可见性先行(private agent 的统计不泄露给非 owner/非 admin)
  (e.agent.visibility = 'workspace'
   OR (e.agent.visibility = 'private'
       AND (e.agent.owner_user_id = m.user_id OR m.role IN ('owner','admin'))))
  AND
  -- ② 关联 issue 的执行继承项目可见性;无 issue 的执行归属 agent、无项目侧信道
  (e.issue_id IS NULL                                        -- manual/chat/integration 等无 issue 执行
   OR e.issue.project_id IS NULL                             -- 收件箱 issue(工作区级可见)
   OR e.issue.project.visibility = 'public'
   OR m.role IN ('owner','admin')                            -- admin/owner 见全工作区(含 private 项目)
   OR m ∈ project_members(e.issue.project)
   OR m ∈ member_project_access(e.issue.project))
```

| 规则 | 内容 |
|------|------|
| 关联 issue 的执行 | **继承关联 issue 当前所属项目的可见性**(「当前归属」口径,与 §2.2.2/§2.2.5 一致):private 项目的执行仅项目成员与 admin/owner 可见,非成员的任何 execution 聚合(执行数/成功率/时长/token)均剔除这些执行。issue 在项目间移动后,执行可见性随**当前**归属变化。 |
| 无 issue 的执行(`manual`/`chat`/`integration` 直派等) | **归属 agent 本身,经 ① agent 可见性即可见**——这类执行不关联任何项目,不携带项目侧信道;workspace 可见 agent 的无 issue 执行对全体工作区成员可见,private agent 的仅 owner/admin 可见。 |
| private agent | **先过 agent 可见性**(`agents.visibility='private'` 仅 owner 与 admin/owner 角色可见,agent.md §3.5):其**一切**执行(无论是否关联 issue)对非 owner/非 admin 不可见,统计不呈现。 |
| 端点覆盖 | `/analytics/agents/stats`(单/多 agent)、`/analytics/workload` 的 workload-B 执行部分、`/dashboards/workspace` 的 agent 统计区一律先按本谓词过滤 `task_executions` 再聚合;`/analytics/agents/stats?agent_id=` 对 private agent 额外校验请求者可见性,不可见 → `403 agent_not_visible`(不泄露统计存在性)。 |
| 缓存键协同(R4) | execution 类指标(`agent_stats`、workload 执行部分)的 `analytics_snapshots.scope_key` 在 admin/owner 全量时为 `ws_admin`,普通成员为 **`exec:p<sha256(可见项目 id 排序)>:a<sha256(可见 agent id 排序)>`**(可见项目集同 §3.1 口径;可见 agent 集 = workspace 可见 agents ∪ 请求者拥有的 private agents)。查询只命中 scope_key 相等的快照,`ws_admin` 绝不返回给非 admin;可见性变化(项目转 private/agent 转 private/成员变更)后旧键不再命中,自然失效。 |

### 2.4 时间窗与时区统一约定(落地 README §6.18)

| 约定 | 内容 |
|------|------|
| 参数 | `from`/`to` 一律 **RFC3339 UTC**;缺省窗由端点定义(如仪表盘默认近 30 天 `[now()-30d, now())`)。窗语义左闭右开 `[from, to)`。 |
| 桶边界(**`calendar_timezone` 分桶语义,R3**) | throughput/仪表盘等分桶**按 `calendar_timezone` 的本地日历对齐**:day = 当地自然日 `[00:00, 次日00:00)`、week = 当地周一起、month = 当地月初起(`date_trunc($g, ts AT TIME ZONE $cal_tz)`,§2.2.3)。每桶返回**本地日历标签 + 对应 UTC 瞬间窗**(`window_start`/`window_end`),标签与统计边界恒一致、本地自然日不跨桶;`calendar_timezone` 缺省 = 请求 `?tz=`/`calendar_timezone=` → 请求者 `users.timezone` → 工作区 `timezone` → `UTC`,显式 `UTC` 即 UTC 分桶;**时区切换(用户改 `users.timezone`)后日期标签与桶边界同步变化、不出现错位**(不再使用"UTC 分桶 + 展示层换标签")。`calendar_timezone` 纳入聚合 `dimensions`(入 `dim_hash`),不同时区的分桶缓存分行、不共享(§2.5)。 |
| 日界展开 | cycle/milestone 的 `DATE` 边界、burndown 的「日」按 `display_timezone` 展开为 UTC 瞬间(`d AT TIME ZONE $tz`)参与比较。 |
| `display_timezone` | 取请求 `?tz=`(IANA)→ 请求者 `users.timezone` → 工作区默认 → `UTC`;**仅用于响应中时间锚点/桶标签的展示层时区化与日界展开**,不改存储与桶真源。每个含时间序列的响应在 `meta.display_timezone` 回显所用时区。 |
| 跨 DST | 日界按该 IANA 时区**当日 00:00 的 UTC 瞬间**计算(`AT TIME ZONE` 自动处理偏移切换),序列点不因 DST 错位或重复;验收见 §5.2。 |
| 相对时间 | 「3 分钟前」等相对时间由前端按 `display_timezone` + locale 渲染(README §6.18 本地化渲染),服务端只给 UTC 锚点。 |

### 2.5 聚合策略与物化缓存 `analytics_snapshots`

**默认按需查询**:六类指标均可以上聚合 SQL 直接计算,命中源表既有索引——`idx_issues_project_status`/`idx_issues_assignee`/`idx_issues_cycle`/`idx_issues_milestone`/`idx_issue_activity_issue`(issue.md §2.3)、`idx_executions_agent_time`/`idx_executions_issue_time`/`idx_attempts_execution`(runtime.md §2.4)、`idx_run_autopilot_started`(autopilot.md)。

**可选物化缓存**:对高频/重计算指标(如工作区仪表盘吞吐量、大项目 burndown)可写入 `analytics_snapshots`,后台 worker 周期刷新 + 查询时过期重算。**缓存非真源**:任何缓存值与真源不一致时以重算为准(§2.6)。**可见性版本纳入缓存键(R3)**:快照携带 `scope_key`——计算该聚合所用的**可见性集合指纹**,查询命中必须 scope_key 与请求者权限匹配,**禁止跨权限复用缓存**(private project 聚合不得经共享缓存泄露给非成员,§3.1/§4.3)。

```sql
CREATE TABLE analytics_snapshots (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  metric_key   TEXT NOT NULL,                 -- 'cycle_time'/'velocity'/'throughput'/'workload'/'burndown'/'agent_stats'
  scope_key    TEXT NOT NULL DEFAULT 'ws_admin',  -- R3:可见性集合指纹(缓存键一部分,禁跨权限缓存)
  dimensions   JSONB NOT NULL DEFAULT '{}',   -- {project_id?, cycle_id?, milestone_id?, agent_id?, granularity?, from_category?, tz?, calendar_timezone?}
  dim_hash     TEXT GENERATED ALWAYS AS (md5(dimensions::text)) STORED,  -- 维度指纹,供唯一键/查找(避免 JSONB 直接入唯一索引)
  window_start TIMESTAMPTZ NOT NULL,          -- UTC
  window_end   TIMESTAMPTZ NOT NULL,          -- UTC
  value        JSONB NOT NULL,                -- 聚合结果(指标值 + 必要 meta,如 sample_size/token_coverage/scope_caliber)
  computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- 同一 (工作区, 指标, 可见性集合, 维度, 窗) 仅一份快照(覆盖式刷新)——scope_key 入键即"跨权限不共享"
  UNIQUE (workspace_id, metric_key, scope_key, dim_hash, window_start, window_end)
);

CREATE INDEX idx_snapshots_lookup
  ON analytics_snapshots (workspace_id, metric_key, scope_key, dim_hash, window_start, window_end);
CREATE INDEX idx_snapshots_stale
  ON analytics_snapshots (computed_at);        -- 供 worker 找过期快照重算
```

> **多租户**:`analytics_snapshots.workspace_id` 为隔离键,查询/刷新必带 `workspace_id`(README §6.2);不存任何跨工作区聚合。
>
> **`scope_key` 取值(R3 写死;R4 扩充)**:① `ws_admin` —— 工作区全量聚合(仅 admin/owner 可查的端点使用,§3.1/§4.3,聚合 SQL 不过滤项目可见性,因为请求者本身即有权看全工作区);② `projects:<sha256(sorted project_id 列表)>` —— 按请求者可见项目集合聚合的 **issue 型指标**(请求者可见项目 = `visibility='public'` 的项目 ∪ 其 `project_members`/`member_project_access` 行覆盖的 private 项目;集合 id 排序后 sha256);③ `project:<project_id>` —— 单项目聚合;④ **R4:`exec:p<sha256(可见项目集)>:a<sha256(可见 agent 集)>` —— execution 类指标(`agent_stats`、workload 执行部分)的统一可见性 scope**:可见项目集同 ②;可见 agent 集 = `visibility='workspace'` 的 agents ∪ 请求者 `owner_user_id` 名下的 private agents(集合 id 排序后 sha256,§2.3.1)。**查询时**:服务端先算请求者 scope_key,**只命中 scope_key 相等的快照行**;不命中则按该 scope 重算并写入。**绝不**把 `ws_admin` 快照返回给非 admin(即"全量聚合"与"可见集合聚合"物理分行、键不共享),杜绝经缓存侧信道泄露 private project 统计与 private agent 统计。

### 2.6 缓存一致性与失效

| 规则 | 内容 |
|------|------|
| 非真源 | `analytics_snapshots` 仅为加速副本;真源永远是 `issues`/`task_executions` 等。 |
| 命中条件 | 查询命中需 `metric_key`/`dim_hash`/窗匹配**且** **`scope_key` 与请求者可见性集合一致(R3,禁跨权限复用)** **且** `computed_at` 新于 TTL(默认 15 分钟,可配)。 |
| 过期重算 | `computed_at` 老于 TTL → **stale-while-revalidate**:返回旧值并触发后台重算;或按端点配置同步重算后返回(仪表盘首屏可同步)。 |
| 周期刷新 | 后台 worker 按 TTL 周期扫描 `idx_snapshots_stale` 刷新热点快照;worker 失败不阻塞查询(回退按需查询)。 |
| 强制重算 | 端点支持 `?refresh=true` 跳过缓存同步重算(管理/排障用,受 §6.14 限流)。 |
| 不缓存项 | `workload`(当前快照,实时性要求高)默认**不缓存**,直接聚合;其余指标可选缓存。 |
| 一致校验 | 集成测试验证:写入源表后缓存失效/重算结果与直接聚合一致(§5.5)。 |

---

## 3. 接口设计

### 3.1 端点清单

基础路径 `/api/v1`;鉴权 `Authorization: Bearer <token>`,经 README §6.14 中间件链(解析 → 工作区成员资格 → RBAC → 限流)。包络/分页/错误信封/过滤限制以 README §6.14 为唯一权威。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/analytics/cycle-time` | cycle time P50/P90(`project_id?`、`from`、`to`、`from_category?`、`tz?`) |
| GET | `/analytics/velocity` | 逐周期完成数/点数(`project_id?`、`cycle_ids?` 或 `from`/`to`、`tz?`) |
| GET | `/analytics/throughput` | created vs completed 桶序列(`project_id?`、`from`、`to`、`granularity=day\|week\|month`、`tz?`) |
| GET | `/analytics/workload` | 成员/agent 负荷排行(`project_id?`、`member_type=human\|agent?`、游标分页) |
| GET | `/analytics/burndown` | 燃尽曲线(`cycle_id?` \| `milestone_id?` 恰好一个、`metric=count\|points`、`tz?`) |
| GET | `/analytics/agents/stats` | 单/多 agent 运行统计(`agent_id?`、`from`、`to`);**R4:聚合前按统一 execution 可见性 scope 过滤(§2.3.1)**——关联不可见 private project 的执行被剔除,private agent 先过 agent 可见性;`agent_id` 指向 private agent 且请求者非 owner/非 admin → `403 agent_not_visible` |
| GET | `/dashboards/project/{project_id}` | 项目仪表盘聚合(velocity + burndown + cycle time,`from`/`to`、`cycle_id?`) |
| GET | `/dashboards/workspace` | 工作区仪表盘聚合(throughput + workload + agent 统计,`from`/`to`、`granularity?`);**全体工作区成员可用,聚合按请求者项目可见性过滤**(private 项目不可见者其数据被剔除,MES-4 HIGH-2/R3);**R4:workload 执行部分与 agent 统计区同样按统一 execution 可见性 scope 过滤(§2.3.1),普通成员无法经执行计数/成本推断不可见 private project 活动**;admin/owner 见全工作区聚合 |

> 项目级端点(`/analytics/*?project_id=`、`/dashboards/project/{id}`)须过 `project_members` 可见性校验(project.md);无可见性返回 `403 project_not_visible`。
>
> **私有项目可见性过滤(MES-4 HIGH-2,全端点覆盖;R3 协同保留)**:
> - **工作区级聚合**(不传 `project_id` 的 cycle time / throughput / workload / `/dashboards/workspace`):**按请求者项目可见性过滤,剔除其不可见的私有项目数据**(即 `WHERE project_id IS NULL OR project_id IN (请求者可见项目集)`),非私有项目成员的普通工作区成员看不到私有项目的计数/曲线/成员负荷;
> - **按 `cycle_id`/`milestone_id` 的查询**(velocity / burndown):先解析归属项目并过 `project_members` 可见性校验,不满足 → `403 project_not_visible`;
> - 工作区仪表盘受众为全体工作区成员,但**聚合数据按上述可见性过滤**(产品口径:数据过滤而非端点限 admin);**admin/owner 可见全工作区聚合(含 private 项目)**。
>
> **R3 可见性缓存边界(与上述过滤协同,硬约束;R4 扩至 execution 指标)**:① **缓存不跨权限共享**:`analytics_snapshots.scope_key` 纳入缓存键(§2.5)——普通成员的 issue 型聚合快照 scope_key 为 `projects:<sha256(请求者可见项目 id 排序)>`,admin/owner 全量聚合为 `ws_admin`;查询**只命中请求者 scope_key 相等的快照**,`ws_admin` 快照绝不返回给非 admin(过滤口径变化即新键,无跨权限泄露窗口,集成测试 T33)。② 单项目聚合 scope_key 为 `project:<id>`;显式多项目(`project_ids`)聚合请求者对其中任一项目不可见 → 整体 `403`(不部分返回,避免集合推断泄露)。③ 可见项目集合变化(项目转 private/成员移除)后旧 scope_key 快照不再命中,自然失效。④ **R4(HIGH-6):execution 类指标(`agent_stats`、workload 执行部分)scope_key 纳入同一可见性 scope——普通成员为 `exec:p<可见项目集 hash>:a<可见 agent 集 hash>`**(可见 agent 集 = workspace 可见 agents ∪ 请求者拥有的 private agents,§2.3.1),admin/owner 为 `ws_admin`;聚合一律先过 `analytics_exec_visible_to` 谓词再落快照,workload-B / agent stats / workspace dashboard agent 统计区共用,跨权限绝不共享。

### 3.2 公共查询参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `from` / `to` | RFC3339 UTC | 时间窗,左闭右开 `[from, to)`;缺省窗按端点(默认近 30 天) |
| `granularity` | `day`\|`week`\|`month` | 桶粒度(throughput/仪表盘),默认 `day` |
| `tz` | IANA 字符串 | `display_timezone`,缺省取请求者 `users.timezone` → 工作区默认 → `UTC`(§2.4) |
| `refresh` | `bool` | `true` 跳过缓存同步重算(§2.6) |
| `project_id` / `cycle_id` / `milestone_id` / `agent_id` | UUID | 维度过滤(burndown 的 `cycle_id`/`milestone_id` 恰好一个) |

### 3.3 请求/响应示例(单对象包络 `{"data":{...}}`)

```http
GET /api/v1/analytics/cycle-time?project_id=<uuid>&from=2026-06-01T00:00:00Z&to=2026-07-01T00:00:00Z&tz=Asia/Shanghai
```
```json
{
  "data": {
    "project_id": "662c73d5-…",
    "from_category": "in_progress",
    "p50_seconds": 172800,
    "p90_seconds": 518400,
    "sample_size": 42,
    "meta": { "insufficient_data": 3, "display_timezone": "Asia/Shanghai" }
  }
}
```

```http
GET /api/v1/analytics/throughput?from=2026-06-01T00:00:00Z&to=2026-06-08T00:00:00Z&granularity=day
```
```json
{
  "data": {
    "granularity": "day",
    "series": [
      { "bucket": "2026-06-01T00:00:00Z", "created": 12, "completed": 8 },
      { "bucket": "2026-06-02T00:00:00Z", "created": 5,  "completed": 9 }
    ],
    "meta": { "display_timezone": "UTC", "net_window": -4 }
  }
}
```

```http
GET /api/v1/analytics/agents/stats?agent_id=<uuid>&from=2026-06-01T00:00:00Z&to=2026-07-01T00:00:00Z
```
```json
{
  "data": {
    "agent_id": "2616450a-…",
    "member_type": "agent",
    "display_name": "Mesh 程序员",
    "executions": 120, "succeeded": 100, "terminal": 110,
    "cancelled_count": 10,
    "success_rate": 0.9091, "timeout_rate": 0.0455,
    "avg_duration_seconds": 845.2, "retry_rate": 0.15,
    "tokens": { "total_tokens": 1820000, "prompt_tokens": 1200000,
                "completion_tokens": 620000, "token_coverage": 0.40 },
    "meta": { "token_note": "token 仅覆盖 autopilot 触发的执行" }
  }
}
```

> velocity / workload / burndown / dashboards 端点同用单对象包络 `{"data":{...}}`;workload 为列表式(成员行数组 + `next_cursor`,README §6.14 整体游标)。所有含时间序列的响应在 `meta.display_timezone` 回显时区。

### 3.4 错误码(具名;包络/HTTP 语义见 README §6.14)

| HTTP | code | 触发 |
|------|------|------|
| 400 | `validation_error` | 参数非法(如 `granularity` 非 day/week/month、`from_category` 非合法 category) |
| 400 | `invalid_time_range` | `from >= to` 或时间格式非 RFC3339 UTC |
| 400 | `burndown_scope_required` | burndown 未给 `cycle_id` 或 `milestone_id` |
| 400 | `burndown_scope_conflict` | burndown 同时给了 `cycle_id` 与 `milestone_id`(须恰好一个) |
| 400 | `invalid_timezone` | `tz` 非合法 IANA 时区 |
| 400 | `filter_too_complex` | 维度组合超限(README §6.14 过滤限制) |
| 401 | `unauthorized` | 未鉴权/令牌失效(README §6.14) |
| 403 | `forbidden` | 非工作区成员 |
| 403 | `project_not_visible` | 项目级端点但请求者无该项目可见性(project.md `project_members`) |
| 403 | `agent_not_visible` | `agent_id` 指向 private agent 且请求者非其 owner/非 admin(R4:private agent 统计不可见,不泄露统计存在性,§2.3.1) |
| 404 | `not_found` | 指定 project/cycle/milestone/agent 不存在或不属于该工作区 |
| 422 | `query_cost_exceeded` | 聚合估算成本超限(README §6.14),建议收窄窗/维度 |
| 429 | `rate_limited` | 触发限流(带 `Retry-After`) |

### 3.5 分页、鉴权与时区响应

- **分页**:仅 workload 排行(列表)用游标分页(README §6.14 整体游标,`next_cursor=null` 表末页);其余指标为单对象聚合,无分页。
- **鉴权**:全部端点经 README §6.14 中间件链;项目级端点叠加项目可见性。
- **时区响应**:每个含时间锚点/序列的响应在 `meta.display_timezone` 回显本次所用 IANA 时区(§2.4),供前端时区化渲染。

---

## 4. UI/UX 设计

### 4.1 信息架构与入口

| 入口 | 位置 | 内容 | 可见角色 |
|------|------|------|----------|
| 项目仪表盘 | 项目详情页「仪表盘」页签 | velocity + burndown + cycle time 卡片 + 时间范围选择器 | 按项目可见性(project.md) |
| 工作区仪表盘 | 工作区独立页(命令面板/导航「洞察」) | 吞吐量趋势 + workload 排行 + agent 统计区 | 工作区成员 |
| agent 统计卡 | agent 详情页(成员名册深链,README §6.12) | 成功率/平均时长/重试率/趋势 sparkline + token 覆盖标注 | 工作区成员 |

> 入口去重:agent 统计卡随成员名册唯一入口呈现(README §6.12「Agents 入口去重」),不在设置里重复罗列。

### 4.2 项目仪表盘

- **时间范围选择器**:预设(本周期/近 30 天/近 90 天)+ 自定义 `from`/`to`;切换即重查(命中缓存即时返回,过期 stale-while-revalidate)。
- **velocity 卡片**:近 N 周期柱状(完成数 + 点数双轴/分组柱),当前周期高亮;hover 显示周期名与完成明细数。
- **burndown 卡片**:理想线(虚线,线性递减)vs 实际线(实线),X 轴日期、Y 轴剩余工作量(count/points 切换);实际线落后于理想线时区域弱填充提示风险。
- **cycle time 卡片**:P50/P90 数值 + 分布直方/分位条;标注样本量 `sample_size` 与 `insufficient_data`(无留痕不计入,诚实呈现)。

### 4.3 工作区仪表盘

> **可见性过滤(R3,协同 MES-4 HIGH-2)**:工作区仪表盘对全体工作区成员开放,但聚合**按请求者项目可见性过滤**——非 private 项目成员看不到该项目的计数/曲线/成员负荷(§3.1 可见性边界);admin/owner 见全工作区聚合(含 private 项目)。UI 对过滤口径给出轻提示(如"按你的项目可见范围统计"),避免把"过滤后数值"误读为全量。

- **吞吐量趋势**:created vs completed 双折线(或柱),按 granularity 切换;附净流量(积压)趋势。
- **workload 排行**:成员/agent 列表,列 = 名称(含 `member_type` 图标:人/agent)、open issues、运行中/排队/需审批(agent 行);按 open issues 降序;agent 行的「运行中 N/排队 M/需审批 K」呈现与 README §6.12 容量呈现一致。
- **agent 统计区**:网格卡片,每 agent 显示成功率(语义色)、平均时长、重试率、近 30 天执行趋势 sparkline;token 覆盖率 <100% 时卡片标注「token 仅覆盖 autopilot 运行」。**R4 可见性**:统计区按统一 execution 可见性 scope 过滤(§2.3.1)——普通成员只见 workspace 可见 agent 的统计(private agent 不呈现),各 agent 的执行计数/时长/token 已剔除其不可见 private project 的执行;UI 与 workload 同给轻提示「按你的可见范围统计」。

### 4.4 agent 详情统计卡

- 顶部 KPI:成功率 / 平均时长 / 重试率 / 超时率;下方近 30 天执行趋势 sparkline + 成功/失败/超时堆叠。
- token 区:total/prompt/completion token + `token_coverage`;覆盖率不足时显式说明口径(仅 autopilot 触发执行有 token)。
- 「查看运行历史」深链到 `/w/{ws}/executions/{id}`(README §6.12 规范深链)。

### 4.5 图表语义色与暗色兼容(README §6.12)

- **一切图表颜色经语义 token 引用**,禁止硬编码色值:成功/完成 = `success`,失败/超时 = `danger`,排队/进行中 = `info`/`warn`,理想线/基准 = 中性 token。
- **亮/暗两套主题各有校准取值**,暗色模式以暗色 token 集整体替换语义 token 实现,两套均满足 WCAG 2.1 AA(4.5:1)。
- **颜色不作唯一信号**:burndown 理想/实际线以线型(虚/实)区分;状态叠加图标/文字;尊重 `prefers-reduced-motion`(图表入场动画可关)。

### 4.6 异常态与空态(README §6.12 矩阵)

- **loading**:skeleton 骨架屏。
- **empty**:窗内无数据 → 空态插画 + 「调整时间范围/新建 issue」主操作;新工作区无任何执行 → agent 统计区空态。
- **insufficient data**:cycle time 样本不足(无状态留痕)→ 卡片标注「N 个工作项因缺少状态变更记录未计入」,不臆造数值。
- **retry**:查询失败 toast + 重试按钮;`query_cost_exceeded` → 提示「收窄时间范围或维度后重试」。
- **permission denied**:无项目可见性 → 「无权限」页 + 联系入口。

---

## 5. 验收标准

### 5.1 功能性 —— 指标口径与 §2 一致(可 SQL 复核)

- [ ] **cycle time**:仅统计 `state_category='done'` 且 `completed_at ∈ [from,to)` 的 issue;起始时间取 `issue_activity` 中目标 category=`from_category` 的最早 `created_at`;P50/P90 经 `percentile_cont` 计算;无留痕/负时长样本不计入且计入 `meta.insufficient_data`(§2.2.1)。给定固定数据集,接口结果与 §2.2.1 SQL 逐值一致。
- [ ] **velocity**:完成判定 = `state_category='done'` 且 `completed_at` 落周期窗(DATE 边界按 `display_timezone` 展开为 UTC);点数为 `SUM(estimate)`;未挂 cycle 的 done issue 不计入任何周期(§2.2.2);**当前归属口径(R3)**:issue 在周期间移动后 velocity 按当前归属重算(响应 meta `scope_caliber='current_attribution'`),不声称还原历史归属。
- [ ] **吞吐量**:created/completed 双序列按 **`calendar_timezone` 本地日历分桶**(R3),窗 `[from,to)`;`net = created - completed`;`granularity` day/week/month 桶边界为当地日历边界(每桶本地标签与 UTC 瞬间窗一致,§2.2.3);`calendar_timezone='UTC'` 退化为 UTC 分桶;**UTC+8 下"某本地日"桶覆盖当地 00:00–24:00(= UTC 前日 16:00–当日 16:00),不以 UTC 日界切割**。
- [ ] **workload**:open issue = `assignee_id` 非空且 `state_category NOT IN (done,cancelled)`(open issue 部分按请求者项目可见性过滤,R3);agent 执行「运行中/排队/需审批」分别对应 `claimed|running|cancelling`/`queued`/`awaiting_approval`,与 README §6.12 容量呈现一致,**执行部分按统一 execution 可见性 scope 过滤(R4,§2.3.1:私有项目执行/private agent 执行对不可见者剔除)**;成员维度经 `members` 统一,`member_type` 为快照(§2.2.4)。
- [ ] **burndown**:`cycle_id`/`milestone_id` 恰好一个(否则 400);实际线 = scope 总量 − 截至各日完成量;理想线线性递减至 0;`metric=count|points` 切换正确;**当前归属口径(R3)**:scope = 当前归属该 cycle/milestone 的 issue 集合(不再声称「曾进入 scope 全部计入」),移入/移出会按当前集合重算曲线,响应 meta `scope_caliber='current_attribution'`(§2.2.5)。
- [ ] **agent 统计**:成功率 = `completed/(completed+failed+timeout)`(cancelled 不入分母但披露 `cancelled_count`);超时率/重试率(`COUNT(attempts)-1` 派生)/平均端到端时长口径与 §2.3 一致;**token 仅来自 `autopilot_runs`**,`token_coverage` 正确返回且 <1 时 UI 标注。
- [ ] **可见性与缓存隔离(R3,协同 §5.6,集成测试 T33)**:① 工作区级聚合(含 `/dashboards/workspace`)按请求者项目可见性过滤——非 private 项目成员得不到该项目的计数/曲线/负荷(§5.6);admin/owner 见全工作区聚合;② 非成员无法经任何聚合端点获得不可见 private project 的统计量(显式多项目聚合含不可见项目 → 整体 `403`,不部分返回,避免集合推断泄露);③ `analytics_snapshots.scope_key` 纳入缓存键:普通成员查询**绝不命中** `ws_admin` 快照(跨权限缓存复用被判失败);不同可见项目集合的请求者各自缓存(scope_key = `projects:<hash>`)、互不串读;④ 项目可见性变更后,旧 scope_key 快照不再被命中(自然失效,无跨权限泄露窗口)。

### 5.2 功能性 —— 时间窗与时区(跨 DST 不错位)

- [ ] 一切 `from`/`to` 为 RFC3339 UTC,窗左闭右开;`from>=to` 返回 `400 invalid_time_range`。
- [ ] 桶边界按 `calendar_timezone` 本地日历对齐(R3),响应回显 `meta.calendar_timezone`(所用 IANA 时区)与每桶 `window_start/window_end`(UTC);DATE 日界按该时区展开为 UTC。
- [ ] **跨 DST 测试**:取一个含 DST 切换的时区(如 `America/New_York` 春进/秋退日)与跨该日的窗,验证 burndown/throughput 的日序列点**按当日 00:00 的 UTC 瞬间**对齐,无重复日、无缺失日、无 23h/25h 错位。
- [ ] 非法 IANA 时区返回 `400 invalid_timezone`。

### 5.3 功能性 —— 只读(不产生源表写入)

- [ ] 对任一指标/仪表盘端点的查询(含 `refresh=true`)**不产生**对 `issues`/`task_executions`/`execution_attempts`/`autopilot_runs`/`issue_activity` 等真源表的 INSERT/UPDATE/DELETE(仅 `analytics_snapshots` 缓存表可写)。
- [ ] 以数据库写入计数/触发器审计验证:查询前后真源表行数与 `updated_at` 不变。

### 5.4 非功能性 —— 性能(命中索引,README §10 基准 P95)

- [ ] 在 README §10 基准数据(单工作区 issue 10 万、task_executions 100 万含 attempts 300 万)下,热缓存:工作区仪表盘聚合、项目仪表盘聚合、agent 统计、cycle time P95 < 500ms;workload 当前快照 P95 < 500ms。
- [ ] 关键聚合查询附 `EXPLAIN (ANALYZE, BUFFERS)`,证明命中既有索引:`idx_issues_project_status`/`idx_issues_assignee`/`idx_issues_cycle`/`idx_issues_milestone`/`idx_issue_activity_issue`(issue.md §2.3)、`idx_executions_agent_time`/`idx_attempts_execution`(runtime.md §2.4)、`idx_run_autopilot_started`(autopilot.md);缓存查找命中 `idx_snapshots_lookup`。
- [ ] 超限维度/成本返回 `400 filter_too_complex`/`422 query_cost_exceeded`(README §6.14),不发生全表失控扫描。

### 5.5 功能性 —— 缓存与真源一致(失效后重算正确)

- [ ] 缓存命中需 `metric_key`/`dim_hash`/窗匹配且 `computed_at` 新于 TTL;过期触发 stale-while-revalidate 或同步重算(§2.6)。
- [ ] **一致性测试**:先查得某指标缓存值 → 修改真源(如把某 issue 置 done / 新增一次 execution)→ 以 `refresh=true` 或待 TTL 过期后重查,结果与**直接聚合(绕过缓存)**逐值一致;`analytics_snapshots` 旧值被覆盖(`UNIQUE(workspace_id, metric_key, dim_hash, window_start, window_end)`)。
- [ ] `workload` 默认不缓存,任意源变更后下次查询即反映最新状态。
- [ ] `analytics_snapshots` 查询/刷新必带 `workspace_id`,跨工作区不可见(多租户隔离,README §6.2)。

### 5.6 安全性 —— 私有项目可见性过滤(HIGH-2;R4 HIGH-6 扩展:execution 指标)

- [ ] **工作区级聚合按请求者项目可见性过滤**:非私有项目 P 成员的普通工作区成员查询 throughput / workload / cycle time(不传 `project_id`)/ `/dashboards/workspace` 时,返回数据**不含项目 P 的 issue 计数/点数/成员负荷/曲线样本**;聚合结果与「手动剔除 P 后重算」一致。
- [ ] **cycle/milestone 直引按归属项目可见性校验**:`velocity?cycle_ids=` / `burndown?cycle_id=` / `burndown?milestone_id=` 引用的 cycle/milestone 归属私有项目时,非该项目成员 → `403 project_not_visible`;不满足可见性的 cycle/milestone 不出现在任何聚合结果中。
- [ ] **私有项目执行负向测试(R4,HIGH-6,集成测试 T33)**:agent A 在私有项目 P 的 issue 上有执行(执行数/失败率/时长/token)——非 P 成员的普通成员查询 `/analytics/agents/stats?agent_id=A` / workload 执行部分 / `/dashboards/workspace` agent 统计区时,**这些执行全部被剔除**(聚合结果与「手动剔除关联 P 的执行后重算」一致),无法经执行计数/成本推断 P 的活动;admin/owner 见全量;P 的成员见含 P 执行的聚合。
- [ ] **private agent 负向测试(R4,HIGH-6)**:`visibility='private'` 的 agent X(非请求者拥有)——普通成员查询 `/analytics/agents/stats?agent_id=X` → `403 agent_not_visible`;workload / workspace dashboard agent 统计区**不呈现** X 的任何统计(其执行无论是否关联 issue 均不可见);X 的 owner 与 admin/owner 可见 X 的统计。
- [ ] **无 issue 执行归属(R4)**:`trigger ∈ (manual, chat, integration)` 且 `issue_id IS NULL` 的执行归属 agent 本身——workspace 可见 agent 的此类执行计入全体工作区成员可见的聚合(不携带项目侧信道);private agent 的此类执行仅 owner/admin 可见。
- [ ] **execution 缓存键隔离(R4)**:`agent_stats` 快照 `ws_admin` 与 `exec:p<hash>:a<hash>` 物理分行,普通成员查询**绝不命中** `ws_admin` 行;项目可见性或 agent 可见性(转 private)变更后,旧 scope_key 快照不再命中(自然失效,无跨权限泄露窗口)。
