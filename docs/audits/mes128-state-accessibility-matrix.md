# MES-128 响应式、页面状态与无障碍收口记录

> 核对日期：2026-08-02
> 规范：`docs/specs/features/design-quality.md` §8、§10、§13.5
> 正常态证据：`frontend/e2e/evidence/mes111-b5/manifest.json`
> 真栈证据：`frontend/e2e/evidence/mes111-b5-real/manifest.json`

## 1. 可复现结论

| 门禁         | 可执行覆盖                                                                                                             | 结果                                                                                              |
| ------------ | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 路由         | `App.tsx` 63 个叶子路由与 manifest AST 对账；55 个受保护、8 个公开、12 个权限路由、4 个重定向                          | 无漏项；每个非重定向路由都有 core/extended 正常态 ready assertion，禁止 `access_only`             |
| 正常态视觉   | 13 核心页 × 4 视口 × 2 主题                                                                                            | 104 个基线                                                                                        |
| 异常态视觉   | 13 页 × 7 状态 = 91 个显式 cell                                                                                        | 13 个正常态复用上述基线；73 个适用场景 × 2 主题 = 146 个快照；5 个 N/A 均绑定当前源码证据         |
| 逐页清单     | 28 行 × 桌面/手机 × light/dark                                                                                         | 112/112 PNG；manifest 校验 route、shown scope、backend kind、尺寸与 SHA-256；363 张全局证据均唯一 |
| axe          | 13 个 core + 46 个 extended 正常态，桌面/触屏手机，WCAG 2 A/AA、2.1 A/AA、2.2 AA tags                                  | 所有扫描无 violation；API 4xx/5xx 或 ErrorState 不能冒充正常态                                    |
| Reflow       | core：320/390/640；extended：320/390/640/768/1024/1440 CSS px                                                          | `scrollWidth <= clientWidth`；640px 为 1280px 的 200% 等效宽度                                    |
| 44px         | 所有可见原生控件、链接、`role=button`、`summary`、tab/menuitem、自定义交互控件、checkbox/radio 及其真实关联 label 矩形 | coarse-pointer 下严格检查实际命中矩形，无 selector 跳过                                           |
| 真栈键盘旅程 | production auth、真实 API、PostgreSQL；320×720 与 390×844                                                              | 两档均完成登录→建 issue→非拖拽移卡→评论→切工作区→搜索，并核对 HTTP 与落库                         |
| 媒体/结构    | forced-colors、prefers-contrast、reduced-motion；landmark、overlay、table                                              | 浏览器与静态门禁通过                                                                              |

13 个核心页为登录、工作台、issue 列表、看板、issue 详情、成员、聊天、运行详情、收件箱、
自动值守、集成、洞察和设置。路由正常态测试由 `src/shell/appRouteManifest.ts` 单一清单驱动；
新增叶子路由若未登记或没有可执行正常态 fixture，单测会 fail closed。

## 2. 13 页状态矩阵

`e2e/visual/state-matrix.spec.ts` 以类型化 manifest 固定每页的 `normal`、`loading`、`empty`、
`error`、`long`、`offline`、`permission` 七格。loading 使用延迟真实响应，empty/long 使用可审计
DTO 变换，error 使用具名失败，offline 触发连接状态，permission 使用权限响应；每个场景先等待
专属语义断言，再截图，不能用同一张正常态图片重复填格。

| 页面       | normal | loading         | empty           | error | long | offline | permission              |
| ---------- | ------ | --------------- | --------------- | ----- | ---- | ------- | ----------------------- |
| 登录       | ✅     | ✅              | N/A（任务表单） | ✅    | ✅   | ✅      | N/A（公开路由）         |
| 工作台     | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| issue 列表 | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| 看板       | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| issue 详情 | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| 成员       | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| 聊天       | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| 运行详情   | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| 收件箱     | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| 自动值守   | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| 集成       | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| 洞察       | ✅     | ✅              | ✅              | ✅    | ✅   | ✅      | ✅                      |
| 设置       | ✅     | N/A（乐观更新） | N/A（固定表单） | ✅    | ✅   | ✅      | N/A（所有登录用户可用） |

