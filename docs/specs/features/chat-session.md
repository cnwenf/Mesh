# 与 Agent 对话(Chat Session)功能 Spec

> **所属层**:智能体编排层(实时聊天会话 + issue 评论区异步协作,统一在「对话」抽象之下)。
> **依赖的其他 Spec**:
> - `workspace.md`:`chat_session` 等外键回 `workspaces.id`,以 workspace 为隔离边界。
> - `member.md`:会话发起人、评论作者、提及发起者/目标统一引用 `members.id`(`member_type ∈ {human, agent}`);会话关联 agent 引用 `agents.id`。
> - `agent.md`:对话的 agent 一方;agent 回复能力来自其运行时与绑定技能。
> - `issue.md`:会话上下文关联 issue;评论挂在 issue 下。
> - `project.md`:会话上下文可关联项目。
> - `runtime.md`:评论区 agent 异步回评被派发为运行记录(`agent_runs`),其生命周期遵循 runtime 长任务状态机 `queued→claimed→running→completed|failed|cancelled`;本 Spec 经 mention 的 `run_id` 引用。
> - `attachment.md`:聊天附件与评论附件以对象存储承载。
> - `auth.md`:JWT 鉴权、限流、审计;agent 以服务账号回评,操作可审计。
> **关系说明**:本 Spec 与 `comment-inbox.md` 共同拥有「对话」抽象 —— 本 Spec 定义聊天会话与统一的消息状态机/提及/通知管线契约;评论线程聚合、收件箱视图由 comment-inbox.md 在其上构建,二者字段语义对齐、共享同一套表,不重复建模。

---

## 1. 功能描述

### 1.1 模块定位

Mesh 把 AI agent 当作真正的队友,队友之间的「对话」天然存在两种互补形态,本模块同时覆盖:

- **形态 A:实时聊天会话(real-time chat)** —— 人与单个 agent 的即时对话,agent 回复逐 token/逐块流式返回,体验类似 IM;适合探索、问答、头脑风暴、需要人 AI 紧密协作的快速迭代。
- **形态 B:issue 评论区的异步协作对话(async comments)** —— 围绕某个具体任务(issue),人与人、人与 agent、agent 与 agent 通过 @提及与线程回复异步往来;agent 被分派/被提及后异步处理完成,再以完整结果回评到评论区;适合任务派发、结果交付、多方评审。

二者不是替代关系,而是互补:**聊天重「过程」,评论重「结论」;聊天是 1 对 1(人↔agent),评论是 1 对多(issue 下多主体);聊天同步流式,评论异步事件驱动;聊天可携带 issue 上下文,聊天结论可一键沉淀回 issue 评论,形成闭环。**

**核心设计:把两种形态统一在「对话」抽象之下**。二者共享同一套消息状态机(`streaming/done/failed/interrupted`)、同一套提及模型、同一条通知管线、同一个 AI 徽章身份体系。数据层 `chat_message` 与 `issue_comment` 字段语义对齐(role/author_type、content、附件),上层 UI 组件库可复用 —— 这让「把聊天沉淀为评论」和「从评论拉起聊天继续」几乎零成本转换,也是 Mesh 区别于普通聊天工具的关键。

> **全局名册约定(与 member.md 一致)**:人与 agent 统一登记在 `members` 名册(`member_type ∈ {human, agent}`,`members.id` 为统一引用键)。本 Spec 中:会话发起人 `owner_id → members.id`(`member_type='human'`);会话关联的 agent `agent_id → agents.id`;评论作者、提及发起者/目标用 `(<x>_type, <x>_id)` 二元组,`<x>_type ∈ {human, agent}`、`<x>_id → members.id`。

### 1.2 功能点 + 用户场景表

**形态 A:实时聊天会话**

| # | 功能 | 说明 | 典型用户场景 |
|---|------|------|--------------|
| A1 | 发起与某 agent 的会话 | 从 agent 名册选择,创建空会话 | 产品经理在"需求分析 agent"详情页点"开始对话" |
| A2 | 多轮消息历史 | 用户消息与 agent 回复持久化,支持向上回溯 | 第二天回到昨天的会话接着上次上下文继续提问 |
| A3 | 流式输出 | agent 回复经 SSE/WebSocket 逐块返回,打字机效果 | 提问后文本逐 token 出现,无需等待完整响应 |
| A4 | 会话携带上下文 | 关联 issue/项目,作为对话上下文注入 | 开会话时挂上 issue,agent 自动知晓其描述、评论与状态 |
| A5 | 中断当前生成 | 生成途中发 stop 信号,终止流式 | agent 答偏了,点"停止"重新组织提问 |
| A6 | 重新生成 | 对某条 agent 消息重跑,保留多个候选回复 | 第一版不够好,点"重新生成",在 3 个候选里挑最优 |
| A7 | 会话列表管理 | 最近会话、置顶/归档/删除、按 agent 筛选 | 重要会话置顶,已完成归档,按 agent 过滤 |
| A8 | 标题自动生成/重命名 | 首轮对话后自动总结标题;支持手动重命名 | 标题从"新对话"自动变为"登录重定向 bug 讨论" |
| A9 | 消息内引用与附件 | 引用某条消息;上传图片/文档附件 | 上传截图让 agent 分析;引用某条回答继续追问 |
| A10 | 消息生成状态 | streaming/done/failed/interrupted 可见可恢复 | 生成中途网络中断,该消息标"生成失败"并提供重试入口 |

**形态 B:issue 评论区的异步协作**

