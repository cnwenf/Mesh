# 小队(Squad)功能 Spec

> **所属层**:智能体编排层(多智能体编排单元 —— leader 拆解、分派、汇总的闭环,而非通讯录分组)。
> **依赖的其他 Spec**:
> - `workspace.md`:`squads.workspace_id` 等全部业务表外键回 `workspaces.id`,以 workspace 为隔离边界。
> - `member.md`:小队成员、消息收发者、任务编排/执行人、时间线行为主体统一引用 `members.id`(复合 FK,README §6.1/§6.2;人类/agent 由 `members.member_type` 判别,本模块不存判别列)。
> - `issue.md`:小队任务以 issue 为**内容真源**,`squad_tasks.issue_id`(复合 FK)→ `issues(workspace_id, id)`;**「把 issue 分派给小队」采用独占 assignee 模型**:`issues.assignee_id = squads.primary_leader_id`(见 §1.2 S4 / §3.2 / §4.x);拆解出的子任务可挂为父 issue 的子 issue。
> - `runtime.md`:agent 成员的执行落地为逻辑执行 `task_executions`(README §6.4),其生命周期遵循全系统统一长任务状态机 `queued→claimed→running→completed|failed|timeout|cancelled`(另有 requeued/cancelling/awaiting_approval);本 Spec 通过 `execution_id` 引用并观察其终态。
> - `comment-inbox.md`:小队内的指令/汇报与 issue 评论共用「对话」抽象与通知管线(见 chat-session.md);本 Spec 的小队消息为编排专用群聊式记录。
> - `auth.md`:RBAC、审计、限流;agent runtime 持 API token 代 leader 调用拆解/分派/汇报端点。
> **被依赖方**:`autopilot.md`(自动化可把任务派给整个小队)、`kanban.md`(小队任务在看板呈现)。

---

## 全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)

1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `UUID`(`gen_random_uuid()`);所有表含 `created_at` / `updated_at`(`TIMESTAMPTZ`,默认 `now()`,UTC);软删除统一 `deleted_at TIMESTAMPTZ NULL`。
2. **成员**:成员模型以 README §6.1 为唯一权威——所有多态行为主体(成员、编排者、执行人、消息收发者、时间线 actor)统一引用 `members.id`(复合 FK)。**存储层禁止 `*_type`/`*_kind` 判别列**(`creator_type`/`member_type`/`orchestrator_type`/`assignee_type`/`sender_type`/`recipient_type`/`actor_type` 一律不进表);人类/agent 判别一律 JOIN `members.member_type`,API 响应可携带服务端计算的 `member_type` 快照(标注"快照,真源为 members")。**系统行为主体**用 `('member','system')` 模式:`squad_messages.sender_id` 在 `kind='system'` 时可为 NULL,`squad_activity.actor_kind CHECK IN ('member','system')` + `actor_id` 系统时 NULL。
3. **多租户**:跨模块外键一律按 README §6.2 建复合 FK + 目标表 `UNIQUE(workspace_id, id)`;自引用父子/根、任务依赖亦携带 `workspace_id` 建复合自引用 FK。
4. **接口**:基础路径 `/api/v1`;包络 / 分页 / 错误信封 / 幂等写 / HTTP 语义见 README §6.14;**SSE 流式遵循 README §6.8(POST 创建 → GET 流 / fetch-stream,禁止 POST SSE)**。
5. **实时**:统一实时契约见 README §6.7(**频道内** `seq`、`realtime_events` 持久重放、`resume_from`/`resync_required`);事件名 `<entity>.<action>`。
6. **队列 / 投递**:agent 成员执行入队经 transactional outbox(README §6.6),落地为 `task_executions`(README §6.4);副作用幂等键见 README §6.5。
7. **审批**:计划审批统一走 `approvals` 实体(`subject_type='squad_plan'`,README §6.10),本模块的 plan approve/reject 端点为其薄封装。
8. **ORM**:SQLAlchemy 2.x 约定。

---

## 1. 功能描述

### 1.1 模块定位

小队(Squad)是 Mesh 的**多智能体编排单元**。其核心价值不在"把成员堆在一起",而在 **leader 的拆解—分派—汇总闭环**:把一张 issue 交给整个小队,由 leader(通常是编排型 agent)接管,读取任务与共享上下文,拆成若干子任务,声明依赖与阶段,分派给 member(agent 或人)并行/串行执行,最后聚合产出回写父任务。

数据模型上必须让 **`squad_tasks`(编排层)与 `issues`(任务内容层)解耦又互链** —— issue 管"任务内容/评论/状态",squad_task 管"这是哪个小队、哪一层、谁拆解、谁执行、依赖谁",二者通过 `issue_id` 关联。这是小队区别于"群组聊天"的根本。

**「人审 AI 编排」是可配置闸门而非默认阻塞**:`require_plan_approval` 让高风险任务在拆解后暂停等人确认方案,低风险任务全自动直通;配合 observer 角色、叫停权、运行中改成员的护栏,构成"AI 自主 + 人类可控"的分层监督。

**「分派给小队」的责任主体模型(CRITICAL,选定方案)**:在「独占 / 旁路 / 并存」三种候选中,本 Spec **选定独占 assignee 模型**——把 issue 分派给一个小队,即把 `issues.assignee_id` 设为该小队的 `primary_leader_id`(leader 这个**成员**成为 issue 的唯一负责人),同时创建关联 squad+issue 的根 `squad_tasks` 行(编排在旁挂起)。**issue 头部永远只有一个责任主体**:leader 头像 + squad 徽章「X 小队 · leader Y 牵头」,不出现"小队 + 个人"两个并列负责人。理由:① 复用 issue 既有的单一 assignee 语义与看板/通知/触发链路,无需为小队新造一套责任主体;② 责任可追溯(始终有一个明确的人/agent 牵头);③ 编排在 squad_tasks 旁挂,与内容层解耦。约束:小队无 leader 时不可被分派(`422 squad_no_leader`);把 issue 改派给他人/其它小队会**级联取消**原小队根任务(见 §3.2 / §4.x)。

> **R2 修正:唯一 active 身份由显式 `issue_squad_assignments` 表承载(§2.5)**。独占 assignee 模型保留——分派给小队仍在**同一事务**设 `issues.assignee_id = 当前 leader`;但**「这是哪支小队在承接」的唯一权威身份不再由 assignee 值表达,而由显式的 `issue_squad_assignments` active 行表达**。原因:同一 leader 可领导多支小队,`issues.assignee_id = leader` 无法区分到底哪支小队承接;若按 assignee 值判定改派,改派到相同 leader 的另一小队时 assignee 值不变会被误判为 no-op,旧根任务无法可靠取消。因此**改派判定以分派行为准而非 assignee 值**:即使目标小队的 leader 与现任 assignee 相同(同 leader 跨 squad 改派)**也不是 no-op**——取消旧分派行与其根任务、建立新分派行(assignee 值可能不变但语义已变);仅当重复派给**同一小队**且 active 分派已存在时才为 no-op(README §6.9 末段:小队分派不走「assignee 值比较」)。leader 更换/离队、重复提交与历史展示的确定协议见 §2.5 / §4.4。


> **全局名册约定(以 README §6.1 为唯一权威)**:人与 agent 统一登记在 `members` 名册(`members.id` 为统一引用键,人类/agent 由 `members.member_type` 判别)。本 Spec 所有多态行为主体(成员、编排者、执行人、消息收发者、时间线 actor)**统一以单一 `<x>_id → members.id` 复合外键引用,不存 `*_type`/`*_kind` 判别列**;人类/agent 类型一律服务端 JOIN `members.member_type` 解析,API 响应可携带计算 `member_type` 快照。系统行为主体(系统消息、系统时间线)用 `('member','system')` 模式:相应 member FK 置 NULL 并以 `kind`/`actor_kind` 标注(见 §2.7/§2.8,README §6.1)。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| S1 | 小队生命周期 | 创建 / 编辑 / 归档(软解散)/ 恢复 / 受限删除 / 列表搜索 | 项目经理为"支付重构"组建固定小队;结项后归档保留全部历史 |
| S2 | 成员构成与角色 | 多 agent + 人混编;角色 leader / member / observer;单/多 leader | 编码、评审、测试 agent 与人类工程师同列;人类负责人作 observer 只读监督 |
| S3 | 增减/变更成员 | 运行中可改成员,但有护栏(不可移除持 in_progress 子任务者) | 把某 member 提升为 leader;新加 member 仅对新分派生效 |
| S4 | 小队级任务入口(独占 assignee 模型 + 唯一 active 分派行) | 把 issue 分派给**整个小队** = 设 `issues.assignee_id = squads.primary_leader_id`(leader 成员成为该 issue 的**唯一负责人**)**并建唯一 active 的 `issue_squad_assignments` 行**(§2.5),再建根 `squad_tasks`;issue 头部呈现单一责任主体(leader 头像 + squad 徽章「X 小队 · leader Y 牵头」);无 leader → `422 squad_no_leader`;**同 leader 跨 squad 改派不为 no-op**(判定依据是分派行而非 assignee 值,旧根任务级联取消);**重复派给同一小队为 no-op**;leader 更换/离队有确定协议(见 §2.5 / §4.4);把 issue 改派给非小队成员则级联取消小队根任务 | 把"订单结算异步化"交给支付重构小队 |
| S5 | leader 拆解 | 读任务与共享上下文,拆成子任务,写目标与验收口径 | leader 拆为"异步化+幂等""对账""回归验证"三子任务 |
| S6 | 依赖与顺序(DAG + stage) | 无依赖并行,有依赖串行;stage 批量并行 | 异步化与对账并行(stage 1),回归依赖前两者(stage 2) |
| S7 | 多层级拆解 | 子 leader 二次拆解,形成多层父子树(限最大深度) | 复杂子任务由被指派的子 leader 再拆 |
| S8 | 汇总结果 | 全部子任务终态后 leader 聚合产出,回写父任务 | leader 汇总各 member 产出,生成总结回写父 issue |
| S9 | 人类审核拆解方案 | `require_plan_approval`:拆解后暂停等人确认再分派 | 高风险任务 leader 拆完先停,人类批准后才分派 |
| S10 | 任务取消/叫停 | 级联取消未完成子任务,终止相关 agent 运行 | observer 中途叫停整个小队任务 |
| S11 | 小队内消息 | 群聊式;指令(leader→member)/ 汇报(member→leader)/ 闲聊 / 系统 / 上下文 | leader 下达结构化指令;member 完成回报结论与产物链接 |
| S12 | 协作时间线(审计) | 全程可追溯:分派 / 状态变更 / 产出 / 消息,按时间线性呈现 | 排查"这个子任务为什么失败""这个 agent 当时做了什么" |
| S13 | 复用形态 | 常设(standing)/ 临时(adhoc)/ 任务级(task_scoped);可选模板 | 临时拉一支突击队,任务完成后自动归档 |

