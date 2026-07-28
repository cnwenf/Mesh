# 开发者平台 CLI(`mesh` 命令行工具)调研记录

> 调研对象:主流开发者平台命令行工具在【鉴权 / 配置 / 资源命令 / 流式日志 / 导入导出 / 终端体验】上的**通用设计模式**(已匿名化,不指向任何具体产品;仅记录中性设计模式与业界标准协议,如 OAuth 设备授权 RFC 8628、XDG 目录、NO_COLOR、sysexits)。
> 模块簇:平台能力层 / 开发者平台(README §11)。
> 溯源基线:README §11.1/§11.2、§6.14(API/错误/分页)、§6.5(幂等键)、§6.16(凭证脱敏)、§6.8(流式协议);auth.md(api_tokens / 会话 / 撤销)、runtime.md(daemon 注册·心跳·日志流)、import-export.md(data-jobs 端点)。
> `[需 auth.md 增量]` 表示现有 auth.md 尚无对应端点、需新增。

---

## 0. 关键结论

1. **CLI 本身不引入服务端新表**:它是 REST API 的瘦客户端,服务端唯一依赖是 auth.md 已有的 `api_tokens`(PAT)。本地仅两份文件:`config.yaml`(可入库/可分享,不含密)+ `credentials`(0600,仅密)。
2. **设备码 OAuth 是 auth.md 的增量**:README §11.1 声明 `mesh auth login` 支持「OAuth 设备码或 PAT」,但 auth.md §3.1 目前只定义了**授权码 + PKCE**(面向浏览器第三方登录)与 PAT 端点,**没有设备码端点**。需在 auth.md 增 `POST /api/v1/auth/device/code` 与 `POST /api/v1/auth/device/token`(RFC 8628 形态,复用会话 access/refresh 体系)。PAT 路径则零增量,直接复用 §3.2。
3. **`mesh runtime register/heartbeat` 与 runtime.md 的 daemon 协议存在命名/形态错位需在 Spec 收口**:runtime.md 的机器接口是独立二进制 `mesh-runtime` + 命名空间 `/api/v1/daemon/*`(activate/heartbeat/claim),README §11.1 则把它写成 `mesh` 的 `runtime register/heartbeat` 子命令。Spec 需明确:`mesh runtime register` = 控制台侧建影子记录(POST /runtimes,返回一次性激活码),`mesh runtime heartbeat` 仅用于**人工排障/演示**,真实守护进程心跳走 `mesh-runtime`(daemon 命名空间),二者不混用鉴权域。

---

## 1. 功能清单(穷举,标注 必备 / 可选增强)

### 1.1 鉴权(auth)

| # | 功能点 | 等级 | 说明 / 通用做法 |
|---|--------|------|-----------------|
| C1 | PAT 登录(`mesh auth login --with-token` / 交互式粘贴) | **必备** | 复用 auth.md §3.2 创建的 `mesh_pat_*`。token 经 **stdin / 受限文件**读入,**绝不作命令行参数**(`--token <x>` 会落 shell 历史与进程表);通用做法是 `echo $TOKEN \| mesh auth login --with-token` 或 `mesh auth login` 后隐藏回显粘贴。校验 = 一次 `GET /api/v1/me` 探活 + 解析归属工作区。 |
| C2 | OAuth 设备码登录(`mesh auth login` 默认) | **必备** `[需 auth.md 增量]` | 见 §3.2 流程。RFC 8628 通用步骤:① CLI → 设备授权端点取 `device_code`/`user_code`/`verification_uri(_complete)`/`expires_in`/`interval`;② 终端打印「浏览器打开 X,输入码 Y」并**尝试自动唤起浏览器**(失败则仅打印);③ 按 `interval`(默认 5s)轮询令牌端点;④ `authorization_pending` 继续、`slow_down` 间隔 +5s、`access_denied` 终止、`expired_token` 重新发起;⑤ 成功后落地 access(+refresh 或长效 PAT)。CLI 属**公共客户端**,无 client secret。 |
| C3 | `mesh auth status` | **必备** | 展示:已登录主体(用户/agent)、当前默认工作区、令牌类型(PAT / 设备码会话)、scope、过期时间、`last_used_at`、API 基址;**不回显令牌明文**(仅 prefix 掩码,如 `mesh_pat_Ab3…****`)。退出码:已认证 0 / 未认证或令牌失效 2。 |
| C4 | `mesh auth logout` | **必备** | 撤销语义二选一并在 Spec 写死:① 设备码会话登录 → 调 `POST /auth/logout` 撤销 refresh(auth.md §3.1),并清本地凭证;② PAT 登录 → **默认仅清本地凭证**(令牌在服务端仍有效,供其它机器使用),`--revoke` 才调 `DELETE /workspaces/{ws}/api-tokens/{id}` 服务端吊销。清本地后任何命令退出码 2。 |
| C5 | 令牌刷新与过期处理 | **必备** | 设备码会话走 auth.md 短期 access JWT + 可撤销 refresh:CLI 收到 401 时用 refresh 静默调 `POST /auth/refresh` 换新 access 并重写本地凭证(轮换 refresh),对用户透明;refresh 也失效 → 退出码 2 + 提示 `mesh auth login`。PAT 无刷新(长效),过期(`expires_at`)即 401 → 退出码 2。**绝不把过期误报为通用错误**。 |
| C6 | 多账号 / 多端点 | **可选增强(建议本期最小实现)** | 通用做法是「命名配置档」(named config / host 切换):同一台机可存多个 API 端点 × 多个账号,`--workspace`/环境变量临时覆盖。本期最小集:配置文件支持单一 `current` + 一个 `hosts` map(按 API 基址 key),`mesh auth login` 写入当前 host;**多账号并发切换列可选增强**,但凭证文件结构应预留多 host 以免日后破坏兼容。 |
| C7 | 令牌存储后端:OS 钥匙串(可选)降级 0600 文件 | **可选增强(文件为必备基线)** | 通用最佳实践:优先 OS 安全存储(macOS Keychain / Windows Credential Manager / Linux Secret Service),不可用时降级到 0600 凭证文件并对过宽权限告警。无头服务器/容器通常无桌面钥匙串,故**文件 0600 是必备基线**,钥匙串为锦上添花。本期建议:**直接落 0600 文件**(简单、可移植、与 daemon 激活码 0600 一致),钥匙串支持列后续增强。 |

