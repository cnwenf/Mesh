# 竞品 Web 前端逐页对照清单（验收基线）

> **MES-108 阶段一交付物**：本清单是「前端全面优化、与竞品对齐」专项的**验收基线**——竞品 Web 端页面/组件/状态的全集穷举 + Mesh 现状逐项对照。它同时是设计优化 Spec 的交叉输入，与阶段三最终验收的逐项核对依据。
>
> - **基线代码**：`main` @ `b62e6baf`（2026-07-30）。
> - **现状列性质**：代码/静态勘察快照（含 i18n 目录、CSS、路由表全量扫描），**非最终验收结论**；阶段三以真实 e2e + 真实 UI 操作复验后改写为最终判定并附存证。
> - **竞品依据**：`docs/research/*.md`（设计调研原始记录）+ `docs/specs/features/*.md` 与 `docs/specs/README.md` §6 全局契约（二者冲突以 specs 为准）。
> - **本清单只产基线，不做最终验收**；最终验收（逐项核对实施结果、存证、放行合入发版）在阶段三另起 Issue。

---

## 0. 使用说明与图例

### 0.1 现状标注

| 标记 | 含义 |
| --- | --- |
| ✅ 已覆盖 | 勘察确认已实现，与竞品/Spec 要求一致 |
| 🟡 部分覆盖 | 已实现但与竞品存在明确差距，或仅部分场景实现 |
| ❌ 缺失 | 竞品/Spec 要求但未实现（**必修**，列入 §4） |
| ⬜ 待实机 | 静态勘察不能定论，阶段三实机走查判定 |
| ⚪ 可选 | 竞品明确标注「可选增强」，本期非必做（列入 §5 备查） |

### 0.2 勾选规则

- 每条 `- [ ]` 是一个**可独立验证**的验收断言；阶段三逐条走查后勾选。
- **任一未勾选 = 验收不通过**。⚪ 可选条目不参与放行判定，但改动涉及相关区域时不得使其退化。
- 每个模块末尾有**四组合走查条目**（桌面+亮 / 桌面+暗 / 手机+亮 / 手机+暗），走查须真实打开页面操作并截图存证，不允许只看桌面亮色一套。
- 条目内 `（依据: …）` 为证据锚点：`research/x.md` 指竞品调研，`specs/features/x.md §y` / `README §6.x` 指 Mesh 自身契约。

### 0.3 页面路由速查（勘察快照）

路由唯一配置于 `frontend/src/App.tsx`（BrowserRouter + Routes，无懒加载）。共 33 条路由，除首页为脚手架演示舞台外均已真实实现；`/skills` 为 Sidebar 死链（路由未注册）。详见各模块条目。

---

## 1. 全局跨切面验收条目

### 1.1 信息架构与导航

- [ ] 顶层导航固定为：收件箱/我的任务、项目、看板/视图、Issue、成员（人+agent 同册）、聊天、小队、自动化（Autopilots/Runtimes）、洞察、集成、设置；guest/agent 角色不见管理区（依据: README §6.12）— 现状 ✅（Sidebar 条目齐全，`shell/Sidebar.tsx`）
- [ ] Sidebar 所有导航项均有对应路由，**点击任何条目不得落入 404**（依据: README §6.12）— 现状 ❌（Sidebar 注册 `/skills` 入口但 App.tsx 无该路由，点击进入 NotFoundPage，`Sidebar.tsx:39`）
- [ ] Settings 不维护独立 Agents 名册；agent 唯一名册入口在成员页（依据: README §6.12「Agent 入口去重」）— 现状 ✅
- [ ] 九条规范深链全部可直达且用于通知/邮件外链：`/w/{ws}/issues/by-identifier/{KEY-N}`、`/projects/{id}`、`/members/{member_id}`、`/agents/{id}`、`/views/{view_id}`、`/executions/{id}`、`/chat/{session_id}`、`/squads/{squad_id}(/tasks/{task_id})`、`/approvals`（依据: README §6.12）— 现状 🟡（`/approvals` 统一审批页、`/members/{member_id}` 成员深链缺失，其余已实现）
- [ ] 旧扁平路由（`/inbox`、`/board`…）经前端路由 replace 迁移到规范路由并保留 query/hash（依据: search-command-palette.md §3.4）— 现状 ⬜ 待实机
- [ ] URL 状态同步：列表筛选/分页/排序、详情 Tab、看板视图等页面状态同步到 URL，刷新不丢失、可分享可收藏（依据: kanban.md §5.1 视图 URL；MES-108 交互优化）— 现状 🟡（看板 `/views/{id}`、issue 列表 useSearchParams 已实现；其余页面待验）
- [ ] 浏览器标签页标题随页面语义变化（如「MES-123 修复登录 · Mesh」），通知未读可反映到标题/favicon（依据: MES-108 完成品体验）— 现状 🟡（MES-124 批次① 经 `hooks/useDocumentTitle` 在登录/注册/MFA/找回/重置/设备/邀请/首页落地「<页面> · Mesh」;其余应用页随后续批次补齐,未读反映标题待做）
- [ ] 工作区切换器（左上角下拉）列出用户全部工作区 +「创建工作区」，切换后整页上下文刷新（依据: workspace.md §4.1）— 现状 ✅
- [ ] 404 页提供回首页/回工作区的可操作出口，非裸错误（依据: README §6.12 异常态矩阵）— 现状 ⬜ 待实机

### 1.2 设计令牌与视觉语言

- [ ] 颜色/间距/圆角/字号/阴影/动效时长全部经语义 token（单一事实源生成，TS↔CSS 漂移有测试守卫），组件禁硬编码色值并有 CI 扫描（依据: theme.md §2/§5.4）— 现状 ✅（`design/tokenValues.ts` + `scripts/gen-tokens.mjs`）
- [ ] 完整 type scale：显示字体 + 正文字体配对，标题/正文/辅助/数字层级对比强烈且一致；中英文混排行距/段距/标点经过处理（依据: MES-108 排版优化）— 现状 🟡（token 仅 sm/md/lg 三档字号，无完整 type scale 与中文排版专项处理）
- [ ] 基础组件齐备：Button/IconButton/Input/Select/Dialog/Toast/Banner/EmptyState/ErrorState/Skeleton/StatusDot/Kbd（依据: theme.md/README §6.12）— 现状 ✅（13 个，均带单测）
- [ ] 中间层复合组件统一提供而非各 feature 自造：Dropdown/Menu、Avatar、Tabs、Tooltip、Accordion（依据: MES-108 视觉优化「杜绝东拼西凑」）— 现状 ❌（五者均无设计层实现；Avatar 由 chat/squads 各自造，Tabs 由 projects/integrations/agents 各自内联）
- [ ] 图标体系统一（同一来源/线宽/尺寸网格），无混用多套图标风格（依据: MES-108 视觉优化）— 现状 ⬜ 待实机
- [ ] 卡片/列表/详情的层次与留白一致：surface 层级（bg/surface/surface-raised）使用规范，阴影/边框语义统一（依据: theme.md §2.1）— 现状 ⬜ 待实机
- [ ] 空状态插画风格统一，随主题语义 token 适配亮/暗（依据: onboarding.md §1.2.2）— 现状 ✅（7 个定制 SVG，`features/onboarding/illustrations.tsx`）
- [ ] 信息密度与竞品对齐：列表行高、卡片内边距、每屏信息量逐页对照无明显稀疏/拥挤差距（依据: MES-108 审查维度）— 现状 ⬜ 待实机逐页对照

### 1.3 主题与暗色模式

- [ ] 三态切换器 light/dark/system，即时生效无刷新无保存按钮；system 旁实时显示系统解析值（依据: theme.md §3/§4.1）— 现状 ✅（`design/themeNegotiation.ts`）
- [ ] 协商链：用户偏好（absent/null=继承）→ 工作区默认 → 系统 prefers-color-scheme，实时跟随系统变化（依据: theme.md §2.1/§2.2）— 现状 ✅（`state/workspaceThemeBridge.ts` 联动）
- [ ] 防 FOUC：首帧前同步应用偏好（入口注入 → 分区 locator → 中性 skeleton 三级），亮/暗/邀请页三场景 e2e 门禁（依据: theme.md §2.3/§5.1）— 现状 ✅（`design/themeLocator.ts`、`ThemeSkeleton.tsx`）
- [ ] 暗色非机械反色：压缩对比、raised 表面按层级提亮、动作色向亮偏移、状态色降饱和、阴影加深；亮/暗 token 键一一对应整组替换（依据: theme.md §2.4）— 现状 ✅（`tokens.css`/`tokens-dark.css`）
- [ ] 亮/暗两套均达 WCAG AA（正文 ≥4.5:1、大文本/图形 ≥3:1），对比度 CI 独立关卡（依据: theme.md §5.4）— 现状 ✅（`scripts/check-contrast.mjs`）
- [ ] `color-scheme` 联动原生控件（滚动条/下拉/焦点环随主题）；`meta theme-color` 双声明（依据: theme.md §4.2）— 现状 ✅
- [ ] forced-colors 适配、打印强制亮色、prefers-reduced-motion 关动画、prefers-contrast: more 增强、reduced-transparency 降级（依据: theme.md §4.2/§4.3）— 现状 ✅（`design/base.css`）
- [ ] 跨标签页主题同步（storage 事件）（依据: theme.md §3.3）— 现状 ⬜ 待实机
- [ ] **工作区默认主题设置入口**（admin 可见，「成员未单独设置时生效」）（依据: theme.md §4.1）— 现状 ❌（Spec 自认「当前无此入口，随协商链一并落地」）
- [ ] 未登录邀请接受页主题随工作区默认（经公开 invitation preview，不暴露完整 workspace detail）（依据: theme.md §2.2/§3.1）— 现状 ⬜ 待实机
- [ ] UGC 内联色（标签 chip/头像底色）双主题对比达标，on-color 按亮度阈值自动取黑/白，有颜色守卫（依据: theme.md §2.5）— 现状 ✅（`design/ugcColorGuard.ts`）
- [ ] 四组合：主题相关改动须在桌面+亮、桌面+暗走查通过（暗色视觉回归基线门禁 maxDiffPixelRatio ≤0.01）（依据: theme.md §5.4）— 现状 ✅（`playwright.visual.config.ts`）/ ⬜ 新页面待补基线

### 1.4 响应式与移动端（四组合之手机两套）

