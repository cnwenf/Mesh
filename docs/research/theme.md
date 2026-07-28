# 主题 / 暗色模式(设计系统级)调研记录

> 调研对象:设计系统级主题(语义 token 分层、暗色模式范式、对比度校验)的**通用设计模式**(已匿名化,不指向任何具体产品);并与 Mesh 既有 README §6.12 条款 + 前端实现逐项对账,标出缺口。
> 数据模型基准:PostgreSQL / UUID / RFC3339 UTC / REST+JSON / 游标分页 / Bearer 鉴权 / 实时走 WebSocket;主题偏好的 API 包络·错误·分页一律引用 README §6.14,主题契约引用 §6.12,展示偏好协商引用 §6.18,用户可控 URL 引用 §6.16。

---

## 0. 既有实现盘点与缺口(对账结论)

**已落地(账号级主题主链路完整)**:

| 层 | 文件 | 现状 |
|----|------|------|
| 条款 | `docs/specs/README.md` §6.12 | 主题契约已立约(light/dark/system、`users.settings.theme` + `workspaces.settings.default_theme`、一切颜色经语义 token、暗色整组替换、两套均 ≥4.5:1、图表双色校准、即时无刷新、尊重 reduced-motion/contrast) |
| 偏好真源 | `auth.md` §2.2 / `backend/.../auth/service.py` | `users.settings.theme ∈ {light,dark,system}`,`PATCH /api/v1/users/me` 键级浅合并写入,显式 `null` = 清除偏好;非法值 → `422 validation_error`(`validate_theme`,`SUPPORTED_THEMES=("light","dark","system")`) |
| 工作区默认 | `workspace.md` §2.2 / `backend/.../workspace/service.py` | `workspaces.settings.default_theme` 已登记(默认 `"system"`),`_validate_settings_keys` 调 `validate_theme` 校验;**写入路径已通** |
| 前端偏好存取 | `frontend/src/state/settingsStore.ts` | `theme` 默认 `'system'`;zustand persist(`mesh.settings.v1`)+ 镜像键 `mesh.theme`;`setTheme` 乐观更新 + fire-and-forget 同步 `PATCH /users/me`(`preferencesSync.ts`),422 经 `lastSyncError` 上报,网络错误静默降级 |
| 主题应用 | `frontend/src/design/ThemeProvider.tsx` | `resolveTheme(mode, systemPrefersDark)` 纯函数;落 `<html data-theme>`;`system` 监听 `prefers-color-scheme` 实时变化、卸载注销 |
| 防闪烁 | `frontend/index.html` | 首帧前同步内联脚本:读 `mesh.theme` → system 时查 `matchMedia` → 设 `data-theme`,try/catch 包裹存储 |
| 语义 token | `design/tokens.css`(亮,`:root`)+ `tokens-dark.css`(暗,`:root[data-theme='dark']`)+ `tokenValues.ts`(单一事实源 `LIGHT_TOKENS`/`DARK_TOKENS`/`AA_CONTRAST_PAIRS`) | 颜色/间距/圆角/字号/阴影/动效 token 全集;暗色 = 整组颜色 token 替换;`tokens.test.ts` 断言 CSS↔TS 逐项镜像 + 暗/亮颜色 token 一一对应 + 必需 token 清单 |
| 对比度 | `design/contrast.ts` + `contrast.test.ts` + `tokens.test.ts` | WCAG 2.1 相对亮度/对比度公式;`AA_CONTRAST_PAIRS` 在亮/暗两套各断言 ≥4.5:1(文本/状态色对背景、状态色作底对其 contrast 文本) |
| 基线样式 | `design/base.css` | `color-scheme: light/dark`(随 data-theme)、最小 reset、`:focus-visible` 焦点环、`prefers-reduced-motion` 降级、`prefers-contrast: more` 边界增强、逻辑属性(RTL 预留)、`.sr-only` |
| 切换入口 | `shell/pages/SettingsPage.tsx` + `shell/AppShell.tsx` | 个人设置「外观」light/dark/system 下拉,即时切换;i18n 键 `theme.*` / `a11y.themeToggle` 已外部化 |
| 图表配色约定 | `analytics.md` §4.5 | 图表色一律经语义 token、亮/暗双主题校准、颜色不作唯一信号(线型/图标/文字叠加)——**约定已立,前端尚无图表库实现** |

