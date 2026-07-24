# 成员(Member)调研记录

> 调研对象:主流团队协作 / 项目管理产品在【成员】模块上的通用设计模式(已匿名化,不指向任何具体产品)。
> 数据模型基准约定:PostgreSQL、UUID 主键、`created_at` / `updated_at`、REST + JSON、游标分页、Bearer token、WebSocket 实时。
> **Mesh 特色**:AI agent 作为一等成员,与人类同列于成员名册、同样可被分派 issue。本文件在业界通用的"人类成员角色"模型之上,补充"AI agent 作为一等成员"的统一名册设计。

---

## 1. 功能清单

### 1.1 人类成员角色

| 角色 | 权限范围 | 典型用户场景 |
|------|----------|--------------|
| `owner`(所有者) | 工作区最高权限:删除工作区、转让所有权、管理计费、所有 admin 权限 | 创始人持有 owner,负责工作区生死与计费 |
| `admin`(管理员) | 管理成员/邀请、配置状态/标签/字段、管理项目,但不可删除工作区 | 团队负责人管理成员与流程配置 |
| `member`(普通成员) | 创建/编辑/分派 issue、评论、修改状态,管理自己被分派的工作 | 工程师日常领任务、改状态、写评论 |
| `guest`(访客) | 只读或受限读写,通常仅限被显式共享的项目/issue | 外部客户查看某项目进度,不能看其它项目 |

**关键设计点(业界标准做法)**:
- 角色是 **工作区级** 的(存在成员关联表上),而非全局用户属性;同一用户在不同工作区可有不同角色。
- 权限校验采用"角色 → 能力"映射(可在服务端维护一张能力表),而非散落硬编码判断。
- `owner` 至少保留一名:转让或移除最后一个 owner 被禁止。
- guest 的可见性常用"按项目/issue 显式共享"实现(共享表),而非全工作区只读。

### 1.2 AI agent 作为一等成员(Mesh 特色)

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| agent 进入成员名册 | AI agent 与人类成员同列于"成员"页,拥有头像、名称、简介 | 团队把"代码助手 agent"加入工作区,在成员页一眼看到它 |
| agent 可被分派 | issue 的 assignee 既可以是人,也可以是 agent;统一通过"成员"引用 | 把"修复登录 bug"分派给代码 agent,它自动开始工作 |
| agent 拥有独立身份 | agent 以自己的身份发评论、改状态、被 @ 提及,操作可追溯到 agent | 看板上一张卡片显示"由 代码助手 处理中" |
| agent 角色 | agent 在名册中也有角色(常为受限的 `member` 级能力),不可担任 owner | agent 可改 issue、发评论,但不能删除工作区 |
| agent 配置 | agent 携带运行时绑定、技能、模型等配置(细节归 agent 模块) | 给 agent 绑定代码仓库与运行环境 |
| 人类↔agent 一致体验 | @提及、分派、收件箱通知对人类和 agent 用同一套交互 | 在评论里 @代码助手,触发它接管该任务 |

**统一名册的核心抽象**:把"可被分派、可发言的实体"统一为 **成员(member)**。成员关联表用类型判别器 + 多态外键,指向 `users`(人类)或 `agents`(AI)。issue.assignee、评论 author、@提及目标都引用统一的"成员"概念,从而让人与 agent 在分派、协作、通知上完全对称。详见 §2 数据模型。

### 1.3 资料 / 头像

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 个人资料 | 全名、头像、个人简介、时区、通知偏好 | 用户更新头像与个人简介 |
| 工作区内显示名 | 允许在工作区显示与全局不同的昵称(可选) | 用中文名显示 |
| 在线状态/忙碌(可选) | 展示活跃状态 | 看到同事在线 |
| agent 资料 | agent 有名称、头像、能力描述、版本 | 鼠标悬停 agent 头像看到"擅长:代码修复、测试生成" |

### 1.4 停用 / 启用

