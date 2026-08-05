# 前端界面设计实施基线

> 状态：v1.2。本文冻结当前可执行的界面规格，覆盖设计令牌、排版、布局、组件、页面结构、响应式、主题和发布边界。业务行为仍以对应功能 Spec 为准；本文不得改变接口、权限、路由或实时协议。v1.2 的数值来自授权运行态的黑盒观测，并经过 WCAG AA 校准；未读取或复制任何外部源码、样式表或品牌资产。

## 1. 目标与单一事实源

界面应当紧凑、安静、信息优先：层级由间距、排版和语义表面共同表达，不依靠装饰性渐变、重阴影或颜色堆叠。每个状态都必须同时具备文字或图形信号，用户无需记忆颜色含义。

实现的单一事实源如下：

- 令牌值：`frontend/src/design/tokenValues.ts`。
- 生成样式：`frontend/src/design/tokens.css`、`tokens-dark.css`、`tokens-print.css`；禁止手改。
- 基础组件：`frontend/src/design/components/`。
- 页面模式：`frontend/src/design/patterns/`。
- 外壳导航：`frontend/src/shell/navigation.ts`。
- 路由与页面族：`frontend/src/App.tsx`。

新增页面必须先选择本文定义的页面模式，再组合共享组件。只有共享组件无法表达且差异可长期复用时，才允许新增 feature 内部组件。业务样式不得建立平行 token、私有断点或另一套状态色。

## 2. 画布、外壳与内容宽度

### 2.1 视口模式

| 模式    | CSS 像素范围 | 外壳                           | 内容行为                             |
| ------- | ------------ | ------------------------------ | ------------------------------------ |
| compact | `0–599`      | 手机顶栏、底部主导航、更多抽屉 | 单列；详情与列表路由化；表格转卡片   |
| medium  | `600–1023`   | 折叠导航或抽屉                 | 单列为主，足够宽时局部双栏           |
| wide    | `1024–1439`  | 展开侧栏                       | 标准内容宽度，可用主辅双栏           |
| xwide   | `≥1440`      | 展开侧栏                       | 宽数据视图或多栏，正文仍限制可读宽度 |

断点只来自 `VIEWPORT_BREAKPOINTS`。业务组件优先使用容器查询；不得用 598、768、800 等近似阈值自建媒体规则。compact 下触控目标最小 `44×44px`，并计算底部安全区。

### 2.2 外壳尺寸

| 语义变量                    | 值       | 用途                                   |
| --------------------------- | -------- | -------------------------------------- |
| `--shell-sidebar-expanded`  | `256px`  | wide/xwide 展开侧栏                    |
| `--shell-sidebar-collapsed` | `64px`   | medium 或用户折叠后的图标 rail         |
| `--shell-topbar-offset`     | `0px`    | 桌面不设横贯页面的顶栏                 |
| `--shell-frame-gap`         | `8px`    | 内容框与应用画布的呼吸缝               |
| `--shell-page-radius`       | `14px`   | 桌面唯一主内容框圆角                   |
| `--page-gutter-compact`     | `16px`   | compact 页边距                         |
| `--page-gutter-medium`      | `24px`   | medium 页边距                          |
| `--page-gutter-wide`        | `32px`   | wide/xwide 内容上限；密集页优先 `16px` |
| `--content-public-flow`     | `384px`  | 登录、注册与恢复流程的固定内容框       |
| `--content-settings`        | `704px`  | 账号设置的主内容列                     |
| `--content-readable`        | `720px`  | 长文本与讨论流                         |
| `--content-form`            | `640px`  | 设置与表单                             |
| `--content-standard`        | `1120px` | 常规列表和详情                         |
| `--content-wide`            | `1440px` | 看板、分析和宽表                       |

桌面外壳固定为双区：左侧 `256px` 中性导航 rail（身份、工作区、搜索、导航同列）→ 右侧带 `8px` 外缝、`14px` 圆角和轻边界的唯一 `main` 内容框。连接横幅只在异常态占据右栏顶部，不得恢复横贯全视口的常驻顶栏。compact 保留手机顶栏、底部主导航与更多抽屉，确保现有导航和无障碍契约完整。DOM 顺序仍为：跳到主内容链接 → rail/手机顶栏 → 状态横幅 → 唯一 `main` → 页面浮层 → toast 区。

