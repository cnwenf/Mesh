/**
 * shell 级快捷键/命令注册(README §6.12,group 恒 'global')。
 *
 * 命令全集按 search-command-palette.md §1.2 S3 九组枚举:
 * ① 顶层导航(首页/收件箱/项目/工作项/看板/成员/聊天/小队/自动化运营区
 *    Autopilots·Runtimes·Skills·Integrations/统计/审批);
 * ② 设置各子页(**admin/owner 且人类成员才注册**——无权命令根本不注册,非「点击才报错」);
 * ③ 待审批统一入口;④ 新建 issue(快捷键 `c`);⑤ 主题 ×4;⑥ 复制当前深链;
 * ⑦ 收藏/取消收藏(仅当前路由可收藏时注册);⑧ 标记全部已读(随当前视图 filter,
 *    无 filter 即全量);⑨ 帮助层(`?`,经 ShortcutProvider 回调,不入命令表)。
 *
 * - 快捷键:G→I/B/M/A 序列跳收件箱/看板/成员/自动值守;`c` 打开 issues 页展开快速创建
 *   (issue.md §4.2);`/` 聚焦顶栏搜索;一切快捷键均有等价鼠标路径(§6.12);
 * - 注册接收调用方工作区角色/身份/路由上下文(可选项,缺省退化为成员视角 +
 *   无工作区命令);返回合并注销函数。
 */
import { getApiClient } from '../api/instance';
import { toggleFavoriteForTarget } from '../api/search';
import type { FavoriteTargetType } from '../api/search';
import { restoreActiveOnboarding } from '../features/onboarding';
import { readAll } from '../features/inbox/api';
import type { InboxFilter } from '../features/inbox/types';
import type { MemberRole } from '../features/members/types';
import { useShortcutRegistry } from '../shortcuts';
import type { ShortcutDef } from '../shortcuts';
import { useSettingsStore } from '../state/settingsStore';
import type { ThemeMode } from '../state/settingsStore';

export const TOPBAR_SEARCH_SELECTOR = '[data-testid="topbar-search"]';

/**
 * 导航命令键联合(与 NAV_COMMAND_ROUTES 显式一一对应)。
 * 标签映射收紧为该键的 Record:映射缺任一键即编译错误,
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
  | 'insights'
  | 'settings'
  | 'squads'
  | 'skills'
  | 'autopilots'
  | 'runtimes'
  | 'integrations'
  | 'approvals';

/** 设置子页命令键(§1.2 S3 ②;仅 admin/owner 注册) */
export type SettingsKey =
  | 'general'
  | 'labels'
  | 'customFields'
  | 'data'
  | 'tokens'
  | 'audit'
  | 'danger';

export interface ShellShortcutLabels {
  /** nav.<key> 文案(必须覆盖全部 NavKey,缺一编译报错) */
  nav: Record<NavKey, string>;
  /** theme.<mode> 文案(light/dark/system) */
  theme: Record<string, string>;
  /**
   * 动作类文案:themeToggle/newIssue/focusSearch/goInbox/goBoard/goMembers/
   * goAutopilot/restoreOnboarding + copyDeepLink/toggleFavorite/markAllRead/
   * pendingApprovals/copiedDeepLink/markedAllRead
   */
  actions: Record<string, string>;
  /** 设置子页文案(必须覆盖全部 SettingsKey) */
  settings: Record<SettingsKey, string>;
}

/** 注册上下文(均可选;缺省退化为成员视角、无工作区相关命令) */
export interface ShellShortcutOptions {
  /** 调用方当前工作区角色;设置子页/危险区仅 admin/owner 注册 */
  role?: MemberRole | null;
  /** 调用方为人类成员;false(agent 身份)时 admin 命令亦不注册 */
  isHuman?: boolean;
  /** 当前工作区 slug(设置子页规范路由);缺失时设置命令落账户设置兜底 */
  workspaceSlug?: string | null;
  /** 当前工作区 id(标记全部已读/收藏切换的作用域);缺失时这两条不注册 */
  workspaceId?: string | null;
  /** 当前路由 pathname(收藏切换仅对可收藏路由注册) */
  path?: string;
  /** 可见反馈注入(toast);缺失时静默 */
  notify?: (message: string) => void;
  /** 当前收件箱视图 filter(标记全部已读随视图口径;缺失 = 无 filter 全量) */
  getInboxFilter?: () => InboxFilter | undefined;
}

