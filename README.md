# Mesh

Mesh 是一个 **AI 原生的团队工作区**:AI agent 被当作真正的队友——像人类成员一样在看板上被分派 issue、在讨论里发评论、修改状态、运行代码,与人类协作完成目标。

## 文档导航

| 文档 | 说明 |
| --- | --- |
| [docs/specs/README.md](docs/specs/README.md) | **整体项目 Spec**:产品定位、整体架构、技术栈、模块总览,并索引全部功能 Spec |
| [docs/specs/features/](docs/specs/features/) | 每个功能一份 Spec:功能描述、数据模型、接口设计、UI/UX、验收标准 |
| [backend/README.md](backend/README.md) | 后端工程骨架与全局契约基础设施(分层、outbox/realtime 唯一写入路径、多租户构件) |
| [docs/research/](docs/research/) | 各模块的设计调研记录(功能 / 数据模型 / 接口 / UI / UX 四维度) |

## 技术栈

- **后端**:Python(FastAPI + SQLAlchemy 2.x + Alembic + PostgreSQL 16 + Redis),实时通信走 WebSocket
- **前端**:React 19 + TypeScript + Vite 单页应用,契约层 / 实时客户端 / 设计系统 / i18n 基线见 [frontend/](frontend/)(选型理由、Quick Start、目录结构见 [frontend/README.md](frontend/README.md))
- **Agent 运行时**:可自托管的执行环境,负责任务领取、代码执行与日志回传

## 仓库结构

| 目录 | 说明 |
| --- | --- |
| [docs/specs/](docs/specs/) | Spec 文档(所有实现的唯一依据) |
| [backend/](backend/) | Python 后端(FastAPI REST + realtime 网关 + outbox/projector worker) |
| [frontend/](frontend/) | Web 前端(阶段 1·B 骨架:工程脚手架、API/实时契约层、设计系统与体验基线、i18n 基线) |
| [tests/](tests/) | 文档级校验脚本(事件词汇、名册入口等) |

开发任何功能前,请先阅读对应的功能 Spec;Spec 是本仓库所有实现的唯一依据。