- [ ] 断点系统：≥1024 桌面 / 768 平板两档布局适配，有统一断点工具（hook 或 CSS 约定）（依据: README §6.12）— 现状 ❌（无断点系统；全库仅 5 处零散 `@media`）
- [ ] 移动端主导航可用：≤768px 侧栏不得直接消失，须有汉堡菜单/抽屉替代，可到达全部一级页面（依据: MES-108 移动端适配）— 现状 ❌（`shell.css:402` 侧栏 `display:none` 无替代，移动端仅余工作区切换器/铃铛/命令面板）
- [ ] 触控目标 ≥44×44px（按钮/图标按钮/链接行），有全局最小尺寸规则（依据: MES-108 交互优化「触控目标」）— 现状 ❌（Button 实测高约 36px，IconButton 更小，无最小尺寸规则）
- [ ] 移动端「只读优先」落地细则：核心页面（收件箱/issue 详情/看板/聊天）手机端布局与降级方案（依据: README §6.12 方针）— 现状 ❌（Spec 仅一句方针，无任何模块细则；**须补进 Spec**）
- [ ] issue 详情 ≤1023px 双栏折单栏，属性侧栏可折叠/后置（依据: 竞品详情页抽屉/全屏双形态，issue.md §4.1）— 现状 🟡（折单栏已实现 `issues.css:201`，无抽屉形态与侧栏折叠交互）
- [ ] 聊天 ≤720px 会话列表折单栏且有列表↔会话切换交互（依据: chat-session.md §4.1）— 现状 🟡（折单栏已实现 `chat.css:14`，无切换交互）
- [ ] 看板移动端：横向滚动列 + 卡片全宽，拖拽降级为状态菜单操作（依据: kanban.md 视图体系 + 移动端方针）— 现状 ❌（未适配）
- [ ] 表单/弹窗移动端适配：Dialog 全屏化、输入区不被软键盘遮挡（依据: MES-108 移动端适配）— 现状 ⬜ 待实机
- [ ] 四组合：每个模块验收须附手机+亮、手机+暗走查存证（依据: 本 Issue 验收标准）— 现状 ❌（移动端实质不可用，无法走查）

### 1.5 国际化与时区

- [ ] 支持 zh-CN + en，两套目录键数一致零缺失，缺 key 三级回退（locale→en→key）（依据: i18n.md §2/§3）— 现状 ✅（各 2288 键）
- [ ] 协商链：URL `?locale` → 账号偏好 → 工作区默认（唯一真源 `settings.default_locale`）→ navigator.languages → en（依据: README §6.18；i18n.md §2.2）— 现状 ✅
- [ ] locale 切换即时无刷新，已渲染文案就地更新；目录按 version ETag 缓存（依据: i18n.md §4.2）— 现状 ✅（远端目录 ETag/304）
- [ ] 日期/时间/数字/相对时间按 locale + 用户时区渲染（12/24h、千分位、复数规则）（依据: i18n.md §4.4）— 现状 ✅（`i18n/format.ts`）
- [ ] 时区切换全量时间即时重渲染（截止日/创建时间/运行耗时/评论时间），存储恒 UTC（依据: i18n.md §4.3）— 现状 ✅
- [ ] 跨时区共享时间除本地时间外同时标注时区（悬浮显示 UTC 原值）（依据: i18n.md §4.3）— 现状 ⬜ 待实机
- [ ] 用户级语言/时区 + 工作区级默认语言入口，两级关系可视化「跟随工作区默认」占位（依据: i18n.md §4.1）— 现状 ✅（SettingsPage 语言/时区 section + 工作区基本信息）
- [ ] 核心页面（看板/issue 详情/收件箱/成员/设置）无硬编码可见文案；错误码经 `error.<code>` 键本地化（依据: i18n.md §5.1）— 现状 ✅（97 个具名错误键）
- [ ] **无增量期残留文案**：`login.phaseNote`（"Authentication arrives in Phase 2. This placeholder…"）、`members.add.agentComingSoon`（功能已实现）等过期文案清除（依据: MES-107 去脚手架化）— 现状 ❌（上述键仍存在于 `catalogs/en.json`）

### 1.6 搜索与命令面板

- [ ] `Ctrl/Cmd+K` 任意页面（含输入框内）打开命令面板；居中浮层 ~640px，限高内滚动，选中行始终可视（依据: search-command-palette.md §4.1）— 现状 ✅（`shortcuts/CommandPalette.tsx`）
- [ ] **六类对象跨模块搜索**：issue（identifier/标题）、成员、agent、项目、视图、聊天会话，按类型分组组头（依据: search-command-palette.md §1.2 S2）— 现状 ❌（仅检索已注册命令，不检索任何业务对象）
- [ ] 顶栏搜索框为真实控件：输入即展开同一结果视图，`/` 聚焦（依据: search-command-palette.md §1.2）— 现状 ❌（TopBar 搜索框无任何 onChange/提交逻辑，纯装饰，`shell/TopBar.tsx:42-48`）
- [ ] 命令全集九组：顶层导航、设置子页、待审批、新建 issue、主题×4、复制当前深链、收藏/取消收藏、标记全部已读（随当前 filter）、帮助层；无权命令不注册不渲染（依据: search-command-palette.md §1.2 S3）— 现状 🟡（仅导航 10 条 + 主题 3 条 + onboarding 恢复）
- [ ] 空 query 数据流：favorites → recents（本地三元组隔离键）→ 常用命令；失权对象惰性清理（依据: search-command-palette.md §4.2.1）— 现状 ❌（无 favorites/recents 区）
- [ ] identifier 精确命中（`web-124` → `WEB-124`）顶置直达，跳过防抖（依据: search-command-palette.md §2.2）— 现状 ❌（依赖对象搜索）
- [ ] 模糊搜索分层打分 + 命中字符高亮（字重/下划线叠加，不以颜色为唯一信号）（依据: search-command-palette.md §3.1/§4.1）— 现状 ❌（依赖对象搜索）
- [ ] no-results：提示 + 建议 +「新建 issue "xxx"」快捷动作（仅有权限者可见，预填不直接提交）（依据: search-command-palette.md §4.2）— 现状 ❌
- [ ] 离线态：仅本地命令可用 +「网络已断开」提示（依据: search-command-palette.md §4.2）— 现状 ⬜ 待实机
- [ ] 键盘导航 ↑/↓/Enter/Esc/Tab 补全；ARIA combobox+listbox + aria-live=polite；焦点陷落/归还；mod+Enter 新标签打开（依据: search-command-palette.md §1.3/§5.5）— 现状 ⬜ 待实机
- [ ] 底部操作提示条「↑↓ 导航 · Enter 打开 · Tab 补全 · ? 快捷键」（依据: search-command-palette.md §4.1）— 现状 ⬜ 待实机
- [ ] 防抖 120–200ms + 取消过期请求；异步补入按稳定 id 维持选中项不移位（依据: search-command-palette.md §3.3/§4.3.1）— 现状 ⬜ 待实机（对象搜索落地后验）

### 1.7 键盘快捷键

- [ ] 全局组：`mod+K` 面板、`/` 搜索、`C` 新建 issue、`?` 帮助层、`G then I/B/M/A` 序列键（超时窗口 ~1s + 等待提示）、`Esc` 关闭（依据: search-command-palette.md §4.3）— 现状 🟡（`g i/g b/g m/g a`、`c`、`/`、`mod+k` 已注册；序列键等待态提示待验）
- [ ] 看板上下文组：方向键/JKHL 二维网格选卡、`C` 当前列新建（预填分组值）、`S` 状态、`A` 分派、`Enter` 打开、`F` 筛选（依据: search-command-palette.md §4.3）— 现状 ❌（目录声明分组但看板页无注册）
- [ ] issue 详情上下文组：`E` 编辑、`S` 状态、`A` 分派、`P` 优先级、`mod+Enter` 提交评论、`Esc`/`X` 关闭、`←/→` 上下个 issue（依据: search-command-palette.md §4.3）— 现状 ❌（未注册）
- [ ] 聊天上下文组：`Enter` 发送 / `Shift+Enter` 换行、`mod+↑` 编辑上一条、`Esc` 退出焦点（依据: search-command-palette.md §4.3）— 现状 ⬜ 待实机
- [ ] `?` 帮助层：模态浮层按上下文分组仅列当前可用键位，键位平台化渲染（mac ⌘ vs Win Ctrl），序列键拆键帽（依据: search-command-palette.md §4.4）— 现状 🟡（ShortcutHelp 已实现分组展示，上下文动态增减待验）
- [ ] 输入框豁免：焦点在输入控件单字符键不触发；**IME 组合输入期间一切快捷键不触发（含聊天 Enter 发送）**（依据: search-command-palette.md §4.5 P1/P2）— 现状 ⬜ 待实机（中文拼音回归用例）
- [ ] Esc 分层关闭栈：输入控件失焦→顶层子弹层→父弹层→抽屉→列表，每层焦点归还触发元素；弹层内 Tab 焦点圈定（依据: search-command-palette.md §4.5 P3）— 现状 ⬜ 待实机
- [ ] 一切快捷键有等价鼠标路径（依据: search-command-palette.md §4.3）— 现状 ⬜ 待实机逐条核对
- [ ] `/` keydown preventDefault；不占用 mod+W/N/T/R 浏览器保留组合；键位取 event.key 字符语义（QWERTZ/AZERTY）（依据: search-command-palette.md §4.5）— 现状 ⬜ 待实机

### 1.8 异常态矩阵（核心页面必实现）

适用页面：看板、issue 详情、成员、聊天、运行详情、收件箱（依据: README §6.12）。

- [ ] loading = skeleton 骨架屏（非全屏 spinner），gated 于 prefers-reduced-motion（依据: README §6.12）— 现状 ✅（48 个 feature 文件使用）
- [ ] empty = 空态插画 + 文案 + 主操作 + 深链既有向导（依据: onboarding.md §1.2.2）— 现状 ✅（六大空态已接入）
- [ ] error = 具名错误文案 + 重试按钮，非「网络错误」了事；97 个错误码本地化映射（依据: README §6.12；i18n.md §5.1）— 现状 ✅
- [ ] permission denied =「无权限」页 + 联系入口（依据: README §6.12）— 现状 ⬜ 待实机（guest 视角走查）
- [ ] offline = 顶部横幅「网络已断开」+ 乐观操作排队 + 自动重连（依据: README §6.12）— 现状 🟡（StatusBanner + StatusDot 六态已实现；「乐观操作排队」离线队列待验）
- [ ] stale/resync =「正在重新同步…」对账后无感消失（依据: README §6.12）— 现状 ✅（StatusDot resyncing 态）
- [ ] partial failure = 逐项成功/失败标记 + 失败项重试（依据: README §6.12）— 现状 ✅（批量操作返回计数与原因）
- [ ] 全局 ErrorBoundary + ErrorPage（role=alert + 重试清边界）（依据: README §6.12）— 现状 ✅
- [ ] 专项恢复入口五条：看板断线顶部重连指示、日志按 offset 自动续传、附件扫描中占位、agent 无可用 runtime 分派提示链 runtime 页、审批过期「重新发起」（依据: README §6.12）— 现状 ⬜ 待实机逐条验

### 1.9 实时与重连

- [ ] WebSocket 增量合并而非整页刷新：客户端按频道记 last_seq，重连带 resume_from（依据: README §6.7）— 现状 ✅
- [ ] 游标过旧收 resync_required + REST 对账 URL（同源校验后请求，防 Bearer 外泄），对账成功无感恢复（依据: README §6.7）— 现状 ✅
- [ ] 重连指数退避 1s→30s 上限加抖动；页面重新可见 single-flight 重连；30s ping 心跳；WS 首帧认证（禁 URL query 传 token）（依据: README §6.16；chat-session.md §3.3）— 现状 ✅
- [ ] WS 不可用各模块降级轮询（看板/issue 30s since、收件箱 30~60s unread-count、onboarding 30s、agent 5s 轻量状态）（依据: kanban.md §3.5 等）— 现状 ✅
- [ ] 连接状态可见：六态指示（connected/connecting/reconnecting/resyncing/offline/idle）（依据: README §6.12）— 现状 ✅（TopBar StatusDot）
- [ ] 私有项目事件只进 `project:{id}` 频道，不先广播再靠前端过滤（依据: README §6.7）— 现状 ⬜ 待实机（抓帧验证）

