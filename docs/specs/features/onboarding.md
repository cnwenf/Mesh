# 上手引导(Onboarding)功能 Spec

> **所属层**:平台能力层(首次使用引导,贯穿基础层与协作/智能体层的横切体验)。
> **依赖 Spec**:`workspace.md`(工作区创建向导、邀请面板,`workspaces`/`workspace_invitations`)、`member.md`(统一名册 `members`,README §6.1)、`agent.md`(agent 四步创建向导,激活路径中「加 agent」)、`issue.md`(首个 issue 创建)、`comment-inbox.md`(agent 回评与收件箱,aha moment 的观测面)、`runtime.md`(`task_executions` 为运行唯一真源,README §6.4;runtime 注册页)、`skill.md`(技能导入向导)、`auth.md`(鉴权/限流/审计)。
> **被依赖**:无(本模块为只读消费方,不被其它模块依赖)。
> **技术栈基准**:FastAPI + SQLAlchemy 2.x + PostgreSQL 16 + WebSocket。
> **文档性质**:可直接指导开发的实现规格。所有命名、约束、端点、事件均以此为准则;与全局约定冲突时以 [README.md](../README.md) §6「全局权威契约」为准。

---

## 全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)

1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `UUID`(`gen_random_uuid()`);所有表含 `created_at` / `updated_at`(`TIMESTAMPTZ`,默认 `now()`,UTC)。
2. **成员**:成员模型以 **README §6.1** 为唯一权威——统一 `members` 名册(`member_type='human'|'agent'`);上手清单归属人 `member_id` 一律引用 `members.id`;**存储层不设 `*_type`/`*_kind` 判别列**,人类/agent 判别一律 JOIN `members.member_type`。
3. **多租户**:跨模块外键一律按 **README §6.2** 建复合 FK `(workspace_id, x_id) → 目标表 (workspace_id, id)`;`onboarding_states.member_id` 以复合 FK 引用 `members(workspace_id, id)`,跨租户引用在 INSERT 时即被数据库拒绝。
4. **接口**:基础路径 `/api/v1`;包络 / 分页 / 错误信封 / 幂等写 / 过滤限制见 **README §6.14**(成功响应单对象 `{"data":{...}}`、列表 `{"data":[...],"next_cursor"}`)。
5. **实时**:统一实时契约见 **README §6.7**(频道内单调 `seq`、`realtime_events` 持久重放、`resume_from` / `resync_required`);本模块事件名 `onboarding.progress` / `onboarding.completed` **已在 README §6.7 事件词汇注册表登记**,本 Spec 仅引用,不另立事件名。
6. **队列 / 投递 / 自动完成**:步骤的**自动完成由领域事件驱动**——业务模块(comment-inbox / agent / runtime / issue / member)在业务事务内写 `outbox_events`(README §6.6),本模块的 outbox 消费者以 at-least-once 语义消费相关事件并幂等标记步骤完成;**禁止**在业务事务外旁路写状态,亦**不**直接写 `realtime_events`(实时事件经 outbox `realtime.publish` 由 realtime projector 统一登记,README §6.6/§6.7)。
7. **触发语义**:激活路径「分派 / @ 触发首个运行」的判定以 **README §6.9** 触发矩阵为唯一权威(`trigger ∈ ('assign','mention')`)。
8. **空状态基线**:核心页面异常态矩阵(loading / empty / permission denied / offline / stale / retry)与键盘快捷键体系以 **README §6.12** 为唯一权威;本 Spec 的空状态规范是其在上手引导场景下的**延伸与实例化**,不重复定义异常态语义。
9. **ORM**:SQLAlchemy 2.x 声明式约定(`Mapped` / `mapped_column`,异步会话)。

---

## 1. 功能描述

### 1.1 模块定位

上手引导(Onboarding)是 Mesh 的**首次使用引导能力**:把「新用户进入一个空工作区,不知从何下手」转化为一条清晰、可度量、最终触达 **aha moment** 的激活路径。

Mesh 的核心价值在「AI agent 作为一等队友被分派任务并回评结果」(README §1)。**只有当用户亲眼在收件箱看到 agent 针对自己分派的任务回评的那一刻,产品价值才被感知**——这就是本模块定义的 aha moment。上手引导的一切设计(清单、空状态、深链)都服务于把用户高效推到这一刻,而非堆砌功能教学。

模块包含三块相互联动的能力:

