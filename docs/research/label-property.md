# 标签与自定义属性(Labels & Custom Properties)调研记录

> 调研对象:主流团队协作 / 项目管理产品在【标签与自定义字段/属性】模块上的通用设计模式(已匿名化,不指向任何具体产品)。
> 数据模型基准约定:PostgreSQL、UUID 主键、`created_at` / `updated_at`、REST + JSON、游标分页、Bearer token、WebSocket 实时。
> 标签是轻量的多对多分类标记;自定义字段(属性)是结构化的、带类型的扩展字段。两者共同为 issue 提供灵活的分类与元数据能力,并可作为视图筛选/分组/排序的依据(见 kanban.md)。

---

## 1. 功能清单

### 1.1 标签(Label)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 创建标签 | 名称 + 颜色(+ 可选描述) | 建一个红色"bug"标签 |
| 标签作用域 | 工作区级标签(全工作区可用)或项目级标签(仅某项目) | "前端"为工作区级;"客户A 需求"为某项目私有 |
| 给 issue 打标签 | 一个 issue 可有多个标签(多对多) | 给 issue 打"bug""前端" |
| 标签管理 | 编辑名称/颜色、合并、删除 | 把"bug"和"defect"合并 |
| 颜色语义 | 颜色用于卡片/行上的视觉点 | 一眼区分类型 |
| 按标签筛选/分组 | 视图按标签过滤或分组 | 看板按标签分列 |
| 标签自动补全 | 输入时从已有标签联想,避免重复造同义标签 | 输入"bu"联想出"bug" |

**关键设计点**:标签 = 简单、扁平、多对多、视觉化;适合开放式分类。当需要"有类型的结构化字段"(如一个"严重程度"单选)时,用自定义字段而非标签。

### 1.2 自定义字段 / 属性(Custom Fields / Properties)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 定义字段 | 名称、类型、是否必填、默认值、作用域、可选项 | 定义"严重程度"单选字段 |
| 字段类型 | 见下表 | — |
| 作用域 | 工作区级 / 项目级;可限定应用到哪些项目 | "影响范围"只在某项目启用 |
| 选项管理(枚举类) | 单选/多选维护可选项(名称+颜色+排序) | 给"严重程度"加选项 |
| 在 issue 上填值 | issue 详情页侧栏展示并编辑自定义字段 | 填"严重程度=Major" |
| 必填校验 | 必填字段未填阻止保存/状态流转(可配置) | 进 done 前必须填"验收人" |
| 作为筛选/分组/排序 | 视图可按自定义字段过滤、分组、排序 | 按"严重程度"分组看板 |
| 字段停用/删除 | 停用(隐藏但保留数据)或删除(级联清值) | 弃用某字段 |
| 字段排序/分组展示 | 详情页中字段的展示顺序与分区 | 把常用字段排前面 |

#### 字段类型清单(业界标准类型集)

| 类型 | 值形态 | 说明 | 典型场景 |
|------|--------|------|----------|
| `text` | 单行文本 | 短文本 | "负责人邮箱" |
| `textarea` | 多行文本 | 长文本 | "备注" |
| `number` | 数值(可带单位/精度) | 整数/小数 | "影响用户数" |
| `date` | 日期 | 仅日期 | "上线日" |
| `datetime` | 日期时间 | 带时间 | "故障发生时刻" |
| `single_select`(enum) | 枚举单选 | 一个可选项 | "严重程度" |
| `multi_select` | 枚举多选 | 多个可选项 | "受影响模块" |
| `member`/user | 成员引用 | 人或 agent | "验收人" |
| `boolean`/checkbox | 布尔 | 是/否 | "是否需要文档" |
| `url` | 链接 | URL | "设计稿链接" |
| (进阶)formula/rollup | 派生 | 公式/汇总(可选,复杂) | 自动计算 |

### 1.3 保存的视图(与标签/属性的关系)

