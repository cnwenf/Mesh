# 成员(Member)功能 Spec

> **所属层**:基础能力层(统一成员名册,人与 AI agent 对称的一等队友抽象)。
> **依赖的其他 Spec**:
> - `workspace.md`:`members.workspace_id` 外键回 `workspaces.id`;邀请落地为名册条目。
> - `auth.md`:`users`(人类登录身份)、角色权限矩阵、鉴权中间件、审计、API token(agent 运行凭证)。
> - `agent.md`:`agents`(AI agent 身份与运行时/技能/模型配置);本 Spec 只消费 agent 的核心身份字段。
> **被依赖方(核心抽象)**:`issue.assignee_id` / `reporter_id`、评论 `author_id`、@提及目标、通知收件人 **一律引用 `members.id`**,使引用方无需关心被引用者是人还是 agent。

---

## 1. 功能描述

### 1.1 模块定位

成员(Member)是 Mesh **"AI agent 作为一等队友"** 这一核心范式在数据层的关键支撑。

核心抽象:把"可被分派、可发言、可被 @ 的实体"统一物化为 **成员(member)** 名册条目。名册条目要么指向一个人类用户(`users`),要么指向一个 AI agent(`agents`),由类型判别器 + 多态外键表达。**所有引用点(issue 负责人、评论作者、@提及、通知)都指向 `members.id`**,而非各自重复携带 `assignee_type` + 两套外键。这样人类与 agent 在分派、协作、通知上完全对称。

角色是**工作区级**的(存在名册条目上),同一用户在不同工作区可有不同角色。权限校验采用"角色 → 能力"映射(见 auth.md 权限矩阵),而非散落硬编码。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| M1 | 人类角色 | owner/admin/member/guest 四级,工作区级 | 团队负责人(admin)管理成员与流程 |
| M2 | agent 进入名册 | AI agent 与人类同列于成员页,有头像/名称/简介 | 把"代码助手 agent"加入工作区,成员页可见 |
| M3 | agent 可被分派 | issue assignee 既可是人也可是 agent,统一引用 member | 把"修复登录 bug"分派给代码 agent |
| M4 | agent 独立身份 | agent 以自己身份发评论/改状态/被 @,可追溯 | 看板卡片显示"由 代码助手 处理中" |
| M5 | agent 角色受限 | agent 通常为受限 `member` 级能力,不可担任 owner | agent 可改 issue、发评论,不能删工作区 |
| M6 | 人↔agent 一致体验 | @提及、分派、通知对二者用同一套交互 | 评论 @代码助手 触发其接管任务 |
| M7 | 资料/头像 | 人类全名/头像/简介/时区;agent 名称/头像/能力描述 | 悬停 agent 头像看到"擅长:代码修复" |
| M8 | 工作区内显示名覆盖 | 允许工作区显示与全局不同的昵称 | 用中文名显示 |
| M9 | 停用/启用 | 保留历史,禁止操作;名下 issue 可转派 | 员工离职停用,保留历史评论 |
| M10 | 移除成员 | 解除成员关系(区别于停用) | 项目结束移除外包 |
| M11 | 资产再分配 | 停用/移除时批量转派未完成 issue | 离职者 20 个进行中 issue 转派接手人 |
| M12 | guest 项目级可见性 | guest 仅可见被显式共享的项目/issue | 外部客户只看某项目进度 |

### 1.3 边界与非目标(明确不做什么)

- **不**定义 agent 的运行时绑定、技能、模型配置、调度——归 `agent.md`(本 Spec 仅在名册中引用 agent 身份)。
- **不**定义角色→能力的权限矩阵、鉴权中间件、审计日志——归 `auth.md`(本 Spec 声明各端点所需角色与 owner/agent 保护约束)。
- **不**定义邀请流程、工作区创建——归 `workspace.md`。
- **不**定义 issue 分派的领域逻辑(状态联动等)——归 `issue.md`(本 Spec 只提供"谁是合法 assignee")。
- **不**实现用户的全局账号注册/登录/密码——归 `auth.md`。
- **不**支持自定义角色(YAGNI;角色为固定枚举,权限矩阵在 auth.md 声明式维护)。