### 1.2 配置(config)

| # | 功能点 | 等级 | 说明 |
|---|--------|------|------|
| C8 | `mesh config set/get` | **必备** | 管理:API 基址(`api_base`)、默认工作区(`workspace`)、默认输出格式(`output`)、可选默认项目。`set` 写用户配置文件;`get` 打印生效值。 |
| C9 | 配置优先级链 | **必备** | 通用契约:**命令行 flag > 环境变量 > 配置文件 > 内置默认**。环境变量命名 `MESH_*`(`MESH_API_BASE`/`MESH_WORKSPACE`/`MESH_OUTPUT`/`MESH_TOKEN`/`MESH_CONFIG`/`MESH_NO_COLOR`)。**密钥建议仅经 `MESH_TOKEN` 环境变量或登录态凭证文件**,不提供 `--token` flag。配置错误须报「文件路径 + 键名 + 期望/实际值」。 |
| C10 | `--config <path>` 显式指定 + 确定性脚本模式 | **可选增强** | 通用做法:支持 `--config` 覆盖默认发现路径;CI 场景提供「忽略远程/项目配置」确定性开关。本期至少支持 `MESH_CONFIG` env 指向配置文件。 |

### 1.3 工作项命令全集(与 issue.md §3 / project.md §3 / member.md / agent.md 端点一一对应)

| # | 命令 | 等级 | 对应端点(详见 §3 映射表) |
|---|------|------|---------------------------|
| C11 | `mesh issue list` (过滤/分页/`--output`) | 必备 | `GET /api/v1/issues`(issue.md §3.2,filters 深度≤3 条件≤20) |
| C12 | `mesh issue get <id>` | 必备 | `GET /api/v1/issues/{id}` |
| C13 | `mesh issue create --title --description-file <path> --priority --assignee --project` | 必备 | `POST /api/v1/issues`;长文本一律 `--description-file`(README §11.1 硬约定,避免 shell 转义吞参) |
| C14 | `mesh issue update <id> ...` | 必备 | `PATCH /api/v1/issues/{id}`(带乐观并发 `If-Match`/`version`,409→退出码 4) |
| C15 | `mesh issue status <id> <status>` | 必备 | issue.md 状态流转端点(非法迁移 422→退出码 3) |
| C16 | `mesh issue comment <id> --content-file <path>` | 必备 | comment 创建端点(comment-inbox.md);`--content-file` 强制 |
| C17 | `mesh issue children <id>` | 必备 | 子项查询(README §11.1) |
| C18 | `mesh issue dependencies <id>` | 必备 | 依赖查看(README §11.1) |
| C19 | `mesh project list/get/create` | 必备 | project.md §3 对应端点 |
| C20 | `mesh member list` | 必备 | member.md 名册端点 |
| C21 | `mesh agent list` / `mesh agent executions` | 必备 | agent.md 名册与运行历史端点 |
| C22 | `mesh execution get/logs/cancel` | 必备 | `GET /executions/{id}`、日志(§1.4)、`POST /executions/{id}:cancel`(runtime.md §3.1) |
| C23 | 列表命令统一游标翻页(`--limit`、自动跟随 `next_cursor` 的 `--all`) | 必备 | README §6.14 keyset 游标;`next_cursor=null` 末页 |

### 1.4 日志流式(`mesh execution logs --follow`)

| # | 功能点 | 等级 | 说明 |
|---|--------|------|------|
| C24 | `mesh execution logs <id>`(拉历史) | 必备 | `GET /api/v1/executions/{id}/logs?offset=N&stream=stdout|stderr`(runtime.md §3.1,REST 轮询/补历史) |
| C25 | `--follow` 实时流 | 必备 | 与 SSE/WS 的关系(写死):**CLI 优先 SSE 降级通道** `GET /api/v1/executions/{id}/logs/stream?offset=N`(runtime.md §3.3),因 CLI 侧 SSE(单向 GET)实现最简单且与 §6.8「先 POST 再 GET stream_url」同构;WS 主通道(`execution:{id}:logs` 频道、首帧认证)用于 Web,**CLI 不强制实现 WS**,留作可选增强。续传协议:记录已见最大 `offset`,断线以 `?offset=` 重连,服务端先补 `[offset, 已封口)` 再接实时尾,客户端按 `offset` 去重(不丢/不重/单调递增)。帧类型 `log`/`status`/`heartbeat`/`end`;收到 `end` 退出。 |
| C26 | `--follow` 下 Ctrl-C 优雅退出 | 必备 | 退码 130(通用 SIGINT 约定);不留悬挂连接。 |

