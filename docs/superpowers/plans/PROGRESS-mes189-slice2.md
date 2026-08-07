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

## 未完成

### 阶段 B 后端
- （无剩余 —— B1/B2/B3/B4/B5/B6 全部完成）

### 阶段 C 前端（17 条）
1. [ ] L92 URL 状态同步（分页/收件箱筛选/详情 Tab/看板草稿态）
2. [x] L93 标签页标题（Issues/IssueDetail/Board/Inbox + 未读 favicon）✅ 已提交 `14e15c64`：InboxBell 权威计数镜像 `state/unreadStore.ts` → `useDocumentTitle` 全局 (N) 前缀 + `applyUnreadFavicon` SVG 徽标（>9 显 9+，卸载清零恢复）；新增 UT 16 例（unreadStore 4 + unreadFavicon 10 + globalChrome 2）+ useDocumentTitle 补 3 例；回归 InboxBell×2/InboxPage/shell-title/IssuesPage/IssueDetailPage/BoardPage 150 例全绿，typecheck/lint 净
3. [ ] L182 离线乐观队列（api/optimisticQueue.ts）
4. [ ] L186 专项恢复入口五条
5. [ ] L202 通知聚合前端（后端归档已做；前端已读组归档视图）
6. [ ] L206 内联审批前端（依赖 B1）
7. [ ] L207 邮件通道前端无直接 UI，后端 B3 承载
8. [ ] L222 收藏入口（useFavorite + ⋯ 菜单）
9. [ ] L242 脏状态保护扩展（autopilot 编辑器/技能编辑/评论草稿）
10. [ ] L247 批量操作 UI（issue 批量转派 + 技能 bulk UI + 成员批量转派复核）
11. [ ] L251 Presence 前端（成员在线 + 看板谁在查看，依赖 B6）
12. [ ] L252 API 契约 UI（429 退避提示 + Deprecation/Sunset 提示）
13. [ ] L480 小队消息着色 + 关联任务 chip
14. [ ] L486 小队导出前端入口（后端已做）
15. [ ] L513 键盘入口一次性提示 + 顶栏占位符
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
