# 标签与自定义属性(Labels & Custom Properties)功能 Spec

> **所属层**:视图与分类层(分类与元数据子层)。为 issue 提供**轻量视觉分类(标签)** 与 **结构化、带类型的扩展字段(自定义属性)**;两者的值都可作为视图筛选/分组/排序的依据。
> **依赖 Spec**:
> - `issue.md`(Issue 工作项)——标签/字段值挂载于 issue;`PATCH /issues/{id}`、状态流转、批量操作复用此。
> - `kanban.md`(看板与视图)——消费标签/自定义字段作为 `filters` / `group_by` / `sort` 输入。
> - `member.md`(统一成员抽象,`member` 类型字段引用人或 AI agent)、`project.md`(作用域)。
> **全局一致性锚点**(本 Spec 全程遵守):
> 1. PostgreSQL;snake_case 复数表名;UUID 主键;`created_at` / `updated_at` 为 `TIMESTAMPTZ NOT NULL DEFAULT now()`。
> 2. issue 状态双层 category/status(本模块不直接处理状态机;但**必填字段可在状态流转时校验**,见 §4.5)。
> 3. API 基础路径 `/api/v1`;`Authorization: Bearer <token>`;游标分页响应统一为 `{ "data": [...], "next_cursor": "..." }`;统一错误信封(见 §3.4)。
> 4. WebSocket 路径 `/ws`;每条事件携带单调递增 `seq`,断线凭 `resume_from=<seq>` 重放;事件类型命名 `<entity>.<action>`。
> 5. ORM 采用 SQLAlchemy 2.x 约定(`DeclarativeBase` / `Mapped` / `mapped_column`)。

---

## 1. 功能描述

### 1.1 定位

- **标签(Label)**:简单、扁平、多对多、视觉化的开放式分类标记。一个 issue 可挂多个标签;标签用颜色在卡片/行上呈现色点。适合"无固定结构"的自由分类(如 bug、前端、客户A)。
- **自定义字段 / 属性(Custom Field)**:结构化、带类型的扩展字段(文本/数字/日期/单选/多选/成员/布尔/链接)。适合"有类型约束"的元数据(如"严重程度"单选、"影响用户数"数字)。
- 两者共同作为视图(见 kanban.md)筛选/分组/排序的输入,实现灵活的分类与查询。

**取舍原则**:需要"有类型的结构化字段"(如一个单选"严重程度")时用自定义字段;只需视觉化自由打标时用标签。

### 1.2 功能点与场景

#### 标签(Label)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 创建标签 | 名称 + 颜色(+ 可选描述) | 建一个红色"bug"标签 |
| 作用域 | 工作区级(`project_id=NULL`,全区可用)/ 项目级(仅某项目) | "前端"工作区级;"客户A 需求"项目私有 |
| 给 issue 打标签 | 多对多,一个 issue 可有多个标签 | 给 issue 打"bug""前端" |
| 标签管理 | 编辑名称/颜色、合并、删除 | 把"bug"与"defect"合并 |
| 颜色语义 | 颜色用于卡片/行视觉点 | 一眼区分类型 |
| 按标签筛选/分组 | 视图按标签过滤或分列 | 看板按标签分列 |
| 自动补全 | 输入时联想已有标签,避免同义重复 | 输入"bu"联想"bug" |

#### 自定义字段(Custom Field)

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 定义字段 | 名称、类型、是否必填、默认值、作用域、可选项 | 定义"严重程度"单选字段 |
| 字段类型 | text/textarea/number/date/datetime/single_select/multi_select/member/boolean/url | 见 §1.3 |
| 作用域 | 工作区级 / 项目级 | "影响范围"只在某项目启用 |
| 选项管理(枚举) | 单选/多选维护可选项(名称 + 颜色 + 排序) | 给"严重程度"加选项 |
| 在 issue 上填值 | issue 详情侧栏展示并编辑 | 填"严重程度=Major" |
| 必填校验 | 必填未填阻止保存/状态流转(可配置) | 进 done 前必须填"验收人" |
| 作为筛选/分组/排序 | 视图按自定义字段过滤/分组/排序 | 按"严重程度"分组看板 |
| 停用/删除 | 停用(隐藏保留数据)/ 删除(级联清值) | 弃用某字段 |
| 展示排序/分区 | 详情页字段展示顺序(`position`) | 常用字段排前面 |

