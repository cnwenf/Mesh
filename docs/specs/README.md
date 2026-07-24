# Mesh 整体项目 Spec

> 状态:Draft v2(R1 修订) | 本文件是 Mesh 所有开发的唯一入口:先读本文建立全局认知,再按「功能 Spec 索引」进入具体模块。各功能 Spec(`features/*.md`)是各模块实现的唯一依据;`../research/*.md` 是设计调研原始记录,仅供溯源,不作为实现依据。
>
> **R1 修订说明**:本文件 §2.2–2.3、§6、§7、§9、§10 为**全局唯一权威契约(canonical contracts)**。所有功能 Spec 中的 schema、API 包络、错误码、分页、状态枚举、事件词汇、幂等/重试语义**一律引用本文定义,不再重复定义**;功能 Spec 与本文冲突时以本文为准。

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
3. **人类监督**:关键编排节点可配置人工确认闸门(如小队计划审批、autopilot 高风险动作确认、高风险工具确认);人类随时可暂停/取消 agent 运行。
4. **防失控优先**:所有 agent 触发路径默认带护栏——频率上限、去重、链深度限制、全局 kill switch;宁可不跑,不可失控互推。
5. **Spec 驱动**:代码是 Spec 的实现;任何行为分歧以 `features/*.md` 为准,跨模块共性以本文为准。

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
│   PostgreSQL 16+(主存储 + outbox + 任务队列:FOR UPDATE SKIP LOCKED)│
│   Redis(缓存 / 限流 / 在线状态 / 事件 fan-out,不做持久真源)       │
│   对象存储(附件 / 日志段,S3 兼容)                                 │
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
| 多租户 | 软多租户:共享库 + 所有业务表带 `workspace_id` 列;**跨租户关系以复合外键 + `(workspace_id,id)` UNIQUE 在数据库层强约束**(§6.2);PostgreSQL RLS 作为纵深防御 | 仅靠"查询带 workspace_id"无法兜住 IDOR 与程序遗漏;约束可验证 |
| 成员模型 | 统一名册 `members`(`member_type=human\|agent`,多态指向 `users`/`agents`,见 §6.1);**member.md 为 `members` 表的唯一 owner Spec** | 让 assignee、评论作者、@提及、小队成员、附件上传者全部引用同一个 id,人机天然对称 |
| 任务队列 | **不引入外部 MQ;采用 PostgreSQL outbox / job queue**(`outbox_events` 表 + relay worker,§6.6;`task_executions` + `FOR UPDATE SKIP LOCKED` 领取,§6.4),**不是进程内事件总线** | 少一个基础设施;"业务提交"与"事件/任务入队"同事务原子,可靠领取与失联自愈由数据库事务保证 |
| Agent 触发 | 「被分派」「被 @ 提及」「autopilot 派单」三条路径汇入**同一任务入口**,触发语义以 §6.9 触发矩阵为唯一权威 | 触发语义统一、可测试,护栏与审计只需建一处 |
| 附件传输 | 签名 URL 客户端直传对象存储,字节流不经应用服务器;**隔离区 → 异步扫描/嗅探 → clean 后可见**(attachment.md) | 应用层无带宽瓶颈;安全扫描完成前不开放下载 |
| 实时 | WebSocket 频道订阅 + **频道内**单调递增 `seq` + 持久化 `realtime_events` 重放(§6.7);Redis 仅作 fan-out;降级为轮询 | 增量合并而非整页刷新;重放真源在数据库,不依赖内存缓冲 |
| 状态建模 | issue 双层状态:`category`(稳定语义,用于聚合/看板)+ `status`(可自定义,用于展示) | 自定义状态不破坏统计与看板列的稳定性 |
| 投递语义 | 任务/通知/实时事件统一 **at-least-once**;外部副作用(工具调用/推送/评论/提交)以**稳定幂等键**去重(§6.5) | 分布式下 exactly-once 不可得;幂等键使"至少一次"对客户端表现为"恰好一次" |

### 2.2 后台执行拓扑、故障责任与扩容(权威)

Mesh 服务端由以下**独立可部署单元**组成。起步可合并进程部署(除 realtime gateway 外全部为无状态 worker),但职责、竞争方式与故障语义必须按下表实现,不允许用进程内事件总线替代:

| 单元 | 职责 | 竞争/并发模型 | 故障责任与自愈 | 扩容方式 |
| --- | --- | --- | --- | --- |
| **API(FastAPI/uvicorn)** | REST + 鉴权 + 业务写入;所有业务事务**同事务写 `outbox_events`** | 多 worker 无状态 | 单 worker 崩溃仅丢 in-flight 请求,客户端重试(幂等键兜底) | 水平加 worker/实例 |
| **Outbox relay** | 轮询 `outbox_events(status='pending')` → 分发到对应处理器(通知 fan-out / 执行入队 / 实时事件登记 / autopilot 事件)→ 置 `published` | `FOR UPDATE SKIP LOCKED` 抢占,多副本不重复处理;`UNIQUE(idempotency_key)` 兜底 | 崩溃后未发布事件由其他副本/重启后继续投递(at-least-once);`delivery_attempts` 超限进 `failed` 告警 | 加副本即提高吞吐 |
| **调度 worker(autopilot/scheduler)** | 扫描到期 `autopilots.next_run_at` 与一次性定时,原子抢占创建 run | `FOR UPDATE SKIP LOCKED` + `next_run_at` 前移,多副本不重复触发 | 崩溃后下一扫描周期补发(misfire_policy 决定补发策略) | 加副本;按 workspace 哈希分片(规模化时) |
| **租约 reaper** | 扫描 `execution_attempts` 租约过期/心跳失联 → 回收 attempt(requeue 新 attempt 或转 failed);扫描过期 approval(§6.10) | 单 leader(数据库 advisory lock)或多副本 SKIP LOCKED 分行 | reaper 全挂 → 任务卡 claimed;由监控告警;恢复后批量回收 | 一般单副本足够;分行扫描可多副本 |
| **通知 fan-out worker** | 消费 outbox 中通知类事件 → 写 `notifications` + 邮件摘要队列 + 实时推送登记 | SKIP LOCKED;`notification_delivery.UNIQUE(notification_id,channel)` 幂等 | 崩溃后由 outbox 重投;邮件失败重试 | 加副本 |
| **附件处理 worker** | 隔离区对象的 MIME 嗅探(读 magic bytes)、SHA-256 校验、病毒扫描、缩略图 | SKIP LOCKED 扫 `attachments(scan_status='pending')` | 崩溃后重扫;`scan_status='error'` 重试上限 | 加副本 |
| **实时网关(WebSocket)** | 客户端长连接;订阅频道时**逐资源授权**;从 `realtime_events` 重放 + Redis fan-out 实时推送 | 每连接单线程;多网关经 Redis pub/sub 广播 | 网关崩溃 → 客户端重连,凭 `resume_from` 从 `realtime_events` 补齐;游标过旧 → `resync_required` | 水平加网关,Redis pub/sub 联通 |

