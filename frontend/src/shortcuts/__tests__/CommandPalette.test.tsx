/**
 * 统一命令面板组件测试(search-command-palette.md §4.1/§4.2/§4.2.1/§4.7):
 * 命令过滤/执行、实体分组渲染、高亮 span、Enter 导航、mod+Enter 新标签、
 * no-results「新建 issue」门控、ARIA 角色、错误重试、offline、Esc 分层关闭。
 *
 * useNavigate 以 vi.mock 间谍替换;网络经全局 fetch 桩路由(me/favorites/search)。
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { useShortcutRegistry } from '../registry';
import type { ShortcutCommand } from '../registry';
import { CommandPalette } from '../CommandPalette';
import { setPaletteQuery, takePaletteQuery } from '../../features/search/paletteBridge';

const { navigateSpy } = vi.hoisted(() => ({ navigateSpy: vi.fn() }));

vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>();
  return { ...actual, useNavigate: () => navigateSpy };
});

const PALETTE_PROPS = {
  closeLabel: 'Close palette',
  searchPlaceholder: 'Search commands',
  emptyText: 'Nothing to show',
  title: 'Command palette',
} as const;

const spies = {
  newIssue: vi.fn(),
  gotoBoard: vi.fn(),
  toggleTheme: vi.fn(),
};

function registerCommands(): void {
  const commands: ShortcutCommand[] = [
    { id: 'new-issue', label: 'Create issue', group: 'global', keywords: ['new', 'create'], combo: 'c', run: spies.newIssue },
    { id: 'goto-board', label: 'Go to board', group: 'board', keywords: ['kanban'], run: spies.gotoBoard },
    { id: 'toggle-theme', label: 'Toggle theme', group: 'global', run: spies.toggleTheme },
  ];
  act(() => {
    for (const command of commands) useShortcutRegistry.getState().registerCommand(command);
  });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 每用例可覆写的搜索响应(缺省空集;可返回未决 Promise 以控制在途窗口) */
let searchResponder: () => Response | Promise<Response>;

function installFetchStub(): void {
  const fetchStub = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (url.includes('/api/v1/users/me')) {
      return jsonResponse({ error: { code: 'not_found', message: 'no me in tests' } }, 404);
    }
    if (url.includes('/api/v1/favorites')) {
      return jsonResponse({ data: [], next_cursor: null });
    }
    if (url.includes('/search')) {
      return searchResponder();
    }
    return jsonResponse({ data: [], next_cursor: null });
  });
  vi.stubGlobal('fetch', fetchStub);
}

function issueEntity(): Record<string, unknown> {
  return {
    type: 'issue',
    id: 'i1',
    title: 'Login page crashes',
    context: {
      identifier: 'WEB-1',
      project: { id: 'p1', name: 'Site' },
      status: { id: 's1', name: 'Todo', category: 'todo' },
    },
    icon: 'issue',
    url: '/w/acme/issues/by-identifier/WEB-1',
    badge: { kind: 'status', label_key: 'issue.status.name', label_params: { name: 'Todo' }, color: 'info' },
    highlight: { title: { unit: 'codepoint', ranges: [[0, 5]] } },
  };
}

function memberEntity(): Record<string, unknown> {
  return {
    type: 'member',
    id: 'm1',
    title: 'Login Admin',
    context: { member_type: 'human', role: 'admin' },
    icon: 'member',
    url: '/w/acme/members/m1',
    badge: { kind: 'member_type', label_key: 'member.type.human', label_params: {}, color: 'info' },
    highlight: { title: { unit: 'codepoint', ranges: [[0, 5]] } },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  takePaletteQuery(); // 清空桥接残留
  searchResponder = () => jsonResponse({ data: [], next_cursor: null });
  installFetchStub();
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  registerCommands();
});