### 1.10 通知与收件箱

- [ ] 顶栏铃铛：未读红点 + 数字徽标 + 下拉最近若干条 +「查看全部」（依据: comment-inbox.md §4.2）— 现状 ✅
- [ ] 收件箱页：筛选 tabs（全部/未读/提及/分派/Agent 单列）、按 issue 分组（组头「不再关注此 issue」静音）、hover 行操作、全部已读/归档已读、空态插画（依据: comment-inbox.md §4.2）— 现状 ✅
- [ ] 通知分级唯一矩阵：执行成功 normal 默认不进箱；失败/审批/被分派/被@ critical 穿透 quiet hours；取消不通知发起者（依据: README §6.13）— 现状 ✅（后端矩阵）/ ⬜ 前端呈现待实机
- [ ] group_key 折叠 + 60s 窗口合并计数；已读+过期组自动归档（依据: README §6.13）— 现状 ⬜ 待实机
- [ ] 未读徽标多端同步 P95<1s；quiet hours 不抑制徽标（依据: comment-inbox.md §5.4）— 现状 ⬜ 待实机（双开验证）
- [ ] 点击通知直达评论锚点高亮闪烁并自动标已读；源实体被删凭 payload 快照可读，目标缺失提示「原内容已删除」（依据: comment-inbox.md §4.3/§5.3）— 现状 ⬜ 待实机
- [ ] 偏好矩阵页：事件类型 × 站内开关 × 邮件策略（无/实时/摘要）；Agent 执行通知单独分区；免打扰时段（标注 critical 穿透）+ 摘要频率（依据: comment-inbox.md §4.2）— 现状 ✅
- [ ] 「需人工确认」通知带内联批准/拒绝按钮（依据: agent.md §5.4）— 现状 ⬜ 待实机
- [ ] 邮件通道：摘要按收件人 locale 渲染、评论预览 HTML 转义、点邮件链接回站内对应锚点并自动标已读（依据: comment-inbox.md §4.4；i18n.md §5.1）— 现状 ⬜ 待实机

### 1.11 无障碍（a11y）

- [ ] 全局 `:focus-visible` 焦点环，forced-colors 下随系统 Highlight（依据: README §6.12）— 现状 ✅
- [ ] 脉冲动画/颜色不作唯一状态信号，必叠图标/文字（如「● 处理中」含文字）（依据: README §6.12）— 现状 ⬜ 待实机抽查
- [ ] Toast/Banner aria-live；未读数与运行状态 live-region 播报（依据: README §6.12）— 现状 ✅（Toast role=status）
- [ ] 全键盘可达：Tab 序合理、Enter/Space 激活、弹层焦点陷落归还（依据: README §6.12）— 现状 ⬜ 待实机（键盘遍历核心页面）
- [ ] 尊重 prefers-reduced-motion（全局降级）（依据: theme.md §4.2）— 现状 ✅
- [ ] 屏幕阅读器实测：核心流程（登录→建 issue→评论→收件箱）NVDA/VoiceOver 可走完（依据: MES-108 可访问性维度）— 现状 ⬜ 待实机

### 1.12 收藏 / 固定

- [ ] 统一 favorites 模型（issue/project/view/chat_session），成员私有（依据: README §6.19）— 现状 🟡（chat 置顶经 favorites 已实现；issue/project/view 收藏入口待验）
- [ ] 命令面板空态 favorites 区（GET /favorites，收藏时间倒序）（依据: search-command-palette.md §4.2.1）— 现状 ❌（面板无 favorites 区）
- [ ] 收藏按钮 UI 入口（详情页/卡片 ⋯ 菜单或星标）与管理（依据: README §6.19）— 现状 ⬜ 待实机
- [ ] 聊天会话置顶区在上（置顶唯一真源 favorites，无独立 pin 端点）（依据: chat-session.md §3.2）— 现状 ✅

### 1.13 统一审批

- [ ] 「待我审批」聚合页 `/approvals`：三类审批（工具/squad 计划/autopilot 动作）统一入口（依据: README §6.10）— 现状 ❌（无 /approvals 路由；审批分散在 squad 页与 run 页）
- [ ] 每条审批显示：动作、所需权限（capability+permission）、影响范围、预估成本、过期时间、续跑提示「将从审批点以新尝试恢复:已完成 N 步」（依据: README §6.10）— 现状 ⬜ 待实机（分散入口内核对）
- [ ] 过期显示「已过期」+「重新发起」（依据: README §6.12）— 现状 ⬜ 待实机
- [ ] agent 不可审批，审批入口仅对人类呈现（依据: README §6.10）— 现状 ⬜ 待实机

### 1.14 微交互与动效

- [ ] 设计层基础组件（Button/IconButton/Input/Select）具备 hover/active/focus 完整状态（依据: MES-108 交互优化）— 现状 ❌（components.css 内 :hover/:active 规则数为 0，hover 由各 feature 分散自补，同系统内并存有/无反馈）
- [ ] 拖拽视觉反馈：dragover 目标列高亮、落点占位条、拖拽 ghost/降透明度样式（依据: kanban.md §4.3「目标列高亮」）— 现状 ❌（board.css drag 相关规则数为 0，拖拽过程零视觉反馈）
- [ ] 过渡动画克制统一（时长经 token fast/slow），keyframes 仅用于必要反馈（spinner/骨架/脉冲/流式光标/高亮闪现）（依据: theme.md 非颜色 token）— 现状 ✅
- [ ] Toast：role=status + aria-live=polite，4 tone，自动消失 + 手动关闭 + 可选 action（依据: README §6.12）— 现状 ✅
- [ ] 乐观更新：评论发送 sending 态、看板拖拽松手即落位 <50ms、成员操作即时反馈，失败回滚/标红重试（依据: kanban.md §4.3；comment-inbox.md §4.3）— 现状 ✅
- [ ] hover 操作浮现一致：行/卡片 hover 出操作按钮的交互在各页面表现一致（依据: MES-108 视觉一致性）— 现状 ⬜ 待实机
- [ ] 相对时间自动刷新（「3 分钟前」随停留时间推进更新），全时区一致（依据: i18n.md §4.4）— 现状 ⬜ 待实机
- [ ] 复制操作即时反馈：复制链接/复制激活码/复制 token 后 toast 或图标态变化（依据: MES-108 交互反馈）— 现状 ⬜ 待实机
- [ ] 表单脏状态保护：编辑器未保存离开时确认拦截（autopilot 编辑器/技能编辑/评论草稿）（依据: autopilot.md §4.2 保存草稿；MES-108 交互优化）— 现状 ⬜ 待实机

### 1.15 批量操作 / Feature Flags / 其他横切

- [ ] issue 批量：多选浮出底栏（状态/优先级/assignee/标签/删除），提交后「成功 N,失败 M」逐条原因（依据: issue.md §1.2.5）— 现状 ✅（issues 列表批量已实现）
- [ ] 收件箱批量已读/归档；成员批量转派；技能一绑多 agent；邀请多邮箱批量（依据: comment-inbox.md §3.2；member.md §3.1；skill.md §1.5；workspace.md §3.2）— 现状 ⬜ 待实机逐项核对
- [ ] 批量失败可选短时撤销（依据: issue.md §1.2.5「可选」）— 现状 ⚪ 可选
- [ ] Feature flags 前端消费机制（工作区级功能开关下发 → UI 条件渲染）（依据: workspace.md §2.1 settings「功能开关」）— 现状 ❌（全库无 feature flag 代码，仅构建期 env）
- [ ] 浏览器桌面通知（Notification 权限流 + 尊重免打扰）（依据: comment-inbox.md §4.3「可选桌面 toast」）— 现状 ⚪ 可选
- [ ] Presence / 在线协作感知：成员在线状态、agent 忙碌指示（运行中 N/排队 M/需审批 K 三元组）、看板谁在查看（依据: agent.md §4.9；member.md §5.3；kanban.md §1.4）— 现状 🟡（agent presence 已接入成员页/看板；成员人类在线状态与看板 viewer presence 待验/可选）
- [ ] API 契约 UI 面：限流 429 + Retry-After 的退避提示、Deprecation/Sunset 头的用户可见提示（依据: cli.md §8）— 现状 ⬜ 待实机

### 1.16 前端安全渲染

- [ ] UGC 内容（评论/描述/技能指令/IM 载荷）Markdown 渲染经 DOMPurify 净化，无 dangerouslySetInnerHTML 裸渲染（依据: attachment.md SVG 净化；MES-108 安全维度）— 现状 ✅（dompurify + marked 依赖，待阶段三红队用例复验）
- [ ] 集成事件台账外部载荷标注「不可信数据」，预览仅只读 JSON 不执行（依据: integrations.md §4.2）— 现状 ✅
- [ ] UGC 内联色守卫（style 属性颜色低对比回退/拦截）（依据: theme.md §2.5）— 现状 ✅（`design/ugcColorGuard.ts`）
- [ ] 会话令牌不进 URL/日志；refresh 仅 HttpOnly cookie（依据: auth.md §4.5；README §6.16 WS 首帧认证）— 现状 ⬜ 待实机（抓包验证）

---

## 2. 逐页 / 逐模块验收条目

### 2.1 认证（登录 / 注册 / 忘记密码 / OAuth / 设备码）

页面：`/login`、`/forgot`、`/reset`、`/auth/oauth/callback/:provider`、`/device`（依据: auth.md §4）。

- [ ] 登录页：邮箱+密码、「记住我」、「忘记密码?」、第三方登录按钮组（vendor 中立 env 配置）、注册入口切换（依据: auth.md §4.1）— 现状 ✅
- [ ] 登录失败统一「邮箱或密码不正确」不暴露账号存在性；锁定 423 / 限流 429+Retry-After 具名提示（依据: auth.md §4.1）— 现状 ✅（具名错误 409/400/422/423/429）
- [ ] MFA 二步：TOTP 验证码输入（启用流含密钥+二维码+备用码）（依据: auth.md §4.2）— 现状 ✅
- [ ] 注册页：密码强度条 + 实时校验；提交后「已发验证邮件」结果页（依据: auth.md §4.1）— 现状 ✅
- [ ] 忘记密码/重置完整流程可用，重置后旧会话失效（依据: auth.md §4.1）— 现状 ✅
- [ ] OAuth 回调页：授权中/成功/失败三态；首登自动建号绑定后进新用户引导（依据: auth.md §5.2）— 现状 🟡（MES-124 批次①:三态 + 失败具名 + 恢复动作经 PublicFlowShell;首登引导由 onboarding 模块承载,待实机）
- [ ] 设备码确认页（CLI 配套）：手工录入 user_code + 工作区 0/1/多分流 + scope 展示 + 批准/拒绝（依据: auth.md §5.1）— 现状 ✅（MES-124 批次① 迁入 PublicFlowShell;单测覆盖绑定所录码/0-1-多工作区）
- [ ] 回跳守卫 safeNextPath 防开放重定向（控字符预检 + origin 一致 + 路径 `/` 开头）（依据: auth.md §4.1）— 现状 ⬜ 待实机（构造恶意 next 验证）
- [ ] **无脚手架残留**：dev token 直填入口移除、`login.phaseNote` 占位文案清除（依据: MES-107）— 现状 ✅（MES-124 批次① 核查:dev token 直填/`login.phaseNote`/`PlaceholderPage` 均无残留,旧 `mesh-login` 样式已删）
- [ ] 四组合走查：登录/注册/忘记密码页 × 桌面亮/暗、手机亮/暗（手机端表单可用性重点）— 现状 ✅（MES-124 批次① + 整改补全 注册/找回 四组合,存证 `e2e/evidence/mes111-b1/`）

