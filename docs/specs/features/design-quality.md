# 前端设计质量与体验 Spec

> Issue：MES-133（承接并取代 MES-109 的实施基线）
>
> 状态：**Architecture / UX reviewed — Ready for implementation**
>
> 代码基线：`4bbff27a`（2026-07-30）
>
> 适用范围：Mesh Web SPA 全部公开页、应用页、浮层、状态与响应式形态
>
> 关联 Spec：
>
> - `theme.md`：主题协商、防闪烁、语义色、对比度和 forced-colors 的唯一权威。
> - `i18n.md`：文案外部化、locale、时区和格式化的唯一权威。
> - `search-command-palette.md`：搜索、命令面板、快捷键和规范深链的唯一权威。
> - `onboarding.md`：激活路径和首次空状态的唯一权威。
> - `attachment.md`：附件上传、扫描、预览和下载状态的唯一权威。
> - 各业务功能 Spec：业务模型、权限、接口和状态机仍由对应文件拥有。
>
> 本 Spec 只定义跨页面的视觉语言、信息架构、组件行为、响应式、可访问性、体验门禁和逐页优化方向，不创建业务表、不改变服务端业务契约。发生冲突时，业务语义服从对应功能 Spec；呈现和交互质量服从本 Spec。

---

## 0. 规范词

- **MUST / 必须**：实施与验收的硬门槛。
- **SHOULD / 应**：默认实现；偏离时必须在 PR 中写明理由和替代保障。
- **MAY / 可**：不影响本阶段完成的增强项。
- **P0**：阻断关键路径或让页面不可达/不可用。
- **P1**：显著增加认知负担、误操作或跨设备失败。
- **P2**：一致性和完成度问题，不阻断主要任务。

### 0.1 Clean-room 来源与合规边界

本 Spec 是 Mesh 的原创实现合同。设计输入仅来自以下两类：

1. Mesh 已有业务 Spec、当前运行界面、测试存证和公开产品需求；
2. 对合法公开、可见界面进行的黑盒观察，只记录通用布局规律、可测尺寸、状态转换和操作结果。

全流程 MUST 遵守：

- 不读取、检索、反编译或复制任何外部产品的源代码、CSS、source map、私有接口、私有素材或设计文件。
- 不提交外部界面截图、品牌名、Logo、URL、文案、图标、插画、组件命名或可识别创意素材。
- 观察截图只可作为临时测量输入，不进入仓库、Issue 附件、测试 fixture 或视觉基线。
- 本文给出的 token、组件 API、文案结构、图标规则和代码分层均由 Mesh 独立决定；实施者只按本文编码，不接触观察输入。
- 第三方字体、图标或库仅可选用 MIT、Apache-2.0、BSD、OFL 等允许项目使用的许可；引入前在依赖清单记录包名、版本、许可证和 NOTICE 要求。
- 无法由黑盒观察确认的行为，以 Mesh 业务 Spec、可访问性和一致性原则独立决策，不推断外部内部实现。

黑盒测量方法固定为：在浏览器 CSS 像素坐标系中记录外框、间距、字阶和状态出现顺序；同一对象至少在两个视口复测；尺寸归一到 §5 的令牌；颜色只归入语义角色并由 Mesh 独立色板重新取值；动效以录屏帧差估算后归一到 §5.5 的时长。仓库只保留归一后的原创决定和验证方法。

### 0.2 权威关系与可验收写法

- 本文是前端视觉、布局、交互状态、响应式和视觉测试环境的唯一权威。
- `theme.md` 继续拥有主题协商和持久化；本文拥有主题落到布局和组件后的表现。
- 各业务 Spec 继续拥有字段、权限、状态机和 API；本文不得改变其业务含义。
- “与现状接近”“保持一致”“体验良好”不是验收条件。本文所有 MUST 均需落到数值、状态、可操作结果或自动化断言。
- §13 的矩阵是阶段 2 实施和阶段 3 验收的共同 case 清单；任何 `N/A` 必须由业务 Spec 明确证明，不得由实现者自行删项。

---

## 1. 目标、原则与非目标

### 1.1 目标

1. 将 Mesh 从“功能齐全的工程界面”提升为“可长期高频使用的完整团队工作区”。
2. 在不牺牲信息密度的前提下，建立清晰的页面层级、稳定的操作位置和统一的视觉节奏。
3. 让人类成员与 AI 队友在名册、分派、评论、运行、通知中的状态同样清晰，强化 Mesh 的产品识别。
4. 使全部关键流程在 320px 手机、平板、桌面和宽屏上可完成，不存在只能桌面使用的入口。
5. 把视觉质量变成可执行、可测试、可回归的工程契约，而不是一次性改皮肤。

### 1.2 设计原则

1. **工作优先**：先呈现用户当前要判断和推进的工作，再呈现配置与系统信息。
2. **层级少而明确**：一页只有一个主标题、一个主操作、一个明确的当前上下文。
3. **高密度但不拥挤**：列表和看板使用紧凑节奏；编辑、审批和危险操作保留足够空间。
4. **渐进披露**：低频字段、筛选器、危险操作和技术细节进入抽屉、菜单或次级页。
5. **状态可解释**：颜色只作增强；文字、图标、位置和可操作恢复共同表达状态。
6. **同构体验**：鼠标、键盘、触控和读屏拥有等价路径；亮暗主题拥有等价层级。
7. **AI 可见但不喧宾夺主**：AI 身份、运行中、等待确认、失败和完成状态必须易辨，但不使用大面积高饱和装饰。
8. **先系统后页面**：新增视觉模式先沉淀 token、基础组件或页面模式，再进入业务 CSS。

### 1.3 非目标

- 不重做后端领域模型、权限和 API。
- 不引入可由用户任意编辑的主题或品牌色。
- 不在本阶段实现原生移动应用；Web 必须先达到完整触控可用。
- 不用大规模插画、渐变和装饰动画掩盖信息架构问题。
- 不把业务页全部重写为同一种卡片；不同任务采用列表、表格、看板、时间线或编辑器的合适形态。

---

## 2. 现状审查与存证

完整走查记录和截图索引见
[MES-109 前端设计审查存证](../../../frontend/e2e/evidence/mes109/README.md)。

### 2.1 审查范围

- 路由树：51 个 `<Route>` 节点。
- 页面实现：44 个 `*Page.tsx`。
- 样式：29 个产品 CSS 文件。
- 视觉基线：24 张核心页亮/暗、桌面/平板快照。
- 功能存证：117 张 PNG，覆盖主要业务域与关键状态。
- 存量走查：53 个浏览器用例，其中视觉/主题 38 个、关键交互 14 个、手机宽度补充 1 个。

### 2.2 已具备的基础

- 主题有 `light/dark/system` 协商、防闪烁、打印、forced-colors、reduced-motion 和对比度门禁。
- `tokenValues.ts` 已包含亮暗语义色、字阶、间距、圆角、阴影、动效和 z-index，生成产物有幂等检查。
- 设计层已有 Button、Input、Select、Dialog、Drawer、Menu、Tabs、Tooltip、Avatar、Badge、Skeleton、EmptyState、ErrorState、Toast 等原语及单测。
- 顶栏搜索已接入统一命令面板；Skills 路由已挂载；手机已有底部主导航、“更多”抽屉和安全区适配。
- i18n 已覆盖中英文，时间与数字有集中格式化基础。
- 评论、附件、看板、聊天、运行、收件箱等复杂流程已有真实浏览器用例和截图。
- 无障碍标记较多，核心视觉套件的 forced-colors 用例通过。

这些能力 MUST 保留；实施不得以视觉重构为由退化现有业务状态、实时行为、主题协商或无障碍能力。

### 2.3 MES-133 基线差距

下表只描述 `4bbff27a` 到本文目标的差距；已在 §2.2 落地的基础不得重复实现。

| ID   | 优先级 | 基线事实                                                               | 用户影响                                   | 阶段 2 必须交付                                                        |
| ---- | ------ | ---------------------------------------------------------------------- | ------------------------------------------ | --------------------------------------------------------------------- |
| B-01 | P0     | 桌面侧栏仍为单层文字列表，宽度 232px，未分组、未折叠                    | 导航扫描慢，大屏占位与规范值不一致         | §4.1 分组侧栏，240/64px 双态，状态与权限驱动入口                       |
| B-02 | P0     | 顶栏品牌不是首页链接，帮助/命令等仍使用字符作为图标                     | 返回路径不稳定，图标语言不统一             | 品牌链接、20px 原创 SVG 图标、统一 tooltip/aria-label                  |
| B-03 | P0     | 手机顶栏把搜索框折成第二行，占用 88–104px                              | 小屏首屏内容不足，键盘弹出前已浪费空间     | 单行 56px 顶栏；搜索改为图标入口，输入只存在于命令面板                 |
| B-04 | P0     | 手机看板仅把横向滚动限制在容器内，尚未完成单泳道与触控移动路径          | 可浏览但难以切列、移动和快速创建           | §8.3 单泳道、列 chips、长按移动 sheet、WIP 预告和回滚                  |
| B-05 | P0     | Issue 详情在窄屏仍是长页面，属性没有底部 sheet，活动与评论缺少清晰模式 | 核心操作被元数据淹没，返回位置不可预测     | §9.9 详情双栏/单栏、属性 sheet、时间线、草稿和返回位置恢复             |
| B-06 | P0     | 设计层缺 Combobox、Popover、DataTable、Card、Editor、Comment、Activity、CommandPalette、Board 模式 | feature 继续自造会造成状态与键盘行为分叉   | §7.8 所列组件全部进入 design 层或明确 pattern 层，feature 只做业务组合 |
| B-07 | P1     | 路由和页面导入集中在 `App.tsx`，页面级错误/加载边界粒度不足             | 首包增长，单模块异常可能影响整个应用       | §11.1 路由清单化、按页面族 lazy、每族 Suspense/ErrorBoundary           |
| B-08 | P1     | 现有截图视口、fixture、字体和命名不完全统一，状态覆盖不构成笛卡尔矩阵  | 视觉差异不可稳定复现，漏测边界状态         | §13.5–§13.7 固定环境、数据夹具、命名和完整 case 生成器                 |
| B-09 | P1     | feature CSS 仍存在自造密度、控件和浮层结构                              | 视觉换肤后仍会出现局部拼装感               | 按 §11 依赖方向迁移，迁完一页即删除该页重复原语                       |

### 2.4 设计系统缺口

基础 token 与第一批原语已完成，剩余缺口位于“复合组件 + 页面模式 + 迁移纪律”：

- Combobox、Popover、DataTable、Card、Editor、Comment、Activity、Command Palette、Board 尚无统一状态合同。
- 字符和 emoji 图标仍存在于顶栏、导航和局部操作；必须迁移到原创 SVG 图标系统。
- `.mesh-page` 仍固定 760px；数据页、详情页、设置页没有统一的 pattern 组件。
- feature CSS 仍可自造近似间距和断点；需要静态检查与容器级布局约束。
- overlay 分别维护焦点圈养、滚动锁和 Esc；需要统一 OverlayManager 与层级栈。
- 视觉用例覆盖页面正常态多，组件边界态、权限态、冲突态和输入法组合态不足。

### 2.5 信息架构与交互缺口

- 桌面导航已区分名称但仍未按“工作/团队/运行/管理”分组，当前项仍依赖大面积强调色。
- 手机已有全量入口，但顶栏、底栏和页面 sticky 区的垂直预算没有统一。
- Issue 详情、聊天、收件箱尚未使用同一“桌面双栏、手机路由化单栏”返回协议。
- 看板、表格、筛选器和业务选择器仍混用原生控件与 feature 自造组件。
- 空态和错误态已有原语，但权限、离线、冲突、输入保留等组合缺少页面级统一。
- AI 运行五态已在业务中出现，但图标、文案、时间和恢复动作尚未统一为跨页面 pattern。
- URL 尚未成为所有筛选、排序、tab 和详情返回上下文的真源，刷新或后退可能丢状态。

### 2.6 路由与浮层盘点

