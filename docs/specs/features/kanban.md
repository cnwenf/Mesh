# 看板与视图(Kanban & Views)功能 Spec

> **所属层**:视图与分类层(Presentation / Projection Layer)。本层是 issue 之上的**只读投影 + 写入入口**,不持久化 issue 集合,只持久化"如何投影"的配置。
> **依赖 Spec**:
> - `issue.md`(Issue 工作项)——视图投影的原子对象;状态机、`state_category`、`position`、`PATCH /issues/{id}` 均来自此。
> - `label-property.md`(标签与自定义属性)——提供可作为筛选/分组/排序依据的 `label` 与自定义字段。
> - `project.md`(项目)、`member.md`(统一成员抽象,含 AI agent)。
> **文档性质**:可直接指导开发的实现规格;与全局约定冲突时以 [README.md](../README.md) §6「全局权威契约」为准。
> **实现状态**:`views` 定义层(v0.11.6)+ issue 投影层(v0.12.0:分组投影整体游标 / 原子 move + WIP 强制 / 每视图手工排序 / 实时增量合并 / `view.presence` / 跨项目迁移视图侧入口 / 前端真实数据看板)已落地。**泳道协议**已在本 Spec 定义为兼容的一维/二维投影,当前运行时仍为 `sub_group_by=NULL` 的一维投影,泳道实现待后续切片。**label / 自定义字段的分组与筛选**依赖 `issue_labels` / `issue_custom_field_values` 关联层;关联层合入前,`group_by=label`、`sub_group_by=label`、任一轴的自定义字段与对应筛选均返回 `400 projection_field_pending`,合入后按 §2.4 映射接通——属分阶段交付,非本 Spec 设计缺陷。

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

视图(View)是同一批 issue 的可保存"投影":用户配置好**筛选(filters)+ 主/子分组(group/sub_group)+ 排序(sort)+ 显示字段(display)+ 看板专属设置(board_settings)** 后存为命名视图,可在看板(board)、列表(list)等 layout 间复用。视图**不存储 issue 集合**——每次打开视图,按其配置实时查询 issues 合成结果。看板是 `layout='board'` 的视图,把 issue 按分组渲染为可拖拽的列、泳道与卡片。

实时性是本模块的核心体验:他人改状态/拖拽/改字段,所有打开同一视图的成员**按视图 filters 做增量合并**(在列/单元格插入、移动或移除单张卡片),而非整板刷新。

### 1.2 功能点与场景

#### 看板(Board,`layout='board'`)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 列 = 分组值 | 每列对应一个分组值;默认 `group_by=state_category`,每列 = 一个状态类别 | 列:Todo / In Progress / In Review / Done |
| 拖拽改状态 | 跨列拖卡片即修改其 `status_id`(落入目标列对应状态) | 把卡片拖进 In Progress |
| 单元格内排序 | 两轴均为单值时,同一 `(group_key,sub_group_key)` 单元格内拖拽改 `view_issue_positions.position`(浮点中点法,见 §4.3;一维视图的 `sub_group_key=''`);多值轴只投影 | 把最紧急卡片拖到单元格顶部 |
| 分组切换 | `group_by` 可选 `state_category`(默认)/`status`/`assignee`/`priority`/`project`/`label`/自定义字段 | 按 assignee 分列看 workload |
| 子分组(泳道) | `layout='board'` 且 `sub_group_by` 非空时生成二维投影:泳道为外层,主列为泳道内单元格;泳道头显示 label + 总数,列头显示跨泳道 count/WIP | 泳道按项目,列按状态 |
| 筛选 | 按 assignee/priority/label/due/自定义字段过滤卡片 | 只看 high 优先级 |
| 排序 | 单元格内按 `position`/`priority`/`due_date`/`created_at` 排序 | 按到期日排序 |
| WIP 限制 | 给主列设最大在制品数并跨泳道汇总;超限软警告(`warn`)或硬阻止(`block`) | In Progress 限 5,超出标红/拒收 |
| 折叠列 | `collapsed_columns` 折叠某列节省空间 | 折叠 Done 列 |
| 卡片字段显示 | `card_fields` 配置卡片展示哪些字段 | 卡片显示估点 + 子任务 3/5 |
| 快速创建 | 单元格底部"+ 新增卡片"就地建 issue(继承主列分组值;有泳道时同时继承泳道子分组值) | 在项目 A / Todo 单元格快速加卡片 |
| 在制品计数 | 列头显示跨全部泳道汇总的 `count` 及 WIP `4/5`;泳道头另显示泳道总数 | "In Progress 4/5" |

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
- **不在本期范围**:时间线/甘特(`layout='timeline'`)与表格高级透视(`layout='table'` 的复杂聚合)仅在枚举中预留取值,不实现 UI;跨视图的 issue 全局排序;离线编辑;泳道手工排序与泳道折叠。
- **泳道只在看板投影**:`layout='list'` 可保存 `sub_group_by` 配置但不渲染泳道;切换回 board 后恢复投影。`layout` 不是保存或校验 `sub_group_by` 的障碍。
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
                     └── 查询期按 filters/group/sub_group/sort 投影 issues —— 无持久外键,合成结果
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
| `sub_group_by` | TEXT | NULL | NULL | 泳道二级分组;可选字段集与 `group_by` 相同,仅 `layout='board'` 时参与投影 |
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
- `group_by` / `sub_group_by` 的可选集均为 §2.4 的 `state_category` / `status` / `assignee` / `priority` / `project` / `label` / 自定义字段(`field_def_id`);创建与 `PATCH` 均执行同一白名单校验。
- `sub_group_by` 与实际 `group_by` 为同一字段时返回 `400 validation_error`;board 的 `group_by=NULL` 先按默认 `state_category` 归一后再比较,禁止两轴投影同一字段。该校验同样适用于当前不渲染泳道的 list 视图。
- 关联层合入前,`sub_group_by=label` / 自定义字段与 `group_by` 的同类配置使用同一门控,返回 `400 projection_field_pending`;不得因其位于子分组轴而绕过字段可用性与权限校验。

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

### 2.4 主列/泳道分组映射(`group_by` / `sub_group_by`)

| 分组字段 | 值来源 | `key` | `label` | 拖入改 |
|----------|--------|-------|---------|--------|
| `state_category`(默认/NULL) | 7 个固定类别 | category 值,如 `in_progress` | 类别显示名 | `status_id` → 目标 category 的默认 status |
| `status` | `issue_statuses` 行 | `status_id` | status.name | `status_id` → 该列 status |
| `assignee` | 成员 | `member_id`(含 `__none__`) | 成员名 | `assignee_id` |
| `priority` | 5 档 | priority 值 | 档位名 | `priority` |
| `project` | 项目 | `project_id`(含 `__none__`) | 项目名 | `project_id`(**跨项目迁移协议**:迁移前预览并要求确认 + 单事务完成字段映射/清除,见 §3.2 与 issue.md §3.8,README §6.14 跨项目迁移契约;**不是裸改 `project_id`**) |
| `label` | 标签(多值) | `label_id` | 标签名 | **只投影,不拖入**;增删走 label-property.md 的标签端点 |
| 自定义字段(`field_def_id`) | 字段值 | 序列化值(枚举为 option_id) | 值显示名 | 单值类型改该字段值;`multi_select` **只投影,不拖入** |

`group_by` 生成主列,`sub_group_by` 生成泳道,二者的 `key` / `label` / 单值字段改写规则均复用上表。泳道顺序也与同字段作为 `group_by` 时完全一致;空值统一归入 `key='__none__'` 的泳道且恒排最后。本期不持久化泳道顺序,也不提供泳道手工排序。

**确定性分组顺序**(供响应骨架与 §3.4 `lane_rank/group_rank` 共用):主列若在 `board_settings.columns` 显式列出 key,先按该数组去重后的次序,未列出的 key 再接字段默认序;泳道没有手工覆盖,使用同字段作为主列且**没有** `board_settings.columns` 覆盖时的默认序。默认序如下,每类最后都以 key / id 作唯一 tie-breaker,`__none__` 无条件排在全部非空 key 之后:

- `state_category`:`backlog → todo → in_progress → in_review → blocked → done → cancelled`。
- `status`:`(category_rank, issue_statuses.position, status_id)`;`category_rank` 取上一行固定序。
- `priority`:`urgent → high → medium → low → none`。
- `assignee` / `project` / `label`:`(lower(label) COLLATE "C", key)`;不得依赖数据库默认 collation。
- 自定义 `single_select` / `multi_select`:`(custom_field_options.position, option_id)`;其它标量自定义字段按类型规范值后再按序列化 key。字段定义/option/name/position 变化会改变 rank,因此使现有二维游标按 §3.4 失效。

**状态与项目的唯一解析顺序**(move 与视图作用域原子 quick-create 共用同一服务层 resolver):