---

## 2. 数据模型

### 2.1 ER 概览(文字图)

```
users(人类登录身份,auth.md)──1:N──┐
                                   ├──► members(统一名册) ◄──1:N── agents(AI,agent.md)
                                   │         │  ▲
                                   │         │  └─ members.id 被以下统一引用:
                                   │         │      • issues.assignee_id / reporter_id
                                   │         │      • comments.author_id
                                   │         │      • comment_mentions.mentioned_id
                                   │         │      • notifications.recipient_id
                                   │         │      • api_tokens.owner_member_id
                                   └─────────┘
                                         │ N:1
                                         ▼
                                    workspaces(workspace.md)

members ──1:N──► member_project_access(guest 项目级可见性)──N:1──► projects
```

**为什么用统一 member 而非多态 assignee**:若 issue 直接存 `assignee_id + assignee_type`,则每个引用点(issue、评论、提及、通知)都要重复携带 type 字段并各自处理两种外键。把"成员"物化成一张名册表,所有引用都指向 `members.id`,引用方无需关心被引用者是人还是 agent。

### 2.2 表:`members`(统一成员名册 —— 核心)

> **本表为全系统成员模型的唯一权威定义(README §6.1)**;`users`/`agents` **不设 `member_id` 反向列**,关联方向恒为 `members.user_id → users.id` / `members.agent_id → agents.id`(尤其禁止 `users.member_id UNIQUE` 这类 1:1 反向关联——它不支持同一 user 加入多个工作区)。
>
> `members.id` 是**统一引用键**。一个工作区的名册条目,要么指向一个 user,要么指向一个 agent。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | **成员 ID(统一引用键)** |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | 所属工作区 |
| `member_type` | TEXT | NOT NULL,CHECK IN ('human','agent') | `'human'` | 类型判别器 |
| `user_id` | UUID | NULL,FK→users(id) ON DELETE CASCADE | — | 人类成员指向 users(`users` 为全局表,无 workspace_id,故为简单 FK) |
| `agent_id` | UUID | NULL,复合 FK `(workspace_id, agent_id) → agents(workspace_id, id)` ON DELETE CASCADE | — | AI agent 成员指向 agents(同工作区强制,README §6.2) |
| `role` | TEXT | NOT NULL,CHECK IN ('owner','admin','member','guest') | `'member'` | 工作区角色 |
| `status` | TEXT | NOT NULL,CHECK IN ('active','disabled','removed') | `'active'` | |
| `display_override` | TEXT | NULL | NULL | 工作区内显示名覆盖 |
| `search_name` | TEXT | NOT NULL | `''` | **检索专用投影(MES-76 H3 登记,search-command-palette.md §2.2 owns 同步契约与归一函数)**:`mesh_search_norm(§2.4 显示名解析链结果)`(NFKD + 去重音 + 小写,与索引/查询/回填同一归一函数,R2-H3),供全局搜索 trigram 与前缀 pattern 索引;**非显示真源**——显示一律实时解析链(§2.4),本列由入册/改名写路径同事务维护 + 周期对账兜底,防跨表表达式不可索引(README §6.1「高频表存储快照须强制一致并明示」条款登记项) |
| `joined_at` | TIMESTAMPTZ | NULL | NULL | 正式加入时间 |
| `disabled_at` | TIMESTAMPTZ | NULL | NULL | 停用时间 |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**多态外键一致性 CHECK(关键)**:
```sql
CHECK (
  (member_type = 'human' AND user_id IS NOT NULL AND agent_id IS NULL)
  OR
  (member_type = 'agent' AND agent_id IS NOT NULL AND user_id IS NULL)
)
```

**agent 不可为 owner**:
```sql
CHECK (member_type = 'human' OR role <> 'owner')
```

