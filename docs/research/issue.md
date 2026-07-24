# Issue(工作项)调研记录【最核心模块】

> 调研对象:主流团队协作 / 项目管理产品在【Issue / 工作项】模块上的通用设计模式(已匿名化,不指向任何具体产品)。
> 数据模型基准约定:PostgreSQL、UUID 主键、`created_at` / `updated_at`、REST + JSON、游标分页、Bearer token、WebSocket 实时。
> Issue 是整个产品的原子工作单元:看板的卡片、列表的行、被分派/被评论/被流转的对象都是它。AI agent 与人类一样可被设为 assignee(依赖 `member.md` 的统一成员抽象)。

---

## 1. 功能清单

### 1.1 字段全集

| 字段 | 类型语义 | 说明 | 典型场景 |
|------|----------|------|----------|
| `title` | 短文本(必填) | 一句话标题 | "登录页在 Safari 崩溃" |
| `description` | 富文本/Markdown | 详细描述、复现步骤、验收标准 | 写 bug 复现步骤 |
| `status` | 枚举(状态机) | 工作流状态,见 §1.3 | 从 todo → in_progress |
| `priority` | 枚举(可排序) | none/low/medium/high/urgent | 标为 urgent |
| `assignee` | 成员引用(可空) | 负责人(人或 AI agent) | 分派给"代码助手"agent |
| `reporter`/creator | 成员引用 | 创建人 | 谁提的这个 bug |
| `estimate` | 数值 + 单位 | 估算(故事点/小时) | 估 5 个故事点 |
| `due_date` | 日期 | 截止日 | 8/15 前完成 |
| `start_date` | 日期(可选) | 计划开始日 | 排期 |
| `project` | 项目引用(可空) | 归属项目 | 归到"官网改版" |
| `milestone` | 里程碑引用(可空) | 里程碑 | 挂到 v1.0 |
| `cycle` | 周期引用(可空) | 迭代 | 排入第 12 迭代 |
| `labels` | 多对多标签 | 分类标签 | 打"bug""前端" |
| `custom_field_values` | 动态属性 | 自定义字段值,见 label-property.md | 填"严重程度=Major" |
| `parent` | 自引用(可空) | 父 issue(sub-issues) | 大需求拆子任务 |
| `position`/排序值 | 数值 | 看板列内/列表内排序 | 拖拽改顺序 |
| `attachments` | 关联附件 | 文件 | 上传截图 |
| `time_tracking`(可选) | 数值 | 预估/已花时间 | 记录工时 |

### 1.2 编号体系(项目前缀 + 自增号)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 人类可读编号 | `<项目前缀>-<自增号>`,如 `WEB-123` | 沟通时引用"WEB-123" |
| 项目内自增 | 序号在**项目内**单调递增(每个项目独立计数) | 新项目从 1 开始 |
| 双主键 | 内部用 UUID 做外键与 URL,编号仅用于人类引用与搜索 | API 用 UUID,UI 显示编号 |
| 唯一约束 | `(project_id, number)` 唯一 | 不允许两个 WEB-123 |
| 序号生成 | 用 `projects.issue_seq` 计数器 + 行锁/原子自增,保证并发不重号 | 多人同时建 issue 不冲突 |
| 无项目 issue | 未归项目的 issue 可用工作区级前缀或独立序列 | 收件箱里的临时 issue |

**关键设计点(业界标准做法)**:序号生成常见三种实现:
1. 项目表计数器字段 `issue_seq`,`UPDATE ... SET issue_seq = issue_seq + 1 RETURNING issue_seq`(行级锁,简单可靠,高并发下成为热点但多数团队规模够用)。
2. PostgreSQL `SEQUENCE`(每项目一个序列,或用 `nextval` 配合映射),并发友好但管理多序列较繁。
3. 独立 `issue_number_seq(workspace_id, project_id, last_number)` 序列表 + 原子更新。
推荐方案 1 起步,规模化后演进到 3。编号**绝不复用**:issue 删除后其编号废弃(保留行或墓碑),避免引用错乱。

