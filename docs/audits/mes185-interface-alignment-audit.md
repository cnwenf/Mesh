# MES-185 界面精确收口审计（含 MES-187 业务深度增量）

> 日期：2026-08-05
> 范围：Web 前端全部既有页面；MES-187 增量复核动态看板关联轴、issue 属性、标签、成员抽屉、项目 key 与账号头像清空。
> 方法：授权运行态黑盒测量 + Mesh Spec 对账 + 单元/组件测试 + 生产构建浏览器 E2E + 固定视口视觉 diff。

## 1. 边界与独立性

本轮只观察授权运行态中可见的布局、排版、间距、尺寸、响应式和主题行为；未读取、复制或引入任何外部源码、样式表、网络字体、图标或品牌资产。临时对照截图只用于本机人工审查，不进入仓库、PR 或交付附件。业务模型、权限、路由和实时协议继续以 Mesh Spec 为唯一真源。

## 2. 黑盒测量结论

| 页面/区域 | 观测值                                                                                          | Mesh 冻结值与处理                                                            |
| --------- | ----------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 公开流程  | 内容框 `384px`；卡片圆角约 `14px`、内边距 `16px`；区块间距 `16px`；标题 `24/32px`               | 新增 `--content-public-flow` 和公开流程标题 token；桌面与手机共用同一上限    |
| 应用外壳  | 桌面 rail `256px`；主内容距画布 `8px`；主框圆角 `14px`                                          | 沿用 MES-130 外壳基线，无结构分叉                                            |
| 看板      | 列宽 `280px`、列距 `16px`、列圆角 `14px`；普通卡宽约 `256px`、高度约 `140–167px`、内边距 `12px` | 视图导轨横排；普通卡补齐描述/项目/估算/截止/负责人/日期；虚拟化卡保留 `72px` |
| 账号设置  | 二级导航 `224px`；内容列 `704px`；section 为紧凑左右字段行                                      | 桌面双列、内容居中；compact 转顶部横向导航和单列字段                         |
| 手机      | 几何与桌面层级一致，内容自然收缩；设置导航可水平滚动                                            | 继续使用 compact 顶栏/底栏/抽屉，禁止页面级横向溢出                          |
| 亮暗主题  | 几何一致，仅语义表面、文字、边界和阴影替换                                                      | 不建立主题专属 DOM 或尺寸分叉                                                |

授权运行态中没有可用的工作区首页样本路由，因此工作区首页严格服从 Mesh Workbench、workspace 与权限 Spec，不虚构对照行为。

## 3. 本轮实现

| 切面                        | 结果                                                                                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 公开流程                    | 登录、注册及恢复外壳统一为 `384px` 紧凑节奏，修正标题字阶、卡片留白和手机边距                                                                                |
| 设置                        | 新增 `/settings/profile` 默认入口；昵称与 HTTPS 头像地址写入既有 `/users/me`；时区留在外观设置；无 `bio` 平行字段                                            |
| 设置密度                    | 外观/语言/时区合并为紧凑行，Select 的 hint/error 都由 `aria-describedby` 关联                                                                                |
| 看板                        | 横向视图导轨、固定列宽、丰富信息卡与两行标题；`card_fields` 控制当前投影已提供的六类元数据，日期、数值和优先级按 locale/用户时区呈现，虚拟列表性能契约不变   |
| 顶栏                        | 用户菜单补齐个人设置、light/dark/system、system 实时解析值、快捷键帮助和退出                                                                                 |
| 时间                        | 评论使用共享 `RelativeTime`，每 `30s` 自动刷新，tooltip 同时显示用户时区绝对时间和 UTC 原值                                                                  |
| 工作区首页                  | 最近项目、issue、收件箱、执行四张真实 API 卡；每张独立 loading/empty/error/ready，并提供规范深链                                                             |
| 看板关联轴(MES-187)         | label / 自定义字段进入主副轴，双多值轴形成笛卡尔 placement；标量轴可 move，多值轴已有卡片只读但 quick-create 原子写关联；关联事件按 placement 集合差增量收敛 |
| Issue / 标签(MES-187)       | Agent 分派提示与同值 no-op、严格状态阻断、必填字段具名提示、409 服务端快照收敛、标签合并和紧凑色点 `+N` 已落地                                               |
| 成员 / 项目 / 资料(MES-187) | human/Agent 名册运行态与真实详情抽屉、永久注册表 key 检查、删除披露、项目冲突收敛及 `avatar_url:null` 恢复默认头像已落地                                     |

