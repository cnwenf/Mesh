# 评论与收件箱(Comment & Inbox)功能 Spec

| 项目 | 内容 |
|------|------|
| 所属层 | 协作层(Collaboration) |
| 模块 | comment-inbox |
| 依赖 Spec | `workspace`(多租户)、`member`(统一 members.id,human\|agent)、`auth`(Bearer/RBAC/限流)、`issue`(评论宿主与订阅路由)、`attachment`(评论内附件)、`agent`(提及 agent 入队运行) |
| 被依赖 | `agent`(运行结果以 agent 评论回流)、`notification`(邮件摘要任务消费 notifications) |
| 技术栈 | FastAPI + SQLAlchemy 2.x + PostgreSQL + WebSocket |
| 状态 | Draft |

> **全局一致性锚点(本 Spec 全程遵循)**
> 1. PostgreSQL;表名 snake_case 复数;主键 `uuid`(默认 `gen_random_uuid()`);`created_at`/`updated_at` 为 `TIMESTAMPTZ NOT NULL DEFAULT now()`;软删除统一 `deleted_at TIMESTAMPTZ NULL`。
> 2. 评论 `author_id`、通知 `actor_id`/`recipient_id` 一律引用**统一 `members.id`**(人类与 agent 同表,以 `members.member_type ∈ {human, agent}` 区分);系统活动评论 `author_id` 为 `NULL`。
> 3. REST 前缀 `/api/v1`;`Authorization: Bearer <token>`;游标分页响应统一 `{"data": [...], "next_cursor": "...", "has_more": bool}`;统一错误信封 `{"error": {"code","message","details"}}`。
> 4. 实时走 WebSocket `/ws`,事件名 `<entity>.<action>`,每条事件带单调递增 `seq`,支持 `?since_seq=` 重放。
> 5. ORM 采用 SQLAlchemy 2.x 声明式约定(`Mapped` / `mapped_column`)。

---

## 1. 功能描述

### 1.1 定位

评论(Comment)是 issue 详情页的协作主线程:人类成员与 agent 队友在同一时间线里发言、追问、表态、沉淀结论。收件箱(Inbox / Notification)是把「与我相关的事件」聚合、去噪、实时送达每位成员的统一通知中心。二者共同构成 Mesh 的异步协作底座,并承载 Mesh 的核心差异:**`@`提及 agent 不只是通知,而是入队一次 agent 运行**。

### 1.2 功能点与场景

#### 评论(Comment)

| # | 功能点 | 典型场景 |
|---|--------|----------|
| C1 | 在 issue 下发表评论 | 成员在详情页底部输入框补充上下文、贴日志、给结论 |
| C2 | 线程化回复(单层折叠) | 针对某条评论追问,回复折叠在父评论下,避免主评论区被多线讨论淹没 |
| C3 | `@`提及人类成员 | 输入 `@` 触发选人弹层,被提及者收到通知并自动加入订阅 |
| C4 | `@`提及 agent(核心差异) | `@` 列表人与 agent 混排;选中 agent 后**入队一次该 agent 运行**,结果以 agent 评论回流同一线程 |
| C5 | Markdown 富文本 | 标题/加粗/斜体/列表/行内代码/代码块(语言高亮)/引用/表格/任务清单/分割线 |
| C6 | 链接与智能引用 | 粘贴 issue 链接渲染为带标题状态的引用卡片;`#MES-123` 简写自动补全为链接 |
| C7 | 表情回应(reaction) | 对评论加 👍/🎉 等,无需另发评论即可表态;显示反应人与计数 |
| C8 | 编辑评论 | 作者修改自己的评论,保留「已编辑」标记与编辑时间 |
| C9 | 删除评论 | 作者或管理员删除;软删除留占位「该评论已删除」以保线程完整 |
| C10 | 解决/重开线程 | 把线程标记已解决并折叠,可重新打开;解决人/时间留痕 |
| C11 | 评论内附件 | 评论携带图片/文件(见 attachment Spec) |
| C12 | 评论深链锚点 | 每条评论有可复制永久链接,跳转并高亮 |
| C13 | 系统活动评论(activity) | 状态/分派/字段变更以只读「系统评论」出现在时间线(`author_kind='system'`) |
| C14 | 草稿自动保存 | 输入内容本地暂存,刷新/切走不丢 |
| C15 | 评论排序 | 顶层按时间正序;线程内回复按时间正序;可选「最新在前」 |