### 2.2 应用外壳（AppShell：TopBar / Sidebar / 布局）

- [ ] AppShell = TopBar + StatusBanner + Sidebar + Outlet 结构稳定（依据: README §6.12 信息架构）— 现状 ✅
- [ ] TopBar：工作区切换器、搜索入口（真实控件，见 §1.6）、铃铛、命令面板入口、用户菜单（依据: search-command-palette.md §1.2；comment-inbox.md §4.2）— 现状 🟡（搜索为死输入框）
- [ ] 用户菜单：个人设置入口、主题快捷切换（system 态标注解析值）、帮助/快捷键入口、登出（依据: theme.md §3.2）— 现状 ⬜ 待实机
- [ ] Sidebar 分组与顺序符合信息架构约定；当前页高亮；折叠/展开（依据: README §6.12）— 现状 ⬜ 待实机
- [ ] PlaceholderPage 等脚手架组件清除（不得被任何路由引用）（依据: MES-107）— 现状 ✅（MES-124 批次① 核查:`PlaceholderPage.tsx` 已不存在、无路由引用）
- [ ] 四组合走查：外壳整体 × 四组合（重点：≤768px 导航可达性）— 现状 ❌（移动端不可用）

### 2.3 首页

- [ ] **首页为真实产品首页**：工作台视角（我的 issue/收件箱摘要/进行中运行/最近项目），非演示舞台（依据: MES-107 去脚手架化 + MES-108）— 现状 ✅（MES-124 批次①:真实工作台——我的工作/最近项目/快捷创建/工作区;有数据不展示演示内容）
- [ ] 首页空态引导新工作区用户进入 onboarding 五步（依据: onboarding.md §1.2.1）— 现状 ✅（MES-124 批次①:空工作区呈现 OnboardingChecklist）
- [ ] 四组合走查：首页 × 四组合 — 现状 ✅（MES-124 批次① 存证 `e2e/evidence/mes111-b1/`）

### 2.4 工作区与设置

页面：`/w/:slug`、`/w/:slug/settings`（基本信息/邀请/角色/标签/自定义字段/数据/API Tokens/审计/Danger Zone）（依据: workspace.md §4；auth.md §4）。

- [ ] 工作区首页：名称/角色/默认 locale + 管理员入口；**信息密度对齐竞品**（竞品工作区首页承载概览内容，非极简字段页）（依据: MES-108 信息密度维度）— 现状 🟡（极简实现）
- [ ] 创建工作区向导模态框：名称 → slug 实时校验（绿勾/红叉）→ 邀请成员可跳过（依据: workspace.md §4.2）— 现状 ✅
- [ ] 基本信息：名称/Logo 上传/slug（旧链接重定向提示）/时区/默认语言（依据: workspace.md §4.2）— 现状 ✅
- [ ] 邀请面板：多邮箱 chip（回车成 chip、粘贴批量）+ 角色选择 + 生成邀请链接（有效期/使用次数/预设角色）；待处理列表（邮箱/角色/状态/过期/撤销）（依据: workspace.md §4.2）— 现状 ✅（邮件+链接双模式）
- [ ] 邀请接受页 `/invite/:token`：preview → accept；过期/已撤销/超次数/无权限四种失败态 UI（依据: workspace.md §4.3）— 现状 ✅
- [ ] 角色矩阵展示（owner/admin/member/guest × 资源权限）（依据: auth.md §2.7）— 现状 ✅
- [ ] 危险操作区：归档/删除需输入 slug 二次确认，仅 owner 可见（依据: workspace.md §5.3）— 现状 ✅
- [ ] 设置变更在线成员 1s 内收 workspace.updated 刷新（依据: workspace.md §5.4）— 现状 ⬜ 待实机（双开验证）
- [ ] 域名自动加入（企业邮箱域名注册即入组）（依据: workspace.md §1.4「可选」）— 现状 ⚪ 可选
- [ ] 四组合走查：工作区设置各 section × 四组合 — 现状 ⬜

### 2.5 成员

页面：`/members`（依据: member.md §4；agent.md §4.2）。

- [ ] 名册表格：头像+名称(+类型徽章 人/agent) | 邮箱/简介 | 角色下拉 | 状态 | 加入时间 | 操作；筛选（全部/人类/AI agent/已停用）+ 搜索（依据: member.md §4.1）— 现状 ✅
- [ ] 「+邀请/添加」双 Tab 弹窗（邀请人类邮箱 / 添加 AI agent 从列表挑选）；`[ + 新建 Agent ]` 唯一 agent 创建入口（依据: member.md §4.2；agent.md §4.2）— 现状 ✅
- [ ] agent 行：AI 徽章 + 机器人头像样式 + 实时忙碌指示 + 悬停能力简介；角色下拉 owner 选项置灰（依据: member.md §4.2；agent.md §4.2）— 现状 ⬜ 待实机
- [ ] 成员详情抽屉：资料、名下进行中 issue、最近活动；agent 额外运行时状态与配置入口（依据: member.md §4.2）— 现状 ⬜ 待实机
- [ ] 停用/移除二次确认：「是否把其名下未完成 issue 转派给…」+ 转派目标选择器（依据: member.md §4.2）— 现状 ✅（乐观更新）
- [ ] 行内改角色即时生效 + 留痕；last_owner / agent_owner_not_allowed 409 具名提示（依据: member.md §4.3）— 现状 ⬜ 待实机
- [ ] assignee 选择器（issue/看板复用）：人/agent 混列各带类型图标（依据: member.md §4.2）— 现状 ✅
- [ ] onboarding 管理员重置入口：「重置该成员上手进度」二次确认（依据: onboarding.md §4.2）— 现状 ⬜ 待实机
- [ ] 个人资料编辑：用户可更新自己的头像/昵称/bio/时区（依据: member.md §3.1）— 现状 ❌（`PATCH /users/me` 仅承载偏好键 locale/theme/timezone，无 profile 编辑界面）
- [ ] guest 项目级可见性管理：按项目显式共享 read/write 的配置入口（依据: member.md member_project_access）— 现状 ⬜ 待实机
- [ ] 四组合走查：成员页 × 四组合（表格手机端降级形态）— 现状 ⬜

### 2.6 项目与周期

页面：`/projects`、`/projects/:id`（+settings）、`/cycles`（依据: project.md §4）。

- [ ] 项目列表：筛选（状态/负责人/我参与的/已归档）+ 新建；卡片（名称+图标/状态徽章/健康度灯/进度条/负责人头像/目标日期）（依据: project.md §4.1）— 现状 ✅
- [ ] 项目详情 Tab：概览 | Issue(看板/列表) | 里程碑 | 时间线 | 更新动态 | 仪表盘（依据: project.md §4.1；analytics.md §4.2）— 现状 ✅（仪表盘 tab 已接入）
- [ ] 健康度灯红/黄/绿 + 文字，点击展开「更新状态」表单（选健康度+写说明），提交留痕头部即时更新（依据: project.md §4.2）— 现状 ✅
- [ ] 进度条基于 issue 完成率，悬停 done/total，project.updated 实时刷新（依据: project.md §4.2）— 现状 ⬜ 待实机
- [ ] 里程碑时间线横向时间轴，逾期标红（依据: project.md §4.2）— 现状 ✅
- [ ] 周期页：头部燃尽与点数 +「待办·未排期」区拖入排期；周期结束未完成 issue 顺延提示；auto-roll（依据: project.md §4.3）— 现状 ✅
- [ ] 创建项目 key 实时去重校验；删除二次确证明示前缀永久保留（依据: project.md §4.3）— 现状 ⬜ 待实机
- [ ] 项目状态更新流（状态更新留痕：作者+时间+说明）（依据: project.md §2.4）— 现状 ✅
- [ ] 四组合走查：项目列表/详情/周期 × 四组合 — 现状 ⬜

### 2.7 Issue

页面：`/issues`、`/issues/:id`、`/issues/by-identifier/:identifier`（依据: issue.md §4）。

- [ ] 列表页：搜索/分类筛选/分页 + 快速创建（`?create=1` 深链）；行显示 identifier/标题/状态色条/优先级图标/assignee 头像(人/agent)/到期日/标签点（依据: issue.md §4.2）— 现状 ✅
- [ ] 批量操作浮动底栏（状态/优先级/删除）+「成功 N,失败 M」逐条原因（依据: issue.md §1.2.5）— 现状 ✅
- [ ] 详情头部：可编辑标题、identifier、状态选择器（按 category 分组带颜色）、操作菜单（依据: issue.md §4.1）— 现状 ✅
- [ ] 详情主体：富文本描述、子 issue 树（完成进度「3/5」+就地新增）、依赖列表（blocks/blocked by + 阻塞视觉提示）、活动流、评论区、附件区（依据: issue.md §4.2）— 现状 ✅
- [ ] 属性侧栏全集：assignee/reporter/priority/estimate/due/start/project/cycle/milestone/labels/自定义字段，每字段点击即编辑（依据: issue.md §4.2）— 现状 ✅
- [ ] 快速创建轻量表单（标题 + 展开更多字段，支持连续创建），`C` 键触发（依据: issue.md §4.2）— 现状 ✅
- [ ] 分派给 agent：选中 agent 浮出「保存后将自动开始工作」提示；再次选同一 assignee = no-op（依据: agent.md §4.6；README §6.9）— 现状 ⬜ 待实机
- [ ] 分派给小队：头部呈单一责任主体「leader 头像 + squad 徽章『X 小队 · leader Y 牵头』」（依据: squad.md §4.3）— 现状 ⬜ 待实机
- [ ] 跨项目迁移两步式：改 project/跨项目拖拽 → 迁移预览（映射/清除/保留清单）→ 确认单事务（依据: issue.md §3.8；README §6.14）— 现状 ✅（看板拖拽含迁移确认）
- [ ] 成环检测：父子环/依赖环就地报错不创建（依据: issue.md §4.3）— 现状 ⬜ 待实机
- [ ] 状态流转严格模式（可配「允许的下一步」）在选择器体现（依据: issue.md §3.4）— 现状 ⬜ 待实机
- [ ] 乐观并发：409 conflict 拉最新收敛无数据丢失（依据: issue.md §3.5）— 现状 ⬜ 待实机
- [ ] 实时更新：列表/收件箱收 issue.updated 按 id 增量合并行（非整页刷新）；WS 断 30s since 轮询（依据: issue.md §3.6）— 现状 ✅
- [ ] 智能链接：`#MES-123` 简写自动补全成链（依据: comment-inbox.md C6）— 现状 ⬜ 待实机
- [ ] 粘贴完整 issue URL 探测 + 引用卡片渲染（依据: comment-inbox.md 实现注记 9「延期项」）— 现状 ⚪ 可选（Spec 明确延期）
- [ ] 四组合走查：列表/详情 × 四组合（详情手机端单栏布局重点）— 现状 🟡（1023px 折单栏已有，手机端整体待验）

