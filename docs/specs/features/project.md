# 项目(Project)功能 Spec

> **所属层**:项目管理核心层 —— 聚合层。项目是 issue 的"目标盒"与编号前缀来源,向上隶属工作区,向下聚合 issue / 里程碑 / 迭代。
>
> **依赖的其他 Spec**:
> - `workspace.md`(工作区):项目隶属 `workspaces`,`workspace_id` 为隔离键;鉴权基于工作区成员资格。
> - `member.md`(成员):项目负责人 `lead_member_id`、项目成员 `project_members.member_id` 均引用统一成员名册 `members.id`(人类与 AI agent 对称)。
>
> **被依赖(下游 Spec)**:
> - `issue.md`(工作项):`issues.project_id` 引用本模块;项目前缀 `key` 是 issue 编号(`<KEY>-<number>`)的来源;`projects.issue_seq` 是 issue 项目级自增计数器。
> - `kanban.md`(视图):项目页内嵌看板/列表/时间线视图。
> - `label-property.md`(标签与自定义字段):标签 / 自定义字段可为项目级作用域(`project_id`)。
>
> **技术基准约定(全局锚点)**:PostgreSQL;表名 snake_case 复数;主键 `id UUID` 默认 `gen_random_uuid()`;`created_at`/`updated_at` 为 `TIMESTAMPTZ`;按需软删除(`deleted_at`)。REST 前缀 `/api/v1`,Bearer token,游标分页响应 `{"data","next_cursor"}`,时间一律 RFC3339 UTC,统一错误信封 `{"error":{"code","message","details"}}`。实时走 WebSocket `/ws`,频道订阅 + `seq` + 断线重放,事件命名 `<entity>.<action>`。ORM 采用 SQLAlchemy 2.x 声明式约定(或等价 DDL)。

---

## 1. 功能描述

### 1.1 模块定位

项目(Project)是团队围绕一个目标(如"官网改版""v2.0 发布")组织工作的容器。它承担四个职责:

1. **归属与隔离**:issue 归属到项目,项目内 issue 共享状态集、默认值与编号空间。
2. **编号前缀**:项目前缀 `key`(如 `WEB`)是 issue 人类可读编号 `WEB-123` 的来源。
3. **进度与健康度**:聚合子 issue 完成率得到自动进度,叠加人工填写的健康度(红/黄/绿),共同呈现给管理者。
4. **节奏管理**:通过里程碑(目标盒)与迭代/周期(时间盒)规划交付节奏。

项目是"目标盒",迭代/周期是"时间盒",二者正交:一个 issue 可同时属于一个项目和一个周期。

### 1.2 功能点与用户场景

#### 1.2.1 项目实体

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 创建项目 | 名称、前缀 `key`、描述、负责人、起止日期、图标/颜色、可见性 | PM 新建"官网改版",指定前缀 `WEB` |
| 归档/删除 | 完成后归档(只读保留);删除为软删除 | 交付后归档,不再出现在活跃列表 |
| 成员与可见性 | 项目级成员与角色;`private` 项目仅成员可见 | 把"官网改版"设为私有,仅相关同事可见 |
| 描述/文档 | 富文本/Markdown 描述、目标、链接 | 在项目首页写背景与目标 |
| 项目分组(可选) | 按团队/主题分组或打标签 | 把市场类项目归入"市场"分组 |

#### 1.2.2 状态与健康度

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 项目状态 | `planning`/`active`/`paused`/`completed`/`cancelled` | PM 把项目从 planning 切到 active |
| 健康度(traffic light) | `on_track`/`at_risk`/`off_track` + 文字说明 | PM 每周更新为"有风险"并写原因 |
| 状态更新留痕 | 每次健康度/状态变更生成一条"项目更新"记录(作者、时间、说明) | 领导查看历史健康度变化 |
| 自动进度 | 依据子 issue `state_category='done'` 占比计算 | 项目页显示"62% 完成" |

> **关键设计点**:健康度 = 人工判断 + 自动进度并存。自动进度由 issue 聚合算出(见 `issue.md` 的 `state_category`);健康度由人填写并附说明,二者一起呈现。

#### 1.2.3 里程碑(Milestone)

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 定义里程碑 | 名称、目标日期、描述 | 设定"v1.0 上线",目标 8/31 |
| 与 issue 关联 | issue 可挂到里程碑;进度 = 其下 issue 完成率 | 把关键 issue 挂到里程碑看完成度 |
| 时间线展示 | 在甘特/时间线视图上展示节点 | 路线图上看各里程碑 |
| 逾期标记 | 目标日已过但未完成自动标红 | 里程碑过期未完成显示逾期 |

#### 1.2.4 项目与 issue 的归属