## 3. 颜色与主题映射

### 3.1 表面、文字、边界与强调色

| Token                      | 亮色                              | 暗色                        | 语义             |
| -------------------------- | --------------------------------- | --------------------------- | ---------------- |
| `--color-canvas`           | `oklch(96.4435% .001327 286.375)` | `oklch(15.5% .005 285.823)` | 应用 rail 画布   |
| `--color-bg`               | `oklch(98.8087% 0 0)`             | `oklch(18% .005 285.823)`   | 主内容框背景     |
| `--color-surface`          | `oklch(100% 0 0)`                 | `oklch(21% .006 285.885)`   | 控件和卡片表面   |
| `--color-surface-subtle`   | `oklch(96.7% .001 286.375)`       | `oklch(27.4% .006 286.033)` | 分组、次级区域   |
| `--color-surface-raised`   | `oklch(100% 0 0)`                 | `oklch(23.5% .007 285.885)` | 浮层             |
| `--color-surface-hover`    | `oklch(96.7% .001 286.375)`       | `oklch(27.4% .006 286.033)` | hover            |
| `--color-surface-pressed`  | `oklch(93.5% .003 286.375)`       | `oklch(30% .006 286.033)`   | pressed          |
| `--color-surface-selected` | `oklch(95% .002 286.375)`         | `oklch(30% .006 286.033)`   | selected/current |
| `--color-text-strong`      | `oklch(14.1% .005 285.823)`       | `oklch(98.5% 0 0)`          | 标题、主数据     |
| `--color-text`             | `oklch(21% .006 285.885)`         | `oklch(92% .004 286.32)`    | 正文             |
| `--color-text-muted`       | `oklch(54% .016 285.938)`         | `oklch(70.5% .015 286.067)` | AA 校准辅助信息  |
| `--color-text-disabled`    | `oklch(70.5% .015 286.067)`       | `oklch(55.2% .016 285.938)` | 不可用信息       |
| `--color-border`           | `oklch(94.5% .003 286.32)`        | `oklch(100% 0 0 / .06)`     | 控件、卡片边界   |
| `--color-border-strong`    | `oklch(92% .004 286.32)`          | `oklch(100% 0 0 / .15)`     | 强分隔           |
| `--color-accent`           | `oklch(21% .006 285.885)`         | `oklch(92% .004 286.32)`    | 主操作、链接     |
| `--color-accent-hover`     | `oklch(27.4% .006 286.033)`       | `oklch(98.5% 0 0)`          | 主操作 hover     |
| `--color-accent-soft`      | `oklch(93.5% .003 286.375)`       | `oklch(27.4% .006 286.033)` | 低权重选中背景   |
| `--color-focus-ring`       | `oklch(55% .16 255)`              | `oklch(65% .16 255)`        | 键盘焦点         |

浅色辅助文本的黑盒观测亮度为 `55.2%`，直接放在 rail 画布上只有约 `4.35:1`；实现将其校准为 `54%`，以保持同一中性观感并通过正文 `4.5:1` 门禁。

状态颜色使用 `success`、`warning`、`danger`、`info`、`neutral` 的 `fg/bg/border` 三元组。颜色只增强含义；状态点、徽标、图表和优先级必须同时有图标、线型、形状或可见文案。

| 状态    | 亮色 `fg / bg / border`       | 暗色 `fg / bg / border`       |
| ------- | ----------------------------- | ----------------------------- |
| success | `#15803d / #dcfce7 / #86efac` | `#4ade80 / #052e16 / #14532d` |
| warning | `#92400e / #fef3c7 / #fcd34d` | `#fbbf24 / #451a03 / #78350f` |
| danger  | `#b91c1c / #fee2e2 / #fca5a5` | `#f87171 / #450a0a / #7f1d1d` |
| info    | `#075985 / #e0f2fe / #7dd3fc` | `#38bdf8 / #082f49 / #0c4a6e` |
| neutral | `#475467 / #f2f4f7 / #d0d5dd` | `#98a2b3 / #252b36 / #343c49` |

### 3.2 主题切换

