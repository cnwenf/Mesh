/**
 * CommandPalette — 既有 prop 面回归 + 六类检索分组/键盘全流程/稳定选择(§4.3.1)/
 * no-results 门控/live region/mod+Enter 新标签/空态唯一数据流(§4.2.1 去重)/
 * Tab 补全/错误重试/offline 降级。
 *
 * 新 i18n 键(search.*)不断言译文,断言 testid/role/结构;既有 prop 文案照旧断言。
 */
import { useState } from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
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
import { listRecents, pushRecent, setRecentsScope, trackCommandUse } from '../recents';

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
      fakeResponse({ body: { data: [issueItem('1', 'board 崩溃')], next_cursor: null } }),
    );
    resetApiClient();
    vi.useFakeTimers();
    renderPalette();
    // query 'board' 同时命中实体桩与本地命令 'Go to board'
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'board' } });
      await vi.advanceTimersByTimeAsync(150);
    });
    const groups = screen.getAllByRole('group');
    expect(groups.length).toBeGreaterThanOrEqual(2);
    // 组头经目录解析(en:Issues / Commands)
    expect(groups[0].getAttribute('aria-label')).toBe('Issues');
    const options = screen.getAllByRole('option');
    // 扁平序:实体在前、命令在后;异步补入不移动既有派生选中(§4.3.1.4)——
    // 实体到达前默认选中命令 goto-board,到达后仍稳定于该命令(此时为 options[1])
    expect(options[1]).toHaveAttribute('data-testid', 'palette-opt-cmd:goto-board');
    expect(options[1]).toHaveAttribute('aria-selected', 'true');
    // ArrowDown 跨组循环:自命令组末项绕回实体组首项
    await act(async () => {
      fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' });
    });
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true');
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
    // 实体 skeleton 的 role=status 在 dialog 内(全局另有 Toast region 亦为 status)
    expect(within(screen.getByRole('dialog')).getByRole('status')).toBeInTheDocument();
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
      await vi.advanceTimersByTimeAsync(150);
    });
    expect(screen.getByTestId('palette-opt-issue:9')).toBeInTheDocument();
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
      await vi.advanceTimersByTimeAsync(150);
    });
    // 实体未到,命令命中一条:goto-board(默认选中)
    expect(screen.getByTestId('palette-opt-cmd:goto-board')).toBeInTheDocument();
    expect(screen.getByTestId('palette-opt-cmd:goto-board')).toHaveAttribute('aria-selected', 'true');
    // 实体响应补入,插入到命令之前
    await act(async () => {
      pending.resolve(
        fakeResponse({ body: { data: [issueItem('7', 'board 相关')], next_cursor: null } }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
    // 选中仍是 cmd:goto-board(稳定 id),即使其索引下移
    expect(screen.getByTestId('palette-opt-cmd:goto-board')).toHaveAttribute('aria-selected', 'true');
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
      await vi.advanceTimersByTimeAsync(150);
    });
    // 检索落地后 live region 播报结果数(en:{count} results)
    const live = screen.getByTestId('palette-live');
    expect(live.textContent).toContain('results');
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
      await vi.advanceTimersByTimeAsync(1); // identifier → 立即请求
    });
    expect(screen.getByTestId('palette-error')).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByTestId('palette-error')).not.toBeInTheDocument();
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
      // 'theme' 命中本地命令 Toggle theme(离线仅本地命令,不发搜索请求)
      await user.type(screen.getByRole('combobox'), 'theme');
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
    // favorites 异步补入:先等收藏项呈现,再断言三组齐备(组头经目录解析)
    await screen.findByText('Fav issue');
    const groups = screen.getAllByRole('group');
    const labels = groups.map((group) => group.getAttribute('aria-label') ?? '');
    expect(labels[0]).toBe('Favorites');
    expect(labels[1]).toBe('Recent');
    expect(labels[2]).toBe('Commands');
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
    expect(labels.some((label) => label === 'Favorites')).toBe(false);
  });
});