| # | 功能 | 说明 | 典型用户场景 |
|---|------|------|--------------|
| B1 | 发表评论 | 在 issue 下发布 markdown 评论 | 开发同学在 issue 里写下实现方案 |
| B2 | @提及人/agent | 提及人→通知;提及 agent→入队异步处理并回评 | 评论里 @测试 agent,agent 跑测试后回评结果 |
| B3 | 线程回复 | 主评论 + 回复聚合为线程,回复折叠(最多一层) | 多人围绕同一条方案讨论,回复挂在主评论下不刷屏 |
| B4 | agent 异步回评 | agent 被分派/被提及后,处理完毕在评论区发结果 | agent 运行数分钟后把测试报告贴回评论区,带 AI 徽章 |
| B5 | 评论编辑/删除 | 作者与有权限者可编辑/软删除评论 | 修正笔误;删除无效评论 |
| B6 | 解决线程(resolve) | 标记某线程为已解决并折叠 | 方案确认,把该讨论线程标为已解决 |
| B7 | 已读/未读与收件箱 | 提及、回复、agent 回评聚合进收件箱,带未读计数 | 收件箱看到 3 条未读提及,逐条处理 |
| B8 | agent 循环防护 | 检测并切断 agent 互相提及导致的无限循环 | A 提及 B,B 回评又提及 A,系统在深度阈值后截断 |

**两种形态共存与互补**

| 维度 | 实时聊天(A) | 异步评论(B) |
|------|--------------|--------------|
| 参与方 | 1 人 + 1 agent | 多人 + 多 agent,围绕 issue |
| 时效 | 同步、流式 | 异步、事件驱动 |
| 承载 | 探索性过程、发散思考 | 任务结论、可交付物 |
| 触发 | 用户主动发送 | 被分派、被 @、状态变更 |
| 产出去向 | 留在会话内 | 落在 issue 评论区,可被链接回看 |
| 实时通道 | SSE/WebSocket 流式 | WebSocket 通知 + REST 拉取 |

**闭环路径**:在聊天里对齐方案 → 一键"沉淀为 issue 评论"→ @ 相关 agent 执行 → agent 异步回评结果 → 人在收件箱确认并 resolve 线程。

### 1.3 边界与非目标(明确不做什么)

- **不**定义 agent 运行时/模型/技能实现 —— 归 `agent.md` / `runtime.md`(本 Spec 仅消费 agent 身份与运行能力)。
- **不**定义 issue 的内容/状态领域逻辑 —— 归 `issue.md`(本 Spec 仅以 issue 为评论挂载点与聊天上下文)。
- **不**重复定义收件箱/线程聚合的完整视图 —— 归 `comment-inbox.md`(本 Spec 定义共享的 mention/notification 表与管线契约)。
- **不**实现流式上游模型的推理细节 —— 仅声明流式协议、事件契约与中断语义。
- **不**支持多人实时协同编辑同一条消息(YAGNI)。
- **不**支持群聊(多 human + 多 agent 的实时会话)—— 实时聊天为 1 人对 1 agent;多方协作走评论区。

---

## 2. 数据模型

### 2.1 ER 概览(文字图)

```
members(member.md)──owns──► chat_session ──serves──► agents(agent.md)
   (owner=human)              │  context_issue_id ──► issues;context_project_id ──► projects
                              ▼ contains
                        chat_message ──自引用──► parent_id(候选回复分支)
                              │ carries
                              ▼
                        chat_attachment

issues ──contains──► issue_comment ──自引用──► parent_id(线程, 最多一层)
                        │ generates
                        ▼
                     mention ──target──► members(human/agent);run_id ──► agent_runs(runtime.md)
                        │ triggers
                        ▼
                     notification(收件箱, 与 comment-inbox.md 共享)

「对话」抽象:chat_message 与 issue_comment 共享 role/author_type、content、附件、生成状态语义
```

### 2.2 表:`chat_session`(聊天会话)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 会话 ID |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `owner_id` | UUID | NOT NULL,FK→members(id) | — | 所属用户(发起人,`member_type='human'`) |
| `agent_id` | UUID | NOT NULL,FK→agents(id) | — | 关联 agent |
| `title` | TEXT | NOT NULL | `'新对话'` | 会话标题 |
| `title_is_auto` | BOOLEAN | NOT NULL | `true` | 标题是否自动生成 |
| `context_issue_id` | UUID | NULL,FK→issues(id) | NULL | 上下文关联 issue |
| `context_project_id` | UUID | NULL,FK→projects(id) | NULL | 上下文关联项目 |
| `status` | TEXT | NOT NULL,CHECK IN ('active','archived','deleted') | `'active'` | 会话状态 |
| `is_pinned` | BOOLEAN | NOT NULL | `false` | 是否置顶 |
| `last_message_at` | TIMESTAMPTZ | NULL | NULL | 最近一条消息时间(排序用) |
| `last_message_preview` | TEXT | NULL | NULL | 最近消息摘要(列表展示) |
| `message_count` | INT | NOT NULL,CHECK (>= 0) | `0` | 消息数 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间 |

