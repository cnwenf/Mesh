# 评论与收件箱(Comment & Inbox)功能 Spec

| 项目 | 内容 |
|------|------|
| 所属层 | 协作层(Collaboration) |
| 模块 | comment-inbox |
| 依赖 Spec | `workspace`(多租户)、`member`(统一 `members.id`,human\|agent)、`auth`(Bearer/RBAC/限流)、`issue`(评论宿主与订阅路由)、`attachment`(评论内附件,统一 `attachments`/`attachment_links`)、`agent` / `runtime`(提及 agent 入队执行 `task_executions`) |
| 被依赖 | `agent` / `runtime`(执行结果以 agent 评论回流)、`chat-session`(形态 B 的评论/提及/通知**引用本 Spec 权威表**)、邮件摘要任务(消费 `notifications`) |
| 技术栈 | FastAPI + SQLAlchemy 2.x + PostgreSQL 16 + WebSocket |
| 状态 | Draft v3(R2 修订) |

> **全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)**
> 1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `uuid`(默认 `gen_random_uuid()`);`created_at`/`updated_at` 为 `TIMESTAMPTZ NOT NULL DEFAULT now()`;软删除统一 `deleted_at TIMESTAMPTZ NULL`。
> 2. **成员**:成员模型以 **README §6.1** 为唯一权威——评论 `author_id`、提及 `mentioned_id`、通知 `actor_id`/`recipient_id`、订阅 `subscriber_id` 一律引用统一 `members.id`(人类与 agent 同表);人类/agent 判别一律 JOIN `members.member_type`,**存储层不设任何人类/agent 判别冗余列**;API 响应可携带服务端计算的 `member_type` 快照(标注"真源为 members")。系统活动作者以 `author_kind ∈ {'member','system'}` + NULL `author_id` 表达(CHECK + NULL FK,见 §2.2,为 §6.1 允许的例外)。
> 3. **多租户**:跨模块外键一律按 **README §6.2** 建复合 FK `(workspace_id, x_id) → 目标表 (workspace_id, id)`,引用表同时存 `workspace_id`。
> 4. **接口**:REST 前缀 `/api/v1`;`Authorization: Bearer <token>`;包络 / 游标分页(分组查询为整体游标)/ 错误信封 / 过滤限制见 **README §6.14**。
> 5. **实时**:统一实时契约见 **README §6.7**(频道内 `seq`、`realtime_events` 持久重放、`resume_from` / `resync_required`);事件名 `<entity>.<action>`。
> 6. **队列 / 投递**:业务写派生的执行入队 / 通知 fan-out / 实时事件登记经 **transactional outbox(README §6.6)**;at-least-once + 幂等键(§6.5);长任务执行实体为 `task_executions` / `execution_attempts`,状态词汇与事件词汇见 **README §6.4 与 runtime.md**(以此为运行的唯一真源实体,不另设其他运行记录实体)。
> 7. **触发语义**:@提及 agent 的确定语义以 **README §6.9** 触发矩阵为唯一权威。
> 8. **通知去噪**:订阅 / 静音 / 重读 / 分级 / 聚合规则以 **README §6.13** 为唯一权威。
> 9. **ORM**:SQLAlchemy 2.x 声明式约定(`Mapped` / `mapped_column`)。

---

## 1. 功能描述

### 1.1 定位

评论(Comment)是 issue 详情页的协作主线程:人类成员与 agent 队友在同一时间线里发言、追问、表态、沉淀结论。收件箱(Inbox / Notification)是把「与我相关的事件」聚合、去噪、实时送达每位成员的统一通知中心。二者共同构成 Mesh 的异步协作底座,并承载 Mesh 的核心差异:**`@`提及 agent 不只是通知,而是入队一次 agent 执行**。

### 1.2 功能点与场景

#### 评论(Comment)

| # | 功能点 | 典型场景 |
|---|--------|----------|
| C1 | 在 issue 下发表评论 | 成员在详情页底部输入框补充上下文、贴日志、给结论 |
| C2 | 线程化回复(单层折叠) | 针对某条评论追问,回复折叠在父评论下,避免主评论区被多线讨论淹没 |
| C3 | `@`提及人类成员 | 输入 `@` 触发选人弹层,被提及者收到通知并自动加入订阅 |
| C4 | `@`提及 agent(核心差异) | `@` 列表人与 agent 混排;选中 agent 后**按 README §6.9 入队一次该 agent 执行**,结果以 agent 评论回流同一线程 |
| C5 | Markdown 富文本 | 标题/加粗/斜体/列表/行内代码/代码块(语言高亮)/引用/表格/任务清单/分割线 |
| C6 | 链接与智能引用 | 粘贴 issue 链接渲染为带标题状态的引用卡片;`#MES-123` 简写自动补全为链接 |
| C7 | 表情回应(reaction) | 对评论加 👍/🎉 等,无需另发评论即可表态;显示反应人与计数 |
| C8 | 编辑评论 | 作者修改自己的评论,保留「已编辑」标记与编辑时间;**新增 @ 仅为新增提及入队(§6.9)** |
| C9 | 删除评论 | 作者或管理员删除;软删除留占位「该评论已删除」以保线程完整 |
| C10 | 解决/重开线程 | 把线程标记已解决并折叠,可重新打开;解决人/时间留痕 |
| C11 | 评论内附件 | 评论携带图片/文件(经统一 `attachments`/`attachment_links`,见 attachment.md) |
| C12 | 评论深链锚点 | 每条评论有可复制永久链接,跳转并高亮 |
| C13 | 系统活动评论(activity) | 状态/分派/字段变更以只读「系统评论」出现在时间线(`author_kind='system'`,`author_id` 为 NULL) |
| C14 | 草稿自动保存 | 输入内容本地暂存,刷新/切走不丢 |
| C15 | 评论排序 | 顶层按时间正序;线程内回复按时间正序;可选「最新在前」 |

#### 收件箱 / 通知(Inbox / Notification)

