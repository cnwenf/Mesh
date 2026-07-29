# 集成平台(Integrations)功能 Spec

> **所属层**:平台能力层(统一第三方集成抽象 —— IM / VCS / 开发者 Webhook 的"同一套摄取、凭据、投递机制";本 Spec 是 README §6.17 集成平台契约的**详 Spec**)。
>
> **依赖的其他 Spec**:
> - `autopilot.md`:**入站事件摄取复用其 `webhook_events` 范式**(autopilot.md §2.5/§3.2:HMAC/签名恒定时间比较 + 时间戳防重放 → 事件 ID 去重 → 全程审计 → 签名无效/缺失一律 401 绝不分发;被拒事件用 `rejected:` 前缀独立去重命名空间防预占)。本模块 `integration_events` 与 autopilot 的 `webhook_events` **同构但相互独立**(autopilot.md §2.5 已声明)。
> - `agent.md`:入站 IM 消息匹配到的 agent 经 `agents.id`(复合 FK)绑定;入队执行的 agent 配置/能力边界由 agent 决定。
> - `runtime.md`:入站触发的运行落地为 `task_executions`(`trigger='integration'`,README §6.4/§6.9);**runtime 的 git checkout/push 是 agent 执行工具,不是本模块的 VCS 集成**(边界见 §1.3)。凭据加密密文契约同 `runtime_credentials.encrypted_value`(README §6.16)。
> - `comment-inbox.md`:出站 IM 通知经 `notification_delivery.channel='im'` 台账(README §6.13),由本模块出站适配器实际投递;VCS 联动可向 issue 发评论(`comments`)。
> - `issue.md`:VCS 连接器把 commit/PR/branch 关联到 issue,经 `identifier`(`WEB-123`)解析自动关联;合并/关闭驱动 issue 状态流转。
> - `auth.md`:管理端点 RBAC、审计、限流;OAuth 授权码 + PKCE 流程;入站回调端点用平台签名校验(非 Bearer)。
> - `workspace.md` / `member.md` / `project.md`:`workspace_id` 隔离根;`created_by`/`bound_agent_id` 引用 `members`/`agents`(复合 FK);绑定可下放到 `project` 级(复合 FK)。
>
> **被依赖方**:通知管线(`notification_delivery.channel='im'` 的实际发送方,README §6.13)、`approvals` 卡片的 IM 呈现与回调(README §6.10)、autopilot 的 VCS 事件联动(autopilot.md `webhook_received` 触发器可消费本模块摄取的事件)。

---

## 全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)

1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `UUID`(`gen_random_uuid()`);所有表含 `created_at` / `updated_at`(`TIMESTAMPTZ`,默认 `now()`,UTC);软删除统一 `deleted_at TIMESTAMPTZ NULL`。
2. **多租户(README §6.2)**:凡可能被跨模块引用的表(`integrations`/`integration_bindings`/`integration_events`/`webhook_subscriptions`)除 `PK(id)` 外建 **`UNIQUE (workspace_id, id)`**;引用方一律存 `workspace_id` 并建**复合 FK** `(workspace_id, <ref>_id) → 目标表 (workspace_id, id)`,使跨租户引用在 INSERT 时即被拒绝。复合 FK 的 `ON DELETE SET NULL` 一律 **PG16 列级写法 `ON DELETE SET NULL (<引用列>)`**(README §6.2 第 6 条);不可悬空的审计/作者引用用软删除 + `ON DELETE RESTRICT`。
3. **成员(README §6.1)**:`created_by` 引用 `members.id`(复合 FK,人或 agent 判别 JOIN `members.member_type`);`bound_agent_id` 引用 `agents.id`(复合 FK)。**本模块各表不存 `*_type`/`*_kind` 判别列**。
4. **投递 / 幂等(README §6.5)**:一切外部可见副作用携带稳定幂等键。入站触发的执行入队幂等键 = `sha256(agent_id | integration_binding_id | external_event_id)`(README §6.9);出向 Webhook 投递幂等键 = `sha256(subscription_id | event_ref)`(README §6.5「出向 Webhook/推送」键的集成平台形态)。
5. **队列(README §6.6)**:入站摄取后的执行入队 / 通知 / 实时事件 / VCS 联动副作用**一律经 transactional outbox**;摄取处理器是 relay 消费方,**不是进程内事件总线**,不在业务事务外直接派生。
6. **实时(README §6.7)**:统一实时契约(频道内 `seq`、`realtime_events` 持久重放、唯一写入路径 outbox→projector、`resume_from`/`resync_required`)。本模块仅使用**已登记事件名** `integration.updated` / `integration.event_ingested`(README §6.7 事件词汇注册表「平台能力」域),不使用未登记名。
7. **触发(README §6.9)**:入站 IM 消息触发走触发矩阵「外部 IM 消息触发」行(`trigger='integration'`);未绑定/未匹配 agent 的外部消息**不触发运行,仅审计留痕**。
8. **审批(README §6.10)**:审批/交互卡片的 IM 推送与回调经本模块出站适配器,但**审批实体与决定权统一在 `approvals`**;卡片回调是 `POST /approvals/{id}/approve|reject` 的触发面,不另设审批存储。
9. **通知(README §6.13)**:IM 渠道仅为**出站增强**,站内收件箱永远是通知真源;`channel='im'` 的优先级/穿透/去噪规则按唯一通知优先级矩阵,本模块不另行定义分级。
10. **接口(README §6.14)**:基础路径 `/api/v1`;成功包络 `{"data":...}` / 游标分页 / 错误信封 / 幂等写 / HTTP 语义为唯一权威。**入站回调端点除外**(平台签名校验,非 Bearer;响应为与外部平台约定的裸 JSON,不套成功包络——与 autopilot.md §3.2 入站 Webhook 端点同例)。
11. **不可信内容(README §6.15)**:入站消息/载荷进入 agent 上下文一律视为**数据而非指令**,结构化隔离;高风险动作走 `confirm_required`。
12. **凭据 / SSRF(README §6.16)**:集成凭据只存加密密文(同 `runtime_credentials.encrypted_value`),响应/日志不回显;全通道脱敏;出向目标 https-only + 私网地址段拒绝(SSRF 防护);用户可控 URL 仅允许 `https`。

---

## 1. 功能描述

### 1.1 模块定位

集成平台是 Mesh 对外的**统一抽象层**:把"第三方系统的事件进得来(入站摄取)、Mesh 的通知/审批出得去(出站适配)、开发者能订阅 Mesh 事件(出向 Webhook)"这三件事收敛到**同一套**注册/绑定模型、凭据保险箱、摄取管线与投递台账上,而不是每个连接器各造一套。

**核心设计:抽象与连接器分离**。平台提供四个通用能力——

1. **集成注册 / 绑定模型**:`integrations`(一个连接器实例:类型 + 非密配置 + 凭据引用)与 `integration_bindings`(把外部身份——IM 群/频道、VCS 仓库——绑定到工作区或项目,携带匹配规则与目标 agent)。
2. **入站事件摄取**:统一管线 = 平台签名校验 → `integration_events.UNIQUE(integration_id, external_event_id)` 去重 → 全程审计 → 匹配绑定 → 经 §6.9 触发矩阵入队(`trigger='integration'`)。**复用 autopilot `webhook_events` 范式**(autopilot.md §2.5/§3.2)。
3. **出站渠道适配**:通知的 IM 投递(README §6.13 `channel='im'`)与审批/交互卡片推送(README §6.10 卡片化)经统一出站适配器发送;适配器负责平台令牌(如飞书 `tenant_access_token`)的缓存与刷新、速率退避、失败重试,台账落 `notification_delivery`。
4. **出向 Webhook 订阅**(开发者平台,README §6.17 立约):`webhook_subscriptions`(目标 URL + 事件类型过滤 + 状态)+ 投递台账(重试退避 / HMAC-SHA256 签名 / 投递结果)+ 订阅级熔断。

连接器是抽象的**具体实现**:本期落地 **飞书/Lark、Slack、钉钉/DingTalk**(IM)与 **GitHub/GitLab**(VCS)三类公开集成目标平台,以及**出向 Webhook** 这一通用开发者通道。新增一个连接器 = 实现"签名校验 + 载荷归一 + 出站适配"三个适配点,无需触碰摄取/去重/凭据/投递的通用机制。

> **钉钉连接器与三项交互能力(MES-82)**:钉钉连接器除通用摄取/出站外,额外落地三项 IM 交互能力,均由平台通用机制承载、不作连接器私有旁路:
> 1. **emoji 确认接收(ack)**:入站任务消息摄取成功后**立即回一条轻量确认消息**(默认 `✅ 已接收,处理中`)再异步执行。钉钉开放平台**不提供**对任意消息添加 emoji 回应(reaction)的机器人 API,故"emoji 确认"以确认消息实现等价语义(见 §3.8);飞书/Slack 连接器同此语义(平台统一,不因平台有 reaction API 而分叉)。
> 2. **`/stop` / `/btw` 指令(命令平面)**:入站文本命中命令前缀即走**控制平面即时处理**,不参与任务排队:`/stop` 取消发起人在本会话的在途执行与排队项;`/btw` 向本会话正在处理的执行追加补充上下文(不可信数据,§6.15)。命令注册表可扩展(见 §3.7)。
> 3. **新消息自动排队**:入站任务消息经**会话级 FIFO 队列**(`integration_message_queue`,§2.10)按序串行派发——同一会话至多一个处理中的执行(部分唯一索引数据库级保证),新消息不丢失、不并发冲突,队列状态/位置可查询。

> **平台与连接器的关系**:`integrations.kind` 决定走哪个连接器适配点;`config`(非密 JSONB)存平台特定配置(app_id、外部租户标识、回调基址等);凭据(app secret / bot token / OAuth refresh token)**不进 config**,只存加密密文引用(锚点 §12,README §6.16)。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| P1 | 集成注册 | 在工作区创建某类连接器实例(`kind`),填非密配置,凭据走 OAuth 授权码 + PKCE 或粘贴 token(只存密文) | 管理员在「集成」页连接飞书企业自建应用,完成 OAuth 授权 |
| P2 | 绑定模型 | 把外部身份(群/频道/仓库)绑定到工作区或项目,配匹配规则(@某 agent、关键词、分支模式)与目标 agent | 把"研发值班群"绑定到 INFRA 项目,@值班 agent 即触发运行 |
| P3 | 入站事件摄取 | 平台回调 → 签名校验 → 去重 → 审计 → 匹配 → 入队;签名无效/缺失 401 绝不分发 | 飞书群里 @Mesh agent 提问,agent 自动领取并回评 |
| P4 | 出站渠道适配 | 通知/审批卡片经 IM 出站适配器发送,令牌缓存刷新 + 退避重试,台账落 `notification_delivery` | 执行失败通知既进站内收件箱,也推到绑定的 Slack 频道 |
| P5 | 飞书/Lark 连接器 | `im.message.receive_v1` 回调触发运行;交互卡片/审批卡片推送与回调;`tenant_access_token` 缓存刷新 | 审批卡片在飞书群内点"批准",运行从审批点续跑 |
| P6 | Slack 连接器 | Events API 事件回调(`message.channels` 等)+ Block Kit 卡片推送/回调 | Slack 频道 @agent,Block Kit 审批卡片回点 |
| P7 | GitHub/GitLab VCS 连接器 | commit/PR/branch ↔ issue 关联(`WEB-123` 自动解析);合并/关闭自动状态流转;仓库事件 → issue 联动 | PR 合并自动把 `WEB-123` 置 done 并发评论 |
| P8 | 出向 Webhook 订阅 | 订阅 Mesh 领域事件 → 经 outbox 投递到外部 URL,HMAC 签名 + 重试退避 + 投递台账 + 订阅级熔断 | 外部审计系统订阅 `issue.updated` 全量事件 |
| P9 | 凭据保险箱 | 凭据加密密文存储、轮换、撤销;响应/日志不回显;脱敏纳入全通道 | 轮换飞书 app secret,旧密文失效 |
| P10 | 摄取审计与可观测 | `integration_events` 全程留痕(签名状态/处理状态/载荷),实时 `integration.event_ingested` | 排查"为什么这条消息没触发运行"——查事件台账见 `rejected`/`deduped` |
| P11 | 钉钉/DingTalk 连接器 | 企业内部应用机器人接入:**Stream 长连接**(推荐,Mesh 侧主动出连钉钉网关、免公网回调地址)与 **HTTP 回调**(`timestamp`+`sign` 头签名)双接收模式,同一摄取管线;`accessToken` 缓存刷新、群/单聊消息收发、主动推送任务进度与结果、互动卡片回调 | 管理员在「集成」页连接钉钉企业内部应用机器人,绑定研发群,@Mesh agent 即触发运行,结果主动推回群 |
| P12 | emoji 确认接收 | 入站任务消息摄取成功(匹配并入队)后**立即**回轻量确认消息(默认 `✅ 已接收,处理中`,模板可配),再异步执行;at-most-once、失败仅审计告警不阻塞执行;去重/未匹配消息不确认 | 群里 @agent 派活,1~2 秒内先看到 ✅ 回执,稍后才收到处理结果 |
| P13 | `/stop` `/btw` 指令 | 命令平面即时生效(不排队):`/stop` 取消发起人在本会话的在途执行与排队项;`/btw <补充>` 向正在处理的执行追加上下文(不可信数据隔离);身份经 `external_identities` 核验,越权拒绝;命令注册表可扩展 | 任务跑了一半发现描述有误,群里发 `/btw 用 staging 环境` 补充,或 `/stop` 直接叫停 |
| P14 | 入站消息自动排队 | 会话级 FIFO 队列(`integration_message_queue`):同一会话(绑定 + 外部会话)新消息按序入队、串行派发,至多一个处理中执行;崩溃租约修复不丢失;状态/位置可查询、本人/管理员可取消排队项 | 连续 @agent 派三个活,后两个自动排队,队列面板可见"第 2/3 位",逐个执行不乱序 |

### 1.3 边界与非目标(明确不做什么)

- **runtime 的 git checkout/push ≠ 产品级 VCS 集成(硬边界)**:agent 运行时经 runtime 协议 checkout 仓库专属分支、推送产物(runtime.md `repo_checkouts`),那是**agent 的执行工具**,服务于单次运行;**本模块的 VCS 连接器是产品级集成**——它把外部 VCS 平台的事件(merge/close/comment/push)持续摄取进 Mesh,驱动 issue 关联与状态流转,与具体某次运行解耦。二者不互相替代:agent 用 runtime git 工具写代码,VCS 连接器把"代码已合并"这一事实回流成 issue 状态。
- **不做集成市场(marketplace)后端**:本期不提供第三方开发者上架连接器、审核、分成的市场后端(YAGNI);连接器由 Mesh 内置,出向 Webhook 是面向开发者的通用订阅通道,不是上架机制。
- **不**定义 agent 执行能力 —— 归 `agent.md`/`runtime.md`(本模块只"摄取事件 + 派单 + 出站投递")。
- **不**定义通知分级/审批实体 —— 归 README §6.13/§6.10(本模块是 IM 出站**通道**与审批**卡片呈现/回调面**,不持有通知/审批真源)。
- **不**做跨 workspace 的全局集成定义:集成与绑定都是工作区级;一个外部身份至多绑定一个工作区(外部侧唯一,§2.3)。
- **不**自定义入站摄取的去重/签名/审计机制 —— 一律复用 autopilot `webhook_events` 范式(autopilot.md §2.5/§3.2),仅替换平台特定的签名算法与载荷归一。
- **钉钉:只支持企业内部应用机器人(双向收发),不支持自定义 Webhook 机器人**:后者只能单向群发、无法接收消息回调,不满足本模块"摄取外部消息触发运行"的最小语义(YAGNI);`kind='im_dingtalk'` 一律以企业内部应用的 `app_key`/`app_secret` 凭据建集成。
- **不依赖平台级 emoji 回应(reaction)API**:钉钉开放平台不对机器人开放"给任意消息添加 emoji 回应"的能力;emoji 确认接收以**轻量确认消息**实现等价语义(§3.8),且三平台语义统一(飞书/Slack 即便有 reaction API 也不分叉实现,避免连接器行为漂移)。
- **命令平面不持有执行生命周期语义**:`/stop` 的实际取消动作经 runtime 的执行取消协议(runtime.md `task_executions` 状态机,`failure_reason='cancelled_by_command'` 为 runtime.md 登记词汇),`/btw` 的上下文注入经 **runtime.md「运行期上下文追加」机制(`execution_context_appends` 表 + 心跳下行,本 Spec 增补同步登记于 runtime.md/agent.md**,不可信数据隔离,§6.15,下一 turn 边界生效);本模块只是命令的**解析、鉴权与转发面**,不另建执行状态真源。

---

## 2. 数据模型

### 2.1 ER 概览(文字图)

```
workspaces ──隔离──► integrations(连接器实例:kind + 非密 config + 凭据密文引用)
                       │   created_by ──► members(复合 FK)
                       │
                       ├──1:N──► integration_bindings(外部身份 ↔ 工作区/项目 + 匹配规则 + 目标 agent)
                       │             ├─ integration_id 复合 FK → integrations
                       │             ├─ project_id NULL 复合 FK → projects(scope='project' 时)
                       │             └─ bound_agent_id NULL 复合 FK → agents
                       │             UNIQUE(provider, provider_tenant_key, external_ref) 外部身份全局唯一绑定(R3)
                       │
                       └──1:N──► integration_events(入站摄取台账:签名/去重/审计,复用 autopilot 范式)
                                     UNIQUE(integration_id, external_event_id) 去重
                                     ──► 匹配 binding ──► task_executions(trigger='integration',README §6.9)

external_identities(外部用户身份 ↔ Mesh 用户映射真源,R3 引入/R4 修订/R5 全局化):
   **全局身份表(与 users 同级,无 workspace_id、不受 workspace RLS 约束,README §6.1/§6.2,R5)**
   UNIQUE(provider, provider_tenant_key, external_user_key) 全局身份键(R4:纳入平台租户)
   user_id FK → users(全局登录身份,README §6.1;同一 users.id 在多工作区各有 member 行)ON DELETE CASCADE
   created_in_workspace_id FK → workspaces NULL,ON DELETE SET NULL(建链发起工作区仅审计,
                             不控制映射生命周期——删除建链工作区映射仍在,R5)
   解链仅映射所属 users.id 本人(无 admin 旁路);管理员仅可撤销本工作区使用权/成员资格(R5)
   卡片回调鉴权:集成实例解析 workspace → 本表查 users.id → JOIN 该 workspace 的
                 members(workspace_id, user_id) → README §6.10 权限再校验(§2.4.1/§3.2)

workspaces ──隔离──► webhook_subscriptions(出向订阅:https URL + 事件过滤 + 熔断状态)
                       │   created_by ──► members(复合 FK)
                       └──1:N──► webhook_subscription_deliveries(投递台账:重试退避/签名/结果)

出站适配(无新表):notification_delivery(channel='im',comment-inbox.md owns)记录 IM 投递;
                   approvals(README §6.10)卡片回调记 decision_comment;凭据密文同 runtime_credentials 契约。
```