## 4. 逐项检查表处置

前端对齐核对表的审计全集仍为 **142 条断言**。MES-187 在 MES-185 基线的 retained 集合中关闭 10 条、MES-188 再关闭评论/agent/runtime 9 条、MES-189 关闭剩余 18 行（17 条功能，`L541–L543` 合 1 条）后，当前处置为：

- **110 条通过**：MES-185 的 73 条基线，加上 MES-187 关闭的 10 条业务深度断言、MES-188 关闭的 9 条评论/agent/runtime 断言与 MES-189 关闭的 18 条 URL 状态/离线/通知/收藏/批量/Presence/小队/引导/导入导出断言。
- **0 条保留产品差异**：retained 集合已全部按对应功能 Spec 独立切片闭合（证据见 §4.1 MES-189 段）；`L373` 仍是明确的部分实现，不属 retained 集合，见 §4.2 注记。
- **32 条环境边界或可选增强**：依赖目标操作系统读屏、真实软键盘、多客户端/外部平台、专门性能环境，或 Spec 明确标为可选。

检查表中的 `[x]` 表示“已逐项审计并完成处置”，实际是否已实现以条目状态和本审计分类为准。以下编号指 MES-187 收口后的当前检查表行号，便于逐项反查；本次仅替换条目文本，没有在对应区段前插入行。

### 4.1 已验证通过（110）

`L90, L91, L103, L104, L106, L117, L119, L142, L167–L170, L172, L195, L204, L212, L214, L220, L240–L241, L274, L283–L285, L299, L306, L316–L317, L319, L321–L324, L333, L336, L350–L351, L353–L355, L357, L359, L366, L379, L387–L388, L391–L393, L402, L406, L407, L408, L410, L420–L421, L427–L428, L434, L438, L440–L443, L455, L456–L458, L466–L471, L483–L485, L487, L499, L501, L514, L526–L528, L530, L540, L552–L553, L581–L584`。

MES-185 直接闭合 `L142`（跨时区 tooltip）、`L240`（相对时间自动刷新）、`L283–L284`（顶栏用户菜单）、`L299`（工作区首页密度）和 `L322`（个人资料）。`L322` 中旧文案提到的 `bio` 与 canonical `users` 模型冲突，按 `member.md` 修正为昵称、头像与时区，不新增字段；MES-187 进一步验证显式 `avatar_url:null` 清空。

MES-187 从 retained 集合移入通过的恰为 `L316, L317, L336, L350, L354, L366, L379, L388, L391, L392`。`L355` 原已计入 MES-185 的 73 条，本次只把检查表状态和 409 收敛证据修正为真实现状，不重复计数。

MES-188（PR #136）从 retained 集合移入通过的为 `L402, L407, L440–L443, L456–L458` 共 9 条评论线程状态机、agent 运行生命周期与 runtime/执行审计断言。证据锚点：`docs/evidence/mes-188/real-stack-contract.json`（乐观重试落库、幂等键、tombstone、四态 runtime、双 attempt 审批续跑、输出评审批准/打回各 1 次、agent 名册移除）与 `docs/evidence/mes-188/{desktop,phone}-{light,dark}-*.png`（comments-and-issue-executions / board-processing / runtime-degraded / attempt-audit / agent-capacity 四组合存证）；真栈旅程见 `frontend/e2e/real-mes188*` 与 `backend/tests/e2e/`。真 LLM provider 旅程（`mes188_real_llm_e2e`）因上游 token-plan 配额 429 暂缓，未计入本 9 条，配额恢复后补跑存证。

MES-189（批次③切片 2）从 retained 集合移入通过的为 `L92, L93, L182, L186, L202, L206, L207, L222, L242, L247, L251, L252, L480, L486, L513, L541–L543` 共 18 行（17 条）：URL 状态同步与标签页标题/未读 favicon、离线乐观队列与专项恢复五入口、通知聚合归档视图与行内联审批、邮件通道（locale 渲染 + 一次性 token 标已读）、收藏入口、脏态保护扩展、issue/技能/成员批量操作、成员在线与看板谁在查看、429/Deprecation 契约提示、小队消息着色与导出、键盘入口可发现性、导入导出 UI（行级进度/413 前置预警/项目与视图 ⋯ 情境入口）。证据锚点：`docs/evidence/mes-189/real-stack-contract.json`（内联审批落库、通知自动归档、邮件 token 标已读、小队导出、技能 bulk 绑定、assign private-agent 护栏拦截）与 `docs/evidence/mes-189/{desktop,phone}-{light,dark}-*.png` 四组合存证；随批债务（TD-1/2/3/5、DEBT-2）与后端增量（B1–B6）见 `docs/superpowers/plans/PROGRESS-mes189-slice2.md` 已完成区。