const NAV_COMMAND_ROUTES: ReadonlyArray<{ key: NavKey; to: string }> = [
  { key: 'home', to: '/' },
  { key: 'inbox', to: '/inbox' },
  { key: 'projects', to: '/projects' },
  { key: 'issues', to: '/issues' },
  { key: 'board', to: '/board' },
  { key: 'members', to: '/members' },
  { key: 'chat', to: '/chat' },
  { key: 'insights', to: '/insights' },
  { key: 'settings', to: '/settings' },
  { key: 'squads', to: '/squads' },
  { key: 'skills', to: '/skills' },
  // 自动值守与运行环境各占明确入口(design-quality §4.1,MES-115);
  // 旧含糊 'automation' 键已移除。
  { key: 'autopilots', to: '/autopilots' },
  { key: 'runtimes', to: '/runtimes' },
  { key: 'integrations', to: '/integrations' },
  { key: 'approvals', to: '/approvals' },
];

const THEME_MODES: ReadonlyArray<ThemeMode> = ['light', 'dark', 'system'];

const NEXT_THEME: Record<ThemeMode, ThemeMode> = {
  light: 'dark',
  dark: 'system',
  system: 'light',
};

const SETTINGS_ROUTES: ReadonlyArray<{ key: SettingsKey; suffix: string }> = [
  { key: 'general', suffix: '' },
  { key: 'labels', suffix: '/labels' },
  { key: 'customFields', suffix: '/custom-fields' },
  { key: 'data', suffix: '/data' },
  { key: 'tokens', suffix: '/tokens' },
  { key: 'audit', suffix: '/audit' },
  { key: 'danger', suffix: '/danger' },
];

function focusTopbarSearch(): void {
  const el = document.querySelector<HTMLElement>(TOPBAR_SEARCH_SELECTOR);
  el?.focus();
}

/** admin/owner 判定(设置子页与危险区命令的注册门控,§1.2 S3) */
export function isAdminRole(role: MemberRole | null | undefined): boolean {
  return role === 'owner' || role === 'admin';
}

export interface FavoritableTarget {
  readonly targetType: FavoriteTargetType;
  readonly targetId: string;
}

/**
 * 当前路由可收藏目标解析(§6.19 四类目标):issues/projects/views/chat 详情路由 →
 * 目标类型 + id;非可收藏路由(含 issue by-identifier 形态,需异步解析 id)→ null。
 */
