# 看板与视图(Kanban & Views)功能 Spec

> **所属层**:视图与分类层(Presentation / Projection Layer)。本层是 issue 之上的**只读投影 + 写入入口**,不持久化 issue 集合,只持久化"如何投影"的配置。
> **依赖 Spec**:
> - `issue.md`(Issue 工作项)——视图投影的原子对象;状态机、`state_category`、`position`、`PATCH /issues/{id}` 均来自此。
> - `label-property.md`(标签与自定义属性)——提供可作为筛选/分组/排序依据的 `label` 与自定义字段。
> - `project.md`(项目)、`member.md`(统一成员抽象,含 AI agent)。
> **文档性质**:可直接指导开发的实现规格;与全局约定冲突时以 [README.md](../README.md) §6「全局权威契约」为准。

---

## 全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)

1. **存储**:PostgreSQL 16+;snake_case 复数表名;UUID 主键(`gen_random_uuid()`);所有表含 `created_at` / `updated_at`(`TIMESTAMPTZ NOT NULL DEFAULT now()`,UTC)。
2. **成员**:`views.owner_member_id` 等成员引用一律指向 `members.id`,模型以 README §6.1 为唯一权威;存储层不设冗余 `*_type` 判别列。
3. **多租户**:`views` / `view_issue_positions` 等持有跨模块引用的表一律按 README §6.2 存 `workspace_id` 并建**复合 FK + 目标表 `UNIQUE(workspace_id, id)`**(views/issues/members 均建该唯一键供引用)。
4. **issue 状态双层**:`issue_statuses.category`(稳定语义)+ `issue_statuses.name`(可自定义展示名),冗余到 `issues.state_category`(issue.md owns)。**看板列默认按 `state_category` 分组,可切换为按具体 `status_id` 分组**(映射见 §2.4)。
5. **接口**:基础路径 `/api/v1`;`Authorization: Bearer <token>`;包络 / 游标分页(**分组查询统一整体游标,不给每组独立 cursor**)/ 错误信封 / 乐观并发 / **过滤限制(嵌套 ≤3、条件 ≤20、`filter_too_complex` / `query_cost_exceeded`)** 见 README §6.14。
6. **实时**:统一实时契约见 README §6.7(**频道内**单调 `seq`、`realtime_events` 持久重放、`resume_from` / `resync_required`、订阅逐资源授权);事件名 `<entity>.<action>`。
7. **性能基准**:一切 P95 / 时延指标仅在 README §10 基准下构成验收标准。
8. **集成测试**:跨租户复合 FK 拒绝、并发拖入 WIP 列、乐观冲突等按 README §9 矩阵(T1/T9 等)必测。
9. **ORM**:SQLAlchemy 2.x 约定(`DeclarativeBase` / `Mapped` / `mapped_column`)。

---

## 1. 功能描述

### 1.1 定位

视图(View)是同一批 issue 的可保存"投影":用户配置好**筛选(filters)+ 分组(group)+ 排序(sort)+ 显示字段(display)+ 看板专属设置(board_settings)** 后存为命名视图,可在看板(board)、列表(list)等 layout 间复用。视图**不存储 issue 集合**——每次打开视图,按其配置实时查询 issues 合成结果。看板是 `layout='board'` 的视图,把 issue 按分组渲染为可拖拽的列与卡片。

实时性是本模块的核心体验:他人改状态/拖拽/改字段,所有打开同一视图的成员**按视图 filters 做增量合并**(插入/移动/移除单张卡片),而非整板刷新。

### 1.2 功能点与场景

#### 看板(Board,`layout='board'`)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 列 = 分组值 | 每列对应一个分组值;默认 `group_by=state_category`,每列 = 一个状态类别 | 列:Todo / In Progress / In Review / Done |
| 拖拽改状态 | 跨列拖卡片即修改其 `status_id`(落入目标列对应状态) | 把卡片拖进 In Progress |
| 列内排序 | 同列内拖拽改 `issues.position`(浮点中点法,见 §4.3) | 把最紧急卡片拖到列顶 |
| 分组切换 | `group_by` 可选 `state_category`(默认)/`status`/`assignee`/`priority`/`project`/`label`/自定义字段 | 按 assignee 分列看 workload |
| 子分组(泳道) | `sub_group_by` 横向二级分组(如 project / priority) | 泳道按项目,列按状态 |
| 筛选 | 按 assignee/priority/label/due/自定义字段过滤卡片 | 只看 high 优先级 |
| 排序 | 列内按 `position`/`priority`/`due_date`/`created_at` 排序 | 按到期日排序 |
| WIP 限制 | 给列设最大在制品数;超限软警告(`warn`)或硬阻止(`block`) | In Progress 限 5,超出标红/拒收 |
| 折叠列 | `collapsed_columns` 折叠某列节省空间 | 折叠 Done 列 |
| 卡片字段显示 | `card_fields` 配置卡片展示哪些字段 | 卡片显示估点 + 子任务 3/5 |
| 快速创建 | 列底"+ 新增卡片"就地建 issue(继承该列分组值) | 在 Todo 列底快速加卡片 |
| 在制品计数 | 列头显示 `count` 及 WIP `4/5` | "In Progress 4/5" |

#### 列表视图(List,`layout='list'`)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 表格化展示 | 行=issue,列=`display_fields` 配置 | 看所有 issue 的结构化清单 |
| 分组 | 按 status/assignee/project/label/自定义字段分组,组头可折叠 | 按状态分组列表 |
| 内联编辑 | 单元格点击即改字段(走 `PATCH /issues/{id}`) | 行内改优先级 |
| 多选批量 | 勾选多行批量操作(走 `POST /issues/bulk`,见 issue.md) | 批量改状态 |
| 列头排序 | 点列头排序 | 按到期日升序 |
| 自定义列 | 选择显示列、调整列宽/顺序 | 加"估点"列 |

#### 保存的视图(Saved Views)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 保存视图 | 把 layout + filters + group + sort + display + board_settings 存为命名视图 | 存"本迭代高优先级看板" |
| 作用域 | `visibility='private'`(仅 owner)/`'shared'`(工作区或项目成员可见) | 团队共享"冲刺看板" |
| 列表/切换 | 侧栏列出可用视图,一键切换 | 在"我的任务""冲刺看板"间切换 |
| 默认视图 | `is_default` 设某视图为工作区/项目默认 | 项目首页默认打开"看板" |
| 编辑/复制/删除/排序 | 视图 CRUD 与 `position` 排序 | 删除废弃视图 |
| 临时(未保存)视图 | 改了配置但未保存,提示"是否保存/另存/丢弃" | 临时筛一下,不污染共享视图 |

