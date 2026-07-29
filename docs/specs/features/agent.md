# Agent（Agent 管理）功能 Spec

> 所属层：AI 队友与智能体编排（AI Agent Core）
> 依赖 Spec：`member`（统一成员名册）、`auth`（鉴权 / API token）、`runtime`（运行时）、`skills`（技能 / 工具）、`issue`（看板 / 分派）、`comment-inbox`（评论 / 收件箱）
> 被依赖：`issue`（分派即触发）、`comment-inbox`（@提及触发）、`squad`（智能体小队）、`autopilot`（定时 / 事件自动化）
> 技术栈基准：Python 异步 Web 框架（FastAPI）+ SQLAlchemy 2.x（`DeclarativeBase` / `Mapped` / `mapped_column`）+ PostgreSQL + WebSocket
> 文档性质：可直接指导开发的实现规格。所有命名、约束、端点、事件均以此为准则；与全局约定冲突时以 [README.md](../README.md) §6「全局权威契约」为准。

---

## 全局一致性锚点（一律引用 README §6，本 Spec 不重复定义）

1. **存储**：PostgreSQL 16+；表名 snake_case 复数；主键 `UUID`（`gen_random_uuid()`）；所有表含 `created_at` / `updated_at`（`TIMESTAMPTZ`，默认 `now()`，UTC）；软删除统一 `deleted_at TIMESTAMPTZ NULL`。
2. **成员**：**成员模型以 README §6.1 为唯一权威**——统一 `members` 名册（`member_type = 'human' | 'agent'`，多态外键 `members.user_id` / `members.agent_id`）；`issue.assignee_id`、`comment.author_id`、提及目标一律引用 `members.id`。**本 Spec owns `agents` 表；`agents` 不设 `member_id` 列，关联方向为 `members.agent_id → agents.id`**（`users.member_id UNIQUE` 这类 1:1 反向关联被明确禁止，因其不支持同一 user 加入多个 workspace）。
3. **多租户**：跨模块外键一律按 README §6.2 建复合 FK + 目标表 `UNIQUE(workspace_id, id)`。
4. **接口**：基础路径 `/api/v1`；包络 / 分页 / 错误信封 / 过滤限制见 README §6.14；供 runtime / CLI 使用的 API token 只存哈希、显式 scope（auth.md）。
5. **实时**：统一实时契约见 README §6.7（频道内 `seq`、`realtime_events` 持久重放、`resume_from` / `resync_required`）；流式输出见 README §6.8；事件名 `<entity>.<action>`。
6. **队列 / 投递**：transactional outbox（README §6.6）、at-least-once + 幂等键（§6.5）、execution/attempt 分层（§6.4）、入队快照（§6.11）。
7. **审批**：高风险工具确认统一走 `approvals` 实体（README §6.10）。
8. **ORM**：SQLAlchemy 2.x 约定（类型注解映射、`select()` 查询、异步会话）。

---

## 1. 功能描述

### 1.1 定位

Agent 是 Mesh 的差异化核心：**AI agent 与人类成员同为 workspace 的一等成员**。本模块负责 agent 的身份、配置、能力绑定、可见性、生命周期，以及「被分派 / 被 @ 即自动开工」这条主链路的入口编排。agent 不是某个用户的附属脚本，而是有名册身份、有岗位说明书（system instructions）、有技能与工具、有可见性与权限、有完整审计历史的「数字队友」。

本 Spec 覆盖：
- agent 与人类同构的统一身份建模（与 `member` 模块共建 `members` 名册）；
- agent profile、模型与推理参数、技能 / 工具绑定、配置版本与回滚；
- 可见性与共享、所有权；
- 生命周期状态机（创建 / 暂停 / 停用 / 归档 / 软删除 / 恢复）；
- **「分派即触发」**：`issue.assigned(agent)` 与 `@提及` 共用同一执行入队机制（事件驱动），交给 runtime 执行。

### 1.2 功能点与场景表

| # | 功能点 | 典型用户场景 |
|---|--------|--------------|
| F1 | agent 与人类统一为成员 `[Mesh 特色]` | 成员列表、@提及弹层、分派人选择器中，agent 与人类出现在同一候选集；系统用统一「成员」承载，`member_type` 区分 |
| F2 | 按类型筛选 | 列表 / 候选可切「仅人类 / 仅 Agent / 全部」；权限与计费按类型分别处理 |
| F3 | agent 独立身份 | agent 有头像、名称、简介；发评论 / 改状态以自身身份署名，不借用创建者身份 |
| F4 | agent 可被分派 issue `[Mesh 特色]` | 看板卡片或 issue 详情把负责人设为某 agent，等于把任务交给它 |
| F5 | agent 可被 @提及 `[Mesh 特色]` | 评论里 `@某agent` 入队一次运行，结果以该 agent 的评论回流 |
| F6 | 行为以自身身份留痕 | 时间线显示「agent X 把状态改为进行中」，与人类操作同构 |
| F7 | 创建者 / 所有者关系 | 记录由哪个人类成员创建 / 拥有，用于权限归属与「我创建的 agent」筛选 |
| F8 | agent 不计入人类席位 | 席位 / 邀请统计区分人类与 agent；agent 单独计费或按用量计费 |
| P1–P7 | Profile | 名称 / 头像 / 简介 / 角色标签 / **AI 身份徽章** / 能力摘要 / 状态指示 |
| C1–C10 | 配置 | 模型档位、system instructions、温度 / top_p / max_tokens、推理强度、参数预设、配置版本与回滚、保存前校验 |
| S1–S7 | 技能 / 工具绑定 | 绑定技能与工具、逐项启停、**权限分级**（只读 / 可写 / 需人工确认）、默认 runtime 绑定、绑定留痕、能力可见 |
| V1–V5 | 可见性与共享 | workspace / private 两档、编辑权限、调用权限、所有权转移 |
| L1–L9 | 生命周期 | 向导式创建、模板 / 复制创建、编辑、软停用、暂停、归档、恢复、软删除、停用前置检查 |
| A1–A7 | 分派即触发 `[Mesh 特色]` | 事件驱动入队、衔接 runtime、@提及触发、去重防抖、暂停拦截、运行状态回流、自动状态流转 |
| D1–D6 | 全场景 AI 标识 `[Mesh 特色]` | 看板卡片 / 评论流 / 成员列表 / @候选 / 分派选择器 / 活动流处处带 AI 徽章 |

### 1.3 边界与非目标

**本模块负责：**
- agent 的名册身份（写 `members` + `agents`）、配置、绑定、可见性、生命周期；
- 把「分派 / @提及」翻译成一次执行入队请求（事件驱动），交给 runtime 调度。

**本模块不负责（非目标）：**
- 人类成员的认证、邮箱、邀请流程（属 `member` / `auth` 模块，本模块只复用 `members` 名册）；
- 任务的实际领取、沙箱、日志、凭证注入（属 `runtime` 模块，本模块只投递 `task_executions`）；
- 技能 / 工具的定义与实现（属 `skills` 模块，本模块只做绑定与授权）；
- issue 的字段、看板泳道、状态机定义（属 `issue` / `kanban`，本模块只在被分派时订阅其事件）；
- 模型注册表与底层模型供应商接入细节（统一以「主流大语言模型」抽象，具体接入属平台基础设施，不在本 Spec 范围）。

**约束红线：**
- AI 身份徽章不可关闭、不可隐藏；任何场景都明确标注非人类身份，杜绝冒充。
- 高风险工具默认 `confirm_required`；agent 自主性以「人类随时可介入」为硬约束。

---

## 2. 数据模型

### 2.0 关键设计决策：统一名册 + 多态外键（权威模型见 README §6.1）

采用 **「统一 `members` 名册 + 多态外键 `user_id`/`agent_id`」模型**（member.md owns `members` 表，README §6.1 为唯一权威）：`members` 是「workspace 内的一个身份」，`member_type` 判别其指向 `users`（人类登录身份，auth.md owns）还是 `agents`（AI 身份，本模块 owns）。关联方向永远是 **`members.user_id → users.id`** / **`members.agent_id → agents.id`**；**`users` 与 `agents` 均不设 `member_id` 反向列**——原「类表继承（`users.member_id UNIQUE`）」方案会使同一人类用户无法加入多个 workspace，已废弃。所有协作实体（issue 负责人、评论作者、提及对象）外键统一指向 `members.id`，从根本上支撑「agent 与人类同为一等成员」。

