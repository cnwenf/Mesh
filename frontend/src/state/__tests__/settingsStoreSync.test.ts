import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../api/client';
import {
  SETTINGS_STORAGE_KEY,
  THEME_MIRROR_KEY,
  bindSyncClient,
  defaultPreferences,
  detectTimezone,
  getPreferences,
  getLastSyncError,
  unbindSyncClient,
  useSettingsStore,
} from '../settingsStore';

function createMockClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({
    baseUrl: 'http://localhost:8901',
    getToken: () => 'test-token',
    fetchImpl,
  });
}

function successFetch(): typeof fetch {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ data: {} }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

function errorFetch(status: number, code: string, message: string): typeof fetch {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ error: { code, message } }), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

describe('settingsStore 基础行为(README §6.12/§6.18)', () => {
  afterEach(() => {
    unbindSyncClient();
  });

  it('默认偏好:theme=system、locale=null、timezone=检测值', () => {
    const { result } = renderHook(() => useSettingsStore());
    expect(result.current.preferences.theme).toBe('system');
    expect(result.current.preferences.locale).toBeNull();
    expect(result.current.preferences.timezone).toBe(detectTimezone());
  });

  it('setTheme 不可变更新并镜像 mesh.theme 键', () => {
    const { result } = renderHook(() => useSettingsStore());
    const before = result.current.preferences;
    act(() => result.current.setTheme('dark'));
    expect(result.current.preferences.theme).toBe('dark');
    expect(result.current.preferences).not.toBe(before);
    expect(localStorage.getItem(THEME_MIRROR_KEY)).toBe('dark');
  });

  it('setLocale 支持置 null(恢复跟随默认)', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setLocale('zh-CN'));
    expect(result.current.preferences.locale).toBe('zh-CN');
    act(() => result.current.setLocale(null));
    expect(result.current.preferences.locale).toBeNull();
  });

  it('setTimezone 更新 IANA 时区', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTimezone('America/New_York'));
    expect(result.current.preferences.timezone).toBe('America/New_York');
  });

  it('resetPreferences 恢复默认并重新镜像主题', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => {
      result.current.setTheme('dark');
      result.current.setLocale('en-US');
      result.current.setTimezone('Asia/Shanghai');
    });
    act(() => result.current.resetPreferences());
    expect(result.current.preferences).toEqual(defaultPreferences());
    expect(localStorage.getItem(THEME_MIRROR_KEY)).toBe('system');
  });

  it('偏好持久化到 mesh.settings.v1', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('light'));
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw as string) as {
      state: { preferences: { theme: string } };
    };
    expect(parsed.state.preferences.theme).toBe('light');
  });

  it('localStorage 镜像写失败时 setTheme 仍生效(静默降级)', () => {
    const original = Storage.prototype.setItem;
    const setter = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(function (this: Storage, key: string, value: string) {
        if (key === THEME_MIRROR_KEY) throw new Error('quota');
        original.call(this, key, value);
      });
    const { result } = renderHook(() => useSettingsStore());
    expect(() => act(() => result.current.setTheme('dark'))).not.toThrow();
    expect(result.current.preferences.theme).toBe('dark');
    setter.mockRestore();
  });

  it('detectTimezone 在 Intl 异常时回退 UTC', () => {
    const original = Intl.DateTimeFormat;
    // @ts-expect-error 模拟 Intl 不可用
    Intl.DateTimeFormat = () => {
      throw new Error('no intl');
    };
    try {
      expect(detectTimezone()).toBe('UTC');
    } finally {
      Intl.DateTimeFormat = original;
    }
  });

  it('lastSyncError 初始为 null', () => {
    const { result } = renderHook(() => useSettingsStore());
    expect(result.current.lastSyncError).toBeNull();
  });

  it('clearSyncError 清除错误状态', () => {
    const { result } = renderHook(() => useSettingsStore());
    // 手动设置错误状态(通过内部 set)
    act(() => {
      useSettingsStore.setState({
        lastSyncError: { code: 'network', message: 'test', status: 0 },
      });
    });
    expect(result.current.lastSyncError).not.toBeNull();
    act(() => result.current.clearSyncError());
    expect(result.current.lastSyncError).toBeNull();
  });

  it('getPreferences 非 React 上下文读取偏好', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('dark'));
    expect(getPreferences().theme).toBe('dark');
  });

  it('getLastSyncError 非 React 上下文读取错误', () => {
    act(() => {
      useSettingsStore.setState({
        lastSyncError: { code: 'server', message: 'err', status: 500 },
      });
    });
    expect(getLastSyncError()?.code).toBe('server');
    act(() => useSettingsStore.getState().clearSyncError());
    expect(getLastSyncError()).toBeNull();
  });
});

