# 与 Agent 对话(Chat Session)功能 Spec

> **所属层**:智能体编排层(实时聊天会话 + issue 评论区异步协作,统一在「对话」抽象之下)。
> **依赖 Spec**:`workspace`(隔离边界与复合 FK)、`member`(统一 `members.id`,README §6.1)、`agent`(对话的 agent 一方)、`issue` / `project`(会话上下文关联)、`runtime`(形态 B @提及触发的执行 `task_executions`,README §6.4)、`attachment`(统一附件 `attachments`/`attachment_links`)、`comment-inbox`(形态 B 的评论/提及/通知**唯一权威**)、`auth`(鉴权/限流/审计)。
> **被依赖**:`comment-inbox`(「从聊天沉淀为评论」调用其发表端点)。
> **技术栈基准**:FastAPI + SQLAlchemy 2.x + PostgreSQL 16 + WebSocket / SSE。
> **文档性质**:可直接指导开发的实现规格。所有命名、约束、端点、事件均以此为准则;与全局约定冲突时以 [README.md](../README.md) §6「全局权威契约」为准。

---

## 全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)

1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `UUID`(`gen_random_uuid()`);所有表含 `created_at` / `updated_at`(`TIMESTAMPTZ`,默认 `now()`,UTC);软删除统一 `deleted_at TIMESTAMPTZ NULL`。
2. **成员**:成员模型以 **README §6.1** 为唯一权威——统一 `members` 名册(`member_type = 'human' | 'agent'`,多态外键 `members.user_id` / `members.agent_id`);`owner_id` 等一律引用 `members.id`;**存储层不设 `*_type`/`*_kind` 判别列**,人类/agent 判别一律 JOIN `members.member_type`;API 响应可携带服务端计算的 `member_type` 快照(标注「真源为 members」)。
3. **多租户**:跨模块外键一律按 **README §6.2** 建复合 FK `(workspace_id, x_id) → 目标表 (workspace_id, id)`。
4. **接口**:基础路径 `/api/v1`;包络 / 分页 / 错误信封 / 过滤限制见 **README §6.14**。
5. **实时**:统一实时契约见 **README §6.7**(频道内 `seq`、`realtime_events` 持久重放、`resume_from` / `resync_required`);流式输出见 **README §6.8**(POST 创建 → GET SSE 流);事件名 `<entity>.<action>`。
6. **队列 / 投递**:业务写派生的实时事件登记 / 通知 / 执行入队经 transactional outbox(**README §6.6**);at-least-once + 幂等键(§6.5);长任务执行实体为 `task_executions` / `execution_attempts`,状态词汇见 **README §6.4**(以此为运行的唯一真源实体,不另设其他运行记录实体)。
7. **触发语义**:@提及 agent 与聊天「沉淀为评论」的触发语义以 **README §6.9** 触发矩阵为唯一权威。
8. **评论 / 提及 / 通知**:以 **comment-inbox.md** 为唯一权威(owns `comments` / `comment_mentions` / `comment_reactions` / `issue_subscriptions` / `notifications` / `notification_preferences` / `notification_delivery`),本 Spec **仅引用,不重复建表**。
9. **附件**:以 **attachment.md** 为唯一权威(owns `attachments` / `attachment_links`);聊天附件经 `attachment_links`(`linked_type='chat_message'`)关联。
10. **ORM**:SQLAlchemy 2.x 声明式约定(`Mapped` / `mapped_column`,异步会话)。

---

## 1. 功能描述

### 1.1 模块定位

Mesh 把 AI agent 当作真正的队友,队友之间的「对话」天然存在两种互补形态,本模块同时覆盖:

- **形态 A:实时聊天会话(real-time chat)** —— 人与单个 agent 的即时对话,agent 回复逐 token/逐块流式返回,体验类似 IM;适合探索、问答、头脑风暴、需要人 AI 紧密协作的快速迭代。
- **形态 B:issue 评论区的异步协作对话(async comments)** —— 围绕某个具体任务(issue),人与人、人与 agent、agent 与 agent 通过 @提及与线程回复异步往来;agent 被分派/被提及后异步执行完成,再以完整结果回评到评论区;适合任务派发、结果交付、多方评审。**形态 B 的数据模型与端点以 comment-inbox.md 为唯一权威,本 Spec 仅引用。**

二者不是替代关系,而是互补:**聊天重「过程」,评论重「结论」;聊天是 1 对 1(人↔agent),评论是 1 对多(issue 下多主体);聊天同步流式,评论异步事件驱动;聊天可携带 issue 上下文,聊天结论可一键沉淀回 issue 评论,形成闭环。**

**核心设计:把两种形态统一在「对话」抽象之下**。二者共享同一套消息状态机(`streaming/done/failed/interrupted`)、同一套提及与通知语义、同一个 AI 徽章身份体系。数据层 `chat_messages` 与 comment-inbox.md 的 `comments` 字段语义对齐(身份引用 `members.id`、`content`、附件经统一 `attachments`),上层 UI 组件库可复用 —— 这让「把聊天沉淀为评论」和「从评论拉起聊天继续」几乎零成本转换,也是 Mesh 区别于普通聊天工具的关键。**评论 / 提及 / 通知的数据模型与端点以 comment-inbox.md 为唯一权威,本 Spec 仅引用,不重复建表。**

> **全局名册约定(README §6.1 唯一权威)**:人与 agent 统一登记在 `members` 名册(`member_type ∈ {human, agent}`,`members.id` 为统一引用键)。本 Spec 中:会话发起人 `owner_id → members.id`(`member_type='human'`);会话关联的 agent `agent_id → agents.id`;形态 B 的评论作者、提及发起者/目标均引用 `members.id`,人类/agent 判别一律 JOIN `members.member_type`(**存储层不设 `*_type`/`*_kind` 判别列**;API 响应可携带服务端计算的 `member_type` 快照)。

### 1.2 功能点 + 用户场景表

**形态 A:实时聊天会话**

