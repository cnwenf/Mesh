/**
 * shortcutsRegistration — 命令面板条目与快捷键注册/注销;主题命令写 settingsStore;/ 聚焦搜索。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useShortcutRegistry } from '../../shortcuts';
import { useSettingsStore } from '../../state/settingsStore';
import { registerShellShortcuts } from '../shortcutsRegistration';
import type { ShellShortcutEnv, ShellShortcutLabels } from '../shortcutsRegistration';
import { MeshApiClient } from '../../api/client';
import { renderWithProviders } from '../../test-utils/render';
import { WorkspaceProvider } from '../../workspace/WorkspaceProvider';
import { OverlayControlsProvider } from '../AppShell';
import { ShellShortcutsRegistrar } from '../shortcutsRegistration';

vi.mock('../../api/favorites', () => ({
  listFavorites: vi.fn(async () => []),
  putFavorite: vi.fn(async () => undefined),
  deleteFavorite: vi.fn(async () => undefined),
}));
vi.mock('../../features/inbox', () => ({
  getCurrentInboxView: vi.fn(() => ({ workspaceId: 'ws-1', filter: 'all' })),
  readAll: vi.fn(async () => undefined),
}));

const LABELS: ShellShortcutLabels = {
  nav: {
    home: 'Home',
    inbox: 'Inbox',
    projects: 'Projects',
    issues: 'Issues',
    board: 'Board',
    members: 'Members',
    skills: 'Skills',
    chat: 'Chat',
    squads: 'Squads',
    autopilots: 'Autopilots',
    runtimes: 'Runtimes',
    insights: 'Insights',
    integrations: 'Integrations',
    approvals: 'Approvals',
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
    goAutopilot: 'Go to Autopilots',
    restoreOnboarding: 'Show the getting-started checklist again',
    help: 'Show shortcuts',
    copyDeepLink: 'Copy link to current page',
    toggleFavorite: 'Favorite / unfavorite current resource',
    markAllRead: 'Mark all as read',
    openApprovals: 'Pending approvals',
    openSettings: 'Workspace settings',
    openSettingsMembers: 'Settings · Members & roles',
    openSettingsApprovals: 'Settings · Approval policies',
    openSettingsFields: 'Settings · Statuses & fields',
    openSettingsDanger: 'Settings · Danger zone',
  },
};

/** 无工作区上下文(member/guest 视角):设置类命令不注册。 */
const ENV_PLAIN: ShellShortcutEnv = { workspaceSlug: null, workspaceId: null, isAdmin: false };
/** 工作区上下文 + admin:设置各子页命令全注册(§1.2 S3 角色门控)。 */
const ENV_ADMIN: ShellShortcutEnv = { workspaceSlug: 'acme', workspaceId: 'ws-1', isAdmin: true };
const OVERLAY = { openHelp: () => undefined };

