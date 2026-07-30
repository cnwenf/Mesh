/**
 * CommandPalette — 既有 prop 面回归 + 六类检索分组/键盘全流程/稳定选择(§4.3.1)/
 * no-results 门控/live region/mod+Enter 新标签/空态唯一数据流(§4.2.1 去重)/
 * Tab 补全/错误重试/offline 降级。
 *
 * 新 i18n 键(search.*)不断言译文,断言 testid/role/结构;既有 prop 文案照旧断言。
 */
import { useState } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetApiClient } from '../../api/instance';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import type { SearchItem } from '../../api/search';
import { renderWithProviders } from '../../test-utils/render';
import { CommandPalette } from '../CommandPalette';
import { resetPaletteContextCache } from '../usePaletteContext';
import type { FavoritesProvider } from '../usePaletteData';
import { useShortcutRegistry } from '../registry';
import type { ShortcutCommand } from '../registry';
import { pushRecent, setRecentsScope, trackCommandUse } from '../recents';

const PALETTE_PROPS = {
  closeLabel: 'Close palette',
  searchPlaceholder: 'Search commands',
  emptyText: 'No matching commands',
  title: 'Command palette',
} as const;

const spies = {
  newIssue: vi.fn(),
  gotoBoard: vi.fn(),
  toggleTheme: vi.fn(),
};

function registerCommands(): void {
  const commands: ShortcutCommand[] = [
    { id: 'new-issue', label: 'Create issue', group: 'global', keywords: ['new', 'create'], run: spies.newIssue },
    { id: 'goto-board', label: 'Go to board', group: 'board', keywords: ['kanban'], run: spies.gotoBoard },
    { id: 'toggle-theme', label: 'Toggle theme', group: 'global', run: spies.toggleTheme },
  ];
  act(() => {
    for (const command of commands) useShortcutRegistry.getState().registerCommand(command);
  });
}

function issueItem(id: string, title: string): SearchItem {
  return {
    type: 'issue',
    id,
    title,
    context: {
      identifier: `WEB-${id}`,
      project: { id: 'p', name: '官网' },
      status: { id: 's', name: 'Todo', category: 'todo' },
    },
    icon: 'issue',
    url: `/issues/${id}`,
  };
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolveFn: (value: T) => void = () => undefined;
  const promise = new Promise<T>((resolve) => {
    resolveFn = resolve;
  });
  return { promise, resolve: resolveFn };
}

/** 全局 fetch 桩:/users/me 返回身份;search 按实现回包 */
function stubGlobalFetch(searchImpl?: (url: string) => Promise<Response> | Response): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({
          body: {
            data: {
              user: { id: 'u-1', email: 'u@c.com', display_name: 'U' },
              memberships: [
                {
                  workspace_id: 'ws-1',
                  workspace_name: 'WS',
                  workspace_slug: 'ws',
                  role: 'member',
                  status: 'active',
                  joined_at: null,
                },
              ],
            },
          },
        });
      }
      if (url.includes('/search') && searchImpl !== undefined) {
        return searchImpl(url);
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch,
  );
}

const EMPTY_FAVORITES: FavoritesProvider = async () => [];

function renderPalette(props: Partial<React.ComponentProps<typeof CommandPalette>> = {}) {
  return renderWithProviders(
    <CommandPalette
      open
      onClose={props.onClose ?? (() => undefined)}
      {...PALETTE_PROPS}
      workspaceId="ws-1"
      userId="u-1"
      favoritesProvider={EMPTY_FAVORITES}
      {...props}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  registerCommands();
  window.localStorage.clear();
  setRecentsScope({ userId: 'u-1', workspaceId: 'ws-1' });
  resetPaletteContextCache();
  resetApiClient();
  stubGlobalFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  resetPaletteContextCache();
  resetApiClient();
});