| # | 功能 | 说明 | 典型用户场景 |
|---|------|------|--------------|
| A1 | 发起与某 agent 的会话 | 从 agent 名册选择,创建空会话 | 产品经理在"需求分析 agent"详情页点"开始对话" |
| A2 | 多轮消息历史 | 用户消息与 agent 回复持久化,支持向上回溯 | 第二天回到昨天的会话接着上次上下文继续提问 |
| A3 | 流式输出 | POST 创建 generation → GET SSE 流逐块返回(README §6.8),打字机效果 | 提问后文本逐 token 出现,无需等待完整响应 |
| A4 | 会话携带上下文 | 关联 issue/项目,作为对话上下文注入 | 开会话时挂上 issue,agent 自动知晓其描述、评论与状态 |
| A5 | 中断当前生成 | 经独立幂等端点发 stop 信号,终止流式 | agent 答偏了,点"停止"重新组织提问 |
| A6 | 重新生成 | 对某条 agent 消息重跑(新建 generation),保留多个候选回复 | 第一版不够好,点"重新生成",在 3 个候选里挑最优 |
| A7 | 会话列表管理 | 最近会话、置顶/归档/删除、按 agent 筛选 | 重要会话置顶,已完成归档,按 agent 过滤 |
| A8 | 标题自动生成/重命名 | 首轮对话后自动总结标题;支持手动重命名 | 标题从"新对话"自动变为"登录重定向 bug 讨论" |
| A9 | 消息内引用与附件 | 引用某条消息;经统一附件(attachment.md)上传图片/文档 | 上传截图让 agent 分析;引用某条回答继续追问 |
| A10 | 消息生成状态 | streaming/done/failed/interrupted 可见可恢复 | 生成中途网络中断,该消息标"生成失败"并提供重试入口 |

**形态 B:issue 评论区的异步协作(数据模型与端点见 comment-inbox.md)**

| # | 功能 | 说明 | 典型用户场景 |
|---|------|------|--------------|
| B1 | 发表评论 | 在 issue 下发布 markdown 评论 | 开发同学在 issue 里写下实现方案 |
| B2 | @提及人/agent | 提及人→通知;提及 agent→按 README §6.9 入队一次执行并回评 | 评论里 @测试 agent,agent 执行后回评结果 |
| B3 | 线程回复 | 主评论 + 回复聚合为线程,回复折叠(最多一层) | 多人围绕同一条方案讨论,回复挂在主评论下不刷屏 |
| B4 | agent 异步回评 | agent 被分派/被提及后,执行完毕在评论区发结果 | agent 执行数分钟后把测试报告贴回评论区,带 AI 徽章 |
| B5 | 评论编辑/删除 | 作者与有权限者可编辑/软删除评论 | 修正笔误;删除无效评论 |
| B6 | 解决线程(resolve) | 标记某线程为已解决并折叠 | 方案确认,把该讨论线程标为已解决 |
| B7 | 已读/未读与收件箱 | 提及、回复、agent 回评聚合进收件箱,带未读计数 | 收件箱看到 3 条未读提及,逐条处理 |
| B8 | agent 循环防护 | 检测并切断 agent 互相提及导致的无限循环(README §6.9 护栏) | A 提及 B,B 回评又提及 A,系统在深度阈值后截断 |

**两种形态共存与互补**

| 维度 | 实时聊天(A) | 异步评论(B) |
|------|--------------|--------------|
| 参与方 | 1 人 + 1 agent | 多人 + 多 agent,围绕 issue |
| 时效 | 同步、流式 | 异步、事件驱动 |
| 承载 | 探索性过程、发散思考 | 任务结论、可交付物 |
| 触发 | 用户主动发送 | 被分派、被 @、状态变更(README §6.9) |
| 产出去向 | 留在会话内 | 落在 issue 评论区,可被链接回看 |
| 实时通道 | POST 创建 → GET SSE 流(README §6.8) | WebSocket 通知(README §6.7)+ REST 拉取 |

**闭环路径**:在聊天里对齐方案 → 一键"沉淀为 issue 评论"(预览目标 issue、最终正文、附件与 @agent 副作用后**一次提交**,见 README §6.9)→ @ 相关 agent 执行 → agent 异步回评结果 → 人在收件箱确认并 resolve 线程。

### 1.3 边界与非目标(明确不做什么)

- **不**定义 agent 运行时/模型/技能实现 —— 归 `agent.md` / `runtime.md`(本 Spec 仅消费 agent 身份与执行能力)。
- **不**定义 issue 的内容/状态领域逻辑 —— 归 `issue.md`(本 Spec 仅以 issue 为评论挂载点与聊天上下文)。
- **不**重复定义评论/提及/通知的数据模型、端点与收件箱/线程聚合视图 —— 评论/提及/通知以 **comment-inbox.md 为唯一权威**,本 Spec 仅引用。
- **不**单独定义聊天附件表 —— 归 `attachment.md`(统一 `attachments`/`attachment_links`)。
- **不**实现流式上游模型的推理细节 —— 仅声明流式协议(README §6.8)、事件契约与中断语义。
- **不**支持多人实时协同编辑同一条消息(YAGNI)。
- **不**支持群聊(多 human + 多 agent 的实时会话)—— 实时聊天为 1 人对 1 agent;多方协作走评论区。

---

## 2. 数据模型

### 2.1 ER 概览(文字图)

```
members（member.md，README §6.1）──owns──► chat_sessions ──serves──► agents（agent.md）
   (owner=human)              │  (workspace_id, context_issue_id) ──► issues（issue.md）
                              │  (workspace_id, context_project_id) ──► projects（project.md）
                              ▼ contains
                        chat_messages ──自引用──► parent_id（候选回复分支）/ quote_message_id（引用）
                              │ linked via
                              ▼
                        attachment_links（attachment.md 唯一权威：attachments/attachment_links，
                                          linked_type='chat_message'，linked_id → chat_messages.id）

形态 B（评论 / 提及 / 通知）：数据模型与端点以 comment-inbox.md 为唯一权威
   （comments / comment_mentions / comment_reactions / issue_subscriptions / notifications …），
   本 Spec 仅引用，不重复建表。
提及 agent 触发的执行实体为 task_executions（runtime.md owns，README §6.4 为运行的唯一真源实体）。

「对话」抽象：chat_messages 与 comments（comment-inbox.md）语义对齐——
   身份引用 members.id、content、统一附件、AI 徽章；「沉淀为评论 / 从评论拉起聊天」近零成本。
```

