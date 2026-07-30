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
> 1. **emoji 确认接收(ack)**:入站任务消息摄取成功后**立即回一条轻量确认消息**(默认 `✅ 已接收,处理中`)再异步执行。钉钉**无官方公开支持**的消息级 emoji 回应(reaction)API(官方 SDK 面的 `emotion/reply` 接口无公开文档、不稳定,不予采用,§3.8),故"emoji 确认"以确认消息实现等价语义(见 §3.8);飞书/Slack 连接器同此语义(平台统一,不因平台有 reaction API 而分叉)。
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
- **不依赖平台级 emoji 回应(reaction)API**:钉钉**无官方公开支持**的消息级 reaction 能力——官方 SDK(robot_1.0)虽存在 `emotion/reply`/`emotion/recall` 接口,但**无任何公开文档页**(未公开支持、无 SLA、随时可变),**不予采用**;emoji 确认接收以**轻量确认消息**实现等价语义(§3.8),且三平台语义统一(飞书/Slack 即便有 reaction API 也不分叉实现,避免连接器行为漂移)。
- **钉钉媒体与状态类 API 本期非目标(YAGNI)**:不做入站媒体下载(`messageFiles/download`,非文本消息仅审计原载荷,不取媒体内容,§3.2 消息类型矩阵)、不做机器人消息撤回(`groupMessages/recall`/`otoMessages/batchRecall`,平台支持 24h 内撤回)、不做消息已读状态查询(`groupMessages/query`/`oToMessages/readStatus` 及已读/撤回事件订阅)、不做机器人菜单/快捷入口(文本命令已足够,实现期可作 `/help` 的 UX 增强)。**平台无"机器人被移出群"事件**(钉钉事件总览「机器人」类仅已读/撤回两项),移出感知经出站失败(`upstream_error`)+ 告警路径(§3.5)。
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
| `health_state` | TEXT | NOT NULL,CHECK IN ('unknown','healthy','auth_failed','unreachable') | `'unknown'` | **连接器健康度**(独立于手动 `active`/`disabled` 的 `status`;由 `:test`(§3.1)与凭据刷新失败驱动;§4.1 健康徽章与「重新授权」联动) |
| `last_error` | TEXT | NULL | NULL | 最近一次连接器健康检查 / 凭据刷新错误摘要(不泄漏内部细节) |
| `last_success_at` | TIMESTAMPTZ | NULL | NULL | 最近一次连接器健康检查成功时刻 |
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
| `external_user_key` | TEXT | NOT NULL | — | 规范化外部用户标识(飞书 `open_id`、Slack `user_id`、**钉钉 `senderStaffId`**(企业内部成员;无 staffId 的外部联系人编码为 `x=<base64url(senderId)>`——`senderId` 为含冒号的加密串,须经 base64url 无冒号编码,§3.10)**、GitHub/GitLab 用户 login/id) |
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
| `event_type` | TEXT | NOT NULL | `''` | **派发时捕获的真实事件类型**(如 `issue.updated`)——出向投递 `Mesh-Event` 头的真值(§3.4),**绝非**不透明的 outbox 事件 UUID |
| `payload` | JSONB | NOT NULL | `'{}'` | **派发时捕获的事件载荷**——body 携带 `event`+`data`(P8:订阅方可从单个投递还原完整域事件,§3.4) |
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
  "verbosity": "final_only",                     // IM 侧推送详略:final_only(默认,仅确认/审批卡片/最终结果) | progress(追加进度通知);中间过程站内永远可见(README §6.13 站内为真源)
  "stream_reconnect": { "base_seconds": 2, "max_seconds": 300, "heartbeat_timeout_seconds": 90 } }
// kind='vcs_github'
{ "installation_id": "1234567", "webhook_secret_ref": "<密文引用>",
  "api_base": "https://api.github.com" }
// kind='vcs_gitlab'
{ "instance_url": "https://gitlab.com", "webhook_token_ref": "<密文引用>" }
```
> **`config` 严禁存明文 secret**:一切密钥以 `*_ref` 指向加密密文(同 `runtime_credentials.encrypted_value` 契约,README §6.16)。`config` 仅存 app_id、外部租户标识、回调基址、模板等非密配置。**Stream 网关测试替身基址 `MESH_DINGTALK_GATEWAY_BASE` 亦不进 config**——仅部署期环境变量,不经任何运行期配置/管理 API 可读写(防 admin 可编辑 → 指向受信任对端 → Stream MITM 窃取内存中 app_secret 的提权路径),生产环境非默认值启动即告警 + 审计(§5.6)。

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
> - **`inbound_queue` 语义(§2.10/§3.9)**:`serial_conversation`(钉钉默认):同一会话的入站任务消息按 FIFO 串行派发,至多一个在途执行;`parallel`(飞书/Slack 默认,保持 §6.9 基线):入队即派发、不等待前序。两种模式均可在集成级切换,切换不追溯已入队项(存量 pending 项按入队时 `dispatch_mode` 快照被派发器清空,§2.10/§3.9)。
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
  health_state TEXT NOT NULL DEFAULT 'unknown'
               CHECK (health_state IN ('unknown','healthy','auth_failed','unreachable')),  -- 连接器健康度(:test / 凭据刷新驱动,独立于 status,§3.1/§4.1)
  last_error   TEXT NULL,                                                               -- 最近健康检查 / 凭据刷新错误摘要
  last_success_at TIMESTAMPTZ NULL,                                                     -- 最近健康检查成功时刻
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
  external_user_key     TEXT NOT NULL,                           -- 飞书 open_id / Slack user_id / 钉钉 senderStaffId(外部联系人 x=<base64url(senderId)>)/ VCS 用户 login
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
  event_type      TEXT NOT NULL DEFAULT '',                                            -- 派发时捕获的真实事件类型(Mesh-Event 头真值,§3.4)
  payload         JSONB NOT NULL DEFAULT '{}',                                         -- 派发时捕获的事件载荷(P8:单投递可还原域事件,§3.4)
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
  integration_id     UUID NULL,                                         -- 入队时 NOT NULL(服务层强制);父集成删除经 SET NULL 置空 → 孤儿审计行
  binding_id         UUID NULL,                                         -- 入队时 NOT NULL(未匹配消息不入队);父绑定删除经 SET NULL 置空 → 孤儿审计行
  integration_event_id UUID NULL,                                       -- 源入站事件(复合 FK 同租户,§6.2;事件台账删除经 SET NULL 保留审计)
  binding_display    TEXT NOT NULL DEFAULT '',                          -- 绑定展示快照(≤200 字符,入队时捕获):孤儿审计行自描述
  project_id_snapshot UUID NULL,                                        -- 绑定 scope='project' 时入队捕获的项目快照(多态逻辑引用,不建 FK):孤儿审计行可见性过滤
  conversation_key   TEXT NOT NULL,                                     -- 规范化会话键:provider:provider_tenant_key:external_ref(如 dingtalk:dingxxx:cidxxx)
  seq                BIGINT NOT NULL CHECK (seq > 0),                   -- 会话内单调递增(会话级事务咨询锁取号 + ON CONFLICT 重试,§2.10)
  dispatch_mode      TEXT NOT NULL CHECK (dispatch_mode IN ('serial_conversation','parallel')),
                                                                        -- 入队时的有效模式快照(含排空-再切换规则,§2.10),项生命周期内不可变
  state              TEXT NOT NULL DEFAULT 'pending'
                     CHECK (state IN ('pending','dispatching','processing','cancelling',
                                      'done','failed','cancelled')),    -- 状态机 §2.10:pending→dispatching→processing→(cancelling→)终态
  execution_id       UUID NULL,                                         -- 派发后绑定的执行(dispatching 写出、processing 起非空)
  target_agent_id    UUID NULL,                                         -- 入队时绑定的目标 agent 快照(派发以快照为准,绑定改目标不追溯)
  message_excerpt    TEXT NOT NULL DEFAULT '',                          -- 入站正文截断净化摘要(≤120 字符,去控制符/换行;队列面板展示用,全文经事件台账)
  sender_identity_key TEXT NOT NULL DEFAULT '',                         -- 规范化发起人全三元组 provider:tenant:user_key;本人取消与 /stop 授权用
  ack_leader_id      UUID NULL,                                         -- 窗口归属:leader 自指 / follower 指向 leader(摄取事务按 seq 确定,§3.8)
  ack_attempted_at   TIMESTAMPTZ NULL,                                  -- leader 外呼闸门(relay T1;与事件同事务置 published,§3.8)
  ack_sent_at        TIMESTAMPTZ NULL,                                  -- 平台已确认收到 leader 确认消息(仅外呼成功后回写)
  ack_represented_at TIMESTAMPTZ NULL,                                  -- 被抑制项:已被窗口 leader 代表(本项不外呼;绝非"已发送",§3.8)
  ack_merged_into    UUID NULL,                                         -- 被抑制项指向的 leader 队列项 id(§3.8)
  lease_expires_at   TIMESTAMPTZ NULL,                                  -- dispatching/processing/cancelling 租约(过期孤儿项由修复扫描处置,§3.9)
  ack_window_at      TIMESTAMPTZ NOT NULL DEFAULT now(),                -- 协议在持 imq_seq 锁后显式写 clock_timestamp()(锁序时间,§3.8 窗口判定真源;DEFAULT now() 仅表级兜底,窗口判定不得依赖之)
  enqueued_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at         TIMESTAMPTZ NULL,                                  -- 进入 processing 的时刻
  finished_at        TIMESTAMPTZ NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_imq_ws_id UNIQUE (workspace_id, id),                    -- 复合 FK 引用前提(§6.2)
  CONSTRAINT uq_imq_event UNIQUE (integration_id, integration_event_id),-- 与 integration_events 去重同源:重复事件不重复入队
  CONSTRAINT uq_imq_conversation_seq UNIQUE (conversation_key, seq),    -- 会话内序号唯一,位置计算真源
  -- 删除保护(§2.10):SET NULL + ck_imq_orphan_terminal —— 父删除仅置空引用(项保留为孤儿审计行),
  -- 且孤儿行必为终态(非终态项失去父引用被 CHECK 拒绝 = 未走强制终止路径的删除 fail-closed)
  CONSTRAINT ck_imq_orphan_terminal CHECK (
       (integration_id IS NOT NULL OR state IN ('done','failed','cancelled'))
   AND (binding_id     IS NOT NULL OR state IN ('done','failed','cancelled'))),
  CONSTRAINT fk_imq_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE SET NULL (integration_id),
  CONSTRAINT fk_imq_binding FOREIGN KEY (workspace_id, binding_id)
    REFERENCES integration_bindings(workspace_id, id) ON DELETE SET NULL (binding_id),
  CONSTRAINT fk_imq_event FOREIGN KEY (workspace_id, integration_event_id)
    REFERENCES integration_events(workspace_id, id) ON DELETE SET NULL (integration_event_id),
  CONSTRAINT fk_imq_execution FOREIGN KEY (workspace_id, execution_id)
    REFERENCES task_executions(workspace_id, id) ON DELETE SET NULL (execution_id),
  CONSTRAINT fk_imq_target_agent FOREIGN KEY (workspace_id, target_agent_id)
    REFERENCES agents(workspace_id, id) ON DELETE SET NULL (target_agent_id)
);
-- MES-82 硬保证:同一会话至多一个串行在途项(数据库级"不并发冲突",§2.10/§3.9)
-- 仅约束 serial 项;parallel 项不受本索引约束(§6.9 并行基线:入队即派发、同会话可并发)
CREATE UNIQUE INDEX uq_imq_conversation_active
  ON integration_message_queue(conversation_key)
  WHERE state IN ('dispatching','processing','cancelling') AND dispatch_mode = 'serial_conversation';
CREATE INDEX idx_imq_conversation_pending
  ON integration_message_queue(conversation_key, seq) WHERE state = 'pending';
CREATE INDEX idx_imq_lease ON integration_message_queue(lease_expires_at)
  WHERE state IN ('dispatching','processing','cancelling');
CREATE INDEX idx_imq_integration_state                            -- 队列列表/游标分页(§3.9)
  ON integration_message_queue(integration_id, state, enqueued_at DESC, id);
CREATE INDEX idx_imq_ws_state ON integration_message_queue(workspace_id, state);
CREATE INDEX idx_imq_binding_state ON integration_message_queue(binding_id, state);
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
| `integration_message_queue.integration_id` / `binding_id` / `integration_event_id` | 复合 FK → `integrations` / `integration_bindings` / `integration_events`(各 `(workspace_id, id)`);**前两者 `ON DELETE SET NULL` + `ck_imq_orphan_terminal` CHECK(删除保护,§2.10)、事件 `ON DELETE SET NULL (integration_event_id)`** | 本模块 | 队列项归属:**禁止绑定/集成删除物理消灭已确认接收的队列项**——父删除仅置空引用、项保留为孤儿审计行(必终态;非终态失父引用被 CHECK 拒绝 = fail-closed);删除端点须先经强制批量终止路径(§3.9 删除保护);事件台账删除仅置空引用 |
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
| `integration_id` | UUID | NULL,复合 FK → `integrations(workspace_id, id)` **`ON DELETE SET NULL (integration_id)`** | — | 所属集成(README §6.2);入队时 NOT NULL(服务层强制),**父集成删除时置空——仅终态行可被置空(`ck_imq_orphan_terminal` CHECK 兜底,见「删除保护」),置空后为孤儿审计行** |
| `binding_id` | UUID | NULL,复合 FK → `integration_bindings(workspace_id, id)` **`ON DELETE SET NULL (binding_id)`** | — | 命中的绑定;**仅匹配到绑定(且目标 agent 非空)的任务消息入队**——未匹配/仅审计消息不占队列(README §6.9:未匹配不触发);入队时 NOT NULL(服务层强制),**父绑定删除时置空(同样仅终态行,删除保护见下)** |
| `binding_display` | TEXT | NOT NULL | `''` | **绑定展示快照**(入队时捕获:"<集成名> / <外部身份展示名>"净化串,≤200 字符):父绑定/集成删除置空后,孤儿审计行仍自描述可展示(不依赖父行存活) |
| `project_id_snapshot` | UUID | NULL | NULL | **绑定作用域为 project 时入队捕获的项目 ID 快照**(多态逻辑引用,不建 FK——项目可能已物理删除):孤儿审计行的 project 可见性过滤依据(§3.9 审计端点);workspace 级绑定为 NULL |
| `integration_event_id` | UUID | NULL,复合 FK → `integration_events(workspace_id, id)` `ON DELETE SET NULL (integration_event_id)` | — | 源入站事件;`UNIQUE(integration_id, integration_event_id)` 与事件去重同源——重复外部事件不重复入队;事件台账删除仅置空引用 |
| `conversation_key` | TEXT | NOT NULL | — | **规范化会话键** `<provider>:<provider_tenant_key>:<external_ref>`(如 `dingtalk:dingxxxx:cidxxxx`);队列串行粒度。**键编码与分段校验(N-1 订正,写死)**:分隔符 `:` 为键结构保留字符,服务层插入前逐段校验——① `provider` 为登记枚举值;② `provider_tenant_key` 按平台模式(钉钉 corpId `ding[A-Za-z0-9]+`);③ `external_ref`/身份段**不得含 `:` 与控制字符**。**平台事实(官方报文样例核验)**:钉钉 `conversationId`/`msgId` 为 base64 样加密 ID(字母表 `A-Za-z0-9+/=`,如 `cid6EUvB2O8qVF2RYQtHTKEsg==`,**含 `=`、可含 `+/`,结构上不含 `:`**),`senderId` 为 `$:LWCP_v1:$…` 加密串(**含 `$`/`:`/`+`**),`senderStaffId`/`chatbotCorpId` 字符集钉死**最宽官方口径** `[A-Za-z0-9._-]`(单一事实源:官方存在「字母数字」与「字母、数字及 `-_`」两版口径,取至宽并集,`.` 亦容)——故 `external_ref` 段字符类为**不含 `:` 的超集** `^[A-Za-z0-9_.@+/=-]+$`(钉钉 cid 合法通过);**外部联系人身份段不得取 `senderId` 原值**(含冒号会复现 `("a","b:c","d")` 与 `("a","b","c:d")` 坍缩歧义),一律编码为 `x=<base64url(senderId)>`(§3.10:**编码键第 2 字符恒为 `=`,而 `=` 不在 staffId 至宽字符集 `[A-Za-z0-9._-]` 内 → 两键空间结构不相交,由字符集代数保证、不依赖文档版本**;E-1 闭合);违例 → `invalid_request` |
| `seq` | BIGINT | NOT NULL,CHECK (> 0),`UNIQUE (conversation_key, seq)` | — | 会话内入队序号。**取号协议(并发安全)**:入队事务先 `pg_advisory_xact_lock(hashtext('imq_seq:'||conversation_key))` 串行化同会话取号,**持锁后取 `ack_window_at = clock_timestamp()`(锁序时间,§3.8 窗口真源)**,再 `INSERT … seq = COALESCE((SELECT max(seq) … WHERE conversation_key=:k),0)+1`(空会话首插同样受咨询锁保护);并以 `ON CONFLICT (conversation_key, seq) DO NOTHING` + 有限次重试(≤3)作背压兜底;**禁止裸 `max+1` 无锁写入**。位置 = 本会话中 `state='pending'` 且 `seq` 较小者计数 + 1 |
| `dispatch_mode` | TEXT | NOT NULL,CHECK IN ('serial_conversation','parallel') | — | **入队时的有效模式快照,项生命周期内不可变**。有效模式 = `config.inbound_queue`,但**会话内仍有非终态 serial 项时强制为 `serial_conversation`(排空-再切换规则)**——模式切换待旧串行 lane 排空后生效,杜绝新 parallel 项越过/重叠旧 serial 项(§3.9) |
| `state` | TEXT | NOT NULL,CHECK IN ('pending','dispatching','processing','cancelling','done','failed','cancelled') | `'pending'` | **状态机(见下)**;终态 = done/failed/cancelled;**终态→终态转换一律 no-op 守卫**(取消与终态回写竞态幂等) |
| `execution_id` | UUID | NULL,复合 FK → `task_executions(workspace_id, id)` `ON DELETE SET NULL (execution_id)` | NULL | 派发时绑定的执行(runtime.md);`dispatching` 写出、经「执行关联回写」在同一事务转入 `processing` 时确认(§3.9,两种模式共用);执行记录删除仅置空引用,队列项审计保留 |
| `target_agent_id` | UUID | NULL,复合 FK → `agents(workspace_id, id)` `ON DELETE SET NULL (target_agent_id)` | NULL | **入队时对绑定 `bound_agent_id` 的快照(派发目标的不可变输入)**:串行等待期间绑定改目标**不追溯**已入队项(派发以快照为准);快照 agent 在派发前被删除/停用(列级 SET NULL)→ 派发时置项 `failed(reason='target_unavailable')` + 审计,不静默改派绑定新目标 |
| `message_excerpt` | TEXT | NOT NULL | `''` | 入站正文**截断净化摘要**(≤120 字符,去控制符/换行/零宽符);队列面板列表展示用;**全文不经本字段暴露**(全文读取经事件台账 `integration_events.payload`,成员角色 + 审计) |
| `sender_identity_key` | TEXT | NOT NULL | `''` | **规范化发起人身份全三元组** `<provider>:<provider_tenant_key>:<external_user_key>`(如 `dingtalk:dingxxxx:014728255240768602`(企业成员 staffId 直通)或 `dingtalk:dingxxxx:x=JEx3Q1B2Ml8xOiQ2R1lzbi16cmM1V1o3N3hjMnY0enN5WGZCdjFt`(外部联系人 `x=<base64url(senderId)>`);各段同 `conversation_key` 分段校验,第三段永不含 `:`)。**一切"本人"判定必须按全三元组经 `external_identities` 解析到 `users.id` 再比对,禁止仅凭裸 `external_user_key` 查询**——不同 provider/租户下同一字符串可映射不同 Mesh 用户(如 GitHub login `foo` 与钉钉 staffId `foo`),裸键解析导致跨身份越权(§3.7/§3.9 授权、§5.6 负向验收) |
| `ack_leader_id` | UUID | NULL | NULL | **窗口归属(摄取事务内按 seq 确定,§3.8)**:leader 项自指(`= 本项.id`),follower 项指向其 leader;不依赖 relay 到达顺序,follower 不写 `im.send` outbox 事件(无外部副作用) |
| `ack_attempted_at` | TIMESTAMPTZ | NULL | NULL | **窗口 leader 的外呼闸门**(relay T1 持久化且**同事务将 im.send 事件置 published**,§3.8):T1 后事件已终态化,第二 worker 无从重领(消除 lost+sent 并存歧义);T1 提交后崩溃 = ack 丢失(at-most-once,审计可辨) |
| `ack_sent_at` | TIMESTAMPTZ | NULL | NULL | **平台已确认收到 leader 确认消息**(仅外呼成功后由 T2 回写);被抑制项**永不**置本字段(其"已被代表"由 `ack_represented_at` 表达,二者语义不混用) |
| `ack_represented_at` | TIMESTAMPTZ | NULL | NULL | **被抑制项**:已被窗口 leader 的确认消息代表(本项不外呼,审计"已被代表确认",非漏发、非已发送) |
| `ack_merged_into` | UUID | NULL | NULL | 被抑制项指向的 leader 队列项 id(§3.8 leading-edge 合并) |
| `lease_expires_at` | TIMESTAMPTZ | NULL | NULL | `dispatching/processing/cancelling` 租约到期时刻(派发放量 = 执行超时上限 + 缓冲);过期孤儿项由修复扫描按执行状态分支处置(§3.9 不丢失保证) |
| `ack_window_at` | TIMESTAMPTZ | NOT NULL | 持锁后 `clock_timestamp()` | **ack 窗口判定的锁序时间真源(§3.8)**:入队事务取得 `imq_seq:` 咨询锁**之后**显式取 `clock_timestamp()`(实时墙钟,非事务开始时刻的 `now()`)并持久化;窗口 `[L.ack_window_at, L.ack_window_at + window)` 与 leader 判定共用此值——杜绝"事务先开始但后取锁"的时序倒挂(该项 `enqueued_at` 早于先取锁项却不落入其窗口的反例) |
| `enqueued_at` / `started_at` / `finished_at` | TIMESTAMPTZ | NOT NULL/NULL/NULL | `now()`/—/— | 入队/派发/终态时刻(队列时延观测;`enqueued_at` 为事务开始时刻,**不参与 ack 窗口判定**) |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**状态机(状态词汇的唯一权威)**:

```
pending ──派发(串行派发器 / 并行摄取事务乐观直派)──► dispatching ──relay 建执行同事务回写──► processing
    │                                                   │                                     │
    │ :cancel / /stop(本人 pending)                     │ 租约修复:执行不存在且 outbox 丢失    │ /stop(本人或 manage)
    ▼                                                   ▼                                     ▼