### 2.2 表:`integrations`(集成定义 / 连接器实例)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK,`UNIQUE (workspace_id, id)`(供复合 FK 引用,README §6.2) | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) `ON DELETE CASCADE` | — | 归属工作区 |
| `kind` | TEXT | NOT NULL,CHECK IN ('im_feishu','im_slack','im_dingtalk','vcs_github','vcs_gitlab','webhook_outbound') | — | 集成类型(决定连接器适配点;`im_dingtalk` = 钉钉企业内部应用机器人,§3.2 双接收模式) |
| `name` | TEXT | NOT NULL | — | 展示名(工作区内唯一,见唯一索引) |
| `status` | TEXT | NOT NULL,CHECK IN ('active','disabled') | `'active'` | 启用状态;`disabled` 时入站摄取拒绝分发、出站停发 |
| `config` | JSONB | NOT NULL | `'{}'` | **非密**平台配置(app_id、外部租户标识、回调基址、默认卡片模板等;**严禁存任何 secret**,见 §2.7) |
| `stream_state` | JSONB | NOT NULL | `'{}'` | **钉钉 Stream 连接状态持久真源**(MES-82;仅 `kind='im_dingtalk'` 且 `receive_mode='stream'` 使用):`{state:'connected'\|'reconnecting'\|'down', last_frame_at, last_attempt_at, backoff_seconds}`;由 Stream worker 在状态迁移事务内经 outbox 同步广播 `integration.updated(subject='stream_channel')`(README §6.6/§6.7);`GET .../integrations/{id}/stream-status`(§3.9)读取,UI 首屏与诊断不依赖实时事件先达 |
| `secret_ref` | TEXT | NULL | NULL | 凭据加密密文引用(同 `runtime_credentials.encrypted_value` 契约,README §6.16;app secret / bot token / OAuth refresh token 只存密文,响应/日志不回显) |
| `created_by` | UUID | NOT NULL,**复合 FK `(workspace_id, created_by) → members(workspace_id, id)` `ON DELETE RESTRICT`** | — | 创建者(人或 agent;判别 JOIN members,README §6.1/§6.2;成员软删除,不悬空) |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**唯一约束**:`UNIQUE (workspace_id, name) WHERE deleted_at IS NULL`(软删除范围内名称唯一)。
**说明**:`kind='webhook_outbound'` 的集成是出向 Webhook 的"分组容器"(可选),具体订阅在 `webhook_subscriptions`;IM/VCS `kind` 的集成持有 `secret_ref` 与平台 `config`。

### 2.3 表:`integration_bindings`(外部身份 ↔ 工作区/项目绑定)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK,`UNIQUE (workspace_id, id)`(供复合 FK 引用,README §6.2) | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) `ON DELETE CASCADE` | — | 归属工作区 |
| `integration_id` | UUID | NOT NULL,**复合 FK `(workspace_id, integration_id) → integrations(workspace_id, id)` `ON DELETE CASCADE`** | — | 所属集成(README §6.2) |
| `provider` | TEXT | NOT NULL,CHECK IN ('feishu','slack','dingtalk','github','gitlab','webhook') | — | **规范化提供商标识**(R3:从 `integrations.kind` 归一,服务层在插入时校验与所属集成 kind 一致;跨 workspace 外部身份唯一键的第一维) |
| `provider_tenant_key` | TEXT | NOT NULL DEFAULT `''` | `''` | **规范化外部平台租户标识**(R3):Slack `team_id`、飞书 `tenant_key`、**钉钉 `corp_id`**(企业 corpId,入站载荷 `chatbotCorpId` 归一)、GitHub `installation_id`(或 org 登录名)、GitLab 实例主机(如 `gitlab.com`)、`webhook_outbound` 恒为 `''`;创建时从 `integrations.config` 归一写入,绑定生命周期内不变 |
| `scope` | TEXT | NOT NULL,CHECK IN ('workspace','project') | `'workspace'` | 绑定作用域 |
| `project_id` | UUID | NULL,**复合 FK `(workspace_id, project_id) → projects(workspace_id, id)` `ON DELETE CASCADE`** | NULL | **`scope='project'` 时必填、`scope='workspace'` 时必须为 NULL(精确异或 CHECK,见下)**;项目物理删除时其项目级绑定随之级联删除(绑定是项目私有配置,不保留悬空行;R3 删除策略) |
| `external_ref` | TEXT | NOT NULL | — | **规范化外部对象标识**(R3):IM 群/频道 ID(飞书 `chat_id`、Slack `channel_id`、**钉钉 `conversationId`**——群聊与单聊会话均以 `conversationId` 归一,单聊亦存在稳定会话 ID)、VCS 仓库全名 `owner/repo`;与 `provider` + `provider_tenant_key` 共同构成**跨 workspace 全局唯一键**(见唯一索引) |
| `match_config` | JSONB | NOT NULL | `'{}'` | 匹配规则(见 §2.6:如 @某 agent 触发、关键词、分支模式、事件类型过滤) |
| `bound_agent_id` | UUID | NULL,**复合 FK `(workspace_id, bound_agent_id) → agents(workspace_id, id)` `ON DELETE SET NULL (bound_agent_id)`** | NULL | 匹配成功后触发的目标 agent;为空时仅审计不触发(README §6.9) |
| `status` | TEXT | NOT NULL,CHECK IN ('active','disabled') | `'active'` | 绑定状态 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**唯一约束与 CHECK(R3 修订:外部身份全局唯一 + scope 精确异或)**:
- **`UNIQUE (provider, provider_tenant_key, external_ref)`(全局,不带 workspace_id)—— 外部身份跨 workspace 唯一绑定**:一个外部平台上的同一个身份(同一提供商、同一平台租户下的同一群/频道/仓库)**至多绑定到一个 Mesh 工作区的一条绑定行**。此前仅 `UNIQUE(integration_id, external_ref)`:两个不同工作区各自安装一个集成实例(两行 `integrations`)时,同一外部群可被两边重复绑定,违反 README §6.17「一个外部身份可绑定到至多一个工作区」。规范化三元组把唯一性从「集成实例内」提升到「全外部平台」,跨 workspace 抢绑在 INSERT 即被拒绝(集成测试 T29);`status='disabled'` 的绑定仍占位(防止绕过:先停用 A 区绑定再把外部身份绑到 B 区需先**删除** A 区绑定行)。
- **`CHECK ((scope = 'workspace' AND project_id IS NULL) OR (scope = 'project' AND project_id IS NOT NULL))`(精确异或)**:workspace 作用域**不得**携带 project_id(此前 `scope='workspace' OR project_id IS NOT NULL` 允许 workspace 绑定带项目,语义不明);project 作用域必带项目。配合 `fk_binding_project ON DELETE CASCADE`:项目物理删除时项目级绑定一并删除,**不存在 `SET NULL` 后违反自身 CHECK 的不可达状态**(R3:此前 `ON DELETE SET NULL(project_id)` 会把 `scope='project'` 行打成 `project_id IS NULL` 从而违反 CHECK,导致项目实际删不掉)。
- agent 为软删除;`bound_agent_id` 列级 `SET NULL` 后绑定退化为「仅审计不触发」,合法。

### 2.4 表:`integration_events`(入站事件摄取台账:签名 / 去重 / 审计)

> **同构于 autopilot `webhook_events`**(autopilot.md §2.5),相互独立:autopilot 的入站事件落 `webhook_events`,集成平台的入站事件落本表;摄取管线的签名/去重/审计/拒无效语义完全一致(`signature_status` 词汇同构,**不含 autopilot 的 `skipped`**——该值仅服务 autopilot test-run 场景,集成平台无此场景;`process_status` 词汇完全一致,限频拒绝复用 `rejected` + `payload._mesh_reject_reason='rate_limited'`,不新增枚举值)。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK,`UNIQUE (workspace_id, id)`(供 `task_executions.trigger_event_id` 逻辑引用,README §6.2/§6.4) | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) `ON DELETE CASCADE` | — | 归属工作区 |
| `integration_id` | UUID | NOT NULL,**复合 FK `(workspace_id, integration_id) → integrations(workspace_id, id)` `ON DELETE CASCADE`** | — | 接收入站事件的集成(README §6.2) |
| `external_event_id` | TEXT | NOT NULL | — | 外部事件唯一 ID(飞书 `event_id`、Slack `event_ts`+team、GitHub `X-GitHub-Delivery`、GitLab `event_uuid`);**被拒事件用 `rejected:<raw-body-hash>` 前缀**(防预占,见 §3.2 与 autopilot.md §3.2) |
| `event_type` | TEXT | NOT NULL | — | 归一后的事件类型(`im.message.receive_v1` / `message.channels` / `pull_request` / `merge_request` / `push` …) |
| `payload` | JSONB | NOT NULL | — | 原始载荷(进入 agent 上下文时按不可信数据隔离,README §6.15) |
| `signature_status` | TEXT | NOT NULL,CHECK IN ('valid','invalid','missing') | — | 签名校验结果(**`invalid`/`missing` 一律 `rejected` + 401,绝不分发**) |
| `process_status` | TEXT | NOT NULL,CHECK IN ('received','matched','dispatched','deduped','rejected','processed','failed') | `'received'` | 处理状态(同 autopilot.md §2.5 词汇) |
| `received_at` | TIMESTAMPTZ | NOT NULL | `now()` | 接收时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**去重唯一键**:`UNIQUE (integration_id, external_event_id)` —— 入站先尝试插入,命中唯一冲突即视为重复,幂等返回 200 `deduped` 不再分发(README §6.9「外部 IM 消息触发」行的去重保证)。

### 2.4.1 表:`external_identities`(外部用户身份 ↔ Mesh 用户映射;R3 协同 MES-4 HIGH-1 补真源,R4 修订模型)

> IM 审批/交互卡片回调的点击者鉴权(§3.2/§4.3)需要把外部平台的点击者身份映射到 Mesh 身份再按 README §6.10 权限行校验;本表是该映射的**唯一真源**(此前卡片回调直接转发 approve/reject,无点击者身份核验——MES-4 v3 安全复审 HIGH-1 修复引入本映射要求,R3 补表与约束,R4 修订映射模型)。
>
> **R4 模型修订(与 README §6.1 多工作区成员模型对齐)**:映射目标从 workspace-scoped 的 `members.id` 改为**全局登录身份 `users.id`**——此前「外部账号全局锁到一个 member_id」与 §6.1 核心模型(同一 `users.id` 在多工作区各有 member 行)冲突,会阻止同一已认证外部账号跨多个 Mesh 工作区参与卡片审批;身份键亦纳入 `provider_tenant_key`,不同外部租户的同名 user key 不再误撞。
>
> **R5 模型修订(HIGH-2,写死:真正的全局身份表)**:既然映射目标为全局 `users.id`,本表即**与 `users` 同级的全局身份表**——**移除租户所有权 / RLS 键**(此前残留的 `workspace_id NOT NULL ... ON DELETE CASCADE`「建链所在工作区」会让删除建链工作区 A 级联删除全局映射,使工作区 B 的卡片审批随即失效;并使 §6.2 的 workspace RLS 口径下 B 无法读取归属 A 的映射)。建链来源仅以**可空审计列 `created_in_workspace_id`(`ON DELETE SET NULL`)** 记录,**绝不级联控制映射生命周期**;映射的删除只由「用户注销级联」与「所属用户本人解链」两条路径产生。解链授权同步收紧:**全局解链仅允许映射所属 `users.id` 本人**,工作区管理员**不得**解链他人的全局身份,只能撤销本工作区的使用权 / 成员资格(member.md owns;该用户在本工作区的卡片回调因 JOIN 名册行失败而回落 403,全局映射不受影响)。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键(**全局表,无 `UNIQUE(workspace_id, id)`——本表不被任何复合 FK 引用,README §6.1/§6.2**) |
| `provider` | TEXT | NOT NULL,CHECK IN ('feishu','slack','dingtalk','github','gitlab') | — | 外部平台(与 bindings 同口径) |
| `provider_tenant_key` | TEXT | NOT NULL DEFAULT `''` | `''` | **规范化外部平台租户标识(R4:纳入身份键)**:飞书 `tenant_key`、Slack `team_id`、**钉钉 `corp_id`**、GitHub `installation_id`(或 org 登录名)、GitLab 实例主机;与 `integration_bindings.provider_tenant_key` 同口径,建链时从集成实例归一 |
| `external_user_key` | TEXT | NOT NULL | — | 规范化外部用户标识(飞书 `open_id`、Slack `user_id`、**钉钉 `senderStaffId`**(企业内部成员;无 staffId 的外部联系人回落 `ext:<senderId>`,`senderId` 为平台加密稳定 ID)**、GitHub/GitLab 用户 login/id) |
| `user_id` | UUID | NOT NULL,**FK→users(id) ON DELETE CASCADE** | — | **映射到的 Mesh 全局登录身份(R4:不再映射到单个 workspace-scoped 的 member_id)**——同一已认证外部账号可跨多个 Mesh 工作区参与卡片审批(每个工作区经各自的 `members(workspace_id, user_id)` 行解析,README §6.1);用户注销 → 映射级联删除(映射生命周期的唯一级联来源) |
| `created_in_workspace_id` | UUID | NULL,**FK→workspaces(id) ON DELETE SET NULL (created_in_workspace_id)** | NULL | **建链发起工作区(仅审计,R5)**:记录建链操作发生在哪个工作区(经哪个集成实例);**可空 + 列级 `SET NULL`——删除该工作区仅置空本列,映射行保留,其他工作区的卡片回调不受影响(R5 HIGH-2:不得以 CASCADE 控制全局映射生命周期)** |
| `verified_at` | TIMESTAMPTZ | NOT NULL | `now()` | 经**认证的连接流程**建立的时间(见下) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束与建立流程**:
- `UNIQUE (provider, provider_tenant_key, external_user_key)`(**全局身份键,R4**):一个外部平台账号(提供商 + 平台租户 + 外部用户)至多映射一个 Mesh 用户;同一账号在其所属平台租户内全局唯一,**不同外部租户的同名 user key 可并存**(此前 `UNIQUE(provider, external_user_key)` 缺租户维度,既可能让不同外部租户的同名 user key 误撞,又把账号锁死到单个 member)。
- **全局表,不受 workspace RLS 约束(R5 写死)**:本表与 `users` 同为全局身份层——**不携带 `workspace_id`,不适用 README §6.2 第 5 条的 workspace RLS 策略**(该条仅作用于携带 `workspace_id` 的业务表);行级访问控制以**所属用户 `user_id`** 为边界:读取 / 解链仅映射所属 `users.id` 本人(经请求者任一成员行解析的全局身份比对,服务端强制;可选 `user_id = current_setting('mesh.user_id')::uuid` 的 user 级 RLS 作纵深防御),**任何工作区角色(含 admin/owner)不因其角色获得对他人映射行的读 / 删权限**(T29 权限负向测试)。
- **映射只能经认证流程建立**:成员在站内「连接外部账号」流程中完成平台侧 OAuth 确认(服务端核对 OAuth 返回的平台用户身份与请求者会话)或一次性验证码确认(§3.1 `:link`/`:link-confirm`)后写入,**映射目标 = 请求者本人的 `users.id`**(经其成员行的 `user_id` 解析,建链端点不接受指向他人用户的参数);建链事务同时写入 `created_in_workspace_id` = 发起工作区(审计);**禁止**经卡片回调/入站事件隐式创建映射(否则攻击者可借他人点击伪造身份绑定)。
- **全局解链授权(R5 写死)**:解链(删除映射行)**仅允许映射所属 `users.id` 本人**——请求者经其当前工作区成员行解析出的 `users.id` 必须等于映射的 `user_id`,否则 `403 identity_unlink_forbidden`;**工作区 admin/owner 角色不构成解链授权(无 admin 旁路)**——管理员对他人外部身份的可及手段仅限「撤销该用户在本工作区的使用权 / 成员资格」(member.md 的成员管理端点),其效果是该用户在本工作区的卡片回调经名册 JOIN 回落 403,**全局映射与其他工作区的审批不受影响**。解链授权规则的可执行参照实现为 validation 脚本的 `external_identity_unlink_allowed(identity_id, member_id)`(T29 实测:授权判定只比对 `users.id`,角色列不参与)。
- **卡片回调鉴权链(§3.2,R4 写死)**:从回调载荷提取点击者外部身份(provider + 平台租户 + user key,租户由接收回调的集成实例归一)→ **由集成实例解析所属 workspace** → 查本表 `(provider, provider_tenant_key, external_user_key)` 得 `users.id` → **JOIN 该 workspace 的 `members(workspace_id, user_id)` 得名册行** → 按 README §6.10 权限行再校验 → **未映射 / 该用户在此工作区无名册行 / 名册行非 active / 无权限 → 403,审批状态不变,审计留痕**。同一映射行服务该用户在**所有**工作区的卡片审批(各工作区独立解析各自 member、独立做权限校验);**映射行不依赖任何工作区存活(R5:建链工作区删除后映射仍在,其余工作区回调链照常解析,T29)**。

### 2.5 表:`webhook_subscriptions`(出向 Webhook 订阅)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK,`UNIQUE (workspace_id, id)`(供 `webhook_subscription_deliveries` 复合 FK 引用,README §6.2) | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) `ON DELETE CASCADE` | — | 归属工作区 |
| `integration_id` | UUID | NULL,**复合 FK `(workspace_id, integration_id) → integrations(workspace_id, id)` `ON DELETE SET NULL (integration_id)`** | NULL | 可选的 `webhook_outbound` 集成分组 |
| `url` | TEXT | NOT NULL | — | 投递目标 URL,**https-only**(服务端校验 scheme,拒绝 `http`/非安全 scheme;私网地址段拒绝,README §6.16 SSRF 防护) |
| `secret_ref` | TEXT | NOT NULL | — | HMAC-SHA256 签名密钥的加密密文引用(同 `runtime_credentials.encrypted_value` 契约,README §6.16;创建后仅显示一次,响应/日志不回显) |
| `event_types` | TEXT[] | NOT NULL | `'{}'` | 订阅的事件类型过滤(如 `{issue.updated,issue.created}`;空数组 = 全部已登记领域事件) |
| `status` | TEXT | NOT NULL,CHECK IN ('active','paused','disabled') | `'active'` | `paused`=人工暂停;`disabled`=**熔断**(连续失败超阈值自动停用 + 告警) |
| `fail_count` | INT | NOT NULL,CHECK (>= 0) | `0` | 连续失败计数(成功后清零;超 `circuit_break_threshold` 转 `disabled`) |
| `created_by` | UUID | NOT NULL,**复合 FK `(workspace_id, created_by) → members(workspace_id, id)` `ON DELETE RESTRICT`** | — | 创建者(成员软删除,不悬空) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**常量**:`circuit_break_threshold`(订阅级熔断阈值,默认 20 次连续失败)、退避基数/封顶见 §2.6。

### 2.6 表:`webhook_subscription_deliveries`(出向投递台账:重试退避)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) `ON DELETE CASCADE` | — | 归属工作区 |
| `subscription_id` | UUID | NOT NULL,**复合 FK `(workspace_id, subscription_id) → webhook_subscriptions(workspace_id, id)` `ON DELETE CASCADE`** | — | 所属订阅(README §6.2) |
| `event_ref` | TEXT | NOT NULL | — | 源事件稳定引用(源 `outbox_events.id` 或领域事件 ID);参与投递幂等键 `sha256(subscription_id | event_ref)`(README §6.5) |
| `state` | TEXT | NOT NULL,CHECK IN ('pending','sent','failed') | `'pending'` | 投递状态 |
| `attempts` | INT | NOT NULL,CHECK (>= 0) | `0` | 已尝试次数 |
| `next_retry_at` | TIMESTAMPTZ | NULL | NULL | 下次重试时刻(指数退避 + 抖动;终态为 NULL) |
| `response_status` | INT | NULL | NULL | 最近一次 HTTP 响应码(NULL = 未收到响应) |
| `last_error` | TEXT | NULL | NULL | 最近一次错误摘要(不泄漏内部细节) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**去重唯一键**:`UNIQUE (subscription_id, event_ref)` —— 同一订阅对同一源事件至多一条投递台账;重复出队经此幂等(README §6.5 at-least-once → 恰好一次副作用)。