**部署形态**:起步 = 1 个 API 进程(含 uvicorn 多 worker)+ 1 个 worker 进程(运行 relay/scheduler/reaper/fan-out/附件处理,各为独立 asyncio 任务)+ 1 个 realtime 网关进程;三者可容器化独立伸缩。worker 各任务之间以 SKIP LOCKED 解耦,**任何单一任务循环卡死不得阻塞其他任务**(看门狗 + 独立取消域)。

### 2.3 独立 MQ 演进阈值(权威)

PostgreSQL outbox/job queue 为起步方案,达到以下**任一亮级**时启动独立消息队列(如 NATS/Kafka/RabbitMQ)迁移评估:

| 信号 | 迁移触发量级 |
| --- | --- |
| outbox 待处理深度 | 持续 > 10 万行 pending 或 P95 relay 延迟 > 5s |
| 数据库写放大 | outbox/队列相关写入占主库写 IOPS > 30% |
| 单库 IOPS | 主库持续 > 70% IOPS 上限且队列为主因 |
| 跨地域 | 出现跨地域 runtime/多活需求(单库 PostgreSQL 无法低延迟服务) |

迁移时保持 §6.6/§6.7 的契约不变(事件 schema、幂等键、seq 语义),仅替换传输层。

---

## 3. 技术栈

### 3.1 后端(Python)

| 组件 | 选型 | 用途 |
| --- | --- | --- |
| 语言 | Python 3.12+ | 全部服务端代码 |
| Web 框架 | FastAPI + Pydantic v2 | REST API、请求/响应模型、自动校验 |
| ORM | SQLAlchemy 2.x(声明式) | 数据模型与查询 |
| 迁移 | Alembic | schema 版本化 |
| 数据库 | **PostgreSQL 16+**(主存储 + outbox + 任务队列 SKIP LOCKED) | 所有 DDL 以 16 为基准可执行 |
| 缓存/限流/fan-out | Redis | 缓存、令牌桶限流、在线状态、实时事件 fan-out(**非持久真源**) |
| 对象存储 | S3 兼容存储 | 附件、日志段;签名 URL 直传/下载 |
| 实时 | WebSocket(FastAPI 原生);**SSE 仅用于"POST 创建 → GET 流"模式**(§6.8) | 增量事件、agent 日志流、聊天流式输出 |
| 密码学 | argon2id(密码)、SHA-256(token 存储) | 认证安全基线 |
| 服务 | uvicorn(多 worker) | 运行入口 |

### 3.2 前端

Spec 不约束前端框架;要求:SPA、乐观更新 + 服务端版本校验、WebSocket 增量合并、离线降级轮询;无障碍与状态基线见 §6.12。

### 3.3 Agent 侧

底层大语言模型经统一适配层接入,可替换不同模型供应商;runtime 与平台之间只依赖 runtime 协议(REST + API token),允许用户把自有机器/容器注册为 runtime。安装与激活安全见 runtime.md(签名发布包 + 校验,激活码不进命令行参数)。

---

## 4. 模块总览

Mesh 由 **15 个功能模块**组成,分四层:

### 4.1 基础层

| 模块 | 定位 |
| --- | --- |
| **workspace(工作区)** | 多租户隔离根:工作区设置、全局唯一 slug、邀请机制 |
| **member(成员)** | 统一成员名册:人类与 agent 同册,角色(owner/admin/member/guest)、停用/启用。**owns `members` 表(唯一权威)** |
| **auth(认证与授权)** | 注册登录、第三方 OAuth、会话、API token(供 CLI 与 runtime)、RBAC、审计、限流。owns `users`/`sessions`/`api_tokens`/`audit_logs` |

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
| **comment-inbox(评论与收件箱)** | **owns `comments`/`comment_mentions`/`comment_reactions`/`issue_subscriptions`/`notifications`/`notification_preferences`/`notification_delivery`(唯一权威)**;线程化评论、@提及(提及 agent = 入队一次运行)、通知中心与未读管理 |
| **attachment(附件)** | **owns `attachments`/`attachment_links`(唯一权威,人与 agent 共用,聊天/评论/issue 附件全部经此)**;签名 URL 直传、隔离区扫描、私有签名下载 |
| **chat-session(与 agent 聊天)** | owns `chat_sessions`/`chat_messages`;与 agent 的实时多轮对话:流式输出、中断/重生成、可关联 issue 上下文;评论/提及/通知/附件**引用** comment-inbox 与 attachment,不重复建表 |

### 4.4 AI 智能体层

| 模块 | 定位 |
| --- | --- |
| **agent(Agent 管理)** | agent 作为一等成员:配置(模型/指令/技能绑定)、可见性、分派即触发。owns `agents` 及其绑定/版本表 |
| **runtime(运行时)** | agent 执行环境:注册/心跳、任务领取(SKIP LOCKED+租约)、日志流、凭证安全、仓库 checkout。**owns `runtimes`/`task_executions`/`execution_attempts` 等——`task_executions` 是全系统运行的唯一真源实体名** |
| **skill(技能)** | 可安装的结构化指令包:定义—版本—安装—绑定四层解耦,沙箱与信任分级 |
| **squad(小队)** | 人机编队协作:角色(leader/member)、拆解树 + 依赖 DAG + 批次、计划审批闸门(经 §6.10 统一 approval) |
| **autopilot(自动化)** | 定时(cron)与事件驱动触发,把任务派给 agent;内置防失控护栏;审批经 §6.10 统一 approval |

---

## 5. 功能 Spec 索引

每份功能 Spec 均包含五部分:**功能描述 / 数据模型 / 接口设计 / UI/UX 设计 / 验收标准**。