**缺口(需在 theme.md Spec 立项解决,按优先级)**:

1. **【高·功能闭环】工作区默认主题未接通消费链**。`default_theme` 只写不读:前端 `resolveTheme`/`settingsStore` 把回退硬编码为 `'system'`,从无 `fetchWorkspaceDefaultTheme`,`workspaces.settings.default_theme` 从未参与解析。**对照模板已存在**——i18n 的 locale 协商链正是同构三级(`i18n/negotiate.ts` + `fetchWorkspaceDefaultLocale`,用户偏好→工作区默认→系统),theme 应镜像该模式补一条 `用户 users.settings.theme → 工作区 default_theme → system` 的解析链(含未登录/邀请接受页等无账号偏好场景)。
2. **【高·契约硬约束】组件硬编码色值**。扫描结果(排除 token 单一事实源与测试夹具):`features/skills/skills.css` ≈52 处、`features/autopilots/autopilots.css` ≈18、`features/data-jobs/dataJobs.css` ≈7、`features/projects/projects.css` ≈1,均为非 token 十六进制值(多为某代码托管平台风格调色板),**暗色模式下不响应、不保证对比度**,直接违反 §6.12「禁止组件硬编码色值」。
3. **【中·例外需立约】用户/数据驱动色**。`features/labels/ColorPicker.tsx`(预设 10 色 + 自定义 hex,标签/选项色属**数据**非主题)、`features/squads/MemberAvatarWall.tsx`(确定性头像底色)是「禁硬编码」规则的**合法例外**,但 Spec 需为其立约:预设色板两套主题下对表面色满足对比、自定义 hex 的文本/前景叠加对比由组件保证、例外须在 CI 扫描白名单中显式登记而非默认豁免。
4. **【中·验收门禁】缺 CI 关卡**。当前无 CSS 静态检查禁止硬编码色、无独立对比度 CI 关卡(对比度仅在 vitest 单测内自证)、无暗色快照/视觉回归。§6.12 的「禁止硬编码 + AA 自证」尚无防回归门禁。
5. **【低·错误码】主题非法值用通用码**。现非法 theme → 通用 `422 validation_error`(区别于 locale 的具名 `unsupported_locale` / timezone 的 `invalid_timezone`)。是否升格为具名 `invalid_theme_mode` 见 §3 权衡。

---

## 1. 功能清单(穷举,必备 / 可选增强)