> 与 `member` 模块共建：`members` 表由 `member` 模块 owns 其迁移；`users` 由 auth.md owns；本模块 owns `agents` 及其绑定 / 版本表。共享同一 `members.member_type` 判别器（`'human' | 'agent'`）与 `members.id` 统一引用键，**禁止任何引用点私自带 `(id, type)` 多态二元组**（README §6.1「类型冗余」规则：存储层不得有 `*_type`/`*_kind` 判别列，API 响应中的 `member_type` 为服务端计算快照）。

### 2.1 `members` — 统一成员名册（member.md owns，此处仅列本模块消费字段）

> 表结构、约束、索引的**唯一权威定义在 member.md §2.2 与 README §6.1**。本模块仅消费以下字段，不重复建表、不改写约束：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 成员唯一身份（统一引用键） |
| workspace_id | uuid | 所属 workspace |
| member_type | varchar(16) | `'human'` / `'agent'` 判别器 |
| agent_id | uuid NULL | `member_type='agent'` 时 FK→`agents(id)`，与 `user_id` 恰好一个非空（CHECK 见 member.md） |
| role | varchar(16) | 工作区级角色；`CHECK (member_type='human' OR role <> 'owner')` |
| status | varchar(16) | 名册状态 `active/disabled/removed` |
| display_override | text NULL | 工作区内显示名覆盖 |

显示名解析顺序（README §6.1）：`members.display_override`（非空）→ agent：`agents.name`；人类：`users.display_name` → `users.email`（`users` 无 `full_name` 列，MES-76 H3 对齐 README §6.1 修订）。

### 2.2 `users` — 人类账号（auth.md owns，此处仅说明关联方式）

> 权威定义在 auth.md §2.2。`users` 是**全局登录身份**（跨工作区），**不含 `member_id` 列**；与名册的关联经 `members.user_id → users.id`（一个 user 可在多个工作区各有一条 `members` 行）。本模块创建 agent 成员时不写 `users`。

### 2.3 `agents` — agent 专有配置表（本模块 owns）

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| id | uuid | PK, default `gen_random_uuid()` | agent id |
| workspace_id | uuid | NOT NULL, FK `workspaces(id)` ON DELETE CASCADE | 归属工作区（agent 为工作区级实体） |
| name | varchar(120) | NOT NULL | 显示名（名册显示名解析的 agent 分支，README §6.1） |
| avatar_url | text | NULL | 头像 URL |
| role_tag | varchar(64) | NULL | 角色标签（如「测试工程师」），用于列表辨识与筛选 |
| owner_user_id | uuid | NOT NULL, FK `users(id)` | 创建者 / 所有者（人类） |
| slug | varchar(64) | NULL | 可选短标识，用于提及简写 |
| bio | text | NULL | 个人简介（markdown） |
| badge_kind | varchar(32) | NOT NULL DEFAULT `'ai'` | AI 身份徽章类型（渲染区分用，不可置空隐藏） |
| lifecycle_status | varchar(16) | NOT NULL DEFAULT `'active'`, CHECK IN (`'active'`,`'paused'`,`'disabled'`,`'archived'`) | 生命周期状态（见 4.8） |
| visibility | varchar(16) | NOT NULL DEFAULT `'workspace'`, CHECK IN (`'workspace'`,`'private'`) | 可见性级别 |
| system_instructions | text | NULL | 系统指令（岗位说明书） |
| model_config | jsonb | NOT NULL DEFAULT `'{}'::jsonb` | 模型与推理参数（结构见 2.4） |
| default_runtime_id | uuid | NULL | 默认运行时；**复合 FK `(workspace_id, default_runtime_id) → runtimes(workspace_id, id) ON DELETE SET NULL (default_runtime_id)`**（PG16 列级，仅置空引用列、`workspace_id` 保持不动，README §6.2 第 6 条） |
| trigger_on_assign | boolean | NOT NULL DEFAULT true | 被分派 issue 时是否自动触发运行 `[Mesh 特色]` |
| active_config_version_id | uuid | NULL，**同 agent 重叠复合 FK `(workspace_id, id, active_config_version_id) → agent_config_versions(workspace_id, agent_id, id) ON DELETE SET NULL (active_config_version_id)`** | 当前生效配置版本指针（见 2.7）；**active 指针必须属于本 agent 自己的配置版本——由重叠复合 FK 在数据库层强制**（被引用表建 `UNIQUE(workspace_id, agent_id, id)`，README §6.2 第 7 条）；置空仅 `active_config_version_id` 列、`workspace_id` 保持不动（PG16 列级 SET NULL，README §6.2 第 6 条） |
| created_at | timestamptz | NOT NULL DEFAULT `now()` | |
| updated_at | timestamptz | NOT NULL DEFAULT `now()` | |
| deleted_at | timestamptz | NULL | 软删除 |

> **入册关联**：agent 进入某工作区名册 = 插入一行 `members(member_type='agent', agent_id=本行 id, workspace_id=同工作区)`（member.md owns 该流程）。**agent 仅可加入其 `workspace_id` 所属工作区的名册**（`members.agent_id` 的复合 FK `(workspace_id, agent_id) → agents(workspace_id, id)` 强制，README §6.2）；跨工作区共享 agent 不在本期范围（YAGNI）。
>
> `members.status`（名册级 `active/disabled/removed`）与 `agents.lifecycle_status`（agent 级 `active/paused/disabled/archived`）是两个正交维度：前者管「是否还在名册里」，后者管「agent 运营状态」。停用 agent 时两者联动（见 4.8）。

约束 / 索引：
- `UNIQUE (workspace_id, id)` —— 供引用方复合 FK（README §6.2）。
- `idx_agents_owner`：`(owner_user_id)` —— 「我创建的 agent」。
- `idx_agents_lifecycle`：`(workspace_id, lifecycle_status)` WHERE `deleted_at IS NULL`。
- `idx_agents_visibility`：`(workspace_id, visibility)` —— 可见性过滤。
- `idx_agents_default_runtime`：`(default_runtime_id)` —— runtime 删除前检查引用。

### 2.4 `model_config` JSONB 结构（模型与推理参数）

用 JSONB 承载频繁演进、字段不固定的推理参数，避免为每个新参数加列；应用层用 JSON Schema / Pydantic 校验。推荐结构：

```json
{
  "model": "mainstream-llm-balanced",
  "model_tier": "balanced",
  "temperature": 0.2,
  "top_p": 1.0,
  "max_tokens": 8192,
  "reasoning_effort": "medium",
  "stop_sequences": [],
  "preset": "strict_engineering",
  "advanced": { "frequency_penalty": 0.0, "presence_penalty": 0.0 }
}
```

| 键 | 类型 | 取值 / 范围 | 说明 |
|----|------|-------------|------|
| model | string | 「主流大语言模型」标识 | 由平台模型注册表枚举，不暴露具体供应商 |
| model_tier | string | `strong_reasoning` / `balanced` / `lightweight_fast` | 模型档位（控本与选型的抽象） |
| temperature | number | [0, 2] | 随机性 |
| top_p | number | [0, 1] | 核采样 |
| max_tokens | integer | [1, 模型上限] | 单次输出上限 |
| reasoning_effort | string | `low` / `medium` / `high` | 内部推理强度 `[Mesh 特色]` |
| stop_sequences | string[] | — | 停止序列 |
| preset | string | 预设名 | 一键套用的参数模板 |
| advanced | object | — | 低频高级参数收纳 |

设计要点：
- 校验在应用层完成，保存前拒绝越界值，错误码 `422 validation_error`。
- 按需对特定键建表达式 GIN 索引（如统计某档位 agent 数）：`CREATE INDEX idx_agents_model_tier ON agents USING gin ((model_config->'model_tier'))`。
- 重大变更同步写入 `agent_config_versions` 快照（2.7），实现可回滚（immutable，不原地改写历史快照）。

### 2.5 agent ↔ 技能 / 工具绑定（skill.md owns，本 Spec 仅引用）

