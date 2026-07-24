# 数据导入导出(Import / Export)功能 Spec

| 项目 | 内容 |
|------|------|
| 所属层 | 平台能力层(Platform Capability) |
| 模块 | import-export |
| 依赖 Spec | `issue`(字段全集 §1.2.1、编号命名空间 §1.2.2/§2.4、双层状态 §1.2.3——导入字段映射与编号生成的依据)、`project`(项目实体 §2.2、前缀 `key` 与计数器 `issue_seq`——项目导入与 issue 归属)、`label-property`(标签/自定义字段——映射目标与 `external_ref` 系统字段)、`member`(统一 `members.id`、按邮箱解析成员)、`attachment`(导入源文件上传 / 导出产物与错误报告**走统一附件通道**:签名直传、隔离区扫描、私有签名下载)、`auth`(Bearer/RBAC/限流)、`workspace`(多租户、设置区入口) |
| 被依赖 | `README §11.1` CLI(`mesh export issues …` / `mesh import issues --file … --dry-run` 已立约,本 Spec 为其 REST 真源) |
| 技术栈 | FastAPI + SQLAlchemy 2.x + PostgreSQL 16+ + 对象存储(经 attachment.md 统一附件通道) |
| 状态 | Draft |

> **全局一致性锚点(一律引用 README §6,本 Spec 不重复定义)**
> 1. **存储**:PostgreSQL 16+;表名 snake_case 复数;主键 `id UUID`(默认 `gen_random_uuid()`);`created_at`/`updated_at` 为 `TIMESTAMPTZ NOT NULL DEFAULT now()`(UTC);本模块为**长任务**,任务级状态枚举见 §2.2(与 README §6.4 长任务词汇同源,但 `data_jobs` 不是 `task_executions`——它不触发 agent 运行,仅搬移数据)。
> 2. **成员**:`requested_by` 引用**统一 `members.id`**(人类与 agent 同册,判别 JOIN `members.member_type`,README §6.1)。**本模块不存 `requested_by_type` 等判别列**;API 响应可携带服务端 JOIN 计算的 `member_type` 快照(标注"真源为 members")。导入解析 assignee/reporter 一律落到 `members.id`。
> 3. **多租户**:`data_jobs` 建 `UNIQUE(workspace_id, id)`;对 `attachments`/`members` 的引用一律按 README §6.2 建**复合 FK + 目标表 `UNIQUE(workspace_id, id)`**(attachments/members 均已建)。跨租户 job/附件引用在 INSERT 即被拒绝(README §9 T1 同类)。
> 4. **复合 FK 删除语义**:一切"删除时置空引用"的复合 FK 一律 PG16 列级 `ON DELETE SET NULL (<引用列>)`(仅置空引用列,`workspace_id` 保持不动,README §6.2 第 6 条);对不可悬空的 `requested_by` 采用 `ON DELETE RESTRICT`(成员一律软删除 `members.status='removed'`,RESTRICT 不阻塞正常移除)。
> 5. **接口**:REST 前缀 `/api/v1`;`Authorization: Bearer <token>`;**成功包络 / 游标分页 / 错误信封 / 幂等写 / HTTP 语义以 README §6.14 为唯一权威**(单对象 `{"data":{...}}`,列表 `{"data":[...],"next_cursor":<opaque|null>}`,错误 `{"error":{"code","message","details"}}`),本 Spec 仅列本模块具名错误码与行级错误码。
> 6. **队列 / 投递**:导入校验/执行、导出生成**一律经 transactional outbox**(README §6.6)移交数据作业 worker(`FOR UPDATE SKIP LOCKED` 领取,README §2.2),**禁止**在业务事务外直接解析文件/写实体;幂等键见 README §6.5。
> 7. **实时**:统一实时契约见 README §6.7(频道内 `seq`、`realtime_events` 持久重放、`resume_from`/`resync_required`、唯一写入路径 = 业务事务写 outbox `realtime.publish` → projector);作业进度事件名取**注册表已登记**的 `data_job.updated`(README §6.7 平台能力域)。
> 8. **通知**:作业完成/失败的通知分级**复用 README §6.13 唯一优先级矩阵的 normal/critical 两级**(本模块不另行定义事件分级):成功 = normal,失败 = critical(详见 §3.10)。
> 9. **附件**:导入源文件、导出产物、导入错误报告**全部经 attachment.md 统一附件通道**(签名直传 → 隔离区扫描 → 放行后签名下载);本 Spec 不重述附件状态机,仅声明引用与可见性闸门。
> 10. **编号**:导入 issue **一律走 issue.md §2.4 正常编号生成**(占用创建时所属命名空间计数器,与人工新建无差异),源工具编号经 `external_ref` 系统字段对照;`preserve_identifiers`/重编号模式**明确不做**(§1.3,写死)。
> 11. **国际化**:导出文件的日期/数字本地化格式在导出时于 `params.locale` 声明(README §6.18);存储与传输仍 UTC RFC3339。
> 12. **性能 / 集成测试**:P95/时延指标仅在 README §10 基准下构成验收标准;跨租户复合 FK 拒绝、大文件流式安全等按 README §9 矩阵(T1 同类)必测。

> **核心设计(必须采纳)**
> - **统一作业实体 `data_jobs`**:导入与导出共表,`kind ∈ ('import','export')` 区分;`entity_type ∈ ('issues','projects')`、`format ∈ ('csv','json')`。
> - **导入三步契约**:`import`(建作业 + 上传源文件)→ `import/{id}/validate`(dry-run:映射预览 + 逐行错误报告,**不落库**)→ `import/{id}/run`(确认执行,**部分成功**)。未 dry-run 不得 run。
> - **部分成功语义(写死)**:`run` 逐行处理,合法行落库、非法行跳过并记入错误报告;`succeeded_rows + failed_rows = total_rows`;行级失败不影响任务完成——任务级 `failed` 仅表示"未能完成逐行处理"(无法解析源文件、存储不可达、被取消)。
> - **导出异步**:`export` 建作业即返回,worker 后台流式生成文件并登记为附件;完成后经 `data_job.updated` 通知,下载走 attachment.md 签名 URL。
> - **内存安全(RED LINE)**:源文件解析与导出生成**一律流式读写**(CSV 逐行、JSON 流式解析/写出),**严禁全量载入内存**;错误报告明细在作业行内仅保留前 N 条预览,完整明细写入错误报告附件。
> - **导出产物默认放行**:导出/错误报告为纯文本(`.csv`/`.json`),属 attachment.md §3.6 纯文本免扫白名单,blob `scan_status='skipped'` 即放行,可立即签名下载。

---

## 1. 功能描述

### 1.1 定位

import-export 为工作区提供**数据迁入与迁出**能力:

1. **导入(迁移)**:把从其它工具导出的 issue/project 数据(CSV/JSON)迁入 Mesh。核心是**字段映射**(源列 → Mesh 字段 + 值转换)、**预校验**(dry-run 不落库)、**逐行错误报告**与**部分成功**(不因个别坏行整批失败)。
2. **导出(备份/分析/迁出)**:把工作区/项目/视图过滤后的 issue/project 数据**异步**导出为 CSV/JSON,产物经统一附件通道**私有签名下载**。