### 2.3 表:`chat_message`(聊天消息,含候选回复分支)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 消息 ID |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `session_id` | UUID | NOT NULL,FK→chat_session(id) | — | 所属会话 |
| `role` | TEXT | NOT NULL,CHECK IN ('user','agent','system') | — | 消息角色 |
| `content` | TEXT | NOT NULL | `''` | 消息内容(markdown) |
| `generation_status` | TEXT | NOT NULL,CHECK IN ('streaming','done','failed','interrupted') | `'done'` | 生成状态(user 消息恒为 done) |
| `parent_id` | UUID | NULL,FK→chat_message(id) | NULL | 候选回复:指向其所回答的用户消息 |
| `selected_candidate` | BOOLEAN | NOT NULL | `true` | 是否为当前选中的候选回复 |
| `quote_message_id` | UUID | NULL,FK→chat_message(id) | NULL | 引用的消息 |
| `prompt_tokens` | INT | NULL | NULL | 输入 token 计数 |
| `completion_tokens` | INT | NULL | NULL | 输出 token 计数 |
| `error_message` | TEXT | NULL | NULL | 失败原因 |
| `started_at` | TIMESTAMPTZ | NULL | NULL | 生成开始时间 |
| `finished_at` | TIMESTAMPTZ | NULL | NULL | 生成结束时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> **候选回复设计**:针对同一条用户消息,多个 agent 候选回复都挂在同一 `parent_id` 下;其中仅一条 `selected_candidate=true`;regenerate 时新建候选并把选中项切到新候选,旧候选保留,可翻页回选(不覆盖、不修改旧消息)。

### 2.4 表:`chat_attachment`(聊天附件)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `message_id` | UUID | NULL,FK→chat_message(id) | NULL | 关联消息(发送前上传则为空) |
| `session_id` | UUID | NOT NULL,FK→chat_session(id) | — | 所属会话 |
| `uploader_id` | UUID | NOT NULL,FK→members(id) | — | 上传者 |
| `file_name` | TEXT | NOT NULL | — | 文件名 |
| `mime_type` | TEXT | NOT NULL | — | MIME 类型 |
| `byte_size` | BIGINT | NOT NULL | — | 字节大小 |
| `storage_key` | TEXT | NOT NULL | — | 对象存储 key(不暴露绝对路径) |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

### 2.5 表:`issue_comment`(issue 评论,含线程)

> 与 `chat_message` 字段语义对齐(`author_type`↔`role`、`content`、附件),共享「对话」抽象。详细收件箱/线程聚合视图见 comment-inbox.md。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | 评论 ID |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `issue_id` | UUID | NOT NULL,FK→issues(id) | — | 所属 issue |
| `author_id` | UUID | NOT NULL,FK→members(id) | — | 作者(人或 agent) |
| `author_type` | TEXT | NOT NULL,CHECK IN ('human','agent') | — | 作者类型 |
| `parent_id` | UUID | NULL,FK→issue_comment(id) | NULL | 线程:所属主评论(最多一层) |
| `content` | TEXT | NOT NULL | `''` | 评论内容(markdown) |
| `resolved` | BOOLEAN | NOT NULL | `false` | 线程是否已解决(仅主评论有效) |
| `resolved_by` | UUID | NULL,FK→members(id) | NULL | 解决者 |
| `resolved_at` | TIMESTAMPTZ | NULL | NULL | 解决时间 |
| `edited_at` | TIMESTAMPTZ | NULL | NULL | 最后编辑时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间 |

### 2.6 表:`mention`(提及记录)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `comment_id` | UUID | NOT NULL,FK→issue_comment(id) | — | 来源评论 |
| `issue_id` | UUID | NOT NULL,FK→issues(id) | — | 所属 issue(反规范化,便于索引) |
| `actor_type` | TEXT | NOT NULL,CHECK IN ('human','agent') | — | 提及发起者类型 |
| `actor_id` | UUID | NOT NULL,FK→members(id) | — | 提及发起者 |
| `target_type` | TEXT | NOT NULL,CHECK IN ('human','agent') | — | 被提及目标类型 |
| `target_id` | UUID | NOT NULL,FK→members(id) | — | 被提及目标 |
| `depth` | INT | NOT NULL,CHECK (>= 0) | `0` | 触发链深度(agent 互提的代际,防循环) |
| `run_triggered` | BOOLEAN | NOT NULL | `false` | 是否已触发 agent run |
| `run_id` | UUID | NULL | NULL | 触发的运行记录 ID(→ runtime.md `agent_runs`) |
| `triggered_at` | TIMESTAMPTZ | NULL | NULL | 触发时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> 提及 agent 时,服务端在同一事务写入 mention(`run_triggered=false`),再异步入队派发 run;接口同步返回,即使派发组件短暂不可用,提及记录也不丢,可被待处理索引补扫。

### 2.7 表:`notification`(收件箱通知,与 comment-inbox.md 共享)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `recipient_id` | UUID | NOT NULL,FK→members(id) | — | 接收者(人) |
| `kind` | TEXT | NOT NULL,CHECK IN ('mention','agent_reply','thread_reply','comment_resolved') | — | 通知类型 |
| `issue_id` | UUID | NULL,FK→issues(id) | NULL | 关联 issue |
| `comment_id` | UUID | NULL,FK→issue_comment(id) | NULL | 关联评论 |
| `is_read` | BOOLEAN | NOT NULL | `false` | 是否已读 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

### 2.8 索引与约束

