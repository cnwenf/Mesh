# 技能(Skill)功能 Spec

> **所属层**:智能体编排层(为 agent 注入领域知识 / SOP / 可复用工作流的"可安装结构化指令包")。
> **依赖的其他 Spec**:
> - `workspace.md`:`skills.workspace_id` / `skill_sources.workspace_id` 外键回 `workspaces.id`,资源以 workspace 为隔离边界。
> - `member.md`:技能创建者、安装者、审批者引用 `members.id`(统一名册);agent 绑定引用 `agents.id`。
> - `agent.md`:绑定终点是某个 agent 定义(`agents.id`);技能的运行时能力声明对应 agent 的工具/权限配置。
> - `runtime.md`:技能脚本在受控沙箱内由 agent 运行执行,执行实例为 runtime 的 `task_executions`(README §6.4);越权调用由运行时拦截。
> - `auth.md`:安装 / 审批 / 绑定类操作的 RBAC 校验、审计、限流;来源凭据存于 secret manager。
> - `attachment.md`:脚本正文与引用资料以对象存储引用承载(`content_ref`)。
> **被依赖方**:`agent.md`(agent 的能力 = 运行时 + 已绑定技能集合)、`autopilot.md`(执行 agent 的能力边界受其绑定技能影响)。

---

## 全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)

1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `UUID`(`gen_random_uuid()`);所有表含 `created_at` / `updated_at`(`TIMESTAMPTZ`,默认 `now()`,UTC);软删除统一 `deleted_at TIMESTAMPTZ NULL`;`skill_versions` 为不可变快照(无 `updated_at`)。
2. **成员**:成员模型以 README §6.1 为唯一权威——技能创建者 / 安装者 / 审批者为人类成员,引用 `members.id`(复合 FK,README §6.1/§6.2);agent 绑定明确指向某 agent 定义,引用 `agents.id`(复合 FK)。**存储层不设任何 `*_type`/`*_kind` 判别列**;人类/agent 判别一律 JOIN `members.member_type`,API 响应可携带服务端计算的 `member_type` 快照(标注"快照,真源为 members")。
3. **多租户**:跨模块外键一律按 README §6.2 建复合 FK + 目标表 `UNIQUE(workspace_id, id)`。
4. **接口**:基础路径 `/api/v1`;包络 / 分页 / 错误信封 / 过滤限制见 README §6.14;安装 / 审批 / 绑定类 API token 只存哈希、显式 scope(auth.md)。
5. **实时**:统一实时契约见 README §6.7(频道内 `seq`、`realtime_events` 持久重放、`resume_from` / `resync_required`);事件名 `<entity>.<action>`。
6. **队列 / 投递**:导入为长任务,遵循 README §6.4 execution/attempt 分层;入队时按 README §6.11 冻结技能版本快照;脚本越权拦截见 runtime.md。
7. **ORM**:SQLAlchemy 2.x 约定(类型注解映射、`select()` 查询、异步会话)。

> 本 Spec 的第三方技能脚本/权限审批是**技能导入域内的前置闸门**(以导入任务状态机承载,见 §3.1 `import` / `approve`),与 README §6.10 统一审批实体所聚合的三类运行时审批(tool_call / autopilot_action / squad_plan)语义对齐但**不复用 `approvals` 表**(subject_type 不含技能导入)。

---

## 1. 功能描述

### 1.1 模块定位

技能(Skill)是 Mesh 给 agent 注入**领域知识、标准作业流程(SOP)与可复用工作流**的打包单元。一个技能本质是"提示词 + 资源 + 可执行逻辑"的三合一:

1. **markdown 指令正文** —— 运行时注入 agent 上下文的自然语言指令,承载 SOP、规范、领域知识;
2. **可执行脚本** —— shell / python 等脚本,被 agent 在受控沙箱内调用;
3. **引用资料** —— 供 agent 按需检索的领域文档(API 说明、模板、样例)。

模块的核心设计是 **「定义—版本—安装—绑定」四层解耦**:

- **定义(`skills`)**:逻辑实体,承载名称、摘要、来源、生命周期状态等元信息;
- **版本(`skill_versions`)**:某一时刻的**不可变快照**(指令正文 + 脚本 + 资料),支撑回滚与灰度;
- **安装(`skill_installations`)**:把某版本引入某作用域(workspace 级 / agent 级)的记录;
- **绑定(`agent_skills`)**:把已安装技能与具体 agent 关联,使其对该 agent 可用。

四层分离使"版本回滚 / 灰度发布 / workspace 共享 / agent 专属"互不干扰。**安全是技能系统的头号约束,必须前置而非补丁**:技能是"可执行的提示词包",一旦放松,等于给 agent 开了不受控的副作用通道。

> **全局名册约定(以 README §6.1 为唯一权威)**:人与 agent 统一登记在 `members` 名册(`members.id` 为统一引用键,人类/agent 由 `members.member_type` 判别)。本 Spec 中:技能创建者 / 安装者 / 审批者为人类成员,引用 `members.id`(复合 FK);agent 绑定明确指向某 agent 定义,引用 `agents.id`(复合 FK)。**本模块各表不存 `*_type`/`*_kind` 判别列**,类型判别一律 JOIN `members`(见顶部锚点 §2)。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| K1 | 技能定义与构成 | 名称 / 摘要 / 触发条件 / 指令正文(必备)+ 脚本 / 资料 / IO 约定 / 权限声明(可选) | 管理员把团队"代码评审 SOP"打包成技能,声明所需权限"只读代码 + 评论" |
| K2 | 技能来源与信任分级 | `builtin > user > marketplace > url`,信任越低审核越严 | 成员从技能市场导入"依赖扫描",另一成员粘贴仓库地址导入私有"发布检查清单" |
| K3 | 导入流程(解析→校验→沙箱预览→安装) | 异步多阶段任务;第三方脚本强制人工审阅 | 运维粘贴仓库地址,预览页逐行查看 2 个 shell 脚本后确认安装 |
| K4 | 安装范围(workspace / agent) | workspace 级全员可绑;agent 级直装直绑 | "客服话术规范"workspace 级;"某 legacy 系统专属操作"agent 级 |
| K5 | 绑定 / 解绑 | 建立 `agent ↔ 已安装版本`;支持批量;可指定自动触发 | 新建"前端开发"agent 后绑定"组件命名规范""单测编写规范" |
| K6 | 版本与更新(SemVer + 不可变快照) | 升级默认不自动覆盖;`updated_available` 提示;可回滚 / 灰度 | 市场技能发 1.2.0,管理员看变更日志后升级;出问题一键回滚 1.1.0 |
| K7 | 发现与自动触发 | 显式调用 + 任务上下文匹配注入(关键词 / 语义 / 标签 / 优先级互斥) | agent 接到含"评审"的任务,平台匹配注入"代码评审规范"SOP |
| K8 | 启用 / 停用(三档软状态) | 定义级 / 安装级 / 绑定级,可随时恢复 | 某脚本发现风险,安装级停用,所有 agent 停止注入 |
| K9 | 权限与安全 | 沙箱 + 权限最小化 + 第三方脚本人工审核 + 审计 + 一键熔断 | 从 url 导入的技能申请"shell + 出站网络",安全负责人仅授予只读 shell |
| K10 | 技能市场浏览 | 浏览 / 搜索 / 排序市场可导入技能 | 在市场中按热度找到"接口文档生成"并预览导入 |