#### 实时更新

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 卡片实时移动 | 他人改状态/拖拽,本地即时反映 | 看到同事把卡片拖进 Done |
| 字段实时刷新 | 改 assignee/优先级,所有打开该视图者即时看到 | 分派后头像立刻更新 |
| 新建/删除实时 | 新 issue 出现、删除消失 | 新建卡片即刻出现在 Todo 列 |
| 协作感知(可选) | `view.presence` 显示谁也在这个看板上 | 看到谁也在这个看板上 |

### 1.3 边界与非目标

- **视图层不定义状态机**。状态流转规则、`completed_at` 写入、依赖校验全部由 issue.md 负责;视图只把状态机可视化,并对"进入某 category"动作施加 WIP 约束。
- **视图不持久化 issue 集合**。不存在 `view_issues` 这类关联表;issue 是否属于视图,永远由查询期执行 filters 决定。
- **不在本期范围**:时间线/甘特(`layout='timeline'`)与表格高级透视(`layout='table'` 的复杂聚合)仅在枚举中预留取值,不实现 UI;跨视图的 issue 全局排序;离线编辑。
- **不重复实现筛选字段语义**。label / 自定义字段的定义与值校验在 label-property.md;本模块只消费它们作为 filters/group/sort 的输入。

---

## 2. 数据模型

### 2.1 实体关系(ER)

```
workspaces ──1:N──► views ◄──N:1── projects(可选;project_id 可空=工作区级视图)
                     │
                     ├── owner: members(创建者,复合 FK)
                     ├── 1:N──► board_wip_limits(可选独立表;亦可内嵌 board_settings.wip)
                     ├── 1:N──► view_issue_positions(每视图手工排序,见 §2.7)
                     └── 查询期按 filters/group/sort 投影 issues —— 无持久外键,合成结果
                                        │
                                        ▼
                                   issues(见 issue.md;`issues.position` 是规范默认排序,
                                          视图内手工拖拽排序写 view_issue_positions,不写 issues.position)

realtime_channel_cursors(每频道游标,可选,见 §2.6)—— 工作区/成员级,非视图所有:
   按 (workspace_id, member_id, channel) 记录跨设备断线续传游标;真源为 realtime_events。
```

### 2.2 `views`(保存的视图)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键 |
| `project_id` | UUID | NULL,**复合 FK** `(workspace_id, project_id)→projects(workspace_id, id)` ON DELETE CASCADE | NULL | NULL=工作区级视图 |
| `owner_member_id` | UUID | NOT NULL,**复合 FK** `(workspace_id, owner_member_id)→members(workspace_id, id)` ON DELETE CASCADE | — | 创建者 |
| `name` | TEXT | NOT NULL, CHECK (`char_length(name) BETWEEN 1 AND 100`) | — | 视图名 |
| `layout` | TEXT | NOT NULL, CHECK IN (`'board'`,`'list'`,`'timeline'`,`'table'`) | `'board'` | 视图类型 |
| `visibility` | TEXT | NOT NULL, CHECK IN (`'private'`,`'shared'`) | `'private'` | 私有/共享 |
| `filters` | JSONB | NOT NULL | `'{}'` | 筛选条件(结构见 §2.3) |
| `group_by` | TEXT | NULL | NULL | 分组字段;NULL 时 board 默认 `state_category` |
| `sub_group_by` | TEXT | NULL | NULL | 泳道二级分组 |
| `sort` | JSONB | NOT NULL | `'[]'` | 排序规则数组 |
| `display_fields` | JSONB | NOT NULL | `'[]'` | 展示字段/列配置 |
| `board_settings` | JSONB | NOT NULL | `'{}'` | 看板专属(列序、折叠、卡片字段、内嵌 WIP) |
| `position` | REAL | NOT NULL | `0` | 视图在侧栏列表中的排序 |
| `is_default` | BOOLEAN | NOT NULL | `false` | 是否默认视图 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**(可执行 DDL,见 §2.8;COALESCE 表达式不能写进表级 `UNIQUE`,一律用部分表达式唯一索引,README §6.3):
- 同一作用域内默认视图唯一:`CREATE UNIQUE INDEX uq_views_default ON views (workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000')) WHERE is_default;`(服务层在同事务内保证"取消旧默认 + 设新默认")。
- 目标表唯一键:`UNIQUE (workspace_id, id)`(供 `view_issue_positions.view_id` 等复合 FK 引用,README §6.2)。
- `layout IN ('timeline','table')` 可写入但本期 UI 不渲染(返回 501 由前端兜底为 board/list)。

### 2.3 `filters` / `sort` / `board_settings` 结构

```jsonc
// filters —— 组合条件,支持 AND/OR 递归嵌套;空对象 {} 表示不过滤
{
  "operator": "AND",                       // "AND" | "OR"
  "conditions": [
    { "field": "state_category", "op": "in",  "value": ["todo","in_progress"] },   // 内置字段
    { "field": "priority",       "op": "in",  "value": ["high","urgent"] },
    { "field": "assignee_id",    "op": "eq",  "value": "mem_a1" },
    { "field": "due_date",       "op": "lte", "value": "2026-08-31" },
    { "field": "label",          "op": "in",  "value": ["lbl_bug"] },              // 标签
    // 自定义字段:field_kind=custom_field + field_def_id;值形态见 label-property.md §2.1
    { "field_kind": "custom_field", "field_def_id": "cf_sev", "op": "eq", "value": "opt_major" },
    // 嵌套分组(可选,任意深度)
    { "operator": "OR", "conditions": [
        { "field": "priority", "op": "eq", "value": "urgent" },
        { "field": "due_date", "op": "lt", "value": "2026-07-31" } ] }
  ]
}
```

**内置可筛选字段集**:`state_category`、`status_id`、`priority`、`assignee_id`、`reporter_id`、`project_id`、`cycle_id`、`milestone_id`、`due_date`、`start_date`、`created_at`、`updated_at`、`label`(经 `issue_labels` 多对多)、`parent_id`、`q`(title/identifier 搜索,仅 `op=contains`)。