| # | 功能 Spec | 层 | 关键内容 |
| --- | --- | --- | --- |
| 1 | [workspace.md](features/workspace.md) | 基础 | 软多租户、slug 重定向、邀请链接生命周期与 redemption 分离 |
| 2 | [member.md](features/member.md) | 基础 | 统一名册 `members`(human\|agent,`user_id`/`agent_id` 多态)、角色、资产转派 |
| 3 | [auth.md](features/auth.md) | 基础 | argon2id、JWT+refresh、API token 哈希存储(owner 统一为 member)、RBAC 矩阵 |
| 4 | [project.md](features/project.md) | 项目管理 | 健康度留痕、里程碑 vs 周期、编号计数器、前缀永久保留 |
| 5 | [issue.md](features/issue.md) | 项目管理 | 双层状态、工作区级编号唯一、父子树与依赖图(advisory lock 防并发成环)、批量操作 |
| 6 | [kanban.md](features/kanban.md) | 项目管理 | 视图=JSONB 投影、`view_issue_positions` 每视图排序、原子 move + WIP、整体游标 |
| 7 | [label-property.md](features/label-property.md) | 项目管理 | 标签多对多、自定义字段按类型分列+JSONB、`(field_def_id,value_*)` 复合索引 |
| 8 | [comment-inbox.md](features/comment-inbox.md) | 协作 | 单层折叠线程、通知 payload 快照、@agent 触发矩阵与回环抑制、通知去噪规则 |
| 9 | [attachment.md](features/attachment.md) | 协作 | 签名直传三阶段、隔离区→扫描→clean 状态机、blob 去重独立记录、私有签名下载 |
| 10 | [chat-session.md](features/chat-session.md) | 协作 | 对话抽象、POST 创建 generation → GET SSE 流、幂等中断、评论/附件引用权威 Spec |
| 11 | [agent.md](features/agent.md) | 智能体 | agent 身份与配置版本快照、分派即触发主链路、入队可复现快照 |
| 12 | [runtime.md](features/runtime.md) | 智能体 | 注册—心跳—领取—上报契约、execution/attempt 分层、租约自愈、凭证 fencing、签名安装 |
| 13 | [skill.md](features/skill.md) | 智能体 | 四层解耦、不可变版本、沙箱与信任分级 |
| 14 | [squad.md](features/squad.md) | 智能体 | 编排层与内容层解耦、DAG + 批次、计划审批(统一 approval)、issue 责任主体模型 |
| 15 | [autopilot.md](features/autopilot.md) | 智能体 | 触发器+条件+动作、护栏默认开启、kill switch、审批(统一 approval) |

调研原始记录见 [`../research/`](../research/)(每模块一份,功能 / 数据模型 / 接口 / UI / UX 四维度)。

---

## 6. 全局权威契约(canonical contracts)

> 本章是所有模块 Spec 的**唯一公共定义源**。功能 Spec 只能**引用**本章条目(如"见 README §6.2"),不得重复定义或改写;发现缺口先修订本章。

### 6.1 成员模型与类型判别(唯一权威)

**权威模型:统一名册 `members` + 多态外键 `user_id`/`agent_id`(member.md owns)。**

```
users(auth.md owns,全局登录身份)──1:N──┐
                                        ├──► members(member.md owns,工作区名册)
agents(agent.md owns,AI 身份与配置)──1:N──┘      ▲
                                                 └─ members.id 是全系统统一引用键:
                                                    issues.assignee_id/reporter_id、comments.author_id、
                                                    comment_mentions.mentioned_id、notifications.recipient_id、
                                                    api_tokens.owner_member_id、附件 uploader_id、小队成员……
```

| 规则 | 内容 |
| --- | --- |
| 表结构 | `members(id, workspace_id, member_type CHECK IN('human','agent'), user_id NULL FK→users, agent_id NULL FK→agents, role, status, display_override, joined_at, disabled_at, created_at, updated_at)`;**恰好一个**非空:`CHECK ((member_type='human' AND user_id IS NOT NULL AND agent_id IS NULL) OR (member_type='agent' AND agent_id IS NOT NULL AND user_id IS NULL))`(邀请人由 `workspace_invitations.invited_by` 记录,名册不冗余) |
| 多工作区 | 同一 `users.id` 可在多个工作区各有一条 `members` 行(每区角色独立);**禁止**在 `users` 上放 `member_id UNIQUE` 这类 1:1 反向关联 |
| 子表 | `users`/`agents` **不设** `member_id` 列;关联方向永远是 `members → users/agents` |
| 软终态 | 名册状态 `status ∈ {active,disabled,removed}`(`removed` 为软终态,物理清理按保留期);agent 另有 `agents.lifecycle_status`(active/paused/disabled/archived),停用 agent 时两者联动 |
| 类型冗余 | **存储层禁止**冗余 `*_type`/`*_kind` 判别列(`author_type`、`assignee_type`、`uploader_kind`、`owner_type` 等一律不进表);人类/agent 判别一律 JOIN `members.member_type`。**API 响应**可携带服务端计算出的 `member_type` 快照字段用于免 JOIN 渲染,标注"快照,真源为 members"。若个别高频表确需存储快照,必须用**触发器/生成列**与 `members` 强制一致(防漂移),并在该 Spec 明示 |
| 显示名解析 | 统一顺序:`members.display_override`(非空)→ 人类 `users.display_name`→`users.full_name`;agent `agents.name`。服务端解析,接口返回单一 `display_name` |

### 6.2 多租户同租户约束(唯一权威)

仅"所有查询带 workspace_id"**不够**。所有跨租户关系必须在数据库层可验证:

1. **目标表**:凡可能被跨模块引用的实体表(projects、members、issue_statuses、labels、cycles、milestones、attachments、squads、runtimes、agents、views 等),除 `PK(id)` 外必须建 **`UNIQUE (workspace_id, id)`**(供复合 FK 引用)。
2. **引用方**:凡持有上述引用的表必须**同时存 `workspace_id` 并建复合外键** `FK (workspace_id, <ref>_id) → 目标表 (workspace_id, id)`,使"引用了别的工作区的对象"在 INSERT 时即被拒绝。
3. **成员引用**:`issues.assignee_id` 等对 `members.id` 的引用同理——引用表存 `(workspace_id, assignee_id)` 复合 FK → `members(workspace_id, id)`。
4. **多态逻辑外键**(如 `attachment_links.linked_id` 指向 issues 或 comments):不建物理 FK,但引用行必须存 `workspace_id`,删除一致性由软删除 + 服务层保证,并在集成测试矩阵(§9)覆盖跨租户用例。
5. **纵深防御**:在以上约束之上启用 **PostgreSQL RLS**:业务表按 `workspace_id = current_setting('mesh.workspace_id')::uuid` 建策略,API 事务开始时设置该 GUC;RLS 作为程序遗漏的兜底而非替代复合 FK。

### 6.3 编号、前缀与默认状态约束(唯一权威)