### 1.3 边界与非目标(明确不做什么)

- **不**定义 agent 的运行时绑定、模型配置、调度 —— 归 `agent.md` / `runtime.md`(本 Spec 仅提供"agent 可绑定哪些技能")。
- **不**实现脚本沙箱的底层隔离机制(容器 / seccomp / 网络命名空间)—— 归 `runtime.md`(本 Spec 声明权限模型与拦截语义)。
- **不**定义技能市场后端(条目上架 / 评分 / 认证体系)—— 仅消费市场列表接口;市场为外部来源之一。
- **不**定义自动触发所需的向量检索 / 语义模型实现 —— 仅声明匹配策略与可解释、可裁剪、可关闭的契约。
- **不**支持运行时动态修改某版本内容(版本不可变;变更必须发新版本)。
- **不**做跨 workspace 的技能共享 / 迁移(YAGNI;`builtin` 技能由平台注入到系统 workspace)。

---

## 2. 数据模型

四层关系:**`skills`(定义)→ `skill_versions`(版本)→ `skill_installations`(安装)→ `agent_skills`(绑定)**;`skill_sources` 描述来源与信任级别;`skill_scripts` / `skill_references` / `skill_triggers` 挂靠在版本下。

### 2.1 ER 概览(文字图)

```
skill_sources(来源/信任级)──1:N──┐
                                ▼
                          skill(定义)──1:N──► skill_versions(不可变快照)
                                                  │  ├─1:N─► skill_scripts(脚本)
                                                  │  ├─1:N─► skill_references(资料)
                                                  │  └─1:N─► skill_triggers(触发条件)
                                                  ▼ 1:N
                                          skill_installations(安装:scope=workspace|agent)
                                                  │ 1:N
                                                  ▼
workspaces ──隔离──► (以上全部携带 workspace_id)   agent_skills(绑定)──N:1──► agents(agent.md)

members(member.md):skill.created_by / skill_installations.installed_by / 审批者 → members.id(human)
agents(agent.md):skill_installations.agent_id / agent_skills.agent_id → agents.id
```

### 2.2 表:`skills`(技能定义)

> SQLAlchemy 2.x 声明式约定;字段名 snake_case;主键 UUID v4。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 所属 workspace(`builtin` 技能归系统 workspace) |
| `source_id` | UUID | NOT NULL,**复合 FK `(workspace_id, source_id) → skill_sources(workspace_id, id)`** | — | 来源(README §6.2) |
| `name` | TEXT | NOT NULL | — | 显示名 |
| `slug` | TEXT | NOT NULL,CHECK (`^[a-z0-9][a-z0-9-]*$`) | — | URL / 匹配用短名 |
| `summary` | TEXT | NOT NULL | — | 一句话摘要(用于发现与匹配) |
| `status` | TEXT | NOT NULL,CHECK IN ('draft','published','deprecated','disabled') | `'draft'` | 生命周期状态 |
| `current_version_id` | UUID | NULL,**同 skill 复合 FK `(workspace_id, id, current_version_id) → skill_versions(workspace_id, skill_id, id)` ON DELETE SET NULL (current_version_id)** | NULL | 当前指向版本(NULL=尚无版本);**current_version 必须属于同一 skill——由重叠复合 FK 在数据库层强制(README §6.2 第 7 条:被引用表建 `UNIQUE(workspace_id, skill_id, id)`,引用方以 `(workspace_id, id, current_version_id)` 重叠复合 FK 引用);置空仅 `current_version_id` 列、`workspace_id` 保持不动(PG16 列级 SET NULL,第 6 条)** |
| `required_capabilities` | JSONB | NOT NULL | `'[]'` | 所需工具/权限声明数组 |
| `tags` | TEXT[] | NOT NULL | `'{}'` | 标签,用于发现 |
| `icon` | TEXT | NULL | NULL | 图标标识 |
| `created_by` | UUID | NOT NULL,**复合 FK `(workspace_id, created_by) → members(workspace_id, id)`** | — | 创建者(人类成员,README §6.1/§6.2) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除 |

> **复合 FK 引用前提(README §6.2)**:`skills` 为可被跨表引用的工作区级实体,除 `PK(id)` 外建 **`UNIQUE (workspace_id, id)`**(供 `skill_versions.skill_id`、`skill_installations.skill_id`、`agent_skills`(经安装)等复合引用)。

### 2.3 表:`skill_versions`(版本快照 —— 不可变)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 冗余隔离列(与所属 `skills` 同 workspace),供复合 FK 引用(README §6.2) |
| `skill_id` | UUID | NOT NULL,**复合 FK `(workspace_id, skill_id) → skill(workspace_id, id)`** | — | 所属技能 |
| `version` | TEXT | NOT NULL | — | SemVer 字符串,如 `1.2.0` |
| `instructions` | TEXT | NOT NULL | — | markdown 指令正文 |
| `status` | TEXT | NOT NULL,CHECK IN ('draft','published','deprecated') | `'draft'` | 版本状态 |
| `changelog` | TEXT | NULL | NULL | 变更说明 |
| `io_contract` | JSONB | NULL | NULL | 输入/输出约定(JSON Schema) |
| `required_capabilities` | JSONB | NOT NULL | `'[]'` | 该版本所需权限(可与定义级不同) |
| `manifest` | JSONB | NOT NULL | `'{}'` | 解析后的原始清单文件,留档 |
| `content_hash` | TEXT | NOT NULL | — | 指令正文 + 脚本的内容哈希(去重 / 变更检测) |
| `created_by` | UUID | NOT NULL,**复合 FK `(workspace_id, created_by) → members(workspace_id, id)`** | — | 创建者(README §6.1/§6.2) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | 创建时间(版本不可变,**无 updated_at**) |

