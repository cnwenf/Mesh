# Mesh 前端脚手架与设计系统/体验基线 Implementation Plan (MES-16)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 0 到 1 搭建 Mesh 前端地基:SPA 工程脚手架、API/实时客户端契约层、设计系统与体验基线骨架、i18n 基线、路由/布局/状态骨架,为阶段 2 各模块 UI 提供统一地基。

**Architecture:** React 18 + TypeScript + Vite 的 SPA;契约层(`src/api`)实现 README §6.14 包络/分页/乐观并发/幂等/错误分发;实时层(`src/realtime`)实现 §6.7 子协议鉴权、每频道 last_seq、resume_from 重放、resync_required REST 对账与离线降级轮询;呈现层(`src/design` + `src/i18n` + `src/shortcuts`)实现 §6.12 语义 token/主题/无障碍/快捷键与 §6.18 消息目录/协商链/本地化渲染;`src/shell` 组装 App shell 与占位页。

**Tech Stack:** React 18、TypeScript 5、Vite 6、react-router-dom 6、zustand 5、react-intl 7(ICU MessageFormat)、Vitest + Testing Library(v8 coverage ≥90% 门禁)、Playwright(真实浏览器 e2e)、ESLint 9 + Prettier、原生 Intl(日期/数字/相对时间/时区)。

## Global Constraints

- 契约语义以 `docs/specs/README.md` §3.2/§6.5/§6.7/§6.12/§6.14/§6.16/§6.18 与 `features/i18n.md` 为唯一权威。
- **不修改后端目录与 `docker-compose.yml`**;不实现任何业务页面(auth/workspace/member 等归后续 Issue)。
- 覆盖率门禁:整体与新增代码 lines/functions/branches/statements 均 ≥90%(vitest v8)。
- UI 文案一律经消息目录外部化,禁止硬编码可见文案;错误 message 由前端按 `error.<code>` 渲染。
- WebSocket **禁止** token 进 URL query,一律经子协议 `Sec-WebSocket-Protocol` 鉴权(§6.16)。
- 一切颜色经语义 token 引用,暗色为整组 token 替换;亮/暗均满足 WCAG 2.1 AA(4.5:1)。
- 提交身份 `cnwenf <cnwenf@gmail.com>`;无 Co-Authored-By;`core.hooksPath=/dev/null`。
- 代码/注释/文档/提交/分支名绝不暴露参考来源。

## File Structure