| 页面域     | 当前路由/入口                                                                                               | 本 Spec 覆盖                                                      |
| ---------- | ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| 认证       | `/login`（登录/注册）、`/forgot`、`/reset`、`/auth/oauth/callback/:provider`                                | PublicFlow、表单、MFA、错误与手机键盘                             |
| 公开协作   | `/device`、`/invite/:token`                                                                                 | 授权/邀请状态、安全提示、恢复路径                                 |
| 应用外壳   | `/` + `AppShell`                                                                                            | 工作台、桌面/手机导航、顶栏、全局状态                             |
| 账号设置   | `/settings`                                                                                                 | Appearance、Notifications、Security、Tokens、Audit 的二级设置结构 |
| 工作区     | `/w/:workspaceSlug`、`/w/:workspaceSlug/settings`                                                           | 工作区首页、切换、设置与权限                                      |
| 工作区管理 | `/w/:workspaceSlug/settings/labels`、`.../custom-fields`、`.../data`                                        | 标签、字段、导入导出、手机表单                                    |
| 收件箱     | `/inbox` + 顶栏铃铛                                                                                         | 双栏/单栏、未读、归档、实时、空态                                 |
| 项目       | `/projects`、`/projects/:projectId`、`/projects/:projectId/settings`、`/cycles`                             | 列表、详情、设置、周期、健康度与归档                              |
| 工作项     | `/issues`、`/issues/by-identifier/:identifier`、`/issues/:issueId`                                          | DataView、深链、详情、属性、评论、附件、标签、VCS、小队分派       |
| 看板       | `/board`、`/views/:viewId`                                                                                  | 保存视图、筛选、WIP、桌面拖拽、手机单泳道                         |
| 名册/Agent | `/members`、`/agents/:agentId`                                                                              | 人机同册、筛选、创建向导、详情和运行状态                          |
| Skills     | `/skills`、`/skills/marketplace`、`/skills/:skillId`                                                        | 保持列表、市场、详情真实刷新可达；统一安装、权限和可信度层级       |
| Squads     | `/squads`、`/squads/:squadId`、`/squads/:squadId/tasks/:taskId`                                             | 列表、详情、计划审批、拆解树、任务看板                            |
| 聊天       | `/chat`                                                                                                     | 会话列表、对话、上下文、附件、流式和手机单栏                      |
| 运行环境   | `/runtimes`、`/runtimes/:runtimeId`、`/executions/:executionId`                                             | 名册、注册向导、详情、日志、凭据与取消                            |
| 自动值守   | `/autopilots`、`/autopilots/new`、`/autopilots/:id`、`.../:id/edit`、`/autopilots/runs/:runId`、`/webhooks` | 列表、分步编辑、详情、运行、Webhook、护栏和 kill switch           |
| 兼容跳转   | `/automation` → `/autopilots`                                                                               | 保留重定向，导航不再使用含糊旧名称                                |
| 集成       | `/integrations`、`/integrations/:integrationId`、`/webhook-subscriptions`                                   | 目录、连接详情、绑定、事件、健康和订阅                            |
| 洞察       | `/insights` + 项目/Agent 内嵌面板                                                                           | KPI、图表、口径、无数据和响应式                                   |
| 全局浮层   | 命令面板、快捷键帮助、工作区切换器、收件箱弹层、Toast                                                       | 焦点、键盘、触控、层级和统一搜索入口                              |
| 兜底       | `*`、ErrorBoundary                                                                                          | 404、全局错误、重试、返回与诊断信息                               |

---

## 3. 对标差距清单

本节以成熟协作型 Web 产品应达到的行为基线为尺度，不依赖任何外部品牌资产或实现。

### 3.1 全局差距

| 切面      | 当前                               | 目标                                                               |
| --------- | ---------------------------------- | ------------------------------------------------------------------ |
| 导航      | 单层长侧栏；手机底栏/抽屉已可达    | 分组侧栏、可折叠 rail；桌面/手机入口、权限和名称同源                |
| 搜索      | 顶栏已接命令面板，业务对象检索不足 | 统一结果、最近项、命令、权限过滤、深链和无结果创建                  |
| 密度      | 局部过空、局部控件拥挤             | 页面模板按浏览/编辑/监控三种密度组织                               |
| 层级      | 主要靠蓝色、边框和粗体             | 通过 surface、排版、留白、图标和状态共同表达                       |
| 图标      | emoji、字符和 SVG 混用             | 统一 16/20/24px SVG 图标系统，文字标签为语义真源                   |
| 反馈      | toast 和局部状态并存但位置不稳定   | 即时局部反馈优先，toast 只承载跨区域结果，危险操作可撤销或二次确认 |
| 手机      | 外壳可达，复杂页仍多为缩窄桌面版   | 关键流程在 320px 可完成；无页面级横向溢出                          |
| 暗色      | token 与门禁完整，部分 feature 层级偏平 | 独立校准 surface、边界、焦点、状态和图表，不做简单反色         |
| 加载      | 大多有 skeleton                    | skeleton 与最终布局同形；局部刷新不抹掉已有内容                    |
| 空态      | 功能上存在                         | 与用户权限、上下文和下一步动作匹配                                 |
| 错误      | 多数有重试                         | 说明发生了什么、影响什么、怎么恢复、输入是否保留                   |
| 快捷键    | 命令面板和帮助已有                 | 所有快捷键可发现、可冲突仲裁、可通过鼠标/触控等价完成              |
| i18n/时区 | 基础完整                           | 长文案扩张、中文换行、数字对齐、时区歧义和相对时间均纳入视觉测试   |
| 附件      | 功能完整                           | 拖拽、粘贴、扫描、失败重试、移动端选择和灯箱操作使用统一反馈       |
| AI 协作   | AI 身份可见                        | 统一“运行中/待确认/需介入/完成/失败”状态语言和时间线               |

### 3.2 逐页差距与方向

| 页面族                       | 当前差距                                                 | 设计方向                                                                                    | 验收重点                                               |
| ---------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| 登录/注册/MFA/找回/重置      | 卡片可用但品牌、层级和模式切换较朴素；窄屏顶部留白不稳定 | 单一认证框架；展示品牌价值、当前步骤、第三方/账号分隔；错误贴近字段                         | 自动填充、密码管理器、软键盘、错误不清空输入           |
| 设备授权/邀请接受/OAuth 回调 | 状态完整但公共页之间缺少一致外壳                         | 共用 PublicFlowShell；明确来源、权限、工作区和安全提示                                      | 过期、已使用、无工作区、回调失败均有恢复动作           |
| 首页/工作区首页              | 已有工作区和最近 Issue，但缺少待确认、运行态和清晰优先级 | 工作台：我的工作、等待确认、AI 运行、最近项目、快速创建                                     | 无数据时进入 onboarding；有数据时优先展示待处理工作    |
| Onboarding                   | 有清单和存证，但和工作台/空状态的视觉关系弱              | 轻量进度卡 + 情境 CTA；完成后自动收起，可从帮助恢复                                         | 每一步只有一个主操作，实时完成不跳动                   |
| 项目列表/详情/设置/周期      | 卡片、表格、面板混杂；大屏利用不足                       | 项目列表采用密集行/网格切换；详情采用 Overview/Issues/Updates/Dashboard；设置独立二级导航   | 健康度、里程碑、周期、归档状态在列表一眼可读           |
| Issue 列表                   | 筛选和批量能力强，但控件密度与层级不统一                 | 标准 DataView：标题栏、保存视图、过滤 chips、表头、行、批量条                               | 320px 转主次行；批量条粘底；键盘上下选择               |
| Issue 详情                   | 主任务与元数据同权；手机超长；标识换行突兀               | 桌面 2 栏；内容/讨论为主，属性为 320px 侧栏；手机属性进入底部抽屉                           | 标题可内联编辑；活动/评论可切换；保存与冲突状态清楚    |
| 评论/提及/回应               | 功能完整，操作文字过密，执行占位表达弱                   | 时间线视觉；悬停/聚焦显示次要操作；agent 触发预览独立提示                                   | 草稿、附件、提及、发布、失败重试、撤销均不丢输入       |
| 附件/灯箱                    | 状态丰富但卡片和上传区视觉偏工程化                       | 统一文件卡、缩略图、扫描状态、进度环、失败操作；触控灯箱工具栏                              | 粘贴、拖拽、多文件、扫描中、感染、签名过期             |
| 看板/保存视图                | 桌面列宽和空白失衡；手机仅内部横滚；面板入口分散         | 桌面自适应列；手机单泳道切换；视图配置集中为一处；卡片突出标识/标题/责任人/状态             | 拖拽、键盘移动、触控长按、WIP 弹回、离线、跨项目确认   |
| 成员/Agent                   | 手机表格溢出；人和 agent 信息密度不平衡                  | 同一名册；头像、名称、类型、角色、状态主次分行；agent 运行态增强                            | 搜索、筛选、角色改动、停用、详情深链、唯一创建入口     |
| Skills                       | 列表、市场、详情已可达，但页面 pattern 与权限层级不统一 | 保留路由；统一安装/权限/可信度信息架构                                                      | 从侧栏和 Agent 详情均可达，路由刷新不 404              |
| Squads/任务                  | 拆解树、看板、审批信息量大                               | 概览 + 成员 + 计划 + 任务四区；审批作为显著但克制的决策卡                                   | 计划变更、阻塞、重试、leader 状态和依赖可读            |
| 收件箱                       | 功能和聚合完整，行层级与批量动作不够稳定                 | 双栏：分组列表 + 预览；手机单栏；未读、优先级、来源、对象一致排列                           | 标已读、归档、深链、实时新通知、空态和 quiet hours     |
| 聊天                         | 平板可用；手机把列表和对话纵向串联                       | 桌面双栏，手机列表/会话二选一；输入区粘底；上下文作为可收起条                               | 流式、停止、重生成、候选切换、附件、引用、长消息       |
| Runtimes/执行                | 监控信息完整但表格和日志缺少统一密度模式                 | 列表强调在线/容量/最近活动；详情分 Overview/Executions/Config；日志使用等宽字体和粘底工具条 | 实时日志、断线恢复、复制、下载、取消和凭据只显一次     |
| Autopilots/Webhook           | 配置表单长、触发/护栏/动作认知负担高                     | 分步编辑器 + 右侧摘要；危险状态用 kill-switch banner；运行时间线统一                        | 草稿、校验、时区、测试触发、密钥一次性展示             |
| Integrations/订阅            | 功能多，目录、连接、绑定、事件和订阅入口分散             | 集成目录与已连接分区；详情使用 Overview/Bindings/Events/Health；订阅独立但有互链            | 健康、重授权、绑定冲突、测试连接、投递失败恢复         |
| Analytics                    | 图表可用但信息层级与数字排版弱；窄屏可能裁切             | KPI 条 + 图表网格 + 口径说明；数字使用 tabular；图表支持横向缩放或重排                      | 无数据、数据不足、权限过滤、时区、亮暗和颜色非唯一信号 |
| 账号/工作区设置              | 长页面堆叠，危险区与普通偏好距离不足                     | 左侧/顶部二级设置导航；内容按 Appearance/Notifications/Security/Data/Danger 分页            | dirty state、保存结果、权限不可见、危险操作确认        |
| 搜索/命令面板/帮助           | 顶栏已接统一面板，但业务对象、最近项和无结果动作不完整   | 一个统一入口；最近项、导航、对象、命令分组；输入时展示权限过滤结果                          | `Cmd/Ctrl+K`、`/`、方向键、回车、Esc、读屏结果计数     |
| 404/全局错误/离线            | 基础错误页存在，缺少品牌和上下文                         | 保留应用外壳（无权限泄露场景除外），给返回、重试、状态页/诊断 ID                            | 路由错误、权限、服务失败、离线、重同步分开表达         |

---

## 4. 信息架构与应用外壳

### 4.1 桌面导航

侧栏按任务分组，组标题只在展开态显示：

1. **工作**：首页、收件箱、项目、工作项、看板。
2. **团队**：成员、技能、小队、聊天。
3. **运行**：自动值守、运行环境、洞察。
4. **管理**：集成、账号设置、工作区设置（按权限出现）。

中文命名写死：

- `Autopilots` → **自动值守**
- `Runtimes` → **运行环境**
- 不再出现两个同名“自动化”。

桌面展开侧栏宽 240px；折叠 rail 宽 64px。图标始终存在，展开态显示文字。当前项使用浅强调背景 + 强文字 + 3px 边缘指示，不使用整块高饱和色作为唯一信号。

### 4.2 顶栏

- 高度 56px；品牌是返回首页的链接。
- 工作区切换器紧随品牌，切换时保留可复用的相对上下文；不存在时回工作区首页。
- 搜索按钮/输入框打开统一搜索面板，不允许存在无行为输入框。
- 右侧依次为同步状态、收件箱、帮助、账号菜单。
- 连接状态在稳定连接时只显示图标和 tooltip；连接中、离线和重同步才显示文字，减少常态噪音。

