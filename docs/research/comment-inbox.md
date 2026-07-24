# 调研记录：评论与收件箱（Comment & Inbox）

> 模块簇：协作与基础能力
> 调研对象：业界主流 AI 原生团队工作区产品在「issue 评论 / 讨论线程」与「收件箱 / 通知中心」上的成熟设计。
> 说明：本文仅记录中性化的设计模式与业界标准做法，用于指导 Mesh 的 Spec 撰写；不指向任何具体产品。
> Mesh 特色标注：`[Mesh 特色]` 表示需要特别为「AI agent 作为队友」这一核心范式做的设计。

---

## 一、功能清单

### 1.1 评论（Comment）

| # | 功能点 | 典型用户场景 |
|---|--------|--------------|
| F1 | 在 issue 下发表评论 | 成员在 issue 详情页底部输入框补充上下文、贴日志、给结论 |
| F2 | 线程化回复（thread / reply） | 针对某条评论展开追问，避免主评论区被多线讨论淹没；回复折叠在父评论下 |
| F3 | `@` 提及成员（人） | 输入 `@` 触发选人弹层，被提及者收到收件箱通知并被加入订阅 |
| F4 | `@` 提及 agent `[Mesh 特色]` | `@` 列表里 agent 与人类成员并列出现；选中 agent 后**入队一次该 agent 的运行**（而非仅通知），运行结果以 agent 评论形式回流到同一线程 |
| F5 | Markdown 富文本支持 | 标题、加粗/斜体、有序/无序列表、行内代码、代码块（带语言高亮）、引用、表格、任务清单、分割线 |
| F6 | 链接与智能引用 | 粘贴 issue 链接自动渲染为带标题/状态的引用卡片；`#MES-123` 形式的简写自动补全为链接 |
| F7 | 表情回应（reaction） | 对评论加 👍/🎉 等 emoji，无需另发一条评论即可表态；显示反应人与计数 |
| F8 | 编辑评论 | 作者修改自己刚发的评论，保留「已编辑」标记与编辑时间 |
| F9 | 删除评论 | 作者或管理员删除评论；删除后留占位（「该评论已删除」）以保线程完整，或硬删除 |
| F10 | 解决（resolve）线程 | 把一条讨论线程标记为已解决，折叠收起；可「重新打开」；解决人/时间留痕 |
| F11 | 评论内附件 | 评论携带图片/文件（拖拽/粘贴/选择），见 attachment 模块 |
| F12 | 置顶 / 精选评论 | 把关键结论置顶到评论区顶部（可选增强项） |
| F13 | 评论锚点 / 深链 | 每条评论有可复制的永久链接，跳转到该评论并高亮 |
| F14 | 系统事件评论（activity） | 状态变更、分派、字段修改等以「系统评论/活动流」形式出现在时间线（只读，区别于用户评论） |
| F15 | 草稿自动保存 | 输入中内容本地暂存，刷新/切走不丢 |
| F16 | 评论排序 | 按时间正序为主；线程内回复按时间正序；可选「最新在前」 |

### 1.2 收件箱 / 通知中心（Inbox / Notification）

| # | 功能点 | 典型用户场景 |
|---|--------|--------------|
| N1 | 通知生成：被分派 | issue 被分派给我（或从我转走）时进入收件箱 |
| N2 | 通知生成：被 `@` 提及 | 有人在评论/描述里 `@` 我 |
| N3 | 通知生成：订阅 issue 有更新 | 我订阅（或参与过）的 issue 有新评论 / 状态变更 / 字段变更 |
| N4 | 通知生成：我创建的 issue 有更新 | 我创建的 issue 任意活动 |
| N5 | 通知生成：被请求评审 / 阻塞 / 截止临近 | 协作增强类通知（可选） |
| N6 | 通知生成：agent 运行完成 `[Mesh 特色]` | 我触发或关注的 agent 运行结束/失败，附带产物评论链接 |
| N7 | 未读 / 已读 | 单条标记已读；一键全部已读；未读计数徽标 |
| N8 | 归档（archive） | 把已处理通知移出收件箱主视图 |
| N9 | 按类型 / 来源分组 | 按 issue 聚合（同一 issue 的多条通知折叠成一组），按类型筛选（提及/分派/订阅/agent） |
| N10 | 实时推送 | WebSocket 实时下发新通知，顶栏徽标即时 +1 |
| N11 | 邮件摘要 | 离线期间累积通知按日/实时聚合发邮件；可在设置里调粒度 |
| N12 | 通知偏好设置 | 按事件类型开关站内/邮件；免打扰；agent 运行通知开关 `[Mesh 特色]` |
| N13 | 标记稍后处理 / Snooze | 暂时收起，到点重新提醒（可选增强项） |
| N14 | 跳转上下文 | 点击通知直达对应 issue / 评论锚点 |

