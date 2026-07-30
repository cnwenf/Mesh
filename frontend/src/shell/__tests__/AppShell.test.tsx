/**
 * AppShell — 布局(顶栏/侧栏/主区 Outlet)与鼠标导航路径。无 token:实时不建连(不触 WS)。
 */
import { act, fireEvent, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Route, Routes } from 'react-router';
import { MeshApiError } from '../../api';
import { env } from '../../env';
import { useAuthStore } from '../../state/authStore';
import { useSettingsStore } from '../../state/settingsStore';
import { useShortcutRegistry } from '../../shortcuts';
import { renderWithProviders } from '../../test-utils/render';
import { AppShell, MAX_RESYNC_PAGES, channelEventsUrl, createReconciler, fetchRestEvents, resolveResyncUrl, useOfflinePolling } from '../AppShell';
import { useT } from '../../i18n';

function InboxStub(): React.JSX.Element {
  const t = useT();
  return <div data-testid="inbox-stub">{t('state.emptyDescription')}</div>;
}

function renderShell(route = '/'): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route index element={<div data-testid="child-stub" />} />
        <Route path="inbox" element={<InboxStub />} />
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

  it('skip link 指向主区稳定锚点(键盘首焦直达主内容,design-quality §10.2)', () => {
    renderShell('/');
    const skipLink = screen.getByRole('link', { name: 'Skip to main content' });
    expect(skipLink).toHaveAttribute('href', '#mesh-main-content');
    const main = screen.getByRole('main');
    expect(main).toHaveAttribute('id', 'mesh-main-content');
  });

  it('手机底部主导航与「更多」抽屉已接线(隐藏侧栏有等价入口,design-quality §4.3/A-03)', () => {
    renderShell('/');
    expect(screen.getByTestId('mobile-nav-home')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-nav-more')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('mobile-nav-more'));
    expect(screen.getByRole('dialog', { name: 'All navigation' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close navigation menu' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
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

  it('命令面板导航命令均带本地化 label(映射缺键会致搜索整体崩溃,MES-45 回归)', () => {
    renderShell('/');
    const commands = useShortcutRegistry.getState().commands;
    expect(commands.length).toBeGreaterThan(0);
    for (const command of commands) {
      expect(typeof command.label).toBe('string');
      expect(command.label.length).toBeGreaterThan(0);
      // 缺失映射的兜底是原始 key 或 undefined,均不应出现
      expect(command.label).not.toBe(command.id);
    }
    // 上一轮回归点:nav 映射缺 issues 键 → label undefined → 输入即抛 TypeError
    const issues = commands.find((command) => command.id === 'nav.issues');
    expect(issues).toBeDefined();
    expect(issues?.label).toBe('Issues');
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

  it('next_cursor 永不为空时翻页到达上限即停(防恶意游标死循环,MEDIUM-1)', async () => {
    const page = { data: [EVENT_BODY.data[0]], next_cursor: 'never-null' };
    const fetchMock = vi.fn(async (_url: URL | RequestInfo, _init?: RequestInit) =>
      new Response(JSON.stringify(page), { status: 200 }),
    );
    const frames = await fetchRestEvents('http://api/api/v1/realtime/events?since=1', fetchMock);
    expect(fetchMock).toHaveBeenCalledTimes(MAX_RESYNC_PAGES); // 不无限翻页
    expect(frames).toHaveLength(MAX_RESYNC_PAGES); // 已聚合帧照常返回
  });
});

describe('resolveResyncUrl / createReconciler 同源校验(MEDIUM-1:token 不得发往非预期主机)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useAuthStore.getState().clearToken();
  });

  const REST = '/api/v1/realtime/events?channel=issue%3A1&since=7';

  it('合法相对 rest 解析为 apiBaseUrl 同源绝对 URL', () => {
    expect(resolveResyncUrl(REST)).toBe(env.apiBaseUrl + REST);
  });

  it('apiBaseUrl 为空(同源部署)时以页面 origin 为基,相对路径照常通过', () => {
    expect(resolveResyncUrl(REST, '')).toBe(window.location.origin + REST);
  });

  it.each([
    ['绝对 URL 跨源', 'https://evil.example/api/v1/realtime/events?since=1'],
    ['协议相对 URL', '//evil.example/api/v1/realtime/events?since=1'],
    ['反斜杠绕过', '/\\evil.example/api/v1/realtime/events'],
    ['非 /api/v1/ 前缀', '/other/path/events?since=1'],
    ['前缀形似但越界', '/api/v1.evil.example/events?since=1'],
    ['不可解析 URL', 'http://'],
  ])('%s 的 rest 被拒绝(MeshApiError)', (_name, rest) => {
    expect(() => resolveResyncUrl(rest)).toThrow(MeshApiError);
  });

  it('拒绝的 rest 经 reconciler 抛错且不发出任何携带 token 的请求', async () => {
    useAuthStore.getState().setToken('secret-token');
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    const client = { ingestReconciledEvent: vi.fn() } as never;
    const reconciler = createReconciler(client, fetchMock);
    await expect(
      reconciler({ channel: 'issue:1', watermark: 9, rest: 'https://evil.example/api/v1/x' }),
    ).rejects.toBeInstanceOf(MeshApiError);
    expect(fetchMock).not.toHaveBeenCalled(); // token 绝无外泄
    expect((client as { ingestReconciledEvent: ReturnType<typeof vi.fn> }).ingestReconciledEvent).not.toHaveBeenCalled();
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
        channels: ['workspace:ws-1:issues'],
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
        channels: ['workspace:ws-1:issues'],
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
        channels: ['c'],
        intervalMs: 1,
        fetchImpl: fetchMock,
      }),
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('无已订阅频道(channels 为空)不轮询(MES-107:演示频道移除后无默认频道)', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    renderHook(() =>
      useOfflinePolling({
        client: stubClient(),
        state: 'reconnecting',
        enabled: true,
        channels: [],
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
          channels: ['workspace:ws-1:issues'],
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

describe('AppShell 实时网关建连(MES-106:绝对 ws(s):// 地址)', () => {
  /** 捕获构造 URL 的最小 WebSocket 替身(不触发任何回调) */
  class FakeWebSocket {
    static urls: string[] = [];

    onopen: (() => void) | null = null;

    onmessage: ((ev: { data: string }) => void) | null = null;

    onclose: (() => void) | null = null;

    onerror: (() => void) | null = null;

    readyState = 0;

    constructor(url: string) {
      FakeWebSocket.urls.push(url);
    }

    send(): void {}

    close(): void {}
  }

  afterEach(() => {
    useAuthStore.getState().clearToken();
    vi.unstubAllGlobals();
    FakeWebSocket.urls = [];
  });

  it('有 token → 以绝对 ws:// URL 建连(env 基址 + /ws,绝不发相对地址)', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    // shell 内偏好回填/离线轮询的网络副作用静默桩平(与本用例断言无关)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ data: [], next_cursor: null }), { status: 200 }),
      ),
    );
    useAuthStore.getState().setToken('tok_valid');
    renderShell('/');
    expect(FakeWebSocket.urls.length).toBeGreaterThan(0);
    for (const url of FakeWebSocket.urls) {
      expect(url).toMatch(/^wss?:\/\//); // 绝对地址(相对 '/ws' 会被构造器拒绝)
    }
    expect(FakeWebSocket.urls[0]).toBe(`${env.wsBaseUrl}/ws`);
  });

  it('无 token → 不建连(不构造 WebSocket,不触 WS)', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    renderShell('/');
    expect(FakeWebSocket.urls).toEqual([]);
  });
});
