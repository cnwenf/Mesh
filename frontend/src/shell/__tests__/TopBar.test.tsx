/**
 * TopBar — 品牌链接(§4.2 返回首页)/搜索/连接状态(稳定态仅点 + tooltip,
 * 进行/异常态显文本)/面板与帮助入口;搜索为真实控件(§4.9):受控输入 +
 * 内联结果弹层(与面板同一 PaletteResults),↑↓ 选择、Enter 激活选中或提交
 * 展开完整面板、Esc 关闭/清空、点击导航。
 */
import { useState } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetApiClient } from '../../api/instance';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../test-utils/render';
import { useShortcutRegistry } from '../../shortcuts';
import { useAuthStore } from '../../state/authStore';
import { resetPaletteContextCache } from '../../shortcuts/usePaletteContext';
import { setRecentsScope } from '../../shortcuts/recents';
import { TopBar } from '../TopBar';
import type { ConnectionState } from '../../realtime';

const LABELS: Record<ConnectionState, string> = {
  idle: 'Not connected',
  connecting: 'Connecting',
  connected: 'Connected',
  reconnecting: 'Reconnecting',
  offline: 'Offline',
  resyncing: 'Resyncing',
};

const runSpy = vi.fn();

function stubNetwork(): void {
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
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch,
  );
}

function renderTopBar(props: Partial<React.ComponentProps<typeof TopBar>> = {}) {
  return renderWithProviders(
    <TopBar
      state="idle"
      onOpenPalette={props.onOpenPalette ?? vi.fn()}
      onOpenHelp={props.onOpenHelp ?? vi.fn()}
      onOpenSearch={props.onOpenSearch ?? vi.fn()}
      favoritesProvider={props.favoritesProvider ?? (async () => [])}
      {...props}
    />,
  );
}

function SearchModeHarness(props: { onOpenSearch: (query: string) => void }): React.JSX.Element {
  const [searchMode, setSearchMode] = useState<'inline' | 'palette'>('inline');
  return (
    <>
      <button type="button" onClick={() => setSearchMode('palette')}>
        Use palette mode
      </button>
      <TopBar
        state="connected"
        onOpenPalette={vi.fn()}
        onOpenHelp={vi.fn()}
        onOpenSearch={props.onOpenSearch}
        favoritesProvider={async () => []}
        searchMode={searchMode}
      />
    </>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  act(() => {
    useShortcutRegistry
      .getState()
      .registerCommand({ id: 'cmd-alpha', label: 'Alpha command', group: 'global', run: runSpy });
    useShortcutRegistry
      .getState()
      .registerCommand({ id: 'cmd-beta', label: 'Beta command', group: 'global', run: vi.fn() });
  });
  window.localStorage.clear();
  setRecentsScope({ userId: 'u-1', workspaceId: 'ws-1' });
  resetPaletteContextCache();
  resetApiClient();
  stubNetwork();
});

afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
  resetPaletteContextCache();
  resetApiClient();
});