- issue **必属于一个工作区**,**通常属于一个项目**(`project_id` 可空 → 收进"收件箱/未归档")。
- 项目是 issue 编号前缀来源(`WEB-123`),并提供项目级自增计数器 `issue_seq`(详见 `issue.md` §2 编号体系)。
- 项目聚合视图:项目页展示其下 issue 的看板/列表/时间线。
- 项目级默认值:新建 issue 时默认继承项目的状态集、默认 assignee 等。

#### 1.2.5 迭代/周期(Cycle / Sprint)

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 定义周期 | 名称、起止日期(常为固定长度,如 2 周) | 建立"第 12 迭代",8/1–8/14 |
| 自动滚动 | 按固定节奏自动生成下一周期 | 每两周自动开新迭代 |
| issue 入周期 | 把 issue 分配到某周期 | 站会把 5 个 issue 拖入本迭代 |
| 周期进度 | 已完成/总点数(或 issue 数)、燃尽 | 看本迭代燃尽图 |
| 未完成处理 | 周期结束未完成 issue 顺延下一周期或退回待办 | 迭代收尾,未完成顺延 |
| 周期 vs 项目 | 周期是时间盒,项目是目标盒;issue 可同时属于一个项目和一个周期 | "登录优化"属"官网项目"且排"第 12 迭代" |

### 1.3 边界与非目标

**本 Spec 范围内**:
- 项目 CRUD、归档/软删除、可见性与项目成员。
- 项目状态、健康度、状态更新留痕、进度聚合。
- 里程碑 CRUD 与 issue 关联。
- 周期 CRUD、自动滚动、issue 入周期、未完成顺延策略。

**非目标(由其他 Spec 承担)**:
- issue 本身的字段、状态机、编号生成、批量操作 → `issue.md`。
- 看板/列表/时间线视图的渲染与筛选/分组 → `kanban.md`。
- 评论、收件箱、@提及、通知中心 → `comment-inbox.md`。
- 项目级标签/自定义字段的定义 → `label-property.md`。
- agent 被分派后的执行运行时 → `agent.md`(本模块仅负责把负责人指向统一成员)。

---

## 2. 数据模型

> **全局契约引用**:本模块的 schema、同租户约束、成员模型、编号与前缀、实时、API 包络/错误/分页一律以 [README.md](../README.md) §6「全局权威契约」为准,本 Spec 仅引用、不重复定义(成员模型 README §6.1、同租户复合 FK README §6.2、编号与前缀永久保留 README §6.3、实时 README §6.7、API/错误/分页 README §6.14)。

### 2.1 ER 概览

```
workspaces ──1:N──► projects ──1:N──► issues(见 issue.md)
                       │                  │ N:1
                       ├──1:N──► milestones ◄┘ (issues.milestone_id)
                       ├──1:N──► project_updates(健康度/状态留痕)
                       ├──1:N──► project_members ──► members(统一名册)
                       │
                       └── issues 亦可 N:1 关联 cycles(issues.cycle_id)
workspaces ──1:N──► cycles(周期常为工作区级,可绑定项目)
```

跨模块外键汇总见 §2.5。

### 2.2 表定义

#### `projects`(项目)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 内部主键 |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键 |
| `name` | TEXT | NOT NULL | — | 1–120 字符 |
| `key` | TEXT | NOT NULL | — | 项目前缀,大写,如 `WEB`,`^[A-Z][A-Z0-9_]{1,11}$` |
| `description` | TEXT | NULL | NULL | 富文本/Markdown |
| `icon` | TEXT | NULL | NULL | 图标标识 |
| `color` | TEXT | NULL | NULL | 主题色(十六进制或调色板键) |
| `status` | TEXT | NOT NULL, CHECK IN ('planning','active','paused','completed','cancelled') | `'planning'` | 项目状态 |
| `health` | TEXT | NULL, CHECK IN ('on_track','at_risk','off_track') | NULL | 健康度 |
| `visibility` | TEXT | NOT NULL, CHECK IN ('public','private') | `'public'` | 可见性 |
| `lead_member_id` | UUID | NULL,复合 FK `(workspace_id, lead_member_id) → members(workspace_id, id)` ON DELETE SET NULL (lead_member_id)(PG16 列级,仅置空引用列,README §6.2 第 6 条) | NULL | 项目负责人(人或 agent;README §6.2) |
| `start_date` | DATE | NULL | NULL | 计划开始日 |
| `target_date` | DATE | NULL | NULL | 目标完成日 |
| `progress_cache` | REAL | NULL | NULL | 进度物化缓存(0.0–1.0,可选,见 §2.4) |
| `issue_seq` | BIGINT | NOT NULL | `0` | 该项目 issue 自增序号计数器(见 issue.md §2.4) |
| `archived_at` | TIMESTAMPTZ | NULL | NULL | 归档时间(非空即归档,只读) |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | 审计时间 |

