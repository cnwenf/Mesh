# MES-128 变更源码逐文件覆盖率

最终命令：`npm run test:coverage`。Vitest 362 个文件、3981 个测试全部通过；聚合覆盖率为
lines 98.69%、functions 97.13%、branches 93.70%、statements 98.69%。随后动态门禁相对
`origin/main` 合并基点，并合并已提交、暂存、未暂存及未跟踪源码，扫描 66 个运行时
TS/TSX 文件。下表来自同一次 coverage snapshot，四指标逐文件均不低于 90%。

| File                                                       |  Lines | Functions | Branches | Statements |
| ---------------------------------------------------------- | -----: | --------: | -------: | ---------: |
| `src/a11y.ts`                                              |   100% |      100% |     100% |       100% |
| `src/api/workspace.ts`                                     |   100% |      100% |     100% |       100% |
| `src/design/components/Dialog.tsx`                         |   100% |      100% |   95.45% |       100% |
| `src/design/components/Drawer.tsx`                         |   100% |      100% |     100% |       100% |
| `src/design/components/PublicFlowShell.tsx`                |   100% |      100% |     100% |       100% |
| `src/design/patterns/ConversationLayout.tsx`               |   100% |      100% |     100% |       100% |
| `src/design/responsive.ts`                                 |   100% |      100% |     100% |       100% |
| `src/design/tokenValues.ts`                                |   100% |      100% |     100% |       100% |
| `src/features/agents/AgentDetailPage.tsx`                  | 97.57% |    92.68% |   92.23% |     97.57% |
| `src/features/analytics/InsightsPage.tsx`                  |  99.6% |      100% |   96.66% |      99.6% |
| `src/features/analytics/InsightsPanels.tsx`                |   100% |      100% |     100% |       100% |
| `src/features/approvals/ApprovalsPage.tsx`                 |   100% |      100% |   97.56% |       100% |
| `src/features/auth/AuditSettings.tsx`                      |   100% |      100% |   96.42% |       100% |
| `src/features/autopilots/AutopilotDetailPage.tsx`          | 98.14% |      100% |   91.57% |     98.14% |
| `src/features/autopilots/AutopilotRunDetailPage.tsx`       | 97.67% |      100% |      90% |     97.67% |
| `src/features/autopilots/AutopilotsPage.tsx`               |    99% |    95.23% |   95.61% |        99% |
| `src/features/autopilots/WebhookConfigPage.tsx`            | 99.02% |      100% |   95.34% |     99.02% |
| `src/features/board/BoardColumns.tsx`                      | 99.09% |      100% |   92.88% |     99.09% |
| `src/features/board/VirtualColumnBody.tsx`                 |   100% |      100% |   93.54% |       100% |
| `src/features/chat/ChatPage.tsx`                           | 96.69% |      100% |   95.61% |     96.69% |
| `src/features/comments/RunStatus.tsx`                      |   100% |      100% |      90% |       100% |
| `src/features/data-jobs/DataManagementPage.tsx`            |   100% |      100% |    93.1% |       100% |
| `src/features/data-jobs/ImportWizard.tsx`                  |  98.2% |      100% |   91.42% |      98.2% |
| `src/features/device/DeviceAuthorizationPage.tsx`          | 99.45% |      100% |   91.37% |     99.45% |
| `src/features/inbox/InboxBell.tsx`                         |   100% |      100% |     100% |       100% |
| `src/features/inbox/NotificationPreferencesSection.tsx`    |   100% |      100% |   91.48% |       100% |
| `src/features/integrations/BindingDrawer.tsx`              | 98.67% |      100% |   91.11% |     98.67% |
| `src/features/integrations/EventLedger.tsx`                |   100% |      100% |    90.9% |       100% |
| `src/features/integrations/IntegrationDetailPage.tsx`      |   100% |      100% |   90.74% |       100% |
| `src/features/integrations/IntegrationsPage.tsx`           | 99.83% |      100% |   91.03% |     99.83% |
| `src/features/issues/IssueDetailPage.tsx`                  | 99.12% |       90% |   90.16% |     99.12% |
| `src/features/issues/MoveProjectDialog.tsx`                |   100% |      100% |   96.15% |       100% |
| `src/features/issues/issueShortcuts.ts`                    |   100% |      100% |     100% |       100% |
| `src/features/members/MembersPage.tsx`                     | 95.84% |    95.12% |   92.06% |     95.84% |
| `src/features/projects/CyclesPage.tsx`                     | 99.02% |       90% |   92.18% |     99.02% |
| `src/features/projects/ProjectDetailPage.tsx`              | 98.18% |    95.45% |   90.51% |     98.18% |
| `src/features/projects/ProjectSettingsPage.tsx`            |   100% |    95.65% |   91.89% |       100% |
| `src/features/projects/ProjectsPage.tsx`                   | 99.64% |      100% |   92.13% |     99.64% |
| `src/features/runtimes/ExecutionDetailPage.tsx`            | 97.25% |     92.3% |   92.66% |     97.25% |
| `src/features/runtimes/RuntimeDetailPage.tsx`              |   100% |    93.33% |   91.42% |       100% |
| `src/features/runtimes/RuntimesPage.tsx`                   | 98.31% |      100% |   90.99% |     98.31% |
| `src/features/skills/AgentSkillsTab.tsx`                   | 97.76% |      100% |   93.75% |     97.76% |
| `src/features/skills/ImportWizard.tsx`                     | 96.56% |      100% |   90.69% |     96.56% |
| `src/features/skills/SkillDetailPage.tsx`                  |   100% |      100% |   90.21% |       100% |
| `src/shell/AppShell.tsx`                                   | 98.34% |    94.11% |   91.13% |     98.34% |
| `src/shell/MobileMoreDrawer.tsx`                           |   100% |      100% |     100% |       100% |
| `src/shell/Sidebar.tsx`                                    |   100% |      100% |     100% |       100% |
| `src/shell/SkipLink.tsx`                                   |   100% |      100% |     100% |       100% |
| `src/shell/TopBar.tsx`                                     | 94.39% |      100% |   93.02% |     94.39% |
| `src/shell/agentTriggerNotice.ts`                          |   100% |      100% |     100% |       100% |
| `src/shell/appRouteManifest.ts`                            |   100% |      100% |     100% |       100% |
| `src/shell/pages/ErrorPage.tsx`                            |   100% |      100% |     100% |       100% |
| `src/shell/pages/ForgotPasswordPage.tsx`                   |   100% |      100% |     100% |       100% |
| `src/shell/pages/LoginPage.tsx`                            |   100% |    95.23% |   98.98% |       100% |
| `src/shell/pages/NotFoundPage.tsx`                         |   100% |      100% |     100% |       100% |
| `src/shell/pages/OAuthCallbackPage.tsx`                    |   100% |      100% |     100% |       100% |
| `src/shell/pages/ResetPasswordPage.tsx`                    |   100% |      100% |     100% |       100% |
| `src/shell/shortcutsRegistration.ts`                       | 99.58% |      100% |      96% |     99.58% |
| `src/shortcuts/CommandPalette.tsx`                         | 99.31% |      100% |   96.96% |     99.31% |
| `src/shortcuts/ShortcutProvider.tsx`                       |   100% |      100% |   98.09% |       100% |
| `src/shortcuts/usePaletteContext.ts`                       | 96.96% |      100% |    92.3% |     96.96% |
| `src/workspace/InvitationList.tsx`                         |   100% |      100% |   91.07% |       100% |
| `src/workspace/RolesMatrix.tsx`                            | 97.46% |      100% |   92.85% |     97.46% |
| `src/workspace/featureFlags.tsx`                           |   100% |      100% |     100% |       100% |
| `src/workspace/pages/InviteAcceptPage.tsx`                 |   100% |      100% |   97.56% |       100% |
| `src/workspace/pages/settings/WorkspaceGeneralSection.tsx` |   100% |      100% |     100% |       100% |

## 门禁实现

`frontend/scripts/verify-perfile-coverage.mjs` 不维护目录白名单。它读取 Git merge-base，合并
branch diff、index、worktree 与 untracked 集合，统一排除测试、声明、入口与测试工具；覆盖率
缺失直接失败，lines/functions/branches/statements 分别判断。对应脚本测试 15/15 通过，覆盖
变更发现、去重、排除、merge-base、缺失 coverage、`Unknown` 与四个独立阈值。