#### 与视图的关系

| 功能点 | 说明 | 典型场景 |
|--------|------|----------|
| 按标签/属性筛选 | `filters` 支持 label、自定义字段条件 | "严重程度=Major 且带 bug 标签" |
| 按标签/属性分组 | `group_by` 支持 label / 自定义字段 | 按"受影响模块"分列 |
| 按属性排序 | `sort` 支持数值/日期/枚举字段 | 按"影响用户数"降序 |
| 保存为视图 | 配置存为命名视图(见 kanban.md) | 存"高严重度 bug 看板" |

### 1.3 字段类型清单

| 类型 | 值形态 | 存储列(见 §2.6) | 典型场景 |
|------|--------|------------------|----------|
| `text` | 单行文本 | `value_text` | "负责人邮箱" |
| `textarea` | 多行文本 | `value_text` | "备注" |
| `number` | 数值(可带精度/单位) | `value_number` | "影响用户数" |
| `date` | 仅日期 | `value_date` | "上线日" |
| `datetime` | 日期时间 | `value_date` | "故障发生时刻" |
| `single_select` | 枚举单选 | `value_json`(option_id) | "严重程度" |
| `multi_select` | 枚举多选 | `value_json`(option_id 数组) | "受影响模块" |
| `member` | 成员引用(人或 agent) | `value_member_id` | "验收人" |
| `boolean` | 是/否 | `value_boolean` | "是否需要文档" |
| `url` | 链接 | `value_text` | "设计稿链接" |

> 进阶类型 `formula`/`rollup`(派生/汇总)不在本期范围,枚举中不预留取值。

### 1.4 边界与非目标

- **不实现状态机**;仅在"issue 保存 / 状态流转"两个时机提供**必填字段校验钩子**(校验逻辑在本模块,触发时机由 issue.md 调用)。
- **不实现** formula/rollup 派生字段、字段级权限(仅作用域级)、字段值历史审计(走 issue.md 的 `issue_activity`)。
- **不重复实现视图**;本模块只产出可被 kanban.md 消费的字段定义与值。
- 标签**无层级**(不支持标签树/父子标签);需要层级化结构请用自定义字段或多标签组合。

---

## 2. 数据模型

### 2.1 实体关系(ER)

```
workspaces ──1:N──► labels ◄──M:N──► issues(via issue_labels)
                      (project_id 可空 → 作用域)

workspaces ──1:N──► custom_field_defs ──1:N──► custom_field_options(枚举可选项)
                          │
                          └──M:N(值)──► issues(via issue_custom_field_values,EAV)
                                            │
                                            └── value_member_id ──► members(member 类型)
```

### 2.2 `labels`(标签)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键 |
| `project_id` | UUID | NULL, FK→projects(id) ON DELETE CASCADE | NULL | NULL=工作区级;非空=项目级 |
| `name` | TEXT | NOT NULL, CHECK (`char_length(name) BETWEEN 1 AND 50`) | — | 标签名 |
| `color` | TEXT | NOT NULL, CHECK (`color ~ '^#[0-9a-fA-F]{6}$'`) | — | 十六进制色 |
| `description` | TEXT | NULL | NULL | |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:`UNIQUE (workspace_id, COALESCE(project_id, NIL_UUID), name)`(部分/表达式唯一索引,处理 NULL project_id)。

### 2.3 `issue_labels`(issue-标签 多对多)