**表级约束**:
- `CHECK (target_date IS NULL OR start_date IS NULL OR target_date >= start_date)`
- `CHECK (issue_seq >= 0)`
- `UNIQUE (workspace_id, id)` —— 供引用方复合 FK(README §6.2;`issues.project_id` 等据此同租户引用)
- 可选防同工作区重名:部分唯一索引 `uq_projects_name`(见 §2.3,`WHERE deleted_at IS NULL`,以 `CREATE UNIQUE INDEX` 表达,不写表级 UNIQUE)

#### `project_updates`(状态/健康度更新留痕)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键(供复合 FK,README §6.2) |
| `project_id` | UUID | NOT NULL, 复合 FK `(workspace_id, project_id) → projects(workspace_id, id)` ON DELETE CASCADE | — | 所属项目(README §6.2) |
| `author_member_id` | UUID | NOT NULL, 复合 FK `(workspace_id, author_member_id) → members(workspace_id, id)` ON DELETE RESTRICT | — | 作者(人或 agent;README §6.2) |
| `health` | TEXT | NULL, CHECK IN ('on_track','at_risk','off_track') | NULL | 本次健康度 |
| `status` | TEXT | NULL, CHECK IN ('planning','active','paused','completed','cancelled') | NULL | 本次状态 |
| `message` | TEXT | NULL | NULL | 说明文字 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | 留痕时间 |

> 追加式(append-only):仅插入,不更新。提交一条更新时,服务层同步把 `health`/`status` 回写到 `projects` 当前值。
>
> **`author_member_id` 为 `NOT NULL` + `ON DELETE RESTRICT`(修复原 NOT NULL 与 ON DELETE SET NULL 自相矛盾,README §6.2)**:留痕作者不可为空、历史不可悬空;成员一律经 `members.status='removed'` **软删除**而非物理 DELETE,故 RESTRICT 不会阻塞正常移除流程,作者署名永久保留。

#### `milestones`(里程碑)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键(供复合 FK 与 `UNIQUE(workspace_id, id)`,README §6.2) |
| `project_id` | UUID | NOT NULL, 复合 FK `(workspace_id, project_id) → projects(workspace_id, id)` ON DELETE CASCADE | — | 所属项目(README §6.2) |
| `title` | TEXT | NOT NULL | — | 1–120 |
| `description` | TEXT | NULL | NULL | |
| `target_date` | DATE | NULL | NULL | 目标日 |
| `state` | TEXT | NOT NULL, CHECK IN ('open','closed') | `'open'` | 开/关 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**表级约束**:`UNIQUE (workspace_id, id)` —— 供 `issues.milestone_id` 复合 FK 引用(README §6.2)。

> 逾期为派生状态:`state='open' AND target_date < CURRENT_DATE` → UI 标红,不落库。

#### `cycles`(迭代/周期)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 周期常为工作区级 |
| `project_id` | UUID | NULL, 复合 FK `(workspace_id, project_id) → projects(workspace_id, id)` ON DELETE CASCADE | NULL | 若绑定到项目则填(README §6.2) |
| `name` | TEXT | NOT NULL | — | 如"第 12 迭代" |
| `starts_at` | DATE | NOT NULL | — | 起 |
| `ends_at` | DATE | NOT NULL | — | 止 |
| `state` | TEXT | NOT NULL, CHECK IN ('planned','active','completed') | `'planned'` | |
| `auto_roll` | BOOLEAN | NOT NULL | `false` | 是否自动滚动生成下一周期 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**表级约束**:`CHECK (ends_at >= starts_at)`;`UNIQUE (workspace_id, id)` —— 供 `issues.cycle_id` 复合 FK 引用(README §6.2)。

#### `project_members`(项目成员/可见性)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | 隔离键(供复合 FK,README §6.2) |
| `project_id` | UUID | NOT NULL, 复合 FK `(workspace_id, project_id) → projects(workspace_id, id)` ON DELETE CASCADE | — | 所属项目(README §6.2) |
| `member_id` | UUID | NOT NULL, 复合 FK `(workspace_id, member_id) → members(workspace_id, id)` ON DELETE CASCADE | — | 人或 agent(README §6.2) |
| `role` | TEXT | NOT NULL, CHECK IN ('lead','member','viewer') | `'member'` | 项目内角色 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**表级约束**:`UNIQUE (project_id, member_id)`。

### 2.3 索引与约束

