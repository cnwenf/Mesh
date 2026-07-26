# Issue(工作项)功能 Spec【全系统最核心实体】

> **所属层**:项目管理核心层 —— 原子工作单元。Issue 是看板的卡片、列表的行、被分派/被评论/被流转的对象;人类与 AI agent 一样可被设为 assignee,是连接项目管理与 agent 执行的枢纽。
>
> **依赖的其他 Spec**:
> - `workspace.md`(工作区):issue 必属于一个工作区,`workspace_id` 为隔离键。
> - `member.md`(成员):`assignee_id`/`reporter_id`/活动 `actor_member_id`/@提及统一引用 `members.id`(`member_type` ∈ {`human`,`agent`},人类与 agent 对称)。
> - `project.md`(项目):`project_id` 归属项目;`projects.key` 提供编号前缀;`projects.issue_seq` 提供项目级自增计数器;`milestone_id`/`cycle_id` 引用里程碑/周期。
> - `label-property.md`(标签与自定义字段):`issue_labels`(M:N)、`issue_custom_field_values`(EAV)。
>
> **被依赖(下游 Spec)**:
> - `kanban.md`(视图):看板/列表/时间线渲染本实体,复用本 Spec 的列表查询(过滤/分组/排序)。
> - `comment-inbox.md`(评论与收件箱):评论挂在 issue 上;@提及触发通知/agent 运行。
> - `agent.md`(智能体):assignee 为 agent 时分派事件触发其执行运行时(本 Spec 点到为止,执行细节归 agent.md)。
>
> **文档性质**:可直接指导开发的实现规格。所有命名、约束、端点、事件均以此为准则;与全局约定冲突时以 [README.md](../README.md) §6「全局权威契约」为准。

---

## 全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)

1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `id UUID`(默认 `gen_random_uuid()`);所有表含 `created_at` / `updated_at`(`TIMESTAMPTZ NOT NULL DEFAULT now()`,UTC);软删除统一 `deleted_at TIMESTAMPTZ NULL`(issue 软删除时编号永久保留)。
2. **成员**:`assignee_id` / `reporter_id` / 活动 `actor_member_id` / @提及目标一律引用 `members.id`,模型以 README §6.1 为唯一权威(统一 `members` 名册,`member_type ∈ {human,agent}`,人类与 agent 对称)。**存储层不设** `assignee_type` 等冗余判别列,人类/agent 判别 JOIN `members.member_type`;API 响应可携带服务端计算的 `member_type` 快照(真源为 members)。
3. **多租户**:跨模块外键一律按 README §6.2 建**复合 FK + 目标表 `UNIQUE(workspace_id, id)`**;`issues` 自身也被多张表复合引用,故同样建 `UNIQUE(workspace_id, id)`。
4. **编号 / 前缀 / 默认状态**:issue 编号、项目前缀永久保留、无项目 issue 工作区级计数器、`UNIQUE(workspace_id, identifier)`、每作用域唯一默认状态(部分表达式唯一索引 + 事务保证至少一个默认)以 **README §6.3 为唯一权威**。
5. **接口**:基础路径 `/api/v1`;包络 / 游标分页(分组查询统一**整体游标**,不给每组独立 cursor)/ 错误信封 / 乐观并发 / **过滤限制(嵌套深度 ≤3、条件数 ≤20、`statement_timeout` 兜底、超限 `filter_too_complex` / `query_cost_exceeded`)** 见 README §6.14。
6. **实时**:统一实时契约见 README §6.7(**频道内**单调 `seq`、`realtime_events` 持久重放、`resume_from` / `resync_required`、订阅逐资源授权);事件名 `<entity>.<action>`。
7. **队列 / 投递 / 触发**:transactional outbox(README §6.6)、at-least-once + 幂等键(§6.5)、execution/attempt 分层与长任务状态词汇(§6.4)、触发语义矩阵(§6.9);**`agent_runs` 不存在,运行真源实体为 `task_executions`**。
8. **性能基准**:一切 P95 / 时延指标仅在 README §10 基准下构成验收标准,本 Spec 引用而非自定。
9. **集成测试**:跨租户复合 FK 拒绝、编号并发、并发成环等场景按 README §9 矩阵(T1/T12/T15 等)必测。
10. **ORM**:SQLAlchemy 2.x 声明式约定(或等价 DDL,以 PostgreSQL 16 可执行为准)。

---

## 1. 功能描述

### 1.1 模块定位

Issue 是整个产品的**原子工作单元**。需求、任务、缺陷、史诗(Epic)在数据层统一为 issue,通过父子关系与依赖关系组合成结构。它承载:

1. **工作流转**:状态机驱动 todo → in_progress → in_review → done 的协作节奏。
2. **责任分派**:assignee 指向统一成员,人或 agent 对称;分派给 agent 即触发其执行。
3. **结构组织**:父子(sub-issues)表达"组成关系",依赖(blocks/blocked_by)表达"顺序关系"。
4. **人类可读寻址**:UUID 内部寻址 + `<项目前缀>-<自增号>`(如 `WEB-123`)人类引用。
5. **可扩展元数据**:标签 + 自定义字段(EAV)提供开放分类与结构化属性。

### 1.2 功能点与用户场景

#### 1.2.1 字段全集

| 字段 | 类型语义 | 说明 | 典型场景 |
|------|----------|------|----------|
| `title` | 短文本(必填) | 一句话标题,1–255 | "登录页在 Safari 崩溃" |
| `description` | 富文本/Markdown | 详细描述、复现步骤、验收标准 | 写 bug 复现步骤 |
| `status` | 枚举(状态机) | 自定义状态,归属某 category,见 §1.3 | 从 Todo → In Progress |
| `priority` | 枚举(可排序) | none/low/medium/high/urgent | 标为 urgent |
| `assignee` | 成员引用(可空) | 负责人(人或 agent) | 分派给"代码助手"agent |
| `reporter` | 成员引用 | 创建人/报告人 | 谁提的这个 bug |
| `estimate` | 数值 + 单位 | 估算(points/hours) | 估 5 个故事点 |
| `due_date` / `start_date` | 日期 | 截止日 / 计划开始日 | 8/15 前完成 |
| `project` | 项目引用(可空) | 归属项目 | 归到"官网改版" |
| `milestone` / `cycle` | 引用(可空) | 里程碑 / 迭代 | 挂到 v1.0;排入第 12 迭代 |
| `labels` | 多对多标签 | 分类标签 | 打"bug""前端" |
| `custom_field_values` | 动态属性 | 自定义字段值(label-property.md) | 填"严重程度=Major" |
| `parent` | 自引用(可空) | 父 issue(sub-issues) | 大需求拆子任务 |
| `position` | 数值 | 看板列内/列表内排序 | 拖拽改顺序 |
| `attachments` | 关联附件 | 文件(attachment.md) | 上传截图 |

#### 1.2.2 编号体系(命名空间前缀 + 自增号)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 人类可读编号 | `identifier = identifier_namespace_key || '-' || number`,如 `WEB-123` | 沟通时引用"WEB-123" |
| 编号命名空间(R2) | issue 持有 `identifier_namespace_key`(创建时取所属项目的 `key`;无项目 issue 取工作区收件箱保留前缀,默认 `WS`,**一经生成永不改变**)与 `number`(命名空间内自增,**永不改变**);`project_id` **仅表示当前归属**,跨项目迁移**只改 `project_id`,不重编号**(README §6.3) | 临时 issue `WS-7` 归入项目 WEB 仍叫 `WS-7`;`WEB-1` 迁入已有 `APP-1` 的项目不违约 |
| 命名空间内自增 | 有项目 issue 的序号在"创建时所属项目"的 `key` 命名空间内单调递增(`projects.issue_seq` 行锁自增,每项目独立计数);计数器绑定命名空间(项目 key / 收件箱前缀),**issue 迁移不改变计数器归属** | 新项目从 1 开始 |
| 双主键 | 内部用 UUID 做外键与 URL;编号仅用于人类引用与搜索 | API 用 UUID,UI 显示编号 |
| 唯一约束(双重) | 同时建 **`UNIQUE (workspace_id, identifier_namespace_key, number)`**(命名空间级编号唯一,取代已废除的项目级 `UNIQUE(project_id,number)`——后者与"不可变编号 + 跨项目迁移"直接冲突,README §6.3)与 **`UNIQUE (workspace_id, identifier)`**(工作区级,兜住无项目 issue 与一切重复) | 不允许两个 WEB-123;也不允许两个 WS-7 |
| 序号生成 | 项目计数器 `projects.issue_seq` 原子自增(行锁 `FOR UPDATE` / `RETURNING`),保证并发不重号 | 多人同时建 issue 不冲突 |
| 无项目 issue | 未归项目的 issue 使用**工作区级计数器 `workspaces.inbox_issue_seq`**(`BIGINT NOT NULL DEFAULT 0`,同发行锁自增,模式与 `projects.issue_seq` 一致)+ 工作区保留前缀(默认 `WS`,可在工作区设置中配置),编号形如 `WS-7` | 收件箱里的临时 issue |
| 编号不可变 | `identifier` 一经生成**永不改变**:issue 在项目间迁移(含归入/移出项目)**只改 `project_id`,不重编号**,`identifier_namespace_key` 与 `number` 保持不变(README §6.3) | 把临时 issue WS-7 归入项目仍叫 WS-7 |
| 前缀永久保留 | 项目前缀 `projects.key` 永久保留(软删除/归档项目后不可复用,见 README §6.3),杜绝历史 `WEB-123` 歧义;**一切 identifier 前缀(项目 `key`、当前与历史的收件箱前缀)由工作区级前缀注册表 `identifier_prefix_registry` 统一排他登记**(workspace.md owns,README §6.3) | 删除 WEB 项目后不能新建同名前缀项目 |