1. **先定目标项目**:project 在任一轴时取目标单元格该轴的 key;否则固定项目视图取 `views.project_id`,工作区级视图的既有卡片取 issue 当前 `project_id`,quick-create 在无 project 轴且视图未固定项目时取无项目作用域。跨项目仍须先完成 §3.2 / issue.md §3.8 的两步授权与确认,不能先解析源项目 status 再迁移。
2. **再在目标项目可用状态域解析 status**(目标项目私有状态 + 工作区级状态):若有 `status` 轴,其 key 是唯一候选 `status_id`,必须属于目标状态域;若只有 `state_category` 轴,取目标 category 下 `is_default=true` 的 status,没有则取该 category 内 `position` 最小、再以 `id` 打破并列的 status。
3. **两轴同写 `status_id` 时只写一次**:`state_category` 与 `status` 虽不是同一配置字段,却属于同一状态领域。二者同时出现时,`status` 轴 key 是最终 `status_id`,`state_category` 轴只作相容性约束;该 status 的 category 必须等于 category 轴 key。前端把不相容单元格标为不可放置/不可快速创建,服务端对伪造请求返回 `422 incompatible_projection_cell`,事务零写入。
4. 最终 `issues.state_category` 只从已解析的 `status_id` 派生并与其同事务写入;不得让两轴按请求顺序相互覆盖。project 位于主轴或副轴、状态/category 位于另一轴时均严格使用以上顺序。

`column_target_status` 只是**单一状态领域轴 + 整份响应内目标项目恒定**时的前端提示缓存:仅 `group_by=state_category`、视图固定到单一项目,且 `sub_group_by` 既不是 `status` 也不是 `project` 时返回平坦 `{category: status_id}`。因此 `sub_group_by=status`(单元格 status 才是真实落点)与 `sub_group_by=project`(单元格目标项目会变化)两类响应都必须省略;工作区级多项目投影、project 位于任一轴或 category 位于副轴时也一律省略。客户端不得从源卡片、某条泳道的 status 或缓存补造映射。move 与 quick-create 的唯一单元格输入都是目标两轴 key,服务端按上方 resolver 得出最终 `project_id/status_id`;`column_target_status` 缺失不影响写入。

**多值轴冻结语义——多格投影、禁止移动/手排**:

- 多值轴仅包括 `label` 与 `type=multi_select` 的自定义字段。一个值对应一个普通 key;issue 同时拥有 N 个值时投影为 N 个单元格卡片实例,空集合才进入 `__none__`;若两轴均为多值,按两组值的笛卡尔积投影。这里不引入组合 key,保持上表 key/label 与 D2 顺序不变。
- 只要任一轴为多值,该视图就是**投影只读移动模式**:UI 隐藏拖拽/键盘移动/手工排序入口;`POST /views/{id}/moves` 与 `/reorder` 均返回 `422 multi_value_axis_move_unsupported`。因此无需猜测应删除哪个源值,也不会让 `UNIQUE(view_id, issue_id)` 承载多个格内 position。
- 单元格 quick-create 仍可用,但只能走 §3.2 的视图作用域原子命令:每个多值轴只把目标格的一个 key 写入新 issue(两轴均多值则各写一个),`__none__` 显式抑制该轴默认值并保持空集合。label 写入 `issue_labels`;`multi_select` 对该轴覆盖字段默认值,以**只含目标 option 的数组**写入 `issue_custom_field_values.value_json`;option/关联写失败必须连同 issue 主行一起回滚。已有 issue 的 add/remove 仍只走 label / custom-field 领域端点,分别广播 `issue.labels_changed` / `issue.custom_field_changed`,不伪造 `issue.moved`。
- `groups[].count` 统计该格投影实例;`lanes[].count` 按该泳道 distinct `issue_id` 计数;`columns[].count` 与 WIP 按该主列跨泳道 distinct `issue_id` 计数,同一 issue 因多值副轴进入多个泳道时只计一次 WIP。多值主轴下同一 issue 可在多个主列各计一次,但任何单列内仍只计一次。quick-create 若增加目标主列成员,必须在同一事务内执行该主列 WIP;领域端点的外部改值可形成超限展示,不反向伪装成视图 move。
- 含多值轴的单元格按视图 `sort` / `issues.position` + `issue_id` 稳定排序,完全忽略 `view_issue_positions`;Realtime 按值集合差集增删 `(lane_key, group_key, issue_id)` 实例,不得全板刷新。

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
| `sub_group_key` | TEXT | NOT NULL | `''` | 卡片所在泳道键(对齐 §2.4;一维视图/存量行为空串) |
| `position` | REAL | NOT NULL | `0` | 该视图内、该 `(group_key, sub_group_key)` 单元格的手工排序值(浮点中点法,见 §4.3) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| UNIQUE | `(view_id, issue_id)` | | | 每视图每 issue 至多一条排序记录 |

**语义**:
- `view_issue_positions` 记录的是**某个视图内**某 issue 的手工顺序;**视图 A 的拖拽只写视图 A 的行,不影响视图 B**(README §6.14 排序契约)。
- **未保存排序记录的视图**(或某 issue 在当前视图无行)回退到 `issues.position` **规范默认排序** / 视图 `sort` 配置;即"手工排序优先,缺省回退规范顺序"。
- **`issues.position` 不再被视图拖拽写入**——它只是全局规范默认排序(由 issue 创建/规范排序维护),视图内拖拽一律 upsert 本表;同一次 upsert 同时更新 `group_key` / `sub_group_key` / `position`。
- 排序作用域是单元格 `(group_key, sub_group_key)`;一维视图统一使用 `sub_group_key=''`,故其排序行为与存量契约完全相同。跨列、跨泳道或斜向拖拽仍只更新该视图中该 issue 的唯一一行。
- **排序行失配即视为不存在**:查询期先从 issue 当前字段解析投影单元格;只有排序行的 `(group_key, sub_group_key)` 与该单元格完全一致时,该行 `position` 才参与排序。详情 `PATCH`、批量操作、领域事件或 Realtime 使 issue 外部跨格后,旧行不得在新格生效,立即回退 `issues.position` / 视图 `sort` + `issue_id`;读路径不搬移也不删除旧行,下一次在当前视图合法拖拽时再 upsert 覆盖唯一行。由此 A/X 的 position 永不串入 B/Y。
- 任一轴为 §2.4 的多值轴时,整份视图不启用手工排序,所有 `view_issue_positions` 行均忽略;这与 `UNIQUE(view_id, issue_id)` 保持一致。
- 视图删除时其排序行级联清除(ON DELETE CASCADE);issue 删除同理。

**迁移**:在既有表上新增 `sub_group_key TEXT NOT NULL DEFAULT ''`,存量行全部保留为空串;`UNIQUE(view_id, issue_id)` 不变。删除旧 `(view_id, group_key, position)` 索引并以 §2.8 的四列索引替换,不复制排序行、不改变现有一维视图顺序。

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
CREATE INDEX idx_vip_view_group_pos ON view_issue_positions(view_id, group_key, sub_group_key, position);
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
        Index("idx_vip_view_group_pos", "view_id", "group_key", "sub_group_key", "position"),
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
    sub_group_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
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
| POST | `/views/{id}/issues` | **看板单元格原子 quick-create 命令**(视图 read/execute 门 + issue/目标项目/目标值域写门 + 当前 filters/cell 命中校验 + 目标项目/status 解析 + 主列 WIP 锁/计数 + issue/label/自定义字段关联 + outbox,单事务,见 §3.2/§4.5) |
| PATCH | `/views/{id}/wip` | 设置某列 WIP 限制 |
| POST | `/views/{id}/moves` | **看板拖拽的原子 move 命令**(乐观锁 + advisory lock + 主列 WIP 校验 + 分组/子分组字段变更 + 每视图排序 upsert,单事务,见 §3.2/§4.3);新增可选 `to_sub_group_key`;任一轴为 project 且发生跨项目移动时触发**跨项目迁移协议**:预览→确认→单事务映射/清除 |
| POST | `/views/{id}/reorder` | 仅调整某视图同一单元格内的卡片顺序(不改分组字段、不跨列/泳道;走 `view_issue_positions`) |
| PATCH | `/workspaces/{ws}/views/reorder` | 调整视图在侧栏的顺序(`position`) |

> **看板写入必须携视图上下文**:`POST /views/{id}/moves` 是拖拽的原子写路径,`POST /views/{id}/issues` 是一维/二维单元格 quick-create 的原子写路径;二者都携 `view_id`,服务端才能在事务内按视图 filters 汇总目标主列并强制 WIP。**不带 `view_id` 的 `PATCH /issues/{id}` / `POST /workspaces/{ws}/issues` 无法感知视图,不能执行视图级 WIP**——前者只用于非拖拽字段编辑,后者只用于视图外通用创建。单元格纯排序可走 `POST /views/{id}/reorder`;含多值轴时 move/reorder 按 §2.4 拒绝,quick-create 则按同节原子写入目标 label/option。