### 2.8 看板与视图

页面：`/board`、`/views/:viewId`（依据: kanban.md §4）。

- [ ] 布局：顶部工具条（视图名|筛选|分组|排序|显示字段|保存/另存）+ 可选泳道 + 列容器横向滚动；列头（状态色+名称+计数+WIP `4/5`）+ 列底「+新增」（依据: kanban.md §4.1）— 现状 ✅
- [ ] 分组：status/priority/assignee/project/label + 泳道子分组（依据: kanban.md §4.2）— 现状 🟡（label/自定义字段分组依赖关联层门控 `projection_field_pending`）
- [ ] 过滤构建器：多条件嵌套 AND/OR + 自定义字段 + 实时预览命中数（依据: kanban.md §4.2）— 现状 ✅
- [ ] WIP：warn=红色徽章+toast / block=落点禁用+422 弹回（依据: kanban.md §4.4）— 现状 ✅
- [ ] 拖拽换位：乐观落位 <50ms，列内排序浮点中点法（视图隔离），WIP 拦截，跨项目迁移确认（依据: kanban.md §4.3）— 现状 ✅（功能层）
- [ ] **拖拽视觉反馈**：目标列 dragover 高亮、落点占位条、卡片 ghost（依据: kanban.md §4.3）— 现状 ❌（零视觉反馈）
- [ ] **列底「+新增」快速建卡**：继承该列分组值，回车即现新卡片（依据: kanban.md §4.5）— 现状 ❌（按钮禁用，文案「arrives with the issue projection increment」）
- [ ] 视图体系：切换/保存/另存/重命名/复制/删除/设默认 + URL 同步 `/views/{id}` + 未保存改动「保存/另存/丢弃」（依据: kanban.md §4.2/§5.1）— 现状 ✅
- [ ] 卡片字段受 card_fields 控制（标签点/估点/子任务进度/assignee 头像）；列可折叠（依据: kanban.md §4.2）— 现状 ✅
- [ ] 实时增量合并：收 issue.* 本地重判归属单卡增删移，**禁止整板刷新**；丢弃旧于本地的事件无回退闪烁（依据: kanban.md §3.5/§5.2）— 现状 ✅
- [ ] **List 布局**：可配置列/列头排序/行内编辑/多选批量条（依据: kanban.md §1.2）— 现状 ❌（空态占位 `board.listPlaceholderTitle`）
- [ ] Timeline/Table 布局（依据: kanban.md §1.3）— 现状 ⚪ 可选（Spec 明确 YAGNI 延期，501 兜底为 board/list）
- [ ] 性能线：1000 卡片列滚动 ≥50fps（虚拟滚动）；单条实时事件本地处理 <16ms（依据: kanban.md §5.3）— 现状 ❌（无虚拟滚动实现）
- [ ] view.presence 协作者头像渲染（依据: kanban.md §3.5「可选」）— 现状 ⚪ 可选
- [ ] 动态分组列（分组值新增自动出现列）（依据: kanban.md 视图投影）— 现状 🟡（占位 `board.dynamicColumnsPlaceholder`）
- [ ] 四组合走查：看板 × 四组合（手机端横向滚动列 + 拖拽降级）— 现状 ❌（移动端未适配）

### 2.9 标签与自定义字段

页面：`/w/:slug/settings/labels`、`/w/:slug/settings/custom-fields`（依据: label-property.md §4）。

- [ ] 标签列表：色点 | 名称 | 作用域 | 使用次数 | 操作（编辑/合并/删除）（依据: label-property.md §4.1）— 现状 ✅
- [ ] 标签选择器：输入联想 + 彩色 chip + 就地新建「新建 'xxx'」弹颜色选择；项目级标签仅对应项目联想中出现（依据: label-property.md §4.2）— 现状 ⬜ 待实机
- [ ] 标签合并 UI：选源→目标→确认影响数→执行，所有卡片色点更新（依据: label-property.md §4.4）— 现状 ⬜ 待实机
- [ ] 自定义字段 10 类型控件：text/textarea/url/number(精度)/date/datetime/single_select(带颜色)/multi_select(chip)/member(人+agent)/boolean（依据: label-property.md §4.3）— 现状 ✅
- [ ] 字段定义编辑器：选项增删改+拖拽排序+配色、必填开关、默认值、作用域、停用/激活（依据: label-property.md §4.3）— 现状 ✅
- [ ] 必填校验：状态流转到配置 category 时缺失就地阻断 `required_field_missing`（依据: label-property.md §4.5）— 现状 ⬜ 待实机
- [ ] 卡片/行标签色点紧凑呈现，多标签溢出 `+N`（依据: label-property.md §4.2）— 现状 ⬜ 待实机
- [ ] 枚举选项停用后所有打开下拉的客户端即时更新（依据: label-property.md §5.3）— 现状 ⬜ 待实机（双开验证）
- [ ] 四组合走查：标签/字段设置页 × 四组合 — 现状 ⬜

### 2.10 评论与活动流

区域：issue 详情评论区（依据: comment-inbox.md §4；chat-session.md 评论协作）。

- [ ] 活动流 + 评论混合时间线（系统活动灰色小字与评论卡片穿插）（依据: comment-inbox.md §4.1）— 现状 ✅
- [ ] 评论卡片：头像 | 作者名+身份徽标(人/agent) | 相对时间 |「已编辑」；Markdown 渲染（代码高亮/任务清单/表格）；操作条（回复/表情/更多:复制链接/编辑/删除/解决线程）（依据: comment-inbox.md §4.1）— 现状 ✅
- [ ] 线程单层折叠「N 条回复 ▸」+ 解决/重新打开（留痕解决人/时间）+「✓ 已解决线程 (N)」区（依据: comment-inbox.md §4.1）— 现状 ⬜ 待实机
- [ ] Reaction：emoji chip（`👍 2`）点击增减 +「+」选择器 + 自己可取消（依据: comment-inbox.md F7）— 现状 ✅（CommentCard 已实现）
- [ ] composer：底部固定、Markdown 工具条、@ 补全（agent 项标「发布后将触发一次运行」）、附件拖拽/粘贴、编辑/预览切换、Cmd+Enter 提交、草稿按 issue 本地暂存（依据: comment-inbox.md §4.1/§4.3）— 现状 ✅
- [ ] @agent 副作用 UI：选中后常驻轻提示条；提交前 trigger preview 列出将触发 agent + 显式抑制开关「仅通知,不触发运行」（依据: comment-inbox.md §4.1；README §6.9）— 现状 ✅（triggerPreview + suppress_triggers）
- [ ] @agent 提交后「⏳ 正在执行…」占位卡片，完成替换为评论，失败留失败占位 +「重试」（依据: comment-inbox.md §3.5）— 现状 ⬜ 待实机
- [ ] 乐观更新：sending 态出现 → WS 广播最终态；失败标红「重试」；删除评论留占位「该评论已删除」（依据: comment-inbox.md §4.3）— 现状 ⬜ 待实机
- [ ] 评论深链可复制、跳转并高亮闪烁（依据: comment-inbox.md C12）— 现状 ⬜ 待实机
- [ ] 实时：comment.created/updated/deleted/resolved、reaction.changed P95<1s；多端已读同步（依据: comment-inbox.md §5.4）— 现状 ⬜ 待实机
- [ ] 四组合走查：评论区 × 四组合 — 现状 ⬜

### 2.11 聊天

页面：`/chat`（依据: chat-session.md §4）。

- [ ] 布局：左=会话列表（置顶优先+倒序；agent 头像+标题+预览+时间；[+新建][搜索]；按 agent/状态筛选；归档区底部）；右上=上下文关联条；中=对话流；底=输入区（依据: chat-session.md §4.1）— 现状 ✅
- [ ] 对话流：用户/agent 气泡左右区分，agent 侧 AI 徽章；流式光标打字机；生成中 [■停止]、完成后 [↻重新生成]；多候选 `‹ 1/3 ›` 翻页 +「使用此条」（依据: chat-session.md §4.2）— 现状 ✅
- [ ] 流式 SSE：逐 token + Last-Event-ID 断点续传 + 15s 心跳；缓冲淘汰降级 REST 拉最终内容（依据: chat-session.md §3.3）— 现状 ✅
- [ ] 中断幂等：重复 stop 无副作用，保留已生成部分标「已中断」（依据: chat-session.md §3.3）— 现状 ✅
- [ ] 单并发：重复发送/regenerate 409 `generation_in_progress`，UI 不允许并发提交（依据: chat-session.md §3.5）— 现状 ⬜ 待实机
- [ ] failed 消息保留内容 + 重试入口（依据: chat-session.md §4.4）— 现状 ⬜ 待实机
- [ ] 会话标题首轮后自动生成（可手动重命名），列表实时预览/时间更新（依据: chat-session.md §1.2 A8）— 现状 ✅
- [ ] 上下文关联选择器：搜索 issue/项目单选，提示「agent 将读取关联上下文作为背景」（依据: chat-session.md §4.2）— 现状 ✅
- [ ] 「沉淀为 issue 评论」闭环：预览（目标 issue/正文/附件/@agent 副作用）→ 确认提交，可勾「仅通知不运行」（依据: chat-session.md §4.3）— 现状 ✅（distill）
- [ ] 会话内附件上传（扫描完成才可见）+ 消息引用（依据: chat-session.md §1.2 A9）— 现状 ✅
- [ ] 置顶经 favorites、会话搜索、归档/删除（依据: chat-session.md §3.2/§4.3）— 现状 ✅
- [ ] 聊天组快捷键 Enter/Shift+Enter/mod+↑/Esc（依据: search-command-palette.md §4.3）— 现状 ⬜ 待实机
- [ ] 四组合走查：聊天 × 四组合（手机端列表↔会话切换）— 现状 🟡

### 2.12 Agent 管理

页面：`/agents/:agentId`（详情），创建经成员页唯一入口（依据: agent.md §4）。

