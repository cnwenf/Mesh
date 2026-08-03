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
import { useLocation, useNavigate } from 'react-router';
import { getApiClient } from '../api/instance';
import { deleteFavorite, listFavorites, putFavorite } from '../api/favorites';
import type { FavoriteTargetType } from '../api/favorites';
import { useT } from '../i18n';
import { useToast } from '../design';
import { fetchMe } from '../features/members/api';
import { restoreActiveOnboarding } from '../features/onboarding';
import { getCurrentInboxView, readAll } from '../features/inbox';
import type { InboxFilter } from '../features/inbox/types';
import type { MemberRole } from '../features/members/types';
import { recordLastWorkspace } from '../workspace/lastWorkspace';
import { useShortcutRegistry } from '../shortcuts';
import type { ShortcutDef } from '../shortcuts';
import { useSettingsStore } from '../state/settingsStore';
import type { ThemeMode } from '../state/settingsStore';
import { useOptionalWorkspace } from '../workspace/WorkspaceProvider';
import {
  useWorkspaceFeatureFlagsContext,
  type WorkspaceFeatureFlags,
} from '../workspace/featureFlags';
import { useOverlayControls } from './AppShell';
import { findNavItem, resolveNavTarget } from './navigation';

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
  | 'integrations'
  | 'approvals'
  | 'settings';

export type SettingsKey =
  'general' | 'labels' | 'customFields' | 'data' | 'tokens' | 'audit' | 'danger';

export interface ShellShortcutLabels {
  /** nav.<key> 文案(必须覆盖全部 NavKey,缺一编译报错) */
  nav: Record<Exclude<NavKey, 'integrations'>, string> & Partial<Record<'integrations', string>>;
  /** theme.<mode> 文案(light/dark/system) */
  theme: Record<string, string>;
  /** 动作类文案(见 ShellActionLabels 键集) */
  actions: ShellActionLabels;
  /** main 设置中心新增的七个子页命令文案;旧调用方可省略。 */
  settings?: Record<SettingsKey, string>;
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
  readonly help?: string;
  readonly copyDeepLink: string;
  readonly toggleFavorite: string;
  readonly markAllRead: string;
  readonly openApprovals?: string;
  readonly openSettings?: string;
  readonly openSettingsMembers?: string;
  readonly openSettingsApprovals?: string;
  readonly openSettingsFields?: string;
  readonly openSettingsDanger?: string;
  readonly pendingApprovals?: string;
  readonly copiedDeepLink?: string;
  readonly markedAllRead?: string;
}

export interface ShellShortcutEnv {
  /** 当前工作区 slug(无上下文时 null → 命令落扁平路由,经迁移解析) */
  readonly workspaceSlug: string | null;
  readonly workspaceId: string | null;
  /** 设置类命令门控(admin/owner 才注册,§1.2 S3 角色可见性矩阵) */
  readonly isAdmin: boolean;
  /** 当前 pathname;registrar 注入后收藏命令可随路由重注册。 */
  readonly path?: string;
  readonly notify?: (message: string) => void;
  /** 工作区功能开关；显式关闭的功能不注册入口或快捷键。 */
  readonly featureFlags?: WorkspaceFeatureFlags;
}

