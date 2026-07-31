/**
 * App 组装入口(README §6.12 / Task 5):Provider 树 + 路由。
 *
 * BrowserRouter > ThemeProvider > I18nProvider > ToastProvider > ShortcutProvider。
 * useT 须在 I18nProvider 内,故内层 ShellProviders 负责取本地化文案并喂给
 * ToastProvider(regionLabel)与命令面板/帮助层;二者的 open 态以 useState 持有于本层,
 * ShortcutProvider 回调开启,OverlayControls 下达 TopBar。路由树经 ErrorBoundary 兜底。
 *
 * 阶段 2 接通(MES-24):
 * - workspaceDefaultLocale 经 useWorkspaceLocale 从工作区 API 异步获取;
 * - 偏好写入经 settingsStore 同步到 PATCH /api/v1/users/me。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router';
import { getApiClient } from './api/instance';
import { useAuthStore } from './state/authStore';
import { restoreActiveOnboarding } from './features/onboarding';
import { activeWorkspace, fetchMe } from './features/members/api';
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
import { BoardPage } from './features/board/BoardPage';
import { MembersPage } from './features/members/MembersPage';
import { CyclesPage } from './features/projects/CyclesPage';
import { IssueDetailPage } from './features/issues/IssueDetailPage';
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
import { SquadDetailPage } from './features/squads/SquadDetailPage';
import { SquadTaskDetailPage } from './features/squads/SquadTaskDetailPage';
import { SquadsPage } from './features/squads/SquadsPage';
import { MarketplacePage } from './features/skills/MarketplacePage';
import { SkillDetailPage } from './features/skills/SkillDetailPage';
import { SkillsPage } from './features/skills/SkillsPage';
import { ErrorBoundary } from './shell/pages/ErrorPage';
import { RequireAuth } from './shell/RequireAuth';
import { DeviceAuthorizationPage } from './features/device/DeviceAuthorizationPage';
import { ForgotPasswordPage } from './shell/pages/ForgotPasswordPage';
import { HomePage } from './shell/pages/HomePage';
import { LoginPage } from './shell/pages/LoginPage';
import { NotFoundPage } from './shell/pages/NotFoundPage';
import { OAuthCallbackPage } from './shell/pages/OAuthCallbackPage';
import { ResetPasswordPage } from './shell/pages/ResetPasswordPage';
import { SettingsPage } from './shell/pages/SettingsPage';
import { InviteAcceptPage } from './workspace/pages/InviteAcceptPage';
import { WorkspaceHomePage } from './workspace/pages/WorkspaceHomePage';
import { WorkspaceSettingsPage } from './workspace/pages/WorkspaceSettingsPage';
import { WorkspaceCustomFieldsPage, WorkspaceLabelsPage } from './features/labels';
import { DataManagementPage } from './features/data-jobs/DataManagementPage';
import { InboxPage } from './features/inbox';

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
 * C6 深链:`#IDENTIFIER` 链接解析为同工作区 issue。后端渲染 `#MES-123` 为
 * `/issues/by-identifier/MES-123`;此路由用当前活跃工作区解析 identifier 后跳详情。
 */