- [ ] 详情页五 Tab：概览 / 配置 / 技能与工具 / 可见性与权限 / 历史（依据: agent.md §4.3）— 现状 🟡（「技能与工具」Tab 为占位 EmptyState「arrive with the skills increment」）
- [ ] 配置 Tab：模型档位单选 + 具体模型下拉 + System Instructions 多行编辑器 + 温度/top_p/max_tokens/推理强度 + 预设套用；越界值红字拦截；保存生成新版本（依据: agent.md §4.3）— 现状 ✅
- [ ] 技能与工具 Tab：双列清单逐项开关；工具权限下拉（只读/可写/需确认），高风险默认「需确认」+警示色（依据: agent.md §4.3）— 现状 ❌（占位）
- [ ] 历史 Tab：配置版本时间线，「对比上一版」「回滚到此版本」（依据: agent.md §4.3）— 现状 ✅
- [ ] 四步创建向导：基本信息→模型与指令→技能与工具(可稍后)→可见性；每步独立校验可后退不丢数据；步骤指示器；「从现有复制」「从模板创建」（依据: agent.md §4.4）— 现状 ⬜ 待实机
- [ ] 全场景 AI 徽章不可关闭（列表/卡片/评论/@候选/分派选择器）（依据: agent.md §5.1）— 现状 ✅
- [ ] 容量三元组「运行中 N / 排队 M / 需审批 K」经 presence 推送，列表与卡片即时变化（依据: agent.md §4.9）— 现状 ⬜ 待实机
- [ ] 分派即开工可观测：卡片「●处理中」+ 时间线「已开始处理」+ claimed 显示 runtime + started 日志流 + 终态通知附 failure_reason/日志摘要/深链（依据: agent.md §4.7）— 现状 ⬜ 待实机（全链路 e2e）
- [ ] 生命周期操作：pause（cancel_current/finish_current）、resume、disable、archive、restore、所有权转移；软删除后历史评论「已停用 agent」占位（依据: agent.md §4.8）— 现状 ⬜ 待实机
- [ ] 人类干预：运行进度条「停止本次运行」；产物批准/打回；配置回滚（依据: agent.md §4.10）— 现状 ⬜ 待实机
- [ ] `agent.trigger_skipped`（paused/disabled 未触发）UI 提示（依据: agent.md §3.6）— 现状 ⬜ 待实机（呈现方式 Spec 未细化，**须补进 Spec**）
- [ ] 四组合走查：agent 详情各 Tab × 四组合 — 现状 ⬜

### 2.13 Runtime 与执行

页面：`/runtimes`、`/runtimes/:id`、`/executions/:id`（依据: runtime.md §4；runtime-executor.md §4）。

- [ ] 列表行：状态点（在线/平台托管/离线）+ 名称 + 类型 + 负载条 + 心跳相对时间 + 操作；离线置灰 + 离线时长；顶部队列深度背压信号 + 图例；筛选/搜索（依据: runtime.md §4.1）— 现状 ✅
- [ ] 详情页：状态/主机/OS/CPU/内存/并发/版本/标签/能力清单；心跳曲线（1h）；正在执行列表；历史任务；暂停/轮换 token（依据: runtime.md §4.2）— 现状 ✅
- [ ] 注册引导三步：①基本信息 ②安装命令块（逐条可复制可审，**无 curl|sh 盲管道**）③等待激活（WS 监听 ⏳→✅ 无需刷新）（依据: runtime.md §4.3）— 现状 ✅
- [ ] 执行详情页：状态 + 运行时长/超时进度条 + agent[AI]+issue+触发方式+分支；Tab[实时日志][产物/Diff][凭证(已脱敏)]；日志「跟随尾部」+ offset 续传 + 下载完整日志；取消二次确认（依据: runtime.md §4.4）— 现状 ✅
- [ ] 凭证 Tab 仅元信息值恒 `***`（依据: runtime.md §2.4）— 现状 ⬜ 待实机
- [ ] runtime 四态可行动：Online/Degraded（精确列出缺失能力+受影响任务类型+修复命令）/Paused/Isolated；**禁止泛化「运行失败」**（依据: runtime-executor.md §4.1）— 现状 ⬜ 待实机
- [ ] 执行按 attempt 展示：provider/version/model、冻结预算 vs 实际 usage、claim/running/approval/requeue/terminal 时间线、高风险动作「请求—审批人—grant—结果」（依据: runtime-executor.md §4.2）— 现状 ⬜ 待实机
- [ ] issue 详情反查其所有 task_executions（依据: runtime.md §4.5）— 现状 ⬜ 待实机
- [ ] 四组合走查：runtime 三页 × 四组合 — 现状 ⬜

### 2.14 技能

页面：技能库/详情/版本历史/导入向导/市场/agent 绑定区（依据: skill.md §4）。

- [ ] **路由接线**：`/skills` 路由注册，Sidebar 入口可达（非死链）（依据: skill.md §4.1；README §6.12）— 现状 ❌（整套 features/skills 已实现但无路由引用，Sidebar 入口落入 404）
- [ ] 技能库页：搜索 + 来源/状态筛选 +「+新建」「⇩ 导入」「浏览技能市场」；卡片网格（名称/摘要/来源标识/版本/安装状态/生命周期/操作）（依据: skill.md §4.1）— 现状 🟡（组件已写，接线后验）
- [ ] 信任徽标（builtin 盾形/user/marketplace/url ⚠）+「⚠ 含脚本」角标 +「↻ 有更新」（依据: skill.md §4.2）— 现状 🟡（同上）
- [ ] 导入向导三步：选择来源 → 预览校验（含脚本强制展开逐项确认、高危高亮、权限默认不勾选）→ 安装（依据: skill.md §4.2）— 现状 🟡（同上）
- [ ] 版本历史子页 + 回滚；历史永不删除（依据: skill.md §4.2）— 现状 🟡（同上）
- [ ] agent 绑定区：启用/版本/auto_trigger/优先级/解绑 + 含脚本约束提示（依据: skill.md §4.2）— 现状 🟡（同上）
- [ ] 更新流：updated_available 通知管理员 → 看 diff → 立即更新/稍后；脚本 hash 变化重新审批（依据: skill.md §4.3）— 现状 🟡（同上）
- [ ] 四组合走查：技能各页 × 四组合（skills.css 800px 断点生效验证）— 现状 ⬜

### 2.15 小队

页面：`/squads`、`/squads/:id`、`/squads/:id/tasks/:taskId`（依据: squad.md §4）。

- [ ] 列表卡片：头像/名称/形态徽标(常设/临时)/状态点/进行中任务计数/成员头像墙（leader 带 (L)、人/agent 异图标）（依据: squad.md §4.1）— 现状 ✅
- [ ] 详情页：左=成员区(+添加)/当前任务；右上=协作时间线（按任务/成员/action 过滤）；底=消息区（tabs 全部/指令/汇报/共享上下文，📌 置顶，@提及/关联任务/附件）（依据: squad.md §4.1）— 现状 ✅
- [ ] 消息着色：指令=蓝/汇报=绿/闲聊=灰/系统=虚线；指令/汇报带「关联任务」标签（依据: squad.md §4.2）— 现状 ⬜ 待实机
- [ ] 拆解树视图：缩进层级 + 状态图标/执行人/阶段/依赖(「等待 st-9003」)/结果摘要；看板视图按子任务状态分列可拖拽（依据: squad.md §4.2）— 现状 ✅（原生拖拽）
- [ ] 审核横幅：awaiting_plan_approval 顶部高亮 [批准][驳回] + 方案 markdown 侧栏（依据: squad.md §4.2）— 现状 ✅
- [ ] 创建表单：名称/描述/头像/形态/组长模式/require_plan_approval/最大拆解层级(1-4) + 成员逐个设角色，至少一名组长否则置灰（依据: squad.md §4.2）— 现状 ⬜ 待实机
- [ ] 编排流 SSE（task.status/subtask.created/…）seq 断点重放，进度 {total,done,in_progress,pending,failed}（依据: squad.md §4.5）— 现状 ⬜ 待实机（长任务 e2e）
- [ ] 叫停整个任务：级联取消 + 终止 agent 运行（依据: squad.md §3.1）— 现状 ⬜ 待实机
- [ ] 任务消息+时间线导出 markdown 归档（依据: squad.md §4.5）— 现状 ⬜ 待实机
- [ ] 运行中护栏：不可移除持有 in_progress 子任务者 422（依据: squad.md §3.1）— 现状 ⬜ 待实机
- [ ] 四组合走查：小队三页 × 四组合 — 现状 ⬜

### 2.16 自动化（Autopilots / Webhooks）

页面：`/autopilots`（+new/id/edit/runs）、`/webhooks`（依据: autopilot.md §4）。

- [ ] 列表页：筛选 + 全局 kill switch「● 已开启」+ 新建；列=名称/触发器(图标+文案)/状态/上次运行结果/近30天成功率/下次运行/操作（依据: autopilot.md §4.1）— 现状 ✅
- [ ] 编辑器四折叠区块：①触发器（cron 可视化 +「下次 5 次运行预览」+ 时区 + 错过补偿）②过滤 ③动作（agent 选择器 + prompt 模板变量插入）④护栏（频率/并发/重试/人工确认/每日预算，推荐默认预填）（依据: autopilot.md §4.2）— 现状 ✅
- [ ] 执行历史时间线（状态/时间/耗时/token/重试/错误）→ 运行详情（输入快照/产物/尝试明细）（依据: autopilot.md §4.2）— 现状 ✅
- [ ] 人工确认点 approve/reject（run 停 waiting_approval）；取消运行（依据: autopilot.md §3.6）— 现状 ✅
- [ ] 手动 test-run 支持 dry_run（只校验不执行，返回 would_run + matched_filters）（依据: autopilot.md §3.6）— 现状 ✅（`AutopilotDetailPage.tsx` testRun + dryRunLabel）
- [ ] kill switch 二次确认 + 填理由（依据: autopilot.md §4.2）— 现状 ⬜ 待实机
- [ ] Webhook 配置页：入站端点 + 签名密钥（创建仅显示一次）+ 最近事件（签名状态/处理状态）（依据: autopilot.md §4.3）— 现状 ✅
- [ ] run 状态 WS 实时推送列表与详情（依据: autopilot.md §5.3）— 现状 ⬜ 待实机
- [ ] 四组合走查：autopilot 五页 × 四组合 — 现状 ⬜

### 2.17 Onboarding

区域：AppShell 内嵌清单 + 六页面空状态（依据: onboarding.md §4）。

- [ ] 上手清单卡片：常驻可折叠 + 进度条（N/5）+ 步骤列表（勾选圈✓图标+文字「已完成」+ CTA 深链 +「✓ 已自动完成」角标）+ 首个未完成步骤高亮展开（依据: onboarding.md §4.2）— 现状 ✅
- [ ] 激活路径五步 CTA 深链既有向导（创建工作区→邀请/添加 agent→首个 issue→分派/@ 触发运行→收件箱见回评）（依据: onboarding.md §1.2.1）— 现状 ✅
- [ ] aha 庆祝态：末步达成切庆祝卡片（插画+文案+深链收件箱），尊重 reduced-motion，可收起（依据: onboarding.md §4.2）— 现状 ✅
- [ ] dismiss/恢复：「不再显示」收起；帮助菜单「重新显示上手清单」恢复（依据: onboarding.md §4.3）— 现状 ✅
- [ ] 六核心页面空状态四要素（插画+文案+主操作+深链），插画随主题适配亮/暗（依据: onboarding.md §1.2.2）— 现状 ✅（六大空态接入）
- [ ] 键盘入口可发现性：首次进入一次性内联提示（⌘K + ?），顶栏搜索占位「搜索或输入命令…(⌘K)」；已关闭不再现（依据: onboarding.md §4.2）— 现状 ⬜ 待实机（依赖顶栏搜索真实化）
- [ ] 步骤完成实时增量刷新（onboarding.progress 私有频道）；WS 不可用 30s 轮询（依据: onboarding.md §4.5）— 现状 ⬜ 待实机
- [ ] 四组合走查：清单卡片/庆祝态/六空态 × 四组合 — 现状 ⬜

