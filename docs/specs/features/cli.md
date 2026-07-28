# 开发者平台 CLI(`mesh` 命令行工具)功能 Spec

> **所属层**:平台能力层 / 开发者平台(README §11 开发者平台契约的详 Spec)。
> **依赖的其他 Spec**:
> - `auth.md`(§2.5 `api_tokens` / §3.1 会话与 OAuth / §3.2 token 端点 / §3.7 撤销联动):CLI 鉴权的唯一服务端依赖;PAT 路径零增量,**设备码授权为 auth.md 增量**(§3.2,本 Spec 定义流程契约,端点/表/限流/审计在 auth.md 落地)。
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
| C5 | 令牌刷新与过期 | 会话 401 → 静默 refresh 轮换重写本地;refresh 失效 → 退出码 2 + 提示 `mesh auth login`;PAT 过期即 401→2。**过期绝不报错为通用错误** | 长期脚本 |
| C6 | `config set/get` | API 基址 / 默认工作区 / 输出格式;优先级链 **flag > env(`MESH_*`) > 配置文件 > 默认** | 多环境切换 |

**资源命令族**(与 REST 端点一一对应,§3.1 映射表)

| # | 命令 | 说明 |
|---|------|------|
| C7 | `mesh issue list/get/create/update/status/comment/children/dependencies` | issue.md §3 端点;`create --title --description-file <path> --priority --assignee --project`;长文本一律 `--description-file`/`--content-file`(§11.1 硬约定);`update` 带 `If-Match` 乐观并发(409→退码 4) |
| C8 | `mesh project list/get/create` | project.md §3 |
| C9 | `mesh member list` | member.md 名册 |
| C10 | `mesh agent list/executions` | agent.md 名册与运行历史 |
| C11 | `mesh execution get/logs/cancel` | runtime.md §3.1;`logs --follow` 见 C12 |
| C12 | `mesh execution logs --follow` | **SSE 降级通道** `GET /executions/{id}/logs/stream?offset=N`(runtime.md §3.3);offset 续传去重(不丢/不重/单调);帧 `log`/`status`/`heartbeat`/`end`,收 `end` 退出;Ctrl-C 退码 130 不留悬挂连接 |
| C13 | `mesh export issues --project <key> --format csv\|json -o <file>` | `POST /data-jobs/export` → 轮询至 `completed` → 签名下载 URL **流式**写文件;进度走 stderr |
| C14 | `mesh import issues --file <path> [--dry-run]` | 上传源附件 → 建作业 → `--dry-run` 仅 validate(打印映射预览 + 逐行错误,退码 0);去 `--dry-run` 执行 run(要求已 validate,否则 `422 validation_required`→3);`completed_with_errors` 下载错误报告 + stderr 警告(退码 0,部分成功语义) |
| C15 | `mesh runtime register` | **控制台侧**建影子记录(`POST /api/v1/runtimes`),返回一次性激活码 + 安装命令;激活码经 0600 文件/stdin 交给独立二进制 `mesh-runtime activate`(runtime.md),**不进命令行参数** |

**通用契约**

| # | 功能点 | 说明 |
|---|--------|------|
| C16 | `--workspace <slug>` | 覆盖默认工作区;所有需工作区的命令支持 |
| C17 | `--idempotency-key` | 透传 `Idempotency-Key`(§6.5/§6.14);**所有写命令**支持 |
| C18 | `--output table\|json` | json 与 REST 包络一致;**stdout 只放结果数据**,进度/spinner/日志/错误一律 stderr |
| C19 | 退出码分类 | `0` 成功 / `1` 通用(5xx/网络/429 重试耗尽)/ `2` 鉴权(401/403/未登录/过期)/ `3` 校验(400/404/413/422)/ `4` 冲突(409/423);`130` SIGINT |
| C20 | 游标翻页 | 列表命令统一 `--limit` + 自动跟随 `next_cursor` 的 `--all`(§6.14) |
| C21 | `--verbose`/`--quiet`/`--yes` | verbose 输出面收敛为**仅 method/path/状态码/耗时**(stderr):**显式不含请求/响应体、不含除掩码 `Authorization`(`Bearer [REDACTED]`)外的任何头**;一次性凭证(`runtime register` 激活码)只写指定 sink(0600 文件/stdin),不进任何诊断输出;破坏性操作默认交互确认,`--yes` 跳过,**非 TTY 未给 `--yes` 报错退出** |
| C22 | `mesh completion <shell>` | bash/zsh/fish 静态补全脚本(命令/flag) |
| C23 | `mesh version` | CLI 版本 + 目标 API 版本(v1);探测到 `Deprecation`/`Sunset` 响应头 → stderr 提示升级 |

