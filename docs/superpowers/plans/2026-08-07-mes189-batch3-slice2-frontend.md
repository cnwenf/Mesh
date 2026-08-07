# MES-189 批次③ 第二切片实施计划：17 条前端对齐项 + 随批债务

> 日期：2026-08-07
> 分支：`agent/mesh/mes189-slice2-frontend`（基于 main @ c4d35a8f，含 PR #137）
> 依据：MES-189 Issue 描述「工作范围」+ `docs/audits/mes185-interface-alignment-audit.md` §4.2（18 行）+ `docs/specs/frontend/competitor-parity-checklist.md`（行号为 #137 修订后的当前行号）

## 0. 口径对账（§4.2 18 行 = 17 条）

| # | 检查表行 | 主题 | Spec 依据 | 现状勘察结论 |
| --- | --- | --- | --- | --- |
| 1 | L92 | URL 状态同步 | kanban.md §5.1 | issues 列表 q/category/priority/mine/sort 已入 URL；缺：列表分页、收件箱筛选、issue 详情 Tab、看板未保存草稿态 |
| 2 | L93 | 标签页标题 | MES-108 体验 | `useDocumentTitle` 覆盖登录族/设置/洞察/审批；缺：Issues/IssueDetail/Board/Inbox + 未读→标题/favicon |
| 3 | L182 | 离线乐观队列 | README §6.12 | RealtimeClient 断线重连 + 横幅已有；preferences 有 pending 队列；缺通用乐观操作排队+回放 |
| 4 | L186 | 专项恢复入口五条 | README §6.12 | 五条逐一实现（看板重连指示/日志 offset 续传/附件扫描占位/无 runtime 分派提示/审批过期重新发起） |
| 5 | L202 | 通知聚合 | README §6.13 | 后端 60s 窗口聚合 + group_key + count 已有、前端 ×N 已有；缺「已读+过期组自动归档」 |
| 6 | L206 | 内联审批 | agent.md §5.4 | approvals 后端+端点+独立页已有；缺：通知 payload/frame 携带 approval_id + 收件箱行内联批准/拒绝 |
| 7 | L207 | 邮件通道 | comment-inbox.md §4.4、i18n.md §5.1 | SMTP+digest worker+HTML 转义已有；缺：按收件人 locale 渲染、回站内锚点链接+自动标已读 |
| 8 | L222 | 收藏入口 | README §6.19 | favorites 后端完整（PUT/DELETE/GET 四目标）；缺：issue/项目/视图 ⋯ 菜单星标 + 管理 |
| 9 | L242 | 脏状态保护 | 通用 UX | `useDirtyNavigationGuard` 已有 1 处消费；扩到 autopilot 编辑器/技能编辑/评论草稿 |
| 10 | L247 | 批量操作 | 各模块 | 收件箱批量已读/归档✅、邀请多邮箱✅；缺：issue 批量转派 UI（后端已有）、技能一绑多 agent（端点+UI）、成员批量转派入口复核 |
| 11 | L251 | Presence | README §6.12 | agent 忙碌三元组✅（RunStateBadge）、view.presence 后端✅但前端丢弃；缺：成员在线状态、看板谁在查看 UI |
| 12 | L252 | API 契约 UI 面 | cli.md C19/C23 | 客户端 429/Retry-After 解析已有；缺：退避用户可见提示、Deprecation/Sunset 头提示 |
| 13 | L480 | 小队消息着色 | squad.md §4.2 | kind 字段与 tab 已有；缺：蓝/绿/灰/虚线着色 + 指令/汇报「关联任务」标签 |
| 14 | L486 | 小队导出 | squad.md §4.5（**锚点勘误**：现行 §4.5 为实时性与通知，无导出要求）| 检查表要求任务消息+时间线导出 markdown；先补 Spec 登记再实现端点+UI |
| 15 | L513 | 键盘入口可发现性 | onboarding.md §4.2 | ⌘K 面板已有；缺：一次性内联提示（可关闭不再现）+ 顶栏占位符「搜索或输入命令…(⌘K)」 |
| 16 | L541–L543（1 条） | 导入导出 UI | import-export.md §4 | data_jobs 后端完整（含 export_too_large 413）；ImportWizard 有实时进度；缺：作业列表行级进度/状态信号、DataManagementPage 实时刷新、项目页/视图页 ⋯ 情境入口 |

**合计：18 行 → 17 条（L541–L543 三行合 1 条）。**

## 1. 阶段 A：安全 HIGH + 随批债务（优先）

- **HIGH（TD-3=DEBT-1）**：private agent 的 **assign 分派路径**补 owner-only 门。
  - mention 侧已有护栏（`visibility_private` 原因、paused/disabled/archived、频率），参照其实现把 assign 触发路径（issue 分派 → agent 触发评估）接入同一护栏函数；非 owner 分派 private agent → 不触发 + `agent.trigger_skipped(visibility_private)`；UT + e2e 负向断言（roster 枚举 member id 后分派他人 private agent 不触发）。