```sql
-- 会话:列表(置顶优先 + 时间倒序)/ 按 agent 筛选 / 反查 issue 关联会话
CREATE INDEX idx_chat_session_owner_list
  ON chat_session(owner_id, is_pinned DESC, last_message_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_chat_session_owner_agent ON chat_session(owner_id, agent_id, last_message_at DESC);
CREATE INDEX idx_chat_session_context_issue ON chat_session(context_issue_id) WHERE context_issue_id IS NOT NULL;

-- 消息:历史分页 / 候选回复 / 当前正在生成(并发守卫)
CREATE INDEX idx_chat_message_session_time ON chat_message(session_id, created_at DESC);
CREATE INDEX idx_chat_message_parent ON chat_message(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_chat_message_streaming ON chat_message(session_id) WHERE generation_status = 'streaming';

-- 评论:列表 / 线程展开 / 未解决线程聚合
CREATE INDEX idx_issue_comment_issue_time ON issue_comment(issue_id, created_at) WHERE deleted_at IS NULL;
CREATE INDEX idx_issue_comment_thread ON issue_comment(parent_id, created_at) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_issue_comment_unresolved
  ON issue_comment(issue_id) WHERE resolved = false AND parent_id IS NULL AND deleted_at IS NULL;

-- 提及:同评论不重复提及同一目标 / 待处理 agent 派发队列 / 人的被提及收件箱
CREATE UNIQUE INDEX uq_mention_comment_target ON mention(comment_id, target_type, target_id);
CREATE INDEX idx_mention_pending
  ON mention(target_id, created_at) WHERE target_type = 'agent' AND run_triggered = false;
CREATE INDEX idx_mention_target_human ON mention(target_id, created_at DESC) WHERE target_type = 'human';
-- agent 循环防护:同 (issue, agent) 时间窗去重扫描
CREATE INDEX idx_mention_loop ON mention(issue_id, target_id, created_at) WHERE target_type = 'agent';

-- 通知:未读
CREATE INDEX idx_notification_unread ON notification(recipient_id, created_at DESC) WHERE is_read = false;
```

### 2.9 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `chat_session.workspace_id` 等 | → `workspaces.id` | workspace.md | 隔离 |
| `chat_session.owner_id`、`chat_attachment.uploader_id`、`issue_comment.author_id`、`mention.actor_id`/`target_id`、`notification.recipient_id` | → `members.id` | member.md | 多态主体(人/agent) |
| `chat_session.agent_id` | → `agents.id` | agent.md | 会话关联 agent |
| `chat_session.context_issue_id` / `issue_comment.issue_id` / `mention.issue_id` | → `issues.id` | issue.md | 上下文 / 评论挂载 |
| `chat_session.context_project_id` | → `projects.id` | project.md | 上下文关联项目 |
| `mention.run_id` | → `agent_runs.id` | runtime.md | @提及 agent 派发的运行实例 |
| `chat_attachment.storage_key` | → 对象存储 | attachment.md | 附件正文 |

---

## 3. 接口设计

REST 基础路径 `/api/v1`,集合嵌套于 `/workspaces/{ws}/`;鉴权 `Authorization: Bearer <JWT>`(见 auth.md);时间 RFC3339 UTC,id 均为 UUID。统一错误信封 `{"error":{"code","message","details"}}`;列表游标分页 `{"data":[...],"next_cursor"}`(`next_cursor` 为 null 表示末页,消息列表默认时间倒序);单资源端点直接返回资源对象本体。

### 3.1 REST 端点清单

**形态 A:聊天会话**

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| POST | `/workspaces/{ws}/chat-sessions` | 创建会话 | 成员 |
| GET | `/workspaces/{ws}/chat-sessions` | 会话列表(可按 agent/status 筛选) | 成员(仅自己) |
| GET | `/workspaces/{ws}/chat-sessions/{id}` | 会话详情 | 会话 owner |
| PATCH | `/workspaces/{ws}/chat-sessions/{id}` | 更新标题/置顶/归档/上下文 | 会话 owner |
| DELETE | `/workspaces/{ws}/chat-sessions/{id}` | 删除(软删除) | 会话 owner |
| POST | `/workspaces/{ws}/chat-sessions/{id}/messages` | 发送消息(`Accept` 决定普通 201 或流式) | 会话 owner |
| GET | `/workspaces/{ws}/chat-sessions/{id}/messages` | 历史(游标分页,倒序) | 会话 owner |
| POST | `/workspaces/{ws}/chat-sessions/{id}/messages/{msg_id}/regenerate` | 重新生成 | 会话 owner |
| POST | `/workspaces/{ws}/chat-sessions/{id}/messages/{msg_id}/stop` | 中断生成(幂等) | 会话 owner |
| POST | `/workspaces/{ws}/chat-sessions/{id}/messages/{msg_id}/select` | 选择候选回复 | 会话 owner |
| POST | `/workspaces/{ws}/chat-sessions/{id}/attachments` | 上传附件 | 会话 owner |

**形态 B:issue 评论**

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| POST | `/workspaces/{ws}/issues/{issue_id}/comments` | 发表评论(含提及解析与派发) | issue 可见成员 |
| GET | `/workspaces/{ws}/issues/{issue_id}/comments` | 评论列表(线程聚合,游标分页) | issue 可见成员 |
| GET | `/workspaces/{ws}/comments/{id}/replies` | 线程回复列表(游标分页) | issue 可见成员 |
| PATCH | `/workspaces/{ws}/comments/{id}` | 编辑评论 | 作者 / admin |
| DELETE | `/workspaces/{ws}/comments/{id}` | 删除评论(软删除) | 作者 / admin |
| POST | `/workspaces/{ws}/comments/{id}/resolve` | 解决线程 | issue 可写成员 |
| POST | `/workspaces/{ws}/comments/{id}/unresolve` | 重新打开线程 | issue 可写成员 |
| POST | `/workspaces/{ws}/mentions/resolve` | 提及解析(文本→目标列表,供自动补全/预览) | 成员 |