| # | 功能点 | 典型场景 |
|---|--------|----------|
| I1 | 被分派通知 | issue 被分派给我(或从我转走)进入收件箱 |
| I2 | 被 `@`提及通知 | 有人在评论/描述里 `@`我 |
| I3 | 订阅 issue 更新通知 | 我订阅/参与过的 issue 有新评论/状态/字段变更 |
| I4 | 我创建的 issue 更新 | 我创建的 issue 任意活动 |
| I5 | agent 执行完成通知(核心差异) | 我触发或关注的 agent 执行结束/失败,附产物评论链接 |
| I6 | 未读/已读 | 单条已读、一键全部已读、未读计数徽标 |
| I7 | 归档 | 已处理通知移出主视图 |
| I8 | 按 issue 分组 / 按类型筛选 | 同一 issue 多条通知折叠成组;按 提及/分派/订阅/agent 筛选 |
| I9 | 实时推送 | WebSocket 实时下发,顶栏徽标即时 +1 |
| I10 | 邮件摘要 | 离线累积通知按实时/日聚合发邮件,粒度可配 |
| I11 | 通知偏好设置 | 按事件类型开关站内/邮件;免打扰(quiet hours,critical 穿透);agent 执行通知开关 |
| I12 | 跳转上下文 | 点击通知直达对应 issue/评论锚点 |

### 1.3 边界与非目标

**范围内:**
- 评论的发表/回复/编辑/删除/解决/反应/提及解析与 agent 执行入队。
- 通知的生成(fan-out,经 outbox)、去重合并、偏好过滤、已读/归档、按 issue 分组、实时推送、邮件摘要触发。

**非目标(本 Spec 不覆盖):**
- agent 执行的执行细节(由 `agent` / `runtime` Spec 负责,本 Spec 只负责「入队 `task_executions`」与「回流评论」的衔接;执行实体与状态词汇见 README §6.4)。
- 附件字节流上传/下载(由 `attachment` Spec 负责,本 Spec 仅在评论载荷中引用 `attachment_ids`)。
- 权限模型本身(由 `auth` Spec 的 RBAC 提供,本 Spec 只声明所需权限点)。
- 富文本编辑器组件的内部实现(前端组件库范畴)。
- 跨 workspace 全局收件箱、第三方 IM 集成、Snooze 重新提醒(列为可选增强,默认不实现)。

---

## 2. 数据模型

> **所有权声明(唯一权威)**:本 Spec **owns** `comments` / `comment_mentions` / `comment_reactions` / `issue_subscriptions` / `notifications` / `notification_preferences` / `notification_delivery` 七张表,是评论、提及、反应、订阅与通知数据模型及相关端点的**唯一权威**。`chat-session.md`(形态 B)**只引用**这些表与端点,不重复建表;聊天/评论附件统一走 `attachment.md` 的 `attachments`/`attachment_links`。

### 2.1 ER 关系

```
workspaces 1─* issues 1─* comments 1─* comment_reactions
                         comments 1─* comment_mentions *─1 members
                         comments 自引用 (parent_id / thread_root_id)
                         comment_mentions.triggered_execution_id ──► task_executions（runtime.md owns，README §6.4）
issues 1─* issue_subscriptions *─1 members
members 1─* notifications (recipient) ; notifications *─1 issues ; *─1 comments
members 1─* notification_preferences
notifications 1─* notification_delivery

members 为统一身份表（member_type ∈ {human, agent}），由 member Spec 定义，README §6.1 为唯一权威。
所有跨模块引用均为复合 FK（workspace_id, x_id）→ 目标表（workspace_id, id），见 README §6.2。
```

### 2.2 `comments` — 评论主表

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK;`UNIQUE (workspace_id, id)`(供复合 FK 引用,README §6.2) | 评论 ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离,所有查询强制带 |
| `issue_id` | uuid | NOT NULL,复合 FK `(workspace_id, issue_id) → issues(workspace_id, id)` | 所属 issue |
| `parent_id` | uuid | NULL,**同 issue 复合 FK `(workspace_id, issue_id, parent_id) → comments(workspace_id, issue_id, id) ON DELETE CASCADE`**(README §6.2 第 7 条:父评论必须同 issue,重叠复合 FK 数据库层强制;深度=1 仍由服务层/CHECK 保证) | 父评论;`NULL` 表示顶层 |
| `thread_root_id` | uuid | NULL,**同 issue 复合 FK `(workspace_id, issue_id, thread_root_id) → comments(workspace_id, issue_id, id) ON DELETE CASCADE`**(README §6.2 第 7 条:线程根必须同 issue) | 线程根(冗余加速聚合);顶层为 `NULL`,回复指向所属顶层评论 |
| `author_kind` | text | NOT NULL, CHECK in ('member','system') | `member`=人类/agent 作者;`system`=活动流(系统作者需 NULL member FK,为 README §6.1 允许的 CHECK + NULL FK 例外;**不是** human\|agent 判别列) |
| `author_id` | uuid | NULL,复合 FK `(workspace_id, author_id) → members(workspace_id, id)` **ON DELETE RESTRICT**(成员一律经 `status='removed'` 软删除,历史不悬空,README §6.2 第 6 条) | 作者;`author_kind='member'` 时 NOT NULL,`'system'` 时 NULL。人类/agent 由 JOIN `members.member_type` 区分 |
| `body_markdown` | text | NOT NULL, CHECK (char_length > 0) | 原始 Markdown(真源,编辑以此为准) |
| `body_html` | text | NULL | 服务端渲染并**净化后**的 HTML 缓存(白名单标签/属性,防 XSS) |
| `body_text` | text | NULL | 纯文本(搜索/摘要/邮件用) |
| `edited_at` | timestamptz | NULL | 最近编辑时间(NULL=从未编辑) |
| `resolved_at` | timestamptz | NULL | 线程被解决时间(仅顶层有意义) |
| `resolved_by_id` | uuid | NULL,复合 FK `(workspace_id, resolved_by_id) → members(workspace_id, id)` **ON DELETE RESTRICT**(成员软删除,历史不悬空,README §6.2 第 6 条) | 解决人 |
| `deleted_at` | timestamptz | NULL | 软删除 |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | 时间戳 |

**关系与约束:**
- **表级约束**:`UNIQUE (workspace_id, issue_id, id)`(供 `parent_id`/`thread_root_id` 的同 issue 重叠复合 FK 引用,README §6.2 第 7 条)。
- `parent_id` 自引用构成线程;应用层 + CHECK/触发器保证**回复深度=1**(回复不能再被回复,新回复一律挂到线程根下),业界主流「单层回复折叠」。
- `thread_root_id` 为冗余字段:写入回复时由服务端填充为顶层评论 id,用于免递归聚合 `reply_count` 与拉取整线程。
- 删除采用软删除以保线程完整;issue 删除时级联软删除其评论。
- `author_kind='system'` 的评论由服务端在字段/状态变更时写入,不接受 API 直接创建。
- **存储层不设任何人类/agent 判别冗余列**;API 响应中的 `author.member_type` 为服务端 JOIN `members` 计算的快照(README §6.1)。