本模块是**数据搬运工**,不新增 issue/project 字段语义——一切落库语义(编号、状态、成员引用、父子/依赖)以 issue.md / project.md / label-property.md 为准。

### 1.2 功能点与场景

| # | 功能点 | 说明 | 典型场景 |
|---|--------|------|----------|
| I1 | 导入源文件上传 | 源 CSV/JSON 经 attachment.md 签名直传,登记为 source attachment | 上传从其它工具导出的 `issues.csv` |
| I2 | 字段映射配置 | 源列 → Mesh 字段;值转换规则(状态名→status、成员邮箱→member_id、优先级映射、日期解析);支持自动推断表头 | 把源 `State` 列的 "In Progress" 映射到 category `in_progress` |
| I3 | 预校验 dry-run | `validate` 流式解析 + 应用映射,**不落库**;返回映射预览(前 N 行转换结果)+ 逐行错误 | 正式导入前先看哪些行会失败 |
| I4 | 逐行错误报告 | 每个坏行记 `{row, field, code, message}`;行内保留前 N 条预览,完整明细写错误报告附件 | "第 37 行:assignee 邮箱无人匹配" |
| I5 | 确认执行 + 部分成功 | `run` 逐行落库;合法行成功、非法行跳过;`succeeded/failed` 精确计数 | 1000 行导入成功 980、失败 20 |
| I6 | 编号对照(external_ref) | 导入 issue 走正常编号生成;源编号写入 `external_ref` 系统字段供对照;父子关系经 external_ref 二次解析 | 源 `WEB-123` 迁入后变 `APP-456`,`external_ref='WEB-123'` |
| I7 | 项目导入 | 导入 project 实体(name/key/description/status/health/lead/日期);`key` 占用经前缀注册表排他校验 | 批量迁入若干项目骨架 |
| E1 | 异步导出作业 | `export` 建作业即返回;worker 后台生成 | 导出整项目 issue 不阻塞 UI |
| E2 | 导出格式 | CSV / JSON | 给 BI 工具喂 CSV |
| E3 | 导出范围 | project / workspace / 视图过滤(复用 issue.md/kanban.md 列表查询 filters,嵌套 ≤3、条件 ≤20,README §6.14) | 导出"第 12 迭代未完成 issue" |
| E4 | 签名下载 | 产物登记为 result attachment,经 attachment.md 短时效签名 URL 下载 | 下载 `issues-2026-07-25.csv` |
| J1 | 作业状态/进度查询 | `GET /data-jobs/{id}` 返回 status/计数/错误预览;`data_job.updated` 实时推进度 | 看导出跑到哪了 |
| J2 | 我的作业列表 | 按 requested_by 列出历史导入/导出作业 | 翻出上周的导出再下载一次 |

### 1.3 边界与非目标

**范围内**:issue/project 的 CSV/JSON 导入(映射/校验/错误报告/部分成功)与异步导出(范围/格式/过滤/签名下载);作业状态机、进度与通知;源文件/产物/错误报告经统一附件通道。

**非目标(明确不做 / 由他处承担)**:
- **附件二进制迁移不做**:导入/导出**仅处理实体元数据**(字段、状态、成员引用、标签、自定义字段值);issue/project 上挂载的附件**二进制不随导入迁移**(导出仅含附件元数据清单如文件名/大小,导入不还原附件字节)。
- **评论批量导入不做**(可选增强):本期导入不含评论/讨论历史;列为后续可选增强。
- **增量同步 / 双向同步明确不做**:导入是**一次性迁入**,导出是**一次性快照**;不建立持续同步、不做变更回放、不做冲突合并(YAGNI)。
- **`preserve_identifiers` / 编号重编号明确不做**:导入 issue 一律走正常编号生成(§1.2 I6 / §3.7),不占用、不篡改既有命名空间计数器以"保留源编号"。
- 对象存储部署运维、附件状态机细节 → attachment.md;issue/project/标签/自定义字段的字段语义与落库约束 → 各自 Spec;CLI 命令参数 → README §11.1(本 Spec 为其 REST 真源)。

---

## 2. 数据模型

### 2.1 ER 关系

```
workspaces 1─* data_jobs
data_jobs.source_attachment_id ─复合 FK─► attachments(workspace_id, id)   (导入源文件;export 时为 NULL)
data_jobs.result_attachment_id ─复合 FK─► attachments(workspace_id, id)   (导出产物 / 导入错误报告)
data_jobs.requested_by         ─复合 FK─► members(workspace_id, id)       (发起人,人或 agent 同册)

API 事务: INSERT data_jobs + INSERT outbox_events(data_job.enqueue)   ← 同事务(README §6.6)
outbox relay ──SKIP LOCKED──► 数据作业 worker(README §2.2):
   import.validate → 流式解析源文件 + 映射预览 + 逐行错误(不落库)
   import.run      → 逐行落库(复用 issue/project 服务层)+ 错误报告附件
   export          → 流式查询 + 写文件 + 登记 result attachment
```

### 2.2 `data_jobs`(导入/导出作业,本模块 owns)