---

## 二、数据模型

> 约定：PostgreSQL；UUID 主键（`uuid_generate_v4()` 或 `gen_random_uuid()`）；所有表含 `created_at`/`updated_at`（`timestamptz`，默认 `now()`）；软删除统一 `deleted_at timestamptz null`；REST + JSON；游标分页；Bearer token 鉴权；实时走 WebSocket。

### 2.1 `comments` — 评论主表

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 评论 ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces | 所属工作区（多租户隔离，所有查询强制带） |
| `issue_id` | uuid | NOT NULL, FK→issues | 所属 issue |
| `parent_id` | uuid | NULL, FK→comments.id | 父评论（线程回复）；`NULL` 表示顶层评论 |
| `thread_root_id` | uuid | NULL, FK→comments.id | 线程根（冗余字段，加速线程聚合查询；顶层为自身或 NULL） |
| `author_type` | text | NOT NULL, CHECK in ('member','agent','system') | 作者类型 `[Mesh 特色]`：支持 member / agent / system |
| `author_id` | uuid | NOT NULL | 作者 ID（按 author_type 解释；system 可为固定值） |
| `body_markdown` | text | NOT NULL | 原始 Markdown 文本（真源，编辑以此为准） |
| `body_html` | text | NULL | 服务端渲染并**净化后**的 HTML（可选缓存，避免重复渲染） |
| `body_text` | text | NULL | 纯文本（用于搜索/摘要/邮件） |
| `edited_at` | timestamptz | NULL | 最近编辑时间（NULL 表示从未编辑） |
| `resolved_at` | timestamptz | NULL | 线程被解决时间（仅 thread root 有意义） |
| `resolved_by_id` | uuid | NULL | 解决人 |
| `deleted_at` | timestamptz | NULL | 软删除 |
| `created_at` / `updated_at` | timestamptz | NOT NULL, default now() | 时间戳 |

**关系与约束：**
- `comments.parent_id → comments.id`：自引用，构成线程；建议约束 `parent_id` 只能指向同 `issue_id` 的顶层或一级回复（限制线程深度为 1～2 层，业界主流做法是「单层回复折叠」，避免无限嵌套）。
- 唯一约束建议：无强唯一；但可对「同一作者对同一评论的同一 emoji 反应」在 reactions 表加唯一约束（见 2.4）。
- 删除策略：评论删除采用软删除以保线程完整；issue 删除时级联软删除其评论。

**关键索引：**
- `idx_comments_issue_created (workspace_id, issue_id, created_at)` —— 按时间拉取某 issue 的评论（主查询路径）。
- `idx_comments_thread (workspace_id, thread_root_id, created_at)` —— 拉取某线程全部回复。
- `idx_comments_author (workspace_id, author_type, author_id, created_at)` —— 「我发过的评论」。
- 部分索引 `idx_comments_active ON comments(issue_id, created_at) WHERE deleted_at IS NULL`。

### 2.2 `comment_mentions` — 提及解析结果

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `comment_id` | uuid | NOT NULL, FK→comments | 来源评论 |
| `mentioned_type` | text | NOT NULL, CHECK in ('member','agent') | 被提及对象类型 `[Mesh 特色]` |
| `mentioned_id` | uuid | NOT NULL | 被提及对象 ID |
| `triggered_run_id` | uuid | NULL, FK→agent_runs | 若提及的是 agent 且成功入队运行，记录运行 ID `[Mesh 特色]` |
| `created_at` | timestamptz | NOT NULL | |

