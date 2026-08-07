# MES-189 批次③ 第二切片 PROGRESS（接力清单）

> 分支：`agent/mesh/mes189-slice2-frontend` · 计划：`docs/superpowers/plans/2026-08-07-mes189-batch3-slice2-frontend.md`
> 更新：2026-08-07（续接 run，thrashing 后第二棒）

## 已完成（已提交）

- [x] 阶段 A 全部：HIGH TD-3/DEBT-1 assign owner-only 护栏（`5cdd5b3e`）；TD-1/DEBT-2/TD-4（`f56b5122`）；TD-2 随 `5cdd5b3e` 同文件清偿
- [x] B2 通知自动归档 worker（`3db6de95` checkpoint）✅ 定向测试已验证绿（2026-08-07 续接 run：test_notification_archive 12 例 + 邻域 59+71 例 exit 0）
- [x] B4 小队导出 markdown（`3db6de95`）✅ 同上验证；**注意：squad.md §4.5 导出要求登记（Spec 锚点勘误）尚未确认是否已写入 spec —— 续接者先 grep 确认**
- [x] B5 技能一绑多 agent bulk 端点（`3db6de95`）✅ 同上验证
- [x] TD-5 覆盖率基线记账：随批说明已记录（agent/service.py 88% / comment_inbox/routes.py 89% 为既有基线）
- [x] B4 Spec 补登记：squad.md §3.1 + §4.6 归档导出（含 L486 锚点勘误注记）✅ 已提交
- [x] B3 邮件通道：收件人 locale 渲染（users.settings.locale → workspaces.settings.default_locale → en）+ HTML 转义 + 签名 JWT 一次性打开 token + `GET /inbox/{id}/open` 标已读 302（统一 404 anti-oracle）+ mailer 透传 ✅ 已提交（test_notification_email_channel.py 12 例 + mailer 透传 2 例全绿；邻域回归 test_inbox_service/test_comment_inbox_api/test_issue_notification_producers/test_comment_inbox_supplement 全绿）
- [x] B6 Presence：`member/presence.py`（Redis hash 计数 + TTL，仅 0→1/1→0 边沿广播 member.presence）+ RealtimeSession 接线（首个订阅计在线 / 末订阅或断线离线）+ `GET /workspaces/{ws}/members/presence` 快照 ✅ 已提交（test_member_presence.py 7 例全绿；test_gateway_session/test_realtime_app/test_realtime_auth/test_view_presence/test_member_api/test_member_service/test_member_project_access 回归全绿）
- [x] B1 后端：approval_id 已在 checkpoint `3db6de95` 完成（producer + consumer + UT）；剩前端内联按钮 → 并入阶段 C 第 6 条（L206）
- [x] C-2 L93 标签页标题 + 未读 favicon 徽标（`14e15c64`）✅ 新增 UT 16 例 + useDocumentTitle 补 3 例全绿；邻域回归 150 例全绿；typecheck/lint 净
- [x] C-15 L513 键盘入口可发现性（`22966a11`）✅ UT 8 新增 + 邻域回归全绿；i18n 目录 version 已重算（附重算脚本）
- [x] C-12 L252 API 契约 UI（见未完成区第 12 条注记）✅ 已提交：client 拦截层 429/Deprecation/Sunset 检测 → 契约通知总线（api/notices.ts，429 去抖 + 弃用每会话一次）→ shell/ApiNoticeToasts 桥以 i18n toast 呈现；UT 21 新增，全套 4894 例回归绿，per-file 门禁过
- [x] C-14 L486 小队导出前端入口（`0286840e`）✅ 已提交：squads/api.ts `exportSquadArchive` 独立 fetch（原始 markdown 非包络，Bearer 同构，非 2xx/网络失败归一 MeshApiError）+ 详情页头部 ⋯ Menu「导出归档」（Blob 下载 squad-{id}.md，读权限即可，导出中 disabled）+ 3 键×双语目录（version 重算）；UT 8 新增（含 403/非 API 失败/普通成员可见），全套 4902 例绿，per-file 门禁过
- [x] C-13 L480 小队消息着色 + 关联任务 chip（`a027000b`）✅ 已提交：消息行按 kind 修饰类（指令蓝/汇报绿/闲聊灰/系统虚线/上下文蓝边，语义 token 双主题）+ 指令/汇报带 task_id 渲染「关联任务」chip 深链任务详情；i18n squads.relatedTask×2 目录（version 重算）；UT 3 新增，全套 4905 例绿，per-file 门禁过
- [x] TD 顺手清偿：IssueExecutionsPanel 取消断言竞态修复（`d521b033`）——coverage 负载下偶发 flake，同步断言改 waitFor 等待行状态收敛
- [x] C-6 L206 收件箱行内联审批（`f673b0ac`）✅ Notification 可选 approval_id + InboxApprovalActions（挂载即 GET 审批态，仅 pending 且未过 reaper 惰性窗口渲染批准/拒绝；决定后收敛状态徽标；服务端幂等兜底；approval.decided 帧跨会话收敛）+ InboxRow 行操作区接线 + inbox.css `> button` 子选择器；UT 11 新增（组件 9 + 页面接线 2），inbox 套件 136 例、全套 433 文件/4916 例绿，per-file 门禁过（新文件 stmts 99.2%/branch 91.5%）
- [x] C-8 L222 收藏入口（`b7d89e8a`）✅ 新增 `useFavorites(workspaceId, targetType)`（features/favorites/）：挂载拉成员集合、乐观 toggle（PUT/DELETE 幂等）、失败回滚 + danger toast、workspaceId 缺失不发请求、列表失败降级空集合；五处入口：IssueDetailPage ⋯ 菜单星标条目 / 看板列表 RowActionsMenu（桌面行 + 移动卡）/ ViewSwitcher 视图 ⋯ 菜单条目（删除项前，回调缺省不渲染）/ ProjectDetailPage 头部星标 IconButton（aria-pressed + filled）/ BoardPage 提供 issue+view 双实例下传；favorites.* 4 键×2 目录（version 重算）。顺手清偿：IssueDetailPage 测试阈值等待改 `queueCallCount` 排除 URL 感知旁路（收藏 GET 记入 calls 不消耗盲队列，直数会提前一格放行致 estimate 用例间歇红），估算用例 6/6 复跑全绿；UT 17 新增（hook 6 + 列表 3 + 视图 3 + 项目 2 + 详情 3），全套 434 文件/4930 例绿，per-file 门禁过（27 文件 ≥90%）
- [x] C-1 L92 URL 状态同步（`cd8019b2`）✅ 新增共享 hook `useUrlState(key)`（null/空串删参、replace 缺省、保留其它键、函数式更新）；四面落地：InboxPage 筛选组合 ↔ ?filter= / IssueDetailPage 讨论/活动 Tab ↔ ?tab=（缺省 comments 不占参数）/ IssuesPage 分页 ↔ ?page=（深链游标补齐上限 20 页、loadMore 写参、筛选变更清参、非法值规范化清除）/ BoardPage 视图草稿 ↔ ?draft=（脏草稿序列化、深链恢复、损坏 JSON 结构校验回落 parseViewDraft）；UT 41 新增（useUrlState 7 + Inbox 已随前段 + Detail 3 + Issues 5 + Board 3 + parseViewDraft 9 + ProjectDetail 探针回归），全套 435 文件/4951 例绿，typecheck/lint 净（0 错误、25 基线 warning），per-file 门禁过（28 文件 ≥90%，BoardPage branches 由 89.87% 经 parseViewDraft 直测补齐至达标）
- [x] C-16 L182 离线乐观队列（`08b603f4`）✅ 新增 `api/optimisticQueue.ts`：离线入队/在线直执/在线执行遇 network 错误转队列，FIFO 回放 + 逐项状态机（queued/running/succeeded/failed）+ 重试上限（默认 3）+ 容量护栏（默认 64，超限丢最旧）+ subscribe/remove/clear/dispose；`initOptimisticQueueTriggers` 挂 window online 与可注入 extraTriggers（realtime state→connected）。AppShell 接线：OptimisticQueueContext + useOptimisticQueue、离线横幅待回放计数（StatusBanner `queuedCount` prop，state.offlineQueued）、回放失败 danger toast（去重 ref，state.offlineOpFailed）；i18n 2 键×双语目录（version 重算 en `2a24fee9` / zh `a640841d`）。UT 25 新增（队列 20 + 横幅 2 + shell 接线 3），全套 436 文件/4985 例绿，typecheck/lint 净，per-file 门禁过（30 文件 ≥90%；AppShell branches 91.56%→94.44%、optimisticQueue stmts 99.38%/branch 95.45%）
- [x] C-17 L186 专项恢复入口五条（`6e01d8f3`）✅ 五条逐一核实：① 看板重连指示——已由全局 StatusBanner（offline/reconnecting/resyncing，Outlet 之上覆盖含看板全部页面）+ board-resync-banner 承载（BoardPage.realtime.test.tsx:849-864），不重复加指示；② 日志 offset 续传——已实现且有测（ExecutionDetailPage 日志三段合一：REST 历史 + WS resume_from + SSE 同 offset 续传，client offset 去重；channelCursors.ts）；③ 附件扫描占位——已实现且有测（scanNoticeOf + attachment-scanning-* testid，AttachmentPanel.coverage.test.tsx:239 / AttachmentComposer.coverage.test.tsx:177）；④ 无 runtime 分派提示——**新增** `runtimes/dispatchHint.ts` workspaceHasOnlineRuntime（status=online&limit=1 轻探测，失败/非 2xx 不误报），IssueDetailPage 分派 agent 成功且确定无在线 runtime 时 warn toast（issues.noRuntimeWarning）带 Runtimes 深链，绝不阻断分派；⑤ 审批过期重新发起——**新增** ExecutionDetailPage 终态横幅内 failure_reason=approval_expired 时展示 runtimes.execution.approvalExpiredNote + 关联任务「重新发起」深链（issue_id 为 null 不渲染链接；收件箱侧 ApprovalCard 前段已做）。i18n +2 键×双语目录（version 重算 en `1723f03e` / zh `7f976924`）。UT 10 新增（dispatchHint 4 + ExecutionDetail 3 + IssueDetail 3），全套 437 文件/4995 例绿，typecheck/lint 净（0 错误、25 基线 warning），per-file 门禁过（32 文件 ≥90%，dispatchHint 100/100/100/100）