### 1.3 状态机与自定义状态

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 内置状态类别(category) | 固定语义类别:`backlog`/`todo`/`in_progress`/`in_review`/`blocked`/`done`/`cancelled` | 报表按类别聚合"完成率" |
| 自定义状态 | 工作区/项目可在某 category 下自定义具体状态名(如"开发中""测试中"都属 in_progress) | 团队把 in_progress 细分为"开发中/联调中" |
| 状态属性 | 名称、所属 category、颜色、排序 | 给"测试中"配蓝色 |
| 状态流转 | 任意状态可切到任意状态(默认无强制限制),或配置允许的转换 | 默认自由流转;严格团队可限制"必须经过 in_review 才能 done" |
| 完成判定 | category=done 视为完成,用于进度/燃尽计算 | 项目进度按 done 占比 |
| 默认状态 | 新建 issue 的默认状态(通常 backlog 或 todo) | 新 issue 默认进 backlog |

**category 与 status 分离的设计要点**:category 是系统稳定语义(用于聚合、看板默认列、自动化触发),status 是用户可自定义的展示层。这样用户能自由命名状态,而进度计算、看板分组等逻辑仍稳定可用。

### 1.4 父子(sub-issues)与依赖关系

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 父子关系 | 一个 issue 可有多个 sub-issue;sub-issue 自身也可有子(支持多层,通常建议≤2-3 层) | Epic 拆成多个 story,story 再拆 task |
| 父进度聚合 | 父 issue 进度 = 子 issue 完成占比 | Epic 显示"3/5 完成" |
| 依赖关系 | issue 之间可声明 `blocks` / `blocked_by`(或 `relates_to`) | "上线"被"压测"阻塞 |
| 依赖类型 | 常见:blocks、blocked by、relates to、duplicates | A blocks B(B 依赖 A) |
| 依赖可视化 | 详情页列出阻塞/被阻塞项;可选关系图 | 看到"还差 2 个前置未完成" |
| 循环依赖检测 | 建立依赖时校验不成环 | 阻止 A→B→A |

**关键设计点**:父子用自引用外键 `parent_id`(树);依赖用独立关联表(有向图,多对多)。两者分开:父子是"组成关系"(强,级联/聚合),依赖是"顺序关系"(弱,仅约束)。

### 1.5 批量操作

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 多选 | 列表/看板勾选多个 issue | 选中 10 个 issue |
| 批量改字段 | 批量改状态/优先级/assignee/项目/标签/周期 | 把选中的 10 个都标为 high |
| 批量删除/归档 | 批量软删除或归档 | 清理一批无效 issue |
| 操作结果反馈 | 返回成功/失败计数,部分失败给出原因 | "成功 9,失败 1(权限不足)" |
| 撤销(可选) | 批量操作后短时撤销 | 误操作回滚 |

---

## 2. 数据模型

### 2.1 核心实体

#### `issue_statuses`(自定义状态定义)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK ON DELETE CASCADE | — | |
| `project_id` | UUID | NULL, FK ON DELETE CASCADE | NULL | NULL=工作区级状态;非空=项目私有状态 |
| `name` | TEXT | NOT NULL | — | 如"测试中" |
| `category` | TEXT | NOT NULL, CHECK IN ('backlog','todo','in_progress','in_review','blocked','done','cancelled') | — | 稳定语义类别 |
| `color` | TEXT | NULL | NULL | |
| `position` | REAL/INT | NOT NULL | `0` | 同 category 内排序 |
| `is_default` | BOOLEAN | NOT NULL | `false` | 是否为新建默认状态 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:`UNIQUE (workspace_id, project_id, name)`(project_id 可空,用 COALESCE 唯一索引)。

#### `issues`(工作项)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 内部主键 |
| `workspace_id` | UUID | NOT NULL, FK ON DELETE CASCADE | — | 隔离键 |
| `project_id` | UUID | NULL, FK→projects(id) ON DELETE SET NULL | NULL | 归属项目 |
| `number` | BIGINT | NOT NULL | — | 项目内自增号 |
| `identifier` | TEXT | NOT NULL | — | 冗余的人类编号 `WEB-123`(便于搜索/展示) |
| `title` | TEXT | NOT NULL | — | 1–255 |
| `description` | TEXT | NULL | NULL | 富文本/Markdown |
| `status_id` | UUID | NOT NULL, FK→issue_statuses(id) | — | 当前状态 |
| `state_category` | TEXT | NOT NULL | — | 冗余自 status.category,加速聚合/筛选 |
| `priority` | TEXT | NOT NULL, CHECK IN ('none','low','medium','high','urgent') | `'none'` | |
| `assignee_id` | UUID | NULL, FK→workspace_members(id) ON DELETE SET NULL | NULL | 人或 agent |
| `reporter_id` | UUID | NULL, FK→workspace_members(id) ON DELETE SET NULL | NULL | |
| `estimate` | NUMERIC | NULL | NULL | 估算值 |
| `estimate_unit` | TEXT | NULL, CHECK IN ('points','hours') | NULL | |
| `due_date` | DATE | NULL | NULL | |
| `start_date` | DATE | NULL | NULL | |
| `milestone_id` | UUID | NULL, FK→milestones(id) ON DELETE SET NULL | NULL | |
| `cycle_id` | UUID | NULL, FK→cycles(id) ON DELETE SET NULL | NULL | |
| `parent_id` | UUID | NULL, FK→issues(id) ON DELETE CASCADE | NULL | 父 issue |
| `position` | REAL | NOT NULL | `0` | 排序值(看板列内/列表) |
| `completed_at` | TIMESTAMPTZ | NULL | NULL | 进入 done 的时间 |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除(编号保留) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:
- `UNIQUE (project_id, number)`(项目内编号唯一)。
- `CHECK (state_category 与 status_id 同步)`(由服务层保证,或触发器维护)。
- 父子防自环:`CHECK (parent_id <> id)`;更深层环检测在服务层(建/改 parent 时遍历)。

