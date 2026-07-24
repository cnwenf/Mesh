# 调研记录：附件（Attachment）

> 模块簇：协作与基础能力
> 调研对象：业界主流团队工作区产品在「附件 / 文件」上的成熟设计。
> 说明：本文仅记录中性化的设计模式与业界标准做法，用于指导 Mesh 的 Spec 撰写；不指向任何具体产品。
> Mesh 特色标注：`[Mesh 特色]` 表示需要特别为「AI agent 作为队友」这一核心范式做的设计（如 agent 产出物截图/报告作为附件）。

---

## 一、功能清单

| # | 功能点 | 典型用户场景 |
|---|--------|--------------|
| F1 | 客户端经签名 URL 直传对象存储 | 大文件不经过应用服务器，浏览器/CLI 拿到预签名 PUT URL 后直接上传到对象存储，降低后端带宽与延迟 |
| F2 | 分块上传（multipart） | 大文件（>数百 MB）切片并发上传、断点续传 |
| F3 | 文件元数据登记 | 上传完成后向服务端「确认」，登记文件名/大小/MIME/上传者，生成附件记录 |
| F4 | 图片预览 / 缩略图 | 图片附件在评论/issue 内联预览；自动生成多尺寸缩略图（列表/卡片/原图） |
| F5 | 文件类型图标 | 非图片文件按 MIME 显示对应图标（PDF/压缩包/表格/代码等） |
| F6 | 附件与 issue 关联 | issue 详情有「附件」区，集中展示该 issue 所有文件 |
| F7 | 附件与 comment 关联 | 评论内嵌附件（图片内联、文件卡片） |
| F8 | 下载鉴权 | 下载需鉴权：要么走签名下载 URL（短时效），要么经后端代理校验权限 |
| F9 | 大小与类型限制 | 单文件大小上限、允许/禁止的 MIME/扩展名清单、配额（每 workspace/每用户） |
| F10 | 删除附件 | 作者或管理员删除；对象存储对象异步清理（软删除 + 延迟回收） |
| F11 | 病毒 / 恶意内容扫描 | 上传后异步扫描，标记风险，命中后隔离或禁止下载（可选增强项） |
| F12 | 重复文件去重 | 按内容哈希（SHA-256）去重存储，节省空间（可选） |
| F13 | 附件搜索 | 按文件名搜索某 issue / workspace 内的附件（可选） |
| F14 | agent 产出物作为附件 `[Mesh 特色]` | agent 运行产出的截图、测试报告、日志、构建产物，由 runtime 经 API token 上传并挂到对应 issue/comment |
| F15 | 图片懒加载与渐进显示 | 列表用缩略图，点开灯箱（lightbox）加载原图；支持缩放/旋转 |
| F16 | 拖拽 / 粘贴上传 | 在 composer 拖入文件或粘贴截图即触发上传 |

---

## 二、数据模型

> 约定：PostgreSQL；UUID 主键；含 `created_at`/`updated_at`；软删除 `deleted_at`；对象存储用签名 URL 直传；下载走签名 URL 或后端代理鉴权。

### 2.1 `attachments` — 附件主表

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 附件 ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces | 多租户隔离 |
| `uploader_type` | text | NOT NULL, CHECK in ('member','agent') | 上传者类型 `[Mesh 特色]` |
| `uploader_id` | uuid | NOT NULL | 上传者 ID |
| `file_name` | text | NOT NULL | 原始文件名（含扩展名） |
| `file_size` | bigint | NOT NULL, CHECK > 0 | 字节数 |
| `mime_type` | text | NOT NULL | MIME 类型（以服务端嗅探/对象存储探测为准，而非客户端声明） |
| `extension` | text | NULL | 归一化扩展名 |
| `content_hash` | text | NULL | SHA-256（去重 + 完整性校验）；索引 |
| `storage_provider` | text | NOT NULL default 's3' | 对象存储提供商标识（中性命名，不绑定具体厂商） |
| `storage_bucket` | text | NOT NULL | 桶名 |
| `storage_key` | text | NOT NULL | 对象键（建议 `ws/<workspace_id>/<uuid>/<sanitized-name>`，不暴露可枚举路径） |
| `upload_status` | text | NOT NULL default 'pending', CHECK in ('pending','uploading','completed','failed','expired') | 直传两阶段状态机 |
| `scan_status` | text | NOT NULL default 'pending', CHECK in ('pending','clean','infected','error','skipped') | 扫描状态（可选） |
| `is_image` | boolean | NOT NULL default false | 是否图片（冗余，加速渲染分支） |
| `image_width` | int | NULL | 图片宽（像素） |
| `image_height` | int | NULL | 图片高 |
| `thumbnail_keys` | jsonb | NULL | 缩略图对象键映射，如 `{"sm":"...","md":"...","lg":"..."}` |
| `expires_at` | timestamptz | NULL | 上传未完成记录的过期清理时间 |
| `deleted_at` | timestamptz | NULL | 软删除 |
| `created_at` / `updated_at` | timestamptz | NOT NULL | |