### 4.3 手机导航

0–599px MUST 使用：

- 48–56px 紧凑顶栏：菜单、上下文标题、搜索、收件箱。
- 底部导航：工作台、工作项、看板、聊天、更多。
- “更多”打开全高抽屉，包含项目、成员、技能、小队、自动值守、运行环境、洞察、集成和设置。
- 底部栏适配 `env(safe-area-inset-bottom)`。
- 当前页、弹窗和软键盘不得被底部栏遮挡。

禁止在隐藏侧栏后不提供等价入口。

### 4.4 页面模板

| 模板         | 用途                                     | 结构                                       |
| ------------ | ---------------------------------------- | ------------------------------------------ |
| Workbench    | 首页、洞察                               | 页面头 + KPI/提醒 + 模块网格               |
| DataView     | issue、项目、成员、runtime、自动值守列表 | 页面头 + 视图/筛选条 + 列表/表格 + 分页    |
| Detail       | issue、项目、agent、runtime、集成详情    | breadcrumb + 对象头 + tabs + 主内容/属性栏 |
| Board        | 看板、小队任务                           | 工具条 + 横向工作区；手机为单泳道          |
| Conversation | 聊天、收件箱                             | 列表/详情双栏；手机单栏路由化              |
| Settings     | 账号/工作区/项目设置                     | 二级导航 + 内容列 + dirty/save 区          |
| PublicFlow   | 登录、邀请、设备授权、回调               | 品牌区 + 单任务卡 + 安全/帮助信息          |

每个模板 MUST 允许业务页通过 slot 扩展，但不得复制外壳、页标题和状态组件。

### 4.5 外壳几何与滚动所有权

| 模式 | 顶栏 | 主导航 | 页面 gutter | 内容区 | 固定区域 |
| --- | --- | --- | --- | --- | --- |
| compact `0–599` | 56px 单行 | 56px 底栏 + safe area；更多为全高 drawer | 16px | `100%`，最小宽 0 | 仅顶栏、底栏；页面工具条可在顶栏下 sticky |
| medium `600–1023` | 56px | 64px rail；可打开 320px drawer | 24px | `minmax(0, 1fr)` | 顶栏、rail |
| wide `1024–1439` | 56px | 240px 展开侧栏 | 24px | 标准页最大 1120px，数据页可占满 | 顶栏、侧栏 |
| xwide `≥1440` | 56px | 240px 展开侧栏 | 32px | 标准页 1120px，宽数据页最大 1440px | 顶栏、侧栏 |

- `body` 不作为应用页的业务滚动容器；`AppShell main` 是默认纵向滚动所有者。
- Dialog、Drawer、Command Palette 打开时锁住背景滚动并保存原滚动位置；关闭后恢复。
- Conversation 和 Board 可拥有内部滚动区，但不得再让 `main` 同方向滚动；同一手势方向最多一个滚动所有者。
- sticky 工具条的 `inset-block-start` 必须为顶栏 56px 加当前状态横幅实际高度。
- 页面锚点和焦点滚入视图时保留 16px 空隙，不得被 sticky 区遮挡。
- 宽数据页在 xwide 不居中压成阅读列；表单和正文仍分别受 640px/720px 上限约束。

---

## 5. 设计令牌

### 5.1 架构

令牌分三层：

1. **Reference**：原始色阶、尺寸和字体，不得在业务组件直接使用。
2. **Semantic**：`color.text.*`、`color.surface.*`、`space.*` 等跨组件语义。
3. **Component**：仅在组件确有稳定差异时定义，如 `button.primary.bg.hover`。

`tokenValues.ts` 继续作为唯一事实源；CSS 由生成脚本产出。迁移期允许旧 token 指向新 semantic token 的别名，一个发布周期后删除。

### 5.2 语义色

下表为规范值。任何调整必须先修订本文并在同一 PR 更新 `tokenValues.ts`、对比度登记和视觉基线；不得由页面或组件临时偏移。

| Token                      | Light                | Dark              | 用途             |
| -------------------------- | -------------------- | ----------------- | ---------------- |
| `--color-canvas`           | `#F7F8FA`            | `#0F1115`         | 应用背景         |
| `--color-surface`          | `#FFFFFF`            | `#171A21`         | 主内容表面       |
| `--color-surface-subtle`   | `#F1F3F5`            | `#1D212A`         | 次级区域         |
| `--color-surface-raised`   | `#FFFFFF`            | `#222732`         | 菜单、浮层、卡片 |
| `--color-surface-hover`    | `#F4F5F7`            | `#252B36`         | hover            |
| `--color-surface-pressed`  | `#E9ECF0`            | `#2B3240`         | pressed          |
| `--color-surface-selected` | `#EEF2FF`            | `#24263F`         | selected         |
| `--color-surface-sunken`   | `#F1F5F9`            | `#162032`         | 输入、代码、内嵌区 |
| `--color-text-strong`      | `#16181D`            | `#F4F6F8`         | 标题和主数据     |
| `--color-text`             | `#2B2F36`            | `#D7DBE0`         | 正文             |
| `--color-text-muted`       | `#5F6980`            | `#9AA3AF`         | 辅助信息         |
| `--color-text-disabled`    | `#98A2B3`            | `#697386`         | 禁用态           |
| `--color-border-subtle`    | `#EAECF0`            | `#252B35`         | 轻分隔           |
| `--color-border`           | `#D7DCE3`            | `#343C49`         | 控件与卡片边界   |
| `--color-border-strong`    | `#B8C0CC`            | `#4A5565`         | 强分隔           |
| `--color-accent`           | `#4F46E5`            | `#818CF8`         | 主操作、链接     |
| `--color-accent-hover`     | `#4338CA`            | `#A5B4FC`         | 主操作 hover     |
| `--color-accent-pressed`   | `#3730A3`            | `#6366F1`         | 主操作 pressed   |
| `--color-accent-soft`      | `#EEF2FF`            | `#24263F`         | 选中背景         |
| `--color-accent-contrast`  | `#FFFFFF`            | `#10131A`         | 强调色上的文字/图标 |
| `--color-focus-ring`       | `#2563EB`            | `#93C5FD`         | 焦点环           |
| `--color-scrim`            | `rgba(15,23,42,.52)` | `rgba(0,0,0,.72)` | 遮罩             |

状态色必须拆为 `fg/bg/border` 三元组：

- `success`：完成、在线、通过。
- `warning`：需要注意、接近限制、待确认。
- `danger`：失败、阻断、破坏性操作。
- `info`：同步、处理中、一般提示。
- `neutral`：草稿、未知、未开始。

状态色不允许同时充当优先级色、成员色和图表系列色。

状态三元组的规范值：

| 语义 | Light `fg / bg / border` | Dark `fg / bg / border` |
| --- | --- | --- |
| success | `#15803D / #DCFCE7 / #86EFAC` | `#4ADE80 / #052E16 / #14532D` |
| warning | `#92400E / #FEF3C7 / #FCD34D` | `#FBBF24 / #451A03 / #78350F` |
| danger | `#B91C1C / #FEE2E2 / #FCA5A5` | `#F87171 / #450A0A / #7F1D1D` |
| info | `#075985 / #E0F2FE / #7DD3FC` | `#38BDF8 / #082F49 / #0C4A6E` |
| neutral | `#475467 / #F2F4F7 / #D0D5DD` | `#98A2B3 / #252B36 / #343C49` |

### 5.3 间距与布局

| Token       | 值   | 典型用途         |
| ----------- | ---- | ---------------- |
| `space-0`   | 0    | 重置             |
| `space-0_5` | 2px  | 图形微调         |
| `space-1`   | 4px  | 紧凑内部间距     |
| `space-1_5` | 6px  | chip             |
| `space-2`   | 8px  | 控件内部         |
| `space-3`   | 12px | 行内组           |
| `space-4`   | 16px | 卡片、手机页边距 |
| `space-5`   | 24px | 标准分区         |
| `space-6`   | 32px | 大分区           |
| `space-8`   | 32px | 页面段落         |
| `space-10`  | 40px | 大段落           |
| `space-12`  | 48px | 页面顶部         |
| `space-16`  | 64px | 展示间距         |

`space-5`、`space-6` 是存量兼容键；新增代码 MUST 使用
`0/0_5/1/1_5/2/3/4/8/10/12/16` 的 4/8pt 主刻度，不得利用兼容键与主刻度的重复值表达新语义。

布局变量：

- `--shell-sidebar-expanded: 240px`
- `--shell-sidebar-collapsed: 64px`
- `--page-gutter-compact: 16px`
- `--page-gutter-medium: 24px`
- `--page-gutter-wide: 32px`
- `--content-readable: 720px`
- `--content-form: 640px`
- `--content-standard: 1120px`
- `--content-wide: 1440px`

### 5.4 圆角、边框和阴影

| 类别 | Token / 值 | 用途 |
| --- | --- | --- |
| 圆角 | `xs 4 / sm 6 / md 8 / lg 12 / xl 16 / full 999px` | xs 小标签；sm 控件；md 菜单/卡；lg 大卡；xl sheet；full 头像/chip |
| 边框 | `1px` 常规；`2px` selected/focus 辅助指示 | 禁止多层重边框；focus ring 不计布局 |
| `shadow-1` Light | `0 1px 2px rgba(15,23,42,.06), 0 1px 3px rgba(15,23,42,.10)` | 轻浮起卡/菜单 |
| `shadow-1` Dark | `0 1px 2px rgba(0,0,0,.40), 0 1px 3px rgba(0,0,0,.50)` | 同上，必须同时有 border |
| `shadow-2` Light | `0 2px 4px rgba(15,23,42,.06), 0 4px 8px rgba(15,23,42,.08)` | Popover、sticky toolbar |
| `shadow-2` Dark | `0 2px 4px rgba(0,0,0,.40), 0 4px 8px rgba(0,0,0,.45)` | 同上，必须同时有 border |
| `shadow-3` Light | `0 4px 8px rgba(15,23,42,.08), 0 12px 32px rgba(15,23,42,.16)` | Dialog、Drawer |
| `shadow-3` Dark | `0 4px 8px rgba(0,0,0,.50), 0 12px 32px rgba(0,0,0,.60)` | 同上，必须同时有 border |

存量 `--shadow-raised` 是迁移别名（Light `0 4px 16px rgba(15,23,42,.16)`；Dark `0 4px 16px rgba(0,0,0,.55)`），M8 删除；新增代码不得引用。

层级只使用 `--z-base:0`、`--z-sticky:100`、`--z-dropdown:200`、`--z-overlay:300`、`--z-toast:400`。业务 CSS 不得创建 `99/999/9999` 等近似层级；同层顺序由 DOM/OverlayManager 管理。

### 5.5 动效

| Token               | 时长  | 用途                         |
| ------------------- | ----- | ---------------------------- |
| `motion-instant`    | 0ms   | 直接状态切换                 |
| `motion-fast`       | 100ms | hover、pressed               |
| `motion-standard`   | 160ms | tooltip、menu                |
| `motion-deliberate` | 240ms | drawer、dialog、布局重排     |
| `motion-slow`       | 360ms | 仅 onboarding 完成等低频反馈 |

标准 easing：

- 进入：`cubic-bezier(.2,.8,.2,1)`
- 退出：`cubic-bezier(.4,0,1,1)`
- 移动：`cubic-bezier(.2,0,0,1)`

`prefers-reduced-motion: reduce` 下，非必要动画缩短至近零；拖拽位置、加载进度和焦点仍必须可辨。

### 5.6 动效行为表

