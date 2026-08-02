<!-- prettier-ignore-start -->

# MES-108 React 迁移模型卡

> 本文件由 `frontend/model-card/mes108-react-migration.json` 生成，请勿手工编辑。
> 机器校验只证明映射、路径、测试与令牌引用完整；像素差异仍须按固定环境人工验收。

## 基线与门禁

- 静态原型：MES-142 @ `a82df9ab382223c125b77635c94f228024384518`
- 用户与验收确认：**尚未确认**
- 主题：`light`<br>`dark`
- 固定视口：`390x844`<br>`1440x900`
- 必填状态：`default`<br>`loading`<br>`empty`<br>`error`
- 输入方式：`mouse`<br>`keyboard`<br>`touch`
- 固定环境：Chromium / zh-CN / UTC / DPR 1 / e2e/fixtures/fonts

确认门禁未通过时，本表只用于迁移与差异追踪，不代表 React 页面已成为最终视觉交付。

## 页面映射

| Blueprint page | React route | Strategy | Reconciliation | Components | Unit tests | E2E | States | Interactions | 视觉矩阵 | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 登录 (`auth-login`)<br>`login` | `/login` | calibrate | pending | `src/shell/pages/LoginPage.tsx`<br>`src/design/components/PublicFlowShell.tsx` | `src/shell/__tests__/LoginPage.test.tsx`<br>`src/shell/__tests__/LoginPageReal.test.tsx` | `e2e/auth-smoke.spec.ts`<br>`e2e/real-mes106-auth-guard.spec.ts` | default=verified<br>loading=verified<br>empty=not-applicable<br>error=verified | `submit-and-return-to-safe-next` [mouse, keyboard]=pending<br>`switch-theme-and-locale` [mouse, keyboard]=pending | pending=12<br>not-applicable=4 | 真实鉴权和安全回跳已接通；仍须按未确认原型做固定环境像素校准。 |
| 邀请注册 (`auth-register`)<br>`register` | `/invite/:token` | calibrate | pending | `src/workspace/pages/InviteAcceptPage.tsx`<br>`src/design/components/PublicFlowShell.tsx` | `src/workspace/__tests__/InviteAcceptPage.test.tsx` | `e2e/workspace-flow.spec.ts` | default=verified<br>loading=verified<br>empty=not-applicable<br>error=verified | `preview-then-accept-invitation` [mouse, keyboard]=pending | pending=12<br>not-applicable=4 | 开放注册模型按真实产品契约收敛为邀请预览与接受流程，不伪造后端能力。 |
| 验证码与恢复流程 (`auth-code`)<br>`code` | `/device`<br>`/forgot`<br>`/reset`<br>`/auth/oauth/callback/:provider` | calibrate | pending | `src/features/device/DeviceAuthorizationPage.tsx`<br>`src/shell/pages/ForgotPasswordPage.tsx`<br>`src/shell/pages/ResetPasswordPage.tsx`<br>`src/shell/pages/OAuthCallbackPage.tsx` | `src/features/device/__tests__/DeviceAuthorizationPage.test.tsx`<br>`src/shell/__tests__/ForgotPasswordPage.test.tsx`<br>`src/shell/__tests__/ResetPasswordPage.test.tsx`<br>`src/shell/__tests__/OAuthCallbackPage.test.tsx` | `e2e/auth-smoke.spec.ts` | default=verified<br>loading=verified<br>empty=not-applicable<br>error=verified | `authorize-or-recover-session` [mouse, keyboard]=pending | pending=12<br>not-applicable=4 | 原型单一验证码状态映射到真实设备码、密码恢复与 OAuth 回调页面族。 |
| 收件箱 (`inbox`)<br>`inbox` | `/w/:workspaceSlug/inbox`<br>`/w/:workspaceSlug/inbox/:notificationId` | calibrate | pending | `src/features/inbox/InboxPage.tsx` | `src/features/inbox/__tests__/InboxPage.test.tsx` | `e2e/real-comments-inbox.spec.ts`<br>`e2e/real-inbox-notify.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `select-read-archive-and-open-deeplink` [mouse, keyboard, touch]=pending | pending=16 | 桌面双栏与移动单栏路由已实现；视觉校准仍待原型确认。 |
| 聊天 (`chat`)<br>`chat` | `/w/:workspaceSlug/chat`<br>`/w/:workspaceSlug/chat/:sessionId` | calibrate | pending | `src/features/chat/ChatPage.tsx` | `src/features/chat/__tests__/ChatPage.test.tsx` | `e2e/real-chat-compose.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `send-stop-regenerate-and-switch-candidate` [mouse, keyboard, touch]=pending | pending=16 | 真实流式会话、附件与运行反馈已接通。 |
| 我的 Issues (`my-issues`)<br>`my` | `/w/:workspaceSlug/issues?mine=true`<br>alias → `issues` | calibrate | pending | `src/features/issues/IssuesPage.tsx` | `src/features/issues/__tests__/IssuesPage.test.tsx` | `e2e/mes79-deeplinks.spec.ts` | default=verified<br>loading=pending<br>empty=pending<br>error=pending | `filter-current-assignee` [mouse, keyboard, touch]=pending | pending=16 | 原型独立入口映射为真实 Issues 数据视图的当前负责人筛选；需补专用深链与状态证据。 |
| Issue 列表 (`issues`)<br>`issues` | `/w/:workspaceSlug/issues` | calibrate | pending | `src/features/issues/IssuesPage.tsx`<br>`src/design/patterns/DataView.tsx` | `src/features/issues/__tests__/IssuesPage.test.tsx` | `e2e/mes111-b2.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `filter-sort-select-and-create` [mouse, keyboard, touch]=pending | pending=16 | 真实过滤、排序、保存视图与创建流程已实现。 |
| 看板 (`board`)<br>`board` | `/w/:workspaceSlug/board`<br>`/w/:workspaceSlug/views/:viewId` | calibrate | pending | `src/features/board/BoardPage.tsx` | `src/features/board/__tests__/BoardPage.test.tsx`<br>`src/features/board/__tests__/BoardTouchMoveSheet.test.tsx` | `e2e/real-board.spec.ts`<br>`e2e/real-board-projection.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `drag-with-feedback-and-rollback` [mouse, touch]=pending<br>`keyboard-and-touch-move` [keyboard, touch]=pending | pending=16 | 真实拖拽、键盘/触控替代路径和实时投影均有测试。 |
| Issue 详情与评论 (`issue-detail`)<br>`issue` | `/w/:workspaceSlug/issues/by-identifier/:identifier`<br>`/w/:workspaceSlug/issues/:issueId` | calibrate | pending | `src/features/issues/IssueDetailPage.tsx`<br>`src/features/comments/CommentsPanel.tsx` | `src/features/issues/__tests__/IssueDetailPage.test.tsx`<br>`src/features/comments/__tests__/CommentsPanel.test.tsx` | `e2e/real-comments-inbox.spec.ts`<br>`e2e/real-attachment.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `edit-comment-attach-retry-and-realtime-refresh` [mouse, keyboard, touch]=pending | pending=16 | 规范 identifier 深链、属性编辑、评论/附件与实时更新已接通。 |
| 项目列表 (`projects`)<br>`projects` | `/w/:workspaceSlug/projects` | calibrate | pending | `src/features/projects/ProjectsPage.tsx` | `src/features/projects/__tests__/ProjectsPage.test.tsx` | `e2e/real-projects.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `search-switch-layout-and-create` [mouse, keyboard, touch]=pending | pending=16 | 真实项目 API 与创建流程已覆盖。 |
| 项目详情与设置 (`project-detail`)<br>`project` | `/w/:workspaceSlug/projects/:projectId`<br>`/w/:workspaceSlug/projects/:projectId/settings` | calibrate | pending | `src/features/projects/ProjectDetailPage.tsx`<br>`src/features/projects/ProjectSettingsPage.tsx` | `src/features/projects/__tests__/ProjectDetailPage.test.tsx`<br>`src/features/projects/__tests__/ProjectSettingsPage.test.tsx` | `e2e/real-projects.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `switch-tabs-update-health-and-settings` [mouse, keyboard, touch]=pending | pending=16 | Overview、Issues、Updates、Dashboard 与项目设置均接真实数据。 |
| 成员名册 (`members`)<br>`members` | `/w/:workspaceSlug/members` | calibrate | pending | `src/features/members/MembersPage.tsx` | `src/features/members/__tests__/MembersPage.test.tsx` | `e2e/real-members.spec.ts`<br>`e2e/real-mes111-b3-evidence.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `search-filter-change-role-and-remove` [mouse, keyboard, touch]=pending | pending=16 | 人类与 Agent 共用单一名册，移动端主次行卡片已实现。 |
| Agent 名册视图 (`agents`)<br>`agents` | `/w/:workspaceSlug/members?member_type=agent`<br>alias → `members` | calibrate | pending | `src/features/members/MembersPage.tsx` | `src/features/members/__tests__/MembersPage.test.tsx` | `e2e/real-members.spec.ts` | default=verified<br>loading=pending<br>empty=pending<br>error=pending | `filter-agent-roster-and-open-detail` [mouse, keyboard, touch]=pending | pending=16 | 原型独立 Agent 列表按单一名册契约合并，需补专用筛选深链证据。 |
| Agent 详情 (`agent-detail`)<br>`agent` | `/w/:workspaceSlug/agents/:agentId` | calibrate | pending | `src/features/agents/AgentDetailPage.tsx` | `src/features/agents/__tests__/AgentDetailPage.test.tsx` | `e2e/mes111-reachability.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `edit-chat-and-manage-skills-tools` [mouse, keyboard, touch]=pending | pending=16 | 详情与运行五态已存在；工具写路径由 MES-116/PR #117 收口。 |
| Skills 列表与市场 (`skills`)<br>`skills` | `/w/:workspaceSlug/automations/skills`<br>`/w/:workspaceSlug/automations/skills/marketplace`<br>`/marketplace` | calibrate | pending | `src/features/skills/SkillsPage.tsx`<br>`src/features/skills/MarketplacePage.tsx` | `src/features/skills/__tests__/SkillsPage.test.tsx`<br>`src/features/skills/__tests__/MarketplacePage.test.tsx` | `e2e/mes111-reachability.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `search-import-install-and-open-detail` [mouse, keyboard, touch]=pending | pending=16 | 真实安装与授权信息架构在 PR #117 收口，当前保持待校准。 |
| Skill 详情 (`skill-detail`)<br>`skill` | `/w/:workspaceSlug/automations/skills/:skillId` | calibrate | pending | `src/features/skills/SkillDetailPage.tsx` | `src/features/skills/__tests__/SkillDetailPage.test.tsx` | `e2e/mes111-reachability.spec.ts` | default=verified<br>loading=verified<br>empty=not-applicable<br>error=verified | `inspect-version-permissions-and-bindings` [mouse, keyboard, touch]=pending | pending=12<br>not-applicable=4 | 详情结构存在，最终真实交互证据随 PR #117 冻结 head 更新。 |
| 自动值守 (`autopilots`)<br>`autopilot`<br>`automations` | `/w/:workspaceSlug/automations/autopilots`<br>`/w/:workspaceSlug/automations/autopilots/new`<br>`/w/:workspaceSlug/automations/autopilots/:autopilotId`<br>`/w/:workspaceSlug/automations/autopilots/:autopilotId/edit`<br>`/w/:workspaceSlug/automations/autopilots/runs/:runId`<br>`/w/:workspaceSlug/automations/webhooks`<br>`/automation` | calibrate | pending | `src/features/autopilots/AutopilotsPage.tsx`<br>`src/features/autopilots/AutopilotEditorPage.tsx`<br>`src/features/autopilots/AutopilotDetailPage.tsx`<br>`src/features/autopilots/AutopilotRunDetailPage.tsx`<br>`src/features/autopilots/WebhookConfigPage.tsx` | `src/features/autopilots/__tests__/AutopilotsPage.test.tsx`<br>`src/features/autopilots/__tests__/AutopilotEditorPage.test.tsx` | `e2e/real-autopilots.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `create-edit-toggle-test-run-and-inspect-timeline` [mouse, keyboard, touch]=pending | pending=16 | 原型单页扩展为真实列表、编辑器、详情、运行与 Webhook 页面族。 |
| 小队 (`squads`)<br>`squads` | `/w/:workspaceSlug/squads`<br>`/w/:workspaceSlug/squads/:squadId`<br>`/w/:workspaceSlug/squads/:squadId/tasks/:taskId` | calibrate | pending | `src/features/squads/SquadsPage.tsx`<br>`src/features/squads/SquadDetailPage.tsx`<br>`src/features/squads/SquadTaskDetailPage.tsx` | `src/features/squads/__tests__/SquadsPage.test.tsx`<br>`src/features/squads/__tests__/SquadTaskDetailPage.test.tsx` | `e2e/mes111-reachability.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `create-manage-members-and-move-task` [mouse, keyboard, touch]=pending | pending=16 | 真实小队编排接口已接通；完整按钮与持久化旅程由 PR #117 收口。 |
| 运行环境与执行详情 (`runtimes`)<br>`runtimes` | `/w/:workspaceSlug/automations/runtimes`<br>`/w/:workspaceSlug/automations/runtimes/:runtimeId`<br>`/w/:workspaceSlug/executions/:executionId` | calibrate | pending | `src/features/runtimes/RuntimesPage.tsx`<br>`src/features/runtimes/RuntimeDetailPage.tsx`<br>`src/features/runtimes/ExecutionDetailPage.tsx` | `src/features/runtimes/__tests__/RuntimesPage.test.tsx`<br>`src/features/runtimes/__tests__/ExecutionDetailPage.test.tsx` | `e2e/real-runtimes.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `register-inspect-log-and-reconnect` [mouse, keyboard]=pending | pending=16 | 真实 runtime 注册、详情与执行日志路径均有覆盖。 |
| 分析 (`analytics`)<br>`usage`<br>`analytics` | `/w/:workspaceSlug/insights` | calibrate | pending | `src/features/analytics/InsightsPage.tsx` | `src/features/analytics/__tests__/InsightsPage.test.tsx`<br>`src/features/analytics/__tests__/InsightsPage.states.test.tsx` | `e2e/real-analytics.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `change-range-inspect-breakdown-and-export` [mouse, keyboard]=pending | pending=16 | 原型 Usage/Analytics 别名收敛为规范 Insights 深链。 |
| 账号与工作区设置 (`settings`)<br>`settings` | `/settings`<br>`/settings/appearance`<br>`/settings/notifications`<br>`/settings/security`<br>`/w/:workspaceSlug/settings`<br>`/w/:workspaceSlug/settings/general`<br>`/w/:workspaceSlug/settings/invitations`<br>`/w/:workspaceSlug/settings/roles`<br>`/w/:workspaceSlug/settings/labels`<br>`/w/:workspaceSlug/settings/custom-fields`<br>`/w/:workspaceSlug/settings/data`<br>`/w/:workspaceSlug/settings/tokens`<br>`/w/:workspaceSlug/settings/audit`<br>`/w/:workspaceSlug/settings/danger`<br>`/w/:workspaceSlug/settings/members`<br>`/w/:workspaceSlug/settings/approvals`<br>`/w/:workspaceSlug/settings/fields` | calibrate | pending | `src/shell/pages/SettingsPage.tsx`<br>`src/workspace/pages/WorkspaceSettingsPage.tsx`<br>`src/design/patterns/SettingsLayout.tsx` | `src/shell/__tests__/SettingsPage.test.tsx`<br>`src/workspace/__tests__/WorkspaceSettingsPage.test.tsx` | `e2e/real-theme.spec.ts`<br>`e2e/real-mes111-b4.spec.ts` | default=verified<br>loading=verified<br>empty=not-applicable<br>error=verified | `switch-sections-edit-save-and-confirm-danger` [mouse, keyboard, touch]=pending | pending=12<br>not-applicable=4 | 账号与工作区设置按真实权限拆分，主题、通知、安全与管理子页均有路由。 |
| 全局状态与组件画廊 (`state-gallery`)<br>`states` | `/styleguide` | calibrate | pending | `src/design/StyleguidePage.tsx`<br>`src/design/components/EmptyState.tsx`<br>`src/design/components/ErrorState.tsx`<br>`src/design/components/Skeleton.tsx` | `src/design/__tests__/StyleguidePage.test.tsx`<br>`src/design/__tests__/ErrorState.test.tsx` | `e2e/visual/styleguide.spec.ts`<br>`e2e/visual/theme-visual.spec.ts` | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `inspect-component-state-theme-and-viewport` [mouse, keyboard, touch]=pending | pending=16 | 原型状态画廊映射到公开设计系统 fixture；像素校准仍待原型确认。 |