### 1.5 导入导出(与 import-export.md data-jobs 联动)

| # | 功能点 | 等级 | 说明 |
|---|--------|------|------|
| C27 | `mesh export issues --project <key> --format csv|json -o <file>` | 必备 | 异步作业:`POST /data-jobs/export` → 轮询 `GET /data-jobs/{id}` 至 `completed` → `GET /data-jobs/{id}/download`(attachment 签名 URL)**流式**写到 `-o` 文件(大文件不全量载入内存;进度走 stderr)。`export_too_large`(413/failed)→ 退码 3。 |
| C28 | `mesh import issues --file <path> --dry-run` | 必备 | 三步:① 经 attachment.md 上传源文件取 `source_attachment_id`;② `POST /data-jobs/import` 建作业;③ `--dry-run` → `POST /data-jobs/import/{id}/validate`(不落库,打印映射预览 + 逐行错误 + `failed_rows` 预测,**退出码 0 即使有行级错误**,因作业本身成功);确认导入(去 `--dry-run`)→ `POST /data-jobs/import/{id}/run`(要求已 validate,否则 422 `validation_required`→退码 3;`source_changed` 422→重新 validate)。`completed_with_errors` 时下载错误报告附件并以退码 0 + stderr 警告呈现(部分成功语义)。 |
| C29 | 大文件流式上传/下载 + 断点 | 必备(流式)/ 可选(断点) | 上传经 attachment.md 分块/流式;下载流式落盘。断点续传列可选增强。 |

### 1.6 通用 flag 与机器可读契约

| # | 功能点 | 等级 | 说明 |
|---|--------|------|------|
| C30 | `--workspace <slug>` | 必备 | 覆盖默认工作区;所有需工作区的命令支持。 |
| C31 | `--idempotency-key <k>` | 必备 | 透传 `Idempotency-Key` 头(README §6.14/§6.5);**所有写命令**支持;不传则由 CLI 对创建类命令自动生成稳定键(可选),重复键返回首次结果。 |
| C32 | `--output table|json` | 必备 | 双模式;json 字段与 REST 包络一致(§1.6 契约)。 |
| C33 | `--verbose` / `--quiet` | 必备 | `--verbose` 打请求方法/路径/耗时到 stderr(不打 token);`--quiet` 仅留必要 stdout。 |
| C34 | `--yes`(跳过确认) | 必备 | 破坏性操作(删除、批量、import run)默认交互确认,`--yes` 跳过;非 TTY 下未给 `--yes` 直接报错退出(不在 CI 里弹提示)。 |
| C35 | 机器可读输出契约 | 必备 | `--output json` 时 stdout **仅**输出合法 JSON:成功 = REST 包络原样(`{"data":...}` / `{"data":[...],"next_cursor":...}`);失败 = REST 错误信封 `{"error":{"code","message","details"}}`。进度/spinner/日志/错误提示一律走 **stderr**,绝不污染 stdout 管道(通用做法)。 |
| C36 | 退出码分类 | 必备 | `0` 成功 / `1` 通用运行时错误(5xx、网络) / `2` 鉴权(401/403、未登录、令牌过期) / `3` 校验(400/422、参数/业务校验、`*_too_large`) / `4` 冲突(409 唯一约束/乐观锁/状态冲突);429 限流 → 按 `Retry-After` 退避重试(可配上限)后仍失败归 `1`;SIGINT 130。 |

### 1.7 终端体验周边

| # | 功能点 | 等级 | 说明 |
|---|--------|------|------|
| C37 | shell 补全(bash/zsh/fish) | **可选增强(强烈建议本期做)** | 通用做法:CLI 自带 `mesh completion <shell>` 生成补全脚本(由命令框架自动派生),用户 source 之。成本低、体验收益高;静态补全(命令/flag)本期可做,动态补全(如 issue id 候选)列增强。 |
| C38 | 自动更新检查 | **可选增强(本期建议:仅提示不自动改)** | 通用做法分档:① 完全不检查;② 后台低频检查新版,仅在新版可用时往 **stderr** 打一行提示(绝不阻断、绝不自动安装、绝不出现在 `--output json` 的 stdout);③ 提供 `mesh update` 手动命令。建议本期取 ②+③ 的最弱形态或干脆①——**自动下载安装列非目标**(与 runtime 安装包「禁 curl\|sh、签名校验」同安全基线)。检查须可经 `MESH_NO_UPDATE_CHECK=1` 关闭。 |
| C39 | `--version` / `mesh version` | 必备 | 打印 CLI 版本 + 协商的 API 版本(v1);`mesh version --verbose` 含构建 commit。 |
| C40 | `--help` 层级 | 必备 | 三层:`mesh --help`(命令族总览)→ `mesh <group> --help`(子命令)→ `mesh <group> <cmd> --help`(flag 详解 + 示例)。未知命令/flag → 退码 2 + 建议最近匹配。 |