| 功能点 | 说明 | 典型用户场景 |
|--------|------|--------------|
| 停用成员 | 保留历史数据,但禁止登录/操作;其名下 issue 可选转派 | 员工离职,停用账号但保留其历史评论 |
| 启用成员 | 恢复访问 | 休假同事回归,重新启用 |
| 移除成员 | 从工作区移除(与停用区分:移除解除成员关系) | 项目结束,移除外包 |
| 停用 agent | 暂停 agent 接单/运行,历史保留 | 临时下线某 agent 进行配置调整 |
| 资产再分配 | 停用/移除时,把其名下未完成的 issue 批量转派给他人 | 离职者名下 20 个进行中 issue 转派给接手人 |

---

## 2. 数据模型

### 2.1 核心实体

#### `users`(人类登录身份)

| 字段 | 类型 | 约束 | 默认值 |
|------|------|------|--------|
| `id` | UUID | PK | `gen_random_uuid()` |
| `email` | CITEXT | NOT NULL, UNIQUE | — |
| `full_name` | TEXT | NOT NULL | — |
| `display_name` | TEXT | NULL | NULL |
| `avatar_url` | TEXT | NULL | NULL |
| `bio` | TEXT | NULL | NULL |
| `timezone` | TEXT | NOT NULL | `'UTC'` |
| `auth_hash` | TEXT | NOT NULL | — |
| `is_active` | BOOLEAN | NOT NULL | `true` | 全局账号是否可用
| `last_seen_at` | TIMESTAMPTZ | NULL | NULL |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |

#### `agents`(AI agent 身份)

> agent 的运行时/技能/模型等深度配置归 agent 模块;此处只列名册所需的核心字段。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | |
| `name` | TEXT | NOT NULL | — | agent 显示名 |
| `avatar_url` | TEXT | NULL | NULL | |
| `description` | TEXT | NULL | NULL | 能力简介 |
| `owner_user_id` | UUID | NULL, FK→users(id) | — | 创建/负责人 |
| `runtime_ref` | TEXT | NULL | NULL | 绑定的运行时标识(详见 agent 模块) |
| `config` | JSONB | NOT NULL | `'{}'` | 模型/技能等配置 |
| `is_active` | BOOLEAN | NOT NULL | `true` | 是否可被分派/运行 |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

#### `workspace_members`(统一成员名册)

> 这是"成员"的核心落地表:一个工作区的名册条目,要么指向一个 user,要么指向一个 agent。issue.assignee、评论 author、@提及统一引用本表的 `id`。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | UUID | PK | `gen_random_uuid()` | **成员 ID(统一引用键)** |
| `workspace_id` | UUID | NOT NULL, FK→workspaces(id) ON DELETE CASCADE | — | |
| `member_type` | TEXT | NOT NULL, CHECK IN ('user','agent') | `'user'` | 类型判别器 |
| `user_id` | UUID | NULL, FK→users(id) ON DELETE CASCADE | — | 人类成员 |
| `agent_id` | UUID | NULL, FK→agents(id) ON DELETE CASCADE | — | AI agent 成员 |
| `role` | TEXT | NOT NULL, CHECK IN ('owner','admin','member','guest') | `'member'` | agent 通常被限制为 `member` |
| `status` | TEXT | NOT NULL, CHECK IN ('active','disabled','removed') | `'active'` | |
| `display_override` | TEXT | NULL | NULL | 工作区内显示名覆盖 |
| `joined_at` | TIMESTAMPTZ | NULL | NULL | |
| `disabled_at` | TIMESTAMPTZ | NULL | NULL | |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | |

**约束**:
- `CHECK ((member_type='user' AND user_id IS NOT NULL AND agent_id IS NULL) OR (member_type='agent' AND agent_id IS NOT NULL AND user_id IS NULL))`
- `UNIQUE (workspace_id, user_id)`(WHERE user_id IS NOT NULL);`UNIQUE (workspace_id, agent_id)`(WHERE agent_id IS NOT NULL)
- agent 不允许 `role='owner'`:`CHECK (member_type='user' OR role <> 'owner')`