### 3.2 请求/响应 JSON 示例

**创建会话** `POST /api/v1/workspaces/{ws}/chat-sessions`
```json
// Request
{ "agent_id": "agt-3f2b...", "context_issue_id": "iss-9a1c...",
  "context_project_id": null, "title": "登录重定向 bug 讨论" }
// 201 Response
{ "id": "ses-b7e4...", "workspace_id": "ws-001", "owner_id": "mem-1111...",
  "agent_id": "agt-3f2b...", "title": "登录重定向 bug 讨论", "title_is_auto": false,
  "context_issue_id": "iss-9a1c...", "context_project_id": null,
  "status": "active", "is_pinned": false, "last_message_at": null,
  "last_message_preview": null, "message_count": 0,
  "created_at": "2026-07-24T09:00:00Z", "updated_at": "2026-07-24T09:00:00Z" }
```

**会话列表(游标分页 + 筛选)** `GET /api/v1/workspaces/{ws}/chat-sessions?agent_id=agt-3f2b...&status=active&limit=20`
```json
{ "data": [
    { "id": "ses-b7e4...", "agent_id": "agt-3f2b...", "title": "登录重定向 bug 讨论",
      "status": "active", "is_pinned": true, "last_message_at": "2026-07-24T10:12:33Z",
      "last_message_preview": "我已定位到 3 个可能原因…", "message_count": 12,
      "context_issue_id": "iss-9a1c..." }
  ],
  "next_cursor": "eyJvZmZzZXQiOjIwfQ" }
```

**获取历史(时间倒序游标)** `GET /api/v1/workspaces/{ws}/chat-sessions/{id}/messages?limit=30`
```json
{ "data": [
    { "id": "m-2", "role": "agent", "content": "经分析,可能原因有 3 个…",
      "generation_status": "done", "parent_id": "m-1", "selected_candidate": true,
      "prompt_tokens": 1820, "completion_tokens": 356,
      "created_at": "2026-07-24T10:12:30Z", "finished_at": "2026-07-24T10:12:33Z", "attachments": [] },
    { "id": "m-1", "role": "user", "content": "帮我看看为什么登录后会跳转错误",
      "generation_status": "done", "parent_id": null, "created_at": "2026-07-24T10:12:01Z",
      "attachments": [{"id": "a-1", "file_name": "screenshot.png", "mime_type": "image/png", "byte_size": 84213}] }
  ],
  "next_cursor": null }
```

**发表评论(含 @提及)** `POST /api/v1/workspaces/{ws}/issues/{issue_id}/comments`
```json
// Request
{ "content": "请 [@测试 agent](mention://agent/agt-3f2b...) 跑一遍回归测试,重点登录模块",
  "parent_id": null,
  "mentions": [{"target_type": "agent", "target_id": "agt-3f2b..."}] }
// 201 Response
{ "id": "c-100", "issue_id": "iss-9a1c...", "author_id": "mem-1111...", "author_type": "human",
  "parent_id": null, "content": "请 [@测试 agent](mention://agent/agt-3f2b...) 跑一遍回归测试,重点登录模块",
  "resolved": false,
  "mentions": [{"target_type": "agent", "target_id": "agt-3f2b...", "run_triggered": true, "run_id": "run-77"}],
  "created_at": "2026-07-24T11:00:00Z" }
```
> 提及 agent 时,服务端在同一事务写入 mention(初始 `run_triggered=false`),再异步入队派发 run;`run_triggered` 反映响应时刻的派发状态。即使派发组件短暂不可用,提及记录也不丢,可被 `idx_mention_pending` 补扫。

**评论列表(线程聚合)** `GET /api/v1/workspaces/{ws}/issues/{issue_id}/comments?limit=20`
```json
{ "data": [
    { "id": "c-100", "author_type": "human", "author_id": "mem-1111...",
      "content": "请 @测试 agent 跑一遍回归测试…", "resolved": false,
      "reply_count": 2, "latest_reply_at": "2026-07-24T11:05:12Z",
      "replies_preview": [
        {"id": "c-101", "author_type": "agent", "content": "回归完成,2 个用例失败…", "created_at": "2026-07-24T11:05:12Z"}
      ],
      "created_at": "2026-07-24T11:00:00Z" }
  ],
  "next_cursor": null }
```

**解决线程** `POST /api/v1/workspaces/{ws}/comments/{id}/resolve` → `200`
```json
{ "id": "c-100", "resolved": true, "resolved_by": "mem-1111...", "resolved_at": "2026-07-24T11:30:00Z" }
```

### 3.3 流式输出协议(重点)

#### 通道选型与双通道对齐

| 通道 | 优点 | 缺点 | 适配场景 |
|------|------|------|----------|
| SSE | 基于 HTTP,原生自动重连与事件 ID,易调试、易过代理 | 半双工(服务端→客户端),中断需独立通道 | 流式输出首选 |
| WebSocket | 全双工,可复用做中断/通知/在线状态 | 重连与心跳需自建,代理复杂度略高 | 实时网关统一通道 |

**Mesh 方案**:SSE 作为流式输出主通道(简单可靠,原生携带 `Last-Event-ID` 便于断点续传);WebSocket 实时网关负责中断信号、评论实时通知、在线状态等全双工场景。**两条通道的事件名保持同名**(`message.delta` / `message.done` / `message.interrupted` / `error`),客户端无论走哪条通道,渲染逻辑一致。