| 项 | 权威规则 |
| --- | --- |
| issue 编号 | 项目内 `number` 由 `projects.issue_seq` 行锁自增;**无项目 issue** 使用工作区级计数器 `workspaces.inbox_issue_seq`(同发行锁自增),前缀为工作区保留前缀(默认 `WS`,可配);`identifier = <前缀> || '-' || number` |
| 编号唯一 | `issues` 上同时建 **`UNIQUE (project_id, number)`**(项目内,仅 project_id 非空行有效)与 **`UNIQUE (workspace_id, identifier)`**(工作区级,兜住无项目 issue 与一切重复),后者为普通唯一索引 |
| 编号不可复用/不可变 | issue 软删除编号永久废弃;`identifier` 一经生成**永不改变**(issue 在项目间迁移不重编号);项目前缀 `key` **永久保留**——`projects` 上建 **`UNIQUE (workspace_id, key)`(不带 `WHERE deleted_at IS NULL`)**,软删除/归档项目后前缀不可复用;项目不得跨工作区迁移;删除项目时其 issue 的 `identifier` 保持不变(`issues.project_id` ON DELETE SET NULL,编号随 issue 走) |
| 默认状态唯一 | "每作用域唯一默认状态"用**部分表达式唯一索引**(COALESCE 表达式不能写进表级 UNIQUE 约束):`CREATE UNIQUE INDEX uq_issue_statuses_default ON issue_statuses (workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000')) WHERE is_default;` |
| 至少一个默认 | 由事务保证:任何"取消某状态默认"的写操作必须与"设置新默认"在同一事务;工作区/项目创建事务内播种默认状态集;服务层自检校验每作用域恰有一个默认,缺失即报警并修复 |
| 其他 NULL 作用域唯一 | labels/custom_field_defs/views 等"工作区级 OR 项目级"命名唯一,一律用上同款**部分表达式唯一索引**写法,不得写表级 `UNIQUE (ws, COALESCE(project_id,…), name)` |

### 6.4 任务队列:execution / attempt 分层(唯一权威)

**逻辑执行 `task_executions` 与物理尝试 `execution_attempts` 分离**(runtime.md owns 两表):

| 表 | 语义 | 关键字段 |
| --- | --- | --- |
| `task_executions` | 一次**逻辑执行**(由分派/@提及/autopilot 触发产生,生命周期内只有一行) | `id, workspace_id, agent_id FK→agents, issue_id NULL, trigger CHECK IN('assign','mention','autopilot','manual','chat'), status, idempotency_key UNIQUE NULL, priority, task_spec JSONB, label_requirements JSONB, trigger_event_id NULL(触发来源事件,审计), config_snapshot JSONB(入队快照,§6.11), timeout_seconds, max_attempts, result, failure_reason, queued_at, finished_at` |
| `execution_attempts` | 一次**物理尝试**(领取、租约、runtime、分支、日志、结果都挂在 attempt 上) | `id, workspace_id, execution_id FK→task_executions, attempt_number, runtime_id FK(复合→runtimes(workspace_id,id)), status CHECK IN('claimed','running','completed','failed','timeout','cancelled','reclaimed'), claimed_by_runtime_id, lease_expires_at, lease_seq, claimed_at, started_at, finished_at, working_branch, result, failure_reason`;`UNIQUE (execution_id, attempt_number)` |

**执行状态机(逻辑层,全系统统一长任务词汇)**:

```
queued ──领取(建 attempt #1)──► claimed ──开始──► running
running ──► completed(终态)
running ──► failed / timeout(失败终态;可重试则见下)
claimed/running ──租约过期/失联──► requeued(当前 attempt 置 reclaimed,逻辑回 queued)
requeued ──► claimed(建 attempt #N+1,不复用旧行)
queued/claimed/running ──用户取消──► cancelling ──► cancelled(终态)
queued ──需审批(§6.10)──► awaiting_approval ──批准──► queued/claimed 续跑
awaiting_approval ──拒绝/过期──► cancelled(失败终态,failure_reason=approval_rejected/approval_expired)
```

规则:
- **requeue 不覆盖审计**:旧 attempt 行保留(runtime/claimed_at/日志/分支/失败原因),`retry_count = COUNT(attempts)-1`;超过 `max_attempts` 转 `failed(failure_reason='max_retries')`。
- **claim 安全**(claim SQL 权威版本见 runtime.md):必须带 `WHERE e.workspace_id = :runtime_workspace_id`;标签/能力匹配只用**服务端保存的** `runtimes.labels/capabilities`,**不信任 daemon 请求里的 labels/capacity**;agent 设了 `default_runtime_id` 时仅该 runtime 可领取。
- **容量防超卖**:claim 事务内对 `runtimes` 行做**原子容量扣减**(`UPDATE runtimes SET current_load = current_load + 1 WHERE id=:rid AND status='online' AND current_load < max_concurrent RETURNING …`),而非前置校验;attempt 终态/回收时**幂等释放**(每个 attempt 只释放一次,由 attempt 状态迁移守卫,`current_load = GREATEST(current_load - 1, 0)`)。
- **租约 fencing**:`lease_seq` 每次领取/续租 +1;旧持有者的一切上报因 `lease_seq` 不匹配被 409 拒绝;`awaiting_approval` 期间 reaper 不回收、租约暂停推进。

### 6.5 投递语义与幂等键(唯一权威)

- 队列/通知/实时事件统一 **at-least-once**;不承诺 exactly-once。
- 一切**外部可见副作用**必须携带稳定幂等键,键的构造:

  | 副作用 | 幂等键 |
  | --- | --- |
  | 执行入队 | `sha256(agent_id \| issue_id \| trigger_event_id)`(同一触发事件不重复入队) |
  | agent 发评论/回流 | `sha256(execution_id \| attempt_number \| 'comment' \| client_seq)` |
  | 工具调用 | `sha256(execution_id \| attempt_number \| tool_id \| stable_args_hash)` |
  | 出向 Webhook/推送 | `sha256(execution_id \| attempt_number \| target \| event)` |
  | git 推送 | 重试分支名 **按 attempt 唯一**:`agent/<execution_id>/a<attempt_number>`,杜绝两个 runtime/attempt 推同一分支 |

- 接收方(评论 API、工具网关等)以 `Idempotency-Key` 落库去重,重复投递返回首次结果。

### 6.6 Transactional outbox(唯一权威)

**任何"业务写库 + 派生动作(创建执行 / 发通知 / 发实时事件 / 调 autopilot)"都必须走 transactional outbox**,杜绝"业务已提交但任务未入队"的永久丢失:

```sql
CREATE TABLE outbox_events (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id),
  event_type     TEXT NOT NULL,           -- issue.assigned / comment.created / execution.enqueue / notification.fanout …
  payload        JSONB NOT NULL,
  idempotency_key TEXT NULL,              -- 处理器去重键(§6.5)
  status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','published','failed')),
  delivery_attempts INT NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at   TIMESTAMPTZ NULL,
  UNIQUE (idempotency_key)                -- NULL 不冲突
);
CREATE INDEX idx_outbox_pending ON outbox_events (created_at) WHERE status = 'pending';
```

- 业务事务**同事务 INSERT outbox_events**(与业务行同提交);relay worker `FOR UPDATE SKIP LOCKED` 领取并分发,成功后置 `published`;失败退避重试,`delivery_attempts` 超限置 `failed` 并告警。
- **禁止**在业务事务外"顺手"创建 execution/notification/realtime 事件(进程内总线、直接调下游)——此为评审硬约束。

### 6.7 实时事件契约(唯一权威)

所有模块的 WebSocket/SSE 实时推送统一遵循:

```sql
CREATE TABLE realtime_channels (
  channel   TEXT PRIMARY KEY,        -- 如 workspace:{ws}:issues / issue:{id} / execution:{id}:logs
  last_seq  BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE realtime_events (
  id          BIGINT GENERATED ALWAYS AS IDENTITY,
  channel     TEXT NOT NULL REFERENCES realtime_channels(channel),
  seq         BIGINT NOT NULL,       -- 频道内单调递增(消除"全局 seq"与"频道内 seq"混用)
  event       TEXT NOT NULL,         -- <entity>.<action>
  payload     JSONB NOT NULL,        -- 完整变更字段 + 可见性水位(见下)
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ NULL,
  UNIQUE (channel, seq)
);
CREATE INDEX idx_realtime_events_replay ON realtime_events (channel, seq);
```

| 规则 | 内容 |
| --- | --- |
| seq 作用域 | **一律为频道内单调**;分配方式:业务事务内 `UPDATE realtime_channels SET last_seq = last_seq + 1 WHERE channel=$1 RETURNING last_seq`,与事件行同事务写入(持久真源) |
| 发布 | relay/网关 worker 把未发布事件经 **Redis pub/sub 仅做 fan-out** 推给各 realtime 网关;Redis 不是真源,丢消息由重放兜底 |
| 保留期 | `realtime_events` 默认保留 **7 天**(可配),到期归档清理 |
| 重连 | 客户端记频道内 `last_seq`,重连带 `resume_from=<last_seq+1>`;网关从 `realtime_events` 顺序补发 |
| 游标过旧 | `resume_from` 早于保留窗口 → 下发 `{ "op": "resync_required", "watermark": <当前最大 seq>, "rest": "<对账 REST URL, 带 since=…>" }`;客户端整拉对账后无感恢复 |
| 订阅授权 | **每个频道订阅时重新做资源级授权**(workspace 成员资格 / project 可见性 / issue 可见性);**私有项目事件只进 `project:{id}` 频道,不得先广播给 `workspace:{ws}:*` 再靠前端过滤** |
| 可见性水位 | 事件 payload 必须携带**完整变更字段**(不只 diff 指针)与 `visibility`(如 issue 当前所属 project/状态),供客户端判定归属;**复杂嵌套 filters 下允许客户端按 id 轻量 refetch**,不得要求前端仅凭 diff 本地重算任意嵌套条件 |
| 断线体验 | 重连/重放过期时 UI 显示"正在重新同步",对账成功后无感恢复(§6.12 异常态) |

### 6.8 流式输出协议(唯一权威)

**浏览器原生 EventSource 不支持 POST SSE**。一切"先提交请求、再流式返回"的场景(聊天生成、squad 编排进度)统一采用:

1. `POST …/generations`(或等价资源)→ `201` 返回 `{generation_id, stream_url}`;
2. 客户端 `GET <stream_url>`(EventSource 兼容,携带 `Last-Event-ID` 断点续传)消费流;事件名跨 SSE/WebSocket 保持一致(`message.delta`/`message.done`/`message.interrupted`/`error`,见 chat-session.md);
3. 中断走**独立幂等端点** `POST …/generations/{id}/stop`(重复 stop 幂等无副作用);
4. 或客户端显式选择 **fetch streaming**(ReadableStream),此时**自行实现重连与 `Last-Event-ID` 对账**,不得声称"原生自动重连"。

### 6.9 触发语义矩阵(唯一权威,B2 硬约束)

以下为所有 agent 触发路径的**确定语义**,不允许"合并或排队"之类不可测试表述:

| 用户动作 | 语义(可测试) |
| --- | --- |
| 分派:assignee 从无/他人 → agent A | 入队 A 的一次执行(`trigger='assign'`);若前任 agent 有 `queued/claimed/running` 执行 → **取消之**(`failure_reason='superseded'`),再入队 A |
| 分派:再次选择**同一** assignee(值未变) | **no-op**,不入队、不产生事件 |
| 保存表单但字段值无变化 | **no-op**(服务端 diff 为空不发 `issue.updated`,不入队) |
| 评论**发布**时 @agent A | 发布后入队 A 的一次执行(`trigger='mention'`,幂等键 §6.5;`uq_mentions(comment_id, mentioned_id)` 保证同评论同 agent 仅一次) |
| **编辑**旧评论:新增 @A | 仅为**新增的**提及入队(对比编辑前后提及集合做 diff);因无关文字修改**不重复**产生运行 |
| 编辑旧评论:移除 @A | 不取消已入队/运行中的执行(仅影响未来);提及记录软删除 |
| 运行中再次 @同一 agent(**新评论**) | **入队新执行**(每条评论 = 独立触发事件);防风暴由频率护栏(rate_limit + 链深度)兜底,语义本身确定 |
| 运行中再次 @同一 agent(**同评论重复编辑**) | 提及集合未变 → **no-op** |
| autopilot 事件重复到达 | 按 `dedup_key` 幂等(autopilot.md),窗口内仅一次 |

**UI 配套**:@ 候选提示语为"**发布后将触发一次运行**"(不得写"选中将立即触发");composer 提交前展示 **trigger preview**(列出将被触发的 agent 清单),并提供**显式抑制**开关(请求体 `suppress_triggers: true` → 仅通知不运行);聊天"沉淀为评论"须展示目标 issue、最终正文、附件与 @agent 副作用预览,确认后**一次提交**。

### 6.10 统一审批实体 approvals(唯一权威,A7/B2 硬约束)

高风险工具确认(`confirm_required`)、squad 拆解方案审批、autopilot 高风险动作审批**共用同一实体与入口**:

```sql
CREATE TABLE approvals (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id      UUID NOT NULL REFERENCES workspaces(id),
  subject_type      TEXT NOT NULL CHECK (subject_type IN ('tool_call','autopilot_action','squad_plan')),
  subject_execution_id UUID NULL,          -- 关联 task_executions(复合 FK → task_executions(workspace_id,id))
  subject_run_id    UUID NULL,             -- autopilot_runs.id(逻辑关联)
  subject_task_id   UUID NULL,             -- squad_tasks.id(逻辑关联)
  requested_by_member_id UUID NOT NULL,    -- 复合 FK → members(workspace_id,id)
  action_summary    JSONB NOT NULL,        -- {action, tool/permission, impact_scope, estimated_cost, detail}
  status            TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','expired','cancelled')),
  requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at        TIMESTAMPTZ NOT NULL,
  decided_by_member_id UUID NULL,
  decided_at        TIMESTAMPTZ NULL,
  decision_comment  TEXT NULL,
  idempotency_key   TEXT NULL UNIQUE
);
CREATE INDEX idx_approvals_pending ON approvals (workspace_id, requested_at) WHERE status = 'pending';
```

| 规则 | 内容 |
| --- | --- |
| 运行时挂起 | 需审批的执行进入 `task_executions.status='awaiting_approval'`(§6.4);reaper 不回收该态;批准后回到队列续跑(新 attempt 或原 runtime 续租继续),拒绝/过期 → `cancelled` |
| API | `GET /api/v1/approvals?role=mine`(**统一"待我审批"收件箱**,聚合三类审批) / `GET /approvals/{id}` / `POST /approvals/{id}/approve` / `POST /approvals/{id}/reject` |
| 展示 | 每条审批显示:动作、所需权限、影响范围、预估成本、过期时间、**批准后的续跑结果**(关联执行深链) |
| 过期 | 到期由 reaper/scheduler 惰性或定时置 `expired`,关联执行转 `cancelled(approval_expired)` 并通知请求者 |
| 权限 | 人类成员且满足:subject 的触发者/分派者、agent owner、或 workspace admin;agent **不可**审批(防自批) |
| 幂等 | 对同一 approval 重复 approve/reject 为 no-op,返回当前状态;`idempotency_key` 兜底重复请求 |
| 租约行为 | 见 §6.4(awaiting_approval 期间租约暂停推进) |

`confirm_required` 不再只是 UI 文案/事件:工具执行前由 runtime 经机器 API 创建 approval 并把执行挂起,批准响应经心跳下行/轮询回传后才继续。

### 6.11 入队可复现快照(唯一权威)

任务入队时必须在 `task_executions.config_snapshot`(JSONB)冻结以下字段,保证运行**可复现、可审计**:

```json
{
  "agent_config_version_id": "<agent_config_versions.id>",
  "skill_versions": {"<skill_id>": "<version_id>", "...": "..."},
  "tool_grants": [{"tool_id": "...", "permission": "read_only|write|confirm_required"}],
  "repo": {"url": "...", "base_ref": "main", "base_sha": "<commit-sha>"},
  "trigger_event_id": "<outbox_events.id 或领域事件 id>"
}
```

配置/技能/工具绑定在运行期间变更**不影响在途执行**,只对后续入队生效。

### 6.12 设计系统与体验基线(唯一权威,B1/B3 硬约束)

**全局信息架构(导航 / 面包屑 / 角色可见性矩阵)**:

| 顶层入口 | 位置 | 可见角色 | 说明 |
| --- | --- | --- | --- |
| 收件箱 / 我的任务 | 日常工作区 | 全部成员 | 通知 + assignee=我 |
| 项目 / 看板 / Issue | 日常工作区 | 按项目可见性 | 核心协作 |
| 成员(人 + agent 同册) | 日常工作区 | 全部成员 | **agent 的唯一名册入口**;agent 的"管理配置"从成员详情深链进入,不再在设置里重复列一遍 agent 名册 |
| 聊天 / 小队 | 日常工作区 | 全部成员 | 对话与编队 |
| 自动化(Autopilots / Runtimes / Skills) | 自动化运营区 | 全部成员可读,管理写需 admin/owner | 渐进披露:普通成员只见"运行与结果",编排配置默认折叠 |
| 设置(工作区/成员角色/审批策略/状态与字段/危险操作) | 管理员区 | admin/owner(guest/agent 不可见) | 普通成员不面对管理后台级复杂度 |

- **Agent 入口去重**:Settings 内不再维护独立 Agents 名册列表;Settings→Agents 仅承载"工作区级 agent 策略"(默认 runtime、触发护栏、审批策略),单个 agent 的配置从成员页/agent 详情进入。
- **全局搜索 / 命令面板**:`Ctrl/Cmd+K` 打开命令面板,跨模块搜索 issue(按 identifier/标题)、成员、agent、项目、视图、聊天会话;所有核心资源有**规范深链**:`/w/{workspace_slug}/issues/by-identifier/{KEY-N}`、`/w/{ws}/projects/{id}`、`/w/{ws}/agents/{id}`、`/w/{ws}/executions/{id}`、`/w/{ws}/chat/{session_id}`、`/w/{ws}/approvals`。
- **设计 token 与无障碍**:颜色以语义 token 定义(status/danger/warn/success/info),文本对比度 ≥ WCAG 2.1 AA(4.5:1);**脉冲动画/颜色不得作为唯一状态信号**(必须叠加图标/文字,如"● 处理中"含文字);全键盘可达(焦点可见、Tab 序合理、Enter/Space 激活);屏幕阅读器标签(aria-label/live-region 用于未读数与运行状态);尊重 `prefers-reduced-motion`;响应式断点 ≥ 1024 桌面 / 768 平板,移动端只读优先。
- **核心页面异常态矩阵**(每个核心页面——看板、issue 详情、成员、聊天、运行详情、收件箱——必须实现):

| 状态 | 呈现 | 恢复入口 |
| --- | --- | --- |
| loading | skeleton 骨架屏 | — |
| empty | 空态插画 + 主操作(如"新建 issue") | — |
| permission denied | 明确"无权限"页 + 申请/联系入口 | 联系 admin |
| offline | 顶部横幅"网络已断开",乐观操作排队 | 自动重连 |
| stale / resync | "正在重新同步…"(`resync_required` 时) | 对账成功后无感消失 |
| partial failure | 批量/多操作逐项成功失败标记 | 失败项"重试" |
| retry | 请求失败 toast + 重试按钮 | 手动重试 |