cancelled                                            failed(dispatch_lost)              cancelling ──execution.finished
                                                                                          (占串行 lane,  (status=cancelled)
processing ──execution.finished(status=completed)──► done                                  等待优雅停止)   │
           ──execution.finished(status=failed/timeout)──► failed                                        ▼
                                                                                                     cancelled
```

- `dispatching` 是「outbox `execution.enqueue` 已写、`task_executions` 行未建」的短暂态(relay 正常时亚秒级);relay 在建执行事务内将其转 `processing` 并绑定 `execution_id`(§3.9)。
- `cancelling` 由 `/stop` 从 `processing` 转入:**取消请求经 runtime 执行取消服务同事务持久化**(DB 状态 + 心跳下行,§3.7;不新增 outbox 事件类型),项**继续占用串行 lane**(等待运行中的执行优雅退出),收到 `execution.finished(status=cancelled)` 后才转 `cancelled` 并唤醒下一项——**不提前释放会话独占**(旧执行尚在停止中时下一项不得启动)。
- 合法转换以外的 UPDATE 一律被服务层 state 守卫拒绝(终态→终态 no-op;`pending→processing` 跳过 `dispatching` 非法)。

**关键约束(§3.9 语义的数据库级保证)**:
- **`UNIQUE INDEX uq_imq_conversation_active ON (conversation_key) WHERE state IN ('dispatching','processing','cancelling') AND dispatch_mode='serial_conversation'`** —— 同一会话**至多一个串行在途项**(派发全阶段:含 dispatching 与 cancelling,防止"取消中提前放行下一项");"不并发冲突"不依赖 worker 互斥,而是冲突即失败的数据库硬约束;**parallel 项不受本索引约束**(§6.9 并行基线:入队即派发、同会话可并发)。串行派发器另以服务层双检"会话内无任何非终态项(含 parallel)"保证 serial 不与残留 parallel 重叠(模式切换期的跨模式串行,索引管 serial-vs-serial、服务层管跨模式)。
- **`UNIQUE (conversation_key, seq)` + 部分索引 `idx_imq_conversation_pending (conversation_key, seq) WHERE state='pending'`** —— 会话内严格 FIFO 取首项(`ORDER BY seq LIMIT 1`)。
- **`UNIQUE (integration_id, integration_event_id)`** —— 与 `integration_events.UNIQUE(integration_id, external_event_id)` 同源的二次幂等:摄取去重失效(理论上不可达)也不产生重复队列项。
- **删除保护(成功路径闭合,写死)**:`integration_id`/`binding_id` 复合 FK 为 **`ON DELETE SET NULL`** + **`ck_imq_orphan_terminal` CHECK:`(integration_id IS NOT NULL OR state IN ('done','failed','cancelled')) AND (binding_id IS NOT NULL OR state IN ('done','failed','cancelled'))`**——**孤儿审计行必为终态,非终态项不可能失去父引用**(任何绕过服务层的直接 DELETE 父行在非终态项存在时被 CHECK 拒绝,fail-closed)。删除成功路径(§3.9 端点流程):① **强制终止**(`?force=cancel`):`pending`→`cancelled`、`dispatching/processing/cancelling`→ 经 runtime 取消服务取消并等待终态(上限 30s,超时强制 `cancelled(reason='binding_deleted')` + 告警,执行侧取消意图仍由 DB 持久化);② **DELETE 父行 → FK SET NULL 触发,全部(终态)队列项的父引用置空,删除实际完成**;③ 孤儿审计行以 `binding_display`/`project_id_snapshot`/`conversation_key`/`sender_identity_key` 自描述,经工作区级审计端点可查(§3.9),保留期 `MESH_IM_QUEUE_AUDIT_RETENTION`(默认 30 天)后由 worker 分批物理清理(仅清理 `binding_id IS NULL` 的终态孤儿行)。无 `?force` 且存在**非终态**项 → `409 binding_has_active_queue`(终态项不阻塞删除,直接随 SET NULL 转孤儿审计);**项目物理删除**(project.md 级联链)的服务流程须先对其 project 级绑定执行同一强制终止,再由绑定 CASCADE 触发 SET NULL——未先终止则 CHECK 拒绝级联(项目删除失败,fail-closed 保护,不得静默丢消息)。源事件引用 `integration_event_id` 同为 `ON DELETE SET NULL`(事件台账删除仅置空引用)。

**入站频率护栏(硬约束,与去重正交;安全防滥用)**:绑定会话内的外部成员对 Mesh 是**未认证方**,无限流入站 = 每条消息一次完整 agent 执行(付费算力)+ 出站配额消耗,构成成本放大与集成拒绝服务面。每条入站 IM 消息在**匹配入队前**过三道计数(Redis 滚动窗口,键含租户维):

| 护栏 | 键维 | 常量 | 默认 |
|------|------|------|------|
| 每身份频率 | `(provider, provider_tenant_key, external_user_key)` | `MESH_IM_INBOUND_PER_IDENTITY_PER_MIN` | 20 / 滚动分钟 |
| 每会话频率 | `conversation_key` | `MESH_IM_INBOUND_PER_CONVERSATION_PER_MIN` | 60 / 滚动分钟 |
| 每会话排队深度 | `conversation_key` 的 pending 计数 | `MESH_IM_QUEUE_MAX_PENDING_PER_CONVERSATION` | 50 |

超限处置:**不入队**,落 `integration_events`(`process_status='rejected'`、`payload._mesh_reject_reason='rate_limited'`,真实 `msgId` 占去重键防同消息重试风暴)+ 机器人回**一次性**限频提示(同会话提示自身限频 1 次/分钟,防提示反射;提示 `im.send` 载荷**自携** `conversation_type` 与单聊 `target_user_key`——被拒消息不入队、无队列项可供出站派生,单聊经 `oToMessages` 投递至发起人,群聊经 `groupMessages`,与出站通道选择单一事实源一致)+ 告警;HTTP 回调模式返回 **200**(非 2xx 会触发平台重推放大),Stream 帧正常 ACK。**入站文本长度上限**:消息正文与 `/btw` 参数统一受 `MESH_IM_INBOUND_TEXT_MAX_CHARS`(默认 4000)约束,超限截断 + `payload.truncated=true` 审计(提示注入面与 token 成本护栏,§6.15 之外的量化补充)。命令平面(§3.7)的频率约束**指向本节**(此前误引 auth.md 限流矩阵"入站回调行"——该行经本 Spec 同步补入 auth.md §3.6,作签名**前**每集成/IP 粗粒度防刷,与上述签名**后**语义级护栏分层互补)。

**与 §6.9 触发矩阵的关系(README §6.9「外部 IM 消息触发」行据此修订)**:入站 IM 消息命中绑定、**过频率护栏**后**入本表**(同摄取事务,`dispatch_mode` 快照当时有效模式——含排空-再切换规则、`target_agent_id` 快照目标、`message_excerpt` 摘要),再经 outbox 入队执行(`trigger='integration'`,幂等键 `sha256(agent_id | integration_binding_id | external_event_id)` 不变):**`serial_conversation`(钉钉默认)** 由派发器在会话无在途项时按 seq 序派发,数据库级至多一在途;**`parallel`(飞书/Slack 默认)** 摄取事务乐观直派(§6.9 原基线:入队即派发,同会话可并发);模式切换待旧串行 lane 排空后生效(§2.10 `dispatch_mode` 说明)。**终态回写的唯一驱动是内部领域事件 `execution.finished`**(runtime.md:执行终态单一扇出事件,payload `{execution_id, status, failure_reason}`,`status ∈ completed/failed/timeout/cancelled`;注意这是 outbox 内部事件,非 README §6.7 实时事件名——实时事件 `execution.completed/failed/…` 与本模块终态回写均由其派生):`completed → done`,`failed/timeout → failed`,`cancelled → cancelled`。

**摄取管线分层(与 MES-68 骨架的契约边界,写死)**:两种接收模式只替换最前一层,其后全部共享——

```
【平台鉴权适配层(按模式替换)】
  HTTP 回调适配器:timestamp+sign 校验 / 集成定位 → 规范化 verified envelope
  Stream 通道适配器:建连鉴权(等价签名)/ 帧路由 → 规范化 verified envelope(同结构)
【共享摄取核心(唯一实现,MES-68 ingest_verified_event())】
  verified envelope { provider, provider_tenant_key, external_event_id(msgId),
    event_type, external_ref(conversationId), conversation_type, sender_key,
    text(已 trim/截断), raw_payload, channel('http'|'stream') }
  → 去重 → msgtype 门(触发仅 text,非文本仅审计)
  → 命令平面(§3.7,命令不入队不触发;/btw 无在途项剥前缀按普通消息继续)
  → 绑定匹配(§6.9:未匹配/无目标 agent 仅审计)
  → 频率窗口护栏(§2.10 身份/会话 Redis 滑窗,fail-closed:Redis 故障即回滚
    摄取事务,平台重推、去重保安全,绝不静默放行)
  → 入队 integration_message_queue(imq_seq 咨询锁 → 锁内 pending 深度
    权威复检(§2.10,并发不可越限)→ ack 主从判定 §3.8)
  → ack 事件(§3.8)→ 审计 integration_events