## 未完成

### 阶段 B 后端
- （无剩余 —— B1/B2/B3/B4/B5/B6 全部完成）

### 阶段 C 前端（17 条）
1. [x] L92 URL 状态同步（分页/收件箱筛选/详情 Tab/看板草稿态）✅ 已提交 `cd8019b2`（见已完成区 C-1）
2. [x] L93 标签页标题（Issues/IssueDetail/Board/Inbox + 未读 favicon）✅ 已提交 `14e15c64`：InboxBell 权威计数镜像 `state/unreadStore.ts` → `useDocumentTitle` 全局 (N) 前缀 + `applyUnreadFavicon` SVG 徽标（>9 显 9+，卸载清零恢复）；新增 UT 16 例（unreadStore 4 + unreadFavicon 10 + globalChrome 2）+ useDocumentTitle 补 3 例；回归 InboxBell×2/InboxPage/shell-title/IssuesPage/IssueDetailPage/BoardPage 150 例全绿，typecheck/lint 净
3. [x] L182 离线乐观队列（api/optimisticQueue.ts）✅ 已提交 `08b603f4`（见已完成区 C-16）
4. [x] L186 专项恢复入口五条 ✅ 已提交 `6e01d8f3`（见已完成区 C-17）
5. [ ] L202 通知聚合前端（后端归档已做；前端已读组归档视图）
6. [x] L206 内联审批前端（依赖 B1）✅ 已提交 `f673b0ac`（见已完成区 C-6）
7. [ ] L207 邮件通道前端无直接 UI，后端 B3 承载
8. [x] L222 收藏入口（useFavorite + ⋯ 菜单）✅ 已提交 `b7d89e8a`（见已完成区 C-8）
9. [ ] L242 脏状态保护扩展（autopilot 编辑器/技能编辑/评论草稿）
10. [ ] L247 批量操作 UI（issue 批量转派 + 技能 bulk UI + 成员批量转派复核）
11. [ ] L251 Presence 前端（成员在线 + 看板谁在查看，依赖 B6）
12. [x] L252 API 契约 UI（429 退避提示 + Deprecation/Sunset 提示）✅ 已提交：notices 总线（429 秒数提示 + 2s 去抖；弃用头每会话一次）+ ApiNoticeToasts 桥（ToastProvider 内，App 层挂载）+ api.* 文案 3 键×2 目录（version 重算）；UT 21 例 + 全套 4894 例绿 + per-file 门禁过；e2e 走查归入阶段 D 四组合
13. [x] L480 小队消息着色 + 关联任务 chip ✅ 已提交 `a027000b`：kind 修饰类五色（双主题语义 token）+ 指令/汇报 chip 深链；UT 3 例；e2e 走查归入阶段 D 四组合
14. [x] L486 小队导出前端入口（后端已做）✅ 已提交 `0286840e`：exportSquadArchive（原始 markdown 独立 fetch + 错误归一）+ 头部 ⋯ Menu 条目（成功/失败 toast，读权限即可）+ i18n 3 键×2 目录；UT 8 例，全套 4902 例绿 + per-file 门禁过；e2e 走查归入阶段 D 四组合
15. [x] L513 键盘入口一次性提示 + 顶栏占位符 ✅ 已提交 `22966a11`：KeyboardHintBanner（Banner onDismiss 通道）+ keyboardHint 本地记忆（localStorage，隐私模式降级不抛错）+ App 浮层 controls 已使用落记忆 + 顶栏占位「搜索或输入命令…（{combo}）」（formatCombo 平台感知）+ 目录 version 重算脚本；UT 8 新增 + TopBar/App/shell 回归 54+37 例全绿，i18n 125 例全绿
16. [ ] L541–543 导入导出 UI（行级进度/413 预警/情境 ⋯ 入口）