```sql
-- projects
-- 前缀永久保留:普通(非部分)唯一索引,软删除/归档后前缀亦不可复用(README §6.3)
CREATE UNIQUE INDEX uq_projects_key ON projects(workspace_id, key);
-- 供引用方复合 FK 的同租户唯一约束(README §6.2)
CREATE UNIQUE INDEX uq_projects_ws_id ON projects(workspace_id, id);
-- 可选:防同工作区重名(部分唯一,仅未删除项目)
CREATE UNIQUE INDEX uq_projects_name
  ON projects(workspace_id, name) WHERE deleted_at IS NULL;
CREATE INDEX idx_projects_workspace
  ON projects(workspace_id, status)
  WHERE deleted_at IS NULL AND archived_at IS NULL;
CREATE INDEX idx_projects_lead ON projects(lead_member_id);

-- milestones / cycles / updates
CREATE UNIQUE INDEX uq_milestones_ws_id ON milestones(workspace_id, id);
CREATE INDEX idx_milestones_project ON milestones(project_id, state);
CREATE UNIQUE INDEX uq_cycles_ws_id ON cycles(workspace_id, id);
CREATE INDEX idx_cycles_workspace ON cycles(workspace_id, starts_at);
CREATE INDEX idx_cycles_state ON cycles(workspace_id, state);
CREATE INDEX idx_project_updates_project
  ON project_updates(project_id, created_at DESC);

-- project_members
CREATE INDEX idx_project_members_member ON project_members(member_id);
```

> **`uq_projects_key` 为普通(非部分)唯一索引,前缀永久保留**(README §6.3):**不带** `WHERE deleted_at IS NULL`——项目前缀 `key` 一经使用即在该工作区内**永久占用**,软删除/归档项目后**不可被新项目复用**。这是 identifier 不可变语义的基石:`identifier = <key> || '-' || number` 永不改变,删除项目仅把 `issues.project_id` 置 NULL(`identifier` 随 issue 保留),历史 `WEB-123` 永远指向同一 issue,杜绝歧义;项目亦不得跨工作区迁移。
>
> **与前缀注册表的衔接(R2)**:创建项目在**同一事务**内向工作区级前缀注册表 `identifier_prefix_registry`(workspace.md owns,README §6.3)登记 `kind='project'` 条目;新 `key` 与任一在册前缀冲突(含已软删除/归档项目的历史前缀、`retired` 历史收件箱前缀与当前收件箱前缀)即返回 409 `project_key_taken`。**软删除项目的 key 仍永久保留**——`uq_projects_key` 非部分唯一索引与注册表**双重保证**,杜绝 identifier 前缀复用(README §6.3)。

### 2.4 项目进度聚合(派生)

进度默认不持久化,查询时聚合(依赖 `issue.md` 的 `issues.state_category`):

```sql
SELECT
  COUNT(*) FILTER (WHERE i.state_category = 'done') * 1.0
    / NULLIF(COUNT(*), 0) AS progress,
  COUNT(*) FILTER (WHERE i.state_category <> 'done' AND i.state_category <> 'cancelled') AS open_issues,
  COUNT(*) FILTER (WHERE i.state_category = 'done') AS done_issues
FROM issues i
WHERE i.project_id = $1 AND i.deleted_at IS NULL;
```

高频读场景可在 issue 状态变更时增量更新 `projects.progress_cache`(物化字段),并经 `project.updated` 广播;`progress_cache` 为 NULL 时回退到实时聚合。`cancelled` 不计入未完成,避免拉低进度。

### 2.5 跨模块外键

| 字段 | 引用 | 说明 |
|------|------|------|
| `projects.workspace_id` | `workspaces(id)` | 隶属工作区(workspace.md) |
| `projects.lead_member_id` | 复合 FK `(workspace_id, lead_member_id) → members(workspace_id, id)`,ON DELETE SET NULL (lead_member_id) | 负责人,人或 agent(member.md;PG16 列级,README §6.2 第 6 条) |
| `project_members.member_id` | 复合 FK `(workspace_id, member_id) → members(workspace_id, id)`,ON DELETE CASCADE | 项目成员(member.md;README §6.2) |
| `project_updates.author_member_id` | 复合 FK `(workspace_id, author_member_id) → members(workspace_id, id)`,ON DELETE RESTRICT | 留痕作者(member.md;成员软删除,RESTRICT 保历史) |
| `project_updates.project_id` | 复合 FK `(workspace_id, project_id) → projects(workspace_id, id)`,ON DELETE CASCADE | 所属项目 |
| `project_members.project_id` | 复合 FK `(workspace_id, project_id) → projects(workspace_id, id)`,ON DELETE CASCADE | 所属项目 |
| `milestones.project_id` | 复合 FK `(workspace_id, project_id) → projects(workspace_id, id)`,ON DELETE CASCADE | 所属项目 |
| `cycles.workspace_id` | `workspaces(id)` | 周期隶属工作区 |
| `cycles.project_id` | 复合 FK `(workspace_id, project_id) → projects(workspace_id, id)`,ON DELETE CASCADE | 可选绑定项目 |
| `issues.project_id` | 复合 FK `(workspace_id, project_id) → projects(workspace_id, id)`,ON DELETE SET NULL (project_id) | 下游引用(issue.md;删项目编号保留;PG16 列级,README §6.2 第 6 条;**迁移只改 `project_id`,`identifier` 不变**,README §6.3) |
| `issues.milestone_id` | 复合 FK `(workspace_id, milestone_id) → milestones(workspace_id, id)`,ON DELETE SET NULL (milestone_id) | 下游引用(issue.md;PG16 列级,README §6.2 第 6 条) |
| `issues.cycle_id` | 复合 FK `(workspace_id, cycle_id) → cycles(workspace_id, id)`,ON DELETE SET NULL (cycle_id) | 下游引用(issue.md;PG16 列级,README §6.2 第 6 条) |

