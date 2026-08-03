# Mesh 整体项目 Spec

> 状态:Draft v3(R3 修订) | 本文件是 Mesh 所有开发的唯一入口:先读本文建立全局认知,再按「功能 Spec 索引」进入具体模块。各功能 Spec(`features/*.md`)是各模块实现的唯一依据;`../research/*.md` 是设计调研原始记录,仅供溯源,不作为实现依据。
>
> **修订说明**:本文件 §2.2–2.3、§6、§7、§9、§10 为**全局唯一权威契约(canonical contracts)**。所有功能 Spec 中的 schema、API 包络、错误码、分页、状态枚举、事件词汇、幂等/重试语义**一律引用本文定义,不再重复定义**;功能 Spec 与本文冲突时以本文为准。
>
> **R2 修订要点**(v2 复审 ❌ 项收口):复合 FK `ON DELETE SET NULL` 改为 PG16 列级写法并补真实 DELETE 行为测试(§6.2/§9 T18);补齐同租户/同父域约束与 realtime 租户键(§6.2/§6.7);不可变编号命名空间与工作区级前缀注册(§6.3);claim 无任务容量回滚 + capability 匹配(§6.4);审批续跑**唯一协议**写死(§6.4/§6.10);outbox → realtime **唯一写入路径**与事件词汇注册表(§6.6/§6.7);每频道游标(§6.7);跨项目迁移字段映射(issue.md/kanban.md);小队 active assignment 唯一身份(squad.md `issue_squad_assignments`);approval 强约束(§6.10);blob 真源与秒传 possession 规则(attachment.md);唯一通知优先级矩阵(§6.13)。
>
> **R3 修订要点**(v3 架构/UX 复审 HIGH×9 收口 + 3 项非阻断建议):agent 配置版本补 `workspace_id`/同租户复合 FK/`UNIQUE(workspace_id,agent_id,id)` 与 active 指针重叠复合 FK(§6.2/agent.md,T27);**调度能力与授权能力严格分型**——`required_capabilities` 纯字符串数组 + `capability_grants` 对象数组,入队归一算法写死(§6.4/§6.11/agent.md/skill.md/runtime.md,T28);集成外部身份规范化 + **跨 workspace 全局唯一键** + scope 精确异或 + 删除策略 + `vcs_links` 真源表(integrations.md,T29);IM 投递台账结构化多目的地键(§6.13/comment-inbox.md,T30);data job 源附件 RESTRICT + source hash + checkpoint/`data_job_rows` 逐批幂等恢复协议(import-export.md,T31);**§6.13 扩充 data job 三行为唯一通知矩阵**,模块只引用不自定义(T25 扩展/T32);`users.settings`/`timezone` 登记 + `PATCH /api/v1/users/me` + workspace locale 单一真源(默认 `en`,弃用 `default_language` 不双写,auth.md/i18n.md/workspace.md,T32);Analytics 工作区聚合按请求者项目可见性过滤(admin 全量;协同 MES-4 HIGH-2 安全修复)+ `scope_key` 可见性缓存键 + 「当前归属」口径 + `calendar_timezone` 分桶(analytics.md,T33);Onboarding 入册播种/全量 reconcile + 末步阅读证据 + 成员名册唯一入口(onboarding.md,T34);**词汇校验脚本 `tests/docs/check_event_vocab.py` + CI 落地**(§6.7/T26);`chat_sessions.is_pinned` 快照删除,置顶真源唯一为 favorites(§6.19)。
>
> **R4 修订要点**(第四轮架构/UX 复审 HIGH×6 收口):capability_grants「严格类型」闭环——授权快照 `permission` **必须存在/字符串/枚举合法**,入队归一算法以**唯一可执行实现** `normalize_capability_declarations()` 实测(混合字符串/对象声明 → 字符串补 `confirm_required`、去重/最严格权限/字典序排序 + claim 联动,§6.4/§6.11/agent.md §3.3,T28 扩展);data job 恢复 **fencing**——claim 单调 `lease_seq` token、每批提交锁 job 校验 owner+token+未过期、row_key **原子占用 + 预分配 target_id**(实体创建幂等),过期旧 worker 重新提交整批被拒(import-export.md §3.4/§3.8/§5.4,T31 扩展);locale 单一真源彻底消除三方冲突——`workspaces.default_language` **从当前模型与全部响应示例移除**(迁移说明独立 migration note),响应只返回 `settings.default_locale`,i18n 写非法 locale/timezone 错误码对齐 auth canonical `422 unsupported_locale`/`422 invalid_timezone`(workspace.md/i18n.md,T32 扩展);Onboarding 事件图/入口/成员归属语义统一——所有图/流程为入册播种 + `notification.read` 末步,第 4/5 步严格按 `trigger_member_id` 完成(不批量污染其他成员),agent 流程只保留成员名册入口(onboarding.md/agent.md §4.7,T34 扩展四场景);外部用户身份模型与多工作区成员模型对齐——`external_identities` 映射**全局 `users.id`**、身份键纳入 `provider_tenant_key`,回调先由集成解析 workspace 再 JOIN 该 workspace 的 member(§6.17/integrations.md,T29 扩展「同一用户跨两个 Mesh workspace」「不同外部 tenant 同 user key」;与主干安全修复的 link/unlink + 验证流程协同保留);Analytics **execution 指标统一可见性 scope**——关联 issue 继承项目可见性、无 issue 执行(manual/chat/integration)归属 agent、private agent 先过 agent 可见性,workload-B / agent stats / workspace dashboard 共用并入缓存键(analytics.md §2.2.4/§2.3/§3.1/§5.6,T33 扩展负向测试)。

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
                │ runtime 协议(注册 / 心跳 / 领取 / 上报,机器令牌 mesh_rt_)
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
| 实时 | WebSocket 频道订阅 + **频道内**单调递增 `seq` + 持久化 `realtime_events` 重放(§6.7);Redis 仅作 fan-out;降级为轮询。**唯一写入路径**:业务事务只写 outbox,realtime projector 以 outbox 唯一键幂等落 `realtime_events` 并分配频道 seq(§6.6/§6.7) | 增量合并而非整页刷新;重放真源在数据库,不依赖内存缓冲;单一路径消除原子性/排序/去重分歧 |
| 状态建模 | issue 双层状态:`category`(稳定语义,用于聚合/看板)+ `status`(可自定义,用于展示) | 自定义状态不破坏统计与看板列的稳定性 |
| 投递语义 | 任务/通知/实时事件统一 **at-least-once**;外部副作用(工具调用/推送/评论/提交)以**稳定幂等键**去重(§6.5) | 分布式下 exactly-once 不可得;幂等键使"至少一次"对客户端表现为"恰好一次" |

### 2.2 后台执行拓扑、故障责任与扩容(权威)

Mesh 服务端由以下**独立可部署单元**组成。起步可合并进程部署(除 realtime gateway 外全部为无状态 worker),但职责、竞争方式与故障语义必须按下表实现,不允许用进程内事件总线替代:

| 单元 | 职责 | 竞争/并发模型 | 故障责任与自愈 | 扩容方式 |
| --- | --- | --- | --- | --- |
| **API(FastAPI/uvicorn)** | REST + 鉴权 + 业务写入;所有业务事务**同事务写 `outbox_events`** | 多 worker 无状态 | 单 worker 崩溃仅丢 in-flight 请求,客户端重试(幂等键兜底) | 水平加 worker/实例 |
| **Outbox relay** | 轮询 `outbox_events(status='pending')` → 分发到对应处理器(执行入队 / 通知 fan-out / autopilot 事件 / **realtime projector**)→ 置 `published` | `FOR UPDATE SKIP LOCKED` 抢占;**每条事件独立短事务(一次只预锁一行)**,慢处理器不预占批内后续行;多副本不重复处理;`UNIQUE(idempotency_key)` 兜底 | 崩溃后未发布事件由其他副本/重启后继续投递(at-least-once);**进程内 relay 协程异常、意外取消或未收到共享停机信号却正常返回,均由 1s 存活看门狗发结构化 ERROR 并退避重启**;锁/语句超时等瞬时 DB 竞争短退避且不消耗失败预算;业务失败指数退避,`delivery_attempts` 超限进 `failed` 告警 | 加副本即提高吞吐 |
| **调度 worker(autopilot/scheduler)** | 扫描到期 `autopilots.next_run_at` 与一次性定时,原子抢占创建 run | `FOR UPDATE SKIP LOCKED` + `next_run_at` 前移,多副本不重复触发 | 崩溃后下一扫描周期补发(misfire_policy 决定补发策略) | 加副本;按 workspace 哈希分片(规模化时) |
| **租约 reaper** | 扫描 `execution_attempts` 租约过期/心跳失联 → 回收 attempt(requeue 新 attempt 或转 failed);扫描过期 approval(§6.10) | 单 leader(数据库 advisory lock)或多副本 SKIP LOCKED 分行 | reaper 全挂 → 任务卡 claimed;由监控告警;恢复后批量回收 | 一般单副本足够;分行扫描可多副本 |
| **通知 fan-out worker** | 消费 outbox 中通知类事件 → 写 `notifications` + 邮件摘要队列(通知的实时推送**不直接写 `realtime_events`**,而是产生 outbox 的 `realtime.publish` 事件交 realtime projector 统一登记,§6.6/§6.7) | SKIP LOCKED;`notification_delivery.UNIQUE(notification_id,channel,destination_key)` 幂等(R3:目的地粒度,IM 多目的地并发投递,§6.13/comment-inbox.md §2.8) | 崩溃后由 outbox 重投;邮件失败重试 | 加副本 |
| **Realtime projector** | 消费 outbox 中实时类事件(`event_type='realtime.publish'`)→ **以 outbox 事件 id 为唯一去重键**写 `realtime_events` 并在同事务分配频道 `seq` → 经 Redis pub/sub 通知各网关发布(§6.7 唯一写入路径) | SKIP LOCKED 抢占 outbox 行;`realtime_events.UNIQUE(outbox_event_id)` 保证"至少一次投递 → 恰好一次登记" | 崩溃后未登记事件由其他副本/重启后继续登记;重复投递被唯一键去重,不产生重复事件/乱序 seq | 加副本即提高吞吐 |
| **附件处理 worker** | 隔离区对象的 MIME 嗅探(读 magic bytes)、SHA-256 校验、病毒扫描、缩略图 | SKIP LOCKED 扫 `attachments(scan_status='pending')` | 崩溃后重扫;`scan_status='error'` 重试上限 | 加副本 |
| **实时网关(WebSocket)** | 客户端长连接;订阅频道时**逐资源授权**;从 `realtime_events` 重放 + Redis fan-out 实时推送 | 每连接单线程;多网关经 Redis pub/sub 广播 | 网关崩溃 → 客户端重连,凭 `resume_from` 从 `realtime_events` 补齐;游标过旧 → `resync_required` | 水平加网关,Redis pub/sub 联通 |

**部署形态**:起步 = 1 个 API 进程(含 uvicorn 多 worker)+ 1 个 worker 进程(运行 relay/scheduler/reaper/fan-out/附件处理,各为独立 asyncio 任务)+ 1 个 realtime 网关进程;三者可容器化独立伸缩。worker 各任务之间以 SKIP LOCKED 解耦,**任何单一任务循环死亡不得拖停或静默移除其他任务**:外层看门狗每 1s 检查各任务终态,将异常、意外取消、意外正常返回统一记录为结构化错误并按任务独立指数退避重启(某任务退避不得阻塞其他 slot 的检查);worker 与看门狗必须共享同一 shutdown Event,仅该 Event 已置位时的返回/取消属于正常关停,不得告警或重启。独立取消域只隔离单任务故障,Supervisor 自身关停则置位共享 Event、取消并 await 全部子任务,不得泄漏后台协程。

**数据与中间件凭据安全(MES-83,权威)**:任何数据存储 / 中间件(PostgreSQL、Redis、MinIO 及 `mesh_app` 角色)的凭据与网络暴露遵循以下硬约束:

1. **强唯一口令**:生产不得有任何可猜测默认口令;`docker-compose.yml` 中全部凭据为必填项(`${VAR:?...}`,缺失即启动报错),本地开发经 `scripts/gen-dev-secrets.sh` 一次性生成强随机值。
2. **不对公网暴露**:数据存储 / 中间件一律不发布宿主端口(compose 中 postgres / redis 无 `ports:`;MinIO 仅回环发布供三阶段直传),仅经内网 / 服务网格可达;Redis 必须 `requirepass` + `protected-mode yes` + `bind` 内网网卡。
3. **启动期 fail-fast**:`MESH_AUTH_MODE=production` 时,API / realtime 网关 / worker 三个启动路径均调用 `validate_infra_settings`,拒绝空值 / 已知默认 / 过短(<16 字符)的 Redis / PostgreSQL / 对象存储凭据。
4. **CI 回归守护**:`backend/tests/unit/test_compose_security.py` 常跑断言「回环唯一发布 + 数据存储零宿主端口 + 凭据必填无默认」,防止弱口令 / 端口暴露回归。

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

底层大语言模型经统一适配层接入,可替换不同模型供应商;runtime 与平台之间只依赖 runtime 协议(REST + 机器令牌 `mesh_rt_`,哈希唯一存 `runtimes.runtime_token_hash`,不入 `api_tokens`,auth.md §2.5.1),允许用户把自有机器/容器注册为 runtime。安装与激活安全见 runtime.md(签名发布包 + 校验,激活码不进命令行参数)。

---

## 4. 模块总览

Mesh 由 **24 个功能模块**组成,分五层(MES-76 L1:计数与 §5 索引对齐,此前误留 20):

### 4.1 基础层

| 模块 | 定位 |
| --- | --- |
| **workspace(工作区)** | 多租户隔离根:工作区设置、全局唯一 slug、邀请机制 |
| **member(成员)** | 统一成员名册:人类与 agent 同册,角色(owner/admin/member/guest)、停用/启用。**owns `members` 表(唯一权威)** |
| **auth(认证与授权)** | 注册登录、第三方 OAuth、会话、API token(供 **CLI / agent**;**runtime 机器令牌 `mesh_rt_` 不入本表,唯一真源为 `runtimes.runtime_token_hash`**,runtime.md owns,auth.md §2.5.1 注册表)、RBAC、审计、限流。owns `users`/`sessions`/`api_tokens`/`audit_logs`/`device_authorizations` |

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
| **runtime(运行时)** | agent 执行环境:注册/心跳、任务领取(SKIP LOCKED+租约)、日志流、凭证安全、仓库 checkout；真实本地执行、安全边界与 provider 适配见 [runtime-executor.md](features/runtime-executor.md)。**owns `runtimes`/`task_executions`/`execution_attempts` 等——`task_executions` 是全系统运行的唯一真源实体名** |
| **skill(技能)** | 可安装的结构化指令包:定义—版本—安装—绑定四层解耦,沙箱与信任分级 |
| **squad(小队)** | 人机编队协作:角色(leader/member)、拆解树 + 依赖 DAG + 批次、计划审批闸门(经 §6.10 统一 approval) |
| **autopilot(自动化)** | 定时(cron)与事件驱动触发,把任务派给 agent;内置防失控护栏;审批经 §6.10 统一 approval |

### 4.5 平台能力层(R2 新增,MES-2 强化轮必修)