> **不可变约束**:版本一旦 `status='published'` 即冻结,任何修改必须新建版本。`content_hash` 用于检测来源是否真的有变化(避免重复发版)。
>
> **复合 FK 引用前提(README §6.2)**:`skill_versions` 被 `skills.current_version_id`、`skill_installations.skill_version_id`、`agent_skills.skill_version_id` 跨表引用,除 `PK(id)` 外建 **`UNIQUE (workspace_id, id)`**;**另建 `UNIQUE (workspace_id, skill_id, id)`(重叠唯一键,供 `skills.current_version_id`、`skill_installations`、`agent_skills` 的"同 skill 版本"重叠复合 FK 引用——保证引用行与被引用版本属于同一 skill,README §6.2 第 7 条)**。
>
> **入队版本快照(README §6.11,必须实现)**:技能版本不可变。任务入队时,该 agent 当时绑定的各技能版本被冻结进 `task_executions.config_snapshot.skill_versions`(`{"<skill_id>": "<skill_version_id>", ...}`);**绑定变更、安装版本切换、回滚、灰度都只影响后续入队,不改动在途执行**——在途执行永远运行其入队时刻快照里的版本,保证可复现、可审计。

### 2.4 表:`skill_installations`(安装记录)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 安装到的 workspace |
| `skill_id` | UUID | NOT NULL,**复合 FK `(workspace_id, skill_id) → skill(workspace_id, id)`** | — | 安装的技能(README §6.2) |
| `skill_version_id` | UUID | NOT NULL,**同 skill 复合 FK `(workspace_id, skill_id, skill_version_id) → skill_versions(workspace_id, skill_id, id)`** | — | 当前安装的版本(**安装版本必须属于所装 skill**——重叠复合 FK 在数据库层强制,README §6.2 第 7 条) |
| `scope` | TEXT | NOT NULL,CHECK IN ('workspace','agent') | `'workspace'` | 安装范围 |
| `agent_id` | UUID | NULL,**复合 FK `(workspace_id, agent_id) → agents(workspace_id, id)`** | NULL | `scope='agent'` 时指定的 agent(README §6.2) |
| `install_status` | TEXT | NOT NULL,CHECK IN ('installed','updated_available','disabled') | `'installed'` | 安装状态 |
| `auto_update` | BOOLEAN | NOT NULL | `false` | 是否自动跟随新版本(仅跟非破坏性 PATCH) |
| `granted_capabilities` | JSONB | NOT NULL | `'[]'` | 实际被授予的权限(审批后) |
| `installed_by` | UUID | NOT NULL,**复合 FK `(workspace_id, installed_by) → members(workspace_id, id)`** | — | 安装者(README §6.1/§6.2) |
| `installed_at` | TIMESTAMPTZ | NOT NULL | `now()` | 安装时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除(卸载) |

**CHECK(应用层 + 触发器)**:`scope='agent'` 时 `agent_id` 必须非空;`granted_capabilities ⊆ skill_versions.required_capabilities`(只授予声明过的权限子集)。

> **工具权限语义(MES-2 必修-3:工具权限并入能力语义,不再另设 `tools`/`agent_tool_bindings` 表)**:`required_capabilities`/`granted_capabilities` 是**授权声明层**字段,数组的每一项既可以是纯字符串能力键(如 `"exec:shell"`),也可以是对象 `{"capability": "exec:shell", "permission": "read_only|write|confirm_required", "enabled": true}`;**工具级权限分级(read_only / write / confirm_required)即在此表达**:未标注 `permission` 的能力默认按 `confirm_required` 处理(高风险默认需人工确认)。`enabled` 仅允许出现在 `granted_capabilities` 对象条目中且默认 `true`;Agent 工具面板禁用能力时持久化 `enabled=false` 以支持再次启用,但入队收集必须先过滤该条目,不可把它写入调度需求或运行授权快照。执行时命中 `confirm_required` 的工具调用经统一 `approvals` 闸门(README §6.10)批准后方可执行。
>
> **声明层 ≠ 调度层(R3 硬约束,README §6.4/§6.11)**:本表的混合格式**仅供声明与授权**,绝不得原样写入调度字段。任务入队时由 agent 编排入口先排除 `granted_capabilities` 中 `enabled=false` 的条目,再执行**入队归一算法**(agent.md §3.3,README §6.4 权威定义),派生出严格类型的两套字段:① 调度用 `task_executions.required_capabilities` = **纯 capability key 字符串数组**(对象条目取其 `capability` 键,去重排序;schema CHECK 禁止任何非字符串元素——对象一旦进入调度字段,runtime claim 的 JSONB `<@` 匹配永不命中,任务永久无法领取);② 授权快照 `task_executions.config_snapshot.capability_grants` = **严格 `[{capability, permission}]` 对象数组**(字符串条目补默认 `confirm_required`,不保留声明层的 `enabled`)。入队归一算法与两套字段的 schema/集成测试见 README §6.4/§6.11 与 T28;工具目录主键真源已删除,统一为版本化 capability key + permission(README §6.11,**任何 Spec 与示例不得再出现 `tool_id`**)。

> **复合 FK 引用前提(README §6.2)**:`skill_installations` 被 `agent_skills.skill_installation_id` 引用,除 `PK(id)` 外建 **`UNIQUE (workspace_id, id)`**。

### 2.5 表:`agent_skills`(绑定关系)

> 与 `skill_installations` 分开:安装记录"哪些技能进了库",绑定记录"哪些 agent 真正使用"。workspace 级安装的技能可被多个 agent 绑定。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `agent_id` | UUID | NOT NULL,**复合 FK `(workspace_id, agent_id) → agents(workspace_id, id)`** | — | agent(README §6.2) |
| `skill_id` | UUID | NOT NULL,**复合 FK `(workspace_id, skill_id) → skills(workspace_id, id)`** | — | 所绑技能(冗余父键,供下行 installation/version 的重叠复合 FK 共享同一 `skill_id`,README §6.2 第 7 条) |
| `skill_installation_id` | UUID | NOT NULL,**复合 FK `(workspace_id, skill_installation_id, skill_id) → skill_installations(workspace_id, id, skill_id)`** | — | 指向安装记录(重叠复合 FK,与下行 `skill_version_id` 共同保证绑定版本属于该安装所装 skill,README §6.2 第 7 条) |
| `skill_version_id` | UUID | NOT NULL,**同 skill 复合 FK `(workspace_id, skill_id, skill_version_id) → skill_versions(workspace_id, skill_id, id)`** | — | 绑定的具体版本(可与安装当前版本不同,支持灰度/回滚);**绑定版本必须属于该安装所装 skill——重叠复合 FK 链同时保证 installation 与 version 属于同一 skill(README §6.2 第 7 条);入队时此版本 id 被冻结进 `task_executions.config_snapshot.skill_versions`(README §6.11)** |
| `enabled` | BOOLEAN | NOT NULL | `true` | 绑定级启用开关 |
| `auto_trigger` | BOOLEAN | NOT NULL | `true` | 是否允许自动触发 |
| `priority` | INT | NOT NULL,CHECK (priority BETWEEN 0 AND 1000) | `100` | 自动触发优先级,数值大者优先 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> **同 skill 重叠复合 FK 链(README §6.2 第 7 条)**:`agent_skills` 冗余父键 `skill_id`,并以两条重叠复合 FK 引用——`(workspace_id, skill_installation_id, skill_id) → skill_installations(workspace_id, id, skill_id)` 与 `(workspace_id, skill_id, skill_version_id) → skill_versions(workspace_id, skill_id, id)`。两条 FK 共享同一 `skill_id`,在数据库层同时保证「绑定的 installation」与「绑定的 version」属于**同一个 skill**:绑定别 skill 的版本、或与安装不同 skill 的版本,均在 INSERT 被拒绝。