## 实现状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 工程骨架与全局契约(§6) | ✅ v0.1.0 | 错误信封/分页包络、事件词汇注册表、outbox → realtime 唯一写入路径、多租户构件、realtime 网关骨架 |
| auth 认证核心(auth.md 增量 1) | ✅ v0.2.0 | 注册/登录/会话/MFA/一次性令牌/账号偏好 + 应用路径 RLS 加固 |
| workspace 工作区与多租户基础(workspace.md) | ✅ v0.4.0 | 工作区 CRUD 与 slug 重定向、邀请全生命周期与兑换分离、RBAC 裁决与审计、前缀注册表、租户表 fail-closed RLS;前端设置/邀请页面待前端脚手架增量接通 |
| member 统一成员名册(member.md) | ✅ v0.6.0 | 名册查询/筛选投影(「仅 Agent」为同路由投影)、服务端显示名解析(含 agents.name JOIN)、角色/状态管理与移除转派、guest 项目级可见性、成员事件;名册前端页面(单一 `[ + 新建 Agent ]` 入口 → agent 创建向导,agent.md 增量已接通)、agent 行深链详情页 |
| agent 智能体核心(agent.md 核心五章,阶段 6 首个模块) | ✅ v0.12.1 | `agents`(agent 专有配置:model_config JSONB + 生命周期状态机 + 可见性 + `default_runtime_id` 预留)+ `agent_config_versions`(不可变配置快照;**同父域重叠复合 FK** 保证 active 指针不跨 agent/跨租户串指,PG16 列级 `SET NULL`,§6.2/T27);创建同事务写 agents + members(member_type='agent')+ 首个配置版本(§5.1 原子性);REST 全套(CRUD / 配置更新生成新版本 + 回滚 / 生命周期 :verb 状态机 409 / 可见性 / 所有权转移 / 软删除;§3.4 错误码 + §3.5 分页鉴权;model_config 范围校验 422;avatar https-only §6.16);**分派即触发 outbox 契约**(§3.3/§6.9/§6.11:统一编排入口消费 `issue.assigned` → 护栏闸门(生命周期/名册状态/opt-out/频率/链深度)→ 冻结 §6.11 可复现快照 + §6.5 幂等键写 `execution.enqueue`(runtime 模块消费)→ `execution.queued` 实时帧;跳过发 `agent.trigger_skipped`;§6.9 矩阵逐行:同值 no-op / 改派 supersede+enqueue / 重投幂等);能力入队归一算法(字符串/对象混合声明 → 严格字符串数组 + permission 必填对象数组,T28);`agent.*` 事件经 outbox 唯一路径;前端:四步创建/编辑向导(名册页唯一入口)+ Agent 详情页(概览/配置/技能占位/可见性/历史五 Tab + 生命周期动作 + 回滚)+ 名册 AI 标识与深链(i18n 全外部化)。执行生命周期消费端(task_executions/claim/租约)、技能绑定已由 skill 模块(v0.16.1)接通、autopilot/squad 触发路径属后续模块 |
| runtime 运行时(runtime.md 五章,阶段 6 智能体层 B) | ✅ v0.15.0 | 九张租户表(迁移 0019:`runtimes` 注册/标签/能力/容量·`task_executions` 逻辑执行(幂等键、§6.11 冻结快照、`required_capabilities` 严格字符串数组 CHECK T28)·`execution_attempts` 物理尝试(`UNIQUE(execution_id, attempt_number)` 审计链不复用、`lease_seq` fencing)·日志段偏移索引(对象存储)·checkout·凭证密文·envelope 审计·心跳明细·`approvals` 统一审批 §6.10)+ 全部 §2.4 索引 + RLS + 复合 FK 同租户;`agents.default_runtime_id` 延迟复合 FK 落地。**claim §2.5 单事务原子**(锁 runtime 校验不预扣 → SKIP LOCKED 选任务(租户等值 + 标签/能力 `<@` 服务端值匹配 + 默认 runtime 亲和)→ 选中才 +1 建 attempt;无匹配零写入 T20);双层状态机(终态迁移守卫容量恰释放一次、`lease_seq` 防诈尸 T10);reaper 失联自愈(reclaim 审计保留 + requeue 新 attempt T4 / max_retries / 心跳失联 offline / 审批过期);机器 API(激活 410/心跳下行/claim/renew/logs/checkouts/refetch 上限 3/approvals,`mesh_rt_` 哈希鉴权 + TLS 强制 NEW-M3 + 跨 runtime 403 + 下线吊销 NEW-L2);控制台 API(runtime 生命周期 + 三段式注册签名发布包 + 执行取消/冻结 + 日志 REST/SSE 续传 + 凭证明文只进不出 + 审批收件箱);凭证 fencing(一次性 envelope + 全通道脱敏);checkout 白名单 + SSRF 拒绝(T16);审批唯一续跑协议(attempt 置 cancelled(awaiting_approval)·租约结束·容量释放→批准回 queued 新 attempt 凭 resume_context 续跑,T21);`execution.enqueue` outbox 消费端(MES-60 契约闭环);前端 Runtimes 列表/详情/注册向导/执行详情实时日志(WS 主 + SSE 降级 + offset 续传,值恒 `***`)。红线 e2e T2/T3/T4/T10/T16/T20/T21 真实起服 + worker 并发实测全绿 |
| skill 技能(skill.md 五章,阶段 6 智能体层 C) | ✅ v0.16.1 | **四层解耦**:`skill_sources`(来源 + 信任分级 builtin>user>marketplace>url)、`skills`(定义 + 生命周期 draft/published/deprecated/disabled)、`skill_versions`(**不可变版本快照**,版本号唯一、无 updated_at;指令正文 + 脚本 + 资料 + 触发条件子表,正文经对象存储 `content_ref` 承载)、`skill_installations`(workspace/agent 作用域安装 + 已授予权限子集 + auto_update)、`agent_skills`(agent 绑定,可独立钉住任一历史版本支持灰度/回滚)+ 导入任务台账 `skill_import_tasks`;**同父域重叠复合 FK**(§6.2 第 7 条):`skills.current_version_id`、安装/绑定的版本引用均以重叠唯一键 + 复合 FK 保证「版本属于同一 skill」,current_version 跨 skill / 安装跨 skill 版本 / 绑定与安装不同 skill 均在 INSERT 即拒,`ON DELETE SET NULL (current_version_id)` PG16 列级(§6.2 第 6 条);全表 `UNIQUE(workspace_id, id)` + 同租户复合 FK(→ members/agents)+ fail-closed RLS。**REST 全套(§3.1/§3.3/§3.4)**:技能 CRUD / 版本创建与发布 / 安装·卸载·回滚·启停 / agent 绑定·解绑·启停·优先级 / 导入启动与进度查询 / 审批 / 市场列表;具名错误码(`approval_required`/`capability_not_declared`/`manifest_invalid`/`version_conflict`/`source_unreachable`/`locked`/`conflict`)、游标分页、admin 级写鉴权 403、写类 120/min + 导入/市场 30/min 限流。**导入流水线(异步状态机)**:parsing→validating→sandbox_preview→审批闸门→ready/installed,逐阶段提交并广播 `skill_import.progress`;**SSRF 防护**(§6.16:拒私网/环回/链路本地/云元数据,仅公网或显式主机白名单,逐跳重校验重定向,中性 502 不泄露内部细节);内容一次拉取即冻结(预览即安装内容,无 TOCTOU);**信任分级审批**:marketplace/url 含脚本强制人工逐项确认 + 权限最小化(granted ⊆ required,422),脚本任一 content_hash 变化无论 SemVer 级别一律重审(§4.4 反绕过);自动更新仅跟随脚本哈希不变的纯 PATCH。**事件(§3.5)**:`skill.changed`/`skill_import.progress`/`skill.update_available`/`skill.approval_required` 经 outbox 唯一路径。**§6.11 联动**:绑定态产 `collect_enqueue_context`(skill_versions 映射 + 授权声明)经 `register_skill_context_resolver` 接入 agent 编排入口,入队冻结、在途执行不受后续改绑影响;**自动触发匹配(§4.5)**:关键词/标签多策略打分 × 绑定优先级、Top-N 裁剪、可解释证据、显式指定强制注入。**前端(§4)**:技能库(搜索/来源/状态筛选 + 卡片 + 信任徽标 + 含脚本角标)、技能详情(概览/版本历史/脚本/资料/触发条件五 Tab,脚本正文展开 + 高危能力高亮)、导入向导(来源→预览校验:脚本强制逐项确认 + 权限最小化勾选→审批→安装,进度轮询退化)、技能市场页、agent 详情页「技能」Tab 绑定区(启停/自动触发/优先级/解绑/从库绑定,替换 MES-60 占位);`/skills` 导航入口;i18n 全外部化(zh-CN + en 各 +136 键,目录版本哈希重算)。**v0.14.1 验收整改**:SSRF 重定向绕过与 DNS rebinding 重写为钉死 IP + 手工重定向(配真实套接字/夹具负向用例);版本回滚 UI + diff;自动触发匹配接入编排入口并落注入清单;畸形授权校验 + handler 降级保护;权限分级逃逸拦截;库卡片补字段/徽标;更新流程 UI;导入预览高危高亮;审批响应 §3.2 形状;进度 realtime 主通道 + 进度条;市场预览;优先级可编辑;compose 透传 skill 环境变量;匹配 2 查询 + GIN + 词位;handler 级冻结/停用/毒化集成用例;§4.5 per-skill 互斥 spec 对齐。前端全局覆盖率 97.2/90.09/93.52/97.2(90% 门禁通过) |
| auth 增量2:PAT/API token + 审计查询端点(auth.md §2.5/§3.2/§3.3) | ✅ v0.7.0 | 长期 PAT/API token(独立于会话 JWT、可吊销、可限定 scope/过期)、token 审计与查询端点;补 MES-12 文档欠账,与 CHANGELOG [0.7.0] 对齐 |
| workspace §4 前端 UI 接通(workspace.md §4) | ✅ v0.8.0 | 工作区切换器/创建向导、设置页(基本信息/邀请全生命周期/角色矩阵/危险区)、邀请接受页(四 reason UI 态)、账号登录接通、realtime 会话 JWT 鉴权管道;i18n 全外部化(zh-CN+en) |
| project 项目(project.md) | ✅ v0.10.0 | 项目 CRUD/归档恢复/软删除、健康度与状态留痕(回写 + 事件)、里程碑 CRUD(逾期派生态)、迭代周期 CRUD(auto_roll 自动滚动)、项目成员与私有可见性、项目模板与实例化;前缀注册表同事务排他登记、前缀永久保留(`UNIQUE(workspace_id, key)` 非部分唯一索引 + 注册表双重保证);`UNIQUE(workspace_id, id)` + 同租户复合 FK + RLS;§3.1 全部端点(包络/游标分页/If-Match 乐观并发/错误码);project 前端页面(列表/详情/设置/周期)与实时增量合并;进度聚合与 issue 顺延待 issue.md 增量 |
| label-property 标签与自定义属性(label-property.md §2–§4 定义层 + issue 关联层) | ✅ v0.13.1 | **定义层(v0.11.0)**:标签 + 自定义字段定义 + 枚举选项三表;作用域内命名唯一用 README §6.3 部分表达式唯一索引(`COALESCE(project_id,…)`,禁表级 UNIQUE)、`UNIQUE(workspace_id, id)` + 同租户复合 FK + RLS;定义层独立 CRUD(包络/游标分页/If-Match 乐观并发)与具名错误码(`label_name_taken`/`field_key_taken`/`invalid_field_config`/`field_inactive`)、十种字段类型 + 按类型 config/default 校验;事件 `label.*`/`custom_field.updated`/`custom_field_option.updated` 经 outbox → projector 唯一路径;工作区/项目设置管理 UI(列表/新建/编辑/删除/颜色选择/枚举选项编辑器,i18n 全外部化)。**issue 关联层(v0.13.1,MES-32 余量)**:`issue_labels` 多对多(复合 PK + 同租户复合 FK 双向 CASCADE)+ `issue_custom_field_values` EAV(按类型分列 + JSONB、`num_nonnulls(...) ≤ 1` 兜底、`value_member_id` PG16 列级 `ON DELETE SET NULL`、`(field_def_id, value_*)` 复合/部分索引 + btree_gin 复合 GIN,§2.7/§2.8);issue 打标/移除/整体替换 + 标签合并(迁移去重)端点、字段值整体提交(按类型校验,具名 422:`invalid_field_value`/`field_inactive`/`label_scope_mismatch`/`required_field_missing`)、必填字段在保存 / 状态流转(`required_on`)时就地阻断;事件 `issue.labels_changed`/`issue.custom_field_changed` 经 outbox 唯一路径广播(详情频道恒发 + 工作区频道按可见性);跨项目迁移清除项目私有标签/字段值(工作区级保留,§3.8 清除清单);issue 详情页标签 picker(联想/多选/就地新建/色点)与自定义字段编辑面板(十类型控件,i18n 全外部化);列表/看板按标签/字段筛选的 SQL 子句接点(投影消费由 MES-33 kanban 投影层 v0.13.0 接通);补 `resolve_default_status` 跨租户默认状态解析的确定性回归(该缺陷已由上游 MES-46 M1 收口) |
| issue 工作项(issue.md 五章) | ✅ v0.11.12 | 双层状态(`issue_statuses` 自定义状态 → 稳定 `state_category`,部分表达式唯一索引保证每作用域唯一默认 + 创建事务播种/自检修复;**作用域最后一个默认状态禁删** 409 `last_default_status`;**改 category 同事务全量联动**受影响 issue 的 `state_category`/`completed_at`/`version`/留痕/事件,主键分页);不可变编号(`identifier_namespace_key`/`number` 创建时固定,行锁计数器 `projects.issue_seq` / `workspaces.inbox_issue_seq`,命名空间级 + 工作区级双重唯一,迁移不重编号);父子树(复合自引用 FK + advisory lock 串行化成环检测)、依赖图(`issue_dependencies` 有向图,`blocks` 规范化存储双向展开,并发成环恰一被拒);批量操作(SAVEPOINT 逐项隔离,部分失败 422 逐条列因);跨项目迁移两步式契约(move-preview → 确认单事务:私有状态映射同 category 默认、项目私有 milestone/cycle 清除、`issue.project_changed` 携带映射/清除清单;**确认请求 `version` schema 必填** 422 `move_version_required`;**私有源广播副本脱敏**源侧可读元数据);长文本/JSONB 输入 1 MiB 字节护栏(422 `field_too_large`);§6.9 触发矩阵 no-op diff + `issue.assigned` outbox 预留(agent 编招待 agent.md 接通);§6.14 过滤限制(深度 ≤3 / 条件 ≤20 / `statement_timeout` 兜底);issue 前端页面(列表/详情:乐观更新 + version 冲突收敛、依赖与子项可视化、批量工具条)与实时增量合并(含搜索水位);严格模式状态流转(`status_strict_mode` 设置 + `allowed_transitions` 状态级配置,违规 409 `invalid_status_transition`);标签/自定义字段值关联已由 label-property.md issue 关联层接通(v0.13.1) |
| kanban 看板与视图 —— views 定义层(kanban.md §2/§3 独立切片) | ✅ v0.11.6 | `views` 表(JSONB 投影配置 filters/group_by/sort/display_fields/board_settings)+ 视图 CRUD/复制/WIP 配置/侧栏排序;作用域命名唯一与默认视图唯一为部分表达式唯一索引(§6.3);`UNIQUE(workspace_id, id)` + 同租户复合 FK + RLS;§3.1 独立端点(包络/游标分页/If-Match 乐观并发/配置白名单校验具名码);`view.updated` 经 outbox 唯一写入路径;看板页面 shell(视图切换器/按 group_by 派生列骨架/筛选/排序/WIP 配置面板,§6.12 空态,不接真实 issue 数据)。投影执行/每视图排序/原子 move+WIP 强制/实时增量合并见下 v0.13.0 |
| kanban 看板与视图 —— issue 投影层(kanban.md §3/§4/§5 余量切片) | ✅ v0.13.0 | `view_issue_positions` 每视图手工排序表(复合 FK + RLS,视图间排序隔离);`GET /views/{id}/issues` 分组投影查询(README §6.14 整体游标:每组 count=组内总数 + data=页切片,顶层单一 next_cursor,无每组独立 cursor;`column_target_status` 落点映射;过滤限制 depth≤3/条件≤20 + `statement_timeout` 兜底 → `filter_too_complex`/`query_cost_exceeded`;视图执行读限流 429);`POST /views/{id}/moves` 原子拖拽(单事务:乐观锁 + `pg_advisory_xact_lock` 串行目标列 + 事务内 WIP 计数 + 状态/分组字段变更 + 排序 upsert,跨项目路径与 MES-48 鉴权/脱敏共用 `apply_confirmed_move_in_session`;`block` 超限 422 `wip_limit_exceeded`、`warn` 放行并广播 `view.wip_exceeded`;`group_by=project` 走跨项目迁移两步契约 T22,未确认 422 `move_confirmation_required`);`POST /views/{id}/reorder` 列内排序 + 精度耗尽整列重排;`view.presence` 在线协作事件;实时增量合并(单卡插入/移动/移除,view.updated/重放过期才整板重拉,§6.12 重连重同步态);前端真实数据看板(拖拽乐观 + 409 收敛、WIP block 服务端强制弹回 toast、跨项目预览确认、列底快速创建);vitest **1324 例**全绿、全局覆盖率 **97.14% / 90.33% / 92.86% / 97.14%**、变更语句行 **93.4%** 门禁全绿,Playwright 真实后端走查 6 张存证 + 存证去重校验接入 CI。**label/自定义字段分组与筛选随 label-property 关联层(MES-32)增量落地** |
| MES-46 issue 页面维度安全审核收口(实时客户端加固) | ✅ v0.11.7 | resync `rest` 同源校验(跨源 / 协议相对 / 反斜杠绕过 / 前缀越界 / 不可解析一律拒绝,防 WS 被攻陷或 MITM 时 token 外发)+ 翻页循环上限;实时合并原型污染 sink 隔离(`__proto__`/`constructor`/`prototype` 键跳过 + null 原型载体);422 迁移预览回显与 i18n 外部化补齐 |
| MES-46 多租户隔离维度安全审核收口(MES-50) | ✅ v0.11.8 | M1 默认状态回退补租户过滤(`resolve_default_status` 末路回退补 `workspace_id`,堵 owner 角色回退下的跨租状态名泄露);M2 `issue_activity` 收权 append-only(迁移 0012 REVOKE `mesh_app` UPDATE/DELETE,最小权限对齐 `audit_logs`;不加触发器以免误伤 FK 参照动作) |
| MES-46 安全审核 HIGH×2 修复(MES-48,issue 迁移越权收口) | ✅ v0.11.9 | H1 `POST /issues/{id}/move` 未确认路径补与确认事务完全对称的源/目标鉴权(任何鉴权失败不携带 preview);H2 `POST /issues/bulk` 未确认预览逐条过源读门(越权/不可见项仅回 error marker,不回 plan);L1 项目写门 guest 分支统一 404 堵存在性 oracle;确认迁移强制 `version` 乐观锁 + 迁移清单审计留痕(issue.md §3.8) |
| comment-inbox 评论与收件箱(comment-inbox.md 五章,阶段 5·协作层) | ✅ v0.14.0 | 七表数据模型(复合 FK + 同父域重叠唯一键 §6.2-7 + RLS);评论 CRUD/单层折叠线程/解决·重开/回应/系统活动评论(§3.1,包络·游标分页·If-Match·`Idempotency-Key` 幂等写);服务端 Markdown 渲染 + 白名单净化(markdown-it-py,XSS 向量矩阵实测);@提及 agent = 入队一次执行(§6.9 触发矩阵逐行:同评论同 agent 一次执行·编辑 diff 仅新增入队·移除不取消·`suppress_triggers` 仅通知·链深度护栏 + 审计;`execution.enqueue` outbox + §6.5 幂等键,`task_executions` 落库待 runtime.md);收件箱/通知(§6.13 唯一矩阵:critical/normal 派生·执行成功默认不进箱·quiet hours 穿透(仅弹窗,徽标恒同步)·同组 60s 聚合(排除已归档组)·重读重置规则·自我抑制·静音;fan-out 经 outbox → relay 唯一路径;投递台账 R3 目的地粒度;偏好矩阵(event_type 取值域校验)+ 摘要 sweep;**H1 生产者**:issue assign/状态/字段变更同事务 fan-out(I1/I3/I4)+ creator/assignee 订阅播种 + `due_soon` 受监督 sweep;**C6 智能链接** `#IDENTIFIER` linkify + 引用卡片);实时事件 §3.6(comment.* / reaction.changed / notification.* / inbox.unread_count 经 outbox → projector,`member:{id}:inbox` 频道级授权);前端评论区(composer 触发预览 + 「发布后将触发一次运行」提示 + 抑制开关 + 草稿暂存)、收件箱页与顶栏铃铛、通知偏好、i18n 全外部化 |
| 安全硬化·依赖收口(auth.md §4.1,MES-46 终局独立扫描) | ✅ v0.11.10 | react-router 6.30.4 → 7.18.1(`npm audit --omit=dev` moderate×2 清零:GHSA-wrjc-x8rr-h8h6 反斜杠开放重定向可达项 + GHSA-337j-9hxr-rhxg SSR hydration 未引入项;无其他依赖 major 升级);登录 `?next=` 与 OAuth 往返回跳守卫统一为 `safeNextPath` 单一实现并升级为**浏览器 URL 解析器等价校验**(控制字符/空白预检 + 同源解析,堵 TAB/LF/CR 与 `/\` 反斜杠两类归一化绕过,CVE-2025-68470 同族);残留 GHSA-qwww-vcr4-c8h2(high)仅影响 unstable RSC API,纯客户端 SPA 不适用,已于 v0.12.0 随 React 19 / react-router 8 迁移清零(MES-56) |
| 安全硬化·依赖收口续:React 19 / react-router 8 迁移(MES-56,MES-55 审计例外清零) | ✅ v0.12.0 | react-router 7.18.1 → 8.3.0 清零 GHSA-qwww-vcr4-c8h2(high,RSC CSRF;本站纯客户端 SPA 无该攻击面,随修复版收口使 `npm audit --omit=dev` 全清);连带 React 18.3.1 → 19.2.8(react-router 8 peer 要求 ≥19.2.7)、`@types/react`(-dom)19、Node 引擎 ≥22.22.0(CI Node 20 → 22);v8 移除 `react-router-dom` 包,全仓 43 文件 import 统一为 `react-router`(纯声明式库模式,所用 API 全兼容,零行为变更);typecheck / lint / 构建 / 1274 例单测全绿(覆盖率 97.26%),真实浏览器 e2e 30/30 + 真实后端全栈走查通过 |
| 安全硬化·依赖收口(auth.md §4.1,MES-46 终局独立扫描) | ✅ v0.11.10 | react-router 6.30.4 → 7.18.1(`npm audit --omit=dev` moderate×2 清零:GHSA-wrjc-x8rr-h8h6 反斜杠开放重定向可达项 + GHSA-337j-9hxr-rhxg SSR hydration 未引入项;无其他依赖 major 升级);登录 `?next=` 与 OAuth 往返回跳守卫统一为 `safeNextPath` 单一实现并升级为**浏览器 URL 解析器等价校验**(控制字符/空白预检 + 同源解析,堵 TAB/LF/CR 与 `/\` 反斜杠两类归一化绕过,CVE-2025-68470 同族);残留 GHSA-qwww-vcr4-c8h2(high)仅影响 unstable RSC API,纯客户端 SPA 不适用,清零待 React 19 迁移独立评估(MES-56) |
| attachment 附件(attachment.md 五章,阶段 5 协作层) | ✅ v0.13.2 | 三阶段签名直传(upload-request 签发短时效 PUT → 客户端直传对象存储 → complete 仅 HEAD 初校验并移交隔离区,字节流不经应用服务器);**blob 真源表 `attachment_blobs`**(内容寻址 `UNIQUE(workspace_id, content_hash)` 并发去重串行化、`ref_count` 同事务原子计数、隔离区 `scan_status` 内容级状态机,扫一次全体共享者可见)+ 独立附件记录 `attachments`(会话级 `upload_status`)+ 多态逻辑外键 `attachment_links` + 分块台账 `upload_sessions` + 配额 `attachment_quotas`(§2);隔离区管线 worker(SKIP LOCKED 领取:magic-byte MIME 嗅探 + 全量 SHA-256 校验 + AV 扫描钩子 + sm/md/lg 缩略图,纯文本白名单 `skipped`,§3.3/README §2.2);**可见性闸门**(未放行下载/预览/缩略图 403 `scan_pending`,感染永久拒绝 403 `scan_infected` + critical 审计,README §9 T14);**秒传 possession**(RED LINE:仅已可读该 blob 方可凭 hash 短路,否则强制完整上传 + 服务端后置去重,T24);私有桶短时效签名下载(60s 级、绑定方法与键、未知/可执行强制 attachment);`attachment.processed`/`attachment.deleted` 经 outbox 唯一写入路径;孤儿清理 / 延迟回收 / GC 仅 `ref_count=0`;§3.1 全部端点(§6.14 包络/游标分页/幂等键/限流,人类 JWT 与 agent API token 同一套接口);`UNIQUE(workspace_id, id)` + 同租户复合 FK + RLS;前端附件功能(composer 拖拽/粘贴/进度上传、issue 详情附件区、缩略图网格/灯箱/文件卡片/扫描中占位/agent 产出物标记,i18n 全外部化) |

## Quick Start

前置条件:Docker + Docker Compose。

```bash
docker compose up --build -d
```

服务与端口(可用 `.env` 覆盖,见 [.env.example](.env.example)):

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 前端占位页 | http://localhost:3001 | nginx 静态页,反代 `/api` 与 `/ws` |
| API | http://localhost:8000 | FastAPI REST(`/api/v1`),健康检查 `/healthz`、`/readyz` |
| Realtime 网关 | ws://localhost:8081/ws | WebSocket(连接后首帧认证,详见 docs/specs/README.md §6.16) |
| MinIO 对象存储 | http://localhost:9000(控制台 :9001) | 附件私有桶,仅经短时效预签名 URL 访问(attachment.md §3) |
| PostgreSQL 16 / Redis 7 | 内部网络 | 数据与 fan-out(Redis 非持久真源) |

> **安全提示(务必阅读)**:本 compose 栈**仅限本机开发**。
>
> - 对外端口(8000 / 8081 / 3001)默认绑定 `127.0.0.1`,**仅本机可达**,不暴露到网络;`.env` 只能改端口号,无法改绑定地址——如需对外暴露必须刻意修改 `docker-compose.yml`。
> - `MESH_AUTH_MODE` 默认 `dev`(任意 `mesh-dev:<workspace-id>` 即获该工作区完全访问),这**仅在端口只绑回环时安全**。任何**非本机/生产**使用必须显式设置 `MESH_AUTH_MODE=production`,并提供真实的数据库/Redis 凭据。
> - API 与 realtime 网关以**受限非 owner 角色 `mesh_app`** 连接数据库,使 PostgreSQL RLS 租户兜底在应用连接路径真实生效(§6.2 第 5 条);worker 保留 owner 角色做跨租户 relay/projector/retention。

冒烟验证:

```bash
curl http://localhost:8000/healthz          # {"data":{"status":"ok"}}
curl http://localhost:8000/readyz           # {"data":{"status":"ready","checks":{"database":"ok","redis":"ok"}}}
curl http://localhost:8000/api/v1/ping      # {"data":{"pong":true}}
curl http://localhost:3001/                 # 前端占位页
curl http://localhost:3001/api/v1/ping      # 经 nginx 代理到 API
```

API 启动时自动执行 `alembic upgrade head`(建表 + RLS 策略);worker 进程运行 outbox relay、realtime projector 与保留期清理。

本地开发(不依赖 compose)见 [backend/README.md](backend/README.md);测试:

```bash
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.lock   # 可复现安装(lockfile 为权威来源,CI/Docker 同源)
pip install -e . --no-deps
pytest --cov=mesh --cov-report=term-missing   # 单测 + 真实 e2e(需本地 PostgreSQL 16 与 Redis)
```