1. **上手清单数据模型与进度持久化**(`onboarding_states` + `onboarding_state_steps`):member × workspace × checklist 维度的进度真源,步骤级状态(`pending`/`completed`/`skipped`)与完成时间持久化;进度既可由用户手动标记,更由**领域事件自动检测**推进(以真实发生的业务事实为准,而非仅 UI 点击)。
2. **Mesh 激活路径(aha moment 清单)**:内置 `activation` 清单,五步对应用户从建区到见到 agent 回评的最短价值路径,每步标注**自动检测方式**。
3. **成体系空状态规范 + 深链既有向导**:每个核心页面的空状态(插画 + 引导文案 + 主操作按钮)深链到对应的**既有**创建向导(agent 四步向导、runtime 注册页、skill 导入、workspace 创建、invite 面板),**不重复造向导**;空状态主操作完成即推进对应清单步骤,清单与空状态双向联动。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| O1 | 清单进度持久化 | 每成员每工作区每清单一主记录 + 步骤明细子表;跨会话/跨设备保持进度 | 昨天建了区没继续,今天回来清单仍停在「邀请成员」步 |
| O2 | 激活路径清单 | 内置 `activation` 五步清单,进度条 + 逐步勾选 | 新用户照着清单一步步把团队跑起来 |
| O3 | 步骤自动检测 | 由 outbox 领域事件消费驱动,真实业务事实即标记完成 | 用户没点清单,但分派了 agent,「触发首个运行」自动勾选 |
| O4 | 步骤手动完成 | 用户/管理员可手动标记某步完成,幂等 | 线下已邀请同事,手动勾选「邀请成员」 |
| O5 | 整体关闭(dismiss) | 用户可整体关闭清单,幂等;可从帮助菜单恢复 | 老用户不需要引导,一键收起 |
| O6 | aha moment 庆祝态 | 末步达成时置 `aha_reached_at`,UI 呈现庆祝反馈 | 收件箱第一次出现 agent 回评,清单弹出庆祝 |
| O7 | 管理员重置 | admin/owner 可重置某成员清单进度 | 误操作 dismiss 后由管理员恢复 |
| O8 | 成体系空状态 | 每核心页面空状态 = 插画 + 文案 + 主操作 + 深链既有向导 | 空的看板给「新建 issue」按钮,空的成员页给「邀请/加 agent」入口 |
| O9 | 空状态↔清单联动 | 空状态主操作完成即推进对应步骤 | 在空成员页点了「加 agent」并完成,清单第 2 步自动完成 |

#### 1.2.1 激活路径(aha moment 清单 `activation`)

| 序 | step_key | 步骤 | 自动检测方式(领域事件驱动,非仅 UI 点击) | 深链既有向导 |
|----|----------|------|--------------------------------------------|----------------|
| 1 | `create_workspace` | 创建工作区 | 清单记录创建时即检查:当前成员隶属的工作区已存在(`members` 行存在)→ 建区者与被邀请入册者该步均直接 `completed`(工作区既已存在) | workspace.md 创建向导(`/w/{ws}/settings`,§4.2) |
| 2 | `invite_member_or_add_agent` | 邀请成员或添加 agent | 消费 `member.added`(README §6.7):工作区名册出现 `member_type='agent'` 成员,或 `member_type='human'` 成员数 ≥ 2 → 对工作区内所有未完成本步骤的清单标记 `completed` | workspace.md 邀请面板 + agent.md 四步创建向导(从成员名册页或设置→Agents「+ 新建」进入;agent 无独立名册路由,README §6.12;详情深链 `/w/{ws}/agents/{id}`) |
| 3 | `create_first_issue` | 创建首个 issue | 消费 `issue.created`:该工作区 `issues` 计数从 0→1(工作区首个 issue)→ 对工作区内所有未完成本步骤的清单标记 `completed`;若 `reporter_id` 为某清单成员,则对其即时标记 | issue.md 新建 issue(看板/收件箱空状态主操作,README §6.12 快捷键 `C`) |
| 4 | `dispatch_or_mention_agent` | 分派 / @ 触发首个运行 | 消费 `execution.queued`(README §6.7):工作区出现首个 `task_executions` 且 `trigger ∈ ('assign','mention')`(README §6.9)→ 对相关清单标记 `completed` | issue.md 分派 assignee / comment-inbox.md @提及 composer(README §6.9 trigger preview) |
| 5 | `see_agent_reply_in_inbox` | 收件箱见 agent 回评(aha moment) | 消费 `execution.completed` + `comment.created`(README §6.7):上述首个执行进入 `completed` **且** issue 评论区存在该 agent 的评论(`comments.author_id` 为该 agent 的 `members.id`,JOIN `members.member_type='agent'`)→ 标记 `completed` 并置 `aha_reached_at`(README §6.7 唯一通知优先级见 §6.13) | comment-inbox.md 收件箱(`/w/{ws}` 收件箱入口,README §6.12) |

> 检测一律以 outbox 领域事件为准(README §6.6),at-least-once 消费 + 步骤完成幂等(§3.5);**不**依赖 UI 点击上报。「首个」的判定以工作区为作用域,按 `created_at` 取最早一行。

**深链既有向导目录(不重复造向导)**:激活路径每步 CTA 与各页面空状态主操作,均深链到 Mesh **既有**的创建/注册入口,本模块不另建向导:

| 既有向导 | 归属 Spec | 入口 | 在激活路径中的 surfaced 时机 |
|----------|-----------|------|------------------------------|
| 工作区创建向导 | workspace.md §4.2 | 工作区切换器「新建」 | 步骤 1 |
| 邀请面板 | workspace.md §4.2 | 设置→邀请 / 成员页「邀请成员」 | 步骤 2 |
| agent 四步创建向导(基本信息 → 模型与指令 → 技能与工具 → 可见性) | agent.md §4.4 | 成员名册页 / 设置→Agents「+ 新建」 | 步骤 2 |
| runtime 注册引导页(基本信息 → 安装命令 → 等待 `runtime.activated`) | runtime.md §4.3 | 自动化→Runtimes「+ 新增 runtime」 | 步骤 2/4 的上下文化次级 CTA:agent 需匹配 runtime 方能领取执行(README §6.4);agent 无可用 runtime 时,分派提示「无匹配 runtime」并深链此页(README §6.12 专项恢复入口) |
| skill 导入向导(选择来源 → 预览校验 → 安装) | skill.md §4.1 | 自动化→Skills(`/skills`)「⇩ 导入」 | 步骤 2 的上下文化次级 CTA:agent 四步向导第 ③ 步「技能与工具」可深链导入技能(可跳过) |

