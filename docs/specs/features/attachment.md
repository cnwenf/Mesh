# 附件(Attachment)功能 Spec

| 项目 | 内容 |
|------|------|
| 所属层 | 协作层 / 基础能力(Collaboration & Foundation) |
| 模块 | attachment |
| 依赖 Spec | `workspace`(多租户、配额)、`member`(统一 members.id,human\|agent)、`auth`(Bearer/RBAC/限流)、`issue`(关联宿主)、`comment-inbox`(评论内附件引用 `attachment_ids`) |
| 被依赖 | `comment-inbox`(评论载荷引用附件)、`agent`(agent 运行产出物经 API token 上传) |
| 技术栈 | FastAPI + SQLAlchemy 2.x + PostgreSQL + 对象存储(私有桶 + 预签名 URL) |
| 状态 | Draft |

> **全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)**
> 1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `uuid`(默认 `gen_random_uuid()`);`created_at`/`updated_at` 为 `TIMESTAMPTZ NOT NULL DEFAULT now()`;软删除统一 `deleted_at TIMESTAMPTZ NULL`。
> 2. **成员**:成员模型以 README §6.1 为唯一权威——附件 `uploader_id` 引用**统一 `members.id`**(人类与 agent 同册,人类/agent 由 `members.member_type` 判别)。**本模块不存 `uploader_kind` 等 `*_type`/`*_kind` 判别列**;API 响应可携带服务端 JOIN `members` 计算出的 `member_type` 快照(标注"快照,真源为 members")。
> 3. **多租户**:跨模块外键一律按 README §6.2 建复合 FK + 目标表 `UNIQUE(workspace_id, id)`(`attachments` 建 `UNIQUE(workspace_id, id)`,`attachment_links.attachment_id` 复合引用)。
> 4. **接口**:REST 前缀 `/api/v1`;`Authorization: Bearer <token>`;**成功包络 / 游标分页 / 错误信封 / 幂等写 / HTTP 语义以 README §6.14 为唯一权威**(列表 `{"data":[...],"next_cursor":<opaque|null>}`,`next_cursor=null` 表示末页;错误 `{"error":{"code","message","details"}}`),本 Spec 仅列本模块具名错误码。
> 5. **实时**:统一实时契约见 README §6.7(频道内 `seq`、`realtime_events` 持久重放、`resume_from`/`resync_required`);事件名 `<entity>.<action>`;附件处理结果以 `attachment.processed` 事件下发。
> 6. **队列 / 投递**:隔离区扫描经 transactional outbox(README §6.6)移交附件处理 worker;副作用幂等键见 README §6.5。
> 7. **ORM**:SQLAlchemy 2.x 声明式约定(`Mapped` / `mapped_column`)。

> **核心设计(必须采纳)**
> - **三阶段直传**:申请签名 URL → 客户端直传对象存储 → 回调 `complete`;字节流**不经过应用服务器**。
> - **两条正交状态机**:`upload_status`(直传,挂在 `attachments` 表:`pending → uploading → completed`,另有 `failed`/`expired`,配合 `expires_at` 清理孤儿对象)与 `scan_status`(隔离区,挂在 **`attachment_blobs`** 表:`pending → clean | infected | error | skipped`;扫一次全体共享者可见)。
> - **隔离区闸门(CRITICAL)**:`complete` **不代表附件可用**——它只置 `upload_status='completed'` 并把对象移交**隔离区管线**(所引 blob 的 `scan_status='pending'` 即隔离中);真正的 MIME 嗅探(magic bytes)、全量 SHA-256 校验、病毒扫描由**附件处理 worker**(README §2.2)服务端读取对象字节后完成。**下载/预览/缩略图仅在所引 blob 的 `scan_status IN ('clean','skipped')` 时开放**,否则按 §3.4 拒绝。
> - **MIME/大小/哈希以服务端为准**:客户端声明仅作预校验;真实 MIME 由 worker 从 magic bytes 嗅探(不信客户端头、不靠 HEAD),SHA-256 由 worker 全量计算并与客户端声明比对。
> - **私有对象 + 短时效签名下载**:桶私有,任何访问经签名 URL 或后端代理;签名时效 60s 量级、绑定方法与对象键。
> - **共享 blob(独立真源表 `attachment_blobs`,`ref_count` 原子计数)**:内容去重只**共享 blob**(`attachment_blobs` 行,按 `content_hash` 内容寻址,`ref_count` 原子维护),但**始终新建独立 `attachments` 行与独立 `attachment_links`**,绝不复用同一附件记录(见 §3.2 / §4.6)。
> - **秒传 possession(RED LINE)**:秒传仅允许调用者**已可读**该 blob(存在至少一条引用该 blob、调用者有读权限的存活 attachment,或调用者即该 blob 某 attachment 的上传者);否则不得凭客户端提供的 hash 短路(防内容探测/越权复用),须完成完整上传,由服务端后置去重(见 §3.2)。
> - **agent 与人类共用模型**:`uploader_id` 统一指向 `members.id`,agent runtime 用 API token 走同一套接口;人类/agent 区分仅为 API 响应中的计算 `member_type` 快照(README §6.1),不落库。

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
workspaces 1─* attachment_blobs 1─* attachments 1─* attachment_links *─1 (issues | comments | chat_messages)
attachments 1─1 upload_sessions (可选,分块上传台账)
workspaces 1─1 attachment_quotas (可选,配额)