> 视图实体本身见 kanban.md;此处强调标签/属性如何驱动视图。

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 按标签/属性筛选 | 视图 filters 支持 label、自定义字段条件 | "严重程度=Major 且带 bug 标签" |
| 按标签/属性分组 | group_by 支持 label / 自定义字段 | 按"受影响模块"分列 |
| 按属性排序 | sort 支持数值/日期/枚举字段 | 按"影响用户数"降序 |
| 保存为视图 | 把上述配置存为命名视图(私有/共享) | 存"高严重度 bug 看板" |

---

## 2. 数据模型

### 2.1 核心实体

#### `labels`(标签)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK ON DELETE CASCADE | — | |
| `project_id` | UUID | NULL, FK ON DELETE CASCADE | NULL | NULL=工作区级;非空=项目级 |
| `name` | TEXT | NOT NULL | — | 1–50 |
| `color` | TEXT | NOT NULL | — | 十六进制色或调色板键 |
| `description` | TEXT | NULL | NULL | |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:`UNIQUE (workspace_id, project_id, name)`(用 COALESCE 处理 NULL project_id 的唯一性)。

#### `issue_labels`(issue-标签 多对多)

| 字段 | 类型 | 约束 |
|------|------|------|
| `issue_id` | UUID | NOT NULL, FK→issues(id) ON DELETE CASCADE |
| `label_id` | UUID | NOT NULL, FK→labels(id) ON DELETE CASCADE |
| `created_at` | TIMESTAMPTZ | NOT NULL |
| PRIMARY KEY | `(issue_id, label_id)` | |

#### `custom_field_defs`(自定义字段定义)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK ON DELETE CASCADE | — | |
| `project_id` | UUID | NULL, FK ON DELETE CASCADE | NULL | 作用域(NULL=工作区级) |
| `name` | TEXT | NOT NULL | — | |
| `field_key` | TEXT | NOT NULL | — | 稳定标识(用于 API/筛选),如 `severity` |
| `type` | TEXT | NOT NULL, CHECK IN ('text','textarea','number','date','datetime','single_select','multi_select','member','boolean','url') | — | |
| `is_required` | BOOLEAN | NOT NULL | `false` | |
| `default_value` | JSONB | NULL | NULL | 默认值(按类型) |
| `config` | JSONB | NOT NULL | `'{}'` | 类型相关配置(数值精度、日期格式等) |
| `position` | REAL | NOT NULL | `0` | 展示排序 |
| `is_active` | BOOLEAN | NOT NULL | `true` | 停用而非删 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:`UNIQUE (workspace_id, project_id, field_key)`。

#### `custom_field_options`(枚举可选项,用于 single_select / multi_select)

| 字段 | 类型 | 约束 | 默认值 |
|------|------|------|--------|
| `id` | UUID | PK | `gen_random_uuid()` |
| `field_def_id` | UUID | NOT NULL, FK→custom_field_defs(id) ON DELETE CASCADE | — |
| `name` | TEXT | NOT NULL | — |
| `color` | TEXT | NULL | NULL |
| `position` | REAL | NOT NULL | `0` |
| `is_active` | BOOLEAN | NOT NULL | `true` |
| UNIQUE | `(field_def_id, name)` | |

#### `issue_custom_field_values`(issue 上的字段值,EAV)

> 用"按类型分列 + JSONB"的混合存储,兼顾可查询性与灵活性。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `issue_id` | UUID | NOT NULL, FK→issues(id) ON DELETE CASCADE | |
| `field_def_id` | UUID | NOT NULL, FK→custom_field_defs(id) ON DELETE CASCADE | |
| `value_text` | TEXT | NULL | text/textarea/url |
| `value_number` | NUMERIC | NULL | number |
| `value_date` | TIMESTAMPTZ | NULL | date/datetime |
| `value_member_id` | UUID | NULL, FK→workspace_members(id) ON DELETE SET NULL | member(人或 agent) |
| `value_boolean` | BOOLEAN | NULL | boolean |
| `value_json` | JSONB | NULL | 单选/多选存 option id(数组),其它结构化值 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| UNIQUE | `(issue_id, field_def_id)` | |

**校验由服务层完成**:按 `field_def.type` 校验只有对应值列被填、枚举值属于该字段选项、member 属于该工作区成员、必填校验等。