#### 发送消息并流式响应

```http
POST /api/v1/workspaces/{ws}/chat-sessions/{id}/messages
Authorization: Bearer <JWT>
Accept: text/event-stream
Content-Type: application/json

{ "content": "帮我分析这个 bug 的可能原因", "attachment_ids": ["a-1"] }
```

响应流(`Content-Type: text/event-stream`):
```
id: 1
event: message.created
data: {"message_id":"m-9","role":"agent","generation_status":"streaming"}

id: 2
event: message.delta
data: {"message_id":"m-9","delta":"经分析"}

id: 3
event: message.delta
data: {"message_id":"m-9","delta":",可能原因有 3 个: "}

id: 4
event: message.done
data: {"message_id":"m-9","generation_status":"done","completion_tokens":356}
```

#### SSE / WebSocket 同名事件类型表

| event | 触发时机 | data 关键字段 |
|-------|----------|----------------|
| `message.created` | agent 消息创建、生成开始前 | `message_id`, `role`, `generation_status=streaming` |
| `message.delta` | 增量文本块(token/句) | `message_id`, `delta` |
| `message.done` | 生成正常完成 | `message_id`, `completion_tokens`, `generation_status=done` |
| `message.interrupted` | 被 stop 中断 | `message_id`, `partial_content`, `generation_status=interrupted` |
| `error` | 生成失败(模型异常、超限等) | `message_id`, `code`, `message` |
| `ping` | 心跳(建议 15s 一次) | `ts` |

每个 SSE 事件带自增数字 `id:`;客户端断线后带 `Last-Event-ID` 重连,服务端从断点重放 delta(delta 缓冲由内存缓存承载;若缓冲已淘汰,客户端降级为"REST 拉一次历史 + 重新订阅")。

#### 中断通道(独立幂等端点)

```http
POST /api/v1/workspaces/{ws}/chat-sessions/{id}/messages/m-9/stop
Authorization: Bearer <JWT>
```
```json
// 202 Response
{ "message_id": "m-9", "generation_status": "interrupted" }
```
服务端收到后:停止上游模型生成 → 在 SSE 流上发出 `message.interrupted` → 关闭该流。若走 WebSocket 网关,则上送帧 `{"type":"stop","data":{"message_id":"m-9"}}`。**stop 必须幂等:重复 stop 返回 200/202 且无副作用**,确保"流断了也能停"。

#### 重新生成与选择候选

```http
POST /api/v1/workspaces/{ws}/chat-sessions/{id}/messages/m-1/regenerate
Accept: text/event-stream
```
服务端:新建一条 agent 候选(`parent_id=m-1`,新候选默认 `selected_candidate=true`,旧候选置 false)→ 立即在事件流返回 `message.created` 开始流式。

```http
POST /api/v1/workspaces/{ws}/chat-sessions/{id}/messages/m-1/select
Content-Type: application/json

{ "selected_message_id": "m-11" }
// 200 Response
{ "parent_id": "m-1", "selected_message_id": "m-11" }
```

#### WebSocket 实时网关帧协议(统一实时通道)

连接 `/ws`(握手鉴权见 auth.md;**禁止在 URL query 参数中传递 token**,应使用 WebSocket 子协议或连接后首帧认证,避免 token 落入访问日志与中间代理);连接后按主题订阅:
```json
{"type": "subscribe", "topic": "chat_session:ses-b7e4..."}
{"type": "subscribe", "topic": "issue:iss-9a1c..."}
```
服务端下行帧(事件名与 SSE 同名,便于双端统一;携带频道内单调递增 `seq`,断线凭 `seq` 重放):
```json
{"type": "message.delta", "seq": 12, "topic": "chat_session:ses-b7e4...", "data": {"message_id": "m-9", "delta": "…"}}
{"type": "comment.created", "seq": 13, "topic": "issue:iss-9a1c...", "data": {"comment_id": "c-101", "author_type": "agent"}}
{"type": "notification.created", "seq": 14, "data": {"kind": "agent_reply", "issue_id": "iss-9a1c..."}}
```
客户端上行中断帧:`{"type": "stop", "data": {"message_id": "m-9"}}`。

**心跳与重连**:客户端每 30s 发 `{"type":"ping"}`,服务端回 `{"type":"pong"}`;断线后指数退避重连(1s/2s/4s/8s,上限 30s,加抖动);页面重新可见时立即触发一次重连(single-flight)。重连成功后用 REST 拉历史对账,增量用 `Last-Event-ID` 或 `since_seq` 补齐。

### 3.4 错误码表

| HTTP | code | 场景 |
|------|------|------|
| 400 | `invalid_request` | 参数校验失败(字段缺失/格式错误) |
| 400 | `context_not_allowed` | 上下文关联非法(issue 不存在/无权限) |
| 401 | `unauthorized` | 未携带或 token 失效 |
| 403 | `forbidden` | 对该会话/issue 无权限 |
| 404 | `not_found` | 资源不存在 |
| 409 | `generation_in_progress` | 该会话已有消息正在生成,禁止重复发送/regenerate |
| 409 | `already_resolved` | 线程已解决 |
| 413 | `payload_too_large` | 附件超限 |
| 422 | `unsupported_file_type` | 附件类型不支持 |
| 429 | `rate_limited` | 发送过于频繁/生成超配额 |
| 429 | `mention_loop_detected` | 提及触发链超深度阈值(防 agent 循环) |
| 500 | `generation_failed` | 模型侧生成失败(对应 SSE `error` 事件) |
| 503 | `agent_unavailable` | agent 运行时不可用 |