**关键索引:**
- `idx_comments_issue_created (workspace_id, issue_id, created_at)` — 按时间拉取某 issue 评论(主路径)。
- `uq_comments_ws_issue_id (workspace_id, issue_id, id)`(`CREATE UNIQUE INDEX uq_comments_ws_issue_id ON comments(workspace_id, issue_id, id);`)— 重叠唯一键,供 `parent_id`/`thread_root_id` 同 issue 复合 FK 引用(README §6.2 第 7 条)。
- `idx_comments_thread (workspace_id, thread_root_id, created_at)` — 拉取某线程全部回复。
- `idx_comments_author (workspace_id, author_id, created_at)` — 「我发过的评论」。
- 部分索引 `idx_comments_active ON comments(issue_id, created_at) WHERE deleted_at IS NULL`。

### 2.3 `comment_mentions` — 提及解析结果

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离(README §6.2) |
| `comment_id` | uuid | NOT NULL,复合 FK `(workspace_id, comment_id) → comments(workspace_id, id)` | 来源评论 |
| `mentioned_id` | uuid | NOT NULL,复合 FK `(workspace_id, mentioned_id) → members(workspace_id, id)` | 被提及成员(human 或 agent,以 JOIN `members.member_type` 区分) |
| `triggered_execution_id` | uuid | NULL,复合 FK `(workspace_id, triggered_execution_id) → task_executions(workspace_id, id)` | 若被提及者为 agent 且成功入队执行,记录**逻辑执行** ID(runtime.md owns `task_executions`,README §6.4;核心差异留痕) |
| `deleted_at` | timestamptz | NULL | 软删除:编辑评论移除 @ 时提及记录软删除(README §6.9:不取消在途执行) |
| `created_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_mentions (comment_id, mentioned_id)` — 同一评论对同一成员只记一次(天然抑制「一条评论 @ 同一 agent 两次只入队一次执行」,README §6.9)。
**关键索引:** `idx_mentions_target (mentioned_id, created_at)` — 反查「提到我的评论」,驱动通知与收件箱;`idx_mentions_chain (workspace_id, mentioned_id, created_at)` — 供 §6.9 护栏(链深度/频率)扫描。

### 2.4 `comment_reactions` — 表情回应

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离(README §6.2) |
| `comment_id` | uuid | NOT NULL,复合 FK `(workspace_id, comment_id) → comments(workspace_id, id)` | |
| `actor_id` | uuid | NOT NULL,复合 FK `(workspace_id, actor_id) → members(workspace_id, id)` | 反应人(human/agent,以 JOIN `members.member_type` 区分) |
| `emoji` | text | NOT NULL | 归一化为 unicode(或 shortcode) |
| `created_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_reaction (comment_id, actor_id, emoji)` — 同一人对同一评论同一 emoji 只一次。
**关键索引:** `idx_reactions_comment (workspace_id, comment_id)`。

### 2.5 `issue_subscriptions` — issue 订阅(驱动通知路由)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | |
| `issue_id` | uuid | NOT NULL,复合 FK `(workspace_id, issue_id) → issues(workspace_id, id)` | |
| `subscriber_id` | uuid | NOT NULL,复合 FK `(workspace_id, subscriber_id) → members(workspace_id, id)` | 订阅者(human/agent) |
| `reason` | text | NOT NULL, CHECK in ('creator','assignee','mentioned','participated','manual') | 订阅来源(README §6.13 默认订阅) |
| `muted` | boolean | NOT NULL DEFAULT false | 静音(保留订阅但不出通知) |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_subscription (issue_id, subscriber_id)`。
**关键索引:** `idx_subscriptions_issue ON issue_subscriptions(workspace_id, issue_id) WHERE NOT muted`。
> 通知 fan-out 遍历「订阅者 ∪ 被提及者 ∪ 分派对象」去重后,再按各自偏好(2.7)与 README §6.13 去噪规则过滤。

### 2.6 `notifications` — 通知主表(收件箱数据源)

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK;`UNIQUE (workspace_id, id)`(供 `notification_delivery` 复合 FK 引用,README §6.2) | 通知 ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离 |
| `recipient_id` | uuid | NOT NULL,复合 FK `(workspace_id, recipient_id) → members(workspace_id, id)` | 接收者(收件箱主要面向 human) |
| `type` | text | NOT NULL | 枚举:`assigned`/`mentioned`/`subscribed_update`/`comment_created`/`status_changed`/`execution_finished`/`review_requested`/`due_soon`(执行词汇对齐 README §6.4)。`execution_finished` 投递与优先级按 README §6.13 矩阵:执行**成功默认不生成该通知**(仅当 `notification_preferences` 显式订阅 `execution_finished` 时生成,`priority=normal`);失败/超时生成且 `priority=critical` |
| `priority` | text | NOT NULL,CHECK IN ('critical','normal') | 通知优先级,服务端按 README §6.13 唯一优先级矩阵派生(critical:执行失败/超时、审批请求、安全隔离、被分派、被 @;normal:其余) |
| `actor_kind` | text | NULL, CHECK in ('member','system') | 触发者种类(系统触发需 NULL member FK,为 README §6.1 允许的 CHECK + NULL FK 例外;**不是** human\|agent 判别列) |
| `actor_id` | uuid | NULL,复合 FK `(workspace_id, actor_id) → members(workspace_id, id)` | 触发者(`actor_kind='system'` 时为 NULL;human/agent 由 JOIN `members.member_type` 区分) |
| `issue_id` | uuid | NULL,复合 FK `(workspace_id, issue_id) → issues(workspace_id, id)` | 关联 issue(按 issue 分组) |
| `comment_id` | uuid | NULL,复合 FK `(workspace_id, comment_id) → comments(workspace_id, id)` | 关联评论(跳转锚点) |
| `execution_id` | uuid | NULL,复合 FK `(workspace_id, execution_id) → task_executions(workspace_id, id)` | 关联执行(`execution_finished` 等;README §6.4) |
| `payload` | jsonb | NOT NULL DEFAULT '{}' | **渲染所需快照**(评论摘要、变更前后值、actor 显示名、目标实体引用等),保证源实体被删后通知仍可读 |
| `group_key` | text | NULL | 分组键(如 `issue:<id>:<type>`),用于折叠 |
| `read_at` | timestamptz | NULL | 已读时间(NULL=未读) |
| `archived_at` | timestamptz | NULL | 归档时间(移出主视图,可回查;README §6.13) |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**关键索引:**
- `idx_notifications_inbox (workspace_id, recipient_id, archived_at, created_at DESC)` — 收件箱主查询。
- 部分索引 `idx_notifications_unread ON notifications(workspace_id, recipient_id) WHERE read_at IS NULL AND archived_at IS NULL` — 未读徽标(高频)。
- `idx_notifications_group (recipient_id, group_key, created_at DESC)` — 分组折叠。
- GIN `idx_notifications_payload (payload)` — 按需。

**设计要点(必须实现):**
- **`payload` 存快照而非全靠外键回查**——评论/issue 被删除后通知仍可读。`payload` 至少含:`{actor_name, actor_avatar_url, preview, title, count}`;目标实体的类型与引用承载于 `payload` 与 `issue_id`/`comment_id`/`execution_id` 具体外键,**不设泛型「目标类型 + 目标 ID」判别列**(README §6.1)。
- **去重合并**:同 `group_key` **60s 聚合窗口**内合并为一条(更新 `payload.count` 与 `updated_at`),避免「连发 5 条评论」刷出 5 条(README §6.13)。

### 2.7 `notification_preferences` — 通知偏好

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | |
| `member_id` | uuid | NOT NULL,复合 FK `(workspace_id, member_id) → members(workspace_id, id)` | |
| `event_type` | text | NOT NULL | 同 `notifications.type`,或 `all` |
| `in_app` | boolean | NOT NULL DEFAULT true | 是否站内通知 |
| `email` | text | NOT NULL DEFAULT 'digest', CHECK in ('none','realtime','digest') | 邮件策略 |
| `quiet_hours_start` | time | NULL | 免打扰开始(用户级,README §6.13;critical 事件穿透) |
| `quiet_hours_end` | time | NULL | 免打扰结束 |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_notif_pref (workspace_id, member_id, event_type)`。
> 缺省:用户未显式设置的 `event_type` 走默认策略(提及/分派=站内+实时邮件;订阅更新=站内+日摘要)。**agent 执行成功默认不投递**(留运行页;显式订阅 `execution_finished` 后为站内 + digest);**执行失败/超时按 critical 站内 + realtime 邮件(穿透 quiet hours,README §6.13)**。