> **agent 与技能、工具的绑定与授权唯一权威定义在 skill.md**（四层解耦：定义—版本—安装—绑定；绑定携带具体 `skill_version_id`，支持灰度/回滚）。本模块**不重复建表**（R1/MES-2 必修-3：`agent_skill_bindings`、`agent_tool_bindings`、`tools` 表已全部删除）：
> - 技能绑定 = skill.md 的 `agent_skills`（经 `skill_installations` 引用版本）；
> - **工具权限并入 skill 的能力语义**：工具由技能声明（`required_capabilities`），安装时按最小权限授予（`skill_installations.granted_capabilities`），**权限分级（`read_only`/`write`/`confirm_required`）作为能力条目上的 `permission` 字段表达**（见 skill.md），**不存在独立的工具目录主键（`tools`/`agent_tool_bindings` 表已删除，无工具主键可冻结）**；高风险能力默认 `confirm_required`，执行时经统一 `approvals` 闸门（README §6.10）；
> - `GET/POST/DELETE /agents/{id}/skills`、`/agents/{id}/tools` 等端点操作的均为 skill.md 的安装/绑定/授权实体（薄封装，不新增数据模型）；其中 `/agents/{id}/tools` 系列端点操作的为 `skill_installations.granted_capabilities` 的**能力条目**（`capability` key + `permission` 的薄封装，**无工具主键**）；
> - 入队时绑定版本与授权清单冻结进 `task_executions.config_snapshot`（`skill_versions` + `capability_grants`，README §6.11）。

### 2.7 `agent_config_versions` — 配置版本快照（审计 / 回滚）

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| id | uuid | PK | 版本 id |
| workspace_id | uuid | NOT NULL, FK `workspaces(id)` ON DELETE CASCADE | 冗余隔离列（与所属 `agents` 同 workspace），供同租户复合 FK 与重叠唯一键引用（README §6.2） |
| agent_id | uuid | NOT NULL，**复合 FK `(workspace_id, agent_id) → agents(workspace_id, id)` ON DELETE CASCADE** | 所属 agent（跨租户指向别区 agent 的版本在 INSERT 即被拒绝，README §6.2 第 2 条） |
| snapshot | jsonb | NOT NULL | 该版本完整配置快照（system_instructions + model_config + 绑定清单） |
| change_summary | text | NULL | 变更摘要（可服务端自动生成） |
| changed_by | uuid | NOT NULL，**复合 FK `(workspace_id, changed_by) → members(workspace_id, id)`** | 操作者成员 id（**审计成员必须与本版本同属一个工作区**——跨租户成员写入在 INSERT 即被拒绝，README §6.2 第 2/3 条；成员软删除不物理删，该引用不置空） |
| created_at | timestamptz | NOT NULL DEFAULT `now()` | 版本生成时间（仅插入，不更新） |

约束 / 索引：
- `UNIQUE (workspace_id, agent_id, id)` —— **重叠唯一键**：供 `agents.active_config_version_id` 的同 agent 重叠复合 FK 引用，在数据库层强制「active 指针只能指向本 agent 的版本」（README §6.2 第 7 条）。
- `UNIQUE (workspace_id, id)` —— 供跨表复合 FK 引用的通用前提（README §6.2 第 1 条）。
- `idx_config_versions_agent_time`：`(agent_id, created_at DESC)`。

> `agents.active_config_version_id` 指向当前生效版本，以重叠复合 FK `(workspace_id, id, active_config_version_id) → agent_config_versions(workspace_id, agent_id, id)` 引用（见 2.3）——把 A agent 的 active 指针指向 B agent 的版本、或指向别的工作区的版本，均在写入时被拒绝（集成测试 T27）；回滚 = 复制旧快照写新版本并把指针指过去（不可变快照，符合 immutable 原则，不抹除历史）。

### 2.8 实体关系（ER 图）

```mermaid
erDiagram
    workspaces ||--o{ members : "has"
    users ||--o{ members : "member_type=human (members.user_id)"
    agents ||--o{ members : "member_type=agent (members.agent_id)"
    users ||--o{ agents : "owns"
    agents ||--o{ agent_config_versions : "has"
    agents ||--o{ agent_skills : "bound (skill.md owns, 含工具能力授权)"
    runtimes ||--o{ agents : "default_runtime"
    members ||--o{ issues : "assignee_id (统一负责人)"
    members ||--o{ comments : "author_id (统一作者)"
    agents ||--o{ task_executions : "执行者 (runtime 模块)"
    task_executions ||--o{ approvals : "高风险工具审批 (README 6.10)"

    members {
        uuid id PK
        uuid workspace_id FK
        varchar member_type "human|agent"
        uuid user_id FK "NULL, 多态"
        uuid agent_id FK "NULL, 多态"
        varchar role
        varchar status
        text display_override
    }
    agents {
        uuid id PK
        uuid workspace_id FK
        varchar name
        uuid owner_user_id FK
        varchar lifecycle_status
        varchar visibility
        text system_instructions
        jsonb model_config
        uuid default_runtime_id FK
        boolean trigger_on_assign
        uuid active_config_version_id FK
    }
    agent_skills {
        uuid id PK
        uuid agent_id FK "skill.md owns"
        uuid skill_installation_id FK
        uuid skill_version_id FK "入队时冻结进快照"
        boolean enabled
    }
    agent_config_versions {
        uuid id PK
        uuid workspace_id FK "同租户隔离列"
        uuid agent_id FK "复合 FK → agents(ws,id)"
        jsonb snapshot
        uuid changed_by FK "复合 FK → members(ws,id)"
    }
```

跨模块外键与唯一约束要点（同租户约束一律按 README §6.2）：
- `members.agent_id` 复合 FK `(workspace_id, agent_id) → agents(workspace_id, id)` → 一个工作区名册条目至多对应一个 agent；`agents.UNIQUE(workspace_id, id)` 供引用。
- `agent_config_versions` 携带 `workspace_id` 并以复合 FK 引用 `agents(workspace_id, id)` 与 `members(workspace_id, id)`（`changed_by` 审计不跨租户）；`UNIQUE(workspace_id, agent_id, id)` + `agents` 上的重叠复合 FK 保证 active 配置指针不跨 agent/跨租户串指（README §6.2 第 7 条，集成测试 T27）。
- agent ↔ 技能 / 工具绑定与授权见 skill.md `agent_skills` / `skill_installations`（唯一权威，本模块不重复建表；`agent_tool_bindings`/`tools` 表已删除，工具权限并入 `granted_capabilities` 语义）。
- `issues.assignee_id` / `comments.author_id` / 提及目标 → `members.id`（复合 FK `(workspace_id, …) → members(workspace_id, id)`）；**不冗余 `assignee_type`/`author_type` 存储列**——AI 徽章渲染所需的 `member_type` 由 API 响应携带服务端计算快照（README §6.1）。
- `task_executions.agent_id` → `agents.id`（runtime 模块 owns，本模块只读引用以展示运行历史）。
- 运行中高风险工具确认创建 `approvals` 行（README §6.10），执行进入 `awaiting_approval`（README §6.4）。

---

## 3. 接口设计

### 3.0 通用约定

- **鉴权**：所有端点要求 `Authorization: Bearer <token>`（用户会话 JWT）。鉴权失败 `401`，权限不足 `403`。
- **基础路径**：`/api/v1`。
- **响应包络**（成功，单对象）：`{"data": { ... }}`。
- **响应包络**（成功，列表 + 游标分页）：`{"data": [ ... ], "next_cursor": "eyJ..." | null}`。
- **错误信封**（失败）：

```json
{
  "error": {
    "code": "validation_error",
    "message": "temperature 必须在 [0,2] 区间",
    "details": [ { "field": "model_config.temperature", "issue": "out_of_range" } ]
  }
}
```

- **分页**：`?cursor=<opaque>&limit=N`（默认 20，上限 100），`next_cursor` 为 `null` 表示末页。
- **时间**：全部 UTC RFC3339。
- **资源命名**：复数名词 `/agents`、`/members`。
- **实时**：长任务 / 状态变化经 `/ws` 推送（见 5.3），REST 仅负责读写。