attachments.blob_id ─复合 FK─► attachment_blobs(workspace_id, id)   (blob 真源引用,README §6.2)
attachments.uploader_id ─复合 FK─► members(workspace_id, id)   (人类/agent 同册,判别 JOIN members.member_type)
attachment_links.attachment_id ─复合 FK─► attachments(workspace_id, id)
attachment_blobs(scan_status='pending') ──outbox/ SKIP LOCKED──► 附件处理 worker(README §2.2:magic-byte 嗅探 + 全量 SHA-256 + AV 扫描)
```

### 2.2 `attachment_blobs` — blob 真源表(独立真源,R2)

> 每个**唯一内容**在 workspace 内只对应一行 `attachment_blobs`;多条 `attachments` 可共享同一 blob(秒传/去重),但附件元数据/关联/生命周期始终独立。扫描结论、MIME 嗅探、缩略图、`ref_count` 均挂在此表,扫一次全体共享者可见。

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | blob ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离 |
| `content_hash` | text | NOT NULL | SHA-256,**内容寻址键**(worker 全量计算,非客户端声明) |
| `storage_provider` | text | NOT NULL DEFAULT 's3' | 对象存储提供商标识(中性命名,不绑定厂商) |
| `storage_bucket` | text | NOT NULL | 桶名 |
| `storage_key` | text | NOT NULL | 对象键(`ws/<workspace_id>/<hash 前缀>/<uuid>` 段,不可枚举) |
| `file_size` | bigint | NOT NULL, CHECK (file_size > 0) | 字节数(worker 服务端读取核验) |
| `mime_type` | text | NULL | **worker 从 magic bytes 嗅探**的真实 MIME(非客户端声明、非 HEAD);隔离期可为 NULL,放行后写回 |
| `extension` | text | NULL | 归一化扩展名(worker 写回) |
| `is_image` | boolean | NOT NULL DEFAULT false | 冗余,加速渲染分支(worker 写回) |
| `image_width` | int | NULL | 图片宽(像素,worker 写回) |
| `image_height` | int | NULL | 图片高(像素,worker 写回) |
| `thumbnail_keys` | jsonb | NULL | 缩略图对象键映射 `{"sm":"...","md":"...","lg":"..."}`(worker 放行后写回) |
| `scan_status` | text | NOT NULL DEFAULT 'pending', CHECK in ('pending','clean','infected','error','skipped') | **隔离区状态机(内容级)**:`pending`=隔离中(不可下载),`clean`/`skipped`=放行可见,`infected`=永久拒绝,`error`=嗅探/校验/扫描失败(重试上限)。`skipped` 仅限策略显式放行的纯文本类(如 `.txt`/`.log`/`.csv`,见 §3.6)。语义同原 attachments.scan_status,改挂 blob(扫一次全体共享者可见) |
| `scan_detail` | jsonb | NULL | 扫描结果明细 `{sniffed_mime, sha256, hash_matches, av_engine, av_result, error_code}`;`error` 时含 `HASH_MISMATCH` 等码 |
| `ref_count` | int | NOT NULL DEFAULT 0, CHECK (ref_count >= 0) | 引用该 blob 的**存活** `attachments` 行数(原子维护:attachment 创建 +1、软删/硬删 −1,同事务) |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**约束与索引:**
- **`UNIQUE (workspace_id, content_hash)`** — 并发去重串行化(§3.2 / §4.6):同一 workspace 同一内容只建一行 blob,并发 INSERT 由唯一约束保证幂等。
- **`UNIQUE (workspace_id, id)`** — 供 `attachments.blob_id` 复合 FK 引用(README §6.2)。
- 部分索引 `idx_blobs_quarantine ON attachment_blobs(created_at) WHERE scan_status = 'pending'` — 附件处理 worker SKIP LOCKED 扫隔离区(README §2.2)。
- 部分索引 `idx_blobs_refcount ON attachment_blobs(storage_key) WHERE ref_count = 0` — GC 候选:无存活引用的 blob 可物理删对象。

**`scan_status` 状态机(隔离区 → 放行,由附件处理 worker 驱动,README §2.2):**
```
                      ┌─(magic-byte 嗅探 + 全量 SHA-256 比对一致 + AV clean)─> clean(放行,可见)
pending(隔离中)──────┼─(纯文本白名单类,策略显式免扫)─────────────────────> skipped(放行,可见)
  由 complete 置入     ├─(命中恶意内容)───────────────────────────────────> infected(永久拒绝 + 告警)
                      └─(HASH_MISMATCH / 嗅探失败 / 扫描异常,重试上限)─────> error(拒绝,可重投)
