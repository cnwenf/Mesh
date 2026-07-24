# 附件(Attachment)功能 Spec

| 项目 | 内容 |
|------|------|
| 所属层 | 协作层 / 基础能力(Collaboration & Foundation) |
| 模块 | attachment |
| 依赖 Spec | `workspace`(多租户、配额)、`member`(统一 members.id,human\|agent)、`auth`(Bearer/RBAC/限流)、`issue`(关联宿主)、`comment-inbox`(评论内附件引用 `attachment_ids`) |
| 被依赖 | `comment-inbox`(评论载荷引用附件)、`agent`(agent 运行产出物经 API token 上传) |
| 技术栈 | FastAPI + SQLAlchemy 2.x + PostgreSQL + 对象存储(私有桶 + 预签名 URL) |
| 状态 | Draft |

> **全局一致性锚点(本 Spec 全程遵循)**
> 1. PostgreSQL;表名 snake_case 复数;主键 `uuid`(默认 `gen_random_uuid()`);`created_at`/`updated_at` 为 `TIMESTAMPTZ NOT NULL DEFAULT now()`;软删除统一 `deleted_at TIMESTAMPTZ NULL`。
> 2. 附件 `uploader_id` 引用**统一 `members.id`**(人类与 agent 同表,以 `members.member_type ∈ {human, agent}` 区分);另存冗余 `uploader_kind` 便于免 JOIN 渲染。
> 3. REST 前缀 `/api/v1`;`Authorization: Bearer <token>`;游标分页响应统一 `{"data": [...], "next_cursor": "...", "has_more": bool}`;统一错误信封 `{"error": {"code","message","details"}}`。
> 4. 实时走 WebSocket `/ws`,事件名 `<entity>.<action>`,带 `seq` 重放;附件处理结果以 `attachment.processed` 事件下发。
> 5. ORM 采用 SQLAlchemy 2.x 声明式约定(`Mapped` / `mapped_column`)。

> **核心设计(必须采纳)**
> - **三阶段直传**:申请签名 URL → 客户端直传对象存储 → 回调 `complete`;字节流**不经过应用服务器**。
> - **两阶段状态机**:`upload_status: pending → completed`(另有 `uploading`/`failed`/`expired`),配合 `expires_at` 清理「传了没确认」的孤儿对象。
> - **MIME/大小以服务端嗅探为准**:客户端声明仅作预校验,安全判定一律服务端 + 对象存储探测。
> - **私有对象 + 短时效签名下载**:桶私有,任何访问经签名 URL 或后端代理;签名时效 60s 量级、绑定方法与对象键。
> - **agent 与人类共用模型**:仅以 `uploader_kind`(镜像 `members.member_type`)区分,agent runtime 用 API token 走同一套接口。

---

## 1. 功能描述

### 1.1 定位

附件(Attachment)为 issue 与 comment 提供文件承载:图片内联预览、文件卡片下载、agent 运行产出物(截图/报告/日志)回流。采用「客户端经预签名 URL 直传对象存储」的规模化方案——应用服务器只负责签发 URL、记账与鉴权,字节流走对象存储,从而降低后端带宽与延迟,并支撑大文件分块/断点续传。

### 1.2 功能点与场景