### 2.7 JSONB 配置结构

**`integrations.config`(非密,按 kind 示例)**:
```json
// kind='im_feishu'
{ "app_id": "cli_xxx", "verification_token_ref": "<密文引用>",
  "encrypt_key_ref": "<密文引用>", "callback_base": "https://mesh.example.com/api/v1/integrations/feishu",
  "card_template": "default" }
// kind='im_slack'
{ "app_id": "A0xxx", "team_id": "T0xxx",
  "signing_secret_ref": "<密文引用>", "bot_user_id": "U0xxx" }
// kind='im_dingtalk'(钉钉企业内部应用机器人,§3.2 双接收模式)
{ "app_key": "dingxxxxxx", "corp_id": "dingxxxxxx",
  "app_secret_ref": "<密文引用>",                // 企业内部应用 app_secret(签名校验 + accessToken 换取,只存密文)
  "robot_code": "dingxxxxxx",                    // 机器人 robotCode(默认同 app_key;出站发消息用)
  "receive_mode": "stream",                      // stream(长连接,推荐) | http(平台回调,需 callback_base)
  "callback_base": null,                         // receive_mode='http' 时必填:https://mesh.example.com/api/v1/integrations/dingtalk
  "ack_template": "✅ 已接收,处理中",             // emoji 确认接收模板(§3.8;置空字符串 = 关闭确认)
  "inbound_queue": "serial_conversation",        // 会话级串行排队(§2.10/§3.9) | parallel(即时派发,飞书/Slack 默认)
  "stream_reconnect": { "base_seconds": 2, "max_seconds": 300, "heartbeat_timeout_seconds": 90 } }
// kind='vcs_github'
{ "installation_id": "1234567", "webhook_secret_ref": "<密文引用>",
  "api_base": "https://api.github.com" }
// kind='vcs_gitlab'
{ "instance_url": "https://gitlab.com", "webhook_token_ref": "<密文引用>" }
```
> **`config` 严禁存明文 secret**:一切密钥以 `*_ref` 指向加密密文(同 `runtime_credentials.encrypted_value` 契约,README §6.16)。`config` 仅存 app_id、外部租户标识、回调基址、模板等非密配置。

**`integration_bindings.match_config`(匹配规则,字段间 AND,同类多值 OR)**:
```json
{ "trigger_on": ["mention", "direct_message"],
  "mention_agents": ["<agent_id>"],
  "keyword_include": ["值班", "线上"], "keyword_exclude": ["忽略"],
  "branch_pattern": "^(main|release/.*)$",
  "vcs_events": ["merge_request.merged", "merge_request.closed", "push"],
  "auto_status_map": { "merged": "done", "closed": "cancelled" } }
```
> - IM 绑定:`trigger_on` ∈ {mention, direct_message, keyword};`mention_agents` 限定 @哪些 agent 才触发(未匹配不触发,仅审计,README §6.9)。钉钉群内 @机器人 = `mention`、单聊 = `direct_message`(入站载荷 `conversationType` 归一:`"2"`=群聊、`"1"`=单聊)。
> - **`inbound_queue` 语义(§2.10/§3.9)**:`serial_conversation`(钉钉默认):同一会话的入站任务消息按 FIFO 串行派发,至多一个在途执行;`parallel`(飞书/Slack 默认,保持 §6.9 基线):入队即派发、不等待前序。两种模式均可在集成级切换,切换不影响已在途项。
> - VCS 绑定:`vcs_events` 过滤事件类型;`branch_pattern` 限定分支;`auto_status_map` 把 VCS 动作映射到 issue 目标状态(经 issue.md 状态流转,服务层校验目标状态存在于该 issue 当前作用域)。
> - **不可信内容(README §6.15)**:`match_config` 中的关键词/模式是**匹配条件**,不入 agent 上下文;入站消息正文入 agent 上下文时按不可信数据隔离。

**`webhook_subscriptions` 退避配置(工作区级常量,非 per-row)**:
```json
{ "retry_max_attempts": 8, "retry_base_seconds": 30, "retry_max_seconds": 3600,
  "circuit_break_threshold": 20, "delivery_timeout_seconds": 10 }
```
> 退避 `delay = min(retry_base × 2^attempts, retry_max) × jitter`;`attempts` 超 `retry_max_attempts` 该投递置 `failed`(不再重试,告警);`fail_count`(订阅级)累计连续失败,超 `circuit_break_threshold` 订阅转 `disabled`(熔断)并告警,人工恢复。

### 2.8 索引与约束(PG16 可执行)

```sql
-- ============ integrations ============
CREATE TABLE integrations (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('im_feishu','im_slack','im_dingtalk','vcs_github','vcs_gitlab','webhook_outbound')),
  name         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  config       JSONB NOT NULL DEFAULT '{}',
  stream_state JSONB NOT NULL DEFAULT '{}',                                             -- 钉钉 Stream 连接状态持久真源(MES-82,§3.9 stream-status)
  secret_ref   TEXT NULL,
  created_by   UUID NOT NULL,
  deleted_at   TIMESTAMPTZ NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_integrations_ws_id UNIQUE (workspace_id, id),                       -- 复合 FK 引用前提(§6.2)
  CONSTRAINT fk_integrations_created_by FOREIGN KEY (workspace_id, created_by)
    REFERENCES members(workspace_id, id) ON DELETE RESTRICT                          -- 作者不悬空(软删除)
);
CREATE UNIQUE INDEX uq_integrations_ws_name ON integrations(workspace_id, name) WHERE deleted_at IS NULL;
CREATE INDEX idx_integrations_ws_kind ON integrations(workspace_id, kind) WHERE deleted_at IS NULL;

-- ============ integration_bindings ============
CREATE TABLE integration_bindings (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id      UUID NOT NULL,
  provider            TEXT NOT NULL CHECK (provider IN ('feishu','slack','dingtalk','github','gitlab','webhook')),
  provider_tenant_key TEXT NOT NULL DEFAULT '',                                      -- R3:规范化外部平台租户(team_id/tenant_key/corp_id/installation_id/实例主机)
  scope               TEXT NOT NULL DEFAULT 'workspace' CHECK (scope IN ('workspace','project')),
  project_id          UUID NULL,
  external_ref        TEXT NOT NULL,                                                 -- R3:规范化外部对象 ID(chat_id/channel_id/conversationId/owner/repo)
  match_config        JSONB NOT NULL DEFAULT '{}',
  bound_agent_id      UUID NULL,
  status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_integration_bindings_ws_id UNIQUE (workspace_id, id),                -- 复合 FK 引用前提(§6.2)
  -- R3:外部身份跨 workspace 唯一绑定(全局键,README §6.17;取代仅 integration 实例内的旧键)
  CONSTRAINT uq_binding_external_identity UNIQUE (provider, provider_tenant_key, external_ref),
  -- R3:scope/project 精确异或(workspace 不带 project;project 必带 project)
  CONSTRAINT ck_binding_scope CHECK ((scope = 'workspace' AND project_id IS NULL)
                                  OR (scope = 'project' AND project_id IS NOT NULL)),
  CONSTRAINT fk_binding_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE CASCADE,
  -- R3:项目级绑定随项目物理删除级联(不再 SET NULL——置空会违反上面的精确异或 CHECK)
  CONSTRAINT fk_binding_project FOREIGN KEY (workspace_id, project_id)
    REFERENCES projects(workspace_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_binding_agent FOREIGN KEY (workspace_id, bound_agent_id)
    REFERENCES agents(workspace_id, id) ON DELETE SET NULL (bound_agent_id)
);
CREATE INDEX idx_binding_integration ON integration_bindings(integration_id, status);
CREATE INDEX idx_binding_agent ON integration_bindings(workspace_id, bound_agent_id) WHERE bound_agent_id IS NOT NULL;

-- ============ integration_events(同构 autopilot.webhook_events)============
CREATE TABLE integration_events (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id    UUID NOT NULL,
  external_event_id TEXT NOT NULL,
  event_type        TEXT NOT NULL,
  payload           JSONB NOT NULL,
  signature_status  TEXT NOT NULL CHECK (signature_status IN ('valid','invalid','missing')),
  process_status    TEXT NOT NULL DEFAULT 'received'
                    CHECK (process_status IN ('received','matched','dispatched','deduped','rejected','processed','failed')),
  received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_integration_events_ws_id UNIQUE (workspace_id, id),                  -- 供 trigger_event_id 引用(§6.2)
  CONSTRAINT uq_integration_event_dedup UNIQUE (integration_id, external_event_id),  -- 入站去重(§6.9)
  CONSTRAINT fk_event_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_event_integration_status ON integration_events(integration_id, process_status, received_at DESC);
CREATE INDEX idx_event_ws_received ON integration_events(workspace_id, received_at DESC);

-- ============ external_identities(R3 协同 MES-4 HIGH-1;R4 HIGH-5:映射全局 users.id + 身份键含平台租户;R5 HIGH-2:真正的全局身份表)============
-- R5 修订:移除租户所有权 / RLS 键(原 workspace_id NOT NULL ... ON DELETE CASCADE)——映射为全局 users.id 级,
-- 建链所在工作区仅以可空审计列 created_in_workspace_id(ON DELETE SET NULL)记录,不级联控制映射生命周期;
-- 删除建链工作区 A 后映射仍在,工作区 B 的回调照常解析;本表与 users 同为全局表,不带 workspace_id、
-- 不适用 README §6.2 第 5 条 workspace RLS,行级访问以所属 user_id 为边界(解链仅所属用户本人,无 admin 旁路)。
CREATE TABLE external_identities (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider              TEXT NOT NULL CHECK (provider IN ('feishu','slack','dingtalk','github','gitlab')),
  provider_tenant_key   TEXT NOT NULL DEFAULT '',                -- R4:平台租户(飞书 tenant_key / Slack team_id / 钉钉 corp_id / GitHub installation 或 org / GitLab 实例主机)
  external_user_key     TEXT NOT NULL,                           -- 飞书 open_id / Slack user_id / 钉钉 senderStaffId(外部联系人 ext:<senderId>)/ VCS 用户 login
  user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- R4:映射全局登录身份(回调按工作区 JOIN members 解析名册行;映射生命周期的唯一级联来源)
  created_in_workspace_id UUID NULL REFERENCES workspaces(id)
                          ON DELETE SET NULL (created_in_workspace_id),         -- R5:建链发起工作区(仅审计;删除该工作区仅置空本列,映射保留)
  verified_at           TIMESTAMPTZ NOT NULL DEFAULT now(),      -- 经认证的「连接外部账号」流程建立
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- R4:身份键 = 平台 + 平台租户 + 外部用户(全局);一个外部平台账号至多映射一个 Mesh 用户,
  -- 不同外部租户同 user key 可并存;同一账号跨多 Mesh 工作区参与 = 单映射行 + 按工作区 JOIN member
  CONSTRAINT uq_external_identity UNIQUE (provider, provider_tenant_key, external_user_key)
);
CREATE INDEX idx_external_identities_user ON external_identities(user_id);
CREATE INDEX idx_external_identities_created_in_ws ON external_identities(created_in_workspace_id)
  WHERE created_in_workspace_id IS NOT NULL;                     -- R5:建链来源审计检索(可选)

-- ============ webhook_subscriptions ============
CREATE TABLE webhook_subscriptions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id UUID NULL,
  url            TEXT NOT NULL,
  secret_ref     TEXT NOT NULL,
  event_types    TEXT[] NOT NULL DEFAULT '{}',
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','disabled')),
  fail_count     INT NOT NULL DEFAULT 0 CHECK (fail_count >= 0),
  created_by     UUID NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_webhook_subscriptions_ws_id UNIQUE (workspace_id, id),               -- 复合 FK 引用前提(§6.2)
  CONSTRAINT fk_subscription_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE SET NULL (integration_id),
  CONSTRAINT fk_subscription_created_by FOREIGN KEY (workspace_id, created_by)
    REFERENCES members(workspace_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_subscription_ws_status ON webhook_subscriptions(workspace_id, status);

-- ============ webhook_subscription_deliveries ============
CREATE TABLE webhook_subscription_deliveries (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  subscription_id UUID NOT NULL,
  event_ref       TEXT NOT NULL,
  state           TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sent','failed')),
  attempts        INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_retry_at   TIMESTAMPTZ NULL,
  response_status INT NULL,
  last_error      TEXT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_delivery_subscription_event UNIQUE (subscription_id, event_ref),     -- 出向投递幂等(§6.5)
  CONSTRAINT fk_delivery_subscription FOREIGN KEY (workspace_id, subscription_id)
    REFERENCES webhook_subscriptions(workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_delivery_retry ON webhook_subscription_deliveries(next_retry_at)
  WHERE state = 'pending';
CREATE INDEX idx_delivery_subscription ON webhook_subscription_deliveries(subscription_id, created_at DESC);

-- ============ vcs_links(R3 新增:VCS 对象 ↔ Mesh 实体 关联真源表)============
-- §3.3 的 VCS link CRUD / 自动关联 / issue 侧栏展示一律以本表为真源(此前只有端点与 UI,无真源表)
CREATE TABLE vcs_links (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id        UUID NOT NULL,                                               -- 必须为 vcs_github/vcs_gitlab 集成(服务层校验 kind)
  provider              TEXT NOT NULL CHECK (provider IN ('github','gitlab')),        -- 规范化提供商
  provider_tenant_key   TEXT NOT NULL DEFAULT '',                                    -- GitHub installation_id/org、GitLab 实例主机(与 bindings 同口径)
  external_object_type  TEXT NOT NULL CHECK (external_object_type IN ('repository','pull_request','merge_request','issue','commit','branch')),
  external_object_ref   TEXT NOT NULL,                                               -- 规范化稳定引用:仓库 `owner/repo`;PR/MR `owner/repo#<number>`;commit 全 sha;branch `owner/repo@<ref>`
  mesh_entity_type      TEXT NOT NULL CHECK (mesh_entity_type IN ('issue','project')),
  mesh_entity_id        UUID NOT NULL,                                               -- 多态逻辑外键(§6.2 第 4 条:携带 workspace_id,软删除一致性由服务层保证)
  link_source           TEXT NOT NULL DEFAULT 'manual'
                        CHECK (link_source IN ('manual','auto_keyword','auto_branch','auto_commit')),
  status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','stale','deleted')),  -- stale=外部对象已关闭/合并后的陈旧标记
  external_state        JSONB NOT NULL DEFAULT '{}',                                 -- 外部对象状态快照(如 PR open/merged/closed、commit sha 列表)
  created_by            UUID NULL,                                                   -- 人工关联时的成员;自动关联为 NULL
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_vcs_links_ws_id UNIQUE (workspace_id, id),                           -- 复合 FK 引用前提(§6.2)
  -- R3:外部对象唯一键——同一外部对象在一个工作区内至多一条 active 关联(跨 workspace 的唯一性由 bindings 的
  -- 外部身份全局键保证:一个仓库所属平台租户至多绑定一个工作区,故此处工作区内唯一即全局有效)
  CONSTRAINT fk_vcs_links_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE CASCADE,                     -- 集成删除 → 其 VCS 关联一并删除
  CONSTRAINT fk_vcs_links_created_by FOREIGN KEY (workspace_id, created_by)
    REFERENCES members(workspace_id, id) ON DELETE SET NULL (created_by)             -- 关联人离册仅置空,关联本身保留
);
-- R3:外部对象唯一键(部分唯一索引:仅 active 占位,stale/deleted 允许历史重关联)
CREATE UNIQUE INDEX uq_vcs_links_external_object
  ON vcs_links(provider, provider_tenant_key, external_object_type, external_object_ref)
  WHERE status = 'active';
-- R3:同租户复合 FK 前提 + Mesh 实体侧唯一(active)
CREATE UNIQUE INDEX uq_vcs_links_mesh_entity
  ON vcs_links(workspace_id, mesh_entity_type, mesh_entity_id, external_object_ref)
  WHERE status = 'active';
-- R3:状态索引——issue 侧栏「关联的 PR/MR」与陈旧关联清理扫描
CREATE INDEX idx_vcs_links_entity_status
  ON vcs_links(workspace_id, mesh_entity_type, mesh_entity_id, status);
CREATE INDEX idx_vcs_links_integration_status
  ON vcs_links(integration_id, status);

