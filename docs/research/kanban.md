# 看板与视图(Kanban & Views)调研记录

> 调研对象:主流团队协作 / 项目管理产品在【看板与视图】模块上的通用设计模式(已匿名化,不指向任何具体产品)。
> 数据模型基准约定:PostgreSQL、UUID 主键、`created_at` / `updated_at`、REST + JSON、游标分页、Bearer token、WebSocket 实时。
> 视图是 issue 集合的"投影":同一批 issue 可用看板、列表、(时间线/表格)等不同视图呈现;视图携带筛选/分组/排序/WIP 等配置,可保存复用。

---

## 1. 功能清单

### 1.1 看板(Board)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 列 = 状态(类别) | 看板每列对应一个状态类别(或具体自定义状态) | 列:Todo / In Progress / In Review / Done |
| 拖拽改状态 | 把卡片从一列拖到另一列即修改其 status | 把卡片拖进 In Progress |
| 列内排序 | 同列内可拖拽调整顺序(手动排序值 position) | 把最紧急的卡片拖到列顶 |
| 分组(group by) | 看板可按 status(默认)、assignee、priority、project、label 等分组 | 按 assignee 分列,看每人 workload |
| 子分组(swimlanes) | 横向泳道二级分组(如按项目/优先级) | 泳道按项目,列按状态 |
| 筛选(filter) | 按 assignee/priority/label/due/自定义字段过滤卡片 | 只看 high 优先级 |
| 排序(sort) | 列内按 position/priority/due/created 排序 | 按到期日排序 |
| WIP 限制 | 给列设最大在制品数;超限给视觉警告(软限制)或阻止拖入(硬限制) | In Progress 限 5,超出标红 |
| 折叠列 | 折叠某列(如 Done)节省空间 | 折叠已完成列 |
| 卡片字段显示 | 配置卡片展示哪些字段(标签、估点、到期、子任务进度) | 卡片显示估点与子任务 3/5 |
| 快速创建 | 列底"+ 新增卡片"就地建 issue | 在 Todo 列底快速加卡片 |
| 在制品计数 | 列头显示当前数量 / WIP 上限 | "In Progress 4/5" |

### 1.2 列表视图(List)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 表格化展示 | 行=issue,列=可配置字段(状态、优先级、assignee、到期等) | 看所有 issue 的结构化清单 |
| 分组 | 按 status/assignee/project/label 分组,组头可折叠 | 按状态分组列表 |
| 内联编辑 | 单元格点击即改字段 | 行内改优先级 |
| 多选批量 | 勾选多行批量操作(见 issue.md) | 批量改状态 |
| 排序 | 点列头排序 | 按到期日升序 |
| 自定义列 | 选择显示哪些列、调整列宽/顺序 | 加上"估点"列 |

### 1.3 视图保存(Saved Views)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 保存视图 | 把当前的"类型(看板/列表)+ 筛选 + 分组 + 排序 + 显示字段"存为命名视图 | 存一个"本迭代高优先级看板" |
| 视图作用域 | 个人私有视图 / 项目共享视图 / 工作区共享视图 | 团队共享"冲刺看板" |
| 视图列表/切换 | 侧栏列出可用视图,一键切换 | 在"我的任务""冲刺看板"间切换 |
| 默认视图 | 设某视图为项目/工作区默认 | 项目首页默认打开"看板" |
| 视图编辑/删除/排序 | 管理已存视图 | 调整视图顺序、删除废弃视图 |
| 临时(未保存)视图 | 修改筛选但未保存,作为临时视图,提示"是否保存" | 临时筛一下,不污染共享视图 |

### 1.4 实时更新

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 卡片实时移动 | 他人改状态/拖拽,本地看板即时反映 | 看到同事把卡片拖进 Done |
| 字段实时刷新 | 改 assignee/优先级,所有打开该视图的人即时看到 | 分派后卡片头像立刻更新 |
| 新建/删除实时 | 新 issue 出现、删除消失 | 新建的卡片即刻出现在 Todo 列 |
| 在线协作感知(可选) | 显示他人正在查看/编辑(头像、光标) | 看到谁也在这个看板上 |

---

## 2. 数据模型

### 2.1 核心实体

