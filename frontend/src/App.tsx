/**
 * App 组装入口(README §6.12 / Task 5):Provider 树 + 路由。
 *
 * BrowserRouter > ThemeProvider > I18nProvider > ToastProvider > ShortcutProvider。
 * useT 须在 I18nProvider 内,故内层 ShellProviders 负责取本地化文案并喂给
 * ToastProvider(regionLabel)与命令面板/帮助层;二者的 open 态以 useState 持有于本层,
 * ShortcutProvider 回调开启,OverlayControls 下达 TopBar。路由树经 ErrorBoundary 兜底。
 *
 * 路由形态(search-command-palette.md §3.4):一切核心资源以 workspace-scoped
 * **规范深链** `/w/{workspace_slug}/…` 渲染(九条清单闭合:issue 按编号 / 项目 /
 * 成员 / agent 别名 / 视图 / 执行 / 聊天 / 小队 / 审批);旧扁平路由经 AppShell
 * 内 catch-all <FlatRouteMigration/> 由**路由器 replace navigation** 迁移至规范
 * 路由(query/hash 保留,active workspace 按解析序求解,多工作区无上下文 →
 * /workspace-picker 选择页)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router';
import { getApiClient } from './api/instance';
import { restoreActiveOnboarding } from './features/onboarding';
import { getIssueByIdentifier } from './features/issues/api';
import { usePaletteIdentity } from './features/search/usePaletteIdentity';
import { ThemeProvider, ToastProvider } from './design';
import { useWorkspaceLocale } from './hooks/useWorkspaceLocale';
import { I18nProvider, useT } from './i18n';
import { CommandPalette, ShortcutHelp, ShortcutProvider } from './shortcuts';
import { AppShell, OverlayControlsProvider } from './shell/AppShell';
import type { OverlayControls } from './shell/AppShell';
import { ChatPage } from './features/chat';
import { AgentDetailPage } from './features/agents/AgentDetailPage';
import { ApprovalsPage } from './features/approvals/ApprovalsPage';
import { BoardPage } from './features/board/BoardPage';
import { MemberDetailPage } from './features/members/MemberDetailPage';
import { MembersPage } from './features/members/MembersPage';
import { CyclesPage } from './features/projects/CyclesPage';
import { IssueByIdRedirect } from './features/issues/IssueByIdRedirect';
import { IssuesPage } from './features/issues/IssuesPage';
import { ProjectDetailPage } from './features/projects/ProjectDetailPage';
import { ProjectsPage } from './features/projects/ProjectsPage';
import { ProjectSettingsPage } from './features/projects/ProjectSettingsPage';
import { InsightsPage } from './features/analytics/InsightsPage';
import { AutopilotDetailPage } from './features/autopilots/AutopilotDetailPage';
import { AutopilotEditorPage } from './features/autopilots/AutopilotEditorPage';
import { AutopilotRunDetailPage } from './features/autopilots/AutopilotRunDetailPage';
import { AutopilotsPage } from './features/autopilots/AutopilotsPage';
import { WebhookConfigPage } from './features/autopilots/WebhookConfigPage';
import { ExecutionDetailPage } from './features/runtimes/ExecutionDetailPage';
import { RuntimeDetailPage } from './features/runtimes/RuntimeDetailPage';
import { RuntimesPage } from './features/runtimes/RuntimesPage';
import { MarketplacePage } from './features/skills/MarketplacePage';
import { SkillDetailPage } from './features/skills/SkillDetailPage';
import { SkillsPage } from './features/skills/SkillsPage';
import { SquadDetailPage } from './features/squads/SquadDetailPage';
import { SquadTaskDetailPage } from './features/squads/SquadTaskDetailPage';
import { SquadsPage } from './features/squads/SquadsPage';
import { ErrorBoundary } from './shell/pages/ErrorPage';
import { DeviceAuthorizationPage } from './features/device/DeviceAuthorizationPage';
import { ForgotPasswordPage } from './shell/pages/ForgotPasswordPage';
import { HomePage } from './shell/pages/HomePage';
import { LoginPage } from './shell/pages/LoginPage';
import { NotFoundPage } from './shell/pages/NotFoundPage';
import { OAuthCallbackPage } from './shell/pages/OAuthCallbackPage';
import { ResetPasswordPage } from './shell/pages/ResetPasswordPage';
import { SettingsPage } from './shell/pages/SettingsPage';
import { FlatRouteMigration } from './workspace/flatRoutes';
import { InviteAcceptPage } from './workspace/pages/InviteAcceptPage';
import { WorkspaceHomePage } from './workspace/pages/WorkspaceHomePage';
import { WorkspaceSettingsPage } from './workspace/pages/WorkspaceSettingsPage';
import {
  WorkspaceApprovalsSettingsPage,
  WorkspaceDangerSettingsPage,
  WorkspaceFieldsSettingsPage,
  WorkspaceMembersSettingsPage,
} from './workspace/pages/settingsSubpages';
import { WorkspaceCustomFieldsPage, WorkspaceLabelsPage } from './features/labels';
import { DataManagementPage } from './features/data-jobs/DataManagementPage';
import { InboxPage } from './features/inbox';
import { useWorkspace } from './workspace/WorkspaceProvider';
import { WorkspacePickerPage } from './workspace/WorkspacePickerPage';

/**
 * 协商链「请求显式参数」级(§6.18):URL `?locale=` 为真正的每请求显式覆盖,
 * 最高优先;浏览器语言(navigator.languages,Accept-Language 的 SPA 等价物)
 * 作为「系统回退」级经 systemLocales 传入 —— 排在账号偏好/工作区默认之后、
 * en 回退之前(否则账号级语言偏好永不生效,违背 i18n.md L1)。
 */
