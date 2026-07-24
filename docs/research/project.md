# 项目(Project)调研记录

> 调研对象:主流团队协作 / 项目管理产品在【项目】模块上的通用设计模式(已匿名化,不指向任何具体产品)。
> 数据模型基准约定:PostgreSQL、UUID 主键、`created_at` / `updated_at`、REST + JSON、游标分页、Bearer token、WebSocket 实时。

---

## 1. 功能清单

### 1.1 项目实体

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 创建项目 | 名称、标识/前缀、描述、负责人、起止日期、图标/颜色 | PM 新建"官网改版"项目,指定前缀 `WEB` |
| 项目归档/删除 | 完成后归档(只读保留)或删除(软删除) | 项目交付后归档,不再出现在活跃列表 |
| 项目成员/可见性 | 项目级成员与权限;私有项目仅成员可见 | 把"官网改版"设为私有,仅相关同事可见 |
| 项目描述/文档 | 富文本描述、目标、链接 | 在项目首页写背景与目标 |
| 项目分组(可选) | 按团队/主题分组或打标签 | 把所有市场类项目归入"市场"分组 |

### 1.2 项目状态 / 健康度

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 项目状态 | 如 `planning` / `active` / `paused` / `completed` / `cancelled` | PM 把项目从 planning 切到 active |
| 健康度(traffic light) | 红/黄/绿(on_track / at_risk / off_track)+ 文字说明 | PM 每周更新健康度为"有风险",并写原因 |
| 状态更新留痕 | 每次健康度/状态变更生成一条"项目更新"记录(含作者、时间、说明) | 领导查看项目历史健康度变化 |
| 自动进度 | 依据 issue 完成率自动计算进度百分比 | 项目页显示"62% 完成(基于子 issue)" |

**关键设计点**:健康度是"人工判断 + 自动进度"并存。自动进度由 issue 聚合算出,健康度由人填写并附说明;两者一起呈现给管理者。

### 1.3 里程碑(Milestone)

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 定义里程碑 | 名称、目标日期、描述 | 设定"v1.0 上线"里程碑,目标 8/31 |
| 里程碑与 issue 关联 | issue 可挂到里程碑;里程碑进度=其下 issue 完成率 | 把关键 issue 挂到里程碑,看完成度 |
| 里程碑时间线 | 在甘特/时间线视图上展示 | 在路线图上看各里程碑节点 |
| 逾期标记 | 目标日期已过但未完成自动标红 | 里程碑过期未完成,显示逾期 |

### 1.4 项目与 issue 的归属

- issue **必属于一个工作区**,通常**属于一个项目**(项目可空 → 收进"收件箱/未归档")。
- 项目是 issue 编号前缀的来源(见 `issue.md`:如 `WEB-123`)。
- 项目聚合视图:项目页展示其下所有 issue 的看板/列表/时间线。
- 项目级默认值:新建 issue 时默认继承项目的状态集、默认 assignee 等。

### 1.5 迭代 / 周期(Cycle / Sprint)

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 定义周期 | 名称、起止日期(常为固定长度,如 2 周) | 建立"第 12 迭代",8/1–8/14 |
| 周期模板/自动滚动 | 按固定节奏自动生成下一周期 | 每两周自动开一个新迭代 |
| issue 入周期 | 把 issue 分配到某周期(承诺本迭代完成) | 站会上把 5 个 issue 拖入本迭代 |
| 周期进度 | 已完成/总点数(或 issue 数)、燃尽 | 看本迭代燃尽图 |
| 未完成的处理 | 周期结束时未完成 issue 自动移到下一周期或退回待办 | 迭代收尾,未完成的顺延 |
| 周期 vs 项目 | 周期是时间盒,项目是目标盒;issue 可同时属于一个项目和一个周期 | issue "登录优化"属于"官网项目"且排在"第12迭代" |

---

## 2. 数据模型

### 2.1 核心实体

#### `projects`(项目)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | |
| `name` | TEXT | NOT NULL | — | 1–120 字符 |
| `key` | TEXT | NOT NULL | — | 项目前缀/标识,大写,如 `WEB` |
| `description` | TEXT | NULL | NULL | 富文本/Markdown |
| `icon` | TEXT | NULL | NULL | 图标标识 |
| `color` | TEXT | NULL | NULL | 主题色 |
| `status` | TEXT | NOT NULL, CHECK IN ('planning','active','paused','completed','cancelled') | `'planning'` | |
| `health` | TEXT | NULL, CHECK IN ('on_track','at_risk','off_track') | NULL | 健康度 |
| `visibility` | TEXT | NOT NULL, CHECK IN ('public','private') | `'public'` | |
| `lead_member_id` | UUID | NULL, FK→workspace_members(id) ON DELETE SET NULL | — | 项目负责人(统一成员) |
| `start_date` | DATE | NULL | NULL | |
| `target_date` | DATE | NULL | NULL | 目标完成日 |
| `issue_seq` | BIGINT | NOT NULL | `0` | 该项目 issue 自增序号计数器(见 issue.md) |
| `archived_at` | TIMESTAMPTZ | NULL | NULL | 归档时间 |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:`UNIQUE (workspace_id, key)`;`UNIQUE (workspace_id, name) WHERE deleted_at IS NULL`(可选)。