> **编号绝不复用、不可变**(README §6.3):issue 软删除后其编号永久废弃(行作为墓碑保留),删除 issue 的 `identifier` **永不被重新分配**;`identifier` 不随项目迁移改变。详见 §2.4。

#### 1.2.3 状态机与自定义状态(双层状态)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 内置状态类别(category) | 固定语义:`backlog`/`todo`/`in_progress`/`in_review`/`blocked`/`done`/`cancelled` | 报表按类别聚合"完成率" |
| 自定义状态 | 工作区/项目在某 category 下自定义具体状态名 | "开发中""联调中"都属 in_progress |
| 状态属性 | 名称、所属 category、颜色、排序、是否默认 | 给"测试中"配蓝色 |
| 状态流转 | 默认任意状态可切到任意状态;严格模式可配置允许的转换 | 限制"必须经过 in_review 才能 done" |
| 完成判定 | category=done 视为完成,用于进度/燃尽 | 项目进度按 done 占比 |
| 默认状态 | 新建 issue 的默认状态(`is_default=true`,通常 backlog/todo) | 新 issue 默认进 backlog |

> **category 与 status 分离(核心设计)**:`category` 是系统稳定语义(用于聚合、看板默认列、自动化触发);`status` 是用户可自定义的展示层。用户自由命名状态,而进度计算、看板分组等逻辑稳定不变。`issues.state_category` 冗余自 `issue_statuses.category`,加速聚合/筛选。

#### 1.2.4 父子(sub-issues)与依赖关系(分开建模)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 父子关系 | 一个 issue 可有多个 sub-issue;支持多层(建议 ≤2–3 层) | Epic 拆 story,story 再拆 task |
| 父进度聚合 | 父进度 = 子完成占比 | Epic 显示"3/5 完成" |
| 依赖关系 | issue 间声明 `blocks`/`blocked_by`/`relates_to`/`duplicates` | "上线"被"压测"阻塞 |
| 依赖可视化 | 详情页列出阻塞/被阻塞项;可选关系图 | "还差 2 个前置未完成" |
| 循环依赖检测 | 建立依赖时校验不成环 | 阻止 A→B→A |

> **关键设计**:父子用自引用外键 `parent_id`(树,强关系:级联/聚合);依赖用独立关联表 `issue_dependencies`(有向图,多对多,弱关系:仅约束)。**两者分开建模**,语义与生命周期不同。

#### 1.2.5 批量操作

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 多选 | 列表/看板勾选多个 issue | 选中 10 个 |
| 批量改字段 | 批量改状态/优先级/assignee/项目/标签/周期 | 把选中 10 个标为 high |
| 批量删除/归档 | 批量软删除 | 清理一批无效 issue |
| 操作结果反馈 | 返回成功/失败计数,部分失败给出原因 | "成功 9,失败 1(权限不足)" |
| 撤销(可选) | 批量操作后短时撤销 | 误操作回滚 |

### 1.3 边界与非目标

**本 Spec 范围内**:issue 字段、编号生成、双层状态机、父子与依赖、批量操作、列表查询(过滤/分组/排序)、变更留痕、实时事件。

**非目标(由其他 Spec 承担)**:
- 看板/列表/时间线视图的渲染、保存视图 → `kanban.md`(复用本 Spec 列表查询)。
- 评论、@提及、收件箱 → `comment-inbox.md`。
- 标签/自定义字段的定义与值存储细节 → `label-property.md`。
- 附件上传 → `attachment.md`。
- agent 被分派后的执行运行时、技能、模型 → `agent.md`(本 Spec 仅负责发出分派事件)。
- 项目进度聚合的呈现 → `project.md`(消费 `state_category`)。

---

## 2. 数据模型

### 2.1 ER 概览

```
projects ──1:N──► issues ──N:1──► issue_statuses(自定义状态)
                   │  │                (status.category 冗余到 issues.state_category)
                   │  ├──N:1──► members(assignee / reporter)
                   │  ├──N:1──► cycles / milestones(project.md)
                   │  ├──自引用 parent_id(树:sub-issues)
                   │  ├──M:N──► labels(via issue_labels,label-property.md)
                   │  ├──1:N──► issue_custom_field_values(EAV,label-property.md)
                   │  ├──1:N──► comments(comment-inbox.md)
                   │  ├──1:N──► issue_activity(变更留痕)
                   │  └──M:N──► issue_dependencies(有向图)
workspaces ──1:N──► issue_statuses(工作区级 / 项目级)
```

### 2.2 表定义

#### `issue_statuses`(自定义状态定义)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | |
| `project_id` | UUID | NULL, FK→projects(id) ON DELETE CASCADE | NULL | NULL=工作区级;非空=项目私有状态 |
| `name` | TEXT | NOT NULL | — | 如"测试中",1–50 |
| `category` | TEXT | NOT NULL, CHECK IN ('backlog','todo','in_progress','in_review','blocked','done','cancelled') | — | 稳定语义类别 |
| `color` | TEXT | NULL | NULL | 颜色 |
| `position` | REAL | NOT NULL | `0` | 同 category 内排序 |
| `is_default` | BOOLEAN | NOT NULL | `false` | 是否为新建默认状态 |
| `allowed_transitions` | JSONB | NOT NULL, `CHECK (jsonb_typeof(allowed_transitions) = 'array')` | `'[]'` | 严格模式「允许的下一步」:目标状态 id 字符串数组(§4.4;空数组 = 未配置任何允许的下一步;严格模式总开关为工作区设置 `workspaces.settings.status_strict_mode`,bool,默认 `false`) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**(可执行 DDL,见 §2.3;COALESCE 表达式不能写进表级 `UNIQUE` 约束,一律用**部分表达式唯一索引**,README §6.3):
- 作用域内状态名唯一:`CREATE UNIQUE INDEX uq_issue_statuses_name ON issue_statuses (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), name);`
- 每作用域唯一默认状态:`CREATE UNIQUE INDEX uq_issue_statuses_default ON issue_statuses (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000')) WHERE is_default;`
- **至少一个默认状态由事务保证**:任何"取消某状态默认"的写操作必须与"设置新默认"在**同一事务**内完成;工作区/项目创建事务内播种默认状态集;服务层自检校验每作用域恰有一个默认,缺失即报警并在同事务内修复(README §6.3)。
- 目标表唯一键:`UNIQUE (workspace_id, id)`(供 `issues.status_id` 等复合 FK 引用,README §6.2)。

> **category → status 映射**:一个 category 下可挂 0..N 个自定义 status;每个 status 必属且仅属一个 category。系统保证每个 `(workspace, project)` 作用域内至少有一个 `is_default=true` 的状态用于新建 issue(见上)。看板默认按 category 分列,列内按 status `position` 排序。
>
> **严格模式状态流转(§3.4/§4.4/§5.2)**:默认自由流转(任意状态可切任意状态)。工作区设置 `status_strict_mode=true` 开启严格模式:状态变更仅允许切到**当前状态** `allowed_transitions` 列出的目标状态;空数组表示该状态未配置任何允许的下一步(严格模式下不可转出);违规返回 409 `invalid_status_transition`(details 携带 from/to/allowed)。系统驱动的跨项目迁移状态映射(§3.8)不受严格模式约束。