| 字段 | 类型 | 约束 |
|------|------|------|
| `issue_id` | UUID | NOT NULL, FK→issues(id) ON DELETE CASCADE |
| `label_id` | UUID | NOT NULL, FK→labels(id) ON DELETE CASCADE |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT `now()` |
| PRIMARY KEY | `(issue_id, label_id)` | |

> 打标签时服务层校验 label 作用域:项目级标签只能打给同项目 issue;工作区级标签全区可用。

### 2.4 `custom_field_defs`(自定义字段定义)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键 |
| `project_id` | UUID | NULL, FK→projects(id) ON DELETE CASCADE | NULL | 作用域(NULL=工作区级) |
| `name` | TEXT | NOT NULL, CHECK (`char_length(name) BETWEEN 1 AND 100`) | — | 字段显示名 |
| `field_key` | TEXT | NOT NULL, CHECK (`field_key ~ '^[a-z][a-z0-9_]{0,49}$'`) | — | 稳定标识(用于 API/筛选),如 `severity` |
| `type` | TEXT | NOT NULL, CHECK IN (`'text'`,`'textarea'`,`'number'`,`'date'`,`'datetime'`,`'single_select'`,`'multi_select'`,`'member'`,`'boolean'`,`'url'`) | — | 字段类型 |
| `is_required` | BOOLEAN | NOT NULL | `false` | 是否必填 |
| `required_on` | JSONB | NOT NULL | `'[]'` | 必填生效时机,如 `["save","status:done"]`;空=保存即校验 |
| `default_value` | JSONB | NULL | NULL | 默认值(按类型) |
| `config` | JSONB | NOT NULL | `'{}'` | 类型相关配置(数值精度/单位、日期格式、url 校验等) |
| `position` | REAL | NOT NULL | `0` | 详情页展示排序 |
| `is_active` | BOOLEAN | NOT NULL | `true` | 停用而非删 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:`UNIQUE (workspace_id, COALESCE(project_id, NIL_UUID), field_key)`。

### 2.5 `custom_field_options`(枚举可选项,single_select / multi_select)

| 字段 | 类型 | 约束 | 默认值 |
|------|------|------|--------|
| `id` | UUID | PK | `gen_random_uuid()` |
| `field_def_id` | UUID | NOT NULL, FK→custom_field_defs(id) ON DELETE CASCADE | — |
| `name` | TEXT | NOT NULL | — |
| `color` | TEXT | NULL | NULL |
| `position` | REAL | NOT NULL | `0` |
| `is_active` | BOOLEAN | NOT NULL | `true` |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| UNIQUE | `(field_def_id, name)` | | |

### 2.6 `issue_custom_field_values`(issue 上的字段值,EAV)

> 采用**"按类型分列 + JSONB"混合存储**,兼顾可查询性(数值/日期/成员可走 B-Tree 索引)与灵活性(枚举/结构化值走 JSONB + GIN)。

| 字段 | 类型 | 约束 | 适用类型 |
|------|------|------|----------|
| `id` | UUID | PK | |
| `issue_id` | UUID | NOT NULL, FK→issues(id) ON DELETE CASCADE | |
| `field_def_id` | UUID | NOT NULL, FK→custom_field_defs(id) ON DELETE CASCADE | |
| `value_text` | TEXT | NULL | text / textarea / url |
| `value_number` | NUMERIC | NULL | number |
| `value_date` | TIMESTAMPTZ | NULL | date / datetime |
| `value_member_id` | UUID | NULL, FK→members(id) ON DELETE SET NULL | member(人或 agent) |
| `value_boolean` | BOOLEAN | NULL | boolean |
| `value_json` | JSONB | NULL | single_select(option_id)/ multi_select(option_id 数组)/ 其它结构化值 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT `now()` | |
| UNIQUE | `(issue_id, field_def_id)` | | |