### 1.3 `mesh runtime` 与 `mesh-runtime` daemon 的职责收口

README §11.1 的 `mesh runtime register/heartbeat` 与 runtime.md 的机器接口(独立二进制 `mesh-runtime` + `/api/v1/daemon/*` 命名空间)形态不同,本 Spec 写死切分:

- `mesh runtime register` = **控制台侧**建影子记录并引导安装 daemon(§1.2 C15);
- `mesh runtime heartbeat` = **仅供人工排障/演示**的代理心跳(经用户凭证调 daemon 端点),**不得**替代 daemon 心跳;
- 真实守护进程的注册激活/心跳/领取/上报一律由 `mesh-runtime` 以 `mesh_rt_` 令牌走 daemon 命名空间(runtime.md §3.5),**两个鉴权域不混用**。

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

> **通用 Bearer-PAT 鉴权依赖(现状缺口)**:当前常规 REST 路由的鉴权依赖仅认会话 JWT,PAT 解析(`resolve_pat`)仅用于个别端点。CLI 要求「持 PAT 调用任意 `/api/v1` 端点」,故 **auth.md 需将 PAT 解析并入统一 Bearer 依赖**(前缀路由:JWT / `mesh_pat_` / `mesh_agt_` 各走校验链,权限取 scopes∩角色)。该增量随 CLI 开发 Issue 在 auth.md 落地。

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
- 写入经「临时文件 → fsync → 原子 rename」,避免半写;
- `MESH_TOKEN` 环境变量优先级最高(CI),其存在时不读凭证文件该 host;
- 凭证后端抽象为 credential store,本期实现文件后端,预留 keychain(N5)。

---

## 3. 接口设计

> CLI 的一切能力经 `/api/v1` REST;包络/错误/分页/幂等以 §6.14/§6.5 为权威。

### 3.1 命令 → REST 端点映射表

| 命令 | 方法 + 端点 | 来源 / 备注 |
|------|-------------|-------------|
| `auth login`(PAT) | 探活 `GET /api/v1/me` | auth.md §3.1;凭证经 stdin/文件 |
| `auth login`(设备码) | `POST /api/v1/auth/device/code` → 轮询 `POST /api/v1/auth/device/token` | **auth.md 增量**(§3.2) |
| `auth logout`(会话) | `POST /api/v1/auth/logout` | auth.md §3.1 |
| `auth logout --revoke`(PAT) | `DELETE /api/v1/workspaces/{ws}/api-tokens/{id}` | auth.md §3.2 |
| `auth status` | `GET /api/v1/me` + 本地凭证元数据 | 不回显明文 |
| `config set/get` | 纯本地文件 | — |
| `issue list` | `GET /api/v1/issues?cursor&limit&filters` | issue.md §3.2 |
| `issue get` | `GET /api/v1/issues/{id}` | issue.md |
| `issue create` | `POST /api/v1/issues`(+`Idempotency-Key`) | `--description-file` |
| `issue update` | `PATCH /api/v1/issues/{id}`(+`If-Match`) | 409→退码 4 |
| `issue status` | issue.md 状态流转端点 | 422→退码 3 |
| `issue comment` | comment 创建端点 | comment-inbox.md;`--content-file` |
| `issue children` / `dependencies` | 子项/依赖查询端点 | §11.1 |
| `project list/get/create` | project.md §3 | create 带幂等键 |
| `member list` | member.md 名册端点 | — |
| `agent list` / `executions` | agent.md 名册/运行历史端点 | — |
| `runtime register` | `POST /api/v1/runtimes` | runtime.md §3.1 控制台侧 |
| `runtime heartbeat`(排障用) | `POST /api/v1/daemon/runtimes/{id}:heartbeat` | §1.3 收口 |
| `execution get` | `GET /api/v1/executions/{id}` | runtime.md §3.1 |
| `execution logs` | `GET /api/v1/executions/{id}/logs?offset=N&stream=` | REST 拉历史 |
| `execution logs --follow` | `GET /api/v1/executions/{id}/logs/stream?offset=N`(SSE) | runtime.md §3.3 |
| `execution cancel` | `POST /api/v1/executions/{id}:cancel` | runtime.md §3.1 |
| `export issues` | `POST /data-jobs/export` → `GET /data-jobs/{id}` → `GET /data-jobs/{id}/download` | import-export.md;流式落盘 |
| `import issues --dry-run` | 上传 attachment → `POST /data-jobs/import` → `POST /data-jobs/import/{id}/validate` | import-export.md |
| `import issues`(执行) | `POST /data-jobs/import/{id}/run` | 要求已 validate |