#### `issues`(工作项)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 内部主键 |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键 |
| `project_id` | UUID | NULL,**复合 FK** `(workspace_id, project_id)→projects(workspace_id, id)` ON DELETE SET NULL (project_id)(PG16 列级,仅置空引用列,README §6.2 第 6 条) | NULL | 归属项目(**仅表示当前归属**;跨项目迁移只改本列,README §6.3) |
| `identifier_namespace_key` | TEXT | NOT NULL | — | 编号命名空间前缀键(创建时固定:项目 key 或收件箱保留前缀),永不随迁移改变(README §6.3) |
| `number` | BIGINT | NOT NULL | — | 命名空间内自增号(有项目:项目 key 命名空间 / 无项目:收件箱前缀命名空间),创建时固定、永不改变 |
| `identifier` | TEXT | NOT NULL | — | 人类编号 `WEB-123` / `WS-7`(= `identifier_namespace_key || '-' || number`,搜索/展示);**一经生成永不改变** |
| `title` | TEXT | NOT NULL | — | 1–255 |
| `description` | TEXT | NULL | NULL | 富文本/Markdown |
| `status_id` | UUID | NOT NULL,**复合 FK** `(workspace_id, status_id)→issue_statuses(workspace_id, id)` ON DELETE RESTRICT | — | 当前状态 |
| `state_category` | TEXT | NOT NULL, CHECK IN ('backlog','todo','in_progress','in_review','blocked','done','cancelled') | — | 冗余自 status.category |
| `priority` | TEXT | NOT NULL, CHECK IN ('none','low','medium','high','urgent') | `'none'` | |
| `assignee_id` | UUID | NULL,**复合 FK** `(workspace_id, assignee_id)→members(workspace_id, id)` ON DELETE SET NULL (assignee_id)(PG16 列级,仅置空引用列,README §6.2 第 6 条) | NULL | 人或 agent |
| `reporter_id` | UUID | NULL,**复合 FK** `(workspace_id, reporter_id)→members(workspace_id, id)` ON DELETE SET NULL (reporter_id)(PG16 列级,仅置空引用列,README §6.2 第 6 条) | NULL | 报告人 |
| `estimate` | NUMERIC | NULL | NULL | 估算值 |
| `estimate_unit` | TEXT | NULL, CHECK IN ('points','hours') | NULL | 单位 |
| `due_date` | DATE | NULL | NULL | 截止日 |
| `start_date` | DATE | NULL | NULL | 计划开始日 |
| `milestone_id` | UUID | NULL,**复合 FK** `(workspace_id, milestone_id)→milestones(workspace_id, id)` ON DELETE SET NULL (milestone_id)(PG16 列级,仅置空引用列,README §6.2 第 6 条) | NULL | |
| `cycle_id` | UUID | NULL,**复合 FK** `(workspace_id, cycle_id)→cycles(workspace_id, id)` ON DELETE SET NULL (cycle_id)(PG16 列级,仅置空引用列,README §6.2 第 6 条) | NULL | |
| `parent_id` | UUID | NULL,**复合自引用 FK** `(workspace_id, parent_id)→issues(workspace_id, id)` ON DELETE CASCADE(README §6.2 第 7 条:显式同租户,不靠"天然") | NULL | 父 issue(同表自引用) |
| `position` | REAL | NOT NULL | `0` | **规范默认排序值**(全局唯一规范排序);各视图内的手工拖拽排序不写本列,而存于 kanban.md `view_issue_positions`(避免一个视图的拖拽污染其它视图,README §6.14 排序契约) |
| `completed_at` | TIMESTAMPTZ | NULL | NULL | 进入 done 的时间 |
| `version` | INT | NOT NULL | `1` | 乐观并发版本号(见 §3.4) |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除(编号保留) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**表级约束**:
- **`UNIQUE (workspace_id, identifier_namespace_key, number)`** —— 命名空间级编号唯一(`uq_issue_namespace_number`;取代已废除的项目级编号唯一约束——后者与"不可变编号 + 跨项目迁移"直接冲突,README §6.3)。
- **`UNIQUE (workspace_id, identifier)`** —— 工作区级编号唯一(普通唯一索引 `uq_issues_identifier`,与命名空间级唯一双重兜住无项目 issue 与一切重复;README §6.3)。
- **`UNIQUE (workspace_id, id)`** —— 供 `issue_dependencies` / `issue_activity` / label-property 值表等**复合 FK** 引用本表(README §6.2)。
- `CHECK (parent_id <> id)` —— 防自环(更深层环检测在服务层,见 §2.5)。
- `CHECK (due_date IS NULL OR start_date IS NULL OR due_date >= start_date)`。
- `state_category` 与 `status_id` 同步由服务层保证(或触发器维护,见 §2.5)。

#### `issue_dependencies`(依赖关系,有向)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键(复合 FK 的本地列,README §6.2) |
| `issue_id` | UUID | NOT NULL,**复合 FK** `(workspace_id, issue_id)→issues(workspace_id, id)` ON DELETE CASCADE | — | 主体 |
| `depends_on_id` | UUID | NOT NULL,**复合 FK** `(workspace_id, depends_on_id)→issues(workspace_id, id)` ON DELETE CASCADE | — | 被依赖项(同工作区,跨租户依赖在 INSERT 即被复合 FK 拒绝) |
| `type` | TEXT | NOT NULL, CHECK IN ('blocks','blocked_by','relates_to','duplicates') | `'relates_to'` | 依赖类型 |
| `created_by` | UUID | NULL,**复合 FK** `(workspace_id, created_by)→members(workspace_id, id)` ON DELETE SET NULL (created_by)(PG16 列级,README §6.2 第 6 条) | NULL | 建立者 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:`UNIQUE (issue_id, depends_on_id, type)`;`CHECK (issue_id <> depends_on_id)`。

> `blocks` 与 `blocked_by` 互为反向语义:A blocks B ⇔ B blocked_by A。服务层写入时统一规范化为一条边(推荐以 `blocks` 存储,查询时双向展开),避免冗余与不一致。

#### `issue_activity`(变更留痕)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | 隔离键(复合 FK 本地列) |
| `issue_id` | UUID | NOT NULL,**复合 FK** `(workspace_id, issue_id)→issues(workspace_id, id)` ON DELETE CASCADE | |
| `actor_member_id` | UUID | NULL,**复合 FK** `(workspace_id, actor_member_id)→members(workspace_id, id)` ON DELETE SET NULL (actor_member_id)(PG16 列级,README §6.2 第 6 条) | 操作者(人或 agent) |
| `field` | TEXT | NOT NULL | 变更字段(如 `status`/`assignee`) |
| `old_value` | JSONB | NULL | 变更前 |
| `new_value` | JSONB | NULL | 变更后 |
| `created_at` | TIMESTAMPTZ | NOT NULL | 时间 |

> 追加式;由服务层在每次成功 PATCH 后写入 diff。高频字段(如 position 拖拽)可选择不留痕以免噪声。
>
> **DB 级最小权限**:对齐 `audit_logs`(auth.md §5.5),`mesh_app` 角色对本表仅授 `SELECT, INSERT`,迁移 0012 已 `REVOKE UPDATE, DELETE`——被陷的 app 角色无法改写/删除留痕。`issue_id` 的 `ON DELETE CASCADE` 与 `actor_member_id` 的 `ON DELETE SET NULL` 属系统强制的 FK 参照动作(不校验表级授权),不受影响;因此**不**像 `audit_logs` 那样再加拒 UPDATE/DELETE 的触发器——那会误伤这两类参照动作、破坏 issue 删除与 member 物理删除(§9 T18)。

#### `issue_custom_field_values` / `issue_labels`(跨模块,见 label-property.md)

```
issue_labels(issue_id, label_id)                      PK(issue_id,label_id)  —— M:N 标签
issue_custom_field_values(id, issue_id, field_def_id,
    value_text, value_number, value_date,
    value_member_id, value_boolean, value_json)        UNIQUE(issue_id,field_def_id) —— EAV
```

> 字段定义、类型、选项、校验规则均在 `label-property.md`;本 Spec 仅引用其值表参与查询(过滤/分组/排序)。

### 2.3 索引与约束

```sql
-- 编号唯一:命名空间级 + 工作区级(README §6.3)
CREATE UNIQUE INDEX uq_issue_namespace_number ON issues(workspace_id, identifier_namespace_key, number);  -- 命名空间级(README §6.3),取代已废除的项目级编号唯一约束
CREATE UNIQUE INDEX uq_issues_identifier ON issues(workspace_id, identifier); -- 工作区级,兜住无项目 issue 与一切重复
CREATE UNIQUE INDEX uq_issues_ws_id ON issues(workspace_id, id);            -- 供复合 FK 引用本表(README §6.2)
CREATE INDEX idx_issues_workspace ON issues(workspace_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_project_status
  ON issues(project_id, state_category) WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_assignee ON issues(assignee_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_reporter ON issues(reporter_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_parent ON issues(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_issues_cycle ON issues(cycle_id) WHERE cycle_id IS NOT NULL;
CREATE INDEX idx_issues_milestone ON issues(milestone_id) WHERE milestone_id IS NOT NULL;
CREATE INDEX idx_issues_due
  ON issues(due_date) WHERE due_date IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX idx_issues_position ON issues(project_id, state_category, position);
CREATE INDEX idx_issues_priority ON issues(workspace_id, priority) WHERE deleted_at IS NULL;

-- issue_statuses:部分表达式唯一索引(COALESCE 不能写进表级 UNIQUE,README §6.3)
CREATE UNIQUE INDEX uq_issue_statuses_name
  ON issue_statuses (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), name);
CREATE UNIQUE INDEX uq_issue_statuses_default
  ON issue_statuses (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000')) WHERE is_default;
CREATE UNIQUE INDEX uq_issue_statuses_ws_id ON issue_statuses(workspace_id, id);  -- 供复合 FK 引用
CREATE INDEX idx_issue_statuses_scope
  ON issue_statuses(workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), category);
CREATE INDEX idx_issue_deps_issue ON issue_dependencies(issue_id);
CREATE INDEX idx_issue_deps_on ON issue_dependencies(depends_on_id);
CREATE INDEX idx_issue_activity_issue ON issue_activity(issue_id, created_at DESC);
```

### 2.4 编号生成(项目级 + 工作区级原子计数器,README §6.3)

**有项目 issue**:复用 `projects.issue_seq` 计数器,事务内行锁原子自增:

```sql
BEGIN;
-- 行锁项目行并自增,RETURNING 取得 key 与 number(FOR UPDATE 语义)
UPDATE projects
   SET issue_seq = issue_seq + 1
 WHERE id = $project_id
 RETURNING key, issue_seq;                  -- → key(命名空间前缀键), number

-- identifier_namespace_key 创建时固定(= 创建时所属项目的 key),此后永不随迁移改变
INSERT INTO issues (id, workspace_id, project_id, identifier_namespace_key, number, identifier,
                    title, status_id, state_category, reporter_id, position)
VALUES ($uuid, $ws, $project_id, $key, $number, $key || '-' || $number,
        $title, $status_id, $category, $reporter_id, $position);
COMMIT;
```

**无项目 issue**:工作区级计数器 `workspaces.inbox_issue_seq`(README §6.3),与 `projects.issue_seq` 同发行锁自增模式。`workspaces` 表(workspace.md owns,权威定义见该 Spec)含 `inbox_issue_seq BIGINT NOT NULL DEFAULT 0` 列;保留前缀取自 `workspaces.settings` 的 `inbox_issue_prefix` 键(默认 `WS`,可配)。