### 2.18 集成

页面：`/integrations`、`/integrations/:id`、`/webhook-subscriptions`（依据: integrations.md §4）。

- [ ] 连接器目录卡片网格（飞书/Lark·Slack·钉钉·GitHub·GitLab·出向 Webhook），每卡 [连接]/[已连接 N]；已连接列表（状态/绑定数/近7天事件量/操作）（依据: integrations.md §4.1）— 现状 ✅
- [ ] OAuth 授权流新窗跳转回跳成功态；界面永不展示 secret 明文；粘贴 token 掩码（依据: integrations.md §4.2）— 现状 ⬜ 待实机
- [ ] 绑定配置抽屉：外部身份选择器 + 作用域 + 匹配规则表单 + 目标 agent（留空=仅审计显式提示）（依据: integrations.md §4.2）— 现状 ✅
- [ ] 事件台账：签名状态/处理状态徽章 + 载荷预览（标注「不可信数据」）；rejected/deduped 高亮原因（依据: integrations.md §4.2）— 现状 ✅
- [ ] 出向订阅页：订阅列表 + 投递历史时间线 + 手动重试 + 熔断横幅 + 密钥仅显示一次（依据: integrations.md §4.3）— 现状 ✅
- [ ] issue 侧栏 VCS 关联区块：PR/commit 列表 + 外部状态徽章 + 来源标注 + 手动关联（依据: integrations.md §4.2）— 现状 ⬜ 待实机
- [ ] 钉钉连接状态卡：状态点 + 心跳 + down 错误横幅 [重新连接][编辑配置]；[测试发送]/[诊断接收] 分离（依据: integrations.md §4.2）— 现状 ⬜ 待实机
- [ ] 消息队列面板：按会话分组 + 在途/排队徽章 + 位置 + 取消 + 空态（依据: integrations.md §4.2）— 现状 ⬜ 待实机
- [ ] IM 审批卡片生命周期（飞书/Slack/钉钉）：批准/拒绝 → 终态文本禁用按钮；无权限/过期/回调失败兜底态（依据: integrations.md §4.4）— 现状 ⬜ 待实机（IM 侧真实验证）
- [ ] 异常态：disabled 横幅、OAuth 失败 [重新授权]、reconnecting「重连中(退避 Ns)」非错误态（依据: integrations.md §4.5）— 现状 ⬜ 待实机
- [ ] 四组合走查：集成三页 × 四组合 — 现状 ⬜

### 2.19 导入导出

页面：`/w/:slug/settings/data` + 项目/视图情境入口（依据: import-export.md §4）。

- [ ] 数据管理页：作业列表（历史/状态/计数/重新下载）+ 导入/导出主入口（依据: import-export.md §4.1）— 现状 ✅
- [ ] 导入向导分步可回退：上传 → 映射（源字段→Mesh 字段 + 值转换预览）→ dry-run 错误报告（行号/字段/原因 + 错误 CSV 下载 + 可回上一步改映射）→ 确认 → 进度（实时「成功 N/失败 M/共 T」）→ 结果（部分成功 + 错误报告 + 深链）（依据: import-export.md §4.2）— 现状 ✅（4 步 + 错误报告下载）
- [ ] 导出异步 UI：范围选择 + 预览匹配行数 + 格式/列/locale；「进行中,完成后通知」可关闭；data_job.updated 进度；completed 下载按钮（签名过期重签）（依据: import-export.md §4.3）— 现状 ✅
- [ ] 幂等 UI：重复点「确认导入」不重复建作业；running 期间按钮禁用显进度（依据: import-export.md §4.4）— 现状 ⬜ 待实机
- [ ] 大文件行级进度流式 UI；超大导出前置预警 `export_too_large`（依据: import-export.md §4.4）— 现状 ⬜ 待实机
- [ ] 状态信号「● 导入中 980/1000」文字+图标叠加（依据: import-export.md §4 引言）— 现状 ⬜ 待实机
- [ ] 项目页/视图页「⋯」情境入口「导出本项目/本视图」「导入到本项目」（依据: import-export.md §4.1）— 现状 ⬜ 待实机
- [ ] 四组合走查：导入导出流 × 四组合 — 现状 ⬜

### 2.20 Analytics / 洞察

页面：`/insights` + 项目详情仪表盘 tab + agent 详情统计卡（依据: analytics.md §4）。

- [ ] 工作区仪表盘：吞吐量趋势（created vs completed + granularity 切换 + 积压趋势）+ workload 排行（member_type 图标、agent 行三元组）+ agent 统计网格卡（成功率语义色/平均时长/重试率/近30天 sparkline）（依据: analytics.md §4.3）— 现状 ✅（自绘 SVG 图表）
- [ ] 项目仪表盘：时间范围选择器（预设+自定义）+ velocity 卡（当前周期高亮 hover 明细）+ burndown 卡（理想虚线 vs 实际实线 + count/points 切换）+ cycle time 卡（P50/P90 + 分布 + sample_size/insufficient_data 标注）（依据: analytics.md §4.2）— 现状 ✅
- [ ] 可见性轻提示：「按你的项目可见范围统计」；admin/owner 见全量（依据: analytics.md §4.3）— 现状 ⬜ 待实机（非 admin 账号验证）
- [ ] agent 详情统计卡：KPI + sparkline + 成功/失败/超时堆叠 + token 区（coverage <100% 标注）+ 运行历史深链（依据: analytics.md §4.4）— 现状 ⬜ 待实机
- [ ] 图表语义色亮/暗各校准；burndown 线型虚实区分（颜色非唯一信号）；尊重 reduced-motion（依据: analytics.md §4.5）— 现状 ⬜ 待实机（暗色下图表逐张核对）
- [ ] 空/异常态：无数据空态 +「调整范围/新建 issue」；query_cost_exceeded「收窄后重试」；无可见性「无权限」页（依据: analytics.md §4.6）— 现状 ⬜ 待实机
- [ ] 本地日历分桶：时区切换后日期标签与桶边界同步不错位；`meta.display_timezone` 回显；「当前归属」口径提示（依据: analytics.md §2.2.3/§2.4）— 现状 ⬜ 待实机
- [ ] 四组合走查：所有图表 × 四组合（暗色图表可读性重点）— 现状 ⬜

### 2.21 账号设置（个人 / 安全 / Tokens / 审计）

页面：`/settings`（依据: auth.md §4；i18n.md §4；theme.md §4）。

- [ ] 个人偏好：外观主题三态、语言（「立即生效」）、时区（「按浏览器时区检测」）+ 实时样例（依据: theme.md §4.1；i18n.md §4.1）— 现状 ✅
- [ ] 安全：修改密码（旧+新+强度条）、2FA、活跃会话列表（设备/UA/IP/最近活跃/「当前」/撤销/「登出所有其他会话」）、第三方账号绑定/解绑（依据: auth.md §4.2）— 现状 ✅（SecuritySettings）
- [ ] API Tokens：列表 prefix+掩码 + scopes 标签 + 过期 + 最近使用 + 撤销；新建后一次性明文框（复制 +「关闭后无法再次查看」）（依据: auth.md §4.3）— 现状 ✅（工作区设置内）
- [ ] 审计页（admin+）：时间/行为者/动作/资源/IP + 筛选，只读不可删（依据: auth.md §4.4）— 现状 ✅
- [ ] 通知偏好 section（见 §1.10）— 现状 ✅
- [ ] 会话/token 撤销实时生效（WS 连接失效）（依据: auth.md §4.5）— 现状 ⬜ 待实机
- [ ] 四组合走查：设置各 section × 四组合 — 现状 ⬜

### 2.22 附件与灯箱

区域：composer 附件、issue 附件区、评论内联、灯箱（依据: attachment.md §4）。

- [ ] 上传入口三通道：composer 回形针 + 文件选择器、拖拽到 composer、粘贴截图 Ctrl+V（依据: attachment.md §4.1）— 现状 ✅（`useAttachmentUploader.ts`）
- [ ] 上传占位卡片：缩略图/类型图标 + 文件名 + 进度条 + 取消；多文件独立进度；失败「重试」；分阶段 validate→upload→scan→complete（依据: attachment.md §4.2）— 现状 ✅
- [ ] 扫描门禁：complete 后「扫描中,完成后开放下载」占位，不暴露下载/预览；`attachment.processed` 到达后切换可预览/已拒绝（依据: attachment.md §4.5/§4.6）— 现状 ✅（扫描拦截态）
- [ ] 图片灯箱：原图加载、缩放/旋转/重置/下载/在附件区定位；多图网格切换（依据: attachment.md §4.3）— 现状 ✅（`Lightbox.tsx`）
- [ ] 非图片文件卡片：类型图标 + 名 + 大小 + 上传者 + 下载（依据: attachment.md §4.3）— 现状 ✅（`FileIcon.tsx`）
- [ ] issue 附件区：缩略图网格/文件列表；hover 出「下载/删除/复制下载链接」（依据: attachment.md §4.3）— 现状 ✅（`AttachmentPanel.tsx`）
- [ ] agent 产出物附件：带 agent 头像与「来自 <agent> 运行」标记；截图类默认内联、报告/日志类文件卡片（依据: attachment.md §4.4）— 现状 ⬜ 待实机（真实运行产出验证）
- [ ] 下载：短时效（60s）签名 URL 过期自动重签；无权限 403「你没有权限下载此文件」；未知/可执行类型强制 attachment 下载（依据: attachment.md §4.5）— 现状 ⬜ 待实机
- [ ] 配额/尺寸错误具名提示：QUOTA_EXCEEDED 423 / FILE_TOO_LARGE 413 / UNSUPPORTED_MEDIA_TYPE 415 / HASH_MISMATCH 422（依据: attachment.md §3.4）— 现状 ⬜ 待实机（构造越限上传验证）
- [ ] 感染文件永久拒绝并通知上传者+管理员（critical）；SVG 净化渲染（依据: attachment.md §4.6）— 现状 ⬜ 待实机（红队样本）
- [ ] 聊天附件与小队消息附件复用统一组件（扫描完成才可见）（依据: chat-session.md §4.2；squad.md §2.3）— 现状 ✅（聊天内复用）
- [ ] 四组合走查：上传流/灯箱/附件区 × 四组合（手机端灯箱手势缩放）— 现状 ⬜

---

## 3. 四组合走查矩阵（桌面/手机 × 亮色/暗色）

> 阶段三对下表每个单元格**真实打开页面操作并截图存证**（桌面 1440×900、手机 390×844 两档基准；亮/暗各切主题后走查）。单元格填 ✅+存证链接 或 ❌+问题编号。任一 ❌ = 该页不通过。

