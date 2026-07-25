/**
 * AppShell — 布局(顶栏/侧栏/主区 Outlet)与鼠标导航路径。无 token:实时不建连(不触 WS)。
 */
import { fireEvent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Route, Routes } from 'react-router-dom';
import { MeshApiError } from '../../api';
import { env } from '../../env';
import { useSettingsStore } from '../../state/settingsStore';
import { useShortcutRegistry } from '../../shortcuts';
import { renderWithProviders } from '../../test-utils/render';
import { AppShell, reconcile } from '../AppShell';
import { PlaceholderPage } from '../PlaceholderPage';

function renderShell(route = '/'): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route index element={<div data-testid="child-stub" />} />
        <Route path="inbox" element={<PlaceholderPage kind="inbox" />} />
      </Route>
    </Routes>,
    { route },
  );
}

describe('AppShell', () => {
  beforeEach(() => {
    useSettingsStore.getState().resetPreferences();
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  });

  it('渲染顶栏/侧栏与主区 Outlet 子内容', () => {
    renderShell('/');
    expect(screen.getByTestId('topbar-search')).toBeInTheDocument();
    expect(screen.getByTestId('nav-home')).toBeInTheDocument();
    expect(screen.getByTestId('child-stub')).toBeInTheDocument();
  });

  it('点击侧栏导航(鼠标路径)切换 Outlet 内容', () => {
    renderShell('/');
    expect(screen.getByTestId('child-stub')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('nav-inbox'));
    // 占位空态描述(侧栏 nav 项不含此文案,避免多匹配)
    expect(screen.getByText('Items you create or follow will show up here.')).toBeInTheDocument();
    expect(screen.queryByTestId('child-stub')).not.toBeInTheDocument();
  });

  it('无 OverlayControls 时顶栏面板/帮助按钮为空操作(不抛错)', () => {
    renderShell('/');
    expect(() => {
      fireEvent.click(screen.getByTestId('open-palette'));
      fireEvent.click(screen.getByTestId('open-help'));
    }).not.toThrow();
  });
});

describe('reconcile(resync REST 对账)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('2xx 时对账成功(拉取 rest URL 并消费响应体)', async () => {
    const fetchMock = vi.fn(async () => new Response('[]', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(
      reconcile({ topic: 'workspace:ws-1:issues', watermark: 7, rest: '/api/v1/demo/issues?since=7' }),
    ).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(env.apiBaseUrl + '/api/v1/demo/issues?since=7');
  });

  it('非 2xx 时抛 MeshApiError(触发客户端退避重试)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 500 })));
    await expect(
      reconcile({ topic: 'workspace:ws-1:issues', watermark: 7, rest: '/api/v1/demo/issues?since=7' }),
    ).rejects.toBeInstanceOf(MeshApiError);
  });
});