---

## 2. 数据模型草图

### 2.1 服务端:无新表,唯一依赖 `api_tokens`(auth.md §2.5)

CLI 不引入任何服务端新表。其鉴权完全复用 auth.md 的 `api_tokens`(PAT 路径)与会话体系(设备码路径)。CLI 对 `api_tokens` 的能力需求(逐一对照 auth.md 现状):

| CLI 需要 | auth.md 现状 | 结论 |
|----------|--------------|------|
| **scope 最小权限** | ✅ `scopes TEXT[]`(`issue:read`/`comment:write` 等) | 已满足。CLI 登录态可展示 scope;命令遇 403 提示「该 token 缺 scope X」。 |
| **前缀识别令牌类型** | ✅ `prefix`(如 `mesh_pat_` 前 8~12 位)+ 「令牌自带可校验前缀/类型位,区分 PAT/agent/refresh」 | 已满足。CLI `auth status` 用 prefix 掩码展示;服务端按前缀路由校验。 |
| **最后使用时间/地点** | ✅ `last_used_at` + `last_used_ip` | 已满足。`auth status` 可回显「最近使用」。 |
| **过期时间** | ✅ `expires_at`(建议强制设置) | 已满足。CLI 创建 PAT 的增强命令(若有)应鼓励设过期。 |
| **撤销** | ✅ `revoked_at` + `DELETE /workspaces/{ws}/api-tokens/{id}`,撤销立即生效→后续 401 | 已满足。支撑 `auth logout --revoke`。 |
| **只存哈希、明文仅一次** | ✅ `token_hash` UNIQUE,创建响应仅一次明文 | 已满足。CLI 创建 PAT 流程必须**当场落地明文到凭证文件**,关闭后不可再取。 |
| **`role_override` 服务端强校验** | ✅ 创建/使用双重校验,不得高于持有者角色 | 已满足,与 CLI 无额外交互。 |
| **持有者去多态** | ✅ `owner_member_id`(人/agent 经 JOIN `members.member_type`) | 已满足。CLI 用人类 PAT 即以本人 member 行留痕。 |

> **设备码路径的服务端依赖(需 auth.md 增量)**:设备码登录成功后颁发的凭证,建议**复用会话体系**(`sessions` 的 refresh + 短期 access JWT,auth.md §2.4/§3.1),并**新建设备码授权记录**(RFC 8628 的 `device_code`/`user_code` 哈希 + TTL + 单次消费 + 授权状态),其形态可参照 auth.md 既有的一次性令牌表(`password_reset_tokens`/`email_verification_tokens`:仅存 SHA-256 哈希、带 TTL、`consumed_at` 单次消费)。此为 auth.md 数据模型增量,不在 cli.md 内重复定义,cli.md 仅引用。

### 2.2 本地:两份文件分离(可分享配置 vs 0600 凭证)

```
~/.config/mesh/                 # 遵循 XDG($MESH_CONFIG 可整体改向)
├── config.yaml                 # 0644 可,非密;可入 dotfiles 分享
└── credentials.yaml            # 0600 强制;仅密;绝不入版本库
```

**config.yaml(非密)**:
```yaml
version: 1
current_host: https://mesh.example.com        # 当前 API 端点(多 host 预留)
output: table                                  # table | json
workspace: acme                                # 默认工作区 slug
hosts:                                         # 多端点 × 多账号预留结构
  https://mesh.example.com:
    workspace: acme
    # 凭证不在此,按 host 去 credentials.yaml 查
update_check: false                            # 可选增强位
```

**credentials.yaml(0600,仅密)**:
```yaml
version: 1
hosts:
  https://mesh.example.com:
    kind: pat                 # pat | device_session
    token: mesh_pat_...        # PAT 明文(或设备码会话的 refresh/access)
    refresh_token: ...         # 仅 device_session;PAT 无
    expires_at: 2026-12-01T00:00:00Z
    scopes: [issue:read, issue:write, comment:write]
    prefix: mesh_pat_Ab3       # 仅展示用,非密
```

**安全约束(写进 Spec)**:
- 凭证文件创建即 `chmod 0600`、父目录 `0700`;CLI 启动校验,过宽(group/other 可读)→ **stderr 告警 + 给出 `chmod 600` 建议**(通用做法可选「拒绝使用」,本期至少告警)。
- 配置文件读写经「写临时文件 → fsync → 原子 rename」,避免半写。
- 钥匙串后端为**可选增强**:接口抽象为 credential store,本期实现文件后端,预留 keychain 后端(降级链:keychain → 0600 文件,与业界通用做法一致)。
- `MESH_TOKEN` 环境变量优先级最高(供 CI),其存在时不读凭证文件该 host。

---

## 3. 接口设计草图

### 3.1 CLI 命令 → REST 端点映射表