describe('registerShellShortcuts', () => {
  beforeEach(() => {
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
    useSettingsStore.getState().resetPreferences();
  });

  it('注册导航命令、主题命令与快捷键', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS, ENV_PLAIN, OVERLAY);
    const state = useShortcutRegistry.getState();
    // 15 导航(+审批 / 洞察 / 小队 / Runtimes / Skills / Integrations) +
    // 3 主题 + 1 切换 + 上手清单恢复 + 新建 issue(面板命令) + 帮助层 + 复制深链 +
    // 收藏切换 = 24 命令(无工作区上下文口径;标记全部已读 M10 门控,workspaceId=null 不注册)
    expect(state.commands).toHaveLength(24);
    // g i / g b / g m / g a / c / / / ? = 7 快捷键
    expect(state.shortcuts).toHaveLength(7);
    unregister();
  });

  it('S3 枚举闭合:新建 issue / 小队 / Runtimes / Skills 均注册为可搜索面板命令(§1.2 S3)', () => {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS, ENV_ADMIN, OVERLAY);
    const commands = useShortcutRegistry.getState().commands;
    const ids = commands.map((command) => command.id);
    for (const id of [
      'issue.new',
      'nav.squads',
      'nav.runtimes',
      'nav.skills',
      'nav.integrations',
    ]) {
      expect(ids, `missing command ${id}`).toContain(id);
    }
    const runCommand = (id: string): void => {
      commands.find((command) => command.id === id)?.run();
    };
    runCommand('issue.new');
    expect(navigate).toHaveBeenCalledWith('/w/acme/issues?create=1');
    runCommand('nav.squads');
    expect(navigate).toHaveBeenCalledWith('/w/acme/squads');
    runCommand('nav.runtimes');
    expect(navigate).toHaveBeenCalledWith('/w/acme/automations/runtimes');
    runCommand('nav.skills');
    expect(navigate).toHaveBeenCalledWith('/w/acme/automations/skills');
    runCommand('nav.integrations');
    expect(navigate).toHaveBeenCalledWith('/w/acme/automations/integrations');
    runCommand('nav.settings');
    expect(navigate).toHaveBeenCalledWith('/settings');
    unregister();
  });

  it('M10:标记全部已读仅在工作区上下文注册(workspaceId=null 恒空操作 → 根本不注册)', () => {
    const unregisterPlain = registerShellShortcuts(vi.fn(), LABELS, ENV_PLAIN, OVERLAY);
    expect(useShortcutRegistry.getState().commands.map((command) => command.id)).not.toContain(
      'mark.all.read',
    );
    unregisterPlain();
    const unregisterAdmin = registerShellShortcuts(vi.fn(), LABELS, ENV_ADMIN, OVERLAY);
    expect(useShortcutRegistry.getState().commands.map((command) => command.id)).toContain(
      'mark.all.read',
    );
    unregisterAdmin();
  });

  it('设置各子页命令仅 admin+ 注册(§1.2 S3:无权命令根本不注册,非点击报错)', () => {
    // member/guest 视角:设置类命令与审批动作命令不存在
    const unregisterPlain = registerShellShortcuts(vi.fn(), LABELS, ENV_PLAIN, OVERLAY);
    const plainIds = useShortcutRegistry.getState().commands.map((command) => command.id);
    for (const id of [
      'settings.open',
      'settings.members',
      'settings.approvals',
      'settings.fields',
      'settings.danger',
      'approvals.open',
    ]) {
      expect(plainIds).not.toContain(id);
    }
    unregisterPlain();

    // admin + 工作区上下文:齐全
    const unregisterAdmin = registerShellShortcuts(vi.fn(), LABELS, ENV_ADMIN, OVERLAY);
    const adminIds = useShortcutRegistry.getState().commands.map((command) => command.id);
    for (const id of [
      'settings.open',
      'settings.members',
      'settings.approvals',
      'settings.fields',
      'settings.danger',
      'approvals.open',
    ]) {
      expect(adminIds).toContain(id);
    }
    unregisterAdmin();
  });

  it('legacy 注册器缺失必需动作文案时 fail-fast,不注册空 label', () => {
    const labels: ShellShortcutLabels = {
      ...LABELS,
      actions: { ...LABELS.actions, openApprovals: '' },
    };

    expect(() => registerShellShortcuts(vi.fn(), labels, ENV_ADMIN, OVERLAY)).toThrow(
      'Missing shell shortcut label: openApprovals',
    );
  });

  it('工作区上下文内导航命令落规范深链 /w/{slug}/…', () => {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS, ENV_ADMIN, OVERLAY);
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'nav.board')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/w/acme/board');
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'settings.danger')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/w/acme/settings/danger');
    useShortcutRegistry
      .getState()
      .shortcuts.find((def) => def.id === 'go.inbox')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/w/acme/inbox');
    unregister();
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
    const unregister = registerShellShortcuts(vi.fn(), LABELS, ENV_PLAIN, OVERLAY);
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

  it('导航命令调用 navigate(对应路由)', () => {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS, ENV_PLAIN, OVERLAY);
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'nav.inbox')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/inbox');
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'nav.home')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/');
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'nav.issues')
      ?.run();
    expect(navigate).toHaveBeenCalledWith('/issues');
    unregister();
  });

  it('全部命令均带非空字符串 label(label undefined 会使命令面板搜索整体崩溃,MES-45)', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS, ENV_PLAIN, OVERLAY);
    const state = useShortcutRegistry.getState();
    expect(state.commands.length).toBeGreaterThan(0);
    for (const command of state.commands) {
      expect(typeof command.label).toBe('string');
      expect(command.label.length).toBeGreaterThan(0);
    }
    // issues 导航命令必须带映射文案,而非 undefined
    expect(
      useShortcutRegistry.getState().commands.find((command) => command.id === 'nav.issues')?.label,
    ).toBe('Issues');
    unregister();
  });

  it('主题命令与切换命令写 settingsStore', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS, ENV_PLAIN, OVERLAY);
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'theme.dark')
      ?.run();
    expect(useSettingsStore.getState().preferences.theme).toBe('dark');
    // dark → 切换 → system
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === 'theme.toggle')
      ?.run();
    expect(useSettingsStore.getState().preferences.theme).toBe('system');
    unregister();
  });

  it('序列/裸键快捷键定义正确(g i 跳收件箱,c 打开 issues 快速创建)', () => {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS, ENV_PLAIN, OVERLAY);
    const shortcuts = useShortcutRegistry.getState().shortcuts;
    shortcuts.find((def) => def.id === 'go.inbox')?.run();
    expect(navigate).toHaveBeenCalledWith('/inbox');
    shortcuts.find((def) => def.id === 'new.issue')?.run();
    expect(navigate).toHaveBeenCalledWith('/issues?create=1');
    unregister();
  });

  it('/ 快捷键聚焦顶栏搜索框', () => {
    document.body.innerHTML = '<input data-testid="topbar-search" />';
    const unregister = registerShellShortcuts(vi.fn(), LABELS, ENV_PLAIN, OVERLAY);
    useShortcutRegistry
      .getState()
      .shortcuts.find((def) => def.id === 'focus.search')
      ?.run();
    expect(document.querySelector('[data-testid="topbar-search"]')).toBe(document.activeElement);
    unregister();
    document.body.innerHTML = '';
  });

  it('注销后命令与快捷键清空', () => {
    const unregister = registerShellShortcuts(vi.fn(), LABELS, ENV_PLAIN, OVERLAY);
    expect(useShortcutRegistry.getState().commands.length).toBeGreaterThan(0);
    unregister();
    expect(useShortcutRegistry.getState().commands).toHaveLength(0);
    expect(useShortcutRegistry.getState().shortcuts).toHaveLength(0);
  });
});