### 3.2 请求/响应示例

**创建看板视图** `POST /api/v1/workspaces/{ws}/views`
```jsonc
// Request
{
  "name": "本迭代高优先级看板", "layout": "board", "visibility": "shared",
  "project_id": null,
  "filters": { "operator": "AND", "conditions": [
    { "field": "cycle_id",  "op": "eq", "value": "cyc_12" },
    { "field": "priority",  "op": "in", "value": ["high","urgent"] } ] },
  "group_by": "state_category",
  "sub_group_by": "project",
  "board_settings": {
    "wip": { "in_progress": { "limit": 5, "enforcement": "warn" } },
    "card_fields": ["labels","estimate","assignee"] }
}
// 201 Response —— 返回完整视图对象(含生成的 id、created_at/updated_at)
```

**执行一维固定项目视图** `GET /api/v1/views/{id}/issues?limit=100`(`views.project_id=prj_1`,`sub_group_by=NULL`,响应形状保持不变)
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

**执行泳道视图** `GET /api/v1/views/{id}/issues?limit=100`(`sub_group_by` 非空)
```jsonc
// columns 是主列跨泳道汇总;lanes 是外层,每个 lane.groups 是各主列单元格。
// count 均为未分页总数,data 才是当前整体页切片;任何层级都不带独立 cursor。
{
  "layout": "board",
  "group_by": "state_category",
  "sub_group_by": "project",
  // project 泳道使目标项目随单元格变化,故按 §2.4 省略平坦 column_target_status。
  "columns": [
    { "key": "todo", "label": "Todo", "count": 5, "wip": null },
    { "key": "in_progress", "label": "In Progress", "count": 4,
      "wip": { "limit": 5, "enforcement": "warn" } }
  ],
  "lanes": [
    { "key": "prj_web", "label": "Web", "count": 6, "groups": [
      { "key": "todo", "count": 3,
        "data": [ { "id": "iss_1", "identifier": "WEB-124", "title": "...", "position": 1.0 } ] },
      { "key": "in_progress", "count": 3, "data": [ /* 当前页切片 */ ] }
    ] },
    { "key": "__none__", "label": "No project", "count": 3, "groups": [
      { "key": "todo", "count": 2, "data": [ /* 当前页切片 */ ] },
      { "key": "in_progress", "count": 1, "data": [] }
    ] }
  ],
  "next_cursor": "opaque-or-null"
}
```

`column_target_status` 的两类二维负向形状必须分别做字段不存在断言(不是返回 `{}` 或错误映射):

- 固定项目 `group_by=state_category,sub_group_by=status`:每个单元格的真实目标由泳道 `status` key 决定,响应顶层**不含** `column_target_status`。
- `group_by=state_category,sub_group_by=project`:每个单元格的目标项目随泳道变化,如上例响应顶层**不含** `column_target_status`。

二维整体游标冻结到**投影卡片实例**而不是只冻结到格:一次响应所有 `lanes[].groups[].data` 的元素总数不超过全局 `limit`,不是每格各取 `limit`。服务端先按 lane → group 展平网格,再按 §3.4 的稳定总序元组逐卡取页;多值轴让同一 issue 合法出现在多个格时,每个 `(lane_key, group_key, issue_id)` 是独立投影实例。每一页都返回完整 `columns`、全部 `lanes` 与各 lane 的完整 `groups` 骨架,只在本页命中的格填充 `data`;因此 `count>0,data=[]` 可能只是尚未加载,并非空格。

`next_cursor` 是响应顶层唯一游标,续页从最后一个卡片实例之后继续。`columns[].count` / `wip`、`lanes[].count` 与单元格 `groups[].count` 使用 §2.4 的单值/多值计数规则并绑定首屏快照。`sub_group_by=NULL` 时不得返回 `columns` / `lanes`,继续使用上方既有 `groups[]` 形状,保证已交付客户端兼容。

**看板单元格快速创建(原子 quick-create)** `POST /api/v1/views/{id}/issues`
```jsonc
// Request —— 本例在「In Progress / Payments」单元格创建;
// Idempotency-Key 遵循 README §6.5/§6.14,重试不得重复创建。
{ "title": "修复支付重试", "group_key": "in_progress", "sub_group_key": "opt_payments" }

// 201 Response —— 通用 issue 成功包络,包含最终 status/project/label/custom-field 快照。
{ "data": { "id": "iss_2", "identifier": "PAY-42", "title": "修复支付重试",
  "status": { "id": "st_pay_ip", "category": "in_progress" },
  "custom_field_values": [ { "field_def_id": "cf_area", "value_json": ["opt_payments"] } ],
  "version": 1, "created_at": "2026-07-24T10:01:00Z", "updated_at": "2026-07-24T10:01:00Z" } }
```

请求只提交轻量表单字段 `title` 与目标 cell key,不提交客户端推导出的 `project_id/status_id/label_ids/custom_field_values`。`group_key` 必填;视图有 `sub_group_by` 时 `sub_group_key` 必填,一维视图必须省略该字段(服务端使用隐式 `''`),形状不匹配返回 `400 validation_error`。`layout!='board'` 不存在可创建单元格,同样返回 `400 validation_error`。所有看板单元格(含既有一维看板)都使用本端点;通用 `POST /workspaces/{ws}/issues` 不接受伪造的 view context,也不替代本命令。

服务端在**一个数据库事务**内按固定顺序完成:

1. 锁定并读取当前 view 配置及解析命中的 project/status/label/field/option 行直到提交:view 与普通值域行使用 `FOR SHARE`(或更强等价锁),目标项目行(`projects.issue_seq`)或无项目时的工作区行(`workspaces.inbox_issue_seq`)因同时承载编号计数器,从第一次获取就直接使用 `FOR UPDATE`,禁止先取共享锁再升级造成并发 quick-create 死锁。先执行 view **read/execute 门**:私有视图仍只允许 owner,共享视图允许具备该视图读取资格的工作区成员执行;quick-create 不修改 view 配置,**不得**复用 `PATCH/DELETE /views/{id}` 的 view 写门。随后独立校验 `issue:write`/issue 创建权限、目标项目非空时的项目写权限及 label/option 目标值域权限,再按当前可见值域解析两轴 key。project/status/category 使用 §2.4 的 project-first resolver;label / 自定义 option 必须在目标项目作用域内可用且 active。任一不可见资源仍按 §3.4 返回统一 403/404,不得通过 key 错误探测值域;并发停用/删除目标值必须等待本事务结束,不能在“校验后、关联写前”制造悬空值。
2. 复用 issue.md 创建服务的**无写入候选模式**生成唯一最终候选:包含调用者 reporter、服务器默认值、同一 `transaction_timestamp()`、目标两轴值及最终 label/自定义字段集合;需要 `q` 判定时,从步骤 1 已锁定的编号计数器读取但不递增下一个 identifier。单值轴进入 issue 主行/单值字段;label 轴的最终集合包含目标 label;`multi_select` 轴以**只含目标 option 的数组**覆盖对应字段默认值;`__none__` 对该轴显式置空并抑制默认值。此候选是后续真正持久化的同一份规范化快照,不得在过滤校验后重新套用另一组默认值。category + status 不相容仍以 `422 incompatible_projection_cell` 在任何写入前拒绝。
3. 使用 `GET /views/{id}/issues` 的**同一份规范化 filter 编译器与 cell 投影器**评估步骤 2 的候选,不得另写一套简化判断。候选必须同时命中已锁定 view 的完整 filters(含嵌套 AND/OR、范围、否定、label/自定义字段与 `q`)并投影到请求的 `(group_key, sub_group_key)`;不自动继承、改写或猜测 filters 中的 assignee/cycle/priority/日期等非 cell 字段。目标 cell 不相容沿用 `422 incompatible_projection_cell`;filters 不命中返回具名 `422 quick_create_filter_mismatch`,`details.unmet_filter_fields` 只列调用者已可见的字段标识,不回显隐藏 option/资源值。两类失败都发生在编号递增、issue/关联/outbox 任一写入前,禁止返回成功后卡片立即从当前视图消失的 silent off-view create。
4. 候选已确定命中 filters 且会增加目标主列成员时,取得既有 advisory lock `'wip:'||view_id||':'||group_key`,再按同一锁定 view 配置事务内汇总该主列全部泳道的 distinct issue 数。`block` 且已满时返回 `422 wip_limit_exceeded`;`warn` 放行并同事务写 `view.wip_exceeded` outbox。多值轴不得跳过或按泳道拆分该锁。
5. 递增已锁定的编号计数器并原样持久化步骤 2 的候选,写 issue 主行与 label/自定义字段关联,最后写携带**最终完整关联快照**的 `issue.created` outbox。任一权限、resolver、过滤/cell、WIP、编号、label/option 或 outbox 写失败都回滚全部行;不得先调用通用创建端点、再以第二个领域请求补 label/option。成功后才返回 201。