### 2.8 `notification_delivery` — 投递/去重台账

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离(README §6.2) |
| `notification_id` | uuid | NOT NULL,复合 FK `(workspace_id, notification_id) → notifications(workspace_id, id)` | |
| `channel` | text | NOT NULL, CHECK in ('in_app','email','websocket','im') | `channel='im'` 时具体 IM 平台(`feishu`/`slack`)与目标外部身份记入 `error`/台账扩展字段;经集成平台出站适配器投递(README §6.13/§6.17) |
| `state` | text | NOT NULL, CHECK in ('pending','sent','failed') | |
| `sent_at` | timestamptz | NULL | |
| `error` | text | NULL | |
| `created_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_delivery (notification_id, channel)` — 邮件摘要任务幂等投递、失败重试。

### 2.9 跨模块外键说明

- `author_id` / `actor_id` / `recipient_id` / `subscriber_id` / `mentioned_id` / `resolved_by_id` → **复合 FK → `members(workspace_id, id)`**(member Spec)。`members.member_type ∈ {human, agent}` 是唯一的人类/agent 判别源;**本模块不冗余该判别列**(展示时 JOIN 或读 `payload` 快照)。
- `triggered_execution_id` / `notifications.execution_id` → **复合 FK → `task_executions(workspace_id, id)`**(runtime.md owns;README §6.4 为唯一权威——`task_executions` 是全系统运行的唯一真源实体名,不另设其他运行记录实体)。
- `issue_id` → **复合 FK → `issues(workspace_id, id)`**(issue Spec);`comment_id` → 复合 FK → `comments(workspace_id, id)`(本模块);`workspace_id` → `workspaces.id`(workspace Spec)。
- 评论附件经 `attachment_links`(`linked_type='comment'`,引用行携带 `workspace_id`;attachment.md owns,README §6.2 第 4 条多态逻辑外键)。
- issue 删除采用软删除时,通知保留(靠 `payload` 快照可读);评论级联软删除。
- 跨租户隔离的权威定义与集成测试矩阵见 README §6.2 / §9(T1)。

---

## 3. 接口设计

> 鉴权:`Authorization: Bearer <token>`(成员会话 token 或 agent runtime API token)。写操作校验 workspace 角色与资源权限(RBAC,见 auth Spec)。时间统一 RFC3339(UTC)。**包络 / 游标分页(分组查询为整体游标)/ 错误信封 / 幂等写 / 过滤限制统一遵循 README §6.14。**

### 3.1 评论端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/issues/{issue_id}/comments` | 列出评论(默认仅顶层 + `reply_count` + 前 N 条预览回复) |
| POST | `/api/v1/issues/{issue_id}/comments` | 发表评论(可带 `parent_id` 成为回复;可带 `suppress_triggers`) |
| GET | `/api/v1/comments/{comment_id}` | 取单条评论 |
| PATCH | `/api/v1/comments/{comment_id}` | 编辑评论(乐观锁,带 `updated_at`;新增 @ 仅为新增提及入队,§3.5) |
| DELETE | `/api/v1/comments/{comment_id}` | 软删除评论 |
| GET | `/api/v1/comments/{comment_id}/replies` | 列出某线程回复(游标分页) |
| POST | `/api/v1/comments/{comment_id}/resolve` | 解决线程 |
| POST | `/api/v1/comments/{comment_id}/reopen` | 重开线程 |
| GET | `/api/v1/comments/{comment_id}/reactions` | 列出反应 |
| POST | `/api/v1/comments/{comment_id}/reactions` | 添加反应 |
| DELETE | `/api/v1/comments/{comment_id}/reactions/{emoji}` | 取消(自己的)反应 |

**发表评论请求体:**
```json
{
  "body_markdown": "已定位问题,详见日志。\n\n@李四 你确认下生产配置?\n@code-reviewer 帮忙跑一遍回归。",
  "parent_id": null,
  "attachment_ids": ["8c1f..."],
  "suppress_triggers": false
}
```
> 提及**由服务端从 Markdown 解析为准**(防伪造/漏记);客户端无需显式提交 mentions。服务端检测到 agent 提及即按 README §6.9 入队执行,并在响应回填 `triggered_execution_ids`。`suppress_triggers: true` = **仅通知不运行**(README §6.9 显式抑制)。