### 2.2 表:`chat_sessions`(聊天会话)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK;`UNIQUE (workspace_id, id)`(供复合 FK 引用,README §6.2) | `gen_random_uuid()` | 会话 ID |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `owner_id` | UUID | NOT NULL,复合 FK `(workspace_id, owner_id) → members(workspace_id, id)` | — | 所属用户(发起人,`member_type='human'`) |
| `agent_id` | UUID | NOT NULL,复合 FK `(workspace_id, agent_id) → agents(workspace_id, id)` | — | 关联 agent |
| `title` | TEXT | NOT NULL | `'新对话'` | 会话标题 |
| `title_is_auto` | BOOLEAN | NOT NULL | `true` | 标题是否自动生成 |
| `context_issue_id` | UUID | NULL,复合 FK `(workspace_id, context_issue_id) → issues(workspace_id, id)` | NULL | 上下文关联 issue |
| `context_project_id` | UUID | NULL,复合 FK `(workspace_id, context_project_id) → projects(workspace_id, id)` | NULL | 上下文关联项目 |
| `status` | TEXT | NOT NULL,CHECK IN ('active','archived','deleted') | `'active'` | 会话状态 |
| `last_message_at` | TIMESTAMPTZ | NULL | NULL | 最近一条消息时间(排序用) |
| `last_message_preview` | TEXT | NULL | NULL | 最近消息摘要(列表展示) |
| `message_count` | INT | NOT NULL,CHECK (>= 0) | `0` | 消息数 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `deleted_at` | TIMESTAMPTZ | NULL | NULL | 软删除时间 |

### 2.3 表:`chat_messages`(聊天消息,含候选回复分支)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK;`UNIQUE (workspace_id, id)`(README §6.2) | `gen_random_uuid()` | 消息 ID |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) | — | 隔离 |
| `session_id` | UUID | NOT NULL,复合 FK `(workspace_id, session_id) → chat_sessions(workspace_id, id)` | — | 所属会话 |
| `role` | TEXT | NOT NULL,CHECK IN ('user','agent','system') | — | 消息角色 |
| `content` | TEXT | NOT NULL | `''` | 消息内容(markdown) |
| `generation_id` | UUID | NULL | NULL | 本次生成标识(对应 §3.3 的 generation,用于 stream_url / stop) |
| `generation_status` | TEXT | NOT NULL,CHECK IN ('streaming','done','failed','interrupted') | `'done'` | 生成状态(user 消息恒为 done) |
| `parent_id` | UUID | NULL,**同会话复合 FK** `(workspace_id, session_id, parent_id)→chat_messages(workspace_id, session_id, id)` ON DELETE SET NULL (parent_id)(README §6.2 第 7 条:父消息必须同会话,数据库层强制) | NULL | 候选回复:指向其所回答的用户消息 |
| `selected_candidate` | BOOLEAN | NOT NULL | `true` | 是否为当前选中的候选回复 |
| `quote_message_id` | UUID | NULL,**同会话复合 FK** `(workspace_id, session_id, quote_message_id)→chat_messages(workspace_id, session_id, id)` ON DELETE SET NULL (quote_message_id)(README §6.2 第 7 条:引用消息必须同会话,数据库层强制) | NULL | 引用的消息 |
| `prompt_tokens` | INT | NULL | NULL | 输入 token 计数 |
| `completion_tokens` | INT | NULL | NULL | 输出 token 计数 |
| `error_message` | TEXT | NULL | NULL | 失败原因 |
| `started_at` | TIMESTAMPTZ | NULL | NULL | 生成开始时间 |
| `finished_at` | TIMESTAMPTZ | NULL | NULL | 生成结束时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

> **候选回复设计**:针对同一条用户消息,多个 agent 候选回复都挂在同一 `parent_id` 下;其中仅一条 `selected_candidate=true`;regenerate 时新建候选并把选中项切到新候选,旧候选保留,可翻页回选(不覆盖、不修改旧消息)。
>
> **同会话约束(README §6.2 第 7 条)**:候选回复(`parent_id`)与引用(`quote_message_id`)必须与当前消息**同属一个 session**——以**重叠唯一键 + 复合 FK** 在数据库层强制(被引用键 `UNIQUE (workspace_id, session_id, id)`,见 §2.8;引用方 `(workspace_id, session_id, parent_id/quote_message_id)→chat_messages(workspace_id, session_id, id)`)。**跨会话**的父消息/引用在 INSERT 时即被数据库拒绝,而非仅靠服务层校验。
>
> **附件**:消息附件经统一 `attachment_links`(`linked_type='chat_message'`,`linked_id → chat_messages.id`)关联,见 §2.4 与 attachment.md;`chat_messages` 上不设附件列。

### 2.4 聊天附件(独立建表已删除 — 引用声明)

聊天附件使用**统一附件模型**:`attachments` / `attachment_links` 由 **attachment.md 唯一拥有**(人与 agent 共用,聊天/评论/issue 附件全部经此)。聊天消息经 `attachment_links`(`linked_type='chat_message'`,`linked_id → chat_messages.id`,引用行携带 `workspace_id`;多态逻辑外键按 README §6.2 第 4 条不设物理 FK,删除一致性由软删除 + 服务层保证)关联附件;上传(签名直传三阶段)、隔离区扫描、私有签名下载语义见 attachment.md。**本 Spec 不再单独建聊天附件表,也不定义附件上传端点。**

### 2.5 issue 评论表(已删除 — 引用声明)

形态 B 的评论数据模型(`comments` 表:线程、resolve、编辑留痕、系统活动评论等)以 **comment-inbox.md §2.2 为唯一权威**,本 Spec 仅引用,**不重复建表**。`chat_messages` 与 `comments` 字段语义对齐(身份引用 `members.id`、`content`、统一附件),支撑「沉淀为评论」闭环。

### 2.6 提及表(已删除 — 引用声明)

提及数据模型(`comment_mentions` 表:`uq_mentions(comment_id, mentioned_id)` 去重、`triggered_execution_id → task_executions`)以 **comment-inbox.md §2.3 为唯一权威**,本 Spec 仅引用,**不重复建表**。提及 agent 的触发语义见 README §6.9。

### 2.7 通知表(已删除 — 引用声明)