describe('CommandPalette — 既有 prop 面回归', () => {
  it('open=false 时不渲染', () => {
    renderWithProviders(
      <CommandPalette open={false} onClose={() => undefined} {...PALETTE_PROPS} />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('打开后:dialog 以 title 标注,搜索框聚焦,空 query 列出全部命令', () => {
    renderPalette();
    expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeInTheDocument();
    const input = screen.getByRole('combobox');
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute('placeholder', 'Search commands');
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveTextContent('Create issue');
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
    expect(input).toHaveAttribute('aria-activedescendant', options[0]?.id ?? '');
  });

  it('按 label / keywords 过滤', async () => {
    const user = userEvent.setup();
    renderPalette();
    await user.type(screen.getByRole('combobox'), 'board');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option')).toHaveTextContent('Go to board');
  });

  it('无匹配时展示 emptyText + no-results 结构,无 option', async () => {
    const user = userEvent.setup();
    renderPalette();
    await user.type(screen.getByRole('combobox'), 'zzz');
    await waitFor(() => expect(screen.getByTestId('palette-no-results')).toBeInTheDocument());
    expect(screen.getByText('No matching commands')).toBeInTheDocument();
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
  });

  it('ArrowDown/ArrowUp 移动选择并循环', async () => {
    const user = userEvent.setup();
    renderPalette();
    const input = screen.getByRole('combobox');
    await user.keyboard('{ArrowDown}');
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true');
    expect(input).toHaveAttribute('aria-activedescendant', screen.getAllByRole('option')[1]?.id ?? '');
    await user.keyboard('{ArrowUp}{ArrowUp}');
    expect(screen.getAllByRole('option')[2]).toHaveAttribute('aria-selected', 'true');
  });

  it('Enter 执行选中命令并关闭', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderPalette({ onClose });
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{Enter}');
    expect(spies.gotoBoard).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('无选项时 Enter 不执行也不关闭', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderPalette({ onClose });
    await user.type(screen.getByRole('combobox'), 'zzz');
    await waitFor(() => expect(screen.getByTestId('palette-no-results')).toBeInTheDocument());
    await user.keyboard('{Enter}');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('点击选项执行命令并关闭(鼠标等价路径)', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderPalette({ onClose });
    await user.click(screen.getByRole('option', { name: 'Toggle theme' }));
    expect(spies.toggleTheme).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('关闭后焦点归还;重新打开查询清空', async () => {
    const user = userEvent.setup();
    function Harness(): React.JSX.Element {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button type="button" onClick={() => setOpen(true)}>
            Open palette
          </button>
          <CommandPalette
            open={open}
            onClose={() => setOpen(false)}
            {...PALETTE_PROPS}
            workspaceId="ws-1"
            userId="u-1"
            favoritesProvider={EMPTY_FAVORITES}
          />
        </div>
      );
    }
    renderWithProviders(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Open palette' });
    await user.click(trigger);
    await user.type(screen.getByRole('combobox'), 'theme');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    await user.keyboard('{Escape}');
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    expect(screen.getByRole('combobox')).toHaveValue('');
    expect(screen.getAllByRole('option')).toHaveLength(3);
  });

  it('无命令且无本地数据时展示 emptyText,无 listbox', () => {
    act(() => useShortcutRegistry.setState({ commands: [] }));
    renderPalette();
    expect(screen.getByText('No matching commands')).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('aria-controls 关联 listbox', () => {
    renderPalette();
    const input = screen.getByRole('combobox');
    const listboxId = input.getAttribute('aria-controls') ?? '';
    expect(screen.getByRole('listbox')).toHaveAttribute('id', listboxId);
    expect(input).toHaveAttribute('aria-expanded', 'true');
  });

  it('initialQuery 打开即按查询过滤;缺省清空', () => {
    renderPalette({ initialQuery: 'theme' });
    const input = screen.getByRole('combobox');
    expect(input).toHaveValue('theme');
    expect(screen.getAllByRole('option')).toHaveLength(1);
  });
});

describe('CommandPalette — 六类检索与键盘', () => {
  it('实体结果分组呈现;方向键跨组移动(扁平导航)', async () => {
    stubGlobalFetch(() =>
      fakeResponse({ body: { data: [issueItem('1', '登录崩溃')], next_cursor: null } }),
    );
    resetApiClient();
    vi.useFakeTimers();
    renderPalette();
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: '登录' } });
      vi.advanceTimersByTime(150);
    });
    await waitFor(() => expect(screen.getAllByRole('group').length).toBeGreaterThanOrEqual(2));
    const groups = screen.getAllByRole('group');
    expect(groups[0].getAttribute('aria-label')).toContain('search.group.issues');
    const options = screen.getAllByRole('option');
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
    // 扁平序:实体在前、命令在后;ArrowDown 自实体移入命令组
    await act(async () => {
      fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' });
    });
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true');
  });

  it('本地命令零延迟先渲染:防抖窗口内已有命令选项(§11.4)', async () => {
    const pending = deferred<Response>();
    stubGlobalFetch(() => pending.promise);
    resetApiClient();
    vi.useFakeTimers();
    renderPalette();
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'board' } });
    });
    // 防抖窗口内:实体未到,命令已同步渲染(不被 skeleton 阻塞)
    expect(screen.getByTestId('palette-opt-cmd:goto-board')).toBeInTheDocument();
    expect(screen.getByRole('status')).toBeInTheDocument(); // 实体 skeleton
  });

  it('mod+Enter 以新标签打开规范深链(window.open + noopener)', async () => {
    stubGlobalFetch(() =>
      fakeResponse({ body: { data: [issueItem('9', 'Safari 崩溃')], next_cursor: null } }),
    );
    resetApiClient();
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    vi.useFakeTimers();
    renderPalette();
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Safari' } });
      vi.advanceTimersByTime(150);
    });
    await waitFor(() => expect(screen.getByTestId('palette-opt-issue:9')).toBeInTheDocument());
    await act(async () => {
      fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter', ctrlKey: true });
    });
    expect(openSpy).toHaveBeenCalledWith('/issues/9', '_blank', 'noopener');
    openSpy.mockRestore();
  });

  it('异步补入按稳定 id 保持选择:命令选中不被实体插入移位,Enter 执行原选中(§4.3.1)', async () => {
    const pending = deferred<Response>();
    stubGlobalFetch(() => pending.promise);
    resetApiClient();
    vi.useFakeTimers();
    renderPalette();
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'board' } });
      vi.advanceTimersByTime(150);
    });
    // 实体未到,命令命中一条:goto-board(默认选中)
    await waitFor(() => expect(screen.getByTestId('palette-opt-cmd:goto-board')).toBeInTheDocument());
    expect(screen.getByTestId('palette-opt-cmd:goto-board')).toHaveAttribute('aria-selected', 'true');
    // 实体响应补入,插入到命令之前
    await act(async () => {
      pending.resolve(
        fakeResponse({ body: { data: [issueItem('7', 'board 相关')], next_cursor: null } }),
      );
    });
    // 选中仍是 cmd:goto-board(稳定 id),即使其索引下移
    await waitFor(() =>
      expect(screen.getByTestId('palette-opt-cmd:goto-board')).toHaveAttribute('aria-selected', 'true'),
    );
    await act(async () => {
      fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
    });
    // Enter 打开的是原选中的命令,不是补入的实体
    expect(spies.gotoBoard).toHaveBeenCalledTimes(1);
  });

  it('Tab 将选中标题补全进输入框(焦点不离开)', () => {
    renderPalette();
    const input = screen.getByRole('combobox');
    fireEvent.keyDown(input, { key: 'Tab' });
    expect(input).toHaveValue('Create issue');
    expect(input).toHaveFocus();
  });

  it('live region 在检索落地后宣布结果数(search.resultsCount)', async () => {
    stubGlobalFetch(() =>
      fakeResponse({ body: { data: [issueItem('1', 'x')], next_cursor: null } }),
    );
    resetApiClient();
    vi.useFakeTimers();
    renderPalette();
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'abc' } });
      vi.advanceTimersByTime(150);
    });
    const live = await screen.findByTestId('palette-live');
    await waitFor(() => expect(live.textContent).toContain('search.resultsCount'));
    expect(live).toHaveAttribute('aria-live', 'polite');
  });
});