| # | 功能点 | 级别 | 说明(通用模式 + Mesh 落点) |
|---|--------|------|------------------------------|
| 1.1 | 主题模式三态 `light`/`dark`/`system` | **必备** | `system` = 跟随系统 `prefers-color-scheme`;三态为业界事实标准,`system` 缺省 |
| 1.2 | 账号级偏好存储 `users.settings.theme` | **必备** | JSONB 键级浅合并;默认 `system`;显式 `null` 清除回退到协商链下一级;经 `PATCH /users/me`(auth.md owns) |
| 1.3 | 工作区默认 `workspaces.settings.default_theme` | **必备** | 默认 `system`;未登录/账号未设时生效;经 `PATCH /workspaces/{id}`(workspace.md owns,admin) |
| 1.4 | **偏好协商链**(用户→工作区→系统) | **必备** | 与 §6.18 locale 协商链同构:`users.settings.theme`(为 `null` 跳过)→ `workspaces.settings.default_theme` → `system`。**当前缺口 #1 的核心** |
| 1.5 | 语义 token 三层分层(基础/语义/组件) | **必备** | 业界共识三层单向依赖:基础色板(raw,仅按属性+刻度命名,组件禁直用)→ 语义层(表意:`bg/surface/text/border/status…`,主题切换的唯一替换面)→ 组件层(可选,按部件+状态命名,解析到语义层)。Mesh 现状为「语义层 + 内联基础值」,组件层尚未抽离 |
| 1.6 | 暗色 token 集**整组替换** | **必备** | 暗色 = 语义 token 取值整组替换(属性选择器 `[data-theme='dark']` 覆盖),非逐组件改写;暗色颜色 token 与亮色一一对应、无遗漏/多余(测试断言) |
| 1.7 | 暗色非机械反色 | **必备** | 通用范式:暗色需压缩对比、 raised 表面按层级提亮(或叠加 elevation overlay)、动作色向**更亮**偏移以在深底保住对比、状态色用降饱和/更亮变体。Mesh 暗色集已按此校准(如 `primary` 亮 `#1d4ed8`→暗 `#3b82f6`) |
| 1.8 | 切换即时无刷新 | **必备** | 仅改 `<html data-theme>` 属性,CSS 变量级联即时生效,不重载、不重建路由 |
| 1.9 | 防闪烁(FOUC,首帧前应用) | **必备** | `<head>` 内**同步内联脚本**,在样式解析前读偏好镜像键 → 解析 system → 设 `data-theme`;存储访问 try/catch;静态 HTML 不预置 `data-theme`(由脚本裁决) |
| 1.10 | 系统偏好**实时跟随** | **必备** | `system` 模式下监听 `prefers-color-scheme` 的 `change` 事件实时切换;显式 light/dark 时忽略系统变化;卸载注销监听 |
| 1.11 | 跨标签页同步 | 可选增强 | `storage` 事件监听镜像键,多标签页主题一致(通用范式;Mesh 经 zustand persist 已部分具备) |
| 1.12 | `prefers-reduced-motion` 尊重 | **必备** | 减少动效时关过渡/动画;主题切换本身不做首帧渐变(避免「白→暗慢fade」替代闪烁的同源问题) |
| 1.13 | `prefers-contrast: more` 尊重 | **必备** | 高对比偏好下边界/文本退回更高对比 token(Mesh `base.css` 已实现 border 增强) |
| 1.14 | `color-scheme` 联动 | **必备** | `:root` 声明 `color-scheme: light`,`[data-theme='dark']` 声明 `dark`——使原生滚动条/下拉/自动填充/焦点环随主题,否则暗色下原生控件刺眼白(Mesh 已实现) |
| 1.15 | 图表/状态色双主题校准 | **必备** | 数据可视化配色经语义 token,亮/暗各有校准取值;颜色不作唯一信号(线型/图标/文字叠加);约定见 analytics.md §4.5 |
| 1.16 | 对比度 AA 校验(设计期) | **必备** | 文本/底色配对在亮/暗两套各 ≥4.5:1(正文),大号文本/图形元件 ≥3:1;以单一事实源 token 值代入公式自证(Mesh `contrast.ts` + `AA_CONTRAST_PAIRS` 已实现) |
| 1.17 | 对比度 / 硬编码 CI 关卡 | **必备** | 设计期自证升级为 CI 门禁(防回归):对比度脚本独立成关 + 硬编码色值扫描(白名单仅 token 源)。**当前缺口 #4** |
| 1.18 | 组件硬编码色值禁令 | **必备** | 组件层一律 `var(--token)`;禁令覆盖 `color/background-color/border-color/outline/fill/stroke/box-shadow` 颜色位;CI 扫描 + 显式白名单(数据色例外登记)。**当前缺口 #2/#3** |
| 1.19 | 主题化图片/Logo/头像 | 可选增强 | `<picture><source media="(prefers-color-scheme: dark)">` 或 `currentColor` SVG;装饰性 SVG 用 `filter`/`currentColor`。用户头像为数据,不主题化(仅暗色下加描边/降亮度处理) |
| 1.20 | 高对比度主题(独立第三套) | 可选增强 | 在 light/dark 之外单列 `high-contrast` 主题集;本期建议以 `prefers-contrast` 媒体查询增强替代(见 §8) |
| 1.21 | 用户自定义品牌色 / 主题编辑器 | 可选增强(本期**非目标**) | 见 §8 |
| 1.22 | 主题市场 / 分享 | 可选增强(本期**非目标**) | 见 §8 |

---

## 2. 数据模型草图

**无新表**。与 i18n.md §2 完全同构:仅约定 `users.settings` / `workspaces.settings` 两处既有 JSONB 的键(PostgreSQL 16),存储层不落业务字段、时间恒 UTC(§6.18)。写入均为**键级浅合并**(PATCH 语义)。

### 2.1 偏好键约定(与 i18n.md §2.1/§2.2/§2.3 同构表)