通知数据模型(`notifications` 及 `notification_preferences` / `notification_delivery`)以 **comment-inbox.md §2.6–§2.8 为唯一权威**,本 Spec 仅引用,**不重复建表**。去噪规则见 comment-inbox.md §4.4 / README §6.13。

### 2.8 索引与约束

```sql
-- 会话:列表(时间倒序)/ 按 agent 筛选 / 反查 issue 关联会话
-- R3:置顶(is_pinned)快照列已删除——置顶真源唯一为 README §6.19 `favorites`(target_type='chat_session');
-- 列表「置顶优先」排序由服务层对请求者 favorites 做 LEFT JOIN(EXISTS 子查询计算 pinned 排序键)再按
-- (pinned DESC, last_message_at DESC) 输出,不在本表冗余快照(消除双真源漂移:此前保留 is_pinned 快照
-- 却无原子同步/修复协议)
CREATE INDEX idx_chat_sessions_owner_list
  ON chat_sessions(owner_id, last_message_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_chat_sessions_owner_agent ON chat_sessions(owner_id, agent_id, last_message_at DESC);
CREATE INDEX idx_chat_sessions_context_issue ON chat_sessions(context_issue_id) WHERE context_issue_id IS NOT NULL;

-- 消息:历史分页 / 候选回复 / 当前正在生成(并发守卫)
CREATE UNIQUE INDEX uq_chat_messages_ws_session_id ON chat_messages(workspace_id, session_id, id);  -- 供 parent_id/quote_message_id 同会话复合 FK 引用(README §6.2 第 7 条)
CREATE INDEX idx_chat_messages_session_time ON chat_messages(session_id, created_at DESC);
CREATE INDEX idx_chat_messages_parent ON chat_messages(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_chat_messages_streaming ON chat_messages(session_id) WHERE generation_status = 'streaming';
```

> 评论/提及/通知相关索引由 comment-inbox.md 定义;附件相关索引由 attachment.md 定义。本 Spec 仅保留 `chat_sessions` / `chat_messages` 的索引。

### 2.9 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `chat_sessions.workspace_id` / `chat_messages.workspace_id` | → `workspaces.id` | workspace.md | 隔离 |
| `chat_sessions.(workspace_id, owner_id)` | → `members(workspace_id, id)` | member.md(README §6.1/§6.2) | 会话发起人(复合 FK) |
| `chat_sessions.(workspace_id, agent_id)` | → `agents(workspace_id, id)` | agent.md | 会话关联 agent(复合 FK) |
| `chat_sessions.(workspace_id, context_issue_id)` | → `issues(workspace_id, id)` | issue.md | 上下文关联(复合 FK,NULL 时不约束) |
| `chat_sessions.(workspace_id, context_project_id)` | → `projects(workspace_id, id)` | project.md | 上下文关联(复合 FK) |
| `chat_messages.(workspace_id, session_id)` | → `chat_sessions(workspace_id, id)` | 本 Spec | 消息归属会话(复合 FK) |
| `chat_messages.(workspace_id, session_id, parent_id)` | → `chat_messages(workspace_id, session_id, id)` | 本 Spec | 候选回复同会话自引用(重叠复合 FK,README §6.2 第 7 条;ON DELETE SET NULL (parent_id)) |
| `chat_messages.(workspace_id, session_id, quote_message_id)` | → `chat_messages(workspace_id, session_id, id)` | 本 Spec | 引用消息同会话自引用(重叠复合 FK,README §6.2 第 7 条;ON DELETE SET NULL (quote_message_id)) |
| 消息附件 | `attachment_links(linked_type='chat_message', linked_id)` + `workspace_id` | attachment.md | 统一附件;多态逻辑外键(README §6.2 第 4 条),不建物理 FK |
| 形态 B 评论/提及/通知 | 引用 `comments` / `comment_mentions` / `notifications` 等 | comment-inbox.md | 唯一权威,本 Spec 仅引用,不持有其 FK |

> 跨租户隔离与复合 FK 的权威定义见 README §6.2;提及 agent 派发的执行实体为 `task_executions`(runtime.md owns,README §6.4),由 comment-inbox.md 的 `comment_mentions.triggered_execution_id` 引用,本 Spec 不直接持有该 FK。

---

## 3. 接口设计

REST 基础路径 `/api/v1`,集合嵌套于 `/workspaces/{ws}/`;鉴权 `Authorization: Bearer <JWT>`(见 auth.md);时间 RFC3339 UTC,id 均为 UUID。**包络 / 游标分页 / 错误信封 / 幂等写 / 过滤限制统一遵循 README §6.14**(列表 `{"data":[...],"next_cursor"}`,`next_cursor=null` 表示末页,消息列表默认时间倒序;单对象 `{"data":{...}}`)。

### 3.1 REST 端点清单

**形态 A:聊天会话**

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| POST | `/workspaces/{ws}/chat-sessions` | 创建会话 | 成员 |
| GET | `/workspaces/{ws}/chat-sessions` | 会话列表(可按 agent/status 筛选) | 成员(仅自己) |
| GET | `/workspaces/{ws}/chat-sessions/{id}` | 会话详情 | 会话 owner |
| PATCH | `/workspaces/{ws}/chat-sessions/{id}` | 更新标题/置顶/归档/上下文 | 会话 owner |
| DELETE | `/workspaces/{ws}/chat-sessions/{id}` | 删除(软删除) | 会话 owner |
| POST | `/workspaces/{ws}/chat-sessions/{id}/messages` | 发送消息 → `201 {message_id, generation_id, stream_url}`(README §6.8) | 会话 owner |
| GET | `/workspaces/{ws}/chat-sessions/{id}/messages` | 历史(游标分页,倒序) | 会话 owner |
| GET | `/workspaces/{ws}/chat-sessions/{id}/generations/{generation_id}/stream` | SSE 流(**GET**,EventSource 兼容,`Last-Event-ID` 断点续传) | 会话 owner |
| POST | `/workspaces/{ws}/chat-sessions/{id}/generations/{generation_id}/stop` | 中断生成(**独立幂等端点**) | 会话 owner |
| POST | `/workspaces/{ws}/chat-sessions/{id}/messages/{msg_id}/regenerate` | 重新生成 → `201` 新 generation + `stream_url` | 会话 owner |
| POST | `/workspaces/{ws}/chat-sessions/{id}/messages/{msg_id}/select` | 选择候选回复 | 会话 owner |

