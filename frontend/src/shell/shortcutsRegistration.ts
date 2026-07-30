/**
 * shell 级快捷键/命令注册(README §6.12,命令全集 search-command-palette.md §1.2 S3)。
 *
 * - 命令面板条目:顶层导航(规范深链优先,无工作区上下文时落扁平路由经迁移
 *   解析)、设置各子页(admin/owner 门控:**无权命令根本不注册**,非「点击才报错」)、
 *   待审批、新建 issue、主题 ×4、复制当前深链、收藏/取消收藏、标记全部已读
 *   (随当前收件箱视图 filter)、帮助层;
 * - 快捷键:G→I/B/M/A 序列跳转;`c` 新建 issue;`/` 聚焦顶栏搜索(preventDefault
 *   由分发层负责);`?` 帮助层(分发层特判,此处登记供帮助层呈现);
 * - 所有快捷键均有等价鼠标路径(§6.12);返回合并注销函数。
 *
 * 工作区相关命令经 ShellShortcutEnv(workspaceSlug/workspaceId/isAdmin)门控:
 * 由 ShellShortcutsRegistrar 从 WorkspaceProvider 上下文注入,工作区切换/角色
 * 变化即重注册。
 */
import { useEffect } from 'react';
import { useNavigate } from 'react-router';
import { getApiClient } from '../api/instance';
import { deleteFavorite, listFavorites, putFavorite } from '../api/favorites';
import type { FavoriteTargetType } from '../api/favorites';
import { useT } from '../i18n';
import { fetchMe } from '../features/members/api';
import { restoreActiveOnboarding } from '../features/onboarding';
import { getCurrentInboxView, readAll } from '../features/inbox';
import { recordLastWorkspace } from '../workspace/lastWorkspace';
import { useShortcutRegistry } from '../shortcuts';
import type { ShortcutDef } from '../shortcuts';
import { useSettingsStore } from '../state/settingsStore';
import type { ThemeMode } from '../state/settingsStore';
import { useOptionalWorkspace } from '../workspace/WorkspaceProvider';
import { useOverlayControls } from './AppShell';

export const TOPBAR_SEARCH_SELECTOR = '[data-testid="topbar-search"]';

/**
 * 导航命令键联合(与 NAV_COMMAND_ROUTES 显式一一对应)。
 * 标签映射收紧为该键的 Record:映射缺任一键即编译失败,
 * 防 label undefined 注册进命令面板导致搜索整体崩溃(MES-45 回归守卫)。
 */
export type NavKey =
  | 'home'
  | 'inbox'
  | 'projects'
  | 'issues'
  | 'board'
  | 'members'
  | 'chat'
  | 'squads'
  | 'autopilots'
  | 'runtimes'
  | 'skills'
  | 'insights'
  | 'approvals'
  | 'settings';

export interface ShellShortcutLabels {
  /** nav.<key> 文案(必须覆盖全部 NavKey,缺一编译报错) */
  nav: Record<NavKey, string>;
  /** theme.<mode> 文案(light/dark/system) */
  theme: Record<string, string>;
  /** 动作类文案(见 ShellActionLabels 键集) */
  actions: ShellActionLabels;
}

export interface ShellActionLabels {
  readonly themeToggle: string;
  readonly newIssue: string;
  readonly focusSearch: string;
  readonly goInbox: string;
  readonly goBoard: string;
  readonly goMembers: string;
  readonly goAutopilot: string;
  readonly restoreOnboarding: string;
  readonly help: string;
  readonly copyDeepLink: string;
  readonly toggleFavorite: string;
  readonly markAllRead: string;
  readonly openApprovals: string;
  readonly openSettings: string;
  readonly openSettingsMembers: string;
  readonly openSettingsApprovals: string;
  readonly openSettingsFields: string;
  readonly openSettingsDanger: string;
}

export interface ShellShortcutEnv {
  /** 当前工作区 slug(无上下文时 null → 命令落扁平路由,经迁移解析) */
  readonly workspaceSlug: string | null;
  readonly workspaceId: string | null;
  /** 设置类命令门控(admin/owner 才注册,§1.2 S3 角色可见性矩阵) */
  readonly isAdmin: boolean;
}

const THEME_MODES: ReadonlyArray<ThemeMode> = ['light', 'dark', 'system'];

const NEXT_THEME: Record<ThemeMode, ThemeMode> = {
  light: 'dark',
  dark: 'system',
  system: 'light',
};