```
- **可见性闸门(CRITICAL)**:下载 / 预览 / 缩略图端点**仅当所引 blob 的 `scan_status IN ('clean','skipped')` 才放行**;`pending`(隔离中)→ `403 scan_pending`,`infected` → `403 scan_infected`(并通知上传者与管理员),`error` → `403 scan_pending`(扫描未完成,语义同隔离中,明细见 `scan_detail`)。
- worker 以 `FOR UPDATE SKIP LOCKED` 扫 `attachment_blobs(scan_status='pending')`(README §2.2);崩溃后重扫,`error` 重试上限后告警。

### 2.3 `attachments` — 附件主表

> 每条 `attachments` 行是一次**独立的附件记录**(独立上传者、关联、生命周期与删除),通过 `blob_id` 引用共享的 `attachment_blobs` 真源行。API 响应中的 `mime_type`/`is_image`/`scan_status`/`thumbnail_url` 由服务端 JOIN `attachment_blobs` 计算返回(快照标注"真源为 attachment_blobs")。

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 附件 ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 多租户隔离 |
| `blob_id` | uuid | NOT NULL,**复合 FK `(workspace_id, blob_id) → attachment_blobs(workspace_id, id)`** | blob 真源引用(README §6.2);MIME/扫描/缩略图/存储键等真源均在 `attachment_blobs` |
| `uploader_id` | uuid | NOT NULL,**复合 FK `(workspace_id, uploader_id) → members(workspace_id, id)`** | 上传者(人类/agent 同册;人类/agent 判别 JOIN `members.member_type`,本表不存判别列,README §6.1/§6.2) |
| `file_name` | text | NOT NULL | 原始文件名(含扩展名) |
| `file_size` | bigint | NOT NULL, CHECK (file_size > 0) | 声明字节数(worker 以 blob 为准核验;`complete` 的 HEAD 仅作存在性/大小初校验) |
| `upload_status` | text | NOT NULL DEFAULT 'pending', CHECK in ('pending','uploading','completed','failed','expired') | 直传状态机(会话级,字节是否传完);**`completed` 仅代表直传完成,不代表可用** |
| `expires_at` | timestamptz | NULL | 未完成记录的过期清理时间(孤儿对象回收依据) |
| `deleted_at` | timestamptz | NULL | 软删除 |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**两条正交状态机(必须实现):**

`upload_status`(直传是否完成,**留在本表——会话级**):
```
pending ──(客户端开始直传)──> uploading ──(complete:HEAD 存在性/大小初校验通过)──> completed
   │                            │
   └──(超时未确认 expires_at)──> expired(后台清理任务置位并删对象)
                                └──(complete 初校验失败 / abort)──> failed