> 聊天附件上传/下载经 **attachment.md 的统一端点**(签名直传),以 `attachment_links`(`linked_type='chat_message'`)关联消息;本 Spec 不定义附件上传端点。

**形态 B:issue 评论(引用声明)**

评论的端点清单与请求/响应契约以 **comment-inbox.md §3.1 / §3.2 为唯一权威**(发表 / 列表 / 回复 / 编辑 / 删除 / resolve / unresolve / reactions / 提及解析)。本 Spec **不再重复定义形态 B 端点**;UI 上「从聊天沉淀为评论」直接调用 comment-inbox.md 的发表评论端点(携带 `suppress_triggers` 选项,见 README §6.9)。

### 3.2 请求/响应 JSON 示例

> 形态 B(评论/提及/通知)的请求/响应示例见 **comment-inbox.md §3.1 / §3.2(权威)**,本节仅示例形态 A(聊天)。

**创建会话** `POST /api/v1/workspaces/{ws}/chat-sessions`
```json
// Request
{ "agent_id": "agt-3f2b...", "context_issue_id": "iss-9a1c...",
  "context_project_id": null, "title": "登录重定向 bug 讨论" }
// 201 Response
{ "data": {
    "id": "ses-b7e4...", "workspace_id": "ws-001", "owner_id": "mem-1111...",
    "agent_id": "agt-3f2b...", "title": "登录重定向 bug 讨论", "title_is_auto": false,
    "context_issue_id": "iss-9a1c...", "context_project_id": null,
    "status": "active", "pinned": false, "last_message_at": null,
    "last_message_preview": null, "message_count": 0,
    "created_at": "2026-07-24T09:00:00Z", "updated_at": "2026-07-24T09:00:00Z" } }
```

> **R3:`pinned` 为服务端计算字段,非存储列**——置顶真源唯一为 README §6.19 `favorites`(`target_type='chat_session'`,成员私有);响应中的 `pinned` 是"请求者是否收藏该会话"的服务端快照(同 README §6.1 `member_type` 响应快照模式,标注"真源为 favorites"),`chat_sessions` 上**不再有 `is_pinned` 列**(删除快照,消除双真源);置顶/取消置顶经 `PUT/DELETE /api/v1/favorites/chat_session/{id}`(README §6.19 端点),本模块不提供独立 pin 端点。

**会话列表(游标分页 + 筛选)** `GET /api/v1/workspaces/{ws}/chat-sessions?agent_id=agt-3f2b...&status=active&limit=20`
```json
{ "data": [
    { "id": "ses-b7e4...", "agent_id": "agt-3f2b...", "title": "登录重定向 bug 讨论",
      "status": "active", "pinned": true, "last_message_at": "2026-07-24T10:12:33Z",
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
      "attachments": [{"id": "att-1", "file_name": "screenshot.png", "mime_type": "image/png", "byte_size": 84213}] }
  ],
  "next_cursor": null }
```
> 响应中的 `attachments` 为由 `attachment_links`(`linked_type='chat_message'`)聚合出的内联快照,附件真源与签名下载 URL 见 attachment.md。

**发送消息(POST 创建 generation,不直接返回流 — README §6.8)** 见 §3.3。

### 3.3 流式输出协议(重点,README §6.8 唯一权威)

> **浏览器原生 EventSource 不支持 POST SSE**(EventSource 只能发 GET)。因此 Mesh 采用 README §6.8 的「**POST 创建 → GET 流**」模式:发送消息 / 重新生成仅创建一次 generation 并返回 `stream_url`,真正的流式消费是对 `stream_url` 的 **GET**(EventSource 兼容,原生自动重连 + `Last-Event-ID` 断点续传);中断走**独立幂等端点**。本节事件名跨 SSE / WebSocket 保持一致(见 README §6.7/§6.8)。

#### 通道分工

| 通道 | 职责 | 说明 |
|------|------|------|
| GET SSE(`stream_url`) | 单次生成的增量流 | EventSource 兼容:原生自动重连、`Last-Event-ID` 断点续传、易过代理 |
| WebSocket(README §6.7) | 会话/issue 频道实时事件 | 生成终态广播、形态 B 评论/通知;频道内 `seq` + `realtime_events` 持久重放 |

#### 第一步:发送消息(POST 创建 generation)

```http
POST /api/v1/workspaces/{ws}/chat-sessions/{id}/messages
Authorization: Bearer <JWT>
Content-Type: application/json
Idempotency-Key: <可选,README §6.14 幂等写>

{ "content": "帮我分析这个 bug 的可能原因", "attachment_ids": ["att-1"] }
```
```json
// 201 Response（JSON，不是流）
{ "data": {
    "message_id": "m-9",
    "generation_id": "gen-42",
    "stream_url": "/api/v1/workspaces/{ws}/chat-sessions/ses-b7e4.../generations/gen-42/stream" } }
```
> 该 POST 同步完成:user 消息落库、agent 消息以 `generation_status='streaming'` 预创建(单会话单并发守卫,见 §3.5)、上游推理开始;返回的 `stream_url` 供第二步消费。

#### 第二步:GET stream_url 消费流(SSE)