### 2.3 表:`member_project_access`(guest 项目级可见性)

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `workspace_id` | UUID | NOT NULL,FK→workspaces(id) ON DELETE CASCADE | — | 隔离键(供复合 FK,README §6.2) |
| `member_id` | UUID | NOT NULL,复合 FK `(workspace_id, member_id) → members(workspace_id, id)` ON DELETE CASCADE | — | guest 成员 |
| `project_id` | UUID | NOT NULL,复合 FK `(workspace_id, project_id) → projects(workspace_id, id)` ON DELETE CASCADE | — | 被共享项目 |
| `permission` | TEXT | NOT NULL,CHECK IN ('read','write') | `'read'` | |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

约束:`UNIQUE (member_id, project_id)`。复合 FK 保证成员与项目同属一工作区(README §6.2,集成测试 T1)。

### 2.4 引用的身份表(由其它 Spec 定义,此处仅列名册消费字段)

**`users`**(auth.md 拥有,真源见 auth.md §2.2):名册 JOIN 消费的展示字段为 `id`、`email`、`display_name`、`avatar_url`(users 仅有单一 `display_name` 名列,**无 `full_name`/`bio`/`is_active`/`last_seen_at` 列**;账号 `status`、展示偏好 `timezone`/`settings` 等其余字段归 auth.md)。

**`agents`**(agent.md 拥有):`id`、`name`、`avatar_url`、`description`、`owner_user_id`、`runtime_ref`、`config`(JSONB)、`is_active`。

> 名册查询通过 `members.user_id` / `agent_id` JOIN 到上述表取展示字段;名册本身不冗余存储 profile,避免双写漂移(列表接口可物化/缓存)。

**显示名解析顺序**(所有 UI 与 API 统一,避免各处不一致):
1. `members.display_override`(工作区内覆盖,若非空)→
2. 人类:`users.display_name`(若非空)→ 邮箱本地段(`users.email` 的 `@` 前缀)→ 成员短 id(`member-<id 前 8 位>`)兜底;
   agent:`agents.name`(若非空)→ agent 短 id(`agent-<agent_id 前 8 位>`)兜底。
解析在服务端完成,接口统一返回单一 `display_name` 字段;`member_type` 与类型徽章独立返回供前端渲染。

### 2.5 索引与约束

```sql
-- 名册主查询:按工作区列成员(带状态过滤)
CREATE INDEX idx_members_workspace ON members(workspace_id, status);
CREATE INDEX idx_members_user ON members(user_id);
CREATE INDEX idx_members_agent ON members(agent_id);
CREATE INDEX idx_members_type ON members(workspace_id, member_type);

-- 检索投影 trigram 索引(MES-76 H3,DDL 与同步契约权威见 search-command-palette.md §2.2)
CREATE INDEX idx_members_search_name_trgm ON members USING gin (search_name gin_trgm_ops);
CREATE INDEX idx_members_search_name_prefix ON members (workspace_id, search_name text_pattern_ops) WHERE status <> 'removed';  -- R2-H3:1–2 字符前缀路径
CREATE INDEX idx_members_ws_type_active ON members (workspace_id, member_type) WHERE status <> 'removed';

-- 供引用方复合 FK(README §6.2):issues.assignee_id 等据此同租户引用 members
CREATE UNIQUE INDEX uq_members_ws_id ON members(workspace_id, id);

-- 多态唯一:同一 user / agent 在同一工作区仅一条
CREATE UNIQUE INDEX uq_members_ws_user ON members(workspace_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_members_ws_agent ON members(workspace_id, agent_id) WHERE agent_id IS NOT NULL;

CREATE INDEX idx_member_access_member ON member_project_access(member_id);
CREATE UNIQUE INDEX uq_member_access ON member_project_access(member_id, project_id);
```

### 2.6 与其他模块的外键关系