```
> 管线次序按 MES-88 已发布实现收口(命令平面先于频率护栏;msgtype 门与锁内深度复检保留)——**Spec owner 复核通过**(Leader,2026-07-30;MES-87 rebase 接缝统一措辞与发布实现及 Spec 原意一致:命令平面先于频率护栏为 MES-88 发布次序,fail-closed 与锁内深度复检保留)。
> PR #58 现有 `process_inbound()`(HTTP 定位/验签与匹配/派发揉在一起)在 #58 rebase 到含本 Spec 的 main 时按此边界重构:拆出 HTTP 鉴权适配器 + 抽出 `ingest_verified_event(envelope)` 共享核心,Stream worker 复用同一核心(§3.2 Stream 小节「同一摄取服务函数」即指本函数)。

### 2.11 台账保留策略(入站事件 / 出向投递)

> 入站台账 `integration_events` 与出向投递台账 `webhook_subscription_deliveries` 均**存原始外部内容**(入站原始载荷、出向投递载荷,可能含 PII),不作永久保留:

- **保留窗口默认 30 天**(与 GitHub 投递日志同级),经 worker **retention loop 定期分批删除**(`created_at` 早于窗口的行,分批限量删除避免长事务 / 锁争用)。
- **`webhook_subscription_deliveries` 的 `pending` 投递绝不删**:仍在重试 / 退避周期(`state='pending'`,含 `next_retry_at` 未到)的投递行不受 retention 清理——retention 仅清理终态(`sent`/`failed`)且超窗的行,杜绝把尚待重试的投递误删导致漏发。
- **入站载荷字节上限见 §3.2**(body 1MiB 上限 + 被拒台账载荷截断 16KiB):retention 管"存多久",§3.2 管"单条多大",二者正交。

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
| DELETE | `/workspaces/{ws}/integrations/{id}` | 软删除集成(软删后入站拒分发);**硬删除须先对其绑定逐项走绑定 DELETE 的强制终止路径**(§3.9 删除保护:队列项全部终态后父引用 SET NULL,删除实际完成) | admin / `integration:manage` |
| POST | `/workspaces/{ws}/integrations/{id}/rotate-secret` | 轮换凭据(旧密文失效) | admin / `integration:manage` |
| POST | `/workspaces/{ws}/integrations/{id}:test` | **测试连接**:轻量平台 API 只读往返校验凭据/连通性(飞书 `tenant_access_token` 换取 / Slack `auth.test` / 钉钉 `gettoken` / GitHub `GET /user` / GitLab `GET /api/v4/user`;`webhook_outbound` 无凭据恒 `healthy`),返回 `{data:{health_state, detail}}`,`health_state ∈ unknown/healthy/auth_failed/unreachable`;**结果同事务驱动连接器健康字段**(`integrations.health_state`/`last_error`/`last_success_at`,§2.2) | admin / `integration:manage`(写限流) |
| GET | `/workspaces/{ws}/integrations/{id}/bindings` | 该集成的绑定列表 | 成员 |
| POST | `/workspaces/{ws}/integrations/{id}/bindings` | 创建绑定(外部身份 + 作用域 + 匹配规则 + 目标 agent) | admin / `integration:manage` |
| PATCH | `/workspaces/{ws}/integration-bindings/{id}` | 更新绑定(匹配规则/目标 agent/状态) | admin / `integration:manage` |
| DELETE | `/workspaces/{ws}/integration-bindings/{id}` | 删除绑定(物理)。**删除保护(§3.9)**:存在非终态队列项时,无 `?force=cancel` → `409 binding_has_active_queue`;`?force=cancel` → 强制终止全部项(pending→cancelled;在途项经 runtime 取消服务终止,30s 上限)后删除成功,队列项转孤儿审计行(`binding_id` SET NULL,保留 `binding_display` 等快照,经审计端点可查,保留期后清理) | admin / `integration:manage` |
| GET | `/workspaces/{ws}/integrations/{id}/events` | 入站事件台账(签名/处理状态过滤,排障用) | 成员 |
| GET | `/workspaces/{ws}/webhook-subscriptions` | 出向订阅列表 | 成员 |
| POST | `/workspaces/{ws}/webhook-subscriptions` | 创建订阅(https URL + 事件过滤;签名密钥创建后仅显示一次) | admin / `integration:manage` |
| GET | `/workspaces/{ws}/webhook-subscriptions/{id}` | 订阅详情(密钥不回显) | 成员 |
| PATCH | `/workspaces/{ws}/webhook-subscriptions/{id}` | 更新订阅(URL/事件过滤/状态) | admin / `integration:manage` |
| DELETE | `/workspaces/{ws}/webhook-subscriptions/{id}` | 删除订阅 | admin / `integration:manage` |
| POST | `/workspaces/{ws}/webhook-subscriptions/{id}/resume` | 恢复熔断/暂停的订阅(`fail_count` 清零) | admin / `integration:manage` |
| POST | `/workspaces/{ws}/webhook-subscriptions/{id}:send-test` | **发送测试事件**:合成 `webhook.test` 事件走**完整签名 + 投递 + 台账**路径(经 outbox 投递 worker,与真实事件同一管线);成功返回 **201** 并落 delivery 台账行;**熔断期** → `422 subscription_circuit_open`;订阅**非 `active`**(paused/disabled)→ 422 拒绝 | admin / `integration:manage` |
| GET | `/workspaces/{ws}/webhook-subscriptions/{id}/deliveries` | 投递台账(状态过滤,重试历史) | 成员 |
| POST | `/workspaces/{ws}/webhook-subscriptions/{id}/deliveries/{delivery_id}/retry` | 手动重试某条失败投递 | admin / `integration:manage` |

**外部身份连接(建链/解链,HIGH-1 信任根;R5 全局所有权模型)**:

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/workspaces/{ws}/external-identities` | 列出**当前成员所属全局身份**已连接的外部身份(`external_identities.user_id` = 请求者经本工作区成员行解析的 `users.id`;全局表按所属用户过滤,非按工作区过滤,R5) | 成员(仅本人所属身份的映射) |
| POST | `/workspaces/{ws}/external-identities:link` | **建链**:将请求者本人的外部平台账号关联到**请求者本人的全局登录身份 `users.id`**(经其本工作区成员行的 `user_id` 解析,R4;**建链目标固定为请求者本人,不接受指向他人用户/成员行的参数**);请求体 `{provider, integration_id, external_user_key}`(`provider_tenant_key` 由 integration 实例归一,不由请求体提供;**`external_user_key`(text,必填)= 验证码将投递到的具体外部账号(请求者本人的外部账号标识,钉钉 `senderStaffId` 等,与 `external_identities.external_user_key` 同口径,即此前所称 `external_account_ref`)——验证码必须送达声明者所称的账号,故请求体必须指名该账号**,钉钉单聊经 `oToMessages` 需 staffId;OAuth 模式下仍必填以指名目标,最终映射身份以 OAuth 返回的平台用户身份为准,服务端核对其与请求者会话);服务端经集成出站适配器**向该外部账号私聊下发一次性验证码**(或走外部平台 OAuth 确认,服务端核对 OAuth 返回的平台用户身份与请求者会话),验证码 TTL 10 分钟 + 单次消费(**实现期项 L1**:验证码签发叠加每成员 + 每目标 `external_account_ref` 频率限制,对齐登录类失败计数范式,防对已连企业内任意 staffId 发码骚扰/枚举);校验通过方写入 `external_identities` 行(映射为全局行,**`created_in_workspace_id` = `{ws}` 仅作建链来源审计,R5**) | 成员(仅本人) |
| POST | `/workspaces/{ws}/external-identities:link-confirm` | **建链确认**:提交验证码 `{provider, integration_id, code}`;服务端校验验证码(匹配 + 未过期 + 未消费)→ 写入映射(`user_id` = 请求者全局身份,`created_in_workspace_id` = `{ws}` 审计);`UNIQUE(provider, provider_tenant_key, external_user_key)` 拒绝同一外部账号重复映射(409 `identity_already_linked`,R4 全局身份键) | 成员(仅本人) |
| DELETE | `/workspaces/{ws}/external-identities/{id}` | **全局解链(R5:仅所属用户本人,无 admin 旁路)**:删除该全局映射;**仅当请求者经 `{ws}` 成员行解析的 `users.id` 等于映射的 `user_id`(映射所属用户本人)时放行**,否则 `403 identity_unlink_forbidden`——**工作区 admin/owner 不得解链他人的全局身份**(管理员只能经 member.md 撤销该用户在本工作区的使用权/成员资格,其卡片回调随之在本工作区回落 403,全局映射不动);解链后该外部身份的卡片点击在**所有工作区**立即恢复为「未映射 → 403」 | 成员(仅映射所属 `users.id` 本人) |

**OAuth 授权(IM/VCS 连接器)**:

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspaces/{ws}/integrations/oauth/{kind}/authorize` | 发起 OAuth 授权码 + PKCE(302 跳外部平台授权页,`state` 防 CSRF) |
| GET | `/integrations/oauth/{kind}/callback` | 授权回调:校验 `state`(**携带发起授权时传入的可选 `name`**)+ 换取 token,**回调成功后即创建集成行**(不再由管理员在授权往返后另行创建);refresh token(**无 refresh token 的提供商如 GitHub 则为 access token**)加密落 `secret_ref`(**密文-only**,响应/日志不回显,§6.16),最小 scope;成功重定向 `/integrations?oauth=success&id=<integration_id>`,失败重定向 `/integrations?oauth=error` |

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

> **未认证端点 DoS 硬化(硬约束,写死)**:入站回调端点对 Mesh 是**未认证面**(平台签名校验在请求处理之内),故在签名校验**之前**先过资源护栏——无凭据攻击者既不能烧 CPU 也不能灌库:
> - **per-IP 滑动窗口限流**:六个入站回调端点**共享一份** per-IP 滑窗预算(键含来源 IP,Redis 滚动窗口),超限 → **429 `rate_limited`**(签名前粗粒度防刷,与 §2.10 签名后语义级频率护栏分层互补);**钉钉回调端点例外(以 auth.md §3.6 行为权威)**:其签名前护栏键维为 **(集成,IP)** 120/min、超限对平台侧**静默 200**(非 2xx 会触发钉钉重推放大),429 适用其余五类入站端点;
> - **body 1MiB 上限**:请求体超 **1MiB** → **413**(`Content-Length` 预检 + 实读字节数复检双道,防 `Content-Length` 谎报绕过);
> - **被拒台账载荷截断**:被拒事件落 `integration_events` 取证时,`payload` **截断至 16KiB 上限**(留存取证前缀 + 记原始字节数),防攻击者以超大被拒载荷灌爆台账存储。

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
  → 帧处理协议(帧结构 { specVersion:'1.0', type, headers{topic,…}, payload, time }):
     · type='SYSTEM' 系统帧:topic='ping' → 【必须回 ACK】{ code:200, headers:原样回传,
       message:'OK', data:回原 payload }(官方 SDK 同构 KeepAlive;不回 ping → 平台判定连接
       不健康主动断连);topic='disconnect' → 平台要求下线,主动关闭连接并立即重走 connections/open
     · type='CALLBACK' topic='/v1.0/im/bot/messages/get'(载荷同 HTTP 回调 body:msgId/
       conversationId/conversationType/senderStaffId/text.content/sessionWebhook…)
       → 【同事务】走统一摄取管线(§3.2 统一摄取流程,与 HTTP 端点共用同一服务函数)
       → 摄取事务提交后回帧 ACK { code:200, headers:原帧 headers, message:'OK', data:'received' }
       —— ACK 必须返回,否则钉钉按未确认重推(重推经 msgId 去重幂等,§3.2)
     · type='CALLBACK' topic='/v1.0/card/instances/callback' → 卡片回调链(§3.10)→ 回 ACK
     · Mesh 侧心跳探活(heartbeat_timeout_seconds,默认 90s 无任何帧)与平台 ping 机制**并列**:
       任一失活即触发重连
  → 连接断开/心跳超时(config.stream_reconnect.heartbeat_timeout_seconds,默认 90s 无帧)
     → 指数退避重连(base 2s,max 300s,±20% 抖动,config.stream_reconnect)→ 重走 connections/open
  → 集成 disabled/删除 → 关闭该集成长连接;凭据轮换 → 断连并以新密文重连
```

> **Stream 通道的签名等价性(签名校验适配点)**:Stream 帧**没有**逐帧签名头,其真确性由 `connections/open` 的 `app_key`/`app_secret` 鉴权在**通道层**一次性确立(密文错/凭据撤销 → 连接建不起来,等价于"签名一律无效");已建立通道内的帧以 `signature_status='valid'` 落库,`payload._mesh_channel='stream'` 标注来源信道。HTTP 模式则是逐请求签名校验(`signature_status` 按校验结果)。两种模式的下游(去重/审计/匹配/排队/派发)**完全一致**——这是"签名校验适配点"的两种实现形态,不是两套摄取机制。**通道层信任不豁免任何下游授权**:帧仍经去重/绑定匹配/命令平面三元组鉴权/频率护栏全链约束。
>
> **传输硬化(硬约束)**:仅接受 `wss://` 网关 endpoint(非 wss 即拒连 + 告警,防降级);强制校验网关 TLS 证书(禁 `verify=False`);`ticket` 是钉钉协议要求的短期一次性建连凭据(置于 WS URL query 为平台协议强制,构成 README §6.16「禁 URL query 传 token」的**显式命名例外**:该约束针对 Mesh 自有 `/ws` 网关的长期会话 token,钉钉 ticket 短时效 + 单次使用 + 不落日志/事件载荷/出站台账),缓解 = wss + 证书校验 + 每轮重连重新换取。
>
> **单实例互斥与崩溃安全**:同一集成的 Stream worker 以数据库咨询锁(`pg_advisory_lock(hashtext('dingtalk_stream:'||integration_id))`)保证全局单连接,避免双连接导致钉钉侧负载与重复推送;即便互斥失效,`integration_events.UNIQUE(integration_id, external_event_id)` 去重仍是最终幂等兜底。worker 崩溃由进程守护(compose `restart: unless-stopped`)重拉,重连后钉钉重推未 ACK 帧,不丢消息。**平台连接上限(S-5)**:钉钉 Stream **单应用最多 50 条连接**——多个 Mesh 集成配置**共享同一 `app_key`** 时,实现期连接管理按 `(app_key)` 粒度去重共享一条物理连接(帧按 `robotCode`/集成路由分发),而非每集成各占一条;`GET .../stream-status` 对共享连接的同 app 集成返回同一连接态。
>
> **`sessionWebhook` 的处理**:入站载荷携带 `sessionWebhook`(钉钉侧快捷回复地址),时效由载荷 `sessionWebhookExpiredTime` 绝对过期戳给出(官方示例约 90 分钟,无固定值);本模块**不将其作为出站主通道**(短时效、不可靠、不利审计),仅记录于事件载荷备查;出站一律经 OpenAPI + `accessToken`(§3.10),受 §6.16 SSRF 防护(`oapi.dingtalk.com`/`api.dingtalk.com` 之外的用户可控地址不参与出站)。

**钉钉入站载荷归一与消息类型矩阵(一致性评审穷举项,写死)**:

| 官方字段 | 归一目标 / 用途 |
|----------|----------------|
| `chatbotCorpId` | `provider_tenant_key`(企业 corpId) |
| `conversationId` / `conversationType`(`"1"` 单聊 / `"2"` 群聊) | `external_ref` / 出站通道选择(群 groupMessages、单聊 oToMessages) |
| `msgId` | `external_event_id`(去重键) |
| `senderStaffId`(企业内部成员 userId,字符集 `[A-Za-z0-9._-]` 至宽口径,**发布后才返回**)/ `senderId`(`$:LWCP_v1:$…` 加密串,**含冒号**) | 发起人键:有 staffId 用 staffId 原值;无 staffId 的外部联系人 → `x=<base64url(senderId)>`(§3.10 无冒号编码,**禁取 senderId 原值**) |
| `senderPlatform` / `senderNick` | 审计载荷字段(展示/排障,不入匹配) |
| `isInAtList` / `atUsers[].{dingtalkId, staffId}` | 群聊 mention 触发判定(`isInAtList=true` 方视为 @机器人;`atUsers` 记审计) |
| `robotCode` | 出站 `robotCode`(默认同 `app_key`) |
| `sessionWebhookExpiredTime` | 仅载荷备查(不使用 sessionWebhook 出站) |

**消息类型矩阵(平台侧接收限制,如实声明)**:钉钉**群聊 @机器人仅投递 `text`/`richText`/`picture`** 三种 msgtype——**`audio`/`video`/`file` 的群聊 @ 平台不投递**(无入站事件,非本模块过滤);单聊各类型均投递。本模块**触发执行仅限 `text`**(命令平面与任务消息);`richText`/`picture` 等非文本类型**仅审计留痕(`integration_events`,`process_status='processed'`,载荷备查),不触发执行、不入队列、不 ack**(本期 YAGNI;未来支持经 match_config 扩展,需先定义多模态正文的不可信隔离形态)。

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
     → 即时执行命令处理器(鉴权经 external_identities 全三元组 → users.id 解析):
         /stop → 本人 processing 项转 cancelling + durable outbox 取消命令;本人 pending 项
                 转 cancelled(多人同群各自取消,互不牵连);机器人两段反馈(正在停止/已停止)
         /btw → 向本会话 processing 项的执行追加补充上下文(不可信数据,§6.15;上限 §3.7);
                cancelling 项不接受;无 processing 项 → 剥前缀后按普通消息继续下行
         未知命令 → 回帮助文本;命令消息不触发执行、不入队列
     → process_status='processed'(命令已处置),流程止于审计
  → 匹配 integration_bindings(external_ref + match_config):
       未匹配 / 未匹配到 agent → 仅审计(matched 留痕,不触发,README §6.9)
       命中 → 【同事务】入队 integration_message_queue(pending,seq 会话内递增,
              dispatch_mode 有效模式快照[排空-再切换],target_agent_id 快照,message_excerpt,§2.10)
              + 按有效模式决定派发时机:
                parallel(飞书/Slack 默认,且会话无残留 serial 项)→ 乐观直派:同事务转
                    dispatching + 写 outbox(execution.enqueue,§6.9 幂等键);
                    会话仍有 serial 非终态项 → 保持 pending,由派发器按序处理(不越序)
                serial_conversation(钉钉默认)→ 保持 pending,由队列派发器(§3.9)在会话
                    无任何非终态项时取首项派发(转 dispatching + 写 outbox)
              → process_status='dispatched'
  → 【emoji 确认接收(§3.8)】入队事务内(持 imq_seq 锁、取 ack_window_at=clock_timestamp() 之后)
     按 ack_window_at 判定窗口 leader:仅 leader 项【同事务】写 outbox('im.send',幂等键 §6.5);
     follower 项不写任何 ack 事件(无外部副作用,窗口归属由 ack_leader_id 结构性表达);
     ack_template='' 的集成跳过全部 ack 处理(不写事件、不占窗口)
     → 出站快 relay 消费 leader 事件:T1 同事务置 ack_attempted_at + 事件 published →
       事务外平台发送 → T2 回写 ack_sent_at + 批量回写窗口 follower represented
       (at-most-once,不重试;不经 notification_delivery)
  → relay 消费 execution.enqueue 创建 task_executions(trigger='integration'),【同事务】
     队列项 dispatching → processing + 绑定 execution_id + lease(§3.9 执行关联回写)
  → 执行终态内部事件 runtime.md execution.finished(payload.status)经 outbox 回写队列项
     done/failed/cancelled(cancelling 项在此转 cancelled)+ 同事务写 'imq.dispatch_wake'
  → 派发器随即派发本会话下一 pending 首项;摄取事件置 'processed'