```sql
BEGIN;
-- 行锁工作区行并自增,RETURNING 取得无项目 issue 的 number 与前缀
UPDATE workspaces
   SET inbox_issue_seq = inbox_issue_seq + 1
 WHERE id = $workspace_id
 RETURNING inbox_issue_seq,
           COALESCE(settings->>'inbox_issue_prefix', 'WS');   -- → number, prefix

-- identifier_namespace_key 创建时固定(= 收件箱保留前缀),identifier = prefix || '-' || number(如 WS-7)
INSERT INTO issues (id, workspace_id, project_id, identifier_namespace_key, number, identifier, ...)
VALUES ($uuid, $workspace_id, NULL, $prefix, $number, $prefix || '-' || $number, ...);
COMMIT;
```

- `UPDATE … RETURNING` 取得行级锁,保证同一计数器(项目行 / 工作区行)并发创建串行化、不重号;多数团队规模下性能够用,计数器行是潜在热点。
- **方案 2(规模化演进)**:独立序列表 `issue_number_seq(workspace_id, project_id NULL, last_number)`,对计数器行 `FOR UPDATE` 原子自增(`project_id IS NULL` 行即工作区级计数),与主表解耦,降低行锁竞争。
- **方案 3(可选)**:每项目 / 每工作区一个 PostgreSQL `SEQUENCE`,`nextval` 取号;并发友好但多序列管理较繁。
- **绝不复用**:删除 issue 仅置 `deleted_at`,不回退 `issue_seq` / `inbox_issue_seq`;对应 `(workspace_id, identifier_namespace_key, number)` / `(workspace_id, identifier)` 行作为墓碑保留,引用不失效。**已删除 issue 的 `identifier` 永不被重新分配**。
- **计数器绑定命名空间**:计数器(项目行 `issue_seq` / 工作区行 `inbox_issue_seq`)绑定于命名空间(项目 `key` / 收件箱保留前缀),**issue 迁移不改变计数器归属**——迁入项目的 issue 继续占用原命名空间的编号,不占用目标项目计数器。前缀的占用与变更语义(项目 `key` 永久占用、收件箱前缀变更时旧前缀置 `retired` 永久保留)见 workspace.md `identifier_prefix_registry`(README §6.3)。
- **编号不可变**:`identifier` 一经生成永不改变——无项目 issue(`WS-7`)归入项目、或 issue 在项目间迁移,**只改 `project_id`,均不重编号**;`identifier_namespace_key` 与 `number` 创建时固定、不随迁移改变(README §6.3)。

### 2.5 服务层一致性约束

1. **`state_category` 同步**:写入/更新 `status_id` 时,服务层读取对应 `issue_statuses.category` 写入 `issues.state_category`;或用数据库触发器维护。禁止二者不一致落库。
2. **`completed_at` 维护**:进入 `category='done'` 时写 `completed_at=now()`;离开 `done` 时清空为 NULL。
3. **父子防环(串行化)**:设置/变更 `parent_id` 时,**先取工作区级 advisory 事务锁**串行化图变更,再做可达性检查:
   ```sql
   -- 同一工作区的父子/依赖图变更串行化(事务结束自动释放),消除并发成环窗口
   SELECT pg_advisory_xact_lock(hashtext('issue_dep_graph:' || $workspace_id::text));
   -- 然后从目标父节点向上遍历祖先链(递归 CTE),命中当前 issue 则拒绝 409 circular_parent
   ```
   `CHECK (parent_id <> id)` 仅防自环,深层环靠上述串行化 + 祖先链遍历。**等价方案**:对涉及的两条 issue 行按**确定性 id 升序** `SELECT ... FOR UPDATE`(避免死锁)再向上遍历祖先链。并发成环必须有集成测试覆盖(README §9 T12)。
4. **依赖防环(串行化)**:新增依赖边时,同样**先取** `pg_advisory_xact_lock(hashtext('issue_dep_graph:' || $workspace_id::text))`,**再**从 `depends_on_id` 出发做有向图可达性遍历(DFS/BFS,递归 CTE),若能到达 `issue_id` 则拒绝(409 `circular_dependency`)。锁先于检查取得是关键——否则两事务并发插入 A→B 与 B→A 会双双通过检查形成环。并发测试见 README §9 T12。
5. **assignee 有效性**:`assignee_id`/`reporter_id` 必须是该工作区 `members` 中 `status='active'` 的成员(人或 agent 均可),否则 422 `assignee_not_member`。
6. **乐观并发**:更新携带 `version`(或 `If-Match`),`WHERE id=$1 AND version=$expected`;不匹配返回 409 `conflict`,客户端重取再改。

### 2.6 跨模块外键(一律复合 FK,README §6.2)

所有跨模块引用均**同时存 `workspace_id` 并建复合外键** `(workspace_id, <ref>_id) → 目标表 (workspace_id, id)`,使"引用了别的工作区的对象"在 INSERT 时即被数据库拒绝;目标表(`projects`/`members`/`issue_statuses`/`milestones`/`cycles`/`issues`/`labels`/`custom_field_defs`)均建 `UNIQUE (workspace_id, id)` 供引用。

| 字段 | 复合 FK 引用 | ON DELETE | 说明 |
|------|------|-----------|------|
| `issues.workspace_id` | `workspaces(id)` | CASCADE | 隔离键(workspace.md) |
| `issues.project_id` | `(workspace_id, project_id)→projects(workspace_id, id)` | SET NULL (project_id) | 归属项目(project.md;PG16 列级,README §6.2 第 6 条) |
| 跨项目迁移(R2) | 迁移**只改 `issues.project_id`** | — | `identifier_namespace_key`/`number`/`identifier` 永不随迁移改变(README §6.3;迁移契约见 §3.8) |
| `issues.status_id` | `(workspace_id, status_id)→issue_statuses(workspace_id, id)` | RESTRICT | 当前状态 |
| `issues.assignee_id` / `reporter_id` | `(workspace_id, assignee_id/reporter_id)→members(workspace_id, id)` | SET NULL (assignee_id) / SET NULL (reporter_id) | 人或 agent(member.md;PG16 列级,README §6.2 第 6 条) |
| `issues.milestone_id` | `(workspace_id, milestone_id)→milestones(workspace_id, id)` | SET NULL (milestone_id) | project.md(PG16 列级,README §6.2 第 6 条) |
| `issues.cycle_id` | `(workspace_id, cycle_id)→cycles(workspace_id, id)` | SET NULL (cycle_id) | project.md(PG16 列级,README §6.2 第 6 条) |
| `issues.parent_id` | `(workspace_id, parent_id)→issues(workspace_id, id)`(复合自引用 FK,README §6.2 第 7 条) | CASCADE | 自引用树(显式同租户,不靠"天然") |
| `issue_dependencies.issue_id` / `depends_on_id` | `(workspace_id, issue_id/depends_on_id)→issues(workspace_id, id)` | CASCADE | 有向图(依赖两端强制同工作区) |
| `issue_dependencies.created_by` | `(workspace_id, created_by)→members(workspace_id, id)` | SET NULL (created_by) | member.md(PG16 列级,README §6.2 第 6 条) |
| `issue_activity.issue_id` | `(workspace_id, issue_id)→issues(workspace_id, id)` | CASCADE | 留痕 |
| `issue_activity.actor_member_id` | `(workspace_id, actor_member_id)→members(workspace_id, id)` | SET NULL (actor_member_id) | member.md(PG16 列级,README §6.2 第 6 条) |
| `issue_custom_field_values.*` / `issue_labels.*` | 见 label-property.md(均带 `workspace_id` + 复合 FK → issues / custom_field_defs / labels / members) | CASCADE | label-property.md |

### 2.7 SQLAlchemy 2.x 声明式约定(核心表示例)