**关键索引：** `idx_mentions_target (mentioned_type, mentioned_id, created_at)` —— 反查「提到我的评论」，驱动通知与收件箱。
**唯一约束：** `uq_mentions (comment_id, mentioned_type, mentioned_id)` —— 同一评论对同一对象只记一次。

### 2.3 `issue_subscriptions` — issue 订阅（驱动通知路由）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL | |
| `issue_id` | uuid | NOT NULL, FK→issues | |
| `subscriber_type` | text | NOT NULL, CHECK in ('member','agent') | |
| `subscriber_id` | uuid | NOT NULL | |
| `reason` | text | NOT NULL | 订阅来源：`creator`/`assignee`/`mentioned`/`manual`/`participated` |
| `muted` | boolean | NOT NULL default false | 是否静音（保留订阅但不出通知） |
| `created_at` / `updated_at` | timestamptz | NOT NULL | |

**唯一约束：** `uq_subscription (issue_id, subscriber_type, subscriber_id)`。
**关键索引：** `idx_subscriptions_issue (workspace_id, issue_id) WHERE NOT muted`。
> 设计要点：通知 fan-out 时遍历「订阅者 ∪ 被提及者 ∪ 分派对象」并去重，再按各订阅者的偏好（2.6）过滤。

### 2.4 `comment_reactions` — 表情回应

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `comment_id` | uuid | NOT NULL, FK→comments | |
| `actor_type` | text | NOT NULL, CHECK in ('member','agent') | |
| `actor_id` | uuid | NOT NULL | |
| `emoji` | text | NOT NULL | 统一 emoji（建议归一化为 shortcode 或 unicode） |
| `created_at` | timestamptz | NOT NULL | |

**唯一约束：** `uq_reaction (comment_id, actor_type, actor_id, emoji)` —— 同一人对同一评论同一 emoji 只一次。
**关键索引：** `idx_reactions_comment (comment_id)`。

### 2.5 `notifications` — 通知主表（收件箱数据源）

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 通知 ID |
| `workspace_id` | uuid | NOT NULL | 多租户隔离 |
| `recipient_type` | text | NOT NULL, CHECK in ('member','agent') | 接收者类型；收件箱主要面向 member |
| `recipient_id` | uuid | NOT NULL | 接收者 ID |
| `type` | text | NOT NULL | 通知类型枚举：`assigned` / `mentioned` / `subscribed_update` / `comment_created` / `status_changed` / `agent_run_finished` / `review_requested` / `due_soon` 等 |
| `actor_type` | text | NULL | 触发者类型（member/agent/system） |
| `actor_id` | uuid | NULL | 触发者 ID |
| `issue_id` | uuid | NULL, FK→issues | 关联 issue（用于按 issue 分组） |
| `comment_id` | uuid | NULL, FK→comments | 关联评论（用于跳转锚点） |
| `target_type` | text | NULL | 目标实体类型（issue/comment/run…） |
| `target_id` | uuid | NULL | 目标实体 ID |
| `payload` | jsonb | NOT NULL default '{}' | 渲染所需快照（如评论摘要、变更前后值），避免渲染时回查已删除实体 |
| `group_key` | text | NULL | 分组键（如 `issue:<id>:<type>:<day>`），用于折叠 |
| `read_at` | timestamptz | NULL | 已读时间（NULL=未读） |
| `archived_at` | timestamptz | NULL | 归档时间 |
| `snoozed_until` | timestamptz | NULL | 稍后处理（可选） |
| `created_at` / `updated_at` | timestamptz | NOT NULL | |

**关键索引：**
- `idx_notifications_inbox (workspace_id, recipient_id, archived_at, created_at DESC)` —— 收件箱主查询。
- `idx_notifications_unread (workspace_id, recipient_id) WHERE read_at IS NULL AND archived_at IS NULL` —— 未读徽标计数（部分索引，高频）。
- `idx_notifications_group (recipient_id, group_key, created_at)` —— 分组折叠。
- GIN `idx_notifications_payload (payload)`（按需，用于按 payload 检索）。