#### 收件箱 / 通知(Inbox / Notification)

| # | 功能点 | 典型场景 |
|---|--------|----------|
| I1 | 被分派通知 | issue 被分派给我(或从我转走)进入收件箱 |
| I2 | 被 `@`提及通知 | 有人在评论/描述里 `@`我 |
| I3 | 订阅 issue 更新通知 | 我订阅/参与过的 issue 有新评论/状态/字段变更 |
| I4 | 我创建的 issue 更新 | 我创建的 issue 任意活动 |
| I5 | agent 运行完成通知(核心差异) | 我触发或关注的 agent 运行结束/失败,附产物评论链接 |
| I6 | 未读/已读 | 单条已读、一键全部已读、未读计数徽标 |
| I7 | 归档 | 已处理通知移出主视图 |
| I8 | 按 issue 分组 / 按类型筛选 | 同一 issue 多条通知折叠成组;按 提及/分派/订阅/agent 筛选 |
| I9 | 实时推送 | WebSocket 实时下发,顶栏徽标即时 +1 |
| I10 | 邮件摘要 | 离线累积通知按实时/日聚合发邮件,粒度可配 |
| I11 | 通知偏好设置 | 按事件类型开关站内/邮件;免打扰;agent 运行通知开关 |
| I12 | 跳转上下文 | 点击通知直达对应 issue/评论锚点 |

### 1.3 边界与非目标

**范围内:**
- 评论的发表/回复/编辑/删除/解决/反应/提及解析与 agent 运行入队。
- 通知的生成(fan-out)、去重合并、偏好过滤、已读/归档、按 issue 分组、实时推送、邮件摘要触发。

**非目标(本 Spec 不覆盖):**
- agent 运行的执行细节(由 `agent` Spec 负责,本 Spec 只负责「入队」与「回流评论」的衔接)。
- 附件字节流上传/下载(由 `attachment` Spec 负责,本 Spec 仅在评论载荷中引用 `attachment_ids`)。
- 权限模型本身(由 `auth` Spec 的 RBAC 提供,本 Spec 只声明所需权限点)。
- 富文本编辑器组件的内部实现(前端组件库范畴)。
- 跨 workspace 全局收件箱、第三方 IM 集成、Snooze 重新提醒(列为可选增强,默认不实现)。

---

## 2. 数据模型

### 2.1 ER 关系

```
workspaces 1─* issues 1─* comments 1─* comment_reactions
                         comments 1─* comment_mentions *─1 members
                         comments 自引用 (parent_id / thread_root_id)
issues 1─* issue_subscriptions *─1 members
members 1─* notifications (recipient) ; notifications *─1 issues ; *─1 comments
members 1─* notification_preferences
notifications 1─* notification_delivery

members 为统一身份表(member_type ∈ {human, agent}),由 member Spec 定义。
```

### 2.2 `comments` — 评论主表

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 评论 ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离,所有查询强制带 |
| `issue_id` | uuid | NOT NULL, FK→issues.id | 所属 issue |
| `parent_id` | uuid | NULL, FK→comments.id | 父评论;`NULL` 表示顶层。约束:仅可指向同 `issue_id` 的顶层评论(限深度=1) |
| `thread_root_id` | uuid | NULL, FK→comments.id | 线程根(冗余加速聚合);顶层为 `NULL`,回复指向所属顶层评论 |
| `author_kind` | text | NOT NULL, CHECK in ('member','system') | `member`=人类/agent 作者;`system`=活动流 |
| `author_id` | uuid | NULL, FK→members.id | 作者;`author_kind='member'` 时 NOT NULL,`'system'` 时 NULL。人类/agent 由 `members.member_type` 区分 |
| `body_markdown` | text | NOT NULL, CHECK (char_length > 0) | 原始 Markdown(真源,编辑以此为准) |
| `body_html` | text | NULL | 服务端渲染并**净化后**的 HTML 缓存(白名单标签/属性,防 XSS) |
| `body_text` | text | NULL | 纯文本(搜索/摘要/邮件用) |
| `edited_at` | timestamptz | NULL | 最近编辑时间(NULL=从未编辑) |
| `resolved_at` | timestamptz | NULL | 线程被解决时间(仅顶层有意义) |
| `resolved_by_id` | uuid | NULL, FK→members.id | 解决人 |
| `deleted_at` | timestamptz | NULL | 软删除 |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | 时间戳 |