| 字段 | 类型 | 约束 / 默认 | 说明 |
|------|------|-------------|------|
| `id` | uuid | PK | 作业 ID |
| `workspace_id` | uuid | NOT NULL, FK→workspaces.id `ON DELETE CASCADE` | 多租户隔离 |
| `kind` | text | NOT NULL, CHECK IN ('import','export') | 作业类型 |
| `entity_type` | text | NOT NULL, CHECK IN ('issues','projects') | 实体类型 |
| `format` | text | NOT NULL, CHECK IN ('csv','json') | 文件格式 |
| `status` | text | NOT NULL DEFAULT 'pending', CHECK IN ('pending','validating','running','completed','completed_with_errors','failed') | 作业状态机(§2.3) |
| `mapping` | jsonb | NOT NULL DEFAULT '{}' | **字段映射**(导入:源列→Mesh 字段 + 值转换规则;导出:字段选择/列顺序,§2.4) |
| `params` | jsonb | NOT NULL DEFAULT '{}' | **任务参数**:导入 `{target_project_id, options, validated_at}`;导出 `{scope, filters, locale, options}`(§2.4) |
| `source_attachment_id` | uuid | NULL,**复合 FK `(workspace_id, source_attachment_id) → attachments(workspace_id, id)` `ON DELETE RESTRICT`** | 导入源文件;**R3:源附件 `RESTRICT` 不可物理删除**——源文件是导入作业审计与幂等重跑的依据,作业存续期间删除源附件被数据库拒绝(409 `source_in_use`,附件仍可经 `deleted_at` 软删除与界面隐藏;此前 `SET NULL` 与「import 必有源文件」CHECK 互斥,实际删除永远被 CHECK 拒绝且语义混乱);export 恒 NULL |
| `source_content_hash` | text | NULL | **R3:源文件内容 sha256**(validate 首次成功时写入并冻结);重跑/恢复时校验源文件未被替换,哈希不一致 → 拒绝续跑并 `422 source_changed`,要求重新 validate(幂等恢复的前提:同一份源) |
| `result_attachment_id` | uuid | NULL,**复合 FK `(workspace_id, result_attachment_id) → attachments(workspace_id, id)` `ON DELETE SET NULL (result_attachment_id)`** | 导出产物 / 导入错误报告(§2.4);产物删除仅置空引用列(列级 SET NULL,README §6.2 第 6 条),作业历史保留 |
| `total_rows` | int | NOT NULL DEFAULT 0, CHECK (total_rows >= 0) | 源文件数据行总数(validate/run 时写入) |
| `succeeded_rows` | int | NOT NULL DEFAULT 0, CHECK (succeeded_rows >= 0) | 成功落库行数(**由 `data_job_rows` 台账聚合驱动,§2.5**) |
| `failed_rows` | int | NOT NULL DEFAULT 0, CHECK (failed_rows >= 0) | 失败(跳过)行数(同上) |
| `error_report` | jsonb | NOT NULL DEFAULT '[]' | 逐行错误**预览**(前 N 条,默认 1000):`[{row, field, code, message}]`(§2.4);完整明细在错误报告附件 |
| `checkpoint` | jsonb | NOT NULL DEFAULT '{}' | **R3:持久恢复点**`{last_committed_batch: <int>, last_row_key: <text>, batch_size: <int>, resumed_count: <int>, resumed_at: <ts>}`——每批提交事务内推进(§3.8);worker 崩溃后新 worker 凭此从**最后一个已提交批次之后**续跑,不重跑已提交批次(幂等兜底见 `data_job_rows`) |
| `lease_owner` | text | NULL | **R3:在途 worker 标识**(worker 实例 id);与 `lease_expires_at` 构成租约,过期作业由 reaper 回收 |
| `lease_expires_at` | timestamptz | NULL | **R3:租约过期时刻**(每批提交时续租);`status='running'` 且租约过期 → reaper 置 `lease_owner=NULL` 并经 outbox 重投恢复事件,新 worker 从 `checkpoint` 续跑——**消除「running 守卫导致崩溃后永久卡住」**(§3.8) |
| `requested_by` | uuid | NOT NULL,**复合 FK `(workspace_id, requested_by) → members(workspace_id, id)` `ON DELETE RESTRICT`** | 发起人(成员软删除,RESTRICT 不悬空,README §6.2) |
| `started_at` | timestamptz | NULL | 进入 running 的时间 |
| `finished_at` | timestamptz | NULL | 到达终态的时间 |
| `failure_reason` | text | NULL | 任务级失败原因(status='failed' 时:`source_unparseable`/`storage_error`/`cancelled`/`export_too_large`…) |
| `created_at` / `updated_at` | timestamptz | NOT NULL DEFAULT now() | |

**表级约束:**
- `UNIQUE (workspace_id, id)` —— 供引用方复合 FK 与租户隔离(README §6.2)。
- `CHECK (succeeded_rows + failed_rows <= total_rows)` —— 计数不变式。
- `CHECK ((kind = 'import' AND source_attachment_id IS NOT NULL) OR (kind = 'export' AND source_attachment_id IS NULL))` —— 导入必有源文件,导出无源文件。

### 2.3 状态机

```
import:  pending ──validate──► validating ──dry-run 完成(回置 pending,写映射预览+error_report+params.validated_at)
         pending ──run(要求 validated_at 非空)──► running ──逐行落库──► completed(failed_rows=0)
                                                              └──────► completed_with_errors(failed_rows>0,含全部失败)
         任意非终态 ──任务级故障──► failed(failure_reason)
export:  pending ──worker 领取──► running ──流式生成+登记附件──► completed
                                                              └──► failed(export_too_large/storage_error/…)
```

- **`completed_with_errors`(行级)**:逐行处理**已完成**,但存在失败行(含 `succeeded_rows=0` 的全部失败)——任务本身跑完,错误在 `error_report` 与错误报告附件。
- **`failed`(任务级)**:未能完成逐行处理——源文件无法解析、对象存储不可达、导出超上限、被取消。**两者不混用**(写死)。
- `validate` 可重复调用(每次 `validating → pending`,覆盖映射预览与 `error_report`,刷新 `params.validated_at`);`run` 之后作业进终态,不得再 validate/run(`409 conflict`)。

### 2.4 JSONB 结构(权威)

**`mapping`(导入:源列→Mesh 字段 + 值转换):**
```json
{
  "columns": [
    {"source": "Summary",        "target": "title",    "transform": {"type": "direct"}},
    {"source": "Description",    "target": "description", "transform": {"type": "direct"}},
    {"source": "State",          "target": "status",   "transform": {"type": "status_by_name", "fallback": "default"}},
    {"source": "Priority",       "target": "priority", "transform": {"type": "value_map", "map": {"Highest":"urgent","High":"high","Medium":"medium","Low":"low","None":"none"}, "default": "none"}},
    {"source": "Assignee Email", "target": "assignee", "transform": {"type": "member_by_email", "on_missing": "null"}},
    {"source": "Due",            "target": "due_date", "transform": {"type": "date_parse", "format": "auto"}},
    {"source": "Labels",         "target": "labels",   "transform": {"type": "list_split", "delimiter": ";", "create_missing": true}},
    {"source": "Key",            "target": "external_ref", "transform": {"type": "direct"}},
    {"source": "Parent Key",     "target": "parent",   "transform": {"type": "parent_by_external_ref"}}
  ],
  "defaults": {"state_category_fallback": "todo"},
  "options": {"strict": false}
}
```
- `target` 取值(issues,对齐 issue.md §1.2.1 字段全集):`title`/`description`/`status`/`priority`/`assignee`/`reporter`/`estimate`/`due_date`/`start_date`/`project`/`milestone`/`cycle`/`labels`/`custom_field_values.<field_key>`/`parent`/`external_ref`。
- `target` 取值(projects,对齐 project.md §2.2):`name`/`key`/`description`/`status`/`health`/`lead`/`start_date`/`target_date`。
- 转换类型:`direct`(直传)、`value_map`(枚举映射,含**优先级映射**)、`status_by_name`(状态名→目标作用域 `issue_statuses`,按名匹配同 category,`fallback='default'` 未匹配落默认状态并记 warning,`fallback='error'` 记行错)、`member_by_email`(邮箱→`members.id`,`on_missing='null'|'error'`)、`date_parse`(解析回 UTC)、`list_split`(标签/多选拆分)、`parent_by_external_ref`(父子经 external_ref 二次解析,§3.7)。
- 导出场景 `mapping.columns` 表达**导出字段选择与列顺序**(`target` 为源字段、`source` 为输出列名)。