| 来源(引用方) | 外键 | 目标 | 说明 |
|----------------|------|------|------|
| `issues.assignee_id` / `reporter_id` | 复合 FK `(workspace_id, …) → members(workspace_id, id)` | 统一名册 | 分派/上报,人或 agent 对称(README §6.2) |
| `comments.author_id` | 复合 FK `(workspace_id, author_id) → members(workspace_id, id)` | 统一名册 | 发言者 |
| `comment_mentions.mentioned_id` | 复合 FK `(workspace_id, mentioned_id) → members(workspace_id, id)` | 统一名册 | @提及目标(comment-inbox.md owns) |
| `notifications.recipient_id` | 复合 FK `(workspace_id, recipient_id) → members(workspace_id, id)` | 统一名册 | 通知收件(comment-inbox.md owns) |
| `api_tokens.owner_member_id` | 复合 FK `(workspace_id, owner_member_id) → members(workspace_id, id)` | 统一名册 | 令牌持有者(auth.md owns) |
| `workspace_invitations.invited_by` | 复合 FK `(workspace_id, invited_by) → members(workspace_id, id)` | 统一名册 | 邀请人(workspace.md owns) |
| `members.workspace_id` | → `workspaces.id` | workspace.md | |
| `members.user_id` | → `users.id` | auth.md | 人类身份(关联方向 members → users,`users` 不设反向列) |
| `members.agent_id` | → `agents.id`(复合 FK `(workspace_id, agent_id) → agents(workspace_id, id)`,仅可加入同工作区名册) | agent.md | AI 身份(README §6.2) |

> **同租户复合 FK(README §6.2)**:一切引用方均**同时存 `workspace_id` 并建复合 FK** `(workspace_id, <ref>_id) → members(workspace_id, id)`(本表 §2.5 建有 `UNIQUE(workspace_id, id)` 供引用),使跨工作区引用在 INSERT 时即被拒绝(集成测试 T1)。
>
> 引用方对 `members.id` 的删除策略按 **README §6.2 第 6 条**统一:需要"删除时置空引用"的复合 FK 一律采用 PG16 列级写法 **`ON DELETE SET NULL (<引用列>)`**(仅置空引用列,不连带 `workspace_id`);对"不可悬空"的引用(如留痕作者)采用**软删除 + `ON DELETE RESTRICT`**。移除成员通过 `status='removed'` 软处理而非物理删除,故 RESTRICT 不阻塞正常移除;真实 DELETE 行为(而非仅建表成功)必须有集成测试覆盖(README §9 T18)。

---

## 3. 接口设计

REST 基础路径 `/api/v1`,Bearer token 鉴权(见 auth.md),游标分页,统一错误信封。

### 3.1 REST 端点清单

| 方法 | 路径 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | `/workspaces/{ws}/members` | 列出名册(人+agent;可按 `member_type`、`status`、`role` 过滤,`q` 搜索) | 成员 |
| GET | `/workspaces/{ws}/members/{id}` | 获取单个成员(含 user/agent 详情) | 成员 |
| POST | `/workspaces/{ws}/members` | 把已有 user/agent 加入名册;**人类成员入册同事务播种 onboarding 清单(R3,onboarding.md §3.5:入册播种为主路径,含历史事实全量 reconcile;agent 成员不播种)** | admin |
| PATCH | `/workspaces/{ws}/members/{id}` | 改角色 / 状态(启用、停用)/ 显示名 | admin(改自己资料除外) |
| DELETE | `/workspaces/{ws}/members/{id}` | 移除成员(可选 `?reassign_to=<member_id>`) | admin |
| POST | `/workspaces/{ws}/members/reassign` | 批量转派某成员未完成 issue 给另一成员 | admin |
| GET | `/users/me` | 当前登录用户及其在各工作区的成员身份 | 已登录 |
| PATCH | `/users/me` | 更新自己的资料(头像、昵称、时区) | 已登录 |
| GET | `/workspaces/{ws}/agents/available` | 列出可加入名册的 agent(详见 agent.md) | admin |
| GET | `/members/{id}/presence` | 成员在线/运行态(可选) | 成员 |
| GET | `/workspaces/{ws}/members/{id}/project-access` | 列出 guest 的项目级可见性 | admin |
| POST | `/workspaces/{ws}/members/{id}/project-access` | 为 guest 授予/变更项目共享(read/write) | admin |
| DELETE | `/workspaces/{ws}/members/{id}/project-access/{project_id}` | 撤销某项目的 guest 共享 | admin |