### 2.6 表:`skill_sources`(来源)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 所属 workspace |
| `source_type` | TEXT | NOT NULL,CHECK IN ('builtin','user','marketplace','url') | `'user'` | 来源类型 |
| `name` | TEXT | NOT NULL | — | 来源显示名(市场名 / 仓库地址) |
| `uri` | TEXT | NULL | NULL | 来源地址;`builtin` 为空 |
| `trust_level` | TEXT | NOT NULL,CHECK IN ('trusted','reviewed','untrusted') | `'untrusted'` | 信任级别(`builtin→trusted`,`user→reviewed`,`marketplace/url→untrusted`) |
| `auth_ref` | TEXT | NULL | NULL | secret manager 的引用键(**绝不存明文凭据**) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除 |

> **复合 FK 引用前提(README §6.2)**:`skill_sources` 被 `skill.source_id` 复合引用,除 `PK(id)` 外建 **`UNIQUE (workspace_id, id)`**。

### 2.7 表:`skill_scripts` / `skill_references` / `skill_triggers`(版本子项)

**`skill_scripts`**

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `skill_version_id` | UUID | NOT NULL,FK→skill_versions(id) | — | 所属版本 |
| `path` | TEXT | NOT NULL | — | 文件相对路径 |
| `runtime` | TEXT | NOT NULL | `'shell'` | 运行时:shell / python / … |
| `entrypoint` | BOOLEAN | NOT NULL | `false` | 是否入口脚本 |
| `content_ref` | TEXT | NOT NULL | — | 对象存储引用(不直接存大文件正文) |
| `content_hash` | TEXT | NOT NULL | — | 内容哈希 |
| `required_capabilities` | JSONB | NOT NULL | `'[]'` | 该脚本申请的权限 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**`skill_references`**:`id`、`skill_version_id`(FK)、`path`、`media_type`(默认 `'text/markdown'`)、`content_ref`、`summary`(供检索)、`created_at`。

**`skill_triggers`**:`id`、`skill_version_id`(FK)、`trigger_type`(CHECK IN 'keyword','semantic','tag',默认 `'keyword'`)、`pattern`(关键词/标签值/语义描述)、`weight`(NUMERIC(5,2),默认 `1.0`)、`created_at`。

> **叶表隔离说明**:`skill_scripts` / `skill_references` / `skill_triggers` 为版本下的模块内叶表,仅经 `skill_version_id` 单一父链可达,不被其它模块跨表引用,故无需自身 `workspace_id` 与 `UNIQUE(workspace_id, id)`;其工作区隔离经 `skill_versions → skill` 父链传递(README §6.2 的复合 FK 约束作用于跨模块引用点)。

### 2.8 索引与约束

```sql
-- 复合 FK 引用前提(README §6.2):被跨表引用的工作区级表建 UNIQUE(workspace_id, id)
ALTER TABLE skills ADD CONSTRAINT uq_skill_ws_id UNIQUE (workspace_id, id);
ALTER TABLE skill_versions ADD CONSTRAINT uq_skill_version_ws_id UNIQUE (workspace_id, id);
ALTER TABLE skill_sources ADD CONSTRAINT uq_skill_source_ws_id UNIQUE (workspace_id, id);
ALTER TABLE skill_installations ADD CONSTRAINT uq_skill_installation_ws_id UNIQUE (workspace_id, id);

-- 同 skill 重叠唯一键(README §6.2 第 7 条):供"同 skill 版本"重叠复合 FK 引用
-- skill_versions(workspace_id, skill_id, id) 供 skills.current_version_id / skill_installations / agent_skills 的 skill_version 引用
ALTER TABLE skill_versions ADD CONSTRAINT uq_skill_version_ws_skill_id UNIQUE (workspace_id, skill_id, id);
-- skill_installations(workspace_id, id, skill_id) 供 agent_skills 的 installation 引用(共享 skill_id 保证同 skill)
ALTER TABLE skill_installations ADD CONSTRAINT uq_skill_installation_ws_skill_id UNIQUE (workspace_id, id, skill_id);

-- 同 workspace 内 slug 唯一(软删除范围内)
CREATE UNIQUE INDEX uq_skill_workspace_slug ON skills(workspace_id, slug) WHERE deleted_at IS NULL;
CREATE INDEX idx_skill_workspace_status ON skills(workspace_id, status);
CREATE INDEX idx_skill_sources ON skills(source_id);
CREATE INDEX idx_skill_tags ON skill USING GIN (tags);

-- 同一技能版本号唯一(不可变快照)
CREATE UNIQUE INDEX uq_skill_versions ON skill_versions(skill_id, version);
CREATE INDEX idx_skill_version_skill ON skill_versions(skill_id, created_at DESC);

-- 同作用域内一个技能只装一次(软删除范围内)
CREATE UNIQUE INDEX uq_install_scope
  ON skill_installations(workspace_id, skill_id, scope, agent_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_install_workspace ON skill_installations(workspace_id, install_status);
CREATE INDEX idx_install_skill_versions ON skill_installations(skill_version_id);
CREATE INDEX idx_install_updated ON skill_installations(install_status) WHERE install_status = 'updated_available';

-- 一个 agent 对同一安装只绑一次
CREATE UNIQUE INDEX uq_agent_skills ON agent_skills(agent_id, skill_installation_id);
CREATE INDEX idx_agent_skill_agent ON agent_skills(agent_id, enabled);
CREATE INDEX idx_agent_skill_install ON agent_skills(skill_installation_id);

CREATE INDEX idx_source_workspace_type ON skill_sources(workspace_id, source_type);
CREATE UNIQUE INDEX uq_script_version_path ON skill_scripts(skill_version_id, path);
CREATE INDEX idx_trigger_version ON skill_triggers(skill_version_id);
-- 关键词匹配可额外建 GIN / 全文索引:
CREATE INDEX idx_trigger_keyword ON skill_triggers USING GIN (to_tsvector('simple', pattern))
  WHERE trigger_type = 'keyword';
```