**关键索引：**
- `idx_attachments_uploader (workspace_id, uploader_type, uploader_id, created_at)`。
- `idx_attachments_hash (workspace_id, content_hash)` —— 去重查询。
- 部分索引 `idx_attachments_pending ON attachments(expires_at) WHERE upload_status <> 'completed'` —— 清理任务扫描未完成的上传。

### 2.2 `attachment_links` — 附件与实体的关联（多对多 / 多态）

> 一个附件可被多个 issue/comment 引用（如转发、复制评论），故用关联表而非在附件表里写死外键。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL | |
| `attachment_id` | uuid | NOT NULL, FK→attachments | |
| `linked_type` | text | NOT NULL, CHECK in ('issue','comment') | 关联实体类型 |
| `linked_id` | uuid | NOT NULL | 关联实体 ID |
| `display` | text | NOT NULL default 'card' | 展示方式：`inline`（图片内联）/`card`（文件卡片） |
| `position` | int | NOT NULL default 0 | 排序 |
| `created_at` | timestamptz | NOT NULL | |

**唯一约束：** `uq_attachment_link (attachment_id, linked_type, linked_id)`。
**关键索引：** `idx_links_target (workspace_id, linked_type, linked_id, position)` —— 拉取某 issue/comment 的附件。

### 2.3 `upload_sessions`（可选，分块上传台账）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | uuid | PK，对应 attachment.id |
| `attachment_id` | uuid | FK→attachments |
| `upload_id` | text | 对象存储侧的 multipart upload id |
| `part_size` | int | 分片大小 |
| `parts` | jsonb | 各分片状态（编号/etag/已传） |
| `created_at` / `updated_at` | timestamptz | |

> 单文件直传可只用 `attachments.upload_status`；分块上传场景再加这张表跟踪断点续传。

### 2.4 `attachment_quotas`（可选，配额）

| 字段 | 类型 | 说明 |
|------|------|------|
| `workspace_id` | uuid | PK / FK |
| `max_file_bytes` | bigint | 单文件上限 |
| `total_bytes` | bigint | workspace 总配额 |
| `used_bytes` | bigint | 已用（可由附件表聚合维护） |
| `allowed_mimes` | jsonb | 允许的 MIME 白名单（NULL=用默认） |

### 2.5 ER 关系总结

```
workspaces 1─* attachments 1─* attachment_links *─1 (issues | comments)
attachments 1─1 upload_sessions（可选）
workspaces 1─1 attachment_quotas（可选）
```

---

## 三、接口设计