> runtime 注册页与 skill 导入向导**不构成激活清单的独立步骤**(YAGNI,清单只保留五步主路径),而是作为步骤 2/4 的**上下文化次级 CTA** 在需要时浮现(如分派时无匹配 runtime、配置 agent 技能时),其交互与数据模型一律归各自 Spec。

#### 1.2.2 成体系空状态规范(README §6.12 异常态矩阵的延伸)

每个核心页面的 `empty` 态统一由四要素构成,并深链到既有向导;空状态是 onboarding 的延伸面,与清单共用同一套步骤推进逻辑:

| 页面 | 空状态插画主题 | 引导文案(i18n key,i18n.md) | 主操作按钮 | 深链既有向导 / 推进步骤 |
|------|----------------|-------------------------------|--------------|---------------------------|
| 收件箱 | 空收件托盘 | 「这里会汇集 @ 你、分派给你与 agent 的回评」 | 查看 issue | comment-inbox.md 收件箱;关联步骤 5 |
| 项目 | 空文件夹 | 「用项目聚合一组相关 issue」 | 新建项目 | project.md 创建向导 |
| 看板 / 视图 | 空看板列 | 「看板是 issue 的可视化投影,拖拽即流转状态」 | 新建 issue | issue.md / kanban.md;推进步骤 3 |
| 成员 | 空名册 | 「邀请人类同事,或添加 AI agent 作为队友」 | 邀请成员 / 添加 agent | workspace.md 邀请面板 + agent.md 四步向导;推进步骤 2 |
| 聊天 | 空会话列表 | 「选一个 agent 开聊,流式输出随问随答」 | 开始对话 | chat-session.md 发起会话 |
| 自动化 | 空 autopilot 列表 | 「让 autopilot 定时或按事件自动把工作派给 agent」 | 新建 autopilot | autopilot.md 创建向导 |

> 四要素(插画 / 文案 / 主操作 / 深链)与 README §6.12 `empty` 态「空态插画 + 主操作」一致并细化;loading / permission denied / offline / stale / retry 等其余异常态语义不在此重复,一律遵循 README §6.12。空状态主操作完成后,前端乐观推进对应清单步骤,服务端以领域事件复核(§1.2.1)。

### 1.3 边界与非目标(明确不做什么)

- **不**重复实现任何被深链的创建向导——workspace 创建、邀请面板、agent 四步向导、runtime 注册页、skill 导入、autopilot 创建均归各自 Spec,本模块仅**深链引用**与**消费其领域事件**。
- **不**定义成员/agent/issue/评论/执行的数据模型与端点——归 `member.md` / `agent.md` / `issue.md` / `comment-inbox.md` / `runtime.md`(本模块仅消费其 outbox 事件)。
- **不**另立实时事件名——`onboarding.progress` / `onboarding.completed` 已在 README §6.7 注册表登记,本 Spec 仅引用。
- **不**定义通知分级与收件箱投递规则——归 README §6.13 / comment-inbox.md(aha moment 的通知呈现遵循唯一通知优先级矩阵)。
- **不**做产品级功能教学/交互式步骤高亮 tour(YAGNI;本模块只做清单 + 空状态 + 深链,不做遮罩式新手 tour)。
- **不**支持多套并行的自定义清单编排(YAGNI;仅内置 `activation` 一套,`checklist` 列预留扩展)。
- **不**统计激活漏斗转化率报表——归 `analytics.md`(本模块只持久化进度真源,不出报表)。

---

## 2. 数据模型

> **全局契约引用**:本模块的 schema、同租户约束、成员模型、实时、API 包络/错误/分页一律以 [README.md](../README.md) §6「全局权威契约」为准,本 Spec 仅引用、不重复定义(成员模型 README §6.1、同租户复合 FK README §6.2、实时 README §6.7、API/错误/分页 README §6.14)。

### 2.1 ER 概览(文字图)

```
workspaces(workspace.md)──1:N──┐
                               ├──► members(member.md,README §6.1,统一名册)
                               │           ▲
                               │           └─ member_id 为清单归属键
                               ▼ N
                        onboarding_states(本 Spec owns,member×workspace×checklist 进度主记录)
                               │ 1
                               ▼ N
                        onboarding_state_steps(本 Spec owns,步骤明细子表:step_key/status/completed_at)

自动完成来源(只读消费,不持有其 FK):
   outbox_events(README §6.6)── member.added / issue.created / execution.queued /
                                  execution.completed / comment.created ──► 本模块消费者幂等标记步骤完成
```

要点:
- `onboarding_states` 是**每成员每工作区每清单一行**的进度主记录;步骤明细落在子表 `onboarding_state_steps`(每步一行),便于步骤级索引、自动检测的精准 UPDATE 与审计。
- 选择**步骤子表**而非单一 `state JSONB`:自动完成由领域事件按 `step_key` 精准更新单行,子表可加部分索引(如「工作区内未完成步骤」)并天然支持 `completed_at` 逐步留痕;JSONB 整体改写不利于并发自动检测与逐步审计。
- 本模块**不持有**任何业务实体(issue/agent/execution/comment)的外键——自动完成通过消费 outbox 事件 + 工作区作用域查询实现,跨模块仅以 `workspace_id` / `member_id` 复合 FK 关联租户与成员。

### 2.2 表:`onboarding_states`(上手清单主记录)