| 场景 | 属性 | 时长 / easing | 完成反馈 | reduced-motion |
| --- | --- | --- | --- | --- |
| Button hover/pressed | `background-color, border-color, color, transform` | 100ms / move | pressed 最大下移 1px，不缩小文案 | 颜色即时切换，移除 transform |
| Tooltip | `opacity, transform` | 延迟 400ms；进入 160ms / enter；退出 100ms / exit | 指向关系保持 | 0ms，仍保留 400ms 延迟 |
| Menu/Popover | `opacity, transform` | 进入 160ms / enter；退出 100ms / exit | 焦点已进入首个可操作项 | 0ms |
| Dialog | `opacity, transform` | 进入 240ms / enter；退出 160ms / exit | 打开完成前即可聚焦，不能等动画 | 0ms |
| Drawer/Sheet | `transform, opacity(scrim)` | 240ms / move | 终态与安全区对齐 | 0ms，直接落终态 |
| Toast | `opacity, transform` | 160ms / enter；退出 160ms / exit | `status/alert` 同步宣布 | 0ms |
| Tabs 指示 | `transform, inline-size` | 160ms / move | panel 同一帧切换，不交叉淡化正文 | 0ms |
| Skeleton | `background-position` | 800ms linear 循环 | 不改变占位尺寸 | 静态 base/highlight 中间值 |
| 乐观插入/更新 | 背景高亮 | 1200ms 后 240ms 淡出 | 成功原位确认；失败回滚 | 直接显示 1200ms 静态轮廓 |
| Board 拖拽 | `transform, box-shadow` | 每帧跟手；落位 160ms / move | 占位、目标列和 live region 同步 | 不做惯性/缩放，位置仍跟手 |
| 冲突回滚 | `transform` + 原位 error | 240ms / move | 回原位并显示原因/重试 | 直接回位并聚焦 error |
| Onboarding 完成 | `opacity, transform` | 360ms / enter，仅一次 | 完成文案、下一步和可恢复入口 | 0ms，不播放庆祝动画 |

动画只允许 `opacity`、`transform` 和必要的颜色过渡；不得动画 `width/height/top/left` 造成持续 layout。进度条、上传百分比和日志滚动是状态可视化，不得因 reduced-motion 被隐藏。

---

## 6. 排版规范

### 6.1 字体配对

- Display/标题：`Manrope`（Latin）+ `Noto Sans SC`（CJK）。
- UI/正文：`Inter`（Latin）+ `Noto Sans SC`（CJK）。
- 代码/日志/标识：`JetBrains Mono` + `SFMono-Regular` + `Consolas`。
- 字体必须自托管并按 Latin/CJK 子集加载；首屏只加载常用 400/500/600，700 按页面需要加载。
- 字体失败时回退系统字体，不允许阻断页面或导致不可交互的长时间 FOIT。

### 6.2 Type scale

| 样式          | 字号/行高 | 字重 | 用途                     |
| ------------- | --------- | ---- | ------------------------ |
| `display-lg`  | 36/44     | 650  | 公共页展示标题，极少使用 |
| `display-sm`  | 30/38     | 650  | 工作台欢迎区             |
| `title-1`     | 24/32     | 650  | 页面标题                 |
| `title-2`     | 20/28     | 650  | 对象详情标题             |
| `title-3`     | 18/26     | 600  | 分区标题、dialog 标题    |
| `body-lg`     | 16/26     | 400  | 长文本、说明             |
| `body`        | 14/22     | 400  | 默认 UI 正文             |
| `body-strong` | 14/22     | 600  | 行标题、字段标签         |
| `body-sm`     | 13/20     | 400  | 表格、卡片辅助信息       |
| `caption`     | 12/18     | 500  | 时间、badge、元数据      |
| `micro`       | 11/16     | 600  | 极短状态标签，不用于正文 |

默认产品 UI 从当前 16px 调整为 14px；输入场景在 iOS 上实际字号不得低于 16px，以避免自动缩放。可通过控件专用 token 实现，不改变桌面密度。

### 6.3 数字、标识和代码

- 数量、时间、百分比和表格数字使用 `font-variant-numeric: tabular-nums`。
- issue 标识、commit、token 前缀、执行 ID 使用等宽字体；不得与标题抢层级。
- 大数字包含单位、口径和时间范围，不允许只显示孤立数字。
- 日志区域默认 13/20 等宽，可切换自动换行；复制保留原始文本。

### 6.4 中文排版

- 中文正文行高 1.6–1.7；表格/控件 1.4–1.55。
- 使用 `line-break: strict`、`overflow-wrap: anywhere` 处理长标识；标题不得把短 issue key 拆成孤立字符。
- 中文和 Latin/数字之间由排版引擎或文案保留可读间距，不在渲染后以字符串替换硬插空格。
- 长文本每行约 32–42 个汉字，最大阅读宽度 720px。
- 正文左对齐，不使用两端对齐；段间距大于行间距。
- 短标题可使用平衡换行；按钮、tab、badge 文案禁止换行。
- 省略必须有可访问的完整值（tooltip、详情或 `aria-label`）。

---

## 7. 组件与视觉细节

### 7.1 图标

- 建立统一 20px 线性 SVG 图标集，默认描边 1.75、圆角端点。
- 尺寸只用 16/20/24px；空状态插图例外。
- emoji 仅允许评论回应和用户内容；导航、按钮、状态、通知、自动值守触发器不得使用 emoji。
- 图标按钮必须有 tooltip 和 `aria-label`；危险图标必须同时有文字或确认上下文。
- “AI”使用统一 sparkle/agent glyph + 可见文字徽标，不依赖头像颜色。

### 7.2 头像与徽标

- 头像尺寸：20、24、32、40、56px。
- 人类无图时使用姓名缩写；agent 使用统一 agent 轮廓，不用随机 emoji。
- 同一身份的颜色由稳定 hash 生成，并在亮/暗表面保持可读。
- 状态 badge 高度 20/24px；内容为图标 + 文案，颜色不是唯一信号。
- 优先级用“图标形状 + 文案”，不只用颜色。

### 7.3 Button

变体：primary、secondary、ghost、danger；尺寸：sm 28、md 36、lg 44。

每个变体 MUST 定义：

- default、hover、focus-visible、pressed、loading、disabled。
- loading 时保持原宽、显示 spinner、阻止重复提交。
- primary 每个页面/浮层同一可视区域原则上不超过一个。
- danger 默认不与 primary 同色权重；最终破坏动作进入确认流程。

### 7.4 表单

- Label、control、hint、error 形成一个字段组件；错误与字段通过 `aria-describedby` 关联。
- 高度 36px（默认）、44px（触控/认证）；textarea 自适应但有最大高度。
- Select、combobox、date、multi-select 使用统一表面和图标；不得混用浏览器原生外观与设计组件外观。
- 校验时机：格式错误失焦后显示；提交错误立即显示；输入恢复合法后清除。
- 保存失败不得丢值；异步唯一性检查展示“检查中/可用/占用”三态。

### 7.5 Dialog、Drawer、Menu

- Dialog 用于需要明确提交/取消的短任务。
- Drawer 用于查看或编辑当前页的次级上下文，如 issue 属性、筛选器、成员详情。
- Menu 用于低频行操作，不承载长表单。
- 打开时焦点进入，关闭时返回触发点；`Esc` 关闭非破坏性浮层。
- 手机 dialog 转底部 sheet；最大高度考虑软键盘和安全区。

### 7.6 表格与列表

- 表头 12–13px，行内容 13–14px，行高默认 44px、舒适模式 52px。
- 可排序列显示方向和无障碍状态。
- 行主操作是点击名称/标题；次要操作仅在 hover、focus-within 或行菜单显示。
- 手机不压缩完整桌面表格：转换为主次行卡片；确需横向滚动时，滚动容器必须有边缘提示且首列粘住。

### 7.7 空状态与错误态

空状态由四部分组成：

1. 统一风格的小型线性插画或图标。
2. 说明当前为什么为空。
3. 一个与权限匹配的主操作。
4. 可选帮助链接或示例。

错误态由四部分组成：

1. 发生了什么。
2. 哪部分受影响，已有输入/数据是否保留。
3. 可执行恢复动作。
4. 可复制的诊断 ID（服务端提供时）。

禁止仅显示“出错了”“请求失败”或裸错误码。

### 7.8 组件实施矩阵

本节是组件 API 和状态验收的最低合同。表中尺寸均为 CSS px；compact/touch 命中区最低 44×44px。所有交互组件共同覆盖
`default / hover / focus-visible / pressed / selected / disabled / loading / error`
中适用的状态；不适用状态必须在组件测试中显式注明。

#### 7.8.1 输入与浮层

| 组件 | 尺寸与结构 | 状态和鼠标/触控 | 键盘与焦点 | 禁用、错误、加载与语义 |
| --- | --- | --- | --- | --- |
| Button | `sm 28 / md 36 / lg 44` 高；横向 padding `10/14/18`；图标 16/20；同一尺寸切 loading 不改宽 | primary/secondary/ghost/danger；pressed 下移 ≤1px；触控命中 ≥44 | `Enter/Space` 激活；focus ring 2px + offset 2px；提交后焦点留在按钮 | disabled 不触发且有原因；loading `aria-busy=true`、阻止重复提交；danger 进入确认或可撤销流程 |
| IconButton | 视觉 28/36/44，图标 16/20/24；无可见文字时命中区 ≥44 | hover/pressed 与 Button 同构；active/current 另有轮廓或底色 | `Enter/Space`；focus 不被圆角裁切 | 必须 `aria-label`；桌面 hover/focus 显示 Tooltip；loading 用同尺寸 spinner；禁止字符/emoji 充当产品图标 |
| Input / Textarea | 输入高 36，认证/触控 44；textarea 最小 88、最大 320 后内部滚动；label→control→hint/error 间距 6/6 | placeholder 不代替 label；clear 仅有值且可编辑时出现；输入中不抖动布局 | Tab 顺序为 label 后 control 再辅助操作；`Esc` 仅清局部建议，不清已提交值；IME composing 不触发快捷键 | disabled 与 readonly 分开；error 1px danger border + 原位文字；异步检查显示 checking/success/error；`aria-invalid`、`aria-describedby` 完整 |
| Select | 36/44 高；右侧 20px 展开图标；菜单宽度 ≥trigger、≤360 | pointer 打开；选中行有 check + selected 背景；触控使用 bottom sheet（选项 >8 或含搜索时） | `Alt+↓/Space` 打开；↑↓、Home/End、首字母跳转、Enter、Esc；关闭归还 trigger | disabled 不打开且说明原因；loading 菜单内 skeleton；请求失败保留旧值和重试；`role=combobox/listbox` 或原生 select 二选一，不混搭 |
| Combobox | 输入 36/44；popup `min-width:trigger`、最大 560；行高 36/44；组头 28 | 120ms 防抖，可取消请求；命中字符用字重/下划线，不只用颜色；支持单选/多选明确分型 | 输入保持焦点，`aria-activedescendant` 指向活动项；↑↓、Home/End、Enter、Esc；Tab 接受当前值仅在 API 明示时启用 | empty/error/loading 在同一 popup 原位；disabled 不请求；多选 chip 可 Delete/Backspace；`aria-expanded/controls/autocomplete` 必须正确 |
| Dialog | 宽 `sm 400 / md 560 / lg 720`，最大 `calc(100vw - 32px)`，高 ≤85vh；标题/正文/操作区 padding 24；compact 转底部 sheet | scrim 点击仅关闭非破坏、无 dirty 的任务；打开锁背景滚动；提交中不可重复关闭 | 初焦点：错误摘要→首字段→主容器；Tab 圈定；Esc 关闭非破坏态；关闭归还 trigger | `role=dialog`、`aria-modal`、label/description；loading 保持结构；错误聚焦摘要且保留表单；danger confirm 必须复述对象 |
| Drawer / Sheet | 桌面宽 `sm 360 / md 480 / lg 640`；compact 全宽、顶部圆角 16、最大高 `calc(100dvh - 24px)` | 适合次级上下文；拖动手柄只作提示，不作为唯一关闭路径；背景锁定 | 与 Dialog 同一 OverlayManager、焦点圈定和归还；Esc 规则相同 | dirty 时关闭确认；loading skeleton 同形；错误不关闭 drawer；`role=dialog` + `aria-modal` |
| Popover | 宽由内容决定，最小 180、最大 360；padding 8/12；与 trigger 间距 6 | 点击或显式快捷键打开；点击外部关闭；不得承载多步骤长表单 | trigger 用 `aria-expanded/controls`；首个可操作项获焦；Esc 关闭并归还 | 请求型内容显示局部 skeleton/error/retry；disabled trigger 不打开；非模态，不阻断背景读屏 |
| Tooltip | 最大宽 280，padding 6×8，字号 12/18；距离 trigger 6 | hover/focus 延迟 400ms；离开 100ms 关闭；触控不承载唯一信息 | focus 可触发；Esc 关闭；tooltip 自身不可获得焦点 | 只放简短补充，不放按钮/错误/关键说明；`role=tooltip`，trigger `aria-describedby`；disabled 控件由可聚焦 wrapper 提供说明 |
| Menu | 宽 180–320；行 36/44；图标列 20，快捷键列右对齐；分隔 1px | 单次低频操作；submenu 延迟 200ms；危险项位于末组且有文案 | 打开聚焦首个可用项；↑↓、Home/End、首字母、Enter/Space、Esc、←/→ 子菜单；关闭归还 | disabled 项可聚焦并说明原因但不可激活；执行中仅目标项 loading；失败关闭后用原位/toast 说明；`role=menu/menuitem` |
| Tabs | tab 高 36/44；间距 4；指示条 2px；窄屏单行横向滚动 | active 与 hover 不同；切 tab 不丢共享编辑草稿；深链 tab 写 URL | Arrow 切焦，Home/End；默认 automatic activation 仅在 panel 即时可用时启用，否则 Enter/Space 激活 | loading 在 panel 内，不禁用整个 tablist；无权 tab 不渲染；`tablist/tab/tabpanel`、`aria-selected/controls` 配对 |
| Toast / Banner | Toast 宽 ≤360，间距 8，桌面右下/compact 顶部；Banner 高度由 40 起，正文最多 2 行 | success 4s、info 6s、warning/danger 不自动消失或 ≥8s；同 key 合并；可操作结果优先原位反馈 | Toast 操作可 Tab 到达，关闭后不抢回业务焦点；Banner 操作按 DOM 顺序 | `status` 用 polite，破坏失败 `alert`；loading 不用 toast；错误含恢复动作；离线/重同步使用全局 Banner，不刷屏 |