```
- `complete` 仅允许从 `pending`/`uploading` 进入 `completed`;对已 `completed` 再 `complete` 返回 `409 conflict`。
- **`upload_status='completed'` 不代表附件可用**:`complete` 同时把对象移交隔离区管线,可用性由所引 blob 的 `scan_status` 决定。

`scan_status`(隔离区 → 放行,**改挂 `attachment_blobs`——内容级**,扫一次全体共享者可见;状态机详见 §2.2):
- **可见性闸门(CRITICAL)**:下载 / 预览 / 缩略图端点**仅当所引 blob 的 `scan_status IN ('clean','skipped')` 才放行**;`pending`(隔离中)→ `403 scan_pending`,`infected` → `403 scan_infected`(并通知上传者与管理员),`error` → `403 scan_pending`(扫描未完成,语义同隔离中,明细见 blob 的 `scan_detail`)。

**关键索引与约束:**
- **`UNIQUE (workspace_id, id)`** — 供 `attachment_links.attachment_id` 复合 FK 引用(README §6.2)。
- `idx_attachments_uploader (workspace_id, uploader_id, created_at)`。
- 部分索引 `idx_attachments_pending ON attachments(expires_at) WHERE upload_status <> 'completed'` — 清理任务扫描未完成上传(孤儿清理)。
- 部分索引 `idx_attachments_active ON attachments(workspace_id, created_at) WHERE deleted_at IS NULL`。

### 2.4 `attachment_links` — 附件与实体的关联(多对多 / 多态)

> 一个附件可被多个 issue/comment 引用(转发、复制评论),故用关联表而非在附件表写死外键。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 隔离(逻辑外键行也必须携带,README §6.2) |
| `attachment_id` | uuid | NOT NULL,**复合 FK `(workspace_id, attachment_id) → attachments(workspace_id, id)`** | 引用附件(README §6.2) |
| `linked_type` | text | NOT NULL, CHECK in ('issue','comment','chat_message') | 关联实体类型(issue / comment / 聊天消息,统一关联表;chat-session.md 引用) |
| `linked_id` | uuid | NOT NULL | 关联实体 ID |
| `display` | text | NOT NULL DEFAULT 'card', CHECK in ('inline','card') | 图片内联 / 文件卡片 |
| `position` | int | NOT NULL DEFAULT 0 | 排序 |
| `created_at` | timestamptz | NOT NULL DEFAULT now() | |

**唯一约束:** `uq_attachment_link (attachment_id, linked_type, linked_id)`。
**关键索引:** `idx_links_target (workspace_id, linked_type, linked_id, position)` — 拉取某 issue/comment/chat_message 的附件。

### 2.5 `upload_sessions` — 分块上传台账(可选)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | uuid | PK | |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id | 隔离(供复合 FK,README §6.2) |
| `attachment_id` | uuid | NOT NULL,**复合 FK `(workspace_id, attachment_id) → attachments(workspace_id, id)`** | |
| `upload_id` | text | NOT NULL | 对象存储侧 multipart upload id |
| `part_size` | int | NOT NULL | 分片大小(字节) |
| `parts` | jsonb | NOT NULL DEFAULT '[]' | 各分片状态 `[{part_number, etag, uploaded}]` |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

> 单文件直传仅用 `attachments.upload_status`;分块/断点续传场景加这张表跟踪。

### 2.6 `attachment_quotas` — 配额(可选)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `workspace_id` | uuid | PK, FK→workspaces.id | |
| `max_file_bytes` | bigint | NOT NULL | 单文件上限 |
| `total_bytes` | bigint | NOT NULL | workspace 总配额 |
| `used_bytes` | bigint | NOT NULL DEFAULT 0 | 已用(由附件表聚合维护) |
| `allowed_mimes` | jsonb | NULL | MIME 白名单(NULL=用默认) |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

### 2.7 跨模块外键说明

- `blob_id` → **复合 FK `attachment_blobs(workspace_id, id)`**(§2.2,README §6.2)。附件的 MIME/扫描/缩略图/存储键真源均在 `attachment_blobs`,API 响应中相应字段为 JOIN 计算快照(真源为 attachment_blobs)。
- `uploader_id` → **复合 FK `members(workspace_id, id)`**(member Spec,README §6.1/§6.2)。人类/agent 判别一律 JOIN `members.member_type`;**本模块不存 `uploader_kind` 判别列**,API 响应中的 `member_type` 为服务端计算快照(真源为 members)。
- `attachment_links.attachment_id` → **复合 FK `attachments(workspace_id, id)`**;`upload_sessions.attachment_id` 同理。
- `workspace_id` → `workspaces.id`(workspace Spec);`attachment_links.linked_id` 为**多态逻辑外键**,指向 `issues.id` / `comments.id` / `chat_messages.id`(以 `linked_type` 区分;不建物理 FK 以避免多态约束复杂度)。**逻辑外键行必须携带 `workspace_id`**,删除一致性由软删除 + 服务层保证,跨租户用例纳入集成测试矩阵(README §9 T1)。

---

## 3. 接口设计

> 鉴权:`Authorization: Bearer <token>`(成员会话或 agent runtime API token)。
> 直传核心:**「申请签名 URL → 客户端直传对象存储 → 回调 complete」** 三阶段,字节流不经应用服务器。

### 3.0 分页与鉴权约定

- **包络 / 分页 / 错误信封**:统一以 README §6.14 为唯一权威。列表端点(`GET /issues/{id}/attachments`、`GET /comments/{id}/attachments`)返回 `{"data": [...], "next_cursor": <opaque|null>}`,`next_cursor=null` 表示末页;游标为不透明字符串(内部基于 `position + id` 的 keyset);错误信封 `{"error":{"code","message","details"}}`。
- **鉴权**:读附件需对宿主 issue/comment/chat_message 有读权限;写(申请上传/删除)需对应写权限;agent runtime 用 API token,权限以其所属 agent 身份校验。
- **幂等**:`complete`/`abort` 以 `upload_status` 状态机做并发保护;创建/动作类端点支持 `Idempotency-Key` 请求头(README §6.5/§6.14),同一 `upload-request` 可携带客户端生成的幂等键避免重复建记录。
- **速率限制**:`upload-requests` 与 `download` 按用户/IP 限流(见 auth Spec),触发返回 `429 rate_limited` 含 `Retry-After`(README §6.14)。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/attachments/upload-requests` | 申请上传:校验类型/大小/配额,建 `pending` 记录,返回签名 PUT URL(或一组分片签名 URL) |
| POST | `/api/v1/attachments/{id}/complete` | 直传成功后回调:HEAD 存在性/大小初校验,置 `completed` 并移交隔离区(blob.scan_status='pending');MIME 嗅探/缩略图/扫描由 worker 异步完成(§3.3)(**仅上传申请者本人可操作**,服务端校验 `uploader_id` = 当前 principal) |
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
- **秒传 / possession 规则(R2 硬约束,RED LINE)**:
  - `content_hash` 命中同 workspace 已有 blob(按 `UNIQUE(workspace_id, content_hash)` 查 `attachment_blobs`)**且调用者对该 blob 已可读**(即存在至少一条引用该 blob、调用者有读权限的存活 attachment,或调用者即该 blob 某 attachment 的上传者)→ **「秒传」**:新建独立 `attachments` 行指向该 blob(`blob.ref_count` 同事务 +1),跳过字节直传,`upload_status='completed'`;可见性随 `blob.scan_status`(已 `clean`/`skipped` 直接放行,否则待隔离区管线完成)。**始终新建独立 `attachments` 行(独立 `id`、独立 `uploader_id`)与独立 `attachment_links`,绝不复用/改写已有附件记录**。
  - **否则不得凭 hash 短路**(防内容探测/越权复用):按新上传签发签名 URL(字节上传本身即持有证明),`complete` 后由 worker 全量计算 SHA-256;若与已有 blob 内容相同,则把新 attachment 改指已有 blob(`ref_count` +1)并删除重复上传对象(**服务端后置去重**)。
- 校验调用者对 `link_to` 目标有写权限。
- 写 `pending` 记录并设 `expires_at`(默认 15 分钟)。

### 3.3 完成上传(complete)

**请求体:**
```json
{"file_size": 245760, "content_hash": "9f86d0...", "parts": [{"part_number":1, "etag":"..."}]}
```