| # | 功能点 | 典型场景 |
|---|--------|----------|
| A1 | 经签名 URL 直传对象存储 | 浏览器/CLI 拿预签名 PUT URL 直传,大文件不经应用服务器 |
| A2 | 分块上传(multipart) | 大文件(>数百 MB)切片并发上传、断点续传 |
| A3 | 元数据登记(complete) | 直传成功后回调服务端确认,登记文件名/大小/MIME/上传者,生成附件记录 |
| A4 | 图片预览 / 缩略图 | 图片在评论/issue 内联预览;异步生成多尺寸缩略图(sm/md/lg) |
| A5 | 文件类型图标 | 非图片按真实 MIME 显示图标(PDF/压缩包/表格/代码等) |
| A6 | 附件与 issue 关联 | issue 详情「附件」区集中展示该 issue 全部文件 |
| A7 | 附件与 comment 关联 | 评论内嵌附件(图片内联、文件卡片) |
| A8 | 下载鉴权 | 下载走短时效签名 URL,或经后端代理校验权限 |
| A9 | 大小与类型限制 | 单文件上限、MIME/扩展名白名单、workspace/用户配额 |
| A10 | 删除附件 | 作者或管理员软删除;对象异步延迟回收 |
| A11 | 病毒/恶意扫描(可选增强) | 上传后异步扫描,命中标记/隔离/禁下载 |
| A12 | 内容哈希去重(可选) | 按 SHA-256 去重存储 + 完整性校验;命中走「秒传」 |
| A13 | 附件搜索(可选) | 按文件名搜索某 issue/workspace 内附件 |
| A14 | agent 产出物作为附件(核心差异) | agent 运行产出的截图/报告/日志由 runtime 经 API token 上传并挂到 issue/comment |
| A15 | 图片懒加载与灯箱 | 列表用缩略图,点开灯箱加载原图,支持缩放/旋转 |
| A16 | 拖拽 / 粘贴上传 | 在 composer 拖入文件或粘贴截图即触发上传 |

### 1.3 边界与非目标

**范围内:**
- 上传申请/直传/完成/取消的状态机与签名签发;元数据登记;缩略图与扫描的异步触发;下载鉴权与签名;软删除与延迟回收;与 issue/comment 的多对多关联;配额与类型限制校验。

**非目标(本 Spec 不覆盖):**
- 对象存储自身的部署/运维(基础设施范畴;本 Spec 以中性「对象存储」抽象,`storage_provider` 标识,不绑定具体厂商)。
- 评论/issue 本身的读写(见 `comment-inbox`/`issue` Spec);本 Spec 仅提供 `attachment_ids` 与关联表。
- 文档全文检索、OCR、在线编辑预览(列为后续增强)。
- 客户端文件选择器/灯箱组件的内部实现(前端组件库范畴)。

---

## 2. 数据模型

### 2.1 ER 关系

```
workspaces 1─* attachments 1─* attachment_links *─1 (issues | comments)
attachments 1─1 upload_sessions (可选,分块上传台账)
workspaces 1─1 attachment_quotas (可选,配额)

attachments.uploader_id → members.id (member_type ∈ {human, agent})
```

### 2.2 `attachments` — 附件主表

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 附件 ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离 |
| `uploader_id` | uuid | NOT NULL, FK→members.id | 上传者(人类/agent,源为 `members.member_type`) |
| `uploader_kind` | text | NOT NULL, CHECK in ('human','agent') | 冗余镜像 `members.member_type`,免 JOIN 渲染(核心差异:agent 与人类共用模型,仅以此区分) |
| `file_name` | text | NOT NULL | 原始文件名(含扩展名) |
| `file_size` | bigint | NOT NULL, CHECK (file_size > 0) | 字节数(以 complete 时对象存储探测为准) |
| `mime_type` | text | NOT NULL | **服务端嗅探/对象存储探测**的真实 MIME,非客户端声明 |
| `extension` | text | NULL | 归一化扩展名 |
| `content_hash` | text | NULL | SHA-256(去重 + 完整性校验) |
| `storage_provider` | text | NOT NULL DEFAULT 's3' | 对象存储提供商标识(中性命名,不绑定厂商) |
| `storage_bucket` | text | NOT NULL | 桶名 |
| `storage_key` | text | NOT NULL | 对象键(`ws/<workspace_id>/<uuid>/<sanitized-name>`,含 UUID 段不可枚举) |
| `upload_status` | text | NOT NULL DEFAULT 'pending', CHECK in ('pending','uploading','completed','failed','expired') | 直传两阶段状态机 |
| `scan_status` | text | NOT NULL DEFAULT 'pending', CHECK in ('pending','clean','infected','error','skipped') | 扫描状态(可选) |
| `is_image` | boolean | NOT NULL DEFAULT false | 冗余,加速渲染分支 |
| `image_width` | int | NULL | 图片宽(像素) |
| `image_height` | int | NULL | 图片高 |
| `thumbnail_keys` | jsonb | NULL | 缩略图对象键映射 `{"sm":"...","md":"...","lg":"..."}` |
| `expires_at` | timestamptz | NULL | 未完成记录的过期清理时间(孤儿对象回收依据) |
| `deleted_at` | timestamptz | NULL | 软删除 |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**状态机(必须实现):**
```
pending ──(客户端开始直传)──> uploading ──(complete 校验通过)──> completed
   │                            │
   └──(超时未确认 expires_at)──> expired(后台清理任务置位并删对象)
                                └──(complete 校验失败 / abort)──> failed
```
- `complete` 仅允许从 `pending`/`uploading` 进入 `completed`;对已 `completed` 再 `complete` 返回 `CONFLICT`。