### 1.3 边界与非目标(明确不做什么)

- **不**定义 issue 的内容/评论/状态领域逻辑 —— 归 `issue.md`(本 Spec 仅以 issue 为内容真源,通过 `issue_id` 关联)。
- **不**定义 agent 运行时的领取/心跳/租约/沙箱 —— 归 `runtime.md`(本 Spec 仅创建运行并观察其终态)。
- **不**定义成员名册与角色权限矩阵 —— 归 `member.md` / `auth.md`(本 Spec 校验"分派对象是小队成员"等业务约束)。
- **不**实现拆解/汇总的 prompt 工程与模型调用细节 —— 归 `agent.md`(leader 能力来自其运行时与绑定技能)。
- **不**支持跨 workspace 组队(YAGNI)。
- **不**做成员模板的市场化分享(模板为可选增强,仅 workspace 内复用)。

---

## 2. 数据模型

五张表串起一条主线:一个 `squad` 由若干 `squad_members`(agent/人,带角色)组成;把一张 issue 交给小队生成一条根 `squad_tasks`,leader 拆成子 `squad_tasks`(父子自引用 + `squad_task_dependencies` 表达先后)并分派给 member;执行中的指令/汇报沉淀到 `squad_messages`(按 task 聚合);每一步关键动作写入 `squad_activity` 形成可追溯时间线。

### 2.1 ER 概览(文字图)

```
workspaces ──隔离──► squads ──1:N──► squad_members ──N:1──► members(member.md, 人/agent)
                       │  (leader_mode / require_plan_approval / max_decompose_depth)
                       │
                       ├──1:N──► squad_tasks ──N:1──► issues(issue.md, 内容真源)
                       │            │  ├─自引用─► parent_task_id / root_task_id(拆解树)
                       │            │  ├─1:N──► squad_task_dependencies(DAG: depends_on)
                       │            │  └─ execution_id ──► task_executions(runtime.md, README §6.4, agent 执行)
                       │            ▼
                       ├──1:N──► issue_squad_assignments(小队分派唯一 active 身份, R2;关联 squads×issues, root_task_id 回填根任务)
                       │            └─ 部分唯一索引:每 issue 至多一条 status='active' 分派(唯一身份保证)
                       ├──1:N──► squad_messages(指令/汇报/闲聊/系统/上下文, 按 task 聚合)
                       └──1:N──► squad_activity(时间线/审计, 只增不改)

多态主体:orchestrator / assignee / sender / recipient / actor = <x>_id ─复合 FK─► members.id(判别 JOIN members.member_type,不存 *_type 列)
系统主体:squad_messages.sender_id(kind='system' 时 NULL)/ squad_activity.actor_kind IN('member','system')+ actor_id(NULL=系统)
issue 责任主体:把 issue 分派给小队 ⇒ 同事务设 issues.assignee_id = squads.primary_leader_id(独占模型)+ 建 issue_squad_assignments active 行(唯一身份)+ 建根 squad_tasks;改派判定以分派行为准(§2.5,README §6.9 末段)
```

### 2.2 表:`squads`(小队主表)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离(所有查询强制带) |
| `name` | TEXT | NOT NULL,CHECK (char_length BETWEEN 1 AND 80) | — | 小队名称 |
| `description` | TEXT | NULL | NULL | 目标 / 职责描述 |
| `instructions` | TEXT | NULL | NULL | **leader 持久指令**:leader 每次接管任务时读取的常驻方针(区别于单次任务 brief 与置顶上下文消息);创建/编辑小队时设置,随 `render_squad` 返回 |
| `avatar_url` | TEXT | NULL | NULL | 头像地址 |
| `kind` | TEXT | NOT NULL,CHECK IN ('standing','adhoc','task_scoped') | `'standing'` | 形态:常设 / 临时 / 任务级 |
| `status` | TEXT | NOT NULL,CHECK IN ('active','archived') | `'active'` | 状态;归档=软解散 |
| `leader_mode` | TEXT | NOT NULL,CHECK IN ('single','multi') | `'single'` | 单 leader(推荐)/ 多 leader |
| `primary_leader_id` | UUID | NULL,**复合 FK `(workspace_id, primary_leader_id) → members(workspace_id, id)`** | NULL | 主 leader(多 leader 时负责最终汇总);**独占 assignee 模型下即"分派给小队"时 `issues.assignee_id` 的取值**(§1.2 S4) |
| `require_plan_approval` | BOOLEAN | NOT NULL | `false` | 拆解方案是否需人类审核后才分派(干预开关);命中即在统一 `approvals` 建 `subject_type='squad_plan'` 行(README §6.10) |
| `max_decompose_depth` | SMALLINT | NOT NULL,CHECK (BETWEEN 1 AND 4) | `2` | 允许的最大拆解层级 |
| `creator_id` | UUID | NOT NULL,**复合 FK `(workspace_id, creator_id) → members(workspace_id, id)`** | — | 创建者 ID(人或 agent;判别 JOIN members,**不存 creator_type**,README §6.1/§6.2) |
| `archived_at` | TIMESTAMPTZ | NULL | NULL | 归档时间 |
| `archived_by_id` | UUID | NULL,**复合 FK `(workspace_id, archived_by_id) → members(workspace_id, id)`** | NULL | 归档操作人 |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除(仅 owner 受限路径) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> **复合 FK 引用前提(README §6.2)**:`squads` 被 `squad_members.squad_id`、`squad_tasks.squad_id`、`squad_messages.squad_id`、`squad_activity.squad_id` 复合引用,除 `PK(id)` 外建 **`UNIQUE (workspace_id, id)`**。

### 2.3 表:`squad_members`(成员关系)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `squad_id` | UUID | NOT NULL,**复合 FK `(workspace_id, squad_id) → squads(workspace_id, id)`** | — | 所属小队(README §6.2) |
| `member_id` | UUID | NOT NULL,**复合 FK `(workspace_id, member_id) → members(workspace_id, id)`** | — | 成员(人或 agent;判别 JOIN members,**不存 member_type**,README §6.1/§6.2) |
| `role` | TEXT | NOT NULL,CHECK IN ('leader','member','observer') | `'member'` | 角色 |
| `joined_at` | TIMESTAMPTZ | NOT NULL | `now()` | 加入时间 |
| `left_at` | TIMESTAMPTZ | NULL | NULL | 离队时间(NULL=在队;软删除成员关系) |
| `added_by_id` | UUID | NULL,**复合 FK `(workspace_id, added_by_id) → members(workspace_id, id)`** | — | 添加者 ID(人或 agent;**NULL=系统自动添加**,判别 JOIN members,不存 added_by_type) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> 用 `left_at` 软删除而非物理删,保留"某人曾是成员"的历史;再次加入插入新行(旧行 `left_at` 已置位,不冲突)。应用层校验:每个 `active` 小队至少一个 `role='leader'`。

### 2.4 表:`squad_tasks`(小队任务 —— 编排核心)

> `squad_task` 是**编排层记录**,包裹一张 issue(复用 issues 作为任务真源),并承载拆解树、分派、状态机。issue 管内容/评论/状态,squad_task 管层级/依赖/分派/状态机。一一对应,或一 issue 多 squad_task(重派)。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `squad_id` | UUID | NOT NULL,**复合 FK `(workspace_id, squad_id) → squads(workspace_id, id)`** | — | 承接小队(README §6.2) |
| `issue_id` | UUID | NOT NULL,**复合 FK `(workspace_id, issue_id) → issues(workspace_id, id)`** | — | 关联 issue(内容真源,README §6.2) |
| `parent_task_id` | UUID | NULL,**复合自引用 FK `(workspace_id, parent_task_id) → squad_tasks(workspace_id, id)`** | NULL | 父任务(拆解树自引用);根任务为 NULL |
| `root_task_id` | UUID | NULL,**复合自引用 FK `(workspace_id, root_task_id) → squad_tasks(workspace_id, id)`** | NULL | 根任务(冗余,加速整树聚合;根指向自身) |
| `depth` | SMALLINT | NOT NULL,CHECK (BETWEEN 0 AND 4) | `0` | 拆解层级(根=0),受 `squads.max_decompose_depth` 约束 |
| `title_snapshot` | TEXT | NOT NULL | — | 标题快照(避免渲染时回查 issue) |
| `status` | TEXT | NOT NULL,CHECK IN ('pending','decomposing','awaiting_plan_approval','dispatching','in_progress','blocked','aggregating','done','failed','cancelled') | `'pending'` | 任务状态机(见 §4.4);`awaiting_plan_approval` 表示有一条 `approvals` 待决(README §6.10) |
| `orchestrator_id` | UUID | NULL,**复合 FK `(workspace_id, orchestrator_id) → members(workspace_id, id)`** | NULL | 拆解/编排者 ID(通常是 leader agent;判别 JOIN members,**不存 orchestrator_type**) |
| `assignee_id` | UUID | NULL,**复合 FK `(workspace_id, assignee_id) → members(workspace_id, id)`** | NULL | 执行人 ID(member);根任务可空(判别 JOIN members,**不存 assignee_type**) |
| `stage` | SMALLINT | NULL | NULL | 执行阶段编号(同 stage 可并行;stage 间串行) |
| `execution_id` | UUID | NULL,**复合 FK `(workspace_id, execution_id) → task_executions(workspace_id, id)`** | NULL | 执行人为 agent 时,其逻辑执行 ID(→ runtime.md `task_executions`,README §6.4) |
| `plan_markdown` | TEXT | NULL | NULL | leader 拆解方案说明(人类审核对象) |
| `result_summary` | TEXT | NULL | NULL | 汇总结果 / member 完成小结 |
| `dispatched_at` | TIMESTAMPTZ | NULL | NULL | 分派时间 |
| `started_at` | TIMESTAMPTZ | NULL | NULL | 开始执行时间 |
| `finished_at` | TIMESTAMPTZ | NULL | NULL | 结束时间 |
| `failure_reason` | TEXT | NULL | NULL | 失败原因 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**关系与约束**:
- `parent_task_id → squad_tasks(workspace_id, id)` 复合自引用构成拆解树;应用层校验子任务与父任务同 `workspace_id`、同 `squad_id`、同 `root_task_id`,且 `depth = parent.depth + 1 ≤ max_decompose_depth`。
- `root_task_id` 在根任务创建时事务内回填为自身 id,便于"给定任意节点取整树"。
- 状态迁移由服务端集中校验(非法迁移返回 409 `conflict`),见 §4.4。
- **复合 FK 引用前提(README §6.2)**:`squad_tasks` 被自引用(parent/root)、`squad_task_dependencies`、`squad_messages.task_id`、`squad_activity.task_id` 引用,除 `PK(id)` 外建 **`UNIQUE (workspace_id, id)`**。
- **根任务即小队分派(独占 assignee 模型 + 唯一 active 分派行,§1.2 S4)**:把 issue 分派给小队 = 在**同一事务**设 `issues.assignee_id = squads.primary_leader_id`(leader 成员成为 issue 唯一负责人)+ **建 `issue_squad_assignments` active 行**(§2.5,唯一身份)+ 建本表根任务(`parent_task_id=NULL`、`root_task_id` 回填自身、`squad_id`+`issue_id` 关联),并把根任务 id **回填到分派行的 `root_task_id`**(双向定位);小队无 leader → `422 squad_no_leader`,不建分派行、不建根任务、不改 issue assignee。
- **计划审批关联(取此方案,README §6.10)**:squad_tasks **不冗余 approval 列**;待决计划审批经统一 `approvals` 实体的 **`approvals.subject_task_id` 复合 FK `(workspace_id, subject_task_id) → squad_tasks(workspace_id, id)`**(R2:已升为物理复合 FK,不再是逻辑关联)反查,`subject_type='squad_plan'`。存在 `status='pending'` 的关联 approval 时,根任务 `status='awaiting_plan_approval'`;**同一根任务仅一个 pending 审批(README §6.10 部分唯一索引 `uq_approvals_pending_task`)**;approve/reject 经 `POST /api/v1/approvals/{id}/approve|reject` 收口。