### 3.1 REST 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/agents` | 创建 agent（profile + 初始配置 + 绑定） |
| GET | `/api/v1/agents` | 列表，`?status=&visibility=&owner_id=&cursor=&limit=&q=` |
| GET | `/api/v1/agents/{id}` | 详情（profile + 配置 + 绑定 + 当前版本） |
| PATCH | `/api/v1/agents/{id}` | 更新 profile（名称 / 头像 / 简介 / 角色标签 / 可见性） |
| PATCH | `/api/v1/agents/{id}/config` | 更新模型 / 推理参数 / system instructions（生成新配置版本） |
| POST | `/api/v1/agents/{id}:pause` | 暂停（body 带 `in_flight_policy`） |
| POST | `/api/v1/agents/{id}:resume` | 恢复到 active |
| POST | `/api/v1/agents/{id}:disable` | 停用 |
| POST | `/api/v1/agents/{id}:enable` | 启用 |
| POST | `/api/v1/agents/{id}:archive` | 归档 |
| POST | `/api/v1/agents/{id}:restore` | 从归档 / 停用恢复 |
| DELETE | `/api/v1/agents/{id}` | 软删除（置 `deleted_at`） |
| POST | `/api/v1/agents/{id}:transfer` | 转移所有权 |
| GET | `/api/v1/agents/{id}/skills` | 列出绑定技能 |
| POST | `/api/v1/agents/{id}/skills` | 绑定技能（批量） |
| DELETE | `/api/v1/agents/{id}/skills/{skill_id}` | 解绑技能 |
| PATCH | `/api/v1/agents/{id}/skills/{skill_id}` | 启用 / 停用单项 |
| GET | `/api/v1/agents/{id}/tools` | 列出绑定的能力条目（`skill_installations.granted_capabilities`，capability key + permission，**无工具主键**） |
| POST | `/api/v1/agents/{id}/tools` | 绑定能力条目（`{"capability": "<key>", "permission": "..."}`，带 permission） |
| DELETE | `/api/v1/agents/{id}/tools/{capability_key}` | 解绑该能力条目 |
| PATCH | `/api/v1/agents/{id}/tools/{capability_key}` | 改该 capability 条目的 permission / 启停 |
| GET | `/api/v1/agents/{id}/config-versions` | 配置版本历史 |
| POST | `/api/v1/agents/{id}/config-versions/{version_id}:rollback` | 回滚到指定版本 |
| GET | `/api/v1/agents/{id}/executions` | 该 agent 的运行历史（只读，源自 runtime 模块） |
| GET | `/api/v1/members` | 统一成员列表，`?member_type=human\|agent&status=&cursor=&limit=` |

> 生命周期 / 回滚等动作型端点统一用 `:verb` 后缀（如 `:pause`），与读写型端点（PATCH/DELETE）区分。

### 3.2 可运行 JSON 示例

**创建 agent — 请求 `POST /api/v1/agents`**

```json
{
  "display_name": "小测",
  "avatar_url": "https://cdn.mesh.internal/avatars/xiaoce.png",
  "role_tag": "测试工程师",
  "bio": "负责回归测试与缺陷复现，输出可执行的测试报告。",
  "visibility": "workspace",
  "system_instructions": "你是测试工程师。收到 issue 后先复现问题，再给出最小复现步骤与修复建议。不做超出测试范围的改动。",
  "model_config": {
    "model_tier": "balanced",
    "temperature": 0.2,
    "top_p": 1.0,
    "max_tokens": 8192,
    "reasoning_effort": "medium",
    "preset": "strict_engineering"
  },
  "default_runtime_id": "8b2c1f0e-4a7d-4c9b-9e1a-2f3b4c5d6e7f",
  "trigger_on_assign": true,
  "skill_ids": ["s-uuid-1", "s-uuid-2"],
  "capabilities": [
    { "capability": "exec:shell", "permission": "confirm_required" },
    { "capability": "read:code", "permission": "read_only" }
  ]
}
```

**创建 agent — 响应 `201 Created`**

```json
{
  "data": {
    "id": "a1b2c3d4-0000-4000-8000-000000000001",
    "member": {
      "id": "m1b2c3d4-0000-4000-8000-000000000001",
      "member_type": "agent",
      "display_name": "小测",
      "avatar_url": "https://cdn.mesh.internal/avatars/xiaoce.png",
      "role_tag": "测试工程师",
      "role": "member",
      "status": "active"
    },
    "owner_user_id": "u-uuid-current",
    "visibility": "workspace",
    "lifecycle_status": "active",
    "badge_kind": "ai",
    "model_config": {
      "model_tier": "balanced",
      "temperature": 0.2,
      "top_p": 1.0,
      "max_tokens": 8192,
      "reasoning_effort": "medium",
      "preset": "strict_engineering"
    },
    "trigger_on_assign": true,
    "active_config_version_id": "v-uuid-1",
    "created_at": "2026-07-24T12:00:00Z",
    "updated_at": "2026-07-24T12:00:00Z"
  }
}
```

> 创建在同一事务内写入 `members`（`member_type='agent'`）+ `agents` + 绑定 + 首个 `agent_config_versions`，并发出 `agent.created` 事件。

**列表 — `GET /api/v1/agents?status=active&limit=2`**

```json
{
  "data": [
    { "id": "a1b2...0001", "display_name": "小测", "role_tag": "测试工程师", "lifecycle_status": "active", "badge_kind": "ai", "busy": true },
    { "id": "a1b2...0002", "display_name": "文档助手", "role_tag": "文档撰写", "lifecycle_status": "active", "badge_kind": "ai", "busy": false }
  ],
  "next_cursor": "eyJpZCI6ImExYjIuLi4wMDAyIn0="
}
```

**更新配置 — `PATCH /api/v1/agents/{id}/config`（生成新版本）**

```json
{
  "model_config": { "temperature": 0.7, "reasoning_effort": "high" },
  "system_instructions": "（更新后的岗位说明书）"
}
```

响应 `200 OK` 返回新的 `active_config_version_id` 与生效配置；`change_summary` 由服务端自动生成。配置变更对进行中运行不生效，对后续运行生效。

**绑定能力条目 — `POST /api/v1/agents/{id}/tools`**（`/tools` 为 `granted_capabilities` 能力条目的薄封装，无工具主键）

```json
{ "capabilities": [ { "capability": "net:fetch", "permission": "read_only" } ] }
```

**生命周期 — `POST /api/v1/agents/{id}:pause`**

```json
{ "reason": "临时维护", "in_flight_policy": "finish_current" }
```

`in_flight_policy` 取值 `finish_current`（让进行中任务跑完）/ **`cancel_current`（取消在途执行）**。响应返回最新 `lifecycle_status` 与被影响的运行数。非法状态迁移返回 `409 conflict`。

> **R1 修订（A7）**：原 `pause_now`（"冻结运行、稍后恢复"）**已废弃**。runtime 的执行状态机（README §6.4）**不含 pause/resume 执行态**，"冻结并可恢复单次执行"无法实现，故不保留该承诺：`cancel_current` 即对 agent 所有 `queued/claimed/running` 执行发起取消（`failure_reason='agent_paused'`，走 runtime.md 两段式取消）；`finish_current` 等在途执行自然终态。paused 期间不入队新执行；resume 回 active 后由新触发重新入队。

**软删除 — `DELETE /api/v1/agents/{id}`** → `204 No Content`；后续列表中不再出现，历史评论以「已停用 agent」占位渲染。

### 3.3 分派即触发的内部契约（outbox 驱动，非公开 REST）

「分派即触发」不通过新增公开 REST，而是经 **transactional outbox**（README §6.6）消费领域事件并入队执行，与 `@提及`、autopilot 共用同一执行入口。**触发语义的唯一权威为 README §6.9 触发矩阵**（再选同一 assignee = no-op；字段无变化的保存 = no-op；编辑评论新增/移除 @ 的触发规则；运行中在新评论再次 @ = 新排队、由频率护栏兜底——不允许"合并或排队"之类不可开发不可测试的表述）。

- 事件来源：`issue.assigned`（assignee 为 agent 且 `trigger_on_assign=true`）、`comment.created` / `comment.updated`（评论模块按 §6.9 对提及集合 diff 后产生 `mention.added` 派生事件）。**业务写库与 outbox 事件在同一事务提交**，杜绝"业务已提交但任务未入队"的永久丢失。
- 处理器 `enqueue_agent_run(agent_id, issue_id, trigger, trigger_event_id)`（由 outbox relay 调用）：
  1. 校验 agent `lifecycle_status='active'` 且 `members.status='active'`，否则发 `agent.trigger_skipped`（原因 `paused/disabled`）并提示，不入队；
  2. 按 README §6.9 去重：同一触发事件不重复入队（幂等键兜底）；替换分派时前任 agent 的在途执行被取消（`failure_reason='superseded'`）；
  3. 组装 issue 上下文（标题 / 描述 / 评论 / 附件 / 标签），**所有外部来源内容注入 agent 上下文时显式标记为不可信数据并做结构隔离**（README §6.15「不可信内容处理」），生成幂等键 `idempotency_key = sha256(agent_id|issue_id|trigger_event_id)`（README §6.5）；
  4. **冻结入队快照** `config_snapshot`（README §6.11）：`agent_config_version_id`、绑定 skill 版本清单、`capability_grants`（**严格对象数组** `[{capability, permission}]`，由下述归一算法从绑定技能声明派生，README §6.11，**无工具主键**）、repo/base SHA、`trigger_event_id`——运行可复现可审计；配置后续变更不影响在途执行；
  5. 创建 `task_executions`（`status='queued'`，`agent_id`；写入权威 **`label_requirements`** 与 **`required_capabilities`**——后者为**严格 capability key 字符串数组**（如 `["ffmpeg","exec:shell"]`），由下述**入队归一算法**（README §6.4 权威定义）从绑定技能声明派生，为 claim 时与服务端 runtime 能力匹配的权威字段，README §6.4；物理领取产生 `execution_attempts`，见 runtime.md / README §6.4）；
  6. 发出 `execution.queued` 事件（outbox → `realtime_events`，README §6.7）。

