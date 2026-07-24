# Mesh 整体项目 Spec

> 状态:Draft v1 | 本文件是 Mesh 所有开发的唯一入口:先读本文建立全局认知,再按「功能 Spec 索引」进入具体模块。各功能 Spec(`features/*.md`)是各模块实现的唯一依据;`../research/*.md` 是设计调研原始记录,仅供溯源,不作为实现依据。

---

## 1. 产品定位

**Mesh 是一个 AI 原生的团队工作区**:AI agent 不是侧边栏里的聊天机器人,而是与人类完全对称的**一等队友**——出现在成员名册里,被分派 issue、在看板上拖拽、在讨论区发评论、修改状态、领取任务并运行代码,与人类成员遵循同一套协作规则。

### 1.1 核心场景

| 场景 | 描述 |
| --- | --- |
| 分派即开工 | 把 issue 的 assignee 设为某个 agent,agent 自动领取任务、checkout 代码仓库、执行、回传进展与结果评论 |
| 讨论即协作 | 在 issue 评论区 @ 某个 agent,等同于给它派一次活;agent 回复评论、补充上下文、推进任务 |
| 人机同组 | 把人类与多个 agent 编成小队(squad),由 leader 角色拆解任务、分派给成员(人或 agent),协作时间线全程可见 |
| 无人值守 | autopilot 定时或事件驱动地把工作派给合适的 agent(如"每晚回归巡检""issue 进入 in_review 时自动跑验收") |
| 随时对话 | 与任意 agent 开 chat session 直接对话,流式输出,可携带 issue 上下文 |

### 1.2 设计原则

1. **对称性**:人与 agent 共享同一成员模型、同一套分派/评论/通知机制;凡是为人类设计的工作流,agent 都能以同样方式参与。
2. **透明与可观测**:agent 的每一次运行都有可追溯的任务记录、实时日志流与产物;看板上能一眼分辨"谁(人或 agent)在做什么"。
3. **人类监督**:关键编排节点可配置人工确认闸门(如小队计划审批、autopilot 高风险动作确认);人类随时可暂停/取消 agent 运行。
4. **防失控优先**:所有 agent 触发路径默认带护栏——频率上限、去重、链深度限制、全局 kill switch;宁可不跑,不可失控互推。
5. **Spec 驱动**:代码是 Spec 的实现;任何行为分歧以 `features/*.md` 为准。

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        客户端(Web SPA)                           │
│   看板 / Issue / 收件箱 / 成员 / Agent 管理 / 聊天 / 设置           │
└───────────────┬───────────────────────────┬──────────────────────┘
                │ REST /api/v1 (JSON)       │ WebSocket /ws(实时 + 日志流)
┌───────────────▼───────────────────────────▼──────────────────────┐
│                     API 层(FastAPI)                              │
│   认证中间件(Bearer/JWT)→ 工作区成员资格 → RBAC → 限流            │
├──────────────────────────────────────────────────────────────────┤
│                        领域服务层                                 │
│  workspace/member │ project/issue │ comment/inbox │ attachment    │
│  agent 编排(统一任务入口:分派事件 + @提及 + autopilot 共用)      │
│  autopilot 调度器(cron + 事件订阅) │ 通知管线 │ 审计               │
├──────────────────────────────────────────────────────────────────┤
│   PostgreSQL(主存储 + 任务队列:FOR UPDATE SKIP LOCKED)           │
│   Redis(缓存 / 限流 / 在线状态)    对象存储(附件,S3 兼容)        │
└───────────────▲──────────────────────────────────────────────────┘
                │ runtime 协议(注册 / 心跳 / 领取 / 上报,API token)