#### `issue_dependencies`(依赖关系,有向)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `issue_id` | UUID | NOT NULL, FK→issues(id) ON DELETE CASCADE | 主体 |
| `depends_on_id` | UUID | NOT NULL, FK→issues(id) ON DELETE CASCADE | 被依赖项 |
| `type` | TEXT | NOT NULL, CHECK IN ('blocks','blocked_by','relates_to','duplicates') DEFAULT 'relates_to' | |
| `created_at` | TIMESTAMPTZ | NOT NULL | |
| UNIQUE | `(issue_id, depends_on_id, type)` | |

#### `issue_custom_field_values`(自定义字段值,EAV)

> 详见 `label-property.md`。简表:

| 字段 | 类型 | 约束 |
|------|------|------|
| `id` | UUID | PK |
| `issue_id` | UUID | NOT NULL, FK ON DELETE CASCADE |
| `field_def_id` | UUID | NOT NULL, FK→custom_field_defs(id) ON DELETE CASCADE |
| `value_text` | TEXT | NULL |
| `value_number` | NUMERIC | NULL |
| `value_date` | TIMESTAMPTZ | NULL |
| `value_member_id` | UUID | NULL, FK→workspace_members(id) |
| `value_json` | JSONB | NULL | 枚举/多选等结构化值 |
| UNIQUE | `(issue_id, field_def_id)` |

#### `issue_activity`(变更留痕,可选)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | PK |
| `issue_id` | UUID | FK |
| `actor_member_id` | UUID | 操作者(人或 agent) |
| `field` | TEXT | 变更字段 |
| `old_value` / `new_value` | TEXT/JSONB | 变更前后 |
| `created_at` | TIMESTAMPTZ | |

### 2.2 实体关系(ER)

```
projects ──1:N──► issues ──N:1──► issue_statuses
                   │  │                (status.category 冗余到 issues.state_category)
                   │  ├──N:1──► workspace_members(assignee/reporter)
                   │  ├──N:1──► cycles / milestones
                   │  ├──自引用 parent_id(树:sub-issues)
                   │  ├──M:N──► labels(via issue_labels)
                   │  ├──1:N──► issue_custom_field_values
                   │  ├──1:N──► comments
                   │  └──M:N──► issue_dependencies(有向图)
```

### 2.3 关键索引

```sql
CREATE UNIQUE INDEX uq_issue_number ON issues(project_id, number);
CREATE INDEX idx_issues_workspace ON issues(workspace_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_project_status ON issues(project_id, state_category) WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_assignee ON issues(assignee_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_issues_parent ON issues(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_issues_cycle ON issues(cycle_id) WHERE cycle_id IS NOT NULL;
CREATE INDEX idx_issues_due ON issues(due_date) WHERE due_date IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX idx_issues_position ON issues(project_id, state_category, position);
-- 全文/标识搜索
CREATE INDEX idx_issues_identifier ON issues(workspace_id, identifier);
CREATE INDEX idx_issue_deps_issue ON issue_dependencies(issue_id);
CREATE INDEX idx_issue_deps_on ON issue_dependencies(depends_on_id);
```

### 2.4 编号生成(伪代码)

```sql
-- 在事务内,行锁项目行并自增
UPDATE projects SET issue_seq = issue_seq + 1
 WHERE id = $project_id
 RETURNING issue_seq;            -- 得到 number
-- identifier = project.key || '-' || number
INSERT INTO issues (id, workspace_id, project_id, number, identifier, title, status_id, ...)
VALUES ($uuid, $ws, $project_id, $number, $key||'-'||$number, $title, $status, ...);
```

