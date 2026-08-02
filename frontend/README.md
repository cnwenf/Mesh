# Mesh 前端

Mesh 的 Web 单页应用(SPA)。本目录是阶段 1·B 的前端地基:工程脚手架、API/实时客户端契约层、设计系统与体验基线骨架、i18n 基线、路由/布局/状态骨架。**契约语义以 `../docs/specs/README.md` §3.2/§6.5/§6.7/§6.12/§6.14/§6.16/§6.18 与 `../docs/specs/features/i18n.md` 为唯一权威。**

## 选型理由

Spec(§3.2)不约束前端框架,仅要求 SPA、乐观更新 + 服务端版本校验、WebSocket 增量合并、离线降级轮询。选型与理由:

| 选型                                | 理由                                                                                                                                                                                                                                                          |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **React 19 + TypeScript 5**         | 生态最成熟、类型安全的组件模型;团队招聘与社区资料成本最低                                                                                                                                                                                                     |
| **Vite 6**                          | 开发启动/HMR 快,生产构建(Rollup)成熟;Vitest 同源配置                                                                                                                                                                                                          |
| **react-router 8**                  | 事实标准路由;支持规范深链(§6.12 深链模式);v7 收口 6.x 开放重定向审计项(GHSA-wrjc-x8rr-h8h6 / GHSA-337j-9hxr-rhxg),v8 清零 RSC CSRF 审计项(GHSA-qwww-vcr4-c8h2);v8 起 import 自 `react-router`(`react-router-dom` 包已移除),要求 React ≥19.2.7 / Node ≥22.22.0 |
| **zustand 5**                       | 轻量全局状态(偏好/鉴权/实时状态),无 Provider 嵌套负担,易测试                                                                                                                                                                                                  |
| **react-intl 7**                    | 原生 ICU MessageFormat,直接满足 §6.18 的 CLDR 复数/占位符要求                                                                                                                                                                                                 |
| **原生 Intl API**                   | 日期/数字/相对时间/时区本地化零依赖(§6.18 时区化仅展示层)                                                                                                                                                                                                     |
| **原生 CSS 自定义属性(设计 token)** | 「暗色 = 整组语义 token 替换」(§6.12)与 CSS 变量模型天然同构,无运行时开销                                                                                                                                                                                     |
| **Vitest + Testing Library**        | 与 Vite 同源;jsdom 组件测试;v8 覆盖率 ≥90% 门禁                                                                                                                                                                                                               |
| **Playwright**                      | 真实浏览器 e2e(主题/语言/快捷键/增量合并/断线重放逐项真实操作验证)                                                                                                                                                                                            |

数据获取不引入额外库:§6.14 的包络解析、游标分页、乐观并发与 409 收敛由 `src/api` 的自研契约层实现(机制骨架 + 测试,业务接入在各模块 Issue)。

## Quick Start

```bash
cd frontend
npm install          # 需要 Node ≥22.22.0(react-router 8 引擎要求)
npm run dev          # http://127.0.0.1:5173
```

Compose/生产镜像的 builder 同样固定为 Node 22.22.0，必须与上述引擎下限一致；
`docker compose build frontend` 不得出现 `EBADENGINE`。

开发默认连接本地 mock 契约服务端(`e2e/mock-server.mjs`,与后端 v0.1.0 线缆协议
逐帧对齐的忠实镜像:§6.14 包络、§6.7 实时契约——首帧鉴权 `{op:'auth',token}` →
`auth_ok`、`{op:'event',channel,seq,event,payload}`、`subscribed{channel,last_seq}`、
resume_from 重放、resync_required + REST 对账),供契约 e2e 使用:

```bash
node e2e/mock-server.mjs   # http://127.0.0.1:8901(dev 鉴权:mesh-dev:<workspace-uuid>)
```

连接真实后端(v0.1.0 已合入 main)用 `.env.local` 覆盖:

```
VITE_MESH_API_BASE_URL=http://127.0.0.1:8000
VITE_MESH_WS_BASE_URL=ws://127.0.0.1:8081
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
# 前置:仓库根目录起后端栈(首次先 ./scripts/gen-dev-secrets.sh 生成强随机 .env;
#       docker compose up postgres redis api worker gateway,MESH_AUTH_MODE=dev)
npx playwright test --config playwright.real.config.ts
```

> 注:后端 v0.1.0 未开 CORS(生产经 nginx 反代同源部署),该联调配置以
> `--disable-web-security` 启动浏览器,仅联调验证用途。

### 钉钉集成真实栈验证

`e2e/real-dingtalk-ui.spec.ts` 与 `playwright.mes90.config.ts` 使用真实 Mesh API、
PostgreSQL、Redis、worker 与浏览器验证钉钉集成管理面：创建/编辑双接收模式、接收诊断与
测试发送分离、数据库持久显式重连、授权队列和私有项目负向、审批 API 真源自动对账及
精确 `approval_id` 深链。四组合固定为 1440×900 与 390×844 的 light/dark，并对队列卡片
正文和辅助文字做运行时 WCAG AA 对比度断言。

浏览器功能链经 compose 内部、无宿主端口的受控 OAPI 对端执行首次认领所有权证明：只有测试生成的
`dingapp<suffix>` / `MES90-<suffix>-DingTalk-Secret!7` 精确关系可通过，错误 secret 必须返回 422 且
不落集成行。随后创建 Stream 集成、验证显式重连 API/数据库状态并切换 HTTP 模式发送真实签名回调；
测试发送仍走真实出站客户端并落到受控对端的失败路径。该替身只替代外部 OAPI，不 mock 任何 Mesh
路由，也不声称建立了真实企业 Stream。物理重连另由后端
`tests/e2e/test_dingtalk_e2e.py::test_explicit_reconnect_api_replaces_live_socket_real_e2e`
在真实 worker 进程、PostgreSQL、Redis 与强制证书校验的 TLS/WSS gateway 下断言旧 socket 关闭、
`connections/open` 重跑和新 socket 活跃。真实测试企业上的 Stream 建连、群消息、卡片点击与结果
回推仍须使用独立企业凭据复验并留存平台侧证据，不得用本地结果替代该验收门禁。

配置不提供 API/WS 默认值，并以 `reuseExistingServer=false` 启动当前 Vite；端口已有旧进程时直接
失败，避免混用其他栈。默认执行四组合视觉门：

```bash
# 仓库根目录；首次运行先用 ./scripts/gen-dev-secrets.sh 生成强随机 .env。
# API/worker 都必须拿到可从浏览器打开的绝对站点基址。
MESH_API_PORT=18090 \
MESH_WS_PORT=18091 \
MESH_STORAGE_PORT=19090 \
MESH_STORAGE_CONSOLE_PORT=19091 \
MESH_STORAGE_PUBLIC_ENDPOINT=http://127.0.0.1:19090 \
MESH_APP_BASE_URL=http://127.0.0.1:18090 \
docker compose -p mes90e2e \
  -f docker-compose.yml \
  -f frontend/e2e/mes90/compose.override.yml \
  up -d --build postgres redis minio api worker gateway mes90-dingtalk-oapi

MES90_API_BASE=http://127.0.0.1:18090 \
MES90_WS_BASE=ws://127.0.0.1:18091 \
npx playwright test --config playwright.mes90.config.ts
```

完整功能链只运行一个 desktop-light project，并额外要求显式指定它直接检查的 PostgreSQL/Redis
容器（也可改用对应 host/password 变量）：

```bash
MES90_SUITE=functional \
MES90_API_BASE=http://127.0.0.1:18090 \
MES90_WS_BASE=ws://127.0.0.1:18091 \
MES90_PG_CONTAINER=mes90e2e-postgres-1 \
MES90_REDIS_CONTAINER=mes90e2e-redis-1 \
npx playwright test --config playwright.mes90.config.ts
```