> **能力入队归一算法（R3 写死，README §6.4/§6.11 权威）**：技能声明（skill.md `required_capabilities`/`granted_capabilities`）允许「字符串 key」或「`{capability, permission}` 对象」两种条目形态（授权语义的声明层表达）；入队时**必须**归一为严格类型的两套字段，**任何对象形态都不得进入 `task_executions.required_capabilities`**（否则 claim 的 JSONB `<@` 匹配永不命中，任务永久无法领取）：
>
> ```text
> normalize_capabilities(declared) -> (required_capabilities, capability_grants)
>   required := []            -- 字符串数组:scheduling 用
>   grants   := []            -- 对象数组:授权快照用
>   for item in declared:
>     if item 是字符串 k:
>       required.append(k)
>       grants.append({"capability": k, "permission": "confirm_required"})  -- 未标注默认高风险闸门
>     else if item 是 {"capability": k, "permission": p}:
>       required.append(k)
>       grants.append({"capability": k, "permission": p})
>     else: 拒绝入队并告警(422 capability_invalid,声明层校验应已拦截)
>   required := 去重后按字典序排序的字符串数组   -- 稳定序列化,便于匹配与审计比对
>   grants   := 按 capability 字典序排序;同一 capability 取声明中**最严格** permission
>               (confirm_required > write > read_only)
>   return (required, grants)
> ```
>
> `task_executions.required_capabilities` 与 `config_snapshot.capability_grants` 的严格类型由 schema CHECK 兜底（字符串数组 / `{capability,permission}` 对象数组，validation 脚本实测，集成测试 T28）；runtime claim 只做 `e.required_capabilities <@ runtimes.capabilities`（双方均为纯字符串数组，README §6.4）。
>
> **R4 写死（授权快照 permission 必填 + 归一唯一实现）**：归一**产物** `capability_grants` 的每个条目 **`permission` 必须存在、必须为字符串、取值必须为 `read_only|write|confirm_required`**——schema CHECK 对缺失/非字符串/非法枚举的 permission 一律拒绝（「未标注 permission」只是**声明层**形态，归一时补 `confirm_required`，绝不以缺 permission 形态落进快照）。本算法的**唯一可执行参照实现**为 validation 脚本的 `normalize_capability_declarations(declared)`（输入混合字符串/对象声明，输出 `{"required": [...], "grants": [...]}`；非法 permission / 非法条目形态 / 非数组输入抛 `capability_invalid`，API 层 422）；后端编排入口的实现必须与其逐条等价，集成测试 T28 以**同一实现**处理混合声明并断言「字符串补 `confirm_required` / 去重 / 最严格权限 / 字典序排序 / claim 联动 / 非法声明拒绝」全部语义。

> 幂等键 + 状态机校验保证同一逻辑触发不会重复入队；领取原子性与跨租户安全见 runtime.md §2.5。

### 3.4 错误码体系

| HTTP | code | 含义 | 触发示例 |
|------|------|------|----------|
| 400 | invalid_request | 请求体格式错误 / JSON 解析失败 | body 非合法 JSON |
| 401 | unauthorized | 缺少 / 无效 token | 未带 Authorization |
| 403 | forbidden | 无权限（编辑 / 调用 / 转移） | 非所有者编辑私有 agent |
| 404 | not_found | 资源不存在或已删除 | 错误的 agent id |
| 409 | conflict | 状态冲突 / 唯一约束冲突 | 非法生命周期迁移；重复绑定同一技能 |
| 409 | last_owner | 试图转移走最后一个 owner（与 member 模块语义一致） | owner 保护 |
| 422 | validation_error | 业务校验失败 | temperature 越界；system_instructions 为空（若要求必填） |
| 429 | rate_limited | 触发限流 | 单位时间创建过多 agent |
| 500 | internal_error | 服务端异常 | 未捕获错误（不向外泄漏堆栈） |

> 错误响应不泄漏内部实现细节（无堆栈、无 SQL）；`details` 仅返回字段级校验信息。所有端点接入限流。

### 3.5 分页与鉴权

- 游标分页：agent 列表按 `(lifecycle_status, created_at, id)` 排序编码游标；`next_cursor=null` 表示末页。
- 鉴权：
  - 读取 agent：workspace 成员可见 `visibility='workspace'` 的 agent；`private` 仅所有者与 admin 可见。
  - 写配置 / 绑定 / 生命周期：所有者或 admin（按 workspace 编辑策略）。
  - 触发（分派 / @）：与可见性一致；`private` agent 仅所有者可触发。
  - 转移所有权：仅当前所有者或 admin。

### 3.6 WebSocket 事件（统一实时契约，README §6.7）

客户端连 `/ws` 后订阅频道；每条事件带**频道内**单调 `seq`（持久化于 `realtime_events`），断线重连带 `resume_from=<last_seq+1>` 补发，游标过旧收 `resync_required`（README §6.7）。

| 频道 | 事件 | 说明 |
|------|------|------|
| `workspace:{ws}:agents` | `agent.created` / `agent.updated` / `agent.deleted` | 列表与候选实时刷新 |
| `workspace:{ws}:agents` | `agent.lifecycle_changed` | 暂停 / 停用 / 归档 / 恢复，含前后状态 |
| `agent:{id}:presence` | `agent.presence` | 容量三元组「运行中 N / 排队 M / 需审批 K」（README §6.12，由 `task_executions` 聚合 + `approvals` 计数推导） |
| `issue:{id}:runs` | `execution.queued` / `execution.started` / `execution.progress` / `execution.awaiting_approval` / `execution.completed` / `execution.failed` | 运行状态回流（事件词汇与 runtime.md 一致，均取自 README §6.7 注册表），卡片忙碌指示与进度条 |
| `workspace:{ws}:agents` | `agent.trigger_skipped` | 分派 / @ 因 paused/disabled 未触发 |
| `workspace:{ws}:approvals` / `execution:{id}` | `approval.created` / `approval.decided` | 高风险工具需人工确认——**统一 `approvals` 实体**（README §6.10），带内联批准 / 拒绝与过期时间 |

帧示例：

```json
{ "seq": 48213, "channel": "issue:{id}:runs", "event": "execution.started",
  "data": { "agent_id": "a1b2...0001", "execution_id": "e-uuid", "issue_id": "i-uuid", "started_at": "2026-07-24T12:00:05Z" } }
```

---

## 4. UI / UX

### 4.1 信息架构

> 全局导航 / 面包屑 / 角色可见性矩阵以 **README §6.12** 为权威。agent 在导航中**只有一个名册入口（成员页）**；Settings 不再维护重复的 agent 名册列表。

```
成员 Members（人与 agent 统一名册 —— agent 的唯一名册入口）
    └── agent 行 → agent 详情页
        ├── Tab：概览 Profile
        ├── Tab：配置 Configuration（模型 + 指令 + 参数）
        ├── Tab：技能与工具 Skills & Tools
        ├── Tab：可见性与权限 Visibility & Access
        └── Tab：历史 History（配置版本 / 审计）
设置 Settings（admin+）
└── Agents 策略（仅工作区级：默认 runtime、触发护栏、审批策略；不重复罗列 agent 名册）
自动化 → Agents 运行视图（全员可见的运行与结果，渐进披露）
```

### 4.2 成员名册页的「仅 Agent」筛选投影（同一路由 / 同一列表组件 / 同一创建入口；不存在独立 Agent 列表页）