```
frontend/
├── package.json / vite.config.ts / tsconfig.json / tsconfig.node.json
├── eslint.config.js / .prettierrc / index.html
├── playwright.config.ts / e2e/**(Playwright 真实浏览器测试)/ e2e/mock-server.mjs(契约+WS mock 服务端)
├── README.md                       # 选型理由、Quick Start、目录结构
├── scripts/verify-coverage.mjs     # 新增代码覆盖率校验(对 git diff 的变更行取交集)
└── src/
    ├── main.tsx / App.tsx          # 入口与 Provider 组装
    ├── env.ts                      # 运行时配置(VITE_MESH_API_BASE / WS_BASE)
    ├── types/                      # 共享类型:包络、事件帧、实体、偏好
    │   ├── envelopes.ts            # ApiEnvelope/ListEnvelope/GroupedEnvelope/ErrorEnvelope
    │   ├── realtime.ts             # RealtimeFrame/SubscribeOp/ResumeOp/ResyncRequired
    │   └── entities.ts             # IssueSummary 等骨架实体 + Visibility
    ├── api/                        # §6.14 契约层(T1)
    │   ├── errors.ts               # MeshApiError + errorDispatch(code→i18n key)
    │   ├── tokenStore.ts           # Bearer token 存取(memory+localStorage)
    │   ├── client.ts               # MeshApiClient:鉴权头、包络解析、If-Match、Idempotency-Key
    │   ├── pagination.ts           # 游标分页迭代 + useCursorPagination hook
    │   ├── optimistic.ts           # 乐观更新 + 版本校验 + 409 收敛 + useOptimisticMutation
    │   ├── filters.ts              # 过滤限制(深度3/条件20)客户端预校验 + 错误归类
    │   └── __tests__/**
    ├── realtime/                   # §6.7 实时客户端(T2)
    │   ├── RealtimeClient.ts       # 连接/子协议鉴权/订阅/resume/重连退避/resync
    │   ├── channelCursors.ts       # 每频道 last_seq(内存+localStorage)
    │   ├── merge.ts                # 增量合并:完整字段+visibility 归属+updated_at 防回退
    │   ├── pollingFallback.ts      # WS 断开 → since= 轮询降级
    │   ├── useRealtime.ts          # React 绑定(状态机:connecting/online/reconnecting/resyncing/offline)
    │   └── __tests__/**
    ├── i18n/                       # §6.18 i18n 基线(T3)
    │   ├── catalogs/en.json / zh-CN.json   # ICU MessageFormat 消息目录(含 error.*)
    │   ├── negotiate.ts            # 协商链 + Accept-Language q 值解析 + BCP-47 主干回退
    │   ├── catalogLoader.ts        # 目录加载/版本(ETag 语义)/缺 key 三级回退
    │   ├── format.ts               # 日期/时间/数字/相对时间 + 时区化 + 本地输入→UTC
    │   ├── I18nProvider.tsx        # react-intl 接线 + 缺失上报(开发期可见标记)
    │   └── __tests__/**
    ├── design/                     # §6.12 设计系统骨架(T4)
    │   ├── tokens.css / tokens-dark.css    # 语义 token(亮/暗两套,AA 对比度)
    │   ├── base.css                # reset + 焦点可见 + reduced-motion
    │   ├── contrast.ts             # 对比度计算(供 token AA 自证测试)
    │   ├── ThemeProvider.tsx       # light/dark/system 即时切换(无刷新)
    │   ├── components/             # Button/Input/Select/Skeleton/EmptyState/ErrorState/
    │   │                           #   Banner/Toast/Dialog(焦点圈养+Esc)/Kbd
    │   └── __tests__/**
    ├── shortcuts/                  # §6.12 快捷键体系(T4)
    │   ├── registry.ts             # 分组注册表(全局/看板/issue/聊天)+ 序列键(G→I/B/M/A)
    │   ├── ShortcutProvider.tsx    # 输入框豁免(除 Ctrl/Cmd 组合)+ 帮助层(?)
    │   ├── CommandPalette.tsx      # Ctrl/Cmd+K 命令面板占位(命令注册接口)
    │   └── __tests__/**
    ├── state/                      # 全局状态(T5)
    │   ├── settingsStore.ts        # theme/locale/timezone 偏好(本地持久化,阶段2接 PATCH /users/me)
    │   ├── authStore.ts            # token 存取
    │   └── __tests__/**
    └── shell/                      # App shell 与占位页(T5)
        ├── AppShell.tsx / TopBar.tsx / Sidebar.tsx
        ├── StatusBanner.tsx        # offline「网络已断开」/ resync「正在重新同步…」
        ├── pages/LoginPage.tsx(占位)/ NotFoundPage.tsx / ErrorPage.tsx / HomePage.tsx
        └── __tests__/**
```

## Module Interface Contracts(跨模块依赖的唯一对齐点)

### types/envelopes.ts
```ts
export interface SingleEnvelope<T> { data: T }
export interface ListEnvelope<T> { data: T[]; next_cursor: string | null }
export interface Group<T> { key: string; label: string; count: number; wip?: number; data: T[] }
export interface GroupedEnvelope<T> { groups: Group<T>[]; next_cursor: string | null }
export interface ErrorEnvelope { error: { code: string; message: string; details?: Record<string, unknown> } }
```

### api/client.ts
```ts
export interface ClientOptions { baseUrl: string; getToken: () => string | null; fetchImpl?: typeof fetch }
export interface RequestOptions {
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  ifMatch?: string;            // 乐观并发:If-Match: <updated_at>
  idempotencyKey?: string;     // 显式键;缺省时 POST/动作类自动生成(§6.5)
  skipEnvelope?: boolean;
  signal?: AbortSignal;
}
export class MeshApiClient {
  constructor(opts: ClientOptions);
  request<T>(method: HttpMethod, path: string, opts?: RequestOptions): Promise<T>;         // 解单对象包络返回 data
  list<T>(path: string, opts?: RequestOptions): Promise<ListEnvelope<T>>;                  // 原样返回列表包络
  grouped<T>(path: string, opts?: RequestOptions): Promise<GroupedEnvelope<T>>;            // 整体游标包络
}
```
错误一律抛 `MeshApiError { status, code, message, details, retryAfter? }`;429 解析 `Retry-After`。