### 2.2 实体关系(ER)

```
workspaces ──1:N──► labels ◄──M:N──► issues(via issue_labels)
                       (project_id 可空 → 作用域)

workspaces ──1:N──► custom_field_defs ──1:N──► custom_field_options
                          │
                          └──M:N(值)──► issues(via issue_custom_field_values)
```

### 2.3 关键索引

```sql
CREATE UNIQUE INDEX uq_labels_name ON labels(workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'), name);
CREATE INDEX idx_labels_workspace ON labels(workspace_id);
CREATE INDEX idx_issue_labels_issue ON issue_labels(issue_id);
CREATE INDEX idx_issue_labels_label ON issue_labels(label_id);   -- 反查"哪些 issue 带某标签"

CREATE UNIQUE INDEX uq_cfdefs_key ON custom_field_defs(workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'), field_key);
CREATE INDEX idx_cfopts_def ON custom_field_options(field_def_id, position);
CREATE INDEX idx_icfv_issue ON issue_custom_field_values(issue_id);
CREATE INDEX idx_icfv_field ON issue_custom_field_values(field_def_id);
-- 支持按枚举值筛选:GIN on value_json
CREATE INDEX idx_icfv_value_json ON issue_custom_field_values USING GIN (value_json);
CREATE INDEX idx_icfv_member ON issue_custom_field_values(value_member_id) WHERE value_member_id IS NOT NULL;
```

---

## 3. 接口设计

REST 基础路径 `/api/v1`,Bearer token,游标分页。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspaces/{ws}/labels` | 列出标签(可按 project 过滤) |
| POST | `/workspaces/{ws}/labels` | 创建标签 |
| PATCH/DELETE | `/labels/{id}` | 编辑/删除标签 |
| POST | `/labels/{id}/merge` | 合并标签(把 A 的 issue 迁到 B) |
| GET/PUT | `/issues/{id}/labels` | 读取/整体替换 issue 标签 |
| POST/DELETE | `/issues/{id}/labels/{label_id}` | 增/删单个标签 |
| GET | `/workspaces/{ws}/custom-fields` | 列出字段定义 |
| POST | `/workspaces/{ws}/custom-fields` | 创建字段定义 |
| PATCH/DELETE | `/custom-fields/{id}` | 编辑/停用/删除 |
| GET/POST/PATCH/DELETE | `/custom-fields/{id}/options[...]` | 枚举选项 CRUD |
| GET/PUT | `/issues/{id}/custom-field-values` | 读取/批量设置 issue 的字段值 |

### 3.2 请求/响应示例

**创建标签** `POST /api/v1/workspaces/{ws}/labels`
```json
{ "name": "bug", "color": "#e5484d", "project_id": null }
// 201:{ "id": "lbl_1", "name": "bug", "color": "#e5484d", "scope": "workspace" }
```

**创建枚举字段** `POST /api/v1/workspaces/{ws}/custom-fields`
```json
// Request
{
  "name": "严重程度", "field_key": "severity", "type": "single_select", "is_required": false,
  "options": [ { "name": "Minor", "color": "#888" }, { "name": "Major", "color": "#f5a623" }, { "name": "Critical", "color": "#e5484d" } ]
}
// 201:返回字段定义,options 各带生成的 id
```

**给 issue 设置字段值** `PUT /api/v1/issues/{id}/custom-field-values`
```json
// Request
{ "values": [ { "field_def_id": "cf_sev", "value_json": "opt_major" } ] }
// 200:返回该 issue 全部字段值
```

**给 issue 打标签** `POST /api/v1/issues/{id}/labels/lbl_1`
```json
// 200:{ "labels": [ { "id": "lbl_1", "name": "bug", "color": "#e5484d" } ] }
```

**按标签筛选 issue**(走 issue 列表接口)`GET /api/v1/workspaces/{ws}/issues?label=lbl_1&cf_severity=opt_major`

### 3.3 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 颜色非法、类型不支持、name 超长 |
| 403 | `forbidden` | 项目级标签/字段无权限 |
| 404 | `not_found` | 标签/字段/选项不存在 |
| 409 | `label_name_taken` / `field_key_taken` | 同作用域内重名 |
| 422 | `invalid_field_value` | 值与字段类型不符;枚举值不属于该字段;member 非工作区成员 |
| 422 | `required_field_missing` | 必填字段未填(保存或状态流转时) |
| 422 | `field_inactive` | 给已停用字段写值 |

### 3.4 分页与鉴权

- 标签/字段定义/选项均游标分页(数量通常不大,亦支持一次性返回 + 缓存)。
- 鉴权:读取需工作区成员;创建/编辑标签与字段定义需 admin(或项目内 lead,若项目级)。

---

## 4. UI 设计

### 4.1 信息架构

```
设置 → 标签管理
   ├── 标签列表:色点 | 名称 | 作用域 | 使用次数 | 操作(编辑/合并/删除)
   └── [+ 新建标签]