| 命令 | 方法 + 端点 | 来源 / 备注 |
|------|-------------|-------------|
| `auth login`(PAT) | 探活 `GET /api/v1/me` | auth.md §3.1;凭证经 stdin/文件 |
| `auth login`(设备码) | `POST /api/v1/auth/device/code` → 轮询 `POST /api/v1/auth/device/token` | **需 auth.md 增量**(§3.2) |
| `auth logout`(会话) | `POST /api/v1/auth/logout` | auth.md §3.1 |
| `auth logout --revoke`(PAT) | `DELETE /api/v1/workspaces/{ws}/api-tokens/{id}` | auth.md §3.2 |
| `auth status` | `GET /api/v1/me`(+本地凭证元数据) | 不回显明文 |
| `config set/get` | 纯本地文件,无端点 | — |
| `issue list` | `GET /api/v1/issues?cursor&limit&filters` | issue.md §3.2 |
| `issue get` | `GET /api/v1/issues/{id}` | issue.md |
| `issue create` | `POST /api/v1/issues` (+`Idempotency-Key`) | issue.md;`--description-file` |
| `issue update` | `PATCH /api/v1/issues/{id}` (+`If-Match`) | 乐观并发 409→退码 4 |
| `issue status` | issue.md 状态流转端点 | 422→退码 3 |
| `issue comment` | comment 创建端点 | comment-inbox.md;`--content-file` |
| `issue children` | 子项查询端点 | README §11.1 |
| `issue dependencies` | 依赖查询端点 | README §11.1 |
| `project list/get/create` | project.md §3 端点 | `create` 带幂等键 |
| `member list` | member.md 名册端点 | — |
| `agent list` | agent.md 名册端点 | — |
| `agent executions` | agent 运行历史端点 | — |
| `runtime register` | `POST /api/v1/runtimes`(返回一次性激活码 + 安装命令) | runtime.md §3.1 控制台侧;激活码经 0600 文件/stdin 交给 `mesh-runtime activate` |
| `runtime heartbeat` | `POST /api/v1/daemon/runtimes/{id}:heartbeat`(仅排障/演示) | runtime.md §3.1 daemon 侧;**真实守护进程心跳由 `mesh-runtime` 发起**,见 §0.3 收口 |
| `execution get` | `GET /api/v1/executions/{id}` | runtime.md §3.1 |
| `execution logs` | `GET /api/v1/executions/{id}/logs?offset=N&stream=` | REST 拉历史 |
| `execution logs --follow` | `GET /api/v1/executions/{id}/logs/stream?offset=N`(SSE) | runtime.md §3.3 SSE 降级通道;WS 主通道留增强 |
| `execution cancel` | `POST /api/v1/executions/{id}:cancel` | runtime.md §3.1 |
| `export issues` | `POST /data-jobs/export` → `GET /data-jobs/{id}` → `GET /data-jobs/{id}/download` | import-export.md §3.5/§3.6;下载流式落盘 |
| `import issues --dry-run` | 上传 attachment → `POST /data-jobs/import` → `POST /data-jobs/import/{id}/validate` | import-export.md §3.2/§3.3;不落库 |
| `import issues`(执行) | `POST /data-jobs/import/{id}/run` | import-export.md §3.4;要求已 validate |

### 3.2 设备码授权端点(需 auth.md 增量,RFC 8628 形态)

> auth.md 现状:§3.1 仅有授权码 + PKCE(第三方浏览器登录)与 PAT 端点,**无设备码端点**。以下为建议增量,供 auth.md 评审(端点定义、错误码、限流、审计登记均在 auth.md 落地,cli.md 仅引用)。

**`POST /api/v1/auth/device/code`**(公开,限流同登录类 §3.6)
- 请求:`{client_id: "mesh-cli", scope: "<space-joined>"}`
- 响应 200:`{data: {device_code, user_code, verification_uri, verification_uri_complete, expires_in(默认 900), interval(默认 5)}}`
- 落库:设备码授权记录,仅存 `device_code_hash`/`user_code_hash`、TTL、`status=pending`、请求 scope。

**`POST /api/v1/auth/device/token`**(公开,限流)
- 请求:`{grant_type: "urn:ietf:params:oauth:grant-type:device_code", device_code, client_id}`
- 轮询响应:
  - `authorization_pending`(用户未授权)→ CLI 继续轮询(建议用 400/403 携带具名 `code`,与 §6.14 错误信封一致,而非裸 OAuth 错误体);
  - `slow_down` → 间隔 +5s;
  - `access_denied` → 终止;
  - `expired_token` → 重新发起;
  - 成功 200 → `{data: {access_token, refresh_token, token_type: "Bearer", expires_in, scope}}`,**同事务**把设备码记录置 `consumed_at`(单次消费),并创建 `sessions` 行(复用会话撤销链路 §3.7)。
- 用户在浏览器侧的授权确认页 = Web 端一个登录态页面(校验 `user_code` → 展示请求的 scope → 批准/拒绝),属 auth.md UI 增量。

### 3.3 日志流式端点(复用 runtime.md,不新增)