### 2.5 表:`issue_squad_assignments`(小队分派唯一 active 身份,R2)

> **本表是「哪支小队在承接这张 issue」的唯一权威身份**(R2 新增,见 §1.1 R2 修正)。独占 assignee 模型下 `issues.assignee_id = leader`,但同一 leader 可领导多支小队,assignee 值无法区分小队;改派判定因此以**本表的 active 分派行**为准,而非 assignee 值(README §6.9 末段)。每张 issue 至多一条 `status='active'` 分派(部分唯一索引兜底),active→cancelled/completed 的历史行永久保留供时间线展示「曾由 X 小队承接」。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离(所有查询强制带) |
| `issue_id` | UUID | NOT NULL,**复合 FK `(workspace_id, issue_id) → issues(workspace_id, id)` ON DELETE CASCADE** | — | 被分派的 issue(内容真源,README §6.2;issue 删除即级联其分派历史) |
| `squad_id` | UUID | NOT NULL,**复合 FK `(workspace_id, squad_id) → squads(workspace_id, id)`** | — | 承接小队(README §6.2) |
| `root_task_id` | UUID | NULL,**复合 FK `(workspace_id, root_task_id) → squad_tasks(workspace_id, id)`** | NULL | 该分派建立的根任务(根任务建立后**回填**,双向定位;分派行 ↔ 根任务一一对应) |
| `leader_member_id` | UUID | NOT NULL,**复合 FK `(workspace_id, leader_member_id) → members(workspace_id, id)`** | — | **分派时 leader 快照**(判别 JOIN members,不存 leader_type);leader 更换时同事务更新(见语义要点) |
| `status` | TEXT | NOT NULL,CHECK IN ('active','cancelled','completed') | `'active'` | 分派状态:active=当前承接(每 issue 至多一条)/ cancelled=被改派或离队取消 / completed=根任务完成 |
| `cancel_reason` | TEXT | NULL | NULL | 取消原因:`reassigned`(改派其它小队)/ `leader_lost`(leader 离队无替补)/ `issue_reassigned`(issue 改派给非小队成员)/ `done`(完成) |
| `assigned_at` | TIMESTAMPTZ | NOT NULL | `now()` | 分派建立时间(历史展示排序键) |
| `cancelled_at` | TIMESTAMPTZ | NULL | NULL | 取消/完成时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:
- **`UNIQUE (workspace_id, id)`**(供复合 FK 引用,README §6.2)。
- **部分唯一索引(唯一身份保证)**:`CREATE UNIQUE INDEX uq_issue_squad_active ON issue_squad_assignments(issue_id) WHERE status='active';` —— **每张 issue 至多一条 active 分派**,数据库层兜住并发双派(恰一条成功)。
- 索引:`idx_issue_squad_assignments_squad ON issue_squad_assignments(squad_id, status)`(按小队查在承/历史)、`idx_issue_squad_assignments_issue ON issue_squad_assignments(issue_id, assigned_at DESC)`(issue 时间线「曾由哪些小队承接」历史展示)。

**语义要点(权威,逐条)**:
- **建立**:分派给小队 = 同事务:若该 issue 存在 active 分派 → 先级联取消其根任务与未完成子任务(见 §4.4)+ 旧行置 `cancelled`(`cancel_reason='reassigned'`),再 INSERT 新 active 行(部分唯一索引兜底并发,冲突即重试/`409 conflict`)并设 `issues.assignee_id = squad.primary_leader_id`;**小队无 leader → `422 squad_no_leader`**(不建分派行、不改 issue assignee)。
- **重复提交幂等**:同一 issue 重复派给**同一 squad**且 active 行已存在 → **no-op**,返回既有分派与根任务(不重建、不取消)。
- **同 leader 跨 squad 改派**:目标 squad 的 leader 与现任 assignee 相同**亦不是 no-op**(判定依据是分派行而非 assignee 值):旧行 `cancelled` + 其根任务级联取消,新行建立(`issues.assignee_id` 值可能不变,但承接小队与根任务已变,README §6.9 末段)。
- **leader 更换**:`squads.primary_leader_id` 变更时,**同事务**把该小队所有 active 分派行的 `leader_member_id` 与对应 `issues.assignee_id` 更新为新 leader + 写审计(`squad_activity`)+ 广播 `squad_assignment.changed`(README §6.7 注册表);**根任务不取消**(承接小队未变,仅换牵头人)。
- **leader 离队且无替补**:active 分派**保留**,但其根任务置 `blocked`(`failure_reason` 记 `leader_lost`)+ 通知发起人与管理员;补上 leader 后解除 `blocked`(并按上条同事务传播 `leader_member_id`/`assignee_id`)。
- **issue 被改派给非小队成员**(PATCH `issues.assignee_id` 离开 active 分派的 leader):active 行置 `cancelled`(`cancel_reason='issue_reassigned'`)+ 其根任务级联取消(§4.4 改派级联)。
- **完成**:根任务 `done` → 分派行 `status='completed'`(`cancel_reason='done'`);历史行(active→cancelled/completed)**永久保留**,供 issue/小队时间线展示「曾由 X 小队承接」。

### 2.6 表:`squad_task_dependencies`(任务依赖 DAG)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | 隔离(供复合自引用 FK,README §6.2) |
| `task_id` | UUID | NOT NULL,**复合 FK `(workspace_id, task_id) → squad_tasks(workspace_id, id)`** | 当前任务 |
| `depends_on_task_id` | UUID | NOT NULL,**复合 FK `(workspace_id, depends_on_task_id) → squad_tasks(workspace_id, id)`** | 前置任务(task_id 须等其 done) |
| `created_at` | TIMESTAMPTZ | NOT NULL | |

**约束**:`UNIQUE (task_id, depends_on_task_id)`;CHECK `task_id <> depends_on_task_id`;服务端建表时即支持递归 CTE 环检测。
> 依赖用独立关系表而非 JSONB,便于环检测与"谁在等谁"反查。`stage` 是依赖的**粗粒度补充**(按阶段批量并行),二者并存:stage 表达"批次",依赖表达"精确先后"。

### 2.7 表:`squad_messages`(小队内消息)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `squad_id` | UUID | NOT NULL,**复合 FK `(workspace_id, squad_id) → squads(workspace_id, id)`** | — | 所属小队(README §6.2) |
| `task_id` | UUID | NULL,**复合 FK `(workspace_id, task_id) → squad_tasks(workspace_id, id)`** | NULL | 关联任务(可空=小队级闲聊) |
| `sender_id` | UUID | NULL,**复合 FK `(workspace_id, sender_id) → members(workspace_id, id)`**;**CHECK (`kind='system' OR sender_id IS NOT NULL`)** | — | 发送者(人或 agent;判别 JOIN members,**不存 sender_type**);**`kind='system'` 时为 NULL(系统消息)** |
| `recipient_id` | UUID | NULL,**复合 FK `(workspace_id, recipient_id) → members(workspace_id, id)`** | NULL | 接收者(人或 agent;判别 JOIN members,**不存 recipient_type**);NULL=广播全队 |
| `kind` | TEXT | NOT NULL,CHECK IN ('chat','instruction','report','system','context') | `'chat'` | 闲聊 / 指令 / 汇报 / 系统 / 共享上下文 |
| `body_markdown` | TEXT | NOT NULL | — | 原始 markdown(真源) |
| `body_html` | TEXT | NULL | NULL | 服务端净化后的 HTML 缓存 |
| `body_text` | TEXT | NULL | NULL | 纯文本(搜索 / 摘要 / 通知预览) |
| `pinned` | BOOLEAN | NOT NULL | `false` | 是否置顶(共享上下文 / 关键决策) |
| `attachment_ids` | JSONB | NOT NULL | `'[]'` | 附件 ID 列表(见 attachment.md) |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> `kind='instruction'` 且发送者为 leader 时,member 端呈现为"待办指令";若接收者是 agent member,则触发其运行(副作用动作,见 §5.3 回环抑制)。