| MES-187 能力                             | 真实实现与测试证据                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 动态 label/自定义字段轴、二维笛卡尔投影  | `backend/src/mesh/views/config.py`、`backend/src/mesh/views/projection.py`；`backend/tests/unit/test_view_projection_service.py::test_execute_view_group_by_label_projects_each_value_and_empty_group`、`backend/tests/unit/test_view_projection_service.py::test_custom_axis_and_sort_enforce_view_project_scope`、`backend/tests/unit/test_view_projection_service.py::test_execute_view_two_multi_value_axes_form_cartesian_cells`                                                                                                                                                     |
| 标量 move、多值轴只读与原子 quick-create | `backend/src/mesh/views/moves.py`；`backend/tests/unit/test_view_moves.py::test_move_group_by_status`、`::test_single_select_primary_axis_move_writes_eav_position_and_realtime`、`::test_label_axis_move_and_reorder_are_read_only`、`::test_multi_value_axes_quick_create_writes_both_associations_atomically`                                                                                                                                                                                                                                                                          |
| placement 集合差实时收敛与动态列         | `frontend/src/features/board/boardRealtime.ts`、`frontend/src/features/board/BoardPage.tsx`；`frontend/src/features/board/__tests__/boardRealtime.test.ts`（label 集合差/双多值笛卡尔 placement）与 `frontend/src/features/board/__tests__/BoardPage.realtime.test.tsx`（label/custom field 增量且不整板 refetch）                                                                                                                                                                                                                                                                        |
| Agent 分派、严格状态、必填与 409         | `frontend/src/features/issues/IssueProperties.tsx`、`frontend/src/features/issues/IssueDetailPage.tsx`；`frontend/src/features/issues/__tests__/IssueProperties.test.tsx` 与 `frontend/src/features/issues/__tests__/IssueDetailPage.test.tsx` 覆盖同 assignee no-op、严格目标、`required_field_missing`、回滚和 409 服务端快照收敛                                                                                                                                                                                                                                                       |
| 标签合并与色点溢出                       | `frontend/src/features/labels/LabelsPanel.tsx`、`frontend/src/features/labels/LabelDots.tsx`；`frontend/src/features/labels/__tests__/LabelsPanel.test.tsx` 与 `frontend/src/features/labels/__tests__/LabelDots.test.tsx::renders compact data-colour dots and a +N overflow counter`                                                                                                                                                                                                                                                                                                    |
| 永久项目 key、成员抽屉与头像清空         | `backend/src/mesh/project/routes.py`、`backend/src/mesh/project/service.py`，`frontend/src/features/projects/CreateProjectDialog.tsx`、`frontend/src/features/members/MembersPage.tsx`、`frontend/src/shell/pages/settings/ProfileSettingsSection.tsx`；对应 `backend/tests/unit/test_project_api.py::test_project_key_availability_endpoint`、`backend/tests/unit/test_project_service.py::test_project_key_availability_uses_permanent_prefix_registry`、`frontend/src/features/members/__tests__/MembersPage.test.tsx`、`frontend/src/shell/__tests__/ProfileSettingsSection.test.tsx` |

### 4.2 保留产品差异（0）

MES-189 按对应功能 Spec 独立切片闭合了全部 18 行保留差异（`L92, L93, L182, L186, L202, L206, L207, L222, L242, L247, L251, L252, L480, L486, L513, L541–L543`），逐项证据见 §4.1 MES-189 段与 `docs/evidence/mes-189/`；本表清空，retained 集合归零。

`L373` 仍是明确的部分实现，不随上述 18 行转为完整通过（也不计入 retained 集合）：卡片投影已覆盖描述、项目、估算、截止日、负责人姓名、更新时间和标签；子任务进度与 assignee 人/Agent 类型头像仍未进入卡片投影，虚拟卡也按固定高度性能契约隐藏扩展元数据。动态分组列 `L379` 已由真实 skeleton 增量闭合，两项不得混用。