> 本表由本 Spec owns。SQLAlchemy 2.x 声明式约定;字段名 snake_case。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK;`UNIQUE (workspace_id, id)`(供步骤子表复合 FK 引用,README §6.2) | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | 所属工作区(隔离根) |
| `member_id` | UUID | NOT NULL,**复合 FK `(workspace_id, member_id) → members(workspace_id, id)` ON DELETE CASCADE** | — | 清单归属成员(`member_type='human'`;README §6.1/§6.2) |
| `checklist` | TEXT | NOT NULL,CHECK (char_length BETWEEN 1 AND 40) | `'activation'` | 清单标识(内置 `activation`;预留扩展) |
| `aha_reached_at` | TIMESTAMPTZ | NULL | NULL | aha moment 达成时间(末步 `see_agent_reply_in_inbox` 完成时置位,且仅置一次) |
| `dismissed_at` | TIMESTAMPTZ | NULL | NULL | 整体关闭时间(NULL=未关闭;dismiss 幂等,见 §3.5) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | 触发器自动维护 |

**表级约束**:`UNIQUE (workspace_id, member_id, checklist)` —— 每成员每工作区每清单至多一条主记录(幂等创建/获取的数据库基础,§3.5)。

### 2.3 表:`onboarding_state_steps`(步骤明细子表)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK;`UNIQUE (workspace_id, id)`(README §6.2) | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | 所属工作区(冗余以满足复合 FK 同租户,README §6.2) |
| `state_id` | UUID | NOT NULL,**复合 FK `(workspace_id, state_id) → onboarding_states(workspace_id, id)` ON DELETE CASCADE** | — | 所属清单主记录(README §6.2) |
| `step_key` | TEXT | NOT NULL,CHECK IN ('create_workspace','invite_member_or_add_agent','create_first_issue','dispatch_or_mention_agent','see_agent_reply_in_inbox') | — | 步骤标识(激活路径五步,§1.2.1) |
| `status` | TEXT | NOT NULL,CHECK IN ('pending','completed','skipped') | `'pending'` | 步骤状态 |
| `completed_via` | TEXT | NULL,CHECK IN ('auto','manual') | NULL | 完成来源:`auto`=领域事件自动检测,`manual`=用户/管理员手动标记(`status != 'completed'` 时为 NULL) |
| `completed_at` | TIMESTAMPTZ | NULL | NULL | 完成时间(`status='completed'` 时非空) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**表级约束**:
- `UNIQUE (workspace_id, state_id, step_key)` —— 每条清单每个步骤至多一行。
- `CHECK ((status = 'completed') = (completed_at IS NOT NULL))` —— 完成态与完成时间一致(完成必有时间,未完成必无时间)。

> 步骤行在清单主记录创建时**同事务批量播种**五步(默认 `pending`,§3.5);`create_workspace` 步对建区者/入册者在建表事务内即置 `completed(completed_via='auto')`(工作区既已存在,§1.2.1)。

### 2.4 索引与约束

```sql
-- 主记录:每成员每工作区每清单唯一(幂等创建/获取基础);供步骤子表复合 FK 引用
CREATE UNIQUE INDEX uq_onboarding_states_ws_member_checklist
  ON onboarding_states(workspace_id, member_id, checklist);
CREATE UNIQUE INDEX uq_onboarding_states_ws_id
  ON onboarding_states(workspace_id, id);
-- 管理员重置/统计:按工作区检索未达成 aha 的清单
CREATE INDEX idx_onboarding_states_ws_aha
  ON onboarding_states(workspace_id, created_at) WHERE aha_reached_at IS NULL;

-- 步骤子表:供复合 FK 引用;每清单一行一步骤;
CREATE UNIQUE INDEX uq_onboarding_steps_ws_state_step
  ON onboarding_state_steps(workspace_id, state_id, step_key);
CREATE UNIQUE INDEX uq_onboarding_steps_ws_id
  ON onboarding_state_steps(workspace_id, id);
-- 自动检测:定位工作区内某步骤未完成的清单(领域事件消费时的精准 UPDATE 范围)
CREATE INDEX idx_onboarding_steps_pending
  ON onboarding_state_steps(workspace_id, step_key) WHERE status <> 'completed';
```

应用层 CHECK:步骤完成的状态迁移守卫(`pending → completed` / `pending → skipped` 单向;`completed`/`skipped` 不再回退,重置走 §3.4 整体重建),由服务层在事务内以条件 UPDATE 保证(§3.5),并配集成测试覆盖。

### 2.5 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `onboarding_states.workspace_id` / `onboarding_state_steps.workspace_id` | → `workspaces.id` | workspace.md | 隔离(ON DELETE CASCADE) |
| `onboarding_states.(workspace_id, member_id)` | → `members(workspace_id, id)` | member.md(README §6.1/§6.2) | 清单归属成员(复合 FK,ON DELETE CASCADE;成员物理清理后其清单一并清除) |
| `onboarding_state_steps.(workspace_id, state_id)` | → `onboarding_states(workspace_id, id)` | 本 Spec | 步骤归属清单(复合 FK,ON DELETE CASCADE,引用 `uq_onboarding_states_ws_id`) |

> 本模块**不持有** issue/agent/task_executions/comments 的外键:自动完成通过消费 outbox 事件 + 工作区作用域查询完成(§3.6),跨模块语义以 README §6.x 为准。跨租户隔离与复合 FK 的权威定义见 README §6.2。

---

## 3. 接口设计

