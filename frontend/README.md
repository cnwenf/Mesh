# Mesh 前端

Mesh 的 Web 单页应用(SPA)。本目录是阶段 1·B 的前端地基:工程脚手架、API/实时客户端契约层、设计系统与体验基线骨架、i18n 基线、路由/布局/状态骨架。**契约语义以 `../docs/specs/README.md` §3.2/§6.5/§6.7/§6.12/§6.14/§6.16/§6.18 与 `../docs/specs/features/i18n.md` 为唯一权威。**

## 选型理由

Spec(§3.2)不约束前端框架,仅要求 SPA、乐观更新 + 服务端版本校验、WebSocket 增量合并、离线降级轮询。选型与理由:

| 选型 | 理由 |
| --- | --- |
| **React 19 + TypeScript 5** | 生态最成熟、类型安全的组件模型;团队招聘与社区资料成本最低 |
| **Vite 6** | 开发启动/HMR 快,生产构建(Rollup)成熟;Vitest 同源配置 |
| **react-router 8** | 事实标准路由;支持规范深链(§6.12 深链模式);v7 收口 6.x 开放重定向审计项(GHSA-wrjc-x8rr-h8h6 / GHSA-337j-9hxr-rhxg),v8 清零 RSC CSRF 审计项(GHSA-qwww-vcr4-c8h2);v8 起 import 自 `react-router`(`react-router-dom` 包已移除),要求 React ≥19.2.7 / Node ≥22.22.0 |
| **zustand 5** | 轻量全局状态(偏好/鉴权/实时状态),无 Provider 嵌套负担,易测试 |
| **react-intl 7** | 原生 ICU MessageFormat,直接满足 §6.18 的 CLDR 复数/占位符要求 |
| **原生 Intl API** | 日期/数字/相对时间/时区本地化零依赖(§6.18 时区化仅展示层) |
| **原生 CSS 自定义属性(设计 token)** | 「暗色 = 整组语义 token 替换」(§6.12)与 CSS 变量模型天然同构,无运行时开销 |
| **Vitest + Testing Library** | 与 Vite 同源;jsdom 组件测试;v8 覆盖率 ≥90% 门禁 |
| **Playwright** | 真实浏览器 e2e(主题/语言/快捷键/增量合并/断线重放逐项真实操作验证) |

数据获取不引入额外库:§6.14 的包络解析、游标分页、乐观并发与 409 收敛由 `src/api` 的自研契约层实现(机制骨架 + 测试,业务接入在各模块 Issue)。

## Quick Start

```bash
cd frontend
npm install          # 需要 Node ≥22.22.0(react-router 8 引擎要求)
npm run dev          # http://127.0.0.1:5173
```

开发默认连接本地 mock 契约服务端(`e2e/mock-server.mjs`,与后端 v0.1.0 线缆协议
逐帧对齐的忠实镜像:§6.14 包络、§6.7 实时契约——首帧鉴权 `{op:'auth',token}` →
`auth_ok`、`{op:'event',channel,seq,event,payload}`、`subscribed{channel,last_seq}`、
resume_from 重放、resync_required + REST 对账),供骨架演示与 e2e 使用:

```bash
node e2e/mock-server.mjs   # http://127.0.0.1:8901(dev 鉴权:mesh-dev:<workspace-uuid>)
```

连接真实后端(v0.1.0 已合入 main)用 `.env.local` 覆盖:

```
VITE_MESH_API_BASE_URL=http://127.0.0.1:8000
VITE_MESH_WS_BASE_URL=ws://127.0.0.1:8081
VITE_MESH_DEMO_CHANNEL=workspace:<workspace-uuid>:issues   # 演示区订阅频道
VITE_MESH_POLLING_INTERVAL_MS=30000                        # 离线降级轮询间隔
VITE_MESH_OAUTH_PROVIDERS=mock                             # 第三方登录按钮组(逗号分隔 ID;dev 默认 mock,生产默认空)
```

第三方登录(auth.md §4.1/§4.5):登录页按 `VITE_MESH_OAUTH_PROVIDERS` 渲染「使用第三方
账号登录」按钮组(vendor 中立,不绑定厂商);点击经后端 `/auth/oauth/<id>/start` 302
往返,提供商回跳前端 `/auth/oauth/callback/<id>` 交换会话凭证。与真实后端联调 dev
`mock` 提供商时,后端须将该回调 URI 列入精确白名单(`MESH_OAUTH_MOCK_REDIRECT_URIS`,
如 `http://localhost:5173/auth/oauth/callback/mock`,开放重定向防护 M1)。