## React 扩展页面

| Extension | React route | Strategy | Reconciliation | States | Interactions | 视觉矩阵 | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 首页、工作区入口与选择器 (`home-and-workspace-entry`) | `/`<br>`/w/:workspaceSlug`<br>`/workspace-picker` | reuse | pending | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `select-workspace-and-open-onboarding` [mouse, keyboard, touch]=pending | pending=16 | React 独有的多工作区入口；须在原型确认后决定是保留扩展还是补入基线。 |
| 待我审批 (`approvals`) | `/approvals`<br>`/w/:workspaceSlug/approvals` | reuse | pending | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `approve-or-reject-request` [mouse, keyboard]=pending | pending=16 | React 独有的审批页；真实权限与双深链已存在，最终视觉关系待确认。 |
| 成员详情 (`member-detail`) | `/w/:workspaceSlug/members/:memberId` | reuse | pending | default=verified<br>loading=verified<br>empty=not-applicable<br>error=verified | `open-member-or-agent-profile` [mouse, keyboard, touch]=pending | pending=12<br>not-applicable=4 | React 独有的人类成员详情；Agent 会按成员类型转入 Agent 详情。 |
| 周期 (`cycles`) | `/w/:workspaceSlug/cycles` | reuse | pending | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `create-and-filter-cycle` [mouse, keyboard]=pending | pending=16 | React 独有的项目周期页面，当前只有可达性证据。 |
| 集成与出向 Webhook (`integrations`) | `/w/:workspaceSlug/automations/integrations`<br>`/w/:workspaceSlug/automations/integrations/:integrationId`<br>`/w/:workspaceSlug/automations/webhook-subscriptions` | reuse | pending | default=verified<br>loading=verified<br>empty=verified<br>error=verified | `configure-test-and-inspect-integration` [mouse, keyboard]=pending | pending=16 | React 独有的集成管理页面族；当前主干只有可达性 e2e，真实写路径证据待收口。 |
| 404 (`not-found`) | `*` | reuse | pending | default=verified<br>loading=not-applicable<br>empty=not-applicable<br>error=not-applicable | `return-home` [mouse, keyboard]=pending | pending=4<br>not-applicable=12 | AppShell 内外两个 catch-all 共用同一来源片段，模型卡按一个逻辑终态归属。 |