**guest 项目共享** `POST /api/v1/workspaces/{ws}/members/{id}/project-access`
```json
// Request:把某项目以 read 共享给 guest 成员
{ "project_id": "prj-7", "permission": "read" }
// 201 Response:返回 member_project_access 条目
{ "id": "mpa-1", "member_id": "mem-d4", "project_id": "prj-7", "permission": "read" }
```
> 项目共享仅对 `role='guest'` 的成员有意义;对 owner/admin/member 设置返回 422(其可见性由角色决定)。guest 对被共享项目内 issue 的可见性规则见 issue.md。

### 3.2 请求/响应 JSON 示例

**列出成员** `GET /api/v1/workspaces/{ws}/members?member_type=all&limit=50`
```json
{
  "data": [
    {
      "id": "mem-a1",
      "member_type": "human",
      "role": "owner",
      "status": "active",
      "display_name": "Jane Doe",
      "joined_at": "2026-01-10T08:00:00Z",
      "profile": { "id": "usr-1", "display_name": "Jane Doe", "email": "jane@acme.com",
                   "avatar_url": "https://cdn.example/jane.png" }
    },
    {
      "id": "mem-b2",
      "member_type": "agent",
      "role": "member",
      "status": "active",
      "display_name": "代码助手",
      "joined_at": "2026-02-01T08:00:00Z",
      "profile": { "id": "agt-9", "name": "代码助手", "description": "擅长代码修复与测试生成",
                   "avatar_url": "https://cdn.example/bot.png" }
    }
  ],
  "next_cursor": null
}
```

**改角色 / 停用** `PATCH /api/v1/workspaces/{ws}/members/{id}`
```json
{ "role": "admin" }        // 或
{ "status": "disabled" }   // 或
{ "display_override": "小李" }
// 200 Response:返回更新后的成员对象
```

> **名册行锁协议(MES-76 R4-H3)**:改角色 / 改状态(停用)/ 移除事务更新本 `members` 行即持有该行排他锁——与设备码消费事务对同一行的 `FOR UPDATE`(auth.md §3.1.1 consume 锁序)在同名册行上**线性化**:两者并发时按锁获取顺序定结果(消费先持锁则会话签发完成后变更再生效;变更先提交则消费按变更后状态拒绝/收窄签发),不存在 TOCTOU 间隙。显示名变更(`display_override`)另触发 `search_name` 同事务重算(§2.2 同步契约,search-command-palette.md §2.2)。

**移除并转派** `DELETE /api/v1/workspaces/{ws}/members/{id}?reassign_to=mem-c3`
```json
// 200 Response
{ "removed": true, "reassigned_issues": 20 }
```

**加入 agent 到名册** `POST /api/v1/workspaces/{ws}/members`
```json
// Request
{ "member_type": "agent", "agent_id": "agt-9", "role": "member" }
// 201 Response:返回新名册条目(member_type=agent, role=member)
```
> **agent 必须同工作区**:加入名册的 agent 须满足 `agents.workspace_id = ws`(agent.md 定义 `agents.workspace_id`),由 `members.agent_id` 的**复合 FK** `(workspace_id, agent_id) → agents(workspace_id, id)` 在数据库层强制(README §6.2);把别工作区的 agent 加入本区名册会被拒绝(集成测试 T1)。**跨工作区共享 agent 不在本期范围(YAGNI)**。