#### 7.8.2 数据展示与反馈

| 组件 | 尺寸与结构 | 状态和交互 | 键盘与焦点 | 禁用、错误、加载与语义 |
| --- | --- | --- | --- | --- |
| Avatar | `20/24/32/40/56`；圆形；状态点为尺寸的 25%，最小 6 | 图片→稳定缩写→agent 原创轮廓三级回退；hover 只增强 | 可点击头像使用 Link/Button 外壳，头像图本身不入 Tab | 图片加载失败无破图；状态必须有文字/tooltip；`alt=""` 当相邻已有姓名，否则 alt 为姓名 |
| Badge / Status | 高 20/24；水平 padding 6/8；图标 12/16；单行 | neutral/info/success/warning/danger；selected chip 另有 remove；颜色不作唯一信号 | 可删除 chip 的 remove 独立可聚焦；静态 badge 不入 Tab | loading 用文案“处理中”而非 spinner-only；未知值为 neutral + 原始安全文案；状态 badge 有可读文本 |
| DataTable | 表头 36；行 compact 44 / comfortable 52；cell padding 8×12；首列/表头可 sticky | hover、selected、expanded、dirty；行点击只作用于主链接，不能让整行伪装按钮 | sortable header 为 button；↑↓ 行导航仅在 grid 模式启用；Space 多选；批量条随选择出现 | loading 保留表头 + 同列 skeleton；empty/error/permission 占完整列宽；排序 `aria-sort`；虚拟化维持可读行序 |
| Card / ListRow | Card padding 12/16，最小高由内容；ListRow 高 44/52；主次信息间距 4/8 | default/hover/focus-within/selected/dragging；整卡可点时内部不能嵌套交互链接 | 主链接在 Tab 序；行菜单后置；focus-within 显示次要操作 | loading 用同形 skeleton；错误卡说明受影响对象；只读保留可复制信息；语义优先 `article/li`，不滥用 `button` |
| Skeleton | 圆角跟随目标；文本高 12/16，标题 20/24；数量和最终区块相同 | 首次加载显示；refreshing 保留已有内容，不回退整页 skeleton | 不入焦点、不影响读屏顺序 | 容器 `aria-busy=true`，skeleton `aria-hidden=true`；reduced-motion 静态；超过 10s 切换为可解释 loading + 取消/重试 |
| EmptyState | 图形 48–96；正文宽 ≤480；主操作 36/44 | first-use / filtered-empty / completed-empty / permission-empty 分型 | 标题后按主操作→次操作→帮助链接排序 | 不把无权限伪装成无数据；数据仍加载时不得提前闪 empty；使用 heading + description，不强制 live announce |
| ErrorState | 图形 32–64；正文宽 ≤560；诊断 ID 等宽；恢复操作 36/44 | recoverable / forbidden / not-found / conflict / fatal 分型 | 错误出现后聚焦摘要（提交错误）或保持原焦点（后台刷新失败） | 必须说明发生、影响、保留和恢复；禁止裸异常；`role=alert` 仅用于用户刚触发的失败，页面初始错误用普通 region |

#### 7.8.3 协作产品模式

| 组件 | 尺寸与结构 | 状态和交互 | 键盘与焦点 | 禁用、错误、加载与语义 |
| --- | --- | --- | --- | --- |
| Editor / Composer | toolbar 高 36；编辑区最小 96、最大 320 后滚动；附件区在正文下；底栏放状态与提交 | empty/draft/saving/saved/uploading/submitting/failed/readonly；粘贴链接、图片和文件；`@` 用 Combobox | `Mod+Enter` 提交，`Shift+Enter` 换行；IME composing 时 Enter 不提交；Esc 先关候选再退出编辑 | 本地草稿按 workspace+resource+member 隔离；失败保留正文/mention/附件；readonly 仍可复制；`aria-multiline`、toolbar 按钮可读 |
| Comment | Avatar 32；内容列间距 12；正文最大 720；meta 12/18；线程缩进桌面 44、compact 16 | sending/sent/edited/failed/deleted/resolved/highlighted；hover/focus 显示回应/更多；触控常驻更多 | 主体按作者→时间→正文→附件→操作；`R` 回应仅在非输入上下文；深链到达后聚焦 heading 并高亮 1.2s | 发送失败原位重试/编辑；删除保留墓碑；agent 触发在发布前可见提示；`article` + 可访问作者/时间 |
| Activity / Timeline | 行最小 40；轨道 1px；节点 8；时间列桌面 112，compact 放正文下 | event/comment/execution/system 分型；可折叠连续同类事件；实时插入不抢滚动 | 折叠按钮 Enter/Space；“跳到最新”可聚焦；用户离底部 >80px 时不自动滚动 | 加载 older 显示顶部 skeleton；失败保留已读事件；事件有自然语言文本，不以图标/颜色代替；`ol` 保持时间顺序 |
| Command Palette | 桌面宽 640、最大高 `min(720px, 72vh)`；compact 全屏；搜索 44；结果行 44；组头 28 | idle/recent/searching/results/empty/offline/error；120ms 防抖；本地结果立即、远程增量；活动项不因旧响应跳动 | 打开即聚焦输入；↑↓、Home/End、Enter、Esc；Tab 只到页脚动作；`Mod+K` 切换；关闭归还触发点 | 无权结果服务端和前端双过滤；离线只显示本地命令；loading 不清旧结果；`combobox/listbox/option` + live 结果计数 |
| Board / Kanban | 桌面列宽 280–320、间距 12；列头 44 sticky；卡片最小高 72、padding 12；compact 一次一列 | loading/empty/filtered/offline/dragging/drop-valid/drop-blocked/optimistic/conflict；pointer 移动阈值 6px，touch 长按 350ms | 卡片 roving tabindex；Enter 打开；`M` 进入移动模式，方向键选列/位置，Enter 确认、Esc 取消；live 宣布 | WIP block 在提交前显示；失败回原位并保留焦点；offline 禁止不可安全排队的移动；列/卡使用可读 heading/list 语义，提供非拖拽菜单路径 |

### 7.9 组件 API 与测试约束

- 原语 props 只表达语义（`tone/size/state`），禁止向 feature 暴露任意颜色、阴影或 z-index。
- Overlay 统一注册到一个栈：同层最多一个 modal；Esc 只关闭栈顶；Toast 不参与焦点栈。
- `disabledReason`、`errorMessage`、`loadingLabel` 是可见/可读文案，不允许只传 boolean 后由组件猜测。
- 异步组件以稳定 request id 丢弃过期响应；unmount 后不得写状态。
- 每个表中组件至少具备：状态单测、键盘单测、axe 自动扫描、light/dark/forced-colors 视觉用例和 compact 触控命中区断言。
- Editor、Comment、Activity、Command Palette、Board 属 pattern；业务模块可注入数据和命令，不得 fork 其交互状态机。

---

## 8. 响应式与触控

### 8.1 断点

| 模式    | 范围        | 外壳                                |
| ------- | ----------- | ----------------------------------- |
| compact | 0–599px     | 手机顶栏 + 底栏 + 抽屉              |
| medium  | 600–1023px  | 折叠 rail 或抽屉；内容单/双栏按容器 |
| wide    | 1024–1439px | 展开侧栏；标准内容宽度              |
| xwide   | ≥1440px     | 展开侧栏；多栏/更宽数据视图         |

业务组件 SHOULD 使用 container query，而不是只依赖 viewport。断点值集中为 token/常量，禁止各 CSS 文件随意创建近似值。

### 8.2 通用规则

- 320px 宽、200% zoom 下不得丢失主操作或产生页面级双向滚动。
- 触控目标最小 44×44px；视觉图标可小于 44px，但命中区不可小于。
- sticky 元素必须计算顶栏、底栏和安全区，不遮挡锚点和输入。
- hover 才出现的能力，在触控设备上必须常驻或进入菜单。
- 横向手势不得与浏览器返回手势、看板拖拽和轮播冲突。
- 软键盘出现时，当前输入、提交按钮和错误提示保持可见。

### 8.3 关键页面重排

- **看板**：compact 只展示一个泳道；顶部横向 chips 切列，支持左右滑/按钮切换；卡片在当前列内排序。拖到其他列通过长按后出现列目标 sheet。
- **Issue 详情**：主内容先展示；属性栏变“属性”按钮，打开底部 sheet；关键状态/负责人保留在标题下的 summary chips。
- **成员/issue/项目列表**：表格转主次行，行菜单承载管理操作。
- **聊天/收件箱**：列表和详情路由化；进入详情后有明确返回，输入区粘底。
- **设置**：二级导航变顶部 select/分组列表；危险区独立页。
- **Analytics**：KPI 两列或单列；图表重排，不压缩文字；表格使用卡片或受控横向滚动。

---

## 9. 交互状态与关键流程

### 9.1 状态矩阵

每个交互组件必须覆盖：

| 状态             | 要求                                        |
| ---------------- | ------------------------------------------- |
| default          | 层级和可操作性明确                          |
| hover            | 仅增强，不承载唯一信息                      |
| focus-visible    | 2px ring + offset，不能被 overflow 裁切     |
| pressed          | 立即反馈，100ms 内出现                      |
| selected/current | 与 hover 不同，同时有 ARIA 状态             |
| disabled         | 说明原因；能解释时优先只读而非静默 disabled |
| loading          | 保持布局，防重复操作                        |
| success          | 局部确认优先，必要时 toast                  |
| error            | 原位提示 + 恢复动作                         |

### 9.2 登录、注册和 MFA

1. 首屏聚焦第一个可编辑字段。
2. 提交后按钮保持宽度并进入 loading。
3. 账号锁定、凭据错误、网络错误分开；密码字段不被清空，除非安全契约要求。
4. MFA 显示步骤、目标、剩余尝试/恢复路径；验证码自动分组但仍是一个可粘贴字段。
5. 注册完成给出邮箱和下一步，不把用户留在无导航成功页。
6. 移动端使用正确 `inputmode`、`autocomplete` 和键盘 Next/Go。

### 9.3 创建 issue

1. 入口：页面主按钮、命令面板、“C”快捷键、空状态主操作。
2. 快速创建只要求标题；项目、负责人、优先级可渐进展开。
3. `Cmd/Ctrl+Enter` 提交，`Esc` 关闭；有内容时关闭先确认。
4. 成功后列表/看板乐观插入并高亮 1.2s；服务端失败回滚且保留草稿。
5. 从看板列创建时继承该列分组；继承结果在表单内可见。

### 9.4 看板拖拽

1. pointer down 不立即拖动；移动阈值后进入 drag。
2. 卡片原位保留占位，拖拽副本有阴影和轻微缩放。
3. 列目标显示可放置状态；WIP warn/block 文案在落下前可见。
4. 乐观落位失败时动画回原位并 toast；409 后重取最新位置。
5. 键盘：聚焦卡片后进入移动模式，方向键选目标，Enter 确认，Esc 取消。
6. 触控：长按进入移动模式，目标列通过 sheet 选择；不依赖精细横向拖动。

### 9.5 评论与附件