- 主通道(WS,Web 用):`/ws` 订阅频道 `execution:{id}:logs`,首帧认证(README §6.16,禁 query 传 token)。
- **CLI 用 SSE 降级通道**:`GET /api/v1/executions/{id}/logs/stream?offset=N`,与 WS 共用同一 offset 续传协议(runtime.md §3.3)。帧:`{type:"log",stream,offset,line}` / `{type:"status"}` / `{type:"heartbeat"}` / `{type:"end",status,final_offset}`。
- 与 README §6.8 的关系:§6.8「POST→201 stream_url→GET SSE」面向聊天生成(需先提交请求);执行日志是**已存在资源的订阅**,直接 GET stream 即可,无需先 POST,属 §6.8 的同构简化形态。

### 3.4 退出码 ↔ 错误信封 `code` / HTTP 映射表

| 退出码 | 语义 | HTTP | 错误信封 `code`(示例,§6.14) |
|--------|------|------|------------------------------|
| 0 | 成功 | 2xx | —(成功包络 `{"data":...}`) |
| 1 | 通用运行时错误 | 500 / 502 / 网络超时 / 429 重试耗尽 | `internal_error` / `storage_error` / `rate_limited` |
| 2 | 鉴权失败 | 401 / 403 | `unauthorized` / `forbidden`(含未登录、令牌过期/撤销、scope 不足) |
| 3 | 校验失败 | 400 / 413 / 415 / 422 | `validation_error` / `filter_too_complex` / `payload_too_large` / `query_cost_exceeded` / `export_too_large` / `validation_required` / `source_changed` / `invalid_state_transition`(422) |
| 4 | 冲突 | 409 | `conflict`(唯一约束、乐观锁版本不符、状态机冲突、`move_confirmation_required`) |
| 130 | 用户中断 | —(SIGINT) | — |

> 注:`404 not_found` 归入退码 3(校验/资源不存在,通用 CLI 惯例)或单列;本期建议归 3 并在 Spec 写死。`410 gone`(激活码过期)在 `runtime register` 场景 → 退码 3 + 提示重新创建。`423 locked` → 退码 4。映射表须在 cli.md 与 README §6.14 双向一致。

---

## 4. UI(终端体验)设计

| 维度 | 规则(通用做法) |
|------|-----------------|
| **table 渲染** | 仅当 stdout 是 **TTY** 且 `--output table`(默认)时渲染人类表格:固定列序、表头大写、列宽自适应、超长截断带省略号;被管道/重定向(非 TTY)时**自动降级**为无装饰、无颜色、可被 `cut/awk` 处理的对齐文本(或直接建议 `--output json`)。提供 `--no-header`(脚本去表头)。table 仅展示摘要列,完整字段用 `get --output json`。 |
| **颜色** | 尊重 `NO_COLOR` 环境变量(存在即禁用);`--color=auto|always|never`(auto = 仅 TTY 上色);`--output json` 时**永不上色**;状态类字段(online/failed)用语义色但非唯一信息载体(色盲可达)。 |
| **TTY 检测** | 以 stdout/stderr 各自 `isatty()` 决定:stdout 非 TTY → 禁表格装饰/颜色/进度;stderr 非 TTY → 禁 spinner 动画(改为一行式日志)。 |
| **进度/spinner** | 长操作(export/import 轮询、device 登录轮询、下载)的 spinner/进度条**一律写 stderr**,绝不写 stdout;`--quiet` 或非 TTY 时降级为周期性一行进度或静默;`--output json` 时进度只在 stderr,stdout 保持单一合法 JSON 文档。 |
| **stdout/stderr 分流** | 铁律:**stdout 只放结果数据**(table 或 json);**stderr 放诊断**(进度、spinner、`--verbose` 请求日志、警告、错误)。使脚本可 `mesh issue list --output json \| jq` 不被污染。 |
| **交互确认** | 破坏性/不可逆操作(删除、`import run`、`logout --revoke`)默认交互式 `[y/N]`;`--yes` 跳过;**非 TTY 且未给 `--yes` → 报错退出(退码 3)**,绝不静默执行也绝不 hang 等输入。 |

---

## 5. UX 设计

### 5.1 首次使用流(黄金路径)

```
$ mesh auth login
! First, open this URL in your browser and enter the code:
    https://mesh.example.com/device
    Code: WDJB-MJHT
  (attempting to open your browser automatically…)
✓ Waiting for authorization… (polling)
✓ Logged in as zhangsan@acme.dev
  Default workspace: acme   (override with --workspace or `mesh config set workspace`)
  Token stored in ~/.config/mesh/credentials.yaml (mode 0600)

$ mesh issue list                      # 立即可用
```
- 自动唤起浏览器失败 → 仅打印 URL + 码,不报错。
- 无浏览器环境(SSH)→ 提示在另一设备打开;设备码流正是为此设计(受限输入设备)。
- 备选:`mesh auth login --with-token` 走 PAT(stdin 粘贴,隐藏回显),适合 CI/无头。

### 5.2 错误信息可操作性(通用做法:每条错误 = 发生了什么 + 下一步)