**`params`(任务参数):**
```json
// import
{"target_project_id": "<uuid|null>", "options": {"create_missing_labels": true}, "validated_at": "2026-07-25T08:00:00Z"}
// export
{"scope": "project", "project_id": "<uuid>", "filters": {"state_category": ["todo","in_progress"], "cycle_id": "<uuid>"},
 "locale": "zh-CN", "options": {"include_attachments_manifest": true}}
```
- 导出 `scope ∈ ('project','workspace','view')`;`filters` 复用 issue.md §3.2 / kanban.md 列表查询过滤契约(嵌套 ≤3、条件 ≤20,README §6.14)。

**`error_report`(逐行错误预览,前 N 条默认 1000):**
```json
[{"row": 37, "field": "assignee", "code": "unknown_member", "message": "no member matches email 'x@y.com'"}]
```
- 行级 `code` 词汇:`required_field_missing` / `unknown_member` / `unknown_status` / `unknown_label` / `invalid_date` / `invalid_value` / `parent_not_found` / `duplicate_within_file` / `project_key_taken` / `unsupported_value`。
- **完整错误明细**(可能 10 万行)写入**错误报告附件**(`result_attachment_id`,CSV:row/field/code/message 列),`error_report` 行内仅前 N 条预览,避免 JSONB 膨胀(内存安全,§5)。

**`result_attachment_id` 双重用途**:export → 导出产物文件;import → 错误报告文件(validate/run 后生成,即使 0 失败也生成空报告供存档)。

### 2.5 索引与约束

```sql
CREATE TABLE data_jobs (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind                 TEXT NOT NULL CHECK (kind IN ('import','export')),
  entity_type          TEXT NOT NULL CHECK (entity_type IN ('issues','projects')),
  format               TEXT NOT NULL CHECK (format IN ('csv','json')),
  status               TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','validating','running','completed','completed_with_errors','failed')),
  mapping              JSONB NOT NULL DEFAULT '{}',
  params               JSONB NOT NULL DEFAULT '{}',
  source_attachment_id UUID NULL,
  source_content_hash  TEXT NULL,                          -- R3:源文件 sha256(幂等恢复前提)
  result_attachment_id UUID NULL,
  total_rows           INT NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
  succeeded_rows       INT NOT NULL DEFAULT 0 CHECK (succeeded_rows >= 0),
  failed_rows          INT NOT NULL DEFAULT 0 CHECK (failed_rows >= 0),
  error_report         JSONB NOT NULL DEFAULT '[]',
  checkpoint           JSONB NOT NULL DEFAULT '{}',        -- R3:持久恢复点(最后已提交批次/行键)
  lease_owner          TEXT NULL,                          -- R3:在途 worker 租约
  lease_expires_at     TIMESTAMPTZ NULL,
  requested_by         UUID NOT NULL,
  started_at           TIMESTAMPTZ NULL,
  finished_at          TIMESTAMPTZ NULL,
  failure_reason       TEXT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  CHECK (succeeded_rows + failed_rows <= total_rows),
  CHECK ((kind = 'import' AND source_attachment_id IS NOT NULL)
      OR (kind = 'export' AND source_attachment_id IS NULL)),
  -- R3:源附件 RESTRICT——作业存续期间源文件不可物理删除(审计 + 幂等重跑依据)
  FOREIGN KEY (workspace_id, source_attachment_id)
      REFERENCES attachments(workspace_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (workspace_id, result_attachment_id)
      REFERENCES attachments(workspace_id, id) ON DELETE SET NULL (result_attachment_id),
  FOREIGN KEY (workspace_id, requested_by)
      REFERENCES members(workspace_id, id) ON DELETE RESTRICT
);
-- 我的作业 / 工作区作业列表
CREATE INDEX idx_data_jobs_ws_created   ON data_jobs (workspace_id, created_at DESC);
CREATE INDEX idx_data_jobs_requester    ON data_jobs (workspace_id, requested_by, created_at DESC);
-- 在途作业(监控/补偿扫描,非 worker 领取路径——领取经 outbox)
CREATE INDEX idx_data_jobs_active       ON data_jobs (created_at)
  WHERE status NOT IN ('completed','completed_with_errors','failed');
-- R3:租约过期作业回收扫描(reaper)
CREATE INDEX idx_data_jobs_lease_expired ON data_jobs (lease_expires_at)
  WHERE status = 'running';

-- ============ data_job_rows(R3 新增:逐行结果台账 —— 行级幂等键 + 恢复真源)============
CREATE TABLE data_job_rows (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  job_id       UUID NOT NULL,
  row_number   INT NOT NULL CHECK (row_number >= 1),       -- 源文件物理行号(定位/排障)
  row_key      TEXT NOT NULL,                              -- 行级稳定幂等键(见下)
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','created','updated','skipped','failed')),
  target_type  TEXT NULL CHECK (target_type IN ('issue','project')),
  target_id    UUID NULL,                                  -- 落库实体 id(created/updated 时必填)
  error        JSONB NULL,                                 -- {field, code, message}(failed 时必填)
  attempts     INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  CONSTRAINT uq_data_job_rows_job_row_key UNIQUE (job_id, row_key),   -- R3:行级幂等键——重放已提交批次不重复建实体
  CHECK ((status IN ('created','updated') AND target_type IS NOT NULL AND target_id IS NOT NULL)
      OR (status = 'failed' AND error IS NOT NULL)
      OR (status IN ('pending','skipped'))),
  CONSTRAINT fk_data_job_rows_job FOREIGN KEY (workspace_id, job_id)
    REFERENCES data_jobs(workspace_id, id) ON DELETE CASCADE
);
-- 续跑扫描:定位作业内未完成行;台账聚合:按作业统计各状态行数
CREATE INDEX idx_data_job_rows_job_status ON data_job_rows (job_id, status);
```

> **复合 FK 删除语义(R3 修订,README §6.2 第 6 条)**:
> - `source_attachment_id` 用 **`ON DELETE RESTRICT`**:源文件是导入作业审计与幂等重跑的依据,作业存续期间物理删除源附件被数据库拒绝(API 层先译 `409 source_in_use`;附件仍可软删除 `deleted_at` 从界面隐藏)。此前 `SET NULL (source_attachment_id)` 与「import 必有源文件」CHECK 直接互斥——删除附件会先把列置空再被 CHECK 拒绝,实际永远删不掉且错误信息误导。
> - `result_attachment_id` 仍用 PG16 列级 `ON DELETE SET NULL (result_attachment_id)`:产物/错误报告附件物理清理时仅置空引用列,`workspace_id` 保持非空、作业历史行不报错(真实 DELETE 行为见 §5 / README §9 T18 同类)。
> - `requested_by` 用 `ON DELETE RESTRICT`:成员一律软删除(`members.status='removed'`),物理 DELETE 不发生,RESTRICT 永不阻塞且发起人署名不悬空。
>
> **行级稳定键 `row_key`(R3 写死)**:优先取映射出的 `external_ref`(源工具的业务键,如 `JIRA-123`,跨重跑天然稳定);无 `external_ref` 映射或值不唯一时,`row_key = 'row:' || row_number || ':' || sha256(该行全部源字段的规范化 JSON)`——**内容寻址**,同一份源文件(经 `source_content_hash` 校验)重跑得同一键集。`UNIQUE(job_id, row_key)` 使「重放已提交批次」成为幂等操作:台账 upsert(`ON CONFLICT (job_id, row_key) DO UPDATE` 仅在 `status NOT IN ('created','updated')` 时改写)不会重复创建 issue/project(集成测试 T31)。

