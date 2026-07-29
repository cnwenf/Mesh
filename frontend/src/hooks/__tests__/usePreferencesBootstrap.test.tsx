/**
 * usePreferencesBootstrap 单测(theme.md §4.5 登录回填核心链路)。
 *
 * 覆盖验收基线 B1 要求:hydrate 成功/失败降级、重放触发器注册/拆卸、
 * 工作区基线写入竞态(bridge loaded/defaultTheme 两态)、卸载竞态(cancelled)、
 * 服务端快照回填事件(SERVER_SNAPSHOT_EVENT)监听与拆卸。
 *
 * store 与 pending 队列用真实模块(集成式断言其行为契约),仅注入
 * API 客户端(getApiClient 经模块 mock 返回 fetchImpl 受控的真实
 * MeshApiClient,包络解析与错误归一与生产一致)。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../api/client';
import { useAuthStore } from '../../state/authStore';
import {
  SERVER_SNAPSHOT_EVENT,
  enqueueFailedWrite,
  getActiveSubject,
  hasPendingWrites,
  noteServerUpdatedAt,
  setActiveUser,
  setActiveWorkspace,
} from '../../state/pendingSettingsQueue';
import type { ServerUserPreferences } from '../../api/userPreferences';
import { defaultPreferences, useSettingsStore } from '../../state/settingsStore';
import { useWorkspaceThemeBridge } from '../../state/workspaceThemeBridge';
import { usePreferencesBootstrap } from '../usePreferencesBootstrap';

/** 受控 API 客户端:getApiClient 返回本测试设置的实例(hoisted 规避提升限制)。 */
const mocks = vi.hoisted(() => ({ client: null as unknown as MeshApiClient | null }));

vi.mock('../../api/instance', () => ({
  getApiClient: (): MeshApiClient => {
    if (mocks.client === null) throw new Error('mock client not set');
    return mocks.client;
  },
  resetApiClient: vi.fn(),
}));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function errorResponse(status: number, code: string): Response {
  return jsonResponse(status, { error: { code, message: code } });
}

/** GET /me 单对象包络 */
function meResponse(me: Record<string, unknown>): Response {
  return jsonResponse(200, { data: me });
}

function workspaceListResponse(ids: string[]): Response {
  return jsonResponse(200, {
    data: ids.map((id) => ({
      id,
      name: 'WS',
      slug: `slug-${id}`,
      logo_url: null,
      my_role: 'owner',
      created_at: '2026-07-25T00:00:00Z',
    })),
    next_cursor: null,
  });
}

function workspaceDetailResponse(settings: Record<string, unknown>): Response {
  return jsonResponse(200, {
    data: {
      id: 'ws-1',
      name: 'WS',
      slug: 'slug-ws-1',
      logo_url: null,
      timezone: 'UTC',
      settings,
      my_role: 'owner',
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T00:00:00Z',
    },
  });
}

interface RouteHandlers {
  me?: () => Response | Promise<Response>;
  workspaceList?: () => Response | Promise<Response>;
  workspaceDetail?: () => Response | Promise<Response>;
  usersMePatch?: () => Response | Promise<Response>;
}

/** 路由式 fetch 桩:按 method + URL 分派,未登记路由返回 500(测试即失败点)。 */
function createFetch(handlers: RouteHandlers): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: unknown, init?: { method?: string }) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method === 'GET' && url.endsWith('/api/v1/me')) {
      return handlers.me !== undefined
        ? handlers.me()
        : errorResponse(500, 'unrouted_me');
    }
    if (method === 'GET' && url.endsWith('/api/v1/workspaces/ws-1')) {
      return handlers.workspaceDetail !== undefined
        ? handlers.workspaceDetail()
        : errorResponse(500, 'unrouted_detail');
    }
    if (method === 'GET' && url.includes('/api/v1/workspaces')) {
      return handlers.workspaceList !== undefined
        ? handlers.workspaceList()
        : workspaceListResponse([]);
    }
    if (method === 'PATCH' && url.endsWith('/api/v1/users/me')) {
      return handlers.usersMePatch !== undefined
        ? handlers.usersMePatch()
        : meResponse({ id: 'u1', timezone: null, settings: {} });
    }
    return errorResponse(500, 'unexpected_route');
  });
}

function createMockClient(fetchImpl: unknown): MeshApiClient {
  return new MeshApiClient({
    baseUrl: 'http://localhost:8901',
    getToken: () => 'test-token',
    fetchImpl: fetchImpl as typeof fetch,
  });
}

const BASE_ME = {
  id: 'u1',
  updated_at: '2026-07-29T00:00:00Z',
  timezone: null,
  settings: {},
} satisfies Record<string, unknown>;