设置 → 自定义字段
   ├── 字段列表:名称 | 类型图标 | 作用域 | 必填 | 状态 | 操作
   └── [+ 新建字段] → 类型选择 → 配置(选项/必填/默认值/作用域)

Issue 详情侧栏
   ├── 标签区:标签 chip(+ 输入联想添加)
   └── 自定义字段区:按 position 排序渲染各类型控件
```

### 4.2 关键组件

- **标签选择器**:输入框 + 已有标签下拉联想;选中标签以彩色 chip 展示;可就地新建。
- **字段类型控件**:
  - text/textarea/url → 文本输入框
  - number → 数字输入(带精度)
  - date/datetime → 日历/时间选择器
  - single_select → 单选下拉(带颜色)
  - multi_select → 多选 chip
  - member → 成员选择器(人 + agent)
  - boolean → 开关
- **字段定义编辑器**:选类型后动态显示对应配置(枚举的选项管理:增删改、拖拽排序、配色;必填开关;默认值;作用域选择)。
- **筛选器中的字段**:筛选弹层自动列出所有自定义字段作为可选条件,枚举字段渲染为选项多选。

---

## 5. UX 设计

### 5.1 关键交互流程

**创建并应用标签**:设置 → 标签 → 新建(填名称、选颜色)→ 保存;回到 issue → 标签区输入名称首字母 → 联想命中 → 选中 → 卡片上出现色点。

**定义枚举字段并使用**:设置 → 自定义字段 → 新建 → 选"单选" → 命名"严重程度" → 添加选项(各配色)→ 设作用域 → 保存;issue 详情侧栏即出现该字段,点选赋值。

**按属性筛选并存视图**:看板工具条 → 筛选 → 加条件"严重程度 = Major"且"标签 含 bug" → 看板即时收窄 → "另存为视图" → 命名"高严重度 bug" → 出现在侧栏。

### 5.2 状态流转(字段生命周期)

```
字段定义: active ──停用──► inactive(隐藏,保留数据) ──启用──► active
                └──删除──► (级联删除该字段所有值与选项)
选项:    active ──停用──► inactive(已有值保留,新选不可用)
```

### 5.3 实时性方案

- WebSocket 事件:
  - `label.created/updated/deleted`、`issue.labels_changed`(卡片色点实时更新)。
  - `custom_field.updated`(字段定义变更,所有打开的 issue/视图刷新该字段)。
  - `issue.custom_field_changed`(他人改值,详情/列表即时刷新)。
- 视图按标签/属性筛选时,收到 issue 标签/字段变更事件后,客户端按当前 filters 判断该 issue 是否仍属于视图,做增量进出。
- 枚举选项变更需通知所有缓存了该字段选项的客户端刷新。

### 5.4 通知触发点

- 标签/字段被删除或合并:影响范围通知相关项目成员(可选)。
- 必填字段在状态流转时缺失:就地阻断并提示(非通知,属校验)。
- (issue 因标签/字段变化触发的业务通知,可经由自动化规则配置,如"严重程度变为 Critical 时通知负责人"——属自动化模块范畴。)