### 3.2 设备码授权流程契约(auth.md 增量,本 Spec 定义流程)

> auth.md 现状仅有授权码 + PKCE(浏览器第三方登录)与 PAT 端点,无设备码端点。以下为**流程权威定义**,端点实现/数据模型/限流/审计登记由 auth.md 同步落地(开发阶段以 auth.md 增量 Issue 承载),本 Spec 仅引用。

**`POST /api/v1/auth/device/code`**(公开,登录类限流):
- 请求:`{client_id: "mesh-cli", scope: "<space-joined>"}`;
- 响应 200:`{data: {device_code, user_code, verification_uri, verification_uri_complete, expires_in(默认 900), interval(默认 5)}}`;
- 落库:设备码授权记录——仅存 `device_code_hash`/`user_code_hash`(SHA-256)、TTL、`status=pending`、请求 scope、`consumed_at`(单次消费),形态参照 auth.md 既有一次性令牌表(`password_reset_tokens` 等);
- **码生成要求(量化,可验收)**:`user_code` 熵 **≥20bit**(RFC 8628 §6.1 基线)且采用**去歧义字符集**(剔除 `0/O/1/I/L` 等易混字符,分组展示如 `XXXX-XXXX`);`device_code` 熵 **≥128bit**(密码学安全随机源)。

**`POST /api/v1/auth/device/token`**(公开,**量化爆破防护**):
- 请求:`{grant_type: "urn:ietf:params:oauth:grant-type:device_code", device_code, client_id}`;
- **爆破防护(量化,§5.3 逐条验收)**——`user_code` 是短码,爆破成功即直接领取受害者已批准的会话令牌(RFC 8628 核心威胁面):
  - 轮询端点**双重限速**:按来源 IP 全局限速 + 按 `device_code` 限速;违规返回 `slow_down`,累计违规超限即**拒绝该码**;
  - 单码**连续猜错上限 ≤5 次** → 立即作废该授权记录(`status=invalidated`)+ **审计留痕**;
  - `device_code` 命中后须比对 **`status=pending` 且未过期未消费**方可推进(已消费/已作废/过期一律拒绝);
- 轮询语义(以 §6.14 错误信封表达,非裸 OAuth 错误体):
  - `authorization_pending`(具名 code,400 携带)→ 继续轮询;
  - `slow_down` → 间隔 +5s;
  - `access_denied` → 终止(退码 2);
  - `expired_token` → 重新发起;
- 成功 200:`{data: {access_token, refresh_token, token_type: "Bearer", expires_in, scope}}`,**同事务**置 `consumed_at` 并创建 `sessions` 行(复用会话撤销链路 auth.md §3.7 `session.revoked`)。
- **授权确认页**(auth.md UI 增量,Web 登录态页面,防 RFC 8628 §5.5 钓鱼家族——攻击者诱使受害者浏览器提交对攻击者 `device_code` 的批准):用户须**手工录入 `user_code`** 且**批准仅绑定所录入的码**;approve 请求带**同源 CSRF 防护**;scope **全量人类可读枚举**,显式确认后方可批准。
- **签发 scope 取交**(与 PAT「创建时与角色权限取交」规则同源,auth.md §2.5):**签发 scope = 请求 scope ∩ 批准用户角色权限,服务端强制**(token 端点兜底重算,不按请求原样签发——否则可能超出批准者角色权限);确认页展示**取交后**的 scope,避免展示与签发的同意 mismatch;token 响应 `scope` 为实际签发值。

### 3.3 日志流式(复用 runtime.md,不新增端点)