function useLocaleInputs(): { requested: string | null; systemLocales: readonly string[] } {
  // 纯浏览器 SPA(入口 main.tsx 仅在浏览器执行),无需 SSR 守卫
  return useMemo(() => {
    const requested = new URLSearchParams(window.location.search).get('locale');
    const systemLocales = Array.isArray(navigator.languages) ? [...navigator.languages] : [];
    return { requested, systemLocales };
  }, []);
}

export default function App(): React.JSX.Element {
  const { requested, systemLocales } = useLocaleInputs();
  // 阶段 2 接通(MES-24):工作区默认 locale 经 workspace API 异步获取
  const workspaceDefaultLocale = useWorkspaceLocale(getApiClient());
  return (
    <BrowserRouter>
      <ThemeProvider>
        <I18nProvider
          requested={requested}
          systemLocales={systemLocales}
          workspaceDefaultLocale={workspaceDefaultLocale}
        >
          <ShellProviders />
        </I18nProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}

/**
 * 规范深链(§3.4):`/w/{ws}/issues/by-identifier/{KEY-N}` —— workspace-scoped
 * 解析 identifier 后 replace 至同工作区 issue 详情。工作区 id 取自当前工作区
 * 上下文(WorkspaceProvider,经 slug 解析,含历史 slug 规范化)。
 */
function WorkspaceIssueByIdentifierRedirect(): React.JSX.Element {
  const { identifier } = useParams<{ identifier: string }>();
  const { status, workspace } = useWorkspace();
  const [target, setTarget] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    if (workspace === null || identifier === undefined) return;
    let cancelled = false;
    const client = getApiClient();
    void (async () => {
      try {
        const detail = await getIssueByIdentifier(client, workspace.id, identifier);
        if (!cancelled) setTarget(detail.id);
      } catch {
        if (!cancelled) setTarget(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspace, identifier]);
  if (status !== 'ready' || workspace === null) return <></>;
  if (target === undefined) return <></>;
  if (target === null) return <Navigate to="/not-found" replace />;
  return <Navigate to={`/w/${workspace.slug}/issues/${target}`} replace />;
}

function ShellProviders(): React.JSX.Element {
  const t = useT();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  // no-results「新建 issue」门控(§4.2):当前工作区成员角色 owner/admin/member 可创建,
  // guest/失权(role null)不可。面板渲染于 Provider 树顶层(工作区路由之外),故经
  // usePaletteIdentity 的成员身份角色解析(§3.4 解析序)取角色,而非 useOptionalWorkspace。
  const paletteIdentity = usePaletteIdentity({ client: getApiClient() });
  const canCreateIssue =
    paletteIdentity.role !== null && paletteIdentity.role !== 'guest';

  const controls = useMemo<OverlayControls>(
    () => ({
      openPalette: () => setPaletteOpen(true),
      openHelp: () => setHelpOpen(true),
    }),
    [],
  );
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  // 帮助菜单恢复上手清单(onboarding.md §4.2 流程 3):恢复成功后收起帮助层,清单按库内进度重现;
  // 失败不再静默吞掉——帮助层内显式提示(§错误处理:用户可见失败反馈)。
  const handleRestoreOnboarding = useCallback(() => {
    setRestoreError(null);
    void restoreActiveOnboarding(getApiClient())
      .then((restored) => {
        if (restored) setHelpOpen(false);
      })
      .catch(() => setRestoreError(t('onboarding.restoreError')));
  }, [t]);

  return (
    <ToastProvider regionLabel={t('a11y.notifications')}>
      <ShortcutProvider
        onOpenPalette={controls.openPalette}
        onOpenHelp={controls.openHelp}
        sequencePendingLabel={t('shortcuts.sequencePending')}
      >
        <OverlayControlsProvider value={controls}>
          <ErrorBoundary>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              {/* 设备码授权确认页(auth.md §3.1.1;CLI mesh auth login 的批准侧) */}
              <Route path="/device" element={<DeviceAuthorizationPage />} />
              <Route path="/forgot" element={<ForgotPasswordPage />} />
              <Route path="/reset" element={<ResetPasswordPage />} />
              {/* OAuth 登录回调(§4.1/§4.5):提供商回跳 code+state,交换会话凭证后回跳 */}
              <Route path="/auth/oauth/callback/:provider" element={<OAuthCallbackPage />} />
              <Route path="/" element={<AppShell />}>
                <Route index element={<HomePage />} />
                {/* 账号设置(主题/语言/时区/安全/通知偏好):保持扁平路由,非工作区设置 */}
                <Route path="settings" element={<SettingsPage />} />
                {/* 多工作区无上下文 → 选择页(search-command-palette.md §3.4 解析序 ⑤) */}
                <Route path="workspace-picker" element={<WorkspacePickerPage />} />
                {/* 邀请接受页(公开;preview → accept,四 reason UI 态) */}
                <Route path="invite/:token" element={<InviteAcceptPage />} />

                {/* ===== 规范深链(workspace-scoped,§3.4 九条清单闭合)===== */}
                <Route path="w/:workspaceSlug" element={<WorkspaceHomePage />} />
                <Route path="w/:workspaceSlug/inbox" element={<InboxPage />} />
                {/* 看板与视图(kanban.md):默认视图 / 选中视图 URL 同步(§4.2 可分享/收藏) */}
                <Route path="w/:workspaceSlug/board" element={<BoardPage />} />
                <Route path="w/:workspaceSlug/views/:viewId" element={<BoardPage />} />
                {/* 成员名册(人 + agent 同册)与成员详情(agent 行经别名路由至 agent 详情) */}
                <Route path="w/:workspaceSlug/members" element={<MembersPage />} />
                <Route path="w/:workspaceSlug/members/:memberId" element={<MemberDetailPage />} />
                <Route path="w/:workspaceSlug/agents/:agentId" element={<AgentDetailPage />} />
                <Route path="w/:workspaceSlug/projects" element={<ProjectsPage />} />
                <Route path="w/:workspaceSlug/projects/:projectId" element={<ProjectDetailPage />} />
                <Route
                  path="w/:workspaceSlug/projects/:projectId/settings"
                  element={<ProjectSettingsPage />}
                />
                <Route path="w/:workspaceSlug/issues" element={<IssuesPage />} />
                <Route
                  path="w/:workspaceSlug/issues/by-identifier/:identifier"
                  element={<WorkspaceIssueByIdentifierRedirect />}
                />
                <Route path="w/:workspaceSlug/issues/:issueId" element={<IssueByIdRedirect />} />
                {/* 聊天模块(chat-session.md §4):会话列表 / 会话详情 */}
                <Route path="w/:workspaceSlug/chat" element={<ChatPage />} />
                <Route path="w/:workspaceSlug/chat/:sessionId" element={<ChatPage />} />
                {/* 小队(squad.md §4,可通知资源):列表 / 详情 / 任务详情 */}
                <Route path="w/:workspaceSlug/squads" element={<SquadsPage />} />
                <Route path="w/:workspaceSlug/squads/:squadId" element={<SquadDetailPage />} />
                <Route
                  path="w/:workspaceSlug/squads/:squadId/tasks/:taskId"
                  element={<SquadTaskDetailPage />}
                />
                <Route path="w/:workspaceSlug/cycles" element={<CyclesPage />} />
                {/* 执行详情(runtime.md §4) */}
                <Route
                  path="w/:workspaceSlug/executions/:executionId"
                  element={<ExecutionDetailPage />}
                />
                {/* 统一「待我审批」入口(README §6.10) */}
                <Route path="w/:workspaceSlug/approvals" element={<ApprovalsPage />} />
                {/* 自动化运营区(§6.12 信息架构:Autopilots / Runtimes / Skills 三入口) */}
                <Route path="w/:workspaceSlug/automations/autopilots" element={<AutopilotsPage />} />
                <Route
                  path="w/:workspaceSlug/automations/autopilots/new"
                  element={<AutopilotEditorPage />}
                />
                <Route
                  path="w/:workspaceSlug/automations/autopilots/runs/:runId"
                  element={<AutopilotRunDetailPage />}
                />
                <Route
                  path="w/:workspaceSlug/automations/autopilots/:autopilotId"
                  element={<AutopilotDetailPage />}
                />
                <Route
                  path="w/:workspaceSlug/automations/autopilots/:autopilotId/edit"
                  element={<AutopilotEditorPage />}
                />
                <Route path="w/:workspaceSlug/automations/runtimes" element={<RuntimesPage />} />
                <Route
                  path="w/:workspaceSlug/automations/runtimes/:runtimeId"
                  element={<RuntimeDetailPage />}
                />
                <Route path="w/:workspaceSlug/automations/webhooks" element={<WebhookConfigPage />} />
                <Route path="w/:workspaceSlug/automations/skills" element={<SkillsPage />} />
                <Route
                  path="w/:workspaceSlug/automations/skills/marketplace"
                  element={<MarketplacePage />}
                />
                <Route
                  path="w/:workspaceSlug/automations/skills/:skillId"
                  element={<SkillDetailPage />}
                />
                {/* 工作区设置(admin+;§6.12 管理员区五子页) */}
                <Route path="w/:workspaceSlug/settings" element={<WorkspaceSettingsPage />} />
                {/* label-property.md §4.1:工作区级标签 / 自定义字段定义管理 */}
                <Route path="w/:workspaceSlug/settings/labels" element={<WorkspaceLabelsPage />} />
                <Route path="w/:workspaceSlug/settings/data" element={<DataManagementPage />} />
                <Route
                  path="w/:workspaceSlug/settings/custom-fields"
                  element={<WorkspaceCustomFieldsPage />}
                />
                <Route
                  path="w/:workspaceSlug/settings/members"
                  element={<WorkspaceMembersSettingsPage />}
                />
                <Route
                  path="w/:workspaceSlug/settings/approvals"
                  element={<WorkspaceApprovalsSettingsPage />}
                />
                <Route
                  path="w/:workspaceSlug/settings/fields"
                  element={<WorkspaceFieldsSettingsPage />}
                />
                <Route
                  path="w/:workspaceSlug/settings/danger"
                  element={<WorkspaceDangerSettingsPage />}
                />
                {/* 统计报表(analytics.md §4.1):工作区洞察仪表盘(规范深链;
                    旧扁平 /insights 经 FlatRouteMigration 迁移至此) */}
                <Route path="w/:workspaceSlug/insights" element={<InsightsPage />} />

                {/* 旧扁平路由迁移:规范路由未命中者经 FlatRouteMigration 以路由器
                    replace navigation 落规范深链(query/hash 保留,§3.4 执行层);
                    非旧路由路径呈现 not-found */}
                <Route path="*" element={<FlatRouteMigration />} />
              </Route>
              <Route path="*" element={<NotFoundPage />} />
            </Routes>
          </ErrorBoundary>
          <CommandPalette
            open={paletteOpen}
            onClose={closePalette}
            title={t('shortcuts.paletteTitle')}
            closeLabel={t('a11y.closeDialog')}
            searchPlaceholder={t('shortcuts.palettePlaceholder')}
            emptyText={t('shortcuts.paletteEmpty')}
            canCreateIssue={canCreateIssue}
          />
          <ShortcutHelp
            open={helpOpen}
            onClose={closeHelp}
            title={t('shortcuts.helpTitle')}
            closeLabel={t('a11y.closeDialog')}
            groupLabels={{
              global: t('shortcuts.groupGlobal'),
              board: t('shortcuts.groupBoard'),
              issue: t('shortcuts.groupIssue'),
              chat: t('shortcuts.groupChat'),
            }}
            restoreLabel={t('onboarding.restoreHelp')}
            onRestore={handleRestoreOnboarding}
            restoreError={restoreError ?? undefined}
          />
        </OverlayControlsProvider>
      </ShortcutProvider>
    </ToastProvider>
  );
}