describe('动作类命令执行体(§6.19 收藏 / §3.2 全部已读 / §3.4 深链 / §6.10 审批 / §6.12 设置门控)', () => {
  const ISSUE_UUID = '0c2f6a1e-1111-2222-3333-444455556666';

  function register(): () => void {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS, ENV_ADMIN, OVERLAY);
    return unregister;
  }

  function runCommand(id: string): void {
    useShortcutRegistry
      .getState()
      .commands.find((command) => command.id === id)
      ?.run();
  }

  function runShortcut(id: string): void {
    useShortcutRegistry
      .getState()
      .shortcuts.find((def) => def.id === id)
      ?.run();
  }

  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('copy.deep.link:复制 origin + 规范路径 + 查询串(§3.4)', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(window.navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    window.history.replaceState({}, '', '/w/acme/board?view=x');
    const unregister = register();
    runCommand('copy.deep.link');
    await vi.waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText).toHaveBeenCalledWith(window.location.origin + '/w/acme/board?view=x');
    unregister();
  });

  it('favorite.toggle:资源路径派生四类目标,未收藏 → put(§6.19)', async () => {
    const { listFavorites, putFavorite } = await import('../../api/favorites');
    const unregister = register();
    const cases: ReadonlyArray<[string, string, string]> = [
      [`/w/acme/issues/${ISSUE_UUID}`, 'issue', ISSUE_UUID],
      ['/w/acme/projects/p-1', 'project', 'p-1'],
      ['/w/acme/views/v-1', 'view', 'v-1'],
      ['/w/acme/chat/c-1', 'chat_session', 'c-1'],
    ];
    for (const [path, type, id] of cases) {
      window.history.replaceState({}, '', path);
      vi.mocked(listFavorites).mockResolvedValue([]);
      runCommand('favorite.toggle');
      await vi.waitFor(() => expect(putFavorite).toHaveBeenCalledWith(expect.anything(), type, id));
      vi.mocked(putFavorite).mockClear();
    }
    unregister();
  });

  it('favorite.toggle:已收藏 → delete 分支', async () => {
    const { listFavorites, deleteFavorite } = await import('../../api/favorites');
    const unregister = register();
    window.history.replaceState({}, '', `/w/acme/issues/${ISSUE_UUID}`);
    vi.mocked(listFavorites).mockResolvedValue([{ target_type: 'issue', target_id: ISSUE_UUID }]);
    runCommand('favorite.toggle');
    await vi.waitFor(() =>
      expect(deleteFavorite).toHaveBeenCalledWith(expect.anything(), 'issue', ISSUE_UUID),
    );
    unregister();
  });

  it('favorite.toggle:非资源路径为空操作(不触收藏端点)', async () => {
    const { putFavorite, deleteFavorite } = await import('../../api/favorites');
    const unregister = register();
    window.history.replaceState({}, '', '/w/acme/board');
    runCommand('favorite.toggle');
    await Promise.resolve();
    expect(putFavorite).not.toHaveBeenCalled();
    expect(deleteFavorite).not.toHaveBeenCalled();
    unregister();
  });

  it('mark.all.read:随当前收件箱视图 filter 口径标记全部已读(comment-inbox.md §3.2)', async () => {
    const { getCurrentInboxView, readAll } = await import('../../features/inbox');
    const unregister = register();
    runCommand('mark.all.read');
    await vi.waitFor(() => expect(readAll).toHaveBeenCalled());
    expect(getCurrentInboxView).toHaveBeenCalled();
    unregister();
  });

  it('mark.all.read:视图无工作区上下文时静默空操作', async () => {
    const { getCurrentInboxView, readAll } = await import('../../features/inbox');
    vi.mocked(getCurrentInboxView).mockReturnValue({ workspaceId: null, filter: 'all' });
    const unregister = register();
    runCommand('mark.all.read');
    await Promise.resolve();
    expect(readAll).not.toHaveBeenCalled();
    unregister();
  });

  it('approvals.open 与设置子页命令经深链导航(§6.10 / §6.12 门控)', () => {
    const navigate = vi.fn();
    const unregister = registerShellShortcuts(navigate, LABELS, ENV_ADMIN, OVERLAY);
    runCommand('approvals.open');
    expect(navigate).toHaveBeenCalledWith('/w/acme/approvals');
    runCommand('settings.open');
    expect(navigate).toHaveBeenCalledWith('/w/acme/settings');
    unregister();
  });

  it('help 快捷键 run 经 overlay 开启帮助层', () => {
    const openHelp = vi.fn();
    const unregister = registerShellShortcuts(vi.fn(), LABELS, ENV_ADMIN, { openHelp });
    runShortcut('help');
    expect(openHelp).toHaveBeenCalled();
    unregister();
  });
});