### 2.9 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `skills.workspace_id` / `skill_sources.workspace_id` / `skill_installations.workspace_id` | → `workspaces.id` | workspace.md | 隔离 |
| `skill.created_by` / `skill_versions.created_by` / `skill_installations.installed_by` | 复合 FK → `members(workspace_id, id)` | member.md | 创建/安装/审批者(人类成员;README §6.1/§6.2) |
| `skill_installations.agent_id` / `agent_skills.agent_id` | 复合 FK → `agents(workspace_id, id)` | agent.md | 明确指向某 agent 定义(README §6.2) |
| `skill_sources.id` / `skills.id` / `skill_versions.id` / `skill_installations.id` | 被引用方建 `UNIQUE(workspace_id, id)` | 本模块 | 供上述复合 FK 引用(README §6.2) |
| `skills.current_version_id` | 同 skill 复合 FK `(workspace_id, id, current_version_id) → skill_versions(workspace_id, skill_id, id)`(`ON DELETE SET NULL (current_version_id)` 列级) | 本模块 | current_version 必须属于同一 skill,重叠复合 FK 强制(README §6.2 第 7/6 条);引用 `skill_versions.UNIQUE(workspace_id, skill_id, id)` |
| `skill_installations.skill_version_id` | 同 skill 复合 FK `(workspace_id, skill_id, skill_version_id) → skill_versions(workspace_id, skill_id, id)` | 本模块 | 安装版本必须属于所装 skill(README §6.2 第 7 条) |
| `agent_skills.skill_id` | 复合 FK `(workspace_id, skill_id) → skills(workspace_id, id)` | 本模块 | 冗余父键,供下行重叠复合 FK 共享(README §6.2 第 7 条) |
| `agent_skills.skill_installation_id` | 复合 FK `(workspace_id, skill_installation_id, skill_id) → skill_installations(workspace_id, id, skill_id)` | 本模块 | installation 属于同一 skill;引用 `skill_installations.UNIQUE(workspace_id, id, skill_id)`(README §6.2 第 7 条) |
| `agent_skills.skill_version_id` | 同 skill 复合 FK `(workspace_id, skill_id, skill_version_id) → skill_versions(workspace_id, skill_id, id)` | 本模块 | 绑定版本必须属于该安装所装 skill,重叠复合 FK 链同时保证 installation 与 version 同 skill(README §6.2 第 7 条) |
| `skill_scripts.content_ref` / `skill_references.content_ref` | → 对象存储 | attachment.md | 脚本/资料正文 |
| `agent_skills.skill_version_id`(入队快照) | → `task_executions.config_snapshot.skill_versions` | runtime.md | 入队时冻结绑定版本(README §6.11);脚本越权由运行时拦截 |

---

## 3. 接口设计

REST 基础路径 `/api/v1`,集合嵌套于 `/workspaces/{ws}/`;鉴权 `Authorization: Bearer <token>`(见 auth.md)。**成功包络 / 游标分页 / 错误信封 / 乐观并发 / HTTP 语义 / 幂等写 / 过滤限制一律以 README §6.14 为唯一权威**(单对象 `{"data":{...}}`、列表 `{"data":[...],"next_cursor":<opaque|null>}`,`next_cursor=null` 表示末页;错误 `{"error":{"code","message","details"}}`),本 Spec 不重复定义,仅在下方列出本模块具名错误码。

### 3.1 REST 端点清单

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/workspaces/{ws}/skills` | 列出技能(`status`/`source_type`/`q` 过滤) | 成员 |
| POST | `/workspaces/{ws}/skills` | 创建技能定义(用户自建) | admin / `skill:manage` |
| GET | `/workspaces/{ws}/skills/{skill_id}` | 技能详情 | 成员 |
| PATCH | `/workspaces/{ws}/skills/{skill_id}` | 更新元信息 / 状态 | admin / `skill:manage` |
| DELETE | `/workspaces/{ws}/skills/{skill_id}` | 软删除技能 | admin |
| POST | `/workspaces/{ws}/skills/import` | 从来源导入(市场/URL),返回导入任务 | admin / `skill:manage` |
| GET | `/workspaces/{ws}/skills/import/{task_id}` | 查询导入进度 | 成员 |
| POST | `/workspaces/{ws}/skills/{skill_id}/approve` | 审批第三方技能脚本/权限 | 安全负责人 / admin |
| GET | `/workspaces/{ws}/marketplace/skills` | 列出市场可导入技能 | 成员 |
| GET | `/workspaces/{ws}/skills/{skill_id}/versions` | 列出技能版本 | 成员 |
| POST | `/workspaces/{ws}/skills/{skill_id}/versions` | 创建新版本 | admin / `skill:manage` |
| GET | `/workspaces/{ws}/skills/{skill_id}/versions/{version_id}` | 版本详情 | 成员 |
| GET | `/workspaces/{ws}/skill-installations` | 列出安装记录 | 成员 |
| POST | `/workspaces/{ws}/skill-installations` | 安装到指定 scope | admin / `skill:manage` |
| PATCH | `/workspaces/{ws}/skill-installations/{id}` | 更新安装(切换版本/启停/auto_update) | admin / `skill:manage` |
| DELETE | `/workspaces/{ws}/skill-installations/{id}` | 卸载(软删除) | admin |
| POST | `/workspaces/{ws}/skill-installations/{id}/rollback` | 回滚到指定历史版本 | admin / `skill:manage` |
| GET | `/workspaces/{ws}/agents/{agent_id}/skills` | 列出某 agent 已装/已绑技能 | 成员 |
| POST | `/workspaces/{ws}/agents/{agent_id}/skills` | 绑定技能到 agent | admin / `skill:manage` |
| PATCH | `/workspaces/{ws}/agents/{agent_id}/skills/{binding_id}` | 更新绑定(启停/优先级/auto_trigger) | admin / `skill:manage` |
| DELETE | `/workspaces/{ws}/agents/{agent_id}/skills/{binding_id}` | 解绑 | admin / `skill:manage` |

### 3.2 请求/响应 JSON 示例

**创建技能定义** `POST /api/v1/workspaces/{ws}/skills`
```json
// Request
{
  "name": "代码评审规范", "slug": "code-review-sop",
  "summary": "对改动进行安全、质量、可维护性评审的标准流程",
  "tags": ["review", "quality"],
  "required_capabilities": ["read:code", "write:comment"]
}
// 201 Response
{
  "data": {
    "id": "5f2a1c00-1111-4a2b-9c3d-000000000001",
    "workspace_id": "7ea1891c-0000-0000-0000-000000000001",
    "source_id": "9b0d0000-0000-0000-0000-000000000002",
    "name": "代码评审规范", "slug": "code-review-sop",
    "summary": "对改动进行安全、质量、可维护性评审的标准流程",
    "status": "draft", "current_version_id": null,
    "required_capabilities": ["read:code", "write:comment"],
    "tags": ["review", "quality"],
    "created_at": "2026-07-24T12:00:00Z", "updated_at": "2026-07-24T12:00:00Z"
  }
}
```

**从来源导入** `POST /api/v1/workspaces/{ws}/skills/import`
```json
// Request
{ "source_type": "url", "uri": "<内部仓库地址>/skills/release-checklist.git", "ref": "v1.3.0" }
// 202 Response
{ "data": { "task_id": "c1a00000-0000-0000-0000-000000000099", "status": "parsing",
  "stage": "manifest_parse", "created_at": "2026-07-24T12:01:00Z" } }