#### `member_project_access`(guest 的项目级可见性,可选)

| 字段 | 类型 | 约束 |
|------|------|------|
| `id` | UUID | PK |
| `member_id` | UUID | NOT NULL, FK→workspace_members(id) ON DELETE CASCADE |
| `project_id` | UUID | NOT NULL, FK→projects(id) ON DELETE CASCADE |
| `permission` | TEXT | NOT NULL, CHECK IN ('read','write') |
| UNIQUE | `(member_id, project_id)` |

### 2.2 实体关系(ER)

```
users ──1:N──┐
             ├──► workspace_members(统一名册) ◄──1:N── agents
             │          │
             │          ├── 被 issues.assignee_id / reporter_id 引用
             │          ├── 被 comments.author_id 引用
             │          └── 被 mentions / notifications 引用
             └──────────┘  (N:1 via workspace)
                       │
                  workspaces
```

**为什么用统一 member 而非多态 assignee**:若 issue 直接存 `assignee_id + assignee_type`,则每个引用点(issue、评论、提及、通知)都要重复携带 type 字段并各自处理两种外键。把"成员"物化成一张表(名册条目),所有引用都指向 `workspace_members.id`,引用方无需关心被引用者是人还是 agent —— 这正是"AI agent 作为一等成员"在数据层的关键支撑。

### 2.3 关键索引

```sql
CREATE INDEX idx_members_workspace ON workspace_members(workspace_id, status);
CREATE INDEX idx_members_user ON workspace_members(user_id);
CREATE INDEX idx_members_agent ON workspace_members(agent_id);
CREATE INDEX idx_members_type ON workspace_members(workspace_id, member_type);
CREATE UNIQUE INDEX uq_members_ws_user ON workspace_members(workspace_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_members_ws_agent ON workspace_members(workspace_id, agent_id) WHERE agent_id IS NOT NULL;
CREATE INDEX idx_member_access_member ON member_project_access(member_id);
```

---

## 3. 接口设计

REST 基础路径 `/api/v1`,Bearer token 鉴权,游标分页。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspaces/{ws}/members` | 列出成员名册(人 + agent;可按 `member_type`、`status` 过滤) |
| GET | `/workspaces/{ws}/members/{id}` | 获取单个成员(含其 user/agent 详情) |
| PATCH | `/workspaces/{ws}/members/{id}` | 改角色 / 状态(启用、停用)/ 显示名 |
| DELETE | `/workspaces/{ws}/members/{id}` | 移除成员(可选 `?reassign_to=<member_id>` 转派资产) |
| POST | `/workspaces/{ws}/members/reassign` | 批量把某成员的未完成 issue 转派给另一成员 |
| GET | `/users/me` | 当前登录用户及其在各工作区的成员身份 |
| PATCH | `/users/me` | 更新自己的资料(头像、昵称、时区) |
| GET | `/agents`(workspace 作用域) | 列出可加入名册的 agent(详见 agent 模块) |
| POST | `/workspaces/{ws}/members` | 把已有 user/agent 加入名册(管理员) |

### 3.2 请求/响应示例

**列出成员** `GET /api/v1/workspaces/{ws}/members?member_type=all&limit=50`
```json
{
  "data": [
    {
      "id": "mem_a1",
      "member_type": "user",
      "role": "owner",
      "status": "active",
      "profile": { "id": "usr_1", "full_name": "Jane Doe", "email": "jane@acme.com", "avatar_url": "..." }
    },
    {
      "id": "mem_b2",
      "member_type": "agent",
      "role": "member",
      "status": "active",
      "profile": { "id": "agt_9", "name": "代码助手", "description": "擅长代码修复与测试生成", "avatar_url": "..." }
    }
  ],
  "next_cursor": null
}
```

**改角色 / 停用** `PATCH /api/v1/workspaces/{ws}/members/{id}`
```json
{ "role": "admin" }              // 或
{ "status": "disabled" }

