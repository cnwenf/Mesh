/**
 * TopBar — 品牌/搜索/连接状态点(文本始终在场)/面板与帮助入口;
 * 搜索为真实控件(§4.9):受控输入 + 内联结果弹层(与面板同一 PaletteResults),
 * ↑↓ 选择、Enter 激活选中或提交展开完整面板、Esc 关闭/清空、点击导航。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetApiClient } from '../../api/instance';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../test-utils/render';
import { useShortcutRegistry } from '../../shortcuts';
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
  vi.unstubAllGlobals();
  resetPaletteContextCache();
  resetApiClient();
});

describe('TopBar', () => {
  it('渲染品牌与全局搜索框(初始无弹层)', () => {
    renderTopBar();
    expect(screen.getByText('Mesh')).toBeInTheDocument();
    expect(screen.getByTestId('topbar-search')).toBeInTheDocument();
    expect(screen.queryByTestId('topbar-search-popover')).not.toBeInTheDocument();
  });

  it.each(Object.entries(LABELS))('连接状态 %s 的文本标签始终呈现', (state, label) => {
    renderTopBar({ state: state as ConnectionState });
    expect(screen.getByTestId('conn-status').textContent).toContain(label);
  });

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
    expect(screen.getByTestId('palette-opt-cmd:cmd-alpha')).toHaveAttribute('aria-selected', 'true');
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
      expect(screen.getByTestId('topbar-search-popover').textContent).toContain('No matching commands'),
    );
  });
});