### api/optimistic.ts
```ts
export interface OptimisticPlan<T> {
  current: T;                  // 本地当前值(带 updated_at/version)
  changes: Partial<T>;
  getServerVersion: (v: T) => string;   // 通常为 updated_at
}
export async function optimisticUpdate<T>(
  client: MeshApiClient, path: string, plan: OptimisticPlan<T>,
  onConflict?: (server: T, err: MeshApiError) => Promise<T>,  // 409 收敛:默认重拉最新并携带新版本重试一次
): Promise<{ result: T; conflicted: boolean }>
```

### realtime/RealtimeClient.ts
```ts
export type ConnectionState = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'resyncing' | 'offline';
export interface RealtimeClientOptions {
  url: string; getToken: () => string | null;
  WebSocketImpl?: typeof WebSocket;          // 测试注入 FakeWebSocket
  reconciler?: (topic: string, rest: string, watermark: number) => Promise<void>;  // resync REST 对账
  maxRetries?: number; baseDelayMs?: number;
}
export class RealtimeClient {
  connect(): void; disconnect(): void;
  subscribe(topic: string): void; unsubscribe(topic: string): void;
  onFrame(cb: (f: RealtimeFrame) => void): () => void;
  onState(cb: (s: ConnectionState) => void): () => void;
  onResync(cb: (r: { topic: string; watermark: number; rest: string }) => void): () => void;
  readonly state: ConnectionState;
}
```
帧格式(服务端→客户端):`{ seq, type, topic, ts, data }`;控制帧 `{ op: 'subscribed'|'resync_required', topic, ... }`。
客户端→服务端:`{ op: 'subscribe', topic, resume_from? }`、`{ op: 'unsubscribe', topic }`、`{ op: 'ping' }`。
子协议鉴权:`new WebSocket(url, ['mesh.auth.v1', token])`。

### i18n/negotiate.ts
```ts
export function parseAcceptLanguage(header: string): string[];                     // q 值降序
export function matchSupported(requested: string[], supported: string[]): string | null; // 精确→主干
export function negotiateLocale(input: {
  requested?: string | string[] | null; userLocale?: string | null;
  workspaceDefaultLocale?: string | null; supported: string[]; fallback?: string;
}): string;
```

### state/settingsStore.ts(zustand,本地持久化键 `mesh.settings.v1`)
```ts
export interface UserPreferences { theme: 'light'|'dark'|'system'; locale: string | null; timezone: string }
export const useSettingsStore: UseBoundStore<StoreApi<SettingsState>>;
// state: preferences + resolved theme/locale; actions: setTheme/setLocale/setTimezone/resetLocale;hydrate()/persist 内置
```

## Tasks(子代理分工与 TDD 循环)

### Task 1 — API 契约层(`src/api/**` + `src/types/**`)
**Produces:** `MeshApiClient`、`MeshApiError`、`useCursorPagination`、`optimisticUpdate`/`useOptimisticMutation`、`validateFilters`/`classifyFilterError`、tokenStore。
**Key behaviors(TDD):**
- 三类包络解析:单对象取 `data`;列表保留 `{data,next_cursor}`(`next_cursor=null` 末页);分组 `{groups,next_cursor}` 整体游标。
- 每个请求携带 `Authorization: Bearer <token>`(getToken 为空则不带头,便于登录前调用)。
- POST/PUT/PATCH/DELETE(动作类)自动附 `Idempotency-Key`(crypto.randomUUID,可用 `idempotencyKey` 覆盖);GET 不附。
- `ifMatch` → `If-Match` 头;`version` 字段随 body 透传。
- 错误信封 → `MeshApiError`;非 JSON/缺 error 字段 → `code='internal_error'`;429 解析 `Retry-After`(秒或 HTTP-date)。
- 游标分页 hook:累积 pages、`hasMore`、`fetchNext`、错误态、重置。
- 409 收敛:捕获 conflict → 回调重拉服务端最新 → 以最新 `updated_at` 重放一次;二次冲突上抛。
- 过滤限制:深度 >3 或条件数 >20 本地抛 `MeshApiError(code='filter_too_complex')`;服务端 `400 filter_too_complex`/`422 query_cost_exceeded` 经 errorDispatch 归类。
**Test:** vitest + fetch mock(fetchImpl 注入),覆盖全部分支。