**服务端动作(必须实现,以对象存储为准,不信客户端):**
1. 向对象存储 **HEAD 对象,仅作存在性与大小初校验**(HEAD **无法**做真实 MIME 嗅探或 SHA-256 校验)。大小不符返回 `422 hash_mismatch`/`409 conflict` 视情形。
2. 置 `upload_status='completed'`,清空 `expires_at`;若带 `link_to`,建立 `attachment_links`(独立行)。
3. **移交隔离区管线**:所引 blob 的 `scan_status` 保持/置为 `'pending'`(隔离中),`complete` **不声明附件可用、不写最终 MIME/缩略图**。同事务写 `outbox_events`(`attachment.scan_requested`,README §6.6),由**附件处理 worker**(README §2.2)以 SKIP LOCKED 领取处理。
4. **附件处理 worker(服务端读取对象字节,异步;扫描对象为 blob——`attachment_blobs(scan_status='pending')`,SKIP LOCKED)**:
   - **全量计算 SHA-256**:与客户端声明 `content_hash` 比对,不匹配 → `blob.scan_status='error'`,`blob.scan_detail.error_code='HASH_MISMATCH'`(告警 + 经 `attachment.processed` 下发);**若计算出的 SHA-256 命中同 workspace 已有 blob(后置去重,§3.2),则把新 attachment 改指已有 blob(`ref_count` +1)并删除重复上传对象**;
   - 从 **magic bytes 嗅探真实 MIME**(不信客户端 `Content-Type`、不靠 HEAD),写回 blob 行 `mime_type`/`extension`/`is_image`;
   - 运行病毒扫描 → `blob.scan_status ∈ clean | infected | error | skipped`(`skipped` 仅限 §3.6 策略显式放行的纯文本白名单类);
   - 图片在 `blob.scan_status` 放行后生成缩略图(sm/md/lg)写入 blob 行 `thumbnail_keys`。
5. 处理完成经 `attachment.processed`(README §6.7)下发最终 `blob_id`/`scan_status`/`mime_type`/`thumbnail_url`(字段来自所引 blob)。
6. API 响应中的 `mime_type`/`is_image`/`scan_status`/`thumbnail_url` 均为服务端 JOIN `attachment_blobs` 计算的快照(真源为 attachment_blobs);上传者 `member_type` 为 JOIN `members` 计算的快照(不落库,README §6.1)。

**响应体(200,仅代表直传完成、对象进入隔离区):**
```json
{
  "data": {
    "id": "att-1",
    "upload_status": "completed",
    "scan_status": "pending",
    "member_type": "human",
    "links": [{"type": "comment", "id": "c-abc"}],
    "note": "扫描中,完成后开放下载"
  }
}
```
> `mime_type`/`is_image`/`scan_status`/`thumbnail_url` 等由 worker 异步写回 **blob 行**,经 `attachment.processed` 下发(真源为 attachment_blobs);`member_type` 为计算快照(真源为 members)。

### 3.4 下载鉴权(私有对象 + 短时效签名)

**可见性闸门(CRITICAL,先于一切)**:下载 / 预览 / 缩略图端点(`download` / `thumbnail` / 内联原图)**仅当所引 blob 的 `scan_status IN ('clean','skipped')` 才放行**;否则按隔离状态拒绝:
- 所引 blob `scan_status='pending'` 或 `'error'`(扫描未完成)→ **`403 scan_pending`**(语义:扫描中,稍后重试);
- 所引 blob `scan_status='infected'` → **`403 scan_infected`**,并通知上传者与 workspace 管理员(安全事件,README §6.13 critical 分级)。

两种下载方式(默认 A,均须先过可见性闸门):
- **A:签名下载 URL**:`GET /attachments/{id}/download` 校验调用者读权限 + 所引 blob 的 `scan_status` 放行 → 生成短时效(默认 60s)签名 GET URL → 返回或 302。客户端直连对象存储下载,省后端带宽。
- **B:后端代理**:后端校验权限 + 所引 blob 的 `scan_status` 放行后流式返回字节(便于精确审计、兼容不支持重定向的客户端,但占后端带宽)。
- 对象本身**私有**,绝不公开读;签名 URL 时效短、单次用途、绑定 HTTP 方法与对象键。
- 图片/缩略图同理用短时效签名 URL(同样要求所引 blob 的 `scan_status` 放行);前端在过期前刷新。
- 下载按 worker 嗅探的真实 MIME(来自所引 blob)设 `Content-Disposition`;未知/可执行类型强制 `attachment` 下载而非内联渲染。

### 3.5 错误码

统一错误信封以 README §6.14 为权威:`{"error": {"code": "<snake_case>", "message": "...", "details": {}}}`。本模块具名错误码:

| HTTP | code | 场景 |
|------|------|------|
| 400 | `validation_error` | 字段非法、MIME 与扩展名不符 |
| 401 | `unauthorized` | token 缺失/失效 |
| 403 | `forbidden` | 无目标 issue/comment/chat_message 写权限或附件读权限 |
| 403 | `scan_pending` | 所引 blob 尚在隔离区(blob `scan_status` 为 `pending`/`error`),扫描未完成,拒绝下载/预览/缩略图(README §9 T14) |
| 403 | `scan_infected` | 所引 blob 扫描命中恶意内容(blob `scan_status='infected'`),永久拒绝并告警(README §9 T14) |
| 404 | `not_found` | 附件不存在 |
| 409 | `conflict` | 重复 complete、状态不允许(如对 completed 再 complete) |
| 413 | `file_too_large` | 超单文件上限 |
| 415 | `unsupported_media_type` | MIME/扩展名不在允许清单 |
| 422 | `hash_mismatch` | `complete` 阶段大小/初校验不符;**真实 SHA-256 比对由隔离区 worker 异步完成(blob 级),不匹配置 `blob.scan_status='error'`(`blob.scan_detail.error_code='HASH_MISMATCH'`)经 `attachment.processed` 下发** |
| 423 | `quota_exceeded` | 超 workspace 配额 |
| 429 | `rate_limited` | 上传申请/下载触发限流(见 auth Spec,带 `Retry-After`) |
| 502 | `storage_error` | 对象存储不可达(不泄露内部细节) |

