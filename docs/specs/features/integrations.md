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

连接器是抽象的**具体实现**:本期落地 **飞书/Lark、Slack**(IM)与 **GitHub/GitLab**(VCS)三类公开集成目标平台,以及**出向 Webhook** 这一通用开发者通道。新增一个连接器 = 实现"签名校验 + 载荷归一 + 出站适配"三个适配点,无需触碰摄取/去重/凭据/投递的通用机制。

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

### 1.3 边界与非目标(明确不做什么)

- **runtime 的 git checkout/push ≠ 产品级 VCS 集成(硬边界)**:agent 运行时经 runtime 协议 checkout 仓库专属分支、推送产物(runtime.md `repo_checkouts`),那是**agent 的执行工具**,服务于单次运行;**本模块的 VCS 连接器是产品级集成**——它把外部 VCS 平台的事件(merge/close/comment/push)持续摄取进 Mesh,驱动 issue 关联与状态流转,与具体某次运行解耦。二者不互相替代:agent 用 runtime git 工具写代码,VCS 连接器把"代码已合并"这一事实回流成 issue 状态。
- **不做集成市场(marketplace)后端**:本期不提供第三方开发者上架连接器、审核、分成的市场后端(YAGNI);连接器由 Mesh 内置,出向 Webhook 是面向开发者的通用订阅通道,不是上架机制。
- **不**定义 agent 执行能力 —— 归 `agent.md`/`runtime.md`(本模块只"摄取事件 + 派单 + 出站投递")。
- **不**定义通知分级/审批实体 —— 归 README §6.13/§6.10(本模块是 IM 出站**通道**与审批**卡片呈现/回调面**,不持有通知/审批真源)。
- **不**做跨 workspace 的全局集成定义:集成与绑定都是工作区级;一个外部身份至多绑定一个工作区(外部侧唯一,§2.3)。
- **不**自定义入站摄取的去重/签名/审计机制 —— 一律复用 autopilot `webhook_events` 范式(autopilot.md §2.5/§3.2),仅替换平台特定的签名算法与载荷归一。

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
                       │             UNIQUE(integration_id, external_ref) 外部侧唯一绑定
                       │
                       └──1:N──► integration_events(入站摄取台账:签名/去重/审计,复用 autopilot 范式)
                                     UNIQUE(integration_id, external_event_id) 去重
                                     ──► 匹配 binding ──► task_executions(trigger='integration',README §6.9)

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
| `kind` | TEXT | NOT NULL,CHECK IN ('im_feishu','im_slack','vcs_github','vcs_gitlab','webhook_outbound') | — | 集成类型(决定连接器适配点) |
| `name` | TEXT | NOT NULL | — | 展示名(工作区内唯一,见唯一索引) |
| `status` | TEXT | NOT NULL,CHECK IN ('active','disabled') | `'active'` | 启用状态;`disabled` 时入站摄取拒绝分发、出站停发 |
| `config` | JSONB | NOT NULL | `'{}'` | **非密**平台配置(app_id、外部租户标识、回调基址、默认卡片模板等;**严禁存任何 secret**,见 §2.7) |
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
| `scope` | TEXT | NOT NULL,CHECK IN ('workspace','project') | `'workspace'` | 绑定作用域 |
| `project_id` | UUID | NULL,**复合 FK `(workspace_id, project_id) → projects(workspace_id, id)` `ON DELETE SET NULL (project_id)`** | NULL | `scope='project'` 时必填(见 CHECK);项目软删除,硬删仅工作区拆解时发生(README §6.2 第 6 条列级 SET NULL) |
| `external_ref` | TEXT | NOT NULL | — | 外部身份标识(IM 群/频道 ID、VCS 仓库全名 `owner/repo`);**同一集成内唯一**(见唯一索引) |
| `match_config` | JSONB | NOT NULL | `'{}'` | 匹配规则(见 §2.6:如 @某 agent 触发、关键词、分支模式、事件类型过滤) |
| `bound_agent_id` | UUID | NULL,**复合 FK `(workspace_id, bound_agent_id) → agents(workspace_id, id)` `ON DELETE SET NULL (bound_agent_id)`** | NULL | 匹配成功后触发的目标 agent;为空时仅审计不触发(README §6.9) |
| `status` | TEXT | NOT NULL,CHECK IN ('active','disabled') | `'active'` | 绑定状态 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**唯一约束**:
- `UNIQUE (integration_id, external_ref)` —— **外部侧唯一绑定**:同一集成内一个外部身份只能绑定一次。由于一个集成隶属唯一工作区、且一个外部平台应用安装对应一个集成,该键与"一个外部身份至多绑定一个工作区"(README §6.17)共同生效:同一外部群/频道/仓库不会被重复绑定。
- `CHECK (scope = 'workspace' OR project_id IS NOT NULL)` —— `project` 作用域必带项目;`workspace` 作用域 `project_id` 为 NULL。项目/agent 均为软删除,该 CHECK 在软删除模型下恒成立(硬删仅发生在工作区拆解,绑定行同时被 `workspace_id` CASCADE 删除)。