function focusTopbarSearch(): void {
  const el = document.querySelector<HTMLElement>(TOPBAR_SEARCH_SELECTOR);
  el?.focus();
}

/** 当前路径 → 可收藏目标(§6.19 四类);非资源路径 → null。 */
function favoriteTargetFromPath(pathname: string): { type: FavoriteTargetType; id: string } | null {
  const patterns: ReadonlyArray<{ type: FavoriteTargetType; pattern: RegExp }> = [
    { type: 'issue', pattern: /^\/w\/[^/]+\/issues\/([0-9a-f-]{36})$/ },
    { type: 'project', pattern: /^\/w\/[^/]+\/projects\/([^/]+)$/ },
    { type: 'view', pattern: /^\/w\/[^/]+\/views\/([^/]+)$/ },
    { type: 'chat_session', pattern: /^\/w\/[^/]+\/chat\/([^/]+)$/ },
  ];
  for (const { type, pattern } of patterns) {
    const match = pathname.match(pattern);
    if (match !== null && match[1] !== undefined) {
      return { type, id: match[1] };
    }
  }
  return null;
}

export function registerShellShortcuts(
  navigate: (to: string) => void,
  labels: ShellShortcutLabels,
  env: ShellShortcutEnv,
  overlay: { openHelp: () => void },
): () => void {
  const registry = useShortcutRegistry.getState();
  const setTheme = useSettingsStore.getState().setTheme;
  const unregisters: Array<() => void> = [];
  const client = getApiClient();

  // 规范深链优先;无工作区上下文时落扁平路由(FlatRouteMigration 解析 active ws)。
  const wsPath = (suffix: string): string =>
    env.workspaceSlug !== null ? `/w/${env.workspaceSlug}${suffix}` : suffix;

  const navRoutes: ReadonlyArray<{ key: NavKey; to: string }> = [
    { key: 'home', to: '/' },
    { key: 'inbox', to: wsPath('/inbox') },
    { key: 'projects', to: wsPath('/projects') },
    { key: 'issues', to: wsPath('/issues') },
    { key: 'board', to: wsPath('/board') },
    { key: 'members', to: wsPath('/members') },
    { key: 'chat', to: wsPath('/chat') },
    { key: 'squads', to: wsPath('/squads') },
    { key: 'autopilots', to: wsPath('/automations/autopilots') },
    // 自动化运营区三入口(§6.12 信息架构):自动值守 + 运行环境 + 技能市场。
    { key: 'runtimes', to: wsPath('/automations/runtimes') },
    { key: 'skills', to: wsPath('/automations/skills') },
    { key: 'insights', to: wsPath('/insights') },
    { key: 'approvals', to: wsPath('/approvals') },
    { key: 'settings', to: wsPath('/settings') },
  ];
  for (const { key, to } of navRoutes) {
    unregisters.push(
      registry.registerCommand({ id: 'nav.' + key, label: labels.nav[key], group: 'global', run: () => navigate(to) }),
    );
  }

  for (const mode of THEME_MODES) {
    unregisters.push(
      registry.registerCommand({
        id: 'theme.' + mode,
        label: labels.theme[mode],
        group: 'global',
        run: () => setTheme(mode),
      }),
    );
  }
  unregisters.push(
    registry.registerCommand({
      id: 'theme.toggle',
      label: labels.actions.themeToggle,
      group: 'global',
      run: () =>
        setTheme(NEXT_THEME[useSettingsStore.getState().preferences.theme ?? 'system']),
    }),
  );

  // 上手清单恢复(onboarding.md §4.2):失败静默(幂等,无可见副作用)。
  unregisters.push(
    registry.registerCommand({
      id: 'onboarding.restore',
      label: labels.actions.restoreOnboarding,
      group: 'global',
      run: () => {
        void restoreActiveOnboarding(client).catch(() => undefined);
      },
    }),
  );

  // 新建 issue(§1.2 S3 ④):既为快捷键 `c`(见下 shortcutDefs),亦须登记为面板
  // command 方可被搜索/执行(面板仅渲染 state.commands)。目标与 `c` 同:issues 页
  // 展开快速创建(issue.md §4.2)。
  unregisters.push(
    registry.registerCommand({
      id: 'issue.new',
      label: labels.actions.newIssue,
      group: 'global',
      combo: 'c',
      keywords: ['issue', 'new', 'create', 'xinjian'],
      run: () => navigate(wsPath('/issues?create=1')),
    }),
  );

  // 帮助层命令(? 快捷键的等价面板入口)。
  unregisters.push(
    registry.registerCommand({
      id: 'help.open',
      label: labels.actions.help,
      group: 'global',
      combo: '?',
      run: () => overlay.openHelp(),
    }),
  );

  // 复制当前深链(规范深链,§3.4;当前路径即规范形态)。
  unregisters.push(
    registry.registerCommand({
      id: 'copy.deep.link',
      label: labels.actions.copyDeepLink,
      group: 'global',
      run: () => {
        const url = window.location.origin + window.location.pathname + window.location.search;
        void navigator.clipboard.writeText(url).catch(() => undefined);
      },
    }),
  );

  // 收藏/取消收藏当前资源(§6.19;路由派生目标,best-effort)。
  unregisters.push(
    registry.registerCommand({
      id: 'favorite.toggle',
      label: labels.actions.toggleFavorite,
      group: 'global',
      run: () => {
        if (env.workspaceId === null) return;
        const target = favoriteTargetFromPath(window.location.pathname);
        if (target === null) return;
        const workspaceId = env.workspaceId;
        void (async () => {
          try {
            const existing = await listFavorites(client, workspaceId, target.type);
            const isFavorite = existing.some((entry) => entry.target_id === target.id);
            if (isFavorite) {
              await deleteFavorite(client, target.type, target.id);
            } else {
              await putFavorite(client, target.type, target.id);
            }
          } catch {
            // best-effort:收藏失败不打断用户(权威反馈在收藏端点错误码)。
          }
        })();
      },
    }),
  );

  // 标记全部已读——随当前收件箱视图 filter 口径(comment-inbox.md §3.2 同 filter)。
  // 无工作区上下文(workspaceId=null)时该命令恒为静默空操作,故根本不注册(与设置类
  // 命令同门控口径,§1.2 S3「无权/无上下文命令根本不注册」),避免命令面板出现死条目。
  if (env.workspaceId !== null) {
    unregisters.push(
      registry.registerCommand({
        id: 'mark.all.read',
        label: labels.actions.markAllRead,
        group: 'global',
        run: () => {
          const view = getCurrentInboxView();
          if (view.workspaceId === null) return;
          void readAll(client, view.workspaceId, view.filter).catch(() => undefined);
        },
      }),
    );
  }

  // 待审批命令(有工作区上下文即可见,§6.10 统一入口;导航命令已含 approvals,
  // 此条以动作语义并列呈现于命令面板动作区)。
  if (env.workspaceSlug !== null) {
    unregisters.push(
      registry.registerCommand({
        id: 'approvals.open',
        label: labels.actions.openApprovals,
        group: 'global',
        keywords: ['approval', 'shenpi'],
        run: () => navigate(wsPath('/approvals')),
      }),
    );
  }

  // 设置各子页命令:仅 admin/owner 注册并渲染(§1.2 S3 角色门控——无权命令
  // 根本不注册,不是「点击才报错」;guest/agent 永不可见)。
  if (env.workspaceSlug !== null && env.isAdmin) {
    const settingsCommands: ReadonlyArray<{ id: string; label: string; to: string }> = [
      { id: 'settings.open', label: labels.actions.openSettings, to: wsPath('/settings') },
      { id: 'settings.members', label: labels.actions.openSettingsMembers, to: wsPath('/settings/members') },
      { id: 'settings.approvals', label: labels.actions.openSettingsApprovals, to: wsPath('/settings/approvals') },
      { id: 'settings.fields', label: labels.actions.openSettingsFields, to: wsPath('/settings/fields') },
      { id: 'settings.danger', label: labels.actions.openSettingsDanger, to: wsPath('/settings/danger') },
    ];
    for (const command of settingsCommands) {
      unregisters.push(
        registry.registerCommand({
          id: command.id,
          label: command.label,
          group: 'global',
          run: () => navigate(command.to),
        }),
      );
    }
  }

  const goTo = (to: string) => () => navigate(to);
  const shortcutDefs: ReadonlyArray<ShortcutDef> = [
    { id: 'go.inbox', combo: 'g i', label: labels.actions.goInbox, group: 'global', run: goTo(wsPath('/inbox')) },
    { id: 'go.board', combo: 'g b', label: labels.actions.goBoard, group: 'global', run: goTo(wsPath('/board')) },
    { id: 'go.members', combo: 'g m', label: labels.actions.goMembers, group: 'global', run: goTo(wsPath('/members')) },
    {
      id: 'go.autopilot',
      combo: 'g a',
      label: labels.actions.goAutopilot,
      group: 'global',
      run: goTo(wsPath('/automations/autopilots')),
    },
    // `c` 打开 issues 页并展开快速创建(issue.md §4.2);看板上下文激活时由看板 handler 仲裁胜出。
    { id: 'new.issue', combo: 'c', label: labels.actions.newIssue, group: 'global', run: goTo(wsPath('/issues?create=1')) },
    { id: 'focus.search', combo: '/', label: labels.actions.focusSearch, group: 'global', run: focusTopbarSearch },
    // '?' 帮助层:分发层特判开启,此登记供帮助层自呈现当前有效键位。
    { id: 'help', combo: '?', label: labels.actions.help, group: 'global', run: () => overlay.openHelp() },
  ];
  unregisters.push(registry.registerShortcuts(shortcutDefs));

  return () => {
    for (const unregister of unregisters) unregister();
  };
}