### 4.3 环境边界与可选增强（32）

| 入口行                                                                               | 分类               | 原因                                                                                               |
| ------------------------------------------------------------------------------------ | ------------------ | -------------------------------------------------------------------------------------------------- |
| `L132, L171, L216, L239`                                                             | 目标设备人工项     | 真实手机软键盘、逐键鼠标等价路径、NVDA/VoiceOver 与跨页面 hover 人工签署需要目标设备               |
| `L203, L259, L377, L409, L522, L529, L556, L568`                                     | 多客户端/外部/性能 | 需要双客户端 P95、抓包、1000 卡性能、外部 OAuth/IM、跨时区数据集或会话撤销环境                     |
| `L308, L338, L394, L445, L459, L472, L488, L502, L515, L531, L544, L557, L569, L586` | 深层状态矩阵       | 当前四组合覆盖页面代表态；全部 Tab、庆祝态、外部回调和手机灯箱手势仍需专项矩阵                     |
| `L248, L250, L307, L358, L376, L378`                                                 | Spec 可选          | 短时撤销、桌面通知、域名自动加入、完整 URL 引用、Timeline/Table 与 viewer presence 明确为可选/延期 |

## 5. 视觉审查矩阵

本轮人工检查登录、看板、设置、工作区首页的桌面/手机 × light/dark 代表态，并复核受共享外壳影响的正常态和异常态 actual/expected/diff。固定视觉项目继续覆盖 `390×844`、`768×1024`、`1024×768`、`1440×900` 四视口和双主题。只有确认差异来自上述有意设计变更后才更新期望图；对照环境截图不进入仓库。

## 6. 安全与可访问性结论

- 个人头像只接受 `https:`，`javascript:`、`data:`、明文 HTTP 与非法 URL 在请求前拒绝；服务端仍为权威校验。
- 退出先尽力撤销服务端会话，无论网络结果都清除本地 access token 并 replace 到登录页。
- Select hint/error、设置导航、活动卡、用户菜单和相对时间均保留可访问名称/描述与键盘路径。
- 颜色不作唯一信号；亮暗主题共用 DOM，继续由对比度、forced-colors、reduced-motion 和响应式门禁守护。
- 目标操作系统上的 NVDA/VoiceOver 人工签署仍遵循 `mes128-screen-reader-runbook.md`，自动化不能冒充人工结果。

## 7. 可复现验证

以下数字是 MES-185 已完成复核的既有记录，不据此声称 MES-187 新增真栈套件已经通过：

- 格式门禁通过（保留 295 个历史债务路径）；ESLint/Stylelint、类型检查和生产构建通过，ESLint 为 0 error、25 个既有 warning。
- 全量 Vitest 的 420 个测试文件全部通过；Statements/Lines `98.90%`、Functions `97.99%`、Branches `94.30%`。本轮 21 个变更 TS/TSX 文件逐文件覆盖率均不低于 `90%`。
- 真实浏览器功能套件 96 项：88 passed、8 skipped；axe 无障碍套件 152 项：142 passed、10 skipped；视觉套件 476 项：432 passed、44 skipped。跳过项均为套件按浏览器项目、视口或前置能力显式分流，不是失败降级。
- production-auth 真栈回归 1 项通过，实际启动服务并验证 API、会话与数据库路径。
- 响应式、无障碍静态契约、遗留 token、76 组双主题对比度、应用契约与真实服务契约门禁全部通过。
- 证据校验通过：412 张截图 SHA-256 全部唯一；检查矩阵 112/112 verified、0 N/A、0 gaps。

MES-187 另提供可复现的 production-auth 真栈入口 `./frontend/e2e/mes187-real/run-e2e.sh`（等价 npm 脚本：`npm --prefix frontend run test:e2e:mes187`）。runner、环境生成器、Playwright 配置与业务旅程分别位于 `frontend/e2e/mes187-real/run-e2e.sh`、`frontend/e2e/mes187-real/gen-stack-env.sh`、`frontend/playwright.mes187.config.ts`、`frontend/e2e/real-mes187-business-depth.spec.ts`；其覆盖真实 API/worker/PostgreSQL/Redis、desktop/phone × light/dark 浏览器操作及最终数据库断言。本轮已实际执行并通过 4 个 Playwright 项目，产出 12 张 desktop/phone × light/dark 业务证据图，退出时完整清理容器与数据卷。