```

> **`process_status='dispatched'` 的语义注记**:摄取事务置 `dispatched` 表示"已入会话队列并确定将派发"(串行 = 待派发器按序派发;并行 = 已乐观直派);队列项 `state` 是派发粒度的细化真源(pending/dispatching/processing/cancelling/终态)。该词不新增枚举值(保持与 autopilot.md §2.5 词汇同构),语义分叉仅此一处注记。

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

> **`Mesh-Event` 头与投递 body 携带真实域事件(P8,写死)**:`Mesh-Event: <event_type>` 取**派发时捕获的真实领域事件类型**(如 `issue.updated`,即 `webhook_subscription_deliveries.event_type`,派生订阅投递时自源 outbox 事件捕获),**绝不**以不透明的 outbox 事件 UUID 占位;投递 JSON body 为 `{"event": <event_type>, "data": <payload.data>, "event_ref": <event_ref>, "delivery_id": <delivery_id>}`(事件载荷捕获于 `webhook_subscription_deliveries.payload`,§2.6)——订阅方**仅凭单个投递即可还原完整域事件**,无需回查 Mesh(P8 出向订阅契约)。

> **投递时 SSRF 守卫(单次解析,闭合 DNS-rebinding TOCTOU,写死)**:投递 worker 对目标 URL 主机**恰好解析一次**——经共享的固定解析守卫(pinned-resolve)把主机名解析为候选地址集,逐一校验并拒绝私网 / 环回 / link-local / 元数据地址段(README §6.16),随后**仅连接经校验的地址**(连接阶段不再二次解析),闭合"校验时解析到公网、连接时 DNS 重绑定到内网"的 TOCTOU(DNS-rebinding)攻击面;**TLS SNI 与证书校验仍锚定原始主机名**(连接用解析后的 IP,SNI / 证书 SAN 比对用原始 hostname,不因固定解析而降级证书校验)。

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
| 409 | `binding_has_active_queue` | 删除绑定/集成时仍存在非终态队列项且未使用强制终止路径(§3.9 删除保护;`?force=cancel` 经批量终止后放行) |
| 409 | `conflict` | 名称重复 / 乐观锁冲突 |
| 409 | `duplicate_event` | 入站去重命中(通常作 200 `deduped`,内部用) |
| 401 | `integration_disabled` | 集成 `status='disabled'`,入站拒绝分发 / 出站停发(与 §3.2/§5.1 一致;原表 410 为笔误) |
| 422 | `ssrf_blocked` | 出向目标命中私网地址段 / 元数据地址(README §6.16) |
| 422 | `identifier_not_resolved` | VCS identifier(`WEB-123`)解析不到 issue(留痕,不阻塞摄取) |
| 422 | `vcs_link_invalid` | VCS 关联的 issue/vcs_ref 非法或跨工作区 |
| 422 | `subscription_circuit_open` | 订阅处于熔断(`disabled`),需 `resume` 后投递 |
| 422 | `queue_item_not_cancellable` | 队列项非 `pending` 态(已派发/终态),不可取消(§3.9;在途执行走 `/stop` 或执行取消协议) |
| 422 | `oauth_failed` | OAuth 授权码换取 token 失败 / scope 不足 |
| 429 | `rate_limited` | API 限流 / 出站平台限流退避 |
| 500 | `internal_error` | 服务内部错误 |
| 502 | `upstream_error` | 外部平台 API 调用失败(出站适配) |
| 503 | `stream_channel_unavailable` | 钉钉 Stream **接收信道**未就绪(仅限接收诊断语境,`GET .../stream-status` 超时/不可达;**测试出站站 `POST .../test-send` 不用本码**——出站经 OpenAPI 不依赖接收信道,失败走 `502 upstream_error`;摄取侧亦不返回本码) |

### 3.6 WebSocket 实时事件

连接 `/ws`(握手鉴权见 auth.md),订阅频道 `workspace:{ws}:integrations` 或 `integration:{id}`。**实时契约以 README §6.7 为唯一权威**(频道内 `seq`、唯一写入路径 outbox→projector、`resume_from`/`resync_required`);事件名取自 README §6.7 注册表「平台能力」域:

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `integration.updated` | 集成/绑定/订阅创建、配置变更、状态切换、熔断;**钉钉 Stream 连接状态变化**(`subject='stream_channel'`,`status`='connected'\|'reconnecting'\|'down') | `integration_id`, `kind`, `status`, `subject`('integration'\|'binding'\|'subscription'\|'stream_channel') |
| `integration.event_ingested` | 入站事件落库(含签名/处理状态、命令处置标记,驱动事件台账实时刷新) | `event_id`, `integration_id`, `event_type`, `signature_status`, `process_status` |
| `integration.queue_updated` | 入站消息队列项状态变化(入队/派发/终态/取消/合并抑制,§3.9;**失效通知语义**:客户端 refetch 授权分片,不做本地 patch;失效顺序以 envelope 频道 `seq` 为准,不另设 revision) | workspace 级项:`integration_id`, `conversation_key`;**project 级项不含 `conversation_key`**(仅 `integration_id` + `scope:'project'`,防向无项目可见性成员泄露会话键) |

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
| `/stop` | 可选 `[原因]`(仅审计) | 取消**命令发起人**在**本会话**的在途执行(经 `cancelling` 两阶段)与其 pending 排队项;**多人同群各自取消、互不牵连**(见下处置序列) | 发起人本人(**将发起人外部身份全三元组与目标队列项 `sender_identity_key`(全三元组,§2.10)各自经 `external_identities` 解析到 `users.id`,两 `users.id` 相等**;禁止 UUID 与外部键字符串直接比对、禁止裸键解析);或对该绑定所属工作区/项目有 `execution:manage` 权限的成员(经成员名册链解析,同卡片回调鉴权链 §3.2) |
| `/btw` | 必填 `<补充说明>`(受 `MESH_IM_INBOUND_TEXT_MAX_CHARS` 截断,§2.10) | 向本会话**正在处理**(队列项 `state='processing'`,**`cancelling` 项不接受**)的执行追加补充上下文;不打断、不新建执行;**受每执行追加上限约束**(runtime.md `execution_context_appends`,M3) | 同 `/stop`(越权拒绝路径同 `/stop` 第 5 步:回拒绝文本 + 审计、不泄露目标任务详情)。**补充内容一律按不可信数据隔离注入**(README §6.15:结构化包裹 + 标注来源 `im_btw`,agent 不得作为指令执行;高风险动作仍走 `confirm_required`) |
| `/help`(内置) | 无 | 回命令清单与用法 | 任何人(含未映射身份) |

> 注册表是服务层常量结构 `{name: {permission, handler}}`,新增命令 = 登记一行 + 实现处理器,不改摄取管线(YAGNI:本期仅 `stop`/`btw`/`help`,不预建别名/参数解析框架之外的机制)。

**`/stop` 处置序列(确定性语义,可测试)**:
1. 解析发起人外部身份**全三元组**(`provider`+`provider_tenant_key`+`external_user_key` → `external_identities.users.id`);**未映射 → 机器人回"请先在 Mesh 站内连接你的外部账号"并附建链入口提示,仅审计,不取消任何东西**。
2. **分别、独立地**处理发起人在本会话的两类项(多人同群语义:即使当前 `processing` 项属于他人 A,发起人 B 的 `/stop` 仍取消 B 自己的 pending,不因"无权停 A"而整体拒绝;两类处置互不牵连):
   - **(a) 在途项**:查本会话 `state='processing'` 且经三元组解析为本人(或请求者具 `execution:manage`)的队列项——**有则原子转 `cancelling`(`UPDATE … SET state='cancelling', updated_at=now() WHERE id=:id AND state='processing'` 守卫)+ 同事务调用 runtime 执行取消服务(`POST /api/v1/workspaces/{ws}/executions/{id}:cancel` 的服务层函数,runtime.md R13:同事务置执行 `cancelling` + `cancel_requested_at`,取消意图即持久化于 DB,daemon 经心跳下行 cancel 指令真停,`failure_reason='cancelled_by_command'`)**——取消是**本地 DB 持久化**,不经事务外网络调用,无"提交后丢失"窗口。**项保持 `cancelling` 继续占用串行 lane**,直到 `execution.finished(status=cancelled)` 到达才转 `cancelled`——**不提前释放会话独占**(运行中的执行优雅停止期间,下一项不得启动);daemon 离线时取消意图已由 DB 持有,daemon 恢复后经心跳下行执行停止,不丢取消意图;
   - **(b) 排队项**:原子批量取消发起人在本会话的全部 `pending` 项(`UPDATE … SET state='cancelled', finished_at=now() WHERE conversation_key=:k AND state='pending' AND <三元组解析本人>`,按 `seq` 序);**立即生效**(pending 无执行,无需两阶段)。
3. **两段式反馈文案**(经 outbox `im.send`,不是 ack、不经合并窗口):
   - **即时段**:命中 (a) → "⏳ 正在停止任务「<消息摘要>」…";仅 (b) → "已取消 N 条排队消息";(a)+(b) → "⏳ 正在停止任务「…」,并已取消 N 条排队消息";皆无 → "当前没有进行中或排队的任务(你的)"。**即时段载荷自携 `conversation_type` 与单聊 `target_user_key`(命令发起人即单聊收件人)**——即时段反馈可在空队列下触发(/help、未知命令、无在途项的 /stop),不依赖队列项出站派生,单聊经 `oToMessages` 投递、群聊经 `groupMessages`;
   - **终态段**:`execution.finished(cancelled)` 回写消费方同事务写 `im.send`(携带 `queue_item_id`,会话类型与单聊目标经队列项 + 源入站事件派生)→ "🛑 已停止任务「<消息摘要>」"。重复 `/stop`(项已 cancelling/终态)→ state 守卫 0 行 → 幂等回"任务正在停止中"或"当前没有进行中的任务"。
4. **执行已在终态的竞态**:转 `cancelling` 成功但执行恰好先达终态 → 终态回写按 `execution.finished.payload.status` 映射(completed→done/failed→failed),`cancelling` 同样接受终态映射(state 守卫防重),取消命令到达 runtime 时幂等 no-op。
5. 越权(发起人对**他人**在途项发 `/stop`)→ 该项不动 + 机器人回 `command_forbidden` 语义的拒绝文本(IM 命令无 HTTP 客户端,错误码为内部分类,渲染为机器人文案;HTTP 403 形态仅适用 `:cancel` 端点)+ 审计留痕,**不泄露目标任务详情**;但第 2(b) 步对本人 pending 的取消仍执行(越权仅针对他人项)。

**`/btw` 处置序列**:
1. 授权同 `/stop`(越权 → 拒绝文本 + 审计,不泄露详情,不注入)。
2. 本会话存在 `state='processing'` 队列项(且非 `cancelling`)→ 经 **runtime 运行期上下文追加机制**(`execution_context_appends`,runtime.md owns:服务层以队列项 `execution_id` 写入 append 行)追加补充消息(载荷 `{source:'im_btw', sender:<三元组解析的 Mesh 身份>, text:<args(截断后)>, received_at}`,**不可信数据隔离**,README §6.15)。**追加上限(M3)**:每执行 `MESH_CONTEXT_APPEND_MAX_COUNT`(默认 20 条)与 `MESH_CONTEXT_APPEND_MAX_CHARS`(默认 32000 字符累计)双上限,**超限 → 不写入**,机器人回"补充已达上限,请直接新建任务说明" + 审计(长时执行的成本放大残余面护栏;频率护栏限瞬时、上限管累积)。**生效时机:LLM 单轮不可打断,补充在该执行下一 agent turn 边界注入**(runtime.md/agent.md 已登记;心跳 `context_progress` 水位 + ACK 去重 + attempt 切换语义见 runtime.md「运行期上下文追加」);机器人回"已补充给正在处理的任务(将在下一步生效)"。
3. 本会话项为 `cancelling` → 机器人回"任务正在停止,无法补充;停止完成后可重新派发"。
4. 无 `processing`/`cancelling` 项 → 剥除 `/btw` 前缀后的 `args` **按普通消息继续下行**(匹配 → 过频率护栏 → 入队),机器人先回一句提示"当前没有进行中的任务,已按新消息排队"。
5. `/btw` 无参数 → 回用法帮助。

**不可信与防滥用**:命令参数(尤其 `/btw` 文本)是入站不可信内容,隔离与长度上限(4000 字符截断)同消息正文(§2.10);命令平面频率受 **§2.10「入站频率护栏」** 三道计数约束(每身份/每会话/排队深度);命令处置全程写审计(`integration_events.payload._mesh_command = {name, actor_identity(三元组), target_item_ids, result}`)。

### 3.8 emoji 确认接收(ack,§1.1 MES-82 能力 2)

**平台能力现状与语义选型(一致性评审核校)**:钉钉官方**公开文档未提供**机器人消息级 emoji 回应(reaction)能力,但官方 SDK(robot_1.0)存在未公开文档的 `POST /v1.0/robot/emotion/reply`(及 `emotion/recall`)接口——其是否对企业内部应用开放、`emotionName` 取值表均**未经官方文档确证(unverified)**;查询消息 reaction 列表的接口官方 SDK 与文档**均不存在(确认缺失)**。飞书/Slack 有 reaction API。**本模块的语义选型(写死)**:以**"轻量确认消息"为 emoji 确认接收的基线实现**——三平台行为一致、零 unverified 平台依赖、不依赖 reaction 查询接口;机器人在摄取成功后**立即**回一条以 emoji 起始的短消息(默认模板 `✅ 已接收,处理中`,经 `integrations.config.ack_template` 可按集成配置;置空字符串 = 关闭该集成的确认消息)。**实现期实测切换点(验收阶段真实联调时判定)**:若 `/v1.0/robot/emotion/reply` 实测对测试企业可用且稳定,钉钉 ack **可升级**为对原消息贴 emoji 回应(✅)+ 保留确认消息作降级路径(贴表情失败回落轻量消息);该升级不改本章投递语义(at-most-once、leading-edge 合并、outbox 快通道照旧),仅替换出站适配器的发送动作。

**投递语义(at-most-once,写死;不做 best-effort 重试)**:outbox 是 at-least-once,而"平台已发送、落库前崩溃"无法由 outbox 幂等键阻止重复外呼,故 ack **选定 at-most-once**:宁可偶发漏发(确认消息是体验增强、非任务真源),**不产生重复确认**。

**leader 在摄取/取号事务内按 seq 确定(不依赖 relay 到达顺序,写死)**:入队事务已持有会话级取号咨询锁(`imq_seq:<conversation_key>`,§2.10)串行化同会话全部入队——**窗口 leader 判定复用该锁,在入队事务内按 seq 顺序完成**:

```
入队事务(持有 imq_seq 咨询锁;已取 ack_window_at = clock_timestamp()、seq = max+1):
  若 integrations.config.ack_template = '' → 【跳过全部 ack 处理】不写事件、
    ack_leader_id 留 NULL、本项不参与/不占用任何 ack 窗口(关闭确认的集成零窗口副作用)
  否则查本会话"覆盖 leader":满足 ack_leader_id = L.id(自指,L 即 leader)
    且 本项.ack_window_at ∈ [L.ack_window_at, L.ack_window_at + MESH_IM_ACK_COALESCE_WINDOW)
    的最近项 L(**窗口时间一律按锁序 ack_window_at,不用 enqueued_at/now()**)
  · 命中 → 本项为 follower:ack_leader_id = L.id;【不写 im.send outbox 事件】
           (follower 无外部副作用;represented 语义经 ack_leader_id 指向表达,
            ack_represented_at/ack_merged_into 由 leader T2 成功后批量回写)
  · 未命中 → 本项为 leader:ack_leader_id = 本项.id(自指);
             【同事务】写 outbox_events(event_type='im.send',
               幂等键 sha256(queue_item_id | 'ack')(README §6.5 登记键),
               payload { kind:'ack', conversation_key, template, position_snapshot })
```
> seq 序判定杜绝"晚消息先被 relay 处理而僭越 leader":M1(seq=1,t0)、M2(seq=2,t0+1s)无论 relay 到达顺序如何,M2 入队时 M1 已是 leader(自指)且在窗口内 → M2 必为 follower。

**五字段语义(互斥,写死)**:
| 字段 | 语义 | 谁写 |
|------|------|------|
| `ack_leader_id` | 窗口归属:leader 项自指(`= 本项.id`);follower 项指向其 leader;**摄取事务内按 seq 确定** | 入队事务 |
| `ack_attempted_at` | **leader 项**的外呼闸门(relay T1 持久化;非 NULL = 已承诺至多一次外呼) | relay 事务 T1 |
| `ack_sent_at` | **平台已确认收到**该 leader 确认消息(外呼成功后回写) | relay 事务 T2 |
| `ack_represented_at` | **follower 项**:已被窗口 leader 的确认消息代表(本项**不外呼**,这不是"已发送") | leader T2 成功后批量回写(见下注) |
| `ack_merged_into` | follower 指向的 leader 项 id(= `ack_leader_id` 冗余明示,审计便读) | 同上 |

> **follower 的 `ack_represented_at`**:follower 入队时不写 outbox 事件(无副作用),其 represented 状态由 `ack_leader_id ≠ 自身` 结构性表达;为保留审计可查性,leader 外呼成功(T2)后**同事务**批量回写本窗口 follower 的 `ack_represented_at=now()`/`ack_merged_into`(`WHERE ack_leader_id=:leader AND ack_represented_at IS NULL`)——leader 外呼失败/丢失时 follower 保持空(如实反映"代表确认未送达",审计可辨)。

**快 relay 事务边界协议(leader 路径,与 README §6.6 机械衔接,写死)**:
```
ack 快 relay(outbox relay 同进程集合的高优先级受监督任务,目标消费延迟 <2s):
  领取 im.send 事件(FOR UPDATE SKIP LOCKED,仅 status='pending' 可领)
  【T1(闸门 + 事件终态化,同一短事务)】
    UPDATE 队列项 SET ack_attempted_at=now() WHERE id=:leader AND ack_attempted_at IS NULL
    + UPDATE outbox_events SET status='published', published_at=now() WHERE id=:evt
    提交 —— 【关键:事件在 T1 即 published,外呼前已终态化】
      · T1 提交后崩溃 → 事件不会被再次领取(at-most-once 允许漏 ack:attempted ∧ ¬sent = 丢失,
        审计可辨;不存在"仍 pending + attempted"的歧义态,第二 worker 无从领取同一事件)
      · T1 提交前崩溃 → 事件仍 pending、attempted 仍 NULL → 重新领取照常处理(干净重入,
        不会产生 lost + sent 并存的审计)
  【外呼(T1 提交后、事务外)】经钉钉 OpenAPI 发确认消息(§3.10,3s 超时)
  【T2(仅结果落库)】
    · 成功 → SET ack_sent_at=now()(守卫 IS NULL)+ 批量回写窗口 follower represented(见上注)
    · 失败/超时 → 【不重试】写 _mesh_ack_failed 审计(at-most-once:不补发;任务派发不受影响)