### 阶段 D
- [ ] 审计文档 §4.2 18 行移入 §4.1 + 计数；parity 清单闭合改写；L486 锚点勘误注记
- [ ] 证据：docs/evidence/mes-189/ 四组合截图 + real-stack-contract.json 断言
- [ ] 门禁全绿（frontend quality 全套 + backend 全量 + spec-checks）
- [ ] Spec 同步（squad.md 导出登记 ✅、member.md presence 落地说明 ✅、onboarding/import-export 段落待 L541–543 完成后补）
- [ ] push + `gh pr ready` + @Mesh Leader（merge_queue=1，勿自合）

## 环境与基建备忘（续接者必读）

- 测试栈容器仍在运行：`mes189s2-pg`（127.0.0.1:5444, db mesh_test）/ `mes189s2-redis`（127.0.0.1:6411）/ `mes189s2-minio`（127.0.0.1:9111）；凭据见 workdir 根 `testenv.sh`（勿提交/勿外发）
- 定向跑法：`cd Mesh/backend && source <workdir>/testenv.sh && PYTHONPATH=$PWD/src /root/venvs/mesh/bin/python -m pytest <files> -q --no-header -p no:cacheprovider -p no:warnings`
- 防 thrashing：大文件先 grep 定位再读区间；测试定向 filter；每完成一组立即 commit + push