**获取单个成员** `GET /api/v1/workspaces/{ws}/members/{id}`
```json
{
  "id": "mem-b2",
  "member_type": "agent",
  "role": "member",
  "status": "active",
  "display_name": "代码助手",
  "display_override": null,
  "joined_at": "2026-02-01T08:00:00Z",
  "disabled_at": null,
  "profile": { "id": "agt-9", "name": "代码助手", "description": "擅长代码修复与测试生成",
               "avatar_url": "https://cdn.example/bot.png", "is_active": true },
  "counts": { "open_issues_assigned": 3 }
}
```

**批量转派** `POST /api/v1/workspaces/{ws}/members/reassign`
```json
// Request:把 from_member 名下未完成 issue 转给 to_member
{ "from_member_id": "mem-a1", "to_member_id": "mem-c3",
  "statuses": ["todo", "in_progress", "in_review"] }
// 200 Response
{ "reassigned_issues": 20 }
```
> 转派目标必须是同工作区 `status='active'` 的成员(人或 agent 均可),否则 422 `reassign_target_invalid`。转派为逐条写操作,写 issue 审计并触发 `issue.updated` 事件;若目标是 agent,转派等同分派,触发其接管。

### 3.3 错误码表

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 非法角色/状态值 |
| 401 | `unauthorized` | token 缺失/失效 |
| 403 | `forbidden` | 非 admin 改他人角色;非 admin 移除成员 |
| 404 | `not_found` | 成员不存在或不可见 |
| 409 | `last_owner` | 试图移除/降级/停用最后一个 active owner(工作区须恒有 ≥1 个 `role='owner' AND status='active'` 成员;校验在行锁下串行化,并发安全) |
| 409 | `agent_owner_not_allowed` | 试图把 agent 设为 owner |
| 409 | `already_member` | 该 user/agent 已在名册 |
| 422 | `reassign_target_invalid` | 转派目标不是有效活跃成员 |
| 429 | `rate_limited` | 触发限流 |

### 3.4 分页 / 鉴权 / 限流

- **分页**:名册按 `joined_at, id` 排序游标分页,`?limit=&cursor=` → `{"data":[...],"next_cursor"}`。
- **鉴权**:读取名册需为该工作区成员;改角色/状态/移除/加入需 `admin` 及以上;**最后一个 owner 保护**与 **agent 不可为 owner** 在服务端强校验(不依赖前端禁用)。改自己的资料/显示名任何成员可操作。
- **限流**:写端点按 principal 限流(见 auth.md)。

### 3.5 WebSocket 实时事件

> **统一实时契约见 README §6.7**(本 Spec 不重复定义):`seq` **一律为频道内单调递增**(持久化于 `realtime_events`,无"全局 seq");断线重连带 `resume_from=<last_seq+1>`,游标过旧收 `resync_required` + REST 对账水位;订阅 `workspace:{ws}:members` 频道时**重新做资源级授权**。

订阅频道 `workspace:{ws}:members`。事件命名 `<entity>.<action>`,携带频道内单调递增 `seq`,断线凭 `resume_from` 重放(README §6.7)。

| 事件 | 触发时机 | payload 关键字段 |
|------|----------|------------------|
| `member.added` | 人或 agent 入册 | `member_id`, `member_type`, `role` |
| `member.updated` | 角色/状态/资料变更 | `member_id`, `changes` |
| `member.removed` | 成员被移除 | `member_id` |
| `member.presence` | 在线/运行态变化(可选) | `member_id`, `presence` |

> agent 的运行态变化(开始/完成某 issue)通过 issue 事件广播(见 issue.md),成员页据此实时反映"该 agent 正在处理 X"。

---

## 4. UI/UX 设计

### 4.1 信息架构与页面布局

```
成员页(/members)
   ├── [筛选: 全部 | 人类 | AI agent | 已停用]   [搜索框]   [+ 邀请/添加]
   └── 名册表格
        列: 头像+名称(+类型徽章 人/agent) | 邮箱/简介 | 角色(下拉) | 状态 | 加入时间 | 操作(…)
```

### 4.2 关键组件