describe('CommandPalette — no-results / 错误 / offline', () => {
  it('no-results「新建 issue」仅 canCreateIssue 时呈现;点击仅预填不提交', async () => {
    const onOpenIssueCreate = vi.fn();
    const user = userEvent.setup();
    renderPalette({ canCreateIssue: true, onOpenIssueCreate });
    await user.type(screen.getByRole('combobox'), 'zzz');
    await waitFor(() => expect(screen.getByTestId('palette-no-results')).toBeInTheDocument());
    await user.click(screen.getByTestId('palette-create-issue'));
    expect(onOpenIssueCreate).toHaveBeenCalledWith('zzz');
  });

  it('canCreateIssue=false 时「新建 issue」动作不渲染(门控)', async () => {
    const user = userEvent.setup();
    renderPalette({ canCreateIssue: false, onOpenIssueCreate: vi.fn() });
    await user.type(screen.getByRole('combobox'), 'zzz');
    await waitFor(() => expect(screen.getByTestId('palette-no-results')).toBeInTheDocument());
    expect(screen.queryByTestId('palette-create-issue')).not.toBeInTheDocument();
  });

  it('错误行 + 重试:重试成功后错误消失、结果呈现', async () => {
    let call = 0;
    stubGlobalFetch(() => {
      call += 1;
      return call === 1
        ? fakeResponse({ status: 422, body: { error: { code: 'query_cost_exceeded', message: 'x' } } })
        : fakeResponse({ body: { data: [issueItem('1', 'ok')], next_cursor: null } });
    });
    resetApiClient();
    vi.useFakeTimers();
    renderPalette();
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'ABC-1' } });
      vi.advanceTimersByTime(1); // identifier → 立即请求
    });
    await waitFor(() => expect(screen.getByTestId('palette-error')).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /search\.retry/ }));
    });
    await waitFor(() => expect(screen.queryByTestId('palette-error')).not.toBeInTheDocument());
    expect(screen.getByTestId('palette-opt-issue:1')).toBeInTheDocument();
  });

  it('offline(navigator.onLine=false):仅本地命令 + 离线提示,不发搜索请求', async () => {
    const searchFetch = vi.fn();
    stubGlobalFetch(() => {
      searchFetch();
      return fakeResponse({ body: { data: [], next_cursor: null } });
    });
    resetApiClient();
    Object.defineProperty(window.navigator, 'onLine', { value: false, configurable: true });
    try {
      const user = userEvent.setup();
      renderPalette();
      await user.type(screen.getByRole('combobox'), 'abc');
      expect(await screen.findByTestId('palette-offline')).toBeInTheDocument();
      expect(screen.getAllByRole('option').length).toBeGreaterThan(0);
      expect(searchFetch).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(window.navigator, 'onLine', { value: true, configurable: true });
    }
  });
});