### 2.6 跨模块外键说明

- `source_attachment_id` / `result_attachment_id` → 复合 FK `attachments(workspace_id, id)`(attachment.md owns,已建 `UNIQUE(workspace_id, id)`)。
- `requested_by` → 复合 FK `members(workspace_id, id)`(member.md owns,README §6.1)。人类/agent 判别 JOIN `members.member_type`,本表不存判别列。
- 导入**落库**的实体经各实体服务层写入(`issues`/`projects`/`issue_labels`/`issue_custom_field_values` 等),其外键约束以 issue.md / project.md / label-property.md 为准,本模块不另建表。

---

## 3. 接口设计

> 鉴权:`Authorization: Bearer <token>`(成员会话或 agent API token)。

### 3.0 分页与鉴权约定

- **包络 / 分页 / 错误信封 / 幂等 / HTTP 语义**:统一以 README §6.14 为唯一权威。创建/动作类端点支持 `Idempotency-Key`(README §6.5/§6.14),重复键返回首次结果(防重复建作业)。
- **权限**:
  - **导出**:对导出范围有**读权限**的成员可发起——`scope='project'` 需项目可见(private 项目需成员);`scope='workspace'` 需 admin/owner;`scope='view'` 需对该视图可读。
  - **导入**(批量写入):需 admin/owner,或对 `target_project_id` 有**写权限**(项目级导入)。
  - **作业查询/下载**:仅 `requested_by` 本人与 workspace admin/owner 可见(导出本人可见自己的作业)。
- **限流**:`import`/`export` 创建按用户/工作区限流(auth.md),触发 `429 rate_limited` 带 `Retry-After`。

### 3.1 端点清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/data-jobs/import` | 建导入作业(源文件经 attachment.md 上传后传 `source_attachment_id`;`mapping` 配置或 `auto_infer=true` 自动推断表头) |
| POST | `/api/v1/data-jobs/import/{id}/validate` | dry-run 校验:流式解析 + 应用映射,**不落库**;返回映射预览 + 逐行错误 |
| POST | `/api/v1/data-jobs/import/{id}/run` | 确认执行导入(**部分成功**);要求已 dry-run |
| POST | `/api/v1/data-jobs/export` | 建导出作业(范围 + 格式 + 过滤);异步 |
| GET | `/api/v1/data-jobs/{id}` | 作业状态 / 进度 / 计数 / 错误预览 |
| GET | `/api/v1/data-jobs` | 作业列表(按 requested_by / kind / status 过滤,游标分页) |
| GET | `/api/v1/data-jobs/{id}/download` | 经 attachment.md 签名下载产物 / 错误报告 |

### 3.2 建导入作业(POST /data-jobs/import)

**请求体:**
```json
{
  "entity_type": "issues",
  "format": "csv",
  "source_attachment_id": "att-src-1",
  "mapping": {"columns": [/* §2.4 */], "defaults": {}, "options": {"strict": false}},
  "auto_infer": false,
  "target_project_id": "proj-1"
}
```
- `source_attachment_id` 必须是调用者**已上传且已放行**(blob `scan_status IN ('clean','skipped')`)的附件;csv/json 属纯文本白名单,通常 `skipped` 放行(attachment.md §3.6)。源附件未放行 → `422 source_not_ready`。
- `auto_infer=true` 时服务端读源文件表头/样例,按字段名相似度生成 `mapping` 草稿(状态/成员/优先级给默认转换),供 UI 预填后再 validate。

**响应体(201):**
```json
{"data": {"id": "dj-1", "kind": "import", "entity_type": "issues", "format": "csv",
          "status": "pending", "source_attachment_id": "att-src-1",
          "mapping": {"columns": [/* 回显或推断结果 */]}, "params": {"target_project_id": "proj-1"}}}
```
> 同事务写 `outbox_events(data_job.enqueue)`(README §6.6);此时**不解析、不落库**,等 `validate`/`run` 触发。

### 3.3 预校验 dry-run(POST /data-jobs/import/{id}/validate)

**动作(经 outbox/worker,§3.8):** status `pending → validating`,worker **流式**读源附件、逐行应用 `mapping`:
- 解析每行 → 按转换规则解析 status/member/priority/date/labels;记录无法解析的行(行级 code,§2.4)。
- **不落任何实体**;把**映射预览**(前 N 行源值→Mesh 值对照)与**逐行错误**写入 `error_report`,`total_rows` 写入,`params.validated_at` 记录校验水位,status **回置 `pending`**。
- 经 `data_job.updated` 下发 validating→pending 与错误计数。

**响应体(200):**
```json
{"data": {"id": "dj-1", "status": "pending", "total_rows": 1000,
          "preview": [{"row": 1, "values": {"title": "登录崩溃", "status": "todo(category)", "assignee": "张三(member_id)"}}],
          "error_report": [{"row": 37, "field": "assignee", "code": "unknown_member", "message": "..."}],
          "failed_rows": 20, "params": {"validated_at": "2026-07-25T08:00:00Z"}}}
```
> dry-run 的 `failed_rows` 为**预测**失败行数;`run` 后为实际值。validate 可重复(覆盖预览/错误/水位)。

### 3.4 确认执行(POST /data-jobs/import/{id}/run)

**前置校验:** `params.validated_at` 非空(已 dry-run),否则 `422 validation_required`;status 须为 `pending`,否则 `409 conflict`;源附件已放行,否则 `422 source_not_ready`;**R3:写入/校验 `source_content_hash`**——validate 后源文件内容被替换(哈希与冻结值不一致)→ `422 source_changed`,要求重新 validate(保证落库内容与 dry-run 预览为同一份源)。

**动作(经 outbox/worker,§3.8,部分成功,逐批幂等):** status `pending → running`(`started_at`,**领取租约** `lease_owner`/`lease_expires_at`),worker 流式重读源文件,**按 `batch_size`(默认 500 行)分批,每批一个数据库事务**(R3 崩溃恢复协议,§3.8):
- 合法行 → 经**实体服务层**创建 issue/project(复用正常落库路径:编号生成 §3.7、状态解析、成员引用、标签/自定义字段、父子二次解析);同事务写 `data_job_rows` 台账行(`status='created'`,`target_type/target_id` 指向新实体,`UNIQUE(job_id, row_key)` 幂等)。
- 非法行 → 跳过,台账行 `status='failed'` + `error` 明细;计入错误报告。
- **批事务末尾同事务推进** `checkpoint`(`last_committed_batch`/`last_row_key`)与 `succeeded_rows`/`failed_rows` 计数、续租(`lease_expires_at = now() + 5min`),并发出 `data_job.updated` 进度;**批间崩溃 = 最后提交的批之前全部落库、之后全部未动**(计数/checkpoint 与实体同事务,不存在"计数已加但实体未建"的漂移)。
- **崩溃后恢复**:新 worker 领取该作业(租约过期回收,§3.8)→ 校验 `source_content_hash` → 从 `checkpoint.last_committed_batch` 之后续跑;重放已提交批次时台账 `ON CONFLICT (job_id, row_key)` upsert 幂等(已 `created` 的行不重复建 issue/project),恢复路径不产生重复实体(集成测试 T31)。
- 终态:全部台账行到终态后,`failed_rows = 0 → completed`;`failed_rows > 0 → completed_with_errors`(含全部失败);任务级故障(源不可解析/存储不可达/被取消)→ `failed`。置 `finished_at`、清空租约;完整错误明细**流式**写入错误报告附件(`result_attachment_id`),`error_report` 行内仅前 N 条。