**操作符 `op` 集**:`eq` / `neq` / `in` / `not_in` / `lt` / `lte` / `gt` / `gte` / `is_null` / `is_not_null` / `contains`(仅文本)。服务层按字段类型校验合法 op(如 `label` 只允许 `in`/`not_in`)。

```jsonc
// sort —— 有序数组,前者优先;自定义字段同样用 field_def_id
[ { "field": "position",   "order": "asc"  },
  { "field": "created_at", "order": "desc" } ]

// board_settings —— 看板专属
{
  "columns": ["backlog","todo","in_progress","in_review","done"],   // 列序;group_by=state_category 时为 category 值
  "collapsed_columns": ["done"],
  "card_fields": ["labels","estimate","due_date","sub_issue_progress","assignee"],
  "wip": { "in_progress": { "limit": 5, "enforcement": "warn" } }   // 内嵌 WIP;独立表见 §2.5
}
```

### 2.4 列分组映射(group_by → 列)

| `group_by` | 列来源 | 列 `key` | 列 `label` | 拖入改 |
|------------|--------|----------|-----------|--------|
| `state_category`(默认/NULL) | 7 个固定类别 | category 值,如 `in_progress` | 类别显示名 | `status_id` → 目标 category 的默认 status |
| `status` | `issue_statuses` 行 | `status_id` | status.name | `status_id` → 该列 status |
| `assignee` | 成员 | `member_id`(含 `__none__`) | 成员名 | `assignee_id` |
| `priority` | 5 档 | priority 值 | 档位名 | `priority` |
| `project` | 项目 | `project_id`(含 `__none__`) | 项目名 | `project_id`(**跨项目迁移协议**:迁移前预览并要求确认 + 单事务完成字段映射/清除,见 §3.2 与 issue.md §3.8,README §6.14 跨项目迁移契约;**不是裸改 `project_id`**) |
| `label` | 标签 | `label_id` | 标签名 | 增/删 `issue_labels` |
| 自定义字段(`field_def_id`) | 字段值 | 序列化值(枚举为 option_id) | 值显示名 | 该字段值 |

> **默认状态映射**:`group_by=state_category` 时,把卡片拖入某列 = 把 `status_id` 改为该 category 下 `is_default=true` 的 `issue_statuses` 行;若该 category 无默认 status,取 `position` 最小者。前端在 `GET /views/{id}/issues` 响应里拿到 `column_target_status` 映射,拖拽时直接 PATCH 目标 `status_id`。

### 2.5 `board_wip_limits`(可选独立表)

> 简单实现可只用 `views.board_settings.wip` JSONB;需要按状态精确审计/独立权限时用独立表。**两者并存时独立表优先**。

| 字段 | 类型 | 约束 | 默认值 |
|------|------|------|--------|
| `id` | UUID | PK | `gen_random_uuid()` |
| `view_id` | UUID | NOT NULL, FK→views(id) ON DELETE CASCADE | — |
| `group_key` | TEXT | NOT NULL | — | 列 key(对齐 §2.4,如 `in_progress`) |
| `limit` | INT | NOT NULL, CHECK (`limit > 0`) | — |
| `enforcement` | TEXT | NOT NULL, CHECK IN (`'warn'`,`'block'`) | `'warn'` |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| UNIQUE | `(view_id, group_key)` | | |

### 2.6 每频道游标 `realtime_channel_cursors`(在线订阅游标,可选)

> **R2 修正**:原"单视图单游标"的视图级在线订阅游标(每视图记一个 last-seen seq)**已删除**。`seq` 一律**按频道**单调(README §6.7),而一个视图会同时消费 `workspace:{ws}:issues` / `project:{id}` / `issue:{id}` 等多个频道——"单视图单游标"对这些跨频道事件**没有语义**,故废除"每视图一个总游标"的设计。
>
> **默认方案**:客户端**按频道各自**记录 `last_seq`,断线重连带 `resume_from=<last_seq+1>` 逐频道补齐(README §6.7);服务端无需为在线订阅持久化游标。

下表为**可选**的服务端跨设备游标持久化(同一成员在多设备间共享断点续传定位),非在线订阅所必需:

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键(复合 FK 本地列) |
| `member_id` | UUID | NOT NULL,**复合 FK** `(workspace_id, member_id)→members(workspace_id, id)` ON DELETE CASCADE | — | 游标所属成员(同租户复合 FK,README §6.2) |
| `channel` | TEXT | NOT NULL | — | 频道名(如 `issue:{id}`,对齐 README §6.7) |
| `last_seq` | BIGINT | NOT NULL | `0` | 该成员在该频道已确认消费的最大 `seq` |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| UNIQUE | `(workspace_id, member_id, channel)` | | | 每成员每频道至多一条游标 |

> 本表**仅用于跨设备断线续传定位**,**不是真源**——重放真源为 `realtime_events`(README §6.7);游标过旧(早于保留窗口)仍按 §3.5 / README §6.7 下发 `resync_required` 走 REST 对账。

### 2.7 `view_issue_positions`(每视图手工排序,README §6.14)

**单一 `issues.position` 会跨视图互相污染**(一个视图的拖拽改变所有视图的顺序)。为此引入**每视图、每 issue 的手工排序表**:每个视图各自保存其卡片顺序,互不影响。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键(复合 FK 本地列) |
| `view_id` | UUID | NOT NULL,**复合 FK** `(workspace_id, view_id)→views(workspace_id, id)` ON DELETE CASCADE | — | 所属视图 |
| `issue_id` | UUID | NOT NULL,**复合 FK** `(workspace_id, issue_id)→issues(workspace_id, id)` ON DELETE CASCADE | — | 卡片对应 issue |
| `group_key` | TEXT | NOT NULL | `''` | 卡片所在分组键(对齐 §2.4;空串=未分组/默认) |
| `position` | REAL | NOT NULL | `0` | 该视图内、该分组内的手工排序值(浮点中点法,见 §4.3) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| UNIQUE | `(view_id, issue_id)` | | | 每视图每 issue 至多一条排序记录 |

**语义**:
- `view_issue_positions` 记录的是**某个视图内**某 issue 的手工顺序;**视图 A 的拖拽只写视图 A 的行,不影响视图 B**(README §6.14 排序契约)。
- **未保存排序记录的视图**(或某 issue 在当前视图无行)回退到 `issues.position` **规范默认排序** / 视图 `sort` 配置;即"手工排序优先,缺省回退规范顺序"。
- **`issues.position` 不再被视图拖拽写入**——它只是全局规范默认排序(由 issue 创建/规范排序维护),视图内拖拽一律 upsert 本表。
- 视图删除时其排序行级联清除(ON DELETE CASCADE);issue 删除同理。