| 场景 | 反例 | 正例(给下一步) |
|------|------|-----------------|
| 未登录 | `Error: 401` | `Error: not authenticated. Run \`mesh auth login\` to sign in.` |
| token 过期 | `unauthorized` | `Error: your token has expired. Run \`mesh auth login\` to re-authenticate.` |
| scope 不足(403) | `forbidden` | `Error: this token lacks scope \`issue:write\`. Re-create it with the needed scope, then retry.` |
| 乐观锁冲突(409) | `conflict` | `Error: the issue was modified by someone else. Re-fetch with \`mesh issue get X\` and retry.` |
| 校验失败(422) | `validation_error` | `Error: invalid --priority "urgent". Expected one of: none, low, medium, high.`(回显 `details`) |
| 工作区不存在/无权限 | `not_found` | `Error: workspace "acme" not found or you are not a member. List yours with \`mesh member list\`.` |
| 限流(429) | — | 自动按 `Retry-After` 退避重试(stderr 提示),耗尽后归退码 1。 |

### 5.3 `--help` 层级与可发现性

- 三层帮助(§1.7 C40);每个叶子命令的帮助含 **2~3 个真实示例**(含 `--output json` 与管道示例)。
- 未知子命令/flag → 退码 2 + 「Did you mean ...?」最近匹配。
- `mesh help <topic>` 支持概念主题(如 `mesh help auth`、`mesh help idempotency`)为可选增强。

---

## 6. 安全要点(红线)

1. **令牌不落 argv / shell 历史 / 进程表**:不提供 `--token <x>` flag;PAT 经 stdin/0600 文件、`MESH_TOKEN` env 注入;设备码 `device_code` 仅在 CLI 进程内轮询,不打印。与 runtime.md「激活码经受限 stdin/0600 文件、用后即毁、不进命令行参数」同基线。
2. **凭证文件 0600 / 父目录 0700**,启动校验过宽即告警;密钥与可分享配置物理分离(§2.2)。
3. **撤销联动实时生效**:`auth logout --revoke` / Web 侧撤销 → auth.md §3.7 outbox→realtime,相关连接下次心跳被拒;access JWT 短期,撤销最长延迟 = 其 TTL。CLI 收 401 即清本地该 host 凭证并退码 2。
4. **stderr/日志不回显 token**:`--verbose` 只打方法/路径/状态/耗时;`Authorization` 头恒为 `Bearer [REDACTED]`;错误信息不泄漏 token/堆栈/SQL/内部 ID(§6.14 message 约束)。全通道脱敏复用 `runtime_credentials.redact_in_logs` 黑名单(§6.16)。
5. **传输层**:全 HTTPS/HSTS;若 API 基址被配成明文 `http`,**fail-closed 拒绝**(除显式 `--insecure` 开发开关,默认禁)。机器/daemon 命名空间强制 TLS(runtime.md §3.5 红线)。
6. **WebSocket 若实现**:禁 URL query 传 token,用首帧认证(§6.16);本期 CLI 走 SSE,天然规避 query 传参问题(SSE 用 `Authorization` 头或会话 cookie 之外的头认证;若 EventSource 不能设头,则改 fetch streaming 自带重连对账,§6.8 第 4 条)。
7. **OpenAPI 暴露面最小化**:OpenAPI 3.1 随仓库发布(README §11.2),但 **`/api/v1/daemon/*` 机器端点与内部管理端点(审批 freeze、token 轮换等)建议不在公开 OpenAPI 文档中暴露,或以 `x-internal: true` 标注从公共文档剔除**——减少攻击者侦察面。此点交 @Mesh 安全审核员 评审定夺。
8. **设备码安全(需 auth.md 落)**:`device_code`/`user_code` 仅存哈希 + 短 TTL(默认 15min)+ 单次消费;`user_code` 高熵易读(防猜测);轮询端点限流防 `user_code` 爆破;授权确认页明确展示请求 scope。
9. **导入导出**:源附件须经 attachment.md 扫描放行(`scan_status IN clean/skipped`)方可建业(import-export.md §3.2);CLI 上传不绕过该闸门。
10. **`role_override` 不越权**:CLI 不暴露任何绕过角色/scope 的旁路;服务端双重校验(auth.md §5.5)为准。

---

## 7. 边界与非目标(本期不做)

| # | 非目标 | 依据 / 说明 |
|---|--------|-------------|
| N1 | **官方 SDK(任何语言)** | README §11.2 已显式声明本期不提供,列后续规划;第三方 SDK 可基于 OpenAPI 生成。CLI 是本期唯一的官方高层开发者接口(REST + OpenAPI + CLI)。 |
| N2 | **插件 / 扩展系统** | YAGNI;命令集本期固定,不做 `mesh plugin install` 之类。 |
| N3 | **TUI / 交互式全屏界面** | 本期仅做一次性命令 + 必要的行内确认/隐藏回显;不做看板/列表的全屏 TUI。 |
| N4 | **自动下载/安装更新** | 仅可做「stderr 提示有新版」,自动安装列非目标(与 runtime 安装包签名校验、禁 curl\|sh 同安全基线)。 |
| N5 | **OS 钥匙串凭证后端** | 本期落 0600 文件;钥匙串为可选增强,接口预留(§2.2)。 |
| N6 | **WS 日志通道实现(CLI 侧)** | CLI 用 SSE;WS 主通道服务 Web,CLI 实现 WS 列可选增强。 |
| N7 | **多账号并发切换的完整 UX** | 配置文件结构预留多 host/多账号,但本期 UX 仅「单一 current + 临时 `--workspace`/env 覆盖」,完整账号切换命令列增强(§1.1 C6)。 |
| N8 | **守护进程本体** | `mesh` CLI 不含 runtime daemon;daemon 是独立二进制 `mesh-runtime`(runtime.md),`mesh runtime register` 仅建影子记录并引导安装。 |

