# MES-116 剩余页面族验收证据

本目录记录 Phase 4 平台与管理页的真实 API + Chromium 验收。测试先通过真实接口创建工作区及全部关联实体，再由浏览器以同一账号逐页操作；服务端返回和持久化状态不以 mock 代替。

## 25 个目标页面

| #     | 文件键                                                              | 页面                           |
| ----- | ------------------------------------------------------------------- | ------------------------------ |
| 01–04 | `projects`、`project-detail`、`project-settings`、`cycles`          | 项目列表/详情/设置、周期       |
| 05    | `agent-detail`                                                      | Agent 五 Tab、向导、技能与工具 |
| 06–08 | `squads`、`squad-detail`、`squad-task`                              | Squad 列表/详情/任务           |
| 09–12 | `autopilots`、`autopilot-new`、`autopilot-detail`、`webhook-config` | 自动值守与入站 Webhook         |
| 13–14 | `runtimes`、`runtime-detail`                                        | Runtime 列表/详情              |
| 15–17 | `skills`、`skill-marketplace`、`skill-detail`                       | 技能库/市场/详情               |
| 18–20 | `integrations`、`integration-detail`、`webhook-subscriptions`       | 集成目录/详情/出向订阅         |
| 21–23 | `labels`、`custom-fields`、`data-management`                        | 标签、自定义字段、数据管理     |
| 24–25 | `not-found`、`oauth-error`                                          | 404 与 OAuth 回调错误恢复      |

## 逐页真实交互

- 项目列表切换 list/grid；详情逐一切换 Issues/Milestones/Updates/Dashboard/Overview；设置编辑草稿；周期筛选。
- Agent 逐一切换五 Tab、推进向导到技能步骤；工具 permission 与 enabled 通过真实写接口变更，并直接 GET 读回持久化结果。
- Squad 搜索、打开创建/编辑、筛选 activity、任务 tree/kanban 切换；Autopilot 搜索、编辑器摘要/步骤、test-run、创建一次性 webhook credential。
- Runtime 搜索与 token 轮换；技能搜索、导入入口、市场搜索和详情五 Tab。
- Integration 打开连接表单并切换 Overview/Bindings/Events/Health；展开出向订阅投递详情。
- 标签/字段创建表单、字段类型切换、导出格式选择；404 返回工作台、OAuth 错误恢复入口对已登录用户返回工作台。

所有操作同时收集浏览器 page error 和 HTTP 5xx；任一异常会使专项失败。新工作区 onboarding 在目标页就绪后统一 dismiss，避免遮挡交互和截图。

## 150 图视觉矩阵

矩阵位于 `matrix/<组合>/<01-25 文件键>.png`。每个组合包含上述 25 页，合计 6 × 25 = 150 张：

| 目录                    | 视口     | 主题  | 数量 |
| ----------------------- | -------- | ----- | ---- |
| `matrix/desktop-light/` | 1440×900 | light | 25   |
| `matrix/desktop-dark/`  | 1440×900 | dark  | 25   |
| `matrix/tablet-light/`  | 768×1024 | light | 25   |
| `matrix/tablet-dark/`   | 768×1024 | dark  | 25   |
| `matrix/mobile-light/`  | 390×844  | light | 25   |
| `matrix/mobile-dark/`   | 390×844  | dark  | 25   |

每页截图前均断言 `document.documentElement.scrollWidth <= clientWidth + 1`，因此完整矩阵同时也是六组合的文档级横向溢出门禁。宽表只能在显式内部滚动容器中滚动。

根目录原六张抽样图保留为首轮 smoke 历史，不计入 150 图完成矩阵；`real-projects/`、`real-runtimes/`、`real-autopilots/` 分别保留三条既有真实端到端旅程的逐步证据。

## 可复现入口与结果

- `playwright.mes116.config.ts` + `e2e/real-mes116-page-families.spec.ts`：逐页交互、320px 专项 overflow、六组合 150 图矩阵。
- `playwright.config.ts` + `e2e/ui-baseline.spec.ts`：Ctrl+K → Settings 导航在目标 option 成为 ARIA active 后执行，隔离重复 20 次验证竞态已消除。
- `node scripts/check-evidence-unique.mjs e2e/evidence/mes116/matrix`：检查 150 张矩阵图内容哈希互异。

最终实测结果：MES-116 专项 `8 passed`（含 25 页真实交互、320px overflow 与六组矩阵），Ctrl+K 隔离重复 `20 passed`；矩阵六个目录各 25 张、总计 150 张，唯一性检查为 `150 张截图均唯一`。

真实栈以独立 Compose project、强随机凭据和仅回环发布的 API/WebSocket/Object Storage 端口启动；PostgreSQL 与 Redis 不发布宿主端口。API 与 WebSocket 地址通过 `MES116_API_BASE`、`MES116_WS_BASE` 注入，Vite 仅承载当前源码前端。