亮暗模式共用同一 DOM 和组件树，只替换语义颜色与阴影。`data-theme='dark'` 是暗色选择器；未完成协商时使用中性 skeleton，不先猜主题。打印由 `tokens-print.css` 强制使用可打印的亮色值。

主题新增值必须满足：

1. 先加入 `tokenValues.ts`，再运行令牌生成器。
2. 亮暗键集合一一对应；非颜色尺寸不在暗色表重复声明。
3. 正文对背景对比度不低于 `4.5:1`，大文字、焦点与图形边界不低于 `3:1`。
4. 暗色浮层同时使用边界和阴影，不能仅用黑色阴影区分层级。
5. 选区、代码、骨架、遮罩与用户内容颜色也必须走专用语义 token。

## 4. 间距、圆角、阴影与层级

### 4.1 间距刻度

| Token         | 值     | 推荐使用                         |
| ------------- | ------ | -------------------------------- |
| `--space-0`   | `0px`  | 重置                             |
| `--space-0-5` | `2px`  | 视觉微调                         |
| `--space-1`   | `4px`  | 紧密图标或状态点                 |
| `--space-1-5` | `6px`  | chip 内部                        |
| `--space-2`   | `8px`  | 控件内部、紧凑行内组             |
| `--space-3`   | `12px` | 标准行内组                       |
| `--space-4`   | `16px` | 卡片内边距、compact 页边距       |
| `--space-5`   | `24px` | 标准分区                         |
| `--space-6`   | `32px` | 大分区                           |
| `--space-8`   | `32px` | 兼容别名，新增代码优先 `space-6` |
| `--space-10`  | `40px` | 页面段落                         |
| `--space-12`  | `48px` | 页面顶部                         |
| `--space-16`  | `64px` | 低频展示间距                     |

同一容器只选一个节奏：控件内部 `4/6/8px`，卡片内部 `12/16px`，页面分区 `24/32px`。禁止为了对齐单个截图引入 13、17、23px 等孤立值。

### 4.2 圆角、边界、阴影和 z-index

- 圆角：`xs 4px`、`sm 6px`、`md 8px`、`lg 10px`、`xl 14px`、`full 999px`；页面内容框固定消费 `--shell-page-radius`。
- 边界：默认 `1px`；选中或焦点指示可用 `2px`，不得叠加多层重边框。
- `shadow-1` 用于轻浮起卡片和菜单；`shadow-2` 用于 popover 和 sticky 工具条；`shadow-3` 用于 dialog 和 drawer；`shadow-raised` 仅用于需要稳定抬升的对象。
- 层级：`base 0`、`sticky 100`、`dropdown 200`、`overlay 300`、`toast 400`。业务样式禁止自造相邻整数。

| 阴影            | 亮色                                                           | 暗色                                                   |
| --------------- | -------------------------------------------------------------- | ------------------------------------------------------ |
| `shadow-1`      | `0 1px 2px rgba(15,23,42,.06), 0 1px 3px rgba(15,23,42,.1)`    | `0 1px 2px rgba(0,0,0,.4), 0 1px 3px rgba(0,0,0,.5)`   |
| `shadow-2`      | `0 2px 4px rgba(15,23,42,.06), 0 4px 8px rgba(15,23,42,.08)`   | `0 2px 4px rgba(0,0,0,.4), 0 4px 8px rgba(0,0,0,.45)`  |
| `shadow-3`      | `0 4px 8px rgba(15,23,42,.08), 0 12px 32px rgba(15,23,42,.16)` | `0 4px 8px rgba(0,0,0,.5), 0 12px 32px rgba(0,0,0,.6)` |
| `shadow-raised` | `0 4px 16px rgba(15,23,42,.16)`                                | `0 4px 16px rgba(0,0,0,.55)`                           |

## 5. 排版

### 5.1 字体角色

- 展示标题与 UI 均以自托管 Inter 优先，缺失时立即回退系统栈；工作台标题字重 `600`。
- UI、正文、表单使用 `--font-family`，常规字重 `400`，强调 `500/600`。
- 标识、日志、代码和等宽数字使用 `--font-family-mono`。
- 字体文件自托管；字体加载失败必须立即回退系统字体，不能阻塞交互。

### 5.2 字阶