---

## 3. 接口设计

REST 基础路径 `/api/v1`,Bearer token,游标分页。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/workspaces/{ws}/issues` | 创建 issue |
| GET | `/workspaces/{ws}/issues` | 列表(强过滤/排序/分组,见下) |
| GET | `/issues/{id}` | 获取(支持 UUID 或 `by-identifier/WEB-123`) |
| PATCH | `/issues/{id}` | 更新字段(含状态流转) |
| DELETE | `/issues/{id}` | 软删除 |
| GET | `/issues/{id}/children` | 子 issue 列表 |
| GET/POST/DELETE | `/issues/{id}/dependencies` | 依赖关系管理 |
| POST | `/issues/bulk` | 批量操作 |
| GET | `/workspaces/{ws}/statuses` | 状态定义列表 |
| POST/PATCH/DELETE | `/workspaces/{ws}/statuses[...]` | 自定义状态 CRUD |
| GET | `/issues/{id}/activity` | 变更历史 |

**列表查询参数**(强大且统一,看板/列表/我的任务都复用):
- 过滤:`status`、`state_category`、`priority`、`assignee_id`、`project_id`、`cycle_id`、`milestone_id`、`label`、`due_before/after`、`q`(搜索 title/identifier)、自定义字段过滤。
- 排序:`sort=position|created_at|priority|due_date&order=asc|desc`。
- 分组:`group_by=state_category|assignee|priority|project|label`(看板/分组列表用)。
- 分页:`limit`、`cursor`。

### 3.2 请求/响应示例

**创建 issue** `POST /api/v1/workspaces/{ws}/issues`
```json
// Request
{
  "title": "登录页在 Safari 崩溃",
  "description": "复现步骤:...",
  "project_id": "prj_1",
  "priority": "high",
  "assignee_id": "mem_b2",          // 可以是 AI agent 成员
  "estimate": 5, "estimate_unit": "points",
  "due_date": "2026-08-15",
  "label_ids": ["lbl_bug"],
  "parent_id": null
}

// 201 Response
{
  "id": "iss_uuid", "identifier": "WEB-124", "number": 124,
  "title": "登录页在 Safari 崩溃",
  "status": { "id": "st_todo", "name": "Todo", "category": "todo" },
  "priority": "high",
  "assignee": { "id": "mem_b2", "name": "代码助手", "member_type": "agent" },
  "estimate": 5, "due_date": "2026-08-15",
  "created_at": "2026-07-24T10:00:00Z"
}
```

**状态流转** `PATCH /api/v1/issues/{id}`
```json
{ "status_id": "st_in_progress" }
// 200:返回更新后的 issue;completed_at 在进入 done 时自动写入
```

**批量操作** `POST /api/v1/issues/bulk`
```json
// Request
{ "issue_ids": ["iss_1","iss_2","iss_3"], "changes": { "priority": "urgent", "assignee_id": "mem_a1" } }
// 200 Response
{ "succeeded": 3, "failed": 0, "errors": [] }
```

**依赖** `POST /api/v1/issues/{id}/dependencies`
```json
{ "depends_on_id": "iss_9", "type": "blocked_by" }
// 201;若形成环 → 409 circular_dependency
```

**分组查询(看板用)** `GET /api/v1/workspaces/{ws}/issues?group_by=state_category&project_id=prj_1`
```json
{
  "groups": [
    { "key": "todo", "label": "Todo", "count": 3, "data": [ { "id": "...", "identifier": "WEB-124", "title": "..." } ] },
    { "key": "in_progress", "label": "In Progress", "count": 2, "data": [ ... ] }
  ],
  "next_cursor": null
}
```

### 3.3 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | title 缺失/超长;非法 priority |
| 403 | `forbidden` | 无权限改该 issue(私有项目/角色不足) |
| 404 | `not_found` | issue 不存在/不可见 |
| 409 | `circular_dependency` | 依赖成环 |
| 409 | `circular_parent` | 父子成环 |
| 409 | `invalid_status_transition` | 启用了流转限制且该转换不允许 |
| 422 | `assignee_not_member` | assignee 不是该工作区有效成员 |
| 422 | `bulk_partial_failure` | 批量部分失败(详情在 errors) |
| 429 | `rate_limited` | 限流 |

### 3.4 分页与鉴权

- **分页**:游标分页,游标编码 `(position 或 created_at, id)`,在分组查询中每组各自带 cursor 或整体游标(实现可二选一,推荐整体游标 + group 元信息)。
- **鉴权**:工作区成员可读可见 issue;写需项目写权限或工作区 member 及以上;批量操作逐个校验权限,失败的计入 errors。
- 乐观并发(推荐):更新可带 `If-Match: <updated_at>` 或 `version`,冲突返回 409,避免拖拽/编辑互相覆盖。

---

## 4. UI 设计

### 4.1 信息架构

```
我的任务(/inbox)        —— assignee=我 的 issue
项目 → Issue 列表/看板   —— 见 kanban.md
Issue 详情(全屏或右侧抽屉)
   ├── 头部:标题(可编辑)、identifier、状态选择器、操作菜单
   ├── 主体:描述(富文本)、子 issue 列表、依赖列表、活动流、评论区
   └── 属性侧栏:assignee、reporter、priority、estimate、due/start、project、cycle、milestone、labels、自定义字段