**响应体(202,异步执行已启动):**
```json
{"data": {"id": "dj-1", "status": "running", "started_at": "2026-07-25T08:01:00Z"}}
```

### 3.5 建导出作业(POST /data-jobs/export)

**请求体:**
```json
{"entity_type": "issues", "format": "csv",
 "scope": "project", "project_id": "proj-1",
 "filters": {"state_category": ["todo","in_progress"]},
 "mapping": {"columns": [{"target":"identifier","source":"编号"},{"target":"title","source":"标题"}]},
 "locale": "zh-CN"}
```
**动作(经 outbox/worker,§3.8,异步):** 建 `pending` 作业 + outbox;worker 领取置 `running`,**流式**执行列表查询(复用 issue.md §3.2 / kanban.md filters,**游标分批拉取,不全量载入**),按 `format` 流式写出到临时对象 → 登记为 result attachment(§3.9)→ `completed`;估算行数/字节超上限 → `failed(failure_reason='export_too_large')`,创建时若可预判直接 `413 export_too_large`。

**响应体(201):**
```json
{"data": {"id": "dj-2", "kind": "export", "status": "pending", "scope": "project", "format": "csv"}}
```

### 3.6 查询 / 列表 / 下载

- **GET /data-jobs/{id}**:`{"data":{...status, total_rows, succeeded_rows, failed_rows, error_report(预览), result_attachment_id, started_at, finished_at, failure_reason...}}`;`member_type` 为 JOIN members 计算快照。
- **GET /data-jobs**:`?kind=&status=&requested_by=` 过滤,游标分页 `{"data":[...],"next_cursor":<opaque|null>}`(README §6.14)。
- **GET /data-jobs/{id}/download**:校验调用者为 requested_by/admin 且 `result_attachment_id` 非空 → **委托 attachment.md 签名下载**(短时效私有签名 URL / 302);产物为纯文本,放行后立即可下载。无产物(如运行中)`404 not_found`;无权限 `403 forbidden`。

### 3.7 导入语义:编号处理(写死)

- **导入 issue 一律走 issue.md §2.4 正常编号生成**:在 `target_project_id` 的命名空间内行锁自增 `projects.issue_seq`(无项目则 `workspaces.inbox_issue_seq`),`identifier = identifier_namespace_key || '-' || number`,**与人工新建语义完全一致**,占用目标命名空间计数器;`UNIQUE(workspace_id, identifier_namespace_key, number)` / `UNIQUE(workspace_id, identifier)` 兜底不重号。
- **源工具编号经 `external_ref` 对照**:`external_ref` 是导入服务在首次导入时**按工作区幂等创建**的预留系统自定义字段(经 label-property.md 自定义字段机制,字段 key 固定 `external_ref`,类型 text);源编号经 `mapping`(`target='external_ref'`)写入,供迁移对照与父子解析,**不参与编号语义、不加唯一约束**。
- **父子关系二次解析**:第一遍创建全部 issue(暂不连父),第二遍按 `parent_by_external_ref` 以源父键查 `external_ref` 回填 `parent_id`;无法解析的父引用记 `parent_not_found` 并降级为顶层(不因父缺失整行失败)。
- **`preserve_identifiers` / 占用或重编号既有命名空间以"保留源编号"——明确不做**(§1.3)。

### 3.8 执行经 outbox / worker(README §6.6 / §2.2)

- 建作业事务**同事务** INSERT `outbox_events`(`event_type='data_job.enqueue'`,payload 含 `data_job_id`/`kind`/动作,幂等键 `sha256(data_job_id \| action)`,README §6.5)。
- outbox relay `FOR UPDATE SKIP LOCKED` 领取 → 分发数据作业 worker;worker 以 `SELECT … WHERE id=$1 AND status IN (期望前置态) FOR UPDATE SKIP LOCKED` 锁定作业行**幂等推进**(重复投递因状态守卫无副作用)。
- **R3 领取即租约**:worker 锁定作业行的同事务写 `lease_owner=<worker-id>`、`lease_expires_at=now()+5min`、`checkpoint.resumed_count+1`(非首次领取时);每批事务续租。**不存在无租约的 `running`**——崩溃后卡 `running` 的作业必然租约过期。
- **R3 租约回收(reaper)**:补偿扫描 `idx_data_jobs_lease_expired`(`status='running' AND lease_expires_at < now()`)→ 同事务置 `lease_owner=NULL`(状态保持 `running`,**不回退计数**——计数与 checkpoint 与实体同事务提交,天然一致)+ 经 outbox 重投 `data_job.resume`(幂等键 `sha256(data_job_id \| 'resume' \| 上次 checkpoint 批次号)`)→ 新 worker 领取后从 `checkpoint.last_committed_batch` 续跑。**消除两类永久故障**:①「`running` 守卫使重投不再执行 → 作业永久卡住」(租约过期即可被新 worker 领取);②「重跑已提交批次 → 重复建 issue/project」(checkpoint 跳过 + `data_job_rows.UNIQUE(job_id, row_key)` upsert 幂等双保险)。
- worker 崩溃在**批事务提交前** → 该批整体未落库,新 worker 重跑该批(幂等);崩溃在**提交后、outbox 进度事件发布前** → 进度事件由 outbox 补投(at-least-once),`data_job.updated` 可能重复推送,客户端按 `checkpoint`/计数收敛。
- **R3 源文件校验**:领取时(首次与恢复皆然)重算源附件内容 sha256 与 `source_content_hash` 比对,不一致 → 作业 `failed(failure_reason='source_changed')`(源在 validate 后被替换,不可安全续跑)。
- **禁止**在业务事务外"顺手"解析文件或写实体(评审硬约束,README §6.6)。

### 3.9 产物登记为附件(经 attachment.md 统一通道)

- 导出产物 / 导入错误报告由 worker **服务端写入对象存储**并登记 `attachments` 行(`uploader_id = requested_by`,blob 经 attachment.md blob 真源 + `ref_count` 原子计数),再回填 `data_jobs.result_attachment_id`。
- csv/json 属 attachment.md §3.6 **纯文本免扫白名单** → blob `scan_status='skipped'` 即放行,产物**默认可下载**(无"扫描中"等待);魔数嗅探 + SHA-256 校验仍由附件 worker 完成。
- 下载经 attachment.md 私有签名 URL(短时效、绑定方法与对象键);**未放行不放行**(纯文本白名单产物默认 `skipped` 放行,语义与 attachment.md 可见性闸门一致)。