> 鉴权：`Authorization: Bearer <token>`（成员会话或 agent runtime API token）。游标分页。
> 直传核心：**「请求签名 URL → 客户端直传对象存储 → 回调服务端确认」** 三阶段，文件字节流不经过应用服务器。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/attachments/upload-requests` | 申请上传：校验类型/大小/配额，建 `pending` 附件记录，返回签名 PUT URL（或分块上传所需的一组签名 URL） |
| POST | `/api/v1/attachments/{id}/complete` | 客户端直传成功后回调：服务端校验对象存在与大小、嗅探 MIME、触发缩略图/扫描，置 `completed` |
| POST | `/api/v1/attachments/{id}/abort` | 取消上传，清理对象 |
| GET | `/api/v1/attachments/{id}` | 取附件元数据 |
| DELETE | `/api/v1/attachments/{id}` | 软删除附件 |
| GET | `/api/v1/attachments/{id}/download` | 获取**签名下载 URL**（短时效），或 302 重定向到签名 URL；亦可由后端代理流式下载 |
| GET | `/api/v1/attachments/{id}/thumbnail?size=sm` | 获取缩略图签名 URL（图片） |
| GET | `/api/v1/issues/{issue_id}/attachments` | 列出某 issue 的附件 |
| GET | `/api/v1/comments/{comment_id}/attachments` | 列出某 comment 的附件 |
| POST | `/api/v1/multipart/{id}/parts` | （分块）申请下一批分片签名 URL |
| POST | `/api/v1/multipart/{id}/complete` | （分块）合并分片并完成 |

### 3.2 申请上传（upload-request）

**请求体：**
```json
{
  "file_name": "screenshot.png",
  "file_size": 245760,
  "mime_type": "image/png",
  "content_hash": "9f86d0...",
  "link_to": {"type": "comment", "id": "c-abc"}
}
```

**响应体（201）：**
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
> 分块场景：`upload` 改为 `{ "upload_id": "...", "part_urls": [{"part_number":1,"url":"..."}, ...] }`。

**校验点（服务端在签发前）：**
- 扩展名/MIME 是否在允许清单；MIME 与扩展名是否匹配（防伪造）。
- `file_size` 是否超单文件上限与 workspace 剩余配额。
- `content_hash` 命中已有附件可走「秒传」（直接复用对象，建关联）。
- 校验调用者对 `link_to` 目标有写权限。

### 3.3 完成上传（complete）

**请求体：**
```json
{"file_size": 245760, "content_hash": "9f86d0...", "parts": [{"part_number":1, "etag":"..."}]}
```

**服务端动作：**
1. 向对象存储 HEAD 对象，确认存在且大小一致（以对象存储为准，不信客户端）。
2. 服务端嗅探真实 MIME（不信客户端 `Content-Type`），写回 `mime_type`/`extension`/`is_image`。
3. 校验 `content_hash`（若提供）匹配。
4. 图片：异步生成缩略图（sm/md/lg）写入 `thumbnail_keys`。
5. 触发病毒扫描（异步，`scan_status`）。
6. `upload_status = completed`；若已带 `link_to`，建立 `attachment_links`。

**响应体（200）：**
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

### 3.4 下载鉴权

两种方式（推荐默认 A）：
- **A：签名下载 URL**：`GET /attachments/{id}/download` 校验调用者对附件有读权限 → 生成短时效（如 60s）签名 GET URL → 返回或 302。客户端直连对象存储下载，省后端带宽。
- **B：后端代理**：后端校验权限后流式返回字节（便于精确审计、对不支持重定向的客户端友好，但占后端带宽）。
- 对象本身**私有**，绝不公开读；签名 URL 时效短、单次用途。
- 图片/缩略图同理用短时效签名 URL；前端在 URL 过期前刷新。

### 3.5 错误码

| HTTP | code | 场景 |
|------|------|------|
| 400 | `VALIDATION_ERROR` | 字段非法、MIME 与扩展名不符 |
| 401 | `UNAUTHENTICATED` | token 缺失/失效 |
| 403 | `FORBIDDEN` | 无目标 issue/comment 写权限或附件读权限 |
| 404 | `NOT_FOUND` | 附件不存在 |
| 409 | `CONFLICT` | 重复 complete、状态不允许（如对 completed 再 complete） |
| 413 | `FILE_TOO_LARGE` | 超单文件上限 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | MIME/扩展名不在允许清单 |
| 422 | `HASH_MISMATCH` | 内容哈希与实际对象不符 |
| 423 | `QUOTA_EXCEEDED` | 超 workspace 配额 |
| 429 | `RATE_LIMITED` | 上传申请/下载触发限流 |
| 502 | `STORAGE_ERROR` | 对象存储不可达（不泄露内部细节） |

### 3.6 大小与类型限制（建议默认值，可配置）

- 单文件上限：默认 100 MB（图片可单独设 25 MB），企业版可调高。
- 允许类型：图片（png/jpg/gif/webp/svg*）、文档（pdf/txt/md/csv/xlsx/docx）、压缩包（zip/tar.gz）、日志/文本、代码文本等；禁止可执行文件（exe/dll/sh/…）直接上传或下载时强提示。
- *SVG 含脚本风险：渲染前净化或以 `<img>` 隔离上下文，不内联执行。
- workspace 总配额按套餐；接近上限时上传申请返回 `QUOTA_EXCEEDED`。

---

## 四、UI 设计

### 4.1 上传入口

- **composer 工具条**：回形针图标 → 文件选择器；支持拖拽文件到 composer 区域、粘贴截图（Ctrl+V）直接触发上传。
- **issue 详情「附件」区**：标题栏右侧或侧栏一个「附件 (N)」折叠面板，展示该 issue 全部附件网格。

### 4.2 上传中状态

- 选中文件后，composer 内出现附件占位卡片：缩略图/类型图标 + 文件名 + 进度条 + 取消按钮。
- 直传进度由客户端监听 XHR/fetch 上传进度实时更新；失败显示「重试」。
- 多个文件并发上传，各自独立进度；全部 completed 才允许提交评论（或允许先提交、附件后台补传，二选一看产品策略）。

### 4.3 已上传附件展示

- **图片**：评论内内联缩略图（md 尺寸），点击打开灯箱（加载原图，支持缩放/旋转/下载/在附件区定位）；多图自动排成网格。
- **非图片**：文件卡片（类型图标 + 文件名 + 大小 + 上传者 + 下载按钮）。
- **issue 附件区**：图片走缩略图网格，文件走列表；每项 hover 出现「下载 / 删除 / 复制下载链接」。

### 4.4 agent 产出物 `[Mesh 特色]`

- agent 评论里的附件带 agent 头像与「来自 code-reviewer 运行」标记，区别于人类上传。
- 截图类产出物默认内联预览；报告/日志类以文件卡片呈现。

---

## 五、UX 设计

### 5.1 上传流程（签名 URL 直传）

1. 用户在 composer 拖入/选择文件。
2. 前端做**预校验**（大小/类型，快速失败），算 `content_hash`（大文件可分块算或跳过由服务端校验）。
3. 前端 `POST /upload-requests` 拿签名 PUT URL（与允许的头）。
4. 前端 `PUT` 文件字节直传对象存储，监听进度更新 UI；分块则并发 PUT 各 part。
5. 直传成功 → 前端 `POST /attachments/{id}/complete` 回调；服务端校验对象、嗅探 MIME、生成缩略图、触发扫描。
6. 评论提交时带上 `attachment_ids`（或由 complete 时已建立的 `link_to` 关联）。
7. 失败/取消：前端 `POST /abort`；后台清理任务定期回收 `pending` 超时对象（`expires_at`）。

### 5.2 下载流程

1. 用户点附件「下载」。
2. 前端 `GET /attachments/{id}/download` → 拿到短时效签名 URL（或 302）。
3. 浏览器直连对象存储下载；URL 过期则前端重新请求一次。
4. 无权限：返回 403，UI 提示「你没有权限下载此文件」。

### 5.3 实时性方案

- 上传/下载本身是 HTTP（非实时通道）。
- 评论携带附件发布后，经 WebSocket `comment.created` 推送，附件元数据随评论 payload 下发；接收端用缩略图签名 URL 渲染。
- 缩略图/扫描异步完成：可用 WebSocket `attachment.processed` 事件更新 `thumbnail_url`/`scan_status`，或前端在打开灯箱时按需拉取。
- 长上传的进度是客户端本地态，无需走 WebSocket。

### 5.4 安全与可靠性细节

- **私有对象 + 短时效签名**：桶设为私有，任何访问都经签名 URL 或后端代理；签名时效 60s 量级、绑定 HTTP 方法与对象键。
- **MIME 以服务端为准**：不信客户端声明，对象存储/服务端嗅探真实类型；下载时按真实 MIME 设 `Content-Disposition`，未知/可执行类型强制 `attachment` 下载而非内联渲染。
- **存储键不可枚举**：对象键含 UUID 段，避免遍历猜测。
- **哈希去重与完整性**：`content_hash` 既去重又校验上传完整性；不匹配返回 `HASH_MISMATCH`。
- **软删除 + 延迟回收**：删除先软删，对象存储对象由后台任务延迟（如 7 天）清理，防误删；回收前确认无其他 `attachment_links` 引用。
- **扫描隔离**：命中恶意内容置 `scan_status=infected`，下载被拒并提示，通知上传者与管理员。
- **限流**：`upload-requests` 与 `download` 端点按用户/IP 限流，防滥用（详见 auth 模块速率限制）。
- **配额前置校验**：签发上传 URL 前校验配额，避免传完才发现超限浪费带宽。

---

## 六、关键设计取舍小结（供 Spec 参考）

1. **直传不经后端**：应用服务器只发签名与记账，字节流走对象存储，是规模化附件的标准做法。
2. **两阶段状态机**：`pending → completed`，配合 `expires_at` 清理任务处理「传了没确认」的孤儿对象。
3. **关联表解耦**：`attachment_links` 让一个附件可挂多个实体，附件本身与引用解耦。
4. **MIME/大小以服务端与对象存储为准**：客户端声明仅作预校验，安全判定一律服务端。
5. **私有 + 短时效签名下载**：默认安全姿态；代理下载作为审计/兼容补充。
6. **agent 与人类共用附件模型**：仅以 `uploader_type` 区分，agent runtime 用 API token 走同一套上传/关联接口 `[Mesh 特色]`。
</antParameter>
</invoke>