describe('TopBar', () => {
  it('渲染品牌链接(§4.2 返回首页)与全局搜索框(初始无弹层)', () => {
    renderTopBar();
    expect(screen.getByRole('link', { name: 'Mesh' })).toHaveAttribute('href', '/');
    expect(screen.getByTestId('topbar-search')).toHaveAttribute('data-slot', 'input');
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
  });

  it.each(['connecting', 'reconnecting', 'resyncing', 'offline'])(
    '进行/异常态 %s 显式呈现文本标签(§4.2)',
    (state) => {
      renderTopBar({ state: state as ConnectionState });
      expect(screen.getByTestId('conn-status').textContent).toContain(
        LABELS[state as ConnectionState],
      );
    },
  );

  it.each(['connected', 'idle'])(
    '稳定态 %s 仅呈现状态点 + tooltip 可读名,不显文本(§4.2 减常态噪音)',
    (state) => {
      renderTopBar({ state: state as ConnectionState });
      // 稳定态不渲染常驻可见文本标签(StatusDot 的 .mesh-status__label),
      // 可读名由 tooltip 承载(role=img + aria-label 供读屏,颜色非唯一信号)。
      const conn = screen.getByTestId('conn-status');
      expect(conn.querySelector('.mesh-status__label')).toBeNull();
      const img = screen.getByRole('img', { name: LABELS[state as ConnectionState] });
      expect(img).toBeInTheDocument();
      // 悬停提示经 title 承载(零布局副作用,不撑出 320px 横向滚动)
      expect(img).toHaveAttribute('title', LABELS[state as ConnectionState]);
    },
  );

  it('命令面板与帮助按钮触发对应回调', () => {
    const onOpenPalette = vi.fn();
    const onOpenHelp = vi.fn();
    renderTopBar({ state: 'connected', onOpenPalette, onOpenHelp });
    fireEvent.click(screen.getByTestId('open-palette'));
    fireEvent.click(screen.getByTestId('open-help'));
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
    expect(onOpenHelp).toHaveBeenCalledTimes(1);
  });

  it('键入展开内联结果弹层(同一 PaletteResults);输入框保留键入值', async () => {
    renderTopBar({ state: 'connected' });
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: 'alp' } });
    expect(input).toHaveValue('alp');
    expect(await screen.findByTestId('topbar-search-popover')).toBeInTheDocument();
    // 本地命令同步过滤呈现(共享 palette-opt-* 稳定 id)
    expect(screen.getByTestId('palette-opt-cmd:cmd-alpha')).toBeInTheDocument();
    expect(screen.queryByTestId('palette-opt-cmd:cmd-beta')).not.toBeInTheDocument();
    // 弹层默认无选中(Enter 语义为提交)
    expect(input).not.toHaveAttribute('aria-activedescendant');
  });

  it('palette 模式把首字符交给完整面板,空值不触发交接且本框不残留状态', () => {
    const onOpenSearch = vi.fn();
    renderWithProviders(<SearchModeHarness onOpenSearch={onOpenSearch} />);
    const input = screen.getByTestId('topbar-search');

    fireEvent.change(input, { target: { value: 'seed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use palette mode' }));
    fireEvent.change(input, { target: { value: '' } });
    expect(onOpenSearch).not.toHaveBeenCalled();
    expect(input).toHaveValue('');

    fireEvent.change(input, { target: { value: 'a' } });
    expect(onOpenSearch).toHaveBeenCalledWith('a');
    expect(input).toHaveValue('');
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
  });

  it('Enter 无选中项 → 携带查询展开完整命令面板并清空本框(统一入口 S1)', async () => {
    const onOpenSearch = vi.fn();
    renderTopBar({ state: 'connected', onOpenSearch });
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: '看板' } });
    await screen.findByTestId('topbar-search-popover');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onOpenSearch).toHaveBeenCalledWith('看板');
    expect(input).toHaveValue('');
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
  });

  it('ArrowDown 进入弹层选择;Enter 激活选中项(命令执行)并收起', async () => {
    const onOpenSearch = vi.fn();
    renderTopBar({ state: 'connected', onOpenSearch });
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: 'alpha' } });
    await screen.findByTestId('topbar-search-popover');
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(screen.getByTestId('palette-opt-cmd:cmd-alpha')).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(input).toHaveAttribute('aria-activedescendant', 'palette-opt-cmd:cmd-alpha');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(runSpy).toHaveBeenCalledTimes(1);
    expect(onOpenSearch).not.toHaveBeenCalled();
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
  });

  it('点击结果项激活(鼠标等价路径)', async () => {
    renderTopBar({ state: 'connected' });
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: 'alpha' } });
    fireEvent.click(await screen.findByTestId('palette-opt-cmd:cmd-alpha'));
    expect(runSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
  });

  it('弹层打开后容器外 mousedown 关闭弹层;容器内按下不关闭(等价鼠标路径)', async () => {
    renderTopBar({ state: 'connected' });
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: 'alpha' } });
    expect(await screen.findByTestId('topbar-search-popover')).toBeInTheDocument();
    // 容器内按下(输入框自身)不关闭
    fireEvent.mouseDown(input);
    expect(screen.getByTestId('topbar-search-popover')).toBeInTheDocument();
    // 容器外按下 → 关闭
    fireEvent.mouseDown(document.body);
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
  });

  it('对象结果中键点击经 openExternal 以新标签打开规范深链(window.open + noopener)', async () => {
    // 有 token 才解析面板身份上下文(§3.2 匿名守卫),远程对象检索方可发起
    useAuthStore.getState().setToken('test-token');
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const issueItem = {
      type: 'issue',
      id: 'i-1',
      title: '登录崩溃排查',
      icon: 'issue',
      url: '/w/ws/issues/i-1',
      context: {
        identifier: 'MES-1',
        project: null,
        status: { id: 's-1', name: 'Todo', category: 'todo' },
      },
    };
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
        if (url.includes('/search')) {
          return fakeResponse({ body: { data: [issueItem], next_cursor: null } });
        }
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }) as typeof fetch,
    );
    renderTopBar({ state: 'connected' });
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: '登录' } });
    const option = await screen.findByTestId('palette-opt-issue:i-1');
    // button=0/2 的 auxclick 不触发新标签语义;中键(1)才触发
    fireEvent(option, new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 0 }));
    expect(openSpy).not.toHaveBeenCalled();
    fireEvent(option, new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 }));
    expect(openSpy).toHaveBeenCalledWith('/w/ws/issues/i-1', '_blank', 'noopener');
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
    openSpy.mockRestore();
  });

  it('Esc 先关闭弹层,再按清空输入(不展开面板)', async () => {
    const onOpenSearch = vi.fn();
    renderTopBar({ state: 'connected', onOpenSearch });
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: 'alpha' } });
    await screen.findByTestId('topbar-search-popover');
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
    expect(input).toHaveValue('alpha');
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(input).toHaveValue('');
    expect(onOpenSearch).not.toHaveBeenCalled();
  });

  it('清空输入关闭弹层(不展开面板)', async () => {
    const onOpenSearch = vi.fn();
    renderTopBar({ state: 'connected', onOpenSearch });
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: 'alpha' } });
    await screen.findByTestId('topbar-search-popover');
    fireEvent.change(input, { target: { value: '' } });
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
    expect(onOpenSearch).not.toHaveBeenCalled();
  });

  it('无结果时弹层呈现空态文案(shortcuts.paletteEmpty 既有键)', async () => {
    renderTopBar({ state: 'connected' });
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: 'zzz' } });
    await waitFor(() =>
      expect(screen.getByTestId('topbar-search-popover').textContent).toContain(
        'No matching commands',
      ),
    );
  });
});
