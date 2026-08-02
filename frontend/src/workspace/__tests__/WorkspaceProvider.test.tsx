import { act, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate, useParams } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MeshApiError } from '../../api/errors';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { RealtimeContext } from '../../shell/AppShell';
import type { RealtimeContextValue } from '../../shell/AppShell';
import { useAuthStore } from '../../state/authStore';
import { renderWithProviders } from '../../test-utils/render';
import { WorkspaceProvider, useWorkspace, workspaceChannel } from '../WorkspaceProvider';
import type { ReactNode } from 'react';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const DETAIL = {
  id: 'ws-1',
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  timezone: 'UTC',
  settings: { default_locale: 'zh-CN' },
  my_role: 'owner',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

function stubClient(...responses: Array<{ status: number; body: unknown }>) {
  const fetchImpl = vi.fn();
  for (const response of responses) {
    fetchImpl.mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(response.status, response.body)),
    );
  }
  return {
    fetchImpl,
    client: {
      baseUrl: 'http://localhost',
      request: async (method: string, path: string, opts: { body?: unknown } = {}) => {
        const response = await fetchImpl(`http://localhost${path}`, {
          method,
          body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        });
        const body = (await response.json()) as { data?: unknown; error?: unknown };
        if (!response.ok) {
          const error = body.error as {
            code: string;
            message: string;
            details?: Record<string, unknown>;
          };
          throw new MeshApiError({
            status: response.status,
            code: error.code,
            message: error.message,
            details: error.details,
          });
        }
        return body.data;
      },
      list: async (path: string) => {
        const response = await fetchImpl(`http://localhost${path}`, { method: 'GET' });
        return response.json();
      },
    },
  };
}

/** 模拟 AppShell 接线:slug 取自路由参数(重定向后随 URL 更新)。 */
function RouteDrivenProvider(props: { client: unknown; children: ReactNode }): React.JSX.Element {
  const { workspaceSlug } = useParams();
  return (
    <WorkspaceProvider slug={workspaceSlug ?? ''} client={props.client as never}>
      {props.children}
    </WorkspaceProvider>
  );
}

function WorkspaceRouteProbe(): React.JSX.Element {
  const navigate = useNavigate();
  const { workspaceSlug } = useParams();
  return (
    <>
      <span data-testid="route-workspace-slug">{workspaceSlug}</span>
      <button type="button" data-testid="switch-to-beta" onClick={() => navigate('/w/beta')}>
        beta
      </button>
    </>
  );
}

function Probe(): React.JSX.Element {
  const context = useWorkspace();
  return (
    <div>
      <span data-testid="probe-status">{context.status}</span>
      <span data-testid="probe-admin">{String(context.isAdmin)}</span>
      <span data-testid="probe-owner">{String(context.isOwner)}</span>
      <span data-testid="probe-name">{context.workspace?.name ?? ''}</span>
      <span data-testid="probe-slug">{context.workspace?.slug ?? ''}</span>
      <span data-testid="probe-error-code">{context.error?.code ?? ''}</span>
      <span data-testid="probe-locale">
        {String(context.workspace?.settings.default_locale ?? '')}
      </span>
      <button type="button" data-testid="probe-refresh" onClick={() => void context.refresh()}>
        refresh
      </button>
      <button
        type="button"
        data-testid="probe-patch"
        onClick={() => void context.patch({ name: 'NewName' })}
      >
        patch
      </button>
    </div>
  );
}

interface FakeRealtimeClient {
  subscribe: ReturnType<typeof vi.fn>;
  unsubscribe: ReturnType<typeof vi.fn>;
  onFrame: ReturnType<typeof vi.fn>;
  getCursor: ReturnType<typeof vi.fn>;
  ingestReconciledEvent: ReturnType<typeof vi.fn>;
  frames: Array<(frame: unknown) => void>;
}

function createFakeRealtime(state: RealtimeContextValue['state'] = 'connected'): {
  value: RealtimeContextValue;
  client: FakeRealtimeClient;
} {
  const frames: Array<(frame: unknown) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((cb: (frame: unknown) => void) => {
      frames.push(cb);
      return () => {
        const index = frames.indexOf(cb);
        if (index >= 0) frames.splice(index, 1);
      };
    }),
    getCursor: vi.fn(() => undefined),
    ingestReconciledEvent: vi.fn(),
    frames,
  };
  return { value: { state, client: client as never }, client };
}