/** 读出当前主体 pending 分区条目(键含 host,经前缀扫描定位)。 */
function readPendingEntries(): Array<{
  baselineUpdatedAt: string | null;
  subject: readonly [string, string, string];
}> {
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i);
    if (key !== null && key.startsWith('mesh.settings.pending:')) {
      return JSON.parse(localStorage.getItem(key) ?? '[]') as never;
    }
  }
  return [];
}

function resetStores(): void {
  localStorage.clear();
  useSettingsStore.setState({
    preferences: defaultPreferences(),
    lastSyncError: null,
    sessionProbed: false,
  });
  useAuthStore.setState({ token: null, refreshToken: null });
  useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: false });
  setActiveUser(null);
  setActiveWorkspace(null);
  noteServerUpdatedAt(null);
}

beforeEach(() => {
  resetStores();
});

describe('usePreferencesBootstrap(theme.md §4.5 登录回填)', () => {
  it('未登录(无 token)时不发请求、不注册重放触发器', () => {
    const fetchImpl = createFetch({});
    mocks.client = createMockClient(fetchImpl);
    const addSpy = vi.spyOn(window, 'addEventListener');

    renderHook(() => usePreferencesBootstrap());

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(addSpy).not.toHaveBeenCalledWith('online', expect.any(Function));
    expect(useSettingsStore.getState().sessionProbed).toBe(false);
    addSpy.mockRestore();
  });

  it('登录态 hydrate 成功:服务端值回填 + 主体与 updated_at 基线记录', async () => {
    const fetchImpl = createFetch({
      me: () =>
        meResponse({
          id: 'u1',
          updated_at: '2026-07-29T00:00:00Z',
          timezone: 'Asia/Shanghai',
          settings: { theme: 'dark', locale: 'zh-CN' },
        }),
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());

    await waitFor(() => expect(useSettingsStore.getState().sessionProbed).toBe(true));
    expect(useSettingsStore.getState().preferences).toEqual({
      theme: 'dark',
      locale: 'zh-CN',
      timezone: 'Asia/Shanghai',
    });
    expect(getActiveSubject().userId).toBe('u1');

    // updated_at 基线经 noteServerUpdatedAt 记录:入队条目携带该基线(§4.5 冲突策略)。
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    expect(hasPendingWrites()).toBe(true);
    expect(readPendingEntries()[0].baselineUpdatedAt).toBe('2026-07-29T00:00:00Z');
  });

  it('服务端无 settings 字段:theme 置 null、locale/timezone 保留本地镜像', async () => {
    useSettingsStore.setState({
      preferences: { theme: 'light', locale: 'en-US', timezone: 'UTC' },
    });
    const fetchImpl = createFetch({
      me: () => meResponse({ id: 'u1', timezone: null }),
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());

    await waitFor(() => expect(useSettingsStore.getState().sessionProbed).toBe(true));
    // settings?.theme absent → null(协商链自工作区默认起);locale/timezone 保留本地。
    expect(useSettingsStore.getState().preferences).toEqual({
      theme: null,
      locale: 'en-US',
      timezone: 'UTC',
    });
  });

  it('GET /me 无 id:主体置 null(队列不入队)、回填照常', async () => {
    const fetchImpl = createFetch({
      me: () => meResponse({ updated_at: null, timezone: null, settings: { theme: 'dark' } }),
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());

    await waitFor(() => expect(useSettingsStore.getState().sessionProbed).toBe(true));
    expect(useSettingsStore.getState().preferences.theme).toBe('dark');
    expect(getActiveSubject().userId).toBeNull();
  });

  it('GET /me 失败:静默降级,本地镜像不动、主体不置,但仍置 sessionProbed', async () => {
    useSettingsStore.setState({
      preferences: { theme: 'light', locale: null, timezone: 'UTC' },
    });
    const fetchImpl = createFetch({ me: () => errorResponse(500, 'internal_error') });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });
    const addSpy = vi.spyOn(window, 'addEventListener');

    renderHook(() => usePreferencesBootstrap());

    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    await waitFor(() => expect(useSettingsStore.getState().sessionProbed).toBe(true));
    // 降级:本地镜像继续可用,不当账号偏好回填;主体未置。
    expect(useSettingsStore.getState().preferences.theme).toBe('light');
    expect(getActiveSubject().userId).toBeNull();
    // 重放触发器仍注册(与 hydrate 成败无关)。
    expect(addSpy).toHaveBeenCalledWith('online', expect.any(Function));
    addSpy.mockRestore();
  });

  it('工作区主体:首个所属工作区 detail 后置 activeWorkspace,且不写主题桥接', async () => {
    useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: true });
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      workspaceList: () => workspaceListResponse(['ws-1']),
      workspaceDetail: () => workspaceDetailResponse({ default_theme: 'dark' }),
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());

    await waitFor(() => expect(getActiveSubject().workspaceId).toBe('ws-1'));
    // 桥接由 WorkspaceProvider 独占(§2.2:全局页无工作区上下文 → system):
    // detail 含 default_theme='dark' 亦不写入桥接。
    expect(useWorkspaceThemeBridge.getState()).toMatchObject({
      defaultTheme: null,
      loaded: true,
    });
  });

  it('桥接已持路由值(工作区导航后)时 bootstrap 同样不触碰', async () => {
    useWorkspaceThemeBridge.setState({ defaultTheme: 'light', loaded: true });
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      workspaceList: () => workspaceListResponse(['ws-1']),
      workspaceDetail: () => workspaceDetailResponse({ default_theme: 'dark' }),
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(3));
    expect(useWorkspaceThemeBridge.getState()).toMatchObject({
      defaultTheme: 'light',
      loaded: true,
    });
    expect(getActiveSubject().workspaceId).toBe('ws-1');
  });

  it('空工作区列表:不发起 detail 请求、工作区主体不置', async () => {
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      workspaceList: () => workspaceListResponse([]),
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());

    await waitFor(() => expect(useSettingsStore.getState().sessionProbed).toBe(true));
    // 仅 /me + 列表两步,无 detail。
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    expect(getActiveSubject().workspaceId).toBeNull();
  });

  it('工作区列表失败:静默降级(不抛错、不置工作区主体)', async () => {
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      workspaceList: () => errorResponse(500, 'internal_error'),
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());

    await waitFor(() => expect(useSettingsStore.getState().sessionProbed).toBe(true));
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    expect(getActiveSubject().workspaceId).toBeNull();
    expect(useWorkspaceThemeBridge.getState().defaultTheme).toBeNull();
  });

  it('detail 失败:静默降级(不抛错、工作区主体不置)', async () => {
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      workspaceList: () => workspaceListResponse(['ws-1']),
      workspaceDetail: () => errorResponse(404, 'not_found'),
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(3));
    expect(getActiveSubject().workspaceId).toBeNull();
    expect(useWorkspaceThemeBridge.getState().loaded).toBe(false);
  });

  it('重放触发器注册(online/前台恢复)并真实重放 pending 后清空', async () => {
    const patch = vi.fn(() => meResponse(BASE_ME));
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      usersMePatch: patch,
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });
    const addSpy = vi.spyOn(window, 'addEventListener');

    renderHook(() => usePreferencesBootstrap());
    await waitFor(() => expect(getActiveSubject().userId).toBe('u1'));
    expect(addSpy).toHaveBeenCalledWith('online', expect.any(Function));

    // 入队一条失败写(主体 u1/无工作区);online 触发 → 快照不较新 → PATCH 重放 → 出队。
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    expect(hasPendingWrites()).toBe(true);
    await act(async () => {
      window.dispatchEvent(new Event('online'));
    });
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(hasPendingWrites()).toBe(false));
    addSpy.mockRestore();
  });

  it('前台恢复(visibilitychange→visible)触发重放', async () => {
    const patch = vi.fn(() => meResponse(BASE_ME));
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      usersMePatch: patch,
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());
    await waitFor(() => expect(getActiveSubject().userId).toBe('u1'));

    enqueueFailedWrite({ settings: { locale: 'zh-CN' } });
    // jsdom 默认 visibilityState='visible' → 事件即触发 flush。
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(hasPendingWrites()).toBe(false));
  });

  it('卸载拆卸:触发器与快照监听移除、主体复位', async () => {
    const patch = vi.fn(() => meResponse(BASE_ME));
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      usersMePatch: patch,
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });
    const removeSpy = vi.spyOn(window, 'removeEventListener');

    const { unmount } = renderHook(() => usePreferencesBootstrap());
    await waitFor(() => expect(getActiveSubject().userId).toBe('u1'));

    enqueueFailedWrite({ settings: { theme: 'dark' } });
    const callsBefore = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls.length;
    unmount();

    // 拆卸:主体复位;online 不再触发重放(PATCH 不发生、条目保留)。
    expect(getActiveSubject().userId).toBeNull();
    expect(removeSpy).toHaveBeenCalledWith('online', expect.any(Function));
    window.dispatchEvent(new Event('online'));
    await act(async () => {
      await Promise.resolve();
    });
    expect(patch).not.toHaveBeenCalled();
    // 条目保留于分区(主体复位后 hasPendingWrites 无活跃键,直读分区验证未重放)。
    expect(readPendingEntries()).toHaveLength(1);
    expect((fetchImpl as ReturnType<typeof vi.fn>).mock.calls.length).toBe(callsBefore);

    // 快照监听已拆卸:再派发不回填。
    window.dispatchEvent(
      new CustomEvent<ServerUserPreferences>(SERVER_SNAPSHOT_EVENT, {
        detail: { timezone: 'Europe/Paris', settings: { theme: 'dark' } },
      }),
    );
    expect(useSettingsStore.getState().preferences.theme).toBeNull();
    removeSpy.mockRestore();
  });

  it('SERVER_SNAPSHOT_EVENT:重放发现服务端较新时监听方回填本地(含基线前移)', async () => {
    const fetchImpl = createFetch({ me: () => meResponse(BASE_ME) });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    renderHook(() => usePreferencesBootstrap());
    await waitFor(() => expect(getActiveSubject().userId).toBe('u1'));

    act(() => {
      window.dispatchEvent(
        new CustomEvent<ServerUserPreferences>(SERVER_SNAPSHOT_EVENT, {
          detail: {
            id: 'u1',
            updated_at: '2026-07-29T09:00:00Z',
            timezone: 'Europe/Paris',
            settings: { theme: 'dark', locale: 'fr-FR' },
          },
        }),
      );
    });

    expect(useSettingsStore.getState().preferences).toEqual({
      theme: 'dark',
      locale: 'fr-FR',
      timezone: 'Europe/Paris',
    });
    // 基线前移:后续入队条目携带快照 updated_at。
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    expect(readPendingEntries()[0].baselineUpdatedAt).toBe('2026-07-29T09:00:00Z');
  });

  it('卸载竞态:GET /me 在卸载后才返回 → 不回填、不置主体、不 probed', async () => {
    let resolveMe: (value: Response) => void = () => {};
    const fetchImpl = createFetch({
      me: () =>
        new Promise<Response>((resolve) => {
          resolveMe = resolve;
        }),
    });
    mocks.client = createMockClient(fetchImpl);
    useAuthStore.setState({ token: 'tk' });

    const { unmount } = renderHook(() => usePreferencesBootstrap());
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    unmount();

    await act(async () => {
      resolveMe(
        meResponse({
          id: 'u1',
          updated_at: '2026-07-29T00:00:00Z',
          timezone: null,
          settings: { theme: 'dark' },
        }),
      );
      await Promise.resolve();
    });

    expect(useSettingsStore.getState().sessionProbed).toBe(false);
    expect(useSettingsStore.getState().preferences.theme).toBeNull();
    expect(getActiveSubject().userId).toBeNull();
  });

  it('卸载竞态:列表在卸载后才返回 → 不置工作区主体、不写基线', async () => {
    let resolveList: (value: Response) => void = () => {};
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      workspaceList: () =>
        new Promise<Response>((resolve) => {
          resolveList = resolve;
        }),
    });
    mocks.client = createMockClient(fetchImpl);
    useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: true });
    useAuthStore.setState({ token: 'tk' });

    const { unmount } = renderHook(() => usePreferencesBootstrap());
    await waitFor(() => expect(getActiveSubject().userId).toBe('u1'));
    unmount();

    await act(async () => {
      resolveList(workspaceListResponse(['ws-1']));
      await Promise.resolve();
    });

    expect(getActiveSubject().workspaceId).toBeNull();
    expect(useWorkspaceThemeBridge.getState().defaultTheme).toBeNull();
  });

  it('卸载竞态:detail 在卸载后才返回 → 不置工作区主体、不写基线', async () => {
    let resolveDetail: (value: Response) => void = () => {};
    const fetchImpl = createFetch({
      me: () => meResponse(BASE_ME),
      workspaceList: () => workspaceListResponse(['ws-1']),
      workspaceDetail: () =>
        new Promise<Response>((resolve) => {
          resolveDetail = resolve;
        }),
    });
    mocks.client = createMockClient(fetchImpl);
    useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: true });
    useAuthStore.setState({ token: 'tk' });

    const { unmount } = renderHook(() => usePreferencesBootstrap());
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(3));
    unmount();

    await act(async () => {
      resolveDetail(workspaceDetailResponse({ default_theme: 'dark' }));
      await Promise.resolve();
    });

    expect(getActiveSubject().workspaceId).toBeNull();
    expect(useWorkspaceThemeBridge.getState().defaultTheme).toBeNull();
  });

  it('登录态切换:false→true 启动回填;true→false 清理主体', async () => {
    const fetchImpl = createFetch({ me: () => meResponse(BASE_ME) });
    mocks.client = createMockClient(fetchImpl);

    renderHook(() => usePreferencesBootstrap());
    expect(fetchImpl).not.toHaveBeenCalled();

    act(() => {
      useAuthStore.setState({ token: 'tk' });
    });
    await waitFor(() => expect(getActiveSubject().userId).toBe('u1'));

    act(() => {
      useAuthStore.setState({ token: null });
    });
    await waitFor(() => expect(getActiveSubject().userId).toBeNull());
  });
});
