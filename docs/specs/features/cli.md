# 开发者平台 CLI(`mesh` 命令行工具)功能 Spec

> **所属层**:平台能力层 / 开发者平台(README §11 开发者平台契约的详 Spec)。
> **依赖的其他 Spec**:
> - `auth.md`(§2.4.2 `device_authorizations` / §2.5 `api_tokens` 与 §2.5.1 令牌前缀注册表 / §3.1 会话与 OAuth 与**当前 Bearer 自省/自撤销端点** / §3.1.1 设备授权端点 / §3.2 token 端点 / §3.7 撤销联动):CLI 鉴权的唯一服务端依赖;PAT 路径零增量;**设备码授权已在 auth.md 全量闭环(MES-76 H7:表/状态机/端点/限流/审计/爆破防护量化),本 Spec §3.2 仅承载 CLI 侧流程语义并引用**。
> - `issue.md`(§3 端点)、`project.md`(§3 端点)、`member.md`(名册)、`agent.md`(名册与运行历史)、`comment-inbox.md`(评论端点):工作项命令族的 REST 映射。
> - `runtime.md`(§3.1 端点 / §3.3 日志流式 / §3.5 daemon 鉴权):`mesh execution *` 与 `mesh runtime register`;**真实守护进程心跳属独立二进制 `mesh-runtime`(daemon 命名空间),不经 `mesh` CLI**(§1.3 收口)。
> - `import-export.md`(§3 data-jobs 端点)、`attachment.md`(签名直传/下载):`mesh export/import` 联动。
> **被依赖方**:无(终端用户工具)。OpenAPI(§5.4)是 REST API 的机器可读契约,CLI 以其为对齐基准。
>
> **全局一致性锚点(canonical anchor)**:本 Spec 是 [README.md](../README.md) §11「开发者平台契约」的**详 Spec**。§11.1 已就**命令族与语义**、**PAT/设备码登录与令牌本地保护**、**双输出模式**、**退出码分类**、**`--description-file`/`--content-file` 长文本约定**作出唯一权威契约;§11.2 已就 **OpenAPI 3.1 随仓库发布**、**URI 版本化与破坏性变更并存弃用周期**、**本期无官方 SDK** 作出唯一权威契约。本 Spec 仅**展开其实现细节**(命令→端点映射、设备码流程、本地配置/凭证结构、终端体验、验收),**不复述、不改写契约原文**——凡与 §11 冲突,一律以 README 为准。相关契约锚点:API 包络/错误/分页(§6.14)、幂等键(§6.5/§6.14)、凭证全通道脱敏(§6.16)、流式输出协议(§6.8)。

---

## 1. 功能描述

### 1.1 模块定位

`mesh` 是 Mesh 的官方命令行工具:**与 Web 同源的 REST 瘦客户端**,经 `api_tokens`(PAT)或设备码会话鉴权(§11.1),面向开发者与自动化场景(CI/脚本/无头服务器):

- **工作项自动化**:issue/project/member/agent/execution 全命令族,`--output json` 机器可读契约稳定;
- **流式日志**:`mesh execution logs --follow` 经 SSE 降级通道实时跟随;
- **导入导出**:与 import-export.md data-jobs 联动,大文件全程流式;
- **开发者契约**:OpenAPI 3.1 随仓库发布,CLI 与 REST 以契约测试对齐(§5.4)。

### 1.2 功能点 + 用户场景表

**鉴权与配置**

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| C1 | PAT 登录 | `mesh auth login --with-token` 经 **stdin/0600 文件**读入(绝不作命令行参数);一次 `GET /api/v1/me` 探活校验 | CI 预置 `MESH_TOKEN` |
| C2 | 设备码登录(默认) | `mesh auth login` 走 OAuth 设备授权(RFC 8628 形态,§3.2):取码 → 打印 URL+码并尝试唤起浏览器 → 按 `interval` 轮询 → 成功落地会话凭证 | 本地开发首次登录 |
| C3 | `auth status` | 展示主体/默认工作区/令牌类型/prefix 掩码/scope/过期/`last_used_at`/API 基址;**不回显明文**;未认证退出码 2 | 排障 |
| C4 | `auth logout` | 会话登录 → 撤销 refresh + 清本地;PAT 登录 → **默认仅清本地**,`--revoke` 才服务端吊销 | 换机/离职 |
| C5 | 令牌刷新与过期 | 会话 401 → 静默 refresh 轮换重写本地;refresh 失效 → 退出码 2 + 提示 `mesh auth login`;PAT 过期即 401→2。**过期绝不报错为通用错误**。**刷新并发互斥(口径对齐 auth.md §3.8 写死,C8)**:同进程内**单飞(进程内锁 + 等待队列,并发调用方共享同一请求结果)**;多进程共用凭证文件时经「**胜者进程写文件 + 后来进程强制重读**」收敛——命中宽限路径或收到 401 时**必须先重读 credentials.yaml 再宣告失败**(胜者进程可能已将新 refresh 写入),重读后仍失败且已超宽限窗 → 退码 2 重新登录。**不加文件锁**:跨进程收敛由服务端有界幂等轮换(§3.8 R5-H1 胜者唯一下发 + 宽限只发 access)与凭证文件真源组合保证,响应乱序不破坏收敛 | 长期脚本 |
| C6 | `config set/get/unset` · `config list --all` | API 基址 / 默认工作区 / 输出格式;优先级链 **flag > env(`MESH_*`) > 配置文件 > 默认**;`unset <key>` 删除该键恢复默认(评审建议项吸收);`config list --all` 逐项列出全部生效配置并**标注来源 `default\|env\|file\|flag`**(排障基线,评审建议项吸收)。**env 解析规则写死**:env 布尔值接受 `1/0/true/false/yes/no`(大小写不敏感),枚举键取值与 flag 同集(非法 → 退码 3),**空串 env 值视为未设置**(落下一级优先级),不报「空值非法」 | 多环境切换 |