**关系与约束:**
- `parent_id` 自引用构成线程;应用层 + CHECK/触发器保证**回复深度=1**(回复不能再被回复,新回复一律挂到线程根下),业界主流「单层回复折叠」。
- `thread_root_id` 为冗余字段:写入回复时由服务端填充为顶层评论 id,用于免递归聚合 `reply_count` 与拉取整线程。
- 删除采用软删除以保线程完整;issue 删除时级联软删除其评论。
- `author_kind='system'` 的评论由服务端在字段/状态变更时写入,不接受 API 直接创建。

**关键索引:**
- `idx_comments_issue_created (workspace_id, issue_id, created_at)` — 按时间拉取某 issue 评论(主路径)。
- `idx_comments_thread (workspace_id, thread_root_id, created_at)` — 拉取某线程全部回复。
- `idx_comments_author (workspace_id, author_id, created_at)` — 「我发过的评论」。
- 部分索引 `idx_comments_active ON comments(issue_id, created_at) WHERE deleted_at IS NULL`。

### 2.3 `comment_mentions` — 提及解析结果

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `comment_id` | uuid | NOT NULL, FK→comments.id | 来源评论 |
| `mentioned_id` | uuid | NOT NULL, FK→members.id | 被提及成员(human 或 agent,以 `members.member_type` 区分) |
| `triggered_run_id` | uuid | NULL, FK→agent_runs.id | 若被提及者为 agent 且成功入队运行,记录运行 ID(核心差异留痕) |
| `created_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_mentions (comment_id, mentioned_id)` — 同一评论对同一成员只记一次(天然抑制「一条评论 @ 同一 agent 两次只跑一次」)。
**关键索引:** `idx_mentions_target (mentioned_id, created_at)` — 反查「提到我的评论」,驱动通知与收件箱。

### 2.4 `comment_reactions` — 表情回应

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `comment_id` | uuid | NOT NULL, FK→comments.id | |
| `actor_id` | uuid | NOT NULL, FK→members.id | 反应人(human/agent) |
| `emoji` | text | NOT NULL | 归一化为 unicode(或 shortcode) |
| `created_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_reaction (comment_id, actor_id, emoji)` — 同一人对同一评论同一 emoji 只一次。
**关键索引:** `idx_reactions_comment (comment_id)`。