> **同租户约束约定(README §6.2)**:凡引用 `members`/`projects`/`milestones`/`cycles` 的表,均**同时存 `workspace_id` 并建复合 FK** `(workspace_id, <ref>_id) → 目标表(workspace_id, id)`;被引用的 `projects`/`milestones`/`cycles` 均建 `UNIQUE(workspace_id, id)`(见 §2.3)。如此"引用了别的工作区的对象"在 INSERT 时即被数据库拒绝(集成测试 T1),并在其上加 PostgreSQL RLS 纵深防御。

### 2.6 SQLAlchemy 2.x 声明式约定(核心表示例)

```python
from datetime import date, datetime
from typing import Optional
from sqlalchemy import (
    BigInteger, CheckConstraint, Date, ForeignKey, ForeignKeyConstraint,
    Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planning','active','paused','completed','cancelled')",
            name="ck_projects_status",
        ),
        CheckConstraint("issue_seq >= 0", name="ck_projects_issue_seq_nonneg"),
        # 供引用方复合 FK(README §6.2)
        UniqueConstraint("workspace_id", "id", name="uq_projects_ws_id"),
        # 负责人复合 FK:同租户引用 members(workspace_id, id)(README §6.2)
        ForeignKeyConstraint(
            ["workspace_id", "lead_member_id"],
            ["members.workspace_id", "members.id"],
            name="fk_projects_lead_member",
            ondelete="SET NULL (lead_member_id)",  # (PG16 列级 SET NULL,README §6.2 第 6 条)
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="planning")
    health: Mapped[Optional[str]] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default="public")
    lead_member_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))
    target_date: Mapped[Optional[date]] = mapped_column(Date)
    issue_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    archived_at: Mapped[Optional[datetime]] = mapped_column()
    deleted_at: Mapped[Optional[datetime]] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=text("now()")
    )

    milestones: Mapped[list["Milestone"]] = relationship(
        cascade="all, delete-orphan", back_populates="project"
    )
```

> 其余表(`project_updates`/`milestones`/`cycles`/`project_members`)按同样约定映射:`onupdate=text("now()")` 维护 `updated_at`,枚举用 `CheckConstraint`,可空外键用 `Optional[...]`;**一切对 `members`/`projects`/`milestones`/`cycles` 的引用均经 `ForeignKeyConstraint(["workspace_id", "<ref>_id"], ["<target>.workspace_id", "<target>.id"])` 建复合 FK**(README §6.2),被引用表建 `UniqueConstraint("workspace_id", "id")`。仓库内统一使用 `UUID(as_uuid=False)` 或团队约定的 UUID 类型。

---

## 3. 接口设计

REST 基础路径 `/api/v1`,`Authorization: Bearer <token>`,游标分页。**成功包络、游标分页、错误信封、HTTP 语义、乐观并发与幂等写一律以 README §6.14 为权威**,本 Spec 仅列模块专属错误码与示例,不重复定义公共契约。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/workspaces/{ws}/projects` | 创建项目 |
| GET | `/workspaces/{ws}/projects` | 列出项目(过滤 status/archived/visibility) |
| GET | `/projects/{id}` | 获取项目(含进度聚合) |
| PATCH | `/projects/{id}` | 更新字段/状态/可见性 |
| DELETE | `/projects/{id}` | 软删除 |
| POST | `/projects/{id}/archive` | 归档 |
| POST | `/projects/{id}/unarchive` | 取消归档 |
| POST | `/projects/{id}/updates` | 提交一条状态/健康度更新 |
| GET | `/projects/{id}/updates` | 历史更新列表 |
| GET | `/projects/{id}/milestones` | 里程碑列表 |
| POST | `/projects/{id}/milestones` | 创建里程碑 |
| PATCH | `/milestones/{id}` | 更新里程碑 |
| DELETE | `/milestones/{id}` | 删除里程碑 |
| GET | `/workspaces/{ws}/cycles` | 周期列表 |
| POST | `/workspaces/{ws}/cycles` | 创建周期 |
| PATCH | `/cycles/{id}` | 更新周期(含状态切换) |
| POST | `/projects/{id}/members` | 添加项目成员 |
| PATCH | `/projects/{id}/members/{member_id}` | 变更项目成员角色 |
| DELETE | `/projects/{id}/members/{member_id}` | 移除项目成员 |

### 3.2 请求/响应示例

**创建项目** `POST /api/v1/workspaces/{ws}/projects`
```json
// Request
{ "name": "官网改版", "key": "WEB", "target_date": "2026-08-31", "visibility": "public" }