**校验由服务层按 `field_def.type` 完成**(数据库不加跨列 CHECK,避免类型演进困难):
- 只有对应类型的值列被填,其余必须为 NULL(如 `type=number` 仅 `value_number` 非空)。
- 枚举值(`value_json`)必须属于该字段的 `custom_field_options`(且为 `is_active`,新写入时)。
- `member` 值的 `value_member_id` 必须属于该工作区成员(人或 agent)。
- `number` 满足 `config.precision`/范围;`url` 满足合法 URL;`date`/`datetime` 合法。
- 必填校验:保存或状态流转(依 `required_on`)时,`is_required` 字段必须有非空值。
- 不允许给 `is_active=false` 的字段写新值。

### 2.7 关键索引

```sql
-- 标签
CREATE UNIQUE INDEX uq_labels_name
  ON labels(workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'), name);
CREATE INDEX idx_labels_workspace ON labels(workspace_id);
CREATE INDEX idx_issue_labels_issue ON issue_labels(issue_id);
CREATE INDEX idx_issue_labels_label ON issue_labels(label_id);        -- 反查"哪些 issue 带某标签"

-- 自定义字段
CREATE UNIQUE INDEX uq_cfdefs_key
  ON custom_field_defs(workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'), field_key);
CREATE INDEX idx_cfdefs_workspace_active ON custom_field_defs(workspace_id) WHERE is_active;
CREATE INDEX idx_cfopts_def ON custom_field_options(field_def_id, position);
CREATE INDEX idx_icfv_issue ON issue_custom_field_values(issue_id);
CREATE INDEX idx_icfv_field ON issue_custom_field_values(field_def_id);
CREATE INDEX idx_icfv_value_json ON issue_custom_field_values USING GIN (value_json);  -- 枚举值筛选
CREATE INDEX idx_icfv_number ON issue_custom_field_values(value_number) WHERE value_number IS NOT NULL;  -- 数值排序/范围
CREATE INDEX idx_icfv_date   ON issue_custom_field_values(value_date)   WHERE value_date   IS NOT NULL;  -- 日期排序/范围
CREATE INDEX idx_icfv_member ON issue_custom_field_values(value_member_id) WHERE value_member_id IS NOT NULL;
```

> 视图按自定义字段筛选/分组/排序时:枚举命中 `idx_icfv_value_json`(GIN);数值/日期命中对应部分索引;`issue_id` JOIN 回 issues 后再套用 kanban.md / issue.md 的视图过滤。

### 2.8 SQLAlchemy 2.x 模型(节选)

```python
import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Text, REAL, Boolean, Numeric, ForeignKey, CheckConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB, TIMESTAMPTZ
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class CustomFieldDef(Base):
    __tablename__ = "custom_field_defs"
    __table_args__ = (
        CheckConstraint(
            "type IN ('text','textarea','number','date','datetime','single_select',"
            "'multi_select','member','boolean','url')", name="ck_cfdefs_type"),
    )
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True,
                                          server_default=text("gen_random_uuid()"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    field_key: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    required_on: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    default_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    position: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False,
        server_default=text("now()"), onupdate=text("now()"))
    options: Mapped[list["CustomFieldOption"]] = relationship(
        back_populates="field_def", cascade="all, delete-orphan",
        order_by="CustomFieldOption.position")

class IssueCustomFieldValue(Base):
    __tablename__ = "issue_custom_field_values"
    __table_args__ = (
        CheckConstraint("num_nonnulls(value_text, value_number, value_date, "
                        "value_member_id, value_boolean, value_json) <= 1",
                        name="ck_icfv_single_value_col"),  # 至多一个值列非空(布尔 false 计非空)
    )
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True,
                                          server_default=text("gen_random_uuid()"))
    issue_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    field_def_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("custom_field_defs.id", ondelete="CASCADE"), nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_number: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    value_date: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    value_member_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False,
        server_default=text("now()"), onupdate=text("now()"))
```

> `num_nonnulls(...) <= 1` 提供数据库级兜底,防止跨类型脏值;布尔型用 `value_boolean`(false 也是有效值,故 CHECK 用 `num_nonnulls` 而非简单互斥)。服务层仍是类型/枚举/成员/必填校验的主战场。