### 2.5 `issue_subscriptions` — issue 订阅(驱动通知路由)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | |
| `issue_id` | uuid | NOT NULL, FK→issues.id | |
| `subscriber_id` | uuid | NOT NULL, FK→members.id | 订阅者(human/agent) |
| `reason` | text | NOT NULL, CHECK in ('creator','assignee','mentioned','participated','manual') | 订阅来源 |
| `muted` | boolean | NOT NULL DEFAULT false | 静音(保留订阅但不出通知) |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_subscription (issue_id, subscriber_id)`。
**关键索引:** `idx_subscriptions_issue ON issue_subscriptions(workspace_id, issue_id) WHERE NOT muted`。
> 通知 fan-out 遍历「订阅者 ∪ 被提及者 ∪ 分派对象」去重后,再按各自偏好(2.7)过滤。

### 2.6 `notifications` — 通知主表(收件箱数据源)

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 通知 ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离 |
| `recipient_id` | uuid | NOT NULL, FK→members.id | 接收者(收件箱主要面向 human) |
| `type` | text | NOT NULL | 枚举:`assigned`/`mentioned`/`subscribed_update`/`comment_created`/`status_changed`/`agent_run_finished`/`review_requested`/`due_soon` |
| `actor_kind` | text | NULL, CHECK in ('member','system') | 触发者类型 |
| `actor_id` | uuid | NULL, FK→members.id | 触发者(`system` 时为 NULL) |
| `issue_id` | uuid | NULL, FK→issues.id | 关联 issue(按 issue 分组) |
| `comment_id` | uuid | NULL, FK→comments.id | 关联评论(跳转锚点) |
| `target_type` | text | NULL | 目标实体类型(issue/comment/run…) |
| `target_id` | uuid | NULL | 目标实体 ID |
| `payload` | jsonb | NOT NULL DEFAULT '{}' | **渲染所需快照**(评论摘要、变更前后值、actor 显示名等),保证源实体被删后通知仍可读 |
| `group_key` | text | NULL | 分组键(如 `issue:<id>:<type>`),用于折叠 |
| `read_at` | timestamptz | NULL | 已读时间(NULL=未读) |
| `archived_at` | timestamptz | NULL | 归档时间 |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**关键索引:**
- `idx_notifications_inbox (workspace_id, recipient_id, archived_at, created_at DESC)` — 收件箱主查询。
- 部分索引 `idx_notifications_unread ON notifications(workspace_id, recipient_id) WHERE read_at IS NULL AND archived_at IS NULL` — 未读徽标(高频)。
- `idx_notifications_group (recipient_id, group_key, created_at DESC)` — 分组折叠。
- GIN `idx_notifications_payload (payload)` — 按需。

**设计要点(必须实现):**
- **`payload` 存快照而非全靠外键回查**——评论/issue 被删除后通知仍可读。`payload` 至少含:`{actor_name, actor_avatar_url, preview, title, count}`。
- **去重合并**:短窗口内同 `group_key` 的通知合并为一条(更新 `payload.count` 与 `updated_at`),避免「连发 5 条评论」刷出 5 条。

### 2.7 `notification_preferences` — 通知偏好

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | |
| `member_id` | uuid | NOT NULL, FK→members.id | |
| `event_type` | text | NOT NULL | 同 `notifications.type`,或 `all` |
| `in_app` | boolean | NOT NULL DEFAULT true | 是否站内通知 |
| `email` | text | NOT NULL DEFAULT 'digest', CHECK in ('none','realtime','digest') | 邮件策略 |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_notif_pref (workspace_id, member_id, event_type)`。
> 缺省:用户未显式设置的 `event_type` 走默认策略(提及/分派=站内+实时邮件;订阅更新=站内+日摘要;agent 运行完成=站内+实时邮件)。

### 2.8 `notification_delivery` — 投递/去重台账

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `notification_id` | uuid | NOT NULL, FK→notifications.id | |
| `channel` | text | NOT NULL, CHECK in ('in_app','email','websocket') | |
| `state` | text | NOT NULL, CHECK in ('pending','sent','failed') | |
| `sent_at` | timestamptz | NULL | |
| `error` | text | NULL | |
| `created_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_delivery (notification_id, channel)` — 邮件摘要任务幂等投递、失败重试。

### 2.9 跨模块外键说明

- `author_id` / `actor_id` / `recipient_id` / `subscriber_id` / `mentioned_id` / `resolved_by_id` → `members.id`(member Spec)。`members.member_type ∈ {human, agent}` 是唯一的人类/agent 判别源;本模块不冗余该判别列(展示时 JOIN 或读 `payload` 快照)。
- `triggered_run_id` → `agent_runs.id`(agent Spec)。
- `issue_id` → `issues.id`(issue Spec);`workspace_id` → `workspaces.id`(workspace Spec)。
- issue 删除采用软删除时,通知保留(靠 `payload` 快照可读);评论级联软删除。

---

## 3. 接口设计