**设计要点：**
- `payload` 存「快照」而非全靠外键回查——评论被删除后通知仍可读。
- 去重/合并：短时间内同 `group_key` 的通知可合并为一条（更新 `payload.count` 与 `updated_at`），避免刷屏。

### 2.6 `notification_preferences` — 通知偏好

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL | |
| `member_id` | uuid | NOT NULL, FK→members | |
| `event_type` | text | NOT NULL | 事件类型（同 notifications.type，或 `all`） |
| `in_app` | boolean | NOT NULL default true | 是否站内通知 |
| `email` | text | NOT NULL default 'digest' | 邮件策略：`none`/`realtime`/`digest` |
| `created_at` / `updated_at` | timestamptz | NOT NULL | |

**唯一约束：** `uq_notif_pref (workspace_id, member_id, event_type)`。
> 缺省记录：用户未显式设置的 event_type 走默认策略（提及/分派=站内+实时邮件，订阅更新=站内+日摘要）。

### 2.7 `notification_delivery` — 投递/去重台账（可选但推荐）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | uuid | PK |
| `notification_id` | uuid | FK→notifications |
| `channel` | text | `in_app`/`email`/`websocket` |
| `state` | text | `pending`/`sent`/`failed` |
| `sent_at` | timestamptz | NULL |
| `error` | text | NULL |

> 用于邮件摘要任务幂等投递、失败重试。

### 2.8 ER 关系总结

```
workspaces 1─* issues 1─* comments 1─* comment_reactions
                         comments 1─* comment_mentions *─1 members/agents
                         comments 自引用(parent_id / thread_root_id)
issues 1─* issue_subscriptions *─1 members/agents
members 1─* notifications ; notifications *─1 issues ; *─1 comments
members 1─* notification_preferences
notifications 1─* notification_delivery
```

---

## 三、接口设计

> 鉴权：`Authorization: Bearer <token>`。成员会话 token 或 API token（见 auth 模块）。所有写操作校验 workspace 角色与资源权限（RBAC）。
> 分页：游标分页。响应含 `data[]` 与 `pagination: { next_cursor, has_more }`；游标为不透明字符串（内部基于 `created_at + id` 的 keyset）。
> 时间：RFC3339（UTC）。

### 3.1 评论端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/issues/{issue_id}/comments` | 列出 issue 评论（默认仅顶层，回复内联或单独拉取） |
| POST | `/api/v1/issues/{issue_id}/comments` | 发表评论（可带 `parent_id` 成为回复） |
| GET | `/api/v1/comments/{comment_id}` | 取单条评论 |
| PATCH | `/api/v1/comments/{comment_id}` | 编辑评论 |
| DELETE | `/api/v1/comments/{comment_id}` | 删除评论（软删除） |
| GET | `/api/v1/comments/{comment_id}/replies` | 列出某线程的回复（游标分页） |
| POST | `/api/v1/comments/{comment_id}/resolve` | 解决线程 |
| POST | `/api/v1/comments/{comment_id}/reopen` | 重新打开线程 |
| GET | `/api/v1/comments/{comment_id}/reactions` | 列出反应 |
| POST | `/api/v1/comments/{comment_id}/reactions` | 添加反应 |
| DELETE | `/api/v1/comments/{comment_id}/reactions/{emoji}` | 取消（自己的）反应 |

**发表评论请求体：**
```json
{
  "body_markdown": "已经定位到问题，详见日志。\n\n@wang 你确认下生产配置？\n@code-reviewer 帮忙跑一遍回归。",
  "parent_id": null,
  "attachment_ids": ["8c1f..."],
  "mentions": [
    {"type": "member", "id": "u-111"},
    {"type": "agent", "id": "a-222"}
  ]
}
```
> `mentions` 可由客户端解析后显式提交，也可由服务端从 Markdown 中解析（推荐服务端解析为准，防伪造）。服务端检测到 agent 提及即入队运行，并在响应里回填 `triggered_run_ids`。