- 主通道(WS,Web 用):`execution:{id}:logs` 频道,首帧认证(§6.16,禁 query 传 token);
- **CLI 用 SSE 降级通道**:`GET /api/v1/executions/{id}/logs/stream?offset=N`,与 WS 共用 offset 续传协议(runtime.md §3.3);帧 `{type:"log",stream,offset,line}` / `{type:"status"}` / `{type:"heartbeat"}` / `{type:"end",status,final_offset}`;
- 与 §6.8 的关系:§6.8「POST→stream_url→GET SSE」面向聊天生成;执行日志是**已存在资源的订阅**,直接 GET stream,属 §6.8 同构简化。

### 3.4 退出码 ↔ 错误信封 ↔ HTTP 三向映射(§6.14 对齐)

| 退出码 | 语义 | HTTP | 错误信封 `code`(示例) |
|--------|------|------|------------------------|
| 0 | 成功 | 2xx | —(成功包络 `{"data":...}`) |
| 1 | 通用运行时错误 | 500/502/网络/429 重试耗尽 | `internal_error` / `storage_error` / `rate_limited` |
| 2 | 鉴权失败 | 401/403 | `unauthorized` / `forbidden`(含未登录、过期/撤销、scope 不足) |
| 3 | 校验失败 | 400/404/413/415/422 | `validation_error` / `not_found` / `payload_too_large` / `query_cost_exceeded` / `export_too_large` / `validation_required` / `source_changed` |
| 4 | 冲突 | 409/423 | `conflict`(唯一约束、乐观锁、状态机冲突、`move_confirmation_required`)/ `locked` |
| 130 | 用户中断 | —(SIGINT) | — |

> `410 gone`(激活码过期)→ 退码 3 + 提示重新创建。映射表须在 cli.md 与 README §6.14 双向一致;新增错误码同步更新本表。

### 3.5 机器可读输出契约(脚本稳定性承诺)

- `--output json` 时 stdout **仅**输出单一合法 JSON 文档:成功 = REST 包络原样(`{"data":...}` / `{"data":[...],"next_cursor":...}`);失败 = REST 错误信封 `{"error":{"code","message","details"}}`;
- 进度/spinner/`--verbose` 日志/更新提示/错误说明**一律 stderr**;
- 同一 CLI 大版本内,`--output json` schema 与退出码语义保持稳定;CLI 破坏性变更走大版本 + CHANGELOG。

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

### 4.3 错误可操作性(每条错误 = 发生了什么 + 下一步)

| 场景 | 输出示例(给下一步) |
|------|---------------------|
| 未登录 | `Error: not authenticated. Run \`mesh auth login\` to sign in.` |
| token 过期 | `Error: your token has expired. Run \`mesh auth login\` to re-authenticate.` |
| scope 不足 | `Error: this token lacks scope \`issue:write\`. Re-create it with the needed scope, then retry.` |
| 乐观锁冲突 | `Error: the issue was modified by someone else. Re-fetch with \`mesh issue get X\` and retry.` |
| 校验失败 | `Error: invalid --priority "urgent". Expected one of: none, low, medium, high.`(回显 `details`) |
| 限流 | 按 `Retry-After` 自动退避重试(stderr 提示),耗尽归退码 1 |

> 错误信息不泄漏 token/堆栈/SQL/内部 ID(§6.14);`--verbose` 输出面仅 method/path/状态码/耗时——不含请求/响应体,不含除掩码 `Authorization`(恒为 `Bearer [REDACTED]`)外的任何头;一次性凭证(激活码)只进指定 sink(0600 文件/stdin),不进任何诊断输出(C21)。

### 4.4 帮助层级

- 三层:`mesh --help`(命令族总览)→ `mesh <group> --help` → `mesh <group> <cmd> --help`(flag 详解 + 2–3 个真实示例,含 `--output json` 管道示例);
- 未知命令/flag → 退码 2 + 「Did you mean …?」最近匹配。

---

## 5. 验收标准

### 5.1 功能性