**资源命令族**(与 REST 端点一一对应,§3.1 映射表)

| # | 命令 | 说明 |
|---|------|------|
| C7 | `mesh issue list/get/create/update/status/comment/children/dependencies` | issue.md §3 端点;`create --title --description-file <path> --priority --assignee --project`;长文本一律 `--description-file`/`--content-file`(§11.1 硬约定);`update` 带 `If-Match` 乐观并发(409→退码 4) |
| C8 | `mesh project list/get/create` | project.md §3 |
| C9 | `mesh member list` | member.md 名册 |
| C10 | `mesh agent list/executions` | agent.md 名册与运行历史 |
| C11 | `mesh execution get/logs/cancel` | runtime.md §3.1;`logs --follow` 见 C12 |
| C12 | `mesh execution logs --follow` | **SSE 降级通道** `GET /workspaces/{ws}/executions/{id}/logs/stream?offset=N`(runtime.md §3.3);offset 续传去重(不丢/不重/单调);帧 `log`(含 RFC3339 `ts`,C5/评审 C5)/`status`/`heartbeat`/`end`,收 `end` 退出;Ctrl-C 退码 130 不留悬挂连接。**时间戳展示(评审 C5 写死)**:`execution logs`(拉历史与 `--follow`)**默认在每行行首展示 RFC3339 时间戳**(取自帧 `ts`),`--timestamps=false` 关闭(管道场景取裸行);**`--since <RFC3339\|相对时长>` 与 `--follow` 下的 stdout/stderr 分流过滤列可选增强**(REST 拉历史路径的 `stream=stdout\|stderr` 过滤已支持,见 §3.1) |
| C13 | `mesh export issues --project <key> --format csv\|json -o <file>` | `POST /data-jobs/export` → 轮询至 `completed` → 签名下载 URL **流式**写文件;进度走 stderr |
| C14 | `mesh import issues --file <path> [--dry-run] [--strict]` | 上传源附件 → 建作业 → `--dry-run` 仅 validate(打印映射预览 + 逐行错误,退码 0);去 `--dry-run` 执行 run(要求已 validate,否则 `422 validation_required`→3);`completed_with_errors` 下载错误报告 + stderr 警告(默认退码 0,部分成功语义;**`--strict` 模式下 `completed_with_errors` 退码 3**——自动化管线对部分成功零容忍时的显式开关,评审 M1) |
| C15 | `mesh runtime register` | **控制台侧**建影子记录(`POST /api/v1/workspaces/{ws}/runtimes`,§3.1 workspace 前缀),返回一次性激活码 + 安装命令;激活码经 0600 文件/stdin 交给独立二进制 `mesh-runtime activate`(runtime.md),**不进命令行参数** |
| C15b | `mesh runtime status <id>` | **人工排障只读命令**(MES-76 H8 收口,取代此前的代理心跳):经**控制台 API** `GET /api/v1/workspaces/{ws}/runtimes/{id}` 展示 runtime 状态/最近心跳时间/负载/在线性——**只读取 daemon 已上报的数据,绝不向 daemon 命名空间发心跳、不伪造机器活性**;鉴权为用户凭证(控制台域) |

**通用契约**