1. 编辑器自动保存本地草稿，并显示“已保存/保存中”弱提示。
2. 输入 `@` 打开候选；agent 候选显示将触发运行，发布前再次摘要。
3. 附件上传、扫描和发布互不混淆；扫描中可发布但明确“暂不可下载”。
4. 提交失败保留正文、提及和附件；提供重试。
5. 发布成功滚动到新评论并短暂高亮；删除可短时撤销，无法撤销的情况必须确认。
6. 次要操作在卡片 hover/focus 时显示，触控进入“更多”菜单。

### 9.6 命令面板与搜索

1. 顶栏搜索点击、`Cmd/Ctrl+K` 均打开同一面板；`/` 聚焦搜索。
2. 未输入时展示最近访问、常用命令和快捷创建。
3. 输入后分组展示工作项、项目、成员/agent、视图、聊天和命令。
4. 120ms 防抖；请求可取消；旧响应不得覆盖新查询。
5. 方向键移动、Enter 打开、`Cmd/Ctrl+Enter` 在新上下文打开（若产品支持）、Esc 关闭。
6. 结果为空时提供语法提示和创建入口；失败时保留查询并可重试。
7. 读屏 live region 宣布结果数量和当前选项。

### 9.7 切换工作区

1. 弹层展示最近工作区、搜索、当前角色和创建入口。
2. 切换后尽量保留相对页面；目标工作区无对应资源时回工作区首页并说明。
3. 切换中保持外壳，不显示前一工作区数据。
4. 键盘焦点与滚动位置可预测；手机使用全高 sheet。

### 9.8 AI 运行反馈

跨页面统一五态：

- `queued`：已排队。
- `running`：运行中，显示开始时间/进度入口。
- `waiting`：等待人工确认或输入。
- `succeeded`：完成，展示结果入口。
- `failed`：失败，展示原因摘要和重试/介入。

同一执行在 issue、评论占位、收件箱、agent 详情和执行页使用相同文案、图标和 tone。

### 9.9 逐页几何、状态与返回路径

| 页面/入口 | 桌面（wide/xwide） | compact（320/390） | 主操作与状态 | 返回路径 |
| --- | --- | --- | --- | --- |
| 登录/注册/找回/重置 `/login` `/forgot` `/reset` | PublicFlow 单卡宽 420，距顶 `min(12vh, 96px)`，padding 32，字段 44 高 | 卡片去阴影和外框，宽 100%，padding 24/16，顶距 32 | 每步一个 44 高 primary；登录/注册切换不清共享字段；loading/error/locked/verified | 成功进安全 `next` 或工作台；找回/重置完成回登录并保留账号 |
| 首次引导 | 工作台右侧/首区 360 宽进度卡，主内容仍可操作 | 工作台首区全宽；一次只展开当前步骤 | 当前步骤唯一 CTA；实时完成后 360ms 收起；loading/empty/error/dismissed/completed | 完成留在工作台；关闭后可从帮助恢复到同一步 |
| 工作区首页 `/` `/w/:slug` | 内容宽 1120；4 个 KPI（每格最小 180）+ 2:1 主次网格；区块间 24 | 单列；KPI 2 列，320 下 1 列；快速创建 44 高 | 新建工作项；loading/first-use/ready/offline/permission/long-data | 卡片进入对象；返回恢复首页滚动和工作区 |
| 看板 `/board` `/views/:id` | 占宽数据模板；toolbar 44；列 280–320；工具条→列头→卡片三层 sticky 不重叠 | 一次一列；顶部列 chips 44；卡片全宽；移动经 sheet | 新建工作项；drag/keyboard/touch move、WIP、filtered-empty、offline、conflict | 打开详情记录 `from`、view/filter/scroll；返回定位原卡 |
| Issue 列表 `/issues` | 内容最大 1440；页头 48、筛选条 44、行 44；批量条粘底 | 主次行卡片 64–88；筛选 bottom sheet；批量条在底栏上方 | 新建工作项；loading/refresh/empty/error/readonly/offline/selected | 详情返回保留 query、页码/游标、选择和滚动 |
| 创建 Issue（全局浮层） | md Dialog 宽 640；标题首字段；常用属性一行，低频字段折叠 | 全屏 sheet；标题和提交在软键盘上方；属性分组 | 快速创建只需标题；`Mod+Enter`；dirty close confirm；submitting/error/success | 成功默认回来源并高亮；“创建并打开”进入详情；取消归还触发点 |
| Issue 详情 `/issues/:id` | Detail 两栏：主列 `minmax(0,720)`，属性列 320，间距 32；对象头 + Description + Activity | 单列；标题下保留状态/负责人 chips；属性按钮开 sheet；活动/评论单一时间线 | 编辑/评论；loading/partial/error/readonly/offline/conflict/deleted | 浏览器返回优先 `from`；无 `from` 回同工作区 Issue 列表 |
| 评论/活动（详情内） | 正文宽 ≤720；评论头像 32；线程缩进 44；composer 在时间线尾 | 缩进 16；composer sticky 于底栏上方；操作进更多菜单 | 评论、回复、提及、附件；draft/uploading/sending/failed/resolved | 深链关闭高亮后仍在详情；取消回复回原评论并恢复焦点 |
| 成员/邀请 `/members` `/invite/:token` | 成员 DataTable；筛选条 44；邀请 Dialog 560；详情可用 Drawer 480 | 成员卡 72；筛选 sheet；邀请 PublicFlow/全屏 sheet | 邀请/新建 agent 权限化；loading/empty/error/readonly/expired/used | 管理 drawer 关闭回原行；邀请接受后进目标工作区首页 |
| 收件箱 `/inbox` | Conversation 双栏：列表 360，详情 `minmax(0,1fr)`；批量菜单在列表头 | 列表/详情二选一；详情用 query/子路由；明确返回收件箱 | 标已读/归档/恢复；loading/empty/error/offline/realtime-new | 返回恢复筛选、分组、scroll；目标对象深链带 `from=inbox` |
| 聊天 `/chat` `/w/:ws/chat/:sessionId` | 会话列表 320 + 对话主列；消息宽 ≤720；composer 粘底 | 会话列表/对话路由化；顶栏返回；composer 适配键盘/safe area | 新会话/发送/停止；streaming/waiting/failed/offline/long-message | 返回会话列表保留草稿和滚动；对象引用打开后可回原消息 |
| 搜索/命令面板 | 居中宽 640、高 ≤72vh；搜索 44；分组结果 | 全屏；顶部搜索 + 返回；结果触控行 44 | 搜索/导航/创建；idle/searching/results/empty/offline/error | 关闭回触发点；打开结果写 `from`，返回恢复 query 和活动项 |
| 设置 `/settings` `/w/:slug/settings/*` | Settings：二级导航 240 + 表单列 640；danger 独立页/末组 | 二级导航改页面内 select/分组列表；表单全宽；保存条在底栏上方 | 保存；pristine/dirty/saving/saved/error/readonly/conflict | 离开 dirty 页确认；保存后留当前 section；危险动作完成回安全父页 |

公共流程不渲染私有应用侧栏；鉴权失效从受保护页跳登录时必须把规范路径、query 和 hash 编码为安全 `next`，回跳后不得显示上一工作区数据。

### 9.10 关键用户旅程与步骤预算

步骤数按“需要用户作出一次独立决定或提交”计，页面加载和自动实时更新不计。

| 旅程 | 最长主步骤 | 必须行为 | 成功反馈 |
| --- | --- | --- | --- |
| 注册/登录 | 2（账号信息→必要验证） | 首字段自动聚焦；错误不清安全允许保留的输入；安全 `next` 回跳 | 进入原目标或工作台；不经过空白成功页 |
| 首次激活 | 5（工作区→成员/agent→Issue→分派/提及→查看回评） | 一次只强调当前步骤；已存在事实自动补证；可关闭和恢复 | 工作台原位完成，明确下一项真实工作 |
| 创建 Issue | 2（输入标题→提交） | 从页面、空态、看板列、`C`、命令面板同一表单；来源上下文可见 | 来源列表/列原位插入并高亮 |
| 分派 agent | 3（打开负责人→选择→确认触发后果） | picker 中人/agent/小队同册分组；不可运行项说明原因；确认显示将触发的状态 | 属性原位更新；运行态在 1s 内出现或显示排队原因 |
| 推进看板 | 1（移动并确认落位；pointer 直接落位等价） | 提交前显示 WIP；键盘/触控有等价路径；409 收敛 | 卡片留在服务端确认位置，失败回滚并保留焦点 |
| 评论/提及 | 2（输入→发布） | 草稿自动保存；agent 提及后果在发布前可见；失败不丢正文/附件 | 新评论原位出现并短暂高亮 |
| 处理收件箱 | 2（选择→标已读/归档或进入对象） | 选择即标已读的行为可预测；批量操作报告部分失败 | 下一项稳定选中；列表不因实时插入跳焦 |
| 发起聊天 | 2（选 agent/会话→发送） | streaming 可停止；IME 不误发；离线保留草稿 | 首个 token/排队态 ≤1s 可见，失败有重试 |
| 全局搜索 | 2（打开并输入→打开结果/命令） | `Mod+K`、顶栏、`/` 共用状态；权限过滤；精确 identifier 立即顶置 | 打开规范深链；返回恢复搜索上下文 |
| 修改设置 | 2（修改→保存） | dirty 可见；服务端校验原位；冲突展示最新值与本地值 | saved 原位反馈，不用无上下文 toast |

### 9.11 导航、URL 与焦点恢复协议

- URL 拥有：工作区、资源 id、tab、保存视图、筛选、排序、搜索 query、分页游标和列表→详情的 `from`。临时 hover、popover 和未提交字段不进 URL。
- 进入详情前记录 `{route, search, hash, scrollAnchor, activeItemId}`；返回时先按稳定 id 定位，再恢复像素滚动，禁止只调用 `history.back()` 猜测来源。
- modal/drawer 关闭后聚焦原 trigger；原 trigger 已删除时聚焦同组最近可用项，再退到页面 `h1`。
- route 切换后聚焦 `main h1`，但浏览器前进/后退恢复列表时聚焦原活动项，不重复打断读屏。
- 手机列表/详情使用规范子路由或 query 表达；CSS 隐藏其中一栏不算路由化。
- 所有外链、通知和复制链接使用规范工作区深链；旧扁平路由只做 replace 兼容且保留 query/hash。

### 9.12 实时、离线与冲突呈现

| 情况 | 已有数据 | 写操作 | 可见反馈 | 恢复 |
| --- | --- | --- | --- | --- |
| reconnecting | 保留 | 幂等、可安全排队的操作允许；其余禁用 | 顶部 warning Banner + 最后同步时间 | 自动重连；不刷新整页 |
| offline | 保留并标 stale | 评论/编辑保留本地草稿；移动、删除等禁用 | danger/neutral Banner（按真实原因），禁用项说明 | online 后用户确认重试，不静默重放危险动作 |
| resyncing | 保留，只读 | 暂停依赖旧版本的写 | info Banner + 局部 refreshing | REST 对账完成后原位解除 |
| optimistic pending | 显示本地预期值 | 同对象重复写合并或排队 | 行/卡局部 pending，不用全局 spinner | 成功去 pending；失败回滚 |
| 409 conflict | 显示服务端最新值并保留本地草稿 | 阻止覆盖提交 | 原位比较“最新值/你的更改” | 重新应用或放弃；焦点回冲突字段 |
| permission revoked | 立即移除不可见数据 | 全部阻止 | 当前页 permission state，不泄漏对象详情 | 回安全父页；清 feature 缓存 |
| realtime insert | 保留用户滚动/选择 | 正常 | 用户靠近顶部/底部时原位插入；否则显示“有 N 条更新” | 用户触发后批量合入并保持活动项 |

---

## 10. 页面状态、无障碍与国际化

### 10.1 页面状态清单

每个页面 PR 必须在 Story/测试或 fixture 中覆盖：

- 初次 loading。
- 局部 refreshing。
- empty（有权限创建 / 无权限创建分开）。
- error（可重试 / 不可重试）。
- forbidden 或只读。
- offline / reconnecting / resyncing。
- stale data（仍可读，写操作受限）。
- success / optimistic pending / conflict rollback。
- 长文本、长名称、大数量、缺头像、缺可选字段。

### 10.2 可访问性

目标为 WCAG 2.2 AA：