**关键索引:**
- `idx_attachments_uploader (workspace_id, uploader_id, created_at)`。
- `idx_attachments_hash (workspace_id, content_hash)` — 去重/秒传查询。
- 部分索引 `idx_attachments_pending ON attachments(expires_at) WHERE upload_status <> 'completed'` — 清理任务扫描未完成上传。
- 部分索引 `idx_attachments_active ON attachments(workspace_id, created_at) WHERE deleted_at IS NULL`。

### 2.3 `attachment_links` — 附件与实体的关联(多对多 / 多态)

> 一个附件可被多个 issue/comment 引用(转发、复制评论),故用关联表而非在附件表写死外键。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | |
| `attachment_id` | uuid | NOT NULL, FK→attachments.id | |
| `linked_type` | text | NOT NULL, CHECK in ('issue','comment') | 关联实体类型 |
| `linked_id` | uuid | NOT NULL | 关联实体 ID |
| `display` | text | NOT NULL DEFAULT 'card', CHECK in ('inline','card') | 图片内联 / 文件卡片 |
| `position` | int | NOT NULL DEFAULT 0 | 排序 |
| `created_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_attachment_link (attachment_id, linked_type, linked_id)`。
**关键索引:** `idx_links_target (workspace_id, linked_type, linked_id, position)` — 拉取某 issue/comment 的附件。

### 2.4 `upload_sessions` — 分块上传台账(可选)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `attachment_id` | uuid | NOT NULL, FK→attachments.id | |
| `upload_id` | text | NOT NULL | 对象存储侧 multipart upload id |
| `part_size` | int | NOT NULL | 分片大小(字节) |
| `parts` | jsonb | NOT NULL DEFAULT '[]' | 各分片状态 `[{part_number, etag, uploaded}]` |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

> 单文件直传仅用 `attachments.upload_status`;分块/断点续传场景加这张表跟踪。

### 2.5 `attachment_quotas` — 配额(可选)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `workspace_id` | uuid | PK, FK→workspaces.id | |
| `max_file_bytes` | bigint | NOT NULL | 单文件上限 |
| `total_bytes` | bigint | NOT NULL | workspace 总配额 |
| `used_bytes` | bigint | NOT NULL DEFAULT 0 | 已用(由附件表聚合维护) |
| `allowed_mimes` | jsonb | NULL | MIME 白名单(NULL=用默认) |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

### 2.6 跨模块外键说明

- `uploader_id` → `members.id`(member Spec);`uploader_kind` 在 `complete` 落库时由服务端读 `members.member_type` 填充,作为渲染快照(真源仍是 members)。
- `workspace_id` → `workspaces.id`(workspace Spec);`attachment_links.linked_id` 逻辑指向 `issues.id`/`comments.id`(以 `linked_type` 区分;跨模块逻辑外键,不建物理 FK 以避免多态约束复杂度,删除一致性由应用层 + 软删除保证)。

---

## 3. 接口设计

> 鉴权:`Authorization: Bearer <token>`(成员会话或 agent runtime API token)。
> 直传核心:**「申请签名 URL → 客户端直传对象存储 → 回调 complete」** 三阶段,字节流不经应用服务器。

### 3.0 分页与鉴权约定