---

## 3. 接口设计

REST 基础路径 `/api/v1`,`Authorization: Bearer <token>`,游标分页。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspaces/{ws}/labels` | 列出标签(可按 `project_id` 过滤;含工作区级) |
| POST | `/workspaces/{ws}/labels` | 创建标签 |
| PATCH | `/labels/{id}` | 编辑名称/颜色/描述 |
| DELETE | `/labels/{id}` | 删除标签(级联清 `issue_labels`) |
| POST | `/labels/{id}/merge` | 合并标签(把源标签的 issue 迁到目标) |
| GET | `/issues/{id}/labels` | 读取 issue 标签 |
| PUT | `/issues/{id}/labels` | 整体替换 issue 标签 |
| POST | `/issues/{id}/labels/{label_id}` | 增单个标签 |
| DELETE | `/issues/{id}/labels/{label_id}` | 删单个标签 |
| GET | `/workspaces/{ws}/custom-fields` | 列出字段定义(可按 `project_id`/`is_active` 过滤) |
| POST | `/workspaces/{ws}/custom-fields` | 创建字段定义(可携初始 options) |
| PATCH | `/custom-fields/{id}` | 编辑字段(改名/必填/默认/停用) |
| DELETE | `/custom-fields/{id}` | 删除字段(级联清值与选项) |
| GET/POST | `/custom-fields/{id}/options` | 列出/新增枚举选项 |
| PATCH/DELETE | `/custom-fields/{id}/options/{opt_id}` | 编辑/停用/删除选项 |
| GET | `/issues/{id}/custom-field-values` | 读取 issue 全部字段值 |
| PUT | `/issues/{id}/custom-field-values` | 批量设置 issue 字段值(整体提交) |

### 3.2 请求/响应示例

**创建标签** `POST /api/v1/workspaces/{ws}/labels`
```jsonc
// Request
{ "name": "bug", "color": "#e5484d", "project_id": null }
// 201 Response
{ "id": "lbl_1", "name": "bug", "color": "#e5484d", "description": null,
  "project_id": null, "scope": "workspace", "created_at": "2026-07-24T10:00:00Z" }
```

**合并标签** `POST /api/v1/labels/lbl_defect/merge`
```jsonc
{ "target_label_id": "lbl_bug" }
// 200:{ "merged_issue_count": 12, "target_label": { "id": "lbl_bug", "name": "bug" } }
// 源标签 lbl_defect 删除,其 issue 改挂 lbl_bug(去重)
```

**创建枚举字段** `POST /api/v1/workspaces/{ws}/custom-fields`
```jsonc
// Request
{ "name": "严重程度", "field_key": "severity", "type": "single_select", "is_required": false,
  "options": [ { "name": "Minor", "color": "#888888" },
               { "name": "Major", "color": "#f5a623" },
               { "name": "Critical", "color": "#e5484d" } ] }
// 201 Response —— 返回字段定义,options 各带生成的 id
```

**给 issue 设置字段值** `PUT /api/v1/issues/{id}/custom-field-values`
```jsonc
// Request —— 按类型传对应值字段;single_select 传 option_id,multi_select 传数组
{ "values": [
  { "field_def_id": "cf_sev", "value_json": "opt_major" },
  { "field_def_id": "cf_users", "value_number": 1500 } ] }