REST 基础路径 `/api/v1`;鉴权 `Authorization: Bearer <token>`(会话 JWT 或 API token,见 auth.md)。时间一律 RFC3339 UTC,id 均为 UUID。**成功包络、游标分页、错误信封、HTTP 语义、幂等写一律以 README §6.14 为权威**(单对象 `{"data":{...}}`,列表 `{"data":[...],"next_cursor"}`),本 Spec 仅列模块专属端点与错误码,不重复定义公共契约。

> **工作区定位**:一个自然人可属于多个工作区(README §6.1),成员自助端点(state / steps / dismiss)以 **`?workspace_id=<uuid>`** 查询参数指定当前工作区(必填,服务端校验该 principal 对其的成员资格);管理员重置端点显式嵌套于 `/workspaces/{ws}/` 路径。

### 3.1 REST 端点清单

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/onboarding/state?workspace_id=` | 获取当前成员在该工作区的清单进度(主记录 + 全部步骤) | 成员(仅本人) |
| POST | `/onboarding/steps/{step_key}/complete?workspace_id=` | 手动完成某步(幂等) | 成员(仅本人) |
| POST | `/onboarding/dismiss?workspace_id=` | 整体关闭清单(幂等) | 成员(仅本人) |
| POST | `/onboarding/restore?workspace_id=` | 恢复已关闭的清单(清除 `dismissed_at`,幂等) | 成员(仅本人) |
| POST | `/workspaces/{ws}/onboarding/reset` | 管理员重置某成员清单(删除主记录与步骤并重建,`member_id` 于请求体) | admin |

> 清单主记录**惰性创建**:首次 `GET /onboarding/state` 即按 `UNIQUE(workspace_id, member_id, checklist)` 幂等播种主记录 + 五步(§3.5),无独立「创建清单」端点。

### 3.2 请求/响应 JSON 示例

**获取清单进度** `GET /api/v1/onboarding/state?workspace_id=ws-001`
```json
{ "data": {
    "id": "obs-7a2c...",
    "workspace_id": "ws-001",
    "member_id": "mem-1111...",
    "checklist": "activation",
    "aha_reached_at": null,
    "dismissed_at": null,
    "progress": { "total": 5, "completed": 2, "skipped": 0 },
    "steps": [
      { "step_key": "create_workspace", "status": "completed",
        "completed_via": "auto", "completed_at": "2026-07-24T10:00:00Z" },
      { "step_key": "invite_member_or_add_agent", "status": "completed",
        "completed_via": "auto", "completed_at": "2026-07-24T10:12:33Z" },
      { "step_key": "create_first_issue", "status": "pending",
        "completed_via": null, "completed_at": null },
      { "step_key": "dispatch_or_mention_agent", "status": "pending",
        "completed_via": null, "completed_at": null },
      { "step_key": "see_agent_reply_in_inbox", "status": "pending",
        "completed_via": null, "completed_at": null }
    ],
    "created_at": "2026-07-24T10:00:00Z",
    "updated_at": "2026-07-24T10:12:33Z" } }
```
> `progress` 为服务端按步骤子表聚合的只读快照;`steps[].step_key` 顺序即激活路径顺序(§1.2.1)。

**手动完成步骤(幂等)** `POST /api/v1/onboarding/steps/create_first_issue/complete?workspace_id=ws-001`
```json
// Request(可选 Idempotency-Key,README §6.14/§6.5)
{}
// 200 Response:返回更新后的步骤对象
{ "data": { "step_key": "create_first_issue", "status": "completed",
            "completed_via": "manual", "completed_at": "2026-07-25T08:00:00Z" } }
```
> 对已 `completed`/`skipped` 的步骤重复调用为 **no-op**,返回当前状态(200),不改写 `completed_via`/`completed_at`(§3.5)。

**整体关闭(幂等)** `POST /api/v1/onboarding/dismiss?workspace_id=ws-001`
```json
// 200 Response
{ "data": { "id": "obs-7a2c...", "dismissed_at": "2026-07-25T08:30:00Z" } }
```
> 重复 dismiss 为 no-op,`dismissed_at` 保持首次值。恢复经 `POST /onboarding/restore`(清除 `dismissed_at`,幂等)。

**管理员重置** `POST /api/v1/workspaces/{ws}/onboarding/reset`
```json
// Request
{ "member_id": "mem-1111...", "checklist": "activation" }
// 200 Response:返回重建后的全新清单(全部步骤回到 pending,aha_reached_at/dismissed_at 清空)
{ "data": { "id": "obs-9f01...", "checklist": "activation",
            "aha_reached_at": null, "dismissed_at": null,
            "progress": { "total": 5, "completed": 1, "skipped": 0 } } }