| 键 | 载体 | 类型 | 默认 | owns / 写端点 | 校验 / 错误码 | 说明 |
|----|------|------|------|---------------|---------------|------|
| 账号主题 | `users.settings.theme` | string \| null | `"system"`(`null` = 清除,落到协商链下一级) | auth.md §2.2 / `PATCH /api/v1/users/me`(本人) | `∈ {light,dark,system}`,否则 `422`(现状通用 `validation_error`;是否具名见 §3) | 账号级主题偏好真源;`system` = 跟随 `prefers-color-scheme` |
| 工作区默认主题 | `workspaces.settings.default_theme` | string | `"system"` | workspace.md §2.2 / `PATCH /api/v1/workspaces/{id}`(admin) | `∈ {light,dark,system}`,经 `validate_theme`(现状通用 `validation_error`) | 协商链「工作区默认」级权威读取键;账号未设/未登录时生效 |

### 2.2 协商链(主题版,镜像 §6.18 locale 链)

```
解析实际应用主题(从高到低):
  1. 用户偏好   users.settings.theme          (为 null → 跳过本级)
  2. 工作区默认 workspaces.settings.default_theme
  3. 系统回退   system → prefers-color-scheme  (dark ? dark : light)
最终落到 <html data-theme="light|dark">;system 态持续跟随系统变化(1.10)
```

> 与 locale 链的差异:locale 第 4 级回退到固定 `en`;theme 第 3 级 `system` 本身即「跟随系统」,故链尾不是常量而是动态媒体查询结果。未登录/邀请接受页等无 `users.settings` 场景,直接从第 2 级(工作区默认)起解析——**这是缺口 #1 必须覆盖的路径**。

### 2.3 单一事实源(前端,既有约定延续)

`tokenValues.ts` 为 token 唯一事实源,`tokens.css`/`tokens-dark.css` 须与其逐项一致(测试解析 CSS 断言镜像);新增/修改 token 须同时改三处并由测试兜底防漂移。

---

## 3. 接口设计草图

> REST 基础 `/api/v1`;包络/错误/分页/幂等以 README §6.14 为权威。本模块**不新增业务端点**,仅复用既有偏好写端点 + 一个待权衡的只读端点。

### 3.1 端点清单(复用为主)

| 方法 | 路径 | 说明 | 鉴权 | 现状 |
|------|------|------|------|------|
| PATCH | `/api/v1/users/me` | 写 `settings.theme`(键级浅合并;显式 `null` 清除)。请求体 `{ "settings": { "theme": "dark" } }` | 本人 | **已实现**(auth.md §3.1) |
| PATCH | `/api/v1/workspaces/{id}` | 写 `settings.default_theme` | admin | **已实现**(workspace.md,`validate_theme`) |
| GET | `/api/v1/me` | 返回合并后 `settings`(含 theme),供登录后回填偏好 | 本人 | 已实现 |
| GET | `/api/v1/workspaces/{id}`(detail) | 返回 `settings.default_theme`,供协商链第 2 级读取(列表短响应不含 settings,须读 detail——同 `fetchWorkspaceDefaultLocale` 模板) | 成员 | 已实现(前端补 `fetchWorkspaceDefaultTheme` 消费即可) |
| GET | `/api/v1/theme/tokens`(可选) | 下发当前主题 token 集 | 已登录 | **待权衡,见 3.2** |

### 3.2 `GET /api/v1/theme/tokens` 权衡(建议:不做,纯前端静态)

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| **A. 纯前端静态 token(随构建)** | token 即设计资产,随前端构建产物分发;无网络往返、无服务端状态;`tokens.css`/`tokens-dark.css` 已由 CSS 级联按 `data-theme` 自动选用,运行时零开销;对比度/镜像在 CI 自证 | token 变更须发版(与设计系统迭代节奏一致,非缺陷) | **推荐**。token 是设计契约不是业务数据,无运行时动态性,服务端化徒增攻击面与耦合 |
| B. `GET /theme/tokens` 服务端下发 | 理论上可不发版改色 | 引入服务端真源与前端 CSS 双真源(漂移温床,正是 i18n `default_language` 旧列被废的同类教训);需缓存/ETag;首帧前需阻塞拉取或退回静态(防闪烁复杂化);token 非多租户数据,服务端化无隔离收益 | 不推荐;除非未来出现「工作区品牌色」需求(本期非目标,§8),否则 YAGNI |

### 3.3 错误码