/** lower-level 注册 API 的 main 兼容调用面。 */
export interface ShellShortcutOptions {
  readonly role?: MemberRole | null;
  readonly isHuman?: boolean;
  readonly workspaceSlug?: string | null;
  readonly workspaceId?: string | null;
  readonly path?: string;
  readonly notify?: (message: string) => void;
  readonly getInboxFilter?: () => InboxFilter | undefined;
  /** 工作区功能开关；显式关闭的功能不注册入口或快捷键。 */
  readonly featureFlags?: WorkspaceFeatureFlags;
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

export function isAdminRole(role: MemberRole | null | undefined): boolean {
  return role === 'owner' || role === 'admin';
}

export interface FavoritableTarget {
  readonly targetType: FavoriteTargetType;
  readonly targetId: string;
}

/** 当前规范/迁移路由 → 可收藏目标(§6.19 四类);非资源路径 → null。 */
export function parseFavoritablePath(pathname: string): FavoritableTarget | null {
  const patterns: ReadonlyArray<{ targetType: FavoriteTargetType; pattern: RegExp }> = [
    { targetType: 'issue', pattern: /^\/(?:w\/[^/]+\/)?issues\/([^/?#]+)\/?$/ },
    { targetType: 'project', pattern: /^\/(?:w\/[^/]+\/)?projects\/([^/?#]+)(?:\/|$)/ },
    { targetType: 'view', pattern: /^\/(?:w\/[^/]+\/)?views\/([^/?#]+)(?:\/|$)/ },
    { targetType: 'chat_session', pattern: /^\/(?:w\/[^/]+\/)?chat\/([^/?#]+)(?:\/|$)/ },
  ];
  for (const { targetType, pattern } of patterns) {
    const match = pathname.match(pattern);
    if (match !== null && match[1] !== undefined) {
      try {
        return { targetType, targetId: decodeURIComponent(match[1]) };
      } catch {
        return null;
      }
    }
  }
  return null;
}

function favoriteTargetFromPath(pathname: string): { type: FavoriteTargetType; id: string } | null {
  const target = parseFavoritablePath(pathname);
  return target === null ? null : { type: target.targetType, id: target.targetId };
}

function requireActionLabel(labels: ShellShortcutLabels, key: keyof ShellActionLabels): string {
  const value = labels.actions[key];
  if (value === undefined || value === '') {
    throw new Error(`Missing shell shortcut label: ${String(key)}`);
  }
  return value;
}

function resolveNavCommandTarget(key: NavKey, workspaceSlug: string | null): string {
  const item = findNavItem(key);
  if (item === undefined) {
    throw new Error(`Shell navigation entry missing: ${key}`);
  }
  return resolveNavTarget(item, workspaceSlug);
}

function registerLegacyShellShortcuts(
  navigate: (to: string) => void,
  labels: ShellShortcutLabels,
  env: ShellShortcutEnv,
  overlay: { openHelp: () => void },
): () => void {
  const registry = useShortcutRegistry.getState();
  const setTheme = useSettingsStore.getState().setTheme;
  const unregisters: Array<() => void> = [];
  const client = getApiClient();
  const appMode = env.path !== undefined;
  const autopilotEnabled = env.featureFlags?.autopilot !== false;

  // 规范深链优先;无工作区上下文时落扁平路由(FlatRouteMigration 解析 active ws)。
  const wsPath = (suffix: string): string =>
    env.workspaceSlug !== null ? `/w/${env.workspaceSlug}${suffix}` : suffix;

  const navKeys: ReadonlyArray<NavKey> = [
    'home',
    'inbox',
    'projects',
    'issues',
    'board',
    'members',
    'chat',
    'squads',
    'autopilots',
    'runtimes',
    'skills',
    'insights',
    'integrations',
    'approvals',
    'settings',
  ];
  for (const key of navKeys) {
    if (key === 'autopilots' && !autopilotEnabled) continue;
    const label = labels.nav[key];
    if (label === undefined) continue;
    const to = resolveNavCommandTarget(key, env.workspaceSlug);
    unregisters.push(
      registry.registerCommand({
        id: 'nav.' + key,
        label,
        group: 'global',
        run: () => navigate(to),
      }),
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
      run: () => setTheme(NEXT_THEME[useSettingsStore.getState().preferences.theme ?? 'system']),
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
      label: requireActionLabel(labels, 'help'),
      group: 'global',
      combo: '?',
      run: () => overlay.openHelp(),
    }),
  );

  // 复制当前深链(规范深链,§3.4;当前路径即规范形态)。
  unregisters.push(
    registry.registerCommand({
      id: appMode ? 'clipboard.copyDeepLink' : 'copy.deep.link',
      label: labels.actions.copyDeepLink,
      group: 'global',
      run: () => {
        const url = window.location.origin + window.location.pathname + window.location.search;
        const write = navigator.clipboard?.writeText(url);
        if (write !== undefined) {
          void write
            .then(() => {
              if (appMode && labels.actions.copiedDeepLink !== undefined) {
                env.notify?.(labels.actions.copiedDeepLink);
              }
            })
            .catch(() => undefined);
        }
      },
    }),
  );

  // 收藏/取消收藏当前资源(§6.19;路由派生目标,best-effort)。
  const initialFavoriteTarget = env.path === undefined ? null : favoriteTargetFromPath(env.path);
  if (!appMode || (env.workspaceId !== null && initialFavoriteTarget !== null)) {
    unregisters.push(
      registry.registerCommand({
        id: appMode ? 'favorites.toggle' : 'favorite.toggle',
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
              if (appMode) env.notify?.(labels.actions.toggleFavorite);
            } catch {
              // best-effort:收藏失败不打断用户(权威反馈在收藏端点错误码)。
            }
          })();
        },
      }),
    );
  }

  // 标记全部已读——随当前收件箱视图 filter 口径(comment-inbox.md §3.2 同 filter)。
  // 无工作区上下文(workspaceId=null)时该命令恒为静默空操作,故根本不注册(与设置类
  // 命令同门控口径,§1.2 S3「无权/无上下文命令根本不注册」),避免命令面板出现死条目。
  if (env.workspaceId !== null) {
    unregisters.push(
      registry.registerCommand({
        id: appMode ? 'inbox.markAllRead' : 'mark.all.read',
        label: labels.actions.markAllRead,
        group: 'global',
        run: () => {
          const view = getCurrentInboxView();
          if (view.workspaceId === null) return;
          void readAll(client, view.workspaceId, view.filter)
            .then(() => {
              if (appMode && labels.actions.markedAllRead !== undefined) {
                env.notify?.(labels.actions.markedAllRead);
              }
            })
            .catch(() => undefined);
        },
      }),
    );
  }

  // 待审批命令(有工作区上下文即可见,§6.10 统一入口;导航命令已含 approvals,
  // 此条以动作语义并列呈现于命令面板动作区)。
  if (env.workspaceSlug !== null) {
    unregisters.push(
      registry.registerCommand({
        id: appMode ? 'approvals.pending' : 'approvals.open',
        label: appMode
          ? (labels.actions.pendingApprovals ?? requireActionLabel(labels, 'openApprovals'))
          : requireActionLabel(labels, 'openApprovals'),
        group: 'global',
        keywords: ['approval', 'shenpi'],
        run: () => navigate(wsPath('/approvals')),
      }),
    );
  }

  // 设置各子页命令:仅 admin/owner 注册并渲染(§1.2 S3 角色门控——无权命令
  // 根本不注册,不是「点击才报错」;guest/agent 永不可见)。
  if (env.workspaceSlug !== null && env.isAdmin && !appMode) {
    const settingsCommands: ReadonlyArray<{ id: string; label: string; to: string }> = [
      {
        id: 'settings.open',
        label: requireActionLabel(labels, 'openSettings'),
        to: wsPath('/settings'),
      },
      {
        id: 'settings.members',
        label: requireActionLabel(labels, 'openSettingsMembers'),
        to: wsPath('/settings/members'),
      },
      {
        id: 'settings.approvals',
        label: requireActionLabel(labels, 'openSettingsApprovals'),
        to: wsPath('/settings/approvals'),
      },
      {
        id: 'settings.fields',
        label: requireActionLabel(labels, 'openSettingsFields'),
        to: wsPath('/settings/fields'),
      },
      {
        id: 'settings.danger',
        label: requireActionLabel(labels, 'openSettingsDanger'),
        to: wsPath('/settings/danger'),
      },
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

  if (env.workspaceSlug !== null && env.isAdmin && appMode && labels.settings !== undefined) {
    const settingsRoutes: ReadonlyArray<{ key: SettingsKey; suffix: string }> = [
      { key: 'general', suffix: '' },
      { key: 'labels', suffix: '/labels' },
      { key: 'customFields', suffix: '/custom-fields' },
      { key: 'data', suffix: '/data' },
      { key: 'tokens', suffix: '/tokens' },
      { key: 'audit', suffix: '/audit' },
      { key: 'danger', suffix: '/danger' },
    ];
    for (const { key, suffix } of settingsRoutes) {
      unregisters.push(
        registry.registerCommand({
          id: `settings.${key}`,
          label: labels.settings[key],
          group: 'global',
          keywords: ['settings', 'workspace'],
          run: () => navigate(wsPath(`/settings${suffix}`)),
        }),
      );
    }
  }

  const goTo = (to: string) => () => navigate(to);
  const shortcutDefs: ReadonlyArray<ShortcutDef> = [
    {
      id: 'go.inbox',
      combo: 'g i',
      label: labels.actions.goInbox,
      group: 'global',
      run: goTo(wsPath('/inbox')),
    },
    {
      id: 'go.board',
      combo: 'g b',
      label: labels.actions.goBoard,
      group: 'global',
      run: goTo(wsPath('/board')),
    },
    {
      id: 'go.members',
      combo: 'g m',
      label: labels.actions.goMembers,
      group: 'global',
      run: goTo(wsPath('/members')),
    },
    ...(autopilotEnabled
      ? [
          {
            id: 'go.autopilot',
            combo: 'g a',
            label: labels.actions.goAutopilot,
            group: 'global' as const,
            run: goTo(wsPath('/automations/autopilots')),
          },
        ]
      : []),
    // `c` 打开 issues 页并展开快速创建(issue.md §4.2);看板上下文激活时由看板 handler 仲裁胜出。
    {
      id: 'new.issue',
      combo: 'c',
      label: labels.actions.newIssue,
      group: 'global',
      run: goTo(wsPath('/issues?create=1')),
    },
    {
      id: 'focus.search',
      combo: '/',
      label: labels.actions.focusSearch,
      group: 'global',
      run: focusTopbarSearch,
    },
    // '?' 帮助层:分发层特判开启,此登记供帮助层自呈现当前有效键位。
    {
      id: 'help',
      combo: '?',
      label: requireActionLabel(labels, 'help'),
      group: 'global',
      run: () => overlay.openHelp(),
    },
  ];
  unregisters.push(registry.registerShortcuts(shortcutDefs));

  return () => {
    for (const unregister of unregisters) unregister();
  };
}

function registerModernShellShortcuts(
  navigate: (to: string) => void,
  labels: ShellShortcutLabels,
  options: ShellShortcutOptions,
): () => void {
  const registry = useShortcutRegistry.getState();
  const client = getApiClient();
  const setTheme = useSettingsStore.getState().setTheme;
  const unregisters: Array<() => void> = [];
  const { role = null, isHuman = true, workspaceSlug = null, workspaceId = null } = options;
  const autopilotEnabled = options.featureFlags?.autopilot !== false;
  const notify = options.notify ?? (() => undefined);
  const wsPath = (suffix: string): string =>
    workspaceSlug !== null ? `/w/${workspaceSlug}${suffix}` : suffix;

  const navKeys: ReadonlyArray<NavKey> = [
    'home',
    'inbox',
    'projects',
    'issues',
    'board',
    'members',
    'chat',
    'insights',
    'settings',
    'squads',
    'skills',
    'autopilots',
    'runtimes',
    'integrations',
    'approvals',
  ];
  for (const key of navKeys) {
    if (key === 'autopilots' && !autopilotEnabled) continue;
    const label = labels.nav[key];
    if (label === undefined) continue;
    const to = resolveNavCommandTarget(key, workspaceSlug);
    unregisters.push(
      registry.registerCommand({
        id: `nav.${key}`,
        label,
        group: 'global',
        run: () => navigate(to),
      }),
    );
  }

  if (isAdminRole(role) && isHuman && labels.settings !== undefined) {
    const settingsRoutes: ReadonlyArray<{ key: SettingsKey; suffix: string }> = [
      { key: 'general', suffix: '' },
      { key: 'labels', suffix: '/labels' },
      { key: 'customFields', suffix: '/custom-fields' },
      { key: 'data', suffix: '/data' },
      { key: 'tokens', suffix: '/tokens' },
      { key: 'audit', suffix: '/audit' },
      { key: 'danger', suffix: '/danger' },
    ];
    for (const { key, suffix } of settingsRoutes) {
      unregisters.push(
        registry.registerCommand({
          id: `settings.${key}`,
          label: labels.settings[key],
          group: 'global',
          keywords: ['settings', 'workspace'],
          run: () => navigate(wsPath(`/settings${suffix}`)),
        }),
      );
    }
  }

  unregisters.push(
    registry.registerCommand({
      id: 'approvals.pending',
      label: labels.actions.pendingApprovals ?? labels.nav.approvals,
      group: 'global',
      keywords: ['approval', 'approve'],
      run: () => navigate(wsPath('/approvals')),
    }),
  );

  for (const mode of THEME_MODES) {
    unregisters.push(
      registry.registerCommand({
        id: `theme.${mode}`,
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
      run: () => setTheme(NEXT_THEME[useSettingsStore.getState().preferences.theme ?? 'system']),
    }),
  );

  unregisters.push(
    registry.registerCommand({
      id: 'clipboard.copyDeepLink',
      label: labels.actions.copyDeepLink,
      group: 'global',
      keywords: ['copy', 'link', 'url'],
      run: () => {
        const write = navigator.clipboard?.writeText(window.location.href);
        if (write !== undefined) {
          void write
            .then(() => {
              if (labels.actions.copiedDeepLink !== undefined) {
                notify(labels.actions.copiedDeepLink);
              }
            })
            .catch(() => undefined);
        }
      },
    }),
  );

  const favoritable =
    workspaceId !== null && options.path !== undefined ? parseFavoritablePath(options.path) : null;
  if (favoritable !== null && workspaceId !== null) {
    unregisters.push(
      registry.registerCommand({
        id: 'favorites.toggle',
        label: labels.actions.toggleFavorite,
        group: 'global',
        keywords: ['favorite', 'pin', 'star'],
        run: () => {
          void (async () => {
            try {
              const existing = await listFavorites(client, workspaceId, favoritable.targetType);
              const found = existing.some((entry) => entry.target_id === favoritable.targetId);
              if (found) {
                await deleteFavorite(client, favoritable.targetType, favoritable.targetId);
              } else {
                await putFavorite(client, favoritable.targetType, favoritable.targetId);
              }
              notify(labels.actions.toggleFavorite);
            } catch {
              // best-effort; endpoint errors remain authoritative.
            }
          })();
        },
      }),
    );
  }

  if (workspaceId !== null) {
    unregisters.push(
      registry.registerCommand({
        id: 'inbox.markAllRead',
        label: labels.actions.markAllRead,
        group: 'global',
        keywords: ['inbox', 'read'],
        run: () => {
          void readAll(client, workspaceId, options.getInboxFilter?.())
            .then(() => {
              if (labels.actions.markedAllRead !== undefined) {
                notify(labels.actions.markedAllRead);
              }
            })
            .catch(() => undefined);
        },
      }),
    );
  }

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

  const goTo = (to: string) => () => navigate(to);
  unregisters.push(
    registry.registerShortcuts([
      {
        id: 'go.inbox',
        combo: 'g i',
        label: labels.actions.goInbox,
        group: 'global',
        run: goTo(wsPath('/inbox')),
      },
      {
        id: 'go.board',
        combo: 'g b',
        label: labels.actions.goBoard,
        group: 'global',
        run: goTo(wsPath('/board')),
      },
      {
        id: 'go.members',
        combo: 'g m',
        label: labels.actions.goMembers,
        group: 'global',
        run: goTo(wsPath('/members')),
      },
      ...(autopilotEnabled
        ? [
            {
              id: 'go.autopilot',
              combo: 'g a',
              label: labels.actions.goAutopilot,
              group: 'global' as const,
              run: goTo(wsPath('/automations/autopilots')),
            },
          ]
        : []),
      {
        id: 'new.issue',
        combo: 'c',
        label: labels.actions.newIssue,
        group: 'global',
        run: goTo(wsPath('/issues?create=1')),
      },
      {
        id: 'focus.search',
        combo: '/',
        label: labels.actions.focusSearch,
        group: 'global',
        run: focusTopbarSearch,
      },
    ]),
  );

  return () => {
    for (const unregister of unregisters) unregister();
  };
}

export function registerShellShortcuts(
  navigate: (to: string) => void,
  labels: ShellShortcutLabels,
  env: ShellShortcutEnv,
  overlay: { openHelp: () => void },
): () => void;
export function registerShellShortcuts(
  navigate: (to: string) => void,
  labels: ShellShortcutLabels,
  options?: ShellShortcutOptions,
): () => void;
export function registerShellShortcuts(
  navigate: (to: string) => void,
  labels: ShellShortcutLabels,
  context: ShellShortcutEnv | ShellShortcutOptions = {},
  overlay?: { openHelp: () => void },
): () => void {
  if (overlay !== undefined || 'isAdmin' in context) {
    return registerLegacyShellShortcuts(
      navigate,
      labels,
      context as ShellShortcutEnv,
      overlay ?? { openHelp: () => undefined },
    );
  }
  return registerModernShellShortcuts(navigate, labels, context);
}

/**
 * shell 快捷键注册编排组件:挂载于 AppShell 布局内(工作区路由命中时即位于
 * WorkspaceProvider 子树),从工作区上下文取 slug/role 门控命令注册;上下文
 * 变化(切换工作区/角色变更)自动重注册。
 */
export function ShellShortcutsRegistrar(): null {
  const navigate = useNavigate();
  const location = useLocation();
  const t = useT();
  const { addToast } = useToast();
  const controls = useOverlayControls();
  const featureFlags = useWorkspaceFeatureFlagsContext();
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
            integrations: t('nav.integrations'),
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
            pendingApprovals: t('shortcuts.actionPendingApprovals'),
            copiedDeepLink: t('shortcuts.copiedDeepLink'),
            markedAllRead: t('shortcuts.markedAllRead'),
            openApprovals: t('shortcuts.actionOpenApprovals'),
            openSettings: t('shortcuts.actionOpenSettings'),
            openSettingsMembers: t('shortcuts.actionOpenSettingsMembers'),
            openSettingsApprovals: t('shortcuts.actionOpenSettingsApprovals'),
            openSettingsFields: t('shortcuts.actionOpenSettingsFields'),
            openSettingsDanger: t('shortcuts.actionOpenSettingsDanger'),
          },
          settings: {
            general: t('shortcuts.settingsGeneral'),
            labels: t('shortcuts.settingsLabels'),
            customFields: t('shortcuts.settingsCustomFields'),
            data: t('shortcuts.settingsData'),
            tokens: t('shortcuts.settingsTokens'),
            audit: t('shortcuts.settingsAudit'),
            danger: t('shortcuts.settingsDanger'),
          },
        },
        {
          workspaceSlug: slug,
          workspaceId,
          isAdmin,
          path: location.pathname,
          featureFlags,
          notify: (message) =>
            addToast(message, { tone: 'success', closeLabel: t('a11y.closeDialog') }),
        },
        { openHelp: () => controls?.openHelp() },
      ),
    [
      navigate,
      t,
      slug,
      workspaceId,
      isAdmin,
      controls,
      location.pathname,
      featureFlags,
      addToast,
    ],
  );

  return null;
}