### 2.8 关键索引

```sql
CREATE INDEX idx_views_workspace  ON views(workspace_id, position);
CREATE INDEX idx_views_project    ON views(project_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_views_owner      ON views(owner_member_id);
CREATE INDEX idx_views_visibility ON views(workspace_id, visibility);
CREATE UNIQUE INDEX uq_views_default
  ON views(workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'))
  WHERE is_default;
CREATE UNIQUE INDEX uq_views_ws_id ON views(workspace_id, id);   -- 供复合 FK 引用(README §6.2)
CREATE INDEX idx_board_wip_view ON board_wip_limits(view_id);
-- 每视图手工排序
CREATE UNIQUE INDEX uq_vip_view_issue ON view_issue_positions(view_id, issue_id);
CREATE INDEX idx_vip_view_group_pos ON view_issue_positions(view_id, group_key, position);
-- 视图内 issue 查询命中 issue.md 既有索引(idx_issues_project_status / idx_issues_position 等);
-- 自定义字段筛选命中 label-property.md 的 idx_icfv_value_json (GIN)。
```

### 2.9 SQLAlchemy 2.x 模型(节选)

```python
import uuid
from datetime import datetime
from sqlalchemy import String, Text, REAL, Boolean, ForeignKey, ForeignKeyConstraint, \
    UniqueConstraint, Index, CheckConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB, TIMESTAMPTZ
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class View(Base):
    __tablename__ = "views"
    __table_args__ = (
        CheckConstraint("layout IN ('board','list','timeline','table')", name="ck_views_layout"),
        CheckConstraint("visibility IN ('private','shared')", name="ck_views_visibility"),
        UniqueConstraint("workspace_id", "id", name="uq_views_ws_id"),  # 供复合 FK 引用
        # 复合 FK(README §6.2)
        ForeignKeyConstraint(["workspace_id", "project_id"],
                             ["projects.workspace_id", "projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["workspace_id", "owner_member_id"],
                             ["members.workspace_id", "members.id"], ondelete="CASCADE"),
        # 部分表达式唯一索引:每作用域唯一默认视图(README §6.3)
        Index("uq_views_default", "workspace_id",
              text("COALESCE(project_id, '00000000-0000-0000-0000-000000000000')"),
              unique=True, postgresql_where=text("is_default")),
    )
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True,
                                          server_default=text("gen_random_uuid()"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)  # 复合 FK 见 __table_args__
    owner_member_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)   # 复合 FK 见 __table_args__
    name: Mapped[str] = mapped_column(Text, nullable=False)
    layout: Mapped[str] = mapped_column(Text, nullable=False, server_default="board")
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default="private")
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    group_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    sub_group_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    display_fields: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    board_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    position: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False,
        server_default=text("now()"), onupdate=text("now()"))
    wip_limits: Mapped[list["BoardWipLimit"]] = relationship(
        back_populates="view", cascade="all, delete-orphan")

class ViewIssuePosition(Base):
    """每视图手工排序(README §6.14):视图 A 的拖拽不污染视图 B。"""
    __tablename__ = "view_issue_positions"
    __table_args__ = (
        UniqueConstraint("view_id", "issue_id", name="uq_vip_view_issue"),
        Index("idx_vip_view_group_pos", "view_id", "group_key", "position"),
        ForeignKeyConstraint(["workspace_id", "view_id"],
                             ["views.workspace_id", "views.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["workspace_id", "issue_id"],
                             ["issues.workspace_id", "issues.id"], ondelete="CASCADE"),
    )
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True,
                                          server_default=text("gen_random_uuid()"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    view_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)    # 复合 FK 见 __table_args__
    issue_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)   # 复合 FK 见 __table_args__
    group_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    position: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False,
        server_default=text("now()"), onupdate=text("now()"))
```

> 写入路径(JSONB 配置)用 Pydantic v2 schema 严格校验后再 `jsonb` 落库;`filters` 校验失败返回 `400 validation_error`,绝不把未校验 JSON 直接交给查询编译器(防注入,见 §3.4)。

---

## 3. 接口设计

