# MES-107 前端去脚手架化 / 生产就绪清理 —— 实施计划

日期:2026-07-30 · 负责人:Mesh 程序员 · 协同:MES-106(PR #87,登录守卫;本分支 stack 于其上)

## 目标

1. 首页 `/` 从「骨架演示区」(demoTheme/demoLocale/demoShortcuts/demoStates/demoRealtime + 调用不存在的 `/api/v1/demo/issues`)替换为**真实产品首页(仪表盘)**。
2. 登录页删除 dev 令牌块(`.mesh-login__dev`)与过时 `login.phaseNote`。
3. 全站清理占位 / 演示 / 「即将上线」文案与组件(PlaceholderPage、demo.*、home.demo*、members.add.agentComingSoon 等)。
4. UT ≥ 90%(整体 + 变更代码)+ 真实 e2e(桌面 + 手机宽度、公网 HTTP)+ 文档同步。

## 新首页设计(真实数据,对齐 README §6.14 包络 / issue.md §3.6 频道)

`HomePage.tsx` 重写为工作区仪表盘,三段:

- **Hero**:`home.greeting {name}`(GET /api/v1/users/me)+ 既有 `home.subtitle`。
- **工作区列表**:memberships → 卡片(名称 / slug / 角色徽标 `roles.*`),链接 `/w/:slug`(testid `home-workspace-list` / `home-workspace-<slug>`);空成员 → EmptyState + 「创建工作区」(复用 `CreateWorkspaceWizard`)。
- **活跃工作区 issue 仪表盘**(取 `activeWorkspace(memberships)`):
  - `listIssues(ws, {limit:5, sort:'updated_at', order:'desc'})` 游标分页 + 「加载更多」;
  - 订阅 `workspaceIssuesChannel(ws)` 实时帧,经 `mergeEntityFrame` 增量合并(belongs: visibility.workspace_id 归属);
  - 快捷创建表单 → `createIssue`;行 → `/issues/:id` 深链。
  - 三态:loading(Skeleton)/ error(ErrorState + retry,errorToI18nKey)/ empty。

不保留任何 demo 组件;乐观 409 收敛由 `src/api/__tests__/optimistic.test.ts` 单测覆盖(已存在)。

## 契约 mock(e2e/mock-server.mjs)——去 demo 化

- 新增真实路径 `GET /api/v1/users/me`(mock 用户 + ws-1 membership,role admin)。
- issue 端点改挂真实路径:`GET/POST /api/v1/workspaces/ws-1/issues`、`GET/PATCH /api/v1/issues/:id`(保留游标分页 / since 增量 / If-Match 409 / Idempotency-Key)。
- 测试控制端点更名 `/api/v1/demo/{reset,emit,purge}` → `/api/v1/mock/{reset,emit,purge}`(治具语义,非产品 API)。
- 删除无引用端点:`/api/v1/demo/board`、`/api/v1/demo/filter-limit`、`/api/v1/demo/errors/*`。

## e2e 改写

- `helpers.ts`:login 改走真实邮箱/密码 UI(mock jane@corp.com);控制端点指 `/api/v1/mock/*`;`gotoHomeReady` → 等待 `home-issue-list`。
- `realtime-contract.spec.ts`:testid demo-* → home-*;删除 UI 改名/409 两条(单测已覆盖),其余(增量合并 / 乱序丢弃 / 分页 / 幂等创建 / 离线 resume / resync 对账)经真实仪表盘驱动保留。
- `ui-baseline.spec.ts`:删 demo-theme/ICU/demo-states 三条演示断言(主题经设置页、ICU 经 i18n 单测、错误态经看板基线覆盖);守卫用例改真实登录 + 断言真实首页。
- `auth-smoke.spec.ts`:`demo-theme` 断言 → `home-workspace-list`。
- `real-backend.spec.ts`:dev-token 直填改 localStorage 注入(`mesh.auth.v1`);testid → home-*;配置删 `VITE_MESH_DEMO_CHANNEL`。
- **新增** `e2e/mes107/` 隔离栈(仿 mes106:production 鉴权 + 公网 HTTP)+ `real-mes107-home.spec.ts` + `playwright.mes107.config.ts`,桌面 + Pixel 7 双视口:注册登录 → 首页真实加载(无加载失败 / 无 `[data-testid^=demo-]`)→ 登录页无 `.mesh-login__dev` → 工作区卡片可进入。

## 清理清单

- `LoginPage.tsx`:删 dev 块 / devToken / handleDevTokenSubmit / setToken;更新头注与 shell.css。
- `PlaceholderPage.tsx` + 测试 + `.mesh-placeholder` css:删除(无路由引用)。
- `env.ts`:删 `demoChannel` / `VITE_MESH_DEMO_CHANNEL`;`useOfflinePolling` 改 `channels: readonly string[]`(仅覆盖已订阅频道)。
- i18n(en + zh-CN 同步,catalogs.test 键集一致):删 `demo.*`、`home.demo*` 与演示专用键、`login.phaseNote/tokenLabel/tokenPlaceholder/submit`、`members.add.agentComingSoon`;新增仪表盘键;`catalogs.test.ts` REQUIRED_KEYS 同步。
- `Sidebar` skills 项有导航但 App 无 `/skills` 路由(点了即 404):补 `SkillsPage` 路由。
- `frontend/placeholder/` 过期静态占位目录:删除(无引用)。
- `agents.skills.placeholder*` 文案:核对 agent 技能绑定实现状态后决定改写/保留(若功能已实现则删)。

## 文档

- `frontend/README.md`:移除骨架演示区 / VITE_MESH_DEMO_CHANNEL 叙述,更新 mock 端点表与目录说明。
- 根 `README.md` / specs 中「首页骨架演示区」叙述更新为真实仪表盘。

## 验证门禁

1. `npm run lint` + `tsc --noEmit` + stylelint;
2. `npm run test:coverage`(≥90% 门禁)+ `node scripts/verify-coverage.mjs --base origin/main`(变更代码 ≥90%);
3. `npm run build` 生产构建;
4. mock 契约套件 `npx playwright test`(playwright.config.ts)全绿;
5. mes107 真实栈 e2e 桌面 + 手机双跑全绿;
6. 全站 grep 自查:`/api/v1/demo`、`PlaceholderPage`、`demo-` testid、`phaseNote`、`comingSoon` 零残留(历史计划文档除外)。

## 交付

- 提交身份 cnwenf <cnwenf@outlook.com>,无 co-author;PR 至 main(stack 于 #87 之上,评注说明);完成后交验收员(含手机端)。