**发表评论响应体(201):**
```json
{
  "data": {
    "id": "c-abc",
    "issue_id": "i-1",
    "parent_id": null,
    "thread_root_id": null,
    "author": {"id": "mem-9", "member_type": "human", "name": "李四", "avatar_url": "..."},
    "body_markdown": "...",
    "body_html": "<p>...</p>",
    "reactions": [{"emoji": "👍", "count": 2, "reacted_by_me": false}],
    "reply_count": 0,
    "resolved_at": null,
    "mentions": [{"id": "mem-222", "member_type": "agent", "name": "code-reviewer"}],
    "triggered_execution_ids": ["exec-77"],
    "created_at": "2026-07-24T10:00:00Z",
    "edited_at": null
  }
}
```
> `author.member_type` / `mentions[].member_type` 为服务端 JOIN `members` 计算的快照(真源为 `members`,README §6.1)。

**列出评论查询参数:** `?limit=50&cursor=<opaque>&include=replies|none&order=asc`。
> 拉取策略(采纳 A):列表只返回顶层评论 + `reply_count` + 前 N 条预览回复;展开时按 `GET /comments/{id}/replies` 分页拉。

### 3.2 收件箱 / 通知端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/inbox` | 列出我的通知(游标分页 + 筛选 + 分组) |
| GET | `/api/v1/inbox/unread-count` | 未读计数(顶栏徽标) |
| POST | `/api/v1/inbox/{notification_id}/read` | 标记单条已读 |
| POST | `/api/v1/inbox/{notification_id}/unread` | 标记未读 |
| POST | `/api/v1/inbox/read-all` | 全部已读(可按筛选条件) |
| POST | `/api/v1/inbox/{notification_id}/archive` | 归档 |
| POST | `/api/v1/inbox/archive-read` | 归档全部已读 |
| POST | `/api/v1/issues/{issue_id}/mute` | 按 issue 静音(保留订阅但不出通知,README §6.13) |
| POST | `/api/v1/issues/{issue_id}/unmute` | 取消静音 |
| GET | `/api/v1/notification-preferences` | 读取偏好 |
| PUT | `/api/v1/notification-preferences` | 更新偏好 |

**列出收件箱查询参数:** `?limit=30&cursor=&filter=unread|all|mentions|assigned|agent&type=&grouped=true`。

**收件箱响应体(200,分组形态;包络与整体游标见 README §6.14):**
```json
{
  "data": [
    {
      "id": "n-1",
      "group_key": "issue:i-1:comment_created",
      "type": "comment_created",
      "issue": {"id": "i-1", "identifier": "MES-1", "title": "..."},
      "actor": {"id": "mem-222", "member_type": "agent", "name": "code-reviewer"},
      "preview": "已修复并通过测试,PR: ...",
      "count": 3,
      "read_at": null,
      "created_at": "2026-07-24T10:05:00Z",
      "latest_comment_id": "c-xyz"
    }
  ],
  "next_cursor": "..."
}
```
> `next_cursor=null` 表示末页;分组查询统一为 README §6.14 的「整体游标」契约(不给每组独立 cursor)。

**`payload` 快照示例(notifications.payload):**
```json
{
  "actor_name": "code-reviewer",
  "actor_avatar_url": "...",
  "actor_member_type": "agent",
  "title": "登录跳转异常",
  "preview": "已修复并通过测试,PR: …",
  "count": 3,
  "changes": {"status": {"from": "in_progress", "to": "in_review"}}
}
```
> 即使 `comment_id`/`issue_id` 对应实体被删,前端仍可用 `payload` 渲染出可读通知(跳转目标不存在时提示「原内容已删除」)。

### 3.3 错误码

> 错误信封与 HTTP 语义遵循 README §6.14(`{"error": {"code", "message", "details"}}`,message 不泄漏堆栈/SQL/内部 ID);下表为本模块具名 code。

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 字段缺失/超长/非法(body 为空、emoji 非法) |
| 401 | `unauthorized` | 缺失/过期/非法 token |
| 403 | `forbidden` | 无权限(删他人评论、跨 workspace、无 agent 触发权限) |
| 404 | `not_found` | 评论/通知/issue 不存在或已删除 |
| 409 | `conflict` | 重复反应、并发编辑冲突(`updated_at` 乐观锁) |
| 410 | `gone` | 目标评论已删除但 ID 曾被引用 |
| 422 | `mention_invalid` | 提及了不存在/无权限的对象 |
| 429 | `rate_limited` | 触发限流(见 auth Spec),含 `Retry-After` |
| 500 | `internal_error` | 服务端异常(不泄露堆栈) |

### 3.4 鉴权与权限要点

- 评论读:workspace 成员且对 issue 有读权限。
- 评论写:对 issue 有评论权限;agent 评论由 agent runtime 用 API token 写入(以 agent 的 `members` 身份署名)。
- 编辑/删除:仅作者本人,或 admin/owner 角色。
- **提及 agent 触发执行**:调用者须对该 agent 有「触发」权限,否则返回 `forbidden`;该 agent 提及**不入队执行**(可降级为普通提及,即仅通知不运行,由产品策略决定,默认拒绝并提示)。

### 3.5 @提及 agent = 入队一次执行(核心差异,必须实现)

**触发语义以 README §6.9 触发矩阵为唯一权威**(不允许「合并或排队」之类不可测试表述)。本模块实现其中与评论相关的确定语义:

| 场景 | 确定语义(可测试) |
|------|--------------------|
| 评论**发布**时 @agent A | 发布后入队 A 的一次执行(`trigger='mention'`,幂等键见 README §6.5);`uq_mentions(comment_id, mentioned_id)` 保证同评论同 agent 仅一次 |
| **编辑**旧评论:新增 @A | 服务端 diff 编辑前后提及集合,**仅为新增的提及入队**;因无关文字修改**不重复**产生执行 |
| 编辑旧评论:移除 @A | **不取消**已入队/运行中的执行(仅影响未来);提及记录软删除(`comment_mentions.deleted_at`) |
| 同评论重复编辑(提及集合未变) | **no-op** |
| 运行中再次 @同一 agent(**新评论**) | **入队新执行**(每条评论 = 独立触发事件);防风暴由频率护栏(rate_limit + 链深度)兜底,语义本身确定 |