REST 基础路径 `/api/v1`,`Authorization: Bearer <token>`,游标分页。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspaces/{ws}/views` | 列出可见视图(本人私有 + 工作区/项目共享) |
| POST | `/workspaces/{ws}/views` | 创建视图 |
| GET | `/views/{id}` | 获取视图配置 |
| PATCH | `/views/{id}` | 更新视图配置(乐观并发,见 §3.4) |
| DELETE | `/views/{id}` | 删除视图 |
| POST | `/views/{id}/duplicate` | 复制视图(新 owner = 当前成员) |
| GET | `/views/{id}/issues` | 执行视图配置,返回分组/排序后的 issue |
| PATCH | `/views/{id}/wip` | 设置某列 WIP 限制 |
| POST | `/views/{id}/moves` | **看板拖拽的原子 move 命令**(乐观锁 + advisory lock + WIP 校验 + 状态变更 + 每视图排序 upsert,单事务,见 §3.2/§4.3;`to_group_key` 为 project 分组值时触发**跨项目迁移协议**:预览→确认→单事务映射/清除,见 §3.2) |
| POST | `/views/{id}/reorder` | 仅调整某视图内卡片顺序(不改状态、不跨列;走 `view_issue_positions`) |
| PATCH | `/workspaces/{ws}/views/reorder` | 调整视图在侧栏的顺序(`position`) |

> **拖拽必须走 move 命令**:`POST /views/{id}/moves` 是唯一能执行**视图级 WIP 限制**的写路径(它带 `view_id`,服务端可在事务内按视图 filters 计数目标列并强制 WIP)。**不带 `view_id` 的 `PATCH /issues/{id}`(issue.md)无法感知视图,不能执行视图级 WIP**——因此 UI 跨列拖拽**必须**用 move 命令;`PATCH /issues/{id}` 仅用于**非拖拽**的字段编辑(详情侧栏改 assignee/priority/状态等)。列内纯排序亦可走 `POST /views/{id}/reorder`。

### 3.2 请求/响应示例

**创建看板视图** `POST /api/v1/workspaces/{ws}/views`
```jsonc
// Request
{
  "name": "本迭代高优先级看板", "layout": "board", "visibility": "shared",
  "project_id": "prj_1",
  "filters": { "operator": "AND", "conditions": [
    { "field": "cycle_id",  "op": "eq", "value": "cyc_12" },
    { "field": "priority",  "op": "in", "value": ["high","urgent"] } ] },
  "group_by": "state_category",
  "board_settings": {
    "wip": { "in_progress": { "limit": 5, "enforcement": "warn" } },
    "card_fields": ["labels","estimate","assignee"] }
}
// 201 Response —— 返回完整视图对象(含生成的 id、created_at/updated_at)
```

**执行视图** `GET /api/v1/views/{id}/issues?limit=100`
```jsonc
// 分组查询统一整体游标(README §6.14):每组只给 count(组内总数)+ data(当前页切片),
// 顶层 next_cursor 驱动下一页;响应中【不含每组独立 cursor】。
{
  "layout": "board",
  "group_by": "state_category",
  "column_target_status": { "todo": "st_todo", "in_progress": "st_in_progress", "done": "st_done" },
  "groups": [
    { "key": "todo", "label": "Todo", "count": 3, "wip": null,
      "data": [ { "id": "iss_1", "identifier": "WEB-124", "title": "...", "position": 1.0,
                 "status": { "id": "st_todo", "name": "Todo", "category": "todo" } } ] },
    { "key": "in_progress", "label": "In Progress", "count": 4,
      "wip": { "limit": 5, "enforcement": "warn" }, "data": [ /* ... */ ] }
  ],
  "next_cursor": null
}
```

**看板拖拽(原子 move)** `POST /api/v1/views/{id}/moves`
```jsonc
// Request —— 一次拖拽 = 一个事务(乐观锁 + advisory lock + WIP 计数 + 状态变更 + 排序 upsert)
{ "issue_id": "iss_1", "to_group_key": "in_progress", "position": 2.5, "version": 7 }
// 服务端在同一事务内:
//  (a) 乐观锁 issue(WHERE id=$1 AND version=$version;不匹配 → 409 conflict)
//  (b) SELECT pg_advisory_xact_lock(hashtext('wip:' || view_id || ':' || to_group_key))  -- 串行化目标列
//  (c) 按视图 filters 计数目标分组当前成员数
//  (d) 强制 WIP:block 且 count>=limit → 422 wip_limit_exceeded;warn → 放行并发 wip_exceeded 事件
//  (e) 按 column_target_status[to_group_key] 改 status_id + upsert view_issue_positions(view_id,issue_id,group_key,position)
// 200 Response
{ "data": { "id": "iss_1", "status": { "id": "st_in_progress", "category": "in_progress" },
            "position": 2.5, "version": 8, "updated_at": "2026-07-24T10:00:01Z" } }
// 目标列 enforcement=block 且已满 → 422 wip_limit_exceeded(details: group_key/limit/count)
// version 与服务端不符 → 409 conflict
```

> **不要再用 `PATCH /issues/{id}` 拖拽**:不带 `view_id` 的 PATCH 无法执行视图级 WIP(见 §3.1)。`PATCH /issues/{id}` 仅供详情侧栏等**非拖拽**字段编辑。

**跨项目拖拽(`group_by=project`)** `POST /api/v1/views/{id}/moves`

> `group_by=project` 视图把卡片拖到**另一项目**列时,`to_group_key` 即目标 `project_id`,本命令为 issue.md §3.8 跨项目迁移契约的**视图侧入口**:先预览并要求确认,再单事务完成迁移(README §6.14 两步式契约)。
```jsonc
// Request —— group_by=project 视图,把卡片拖到另一项目列
{ "issue_id": "iss_1", "to_group_key": "prj_app", "position": 1.5, "version": 7 }
// 未确认 → 422(客户端先展示预览并要求确认)
{ "error": { "code": "move_confirmation_required",
  "message": "跨项目迁移将影响以下字段,请确认后重试",
  "details": { "preview": { "mapped_fields": [ {"field":"status","from":"st_web_dev","to":"st_app_todo"} ],
    "cleared_fields": [ {"field":"milestone_id"}, {"field":"cycle_id"},
      {"field":"labels","items":["lbl_web_only"]}, {"field":"custom_field_values","items":["cf_web_severity"]} ],
    "kept_fields": ["priority","due_date","assignee_id","workspace 级标签/字段"] } } } }
// 确认(携 confirm:true)→ 单事务迁移(乐观锁 + project_id 变更 + status 映射 + 清除项目私有字段 + 排序 upsert)
{ "issue_id": "iss_1", "to_group_key": "prj_app", "position": 1.5, "version": 7, "confirm": true }
// 200 Response
{ "data": { "id": "iss_1", "project_id": "prj_app",
            "status": { "id": "st_app_todo", "category": "todo" },
            "position": 1.5, "version": 8,
            "move_result": { "mapped_fields": [...], "cleared_fields": [...] } } }