**发表评论响应体（201）：**
```json
{
  "data": {
    "id": "c-abc",
    "issue_id": "i-1",
    "parent_id": null,
    "author": {"type": "member", "id": "u-9", "name": "李四", "avatar_url": "..."},
    "body_markdown": "...",
    "body_html": "<p>...</p>",
    "reactions": [{"emoji": "👍", "count": 2, "reacted_by_me": false}],
    "reply_count": 0,
    "resolved_at": null,
    "triggered_run_ids": ["run-77"],
    "created_at": "2026-07-24T10:00:00Z",
    "edited_at": null
  }
}
```

**列出评论查询参数：** `?limit=50&cursor=<opaque>&include=replies|none&order=asc`。
> 线程拉取策略二选一（推荐 A）：
> - A：列表只返回顶层评论 + `reply_count` + 前 N 条预览回复，展开时按 `GET /comments/{id}/replies` 分页拉。
> - B：一次性返回扁平列表（含 `parent_id`），前端自行组装。

### 3.2 收件箱 / 通知端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/inbox` | 列出我的通知（游标分页，支持筛选） |
| GET | `/api/v1/inbox/unread-count` | 未读计数（顶栏徽标） |
| POST | `/api/v1/inbox/{notification_id}/read` | 标记单条已读 |
| POST | `/api/v1/inbox/{notification_id}/unread` | 标记未读 |
| POST | `/api/v1/inbox/read-all` | 全部已读（可按筛选条件） |
| POST | `/api/v1/inbox/{notification_id}/archive` | 归档 |
| POST | `/api/v1/inbox/archive-read` | 归档全部已读 |
| GET | `/api/v1/notification-preferences` | 读取偏好 |
| PUT | `/api/v1/notification-preferences` | 更新偏好 |

**列出收件箱查询参数：** `?limit=30&cursor=&filter=unread|all|mentions|assigned|agent&type=&grouped=true`。

**收件箱响应体（200，分组形态）：**
```json
{
  "data": [
    {
      "id": "n-1",
      "group_key": "issue:i-1:comment_created",
      "type": "comment_created",
      "issue": {"id": "i-1", "identifier": "MES-1", "title": "..."},
      "actor": {"type": "agent", "id": "a-222", "name": "code-reviewer"},
      "preview": "已经修复并通过测试，PR: ...",
      "count": 3,
      "read_at": null,
      "created_at": "2026-07-24T10:05:00Z",
      "latest_comment_id": "c-xyz"
    }
  ],
  "pagination": {"next_cursor": "...", "has_more": true}
}
```

### 3.3 错误码体系

统一错误包络：
```json
{"error": {"code": "FORBIDDEN", "message": "你没有权限执行此操作", "details": {}}}
```

| HTTP | code | 场景 |
|------|------|------|
| 400 | `VALIDATION_ERROR` | 字段缺失/超长/非法（如 body 为空、emoji 非法） |
| 401 | `UNAUTHENTICATED` | 缺失/过期/非法 token |
| 403 | `FORBIDDEN` | 无权限（如删除他人评论、跨 workspace 访问） |
| 404 | `NOT_FOUND` | 评论/通知/issue 不存在或已删除 |
| 409 | `CONFLICT` | 重复反应、并发编辑冲突（用 `updated_at` 乐观锁） |
| 410 | `GONE` | 目标评论已被删除但 ID 曾被引用 |
| 422 | `MENTION_INVALID` | 提及了不存在/无权限的对象 |
| 429 | `RATE_LIMITED` | 触发速率限制（见 auth 模块），响应含 `Retry-After` |
| 500 | `INTERNAL` | 服务端异常（不泄露堆栈） |

### 3.4 鉴权与权限要点