## 质量命令

```bash
npm run lint            # ESLint 9(flat config)
npm run typecheck       # tsc --noEmit
npm run test            # vitest 单元/组件测试
npm run test:coverage   # 覆盖率(整体 lines/functions/branches/statements ≥90% 门禁)
node scripts/verify-coverage.mjs --base origin/main   # 新增/变更代码覆盖率 ≥90% 校验
npm run build           # 生产构建(gen:tokens + tsc -b + vite build)
npm run test:e2e        # Playwright 真实浏览器 e2e(自动拉起 mock 服务端与生产构建预览)
```

mock 契约套件在每次运行时先生成一次生产构建,再由 `vite preview`
服务该静态构建;不复用已存在的服务进程,且 Playwright 保持 `retries: 0`。
这使命令面板、抽屉和实时契约的验证面与发布产物一致,并避免开发期
HMR/源码模块图参与门禁结果。测试构建会显式启用内置 `mock` OAuth
提供商,不依赖 `import.meta.env.DEV` 的开发默认值。

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
    │   ├── uuid.ts           #   安全上下文无关 uuidv4(MES-129):HTTP 部署下 crypto.randomUUID
    │   │                     #   缺失,幂等键/本地 ID 一律经此生成(getRandomValues 兜底)
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
    ├── design/               # 设计系统(design-quality.md §5–§9;README §6.12)
    │   ├── tokenValues.ts    #   语义 token 单一事实源:三层令牌(表面/文本/边界层级、
    │   │                     #   状态色 fg/bg/border 三元组、间距/布局/圆角/阴影/动效/
    │   │                     #   z-index/排版)+ AA 对比度配对登记表
    │   ├── tokens*.css       #   生成的 token CSS(亮/暗/打印三套,禁手改,CI 幂等)
    │   ├── typography.css    #   type scale 工具类(§6.2 十一档 + 表格数字/等宽标识/中文排版)
    │   ├── contrast.ts       #   对比度计算(token AA 自证)
    │   ├── ThemeProvider.tsx #   light/dark/system 即时切换(无刷新)
    │   ├── StyleguidePage.tsx#   /styleguide 组件状态 fixture(四视口×亮暗视觉回归拍摄对象,MES-115)
    │   └── components/       #   原语:Button(28/36/44 三档全状态)/IconButton/Input/Select/
    │                         #   Badge/Avatar(稳定 hash 取色·agent 统一轮廓)/Icon(20px 线性集 50+,
    │                         #   filled 变体)/Tooltip/Menu/Tabs/Accordion/Drawer(焦点圈养)/Dialog/
    │                         #   Skeleton/EmptyState/ErrorState(§7.7 四部分)/Banner/Toast/Kbd/StatusDot
    │                         #   + MES-115 增补:Field/Textarea/Checkbox(半选)/Switch/Popover(翻转定位)/
    │                         #   PageHeader/Toolbar/DataTable(aria-sort 排序)
    │   └── patterns/         #   页面模板(design-quality.md §4.4/§11.1,MES-111 批次②沉淀):
    │                         #   PageHeader(唯一 h1 + 面包屑 + 动作槽)/DataView(标题栏 + 工具条
    │                         #   槽 + 主体 + 分页 + 粘底批量条)/DetailLayout(桌面两栏 + 320px
    │                         #   属性侧栏;窄容器自动收为「属性」底部 Drawer)/FilterChips/BulkBar/
    │                         #   useListKeyboardSelection(漫游 tabindex 行选择)。依赖方向
    │                         #   features → patterns → primitives → foundations,禁反向。
    ├── shortcuts/            # 快捷键体系(§6.12)
    │   ├── registry.ts       #   分组命令/快捷键注册表(上下文感知)
    │   ├── ShortcutProvider.tsx  # 输入框豁免(除 Ctrl/Cmd 组合)、序列键 G→I/B/M/A
    │   ├── CommandPalette.tsx#   Ctrl/Cmd+K 命令面板(命令注册接口)
    │   └── ShortcutHelp.tsx  #   ? 快捷键帮助层
    ├── state/                # 全局状态
    │   ├── settingsStore.ts  #   theme/locale/timezone 偏好(本地镜像 + 服务端同步:PATCH /users/me,auth.md §3.1;preferencesSync/pendingSettingsQueue/usePreferencesBootstrap)
    │   └── authStore.ts      #   Bearer token 存取
    └── shell/                # App shell(顶栏/侧栏/状态横幅/登录守卫)
        ├── navigation.ts     #   全局导航唯一事实源:四分组入口表(桌面侧栏/手机底栏/更多抽屉同源)
        ├── AppShell/TopBar/StatusBanner(offline/重连·重放→「正在重新同步」横幅,§6.12/§6.7)
        ├── Sidebar           #   桌面分组可折叠侧栏(256px ↔ 64px rail,§4.1)
        ├── MobileNav/MobileMoreDrawer  # 手机底栏 + 「更多」全高抽屉(§4.3)
        └── pages/            #   登录页、404、错误页、真实首页(工作区仪表盘,MES-107)