| # | 页面 / 视图 | 桌面+亮 | 桌面+暗 | 手机+亮 | 手机+暗 |
| --- | --- | --- | --- | --- | --- |
| 1 | 登录 / 注册 / 忘记密码 | ✅ | ✅ | ✅ | ✅ |
| 2 | OAuth 回调 / 设备码确认 / 邀请接受 | ✅ | ⬜ | ⬜ | ⬜ |
| 3 | 首页（真实产品首页） | ✅ | ✅ | ✅ | ✅ |
| 4 | AppShell（TopBar/Sidebar/导航） | ⬜ | ⬜ | ⬜ | ⬜ |
| 5 | 命令面板 / 快捷键帮助层 | ⬜ | ⬜ | ⬜ | ⬜ |
| 6 | 收件箱 / 铃铛下拉 / 通知偏好 | ⬜ | ⬜ | ⬜ | ⬜ |
| 7 | 项目列表 / 项目详情各 Tab | ⬜ | ⬜ | ⬜ | ⬜ |
| 8 | 周期页 | ⬜ | ⬜ | ⬜ | ⬜ |
| 9 | Issue 列表（筛选/批量） | ⬜ | ⬜ | ⬜ | ⬜ |
| 10 | Issue 详情（属性/评论/附件/活动） | ⬜ | ⬜ | ⬜ | ⬜ |
| 11 | 看板（拖拽/WIP/过滤/视图切换） | ⬜ | ⬜ | ⬜ | ⬜ |
| 12 | 成员名册 / 详情抽屉 | ⬜ | ⬜ | ⬜ | ⬜ |
| 13 | Agent 详情五 Tab / 创建向导 | ⬜ | ⬜ | ⬜ | ⬜ |
| 14 | 技能库 / 详情 / 导入向导 / 市场 | ⬜ | ⬜ | ⬜ | ⬜ |
| 15 | 聊天（流式/候选/沉淀） | ⬜ | ⬜ | ⬜ | ⬜ |
| 16 | 小队列表 / 详情 / 任务详情 | ⬜ | ⬜ | ⬜ | ⬜ |
| 17 | Runtime 列表 / 详情 / 注册引导 | ⬜ | ⬜ | ⬜ | ⬜ |
| 18 | 执行详情（日志/产物/凭证） | ⬜ | ⬜ | ⬜ | ⬜ |
| 19 | Autopilot 列表 / 编辑器 / 运行详情 | ⬜ | ⬜ | ⬜ | ⬜ |
| 20 | Webhook / 出向订阅 / 集成目录 / 台账 | ⬜ | ⬜ | ⬜ | ⬜ |
| 21 | 洞察仪表盘 / 项目仪表盘 / 图表 | ⬜ | ⬜ | ⬜ | ⬜ |
| 22 | 导入导出向导 / 作业列表 | ⬜ | ⬜ | ⬜ | ⬜ |
| 23 | 工作区设置（基本信息/邀请/标签/字段/数据/Tokens/审计/Danger） | ⬜ | ⬜ | ⬜ | ⬜ |
| 24 | 个人设置（外观/语言/时区/安全/通知） | ⬜ | ⬜ | ⬜ | ⬜ |
| 25 | 统一审批页 /approvals | ⬜ | ⬜ | ⬜ | ⬜ |
| 26 | Onboarding 清单 / aha 庆祝 / 六空态 | ⬜ | ⬜ | ⬜ | ⬜ |
| 27 | 附件灯箱（缩放/旋转/下载） | ⬜ | ⬜ | ⬜ | ⬜ |
| 28 | 404 / 错误页 / 无权限页 / 离线横幅 | ⬜ | ⬜ | ⬜ | ⬜ |

**走查关注点（每个单元格通用）**：
1. 亮/暗：无硬编码色值漏网（白底黑字块、刺眼边框）、图表双色板可读、焦点环可见、原生控件随 color-scheme；
2. 手机：导航可达（抽屉/汉堡）、触控目标 ≥44px、表格降级形态、弹窗不溢出、软键盘不遮输入区、拖拽有替代操作；
3. 文案随 locale 完整（zh-CN/en 各走一遍核心页）。

---

## 4. 已知缺口汇总（必修项）

> 由上文 ❌ 条目自动汇总；阶段二设计 Spec 须覆盖以下各项，阶段三逐项核销。

| # | 缺口 | 位置 | 影响 | 补充建议 |
| --- | --- | --- | --- | --- |
| G1 | 首页为脚手架演示舞台（demo 区块 + mock 端点），非真实产品首页 | `shell/pages/HomePage.tsx` | 产品第一印象；MES-107 核心 | 工作台视角首页：我的 issue/收件箱摘要/进行中运行/最近项目 |
| G2 | 移动端导航不可用：≤768px 侧栏 `display:none` 无抽屉替代 | `shell/shell.css:402` | 手机 × 两套组合整体不通过 | 汉堡菜单 + 抽屉侧栏；断点系统（≥1024/768）；触控目标 ≥44px；**移动端布局细则须补进各模块 Spec** |
| G3 | 全局搜索为死输入框（无 onChange/提交） | `shell/TopBar.tsx:42-48` | 竞品核心入口；onboarding 键盘提示依赖它 | 接入与命令面板同一结果视图 |
| G4 | 命令面板不检索业务对象（六类对象搜索缺失）、无 favorites/recents 空态、无 identifier 直达、无 no-results 建 issue | `shortcuts/CommandPalette.tsx` | 竞品最高频能力差距 | 按 search-command-palette.md §1.2/§4.2 全量落地 |
| G5 | `/skills` 路由未注册：Sidebar 死链 + features/skills 整套孤儿代码 | `App.tsx` / `Sidebar.tsx:39` | 功能不可达；导航破窗 | 注册路由并接线 agent 详情「技能与工具」Tab |
| G6 | Agent 详情「技能与工具」Tab 为占位 EmptyState | `AgentDetailPage.tsx:627` | 工具权限（只读/可写/需确认）无管理面 | 接线 skills 绑定区 + 工具权限下拉 |
| G7 | 看板 List 布局占位、快速建卡禁用、拖拽零视觉反馈、无虚拟滚动（性能线 1000 卡 ≥50fps 不达） | `features/board/` | 竞品看板核心体验 | List 视图落地；dragover 高亮/落点条/ghost；虚拟滚动 |
| G8 | 设计层基础组件无 hover/active 状态；全库 :active 仅 1 条 | `design/components.css` | 微交互一致性差距 | hover/active/pressed 状态令牌化补入组件层 |
| G9 | 上下文快捷键组（看板/issue 详情）Spec 已定义但页面无注册 | `shell/shortcutsRegistration.ts` | 键盘效率承诺未兑现 | 按 search-command-palette.md §4.3 注册 + 等价鼠标路径核对 |
| G10 | 统一审批页 `/approvals` 缺失（九条规范深链之一） | `App.tsx` | 审批入口分散；深链断 | 聚合三类审批的独立页 |
| G11 | 工作区默认主题设置入口缺失（Spec 自认未落地） | theme.md §4.1 | 协商链中段无管理面 | 工作区设置外观 section |
| G12 | 脚手架/增量期文案残留：`login.phaseNote`、`members.add.agentComingSoon`、dev token 直填入口、`PlaceholderPage.tsx` | `catalogs/*.json`、`shell/` | 完成品观感 | 清除并加 CI 守卫（coming soon/placeholder 关键词扫描） |
| G13 | type scale 不完整（仅三档字号，无中英字体配对/数字表格化专项） | `design/tokenValues.ts` | 排版品质差距 | 显示+正文字体配对、完整字号/字重/行高阶梯、表格数字等宽 |
| G14 | 中间层组件缺失：Dropdown/Menu、Avatar、Tabs、Tooltip、Accordion 各 feature 自造 | `design/` | 视觉漂移、重复造轮子 | 设计层统一供给并迁移各 feature |
| G15 | Feature flags 前端消费机制缺失 | 全库 | 工作区功能开关无 UI 呈现 | flag 下发 + 条件渲染约定 |
| G16 | 移动端 Spec 细则缺失：README 仅「只读优先」一句方针 | README §6.12 | 移动验收无据可依 | **须补进 Spec**：各核心页移动端布局/降级/手势细则 |
| G17 | `agent.trigger_skipped` 呈现方式 Spec 未细化 | agent.md §3.6 | 验收无组件级依据 | **须补进 Spec**：toast 或内联提示选型 |
| G18 | 个人资料编辑缺失：无头像/昵称/bio 编辑界面（`PATCH /users/me` 仅偏好键） | `api/userPreferences.ts` | 竞品成员模型基本项 | 个人设置增 Profile section（头像上传 + 显示名 + bio） |
| G19 | 浏览器标签页标题全站静态（无 document.title 逐页设置） | 全库 | 多标签工作流辨识度；完成品基本项 | 路由级标题（实体页带 identifier/名称），未读可反映标题/favicon |

---

## 5. 可选项目记录（本期非必做，改动不得使其退化）

| # | 项目 | 出处 | 说明 |
| --- | --- | --- | --- |
| O1 | Timeline/Table（甘特）视图 | kanban.md §1.3 | Spec 明确 YAGNI 延期，501 兜底 |
| O2 | 批量操作短时撤销 | issue.md §1.2.5 | 「可选」 |
| O3 | 浏览器桌面通知（Notification 权限流） | comment-inbox.md §4.3 | 「可选桌面 toast」 |
| O4 | view.presence 协作者头像/光标 | kanban.md §1.4 | 「可选」 |
| O5 | 评论 Snooze 稍后处理 / 评论置顶精选 | comment-inbox.md §3.2/F12 | 「可选增强」 |
| O6 | 自定义快捷键编辑器 | search-command-palette.md §1.3 | 「起步不做」，表预留 |
| O7 | 全文检索（描述/评论正文/附件内容） | search-command-palette.md | 标题级起步，全文非目标 |
| O8 | 粘贴完整 issue URL 跨工作区引用卡片 | comment-inbox.md 注记 9 | 延期项 |
| O9 | 域名自动加入 / 自定义角色表 | workspace.md §1.4；auth.md §2.7 | 可选 |
| O10 | 离线编辑/队列化提交完整离线模式 | research 缺口 14 | 当前仅 WS 降级轮询 |
| O11 | 计费/订阅管理页 | workspace.md §1.2 | 开源自托管定位，可选 |
| O12 | issue time_tracking 时间追踪 | research issue.md | 「可选」 |

---

## 6. 基线维护与阶段三验收流程

1. **基线冻结**：本清单随 MES-110 PR 合入 main 即冻结为阶段三依据；此后新发现的竞品功能点（复查/用户反馈）以增量 PR 补充条目，不改既有编号（G/O 编号稳定可引用）。
2. **阶段三验收方法**（另起 Issue）：
   - 逐条 `- [ ]` 以**真实 e2e + 真实 UI 操作**核销：真实 API 调用、真实校验响应与落库、真实点击页面与按钮；
   - §3 矩阵 112 个单元格逐个截图存证（桌面 1440×900 / 手机 390×844，亮/暗各切）；
   - 现状列由勘察快照改写为最终判定 ✅/❌ + 存证链接；
   - §4 缺口 G1–G17 全部核销方可放行；
   - 复用本仓库既有门禁：对比度关卡、暗色视觉回归基线、per-file 覆盖率 ≥90%、全量 e2e 套件。
3. **放行标准**：清单勾选率 100%（⚪ 除外）+ 四组合矩阵无 ❌ + UT 覆盖率 ≥90% + 代码合入 main 且远端核实 + 提交人 `cnwenf@outlook.com` 无 co-author + 全项目无参考来源暴露（竞品具名/链接/照搬命名文案）。