## 旧路由兼容映射

| Source pattern | Owner | Canonical target |
| --- | --- | --- |
| `^\/inbox$` | `inbox` | `/w/:workspaceSlug/inbox` |
| `^\/inbox\/([^/]+)$` | `inbox` | `/w/:workspaceSlug/inbox/:notificationId` |
| `^\/board$` | `board` | `/w/:workspaceSlug/board` |
| `^\/views\/([^/]+)$` | `board` | `/w/:workspaceSlug/views/:viewId` |
| `^\/members$` | `members` | `/w/:workspaceSlug/members` |
| `^\/members\/([^/]+)$` | `member-detail` | `/w/:workspaceSlug/members/:memberId` |
| `^\/projects$` | `projects` | `/w/:workspaceSlug/projects` |
| `^\/projects\/([^/]+)\/settings$` | `project-detail` | `/w/:workspaceSlug/projects/:projectId/settings` |
| `^\/projects\/([^/]+)$` | `project-detail` | `/w/:workspaceSlug/projects/:projectId` |
| `^\/issues$` | `issues` | `/w/:workspaceSlug/issues` |
| `^\/issues\/by-identifier\/([^/]+)$` | `issue-detail` | `/w/:workspaceSlug/issues/by-identifier/:identifier` |
| `^\/issues\/([^/]+)$` | `issue-detail` | `/w/:workspaceSlug/issues/:issueId` |
| `^\/chat$` | `chat` | `/w/:workspaceSlug/chat` |
| `^\/chat\/([^/]+)$` | `chat` | `/w/:workspaceSlug/chat/:sessionId` |
| `^\/squads$` | `squads` | `/w/:workspaceSlug/squads` |
| `^\/squads\/([^/]+)\/tasks\/([^/]+)$` | `squads` | `/w/:workspaceSlug/squads/:squadId/tasks/:taskId` |
| `^\/squads\/([^/]+)$` | `squads` | `/w/:workspaceSlug/squads/:squadId` |
| `^\/cycles$` | `cycles` | `/w/:workspaceSlug/cycles` |
| `^\/executions\/([^/]+)$` | `runtimes` | `/w/:workspaceSlug/executions/:executionId` |
| `^\/insights$` | `analytics` | `/w/:workspaceSlug/insights` |
| `^\/agents\/([^/]+)$` | `agent-detail` | `/w/:workspaceSlug/agents/:agentId` |
| `^\/automation$` | `autopilots` | `/w/:workspaceSlug/automations/autopilots` |
| `^\/autopilots$` | `autopilots` | `/w/:workspaceSlug/automations/autopilots` |
| `^\/autopilots\/(.+)$` | `autopilots` | `/w/:workspaceSlug/automations/autopilots/:rest` |
| `^\/runtimes$` | `runtimes` | `/w/:workspaceSlug/automations/runtimes` |
| `^\/runtimes\/([^/]+)$` | `runtimes` | `/w/:workspaceSlug/automations/runtimes/:runtimeId` |
| `^\/webhooks$` | `autopilots` | `/w/:workspaceSlug/automations/webhooks` |
| `^\/skills$` | `skills` | `/w/:workspaceSlug/automations/skills` |
| `^\/skills\/(.+)$` | `skills` | `/w/:workspaceSlug/automations/skills/:rest` |
| `^\/integrations$` | `integrations` | `/w/:workspaceSlug/automations/integrations` |
| `^\/integrations\/([^/]+)$` | `integrations` | `/w/:workspaceSlug/automations/integrations/:integrationId` |
| `^\/webhook-subscriptions$` | `integrations` | `/w/:workspaceSlug/automations/webhook-subscriptions` |
| `^\/automations\/(.+)$` | `autopilots` | `/w/:workspaceSlug/automations/:rest` |
| `^\/settings\/(labels\|data\|custom-fields\|members\|approvals\|fields\|danger)$` | `settings` | `/w/:workspaceSlug/settings/:section` |