```http
GET /api/v1/workspaces/{ws}/chat-sessions/ses-b7e4.../generations/gen-42/stream
Authorization: Bearer <JWT>
Accept: text/event-stream
Last-Event-ID: 3            # 断线重连时由 EventSource 自动携带
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

客户端以 `new EventSource(stream_url)` 订阅(GET 请求,**原生支持自动重连**);每个事件带自增数字 `id:`,断线后 EventSource 自动携带 `Last-Event-ID` 重连,服务端从断点重放 delta(delta 缓冲由服务端缓存承载;若缓冲已淘汰,客户端降级为「REST 拉一次该消息最终内容 + 重新订阅」)。客户端也可显式选择 **fetch streaming**(ReadableStream),此时**自行实现重连与 `Last-Event-ID` 对账,不得声称「原生自动重连」**。

#### SSE / WebSocket 同名事件类型表(README §6.7/§6.8)

| event | 触发时机 | data 关键字段 |
|-------|----------|----------------|
| `message.created` | agent 消息创建、生成开始前 | `message_id`, `role`, `generation_status=streaming` |
| `message.delta` | 增量文本块(token/句) | `message_id`, `delta` |
| `message.done` | 生成正常完成 | `message_id`, `completion_tokens`, `generation_status=done` |
| `message.interrupted` | 被 stop 中断 | `message_id`, `partial_content`, `generation_status=interrupted` |
| `error` | 生成失败(模型异常、超限等) | `message_id`, `code`, `message` |
| `ping` | 心跳(建议 15s 一次) | `ts` |

> WebSocket 侧的 `seq` 语义(**频道内**单调、持久化于 `realtime_events`、`resume_from` / `resync_required`)见 **README §6.7**,本 Spec 不重复定义。

#### 中断通道(独立幂等端点)

```http
POST /api/v1/workspaces/{ws}/chat-sessions/{id}/generations/gen-42/stop
Authorization: Bearer <JWT>
```
```json
// 202 Response
{ "data": { "generation_id": "gen-42", "message_id": "m-9", "generation_status": "interrupted" } }
```
服务端收到后:停止上游模型生成 → 在 SSE 流上发出 `message.interrupted` → 关闭该流。**stop 必须幂等:重复 stop 返回 200/202 且无副作用**,确保"流断了也能停"。

#### 重新生成与选择候选

```http
POST /api/v1/workspaces/{ws}/chat-sessions/{id}/messages/m-1/regenerate
Authorization: Bearer <JWT>
```
```json
// 201 Response（新 generation，客户端随后 GET stream_url 消费新流）
{ "data": {
    "message_id": "m-11",
    "generation_id": "gen-43",
    "stream_url": "/api/v1/workspaces/{ws}/chat-sessions/ses-b7e4.../generations/gen-43/stream" } }
```
服务端:新建一条 agent 候选(`parent_id=m-1`,新候选默认 `selected_candidate=true`,旧候选置 false)→ 客户端对 `stream_url` 发起 GET 开始流式。

```http
POST /api/v1/workspaces/{ws}/chat-sessions/{id}/messages/m-1/select
Content-Type: application/json

{ "selected_message_id": "m-11" }
// 200 Response
{ "data": { "parent_id": "m-1", "selected_message_id": "m-11" } }
```

#### WebSocket 实时网关(统一实时通道,README §6.7)

连接 `/ws`(握手鉴权见 auth.md;**禁止在 URL query 参数中传递 token**,使用连接建立后首帧认证单一机制(README §6.16,v0.1.0 起实现基线),避免 token 落入访问日志与中间代理);按频道订阅(频道命名遵循 README §6.7,如 `chat_session:ses-b7e4...` / `issue:iss-9a1c...`,订阅时逐资源授权):
```json
{"type": "subscribe", "channel": "chat_session:ses-b7e4..."}
{"type": "subscribe", "channel": "issue:iss-9a1c..."}
```
服务端下行帧(事件名与 SSE 同名,便于双端统一;携带**频道内**单调递增 `seq`,由 `realtime_events` 同事务分配、持久真源):
```json
{"type": "message.done", "channel": "chat_session:ses-b7e4...", "seq": 12, "data": {"message_id": "m-9", "generation_status": "done"}}
{"type": "comment.created", "channel": "issue:iss-9a1c...", "seq": 13, "data": {"comment_id": "c-101", "member_type": "agent"}}
{"type": "notification.created", "channel": "member:mem-1111...:inbox", "seq": 14, "data": {"type": "execution_finished", "issue_id": "iss-9a1c..."}}
```
> `member_type` 为服务端计算快照(真源为 `members`,README §6.1)。形态 B 事件(comment.* / notification.*)的权威定义见 comment-inbox.md §3.6。

**重连与重放(README §6.7)**:客户端记各频道 `last_seq`,重连带 `resume_from=<last_seq+1>`,网关从 `realtime_events` 顺序补发;游标过旧(早于保留窗口)收 `{"op": "resync_required", "watermark": ..., "rest": "<对账 REST URL>"}` 后走 REST 对账,无感恢复。

**心跳与退避**:客户端每 30s 发 `{"type":"ping"}`,服务端回 `{"type":"pong"}`;断线后指数退避重连(1s/2s/4s/8s,上限 30s,加抖动);页面重新可见时立即触发一次重连(single-flight)。

### 3.4 错误码表

> 错误信封与 HTTP 语义遵循 README §6.14(`{"error":{"code","message","details"}}`,message 不泄漏堆栈/SQL/内部 ID);下表为本模块具名 code 补充。

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 参数校验失败(字段缺失/格式错误) |
| 400 | `context_not_allowed` | 上下文关联非法(issue 不存在/无权限) |
| 401 | `unauthorized` | 未携带或 token 失效 |
| 403 | `forbidden` | 对该会话无权限 |
| 404 | `not_found` | 资源不存在 |
| 409 | `generation_in_progress` | 该会话已有消息正在生成,禁止重复发送/regenerate |
| 413 | `payload_too_large` | 附件超限(细则见 attachment.md) |
| 422 | `unsupported_file_type` | 附件类型不支持(细则见 attachment.md) |
| 429 | `rate_limited` | 发送过于频繁/生成超配额(带 `Retry-After`) |
| 500 | `generation_failed` | 模型侧生成失败(对应 SSE `error` 事件) |
| 503 | `agent_unavailable` | agent 执行环境(runtime)不可用 |

### 3.5 分页 / 鉴权 / 限流

- **包络 / 分页 / 错误**:统一遵循 **README §6.14**(成功包络、opaque 游标 keyset 分页、错误信封、过滤限制);消息列表默认时间倒序,`next_cursor=null` 表示末页。
- **鉴权**:JWT 携带 `sub`(用户 id)与 workspace 成员身份;会话仅 `owner_id` 可访问;形态 B 评论遵循 issue 的可见范围(见 comment-inbox.md)。agent 在评论区回评以其 `members` 身份执行(`member_type='agent'` 由服务端 JOIN 解析),操作可审计。
- **限流**:每用户每会话发送 QPS 限制;**每会话同一时刻至多一个并发生成**(服务端用 `idx_chat_messages_streaming` 部分索引守卫,冲突返回 409 `generation_in_progress`)。
- **幂等写**:创建消息 / regenerate / stop 支持 `Idempotency-Key` 请求头(README §6.14/§6.5);重复键返回首次结果。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
聊天主界面（/chat）
   ├── 左:会话列表（置顶在上,按 last_message_at 倒序;agent 头像+标题+预览+时间;[+ 新建][搜索];按 agent/状态筛选;归档区在底部）
   ├── 右上:上下文关联条（关联 issue/项目,× 移除,点击开选择器）
   ├── 中:对话流（用户/agent 气泡区分左右;agent 侧带 AI 徽章;流式时光标+打字机;候选回复 ‹ 1/3 › 翻页）
   └── 底:输入区（[附件📎 经 attachment.md 直传][输入框][发送];生成中显示 [■ 停止];完成后该条尾部 [↻ 重新生成]）

评论区与收件箱（形态 B）
   └── 信息架构、组件与交互以 comment-inbox.md §4 为唯一权威（本 Spec 仅消费其端点与事件）
```