```

> **迁移语义以 issue.md §3.8 为权威**:项目私有 status → 目标项目同 category 默认 status;项目私有 milestone/cycle/label/自定义字段值清除;工作区级字段保留;迁移产生 `issue.project_changed` 事件携带映射/清除清单(经 outbox → realtime 唯一写入路径,README §6.6/§6.7)。本节 moves 命令在 `group_by=project` 时为其视图侧入口,未确认返回 422 `move_confirmation_required`;`dry_run: true` 等价 issue.md 的 `move-preview`(仅返回预览不落库)。

**设置 WIP** `PATCH /api/v1/views/{id}/wip`
```jsonc
{ "group_key": "in_progress", "limit": 5, "enforcement": "block" }
// 200:返回更新后的 board_wip_limits / 同步写入 board_settings.wip
```

### 3.3 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 非法 filters/layout/op;字段类型与 op 不匹配 |
| 400 | `filter_too_complex` | filters 嵌套深度 >3 或条件数 >20(README §6.14) |
| 401 | `unauthorized` | 缺失/失效 token |
| 403 | `forbidden` | 编辑他人私有视图;对共享视图无写权限 |
| 404 | `not_found` | 视图不存在或不可见 |
| 409 | `conflict` | 乐观并发版本不符(move `version` / `If-Match`);默认视图重复设置 |
| 422 | `wip_limit_exceeded` | 硬 WIP 限制下拖入已满列(`details` 含 `group_key`/`limit`/`count`) |
| 422 | `move_confirmation_required` | 跨项目拖拽未确认(`details` 含字段映射/清除预览,README §6.14;见 §3.2 与 issue.md §3.8) |
| 422 | `query_cost_exceeded` | 视图查询估算成本/`statement_timeout` 超限(README §6.14,建议收窄条件) |
| 429 | `rate_limited` | 限流 |
| 501 | `not_implemented` | 请求 `layout IN ('timeline','table')` 的渲染 |

### 3.4 分页、鉴权与安全

- **分页(README §6.14 整体游标契约)**:视图列表游标分页,游标编码 `(position, id)`;视图内 issue 执行后亦游标分页——**分组场景统一整体游标**:响应顶层为 `{ "groups": [{key,label,count,wip?,data}], "next_cursor": ... }`,`count` 为组内总数,`data` 为当前页切片;**不得**在响应中给每组独立 `cursor`(与 issue.md 统一此契约)。
- **过滤限制(README §6.14)**:视图 filters **最大嵌套深度 3、最大条件数 20**;服务端以 `statement_timeout`(默认 3s)+ 估算查询成本兜底;超限返回 `400 filter_too_complex`,成本超限返回 `422 query_cost_exceeded`(建议收窄条件)。
- **鉴权**:私有视图仅 `owner_member_id` 可见可写;`shared` 视图工作区成员可读,写需 owner 或具备共享视图写权限(工作区 admin / 项目 lead)。执行视图(`GET /views/{id}/issues`)时,服务端**再次**按成员可见范围裁剪 issues(不暴露其无权 issue)。
- **乐观并发**:`PATCH /views/{id}` 与拖拽 `PATCH /issues/{id}` 支持 `If-Match: <updated_at>`;版本不符返回 `409 conflict`,客户端拉取最新后重试或提示。
- **JSONB 安全**:`filters`/`sort` 经白名单 Pydantic schema 校验,`field` 必须落在内置字段集或为合法 `field_def_id`;查询编译时一律参数化绑定,杜绝从 JSON 拼接 SQL。
- **统一错误信封**:
```jsonc
{ "error": { "code": "wip_limit_exceeded",
             "message": "目标列已达 WIP 上限,无法移入",
             "details": { "group_key": "in_progress", "limit": 5, "count": 5 } } }
```

### 3.5 WebSocket 增量合并事件

> 实时契约**以 README §6.7 为唯一权威**:`seq` 为**频道内**单调递增(非全局)、`realtime_events` 持久重放、`resume_from` 重连补齐、游标过旧下发 `resync_required` + REST 对账水位、订阅逐资源授权。本节仅描述看板视图的频道与增量合并动作,不重复定义 seq 语义。

- **连接**:`wss://<host>/ws`(握手鉴权;**禁止在 URL query 中传 token**,使用连接建立后首帧认证单一机制,README §6.16/§6.7/auth.md);建立后服务端推送的所有帧统一为(`seq` 为频道内单调,README §6.7):
```jsonc
{ "seq": 1042, "type": "issue.updated", "topic": "view:{view_id}",
  "ts": "2026-07-24T10:00:01Z", "data": { /* 见各事件 */ } }
```
- **订阅**:客户端发 `{ "op": "subscribe", "topic": "view:{view_id}" }`;亦可订阅底层 `workspace:{ws}:issues` 由客户端自行按视图过滤。
- **重放(README §6.7)**:客户端**按频道**记录 `last_seq`(每频道游标,§2.6);断线重连后发 `{ "op": "resume", "resume_from": <last_seq+1> }`,服务端从 `realtime_events` 顺序补发缺口;`resume_from` 早于保留窗口则回 `{ "op": "resync_required", "watermark": <最大 seq>, "rest": "<对账 REST URL>" }`,客户端整板重拉 `GET /views/{id}/issues` 对账后无感恢复。
- **事件清单**(`<entity>.<action>`):

| 事件 | data(关键字段) | 客户端增量动作 |
|------|------------------|----------------|
| `issue.created` | issue 全量 | 按视图 filters 判定:命中 → 插入对应列;否则忽略 |
| `issue.updated` | `id` + 变更字段 diff + 新 `updated_at` | 按 filters 重判:仍命中 → 就地更新/跨列移动;不再命中 → 移除 |
| `issue.moved` | `id`、`from_group`、`to_group`、`position` | 精确移动单卡;`updated_at` 旧于本地则丢弃(防回退) |
| `issue.deleted` | `id` | 若在视图内 → 移除卡片 |
| `view.updated` | 视图配置 diff | 配置变更:若 filters/group/sort 变 → 整板重拉;仅 card_fields 变 → 局部刷新 |
| `view.presence` | `view_id`、成员列表(可选) | 渲染协作者头像(事件名取 README §6.7 词汇注册表) |

- **增量合并原则**:收到 `issue.*` 后,客户端用**当前视图 filters** 在本地重判该 issue 归属,做单卡插入/移动/移除,**禁止整板刷新**(仅 `view.updated` 改到投影规则或收到 `resync_required` 时才整板重拉)。
- **降级**:WS 断开 → 30s 轮询 `GET /views/{id}/issues?since=<最大 updated_at>` 增量拉取。

---

## 4. UI/UX

### 4.1 信息架构

```
左侧栏(视图导航)
   ├── 视图列表(图标 + 名称):看板 / 列表 / 我的任务 / 冲刺看板 …
   ├── [+ 新建视图]
   └── 视图操作(右键/…):重命名、复制、设默认、删除、调序

主区(看板布局 layout=board)
   ├── 顶部工具条:视图名 | 筛选 | 分组 | 排序 | 显示字段 | [保存/另存]
   ├── (可选)横向泳道(sub_group_by)
   └── 列容器(横向滚动)
        每列:列头(状态色 + 名称 + 计数 + WIP) → 卡片堆叠(纵向滚动) → 列底"+ 新增"

主区(列表布局 layout=list)
   ├── 顶部工具条(同上)
   └── 表格:可配置列 / 列头排序 / 行内编辑 / 复选批量条
```

### 4.2 关键组件