### 3.6 大小与类型限制(默认值,可配置)

- 单文件上限:默认 100 MB(图片可单独设 25 MB),企业版可调高;以 `attachment_quotas.max_file_bytes` 为准。
- 允许类型:图片(png/jpg/gif/webp/svg*)、文档(pdf/txt/md/csv/xlsx/docx)、压缩包(zip/tar.gz)、日志/文本、代码文本等;禁止可执行文件(exe/dll/sh/…)直接上传,或下载时强制 `attachment` 并强提示。
- *SVG 含脚本风险:渲染前净化或以 `<img>` 隔离上下文,不内联执行。
- **纯文本免扫白名单(blob `scan_status='skipped'` 的唯一来源)**:仅 `text/plain`、`.log`、`.csv`、`.md`、`.txt` 等**无宏、不可执行**的纯文本类,经工作区策略显式列入白名单后,worker 可跳过 AV 扫描置 blob `skipped`(仍做 magic-byte 嗅探与 SHA-256 校验);其余一律走完整扫描。白名单默认保守,由 admin 配置。
- workspace 总配额按套餐;接近上限时上传申请返回 `quota_exceeded`。

### 3.7 WebSocket 事件

**实时契约以 README §6.7 为唯一权威**:事件名 `<entity>.<action>`,携带**频道内**单调递增 `seq`(业务事务内自 `realtime_channels.last_seq` 分配),断线凭 `resume_from` 从 `realtime_events` 重放,游标过旧下发 `resync_required`;Redis 仅做 fan-out。

| 事件 | 载荷要点 | 触发 |
|------|----------|------|
| `attachment.processed` | 附件 id + `blob_id` + `scan_status` + `mime_type` + `thumbnail_url`(放行时);字段来自所引 blob(真源为 attachment_blobs) | 隔离区 worker 完成嗅探/校验/扫描(放行或拒绝) |
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
- 多个文件并发上传,各自独立进度;全部 `completed` 才允许提交评论(或允许先提交、附件后台补传,二选一看产品策略,默认全部完成方可提交)。注意:`upload_status='completed'` 仅代表字节传完,**不代表可预览**——可预览取决于所引 blob 的 `scan_status`(§2.2)。

### 4.3 已上传附件展示(预览)

> 预览/下载可用性取决于所引 blob 的 `scan_status`(§2.2 可见性闸门);`scan_status='pending'` 时展示「扫描中」占位,不暴露下载/预览按钮(README §6.12)。

- **图片**:评论内内联缩略图(md 尺寸,`blob.scan_status` 放行后加载),点击打开灯箱(加载原图,支持缩放/旋转/下载/在附件区定位);多图自动排成网格。
- **非图片**:文件卡片(类型图标 + 文件名 + 大小 + 上传者 + 下载按钮)。
- **issue 附件区**:图片走缩略图网格,文件走列表;每项 hover 出现「下载 / 删除 / 复制下载链接」。

### 4.4 agent 产出物(核心差异)

- agent 评论里的附件带 agent 头像与「来自 code-reviewer 运行」标记(上传者人类/agent 由 API 响应中 JOIN `members` 计算的 `member_type` 快照判别,README §6.1;无存储判别列),区别于人类上传。
- 截图类产出物默认内联预览(`display='inline'`);报告/日志类以文件卡片呈现(`display='card'`)。

### 4.5 关键交互流程

**上传(签名 URL 直传):**
1. 用户在 composer 拖入/选择/粘贴文件。
2. 前端**预校验**(大小/类型,快速失败),算 `content_hash`(大文件可分块算或跳过由服务端校验)。
3. 前端 `POST /upload-requests` 拿签名 PUT URL(与允许的头)。
4. 前端 `PUT` 字节直传对象存储,监听进度更新 UI;分块则并发 PUT 各 part。
5. 直传成功 → `POST /attachments/{id}/complete`:**服务端仅 HEAD 做存在性/大小初校验并移交隔离区**(blob.scan_status='pending'),不做 MIME 嗅探/缩略图(由附件处理 worker 异步完成,§3.3)。
6. 评论提交时带 `attachment_ids`(或用 complete 时已建的 `link_to` 关联)。
7. 失败/取消:`POST /abort`;后台清理任务定期回收 `pending` 超时对象(`expires_at`)。

> **进度态(上传完成 ≠ 可预览)**:`complete` 后 UI 占位"扫描中,完成后开放下载"(README §6.12);`attachment.processed` 到达后按 `blob.scan_status` 切换为可预览/已拒绝。

**下载:**
1. 用户点附件「下载」。
2. 前端 `GET /attachments/{id}/download` → 拿短时效签名 URL(或 302)。
3. 浏览器直连对象存储下载;URL 过期则重新请求一次。
4. 无权限:返回 403,UI 提示「你没有权限下载此文件」。

### 4.6 安全与可靠性细节(必须实现)

