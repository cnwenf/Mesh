# MES-128 响应式、可访问性与门禁收尾实施计划

> 状态：验收整改执行中
> 基线：PR #118 重放到 `origin/main` 后的提交树
> 权威范围：`design-quality.md` §8、§10、§13，逐页验收清单与 MES-128 工单

## 1. 完成定义

本轮只有在下列条件同时成立时才重新提验：

1. `origin/main...HEAD` 的每个新增或变更前端源码文件均进入覆盖率门禁，且
   statements、branches、functions、lines 四项逐文件均不低于 90%。
2. 44px 浏览器门禁覆盖原生控件、链接、ARIA/custom interactive、`summary`、
   checkbox/radio，并按 WCAG 2.2 target-size/spacing 规则给出可解释判定。
3. 两档手机视口（320 与 390 CSS px）均用真实浏览器、真实 API、真实 PostgreSQL
   完成登录、创建 issue、非拖拽移卡、评论、切工作区、搜索；每一步同时断言响应和落库。
4. 13 个核心页的 loading、refreshing、empty、error、permission、offline/stale、
   long/large/missing 状态有可复现证据；虚拟看板有不会跳过未挂载卡片的读屏模式。
5. `design-quality.md` §13.1–§13.5、逐页清单和 G1–G17 均以当前代码和实测证据
   重新判定，不保留“后续处理”作为本工单结论。
6. 全量质量、真实浏览器、视觉、无障碍、构建和安全门禁在精确新 head 上通过；
   PR 与 MES-128 可见关联、基于最新 main、转 Ready，随后请求独立验收与安全评审。

## 2. 工作流与可复验证据

### 2.1 writing-plans

- 先读取工单、验收线程、GitHub reviewThreads、Spec 与现有审计，按阻断簇拆解本计划。
- 每个任务写明目标文件、RED 条件、GREEN 条件、验证命令和证据落点。
- 不用聚合覆盖率、mock 正常态截图或“已登记缺口”替代逐项完成定义。

### 2.2 test-driven-development

所有行为修复都按下面顺序执行并记录命令输出：

1. **RED**：先新增门禁/测试，使现有缺口稳定失败。
2. **最小 GREEN**：只实现让该行为通过的最小变更。
3. **REFACTOR**：在目标测试保持绿色时去重和收紧类型。
4. **回归**：跑所属模块、逐文件覆盖率和全量套件。

重点 RED：

- 变更源码发现器必须列出白名单外文件，并对任一四指标 `<90` 返回非零。
- 44px fixture 中的链接、`role=button`、`summary`、checkbox/radio 和自定义控件必须失败。
- 虚拟列 250 项时，读屏模式必须能按 DOM 顺序读取第 1 至 250 项。
- paused/disabled agent 的 `agent.trigger_skipped` 帧必须产生具名、可关闭的提示。
- feature flag 为 `false` 时对应导航、命令和路由内容必须不可用；缺省键保持兼容启用。
- Board/Issue 上下文快捷键必须只在对应页面激活，输入/IME 中不得误触发。

### 2.3 systematic-debugging

真栈流程失败时固定采集：浏览器 console/network、API 状态与响应 envelope、服务日志、
PostgreSQL 相关行、当前 URL/焦点/aria-live 文本。先定位最小失败边界，再修改产品代码；
禁止通过重试、延时扩大或 mock 返回绕过失败。

### 2.4 verification-before-completion

提交前从干净依赖和新鲜服务执行：

- 前端全量 coverage、逐变更文件 coverage、lint、stylelint、typecheck、build。
- 默认 e2e、a11y、visual，以及 MES-128 真栈两视口六流程与状态矩阵。
- 证据 manifest/md5、对比度、响应式、无障碍结构、token 幂等与债务门禁。
- 后端受影响 UT/e2e、数据库迁移检查、Compose 隔离与凭据/端口安全检查。
- Git diff、提交身份、co-author、PR exact-head checks 与 main 同步状态。

### 2.5 requesting-code-review

- 先做一次按六个阻断簇的自审，列出已解决/不适用/仍阻断项。
- 请求独立 reviewer 复查逐文件覆盖率表、真栈 DB 断言、读屏/状态矩阵与两条 inline 线程。
- 评审未通过前保持 Draft；所有阻断清零且全量门禁绿后才转 Ready 并重新提验。

## 3. 实施任务

### A. 基线与交付状态

- [x] 读取工单触发线程和 GitHub review/thread 状态。
- [x] 安装 Superpowers 工作流并把五项阶段写入本计划。
- [x] `git fetch origin main` 后将 PR 两个提交 rebase 到 `origin/main`。
- [x] 完工前再次 fetch/rebase，确认相对 `origin/main` 为 0 behind；更新 PR title/body，
      以 `Closes MES-128` 建立可见关联意图。
- [ ] 全绿后转 Ready，请求独立 review；不自行合并或发布。

### B. 逐文件覆盖率