**实现要点(服务端):**
1. 评论创建/编辑成功,服务端从 `body_markdown` 解析出提及列表(以服务端解析为准,客户端显式提交的提及仅作参考)。
2. 编辑时与上一版本提及集合做 **diff**,仅对**新增**且 JOIN `members` 得 `member_type='agent'` 的提及走触发流程。
3. 入队经 **transactional outbox(README §6.6)** 写 `execution.enqueue` 事件 → relay 创建 `task_executions`(`queued`,`trigger='mention'`,入队快照见 README §6.11,幂等键见 §6.5),落 `comment_mentions.triggered_execution_id`。业务事务与 outbox 同提交,接口同步返回;即使派发组件短暂不可用,事件也不丢(outbox 补投,集成测试 T5)。
4. 请求体 `suppress_triggers: true` → **仅发通知、不入队执行**(README §6.9 显式抑制)。
5. 执行结果由 agent runtime 以 **agent 评论**形式回流到**原线程**(`parent_id`=原评论所属线程根),并把触发者加入订阅(reason=`participated`/`mentioned`),触发者收到 `execution_finished` 通知;执行事件词汇(`execution.queued` / `execution.completed` 等)见 runtime.md。

**权限校验:** 无触发权限 → 该提及返回 `mention_invalid`/`forbidden`,不入队;其余合法提及正常处理。

**agent 间回环抑制(必须实现):**
- **不提及即结束**:回复 agent 的评论若不再 `@` 它,则不触发新执行——天然终止 agent-to-agent 循环。这是默认且主要的抑制手段。
- **自我抑制**:动作发起者不给自己生成通知;agent 永不会收到「会再次触发自己」的通知(README §6.13)。
- **同评论去重**:`uq_mentions (comment_id, mentioned_id)` 保证一条评论 @ 同一 agent 两次只入队一次执行。
- **agent 主动 @ 另一 agent**:仅在 agent 评论显式包含对另一 agent 的提及时才触发,且受链深度上限保护(默认 `MAX_AGENT_CHAIN_DEPTH`,超过则静默丢弃并记录审计),防止 A↔B 互相 @ 形成死循环;频率护栏(rate_limit)兜底防风暴。
- 执行失败:在评论区留失败占位卡片 + 通知触发者,便于重试;失败不自动重试入队(避免放大)。

**UI 配套(README §6.9):** @ 候选提示语为「**发布后将触发一次运行**」(不得使用暗示「选中即触发」的措辞);composer 提交前展示 **trigger preview**(列出将被触发的 agent 清单);提供**显式抑制**开关(请求体 `suppress_triggers: true` → 仅通知不运行)。

### 3.6 WebSocket 事件

> **统一实时契约见 README §6.7**:事件持久化于 `realtime_events`,`seq` 为**频道内**单调递增(频道如 `issue:{issue_id}` / `member:{member_id}:inbox`),与事件行同事务分配(持久真源);客户端记频道内 `last_seq`,重连带 `resume_from=<last_seq+1>` 从 `realtime_events` 顺序补发;游标过旧收 `{"op":"resync_required", ...}` 后走 REST 对账。本节只列本模块的事件名与载荷,**不重复定义 seq 契约**;订阅时逐资源授权(workspace 成员资格 / issue 可见性)。

| 事件 | 载荷要点 | 触发 |
|------|----------|------|
| `comment.created` | 评论对象(含 author、reactions、triggered_execution_ids) | 评论发表(含 agent 回流评论) |
| `comment.updated` | 评论 id + 变更字段 | 编辑 |
| `comment.deleted` | 评论 id | 软删除 |
| `comment.resolved` | 评论 id + resolved_at/by | 解决/重开 |
| `reaction.changed` | 评论 id + 反应聚合 | 增/减反应 |
| `notification.created` | 通知对象 | fan-out 生成新通知(直接进收件箱) |
| `notification.read` | 通知 id + read_at | 标已读(多端同步) |
| `inbox.unread_count` | count | 未读计数变更(多端同步红点) |
| `execution.queued` / `execution.completed` | execution id + status + comment_id | 提及 agent 入队/回流(事件词汇见 README §6.7 注册表与 runtime.md,核心差异) |
| `execution.failed` / `execution.awaiting_approval` | execution id + status + reason/comment_id | 执行失败/超时与工具审批挂起回流(README §6.7 注册表;失败/超时按 critical 进收件箱,见 §4.4) |

**可靠性兜底:**
- 重连凭 `resume_from` 从 `realtime_events` 重放(README §6.7);并额外拉一次 `unread-count` 与增量列表对账,防丢事件。
- `resync_required` 时按对账 REST URL 整拉后无感恢复(README §6.12 异常态:「正在重新同步…」、对账成功后无感消失)。
- 极端降级:WebSocket 不可用时退化为短轮询 `unread-count` + 增量列表(30~60s)。

---

## 4. UI/UX

### 4.1 评论区(issue 详情主栏底部)

布局(自上而下):
1. **活动流 + 评论混合时间线**:系统活动(状态/分派变更,灰色小字,`author_kind='system'`)与用户评论(头像卡片)按时间穿插。
2. **评论卡片**:头像 | 作者名 + 身份徽标(人类/agent 区分图标,核心差异;`member_type` 为服务端快照)| 相对时间 | 「已编辑」;正文 Markdown 渲染;底部操作条(回复 / 表情 / 更多:复制链接、编辑、删除、解决线程)。
3. **线程折叠**:有回复的评论下方显示「N 条回复 ▸」,展开缩进显示回复;线程右上角「解决 / 已解决」状态。回复深度恒为 1(对回复点「回复」仍挂到线程根,UI 以 `@名字` 提示指向)。
4. **反应区**:评论下方一排 emoji chip(`👍 2`),点击增减;「+」打开 emoji 选择器。
5. **评论输入框(composer)**:底部固定,Markdown 工具条、`@` 自动补全弹层(人/agent 混排,agent 项标注「**发布后将触发一次运行**」)、附件拖拽/粘贴(经 attachment.md 直传)、编辑/预览切换、Cmd+Enter 提交、草稿本地暂存(按 issue 维度)。