### 2.8 表:`squad_activity`(协作时间线 / 审计)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `squad_id` | UUID | NOT NULL,**复合 FK `(workspace_id, squad_id) → squads(workspace_id, id)`** | — | 所属小队(README §6.2) |
| `task_id` | UUID | NULL,**复合 FK `(workspace_id, task_id) → squad_tasks(workspace_id, id)`** | NULL | 关联任务(可空=小队级事件) |
| `actor_kind` | TEXT | NOT NULL,CHECK IN ('member','system') | — | 行为主体类别:`member`=成员(人/agent,判别 JOIN members),`system`=系统;**不存 human/agent 判别列**(README §6.1) |
| `actor_id` | UUID | NULL,**复合 FK `(workspace_id, actor_id) → members(workspace_id, id)`**;**CHECK (`actor_kind='system' OR actor_id IS NOT NULL`)** | — | 行为主体 ID;**`actor_kind='system'` 时为 NULL** |
| `action` | TEXT | NOT NULL | — | 事件枚举(见下) |
| `target_type` | TEXT | NULL | NULL | 目标实体类型(squad/member/task/message/issue) |
| `target_id` | UUID | NULL | NULL | 目标实体 ID |
| `payload` | JSONB | NOT NULL | `'{}'` | 渲染快照(变更前后值、子任务计数),实体被删后仍可读 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | 活动只增不改 |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | 约定列 |

**`action` 枚举**:`squad_created` / `squad_updated` / `squad_archived` / `squad_restored` / `member_added` / `member_removed` / `role_changed` / `task_received` / `decompose_started` / `plan_submitted` / `plan_approved` / `plan_rejected` / `task_decomposed` / `task_dispatched` / `task_started` / `task_blocked` / `task_finished` / `task_failed` / `task_cancelled` / `task_aggregated` / `message_sent`(仅关键消息可选入时间线)/ `leader_evaluated`(leader 触发-评估闭环结果,payload `result ∈ {action, no_action, failed}`,见 §4.4 注)。

### 2.9 索引与约束

```sql
-- 复合 FK 引用前提(README §6.2):被跨表引用的工作区级表建 UNIQUE(workspace_id, id)
ALTER TABLE squads ADD CONSTRAINT uq_squads_ws_id UNIQUE (workspace_id, id);
ALTER TABLE squad_tasks ADD CONSTRAINT uq_squad_tasks_ws_id UNIQUE (workspace_id, id);
ALTER TABLE issue_squad_assignments ADD CONSTRAINT uq_issue_squad_assignments_ws_id UNIQUE (workspace_id, id);

-- 小队分派唯一 active 身份(R2,§2.5):每 issue 至多一条 active 分派,兜住并发双派
CREATE UNIQUE INDEX uq_issue_squad_active ON issue_squad_assignments(issue_id) WHERE status = 'active';
CREATE INDEX idx_issue_squad_assignments_squad ON issue_squad_assignments(squad_id, status);
CREATE INDEX idx_issue_squad_assignments_issue ON issue_squad_assignments(issue_id, assigned_at DESC);

-- 小队列表与筛选
CREATE UNIQUE INDEX uq_squads_name ON squads(workspace_id, name)
  WHERE deleted_at IS NULL AND status = 'active';
CREATE INDEX idx_squads_list ON squads(workspace_id, status, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_squads_kind ON squads(workspace_id, kind, status);

-- 成员:当前在队 / 反查我加入的小队(README §6.1:不含 *_type 判别列)
CREATE UNIQUE INDEX uq_squad_member_active ON squad_members(squad_id, member_id)
  WHERE left_at IS NULL;
CREATE INDEX idx_squad_members_active ON squad_members(squad_id, role) WHERE left_at IS NULL;
CREATE INDEX idx_squad_members_member ON squad_members(member_id) WHERE left_at IS NULL;

-- 任务:列表 / 拆解树 / 父子汇总 / member 工作台 / 活跃快查
CREATE INDEX idx_squad_tasks_squad ON squad_tasks(workspace_id, squad_id, status, created_at DESC);
CREATE INDEX idx_squad_tasks_tree ON squad_tasks(root_task_id, depth, created_at);
CREATE INDEX idx_squad_tasks_parent ON squad_tasks(parent_task_id, status);
CREATE INDEX idx_squad_tasks_assignee ON squad_tasks(assignee_id, status);
CREATE INDEX idx_squad_tasks_issue ON squad_tasks(workspace_id, issue_id);
CREATE INDEX idx_squad_tasks_active ON squad_tasks(squad_id)
  WHERE status NOT IN ('done','failed','cancelled');

-- 依赖:取前置集合 / 反查被阻塞者
CREATE UNIQUE INDEX uq_task_dep ON squad_task_dependencies(task_id, depends_on_task_id);
CREATE INDEX idx_dep_task ON squad_task_dependencies(task_id);
CREATE INDEX idx_dep_blocker ON squad_task_dependencies(depends_on_task_id);

-- 消息:流主查询 / 按任务聚合 / 收件箱 / 置顶
CREATE INDEX idx_messages_squad ON squad_messages(workspace_id, squad_id, created_at) WHERE deleted_at IS NULL;
CREATE INDEX idx_messages_task ON squad_messages(squad_id, task_id, created_at) WHERE task_id IS NOT NULL;
CREATE INDEX idx_messages_recipient ON squad_messages(recipient_id, created_at);
CREATE INDEX idx_messages_pinned ON squad_messages(squad_id) WHERE pinned = true;

-- 时间线
CREATE INDEX idx_activity_squad ON squad_activity(workspace_id, squad_id, created_at DESC);
CREATE INDEX idx_activity_task ON squad_activity(squad_id, task_id, created_at) WHERE task_id IS NOT NULL;
CREATE INDEX idx_activity_actor ON squad_activity(actor_kind, actor_id, created_at);
```

### 2.10 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `squads.workspace_id` 等 | → `workspaces.id` | workspace.md | 隔离 |
| `squad_members.member_id`、`squads.primary_leader_id`/`creator_id`/`archived_by_id`、`squad_tasks.orchestrator_id`/`assignee_id`、消息收发者(`sender_id`/`recipient_id`)、时间线 `actor_id`、`squad_members.added_by_id` | 复合 FK → `members(workspace_id, id)` | member.md | 多态主体(人/agent 对称;判别 JOIN members,不存 *_type;系统主体置 NULL,README §6.1/§6.2) |
| `squad_members.squad_id`、`squad_tasks.squad_id`、`squad_messages.squad_id`、`squad_activity.squad_id` | 复合 FK → `squads(workspace_id, id)` | 本模块 | 小队归属(README §6.2) |
| `squad_tasks.parent_task_id`/`root_task_id`、`squad_task_dependencies.task_id`/`depends_on_task_id`、消息/时间线 `task_id` | 复合(自引用)FK → `squad_tasks(workspace_id, id)` | 本模块 | 拆解树/DAG/按任务聚合(README §6.2) |
| `squad_tasks.issue_id`、`issue_squad_assignments.issue_id` | 复合 FK → `issues(workspace_id, id)` | issue.md | 任务内容真源;**独占 assignee 模型:`issues.assignee_id = squads.primary_leader_id`**(§1.2 S4);分派行 `issue_id` `ON DELETE CASCADE`(issue 删除级联其分派历史) |
| `issue_squad_assignments.squad_id` | 复合 FK → `squads(workspace_id, id)` | 本模块 | 承接小队(§2.5,README §6.2) |
| `issue_squad_assignments.root_task_id` | 复合 FK → `squad_tasks(workspace_id, id)` | 本模块 | 分派建立的根任务(建立后回填,§2.5) |
| `issue_squad_assignments.leader_member_id` | 复合 FK → `members(workspace_id, id)` | member.md | 分派时 leader 快照(leader 更换同事务更新,§2.5;判别 JOIN members,不存 leader_type) |
| `squad_tasks.execution_id` | 复合 FK → `task_executions(workspace_id, id)` | runtime.md | agent 成员的逻辑执行实例(README §6.4) |
| `approvals.subject_task_id` | 复合 FK → `squad_tasks(workspace_id, id)` | README §6.10 | `subject_type='squad_plan'` 的计划审批(R2:已升为物理复合 FK,不再是逻辑关联;同 subject 仅一个 pending,部分唯一索引 `uq_approvals_pending_task`) |
| `squad_messages.attachment_ids` | 多态逻辑外键(行带 `workspace_id`) | attachment.md | 消息附件 |

---

## 3. 接口设计

REST 基础路径 `/api/v1`,集合嵌套于 `/workspaces/{ws}/`;鉴权 `Authorization: Bearer <token>`(成员会话 token 或 agent runtime 的 API token,见 auth.md)。**成功包络 / 游标分页 / 错误信封 / 乐观并发 / 幂等写 / 过滤限制一律以 README §6.14 为唯一权威**(单对象 `{"data":{...}}`、列表 `{"data":[...],"next_cursor":<opaque|null>}`,`next_cursor=null` 表示末页;错误 `{"error":{"code","message","details"}}`,code 为 snake_case),本 Spec 不重复定义,仅列本模块具名错误码。**编排进度的 SSE 流遵循 README §6.8(POST 创建 → GET 流 / fetch-stream,浏览器原生 EventSource 不支持 POST SSE)**。