// 200 Response —— 返回该 issue 全部字段值(含字段定义快照)
```

**给 issue 打标签** `POST /api/v1/issues/{id}/labels/lbl_1`
```jsonc
// 200:{ "labels": [ { "id": "lbl_1", "name": "bug", "color": "#e5484d" } ] }
```

**按标签/字段筛选 issue**(走 issue.md 列表 / kanban.md 视图执行):
```
GET /api/v1/workspaces/{ws}/issues?label=lbl_1&cf_severity=opt_major
```

### 3.3 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 颜色非法、name 超长、field_key 格式非法、类型不支持 |
| 401 | `unauthorized` | 缺失/失效 token |
| 403 | `forbidden` | 项目级标签/字段无权限;非 admin 改字段定义 |
| 404 | `not_found` | 标签/字段/选项不存在 |
| 409 | `label_name_taken` | 同作用域内标签重名 |
| 409 | `field_key_taken` | 同作用域内 field_key 重名 |
| 409 | `conflict` | 选项名重名;合并目标即源 |
| 422 | `invalid_field_value` | 值与字段类型不符;枚举值不属于该字段;member 非工作区成员;值列填错列 |
| 422 | `required_field_missing` | 必填字段未填(保存或状态流转时;`details` 列出缺失字段) |
| 422 | `field_inactive` | 给已停用字段写值 |
| 422 | `label_scope_mismatch` | 把项目级标签打给其它项目的 issue |
| 429 | `rate_limited` | 限流 |

### 3.4 分页、鉴权与安全

- **分页**:标签/字段定义/选项均游标分页,游标编码 `(created_at, id)`;数量通常较小,亦支持 `?limit=200` 一次性返回 + 客户端缓存。issue 的字段值/标签随 issue 返回,不单独分页。
- **鉴权**:读取需工作区成员;创建/编辑/删除标签与字段定义需工作区 admin(项目级标签/字段亦允许该项目 lead);给 issue 打标签/填值需对该 issue 有写权限(见 issue.md)。
- **输入校验**:颜色正则、name/field_key 长度与字符集、URL 合法性、数值精度范围在服务层强制;枚举/成员值校验归属关系。所有写入参数化绑定。
- **统一错误信封**:
```jsonc
{ "error": { "code": "required_field_missing",
             "message": "进入 Done 前需填写以下必填字段",
             "details": { "missing": [ { "field_def_id": "cf_acceptor", "name": "验收人" } ] } } }
```

### 3.5 WebSocket 增量合并事件

- **连接与重放**:同 kanban.md §3.5——`wss://<host>/ws`,帧含 `seq`,`resume_from` 重放,过旧 `resync`。
- **事件清单**(`<entity>.<action>`):

| 事件 | data(关键字段) | 客户端增量动作 |
|------|------------------|----------------|
| `label.created` / `label.updated` / `label.deleted` | label 对象 / diff / `id` | 刷新标签缓存与选择器联想列表 |
| `issue.labels_changed` | `issue_id` + 新 labels 数组 | 卡片色点/行标签即时更新;按视图 filters 重判该 issue 进出 |
| `custom_field.updated` | 字段定义 diff(含 `is_active`) | 所有打开的 issue/视图刷新该字段渲染;停用时隐藏 |
| `custom_field_option.updated` | `field_def_id` + 选项 diff | 刷新缓存了该字段选项的客户端 |
| `issue.custom_field_changed` | `issue_id` + `field_def_id` + 新值 | 详情/列表该单元格即时刷新;按视图 filters 重判进出 |

- **增量合并原则**:视图按标签/属性筛选时,收到 `issue.labels_changed` / `issue.custom_field_changed` 后,客户端用**当前视图 filters** 重判该 issue 是否仍属于视图,做单卡进出(与 kanban.md §3.5 一致),不整板刷新。
- **枚举选项变更**需广播 `custom_field_option.updated`,通知所有缓存该字段选项的客户端刷新(含筛选器弹层、详情下拉)。
- **降级**:WS 断开 → 轮询 issue 增量接口;标签/字段定义变更走 `GET /workspaces/{ws}/labels` / `/custom-fields` 带 `since=<updated_at>`。

---

## 4. UI/UX

### 4.1 信息架构