| 样式       | 字号/行高 | 字重      | 用途                            |
| ---------- | --------- | --------- | ------------------------------- |
| display-lg | `36/44px` | `650`     | 低频公开页展示标题              |
| display-sm | `30/38px` | `650`     | 工作台欢迎区                    |
| public     | `24/32px` | `500`     | 登录、注册与恢复流程标题        |
| title-1    | `16/24px` | `600`     | 页面唯一 `h1`                   |
| title-2    | `16/24px` | `600`     | 对象详情标题                    |
| title-3    | `14/20px` | `600`     | 分区、浮层标题                  |
| body-lg    | `14/20px` | `400`     | 长说明                          |
| body       | `14/20px` | `400`     | 默认正文、列表行                |
| body-sm    | `13/16px` | `400/500` | 密集元数据                      |
| caption    | `12/16px` | `400/500` | 辅助标签                        |
| micro      | `11/16px` | `500/600` | 极短状态，不承载正文            |
| control    | `16px`    | 按控件    | 表单输入，避免 compact 聚焦缩放 |

标题最多两行并在容器内换行；标识符和数字使用 `font-variant-numeric: tabular-nums`。中文与拉丁文字之间由排版自然留白，不用连续空格硬对齐。时间、数量和百分比必须由本地化格式器输出。

## 6. 动效与反馈

| Token                 | 时长    | 场景                     |
| --------------------- | ------- | ------------------------ |
| `--motion-instant`    | `0ms`   | 数据与主题直接切换       |
| `--motion-fast`       | `100ms` | hover、pressed           |
| `--motion-standard`   | `160ms` | tooltip、menu            |
| `--motion-deliberate` | `240ms` | drawer、dialog、布局移动 |
| `--motion-slow`       | `360ms` | 低频完成反馈             |

进入使用 `cubic-bezier(0.2, 0.8, 0.2, 1)`，退出使用 `cubic-bezier(0.4, 0, 1, 1)`，位置移动使用 `cubic-bezier(0.2, 0, 0, 1)`。状态反馈应在 `100ms` 内出现；异步操作保持原尺寸，避免布局跳动。`prefers-reduced-motion: reduce` 下取消非必要位移与缩放，只保留可理解状态所需的瞬时变化。

## 7. 组件结构与状态

### 7.1 通用状态矩阵

所有可交互组件覆盖 `default`、`hover`、`focus-visible`、`pressed`、`selected/current`、`disabled/read-only`、`loading`、`success` 和 `error`。键盘焦点使用可见的 `2px` ring 且不得被裁切；hover 不承载唯一操作。异步失败保留用户输入并提供原位恢复动作。

### 7.2 核心组件

| 组件            | 结构                                           | 尺寸与规则                                                      |
| --------------- | ---------------------------------------------- | --------------------------------------------------------------- |
| Button          | leading icon → label → loading indicator       | `sm 28px`、`md 36px`、`lg 44px`；同一可视区域原则上一个 primary |
| IconButton      | 44px 命中区内的 16/20/24px 图标                | 必须有可访问名称；非显然操作同时有 tooltip                      |
| Field           | label → control → hint/error                   | error 通过 `aria-describedby` 关联；提交失败不清空              |
| Input/Select    | value → optional affordance                    | 默认 36px，触控和公开流程 44px；不可混用未适配原生外观          |
| Badge/StatusDot | icon/shape → text                              | 高度 20/24px；颜色不是唯一信号                                  |
| Tabs            | tablist → tab → tabpanel                       | 漫游 tabindex，方向键、Home、End 可用                           |
| Menu/Popover    | trigger → anchored surface → item group        | 低频短动作；打开后焦点进入，关闭后回触发点                      |
| Dialog          | title → description → content → actions        | 明确提交/取消的短任务；compact 转底部 sheet                     |
| Drawer          | header → scrollable body → sticky actions      | 次级上下文和较长编辑；考虑软键盘与安全区                        |
| DataTable       | caption → header → rows → pagination           | 排序状态可读；行高 44/52px；compact 转主次卡片                  |
| EmptyState      | icon → reason → primary action → optional help | 操作按权限显示，不使用无意义占位文案                            |
| ErrorState      | summary → impact → recovery → diagnostic id    | 不显示原始堆栈，不只显示错误码                                  |
| Skeleton        | 与真实布局同形的中性块                         | 不猜测主题，不造成内容布局位移                                  |