#### `project_updates`(项目状态/健康度更新留痕)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `project_id` | UUID | NOT NULL, FK ON DELETE CASCADE | |
| `author_member_id` | UUID | NOT NULL, FK→workspace_members(id) | |
| `health` | TEXT | NULL | 本次填写的健康度 |
| `status` | TEXT | NULL | 本次状态 |
| `message` | TEXT | NULL | 说明文字 |
| `created_at` | TIMESTAMPTZ | NOT NULL | |

#### `milestones`(里程碑)

| 字段 | 类型 | 约束 | 默认值 |
|------|------|------|--------|
| `id` | UUID | PK | `gen_random_uuid()` |
| `project_id` | UUID | NOT NULL, FK ON DELETE CASCADE | — |
| `title` | TEXT | NOT NULL | — |
| `description` | TEXT | NULL | NULL |
| `target_date` | DATE | NULL | NULL |
| `state` | TEXT | NOT NULL, CHECK IN ('open','closed') | `'open'` |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |

#### `cycles`(迭代 / 周期)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK ON DELETE CASCADE | — | 周期常为工作区级 |
| `project_id` | UUID | NULL, FK ON DELETE CASCADE | NULL | 若周期绑定到项目则填 |
| `name` | TEXT | NOT NULL | — | 如"第 12 迭代" |
| `starts_at` | DATE | NOT NULL | — | |
| `ends_at` | DATE | NOT NULL | — | |
| `state` | TEXT | NOT NULL, CHECK IN ('planned','active','completed') | `'planned'` | |
| `auto_roll` | BOOLEAN | NOT NULL | `false` | 是否自动滚动生成下一周期 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:`CHECK (ends_at >= starts_at)`。

#### `project_members`(项目成员 / 可见性)

| 字段 | 类型 | 约束 |
|------|------|------|
| `id` | UUID | PK |
| `project_id` | UUID | NOT NULL, FK ON DELETE CASCADE |
| `member_id` | UUID | NOT NULL, FK→workspace_members(id) ON DELETE CASCADE |
| `role` | TEXT | NOT NULL, CHECK IN ('lead','member','viewer') DEFAULT 'member' |
| UNIQUE | `(project_id, member_id)` |

### 2.2 实体关系(ER)

```
workspaces ──1:N──► projects ──1:N──► issues
                       │                  │ N:1
                       ├──1:N──► milestones◄┘ (issue.milestone_id)
                       ├──1:N──► project_updates
                       ├──1:N──► project_members ──► workspace_members
                       └── (issues 也可 N:1 关联 cycles)
workspaces ──1:N──► cycles
```

### 2.3 关键索引

```sql
CREATE UNIQUE INDEX uq_projects_key ON projects(workspace_id, key) WHERE deleted_at IS NULL;
CREATE INDEX idx_projects_workspace ON projects(workspace_id, status) WHERE deleted_at IS NULL AND archived_at IS NULL;
CREATE INDEX idx_projects_lead ON projects(lead_member_id);
CREATE INDEX idx_milestones_project ON milestones(project_id, state);
CREATE INDEX idx_cycles_workspace ON cycles(workspace_id, starts_at);
CREATE INDEX idx_cycles_state ON cycles(workspace_id, state);
CREATE INDEX idx_project_updates_project ON project_updates(project_id, created_at DESC);
```

### 2.4 项目进度聚合(派生)

进度通常不持久化,而是查询时聚合:
```sql
SELECT
  COUNT(*) FILTER (WHERE i.state_category='done') * 1.0 / NULLIF(COUNT(*),0) AS progress
FROM issues i WHERE i.project_id = $1 AND i.deleted_at IS NULL;
```
或在 issue 变更时增量更新 `projects.progress_cache`(物化字段)以降低高频读开销。

---

## 3. 接口设计

REST 基础路径 `/api/v1`,Bearer token,游标分页。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/workspaces/{ws}/projects` | 创建项目 |
| GET | `/workspaces/{ws}/projects` | 列出项目(过滤 status/archived) |
| GET | `/projects/{id}` | 获取项目(含进度聚合) |
| PATCH | `/projects/{id}` | 更新字段/状态/健康度 |
| DELETE | `/projects/{id}` | 软删除 |
| POST | `/projects/{id}/archive` | 归档 |
| POST | `/projects/{id}/updates` | 提交一条状态/健康度更新 |
| GET | `/projects/{id}/updates` | 历史更新列表 |
| GET/POST/PATCH/DELETE | `/projects/{id}/milestones[...]` | 里程碑 CRUD |
| GET/POST/PATCH | `/workspaces/{ws}/cycles[...]` | 周期 CRUD |
| POST | `/projects/{id}/members` | 添加项目成员 |

### 3.2 请求/响应示例

**创建项目** `POST /api/v1/workspaces/{ws}/projects`
```json
// Request
{ "name": "官网改版", "key": "WEB", "target_date": "2026-08-31", "visibility": "public" }