> **R5 修订（HIGH-1，写死）：不存在独立「Agents」列表页与第二个创建入口。** 所谓「agent 列表」是成员名册页（§4.5）的**「仅 Agent」筛选投影**——同一路由（`/w/{ws}/members?member_type=agent`）、同一列表组件、同一 `[ + 新建 Agent ]` 入口，**不形成第二导航、第二名册、第二创建入口**（README §6.12「Agents 入口去重」与 §4.1 信息架构一致；onboarding.md §1.2.1 的 CTA 亦深链此唯一入口）。本节只定义投影状态下行与筛选的渲染契约；页面框架与创建向导入口一律以 §4.5 / §4.7 为准。

```
┌────────────────────────────────────────────────────────────────┐
│  成员 Members                        [ 邀请成员 ] [ + 新建 Agent ] │
│  筛选: [● 仅 Agent ▾]   搜索: [__________________________]        │
│  （成员名册页同一页面、同一路由 /w/{ws}/members?member_type=agent） │
├────────────────────────────────────────────────────────────────┤
│  ◉ 小测 [AI]    Agent · 测试工程师   ● 处理中 · active   ⋯        │
│  ◉ 文档助手[AI] Agent · 文档撰写     ○ 空闲   · active   ⋯        │
│  ◉ 值班运维[AI] Agent · 运维        ◐ 已暂停 · paused   ⋯        │
│  ▢ 老报表 [AI]  Agent · 报表        ▭ 已归档 · archived ⋯        │
├────────────────────────────────────────────────────────────────┤
│  共 12 个 · 加载下一页 ↓                                          │
└────────────────────────────────────────────────────────────────┘
```

- **投影 = 成员页的筛选状态，不是独立页面**：`?member_type=agent` 是成员页「全部 / 仅人类 / 仅 Agent」筛选（§4.5）的一个取值，由同一列表组件渲染；页面标题恒为「成员 Members」，`[ + 新建 Agent ]` 即成员页同一入口（与「邀请成员」并列），**不存在任何 Agents 视图专属的 `[+ 新建]` 按钮或独立路由**。
- 每个 agent 行：头像（右下角叠加 AI 角标）、名称 + `[AI]` 徽章、类型列显式标「Agent」、实时忙碌指示（●处理中 / ○空闲，来自 `agent.presence`）、生命周期状态、`⋯` 行内操作（暂停 / 停用 / 归档 / 复制 / 转移）。
- 投影内的筛选条件（生命周期状态 active/paused/disabled/archived、可见性、所有者、关键字搜索）是成员列表组件的子筛选，不构成第二入口；列表默认隐藏 archived/disabled，需主动勾选显示。
- **防回归文档校验（R5 写死，T35）**：`tests/docs/check_roster_entry.py`（CI 常跑，不通过即 CI 失败）扫描全部 Spec，把以下情形判为独立 Agents 名册回归并失败：① 线框图中页面标题为 `Agents` 且带 `[+ 新建]`（「新建」后无 Agent 后缀）的独立列表页；② 未与「筛选投影 / 不存在 / 不维护 / 不是」等否定或投影标注同行出现的「Agent 列表页」表述；③ 导航 / 信息架构图中 `Agents` 行携带 `[+ 新建]` 入口。新增相关文案必须显式声明为成员名册页的投影。

### 4.3 Agent 详情页（配置 / 指令 / 模型参数 / 技能绑定）

```
┌──────────────────────────────────────────────────────────────────┐
│  ← 返回   ◉ 小测 [AI]   active ● 处理中        [ 暂停 ] [ ⋯ 更多 ] │
│  测试工程师 · 由 张三 创建 · workspace 可见                         │
├──────────────────────────────────────────────────────────────────┤
│ [概览] [配置] [技能与工具] [可见性与权限] [历史]                     │
├──────────────────────────────────────────────────────────────────┤
│  配置 Tab：                                                        │
│   底层模型档位   ( ○强推理  ●均衡  ○轻量快速 )                       │
│   具体模型       [ 主流大语言模型-均衡版 ▾ ]                         │
│   System Instructions                                            │
│   ┌──────────────────────────────────────────────┐                │
│   │ 你是测试工程师。收到 issue 后先复现问题……       │                │
│   └──────────────────────────────────────────────┘                │
│   ▸ 高级参数                                                       │
│     温度 ──●────── 0.2     top_p ──────● 1.0                       │
│     max_tokens [ 8192 ]    推理强度 (低 ●中 高)                     │
│   预设: [严谨工程 ▾]   [ 应用预设 ]                                 │
│                                       [ 取消 ]  [ 保存(生成新版本) ] │
└──────────────────────────────────────────────────────────────────┘
```

- **概览 Tab**：头像（可换）、名称、角色标签、bio（markdown 预览）、能力摘要（绑定技能 / 工具数量与清单 + 模型档位）、当前状态。
- **配置 Tab**：模型档位单选 + 具体模型下拉；system instructions 多行编辑器（支持 markdown、变量插值提示）；推理参数滑块 / 输入；高级折叠区；预设套用；保存即生成新配置版本。越界值在保存前红字拦截。
- **技能与工具 Tab**：双列清单，逐项开关；工具项带权限下拉（只读 / 可写 / 需确认），高风险默认「需确认」并加警示色。
- **可见性与权限 Tab**：可见性单选、编辑权限策略、所有权转移。
- **历史 Tab**：配置版本时间线，支持「对比上一版」「回滚到此版本」。

### 4.4 创建 / 编辑向导

四步向导（编辑时复用同一组件，预填现有值）：

```
① 基本信息  →  ② 模型与指令  →  ③ 技能与工具  →  ④ 可见性  →  [完成]
─────────────────────────────────────────────────────────
步骤 ①  名称*  [____________]   头像 [上传/自动生成]
        角色标签 [____________]  简介 [________________]
─────────────────────────────────────────────────────────
        [ 上一步 ]                         [ 下一步 ]
```

- 每步可独立校验、可后退不丢数据；步骤指示器显示进度。
- 步骤 ② 提供「预设」快速起步，新手无需逐项调参。
- 步骤 ③ 支持「稍后配置」，允许先建一个最小 agent 再补能力。
- 「完成」后立即出现在成员列表与 `@`/分派候选中。
- 提供「从现有 agent 复制」与「从模板创建」快捷入口。

### 4.5 成员列表中的 AI 标识

```
成员 Members                       [ 全部 ▾ ]  [ 邀请成员 ] [ + 新建 Agent ]
─────────────────────────────────────────────────────────
👤 张三        人类 · 产品负责人          在线
👤 李四        人类 · 后端工程师          离线
◉ 小测 [AI]    Agent · 测试工程师         ● 处理中 · active
◉ 文档助手[AI] Agent · 文档撰写           ○ 空闲   · active
```

- agent 行：头像带 AI 角标、名称旁 `[AI]` 徽章、类型列显式标「Agent」、状态列含生命周期 + 实时忙碌。
- 顶部筛选可切「全部 / 仅人类 / 仅 Agent」（对应 `member_type`）。
- `[ + 新建 Agent ]` 与「邀请成员」并列，体现 agent 与人类同为可加入的「成员」。
- 行内角色下拉中 agent 行的 `owner` 选项禁用置灰（agent 不得为 owner）。

### 4.6 看板卡片与评论区中 agent 头像与徽章

看板卡片：
```
┌──────────────────────────┐
│ 修复登录态丢失      [高]   │
│ #MES-42                  │
│ 标签: [bug]              │
│            ◉[AI] ●处理中  │  ← 负责人头像 + AI 角标 + 忙碌动效
└──────────────────────────┘
```
评论区：
```
┌──────────────────────────────────────────────┐
│ ◉[AI] 小测 · AI · 2 分钟前                     │
│ 已复现：在 token 过期后刷新页面会丢失登录态。    │
│ 最小复现步骤：1) ……  2) ……                     │
│ [ 产物附件 ▾ ]                                 │
└──────────────────────────────────────────────┘
```

- 卡片：负责人位显示 agent 头像（右下 AI 角标），处理中加动态指示（脉冲 / 进度）。
- 评论：agent 评论恒带 `[AI]` 标签与徽章，署名是 agent 自身；产物（报告 / 补丁）作为附件挂在该评论下。
- `@` 候选弹层：agent 项带 AI 图标 + 副文案「**发布后将触发一次运行**」（不得写"选中将立即触发"——触发发生在评论提交后，README §6.9）；composer 提交前展示 **trigger preview**（将被触发的 agent 清单）并提供「本次不触发」抑制开关（`suppress_triggers: true`，README §6.9）。
- 分派人选择器：人与 agent 分组或加图标区分；选中 agent 时浮出提示「**保存后将自动开始工作**」；再次选择同一 assignee 为 no-op（README §6.9）。