/**
 * shell 快捷键注册编排组件:挂载于 AppShell 布局内(工作区路由命中时即位于
 * WorkspaceProvider 子树),从工作区上下文取 slug/role 门控命令注册;上下文
 * 变化(切换工作区/角色变更)自动重注册。
 */
export function ShellShortcutsRegistrar(): null {
  const navigate = useNavigate();
  const t = useT();
  const controls = useOverlayControls();
  const workspaceContext = useOptionalWorkspace();
  const workspace = workspaceContext !== null ? workspaceContext.workspace : null;
  const isAdmin = workspaceContext !== null ? workspaceContext.isAdmin : false;
  const slug = workspace !== null ? workspace.slug : null;
  const workspaceId = workspace !== null ? workspace.id : null;

  // active workspace 记忆(search-command-palette.md §3.4 解析序 ②):规范工作区
  // 上下文就绪即记录 mesh.last_workspace:{host}:{user},供旧扁平路由迁移/无上下文
  // 入口解析。纯体验增强,失败静默(服务端 last_active_workspace_id 经 RBAC 节流回填)。
  useEffect(() => {
    if (slug === null) return;
    let cancelled = false;
    void fetchMe(getApiClient())
      .then((me) => {
        if (!cancelled) recordLastWorkspace(me.user.id, slug);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [slug]);

  useEffect(
    () =>
      registerShellShortcuts(
        navigate,
        {
          nav: {
            home: t('nav.home'),
            inbox: t('nav.inbox'),
            projects: t('nav.projects'),
            issues: t('nav.issues'),
            board: t('nav.board'),
            members: t('nav.members'),
            chat: t('nav.chat'),
            squads: t('nav.squads'),
            autopilots: t('nav.autopilots'),
            runtimes: t('nav.runtimes'),
            skills: t('nav.skills'),
            insights: t('nav.insights'),
            approvals: t('nav.approvals'),
            settings: t('nav.settings'),
          },
          theme: {
            light: t('theme.light'),
            dark: t('theme.dark'),
            system: t('theme.system'),
          },
          actions: {
            themeToggle: t('a11y.themeToggle'),
            newIssue: t('shortcuts.actionNewIssue'),
            focusSearch: t('shortcuts.actionFocusSearch'),
            goInbox: t('shortcuts.actionGoInbox'),
            goBoard: t('shortcuts.actionGoBoard'),
            goMembers: t('shortcuts.actionGoMembers'),
            goAutopilot: t('shortcuts.actionGoAutopilot'),
            restoreOnboarding: t('onboarding.restoreHelp'),
            help: t('shortcuts.actionHelp'),
            copyDeepLink: t('shortcuts.actionCopyDeepLink'),
            toggleFavorite: t('shortcuts.actionToggleFavorite'),
            markAllRead: t('shortcuts.actionMarkAllRead'),
            openApprovals: t('shortcuts.actionOpenApprovals'),
            openSettings: t('shortcuts.actionOpenSettings'),
            openSettingsMembers: t('shortcuts.actionOpenSettingsMembers'),
            openSettingsApprovals: t('shortcuts.actionOpenSettingsApprovals'),
            openSettingsFields: t('shortcuts.actionOpenSettingsFields'),
            openSettingsDanger: t('shortcuts.actionOpenSettingsDanger'),
          },
        },
        { workspaceSlug: slug, workspaceId, isAdmin },
        { openHelp: () => controls?.openHelp() },
      ),
    [navigate, t, slug, workspaceId, isAdmin, controls],
  );

  return null;
}