describe('CommandPalette(统一命令面板)', () => {
  it('open=false 时不渲染', () => {
    renderWithProviders(<CommandPalette open={false} onClose={() => undefined} {...PALETTE_PROPS} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('打开:dialog 以 title 标注,搜索框聚焦;空 query 列出命令区(组头 + 选项)', async () => {
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeInTheDocument();
    const input = screen.getByRole('combobox');
    await waitFor(() => expect(input).toHaveFocus());
    expect(input).toHaveAttribute('placeholder', 'Search commands');
    expect(screen.getByText('Commands')).toBeInTheDocument();
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveTextContent('Create issue');
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
    expect(input).toHaveAttribute('aria-activedescendant', options[0]?.id ?? '');
    // 命令快捷键右对齐呈现(平台自适应,'c' → 'C')
    expect(within(options[0] as HTMLElement).getByText('C')).toBeInTheDocument();
  });

  it('顶栏桥接查询:打开时消费为初始查询(take 语义)', async () => {
    setPaletteQuery('board');
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue('board'));
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option')).toHaveTextContent('Go to board');
  });

  it('按 label / keywords 同步过滤命令(零延迟)', async () => {
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'board');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option')).toHaveTextContent('Go to board');
  });

  it('实体结果分组渲染(Issues/Members 组头)+ 高亮 span + 副标题 + 徽章', async () => {
    searchResponder = () => jsonResponse({ data: [issueEntity(), memberEntity()], next_cursor: null });
    const user = userEvent.setup();
    const { container } = renderWithProviders(
      <CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />,
    );
    await user.type(screen.getByRole('combobox'), 'Login');
    const options = await screen.findAllByRole('option');
    expect(options).toHaveLength(2);
    expect(screen.getByText('Issues')).toBeInTheDocument();
    expect(screen.getByText('Members')).toBeInTheDocument();
    // 两条结果各有一个命中高亮片段(字重 + 下划线,非颜色唯一信号)
    expect(container.querySelectorAll('.mesh-palette__hit')).toHaveLength(2);
    expect(container.querySelector('.mesh-palette__hit')).toHaveTextContent('Login');
    // 副标题经消息目录本地化组装
    expect(screen.getByText('WEB-1 · Site · Todo')).toBeInTheDocument();
    // 徽章经 label_key + params 渲染
    expect(screen.getByText('Todo')).toBeInTheDocument();
    expect(screen.getByText('Human')).toBeInTheDocument();
  });

  it('Enter 直达规范深链(当前标签导航)并关闭', async () => {
    const onClose = vi.fn();
    searchResponder = () => jsonResponse({ data: [issueEntity()], next_cursor: null });
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'Login');
    await screen.findAllByRole('option');
    await user.keyboard('{Enter}');
    expect(navigateSpy).toHaveBeenCalledWith('/w/acme/issues/by-identifier/WEB-1');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('mod+Enter 新标签打开(window.open noopener),不当前页导航', async () => {
    const onClose = vi.fn();
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    searchResponder = () => jsonResponse({ data: [issueEntity()], next_cursor: null });
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'Login');
    await screen.findAllByRole('option');
    await user.keyboard('{Control>}{Enter}{/Control}');
    expect(openSpy).toHaveBeenCalledWith('/w/acme/issues/by-identifier/WEB-1', '_blank', 'noopener');
    expect(navigateSpy).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it('ArrowDown/ArrowUp 移动选择并循环(aria-selected / activedescendant)', async () => {
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    const input = screen.getByRole('combobox');
    await waitFor(() => expect(input).toHaveFocus());
    await user.keyboard('{ArrowDown}');
    const options = screen.getAllByRole('option');
    expect(options[1]).toHaveAttribute('aria-selected', 'true');
    expect(input).toHaveAttribute('aria-activedescendant', options[1]?.id ?? '');
    await user.keyboard('{ArrowUp}{ArrowUp}');
    expect(screen.getAllByRole('option')[2]).toHaveAttribute('aria-selected', 'true');
  });

  it('Enter 执行选中命令并关闭;使用计数写入本地存储', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveFocus());
    await user.keyboard('{ArrowDown}{Enter}');
    expect(spies.gotoBoard).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    const counts = JSON.parse(localStorage.getItem('mesh.palette.cmdcount:anon') ?? '{}') as Record<string, number>;
    expect(counts['goto-board']).toBe(1);
  });

  it('选中 key 不在当前结果集时回落首行,Enter 不误命中(§4.3.1 稳定性)', async () => {
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    const input = screen.getByRole('combobox');
    await waitFor(() => expect(input).toHaveFocus());
    // 过滤到单一命令并悬停选中它(模拟鼠标停留产生的 selectedKey)
    await user.type(input, 'board');
    const boardOption = await screen.findByRole('option', { name: 'Go to board' });
    fireEvent.mouseEnter(boardOption);
    expect(boardOption).toHaveAttribute('aria-selected', 'true');
    // 改查询:悬停选中的命令已不在结果集;effective 应回落首行而非钉住失效 key
    await user.clear(input);
    await user.type(input, 'theme');
    const themeOption = await screen.findByRole('option', { name: 'Toggle theme' });
    expect(themeOption).toHaveAttribute('aria-selected', 'true');
    expect(input).toHaveAttribute('aria-activedescendant', themeOption.id);
    // Enter 命中首行(Toggle theme),而非已失效的 Go to board
    await user.keyboard('{Enter}');
    expect(spies.toggleTheme).toHaveBeenCalledTimes(1);
    expect(spies.gotoBoard).not.toHaveBeenCalled();
  });

  it('点击选项执行命令并关闭(鼠标等价路径)', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await user.click(await screen.findByRole('option', { name: 'Toggle theme' }));
    expect(spies.toggleTheme).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Tab 补全选中标题到输入框', async () => {
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    const input = screen.getByRole('combobox');
    await user.type(input, 'board');
    await screen.findByRole('option', { name: 'Go to board' });
    await user.keyboard('{Tab}');
    expect(input).toHaveValue('Go to board');
  });

  it('no-results:文案 + 建议;无 canCreateIssue 时不渲染「新建 issue」', async () => {
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'zzz');
    await screen.findByText('No results for “zzz”');
    expect(screen.getByText('Check the spelling or try fewer keywords.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Create issue/ })).not.toBeInTheDocument();
  });

  it('no-results + canCreateIssue:「新建 issue」预填跳转(不直接提交)', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(
      <CommandPalette open onClose={onClose} {...PALETTE_PROPS} canCreateIssue />,
    );
    await user.type(screen.getByRole('combobox'), 'zzz');
    const createButton = await screen.findByRole('button', { name: 'Create issue “zzz”' });
    await user.click(createButton);
    expect(navigateSpy).toHaveBeenCalledWith('/issues?create=1&title=zzz');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('no-results 无瞬态闪现:在途窗口不渲染,仅检索完成且结果空时呈现(§4.2)', async () => {
    let resolveSearch: (response: Response) => void = () => undefined;
    searchResponder = () =>
      new Promise<Response>((resolve) => {
        resolveSearch = resolve;
      });
    const user = userEvent.setup();
    renderWithProviders(
      <CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} canCreateIssue />,
    );
    await user.type(screen.getByRole('combobox'), 'zzz');
    // 等到请求在途(防抖结束、进度条出现):此刻检索尚未落定
    expect(await screen.findByRole('progressbar')).toBeInTheDocument();
    // 在途窗口:no-results 文案、「新建 issue」按钮与空态文案一律不渲染
    expect(screen.queryByText('No results for “zzz”')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Create issue/ })).not.toBeInTheDocument();
    expect(screen.queryByText('Nothing to show')).not.toBeInTheDocument();
    // 检索完成且结果空 → no-results 与「新建 issue」方呈现
    await act(async () => {
      resolveSearch(jsonResponse({ data: [], next_cursor: null }));
    });
    expect(await screen.findByText('No results for “zzz”')).toBeInTheDocument();
    expect(
      await screen.findByRole('button', { name: 'Create issue “zzz”' }),
    ).toBeInTheDocument();
  });

  it('错误态:内联错误行 + 重试按钮;重试成功后渲染结果', async () => {
    let failed = false;
    searchResponder = () => {
      if (!failed) {
        failed = true;
        return jsonResponse({ error: { code: 'internal_error', message: 'boom' } }, 500);
      }
      return jsonResponse({ data: [issueEntity()], next_cursor: null });
    };
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'Login');
    const retryButton = await screen.findByRole('button', { name: 'Retry' });
    expect(screen.getByText('Search failed')).toBeInTheDocument();
    await user.click(retryButton);
    await screen.findAllByRole('option');
    expect(screen.queryByText('Search failed')).not.toBeInTheDocument();
  });

  it('offline:提示网络断开,本地命令仍可用', async () => {
    Object.defineProperty(window.navigator, 'onLine', { value: false, configurable: true });
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    expect(await screen.findByText('Network offline — showing local commands')).toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(3);
    Object.defineProperty(window.navigator, 'onLine', { value: true, configurable: true });
  });

  it('Esc 分层关闭:输入框获焦时首个 Esc 仅失焦,第二个 Esc 关面板(§4.5)', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    const input = screen.getByRole('combobox');
    await waitFor(() => expect(input).toHaveFocus());
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
    expect(input).not.toHaveFocus();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('无任何可展示行时展示 emptyText(prop)', async () => {
    act(() => useShortcutRegistry.setState({ commands: [] }));
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    expect(await screen.findByText('Nothing to show')).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('空态组装:favorites 区 + recents 区(同 target 去重)+ 命令区;点击各自导航', async () => {
    const onClose = vi.fn();
    const fetchStub = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url = String(input);
      if (url.includes('/api/v1/favorites')) {
        return jsonResponse({
          data: [
            {
              id: 'f1',
              workspace_id: 'default',
              member_id: 'm',
              target_type: 'issue',
              target_id: 'i-1',
              created_at: '2026-07-01T00:00:00.000Z',
            },
          ],
          next_cursor: null,
        });
      }
      if (url.includes('/api/v1/users/me')) {
        return jsonResponse({ error: { code: 'not_found', message: 'no me' } }, 404);
      }
      return jsonResponse({ data: [], next_cursor: null });
    });
    vi.stubGlobal('fetch', fetchStub);
    // 预置本地 recents:一条与收藏同 target(应去重),一条独立
    const { recordRecent, recentsStorageKey } = await import('../../features/search/recents');
    recordRecent('anon', 'default', {
      type: 'issue',
      id: 'i-1',
      title: 'Fav issue',
      url: '/w/acme/issues/by-identifier/FAV-1',
    });
    recordRecent('anon', 'default', {
      type: 'project',
      id: 'p-1',
      title: 'Website',
      url: '/w/acme/projects/p-1',
    });

    const user = userEvent.setup();
    const first = renderWithProviders(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    // 三区组头齐备;收藏行经本地 recents 解析出标题;同 target recent 被去重
    expect(await screen.findByText('Favorites')).toBeInTheDocument();
    expect(screen.getByText('Recent')).toBeInTheDocument();
    expect(screen.getByText('Commands')).toBeInTheDocument();
    const favOption = await screen.findByRole('option', { name: 'Fav issue' });
    expect(screen.getAllByRole('option', { name: 'Fav issue' })).toHaveLength(1);

    // 点击收藏行 → 规范深链导航 + recent 记录
    await user.click(favOption);
    expect(navigateSpy).toHaveBeenCalledWith('/w/acme/issues/by-identifier/FAV-1');
    expect(onClose).toHaveBeenCalledTimes(1);
    const stored = JSON.parse(localStorage.getItem(recentsStorageKey('anon', 'default')) ?? '[]') as Array<{ id: string }>;
    expect(stored[0]?.id).toBe('i-1');

    // 重开:点击 recent 行导航
    first.unmount();
    navigateSpy.mockClear();
    onClose.mockClear();
    renderWithProviders(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await user.click(await screen.findByRole('option', { name: 'Website' }));
    expect(navigateSpy).toHaveBeenCalledWith('/w/acme/projects/p-1');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('agent 容量呈现(search.capacity)+ 无徽章实体不渲染徽章', async () => {
    searchResponder = () =>
      jsonResponse({
        data: [
          {
            type: 'agent',
            id: 'a1',
            title: 'Codebot',
            context: {
              member_type: 'agent',
              role: 'member',
              capacity: { running: 2, queued: 1, awaiting_approval: 3 },
            },
            icon: 'agent',
            url: '/w/acme/members/a1',
          },
        ],
        next_cursor: null,
      });
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'Codebot');
    await screen.findByRole('option', { name: /Codebot/ });
    expect(screen.getByText(/Running 2 · Queued 1 · Awaiting approval 3/)).toBeInTheDocument();
    expect(document.querySelector('.mesh-palette__badge')).toBeNull();
  });

  it('惰性失效清理:identifier 检索无命中时本地对应 recent 被剔除(§4.2.1)', async () => {
    const { recordRecent, recentsStorageKey } = await import('../../features/search/recents');
    recordRecent('anon', 'default', {
      type: 'issue',
      id: 'i-gone',
      title: 'Gone issue',
      url: '/w/acme/issues/by-identifier/WEB-9',
    });
    recordRecent('anon', 'default', {
      type: 'issue',
      id: 'i-safe',
      title: 'Safe issue',
      url: '/w/acme/issues/by-identifier/WEB-1',
    });
    // WEB-9 检索无结果 → 失效;WEB-1 无关条目不受影响
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'WEB-9');
    await waitFor(() => {
      const stored = JSON.parse(
        localStorage.getItem(recentsStorageKey('anon', 'default')) ?? '[]',
      ) as Array<{ id: string }>;
      expect(stored.map((entry) => entry.id)).toEqual(['i-safe']);
    });
  });

  it('mod+Enter 作用于命令行:等价于直接执行(命令无深链)', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveFocus());
    await user.keyboard('{Control>}{Enter}{/Control}');
    expect(spies.newIssue).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('打开期间桥接查询变化 → 同步到输入框', async () => {
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveFocus());
    act(() => setPaletteQuery('theme'));
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue('theme'));
  });

  it('ARIA:combobox 经 aria-controls 关联 listbox;aria-expanded;结果计数 aria-live', async () => {
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    const input = screen.getByRole('combobox');
    const listboxId = input.getAttribute('aria-controls') ?? '';
    // await 等待 favorites 等异步源落定(避免测试后状态更新)
    expect(await screen.findByRole('listbox')).toHaveAttribute('id', listboxId);
    expect(input).toHaveAttribute('aria-expanded', 'true');
    expect(document.querySelector('.mesh-palette__live')).toHaveAttribute('aria-live', 'polite');
  });

  it('initialQuery 提供时以该查询打开并按查询过滤(顶栏搜索续输入展开同一面板,search-command-palette S1)', async () => {
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} initialQuery="theme" />);
    const input = screen.getByRole('combobox');
    // 聚焦经 macrotask 延后一拍(与 Dialog 焦点移入竞争的安全时序)
    await waitFor(() => expect(input).toHaveFocus());
    expect(input).toHaveValue('theme');
    expect(await screen.findAllByRole('option')).toHaveLength(1);
  });

  it('initialQuery 缺省时打开仍清空查询(向后兼容)', async () => {
    renderWithProviders(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    expect(await screen.findByRole('combobox')).toHaveValue('');
  });
});