| # | 功能点 | 说明 |
|---|--------|------|
| C16 | `--workspace <slug>` | 覆盖默认工作区;所有需工作区的命令支持 |
| C17 | `--idempotency-key` | 透传 `Idempotency-Key`(§6.5/§6.14);**所有写命令**支持 |
| C18 | `--output table\|json` | json 与 REST 包络一致;**stdout 只放结果数据**,进度/spinner/日志/错误一律 stderr |
| C19 | 退出码分类 | `0` 成功 / `1` 通用(5xx/网络/429 重试耗尽)/ `2` 鉴权(401/403/未登录/过期)/ `3` 校验(400/404/413/422)/ `4` 冲突(409/423);`130` SIGINT |
| C20 | 游标翻页 | 列表命令统一 `--limit` + 自动跟随 `next_cursor` 的 `--all`(§6.14) |
| C21 | `--verbose`/`--quiet`/`--yes` | verbose 输出面收敛为**仅 method/path/状态码/耗时**(stderr):**显式不含请求/响应体、不含除掩码 `Authorization`(`Bearer [REDACTED]`)外的任何头**;一次性凭证(`runtime register` 激活码)只写指定 sink(0600 文件/stdin),不进任何诊断输出;破坏性操作默认交互确认,`--yes` 跳过,**非 TTY 未给 `--yes` 报错退出** |
| C22 | `mesh completion <shell>` | bash/zsh/fish/**powershell** 静态补全脚本(命令/flag;评审 C7④ 补齐 PowerShell) |
| C23 | `mesh version` | CLI 版本 + 目标 API 版本(v1);探测到 `Deprecation`/`Sunset` 响应头 → stderr 提示升级 |
| C24 | `--jq <expr>` | **内联 jq 过滤(评审 C4 补入,脚本化基线)**:对成功包络的 `.data`(单对象或数组)求 jq 表达式,stdout 输出求值结果 JSON(数组逐元素逐行输出,便于管道);**仅与 `--output json` 语义兼容**——与 `table` 同用 → 退码 3;表达式编译/求值错误 → 退码 3 + stderr 定位;**内置 jq 求值库实现,不依赖外部 jq 进程**;失败响应(错误信封)不经 `--jq`,原样按错误路径输出 |
| C25 | `--web` | **GUI 桥接(评审 C7① 补入)**:`get` 类资源命令(issue/project/execution/runtime/member/agent/squad)携 `--web` 时**不请求数据、不打印**,直接以默认浏览器打开该资源的规范深链(search-command-palette.md §3.4);浏览器唤起失败 → 退码 0 并打印深链 URL 供手工打开 |
| C26 | 代理与自定义 CA | **企业内网/TLS inspection 基线(评审 C3 补入,自托管一键部署场景必备)**:尊重 `HTTPS_PROXY`/`HTTP_PROXY`(大小写双形,大写优先)与 `NO_PROXY`/`no_proxy`(逗号分隔:**域名按后缀匹配**、**支持 CIDR IP 段**、`*` 匹配全部);**带认证代理**经代理 URL userinfo(`http://user:pass@host:port`,**仅接受 env,不接受 config 持久化**,与令牌不落盘同级);自定义 CA **三入口**(优先级 `--ca-cert <pem>` 单次 flag > config 键 `tls.ca_cert`(per-host)> env `SSL_CERT_FILE`(OpenSSL 兼容)),缺省走系统信任库;**与 §5.3 `--insecure` fail-closed 边界统一**:默认证书校验 fail-closed,校验失败错误携带**可操作诊断线索**(端点/代理/CA/DNS 逐项提示),`--insecure` 仍为单次调用逃生旗标(不持久化、每次 stderr 警告),`/api/v1/daemon/*` TLS 强制不随 `--insecure` 放宽 |
| C27 | `mesh config unset <key>` · alias 别名 | `unset` 见 C6。**alias 别名系统(评审 C7③ 补入,一线标配,不随 N2 插件排除)**:config.yaml `aliases:` 映射(如 `co: issue create`、`ls: issue list`)为**配置级语法糖**——单级字符串展开(展开结果**不再递归展开**,防环防套娃)、位置参数原样透传、与 flag 组合正常解析;未知 alias → 退码 3 + did-you-mean;**alias 不构成插件/扩展机制**(N2 边界不变:命令集本期固定,alias 仅既有命令的别名) |

### 1.3 `mesh runtime` 与 `mesh-runtime` daemon 的职责收口(评审 H8 写死:两鉴权域零混用)

README §11.1 的 `mesh runtime register/status` 与 runtime.md 的机器接口(独立二进制 `mesh-runtime` + `/api/v1/daemon/*` 命名空间)形态不同,本 Spec 写死切分:

- `mesh runtime register` = **控制台侧**建影子记录并引导安装 daemon(§1.2 C15);
- `mesh runtime status` = **人工排障只读**(§1.2 C15b):经用户凭证调**控制台** `GET /api/v1/runtimes/{id}` 读取 runtime 元数据/最近心跳/负载;**只读,绝不向 `/api/v1/daemon/*` 发任何请求**;
- **`mesh` CLI 不提供任何形式的心跳命令**(此前的「代理心跳 `mesh runtime heartbeat`」已删除——经用户凭证调 daemon 端点与「两域不混用」自相矛盾,且会伪造机器活性误导 reaper 判断);
- 真实守护进程的注册激活/心跳/领取/上报**一律且仅由** `mesh-runtime` 以 `mesh_rt_` 令牌走 daemon 命名空间(runtime.md §3.2/§3.5,`mesh_rt_` 前缀见 auth.md §2.5.1 注册表),**控制台域与机器域零混用**。

### 1.4 边界与非目标(明确不做什么)

| # | 非目标 | 依据 |
|---|--------|------|
| N1 | 官方 SDK(任何语言) | §11.2 显式声明本期不提供;第三方 SDK 可基于 OpenAPI 生成 |
| N2 | 插件/扩展系统 | YAGNI,命令集本期固定 |
| N3 | TUI/全屏交互界面 | 仅一次性命令 + 必要行内确认 |
| N4 | 自动下载/安装更新 | 仅可 stderr 提示新版;自动安装非目标(与 runtime 安装包签名校验、禁盲管道同安全基线) |
| N5 | OS 钥匙串凭证后端 | 本期 0600 文件;keychain 接口预留,列增强 |
| N6 | CLI 侧 WS 日志通道 | CLI 用 SSE;WS 主通道服务 Web |
| N7 | 多账号并发切换完整 UX | 配置结构预留多 host,本期「单一 current + `--workspace`/env 覆盖」 |
| N8 | 守护进程本体 | daemon 是独立二进制 `mesh-runtime`(runtime.md) |
| N9 | **使用遥测 / 崩溃上报(评审 C6 写死)** | **CLI 无任何使用遥测与崩溃上报**(不采集命令用法、不上传 panic/堆栈、无错误统计回传)。**如后续版本引入,必须**:尊重 `DO_NOT_TRACK=1` 业界约定、提供 **env + config 双开关**(`MESH_TELEMETRY=0` / config `telemetry.enabled: false`,任一为关即关)、默认关闭且首次启用前显式告知 |
| N10 | `$EDITOR` 交互式编辑长文本(评审 C7② 表态) | 非目标:长文本一律 `--description-file`/`--content-file` 文件流(§11.1 硬约定,已兜底);拉起 `$EDITOR` 全屏交互与 N3(无 TUI/全屏交互界面)冲突,不引入 |
| N11 | `--wait` 异步等待原语(评审建议项表态) | export/import 已内建阻塞轮询至终态(C13/C14);execution/runtime 命令的通用 `--wait [--wait-timeout]` 列可选增强,本期以 `execution logs --follow` + `execution get` 组合替代 |
| N12 | pager 集成 / NDJSON 流式 / `-q` 仅 ID 输出(评审建议项表态) | 均可选增强:本期 `--output json` 单一合法 JSON(§3.5)即脚本契约,分页经 `--all`(C20);尊重 `PAGER` 的自动分页、超大数据 NDJSON 逐行流式、`-q` 仅输出新建资源 ID 均列增强,不混入本期稳定面 |
| N13 | 错误信封 `request_id` 透传(评审建议项表态,README §6.14 级) | **仅表态留待后续**:错误信封字段归 README §6.14 canonical(本 Spec 不自行扩字段);`request_id` 服务端透传与 CLI 错误行展示属 §6.14 级增强,随 §6.14 修订一并落地,本期 CLI 错误行已含 `code`/`message`/`details` 足够可操作 |

---

## 2. 数据模型与配置

> **全局契约引用**:API 包络/错误/分页/幂等以 [README.md](../README.md) §6.14/§6.5 为权威;凭证脱敏以 §6.16 为权威。
>
> **服务端零新表(CLI 本身)**:CLI 是 REST 瘦客户端,服务端唯一依赖是 auth.md 既有 `api_tokens` 与会话体系;**设备码授权记录为 auth.md 数据模型增量**(§3.2),不在本 Spec 重复定义。

### 2.1 服务端依赖核对(`api_tokens` 能力,auth.md §2.5 既有)

| CLI 需要 | auth.md 现状 | 结论 |
|----------|--------------|------|
| scope 最小权限 | `scopes TEXT[]`(创建时与角色权限取交) | 已满足;命令遇 403 提示「缺 scope X」 |
| 前缀识别类型 | `prefix` + `mesh_pat_`/`mesh_agt_` 前缀路由 | 已满足;`auth status` 用 prefix 掩码展示 |
| 最后使用 | `last_used_at` + `last_used_ip` | 已满足 |
| 过期 | `expires_at` | 已满足 |
| 撤销 | `revoked_at` + DELETE 端点,立即生效 | 已满足;支撑 `logout --revoke` |
| 只存哈希、明文仅一次 | `token_hash` UNIQUE,创建仅回显一次 | 已满足;CLI 创建 PAT 须**当场落地明文** |
| `role_override` 强校验 | 创建/使用双重校验 | 已满足 |

> **通用 Bearer-PAT 鉴权依赖(auth.md 已闭环,MES-76 H7)**:常规 REST 路由的鉴权依赖已按前缀路由统一校验链(JWT / `mesh_pat_` / `mesh_agt_`,权限取 scopes∩角色;`mesh_rt_`/`mesh_rft_` 在常规路由拒绝)——见 auth.md §2.5.1 前缀注册表与统一 Bearer 依赖条款,代表性端点集成测试见 auth.md §5.2。CLI「持 PAT 调用任意 `/api/v1` 端点」直接复用该依赖,不再是个别端点特例。

### 2.2 本地配置与凭证(两份文件,物理分离)

```
~/.config/mesh/                 # XDG 约定;$MESH_CONFIG 可整体改向
├── config.yaml                 # 0644 可;非密,可入 dotfiles
└── credentials.yaml            # 0600 强制;仅密,绝不入版本库
```

**config.yaml(非密)**:
```yaml
version: 1
current_host: https://mesh.example.com
output: table
workspace: acme
hosts:
  https://mesh.example.com:
    workspace: acme
    tls:
      ca_cert: /etc/ssl/corp/ca-bundle.pem   # 自定义 CA(C26,可选;非密路径,不含凭证)
aliases:                                      # 别名语法糖(C27,单级展开)
  co: issue create
  ls: issue list
```

**credentials.yaml(0600,仅密)**:
```yaml
version: 1
hosts:
  https://mesh.example.com:
    kind: pat                  # pat | device_session
    token: mesh_pat_...
    refresh_token: ...         # 仅 device_session
    expires_at: 2026-12-01T00:00:00Z
    scopes: [issue:read, issue:write, comment:write]
    prefix: mesh_pat_Ab3       # 仅展示用,非密
```

**本地安全约束**:
- 凭证文件创建即 `chmod 0600`、父目录 `0700`;启动校验 **fail-closed**:发现凭证文件/父目录 group/other 可读或可写 → **拒绝加载**(退码 2 + 一行修复指令 `chmod 700 <dir> && chmod 600 <file>`),**不降级为告警**(credentials.yaml 明文持有长效 PAT/refresh token,过宽权限即等同泄漏);
- **属主与链接校验(评审 M2 收口)**:凭证文件/父目录的 **owner 必须为当前进程 uid**(非本人所有即拒绝加载,退码 2 + 修复指令);**拒绝符号链接**(`lstat` 发现 symlink 即 fail-closed,防攻击者以链接指向共享/受控路径);无法修复的情形一律 **fail closed**,不存在「告警后继续读取」分支;
- 写入经「**临时文件(创建即 0600,同目录)** → fsync → 原子 rename」,避免半写与中间态过宽权限;
- `MESH_TOKEN` 环境变量优先级最高(CI),其存在时不读凭证文件该 host;
- 凭证后端抽象为 credential store,本期实现文件后端,预留 keychain(N5)。

---

## 3. 接口设计

> CLI 的一切能力经 `/api/v1` REST;包络/错误/分页/幂等以 §6.14/§6.5 为权威。

### 3.1 命令 → REST 端点映射表

> **路径约定(评审 C1 收口,与后端实际实现逐端点核对)**:集合/工作区作用域的资源操作**一律带 `/workspaces/{ws}/` 前缀**(`{ws}` = workspace UUID 或 slug,与 issue.md §3.1 集合端点同构),由 `--workspace`/默认工作区解析(C16),未解析到工作区即执行需工作区的命令 → 退码 3 + 可操作提示;**以全局唯一 UUID 寻址的单条资源项操作为无前缀例外**(逐项已在备注标注,如 `GET /issues/{id}`);**data-jobs 为用户维度资源,端点不带 `{ws}` 前缀**(作业绑定 `requested_by`)。各端点的权威定义以后端各 owner Spec 为准,本表为 CLI 视角的映射快照。

| 命令 | 方法 + 端点 | 来源 / 备注 |
|------|-------------|-------------|
| `auth login`(PAT) | 探活 `GET /api/v1/me` | auth.md §3.1;凭证经 stdin/文件;无前缀(用户维度) |
| `auth login`(设备码) | `POST /api/v1/auth/device/code` → 轮询 `POST /api/v1/auth/device/token` | **auth.md 增量**(§3.2);无前缀(鉴权域) |
| `auth logout`(会话) | `POST /api/v1/auth/logout`(或当前 Bearer 自撤销 `DELETE /api/v1/auth/token`) | auth.md §3.1;无前缀 |
| `auth logout --revoke`(PAT) | **当前 Bearer 自撤销 `DELETE /api/v1/auth/token`**(auth.md §3.1,评审 H7 新增;无需本地持有 token id) | auth.md §3.1;无前缀 |
| `auth status` | **当前 Bearer 自省 `GET /api/v1/auth/token`**(kind/token_id/prefix/scopes/expires_at/last_used_at,auth.md §3.1 新增)+ `GET /api/v1/me`(主体/工作区) | 不回显明文;无前缀 |
| `config set/get/unset` | 纯本地文件 | `unset` 恢复默认(C27);无前缀 |
| `issue list` | `GET /api/v1/workspaces/{ws}/issues?cursor&limit&filters` | issue.md §3.2;**workspace 前缀** |
| `issue get` | `GET /api/v1/issues/{id}` | issue.md;**无前缀例外**(UUID 单条寻址) |
| `issue create` | `POST /api/v1/workspaces/{ws}/issues`(+`Idempotency-Key`) | `--description-file`;**workspace 前缀** |
| `issue update` | `PATCH /api/v1/issues/{id}`(+`If-Match`) | 409→退码 4;**无前缀例外** |
| `issue status` | `PATCH /api/v1/issues/{id}`(状态字段流转,issue.md StatusPatch)/ 看板移动 `POST /api/v1/issues/{id}/move` | 422→退码 3;**无前缀例外** |
| `issue comment` | `POST /api/v1/issues/{id}/comments` | comment-inbox.md;`--content-file`;**无前缀例外**(按 issue UUID 寻址) |
| `issue children` / `dependencies` | `GET /api/v1/issues/{id}/children` · `GET /api/v1/issues/{id}/dependencies`(依赖写: `POST /api/v1/issues/{id}/dependencies`) | §11.1;**无前缀例外** |
| `project list` | `GET /api/v1/workspaces/{ws}/projects` | project.md §3;**workspace 前缀** |
| `project get` | `GET /api/v1/projects/{id}` | project.md §3;**无前缀例外** |
| `project create` | `POST /api/v1/workspaces/{ws}/projects`(+`Idempotency-Key`) | project.md §3;**workspace 前缀** |
| `member list` | `GET /api/v1/workspaces/{ws}/members` | member.md 名册;**workspace 前缀** |
| `agent list` | `GET /api/v1/workspaces/{ws}/agents` | agent.md 名册;**workspace 前缀** |
| `agent executions` | `GET /api/v1/workspaces/{ws}/executions?agent_id=<uuid>` | 运行历史即执行列表按 agent 过滤(runtime.md §3.1 owns `task_executions`);**workspace 前缀** |
| `runtime register` | `POST /api/v1/workspaces/{ws}/runtimes` | runtime.md §3.1 控制台侧(§1.2 C15);**workspace 前缀** |
| `runtime status`(排障只读) | `GET /api/v1/workspaces/{ws}/runtimes/{id}` | runtime.md §3.1 控制台侧;**不触达 daemon 命名空间**(§1.3 收口,评审 H8);**workspace 前缀** |
| `execution get` | `GET /api/v1/workspaces/{ws}/executions/{id}` | runtime.md §3.1;**workspace 前缀** |
| `execution logs` | `GET /api/v1/workspaces/{ws}/executions/{id}/logs?offset=N&stream=` | REST 拉历史(`stream=stdout\|stderr` 分流过滤已支持);**workspace 前缀** |
| `execution logs --follow` | `GET /api/v1/workspaces/{ws}/executions/{id}/logs/stream?offset=N`(SSE) | runtime.md §3.3;**workspace 前缀** |
| `execution cancel` | `POST /api/v1/workspaces/{ws}/executions/{id}:cancel` | runtime.md §3.1;**workspace 前缀** |
| `export issues` | `POST /api/v1/data-jobs/export` → `GET /api/v1/data-jobs/{id}` → `GET /api/v1/data-jobs/{id}/download` | import-export.md;流式落盘;**无前缀**(data-jobs 用户维度,作业绑 `requested_by`) |
| `import issues --dry-run` | 上传 attachment → `POST /api/v1/data-jobs/import` → `POST /api/v1/data-jobs/import/{id}/validate` | import-export.md;**无前缀**(同上) |
| `import issues`(执行) | `POST /api/v1/data-jobs/import/{id}/run` | 要求已 validate;**无前缀**(同上) |

### 3.2 设备码授权流程契约(auth.md 已闭环,本 Spec 仅引用)

> **服务端契约已在 auth.md 闭环(MES-76 评审 H7 收口)**:表结构与状态机见 auth.md §2.4.2(`device_authorizations`,pending→approved/denied→consumed/expired/invalidated)、端点见 auth.md §3.1.1(取码 / 轮询 / 确认页数据 / 批准 / 拒绝)、限流见 auth.md §3.6、审计动作见 auth.md §2.6、爆破防护量化验收见 auth.md §5.5(与 MES-75 安全评审 H2 合并为唯一落点)。本节仅保留 **CLI 侧流程语义**,数据模型/安全量化一律以 auth.md 为准,**两处不再各写一套**。

**CLI 流程**:
1. `POST /api/v1/auth/device/code`(公开)→ 取 `device_code`/`user_code`/`verification_uri(_complete)`/`expires_in`/`interval`;CLI 打印 URL + 码(`device_code` **绝不打印**),尝试唤起浏览器;
2. 按 `interval` 轮询 `POST /api/v1/auth/device/token`,错误信封语义(auth.md §3.5 登记):
   - `authorization_pending`(400)→ 继续轮询;
   - `slow_down`(429)→ 间隔 +5s;
   - `access_denied`(400)→ 终止(退码 2);
   - `expired_token`/`invalid_grant`(400)→ 提示重新发起(退码 2);
3. 成功 200 → 落地会话凭证(`access_token` + `refresh_token`,**前缀 `mesh_rft_`**,auth.md §2.5.1 前缀注册表);`scope` 为服务端取交后的实际签发值(请求 scope ∩ 批准用户角色权限,服务端强制);
4. **确认页为 Web 登录态页面(auth.md §3.1.1)**:手工录入 `user_code`、批准仅绑定所录码、同源 CSRF、scope 全量人类可读枚举(取交后)、**工作区显式选定**;码安全(user_code ≥20bit 去歧义字符集 / device_code ≥128bit / **HMAC-SHA256(服务端 pepper)仅存哈希,非裸 SHA-256** / TTL 15min / 单次消费 / 双重限速 / 猜错 ≤5 作废 + 审计)逐条验收见 auth.md §5.5。

### 3.3 日志流式(复用 runtime.md,不新增端点)

- 主通道(WS,Web 用):`execution:{id}:logs` 频道,首帧认证(§6.16,禁 query 传 token);
- **CLI 用 SSE 降级通道**:`GET /api/v1/workspaces/{ws}/executions/{id}/logs/stream?offset=N`(§3.1 workspace 前缀),与 WS 共用 offset 续传协议(runtime.md §3.3);帧 `{type:"log",stream,offset,line,ts}`(**`ts` 为 RFC3339 UTC 服务端收口时间**,评审 C5 / runtime.md §3.3 同步)/ `{type:"status"}` / `{type:"heartbeat",server_time}` / `{type:"end",status,final_offset}`;CLI 默认按 `ts` 在行首渲染时间戳,`--timestamps=false` 关闭(C12);
- 与 §6.8 的关系:§6.8「POST→stream_url→GET SSE」面向聊天生成;执行日志是**已存在资源的订阅**,直接 GET stream,属 §6.8 同构简化。

### 3.4 退出码 ↔ 错误信封 ↔ HTTP 三向映射(§6.14 对齐)

| 退出码 | 语义 | HTTP | 错误信封 `code`(示例) |
|--------|------|------|------------------------|
| 0 | 成功 | 2xx | —(成功包络 `{"data":...}`) |
| 1 | 通用运行时错误 | 500/502/网络/429 重试耗尽 | `internal_error` / `storage_error` / `rate_limited` |
| 2 | 鉴权失败(**专属**,不含用法错误) | 401/403 | `unauthorized` / `forbidden`(含未登录、过期/撤销、scope 不足) |
| 3 | 校验失败(**含 CLI 用法错误与 move 待确认**) | 400/404/413/415/422/**无(客户端侧)** | `validation_error` / `not_found` / `payload_too_large` / `query_cost_exceeded` / `export_too_large` / `validation_required` / `source_changed` / **`move_confirmation_required`(422,README §6.14 两步式 move 权威语义)**;**客户端侧:未知命令/未知 flag/参数非法(无 HTTP 往返,评审 M1 收口:此前误用退码 2,与鉴权专属码冲突)** |
| 4 | 冲突 | 409/423 | `conflict`(唯一约束、乐观锁、状态机冲突)/ `locked` |
| 130 | 用户中断 | —(SIGINT) | — |

> `410 gone`(激活码过期)→ 退码 3 + 提示重新创建。**`move_confirmation_required` 的权威归类为 422/退码 3**(README §6.14;评审 M1 收口:此前误列于 409/退码 4)。映射表须在 cli.md 与 README §6.14 双向一致;新增错误码同步更新本表;**本表以表驱动测试逐行断言**(§5.1)。

### 3.5 机器可读输出契约(脚本稳定性承诺)

- `--output json` 时 stdout **仅**输出单一合法 JSON 文档:成功 = REST 包络原样(`{"data":...}` / `{"data":[...],"next_cursor":...}`);失败 = REST 错误信封 `{"error":{"code","message","details"}}`;
- 进度/spinner/`--verbose` 日志/更新提示/错误说明**一律 stderr**;
- 同一 CLI 大版本内,`--output json` schema 与退出码语义保持稳定;CLI 破坏性变更走大版本 + Release Notes。

---

## 4. UI/UX 设计(终端体验)

### 4.1 渲染与分流规则

| 维度 | 规则 |
|------|------|
| **table 渲染** | 仅当 stdout 是 TTY 且 `--output table`(默认)时渲染:固定列序、表头大写、列宽自适应、超长截断;非 TTY 自动降级无装饰对齐文本;`--no-header` 供脚本 |
| **颜色** | 尊重 `NO_COLOR`;`--color=auto\|always\|never`;`--output json` 永不上色;状态字段用语义色但非唯一信息载体 |
| **TTY 检测** | stdout 非 TTY → 禁表格装饰/颜色/进度;stderr 非 TTY → spinner 降级一行式日志 |
| **进度/spinner** | export/import 轮询、device 登录轮询、下载的 spinner/进度**一律 stderr**;`--quiet`/非 TTY 降级周期一行或静默 |
| **stdout/stderr 铁律** | stdout 只放结果数据;stderr 放诊断。`mesh issue list --output json \| jq` 不被污染 |
| **交互确认** | 删除/`import run`/`logout --revoke` 默认 `[y/N]`;`--yes` 跳过;**非 TTY 未给 `--yes` → 退码 3**,不静默执行不 hang |

### 4.2 首次使用流(黄金路径)

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
```

- 自动唤起浏览器失败 → 仅打印 URL + 码,不报错;
- 无浏览器环境(SSH)→ 提示在另一设备打开(设备码流正是为受限输入设备设计);
- CI/无头 → `mesh auth login --with-token`(stdin,隐藏回显)。

**登录成功后的默认工作区(评审 R2-H1 收口,写死)**:设备码会话的工作区**在浏览器批准页已完成 0/1/多分流并显式绑定**(auth.md §3.1.1:0 个工作区无法批准、1 个自动绑定、多个必选),token 响应携带 `workspace: {id, slug}`;CLI 成功后**直接采用该批准绑定工作区为默认**(写入 config.yaml,stdout 明示「Default workspace: <slug>(批准时绑定)」),**不再按全部所属工作区二次选择**——二次选择可能选到批准绑定之外的工作区,与会话固化的 `workspace_id` 冲突。PAT 登录(`--with-token`)的默认工作区经 `mesh config set workspace <slug>` 或每命令 `--workspace <slug>` 指定;未解析工作区时执行需工作区的命令 → 退码 3 + 可操作提示(列出所属工作区)。**`--workspace` 逐命令覆盖**仍支持,但覆盖值仍受会话/令牌工作区边界约束(设备会话指名他区 → `403 forbidden`)。

### 4.3 错误可操作性(每条错误 = 发生了什么 + 下一步)

| 场景 | 输出示例(给下一步) |
|------|---------------------|
| 未登录 | `Error: not authenticated. Run \`mesh auth login\` to sign in.` |
| token 过期 | `Error: your token has expired. Run \`mesh auth login\` to re-authenticate.` |
| scope 不足 | `Error: this token lacks scope \`issue:write\`. Re-create it with the needed scope, then retry.` |
| 敏感操作需再认证(R7-H2) | `Error: this session lacks recent active authentication (reauth_required). Have the user complete reauth on the Web (POST /auth/reauth), then re-run \`mesh auth login\` to re-approve a device session inheriting the fresh authentication.`(PAT 创建/撤销、**agent 运行凭证签发**受 step-up 窗口约束,auth.md §1.1 凭证矩阵;旧 CLI 会话无法在 CLI 侧 reauth,退码 2,auth.md §1.1 恢复路径同口径) |
| 乐观锁冲突 | `Error: the issue was modified by someone else. Re-fetch with \`mesh issue get X\` and retry.` |
| 校验失败 | `Error: invalid --priority "urgent". Expected one of: none, low, medium, high.`(回显 `details`) |
| 限流 | 按 `Retry-After` 自动退避重试(stderr 提示),耗尽归退码 1 |

> 错误信息不泄漏 token/堆栈/SQL/内部 ID(§6.14);`--verbose` 输出面仅 method/path/状态码/耗时——不含请求/响应体,不含除掩码 `Authorization`(恒为 `Bearer [REDACTED]`)外的任何头;一次性凭证(激活码)只进指定 sink(0600 文件/stdin),不进任何诊断输出(C21)。

### 4.4 帮助层级

- 三层:`mesh --help`(命令族总览)→ `mesh <group> --help` → `mesh <group> <cmd> --help`(flag 详解 + 2–3 个真实示例,含 `--output json` 管道示例);
- 未知命令/flag → **退码 3(CLI 用法错误,§3.4;评审 M1:不再占用鉴权专属退码 2)** + 「Did you mean …?」最近匹配。

---

## 5. 验收标准

### 5.1 功能性

- [ ] **PAT 登录全链路**:`--with-token` stdin 登录 → 凭证 0600 落地 → `auth status` 正确展示(prefix 掩码,无明文)→ 任意工作项命令可用;`logout --revoke` 后服务端即 401。
- [ ] **设备码登录全链路**(auth.md 增量落地后):取码 → 浏览器确认页批准(**工作区在批准页绑定**)→ CLI 轮询成功 → 会话凭证落地,**默认工作区直接采用批准绑定值(无成功后二次选择,e2e 断言多工作区账号登录后 config 即批准所选工作区)**;`authorization_pending`/`slow_down`/`access_denied`/`expired_token` 四分支各有 e2e 用例;撤销 refresh 后 CLI 退出码 2。
- [ ] **工作项命令族 e2e**:§3.1 映射表每条命令真实启动服务 + 真实 API 调用 + 响应与落库校验;`issue create --description-file` 长文本含特殊字符(反引号/`$()`/引号)不被 shell 吞参。
- [ ] **乐观并发**:`issue update` 携过期 `If-Match` → 409 → 退码 4 + 可操作错误。
- [ ] **`logs --follow`**:SSE 流式跟随运行中执行,断线以 offset 重连**不丢不重**;收 `end` 自动退出;Ctrl-C 退码 130 无悬挂连接。
- [ ] **export/import**:10 万行级导出全程流式(内存不随行数增长,§10 数据作业基线);`import --dry-run` 不落库且行级错误逐行报告;未 validate 直接 run → `422 validation_required`→退码 3。
- [ ] **退出码契约(表驱动,评审 M1)**:§3.4 映射表以**单一表驱动测试**逐行断言(用例由映射表数据生成,不手写散例)——401/403→2、400/404/422(含 `move_confirmation_required`、`validation_required`)→3、409/423→4、5xx/429 耗尽→1、**未知命令/未知 flag→3(不占退码 2)**、SIGINT→130;`import completed_with_errors` 默认退码 0、`--strict` 下退码 3。
- [ ] **json 契约**:`--output json` 时 stdout 为单一合法 JSON(成功包络/错误信封),stderr 无任何污染;`| jq` 管道可用。
- [ ] **幂等**:写命令 `--idempotency-key` 重复提交返回首次结果(§6.5)。
- [ ] **补全与帮助**:`mesh completion bash/zsh/fish/powershell` 脚本可加载(评审 C7④:PowerShell 补全 e2e 在 `pwsh` 下加载无错);三层帮助含示例。
- [ ] **内联过滤(评审 C4)**:`mesh issue list --output json --jq '.[] | .identifier'` 输出逐行编号;表达式错误 → 退码 3 + stderr 定位;`--jq` 与 `--output table` 同用 → 退码 3;错误信封响应不经 `--jq`。
- [ ] **GUI 桥接(评审 C7①)**:`mesh issue get WEB-1 --web` 以默认浏览器打开规范深链 `/w/{ws}/issues/by-identifier/WEB-1`(search-command-palette.md §3.4),stdout 无数据输出;无浏览器环境 → 打印 URL、退码 0。
- [ ] **alias 别名(评审 C7③)**:config `aliases: {co: issue create}` 后 `mesh co --title X` 等价 `mesh issue create --title X`;位置参数透传;**递归 alias(`a: b`,`b: a`)不展开第二级**(单级写死断言);未知 alias → 退码 3 + did-you-mean。
- [ ] **config 面(评审建议项吸收)**:`config unset workspace` 后该键恢复默认来源;`config list --all` 每行标注来源 `default|env|file|flag`,构造同名 env + file 配置断言标注与优先级链一致;env 空串值视为未设置(落下一级)。
- [ ] **日志时间戳(评审 C5)**:`execution logs` 与 `--follow` 默认每行行首 RFC3339 时间戳(取自帧 `ts`,与 runtime.md §3.3 帧形一致);`--timestamps=false` 输出裸行(`| grep` 管道断言)。

### 5.2 性能

- [ ] `issue list --all` 翻页 1 万条无重复/遗漏(游标正确),P95 每页 < 500ms(§10 基准);
- [ ] `logs --follow` 端到端延迟 P95 ≤ 2s(与 runtime.md §3.3 日志续传基线一致);
- [ ] 导出产物 ≤ 512MB/20 万行约束在 CLI 侧正确报 `export_too_large`(import-export.md 限额)。

### 5.3 安全(红线)

- [ ] **令牌不落 argv/历史/进程表**:无 `--token` flag;`ps`/shell 历史抓包无令牌;`device_code` 不打印。
- [ ] **凭证文件 0600/父目录 0700(fail-closed)**:构造凭证文件/父目录 group/other 可读或可写 → 启动**拒绝加载**(退码 2 + 一行修复指令 `chmod 700 <dir> && chmod 600 <file>`),**不降级为告警**。
- [ ] **诊断输出面收敛**:`--verbose` 仅 method/path/状态码/耗时——不含请求/响应体、不含除掩码 `Authorization`(`Bearer [REDACTED]`)外的任何头;全通道脱敏复用 §6.16 黑名单;一次性凭证(`runtime register` 激活码)只进指定 sink(0600 文件/stdin),不进任何诊断输出。
- [ ] **撤销联动(PAT,即时)**:Web 侧撤销 PAT → CLI 下次调用**即时 401**(服务端逐请求查 `revoked_at`,auth.md §2.5)→ 清本地凭证 + 退码 2。
- [ ] **撤销联动(会话,延迟有界)**:Web 侧撤销设备码会话 → CLI 在 **≤ access TTL(15min)** 内或 refresh 被拒时 401 → 清本地凭证 + 退码 2;延迟上界 = access TTL(auth.md §3.7 权威语义:无状态短期 access JWT,窗口内已撤销 JWT 仍可通过,验收不得要求会话撤销即时生效)。
- [ ] **传输 fail-closed 与 `--insecure` 边界**:API 基址为明文 `http` 默认拒绝;`--insecure` ① **仅作单次调用 flag**(`mesh config set` 拒绝持久化该键);② 每次使用 **stderr 打一行警告**;③ `/api/v1/daemon/*` 的 TLS 强制(runtime.md §3.5 红线)**不随 `--insecure` 放宽**。
- [ ] **代理与自定义 CA(评审 C3)**:经**带认证 HTTP 代理**访问 API 成功(`HTTPS_PROXY=http://user:pass@…`,e2e 起本地代理断言 `Proxy-Authorization`);`NO_PROXY` 后缀与 CIDR 命中时绕过代理(构造命中/不命中各一例);自签 CA 经三入口任一(`--ca-cert` / config `tls.ca_cert` / `SSL_CERT_FILE`)校验通过,三入口皆无时 fail-closed 且错误含端点/代理/CA 诊断线索;**代理凭证仅 env 可设**(`mesh config set` 拒绝含 userinfo 的代理键持久化)。
- [ ] **设备码安全(服务端量化验收以 auth.md §5.5 为唯一落点,MES-75 安全 H2 合并)**:码熵/字符集、**HMAC(pepper)仅存哈希**、TTL/单次消费、双重限速、猜错作废 + 审计、确认页手工录入/CSRF/scope 取交的逐条量化断言**在 auth.md §5.5 验收**;CLI 侧 e2e 补 `authorization_pending`/`slow_down`/`access_denied`/`expired_token` 四分支与撤销 refresh 后退码 2,以及轮询遵守 `interval`/`slow_down` +5s(不在本节重复服务端量化条目,防两套漂移)。
- [ ] **导入闸门**:源附件经 attachment.md 扫描放行方可建业,CLI 不绕过。
- [ ] **无暴露外部出处**:代码/注释/帮助文本/示例不含任何竞品名称或外部出处。

### 5.4 版本、分发与 OpenAPI

- [ ] **OpenAPI 3.1 随仓库发布**:`docs/api/openapi.yaml`(FastAPI 生成 + 人工校准)覆盖 §6.14 包络/错误码/分页与各模块端点,每端点含请求/响应 schema 与错误示例;CLI e2e 含对 OpenAPI 的**契约测试**(请求构造/响应解析不漂移)。
- [ ] **内部端点暴露面(安全评审定夺:完全剔除)**:公开发布的 `docs/api/openapi.yaml` **不含** `/api/v1/daemon/*` 及内部管理端点(**整体剔除,非 `x-internal: true` 标记**——标记后 schema 仍随公开产物分发,等于泄漏内部机器接口的路径/参数/错误码全表面;daemon 协议是首方 `mesh-runtime` 二进制的契约,runtime.md 已文档化,无第三方 SDK 生成需求);**CI 断言** `docs/api/openapi.yaml` 中 `^/api/v1/daemon/` 路径**零命中**。
- [ ] **版本协商**:`mesh version --verbose` 报告 CLI 版本 + API 版本;`Deprecation`/`Sunset` 响应头触发 stderr 升级提示。
- [ ] **分发**:单一静态二进制(多平台多架构)经 Releases 发布,**附 SHA-256 校验和与签名**(公钥随产品发布,与 runtime.md 安装包同基线);安装脚本可审阅、不鼓励盲管道。
- [ ] **前向兼容**:CLI 解析 JSON 容忍未知字段(旧客户端忽略新字段,§11.2)。