```
> **不**在摄取请求内同步直发外部平台(README §6.6:外部可见副作用一律经 outbox;ack 外呼是 outbox 事件 handler 的消费动作:T1 闸门 + 事件终态化 → 事务外外呼 → T2 结果落库)。

**leading-edge 合并(防 ack 反射放大,安全护栏)**:窗口 leader(摄取事务内按 seq 判定,见上)即时发出通用确认("✅ 已接收,处理中/排队中",携带发送时刻 best-effort 位置);窗口 `[leader.ack_window_at, leader.ack_window_at + MESH_IM_ACK_COALESCE_WINDOW)`(默认 5s,**锁序时间**,不用事务开始时刻)内后续项为 follower(无外呼、无 outbox 事件,仅结构性指向 leader)。**relay 到达顺序与并发对合并结果无影响**(leader 由入队 seq 序先定);**不做尾部"共 N 条"合并消息**(与"首条即时"不可兼得,YAGNI)。

**规则(可测试)**:
- **仅 `dispatched` 的任务消息触发 ack**:去重(`deduped`)、未匹配、被限频(`rejected` + `_mesh_reject_reason='rate_limited'`)、命令消息、被拒消息**一律不发 ack**(避免重复事件刷确认、避免给未绑定群发噪音、避免灌消息放大出站配额)。
- **即时性**:窗口 leader 确认目标端到端延迟 <2s(§5.6 验收该指标,不依赖 CI 时序抖动断言)。
- **串行排队下的位置提示**:仅 **leader 确认**携带位置(发送时刻重算:本会话更小 seq 的 pending 计数 + 1,对冲式措辞"已排队(第 N 位,可能很快轮到)");被抑制项不单独发消息(其位置经队列面板/`queue_updated` 可查)。
- **限频共享**:ack 出站量受入站频率护栏(§2.10「入站频率护栏」)上游约束——超限消息根本不入队、不产生 ack 事件,从源头遏制"N 条灌入 → N 条 ack → 平台发送配额耗尽"的反射放大。
- **台账**:确认接收与命令反馈是**会话性回复,不是通知**——不经 `notification_delivery`(其 `notification_id` NOT NULL 挂靠 `notifications` 真源,comment-inbox.md §2.8),发送结果记入队列项四字段与 `integration_events.payload`;任务进度/结果等**真通知**才经 `notification_delivery(channel='im', provider='dingtalk')`(README §6.13,§3.10)。

### 3.9 入站消息队列(§2.10,「新消息自动排队」)

**部署形态**:队列派发器(queue dispatcher)与出站快 relay(im.send fast relay)、钉钉 Stream worker 均为 **`mesh.workers` 进程内新增的受监督 asyncio 任务**(backend/README.md 既有 worker 任务集合的同构扩展,**不新增 compose service**、不改变部署拓扑);与 outbox relay/projector 共享进程监督与重试策略。**租户安全上下文对齐(纵深防御)**:三者均以 DB owner 角色运行(跨租户扫描),凡进入"与 HTTP 摄取同一服务函数"的路径,**入口处显式 `set_tenant_context(workspace_id)`(设 `mesh.workspace_id` GUC)使 RLS 与 HTTP 路径(mesh_app 受限角色)等价生效**,不因 worker 特权身份旁路 RLS。

**派发器(queue dispatcher)**:消费 `integration_message_queue`,**按项的 `dispatch_mode`(入队时快照)而非集成实时 config 决定行为**(排空-再切换规则保证 serial→parallel 切换待旧 lane 排空后生效,存量项不被饿死、新 parallel 项不越序):

```
循环(1s tick 兜底 + 经 outbox 'imq.dispatch_wake' 事件显式唤醒):
  取候选会话:存在 pending 项 且 满足派发前置
    派发前置(按首项 dispatch_mode):
      serial_conversation → 本会话【无任何】非终态项(含 parallel 的 dispatching/processing/
                            cancelling——跨模式串行,防 serial 与残留 parallel 重叠)
      parallel → 仅当摄取事务乐观直派失败回退的项(正常 parallel 在摄取事务已直派);
                 前置同 serial(不越序)
  对每个候选会话(会话间并发、互不阻塞):
    FOR UPDATE SKIP LOCKED 取该会话最小 seq 的 pending 项(严格 FIFO 首项)
    → 校验 target_agent_id 快照仍可用(agent 存在且 active;已 SET NULL/停用 →
      项转 failed(reason='target_unavailable') + 审计,不静默改派)
    → 同事务:UPDATE 项 state='dispatching', lease_expires_at=now()+timeout+buffer
              (uq_imq_conversation_active 对 serial 项数据库级兜底;UPDATE 0 行 = 会话已被
               另一派发副本占用,退避)
              + 写 outbox(execution.enqueue, trigger='integration',
                 幂等键 sha256(target_agent_id | binding_id | external_event_id),§6.9)
  parallel 正常路径:摄取事务内已乐观转 dispatching + 写 outbox(§3.2),不经派发器等待
```

**执行关联回写(两种模式共用)**:`execution.enqueue` 的 outbox 消费方(relay)在创建 `task_executions` 的**同一事务**,以 `trigger_event_id → integration_message_queue.integration_event_id` 反查队列项:`UPDATE integration_message_queue SET execution_id=:exec, state='processing', started_at=now(), lease_expires_at=now()+timeout+buffer WHERE integration_event_id=:evt AND state='dispatching'`(state 守卫保证 `dispatching → processing` 单一转换;0 行 = 租约修复已处置或重复消费,幂等跳过)。队列面板"处理中项 → 执行详情深链"在两种模式下均经此回写成立。

**终态回写与不丢失保证**:
- **终态的唯一驱动是内部领域事件 `execution.finished`**(runtime.md owns:执行终态单一扇出 outbox 事件,payload `{execution_id, status, failure_reason}`,`status ∈ completed/failed/timeout/cancelled`;**非 README §6.7 实时事件名**——实时事件 `execution.completed/failed/…` 与本回写均由其派生):消费方按 `execution_id` 反查队列项,`completed → done`、`failed/timeout → failed`、`cancelled → cancelled`,`finished_at=now()`(**接受从 `processing` 与 `cancelling` 两种态转入**,state 守卫防重、终态→终态 no-op);回写事务**同事务写 `imq.dispatch_wake`(payload 含 conversation_key)** + `cancelling` 项的终态段反馈消息(§3.7 两段式)+ `integration.queue_updated` 失效通知(见下);1s tick 仅作兜底,M1→M2 衔接时延有界。
- **租约修复(崩溃安全,五分支全覆盖)**:周期扫描 `state IN ('dispatching','processing','cancelling') AND lease_expires_at < now()` 孤儿项,按其执行状态分支:
  1. 执行已终态(终态事件丢失)→ 按 `execution.finished.payload.status` 补回写 `done`/`failed`/`cancelled`;
  2. 执行**仍在途**(claimed/running/cancelling——合法长任务、缓冲取小了)→ **续租对齐**:`lease_expires_at = max(now()+buffer, 该执行当前 attempt 租约)`,不置失败(长任务不误杀);
  3. 执行**仍 `queued` 且未超** `MESH_IM_QUEUE_MAX_STUCK_SECONDS` → **续租等待**(同分支 2,容量压力下属合法等待);
  4. 执行存在但 `queued` **超** `max_stuck`(默认 = 2×执行超时)→ 置 `failed(reason='dispatch_stuck')` + 告警,**不重派发**(幂等键固定,重入队必为 no-op,重派是死路);
  5. 执行不存在(入队事件消费失败/丢失)→ **outbox rearm(按 README §6.6 真实 `outbox_events` DDL,字段 `status ∈ pending/published/failed`、`delivery_attempts`、`available_at`(R4-4 退避)、`published_at`)**:查该队列项原 enqueue 事件(`idempotency_key` = 原键),**四态闭合**:
     - **a. `status='pending'`(relay 尚未消费/消费中崩溃)→ 不造新事件**:续队列项租约(`lease_expires_at = now()+buffer`),等待现有 relay 消费(relay 以 pending 行为扫描真源,本项会被自然消费);**升级条件**:若 `outbox_events.created_at < now() - MESH_OUTBOX_CONSUME_SLA`(默认 = 2× relay 轮询间隔)且仍 pending、执行仍不存在 → 视同丢失走 d(rearm 键新写);
     - **b. `status='failed'`(relay 重试耗尽)→ 条件 rearm:`UPDATE outbox_events SET status='pending', delivery_attempts=0, published_at=NULL WHERE id=:evt AND status='failed'`**(0 行 = 其他修复副本已先 rearm,退避);relay 重新消费创建执行(原幂等键,执行行不存在故创建成功);
     - **c. `status='published'`(handler 自称完成但执行行缺失——理论不可达:消费事务回滚则不会置 published)→ 不 DELETE 原行**(outbox 终态行按 §6.6 保留期审计保留,删除破坏审计;派生 rearm 键本就不撞原键唯一约束,无需释放占位):走 d;
     - **d. 行不存在 或 c 之异常 published → INSERT 新 outbox 事件**:`event_type='execution.enqueue'`,**键分层(R4-3/R5-2 写死)**——`outbox_events.idempotency_key = K2 = sha256(K | 'rearm' | item_id)`(仅 outbox 行级去重,与原键 K 不撞唯一约束)而 **payload 沿用既有平台标准字段 `idempotency_key = K`(稳定执行级键)+ 携带 `queue_item_id`**(不引入新字段,与现有消费者 `payload.idempotency_key` 读取路径兼容);
     - **消费者契约(R5-2 集成作用域,不改动全局 enqueue 契约)**:执行级幂等键取 `payload.idempotency_key`(既有平台标准,现有消费者路径不变;原始事件[K]与派生事件[K2 payload 携带 K]创建/命中**同一** `task_executions.idempotency_key=K`,执行唯一约束跨两者去重)。**仅 `trigger='integration'` 附加要求**:① payload 必须携带 `queue_item_id`;② 消费者事务**先 `FOR UPDATE` 锁该队列项并守卫状态**(`state='dispatching' AND execution_id IS NULL`),守卫通过方创建执行并在**同一事务**绑定 `execution_id` + 转 `processing`;守卫失败(项已被其他消费者绑定/已终态)→ **整事务回滚,不创建执行(无孤儿 execution)**;③ 旧 pending 事件与派生事件**任意消费顺序、并发消费 → 恰好一个 execution**(键 K 唯一约束 + 队列项守卫双保证),队列项只绑定该执行。**其它触发(assign/mention/autopilot/chat)保留既有消费契约**(payload.idempotency_key 与事件级键 fallback 行为不变,无队列项、无附加守卫)。
     **仅 b/d 真正重派发,a 等待现有 relay**;validation 以真实 `outbox_events` DDL 对四态逐一实测 + **T39-17 约束结果模拟**(单事务顺序验证两消费顺序的约束结果;真实双消费者交错归服务层并发测试,§5.6;T39-9/T39-14/T39-17)。
  任一分支都使项离开"在途且过期"集合,杜绝扫描空转;**任何崩溃路径下已入队消息要么被执行、要么进终态可查,不静默丢失**。
- **删除保护(成功路径闭合,§2.10 `ck_imq_orphan_terminal` + SET NULL)**:绑定/集成删除端点(§3.1 DELETE)两种形态:**无 `force`** —— 存在**非终态**项 → `409 binding_has_active_queue`(终态项不阻塞);**`?force=cancel`** —— ① 强制批量终止:该父对象下 `pending` 项 → `cancelled(reason='binding_deleted')`,`dispatching/processing/cancelling` 项 → 调用 runtime 取消服务(幂等)并等待终态(上限 30s,超时强制 `cancelled(reason='binding_deleted')` + 告警,执行侧取消意图已由 DB 持久化、daemon 恢复后继续停止);② **DELETE 父行 → 全部队列项已终态,FK `ON DELETE SET NULL` 触发,父引用置空、删除实际完成**(DELETE 语句成功返回,不被 FK 阻塞);③ 孤儿审计行(`binding_id IS NULL`,携带 `binding_display`/`project_id_snapshot`/`conversation_key`/`sender_identity_key` 自描述)经**工作区级审计端点 `GET /workspaces/{ws}/integration-queue-audit`** 可查(成员角色;project 级项按 `project_id_snapshot` 可见性过滤;仅返回终态孤儿行),保留期 `MESH_IM_QUEUE_AUDIT_RETENTION`(默认 30 天)后由 worker 分批物理清理(仅清理 `binding_id IS NULL` 的终态行)。**绕过端点的直接父行 DELETE**(含项目物理删除对 project 级绑定的 CASCADE 链)在非终态项存在时被 `ck_imq_orphan_terminal` CHECK 拒绝(SET NULL 使非终态项失去父引用 → CHECK 违例 → 整个 DELETE 回滚)——项目删除服务流程须先对其 project 级绑定执行同一强制终止(fail-closed:未清理则项目删除失败,不静默丢消息)。

**查询与操作端点**:

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/workspaces/{ws}/integrations/{id}/queue` | 队列状态:按会话分组返回项(`conversation_key`、`seq`、`state`(含 dispatching/cancelling)、`position`(pending 项的实时排队位置,= 本会话更小的 pending 计数 + 1)、`sender`(经 `external_identities` + 展示层解析的显示名,未映射显示外部昵称 + 未连接标记)、**`target_agent`(`{id, name}`,入队快照解析)**、**`message_excerpt`(截断净化摘要,≤120 字符;全文不经本端点——经事件台账 `GET .../integrations/{id}/events` 的 payload,UI 默认不向普通成员展开全文)**、`ack_sent_at`、`ack_merged_into`、`execution_id`(→ 执行详情深链)、时间戳);过滤 `state`/`conversation_key`;游标分页键 `(enqueued_at, id)`(走 `idx_imq_integration_state`,README §6.14);**project 可见性过滤:属于 `scope='project'` 绑定的队列项仅对具该 project 可见性的成员返回(project.md 规则),workspace 级绑定的项对全体成员可见** | 成员 |
| GET | `/workspaces/{ws}/integrations/{id}/queue/summary` | 轻量汇总:各会话 pending 数 + 当前在途项(dispatching/processing/cancelling)摘要(队列面板徽章/角标用;同 project 可见性过滤;走 `idx_imq_binding_state`/`idx_imq_ws_state`) | 成员 |
| POST | `/workspaces/{ws}/integrations/{id}/queue/{item_id}:cancel` | 取消 **pending** 项;**原子条件更新 `UPDATE … SET state='cancelled', finished_at=now() WHERE id=:id AND state='pending'`(0 行 → `422 queue_item_not_cancellable`,杜绝与派发器的 TOCTOU 竞态;在途项走 `/stop` 两阶段)**;**授权:由项的 `conversation_key`/绑定派生 `(provider, provider_tenant_key)`,与项的 `sender_identity_key`(全三元组,§2.10)组全键经 `external_identities` 解析到 `users.id`,与请求者经成员行解析的 `users.id` 比对相等(本人),或请求者对该集成/绑定有 `integration:manage` 权限;禁止仅凭裸 external_user_key 解析** | 成员(本人或 manage 权限) |
| GET | `/workspaces/{ws}/integration-queue-audit` | **孤儿队列审计(删除保护配套;孤儿项的唯一读取入口)**:`binding_id IS NULL` 的终态孤儿行(强制终止/父删除后保留),按 `binding_display`/`project_id_snapshot`/`conversation_key`/`sender`/`state`/时间展示;**可见性写死:`project_id_snapshot` 非空 → 按该项目可见性过滤,快照项目已物理删除 → 仅工作区 admin/owner 可见**(项目不存,成员级可见性无所依,admin 兜底审计);`project_id_snapshot` 为空(原 workspace 级绑定)→ 全体成员可见;**普通 `.../integrations/{id}/queue` 与 `.../queue/summary` 固定 `WHERE binding_id IS NOT NULL` 排除孤儿项**(孤儿不经普通队列端点返回);游标分页;保留期 `MESH_IM_QUEUE_AUDIT_RETENTION`(默认 30 天)后由 worker 清理 | 成员(快照项目已删时 admin/owner) |
| GET | `/workspaces/{ws}/integrations/{id}/stream-status` | **接收连接诊断(仅 Stream 模式)**:钉钉 Stream 连接状态(`integrations.stream_state`,§2.2):`state`(connected/reconnecting/down/disabled)、`last_frame_at`、`last_attempt_at`、`backoff_seconds`;UI 首屏与接收诊断的真源(实时事件未达前即可读)。**与 [测试发送] 分离**——本端点只读接收信道态,不发起出站 | 成员 |
| POST | `/workspaces/{ws}/integrations/{id}/test-send` | **测试出站(与接收诊断分离)**:经 OpenAPI 出站适配器向指定会话发一条测试文本(§3.10);失败按出站语义返回 `502 upstream_error`/凭据失效提示——**不**因 Stream 接收信道 down 返回 `stream_channel_unavailable`(出站不依赖接收信道);`503 stream_channel_unavailable` 仅供接收诊断语境(stream-status 不可达/超时)使用 | admin / `integration:manage` |