// 200 Response:返回更新后的成员对象
```

**移除并转派** `DELETE /api/v1/workspaces/{ws}/members/{id}?reassign_to=mem_c3`
```json
// 200 Response
{ "removed": true, "reassigned_issues": 20 }
```

### 3.3 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 非法角色/状态值 |
| 403 | `forbidden` | 非 admin 改他人角色;改自己的 owner 角色 |
| 404 | `not_found` | 成员不存在 |
| 409 | `last_owner` | 试图移除/降级最后一个 owner |
| 409 | `agent_owner_not_allowed` | 试图把 agent 设为 owner |
| 409 | `already_member` | 重复加入 |
| 422 | `reassign_target_invalid` | 转派目标不是有效活跃成员 |

### 3.4 分页与鉴权

- 游标分页:成员名册按 `joined_at, id` 排序,`?limit=&cursor=`。
- 鉴权:读取名册需为该工作区成员;改角色/状态/移除需 `admin` 及以上;涉及最后一个 owner 的保护在服务端强校验。

---

## 4. UI 设计

### 4.1 信息架构

```
成员页(/members)
   ├── [筛选: 全部 | 人类 | AI agent | 已停用]   [搜索框]   [+ 邀请/添加]
   └── 名册表格
        列: 头像+名称(+类型徽章 人/agent) | 邮箱/简介 | 角色(下拉) | 状态 | 加入时间 | 操作(…)
```

### 4.2 关键组件

- **名册表格**:人类与 agent 同表;agent 行带"AI"徽章与机器人头像样式,悬停展示能力简介。
- **角色下拉**:行内可改(owner/admin/member/guest),agent 行的 owner 选项禁用并置灰。
- **成员详情抽屉**:点击成员打开侧栏,展示资料、其名下进行中的 issue、最近活动;agent 详情额外展示运行时状态与配置入口。
- **添加成员**:弹窗,两个 Tab——"邀请人类"(邮箱)与"添加 AI agent"(从可用 agent 列表挑选)。
- **停用/移除确认**:二次确认弹窗,提示"是否把其名下未完成 issue 转派给…",并提供转派目标选择器。

---

## 5. UX 设计

### 5.1 关键交互流程

**把 AI agent 加入团队**:成员页 → "+ 添加" → "AI agent" Tab → 从可用列表选择 → 设定角色(默认 member)→ 确认 → agent 出现在名册,从此可被分派、可被 @。

**分派给 agent**:在 issue 详情或看板卡片上点 assignee → 选择器中人与 agent 混合列出(各带类型图标)→ 选中 agent → 保存 → agent 被触发接管该 issue。

**停用离职成员**:成员页 → 行操作 → 停用 → 弹窗询问"是否转派其 20 个进行中 issue"→ 选接手人 → 确认 → 该成员变灰、issue 已转派、历史评论保留。

### 5.2 状态流转(成员)

```
active ──停用──► disabled ──启用──► active
active ──移除──► removed(解除成员关系,历史保留)
disabled ──移除──► removed
```

### 5.3 实时性方案

- WebSocket 订阅 `workspace:{ws}:members` 频道。
- 事件:`member.added`、`member.updated`(角色/状态/资料)、`member.removed`、`member.presence`(在线状态,可选)。
- agent 的运行态变化(开始/完成某 issue)通过 issue 事件广播,成员页可实时反映"该 agent 正在处理 X"。

### 5.4 通知触发点

- 被加入工作区/被赋予角色:站内通知。
- 被 @ 提及:人类收站内+邮件;agent 收"提及事件"并触发运行。
- 被分派 issue:人类收通知;agent 收分派事件触发接管。
- 成员被停用/移除:管理员侧记录;被移除者收到通知(若为人类)。