图标采用统一线性 SVG 语言，尺寸仅使用 16/20/24px；导航、按钮和系统状态不得使用 emoji。头像使用 20/24/32/40/56px 五档，缺图时使用稳定身份缩写或统一 agent 轮廓。

## 8. 页面模式与页面族

### 8.1 Workbench

适用于工作区首页与洞察。结构为 `PageHeader → 身份/范围摘要 → 最近活动模块网格 → 快速入口`。工作区首页的最近项目、issue、收件箱和执行卡必须读取真实 API，分别呈现 loading、empty、error 和 ready，不用示例数据填充空白。xwide/wide 为四列，medium 为两列，compact 为单列；卡片最小高度 `116px`、内边距 `12px`、间距 `12px`。

### 8.2 DataView

适用于 issue、项目、成员、runtime、skill、squad、自动化规则和集成列表。结构为 `PageHeader → view/filter/sort toolbar → selection/bulk bar → list/table/cards → pagination`。筛选、排序和分页状态进入 URL；刷新保留旧数据并标记 refreshing。

### 8.3 Detail

适用于 issue、项目、成员、agent、runtime、执行、skill、squad 和集成详情。结构为 `breadcrumb → object header/actions → status summary → tabs → main content + property rail`。compact 下主内容先显示，属性 rail 变为 drawer；关键状态与负责人保留在标题下。

### 8.4 Board

结构为 `horizontal view switcher → filter/sort/WIP toolbar → shared column headers → lane/cell grid → quick create`。wide/xwide 使用横向滚动的共享列，固定列宽 `280px`、列间距 `16px`、列圆角 `14px`；普通卡片最小高度 `140px`、内边距 `12px`，标题最多两行，并按视图设置显示当前投影已提供的描述、项目、估算、截止时间、负责人和更新时间。截止日作为日历日期按 locale 呈现，更新时间按 locale 与用户时区呈现。标签、子任务进度及人/agent 类型头像须待服务端卡片投影提供后再接入，不得由前端虚构；虚拟化大列表继续使用固定 `72px` 紧凑卡并隐藏扩展元数据，避免破坏千卡性能契约。compact 一次展示一个列或泳道，并以可访问选择器切换。pointer、键盘和触控移动都必须走同一原子命令与回滚反馈，不能为视觉效果复制数据流。

### 8.5 Conversation

适用于聊天与收件箱。结构为 `conversation list → detail header → scrollable timeline → sticky composer/actions`。wide 为双栏，compact 为列表/详情两个路由；返回操作和草稿保留必须明确。

### 8.6 Settings

适用于账号、工作区和项目设置。账号设置使用 `224px secondary navigation → 704px content column → section panels`，内容列在余下空间居中；字段行在桌面采用左侧 label/hint、右侧控件的紧凑布局。普通设置、权限设置、令牌与危险操作分区；compact 下二级导航变为可横向滚动的顶部标签，内容回到 `16px` 页边距，字段行改为单列，危险区独立呈现。

### 8.7 PublicFlow 与恢复页

登录、注册、邀请、设备授权、回调使用 `context/identity → single-task card → help/security note`。内容框固定最大 `384px`，区块间距 `16px`，卡片内边距 `16px`、圆角 `14px`，标题使用 `24/32px` 中等字重；compact 仍保持同一内容宽度上限并由视口自然收缩。403、404 和全局错误页不渲染不可见工作区上下文，必须提供明确恢复路径。

## 9. 工作区隔离与页面状态

工作区路由的 `WorkspaceProvider` 是项目、issue、看板、成员和自动化页面的唯一作用域来源。存在 `/w/:workspaceSlug/...` 时严禁回退到 membership 首项。原位切换工作区必须：

1. 清空旧列表、游标、选择和局部错误。
2. 以请求代次丢弃旧工作区迟到的成功与失败响应。
3. 只接收当前工作区频道，且实时载荷的 `workspace_id` 必须一致。
4. 所有创建、详情深链和对话框目标使用 provider 的当前工作区。