**实时(失效通知语义,写死)**:`integration.queue_updated`(README §6.7 注册表新增,「平台能力」域)是**失效通知,不携带可变明细、不自维护 revision 计数器**——**排序/去重/幂等失效一律以实时 envelope 的频道 `seq` 为准**(README §6.7 统一实时契约:projector 在投影事务内分配频道 seq,客户端凭 `resume_from` 重放;本事件不另造 revision 真源)。payload 按作用域分两形(**project 级元数据隔离**):
- **workspace 级绑定的项** → 推 `workspace:{ws}:integrations` 频道,payload `{integration_id, conversation_key, subject:'queue_updated'}`(workspace 成员均可见该会话键);
- **project 级绑定的项** → **payload 不含 `conversation_key`**(仅 `{integration_id, subject:'queue_updated', scope:'project'}`),杜绝向无该 project 可见性的成员泄露会话键;需要明细的客户端经授权 refetch 获取(端点按 project 可见性过滤,§3.9)。
客户端收到任一形态 → **refetch `.../queue`(或 `.../queue/summary`)授权分片**,不本地 patch 单项状态/位置(取消 M2 使 M3 等后继位置全部变化,单项载荷必然失真)。入队/派发/终态/取消/合并抑制均触发;经唯一写入路径(outbox→projector)发布;降级轮询 `.../queue/summary`(3~5s)。

### 3.10 钉钉出站适配(令牌缓存 / 消息发送 / 主动推送 / 卡片)