- **名册表格**:人类与 agent 同表;agent 行带"AI"徽章与机器人头像样式,悬停展示能力简介。
- **角色下拉**:行内可改(owner/admin/member/guest);agent 行的 `owner` 选项禁用并置灰(后端同样强校验)。
- **成员详情抽屉**:点击成员打开侧栏,展示资料、其名下进行中 issue、最近活动;agent 详情额外展示运行时状态与配置入口(链接到 agent.md)。
- **添加成员弹窗**:两个 Tab——"邀请人类"(邮箱,衔接 workspace.md 邀请)与"添加 AI agent"(从可用 agent 列表挑选)。
- **停用/移除确认**:二次确认弹窗,提示"是否把其名下未完成 issue 转派给…",并提供转派目标选择器。
- **assignee 选择器**(issue/看板复用):人与 agent 混合列出,各带类型图标;选中 agent 即触发其接管。

### 4.3 关键交互流程

**把 AI agent 加入团队**:成员页 → "+ 添加" → "AI agent" Tab → 从可用列表选择 → 设定角色(默认 member,owner 禁用)→ 确认 → agent 出现在名册,从此可被分派、可被 @。

**分派给 agent**:issue 详情或看板卡片点 assignee → 选择器中人/agent 混列(带类型图标)→ 选中 agent → 保存 → 触发 agent 接管该 issue。

**停用离职成员**:成员页 → 行操作 → 停用 → 弹窗询问"是否转派其 20 个进行中 issue"→ 选接手人 → 确认 → 该成员变灰、issue 已转派、历史评论保留。

### 4.4 状态流转(成员)

```
active ──停用──► disabled ──启用──► active
active ──移除──► removed(解除成员关系,历史保留)
disabled ──移除──► removed
```
> `removed` 为软终态:保留历史 issue/评论的 `members.id` 引用,名册默认列表不再展示。物理清理由后台保留期策略负责。

角色流转:任意角色可由 admin 调整,但受 `last_owner` 与 `agent_owner_not_allowed` 约束。

### 4.5 实时性与通知

- **实时**:WebSocket 订阅 `workspace:{ws}:members`(§3.5);降级轮询。
- **通知触发点**:
  - 被加入工作区/被赋予角色:站内通知。
  - 被 @ 提及:人类收站内 + 邮件;agent 收"提及事件"并触发运行(见 agent.md)。
  - 被分派 issue:人类收通知;agent 收分派事件触发接管。
  - 成员被停用/移除:管理员侧记录审计;被移除者(若为人类)收到通知。

---

## 5. 验收标准

### 5.1 功能性