- 增加“跳到主内容”链接和稳定的 `main` 锚点。
- 所有页面有唯一 `h1`，标题层级不跳级。
- Dialog/Drawer 焦点圈定和返回触发点。
- 颜色非唯一信号；正文/背景 ≥4.5:1，图形和控件边界 ≥3:1。
- 支持键盘完成创建、筛选、选择、评论、工作区切换和看板移动。
- 拖拽有非拖拽替代路径和 live announcement。
- 表格具有 caption/列头/排序状态；虚拟列表不破坏读屏顺序。
- 状态更新使用合适的 `status`/`alert`，不重复朗读。
- 200% zoom 与 320 CSS px 下 reflow。
- forced-colors、prefers-contrast、reduced-motion 继续纳入 CI。

### 10.3 i18n 与时区

- 所有新文案同时提交 `zh-CN` 和 `en`，不得在组件中拼接句子。
- 视觉测试至少包含一组中文和一组英文；英文按 1.4 倍长度、中文按长标题测试。
- 时间始终能查看绝对时间和时区；相对时间通过 tooltip/详情提供绝对值。
- 日期输入与显示分开：输入使用用户本地墙钟，提交遵循对应业务 Spec 的 UTC 契约。
- 表格数字、日期、百分比使用 locale formatter，不手写逗号和顺序。
- 继续使用逻辑方向属性；即使当前不提供 RTL，也不新增物理方向技术债。

---

## 11. 技术架构与迁移

### 11.1 前端分层

```text
design/
  foundations/    token、字体、reset、图标
  primitives/     Button、Input、Select、Badge、Avatar、Tooltip
  overlays/       Dialog、Drawer、Menu、Popover、Toast
  data-display/   DataTable、ListRow、Card、Timeline、ChartFrame
  feedback/       Skeleton、EmptyState、ErrorState、Status
  patterns/       PageHeader、DataView、DetailLayout、SettingsLayout
shell/
  desktop-nav/
  mobile-nav/
  topbar/
features/
  只组合 design pattern 与业务状态，不重新定义基础视觉
```

当前目录可渐进迁移，不要求一次性移动所有文件；但新组件必须遵循该依赖方向：`features → patterns → primitives → foundations`，禁止反向依赖。

规范依赖图：

```text
App providers
  ├─ auth / workspace / theme / i18n
  ├─ realtime cursor + connection
  ├─ overlay manager + toast
  └─ router
       └─ route module (lazy + error/loading boundary)
            └─ feature controller (API、权限、乐观状态、event reducer)
                 └─ design pattern
                      └─ primitive
                           └─ semantic token
```

- `design/` 不得导入 router、API client、workspace 或任一 feature。
- pattern 可接收数据、状态和回调，但不得直接请求 API 或订阅 WebSocket。
- feature 之间不得导入对方 Page/私有 store；跨域关系通过共享契约类型、规范深链或后端聚合端点。
- route module 按 PublicFlow、Workbench、Issues/Board、Conversation、Team、Automation、Settings 七个页面族拆包；初始 AppShell 不静态导入其 Page。
- 每个 route module 有自己的 Suspense skeleton 和 ErrorBoundary；全局边界只兜住 provider/shell 级异常。

状态所有权：

| 状态 | 唯一所有者 | 持久化/同步 | 禁止 |
| --- | --- | --- | --- |
| 路由、tab、filter、sort、cursor、search | URL | history + 可复制深链 | 另建 Zustand 副本 |
| auth、用户偏好、工作区选择 | 现有 Zustand store | 服务端真源 + 本地首帧镜像 | feature 复制用户/工作区对象 |
| 服务端实体 | 对应 feature resource controller | REST 首取 + WebSocket reducer + `version/updated_at` | 把整个后端缓存塞进全局 store |
| 表单与未提交草稿 | 组件/feature draft store | 必要时按 member+workspace+resource 分区本地保存 | 通过 URL 暴露敏感正文 |
| overlay 栈、焦点归还 | OverlayManager | 内存 | 每个 feature 自造 document listener |
| 连接态、频道 cursor | RealtimeProvider | session/local cursor store | 页面各自开 WebSocket |
| Toast | ToastProvider | 内存、按 id 去重 | 用 toast 替代字段错误或持续状态 |

读写与实时合并协议：

1. route loader/controller 发 REST 请求，先验证统一包络、权限和资源版本。
2. 写操作带 `Idempotency-Key`；可逆、低冲突字段可乐观更新，危险/跨资源操作等待服务端确认。
3. 每个 feature 只维护一个纯函数 event reducer；按 `seq` 去重、按 `updated_at/version` 防回退、按 `visibility` 移除失权/移出筛选对象。
4. 缺帧或过滤条件无法可靠本地重算时，按稳定 id 局部 refetch；不得整页 reload。
5. `resync_required` 经统一 REST 对账并原子替换快照；对账期间保留旧数据只读。
6. API 401 由全局 unauthorized handler 清会话并安全回登录；403/404/409 由 route/feature 原位呈现，禁止混为“网络错误”。

服务端继续采用既有 Python `API → service → data` 分层、PostgreSQL 真源和 outbox→WebSocket 投影，不因前端视觉迁移新增并行协议。前端只依赖业务 Spec 的 REST/WS 合同；若界面需要新的聚合数据，先修订对应业务 Spec 和服务层，禁止浏览器 N+1 拼接或读取数据库形状。

可测试性边界：

- primitive/pattern 以纯 props fixture 测试，不启动路由和网络。
- feature controller 用 mock API + 真实 event reducer 测 loading/empty/error/offline/conflict。
- route 测试使用 MemoryRouter 验证 deep link、返回上下文和权限。
- E2E 使用忠实 mock 合同与真实后端两层；视觉测试只使用 §13.6 固定 fixture。
- 测试通过 role/name/稳定业务 id 定位，不使用 CSS 类、DOM 层级或像素坐标驱动交互。

### 11.2 CSS 策略

- token/基础组件使用全局稳定类；业务样式使用 feature 前缀。
- 页面布局采用 Grid/Flex + container query，不用 JS 读取窗口宽度决定纯视觉布局。
- 禁止在业务 CSS 新增原始颜色。
- 新增固定尺寸必须说明属于图标、触控目标、断点还是内容约束；常规间距使用 token。
- z-index 使用 `base/sticky/dropdown/overlay/toast` 层级 token。
- 页面级 `overflow-x:hidden` 不得用来掩盖子组件溢出。

### 11.3 兼容迁移

1. 新 token 先加入，旧 token 作为别名。
2. 重做 Button/Input/Select/Icon，不改业务接口。
3. 引入 PageHeader、DataView、DetailLayout，按页面族迁移。
4. 外壳和手机导航独立上线；旧侧栏保留桌面兼容直到全部入口迁移。
5. 每个页面迁移后删除对应散落样式，不保留双实现。
6. 一个发布周期后删除旧 token 别名，并通过静态扫描阻止回归。

### 11.4 性能预算

- 字体总首屏压缩体积 SHOULD ≤180KB；CJK 按子集和按需策略加载。
- 新图标使用 SVG sprite/组件，不为每个图标发独立请求。
- 命令面板首开交互 ≤100ms；远程结果使用 skeleton，不阻塞本地命令。
- 列表超过 200 行评估虚拟化；虚拟化不得破坏焦点和读屏。
- 目标：LCP ≤2.5s、INP ≤200ms、CLS ≤0.1（生产 75 分位）。
- skeleton 尺寸必须接近真实内容，字体加载不得导致显著布局位移。

---

## 12. 实施顺序

阶段 1（本 Issue）只冻结本文，不改生产 UI。阶段 2 必须按下列顺序渐进迁移；后续页面不得绕过未完成的前置层。

| 顺序 | 交付单元 | 主要改动 | 可并行边界 | 退出条件 |
| --- | --- | --- | --- | --- |
| M0 | 基线与门禁 | 建 `design-quality-v1` fixture/manifest/case 生成器；固定 token 快照；登记现存豁免 | 仅测试与文档，可单独 PR | 当前主干全部基线可复现；没有把临时观察截图带入仓库 |
| M1 | Foundation 完成 | 原创 SVG Icon；Combobox/Popover/DataTable/Card；统一 OverlayManager；补 §7.8 全状态 | 原语可按文件并行，API 在首个 PR 冻结 | 组件状态、键盘、axe、四主题模式测试全绿；feature 尚未换肤 |
| M2 | Product patterns | Editor/Comment/Activity/CommandPalette/Board；PageHeader/DataView/Detail/Conversation/Settings | pattern 可按族并行，不接业务 API | 纯 fixture 下覆盖 §7.8；patterns 不导入 feature/router/API |
| M3 | AppShell 与路由 | 56px 顶栏、240/64 侧栏、compact 单行顶栏；route modules lazy；滚动/焦点协议 | shell 和 route 拆包可并行，合入前联调 | 全部入口桌面/手机可达；无死链、无双滚动、首包预算通过 |
| M4 | 激活路径 | 登录/注册/找回/邀请、Onboarding、工作区首页 | PublicFlow 与 Workbench 两组可并行 | 首次激活旅程 ≤§9.10 步数；所有状态/主题/视口 tuple 通过 |
| M5 | 工作核心 | Issue 列表、创建、详情、评论/活动/附件、Board | 列表/详情与 Board 在 patterns 冻结后并行 | 创建、分派、评论、三输入方式移动、冲突/离线 E2E 通过 |
| M6 | 协作入口 | 成员/邀请、收件箱、聊天、搜索/命令面板 | 四页面族可并行，共享 Conversation/Combobox 不得 fork | 列表↔详情返回、实时插入、草稿和权限矩阵通过 |
| M7 | 设置与平台页 | 账号/工作区设置、项目、周期、Skills、Squads、Runtimes、执行、自动值守、集成、洞察 | 低耦合页面族并行，复用前述 patterns | 所有生产路由采用页面模板；设置 dirty/conflict 与管理权限通过 |
| M8 | 清理与冻结 | 删除旧 token alias、重复组件/样式、字符图标和过期截图；生成最终矩阵 | 只能在所有消费者迁完后开始 | §13 全绿；静态扫描零未登记豁免；阶段 3 获得固定验收输入 |

每个交付单元：

1. 先加新实现和适配器，业务行为测试保持绿色；
2. 按一个页面族迁移并在同 PR 删除该族旧视觉实现；
3. 禁止全局 feature flag 长期保留双 UI；若必须灰度，开关最长一个发布周期并记录删除日期；
4. 任何 token 变化单独 PR，附 light/dark 对比度与全组件视觉 diff；
5. 一个单元未达到退出条件，不得晋级依赖它的下一单元。

---

## 13. 验收标准

### 13.1 全局

- [ ] 当前 51 个路由节点及其公开/权限状态有可达性测试；新增路由自动进入 manifest，不以固定计数逃逸。
- [ ] `/skills`、市场和详情路由真实刷新可达。
- [ ] 顶栏搜索输入、回车、鼠标点击和快捷键进入同一结果系统。
- [ ] 桌面导航分组明确，中文无两个同名“自动化”。
- [ ] 320px、390px、768px、1024px、1440px 无页面级横向溢出。
- [ ] 320px 手机可完成登录、创建 issue、移动看板卡片、评论、切工作区和聊天。
- [ ] 亮暗主题均为独立校准的 surface/边界/状态/图表体系。
- [ ] 页面只有一个主标题和一个主要 CTA。

### 13.2 令牌与组件

- [ ] 色彩、字体、间距、圆角、阴影、动效、z-index 均令牌化。
- [ ] 业务 CSS 无新增原始颜色；散落间距显著下降并有静态门禁。
- [ ] Button、字段、Select、菜单、Dialog、Drawer 覆盖完整状态矩阵。
- [ ] 导航和操作图标不再使用 emoji/字符；回应 emoji 例外。
- [ ] 状态、优先级和 AI 身份均不只靠颜色。
- [ ] 触控目标 ≥44×44px。

### 13.3 页面与状态

- [ ] 登录/注册/MFA、首页、项目、issue、看板、评论、成员、Skills、Squads、收件箱、聊天、Runtimes、自动值守、Integrations、Analytics、设置全部完成页面级审查项。
- [ ] 每页覆盖 loading、refreshing、empty、error、permission、offline/stale 和长内容。
- [ ] ErrorState 告知影响和恢复动作；失败不丢用户输入。
- [ ] skeleton 与最终布局同形，局部刷新不清空已有内容。
- [ ] destructive action 有明确确认或撤销。

### 13.4 无障碍与国际化