### 3.5 分页 / 鉴权 / 限流

- **分页**:游标分页 `?limit=&cursor=<opaque>`,响应 `{"data":[...],"next_cursor"}`(null 表示末页);消息/评论列表默认时间倒序。
- **鉴权**:JWT 携带 `sub`(用户 id)与 workspace 成员身份;会话仅 `owner_id` 可访问;评论遵循 issue 的可见范围。agent 在评论区回评时 `author_type='agent'`,由 agent 服务账号执行,操作可审计。
- **限流**:每用户每会话发送 QPS 限制;**每会话同一时刻至多一个并发生成**(服务端用 `idx_chat_message_streaming` 部分索引守卫,冲突返回 409 `generation_in_progress`)。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
聊天主界面(/chat)
   ├── 左:会话列表(置顶在上,按 last_message_at 倒序;agent 头像+标题+预览+时间;[+ 新建][搜索];按 agent/状态筛选;归档区在底部)
   ├── 右上:上下文关联条(关联 issue/项目,× 移除,点击开选择器)
   ├── 中:对话流(用户/agent 气泡区分左右;agent 侧带 AI 徽章;流式时光标+打字机;候选回复 ‹ 1/3 › 翻页)
   └── 底:输入区([附件📎][输入框][发送];生成中显示 [■ 停止];完成后该条尾部 [↻ 重新生成])