## 真实后端联调验证

`e2e/real-backend.spec.ts` 对真实后端栈(postgres + redis + api + worker + gateway,
`MESH_AUTH_MODE=dev`,token `mesh-dev:<workspace-uuid>`)以真实浏览器验证:首帧鉴权、
经真实 outbox → relay → projector → Redis fan-out 的实时帧增量合并、断线重连
`resume_from` 重放补齐、游标过旧 → `resync_required` → REST `/api/v1/realtime/events`
对账 → 无感恢复。事件注入经真实生产路径(INSERT `outbox_events` → worker 投影),
保留窗口清理经 SQL DELETE(与后端 e2e T6 同法)。

```bash
# 前置:仓库根目录起后端栈(docker compose up postgres redis api worker gateway,MESH_AUTH_MODE=dev)
npx playwright test --config playwright.real.config.ts
```

> 注:后端 v0.1.0 未开 CORS(生产经 nginx 反代同源部署),该联调配置以
> `--disable-web-security` 启动浏览器,仅联调验证用途。

## 质量命令

```bash
npm run lint            # ESLint 9(flat config)
npm run typecheck       # tsc --noEmit
npm run test            # vitest 单元/组件测试
npm run test:coverage   # 覆盖率(整体 lines/functions/branches/statements ≥90% 门禁)
node scripts/verify-coverage.mjs --base origin/main   # 新增/变更代码覆盖率 ≥90% 校验
npm run build           # 生产构建(tsc -b + vite build)
npm run test:e2e        # Playwright 真实浏览器 e2e(自动拉起 mock 服务端与 dev server)
```

CI:`.github/workflows/frontend.yml` 在 `frontend/**` 变更时跑 lint → typecheck → test:coverage → 新增代码覆盖率校验 → build → e2e。

## 目录结构

```
frontend/
├── e2e/                      # Playwright e2e + 契约 mock 服务端(mock-server.mjs)
├── scripts/verify-coverage.mjs   # 新增代码覆盖率校验(git diff × coverage 交集)
├── index.html                # 入口(内联防主题闪烁脚本)
└── src/
    ├── main.tsx / App.tsx    # 入口与 Provider 组装
    ├── env.ts                # 运行时配置(VITE_MESH_API_BASE_URL / VITE_MESH_WS_BASE_URL / VITE_MESH_OAUTH_PROVIDERS)
    ├── types/                # 共享契约类型:包络(§6.14)/实时帧(§6.7)/骨架实体
    ├── api/                  # API 客户端契约层(§6.14)
    │   ├── client.ts         #   Bearer 鉴权、三类包络解析、If-Match、Idempotency-Key(§6.5)
    │   ├── errors.ts         #   统一错误信封 → MeshApiError(code 具名分发)
    │   ├── pagination.ts     #   keyset 游标分页 hook
    │   ├── optimistic.ts     #   乐观更新 + 服务端版本校验 + 409 收敛
    │   └── filters.ts        #   过滤限制(深度 3 / 条件 20)与 filter_too_complex / query_cost_exceeded
    ├── realtime/             # 实时客户端(§6.7/§6.16)
    │   ├── RealtimeClient.ts #   首帧鉴权 auth/auth_ok(token 绝不进 URL query,对齐后端
    │   │                     #   v0.1.0)、每频道 last_seq、resume_from 重放、
    │   │                     #   resync_required → REST 对账、指数退避重连、online/offline 感知
    │   ├── channelCursors.ts #   每频道游标持久化
    │   ├── merge.ts          #   增量合并(完整变更字段 + visibility 归属 + updated_at 防回退)
    │   ├── pollingFallback.ts#   WS 断开 → seq 水位轮询对账端点降级(§3.2,AppShell 自动编排)
    │   └── useRealtime.ts    #   React 绑定(连接状态机)
    ├── i18n/                 # i18n 基线(§6.18)
    │   ├── catalogs/         #   ICU MessageFormat 消息目录(en 权威源 + zh-CN)
    │   ├── negotiate.ts      #   协商链:显式参数 → users.settings.locale → 工作区默认 → 系统语言 → en
    │   ├── catalogLoader.ts  #   ETag 版本缓存 + 缺 key 三级回退
    │   ├── format.ts         #   日期/数字/相对时间本地化 + 时区化展示 + 输入解析回 UTC
    │   └── I18nProvider.tsx  #   react-intl 接线 + 开发期缺译可见标记
    ├── design/               # 设计系统与体验基线骨架(§6.12)
    │   ├── tokens*.css       #   语义 token(亮/暗两套,均过 WCAG 2.1 AA 4.5:1)
    │   ├── contrast.ts       #   对比度计算(token AA 自证)
    │   ├── ThemeProvider.tsx #   light/dark/system 即时切换(无刷新)
    │   └── components/       #   Button/Input/Select/Skeleton/EmptyState/ErrorState/Banner/
    │                         #   Toast/Dialog(焦点圈养)/Kbd/StatusDot
    ├── shortcuts/            # 快捷键体系(§6.12)
    │   ├── registry.ts       #   分组命令/快捷键注册表(上下文感知)
    │   ├── ShortcutProvider.tsx  # 输入框豁免(除 Ctrl/Cmd 组合)、序列键 G→I/B/M/A
    │   ├── CommandPalette.tsx#   Ctrl/Cmd+K 命令面板(命令注册接口)
    │   └── ShortcutHelp.tsx  #   ? 快捷键帮助层
    ├── state/                # 全局状态
    │   ├── settingsStore.ts  #   theme/locale/timezone 偏好(本地持久化;阶段 2 接 PATCH /users/me)
    │   └── authStore.ts      #   Bearer token 存取
    └── shell/                # App shell 与占位页
        ├── AppShell/TopBar/Sidebar/StatusBanner(offline/重连·重放→「正在重新同步」横幅,§6.12/§6.7)
        └── pages/            #   登录占位页、404、错误页、首页骨架演示区
```