专项恢复入口:看板断线→顶部重连指示;日志续传→按 offset 自动续;附件上传扫描中→"扫描中,完成后开放下载"占位;agent 无可用 runtime→分派时明确提示"无匹配 runtime"并链到 runtime 页;**审批过期**→执行详情与审批收件箱显示"已过期"并提供"重新发起"。
- **Agent 容量呈现**:agent 状态从二元"空闲/处理中"改为 **"运行中 N / 排队 M / 需审批 K"**(数据源:`task_executions` 按 agent 聚合 + `approvals` 计数),避免误判并发容量。

### 6.13 通知与实时体验(唯一权威,B4 硬约束)

| 规则 | 内容 |
| --- | --- |
| 默认订阅 | 创建者、assignee 自动订阅(reason=creator/assignee);发过评论者自动订阅(participated);被 @ 自动订阅(mentioned);可手动订阅/取消 |
| 按 issue 静音 | `issue_subscriptions.muted=true` 保留订阅但不出通知;收件箱提供"不再关注此 issue"一键静音 |
| 重新置未读 | 同组通知已读后,**仅新的高优事件(mention/assign/agent 运行结束)重新置未读**;同类计数累加(如又多了 3 条评论)**不重新置未读** |
| 分组与归档 | 按 `group_key`(issue+type)折叠;已读 + 过期组自动归档;`archived_at` 语义为移出主视图,可回查 |
| quiet hours | 用户级免打扰时段(站内不弹窗、邮件合并到时段后摘要);**critical 事件穿透免打扰** |
| 事件分级 | **critical(进收件箱 + 可选推送)**:运行失败/超时、审批请求、安全隔离(freeze/扫描命中)、被分派、被 @;**normal(留在运行页/时间线,不进收件箱)**:普通日志、阶段进度、presence 变化 |
| 聚合窗口 | 同 `group_key` 60s 窗口内合并为一条(`payload.count` 递增),避免通知风暴 |
| 自我抑制 | 动作发起者不给自己生成通知;agent 永不接收会再触发自己的通知(回环防护) |

### 6.14 API / 错误 / 分页 词汇(唯一权威)

| 项 | 权威定义 |
| --- | --- |
| 基础 | 前缀 `/api/v1`;JSON;时间一律 RFC3339 UTC;id 一律 UUID |
| 鉴权 | `Authorization: Bearer <token>`(会话 JWT / API token);中间件链:解析 → 工作区成员资格 → RBAC → 限流 |
| 成功包络 | 单对象 `{"data": {...}}`;列表 `{"data": [...], "next_cursor": <opaque\|null>}`;`next_cursor=null` 表示末页 |
| 分页 | 游标分页(keyset,base64 编码 `(sort_key, id)`);**分组查询统一为"整体游标"契约**:`{"groups": [{key,label,count,wip?,data}], "next_cursor": ...}`——`count` 为组内总数,`data` 为当前页切片;**不得**在响应中再给每组独立 cursor(issue.md 与 kanban.md 统一此契约) |
| 乐观并发 | 写操作支持 `version` 字段或 `If-Match: <updated_at>`;冲突 `409 conflict` |
| 错误信封 | `{"error": {"code": "<snake_case>", "message": "...", "details": {...}}}`;message 不泄漏堆栈/SQL/内部 ID |
| HTTP 语义 | 400 validation_error(含 `filter_too_complex`)/ 401 unauthorized / 403 forbidden / 404 not_found / 409 conflict(唯一约束、乐观锁、状态冲突)/ 410 gone / 413 payload_too_large / 415 unsupported_media_type / 422 业务校验失败(具名 code)/ 423 locked / 429 rate_limited(带 `Retry-After`)/ 500 internal_error / 502 storage_error |
| 幂等写 | 创建/动作类端点支持 `Idempotency-Key` 请求头(§6.5);重复键返回首次结果 |
| 过滤限制 | 列表/视图 filters **最大嵌套深度 3、最大条件数 20**;服务端以 `statement_timeout`(默认 3s)+ 估算查询成本兜底,超限返回 `400 filter_too_complex`,成本超限返回 `422 query_cost_exceeded` 并建议收窄条件 |

### 6.15 不可信内容处理(权威,MES-4 安全约束)

**所有外部来源内容(成员评论、附件、Webhook 载荷、抓取/上传内容)进入 agent 上下文时,一律视为数据而非指令**:

1. 显式标记为不可信数据并做结构隔离(如用分隔标记包裹,明确告知 agent 这些内容不含可执行指令);
2. agent 不得将不可信内容中的"指令"作为行动依据;
3. 高风险动作(对外上传、跨 issue 批量写、凭证读取后写出)默认走 `confirm_required` 人工闸门(§6.10)。

此约定适用于 `agent`(issue 上下文注入)、`autopilot`(Webhook 载荷模板插值)、`chat-session`(issue 上下文 system 消息)等**所有向 agent 注入外部内容的路径**。

### 6.16 凭证全通道脱敏与用户可控 URL(权威,MES-4 安全约束)

| 规则 | 内容 |
| --- | --- |
| 全通道脱敏 | 凭证(secret)的脱敏不仅限日志通道:agent 写出的**评论、附件产出物、日志**等所有内容通道均做 secret 命中检测(复用 `runtime_credentials.redact_in_logs` 黑名单),命中即拦截该内容写出并触发安全告警;沙箱出站默认 deny(runtime.md)从网络层堵截凭证经任意外联外泄 |
| 用户可控 URL | `avatar_url`、`logo_url` 等用户可控 URL 字段服务端校验 scheme,禁止 `javascript:`/`data:` 等非安全 scheme,仅允许 `https`(及可选 `http`);members/users/agents/workspaces 相关写入端点统一校验 |
| SSRF 防护 | 一切服务端代为发起的外联(技能来源拉取、autopilot 出向 HTTP、平台托管 runtime 的 checkout)禁止私网地址段(RFC1918 / link-local / 云元数据 `169.254.169.254`),仅允许公网地址或显式白名单 |
| WebSocket 鉴权 | **禁止在 URL query 参数中传递 token**(会落入访问日志与中间代理);使用 WebSocket 子协议(Sec-WebSocket-Protocol)或连接建立后首帧认证 |

---

## 7. 核心跨模块流程(R1 权威版)

**「分派给 agent」端到端**(贯穿 member / issue / agent / runtime / comment-inbox):