评论区(issue 详情页内)
   ├── 输入框(占位"评论,@ 提及…",[格式 B I `][📎][@][发布])
   ├── 主评论 + 线程回复(reply_count>2 默认折叠显示预览;@ 自动补全人/agent 混排,agent 带 AI 徽章)
   └── 已解决线程区(✓ 折叠)
收件箱(/inbox):未读提及/agent 回复/线程回复;点击直达 issue 定位评论
```

### 4.2 关键组件

- **上下文关联选择器**:搜索 issue/项目,单选关联;提示"agent 将读取关联上下文作为背景"。服务端把上下文快照注入为 system 消息,保证 agent 回答紧扣任务。**注入的 issue 上下文(标题/描述/评论/附件)显式标记为不可信数据并做结构隔离**(见 README §6「不可信内容处理」),防止恶意 issue 内容劫持 agent 行为。
- **流式气泡**:agent 回复逐 token 打字机显示;生成中输入区"停止"按钮全程可用;完成后该条尾部"重新生成"。
- **候选回复**:多候选用 `‹ 1/3 ›` 翻页,并提供"使用此条";regenerate 不覆盖旧候选,全部可回看回选。
- **评论区**:agent 评论带 AI 徽章,可展开"生成方式/运行摘要";主评论上"解决线程"按钮;解决后整线程折叠进"已解决"区。
- **@ 自动补全**:人与 agent 混排,agent 项带 AI 徽章;选中即生成 mention 链接。

### 4.3 关键交互流程

**流程 1:实时聊天**:agent 名册点"开始对话"→ 创建会话 → 顶部"关联上下文"选 issue(服务端注入快照)→ 发送提问(用户气泡乐观 UI 立即出现)→ SSE 连接建立开始流式 → agent 回复逐 token 显示,底部"停止"全程可用 → 中途点"停止"(POST stop → 流以 `message.interrupted` 结束,保留已生成部分并标"已中断")→ 点"重新生成"(新候选,可翻页切换)→ 首轮完成后台异步生成标题写回(`title_is_auto=true`),列表实时更新预览与时间。

**流程 2:异步评论协作**:在 issue 写评论并 @测试 agent → 发布时服务端写 mention 并入队 run → agent 异步运行(可能数分钟)→ 完成后 POST 评论(`author_type=agent`)→ WebSocket 向订阅该 issue 的客户端推 `comment.created` + `notification.created` → 用户收件箱收"agent 回复"通知 → 点击进入 issue 看带 AI 徽章的评论 → 用户在线程内回复再次 @ agent(深度 +1)→ 方案确认点"解决线程",线程折叠,相关方收 `comment_resolved` 通知。

**闭环:聊天沉淀为评论**:聊天里对齐方案 → 一键"沉淀为 issue 评论"(把选定消息转为 issue_comment,可附带 @ 相关 agent)→ agent 异步执行回评 → 人在收件箱确认并 resolve。

### 4.4 状态流转

**消息生成状态机**(聊天与评论 agent 回复共享语义):
```
[*] ──用户发送 / 触发 regenerate──► (idle)──message.created──► streaming
streaming ──message.done──► done
streaming ──用户 stop──► interrupted
streaming ──模型异常 / 超限──► failed
done / interrupted / failed ──点重新生成(新建候选)──► (新候选 streaming)
done ──► [*]
```
要点:
- **单会话单并发**:同一时刻只允许一条消息处于 `streaming`(用 `idx_chat_message_streaming` 部分索引快速定位),重复发送/regenerate 返回 409 `generation_in_progress`。
- `interrupted` 与 `failed` 均保留已产生内容与状态,二者都可重新生成。
- regenerate 不修改旧消息,而是新建候选并切换 `selected_candidate`,历史候选全部可回看回选。

**与 runtime 长任务状态机的衔接**:形态 B 中,agent 被 @ 提及后由 mention 管线派发为 runtime 运行记录(`agent_runs`,经 `mention.run_id` 关联),其生命周期遵循 runtime 长任务状态机 `queued→claimed→running→completed|failed|cancelled`;运行 `completed` 后 agent 把产出作为 `issue_comment`(`author_type='agent'`)回评。形态 A 的流式生成是会话内的实时推理,不走 agent_run 队列;两条路径在 UI 上以统一的 AI 徽章与消息状态呈现。

### 4.5 实时性与通知

| 事件 | 通知谁 | 通道 |
|------|--------|------|
| agent 异步回评(comment) | 该线程参与者、被提及者 | WebSocket + 收件箱 |
| 被人 @提及 | 被提及的人 | WebSocket + 收件箱(可选邮件) |
| 线程被解决 | 线程参与者 | 收件箱 |
| 聊天生成失败 | 会话 owner | 页面内提示 |

- **流式输出**:SSE 优先(原生自动重连 + 事件 id 断点续传),每 15s 一次心跳 ping 防中间设备断流;重连带 `Last-Event-ID` 对账,缓冲淘汰则降级 REST 拉历史。
- **中断**:独立 REST 端点(POST stop)或 WebSocket 上行帧;两条路径都必须幂等(重复 stop 返回 200/202 且无副作用)。
- **异步评论通知**:WebSocket 推 `comment.created` / `notification.created`;客户端离线/断线 → 重连后经 REST 对账;重要通知同时落收件箱持久化(**推送是增强,不是唯一依据**)。
- **去重**:同一用户对同一评论只产生一条通知(按 `comment_id + recipient` 聚合)。

---

## 5. 验收标准

### 5.1 功能性

- [ ] 两种形态统一在「对话」抽象:`chat_message` 与 `issue_comment` 字段语义对齐(role/author_type、content、附件),共享消息状态机、提及模型、通知管线、AI 徽章身份体系。
- [ ] 会话可关联 issue/项目上下文,服务端把上下文快照注入为 system 消息;聊天结论可一键沉淀为 issue 评论。
- [ ] 多轮历史持久化,游标倒序分页;`message_count`/`last_message_at`/`last_message_preview` 与列表一致。
- [ ] 候选回复用 `parent_id` 分支 + `selected_candidate` 选择,regenerate 不覆盖旧候选,全部可回看回选。
- [ ] 评论线程最多一层(`parent_id` 自引用);`reply_count>2` 默认折叠显示预览;resolve/unresolve 可逆。
- [ ] 提及 agent 时同事务写 mention(`run_triggered=false`)再异步派发;派发组件短暂不可用时 `idx_mention_pending` 可补扫,提及不丢。
- [ ] 评论编辑/软删除仅作者或 admin;软删除后列表不展示但线程引用保留。
- [ ] 收件箱聚合提及/agent 回复/线程回复/解决,带未读计数,支持批量已读;通知按 `comment_id + recipient` 去重。

### 5.2 性能

- [ ] **流式首字节时延(TTFB)**:用户发送到收到首个 `message.delta` P95 < 1s(不含上游模型固有延迟);SSE 心跳 15s 防断流。
- [ ] 会话列表(置顶优先 + 时间倒序)走 `idx_chat_session_owner_list`,万级会话 P95 < 200ms。
- [ ] 历史分页走 `idx_chat_message_session_time`,单并发守卫走 `idx_chat_message_streaming` 部分索引,无全表扫描。
- [ ] 游标分页在百万级消息/评论行下稳定(无 OFFSET 深翻页)。

### 5.3 安全

- [ ] **防回环(agent 循环防护三道保险)**:① mention `depth` 超阈值(如 5)拒绝触发新 run,返回 429 `mention_loop_detected`;② 同一 `(issue, agent)` 对在时间窗内(如 60s)run 去重(`idx_mention_loop`),后续提及合并进同一 run 上下文;③ 检测到异常循环时系统自动评论告警并锁定该线程(已锁定线程禁止 agent 再回评)。
- [ ] **单会话单并发**:同一时刻至多一条 `streaming` 消息,重复发送/regenerate 返回 409 `generation_in_progress`。
- [ ] **中断幂等**:重复 stop 返回 200/202 且无副作用;流连接断开时仍能经独立端点停止生成。
- [ ] **身份不可冒充**:agent 评论/消息恒带 AI 徽章,`author_type='agent'` 由服务账号写入,操作可审计。
- [ ] **审核闸门(可选)**:agent 评论可配置"正式发布前由人审核";线程 resolve/锁定后禁止 agent 再回评。
- [ ] 会话仅 owner 可访问;评论遵循 issue 可见范围;附件以 `storage_key` 引用,不暴露绝对路径;附件超限返回 413、类型不支持返回 422;发送限流,超限 429。
- [ ] @提及、resolve、删除等写操作走 auth.md 限流与审计。

### 5.4 实时

- [ ] 流式输出经 SSE(`message.created/delta/done/interrupted/error`)推送,事件带自增 `id`,断线凭 `Last-Event-ID` 续订;缓冲淘汰降级 REST 拉历史 + 重新订阅。
- [ ] **双通道同名事件对齐**:SSE 与 WebSocket 帧采用同名事件(`message.delta` 等),客户端渲染逻辑一致;WebSocket 事件携带 `seq`,断线凭 `seq` 重放。
- [ ] 异步评论经 WebSocket 推 `comment.created` / `notification.created`,在线 1s 内收到;离线/断线重连后 REST 对账,通知同时落收件箱持久化(不丢)。
- [ ] 重连指数退避(1s→30s 上限)加抖动;页面可见事件 single-flight 重连;无丢失无重复。