- **私有对象 + 短时效签名**:桶私有,任何访问经签名 URL 或后端代理;签名 60s 量级、绑定方法与对象键。
- **隔离区可见性闸门(CRITICAL)**:`complete` 后对象进隔离区(所引 blob 的 `scan_status='pending'`),**下载/预览/缩略图仅在所引 blob 的 `scan_status IN ('clean','skipped')` 开放**;隔离中 → `403 scan_pending`,感染 → `403 scan_infected`(永久拒绝 + 通知上传者与管理员,README §6.13 critical)。**扫描完成前附件占位呈现「扫描中,完成后开放下载」**(README §6.12 异常态矩阵),不暴露下载按钮。
- **MIME / 哈希以服务端为准**:真实 MIME 由附件处理 worker 从 **magic bytes** 嗅探(不信客户端头、不靠 HEAD);SHA-256 由 worker 全量计算并与客户端声明比对,不匹配置 `blob.scan_status='error'`(`HASH_MISMATCH`,告警 + `attachment.processed` 下发)。下载按真实 MIME 设 `Content-Disposition`,未知/可执行强制 `attachment`。
- **存储键不可枚举**:对象键含 UUID 段,避免遍历猜测。
- **去重独立记录(CRITICAL,blob 真源)**:秒传/去重**只共享 blob**(`attachment_blobs` 行,按 `content_hash` 内容寻址,`ref_count` 原子计数),**始终新建独立 `attachments` 行与独立 `attachment_links`**,绝不复用同一附件记录——每条附件有独立的上传者、关联、生命周期与删除。`ref_count` 经 attachment 创建(+1)/软删(−1)/硬删(−1)在同事务原子增减;删除某附件永不影响共享同 blob 的其他附件。
- **软删除 + 延迟回收(GC 按 blob.ref_count)**:删除先软删附件行(`blob.ref_count` 同事务 −1),对象由后台任务延迟(默认 7 天)清理。**GC 物理删对象的唯一条件是:`blob.ref_count = 0` 且无在途 pending 上传**;只要 `ref_count > 0`,对象就保留。**删除某一附件永不影响共享同一 blob 的其它附件记录**。
- **秒传 possession(RED LINE)**:秒传仅允许调用者**已可读**该 blob(存在至少一条引用该 blob、调用者有读权限的存活 attachment,或调用者即该 blob 某 attachment 的上传者);否则不得凭客户端提供的 hash 短路(防内容探测/越权复用),须完成完整上传,由服务端后置去重(§3.2)。
- **限流**:`upload-requests` 与 `download` 按用户/IP 限流(见 auth Spec)。
- **配额前置校验**:签发 URL 前校验配额。
- **孤儿对象清理**:后台任务按 `idx_attachments_pending` 扫描 `expires_at` 已过且未 `completed` 的记录,置 `expired` 并删对象。孤儿对象清理(未 completed 的 upload)**不受 `ref_count` 约束**(对象尚未纳入 blob 真源)。

---

## 5. 验收标准

### 5.1 功能 — 上传 / 下载

- [ ] 三阶段直传闭环:`upload-request` → 客户端 PUT 直传 → `complete`;字节流不经应用服务器。
- [ ] `upload_status` 状态机正确:仅 `pending`/`uploading` 可 `complete`;对 `completed` 再 `complete` 返回 `409 conflict`;`abort` 置 `failed` 并清理对象。
- [ ] `complete` **仅以 HEAD 做存在性/大小初校验并置 `upload_status='completed'`、移交隔离区**(所引 blob 的 `scan_status='pending'`);`complete` 不声明附件可用、不做 MIME 嗅探/缩略图(由附件处理 worker 异步完成)。
- [ ] **隔离区管线(附件处理 worker,README §2.2;扫描对象为 blob)**:worker 以 SKIP LOCKED 扫 `attachment_blobs(scan_status='pending')`,服务端读取对象字节,从 magic bytes 嗅探真实 MIME 写回 blob 行(客户端伪造 `Content-Type` 无效、HEAD 不足以判定);全量计算 SHA-256 与客户端声明比对,不匹配置 `blob.scan_status='error'`(`HASH_MISMATCH`);AV 扫描后 `blob.scan_status ∈ clean|infected|error|skipped`(`skipped` 仅限 §3.6 纯文本白名单)。
- [ ] **可见性闸门(README §9 T14,逐条)**:① 上传完成(所引 blob `scan_status='pending'`)即请求下载 → 拒绝 `403 scan_pending`;② worker 置 blob `clean` 后可下载;③ blob `infected` 时永久拒绝 `403 scan_infected` 并告警(通知上传者与管理员);④ 缩略图/预览同样受闸门约束;⑤ 扫描完成前 UI 占位「扫描中,完成后开放下载」(README §6.12)。
- [ ] **去重独立记录**:秒传/去重共享同一 blob(`attachment_blobs` 行,按 `content_hash` 内容寻址,`ref_count` 原子计数),但**始终新建独立 `attachments` 行与独立 `attachment_links`**,绝不复用同一附件记录;两条共享 blob 的附件各自有独立上传者/关联/删除。
- [ ] 图片在 blob `scan_status` 放行后异步生成 sm/md/lg 缩略图(写入 blob 行 `thumbnail_keys`);`attachment.processed` 事件下发 `blob_id`/`scan_status`/`mime_type`/`thumbnail_url`(字段来自所引 blob)。
- [ ] 下载走短时效(60s)签名 URL 或 302;对象私有,无公开读;过期可重新申请。
- [ ] 未知/可执行类型下载强制 `Content-Disposition: attachment`,不内联渲染。
- [ ] 分块上传:分片签名、并发 PUT、断点续传(`upload_sessions.parts`)、合并完成。