### 3.1 REST 端点清单

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/workspaces/{ws}/squads` | 列出小队(`status`/`kind`/`q`;`q` 为字面子串匹配,通配符 `%`/`_` 转义;每项响应含 `member_preview`(至多 8 名在队成员快照 `{member_id, member_type, name, role}`,供列表头像墙) | 成员 |
| POST | `/workspaces/{ws}/squads` | 创建小队(可带初始成员) | 成员 |
| GET | `/workspaces/{ws}/squads/{squad_id}` | 详情(含成员摘要、活跃任务计数) | 小队成员 / observer / admin |
| PATCH | `/workspaces/{ws}/squads/{squad_id}` | 更新名称/描述/头像/编排开关 | 小队管理权限 / admin |
| POST | `/workspaces/{ws}/squads/{squad_id}/archive` | 归档(软解散) | 小队管理权限 / admin |
| POST | `/workspaces/{ws}/squads/{squad_id}/restore` | 恢复 | 小队管理权限 / admin |
| GET | `/workspaces/{ws}/squads/{squad_id}/members` | 列出成员(`role`) | 小队成员 |
| POST | `/workspaces/{ws}/squads/{squad_id}/members` | 添加成员(批量) | 小队管理权限 / admin |
| PATCH | `/workspaces/{ws}/squads/{squad_id}/members/{member_id}` | 变更角色 | 小队管理权限 / admin |
| DELETE | `/workspaces/{ws}/squads/{squad_id}/members/{member_id}` | 移除成员(置 `left_at`) | 小队管理权限 / admin |
| POST | `/workspaces/{ws}/squads/{squad_id}/tasks` | 把任务交给小队:**独占 assignee 模型**——设 `issues.assignee_id = squads.primary_leader_id` + **建唯一 active 的 `issue_squad_assignments` 分派行**(§2.5)+ 建根任务 + 唤醒 leader;**同 squad 重复提交 = no-op**(返回既有分派与根任务);**同 leader 跨 squad 改派取消旧根任务**(判定按分派行而非 assignee 值,永不为 no-op,README §6.9 末段);无 leader → `422 squad_no_leader`(§1.2 S4) | 对 issue 有分派权 / autopilot |
| GET | `/workspaces/{ws}/squads/{squad_id}/tasks` | 列出小队任务(`status`) | 小队成员 |
| GET | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}` | 单任务详情 | 小队成员 |
| GET | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/tree` | 拆解树(整棵子任务,含依赖) | 小队成员 |
| GET | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/status` | 长任务状态查询(拆解/汇总进度) | 小队成员 |
| GET | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/stream` | SSE 流式订阅编排进度。**遵循 README §6.8 的 GET 流端点**:资源由 `POST .../tasks`(创建根任务,返回 `stream_url`)/`POST .../subtasks` 触发,客户端 `GET` 本端点(EventSource 兼容,携 `Last-Event-ID` 断点续传);**非 POST SSE** | 小队成员 |
| POST | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/subtasks` | leader 创建子任务(批量,含依赖) | orchestrator agent / admin |
| POST | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/plan/approve` | 人类批准拆解方案。**`POST /api/v1/approvals/{id}/approve` 的薄封装**:定位该任务的待决 `approvals`(`subject_task_id=task_id`,`subject_type='squad_plan'`)后转发(README §6.10);保留便捷路径 | 人类成员 / observer / admin |
| POST | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/plan/reject` | 驳回方案(leader 重拆)。**`POST /api/v1/approvals/{id}/reject` 的薄封装**(README §6.10) | 人类成员 / observer / admin |
| POST | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/dispatch` | 分派已就绪的子任务 | orchestrator agent / admin |
| PATCH | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/status` | 人工改任务状态(看板拖拽落点,§4.2;body `{status, result_summary?}`;服务端按 §4.4 状态机校验,非法迁移 409 `conflict`;done/failed 触发依赖解锁与父任务聚合) | 小队成员 / observer / admin |
| GET | `/workspaces/{ws}/squads/assignments/by-issue/{issue_id}` | 查询某 issue 当前的 active 小队分派(§2.5;返回 `{assignment_id, squad_id, squad_name, root_task_id, leader 快照}` 或 `data: null`;issue 头部单一责任主体呈现用,§4.3-2) | 成员 |
| POST | `/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/cancel` | 取消(级联取消未完成子任务) | 发起人 / observer / admin |
| GET | `/workspaces/{ws}/squads/{squad_id}/messages` | 列出消息(`task_id`/`kind`) | 小队成员 |
| POST | `/workspaces/{ws}/squads/{squad_id}/messages` | 发送消息 | 小队成员 |
| GET | `/workspaces/{ws}/squads/{squad_id}/activity` | 协作时间线(`task_id`/`action`) | 小队成员 |
| GET | `/workspaces/{ws}/squads/{squad_id}/export` | **归档导出**(§4.6):任务清单(含结果摘要)+ 任务消息(按 kind 标注、关联任务 tag)+ 协作时间线,单一 markdown 文档;`Content-Disposition: attachment; filename="squad-{squad_id}.md"`;正文为 markdown 源文本直嵌,不渲染 HTML | 小队成员 / observer / admin |

### 3.2 请求/响应 JSON 示例

> **关于载荷中的 `member_type`/`assignee_type`(README §6.1)**:以下示例 JSON 里出现的 `member_type`/`assignee_type` 仅为**服务端 JOIN `members.member_type` 计算出的快照**(响应)或客户端可选提示(请求),**绝非存储列**;权威类型一律由 `member_id → members.id` 解析,客户端即便不传 `member_type` 亦可,服务端以 `members` 为准。下文为可读性保留该快照字段。

**创建小队** `POST /api/v1/workspaces/{ws}/squads`
```json
// Request
{ "name": "支付重构小队", "description": "负责支付链路的重构与回归验证",
  "instructions": "所有改动必须附回归验证结论;涉及资金计算一律用 decimal。",
  "kind": "standing", "leader_mode": "single",
  "require_plan_approval": true, "max_decompose_depth": 2,
  "members": [
    {"member_type": "agent", "member_id": "mem-leader-01", "role": "leader"},
    {"member_type": "agent", "member_id": "mem-coder-02", "role": "member"},
    {"member_type": "agent", "member_id": "mem-reviewer-03", "role": "member"},
    {"member_type": "human", "member_id": "mem-zhang-09", "role": "observer"}
  ] }
// 201 Response
{ "data": {
    "id": "sq-1f3a", "workspace_id": "ws-001", "name": "支付重构小队",
    "description": "负责支付链路的重构与回归验证", "kind": "standing", "status": "active",
    "leader_mode": "single", "primary_leader_id": "mem-leader-01",
    "require_plan_approval": true, "max_decompose_depth": 2, "member_count": 4,
    "member_preview": [
      {"member_type": "agent", "member_id": "mem-leader-01", "name": "orchestrator", "role": "leader"},
      {"member_type": "agent", "member_id": "mem-coder-02", "name": "coder", "role": "member"}
    ],
    "leaders": [{"member_type": "agent", "member_id": "mem-leader-01", "name": "orchestrator"}],
    "created_at": "2026-07-24T09:00:00Z", "updated_at": "2026-07-24T09:00:00Z" } }
```

**把任务交给小队(独占 assignee 模型)** `POST /api/v1/workspaces/{ws}/squads/{squad_id}/tasks`
```json
// Request
{ "issue_id": "i-2201", "brief": "把订单结算从同步改为异步,并补齐幂等与对账。",
  "priority": "high", "due_date": "2026-08-05T18:00:00Z" }
// 202 Response(已受理,异步唤醒 leader)
{ "data": {
    "assignment_id": "isa-01", "id": "st-root-9001", "squad_id": "sq-1f3a", "issue_id": "i-2201",
    "parent_task_id": null, "root_task_id": "st-root-9001", "depth": 0,
    "title_snapshot": "订单结算异步化改造", "status": "pending",
    "orchestrator_id": "mem-leader-01",
    "issue_assignee_id": "mem-leader-01",
    "status_url": "/api/v1/workspaces/ws-001/squads/sq-1f3a/tasks/st-root-9001/status",
    "stream_url": "/api/v1/workspaces/ws-001/squads/sq-1f3a/tasks/st-root-9001/stream",
    "created_at": "2026-07-24T09:05:00Z" } }
// 小队无 leader → 422
{ "error": {"code": "squad_no_leader", "message": "该小队尚无 leader,无法承接分派", "details": {}} }
```
> **独占 assignee 模型(§1.2 S4 / §2.5)**:服务端在**同一事务**设 `issues.assignee_id = squads.primary_leader_id`(leader 成员成为该 issue 的**唯一负责人**,issue 头部呈现 leader 头像 + squad 徽章「X 小队 · leader Y 牵头」)+ **建唯一 active 的 `issue_squad_assignments` 分派行**(返回 `assignment_id`)+ 落库根任务,随后异步入队 leader 运行。小队无 `primary_leader_id` → `422 squad_no_leader`(不改 issue、不建分派行、不建根任务)。**重复派给同一小队 = no-op**(返回既有 `assignment_id` 与根任务);**同 leader 跨 squad 改派永不为 no-op**(判定按分派行而非 assignee 值,旧根任务级联取消,见下例与 §4.4)。客户端用 `status_url` 轮询或 `stream_url`(README §6.8 GET 流)订阅拆解进度。

**同 leader 跨 squad 改派(R2,不为 no-op)** `POST /api/v1/workspaces/{ws}/squads/{squad_id}/tasks`
```json
// 背景:同一 leader mem-leader-01 同时领导 S1(sq-1f3a)与 S2(sq-2b4c);issue i-2201 先派给 S1
//      (active 分派 isa-01 / 根任务 st-root-9001)。现把 i-2201 改派给 S2。
// Request(目标小队 = S2,其 leader 与现任 assignee 相同)
{ "issue_id": "i-2201", "brief": "改由 S2 承接订单结算异步化改造。" }
// 202 Response:判定依据是分派行而非 assignee 值 → 不是 no-op
//   ① 旧分派 isa-01 置 cancelled(cancel_reason='reassigned'),S1 根任务 st-root-9001 及其未完成子任务级联取消;
//   ② 建新 active 分派 isa-02(承接 S2),建新根任务 st-root-9101;
//   ③ issues.assignee_id 值不变(仍 mem-leader-01),但承接小队与根任务已变。
{ "data": {
    "assignment_id": "isa-02", "id": "st-root-9101", "squad_id": "sq-2b4c", "issue_id": "i-2201",
    "parent_task_id": null, "root_task_id": "st-root-9101", "depth": 0,
    "title_snapshot": "订单结算异步化改造", "status": "pending",
    "orchestrator_id": "mem-leader-01",
    "issue_assignee_id": "mem-leader-01",
    "superseded_assignment_id": "isa-01", "superseded_root_task_id": "st-root-9001",
    "status_url": "/api/v1/workspaces/ws-001/squads/sq-2b4c/tasks/st-root-9101/status",
    "stream_url": "/api/v1/workspaces/ws-001/squads/sq-2b4c/tasks/st-root-9101/stream",
    "created_at": "2026-07-24T10:15:00Z" } }
```
> 关键:`issues.assignee_id` 值未变(同 leader),但**改派按分派行判定**——旧分派/旧根任务被取消、新分派建立,因此**不是 no-op**(README §6.9 末段:小队分派不走「assignee 值比较」)。反之,若再次把 i-2201 派给**仍在承接的 S2**(active 分派 isa-02 存在)则为 **no-op**,原样返回 isa-02 与 st-root-9101。