### 4.2 关键组件

- **上下文关联选择器**:搜索 issue/项目,单选关联;提示"agent 将读取关联上下文作为背景"。服务端把上下文快照注入为 system 消息,保证 agent 回答紧扣任务。**注入的 issue 上下文(标题/描述/评论/附件)显式标记为不可信数据并做结构隔离**(见 README §6.15「不可信内容处理」),防止恶意 issue 内容劫持 agent 行为。
- **流式气泡**:agent 回复逐 token 打字机显示(GET SSE 流驱动);生成中输入区"停止"按钮全程可用;完成后该条尾部"重新生成"。
- **候选回复**:多候选用 `‹ 1/3 ›` 翻页,并提供"使用此条";regenerate 不覆盖旧候选,全部可回看回选。
- **附件**:经 attachment.md 签名直传(隔离区扫描完成后才可见/可下载),消息内以缩略图/文件卡呈现。
- **评论区 / @ 自动补全 / 收件箱组件**:以 comment-inbox.md §4.1–§4.2 为唯一权威(agent 评论 AI 徽章、@ 候选「发布后将触发一次运行」提示、trigger preview、解决线程等),本 Spec 不重复定义。

### 4.3 关键交互流程

**流程 1:实时聊天**:agent 名册点"开始对话"→ 创建会话 → 顶部"关联上下文"选 issue(服务端注入快照)→ 发送提问(用户气泡乐观 UI 立即出现;**POST 创建 generation 拿到 `stream_url`**)→ **GET `stream_url` 建立 SSE 流**(EventSource)→ agent 回复逐 token 显示,底部"停止"全程可用 → 中途点"停止"(**POST `generations/{id}/stop`**,幂等 → 流以 `message.interrupted` 结束,保留已生成部分并标"已中断")→ 点"重新生成"(**POST regenerate 得新 generation + stream_url**,GET 新流,可翻页切换候选)→ 首轮完成后台异步生成标题写回(`title_is_auto=true`),列表实时更新预览与时间。

**流程 2:异步评论协作(权威流程见 comment-inbox.md)**:在 issue 写评论并 @测试 agent → 发布时服务端按 **README §6.9** 经 outbox 入队一次执行(`task_executions`,`trigger='mention'`)→ agent 异步执行(可能数分钟)→ 完成后以 agent 身份回评原线程(`comments.author_id → members.id`,`member_type='agent'`)→ WebSocket 按 README §6.7 推 `comment.created` + `notification.created` → 用户在收件箱收"agent 执行完成"通知 → 点击进入 issue 看带 AI 徽章的评论 → 方案确认点"解决线程"。端点、触发矩阵与去噪规则均以 comment-inbox.md 为准。

**闭环:聊天沉淀为评论(README §6.9)**:聊天里对齐方案 → 点"沉淀为 issue 评论"弹出预览:**目标 issue、最终正文、附件清单、@agent 副作用预览(将被触发的 agent 列表)** → 确认后**一次提交**(调用 comment-inbox.md 的发表评论端点创建评论 + 解析提及并入队执行;可勾选"仅通知不运行"即请求体 `suppress_triggers: true`)→ agent 异步执行回评 → 人在收件箱确认并 resolve。

### 4.4 状态流转

**消息生成状态机**(聊天):
```
[*] ──用户发送 / 触发 regenerate──► (idle)──message.created──► streaming
streaming ──message.done──► done
streaming ──用户 stop──► interrupted
streaming ──模型异常 / 超限──► failed
done / interrupted / failed ──点重新生成(新建候选)──► (新候选 streaming)
done ──► [*]
```
要点:
- **单会话单并发**:同一时刻只允许一条消息处于 `streaming`(用 `idx_chat_messages_streaming` 部分索引快速定位),重复发送/regenerate 返回 409 `generation_in_progress`。
- `interrupted` 与 `failed` 均保留已产生内容与状态,二者都可重新生成。
- regenerate 不修改旧消息,而是新建候选并切换 `selected_candidate`,历史候选全部可回看回选。

**与长任务执行状态机的衔接**:形态 B 中,agent 被 @ 提及后由 comment-inbox.md 的提及管线经 transactional outbox(README §6.6)入队 `task_executions`(`trigger='mention'`,经 `comment_mentions.triggered_execution_id` 关联),其生命周期遵循 **README §6.4 长任务状态机**(`queued → claimed → running → completed / failed / timeout`,含 `requeued` / `cancelling` / `cancelled` / `awaiting_approval`);执行 `completed` 后 agent 把产出作为 `comments` 回评(`author_id → members.id`,`member_type='agent'` 为快照)。形态 A 的流式生成是会话内的实时推理(generation),**不入 `task_executions` 队列**;两条路径在 UI 上以统一的 AI 徽章与消息状态呈现。

### 4.5 实时性与通知

| 事件 | 通知谁 | 通道 |
|------|--------|------|
| 形态 A 生成完成/失败/中断 | 会话 owner | SSE 流内事件 + 页面内提示 |
| 形态 B agent 异步回评 / 被 @ / 线程解决 | 线程参与者、被提及者 | 见 comment-inbox.md(WebSocket + 收件箱) |