## 共享组件映射

| Model | Strategy | Reconciliation | React files |
| --- | --- | --- | --- |
| `app-shell` | calibrate | pending | `src/shell/AppShell.tsx`<br>`src/shell/Sidebar.tsx`<br>`src/shell/MobileNav.tsx` |
| `public-flow-shell` | reuse | reused | `src/design/components/PublicFlowShell.tsx` |
| `page-header` | reuse | reused | `src/design/components/PageHeader.tsx` |
| `breadcrumb` | reuse | reused | `src/design/patterns/DataView.tsx`<br>`src/design/components/PageHeader.tsx` |
| `data-view` | reuse | reused | `src/design/patterns/DataView.tsx` |
| `detail-layout` | reuse | reused | `src/design/patterns/DetailLayout.tsx` |
| `conversation-layout` | reuse | reused | `src/design/patterns/ConversationLayout.tsx` |
| `settings-layout` | reuse | reused | `src/design/patterns/SettingsLayout.tsx` |
| `surface-card` | calibrate | pending | `src/design/patterns/DataView.tsx`<br>`src/design/patterns/DetailLayout.tsx` |
| `progress` | calibrate | pending | `src/features/runtimes/ExecutionDetailPage.tsx`<br>`src/features/attachments/components/ProgressRing.tsx` |
| `kpi-card` | calibrate | pending | `src/features/analytics/Kpi.tsx`<br>`src/features/analytics/KpiStrip.tsx` |
| `button` | reuse | reused | `src/design/components/Button.tsx` |
| `input` | reuse | reused | `src/design/components/Input.tsx` |
| `select` | reuse | reused | `src/design/components/Select.tsx` |
| `badge` | reuse | reused | `src/design/components/Badge.tsx` |
| `avatar` | reuse | reused | `src/design/components/Avatar.tsx` |
| `icon` | reuse | reused | `src/design/components/Icon.tsx` |
| `tabs` | reuse | reused | `src/design/components/Tabs.tsx` |
| `menu` | reuse | reused | `src/design/components/Menu.tsx` |
| `dialog` | reuse | reused | `src/design/components/Dialog.tsx` |
| `drawer` | reuse | reused | `src/design/components/Drawer.tsx` |
| `popover` | reuse | reused | `src/design/components/Popover.tsx` |
| `tooltip` | reuse | reused | `src/design/components/Tooltip.tsx` |
| `empty-state` | reuse | reused | `src/design/components/EmptyState.tsx` |
| `error-state` | reuse | reused | `src/design/components/ErrorState.tsx` |
| `skeleton` | reuse | reused | `src/design/components/Skeleton.tsx` |
| `status-dot` | reuse | reused | `src/design/components/StatusDot.tsx` |
| `data-table` | reuse | reused | `src/design/components/DataTable.tsx` |
| `toolbar` | reuse | reused | `src/design/components/Toolbar.tsx` |
| `command-palette` | calibrate | pending | `src/shortcuts/CommandPalette.tsx` |
| `toast` | reuse | reused | `src/design/components/Toast.tsx` |

