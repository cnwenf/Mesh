# 全局搜索 / 命令面板 / 键盘快捷键体系 功能 Spec

> **所属层**:平台能力层(横切导航与效率层;README §6.12 设计系统与体验基线的详 Spec)。
> **依赖的其他 Spec**:
> - `auth.md`(§3 RBAC 权限矩阵):搜索结果的逐资源可见性判定、角色可见性矩阵。
> - `member.md`(统一名册 `members`,`member_type ∈ human|agent`):成员与 agent 两类搜索对象同源表。
> - `issue.md`(§3 端点 / identifier 体系 / 项目可见性):issue 搜索对象、identifier 精确命中、私有项目可见性。
> - `kanban.md`(视图 `views` 投影):视图搜索对象与视图深链。
> - `project.md`(项目可见性 `visibility`):项目搜索对象与私有项目过滤。
> - `chat-session.md`(会话与参与者):聊天会话搜索对象(仅参与者可见)。
> - `agent.md`(agent 容量呈现「运行中 N / 排队 M / 需审批 K」):agent 结果 badge 快照。
> - `theme.md`(语义 token):面板/帮助层一切颜色经语义 token。
> - `i18n.md`(消息目录):面板/帮助层一切可见文案经消息目录外部化。
> **被依赖方**:所有模块的核心资源均以本 Spec 的**规范深链**(§3.4)为唯一外链入口;通知(§6.13)、邮件摘要、IM 卡片中的资源链接一律指向规范深链。
>
> **全局一致性锚点(canonical anchor)**:本 Spec 是 [README.md](../README.md) §6.12「全局搜索 / 命令面板」与「键盘快捷键体系」两段的**详 Spec**。§6.12 已就**命令面板触发与搜索对象全集**、**规范深链清单**、**power-user 快捷键表**、**`?` 帮助层**、**输入框获焦豁免**、**等价鼠标路径**、**上下文分组**作出唯一权威契约;本 Spec 仅**展开其实现细节**(服务端搜索端点、结果形状、前端路由与深链落地、快捷键注册模型、排序与交互、验收),**不复述、不改写契约原文**——凡与 §6.12 冲突,一律以 README 为准。相关契约锚点:API 包络/错误/分页/过滤限制(§6.14)、多租户同租户约束(§6.2)、实时订阅授权与私有项目不越权(§6.7)、收藏 favorites(§6.19)、异常态矩阵(§6.12)、国际化(§6.18)。

---

## 1. 功能描述

### 1.1 模块定位

本模块是 Mesh 的**横切导航与效率层**,不新增核心业务实体,而是把既有资源(issue / 成员 / agent / 项目 / 视图 / 聊天会话)通过统一检索入口与键盘体系加速访问:

- **命令面板**(`Ctrl/Cmd+K`):跨模块搜索 + 命令执行的单一入口,键盘优先,结果即深链(§6.12)。
- **全局搜索端点**:服务端按权限可见性过滤的统一检索,支撑命令面板与顶栏搜索(§6.12 / §6.14)。
- **规范深链**:一切核心资源有唯一规范 URL,搜索结果、通知、IM 卡片统一指向(§6.12)。
- **power-user 快捷键体系**:全局/看板/issue 详情/聊天四组,`?` 帮助层随上下文实时反映,一切快捷键有等价鼠标路径(§6.12)。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| S1 | 命令面板触发 | `Ctrl/Cmd+K` 任意页面打开;`/` 聚焦顶栏搜索(回车或继续输入展开面板);顶栏搜索按钮为等价鼠标入口 | 随时呼出,不碰鼠标 |
| S2 | 跨模块对象搜索 | 六类对象:issue(identifier/标题)、成员、agent、项目、视图、聊天会话(§6.12 全集) | 输入「登录」命中相关 issue/会话 |
| S3 | 命令/动作条目 | 导航命令(各顶层入口)、新建 issue(等价 `C`)、主题切换(复用 theme.md 命令)、打开帮助层、复制当前深链、收藏/取消收藏(§6.19)、标记全部已读 | 不离开键盘完成动作 |
| S4 | 分组与上下文 | 结果按类型分组;命令集随上下文(全局/看板/issue/聊天)动态增减;组头标注 | 看板页多出「新建卡片」「改状态」命令 |
| S5 | 模糊匹配与排序 | 分层打分(§4.6),identifier 精确命中顶置;命中字符高亮(不以颜色为唯一信号) | 打 `saf cri` 命中「Safari 崩溃」 |
| S6 | 最近使用与收藏区 | 空 query 展示 recents(前端本地)+ favorites(`GET /api/v1/favorites`,§6.19)+ 常用命令 | 打开面板即有可点项 |
| S7 | 键盘导航 | ↑/↓ 移动、Enter 执行、Esc 关闭、Tab 补全选中文本到输入框;ARIA combobox/listbox | 纯键盘操作 |
| S8 | 规范深链跳转 | 选中结果 Enter 直达 §3.4 规范深链;一切资源外链统一规范深链 | 搜索结果即入口 |
| S9 | 全局快捷键组 | `mod+K` 面板 / `/` 搜索 / `C` 新建 issue / `?` 帮助层 / `G then I\|B\|M\|A` 跳转(§6.12 写死) | 肌肉记忆导航 |
| S10 | 看板上下文组 | `C` 当前列新建卡片;`S` 改状态;`A` 改 assignee;`Enter` 打开选中卡片;`F` 筛选 | 看板页纯键盘流转 |
| S11 | issue 详情上下文组 | `E` 编辑;`S` 改状态;`A` 改 assignee;`P` 改优先级;`Ctrl/Cmd+Enter` 提交评论;`Esc` 关闭 | 详情页快速处理 |
| S12 | 聊天上下文组 | `Enter` 发送 / `Shift+Enter` 换行;`Ctrl/Cmd+↑` 编辑上一条;`Esc` 退出输入焦点 | 对话不离手 |
| S13 | 序列键 | `G` 首键进入等待态(状态条提示),超时窗口 1000ms,超时/Esc 取消 | `G I` 跳收件箱 |
| S14 | `?` 帮助层 | 按上下文分组列出**当前可用**全部快捷键,平台键自适应(`⌘` vs `Ctrl`) | 随时查键位 |
| S15 | 输入框豁免 | 焦点在输入控件时单字符快捷键一律不触发;仅放行显式 `Ctrl/Cmd` 组合与表单语义键(Esc/Tab/Enter)(§6.12) | 打字不误触 |
| S16 | 等价鼠标路径 | 一切快捷键操作均有菜单/按钮可达路径;快捷键是加速不是唯一入口(§6.12) | 鼠标用户无损 |