- [ ] 名册条目通过 `member_type` + 多态外键指向 user 或 agent,CHECK 约束保证 user_id/agent_id 恰好一个非空。
- [ ] 同一 user / agent 在同一工作区仅一条名册(部分唯一索引),重复加入返回 409 `already_member`。
- [ ] 把 agent 设为 `owner` 被拒,返回 409 `agent_owner_not_allowed`(前端禁用 + 后端强校验)。
- [ ] 降级/移除/停用最后一个 active owner 被拒,返回 409 `last_owner`;校验先锁定 active owner 行再计数(FOR UPDATE,id 升序),并发竞态中恰有一个操作被拒,任何时刻 ≥1 个 active owner。移除/降级已停用的 owner 不削减 active 计数,不受此限。
- [ ] `GET /members` 同时返回人类与 agent,可按 `member_type`、`status`、`role` 过滤,`q` 模糊搜索。
- [ ] **`q` 搜索通配符转义**:`q` 为字面子串匹配,搜索词中的 `%` / `_` / `\` 经共享 `escape_like` 工具转义后 `ILIKE ... ESCAPE '\'`,`q=%` 不扩大匹配集(不命中全名册);与 issue 列表搜索(issue.md §3.2)同语义同实现。
- [ ] issue.assignee、评论 author、@提及、通知均可统一引用 `members.id`,且对人与 agent 表现一致。
- [ ] **成员模型权威(README §6.1)**:`users`/`agents` 表**不含 `member_id` 反向列**;关联方向恒为 `members.user_id → users.id` / `members.agent_id → agents.id`;同一 user 可在多个工作区各有一条 `members` 行。
- [ ] **同租户复合 FK(README §6.2 / §9 T1)**:`members` 建有 `UNIQUE(workspace_id, id)`;引用方(issues.assignee_id/reporter_id、comments.author_id、comment_mentions.mentioned_id、notifications.recipient_id、api_tokens.owner_member_id 等)均以复合 FK `(workspace_id, <ref>_id) → members(workspace_id, id)` 引用;构造跨工作区引用(把 A 区成员设为 B 区 issue 负责人)被数据库约束拒绝。
- [ ] 把别工作区的 agent 加入本区名册被复合 FK `(workspace_id, agent_id) → agents(workspace_id, id)` 拒绝(README §6.2 / §9 T1)。
- [ ] 停用成员后其无法操作,但历史评论/issue 保留;启用后恢复。
- [ ] 移除成员时 `?reassign_to=` 把其未完成 issue 批量转派,返回转派数量;转派目标非法返回 422。
- [ ] guest 仅可见 `member_project_access` 中共享的项目/issue,其它返回 404。
- [ ] guest 项目共享端点仅对 `role='guest'` 成员生效,对其它角色返回 422;`permission` 限 read/write。
- [ ] 撤销 guest 某项目共享后,该成员立即不可见该项目及其 issue。
- [ ] 所有 UI/API 的显示名遵循统一解析顺序(display_override → user/agent 名称),返回单一 `display_name` 字段。
- [ ] `PATCH /users/me` 允许任何成员改自己资料;改他人需 admin。
- [ ] `display_override` 仅在当前工作区生效,不影响全局 `users` 资料。

### 5.2 性能

- [ ] 万级名册列表 P95 < 200ms(命中 `idx_members_workspace`)。
- [ ] assignee 选择器搜索(人+agent 混列)P95 < 150ms。
- [ ] 多态唯一校验走部分唯一索引,无全表扫描。

### 5.3 安全

- [ ] 改角色/状态/移除/加入端点强制 `admin` 校验,不足返回 403。
- [ ] `last_owner` 与 `agent_owner_not_allowed` 在服务端强校验,绕过前端亦被拒。`last_owner` 覆盖降级/移除/停用三条削减 active owner 的路径,且 gate 判定与计数均基于锁后状态(目标行 + active owner 行同一条 FOR UPDATE 语句按 id 升序加锁并刷新),并发提升/削减交错下不可绕过,TOCTOU 安全。
- [ ] 角色变更、移除、停用、转派均写 auth.md 的 append-only 审计日志(行为者以 `actor_member_id` 落 member 行,人/agent 经 JOIN `members.member_type` 判别;`actor_kind∈('member','system')`,无 `actor_type` 列,见 auth.md §2.6)。
- [ ] 移除/停用受 auth.md 限流约束。
- [ ] guest 的项目级可见性在服务端逐资源校验,不依赖前端隐藏。
- [ ] **用户可控 URL scheme 校验**:`avatar_url` 等用户可控 URL 字段在服务端校验 scheme,禁止 `javascript:`/`data:` 等非安全 scheme,**仅允许 `https`**(README §6.16 R2 统一 https-only,明文 `http` 的用户可控头像 URL 是混合内容弱攻击面);`members`/`users`/`agents` 相关端点写入时统一校验。

### 5.4 实时

- [ ] 入册/角色变更/状态变更/移除分别触发 `member.added`/`member.updated`/`member.removed`,在线成员 1s 内收到。
- [ ] @提及人类触发站内+邮件;@提及 agent 触发运行事件(衔接 agent.md)。
- [ ] 分派给 agent 触发其接管事件。
- [ ] 客户端断线重连凭 `resume_from` 重放、游标过旧收 `resync_required`(README §6.7),名册状态最终一致,无丢失无重复。