```

**查询导入进度(待人工审批脚本)** `GET /api/v1/workspaces/{ws}/skills/import/{task_id}`
```json
{
  "data": {
    "task_id": "c1a00000-0000-0000-0000-000000000099",
    "status": "awaiting_review", "stage": "sandbox_preview",
    "preview": {
      "name": "发布检查清单", "version": "1.3.0", "summary": "发布前的标准检查流程",
      "instructions_preview": "## 发布前检查\n1. 运行回归测试...",
      "scripts": [
        {"path": "scripts/check.sh", "runtime": "shell", "entrypoint": true,
         "required_capabilities": ["exec:shell", "net:outbound"]}
      ],
      "references": [{"path": "docs/runbook.md", "media_type": "text/markdown"}],
      "requested_capabilities": ["exec:shell", "net:outbound"]
    },
    "requires_approval": true,
    "created_at": "2026-07-24T12:01:00Z", "updated_at": "2026-07-24T12:01:20Z"
  }
}
```

**审批第三方技能(只授予部分权限)** `POST /api/v1/workspaces/{ws}/skills/{skill_id}/approve`
```json
// Request
{ "task_id": "c1a00000-0000-0000-0000-000000000099",
  "granted_capabilities": ["exec:shell"], "decision": "approve",
  "comment": "拒绝出站网络,仅允许只读 shell" }
// 200 Response
{ "data": { "skill_id": "5f2a1c00-0000-4a2b-9c3d-000000000010",
  "skill_version_id": "8d3b0000-0000-0000-0000-000000000020",
  "status": "published", "granted_capabilities": ["exec:shell"],
  "reviewed_by": "mem-uuid", "reviewed_at": "2026-07-24T12:05:00Z" } }
```

**安装到指定 scope** `POST /api/v1/workspaces/{ws}/skill-installations`
```json
// Request(workspace 级)
{ "skill_id": "5f2a1c00-0000-4a2b-9c3d-000000000010",
  "skill_version_id": "8d3b0000-0000-0000-0000-000000000020",
  "scope": "workspace", "auto_update": false }
// 201 Response
{ "data": { "id": "a1000000-0000-0000-0000-000000000030",
  "workspace_id": "7ea1891c-0000-0000-0000-000000000001",
  "skill_id": "5f2a1c00-0000-4a2b-9c3d-000000000010",
  "skill_version_id": "8d3b0000-0000-0000-0000-000000000020",
  "scope": "workspace", "agent_id": null, "install_status": "installed",
  "auto_update": false, "granted_capabilities": ["exec:shell"],
  "installed_at": "2026-07-24T12:06:00Z" } }
```

**绑定到 agent** `POST /api/v1/workspaces/{ws}/agents/{agent_id}/skills`
```json
// Request
{ "skill_installation_id": "a1000000-0000-0000-0000-000000000030",
  "skill_version_id": "8d3b0000-0000-0000-0000-000000000020",
  "auto_trigger": true, "priority": 120 }
// 201 Response
{ "data": { "id": "b2000000-0000-0000-0000-000000000040",
  "agent_id": "d3000000-0000-0000-0000-000000000050",
  "skill_installation_id": "a1000000-0000-0000-0000-000000000030",
  "skill_version_id": "8d3b0000-0000-0000-0000-000000000020",
  "enabled": true, "auto_trigger": true, "priority": 120,
  "created_at": "2026-07-24T12:07:00Z" } }
```

**列出某 agent 已装技能(分页)** `GET /api/v1/workspaces/{ws}/agents/{agent_id}/skills?limit=20`
```json
{
  "data": [
    { "binding_id": "b2000000-0000-0000-0000-000000000040",
      "skill": { "id": "5f2a1c00-0000-4a2b-9c3d-000000000010", "name": "发布检查清单",
                 "slug": "release-checklist", "summary": "发布前的标准检查流程",
                 "source_type": "url", "status": "published" },
      "version": "1.3.0", "install_status": "updated_available",
      "enabled": true, "auto_trigger": true, "priority": 120 }
  ],
  "next_cursor": "eyJvZmZzZXQiOjIwfQ=="
}
```

**回滚版本** `POST /api/v1/workspaces/{ws}/skill-installations/{id}/rollback`
```json
// Request
{ "target_version_id": "8d3b0000-0000-0000-0000-000000000019",
  "reason": "1.3.0 引发脚本超时,回滚到 1.2.0" }
// 200 Response
{ "data": { "id": "a1000000-0000-0000-0000-000000000030",
  "skill_version_id": "8d3b0000-0000-0000-0000-000000000019",
  "previous_version_id": "8d3b0000-0000-0000-0000-000000000020",
  "install_status": "installed", "updated_at": "2026-07-24T13:00:00Z" } }
