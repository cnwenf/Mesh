# MES-116 剩余页面族收口实施计划

日期：2026-08-02 · 负责人：Mesh 程序员 · 基线：`main@b3113ce4`

## 目标与边界

在已合入的设计系统、应用壳层和页面模式层之上，收口项目/周期、Agent/Skills、小队、自动值守、运行环境/执行、集成/订阅及低频异常页面。保持既有 API、`data-testid`、实时协议和写操作语义不变，不新增后端契约；新增 UI 一律复用 `src/design`、语义令牌和双语目录。

## TDD 工作包

1. **项目与周期**（`features/projects`）
   - 先补页面模式、密集行/网格切换、健康度/里程碑/周期/归档可读性、详情 Tab 和窄屏长文本测试。
   - 再迁移到 `DataView` / `DetailLayout` / `PageHeader`，补足对应状态与响应式 CSS。
2. **Agent 与 Skills**（`features/agents`、`features/skills`）
   - 先补运行五态、真实“技能与工具”向导步骤、工具权限 read-only/write/confirm-required、可信度/权限信息架构测试。
   - 再接通已有技能绑定能力，消除占位步骤并统一列表/市场/详情呈现。
3. **Squads 与 Autopilots**（`features/squads`、`features/autopilots`）
   - 先补概览/成员/计划/任务四区、审批决策卡、分步编辑摘要、kill-switch 横幅与运行时间线测试。
   - 再调整页面结构与 CSS，不改变审批、运行和 Webhook 契约。
4. **Runtimes 与执行**（`features/runtimes`）
   - 先补 `DataView` / `DetailLayout` 页面模式、等宽日志、日志区粘底工具条、凭据恒脱敏与一次性 token 清理测试。
   - 再迁移页面结构并补齐窄屏表格降级、焦点和长日志溢出处理。
5. **Integrations 与订阅**（`features/integrations`）
   - 先补目录/已连接分区、详情 Overview/Bindings/Events/Health 四区、健康恢复动作、熔断订阅和一次性签名密钥测试。
   - 再迁移列表/详情页模式并补独立 Health 面板；凭据继续只收不显。
6. **低频管理与异常路由**
   - 核对标签、自定义字段、数据作业页面均由工作区 SettingsLayout 可达且无占位/断链。
   - 先补 404/ErrorBoundary 四部分错误态、恢复动作和诊断 ID 测试，再复用设计层 `ErrorState`；OAuth 回调保留 PublicFlowShell 及恢复路径。

## 共享收口

- i18n：新增键同步 `en` / `zh-CN`，跑目录一致性测试。
- 路由：逐项验证 `/projects`、`/cycles`、`/agents/:id`、Skills、Squads、Autopilots、Runtimes/Executions、Integrations/Webhook subscriptions、设置子页、OAuth 与 `*`。
- 文档：同步功能 Spec 的已实现 UI 说明与 README 页面能力摘要；不改变后端数据或接口真源。
- Git：仓库级关闭提交钩子，author/committer 固定 `cnwenf <cnwenf@outlook.com>`，提交正文不得包含 co-author。

## 验证门禁

1. 变更模块先跑定向 UT，再跑 `npm run test:coverage` 与 per-file 门禁，整体及新增/变更文件覆盖率均不低于 90%。
2. `npm run lint`、`npm run typecheck`、`npm run build`、`npm run check:contrast` 全绿。
3. 新增 MES-116 Playwright 套件，真实启动前端与契约服务，逐路由真实点击；桌面/平板/手机 × light/dark，含长文本与横向溢出扫描。
4. 证据写入 `frontend/e2e/evidence/mes116/`，截图唯一性门禁通过。
5. 运行现有视觉回归及相关真实 e2e，确认无回归后再请求代码评审、提交、推送并创建关联 MES-116 的 draft PR。

## 并行分工

- 子任务 A：项目/周期，仅改 `features/projects/**`。
- 子任务 B：Agent/Skills，仅改 `features/agents/**` 与 `features/skills/**`。
- 子任务 C：Squads/Autopilots，仅改对应两个 feature 目录。
- 主程：Runtimes、Integrations、异常路由、i18n、共享 e2e/证据、文档、集成验证与 PR。
