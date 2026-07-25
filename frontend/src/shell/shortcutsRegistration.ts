/**
 * shell 级快捷键/命令注册(README §6.12,group 恒 'global')。
 *
 * - 命令面板条目:导航(首页/收件箱/项目/看板/成员/聊天/自动化/设置)+
 *   主题(light/dark/system/循环切换);主题经 settingsStore.getState() 直接写入;
 * - 快捷键:G→I/B/M/A 序列跳收件箱/看板/成员/自动化;`c` 新建工作项(骨架阶段导航首页,
 *   真实新建归阶段 2);`/` 聚焦顶栏搜索;
 * - 所有快捷键均有等价鼠标路径(§6.12);返回合并注销函数。
 */
import { useShortcutRegistry } from '../shortcuts';
import type { ShortcutDef } from '../shortcuts';
import { useSettingsStore } from '../state/settingsStore';
import type { ThemeMode } from '../state/settingsStore';

export const TOPBAR_SEARCH_SELECTOR = '[data-testid="topbar-search"]';

export interface ShellShortcutLabels {
  /** nav.<key> 文案(home/inbox/projects/board/members/chat/automation/settings) */
  nav: Record<string, string>;
  /** theme.<mode> 文案(light/dark/system) */
  theme: Record<string, string>;
  /** 动作类文案(themeToggle/newIssue/focusSearch/goInbox/goBoard/goMembers/goAutomation) */
  actions: Record<string, string>;
}

const NAV_COMMAND_ROUTES: ReadonlyArray<{ key: string; to: string }> = [
  { key: 'home', to: '/' },
  { key: 'inbox', to: '/inbox' },
  { key: 'projects', to: '/projects' },
  { key: 'board', to: '/board' },
  { key: 'members', to: '/members' },
  { key: 'chat', to: '/chat' },
  { key: 'automation', to: '/automation' },
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
      run: () => setTheme(NEXT_THEME[useSettingsStore.getState().preferences.theme]),
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
      id: 'go.automation',
      combo: 'g a',
      label: labels.actions.goAutomation,
      group: 'global',
      run: goTo('/automation'),
    },
    // 骨架阶段:`c` 导航首页(真实「新建工作项」归阶段 2)
    { id: 'new.issue', combo: 'c', label: labels.actions.newIssue, group: 'global', run: goTo('/') },
    { id: 'focus.search', combo: '/', label: labels.actions.focusSearch, group: 'global', run: focusTopbarSearch },
  ];
  unregisters.push(registry.registerShortcuts(shortcutDefs));

  return () => {
    for (const unregister of unregisters) unregister();
  };
}