```python
from sqlalchemy import ForeignKeyConstraint, Index

class IssueStatus(Base):
    __tablename__ = "issue_statuses"
    __table_args__ = (
        CheckConstraint(
            "category IN ('backlog','todo','in_progress','in_review',"
            "'blocked','done','cancelled')", name="ck_issue_statuses_category"),
        # 复合 FK:project_id 引用 projects(workspace_id, id)(README §6.2)
        ForeignKeyConstraint(["workspace_id", "project_id"],
                             ["projects.workspace_id", "projects.id"], ondelete="CASCADE"),
        UniqueConstraint("workspace_id", "id", name="uq_issue_statuses_ws_id"),
        # 部分表达式唯一索引(COALESCE 不能写进表级 UNIQUE,README §6.3)
        Index("uq_issue_statuses_name", "workspace_id",
              text("COALESCE(project_id, '00000000-0000-0000-0000-000000000000')"),
              "name", unique=True),
        Index("uq_issue_statuses_default", "workspace_id",
              text("COALESCE(project_id, '00000000-0000-0000-0000-000000000000')"),
              unique=True, postgresql_where=text("is_default")),
    )
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    server_default=text("gen_random_uuid()"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[Optional[str]] = mapped_column()  # 复合 FK 见 __table_args__
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[float] = mapped_column(server_default="0")
    is_default: Mapped[bool] = mapped_column(server_default=text("false"))


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        # 命名空间级编号唯一(README §6.3):取代已废除的项目级编号唯一约束
        UniqueConstraint("workspace_id", "identifier_namespace_key", "number",
                         name="uq_issue_namespace_number"),
        UniqueConstraint("workspace_id", "identifier", name="uq_issues_identifier"),  # 工作区级编号唯一
        UniqueConstraint("workspace_id", "id", name="uq_issues_ws_id"),              # 供复合 FK 引用
        CheckConstraint("parent_id <> id", name="ck_issues_no_self_parent"),
        # 复合 FK(README §6.2):各跨模块引用均带 workspace_id
        ForeignKeyConstraint(["workspace_id", "project_id"],
                             ["projects.workspace_id", "projects.id"],
                             ondelete="SET NULL (project_id)"),  # (PG16 列级 SET NULL,README §6.2 第 6 条)
        ForeignKeyConstraint(["workspace_id", "status_id"],
                             ["issue_statuses.workspace_id", "issue_statuses.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["workspace_id", "assignee_id"],
                             ["members.workspace_id", "members.id"],
                             ondelete="SET NULL (assignee_id)"),  # (PG16 列级 SET NULL,README §6.2 第 6 条)
        ForeignKeyConstraint(["workspace_id", "reporter_id"],
                             ["members.workspace_id", "members.id"],
                             ondelete="SET NULL (reporter_id)"),  # (PG16 列级 SET NULL,README §6.2 第 6 条)
        ForeignKeyConstraint(["workspace_id", "milestone_id"],
                             ["milestones.workspace_id", "milestones.id"],
                             ondelete="SET NULL (milestone_id)"),  # (PG16 列级 SET NULL,README §6.2 第 6 条)
        ForeignKeyConstraint(["workspace_id", "cycle_id"],
                             ["cycles.workspace_id", "cycles.id"],
                             ondelete="SET NULL (cycle_id)"),  # (PG16 列级 SET NULL,README §6.2 第 6 条)
        # parent 复合自引用 FK(README §6.2 第 7 条:显式同租户,不靠"天然")
        ForeignKeyConstraint(["workspace_id", "parent_id"],
                             ["issues.workspace_id", "issues.id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    server_default=text("gen_random_uuid()"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[Optional[str]] = mapped_column()      # 复合 FK 见 __table_args__;仅表示当前归属,迁移只改本列
    identifier_namespace_key: Mapped[str] = mapped_column(Text, nullable=False)  # 创建时固定(项目 key / 收件箱保留前缀),永不随迁移改变(README §6.3)
    number: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 命名空间内自增,创建时固定、永不改变
    identifier: Mapped[str] = mapped_column(Text, nullable=False)  # 一经生成永不改变
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status_id: Mapped[str] = mapped_column()                 # 复合 FK 见 __table_args__
    state_category: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False, server_default="none")
    assignee_id: Mapped[Optional[str]] = mapped_column()     # 复合 FK 见 __table_args__
    reporter_id: Mapped[Optional[str]] = mapped_column()     # 复合 FK 见 __table_args__
    milestone_id: Mapped[Optional[str]] = mapped_column()
    cycle_id: Mapped[Optional[str]] = mapped_column()
    parent_id: Mapped[Optional[str]] = mapped_column()       # 普通列;复合自引用 FK 见 __table_args__(README §6.2 第 7 条)
    position: Mapped[float] = mapped_column(server_default="0")  # 规范默认排序;视图内手工排序见 kanban view_issue_positions
    completed_at: Mapped[Optional[datetime]] = mapped_column()
    version: Mapped[int] = mapped_column(server_default="1")
    deleted_at: Mapped[Optional[datetime]] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), onupdate=text("now()"))

    # parent 自引用改为复合 FK (workspace_id, parent_id) 后,relationship 的 foreign_keys/remote_side 需覆盖两列
    children: Mapped[list["Issue"]] = relationship(
        cascade="all, delete-orphan",
        foreign_keys="[Issue.workspace_id, Issue.parent_id]",
        back_populates="parent")
    parent: Mapped[Optional["Issue"]] = relationship(
        remote_side="[Issue.workspace_id, Issue.id]",
        foreign_keys="[Issue.workspace_id, Issue.parent_id]",
        back_populates="children")
```

---

## 3. 接口设计

REST 基础路径 `/api/v1`,Bearer token,游标分页。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/workspaces/{ws}/issues` | 创建 issue |
| GET | `/workspaces/{ws}/issues` | 列表(强过滤/排序/分组,见 §3.2) |
| GET | `/issues/{id}` | 获取(支持 UUID 或 `by-identifier/WEB-123`) |
| PATCH | `/issues/{id}` | 更新字段(含状态流转) |
| DELETE | `/issues/{id}` | 软删除 |
| GET | `/issues/{id}/children` | 子 issue 列表 |
| GET | `/issues/{id}/dependencies` | 依赖列表(blocks/blocked_by) |
| POST | `/issues/{id}/dependencies` | 新增依赖 |
| DELETE | `/issues/{id}/dependencies/{dep_id}` | 删除依赖 |
| POST | `/issues/{id}/move-preview` | 跨项目迁移预览:返回将被映射/清除的字段清单(§3.8) |
| POST | `/issues/{id}/move` | 跨项目迁移:需 `confirm:true`,单事务完成(§3.8) |
| POST | `/issues/bulk` | 批量操作 |
| GET | `/workspaces/{ws}/statuses` | 状态定义列表 |
| POST | `/workspaces/{ws}/statuses` | 创建自定义状态 |
| PATCH | `/statuses/{id}` | 更新状态 |
| DELETE | `/statuses/{id}` | 删除状态(需迁移其下 issue) |
| GET | `/issues/{id}/activity` | 变更历史 |
| GET | `/workspaces/{ws}/issue-templates` | 列出 issue 模板(R2,建议-10,§3.9) |
| POST | `/workspaces/{ws}/issue-templates` | 创建 issue 模板 |
| PATCH | `/issue-templates/{id}` | 更新模板 |
| DELETE | `/issue-templates/{id}` | 删除模板 |
| POST | `/issue-templates/{id}/instantiate` | 由模板创建 issue |

### 3.2 列表查询参数(统一,看板/列表/我的任务复用)

- **过滤**:`status`(status_id)、`state_category`、`priority`、`assignee_id`、`reporter_id`、`project_id`、`cycle_id`、`milestone_id`、`label`(label_id)、`parent_id`、`due_before`/`due_after`、`q`(搜索 title/identifier)、自定义字段过滤(如 `cf_severity=opt_major`)。
- **过滤限制(README §6.14)**:filters **最大嵌套深度 3、最大条件数 20**(**扁平查询参数与 `filters` 树条件合并计数**,合计 ≤20);服务端以 `statement_timeout`(默认 3s)+ 估算查询成本兜底。超限返回 **`400 filter_too_complex`**;成本超限返回 **`422 query_cost_exceeded`**(建议收窄条件)。
- **排序**:`sort=position|created_at|priority|due_date&order=asc|desc`。
- **分组**:`group_by=state_category|assignee|priority|project|label|cycle`(看板/分组列表用)。
- **分页**:`limit`、`cursor`;**分组查询统一整体游标**(见 §3.5,README §6.14)。

### 3.3 请求/响应示例

**创建 issue** `POST /api/v1/workspaces/{ws}/issues`
```json
// Request
{
  "title": "登录页在 Safari 崩溃",
  "description": "复现步骤:…",
  "project_id": "prj_uuid_1",
  "priority": "high",
  "assignee_id": "mem_uuid_b2",
  "estimate": 5, "estimate_unit": "points",
  "due_date": "2026-08-15",
  "label_ids": ["lbl_uuid_bug"],
  "parent_id": null
}

// 201 Response
{
  "data": {
    "id": "iss_uuid_124",
    "identifier": "WEB-124",
    "identifier_namespace_key": "WEB",
    "number": 124,
    "title": "登录页在 Safari 崩溃",
    "status": { "id": "st_uuid_todo", "name": "Todo", "category": "todo" },
    "state_category": "todo",
    "priority": "high",
    "assignee": { "id": "mem_uuid_b2", "name": "代码助手", "member_type": "agent" },
    "reporter": { "id": "mem_uuid_a1", "name": "Jane Doe", "member_type": "human" },
    "estimate": 5, "estimate_unit": "points",
    "due_date": "2026-08-15",
    "version": 1,
    "created_at": "2026-07-24T10:00:00Z",
    "updated_at": "2026-07-24T10:00:00Z"
  }
}
```

**状态流转** `PATCH /api/v1/issues/{id}`
```json
// Request(携带乐观并发版本)
{ "status_id": "st_uuid_in_progress", "version": 1 }

// 200 Response:返回更新后 issue,version+1;进入 done 时 completed_at 自动写入
```

**批量操作** `POST /api/v1/issues/bulk`
```json
// Request
{
  "issue_ids": ["iss_1", "iss_2", "iss_3"],
  "changes": { "priority": "urgent", "assignee_id": "mem_uuid_a1" }
}

// 200 Response(返回成功/失败计数;部分失败给出原因)
{
  "data": {
    "succeeded": 2,
    "failed": 1,
    "errors": [ { "issue_id": "iss_3", "code": "forbidden", "message": "无权限改该 issue" } ]
  }
}
```
> 全部成功时 HTTP 200 且 `failed=0`;存在失败时 HTTP 422 `bulk_partial_failure`,`errors` 列出每条失败原因(权限/校验/成环等)。

**依赖** `POST /api/v1/issues/{id}/dependencies`
```json
// Request
{ "depends_on_id": "iss_uuid_9", "type": "blocked_by" }