| HTTP | code | 触发 | 说明 |
|------|------|------|------|
| 422 | `validation_error`(现状) | `settings.theme` / `settings.default_theme` 不在 `{light,dark,system}` | `validate_theme` 已带 `details:{theme, supported}`;前端可按 `details.supported` 提示 |
| 422 | `invalid_theme_mode`(**可选升格**) | 同上 | **权衡**:locale/timezone 均具名(`unsupported_locale`/`invalid_timezone`),theme 独用通用码不一致;升格具名码利于前端按 code 渲染本地化错误文案(i18n §3.4)与跨模块对齐。**建议升格**,但因前端三态下拉本地产出合法值、非法值仅可能来自越权/旧客户端,优先级低;无论是否升格,均须同步 auth.md / workspace.md / i18n.md 错误码表与 §6.14 词汇 |
| 400 | `invalid_request` | PATCH 含未知字段 | 沿用 auth.md §3.1 既有语义 |

---

## 4. UI 设计

**4.1 切换入口(两层)**
- **个人偏好**:设置 → 外观 → 主题下拉(light/dark/system),即时生效(已实现于 `SettingsPage.tsx`)。建议另在顶栏/命令面板提供快捷切换(`system` 态标注当前系统解析值,如「跟随系统(暗)」,让用户预知结果)。
- **工作区默认**:工作区设置 → 默认主题(admin 可见),写入 `settings.default_theme`;文案说明「成员未单独设置时生效」。当前 `WorkspaceSettingsPage.tsx` **无此入口(缺口 #1 的 UI 面)**,可复用同款三态 Select。

**4.2 切换器交互(即时预览)**:选项变更即落 `data-theme`,所见即所得,无「保存」按钮、无刷新;`system` 选项旁实时显示当前系统解析结果。

**4.3 token 命名规范与清单结构**
- 命名 `--color-<语义>[-<状态>]`(kebab-case),表意不表值(禁 `--color-red` 式);状态色成对出现 `--color-<tone>` + `--color-<tone>-contrast`(文本/底色两用,配对 ≥4.5:1)。
- 现有清单(亮/暗各一份,一一对应):表面文本 `bg/surface/surface-raised/text/text-muted/border`;品牌 `primary/primary-contrast`;状态 `danger/warn/success/info` 各 + `-contrast`;`focus-ring`;`scrim`;非颜色 `space-1..6`(4/8/12/16/24/32)、`radius-sm/md/lg`、`font-size-sm/md/lg`、`font-family`、`shadow-raised`、`duration-fast/slow`。
- 建议演进:抽出**基础色板层**(原始刻度,组件禁直用)与**组件层**(按部件+状态命名解析到语义层),使暗色/高对比替换面更清晰(§1.5;可按 YAGNI 渐进,出现真实分化需求再抽组件层)。

**4.4 暗色模式细部处理**
- **阴影**:暗色下加深(`shadow-raised` 亮 `rgba(15,23,42,.16)`→暗 `rgba(0,0,0,.55)`,已实现);raised 表面按层级提亮(`surface-raised` 暗 `#334155`)表达层级,而非仅靠阴影。
- **焦点环**:暗色用更亮焦点色(`focus-ring` 亮 `#2563eb`→暗 `#60a5fa`),`:focus-visible` 2px + offset(已实现)。
- **图片/头像**:用户头像为数据不主题化,暗色下可加 1px 语义 border 或轻微降亮度;装饰 SVG 用 `currentColor`/`<picture media>`(可选增强)。
- **遮罩**:`scrim` 暗色加深(`rgba(0,0,0,.6)`)。
- **原生控件**:经 `color-scheme` 随主题(已实现)。

**4.5 数据可视化双色板**:图表色一律引用语义 token(status/danger/warn/success/info + 中性),亮/暗各校准;类别色板(多系列)若需扩展,应在语义层增设 `--color-chart-N` 并双主题校准、纳入 `AA_CONTRAST_PAIRS` 校验;颜色不作唯一信号(线型虚实 + 图标 + 文字,analytics.md §4.5)。

---

## 5. UX 设计

- **system 实时性**:用户选 `system` 后,操作系统切换深浅色 → 应用即时跟随(`matchMedia change`),无需重启/刷新(已实现,测试覆盖)。
- **切换无刷新、不重放动画**:仅属性切换;主题过渡若启用须 gate 在首帧后(避免首帧渐变),且受 `prefers-reduced-motion` 约束(减少动效则无过渡)。
- **首次访问 / 未登录**:无账号偏好 → 工作区默认 `default_theme`(邀请接受页/公开页等);无工作区上下文 → `system`。**当前前端直接落 `system`,跳过工作区默认(缺口 #1)**;防闪烁脚本仅读本地镜像键,未登录无镜像时落 system——邀请页接通工作区默认需在登录后/进入工作区上下文时补协商(或邀请链接携带工作区默认的内联兜底)。
- **跨设备一致性**:账号偏好经 `PATCH /users/me` 持久化服务端,登录即回填(`GET /me`);本地 persist 仅作降级镜像与防闪烁首帧用;服务端为跨设备真源。
- **降级**:服务端同步失败不回滚本地(乐观 + 降级镜像),错误经 `lastSyncError` 按 code 渲染本地化提示;离线本地偏好仍可用。

---

## 6. 安全要点

| 面 | 结论 |
|----|------|
| **CSS 变量注入面** | 主题只接受**受控 token 名/模式枚举**(`light/dark/system`),不接受任意颜色值/URL。`data-theme` 仅取 `light|dark`(脚本与 `resolveTheme` 均收敛到二值),用户无法经偏好注入任意 CSS 自定义属性值;token 值全部来自构建期静态 CSS,非运行时拼接用户输入。**主题不引入任意值注入面**。 |
| **数据色例外(标签/头像底色)** | ColorPicker 自定义 hex 经 `^#[0-9a-fA-F]{6}$` 严格校验后才作 CSS 值,仅用于标签底色(数据),不进入全局 token;其上的文本/前景对比由组件保证。该面是「受控格式的用户数据色」,非主题注入面,但须在 Spec 显式登记为硬编码扫描白名单(§7)。 |
| **CSP 与内联防闪烁脚本** | 防闪烁脚本是 `<head>` 内**内联同步脚本**,与「`script-src` 禁 `unsafe-inline`」冲突。**协调方案**:为该内联脚本配**每请求 nonce**(CSP `script-src 'nonce-{RANDOM}'`,脚本标签带 `nonce`),或对其内容做 `sha256` 哈希白名单(`script-src 'sha256-...'`,脚本内容固定可预计算)。建议 nonce 方案(与既有 CSP 一致);**绝不**为它放开 `unsafe-inline`。脚本不含用户输入,哈希方案亦可。 |
| **用户可控 URL(avatar_url/logo_url)** | 沿用 §6.16:scheme 校验仅允许 `https`,拒 `javascript:`/`data:`。主题功能**不新增**用户可控 URL 面(头像/Logo 为既有数据);暗色下头像处理仅为呈现层(描边/降亮度),不改 URL 校验。 |
| **偏好写入鉴权** | `PATCH /users/me` 仅本人;`PATCH /workspaces/{id}` 的 `default_theme` 需 admin/workspace:settings 权限(auth.md §3 权限矩阵);非法枚举值 422。变更写 `audit_logs`(auth.md 既有)。 |

---

## 7. 验收手段

**7.1 对比度 CI(把 `contrast.ts` 从单测升为门禁)**
- 现有 `tokens.test.ts` 已在 vitest 内对亮/暗两套逐对断言 ≥4.5:1;**升格为独立 CI 关卡**:抽 `scripts/check-contrast`(node)直接 import `tokenValues.ts` 的 `LIGHT_TOKENS/DARK_TOKENS/AA_CONTRAST_PAIRS` + `contrastRatio`,任一配对 < 阈值 → 进程非零退出 → PR 失败。
- 阈值:正文/状态色文本 ≥4.5:1,大号文本与图形元件 ≥3:1;新增 token(如图表 `--color-chart-N`)须先登记进 `AA_CONTRAST_PAIRS` 再合入。
- 可选增强:渲染层用无头浏览器 + 可访问性引擎对真实页面做对比度扫描(覆盖运行时合成色/状态),作为 token 级静态校验的补充(token 级是必要门槛,渲染级捕捉组合态)。

**7.2 硬编码色值扫描规则**
- 规则:组件/特性目录的 `*.css`/`*.tsx` 中,颜色属性(`color/background-color/border-color/outline-color/fill/stroke` 及 `box-shadow` 颜色位)**禁止** `#hex` / `rgb()/rgba()` / 命名色,必须 `var(--token)`。
- 实现:CSS 静态检查(声明值白名单规则:上述属性值须为 `var(--*)`,白名单放行 `transparent/currentColor/inherit/initial/revert/unset`)+ 一条仓库 grep 门禁(扫描 `#[0-9a-fA-F]{3,8}` / `rgba?(` 命中非白名单文件即失败,复用本调研使用的扫描式)。
- **白名单(显式登记,非默认豁免)**:`design/tokens.css`、`design/tokens-dark.css`(token 单一事实源)、测试夹具;数据色例外(`labels/ColorPicker.tsx` 预设板、`MemberAvatarWall.tsx` 头像底色)须在白名单**逐文件登记并注释原因**,新增例外需评审。
- 落地后即清缺口 #2:`skills.css`(≈52)/`autopilots.css`(≈18)/`dataJobs.css`(≈7)/`projects.css`(≈1)的硬编码值须迁移到语义 token(缺失的语义先在 token 源补,再替换)。

**7.3 暗色快照 / 视觉回归**
- 对每个核心页面(看板/issue 详情/成员/聊天/运行详情/收件箱,§6.12 异常态矩阵页面)在 `light` 与 `dark` 两态各截屏,接入视觉回归(Playwright 双主题存证,沿用 kanban 已接入的存证去重 CI 范式);关键断点(≥1024 桌面 / 768 平板)各一份。
- 既有 `ThemeProvider.test.tsx`(解析/实时跟随/即时切换/卸载注销)与 `tokens.test.ts`(镜像/一一对应/AA)作为单元层回归常绿。

---

## 8. 边界与非目标

- **不做多主题自定义编辑器 / 用户自定义品牌色**:本期主题仅 `light/dark/system` 三态,不开放用户编辑调色板/品牌色。token 是设计契约,品牌一致性优先于个性化。(未来若需「工作区品牌色」,再评估 §3.2 方案 B 服务端下发,届时须解决双真源漂移与首帧阻塞。)
- **不做主题市场 / 分享**:无主题打包/导入/社区分享。
- **高对比度主题是否单列**:**建议本期不单列独立 `high-contrast` 主题集**,而以 `@media (prefers-contrast: more)` 媒体查询在亮/暗各自增强(已实现 border 增强,可扩展文本/焦点)。理由:独立第三套 token 集使「模式 × 对比」矩阵翻倍、维护与 AA 校验成本上升,而 `prefers-contrast` 已覆盖系统级高对比诉求;待真实用户量级与无障碍审计压力到位,再升级为独立主题集(届时按 §1.5 组件层抽离顺势扩展)。
- **不改存储层时间语义**(UTC 不变,§6.18)、**不新增业务表**、**不自定义角色/权限模型**(沿用 auth.md RBAC)、**不约束前端框架**(README §3.2)。
- **图表库实现**:analytics.md §4.5 已立约,具体图表组件实现属 analytics 模块,本 Spec 仅定义「图表色经语义 token + 双主题校准 + 颜色非唯一信号」的约束面。

---

### 附:与 i18n.md 的同构关系(便于 Spec 撰写对齐)

| 维度 | i18n(已落地范式) | theme(本调研结论) |
|------|-------------------|--------------------|
| 账号偏好键 | `users.settings.locale`(null=跟随下一级) | `users.settings.theme`(null=跟随下一级) |
| 工作区默认键 | `workspaces.settings.default_locale`(默认 en) | `workspaces.settings.default_theme`(默认 system) |
| 协商链 | 请求参数→用户→工作区→`en` | 用户→工作区→`system`(→prefers-color-scheme) |
| 写端点 | `PATCH /users/me` + `PATCH /workspaces/{id}` | 同(复用) |
| 错误码 | `unsupported_locale` / `invalid_timezone`(具名) | 现状通用 `validation_error`,建议升格 `invalid_theme_mode` |
| 工作区默认读取 | `fetchWorkspaceDefaultLocale` + `negotiate.ts` | **待补 `fetchWorkspaceDefaultTheme` + 解析链(缺口 #1)** |
| 防闪烁/首帧 | 目录版本 ETag 缓存 | `index.html` 内联同步脚本 + `mesh.theme` 镜像键(已实现) |
