# MES-189 批次③ 第二切片 PROGRESS（接力清单）

> 分支：`agent/mesh/mes189-slice2-frontend` · 计划：`docs/superpowers/plans/2026-08-07-mes189-batch3-slice2-frontend.md`
> 更新：2026-08-07（续接 run，thrashing 后第二棒）

## 已完成（已提交）

- [x] 阶段 A 全部：HIGH TD-3/DEBT-1 assign owner-only 护栏（`5cdd5b3e`）；TD-1/DEBT-2/TD-4（`f56b5122`）；TD-2 随 `5cdd5b3e` 同文件清偿
- [x] B2 通知自动归档 worker（`3db6de95` checkpoint）✅ 定向测试已验证绿（2026-08-07 续接 run：test_notification_archive 12 例 + 邻域 59+71 例 exit 0）
- [x] B4 小队导出 markdown（`3db6de95`）✅ 同上验证；**注意：squad.md §4.5 导出要求登记（Spec 锚点勘误）尚未确认是否已写入 spec —— 续接者先 grep 确认**
- [x] B5 技能一绑多 agent bulk 端点（`3db6de95`）✅ 同上验证
- [x] TD-5 覆盖率基线记账：随批说明已记录（agent/service.py 88% / comment_inbox/routes.py 89% 为既有基线）

## 未完成

### 阶段 B 后端
- [ ] B1 内联审批：`review_requested` fanout 携带 approval_id（payload + wire frame）→ 收件箱行内联批准/拒绝
- [ ] B3 邮件通道：按收件人 locale 渲染 digest/realtime 邮件 + 回站内深链 + 一次性已读 token（签名/过期/单次，GET /inbox/{id}/open?token=… 标已读 302）
- [ ] B6 Presence：gateway member 在线集（Redis+TTL）+ member.presence 广播/REST 快照；前端看板接通 view.presence（属阶段 C）

### 阶段 C 前端（17 条，全部未开工）
1. [ ] L92 URL 状态同步（分页/收件箱筛选/详情 Tab/看板草稿态）
2. [ ] L93 标签页标题（Issues/IssueDetail/Board/Inbox + 未读 favicon）
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
- [ ] Spec 同步（squad.md 导出登记、onboarding/import-export 段落）
- [ ] push + `gh pr ready` + @Mesh Leader（merge_queue=1，勿自合）

## 环境与基建备忘（续接者必读）

- 测试栈容器仍在运行：`mes189s2-pg`（127.0.0.1:5444, db mesh_test）/ `mes189s2-redis`（127.0.0.1:6411）/ `mes189s2-minio`（127.0.0.1:9111）；凭据见 workdir 根 `testenv.sh`（勿提交/勿外发）
- 定向跑法：`cd Mesh/backend && source <workdir>/testenv.sh && PYTHONPATH=$PWD/src /root/venvs/mesh/bin/python -m pytest <files> -q --no-header -p no:cacheprovider -p no:warnings`
- 防 thrashing：大文件先 grep 定位再读区间；测试定向 filter；每完成一组立即 commit + push