### 1.3 边界与非目标(明确不做什么)

- **不做独立全文检索引擎**:起步 PostgreSQL(`pg_trgm` + GIN / 等值索引)满足标题 + identifier 检索;description/评论正文全文检索列**可选增强**(YAGNI,待真实需求)。
- **不做 OCR / 附件内容搜索**:附件仅按文件名命中(attachment.md)。
- **不做自定义快捷键编辑器(rebind)**:本期固定映射 + `?` 帮助层;`user_keybindings` 表列后续规划。
- **不做跨设备 recents 同步**:recents 纯前端 localStorage(按 user 隔离);`recent_items` 表列可选增强,本期不建。
- **不做搜索分析/个性化排序模型**:仅 recency/frequency 封顶加权;不落服务端搜索日志(隐私,§5.3)。
- **不新增实时事件/频道**:面板只读快照渲染,不扩 §6.7 事件词汇注册表。
- **不**新增角色/权限模型(沿用 auth.md RBAC);**不**自定义 API 包络(§6.14)。

---

## 2. 数据模型与配置

> **全局契约引用**:API 包络/错误/分页一律以 [README.md](../README.md) §6.14 为权威;多租户隔离以 §6.2 为权威;收藏以 §6.19 为权威。本 Spec 仅引用、不重复定义。
>
> **不新增表(本期)**:命令注册表、快捷键定义、分组配置均为**前端代码常量**(随版本发布),不进数据库;recents 为前端本地存储。服务端新增检索索引,并在 `members` 上增加**受控同步的 `search_name` 检索投影列**(§2.2,member.md §2.2 已登记;非新表、非显示真源)——MES-76 H3 收口:显示名跨表解析不可直接建索引。

### 2.1 前端常量与本地存储

| 数据 | 载体 | 说明 |
|------|------|------|
| 命令注册表 | 前端常量(zustand registry) | `{ id, title, shortcut?, group, when(上下文谓词), keywords, handler }`;同 id 注册即替换,返回注销函数(既有实现延续) |
| 快捷键定义 | 前端常量 | `{ id, combo, group, when, handler }`;combo 归一化(`mod`=mac `⌘`/其余 `Ctrl`;`space`/`esc` 别名);**同 combo 冲突按 §4.3 确定性仲裁(最具体 active context > global),同优先级冲突为编程错误(开发态报错 + CI 失败)** |
| 上下文集合 | 前端运行态 | `activeContexts: Set<'global'\|'board'\|'issue'\|'chat'>`;路由/页面挂载时 `setContexts`,卸载复位;**context 特异性序写死:`issue > board > global`,`chat` 页面独占(与 board/issue 不叠加)** |
| recents | localStorage(**键按 host + user + workspace 三元组隔离**) | 最近访问对象 + 最近执行命令,上限 20 条,LRU 淘汰;**不进服务端**(键形如 `mesh.recents:{host}:{user_id}:{workspace_id}`,评审 M3 收口:防跨工作区/跨账号串用) |

### 2.2 服务端检索索引策略(可执行 DDL,评审 H3 收口)

**数据模型事实**:member/agent 的「显示名」是 `members.display_override → users.display_name → users.email`(人类)/`agents.name`(agent)的**跨表解析**(README §6.1),PostgreSQL **不能对跨表表达式建普通索引**——直接对「members 显示名」建 GIN 不可行。本 Spec 采用**受控同步的搜索投影**方案:在 `members` 上维护 `search_name` 投影列(与 README §6.1 显示名链同算法),对投影建 trigram 索引;显示渲染仍用实时解析链,投影**仅供检索**。

```sql
-- 0. 扩展(随迁移启用,幂等)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1. member/agent:受控同步的搜索投影(评审 H3 主方案)
ALTER TABLE members ADD COLUMN IF NOT EXISTS search_name TEXT NOT NULL DEFAULT '';
COMMENT ON COLUMN members.search_name IS
  '检索专用投影,与 README §6.1 显示名解析链同算法(display_override → users.display_name → users.email / agents.name),小写归一;仅用于 trigram 检索,不用于显示渲染。同步契约见 search-command-palette.md §2.2';

-- 投影索引:trigram + 租户/类型/状态支撑索引
CREATE INDEX idx_members_search_name_trgm ON members USING gin (search_name gin_trgm_ops);
CREATE INDEX idx_members_ws_type_active ON members (workspace_id, member_type)
  WHERE status <> 'removed';

-- 2. issue:identifier 等值快路径(已有唯一索引)+ title trigram + 租户/软删组合
CREATE INDEX idx_issues_title_trgm ON issues USING gin (title gin_trgm_ops);
CREATE INDEX idx_issues_ws_not_deleted ON issues (workspace_id, project_id)
  WHERE deleted_at IS NULL;   -- 与 title GIN 做 BitmapAnd:租户 + 软删谓词下推

-- 3. project:name trigram(私有项目可见性谓词在查询内,§3.3)
CREATE INDEX idx_projects_name_trgm ON projects USING gin (name gin_trgm_ops);

-- 4. view:name trigram(修订:此前仅 B-tree,不支持 Spec 承诺的模糊匹配)
CREATE INDEX idx_views_name_trgm ON views USING gin (name gin_trgm_ops);

-- 5. chat_session:title trigram(参与者谓词在查询内,§3.3)
CREATE INDEX idx_chat_sessions_title_trgm ON chat_sessions USING gin (title gin_trgm_ops);
```

**`members.search_name` 同步契约(写死,防漂移)**:

| 环节 | 规则 |
|------|------|
| 写入路径(同事务) | ① member 入册(人类/agent):按显示名链解析后写 `search_name = lower(resolved)`;② `members.display_override` 变更:重算;③ `users.display_name`/`users.email` 变更(改名):同事务重算该 user 的**全部** members 行(跨工作区);④ `agents.name` 变更:同事务重算该 agent 的全部 members 行。服务层写路径统一经 `sync_member_search_name(member_id)` 单一函数 |
| 回填迁移 | 上线迁移一次性全量回填(`UPDATE members m SET search_name = lower(...) FROM users/agents ...`),分批提交,迁移完成前搜索降级为 `ILIKE` 兜底(不阻塞发布) |
| 周期对账 | 低频 reconcile 任务(每日)全量比对投影与实时解析链,不一致即修复并告警(漂移可观测) |
| 一致性验收 | 集成测试:改名(users.display_name / agents.name / display_override)后搜索**立即**命中新名、旧名不再命中;跨工作区同一 user 的两条 members 行同步更新 |
| 与 README §6.1 的关系 | `search_name` 是 README §6.1「若个别高频表确需存储快照,必须强制一致并明示」条款下的**受控搜索投影**:非显示真源(显示一律实时解析链)、同步由单一函数 + 对账兜底、在本 Spec 明示 |

**三条查询路径(按输入形态分流,写死)**:

| 输入形态 | 路径 | 候选上限 |
|----------|------|----------|
| 1–2 字符 | trigram 在 <3 字符不可用:仅走**前缀等值**(`search_name LIKE 'ab%'` / `identifier LIKE 'AB%'` / `name LIKE 'ab%'`,经 B-tree `text_pattern_ops` 或顺序扫描小结果集)+ 本地命令匹配;对象类结果**仅前缀命中**,不做模糊 | 每类 ≤5 |
| 完整 identifier(归一后匹配 `^[A-Za-z][A-Za-z0-9]*-\d+$`) | **identifier 等值快路径**(`UNIQUE(workspace_id, identifier)`),跳过 150ms 防抖,命中即顶置 | 1(顶置)+ 常规路径补齐 |
| ≥3 字符 | trigram 相似度(`%` 运算符 / `similarity()` 排序)+ §4.6 分层打分;可见性 JOIN 在查询内(§3.3) | 每类 ≤20,合并后 ≤ `limit×2` |

- 一切查询 SQL 携带 `workspace_id` 复合前缀谓词 + RLS 纵深防御(§6.2);
- **可见性过滤一律在查询内 JOIN/WHERE 完成**(§3.3),EXPLAIN 证明无「先取后筛」;
- `statement_timeout`(默认 3s)+ 估算成本兜底,超限 `422 query_cost_exceeded`(§6.14);
- **性能验收(§5.2 / README §10)**:§10 基准(10 万 issue / 1 万成员工作区,50 VU 稳态 + 100 VU 峰值,冷/热缓存各标注)下搜索 P95 < 300ms、identifier 快路径 P95 < 100ms;附三条路径各自的 `EXPLAIN (ANALYZE, BUFFERS)`,证明命中上述 trigram/唯一/部分索引、无全表顺序扫描(可见性 JOIN 走真实成员/项目表)。

### 2.3 `recent_items`(可选增强,本期不建)

跨设备同步最近项时才需要,模型见 [调研记录 §2.1](../../research/search-command-palette.md);本期 recents 纯前端,不建表、不开端点。

---

## 3. 接口设计

> REST 基础路径 `/api/v1`;鉴权 `Authorization: Bearer <token>`(auth.md)。**成功包络、错误信封、HTTP 语义、过滤限制一律以 README §6.14 为权威**;时间一律 RFC3339 UTC。

### 3.1 端点清单

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| GET | `/api/v1/workspaces/{ws}/search?q=&types=&limit=&cursor=` | 全局搜索(对象类结果;命令条目由前端本地合并) | 工作区成员 |
| GET | `/api/v1/favorites`(§6.19 既有) | 面板空态收藏区数据源(**空 query 的唯一服务端数据源**,§4.2) | 本人 |

> **workspace scope 唯一来源(评审 H4 收口,写死)**:搜索的工作区作用域**只从路径 `{ws}`(UUID 或 slug)解析**,与 issue/project 等集合端点(`/workspaces/{ws}/issues` 等,issue.md §3.1)同构——**不接受** query/header/token 派生的第二来源(agent token 自带 workspace 时仍须在路径指名,指名工作区与 token 工作区不一致 → `403 forbidden`);路径缺失即无搜索端点可调用,不存在「默认工作区」隐式解析。
>
> 服务端**只返回对象类结果**;命令注册表是前端常量,命令命中由前端本地过滤后与服务端结果在客户端合并分组。本模块**不新增写端点、不新增实时频道**。

### 3.2 请求/响应 JSON 示例

**搜索** `GET /api/v1/workspaces/{ws}/search?q=登录&types=issue,member,agent,project,view,chat_session&limit=20`

| 参数 | 类型 | 说明 |
|------|------|------|
| `q` | string | 查询词(≤120 字符);**空/缺省 → `200 {"data": [], "next_cursor": null}`(服务端不返对象结果)**;面板空态的 favorites/recents/常用命令由前端按 §4.2 唯一数据流组装(favorites 经 `GET /api/v1/favorites`,recents/命令纯本地)——**不存在服务端「favorites + 最近项默认集」第二数据流**(评审 H4 收口:此前与「recents 仅本地、favorites 单独端点」矛盾) |
| `types` | csv | 对象类型白名单子集;缺省=全部六类;非法值 `400 validation_error` |
| `limit` | int | 默认 20,上限 50 |
| `cursor` | opaque | 整体游标(§6.14):**`cursor = base64(完整排序元组 + 绑定指纹 + 服务端 HMAC 签名)`**——完整排序元组与 §4.6 平局裁决链**逐因子一一对应**:`(score_bucket, context_rank, recency_rank, frequency_rank, title_len, title_lex, type, id)`(score 以固定精度量化为整数 bucket,杜绝浮点不可复现);绑定指纹 = `sha256(q \| 排序后的 types 集合 \| workspace_id)`,**cursor 与产生它的 query/types/workspace 绑定,跨任一参数复用 → `400 validation_error`**(防攻击者搬运 cursor 探测他查询/他工作区的 keyset 路径);签名不符 → `400 validation_error`;内部字段经签名校验前**一律不信任**(MES-75 安全收口延续) |