// 201 Response
{ "data": { "id": "dep_uuid_1", "issue_id": "iss_uuid_124", "depends_on_id": "iss_uuid_9", "type": "blocked_by" } }
// 若形成环 → 409 circular_dependency
```

**分组查询(看板用)** `GET /api/v1/workspaces/{ws}/issues?group_by=state_category&project_id=prj_uuid_1`
```json
{
  "groups": [
    { "key": "todo", "label": "Todo", "count": 3,
      "data": [ { "id": "iss_uuid_124", "identifier": "WEB-124", "title": "登录页在 Safari 崩溃" } ] },
    { "key": "in_progress", "label": "In Progress", "count": 2, "data": [ ] }
  ],
  "next_cursor": null
}
```

**错误响应(统一信封)**
```json
{ "error": { "code": "circular_dependency", "message": "依赖将形成环", "details": { "path": ["iss_1","iss_9","iss_1"] } } }
```

### 3.4 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | title 缺失/超长;非法 priority;`due_date < start_date` |
| 401 | `unauthorized` | token 缺失/失效 |
| 403 | `forbidden` | 无权限改该 issue(私有项目/角色不足) |
| 404 | `not_found` | issue 不存在/不可见 |
| 409 | `circular_dependency` | 依赖成环 |
| 409 | `circular_parent` | 父子成环 |
| 409 | `invalid_status_transition` | 启用流转限制且该转换不允许 |
| 409 | `conflict` | 乐观并发版本不匹配 |
| 422 | `assignee_not_member` | assignee/reporter 非该工作区有效活跃成员 |
| 422 | `bulk_partial_failure` | 批量部分失败(详情在 errors) |
| 422 | `required_field_missing` | 状态流转时必填自定义字段未填(label-property.md) |
| 422 | `move_confirmation_required` | 跨项目迁移未携带 `confirm:true`(`details.preview` 携带预览清单,§3.8) |
| 429 | `rate_limited` | 限流 |

### 3.5 分页与鉴权

- **分页(README §6.14 整体游标契约)**:游标分页,游标编码 `(position 或 created_at, id)`。**分组查询统一为整体游标**:响应顶层为 `{"groups": [{key,label,count,wip?,data}], "next_cursor": ...}`,`count` 为组内总数,`data` 为当前页该组切片,`next_cursor` 驱动下一页;**不得**在响应中给每组独立 `cursor`(与 kanban.md 统一此契约)。
- **过滤限制(README §6.14)**:filters 嵌套深度 ≤3、条件数 ≤20(扁平查询参数与 `filters` 树合并计数);`statement_timeout`(默认 3s)+ 成本估算兜底;超限 `400 filter_too_complex` / `422 query_cost_exceeded`。
- **鉴权**:
  - 读:工作区成员可读可见 issue;`private` 项目的 issue 需项目成员或 admin。
  - 写:项目写权限或工作区 `member` 及以上;改他人 issue 需项目 `member`/`lead` 或 admin。
  - 批量操作**逐个校验权限**,失败的计入 `errors`,成功的照常应用。
- **乐观并发(推荐)**:PATCH 带 `version`(或 `If-Match: <updated_at>`),冲突返回 409,避免拖拽/编辑互相覆盖。
- **限流**:写端点按工作区/成员维度限流。

### 3.6 WebSocket 事件

> 实时契约**以 README §6.7 为唯一权威**(频道内单调 `seq`、`realtime_events` 持久重放、`resume_from` / `resync_required`、订阅逐资源授权、payload 携带完整变更字段与可见性水位)。本节仅列出 issue 模块的频道与事件名,不重复定义 seq 语义。

- **频道订阅**:`workspace:{ws}:issues`(列表级)、`issue:{id}`(详情级);订阅时按 README §6.7 重新做资源级授权(工作区成员资格 / 项目可见性)。
- **seq 与断线重放**:频道内单调 `seq`(README §6.7);重连传 `resume_from=<last_seq+1>`,服务端自 `realtime_events` 重放;游标过旧下发 `resync_required` + REST 对账水位。
- **事件清单**(`<entity>.<action>`):

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `issue.created` | 创建 issue | `issue`(含 identifier) |
| `issue.updated` | 字段/状态变更 | `id`、`changes`(字段 diff)、`version` |
| `issue.deleted` | 软删除 | `id` |
| `issue.moved` | 看板位置/状态/分组变化 | `id`、`from`/`to`(category/position) |
| `issue.project_changed` | 跨项目迁移完成(§3.8) | `id`、`from_project_id`/`to_project_id`、`mapped_fields`、`cleared_fields` |
| `issue.labels_changed` | 标签变更 | `id`、`label_ids` |
| `issue.custom_field_changed` | 自定义字段值变更 | `id`、`field_def_id`、`value` |
| `dependency.changed` | 依赖增删 | `issue_id`、`depends_on_id`、`type`、`action` |
| `comment.created` | 新评论(见 comment-inbox.md;事件名以 README §6.7 词汇注册表为准) | `issue_id`、`comment` |

> **看板拖拽**:乐观更新 + 服务端确认 + 失败回滚;并发冲突由 `version` 检测。列表/收件箱收到 `issue.updated` 时按 `id` 做**增量合并**(更新对应行),而非整页刷新。降级:WS 断开时 30s 轮询列表(带 `since=updated_at` 增量拉取)。

### 3.7 分派给 agent 的执行入口(与 agent.md 衔接)

当 `assignee_id` 指向 `member_type='agent'` 的成员,或评论中 @提及 agent 时,触发语义**以 README §6.9 触发矩阵为唯一权威**(分派 → `trigger='assign'`;再次选择同一 assignee = no-op;字段值无变化 = no-op;@提及 = 发布后入队一次执行)。本模块在**同一业务事务内**经 **transactional outbox**(README §6.6)写入派生事件(`issue.assigned` / `execution.enqueue`),由 outbox relay 分发到统一 agent 编排入口创建 `task_executions`(**运行真源实体,不存在 `agent_runs`**;execution/attempt 分层与长任务状态词汇见 README §6.4),agent 以自己的成员身份接管该 issue(改状态、发评论、产出结果)。**执行运行时、技能、模型、sandbox 等细节归 `agent.md` / `runtime.md`**,本 Spec 仅保证:分派对人与 agent 走同一接口、同一 outbox 事件、同一成员引用,体验对称;且"业务已提交但任务未入队"的永久丢失被 outbox 杜绝。

### 3.8 跨项目迁移(move,R2 两步式契约)

跨项目迁移 issue(属性栏改 project、或看板 `group_by=project` 拖拽)按 README §6.14 落地"预览 → 确认"两步式契约:先给出将被**映射/清除**的字段清单并要求确认,再在**单事务**内完成迁移,杜绝"当前项目 + 旧项目私有字段"的脏状态。

**第一步:迁移预览** `POST /api/v1/issues/{id}/move-preview`
```json
// Request
{ "target_project_id": "prj_uuid_9" }

// 200 Response
{
  "data": {
    "issue_id": "iss_uuid_124",
    "identifier": "WEB-124",
    "from_project_id": "prj_uuid_1",
    "target_project_id": "prj_uuid_9",
    "version": 3,
    "mapped_fields": [
      { "field": "status",
        "from": { "id": "st_uuid_dev", "name": "开发中", "category": "in_progress" },
        "to": { "id": "st_uuid_todo9", "name": "Todo", "category": "todo" },
        "reason": "项目私有 status → 目标项目同 category 默认 status" }
    ],
    "cleared_fields": [
      { "field": "milestone_id", "reason": "项目私有里程碑" },
      { "field": "cycle_id", "reason": "项目绑定的周期" },
      { "field": "labels", "items": [ { "id": "lbl_uuid_web1", "name": "官网" } ], "reason": "项目级标签" },
      { "field": "custom_field_values", "items": [ { "field_def_id": "cfd_uuid_sev", "name": "严重程度" } ], "reason": "项目级自定义字段值" }
    ],
    "kept_fields": [ "title", "description", "priority", "assignee_id", "reporter_id",
                     "estimate", "due_date", "start_date", "identifier",
                     "工作区级 labels", "工作区级自定义字段值" ]
  }
}
```

- **status 映射规则**:映射为目标项目**同 category 的默认 status**(`is_default=true`);目标项目该 category 下无自定义 status 时,取该 category 下 `position` 最小者。
- **清除规则**:项目私有 milestone/cycle、项目级 label、项目级自定义字段值一律清除(置 NULL / 删除值行);**工作区级** label、自定义字段值及其余工作区级字段保留。
- **编号不变**:迁移只改 `project_id`;`identifier_namespace_key`/`number`/`identifier` 保持不变(README §6.3)。
- **鉴权前置(安全契约)**:预览与未确认(`confirm` 缺省)请求携带完整字段清单,**先鉴权、后出清单**:源 issue 走读门(不可见 → guest 404 `not_found` / 其他成员 403 `forbidden`,与 `GET /issues/{id}` 同矩阵);`target_project_id` 非空时走项目写门(不可见 → guest 404 / 403,不存在/跨工作区 → 404)。任何鉴权失败**只回错误信封,不携带 `preview`**。`POST /issues/bulk` 未确认聚合预览同理**逐条**过源 issue 读门:越权项仅回 error marker(`{"issue_id", "error": "forbidden"|"not_found"}`),**不回 plan**;目标项目写门在聚合前整体校验(失败即整体 403/404)。**聚合预览覆盖全部条目**(`issue_ids` 上限 100,不截断),确认前每一项的映射/清除清单可见。

**第二步:确认迁移** `POST /api/v1/issues/{id}/move`
```json
// Request(必须携带 confirm:true 与当前 version)
{ "target_project_id": "prj_uuid_9", "confirm": true, "version": 3 }