**@提及 agent 的副作用提示 UI(核心差异,必须实现,README §6.9):**
- `@` 补全弹层中,agent 项与人类项视觉区分(图标 + 标签「Agent」),并附一行说明「**发布后将触发一次运行**」。
- 选中 agent 后,composer 下方出现常驻轻提示条:「提及 @code-reviewer **发布后将触发一次运行**」——明确告知副作用,避免误触发产生成本。
- **trigger preview(提交前)**:composer 提交按钮旁展示「本次发布将触发:code-reviewer、test-runner」清单,并提供**显式抑制**开关(「仅通知,不触发运行」→ 请求体 `suppress_triggers: true`)。
- 提交后,对应位置出现「⏳ code-reviewer 正在执行…」占位卡片;执行完成替换为 agent 评论(经 `comment.created` 推送),失败显示失败占位 + 「重试」入口。

### 4.2 收件箱(顶栏铃铛 + 独立页/抽屉)

- **顶栏铃铛**:未读红点 + 数字徽标;点击下拉最近若干条,底部「查看全部」进收件箱页。
- **收件箱页(或右侧抽屉)**:
  - 顶部筛选 tabs:`全部 / 未读 / 提及我的 / 分派 / Agent`(Agent 单列 tab,核心差异)。
  - 列表按 issue 分组:组头为 issue 标识 + 标题(组头提供「不再关注此 issue」一键静音,README §6.13);组内为通知行(actor 头像、动作描述、预览文本、相对时间、未读圆点)。
  - 行操作(hover 出现):标已读 / 归档 / 跳转。
  - 顶部工具条:「全部已读」「归档已读」。
- **空状态**:插画 + 「收件箱已清空」。
- **通知偏好(Settings → Notifications)**:矩阵表格(行=事件类型,列=站内开关 + 邮件策略 无/实时/摘要);「Agent 执行通知」单独分区(核心差异);全局免打扰时段(quiet hours,标注 critical 事件穿透)、邮件摘要频率。

### 4.3 关键交互流程

**发表评论:**
1. composer 输入,`@` 触发补全:即搜 workspace 成员 + agent,键盘选择,回车插入提及 chip。
2. 选中 agent 出现副作用提示与 trigger preview(见 4.1);可勾选「仅通知,不触发运行」。
3. 提交:前端乐观更新(评论以 `sending` 出现)→ 服务端落库 → WebSocket 回广播 → 更新最终态;失败标红「重试」。
4. 服务端:解析 Markdown → 净化 HTML(白名单防 XSS)→ 提取提及落 `comment_mentions` → 对新增 agent 提及经 outbox 入队执行(README §6.6/§6.9;`suppress_triggers=true` 时跳过)→ fan-out 通知 → 推送 `comment.created`。
5. 草稿:本地暂存,回来恢复,提交成功清除。

**处理收件箱:**
1. 新通知经 WebSocket 到达:铃铛 +1,可选桌面 toast(尊重免打扰;critical 事件穿透 quiet hours)。
2. 点击通知 → 直达评论锚点并高亮闪烁;同时自动标已读。
3. 批量「全部已读 / 归档已读」;按 issue 分组降噪;组头一键静音。
4. 离线累积:登录后一次性拉未读;邮件按偏好发实时或摘要。

### 4.4 通知生成与去噪规则(README §6.13 唯一权威,必须实现)

- **投递经 outbox**:评论创建/提及/分派/状态变更在业务事务内**同事务写 `outbox_events`**(README §6.6),relay 分发至通知 fan-out worker,**不阻塞主请求**,杜绝「业务已提交但通知未登记」的丢失(集成测试 T5)。
- **默认订阅**:创建者(reason=`creator`)、assignee(reason=`assignee`)自动订阅;发过评论者自动订阅(`participated`);被 @ 自动订阅(`mentioned`);可手动订阅/取消(`manual`)。
- **按 issue 静音**:`issue_subscriptions.muted=true` 保留订阅但不出通知;收件箱提供「不再关注此 issue」一键静音。
- **重新置未读**:同组通知已读后,**仅新的 critical 事件(执行失败/超时、审批请求、安全隔离、被分派、被 @)重新置未读**;**执行成功不重置未读**;同类计数累加(如又多了 3 条评论)**不重新置未读**。
- **分组与归档**:按 `group_key`(issue+type)折叠;已读 + 过期组自动归档;`archived_at` 语义为移出主视图,可回查。
- **quiet hours**:用户级免打扰时段(站内不弹窗、邮件合并到时段后摘要);**critical 事件穿透免打扰**。
- **事件分级(引用 README §6.13 唯一权威)**:critical/normal 分级与「进收件箱 / 穿透 quiet hours / 重置未读」规则以 **README §6.13 唯一优先级矩阵**为唯一权威,本模块按其生成与分发,不另行定义(摘要:critical=执行失败/超时、审批请求、安全隔离、被分派、被 @;normal=其余;普通日志/阶段进度/presence 为非通知事件,留运行页/实时频道)。
- **执行成功默认不进收件箱(README §6.13 R2)**:`execution_finished` 成功事件仅在 `notification_preferences` 显式订阅时投递(进箱后亦按普通事件不重置已读组);runtime.md 终态通知分发与本模块同源(均按 §6.13 矩阵),不再各自定义。
- **聚合窗口**:同 `group_key` **60s 窗口**内合并为一条(`payload.count` 递增),避免通知风暴。
- **自我抑制**:动作发起者不给自己生成通知;agent 永不接收会再触发自己的通知(回环防护)。
- **邮件摘要**:定时任务扫描 `email='digest'` 且未投递的站内通知,聚合摘要邮件,写 `notification_delivery` 防重(`uq_delivery`);点邮件链接回站内并标已读。**邮件中的评论预览内容必须做 HTML 转义**(防邮件端注入),摘要模板使用纯文本或严格净化后的 HTML。

---

## 5. 验收标准

### 5.1 功能 — 评论

- [ ] 在 issue 下发表、编辑、软删除评论;编辑留「已编辑」与时间;删除留占位保线程完整。
- [ ] 回复深度恒为 1;`thread_root_id` 正确填充;`reply_count` 与线程拉取正确。
- [ ] 解决/重开线程留痕(`resolved_at`/`resolved_by_id`),折叠/展开正常。
- [ ] Markdown 全量渲染;`body_html` 经白名单净化,无 XSS(脚本/事件属性被剥离)。
- [ ] 表情回应增删,`uq_reaction` 生效;计数与 `reacted_by_me` 正确。
- [ ] 评论深链可复制、跳转并高亮。
- [ ] 系统活动以 `author_kind='system'`(`author_id` 为 NULL)评论呈现,只读,不接受 API 创建;**存储层无 human\|agent 判别列**,`member_type` 一律为 JOIN `members` 的响应快照。
- [ ] 草稿本地暂存,刷新/切走不丢,提交后清除。