### 4.7 关键交互流程：创建 → 配置 → 分派 → 观察自动工作

```
1. 用户在 成员名册页（人与 agent 统一名册 —— agent 的**唯一**创建入口，README §6.12；
   Settings→Agents 仅承载工作区级 agent 策略，不维护 agent 名册与「+ 新建」入口）点「+ 新建 Agent」，走向导：
   ① 起名「小测」、传头像、填角色标签
   ② 选「均衡」模型档位、套用「严谨工程」预设、写岗位说明书
   ③ 绑定「回归测试」技能 + 「代码执行(需确认)」工具
   ④ 可见性选 workspace → 完成
2. 「小测」立即出现在成员列表与 @ /分派候选（头像带 AI 角标）。
3. 用户在看板把卡片 #MES-42 的负责人改为「小测」。
4. 系统发 issue.assigned 事件 → enqueue_agent_run 入队 → 卡片头像出现「●处理中」动效，
   issue 状态自动转「进行中」，时间线记一条「小测 已开始处理」。
5. 运行中：卡片 / 详情实时显示进度指示；agent 阶段性进展以评论回流。
6. 运行完成：agent 发一条带产物的评论，把状态置「待评审」，触发通知给分派者；用户在评论区复核产物。
7. 全程 agent 以自身身份署名，AI 徽章始终可见。
```

设计要点：分派即触发是「无感自动化」的关键——用户不需要再点「开始」，这正是「agent 像队友一样接单干活」的体验核心。同时通过浮出提示与去重防抖，避免「不知道它已经开始」或「重复触发」。

### 4.8 生命周期状态机

```mermaid
stateDiagram-v2
    [*] --> active : 创建完成
    active --> paused : pause（临时挂起）
    paused --> active : resume
    active --> disabled : disable（停用）
    paused --> disabled : disable
    disabled --> active : enable
    active --> archived : archive
    paused --> archived : archive
    disabled --> archived : archive
    archived --> active : restore
    active --> [*] : 软删除(deleted_at)
    paused --> [*] : 软删除
    disabled --> [*] : 软删除
    archived --> [*] : 软删除
```

状态语义与拦截规则：
- **active**：可被分派 / `@` 触发，可正常处理任务。
- **paused**：不接新任务；进行中任务按 `in_flight_policy` 决定继续或一并暂停；可快速 resume。
- **disabled**：停用，不接新任务；保留全部历史归属。
- **archived**：从主列表隐藏，数据保留；restore 回 active。
- **软删除**：置 `deleted_at`，默认从所有候选 / 列表隐藏；历史外键以「已停用 agent」占位渲染。
- 非法迁移（如 archived 直接 pause）返回 `409 conflict`。状态变更经 `agent.lifecycle_changed` 广播，UI 即时刷新。
- 与名册联动：`disable` 时 `members.status` 置 `disabled`；`enable/restore` 回 `active`；软删除时 `members.status` 置 `removed`（权威 `members` 模型无 `deleted_at` 列，以 `status='removed'` 为名册软终态，与 README §6.1 一致；本句更正旧文案对 `members.deleted_at` 的误述）。

### 4.9 实时性方案

- **在线 / 容量状态**：agent 呈现从二元「空闲 / 处理中」改为 **「运行中 N / 排队 M / 需审批 K」**（README §6.12），由 `task_executions` 按 agent 聚合 + `approvals` 计数推导，经 `agent:{id}:presence` 频道推送；列表与卡片上的指示即时变化（处理中辅以脉冲动画，但**动画/颜色不作为唯一状态信号**，恒有文字/图标，README §6.12）。
- **运行进度**：每次运行有独立事件流，把「开始 / 阶段进展 / 完成 / 失败」推给订阅了该 issue 或该 agent 的客户端，卡片与详情页实时刷新。
- **配置 / 状态变更**：agent 的生命周期与配置变更经 workspace 级频道广播，多端同步。
- **降级**：`/ws` 断线时回退到带 `updated_at` 的轮询（如每 5s 拉一次相关 agent 的轻量状态），重连后凭 `since_seq` 增量补齐。
- **一致性**：所有实时事件携带单调递增 `seq` / `updated_at`，客户端按序去重，避免乱序覆盖。

### 4.10 通知与人类监督

通知（进收件箱，可按类型开关，含「agent 运行通知」总开关）。**事件分级以 README §6.13 唯一通知优先级矩阵为准，本表为其 agent 视角摘录**——是否进收件箱、是否穿透 quiet hours、是否重置未读，一律以 §6.13 矩阵为唯一依据：

| 事件 | priority | 通知对象 | 渠道 |
|------|----------|----------|------|
| 我分派的 issue 由 agent **完成（成功）** | normal | 分派者 / 订阅者 | 默认留运行页 / 时间线，**不进收件箱**；仅当在 `notification_preferences` 显式订阅「执行结果」后进收件箱；**不穿透 quiet hours、不重置未读** |
| 我分派的 issue 由 agent **失败 / 超时** | critical | 分派者 / 订阅者 | 进收件箱 + **穿透 quiet hours + 重置未读**，站内 + 实时邮件 |
| 我 `@` 触发的 agent 运行**成功**结束 | normal | 触发者 | 默认不进收件箱（留运行页；仅订阅后入箱，不穿透、不重置未读） |
| 我 `@` 触发的 agent 运行**失败 / 超时** | critical | 触发者 | 进收件箱 + 穿透 quiet hours + 重置未读，站内 |
| 我创建 / 拥有的 agent 被停用 / 归档 / 转移 | normal | 所有者 | 站内 |
| agent 运行需要人工确认（高风险工具）`[Mesh 特色]` | critical | 分派者 / 所有者 | 站内（进「待我审批」统一入口，穿透 quiet hours + 重置未读，README §6.10） |
| agent 长时间无心跳 / 运行卡死 | critical | 所有者 / 管理员 | 站内 |

人类干预矩阵（自主性以「人类随时可踩刹车」为前提）：
1. **暂停一个正在工作的 agent**：选 `cancel_current` 立即取消在途执行（runtime 无 pause/resume 执行态，README §6.4），或 `finish_current` 让它跑完手头执行再停（见 §3.2 修订说明）。
2. **取消单次运行**：在 issue 的运行进度条上「停止本次运行」，不影响 agent 整体生命周期。
3. **复核产出**：agent 完成后 issue 置「待评审」，产物以评论附件呈现；人类可批准（转「完成」）、打回（评论补充意见，重新 `@` 或再次分派触发返工）。
4. **高风险操作闸门**：绑定为 `confirm_required` 的工具在执行前创建统一 `approvals` 审批（README §6.10），执行进入 `awaiting_approval`，批准后才继续、拒绝/过期则取消——写操作 / 外部调用的默认护栏；三套审批（工具 / squad 计划 / autopilot 动作）统一进入「待我审批」入口。
5. **配置回滚**：发现行为异常可回滚到上一个配置版本，快速止损（在途执行不受影响，快照已冻结，README §6.11）。
6. **审计可追溯**：所有配置变更、生命周期操作、绑定变更留有版本与操作者记录。

---

## 5. 验收标准

### 5.1 功能验收