describe('ShellShortcutsRegistrar(工作区上下文门控注册编排)', () => {
  beforeEach(() => {
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  });

  it('工作区路由命中即注册命令;help.open 经 overlay 控制句柄开启帮助层', async () => {
    const openHelp = vi.fn();
    const fetchImpl = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            data: {
              id: 'ws-1',
              slug: 'acme',
              name: 'Acme',
              my_role: 'owner',
              settings: { default_issue_view: 'board' },
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    );
    const client = new MeshApiClient({
      baseUrl: 'http://api.test',
      getToken: () => 'tok',
      fetchImpl,
    });
    renderWithProviders(
      <OverlayControlsProvider
        value={{ openPalette: () => undefined, openHelp, openSearch: () => undefined }}
      >
        <WorkspaceProvider slug="acme" client={client}>
          <ShellShortcutsRegistrar />
        </WorkspaceProvider>
      </OverlayControlsProvider>,
      { route: '/w/acme/board' },
    );
    let helpCommand: { run: () => void } | undefined;
    await vi.waitFor(() => {
      helpCommand = useShortcutRegistry
        .getState()
        .commands.find((command) => command.id === 'help.open');
      expect(helpCommand).toBeDefined();
    });
    helpCommand?.run();
    expect(openHelp).toHaveBeenCalled();
  });
});
