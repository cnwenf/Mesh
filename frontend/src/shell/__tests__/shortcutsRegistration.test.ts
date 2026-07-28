/**
 * shortcutsRegistration — 命令面板条目与快捷键注册/注销;主题命令写 settingsStore;/ 聚焦搜索。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useShortcutRegistry } from '../../shortcuts';
import { useSettingsStore } from '../../state/settingsStore';
import { registerShellShortcuts } from '../shortcutsRegistration';
import type { ShellShortcutLabels } from '../shortcutsRegistration';

const LABELS: ShellShortcutLabels = {
  nav: {
    home: 'Home',
    inbox: 'Inbox',
    projects: 'Projects',
    issues: 'Issues',
    board: 'Board',
    members: 'Members',
    chat: 'Chat',
    automation: 'Automation',
    insights: 'Insights',
    settings: 'Settings',
  },
  theme: { light: 'Light', dark: 'Dark', system: 'System' },
  actions: {
    themeToggle: 'Toggle theme',
    newIssue: 'New issue',
    focusSearch: 'Focus search',
    goInbox: 'Go to Inbox',
    goBoard: 'Go to Board',
    goMembers: 'Go to Members',
    goAutomation: 'Go to Automation',
    restoreOnboarding: 'Show the getting-started checklist again',
  },
};

describe('registerShellShortcuts', () => {
  beforeEach(() => {
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
    useSettingsStore.getState().resetPreferences();
  });

  it('注册导航命令、主题命令与快捷键', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS);
    const state = useShortcutRegistry.getState();
    // 10 导航(含 insights,MES-71) + 3 主题 + 1 切换 + 1 上手清单恢复(MES-69) = 15 命令
    expect(state.commands).toHaveLength(15);
    // g i / g b / g m / g a / c / / = 6 快捷键
    expect(state.shortcuts).toHaveLength(6);
    unregister();
  });

  it('onboarding.restore 命令经全局客户端恢复活跃工作区清单(onboarding.md §4.2)', async () => {
    const { fakeResponse } = await import('../../api/__tests__/fetchStub');
    const { resetApiClient } = await import('../../api/instance');
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      (async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.includes('/users/me')) {
          return fakeResponse({
            body: {
              data: {
                user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
                memberships: [
                  {
                    workspace_id: 'ws-1',
                    workspace_name: 'WS',
                    workspace_slug: 'ws',
                    role: 'owner',
                    status: 'active',
                    joined_at: null,
                  },
                ],
              },
            },
          });
        }
        return fakeResponse({ body: { data: { id: 'obs-1', dismissed_at: null } } });
      }) as typeof fetch,
    );
    resetApiClient();
    const unregister = registerShellShortcuts(vi.fn(), LABELS);
    useShortcutRegistry.getState().commands.find((command) => command.id === 'onboarding.restore')?.run();
    await vi.waitFor(() =>
      expect(calls.some((url) => url.includes('/onboarding/restore'))).toBe(true),
    );
    unregister();
    vi.unstubAllGlobals();
    resetApiClient();
  });

  it('导航命令调用 navigate(对应路由)', () => {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS);
    useShortcutRegistry.getState().commands.find((command) => command.id === 'nav.inbox')?.run();
    expect(navigate).toHaveBeenCalledWith('/inbox');
    useShortcutRegistry.getState().commands.find((command) => command.id === 'nav.home')?.run();
    expect(navigate).toHaveBeenCalledWith('/');
    useShortcutRegistry.getState().commands.find((command) => command.id === 'nav.issues')?.run();
    expect(navigate).toHaveBeenCalledWith('/issues');
    unregister();
  });

  it('全部命令均带非空字符串 label(label undefined 会使命令面板搜索整体崩溃,MES-45)', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS);
    const state = useShortcutRegistry.getState();
    expect(state.commands.length).toBeGreaterThan(0);
    for (const command of state.commands) {
      expect(typeof command.label).toBe('string');
      expect(command.label.length).toBeGreaterThan(0);
    }
    // issues 导航命令必须带映射文案,而非 undefined
    expect(useShortcutRegistry.getState().commands.find((command) => command.id === 'nav.issues')?.label).toBe(
      'Issues',
    );
    unregister();
  });

  it('主题命令与切换命令写 settingsStore', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS);
    useShortcutRegistry.getState().commands.find((command) => command.id === 'theme.dark')?.run();
    expect(useSettingsStore.getState().preferences.theme).toBe('dark');
    // dark → 切换 → system
    useShortcutRegistry.getState().commands.find((command) => command.id === 'theme.toggle')?.run();
    expect(useSettingsStore.getState().preferences.theme).toBe('system');
    unregister();
  });

  it('序列/裸键快捷键定义正确(g i 跳收件箱,c 打开 issues 快速创建)', () => {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS);
    const shortcuts = useShortcutRegistry.getState().shortcuts;
    shortcuts.find((def) => def.id === 'go.inbox')?.run();
    expect(navigate).toHaveBeenCalledWith('/inbox');
    shortcuts.find((def) => def.id === 'new.issue')?.run();
    expect(navigate).toHaveBeenCalledWith('/issues?create=1');
    unregister();
  });

  it('/ 快捷键聚焦顶栏搜索框', () => {
    document.body.innerHTML = '<input data-testid="topbar-search" />';
    const unregister = registerShellShortcuts(vi.fn(), LABELS);
    useShortcutRegistry.getState().shortcuts.find((def) => def.id === 'focus.search')?.run();
    expect(document.querySelector('[data-testid="topbar-search"]')).toBe(document.activeElement);
    unregister();
    document.body.innerHTML = '';
  });

  it('注销后命令与快捷键清空', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS);
    expect(useShortcutRegistry.getState().commands.length).toBeGreaterThan(0);
    unregister();
    expect(useShortcutRegistry.getState().commands).toHaveLength(0);
    expect(useShortcutRegistry.getState().shortcuts).toHaveLength(0);
  });
});