- **流式输出**:按 **README §6.8**「POST 创建 → GET SSE 流」;GET `stream_url` 由原生 EventSource 承载,**自动重连 + `Last-Event-ID` 断点续传**;每 15s 一次心跳 ping 防中间设备断流;缓冲淘汰降级 REST 拉最终内容 + 重新订阅。
- **中断**:独立幂等端点 `POST .../generations/{id}/stop`(重复 stop 无副作用)。
- **实时事件**:WebSocket 频道事件的 `seq` / 重放 / 对账统一遵循 **README §6.7**(频道内 `seq`、`realtime_events` 持久化、`resume_from` / `resync_required`);离线/断线重连后对账,通知同时落收件箱持久化(**推送是增强,不是唯一依据**)。
- **派生动作走 outbox**:消息/生成状态变更派生的实时事件登记与通知经 **transactional outbox(README §6.6)**,杜绝"业务已提交但推送未登记"的丢失。
- 形态 B 的通知去噪(去重、聚合、静音、分级、重读规则)以 **comment-inbox.md §4.4 / README §6.13** 为准,本 Spec 不重复定义。

---

## 5. 验收标准

### 5.1 功能性

- [ ] 两种形态统一在「对话」抽象:`chat_messages` 与 comment-inbox.md 的 `comments` 语义对齐(身份引用 `members.id`、`content`、统一附件),共享消息状态机与 AI 徽章身份体系;**评论/提及/通知引用 comment-inbox.md 权威表与端点,不重复建表(本 Spec 无任何评论/提及/通知表定义)**。
- [ ] **附件走统一 `attachments` / `attachment_links`(attachment.md 权威)**:聊天附件经 `attachment_links`(`linked_type='chat_message'`)关联消息,**不存在独立的聊天附件表**;扫描完成前不可下载。
- [ ] 会话可关联 issue/项目上下文(复合 FK),服务端把上下文快照注入为 system 消息;聊天结论可一键沉淀为 issue 评论。
- [ ] 多轮历史持久化,游标倒序分页(README §6.14);`message_count`/`last_message_at`/`last_message_preview` 与列表一致。
- [ ] 候选回复用 `parent_id` 分支 + `selected_candidate` 选择,regenerate 不覆盖旧候选,全部可回看回选。
- [ ] **闭环「沉淀为评论」(README §6.9)**:沉淀前展示**目标 issue、最终正文、附件与 @agent 副作用预览(trigger preview)**,确认后**一次提交**;支持 `suppress_triggers: true` 仅通知不运行。
- [ ] **触发语义符合 README §6.9**:`@agent` 入队/抑制语义与 comment-inbox.md 一致,文档与实现无"合并/排队"式不可测试表述。

### 5.2 性能

- [ ] **流式首字节时延(TTFB)**:用户发送到收到首个 `message.delta` P95 < 1s(不含上游模型固有延迟);SSE 心跳 15s 防断流。
- [ ] 会话列表(置顶优先 + 时间倒序)走 `idx_chat_sessions_owner_list`,万级会话 P95 < 200ms。
- [ ] 历史分页走 `idx_chat_messages_session_time`,单并发守卫走 `idx_chat_messages_streaming` 部分索引,无全表扫描。
- [ ] 游标分页在百万级消息行下稳定(无 OFFSET 深翻页)。
- [ ] 性能基准按 **README §10**(标注冷/热缓存)。

### 5.3 安全

- [ ] **单会话单并发**:同一时刻至多一条 `streaming` 消息,重复发送/regenerate 返回 409 `generation_in_progress`。
- [ ] **中断幂等**:重复 stop 返回 200/202 且无副作用;流连接断开时仍能经独立端点停止生成。
- [ ] **身份不可冒充**:agent 消息恒带 AI 徽章,`member_type` 由服务端 JOIN `members` 解析(API 快照标注"真源为 members");**存储层无任何人类/agent 判别冗余列**;操作可审计。
- [ ] **防回环**:形态 B 的 agent 互提循环防护由 **comment-inbox.md §3.5 与 README §6.9 护栏**(链深度/频率)统一实现,本 Spec 不另建机制。
- [ ] **跨租户复合 FK(集成测试 T1)**:`chat_sessions` / `chat_messages` 的 owner/agent/context 引用均为复合 FK `(workspace_id, x_id)`,跨工作区插入被数据库约束拒绝;A 区凭证访问 B 区会话返回 403/404。
- [ ] **同会话约束(README §6.2 第 7 条 / §9 T1 同类)**:`parent_id` / `quote_message_id` 经**重叠复合 FK** `(workspace_id, session_id, parent_id/quote_message_id)→chat_messages(workspace_id, session_id, id)` 强制同 session;构造**跨会话**的父消息/引用在 INSERT 时被数据库拒绝(被引用唯一键 `uq_chat_messages_ws_session_id`,§2.8)。
- [ ] 会话仅 owner 可访问;附件经 attachment.md 签名 URL 访问,不暴露存储绝对路径;附件超限/类型非法按 attachment.md 错误码返回;发送限流,超限 429。
- [ ] @提及、删除等写操作走 auth.md 限流与审计。

### 5.4 实时

- [ ] **流式已按 README §6.8 修正**:发送/重新生成的 POST 仅创建 generation 并返回 `stream_url`,流式消费一律为 **GET SSE**(原生 EventSource 自动重连 + `Last-Event-ID`);文档与实现中**无任何"同一 POST 请求直接返回事件流并声称原生自动重连"的表述**。
- [ ] 流式事件 `message.created/delta/done/interrupted/error` 经 GET `stream_url` 推送,事件带自增 `id`,断线凭 `Last-Event-ID` 续订;缓冲淘汰降级 REST 拉最终内容 + 重新订阅。
- [ ] **实时按 README §6.7**:WebSocket 事件 `seq` 为**频道内**单调、持久化于 `realtime_events`,重连凭 `resume_from` 重放,游标过旧收 `resync_required` 后 REST 对账;本 Spec 不重复定义 seq 契约。
- [ ] **双通道同名事件对齐**:SSE 与 WebSocket 采用同名事件(`message.delta` 等),客户端渲染逻辑一致。
- [ ] 派生实时事件登记经 **transactional outbox(README §6.6,集成测试 T5)**:relay 崩溃重启后事件不丢失。
- [ ] 重连指数退避(1s→30s 上限)加抖动;页面可见事件 single-flight 重连。