// 201 Response
{
  "data": {
    "id": "0d6f1a2e-0000-0000-0000-000000000001",
    "name": "官网改版",
    "key": "WEB",
    "status": "planning",
    "health": null,
    "visibility": "public",
    "progress": 0.0,
    "issue_seq": 0,
    "target_date": "2026-08-31",
    "created_at": "2026-07-24T10:00:00Z",
    "updated_at": "2026-07-24T10:00:00Z"
  }
}
```

**获取项目(含进度)** `GET /api/v1/projects/{id}`
```json
{
  "data": {
    "id": "0d6f1a2e-0000-0000-0000-000000000001",
    "name": "官网改版",
    "key": "WEB",
    "status": "active",
    "health": "at_risk",
    "progress": 0.62,
    "open_issues": 15,
    "done_issues": 25,
    "lead": { "id": "mem_uuid_a1", "name": "Jane Doe", "member_type": "human" },
    "milestones": [
      { "id": "ms_uuid_1", "title": "v1.0 上线", "target_date": "2026-08-31", "state": "open", "overdue": false }
    ],
    "target_date": "2026-08-31",
    "created_at": "2026-07-24T10:00:00Z",
    "updated_at": "2026-07-24T11:00:00Z"
  }
}
```

**提交健康度更新** `POST /api/v1/projects/{id}/updates`
```json
// Request
{ "health": "at_risk", "message": "第三方接口延期,存在上线风险" }

// 201 Response(返回留痕记录;同时回写 projects.health)
{
  "data": {
    "id": "pu_uuid_1",
    "project_id": "0d6f1a2e-0000-0000-0000-000000000001",
    "author": { "id": "mem_uuid_a1", "name": "Jane Doe", "member_type": "human" },
    "health": "at_risk",
    "status": null,
    "message": "第三方接口延期,存在上线风险",
    "created_at": "2026-07-24T11:00:00Z"
  }
}
```

**列出周期** `GET /api/v1/workspaces/{ws}/cycles?state=active`
```json
{
  "data": [
    { "id": "cyc_uuid_12", "name": "第 12 迭代", "starts_at": "2026-08-01",
      "ends_at": "2026-08-14", "state": "active", "auto_roll": true }
  ],
  "next_cursor": null
}
```

**添加项目成员** `POST /api/v1/projects/{id}/members`
```json
// Request
{ "member_id": "mem_uuid_b2", "role": "member" }

// 201 Response
{ "data": { "id": "pm_uuid_1", "project_id": "0d6f1a2e-...", "member_id": "mem_uuid_b2", "role": "member" } }
```

**错误响应(统一信封)**
```json
{ "error": { "code": "project_key_taken", "message": "前缀 WEB 已被占用", "details": { "key": "WEB" } } }
```

### 3.3 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | `key` 非大写/超长;`ends_at < starts_at`;`target_date < start_date` |
| 401 | `unauthorized` | token 缺失/失效 |
| 403 | `forbidden` | 私有项目非成员访问;写操作角色不足 |
| 404 | `not_found` | 项目不存在或不可见 |
| 409 | `project_key_taken` | 前缀在工作区内已被占用(**含已软删除/归档项目——前缀永久保留,不可复用**;含与已软删除/归档项目、`retired` 历史前缀、当前收件箱前缀的冲突——前缀注册表排他,README §6.3) |
| 409 | `cycle_overlap`(可选) | 同范围周期重叠 |
| 422 | `project_archived` | 对已归档项目写入 |
| 429 | `rate_limited` | 触发限流 |

### 3.4 分页与鉴权

- **分页**:项目列表、更新历史、里程碑、周期均游标分页;请求 `?limit=&cursor=`,响应 `{"data","next_cursor"}`(末页 `next_cursor=null`)。默认按 `created_at DESC, id` 排序,游标内部为 base64 编码的 `(sort_key, id)`。**包络 / 游标 / 错误信封以 README §6.14 为权威。**
- **鉴权**:
  - 读:工作区成员可读 `public` 项目;`private` 项目需 `project_members` 命中(或工作区 admin 及以上)。
  - 写:项目 `member`/`lead` 或工作区 `admin` 及以上;删除/归档需 `lead` 或 `admin`;成员管理需 `lead` 或 `admin`。
- **限流**:写端点按工作区维度限流,超限返回 429。

### 3.5 WebSocket 事件

- **频道订阅**:`/ws` 上订阅 `project:{id}`(详情级)与 `workspace:{ws}:projects`(列表级)。**私有项目事件只进 `project:{id}` 频道,不得先广播给 `workspace:{ws}:*` 再靠前端过滤;每次订阅重新做资源级授权(README §6.7)。**
- **seq 与断线重放(统一实时契约 README §6.7)**:每条事件携带**频道内**单调递增 `seq`(持久化于 `realtime_events`,无"全局 seq");客户端重连带 `resume_from=<last_seq+1>` 自该点补发,游标过旧收 `resync_required` + REST 对账水位,保证不漏不重。
- **事件清单**(`<entity>.<action>`):

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `project.created` | 创建项目 | `project`(摘要) |
| `project.updated` | 状态/健康度/进度/字段变更 | `id`、`changes`(diff)、`progress` |
| `project.archived` / `project.unarchived` | 归档/取消归档 | `id` |
| `project.deleted` | 软删除 | `id` |
| `project_update.added` | 新增健康度/状态留痕 | `update`(含 health/status/message) |
| `milestone.created` / `milestone.updated` / `milestone.deleted` | 里程碑变更 | `milestone` |
| `cycle.updated` | 周期变更/状态切换 | `cycle` |

> 进度聚合在 issue 状态变更时计算(见 issue.md `issue.updated`),并经 `project.updated` 广播,项目页进度条实时刷新。

---

## 4. UI/UX 设计

### 4.1 信息架构

```
项目列表页(/projects)
   ├── [筛选: 状态 / 负责人 / 我参与的 / 已归档]   [+ 新建项目]
   └── 项目卡片网格/列表:
        名称+图标 | 状态徽章 | 健康度灯 | 进度条 | 负责人头像(人/agent) | 目标日期