```
> 重置在同一事务内 DELETE 既有主记录(级联步骤)+ 重新播种(§3.5);`create_workspace` 步因工作区已存在仍即置 `completed(auto)`,故重置后 `completed=1`。

### 3.3 错误码表

> 错误信封与 HTTP 语义遵循 README §6.14(`{"error":{"code","message","details"}}`,message 不泄漏堆栈/SQL/内部 ID);下表为本模块具名 code 补充。

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | `workspace_id` 缺失/非法;`step_key` 不在激活路径枚举内 |
| 401 | `unauthorized` | token 缺失/失效 |
| 403 | `forbidden` | 操作他人清单(成员自助端点仅限本人)/ 非 admin 调用重置 |
| 404 | `not_found` | 工作区不存在或对当前 principal 不可见(不泄露存在性) |
| 404 | `step_not_found` | `step_key` 对该清单不存在(如清单未播种该步) |
| 422 | `checklist_completed` | 试图手动完成末步之外、但清单已整体关闭(`dismissed_at` 非空)时的业务校验失败(需先 restore) |
| 429 | `rate_limited` | 触发限流(见 auth.md,带 `Retry-After`) |

### 3.4 分页 / 鉴权 / 限流

- **包络 / 分页 / 错误**:统一遵循 README §6.14;`GET /onboarding/state` 返回单对象(主记录 + 内联全部步骤,无分页——单清单步骤数固定为 5)。
- **鉴权**:中间件链路:解析 token → 得 principal → 校验对 `workspace_id`/`{ws}` 的成员资格与角色 → 放行。成员自助端点额外校验「操作的清单归属 == 当前 principal 的 member_id」(防 IDOR);重置端点需 admin/owner。
- **限流**:写端点(complete / dismiss / restore / reset)按 principal + IP 限流,阈值与响应头见 auth.md。
- **幂等写**:complete / dismiss / restore 支持 `Idempotency-Key` 请求头(README §6.14/§6.5),重复键返回首次结果;即便不带幂等键,状态迁移守卫亦保证重复调用为 no-op(§3.5)。

### 3.5 幂等与状态迁移(权威)

- **清单惰性创建**:`GET /onboarding/state` 在单一事务内 `INSERT ... ON CONFLICT (workspace_id, member_id, checklist) DO NOTHING` 播种主记录,随后批量插入五步(`ON CONFLICT (workspace_id, state_id, step_key) DO NOTHING`),`create_workspace` 步即置 `completed(auto)`;并发首访由唯一约束兜底,只成一行。
- **步骤完成守卫**:完成以条件 UPDATE 实现——`UPDATE onboarding_state_steps SET status='completed', completed_via=$1, completed_at=now() WHERE workspace_id=$2 AND state_id=$3 AND step_key=$4 AND status='pending' RETURNING ...`;**0 行返回即已完成/跳过,no-op**,杜绝并发重复完成与来源覆盖。`skipped` 同理由 `pending` 单向迁移。
- **dismiss / restore 幂等**:dismiss 仅当 `dismissed_at IS NULL` 时置位(条件 UPDATE);restore 仅当 `dismissed_at IS NOT NULL` 时清空;重复操作 no-op。
- **aha 仅置一次**:`aha_reached_at` 以 `WHERE aha_reached_at IS NULL` 条件 UPDATE 置位,重复达成不覆盖最早时间。
- **重置**:admin 重置在单事务内 DELETE 主记录(级联步骤)+ 重新播种(同惰性创建路径),返回全新进度。

### 3.6 自动完成:领域事件消费(README §6.6 唯一权威)

步骤自动完成由**本模块的 outbox 消费者**驱动:订阅业务模块在业务事务内写入的 `outbox_events`(README §6.6),at-least-once 消费、幂等标记(§3.5 完成守卫即去重)。**禁止**业务模块直接写本模块表,亦**禁止**本模块旁路 outbox 直连业务表轮询作为主路径(工作区作用域查询仅用于「首个」判定与建表时即时检查)。

| 消费事件(outbox `event_type`) | 推进步骤 | 判定 |
|----------------------------------|----------|------|
| `member.added` | `invite_member_or_add_agent` | 工作区出现 `member_type='agent'` 成员或 human 成员数 ≥ 2 → 对工作区内该步 `pending` 的清单批量完成(`completed_via='auto'`) |
| `issue.created` | `create_first_issue` | 工作区 `issues` 计数 0→1(按 `created_at` 最早一行判定「首个」)→ 批量完成该步 `pending` 清单;`reporter_id` 命中者即时完成 |
| `execution.queued` | `dispatch_or_mention_agent` | 工作区出现首个 `task_executions` 且 `trigger ∈ ('assign','mention')`(README §6.9)→ 完成该步 |
| `execution.completed` + `comment.created` | `see_agent_reply_in_inbox` | 上述执行 `completed` **且** issue 存在该 agent 的评论(JOIN `members.member_type='agent'`)→ 完成该步并置 `aha_reached_at`(§3.5) |

> 每次步骤完成后,本模块在**同一事务**写 outbox 的 `realtime.publish` 事件(载荷含频道、事件名、完整步骤快照),经 realtime projector 统一登记 `onboarding.progress`;末步完成时一并登记 `onboarding.completed`(README §6.6/§6.7 唯一写入路径,本模块不直接写 `realtime_events`)。

### 3.7 WebSocket 实时事件

> **统一实时契约见 README §6.7**(本 Spec 不重复定义):`seq` 一律为频道内单调递增(持久化于 `realtime_events`);客户端断线重连带 `resume_from=<last_seq+1>` 补发;游标过旧收 `resync_required` + REST 对账;订阅时逐资源授权。事件名 `onboarding.progress` / `onboarding.completed` 已在 README §6.7 注册表登记。

连接 `/ws`(握手鉴权见 auth.md;**禁止在 URL query 参数中传 token**,README §6.16),客户端订阅成员私有频道 `member:{member_id}:onboarding`(频道行携带 `workspace_id`,README §6.2 第 8 条/§6.7)。

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `onboarding.progress` | 任一步骤完成/跳过(自动或手动) | `state_id`, `checklist`, `step_key`, `status`, `completed_via`, `progress`(聚合快照) |
| `onboarding.completed` | 末步达成、`aha_reached_at` 首次置位 | `state_id`, `checklist`, `aha_reached_at`, `progress` |

**降级方案**:WebSocket 不可用时,退化为 30s 轮询 `GET /onboarding/state`;因进度真源在数据库,轮询路径功能等价。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
日常工作区(任意核心页面右侧/顶部)
   ├── 上手清单组件(可折叠卡片,未 dismiss 且未全部完成时常驻;达成 aha 后转庆祝态并可收起)
   │      ├── 进度条(已完成 N / 共 5)+ 百分比
   │      ├── 步骤列表(逐步勾选态 + 每步 CTA 深链 + 自动/手动来源标记)
   │      └── [整体关闭] / aha 庆祝态
   ├── 帮助菜单(? / 快捷键层,README §6.12)
   │      └── 「重新显示上手清单」(dismiss 后恢复入口 → POST /onboarding/restore)
   └── 各核心页面空状态(§1.2.2 四要素:插画 + 文案 + 主操作 + 深链既有向导)
          └── 空状态主操作完成 → 乐观推进对应清单步骤,服务端领域事件复核
```