### 3.10 通知(只引用 README §6.13 唯一矩阵,不自定义分级)

> **R3 修订**:此前本模块为 data job 自行定义成功/失败分级,而 README §6.13 canonical 矩阵没有 data job 行——违反「§6.13 为唯一通知矩阵」。R3 先在 README §6.13 补入 **data job 三行**(失败 critical / 部分成功 normal 进箱 / 成功默认不进箱),本模块**只引用不定义**:

| 作业结果 | 矩阵行(README §6.13) | 行为 |
|----------|------------------------|------|
| `completed`(成功) | **data job 成功 = normal,默认不进收件箱** | 留数据作业页(toast + 下载入口);仅当 `requested_by` 在 `notification_preferences` 显式订阅 `data_job_finished` 时进箱;不穿透 quiet hours、不重置未读;邮件 none→订阅后 digest |
| `completed_with_errors`(部分成功) | **data job 部分成功 = normal,进收件箱** | 收件人 `requested_by`(有失败行需人工处理,故默认进箱);不穿透 quiet hours、不重置同组未读;邮件 digest;文案附"成功 N / 失败 M,下载错误报告" |
| `failed`(任务级失败) | **data job 失败 = critical** | 进收件箱(`requested_by`),**穿透** quiet hours,**重置**同组未读,邮件 realtime;附 `failure_reason` |

- 通知 fan-out 经 outbox(README §6.6)→ notifications(comment-inbox.md owns,携带服务端按 §6.13 派生的 `priority`);实时推送经 §6.7 唯一写入路径。
- **本模块不得另行定义事件分级**;data job 通知的进箱/穿透/重置语义以 README §6.13 矩阵为唯一实现依据(集成测试 T25 扩至 data job,T32)。

### 3.11 实时事件(README §6.7 注册表)

| 事件 | 频道 | 载荷要点 | 触发 |
|------|------|----------|------|
| `data_job.updated` | `data_job:{id}` | `id`/`status`/`total_rows`/`succeeded_rows`/`failed_rows`/`result_attachment_id`/`failure_reason` + `visibility`(workspace_id) | status 迁移、每批进度、终态 |

- 频道 `data_job:{id}` 订阅时逐资源授权(仅 requested_by/admin,README §6.7);频道字符串不是隔离边界,`realtime_channels.workspace_id` 复合 FK + RLS 兜底。
- 事件经业务事务写 outbox `realtime.publish` → projector 分配频道 seq(唯一写入路径,README §6.6/§6.7)。

### 3.12 错误码(README §6.14 具名)

| HTTP | code | 场景 |
|------|------|------|
| 400 | `mapping_invalid` | 映射配置非法:未知 `target` 字段、转换类型缺失、必填列未映射(如 issue 无 title 映射) |
| 400 | `validation_error` | 请求字段非法(format/scope/entity_type 越界等) |
| 401 | `unauthorized` | token 缺失/失效 |
| 403 | `forbidden` | 无权限(非 job 属主/admin、无目标项目写权限、无导出范围读权限) |
| 404 | `not_found` | job 不存在或跨租户、产物未生成时 download |
| 409 | `conflict` | 状态机不允许(对 running/终态 job 再 validate/run;重复 run) |
| 413 | `export_too_large` | 导出估算/实际超行数或字节上限 |
| 422 | `validation_required` | import `run` 前未 dry-run(`params.validated_at` 为空) |
| 422 | `source_not_ready` | 源附件未放行(blob scan 未 clean/skipped)或无法解析 |
| 429 | `rate_limited` | 创建作业触发限流(带 `Retry-After`) |
| 502 | `storage_error` | 对象存储不可达(不泄露内部细节) |

> 行级错误码(`error_report[].code`,非 HTTP 错误)见 §2.4:`required_field_missing`/`unknown_member`/`unknown_status`/`unknown_label`/`invalid_date`/`invalid_value`/`parent_not_found`/`duplicate_within_file`/`project_key_taken`/`unsupported_value`。

---

## 4. UI/UX

> 异常态矩阵(loading/empty/permission denied/offline/stale/partial failure/retry)按 README §6.12 实现;脉冲动画/颜色不作唯一状态信号(叠加图标/文字,如"● 导入中 980/1000")。

### 4.1 入口

- **设置 → 数据管理**(管理员区,admin/owner,README §6.12):作业列表(导入/导出历史、状态、计数、重新下载),导入/导出主入口。
- **项目页 / 视图页**(情境入口):有读权限的成员从「⋯」菜单发起「导出本项目/本视图」;有写权限者从项目「⋯」发起「导入到本项目」。

### 4.2 导入向导(分步,可回退)

1. **上传**:选择源 CSV/JSON,经 attachment.md 签名直传(进度条);完成显示文件名/行数预估。csv/json 纯文本默认 `skipped` 放行,无需"扫描中"等待。
2. **映射配置**:左列源字段(自动推断表头)、右列 Mesh 字段下拉;每个映射行展示**值转换预览**(如 "State: 'In Progress' → in_progress(todo)");状态/成员/优先级转换规则可展开调整;`external_ref` 默认映射源编号列。
3. **dry-run 错误报告**:`validate` 后展示"共 N 行,可导入 X,将跳过 Y";错误表(行号/字段/原因),可下载完整错误 CSV;允许返回第 2 步改映射后重新 validate。
4. **确认导入**:展示最终映射与计数,「确认导入」→ `run`。
5. **进度**:`data_job.updated` 实时推进"成功 N / 失败 M / 共 T"进度条(文字 + 图标)。
6. **结果**:成功 → "已导入 X 条";部分成功 → "成功 X,跳过 Y" + 「下载错误报告」+ 「查看导入的 issue」深链;失败 → 任务级原因 + 重试入口。

### 4.3 导出(异步)

1. **范围选择**:project / workspace / 当前视图过滤;预览匹配行数。
2. **格式 + 字段**:CSV/JSON;可选导出列(默认核心字段集);locale 声明(本地化日期/数字,README §6.18)。
3. **提交**:建作业,UI 提示"导出进行中,完成后通知你",可关闭弹窗。
4. **进度/下载**:`data_job.updated` 推进度;`completed` 后出现「下载」按钮(attachment.md 签名 URL,过期自动重签);收件箱收到 normal 通知深链到下载。

### 4.4 关键交互细节

- **部分成功可视化**:导入结果页逐行成功/失败标记(README §6.12 partial failure),失败项给原因与错误报告下载。
- **幂等**:重复点「确认导入」不重复建作业(`Idempotency-Key` + 状态守卫);按钮在 running 期间禁用并显进度。
- **大文件**:上传/解析/导出全程流式,UI 显示行级进度而非"卡死转圈";超大导出在范围选择阶段预估并提示收窄(`export_too_large` 前置预警)。