| 模块 | 定位 |
| --- | --- |
| **onboarding(上手引导)** | 首次使用引导:上手清单数据模型(`onboarding_states`,member×workspace 进度持久化)、Mesh 激活路径(建区 → 邀请/加 agent → 建首 issue → 分派/@ 触发首个运行 → 收件箱见 agent 回评 = aha moment)、成体系空状态规范 |
| **integrations(集成平台)** | 统一第三方集成抽象:集成注册/绑定模型、入站事件摄取(签名 + 去重 + 审计,复用 autopilot `webhook_events` 范式)、出站渠道适配(IM 通知渠道)、出向 Webhook 订阅(开发者平台);落地 飞书/Lark、Slack、GitHub/GitLab(VCS)三连接器。owns `integrations`/`integration_bindings`/`integration_events`/`webhook_subscriptions`/`vcs_links`/`external_identities` |
| **import-export(数据导入导出)** | issue/project 的 CSV/JSON 导入(字段映射/校验/错误行报告,支持从其它工具迁移)+ 异步导出任务与签名下载(走统一附件通道)。owns `data_jobs` |
| **analytics(统计报表)** | cycle time / velocity / 吞吐量 / workload / burndown、项目与工作区仪表盘、按 agent 维度的运行统计;数据源为 `issues`/`task_executions` 聚合,明确口径与时间窗 |
| **i18n(国际化与时区)** | locale 协商、字符串外部化、本地化日期/数字渲染、`users.timezone` 展示层时区化(存储仍 UTC);与主题/暗色(§6.12)共同构成前端呈现契约 |
| **search-command-palette(全局搜索 / 命令面板 / 快捷键体系)** | §6.12 详 Spec:`Ctrl/Cmd+K` 命令面板(跨模块搜 issue/成员/agent/项目/视图/聊天会话,服务端权限过滤)、规范深链(一切资源外链唯一形态)、power-user 快捷键四组(全局/看板/issue/聊天)+ `?` 上下文帮助层 + 输入框豁免;横切导航效率层,不新增业务表,不扩 §6.7 事件词汇 |
| **cli(开发者平台 CLI)** | §11 详 Spec:`mesh` 官方命令行(REST 瘦客户端,经 `api_tokens`/设备码会话鉴权),工作项/项目/成员/agent/执行/日志流式(SSE)/导入导出全命令族,`--output table\|json` 双模式与退出码分类,OpenAPI 3.1 随仓库发布;服务端零新表(设备码授权为 auth.md 增量) |
| **theme(主题与暗色模式)** | §6.12 主题段详 Spec:三态 `light/dark/system`、偏好协商链(用户→工作区默认→系统,镜像 §6.18 locale 链)、语义 token 单一取色路径与暗色 token 集整组替换、两套主题 WCAG AA 自证 + CI 门禁(对比度/硬编码扫描);设计系统级呈现契约,不新增业务表 |
| **design-quality(前端设计质量与体验)** | 全局 UI/UX 实施基线:页面模板、信息架构、设计令牌扩展、排版、组件状态、图标、响应式/触控、逐页差距清单、关键流程微交互与视觉/无障碍门禁;不新增业务表 |

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
| 12 | [runtime.md](features/runtime.md)（[本地执行体子 Spec](features/runtime-executor.md)） | 智能体 | 注册—心跳—领取—上报契约、execution/attempt 分层、租约自愈、凭证 fencing、真实 provider、安全沙箱与 task broker |
| 13 | [skill.md](features/skill.md) | 智能体 | 四层解耦、不可变版本、沙箱与信任分级 |
| 14 | [squad.md](features/squad.md) | 智能体 | 编排层与内容层解耦、DAG + 批次、计划审批(统一 approval)、issue 责任主体模型 |
| 15 | [autopilot.md](features/autopilot.md) | 智能体 | 触发器+条件+动作、护栏默认开启、kill switch、审批(统一 approval) |
| 16 | [onboarding.md](features/onboarding.md) | 平台能力 | `onboarding_states` 进度持久化、Mesh 激活路径(aha moment)、成体系空状态规范、深链既有向导 |
| 17 | [integrations.md](features/integrations.md) | 平台能力 | 统一集成抽象(注册/绑定/入站摄取/出站适配/出向订阅)、飞书/Lark · Slack · GitHub/GitLab 三连接器 |
| 18 | [import-export.md](features/import-export.md) | 平台能力 | CSV/JSON 导入(映射/校验/错误行报告)、异步导出 + 签名下载(统一附件通道) |
| 19 | [analytics.md](features/analytics.md) | 平台能力 | cycle time/velocity/吞吐量/workload/burndown、项目与工作区仪表盘、agent 运行统计 |
| 20 | [i18n.md](features/i18n.md) | 平台能力 | locale 协商、字符串外部化、本地化渲染、时区化展示(存储 UTC) |
| 21 | [search-command-palette.md](features/search-command-palette.md) | 平台能力 | 命令面板跨模块搜索(服务端权限过滤)、规范深链、power-user 快捷键四组 + `?` 上下文帮助层 + 输入框豁免 |
| 22 | [cli.md](features/cli.md) | 平台能力 | `mesh` CLI 命令族(REST 瘦客户端)、PAT/设备码鉴权、日志 SSE 流式、导入导出联动、退出码契约、OpenAPI 3.1 |
| 23 | [theme.md](features/theme.md) | 平台能力 | 三态主题与偏好协商链、语义 token + 暗色整组替换、WCAG AA 自证与 CI 门禁、组件硬编码色值禁令 |
| 24 | [design-quality.md](features/design-quality.md) | 平台能力 | 全局 UI/UX 设计基线、逐页差距清单、令牌/排版/组件、响应式/触控、关键流程与验收门禁 |

runtime 的 Server 协议与数据模型以
[runtime.md](features/runtime.md) 为唯一权威；本地执行进程的组件边界、CLI
适配、任务隔离、预算熔断、部署和真实 LLM E2E 见配套设计
[runtime-executor.md](features/runtime-executor.md)。

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
| 显示名解析 | 统一顺序:`members.display_override`(非空)→ 人类 `users.display_name`→`users.email`(`users` 无 `full_name` 列,MES-76 H3 修订:此前引用不存在的 `users.full_name`);agent `agents.name`。服务端解析,接口返回单一 `display_name`。**检索专用投影**:`members.search_name` 为按本链算法同步的小写投影,**仅供 trigram 检索、不作显示真源**(同步契约与 DDL 见 search-command-palette.md §2.2,属本条「高频表存储快照须强制一致并明示」条款的登记项) |
| 全局身份层(R5 写死) | `users` 与 `external_identities`(外部平台账号 ↔ `users.id` 映射,integrations.md §2.4.1)是**与成员名册解耦的全局身份表**——**不携带 `workspace_id` 所有权列,不是任何工作区的租户资源**:一个自然人(同一 `users.id`)凭各工作区的 `members` 行参与多个工作区,其外部身份映射为**单行全局行**,删除任一工作区(含建链工作区)不影响映射(建链来源仅以可空审计列 `created_in_workspace_id ON DELETE SET NULL` 记录,不级联控制映射生命周期);行级访问以所属 `users.id` 为边界(**全局解链仅映射所属用户本人,工作区 admin 无旁路**,只能撤销本工作区使用权/成员资格),不适用下述第 5 条 workspace RLS |

### 6.2 多租户同租户约束(唯一权威)

仅"所有查询带 workspace_id"**不够**。所有跨租户关系必须在数据库层可验证:

1. **目标表**:凡可能被跨模块引用的实体表(projects、members、issue_statuses、labels、cycles、milestones、attachments、squads、runtimes、agents、views 等),除 `PK(id)` 外必须建 **`UNIQUE (workspace_id, id)`**(供复合 FK 引用)。
2. **引用方**:凡持有上述引用的表必须**同时存 `workspace_id` 并建复合外键** `FK (workspace_id, <ref>_id) → 目标表 (workspace_id, id)`,使"引用了别的工作区的对象"在 INSERT 时即被拒绝。
3. **成员引用**:`issues.assignee_id` 等对 `members.id` 的引用同理——引用表存 `(workspace_id, assignee_id)` 复合 FK → `members(workspace_id, id)`。
4. **多态逻辑外键**(如 `attachment_links.linked_id` 指向 issues 或 comments):不建物理 FK,但引用行必须存 `workspace_id`,删除一致性由软删除 + 服务层保证,并在集成测试矩阵(§9)覆盖跨租户用例。
5. **纵深防御**:在以上约束之上启用 **PostgreSQL RLS**:业务表(含 `realtime_channels`/`realtime_events`)按 `workspace_id = current_setting('mesh.workspace_id')::uuid` 建策略,API 事务开始时设置该 GUC;RLS 作为程序遗漏的兜底而非替代复合 FK。**全局身份表(`users`、`external_identities`)不携带 `workspace_id`,不在 workspace RLS 作用域内(R5 写死)**——其行级边界为所属用户:服务层(可选叠加 `user_id = current_setting('mesh.user_id')::uuid` 的 user 级 RLS 作纵深防御)保证读取/解链仅及映射所属 `users.id` 本人,工作区角色不放宽该边界;把全局映射塞进某个 `workspace_id` 的 RLS 口径会使其他工作区无法读取同一用户的全局身份(假隔离、真故障,T29 负向测试)。
6. **复合 FK 的 `ON DELETE SET NULL` 必须列级显式**(R2 硬约束):PostgreSQL 默认把复合 FK 的**所有**引用列一起置 NULL,会连带把 `NOT NULL` 的 `workspace_id` 置空,导致删除实际失败——**建表成功 ≠ 删除语义可用**。一切需要"删除时置空引用"的复合 FK 一律采用 PostgreSQL 16 列级语法 **`ON DELETE SET NULL (<引用列>)`**(仅置空引用列,`workspace_id` 保持不动),迁移层显式 DDL;对"不可悬空"的引用(如留痕作者)采用**软删除 + `ON DELETE RESTRICT`**。真实 DELETE 行为(而非仅建表)必须有集成测试覆盖(§9 T18)。
7. **同父域约束**(R2 硬约束):自引用与"同属一个父对象"的多引用(父评论/父消息/引用消息/当前版本指针等),仅同租户**不够**,还必须以**重叠唯一键 + 复合 FK** 在数据库层保证"引用行与被引用行属于同一父对象":被引用表建形如 `UNIQUE (workspace_id, <parent>_id, id)` 的唯一键,引用方以 `(workspace_id, <parent>_id, <ref>_id) → 目标表 (workspace_id, <parent>_id, id)` 复合 FK 引用(如评论的 parent 必须同 issue、聊天消息的 parent/quote 必须同会话、`skills.current_version_id` 必须属于同一 skill、skill 安装/绑定的版本必须属于所装技能)。
8. **realtime 表也是租户资源**(R2 硬约束):`realtime_channels`/`realtime_events` 必须携带 `workspace_id` 并建复合 FK/唯一键与 RLS(§6.7),使租户隔离在数据库层可执行;**频道字符串(如 `issue:{id}`)不得充当租户隔离边界**。

### 6.3 编号、前缀与默认状态约束(唯一权威)

| 项 | 权威规则 |
| --- | --- |
| 编号命名空间(R2:与当前归属项目解耦) | `issues` 上 `identifier_namespace_key TEXT NOT NULL`(创建时取所属项目的 `key`,无项目 issue 取工作区收件箱保留前缀,默认 `WS`)与 `number BIGINT NOT NULL`(创建时在该命名空间内自增),**两者一经生成永不改变、不随项目迁移**;`identifier = identifier_namespace_key || '-' || number` 亦不可变。`project_id` **仅表示当前归属项目**,跨项目迁移时改变的是 `project_id`,命名空间与编号保持不变。有项目 issue 的 `number` 由 `projects.issue_seq` 行锁自增(计数器绑定于"创建时所属项目"的 key 命名空间);**无项目 issue** 使用工作区级计数器 `workspaces.inbox_issue_seq`(同发行锁自增)+ 收件箱保留前缀。**`UNIQUE (project_id, number)` 已废除**——它与"不可变编号 + 跨项目迁移"直接冲突(`WEB-1` 移入已有 `APP-1` 的项目会违约,看板 `group_by=project` 拖拽因此不可实现) |
| 编号唯一 | `issues` 上同时建 **`UNIQUE (workspace_id, identifier_namespace_key, number)`**(命名空间级,取代原 `UNIQUE(project_id, number)`)与 **`UNIQUE (workspace_id, identifier)`**(工作区级,兜住无项目 issue 与一切重复) |
| 前缀注册(R2:工作区级排他) | 一切 identifier 前缀——项目 `key`、**当前与历史的**收件箱前缀——统一登记在工作区级前缀注册表 `identifier_prefix_registry`(workspace.md owns):`UNIQUE (workspace_id, key)`,含 `kind ∈ ('project','inbox','retired')` 与可选 `project_id`。创建项目占用 `key`、变更 `settings.inbox_issue_prefix` 都必须先经注册表排他校验:新前缀与任一在册前缀(含 `retired`)冲突即拒绝;收件箱前缀变更时旧前缀置 `retired` **永久保留**(历史 identifier 不重编号)。如此 `UNIQUE(workspace_id, identifier)` 不再在创建 issue 时"随机"被违反 |
| 编号不可复用/不可变 | issue 软删除编号永久废弃;`identifier` 一经生成**永不改变**(issue 在项目间迁移只改 `project_id`,不重编号);项目前缀 `key` **永久保留**——`projects` 上建 **`UNIQUE (workspace_id, key)`(不带 `WHERE deleted_at IS NULL`)** 并与前缀注册表一致,软删除/归档项目后前缀不可复用;项目不得跨工作区迁移;删除项目时其 issue 的 `identifier` 保持不变(`issues.project_id` 复合 FK `ON DELETE SET NULL (project_id)`,仅置空归属列,§6.2 第 6 条) |
| 默认状态唯一 | "每作用域唯一默认状态"用**部分表达式唯一索引**(COALESCE 表达式不能写进表级 UNIQUE 约束):`CREATE UNIQUE INDEX uq_issue_statuses_default ON issue_statuses (workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000')) WHERE is_default;` |
| 至少一个默认 | 由事务保证:任何"取消某状态默认"的写操作必须与"设置新默认"在同一事务;工作区/项目创建事务内播种默认状态集;服务层自检校验每作用域恰有一个默认,缺失即报警并修复 |
| 其他 NULL 作用域唯一 | labels/custom_field_defs/views 等"工作区级 OR 项目级"命名唯一,一律用上同款**部分表达式唯一索引**写法,不得写表级 `UNIQUE (ws, COALESCE(project_id,…), name)` |

### 6.4 任务队列:execution / attempt 分层(唯一权威)

**逻辑执行 `task_executions` 与物理尝试 `execution_attempts` 分离**(runtime.md owns 两表):

| 表 | 语义 | 关键字段 |
| --- | --- | --- |
| `task_executions` | 一次**逻辑执行**(由分派/@提及/autopilot/外部集成触发产生,生命周期内只有一行) | `id, workspace_id, agent_id FK→agents, issue_id NULL, trigger CHECK IN('assign','mention','autopilot','manual','chat','integration')`(R2:`integration` = 外部 IM/VCS 集成触发,§6.17/integrations.md)`, status, idempotency_key UNIQUE NULL, priority, task_spec JSONB, label_requirements JSONB, required_capabilities JSONB NOT NULL DEFAULT '[]'`(R2:权威能力需求字段,claim 时与服务端 runtime 能力匹配;**R3:严格类型为「字符串数组」,schema CHECK 拒绝任何非字符串元素**——调度字段只接受 capability key 集合,`{capability,permission}` 对象一律只进 `config_snapshot.capability_grants` 授权快照;**R4:两套字段由入队归一算法的唯一实现派生并实测——validation 脚本的 `normalize_capability_declarations()`(agent.md §3.3 权威算法的可执行参照)接受混合字符串/对象声明,产出「去重 + 字典序排序的字符串数组」与「`permission` 必填的严格对象数组」,集成测试 T28 以同一实现断言全部归一语义与 claim 联动**)`, trigger_event_id NULL(触发来源事件,审计), config_snapshot JSONB(入队快照,§6.11), timeout_seconds, max_attempts, result, failure_reason, queued_at, finished_at` |
| `execution_attempts` | 一次**物理尝试**(领取、租约、runtime、分支、日志、结果都挂在 attempt 上) | `id, workspace_id, execution_id FK→task_executions, attempt_number, runtime_id FK(复合→runtimes(workspace_id,id)), status CHECK IN('claimed','running','cancelling','completed','failed','timeout','cancelled','reclaimed')`(R2:`cancelling` 为物理层两段式取消中间态,与逻辑层词汇统一;`cancelled(failure_reason='awaiting_approval')` 为审批挂起时当前 attempt 的终态)`, claimed_by_runtime_id, lease_expires_at, lease_seq, claimed_at, started_at, finished_at, working_branch, result, failure_reason`;`UNIQUE (execution_id, attempt_number)` |