### 5.2 功能 — 提及与 agent 执行(核心差异)

- [ ] 提及由服务端从 Markdown 解析为准;客户端伪造 mentions 无效。
- [ ] `@`人类:被提及者收到 `mentioned` 通知并自动加入订阅(reason=`mentioned`)。
- [ ] `@`agent 且调用者有触发权限:入队**一次**执行(`task_executions`,`trigger='mention'`),`triggered_execution_ids` 回填,`triggered_execution_id` 落库(复合 FK)。
- [ ] `@`agent 无触发权限:返回 `forbidden`/`mention_invalid`,不入队。
- [ ] 一条评论 @ 同一 agent 两次:仅入队一次执行(`uq_mentions`)。
- [ ] 执行结果以 agent 评论回流**原线程**,触发者加入订阅并收到 `execution_finished` 通知与 `execution.completed` 事件。
- [ ] 回复 agent 评论但不再 @ 它:不触发新执行(不提及即结束)。
- [ ] agent 不接收会再触发自己的通知;agent 互 @ 受 `MAX_AGENT_CHAIN_DEPTH` 保护,超限静默丢弃并审计。
- [ ] 执行失败:评论区失败占位卡片 + 通知触发者;不自动重试入队。**失败/超时按 critical 通知**(进收件箱 + 穿透 quiet hours + 重置同组未读,README §6.13)。
- [ ] **README §6.9 触发矩阵逐行可测(集成测试 T7)**:重复 @ 同一评论仅一次执行;**编辑评论新增 @ 仅为新增者入队**;无关文字编辑**不重复触发**;移除 @ **不取消在途执行**(提及记录软删除);`suppress_triggers: true` 仅通知不运行;运行中新评论 @ 同一 agent 入队新执行。

### 5.3 功能 — 收件箱 / 通知(README §6.13)

- [ ] 被分派/被提及/订阅更新/我创建的 issue 更新/agent 执行完成 均正确生成通知。
- [ ] **默认订阅**正确:creator/assignee 自动订阅,participated(发过评论)、mentioned(被 @)自动订阅,可手动订阅/取消。
- [ ] **按 issue 静音**:`muted=true` 保留订阅但不出通知;「不再关注此 issue」一键静音可用。
- [ ] **重新置未读**:已读组仅新的 **critical 事件(执行失败/超时、审批请求、安全隔离、被分派、被 @)**重新置未读;**执行成功不重置未读**;**同类计数累加不重新置未读**。
- [ ] **事件分级**:按 **README §6.13 唯一矩阵(§9 T25)**——成功默认留运行页、失败/超时 critical 穿透 quiet hours 并重置未读、审批 critical、cancelled 不通知发起者;本模块不另行定义分级。
- [ ] **`notifications.priority` 字段按矩阵派生**:每条通知落库携带服务端按 README §6.13 派生的 `priority ∈ {critical, normal}`,分发(进箱/穿透/重读)以该字段为准。
- [ ] **同 issue 父评论约束(README §6.2 第 7 条)**:跨 issue 的 `parent_id`/`thread_root_id` INSERT 被重叠复合 FK `(workspace_id, issue_id, parent_id/thread_root_id)` 拒绝(`UNIQUE (workspace_id, issue_id, id)` 供引用)。
- [ ] **quiet hours** 生效(站内不弹窗、邮件合并到时段后摘要),且 **critical 事件穿透免打扰**。
- [ ] **聚合窗口 = 60s**:同 `group_key` 60s 窗口内合并为一条,`payload.count` 递增。
- [ ] `payload` 快照完整:删除源评论/issue 后通知仍可读,跳转目标缺失时提示「原内容已删除」。
- [ ] 单条已读/未读、全部已读、归档、归档已读;未读计数徽标准确;`archived_at` 为移出主视图(可回查)。
- [ ] 按 issue 分组(整体游标,README §6.14)、按类型筛选(含 Agent tab)正确。
- [ ] 偏好矩阵生效:`in_app=false` 不出站内;`email` 策略(none/realtime/digest)正确;`muted` 订阅不出通知。
- [ ] **自我抑制**:发起者不给自己生成通知;agent 不接收会再触发自己的通知。
- [ ] 邮件摘要任务幂等(`uq_delivery`),失败可重试。
- [ ] 点击通知直达评论锚点并高亮,自动标已读。

### 5.4 非功能

- [ ] **通知不丢(outbox,集成测试 T5)**:业务事务同事务写 `outbox_events`;relay 崩溃重启后事件仍被投递(通知生成、实时事件可重放),无丢失;任一通道故障不导致通知永久丢失。
- [ ] **实时按 README §6.7**:`seq` 为频道内单调、持久化于 `realtime_events`;重连凭 `resume_from` 重放 + `unread-count` 对账;游标过旧收 `resync_required` 后 REST 对账;极端降级短轮询。
- [ ] **实时时延**:在线端从评论发表到收到 `comment.created`/`notification.created` P95 < 1s;未读徽标多端同步 P95 < 1s。
- [ ] **多端一致**:一端标已读,其余端红点消除(`notification.read` + `inbox.unread_count` 广播)。
- [ ] **性能**:issue 评论列表(顶层 + 预览回复)在万级评论量下 P95 < 300ms;未读计数走部分索引,P95 < 50ms;基准按 README §10(标注冷/热缓存)。
- [ ] **多租户隔离与复合 FK(集成测试 T1)**:`comment_mentions` / `comment_reactions` / `issue_subscriptions` / `notifications` / `notification_delivery` 等均携带 `workspace_id`,跨模块引用一律复合 FK `(workspace_id, x_id)`;A 区凭证访问 B 区评论/通知返回 403/404;构造跨 workspace 的复合 FK 插入被数据库约束拒绝。
- [ ] **真实 DELETE 行为(README §9 T18)**:`comments.author_id`/`resolved_by_id` 为 `ON DELETE RESTRICT`——成员一律经 `status='removed'` 软删除、不物理删,历史评论与解决留痕不悬空;误试物理删除被引用成员行时 DELETE 被 RESTRICT 拒绝。
- [ ] **安全**:Markdown 净化防 XSS;提及/触发权限校验;限流(auth Spec)生效;错误信息不泄露堆栈。
- [ ] **可观测**:agent 执行入队/回流/失败、回环抑制丢弃均有审计日志。