```json
// 200 Response
{
  "data": [
    {
      "type": "issue",
      "id": "0c2f…",
      "title": "登录页在 Safari 崩溃",
      "context": {
        "identifier": "WEB-124",
        "project": { "id": "p-1", "name": "官网改版" },
        "status": { "id": "s-3", "name": "In Progress", "category": "in_progress" }
      },
      "icon": "issue",
      "url": "/w/acme/issues/by-identifier/WEB-124",
      "badge": { "kind": "status", "label_key": "issue.status.name", "label_params": { "name": "In Progress" }, "color": "info" },
      "highlight": { "title": { "unit": "codepoint", "ranges": [[0, 2]] } }
    },
    {
      "type": "agent",
      "id": "mem_b2",
      "title": "代码助手",
      "context": {
        "member_type": "agent",
        "role": "member",
        "capacity": { "running": 2, "queued": 1, "awaiting_approval": 0 }
      },
      "icon": "agent",
      "url": "/w/acme/agents/mem_b2",
      "badge": { "kind": "member_type", "label_key": "member.type.agent", "label_params": {}, "color": "info" }
    }
  ],
  "next_cursor": null
}
```

**结果条目统一形状**(前端按此渲染;**服务端只返回稳定 key + 结构化数据,不返回拼接好的可见句子**,§6.18——本地化 subtitle 由前端用消息目录组装,如 `search.subtitle.issue = "{identifier} · {project} · {status}"`):

| 字段 | 必备 | 说明 |
|------|------|------|
| `type` | ✓ | `issue` / `member` / `agent` / `project` / `view` / `chat_session` |
| `id` | ✓ | UUID(成员类为 `members.id`) |
| `title` | ✓ | 主标题**原文**(issue 标题 / 成员 display_name / 项目名 / 视图名 / 会话标题;未归一化,供渲染与 highlight 映射) |
| `context` | ✓ | **按类型的结构化上下文对象**(取代此前的拼接字符串 `subtitle`),前端据此组装本地化副标题:`issue` → `{identifier, project:{id,name}|null, status:{id,name,category}}`;`member`/`agent` → `{member_type, role, presence?, capacity?{running,queued,awaiting_approval}(agent 快照,§6.12 容量呈现)}`;`project` → `{visibility, key}`;`view` → `{scope:"project"\|"workspace", project?:{id,name}, owner_only?}`;`chat_session` → `{participants_count, agent?:{id,name}}`;所有枚举走稳定 key(status category / visibility / role),展示文案经消息目录 |
| `icon` | ✓ | 类型图标键(语义键,前端映射图标) |
| `url` | ✓ | 规范深链(§3.4),Enter 直达 |
| `badge` | – | `{kind, label_key, label_params, color}`:徽章文案经**消息目录 key + 参数**(如 status 徽章 `label_key="issue.status.name"`,params 携带 status 原名);`color` 仅取语义 token 名(status/danger/warn/success/info) |
| `highlight` | – | 命中区间 `{title: {"unit": "codepoint", "ranges": [[start,end], …]}}`:**offset 单位写死为原始 `title` 字符串的 Unicode code point**(半开区间 `[start,end)`;匹配命中标注在**原文**上计算——NFKD/去重音仅用于候选召回与排序,**不作用于 highlight 映射**,杜绝归一化后偏移无法映回原文;前端经 `Array.from(title)` 映射到渲染偏移);**只返回 offset 区间,不返回 HTML** |

> 排序:服务端按 §4.6 全序输出,**全序各因子与 cursor 内部排序元组逐一对应**(§4.6 平局裁决链即 keyset 排序键);`next_cursor=null` 表示末页(§6.14)。`score` 原始浮点值**不返回**(不稳定、仅供调试,调试构建经显式开关输出,避免泄漏排序内部权重——MES-75 L5 延续)。

### 3.3 权限过滤语义(§6.2 / §6.7 对齐,硬约束)

- **过滤下推到查询内**——不得「先取后筛」(否则分页计数/游标失真,并经计数泄漏受限资源存在性)。
- 逐资源授权(与 §6.7 订阅授权同源):

| 对象 | 可见性谓词 |
|------|-----------|
| issue | 工作区成员资格 + 所属项目可见性(私有项目仅其成员)+ issue 级可见性 |
| project | 项目可见性(私有项目仅成员) |
| member / agent | 工作区名册可见;**`visibility='private'` 的 agent 仅其 owner 与 admin 可见**(agent.md「private 仅所有者与 admin 可见」同源;名册角色矩阵按 auth.md;guest/agent 受限按 §6.12) |
| view | 私有视图仅 owner;共享视图按工作区/项目成员;**归属项目的视图与项目可见性取 AND**(项目不可见则该视图即使共享亦不可见) |
| chat_session | 会话参与者 |

- **私有项目不暴露存在性**:命中私有项目内 issue 时,非成员**不进结果、不进计数、不进默认集**——与「私有项目事件只进 `project:{id}` 频道,不广播后前端过滤」同原则(§6.7)。
- **私有 agent 不暴露存在性**:`visibility='private'` 的 agent 对非 owner/非 admin **不进结果、不进默认集**(与私有项目同「不暴露存在性」原则;私有 agent 往往携带机密指令配置,名称/存在性本身即敏感)。
- 集成测试必须覆盖跨租户、跨私有项目、跨私有 agent、跨会话的**负向用例**(§5.3)。

### 3.4 规范深链(§6.12 权威清单的前端路由落地)

README §6.12 定义的规范深链是**一切资源外链的唯一形态**;前端实现对应的 workspace-scoped 路由。**清单对全部可搜索/可通知资源闭合(评审 H5 收口:补齐 member、view)**:

| 资源 | 规范深链 | 落地说明 |
|------|----------|----------|
| issue(按编号) | `/w/{workspace_slug}/issues/by-identifier/{KEY-N}` | 后端 `by-identifier` 读端点已存在(issue.md §3);前端路由解析后加载详情 |
| 项目 | `/w/{ws}/projects/{id}` | |
| **成员(人/agent 名册条目)** | `/w/{ws}/members/{member_id}` | 成员详情页(人类资料 / agent 配置入口同页按 `member_type` 分区;§6.12 Agent 入口去重——agent 详情即 `member_type='agent'` 的成员详情) |
| agent | `/w/{ws}/agents/{id}` | `/w/{ws}/members/{member_id}` 的**别名**(agent_id → 其 member 行解析后渲染同一成员详情页);外链生成统一优先 member 规范链 |
| **视图(看板/列表投影)** | `/w/{ws}/views/{view_id}` | 视图深链(归属项目的视图进入即应用其投影;私有视图非 owner 访问 → §6.12 permission denied 异常态) |
| 执行 | `/w/{ws}/executions/{id}` | 运行详情 |
| 聊天会话 | `/w/{ws}/chat/{session_id}` | |
| 审批 | `/w/{ws}/approvals` | 统一「待我审批」入口(§6.10/§6.13) |

**与既有扁平路由的迁移契约(评审 H5 收口,逐条映射 + 执行层写死)**:当前前端为 SPA 扁平路由(`/inbox`、`/board`、`/members`…),本身不含 workspace——规范深链落地后:

1. 八类资源详情/入口一律以 `/w/{ws}/…` 规范路由渲染;搜索结果、通知、邮件、IM 卡片中的链接**只生成规范深链**;
2. **旧→新逐条映射**:

   | 旧扁平路由 | 规范路由 | 说明 |
   |----------|----------|------|
   | `/inbox` | `/w/{ws}/inbox` | 收件箱 |
   | `/board` | `/w/{ws}/board`(默认视图;有当前视图上下文时 `/w/{ws}/views/{view_id}`) | 看板 |
   | `/members` | `/w/{ws}/members` | 成员名册(含「仅 Agent」筛选投影) |
   | `/projects` · `/projects/{id}` | `/w/{ws}/projects` · `/w/{ws}/projects/{id}` | 项目列表/详情 |
   | `/issues/{id}` | `/w/{ws}/issues/by-identifier/{KEY-N}`(解析后) | issue 详情 |
   | `/chat` · `/chat/{session_id}` | `/w/{ws}/chat` · `/w/{ws}/chat/{session_id}` | 聊天 |
   | `/settings/*` | `/w/{ws}/settings/*` | 工作区设置(admin+) |
   | `/automations/*` | `/w/{ws}/automations/*` | 自动化运营区 |

   **一律保留原 query 与 hash**(如 `/board?view=x#card-1` → `/w/{ws}/board?view=x#card-1`);
3. **active workspace 来源(写死,按序解析)**:① 当前 URL 已在 `/w/{ws}/…` 内 → 取 URL 中的 workspace;② 否则取**最近活跃工作区**(登录后经 `GET /workspaces` + 本地持久化 `mesh.last_workspace:{host}:{user}`,服务端 `users.last_active_workspace_id` 回填);③ 所属恰一个工作区 → 直接采用;④ **无上下文且多工作区 → 工作区选择页**(`/workspace-picker`,列出所属工作区,选定后跳规范路由并记忆);
4. **302 执行层(写死)**:扁平路由是 **SPA 客户端路由**(服务端对应用路径统一返回 index.html,无 HTTP 层路由可言),故旧→新重定向在**前端应用层**执行——以 **`replaceState` 替换(不新增历史栈条目,等价 302 语义)** 至规范路由;**HTTP 302/301 仅发生在服务端可渲染入口**:邀请链接/邮件/IM 卡片一律直接生成规范深链(不经扁平路由),过期 slug 经 workspace.md `workspace_slug_history` 的 **301** 契约(服务端或前端解析 slug 时同一重定向源);
5. **SEO**:认证内页面统一 `<meta name="robots" content="noindex">` + `<link rel="canonical">` 指向规范深链——**不为 SEO 牺牲路由一致性**(不存在为爬虫保留扁平路由的分支);
6. **测试矩阵(逐场景)**:① 旧书签 `/board` 直接刷新 → 落 active workspace 的 `/w/{ws}/board`(多工作区用户无上下文 → 选择页);② 过期 slug 深链 → 301 至新 slug,query/hash 保留;③ 通知/邮件链接(规范深链)直达正确页面;④ 多工作区用户 A→B 切换后旧扁平路由解析到 B(最近活跃);⑤ 无权限视图深链 → permission denied 异常态而非白屏。

### 3.5 错误码表(模块专属)

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | `types` 含非法值 / `limit` 超限 / `q` 超 120 字符(README §6.14 canonical) |
| 401 | `unauthorized` | 凭证缺失/无效(§6.14 canonical) |
| 403 | `forbidden` | 非工作区成员(§6.14 canonical) |
| 422 | `query_cost_exceeded` | 估算查询成本超限,建议收窄 `types` 或加长 `q`(§6.14 canonical) |
| 429 | `rate_limited` | 搜索触发限流,带 `Retry-After`(§6.14 canonical) |

> 公共 HTTP 语义与错误信封不重复定义(README §6.14)。搜索无结果返回 `200 {"data": [], "next_cursor": null}`,**不是错误**。

---

## 4. UI/UX 设计

### 4.1 命令面板布局

```
┌───────────────────────────────────────────────┐
│  ⌕ [ 输入查询…                          ] Esc │  ← 打开即聚焦
├───────────────────────────────────────────────┤
│  RECENT / 收藏(空 query)                      │
│  ISSUES                                        │  ← 分组组头(i18n 键)
│  ▸ WEB-124 登录页在 Safari 崩溃   [In Progress]│  ← 选中行
│  MEMBERS                                       │
│  ▸ 代码助手                       [agent]      │
│  COMMANDS                                      │
│    新建 issue                            C     │  ← 右侧快捷键提示
├───────────────────────────────────────────────┤
│  ↑↓ 导航 · Enter 打开 · Tab 补全 · ? 快捷键    │  ← 底部提示条
└───────────────────────────────────────────────┘
```