view 行锁覆盖候选生成、filters/cell 判定、WIP 与提交;`PATCH /views/{id}` 必须取得冲突的更新锁。因此 quick-create 与 filters/group/sub_group 配置变更呈现唯一串行顺序:配置先提交则创建按新配置判定,创建先提交则其 201 线性化点仍命中旧配置,随后正常的 `view.updated` 使客户端按新配置重拉;不存在用旧配置校验、却在新配置版本下静默提交的窗口。

**看板拖拽(原子 move)** `POST /api/v1/views/{id}/moves`
```jsonc
// Request —— to_sub_group_key 可选;本例为斜向拖入「In Progress / Web」单元格。
{ "issue_id": "iss_1", "to_group_key": "in_progress", "to_sub_group_key": "prj_web",
  "position": 2.5, "version": 7 }
// 服务端在同一事务内:
//  (a) 乐观锁 issue(WHERE id=$1 AND version=$version;不匹配 → 409 conflict)
//  (b) SELECT pg_advisory_xact_lock(hashtext('wip:' || view_id || ':' || to_group_key))  -- 串行化目标列
//  (c) 按视图 filters 计数目标主列全部泳道的当前成员数
//  (d) 强制 WIP:block 且 count>=limit → 422 wip_limit_exceeded;warn → 放行并发 view.wip_exceeded 事件
//  (e) 按 §2.4 同时改写主分组/子分组字段 +
//      upsert view_issue_positions(view_id,issue_id,group_key,sub_group_key,position)
// 200 Response
{ "data": { "id": "iss_1", "status": { "id": "st_in_progress", "category": "in_progress" },
            "position": 2.5, "version": 8, "updated_at": "2026-07-24T10:00:01Z" } }
// 目标列 enforcement=block 且已满 → 422 wip_limit_exceeded(details: group_key/limit/count)
// version 与服务端不符 → 409 conflict
```

WIP 校验只约束**主列成员数增加**的 move。纯跨泳道移动的 `to_group_key` 与当前主列相同,不会改变该列总数,即使该列已达 limit 也不得把这次泳道内搬移误判为新增并拒绝。

`to_sub_group_key` 语义:
- **省略**:不改 issue 的子分组字段;排序 upsert 的 `sub_group_key` 取该 issue 当前解析出的泳道 key。一维视图的所有 move 均沿用此路径并写 `sub_group_key=''`。
- **视图没有 `sub_group_by` 却传入**:返回 `400 validation_error`,事务不写 issue 或排序行。
- **指定**:按 §2.4 的目标项目 → 状态唯一 resolver 在同一事务内改写子分组字段。它可与 `to_group_key` 组合;斜向拖拽必须先验证目标单元格相容,再把主列字段变更、子分组字段变更与 `view_issue_positions` upsert 作为一个原子提交,任一步失败则全部回滚。
- **任一轴为多值**:整份视图按 §2.4 禁止移动/手排,无论请求是否省略 `to_sub_group_key`,均在字段写入与 WIP 锁之前返回 `422 multi_value_axis_move_unsupported`。

> **不要再用 `PATCH /issues/{id}` 拖拽**:不带 `view_id` 的 PATCH 无法执行视图级 WIP(见 §3.1)。`PATCH /issues/{id}` 仅供详情侧栏等**非拖拽**字段编辑。

**跨项目拖拽(`group_by=project` 或 `sub_group_by=project`)** `POST /api/v1/views/{id}/moves`

> project 位于主列轴时,`to_group_key` 是目标 `project_id`;project 位于泳道轴时,`to_sub_group_key` 是目标 `project_id`。只要目标项目发生变化,本命令就复用 issue.md §3.8 的**完整两步式跨项目迁移契约**:先预览并要求确认,再单事务完成迁移;不得以子分组参数为由裸改 `project_id` 或绕过源/目标权限检查。
```jsonc
// Request —— sub_group_by=project 视图,把卡片拖到另一项目泳道;to_group_key 保持目标主列。
{ "issue_id": "iss_1", "to_group_key": "in_progress", "to_sub_group_key": "prj_app",
  "position": 1.5, "version": 7 }
// 未确认 → 422(客户端先展示预览并要求确认)
{ "error": { "code": "move_confirmation_required",
  "message": "跨项目迁移将影响以下字段,请确认后重试",
  "details": { "preview": { "mapped_fields": [ {"field":"status","from":"st_web_dev","to":"st_app_dev"} ],
    "cleared_fields": [ {"field":"milestone_id"}, {"field":"cycle_id"},
      {"field":"labels","items":["lbl_web_only"]}, {"field":"custom_field_values","items":["cf_web_severity"]} ],
    "kept_fields": ["priority","due_date","assignee_id","workspace 级标签/字段"] } } } }
// 确认(携 confirm:true)→ 单事务迁移(乐观锁 + project_id 变更 + status 映射 + 清除项目私有字段 + 排序 upsert)
{ "issue_id": "iss_1", "to_group_key": "in_progress", "to_sub_group_key": "prj_app",
  "position": 1.5, "version": 7, "confirm": true }
// 200 Response
{ "data": { "id": "iss_1", "project_id": "prj_app",
            "status": { "id": "st_app_dev", "category": "in_progress" },
            "position": 1.5, "version": 8,
            "move_result": { "mapped_fields": [...], "cleared_fields": [...] } } }
```

> **迁移语义以 issue.md §3.8 为权威**:项目私有 status → 目标项目同 category 默认 status;项目私有 milestone/cycle/label/自定义字段值清除;工作区级字段保留。斜向拖拽先锁定目标 project,再按 §2.4 在该项目域解析另一轴的 category/status:category 轴取目标项目该 category 的目标 status,status 轴必须属于目标项目可用状态域;两轴为 category + status 时还须 category 相容。最终 status 不能被源项目/源列映射覆盖。迁移产生 `issue.project_changed` 事件携带映射/清除清单(经 outbox → realtime 唯一写入路径,README §6.6/§6.7)。本节 moves 命令在 project 位于任一分组轴时均为其视图侧入口,未确认返回 422 `move_confirmation_required`;`dry_run: true` 等价 issue.md 的 `move-preview`(仅返回预览不落库)。

**设置 WIP** `PATCH /api/v1/views/{id}/wip`
```jsonc
{ "group_key": "in_progress", "limit": 5, "enforcement": "block" }
// 200:返回更新后的 board_wip_limits / 同步写入 board_settings.wip
```

WIP 永远以主列为维度:`board_wip_limits` 仍以 `(view_id, group_key)` 唯一,advisory lock key 仍为 `'wip:'||view_id||':'||group_key`;计数包含该主列全部泳道的 distinct issue(多值副轴不重复计),不新增单元格级 limit、锁或计数口径。

### 3.3 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 非法 filters/layout/op;字段类型与 op 不匹配;`sub_group_by` 与 `group_by` 同字段;无子分组的视图传 `to_sub_group_key`;quick-create 的 layout/cell key 形状与视图不符 |
| 400 | `filter_too_complex` | filters 嵌套深度 >3 或条件数 >20(README §6.14) |
| 400 | `projection_field_pending` | label / 自定义字段关联层未就绪时用于 `group_by` / `sub_group_by` / filters |
| 401 | `unauthorized` | 缺失/失效 token |
| 403 | `forbidden` | 编辑他人私有视图;对共享视图无配置写权限;quick-create 缺少 issue 创建/目标项目/目标值域写权限 |
| 404 | `not_found` | 视图不存在或不可见 |
| 409 | `conflict` | 乐观并发版本不符(move `version` / `If-Match`);默认视图重复设置 |
| 409 | `cursor_invalidated` | 整体分页快照/视图配置/授权域已变化或游标过期;客户端必须丢弃已拼页并从无 cursor 的首屏重启 |
| 422 | `wip_limit_exceeded` | 硬 WIP 限制下拖入或 quick-create 进入已满主列(`details` 含 `group_key`/`limit`/`count`) |
| 422 | `move_confirmation_required` | 跨项目拖拽未确认(`details` 含字段映射/清除预览,README §6.14;见 §3.2 与 issue.md §3.8) |
| 422 | `incompatible_projection_cell` | category/status 不相容,或 status 不属于先解析出的目标项目状态域 |
| 422 | `quick_create_filter_mismatch` | quick-create 最终候选不命中当前锁定 view filters;零写入,客户端保留标题并转完整创建 |
| 422 | `multi_value_axis_move_unsupported` | label / `multi_select` 位于任一轴时调用 move/reorder;该视图只投影且禁止移动/手排 |
| 422 | `query_cost_exceeded` | 视图查询估算成本/`statement_timeout` 超限(README §6.14,建议收窄条件) |
| 429 | `rate_limited` | 限流 |
| 501 | `not_implemented` | 请求 `layout IN ('timeline','table')` 的渲染 |

