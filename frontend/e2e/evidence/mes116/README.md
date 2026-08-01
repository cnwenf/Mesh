# MES-116 剩余页面族验收证据

本目录记录 Phase 4 剩余平台与管理页的真实 API + Chromium 验收。数据通过真实接口创建，浏览器随后访问并操作同一批实体；没有以 mock 响应代替服务端行为。

## 覆盖范围

- 25 个目标路由：项目/项目详情/设置/周期、Agent 详情与向导、Skills 列表/市场/详情、Squad 列表/详情/任务、自动值守列表/编辑/详情/webhook、Runtime 列表/详情、Integration 列表/详情/subscription、标签、自定义字段、数据导入导出、404 与 OAuth 错误回调。
- 真实交互：列表/网格切换、详情 Tab、Agent 技能与有效工具、向导能力摘要、Squad 树/看板切换、Integration 健康 Tab、webhook subscription 展开。
- 响应式门禁：全部 25 个路由在 320 CSS px 暗色主题下逐页断言文档级 `scrollWidth <= clientWidth`；宽表只允许在自身显式滚动边界内滚动。
- 视觉矩阵：桌面 1440×900、平板 768×1024、手机 390×844，light/dark 各一张主证据；另保留项目、Runtime、自动值守三条既有真实流程的逐步证据。

## 主视觉矩阵

| 证据                                        | 视口     | 主题  | 页面                     |
| ------------------------------------------- | -------- | ----- | ------------------------ |
| `01-desktop-light-project-detail.png`       | 1440×900 | light | 项目详情与长内容         |
| `02-desktop-dark-agent-effective-tools.png` | 1440×900 | dark  | Agent 有效工具与权限     |
| `03-tablet-light-squad-detail.png`          | 768×1024 | light | Squad 详情               |
| `04-tablet-dark-autopilot-editor.png`       | 768×1024 | dark  | 自动值守编辑器与固定摘要 |
| `05-mobile-light-runtime-detail.png`        | 390×844  | light | Runtime 详情             |
| `06-mobile-dark-integration-health.png`     | 390×844  | dark  | Integration 健康状态     |

## 验证结果

- MES-116 专项：3/3 通过（25 路由可达与交互、25 路由 320px overflow、六组合视觉矩阵）。
- 既有真实流程：项目 1/1、Runtime/执行 1/1、自动值守/webhook 1/1 通过；一次性凭据只在断言期间驻留于内存，截图前关闭或消费，teardown 删除工作区并撤销会话。
- 27 张 PNG 内容哈希互异；六张主证据均已人工检查长内容、主题、层级与窄屏可读性。

运行入口：`playwright.mes116.config.ts` 与 `e2e/real-mes116-page-families.spec.ts`。所需 API、WebSocket 地址通过 `MES116_API_BASE`、`MES116_WS_BASE` 指向测试栈，浏览器服务由配置以当前源码启动。