- 居中浮层(~640px),限高内滚动,选中行始终可视;复用既有 Dialog 与语义 token(theme.md);
- 命中字符高亮以**字重/下划线**叠加,不以颜色为唯一信号(§6.12);
- 命令条目右对齐显示快捷键(平台自适应);组头随上下文动态增减。

### 4.2.1 空态组装唯一数据流(评审 M3 收口,写死)

空 query 面板由**单一组装规则**渲染(服务端搜索对空 q 只返回空集,§3.2):

1. **数据源**:favorites ← `GET /api/v1/favorites`(§6.19,**唯一服务端来源**,目标失效者该端点已不返回);recents ← 本地 `mesh.recents:{host}:{user}:{workspace}`;常用命令 ← 本地命令使用计数;
2. **排序与去重**:favorites 区(按收藏时间倒序)→ recents 区(按访问时间倒序)→ 常用命令区(按使用频次倒序);**recents 中已出现在 favorites 区的条目去重**(同一 target 不重复展示);命令按 id 去重;
3. **本地 recent 失效清理(惰性)**:渲染空态时对 recents 条目做**轻量批量存在性/可见性核验**(批量 id 查询,失败/403/404 即过期),命中失效即从本地存储剔除;另订阅 `favorites.changed`(§6.7 已登记)与路由切换时的删除事件联动清理;**被删/失权对象不得残留在 recents**;
4. **recents 隔离**:键按 host + user + workspace 三元组隔离(§2.1),登出不清理 recents(非敏感、按 user 隔离即可),切换工作区自然换键。

### 4.2 异常态矩阵(§6.12 核心页面异常态在本模块的实例)

| 状态 | 呈现 |
|------|------|
| loading | 顶部细进度条;本地命令命中**零延迟先渲染**,对象结果异步补入(**补入按稳定 id 保持当前选择,§4.3.1**) |
| empty(空 query) | Recents(本地)+ favorites(`GET /api/v1/favorites`,§6.19 唯一服务端数据源)+ 常用命令,**唯一数据流组装**(§4.2.1) |
| no results | 「未找到与 "xxx" 匹配的结果」+ 建议(检查拼写/减少关键词)+「新建 issue "xxx"」快捷动作(**仅对当前工作区有 `issue:write` 权限者展示;点击预填创建弹窗标题,不直接提交**) |
| permission denied | 越权对象不返回、不暴露存在性(§3.3) |
| error / retry | 内联错误行 + 「重试」按钮;toast 兜底 |
| offline | 仅本地命令可用,对象区提示「网络已断开,显示本地命令」 |

### 4.3 快捷键全集与上下文分组

**全局组**(任意页面):

| combo | 命令 | 等价鼠标路径 |
|-------|------|--------------|
| `mod+K` | 打开命令面板(输入框内亦生效) | 顶栏搜索按钮 |
| `/` | 聚焦顶栏搜索 | 点击搜索框 |
| `C` | 新建 issue | 「+ 新建」按钮 |
| `?`(`Shift+/`) | 打开帮助层 | 设置/页脚「快捷键」入口 |
| `G then I` | 跳收件箱 | 侧栏「收件箱」 |
| `G then B` | 跳看板 | 侧栏「看板」 |
| `G then M` | 跳成员 | 侧栏「成员」 |
| `G then A` | 跳自动化 | 侧栏「自动化」 |

**看板组**(看板页挂载时激活):`C` 当前列新建卡片(**复用全局新建弹窗并预填当前选中列,§4.3.1 仲裁**;无可用列回退全局新建)· `S` 改选中卡片状态 · `A` 改 assignee · `Enter` 打开选中卡片 · `F` 打开筛选。
**issue 详情组**(详情挂载时激活):`E` 编辑 · `S` 状态 · `A` assignee · `P` 优先级 · `mod+Enter` 提交评论 · `Esc` 关闭。
**聊天组**(会话页激活):`Enter` 发送 / `Shift+Enter` 换行 · `mod+↑` 编辑上一条 · `Esc` 退出输入焦点。

> 上下文组命令同时注册进命令面板(§4.4 帮助层一致);上下文切换时 `setContexts` 更新,帮助层与面板实时反映。**一切快捷键均有等价鼠标路径**(§6.12);每个上下文组的具体等价路径随各模块 UI 落地(kanban.md / issue.md / chat-session.md),本 Spec 给出基线,各模块验收时核对。

### 4.3.1 快捷键冲突确定性仲裁(评审 H6 收口,写死)

同一 combo 被多个已注册 handler 匹配时,**每次按键只执行一个 handler**,仲裁规则如下(确定性、可测试):

1. **优先级 = 最具体 active context > global**:特异性序 `issue > board > global`(`chat` 页面独占,其激活时不叠加 board/issue);按键到达时,在 `combo` 命中且 `when` 谓词为真的 handler 集合中取**特异性最高者执行**,其余被屏蔽;
2. **同优先级冲突 = 编程错误**:同一 active context 内两个 handler 声明相同 combo(如 board 组内两条 `C`)——**开发态抛错 + CI 注册表静态断言失败**(测试枚举全部注册快捷键,按 active context 组合检查 combo 唯一性),不静默「先注册者胜」;
3. **看板 `C` 与全局 `C` 的关系**:看板上下文激活时 `C` 由看板 handler 执行(特异性更高)——**复用与全局新建相同的创建 issue 弹窗,并预填当前选中列(状态)**;仅当看板**无可用列**(如空视图/无状态可写)时回退执行全局新建(空弹窗);两者是同一弹窗组件的两种预填形态,不是两个弹窗;
4. **异步结果补入保持当前选择**:对象搜索结果异步到达并插入列表时,**按稳定 `id` 维持当前选中项**——已选中的条目不因新结果插入上位而移位;用户按下 Enter 时以**按键瞬间选中项的 id** 为目标(捕获于 keydown),补入竞态不得把用户将要 Enter 的条目替换;
5. **裸键抑制面**:IME composition 期间(`isComposing`/`compositionstart…end`)**一切裸键快捷键不触发**(中文输入 `c` 不得弹新建);命令面板、帮助层、任意 modal/overlay 打开期间,**底层页面的裸键快捷键一律屏蔽**(仅该浮层自身的键绑定生效 + `Esc` 语义);
6. **`?` 帮助层展示仲裁后的有效键位**:同一 combo 多上下文注册时,帮助层按当前 `activeContexts` 只展示**仲裁胜出的那一条**(如 board 激活时 `C` 展示为「当前列新建卡片」,不再并列两条 `C`);离开 board 上下文后实时切回全局语义。

