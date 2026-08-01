/**
 * shortcutsRegistration — 命令全集(§1.2 S3 九组)/角色门控(设置子页 admin 限定)/
 * 上下文条件命令(收藏切换按路由、标记已读按工作区)/主题写入/快捷键定义。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useShortcutRegistry } from '../../shortcuts';
import { useSettingsStore } from '../../state/settingsStore';
import {
  isAdminRole,
  parseFavoritablePath,
  registerShellShortcuts,
} from '../shortcutsRegistration';
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
    autopilots: 'Autopilots',
    insights: 'Insights',
    settings: 'Settings',
    squads: 'Squads',
    skills: 'Skills',
    runtimes: 'Runtimes',
    integrations: 'Integrations',
    approvals: 'Approvals',
  },
  theme: { light: 'Light', dark: 'Dark', system: 'System' },
  actions: {
    themeToggle: 'Toggle theme',
    newIssue: 'New issue',
    focusSearch: 'Focus search',
    goInbox: 'Go to Inbox',
    goBoard: 'Go to Board',
    goMembers: 'Go to Members',
    goAutopilot: 'Go to Autopilots',
    restoreOnboarding: 'Show the getting-started checklist again',
    pendingApprovals: 'Pending approvals',
    copyDeepLink: 'Copy deep link',
    toggleFavorite: 'Toggle favorite',
    markAllRead: 'Mark all as read',
    copiedDeepLink: 'Link copied',
    markedAllRead: 'Marked all as read',
  },
  settings: {
    general: 'Workspace settings',
    labels: 'Labels',
    customFields: 'Custom fields',
    data: 'Data',
    tokens: 'Tokens',
    audit: 'Audit',
    danger: 'Danger zone',
  },
};

function commandIds(): string[] {
  return useShortcutRegistry.getState().commands.map((command) => command.id);
}

describe('registerShellShortcuts', () => {
  beforeEach(() => {
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
    useSettingsStore.getState().resetPreferences();
  });

  it('成员视角:导航(含 squads/skills/runtimes/integrations/approvals)+ 主题 + 动作命令齐全,无设置子页', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS, { role: 'member' });
    const ids = commandIds();
    // 15 导航 + 1 待审批 + 3 主题 + 1 切换 + 1 复制深链 + 1 上手清单 = 22(无工作区 → 无标记已读/收藏)
    expect(ids).toContain('nav.squads');
    expect(ids).toContain('nav.skills');
    expect(ids).toContain('nav.runtimes');
    expect(ids).toContain('nav.integrations');
    expect(ids).toContain('nav.approvals');
    expect(ids).toContain('approvals.pending');
    expect(ids).toContain('clipboard.copyDeepLink');
    expect(ids.filter((id) => id.startsWith('settings.'))).toHaveLength(0);
    expect(ids).not.toContain('inbox.markAllRead');
    expect(ids).not.toContain('favorites.toggle');
    expect(useShortcutRegistry.getState().shortcuts).toHaveLength(6);
    unregister();
  });

  it('可选导航文案缺失时跳过该命令,待审批文案回退到导航文案', () => {
    const { integrations, ...nav } = LABELS.nav;
    const { pendingApprovals, ...actions } = LABELS.actions;
    void integrations;
    void pendingApprovals;
    const labels: ShellShortcutLabels = { ...LABELS, nav, actions };
    const unregister = registerShellShortcuts(vi.fn(), labels);

    expect(commandIds()).not.toContain('nav.integrations');
    expect(
      useShortcutRegistry.getState().commands.find((command) => command.id === 'approvals.pending')
        ?.label,
    ).toBe('Approvals');
    unregister();
  });

  it('admin/owner + 工作区:设置七子页注册且落工作区规范路由;guest 不注册(§1.2 S3 门控)', () => {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS, {
      role: 'admin',
      isHuman: true,
      workspaceSlug: 'acme',
      workspaceId: 'ws-1',
      path: '/',
    });
    const ids = commandIds();
    const settingsIds = ids.filter((id) => id.startsWith('settings.'));
    expect(settingsIds).toHaveLength(7);
    expect(settingsIds).toContain('settings.danger');
    expect(ids).toContain('inbox.markAllRead');
    // 设置命令导航到 /w/acme/settings 子页
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'settings.labels')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/w/acme/settings/labels');
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'settings.general')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/w/acme/settings');
    unregister();

    // guest:设置子页不注册(不是「点击才报错」)
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
    const unregisterGuest = registerShellShortcuts(vi.fn(), LABELS, {
      role: 'guest',
      workspaceId: 'ws-1',
      path: '/',
    });
    expect(commandIds().filter((id) => id.startsWith('settings.'))).toHaveLength(0);
    unregisterGuest();
  });

  it('agent 身份(isHuman=false)即使是 admin 也不注册设置命令', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS, {
      role: 'admin',
      isHuman: false,
      workspaceId: 'ws-1',
      path: '/',
    });
    expect(commandIds().filter((id) => id.startsWith('settings.'))).toHaveLength(0);
    unregister();
  });

  it('isAdminRole 判定', () => {
    expect(isAdminRole('owner')).toBe(true);
    expect(isAdminRole('admin')).toBe(true);
    expect(isAdminRole('member')).toBe(false);
    expect(isAdminRole('guest')).toBe(false);
    expect(isAdminRole(null)).toBe(false);
  });

  it('favorites.toggle 仅对可收藏路由注册;run 经 favorites 端点切换', async () => {
    const { fakeResponse } = await import('../../api/__tests__/fetchStub');
    const { resetApiClient } = await import('../../api/instance');
    const calls: Array<{ url: string; method?: string }> = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method });
      if (url.includes('/api/v1/favorites') && (init?.method ?? 'GET') === 'GET') {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return fakeResponse({ status: 204 });
    }) as typeof fetch);
    resetApiClient();

    const unregister = registerShellShortcuts(vi.fn(), LABELS, {
      role: 'member',
      workspaceId: 'ws-1',
      path: '/issues/iss-9',
    });
    expect(commandIds()).toContain('favorites.toggle');
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'favorites.toggle')
      ?.run();
    await vi.waitFor(() =>
      expect(
        calls.some((call) => call.url.includes('/favorites/issue/iss-9') && call.method === 'PUT'),
      ).toBe(true),
    );
    unregister();

    // 非可收藏路由 → 不注册
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
    const unregister2 = registerShellShortcuts(vi.fn(), LABELS, {
      role: 'member',
      workspaceId: 'ws-1',
      path: '/board',
    });
    expect(commandIds()).not.toContain('favorites.toggle');
    unregister2();
    vi.unstubAllGlobals();
    resetApiClient();
  });

  it('favorites.toggle 对已收藏资源发送 DELETE', async () => {
    const { fakeResponse } = await import('../../api/__tests__/fetchStub');
    const { resetApiClient } = await import('../../api/instance');
    const calls: Array<{ url: string; method?: string }> = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method });
      if (url.includes('/api/v1/favorites') && (init?.method ?? 'GET') === 'GET') {
        return fakeResponse({
          body: {
            data: [{ target_type: 'issue', target_id: 'iss-9' }],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ status: 204 });
    }) as typeof fetch);
    resetApiClient();

    const unregister = registerShellShortcuts(vi.fn(), LABELS, {
      role: 'member',
      workspaceId: 'ws-1',
      path: '/issues/iss-9',
    });
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'favorites.toggle')
      ?.run();
    await vi.waitFor(() =>
      expect(
        calls.some(
          (call) => call.url.includes('/favorites/issue/iss-9') && call.method === 'DELETE',
        ),
      ).toBe(true),
    );

    unregister();
    vi.unstubAllGlobals();
    resetApiClient();
  });

  it('favorites.toggle 端点失败时保持静默且不通知', async () => {
    const { fakeResponse } = await import('../../api/__tests__/fetchStub');
    const { resetApiClient } = await import('../../api/instance');
    const calls: string[] = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return fakeResponse({ status: 500, body: { error: { code: 'internal_error' } } });
    }) as typeof fetch);
    resetApiClient();
    const notify = vi.fn();

    const unregister = registerShellShortcuts(vi.fn(), LABELS, {
      role: 'member',
      workspaceId: 'ws-1',
      path: '/issues/iss-9',
      notify,
    });
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'favorites.toggle')
      ?.run();
    await vi.waitFor(() =>
      expect(calls.some((url) => url.includes('/api/v1/favorites'))).toBe(true),
    );
    await Promise.resolve();
    expect(notify).not.toHaveBeenCalled();

    unregister();
    vi.unstubAllGlobals();
    resetApiClient();
  });

  it('inbox.markAllRead 随注入的视图 filter 发送(comment-inbox.md 同口径)', async () => {
    const { fakeResponse } = await import('../../api/__tests__/fetchStub');
    const { resetApiClient } = await import('../../api/instance');
    const bodies: string[] = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      bodies.push(String(init?.body ?? ''));
      if (String(input).includes('/users/me')) {
        return fakeResponse({
          body: {
            data: {
              user: { id: 'u', email: 'e', display_name: 'd' },
              memberships: [],
            },
          },
        });
      }
      return fakeResponse({ body: { data: { updated: 3 } } });
    }) as typeof fetch);
    resetApiClient();
    const notify = vi.fn();
    const unregister = registerShellShortcuts(vi.fn(), LABELS, {
      role: 'member',
      workspaceId: 'ws-1',
      path: '/inbox',
      notify,
      getInboxFilter: () => 'unread',
    });
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'inbox.markAllRead')
      ?.run();
    await vi.waitFor(() =>
      expect(bodies.some((body) => body.includes('"filter":"unread"'))).toBe(true),
    );
    await vi.waitFor(() => expect(notify).toHaveBeenCalledWith('Marked all as read'));
    unregister();
    vi.unstubAllGlobals();
    resetApiClient();
  });

  it('clipboard.copyDeepLink 复制当前 URL 并经 notify 反馈', async () => {
    const written: string[] = [];
    Object.defineProperty(window.navigator, 'clipboard', {
      value: { writeText: async (text: string) => void written.push(text) },
      configurable: true,
    });
    const notify = vi.fn();
    const unregister = registerShellShortcuts(vi.fn(), LABELS, { notify });
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'clipboard.copyDeepLink')
      ?.run();
    await vi.waitFor(() => expect(written).toHaveLength(1));
    expect(written[0]).toBe(window.location.href);
    await vi.waitFor(() => expect(notify).toHaveBeenCalledWith('Link copied'));
    unregister();
  });

  it('导航命令调用 navigate(对应路由);全部命令 label 非空(MES-45 守卫)', () => {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS);
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'nav.inbox')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/inbox');
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'nav.squads')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/squads');
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'approvals.pending')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/approvals');
    for (const command of useShortcutRegistry.getState().commands) {
      expect(typeof command.label).toBe('string');
      expect(command.label.length).toBeGreaterThan(0);
    }
    unregister();
  });

  it('主题命令与切换命令写 settingsStore', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS);
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'theme.dark')
      ?.run();
    expect(useSettingsStore.getState().preferences.theme).toBe('dark');
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'theme.toggle')
      ?.run();
    expect(useSettingsStore.getState().preferences.theme).toBe('system');
    unregister();
  });

  it('序列/裸键快捷键定义正确(g i 跳收件箱,c 打开 issues 快速创建,/ 聚焦搜索)', () => {
    document.body.innerHTML = '<input data-testid="topbar-search" />';
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS);
    const shortcuts = useShortcutRegistry.getState().shortcuts;
    shortcuts.find((def) => def.id === 'go.inbox')?.run();
    expect(navigate).toHaveBeenCalledWith('/inbox');
    shortcuts.find((def) => def.id === 'new.issue')?.run();
    expect(navigate).toHaveBeenCalledWith('/issues?create=1');
    shortcuts.find((def) => def.id === 'focus.search')?.run();
    expect(document.querySelector('[data-testid="topbar-search"]')).toBe(document.activeElement);
    unregister();
    document.body.innerHTML = '';
  });

  it('注销后命令与快捷键清空;再注册幂等(同 id 替换)', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS);
    const firstCount = useShortcutRegistry.getState().commands.length;
    const unregister2 = registerShellShortcuts(vi.fn(), LABELS);
    expect(useShortcutRegistry.getState().commands).toHaveLength(firstCount);
    unregister2();
    unregister();
    expect(useShortcutRegistry.getState().commands).toHaveLength(0);
    expect(useShortcutRegistry.getState().shortcuts).toHaveLength(0);
  });

  it('onboarding.restore 命令经全局客户端恢复活跃工作区清单(onboarding.md §4.2)', async () => {
    const { fakeResponse } = await import('../../api/__tests__/fetchStub');
    const { resetApiClient } = await import('../../api/instance');
    const calls: string[] = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
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
    }) as typeof fetch);
    resetApiClient();
    const unregister = registerShellShortcuts(vi.fn(), LABELS);
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'onboarding.restore')
      ?.run();
    await vi.waitFor(() =>
      expect(calls.some((url) => url.includes('/onboarding/restore'))).toBe(true),
    );
    unregister();
    vi.unstubAllGlobals();
    resetApiClient();
  });
});

describe('parseFavoritablePath', () => {
  it('issues/projects/views/chat 详情路由解析目标;其余 → null', () => {
    expect(parseFavoritablePath('/issues/iss-1')).toEqual({
      targetType: 'issue',
      targetId: 'iss-1',
    });
    expect(parseFavoritablePath('/projects/p-1/settings')).toEqual({
      targetType: 'project',
      targetId: 'p-1',
    });
    expect(parseFavoritablePath('/views/v%201')).toEqual({ targetType: 'view', targetId: 'v 1' });
    expect(parseFavoritablePath('/chat/s-1')).toEqual({
      targetType: 'chat_session',
      targetId: 's-1',
    });
    expect(parseFavoritablePath('/board')).toBeNull();
    expect(parseFavoritablePath('/issues')).toBeNull();
    expect(parseFavoritablePath('/issues/by-identifier/WEB-1')).toBeNull();
  });
});