> 鉴权:`Authorization: Bearer <token>`(成员会话 token 或 agent runtime API token)。写操作校验 workspace 角色与资源权限(RBAC,见 auth Spec)。时间统一 RFC3339(UTC)。

### 3.1 评论端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/issues/{issue_id}/comments` | 列出评论(默认仅顶层 + `reply_count` + 前 N 条预览回复) |
| POST | `/api/v1/issues/{issue_id}/comments` | 发表评论(可带 `parent_id` 成为回复) |
| GET | `/api/v1/comments/{comment_id}` | 取单条评论 |
| PATCH | `/api/v1/comments/{comment_id}` | 编辑评论(乐观锁,带 `updated_at`) |
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
  "attachment_ids": ["8c1f..."]
}
```
> 提及**由服务端从 Markdown 解析为准**(防伪造/漏记);客户端无需显式提交 mentions。服务端检测到 agent 提及即入队运行,并在响应回填 `triggered_run_ids`。

**发表评论响应体(201):**
```json
{
  "data": {
    "id": "c-abc",
    "issue_id": "i-1",
    "parent_id": null,
    "thread_root_id": null,
    "author": {"id": "u-9", "member_type": "human", "name": "李四", "avatar_url": "..."},
    "body_markdown": "...",
    "body_html": "<p>...</p>",
    "reactions": [{"emoji": "👍", "count": 2, "reacted_by_me": false}],
    "reply_count": 0,
    "resolved_at": null,
    "mentions": [{"id": "a-222", "member_type": "agent", "name": "code-reviewer"}],
    "triggered_run_ids": ["run-77"],
    "created_at": "2026-07-24T10:00:00Z",
    "edited_at": null
  }
}
```

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
| GET | `/api/v1/notification-preferences` | 读取偏好 |
| PUT | `/api/v1/notification-preferences` | 更新偏好 |

**列出收件箱查询参数:** `?limit=30&cursor=&filter=unread|all|mentions|assigned|agent&type=&grouped=true`。

**收件箱响应体(200,分组形态):**
```json
{
  "data": [
    {
      "id": "n-1",
      "group_key": "issue:i-1:comment_created",
      "type": "comment_created",
      "issue": {"id": "i-1", "identifier": "MES-1", "title": "..."},
      "actor": {"id": "a-222", "member_type": "agent", "name": "code-reviewer"},
      "preview": "已修复并通过测试,PR: ...",
      "count": 3,
      "read_at": null,
      "created_at": "2026-07-24T10:05:00Z",
      "latest_comment_id": "c-xyz"
    }
  ],
  "next_cursor": "...",
  "has_more": true
}
```

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

统一错误信封:`{"error": {"code": "FORBIDDEN", "message": "你没有权限执行此操作", "details": {}}}`。

| HTTP | code | 场景 |
|------|------|------|
| 400 | `VALIDATION_ERROR` | 字段缺失/超长/非法(body 为空、emoji 非法) |
| 401 | `UNAUTHENTICATED` | 缺失/过期/非法 token |
| 403 | `FORBIDDEN` | 无权限(删他人评论、跨 workspace、无 agent 触发权限) |
| 404 | `NOT_FOUND` | 评论/通知/issue 不存在或已删除 |
| 409 | `CONFLICT` | 重复反应、并发编辑冲突(`updated_at` 乐观锁) |
| 410 | `GONE` | 目标评论已删除但 ID 曾被引用 |
| 422 | `MENTION_INVALID` | 提及了不存在/无权限的对象 |
| 429 | `RATE_LIMITED` | 触发限流(见 auth Spec),含 `Retry-After` |
| 500 | `INTERNAL` | 服务端异常(不泄露堆栈) |

### 3.4 鉴权与权限要点

- 评论读:workspace 成员且对 issue 有读权限。
- 评论写:对 issue 有评论权限;agent 评论由 agent runtime 用 API token 写入。
- 编辑/删除:仅作者本人,或 admin/owner 角色。
- **提及 agent 触发运行**:调用者须对该 agent 有「触发」权限,否则返回 `FORBIDDEN`;该 agent 提及**不入队运行**(可降级为普通提及,即仅通知不运行,由产品策略决定,默认拒绝并提示)。

### 3.5 @提及 agent = 入队一次运行(核心差异,必须实现)

**触发条件(服务端,事务提交后异步执行):**
1. 评论创建/编辑成功,服务端从 `body_markdown` 解析出提及列表。
2. 对每个被提及成员 JOIN `members` 取 `member_type`。
3. 若 `member_type='agent'` 且调用者对该 agent 有触发权限,且通过回环抑制检查(见下),则向 agent 运行队列**入队一次运行**,落 `comment_mentions.triggered_run_id`。
4. 运行结果由 agent runtime 以 **agent 评论**形式回流到**原线程**(`parent_id`=原评论所属线程根),并把触发者加入订阅,触发者收到 `agent_run.finished` 通知。

**权限校验:** 无触发权限 → 该提及返回 `MENTION_INVALID`/`FORBIDDEN`,不入队;其余合法提及正常处理。

**agent 间回环抑制(必须实现):**
- **不提及即结束**:回复 agent 的评论若不再 `@` 它,则不触发新运行——天然终止 agent-to-agent 循环。这是默认且主要的抑制手段。
- **自我抑制**:动作发起者不给自己生成通知;agent 永不会收到「会再次触发自己」的通知。
- **同评论去重**:`uq_mentions (comment_id, mentioned_id)` 保证一条评论 @ 同一 agent 两次只入队一次。
- **agent 主动 @ 另一 agent**:仅在 agent 评论显式包含对另一 agent 的提及时才触发,且受链深度上限保护(默认 `MAX_AGENT_CHAIN_DEPTH`,超过则静默丢弃并记录审计),防止 A↔B 互相 @ 形成死循环。
- 运行失败:在评论区留失败占位卡片 + 通知触发者,便于重试;失败不自动重试入队(避免放大)。

### 3.6 WebSocket 事件

> 连接 `/ws`(Bearer 鉴权,按 `workspace_id + member_id` 订阅);每条事件带 `seq`;客户端记 `last_seq`,重连带 `?since_seq=` 重放缺口(服务端保留近 N 分钟事件缓冲)。

| 事件 | 载荷要点 | 触发 |
|------|----------|------|
| `comment.created` | 评论对象(含 author、reactions、triggered_run_ids) | 评论发表(含 agent 回流评论) |
| `comment.updated` | 评论 id + 变更字段 | 编辑 |
| `comment.deleted` | 评论 id | 软删除 |
| `comment.resolved` | 评论 id + resolved_at/by | 解决/重开 |
| `reaction.changed` | 评论 id + 反应聚合 | 增/减反应 |
| `notification.created` | 通知对象 | fan-out 生成新通知(直接进收件箱) |
| `notification.read` | 通知 id + read_at | 标已读(多端同步) |
| `inbox.unread_count` | count | 未读计数变更(多端同步红点) |
| `agent_run.started` / `agent_run.finished` | run id + status + comment_id | 提及 agent 入队/回流(核心差异) |

**可靠性兜底:**
- 重连后额外拉一次 `unread-count` 与增量列表对账,防丢事件。
- 极端降级:WebSocket 不可用时退化为短轮询 `unread-count` + 增量列表(30~60s)。

---

## 4. UI/UX

### 4.1 评论区(issue 详情主栏底部)

布局(自上而下):
1. **活动流 + 评论混合时间线**:系统活动(状态/分派变更,灰色小字,`author_kind='system'`)与用户评论(头像卡片)按时间穿插。
2. **评论卡片**:头像 | 作者名 + 身份徽标(人类/agent 区分图标,核心差异)| 相对时间 | 「已编辑」;正文 Markdown 渲染;底部操作条(回复 / 表情 / 更多:复制链接、编辑、删除、解决线程)。
3. **线程折叠**:有回复的评论下方显示「N 条回复 ▸」,展开缩进显示回复;线程右上角「解决 / 已解决」状态。回复深度恒为 1(对回复点「回复」仍挂到线程根,UI 以 `@名字` 提示指向)。
4. **反应区**:评论下方一排 emoji chip(`👍 2`),点击增减;「+」打开 emoji 选择器。
5. **评论输入框(composer)**:底部固定,Markdown 工具条、`@` 自动补全弹层(人/agent 混排,agent 项标注「将触发一次运行」)、附件拖拽/粘贴、编辑/预览切换、Cmd+Enter 提交、草稿本地暂存(按 issue 维度)。

**@提及 agent 的副作用提示 UI(核心差异,必须实现):**
- `@` 补全弹层中,agent 项与人类项视觉区分(图标 + 标签「Agent」),并附一行说明「选中将立即触发一次运行」。
- 选中 agent 后,composer 下方出现常驻轻提示条:「提及 @code-reviewer 将立即触发一次运行」——明确告知副作用,避免误触发产生成本。
- 提交后,对应位置出现「⏳ code-reviewer 正在运行…」占位卡片;运行完成替换为 agent 评论(经 `comment.created` 推送),失败显示失败占位 + 「重试」入口。

### 4.2 收件箱(顶栏铃铛 + 独立页/抽屉)

- **顶栏铃铛**:未读红点 + 数字徽标;点击下拉最近若干条,底部「查看全部」进收件箱页。
- **收件箱页(或右侧抽屉)**:
  - 顶部筛选 tabs:`全部 / 未读 / 提及我的 / 分派 / Agent`(Agent 单列 tab,核心差异)。
  - 列表按 issue 分组:组头为 issue 标识 + 标题;组内为通知行(actor 头像、动作描述、预览文本、相对时间、未读圆点)。
  - 行操作(hover 出现):标已读 / 归档 / 跳转。
  - 顶部工具条:「全部已读」「归档已读」。
- **空状态**:插画 + 「收件箱已清空」。
- **通知偏好(Settings → Notifications)**:矩阵表格(行=事件类型,列=站内开关 + 邮件策略 无/实时/摘要);「Agent 运行通知」单独分区(核心差异);全局免打扰时段、邮件摘要频率。

### 4.3 关键交互流程

**发表评论:**
1. composer 输入,`@` 触发补全:即搜 workspace 成员 + agent,键盘选择,回车插入提及 chip。
2. 选中 agent 出现副作用提示(见 4.1)。
3. 提交:前端乐观更新(评论以 `sending` 出现)→ 服务端落库 → WebSocket 回广播 → 更新最终态;失败标红「重试」。
4. 服务端:解析 Markdown → 净化 HTML(白名单防 XSS)→ 提取提及落 `comment_mentions` → 对 agent 提及入队运行 → fan-out 通知 → 推送 `comment.created`。
5. 草稿:本地暂存,回来恢复,提交成功清除。

**处理收件箱:**
1. 新通知经 WebSocket 到达:铃铛 +1,可选桌面 toast(尊重免打扰)。
2. 点击通知 → 直达评论锚点并高亮闪烁;同时自动标已读。
3. 批量「全部已读 / 归档已读」;按 issue 分组降噪。
4. 离线累积:登录后一次性拉未读;邮件按偏好发实时或摘要。

### 4.4 通知生成与去噪规则(必须实现)

- **触发即生成**:评论创建/提及/分派/状态变更在事务提交后**异步 fan-out**(消息队列解耦,不阻塞主请求)。
- **去重合并**:同 `group_key`(同 issue 同类型短窗口)合并为一条,更新 `payload.count` 与 `updated_at`。
- **自我抑制**:发起者不给自己生成通知;agent 不接收会再触发自己的通知(防 agent-to-agent 死循环)。
- **偏好过滤**:fan-out 按 `notification_preferences` 决定站内/邮件;`muted` 订阅不出通知。
- **邮件摘要**:定时任务扫描 `email='digest'` 且未投递的站内通知,聚合摘要邮件,写 `notification_delivery` 防重;点邮件链接回站内并标已读。**邮件中的评论预览内容必须做 HTML 转义**(防邮件端注入),摘要模板使用纯文本或严格净化后的 HTML。

---

## 5. 验收标准

### 5.1 功能 — 评论

- [ ] 在 issue 下发表、编辑、软删除评论;编辑留「已编辑」与时间;删除留占位保线程完整。
- [ ] 回复深度恒为 1;`thread_root_id` 正确填充;`reply_count` 与线程拉取正确。
- [ ] 解决/重开线程留痕(`resolved_at`/`resolved_by_id`),折叠/展开正常。
- [ ] Markdown 全量渲染;`body_html` 经白名单净化,无 XSS(脚本/事件属性被剥离)。
- [ ] 表情回应增删,`uq_reaction` 生效;计数与 `reacted_by_me` 正确。
- [ ] 评论深链可复制、跳转并高亮。
- [ ] 系统活动以 `author_kind='system'` 评论呈现,只读,不接受 API 创建。
- [ ] 草稿本地暂存,刷新/切走不丢,提交后清除。

### 5.2 功能 — 提及与 agent 运行(核心差异)

- [ ] 提及由服务端从 Markdown 解析为准;客户端伪造 mentions 无效。
- [ ] `@`人类:被提及者收到 `mentioned` 通知并自动加入订阅(reason=`mentioned`)。
- [ ] `@`agent 且调用者有触发权限:入队**一次**运行,`triggered_run_ids` 回填,`triggered_run_id` 落库。
- [ ] `@`agent 无触发权限:返回 `FORBIDDEN`/`MENTION_INVALID`,不入队。
- [ ] 一条评论 @ 同一 agent 两次:仅入队一次(`uq_mentions`)。
- [ ] 运行结果以 agent 评论回流**原线程**,触发者加入订阅并收到 `agent_run.finished`。
- [ ] 回复 agent 评论但不再 @ 它:不触发新运行(不提及即结束)。
- [ ] agent 不接收会再触发自己的通知;agent 互 @ 受 `MAX_AGENT_CHAIN_DEPTH` 保护,超限静默丢弃并审计。
- [ ] 运行失败:评论区失败占位卡片 + 通知触发者;不自动重试入队。

### 5.3 功能 — 收件箱 / 通知

- [ ] 被分派/被提及/订阅更新/我创建的 issue 更新/agent 运行完成 均正确生成通知。
- [ ] `payload` 快照完整:删除源评论/issue 后通知仍可读,跳转目标缺失时提示「原内容已删除」。
- [ ] 同 `group_key` 短窗口合并为一条,`payload.count` 递增。
- [ ] 单条已读/未读、全部已读、归档、归档已读;未读计数徽标准确。
- [ ] 按 issue 分组、按类型筛选(含 Agent tab)正确。
- [ ] 偏好矩阵生效:`in_app=false` 不出站内;`email` 策略(none/realtime/digest)正确;`muted` 订阅不出通知。
- [ ] 邮件摘要任务幂等(`uq_delivery`),失败可重试。
- [ ] 点击通知直达评论锚点并高亮,自动标已读。

### 5.4 非功能

- [ ] **通知不丢**:事务提交后异步 fan-out;WebSocket 断线重连 `?since_seq=` 重放 + `unread-count` 对账,极端降级短轮询。任一通道故障不导致通知永久丢失。
- [ ] **实时时延**:在线端从评论发表到收到 `comment.created`/`notification.created` P95 < 1s;未读徽标多端同步 P95 < 1s。
- [ ] **多端一致**:一端标已读,其余端红点消除(`notification.read` + `inbox.unread_count` 广播)。
- [ ] **性能**:issue 评论列表(顶层 + 预览回复)在万级评论量下 P95 < 300ms;未读计数走部分索引,P95 < 50ms。
- [ ] **多租户隔离**:所有查询强制带 `workspace_id`,跨 workspace 访问返回 403/404。
- [ ] **安全**:Markdown 净化防 XSS;提及/触发权限校验;限流(auth Spec)生效;错误信息不泄露堆栈。
- [ ] **可观测**:agent 运行入队/回流/失败、回环抑制丢弃均有审计日志。