### 3.4 分页、鉴权与安全

- **分页形状(README §6.14 整体游标契约)**:视图列表仍编码 `(position, id)`。视图内 `sub_group_by=NULL` 的一维形状保持 `{ "groups": [{key,label,count,wip?,data}], "next_cursor": ... }`;`sub_group_by` 非空时使用 `{ "columns": [{key,label,count,wip}], "lanes": [{key,label,count,groups:[{key,count,data}]}], "next_cursor": ... }`。两种形状都只有顶层 `next_cursor`,`count` 都是未分页总数,**不得**给组、单元格或泳道独立 cursor。
- **二维 `limit` 与稳定总序**:`limit` 是整份二维响应最多返回的投影卡片实例数,即 `Σ len(lane.groups[].data) <= limit`。服务端以总序元组 `K=(lane_rank, lane_key, group_rank, group_key, sort_tuple, issue_id)` 做 keyset: `lane_rank` 来自 §2.4 泳道顺序、`group_rank` 来自 `board_settings.columns`/字段规范顺序,rank 并列再按 key;`sort_tuple` 是方向与 NULL 次序均规范化的视图排序值,其中 position 使用“键匹配的 `view_issue_positions.position`,否则 `issues.position`”并追加剩余 view sort;`issue_id` 是最终唯一 tie-breaker。多值轴实例的完整身份是 `(lane_key, group_key, issue_id)`,故同一 issue 跨格出现不是重复。
- **游标内容与快照绑定**:`next_cursor` 对客户端保持 opaque 且签名,内部绑定格式版本、`view_id`、规范化 filters/group/sub_group/sort/列泳道顺序指纹、授权可见域指纹、请求 `limit`、首屏各查询作用域的数据水位、上一个完整 `K` 与过期时间。续页必须返回与首屏相同的完整 `columns/lanes/groups` 骨架和快照 count,只填本页 `data`。视图配置/字段定义或排序域变化、授权域变化、请求 limit 改变、任一相关 issue 写入使水位前进、游标过期或快照水位已不可验证时返回 `409 cursor_invalidated`;客户端丢弃该次分页已合并数据,不沿用旧 cursor,从首屏重新请求。签名/格式非法仍按 `400 validation_error`。
- **页边界与客户端合并**:边界落在格内时,下一页从同格最后 `K` 后的卡片开始;恰落在列格末尾时,从同泳道下一非空 group 开始;恰落在泳道末尾时,从下一非空 lane 开始。客户端先按 `(lane_key, group_key)` 找到完整骨架中的单元格,再 append `data`,并在格内以 `(cell_key, issue_id)` 去重(`cell_key=(lane_key,group_key)`);不得全局按 issue_id 去重,否则会误删合法多值投影。一个未失效快照的全部页并集基数必须等于首屏 `Σ lanes[].groups[].count`,且每个 `(lane_key,group_key,issue_id)` 投影实例恰好一次;`lanes[].count` / `columns[].count` 的 distinct 口径不用于推算该基数。
- **过滤限制(README §6.14)**:视图 filters **最大嵌套深度 3、最大条件数 20**;服务端以 `statement_timeout`(默认 3s)+ 估算查询成本兜底;超限返回 `400 filter_too_complex`,成本超限返回 `422 query_cost_exceeded`(建议收窄条件)。
- **鉴权**:私有视图仅 `owner_member_id` 可见可写;`shared` 视图工作区成员可读/执行,修改/删除/WIP 配置仍需 owner 或共享视图写权限(工作区 admin / 项目 lead)。执行视图(`GET /views/{id}/issues`)时,服务端**再次**按成员可见范围裁剪 issues(不暴露其无权 issue)。quick-create 只要求同一 view read/execute 门,不授予也不要求 view 配置写权;它另行强制 `issue:write`/issue 创建权限、目标项目写门与每个 label/option 的作用域/可见值域门。普通 member 可因此在可读共享板创建但仍不可编辑该 view;guest 因缺少 `issue:write` 不可创建,持 view read 但缺少目标项目写权的成员同样不可创建。任一失败都在事务写入前拒绝。
- **move 子分组权限**:`to_sub_group_key` 先按视图的 `sub_group_by` 白名单与可见值域解析,不得作为任意字段名或 SQL 片段。`sub_group_by=project` 的预览、未确认与确认路径全部复用 issue.md §3.8 的源 issue 读门、目标项目写门与私有源 payload 脱敏;鉴权失败只返回统一 403/404,不得泄漏项目、泳道或迁移预览是否存在。
- **无前缀端点 404 口径(workspace.md §5.3)**:`/views/{id}` 各路径对「id 不存在」「存在但非成员」「软删除」返回同一 `view not found`,成员门 404 在路由层转写,无视图存在性 oracle。
- **乐观并发**:`PATCH /views/{id}` 与 `POST /views/{id}/moves` 支持 `If-Match: <updated_at>` / `version`;版本不符返回 `409 conflict`,客户端拉取最新后重试或提示。
- **JSONB 与 cell key 安全**:`filters`/`sort` 经白名单 Pydantic schema 校验,`field` 必须落在内置字段集或为合法 `field_def_id`;`group_key`/`sub_group_key` 只能按已锁定 view 的两轴定义和值域解析,不得作为任意字段名或 SQL 片段;查询/计数/关联写一律参数化绑定。
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
| `issue.created` | issue 全量 | 按视图 filters 判定:命中 → 插入对应列/单元格;否则忽略 |
| `issue.updated` | `id` + 变更字段 diff + 新 `updated_at` | 按 filters 重判:仍命中 → 就地更新/跨单元格移动;不再命中 → 移除 |
| `issue.moved` | `id`、`from_group`、`to_group`、`position`;二维视图增加 `from_sub_group` / `to_sub_group` | 按 `(group_key, sub_group_key)` 精确移动单卡;`updated_at` 旧于本地则丢弃(防回退) |
| `issue.labels_changed` / `issue.custom_field_changed` | `issue_id` + 新值集合 | 多值轴按新旧集合差集增删对应格内实例并重算 distinct count/WIP;不转写成 `issue.moved` |
| `issue.deleted` | `id` | 若在视图内 → 移除卡片 |
| `view.updated` | 视图配置 diff | 配置变更:若 filters/group/sub_group/sort 变 → 整板重拉;仅 card_fields 变 → 局部刷新 |
| `view.presence` | `view_id`、成员列表(可选) | 渲染协作者头像(事件名取 README §6.7 词汇注册表) |

- **`issue.moved` payload**:泳道视图必须同时携带 `from_sub_group` / `to_sub_group`(值为 §2.4 的泳道 key);一维视图**省略这两个字段**,不发送 `null` 占位,保持既有 payload 兼容。列内/单元格内重排时 from/to key 相同。
- **增量合并原则**:收到 `issue.*` 后,客户端用**当前视图 filters** 在本地重判该 issue 归属。二维视图以 `(group_key, sub_group_key)` 二元组作为单元格身份,做单卡插入/移动/移除;一维视图等价使用 `(group_key, '')`。**禁止整板刷新**(仅 `view.updated` 改到投影规则或收到 `resync_required` 时才整板重拉)。
- **多值轴实例合并**:客户端从事件的新完整值集合重算该 issue 的目标 cell 集,以 `(lane_key, group_key, issue_id)` 做集合差:只删除离开的实例、只插入新增实例,保留未变化格的 DOM/焦点。若某实例通过详情 PATCH/批量/领域事件进入新格且服务端排序行 key 与新格不符,按 §2.7 回退排序,不得携旧 position 串格。
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
   ├── 主列头(columns,横向,sticky top):状态色 + 名称 + 跨泳道计数 + WIP
   └── 泳道列表(lanes,sub_group_by 非空时;纵向)
        每条泳道:泳道头(sticky left,label + 泳道总数)
                 └── 主列单元格(groups,横向):卡片堆叠 → 单元格底部"+ 新增"

主区(列表布局 layout=list)
   ├── 顶部工具条(同上)
   └── 表格:可配置列 / 列头排序 / 行内编辑 / 复选批量条
