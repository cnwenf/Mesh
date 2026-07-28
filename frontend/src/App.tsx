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
import { activeWorkspace, fetchMe } from './features/members/api';
import { getIssueByIdentifier } from './features/issues/api';
import { ThemeProvider, ToastProvider } from './design';
import { useWorkspaceLocale } from './hooks/useWorkspaceLocale';
import { I18nProvider, useT } from './i18n';
import { CommandPalette, ShortcutHelp, ShortcutProvider } from './shortcuts';
import { AppShell, OverlayControlsProvider } from './shell/AppShell';
import type { OverlayControls } from './shell/AppShell';
import { PlaceholderPage } from './shell/PlaceholderPage';
import { AgentDetailPage } from './features/agents/AgentDetailPage';
import { MarketplacePage } from './features/skills/MarketplacePage';
import { SkillDetailPage } from './features/skills/SkillDetailPage';
import { SkillsPage } from './features/skills/SkillsPage';
import { BoardPage } from './features/board/BoardPage';
import { MembersPage } from './features/members/MembersPage';
import { CyclesPage } from './features/projects/CyclesPage';
import { IssueDetailPage } from './features/issues/IssueDetailPage';
import { IssuesPage } from './features/issues/IssuesPage';
import { ProjectDetailPage } from './features/projects/ProjectDetailPage';
import { ProjectsPage } from './features/projects/ProjectsPage';
import { ProjectSettingsPage } from './features/projects/ProjectSettingsPage';
import { ExecutionDetailPage } from './features/runtimes/ExecutionDetailPage';
import { RuntimeDetailPage } from './features/runtimes/RuntimeDetailPage';
import { RuntimesPage } from './features/runtimes/RuntimesPage';
import { ErrorBoundary } from './shell/pages/ErrorPage';
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
  const [helpOpen, setHelpOpen] = useState(false);

  const controls = useMemo<OverlayControls>(
    () => ({
      openPalette: () => setPaletteOpen(true),
      openHelp: () => setHelpOpen(true),
    }),
    [],
  );
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);

  return (
    <ToastProvider regionLabel={t('a11y.notifications')}>
      <ShortcutProvider onOpenPalette={controls.openPalette} onOpenHelp={controls.openHelp}>
        <OverlayControlsProvider value={controls}>
          <ErrorBoundary>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/forgot" element={<ForgotPasswordPage />} />
              <Route path="/reset" element={<ResetPasswordPage />} />
              {/* OAuth 登录回调(§4.1/§4.5):提供商回跳 code+state,交换会话凭证后回跳 */}
              <Route path="/auth/oauth/callback/:provider" element={<OAuthCallbackPage />} />
              <Route path="/" element={<AppShell />}>
                <Route index element={<HomePage />} />
                <Route path="settings" element={<SettingsPage />} />
                {/* 工作区 §4:当前工作区上下文路由(slug 寻址,含历史 slug 重定向) */}
                <Route path="w/:workspaceSlug" element={<WorkspaceHomePage />} />
                <Route path="w/:workspaceSlug/settings" element={<WorkspaceSettingsPage />} />
                {/* label-property.md §4.1:工作区级标签 / 自定义字段定义管理 */}
                <Route path="w/:workspaceSlug/settings/labels" element={<WorkspaceLabelsPage />} />
                <Route
                  path="w/:workspaceSlug/settings/custom-fields"
                  element={<WorkspaceCustomFieldsPage />}
                />
                {/* 邀请接受页(公开;preview → accept,四 reason UI 态) */}
                <Route path="invite/:token" element={<InviteAcceptPage />} />
                <Route path="inbox" element={<InboxPage />} />
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
                {/* 技能库 / 详情 / 市场(skill.md §4.1):绑定入口在 agent 详情页「技能」Tab */}
                <Route path="skills" element={<SkillsPage />} />
                <Route path="skills/:skillId" element={<SkillDetailPage />} />
                <Route path="marketplace" element={<MarketplacePage />} />
                {/* agent 详情页:成员名册 agent 行的唯一深链入口(agent.md §4.3,README §6.12) */}
                <Route path="agents/:agentId" element={<AgentDetailPage />} />
                <Route path="cycles" element={<CyclesPage />} />
                <Route path="chat" element={<PlaceholderPage kind="chat" />} />
                {/* runtime.md §4:自动化入口落地为 Runtimes 模块(注册 / 监控 / 执行详情) */}
                <Route path="runtimes" element={<RuntimesPage />} />
                <Route path="runtimes/:runtimeId" element={<RuntimeDetailPage />} />
                <Route path="executions/:executionId" element={<ExecutionDetailPage />} />
                <Route path="automation" element={<Navigate to="/runtimes" replace />} />
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
          />
        </OverlayControlsProvider>
      </ShortcutProvider>
    </ToastProvider>
  );
}