**leader 创建子任务** `POST /api/v1/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/subtasks`
```json
// Request
{ "plan_markdown": "拆为 3 个子任务:先做异步化与幂等(并行),再做对账,最后回归验证(依赖前三者)。",
  "subtasks": [
    {"title": "结算入口异步化 + 幂等键", "assignee": {"member_type": "agent", "member_id": "mem-coder-02"}, "stage": 1, "depends_on": []},
    {"title": "对账批处理任务", "assignee": {"member_type": "agent", "member_id": "mem-coder-02"}, "stage": 1, "depends_on": []},
    {"title": "回归与压测验证", "assignee": {"member_type": "human", "member_id": "mem-li-07"}, "stage": 2,
     "depends_on": ["结算入口异步化 + 幂等键", "对账批处理任务"]}
  ] }
// 201 Response(require_plan_approval=true → 进入待审核)
{ "data": {
    "root_task_id": "st-root-9001", "root_status": "awaiting_plan_approval",
    "created_subtasks": [
      {"id": "st-9002", "title": "结算入口异步化 + 幂等键", "assignee_id": "mem-coder-02", "assignee_type": "agent", "stage": 1, "depth": 1, "status": "pending"},
      {"id": "st-9003", "title": "对账批处理任务", "assignee_id": "mem-coder-02", "assignee_type": "agent", "stage": 1, "depth": 1, "status": "pending"},
      {"id": "st-9004", "title": "回归与压测验证", "assignee_id": "mem-li-07", "assignee_type": "human", "stage": 2, "depth": 1, "status": "pending"}
    ],
    "dependencies": [
      {"task_id": "st-9004", "depends_on_task_id": "st-9002"},
      {"task_id": "st-9004", "depends_on_task_id": "st-9003"}
    ],
    "awaiting_approval": true,
    "approval": {
      "id": "apr-7701", "subject_type": "squad_plan", "subject_task_id": "st-root-9001",
      "action_summary": {
        "plan_digest": "拆为 3 个子任务:异步化+幂等 / 对账 / 回归验证(后者依赖前两者)",
        "impact_scope": "issue i-2201 及其子树;分派给 2 名 agent + 1 名人类成员",
        "subtask_count": 3,
        "expires_at": "2026-07-25T09:06:10Z"
      },
      "status": "pending"
    } } }
```
> `depends_on` 支持引用本批内子任务标题(创建时服务端解析为 id)或 `temp_ref` 临时编号;跨已有任务的依赖直接用 `task_id`。服务端做环检测,越层返回 422 `decompose_depth_exceeded`。
> **计划审批统一(README §6.10)**:`require_plan_approval=true` 时,leader 提交方案即在统一 `approvals` 实体创建一行(`subject_type='squad_plan'`、`subject_task_id=根任务 id`、`requested_by_member_id=leader`),`action_summary` 含**方案摘要 / 影响范围 / 子任务数 / 过期时间**;根任务进 `awaiting_plan_approval`。批准/驳回经 `POST /api/v1/approvals/{id}/approve|reject`(本模块 `plan/approve|reject` 为薄封装);**过期 → 根任务 `failed(approval_expired)` 并通知 leader 与发起人**。

**拆解树** `GET /api/v1/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/tree`
```json
{ "data": {
    "id": "st-root-9001", "title_snapshot": "订单结算异步化改造", "status": "in_progress", "depth": 0,
    "progress": {"total": 3, "done": 1, "in_progress": 1, "pending": 1, "failed": 0},
    "children": [
      {"id": "st-9002", "title_snapshot": "结算入口异步化 + 幂等键", "status": "done",
       "assignee": {"member_type": "agent", "member_id": "mem-coder-02", "name": "coder"}, "stage": 1,
       "result_summary": "已引入消息队列与幂等键,单测通过。", "finished_at": "2026-07-24T11:20:00Z", "children": []},
      {"id": "st-9003", "title_snapshot": "对账批处理任务", "status": "in_progress",
       "assignee": {"member_type": "agent", "member_id": "mem-coder-02", "name": "coder"}, "stage": 1, "children": []},
      {"id": "st-9004", "title_snapshot": "回归与压测验证", "status": "pending",
       "assignee": {"member_type": "human", "member_id": "mem-li-07", "name": "李工"}, "stage": 2,
       "depends_on": ["st-9002", "st-9003"], "blocked_by": ["st-9003"], "children": []}
    ] } }
```
> `blocked_by` 由服务端依据 `squad_task_dependencies` 实时计算:前置未 done 的任务列出阻塞者。

**SSE 流式订阅** `GET /api/v1/workspaces/{ws}/squads/{squad_id}/tasks/{task_id}/stream`(`Accept: text/event-stream`)
```
id: 1
event: task.status
data: {"task_id":"st-root-9001","status":"decomposing","at":"2026-07-24T09:05:03Z"}

id: 2
event: subtask.created
data: {"task_id":"st-9002","title":"结算入口异步化 + 幂等键","assignee_id":"mem-coder-02"}

id: 3
event: task.status
data: {"task_id":"st-root-9001","status":"awaiting_plan_approval","at":"2026-07-24T09:06:10Z"}

id: 4
event: task.status
data: {"task_id":"st-root-9001","status":"done","result_summary":"已完成异步化改造,对账通过,回归无异常。","at":"2026-07-24T15:40:00Z"}
```
> 每个事件带单调递增 `id`(即 seq);客户端断线重连用 `Last-Event-ID` 续订,服务端从事件缓冲重放缺口。事件类型:`task.status` / `subtask.created` / `subtask.assigned` / `plan.submitted` / `task.aggregated`。

**发送小队消息** `POST /api/v1/workspaces/{ws}/squads/{squad_id}/messages`
```json
// Request
{ "task_id": "st-9003", "recipient": {"member_type": "agent", "member_id": "mem-leader-01"},
  "kind": "report",
  "body_markdown": "对账批处理已完成首轮,发现 3 笔差异,详见日志。\n产物: <CI 运行链接>",
  "attachment_ids": ["att-501"] }
// 201 Response
{ "data": {
    "id": "msg-7001", "squad_id": "sq-1f3a", "task_id": "st-9003",
    "sender": {"member_type": "agent", "member_id": "mem-coder-02", "name": "coder"},
    "recipient": {"member_type": "agent", "member_id": "mem-leader-01", "name": "orchestrator"},
    "kind": "report",
    "body_markdown": "对账批处理已完成首轮,发现 3 笔差异,详见日志。\n产物: <CI 运行链接>",
    "body_html": "<p>对账批处理已完成首轮,发现 3 笔差异,详见日志。<br>产物: <a>…</a></p>",
    "pinned": false, "created_at": "2026-07-24T13:10:00Z" } }
```
> 定向消息 `recipient` 为空即广播全队。`kind='instruction'` 且发送者为 leader 时,member 端呈现为"待办指令"并触发对应运行。

### 3.3 错误码表

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 字段缺失/超长/非法(名称为空、role 非法等) |
| 401 | `unauthorized` | 缺失/过期/非法 token |
| 403 | `forbidden` | 无权限(非成员操作小队、跨 workspace 访问、agent 自改所属小队成员) |
| 404 | `not_found` | 小队/任务/成员/消息不存在或已删除 |
| 409 | `conflict` | 非法状态迁移(对已完成任务再分派)、并发更新冲突、归档时仍有运行中任务 |
| 409 | `squad_name_taken` | 同工作区活跃小队重名 |
| 422 | `no_leader` | 变更后小队将没有 leader |
| 422 | `squad_no_leader` | 把 issue 分派给小队时该小队尚无 `primary_leader_id`(独占 assignee 模型无法成立,§1.2 S4) |
| 422 | `approval_expired` | 计划审批已过期(对应根任务 `failed(approval_expired)`,README §6.10) |
| 422 | `decompose_depth_exceeded` | 拆解层级超过 `max_decompose_depth` |
| 422 | `dependency_cycle` | 子任务依赖构成环 |
| 422 | `assignee_not_member` | 分派对象不是当前小队成员 |
| 422 | `member_has_active_task` | 移除的成员仍持有 in_progress 子任务 |
| 429 | `rate_limited` | 触发速率限制(响应含 `Retry-After`) |
| 500 | `internal_error` | 服务端异常(不泄露堆栈) |

### 3.4 分页 / 鉴权 / 限流

- **分页**:游标分页 `?limit=<1..100,默认30>&cursor=<opaque>`,响应 `{"data":[...],"next_cursor"}`;游标内部为 `(created_at, id)` keyset 编码,`next_cursor` 为 null 表示末页。
- **鉴权**:小队读需 workspace 成员且为小队成员/observer 或 admin;小队写(创建/编辑/归档/增删成员)需小队管理权限或 admin;**agent 不能自改自己所属小队的成员构成**(防越权)。给小队分派任务需对目标 issue 有分派权(或 autopilot)。leader 拆解/分派/汇报经 **task broker 持短期 task token 代该 leader 调用**(runtime-executor §2.2/§3.3:`squad.members` 读名册、`squad.subtasks` 提交拆解,仅 `squad_role=orchestrator` 的 attempt 获此 scope;评论/状态经 `/api/v1/task/issues/*`,评论 `suppress_triggers` 防回环),服务端校验"调用 attempt 的 agent 确为该任务的 orchestrator",绝不注入长期 agent PAT。审核方案/取消任务需人类成员、observer 或 admin。
- **限流**:写端点按 principal 限流;agent runtime 的拆解/分派端点单独限流(防 leader 高频刷派),见 auth.md。

### 3.5 实时通道(WebSocket 为主,SSE 用于编排长链路)

**小队级实时(WebSocket)**:连接 `/ws`(握手鉴权见 auth.md),按 `workspace_id + member_id` 订阅;进入小队详情页时再订阅 `squad:{squad_id}` 频道。**实时契约以 README §6.7 为唯一权威**:事件命名 `<entity>.<action>`,携带**频道内**单调递增 `seq`(业务事务内自 `realtime_channels.last_seq` 分配,**非全局 seq**),断线凭 `resume_from=<last_seq+1>` 从 `realtime_events` 重放,游标过旧下发 `resync_required`;订阅时逐资源授权;Redis 仅做 fan-out。

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `squad_message.created` | 消息实时上墙 | `squad_id`, `message_id`, `kind`, `task_id` |
| `squad_task.status_changed` | 子任务状态流转(驱动拆解树/看板刷新) | `task_id`, `old_status`, `new_status` |
| `squad_activity.created` | 时间线增量 | `squad_id`, `action`, `task_id` |
| `squad_member.changed` | 成员/角色变更 | `squad_id`, `member_id`, `role` |
| `squad_assignment.changed` | 小队分派建立/取消/leader 变更(R2,§2.5;README §6.7 注册表) | `issue_id`, `squad_id`, `assignment_id`, `status`, `cancel_reason` |
| `squad.updated` / `squad.archived` | 小队信息/状态变更 | `squad_id` |

**长任务编排流(SSE,README §6.8)**:`GET .../tasks/{task_id}/stream` 专为"拆解—分派—汇总"长链路提供进度流(见 §3.2)。遵循 README §6.8 的「POST 创建 → GET 流」模式:根任务经 `POST .../tasks` 创建并返回 `stream_url`,客户端 `GET` 该 URL(EventSource 兼容)消费流,每事件带 `id`(seq),断线用 `Last-Event-ID` 续订;**浏览器原生 EventSource 不支持 POST SSE**,若客户端改用 fetch streaming(ReadableStream)须自行实现重连与 `Last-Event-ID` 对账。