describe('CommandPalette — 激活副作用与平台分支', () => {
  it('navigator 缺失环境视为在线(getIsOnline 兜底 true,命令照常列出)', () => {
    vi.stubGlobal('navigator', undefined);
    renderPalette();
    expect(screen.getAllByRole('option')).toHaveLength(3);
  });

  it('Tab 无候选时不补全输入(target undefined,不阻止默认行为)', () => {
    act(() => useShortcutRegistry.setState({ commands: [] }));
    renderPalette();
    const input = screen.getByRole('combobox');
    fireEvent.keyDown(input, { key: 'Tab' });
    expect(input).toHaveValue('');
  });

  it('实体选项点击:深链导航 + recents 记录对象条目(item 路径)', async () => {
    const onClose = vi.fn();
    stubGlobalFetch(() =>
      fakeResponse({ body: { data: [issueItem('9', 'Safari 崩溃')], next_cursor: null } }),
    );
    resetApiClient();
    vi.useFakeTimers();
    renderPalette({ onClose });
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Safari' } });
      await vi.advanceTimersByTimeAsync(150);
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('palette-opt-issue:9'));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    // 激活写入 recents 对象条目(type/id/url 来自 item)
    const recents = listRecents();
    expect(recents[0]).toMatchObject({ kind: 'object', type: 'issue', id: '9', url: '/issues/9' });
  });

  it('收藏选项点击:stableId(fav:{type}:{id})解析为对象 recent', async () => {
    const onClose = vi.fn();
    const favoritesProvider: FavoritesProvider = async () => [
      { target_type: 'issue', target_id: 'i-fav', title: 'Fav issue', url: '/issues/i-fav', created_at: '2026-02-01T00:00:00Z' },
    ];
    renderPalette({ favoritesProvider, onClose });
    await screen.findByText('Fav issue');
    fireEvent.click(screen.getByText('Fav issue'));
    expect(onClose).toHaveBeenCalledTimes(1);
    const recents = listRecents();
    expect(recents[0]).toMatchObject({
      kind: 'object',
      type: 'issue',
      id: 'i-fav',
      url: '/issues/i-fav',
    });
  });

  it('收藏目标类型未知时 type 落 undefined(仍记录深链 recent)', async () => {
    const favoritesProvider: FavoritesProvider = async () => [
      { target_type: 'weird', target_id: 'w-1', title: 'Weird fav', url: '/w/1', created_at: '2026-02-01T00:00:00Z' },
    ];
    renderPalette({ favoritesProvider });
    await screen.findByText('Weird fav');
    fireEvent.click(screen.getByText('Weird fav'));
    const recents = listRecents();
    expect(recents[0]).toMatchObject({ kind: 'object', id: 'w-1' });
    expect(recents[0]?.type).toBeUndefined();
  });

  it('收藏 target_id 为空时守卫为 null,不写 recents', async () => {
    const favoritesProvider: FavoritesProvider = async () => [
      { target_type: 'issue', target_id: '', title: 'Empty id fav', url: '/issues/x', created_at: '2026-02-01T00:00:00Z' },
    ];
    renderPalette({ favoritesProvider });
    await screen.findByText('Empty id fav');
    fireEvent.click(screen.getByText('Empty id fav'));
    expect(listRecents()).toHaveLength(0);
  });

  it('recents 无 type 对象条目点击:stableId(object:{id})非 fav 路径解析', async () => {
    pushRecent({ kind: 'object', id: 'no-type', title: 'No type recent', url: '/nt', at: 1 });
    renderPalette();
    await screen.findByText('No type recent');
    fireEvent.click(screen.getByText('No type recent'));
    const recents = listRecents();
    // 同身份去重后仍一条;id 经 parts.slice(1) 解析,type 不在六类枚举落 undefined
    expect(recents).toHaveLength(1);
    expect(recents[0]).toMatchObject({ kind: 'object', id: 'no-type' });
    expect(recents[0]?.type).toBeUndefined();
  });

  it('no-results 缺省动作:无 onOpenIssueCreate 时导航预填链接并关闭', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderPalette({ canCreateIssue: true, onClose });
    await user.type(screen.getByRole('combobox'), 'zzz');
    await waitFor(() => expect(screen.getByTestId('palette-no-results')).toBeInTheDocument());
    await user.click(screen.getByTestId('palette-create-issue'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('mac 平台 mod+Enter 以 metaKey 判定新标签(detectMac 分支)', async () => {
    Object.defineProperty(window.navigator, 'platform', { value: 'MacIntel', configurable: true });
    try {
      stubGlobalFetch(() =>
        fakeResponse({ body: { data: [issueItem('9', 'Safari 崩溃')], next_cursor: null } }),
      );
      resetApiClient();
      const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
      vi.useFakeTimers();
      renderPalette();
      await act(async () => {
        fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Safari' } });
        await vi.advanceTimersByTimeAsync(150);
      });
      await act(async () => {
        fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter', metaKey: true });
      });
      expect(openSpy).toHaveBeenCalledWith('/issues/9', '_blank', 'noopener');
      openSpy.mockRestore();
    } finally {
      Object.defineProperty(window.navigator, 'platform', { value: '', configurable: true });
    }
  });
});