#### `views`(保存的视图)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK ON DELETE CASCADE | — | |
| `project_id` | UUID | NULL, FK ON DELETE CASCADE | NULL | NULL=工作区级视图 |
| `owner_member_id` | UUID | NOT NULL, FK→workspace_members(id) ON DELETE CASCADE | — | 创建者 |
| `name` | TEXT | NOT NULL | — | 视图名 |
| `layout` | TEXT | NOT NULL, CHECK IN ('board','list','timeline','table') | `'board'` | 视图类型 |
| `visibility` | TEXT | NOT NULL, CHECK IN ('private','shared') | `'private'` | 私有/共享 |
| `filters` | JSONB | NOT NULL | `'{}'` | 筛选条件 |
| `group_by` | TEXT | NULL | NULL | 分组字段 |
| `sub_group_by` | TEXT | NULL | NULL | 泳道二级分组 |
| `sort` | JSONB | NOT NULL | `'[]'` | 排序规则数组 |
| `display_fields` | JSONB | NOT NULL | `'[]'` | 展示字段/列配置 |
| `board_settings` | JSONB | NOT NULL | `'{}'` | 看板专属(WIP、折叠列、卡片字段) |
| `position` | REAL | NOT NULL | `0` | 视图在列表中的排序 |
| `is_default` | BOOLEAN | NOT NULL | `false` | 是否默认视图 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

#### `board_wip_limits`(WIP 限制,可内嵌于 board_settings,亦可独立表)

> 简单实现可直接放 `views.board_settings` JSONB;需要按状态精确定义时可独立成表。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `view_id` | UUID | NOT NULL, FK→views(id) ON DELETE CASCADE | |
| `status_category` | TEXT | NOT NULL | 针对哪个状态类别 |
| `limit` | INT | NOT NULL | 上限 |
| `enforcement` | TEXT | NOT NULL, CHECK IN ('warn','block') DEFAULT 'warn' | 软警告/硬阻止 |
| UNIQUE | `(view_id, status_category)` | |

#### `view_subscriptions`(用户当前打开的视图,用于实时推送,可选)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | PK |
| `view_id` | UUID | FK |
| `member_id` | UUID | FK |
| `last_seen_event_id` | BIGINT | 增量推送游标 |

### 2.2 filters / sort / board_settings 结构示例

```jsonc
// filters(组合条件,AND/OR 嵌套)
{
  "operator": "AND",
  "conditions": [
    { "field": "state_category", "op": "in", "value": ["todo","in_progress"] },
    { "field": "priority", "op": "in", "value": ["high","urgent"] },
    { "field": "assignee_id", "op": "eq", "value": "mem_a1" },
    { "field": "due_date", "op": "lte", "value": "2026-08-31" },
    { "field": "cf_severity", "op": "eq", "value": "major" }   // 自定义字段
  ]
}

// sort
[ { "field": "position", "order": "asc" }, { "field": "created_at", "order": "desc" } ]

// board_settings
{
  "columns": ["backlog","todo","in_progress","in_review","done"],
  "collapsed_columns": ["done"],
  "card_fields": ["labels","estimate","due_date","sub_issue_progress"],
  "wip": { "in_progress": { "limit": 5, "enforcement": "warn" } }
}
```

### 2.3 实体关系(ER)

```
workspaces ──1:N──► views ◄──N:1── projects(可选)
                     │
                     ├── owner: workspace_members
                     ├── board_wip_limits(可选独立表)
                     └── 视图执行其 filters/group/sort 后投影 issues(无持久外键,查询期合成)
```

### 2.4 关键索引

```sql
CREATE INDEX idx_views_workspace ON views(workspace_id, position);
CREATE INDEX idx_views_project ON views(project_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_views_owner ON views(owner_member_id);
CREATE INDEX idx_views_visibility ON views(workspace_id, visibility);
```

> 注:视图本身不存 issue 集合,只存"如何投影"的配置;每次打开视图时按其 filters/group/sort 查询 issues(命中 issue.md 的索引)。卡片在列内的手动排序写在 `issues.position`。

---

## 3. 接口设计