## 令牌映射

| Static token | React semantic token | Strategy | Reconciliation |
| --- | --- | --- | --- |
| `--shell` | `--color-bg` | calibrate | pending |
| `--canvas` | `--color-canvas` | calibrate | pending |
| `--surface` | `--color-surface` | calibrate | pending |
| `--raised` | `--color-surface-raised` | calibrate | pending |
| `--hover` | `--color-surface-hover` | calibrate | pending |
| `--selected` | `--color-surface-selected` | calibrate | pending |
| `--ink` | `--color-text-strong` | calibrate | pending |
| `--ink-soft` | `--color-text-muted` | calibrate | pending |
| `--ink-faint` | `--color-text-disabled` | calibrate | pending |
| `--line` | `--color-border-subtle` | calibrate | pending |
| `--line-strong` | `--color-border` | calibrate | pending |
| `--input-line` | `--color-border` | calibrate | pending |
| `--primary` | `--color-accent` | calibrate | pending |
| `--primary-ink` | `--color-accent-contrast` | calibrate | pending |
| `--disabled` | `--color-surface-pressed` | calibrate | pending |
| `--disabled-ink` | `--color-text-disabled` | calibrate | pending |
| `--brand` | `--color-accent` | calibrate | pending |
| `--brand-soft` | `--color-accent-soft` | calibrate | pending |
| `--success` | `--color-success-fg` | calibrate | pending |
| `--success-soft` | `--color-success-bg` | calibrate | pending |
| `--warning` | `--color-warning-fg` | calibrate | pending |
| `--warning-soft` | `--color-warning-bg` | calibrate | pending |
| `--danger` | `--color-danger-fg` | calibrate | pending |
| `--danger-soft` | `--color-danger-bg` | calibrate | pending |
| `--info` | `--color-info-fg` | calibrate | pending |
| `--info-soft` | `--color-info-bg` | calibrate | pending |
| `--violet` | `--color-avatar-h5-bg` | calibrate | pending |
| `--orange` | `--color-avatar-h3-bg` | calibrate | pending |
| `--shadow-surface` | `--shadow-1` | calibrate | pending |
| `--shadow-menu` | `--shadow-2` | calibrate | pending |
| `--shadow-float` | `--shadow-3` | calibrate | pending |
| `--radius-xs` | `--radius-xs` | calibrate | pending |
| `--radius-sm` | `--radius-sm` | calibrate | pending |
| `--radius-md` | `--radius-md` | calibrate | pending |
| `--radius-lg` | `--radius-lg` | calibrate | pending |
| `--radius-xl` | `--radius-xl` | calibrate | pending |
| `--radius-round` | `--radius-full` | calibrate | pending |
| `--sidebar-size` | `--shell-sidebar-expanded` | calibrate | pending |
| `--page-header` | `--space-12` | calibrate | pending |
| `--ease` | `--ease-enter`<br>`--ease-exit`<br>`--ease-move` | calibrate | pending |
| `--font-ui` | `--font-family` | calibrate | pending |
| `--font-mono` | `--font-family-mono` | calibrate | pending |