-- ============ integration_message_queue(MES-82:入站消息会话级 FIFO 队列,§2.10)============
CREATE TABLE integration_message_queue (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id       UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id     UUID NOT NULL,
  binding_id         UUID NOT NULL,                                     -- 仅命中绑定的任务消息入队(未匹配消息只审计 integration_events,不占队列)
  integration_event_id UUID NOT NULL,                                   -- 源入站事件(复合 FK 同租户,§6.2)
  conversation_key   TEXT NOT NULL,                                     -- 规范化会话键:provider:provider_tenant_key:external_ref(如 dingtalk:dingxxx:cidxxx)
  seq                BIGINT NOT NULL CHECK (seq > 0),                   -- 会话内单调递增(会话级事务咨询锁取号 + ON CONFLICT 重试,§2.10)
  dispatch_mode      TEXT NOT NULL CHECK (dispatch_mode IN ('serial_conversation','parallel')),
                                                                        -- 入队时对 config.inbound_queue 的快照,项生命周期内不可变(§3.9)
  state              TEXT NOT NULL DEFAULT 'pending'
                     CHECK (state IN ('pending','processing','done','cancelled','failed')),
  execution_id       UUID NULL,                                         -- 派发后绑定的执行(processing/done/failed 期间非空)
  sender_identity_key TEXT NOT NULL DEFAULT '',                         -- 规范化发起人(provider external_user_key);本人取消排队项与 /stop 授权用
  ack_sent_at        TIMESTAMPTZ NULL,                                  -- emoji 确认接收发送时刻(§3.8;NULL = 未发/失败)
  lease_expires_at   TIMESTAMPTZ NULL,                                  -- processing 租约(过期孤儿项由修复扫描处置,§3.9)
  enqueued_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at         TIMESTAMPTZ NULL,
  finished_at        TIMESTAMPTZ NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_imq_ws_id UNIQUE (workspace_id, id),                    -- 复合 FK 引用前提(§6.2)
  CONSTRAINT uq_imq_event UNIQUE (integration_id, integration_event_id),-- 与 integration_events 去重同源:重复事件不重复入队
  CONSTRAINT uq_imq_conversation_seq UNIQUE (conversation_key, seq),    -- 会话内序号唯一,位置计算真源
  CONSTRAINT fk_imq_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_imq_binding FOREIGN KEY (workspace_id, binding_id)
    REFERENCES integration_bindings(workspace_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_imq_event FOREIGN KEY (workspace_id, integration_event_id)
    REFERENCES integration_events(workspace_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_imq_execution FOREIGN KEY (workspace_id, execution_id)
    REFERENCES task_executions(workspace_id, id) ON DELETE SET NULL (execution_id)
);
-- MES-82 硬保证:同一会话至多一个处理中项(数据库级"不并发冲突",§3.9)
CREATE UNIQUE INDEX uq_imq_conversation_processing
  ON integration_message_queue(conversation_key) WHERE state = 'processing';
CREATE INDEX idx_imq_conversation_pending
  ON integration_message_queue(conversation_key, seq) WHERE state = 'pending';
CREATE INDEX idx_imq_lease ON integration_message_queue(lease_expires_at)
  WHERE state = 'processing';
CREATE INDEX idx_imq_ws_state ON integration_message_queue(workspace_id, state, enqueued_at DESC);
```

### 2.9 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `integrations.workspace_id` 等 | → `workspaces.id` | workspace.md | 隔离 |
| `integrations.created_by` / `webhook_subscriptions.created_by` | 复合 FK → `members(workspace_id, id)` | member.md | 创建者(人或 agent;判别 JOIN members,README §6.1/§6.2) |
| `integration_bindings.integration_id` / `integration_events.integration_id` / `webhook_subscriptions.integration_id` | 复合 FK → `integrations(workspace_id, id)` | 本模块 | 集成归属(README §6.2) |
| `integration_bindings.project_id` | 复合 FK → `projects(workspace_id, id)` `ON DELETE CASCADE` | project.md | `scope='project'` 时下放绑定(精确异或 CHECK 下项目删除级联删绑定,R3) |
| `integration_bindings.bound_agent_id` | 复合 FK → `agents(workspace_id, id)` | agent.md | 匹配后触发的目标 agent(README §6.2) |
| `vcs_links.integration_id` | 复合 FK → `integrations(workspace_id, id)` `ON DELETE CASCADE` | 本模块 | VCS 关联真源表归属的 VCS 集成(R3,§2.8;集成删除级联删关联) |
| `external_identities.user_id` | FK → `users(id)` `ON DELETE CASCADE` | auth.md(README §6.1 全局登录身份) | 外部用户身份 → Mesh **用户**映射(R3 引入、R4 修订、R5 全局化,§2.4.1;用户注销级联删映射——映射生命周期的唯一级联来源;卡片回调按集成解析 workspace 后 JOIN `members(workspace_id, user_id)` 得名册行再校验 §6.10 权限,未映射/无该行/无权限 → 403) |
| `external_identities.created_in_workspace_id` | FK → `workspaces(id)` `ON DELETE SET NULL (created_in_workspace_id)`(可空) | workspace.md | 建链发起工作区(**仅审计,R5**:删除该工作区仅置空本列,**不级联删除全局映射**——映射为全局 `users.id` 级,其余工作区回调不受影响;全局表无 `workspace_id` 所有权列、不适用 workspace RLS,§2.4.1/README §6.1/§6.2) |
| `vcs_links.created_by` | 复合 FK → `members(workspace_id, id)` `ON DELETE SET NULL (created_by)` | member.md | 人工关联者(自动关联为 NULL;离册仅置空) |
| `vcs_links.mesh_entity_id` | 多态逻辑外键 → `issues`/`projects`(携带 `workspace_id`,README §6.2 第 4 条) | issue.md / project.md | 关联的 Mesh 实体(软删除一致性由服务层保证) |
| `webhook_subscription_deliveries.subscription_id` | 复合 FK → `webhook_subscriptions(workspace_id, id)` | 本模块 | 投递台账归属(README §6.2) |
| `integration_message_queue.integration_id` / `binding_id` / `integration_event_id` | 复合 FK → `integrations` / `integration_bindings` / `integration_events`(各 `(workspace_id, id)`) | 本模块 | 队列项归属:集成删除级联删队列项;绑定删除级联删其会话队列;事件删除级联删对应项(§2.10) |
| `integration_message_queue.execution_id` | 复合 FK → `task_executions(workspace_id, id)` `ON DELETE SET NULL (execution_id)` | runtime.md / README §6.4 | 队列项触发的执行(执行记录删除仅置空引用,队列项审计保留;§2.10) |
| `notification_delivery.provider`(comment-inbox.md owns) | CHECK 扩展 `'dingtalk'` | comment-inbox.md / README §6.13 | **跨模块协同项(本 Spec 增补已同步修订)**:钉钉出站的**真通知**(任务进度/结果/审批卡片,挂靠 `notifications` 行)经 `notification_delivery` 台账,`provider='dingtalk'`、`destination_key='dingtalk:<binding_id>:<conversationId>'`;comment-inbox.md §2.8 的 `provider` CHECK 与 `docs/specs/validation/schema_r2_validation.sql` 已同步扩展。**确认接收 ack 与命令反馈不经本表**(会话性回复,§3.8) |
| `task_executions.trigger` / `trigger_event_id` | `trigger='integration'`;`trigger_event_id` 逻辑引用 `integration_events.id` | runtime.md / README §6.4 | 入站触发的执行(幂等键 §6.9) |
| `notification_delivery.channel='im'` | 出站适配器写入(台账为 comment-inbox.md owns) | comment-inbox.md / README §6.13 | IM 出站投递台账 |
| `approvals`(卡片回调) | 出站适配器推送卡片;回调经 `POST /approvals/{id}/approve\|reject` | README §6.10 | 审批卡片化呈现与回调 |

### 2.10 表:`integration_message_queue`(入站消息会话级 FIFO 队列;MES-82「新消息自动排队」真源)

> **定位**:入站 IM 任务消息从「摄取审计」(`integration_events`)到「执行派发」(`task_executions`)之间的**排序层**。解决"机器人处理上一条消息时,新到达的消息自动排队、按序处理、不丢失、不并发冲突,且状态/位置可查询"。命令消息(`/stop`/`/btw`)**不进本表**——命令是控制平面,摄取管线即时处理(§3.7),只落 `integration_events` 审计。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK,`UNIQUE (workspace_id, id)` | `gen_random_uuid()` | 主键 |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) `ON DELETE CASCADE` | — | 归属工作区 |
| `integration_id` | UUID | NOT NULL,复合 FK → `integrations(workspace_id, id)` `ON DELETE CASCADE` | — | 所属集成(README §6.2) |
| `binding_id` | UUID | NOT NULL,复合 FK → `integration_bindings(workspace_id, id)` `ON DELETE CASCADE` | — | 命中的绑定;**仅匹配到绑定(且目标 agent 非空)的任务消息入队**——未匹配/仅审计消息不占队列(README §6.9:未匹配不触发) |
| `integration_event_id` | UUID | NOT NULL,复合 FK → `integration_events(workspace_id, id)` `ON DELETE CASCADE` | — | 源入站事件;`UNIQUE(integration_id, integration_event_id)` 与事件去重同源——重复外部事件不重复入队 |
| `conversation_key` | TEXT | NOT NULL | — | **规范化会话键** `<provider>:<provider_tenant_key>:<external_ref>`(如 `dingtalk:dingxxxx:cidxxxx`);队列串行粒度。**各段字符集校验**:服务层插入前校验三段均不含 `:` 且仅含平台合法字符(钉钉/飞书/Slack 平台 ID 均为字母数字,违例 → `invalid_request`),杜绝 `("a","b:c","d")` 与 `("a","b","c:d")` 坍缩为同一键的歧义 |
| `seq` | BIGINT | NOT NULL,CHECK (> 0),`UNIQUE (conversation_key, seq)` | — | 会话内入队序号。**取号协议(并发安全)**:入队事务先 `pg_advisory_xact_lock(hashtext('imq_seq:'||conversation_key))` 串行化同会话取号,再 `INSERT … seq = COALESCE((SELECT max(seq) … WHERE conversation_key=:k),0)+1`(空会话首插同样受咨询锁保护);并以 `ON CONFLICT (conversation_key, seq) DO NOTHING` + 有限次重试(≤3)作背压兜底;**禁止裸 `max+1` 无锁写入**。位置 = 本会话中 `state='pending'` 且 `seq` 较小者计数 + 1 |
| `dispatch_mode` | TEXT | NOT NULL,CHECK IN ('serial_conversation','parallel') | — | **入队时对 `integrations.config.inbound_queue` 的快照,项生命周期内不可变**:派发器按**项的 `dispatch_mode`** 决定行为(§3.9),集成级模式切换只影响切换后入队的新项,存量项按入队时的模式清空(防 serial→parallel 切换饿死存量 pending) |
| `state` | TEXT | NOT NULL,CHECK IN ('pending','processing','done','cancelled','failed') | `'pending'` | 生命周期:`pending`(已入队待派发)→ `processing`(已派发执行,执行在途)→ `done`(执行成功终态)/ `failed`(执行失败终态);`cancelled`(被 `/stop` 或队列取消端点取消,仅 `pending` 态可转;**终态→终态转换一律 no-op 守卫**,命令取消与终态回写竞态幂等) |
| `execution_id` | UUID | NULL,复合 FK → `task_executions(workspace_id, id)` `ON DELETE SET NULL (execution_id)` | NULL | 派发时绑定的执行(runtime.md);经「执行关联回写」绑定(§3.9,两种模式共用);执行记录删除仅置空引用,队列项审计保留 |
| `sender_identity_key` | TEXT | NOT NULL | `''` | **规范化发起人身份全三元组** `<provider>:<provider_tenant_key>:<external_user_key>`(如 `dingtalk:dingxxxx:staffidxxxx`;与 `conversation_key` 同字符集校验)。**一切"本人"判定必须按全三元组经 `external_identities` 解析到 `users.id` 再比对,禁止仅凭裸 `external_user_key` 查询**——不同 provider/租户下同一字符串可映射不同 Mesh 用户(如 GitHub login `foo` 与钉钉 staffId `foo`),裸键解析导致跨身份越权(§3.7/§3.9 授权、§5.6 负向验收) |
| `ack_sent_at` | TIMESTAMPTZ | NULL | NULL | emoji 确认接收已发送的时刻(§3.8);NULL = 未发送(关闭确认 / 发送失败,失败仅审计告警) |
| `lease_expires_at` | TIMESTAMPTZ | NULL | NULL | `processing` 租约到期时刻(派发放量 = 执行超时上限 + 缓冲);过期且执行非在途 → 修复扫描置 `failed`/`done`(§3.9 不丢失保证) |
| `enqueued_at` / `started_at` / `finished_at` | TIMESTAMPTZ | NOT NULL/NULL/NULL | `now()`/—/— | 入队/派发/终态时刻(队列时延观测) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**关键约束(§3.9 语义的数据库级保证)**:
- **`UNIQUE INDEX uq_imq_conversation_processing ON (conversation_key) WHERE state='processing'`** —— 同一会话**至多一个处理中项**:"不并发冲突"不依赖 worker 互斥,而是 INSERT/UPDATE 冲突即失败的数据库硬约束;派发器争抢失败 = 该会话已有在途执行,退避即可。
- **`UNIQUE (conversation_key, seq)` + 部分索引 `idx_imq_conversation_pending (conversation_key, seq) WHERE state='pending'`** —— 会话内严格 FIFO 取首项(`ORDER BY seq LIMIT 1`)。
- **`UNIQUE (integration_id, integration_event_id)`** —— 与 `integration_events.UNIQUE(integration_id, external_event_id)` 同源的二次幂等:摄取去重失效(理论上不可达)也不产生重复队列项。

**入站频率护栏(硬约束,与去重正交;安全防滥用)**:绑定会话内的外部成员对 Mesh 是**未认证方**,无限流入站 = 每条消息一次完整 agent 执行(付费算力)+ 出站配额消耗,构成成本放大与集成拒绝服务面。每条入站 IM 消息在**匹配入队前**过三道计数(Redis 滚动窗口,键含租户维):

| 护栏 | 键维 | 常量 | 默认 |
|------|------|------|------|
| 每身份频率 | `(provider, provider_tenant_key, external_user_key)` | `MESH_IM_INBOUND_PER_IDENTITY_PER_MIN` | 20 / 滚动分钟 |
| 每会话频率 | `conversation_key` | `MESH_IM_INBOUND_PER_CONVERSATION_PER_MIN` | 60 / 滚动分钟 |
| 每会话排队深度 | `conversation_key` 的 pending 计数 | `MESH_IM_QUEUE_MAX_PENDING_PER_CONVERSATION` | 50 |

超限处置:**不入队**,落 `integration_events`(`process_status='rejected'`、`payload._mesh_reject_reason='rate_limited'`,真实 `msgId` 占去重键防同消息重试风暴)+ 机器人回**一次性**限频提示(同会话提示自身限频 1 次/分钟,防提示反射)+ 告警;HTTP 回调模式返回 **200**(非 2xx 会触发平台重推放大),Stream 帧正常 ACK。**入站文本长度上限**:消息正文与 `/btw` 参数统一受 `MESH_IM_INBOUND_TEXT_MAX_CHARS`(默认 4000)约束,超限截断 + `payload.truncated=true` 审计(提示注入面与 token 成本护栏,§6.15 之外的量化补充)。命令平面(§3.7)的频率约束**指向本节**(此前误引 auth.md 限流矩阵"入站回调行"——该行经本 Spec 同步补入 auth.md §3.6,作签名**前**每集成/IP 粗粒度防刷,与上述签名**后**语义级护栏分层互补)。

**与 §6.9 触发矩阵的关系(README §6.9「外部 IM 消息触发」行据此修订)**:入站 IM 消息命中绑定、**过频率护栏**后**入本表**(同摄取事务,`dispatch_mode` 快照当时配置),再由**队列派发器/执行关联回写**经 outbox 入队执行(`trigger='integration'`,幂等键 `sha256(agent_id | integration_binding_id | external_event_id)` 不变);`dispatch_mode='serial_conversation'`(钉钉默认)时派发器等待本会话前序项到达终态再派发下一项,`dispatch_mode='parallel'`(飞书/Slack 默认)时入队即派发(§6.9 原基线行为)。执行终态回写队列项经 runtime 登记的执行终态事件(`execution.completed`/`execution.failed`/`execution.timeout` → done/failed;`execution.cancelled` → cancelled)驱动,outbox 消费,非轮询旁路。

---

## 3. 接口设计

REST 基础路径 `/api/v1`;管理端点鉴权 `Authorization: Bearer <token>`,**入站回调端点除外**(平台签名校验,非 Bearer)。**成功包络 / 游标分页 / 错误信封 / 乐观并发 / 幂等写 / 过滤限制一律以 README §6.14 为唯一权威**(单对象 `{"data":{...}}`、列表 `{"data":[...],"next_cursor":<opaque|null>}`、错误 `{"error":{"code","message","details"}}`),本 Spec 不重复定义,仅列本模块具名错误码。

### 3.1 管理端点(CRUD)

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/workspaces/{ws}/integrations` | 集成列表(`kind`/`status` 过滤) | 成员 |
| POST | `/workspaces/{ws}/integrations` | 创建集成(选 `kind` + 非密 config) | admin / `integration:manage` |
| GET | `/workspaces/{ws}/integrations/{id}` | 集成详情(`secret_ref` 不回显明文) | 成员 |
| PATCH | `/workspaces/{ws}/integrations/{id}` | 更新配置/状态 | admin / `integration:manage` |
| DELETE | `/workspaces/{ws}/integrations/{id}` | 软删除集成 | admin / `integration:manage` |
| POST | `/workspaces/{ws}/integrations/{id}/rotate-secret` | 轮换凭据(旧密文失效) | admin / `integration:manage` |
| GET | `/workspaces/{ws}/integrations/{id}/bindings` | 该集成的绑定列表 | 成员 |
| POST | `/workspaces/{ws}/integrations/{id}/bindings` | 创建绑定(外部身份 + 作用域 + 匹配规则 + 目标 agent) | admin / `integration:manage` |
| PATCH | `/workspaces/{ws}/integration-bindings/{id}` | 更新绑定(匹配规则/目标 agent/状态) | admin / `integration:manage` |
| DELETE | `/workspaces/{ws}/integration-bindings/{id}` | 删除绑定 | admin / `integration:manage` |
| GET | `/workspaces/{ws}/integrations/{id}/events` | 入站事件台账(签名/处理状态过滤,排障用) | 成员 |
| GET | `/workspaces/{ws}/webhook-subscriptions` | 出向订阅列表 | 成员 |
| POST | `/workspaces/{ws}/webhook-subscriptions` | 创建订阅(https URL + 事件过滤;签名密钥创建后仅显示一次) | admin / `integration:manage` |
| GET | `/workspaces/{ws}/webhook-subscriptions/{id}` | 订阅详情(密钥不回显) | 成员 |
| PATCH | `/workspaces/{ws}/webhook-subscriptions/{id}` | 更新订阅(URL/事件过滤/状态) | admin / `integration:manage` |
| DELETE | `/workspaces/{ws}/webhook-subscriptions/{id}` | 删除订阅 | admin / `integration:manage` |
| POST | `/workspaces/{ws}/webhook-subscriptions/{id}/resume` | 恢复熔断/暂停的订阅(`fail_count` 清零) | admin / `integration:manage` |
| GET | `/workspaces/{ws}/webhook-subscriptions/{id}/deliveries` | 投递台账(状态过滤,重试历史) | 成员 |
| POST | `/workspaces/{ws}/webhook-subscriptions/{id}/deliveries/{delivery_id}/retry` | 手动重试某条失败投递 | admin / `integration:manage` |

**外部身份连接(建链/解链,HIGH-1 信任根;R5 全局所有权模型)**:

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/workspaces/{ws}/external-identities` | 列出**当前成员所属全局身份**已连接的外部身份(`external_identities.user_id` = 请求者经本工作区成员行解析的 `users.id`;全局表按所属用户过滤,非按工作区过滤,R5) | 成员(仅本人所属身份的映射) |
| POST | `/workspaces/{ws}/external-identities:link` | **建链**:将请求者本人的外部平台账号关联到**请求者本人的全局登录身份 `users.id`**(经其本工作区成员行的 `user_id` 解析,R4;**建链目标固定为请求者本人,不接受指向他人用户/成员行的参数**);请求体 `{provider, integration_id, external_account_ref}`(`provider_tenant_key` 由 integration 实例归一,不由请求体提供;**`external_account_ref` = 请求者本人的外部账号标识(钉钉 `senderStaffId` 等),验证码模式下定向私聊的必备目标**——钉钉单聊经 `oToMessages` 需 staffId;OAuth 模式下该字段可选,以 OAuth 返回的平台身份为准);服务端经集成出站适配器**向该外部账号私聊下发一次性验证码**(或走外部平台 OAuth 确认,服务端核对 OAuth 返回的平台用户身份与请求者会话),验证码 TTL 10 分钟 + 单次消费;校验通过方写入 `external_identities` 行(映射为全局行,**`created_in_workspace_id` = `{ws}` 仅作建链来源审计,R5**) | 成员(仅本人) |
| POST | `/workspaces/{ws}/external-identities:link-confirm` | **建链确认**:提交验证码 `{provider, integration_id, code}`;服务端校验验证码(匹配 + 未过期 + 未消费)→ 写入映射(`user_id` = 请求者全局身份,`created_in_workspace_id` = `{ws}` 审计);`UNIQUE(provider, provider_tenant_key, external_user_key)` 拒绝同一外部账号重复映射(409 `identity_already_linked`,R4 全局身份键) | 成员(仅本人) |
| DELETE | `/workspaces/{ws}/external-identities/{id}` | **全局解链(R5:仅所属用户本人,无 admin 旁路)**:删除该全局映射;**仅当请求者经 `{ws}` 成员行解析的 `users.id` 等于映射的 `user_id`(映射所属用户本人)时放行**,否则 `403 identity_unlink_forbidden`——**工作区 admin/owner 不得解链他人的全局身份**(管理员只能经 member.md 撤销该用户在本工作区的使用权/成员资格,其卡片回调随之在本工作区回落 403,全局映射不动);解链后该外部身份的卡片点击在**所有工作区**立即恢复为「未映射 → 403」 | 成员(仅映射所属 `users.id` 本人) |

**OAuth 授权(IM/VCS 连接器)**:

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspaces/{ws}/integrations/oauth/{kind}/authorize` | 发起 OAuth 授权码 + PKCE(302 跳外部平台授权页,`state` 防 CSRF) |
| GET | `/integrations/oauth/{kind}/callback` | 授权回调:校验 `state` + 换取 token,**refresh token 只存密文**(`secret_ref`),最小 scope |

### 3.2 入站回调端点(平台签名校验,非 Bearer)

| 方法 | 路径 | 平台 | 签名方案 |
|------|------|------|----------|
| POST | `/api/v1/integrations/feishu/events` | 飞书/Lark | `signature = SHA256(timestamp + nonce + encrypt_key + raw_body)`(取 `timestamp`/`nonce` 头);恒定时间比较 + 时间戳防重放;**`url_verification` challenge 处理见下** |
| POST | `/api/v1/integrations/feishu/cards` | 飞书/Lark | 交互/审批卡片回调(同签名方案);回调经 `card.action.value` 携带 `approval_id`;**服务端必须从回调载荷提取点击者外部身份(飞书 `open_id`)→ 经 `external_identities`(`(provider, provider_tenant_key, external_user_key)` 全局身份键)映射到全局 `users.id`,再由接收回调的集成实例解析所属 workspace、JOIN 该 workspace 的 `members(workspace_id, user_id)` 得名册行 → 按 README §6.10 权限行再校验(未映射/该用户在此工作区无名册行/无权限 → 403,审批状态不变,留痕)→ 方可转发 `POST /approvals/{id}/approve\|reject`**(R4 映射模型) |
| POST | `/api/v1/integrations/slack/events` | Slack | `X-Slack-Signature: v0=HMAC_SHA256(signing_secret, "v0:" + X-Slack-Request-Timestamp + ":" + raw_body)`;恒定时间比较 + 时间戳防重放;`url_verification` 回显 `challenge` |
| POST | `/api/v1/integrations/slack/cards` | Slack | Block Kit 交互回调(`X-Slack-Signature` 同方案);`actions[].value` 携带 `approval_id`;**同飞书:提取 `user_id`(连同 `team_id` 归一平台租户)→ `external_identities` 映射全局 `users.id` → 集成解析 workspace 后 JOIN 该 workspace 名册行 → §6.10 权限校验 → 转发统一审批端点**(R4 映射模型) |
| POST | `/api/v1/integrations/github/events` | GitHub | `X-Hub-Signature-256: sha256=HMAC_SHA256(webhook_secret, raw_body)`;`X-GitHub-Delivery` 作 `external_event_id`;`X-GitHub-Event` 作事件类型 |
| POST | `/api/v1/integrations/gitlab/events` | GitLab | `X-Gitlab-Token`(共享密钥,恒定时间比较)或 `X-Gitlab-Signature`(HMAC);`X-Gitlab-Event` 作事件类型;`event_uuid` 作 `external_event_id` |
| POST | `/api/v1/integrations/dingtalk/events` | 钉钉/DingTalk | **HTTP 回调模式**(`config.receive_mode='http'`):请求头 `timestamp`(毫秒)+ `sign = Base64(HMAC_SHA256(app_secret, timestamp + "\n" + app_secret))`;恒定时间比较 + 时间戳防重放(**钉钉官方容差 ±3600s**,严于其上限即拒绝合法回调,不得收窄);经 body `chatbotCorpId`(+ `robotCode`)定位集成;`msgId` 作 `external_event_id`。**Stream 模式不经本端点**( Mesh 侧主动出连,见下) |

**钉钉 Stream 模式入站通道(`config.receive_mode='stream'`,推荐)**:

钉钉企业内部机器人 Stream 模式下,**钉钉不回调 Mesh**,而由 Mesh 侧**主动与钉钉网关建立 WebSocket 长连接**接收推送(免公网回调地址、免入站端口):

```
stream worker(常驻进程,与 outbox relay 同类的基础设施 worker)启动
  → 对每个 status='active' 且 receive_mode='stream' 的 im_dingtalk 集成:
     POST https://api.dingtalk.com/v1.0/gateway/connections/open
       { "clientId": "<app_key>", "clientSecret": "<app_secret(密文解出,仅内存)>",
         "subscriptions": [ { "type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get" },
                            { "type": "CALLBACK", "topic": "/v1.0/card/instances/callback" } ],
         "ua": "mesh-integration/<version>" }
     → 得 { endpoint, ticket }(均短期有效)→ WSS 连接 wss://<endpoint>?ticket=<ticket>
  → 收到 topic='/v1.0/im/bot/messages/get' 帧(载荷同 HTTP 回调 body:msgId/conversationId/
     conversationType/senderStaffId/text.content/sessionWebhook…)
     → 【同事务】走统一摄取管线(§3.2 统一摄取流程,与 HTTP 端点共用同一服务函数)
     → 摄取事务提交后向钉钉回帧 ACK { "code": 200, "headers": <原帧 headers>, "message": "OK",
       "data": "received" } —— ACK 必须返回,否则钉钉按未确认重推(重推经 msgId 去重幂等,§3.2)
  → 连接断开/心跳超时(config.stream_reconnect.heartbeat_timeout_seconds,默认 90s 无帧)
     → 指数退避重连(base 2s,max 300s,±20% 抖动,config.stream_reconnect)→ 重走 connections/open
  → 集成 disabled/删除 → 关闭该集成长连接;凭据轮换 → 断连并以新密文重连
```

> **Stream 通道的签名等价性(签名校验适配点)**:Stream 帧**没有**逐帧签名头,其真确性由 `connections/open` 的 `app_key`/`app_secret` 鉴权在**通道层**一次性确立(密文错/凭据撤销 → 连接建不起来,等价于"签名一律无效");已建立通道内的帧以 `signature_status='valid'` 落库,`payload._mesh_channel='stream'` 标注来源信道。HTTP 模式则是逐请求签名校验(`signature_status` 按校验结果)。两种模式的下游(去重/审计/匹配/排队/派发)**完全一致**——这是"签名校验适配点"的两种实现形态,不是两套摄取机制。**通道层信任不豁免任何下游授权**:帧仍经去重/绑定匹配/命令平面三元组鉴权/频率护栏全链约束。
>
> **传输硬化(硬约束)**:仅接受 `wss://` 网关 endpoint(非 wss 即拒连 + 告警,防降级);强制校验网关 TLS 证书(禁 `verify=False`);`ticket` 是钉钉协议要求的短期一次性建连凭据(置于 WS URL query 为平台协议强制,构成 README §6.16「禁 URL query 传 token」的**显式命名例外**:该约束针对 Mesh 自有 `/ws` 网关的长期会话 token,钉钉 ticket 短时效 + 单次使用 + 不落日志/事件载荷/出站台账),缓解 = wss + 证书校验 + 每轮重连重新换取。
>
> **单实例互斥与崩溃安全**:同一集成的 Stream worker 以数据库咨询锁(`pg_advisory_lock(hashtext('dingtalk_stream:'||integration_id))`)保证全局单连接,避免双连接导致钉钉侧负载与重复推送;即便互斥失效,`integration_events.UNIQUE(integration_id, external_event_id)` 去重仍是最终幂等兜底。worker 崩溃由进程守护(compose `restart: unless-stopped`)重拉,重连后钉钉重推未 ACK 帧,不丢消息。
>
> **`sessionWebhook` 的处理**:入站载荷携带有效期约 1 小时的 `sessionWebhook`(钉钉侧快捷回复地址);本模块**不将其作为出站主通道**(短时效、不可靠、不利审计),仅记录于事件载荷备查;出站一律经 OpenAPI + `accessToken`(§3.10),受 §6.16 SSRF 防护(`oapi.dingtalk.com`/`api.dingtalk.com` 之外的用户可控地址不参与出站)。

**统一摄取流程**(复用 autopilot.md §3.2 范式,所有入站端点与钉钉 Stream 通道共用):

```
接收(定位集成:飞书经 app_id/encrypt_key、Slack 经 team_id、钉钉经 chatbotCorpId+robotCode、
      GitHub/GitLab 经 installation/绑定路由;钉钉 Stream 帧与 HTTP 回调进入同一入口函数)
  → 校验平台签名(signature_status;Stream 通道 = 通道层鉴权,帧恒 valid;**invalid/missing 一律落库
     integration_events(process_status='rejected',external_event_id='rejected:<raw-body-hash>')
     并返回 401,绝不分发不路由**——rejected 前缀独立命名空间防预占)
  → 集成 status='disabled' → 落库 rejected(401 integration_disabled)
  → 签名通过 → 落库(received)→ 以 (integration_id, external_event_id) 去重插入
     (命中唯一冲突 → process_status='deduped',幂等返回 200,不再分发)
  → 【命令平面(IM 连接器,§3.7)】文本(trim 后)命中命令前缀 `/stop`、`/btw` 等注册命令
     → 即时执行命令处理器(鉴权经 external_identities → 成员解析):
         /stop → 取消发起人在本会话的在途执行 + 取消其 pending 队列项,机器人回执行反馈
         /btw → 向本会话 processing 队列项的执行追加补充上下文(不可信数据,§6.15);
                无 processing 项 → 剥前缀后按普通消息继续下行
         未知命令 → 回帮助文本;命令消息不触发执行、不入队列
     → process_status='processed'(命令已处置),流程止于审计
  → 匹配 integration_bindings(external_ref + match_config):
       未匹配 / 未匹配到 agent → 仅审计(matched 留痕,不触发,README §6.9)
       命中 → 【同事务】入队 integration_message_queue(pending,seq 会话内递增,§2.10)
              + 按 config.inbound_queue 决定派发时机:
                parallel(飞书/Slack 默认)→ 同事务写 outbox(execution.enqueue,§6.9 幂等键)
                serial_conversation(钉钉默认)→ 由队列派发器(§3.9)在本会话无 processing 项时
                                              经 outbox 写 execution.enqueue
              → process_status='dispatched'
  → 【emoji 确认接收(§3.8)】入队事务【同事务】写 outbox('im.send',幂等键 §6.5)
     → 出站快 relay 秒级消费发确认消息(at-most-once,失败仅审计;不经 notification_delivery)
  → relay 消费 outbox 入队 task_executions(trigger='integration'),【同事务】经
     trigger_event_id 反查队列项回写 execution_id + state='processing'(§3.9 执行关联回写)
  → 执行终态事件(runtime.md execution.completed/failed/cancelled)经 outbox 回写队列项
     done/failed/cancelled + 同事务写 'imq.dispatch_wake' 唤醒派发器(§3.9)
  → 串行模式下派发器随即派发本会话下一 pending 项;摄取事件置 'processed'
```

> **`process_status='dispatched'` 在串行模式下的语义注记**:串行模式下摄取事务置 `dispatched` 表示"已入会话队列、派发器将按序派发"(队列项 `state` 是派发粒度的细化真源);并行模式下即 autopilot 范式的原义(入队即写 execution.enqueue)。该词不新增枚举值(保持与 autopilot.md §2.5 词汇同构),语义分叉仅此一处注记。

> **去重防预占(可用性保护,同 autopilot.md §3.2)**:被拒事件的 `external_event_id` 用 `rejected:` 前缀 + 原始请求体哈希,与合法事件命名空间隔离——攻击者无法用伪造未签名请求预占外部事件 ID 使后续合法事件被静默去重。

**飞书 URL verification challenge**(订阅校验):
```json
// 飞书发起订阅校验(明文模式)
{ "challenge": "ajls384kdjx98XX", "token": "<verification_token>", "type": "url_verification" }
// 集成平台校验 token 后原样回显 challenge(裸 JSON,不套成功包络)
{ "challenge": "ajls384kdjx98XX" }
```
> 加密模式下先解密再回显;`token` 校验失败返回 401 `invalid_signature`。Slack `url_verification` 同理回显 `{"challenge": ...}`。

**入站响应**(与外部平台约定的裸 JSON,同 autopilot.md §3.2,不套 README §6.14 成功包络):
```json
// 200(幂等;重复事件同样 200)
{ "received": true, "event_id": "evt_9f2a...", "process_status": "dispatched" }
// 重复事件
{ "received": true, "event_id": "evt_9f2a...", "process_status": "deduped" }
// 签名失败 401
{ "error": {"code": "invalid_signature", "message": "signature verification failed", "details": {}} }
```

### 3.3 VCS 关联端点

> **真源表(R3 补齐)**:本节全部 CRUD / 自动关联 / issue 侧栏展示一律读写 **`vcs_links` 表**(§2.8 新增:同租户复合 FK、外部对象唯一键、状态索引)。此前仅有端点与 UI 而无真源表,关联无处落库;R3 起 `vcs_links` 是唯一权威,侧栏「关联的 PR/MR」即 `vcs_links WHERE mesh_entity_type='issue' AND mesh_entity_id=:id AND status='active'`。

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| POST | `/api/v1/integrations/vcs/links` | **显式关联** PR/commit/branch ↔ issue(请求体 `{integration_id, vcs_ref:{type, url, id}, mesh_entity_type:'issue', issue_id}`)→ INSERT `vcs_links`(`link_source='manual'`,`created_by`=当前成员) | 成员(对 issue 有写权限) |
| DELETE | `/api/v1/integrations/vcs/links/{id}` | 解除关联(置 `status='deleted'`,保留审计;唯一键为部分索引,释放外部对象占位) | 成员 |
| GET | `/api/v1/issues/{issue_id}/vcs-links` | 列出某 issue 的 VCS 关联(PR/commit/branch + `external_state` 状态快照) | 成员 |
| POST | `/api/v1/integrations/vcs/resolve` | **identifier 解析**:从文本/分支/PR 标题提取 `WEB-123` 自动关联(请求体 `{integration_id, source_text, vcs_ref}`)→ 命中写 `vcs_links`(`link_source='auto_keyword'\|'auto_branch'\|'auto_commit'`) | 成员 / 入站摄取内部调用 |

**identifier 自动关联**:VCS 入站事件(`pull_request`/`merge_request`/`push`/`commit_comment`)的标题、正文、分支名、commit message 中匹配 `<前缀>-<号>`(issue.md `identifier`,如 `WEB-123`)→ 经 `UNIQUE(workspace_id, identifier)` 解析到 issue → 自动建立 `vcs_links` 行(命中 `uq_vcs_links_external_object` 部分唯一索引即幂等跳过,重复事件不重复建关联);解析不到(前缀不存在/已软删)→ 仅审计不报错(`identifier_not_resolved` 留痕)。**仓库必须属于已绑定工作区的 VCS 集成**:`integration_bindings` 的外部身份全局唯一键(§2.3)保证一个仓库至多归属一个工作区,摄取时经 `(provider, provider_tenant_key, external_ref=owner/repo)` 反查绑定定工作区;无绑定 → 仅审计不分发。

**自动状态流转**:VCS 绑定 `match_config.auto_status_map`(如 `{"merged":"done","closed":"cancelled"}`)在对应事件入站且成功关联 issue 后,经 issue.md 状态流转端点把 issue 置目标状态(服务层校验目标状态存在且迁移合法;以摄取事件幂等,重复事件不重复改状态),同步刷新 `vcs_links.external_state`(如 `{"pr_state":"merged"}`,状态变 `stale` 表示已合并/关闭的陈旧关联),并在 issue 发评论留痕("PR #N 已合并,自动置为 done",经 comment-inbox.md,幂等键 §6.5)。

### 3.4 出向订阅投递(经 outbox,README §6.6)

```
领域事件(issue.updated 等)的业务事务同事务写 outbox_events(event_type='realtime.publish' 与
  'webhook.dispatch' 分别承载实时与出向;幂等键 §6.5)
  → outbox relay 分发 'webhook.dispatch':取 event_types 命中且 status='active' 的订阅
  → 每订阅在投递事务内 INSERT webhook_subscription_deliveries(state='pending',
     UNIQUE(subscription_id, event_ref) 幂等)
  → 投递 worker 取 pending(按 next_retry_at)→ HTTPS POST 目标 URL:
       头:Mesh-Signature: t=<ts>,v1=HMAC_SHA256(secret, "<ts>.<body>")
           Mesh-Event: <event_type>   Mesh-Delivery: <delivery_id>
       目标受 §6.16 SSRF 防护(禁私网段 / https-only)
  → 2xx → state='sent',订阅 fail_count 清零
  → 非 2xx/超时 → attempts+1,计算 next_retry_at(指数退避+抖动);超 retry_max_attempts → state='failed'
       订阅 fail_count+1;超 circuit_break_threshold → 订阅 status='disabled'(熔断)+ 告警
```

> **不**在业务事务外直接 POST 外部 URL(README §6.6 硬约束);出向投递是 outbox 的消费方。投递幂等键 `sha256(subscription_id | event_ref)`(README §6.5)。

### 3.5 错误码表(本模块具名,通用码见 README §6.14)

| HTTP | code | 场景 |
|------|------|------|
| 400 | `invalid_request` | 请求体/参数非法 |
| 400 | `invalid_url_scheme` | 出向订阅 URL 非 `https`(README §6.16) |
| 401 | `invalid_signature` | 入站平台签名校验失败 / 缺失(绝不分发) |
| 401 | `invalid_challenge` | URL verification 的 token/challenge 校验失败 |
| 401 | `unauthorized` | 管理端点缺少/无效 Bearer token |
| 403 | `forbidden` | 无权限操作该集成/绑定/订阅 |
| 403 | `identity_unlink_forbidden` | 解链他人全局外部身份(R5:解链仅映射所属 `users.id` 本人;admin/owner 角色不构成授权,无旁路) |
| 403 | `command_forbidden` | IM 命令(`/stop` 取消他人任务、队列项非本人取消等)越权(§3.7;未映射身份的提示走机器人反馈文本,不经本错误码) |
| 404 | `not_found` | 集成/绑定/订阅/投递不存在 |
| 409 | `binding_conflict` | `UNIQUE(provider, provider_tenant_key, external_ref)` 冲突(R3 全局键)——该外部身份已被(可能另一工作区的)绑定占用 |
| 409 | `conflict` | 名称重复 / 乐观锁冲突 |
| 409 | `duplicate_event` | 入站去重命中(通常作 200 `deduped`,内部用) |
| 410 | `integration_disabled` | 集成 `status='disabled'`,入站拒绝分发 / 出站停发 |
| 422 | `ssrf_blocked` | 出向目标命中私网地址段 / 元数据地址(README §6.16) |
| 422 | `identifier_not_resolved` | VCS identifier(`WEB-123`)解析不到 issue(留痕,不阻塞摄取) |
| 422 | `vcs_link_invalid` | VCS 关联的 issue/vcs_ref 非法或跨工作区 |
| 422 | `subscription_circuit_open` | 订阅处于熔断(`disabled`),需 `resume` 后投递 |
| 422 | `queue_item_not_cancellable` | 队列项非 `pending` 态(已派发/终态),不可取消(§3.9;在途执行走 `/stop` 或执行取消协议) |
| 422 | `oauth_failed` | OAuth 授权码换取 token 失败 / scope 不足 |
| 429 | `rate_limited` | API 限流 / 出站平台限流退避 |
| 500 | `internal_error` | 服务内部错误 |
| 502 | `upstream_error` | 外部平台 API 调用失败(出站适配) |
| 503 | `stream_channel_unavailable` | 钉钉 Stream 长连接未就绪(管理端点触发测试发送/连接诊断时;摄取侧不返回本码——HTTP 模式不可达即平台侧重推,Stream 模式无入站端点) |

### 3.6 WebSocket 实时事件

连接 `/ws`(握手鉴权见 auth.md),订阅频道 `workspace:{ws}:integrations` 或 `integration:{id}`。**实时契约以 README §6.7 为唯一权威**(频道内 `seq`、唯一写入路径 outbox→projector、`resume_from`/`resync_required`);事件名取自 README §6.7 注册表「平台能力」域:

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `integration.updated` | 集成/绑定/订阅创建、配置变更、状态切换、熔断;**钉钉 Stream 连接状态变化**(`subject='stream_channel'`,`status`='connected'\|'reconnecting'\|'down') | `integration_id`, `kind`, `status`, `subject`('integration'\|'binding'\|'subscription'\|'stream_channel') |
| `integration.event_ingested` | 入站事件落库(含签名/处理状态、命令处置标记,驱动事件台账实时刷新) | `event_id`, `integration_id`, `event_type`, `signature_status`, `process_status` |
| `integration.queue_updated` | 入站消息队列项状态变化(入队/派发/终态/取消,§3.9;驱动队列面板实时刷新) | `item_id`, `integration_id`, `conversation_key`, `state`, `position` |

> 不使用未登记事件名(README §6.7 词汇零容忍)。降级:WebSocket 不可用时事件台账退化为轮询 `GET .../integrations/{id}/events`(3~5s)。

### 3.7 IM 命令平面(`/stop` / `/btw`,MES-82)

入站 IM 文本消息在去重之后、绑定匹配之前,经**命令平面**解析:命令是控制平面消息,**即时处理、不入队列、不触发执行**(除 `/btw` 无在途任务时的降级路径)。三平台语义统一(钉钉/飞书/Slack 共用命令注册表与处理器,仅文本归一各走连接器适配点)。

**解析规则**:
- 文本归一:钉钉 `text.content` 去除 @机器人 前缀与首尾空白(钉钉在 @机器人 后注入一个前导空格,必须 trim)→ 得 `normalized_text`。
- 命令判定:`normalized_text` 匹配 `^/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+([\s\S]*))?$` → `(command, args)`;**大小写不敏感**;仅**行首** `/` 起算(消息正文中间的 `/stop` 不是命令,按普通文本处理)。
- 命中注册命令 → 执行处理器;未注册的 `/xxx` → 机器人回帮助文本(命令清单 + 一句话说明),`process_status='processed'`,**不触发执行**(防命令探测注入)。

**命令注册表(可扩展)**:

| 命令 | 参数 | 语义 | 授权 |
|------|------|------|------|
| `/stop` | 可选 `[原因]`(仅审计) | 取消**命令发起人**在**本会话**的在途执行与排队项(见下) | 发起人本人(**将发起人外部身份全三元组与目标队列项 `sender_identity_key`(全三元组,§2.10)各自经 `external_identities` 解析到 `users.id`,两 `users.id` 相等**;禁止 UUID 与外部键字符串直接比对、禁止裸键解析);或对该绑定所属工作区/项目有 `execution:manage` 权限的成员(经成员名册链解析,同卡片回调鉴权链 §3.2) |
| `/btw` | 必填 `<补充说明>`(受 `MESH_IM_INBOUND_TEXT_MAX_CHARS` 截断,§2.10) | 向本会话**正在处理**(队列项 `state='processing'`)的执行追加补充上下文;不打断、不新建执行 | 同 `/stop`(越权拒绝路径同 `/stop` 第 4 步:回拒绝文本 + 审计、不泄露目标任务详情)。**补充内容一律按不可信数据隔离注入**(README §6.15:结构化包裹 + 标注来源 `im_btw`,agent 不得作为指令执行;高风险动作仍走 `confirm_required`) |
| `/help`(内置) | 无 | 回命令清单与用法 | 任何人(含未映射身份) |

> 注册表是服务层常量结构 `{name: {permission, handler}}`,新增命令 = 登记一行 + 实现处理器,不改摄取管线(YAGNI:本期仅 `stop`/`btw`/`help`,不预建别名/参数解析框架之外的机制)。

**`/stop` 处置序列(确定性语义,可测试)**:
1. 解析发起人外部身份**全三元组**(`provider`+`provider_tenant_key`+`external_user_key` → `external_identities.users.id`);**未映射 → 机器人回"请先在 Mesh 站内连接你的外部账号"并附建链入口提示,仅审计,不取消任何东西**。
2. 查本会话(`conversation_key`)`state='processing'` 的队列项,授权判定:项的 `sender_identity_key` 全三元组经 `external_identities` 解析的 `users.id` == 发起人的 `users.id`(本人),或请求者具备 `execution:manage`:
   - 命中 → **先提交命令审计事务**(`integration_events` 命令留痕 + 队列项原子置 `cancelled`,`WHERE state='processing'` 守卫),**再于事务外**调用 runtime 执行取消端点(`POST /executions/{id}:cancel`,runtime.md:即时置 `cancelling`、daemon 经心跳下行真停;该端点自身经 outbox 发 `execution.cancelled`,守 README §6.6;`failure_reason='cancelled_by_command'`,runtime.md 登记词汇);取消是幂等的——执行已在终态则 no-op,队列项经终态→终态守卫不被重复改写;
   - 再取消该发起人在本会话的全部 `pending` 队列项(原子批量 `UPDATE … WHERE state='pending' AND <三元组解析本人>`,按 `seq` 序);
3. 机器人回执行反馈消息(命中:"已停止任务「<消息摘要>」,并取消 N 条排队消息";无在途:"当前没有进行中的任务")。**取消调用失败(runtime 不可达)→ 回"取消请求失败,请重试",不改队列项**(不产生"项已取消、执行仍在跑"的撕裂态)。反馈消息经 outbox `im.send` 发送(§3.8 同通道),不是确认接收,不经 ack 模板与合并窗口。
4. 越权(发起人对他人任务发 `/stop`)→ 机器人回 `command_forbidden` 语义的拒绝文本(IM 命令无 HTTP 客户端,错误码为内部分类,渲染为机器人文案;HTTP 403 形态仅适用 `:cancel` 端点)+ 审计留痕,**不泄露目标任务详情**。

**`/btw` 处置序列**:
1. 授权同 `/stop`(越权 → 拒绝文本 + 审计,不泄露详情,不注入)。
2. 本会话存在 `state='processing'` 队列项 → 经 **runtime 运行期上下文追加机制**(`execution_context_appends`,runtime.md owns:服务层以队列项 `execution_id` 写入 append 行)向其执行追加补充消息(载荷 `{source:'im_btw', sender:<三元组解析的 Mesh 身份>, text:<args(截断后)>, received_at}`,**不可信数据隔离**,README §6.15);**生效时机:LLM 单轮不可打断,补充在该执行下一 agent turn 边界注入**(runtime.md/agent.md 已登记该机制);机器人回"已补充给正在处理的任务(将在下一步生效)"。
3. 无 `processing` 项 → 剥除 `/btw` 前缀后的 `args` **按普通消息继续下行**(匹配 → 过频率护栏 → 入队),机器人先回一句提示"当前没有进行中的任务,已按新消息排队"。
4. `/btw` 无参数 → 回用法帮助。

**不可信与防滥用**:命令参数(尤其 `/btw` 文本)是入站不可信内容,隔离与长度上限(4000 字符截断)同消息正文(§2.10);命令平面频率受 **§2.10「入站频率护栏」** 三道计数约束(每身份/每会话/排队深度);命令处置全程写审计(`integration_events.payload._mesh_command = {name, actor_identity(三元组), target_item_ids, result}`)。

### 3.8 emoji 确认接收(ack,§1.1 MES-82 能力 2)

**平台约束与等价语义**:钉钉开放平台**不提供**机器人对任意消息添加 emoji 回应(reaction)的 OpenAPI(消息级 reaction 仅客户端人工操作可及);飞书/Slack 虽有 reaction API,但为三平台行为一致与实现单一,本模块**统一以"轻量确认消息"实现 emoji 确认接收**——机器人在摄取成功后**立即**回一条以 emoji 起始的短消息(默认模板 `✅ 已接收,处理中`,经 `integrations.config.ack_template` 可按集成配置;置空字符串 = 关闭该集成的确认消息)。

**时序与一致性(经 outbox 快通道,守 README §6.6 硬约束)**:
```
摄取事务【同事务】写 outbox_events(event_type='im.send',
  幂等键 sha256(queue_item_id | 'ack')(README §6.5 登记键),
  payload { kind:'ack', conversation_key, template, position_snapshot })
  → 出站快 relay(消费 'im.send' 高优先级通道,outbox relay 同进程集合的受监督任务,
    目标端到端延迟 <2s;§3.9)经钉钉 OpenAPI 发确认消息(§3.10)
  → 发送成功 → 回写 integration_message_queue.ack_sent_at(state 守卫:ack_sent_at IS NULL 方写)
  → 失败(平台限流/网络)→ 就地重试 1 次(3s 超时);仍失败 → 仅审计 + 告警,不阻塞执行、
    不进死信(确认消息是体验增强,丢一条 ✅ 不影响任务真源;at-most-once)
```
> **不**在摄取请求内同步直发外部平台(README §6.6:外部可见副作用一律经 outbox;ack 是该硬约束下的常规成员,不是例外)。快 relay 的秒级消费保证用户感知即时性;快 relay 不可用时 ack 延迟退化为 relay 恢复时补发窗口内的 at-most-once 尝试,任务派发不受影响(派发经独立 `execution.enqueue` 通道)。

**合并窗口(防 ack 反射放大,安全护栏)**:同一 `conversation_key` 在 `MESH_IM_ACK_COALESCE_WINDOW`(默认 5s,Redis 会话级键)内的多条入队项**仅发一条**合并确认消息("✅ 已接收 N 条任务,处理中/排队中(第 X 位)"),窗口内各项 `ack_sent_at` 一并回写;窗口状态不入业务库。

**规则(可测试)**:
- **仅 `dispatched` 的任务消息触发 ack**:去重(`deduped`)、未匹配、被限频(`rejected` + `_mesh_reject_reason='rate_limited'`)、命令消息、被拒消息**一律不发 ack**(避免重复事件刷确认、避免给未绑定群发噪音、避免灌消息放大出站配额)。
- **at-most-once**:outbox 幂等键 `sha256(queue_item_id | 'ack')` + 队列项 `ack_sent_at IS NULL` 双守卫;摄取后进程崩溃 → 快 relay 恢复后仍按幂等键至多发一条(跨进程数据库级幂等,非仅进程内前置)。
- **即时性**:快 relay 目标端到端延迟 <2s(§5.6 以该指标验收,不依赖 CI 时序抖动断言)。
- **串行排队下的 ack**:串行模式下排队项**入队时即 ack**(每条消息都让用户知道"被接住了"),而不是轮到执行时才 ack;ack 文案对排队项附带入队时刻的位置快照(`position_snapshot`,= 入队事务内本会话更小的 pending 计数 + 1),且**措辞为对冲式**("已排队(入队时第 N 位,可能很快轮到)")——位置是入队时刻 best-effort 快照而非实时值(前序项可能秒级完成),快 relay 发送前可选重算一次当前位置,已无更小 pending 时去掉"排队中"字样。
- **限频共享**:ack 出站量受入站频率护栏(§2.10「入站频率护栏」)上游约束——超限消息根本不入队、不产生 ack 事件,从源头遏制"N 条灌入 → N 条 ack → 平台发送配额耗尽"的反射放大。
- **台账**:确认接收与命令反馈是**会话性回复,不是通知**——不经 `notification_delivery`(其 `notification_id` NOT NULL 挂靠 `notifications` 真源,comment-inbox.md §2.8),发送结果记入队列项 `ack_sent_at` 与 `integration_events.payload`;任务进度/结果等**真通知**才经 `notification_delivery(channel='im', provider='dingtalk')`(README §6.13,§3.10)。

### 3.9 入站消息队列(§2.10,「新消息自动排队」)

**部署形态**:队列派发器(queue dispatcher)与出站快 relay(im.send fast relay)、钉钉 Stream worker 均为 **`mesh.workers` 进程内新增的受监督 asyncio 任务**(backend/README.md 既有 worker 任务集合的同构扩展,**不新增 compose service**、不改变部署拓扑);与 outbox relay/projector 共享进程监督与重试策略。**租户安全上下文对齐(纵深防御)**:三者均以 DB owner 角色运行(跨租户扫描),凡进入"与 HTTP 摄取同一服务函数"的路径,**入口处显式 `set_tenant_context(workspace_id)`(设 `mesh.workspace_id` GUC)使 RLS 与 HTTP 路径(mesh_app 受限角色)等价生效**,不因 worker 特权身份旁路 RLS。

**派发器(queue dispatcher)**:消费 `integration_message_queue`,**按项的 `dispatch_mode`(入队时快照)而非集成实时 config 决定行为**(集成级模式切换不追溯存量项——serial→parallel 切换后,存量串行 pending 项仍由派发器按 serial 清空,不被饿死):

```
循环(1s tick 兜底 + 经 outbox 'imq.dispatch_wake' 事件显式唤醒):
  取候选会话:存在 dispatch_mode='serial_conversation' 的 pending 项 且 无 processing 项
    (uq_imq_conversation_processing 部分唯一索引保证至多一并发)
  对每个候选会话(会话间并发、互不阻塞):
    FOR UPDATE SKIP LOCKED 取该会话最小 seq 的 pending 项
    → 双检本会话无 processing 项与在途执行
    → 同事务:UPDATE 项 state='processing', started_at=now(),
              lease_expires_at = now() + 执行超时上限 + 缓冲(§6.4 runtime 超时)
              + 写 outbox(execution.enqueue, trigger='integration',
                 幂等键 sha256(agent_id | binding_id | external_event_id),§6.9)
  dispatch_mode='parallel' 的项:摄取事务内已直接写 execution.enqueue(§3.2),
    不经派发器等待;其 state 推进统一由「执行关联回写」完成(见下)
```

**执行关联回写(两种模式共用,取代未定义的"入队回执")**:`execution.enqueue` 的 outbox 消费方(relay)在创建 `task_executions` 的**同一事务**,以 `trigger_event_id → integration_message_queue.integration_event_id` 反查队列项:`UPDATE integration_message_queue SET execution_id=:exec, state='processing', started_at=COALESCE(started_at, now()), lease_expires_at=now()+timeout+buffer WHERE integration_event_id=:evt AND state='pending'`(串行项已被派发器置 `processing` → 本 UPDATE 的 state 守卫仅补 `execution_id`,幂等)。队列面板"处理中项 → 执行详情深链"在两种模式下均经此回写成立。

**终态回写与不丢失保证**:
- 执行终态经 runtime 执行事件(outbox 消费,非轮询;事件名取 runtime.md 登记词汇):`execution.completed` → 项 `done`;`execution.failed`/`execution.timeout` → 项 `failed`;`execution.cancelled` → 项 `cancelled`;`finished_at=now()`;回写事务**同事务写 `imq.dispatch_wake`(payload 含 conversation_key)** 唤醒派发器派发该会话下一 pending 项(1s tick 仅作兜底,M1→M2 衔接时延有界)。
- **租约修复(崩溃安全,四分支全覆盖)**:周期扫描 `state='processing' AND lease_expires_at < now()` 孤儿项,按其执行状态分支:
  1. 执行已终态(事件丢失)→ 补回写 `done`/`failed`/`cancelled`;
  2. 执行**仍在途**(claimed/running/cancelling——合法长任务、缓冲取小了)→ **续租对齐**:`lease_expires_at = max(now()+buffer, 该执行当前 attempt 租约)`,不置失败(长任务不误杀);
  3. 执行不存在(入队事件丢失)→ 经幂等键重写 outbox 派发(仅此支真正重派发);
  4. 执行存在但 `queued` 超 `MESH_IM_QUEUE_MAX_STUCK_SECONDS`(默认 = 2×执行超时)→ 置 `failed`(`payload.reason='dispatch_stuck'`)+ 告警,**不重派发**(幂等键固定,重入队必为 no-op,重派是死路)。
  任一分支都使项离开"processing 且过期"集合,杜绝扫描空转;**任何崩溃路径下已入队消息要么被执行、要么进终态可查,不静默丢失**。
- 绑定删除/集成删除:级联删队列项(配置私有不保留);执行已派发的不受影响(执行侧自有生命周期)。

**查询与操作端点**:

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/workspaces/{ws}/integrations/{id}/queue` | 队列状态:按会话分组返回项(`conversation_key`、`seq`、`state`、`position`(pending 项的实时排队位置,= 本会话更小的 pending 计数 + 1)、`sender_identity_key`(经 `external_identities` + 展示层解析显示名)、`ack_sent_at`、`execution_id`、时间戳);过滤 `state`/`conversation_key`;游标分页(README §6.14) | 成员 |
| GET | `/workspaces/{ws}/integrations/{id}/queue/summary` | 轻量汇总:各会话 pending 数 + 当前 processing 项摘要(队列面板徽章/角标用) | 成员 |
| POST | `/workspaces/{ws}/integrations/{id}/queue/{item_id}:cancel` | 取消 **pending** 项;**原子条件更新 `UPDATE … SET state='cancelled', finished_at=now() WHERE id=:id AND state='pending'`(0 行 → `422 queue_item_not_cancellable`,杜绝与派发器的 TOCTOU 竞态)**;**授权:由项的 `conversation_key`/绑定派生 `(provider, provider_tenant_key)`,与项的 `sender_identity_key`(全三元组,§2.10)组全键经 `external_identities` 解析到 `users.id`,与请求者经成员行解析的 `users.id` 比对相等(本人),或请求者对该集成/绑定有 `integration:manage` 权限;禁止仅凭裸 external_user_key 解析** | 成员(本人或 manage 权限) |
| GET | `/workspaces/{ws}/integrations/{id}/stream-status` | 钉钉 Stream 连接状态(`integrations.stream_state`,§2.2):`state`(connected/reconnecting/down/disabled)、`last_frame_at`、`last_attempt_at`、`backoff_seconds`;UI 首屏与 [测试发送] 诊断的真源(实时事件未达前即可读) | 成员 |

**实时**:`integration.queue_updated`(README §6.7 注册表新增,「平台能力」域):入队/派发/终态/取消时经唯一写入路径(outbox→projector)推 `workspace:{ws}:integrations` 频道,payload `{item_id, conversation_key, state, position}`;队列面板据此实时刷新,降级轮询 `.../queue`(3~5s)。

### 3.10 钉钉出站适配(令牌缓存 / 消息发送 / 主动推送 / 卡片)

**accessToken 缓存刷新**(同飞书 `tenant_access_token` 范式,§5.2):出站适配器按集成缓存 `accessToken`(`POST https://api.dingtalk.com/v1.0/oauth2/accessToken` `{appKey, appSecret}`,有效期 7200s),**过期前 5 分钟主动刷新 + 单飞(single-flight)并发保护**;刷新失败/凭据撤销 → 出站投递记 `failed` + 告警;**令牌值与 appSecret 永不回显响应/日志/出站请求调试信息**(README §6.16 全通道脱敏:**解密后的 app_secret/accessToken 一律登记 `redact_in_logs` 黑名单**;`connections/open`/`accessToken` 等携带明文秘钥的出站请求体在日志、错误台账、投递详情中以 `***` 替换,502 排障仅记 `method/url/status`,不记 body)。

**发送通道**:

| 场景 | 通道 | 载荷 |
|------|------|------|
| 群消息(确认接收/进度/结果/命令反馈) | `POST /v1.0/robot/groupMessages/send` `{robotCode, openConversationId(=external_ref), msgKey, msgParam}` | `msgKey` ∈ `sampleText`/`sampleMarkdown`/`sampleActionCard6` 等;`msgParam` 为对应 JSON 字符串 |
| 单聊消息 | `POST /v1.0/robot/oToMessages/batchSend` `{robotCode, userIds:[<senderStaffId>], msgKey, msgParam}` | 同上 |
| 审批/交互卡片 | 钉钉**互动卡片**(模板 + 投放 + 更新):投放经上述发送通道(`msgKey` 为卡片模板),按钮回调经 Stream topic `/v1.0/card/instances/callback`(HTTP 模式经独立回调地址,签名同 §3.2 钉钉行) | 回调鉴权链同飞书/Slack 卡片(§3.2/§4.3 流程 B):点击者 `userId` → `external_identities`(provider='dingtalk', tenant=corp_id)→ 全局 `users.id` → 集成解析 workspace → JOIN `members` 名册行 → README §6.10 权限校验 → 转发 `POST /approvals/{id}/approve\|reject`;未映射/无名册行/无权限 → 403,审批状态不变,留痕 |

**主动推送(任务进度与结果)**:执行进度/结果通知经统一通知管线(README §6.13 `channel='im'`)→ 出站适配器按 `notification_delivery.destination_key='dingtalk:<binding_id>:<conversationId>'` 投递到源会话;台账落 `notification_delivery(channel='im', provider='dingtalk')`;限流退避(钉钉 OpenAPI 速率限制)与失败重试经出站适配器统一处理(同飞书范式)。

**SSRF 与 URL 约束**:钉钉出站目标固定为平台官方域(`api.dingtalk.com`/`oapi.dingtalk.com`),不接受用户可控出站地址(README §6.16);入站载荷中的 `sessionWebhook` 不作为出站目标使用(§3.2 备注)。

**外部联系人单聊出站能力限制(写死的降级)**:`oToMessages/batchSend` 仅支持 `senderStaffId`(企业内部成员);**无 staffId 的外部联系人(`ext:<senderId>` 身份)单聊路径,ack/命令反馈/主动推送不保证送达**——出站适配器检测目标键为 `ext:` 前缀 → 投递记 `failed(reason='no_staff_id')` + 告警,**摄取与执行不受影响**(任务照常运行,结果在 Mesh 站内可查);群聊路径不受此限(群消息以 `conversationId` 投递,不依赖发起人 staffId)。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
集成管理页(/integrations,设置区,admin/owner 可写)
   ├── 连接器目录:卡片网格(飞书/Lark · Slack · 钉钉/DingTalk · GitHub · GitLab · 出向 Webhook),每卡 [连接]/[已连接 N]
   ├── 已连接集成列表:名称 | 类型图标 | 状态徽章(active/disabled)| 绑定数 | 近7天事件量 | 操作(⚙ ⏸ ⋯)
   │      └── 钉钉集成行追加「连接状态」点:connected(绿)/ reconnecting(黄)/ down(红)(Stream 模式,§3.2)
   └── [+ 添加集成](选 kind → OAuth 授权流 / 粘贴 token)
集成详情页:① 概览(非密配置只读 + [编辑] + 凭据状态[轮换]);② 绑定 tab;③ 事件台账 tab(签名/处理状态过滤);④ 消息队列 tab(MES-82)
   └── 钉钉概览追加:接收模式(Stream/HTTP 只读标识 + Stream 连接状态卡:状态点、最近心跳、[测试发送] 按钮;HTTP 模式显示回调 URL + [复制])
绑定配置抽屉:外部身份(群/频道/仓库选择器)+ 作用域(工作区/项目)+ 匹配规则(@agent/关键词/分支模式)+ 目标 agent
出向订阅页(/webhooks):订阅列表(https URL | 事件过滤 | 状态 active/paused/disabled[熔断] | 成功率)
   └── 订阅详情:投递历史时间线(state | attempts | response_status | next_retry)+ [手动重试] [恢复熔断]
VCS 关联(issue 详情侧栏):「关联 PR / 提交」区块 —— 列出关联 PR/commit/branch + 状态(打开/已合并/已关闭)
   + 自动状态流转标记("PR #123 合并 → 自动置 done",带集成图标与时间)
消息队列面板(集成详情「消息队列」tab,MES-82):按会话分组的队列视图 ——
   ├── 每会话卡:会话名(外部群/单聊展示名)+ 当前处理项(消息摘要 → 执行详情深链、运行时长)+ 排队列表(seq/位置/摘要/发起人/入队时间)
   ├── 排队项操作:[取消](本人或 manage 权限;非 pending 置灰 + 提示"已派发,请用 /stop")
   └── 实时刷新(integration.queue_updated;降级轮询 3~5s)
IM 卡片(外部平台内):审批卡片 + 交互卡片(样式约定见 §4.4)
```

### 4.2 关键组件

- **连接器目录卡片**:每个公开集成目标平台一张卡(图标 + 名称 + 简述 + 能力标签"IM 通知/审批卡片/事件触发"或"VCS 关联/状态流转");未连接显示 [连接],已连接显示 [已连接 N 个] 与状态点。
- **OAuth 授权流**:[连接] → 新窗跳外部平台授权页(PKCE)→ 授权后回跳 → 显示"授权成功,已连接",凭据落密文(界面**永不展示** secret 明文;粘贴 token 模式仅显示掩码 `••••abcd`)。
- **绑定配置抽屉**:外部身份选择器(从集成已可见的群/频道/仓库拉取)+ 作用域切换(选项目时显示项目下拉)+ 匹配规则表单(@agent 多选、关键词、VCS 事件勾选、分支模式输入,带语法提示)+ 目标 agent 选择器(留空 = 仅审计不触发,显式提示)。
- **事件台账**:每行 时间 + 事件类型 + 签名状态徽章(valid/invalid/missing)+ 处理状态徽章(received/matched/dispatched/deduped/rejected/processed/failed)+ 载荷预览(只读 JSON,外部内容标注"不可信数据");`rejected`/`deduped` 行高亮原因,直接回答"为什么没触发"。
- **出向订阅投递历史**:时间线每行 state 图标 + attempts + response_status + 退避倒计时(next_retry_at);`failed` 行提供 [手动重试];熔断订阅顶部横幅"已连续失败 N 次,已停用,[恢复]"。
- **issue 侧栏 VCS 关联区块**:关联 PR/commit 列表(图标 + 标题 + 外部状态徽章 + 深链);自动状态流转条目以集成图标标注来源("来自 GitHub 集成 · PR #123 合并");[+ 关联] 手动关联(PR URL/commit SHA 输入)。
- **钉钉连接状态卡(MES-82)**:Stream 模式集成概览顶部 —— 状态点(connected/reconnecting/down)+ 最近心跳相对时间("12 秒前")+ 接收模式徽章;`down` 时横幅"Stream 长连接断开,自动重连中(退避 Ns),期间消息将由钉钉侧暂存并重推";[测试发送] 向选定会话发一条测试文本(经出站适配器,失败回 `stream_channel_unavailable`/`upstream_error` 提示)。HTTP 模式则展示只读回调 URL 与 [复制],不显示连接状态。
- **消息队列面板(MES-82)**:会话分组卡;处理中项显示消息摘要(截断 + tooltip 全文)、目标 agent、运行时长与执行详情深链;排队项显示位置徽章("第 2 位")、发起人(展示名解析,未映射显示外部昵称 + "未连接"标记)、入队相对时间;[取消] 按钮(本人/manage 可见,非 pending 禁用);空态"没有排队消息 —— 在 IM 里 @<agent> 即可派活";`integration.queue_updated` 实时刷新(行级高亮新入队项)。

### 4.3 关键交互流程

**流程 A:连接飞书并绑定值班群**:集成页 → 飞书卡 [连接] → OAuth 授权回跳成功 → 集成详情 → 绑定 tab → [+ 新绑定] → 选"研发值班群"(external_ref)→ 作用域选 INFRA 项目 → 匹配规则勾"@指定 agent"+ 选值班 agent → 保存。群里 @值班 agent → 入站摄取(签名/去重/审计)→ 触发运行 → agent 回评到 issue(IM 消息按不可信数据隔离入上下文)。

**流程 B:审批卡片在 IM 内闭环**:运行命中 `confirm_required` → `approvals` 建审批(README §6.10)→ 出站适配器向绑定 IM 频道推审批卡片(动作/权限/影响/成本/批准按钮)→ 审批人在飞书/Slack 卡片点"批准" → 卡片回调 → **服务端提取点击者外部身份(飞书 `open_id`/Slack `user_id`)→ 经 `external_identities`(`(provider, provider_tenant_key, external_user_key)` 全局身份键)映射到全局 `users.id` → 由接收回调的集成实例解析所属 workspace,JOIN 该 workspace 的 `members(workspace_id, user_id)` 得名册行 → 按 README §6.10 权限行校验(未映射/该用户在此工作区无名册行/无权限 → 403 拒绝,审批状态不变,审计留痕)** → 校验通过 → 转发 `POST /approvals/{id}/approve` → 运行从审批点续跑;台账记 `notification_delivery(channel='im')` 与 approvals `decision_comment`。**同一已认证外部账号可在其所属的多个 Mesh 工作区各自闭环审批(R4:映射全局 users.id,各工作区独立解析名册行与权限)**。

**流程 C:GitHub PR 合并自动流转**:绑定 GitHub 仓库到 WEB 项目(`auto_status_map={"merged":"done"}`)→ 开发者 PR 标题含 `WEB-123` → PR 合并事件入站 → 签名/去重 → identifier 解析关联 `WEB-123` → 自动置 done + 发评论"PR #N 已合并,自动置为 done" → issue 侧栏显示关联 PR 与流转标记。

**流程 D:开发者订阅出向 Webhook**:出向订阅页 → [+ 新订阅] → 填 https URL(非 https 即拒)+ 勾事件类型(`issue.updated` 等)→ 创建后**仅显示一次**签名密钥(提示妥善保存)→ Mesh 事件经 outbox 投递(HMAC 签名 + 重试退避)→ 投递历史可查;连续失败熔断后 [恢复]。

**流程 E:钉钉群内派活全链路(MES-82)**:集成页 → 钉钉卡 [连接] → 填企业内部应用 `app_key`/`app_secret`(密文存储)+ 选接收模式(默认 Stream,免公网地址)→ 保存后 Stream worker 建连(概览状态点转绿)→ 绑定 tab → [+ 新绑定] → 选"研发群"(`conversationId`)+ 目标 agent → 保存。群里 @机器人发"帮我查下昨晚的报警" → **秒级收到 `✅ 已接收,处理中` 回执**(emoji 确认)→ agent 执行,进度/结果主动推回群 → 期间连发两条新任务 → **自动排队**,队列面板显示"第 2/3 位",群里再发 `/btw 重点看 payment 服务` → 机器人回"已补充给正在处理的任务"(补充作为不可信上下文注入,不打断)→ 发现派错发 `/stop` → 机器人回"已停止任务…",排队消息一并取消;全程事件台账与队列面板实时可查。

### 4.4 IM 卡片样式约定(审批卡片,README §6.10)

审批卡片在飞书(交互卡片)/ Slack(Block Kit)的字段约定,**与统一 `approvals.action_summary` 一一对应**:

| 卡片区块 | 内容(取自 `approvals.action_summary`) |
|----------|----------------------------------------|
| 标题 | "Mesh 审批请求 · <动作摘要>" + 来源 agent 名 |
| 动作 | `action`(将执行的操作描述) |
| 所需权限 | `capability` + `permission`(read_only/write/confirm_required) |
| 影响范围 | `impact_scope`(影响的 issue/项目/外部系统) |
| 预估成本 | `estimated_cost`(token/运行次数/外部调用) |
| 过期时间 | `expires_at`(本地化渲染,i18n.md) |
| 续跑提示 | "批准后将由 agent 从审批点恢复:已完成 N 步,待执行 <工具调用摘要>" |
| 操作按钮 | [批准] [拒绝] —— 回调 `card.action.value = {approval_id, decision}`,经卡片回调端点转发统一审批端点 |

> 卡片是 `approvals` 的**呈现与回调面**,不持有审批状态;批准/拒绝/过期/幂等一切语义以 README §6.10 为准。卡片回调失败不改变审批状态(审批真源在站内);站内"待我审批"收件箱始终可决(README §6.13:站内为通知真源,IM 为增强)。

### 4.5 异常态(对齐 README §6.12 核心页面异常态矩阵)

集成管理页/订阅页/事件台账须实现 loading(skeleton)/ empty(空态 + "连接第一个集成"主操作)/ permission denied(非 admin 只读)/ offline / retry;集成 `disabled` 显示明确"已停用,入站事件将被拒绝"横幅;出向订阅熔断显示"已连续失败 N 次已停用 [恢复]";OAuth 失败显示"授权失败,请重试"并链回 [重新授权]。**钉钉 Stream 连接异常态(MES-82)**:reconnecting 显示"重连中(退避 Ns)"非错误态(自动恢复不打扰);down(超最大退避仍失败/凭据失效)显示错误横幅"长连接不可用,请检查凭据或切换 HTTP 模式 [重新连接] [编辑配置]";队列面板处理项的执行失败以红色终态徽章标注并链到执行详情。

---

## 5. 验收标准

### 5.1 入站摄取(复用 autopilot 范式)

- [ ] **签名无效绝不分发**:对四个入站端点(飞书/Slack/GitHub/GitLab)发送签名错误/缺失签名的请求 → 一律返回 **401 `invalid_signature`**,`integration_events` 落库 `signature_status='invalid'/'missing'` + `process_status='rejected'`,**不创建任何 task_executions、不路由、不出站**。
- [ ] **签名恒定时间比较 + 时间戳防重放**:各平台签名按 §3.2 方案校验;时间戳超出容差窗口(±300s)的合法签名请求按重放拒绝(401)。
- [ ] **event_id 去重**:同一 `(integration_id, external_event_id)` 重复投递 → 首次 `dispatched`,重复返回 **200 `deduped`** 且不再入队(`UNIQUE(integration_id, external_event_id)` 保证)。
- [ ] **去重防预占**:被拒(未签名)事件 `external_event_id` 用 `rejected:<hash>` 前缀;先伪造未签名请求占用某事件 ID,再发同 ID 合法签名事件 → 合法事件正常 `dispatched`(不被静默去重)。
- [ ] **不可信内容隔离(README §6.15)**:入站 IM 消息正文进入 agent 上下文时显式标记为不可信数据并结构隔离;消息中"指令"(如"请删除所有 issue")不作为行动依据;高风险动作走 `confirm_required`(§6.10)。
- [ ] **触发幂等(README §6.9)**:入站 IM 消息命中绑定 → 入队 `trigger='integration'` 执行,幂等键 `sha256(agent_id | integration_binding_id | external_event_id)`;同一外部事件重复到达只入队一次。
- [ ] **未匹配不触发**:未绑定/未匹配到 agent 的外部消息 → `integration_events` 审计留痕(`matched`/`received`),**不创建执行**(README §6.9)。
- [ ] **集成停用拒绝分发**:`integrations.status='disabled'` 时入站事件落库 `rejected` 并返回 401 `integration_disabled`。
- [ ] **飞书 challenge**:收到 `url_verification` → 校验 token 后原样回显 `{"challenge": ...}`(裸 JSON);token 错误返回 401。
- [ ] **摄取经 outbox(README §6.6/§9 T5)**:摄取匹配后同事务写 outbox(`execution.enqueue`);摄取提交后、relay 分发前杀 relay → 重启后执行仍被入队,无丢失;不存在进程内事件总线。
- [ ] **入站事件实时**:`integration.event_ingested` 经唯一写入路径(outbox→projector)推送,带频道 `seq`,台账实时刷新;断线重放无丢失无重复。

### 5.2 连接器(飞书 / Slack / VCS)

- [ ] **飞书 `tenant_access_token` 缓存刷新**:出站适配器缓存 `tenant_access_token`,过期前主动刷新;刷新失败/凭据撤销 → 出站投递记 `failed` 并告警,**刷新过程与令牌值不回显响应/日志**。
- [ ] **飞书 `im.message.receive_v1` 触发**:群里 @绑定 agent 或私聊 agent → 摄取 → 触发运行;agent 回评经出站适配器发回 IM/issue。
- [ ] **飞书/Slack 审批卡片闭环**:审批产生 → 推审批卡片(字段同 §4.4)→ 卡片点"批准/拒绝" → 回调转发 `POST /approvals/{id}/approve|reject`(README §6.10)→ 运行从审批点续跑/取消;回调记 approvals `decision_comment` + `notification_delivery(channel='im')`;**重复回点幂等**(README §6.10 重复 approve/reject no-op)。
- [ ] **IM 卡片回调点击者鉴权(HIGH-1;R4 映射模型)**:卡片回调必须从载荷提取点击者外部身份(飞书 `open_id`/Slack `user_id`,连同平台租户归一)→ 经 `external_identities`(`(provider, provider_tenant_key, external_user_key) ↔ users.id` 全局身份键,经认证的「连接外部账号」流程建立)映射到全局 `users.id` → **由接收回调的集成实例解析所属 workspace,JOIN 该 workspace 的 `members(workspace_id, user_id)` 得名册行,服务端按 README §6.10 权限行再校验**;**未映射/该用户在此工作区无名册行/无权限的 IM 用户点卡片批准 → 403 拒绝,审批状态不变,审计记录**;卡片决定路径与站内审批权限校验等价;兜底:高危动作审批的卡片可只呈现不提供按钮,强制站内「待我审批」决。
- [ ] **外部身份建链信任根(HIGH-1 补齐)**:无法将自己不控制的外部身份关联到本人(验证码仅投递至该外部账号私聊,或经 OAuth 确认服务端核对平台返回的用户身份与请求者会话);建链目标固定为请求者本人的 `users.id`(不接受指向他人用户/成员行的参数);建链不可经卡片回调/入站事件隐式创建。
- [ ] **外部身份全局唯一 + 多工作区模型(R4,HIGH-5;R5 HIGH-2 全局化,集成测试 T29)**:`UNIQUE(provider, provider_tenant_key, external_user_key)` 生效——同一外部平台账号(含平台租户维度)至多映射一个 Mesh 用户;**同一已认证外部账号可跨两个 Mesh 工作区参与卡片审批(单映射行,各工作区经各自 `members(workspace_id, user_id)` 行解析,不再被锁到单个 member_id)**;**不同外部租户的同名 user key 可并存(身份键含 `provider_tenant_key`,不误撞)**;同一外部账号重复建链 → 409 `identity_already_linked`;**删除建链工作区 A 后全局映射仍存在(`created_in_workspace_id` 经 `ON DELETE SET NULL` 置空),工作区 B 的卡片回调经名册 JOIN 仍可解析(T29 跨工作区删除负向场景)**。
- [ ] **全局身份表结构与 RLS/权限负向测试(R5,HIGH-2,集成测试 T29)**:`external_identities` **不含 `workspace_id` 列、无任何对工作区的 `ON DELETE CASCADE` 外键**(information_schema 结构断言;映射生命周期不受任何工作区删除控制);本表为全局身份表(与 `users` 同级),**不适用 workspace RLS、不存在 `mesh.workspace_id` 策略**(pg_policies 负向断言);**解链授权仅比对所属 `users.id`(`external_identity_unlink_allowed` 可执行参照):映射所属用户经任一工作区成员行解链放行,其他用户(含工作区 admin/owner 角色)一律拒绝、无 admin 旁路**;管理员对他人外部身份的可及手段仅为撤销本工作区使用权/成员资格(member.md),全局映射不受影响。
- [ ] **建链/解链审计与即时生效**:建链/解链操作均写审计日志(建链记 `created_in_workspace_id` 来源);解链后该外部身份的卡片点击**立即**恢复为「未映射 → 403」(无缓存延迟),且对其余工作区同样立即生效(全局映射单行);**用户注销(`users` 行 `ON DELETE CASCADE`)后映射级联删除**,效果同解链(映射生命周期的唯一级联来源)。
- [ ] **Slack 同构**:Events API 事件回调(`message.channels` 等)经 `X-Slack-Signature` 校验后触发;Block Kit 卡片推送/回调与飞书语义对齐(同一抽象,不同适配点)。
- [ ] **VCS 关联(真源 `vcs_links`,R3)**:commit/PR/branch ↔ issue 经 `POST /integrations/vcs/links` 显式关联,或经 identifier(`WEB-123`)自动解析关联(`UNIQUE(workspace_id, identifier)`),**一律落 `vcs_links` 表**(§2.8:同租户复合 FK `(workspace_id, integration_id) → integrations(workspace_id, id)`、外部对象部分唯一键 `uq_vcs_links_external_object`、状态索引);`GET /issues/{id}/vcs-links` 返回该 issue 的 active 关联列表;同一外部 PR 重复关联幂等(部分唯一索引命中跳过),外部对象已存在 active 关联时异工作区/异 issue 抢关被拒 409。
- [ ] **VCS 自动状态流转**:PR merge/close 事件入站并关联 issue 后,按 `auto_status_map` 经 issue.md 状态流转置目标状态(校验目标状态存在 + 迁移合法)+ 刷新 `vcs_links.external_state`/`status='stale'` + 发评论留痕;**重复事件幂等不重复改状态**。
- [ ] **边界:runtime git ≠ VCS 集成**:agent 运行时经 runtime 协议 checkout/push(runtime.md)是执行工具,与本模块 VCS 连接器(产品级事件摄取 + issue 联动)互不替代;Spec/代码不混淆二者。

### 5.3 出向 Webhook 订阅

- [ ] **HMAC 签名**:出向投递携带 `Mesh-Signature: t=<ts>,v1=HMAC_SHA256(secret,"<ts>.<body>")` + `Mesh-Event` + `Mesh-Delivery` 头;接收方可以用密钥重算验证;密钥创建后仅显示一次,响应/日志不回显。
- [ ] **重试退避**:非 2xx/超时 → `attempts+1` + 指数退避(`min(base×2^n,max)×jitter`)写 `next_retry_at`;超 `retry_max_attempts` 置 `failed`。
- [ ] **订阅级熔断**:连续失败超 `circuit_break_threshold` → 订阅 `status='disabled'`(熔断)+ 告警;`POST .../resume` 恢复并 `fail_count` 清零;熔断期间投递返回 422 `subscription_circuit_open`。
- [ ] **投递幂等(README §6.5)**:`UNIQUE(subscription_id, event_ref)` 保证同一订阅对同一源事件至多一条台账;outbox 重复出队不产生重复投递。
- [ ] **经 outbox(README §6.6)**:出向投递由 outbox `webhook.dispatch` 消费驱动,业务事务不直接 POST 外部 URL。
- [ ] **https-only + SSRF(README §6.16)**:创建订阅 URL 非 `https` → 400 `invalid_url_scheme`;目标解析到私网地址段(RFC1918/link-local/`169.254.169.254`)→ 422 `ssrf_blocked`,拒绝投递。

### 5.4 凭据与多租户

- [ ] **凭据脱敏(README §6.16)**:集成凭据(app secret/bot token/OAuth refresh token)只存加密密文(`secret_ref`,同 `runtime_credentials.encrypted_value` 契约);`GET` 集成/订阅响应与日志**永不回显明文**;`config` JSONB 经扫描确认不含明文 secret;凭据轮换后旧密文失效。
- [ ] **跨租户复合 FK 拒绝(README §6.2/§9 T1 同类)**:`integrations`/`integration_bindings`/`integration_events`/`webhook_subscriptions` 均建 `UNIQUE(workspace_id, id)`;`integration_id`→`integrations(workspace_id,id)`、`project_id`→`projects(workspace_id,id)`、`bound_agent_id`→`agents(workspace_id,id)`、`created_by`→`members(workspace_id,id)`、`subscription_id`→`webhook_subscriptions(workspace_id,id)` 均为复合 FK;**构造跨 workspace 复合 FK 插入被数据库约束拒绝**;A 区凭证访问 B 区集成/绑定/订阅/事件 → 403/404。
- [ ] **外部身份跨 workspace 唯一绑定(R3,合并 MES-4 HIGH 加固)**:`UNIQUE(provider, provider_tenant_key, external_ref)` **全局键**(规范化 平台 + 外部租户 + 外部对象 三维度)下,两个工作区各自的集成实例抢绑同一外部群/频道/仓库 → 第二者 **409 `binding_conflict`**(INSERT 即被数据库拒绝,防同一外部群/仓库跨集成/跨工作区重复绑定导致入站路由歧义);同工作区同集成重复绑定同样被拒;**入站事件匹配到多个绑定时仅审计 + 告警,不触发运行**(集成测试 T29)。
- [ ] **scope 精确异或与删除策略(R3)**:`scope='workspace'` 携带 `project_id` 的绑定创建被 CHECK 拒绝(422);`scope='project'` 缺 `project_id` 被拒;**物理删除项目时其项目级绑定经 `ON DELETE CASCADE` 一并删除**——不产生 `project_id` 被置空而违反 CHECK 的不可达状态,项目删除不因绑定存在而失败(集成测试 T29)。
- [ ] **真实 DELETE 行为(README §6.2 第 6 条/§9 T18 同类)**:删除 agent 时 `integration_bindings.bound_agent_id` 经列级 `ON DELETE SET NULL (bound_agent_id)` 仅置空引用列、`workspace_id` 保持非空;硬删集成级联其 bindings/events/**vcs_links**;软删除集成后绑定/事件保留;**删除建链工作区时 `external_identities.created_in_workspace_id` 经列级 `ON DELETE SET NULL (created_in_workspace_id)` 仅置空审计列、映射行保留(全局表,不受工作区删除级联,R5/T29)**。

### 5.5 实时与可观测

- [ ] `integration.updated`(集成/绑定/订阅变更、熔断)与 `integration.event_ingested`(入站落库)均取自 README §6.7 注册表「平台能力」域,无未登记事件名;经唯一写入路径推送,带频道 `seq`,断线凭 `resume_from` 重放无丢失无重复(README §6.7/§9 T26)。
- [ ] 事件台账可查询某条入站事件的完整生命周期(签名状态/处理状态/载荷),直接定位"未触发"原因(`rejected`/`deduped`/未匹配);出向投递台账可查每次尝试的 attempts/response_status/退避。

### 5.6 钉钉连接器与交互能力(MES-82:接入 / emoji 确认 / /stop /btw / 自动排队)

**接入与鉴权(§3.2 双接收模式)**:
- [ ] **HTTP 回调签名校验**:构造 `timestamp` + `sign = Base64(HMAC_SHA256(app_secret, timestamp + "\n" + app_secret))` 合法签名的钉钉回调 → 200 摄取;篡改 body / 错误 secret / 缺 `sign` 头 → **401 `invalid_signature`**,`integration_events` 落 `rejected`,`signature_status='invalid'/'missing'`,**不派发、不 ack**;恒定时间比较以**实现断言验收**(使用 `hmac.compare_digest`/等价恒定时间原语 + 代码审查),不做 CI 时序测量。
- [ ] **时间戳防重放(钉钉官方容差 ±3600s)**:签名合法但 `timestamp` 超出当前 ±3600s → 401 拒绝(重放防护);边界内(如 59 分钟前)放行。
- [ ] **Stream 长连接摄取(经可控测试注入门)**:Stream worker 的网关基址经 `MESH_DINGTALK_GATEWAY_BASE` 配置(生产默认钉钉官方域;e2e 指向本地钉钉网关测试替身——实现 `connections/open` + WSS 帧协议的最小 fake,可注入帧/切断连接/模拟重推);`receive_mode='stream'` 集成 → worker 经 `connections/open`(app_key/app_secret)建连并接收 `/v1.0/im/bot/messages/get` 帧 → 与 HTTP 模式同一摄取服务函数落库(`payload._mesh_channel='stream'`,`signature_status='valid'`);每帧回 ACK;**未 ACK 帧重推 → `msgId` 去重幂等(200 `deduped`,不重复入队/不重复 ack)**;仅 `wss://` endpoint 被接受(非 wss 拒连 + 告警),TLS 证书强制校验。
- [ ] **Stream 凭据错误即全拒**:app_secret 错误的集成 → `connections/open` 失败,不建连、零摄取(等价"签名一律无效");集成概览连接状态 `down` + 告警。
- [ ] **断线重连**:kill Stream 连接 → 指数退避(2s→300s,±20% 抖动)重连成功;重连期间钉钉侧暂存的未 ACK 消息重推后正常摄取,**无丢失**;重连过程经 `integration.updated(subject='stream_channel')` 实时反映(connected→reconnecting→connected)。
- [ ] **单实例互斥**:两个 worker 进程同时启动 → 同一集成仅一个建立 Stream 连接(advisory lock);集成测试模拟互斥失效双摄取 → 去重键兜底,无重复执行。
- [ ] **凭据轮换即时生效**:`rotate-secret` 后 Stream 断连并以新 app_secret 重连成功;旧凭据立即不可用;轮换过程与令牌值不回显响应/日志(README §6.16)。

**规范化与绑定**:
- [ ] **三元组归一**:入站载荷 `chatbotCorpId` → `provider_tenant_key=corp_id`、`conversationId` → `external_ref`、`senderStaffId` → 发起人键(无 staffId 的外部联系人归一 `ext:<senderId>`);`conversationType` `"1"`/`"2"` 归一单聊/群聊并决定出站通道(oToMessages/groupMessages)。
- [ ] **全局唯一绑定(R3 键含钉钉)**:`UNIQUE(provider='dingtalk', provider_tenant_key, external_ref)` 下,两个工作区抢绑同一钉钉群 → 第二者 409 `binding_conflict`;同群单聊会话同口径占位。

**emoji 确认接收(§3.8)**:
- [ ] **即时确认**:绑定会话内 @机器人发任务消息 → 摄取成功后**先收到 `✅ 已接收,处理中` 确认消息,后才收到执行结果**(确认发送先于 agent 任何出站动作;集成测试断言 ack 时间戳 < 首个结果消息时间戳)。
- [ ] **仅 dispatched 触发**:重复事件(deduped)/ 未绑定会话 / 命令消息 / 签名被拒消息 → **不发确认消息**(出站台账无对应行)。
- [ ] **at-most-once**:同一队列项制造出站失败重试场景 → 至多一条确认消息(`ack_sent_at` 前置条件 + 台账断言);摄取后、ack 前杀进程 → 任务照常执行、不补发 ack(不阻塞)。
- [ ] **串行排队位置提示**:串行模式下连发 3 条 → 每条都即时收到确认,第 2/3 条确认文案含排队位置("第 2 位"/"第 3 位");`ack_template` 置空的集成不发确认。
- [ ] **出站失败不阻塞**:出站适配器发送 ack 失败(模拟平台 5xx)→ 重试 1 次后放弃 + 审计告警,执行正常进行,摄取响应不受影响(3s 上限)。

**`/stop` 与 `/btw`(§3.7)**:
- [ ] **`/stop` 取消在途执行**:会话内派任务 → 执行 running 时发 `/stop` → 执行被取消(`failure_reason='cancelled_by_command'`),队列项 `cancelled`,机器人回"已停止任务…";重复 `/stop`(执行已终态)→ 幂等回"当前没有进行中的任务"。
- [ ] **`/stop` 连同排队项取消**:串行模式下发起人排队了 2 条 pending → `/stop` 后在途执行取消 + 2 条 pending 一并 `cancelled`(按 seq 序),机器人反馈含取消条数。
- [ ] **`/stop` 授权负向**:用户 B(已映射身份、无 manage 权限)对用户 A 的在途任务发 `/stop` → 拒绝(回 command_forbidden 语义文本)+ 审计,**A 的执行不受影响、详情不泄露**;未映射身份发 `/stop` → 回建链提示,零副作用;有 `execution:manage` 权限成员发 `/stop` → 放行。
- [ ] **`/btw` 注入在途执行**:执行 running 时发 `/btw 用 staging 环境` → 机器人回"已补充…",执行上下文中出现该补充(结构化隔离标记 `source='im_btw'`,**作为数据而非指令**:执行不因补充文本中的"指令性措辞"改变高危行为,README §6.15);执行不打断、不新建。
- [ ] **`/btw` 无在途降级**:会话无 processing 项时发 `/btw 查下日志` → 回提示"…已按新消息排队" + 剥前缀文本按普通消息入队执行。
- [ ] **命令不入队/不触发**:`/stop`/`/btw`/`/help`/未知 `/xxx` 消息 → `integration_message_queue` 无对应行;未知命令回帮助文本;消息正文中间的 "/stop" 不解析为命令(按普通任务消息处理)。
- [ ] **钉钉 @前缀归一**:钉钉群消息 `text.content` 含前导空格与 @机器人 前缀 → trim 后正确解析命令(不出现" /stop"识别失败)。

**自动排队(§3.9 / §2.10)**:
- [ ] **串行按序**:串行集成会话内快速连发 M1/M2/M3 → 队列 seq=1/2/3;M1 执行期间 M2/M3 保持 pending;**执行顺序严格 M1→M2→M3**(断言 started_at 序与执行创建序一致),无并发(任意时刻该会话 processing 项 ≤ 1)。
- [ ] **数据库级并发保证**:`uq_imq_conversation_processing` 部分唯一索引生效——并发派发器争抢同会话 → 至多一个成功,其余唯一约束冲突回退(information_schema/pg_indexes 结构断言 + 并发注入测试)。
- [ ] **不丢失(崩溃恢复)**:M1 processing 时杀派发器/进程 → 重启后租约修复:M1 执行已终态则补回写 done/failed;执行丢失则经幂等键重新派发;**M2/M3 继续按序执行**,队列最终无悬挂 pending(超租约阈值后断言)。
- [ ] **位置查询**:`GET .../integrations/{id}/queue` 返回各会话项与 `position`(M3 在 M1 处理、M2 排队时 position=2);`:cancel` 取消 M2(本人)→ M3 position 变 1;非 pending 项取消 → 422 `queue_item_not_cancellable`;他人 pending 项由无 manage 权限者取消 → 403。
- [ ] **parallel 模式基线**:飞书/Slack 默认 `parallel` → 连发消息各自即时派发(不等前序终态),§6.9 原触发语义不变;集成级切 `serial_conversation` 后新消息按串行处理,切换不影响已在途项。
- [ ] **实时**:入队/派发/终态/取消均推 `integration.queue_updated`(README §6.7 注册表已登记),带 `position`,面板实时刷新;断线重放无丢失无重复。

**出站与推送(§3.10)**:
- [ ] **accessToken 缓存刷新**:出站适配器缓存 accessToken(7200s),过期前 5 分钟主动刷新 + single-flight(并发出站仅一次刷新请求);刷新失败 → 出站记 `failed` + 告警;**accessToken/appSecret 不回显任何响应与日志**(脱敏断言)。
- [ ] **主动推送进度/结果**:执行产生进度/结果通知 → 经 `notification_delivery(channel='im', provider='dingtalk')` 投递到源会话(群走 groupMessages、单聊走 oToMessages);平台限流(429)→ 退避重试;台账可查。
- [ ] **互动卡片回调鉴权(同 §5.2 卡片链)**:钉钉互动卡片按钮回调 → 点击者 `userId` + corp_id → `external_identities` → `users.id` → 集成解析 workspace → JOIN members → §6.10 权限校验;未映射/无名册行点击批准 → 403,审批状态不变,留痕;已映射有权限者点击 → 转发 approve/reject,重复点击幂等。
- [ ] **真实钉钉联调(验收阶段)**:真实企业内部应用机器人(测试企业)端到端 —— Stream 建连、群内 @触发 + ✅ 确认、/stop、/btw、排队、主动推送全部真实验证(非 mock);联调存证(截图/日志)附验收评论。

**平台硬化与横切(评审收口)**:
- [ ] **入站频率护栏(§2.10)**:单身份超 20 条/分钟(`MESH_IM_INBOUND_PER_IDENTITY_PER_MIN`)或单会话超 60 条/分钟或会话 pending 深度超 50 → 超限消息**不入队、不创建执行、不 ack**,落 `rejected` + `_mesh_reject_reason='rate_limited'` + 一次性机器人提示(同会话提示 ≤1 次/分钟)+ 告警;HTTP 模式超限仍返回 200(不触发平台重推)。
- [ ] **身份三元组授权负向(跨 provider 越权)**:构造 GitHub login 与钉钉 staffId 同名(如 `foo`)映射到两个不同 Mesh 用户;GitHub 用户经 `:cancel`/`/stop` 指向同名钉钉队列项 → **拒绝(403/拒绝文本),目标项与执行不受影响**(断言 `sender_identity_key` 全三元组解析,裸键解析路径不存在)。
- [ ] **入站文本长度上限**:超 `MESH_IM_INBOUND_TEXT_MAX_CHARS`(默认 4000)的消息正文/`/btw` 参数 → 截断 + `payload.truncated=true` 审计;执行上下文中的截断标记可见。
- [ ] **ack 合并窗口**:同会话 5s(`MESH_IM_ACK_COALESCE_WINDOW`)内连发 3 条 → 仅 1 条合并确认消息(含数量与位置),3 个队列项 `ack_sent_at` 均回写。
- [ ] **执行关联回写(双模式)**:parallel 与 serial 模式下队列项 `execution_id` 均经 relay 创建执行事务同事务回写(经 `trigger_event_id` 反查),队列面板"处理中项 → 执行深链"在两种模式下均可点;回写 state 守卫幂等(串行项不被重复置位)。
- [ ] **租约修复四分支**:制造 ① 终态事件丢失(补回写)② 长任务超租约仍在跑(续租不误杀)③ 执行不存在(重派发)④ queued 卡死超 `max_stuck_seconds`(置 failed + 告警不重派)四种孤儿场景 → 各分支行为如 §3.9,扫描不空转,无消息静默丢失。
- [ ] **模式切换不追溯存量**:serial 下积压 2 条 pending → 集成切 `parallel` → 存量 2 条仍按 serial 由派发器依序清空(项 `dispatch_mode` 快照 = 入队时配置),切换后新消息按 parallel 即时派发。
- [ ] **`/btw` 运行期注入(下一 turn 生效)**:执行 running 时 `/btw` → `execution_context_appends` 落行(source='im_btw');该执行下一 agent turn 上下文中出现结构化隔离的补充块;**补充文本中的指令性措辞(如"请删除所有 issue")不改变执行的高危行为**(README §6.15 断言);心跳 `context_appends_seq` 推进驱动 daemon 拉取。
- [ ] **`/stop` 失败不撕裂**:模拟 runtime 取消端点不可达 → `/stop` 回"取消请求失败",队列项**保持原态**(无"项已取消、执行仍跑"撕裂);恢复后重试 `/stop` 成功。
- [ ] **外部联系人单聊降级**:无 staffId 的外部联系人(`ext:<senderId>`)单聊触发 → 执行正常创建运行,ack/结果推送记 `failed(reason='no_staff_id')` + 告警,不阻塞执行。
- [ ] **出站请求体脱敏**:`connections/open`/`accessToken` 出站失败(模拟 5xx)→ 日志/错误台账仅 `method/url/status`,body 中 `clientSecret`/`appSecret`/`accessToken` 均以 `***` 出现(或不出现);`redact_in_logs` 黑名单含解密后的秘钥值。
- [ ] **Stream 状态持久真源**:杀 Stream worker → `integrations.stream_state` 经 outbox 迁移至 reconnecting/down,`GET .../stream-status` 可读(前端刷新首屏不依赖实时事件);恢复后 connected + `last_frame_at` 刷新。
- [ ] **队列取消 TOCTOU**:`:cancel` 与派发器并发(项刚被派发)→ 原子条件更新 0 行命中 → 422,不产生 cancelled 与 processing 并存态。