**心跳与重连**:WebSocket 每 ~25s ping/pong,超时指数退避重连(带抖动);重连后带频道内 `resume_from=<last_seq+1>` 重放缺口(README §6.7),游标过旧收 `resync_required` 走 REST 对账,并对账一次活跃任务与未读。

**降级**:WebSocket 不可用时,长任务进度退化为轮询 `status` 端点(3~5s),消息退化为列表轮询。
> 选型理由:小队消息/状态变更是多向、双向、高频的小队级事件,走常驻 WebSocket;单任务编排进度是"围绕一个资源的单向流",用 SSE 更轻、天然支持断线续订,二者互补。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
小队页(/squads)
   ├── 顶部:[搜索] [形态▾] [状态▾]   [+ 新建小队]
   └── 卡片:头像/名称/形态徽标(常设/临时)/状态点/进行中任务计数/成员头像墙(leader 带(L),人/agent 异图标)
小队详情页(/squads/{id})
   ├── 左:成员区(+ 添加成员)/ 当前任务(进度条 → 任务详情)
   ├── 右上:协作时间线(按任务/成员/action 过滤)
   └── 底部:消息区([全部|指令|汇报|共享上下文],📌 置顶上下文,输入框 @提及/关联任务/附件)
小队任务详情页(/squads/{id}/tasks/{task_id})
   ├── 顶部:状态 + 进度条 + [查看时间线][取消任务];审核横幅(若 awaiting_plan_approval)
   └── [拆解树视图 | 看板视图]
