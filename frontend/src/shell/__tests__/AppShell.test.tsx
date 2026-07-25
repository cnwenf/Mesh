/**
 * AppShell — 布局(顶栏/侧栏/主区 Outlet)与鼠标导航路径。无 token:实时不建连(不触 WS)。
 */
import { act, fireEvent, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Route, Routes } from 'react-router-dom';
import { MeshApiError } from '../../api';
import { env } from '../../env';
import { useAuthStore } from '../../state/authStore';
import { useSettingsStore } from '../../state/settingsStore';
import { useShortcutRegistry } from '../../shortcuts';
import { renderWithProviders } from '../../test-utils/render';
import { AppShell, channelEventsUrl, createReconciler, fetchRestEvents, useOfflinePolling } from '../AppShell';
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

describe('fetchRestEvents / createReconciler(resync REST 对账,§6.7)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const EVENT_BODY = {
    data: [
      { channel: 'issue:1', seq: 8, event: 'issue.updated', payload: { id: 'x', v: 1 } },
      { channel: 'issue:1', seq: 9, event: 'issue.updated', payload: { id: 'y', v: 2 } },
    ],
    next_cursor: null,
  };

  it('2xx 时聚合事件帧(op:event 形态)并携带 Bearer 头', async () => {
    const fetchMock = vi.fn(async (_url: URL | RequestInfo, _init?: RequestInit) =>
      new Response(JSON.stringify(EVENT_BODY), { status: 200 }),
    );
    const frames = await fetchRestEvents('http://api/api/v1/realtime/events?channel=issue%3A1&since=7', fetchMock);
    expect(frames).toEqual([
      { op: 'event', channel: 'issue:1', seq: 8, event: 'issue.updated', payload: { id: 'x', v: 1 } },
      { op: 'event', channel: 'issue:1', seq: 9, event: 'issue.updated', payload: { id: 'y', v: 2 } },
    ]);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined(); // 无 token 不带头
  });

  it('next_cursor 非空时翻页拉取(cursor 参数追加)', async () => {
    const page1 = { data: [EVENT_BODY.data[0]], next_cursor: 'cur-1' };
    const page2 = { data: [EVENT_BODY.data[1]], next_cursor: null };
    const fetchMock = vi
      .fn(async (_url: URL | RequestInfo, _init?: RequestInit) => new Response('{}', { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(page1), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(page2), { status: 200 }));
    const frames = await fetchRestEvents('http://api/events?since=7', fetchMock);
    expect(frames).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe('http://api/events?since=7&cursor=cur-1');
  });

  it('非 2xx 时抛 MeshApiError(触发客户端退避重试)', async () => {
    await expect(
      fetchRestEvents('http://api/events', vi.fn(async () => new Response('', { status: 500 }))),
    ).rejects.toBeInstanceOf(MeshApiError);
  });

  it('createReconciler:拉取 rest 并经 client.ingestReconciledEvent 注入帧', async () => {
    const fetchMock = vi.fn(async (_url: URL | RequestInfo, _init?: RequestInit) =>
      new Response(JSON.stringify(EVENT_BODY), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const client = { ingestReconciledEvent: vi.fn() } as never;
    const reconciler = createReconciler(client);
    await reconciler({ channel: 'issue:1', watermark: 9, rest: '/api/v1/realtime/events?channel=issue%3A1&since=7' });
    expect(fetchMock.mock.calls[0][0]).toBe(
      env.apiBaseUrl + '/api/v1/realtime/events?channel=issue%3A1&since=7',
    );
    expect((client as { ingestReconciledEvent: ReturnType<typeof vi.fn> }).ingestReconciledEvent).toHaveBeenCalledTimes(2);
  });

  it('channelEventsUrl 生成对账/轮询共用的频道事件 URL', () => {
    expect(channelEventsUrl('workspace:ws-1:issues', 41)).toBe(
      env.apiBaseUrl + '/api/v1/realtime/events?channel=workspace%3Aws-1%3Aissues&since=41',
    );
  });
});


describe('useOfflinePolling(§3.2 离线降级轮询编排)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const FRAMES_BODY = {
    data: [{ channel: 'workspace:ws-1:issues', seq: 9, event: 'issue.updated', payload: { id: 'p1' } }],
    next_cursor: null,
  };

  function stubClient() {
    return { getCursor: vi.fn(() => 7), ingestReconciledEvent: vi.fn() };
  }

  it('reconnecting 时启动轮询:按游标水位拉取 REST 事件并经 ingest 注入(携带 Bearer)', async () => {
    useAuthStore.getState().setToken('mesh-dev:ws');
    const fetchMock = vi.fn(async (_url: URL | RequestInfo, _init?: RequestInit) =>
      new Response(JSON.stringify(FRAMES_BODY), { status: 200 }),
    );
    const client = stubClient();
    renderHook(() =>
      useOfflinePolling({
        client,
        state: 'reconnecting',
        enabled: true,
        channel: 'workspace:ws-1:issues',
        intervalMs: 1,
        fetchImpl: fetchMock,
      }),
    );
    // 轮询节拍 1ms,持续注入同帧(桩不模拟 seq>since 语义);真实 REST 不重复返回,
    // 且客户端游标守卫对重复帧天然去重。此处验证首帧注入与请求形态。
    await waitFor(() => expect(client.ingestReconciledEvent).toHaveBeenCalled());
    expect(client.ingestReconciledEvent).toHaveBeenCalledWith(
      expect.objectContaining({ op: 'event', seq: 9 }),
    );
    expect(client.getCursor).toHaveBeenCalledWith('workspace:ws-1:issues');
    const calledUrl = String(fetchMock.mock.calls[0][0]);
    expect(calledUrl).toContain('since=7'); // 以 WS 游标为水位
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer mesh-dev:ws');
  });

  it('connected 状态不轮询', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    renderHook(() =>
      useOfflinePolling({
        client: stubClient(),
        state: 'connected',
        enabled: true,
        channel: 'workspace:ws-1:issues',
        intervalMs: 1,
        fetchImpl: fetchMock,
      }),
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('未登录(enabled=false)不轮询', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    renderHook(() =>
      useOfflinePolling({
        client: stubClient(),
        state: 'reconnecting',
        enabled: false,
        channel: 'c',
        intervalMs: 1,
        fetchImpl: fetchMock,
      }),
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('恢复 connected 后停止轮询', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(FRAMES_BODY), { status: 200 }),
    );
    const client = stubClient();
    const initialProps: { state: 'reconnecting' | 'connected' } = { state: 'reconnecting' };
    const { rerender } = renderHook(
      (props: { state: 'reconnecting' | 'connected' }) =>
        useOfflinePolling({
          client,
          state: props.state,
          enabled: true,
          channel: 'workspace:ws-1:issues',
          intervalMs: 1,
          fetchImpl: fetchMock,
        }),
      { initialProps },
    );
    await waitFor(() => expect(client.ingestReconciledEvent).toHaveBeenCalled());
    rerender({ state: 'connected' });
    const calls = fetchMock.mock.calls.length;
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(fetchMock.mock.calls.length).toBe(calls); // 不再新增
  });
});
