/**
 * RuntimesPage 组件测试(runtime.md §4.1):行渲染(状态点 + 名称 + 负载 + 心跳)、
 * 空态、队列深度横幅(queue.depth_changed 帧)、行级实时重拉(runtime.* 帧)、
 * 名称搜索、暂停 / 恢复 / 删除动作、状态 / 类型筛选参数。
 * 页面自建 client → 桩 global fetch,按 URL 派发响应。
 */
import { act } from 'react';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { RuntimesPage } from '../RuntimesPage';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const ME = {
  user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'T',
      workspace_slug: 't',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

const RUNTIME_ONLINE = {
  id: 'r-1',
  name: 'intranet-build-01',
  kind: 'self_hosted',
  status: 'online',
  labels: { region: 'intranet' },
  capabilities: ['python'],
  hostname: 'build-node-7',
  os: 'linux-x86_64',
  cpu_cores: 8,
  memory_mb: 32768,
  max_concurrent: 4,
  current_load: 2,
  last_heartbeat_at: new Date(Date.now() - 5_000).toISOString(),
  heartbeat_interval_seconds: 15,
  version: '1.4.2',
  created_at: '2026-01-01T00:00:00Z',
};

const RUNTIME_PAUSED = {
  ...RUNTIME_ONLINE,
  id: 'r-2',
  name: 'gpu-worker-02',
  status: 'paused',
  current_load: 0,
};

interface Recorded {
  url: string;
  method: string;
}

function setup(runtimes: unknown[] = [RUNTIME_ONLINE, RUNTIME_PAUSED]): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (method === 'POST' && url.endsWith('/runtimes')) {
      // 创建 runtime → 影子记录 + 一次性激活码(§4.3)
      return fakeResponse({
        body: {
          data: {
            ...RUNTIME_ONLINE,
            id: 'r-new',
            name: 'build-09',
            status: 'pending',
            activation: {
              code: 'ACT-TEST-CODE',
              expires_at: '2026-07-27T10:15:00Z',
              release: {
                artifact_url: 'https://releases.mesh.example/runtime/1.4.2/mesh-runtime.tar.gz',
                sha256: 'ab'.repeat(32),
                signature_url:
                  'https://releases.mesh.example/runtime/1.4.2/mesh-runtime.tar.gz.sig',
                signing_key_url: 'https://releases.mesh.example/mesh-release.pub',
              },
              activate_hint: 'mesh-runtime activate --activation-file ./activation.txt',
            },
          },
        },
      });
    }
    if (method !== 'GET') return fakeResponse({ body: { data: RUNTIME_ONLINE } });
    return fakeResponse({ body: { data: runtimes, next_cursor: null } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

type FrameListener = (frame: RealtimeEventFrame) => void;

function makeRealtime() {
  const listeners = new Set<FrameListener>();
  const subscribed: string[] = [];
  const client = {
    subscribe: (channel: string) => {
      subscribed.push(channel);
    },
    unsubscribe: vi.fn(),
    onFrame: (cb: FrameListener) => {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
  };
  return {
    value: { state: 'connected', client } as unknown as RealtimeContextValue,
    emit: (frame: RealtimeEventFrame) => {
      act(() => {
        for (const listener of listeners) listener(frame);
      });
    },
    subscribed,
  };
}

function renderPage(realtime: ReturnType<typeof makeRealtime> | null = null) {
  const page = <RuntimesPage />;
  // Routes 包裹:列表挂 `/`,详情深链 `/runtimes/:runtimeId` 以哨兵路由承接导航断言。
  return renderWithProviders(
    <Routes>
      <Route
        path="/"
        element={
          realtime === null ? (
            page
          ) : (
            <RealtimeContext.Provider value={realtime.value}>{page}</RealtimeContext.Provider>
          )
        }
      />
      <Route path="/runtimes/:runtimeId" element={<div data-testid="navigated-to-detail" />} />
    </Routes>,
  );
}

describe('RuntimesPage', () => {
  it('uses the shared DataView page pattern for the runtime inventory', async () => {
    setup();
    renderPage();
    expect(await screen.findByTestId('data-view')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Runtimes' })).toBeInTheDocument();
  });

  it('渲染行:状态文本 + 名称 + 类型 + 负载 + 心跳新鲜度', async () => {
    setup();
    renderPage();
    expect(await screen.findByTestId('runtime-row-r-1')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-name-r-1')).toHaveTextContent('intranet-build-01');
    expect(screen.getByTestId('runtime-row-r-1')).toHaveTextContent('Online');
    expect(screen.getByTestId('runtime-row-r-1')).toHaveTextContent('Self-hosted');
    expect(screen.getByTestId('runtime-load-r-1')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-heartbeat-r-1').textContent).toMatch(/s ago/);
    // 暂停态行呈现 Resume 动作而非 Pause。
    expect(screen.getByTestId('runtime-resume-r-2')).toBeInTheDocument();
    expect(screen.queryByTestId('runtime-pause-r-2')).toBeNull();
  });

  it('空列表呈现空态', async () => {
    setup([]);
    renderPage();
    expect(await screen.findByText('Nothing here yet')).toBeInTheDocument();
    expect(screen.queryByTestId('runtimes-table')).toBeNull();
  });

  it('队列深度横幅随 queue.depth_changed 帧更新(§3.6)', async () => {
    setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('runtime-row-r-1');
    expect(realtime.subscribed).toContain('workspace:ws-1:queue');
    expect(screen.queryByTestId('runtimes-queue-depth')).toBeNull();
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:queue',
      seq: 1,
      event: 'queue.depth_changed',
      payload: { depth: 7 },
    });
    expect(await screen.findByTestId('runtimes-queue-depth')).toHaveTextContent('7');
  });

  it('runtime.* 生命周期帧触发整列重拉', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('runtime-row-r-1');
    expect(realtime.subscribed).toContain('workspace:ws-1:runtimes');
    const listCallsBefore = calls.filter(
      (c) => c.url.includes('/runtimes') && c.method === 'GET',
    ).length;
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:runtimes',
      seq: 2,
      event: 'runtime.online',
      payload: { data: { id: 'r-1' } },
    });
    await waitFor(() =>
      expect(
        calls.filter((c) => c.url.includes('/runtimes') && c.method === 'GET').length,
      ).toBeGreaterThan(listCallsBefore),
    );
  });

  it('其它频道 / 无关事件不触发重拉', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('runtime-row-r-1');
    const before = calls.filter((c) => c.url.includes('/runtimes') && c.method === 'GET').length;
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:issues',
      seq: 3,
      event: 'runtime.online',
      payload: {},
    });
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:runtimes',
      seq: 4,
      event: 'unrelated.event',
      payload: {},
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls.filter((c) => c.url.includes('/runtimes') && c.method === 'GET').length).toBe(
      before,
    );
  });

  it('名称搜索客户端过滤行', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('runtime-row-r-1');
    await user.type(screen.getByTestId('runtimes-search'), 'gpu-worker');
    expect(screen.queryByTestId('runtime-row-r-1')).toBeNull();
    expect(screen.getByTestId('runtime-row-r-2')).toBeInTheDocument();
    // 搜索无命中 → 空态
    await user.clear(screen.getByTestId('runtimes-search'));
    await user.type(screen.getByTestId('runtimes-search'), 'does-not-exist');
    expect(screen.getByText('Nothing here yet')).toBeInTheDocument();
  });

  it('暂停动作 POST :pause 并提示', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('runtime-row-r-1');
    await user.click(screen.getByTestId('runtime-pause-r-1'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/runtimes/r-1:pause') && c.method === 'POST')).toBe(
        true,
      ),
    );
  });

  it('删除动作(离线 / 下线行)POST DELETE', async () => {
    const user = userEvent.setup();
    const offline = { ...RUNTIME_ONLINE, id: 'r-3', name: 'old-laptop', status: 'unavailable' };
    const calls = setup([offline]);
    renderPage();
    await screen.findByTestId('runtime-row-r-3');
    await user.click(screen.getByTestId('runtime-delete-r-3'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/runtimes/r-3') && c.method === 'DELETE')).toBe(
        true,
      ),
    );
  });

  it('状态 / 类型筛选经 URL 参数下发服务端', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('runtime-row-r-1');
    await user.selectOptions(screen.getByTestId('runtimes-status-filter'), 'online');
    await user.selectOptions(screen.getByTestId('runtimes-kind-filter'), 'self_hosted');
    await waitFor(() =>
      expect(
        calls.some((c) => c.url.includes('status=online') && c.url.includes('kind=self_hosted')),
      ).toBe(true),
    );
  });

  it('打开注册向导并创建(→ 安装步)', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('runtime-row-r-1');
    await user.click(screen.getByTestId('new-runtime-button'));
    expect(screen.getByTestId('runtime-wizard-basic')).toBeInTheDocument();
    await user.type(screen.getByTestId('runtime-wizard-name'), 'build-09');
    await user.click(screen.getByTestId('runtime-wizard-next'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith('/runtimes') && c.method === 'POST')).toBe(true),
    );
    expect(await screen.findByTestId('runtime-wizard-install')).toBeInTheDocument();
  });

  it('无工作区时呈现空态(无成员身份)', async () => {
    const impl = (async () =>
      fakeResponse({
        body: { data: { user: { id: 'u-1' }, memberships: [] } },
      })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByText('Nothing here yet')).toBeInTheDocument();
    expect(screen.queryByTestId('new-runtime-button')).toBeNull();
  });

  it('稀疏 runtime:从未心跳呈现 never,max_concurrent=0 负载条 0%', async () => {
    const pending = {
      ...RUNTIME_ONLINE,
      id: 'r-4',
      name: 'pending-box',
      status: 'pending',
      last_heartbeat_at: null,
      max_concurrent: 0,
      current_load: 0,
    };
    setup([pending]);
    renderPage();
    await screen.findByTestId('runtime-row-r-4');
    expect(screen.getByTestId('runtime-heartbeat-r-4')).toHaveTextContent('never');
    expect(screen.getByTestId('runtime-load-r-4')).toHaveAttribute('aria-valuemax', '0');
  });

  it('行详情按钮导航至 /runtimes/{id}(哨兵路由承接)', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('runtime-row-r-1');
    await user.click(screen.getByTestId('runtime-detail-r-1'));
    expect(await screen.findByTestId('navigated-to-detail')).toBeInTheDocument();
    expect(screen.queryByTestId('runtimes-table')).toBeNull();
  });

  it('恢复动作 POST :resume', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('runtime-row-r-2');
    await user.click(screen.getByTestId('runtime-resume-r-2'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/runtimes/r-2:resume') && c.method === 'POST')).toBe(
        true,
      ),
    );
  });

  it('错误态重试按钮触发重拉', async () => {
    const user = userEvent.setup();
    let fail = true;
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (fail) {
        fail = false;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      }
      return fakeResponse({ body: { data: [RUNTIME_ONLINE], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    const retry = await screen.findByRole('button', { name: 'Retry' });
    await user.click(retry);
    expect(await screen.findByTestId('runtime-row-r-1')).toBeInTheDocument();
  });

  it('向导经 Dialog 关闭按钮关闭(onClose)', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('runtime-row-r-1');
    await user.click(screen.getByTestId('new-runtime-button'));
    expect(screen.getByTestId('runtime-wizard-basic')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(screen.queryByTestId('runtime-wizard-basic')).toBeNull();
  });

  it('加载失败呈现错误态并可重试', async () => {
    let first = true;
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (first) {
        first = false;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      }
      return fakeResponse({ body: { data: [RUNTIME_ONLINE], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  });
});