项目详情页(/projects/{id})
   ├── 头部:名称 / 状态 / 健康度灯 / 进度 / 负责人 / 目标日期   [更新状态] [···]
   ├── Tab:概览 | Issue(看板/列表) | 里程碑 | 时间线 | 更新动态
   └── 侧栏:字段、成员、设置

周期页(/cycles/{id})
   ├── 头部:名称 / 起止 / 状态 / 燃尽与点数
   └── 待办·未排期区 + 本周期 issue 列表
```

### 4.2 关键组件

- **健康度灯**:红/黄/绿三色圆点 + 文字;点击展开"更新状态"表单(选健康度 + 写说明),提交即留痕。
- **进度条**:基于 issue 完成率的环形/条形进度;悬停显示 `done/total`。
- **里程碑时间线**:横向时间轴,节点=里程碑,标注目标日与完成度,逾期(`open` 且过 target_date)标红。
- **周期切换器**:看板/列表上方下拉,选"第 12 迭代"即按周期过滤 issue;周期页头部显示燃尽与点数。
- **项目状态徽章**:`planning`/`active`/`paused`/`completed`/`cancelled` 用不同颜色标签。
- **负责人选择器**:混合列出人类与 agent(各带类型图标),复用统一成员选择器(见 member.md)。

### 4.3 关键交互流程

**创建项目**:新建 → 填名称(自动建议大写 `key`)→ `key` 实时去重校验(绿勾/红叉)→ 选负责人/目标日/可见性 → 完成,进入空项目页。

**更新健康度**:项目头 → 点健康度灯 → 选红/黄/绿 + 写说明 → 提交 → 头部灯即时更新,"更新动态"Tab 新增一条留痕。

**周期排期**:进入周期页 → 从"待办/未排期"区把 issue 拖入本周期 → 周期进度与燃尽实时更新 → 周期结束触发"未完成 issue 顺延下一周期"提示。

**归档/删除**:头部 `···` → 归档(项目变只读,从活跃列表移除)或删除(二次确认,软删除,**前缀永久保留、不可复用**,README §6.3)。删除项目仅把其 issue 的 `project_id` 置 NULL(`ON DELETE SET NULL (project_id)` 列级,仅置空归属列,README §6.2 第 6 条,经 §9 T18 实测),issue 的 `identifier` 保持不变(编号随 issue 走)。

### 4.4 状态机

**项目状态**:
```
planning ──启动──► active ──完成──► completed
   │                 │  ▲
   │                 ▼  │
   └──取消──► cancelled   paused(暂停/恢复)