```

### 4.2 关键组件

- **issue 行/卡片**:显示 identifier、标题、状态色条、优先级图标、assignee 头像(人/agent 区分)、到期日、标签点。
- **快速创建**:列表顶部"C"或"+ 新建"弹出轻量表单(标题 + 可选展开更多字段),支持连续创建。
- **属性内联编辑**:侧栏每个字段点击即编辑(assignee 弹成员选择器含 agent;priority 弹图标菜单;日期弹日历)。
- **状态选择器**:下拉按 category 分组列出所有自定义状态,带颜色。
- **子 issue 区**:树状展示,父显示完成进度;支持就地新增子任务。
- **依赖区**:列出 blocks / blocked by,点击跳转;阻塞项未完成时给出视觉提示。
- **批量操作工具条**:勾选后浮出底栏(改状态/优先级/assignee/标签/删除/取消选)。

---

## 5. UX 设计

### 5.1 关键交互流程

**创建 issue**:按 `C` → 输入标题(回车快速创建,或 Tab 展开填项目/assignee/优先级)→ 保存 → 自动分配编号 `WEB-124` → 出现在对应项目/看板。

**分派给 AI agent**:属性栏 assignee → 选择器混合列出人类与 agent(各带类型图标)→ 选 agent → 保存 → agent 收到分派事件并接管。

**拖拽改状态(看板)**:拖动卡片到目标列 → 乐观更新立即落位 → 后台 `PATCH` 改 status → 成功则确认,失败则回滚并提示。

**批量改优先级**:列表勾选多个 → 底栏出现 → 点优先级 → 选 urgent → 提交 → 行内即时刷新,返回成功/失败计数。

### 5.2 状态流转图(文字描述)

```
        ┌──────────────────────────────────────────┐
        ▼                                          │
[backlog] → [todo] → [in_progress] → [in_review] → [done]
                        │     ▲
                        ▼     │
                     [blocked](被阻塞/解除)
   任意状态 → [cancelled](取消)
```
- 默认无强制顺序,可自由跳转;严格模式可在状态定义上配置"允许的下一步"。
- 进入 `done`(category)时写 `completed_at`;离开 done 时清空。
- `state_category` 用于看板列、进度聚合、自动化触发(如"进入 in_review 时通知 reviewer")。

### 5.3 实时性方案

- WebSocket 订阅:`workspace:{ws}:issues`(列表级)、`issue:{id}`(详情级)。
- 事件:`issue.created`、`issue.updated`(含变更字段 diff)、`issue.deleted`、`issue.moved`(看板位置/状态变化)、`comment.added`、`dependency.changed`。
- 看板拖拽采用 **乐观更新 + 服务端确认 + 失败回滚**;并发冲突由 `updated_at` 版本检测。
- 列表/收件箱收到 `issue.updated` 时做增量合并(按 id 更新行),而非整页刷新。
- 降级:WS 断开时 30s 轮询列表(带 `since=updated_at` 增量拉取)。

### 5.4 通知触发点

- 被分派 / 取消分派:通知 assignee(人收站内+邮件;agent 收事件触发运行)。
- 被 @ 提及:同上。
- 状态流转到 `in_review`/`done`:通知 reporter / 关注者(可配置)。
- 评论新增:通知参与者(创建人、assignee、曾评论者)。
- 临近/逾期 due_date:提醒 assignee。
- 依赖解除(被阻塞项完成):通知阻塞方。
- 子 issue 全部完成:通知父 issue 负责人。