// 200 Response(返回更新后的 issue,version+1)
{
  "data": { "id": "iss_uuid_124", "identifier": "WEB-124", "project_id": "prj_uuid_9",
            "status": { "id": "st_uuid_todo9", "name": "Todo", "category": "todo" },
            "version": 4 }
}
```

在**单事务**内完成:① 乐观锁校验(`version` 匹配,否则 409 `conflict`);② `project_id` 变更;③ 按预览做 status 映射;④ 清除项目私有 milestone/cycle、项目级 label、项目级自定义字段值(工作区级保留);⑤ `version + 1`;⑥ 写 `issue_activity` 留痕(含映射/清除清单);⑦ 同事务写 `outbox_events` `issue.project_changed` 事件(payload 含 `from_project_id`/`to_project_id`、`mapped_fields`、`cleared_fields`,README §6.6),经 outbox relay → realtime projector 唯一路径登记并推送(README §6.7)。

- **未携带 `confirm: true`** → 422 `move_confirmation_required`,`details.preview` 返回第一步的预览清单(客户端必须展示并要求确认,README §6.14)。
- 版本不符 → 409 `conflict`;目标项目不存在/不可见 → 404 `not_found` / 403 `forbidden`。
- 看板 `group_by=project` 拖拽经 `POST /views/{id}/moves`(`confirm=true`)调用同一事务契约(与 kanban.md §3.2 统一,README §6.14)。

---

### 3.9 issue 模板(R2,建议-10 转正)

**数据模型**(`issue_templates`,本 Spec owns):

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE;UNIQUE(workspace_id, id) | 隔离(README §6.2) |
| `project_id` | UUID | NULL,复合 FK `(workspace_id, project_id)→projects(workspace_id, id)` ON DELETE SET NULL (project_id) | NULL=工作区级模板;非空=项目私有模板 |
| `name` | TEXT | NOT NULL | 模板名,1–120;`UNIQUE (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), name)`(部分表达式唯一索引,README §6.3) |
| `description` | TEXT | NULL | 模板用途说明 |
| `template_body` | JSONB | NOT NULL | 预填字段集:`{title_prefix, description, state_category/status_id, priority, label_ids[], custom_field_values{}, estimate/estimate_unit, parent_strategy}`;引用型字段(status/label/自定义字段)按 README §6.2 复合 FK 语义校验同租户 |
| `created_by` | UUID | NOT NULL,复合 FK `(workspace_id, created_by)→members(workspace_id, id)` ON DELETE RESTRICT | 创建者(成员软删除,不悬空) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | |

**实例化语义** `POST /api/v1/issue-templates/{id}/instantiate`(请求 `{title, overrides?}`):
- 以 `template_body` 为基线、请求 `overrides` 覆盖,走**与 `POST /workspaces/{ws}/issues` 完全相同的创建链路**(编号生成 §2.4、必填字段校验 label-property.md §4.5、分派触发 README §6.9),返回 201 issue 对象;
- 模板引用的 status/label/自定义字段若已失效(删除/停用)→ 该字段回退默认值并在响应 `details.skipped_fields` 列出(**不整体失败**);
- 模板本身不产生编号、不触发 agent;仅实例化产生的 issue 走正常触发矩阵。

**验收**:模板 CRUD 与项目/工作区作用域唯一生效;由模板创建的 issue 字段与模板一致(除 overrides);失效引用字段优雅降级;跨租户模板引用被复合 FK 拒绝(README §9 T1 同类)。

## 4. UI/UX 设计

### 4.1 信息架构

```
我的任务(/inbox)            —— assignee=我(人或 agent 视角)的 issue
项目 → Issue 列表/看板       —— 见 kanban.md(复用本 Spec 列表查询)

Issue 详情(全屏或右侧抽屉)
   ├── 头部:标题(可编辑)、identifier、状态选择器、操作菜单(···)
   ├── 主体:描述(富文本)、子 issue 列表、依赖列表、活动流、评论区
   └── 属性侧栏:assignee、reporter、priority、estimate、due/start、
                 project、cycle、milestone、labels、自定义字段
```

### 4.2 关键组件

- **issue 行/卡片**:显示 identifier、标题、状态色条、优先级图标、assignee 头像(人/agent 区分)、到期日、标签点。
- **快速创建**:列表顶部按 `C` 或点"+ 新建"弹轻量表单(标题 + 可选展开更多字段),支持连续创建。
- **属性内联编辑**:侧栏每字段点击即编辑(assignee 弹成员选择器含 agent;priority 弹图标菜单;日期弹日历)。
- **状态选择器**:下拉按 category 分组列出所有自定义状态,带颜色;选中即流转。
- **子 issue 区**:树状展示,父显示完成进度(如"3/5");支持就地新增子任务。
- **依赖区**:列出 blocks / blocked_by,点击跳转;阻塞项未完成时给出视觉提示("还差 2 个前置")。
- **批量操作工具条**:勾选后浮出底栏(改状态/优先级/assignee/标签/删除/取消选),提交后展示成功/失败计数。

### 4.3 关键交互流程

**创建 issue**:按 `C` → 输入标题(回车快速创建,或 Tab 展开填项目/assignee/优先级)→ 保存 → 自动分配编号 `WEB-124` → 出现在对应项目/看板。

**分派给 AI agent**:属性栏 assignee → 选择器混合列出人类与 agent(各带类型图标)→ 选 agent → 保存 → 发出 `issue.updated`(assignee=agent)→ agent 收到分派事件并接管。

**拖拽改状态(看板)**:拖动卡片到目标列 → 乐观更新立即落位 → 后台 `PATCH` 改 status → 成功确认,失败回滚并提示。

**改项目(跨项目迁移)**:属性栏改 project,或看板 `group_by=project` 下在项目分组间拖拽卡片 → 先弹**迁移预览**(将被映射/清除的字段清单:项目私有 status → 目标项目同 category 默认 status;项目私有 milestone/cycle、项目级 label/自定义字段值将被清除;编号与工作区级字段保留)→ 用户确认后**单事务**完成迁移,未确认不移动(详见 §3.8,README §6.14 两步式契约)。

**批量改优先级**:列表勾选多个 → 底栏出现 → 点优先级 → 选 urgent → 提交 → 行内即时刷新,返回"成功 9,失败 1"。

**建立依赖**:详情页依赖区 → "添加依赖" → 搜索并选目标 issue → 选类型(blocked_by)→ 提交;若成环则就地报错,不创建。

### 4.4 状态机

```
        ┌──────────────────────────────────────────┐
        ▼                                          │
[backlog] → [todo] → [in_progress] → [in_review] → [done]
                        │     ▲
                        ▼     │
                     [blocked](被阻塞/解除)
   任意状态 → [cancelled](取消)