```

### 3.3 错误码表

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 请求体/参数校验失败(含清单 JSON Schema 校验失败) |
| 401 | `unauthorized` | 缺少或无效 Bearer token |
| 403 | `forbidden` | 无权限(如非管理员/安全负责人审批第三方脚本) |
| 404 | `not_found` | 资源不存在或已删除 |
| 409 | `conflict` | 唯一约束冲突(slug 重复 / 重复安装 / 重复绑定) |
| 409 | `version_conflict` | 版本号已存在(SemVer 唯一) |
| 422 | `manifest_invalid` | 清单结构合法但语义非法(缺指令正文、未知 runtime) |
| 422 | `approval_required` | 第三方技能含脚本但未审批 |
| 422 | `capability_not_declared` | 授予了未在 `required_capabilities` 声明的权限 |
| 423 | `locked` | 技能处于 draft / disabled 不可安装 |
| 429 | `rate_limited` | 触发限流 |
| 502 | `source_unreachable` | 导入时来源地址不可达 |
| 500 | `internal_error` | 服务端错误 |

### 3.4 分页 / 鉴权 / 限流

- **分页**:游标分页 `?cursor=<opaque>&limit=<1..100>`(默认 `limit=20`),响应 `{"data":[...],"next_cursor"}`;游标内部为 `(created_at, id)` keyset 编码。
- **鉴权**:所有端点要求 Bearer token;审批 / 安装 / 绑定类操作要求 workspace `admin` 或具备 `skill:manage` 权限;第三方脚本审批要求安全负责人角色。校验链路与 member.md / workspace.md 一致(解析 token → principal → workspace 成员资格与角色)。
- **限流**:`import`、`marketplace` 拉取类端点单独限流(防止对来源造成压力);写端点按 principal 限流(见 auth.md),超限返回 429 + `Retry-After`。

### 3.5 WebSocket 实时事件

连接 `/ws`(握手鉴权见 auth.md),订阅频道 `workspace:{ws}:skills`。**实时契约以 README §6.7 为唯一权威**:事件命名 `<entity>.<action>`,携带**频道内**单调递增 `seq`(业务事务内自 `realtime_channels.last_seq` 分配),断线凭 `resume_from=<last_seq+1>` 从 `realtime_events` 重放,游标过旧下发 `resync_required`;Redis 仅做 fan-out。

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `skill_import.progress` | 导入任务阶段推进 | `task_id`, `stage`, `status`, `percent` |
| `skill.changed` | 安装/绑定/版本切换/启停完成 | `skill_id`, `installation_id`, `change_type` |
| `skill.update_available` | 检测到来源新版本 | `skill_id`, `installation_id`, `new_version` |
| `skill.approval_required` | 第三方技能待审批 | `skill_id`, `task_id` |

**降级方案**:未连接 WebSocket 时,导入进度退化为轮询 `GET .../skills/import/{task_id}`(3~5s);列表/卡片刷新退化为 30s 轮询。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
技能库(/skills)
   ├── 顶部:[搜索 q] [来源▾] [状态▾]   [+ 新建] [⇩ 导入] [浏览技能市场 →]
   ├── 卡片网格:名称 / 摘要 / 来源标识 / 当前版本 / 安装状态 / 生命周期状态 / 操作
   └── [加载更多 next_cursor]
技能详情页(/skills/{id})
   ├── 左侧 Tab:[概览][版本历史][脚本][资料][触发条件]
   └── 右侧:指令正文渲染 + 脚本列表 + 引用资料 + 所需权限 + 安装/绑定操作区
导入向导(模态):① 选择来源 → ② 预览校验(含脚本审批)→ ③ 安装
agent 配置页 → [技能] Tab:已绑定技能列表(+ 从库中绑定 / 解绑 / 启停)
技能市场页(/marketplace):卡片(评分/下载量/维护方认证)+ [预览][导入]
```

### 4.2 关键组件

- **技能卡片**:来源标识带信任徽标(`builtin` 盾形 / `user` / `marketplace` / `url` ⚠);含脚本的技能加"⚠ 含脚本"角标;`updated_available` 显示"↻ 有更新"。
- **导入向导预览页**:把"指令正文 + 脚本 + 权限"同屏呈现;脚本默认折叠,但**含脚本时强制展开并要求逐项确认**;网络/写文件等高危代码高亮;权限授予默认最小化(建议拒绝项预置不勾选)。
- **版本历史子页**:表格列 版本 / 状态(●当前)/ 变更说明 / 操作([查看] [回滚到此版]);历史版本永不删除,回滚始终可用。
- **agent 绑定区**:每行 启用复选 + 名称 + 版本 + 自动触发开关 + 优先级 + [解绑];底部提示"标记 ⚠ 的技能含脚本,执行受沙箱与已授予权限约束"。
- **市场页**:含脚本的第三方技能在导入入口提示"需人工审批"。

### 4.3 关键交互流程

**从市场导入 → 安装 → 绑定 → 自动触发**:市场选中 → 导入向导(① 选择来源 → ② 预览校验,含脚本强制人工审阅脚本与权限 → ③ 审批安装,默认 workspace 级、`auto_update=false`)→ agent 配置页绑定(`auto_trigger=on`,`priority` 可调)→ agent 接到命中关键词的任务 → 平台匹配注入指令正文 → agent 执行。

**版本更新与回滚**:检测到来源新版本 → `install_status=updated_available` → 通知管理员(inbox + 角标)→ 详情页看变更日志与 diff → [立即更新](PATCH 安装记录)或 [稍后];若新版引发问题 → 版本历史选旧版 → [回滚]。绑定记录可独立指向某版本,支持灰度(部分 agent 用新版,其余留旧版)。

### 4.4 状态流转

**技能定义级**:
```
draft ──校验+审批通过──► published ──标记弃用──► deprecated(仍可运行,不再推荐)
published ──停用──► disabled(暂停所有注入)──恢复──► published
deprecated ──停用──► disabled;deprecated / disabled ──软删除──► (deleted_at 置位)
```

**安装级**:
```
installed ──检测到新版本──► updated_available ──升级完成──► installed
installed / updated_available ──安装级停用──► disabled ──恢复──► installed
installed ──卸载──► (deleted_at 置位)
```

> 升级默认需显式确认;`auto_update=true` 时仅自动跟进**纯指令文案/资料变更且所有脚本 `content_hash` 不变**的非破坏性 PATCH 版本;**含脚本的技能,新版本只要任一脚本 `content_hash` 发生变化,无论 SemVer 级别(MAJOR/MINOR/PATCH)一律重新进入人工审批**(否则返回 422 `approval_required`),杜绝以 PATCH 版本号绕过审核闸门。绑定级停用保留绑定但对该 agent 暂停注入。三档停用均为软状态,可一键熔断后随时恢复。

### 4.5 自动触发机制(基于任务上下文的匹配策略)

匹配在任务进入 agent 处理前执行:

1. **收集候选**:取该 agent 已绑定且 `enabled=true` 且 `auto_trigger=true` 的技能;
2. **多策略打分**:关键词命中(触发关键词 ∩ 任务标题/描述 × `weight`)+ 语义相似度(任务文本 vs 技能摘要/触发描述)+ 标签匹配(任务标签 ∩ 技能标签);
3. **排序与裁剪**:按总分 × `priority` 排序,取 Top-N(避免上下文过载);
4. **互斥与冲突**:**v0.1 以「每技能去重」(per-skill) 为互斥契约**——同一技能仅保留最高分绑定版本;数据模型暂不承载 `mutex_group` 列,跨技能互斥组作为后续增量(若需要,补 `mutex_group` 列后此处升级为「同互斥组只保留最高分者」)。该降级已在验收中确认为 spec↔model 对齐的合法选项;
5. **注入**:把命中技能指令正文作为**可信 SOP** 拼入 agent 上下文(区别于 §6.15 不可信 issue 上下文),并**记录"本次注入了哪些技能"**(含 `matched_by` 证据,落 `config_snapshot.injected_skills` 供审计,可解释、可审计)。

> **匹配实现注记**:候选绑定与版本一次查询取回,其触发器以第二条查询(`IN` 版本集合)批量取回——**无逐候选 N+1**;关键词预筛走 §2.8 的 `idx_trigger_keyword`(GIN,`to_tsvector('simple', pattern) @@ <任务词位 OR 查询>`),最终命中以**词位相等**为准(故 `deploy` 不会误命中 `undeployable`),语义相似度策略保留位(v0.1 不打分,§1.3)。