describe('CommandPalette — 空态唯一数据流(§4.2.1)', () => {
  it('favorites(服务端)→ recents(本地,同 target 去重)→ 常用命令(频次倒序)', async () => {
    const favoritesProvider: FavoritesProvider = async () => [
      { target_type: 'issue', target_id: 'i-dup', title: 'Fav issue', url: '/issues/i-dup', created_at: '2026-02-01T00:00:00Z' },
      { target_type: 'project', target_id: 'p-1', title: 'Fav project', url: '/projects/p-1', created_at: '2026-01-01T00:00:00Z' },
    ];
    pushRecent({ kind: 'object', type: 'issue', id: 'i-dup', title: 'Recent dup', url: '/issues/i-dup', at: 5 });
    pushRecent({ kind: 'object', type: 'view', id: 'v-9', title: 'Recent view', url: '/views/v-9', at: 6 });
    trackCommandUse('toggle-theme');
    trackCommandUse('toggle-theme');

    renderPalette({ favoritesProvider });
    const groups = await screen.findAllByRole('group');
    const labels = groups.map((group) => group.getAttribute('aria-label') ?? '');
    expect(labels[0]).toContain('search.group.favorites');
    expect(labels[1]).toContain('search.group.recents');
    expect(labels[2]).toContain('search.group.commands');
    // 去重:i-dup 仅 favorites 区出现;recents 区不再呈现同 target
    expect(screen.getByText('Fav issue')).toBeInTheDocument();
    expect(screen.queryByText('Recent dup')).not.toBeInTheDocument();
    expect(screen.getByText('Recent view')).toBeInTheDocument();
    // 常用命令区含使用计数最高者
    const commandTexts = groups[2].textContent ?? '';
    expect(commandTexts).toContain('Toggle theme');
  });

  it('favorites 提供器失败 → 空态降级为 recents + 命令(不崩溃)', async () => {
    const failing: FavoritesProvider = async () => {
      throw new Error('boom');
    };
    pushRecent({ kind: 'object', type: 'view', id: 'v-1', title: 'Recent view', url: '/views/v-1', at: 1 });
    renderPalette({ favoritesProvider: failing });
    await waitFor(() => expect(screen.getByText('Recent view')).toBeInTheDocument());
    const labels = screen.getAllByRole('group').map((group) => group.getAttribute('aria-label') ?? '');
    expect(labels.some((label) => label.includes('search.group.favorites'))).toBe(false);
  });
});