### 4.4 `?` 帮助层

- 模态浮层,按 `GROUP_ORDER [global, board, issue, chat]` × `activeContexts` 过滤分组,仅列**当前可用**项;**同 combo 仅展示 §4.3.1 仲裁胜出的有效键位**(不并列两条 `C`);
- 键位平台化渲染(mac `⌘`/`⌥` vs Win/Linux `Ctrl`/`Alt`);序列键拆多键帽(`G` `I`);
- `Esc`/`?` 关闭;焦点陷落与归还遵循 §6.12 焦点管理。

### 4.5 输入框豁免与序列键

- 焦点在 `input`/`textarea`/`select`/`contenteditable` 时:**单字符快捷键一律不触发**;仅放行显式 `mod` 组合(`mod+K`、`mod+Enter`)与表单语义键(`Esc`/`Tab`/`Enter`)(§6.12);
- **IME composition 期间**(`isComposing` 或 `compositionstart…compositionend` 之间):**一切裸键与序列键首键一律不触发**(候选词输入阶段的按键不是快捷键意图,§4.3.1);
- **modal/overlay 打开期间**(命令面板、帮助层、创建弹窗等):底层页面裸键快捷键**全屏蔽**,仅浮层自身键绑定与 `Esc` 生效(§4.3.1);
- 序列键:`G` 按下进入等待态,状态条提示 `G —`;**超时窗口 1000ms**(既有 `SEQUENCE_WINDOW_MS` 延续),超时或 `Esc` 清缓冲;第二键到达即执行。

### 4.6 模糊匹配排序(分层打分)

匹配强度阶梯(强 → 弱),文本相关性主导,个性化仅封顶加权:

```
精确匹配 > 前缀 > 词首(token prefix) > 首字母缩略(acronym)
> 词边界/驼峰/路径分隔 > 连续子串 > 子序列模糊 > 副标题/关键词兜底
```

- 归一化:匹配前小写 + 去重音(NFKD),**展示保留原文**(highlight 在原文上计算,§3.2);分词覆盖空格/`-`/`_`/`/`/`.`/驼峰边界;
- **identifier 精确命中直接顶置**(等值快路径);
- recency / frequency / 上下文可用性各**封顶**小幅加分,不得压过文本相关性;
- **全序 = keyset 排序键(评审 H4 收口,与 §3.2 cursor 内部元组逐因子一一对应)**:`score_bucket DESC`(文本相关性主分,固定精度量化为整数,杜绝浮点不可复现)→ `context_rank`(当前上下文可用性封顶加分)→ `recency_rank`(最近使用封顶)→ `frequency_rank`(频率封顶)→ `title_len ASC`(标题更短优先)→ `title_lex ASC`(标题字典序)→ `type ASC` → `id ASC`(终极确定性)。**不存在「原始顺序」之类不可复现因子**——cursor 携带全序完整元组 + 绑定指纹(§3.2),翻页严格单调推进,跨页无重复无遗漏;
- 排序权重以**黄金集**(query→期望 top 结果测试集)的 Top-3 命中率/MRR 校准,不凭感觉。

### 4.7 防抖、焦点与无障碍

- 本地命令过滤同步执行(零延迟先渲染);服务端检索**防抖 150ms** + 过期请求取消;完整 identifier 命中跳过防抖;
- ARIA combobox + listbox:`role=combobox` + `aria-expanded`/`aria-controls`/`aria-activedescendant`;结果项 `role=option` + `aria-selected`;结果变化 `aria-live=polite` 播报;
- 打开陷落焦点、关闭归还触发元素;全键盘可达、焦点可见(§6.12);
- **尊重 `prefers-reduced-motion`**:浮层动画/高亮脉冲降级为即时/静态。

### 4.8 实时性

- 本模块为**请求-响应检索**,不新增实时频道;结果 badge(issue 状态、agent 容量「运行中 N / 排队 M」)为**服务端快照**,真源在对应模块,面板不订阅不保证实时收敛;
- **不扩 §6.7 事件词汇注册表**:搜索/快捷键/深链均不产生 realtime 事件。

### 4.9 顶栏搜索

- 顶栏搜索框为**真实控件**(替换当前占位):`value`/`onChange`/`onSubmit` 接通;`/` 聚焦;输入即展开命令面板同一结果视图(同一组件、同一数据源),保证键鼠一致;
- 文案经 i18n 消息目录(`search.placeholder` 等键)。

---

## 5. 验收标准

### 5.1 功能性