### 4.2 关键组件

- **上手清单卡片**:顶部进度条(语义 token `success` 填充,对比度 ≥ WCAG 2.1 AA,README §6.12);步骤列表每行 = 勾选圈(完成态叠加 ✓ 图标 + 文字「已完成」,**脉冲动画/颜色不作唯一信号**,README §6.12)+ 步骤名 + 一步 CTA(深链到既有向导,§1.2.1)+ 来源角标(自动完成显示「✓ 已自动完成」)。当前首个未完成步骤高亮并默认展开 CTA。
- **aha 庆祝态**:末步达成时清单切换为庆祝卡片(插画 + 「你的第一位 AI 队友已上岗」+ 「查看 ta 的回评」深链收件箱);尊重 `prefers-reduced-motion`(README §6.12),庆祝不以动画为唯一信号(叠加文字与图标);可一键收起,收起后不再常驻。
- **dismiss / 恢复**:清单卡片提供「不再显示」(dismiss);dismiss 后从帮助菜单(`?` / `Ctrl/Cmd+/`,README §6.12 快捷键层)「重新显示上手清单」恢复(restore)。
- **空状态组件库**:统一四要素组件(§1.2.2),插画随主题语义 token 适配亮/暗色(README §6.12 主题契约),主操作按钮深链既有向导;空状态主操作与清单 CTA 复用同一深链,避免双份维护。
- **管理员重置入口**:成员管理页(member.md)对 admin/owner 提供「重置该成员上手进度」,调用 §3.4 重置端点,二次确认。

### 4.3 关键交互流程

**流程 1:新用户被清单引导至 aha moment**:新用户建区/受邀入册 → 进入工作区,清单卡片常驻,`create_workspace` 已自动勾选 → 点第 2 步 CTA「邀请成员 / 添加 agent」深链邀请面板或 agent 四步向导 → 完成后 `member.added` 事件驱动第 2 步自动勾选(实时 `onboarding.progress` 推送,清单刷新)→ 点第 3 步 CTA「新建 issue」深链看板/收件箱空状态主操作 → 建首 issue,`issue.created` 驱动第 3 步完成 → 第 4 步 CTA「分派给 agent / @ agent」深链 issue 分派或评论 composer(README §6.9 trigger preview 提示「发布后将触发一次运行」)→ 分派/@ 后 `execution.queued` 驱动第 4 步完成 → agent 异步执行并回评,`execution.completed` + `comment.created` 驱动第 5 步完成并置 `aha_reached_at`,清单切庆祝态 + `onboarding.completed` 推送,收件箱出现 agent 回评通知(README §6.13)。

**流程 2:空状态与清单联动**:用户未走清单,直接进空成员页 → 空状态主操作「添加 agent」深链 agent 向导 → 完成 → 成员页脱离空态,同时清单第 2 步经 `member.added` 自动完成(清单与空状态共享步骤推进逻辑,§1.2.2)。

**流程 3:dismiss 与恢复**:老用户点清单「不再显示」→ dismiss,清单消失 → 需要时从帮助菜单「重新显示上手清单」→ restore,清单按数据库进度恢复(未完成的步骤仍可继续)。

### 4.4 状态流转

**步骤状态机**(每步独立):
```
pending ──领域事件自动检测(completed_via='auto')──► completed(终态)
pending ──用户/管理员手动标记(completed_via='manual')──► completed(终态)
pending ──跳过(可选)──► skipped(终态)
completed / skipped ──(无回退)──;重置经 §3.4 整体重建回到 pending
```

**清单主记录**:
```
活动(dismissed_at IS NULL)──dismiss──► 已关闭(dismissed_at 非空)──restore──► 活动
活动 ──末步达成──► aha_reached_at 置位(仅一次)──► 庆祝态(可收起,仍可 dismiss)
```
> 完成守卫保证 `completed`/`skipped` 单向、`aha_reached_at` 仅置一次、dismiss/restore 幂等(§3.5)。

### 4.5 实时性与通知

- **实时**:走 WebSocket(§3.7,统一契约 README §6.7)。步骤完成/aha 达成经 `onboarding.progress` / `onboarding.completed` 实时推送(成员私有频道),清单卡片增量刷新;断线重连凭 `resume_from` 重放,游标过旧收 `resync_required` 对账;降级 30s 轮询 `GET /onboarding/state`。
- **派生实时事件走 outbox**:步骤变更派生的实时事件登记经 transactional outbox(README §6.6,本模块不直接写 `realtime_events`),杜绝「进度已更新但推送未登记」。
- **通知**:aha moment 的站内/邮件通知呈现遵循 README §6.13 唯一通知优先级矩阵与 comment-inbox.md(agent 回评本身的通知归 comment-inbox);本模块**不**为普通步骤完成单独发通知(避免引导噪声),仅以清单 UI + 实时事件呈现进度。