```

## 主题体系(theme.md — 设计系统级契约)

- **token 单一事实源**:新增/修改 token **只改 `src/design/tokenValues.ts`**,随后 `npm run gen:tokens` 重新生成 `tokens.css` / `tokens-dark.css` / `tokens-print.css`(生成产物首行带禁改标记;CI 幂等断言:生成后工作区无 diff)。`AA_CONTRAST_PAIRS` 为对比度配对登记表(新增颜色 token 须先登记再合入;text 4.5:1、大文本/图形 3:1)。
- **协商链(§2.2)**:`users.settings.theme`(absent/null = 继承工作区默认;显式 `system` = 忽略工作区跟随 OS)→ `workspaces.settings.default_theme`(WorkspaceProvider 经 `workspaceThemeBridge` 桥接,`workspace.updated` 实时联动)→ 系统 `prefers-color-scheme`。未登录邀请页经 preview `appearance.default_theme` 解析第 2 级。
- **首帧三级链路(§2.3)**:`index.html` 内联脚本按「入口注入 `__MESH_APPEARANCE__`(服务端逐请求解析)→ 分区镜像键 `mesh.theme.active`(路由身份 `id` 校验先于 `mode` 白名单读取)→ `data-theme-pending` skeleton 兜底」顺序首帧落主题;`ThemeProvider` 挂载后以协商链权威解析覆盖并回写 locator。宁可短暂无主题骨架,不可先错后改。
- **取色铁律(§5.4)**:组件一律 `var(--<语义 token>)`,禁硬编码色值——AST 级门禁(Stylelint + ESLint 自定义规则)CI 拦截;数据色例外(标签色板等)须「行级 `mesh-data-color` 注释 + `theme-lint-exemptions.json` 登记」双要件,禁整文件白名单。
- **门禁命令**:`npm run check:contrast`(对比度独立关卡)、`npm run lint:css`(Stylelint)、`npm run test:e2e:visual`(双主题视觉回归,基线更新经独立 PR)。

## 阶段 1·B 边界(历史边界说明,仅存档案价值)

- 本阶段曾不实现业务页面、首页为骨架演示区(playground);上述边界已被后续各模块
  Issue 全部突破(MES-107 起首页为真实工作区仪表盘,演示组件与占位页整体移除)。
- 偏好写入已接通服务端同步:`PATCH /api/v1/users/me`(auth.md §3.1,键级浅合并;失败写 pending 分区队列待重放,§4.5)+ `PATCH /api/v1/workspaces/{id}` 的 `settings.default_theme`(workspace.md,admin);本地持久化降级为镜像与防闪烁首帧用途。
- 前端容器化在后续 Issue 集成(本阶段以 dev server + 生产构建验证)。