## 主题体系(theme.md — 设计系统级契约)

- **token 单一事实源**:新增/修改 token **只改 `src/design/tokenValues.ts`**,随后 `npm run gen:tokens` 重新生成 `tokens.css` / `tokens-dark.css` / `tokens-print.css`(生成产物首行带禁改标记;CI 幂等断言:生成后工作区无 diff)。`AA_CONTRAST_PAIRS` 为对比度配对登记表(新增颜色 token 须先登记再合入;text 4.5:1、大文本/图形 3:1)。
- **协商链(§2.2)**:`users.settings.theme`(absent/null = 继承工作区默认;显式 `system` = 忽略工作区跟随 OS)→ `workspaces.settings.default_theme`(WorkspaceProvider 经 `workspaceThemeBridge` 桥接,`workspace.updated` 实时联动)→ 系统 `prefers-color-scheme`。未登录邀请页经 preview `appearance.default_theme` 解析第 2 级。
- **首帧三级链路(§2.3)**:`index.html` 内联脚本按「入口注入 `__MESH_APPEARANCE__`(服务端逐请求解析)→ 分区镜像键 `mesh.theme.active`(路由身份 `id` 校验先于 `mode` 白名单读取)→ `data-theme-pending` skeleton 兜底」顺序首帧落主题;`ThemeProvider` 挂载后以协商链权威解析覆盖并回写 locator。宁可短暂无主题骨架,不可先错后改。
- **取色铁律(§5.4)**:组件一律 `var(--<语义 token>)`,禁硬编码色值——AST 级门禁(Stylelint + ESLint 自定义规则)CI 拦截;数据色例外(标签色板等)须「行级 `mesh-data-color` 注释 + `theme-lint-exemptions.json` 登记」双要件,禁整文件白名单。
- **门禁命令**:`npm run check:contrast`(对比度独立关卡)、`npm run lint:css`(Stylelint)、`npm run test:e2e:visual`(双主题视觉回归,基线更新经独立 PR)。

## 阶段 1·B 边界

- 不实现业务页面(auth/workspace/member/issue 等 UI 归各阶段 Issue);首页为骨架演示区(playground)。
- 偏好写入当前为本地持久化;阶段 2 接通 `PATCH /api/v1/users/me`(auth.md §3.1)与 `PATCH /api/v1/workspaces/{id}`(workspace.md)。
- 前端容器化在后续 Issue 集成(本阶段以 dev server + 生产构建验证)。