---

## 5. 验收标准

### 5.1 功能性

- [ ] 每成员每工作区每清单一主记录:`UNIQUE(workspace_id, member_id, checklist)` 下,首次 `GET /onboarding/state` 幂等播种主记录 + 五步,并发首访(≥10)恰成一行、步骤恰五步。
- [ ] `create_workspace` 步对建区者与被邀请入册者在建表事务内即 `completed(completed_via='auto')`(工作区既已存在)。
- [ ] **步骤自动检测正确性(逐条)**:① 工作区出现 agent 成员或第 2 个人类成员(`member.added`)→ 第 2 步自动完成;② 工作区首个 issue(`issue.created`,按 `created_at` 判定)→ 第 3 步自动完成;③ 首个 `task_executions` 且 `trigger ∈ ('assign','mention')`(`execution.queued`,README §6.9)→ 第 4 步自动完成;④ 该执行 `completed` **且** issue 存在该 agent 的评论(`execution.completed` + `comment.created`,JOIN `members.member_type='agent'`)→ 第 5 步完成并置 `aha_reached_at`。自动检测以 outbox 领域事件为准,**不依赖 UI 点击**。
- [ ] **完成幂等**:对已 `completed`/`skipped` 步骤重复 `POST /onboarding/steps/{step_key}/complete` 为 no-op,`completed_via`/`completed_at` 不被覆盖(完成守卫 0 行返回);同一事件重复消费(at-least-once)不产生重复完成。
- [ ] **dismiss / restore 幂等**:重复 dismiss `dismissed_at` 保持首次值;restore 清除 `dismissed_at`;dismiss 后清单 UI 消失,经帮助菜单 restore 后按数据库进度恢复。
- [ ] **aha 仅置一次**:末步多次达成不覆盖最早 `aha_reached_at`;`onboarding.completed` 仅在首次置位时推送一次。
- [ ] **管理员重置**:admin `POST /workspaces/{ws}/onboarding/reset` 在单事务内删除并重建清单,步骤回 `pending`(`create_workspace` 仍即完成),`aha_reached_at`/`dismissed_at` 清空;非 admin 调用返回 403。
- [ ] 手动完成步骤后 `completed_via='manual'`,与自动完成可区分。
- [ ] `status`/`completed_at` 一致性 CHECK:`(status='completed') = (completed_at IS NOT NULL)` 不被违反。
- [ ] 空状态四要素(插画 + 文案 + 主操作 + 深链)在收件箱/项目/看板/成员/聊天/自动化六个核心页面齐备,主操作深链到对应既有向导(workspace 创建 / invite 面板 / agent 四步向导 / issue 新建 / chat 发起 / autopilot 创建),**不重复实现向导**。
- [ ] 空状态主操作完成即推进对应清单步骤(乐观 UI + 服务端领域事件复核),与清单 CTA 共享深链。

### 5.2 性能

- [ ] `GET /onboarding/state`(含 5 步内联)P95 < 150ms(热缓存,README §10 基准)。
- [ ] 自动检测的精准 UPDATE 走 `idx_onboarding_steps_pending` 部分索引(按 `workspace_id, step_key` 定位未完成步骤),无全表扫描。
- [ ] 领域事件消费对单工作区批量完成步骤为单条集合 UPDATE,万级成员工作区下 P95 < 300ms(README §10)。
- [ ] 未达成 aha 清单的检索走 `idx_onboarding_states_ws_aha` 部分索引。

### 5.3 安全

- [ ] **跨租户复合 FK(README §6.2 / §9 T1 同类)**:`onboarding_states.(workspace_id, member_id)` 复合 FK → `members(workspace_id, id)`,构造跨工作区的 `member_id` 插入被数据库约束拒绝;A 区凭证携带 B 区 `workspace_id`/`member_id` 访问清单返回 403/404(不泄露存在性)。
- [ ] **防 IDOR**:成员自助端点(state/steps/dismiss/restore)校验「清单归属 member_id == 当前 principal」,操作他人清单返回 403。
- [ ] 仅 admin/owner 可调用重置;普通成员/guest/agent 调用返回 403。
- [ ] `step_key` 严格枚举校验,非法值返回 400 `validation_error`。
- [ ] 错误信息不泄漏其它工作区存在性或内部细节(README §6.14)。
- [ ] 写端点受 auth.md 限流约束,超限返回 429 + `Retry-After`。
- [ ] WebSocket 订阅成员私有频道 `member:{member_id}:onboarding` 时逐资源授权(仅本人可订阅),token 不经 URL query 传递(README §6.16)。

### 5.4 实时

- [ ] 步骤完成/跳过触发 `onboarding.progress`,末步达成触发 `onboarding.completed`,事件名命中 README §6.7 注册表(无未登记事件名)。
- [ ] 实时事件经 transactional outbox 唯一路径登记(README §6.6/§6.7,§9 T5/T26 同类):本模块不直接写 `realtime_events`;relay/projector 崩溃重启后进度事件不丢失、频道 `seq` 无缺口无重复。
- [ ] 事件 `seq` 为频道内单调、持久化于 `realtime_events`,重连凭 `resume_from` 重放,游标过旧收 `resync_required` 后 REST 对账(README §6.7)。
- [ ] WebSocket 不可用时,30s 轮询 `GET /onboarding/state` 降级路径功能等价(进度真源在数据库)。