### 2.4 表:`integration_events`(入站事件摄取台账:签名 / 去重 / 审计)

> **同构于 autopilot `webhook_events`**(autopilot.md §2.5),相互独立:autopilot 的入站事件落 `webhook_events`,集成平台的入站事件落本表;摄取管线的签名/去重/审计/拒无效语义完全一致。

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
> - IM 绑定:`trigger_on` ∈ {mention, direct_message, keyword};`mention_agents` 限定 @哪些 agent 才触发(未匹配不触发,仅审计,README §6.9)。
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
  kind         TEXT NOT NULL CHECK (kind IN ('im_feishu','im_slack','vcs_github','vcs_gitlab','webhook_outbound')),
  name         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  config       JSONB NOT NULL DEFAULT '{}',
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
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id UUID NOT NULL,
  scope          TEXT NOT NULL DEFAULT 'workspace' CHECK (scope IN ('workspace','project')),
  project_id     UUID NULL,
  external_ref   TEXT NOT NULL,
  match_config   JSONB NOT NULL DEFAULT '{}',
  bound_agent_id UUID NULL,
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_integration_bindings_ws_id UNIQUE (workspace_id, id),                -- 复合 FK 引用前提(§6.2)
  CONSTRAINT uq_binding_external_ref UNIQUE (integration_id, external_ref),          -- 外部侧唯一绑定
  CONSTRAINT ck_binding_scope CHECK (scope = 'workspace' OR project_id IS NOT NULL),
  CONSTRAINT fk_binding_integration FOREIGN KEY (workspace_id, integration_id)
    REFERENCES integrations(workspace_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_binding_project FOREIGN KEY (workspace_id, project_id)
    REFERENCES projects(workspace_id, id) ON DELETE SET NULL (project_id),           -- §6.2 第 6 条列级 SET NULL
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
```

### 2.9 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `integrations.workspace_id` 等 | → `workspaces.id` | workspace.md | 隔离 |
| `integrations.created_by` / `webhook_subscriptions.created_by` | 复合 FK → `members(workspace_id, id)` | member.md | 创建者(人或 agent;判别 JOIN members,README §6.1/§6.2) |
| `integration_bindings.integration_id` / `integration_events.integration_id` / `webhook_subscriptions.integration_id` | 复合 FK → `integrations(workspace_id, id)` | 本模块 | 集成归属(README §6.2) |
| `integration_bindings.project_id` | 复合 FK → `projects(workspace_id, id)` | project.md | `scope='project'` 时下放绑定(列级 SET NULL,README §6.2 第 6 条) |
| `integration_bindings.bound_agent_id` | 复合 FK → `agents(workspace_id, id)` | agent.md | 匹配后触发的目标 agent(README §6.2) |
| `webhook_subscription_deliveries.subscription_id` | 复合 FK → `webhook_subscriptions(workspace_id, id)` | 本模块 | 投递台账归属(README §6.2) |
| `task_executions.trigger` / `trigger_event_id` | `trigger='integration'`;`trigger_event_id` 逻辑引用 `integration_events.id` | runtime.md / README §6.4 | 入站触发的执行(幂等键 §6.9) |
| `notification_delivery.channel='im'` | 出站适配器写入(台账为 comment-inbox.md owns) | comment-inbox.md / README §6.13 | IM 出站投递台账 |
| `approvals`(卡片回调) | 出站适配器推送卡片;回调经 `POST /approvals/{id}/approve\|reject` | README §6.10 | 审批卡片化呈现与回调 |

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

**OAuth 授权(IM/VCS 连接器)**:

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspaces/{ws}/integrations/oauth/{kind}/authorize` | 发起 OAuth 授权码 + PKCE(302 跳外部平台授权页,`state` 防 CSRF) |
| GET | `/integrations/oauth/{kind}/callback` | 授权回调:校验 `state` + 换取 token,**refresh token 只存密文**(`secret_ref`),最小 scope |

### 3.2 入站回调端点(平台签名校验,非 Bearer)

| 方法 | 路径 | 平台 | 签名方案 |
|------|------|------|----------|
| POST | `/api/v1/integrations/feishu/events` | 飞书/Lark | `signature = SHA256(timestamp + nonce + encrypt_key + raw_body)`(取 `timestamp`/`nonce` 头);恒定时间比较 + 时间戳防重放;**`url_verification` challenge 处理见下** |
| POST | `/api/v1/integrations/feishu/cards` | 飞书/Lark | 交互/审批卡片回调(同签名方案);回调经 `card.action.value` 携带 `approval_id`,转发 `POST /approvals/{id}/approve\|reject`(README §6.10) |
| POST | `/api/v1/integrations/slack/events` | Slack | `X-Slack-Signature: v0=HMAC_SHA256(signing_secret, "v0:" + X-Slack-Request-Timestamp + ":" + raw_body)`;恒定时间比较 + 时间戳防重放;`url_verification` 回显 `challenge` |
| POST | `/api/v1/integrations/slack/cards` | Slack | Block Kit 交互回调(`X-Slack-Signature` 同方案);`actions[].value` 携带 `approval_id`,转发统一审批端点 |
| POST | `/api/v1/integrations/github/events` | GitHub | `X-Hub-Signature-256: sha256=HMAC_SHA256(webhook_secret, raw_body)`;`X-GitHub-Delivery` 作 `external_event_id`;`X-GitHub-Event` 作事件类型 |
| POST | `/api/v1/integrations/gitlab/events` | GitLab | `X-Gitlab-Token`(共享密钥,恒定时间比较)或 `X-Gitlab-Signature`(HMAC);`X-Gitlab-Event` 作事件类型;`event_uuid` 作 `external_event_id` |

**统一摄取流程**(复用 autopilot.md §3.2 范式,所有入站端点共用):

```
接收(定位集成:飞书经 app_id/encrypt_key、Slack 经 team_id、GitHub/GitLab 经 installation/绑定路由)
  → 校验平台签名(signature_status;**invalid/missing 一律落库 integration_events(process_status='rejected',
     external_event_id='rejected:<raw-body-hash>')并返回 401,绝不分发不路由**——rejected 前缀独立命名空间防预占)
  → 集成 status='disabled' → 落库 rejected(401 integration_disabled)
  → 签名通过 → 落库(received)→ 以 (integration_id, external_event_id) 去重插入
     (命中唯一冲突 → process_status='deduped',幂等返回 200,不再分发)
  → 匹配 integration_bindings(external_ref + match_config):
       未匹配 / 未匹配到 agent → 仅审计(matched 留痕,不触发,README §6.9)
       命中 → 同事务写 outbox(execution.enqueue,trigger='integration',
              幂等键 sha256(agent_id|integration_binding_id|external_event_id),README §6.9)
              → process_status='dispatched'
  → relay 消费 outbox 入队 task_executions(trigger='integration')→ 摄取完成置 'processed'
```

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

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| POST | `/api/v1/integrations/vcs/links` | **显式关联** PR/commit/branch ↔ issue(请求体 `{integration_id, vcs_ref:{type, url, id}, issue_id}`) | 成员(对 issue 有写权限) |
| DELETE | `/api/v1/integrations/vcs/links/{id}` | 解除关联 | 成员 |
| GET | `/api/v1/issues/{issue_id}/vcs-links` | 列出某 issue 的 VCS 关联(PR/commit/branch + 状态) | 成员 |
| POST | `/api/v1/integrations/vcs/resolve` | **identifier 解析**:从文本/分支/PR 标题提取 `WEB-123` 自动关联(请求体 `{integration_id, source_text, vcs_ref}`) | 成员 / 入站摄取内部调用 |

**identifier 自动关联**:VCS 入站事件(`pull_request`/`merge_request`/`push`/`commit_comment`)的标题、正文、分支名、commit message 中匹配 `<前缀>-<号>`(issue.md `identifier`,如 `WEB-123`)→ 经 `UNIQUE(workspace_id, identifier)` 解析到 issue → 自动建立 VCS 关联;解析不到(前缀不存在/已软删)→ 仅审计不报错(`identifier_not_resolved` 留痕)。

**自动状态流转**:VCS 绑定 `match_config.auto_status_map`(如 `{"merged":"done","closed":"cancelled"}`)在对应事件入站且成功关联 issue 后,经 issue.md 状态流转端点把 issue 置目标状态(服务层校验目标状态存在且迁移合法;以摄取事件幂等,重复事件不重复改状态),并在 issue 发评论留痕("PR #N 已合并,自动置为 done",经 comment-inbox.md,幂等键 §6.5)。

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
| 404 | `not_found` | 集成/绑定/订阅/投递不存在 |
| 409 | `binding_conflict` | `UNIQUE(integration_id, external_ref)` 冲突——该外部身份已绑定 |
| 409 | `conflict` | 名称重复 / 乐观锁冲突 |
| 409 | `duplicate_event` | 入站去重命中(通常作 200 `deduped`,内部用) |
| 410 | `integration_disabled` | 集成 `status='disabled'`,入站拒绝分发 / 出站停发 |
| 422 | `ssrf_blocked` | 出向目标命中私网地址段 / 元数据地址(README §6.16) |
| 422 | `identifier_not_resolved` | VCS identifier(`WEB-123`)解析不到 issue(留痕,不阻塞摄取) |
| 422 | `vcs_link_invalid` | VCS 关联的 issue/vcs_ref 非法或跨工作区 |
| 422 | `subscription_circuit_open` | 订阅处于熔断(`disabled`),需 `resume` 后投递 |
| 422 | `oauth_failed` | OAuth 授权码换取 token 失败 / scope 不足 |
| 429 | `rate_limited` | API 限流 / 出站平台限流退避 |
| 500 | `internal_error` | 服务内部错误 |
| 502 | `upstream_error` | 外部平台 API 调用失败(出站适配) |

### 3.6 WebSocket 实时事件

连接 `/ws`(握手鉴权见 auth.md),订阅频道 `workspace:{ws}:integrations` 或 `integration:{id}`。**实时契约以 README §6.7 为唯一权威**(频道内 `seq`、唯一写入路径 outbox→projector、`resume_from`/`resync_required`);事件名取自 README §6.7 注册表「平台能力」域:

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `integration.updated` | 集成/绑定/订阅创建、配置变更、状态切换、熔断 | `integration_id`, `kind`, `status`, `subject`('integration'\|'binding'\|'subscription') |
| `integration.event_ingested` | 入站事件落库(含签名/处理状态,驱动事件台账实时刷新) | `event_id`, `integration_id`, `event_type`, `signature_status`, `process_status` |

> 不使用未登记事件名(README §6.7 词汇零容忍)。降级:WebSocket 不可用时事件台账退化为轮询 `GET .../integrations/{id}/events`(3~5s)。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
集成管理页(/integrations,设置区,admin/owner 可写)
   ├── 连接器目录:卡片网格(飞书/Lark · Slack · GitHub · GitLab · 出向 Webhook),每卡 [连接]/[已连接 N]
   ├── 已连接集成列表:名称 | 类型图标 | 状态徽章(active/disabled)| 绑定数 | 近7天事件量 | 操作(⚙ ⏸ ⋯)
   └── [+ 添加集成](选 kind → OAuth 授权流 / 粘贴 token)
集成详情页:① 概览(非密配置只读 + [编辑] + 凭据状态[轮换]);② 绑定 tab;③ 事件台账 tab(签名/处理状态过滤)
绑定配置抽屉:外部身份(群/频道/仓库选择器)+ 作用域(工作区/项目)+ 匹配规则(@agent/关键词/分支模式)+ 目标 agent
出向订阅页(/webhooks):订阅列表(https URL | 事件过滤 | 状态 active/paused/disabled[熔断] | 成功率)
   └── 订阅详情:投递历史时间线(state | attempts | response_status | next_retry)+ [手动重试] [恢复熔断]
VCS 关联(issue 详情侧栏):「关联 PR / 提交」区块 —— 列出关联 PR/commit/branch + 状态(打开/已合并/已关闭)
   + 自动状态流转标记("PR #123 合并 → 自动置 done",带集成图标与时间)
IM 卡片(外部平台内):审批卡片 + 交互卡片(样式约定见 §4.4)
```

### 4.2 关键组件

- **连接器目录卡片**:每个公开集成目标平台一张卡(图标 + 名称 + 简述 + 能力标签"IM 通知/审批卡片/事件触发"或"VCS 关联/状态流转");未连接显示 [连接],已连接显示 [已连接 N 个] 与状态点。
- **OAuth 授权流**:[连接] → 新窗跳外部平台授权页(PKCE)→ 授权后回跳 → 显示"授权成功,已连接",凭据落密文(界面**永不展示** secret 明文;粘贴 token 模式仅显示掩码 `••••abcd`)。
- **绑定配置抽屉**:外部身份选择器(从集成已可见的群/频道/仓库拉取)+ 作用域切换(选项目时显示项目下拉)+ 匹配规则表单(@agent 多选、关键词、VCS 事件勾选、分支模式输入,带语法提示)+ 目标 agent 选择器(留空 = 仅审计不触发,显式提示)。
- **事件台账**:每行 时间 + 事件类型 + 签名状态徽章(valid/invalid/missing)+ 处理状态徽章(received/matched/dispatched/deduped/rejected/processed/failed)+ 载荷预览(只读 JSON,外部内容标注"不可信数据");`rejected`/`deduped` 行高亮原因,直接回答"为什么没触发"。
- **出向订阅投递历史**:时间线每行 state 图标 + attempts + response_status + 退避倒计时(next_retry_at);`failed` 行提供 [手动重试];熔断订阅顶部横幅"已连续失败 N 次,已停用,[恢复]"。
- **issue 侧栏 VCS 关联区块**:关联 PR/commit 列表(图标 + 标题 + 外部状态徽章 + 深链);自动状态流转条目以集成图标标注来源("来自 GitHub 集成 · PR #123 合并");[+ 关联] 手动关联(PR URL/commit SHA 输入)。

### 4.3 关键交互流程

**流程 A:连接飞书并绑定值班群**:集成页 → 飞书卡 [连接] → OAuth 授权回跳成功 → 集成详情 → 绑定 tab → [+ 新绑定] → 选"研发值班群"(external_ref)→ 作用域选 INFRA 项目 → 匹配规则勾"@指定 agent"+ 选值班 agent → 保存。群里 @值班 agent → 入站摄取(签名/去重/审计)→ 触发运行 → agent 回评到 issue(IM 消息按不可信数据隔离入上下文)。

**流程 B:审批卡片在 IM 内闭环**:运行命中 `confirm_required` → `approvals` 建审批(README §6.10)→ 出站适配器向绑定 IM 频道推审批卡片(动作/权限/影响/成本/批准按钮)→ 审批人在飞书/Slack 卡片点"批准" → 卡片回调 → 转发 `POST /approvals/{id}/approve` → 运行从审批点续跑;台账记 `notification_delivery(channel='im')` 与 approvals `decision_comment`。

**流程 C:GitHub PR 合并自动流转**:绑定 GitHub 仓库到 WEB 项目(`auto_status_map={"merged":"done"}`)→ 开发者 PR 标题含 `WEB-123` → PR 合并事件入站 → 签名/去重 → identifier 解析关联 `WEB-123` → 自动置 done + 发评论"PR #N 已合并,自动置为 done" → issue 侧栏显示关联 PR 与流转标记。

**流程 D:开发者订阅出向 Webhook**:出向订阅页 → [+ 新订阅] → 填 https URL(非 https 即拒)+ 勾事件类型(`issue.updated` 等)→ 创建后**仅显示一次**签名密钥(提示妥善保存)→ Mesh 事件经 outbox 投递(HMAC 签名 + 重试退避)→ 投递历史可查;连续失败熔断后 [恢复]。

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

集成管理页/订阅页/事件台账须实现 loading(skeleton)/ empty(空态 + "连接第一个集成"主操作)/ permission denied(非 admin 只读)/ offline / retry;集成 `disabled` 显示明确"已停用,入站事件将被拒绝"横幅;出向订阅熔断显示"已连续失败 N 次已停用 [恢复]";OAuth 失败显示"授权失败,请重试"并链回 [重新授权]。

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
- [ ] **Slack 同构**:Events API 事件回调(`message.channels` 等)经 `X-Slack-Signature` 校验后触发;Block Kit 卡片推送/回调与飞书语义对齐(同一抽象,不同适配点)。
- [ ] **VCS 关联**:commit/PR/branch ↔ issue 经 `POST /integrations/vcs/links` 显式关联,或经 identifier(`WEB-123`)自动解析关联(`UNIQUE(workspace_id, identifier)`);`GET /issues/{id}/vcs-links` 返回关联列表。
- [ ] **VCS 自动状态流转**:PR merge/close 事件入站并关联 issue 后,按 `auto_status_map` 经 issue.md 状态流转置目标状态(校验目标状态存在 + 迁移合法)+ 发评论留痕;**重复事件幂等不重复改状态**。
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
- [ ] **外部侧唯一绑定**:`UNIQUE(integration_id, external_ref)` 下重复绑定同一外部群/频道/仓库 → 409 `binding_conflict`。
- [ ] **真实 DELETE 行为(README §6.2 第 6 条/§9 T18 同类)**:删除 agent 时 `integration_bindings.bound_agent_id` 经列级 `ON DELETE SET NULL (bound_agent_id)` 仅置空引用列、`workspace_id` 保持非空;删除项目时 `project_id` 置空而绑定行不报错;硬删集成级联其 bindings/events;软删除集成后绑定/事件保留。

### 5.5 实时与可观测

- [ ] `integration.updated`(集成/绑定/订阅变更、熔断)与 `integration.event_ingested`(入站落库)均取自 README §6.7 注册表「平台能力」域,无未登记事件名;经唯一写入路径推送,带频道 `seq`,断线凭 `resume_from` 重放无丢失无重复(README §6.7/§9 T26)。
- [ ] 事件台账可查询某条入站事件的完整生命周期(签名状态/处理状态/载荷),直接定位"未触发"原因(`rejected`/`deduped`/未匹配);出向投递台账可查每次尝试的 attempts/response_status/退避。