### 5.1b 功能 — blob 真源与秒传 possession(README §9 T24)

- [ ] **unreadable blob 秒传被拒**:调用者对目标 blob 无读权限(不存在引用该 blob 且有读权限的存活 attachment,且调用者非该 blob 任何 attachment 的上传者)时,`content_hash` 命中不得短路,须签发签名 URL 完整上传;仅凭客户端 hash 无法探测/复用他人内容。
- [ ] **可读 blob 秒传成功**:调用者已可读该 blob 时,秒传新建独立 `attachments` 行指向该 blob,`blob.ref_count` 同事务 +1,跳过字节直传,`upload_status='completed'`。
- [ ] **ref_count=0 前对象不删**:两条共享同一 blob 的附件,删除其中一条(软删,`ref_count` −1)后,另一条仍可下载、元数据完整;GC 仅在 `blob.ref_count = 0` 且无在途 pending 时才物理删对象。
- [ ] **并发同 hash 串行化**:两个请求并发提交相同 `content_hash`,由 `UNIQUE(workspace_id, content_hash)` 保证只建一行 blob,无重复 blob。

### 5.2 功能 — 限制 / 关联 / 删除

- [ ] 超单文件上限返回 `413 file_too_large`;MIME/扩展名不在白名单返回 `415 unsupported_media_type`;MIME 与扩展名不符返回 `400 validation_error`。
- [ ] 配额前置校验,超限返回 `423 quota_exceeded`(签发 URL 前)。
- [ ] `attachment_links` 正确建立:`linked_type ∈ {issue, comment, chat_message}`;同一附件可挂多个宿主;`uq_attachment_link` 生效;`display`/`position` 正确。
- [ ] 列出 issue/comment 附件按 `position` 排序;图片内联、文件卡片展示正确。
- [ ] **多租户复合 FK(README §6.2 / §9 T1)**:`attachments` 建 `UNIQUE(workspace_id, id)`;`blob_id` → `attachment_blobs(workspace_id,id)`、`uploader_id` → `members(workspace_id,id)`、`attachment_links.attachment_id`/`upload_sessions.attachment_id` → `attachments(workspace_id,id)` 均为复合 FK;`attachment_links` 逻辑外键行携带 `workspace_id`;构造跨 workspace 复合 FK 插入被数据库约束拒绝,A 区凭证访问 B 区附件 → 403/404。
- [ ] **去重独立性(删除其一不影响共享 blob 的其它附件记录)**:两条共享同一 blob 的附件,删除其中一条(软删,`blob.ref_count` −1)后,另一条仍可下载、元数据完整;GC 仅在 **`blob.ref_count = 0` 且无在途 pending** 时才物理删对象。
- [ ] 软删除后对象延迟(7 天)回收;无权限删除返回 `403 forbidden`。
- [ ] 后台清理任务回收 `expires_at` 超时的孤儿对象,置 `expired`(孤儿清理不受 `ref_count` 约束)。

### 5.3 功能 — agent 产出物(核心差异)

- [ ] agent runtime 用 API token 走同一套 `upload-requests`/`complete`/关联接口;`uploader_id` 指向该 agent 的 `members.id`,API 响应中计算的 `member_type='agent'` 快照正确(无存储判别列,README §6.1)。
- [ ] agent 评论附件 UI 带 agent 头像与「来自 <agent> 运行」标记,区别于人类上传。
- [ ] 截图类默认 `display='inline'`,报告/日志类 `display='card'`。

### 5.4 非功能

- [ ] **上传大小限制**:单文件上限以 `attachment_quotas.max_file_bytes` 为准(默认 100 MB,图片 25 MB);超限在签发前拒绝,不浪费带宽。
- [ ] **直传性能**:大文件直传不占用应用服务器带宽;`upload-request`/`complete` 接口 P95 < 300ms。
- [ ] **下载时延**:签名 URL 签发 P95 < 200ms;缩略图渲染走对象存储 CDN/直连。
- [ ] **安全**:桶私有、签名短时效绑定方法与键;MIME 由 worker magic-byte 嗅探;SVG 净化/隔离;存储键不可枚举;隔离区可见性闸门(未放行不可下载);错误信息不泄露内部细节。
- [ ] **属主校验**:`complete`/`abort` 仅上传申请者本人可操作(服务端校验 `uploader_id` = 当前 principal),非属主返回 403。
- [ ] **签名 URL 尺寸约束**:签名 PUT URL 绑定声明的 `file_size` 上限(如通过 `Content-Length` 条件或存储侧策略),防止攻击者向 pending 键灌超大对象;配合存储侧生命周期规则兜底清理。
- [ ] **多租户隔离**:所有查询强制带 `workspace_id`;跨 workspace 访问返回 403/404。
- [ ] **可靠性**:孤儿对象清理任务幂等;软删除延迟回收防误删;限流(auth Spec)生效。
- [ ] **可观测**:上传申请/完成/失败、扫描结果、配额拒绝均有审计日志。