- [ ] **命令面板六类对象搜索**:输入关键词命中 issue(identifier/标题)、成员、agent、项目、视图、聊天会话,分组呈现;每类至少一条真实数据 e2e 命中校验。
- [ ] **workspace scope 与结果契约(评审 H4)**:搜索经 `/workspaces/{ws}/search` 唯一路径解析工作区(query/header 无第二来源;agent token 指名他区 → 403);响应**不含拼接好的可见句子**——`context` 为按类型结构化字段、`badge` 为消息目录 key + 参数(e2e 切换 locale 后副标题/徽章本地化正确,服务端响应体不变);`highlight` offset 以原始 title 的 Unicode code point 计(构造含多字节/组合字符标题的断言,前端高亮区间与命中词精确对齐)。
- [ ] **游标稳定性(评审 H4)**:同一 (q, types, workspace) 翻页经 cursor 无重复/无遗漏(10 万 issue 工作区遍历断言);cursor 换 q / 换 types / 换 workspace 复用 → `400 validation_error`;篡改 cursor 内部字段(HMAC 不符)→ `400`;空 `q` 服务端返回空 `data`(空态 favorites/recents/命令按 §4.2 唯一数据流本地组装,favorites 经 §6.19 端点)。
- [ ] **identifier 精确命中顶置**:输入完整 `KEY-N` 时该 issue 为第一结果,Enter 直达规范深链。
- [ ] **命令条目**:导航命令、新建 issue、主题切换、打开帮助层、复制深链、收藏/取消收藏均可经面板执行,且各有等价鼠标路径。
- [ ] **上下文分组生效**:看板/issue 详情/聊天页分别激活各自上下文组,帮助层与面板命令集实时反映;离开页面即复位(生产代码实际调用 `setContexts`,不再是死代码)。
- [ ] **快捷键全集与仲裁(评审 H6)**:全局组 8 条 + 各上下文组按 §4.3 落地;序列键窗口 1000ms,超时/Esc 取消有 UI 提示;平台键渲染 mac/非 mac 各验;**看板页按 `C` 只触发看板新建(复用全局弹窗 + 预填当前列),全局 `C` 被屏蔽不并发执行;看板无可用列回退全局新建**;CI 静态断言全部注册快捷键在任一 active context 组合下无同优先级 combo 冲突;**异步结果补入不移位当前选中项**(e2e:选中第 2 条时插入新结果,Enter 打开的仍是原选中对象);**IME 输入中文时按候选键不触发裸键快捷键;modal 打开时底层页面裸键全屏蔽**;`?` 帮助层同 combo 只展示仲裁后有效键位。
- [ ] **输入框豁免**:输入控件聚焦时单字符键不触发、`mod+K` 仍生效(回归用例)。
- [ ] **规范深链(八条闭合,评审 H5)**:八条规范深链(issue/项目/成员/agent 别名/视图/执行/聊天/审批)可直接访问并正确渲染;既有扁平路由按 §3.4 映射表在前端应用层 `replaceState` 至规范路由(query/hash 保留);多工作区无上下文 → 工作区选择页;slug 过期走 workspace.md 301 重定向;§3.4 测试矩阵五场景逐条 e2e;认证内页面 `noindex` + canonical。
- [ ] **异常态**:loading/empty/no-results/error/offline 五态按 §4.2 实现;no-results 提供「新建 issue "q"」动作——**仅有 `issue:write` 权限者可见(无权限者该动作不渲染),点击预填创建弹窗而非直接提交**(e2e 以 guest/无权限成员断言动作缺失)。
- [ ] **空态(评审 M3)**:空 query 按 §4.2.1 唯一数据流展示 favorites(§6.19 端点)+ recents(本地)+ 常用命令;**favorites 与 recents 同 target 去重、各区排序正确**;**被删/失权对象的本地 recent 打开面板即被清理不再出现**;切换工作区后 recents 键隔离(不串用他区记录)。
- [ ] **文案外部化**:面板/帮助层/错误提示无硬编码可见文案(i18n.md);颜色无硬编码(theme.md)。

### 5.2 性能(§10 基准)

- [ ] 搜索 P95 < 300ms(热缓存,10 万 issue / 1 万成员工作区,q 为常见短词);identifier 精确命中 P95 < 100ms。**基准按 README §10**:k6 50 VU 稳态 + 100 VU 峰值、冷/热缓存各标注;查询携带**真实可见性 JOIN**(成员资格/项目可见性/私有 agent 谓词,§3.3),不以去权限的简化查询充数(评审 H3 收口)。
- [ ] 命令面板本地命令过滤 < 16ms(单帧内);服务端结果防抖 150ms + 过期取消,无可感卡顿。
- [ ] **三条查询路径各有 `EXPLAIN (ANALYZE, BUFFERS)`**(§2.2):1–2 字符前缀路径、完整 identifier 快路径、≥3 字符 trigram 路径,在 10 万 issue / 1 万成员分布下证明命中 §2.2 的 trigram/唯一/部分索引组合(BitmapAnd/快路径),**无全表顺序扫描**;`members.search_name` 投影改名后即时可搜(同步契约验收)。

### 5.3 安全与一致性

- [ ] **跨租户负向**:A 租户搜索 q 不返回 B 租户任何对象(集成测试,RLS + 谓词双断言)。
- [ ] **私有项目不暴露存在性**:非私有项目成员搜索命中词时,该私有项目内 issue/视图**不进结果、不进计数、不进默认集**;归属项目的视图与项目可见性取 AND(§3.3);与 §6.7 同源原则核对。
- [ ] **私有 agent 不暴露存在性**:非 owner/非 admin 搜索**不返回** `visibility='private'` 的 agent(**不进结果、不进默认集**);owner 与 admin 可搜到(正向对照,§3.3 与 agent.md 同源)。
- [ ] **会话隔离**:非参与者搜索不返回他人聊天会话。
- [ ] **负向矩阵扩展**:空 `q` 组装空态(favorites 经 §6.19 端点 + 本地 recents,§4.2.1)同样不泄漏越权对象(私有项目/私有 agent/他人会话探测均为空);成员退出某私有项目后,其 issue/视图命中与相关收藏**即刻消失**,本地 recents 中的失效条目打开面板即清理(与 §6.19 收藏失效目标清理联动)。
- [ ] **不落搜索日志(全通道)**:`q` 原始值经**任何通道**(访问日志、错误上报、trace/span 属性、指标 label)**不得出请求处理器**——不采集或脱敏;服务端不持久化原始 query;集成测试断言上述各通道抓样无 query 明文。
- [ ] **注入防护**:`q` 参数化 + `LIKE` 通配符转义;`types` 白名单;`highlight` 只返回 offset/length,前端不回显 HTML;`cursor` = base64(内部组成 + 服务端 HMAC),**签名不符 → 400**,内部字段经签名校验前一律不信任(§3.2)。
- [ ] **限流**:搜索端点纳入限流中间件,429 带 `Retry-After`;错误 message 不泄漏 SQL/堆栈/内部 ID(§6.14)。
- [ ] **无暴露外部出处**:代码/注释/文案/测试不含任何竞品名称或外部出处信息。

### 5.4 可观测

- [ ] 搜索端点指标:QPS、P95/P99 延迟、空结果率、错误率(不记录 query 内容);
- [ ] 黄金集回归:query→期望 Top-3 测试集随 CI 跑,Top-3 命中率低于阈值即失败。