`IssuesPage` 的列表、分页、快速创建、实时合并与深链已经遵循该约束；回归测试必须继续覆盖双工作区切换和迟到响应，不得以文档说明替代运行时守卫。

每个页面还要覆盖：初次 loading、局部 refreshing、empty（有/无创建权限）、可重试 error、forbidden/read-only、offline/reconnecting/resyncing、stale、optimistic pending、success 和 conflict rollback。

## 10. 生产 HTTPS 与同源传输

### 10.1 部署结论

仓库提供的前端容器只在内部 HTTP 端口提供静态资源和同源反向代理；Compose 将其绑定到回环地址，因此它是本机开发入口，不是可直接暴露的生产 TLS 终点。生产必须在受信任边缘终止 TLS，前端容器只允许从该边缘所在的私有网络访问。

生产发布必须 fail closed：

1. 公网 HTTP 请求只能返回到同一主机 HTTPS 的永久重定向或直接拒绝，绝不能返回登录页、应用 shell、API 或 WebSocket 升级。
2. TLS 证书无效、过期、域名不匹配或边缘配置缺失时停止发布，不允许降级为 HTTP。
3. 边缘先删除客户端提交的 `Forwarded` 与 `X-Forwarded-*`，再写入可信值；来源容器不得直接面向公网。
4. 生产会话保持 `Secure; HttpOnly; SameSite=Strict`，不得为了通过 HTTP 冒烟而关闭 `Secure`。
5. 严格传输响应头由公网 TLS 边缘添加；只有全部子域均已支持 HTTPS 时才启用子域扩展。
6. API、SSE、附件和 `/ws` 保持与页面同源；CSP、frame、referrer 与 permissions 头继续由现有入口链路提供。

### 10.2 现有同源行为

生产镜像以空的 API/WS 基址构建，浏览器使用页面 origin 请求 `/api` 与 `/ws`。`resolveWsGatewayUrl` 根据页面协议派生实时地址：HTTPS 页面得到 `wss://<当前主机>/ws`，HTTP 页面只在回环开发环境得到 `ws://<当前主机>/ws`。显式基址也会把 `https://` 归一为 `wss://`。

因此生产配置不得注入 `http://` API 基址或 `ws://` 实时基址。边缘需支持 WebSocket upgrade、长连接超时和流式响应禁缓冲；重定向必须保持原 path/query，且不得把敏感查询写入访问日志。

### 10.3 发布验证

发布前至少验证：

- 公网 HTTP 不返回应用内容，HTTPS 返回有效证书链。
- HTTPS 文档响应具有严格传输与现有安全头。
- 浏览器实际建立的实时连接使用 `wss:`，REST/SSE 没有 mixed-content 请求。
- 来源容器端口从公网不可达，只有边缘健康检查和反代可达。
- 登录后会话 cookie 含 `Secure`，注销和过期路径仍正确清理会话。

## 11. 静态标识与资源边界

站点图标为仓库内 `frontend/public/favicon.svg` 的 code-native SVG，并由 `frontend/index.html` 以根路径引用。图标不得包含脚本、事件属性、远程图片、字体或样式导入；浏览器首屏不能因站点图标发起跨源请求。其他运行时图标与字体也必须随构建产物自托管。

## 12. 格式与视觉门禁

`npm run format:check` 使用可审计的历史债务基线：

- 当前基线只冻结既有未格式化路径，新增 drift 一律失败。
- 本次分支触及的 `frontend/` 文件即使在历史基线中也必须格式正确。
- 令牌生成 CSS 由生成器幂等门禁负责并显式排除在格式器之外，避免格式器与生成器互相改写。
- 文件修正后从基线移除；禁止为绕过 CI 添加新基线项。
- `npm run format:check:all` 是前端树全量清零命令。债务按目录分批格式化、跑对应测试并删除基线项，直到该命令无输出，再删除基线兼容层。仓库其他目录由各自工作流负责，不能借此前端基线跳过其格式约束。

合入还必须通过：令牌生成幂等、颜色与 z-index 静态门禁、亮暗对比度、responsive/a11y contract、类型检查、单元与组件覆盖率、生产构建、真实浏览器 e2e、固定视口视觉回归。视觉基线只在有意设计变更时更新，失败时先审查 actual/expected/diff，不得直接覆盖期望图。
