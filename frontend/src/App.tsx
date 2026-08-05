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
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router';
import { getApiClient } from './api/instance';
import { useAuthStore } from './state/authStore';
import { restoreActiveOnboarding } from './features/onboarding';
import { getIssueByIdentifier } from './features/issues/api';
import { ThemeProvider, ToastProvider } from './design';
import { StyleguidePage } from './design/StyleguidePage';
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
import { IntegrationDetailPage } from './features/integrations/IntegrationDetailPage';
import { IntegrationsPage } from './features/integrations/IntegrationsPage';
import { WebhooksPage } from './features/integrations/WebhooksPage';
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
import { RequireAuth } from './shell/RequireAuth';
import { DeviceAuthorizationPage } from './features/device/DeviceAuthorizationPage';
import { ForgotPasswordPage } from './shell/pages/ForgotPasswordPage';
import { ForbiddenPage } from './shell/pages/ForbiddenPage';
import { HomePage } from './shell/pages/HomePage';
import { LoginPage } from './shell/pages/LoginPage';
import { OAuthCallbackPage } from './shell/pages/OAuthCallbackPage';
import { RegisterPage } from './shell/pages/RegisterPage';
import { ResetPasswordPage } from './shell/pages/ResetPasswordPage';
import { SettingsPage } from './shell/pages/SettingsPage';
import { AppearanceSettingsSection } from './shell/pages/settings/AppearanceSettingsSection';
import { ProfileSettingsSection } from './shell/pages/settings/ProfileSettingsSection';
import { SecuritySettingsSection } from './shell/pages/settings/SecuritySettingsSection';
import { FlatRouteMigration } from './workspace/flatRoutes';
import { InviteAcceptPage } from './workspace/pages/InviteAcceptPage';
import { WorkspaceHomePage } from './workspace/pages/WorkspaceHomePage';
import { WorkspaceSettingsPage } from './workspace/pages/WorkspaceSettingsPage';
import {
  WorkspaceApprovalsSettingsPage,
  WorkspaceFieldsSettingsPage,
  WorkspaceMembersSettingsPage,
} from './workspace/pages/settingsSubpages';
import { WorkspaceAuditSection } from './workspace/pages/settings/WorkspaceAuditSection';
import { WorkspaceCustomFieldsSection } from './workspace/pages/settings/WorkspaceCustomFieldsSection';
import { WorkspaceDangerSection } from './workspace/pages/settings/WorkspaceDangerSection';
import { WorkspaceDataSection } from './workspace/pages/settings/WorkspaceDataSection';
import { WorkspaceGeneralSection } from './workspace/pages/settings/WorkspaceGeneralSection';
import { WorkspaceInvitationsSection } from './workspace/pages/settings/WorkspaceInvitationsSection';
import { WorkspaceLabelsSection } from './workspace/pages/settings/WorkspaceLabelsSection';
import { WorkspaceRolesSection } from './workspace/pages/settings/WorkspaceRolesSection';
import { WorkspaceTokensSection } from './workspace/pages/settings/WorkspaceTokensSection';
import { InboxPage, NotificationPreferencesSection } from './features/inbox';
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
  // MES-106:工作区默认 locale 为鉴权请求,登录前不发起(否则匿名首页即收
  // 401);token 写入后随 hasToken 变化补取(登录前协商链自系统级回退)。
  const hasToken = useAuthStore((state) => state.token !== null);
  // 阶段 2 接通(MES-24):工作区默认 locale 经 workspace API 异步获取
  const workspaceDefaultLocale = useWorkspaceLocale(hasToken ? getApiClient() : null);
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
  const location = useLocation();
  // 403 独立页不暴露工作区级浮层、favorites/recents 或恢复操作。
  const globalOverlaysEnabled = location.pathname !== '/forbidden';
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState('');
  const [helpOpen, setHelpOpen] = useState(false);
  const controls = useMemo<OverlayControls>(
    () => ({
      openPalette: () => {
        if (!globalOverlaysEnabled) return;
        setPaletteQuery('');
        setPaletteOpen(true);
      },
      openHelp: () => {
        if (globalOverlaysEnabled) setHelpOpen(true);
      },
      // 统一搜索入口(design-quality A-02):顶栏搜索键入/回车携带查询展开同一面板
      openSearch: (query: string) => {
        if (!globalOverlaysEnabled) return;
        setPaletteQuery(query);
        setPaletteOpen(true);
      },
    }),
    [globalOverlaysEnabled],
  );
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  useEffect(() => {
    if (globalOverlaysEnabled) return;
    setPaletteOpen(false);
    setHelpOpen(false);
    setRestoreError(null);
  }, [globalOverlaysEnabled]);
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
              <Route path="/register" element={<RegisterPage />} />
              {/* 设计系统 fixture 页(MES-115 视觉回归基础):公开、无业务数据,
                  组件状态矩阵走查与 1440/1024/768/390 × 亮暗拍摄对象 */}
              <Route path="/styleguide" element={<StyleguidePage />} />
              {/* 设备码授权确认页(auth.md §3.1.1;CLI mesh auth login 的批准侧) */}
              <Route path="/device" element={<DeviceAuthorizationPage />} />
              <Route path="/forgot" element={<ForgotPasswordPage />} />
              <Route path="/reset" element={<ResetPasswordPage />} />
              {/* OAuth 登录回调(§4.1/§4.5):提供商回跳 code+state,交换会话凭证后回跳 */}
              <Route path="/auth/oauth/callback/:provider" element={<OAuthCallbackPage />} />
              {/* 授权失败页不套 AppShell，避免泄漏不可见工作区上下文；仍要求已有会话。 */}
              <Route element={<RequireAuth />}>
                <Route path="/forbidden" element={<ForbiddenPage />} />
              </Route>
              <Route path="/" element={<AppShell />}>
                {/* 邀请接受页(公开;preview → accept,四 reason UI 态):
                    未登录可见预览、accept 时才跳登录,故置于登录守卫之外。 */}
                <Route path="invite/:token" element={<InviteAcceptPage />} />
                {/* MES-106 路由守卫(auth.md §4.1):未登录访问受保护页统一跳
                    /login?next=<原路径>,登录后经 safeNextPath 回跳;token 失效
                    由 API 层 401 全局兜底清 token,守卫随之生效(二者不重叠)。 */}
                <Route element={<RequireAuth />}>
                  <Route index element={<HomePage />} />
                  {/* 账号设置(design-quality §4.4 Settings 模板):二级导航 + 子路由分页 */}
                  <Route path="settings" element={<SettingsPage />}>
                    <Route index element={<Navigate to="profile" replace />} />
                    <Route path="profile" element={<ProfileSettingsSection />} />
                    <Route path="appearance" element={<AppearanceSettingsSection />} />
                    <Route path="notifications" element={<NotificationPreferencesSection />} />
                    <Route path="security" element={<SecuritySettingsSection />} />
                  </Route>
                  {/* 多工作区无上下文 → 选择页(search-command-palette.md §3.4 解析序 ⑤) */}
                  <Route path="workspace-picker" element={<WorkspacePickerPage />} />
                  {/* 侧栏兼容入口:页面内解析当前 active workspace;搜索结果仍使用下方规范深链。 */}
                  <Route path="approvals" element={<ApprovalsPage />} />

                  {/* ===== 规范深链(workspace-scoped,§3.4 九条清单闭合)===== */}
                  <Route path="w/:workspaceSlug" element={<WorkspaceHomePage />} />
                  <Route path="w/:workspaceSlug/inbox" element={<InboxPage />} />
                  {/* 收件箱详情:桌面双栏选中 + 手机单栏路由化(design-quality §4.4) */}
                  <Route path="w/:workspaceSlug/inbox/:notificationId" element={<InboxPage />} />
                  {/* 看板与视图(kanban.md):默认视图 / 选中视图 URL 同步(§4.2 可分享/收藏) */}
                  <Route path="w/:workspaceSlug/board" element={<BoardPage />} />
                  <Route path="w/:workspaceSlug/views/:viewId" element={<BoardPage />} />
                  {/* 成员名册(人 + agent 同册)与成员详情(agent 行经别名路由至 agent 详情) */}
                  <Route path="w/:workspaceSlug/members" element={<MembersPage />} />
                  <Route path="w/:workspaceSlug/members/:memberId" element={<MemberDetailPage />} />
                  <Route path="w/:workspaceSlug/agents/:agentId" element={<AgentDetailPage />} />
                  <Route path="w/:workspaceSlug/projects" element={<ProjectsPage />} />
                  <Route
                    path="w/:workspaceSlug/projects/:projectId"
                    element={<ProjectDetailPage />}
                  />
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
                  <Route
                    path="w/:workspaceSlug/automations/autopilots"
                    element={<AutopilotsPage />}
                  />
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
                  <Route
                    path="w/:workspaceSlug/automations/webhooks"
                    element={<WebhookConfigPage />}
                  />
                  <Route path="w/:workspaceSlug/automations/skills" element={<SkillsPage />} />
                  <Route
                    path="w/:workspaceSlug/automations/skills/marketplace"
                    element={<MarketplacePage />}
                  />
                  <Route
                    path="w/:workspaceSlug/automations/skills/:skillId"
                    element={<SkillDetailPage />}
                  />
                  {/* 集成平台(integrations.md §4,MES-68):集成管理 / 详情 / 出向
                      Webhook 订阅,落 automations 运营区规范深链(侧栏同族入口);
                      旧扁平 /integrations、/webhook-subscriptions 经迁移表收敛至此
                      (出向订阅避让 autopilot 入站 /webhooks)。 */}
                  <Route
                    path="w/:workspaceSlug/automations/integrations"
                    element={<IntegrationsPage />}
                  />
                  <Route
                    path="w/:workspaceSlug/automations/integrations/:integrationId"
                    element={<IntegrationDetailPage />}
                  />
                  <Route
                    path="w/:workspaceSlug/automations/webhook-subscriptions"
                    element={<WebhooksPage />}
                  />
                  {/* 工作区设置(workspace.md §4.1/§4.2):二级导航 + 九子页,危险区仅 owner */}
                  <Route path="w/:workspaceSlug/settings" element={<WorkspaceSettingsPage />}>
                    <Route index element={<Navigate to="general" replace />} />
                    <Route path="general" element={<WorkspaceGeneralSection />} />
                    <Route path="invitations" element={<WorkspaceInvitationsSection />} />
                    <Route path="roles" element={<WorkspaceRolesSection />} />
                    {/* label-property.md §4.1:工作区级标签 / 自定义字段定义管理 */}
                    <Route path="labels" element={<WorkspaceLabelsSection />} />
                    <Route path="custom-fields" element={<WorkspaceCustomFieldsSection />} />
                    <Route path="data" element={<WorkspaceDataSection />} />
                    <Route path="tokens" element={<WorkspaceTokensSection />} />
                    <Route path="audit" element={<WorkspaceAuditSection />} />
                    <Route path="danger" element={<WorkspaceDangerSection />} />
                  </Route>
                  {/* 搜索 Spec 既有管理员命令深链继续可达;新版设置导航使用 roles 等子页。 */}
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
                  {/* 统计报表(analytics.md §4.1):工作区洞察仪表盘(规范深链;
                      旧扁平 /insights 经 FlatRouteMigration 迁移至此) */}
                  <Route path="w/:workspaceSlug/insights" element={<InsightsPage />} />

                  {/* 技能市场旧入口重定向(design-quality §2.6 / MES-111 死链闭合):
                      /marketplace → /skills/marketplace,再经 FlatRouteMigration 落规范路由 */}
                  <Route
                    path="marketplace"
                    element={<Navigate to="/skills/marketplace" replace />}
                  />
                  {/* 自动化运营区旧入口重定向:/automation → /autopilots,经迁移表落规范路由 */}
                  <Route path="automation" element={<Navigate to="/autopilots" replace />} />

                  {/* 旧扁平路由迁移:规范路由未命中者经 FlatRouteMigration 以路由器
                      replace navigation 落规范深链(query/hash 保留,§3.4 执行层);
                      非旧路由路径呈现 not-found */}
                  <Route path="*" element={<FlatRouteMigration />} />
                </Route>
              </Route>
            </Routes>
          </ErrorBoundary>
          {globalOverlaysEnabled ? (
            <>
              <CommandPalette
                open={paletteOpen}
                onClose={closePalette}
                title={t('shortcuts.paletteTitle')}
                closeLabel={t('a11y.closeDialog')}
                searchPlaceholder={t('shortcuts.palettePlaceholder')}
                emptyText={t('shortcuts.paletteEmpty')}
                initialQuery={paletteQuery}
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
            </>
          ) : null}
        </OverlayControlsProvider>
      </ShortcutProvider>
    </ToastProvider>
  );
}