**执行状态机(逻辑层,全系统统一长任务词汇)**:

```
queued ──领取(建 attempt #1)──► claimed ──开始──► running
running ──► completed(终态)
running ──► failed / timeout(失败终态;可重试则见下)
claimed/running ──租约过期/失联──► requeued(当前 attempt 置 reclaimed,逻辑回 queued)
requeued ──► claimed(建 attempt #N+1,不复用旧行)
queued/claimed/running ──用户取消──► cancelling ──► cancelled(终态)
running ──工具命中 confirm_required(§6.10)──► awaiting_approval
    (当前 attempt 置 cancelled(failure_reason='awaiting_approval'),租约结束、容量幂等释放,审计行保留)
awaiting_approval ──批准──► queued(新 attempt #N+1 携带审批上下文从审批点续跑,见 §6.10)
awaiting_approval ──拒绝/过期──► cancelled(失败终态,failure_reason=approval_rejected/approval_expired)
```

> **审批挂起只能从 `running` 进入**(工具调用发生在执行中);不存在 `queued → awaiting_approval` 迁移(入队前的人工确认由 autopilot/squad 各自的编排层审批承载,见对应 Spec)。

规则:
- **requeue 不覆盖审计**:旧 attempt 行保留(runtime/claimed_at/日志/分支/失败原因),`retry_count = COUNT(attempts)-1`;超过 `max_attempts` 转 `failed(failure_reason='max_retries')`。
- **claim 安全**(claim SQL 权威版本见 runtime.md):必须带 `WHERE e.workspace_id = :runtime_workspace_id`;**标签与能力匹配**只用**服务端保存的** `runtimes.labels/capabilities`(`e.label_requirements <@ runtimes.labels` **且** `e.required_capabilities <@ runtimes.capabilities`,R2 补齐能力条件),**不信任 daemon 请求里的 labels/capacity**;agent 设了 `default_runtime_id` 时仅该 runtime 可领取。
- **容量防超卖 + 无任务必回滚**(R2 硬约束):claim 是「选任务 + 扣容量 + 建 attempt」的**单一原子成功分支**——先锁 runtime 行校验在线/容量(不预扣),`FOR UPDATE SKIP LOCKED` 选出匹配任务;**选中任务后**才 `current_load + 1`、转 `claimed`、建 attempt,一次提交。**有容量但无匹配任务时,事务必须整体回滚(`current_load` 保持不变)再返回 204**,不得"先 +1 再找任务"后带着 0 行结果 COMMIT(那会造成容量永久泄漏)。attempt 终态/回收时**幂等释放**(每个 attempt 只释放一次,由 attempt 状态迁移守卫,`current_load = GREATEST(current_load - 1, 0)`)。集成测试见 §9 T3/T20。
- **租约 fencing**:`lease_seq` 每次领取/续租 +1;旧持有者的一切上报因 `lease_seq` 不匹配被 409 拒绝。
- **审批续跑唯一协议**(R2 写死,取代"新 attempt 或原 runtime 续跑"的互斥双方案):进入 `awaiting_approval` 时当前 attempt 置 `cancelled(awaiting_approval)`——**attempt 不保留在途态**(审计行保留)、**租约不继续**(随 attempt 终态结束,reaper 无需特殊处理该态)、**容量不占用**(幂等释放);runtime 失联不产生回收问题(无在途租约);批准后执行回 `queued`,由(可能不同的)runtime 领取并建 attempt #N+1,凭审批请求时冻结的 `resume_context`(检查点引用 + 已完成步骤水位 + 待执行工具调用参数,§6.10)从审批点恢复上下文;拒绝/过期 → `cancelled`。该协议任何一环皆可测试(§9 T21),且不存在"租约暂停导致永久卡死"的路径。

### 6.5 投递语义与幂等键(唯一权威)

- 队列/通知/实时事件统一 **at-least-once**;不承诺 exactly-once。
- 一切**外部可见副作用**必须携带稳定幂等键,键的构造:

  | 副作用 | 幂等键 |
  | --- | --- |
  | 执行入队 | `sha256(agent_id \| issue_id \| trigger_event_id)`(同一触发事件不重复入队) |
  | agent 发评论/回流 | `sha256(execution_id \| attempt_number \| 'comment' \| client_seq)` |
  | 工具调用 | `sha256(execution_id \| attempt_number \| capability_key \| stable_args_hash)`(R2:`tool_id` 真源已删除,统一为版本化 capability key,§6.11) |
  | 出向 Webhook/推送 | `sha256(execution_id \| attempt_number \| target \| event)` |
  | git 推送 | 重试分支名 **按 attempt 唯一**:`agent/<execution_id>/a<attempt_number>`,杜绝两个 runtime/attempt 推同一分支 |
  | 数据作业入队(import/export) | `sha256(data_job_id \| action)`(`action ∈ {created, import-validate, import-run, export}`;同一作业同一动作不重复入队,import-export.md §3.8) |
  | 数据作业恢复(reaper) | `sha256(data_job_id \| 'resume' \| last_committed_batch)`(按 checkpoint 批次去重,保证回收-重投幂等,import-export.md §3.8 R3) |
  | 集成 IM 会话性出站(确认接收 ack / 命令反馈,integrations.md §3.8) | `sha256(queue_item_id \| 'ack')`(经 outbox `im.send` 快通道,at-most-once;同一队列项至多一条确认消息) |
  | 集成 IM 超长结果分段发送(integrations.md §3.10,钉钉 msgParam ≤15000 字节) | `sha256(notification_id \| 'chunk' \| i)`(第 i 段至多一次;at-least-once 出队下重复不重发段) |

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
  available_at   TIMESTAMPTZ NOT NULL DEFAULT now(),   -- 最早可领取时刻(退避/可重试结果后移;MES-82 R4-4 入权威)
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at   TIMESTAMPTZ NULL,
  UNIQUE (idempotency_key)                -- NULL 不冲突
);
CREATE INDEX idx_outbox_pending ON outbox_events (available_at, created_at) WHERE status = 'pending';
```

- 业务事务**同事务 INSERT outbox_events**(与业务行同提交);relay worker 领取条件 `status='pending' AND available_at <= now()`(`FOR UPDATE SKIP LOCKED`)并分发,成功后置 `published`;失败退避重试(**后移 `available_at` = now() + 指数退避,同时 `delivery_attempts+1`**),`delivery_attempts` 超限置 `failed` 并告警。**可重试非失败结果(如 integrations.md `token_refresh_busy`,MES-82 R4-4)只后移 `available_at`(短退避)、不递增 `delivery_attempts`**——不消耗失败预算、不终态,`available_at` 过滤同时防止热循环。**`execution.enqueue` 执行级幂等键沿用既有标准 `payload.idempotency_key`(各触发路径与消费者契约不变);仅 `trigger='integration'` 附加要求 payload 携带 `queue_item_id` 且消费者先 `FOR UPDATE` 锁队列项守卫状态(integrations.md §3.9 rearm 键分层:行级键 K2 仅 outbox 去重,payload 仍携带执行级键 K)**。
- **relay 锁边界与超时守护(硬约束)**:批大小仅限制一次 pass 的最大处理量,不得在一个事务中预锁整批后逐条执行;必须按「`FOR UPDATE SKIP LOCKED` 领取 1 条 → 同事务分发/写状态 → COMMIT 释放全部业务表与 outbox 行锁」循环。每条事务设置有限 `lock_timeout`(默认 500ms,可配置;`0` 显式关闭);`40P01`/`40001`/`55P03`/`57014` 属瞬时竞争,回滚 savepoint 后只后移 `available_at`、不消耗 `delivery_attempts`。普通处理失败按上条指数退避。该边界保证单条坏事务或维护锁竞争不会预占后续事件、拖停整条 publisher。
- **终态行保留期清理(防无限膨胀)**:`published`/`failed` 行(含 `idempotency_key` 唯一索引项)由 worker 的 outbox-retention 循环按保留期(默认 7 天,`MESH_OUTBOX_EVENT_RETENTION` 可配)分批删除;`pending` 行**永不**清理(清理即静默丢任务)。`failed` 行需整段保留期过后才可删,远大于 relay 重试预算,故 §6.6 永久失败告警必然先于清理发出。
- **禁止**在业务事务外"顺手"创建 execution/notification/realtime 事件(进程内总线、直接调下游)——此为评审硬约束。
- **实时事件的唯一登记路径(R2 硬约束)**:一切实时事件(含各模块 §3.x/§4.x 所列 WebSocket 事件、`notification.created`、执行状态回流)一律为:业务事务写 `outbox_events`(`event_type='realtime.publish'`,payload 含频道、事件名、完整变更字段)→ **realtime projector**(§2.2)以 outbox 事件 id 为唯一去重键写 `realtime_events` 并**在投影事务内分配频道 `seq`**(§6.7)→ 经 Redis pub/sub 通知网关发布。**禁止业务事务直接 INSERT `realtime_events` 或直接分配 `seq`**——两条路径会产生不同的原子性/排序/去重实现乃至重复事件;projector 崩溃后重启经 outbox 补投,`realtime_events.UNIQUE(outbox_event_id)` 保证不重复登记(§9 T5/T26)。

### 6.7 实时事件契约(唯一权威)

所有模块的 WebSocket/SSE 实时推送统一遵循:

```sql
CREATE TABLE realtime_channels (
  channel      TEXT NOT NULL,          -- 如 workspace:{ws}:issues / issue:{id} / execution:{id}:logs
  workspace_id UUID NOT NULL REFERENCES workspaces(id),  -- R2:租户键(频道字符串不得充当隔离边界,§6.2 第 8 条)
  last_seq     BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (channel),
  UNIQUE (workspace_id, channel)
);
CREATE TABLE realtime_events (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id  UUID NOT NULL REFERENCES workspaces(id),  -- R2:租户键 + RLS 可执行
  channel       TEXT NOT NULL,
  seq           BIGINT NOT NULL,       -- 频道内单调递增(消除"全局 seq"与"频道内 seq"混用)
  event         TEXT NOT NULL,         -- <entity>.<action>,必须命中下方「事件词汇注册表」
  payload       JSONB NOT NULL,        -- 完整变更字段 + 可见性水位(见下)
  outbox_event_id UUID NOT NULL,       -- R2:唯一写入路径——来自 outbox_events.id(§6.6),幂等去重
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at  TIMESTAMPTZ NULL,
  UNIQUE (channel, seq),
  UNIQUE (outbox_event_id),            -- at-least-once 投递 → 恰好一次登记(projector 重投不产生重复事件)
  FOREIGN KEY (workspace_id, channel) REFERENCES realtime_channels(workspace_id, channel)
);
CREATE INDEX idx_realtime_events_replay ON realtime_events (channel, seq);
CREATE INDEX idx_realtime_events_ws_created ON realtime_events (workspace_id, created_at);  -- 保留期归档清理

-- RLS 纵深防御(§6.2 第 5 条):realtime 表同样是租户资源
ALTER TABLE realtime_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE realtime_events  ENABLE ROW LEVEL SECURITY;
CREATE POLICY mesh_rt_channels_tenant ON realtime_channels
  USING (workspace_id = current_setting('mesh.workspace_id')::uuid);
CREATE POLICY mesh_rt_events_tenant ON realtime_events
  USING (workspace_id = current_setting('mesh.workspace_id')::uuid);