describe('settingsStore 服务端同步(MES-24,auth.md §3.1)', () => {
  afterEach(() => {
    unbindSyncClient();
  });

  it('绑定 syncClient 后 setTheme 触发服务端 PATCH', async () => {
    const fetchImpl = successFetch();
    const client = createMockClient(fetchImpl);
    bindSyncClient(client);

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('dark'));

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    const [url, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(url).toContain('/api/v1/users/me');
    expect(options.method).toBe('PATCH');
    const body = JSON.parse(options.body as string);
    expect(body.settings.theme).toBe('dark');
  });

  it('绑定 syncClient 后 setLocale 触发服务端 PATCH', async () => {
    const fetchImpl = successFetch();
    const client = createMockClient(fetchImpl);
    bindSyncClient(client);

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setLocale('zh-CN'));

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    const body = JSON.parse(options.body as string);
    expect(body.settings.locale).toBe('zh-CN');
  });

  it('绑定 syncClient 后 setTimezone 触发服务端 PATCH', async () => {
    const fetchImpl = successFetch();
    const client = createMockClient(fetchImpl);
    bindSyncClient(client);

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTimezone('Asia/Shanghai'));

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    const body = JSON.parse(options.body as string);
    expect(body.timezone).toBe('Asia/Shanghai');
  });

  it('422 unsupported_locale 设置 lastSyncError', async () => {
    const fetchImpl = errorFetch(422, 'unsupported_locale', 'locale not supported');
    const client = createMockClient(fetchImpl);
    bindSyncClient(client);

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setLocale('fr-FR'));

    await waitFor(() => {
      expect(result.current.lastSyncError).not.toBeNull();
    });
    expect(result.current.lastSyncError?.code).toBe('unsupported_locale');
    expect(result.current.lastSyncError?.status).toBe(422);
    // 本地状态仍然生效(乐观更新不回滚)
    expect(result.current.preferences.locale).toBe('fr-FR');
  });

  it('422 invalid_timezone 设置 lastSyncError', async () => {
    const fetchImpl = errorFetch(422, 'invalid_timezone', 'bad tz');
    const client = createMockClient(fetchImpl);
    bindSyncClient(client);

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTimezone('Bad/Zone'));

    await waitFor(() => {
      expect(result.current.lastSyncError).not.toBeNull();
    });
    expect(result.current.lastSyncError?.code).toBe('invalid_timezone');
    // 本地状态仍然生效
    expect(result.current.preferences.timezone).toBe('Bad/Zone');
  });

  it('网络错误设置 lastSyncError code=network', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('net')) as unknown as typeof fetch;
    const client = createMockClient(fetchImpl);
    bindSyncClient(client);

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('dark'));

    await waitFor(() => {
      expect(result.current.lastSyncError).not.toBeNull();
    });
    expect(result.current.lastSyncError?.code).toBe('network');
    expect(result.current.lastSyncError?.status).toBe(0);
  });

  it('未绑定 syncClient 时 setter 仅更新本地(无网络请求)', () => {
    unbindSyncClient();
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('dark'));
    expect(result.current.preferences.theme).toBe('dark');
    expect(result.current.lastSyncError).toBeNull();
  });

  it('resetPreferences 绑定时触发全量同步', async () => {
    const fetchImpl = successFetch();
    const client = createMockClient(fetchImpl);
    bindSyncClient(client);

    const { result } = renderHook(() => useSettingsStore());
    act(() => {
      result.current.setTheme('dark');
      result.current.setLocale('zh-CN');
    });
    // 清除之前的调用记录
    (fetchImpl as ReturnType<typeof vi.fn>).mockClear();

    act(() => result.current.resetPreferences());

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    const body = JSON.parse(options.body as string);
    expect(body.timezone).toBe(detectTimezone());
    expect(body.settings.theme).toBe('system');
  });

  it('每次 setter 调用先清除 lastSyncError', async () => {
    const fetchImpl = errorFetch(422, 'unsupported_locale', 'bad');
    const client = createMockClient(fetchImpl);
    bindSyncClient(client);

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setLocale('bad-locale'));

    await waitFor(() => {
      expect(result.current.lastSyncError?.code).toBe('unsupported_locale');
    });

    // 下一次 setter 调用时先清除错误
    const successImpl = successFetch();
    unbindSyncClient();
    bindSyncClient(createMockClient(successImpl));
    act(() => result.current.setLocale('en'));
    // 同步开始时 lastSyncError 已被清除
    expect(result.current.lastSyncError).toBeNull();
  });
});