- [ ] **PAT 登录全链路**:`--with-token` stdin 登录 → 凭证 0600 落地 → `auth status` 正确展示(prefix 掩码,无明文)→ 任意工作项命令可用;`logout --revoke` 后服务端即 401。
- [ ] **设备码登录全链路**(auth.md 增量落地后):取码 → 浏览器确认页批准 → CLI 轮询成功 → 会话凭证落地;`authorization_pending`/`slow_down`/`access_denied`/`expired_token` 四分支各有 e2e 用例;撤销 refresh 后 CLI 退出码 2。
- [ ] **工作项命令族 e2e**:§3.1 映射表每条命令真实启动服务 + 真实 API 调用 + 响应与落库校验;`issue create --description-file` 长文本含特殊字符(反引号/`$()`/引号)不被 shell 吞参。
- [ ] **乐观并发**:`issue update` 携过期 `If-Match` → 409 → 退码 4 + 可操作错误。
- [ ] **`logs --follow`**:SSE 流式跟随运行中执行,断线以 offset 重连**不丢不重**;收 `end` 自动退出;Ctrl-C 退码 130 无悬挂连接。
- [ ] **export/import**:10 万行级导出全程流式(内存不随行数增长,§10 数据作业基线);`import --dry-run` 不落库且行级错误逐行报告;未 validate 直接 run → `422 validation_required`→退码 3。
- [ ] **退出码契约**:§3.4 映射表每条至少一个用例(401/403→2、400/422→3、409→4、5xx→1);SIGINT→130。
- [ ] **json 契约**:`--output json` 时 stdout 为单一合法 JSON(成功包络/错误信封),stderr 无任何污染;`| jq` 管道可用。
- [ ] **幂等**:写命令 `--idempotency-key` 重复提交返回首次结果(§6.5)。
- [ ] **补全与帮助**:`mesh completion bash/zsh/fish` 脚本可加载;三层帮助含示例。

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
- [ ] **设备码安全**(auth.md 增量验收,逐条量化):
  - `user_code` 熵 ≥20bit + 去歧义字符集(剔除 0/O/1/I/L);`device_code` 熵 ≥128bit;
  - `device_code`/`user_code` 仅存哈希 + TTL 15min + 单次消费;
  - 轮询端点双重限速(按 IP 全局 + 按 `device_code`),`slow_down` 累计违规超限即拒绝该码;
  - 单码连续猜错 ≤5 次 → 立即作废该授权码 + 审计留痕(e2e 触发并核验作废与审计行);
  - `device_code` 命中后比对 `status=pending` 且未过期未消费方可推进;
  - 确认页须用户手工录入 `user_code` 且批准仅绑定所录码,approve 带同源 CSRF 防护,scope 全量人类可读枚举、显式确认后方可批准(构造跨码 CSRF 攻击用例被拒);
  - 签发 scope = 请求 scope ∩ 批准用户角色权限(服务端强制,验收构造越权 scope 请求被收窄),确认页展示取交后 scope。
- [ ] **导入闸门**:源附件经 attachment.md 扫描放行方可建业,CLI 不绕过。
- [ ] **无暴露外部出处**:代码/注释/帮助文本/示例不含任何竞品名称或外部出处。

### 5.4 版本、分发与 OpenAPI

- [ ] **OpenAPI 3.1 随仓库发布**:`docs/api/openapi.yaml`(FastAPI 生成 + 人工校准)覆盖 §6.14 包络/错误码/分页与各模块端点,每端点含请求/响应 schema 与错误示例;CLI e2e 含对 OpenAPI 的**契约测试**(请求构造/响应解析不漂移)。
- [ ] **内部端点暴露面(安全评审定夺:完全剔除)**:公开发布的 `docs/api/openapi.yaml` **不含** `/api/v1/daemon/*` 及内部管理端点(**整体剔除,非 `x-internal: true` 标记**——标记后 schema 仍随公开产物分发,等于泄漏内部机器接口的路径/参数/错误码全表面;daemon 协议是首方 `mesh-runtime` 二进制的契约,runtime.md 已文档化,无第三方 SDK 生成需求);**CI 断言** `docs/api/openapi.yaml` 中 `^/api/v1/daemon/` 路径**零命中**。
- [ ] **版本协商**:`mesh version --verbose` 报告 CLI 版本 + API 版本;`Deprecation`/`Sunset` 响应头触发 stderr 升级提示。
- [ ] **分发**:单一静态二进制(多平台多架构)经 Releases 发布,**附 SHA-256 校验和与签名**(公钥随产品发布,与 runtime.md 安装包同基线);安装脚本可审阅、不鼓励盲管道。
- [ ] **前向兼容**:CLI 解析 JSON 容忍未知字段(旧客户端忽略新字段,§11.2)。