---

## 8. 版本与分发

### 8.1 CLI 版本 ↔ API 版本关系

- **API 版本**:URI 版本化 `/api/v1`(README §11.2);破坏性变更升 `/api/v2` 并与 v1 **并存 ≥3 个月**,经 `Deprecation`/`Sunset` 响应头公告;非破坏新增(新字段/新端点)在 v1 内演进,**旧客户端忽略未知字段**(故 CLI 解析 JSON 须容忍未知字段,前向兼容)。
- **CLI 版本**:独立语义化版本(SemVer)。CLI 与 API 版本**解耦但协商**:`mesh version --verbose` 报告 CLI 版本 + 其目标的 API 版本(v1)。CLI 应在响应头/`/api/v1` 探测到 `Deprecation`/`Sunset` 时,于 stderr 提示「当前 API 版本将于 X 弃用,请升级 CLI」。
- **兼容承诺**:同一 CLI 大版本内,面向脚本的 `--output json` schema 与退出码语义保持稳定;CLI 自身的破坏性变更(改 flag/改 json 字段)走 CLI 大版本 + CHANGELOG。

### 8.2 OpenAPI 兼容性承诺(README §11.2 落地)

- `docs/api/openapi.yaml`(FastAPI 自动生成 + Spec 人工校准)为开发者契约真源,覆盖 §6.14 包络/错误码/分页 + 各模块端点,每端点含请求/响应 schema 与错误示例。
- **CLI 的请求构造与响应解析应以 OpenAPI 为契约对齐**(可作为契约测试:CLI e2e 对 OpenAPI 校验),避免 CLI 与 REST 漂移。
- 内部/机器端点(`/api/v1/daemon/*`、freeze/token 轮换等管理面)在公开 OpenAPI 中以 `x-internal` 剔除或单独私有规格(§6.7 安全,交安全审核员定夺)。

### 8.3 分发渠道(通用建议,不绑定厂商)

| 渠道 | 说明 | 等级 |
|------|------|------|
| **单一静态二进制**(多平台多架构) | 主分发形态;经仓库 Releases 发布,**附签名与校验和**(SHA-256 + 签名,公钥随产品发布,与 runtime.md 安装包同基线);用户下载后本地校验。 | 必备 |
| **安装脚本(非管道)** | 提供**可审阅**的安装脚本,但**不鼓励 `curl \| sh` 盲管道**(runtime.md 已废此模式);脚本逐条可见、下载→校验→解包到可审阅目录。 | 必备(安全形态) |
| **系统包管理器**(homebrew/apt/yum/AUR/scoop 等通用类目) | 列增强;社区/官方维护 formula 提升可达性,但本期不承诺官方仓库。 | 可选增强 |
| **容器镜像** | 列增强;CI 场景以 `FROM` 方式引入 CLI。 | 可选增强 |
| **CLI 自更新** | 仅 `mesh update` 手动命令(下载签名包→校验→替换),**默认关闭、绝不静默**(§7 N4)。 | 可选增强 |

---

## 附:需其它 Spec 协同的开放项

1. **auth.md(设备码增量)**:`POST /auth/device/code`、`POST /auth/device/token`、设备码授权记录表(哈希 + TTL + 单次消费)、浏览器授权确认页 UI、轮询限流、审计动作码(如 `auth.device_login`)登记 §6.7/§2.6。**这是 cli.md 落地的硬前置**(否则 `mesh auth login` 只能走 PAT)。
2. **安全维度**:① OpenAPI 是否公开 `/api/v1/daemon/*` 与管理端点(`x-internal` 策略);② 设备码 `user_code` 熵与轮询爆破防护阈值;③ CLI 凭证文件降级策略(钥匙串缺失时 0600 是否需「拒绝」而非「告警」);④ 明文 `http` API 基址 fail-closed 的例外开关边界。
3. **架构 / UX 维度**:① `mesh runtime register/heartbeat` 与 `mesh-runtime` daemon 的命名/职责切分(§0.3)是否合并表述为「`mesh runtime register` + 引导安装 `mesh-runtime`」;② 多账号是否本期最小化(§1.1 C6);③ CLI 技术选型(后端栈 Python 已定,CLI 本体语言/框架选型,需评估与后端 OpenAPI 契约测试的协同)。
4. **功能点穷举比对维度**:把主流开发者 CLI 的通用能力面(鉴权多模式、配置层级、双输出、流式日志、导入导出、补全、退出码契约、错误可操作性)逐一比对 cli.md,反复复查无漏项,特别核对「自动更新/插件/TUI/SDK」等非目标是否与本调研一致。

---

*匿名化复核:本文「业界通用做法」均表述为中性设计模式与标准协议(RFC 8628 设备授权、XDG Base Directory、NO_COLOR、sysexits、OS 安全存储抽象),未出现任何具体产品名称、厂商或外链。*