目标文件：coverage 配置/脚本、15 个验收点名页面及其测试。

- [x] RED：从 `git diff --name-only origin/main...HEAD` 自动发现全部 TS/TSX 产品源码，
      排除测试、声明、生成文件和入口胶水的规则必须集中且有单测。
- [x] RED：输出每个变更源码四指标表，任一项低于 90% 时列未覆盖行/分支并失败。
- [x] GREEN：为 15 个低覆盖组件补用户可见分支测试；禁止 istanbul ignore 掩盖。
- [x] GREEN：门禁在 CI 使用 PR base SHA，合并/本地场景有明确 fallback。

验证：目标 Vitest → `npm run test:coverage` → 变更逐文件校验脚本。

### C. 44px 全交互目标门禁

目标文件：`frontend/e2e/a11y/mes128-axe.spec.ts` 及独立 helper/fixture 测试。

- [x] RED fixture 覆盖 `a[href]`、button/input/select/textarea、`summary`、
      `[role=button|link|menuitem|tab|checkbox|radio|switch|option]`、contenteditable 与 tabindex 控件。
- [x] GREEN 判定可见、启用且实际可操作元素；每个实际命中矩形严格达到 44×44px，
      不以孤立目标或 spacing 例外放宽。
- [x] checkbox/radio 以关联 label 的合并命中框检查，不再无条件跳过。
- [x] 失败输出 selector/accessible name/矩形/最近目标间距，便于定位。

### D. 真栈两视口六流程

目标文件：MES-128 专用 Compose override/env generator、Playwright config/spec、DB 断言 helper、
`frontend/e2e/evidence/mes111-b5-real/`。

- [x] 强随机 PostgreSQL/Redis/MinIO/JWT 凭据；服务只在隔离网络通信，不发布数据端口。
- [x] 通过真实 UI 注册/登录并断言 users、sessions、workspaces 记录。
- [x] 键盘创建 issue，断言 201 envelope 与 issues/outbox 数据。
- [x] 看板通过非拖拽入口移动卡片，断言 200 响应、status/version 与落库。
- [x] 键盘发布评论，断言 201 响应与 comment 落库。
- [x] 创建第二工作区并用切换器切换，断言成员关系和请求 workspace scope。
- [x] `Ctrl/Cmd+K` 搜索新 issue，断言真实 search API 命中并键盘打开。
- [x] 整条旅程分别在 320×720、390×844 运行，检查焦点、live region、无横向溢出并截图。

### E. 状态矩阵、读屏连续性与 G 项

- [x] 为 13 核心页构造真实 loading/refreshing、empty、error、permission、offline/stale、
      long/large/missing 场景；每类使用真实响应或可审计的网络故障注入，截图和 manifest 唯一。
- [x] 为虚拟看板增加用户可切换的“读屏完整列表”模式；该模式禁用窗口化并保留移动操作。
- [x] 实现 Board 与 Issue 详情上下文快捷键组及帮助层动态上下文（G9）。
- [x] 在 `workspace.md` 定义 feature flag 消费契约，实现缺省启用、显式禁用的导航/命令/路由门控（G15）。
- [x] 在 `agent.md` 定义 `agent.trigger_skipped` 的 toast + 关联 agent/原因呈现，接入实时事件并测试（G17）。
- [x] 用当前代码/测试核实并更新 G1–G17：已由前序批次实现的项目附精确文件、测试和真栈证据；
      未实现项目在本轮补齐，不能仅改文字状态。
- [ ] 执行核心读屏手册，记录读序、焦点归还、live announcement 和虚拟列表连续性；
      若当前系统不能运行 NVDA/VoiceOver，必须把该外部环境条件作为阻断交验，不能写成已通过。

### F. Spec、README、审计与 CI

- [x] `design-quality.md` §13.1–§13.5 每个自动化勾选项附测试/证据锚点后再勾选；人工
      NVDA/VoiceOver 项保持未勾选。
- [x] 逐页清单按模块、用户旅程、跨切面三轮更新；112 四组合单元格链接真实截图。
- [x] 重写 MES-128 审计，删除自动化“保留后续”结论，增加逐文件覆盖率表、真栈 DB 表、
      状态矩阵 manifest、读屏记录和 G1–G17 核销表。
- [x] README 增加可复现的 MES-128 真栈/门禁命令；CI 默认运行变更逐文件门禁和真栈流程。

## 4. 风险与回滚

- 状态视觉证据与真实业务旅程分离：视觉故障注入不得改生产 API 语义，真栈旅程不得 mock。
- 兼容性 feature flag 缺省启用，只有工作区显式 `false` 才隐藏，避免升级后功能消失。
- 读屏完整列表模式由用户显式选择，默认仍保留大数据虚拟化性能。
- 视觉基线仅在新鲜 production preview/隔离真栈中生成；禁止无检查批量更新快照。
- rebase/force-with-lease 只作用于 PR #118 的专用远端分支。