function renderProvider(
  slug: string,
  client: unknown,
  realtime?: RealtimeContextValue | null,
): ReturnType<typeof render> {
  return render(providerTree(slug, client, realtime));
}

function providerTree(
  slug: string,
  client: unknown,
  realtime?: RealtimeContextValue | null,
): React.JSX.Element {
  const provider = (
    <WorkspaceProvider slug={slug} client={client as never}>
      <Probe />
    </WorkspaceProvider>
  );
  const tree =
    realtime !== undefined ? (
      <RealtimeContext.Provider value={realtime}>{provider}</RealtimeContext.Provider>
    ) : (
      provider
    );
  return (
    <MemoryRouter initialEntries={[`/w/${slug}`]}>
      <ThemeProvider>
        <I18nProvider
          workspaceDefaultLocale={null}
          reporter={{ report: () => undefined, reported: [] }}
        >
          <ToastProvider regionLabel="notifications">{tree}</ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('WorkspaceProvider(工作区上下文,workspace.md §4.1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().setToken(null);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    useAuthStore.getState().setToken(null);
  });

  it('by-slug 加载成功 → ready + 角色派生', async () => {
    const { client } = stubClient({ status: 200, body: { data: DETAIL } });
    renderProvider('acme', client);

    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('ready'));
    expect(screen.getByTestId('probe-name').textContent).toBe('Acme');
    expect(screen.getByTestId('probe-admin').textContent).toBe('true');
    expect(screen.getByTestId('probe-owner').textContent).toBe('true');
  });

  it('404 → not_found(与不存在同形,无泄漏)', async () => {
    const { client } = stubClient({
      status: 404,
      body: { error: { code: 'not_found', message: 'workspace not found' } },
    });
    renderProvider('ghost', client);

    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('not_found'));
    expect(screen.getByTestId('probe-admin').textContent).toBe('false');
  });

  it('500 → error,refresh 可重试', async () => {
    const { client, fetchImpl } = stubClient(
      { status: 500, body: { error: { code: 'internal_error', message: 'boom' } } },
      { status: 200, body: { data: DETAIL } },
    );
    renderProvider('acme', client);

    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('error'));
    await act(async () => {
      screen.getByTestId('probe-refresh').click();
    });
    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('ready'));
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('非 API 异常 → 统一映射为 unknown error', async () => {
    const client = {
      request: vi.fn().mockRejectedValue(new Error('transport exploded')),
    };
    renderProvider('acme', client);

    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('error'));
    expect(screen.getByTestId('probe-error-code').textContent).toBe('unknown');
  });

  it('member 角色 → isAdmin/isOwner 均为 false', async () => {
    const { client } = stubClient({
      status: 200,
      body: { data: { ...DETAIL, my_role: 'member' } },
    });
    renderProvider('acme', client);

    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('ready'));
    expect(screen.getByTestId('probe-admin').textContent).toBe('false');
    expect(screen.getByTestId('probe-owner').textContent).toBe('false');
  });

  it('patch → PATCH 请求后就地更新上下文', async () => {
    const { client, fetchImpl } = stubClient(
      { status: 200, body: { data: DETAIL } },
      { status: 200, body: { data: { ...DETAIL, name: 'NewName' } } },
    );
    renderProvider('acme', client);
    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('ready'));

    await act(async () => {
      screen.getByTestId('probe-patch').click();
    });
    await waitFor(() => expect(screen.getByTestId('probe-name').textContent).toBe('NewName'));
    const [, init] = fetchImpl.mock.calls[1] as [string, { method: string; body: string }];
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body)).toEqual({ name: 'NewName' });
  });

  it('历史 slug 解析到当前工作区 → replace 规范化路由(W6)', async () => {
    // 规范化后 RouteDrivenProvider 以新 slug 重拉(与 AppShell 接线一致),桩给两发响应
    const { client } = stubClient(
      { status: 200, body: { data: { ...DETAIL, slug: 'acme-corp' } } },
      { status: 200, body: { data: { ...DETAIL, slug: 'acme-corp' } } },
    );
    render(
      <MemoryRouter initialEntries={['/w/old-acme/settings']}>
        <ThemeProvider>
          <I18nProvider
            workspaceDefaultLocale={null}
            reporter={{ report: () => undefined, reported: [] }}
          >
            <ToastProvider regionLabel="notifications">
              <Routes>
                <Route
                  path="/w/:workspaceSlug/*"
                  element={
                    <RouteDrivenProvider client={client}>
                      <Routes>
                        <Route path="settings" element={<span data-testid="at-settings" />} />
                      </Routes>
                      <Probe />
                    </RouteDrivenProvider>
                  }
                />
              </Routes>
            </ToastProvider>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );

    // 规范化后 URL 指向当前 slug,设置子路由仍在
    await waitFor(() => expect(screen.getByTestId('at-settings')).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId('probe-slug').textContent).toBe('acme-corp'));
  });

  it('切换 slug 时不会用上一工作区的暂存数据回滚路由', async () => {
    const beta = { ...DETAIL, id: 'ws-2', name: 'Beta', slug: 'beta' };
    const { client, fetchImpl } = stubClient({ status: 200, body: { data: DETAIL } });
    let resolveBeta: ((response: Response) => void) | undefined;
    fetchImpl.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveBeta = resolve;
        }),
    );
    // 旧数据若错误地把路由推回 acme，会触发第三次加载；给它响应以暴露稳定回滚结果。
    fetchImpl.mockImplementationOnce(() => Promise.resolve(jsonResponse(200, { data: DETAIL })));
    render(
      <MemoryRouter initialEntries={['/w/acme']}>
        <ThemeProvider>
          <I18nProvider
            workspaceDefaultLocale={null}
            reporter={{ report: () => undefined, reported: [] }}
          >
            <ToastProvider regionLabel="notifications">
              <Routes>
                <Route
                  path="/w/:workspaceSlug"
                  element={
                    <RouteDrivenProvider client={client}>
                      <WorkspaceRouteProbe />
                      <Probe />
                    </RouteDrivenProvider>
                  }
                />
              </Routes>
            </ToastProvider>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByTestId('probe-slug').textContent).toBe('acme'));
    act(() => screen.getByTestId('switch-to-beta').click());
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    await act(async () => undefined);
    const slugWhileBetaLoads = screen.getByTestId('route-workspace-slug').textContent;
    await act(async () => {
      resolveBeta?.(jsonResponse(200, { data: beta }));
    });

    expect(slugWhileBetaLoads).toBe('beta');
    await waitFor(() => expect(screen.getByTestId('probe-slug').textContent).toBe('beta'));
  });

  it('快速切换 slug 时丢弃旧请求的迟到成功响应', async () => {
    const beta = { ...DETAIL, id: 'ws-2', name: 'Beta', slug: 'beta' };
    const { client, fetchImpl } = stubClient();
    let resolveAcme: ((response: Response) => void) | undefined;
    fetchImpl.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveAcme = resolve;
        }),
    );
    fetchImpl.mockImplementationOnce(() => Promise.resolve(jsonResponse(200, { data: beta })));

    render(
      <MemoryRouter initialEntries={['/w/acme']}>
        <ThemeProvider>
          <I18nProvider
            workspaceDefaultLocale={null}
            reporter={{ report: () => undefined, reported: [] }}
          >
            <ToastProvider regionLabel="notifications">
              <Routes>
                <Route
                  path="/w/:workspaceSlug"
                  element={
                    <RouteDrivenProvider client={client}>
                      <WorkspaceRouteProbe />
                      <Probe />
                    </RouteDrivenProvider>
                  }
                />
              </Routes>
            </ToastProvider>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    act(() => screen.getByTestId('switch-to-beta').click());
    await waitFor(() => expect(screen.getByTestId('probe-slug').textContent).toBe('beta'));

    await act(async () => {
      resolveAcme?.(jsonResponse(200, { data: DETAIL }));
    });

    expect(screen.getByTestId('route-workspace-slug').textContent).toBe('beta');
    expect(screen.getByTestId('probe-slug').textContent).toBe('beta');
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('WS 重连时以现有游标轮询 REST 对账并注入真实帧', async () => {
    const { client } = stubClient({ status: 200, body: { data: DETAIL } });
    const realtime = createFakeRealtime('connected');
    realtime.client.getCursor.mockReturnValue(7);
    useAuthStore.getState().setToken('jwt-test');
    const restFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        data: [
          {
            channel: workspaceChannel('ws-1'),
            seq: 8,
            event: 'workspace.updated',
            payload: { workspace_id: 'ws-1', changes: { name: 'Polled' } },
          },
        ],
        next_cursor: null,
      }),
    );
    vi.stubGlobal('fetch', restFetch);
    const view = renderProvider('acme', client, realtime.value);
    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('ready'));

    vi.useFakeTimers();
    view.rerender(
      providerTree('acme', client, {
        state: 'reconnecting',
        client: realtime.value.client,
      }),
    );
    await act(async () => {
      await vi.advanceTimersToNextTimerAsync();
    });

    expect(restFetch).toHaveBeenCalledWith(
      expect.stringContaining('channel=workspace%3Aws-1&since=7'),
      expect.objectContaining({ headers: { Authorization: 'Bearer jwt-test' } }),
    );
    expect(realtime.client.ingestReconciledEvent).toHaveBeenCalledWith(
      expect.objectContaining({ channel: workspaceChannel('ws-1'), seq: 8 }),
    );
    view.unmount();
  });

  it('realtime workspace.updated → changes 浅合并进上下文(settings 键级合并)', async () => {
    const { client } = stubClient({ status: 200, body: { data: DETAIL } });
    const realtime = createFakeRealtime('connected');
    renderProvider('acme', client, realtime.value);
    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('ready'));
    // 等订阅 effect 登记完成再断言/发射,消除「探针 ready 早于 subscribe 注册」的
    // 时序竞态(CI quality 间歇红因;与同文件 deleted 用例既有反竞态模式同构——
    // subscribe 与 onFrame 同一 effect 登记,waitFor subscribe 即保证帧回调就绪)。
    await waitFor(() =>
      expect(realtime.client.subscribe).toHaveBeenCalledWith(workspaceChannel('ws-1')),
    );

    act(() => {
      for (const cb of realtime.client.frames) {
        cb({
          op: 'event',
          channel: workspaceChannel('ws-1'),
          seq: 1,
          event: 'workspace.updated',
          payload: {
            workspace_id: 'ws-1',
            changes: { name: 'Acme2', settings: { default_locale: 'en' } },
          },
        });
      }
    });

    await waitFor(() => expect(screen.getByTestId('probe-name').textContent).toBe('Acme2'));
    expect(screen.getByTestId('probe-locale').textContent).toBe('en');
  });

  it('realtime workspace.deleted → 返回首页并提示', async () => {
    const { client } = stubClient({ status: 200, body: { data: DETAIL } });
    const realtime = createFakeRealtime('connected');
    render(
      <MemoryRouter initialEntries={['/w/acme']}>
        <ThemeProvider>
          <I18nProvider
            workspaceDefaultLocale={null}
            reporter={{ report: () => undefined, reported: [] }}
          >
            <ToastProvider regionLabel="notifications">
              <RealtimeContext.Provider value={realtime.value}>
                <WorkspaceProvider slug="acme" client={client as never}>
                  <Probe />
                </WorkspaceProvider>
              </RealtimeContext.Provider>
              <Routes>
                <Route path="/" element={<span data-testid="home-page" />} />
                <Route path="/w/:workspaceSlug" element={<span />} />
              </Routes>
            </ToastProvider>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('ready'));
    // 等订阅 effect 登记完成再发射,消除「探针 ready 早于 onFrame 注册」的时序竞态。
    await waitFor(() =>
      expect(realtime.client.subscribe).toHaveBeenCalledWith(workspaceChannel('ws-1')),
    );
    await waitFor(() => expect(realtime.client.frames.length).toBeGreaterThan(0));

    act(() => {
      for (const cb of [...realtime.client.frames]) {
        cb({
          op: 'event',
          channel: workspaceChannel('ws-1'),
          seq: 2,
          event: 'workspace.deleted',
          payload: { workspace_id: 'ws-1' },
        });
      }
    });

    await waitFor(() => expect(screen.getByTestId('home-page')).toBeTruthy());
  });

  it('其他工作区的帧被忽略', async () => {
    const { client } = stubClient({ status: 200, body: { data: DETAIL } });
    const realtime = createFakeRealtime('connected');
    renderProvider('acme', client, realtime.value);
    await waitFor(() => expect(screen.getByTestId('probe-status').textContent).toBe('ready'));

    act(() => {
      for (const cb of realtime.client.frames) {
        cb({
          op: 'event',
          channel: 'workspace:other',
          seq: 1,
          event: 'workspace.updated',
          payload: { workspace_id: 'other', changes: { name: 'X' } },
        });
      }
    });

    expect(screen.getByTestId('probe-name').textContent).toBe('Acme');
  });
});

describe('useWorkspace 契约', () => {
  it('Provider 外调用抛错', () => {
    expect(() => renderWithProviders(<Probe />)).toThrow(/WorkspaceProvider/);
  });
});

describe('workspaceChannel', () => {
  it('生成 workspace:{id} 频道名(§3.5)', () => {
    expect(workspaceChannel('ws-1')).toBe('workspace:ws-1');
  });
});