```
- 默认无强制顺序,可在任意状态间切换;`completed`/`cancelled` 为终态(可重新激活回 `active`)。
- 每次状态/健康度变更建议同步生成一条 `project_updates` 留痕。

**周期状态**:`planned ──开始──► active ──结束──► completed`。结束时触发未完成 issue 顺延策略。

### 4.5 实时与通知

- **实时**:订阅 `project:{id}` 与 `workspace:{ws}:projects`;事件见 §3.5。断线凭 `resume_from` 重放、游标过旧 `resync_required`(README §6.7);WS 长时间不可用降级为 30s 轮询 `GET /projects/{id}`。
- **通知触发点**:
  - 被设为项目负责人/加入项目:站内通知。
  - 健康度变为 `at_risk`/`off_track`:通知负责人与关注者(可选邮件)。
  - 里程碑临近/逾期:提醒负责人。
  - 周期开始/结束:通知周期内 issue 的相关成员。

---

## 5. 验收标准

### 5.1 功能性

- [ ] 创建项目时 `key` 经实时与服务端双重去重校验;同工作区项目前缀**永久唯一**(含已软删除/归档项目),占用返回 409 `project_key_taken`。
- [ ] `key` 格式校验生效:仅大写字母/数字/下划线,2–12 字符,首字符为字母;非法返回 400 `validation_error`。
- [ ] **前缀永久保留、不可复用(README §6.3)**:`uq_projects_key` 为**普通(非部分)唯一索引** `ON projects(workspace_id, key)`(不带 `WHERE deleted_at IS NULL`);软删除/归档项目后,以同前缀新建项目被数据库拒绝(409 `project_key_taken`)。
- [ ] **前缀注册表登记(R2,关联 README §9 T19)**:创建项目在同事务内向 `identifier_prefix_registry`(workspace.md owns)登记 `kind='project'`;与任一在册前缀(含已软删除/归档项目、`retired` 历史前缀、当前收件箱前缀)冲突即 409 `project_key_taken`(README §6.3)。
- [ ] **identifier 语义不可变**:删除项目仅把其 issue 的 `project_id` 置 NULL(`issues.project_id` 复合 FK `ON DELETE SET NULL (project_id)` 列级,仅置空归属列,`workspace_id` 保持非空,README §6.2 第 6 条,经 §9 T18 实测),issue 的 `identifier`(`<key>-<number>`)**保持不变**;项目不得跨工作区迁移;issue 跨项目迁移只改 `project_id`(identifier 不变,README §6.3);历史 `WEB-123` 永远指向同一 issue,无歧义。
- [ ] 归档后项目只读:对已归档项目的写操作返回 422 `project_archived`;取消归档后恢复可写。
- [ ] `private` 项目仅 `project_members` 命中者或工作区 admin 可见;其他成员访问返回 403/404。
- [ ] 提交健康度更新会:(a) 写入 `project_updates` 留痕,(b) 回写 `projects.health`,(c) 广播 `project_update.added` 与 `project.updated`。
- [ ] 项目进度 = 子 issue `state_category='done'` 占比,`cancelled` 不计入未完成;`GET /projects/{id}` 返回 `progress`/`open_issues`/`done_issues`。
- [ ] issue 状态变更经 `project.updated` 广播进度,项目页进度条实时刷新。
- [ ] 里程碑逾期判定:`state='open' AND target_date < CURRENT_DATE` 时响应含 `overdue=true`,UI 标红。
- [ ] 里程碑/周期 CRUD 正常;周期校验 `ends_at >= starts_at`,违反返回 400。
- [ ] 周期结束时,未完成 issue 按配置顺延下一周期或退回待办,并通知相关成员。
- [ ] 项目负责人 / 项目成员 / 留痕作者均以**复合 FK** `(workspace_id, …) → members(workspace_id, id)` 引用 `members.id`,可选人类或 agent;`lead_member_id` 可空(`ON DELETE SET NULL (lead_member_id)` 列级,README §6.2 第 6 条),`project_updates.author_member_id` 为 NOT NULL + ON DELETE RESTRICT(成员经 `status='removed'` 软删除,署名永久保留,无悬空)。
- [ ] **同租户复合 FK(README §6.2 / §9 T1)**:`projects`/`milestones`/`cycles` 均建 `UNIQUE(workspace_id, id)`;构造跨工作区的复合 FK 插入(如把 A 区成员设为 B 区项目负责人、把 issue 挂到别区里程碑)被数据库约束拒绝。
- [ ] 所有列表端点支持游标分页,响应形如 `{"data":[...],"next_cursor":...}`,末页 `next_cursor=null`。

### 5.2 非功能性

- [ ] 所有时间字段为 `TIMESTAMPTZ`,API 输出 RFC3339 UTC(如 `2026-07-24T10:00:00Z`)。
- [ ] 所有错误响应使用统一信封 `{"error":{"code","message","details"}}`。
- [ ] 主键为 UUID,默认 `gen_random_uuid()`;表名 snake_case 复数。
- [ ] 鉴权中间件校验工作区成员资格与项目可见性/角色,跨工作区访问被拒绝。
- [ ] WebSocket 事件携带**频道内**单调 `seq`(无"全局 seq"),客户端断线带 `resume_from` 重放、游标过旧收 `resync_required`,不漏不重(README §6.7)。
- [ ] 写端点限流生效,超限返回 429 `rate_limited`。
- [ ] `projects.issue_seq` 在并发创建 issue 下保证项目内编号单调不重号(与 issue.md 编号生成联调通过)。
- [ ] 进度聚合在 1 万级 issue 项目下查询延迟可接受(命中 `idx_issues_project_status`),或启用 `progress_cache` 物化字段。
- [ ] 软删除保留历史:删除项目不级联硬删 issue(下游 `issues.project_id` 复合 FK `ON DELETE SET NULL (project_id)` 列级,仅置空归属列,README §6.2 第 6 条,T18 实测),且 issue 的 `identifier` 保持不变。
- [ ] 错误消息不泄露敏感数据(堆栈/内部 ID 不外泄)。