```

| 规则 | 内容 |
| --- | --- |
| seq 作用域 | **一律为频道内单调**;分配方式:**realtime projector**(§2.2/§6.6)在写事件行的**同一事务**内 `UPDATE realtime_channels SET last_seq = last_seq + 1 WHERE channel=$1 RETURNING last_seq`(持久真源)。**业务事务不分配 seq、不直接写 `realtime_events`**——业务事务只写 outbox 的 `realtime.publish` 事件 |
| 唯一写入路径 | 业务事务 → `outbox_events(realtime.publish)` → realtime projector 以 `outbox_event_id` 去重落库并分配频道 seq → Redis pub/sub 通知网关发布(§6.6)。projector 崩溃重启后经 outbox 补投,`UNIQUE(outbox_event_id)` 保证不重复登记;事件在频道内的顺序以 projector 分配的 `seq` 为准,payload 携带 `updated_at`/`version` 供客户端收敛 |
| 发布 | projector/网关把未发布事件经 **Redis pub/sub 仅做 fan-out** 推给各 realtime 网关;Redis 不是真源,丢消息由重放兜底 |
| 保留期 | `realtime_events` 默认保留 **7 天**(可配),到期按 `(workspace_id, created_at)` 归档清理 |
| 重连 | 客户端记**每频道** `last_seq`,重连带 `resume_from=<last_seq+1>`(频道级游标);网关从 `realtime_events` 顺序补发。可选的服务端跨设备游标持久化见 kanban.md `realtime_channel_cursors`(`(workspace_id, member_id, channel)`);**不存在"单个视图一个总游标"的设计**(一个视图消费多个频道,单游标无语义,R2 已删除 `view_subscriptions.last_seen_seq`) |
| 游标过旧 | `resume_from` 早于保留窗口 → 下发 `{ "op": "resync_required", "watermark": <当前最大 seq>, "rest": "<对账 REST URL, 带 since=…>" }`;客户端整拉对账后无感恢复。**客户端纵深防御**:`rest` 先经 `new URL(rest, apiBaseUrl)` 解析并断言与 API 基同源(同源部署 `apiBaseUrl` 为空时以页面 origin 为基)且路径在 `/api/v1/` 之下,不满足即拒发对账请求(走 reconciler 错误路径退避重试,绝不发出携带 Bearer 的外泄请求);对账翻页设上限,超限即停(防恶意 `next_cursor` 死循环) |
| 订阅授权 | **每个频道订阅时重新做资源级授权**(workspace 成员资格 / project 可见性 / issue 可见性),并以 `realtime_channels.workspace_id` 在数据库层校验频道归属;**私有项目事件只进 `project:{id}` 频道,不得先广播给 `workspace:{ws}:*` 再靠前端过滤** |
| 可见性水位 | 事件 payload 必须携带**完整变更字段**(不只 diff 指针)与 `visibility`(如 issue 当前所属 project/状态),供客户端判定归属;**复杂嵌套 filters 下允许客户端按 id 轻量 refetch**,不得要求前端仅凭 diff 本地重算任意嵌套条件 |
| 断线体验 | 重连/重放过期时 UI 显示"正在重新同步",对账成功后无感恢复(§6.12 异常态) |

**事件词汇注册表(唯一权威,R2)**:所有实时/SSE 事件名必须取自下表(`<entity>.<action>` 形式;`message.*` 与流控帧 `error`/`ping` 为 README §6.8 流式协议的流内事件名,一并登记)。各功能 Spec 只可引用本表事件名,**禁止使用未登记事件名**;仓库提供文档级词汇校验脚本 `tests/docs/check_event_vocab.py`(扫描 `docs/specs/**/*.md` 中的事件名引用并与本注册表比对,不通过即 CI 失败):

| 域 | 登记事件名 |
| --- | --- |
| workspace / 成员 / 邀请 | `workspace.updated` · `workspace.deleted` · `member.added` · `member.updated` · `member.removed` · `member.role_changed` · `member.presence` · `invitation.redeemed` |
| 会话 / 鉴权 | `session.revoked`(auth.md §3.7/§5.6:会话/令牌撤销广播,使相关连接下次心跳鉴权失败重连被拒) |
| 项目 / 里程碑 / 周期 | `project.created` · `project.updated` · `project.archived` · `project.unarchived` · `project.deleted` · `project_update.added` · `milestone.created` · `milestone.updated` · `milestone.deleted` · `cycle.updated` |
| issue / 依赖 / 视图 | `issue.created` · `issue.updated` · `issue.deleted` · `issue.moved` · `issue.project_changed`(跨项目迁移,R2 新增) · `issue.labels_changed` · `issue.custom_field_changed` · `dependency.changed` · `view.updated` · `view.presence` · `view.wip_exceeded`(看板 warn 列超限,kanban.md §4.4) |
| 评论 / 反应 / 通知 | `comment.created` · `comment.updated` · `comment.deleted` · `comment.resolved` · `reaction.changed` · `notification.created` · `notification.read` · `inbox.unread_count` |
| 标签 / 自定义字段 | `label.created` · `label.updated` · `label.deleted` · `custom_field.updated` · `custom_field_option.updated` |
| agent | `agent.created` · `agent.updated` · `agent.deleted` · `agent.lifecycle_changed` · `agent.presence` · `agent.trigger_skipped` |
| 执行 / 审批 / 队列 / runtime | `execution.queued` · `execution.claimed` · `execution.started` · `execution.progress` · `execution.completed` · `execution.failed` · `execution.timeout` · `execution.cancelled` · `execution.requeued` · `execution.awaiting_approval`(R2 新增:工具审批挂起) · `execution.log` · `approval.created` · `approval.decided` · `queue.depth_changed` · `runtime.activated` · `runtime.online` · `runtime.offline` · `runtime.degraded` · `runtime.paused` |
| 技能 / 附件 | `skill_import.progress` · `skill.changed` · `skill.update_available` · `skill.approval_required` · `attachment.processed` · `attachment.deleted` |
| 小队 | `squad.updated` · `squad.archived` · `squad_member.changed` · `squad_task.status_changed` · `squad_activity.created` · `squad_message.created` · `squad_assignment.changed`(R2 新增:小队分派建立/取消,squad.md) · `task.status` · `subtask.created` · `subtask.assigned` · `plan.submitted` · `task.aggregated`(§6.8 编排进度 SSE 流帧,持久于 `squad_task:{id}` 频道凭 seq 断点重放,squad.md §3.2/§3.5) |
| 自动化 | `autopilot.updated` · `autopilot.rate_limited` · `autopilot_runs.status_changed` · `autopilot_runs.approval_required` · `webhook_events.received` |
| 平台能力(R2 新增模块) | `onboarding.progress` · `onboarding.completed` · `integration.updated` · `integration.event_ingested` · `integration.queue_updated` · `data_job.updated` · `favorites.changed` |
| 聊天流式(§6.8 流内事件) | `message.created` · `message.delta` · `message.done` · `message.interrupted` · `error` · `ping` |

> **词汇漂移零容忍**(R2):如 agent.md 曾出现的帧示例 "agent.run_started"(未登记运行起始帧名,与本表 `execution.started` 冲突)一律以本注册表为准修正;新事件必须先进本表再在模块 Spec 引用。**R3:文档级词汇校验脚本与 CI 已落地**——`tests/docs/check_event_vocab.py` 扫描 `docs/specs/**/*.md` 的事件名引用并与本注册表比对,未登记即 CI 失败(`.github/workflows/spec-checks.yml`;此前本节约定在校验脚本缺位下以人工评审兜底,R3 起为自动化硬关卡)。

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
| **外部 IM 消息触发**(R2,§6.17) | 已绑定 IM 渠道(飞书/Lark、Slack、**钉钉/DingTalk**)中 @agent 或私聊 agent → 入**会话级 FIFO 队列**(`integration_message_queue`,integrations.md §2.10)后入队一次执行(`trigger='integration'`,幂等键 `sha256(agent_id \| integration_binding_id \| external_event_id)`,`integration_events.UNIQUE(integration_id, external_event_id)` 去重保证同一外部事件仅一次);**派发时机按入队时有效模式快照(项 `dispatch_mode`,含排空-再切换规则)**:`serial_conversation`(钉钉默认)同一会话串行派发(**数据库级至多一个在途项,部分唯一索引覆盖 `dispatching/processing/cancelling` 全在途态**——`/stop` 的 `cancelling` 项继续占用 lane、不提前放行下一项;新消息按序排队)、`parallel`(飞书/Slack 默认)入队即派发(同会话可并发,不受独占索引约束);队列项状态机 `pending→dispatching→processing→(cancelling→)终态`,执行终态由内部事件 `execution.finished` 单一驱动(runtime.md);**命令消息(`/stop`/`/btw` 等)走控制平面即时处理,不入队不触发**(integrations.md §3.7);**入站消息内容一律按不可信数据处理**(§6.15);未绑定/未匹配 agent 的外部消息不触发运行(仅审计留痕) |

**UI 配套**:@ 候选提示语为"**发布后将触发一次运行**"(不得写"选中将立即触发");composer 提交前展示 **trigger preview**(列出将被触发的 agent 清单),并提供**显式抑制**开关(请求体 `suppress_triggers: true` → 仅通知不运行);聊天"沉淀为评论"须展示目标 issue、最终正文、附件与 @agent 副作用预览,确认后**一次提交**。

> **小队分派不走"assignee 值比较"(R2)**:「把 issue 分派给小队」经**显式小队分派端点**(squad.md `issue_squad_assignments`)而非 PATCH assignee 值——因为同一 leader 可领导多支小队,`issues.assignee_id=leader` 无法区分哪支小队。因此"再次选择同一 assignee = no-op"**仅适用于个人 assignee**;小队改派(即使目标小队 leader 与现任相同)永远不是 no-op:取消旧小队根任务、建立新分派(详见 squad.md §1.2 S4 / §2.x / §4.4)。

### 6.10 统一审批实体 approvals(唯一权威,A7/B2 硬约束)

高风险工具确认(`confirm_required`)、squad 拆解方案审批、autopilot 高风险动作审批**共用同一实体与入口**:

```sql
CREATE TABLE approvals (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id      UUID NOT NULL REFERENCES workspaces(id),
  subject_type      TEXT NOT NULL CHECK (subject_type IN ('tool_call','autopilot_action','squad_plan')),
  subject_execution_id UUID NULL,          -- tool_call 主题 → task_executions
  subject_run_id    UUID NULL,             -- autopilot_action 主题 → autopilot_runs
  subject_task_id   UUID NULL,             -- squad_plan 主题 → squad_tasks
  requested_by_member_id UUID NOT NULL,
  action_summary    JSONB NOT NULL,        -- {action, capability+permission, impact_scope, estimated_cost,
                                           --  resume_context:{checkpoint_ref, completed_steps, pending_tool_call}, detail}
  status            TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','expired','cancelled')),
  requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at        TIMESTAMPTZ NOT NULL,
  decided_by_member_id UUID NULL,
  decided_at        TIMESTAMPTZ NULL,
  decision_comment  TEXT NULL,
  idempotency_key   TEXT NULL UNIQUE,
  -- R2:subject 同租户复合 FK(主题真源表均已建 UNIQUE(workspace_id, id))
  FOREIGN KEY (workspace_id, subject_execution_id) REFERENCES task_executions(workspace_id, id),
  FOREIGN KEY (workspace_id, subject_run_id)       REFERENCES autopilot_runs(workspace_id, id),
  FOREIGN KEY (workspace_id, subject_task_id)      REFERENCES squad_tasks(workspace_id, id),
  FOREIGN KEY (workspace_id, requested_by_member_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, decided_by_member_id)   REFERENCES members(workspace_id, id),
  -- R2:按 subject_type 恰好一个 subject 列非空
  CHECK (
       (subject_type = 'tool_call'        AND subject_execution_id IS NOT NULL
                                         AND subject_run_id IS NULL AND subject_task_id IS NULL)
    OR (subject_type = 'autopilot_action' AND subject_run_id IS NOT NULL
                                         AND subject_execution_id IS NULL AND subject_task_id IS NULL)
    OR (subject_type = 'squad_plan'       AND subject_task_id IS NOT NULL
                                         AND subject_execution_id IS NULL AND subject_run_id IS NULL)
  )
);
CREATE INDEX idx_approvals_pending ON approvals (workspace_id, requested_at) WHERE status = 'pending';
-- R2:同一 subject 仅一个 pending approval(部分唯一索引)
CREATE UNIQUE INDEX uq_approvals_pending_execution
  ON approvals (workspace_id, subject_execution_id)
  WHERE status = 'pending' AND subject_type = 'tool_call';
CREATE UNIQUE INDEX uq_approvals_pending_run
  ON approvals (workspace_id, subject_run_id)
  WHERE status = 'pending' AND subject_type = 'autopilot_action';
CREATE UNIQUE INDEX uq_approvals_pending_task
  ON approvals (workspace_id, subject_task_id)
  WHERE status = 'pending' AND subject_type = 'squad_plan';