```
设置 → 标签管理
   ├── 标签列表:色点 | 名称 | 作用域 | 使用次数 | 操作(编辑/合并/删除)
   └── [+ 新建标签]

设置 → 自定义字段
   ├── 字段列表:名称 | 类型图标 | 作用域 | 必填 | 状态(active/inactive) | 操作
   └── [+ 新建字段] → 类型选择 → 配置(选项/必填/默认值/作用域/展示排序)

Issue 详情侧栏
   ├── 标签区:标签 chip(+ 输入联想添加 + 就地新建)
   └── 自定义字段区:按 position 排序渲染各类型控件
```

### 4.2 标签选择器

- 输入框 + 已有标签下拉联想(按名称前缀/子串);选中以彩色 chip 展示,chip 可点 × 移除。
- 支持**就地新建**:联想无命中时,出现"新建 'xxx'"项,弹出颜色选择即建。
- 项目级标签仅在对应项目的 issue 上出现在联想列表;工作区级标签全区可见。
- 卡片/行上以**色点**紧凑呈现(多标签时多个色点,溢出 `+N`)。

### 4.3 自定义字段编辑器

- **按类型渲染控件**:
  - text / textarea / url → 文本输入框(textarea 多行;url 带格式校验提示)
  - number → 数字输入(按 `config.precision` 约束,可带单位后缀)
  - date / datetime → 日历 / 日期时间选择器
  - single_select → 单选下拉(选项带颜色)
  - multi_select → 多选 chip
  - member → 成员选择器(人 + agent 混合列表,各带类型图标)
  - boolean → 开关
- **字段定义编辑器**(设置页):选类型后动态显示对应配置——枚举的选项管理(增删改、拖拽排序 `position`、配色、停用)、必填开关、`required_on` 时机、默认值、作用域选择、展示 `position`。
- **筛选器中的字段**(见 kanban.md):筛选弹层自动列出所有自定义字段作为可选条件;枚举字段渲染为选项多选;数值/日期渲染为范围/比较输入。
- **停用字段**:详情页隐藏 inactive 字段,但已有值在数据库保留(重新启用后恢复显示)。

### 4.4 关键交互流程

- **创建并应用标签**:设置 → 标签 → 新建(填名称、选颜色)→ 保存;回到 issue → 标签区输入首字母 → 联想命中 → 选中 → 卡片出现色点(广播 `issue.labels_changed`)。
- **定义枚举字段并使用**:设置 → 自定义字段 → 新建 → 选"单选" → 命名"严重程度" → 添加选项(各配色)→ 设作用域 → 保存;issue 详情侧栏即出现该字段,点选赋值(广播 `issue.custom_field_changed`)。
- **按属性筛选并存视图**:看板工具条 → 筛选 → 加条件"严重程度 = Major"且"标签 含 bug" → 看板即时收窄 → "另存为视图"(见 kanban.md)。
- **标签合并**:设置 → 标签 → 选源标签"defect" → 合并到"bug" → 确认影响数 → 执行;所有带 defect 的卡片色点更新为 bug。

### 4.5 状态流转(字段/选项生命周期 + 必填校验)

```
字段定义: active ──停用──► inactive(隐藏,保留数据) ──启用──► active
                └──删除──► (级联删除该字段所有值与选项)
选项:    active ──停用──► inactive(已有值保留,新选不可用) ──删除──► (已有该选项的值如何处理:multi 移除该项;single 置空,服务层执行)
```

**必填校验时机**(由 `required_on` 配置):
- `save`:issue 保存(`POST`/`PATCH /issues/{id}`)时校验。
- `status:<category>`(如 `status:done`):状态流转到该 category 时校验;缺失返回 `422 required_field_missing` 并就地阻断(非通知,属校验)。issue.md 在状态流转前调用本模块校验钩子。

### 4.6 通知触发点

- 标签/字段被删除或合并:影响范围通知相关项目成员(可选)。
- 必填字段在状态流转时缺失:就地阻断并提示(校验,非通知)。
- issue 因标签/字段变化触发的业务通知(如"严重程度变为 Critical 时通知负责人")属自动化模块范畴,本模块仅产出变更事件。

---