- **TD-1**：`views/projection.py` 自定义字段 `contains` 转义分支补直接 UT（`%`/`_`/`\` 转义语义）。
- **DEBT-2**：评论路径 404 消息统一（guest/member 同口径，消除残余存在性推断）。
- **TD-2**：`mentions.py` agent_not_found 分支补测 + 不可达 else 清理。
- **TD-4**：README L119 护栏枚举补 `visibility_private`。
- **TD-5**：agent/service.py（88%）与 comment_inbox/routes.py（89%）逐文件覆盖率基线记账（沿用既有基线机制；本切片新增代码仍须 ≥90%）。

## 2. 阶段 B：后端契约增量（服务前端项）

- **B1 通知内联审批（L206）**：`review_requested` fanout 携带 `approval_id`（payload + wire frame）；收件箱行据 `approval_id` + pending 态渲染内联批准/拒绝（复用 `POST /approvals/{id}/approve|reject`）；决定后行内态更新；审批已决/过期/取消 → 按钮不出现（防 stale 操作，服务端已幂等/拒绝）。
- **B2 通知自动归档（L202）**：`已读 + 过期组` 自动归档——新增 worker sweep（配置化阈值，默认 7 天）归档 `read_at 非空 且 created_at/最近聚合 早于阈值` 的通知；`archived_at` 语义=移出主视图可回查；UT + 集成验证。
- **B3 邮件通道（L207）**：digest/realtime 邮件按收件人 `users.settings.locale`（回退 workspace default_locale → en）选择文案模板（en/zh-CN 两套目录化模板，纯文本）；评论预览保持 HTML 转义；邮件正文带回站内深链 + 一次性已读 token（`GET /inbox/{id}/open?token=…` 标已读并 302 回站内锚点；token 签名+过期+单次）。
- **B4 小队导出（L486）**：`POST /workspaces/{ws}/squads/{squad_id}/export`（markdown 归档：任务消息按 kind/时间 + 任务时间线）；鉴权=小队可读；先在 squad.md 登记该要求（锚点勘误说明）。
- **B5 技能一绑多 agent（L247）**：`POST /workspaces/{ws}/skills/{skill_id}/bindings/bulk`（agent_ids 列表，逐项 SAVEPOINT 部分成功语义，复用 issues/bulk 的 error marker 约定）+ UT。
- **B6 成员在线状态（L251）**：gateway 连接维护 member 在线集（Redis + TTL），`member.presence` 广播/REST 快照；看板「谁在查看」接通既有 `view.presence` 帧（前端不再丢弃）。

## 3. 阶段 C：前端 17 条实现（逐条 TDD）

按 §0 表逐条：先单测/组件测（RED→GREEN），再接线页面，最后四组合走查存证。共享基建：
- URL 状态：抽 `useUrlState` 轻量封装（searchParams 序列化），用于 L92 各页。
- 乐观队列：`api/optimisticQueue.ts`（离线排队 + online/重连回放 + 逐项结果标记），L182。
- 收藏：`useFavorite` hook + ⋯ 菜单项（issue 详情/卡片/项目/视图），L222。
- 脏保护：扩展 `useDirtyNavigationGuard` 消费点，L242。
- 恢复入口五条按 README §6.12 矩阵逐一落位，L186。
- 导入导出 UI：DataManagementPage 订阅 `data_job.updated` 行级进度 + 「● 导入中 N/T」信号；ExportDialog 提交 413→前置预警弹窗；项目页 ⋯ 菜单（导出本项目/导入到本项目）与视图 ⋯ 菜单（导出本视图），L541–L543。
- 小队着色：kind→语义 token（info/success/muted/dashed）+「关联任务」chip（task_id 深链），L480。
- 键盘提示：localStorage 一次性标志 + 内联提示条 + 顶栏占位符文案，L513。
- API 契约 UI：429 退避 toast（Retry-After 秒数）+ Deprecation/Sunset 头检测提示（client 拦截层一次性去抖），L252。

## 4. 阶段 D：记账 + 验证 + 交付

1. 审计文档：18 行移入 §4.1 + 证据锚点 + 计数同步（92→109/110，retained→0/残余）；parity 清单 18 行「现状」改写为已闭合 + 锚点；L486 锚点勘误注记。
2. 证据：`docs/evidence/mes-189/` 四组合截图（新能力代表页 × 桌面/手机 × 亮/暗）+ `real-stack-contract.json` 契约断言（内联审批落库、自动归档、邮件 token 标已读、小队导出、技能 bulk 绑定、assign 护栏拦截）。
3. 门禁：前端 quality 全套（npm audit/format/lint/typecheck/覆盖率 ≥90% 逐文件/build/tokens）+ backend 全量（UT+e2e+覆盖率 ≥90%）+ spec-checks；真实 e2e（real-stack 旅程）+ 四组合走查。
4. Spec 同步：squad.md（导出登记）、onboarding/import-export/agent 相关段落与实现对账更新；README 护栏枚举（TD-4）。
5. 提交身份 `cnwenf <cnwenf@outlook.com>` 无 co-author；全绿 → `gh pr ready` → @Mesh Leader 串行合入，不自合。