五个 N/A 不是静态豁免：测试读取其 `source` 并匹配 `evidence`；生产语义改变后旧 N/A 会失败。

## 3. 连续读屏与键盘路径

- 看板增加显式完整列表模式；250 张 fixture 卡片在该模式全部按 DOM 顺序挂载，第 1 与第 250
  张均可读，且保留非拖拽移动操作。默认视觉模式仍保留虚拟化性能。
- Board 与 issue 详情把上下文快捷键注册到同一 provider；最近页面上下文优先，输入框、IME、
  原生 Enter/Space activation 和 `defaultPrevented` 均不被全局快捷键接管（G9）。
- 工作区 `autopilot` flag 有后端类型校验、管理 UI、入口/命令/路由统一门控；真栈同时验证非法
  字符串返回 400、显式 `false` 返回 200 且 PostgreSQL 为 false（G15）。
- `agent.trigger_skipped` 六类原因均解析为本地化 toast，包含可用的 agent/issue 上下文；畸形帧
  fail closed（G17）。
- 可滚动代码/JSON 区域可键盘聚焦；共享 Dialog/Drawer 保留焦点圈定、Esc 关闭和触发点恢复；
  表格 caption/scope、唯一 h1、skip link 与 live region 受静态及浏览器门禁保护。

## 4. 真栈证据边界

`e2e/mes128-real/run-e2e.sh` 每次删除旧容器与卷后构建 7 服务 production 栈；只把前端发布到
`127.0.0.1`，PostgreSQL、Redis、MinIO、API 与 gateway 均无 host port。凭据每次强随机生成，
本地 `stack.env` 为 mode 600 且被忽略。每档最终数据库断言为：1 user、1 active session、
2 memberships、目标 issue `in_progress`/version 2、1 条精确评论、第二工作区 1 条搜索目标 issue。
12 张流程截图、HTTP 状态序列、DB 值、尺寸与 SHA-256 写入真栈 manifest；mock-contract 的
104/146/112 视觉证据不被描述成数据库证据。

## 5. 唯一外部人工阻断

axe、ARIA snapshot、键盘 E2E 和完整 DOM 模式都不能替代目标辅助技术。当前 Linux 执行环境
不能运行 Windows NVDA 或 macOS VoiceOver，因此尚未勾选 `design-quality.md` §13.4 的人工项，
PR 必须保持 Draft。可直接执行并逐格签署的 Windows/Chrome 与 macOS/Safari 手册位于
`docs/audits/mes128-screen-reader-runbook.md`；记录必须包含版本、读序、焦点归还、live announcement、
250 卡连续性以及 320/390 两档六流程结果。

## 6. 旧 token 别名债务

`scripts/legacy-token-baseline.json` 记录精确的 `{file, token, count}` 基线；门禁禁止新增
文件/token 对或增加任一现有计数，删除始终允许。本轮盘点共 **263 次 / 110 个文件-token
对**，只建账，不在别名兼容周期内批量替换。

| 别名              | 次数 | 别名                                        |  次数 |
| ----------------- | ---: | ------------------------------------------- | ----: |
| `--color-primary` |   47 | `--color-primary-contrast`                  |    10 |
| `--color-danger`  |   43 | `--color-danger-contrast`                   |     8 |
| `--color-warn`    |   34 | `--color-warn-bg` / `--color-warn-contrast` | 1 / 6 |
| `--color-success` |   23 | `--color-success-contrast`                  |     7 |
| `--color-info`    |   32 | `--color-info-contrast`                     |     5 |
| `--space-5`       |   32 | `--space-6`                                 |    15 |

删除计划：兼容一个发布周期后另起变更，按语义逐处迁移；不得直接改旧 spacing 的变量值，
因为旧 `space-5/6` 的实际像素含义与新阶梯并不一一相同。

## 7. 复现命令

```bash
cd frontend
npm run test:coverage
npm run test:e2e
npm run check:responsive
npm run check:a11y-contract
npm run check:legacy-token-debt
npm run test:e2e:a11y
npm run test:e2e:visual
npm run check:evidence
cd ..
./frontend/e2e/mes128-real/run-e2e.sh
```