- 评论读：workspace 成员且对该 issue 有读权限（RBAC，见 auth 模块）。
- 评论写：对 issue 有评论权限；agent 评论由 agent runtime 用 API token 写入。
- 编辑/删除：仅作者本人，或具备管理员/owner 角色。
- 提及 agent 触发运行：调用者需对该 agent 有「触发」权限，否则返回 `FORBIDDEN`，提及不产生运行（可降级为普通提及或拒绝）。

---

## 四、UI 设计

### 4.1 评论区（issue 详情右侧主栏底部）

布局（自上而下）：
1. **活动流 + 评论混合时间线**：系统活动（状态/分派变更，灰色小字）与用户评论（带头像卡片）按时间穿插。
2. **评论卡片**：头像 | 作者名 + 角色徽标（人/agent 区分图标 `[Mesh 特色]`）| 相对时间 | 「已编辑」标记；正文 Markdown 渲染；底部操作条（回复 / 表情 / 更多菜单：复制链接、编辑、删除、解决线程）。
3. **线程折叠**：有回复的评论下方显示「N 条回复 ▸」，展开缩进显示回复；线程右上角「解决 / 已解决」状态。
4. **反应区**：评论下方一排 emoji chip（`👍 2`），点击增减；「+」打开 emoji 选择器。
5. **评论输入框（composer）**：底部固定，支持 Markdown 工具条、`@` 自动补全弹层（人/agent 混排，agent 项标注「将触发一次运行」）、附件拖拽/粘贴、预览切换（编辑/预览）、Cmd+Enter 提交。
6. **agent 运行回流提示 `[Mesh 特色]`**：提及 agent 后，对应位置出现「⏳ code-reviewer 正在运行…」占位卡片，运行完成后替换为 agent 评论。

### 4.2 收件箱（顶栏铃铛 + 独立页面/抽屉）

信息架构：
- **顶栏铃铛**：未读红点 + 数字徽标；点击下拉最近若干条，底部「查看全部」进收件箱页。
- **收件箱页（或右侧抽屉）**：
  - 顶部筛选 tabs：`全部 / 未读 / 提及我的 / 分派 / Agent`（`[Mesh 特色]` 单列 agent 通知 tab）。
  - 列表按 issue 分组：每组头部为 issue 标识 + 标题；组内为通知行（actor 头像、动作描述、预览文本、相对时间、未读圆点）。
  - 行操作（hover 出现）：标已读 / 归档 / 跳转。
  - 顶部工具条：「全部已读」「归档已读」。
- **空状态**：插画 + 「收件箱已清空」。

### 4.3 通知偏好设置页（Settings → Notifications）

- 矩阵表格：行=事件类型（被分派/被提及/订阅更新/状态变更/agent 运行完成…），列=站内开关 + 邮件策略（无/实时/摘要）。
- agent 通知分区 `[Mesh 特色]`：单独一组「Agent 运行通知」开关与粒度。
- 全局：免打扰时段、邮件摘要频率（实时/每小时/每日）。

---

## 五、UX 设计

### 5.1 发表评论流程

1. 用户在 composer 输入，`@` 触发补全：输入即搜索 workspace 成员 + agent，键盘上下选择，回车确认插入提及 chip。
2. 选中 agent 时，输入框下方出现一行轻提示：「提及 @code-reviewer 将立即触发一次运行」——明确告知副作用，避免误触发烧钱。
3. 提交：前端乐观更新（评论立即出现在列表，状态 `sending`）→ 服务端落库 → WebSocket 回广播 → 成功后更新为最终态；失败则标红「重试」。
4. 服务端：解析 Markdown → 净化 HTML（白名单标签/属性，防 XSS）→ 提取提及落 `comment_mentions` → 对 agent 提及入队运行 → fan-out 通知给订阅者与被提及者 → 经 WebSocket 推送 `comment.created` 事件。
5. 草稿：输入内容本地暂存（按 issue 维度），离开再回来恢复；提交成功清除。

### 5.2 处理收件箱流程