┌───────────────┴──────────────────────────────────────────────────┐
│              Agent Runtime 集群(平台托管 + 用户自托管)            │
│   领取任务 → checkout 仓库专属分支 → 沙箱执行 → 流式日志 → 回传产物 │
│   底层模型经统一适配层接入(不绑定特定模型供应商)                   │
└──────────────────────────────────────────────────────────────────┘
```

### 2.1 核心架构决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 多租户 | 软多租户:共享库 + 所有业务表带 `workspace_id` 列并索引 | 成本与隔离平衡;鉴权层统一校验成员资格 |
| 成员模型 | 统一名册 `members`(`member_type=human\|agent`,多态指向 `users`/`agents` 子表) | 让 assignee、评论作者、@提及、小队成员、附件上传者全部引用同一个 id,人机天然对称 |
| 任务队列 | 不引入外部 MQ;PostgreSQL `FOR UPDATE SKIP LOCKED` + 租约 + 序号 | 少一个基础设施,可靠领取与失联自愈由数据库事务保证 |
| Agent 触发 | 「被分派」「被 @ 提及」「autopilot 派单」三条路径汇入**同一任务入口** | 触发语义统一,护栏与审计只需建一处 |
| 附件传输 | 签名 URL 客户端直传对象存储,字节流不经应用服务器 | 应用层无带宽瓶颈;两阶段状态机清理孤儿对象 |
| 实时 | WebSocket 频道订阅 + 单调递增 `seq` + 断线重放;降级为轮询 | 增量合并而非整页刷新;任何客户端都能获得一致视图 |
| 状态建模 | issue 双层状态:`category`(稳定语义,用于聚合/看板)+ `status`(可自定义,用于展示) | 自定义状态不破坏统计与看板列的稳定性 |

---

## 3. 技术栈

### 3.1 后端(Python)

| 组件 | 选型 | 用途 |
| --- | --- | --- |
| 语言 | Python 3.12+ | 全部服务端代码 |
| Web 框架 | FastAPI + Pydantic v2 | REST API、请求/响应模型、自动校验 |
| ORM | SQLAlchemy 2.x(声明式) | 数据模型与查询 |
| 迁移 | Alembic | schema 版本化 |
| 数据库 | PostgreSQL 15+ | 主存储 + 任务队列(SKIP LOCKED) |
| 缓存/限流 | Redis | 缓存、令牌桶限流、在线状态、事件缓冲 |
| 对象存储 | S3 兼容存储 | 附件,签名 URL 直传/下载 |
| 实时 | WebSocket(FastAPI 原生),SSE 兜底 | 增量事件、agent 日志流、聊天流式输出 |
| 密码学 | argon2id(密码)、SHA-256(token 存储) | 认证安全基线 |
| 服务 | uvicorn(多 worker) | 运行入口 |

### 3.2 前端

Spec 不约束前端框架;要求:SPA、乐观更新 + 服务端版本校验、WebSocket 增量合并、离线降级轮询。

### 3.3 Agent 侧

底层大语言模型经统一适配层接入,可替换不同模型供应商;runtime 与平台之间只依赖 runtime 协议(REST + API token),允许用户把自有机器/容器注册为 runtime。

---

## 4. 模块总览

Mesh 由 **15 个功能模块**组成,分四层:

### 4.1 基础层

| 模块 | 定位 |
| --- | --- |
| **workspace(工作区)** | 多租户隔离根:工作区设置、全局唯一 slug、邀请机制 |
| **member(成员)** | 统一成员名册:人类与 agent 同册,角色(owner/admin/member/guest)、停用/启用 |
| **auth(认证与授权)** | 注册登录、第三方 OAuth、会话、API token(供 CLI 与 runtime)、RBAC、审计、限流 |

### 4.2 项目管理层

| 模块 | 定位 |
| --- | --- |
| **project(项目)** | 工作聚合层:项目状态/健康度留痕、里程碑、迭代周期、前缀与编号计数器 |
| **issue(工作项)** | 全系统核心实体:双层状态、`<前缀>-<号>` 编号、父子树、依赖图、批量操作 |
| **kanban(看板与视图)** | issue 的可保存"投影":列=分组、拖拽、筛选/排序、WIP 限制、实时增量合并 |
| **label-property(标签与自定义属性)** | 轻量标签 + 带类型的自定义字段(文本/数字/日期/枚举/多选/成员等) |

### 4.3 协作层

| 模块 | 定位 |
| --- | --- |
| **comment-inbox(评论与收件箱)** | 线程化评论、@提及(提及 agent = 入队一次运行)、通知中心与未读管理 |
| **attachment(附件)** | 签名 URL 直传、预览/缩略图、私有签名下载;人与 agent 共用模型 |
| **chat-session(与 agent 聊天)** | 与 agent 的实时多轮对话:流式输出、中断/重生成、可关联 issue 上下文 |

### 4.4 AI 智能体层

| 模块 | 定位 |
| --- | --- |
| **agent(Agent 管理)** | agent 作为一等成员:配置(模型/指令/技能绑定)、可见性、分派即触发 |
| **runtime(运行时)** | agent 执行环境:注册/心跳、任务领取(SKIP LOCKED+租约)、日志流、凭证安全、仓库 checkout |
| **skill(技能)** | 可安装的结构化指令包:定义—版本—安装—绑定四层解耦,沙箱与信任分级 |
| **squad(小队)** | 人机编队协作:角色(leader/member)、拆解树 + 依赖 DAG + 批次、计划审批闸门 |
| **autopilot(自动化)** | 定时(cron)与事件驱动触发,把任务派给 agent;内置防失控护栏 |

---

## 5. 功能 Spec 索引

每份功能 Spec 均包含五部分:**功能描述 / 数据模型 / 接口设计 / UI/UX 设计 / 验收标准**。

| # | 功能 Spec | 层 | 关键内容 |
| --- | --- | --- | --- |
| 1 | [workspace.md](features/workspace.md) | 基础 | 软多租户、slug 重定向、邀请状态机 |
| 2 | [member.md](features/member.md) | 基础 | 统一名册 `members`(human\|agent)、角色、资产转派 |
| 3 | [auth.md](features/auth.md) | 基础 | argon2id、JWT+refresh、API token 哈希存储、RBAC 矩阵 |
| 4 | [project.md](features/project.md) | 项目管理 | 健康度留痕、里程碑 vs 周期、编号计数器契约 |
| 5 | [issue.md](features/issue.md) | 项目管理 | 双层状态、原子编号、父子树与依赖图、批量操作 |
| 6 | [kanban.md](features/kanban.md) | 项目管理 | 视图=JSONB 投影、拖拽乐观更新、WIP、增量合并 |
| 7 | [label-property.md](features/label-property.md) | 项目管理 | 标签多对多、自定义字段按类型分列+JSONB |
| 8 | [comment-inbox.md](features/comment-inbox.md) | 协作 | 单层折叠线程、通知 payload 快照、@agent 触发与回环抑制 |
| 9 | [attachment.md](features/attachment.md) | 协作 | 签名直传三阶段、两阶段状态机、私有签名下载 |
| 10 | [chat-session.md](features/chat-session.md) | 协作 | 对话统一抽象、流式双通道、幂等中断、循环防护 |
| 11 | [agent.md](features/agent.md) | 智能体 | agent 身份与配置版本快照、分派即触发主链路 |
| 12 | [runtime.md](features/runtime.md) | 智能体 | 注册—心跳—领取—上报契约、租约自愈、凭证不落盘 |
| 13 | [skill.md](features/skill.md) | 智能体 | 四层解耦、不可变版本、沙箱与信任分级 |
| 14 | [squad.md](features/squad.md) | 智能体 | 编排层与内容层解耦、DAG + 批次、计划审批 |
| 15 | [autopilot.md](features/autopilot.md) | 智能体 | 触发器+条件+动作、护栏默认开启、kill switch |

调研原始记录见 [`../research/`](../research/)(每模块一份,功能 / 数据模型 / 接口 / UI / UX 四维度)。

---

## 6. 全局约定(所有模块 Spec 共同遵守)

| 约定 | 内容 |
| --- | --- |
| 数据库 | PostgreSQL;表名 snake_case 复数;主键 `id UUID`(`gen_random_uuid()`);每表 `created_at`/`updated_at`(`TIMESTAMPTZ`);按需软删除(`deleted_at` 或状态字段) |
| 成员引用 | `issue.assignee_id`、评论 `author_id`、@提及目标、小队成员、附件 `uploader_id` 一律引用 `members.id`(`member_type=human\|agent`) |
| API | 前缀 `/api/v1`;JSON;游标分页 `?limit=&cursor=` → `{"data": [...], "next_cursor": ...}`;时间一律 RFC3339 UTC |
| 鉴权 | `Authorization: Bearer <token>`(会话 JWT 或 API token);中间件链:解析 → 工作区成员资格 → RBAC → 限流 |
| 错误信封 | `{"error": {"code": "...", "message": "...", "details": {...}}}`;HTTP 语义化:400/401/403/404/409/422/429 |
| 实时 | WebSocket `/ws`,频道订阅 + 单调递增 `seq` + 断线重放;事件命名 `<entity>.<action>`(如 `issue.updated`);降级为轮询 |
| 长任务状态机 | `queued → claimed → running → completed / failed / cancelled`(agent 运行、autopilot 运行共用语义) |
| 触发护栏 | 所有 agent 触发路径默认:频率上限、去重、链深度限制、全局 kill switch |

## 7. 核心跨模块流程

**「分派给 agent」端到端**(贯穿 member / issue / agent / runtime / comment-inbox):

```
人类把 issue.assignee 改为 agent
  → issue 服务写库并发出 issue.assigned 事件
  → agent 编排入口(与 @提及、autopilot 共用)校验护栏后创建任务(queued)
  → 匹配可用 runtime → runtime 以 FOR UPDATE SKIP LOCKED 领取(claimed,一次性下发凭证)
  → runtime checkout 仓库专属分支,沙箱执行(running,日志经 WebSocket 流式回传)
  → 完成:agent 以成员身份在 issue 发结果评论、改状态(completed)
  → 失败/超时:租约到期自愈,任务回到队列或标记 failed
```

## 8. 整体验收标准

- [x] 覆盖全部 15 个核心模块,每个模块的功能 Spec 均含 功能 / 数据模型 / 接口 / UI/UX 四个维度(见 §5 索引)。
- [x] 整体项目 Spec 完成并正确 reference 所有功能 Spec(§5 全部为有效相对链接)。
- [x] 每份功能 Spec 含可逐条验证的验收标准,数据模型 + 接口 + UI/UX 齐全,可直接指导开发。
- [x] 全部产出物已提交到 Mesh 仓库主干(`main`)。
- [x] 无任何暴露参考来源的内容(全部文档经品牌词/URL 扫描,仅含占位地址)。

---

*文档版本:Draft v1(2026-07-24)。后续任何 Spec 变更须在对应功能文件内修订并更新本索引。*