- **视图切换器**:侧栏视图列表;当前视图高亮;切换时 URL 同步 `/views/{id}`(可分享/收藏);未保存改动时切换弹"保存/另存/丢弃"。
- **看板列**:列头含状态色、名称、计数(及 WIP `4/5`;超限 warn 变黄、block 变红);列体可滚动;支持折叠(`collapsed_columns`)。
- **卡片**:`identifier` + 标题 + 状态色条 + assignee 头像(人/agent 区分) + 标签点 + 估点 + 子任务进度;悬停出快捷操作;显示内容受 `card_fields` 控制。
- **筛选器弹层**:多条件组合(字段 + 操作符 + 值),字段下拉自动列出内置字段 + 所有自定义字段(枚举字段渲染为选项多选);实时预览命中数;支持 AND/OR 嵌套。
- **分组/排序下拉**:选 `group_by`(默认 state_category)、`sub_group_by`(泳道)、`sort` 字段与方向。
- **视图保存条**:配置改动未保存时,工具条出现"保存 / 另存为新视图 / 丢弃"。

### 4.3 拖拽:乐观更新 + 版本校验 + position 浮点中点法

**跨列拖拽(改状态)**
1. 鼠标按住卡片 → 拖向目标列(目标列高亮;若 WIP 将超限,列头实时预警:warn 黄色、block 红色 + 禁用落点)。
2. 松手 → **乐观落位**:UI 立即把卡片渲染进目标列。
3. 计算 `position`(列内插入点)→ 发 **`POST /views/{id}/moves`** 携 `{ issue_id, to_group_key, position, version }`(原子 move 命令,见 §3.2;服务端单事务完成乐观锁 + advisory lock + WIP 计数 + `status_id` 变更 + `view_issue_positions` upsert)。**不再用 `PATCH /issues/{id}` 拖拽**——它不带 `view_id`,无法执行视图级 WIP。
4. 成功 → 用响应的新 `version`/`updated_at` 更新本地版本;失败处理:
   - `422 wip_limit_exceeded`(block)→ **卡片弹回原列** + toast 提示。
   - `409 conflict`(他人同时改了该卡)→ 拉最新 issue,按服务端结果收敛(后到事件覆盖)。

**跨项目拖拽(`group_by=project`)**
1. 在 `group_by=project` 视图把卡片拖向**另一项目**列 → 松手后**不直接落位**,而是弹出**迁移预览模态**:列出将被**映射**的 status(项目私有 status → 目标项目同 category 默认 status)、将被**清除**的项目私有 milestone/cycle/标签/自定义字段值,以及保留的工作区级字段(预览来自 `moves` 未确认返回的 422 `details.preview`,或先以 `dry_run: true` 取预览,见 §3.2 / issue.md §3.8)。
2. 用户确认 → 发 `POST /views/{id}/moves` 携 `{ issue_id, to_group_key:<目标 project_id>, position, version, confirm: true }` → 服务端**单事务**完成 `project_id` 变更 + status 映射 + 清除项目私有字段 + 排序 upsert。
3. 成功 → 卡片落位目标项目列,响应携带 `move_result`(映射/清除清单),UI 明确呈现"拖入项目后哪些字段为何变化"对用户可见;`422 move_confirmation_required`(未确认)→ **卡片弹回原列** + 重新展示预览;`409 conflict` → 拉最新收敛。迁移语义以 issue.md §3.8 为权威(README §6.14 两步式契约),迁移产生 `issue.project_changed` 事件。

**列内排序(浮点中点法,写入 `view_issue_positions`)**
- 拖到两张相邻卡片 `A`(position=pA)与 `B`(pB)之间 → 新 `position = (pA + pB) / 2`。
- 拖到列顶 → `position = first.position - 1.0`;拖到列底 → `position = last.position + 1.0`。
- 经 `POST /views/{id}/moves`(同列,`to_group_key` 不变)或 `POST /views/{id}/reorder` upsert **当前视图的** `view_issue_positions(view_id, issue_id, group_key, position)`;**不写 `issues.position`**——一个视图的排序不污染其它视图(README §6.14)。服务端广播 `issue.moved`(payload 带 `view_id`)。
- **精度耗尽**:当 `|pB - pA|` 小于阈值(如 `1e-6`,REAL 精度逼近)时,触发该视图该列**整列重排**——服务端按当前顺序重新分配整数间隔序列(如 1.0, 2.0, 3.0 …)写回 `view_issue_positions`,广播全列 `issue.moved`。

**实时一致性**:服务端按 `version`/`updated_at` 版本仲裁;客户端丢弃 `updated_at` 旧于本地缓存的事件,保证多人同时拖同一卡片时 UI 平滑收敛到最新写。

### 4.4 WIP 软警告 / 硬阻止

| enforcement | 拖入将超限时 | 落位后超限 | 视觉 |
|-------------|--------------|-----------|------|
| `warn`(软) | 允许拖入,列头预警 | 卡片正常落位 | 列头红色徽章 `6/5` + 顶部 toast 提示 |
| `block`(硬) | 落点禁用,提示"已达上限" | move 命令(`POST /views/{id}/moves`)在事务内计数后返回 `422 wip_limit_exceeded` → 卡片弹回 | 列头红色 + 拖拽过程禁用高亮 |

- 列头计数实时 = 当前可见卡片数 / WIP `limit`;超限 warn 黄、block 红。
- (可选)超限时通知列负责人——属通知模块,本模块只发事件。

### 4.5 关键交互流程

- **保存视图**:调好筛选/分组/排序 → 工具条"另存为" → 命名 + 选 `visibility` → 保存 → 出现在侧栏。
- **切换视图**:点侧栏视图项 → 按其配置 `GET /views/{id}/issues` 重新查询渲染;URL 同步,可分享。
- **快速创建**:列底"+ 新增" → 轻量表单(标题 + 继承该列分组值,如默认 status)→ 回车 → 新卡片出现并广播 `issue.created`。

### 4.6 状态流转(视图层不改变 issue 状态机)

视图是只读投影 + 写入入口;拖拽本质是触发 issue 状态流转(见 issue.md §5.2)。视图层负责把状态机可视化,WIP 是视图层施加在"进入某分组(默认某 category)"动作上的约束。`state_category` 用于看板默认列、计数与进度聚合;`status_id` 是用户可自定义的展示层,两者映射见 §2.4。

---

## 5. 验收标准

### 5.1 功能验收