export function parseFavoritablePath(path: string): FavoritableTarget | null {
  const patterns: ReadonlyArray<{ targetType: FavoriteTargetType; pattern: RegExp }> = [
    { targetType: 'issue', pattern: /^\/issues\/([^/?#]+)\/?$/ },
    { targetType: 'project', pattern: /^\/projects\/([^/?#]+)/ },
    { targetType: 'view', pattern: /^\/views\/([^/?#]+)/ },
    { targetType: 'chat_session', pattern: /^\/chat\/([^/?#]+)/ },
  ];
  for (const { targetType, pattern } of patterns) {
    const match = pattern.exec(path);
    if (match !== null && match[1] !== undefined && match[1] !== '') {
      return { targetType, targetId: decodeURIComponent(match[1]) };
    }
  }
  return null;
}

function settingsBasePath(workspaceSlug: string | null | undefined): string {
  return workspaceSlug !== null && workspaceSlug !== undefined && workspaceSlug !== ''
    ? `/w/${workspaceSlug}/settings`
    : '/settings';
}

export function registerShellShortcuts(
  navigate: (to: string) => void,
  labels: ShellShortcutLabels,
  options: ShellShortcutOptions = {},
): () => void {
  const registry = useShortcutRegistry.getState();
  const setTheme = useSettingsStore.getState().setTheme;
  const unregisters: Array<() => void> = [];
  const { role = null, isHuman = true, workspaceSlug = null, workspaceId = null } = options;
  const notify = options.notify ?? (() => undefined);

  for (const { key, to } of NAV_COMMAND_ROUTES) {
    unregisters.push(
      registry.registerCommand({
        id: 'nav.' + key,
        label: labels.nav[key],
        group: 'global',
        run: () => navigate(to),
      }),
    );
  }

  // ② 设置各子页:仅 admin/owner 且人类成员注册(无权命令根本不注册,§1.2 S3)
  if (isAdminRole(role) && isHuman) {
    const base = settingsBasePath(workspaceSlug);
    for (const { key, suffix } of SETTINGS_ROUTES) {
      const to = suffix === '' ? base : `${base}${suffix}`;
      unregisters.push(
        registry.registerCommand({
          id: 'settings.' + key,
          label: labels.settings[key],
          group: 'global',
          keywords: ['settings', 'workspace'],
          run: () => navigate(to),
        }),
      );
    }
  }

  // ③ 待审批统一入口(§6.10)
  unregisters.push(
    registry.registerCommand({
      id: 'approvals.pending',
      label: labels.actions.pendingApprovals,
      group: 'global',
      keywords: ['approval', 'approve'],
      run: () => navigate('/approvals'),
    }),
  );

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

  // ⑥ 复制当前规范深链(§3.4;clipboard 不可用时显式吞错——命令无破坏性副作用)
  unregisters.push(
    registry.registerCommand({
      id: 'clipboard.copyDeepLink',
      label: labels.actions.copyDeepLink,
      group: 'global',
      keywords: ['copy', 'link', 'url'],
      run: () => {
        const write = navigator.clipboard?.writeText(window.location.href);
        if (write !== undefined) {
          write
            .then(() => notify(labels.actions.copiedDeepLink))
            .catch(() => undefined);
        }
      },
    }),
  );

  // ⑦ 收藏/取消收藏:仅当前路由可收藏且有工作区作用域时注册(§6.19)
  const favoritable =
    workspaceId !== null && options.path !== undefined
      ? parseFavoritablePath(options.path)
      : null;
  if (favoritable !== null && workspaceId !== null) {
    const { targetType, targetId } = favoritable;
    unregisters.push(
      registry.registerCommand({
        id: 'favorites.toggle',
        label: labels.actions.toggleFavorite,
        group: 'global',
        keywords: ['favorite', 'pin', 'star'],
        run: () => {
          void toggleFavoriteForTarget(getApiClient(), workspaceId, targetType, targetId)
            .then(() => notify(labels.actions.toggleFavorite))
            .catch(() => undefined);
        },
      }),
    );
  }

  // ⑧ 标记全部已读:随当前视图 filter 口径(comment-inbox.md);无 filter = 全量
  if (workspaceId !== null) {
    unregisters.push(
      registry.registerCommand({
        id: 'inbox.markAllRead',
        label: labels.actions.markAllRead,
        group: 'global',
        keywords: ['inbox', 'read'],
        run: () => {
          const filter = options.getInboxFilter?.();
          void readAll(getApiClient(), workspaceId, filter)
            .then(() => notify(labels.actions.markedAllRead))
            .catch(() => undefined);
        },
      }),
    );
  }

  // 上手清单恢复(onboarding.md §4.2):与帮助菜单入口同一编排;失败静默(幂等,无可见副作用)
  unregisters.push(
    registry.registerCommand({
      id: 'onboarding.restore',
      label: labels.actions.restoreOnboarding,
      group: 'global',
      run: () => {
        void restoreActiveOnboarding(getApiClient()).catch(() => undefined);
      },
    }),
  );

  const goTo = (to: string) => () => navigate(to);
  const shortcutDefs: ReadonlyArray<ShortcutDef> = [
    { id: 'go.inbox', combo: 'g i', label: labels.actions.goInbox, group: 'global', run: goTo('/inbox') },
    { id: 'go.board', combo: 'g b', label: labels.actions.goBoard, group: 'global', run: goTo('/board') },
    {
      id: 'go.members',
      combo: 'g m',
      label: labels.actions.goMembers,
      group: 'global',
      run: goTo('/members'),
    },
    {
      id: 'go.autopilot',
      combo: 'g a',
      label: labels.actions.goAutopilot,
      group: 'global',
      run: goTo('/autopilots'),
    },
    // `c` 打开 issues 页并展开快速创建(issue.md §4.2)
    { id: 'new.issue', combo: 'c', label: labels.actions.newIssue, group: 'global', run: goTo('/issues?create=1') },
    { id: 'focus.search', combo: '/', label: labels.actions.focusSearch, group: 'global', run: focusTopbarSearch },
  ];
  unregisters.push(registry.registerShortcuts(shortcutDefs));

  return () => {
    for (const unregister of unregisters) unregister();
  };
}