```

### 4.2 关键组件

- **成员头像墙**:每行头像 + 名称 + 角色徽标(组长/成员/观察员);agent 与人类图标区分;hover 出"改角色/移除"。
- **拆解树**:缩进展示父子层级,每节点带状态图标/执行人/阶段/依赖("等待 st-9003")/结果摘要。
- **看板视图**:按子任务状态分列拖拽(人工可改状态);agent 子任务由运行自动流转。
- **审核横幅**:`require_plan_approval` 且处于 `awaiting_plan_approval` 时,顶部高亮"leader 已提交拆解方案,等待审核 [批准] [驳回]",方案 markdown 渲染在侧栏。
- **消息区**:群聊式,按 `kind` 分 tab(指令=蓝、汇报=绿、闲聊=灰、系统=虚线);指令/汇报带"关联任务"标签;顶部固定共享上下文(置顶消息)。
- **创建/编辑小队表单**:名称/描述/头像/形态/组长模式/`require_plan_approval`/最大拆解层级 + 成员混排选人(人或 agent,逐个设角色,至少一名组长否则"创建"置灰)。

### 4.3 关键交互流程

1. **组建小队**:小队页 → 新建 → 填名称/描述/形态 → 添加成员(混排选人与 agent,逐个设角色,至少一名组长)→ 可选开启"拆解需审核"→ 创建。
2. **把大任务交给小队(独占 assignee 模型)**:issue 详情或小分队列 →"分派给小队"→ 服务端在**同一事务**设 `issues.assignee_id = primary_leader_id`(issue 头部呈单一责任主体:leader 头像 + squad 徽章「X 小队 · leader Y 牵头」)+ 建根 squad_task(`pending`)→ 返回 202 与 `status_url`/`stream_url`;**小队无 leader → `422 squad_no_leader`**,分派不成立。
3. **leader 自动拆解**:根任务唤醒 leader agent → `decomposing` → leader 读任务与共享上下文,调 `subtasks` 端点批量建子任务并声明依赖/阶段 → 写 `squad_activity`。
4. **(可选)人类审核**:若开启审核,根任务进 `awaiting_plan_approval`,通知人类 → 人类在任务详情页看方案 → 批准(进分派)或驳回(leader 重拆,可附意见)。
5. **分派与执行**:批准/无需审核后 `dispatching` → 无依赖(stage 1)子任务并行分派:agent member 入队运行、human member 收通知 → 各子任务 `in_progress`;有依赖(stage 2)等前置 done 自动解锁。
6. **协作与汇报**:member 执行中通过小队消息协商/向 leader 汇报(`report`,关联子任务);遇阻置 `blocked` 并通知 leader。
7. **leader 汇总**:某父任务全部直接子任务达终态 → 根任务 `aggregating` → leader 聚合各 `result_summary` 与产物,生成总结回写父 issue → 根任务 `done`(有失败则 `failed`,附原因)。
8. **完成与归档**:发起人收到"小队任务完成"通知;临时小队(adhoc/task_scoped)可在任务完成后自动归档。

### 4.4 状态流转

**根任务 / 编排级状态机**:
```
[*] ──► pending ──leader 接管──► decomposing
decomposing ──提交方案(需审核,建 approvals subject_type='squad_plan')──► awaiting_plan_approval ──批准──► dispatching
decomposing ──无需审核──► dispatching
awaiting_plan_approval ──驳回──► decomposing(重拆);awaiting_plan_approval ──审批过期──► failed(approval_expired)
dispatching ──子任务已分派──► in_progress ──全部子任务终态──► aggregating
in_progress ──关键子任务受阻──► blocked ──解除──► in_progress
aggregating ──汇总成功──► done;aggregating ──存在失败且不可恢复──► failed
pending / decomposing / in_progress ──人为叫停──► cancelled(级联取消子任务)
根任务专属:active 分派行被取消(改派其它小队 / issue 改派给非小队成员)──► cancelled(级联取消子任务,经 issue_squad_assignments 判定,见下)
根任务专属:leader 离队且无替补──► blocked(failure_reason='leader_lost',补上 leader 后解除)
done / failed / cancelled ──► [*]
```
> **计划审批统一(README §6.10)**:`awaiting_plan_approval` ↔ 统一 `approvals` 实体(`subject_type='squad_plan'`,经 `approvals.subject_task_id` 关联根任务);approve/reject 经 `POST /api/v1/approvals/{id}/approve|reject`(`plan/approve|reject` 为薄封装),approval `action_summary` 含方案摘要/影响范围/子任务数/过期时间;**审批过期 → 根任务 `failed(approval_expired)` + 通知 leader 与发起人**(README §9 T8)。
> **leader 触发-评估闭环**:分派唤醒 leader 运行后,leader 先评估任务再决定动作,其运行终态落到时间线 `leader_evaluated`,payload `result` 三值:① `action` —— 已产出拆解(存在子任务),流程继续;② `no_action` —— 评估后认为无需动作(运行成功但零拆解),任务直接 `done`(经 `decomposing → done`,result_summary 记评估结论),走完成回写;③ `failed` —— 评估/拆解运行失败且无产出,任务 `failed`。三值均记 `squad_activity` 供追溯「leader 当时做了什么」。
> **改派级联(经 `issue_squad_assignments` 判定,§1.2 S4 / §2.5,README §6.9 末段)**:小队分派的唯一权威身份是 `issue_squad_assignments` 的 active 行(而非 `issues.assignee_id` 值)。**当该 issue 的 active 分派行被取消时,服务端级联取消对应根 `squad_tasks`**(及其未完成子任务、相关 agent 执行),已完成结果保留。两种触发:① **改派给其它小队**(含同 leader 跨 squad —— 判定按分派行,assignee 值即使不变也**不为 no-op**,旧分派/旧根任务取消、新分派建立);② **issue 被 PATCH 改派给非小队成员**(assignee 离开 active 分派的 leader,`cancel_reason='issue_reassigned'`)。分派建立/取消均广播 **`squad_assignment.changed`**(README §6.7 事件词汇注册表)。
> **leader 更换 / 离队协议(§2.5)**:`squads.primary_leader_id` 变更 → **同事务**把该小队所有 active 分派行的 `leader_member_id` 与对应 `issues.assignee_id` 更新为新 leader + 写 `squad_activity` 审计 + 广播 `squad_assignment.changed`,**根任务不取消**(承接小队未变);leader 离队且无替补 → active 分派保留但根任务置 `blocked`(`failure_reason='leader_lost'`)+ 通知发起人与管理员,补上 leader 后解除 blocked。

**member 子任务状态机**(同一 `status` 枚举,流转更简单):
```
[*] ──► pending ──依赖满足被分派──► dispatching ──member 领取/运行启动──► in_progress
in_progress ──产出待审核(可选)──► (人审)──► done;in_progress ──执行成功──► done
in_progress ──遇阻上报──► blocked ──解除──► in_progress;in_progress ──执行失败──► failed
pending / dispatching ──父任务取消──► cancelled
```
> 状态迁移集中在服务端校验,非法迁移返回 409 `conflict`。前置任务 done 时,服务端扫描 `squad_task_dependencies.depends_on_task_id` 找出被解锁的子任务,若其全部前置已 done 且 stage 允许,则自动 `pending → dispatching`。

**与全系统统一长任务状态机的衔接(README §6.4)**:子任务分派给 agent member 时,服务端经 transactional outbox(README §6.6)入队一条逻辑执行 `task_executions`(经 `squad_tasks.execution_id` 复合关联),其生命周期遵循 README §6.4 统一词汇 `queued→claimed→running→completed|failed|timeout|cancelled`(runtime.md 内部含租约/心跳/requeue/cancelling/awaiting_approval 等恢复/审批态)。squad_task 观察该执行的终态:execution `completed` → 子任务 `done`;execution `failed`/`timeout` → 子任务 `failed`(写 `failure_reason`)。**squad_task 的编排状态机是上层,`task_executions` 是下层执行真源,二者经 `execution_id` 对齐**。

### 4.5 实时性与通知

- **被分派子任务 → 通知 member**:agent member 入队运行(不依赖通知);human member 进收件箱 + 可选邮件/站内推送,含任务标题、来源小队、跳转链接。
- **汇总完成 → 通知发起人**:根任务 `done`/`failed` 时通知原始分派人,附 `result_summary` 与跳转。
- **方案待审核 → 通知审核人**:`awaiting_plan_approval` 时通知人类成员/observer 审批。
- **受阻/失败 → 通知 leader 与发起人**:子任务 `blocked`/`failed` 时上报。
- **去噪与回环抑制**:动作发起者不给自己发通知;agent 之间的指令/汇报不触发"会再次唤醒自身"的通知,防 leader↔member 死循环(沿用 comment-inbox 的回环抑制原则,见 §5.3)。
- **通知偏好**:复用通知偏好矩阵,新增事件类型 `squad_task_assigned` / `squad_task_finished` / `squad_plan_review`,可分别开关站内/邮件。

### 4.6 归档导出(检查表 L486)

> 锚点勘误:parity 检查表 L486 原指向 §4.5,而 §4.5 主题为实时性与通知;导出要求登记于本节(§4.6)。

- **能力**:小队页「⋯」菜单提供「导出归档」,产出一份 markdown 文档,内容按序为:小队头部信息(名称/ID/状态/形态/描述/Leader/成员数/导出时间 UTC)→ 任务清单(每任务状态、关联 Issue、执行者、起止时间、失败原因、结果摘要)→ 任务消息(逐条时间戳 + kind 标签(指令/汇报/闲聊/系统/上下文)+ 收发者 + 关联任务 tag,正文以 markdown 引用块直嵌)→ 协作时间线(actor/action/target/任务)。
- **语义**:导出是**只读文档快照**,不含可执行内容;消息正文已是 markdown 源(`body_markdown` 为落库真源),逐字嵌入不做 HTML 渲染。时间戳一律保留 UTC 原值(§6.18)。
- **鉴权**:与小队读同口径(小队成员 / observer / admin);响应 `Content-Disposition: attachment; filename="squad-{squad_id}.md"`。
- **定位**:结项归档(S1)之外的离线留档手段;归档(软解散)不依赖导出,导出亦不改变任何状态。

---

## 5. 验收标准

### 5.1 功能性

- [ ] 小队为编排单元:`squad_tasks`(编排层)与 `issues`(内容层)解耦互链(经 `issue_id`);issue 管内容,squad_task 管层级/依赖/分派/状态机。
- [ ] 拆解树用 `parent_task_id`/`root_task_id` 表达父子(冗余 root 加速整树聚合);依赖用独立 `squad_task_dependencies` 表(非 JSONB);`stage` 为粗粒度并行批次。
- [ ] **把任务交给小队(独占 assignee 模型 + 唯一 active 分派行,§1.2 S4 / §2.5)**:同事务设 `issues.assignee_id = squads.primary_leader_id`(leader 成员成为 issue **唯一负责人**,issue 头部单一责任主体:leader 头像 + squad 徽章「X 小队 · leader Y 牵头」)+ **建唯一 active 的 `issue_squad_assignments` 分派行** + 建根任务(`root_task_id` 双向回填),返回 202 + `assignment_id` + `status_url`/`stream_url`;leader 异步唤醒拆解。**小队无 leader → `422 squad_no_leader`**(不改 issue、不建分派行、不建根任务)。
- [ ] **改派级联(R2:按分派行判定,T23)**:把已被小队承接的 issue 改派给他人/其它小队(active 分派行被取消)→ 原小队根任务及其未完成子任务级联 `cancelled`,相关 agent 执行终止,已完成结果保留。**同 leader 跨 squad 改派不为 no-op**(assignee 值即使不变,旧分派/旧根任务取消、新分派建立);issue 被 PATCH 改派给非小队成员同样取消 active 分派(`cancel_reason='issue_reassigned'`)。
- [ ] **小队 active assignment 唯一身份(R2,README §9 T23)**:① `uq_issue_squad_active` 部分唯一索引保证**每 issue 至多一条 active 分派**,并发双派恰一条成功;② **重复派给同一小队 = no-op**(返回既有分派与根任务);③ **同 leader 跨 squad 改派**取消旧根任务、建新分派(不为 no-op);④ **leader 更换** → active 分派行 `leader_member_id` 与 `issues.assignee_id` 同事务更新为新 leader + 广播 `squad_assignment.changed`,根任务不取消;⑤ **leader 离队且无替补** → 根任务 `blocked`(`failure_reason='leader_lost'`)并通知发起人/管理员,补上 leader 后解除;⑥ active→cancelled/completed 的**历史分派行永久保留**,供时间线展示「曾由 X 小队承接」。
- [ ] 子任务批量创建支持 `depends_on`(本批标题/`temp_ref`/既有 `task_id`);服务端环检测,成环返回 422 `dependency_cycle`;越层返回 422 `decompose_depth_exceeded`。
- [ ] 前置任务 done 时自动解锁后置子任务(全部前置 done 且 stage 允许 → `pending→dispatching`)。
- [ ] **计划审批统一(README §6.10 / §9 T8)**:`require_plan_approval=true` 时拆解后在统一 `approvals` 建 `subject_type='squad_plan'` 行(经 `approvals.subject_task_id` **复合 FK `(workspace_id, subject_task_id) → squad_tasks(workspace_id, id)`** 关联根任务,R2 已升为物理复合 FK),根任务停在 `awaiting_plan_approval`;**同一根任务仅一个 pending 审批**(README §6.10 部分唯一索引 `uq_approvals_pending_task`,重复发起取既有 pending 返回);`plan/approve|reject` 为 `POST /approvals/{id}/approve|reject` 的薄封装,批准进分派、驳回 leader 重拆;approval `action_summary` 含方案摘要/影响范围/子任务数/过期时间;**审批过期 → 根任务 `failed(approval_expired)` + 通知**,过期后 approve → no-op/`410`。
- [ ] 每个 active 小队至少一个 leader;变更后无 leader 返回 422 `no_leader`;分派非成员返回 422 `assignee_not_member`。
- [ ] **leader 触发-评估闭环**:orchestrator 运行终态记 `leader_evaluated` 时间线(result ∈ action/no_action/failed);`no_action`(成功零拆解)→ 任务经 `decomposing → done` 完成并回写;`failed`(失败零拆解)→ 任务 failed。
- [ ] **leader 汇总(§S8)**:全部直接子任务终态 → 父任务 `aggregating`;**agent leader 先经一次 summary 运行**(`squad_role='aggregator'` 唤醒)再结算,人类 leader 同步结算;根任务 `done` 后其 result_summary 由 leader **回写父 issue**(系统经 leader 身份发评论,幂等);父任务非根则继续向上聚合。
- [ ] 取消级联取消未完成子任务、终止相关 agent 执行(`task_executions`),已完成结果保留。
- [ ] **执行词汇对齐(README §6.4)**:agent 成员执行落地为 `task_executions`(经 `squad_tasks.execution_id` 复合关联),状态遵循全系统统一词汇 `queued→claimed→running→completed|failed|timeout|cancelled`;squad_task 观察 execution 终态映射 `done`/`failed`。
- [ ] **多租户复合 FK(README §6.2 / §9 T1)**:`squads`/`squad_tasks` 建 `UNIQUE(workspace_id, id)`;member FK(`member_id`/`primary_leader_id`/`creator_id`/`orchestrator_id`/`assignee_id`/`sender_id`/`recipient_id`/`actor_id`/`added_by_id`)→ `members(workspace_id,id)`、`squad_id` → `squads(workspace_id,id)`、`issue_id` → `issues(workspace_id,id)`、parent/root/依赖 → `squad_tasks(workspace_id,id)` 均为复合 FK;构造跨 workspace 复合 FK 插入被数据库约束拒绝。
- [ ] **存储层无 `*_type`/`*_kind` 判别列(README §6.1)**:`creator_type`/`member_type`/`orchestrator_type`/`assignee_type`/`sender_type`/`recipient_type`/`actor_type` 均不进表;人类/agent 判别一律 JOIN `members.member_type`,API 响应 `member_type` 为计算快照;系统主体用 `squad_messages.sender_id NULL(kind='system')` 与 `squad_activity.actor_kind IN('member','system')` + `actor_id NULL`。
- [ ] 临时小队(adhoc/task_scoped)任务完成后可自动归档;归档保留全部历史(成员/任务/消息/时间线),不做物理删除。
- [ ] 协作时间线只增不改,`payload` 渲染快照在实体被删后仍可读;支持按任务/成员/action 过滤。

### 5.2 性能

- [ ] 小队任务列表/看板(万级)P95 < 200ms(命中 `idx_squad_tasks_squad`);拆解树拉取走 `idx_squad_tasks_tree`。
- [ ] 前置完成解锁扫描走 `idx_dep_blocker`,无全表扫描;活跃任务校验走部分索引 `idx_squad_tasks_active`。
- [ ] 拆解树/汇总进度查询 P95 < 150ms;游标分页在百万级行下稳定(无 OFFSET 深翻页)。

### 5.3 安全

- [ ] **运行中改成员护栏**:不可移除持有 in_progress 子任务的 member(返回 422 `member_has_active_task`);新增 member 仅对后续分派生效,不追溯改写已分派任务。
- [ ] **分派并发(R2,§2.5)**:同一 issue 的并发双派由 `uq_issue_squad_active` 部分唯一索引兜底——**恰一条 active 分派成功**,另一条被唯一约束拒绝(重试或返回 `409 conflict`),数据库层杜绝"一 issue 两条 active 分派"。
- [ ] **防越权**:agent 不能自改所属小队成员构成(返回 403);leader 拆解/分派端点校验"调用 agent 确为该任务 orchestrator"(非该任务 orchestrator 且非 admin/owner 调用 `subtasks`/`dispatch` 返回 403,仅有 workspace 级 `agent:trigger` 权限不足)。
- [ ] **防回环**:leader↔member 的指令/汇报抑制"触发自身再运行"的通知;多 leader 模式设主 leader 收口汇总,避免分派冲突;默认推荐单 leader。
- [ ] **频率上限**:agent runtime 的拆解/分派/汇报端点受限流约束,超限 429 + `Retry-After`,防 leader 高频刷派。
- [ ] 状态迁移服务端集中校验,非法迁移返回 409;状态切换/分派/角色变更/叫停均写 auth.md 的 append-only 审计日志(行为主体经 `actor_kind ∈ {member,system}` + `actor_id → members.id` 记录,人/agent 由 JOIN `members.member_type` 解析,**不存 human/agent 判别列**)。
- [ ] 跨 workspace 访问返回 404(不泄露存在性);所有查询隐式带 `workspace_id`。

### 5.4 实时

- [ ] 小队级事件(`squad_message.created`/`squad_task.status_changed`/`squad_activity.created`/`squad_member.changed`)经 WebSocket 实时推送(带 `seq`),在线成员 1s 内收到。
- [ ] 编排长链路经 SSE `stream` 推送 `task.status`/`subtask.created`/`plan.submitted`/`task.aggregated`,断线凭 `Last-Event-ID` 续订。
- [ ] 客户端断线重连凭 `seq` 重放缺口,并对账活跃任务与未读,无丢失无重复。
- [ ] WebSocket 不可用时,长任务进度降级轮询 `status`(3~5s)、消息降级列表轮询,功能等价。