```

### 4.2 关键组件

- **视图切换器**:侧栏视图列表;当前视图高亮;切换时 URL 同步 `/views/{id}`(可分享/收藏);未保存改动时切换弹"保存/另存/丢弃"。
- **看板列**:列头由顶层 `columns` 渲染,含状态色、名称、跨泳道计数(及 WIP `4/5`;超限 warn 变黄、block 变红);支持折叠(`collapsed_columns`)。一维视图继续从 `groups` 渲染同样的列头。
- **泳道与双轴滚动**:泳道为外层行,泳道头显示 `label` + `count`,其 `groups` 与主列对齐组成单元格;空值 `__none__` 泳道在最后。本期无泳道折叠、无泳道拖拽调序。桌面主列头 sticky top、泳道头 sticky left,左上角交点拥有最高 z-index;两者与单元格共享同一个横纵滚动坐标和列宽/行高测量,虚拟化不得造成头格错位。
- **单元格加载态**:`count=0,data=[]` 才渲染真实空态与快速创建;`count>0,data=[]` 渲染保持尺寸的“待加载”占位并继续取顶层 `next_cursor`,不可显示“暂无卡片”。后续页只向命中单元格渐进 append + 去重,不清空已渲染格、不阻塞首屏交互。
- **卡片**:`identifier` + 标题 + 状态色条 + assignee 头像(人/agent 区分) + 标签点 + 估点 + 子任务进度;悬停出快捷操作;显示内容受 `card_fields` 控制。
- **筛选器弹层**:多条件组合(字段 + 操作符 + 值),字段下拉自动列出内置字段 + 所有自定义字段(枚举字段渲染为选项多选);实时预览命中数;支持 AND/OR 嵌套。
- **分组/排序下拉**:选 `group_by`(默认 state_category)、`sub_group_by`(泳道)、`sort` 字段与方向;两轴不能选同一字段,泳道顺序不可手调。
- **视图保存条**:配置改动未保存时,工具条出现"保存 / 另存为新视图 / 丢弃"。

### 4.3 拖拽:乐观更新 + 版本校验 + position 浮点中点法

**跨列/跨泳道拖拽**
> 本节只适用于两轴均为单值字段的视图;任一轴为 label / `multi_select` 时按 §2.4 隐藏全部移动/手排入口,服务端亦 fail closed。

1. 鼠标按住卡片 → 拖向目标单元格(目标主列与泳道同时高亮;若目标主列 WIP 将超限,列头实时预警:warn 黄色、block 红色 + 禁用落点)。category/status 不相容或 status 不属于先确定的目标项目时同样标为禁用落点,不做乐观移动。
2. 松手 → **乐观落位**:非跨项目移动时,UI 立即把卡片渲染进目标单元格。
3. 在目标 `(group_key, sub_group_key)` 单元格计算 `position` → 发 **`POST /views/{id}/moves`** 携 `{ issue_id, to_group_key, to_sub_group_key?, position, version }`。仅跨主列时省略 `to_sub_group_key` 以保持原泳道;跨泳道时传目标 key;斜向拖拽同时传两轴目标。服务端先按 §2.4 定目标 project、再解析 category/status,然后单事务完成乐观锁 + 主列 WIP 锁/计数 + 两轴字段改写 + `view_issue_positions` upsert。**不再用 `PATCH /issues/{id}` 拖拽**——它不带 `view_id`,无法执行视图级 WIP。
4. 成功 → 用响应的新 `version`/`updated_at` 更新本地版本;失败处理:
   - `422 wip_limit_exceeded`(block)→ **卡片弹回原单元格** + toast 提示。
   - `422 incompatible_projection_cell` → 卡片保持原位 + 聚焦/读屏说明目标项目与状态/category 不相容。
   - `409 conflict`(他人同时改了该卡)→ 拉最新 issue,按服务端结果收敛(后到事件覆盖)。

**跨项目拖拽(`group_by=project` 或 `sub_group_by=project`)**
1. 把卡片拖向**另一项目**列/泳道 → 松手后**不直接落位**,而是弹出**迁移预览模态**:列出将被**映射**的 status(项目私有 status → 目标项目同 category 默认 status)、将被**清除**的项目私有 milestone/cycle/标签/自定义字段值,以及保留的工作区级字段(预览来自 `moves` 未确认返回的 422 `details.preview`,或先以 `dry_run: true` 取预览,见 §3.2 / issue.md §3.8)。
2. 用户确认 → 发 `POST /views/{id}/moves`;project 在主列轴时传 `to_group_key:<目标 project_id>`,在泳道轴时传 `to_sub_group_key:<目标 project_id>`,并携 `position` / `version` / `confirm:true` → 服务端**单事务**完成 `project_id` 变更 + status 映射 + 清除项目私有字段 + 两轴排序 upsert。
3. 成功 → 卡片落位目标项目列/泳道,响应携带 `move_result`(映射/清除清单),UI 明确呈现"拖入项目后哪些字段为何变化"对用户可见;`422 move_confirmation_required`(未确认)→ **卡片弹回原单元格** + 重新展示预览;`409 conflict` → 拉最新收敛。迁移语义以 issue.md §3.8 为权威(README §6.14 两步式契约),迁移产生 `issue.project_changed` 事件。

**单元格内排序(浮点中点法,写入 `view_issue_positions`)**
- 拖到两张相邻卡片 `A`(position=pA)与 `B`(pB)之间 → 新 `position = (pA + pB) / 2`。
- 拖到单元格顶 → `position = first.position - 1.0`;拖到底 → `position = last.position + 1.0`。
- 经 `POST /views/{id}/moves`(两轴 key 不变)或 `POST /views/{id}/reorder` upsert **当前视图的** `view_issue_positions(view_id, issue_id, group_key, sub_group_key, position)`;**不写 `issues.position`**——一个视图的排序不污染其它视图(README §6.14)。服务端广播 `issue.moved`(payload 带 `view_id`;二维视图带相同的 from/to subgroup key)。
- **精度耗尽**:当 `|pB - pA|` 小于阈值(如 `1e-6`,REAL 精度逼近)时,只触发该视图该 `(group_key, sub_group_key)` **单元格整格重排**——服务端按格内当前顺序重新分配整数间隔序列(如 1.0, 2.0, 3.0 …)写回 `view_issue_positions`,广播该单元格的 `issue.moved`;不得重排同主列的其它泳道。
- **外部跨格**:详情 PATCH、批量或 Realtime 改轴字段时不批量维护所有视图排序行;渲染新格前执行 §2.7 key 匹配,失配即用 fallback 顺序。只有用户在新格再次手排才覆盖唯一行。

**实时一致性**:服务端按 `version`/`updated_at` 版本仲裁;客户端丢弃 `updated_at` 旧于本地缓存的事件,并以 `(group_key, sub_group_key)` 归并卡片,保证多人同时拖同一卡片时 UI 平滑收敛到最新写。

### 4.4 WIP 软警告 / 硬阻止

| enforcement | 拖入将超限时 | 落位后超限 | 视觉 |
|-------------|--------------|-----------|------|
| `warn`(软) | 允许拖入,列头预警 | 卡片正常落位 | 列头红色徽章 `6/5` + 顶部 toast 提示 |
| `block`(硬) | 落点禁用,提示"已达上限" | move 命令(`POST /views/{id}/moves`)在事务内计数后返回 `422 wip_limit_exceeded` → 卡片弹回 | 列头红色 + 拖拽过程禁用高亮 |

- 列头计数取顶层 `columns[].count`,即该主列全部泳道的 distinct issue 数 / WIP `limit`(单值轴等价于卡片数;多值副轴按 §2.4 不重复计同一 issue);WIP 配置、advisory lock 与服务端事务内计数均只按 `group_key` 汇总,不按单元格拆分。纯跨泳道移动不增加主列计数,不得因该列已满而拒绝。超限 warn 黄、block 红。
- (可选)超限时通知列负责人——属通知模块,本模块只发事件。

### 4.5 关键交互流程

- **保存视图**:调好筛选/分组/排序 → 工具条"另存为" → 命名 + 选 `visibility` → 保存 → 出现在侧栏。
- **切换视图**:点侧栏视图项 → 按其配置 `GET /views/{id}/issues` 重新查询渲染;URL 同步,可分享。
- **快速创建**:单元格底部"+ 新增" → 轻量表单只提交标题 + 目标 `(group_key, sub_group_key)` 到 `POST /views/{id}/issues` → 服务端按 §2.4 **先定目标 project**,再解析唯一 `status_id` 与其它轴值,并在同一事务执行当前 filters/cell 命中、主列 WIP、issue/label/option/outbox 写入 → 成功后新卡片必然出现在原单元格并由最终快照 `issue.created` 广播。project 在主轴/category 在副轴与 project 在副轴/category 在主轴时完全同序;status + category 单元格仅在相容时显示入口。多值轴只给新 issue 写目标格的一个 label/option(两轴均多值则各一个),`__none__` 保持该轴为空。若服务器返回 `422 quick_create_filter_mismatch`,轻量表单保留用户标题与焦点,用可见字段名说明“未满足当前视图筛选”,并提供“转到完整创建”以补 assignee/cycle/priority/日期等字段;不得清空输入、显示成功 toast 或让卡片闪现后消失。入口按 view read/execute + `issue:write` + 目标项目写权逐 cell 门控:普通 member 可在可读共享 view 创建但仍不能编辑 view,guest/缺目标项目写权者不显示入口(服务端仍独立强制)。平坦 `column_target_status` 仅可在 §2.4 的“唯一状态领域轴 + 固定项目”场景作提示,不是写入事实源;一维看板也改走同一视图命令并保持只继承列分组值的用户行为。

### 4.6 响应式、键盘与读屏(对齐 design-quality.md §8.3/§9.4/§10.2)

- **桌面双轴 sticky**:主列头 sticky top、泳道头 sticky left,交点表头同时标识“泳道 / 主列”;横向滚动只移动列与单元格,纵向滚动只移动泳道与单元格,头部始终与共享坐标系对齐。键盘焦点滚入虚拟窗口时不得被 sticky 层遮挡。
- **compact**:一次只展示一条泳道,顶部 lane selector 切换泳道,其下横向 chips 切主列;无 `sub_group_by` 时使用单个隐式泳道保持一维行为。触控长按或卡片“移动”操作打开目标 sheet,必须在同一 sheet 明确选择 **lane + column** 并在确认前显示两轴名称、WIP 与无效原因;不依赖精细横向拖动。
- **键盘移动**:聚焦卡片后可进入移动模式;左右键选择主列、上下键选择泳道,Enter 确认,Esc 取消。每次目标变化都把“卡片名、目标泳道、目标列、WIP n/m、可放置/不可放置原因”写入 polite live region;确认成功、409 回滚、WIP block、`incompatible_projection_cell` 与跨项目迁移待确认分别宣布。迁移预览 Dialog 焦点圈定并在关闭后回到原卡片。
- **多值轴可访问性**:投影只读移动模式不暴露 draggable 或移动菜单;卡片说明中标注“该视图按多值字段投影,请在详情中编辑值”,避免给出必然失败的键盘动作。
- **分页与状态**:`count>0,data=[]` 单元格显示“待加载” status,不使用 empty 文案;每页到达后只更新命中单元格并由 live region 合并宣布“已加载 N 张,尚有 M 张”,不逐卡重复朗读。分页请求不阻塞已加载卡片的查看、筛选与移动;`cursor_invalidated` 重启时保留焦点语义并宣布正在重新同步。
- 全部操作满足 44×44px 触控目标、200% zoom / 320 CSS px reflow、`prefers-reduced-motion` 与非颜色唯一信号;拖拽始终有上述键盘/目标 sheet 替代路径。

### 4.7 状态流转(视图层不改变 issue 状态机)

视图是只读投影 + 写入入口;拖拽本质是触发 issue 状态流转(见 issue.md §5.2)。视图层负责把状态机可视化,WIP 是视图层施加在"进入某分组(默认某 category)"动作上的约束。`state_category` 用于看板默认列、计数与进度聚合;`status_id` 是用户可自定义的展示层,两者映射见 §2.4。

---

## 5. 验收标准

### 5.1 功能验收

- [ ] 可创建/读取/更新/删除/复制视图;`layout`/`visibility` 取值受 CHECK 约束,非法值返回 `400`。
- [ ] 视图配置(filters/group_by/sub_group_by/sort/display_fields/board_settings)以 JSONB 持久化,**不持久化任何 issue 集合**。
- [ ] **子分组配置校验**:`sub_group_by` 可选集与 `group_by` 完全一致;两轴同字段(含 `group_by=NULL` 归一后的默认 `state_category`)在创建与 PATCH 均返回 `400 validation_error`;关联层未就绪时 label/自定义字段两轴均返回 `400 projection_field_pending`;list 可保存但不渲染 `sub_group_by`。
- [ ] **一维兼容**:`sub_group_by=NULL` 时,`GET /views/{id}/issues` 继续返回既有 `groups[{key,label,count,wip?,data}]` + 顶层 `next_cursor`,不得出现 `columns` / `lanes`,已交付响应形状零变化。
- [ ] **二维投影**:`sub_group_by` 非空时,响应精确包含顶层 `columns[{key,label,count,wip}]`(主列跨泳道汇总)、`lanes[{key,label,count,groups:[{key,count,data}]}]` 与唯一顶层 `next_cursor`;每一页都返回完整网格骨架,单元格 `count` 为未分页总数、`data` 为当前全局页切片,无组/单元格/泳道独立 cursor;`count>0,data=[]` 表示待加载而非空格。
- [ ] **卡片级整体游标**:二维 `limit=N` 时全网格 `data` 合计 ≤N,以 `K=(lane_rank,lane_key,group_rank,group_key,sort_tuple,issue_id)` 稳定总序逐卡分页;opaque cursor 绑定查询/视图/授权指纹、limit、数据水位、末尾 K 与过期时间。任一绑定变化或相关写入返回 `409 cursor_invalidated`,客户端清空本次拼页并从无 cursor 首屏重启。
- [ ] **三类页边界无重无漏**:分别用 limit 让页尾落在①单元格内部、②同泳道列格边界、③泳道边界;逐页按 `(lane_key,group_key)` append、以 `(cell_key,issue_id)` 格内去重后,全部页并集基数等于首屏 `Σ groups[].count`,每实例恰好一次;合法多值跨格实例不得被全局 issue_id 去重。
- [ ] **过滤限制(README §6.14)**:视图 filters 嵌套 >3 或条件 >20 返回 `400 filter_too_complex`;成本/`statement_timeout` 超限返回 `422 query_cost_exceeded`。
- [ ] 看板默认按 `state_category` 分列;主列与泳道均可切换 `state_category/status/assignee/priority/project/label/自定义字段`,两轴 key/label 符合 §2.4 同一映射表;单值轴按表改写,多值轴按只投影契约处理;泳道顺序与同字段主列顺序一致,`__none__` 恒最后,无手工泳道排序。
- [ ] **状态领域唯一解析**:move/quick-create 一律先定目标 project,再在其可用状态域解析 category/status;category + status 两轴时仅 status key 写 `status_id`,category 只校验相容,不相容格前端禁用且伪造请求 `422 incompatible_projection_cell`。`column_target_status` 只在目标项目固定、主轴 category 是唯一状态领域轴时返回;`sub_group_by=status` 与 `sub_group_by=project` 两类响应均断言字段省略,其它不满足条件的场景也省略且客户端不得补造。
- [ ] **看板拖拽走原子 move 命令** `POST /views/{id}/moves`:省略 `to_sub_group_key` 不改泳道;一维视图传入该参数返回 `400 validation_error`;指定时按 §2.4 改写子分组字段;单值双轴斜向拖拽在单事务内完成目标项目/状态解析、两轴字段变更 + `view_issue_positions(group_key,sub_group_key,position)` upsert;`PATCH /issues/{id}` 不用于拖拽。
- [ ] **project 双轴对称验收**:分别覆盖 `group_by=project,sub_group_by=state_category|status` 与 `group_by=state_category|status,sub_group_by=project` 的斜向 move 和 `POST /views/{id}/issues` 单元格 quick-create;两种方向都先选目标项目、再解析目标 status,状态不属于项目/类别不相容时零写入;category quick-create 取目标项目该 category 的 resolver 结果,status quick-create 使用并校验该 status key。
- [ ] **跨项目拖拽(R2,README §9 T22)**:project 位于主列或泳道轴时,对应 `to_group_key` / `to_sub_group_key` 均复用 issue.md §3.8 两步迁移;未确认返回 `422 move_confirmation_required` + 完整授权后的预览,`confirm:true` 后单事务完成 `project_id` 变更、目标项目域 status 解析、项目私有字段清除与排序 upsert,不存在裸改/越权预览旁路。
- [ ] **多值轴**:label / `multi_select` 每值投影一格(双多值轴为笛卡尔积,空集合才进 `__none__`);含任一多值轴的 move/reorder 返回 `422 multi_value_axis_move_unsupported`,UI 无拖拽/键盘移动/手排。quick-create 在创建事务内只写目标格 label/option(`__none__` 抑制默认并留空);领域 add/remove 事件按集合差增删实例;格 count 计实例、lane/column 与 WIP 按各自维度 distinct issue 计数;排序只用 view sort/`issues.position` + `issue_id`,不读 `view_issue_positions`。
- [ ] **每视图/单元格排序隔离(README §6.14)**:`view_issue_positions` 含 `sub_group_key TEXT NOT NULL DEFAULT ''`,唯一键仍为 `(view_id,issue_id)`,查询索引为 `(view_id,group_key,sub_group_key,position)`;浮点中点按单元格计算,精度耗尽只重排该视图该单元格;视图 A 拖动不改变视图 B,一维存量行与缺省回退顺序不变。
- [ ] **旧 position 不串格**:在 A/X 手排后通过详情 PATCH、批量与 Realtime 三条路径分别把 issue 改到 B/Y;查询/增量合并仅在排序行 key 与当前投影 cell 完全相等时应用 position,三条路径均回退 `issues.position`/view sort + `issue_id`,下一次 B/Y 拖拽才覆盖唯一行。
- [ ] **主列 WIP**:`columns[].count/wip` 汇总该列全部泳道;`board_wip_limits` 键与 `'wip:'||view_id||':'||group_key` advisory lock 不变,无单元格级 WIP。`warn` 超限允许拖入 + 徽章/toast + `view.wip_exceeded`;`block` 仅在主列成员数增加时事务内汇总计数并可能返回 `422`,纯跨泳道移动即使主列已满也不拒绝。
- [ ] **原子 quick-create**:`POST /views/{id}/issues` 是所有看板单元格(含一维)的唯一快速创建路径;请求只携 `title/group_key/sub_group_key?`,服务端在单事务完成 view read/execute 门、issue/目标项目/目标值域写门、project-first status resolver、最终候选 filters/cell 命中、主列 WIP 锁与计数、issue + 关联值 + 最终快照 outbox。任一步失败无编号增量/issue/关联/outbox 半成品;通用 `POST /workspaces/{ws}/issues` 不承载 view context。折叠列、`card_fields`、泳道头 label + 总数继续生效,本期无泳道折叠。
- [ ] **quick-create 权限分层**:普通 member 对 shared view 有 read/execute、具备 `issue:write` 与目标项目写权时返回 201,同一身份 `PATCH/DELETE` 该 view 仍返回 403;guest 即使可读共享 view/项目也因缺少 `issue:write` 返回 403,有 view read/execute 但无目标项目写权的 member 返回 403,私有 view 非 owner 仍按统一不可见口径 404。所有负向断言编号、issue、关联与 outbox 零写入。
- [ ] **quick-create 不创建视图外卡片**:最终候选使用执行视图的同一 filter/cell evaluator;简单等值 filter 在 cell 值/服务器默认值满足时 201,不满足时 `422 quick_create_filter_mismatch`。嵌套 OR、范围与否定条件逐一覆盖:只按最终候选正常求值,不得从 filter 自动继承或猜值;不命中时数据库零写入,UI 保留标题、列出可见的未满足字段并可转完整创建,无成功 toast/闪现后消失。
- [ ] **泳道 UX**:桌面列头 sticky top + 泳道头 sticky left 且共享滚动坐标;compact 一次一条 lane、chips 切 column,目标 sheet 同时选择 lane + column;键盘左右选列/上下选泳道/Enter/Esc 完成移动,live region 宣布两轴、WIP、无效落点、回滚与迁移确认;`count>0,data=[]` 显示“待加载”并逐页渐进合并而不阻塞首屏。
- [ ] 列表视图支持可配置列、列头排序、行内编辑、多选批量(走 `POST /issues/bulk`)。
- [ ] 视图作用域鉴权:私有仅 owner 可见;共享工作区成员可读/执行;PATCH/DELETE/WIP 等视图配置写入需 owner/共享写权限;执行视图时按成员可见范围裁剪 issues。quick-create 复用 read/execute 门后独立校验 issue/目标项目/值域写门,不把执行命令误当成修改 view 配置。
- [ ] 默认视图唯一约束生效;重复设默认返回 `409`。
- [ ] 未保存改动有"保存/另存/丢弃"提示;切换视图 URL 同步 `/views/{id}`。
- [ ] 筛选器支持内置字段 + 标签 + 自定义字段,支持 AND/OR 嵌套,实时预览命中数。

### 5.2 实时一致性验收

- [ ] WebSocket 遵循 README §6.7(频道内单调 `seq`、`realtime_events` 重放);断线重连 `resume_from` 可重放缺口,过旧触发 `resync_required` + REST 对账水位后整板重拉。
- [ ] **每频道游标(R2)**:原"单视图单游标"设计已删除;断线重放按**频道** `last_seq`(客户端每频道各自记录,§2.6 / §3.5),不存在"每视图一个总游标";可选的服务端 `realtime_channel_cursors` 经复合 FK `(workspace_id, member_id)→members(workspace_id, id)` 强制同租户(README §9 T1 同类),且仅作跨设备断线续传定位、真源为 `realtime_events`。
- [ ] 他人改状态/拖拽/改字段/新建/删除,本地看板按视图 filters **增量合并单卡**(插入/移动/移除),二维视图以 `(group_key,sub_group_key)` 判定单元格,非整板刷新。
- [ ] 二维视图的 `issue.moved` payload 携 `from_sub_group` / `to_sub_group`;一维视图省略两字段并保持原 payload;`issue.updated` 触发 filters 重判后在正确单元格就地更新/移动或移除。
- [ ] 多值轴收到 `issue.labels_changed` / `issue.custom_field_changed` 后按值集合差增删 `(lane_key,group_key,issue_id)` 实例、更新 distinct count/WIP,不发/不期待伪造的 `issue.moved`,未变化格不重渲染。
- [ ] 拖拽乐观更新 + `If-Match: <updated_at>` 版本校验;`409` 时拉最新收敛,多人同拖同卡 UI 平滑收敛到最新写。
- [ ] 客户端丢弃 `updated_at` 旧于本地的事件,无卡片回退/闪烁。
- [ ] `view.updated`:仅 card_fields 变局部刷新;filters/group/sub_group/sort 变整板重拉。
- [ ] WS 断开自动降级为 30s 轮询 `?since=<updated_at>`,恢复后回到增量模式。

### 5.3 非功能验收

- [ ] **拖拽/网格性能**(README §10 基准下构成验收标准):单次拖拽从松手到乐观落位 < 50ms(本地);服务端 move 命令 P95 指标按 README §10 标注冷/热缓存;在至少 20 泳道 × 12 主列、1000 个投影卡片实例下,桌面双轴滚动与 compact 切 lane/column ≥50fps(虚拟化),首屏仅等第一页、后续页渐进填格且不冻结已加载交互。
- [ ] **增量合并性能**:单条实时事件本地处理 < 16ms,不触发整板 re-render。
- [ ] **查询性能**(README §10 基准):执行视图(命中 issue.md 索引)在 10 万 issue 工作区、热缓存下 P95 < 500ms;自定义字段筛选命中 GIN 索引。
- [ ] **WIP 并发不穿透(集成测试)**:`enforcement=block`、`limit=N` 的主列,从多个不同泳道并发(>N)拖入或 quick-create 进入该列时,move 与 `POST /views/{id}/issues` 共用 `pg_advisory_xact_lock(hashtext('wip:'||view_id||':'||group_key))` 串行化 + 事务内跨泳道汇总计数,**最终主列成员总数 ≤ N**,多余写入返回 `422 wip_limit_exceeded`,无端点/泳道拆锁导致的并发穿透。
- [ ] **quick-create 原子回滚(真实数据库集成测试)**:① inactive/越作用域 `multi_select` option 在写入前被拒且零落库;② 在真实 PostgreSQL 事务中注入关联约束失败,断言 issue 主行、编号增量、`issue_custom_field_values`/`issue_labels` 与 outbox 均回滚;③ 并发停用目标 option 被 resolver 的行锁串行化,提交后不存在悬空值;④ 同一 `Idempotency-Key` 重试只创建一张卡。
- [ ] **quick-create 与 view 配置并发(真实数据库集成测试)**:并发执行 quick-create 与 `PATCH /views/{id}` 修改 filters/group/sub_group,断言 view 行锁给出唯一串行顺序:PATCH 先提交时创建只按新配置成功或 `422`,创建先提交时 201 候选命中其锁定版本且后续 `view.updated` 触发重拉;不得出现按旧配置放行、却在新配置版本下提交的 silent off-view create。
- [ ] **一致性**:并发拖拽同一卡片最终一致(服务端 `version`/`updated_at` 仲裁,无丢失更新,README §9 T9)。
- [ ] **跨租户隔离(README §9 T1)**:`view_issue_positions` / `views` 的复合 FK 拒绝跨 workspace 引用(视图引用别区 issue/member 在 INSERT 被拒);A 区凭证访问 B 区视图返回 403/404。
- [ ] **安全**:filters/sort、move/quick-create 的两轴 key 经已锁定 view 白名单/可见值域校验 + 参数化绑定,无 SQL 注入;quick-create 执行 view read/execute 门后独立执行 issue 创建门、目标项目写门与 label/option 作用域门,不要求或授予 view 配置写权;filter mismatch details 只含调用者可见字段标识,不回显隐藏值。project 泳道迁移的预览与确认均执行源读门/目标写门并遵循私有源 payload 脱敏;跨工作区/越权访问返回 `403`/`404` 且不泄漏泳道/预览存在性。
- [ ] **限流**:视图执行接口有 rate limit,超限返回 `429`。