兜底:用户可在任务里显式指定技能,显式指定的技能强制注入且不参与裁剪。

### 4.6 实时性与通知

| 触发 | 通知对象 | 渠道 |
|------|----------|------|
| 有可用更新(`updated_available`) | workspace 管理员 / 安装者 | inbox + 列表角标 |
| 第三方技能待审批 | 安全负责人 / 管理员 | inbox + 待办 |
| 导入失败(来源不可达 / 校验失败) | 发起人 | inbox |
| 技能被停用 / 弃用 | 已绑定 agent 的拥有者 | inbox |
| 脚本执行异常 / 越权被拦截 | 安全负责人 | inbox + 告警 |

---

## 5. 验收标准

### 5.1 功能性

- [ ] 「定义—版本—安装—绑定」四层独立建模;版本不可变,`published` 后任何修改必须新建版本(`uq_skill_versions` 保证版本号唯一)。
- [ ] 同 workspace 内 slug 唯一(部分唯一索引),重复创建返回 409 `conflict`。
- [ ] 导入流程为异步多阶段(解析→校验→沙箱预览→审批→安装),进度可查询;校验失败返回 422 `manifest_invalid`,来源不可达返回 502 `source_unreachable`。
- [ ] workspace 级安装可被多个 agent 绑定;agent 级安装必须携带 `agent_id`(否则 400);同作用域重复安装返回 409。
- [ ] 绑定可独立指向某历史版本,支持灰度;一个 agent 对同一安装只绑一次(`uq_agent_skills`)。
- [ ] 升级默认不自动覆盖,需显式确认;`auto_update=true` 仅跟非破坏性 PATCH;回滚把安装当前版本指针指向任意历史版本,历史版本永不删除。
- [ ] 自动触发"可解释、可裁剪、可关闭":返回本次注入的技能清单;Top-N 裁剪;绑定级可一键关闭 `auto_trigger`;显式指定技能强制注入。
- [ ] 三档停用(定义/安装/绑定)为软状态,停用即停止注入,可随时恢复。
- [ ] **入队版本快照(README §6.11)**:任务入队时把该 agent 当时绑定的技能版本冻结进 `task_executions.config_snapshot.skill_versions`(`{skill_id: skill_version_id}`);入队后变更绑定 / 切换安装版本 / 回滚 / 灰度**均不影响在途执行**,仅对后续入队生效;在途执行恒运行其入队快照里的版本,可复现、可审计。
- [ ] **多租户复合 FK(README §6.2 / §9 T1)**:`skills` / `skill_versions` / `skill_sources` / `skill_installations` 各建 `UNIQUE(workspace_id, id)`;`created_by`/`installed_by` → `members(workspace_id,id)`、`agent_id` → `agents(workspace_id,id)`、`skill_id`/`source_id`/`skill_version_id`/`skill_installation_id` 均为复合 FK;构造跨 workspace 的复合 FK 插入被数据库约束拒绝(A 区凭证访问 B 区 skill → 403/404)。
- [ ] **同 skill 版本约束(README §6.2 第 7 条 / §9 T1)**:`skill_versions` 建重叠唯一键 `UNIQUE(workspace_id, skill_id, id)`、`skill_installations` 建 `UNIQUE(workspace_id, id, skill_id)`;`skills.current_version_id`、`skill_installations.skill_version_id`、`agent_skills` 的 installation/version 均以重叠复合 FK 引用,在数据库层保证版本属于同一 skill——**`current_version_id` 指向别 skill 的版本、安装别 skill 的版本、绑定与安装不同 skill 的版本,均在 INSERT 被重叠复合 FK 拒绝**;`skills.current_version_id` 删除时仅置空该列(PG16 列级 SET NULL,`workspace_id` 不动)。
- [ ] **存储层无 `*_type`/`*_kind` 判别列**:人类/agent 判别一律 JOIN `members.member_type`,API 响应中的 `member_type` 为服务端计算快照(README §6.1)。

### 5.2 性能

- [ ] 技能库列表(万级)P95 < 200ms(命中 `idx_skill_workspace_status`);标签搜索走 GIN 索引无全表扫描。
- [ ] 自动触发匹配(单 agent 候选技能百级)P95 < 150ms,关键词匹配走全文索引。
- [ ] 游标分页在百万级安装/绑定行下保持稳定(无 OFFSET 深翻页)。

### 5.3 安全

- [ ] **来源信任分级**:`builtin > user > marketplace > url`;`marketplace`/`url` 含脚本的技能首次安装**强制人工逐一审阅**脚本与权限,未审批安装返回 422 `approval_required`。
- [ ] **权限最小化**:`granted_capabilities ⊆ required_capabilities`,授予未声明权限返回 422 `capability_not_declared`;脚本只能拿到被授予的权限。
- [ ] **声明层与调度层严格分离(R3)**:`required_capabilities`/`granted_capabilities` 的混合格式(字符串 / `{capability,permission,enabled?}` 对象)仅在声明与授权端点流通;入队先过滤授权条目 `enabled=false`,再经归一算法(README §6.4、agent.md §3.3)派生 `task_executions.required_capabilities`(**纯字符串数组**,schema CHECK 拒绝对象元素)与 `config_snapshot.capability_grants`(严格 `[{capability,permission}]` 对象数组);混用声明直接写入调度字段在集成测试 T28 被判定失败。
- [ ] **沙箱执行边界**:脚本默认无网络、无任意写、无特权;越权调用被运行时拦截并告警(衔接 runtime.md)。
- [ ] **凭据安全**:`skill_sources.auth_ref` 仅存 secret manager 引用键,绝不存明文;响应/日志不回显凭据。
- [ ] **SSRF 防护**:服务端拉取用户提供的技能来源 URI(git 仓库 / URL)时,禁止访问私网地址段(RFC1918 / link-local / 云元数据 `169.254.169.254` 等),仅允许公网地址或配置的主机白名单;来源地址校验失败返回 502 `source_unreachable` 并不暴露内部错误。
- [ ] **一键熔断**:发现风险可立即定义级/安装级/绑定级停用,即时停止注入。
- [ ] 安装/绑定/权限授予/脚本执行均写 auth.md 的 append-only 审计日志;审批/安装类端点强制角色校验,不足返回 403;写端点受限流约束,超限 429。

### 5.4 实时

- [ ] 导入进度经 `skill_import.progress` 实时推送(带 `seq`),无连接时降级轮询;阶段与进度条实时更新。
- [ ] 安装/绑定/版本切换/启停完成后广播 `skill.changed`,在线成员 1s 内收到,列表/卡片局部刷新。
- [ ] 检测到新版本推送 `skill.update_available` 并落 inbox。
- [ ] 客户端断线重连凭 `seq` 重放,无丢失无重复。