### Task 2 — 实时客户端(`src/realtime/**`)
**Consumes:** `types/realtime.ts`、`MeshApiError`(对账失败归类)。
**Key behaviors(TDD,注入 FakeWebSocket):**
- connect 使用子协议 `['mesh.auth.v1', token]`;token 缺失 → 状态 offline 且不建连(绝不进 URL query)。
- subscribe 发 `{op:'subscribe',topic}`;有该频道 last_seq 时带 `resume_from=last_seq+1`。
- 收到数据帧 → 更新该频道 last_seq、派发 onFrame;seq ≤ last_seq 的重复帧幂等丢弃(at-least-once)。
- 收到 `{op:'resync_required',topic,watermark,rest}` → 状态 `resyncing`、调 reconciler → 成功后重置该频道游标为 watermark 并重订阅、恢复 `connected`;reconciler 失败 → 退避重试。
- 断线 → `reconnecting` 指数退避(base 500ms ×2,上限 30s,抖动)重连,重连后对所有已订阅频道重发 subscribe(带游标)。
- 每频道游标持久化(localStorage `mesh.rt.cursors.v1`),key=channel。
- ping/keepalive:30s 无帧发 `{op:'ping'}`。
- merge.ts:`mergeEntityFrame(map, frame, { belongs })` — payload 完整字段合并;`updated_at` 旧于本地则丢弃;`belongs(visibility)` 判定归属,不命中则移除。
- pollingFallback.ts:与 RealtimeClient 同接口的降级实现,`since=<max updated_at>` 轮询(默认 30s),WS 不可用时由 useRealtime 自动切换。

### Task 3 — i18n 基线(`src/i18n/**`)
**Key behaviors(TDD):**
- negotiate:显式参数 → userLocale → workspaceDefault → `en`;`parseAcceptLanguage` 按 q 降序;BCP-47 精确→主干(`zh-TW`→`zh-CN`);非法值忽略不报错。
- catalogs:en 为权威源,一切 key 先登记 en;zh-CN 全量对应;含 `error.<code>` 键覆盖 §6.14 全部错误码(unauthorized/forbidden/not_found/conflict/gone/payload_too_large/unsupported_media_type/rate_limited/internal_error/storage_error/validation_error/filter_too_complex/query_cost_exceeded/invalid_timezone/unsupported_locale + 通用网络态)。
- 缺 key 三级回退:请求 locale → en → key 原样;命中回退触发 `reportMissing`(开发期,去重窗口 60s,`POST /api/v1/i18n/missing` 失败静默);开发构建文案包裹 `⚠[key]` 标记,生产关闭。
- catalogLoader:模拟 ETag 版本缓存语义(带 version 请求,304 沿用本地)— 以可注入的 fetcher 实现,单测覆盖命中/未命中。
- format.ts:`formatDateTime(utc, {locale, timeZone, dateStyle, timeStyle})`、`formatNumber`、`formatRelativeTime(utc, now)`(Intl.RelativeTimeFormat + 自动单位)、`parseLocalToUTC(localParts, timeZone)` 输入解析回 UTC;跨时区标注辅助 `formatWithZoneAnnotation`。
- I18nProvider:react-intl IntlProvider 接线 settingsStore.locale + 目录;`onError` 仅对 missing 上报。

### Task 4 — 设计系统与快捷键(`src/design/**` + `src/shortcuts/**`)
**Key behaviors(TDD):**
- tokens.css:语义变量(--color-bg/surface/text/text-muted/border/primary/…/status-success/warn/danger/info);tokens-dark.css 以 `[data-theme='dark']` 整组替换。
- contrast.ts:WCAG 相对亮度 + 对比度比;测试断言亮/暗两套 text/bg、muted/bg、各 status 色对底 ≥4.5:1。
- ThemeProvider:读 settingsStore.theme;`system` 监听 `matchMedia('(prefers-color-scheme: dark)')` 变化;设置 `<html data-theme>`;切换即时无刷新;index.html 内联防闪烁脚本。
- base.css:焦点可见(:focus-visible 描边)、`prefers-reduced-motion` 降级、逻辑属性(margin-inline 等,为 RTL 预留)。
- 组件:Button(variant/size/loading,Enter/Space 激活)、Input/Select(aria-label)、Skeleton、EmptyState(插画槽+主操作槽)、ErrorState(重试按钮)、Banner(aria-live=polite/assertive)、Toast(Provider+live region+自动消失+手动关闭)、Dialog(焦点圈养:Tab 循环、Esc 关闭、关闭后焦点归还触发元素)、Kbd。
- shortcuts/registry:分组(global/board/issue/chat);`?` 帮助层实时反映当前分组;输入框聚焦时忽略裸键(保留 Ctrl/Cmd 组合);序列键 G→I/B/M/A(1s 窗口)跳收件箱/看板/成员/自动化占位路由;`C` 新建 issue、`/` 聚焦搜索为注册占位;所有命令有等价鼠标路径。
- CommandPalette:Ctrl/Cmd+K 打开,命令注册接口 `registerCommand({id,label,group,run,keywords})`,占位命令集;搜索过滤;键盘上下选择 Enter 执行;aria-modal + 焦点圈养。

