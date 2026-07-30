/**
 * shell 级快捷键/命令注册(README §6.12,group 恒 'global')。
 *
 * - 命令面板条目:导航(首页/收件箱/项目/工作项/看板/成员/技能/聊天/自动值守/
 *   运行环境/洞察/设置——与侧栏同源口径,自动值守与运行环境各占明确入口,
 *   design-quality §4.1)+ 主题(light/dark/system/循环切换);主题经
 *   settingsStore.getState() 直接写入;
 * - 快捷键:G→I/B/M/A 序列跳收件箱/看板/成员/自动值守;`c` 打开 issues 页展开快速创建
 *   (issue.md §4.2);`/` 聚焦顶栏搜索;
 * - 所有快捷键均有等价鼠标路径(§6.12);返回合并注销函数。
 */
import { getApiClient } from '../api/instance';
import { restoreActiveOnboarding } from '../features/onboarding';
import { useShortcutRegistry } from '../shortcuts';
import type { ShortcutDef } from '../shortcuts';
import { useSettingsStore } from '../state/settingsStore';
import type { ThemeMode } from '../state/settingsStore';

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
  | 'skills'
  | 'chat'
  | 'autopilots'
  | 'runtimes'
  | 'insights'
  | 'settings';

export interface ShellShortcutLabels {
  /** nav.<key> 文案(必须覆盖全部 NavKey,缺一编译报错) */
  nav: Record<NavKey, string>;
  /** theme.<mode> 文案(light/dark/system) */
  theme: Record<string, string>;
  /** 动作类文案(themeToggle/newIssue/focusSearch/goInbox/goBoard/goMembers/goAutopilot) */
  actions: Record<string, string>;
}

const NAV_COMMAND_ROUTES: ReadonlyArray<{ key: NavKey; to: string }> = [
  { key: 'home', to: '/' },
  { key: 'inbox', to: '/inbox' },
  { key: 'projects', to: '/projects' },
  { key: 'issues', to: '/issues' },
  { key: 'board', to: '/board' },
  { key: 'members', to: '/members' },
  { key: 'skills', to: '/skills' },
  { key: 'chat', to: '/chat' },
  // 自动值守与运行环境各占明确入口(§4.1);旧含糊 'automation' 键已移除。
  { key: 'autopilots', to: '/autopilots' },
  { key: 'runtimes', to: '/runtimes' },
  { key: 'insights', to: '/insights' },
  { key: 'settings', to: '/settings' },
];

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

export function registerShellShortcuts(
  navigate: (to: string) => void,
  labels: ShellShortcutLabels,
): () => void {
  const registry = useShortcutRegistry.getState();
  const setTheme = useSettingsStore.getState().setTheme;
  const unregisters: Array<() => void> = [];

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