---

## 5. 验收标准

### 5.1 导入 — 映射 / 校验 / 部分成功

- [ ] **映射校验拒绝非法行并报告行号**:dry-run 对每个坏行产出 `{row, field, code, message}`,行号准确(1-based 数据行);映射配置非法(未知 target/缺必填映射)返回 `400 mapping_invalid`。
- [ ] **值转换正确**:状态名→目标作用域 `issue_statuses`(`fallback='default'` 未匹配落默认状态 + warning);成员邮箱→`members.id`(`on_missing='null'` 置空、`'error'` 记行错);优先级经 value_map→`none/low/medium/high/urgent`;日期解析回 UTC。
- [ ] **dry-run 不落库**:`validate` 后数据库无新增 issue/project;`total_rows`/预测 `failed_rows`/映射预览/`error_report`/`params.validated_at` 正确写入;可重复 validate 覆盖结果。
- [ ] **未 dry-run 不得 run**:`params.validated_at` 为空时 `run` 返回 `422 validation_required`。
- [ ] **部分成功语义(计数准确)**:`run` 后 `succeeded_rows + failed_rows = total_rows` 且与逐行结果逐一对账;合法行落库、非法行跳过;`failed_rows=0 → completed`,`failed_rows>0 → completed_with_errors`(含全部失败);任务级故障(源不可解析/存储不可达)→ `failed` 且 `failure_reason` 正确——行级失败**不**误判为任务 `failed`。
- [ ] **编号对照**:导入 issue 走 issue.md §2.4 正常编号生成(占用目标命名空间计数器,`UNIQUE(workspace_id, identifier)` 不重号);源编号写入 `external_ref` 系统字段(首次导入按工作区幂等创建该字段定义);父子经 external_ref 二次解析,无法解析的父引用记 `parent_not_found` 并降级顶层。
- [ ] **项目导入**:project 实体字段映射落库;`key` 经前缀注册表排他校验,冲突记 `project_key_taken`(README §6.3)。
- [ ] **错误报告附件**:完整逐行错误(可 10 万行)流式写入错误报告附件(`result_attachment_id`),`error_report` 行内仅前 N 条预览(默认 1000)。

### 5.2 导出

- [ ] **异步导出**:`export` 建作业即返回 `pending`;worker 后台流式生成;`data_job.updated` 推进度;完成后 `result_attachment_id` 非空。
- [ ] **范围/格式/过滤**:`scope ∈ project/workspace/view`;csv/json 输出正确;`filters` 复用列表查询契约(嵌套 ≤3、条件 ≤20,超限 `400 filter_too_complex`,README §6.14)。
- [ ] **导出文件经 attachment 签名下载(私有,未 clean 不放行)**:产物登记为 attachment,经 attachment.md 短时效私有签名 URL 下载;csv/json 纯文本白名单 → blob `scan_status='skipped'` 放行(默认可下载);未放行附件下载被可见性闸门拒绝;无权限 download → `403 forbidden`。
- [ ] **超上限**:`export_too_large` 在创建预判或运行时正确触发(`413` / `failed`)。

### 5.3 通知 / 实时

- [ ] **data job 通知按 README §6.13 唯一矩阵分发(R3,T25 扩至 data job / T32)**:`failed` = critical(进 `requested_by` 收件箱、穿透 quiet hours、重置同组未读、邮件 realtime);`completed_with_errors` = normal 进箱(不穿透、不重置、digest,文案附成功/失败数 + 错误报告下载);**`completed`(成功)默认不进收件箱**(留数据作业页;仅显式订阅 `data_job_finished` 才进箱且不重置已读组);通知 `priority` 字段由服务端按 §6.13 派生,本模块**无任何自定义分级表述**(§3.10 只引用矩阵)。
- [ ] **实时**:`data_job.updated` 经 outbox→projector 唯一写入路径登记,频道内 seq 单调;订阅逐资源授权(非属主/admin 订阅被拒)。

### 5.4 多租户 / 安全 / 内存

- [ ] **跨租户 job/附件复合 FK 拒绝(README §9 T1 同类)**:`data_jobs` 建 `UNIQUE(workspace_id, id)`;`source/result_attachment_id → attachments(workspace_id,id)`、`requested_by → members(workspace_id,id)` 均复合 FK;构造跨 workspace 复合 FK 插入被数据库约束拒绝;A 区凭证访问 B 区 job/download → 403/404。
- [ ] **真实 DELETE 行为(R3 修订,README §9 T18 同类)**:物理清理某 attachment 时,**`data_jobs.source_attachment_id` 经 `ON DELETE RESTRICT` 拒绝删除**(作业存续期间源文件不可物理删,API 层 `409 source_in_use`;软删除不受影响);`result_attachment_id` 经列级 `ON DELETE SET NULL (result_attachment_id)` 仅置空引用列,`workspace_id` 保持非空、行不报错;删除 workspace 级联其 data_jobs 与 `data_job_rows`(集成测试 T31)。
- [ ] **崩溃恢复与行级幂等(R3,集成测试 T31)**:① import 执行中(第 K 批提交后)杀 worker → reaper 在 `lease_expires_at` 过期后回收租约,新 worker 领取并**从 `checkpoint.last_committed_batch` 续跑**;② 前 K 批已建实体**不重复创建**(`data_job_rows.UNIQUE(job_id, row_key)` upsert 幂等 + checkpoint 跳过双保险),最终 `succeeded_rows` = 台账 `created` 行数、与实体实际数量一致;③ 源文件在 validate 后被替换 → 恢复领取时 `source_content_hash` 校验失败,作业 `failed(source_changed)`;④ 作业不会因「`running` 守卫」永久卡住(租约过期即可被重新领取);⑤ 删除源附件被 RESTRICT 拒绝。
- [ ] **大文件分片/内存安全(流式读写,不全量载入)**:源文件解析(CSV 逐行 / JSON 流式)与导出生成(游标分批查询 + 流式写出)全程流式;在 README §10 数据规模(单作业 10 万行)下内存占用平稳,不因单作业 OOM;错误报告流式写附件,行内 `error_report` 有上限;`data_job_rows` 台账逐批写入,内存不随文件总行数增长。
- [ ] **幂等**:重复 `run`/`validate` 经状态守卫无副作用;`Idempotency-Key` 重复建作业返回首次结果;outbox 重投不产生重复落库(幂等键 + 状态守卫 + 行台账,README §6.5/§6.6)。
- [ ] **属主/权限**:非 requested_by/admin 无法查看/下载他人作业(`403`);导入需目标写权限、导出需范围读权限。
- [ ] **源附件属主校验(M-2)**:`source_attachment_id` 必须是调用者已上传(`uploader_id` = 调用者)或调用者对附件链接目标有读权限的附件,否则 `403`;不可凭附件 ID 引入他人上传的文件(与 attachment.md complete/abort 属主校验先例一致)。
- [ ] **可观测**:建作业/校验/执行/失败/下载均有审计日志;错误信息不泄露堆栈/SQL/内部 ID(README §6.14)。