REST 基础路径 `/api/v1`,Bearer token,游标分页。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspaces/{ws}/views` | 列出可见视图(私有 + 共享) |
| POST | `/workspaces/{ws}/views` | 创建视图 |
| GET/PATCH/DELETE | `/views/{id}` | 视图 CRUD |
| POST | `/views/{id}/duplicate` | 复制视图 |
| GET | `/views/{id}/issues` | 执行视图配置返回 issue(分组/排序后) |
| PATCH | `/views/{id}/wip` | 设置 WIP 限制 |
| POST | `/views/{id}/reorder` | 调整卡片列内顺序(或走 issue PATCH position) |

### 3.2 请求/响应示例

**创建看板视图** `POST /api/v1/workspaces/{ws}/views`
```json
// Request
{
  "name": "本迭代高优先级看板", "layout": "board", "visibility": "shared",
  "project_id": "prj_1",
  "filters": { "operator": "AND", "conditions": [
    { "field": "cycle_id", "op": "eq", "value": "cyc_12" },
    { "field": "priority", "op": "in", "value": ["high","urgent"] } ] },
  "group_by": "state_category",
  "board_settings": { "wip": { "in_progress": { "limit": 5, "enforcement": "warn" } }, "card_fields": ["labels","estimate","assignee"] }
}
// 201:返回视图对象
```

**执行视图** `GET /api/v1/views/{id}/issues?limit=100`
```json
{
  "layout": "board",
  "groups": [
    { "key": "todo", "label": "Todo", "count": 3, "wip": null,
      "data": [ { "id": "iss_1", "identifier": "WEB-124", "title": "...", "position": 1.0 } ] },
    { "key": "in_progress", "label": "In Progress", "count": 4, "wip": { "limit": 5, "enforcement": "warn" },
      "data": [ ... ] }
  ],
  "next_cursor": null
}
```

**拖拽改状态 + 排序** `PATCH /api/v1/issues/{id}`
```json
{ "status_id": "st_in_progress", "position": 2.5 }
// 200;若目标列 WIP enforcement=block 且已满 → 422 wip_limit_exceeded
```

### 3.3 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 非法 filters/layout |
| 403 | `forbidden` | 编辑他人私有视图;共享视图无写权限 |
| 404 | `not_found` | 视图不存在 |
| 409 | `conflict` | 默认视图重复设置 |
| 422 | `wip_limit_exceeded` | 硬 WIP 限制下拖入已满列 |

### 3.4 分页与鉴权

- 视图列表游标分页;视图内 issue 执行后亦游标分页(分组场景整体游标)。
- 鉴权:私有视图仅 owner 可见;共享视图工作区/项目成员可见;写需 owner 或共享写权限。

---

## 4. UI 设计

### 4.1 信息架构

```
左侧栏(视图导航)
   ├── 视图列表(图标+名称):看板 / 列表 / 我的任务 / 冲刺看板 …
   ├── [+ 新建视图]
   └── 视图操作(右键/…):重命名、复制、设默认、删除

主区(看板布局)
   ├── 顶部工具条:视图名 | 筛选 | 分组 | 排序 | 显示字段 | [保存/另存]
   └── 列容器(横向滚动)
        每列:列头(名称 + 计数 + WIP) → 卡片堆叠 → 列底"+ 新增"
```

### 4.2 关键组件

- **看板列**:列头含状态色、名称、计数(及 WIP `4/5`,超限变红/黄);列体可滚动;支持折叠。
- **卡片**:identifier + 标题 + 色条 + 头像 + 标签点 + 估点 + 子任务进度;悬停出快捷操作。
- **筛选器弹层**:多条件组合(字段+操作符+值),可加自定义字段;实时预览命中数。
- **分组/排序下拉**:选择 group_by、sub_group(泳道)、sort 字段与方向。
- **WIP 提示**:列超限,列头红色徽章 + 顶部 toast;硬限制时拖入被弹回并提示。
- **视图保存条**:改动未保存时工具条出现"保存 / 另存为新视图 / 丢弃"。
- **列表视图**:可配置列、列头排序、行内编辑、复选批量条。

---

## 5. UX 设计

### 5.1 关键交互流程

**拖拽改状态**:鼠标按住卡片 → 拖到目标列(目标列高亮;若 WIP 将超限,列头预警)→ 松手 → 乐观落位 → 后台 `PATCH`(status_id + position)→ 成功确认;硬 WIP 拒绝则卡片弹回原列并提示。

**列内排序**:同列内拖动 → 计算插入位置上下相邻卡片的 position 中点值 → `PATCH position` → 服务端广播新顺序。

**保存视图**:调好筛选/分组/排序 → 工具条"另存为" → 命名 + 选作用域(私有/共享)→ 保存 → 出现在侧栏视图列表。

**切换视图**:点侧栏视图项 → 按其配置重新查询并渲染;URL 同步 `/views/{id}`,可分享/收藏。

### 5.2 状态流转(视图层不改变 issue 状态机)

视图是只读投影 + 写入入口;拖拽本质是触发 issue 的状态流转(见 issue.md §5.2),视图层负责把状态机可视化。WIP 限制是视图层施加在"进入某 category"动作上的约束。

### 5.3 实时性方案

- WebSocket 订阅:打开视图时订阅 `view:{id}`(或底层 `workspace:{ws}:issues` + 视图过滤)。
- 事件:`issue.created/updated/deleted/moved`、`view.updated`(配置变更)、`presence`(谁在看,可选)。
- 收到事件后,客户端按视图 filters 判断该 issue 是否应出现在当前视图,做增量插入/移动/移除,而非整板刷新。
- **拖拽冲突处理**:乐观更新 + `updated_at` 版本校验;他人同时移动同一卡片时,后到的事件覆盖,UI 平滑收敛。
- position 用浮点中点法减少全列重排;偶发"position 精度耗尽"触发该列重排(重新分配整数/分数序列)。
- 降级:WS 断开退化为轮询 `GET /views/{id}/issues?since=<updated_at>`。

### 5.4 通知触发点

- 共享视图被他人修改配置:通知 owner / 关注者(可选)。
- WIP 超限:对拖入者就地提示;可配置通知列负责人。
- (issue 级通知见 issue.md;视图层主要做实时渲染,通知较少。)