- [ ] 创建 agent 在同一事务写入 `agents` + `members`（`member_type='agent'`，`agent_id` 指向上行）+ 绑定 + 首个配置版本，缺一即整体回滚；`agents` 不含 `member_id` 列（关联方向为 `members.agent_id → agents.id`，README §6.1）。
- [ ] `members.id` 是 issue 负责人 / 评论作者 / @提及的唯一引用键；agent 与人类在同一候选集中可被分派与提及。
- [ ] agent 发评论 / 改状态以自身 `member_id` 署名，时间线显示为 agent 身份且带 AI 徽章。
- [ ] 模型与推理参数保存前完成范围校验（temperature ∈ [0,2]、top_p ∈ [0,1]、max_tokens ≥ 1），越界返回 `422`。
- [ ] 每次 `PATCH /config` 生成新的不可变 `agent_config_versions` 快照并更新 `active_config_version_id`；可列出历史、对比、回滚。`agent_config_versions` 携带 `workspace_id`，`agent_id`/`changed_by` 均为同租户复合 FK，`UNIQUE(workspace_id, agent_id, id)` + `agents` 的重叠复合 FK 保证 **active 指针不跨 agent / 不跨租户串指、审计成员不来自别的工作区**（README §6.2 第 2/7 条，集成测试 T27）。
- [ ] 技能 / 工具可逐项启停；绑定与授权全部走 skill.md（`agent_skills`/`skill_installations`/`granted_capabilities`，不重复建表；`agent_skill_bindings`/`agent_tool_bindings`/`tools` 已删除）；**工具权限统一为 capability 条目语义**（`{"capability": "<key>", "permission": "read_only|write|confirm_required"}`，**无工具主键**），`/agents/{id}/tools` 系列端点为 `skill_installations.granted_capabilities` 能力条目的薄封装（README §6.11）；高风险能力默认 `permission='confirm_required'`，执行时经统一 `approvals`（README §6.10）；入队快照含绑定版本与 `capability_grants` 授权清单（README §6.11）。
- [ ] 可见性 `workspace`/`private` 生效：private agent 非所有者 / 非 admin 不可见、不可触发。
- [ ] 生命周期状态机按 4.8 实现；非法迁移返回 `409`；`disable` 时 `members.status` 联动置 `disabled`。
- [ ] 软删除置 `deleted_at` 后从所有列表 / 候选隐藏；历史评论以「已停用 agent」占位渲染，外键不报错。
- [ ] 所有权转移仅所有者 / admin 可操作；转移后 `owner_user_id` 更新并发 `agent.updated`。
- [ ] **分派即触发**：把 agent 设为 issue 负责人发出 `issue.assigned` → 自动入队一次运行，无需人工点「开始」；`trigger_on_assign=false` 时不触发。
- [ ] **@提及触发**：评论 @agent 与分派共用同一 `enqueue_agent_run` 入口。
- [ ] **触发语义符合 README §6.9 矩阵（可逐行测试）**：再选同一 assignee = no-op；无字段变化的保存 = no-op；同评论重复 @ = 仅一次执行；编辑评论仅为新增提及入队、无关文字修改不重复触发；新评论再次 @ 运行中的 agent = 新执行（频率护栏兜底）；替换分派取消前任在途执行（`superseded`）。
- [ ] **入队经 transactional outbox**（README §6.6）：业务提交与事件入队同事务，kill relay 后重启不丢触发（集成测试 T5）。
- [ ] **入队快照**（README §6.11）：`task_executions.config_snapshot` 冻结 agent_config_version、skill 版本、`capability_grants`（capability key + permission，**无工具主键**）、repo/base SHA、trigger_event_id；配置变更不影响在途执行。
- [ ] **入队写权威能力需求**（README §6.4）：创建 `task_executions` 时写入权威 `label_requirements` 与 `required_capabilities`（**严格 capability key 字符串数组**，经 §3.3 入队归一算法从绑定技能声明派生——字符串条目与 `{capability,permission}` 对象条目一律归一为纯 key 集合，**任何对象形态不得进入 `required_capabilities`**，否则 claim 的 JSONB `<@` 匹配永不命中），claim 时以 `e.required_capabilities <@ runtimes.capabilities` 做服务端能力匹配；授权快照 `config_snapshot.capability_grants` 为严格 `[{capability,permission}]` 对象数组（集成测试 T28）。
- [ ] **暂停 / 停用拦截**：agent 处于 paused/disabled 时分派 / @ 不触发运行，发 `agent.trigger_skipped` 并提示。
- [ ] **运行状态回流**：运行开始 / 进行 / 完成 / 失败实时回写 issue（卡片忙碌指示、进度、评论），agent 接单自动置「进行中」、产出置「待评审」。
- [ ] 全场景 AI 徽章不可关闭：列表、卡片、评论、@候选、分派选择器均显示；@候选提示为「**发布后将触发一次运行**」，提交前有 trigger preview 与显式抑制开关（README §6.9）。
- [ ] 高风险能力（`confirm_required`）执行前创建统一 `approvals` 审批（README §6.10 唯一协议）：执行进入 `awaiting_approval` 时**当前 attempt 置 `cancelled(awaiting_approval)`、租约结束、容量幂等释放**（无在途租约，reaper 无需特殊处理）；批准后执行回 `queued`，下一次领取建 attempt #N+1，凭审批请求时冻结的 `resume_context`（检查点引用 + 已完成步骤水位 + 待执行工具调用参数）**从审批点续跑**；拒绝/过期转 `cancelled`；「待我审批」入口聚合展示动作/权限（capability + permission）/影响范围/成本/过期时间。
- [ ] **实时事件词汇（README §6.7 注册表）**：本 Spec 所有 WebSocket 帧示例与事件表统一使用注册表内事件名——运行状态回流为 `execution.*`（`queued`/`started`/`progress`/`awaiting_approval`/`completed`/`failed`），agent 域为 `agent.*`，审批为 `approval.created`/`approval.decided`；**帧示例与事件表已统一为 `execution.*`，无任何未登记的运行起始事件名**。
- [ ] agent 容量呈现为「运行中 N / 排队 M / 需审批 K」（README §6.12），非二元空闲/处理中。
- [ ] 跨模块外键按 README §6.2 建复合 FK（`agents(workspace_id,id)` UNIQUE 等），跨租户引用被数据库拒绝（集成测试 T1）。
- [ ] **agent 创建入口唯一为成员名册页（R4，R5 防回归加固，README §6.12）**：创建向导仅从成员名册「+ 新建 Agent」进入（§4.5/§4.7）；**「仅 Agent」视图是成员名册页的筛选投影（同一路由 / 同一列表组件 / 同一 `[ + 新建 Agent ]` 入口，§4.2），不存在独立「Agents」列表页、第二导航 / 第二名册或第二个创建入口**；Settings→Agents 仅承载工作区级 agent 策略（默认 runtime、触发护栏、审批策略），**不维护 agent 名册列表与「+ 新建」入口**；Spec 全文（含 §4.x 交互流程）不存在「设置→Agents 创建 agent」的表述（与 onboarding.md §1.2.1 唯一入口一致）；**`tests/docs/check_roster_entry.py` 文档结构校验通过（CI 常跑，T35）——独立 `Agents [+新建]` 页面 / 未标注为投影的「Agent 列表页」表述 / 导航图中的 Agents 新建入口均被判失败**。

### 5.2 非功能验收

- [ ] **不重复领取**：分派 / @ 产生的执行经幂等键 + 状态机校验，绝不产生重复的 `task_executions`（与 runtime 的 SKIP LOCKED 协同）。
- [ ] **失联自愈**：agent 绑定的 runtime 失联时，运行由 runtime 模块 requeue / 标记失败；agent 侧 presence 与卡片状态随之更新，无需人工干预。
- [ ] **凭证不落盘**：本模块不持久化任何运行期凭证；凭证由 runtime 在 claim 时一次性下发，agent 配置中只引用 `credential_id`，不存明文。
- [ ] **不可信内容隔离**：issue 标题 / 描述 / 评论 / 附件注入 agent 上下文时显式标记为不可信数据并做结构隔离（见 README §6「不可信内容处理」）；agent 写出的评论 / 附件产出物经全通道 secret 命中检测，命中即拦截并告警。
- [ ] **日志时延**：运行进度 / 状态事件从发生到 UI 可见 P95 ≤ 2s（WebSocket 在线时）；断线重连凭 `since_seq` 补发不丢不重。
- [ ] **配置不可变**：历史配置版本只增不改；回滚通过新增版本实现，审计链完整可追溯。
- [ ] **审计完整**：所有配置 / 生命周期 / 绑定变更记录操作者 `members.id` 与时间，可查询。
- [ ] **限流**：创建 / 更新 / 触发类端点接入限流，超限返回 `429` 带 `Retry-After`。
- [ ] **错误信息不泄漏**：错误响应无堆栈 / 无 SQL，仅含字段级 `details`。
- [ ] **用户可控 URL scheme 校验**：`avatar_url` 等用户可控 URL 字段服务端校验 scheme，禁止 `javascript:`/`data:`，仅允许 `https`。
- [ ] **性能**：成员 / agent 列表主查询命中 `idx_members_ws_type_status` / `idx_agents_lifecycle`，10 万成员 workspace 下列表 P95 ≤ 300ms。