## 5. 验收标准

### 5.1 功能验收 —— 标签

- [ ] 可创建/编辑/删除标签;name 1–50、颜色 `#RRGGBB` 校验生效,非法返回 `400`。
- [ ] 作用域生效:`project_id=NULL` 为工作区级全区可用;项目级仅在该项目可用;`UNIQUE (workspace_id, COALESCE(project_id,NIL), name)` 防同作用域重名(重名返回 `409 label_name_taken`)。
- [ ] issue 多对多打标签:增/删单个、整体替换均正确;项目级标签打给异项目 issue 返回 `422 label_scope_mismatch`。
- [ ] 合并标签:源标签 issue 迁到目标(去重),源标签删除,返回迁移计数。
- [ ] 标签选择器联想命中、就地新建、彩色 chip、卡片色点(溢出 `+N`)均生效。
- [ ] 删除标签级联清 `issue_labels`,卡片色点实时消失。

### 5.2 功能验收 —— 自定义字段

- [ ] 可创建 10 种类型字段;`field_key` 格式与同作用域唯一性校验生效(重名返回 `409 field_key_taken`)。
- [ ] 枚举字段选项 CRUD:增删改、拖拽排序、配色、停用;`UNIQUE (field_def_id, name)` 生效。
- [ ] 在 issue 上填值:`PUT /issues/{id}/custom-field-values` 整体提交;按类型只填对应值列,填错列返回 `422 invalid_field_value`。
- [ ] 枚举值必须属于该字段 active 选项;member 必须为工作区成员;非法返回 `422 invalid_field_value`。
- [ ] 必填校验:依 `required_on` 在保存 / 状态流转(如 `status:done`)时校验,缺失返回 `422 required_field_missing` 并阻断。
- [ ] 停用字段:隐藏但保留数据,重新启用恢复;给 inactive 字段写值返回 `422 field_inactive`。
- [ ] 删除字段级联清值与选项;`num_nonnulls(...) <= 1` 兜底无跨类型脏值。
- [ ] 字段值可作为视图筛选/分组/排序依据:枚举命中 GIN、数值/日期命中部分索引;在 kanban.md 视图执行中返回正确分组/排序。

### 5.3 实时一致性验收

- [ ] WebSocket 帧含 `seq`,`resume_from` 重放、过旧 `resync` 与 kanban.md 一致。
- [ ] `label.*` / `custom_field.updated` / `custom_field_option.updated` 广播后,所有缓存客户端的标签选择器/字段控件/筛选器同步刷新。
- [ ] `issue.labels_changed` / `issue.custom_field_changed` 触发卡片色点/单元格即时刷新,并按当前视图 filters 增量进出,不整板刷新。
- [ ] 枚举选项停用/删除后,所有打开该字段下拉的客户端即时更新选项列表。
- [ ] WS 断开降级为带 `since=<updated_at>` 的轮询,恢复后回到增量模式。

### 5.4 非功能验收

- [ ] **查询性能**:按自定义字段筛选/分组在 10 万 issue 工作区下 P95 < 500ms;枚举筛选命中 `idx_icfv_value_json`,数值/日期命中部分索引(有 EXPLAIN 证据)。
- [ ] **写入性能**:单次 `PUT custom-field-values`(≤20 字段)P95 < 200ms;打标签单操作 P95 < 100ms。
- [ ] **一致性**:并发写同一 issue 的同一字段最终一致(走 issue.md 乐观并发 `updated_at` 仲裁),无丢失更新。
- [ ] **缓存**:标签/字段定义/选项支持客户端缓存 + 事件失效;枚举选项变更必触发失效广播。
- [ ] **安全**:颜色/名称/field_key/URL/数值范围在服务层强制校验;枚举与 member 值校验归属关系;越权访问返回 `403`/`404`;所有写入参数化绑定无 SQL 注入。
- [ ] **限流**:标签/字段定义管理接口有 rate limit,超限返回 `429`。