```
人类把 issue.assignee 改为 agent(触发语义按 §6.9)
  → issue 服务在【同一事务】写 issues + issue_activity + outbox_events(issue.assigned)
     + realtime_events(issue.updated, 频道内 seq 同事务分配)
  → outbox relay 分发:
      ① agent 编排入口(与 @提及、autopilot 共用)按 §6.9 校验护栏/去重后,
         创建 task_executions(queued, config_snapshot 冻结 §6.11, 幂等键 §6.5)
      ② 通知 fan-out(按 §6.13 订阅/去噪规则写 notifications)
      ③ realtime 网关经 Redis fan-out 推 issue.updated / execution.queued
  → runtime 以 FOR UPDATE SKIP LOCKED 领取(§6.4:workspace 校验 + 服务端标签/能力 +
     原子容量扣减,建 execution_attempts #1,一次性下发 attempt 绑定凭证)
  → runtime checkout 专属分支 agent/<execution-id>/a<attempt>,沙箱执行(running,日志经 WS 流式回传)
  → 完成:agent 以成员身份在 issue 发结果评论(幂等键)、改状态(execution completed)
  → 失败/超时/失联:租约 reaper 把当前 attempt 置 reclaimed,逻辑回 queued 建下一个 attempt
     (at-least-once + 幂等副作用),超 max_attempts 转 failed
```

---

## 8. 整体验收标准

- [x] 覆盖全部 15 个核心模块,每个模块的功能 Spec 均含 功能 / 数据模型 / 接口 / UI/UX 四个维度(见 §5 索引)。
- [x] 整体项目 Spec 完成并正确 reference 所有功能 Spec(§5 全部为有效相对链接)。
- [x] 每份功能 Spec 含可逐条验证的验收标准,数据模型 + 接口 + UI/UX 齐全,可直接指导开发。
- [x] 全部产出物已提交到 Mesh 仓库主干(`main`)。
- [x] 无任何暴露外部出处的内容(全部文档经品牌词/URL 扫描,仅含占位地址)。
- [ ] **(R1)** §6 全部权威契约已在各功能 Spec 中以引用方式落地,各 Spec 无重复/冲突定义;全部 DDL 在本地 PostgreSQL 16+ 实际执行通过。
- [ ] **(R1)** §9 集成测试矩阵作为各模块验收的必测项。

---

## 9. 集成测试矩阵(权威,各模块必测)

以下场景必须有自动化集成测试覆盖(真实启动服务 + 真实 PostgreSQL,不允许纯 mock):

| # | 场景 | 断言要点 |
| --- | --- | --- |
| T1 | **跨租户隔离** | 对每类资源(issues/comments/attachments/executions/approvals/views/squads/autopilots/runtimes/credentials)用 A 区凭证访问 B 区 id → 403/404;构造跨 workspace 的复合 FK 插入 → 数据库约束拒绝 |
| T2 | **并发 claim** | N(≥10)台 runtime 并发领取同一批任务:恰有任务数台成功,无重复领取;`execution_attempts` 每 execution 仅一条 claimed |
| T3 | **容量竞争** | `max_concurrent=2` 的 runtime 并发发起 5 次 claim:成功 ≤2;attempt 终态后 current_load 幂等归零(不出现负数/泄漏) |
| T4 | **requeue 审计** | 领取后杀 runtime → reaper 回收 → 新 attempt 领取成功;旧 attempt 行(runtime/claimed_at/日志引用)完整保留 |
| T5 | **outbox 崩溃恢复** | 业务提交后、relay 分发前杀 relay 进程 → 重启后事件仍被投递(执行被创建、通知生成、实时事件可重放),无丢失 |
| T6 | **WS 重放过期** | 客户端持过旧 `resume_from` 重连 → 收到 `resync_required` + REST 对账水位 → 对账后视图与服务端一致 |
| T7 | **重复触发** | §6.9 矩阵逐行:重复分派同一 assignee=no-op;同评论重复 @=一次执行;编辑评论新增/移除 @ 的触发/不触发;无关文字编辑不重复触发 |
| T8 | **审批过期** | 创建 approval → 到期 → 关联执行转 cancelled(approval_expired) + 请求者收通知;过期后 approve → no-op/410 |
| T9 | **乐观并发冲突** | 两客户端同拖一卡/同改一 issue:一方 409,UI 收敛到服务端最新写,无丢失更新 |
| T10 | **租约防诈尸** | attempt 被 reaper 回收并由新 runtime 领取后,旧 runtime 上报结果 → 409 拒绝,新持有者结果不被覆盖 |
| T11 | **邀请并发** | `max_uses=1` 链接被两用户同时接受:恰一人成功;另一人 422;`used_count` 不超 max_uses |
| T12 | **并发成环** | 两事务并发插入 A→B 与 B→A 依赖(或父子环):advisory lock 串行化下恰一条被拒 `circular_dependency/circular_parent` |
| T13 | **幂等副作用** | 同一 attempt 重复提交相同 Idempotency-Key 的评论/工具调用:结果只产生一次 |
| T14 | **附件隔离区** | 上传完成(scan_status=pending)时请求下载 → 拒绝(403 `scan_pending`);worker 置 clean 后可下载;infected 时永久拒绝并告警 |
| T15 | **编号并发** | 同项目 / 无项目并发创建 issue(≥10):`UNIQUE(workspace_id, identifier)` 下无重号、无跳号(除失败回滚) |

---

## 10. 性能基准方法(权威)

一切 Spec 中的 P95/时延指标**仅在以下基准下构成验收标准**,各模块引用本节而非各自臆测:

| 维度 | 基准 |
| --- | --- |
| 硬件 | 8 vCPU / 32 GB RAM / NVMe SSD 单机;PostgreSQL 16、Redis 7,默认配置(shared_buffers=8GB) |
| 数据规模 | 工作区 50 个;单工作区 issue 10 万、成员 1 万、评论 100 万、通知 500 万、task_executions 100 万(含 attempts 300 万)、附件 10 万 |
| 并发 | k6 50 VU 稳态 + 100 VU 峰值;WebSocket 2000 并发连接 |
| 冷热缓存 | 冷:重启数据库后首跑;热:二跑取数;指标标注冷/热 |
| 测试方法 | 压测脚本随仓库提供(`tests/perf/`),CI 夜间跑;P95 取 5 分钟窗口 |
| 代表性 EXPLAIN | 自定义字段过滤等关键查询须在上述数据分布下附 `EXPLAIN (ANALYZE, BUFFERS)` 结果,证明命中 §6/模块定义的复合/部分索引(见 label-property.md) |

示例基准目标(热缓存):issue 列表/分组 P95 < 500ms(10 万 issue)、claim P95 < 100ms(1000 runtime 并发)、未读计数 P95 < 50ms、实时事件端到端 P95 < 1s(WS 在线)、日志尾部增量 P95 ≤ 2s。

---

*文档版本:Draft v2 / R1 修订(2026-07-24)。后续任何 Spec 变更须在对应功能文件内修订;涉及公共契约的变更必须先改本章并同步引用方。*