**accessToken 缓存刷新(多副本语义,写死)**:多个 `mesh.workers` 副本共享同一令牌缓存,刷新必须是**分布式单飞**:
- **共享缓存**:Redis 键 `dingtalk:access_token:<integration_id>` → `{token, expires_at}`,写入 TTL = `7200s − 300s 缓冲 + jitter(±60s,防多集成同时过期惊群)`;副本读取本地进程缓存(LRU,≤30s)未命中再读 Redis。
- **主动刷新(所有权安全协议,写死)**:本地/共享缓存 `expires_at` 临近(≤5 分钟)→ 抢分布式锁 `SET dingtalk:token_lock:<integration_id> <random_owner_token> NX EX 30`(**每次抢占生成随机 owner token**,30s 租约)→ **抢到后双检** Redis `expires_at` 仍临近方调用 `POST /v1.0/oauth2/accessToken` `{appKey, appSecret}`(**该请求超时 = 10s,严格小于 30s 租约,持锁期间不会租约过期**;若平台响应异常接近超时不续租,直接失败走重试路径)→ 写回共享缓存 → **经 Lua 条件脚本释放锁(仅当 `GET lock == 本 owner token` 方 `DEL`,防超时后迟到的旧 owner 误删新锁)**。
- **follower 有界等待(覆盖刷新超时窗口,写死)**:未抢到锁的副本(follower)→ **500ms 双检重读共享缓存,循环至 `MESH_TOKEN_FOLLOWER_WAIT`(默认 12s = 刷新请求超时 10s + 2s 缓冲)——正常刷新(≤10s)期间 follower 必然等到新令牌,不得在 leader 合法刷新期间终态失败** → 等待窗口耗尽仍无令牌 → **尝试重抢一次**(持锁者可能已崩溃且租约到期)→ 仍未得 → 本次出站结果为 **`token_refresh_busy`(可重试退避结果,非终态失败):outbox 事件保持 `pending`,仅后移 `available_at`(README §6.6 权威字段,短退避默认 +2s)、**不递增 `delivery_attempts`——不消耗失败预算、不终态,`available_at` 过滤防热循环**(**#58 迁移落点:该字段随 README §6.6 权威 DDL 入 migration,relay 领取 SQL 按 `available_at <= now()` 过滤**);出站投递台账记一次 busy 尝试;**仅凭据真失败(refresh 端点返回 invalid app_secret 类错误 → `invalid_credentials` 终态)或刷新端点连续失败超 relay 重试预算(`upstream_error` 终态,递增 `delivery_attempts`)才终态 `failed`**。
- **崩溃恢复与迟到的旧 owner**:锁持有者崩溃 → 租约 30s 自动释放,等待副本经"尝试重抢"接管;接管后**旧 owner 迟到释放被 Lua owner 比对拒绝**(锁值已是新 owner token,DEL 不执行)——**任何时刻至多一个有效刷新者**;共享缓存写入是先于锁释放的独立 SET,持有者写后崩溃不影响令牌可用。
- **平台侧失效强制刷新**:钉钉返回令牌失效错误码(如 `40014`/`88`)→ 作废旧缓存 + 按上述所有权协议抢锁强制刷新**一次**(幂等:刷新后重试原请求仅一次,仍失败记 `failed`),全副本经共享缓存失效一致。
- 刷新彻底失败/凭据撤销 → 出站投递记 `failed` + 告警(不阻塞其他集成);**令牌值与 appSecret 永不回显响应/日志/出站请求调试信息**(README §6.16 全通道脱敏:**解密后的 app_secret/accessToken 一律登记 `redact_in_logs` 黑名单**;`connections/open`/`accessToken` 等携带明文秘钥的出站请求体在日志、错误台账、投递详情中以 `***` 替换,502 排障仅记 `method/url/status`,不记 body)。

**发送通道**:

| 场景 | 通道 | 载荷 |
|------|------|------|
| 群消息(确认接收/进度/结果/命令反馈) | `POST /v1.0/robot/groupMessages/send` `{robotCode, openConversationId(=external_ref), msgKey, msgParam}` | `msgKey` ∈ `sampleText`/`sampleMarkdown`/`sampleActionCard6` 等;`msgParam` 为对应 JSON 字符串 |
| 单聊消息 | `POST /v1.0/robot/oToMessages/batchSend` `{robotCode, userIds:[<senderStaffId>], msgKey, msgParam}` | 同上 |
| 审批/交互卡片 | **钉钉互动卡片 card_1.0 体系(写死,勿与旧 `im/robots/interactiveCards` 混用;`sampleActionCard6` 等传统 ActionCard 无回调/无更新能力,仅可作纯通知卡,严禁承载 §6.10 审批卡)**:投放经 **`POST /v1.0/card/instances/createAndDeliver`** —— `cardTemplateId`(模板)+ **`outTrackId`(每卡唯一,绑定 `approval_id`,回调据此回查审批)** + **`openSpaceId`(`dtv1.card//IM_GROUP.<openConversationId>` 群 / `dtv1.card//IM_ROBOT.<senderStaffId>` 单聊)** + `cardData` + **`callbackType='STREAM'`(复用本模块已建长连接,推荐)或 `'HTTP'`**(独立回调地址,签名同 §3.2 钉钉行,二选一与接收模式对齐);更新经 `PUT /v1.0/card/instances`;卡片参数受官方硬约束(`cardParamMap` key≤100B / value≤1KB) | 回调(topic `/v1.0/card/instances/callback`)鉴权链同飞书/Slack 卡片(§3.2/§4.3 流程 B):**点击者身份锚定回调载荷 `userId`(配 `userIdType`,**写死按 staffId 归一**),无 staffId 回落 `x=<base64url(senderId)>` 编码**(§3.10 编码,与入站同映射)→ `external_identities`(provider='dingtalk', tenant=corp_id)→ 全局 `users.id` → 集成解析 workspace → JOIN `members` 名册行 → README §6.10 权限校验 → 转发 `POST /approvals/{id}/approve\|reject`;未映射/无名册行/无权限 → 403,审批状态不变,留痕;**回调响应即回写卡片**(响应体 `cardUpdateOptions` + `cardData`/`userPrivateData`,loading 态经 `userPrivateData` 按点击者私有呈现,终态经 `cardData` 公共更新;§4.4 生命周期) |

**主动推送(任务进度与结果)**:执行进度/结果通知经统一通知管线(README §6.13 `channel='im'`)→ 出站适配器按 `notification_delivery.destination_key='dingtalk:<binding_id>:<conversationId>'` 投递到源会话;台账落 `notification_delivery(channel='im', provider='dingtalk')`;限流退避(钉钉 OpenAPI 速率限制)与失败重试经出站适配器统一处理(同飞书范式)。**静默优先(`config.verbosity='final_only'`,默认)**:IM 会话只推确认接收、审批/交互卡片与**最终结果**,中间进度(工具调用流水、阶段性日志)**默认不出站**——避免群聊刷屏,过程可观测性以 Mesh 站内执行详情为真源(README §6.13:站内永远是通知真源);`verbosity='progress'` 时进度通知一并推送(通知分级与去噪仍按 README §6.13 唯一优先级矩阵,本模块不另行定义)。

**平台官方出站约束(一致性评审穷举,写死)**:
- **限流以错误码呈现,无官方公开每分钟数值**:机器人消息 API 的限流经响应错误码 + `flowControlledStaffIdList`(被限流的接收人)返回(`send.too.fast`/`too.many.group`/`too.many.people`/`send.byToken.tooFast`);出站适配器**按错误码分类退避**(命中限流码 → 指数退避 + 对 `flowControlledStaffIdList` 中的接收人延迟重试,不整体失败),与 HTTP 429 同策略;Spec **不写死具体限额数值**。
- **平台通用限额(自托管运维知悉项)**:标准版单应用单 API 约 20 QPS、全部内部应用合计约 1 万次/月、单 IP >1 万次/20s 封禁 5 分钟(专业/专属版分级不同);大规模部署的运维容量规划参考,非本模块运行时参数。
- **`groupMessages/send` 载荷约束**:`msgParam` **≤ 15000 字节**、**不支持 @ 提及**(UX 约束:ack/结果文案设计不得依赖 @ 发起人,需引起注意时以文案直呼展示名替代)。
- **超长结果行为(写死)**:结果文本超 15000 字节 → **markdown 分段发送**(按段落/代码块边界切分,每段 < 15000 字节;分段幂等键 `sha256(notification_id | 'chunk' | i)` 登记 README §6.5,at-least-once 下重复出队不重复发段);**分段数超 `MESH_IM_MAX_CHUNKS`(默认 5)→ 其后内容截断 + 末段附站内执行详情深链**("完整结果见 Mesh:<URL>")。

**SSRF 与 URL 约束**:钉钉出站目标固定为平台官方域(`api.dingtalk.com`/`oapi.dingtalk.com`),不接受用户可控出站地址(README §6.16);入站载荷中的 `sessionWebhook` 不作为出站目标使用(§3.2 备注)。

**外部联系人单聊出站能力限制(写死的降级)**:`oToMessages/batchSend` 仅支持 `senderStaffId`(企业内部成员);**无 staffId 的外部联系人单聊路径,ack/命令反馈/主动推送不保证送达**——出站适配器检测目标用户键为外部联系人编码(规范化为 `x=<base64url(senderId)>`,见下)→ 投递记 `failed(reason='no_staff_id')` + 告警,**摄取与执行不受影响**(任务照常运行,结果在 Mesh 站内可查);群聊路径不受此限(群消息以 `conversationId` 投递,不依赖发起人 staffId)。

**外部用户键编码(无歧义 + 结构不相交,写死;N-1 订正 + E-1 闭合)**:规范化 `external_user_key`(用于 `external_identities`、`sender_identity_key` 三元组第三段、出站 `userIds`)——**企业内部成员 = `senderStaffId` 原值直通**(字符集钉死最宽官方口径 `[A-Za-z0-9._-]`,如 `014728255240768602`;**单一事实源,§2.10 同文**);**外部联系人(无 staffId)= `x=<base64url(senderId 原值字节)>`**——钉钉 `senderId` 实为 `$:LWCP_v1:$6GYsn+zr…` 加密串(**含 `:`/`$`/`+`,若取原值会复现三元组分隔符坍缩歧义**),故一律以 **base64url(字母表 `A-Za-z0-9_-`,无填充)重编码**消除冒号。**键空间不相交的结构证明(E-1)**:编码键第 2 字符恒为 `=`(前缀 `x=`);`=` **不在 staffId 至宽字符集 `[A-Za-z0-9._-]` 的任何官方口径内**,且 base64url 无填充编码值自身不含 `=` → **任何合法 staffId 都不可能等于任何编码键**,不相交由字符集代数保证(不依赖钉钉文档版本、不依赖「staffId 纯数字」假设;攻击者即使拥有钉钉企业管理员权创建自定义 userid,其 userid 仍受该字符集约束,无法构造 `x=…` 形 userid 冒领外部联系人身份);解码映射仅在身份解析服务内持有(外部键为不透明标识)。单聊出站时外部联系人键反解 staffId 不可得 → 走 §3.10 `no_staff_id` 降级(编码不影响该结论)。

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
   └── 钉钉概览追加:接收模式(Stream/HTTP 只读标识 + Stream 连接状态卡:状态点、最近心跳、[测试发送](出站)/[诊断接收](读 stream-status)两个分离动作;HTTP 模式显示回调 URL + [复制])
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
- **钉钉连接状态卡(MES-82)**:Stream 模式集成概览顶部 —— 状态点(connected/reconnecting/down)+ 最近心跳相对时间("12 秒前")+ 接收模式徽章;`down` 时横幅"Stream 长连接断开,自动重连中(退避 Ns),期间消息将由钉钉侧暂存并重推"。**[测试发送] 与 [诊断接收] 分离两个动作**:[测试发送] 经 OpenAPI 出站(不依赖接收信道,失败提示 `upstream_error`/凭据问题,不误导为连接故障);[诊断接收] 读 `stream-status`(接收信道态)。HTTP 模式展示只读回调 URL + [复制],无连接状态([测试发送] 同样可用)。
- **消息队列面板(MES-82)**:会话分组卡;在途项显示状态徽章(processing 蓝/cancelling 黄"停止中")、`message_excerpt`(截断净化摘要;**全文仅 admin 或项发起人经事件台账展开,默认 tooltip 不向普通成员展示**)、`target_agent`(入队快照解析:名称 + AI 徽章)、运行时长与执行详情深链;排队项显示位置徽章("第 2 位")、发起人(展示名解析,未映射显示外部昵称 + "未连接"标记)、入队相对时间;[取消] 按钮(本人/manage 可见,非 pending 禁用 + 提示"在途项请用 /stop");空态"没有排队消息 —— 在 IM 里 @<agent> 即可派活";**`integration.queue_updated` 失效通知 → 按会话 refetch(不本地 patch 位置)**;project 级绑定项按项目可见性过滤。

### 4.3 关键交互流程

**流程 A:连接飞书并绑定值班群**:集成页 → 飞书卡 [连接] → OAuth 授权回跳成功 → 集成详情 → 绑定 tab → [+ 新绑定] → 选"研发值班群"(external_ref)→ 作用域选 INFRA 项目 → 匹配规则勾"@指定 agent"+ 选值班 agent → 保存。群里 @值班 agent → 入站摄取(签名/去重/审计)→ 触发运行 → agent 回评到 issue(IM 消息按不可信数据隔离入上下文)。

**流程 B:审批卡片在 IM 内闭环**:运行命中 `confirm_required` → `approvals` 建审批(README §6.10)→ 出站适配器向绑定 IM 频道推审批卡片(动作/权限/影响/成本/批准按钮)→ 审批人在飞书/Slack 卡片点"批准" → 卡片回调 → **服务端提取点击者外部身份(飞书 `open_id`/Slack `user_id`)→ 经 `external_identities`(`(provider, provider_tenant_key, external_user_key)` 全局身份键)映射到全局 `users.id` → 由接收回调的集成实例解析所属 workspace,JOIN 该 workspace 的 `members(workspace_id, user_id)` 得名册行 → 按 README §6.10 权限行校验(未映射/该用户在此工作区无名册行/无权限 → 403 拒绝,审批状态不变,审计留痕)** → 校验通过 → 转发 `POST /approvals/{id}/approve` → 运行从审批点续跑;台账记 `notification_delivery(channel='im')` 与 approvals `decision_comment`。**同一已认证外部账号可在其所属的多个 Mesh 工作区各自闭环审批(R4:映射全局 users.id,各工作区独立解析名册行与权限)**。

**流程 C:GitHub PR 合并自动流转**:绑定 GitHub 仓库到 WEB 项目(`auto_status_map={"merged":"done"}`)→ 开发者 PR 标题含 `WEB-123` → PR 合并事件入站 → 签名/去重 → identifier 解析关联 `WEB-123` → 自动置 done + 发评论"PR #N 已合并,自动置为 done" → issue 侧栏显示关联 PR 与流转标记。

**流程 D:开发者订阅出向 Webhook**:出向订阅页 → [+ 新订阅] → 填 https URL(非 https 即拒)+ 勾事件类型(`issue.updated` 等)→ 创建后**仅显示一次**签名密钥(提示妥善保存)→ Mesh 事件经 outbox 投递(HMAC 签名 + 重试退避)→ 投递历史可查;连续失败熔断后 [恢复]。

**流程 E:钉钉群内派活全链路(MES-82)**:集成页 → 钉钉卡 [连接] → 填企业内部应用 `app_key`/`app_secret`(密文存储)+ 选接收模式(默认 Stream,免公网地址)→ 保存后 Stream worker 建连(概览状态点转绿)→ 绑定 tab → [+ 新绑定] → 选"研发群"(`conversationId`)+ 目标 agent → 保存。群里 @机器人发"帮我查下昨晚的报警" → **秒级收到 `✅ 已接收,处理中` 回执**(emoji 确认,leading edge)→ agent 执行,**默认(verbosity='final_only')仅推最终结果与审批卡片到群,中间进度只在 Mesh 站内执行详情可见**(开启 `verbosity='progress'` 后才推进度)→ 5s 内连发两条新任务 → 首条回执后两条被合并抑制(审计记 `ack_merged_into`,队列面板显示"第 2/3 位")→ **自动排队串行执行**,群里再发 `/btw 重点看 payment 服务` → 机器人回"已补充给正在处理的任务(将在下一步生效)"(补充作为不可信上下文注入,不打断,受每执行追加上限)→ 发现派错发 `/stop` → 机器人**先回"⏳ 正在停止…"**(执行优雅停止、仍占会话 lane),终态后**再回"🛑 已停止任务…"**,本人排队消息一并取消(他人任务不受影响);全程事件台账与队列面板实时可查。

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

**钉钉互动卡片交互生命周期(MES-82,写死)**:钉钉审批/交互卡片经互动卡片模板投放(§3.10),点击后的状态流转全部以**卡片更新**(钉钉卡片更新 API,更新幂等键 = `approval_id`)呈现,按钮状态与审批真源一致:

| 点击情形 | 卡片即时反馈 | 回调处理后卡片终态 |
|----------|--------------|--------------------|
| 点击 [批准]/[拒绝](鉴权通过) | 按钮置 loading("处理中…",经卡片私有数据按点击者呈现) | 更新为终态文本("✅ 已批准 · <处理人> · <时间>"/"❌ 已拒绝…")+ **禁用全部按钮**(防重复点击;重复回调本身幂等,README §6.10) |
| 重复点击(审批已决) | — | 卡片保持终态文本(no-op,不回错误打扰) |
| 点击者未映射外部身份 / 无名册行 / 无权限 | loading | 更新为"⚠️ 无权限处理此审批"(不泄露审批详情)+ 附"在 Mesh 站内连接账号 / 联系管理员"引导;审批状态不变(403 留痕) |
| 审批已过期 | loading | 更新为"⏰ 审批已过期" + **[回 Mesh 处理] 深链**(站内审批 URL 兜底) |
| 回调转发 approve/reject 失败(站内异常) | loading | 更新为"处理失败" + **[回 Mesh 处理] 深链兜底** + 告警;审批状态不变 |

> [回 Mesh 处理] 兜底链接指向工作区站内审批详情(README §6.13 站内为真源):IM 卡片任何异常态均有人工闭环出口,不产生"点不动、无处去"的死路。飞书/Slack 卡片同此生命周期语义(各平台以自身卡片更新机制实现)。

> **钉钉卡片更新协议要点(card_1.0)**:状态流转经卡片更新接口以 `outTrackId`(= `approval_id` 派生)幂等更新;点击瞬间的 loading 经回调**响应体** `userPrivateData`(仅点击者可见)回写,终态文案经 `cardData` 公共更新并禁用按钮;**若未来采用 AI 流式卡片**(长结果流式呈现,非本期审批卡片路径),须遵守官方约束:markdown `isFull=true` 全量更新、单帧 ≤1KB / 总量 ≤3KB、`guid` 幂等,且**终态以 `UpdateCard(flowStatus=FINISHED/FAILED)` 显式收尾**(不仅依赖流式 `isFinalize`)。

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
- [ ] **HTTP 回调签名校验(可执行表述,M1)**:构造 `timestamp` + `sign = Base64(HMAC_SHA256(app_secret, timestamp + "\n" + app_secret))` 合法签名的钉钉回调 → 200 摄取;**签名机制能拒绝的场景(断言这些)**:① 错误 secret(签名重算不匹配)② 缺 `sign` 头(`signature_status='missing'`)③ `timestamp` 超窗(下条)④ **路由字段篡改导致集成定位错配**(`chatbotCorpId` 改为其他值 → 定位到另一集成或无集成 → 密钥不匹配/无法定位 → 401);被拒一律落 `rejected`、**不派发、不 ack**。**明确断言边界**:钉钉签名串仅覆盖 `timestamp + "\n" + app_secret`,**不覆盖 body**——body 字段级完整性由 HTTPS/TLS 传输层保证,测试**不**以"篡改 body 字段而 sign/timestamp 合法"为拒绝用例(该组合在真实 TLS 信道不可达;按此写测试会诱发非标校验拒掉合法回调)。恒定时间比较以**实现断言验收**(`hmac.compare_digest`/等价恒定时间原语 + 代码审查),不做 CI 时序测量。
- [ ] **时间戳防重放(钉钉官方容差 ±3600s)**:签名合法但 `timestamp` 超出当前 ±3600s → 401 拒绝(重放防护);边界内(如 59 分钟前)放行。
- [ ] **Stream 长连接摄取(经可控测试注入门)**:Stream worker 的网关基址经 `MESH_DINGTALK_GATEWAY_BASE` 配置——**写死约束(M2):该值仅为部署期环境变量,不进入任何运行期配置(`integrations.config`)、不经任何管理 API 可读写(防 admin 可编辑 → 指向受信任对端 → Stream MITM 窃取内存中 app_secret 的提权路径);生产环境检测到非默认值(默认钉钉官方域)→ 启动即告警 + 写审计日志**;e2e 经该环境变量指向本地钉钉网关测试替身(实现 `connections/open` + WSS 帧协议的最小 fake,可注入帧/切断连接/模拟重推);`receive_mode='stream'` 集成 → worker 经 `connections/open`(app_key/app_secret)建连并接收 `/v1.0/im/bot/messages/get` 帧 → 与 HTTP 模式同一摄取服务函数落库(`payload._mesh_channel='stream'`,`signature_status='valid'`);每帧回 ACK;**未 ACK 帧重推 → `msgId` 去重幂等(200 `deduped`,不重复入队/不重复 ack)**;仅 `wss://` endpoint 被接受(非 wss 拒连 + 告警),TLS 证书强制校验。
- [ ] **Stream 凭据错误即全拒**:app_secret 错误的集成 → `connections/open` 失败,不建连、零摄取(等价"签名一律无效");集成概览连接状态 `down` + 告警。
- [ ] **断线重连**:kill Stream 连接 → 指数退避(2s→300s,±20% 抖动)重连成功;重连期间钉钉侧暂存的未 ACK 消息重推后正常摄取,**无丢失**;重连过程经 `integration.updated(subject='stream_channel')` 实时反映(connected→reconnecting→connected)。
- [ ] **单实例互斥**:两个 worker 进程同时启动 → 同一集成仅一个建立 Stream 连接(advisory lock);集成测试模拟互斥失效双摄取 → 去重键兜底,无重复执行。
- [ ] **凭据轮换即时生效**:`rotate-secret` 后 Stream 断连并以新 app_secret 重连成功;旧凭据立即不可用;轮换过程与令牌值不回显响应/日志(README §6.16)。

**规范化与绑定**:
- [ ] **三元组归一**:入站载荷 `chatbotCorpId` → `provider_tenant_key=corp_id`、`conversationId` → `external_ref`、`senderStaffId` → 发起人键(无 staffId 的外部联系人归一 `x=<base64url(senderId)>`,§3.10);`conversationType` `"1"`/`"2"` 归一单聊/群聊并决定出站通道(oToMessages/groupMessages)。
- [ ] **真实平台 ID 注入(N-1)**:以官方报文样例真实值注入——`conversationId='cid6EUvB2O8qVF2RYQtHTKEsg=='`(含 `=`)、`senderId='$:LWCP_v1:$6GYsn+zrv5WZ77xc2v4zsyXfBv1MhAv9'`(含 `:`/`$`/`+`)→ **入队成功**(不被字符集校验拒绝)、`conversation_key` 第三段含 `=` 合法存储;外部联系人身份段 = `x=<base64url(senderId)>` **不含 `:`**;**负向碰撞**:构造两个不同 `senderId`(编码前缀相同的子串情形)→ 编码后键不相等、`external_identities` 三元组唯一解析无坍缩;`senderId` 原值(含冒号)作为身份段 → 服务层拒绝(`invalid_request`,防坍缩歧义)。
- [ ] **键空间结构不相交(E-1)**:staffId 形用户键(匹配至宽口径 `^[A-Za-z0-9._-]+$`)与编码键(`x=<base64url>`)**结构不可碰撞**——断言编码键第 2 字符恒为 `=`、`=` 不匹配 staffId 字符类(字符集代数,T39-15 PG 实测);**攻击链负向**:以某外部联系人编码键串作为 `external_account_ref` 走 `:link`(伪造成 staffId)→ 服务层识别 `x=` 前缀非合法 staffId(不匹配 `[A-Za-z0-9._-]+`)→ 422 拒绝(link 流 staffId 字符集校验守卫),冒领不成立;同 corp 下 staffId 形键行与编码键行在 `external_identities` 各映射独立三元组、互不解析到同一 `users.id`。
- [ ] **全局唯一绑定(R3 键含钉钉)**:`UNIQUE(provider='dingtalk', provider_tenant_key, external_ref)` 下,两个工作区抢绑同一钉钉群 → 第二者 409 `binding_conflict`;同群单聊会话同口径占位。

**emoji 确认接收(§3.8)**:
- [ ] **即时确认(leading edge)**:绑定会话内 @机器人发任务消息 → 摄取成功后 **<2s 先收到 `✅ 已接收` 确认消息,后才收到执行结果**(确认发送先于 agent 任何出站动作;集成测试断言 ack 时间戳 < 首个结果消息时间戳);确认文案携带发送时刻 best-effort 位置(串行排队时"第 N 位"对冲式措辞)。
- [ ] **仅 dispatched 触发**:重复事件(deduped)/ 未绑定会话 / 命令消息 / 签名被拒消息 → **不发确认消息**(出站台账无对应行)。
- [ ] **leading-edge 合并(五字段语义)**:同会话窗口(`MESH_IM_ACK_COALESCE_WINDOW`,**以 leader `ack_window_at`(锁序时间)起算 5s**)内连发 3 条 → **仅 leader 收到确认消息**(leader: `ack_leader_id` 自指 + `ack_attempted_at` + `ack_sent_at` 置位);后 2 条为 follower(`ack_leader_id` 指向 leader、`ack_sent_at` 保持 NULL,leader T2 后 `ack_represented_at`/`ack_merged_into` 回写;**follower 无 im.send outbox 事件**,被代表 ≠ 已发送);**不发"共 N 条"尾部消息**。
- [ ] **ack 并发闭合①(到达顺序无关)**:**强制 M2(seq=2)先于 M1(seq=1)进入 relay 处理**(测试以入队节拍控制)→ leader 仍为 M1(摄取事务按 seq 先定),平台 mock **恰好一次外呼**;M2 为 follower(断言 `ack_leader_id` 指向 M1)。
- [ ] **ack 并发闭合③(锁序时间,事务先开始但后取锁)**:T2 先开事务暂停、T1 后开事务却先取 `imq_seq` 锁写入 seq=1 → T2 随后取锁写入 seq=2(其 `enqueued_at` < seq=1 项的 `enqueued_at`);断言 **窗口判定按 `ack_window_at`(持锁后 `clock_timestamp()`)而非 `enqueued_at`** → T2 项落入 seq=1 项窗口成为 follower(不成第二个 leader),平台恰好一条确认(T39-16 以显式时间值模拟该倒序)。
- [ ] **ack_template='' 不占窗口**:关闭确认的集成连发多条 → 无 im.send 事件、`ack_leader_id` 全 NULL、不产生任何窗口占位;其后同会话(同集成)无 leader/follower 结构残留。
- [ ] **ack 并发闭合②(T1 后停驻无歧义)**:W1 领取 leader 事件、停在 T1 提交后(事件已 published、`ack_attempted_at` 置位、外呼未发)→ W2 轮询同一事件 → **不可领取(published 不在 SKIP LOCKED 候选)**,不产生 `ack_lost` + `ack_sent` 并存审计;W1 继续外呼成功后最终态 = attempted ∧ sent ∧ 窗口 follower represented,平台恰好一条。
- [ ] **at-most-once 崩溃点**:T1 提交后、外呼前杀 relay → 事件不被重领、ack 丢失(断言 attempted ∧ ¬sent ∧ published,无重试);T1 提交前杀 → 事件重领、正常外呼一次。
- [ ] **关闭与失败**:`ack_template` 置空的集成不发确认;模拟平台 5xx → 不重试、仅审计告警,执行正常进行。

**`/stop` 与 `/btw`(§3.7)**:
- [ ] **`/stop` 两阶段取消**:会话内派任务 → 执行 running 时发 `/stop` → 队列项**先转 `cancelling`(继续占串行 lane)**+ durable outbox 取消命令下发,机器人**即时回"⏳ 正在停止…"**;执行优雅停止后 `execution.finished(cancelled)` → 项转 `cancelled`(`failure_reason='cancelled_by_command'`),机器人**再回"🛑 已停止任务…"**;断言 cancelling 期间**下一排队项未被派发**(lane 未提前释放)。重复 `/stop`(项 cancelling 中)→ 幂等回"任务正在停止中";执行已终态 → 回"当前没有进行中的任务"。
- [ ] **`/stop` 连同排队项取消**:串行模式下发起人排队了 2 条 pending → `/stop` 后在途执行进入 cancelling + 2 条 pending 即时 `cancelled`(按 seq 序),即时段反馈含取消条数。
- [ ] **多人同群各自取消**:同群用户 A 的任务 processing、用户 B 有 1 条 pending → B 发 `/stop` → **B 的 pending 被取消,A 的 processing 不受影响**(不因"无权停 A"整体拒绝),反馈文案区分("已取消你的 1 条排队消息;当前进行中的任务不属于你")。
- [ ] **`/stop` 授权负向**:用户 B(已映射身份、无 manage 权限)仅对 A 的在途任务发 `/stop`(B 无排队项)→ 拒绝(回 command_forbidden 语义文本)+ 审计,**A 的执行不受影响、详情不泄露**;未映射身份发 `/stop` → 回建链提示,零副作用;有 `execution:manage` 权限成员发 `/stop` → 放行(可停他人任务)。
- [ ] **`/stop` 取消意图不丢失**:取消请求经 runtime 取消服务落库(执行 `cancelling` + `cancel_requested_at`)后使 daemon 离线 → 项保持 cancelling(不放行下一项),daemon 恢复后经心跳下行停止执行、`execution.finished(cancelled)` 转 cancelled(不产生"项已取消、执行仍跑"撕裂;取消服务调用本身为同事务 DB 写入,无事务外网络丢失窗口)。
- [ ] **`/btw` 注入在途执行**:执行 running 时发 `/btw 用 staging 环境` → 机器人回"已补充…",执行上下文中出现该补充(结构化隔离标记 `source='im_btw'`,**作为数据而非指令**:执行不因补充文本中的"指令性措辞"改变高危行为,README §6.15);执行不打断、不新建;**下一 agent turn 边界生效**(断言当前 turn 不被打断、补充块出现在后续 turn)。
- [ ] **`/btw` 追加上限(M3)**:对同一执行连发 `/btw` 超 `MESH_CONTEXT_APPEND_MAX_COUNT`(默认 20)或累计超 `MESH_CONTEXT_APPEND_MAX_CHARS`(默认 32000)→ 超限起**不写入** `execution_context_appends` + 机器人回"补充已达上限…" + 审计;限额内的补充照常注入。
- [ ] **`/btw` cancelling 拒绝与无在途降级**:项为 cancelling 时发 `/btw` → 回"任务正在停止,无法补充";会话无 processing/cancelling 项时发 `/btw 查下日志` → 回提示"…已按新消息排队" + 剥前缀文本按普通消息入队执行。
- [ ] **命令不入队/不触发**:`/stop`/`/btw`/`/help`/未知 `/xxx` 消息 → `integration_message_queue` 无对应行;未知命令回帮助文本;消息正文中间的 "/stop" 不解析为命令(按普通任务消息处理)。
- [ ] **钉钉 @前缀归一**:钉钉群消息 `text.content` 含前导空格与 @机器人 前缀 → trim 后正确解析命令(不出现" /stop"识别失败)。

**自动排队(§3.9 / §2.10)**:
- [ ] **串行按序 + 状态机**:串行集成会话内快速连发 M1/M2/M3 → 队列 seq=1/2/3,状态流转 `pending → dispatching → processing → done`(DDL CHECK 词汇断言);M1 执行期间 M2/M3 保持 pending;**执行顺序严格 M1→M2→M3**(断言 started_at 序与执行创建序一致),无并发(任意时刻该会话 serial 在途项 ≤ 1)。
- [ ] **数据库级并发保证**:`uq_imq_conversation_active` 部分唯一索引(`state IN ('dispatching','processing','cancelling') AND dispatch_mode='serial_conversation'`)生效——并发派发器争抢同会话 → 至多一个成功,其余唯一约束冲突回退(information_schema/pg_indexes 结构断言 + 并发注入测试);cancelling 项占位期间派发下一项被索引拒绝。
- [ ] **不丢失(崩溃恢复)**:M1 processing 时杀派发器/进程 → 重启后租约修复:M1 执行已终态则按 `execution.finished` 补回写;执行丢失则经 outbox rearm 重新派发;**M2/M3 继续按序执行**,队列最终无悬挂 pending(超租约阈值后断言)。
- [ ] **rearm 并发消费竞态(R4-3/R5-2)**:同一队列项,原事件(行级键 K)与派生 rearm 事件(行级键 K2、**payload `idempotency_key=K` + `queue_item_id`**)**并发、任意顺序被两个消费者处理** → **最终恰好一个 `task_executions` 行(`idempotency_key=K`)、队列项 `execution_id` 只绑定一次**(第二个消费者:执行键唯一冲突或队列项守卫 0 行 → 整事务回滚,无孤儿 execution;T39-17 以单事务模拟两消费顺序的约束结果,真实双消费者交错经服务层并发测试覆盖)。
- [ ] **全局 enqueue 契约零回归(R5-2)**:**assign/mention/autopilot/chat 四类触发的消费契约不变**——仍读 `payload.idempotency_key`(mention 路径保留事件级键 fallback)、无 `queue_item_id` 要求、无队列项守卫;仅 `trigger='integration'` 走附加契约(实现路径断言:消费者分支仅按 trigger 分流,非 integration 路径不引用 `integration_message_queue`)。
- [ ] **位置查询与契约**:`GET .../integrations/{id}/queue` 返回各会话项与 `position`(M3 在 M1 处理、M2 排队时 position=2)+ `target_agent` + `message_excerpt`(≤120 字符、无控制符;**全文不在响应中**)+ `state`(含 dispatching/cancelling);`:cancel` 取消 M2(本人)→ refetch 后 M3 position 变 1;非 pending 项取消 → 422 `queue_item_not_cancellable`;他人 pending 项由无 manage 权限者取消 → 403;**project 级绑定项对无该 project 可见性的成员不返回**。
- [ ] **parallel 模式基线**:飞书/Slack 默认 `parallel` → 连发消息各自即时派发(不等前序终态,**同会话可并发**——parallel 项不受独占索引约束),§6.9 原触发语义不变;**排空-再切换**:serial 下积压 2 条 pending 时集成切 `parallel` → 新消息入队有效模式仍为 serial(会话有非终态 serial 项),由派发器依序清空,**清空后**新消息方按 parallel 即时派发(断言切换点前后入队项的 `dispatch_mode` 快照值);反向 serial 切换后派发器等待会话内 parallel 在途项终态再派发(跨模式不重叠)。
- [ ] **实时(失效通知 + project 隔离负向)**:入队/派发/终态/取消/合并抑制均推 `integration.queue_updated`(README §6.7 注册表已登记),失效顺序以 envelope 频道 `seq` 为准(无自维护 revision)——断言客户端 refetch 而非本地 patch;**私有 project 级绑定的队列项变更:无该项目可见性的成员收到的 WS 帧 payload 不含 `conversation_key`(仅 integration_id + scope),且其 refetch `.../queue` 结果不含该项目项**(project 成员则可见;跨项目隔离负向验收)。

**出站与推送(§3.10)**:
- [ ] **accessToken 多副本 + 锁所有权**:**两个 mesh.workers 副本并发触发同集成令牌刷新 → 钉钉 accessToken 端点恰好被调用一次**(出站请求计数断言;随机 owner token 锁 + 双检);**租约过期接管 + 旧 owner 迟到释放**:模拟持锁副本刷新超时(>30s 租约)→ 第二副本接管刷新成功 → 旧副本完成后执行释放 → **Lua owner 比对拒绝误删新锁**(断言锁值仍为新 owner 或已正确释放、平台端点仍仅两次内且最终持锁者为新副本);**follower 等待覆盖刷新窗口**:mock leader 刷新耗时 8s + 3 个 follower 并发出站 → **follower 全部在 ≤~8.5s 读到新令牌、零投递失败**(busy 不为终态)、accessToken 端点恰被调用一次;等待窗口耗尽场景(刷新 >12s)→ 出站结果 `token_refresh_busy` **仅后移 `available_at`、`delivery_attempts` 不变**:**连续 busy 次数超通用 `max_attempts` 仍不终态、不热循环**(`available_at` 短退避节奏可观察),刷新完成后经 `available_at` 到期重试成功(真实 relay 测试:T39-18 以 DDL 字段实测 busy 不耗预算 + 到期可领);刷新请求超时 10s < 租约 30s(实现断言);平台返回令牌失效码 → 作废旧缓存 + 单次强制刷新;TTL 含 ±60s 抖动;**accessToken/appSecret 不回显任何响应与日志**(脱敏断言)。
- [ ] **主动推送(verbosity 语义)**:默认 `verbosity='final_only'` → IM 会话仅收到确认接收、审批/交互卡片与**最终结果**通知(中间进度通知不出站,`notification_delivery` 无 progress 类台账行);`verbosity='progress'` → 进度通知一并推送;两种模式站内执行详情均完整(站内为真源);投递经 `notification_delivery(channel='im', provider='dingtalk')`(群走 groupMessages、单聊走 oToMessages);平台限流(429)→ 退避重试;台账可查。
- [ ] **互动卡片回调鉴权(同 §5.2 卡片链)**:钉钉互动卡片按钮回调 → 点击者 `userId` + corp_id → `external_identities` → `users.id` → 集成解析 workspace → JOIN members → §6.10 权限校验;未映射/无名册行点击批准 → 403,审批状态不变,留痕;已映射有权限者点击 → 转发 approve/reject,重复点击幂等。
- [ ] **钉钉卡片交互全生命周期(§4.4)**:点击 → 按钮即时 loading;成功 → 卡片更新终态文本 + **按钮禁用**;重复点击 → no-op 终态保持;审批过期 → "已过期" + [回 Mesh 处理] 深链;回调转发失败 → "处理失败" + 深链兜底 + 告警;无权/未映射 → "无权限" + 引导(不泄露详情);断言各态卡片更新幂等(同一 approval_id 更新不冲突)。
- [ ] **测试出站与接收诊断分离**:`POST .../test-send` 在 Stream 接收信道 down 时**仍成功**(出站不依赖接收信道;失败仅 `upstream_error`/凭据问题);`GET .../stream-status` 独立报告接收信道态;`503 stream_channel_unavailable` 不出现在 test-send 响应。
- [ ] **非文本入站消息矩阵(C-1)**:群聊 @机器人发 audio/video/file → 平台不投递(无 `integration_events` 行,非模块过滤);单聊发 picture/richText → 事件落库 `processed` + **不创建执行、不入队列、不 ack**(审计载荷含 msgtype);单聊 text → 正常触发;`messageFiles/download` 媒体下载路径不存在于代码(非目标断言)。
- [ ] **出站平台约束(C-2)**:模拟 `send.too.fast` 错误码 → 出站适配器指数退避重试(不整体失败,台账记限流码);结果文本 >15000 字节 → 按边界分段发送(各段幂等键 `sha256(notification_id|'chunk'|i)`,重复出队不重发段);超 `MESH_IM_MAX_CHUNKS`(默认 5)→ 截断 + 末段含站内深链;出站文案断言不含 @ 提及(平台不支持)。
- [ ] **卡片 API 面(C-3)**:审批卡片投放经 `createAndDeliver`(断言请求含 `cardTemplateId`/`outTrackId`=approval 派生/`openSpaceId` 群或单聊格式/`callbackType`);**审批卡片路径不出现 `sampleActionCard6`/传统 ActionCard**(代码路径断言);更新经 `PUT /v1.0/card/instances`,outTrackId 幂等(重复更新不冲突)。
- [ ] **Stream 系统帧(C-4)**:测试替身注入 `type='SYSTEM', topic='ping'` 帧 → worker 回 ACK(code=200 + 原 headers + 原 payload data);注入 `topic='disconnect'` → worker 主动断连并重走 connections/open;90s 无帧 → Mesh 侧探活重连(与平台 ping 并列)。
- [ ] **真实钉钉联调(验收阶段)**:真实企业内部应用机器人(测试企业)端到端 —— Stream 建连、群内 @触发 + ✅ 确认(leading edge)、/stop 两阶段、/btw、排队、卡片点击闭环、最终结果推送全部真实验证(非 mock);联调存证(截图/日志)附验收评论。**观察项(C-5)**:联调时实测 `/v1.0/robot/emotion/reply` 实际可用性并记录(可用亦不改本期设计——无官方公开文档/无 SLA,官方转正后再评估)。

**平台硬化与横切(评审收口)**:
- [ ] **入站频率护栏(§2.10)**:单身份超 20 条/分钟(`MESH_IM_INBOUND_PER_IDENTITY_PER_MIN`)或单会话超 60 条/分钟或会话 pending 深度超 50 → 超限消息**不入队、不创建执行、不 ack**,落 `rejected` + `_mesh_reject_reason='rate_limited'` + 一次性机器人提示(同会话提示 ≤1 次/分钟)+ 告警;HTTP 模式超限仍返回 200(不触发平台重推)。
- [ ] **身份三元组授权负向(跨 provider 越权)**:构造 GitHub login 与钉钉 staffId 同名(如 `foo`)映射到两个不同 Mesh 用户;GitHub 用户经 `:cancel`/`/stop` 指向同名钉钉队列项 → **拒绝(403/拒绝文本),目标项与执行不受影响**(断言 `sender_identity_key` 全三元组解析,裸键解析路径不存在)。
- [ ] **入站文本长度上限**:超 `MESH_IM_INBOUND_TEXT_MAX_CHARS`(默认 4000)的消息正文/`/btw` 参数 → 截断 + `payload.truncated=true` 审计;执行上下文中的截断标记可见。
- [ ] **执行关联回写(双模式、dispatching 单一转换)**:parallel 与 serial 模式下队列项均经 `dispatching → processing` 转换绑定 `execution_id`(relay 建执行同事务,经 `trigger_event_id` 反查),队列面板"处理中项 → 执行深链"在两种模式下均可点;state 守卫保证无 `pending → processing` 跳变(注入测试:伪造跳过 dispatching 的 UPDATE 被拒/0 行)。
- [ ] **租约修复五分支 + outbox rearm**:制造 ① 终态事件丢失(按 `execution.finished` 补回写)② 长任务超租约仍在跑(续租不误杀)③ queued 未超 max_stuck(续租等待)④ queued 超 `max_stuck_seconds`(置 failed + 告警不重派)⑤ 执行不存在(**outbox rearm 四态闭合,按真实 `outbox_events` DDL**:a 原事件 `pending` → 不造新事件、续租等待现有 relay(超 `MESH_OUTBOX_CONSUME_SLA` 升级走 d);b `failed` → 条件更新 `status='pending', delivery_attempts=0, published_at=NULL` 后 relay 重建执行;c 异常 `published` → **原行保留不 DELETE**(§6.6 审计保留)+ 派生 rearm 键新写;d 行缺失 → 派生键新写)五种孤儿场景 → 各分支行为如 §3.9,扫描不空转,无消息静默丢失、无重复执行;**validation T39-9/T39-14 对真实 outbox DDL 逐态实测(非文字断言)**。
- [ ] **终态回写单一驱动**:队列项终态全部由内部事件 `execution.finished`(payload.status)驱动——断言 relay 不直接消费实时事件 `execution.completed/…` 作回写源(代码路径断言 + cancelling 项收到 finished(cancelled) 转 cancelled)。
- [ ] **删除保护成功路径闭合**:① 绑定存在 pending/processing 项且无 force → `409 binding_has_active_queue`;② **`?force=cancel` 后删除实际完成**:强制终止全部项 → DELETE 绑定**成功返回**(FK SET NULL 触发,无阻塞),队列项保留为 `binding_id IS NULL` 的终态孤儿审计行(`binding_display` 快照完整、经审计端点可查),保留期后物理清理;③ **fail-closed 负向**:非终态项存在时绕过端点直接 DELETE 父行 → `ck_imq_orphan_terminal` CHECK 拒绝(整个 DELETE 回滚,绑定行仍在、项父引用未置空);④ project 物理删除经同一强制终止路径(未先清理 → 项目删除被 CHECK 拒绝);⑤ **孤儿项端点隔离负向**:普通 `.../queue` 与 `.../queue/summary` 对任何成员**不返回** `binding_id IS NULL` 的孤儿项;**已删私有 project 的终态孤儿项:非 admin 成员经 audit 端点不可见,且绝不以 workspace 项形态经普通端点返回**;admin/owner 经 audit 端点可见(快照项目已删的兜底)。
- [ ] **`/btw` 运行期注入(服务端持久水位闭合)**:执行 running 时 `/btw` → `execution_context_appends` 落行(source='im_btw');daemon 经心跳 `context_progress[{attempt_id,execution_id,injected_through_seq,lease_seq}]` ACK(真实注入 turn 后推进;**lease_seq 为 claim/renew 后持有的最新 fencing 令牌,R7-1 协议输入**)、收 `inject_context(带 attempt_id, from_seq=服务端水位)` 拉取;断言:① **服务端水位 `task_executions.context_injected_through_seq` 按连续前缀重算,同一 attempt 内单调不回退**(ACK 乱序到达不污染;跨 attempt 由 requeue 重置);② **receipt ACK 闭合反例链(R6-1/R7-1,T39-10 真实 receipt 守卫结果实测——单事务顺序验证 fencing/覆盖守卫逐分支结果;真实 `FOR UPDATE` 锁序与并发交错归服务层集成测试,不描述为已实测竞态)**:**A receipt(seq=4,injected_attempt_id=A,lease_seq 精确匹配,水位=4)→ A reclaimed、执行 requeue(receipt 清空 + 水位重置 0)→ B GET 命中 seq=4 → B ACK(携带 claim 后最新 lease_seq)恰好更新 1 行 → B 水位推进至 4 → B 再 GET 不返回 seq=4 → A 迟到 ACK(过期 lease_seq / 非当前 attempt)经 fencing 为 0 行、不覆盖 B**;**lease_seq 负向:错误/过期 lease_seq 的 ACK 整条 0 行(不写 receipt、不推进水位)**;**审批续跑反例(R7-2,T39-19)**:A 注入并 ACK seq=4 → 执行审批挂起 → 批准续跑(**执行回 queued 同事务清空 receipt + 水位置 0**,与失联 requeue 同一路径)→ B claim 新 attempt → **seq=4 经重新 GET 至少进入 B 续跑上下文一次**(at-least-once,叠加 resume_context)→ B ACK 覆盖为 B,A 迟到 ACK 不得覆盖 B;③ **注入语义为 at-least-once(R4-2 诚实降级)**:seq=4 注入后、记录落库前杀 daemon → 新 attempt 恢复后 seq=4 **至少投递一次**(窄窗口内可能重复);**下游容忍断言**:同一 `(execution_id, seq)` 重复块与单块语义等价(补充数据非指令),执行不因重复补充产生累积副作用/重复工具调用(README §6.15;Spec 不承诺恰好一份——现有契约无每 turn 检查点原语);④ **尽力去重快路径**:心跳 `context_progress` 回写 receipt/水位为 best-effort(上报丢失只扩大重复窗口、不破坏语义);GET 端点 **attempt 作用域过滤(`injected_attempt_id IS NULL OR <> 当前 attempt`)使同一 attempt 已 receipt 行不再下发**(单指针模型:receipt 只保留最新,requeue 清空);⑤ **M3 上限与 seq 取号共用 `eca:` 执行级咨询锁**:N 个并发 `/btw` 写入 → 总数 ≤ `MESH_CONTEXT_APPEND_MAX_COUNT`、累计字符 ≤ `MESH_CONTEXT_APPEND_MAX_CHARS`(并发写不穿上限);⑥ **补充文本中的指令性措辞(如"请删除所有 issue")不改变执行的高危行为**(README §6.15 断言)。
- [ ] **外部联系人单聊降级**:无 staffId 的外部联系人(`x=<base64url(senderId)>` 编码)单聊触发 → 执行正常创建运行,ack/结果推送记 `failed(reason='no_staff_id')` + 告警,不阻塞执行。
- [ ] **出站请求体脱敏**:`connections/open`/`accessToken` 出站失败(模拟 5xx)→ 日志/错误台账仅 `method/url/status`,body 中 `clientSecret`/`appSecret`/`accessToken` 均以 `***` 出现(或不出现);`redact_in_logs` 黑名单含解密后的秘钥值。
- [ ] **Stream 状态持久真源**:杀 Stream worker → `integrations.stream_state` 经 outbox 迁移至 reconnecting/down,`GET .../stream-status` 可读(前端刷新首屏不依赖实时事件);恢复后 connected + `last_frame_at` 刷新。
- [ ] **队列取消 TOCTOU**:`:cancel` 与派发器并发(项刚被派发)→ 原子条件更新 0 行命中 → 422,不产生 cancelled 与 processing 并存态。
- [ ] **两张新表 DDL CI 覆盖(HIGH-6)**:`docs/specs/validation/schema_r2_validation.sql` 含 `integration_message_queue` 与 `execution_context_appends` 的完整 DDL + 行为断言(状态机 CHECK 词汇、`uq_imq_conversation_active` 部分唯一索引(serial 在途独占/parallel 豁免)、RESTRICT FK、seq 唯一、appends 双上限常量);MES-68 rebase 后 model/migration 与本 DDL 逐约束对账(含 `notification_delivery.provider` 代码枚举落 `dingtalk`)。