- [ ] 键盘可完成全部关键流程，看板有非拖拽路径。
- [ ] skip link、焦点圈定、焦点恢复、唯一 h1、live region 通过自动化与人工检查。
- [ ] light/dark 正文对比 ≥4.5:1，图形/边界 ≥3:1。
- [ ] forced-colors、prefers-contrast、reduced-motion 测试通过。
- [ ] 中英文目录同步；长英文、长中文、时区、相对/绝对时间均有视觉用例。
- [ ] 200% zoom 与 320 CSS px 下内容 reflow。

### 13.5 视觉与交互门禁

矩阵不是抽样清单，而是 case 生成规则。页面行的
`视口 × 主题 × 状态 × 交互`
取笛卡尔积；每个 tuple 都必须有功能断言，造成不同可见终态的 tuple 还必须有截图断言。

视口：

- `D`：1440×900，DPR 1，鼠标/键盘。
- `W`：1024×768，DPR 1，鼠标/键盘；覆盖 wide 断点下界。
- `T`：768×1024，DPR 1，鼠标/键盘/触控。
- `M`：390×844，DPR 2，触控/外接键盘。
- `N`：320×800，DPR 1，触控/外接键盘；同时在 200% zoom 下跑 reflow。

主题为 `L=light`、`K=dark`。交互集按视口固定：

- `D/W={P,K,R,N}`：pointer、keyboard、realtime、navigation/return。
- `T={P,K,C,R,N}`：增加 touch。
- `M/N={C,K,R,N}`。
- PublicFlow 没有业务实时流时移除 `R`，但必须保留 offline；纯展示状态的 `P/K/C` 断言为“无伪交互、Tab 顺序稳定”。

状态代码：

| 代码 | 含义 |
| --- | --- |
| `ready` | 正常、有数据、可操作 |
| `loading` | 首次加载，同形 skeleton |
| `refresh` | 已有数据局部刷新 |
| `empty` | 有权限但无数据 |
| `readonly` | 无创建/写权限或权限刚撤销 |
| `forbidden` | 无页面/对象读取权限；不泄漏对象是否存在 |
| `error` | 可恢复请求失败 |
| `offline` | 断网且保留 stale data/draft |
| `conflict` | 409 或实时版本冲突 |
| `long` | 长中文、1.4× 英文、长标识、大数量、缺头像 |
| `pending` | 乐观写、上传、发送或运行中 |

完整页面矩阵：

| Page code | 页面/模式 | 视口 | 主题 | 状态集合 |
| --- | --- | --- | --- | --- |
| `auth` | 登录/注册/找回/重置 | D,W,T,M,N | L,K | ready,loading,error,offline,pending,long |
| `onboarding` | 首次引导 | D,W,T,M,N | L,K | ready,loading,error,offline,pending,long |
| `home` | 工作台/工作区首页 | D,W,T,M,N | L,K | ready,loading,refresh,empty,readonly,forbidden,error,offline,long |
| `issue-list` | Issue DataView | D,W,T,M,N | L,K | ready,loading,refresh,empty,readonly,forbidden,error,offline,pending,long |
| `issue-create` | 创建浮层 | D,W,T,M,N | L,K | ready,loading,readonly,forbidden,error,offline,pending,long |
| `issue-detail` | Issue 详情/属性 | D,W,T,M,N | L,K | ready,loading,refresh,readonly,forbidden,error,offline,conflict,pending,long |
| `comment-activity` | 评论/活动/附件 | D,W,T,M,N | L,K | ready,loading,empty,readonly,forbidden,error,offline,conflict,pending,long |
| `board` | 看板/保存视图 | D,W,T,M,N | L,K | ready,loading,refresh,empty,readonly,forbidden,error,offline,conflict,pending,long |
| `members` | 成员名册/管理 | D,W,T,M,N | L,K | ready,loading,refresh,empty,readonly,forbidden,error,offline,pending,long |
| `invite` | 邀请创建/接受 | D,W,T,M,N | L,K | ready,loading,readonly,forbidden,error,offline,pending,long |
| `inbox` | 收件箱列表/详情 | D,W,T,M,N | L,K | ready,loading,refresh,empty,forbidden,error,offline,pending,long |
| `chat` | 会话列表/消息/输入 | D,W,T,M,N | L,K | ready,loading,empty,readonly,forbidden,error,offline,pending,long |
| `palette` | 搜索/命令面板 | D,W,T,M,N | L,K | ready,loading,empty,readonly,forbidden,error,offline,pending,long |
| `settings` | 账号/工作区设置 | D,W,T,M,N | L,K | ready,loading,readonly,forbidden,error,offline,conflict,pending,long |

平台页面（项目、周期、Skills、Squads、Runtimes、执行、自动值守、集成、洞察）按其页面模板继承上表：DataView 用 `issue-list` 状态集，Detail 用 `issue-detail`，Conversation 用 `inbox`，Settings 用 `settings`；每条生产路由必须在 manifest 中映射到且仅映射到一个 page code。

每个 tuple 的自动断言：

- 页面只有一个 `h1`，焦点/Tab 顺序和主操作符合 §9。
- 没有页面级横向溢出；显式 Board/DataTable 内滚动容器有名称和边缘提示。
- light/dark 无硬编码色回退，对比度达标。
- 状态文字、图标和恢复动作完整，输入在 error/offline/conflict 下不丢。
- pointer/keyboard/touch 到达同一业务终态；realtime 不抢焦点/滚动；return 恢复 URL 和稳定对象。

### 13.6 固定浏览器、字体与数据夹具

视觉基线只在仓库提供的 Linux 容器中生成：

| 项 | 固定值 |
| --- | --- |
| OS/架构 | Ubuntu 24.04 LTS x86_64 容器；禁止本机直接更新基线 |
| 浏览器 | `package-lock.json` 对应 Playwright bundled Chromium；运行时 `browser.version()` 必须与 baseline manifest 完全相等 |
| 色彩 | sRGB、无 HDR、默认 contrast；forced-colors/prefers-contrast 在独立功能用例跑 |
| locale/timezone | 主矩阵 `zh-CN` / `Asia/Shanghai`；扩张用例 `en-US` / `America/Los_Angeles` |
| 字体 | 仓库自托管 Manrope 600、Inter 400/500/600、Noto Sans SC 400/500/600、JetBrains Mono 400/500；`document.fonts.ready` 后截图 |
| 时钟 | `2026-07-30T12:00:00+08:00`；relative time、cron、日期输入均冻结 |
| 网络 | 默认零延迟；loading/pending 由可控 deferred response；offline 用 BrowserContext 切换 |
| 动画 | 截图用例注入 reduced motion 并等待两个 animation frame；§5.6 的时长/轨迹由独立非截图用例验证 |
| 光标/选择 | 隐藏 caret，清除文本 selection；不得 mask 产品内容或整块区域 |

`design-quality-v1` 数据夹具必须确定性生成：

- 工作区“星桥实验室”；owner“林然”、member“顾一”、agent“构建助手”、squad“发布小队”。
- 项目“控制台重构”；Issue `MESH-101` 至 `MESH-112`，覆盖 7 个状态类别、5 个优先级、无负责人、人类、agent 和 squad。
- 看板 5 个可见列：Backlog 2、Todo 3、In Progress 2（WIP=2）、In Review 1、Done 2；另有一个 blocked 和一个跨项目移动 case。
- 详情含 3 段 Markdown、父子项、依赖、4 个属性、根评论/回复/系统活动/agent 运行各至少一条，以及 clean/scanning/failed 三种附件。
- 收件箱含未读、已读、归档、提及、分派、运行失败各一项；同 Issue 的更新可聚合。
- 聊天含普通消息、长代码块、上传、streaming、waiting、failed；每个会话有稳定 id。
- `long` fixture 含 80 个 CJK 字符标题、180 个 Latin 字符名称、不可断长标识、9999+ 计数、无头像和空可选字段。
- `readonly` 使用 guest；`conflict` 返回新 `version`；所有 UUID、时间、排序和相对时间固定。

fixture schema 变更必须提升 `fixture_version`；基线 manifest 记录 seed、schema version 和内容 hash。

### 13.7 截图命名、diff 与更新纪律

文件名：

```text
dqv1__{page-code}__{viewport}__{theme}__{state}__{interaction}__{locale}.png
```

示例：

```text
dqv1__issue-detail__M__K__conflict__C__zh-CN.png
```

- 字段只用上文代码；禁止 `final/new/fixed/2` 等非稳定后缀。
- 同一容器、浏览器、字体 hash、fixture 和 DPR 下 `maxDiffPixels=0`；环境任一项不匹配时测试必须 fail-fast，不得放宽阈值。
- 截图前等待网络 idle 不是充分条件；必须等待页面专用 `data-visual-ready`、字体就绪和两个 animation frame。
- baseline 更新独立 PR，附 manifest diff 和变更原因；不得在修功能的同一提交中批量接受未知视觉变化。
- 仓库基线只包含 Mesh 原创界面和 fixture，不得放入任何观察输入、外部品牌、URL 或素材。

自动化总门禁：

- [ ] token 生成幂等、亮暗键集合一致。
- [ ] 对比度、硬编码颜色、原始 z-index/断点/间距静态检查。
- [ ] §7.8 全组件状态、键盘、axe 与 forced-colors 测试。
- [ ] 全生产路由可达且映射 page code。
- [ ] §13.5 case 生成结果无缺口，视觉 diff 为零。
- [ ] 自动无障碍扫描 + 关键流程键盘/触控 E2E。
- [ ] 320px/200% zoom overflow 检查（显式横向滚动容器除外）。
- [ ] `prefers-reduced-motion`、`prefers-contrast`、forced-colors、打印模式通过。

---

## 14. 最终决策

1. 保留现有 React、主题协商、i18n、实时和业务组件逻辑，采用渐进式设计系统迁移。
2. 已落地的可达性、手机外壳和基础原语作为不可退化基线；阶段 2 从复合组件和页面模式开始。
3. 统一搜索入口，不维护“顶栏搜索”和“命令面板搜索”两套状态。
4. 采用分组桌面侧栏 + 手机底栏/抽屉；禁止隐藏导航后无替代入口。
5. Issue 详情采用“主内容 + 属性栏/抽屉”，看板手机采用单泳道模式。
6. 主题基础继续由 `theme.md` 拥有；本 Spec 扩展层级和组件语义，不复制主题协商协议。
7. URL 是可分享页面状态的真源；服务端实体由 feature controller 管理；实时只经一个连接层和纯 event reducer 合并。
8. 视觉验收以 §13 的原创 fixture 和固定环境为唯一仓库基线，外部观察截图永不进入仓库。

### 14.1 技术架构评审

结论：**架构评审通过**。

- 分层明确为 route module → feature controller → pattern → primitive → token，依赖方向可由 lint/边界测试验证。
- 保留既有 Python API/service/data、REST 包络、OCC、outbox/WebSocket 与 i18n/theme 真源，不以视觉重构制造第二套协议。
- URL、服务端实体、草稿、overlay、实时 cursor 的所有权唯一，消除了多 store 双写和页面各自订阅的风险。
- M0–M8 允许逐页迁移、逐页删旧实现；组件 API 先冻结，页面族再并行，降低大分支和 CSS 互相覆盖风险。
- route lazy、局部边界、性能预算、fixture 分层和纯 reducer 使单元、组件、真实 E2E、视觉回归均可稳定测试。

非阻断实施提醒：M1 首个 PR 必须先冻结 OverlayManager、Combobox 和 pattern props；任何需要新聚合接口的页面先修订业务 Spec，不允许前端 N+1 补洞。

### 14.2 UX 评审

结论：**UX 评审通过**。

- 登录→激活→创建→分派→回评形成有步骤预算的闭环，每一页只有一个主 CTA 和明确返回路径。
- 桌面、平板、390px、320px、亮暗主题、loading/empty/error/permission/offline/conflict/长内容均进入可生成矩阵，不再依赖“看起来接近”的主观判断。
- Issue、Board、Comment、Inbox、Chat、Search 的高频路径同时定义 pointer、keyboard、touch 和 realtime 行为；移动端不是缩窄桌面版。
- 焦点恢复、IME、dirty draft、冲突、WIP、失权和断线都有保值/恢复策略，避免用户输入丢失和静默失败。
- 信息密度、表面层级、字阶、44px 触控目标、颜色非唯一信号和 WCAG 2.2 AA 已形成统一合同。

本 Spec 已覆盖阶段 1 的全部交付物并完成技术架构/UX 双评审，**允许进入阶段 2 原创实现；阶段 3 验收仍由专责角色执行**。