```
- 上图为 **category** 层语义;列内具体 **status** 可自定义(如 in_progress 下"开发中/联调中")。
- 默认无强制顺序,可自由跳转;严格模式可在状态定义上配置"允许的下一步",违反返回 409 `invalid_status_transition`。
- 进入 `done`(category)时写 `completed_at`;离开 done 时清空。
- `state_category` 用于看板列、进度聚合(project.md)、自动化触发(如"进入 in_review 通知 reviewer")。

### 4.5 实时与通知

- **实时**:订阅 `workspace:{ws}:issues` 与 `issue:{id}`;事件见 §3.6。断线依 `seq` 重放;长时间不可用降级 30s 增量轮询。
- **通知触发点**:
  - 被分派 / 取消分派:通知 assignee(人收站内+邮件;agent 收事件触发运行)。
  - 被 @ 提及:同上。
  - 状态流转到 `in_review`/`done`:通知 reporter / 关注者(可配置)。
  - 评论新增:通知参与者(创建人、assignee、曾评论者)。
  - 临近/逾期 `due_date`:提醒 assignee。
  - 依赖解除(被阻塞项完成):通知阻塞方。
  - 子 issue 全部完成:通知父 issue 负责人。

---

## 5. 验收标准

### 5.1 功能性 —— 编号体系

- [ ] 创建 issue 自动生成 `identifier = identifier_namespace_key || '-' || number`(有项目取 `project.key`,无项目取收件箱保留前缀),`number` 在命名空间内单调递增。
- [ ] 并发(≥10)在同一项目创建 issue,编号无重复、无跳号(命中 `uq_issue_namespace_number`,`projects.issue_seq` 原子自增)。
- [ ] **编号并发(README §9 T15)**:同项目 / 无项目并发创建 issue(≥10),`UNIQUE (workspace_id, identifier)`(`uq_issues_identifier`)下无重号、无跳号(除失败回滚)。
- [ ] **唯一约束(双重)**:命名空间级 `UNIQUE (workspace_id, identifier_namespace_key, number)`(`uq_issue_namespace_number`,README §6.3)与工作区级 `UNIQUE (workspace_id, identifier)` 同时生效。
- [ ] **编号命名空间(R2,README §9 T19)**:`identifier_namespace_key`/`number` 创建时固定、永不改变;`WEB-1` 迁入已有 `APP-1` 的项目,`identifier` 不变且 `UNIQUE (workspace_id, identifier_namespace_key, number)` 不违约;迁入/迁出/删除项目后历史 identifier 指向不变。
- [ ] **前缀注册表排他(R2,README §6.3 / §9 T19)**:项目 `key` 与收件箱前缀(含 `retired` 历史前缀)经工作区级 `identifier_prefix_registry`(workspace.md owns)统一排他校验,冲突被拒;变更收件箱前缀后旧前缀永久保留、历史 issue 不重编号。
- [ ] 不同项目计数相互独立,新项目从 1 开始;计数器绑定命名空间,issue 迁移不改变计数器归属。
- [ ] 无项目 issue 使用**工作区级计数器 `workspaces.inbox_issue_seq`**(行锁自增,模式同 `projects.issue_seq`)+ 工作区保留前缀(默认 `WS`,可配),编号形如 `WS-N`。
- [ ] **编号不可变**:issue 在项目间迁移(归入/移出项目)**只改 `project_id`**,`identifier_namespace_key`/`number`/`identifier` 保持不变,不重编号。
- [ ] 软删除 issue 后编号**不复用**:新项目仍取下一个计数器值,被删 `identifier` 永不再分配;项目前缀永久保留(软删除项目后前缀不可复用,README §6.3)。
- [ ] 支持 `GET /issues/by-identifier/WEB-123` 与 UUID 两种寻址,均返回同一 issue。

### 5.2 功能性 —— 双层状态

- [ ] 每个自定义 status 必属且仅属一个 category;`issues.state_category` 始终与 `status_id` 对应 category 一致(无脏数据)。
- [ ] 看板默认按 category 分列,列内按 status `position` 排序。
- [ ] 进度/燃尽聚合基于 `state_category='done'`,与具体 status 名无关。
- [ ] 进入 done 自动写 `completed_at`,离开 done 清空。
- [ ] 严格模式下,配置外的状态转换返回 409 `invalid_status_transition`;默认模式自由流转。
- [ ] 每个作用域存在**唯一** `is_default=true` 状态(由部分表达式唯一索引 `uq_issue_statuses_default` 强制,COALESCE 不写进表级 UNIQUE);新建 issue 未指定 status 时落入默认状态。
- [ ] **至少一个默认状态由事务保证**:取消某状态默认必须与设置新默认在同一事务;工作区/项目创建事务播种默认状态集;服务层自检发现缺失即报警并修复(README §6.3)。
- [ ] 作用域内状态名唯一由 `uq_issue_statuses_name` 部分表达式唯一索引强制(README §6.3)。
- [ ] 删除被引用的 status 前需迁移其下 issue,否则拒绝。

### 5.3 功能性 —— 父子与依赖

- [ ] 父子通过 `parent_id` 自引用建模,**复合自引用 FK** `(workspace_id, parent_id)→issues(workspace_id, id)` ON DELETE CASCADE(README §6.2 第 7 条:显式同租户,不靠"天然");`GET /issues/{id}/children` 返回直接子项;父进度 = 子 done 占比。
- [ ] `parent_id = id` 被 `CHECK` 拒绝;设置祖先为子(深层环)返回 409 `circular_parent`。
- [ ] **并发成环(README §9 T12)**:两事务并发插入 A→B 与 B→A 依赖(或并发设置互为父子),在 `pg_advisory_xact_lock(hashtext('issue_dep_graph:' || workspace_id))` 串行化下**恰一条被拒** `circular_dependency` / `circular_parent`,无环漏网。
- [ ] 父被删除时子级联处理(ON DELETE CASCADE),符合产品策略。
- [ ] 依赖通过 `issue_dependencies` 有向图建模,与父子表分离;依赖两端经复合 FK 强制同工作区(跨租户依赖 INSERT 被拒,README §9 T1)。
- [ ] 新增依赖成环返回 409 `circular_dependency`,且 `details.path` 给出环路径。
- [ ] `blocks`/`blocked_by` 语义对称,查询双向展开;`UNIQUE(issue_id,depends_on_id,type)` 防重复边。
- [ ] 删除 issue 时其依赖边级联清除(ON DELETE CASCADE)。

### 5.4 功能性 —— 分派与成员

- [ ] `assignee_id`/`reporter_id` 一律引用 `members.id`,人类与 agent 对称可选。
- [ ] assignee 非该工作区活跃成员返回 422 `assignee_not_member`。
- [ ] 分派给 agent(`member_type='agent'`)后,事务提交即发出分派事件,agent 运行时可据此接管(与 agent.md 联调通过)。
- [ ] 成员被删除时 `assignee_id`/`reporter_id` 置 NULL(`ON DELETE SET NULL (assignee_id)` / `SET NULL (reporter_id)` 列级,仅置空引用列,`workspace_id` 保持非空,README §6.2 第 6 条,经 §9 T18 实测),issue 不丢失。
- [ ] @提及 agent 与提及人类走同一交互;agent 收提及事件触发运行。

### 5.5 功能性 —— 批量操作

- [ ] `POST /issues/bulk` 支持批量改状态/优先级/assignee/项目/标签/周期与批量软删除。
- [ ] 响应返回 `succeeded`/`failed` 计数;部分失败 HTTP 422 `bulk_partial_failure`,`errors` 逐条给出 `issue_id`+`code`+`message`。
- [ ] 批量操作逐个校验权限,无权限项计入失败,不影响其余项成功。
- [ ] 批量操作产生对应 `issue.updated`/`issue.deleted` 事件与 `issue_activity` 留痕。

### 5.6 功能性 —— 查询与留痕

- [ ] 列表接口支持 §3.2 全部过滤/排序/分组参数,看板/列表/我的任务复用同一端点。
- [ ] 分组查询返回 `groups[].{key,label,count,data}` 与整体 `next_cursor`;**响应不含每组独立 cursor**(README §6.14 整体游标契约)。
- [ ] **过滤限制(README §6.14)**:嵌套深度 >3 或条件数 >20 返回 `400 filter_too_complex`;估算成本/`statement_timeout`(默认 3s)超限返回 `422 query_cost_exceeded`。
- [ ] 自定义字段可作为过滤/分组/排序条件(经 label-property.md 值表)。
- [ ] 每次成功 PATCH 写入 `issue_activity` diff(field/old/new/actor)。
- [ ] 乐观并发:携带过期 `version` 的更新返回 409 `conflict`。

### 5.7 功能性 —— 跨项目迁移(R2,README §9 T22)

- [ ] `POST /issues/{id}/move-preview` 返回将被映射/清除/保留的字段清单:项目私有 status → 目标项目同 category 默认 status(无则取该 category 下 `position` 最小者);项目私有 milestone/cycle、项目级 label、项目级自定义字段值清除;工作区级字段保留。
- [ ] `POST /issues/{id}/move` 未携带 `confirm: true` 返回 422 `move_confirmation_required`,`details.preview` 携带预览清单。
- [ ] 携带确认的迁移在**单事务**完成:`project_id` 变更 + status 映射 + 项目私有字段清除 + `version+1` + `issue_activity` 留痕;迁移后不存在"当前项目 + 旧项目私有字段"脏状态。
- [ ] 迁移前后 `identifier_namespace_key`/`number`/`identifier` 不变(与 §5.1 编号命名空间项联调,README §9 T19)。
- [ ] 乐观并发:携带过期 `version` 的迁移返回 409 `conflict`。
- [ ] 迁移产生 `issue.project_changed` 事件,payload 含 `from_project_id`/`to_project_id`、`mapped_fields`、`cleared_fields`(经 outbox → realtime 唯一写入路径,README §6.6/§6.7)。

### 5.8 非功能性

- [ ] 所有时间字段 `TIMESTAMPTZ`,API 输出 RFC3339 UTC。
- [ ] **真实 DELETE 行为(README §9 T18)**:不止建表成功——实际执行 DELETE 并断言:物理删除/软清理 member 行时 `issues.assignee_id`/`reporter_id` 经 `ON DELETE SET NULL (<列>)` 列级仅置空引用列、`workspace_id` 保持非空;删除 project 时 `issues.project_id` 置空而 `identifier` 不变;删除被 `issues.status_id` 引用的状态被 `RESTRICT` 拒绝;删除父 issue 级联子 issue。所有带 `SET NULL (<列>)` 的复合 FK 逐一覆盖。
- [ ] 所有错误响应使用统一信封 `{"error":{"code","message","details"}}`;错误消息不泄露堆栈/内部敏感信息。
- [ ] 主键 UUID(`gen_random_uuid()`),表名 snake_case 复数,软删除 `deleted_at` 保留编号。
- [ ] 鉴权中间件校验工作区成员资格 + 项目可见性 + 角色;跨工作区/越权访问被拒(403/404)。
- [ ] **跨租户隔离(README §9 T1)**:构造跨 workspace 的复合 FK 插入(如 issue 引用别区 status/member/project)被数据库约束拒绝;A 区凭证访问 B 区 issue 返回 403/404。
- [ ] WebSocket 事件遵循 README §6.7(频道内单调 `seq`、`realtime_events` 重放、`resume_from` / `resync_required`),断线重放不漏不重;客户端按 id 增量合并,不整页刷新。
- [ ] WS 不可用降级为 30s 增量轮询(`since=updated_at`),功能不中断。
- [ ] 写端点限流生效,超限 429 `rate_limited`。
- [ ] 关键查询命中索引(`idx_issues_project_status`/`idx_issues_assignee` 等);列表/分组延迟在 **README §10 基准下**(10 万 issue,热缓存 P95 < 500ms)构成验收标准。
- [ ] 所有用户输入(标题、描述、过滤参数)经 schema 校验,防注入;富文本输出经净化防 XSS。
- [ ] 编号生成、依赖/父子环检测、批量操作均有单元 + 集成测试,核心路径覆盖率 ≥ 80%。