### Task 5 — 状态骨架与 App shell(`src/state/**` + `src/shell/**` + `App.tsx`)
**Consumes:** Task 1–4 全部导出。
**Key behaviors(TDD):**
- settingsStore:zustand + localStorage 持久化(`mesh.settings.v1`);默认 `{theme:'system', locale:null, timezone: 浏览器检测}`;resetLocale → null(跟随工作区默认)。
- authStore:token 存取(localStorage `mesh.auth.v1`),供 MeshApiClient.getToken 与 RealtimeClient.getToken。
- AppShell:TopBar(品牌、连接状态点+aria-live、主题切换、语言切换、命令面板按钮、快捷键帮助按钮);Sidebar(收件箱/项目/看板/成员/聊天/自动化/设置 导航,占位路由);主内容区 Outlet。
- StatusBanner:订阅 realtime 状态 — offline 显示「网络已断开,操作将排队并在恢复后同步」(横幅+自动重连指示),resyncing 显示「正在重新同步…」,对账成功无感消失。
- pages:LoginPage(占位表单 + token 粘帖入口,标注阶段 2 接真实 auth;提交仅写 authStore 供联调)、NotFoundPage(404+回首页)、ErrorPage(ErrorBoundary 兜底+重试)、HomePage(骨架演示区:演示主题/语言/快捷键/状态组件的 playground,文案全走目录)。
- 路由:`/login`、`/`(AppShell 嵌套:home/settings/inbox… 占位)、`*` → 404;深链模式 `/w/:workspaceSlug/...` 预留占位重定向。

### Task 6 — 集成验证与交付(主线执行)
- mock 服务端(`e2e/mock-server.mjs`,node:http + ws):实现三类包络 GET、PATCH If-Match/409、POST 幂等回显、错误码端点;WS 实现 subscribe/resume_from 重放/resync_required/断线。
- Playwright e2e:主题切换(暗色 token 生效)、locale 切换(zh-CN/en 就地更新)、快捷键(? 帮助层、Ctrl/Cmd+K 面板)、模拟事件增量合并(卡片插入/移动/移除)、断线→重连 resume_from 重放、过旧游标→resync 横幅→对账恢复、404 页、焦点/aria 抽查。
- 覆盖率:`npm run test:coverage` 整体 ≥90%;`scripts/verify-coverage.mjs` 对 git diff 新增行校验 ≥90%。
- CI:`.github/workflows/frontend.yml`(lint → typecheck → test:coverage → build → e2e)。
- 文档:`frontend/README.md`(选型理由/Quick Start/目录结构);根 `README.md` 增补前端章节;Spec 无需改写(前端不约束框架,§3.2 已满足项在 README 说明)。
- 提交与 PR:conventional commits、cnwenf 身份、无 co-author;PR 到 main 附自证清单。

## Self-Review

- **Spec 覆盖**:§3.2(脚手架/乐观更新/WS 增量合并/离线轮询 → Task 1/2)、§6.5(幂等键 → Task 1)、§6.7(子协议鉴权/每频道游标/resume/resync/visibility → Task 2)、§6.12(token/主题/无障碍/异常态矩阵/快捷键/命令面板 → Task 4/5)、§6.14(包络/分页/并发/错误/过滤限制 → Task 1)、§6.16(token 不进 URL → Task 2)、§6.18(协商链/外部化/本地化/时区 → Task 3)。✅
- **类型一致性**:`MeshApiError`/`RealtimeFrame`/`ConnectionState`/`UserPreferences` 名称与签名在接口契约与各 Task 间一致。✅
- **无占位符**:接口契约给出完整签名;各 Task 的 Key behaviors 为可测试的行为清单(非 TBD)。✅