function IssueByIdentifierRedirect(): React.JSX.Element {
  const { identifier } = useParams<{ identifier: string }>();
  const [target, setTarget] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    const client = getApiClient();
    void (async () => {
      try {
        const me = await fetchMe(client);
        const active = activeWorkspace(me.memberships);
        if (active === null || identifier === undefined) {
          if (!cancelled) setTarget(null);
          return;
        }
        const detail = await getIssueByIdentifier(client, active.workspace_id, identifier);
        if (!cancelled) setTarget(detail.id);
      } catch {
        if (!cancelled) setTarget(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [identifier]);
  if (target === undefined) return <></>;
  if (target === null) return <Navigate to="/not-found" replace />;
  return <Navigate to={`/issues/${target}`} replace />;
}

function ShellProviders(): React.JSX.Element {
  const t = useT();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState('');
  const [helpOpen, setHelpOpen] = useState(false);

  const controls = useMemo<OverlayControls>(
    () => ({
      openPalette: () => {
        setPaletteQuery('');
        setPaletteOpen(true);
      },
      openHelp: () => setHelpOpen(true),
      // 统一搜索入口(design-quality A-02):顶栏搜索键入/回车携带查询展开同一面板
      openSearch: (query: string) => {
        setPaletteQuery(query);
        setPaletteOpen(true);
      },
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
      <ShortcutProvider onOpenPalette={controls.openPalette} onOpenHelp={controls.openHelp}>
        <OverlayControlsProvider value={controls}>
          <ErrorBoundary>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              {/* 设计系统 fixture 页(MES-115 视觉回归基础):公开、无业务数据,
                  组件状态矩阵走查与 1440/1024/768/390 × 亮暗拍摄对象 */}
              <Route path="/styleguide" element={<StyleguidePage />} />
              {/* 设备码授权确认页(auth.md §3.1.1;CLI mesh auth login 的批准侧) */}
              <Route path="/device" element={<DeviceAuthorizationPage />} />
              <Route path="/forgot" element={<ForgotPasswordPage />} />
              <Route path="/reset" element={<ResetPasswordPage />} />
              {/* OAuth 登录回调(§4.1/§4.5):提供商回跳 code+state,交换会话凭证后回跳 */}
              <Route path="/auth/oauth/callback/:provider" element={<OAuthCallbackPage />} />
              <Route path="/" element={<AppShell />}>
                {/* 邀请接受页(公开;preview → accept,四 reason UI 态):
                    未登录可见预览、accept 时才跳登录,故置于登录守卫之外。 */}
                <Route path="invite/:token" element={<InviteAcceptPage />} />
                {/* MES-106 路由守卫(auth.md §4.1):未登录访问受保护页统一跳
                    /login?next=<原路径>,登录后经 safeNextPath 回跳;token 失效
                    由 API 层 401 全局兜底清 token,守卫随之生效(二者不重叠)。 */}
                <Route element={<RequireAuth />}>
                  <Route index element={<HomePage />} />
                  <Route path="settings" element={<SettingsPage />} />
                  {/* 工作区 §4:当前工作区上下文路由(slug 寻址,含历史 slug 重定向) */}
                  <Route path="w/:workspaceSlug" element={<WorkspaceHomePage />} />
                  <Route path="w/:workspaceSlug/settings" element={<WorkspaceSettingsPage />} />
                  {/* label-property.md §4.1:工作区级标签 / 自定义字段定义管理 */}
                  <Route
                    path="w/:workspaceSlug/settings/labels"
                    element={<WorkspaceLabelsPage />}
                  />
                  <Route path="w/:workspaceSlug/settings/data" element={<DataManagementPage />} />
                  <Route
                    path="w/:workspaceSlug/settings/custom-fields"
                    element={<WorkspaceCustomFieldsPage />}
                  />
                  <Route path="inbox" element={<InboxPage />} />
                  {/* 收件箱详情深链:桌面双栏选中 + 手机单栏路由化(design-quality §4.4 Conversation) */}
                  <Route path="inbox/:notificationId" element={<InboxPage />} />
                  <Route path="projects" element={<ProjectsPage />} />
                  <Route path="projects/:projectId" element={<ProjectDetailPage />} />
                  <Route path="projects/:projectId/settings" element={<ProjectSettingsPage />} />
                  <Route path="issues" element={<IssuesPage />} />
                  {/* C6 深链:#IDENTIFIER 链接 → 解析当前工作区 issue 后跳详情 */}
                  <Route
                    path="issues/by-identifier/:identifier"
                    element={<IssueByIdentifierRedirect />}
                  />
                  <Route path="issues/:issueId" element={<IssueDetailPage />} />
                  {/* 看板与视图(kanban.md):视图定义层 shell(MES-43 切片;
                      选中视图 URL 同步 /views/{id} 可分享/收藏,§4.2) */}
                  <Route path="board" element={<BoardPage />} />
                  <Route path="views/:viewId" element={<BoardPage />} />
                  <Route path="members" element={<MembersPage />} />
                  {/* agent 详情页:成员名册 agent 行的唯一深链入口(agent.md §4.3,README §6.12) */}
                  <Route path="agents/:agentId" element={<AgentDetailPage />} />
                  {/* 小队(squad.md §4):列表 / 详情 / 任务详情(拆解树 + 计划审批) */}
                  <Route path="squads" element={<SquadsPage />} />
                  <Route path="squads/:squadId" element={<SquadDetailPage />} />
                  <Route path="squads/:squadId/tasks/:taskId" element={<SquadTaskDetailPage />} />
                  <Route path="cycles" element={<CyclesPage />} />
                {/* 技能库(skill.md §4.1/§4.2 / design-quality §2.6):库页 / 市场 / 详情。
                    市场路由规范深链为 /skills/marketplace(侧栏技能入口同族);旧 /marketplace 兼容重定向。 */}
                <Route path="skills" element={<SkillsPage />} />
                <Route path="skills/marketplace" element={<MarketplacePage />} />
                <Route path="skills/:skillId" element={<SkillDetailPage />} />
                <Route path="marketplace" element={<Navigate to="/skills/marketplace" replace />} />
                  {/* 聊天模块(chat-session.md §4):agent 会话(流式 / 候选 / 中断 / 沉淀);
                      /chat/:sessionId 桌面同步选中 + 手机列表/会话单栏路由化(design-quality §4.4) */}
                  <Route path="chat" element={<ChatPage />} />
                  <Route path="chat/:sessionId" element={<ChatPage />} />
                  {/* runtime.md §4:自动化入口落地为 Runtimes 模块(注册 / 监控 / 执行详情) */}
                  <Route path="runtimes" element={<RuntimesPage />} />
                  <Route path="runtimes/:runtimeId" element={<RuntimeDetailPage />} />
                  <Route path="executions/:executionId" element={<ExecutionDetailPage />} />
                  <Route path="autopilots" element={<AutopilotsPage />} />
                  {/* 统计报表(analytics.md §4.1):工作区洞察仪表盘 */}
                  <Route path="insights" element={<InsightsPage />} />
                  <Route path="autopilots/new" element={<AutopilotEditorPage />} />
                  <Route path="autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
                  <Route path="autopilots/:autopilotId" element={<AutopilotDetailPage />} />
                  <Route path="autopilots/:autopilotId/edit" element={<AutopilotEditorPage />} />
                  <Route path="webhooks" element={<WebhookConfigPage />} />
                  {/* 集成平台(integrations.md §4):集成管理 / 详情 / 出向 Webhook 订阅。
                      出向订阅落 /webhook-subscriptions,避让 autopilot 入站 /webhooks。 */}
                  <Route path="integrations" element={<IntegrationsPage />} />
                  <Route path="integrations/:integrationId" element={<IntegrationDetailPage />} />
                  <Route path="webhook-subscriptions" element={<WebhooksPage />} />
                  <Route path="automation" element={<Navigate to="/autopilots" replace />} />
                </Route>
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
        </OverlayControlsProvider>
      </ShortcutProvider>
    </ToastProvider>
  );
}