- [ ] 可创建/读取/更新/删除/复制视图;`layout`/`visibility` 取值受 CHECK 约束,非法值返回 `400`。
- [ ] 视图配置(filters/group_by/sub_group_by/sort/display_fields/board_settings)以 JSONB 持久化,**不持久化任何 issue 集合**。
- [ ] `GET /views/{id}/issues` 按配置实时合成结果,返回 `groups` + 每组 `count`/`wip` + **顶层整体 `next_cursor`**;**响应不含每组独立 cursor**(README §6.14)。
- [ ] **过滤限制(README §6.14)**:视图 filters 嵌套 >3 或条件 >20 返回 `400 filter_too_complex`;成本/`statement_timeout` 超限返回 `422 query_cost_exceeded`。
- [ ] 看板默认按 `state_category` 分列;可切换 `group_by=status/assignee/priority/project/label/自定义字段`,列 key/label/拖入改写目标符合 §2.4 映射表。
- [ ] 拖拽 `group_by=state_category` 时,`status_id` 改为目标 category 的默认 status(`column_target_status` 映射正确)。
- [ ] **看板拖拽走原子 move 命令** `POST /views/{id}/moves`(乐观锁 + advisory lock + WIP 计数 + 状态变更 + `view_issue_positions` upsert,单事务);`PATCH /issues/{id}` 不用于拖拽。
- [ ] **跨项目拖拽(R2,README §9 T22)**:`group_by=project` 视图把卡片拖入另一项目列,未确认的 `moves` 返回 `422 move_confirmation_required` 且 `details.preview` 携带字段映射/清除预览;携 `confirm: true` 后**单事务**完成 `project_id` 变更 + 项目私有 status 映射 + 项目私有 milestone/cycle/label/自定义字段值清除(工作区级保留),迁移后不存在"当前项目 + 旧项目私有字段"脏状态;`issue.project_changed` 事件正确携带映射/清除清单(迁移语义以 issue.md §3.8 为权威,README §6.14)。
- [ ] **每视图排序隔离(README §6.14)**:列内拖拽用浮点中点法计算 `position` 并写入**当前视图**的 `view_issue_positions`;**在视图 A 拖动卡片不改变视图 B 的顺序**;无保存排序的视图回退 `issues.position` 规范顺序;精度耗尽触发该视图整列重排且 UI 收敛正确。
- [ ] WIP `warn`:超限允许拖入 + 红色徽章 + toast + `wip_exceeded` 事件;WIP `block`:超限拖入(move 命令事务内计数)返回 `422 wip_limit_exceeded` 且卡片弹回原列。
- [ ] 折叠列、卡片字段显示(`card_fields`)、列底快速创建均生效;快速创建的 issue 继承该列分组值。
- [ ] 列表视图支持可配置列、列头排序、行内编辑、多选批量(走 `POST /issues/bulk`)。
- [ ] 视图作用域鉴权:私有仅 owner 可见;共享工作区成员可读;写需 owner/共享写权限;执行视图时按成员可见范围裁剪 issues。
- [ ] 默认视图唯一约束生效;重复设默认返回 `409`。
- [ ] 未保存改动有"保存/另存/丢弃"提示;切换视图 URL 同步 `/views/{id}`。
- [ ] 筛选器支持内置字段 + 标签 + 自定义字段,支持 AND/OR 嵌套,实时预览命中数。

### 5.2 实时一致性验收

- [ ] WebSocket 遵循 README §6.7(频道内单调 `seq`、`realtime_events` 重放);断线重连 `resume_from` 可重放缺口,过旧触发 `resync_required` + REST 对账水位后整板重拉。
- [ ] **每频道游标(R2)**:原"单视图单游标"设计已删除;断线重放按**频道** `last_seq`(客户端每频道各自记录,§2.6 / §3.5),不存在"每视图一个总游标";可选的服务端 `realtime_channel_cursors` 经复合 FK `(workspace_id, member_id)→members(workspace_id, id)` 强制同租户(README §9 T1 同类),且仅作跨设备断线续传定位、真源为 `realtime_events`。
- [ ] 他人改状态/拖拽/改字段/新建/删除,本地看板按视图 filters **增量合并单卡**(插入/移动/移除),非整板刷新。
- [ ] `issue.updated` 触发本地按 filters 重判:仍命中就地更新/跨列移动,不再命中移除。
- [ ] 拖拽乐观更新 + `If-Match: <updated_at>` 版本校验;`409` 时拉最新收敛,多人同拖同卡 UI 平滑收敛到最新写。
- [ ] 客户端丢弃 `updated_at` 旧于本地的事件,无卡片回退/闪烁。
- [ ] `view.updated`:仅 card_fields 变局部刷新;filters/group/sort 变整板重拉。
- [ ] WS 断开自动降级为 30s 轮询 `?since=<updated_at>`,恢复后回到增量模式。

### 5.3 非功能验收

- [ ] **拖拽性能**(README §10 基准下构成验收标准):单次拖拽从松手到乐观落位 < 50ms(本地);服务端 move 命令 P95 指标按 README §10 标注冷/热缓存;1000 张卡片的列滚动帧率 ≥ 50fps(虚拟滚动)。
- [ ] **增量合并性能**:单条实时事件本地处理 < 16ms,不触发整板 re-render。
- [ ] **查询性能**(README §10 基准):执行视图(命中 issue.md 索引)在 10 万 issue 工作区、热缓存下 P95 < 500ms;自定义字段筛选命中 GIN 索引。
- [ ] **WIP 并发不穿透(集成测试)**:`enforcement=block`、`limit=N` 的列,并发(>N)拖入同一列时,move 命令经 `pg_advisory_xact_lock(hashtext('wip:'||view_id||':'||group_key))` 串行化 + 事务内计数,**最终列内成员数 ≤ N**,多余拖入返回 `422 wip_limit_exceeded`,无并发穿透(README §9 乐观冲突 T9 同类)。
- [ ] **一致性**:并发拖拽同一卡片最终一致(服务端 `version`/`updated_at` 仲裁,无丢失更新,README §9 T9)。
- [ ] **跨租户隔离(README §9 T1)**:`view_issue_positions` / `views` 的复合 FK 拒绝跨 workspace 引用(视图引用别区 issue/member 在 INSERT 被拒);A 区凭证访问 B 区视图返回 403/404。
- [ ] **安全**:filters/sort 经白名单校验 + 参数化绑定,无 SQL 注入;跨工作区/越权访问返回 `403`/`404`。
- [ ] **限流**:视图执行接口有 rate limit,超限返回 `429`。