## 已知校准差异

- **shell-width**：侧栏 256px → --shell-sidebar-expanded 为 240px（pending）
- **responsive-breakpoint**：移动壳层断点 720px → 现有壳层断点 599px（pending）
- **header-topology**：页面内 48px page bar → 全局 TopBar 与页面 PageHeader 分层（pending）
- **primary-action**：中性主按钮 → indigo accent 主按钮（pending）
- **surface-and-radius**：canvas/surface/selected 与 10px/14px 圆角 → 语义表面与 12px/16px 圆角（pending）

## 自动与人工边界

- `node scripts/verify-model-card.mjs --mode audit` 校验原型/React 页面全集、路由兼容表、组件/测试文件、状态与视觉矩阵、令牌引用及本文件生成结果。
- `node scripts/verify-model-card.mjs --mode release` 是最终门禁；未确认原型、`pending`、`blocked` 或缺少真实证据时必须失败。
- Unit/E2E 文件存在不等于测试已通过；交付时仍须运行对应命令并记录精确结果。
- 颜色、字号、间距、布局、动效、亮暗主题与响应式的像素一致性必须在固定浏览器、视口、DPR 与字体环境中逐页比对。
- `pending` 与 `blocked` 必须保留，禁止为通过门禁而改写为已完成；只有真实验证证据才能推进状态。

<!-- prettier-ignore-end -->