```

| 规则 | 内容 |
| --- | --- |
| 运行时挂起与续跑(**唯一协议,R2 写死**) | 工具命中 `confirm_required` 时,runtime 经机器 API 创建 approval,逻辑执行进入 `task_executions.status='awaiting_approval'`,同时**当前 attempt 置 `cancelled(failure_reason='awaiting_approval')`**(审计行保留)、**租约随 attempt 终态结束、容量幂等释放**(不存在"租约暂停/reaper 不回收"的在途态,因而不可能永久卡死);reaper 对 `awaiting_approval` 无需特殊处理。**批准后**执行回 `queued`,下一次领取建 attempt #N+1,凭 `action_summary.resume_context`(审批请求时由 runtime 冻结:检查点对象存储引用 + 已完成步骤水位 + 待执行工具调用参数)**从审批点恢复上下文续跑**;**拒绝/过期** → `cancelled(approval_rejected/approval_expired)`。该协议每个环节可测试(§9 T21) |
| API | `GET /api/v1/approvals?role=mine`(**统一"待我审批"收件箱**,聚合三类审批) / `GET /approvals/{id}` / `POST /approvals/{id}/approve` / `POST /approvals/{id}/reject` |
| 展示 | 每条审批显示:动作、所需权限(capability + permission)、影响范围、预估成本、过期时间、**批准后的续跑结果**(关联执行深链 + 「将从审批点以新尝试恢复:已完成 N 步,待执行 <工具调用摘要>」) |
| 过期 | 到期由 reaper/scheduler 惰性或定时置 `expired`,关联执行转 `cancelled(approval_expired)` 并通知请求者 |
| 权限 | 人类成员且满足:subject 的触发者/分派者、agent owner、或 workspace admin;agent **不可**审批(防自批) |
| 幂等 | 对同一 approval 重复 approve/reject 为 no-op,返回当前状态;`idempotency_key` 兜底重复请求;同一 subject 重复发起 pending 审批被部分唯一索引拒绝(取既有 pending 审批返回) |

`confirm_required` 不再只是 UI 文案/事件:工具执行前由 runtime 经机器 API 创建 approval 并把执行按上述唯一协议挂起,批准结果经心跳下行/轮询回传,执行以新 attempt 从审批点续跑。

### 6.11 入队可复现快照(唯一权威)

任务入队时必须在 `task_executions.config_snapshot`(JSONB)冻结以下字段,保证运行**可复现、可审计**:

```json
{
  "agent_config_version_id": "<agent_config_versions.id>",
  "skill_versions": {"<skill_id>": "<version_id>", "...": "..."},
  "capability_grants": [{"capability": "exec:shell", "permission": "read_only|write|confirm_required"}, "..."],
  "repo": {"url": "...", "base_ref": "main", "base_sha": "<commit-sha>"},
  "trigger_event_id": "<outbox_events.id 或领域事件 id>"
}
```

> **R2:`tool_id` 真源已删除**(`tools`/`agent_tool_bindings` 表于 MES-2 删除,不再有工具目录主键可冻结)。工具权限统一以**版本化 capability key + permission** 表达并冻结进 `capability_grants`——与 skill.md 的 `required_capabilities`/`granted_capabilities` 条目结构一致(`{"capability": "<key>", "permission": "read_only|write|confirm_required"}`;**声明层**未标注 permission 的条目在归一时补默认 `confirm_required`,**归一后的授权快照 `permission` 必填**,见下条 R3/R4);`/agents/{id}/tools` 系列端点为 capability 条目的薄封装(agent.md),**任何 Spec 与示例不得再出现 `tool_id` / `tool_grants`**。
>
> **R3:声明层与调度层严格分离(硬约束)**:skill.md 的 `required_capabilities`/`granted_capabilities` 允许「字符串 key」与「`{capability,permission}` 对象」混用——那是**授权声明层**表达,绝不得原样写入调度字段。入队时由编排入口执行**归一算法**(权威定义见 agent.md §3.3)派生严格类型的两套字段:① **调度字段** `task_executions.required_capabilities` = 纯 capability key **字符串数组**(对象条目只取其 `capability` key;去重、字典序排序;schema CHECK 拒绝非字符串元素);② **授权快照** `config_snapshot.capability_grants` = 严格 **`[{capability, permission}]` 对象数组**(字符串条目补默认 `confirm_required`;同一 capability 取最严格 permission:`confirm_required > write > read_only`)。对象进入调度字段会使 claim 的 JSONB `<@` 匹配永不命中、任务永久无法领取;两套字段的类型约束由 validation 脚本实测,集成测试 T28 覆盖归一算法与 claim 联动。
>
> **R4:授权快照 `permission` 必填 + 归一算法唯一实现(硬约束)**:`config_snapshot.capability_grants` 的每个条目 **`permission` 必须存在、必须为字符串、取值必须为 `read_only|write|confirm_required`**——schema CHECK 对缺失/非字符串/非法枚举的 permission 一律拒绝(归一前的声明层缺省语义只存在于 skill.md 声明与归一函数入口,**不得**以"缺 permission"形态落进快照)。归一算法的**唯一可执行实现**为 validation 脚本的 `normalize_capability_declarations(declared)`(agent.md §3.3 权威算法的参照实现:字符串条目 → grants 补 `confirm_required`;对象条目缺 permission → 补 `confirm_required`;permission 非法 → 抛 `capability_invalid`(422);输出 required 去重 + 字典序、grants 按 capability 字典序且同 capability 取最严格);后端编排入口的实现必须与该函数逐条等价,T28 以**同一实现**处理混合字符串/对象声明并断言「字符串补默认 / 去重 / 最严格权限 / 排序 / 非法声明拒绝 / claim 联动」全部语义(不接受仅手工写入两份结果的断言)。

配置/技能/能力授权在运行期间变更**不影响在途执行**,只对后续入队生效。

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

- **Agent 入口去重(R5 写死:唯一名册 + 唯一创建入口 + 防回归校验)**:Settings 内不再维护独立 Agents 名册列表;Settings→Agents 仅承载"工作区级 agent 策略"(默认 runtime、触发护栏、审批策略),单个 agent 的配置从成员页/agent 详情进入。**「仅 Agent」视图是成员名册页的筛选投影(同一路由 / 同一列表组件 / 同一 `[ + 新建 Agent ]` 入口,agent.md §4.2/§4.5),不存在独立 Agents 列表页、第二导航 / 第二名册或第二个创建入口;`tests/docs/check_roster_entry.py` 随 CI 常跑(§9 T35),独立 `Agents [+ 新建]` 页面 / 未标注为投影的相关表述 / 导航图中的 Agents 新建入口均判 CI 失败。**
- **全局搜索 / 命令面板**:`Ctrl/Cmd+K` 打开命令面板,跨模块搜索 issue(按 identifier/标题)、成员、agent、项目、视图、聊天会话;所有核心资源(**一切可搜索/可通知资源,MES-76 H5 补齐 member/view;MES-77 R1/P7 补齐 squad**)有**规范深链**:`/w/{workspace_slug}/issues/by-identifier/{KEY-N}`、`/w/{ws}/projects/{id}`、`/w/{ws}/members/{member_id}`(成员/agent 名册条目详情;`/w/{ws}/agents/{id}` 为其按 agent_id 解析的别名)、`/w/{ws}/views/{view_id}`、`/w/{ws}/executions/{id}`、`/w/{ws}/chat/{session_id}`、`/w/{ws}/squads/{squad_id}`(小队详情,squad.md;小队任务详情 `/w/{ws}/squads/{squad_id}/tasks/{task_id}`)、`/w/{ws}/approvals`。旧扁平路由(`/inbox`、`/board`、`/members`…)为同工作区上下文内的应用内别名,经**前端路由器 replace navigation**(`navigate(target,{replace:true})`,触发路由匹配与数据加载)至规范路由并保留 query/hash;**真实 HTTP 301 仅由 SPA 入口文档处理器对过期 slug 返回**;执行层与逐条映射见 search-command-palette.md §3.4。
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
- **主题与暗色模式(R2,必修-F)**:在上述语义 token 基础上建立**主题切换契约**——主题模式 `light`/`dark`/`system`(跟随系统 `prefers-color-scheme`),用户偏好存 `users.settings.theme`(账号级)+ 工作区可设默认(`workspaces.settings.default_theme`,未登录/未设置时生效);**一切颜色必须经语义 token 引用,禁止组件硬编码色值**,暗色模式以**暗色 token 集**整体替换语义 token 取值实现(而非逐组件改写);暗色 token 集与亮色集一一对应且**同样满足 WCAG 2.1 AA 对比度(4.5:1)**;图表/状态色在两套主题下各有校准取值(数据可视化配色见各模块约定);主题切换即时生效(无刷新),并尊重 `prefers-reduced-motion`/`prefers-contrast`。
- **键盘快捷键体系(R2,建议-7 转正)**:在既有无障碍键盘可达之上,提供 **power-user 快捷键体系**:全局 `Ctrl/Cmd+K` 命令面板(前述)、`C` 新建 issue、`/` 聚焦搜索、`G` 然后 `I`/`B`/`M`/`A` 跳转(收件箱/看板/成员/自动化)、`?` 打开**快捷键帮助层**(列出当前上下文全部可用快捷键);快捷键在输入框获焦时不触发(除显式 `Ctrl/Cmd` 组合);所有快捷键操作均有等价鼠标路径(快捷键是加速,不是唯一入口);快捷键表随上下文(全局/看板/issue 详情/聊天)分组,帮助层实时反映。

### 6.13 通知与实时体验(唯一权威,B4 硬约束)

> **R2:唯一通知优先级矩阵**。全系统(README / runtime.md / comment-inbox.md / 各模块)对"什么事件进收件箱、是否穿透 quiet hours、是否重置未读"**只以本表为准**;此前"execution completed 三处互相冲突"(§6.13 critical 不含成功 / runtime 所有终态进通知 / comment-inbox 默认实时邮件并重置未读)统一收口如下。`notifications` 表携带服务端按本矩阵派生的 `priority TEXT NOT NULL CHECK IN ('critical','normal')` 字段(comment-inbox.md owns)。

| 事件 | priority | 进收件箱 | 穿透 quiet hours | 重置同组未读 | 邮件默认 |
| --- | --- | --- | --- | --- | --- |
| **执行成功**(execution `completed`) | normal | **否——默认留运行页/时间线**;仅当用户在 `notification_preferences` 显式订阅 `execution_finished`,或该执行由本人 @/分派触发且开启"执行结果"订阅时进收件箱 | 否 | 否(即使订阅进箱,也按普通事件不重置已读组) | none;订阅后 digest |
| **执行失败/超时**(execution `failed`/`timeout`) | **critical** | 是(触发者/分派者/订阅者) | **是** | **是** | realtime |
| **执行取消**(execution `cancelled`,含 superseded/agent_paused) | normal | 否(留运行页;取消发起者本人不通知) | 否 | 否 | none |
| **审批请求**(approval.created,工具/squad 计划/autopilot 动作) | **critical** | 是(统一"待我审批"入口) | **是** | **是** | realtime |
| **安全隔离**(freeze / 扫描命中 infected / 凭证撤销告警) | **critical** | 是(上传者 + admin) | **是** | **是** | realtime |
| **被分派 / 被 @**(assigned / mentioned) | **critical** | 是 | **是** | **是** | mentioned=realtime;assigned 可配 digest |
| **data job 成功**(data_jobs `completed`,import/export 无失败行,R3 新增) | normal | **否——默认留数据作业页**(toast + 下载入口);仅当 `requested_by` 显式订阅 `data_job_finished` 时进收件箱 | 否 | 否 | none;订阅后 digest |
| **data job 部分成功**(data_jobs `completed_with_errors`,存在失败行需人工处理,R3 新增) | normal | 是(收件人 `requested_by`;有失败行需处理,故默认进箱) | 否 | 否(计数累加) | digest |
| **data job 失败**(data_jobs `failed`,任务级故障,R3 新增) | **critical** | 是(收件人 `requested_by`) | **是** | **是** | realtime |
| 评论新增 / 状态变更 / 订阅更新 | normal | 是(按 `group_key` 聚合组) | 否 | 否(计数累加) | digest |
| 普通日志 / 阶段进度 / presence 变化 / 执行 `queued`/`claimed`/`started` / data_jobs 中间进度(`data_job.updated` 进度帧) | —(非通知事件) | 否(留运行页/作业页/实时频道) | — | — | — |

| 规则 | 内容 |
| --- | --- |
| 默认订阅 | 创建者、assignee 自动订阅(reason=creator/assignee);发过评论者自动订阅(participated);被 @ 自动订阅(mentioned);可手动订阅/取消 |
| 按 issue 静音 | `issue_subscriptions.muted=true` 保留订阅但不出通知;收件箱提供"不再关注此 issue"一键静音 |
| 重新置未读 | 同组通知已读后,**仅新的 critical 事件(执行失败/超时、审批请求、安全隔离、被分派、被 @)重新置未读**;**执行成功不重置未读**;同类计数累加(如又多了 3 条评论)**不重新置未读** |
| 分组与归档 | 按 `group_key`(issue+type)折叠;已读 + 过期组自动归档;`archived_at` 语义为移出主视图,可回查 |
| quiet hours | 用户级免打扰时段(站内不弹窗、邮件合并到时段后摘要);**仅 critical 事件穿透免打扰** |
| 聚合窗口 | 同 `group_key` 60s 窗口内合并为一条(`payload.count` 递增),避免通知风暴 |
| 自我抑制 | 动作发起者不给自己生成通知;agent 永不接收会再触发自己的通知(回环防护) |
| 模块对齐(R2/R3) | runtime.md 的"终态触发通知"改为**按本矩阵分发**(成功→运行页,失败/超时→收件箱 + 可选 Webhook);comment-inbox.md 的 `execution_finished` 类型**默认不投递成功事件**(preferences 显式订阅后才进箱),失败/超时按 critical 投递;**import-export.md 的 data job 通知只引用本矩阵的 data job 三行(R3),不得自行定义成功/失败分级**;**任何模块 Spec 不得另行定义事件分级或无条件成功通知**("触发者收到 execution_finished""agent 完成均生成通知"之类表述一律以本矩阵为准修正) |
| 投递渠道(R2) | `notification_delivery.channel` 取值扩展为 `in_app`/`email`/`websocket`/**`im`**(comment-inbox.md owns);`channel='im'` 时在投递台账记录具体 IM 平台(`feishu`/`slack`/`dingtalk`)与目标外部身份;IM 投递经 §6.17 集成平台出站适配器发送(失败重试/幂等与其余渠道一致,台账为 `notification_delivery`)。**IM 渠道仅为出站增强,站内收件箱永远是通知真源**(推送是增强,不是唯一依据) |

### 6.14 API / 错误 / 分页 词汇(唯一权威)

| 项 | 权威定义 |
| --- | --- |
| 基础 | 前缀 `/api/v1`;JSON;时间一律 RFC3339 UTC;id 一律 UUID |
| 鉴权 | `Authorization: Bearer <token>`(会话 JWT / API token);中间件链:解析 → 工作区成员资格 → RBAC → 限流 |
| 成功包络 | 单对象 `{"data": {...}}`;列表 `{"data": [...], "next_cursor": <opaque\|null>}`;`next_cursor=null` 表示末页 |
| 分页 | 游标分页(keyset,base64 编码 `(sort_key, id)`);**分组查询统一为"整体游标"契约**:`{"groups": [{key,label,count,wip?,data}], "next_cursor": ...}`——`count` 为组内总数,`data` 为当前页切片;**不得**在响应中再给每组独立 cursor(issue.md 与 kanban.md 统一此契约) |
| 乐观并发 | 写操作支持 `version` 字段或 `If-Match: <updated_at>`;冲突 `409 conflict` |
| 错误信封 | `{"error": {"code": "<snake_case>", "message": "...", "details": {...}}}`;message 不泄漏堆栈/SQL/内部 ID |
| HTTP 语义 | 400 validation_error(含 `filter_too_complex`)/ 401 unauthorized / 403 forbidden / 404 not_found / 409 conflict(唯一约束、乐观锁、状态冲突)/ 410 gone / 413 payload_too_large / 415 unsupported_media_type / 422 业务校验失败(具名 code)/ 423 locked / 429 rate_limited(带 `Retry-After`)/ 500 internal_error / 502 storage_error / 503 service_unavailable(模块具名码 `stream_channel_unavailable`:集成 Stream 长连接信道未就绪等上游信道态,integrations.md §3.5) |
| 幂等写 | 创建/动作类端点支持 `Idempotency-Key` 请求头(§6.5);重复键返回首次结果 |
| 过滤限制 | 列表/视图 filters **最大嵌套深度 3、最大条件数 20**;服务端以 `statement_timeout`(默认 3s)+ 估算查询成本兜底,超限返回 `400 filter_too_complex`,成本超限返回 `422 query_cost_exceeded` 并建议收窄条件 |
| 跨项目迁移(R2) | 跨项目移动 issue(看板 `group_by=project` 拖拽或显式 move 端点)为**两步式契约**:`POST /api/v1/issues/{id}/move-preview`(或 move 命令 `dry_run`)返回将被**映射/清除**的字段清单(项目私有 status → 目标项目同 category 默认 status;项目私有 milestone/cycle/label/自定义字段值清除;工作区级字段保留)→ 客户端展示并要求确认 → `POST /api/v1/issues/{id}/move`(或 `POST /views/{id}/moves`,`confirm=true`)在**单事务**完成迁移;未确认的 move 返回 `422 move_confirmation_required`(详见 issue.md §3.8 / kanban.md §3.2) |

### 6.15 不可信内容处理(权威,MES-4 安全约束)

**所有外部来源内容(成员评论、附件、Webhook 载荷、抓取/上传内容、**checkout 仓库内文件与命令输出**)进入 agent 上下文时,一律视为数据而非指令**:

1. 显式标记为不可信数据并做结构隔离(如用分隔标记包裹,明确告知 agent 这些内容不含可执行指令);
2. agent 不得将不可信内容中的"指令"作为行动依据;
3. 高风险动作(对外上传、跨 issue 批量写、凭证读取后写出)默认走 `confirm_required` 人工闸门(§6.10)。

此约定适用于 `agent`(issue 上下文注入)、`autopilot`(Webhook 载荷模板插值)、`chat-session`(issue 上下文 system 消息)等**所有向 agent 注入外部内容的路径**。

### 6.16 凭证全通道脱敏与用户可控 URL(权威,MES-4 安全约束)

| 规则 | 内容 |
| --- | --- |
| 全通道脱敏 | 凭证(secret)的脱敏不仅限日志通道:agent 写出的**评论、附件产出物、日志**等所有内容通道均做 secret 命中检测(复用 `runtime_credentials.redact_in_logs` 黑名单),命中即拦截该内容写出并触发安全告警;沙箱出站默认 deny(runtime.md)从网络层堵截凭证经任意外联外泄 |
| 轮换临时令牌 | `accessToken` 等由第三方平台签发并持续轮换的临时值不进入字面值黑名单（无法在多副本刷新竞态下完整、及时登记且安全收益不可靠）；持有此类值的适配器必须采用**结构化零日志**：不得记录或持久化请求体、响应体及鉴权头值，错误诊断只保留 `method/url/status` 等非密元数据。长期存储凭据的解密值仍按上一行进入 `redact_in_logs`。 |
| 用户可控 URL | `avatar_url`、`logo_url` 等用户可控 URL 字段服务端校验 scheme,禁止 `javascript:`/`data:` 等非安全 scheme,**仅允许 `https`**(R2:统一 https-only,明文 `http` 的用户可控头像/Logo URL 是混合内容弱攻击面,不再提供可选 http);members/users/agents/workspaces/squads 相关写入端点统一校验 |
| SSRF 防护 | 一切服务端代为发起的外联(技能来源拉取、autopilot 出向 HTTP、平台托管 runtime 的 checkout)禁止私网地址段(RFC1918 / link-local / 云元数据 `169.254.169.254`),仅允许公网地址或显式白名单 |
| WebSocket 鉴权 | **禁止在 URL query 参数中传递 token**(会落入访问日志与中间代理);使用**连接建立后首帧认证**单一机制(客户端连接成功后发送 `{op:'auth',token}` → 服务端回复 `auth_ok`;v0.1.0 起实现基线,前后端已收敛于首帧,不再保留子协议可选项) |
| WebSocket DoS 硬化 | 每连接资源护栏(M4):**首帧认证超时 5s**(`MESH_WS_AUTH_TIMEOUT`,未认证连接快速释放);**入站帧限速 30/滚动秒**,超限回 `rate_limited` 错误帧后断开;**单连接订阅上限 256**(`MESH_WS_MAX_SUBSCRIPTIONS`),超限对新频道回 `too_many_subscriptions` 错误帧(不断开,已订阅频道重订阅幂等放行);**传输层帧上限 64KB**(uvicorn `--ws-max-size`,与 `MESH_WS_MAX_SIZE_BYTES` 一致,compose 已内置);**错误帧不回显客户端原始内容**(unknown-op / forbidden 消息为固定文案,频道关联仅走结构化 `channel` 字段) |

### 6.17 集成平台契约(唯一权威,R2 必修-B;详 Spec 见 integrations.md)

第三方集成(IM / VCS / 开发者 Webhook)**统一经集成平台抽象**,不允许各连接器各建一套摄取/凭据/投递机制:

| 规则 | 内容 |
| --- | --- |
| 注册与绑定 | `integrations`(集成定义:`kind ∈ ('im_feishu','im_slack','im_dingtalk','vcs_github','vcs_gitlab','webhook_outbound')`、启用状态、配置)+ `integration_bindings`(工作区/项目级绑定:外部租户/仓库/频道 ↔ Mesh 工作区,携带匹配规则如"该 IM 群消息 @agent 时触发谁")。绑定经复合 FK 同租户(§6.2);**一个外部身份可绑定到至多一个工作区——规范化 `(provider, provider_tenant_key, external_ref)` 全局唯一键(R3;钉钉:corp_id + conversationId)**;VCS 对象 ↔ Mesh 实体关联真源为 `vcs_links`(R3);**外部用户身份 ↔ Mesh 用户映射真源为 `external_identities`(R3 协同 MES-4 HIGH-1 引入,R4 修订模型,R5 全局化):映射到**全局登录身份 `users.id`**(不再锁到单个 workspace-scoped 的 `member_id`——与 §6.1「同一 `users.id` 在多工作区各有 member 行」的核心模型一致,同一已认证外部账号可跨多个 Mesh 工作区参与卡片审批),**身份键为 `UNIQUE(provider, provider_tenant_key, external_user_key)`**(纳入平台租户,不同外部租户的同名 user key 不冲突);**R5 写死:本表是与 `users` 同级的全局身份表——不携带 `workspace_id` 所有权 / RLS 键(§6.1 全局身份层、§6.2 第 5 条),建链来源仅以可空审计列 `created_in_workspace_id ON DELETE SET NULL` 记录,删除建链工作区不级联删除映射(其余工作区回调照常解析);全局解链仅允许映射所属 `users.id` 本人(无 admin 旁路),工作区管理员只能撤销本工作区使用权 / 成员资格(T29 跨工作区删除 + RLS / 权限负向测试)**;**卡片回调鉴权链**:回调先由集成实例解析所属 workspace → 查本表得 `users.id` → JOIN 该 workspace 的 `members(workspace_id, user_id)` 得名册行 → 按 §6.10 权限行再校验(未映射/该用户在此工作区无名册行/无权限 → 403,审批状态不变,审计留痕)** |
| 入站事件摄取 | **复用 autopilot `webhook_events` 范式**(autopilot.md):HMAC/签名校验(恒定时间比较 + 时间戳防重放)→ `integration_events.UNIQUE(integration_id, external_event_id)` 去重(重复事件幂等 200 不再分发)→ 全程审计 → **签名无效/缺失一律拒绝(401),绝不分发**。`integration_events` 由 integrations.md owns,与 autopilot 的 `webhook_events` 同构但相互独立。**接收信道有二态、摄取管线唯一**:平台 HTTP 回调(逐请求签名)与 **Mesh 侧主动出连的长连接信道(钉钉 Stream 模式:通道层以 app_key/app_secret 鉴权,帧真确性由建连鉴权一次性确立,等价逐帧签名)**;长连接 worker 单实例互斥 + 指数退避重连 + 未 ACK 重推经去重幂等(integrations.md §3.2) |
| 入站 → 触发 | 入站消息/事件经 §6.9 触发矩阵的「外部 IM 消息触发」行入队执行(`trigger='integration'`);**入站内容一律按不可信数据处理**(§6.15:结构化隔离,不当指令执行);VCS 事件(merge/close/comment)经 autopilot 规则或内置联动规则映射到 issue 状态流转/评论 |
| 出站渠道 | 通知的 IM 投递(§6.13 `channel='im'`)、审批/交互卡片推送(§6.10 approvals 的卡片化呈现与回调)经集成平台**出站适配器**统一发送;适配器负责平台令牌(如 `tenant_access_token`)的缓存与刷新、速率退避、失败重试(台账见 `notification_delivery` / 卡片回调记 approvals `decision_comment`) |
| 出向 Webhook(开发者平台,建议-9 转正) | `webhook_subscriptions`(订阅:目标 URL + 事件类型过滤 + 状态)+ 投递台账(重试退避 / HMAC-SHA256 签名 / `Mesh-Signature`/`Mesh-Event`/`Mesh-Delivery` 头 / 投递结果);订阅级熔断(连续失败暂停 + 告警);**出向目标受 §6.16 SSRF 防护约束** |
| 凭据安全 | 集成凭据(app secret / bot token / OAuth refresh token)只存加密密文(同 `runtime_credentials.encrypted_value` 契约),响应/日志不回显;脱敏纳入 §6.16 全通道脱敏;OAuth 授权码流程 + PKCE,令牌最小 scope |
| 平台边界 | runtime 的 git checkout/push 是 **agent 执行工具**(runtime.md),**不是产品级 VCS 集成**,不替代本契约的 VCS 连接器 |

### 6.18 国际化与时区契约(唯一权威,R2 必修-E;详 Spec 见 i18n.md)

| 规则 | 内容 |
| --- | --- |
| 存储层 | **一切时间戳存储与传输一律 UTC RFC3339**(不变);用户/工作区的 locale 与 timezone 是**展示层偏好**,不落业务字段 |
| locale 协商 | 优先级:请求显式参数(`?locale=`/`Accept-Language`)→ 用户偏好 `users.settings.locale` → 工作区默认 `workspaces.settings.default_locale`(**唯一工作区 locale 真源,默认 `en`,R3**) → 系统回退 `en`;locale 取值 BCP-47(如 `zh-CN`/`en-US`),首发语言 `zh-CN` + `en`(指支持清单,不等于默认值)。**偏好真源与写接口(R3;R4 收口)**:`users.settings`(locale/theme)与 `users.timezone` 由 auth.md §2.2 登记,经 `PATCH /api/v1/users/me`(auth.md §3.1)写入,非法 timezone → `422 invalid_timezone`、不支持 locale → `422 unsupported_locale`(auth canonical,全模块对齐);`workspaces.settings.default_locale` 经 workspace.md PATCH 写入;**R4:`workspaces.default_language` 旧列已从当前模型与全部响应示例移除(存量值经独立迁移一次性写入 `settings.default_locale` 后删列,迁移说明见 workspace.md migration note)——响应只返回 `settings.default_locale`,无双写、无第二真源** |
| 字符串外部化 | **UI 文案一律经 i18n 消息目录外部化**,禁止界面硬编码可见文案;错误码(§6.14)为稳定 key,面向用户的 message 由前端按 locale 渲染(后端 message 保持英文/中性,不泄漏内部细节的原则不变) |
| 本地化渲染 | 日期/时间/数字/相对时间("3 分钟前")按 locale + 用户 timezone 渲染;**时区化仅发生在展示层**——`users.timezone`(IANA)决定用户看到的本地时间,输入的时间值解析回 UTC 存储;跨时区协作场景(截止日/周期)UI 同时标注时区 |
| 服务端职责 | API 不做文案翻译(返回稳定 key + 结构化数据);邮件摘要(comment-inbox)按收件人 locale 渲染模板;导出/报表(import-export.md/analytics.md)的本地化格式在导出时声明 locale |

### 6.19 收藏与固定(唯一权威,R2 建议-8 转正)

**统一 favorites 模型**,取代分散的置顶/收藏实现:

```sql
CREATE TABLE favorites (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  member_id    UUID NOT NULL,
  target_type  TEXT NOT NULL CHECK (target_type IN ('issue','project','view','chat_session')),
  target_id    UUID NOT NULL,          -- 多态逻辑外键(§6.2 第 4 条:行携带 workspace_id,删除一致性由软删除 + 服务层保证)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (member_id, target_type, target_id),
  FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_favorites_member ON favorites (workspace_id, member_id, created_at DESC);
```

- **语义**:收藏是**成员私有**视图(他人不可见),用于"我的任务/收件箱"侧栏的固定区与快速入口;**会话置顶并入本模型且不再保留快照(R3:`chat_sessions.is_pinned` 列已删除)**——`target_type='chat_session'` 的 favorites 行是置顶的**唯一真源**,chat-session.md 不存 `is_pinned` 兼容快照(此前"真源 + 快照"并存却无原子同步/修复协议,双真源必然漂移;列表"置顶优先"排序由服务层对请求者 favorites 计算,响应 `pinned` 为服务端快照字段,标注真源为 favorites);issue/project/view 的收藏均经本表。
- **端点**:`PUT /api/v1/favorites/{target_type}/{target_id}`(收藏,幂等)/ `DELETE` 同路径(取消)/ `GET /api/v1/favorites?target_type=`(列表,游标分页,README §6.14)。
- **目标删除**:目标软删除/物理删除后 favorites 行由服务层清理(多态逻辑外键不建物理 FK,§6.2 第 4 条);列表接口对失效目标不返回。

---

## 7. 核心跨模块流程(R2 权威版)

**「分派给 agent」端到端**(贯穿 member / issue / agent / runtime / comment-inbox):

```
人类把 issue.assignee 改为 agent(触发语义按 §6.9)
  → issue 服务在【同一事务】写 issues + issue_activity
     + outbox_events(issue.assigned)
     + outbox_events(realtime.publish,载荷 issue.updated)      ← 业务事务只写 outbox,不直接写 realtime_events(§6.6)
  → outbox relay 分发:
      ① agent 编排入口(与 @提及、autopilot 共用)按 §6.9 校验护栏/去重后,
         创建 task_executions(queued, config_snapshot 冻结 §6.11:
         agent_config_version + skill_versions + capability_grants + repo/base SHA + trigger_event_id,
         幂等键 §6.5;label_requirements + required_capabilities 为权威匹配字段 §6.4)
      ② 通知 fan-out(按 §6.13 订阅/去噪 + 唯一优先级矩阵写 notifications,带 priority)
  → realtime projector 消费 realtime.publish:以 outbox 事件 id 去重写 realtime_events
     (同事务分配频道 seq),经 Redis pub/sub 通知网关推 issue.updated / execution.queued(§6.7)
  → runtime 以 FOR UPDATE SKIP LOCKED 领取(§6.4:workspace 校验 + 服务端标签与能力匹配 +
     原子容量扣减;无匹配任务则整体回滚、容量不泄漏;建 execution_attempts #1,一次性下发 attempt 绑定凭证)
  → mesh-runtime checkout 专属分支 agent/<execution-id>/a<attempt>,以冻结 AttemptSpec 创建隔离沙箱；
     provider 只连接 task broker，出站强制走钉死 IP 的 egress gateway，日志先脱敏再流式回传
  → 工具命中 confirm_required:经机器 API 创建 approval(§6.10),当前 attempt 置
     cancelled(awaiting_approval)、租约结束、容量释放;批准 → queued → attempt #N+1
     凭 resume_context 从审批点续跑;拒绝/过期 → cancelled
  → 完成:agent 以成员身份在 issue 发结果评论(幂等键)、改状态(execution.completed;
     成功默认留运行页,失败/超时按 §6.13 critical 进收件箱)
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
- [x] **(R1)** §6 全部权威契约已在各功能 Spec 中以引用方式落地,各 Spec 无重复/冲突定义;全部 DDL 在本地 PostgreSQL 16+ 实际执行通过。
- [x] **(R1)** §9 集成测试矩阵作为各模块验收的必测项。
- [x] **(R2)** 复合 FK `ON DELETE SET NULL` 一律 PG16 列级写法(§6.2 第 6 条),补齐同租户/同父域约束与 realtime 租户键(§6.2 第 7/8 条、§6.7),并以**真实 DELETE 行为与跨租户约束测试**证明(§9 T18/T1)。
- [x] **(R2)** 不可变编号命名空间与工作区级前缀注册落地(§6.3),跨项目迁移为单事务 + 字段映射预览(issue.md/kanban.md,§9 T19/T22)。
- [x] **(R2)** claim 无任务容量回滚、capability 权威匹配、审批 attempt/租约/容量**唯一续跑协议**写死(§6.4/§6.10,§9 T20/T21)。
- [x] **(R2)** outbox → realtime **唯一写入路径**与 canonical 事件词汇注册表(§6.6/§6.7,§9 T26);bare response / `unauthenticated` / "agent.run_started" / `tool_id` 等 canonical 冲突清零(§6.11/§6.14/auth.md)。**R3:词汇校验脚本 `tests/docs/check_event_vocab.py` + CI 落地**(§6.7/T26 声称兑现)。
- [x] **(R2)** 每频道游标(§6.7/kanban.md)、小队 active assignment 唯一身份(squad.md)、approval 强约束与 blob 真源/秒传 possession(§6.10/attachment.md,§9 T23/T24)。
- [x] **(R2)** 唯一通知优先级矩阵(§6.13,§9 T25);MES-4 LOW-2/LOW-3 硬化一并处理(workspace.md/§6.16)。
- [x] **(R2·MES-2 强化轮必修)** 5 份新功能 Spec 五章齐备:onboarding.md / integrations.md(飞书·Slack·GitHub/GitLab 三连接器 + 出向 Webhook 订阅)/ import-export.md / analytics.md / i18n.md;集成平台/i18n/收藏契约先入 §6.17–§6.19;主题暗色与键盘快捷键入 §6.12;触发枚举扩 `integration`、投递渠道扩 `im`(§6.4/§6.13)。
- [x] **(R2·MES-2 建议项处置)** 7–11 转正为正式条款(§6.12 快捷键 / §6.19 收藏 / §6.17 出向 Webhook / issue·project 模板 / §11 开发者平台含 CLI 规格与 OpenAPI 版本策略);12–16 显式声明为未来规划/可选增强(§12)。
- [x] **(R2)** §4/§5 模块总览与索引更新至 20 个模块,链接有效;新增表 DDL(favorites/integrations/integration_bindings/integration_events/webhook_subscriptions/onboarding_states/data_jobs)与枚举扩展在 PostgreSQL 16 验证脚本中实际执行通过。
- [x] **(R3)** v3 复审 HIGH-1～HIGH-9 全部修订落地:agent 配置版本同租户/同 agent 重叠 FK(§6.2/agent.md,T27);调度能力与授权能力严格分型 + 入队归一算法写死(§6.4/§6.11/agent.md/skill.md/runtime.md,T28);集成外部身份全局唯一键 + scope 精确异或 + `vcs_links` 真源表(integrations.md §2.3/§2.8/§3.3,T29);IM 投递台账结构化多目的地 + error 分离(comment-inbox.md §2.8,T30);data job 源附件 RESTRICT + source hash + checkpoint + `data_job_rows` 行台账 + 逐批幂等恢复协议(import-export.md §2.2/§2.5/§3.4/§3.8,T31);§6.13 扩充为含 data job 三行的**唯一**通知矩阵,各模块只引用不自定义(comment-inbox.md/import-export.md,T25 扩至 data job/T32);`users.settings`/`users.timezone` 登记 + `PATCH /api/v1/users/me` + workspace locale 单一真源默认 `en`(auth.md/i18n.md/workspace.md,T32);Analytics 工作区聚合按请求者项目可见性过滤(private 项目不泄露,admin 全量;与 MES-4 v3 安全复审 HIGH-2 修复协同保留)+ `scope_key` 可见性缓存键(禁跨权限缓存)+ 历史指标「当前归属」口径 + `calendar_timezone` 分桶(analytics.md,T33);Onboarding 入册播种/全量 reconcile + 末步阅读证据 + 成员名册唯一入口(onboarding.md/member.md/workspace.md,T34)。
- [x] **(R3)** 3 项非阻断建议一并处理:① `tests/docs/check_event_vocab.py` 词汇校验脚本 + CI 实际落地(§6.7/T26 声称兑现,此前主干无此文件);② `chat_sessions.is_pinned` 快照列删除,置顶唯一真源为 §6.19 favorites(不再保留无同步协议的双真源);③ Analytics 明确 `calendar_timezone` 本地日历分桶语义(本地自然日不跨桶,§6.18/analytics.md §2.2.3/§2.4)。
- [x] **(R3)** §9 集成测试矩阵扩充 T27–T34;全部 R3 新增/修订 DDL、CHECK、重叠 FK 与真实 DELETE/CASCADE/RESTRICT 行为在 PostgreSQL 16 验证脚本中实跑通过(75 项断言全绿);MES-2 canonical 一致性保持(单一 owner Spec、统一词汇/错误码/分页包络、§6.13 唯一矩阵),未引入新跨 Spec 冲突。
- [x] **(R4)** 第四轮架构/UX 复审 HIGH×6 全部修订落地(均为「定义 + 可执行测试」双重闭环):① capability_grants 严格类型闭环——`permission` 必须存在/字符串/枚举合法 + 归一算法唯一实现 `normalize_capability_declarations()` 实测(§6.4/§6.11/agent.md §3.3,T28 扩展);② data job 恢复 fencing——单调 `lease_seq` + 每批锁 job 校验 owner+token+未过期 + row_key 原子占用/预分配 `target_id`,过期旧 worker 重新提交整批被拒、真实实体最终恰每行一条(import-export.md §2.2/§3.4/§3.8/§5.4,T31 扩展);③ locale 单一真源彻底收口——`default_language` 从当前模型与全部响应示例移除(迁移说明独立 migration note),响应只返回 `settings.default_locale`,i18n 错误码对齐 auth canonical `422 unsupported_locale`/`422 invalid_timezone`(workspace.md/i18n.md/§6.18,T32 扩展);④ Onboarding 语义统一——所有图/流程为入册播种 + `notification.read`,第 4/5 步严格按 `trigger_member_id` 完成(不批量污染其他成员),agent 流程只保留成员名册入口(onboarding.md §2.1/§3.1/§3.5–§3.6、agent.md §4.7,T34 扩展入册播种/成熟工作区 reconcile/未读不得完成/错误 trigger member 不得完成四场景);⑤ 外部用户身份模型对齐多工作区成员模型——`external_identities` 映射全局 `users.id`、身份键纳入 `provider_tenant_key`、回调按集成解析 workspace 再 JOIN 成员,补「同一用户跨两个 Mesh workspace」「不同外部 tenant 同 user key」测试并更新 ER 图(§6.17/integrations.md §2.1/§2.4.1/§3.2,T29 扩展;与主干安全修复的 link/unlink + 验证流程协同保留、未回退);⑥ Analytics execution 指标统一可见性 scope——关联 issue 继承项目可见性、无 issue 执行归属 agent、private agent 先过 agent 可见性,workload-B / agent stats / workspace dashboard 共用并入缓存键,§5.6 增私有项目执行与 private agent 负向测试(analytics.md §2.2.4/§2.3/§3.1/§5.6,T33 扩展)。
- [x] **(R4)** §9 集成测试矩阵 T28–T34 描述同步扩充;validation 脚本在 PostgreSQL 16 全量实跑 93 项断言全绿(退出 0);词汇校验 `tests/docs/check_event_vocab.py` 通过(无未登记事件名);MES-2 canonical 一致性保持(词汇/错误码/分页包络/唯一通知矩阵),未引入新跨 Spec 冲突;无暴露外部出处内容。
- [x] **(R5)** 第五轮架构/UX 复审 HIGH×3 全部修订落地(均为「定义 + 可执行测试」双重闭环):① **成员名册唯一入口**——agent.md §4.2 原独立「Agents」列表页改为成员名册页的「仅 Agent」筛选投影(同一路由 `/w/{ws}/members?member_type=agent` / 同一列表组件 / 同一 `[ + 新建 Agent ]` 入口,不形成第二导航/名册),章节标题、线框图与 §5.1 同步修正;新增文档结构校验 `tests/docs/check_roster_entry.py` + CI 常跑,防独立 `Agents [+ 新建]` 回归(README §6.12、§9 T35);② **external_identities 真正全局化**——既然映射目标为全局 `users.id`,本表改为与 `users` 同级的全局身份表:移除 `workspace_id` 租户所有权 / RLS 键,建链来源仅以可空审计列 `created_in_workspace_id ON DELETE SET NULL` 记录(不级联控制映射生命周期);全局解链仅映射所属 `users.id` 本人,工作区 admin 无旁路(仅可撤销本工作区使用权 / 成员资格),解链授权可执行参照 `external_identity_unlink_allowed()`;T29 扩展「删除建链工作区 A 后映射仍存在且 B 回调仍可解析」+ 全局表结构 / RLS / 解链权限负向测试(§6.1 全局身份层 / §6.2 第 5 条 / §6.17、integrations.md §2.1/§2.4.1/§2.8/§2.9/§3.1/§3.5/§5.2/§5.4);**与 `0611e35` 安全修复链(验证码 / OAuth 建链、`link-confirm`、解链即时生效、卡片回调二次权限校验)协同保留、未覆盖**;③ **Analytics 可见性谓词落入权威聚合 SQL**——`visible_executions` 统一 CTE 直接写入 §2.2.4 workload-B 与 §2.3 agent 主统计 / retry 子查询 / token 聚合(含 attempts、autopilot token 关联),明确 workspace dashboard 复用同一查询构件;T33 以**同一聚合 SQL** 对普通成员 / 项目成员 / private-agent owner / admin 四类请求者断言**最终统计值**(而非仅测 helper)(analytics.md §2.2.4/§2.3/§2.3.1/§3.1/§5.6)。
- [x] **(R5)** §9 集成测试矩阵 T29/T33 描述同步扩充、新增 T35;validation 脚本在 PostgreSQL 16 全量实跑 **100 项断言全绿(退出 0)**;词汇校验 `tests/docs/check_event_vocab.py`(96 事件 / 21 Spec 零漂移)与文档结构校验 `tests/docs/check_roster_entry.py` 通过;MES-2 canonical 一致性保持(词汇 / 错误码 / 分页包络 / 唯一通知矩阵),未引入新跨 Spec 冲突;无暴露外部出处内容。
- [ ] **(持续)** §9 全部集成测试(含 R2 T18–T26、R3/R4/R5 T27–T35 与 runtime 执行体 T36)在开发阶段作为各模块验收的必测项落实。

---

## 9. 集成测试矩阵(权威,各模块必测)

以下场景必须有自动化集成测试覆盖(真实启动服务 + 真实 PostgreSQL,不允许纯 mock):

| # | 场景 | 断言要点 |
| --- | --- | --- |
| T1 | **跨租户隔离** | 对每类资源(issues/comments/attachments/executions/approvals/views/squads/autopilots/runtimes/credentials)用 A 区凭证访问 B 区 id → 403/404;构造跨 workspace 的复合 FK 插入 → 数据库约束拒绝 |
| T2 | **并发 claim** | N(≥10)台 runtime 并发领取同一批任务:恰有任务数台成功,无重复领取;`execution_attempts` 每 execution 仅一条 claimed |
| T3 | **容量竞争** | `max_concurrent=2` 的 runtime 并发发起 5 次 claim:成功 ≤2;attempt 终态后 current_load 幂等归零(不出现负数/泄漏) |
| T4 | **requeue 审计** | 领取后杀 runtime → reaper 回收 → 新 attempt 领取成功;旧 attempt 行(runtime/claimed_at/日志引用)完整保留 |
| T5 | **outbox 崩溃恢复** | 业务提交后、relay 分发前杀 relay 进程 → 重启后事件仍被投递(执行被创建、通知生成、实时事件可重放),无丢失;另在真实数据库中注入进程内 relay 常驻协程意外取消/返回 → 看门狗在 1～2 tick 内记录结构化错误并自愈重启,`delivery_attempts=0` 的 pending 积压被 drain,worker 进程和健康兄弟任务不中断 |
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
| T16 | **仓库 checkout 白名单(H1)** | checkout `config_snapshot.repo.url` 不在 `allowed_repos` 白名单内 → 403;`repo_token` 用于白名单外仓库 → 拒绝;平台托管 runtime checkout 私网 / 元数据地址 → 拒绝 |
| T17 | **全通道脱敏(C2)** | agent 尝试把已知 secret 值写入评论 / 附件产出物 → 内容被拦截、不发布、触发安全告警;日志中 secret 命中 → `***` 替换(评论 / 附件 / 日志三通道均覆盖) |
| T18 | **真实 DELETE 行为(R2)** | 不止建表成功——实际执行 DELETE 并断言:① 物理删除/软清理 member 行时,`issues.assignee_id` 经 `ON DELETE SET NULL (assignee_id)` 仅置空引用列,`workspace_id` 保持非空、行不报错;② 删除 project 时 `issues.project_id` 置空而 `identifier` 不变;③ 删除被 `issues.status_id` 引用的状态被 `RESTRICT` 拒绝;④ 删除父 issue 级联子 issue;⑤ 删除 workspace 级联其全部租户数据。所有 `ON DELETE SET NULL` 复合 FK 逐一覆盖 |
| T19 | **不可变编号与跨项目迁移(R2)** | `WEB-1` 迁入已有 `APP-1` 的项目:`identifier_namespace_key/number/identifier` 不变、`project_id` 变更、`UNIQUE(workspace_id, identifier_namespace_key, number)` 不违约;迁入/迁出/删除项目后历史 identifier 指向不变;前缀注册表排他:项目 key 与收件箱前缀(含 `retired` 历史前缀)冲突被拒,变更收件箱前缀后旧前缀永久保留、历史 issue 不重编号 |
| T20 | **claim 容量回滚与能力匹配(R2)** | ① runtime 有容量但队列中**无匹配任务** → claim 返回 204 且 `current_load` **保持不变**(事务整体回滚,无泄漏);② `required_capabilities=["ffmpeg"]` 的执行仅被 `capabilities` 含 `ffmpeg` 的 runtime 领取(能力不匹配的 runtime 跳过该任务,标签条件并行生效);③ 并发 5 抢 2 容量恰成功 2,终态后 `current_load` 幂等归零 |
| T21 | **审批续跑唯一协议(R2)** | running 执行命中 `confirm_required` → 创建 approval(同 subject 仅一个 pending,部分唯一索引兜底)+ 当前 attempt 置 `cancelled(awaiting_approval)`、租约结束、`current_load` 释放;批准后执行回 `queued`,新 attempt #N+1 凭 `resume_context` 从审批点续跑(已完成步骤不重做);拒绝/过期 → `cancelled(approval_rejected/approval_expired)`;审批挂起期间 runtime 失联不产生卡死(无在途租约) |
| T22 | **跨项目迁移事务(R2)** | 看板 `group_by=project` 拖拽:无确认的 move 返回 422 要求确认;确认后单事务完成 `project_id` 变更 + 项目私有 status 映射(→ 目标项目同 category 默认 status)/ 项目私有 milestone/cycle/label/自定义字段值清除,工作区级字段保留;迁移后不存在"当前项目 + 旧项目私有字段"脏状态;`issue.project_changed` 事件携带映射/清除清单 |
| T23 | **小队 active assignment 唯一身份(R2)** | 同一 leader 领导 S1/S2 两小队:issue 先派给 S1 再派给 S2(assignee 值不变)→ S1 根任务级联取消、S2 根任务建立(**不是 no-op**);重复派给 S1 = no-op 返回既有分派;`issue_squad_assignments` 部分唯一索引保证每 issue 至多一条 active;leader 更换 → active 分派与 `issues.assignee_id` 同事务更新;leader 离队且无替补 → 根任务 blocked 并通知 |
| T24 | **blob 真源与秒传 possession(R2)** | ① 调用者对某 `content_hash` 无任何可读 attachment 时,"秒传"短路被拒(必须完整上传,上传本身即持有证明);② 对已可读 blob 秒传成功(新建独立 attachments 行指向同一 `attachment_blobs` 行);③ `ref_count` 原子维护:删除共享 blob 的其中一条附件,另一条不受影响;`ref_count=0` 后对象才被 GC;④ 并发秒传/上传同一 hash 由 `UNIQUE(workspace_id, content_hash)` 串行化,不产生重复 blob 行 |
| T25 | **通知优先级矩阵(R2;R3 扩至 data job)** | 执行成功默认**不进收件箱**(留运行页),失败/超时进收件箱且**穿透 quiet hours** 并重置同组未读;执行成功订阅后才进箱且不重置已读组;审批请求/安全隔离为 critical;cancelled 不通知发起者;**data job 成功默认不进收件箱、`completed_with_errors` 进箱(normal)、`failed` 为 critical(穿透 + 重置)**(R3);runtime.md/comment-inbox.md/import-export.md 的分发与本矩阵逐事件一致,各模块 Spec 无自定义分级 |
| T26 | **realtime 唯一路径与词汇(R2)** | ① 业务提交后、projector 登记前杀 projector → 重启后事件仍被登记且**频道 seq 无缺口/无重复**(`UNIQUE(outbox_event_id)` 去重);② 跨 workspace 的频道订阅/事件读取被 RLS 与复合 FK 拒绝;③ 文档级词汇校验:`docs/specs/**/*.md` 引用的全部事件名命中 §6.7 注册表,无未登记名("agent.run_started" 之类未登记运行起始帧名不允许存在);**校验脚本 `tests/docs/check_event_vocab.py` 随 CI 常跑(R3 落地),不通过即 CI 失败** |
| T27 | **agent 配置版本同租户/同 agent 约束(R3,HIGH-1)** | `agent_config_versions` 携带 `workspace_id`,`agent_id`/`changed_by` 为同租户复合 FK:跨租户 agent 版本、跨租户审计成员 INSERT 被拒;`agents.active_config_version_id` 经重叠复合 FK `(workspace_id, id, active_config_version_id) → agent_config_versions(workspace_id, agent_id, id)` 强制同父域——**把 A agent 的 active 指针指向 B agent 的版本、或指向别工作区的版本,均被数据库拒绝** |
| T28 | **能力字段严格类型与入队归一(R3,HIGH-2;R4 HIGH-1 扩展)** | ① `task_executions.required_capabilities` schema CHECK 拒绝任何非字符串元素(对象混入 → 拒绝,杜绝 claim `<@` 永不命中导致任务永久无法领取);② `config_snapshot.capability_grants` CHECK 强制 `[{capability,permission}]` 对象数组(permission ∈ read_only/write/confirm_required),**R4:`permission` 缺失 / 非字符串 / 非法枚举一律 CHECK 拒绝(快照层 permission 必填)**;③ **R4:以混合字符串/对象声明调用归一算法的唯一可执行实现 `normalize_capability_declarations()`**(agent.md §3.3 的参照实现),断言字符串条目补默认 `confirm_required`、去重、同一 capability 取最严格权限(`confirm_required > write > read_only`)、字典序排序,归一产物通过严格类型 CHECK 且 `required_capabilities <@ runtimes.capabilities` claim 命中;④ 混用声明直接写调度字段在测试中判失败;**R4:非法声明(非法 permission / 非字符串非对象条目 / 非数组输入)被归一实现拒绝(capability_invalid,422)** |
| T29 | **集成外部身份全局唯一 + scope 异或 + vcs_links + external_identities 多工作区模型(R3,HIGH-3;R4 HIGH-5 扩展)** | ① 两个工作区各自的集成实例抢绑同一外部身份(同 provider + 平台租户 + 外部对象)→ 全局键 `UNIQUE(provider, provider_tenant_key, external_ref)` 拒绝;② `scope='workspace'` 带 `project_id`、`scope='project'` 缺 `project_id` 均被精确异或 CHECK 拒绝;③ **删除项目 → 项目级绑定 `ON DELETE CASCADE` 一并删除**,不产生置空后违反 CHECK 的不可达态,项目删除不因绑定存在而失败;④ `vcs_links` 外部对象 active 部分唯一键(同 PR 重复 active 关联被拒)、同租户复合 FK(集成删除级联删关联)、状态索引可用;**R4:⑤ `external_identities` 映射全局 `users.id`、身份键 `UNIQUE(provider, provider_tenant_key, external_user_key)`——同一已认证外部账号跨两个 Mesh 工作区参与(单映射行,回调按集成解析 workspace 后 JOIN 各自 `members(workspace_id, user_id)` 均解析成功);⑥ 不同外部租户同 user key 可并存;⑦ 同一外部账号重复映射(即使指向不同用户)被全局键拒绝;⑧ 用户注销级联删除映射(卡片点击回落 403)**;**R5(HIGH-2 全局化):⑨ **删除建链工作区后全局映射仍存在**(`created_in_workspace_id` 经列级 SET NULL 置空,无 CASCADE),其余工作区回调经名册 JOIN 仍可解析;⑩ **全局表结构/RLS 负向**:`external_identities` 无 `workspace_id` 列、无对工作区的 CASCADE FK(information_schema 断言)、无 workspace RLS 策略(pg_policies 负向);⑪ **解链权限负向**:`external_identity_unlink_allowed()` 仅比对所属 `users.id`——所属用户经任一工作区成员行解链放行,工作区 admin(非所属用户)与普通成员一律拒绝(无 admin 旁路);管理员撤销成员资格仅使该工作区回调回落 403,全局映射与其他工作区不受影响** |
| T30 | **IM 投递台账多目的地(R3,HIGH-4)** | 同一通知可并发投递多个 IM 目的地(Slack + 飞书 + 同平台多绑定),每目的地一行 `notification_delivery`(`UNIQUE(notification_id, channel, destination_key)` 幂等);`in_app`/`websocket` 的 `destination_key=''` 保持每通知每渠道一行;**IM 平台/外部目标由结构化列(`provider`/`external_target`/`integration_id`/`binding_id`)表达,`error` 字段只记失败原因**(扫描确认不含路由数据);集成/绑定删除经列级 SET NULL 置空路由列、台账保留 |
| T31 | **data job 删除与故障恢复协议(R3,HIGH-5;R4 HIGH-2 扩展:fencing + 实体副作用幂等)** | ① 作业存续期间删除导入源附件被 `ON DELETE RESTRICT` 拒绝(409;消除 SET NULL 与「import 必有源」CHECK 互斥);② 导入分批执行中杀 worker → reaper 在 `lease_expires_at` 过期后回收租约(作业**不因 running 守卫永久卡住**),新 worker 凭 `checkpoint.last_committed_batch` 续跑;③ **R4:claim 领取即单调 fencing token `lease_seq + 1`;每批事务先锁 job 行(`FOR UPDATE`)并校验 `lease_owner + lease_seq + 未过期`,不符即整批拒绝回滚**;④ **R4:行台账先原子占用 `row_key`(`ON CONFLICT (job_id,row_key) DO NOTHING` + 预分配 `target_id`),仅占用成功者创建实体**——重放已提交批次占用冲突即跳过实体创建,**不重复创建 issue/project**;⑤ **R4:过期旧 worker「复活」后重新领取场景——其持过期 fencing token 的批提交被整体拒绝回滚,真实 issue/project 最终每行恰一条,计数/checkpoint/台账三方一致**;⑥ 源文件在 validate 后被替换 → `source_content_hash` 校验失败拒绝续跑;⑦ `data_job_rows` CHECK:created/updated 必带 target、failed 必带 error |
| T32 | **偏好真源与 locale 单一权威(R3,HIGH-7;R4 HIGH-3 扩展:模型/响应无双真源)** | ① `users.settings`(`locale`/`theme`)与 `users.timezone` 可经 `PATCH /api/v1/users/me` 写入并回读(校验:非法 timezone `422 invalid_timezone`、不支持 locale `422 unsupported_locale`、theme ∈ light/dark/system——auth canonical,i18n/workspace 对齐,不再用 `400 validation_error`);② workspace locale 唯一真源 `workspaces.settings.default_locale`(默认 `en`,与 i18n.md/§6.18 一致),**R4:`default_language` 列已从当前模型删除(information_schema 无此列),响应只返回 `settings.default_locale`,workspaces 无任何顶层 locale 列**;③ `default_locale` 经 settings 按键浅合并写入并可回读;④ locale 协商链按 §6.18 逐级回退;迁移说明独立于 workspace.md migration note(存量值一次性写入 `settings.default_locale` 后删列,无双写期) |
| T33 | **Analytics 可见性缓存键与口径(R3,HIGH-8;协同 MES-4 HIGH-2;R4 HIGH-6 扩展:execution 可见性 scope)** | ① 工作区级聚合(含 `/dashboards/workspace`)**按请求者项目可见性过滤**——非 private 项目成员得不到该项目统计量(admin/owner 见全工作区聚合);显式多项目聚合含不可见项目 → 整体 403(不部分返回);② `analytics_snapshots.scope_key` 纳入缓存唯一键:`ws_admin`(admin 全量)与 `projects:<hash>`(成员可见集合)快照分行并存,**跨权限缓存绝不共享**(普通成员查询不命中 ws_admin 行,可见性变更后旧键自然失效);③ burndown/velocity 按**当前归属口径**计算(响应 `scope_caliber='current_attribution'`),issue 移入/移出按当前集合重算,不声称还原历史归属;④ `calendar_timezone` 入维度指纹(不同时区分桶缓存不共享,本地自然日不跨桶);**R4:⑤ execution 指标统一可见性 scope(`analytics_exec_visible_to` 谓词,workload-B / agent stats / workspace dashboard 共用)——关联 issue 的执行继承项目可见性(普通成员看不到不可见 private project 的执行计数/时长/token,堵侧信道);无 issue 的 manual/chat/integration 执行归属 agent、无项目侧信道;private agent 先过 agent 可见性(仅 owner/admin 可见其统计);⑥ execution 类指标缓存键纳入同一 scope:`ws_admin` 与 `exec:p<可见项目集 hash>:a<可见 agent 集 hash>` 物理分行、跨权限绝不共享**;**R5(HIGH-3):⑦ 权威聚合 SQL 真实闭环——§2.2.4 workload-B 与 §2.3 agent 主统计 / retry 子查询 / token 聚合(含 attempts、autopilot token 关联)四段落地 SQL 均内联 §2.3.1 `visible_executions` 统一 CTE(workspace dashboard 复用同一构件);T33 以同一聚合 SQL 对普通成员 / 项目成员 / private-agent owner / admin 四类请求者断言最终统计值(executions·succeeded / running·queued / retry_rate / total_tokens),而非仅断言 helper 返回值** |
| T34 | **Onboarding 证据与末步判定(R3,HIGH-9;R4 HIGH-4 扩展:四真实场景)** | ① **入册播种**:人类成员入册事务同事务播种清单 + 五步(步骤 1 即完成),agent 成员不播种;② **成熟工作区 reconcile**:受邀进入成熟工作区(已有 agent 成员/issue/历史执行)→ 建状态全量回查历史事实,步骤 2–4 按**成员自身历史**带证据直接完成,**不永久 pending**——**未触发过执行的成员步骤 4 保持 pending(不按「工作区首个执行」给未触发者批量完成、不伪造证据)**;③ **未读不得完成**:末步仅由 `notification.read` 驱动,相关通知未读 → 末步保持 pending、aha 不置位(不再凭「workspace 存在 completed 执行 + agent 评论」对全体成员批量完成);④ **错误 trigger member 不得完成**:末步严格按 `trigger_member_id` 完成——读了「他人触发的执行」的 agent 回评通知不得完成本人末步;触发者本人阅读后完成,`evidence` 持久化 `{execution_id, comment_id, notification_id, trigger_member_id}` 四元组、aha 仅为触发者置位;⑤ agent 创建入口唯一为成员名册(README §6.12,Settings 无独立 Agents 名册;onboarding 所有图/流程统一为入册播种 + `notification.read`) |
| T35 | **成员名册唯一入口文档结构校验(R5,HIGH-1 防回归)** | 文档级结构校验:`tests/docs/check_roster_entry.py` 扫描 `docs/specs/**/*.md`,① 线框图中页面标题为 `Agents` 且带不带 Agent 后缀的 `[+ 新建]` 的独立列表页;② 未与「筛选投影 / 不存在 / 不维护」等标注同行的「Agent 列表页」表述;③ 导航 / 信息架构图中 `Agents` 行携带 `[+ 新建]` 入口——三类独立 Agents 名册 / 第二创建入口回归均判失败。**校验脚本随 CI 常跑(R5 落地),不通过即 CI 失败**;成员名册页「仅 Agent」筛选投影(标题「成员 Members」+ `[ + 新建 Agent ]`)不命中 |
| T36 | **真实 runtime 执行体安全红线** | 在真实 Linux namespace/cgroup/network、`max_concurrent>=2` 下执行 runtime-executor.md §5.2 全矩阵且禁止 mock/skip：attempt A 不可读写 B 的文件、进程、内存、socket、凭证；沙箱不可读 daemon env/内存/token/control socket；恶意仓库 MCP/settings/hooks/项目指令不加载；无 broker/approval 无法 push、跨资源写或非白名单出站；可信解析→全 IP 过滤→建连钉死并逐跳重验，DNS rebinding/IPv4-mapped/元数据跳转均拒绝；清理后新 attempt 零残留；日志/result/diff/评论/附件全通道零 secret。任一失败阻断受保护分支和发布 |

---

## 10. 性能基准方法(权威)

一切 Spec 中的 P95/时延指标**仅在以下基准下构成验收标准**,各模块引用本节而非各自臆测:

| 维度 | 基准 |
| --- | --- |
| 硬件 | 8 vCPU / 32 GB RAM / NVMe SSD 单机;PostgreSQL 16、Redis 7,默认配置(shared_buffers=8GB) |
| 数据规模 | 工作区 50 个;单工作区 issue 10 万、成员 1 万、评论 100 万、通知 500 万、task_executions 100 万(含 attempts 300 万)、附件 10 万;**数据作业(import-export.md §5)单作业 ≤ 10 万行、全程流式(源解析 / 导出生成 / 错误报告皆不全量载入内存),导出产物 ≤ 512 MB / ≤ 20 万行(`data_job_export_max_rows/bytes`)** |
| 并发 | k6 50 VU 稳态 + 100 VU 峰值;WebSocket 2000 并发连接 |
| 冷热缓存 | 冷:重启数据库后首跑;热:二跑取数;指标标注冷/热 |
| 测试方法 | 压测脚本随仓库提供(`tests/perf/`),CI 夜间跑;P95 取 5 分钟窗口 |
| 代表性 EXPLAIN | 自定义字段过滤等关键查询须在上述数据分布下附 `EXPLAIN (ANALYZE, BUFFERS)` 结果,证明命中 §6/模块定义的复合/部分索引(见 label-property.md) |

示例基准目标(热缓存):issue 列表/分组 P95 < 500ms(10 万 issue)、claim P95 < 100ms(1000 runtime 并发)、未读计数 P95 < 50ms、实时事件端到端 P95 < 1s(WS 在线)、日志尾部增量 P95 ≤ 2s。

---

## 11. 开发者平台契约(R2 新增,建议-11 转正)

### 11.1 CLI 命令规格

Mesh 提供官方命令行工具 `mesh`(与 Web 同源 REST API,经 `api_tokens` 鉴权,auth.md),命令族与语义如下(完整参数以 `mesh <command> --help` 为准):

| 命令族 | 命令 | 说明 |
| --- | --- | --- |
| 鉴权与配置 | `mesh auth login` / `logout` / `status` | OAuth 设备码或 PAT 登录;令牌存本地受保护配置(0600),**不进命令行参数/历史** |
| | `mesh config set/get` | API 基址、默认工作区、输出格式(`--output json\|table`) |
| 工作项 | `mesh issue list/get/create/update/status/comment` | 与 issue.md §3 端点一一对应;`create --title --description-file <path> --priority --assignee --project`;长文本一律 `--description-file`/`--content-file`(避免 shell 转义吞参) |
| | `mesh issue children <id>` / `dependencies` | 结构与依赖查看 |
| 项目 | `mesh project list/get/create` | project.md §3 对应 |
| 成员与 agent | `mesh member list` / `mesh agent list/executions` | 名册与运行历史 |
| 运行与 runtime | `mesh runtime register/status`(**控制台侧建影子记录 + 安装引导** / **人工排障只读**,均为控制台 API)/ `mesh execution get/logs/cancel` | runtime 协议的 CLI 形态;日志 `--follow` 流式。**两命令职责收口(MES-77 R2/C2,以 cli.md §1.3 / runtime.md 为准)**:`register` = 控制台侧建 runtime 影子记录(`POST /workspaces/{ws}/runtimes`)并返回一次性激活码 + 安装命令,引导部署独立二进制 `mesh-runtime`;`status` = 用户凭证调控制台 `GET /workspaces/{ws}/runtimes/{id}` 只读取 daemon 已上报的状态/最近心跳/负载,供人工排障;**CLI 无任何心跳命令**——真实守护进程的注册激活/心跳/领取/上报一律且仅由独立二进制 `mesh-runtime` 以 `mesh_rt_` 令牌走 `/api/v1/daemon/*`(runtime.md §3.2/§3.5,控制台域与机器域零混用,MES-76 H8) |
| 导入导出 | `mesh export issues --project <key> --format csv\|json -o <file>` / `mesh import issues --file <path> --dry-run` | import-export.md 对应 |
| 通用 | `--workspace <slug>` 覆盖默认工作区;`--idempotency-key` 透传幂等键(§6.14) | 所有写命令支持 |

**CLI 约定**:输出 `table`(人类)与 `json`(脚本,字段与 REST 包络一致)双模式;错误经统一错误信封(§6.14)并以非零退出码区分错误类别(1 通用 / 2 鉴权 / 3 校验 / 4 冲突);版本策略与 API 同(§11.2)。

### 11.2 API 文档、版本策略与 SDK

- **OpenAPI**:REST API 以 **OpenAPI 3.1** 机器可读规格随仓库发布(`docs/api/openapi.yaml`,由 FastAPI 自动生成并以 Spec 为准人工校准),覆盖 §6.14 包络/错误码/分页与各模块端点;每个端点含请求/响应 schema 与错误示例。
- **版本策略**:URI 版本化(`/api/v1`);**破坏性变更必须升 `/api/v2` 并与 v1 并存一个弃用周期**(≥ 3 个月,`Deprecation`/`Sunset` 响应头公告);非破坏性新增(新字段/新端点)在 v1 内演进,旧客户端忽略未知字段。
- **SDK**:**本期不提供官方 SDK,明确列为后续规划**;当前以 REST + OpenAPI + CLI 为开发者接口;第三方 SDK 可基于 OpenAPI 生成(许可与声明见仓库)。

---

## 12. 未来规划与显式延期声明(R2 新增,MES-2 强化轮建议项处置)

以下能力经评审讨论后**显式声明为可选增强/未来规划**(非本期范围),在此记录决定,避免被误读为遗漏:

| # | 能力 | 决定 | 说明 |
| --- | --- | --- | --- |
| 12 | Feature Flags / 灰度开关系统 | **基础工作区开关已产品化；高级灰度未来规划** | `workspaces.settings.feature_flags.autopilot` 已有 admin 设置 UI、类型校验、导航/命令/路由条件呈现，具体契约见 workspace.md §2.2。百分比灰度、人群规则、实验分流和集中运营平台仍属后续工程基建 |
| 13 | SSO/SAML + SCIM 企业目录 | 未来规划(企业版) | 第三方 OAuth + TOTP 2FA(auth.md)覆盖当前需求;SAML SSO 与 SCIM 账户同步列企业版规划 |
| 14 | 提醒 Snooze / 重新提醒 | 可选增强,默认不实现 | due_date/里程碑/邀请过期提醒已有(issue/project/workspace);Snooze 保持 comment-inbox §1 已声明的可选增强 |
| 15 | 路线图 / 时间线 / 甘特视图 | **确认本期不做**(Leader 决定) | 维持 kanban.md 已声明的 YAGNI 延期;`views.layout` 保留 `timeline` 枚举占位,不实现 UI;后续立项时基于既有视图投影模型扩展 |
| 16 | Triage / 分诊队列 | 可选增强 | `backlog` 状态 + 收件箱(issue.md/comment-inbox.md)已覆盖入队与待分类语义;专门分诊队列保持可选 |
| 17 | 周期结束未完成 issue 顺延/退回待办机制 | 跨模块延期 → **issue.md** | project.md §1.2.5/§4.4/§5.1 承诺的周期结束处理依赖 issue 的 `cycle_id` 与状态流转;project 模块仅承载 `cycles.auto_roll` 生成下一周期(已实现),未完成项的实际搬运属 issue.md 增量,通知随 comment-inbox.md |
| 18 | 项目相关通知事件登记(health 变差 / 里程碑临近逾期 / 周期开始结束 / 加入项目) | 跨模块延期 → **comment-inbox.md** | project.md §4.5 列出的四类通知触发点的事件类型码与订阅/去噪矩阵由 comment-inbox.md(通知唯一 owner)登记到 §6.7 注册表;project 模块仅产出 outbox 业务事件,不直接登记通知类型码 |
| 19 | 项目分组(项目分组/分组数据模型) | 跨模块延期 → **label-property.md** | project.md §1.2.1「项目分组(可选)」无独立数据模型;分组语义复用 label-property.md 的标签/分组作用域(`project` 作用域),不另建 `project_groups` 表 |
| 20 | 里程碑时间线/甘特可视化 | 确认延期 → **kanban.md**(与 §12#15 同口径) | project.md §1.2.3/§4.2 的里程碑「时间线展示」与 kanban.md §12#15 的路线图/甘特 YAGNI 延期统一:project 仅提供里程碑数据 + `overdue` 派生态,时间线/甘特 UI 待 kanban.md 立项,二者不矛盾(数据先行、视图延后) |
| 21 | docs / 静态站点 / 公开文档能力 | 未来规划(工程基建) | 无产品级 docs 站点能力;Spec 即文档(`docs/specs`),公开文档站点偏工程基建,未产品化 |

---

*文档版本:Draft v3 / R4 修订(2026-07-25,含第四轮架构/UX 复审 HIGH×6 收口;R3 修订 v3 复审 HIGH×9 + 3 项非阻断建议、R2 修订与 MES-2 强化轮必修 A–F、建议 7–16 处置见上方要点)。后续任何 Spec 变更须在对应功能文件内修订;涉及公共契约的变更必须先改本章并同步引用方。*