1. 新通知经 WebSocket 实时到达：铃铛徽标 +1，可选桌面 toast（尊重免打扰设置）。
2. 点击通知 → 直达 issue 对应评论锚点并高亮闪烁；同时该条自动标已读。
3. 在收件箱内可批量「全部已读 / 归档已读」；按 issue 分组让同一 issue 的多条聚合，减少噪音。
4. 离线/未登录期间累积的通知：登录后一次性拉取未读；邮件按偏好发实时或摘要。

### 5.3 实时性方案（WebSocket 为主）

- **连接**：客户端登录后建立 WebSocket（`wss://…/ws`），以 token 鉴权（连接时携带或首消息认证）；按 `workspace_id + member_id` 订阅频道。
- **心跳**：客户端每 ~25s 发 ping，服务端回 pong；超时断开并指数退避重连（抖动避免雪崩）。
- **事件类型**（服务端 → 客户端）：
  - `comment.created` / `comment.updated` / `comment.deleted` / `comment.resolved`
  - `reaction.changed`
  - `notification.created`（携带通知对象，直接进收件箱）
  - `notification.read` / `inbox.unread_count`（多端已读同步）
  - `agent_run.started` / `agent_run.finished` `[Mesh 特色]`
  - `issue.updated`（字段/状态变更，驱动评论区活动流）
- **可靠性兜底**：
  - 每条事件带单调递增 `seq`；客户端记录 last_seq，重连后带 `?since_seq=` 重放缺口事件（服务端保留近 N 分钟/条的事件缓冲，基于 Redis Stream 或类似）。
  - 重连后额外拉一次 `unread-count` 与增量列表对账，防丢事件。
  - 极端降级：WebSocket 不可用时退化为短轮询 `unread-count` + 增量列表（30~60s）。
- **多端一致性**：一端标已读，服务端广播 `notification.read`，其余端同步消除红点。

### 5.4 通知生成与去噪规则

- **触发即生成**：评论创建/提及/分派/状态变更等动作在事务提交后异步 fan-out（用消息队列解耦，避免阻塞主请求）。
- **去重合并**：同 `group_key`（同 issue 同类型短窗口）合并为一条，更新计数与时间，避免「某人连发 5 条评论」刷出 5 条通知。
- **自我抑制**：动作发起者不给自己生成通知；agent 自己触发的连环动作避免回环（agent 不接收会再次触发自己的通知，防 agent-to-agent 死循环 `[Mesh 特色]`）。
- **偏好过滤**：fan-out 时按 `notification_preferences` 决定站内/邮件；`muted` 订阅不出通知。
- **邮件摘要**：定时任务（如每 30 分钟/每日）扫描 `email='digest'` 且未投递的站内通知，聚合一封摘要邮件，写入 `notification_delivery` 防重；用户点邮件内链接回站内并标已读。

### 5.5 提及 agent 的特殊交互 `[Mesh 特色]`

- 提及 agent = 副作用动作（入队运行），UI 上必须与提及人区分（图标、二次确认/提示）。
- 默认「不提及即结束对话」：回复 agent 的评论若不再 `@` 它，则不再触发新运行，天然终止 agent 间循环。
- 运行结果以 agent 评论形式回流到**原线程**，并把触发者加入订阅，触发者收到 `agent_run.finished` 通知。
- 运行失败：在评论区留下失败占位卡片 + 通知触发者，便于重试。

---

## 六、关键设计取舍小结（供 Spec 参考）

1. **评论 vs 活动流同表异源**：用 `author_type=system` 表达活动流，前端按类型差异化渲染，避免两套数据管道。
2. **线程深度受限**：业界主流是「单层回复 + 折叠」，`parent_id` 限深度，`thread_root_id` 冗余加速聚合。
3. **通知存快照**：`payload` 冗余渲染所需信息，实体被删后通知仍可读。
4. **服务端解析提及为准**：客户端提交的 `mentions` 仅作提示，最终以服务端从 Markdown 解析结果落库，防伪造与漏记。
5. **WebSocket + seq 重放 + 轮询兜底**：三层保障实时性与可靠性。
6. **agent 提及即运行**：是 Mesh 区别于传统产品的核心，必须在 UI 提示、权限校验、回环抑制三处做足。
</antParameter>
</invoke>