- **游标分页**:列表端点(`GET /issues/{id}/attachments`、`GET /comments/{id}/attachments`)统一返回 `{"data": [...], "next_cursor": "...", "has_more": bool}`;游标为不透明字符串(内部基于 `position + id` 的 keyset)。
- **鉴权**:读附件需对宿主 issue/comment 有读权限;写(申请上传/删除)需对应写权限;agent runtime 用 API token,权限以其所属 agent 身份校验。
- **幂等**:`complete`/`abort` 以 `upload_status` 状态机做并发保护;同一 `upload-request` 可携带客户端生成的幂等键避免重复建记录(可选)。
- **速率限制**:`upload-requests` 与 `download` 按用户/IP 限流(见 auth Spec),触发返回 `RATE_LIMITED` 含 `Retry-After`。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/attachments/upload-requests` | 申请上传:校验类型/大小/配额,建 `pending` 记录,返回签名 PUT URL(或一组分片签名 URL) |
| POST | `/api/v1/attachments/{id}/complete` | 直传成功后回调:校验对象存在与大小、嗅探 MIME、触发缩略图/扫描,置 `completed`(**仅上传申请者本人可操作**,服务端校验 `uploader_id` = 当前 principal) |
| POST | `/api/v1/attachments/{id}/abort` | 取消上传,清理对象(**仅上传申请者本人可操作**) |
| GET | `/api/v1/attachments/{id}` | 取附件元数据 |
| DELETE | `/api/v1/attachments/{id}` | 软删除附件 |
| GET | `/api/v1/attachments/{id}/download` | 获取**短时效签名下载 URL**,或 302 重定向;亦可后端代理流式下载 |
| GET | `/api/v1/attachments/{id}/thumbnail?size=sm` | 获取缩略图签名 URL(图片) |
| GET | `/api/v1/issues/{issue_id}/attachments` | 列出某 issue 的附件 |
| GET | `/api/v1/comments/{comment_id}/attachments` | 列出某 comment 的附件 |
| POST | `/api/v1/multipart/{id}/parts` | (分块)申请下一批分片签名 URL |
| POST | `/api/v1/multipart/{id}/complete` | (分块)合并分片并完成 |

### 3.2 申请上传(upload-request)

**请求体:**
```json
{
  "file_name": "screenshot.png",
  "file_size": 245760,
  "mime_type": "image/png",
  "content_hash": "9f86d0...",
  "link_to": {"type": "comment", "id": "c-abc"}
}
```

**响应体(201):**
```json
{
  "data": {
    "id": "att-1",
    "upload_status": "pending",
    "upload": {
      "method": "PUT",
      "url": "https://<object-storage>/<bucket>/<key>?<signature>&expires=...",
      "headers": {"Content-Type": "image/png", "x-content-sha256": "9f86d0..."},
      "expires_at": "2026-07-24T10:15:00Z"
    },
    "limits": {"max_file_bytes": 104857600}
  }
}
```
> 分块场景:`upload` 改为 `{ "upload_id": "...", "part_urls": [{"part_number":1,"url":"..."}, ...] }`。

**服务端签发前校验(必须实现):**
- 扩展名/MIME 是否在允许清单;MIME 与扩展名是否匹配(防伪造)。
- `file_size` 是否超单文件上限与 workspace 剩余配额(配额前置校验,避免传完才发现超限浪费带宽)。
- `content_hash` 命中已有附件 → 「秒传」(直接复用对象、建关联,返回 `upload_status='completed'`)。
- 校验调用者对 `link_to` 目标有写权限。
- 写 `pending` 记录并设 `expires_at`(默认 15 分钟)。

### 3.3 完成上传(complete)

**请求体:**
```json
{"file_size": 245760, "content_hash": "9f86d0...", "parts": [{"part_number":1, "etag":"..."}]}
```

**服务端动作(必须实现,以对象存储为准,不信客户端):**
1. 向对象存储 HEAD 对象,确认存在且大小一致。
2. 服务端**嗅探真实 MIME**(不信客户端 `Content-Type`),写回 `mime_type`/`extension`/`is_image`;读 `members.member_type` 填 `uploader_kind`。
3. 校验 `content_hash`(若提供)匹配,不匹配返回 `HASH_MISMATCH`。
4. 图片:异步生成缩略图(sm/md/lg)写入 `thumbnail_keys`。
5. 触发病毒扫描(异步,`scan_status`)。
6. `upload_status='completed'`,清空 `expires_at`;若带 `link_to`,建立 `attachment_links`。

**响应体(200):**
```json
{
  "data": {
    "id": "att-1",
    "upload_status": "completed",
    "mime_type": "image/png",
    "is_image": true,
    "image_width": 1280, "image_height": 800,
    "thumbnail_url": "https://<object-storage>/.../md.png?<signature>",
    "links": [{"type": "comment", "id": "c-abc"}]
  }
}
```

### 3.4 下载鉴权(私有对象 + 短时效签名)

两种方式(默认 A):
- **A:签名下载 URL**:`GET /attachments/{id}/download` 校验调用者读权限 → 生成短时效(默认 60s)签名 GET URL → 返回或 302。客户端直连对象存储下载,省后端带宽。
- **B:后端代理**:后端校验权限后流式返回字节(便于精确审计、兼容不支持重定向的客户端,但占后端带宽)。
- 对象本身**私有**,绝不公开读;签名 URL 时效短、单次用途、绑定 HTTP 方法与对象键。
- 图片/缩略图同理用短时效签名 URL;前端在过期前刷新。
- 下载按真实 MIME 设 `Content-Disposition`;未知/可执行类型强制 `attachment` 下载而非内联渲染。

### 3.5 错误码

统一错误信封:`{"error": {"code": "...", "message": "...", "details": {}}}`。

| HTTP | code | 场景 |
|------|------|------|
| 400 | `VALIDATION_ERROR` | 字段非法、MIME 与扩展名不符 |
| 401 | `UNAUTHENTICATED` | token 缺失/失效 |
| 403 | `FORBIDDEN` | 无目标 issue/comment 写权限或附件读权限 |
| 404 | `NOT_FOUND` | 附件不存在 |
| 409 | `CONFLICT` | 重复 complete、状态不允许(如对 completed 再 complete) |
| 413 | `FILE_TOO_LARGE` | 超单文件上限 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | MIME/扩展名不在允许清单 |
| 422 | `HASH_MISMATCH` | 内容哈希与实际对象不符 |
| 423 | `QUOTA_EXCEEDED` | 超 workspace 配额 |
| 429 | `RATE_LIMITED` | 上传申请/下载触发限流(见 auth Spec) |
| 502 | `STORAGE_ERROR` | 对象存储不可达(不泄露内部细节) |

### 3.6 大小与类型限制(默认值,可配置)

- 单文件上限:默认 100 MB(图片可单独设 25 MB),企业版可调高;以 `attachment_quotas.max_file_bytes` 为准。
- 允许类型:图片(png/jpg/gif/webp/svg*)、文档(pdf/txt/md/csv/xlsx/docx)、压缩包(zip/tar.gz)、日志/文本、代码文本等;禁止可执行文件(exe/dll/sh/…)直接上传,或下载时强制 `attachment` 并强提示。
- *SVG 含脚本风险:渲染前净化或以 `<img>` 隔离上下文,不内联执行。
- workspace 总配额按套餐;接近上限时上传申请返回 `QUOTA_EXCEEDED`。

### 3.7 WebSocket 事件

| 事件 | 载荷要点 | 触发 |
|------|----------|------|
| `attachment.processed` | 附件 id + `thumbnail_url` + `scan_status` | 缩略图/扫描异步完成 |
| `attachment.deleted` | 附件 id | 软删除 |

> 上传/下载本身走 HTTP(非实时通道)。评论携带附件发布后,经 `comment.created`(comment-inbox Spec)推送,附件元数据随评论载荷下发;接收端用缩略图签名 URL 渲染。缩略图/扫描异步完成可用 `attachment.processed` 更新,或前端打开灯箱时按需拉取。长上传进度是客户端本地态,无需走 WebSocket。

---

## 4. UI/UX

### 4.1 上传入口

- **composer 工具条**:回形针图标 → 文件选择器;支持拖拽文件到 composer 区域、粘贴截图(Ctrl+V)直接触发上传。
- **issue 详情「附件」区**:标题栏右侧或侧栏「附件 (N)」折叠面板,展示该 issue 全部附件网格。

### 4.2 上传中状态(拖拽 / 粘贴 / 进度)

- 选中文件后,composer 内出现附件占位卡片:缩略图/类型图标 + 文件名 + **进度条** + 取消按钮。
- 直传进度由客户端监听 XHR/fetch 上传进度实时更新;失败显示「重试」。
- 多个文件并发上传,各自独立进度;全部 `completed` 才允许提交评论(或允许先提交、附件后台补传,二选一看产品策略,默认全部完成方可提交)。

### 4.3 已上传附件展示(预览)

- **图片**:评论内内联缩略图(md 尺寸),点击打开灯箱(加载原图,支持缩放/旋转/下载/在附件区定位);多图自动排成网格。
- **非图片**:文件卡片(类型图标 + 文件名 + 大小 + 上传者 + 下载按钮)。
- **issue 附件区**:图片走缩略图网格,文件走列表;每项 hover 出现「下载 / 删除 / 复制下载链接」。

### 4.4 agent 产出物(核心差异)

- agent 评论里的附件带 agent 头像与「来自 code-reviewer 运行」标记(`uploader_kind='agent'`),区别于人类上传。
- 截图类产出物默认内联预览(`display='inline'`);报告/日志类以文件卡片呈现(`display='card'`)。

### 4.5 关键交互流程

**上传(签名 URL 直传):**
1. 用户在 composer 拖入/选择/粘贴文件。
2. 前端**预校验**(大小/类型,快速失败),算 `content_hash`(大文件可分块算或跳过由服务端校验)。
3. 前端 `POST /upload-requests` 拿签名 PUT URL(与允许的头)。
4. 前端 `PUT` 字节直传对象存储,监听进度更新 UI;分块则并发 PUT 各 part。
5. 直传成功 → `POST /attachments/{id}/complete`;服务端校验对象、嗅探 MIME、生成缩略图、触发扫描。
6. 评论提交时带 `attachment_ids`(或用 complete 时已建的 `link_to` 关联)。
7. 失败/取消:`POST /abort`;后台清理任务定期回收 `pending` 超时对象(`expires_at`)。

**下载:**
1. 用户点附件「下载」。
2. 前端 `GET /attachments/{id}/download` → 拿短时效签名 URL(或 302)。
3. 浏览器直连对象存储下载;URL 过期则重新请求一次。
4. 无权限:返回 403,UI 提示「你没有权限下载此文件」。

### 4.6 安全与可靠性细节(必须实现)

- **私有对象 + 短时效签名**:桶私有,任何访问经签名 URL 或后端代理;签名 60s 量级、绑定方法与对象键。
- **MIME 以服务端为准**:不信客户端声明;下载按真实 MIME 设 `Content-Disposition`,未知/可执行强制 `attachment`。
- **存储键不可枚举**:对象键含 UUID 段,避免遍历猜测。
- **哈希去重与完整性**:`content_hash` 既去重又校验完整性;不匹配返回 `HASH_MISMATCH`。
- **软删除 + 延迟回收**:删除先软删,对象由后台任务延迟(默认 7 天)清理,防误删;回收前确认无其他 `attachment_links` 引用。
- **扫描隔离**:命中恶意内容置 `scan_status='infected'`,下载被拒并提示,通知上传者与管理员。
- **限流**:`upload-requests` 与 `download` 按用户/IP 限流(见 auth Spec)。
- **配额前置校验**:签发 URL 前校验配额。
- **孤儿对象清理**:后台任务按 `idx_attachments_pending` 扫描 `expires_at` 已过且未 `completed` 的记录,置 `expired` 并删对象。

---

## 5. 验收标准

### 5.1 功能 — 上传 / 下载

- [ ] 三阶段直传闭环:`upload-request` → 客户端 PUT 直传 → `complete`;字节流不经应用服务器。
- [ ] `upload_status` 状态机正确:仅 `pending`/`uploading` 可 `complete`;对 `completed` 再 `complete` 返回 `CONFLICT`;`abort` 置 `failed` 并清理对象。
- [ ] `complete` 以对象存储 HEAD 结果为准校验大小;嗅探真实 MIME 写回,客户端伪造 `Content-Type` 无效。
- [ ] `content_hash` 不匹配返回 `HASH_MISMATCH`;命中已有附件走「秒传」复用对象。
- [ ] 图片异步生成 sm/md/lg 缩略图;`attachment.processed` 事件下发 `thumbnail_url`。
- [ ] 下载走短时效(60s)签名 URL 或 302;对象私有,无公开读;过期可重新申请。
- [ ] 未知/可执行类型下载强制 `Content-Disposition: attachment`,不内联渲染。
- [ ] 分块上传:分片签名、并发 PUT、断点续传(`upload_sessions.parts`)、合并完成。

### 5.2 功能 — 限制 / 关联 / 删除

- [ ] 超单文件上限返回 `FILE_TOO_LARGE`;MIME/扩展名不在白名单返回 `UNSUPPORTED_MEDIA_TYPE`;MIME 与扩展名不符返回 `VALIDATION_ERROR`。
- [ ] 配额前置校验,超限返回 `QUOTA_EXCEEDED`(签发 URL 前)。
- [ ] `attachment_links` 正确建立:同一附件可挂多个 issue/comment;`uq_attachment_link` 生效;`display`/`position` 正确。
- [ ] 列出 issue/comment 附件按 `position` 排序;图片内联、文件卡片展示正确。
- [ ] 软删除后对象延迟(7 天)回收;回收前确认无其他 `attachment_links` 引用;无权限删除返回 `FORBIDDEN`。
- [ ] 后台清理任务回收 `expires_at` 超时的孤儿对象,置 `expired`。

### 5.3 功能 — agent 产出物(核心差异)

- [ ] agent runtime 用 API token 走同一套 `upload-requests`/`complete`/关联接口;`uploader_kind='agent'` 正确填充。
- [ ] agent 评论附件 UI 带 agent 头像与「来自 <agent> 运行」标记,区别于人类上传。
- [ ] 截图类默认 `display='inline'`,报告/日志类 `display='card'`。

### 5.4 非功能

- [ ] **上传大小限制**:单文件上限以 `attachment_quotas.max_file_bytes` 为准(默认 100 MB,图片 25 MB);超限在签发前拒绝,不浪费带宽。
- [ ] **直传性能**:大文件直传不占用应用服务器带宽;`upload-request`/`complete` 接口 P95 < 300ms。
- [ ] **下载时延**:签名 URL 签发 P95 < 200ms;缩略图渲染走对象存储 CDN/直连。
- [ ] **安全**:桶私有、签名短时效绑定方法与键;MIME 服务端嗅探;SVG 净化/隔离;存储键不可枚举;扫描命中隔离;错误信息不泄露内部细节。
- [ ] **属主校验**:`complete`/`abort` 仅上传申请者本人可操作(服务端校验 `uploader_id` = 当前 principal),非属主返回 403。
- [ ] **签名 URL 尺寸约束**:签名 PUT URL 绑定声明的 `file_size` 上限(如通过 `Content-Length` 条件或存储侧策略),防止攻击者向 pending 键灌超大对象;配合存储侧生命周期规则兜底清理。
- [ ] **多租户隔离**:所有查询强制带 `workspace_id`;跨 workspace 访问返回 403/404。
- [ ] **可靠性**:孤儿对象清理任务幂等;软删除延迟回收防误删;限流(auth Spec)生效。
- [ ] **可观测**:上传申请/完成/失败、扫描结果、配额拒绝均有审计日志。