// 201 Response
{
  "id": "prj_1", "name": "官网改版", "key": "WEB", "status": "planning",
  "health": null, "progress": 0.0, "issue_seq": 0,
  "target_date": "2026-08-31", "created_at": "2026-07-24T10:00:00Z"
}
```

**获取项目(含进度)** `GET /api/v1/projects/prj_1`
```json
{
  "id": "prj_1", "name": "官网改版", "key": "WEB",
  "status": "active", "health": "at_risk",
  "progress": 0.62, "open_issues": 15, "done_issues": 25,
  "lead": { "id": "mem_a1", "name": "Jane Doe" },
  "milestones": [ { "id": "ms_1", "title": "v1.0 上线", "target_date": "2026-08-31", "state": "open" } ]
}
```

**提交健康度更新** `POST /api/v1/projects/prj_1/updates`
```json
{ "health": "at_risk", "message": "第三方接口延期,存在上线风险" }
// 201:返回该更新记录;同时更新 projects.health
```

**列出周期** `GET /api/v1/workspaces/{ws}/cycles?state=active`
```json
{ "data": [ { "id": "cyc_12", "name": "第 12 迭代", "starts_at": "2026-08-01", "ends_at": "2026-08-14", "state": "active" } ], "next_cursor": null }
```

### 3.3 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | key 非大写/超长;ends_at < starts_at |
| 403 | `forbidden` | 私有项目非成员访问 |
| 404 | `not_found` | 项目不存在 |
| 409 | `project_key_taken` | 前缀已被占用 |
| 409 | `cycle_overlap`(可选) | 同范围周期重叠 |
| 422 | `project_archived` | 对已归档项目写入 |

### 3.4 分页与鉴权

- 项目列表/更新历史/里程碑均游标分页。
- 鉴权:工作区成员可读 public 项目;private 项目需 project_members 命中。写操作需项目 member/lead 或工作区 admin。

---

## 4. UI 设计

### 4.1 信息架构

```
项目列表页(/projects)
   ├── [筛选: 状态 / 负责人 / 我参与的]  [+ 新建项目]
   └── 项目卡片网格/列表:名称+图标 | 状态徽章 | 健康度灯 | 进度条 | 负责人头像 | 目标日期

项目详情页(/projects/{id})
   ├── 头部:名称/状态/健康度/进度/负责人/目标日期  [更新状态] [···]
   ├── Tab: 概览 | Issue(看板/列表) | 里程碑 | 时间线 | 更新动态
   └── 侧栏: 字段、成员、设置
```

### 4.2 关键组件

- **健康度灯**:红/黄/绿三色圆点 + 文字,点击展开"更新状态"表单(选健康度 + 写说明)。
- **进度条**:基于 issue 完成率的环形/条形进度。
- **里程碑时间线**:横向时间轴,节点=里程碑,标注目标日与完成度,逾期标红。
- **周期切换器**:看板/列表上方下拉,选"第 12 迭代"即按周期过滤 issue;周期页头部显示燃尽与点数。
- **项目状态徽章**:planning/active/paused/completed 用不同颜色标签。

---

## 5. UX 设计

### 5.1 关键交互流程

**创建项目**:新建 → 填名称(自动建议大写 key)→ key 实时去重校验 → 选负责人/目标日/可见性 → 完成,进入空项目页。

**更新健康度**:项目头 → 点健康度灯 → 选红/黄/绿 + 写说明 → 提交 → 头部灯即时更新,动态 Tab 新增一条留痕。

**周期排期**:进入周期页 → 从"待办/未排期"区把 issue 拖入本周期 → 周期进度与燃尽实时更新 → 周期结束触发"未完成 issue 顺延下一周期"提示。

### 5.2 状态流转(项目)

```
planning ──启动──► active ──完成──► completed
   │                 │  ▲
   │                 ▼  │
   └──取消──► cancelled   paused(暂停/恢复)
```

### 5.3 实时性方案

- WebSocket 订阅 `project:{id}` 与 `workspace:{ws}:projects` 频道。
- 事件:`project.updated`(状态/健康度/进度)、`project.update_added`(新动态)、`milestone.updated`、`cycle.updated`。
- 进度聚合在 issue 状态变更时计算并经 `project.updated` 广播,项目页进度条实时刷新。

### 5.4 通知触发点

- 被设为项目负责人/加入项目:站内通知。
- 健康度变为 `at_risk`/`off_track`:通知负责人与关注者(可选邮件)。
- 里程碑临近/逾期:提醒负责人。
- 周期开始/结束:通知周期内 issue 的相关成员。
