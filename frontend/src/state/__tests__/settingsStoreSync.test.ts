import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../api/client';
import { THEME_LOCATOR_KEY } from '../../design/themeLocator';
import {
  LOCATOR_CHANGE_EVENT,
  SETTINGS_STORAGE_KEY,
  bindSyncClient,
  defaultPreferences,
  detectTimezone,
  getPreferences,
  getLastSyncError,
  initCrossTabSync,
  onLogoutCleanup,
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

function resetStore(): void {
  useSettingsStore.setState({ preferences: defaultPreferences(), lastSyncError: null });
}

describe('settingsStore 基础行为(theme.md §2.1 三值语义)', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });
  afterEach(() => {
    unbindSyncClient();
  });

  it('默认偏好:theme=null(未表达,继承工作区默认)、locale=null、timezone=检测值', () => {
    const { result } = renderHook(() => useSettingsStore());
    expect(result.current.preferences.theme).toBeNull();
    expect(result.current.preferences.locale).toBeNull();
    expect(result.current.preferences.timezone).toBe(detectTimezone());
  });

  it('setTheme 不可变更新(镜像由 ThemeProvider 的分区 locator 承载)', () => {
    const { result } = renderHook(() => useSettingsStore());
    const before = result.current.preferences;
    act(() => result.current.setTheme('dark'));
    expect(result.current.preferences.theme).toBe('dark');
    expect(result.current.preferences).not.toBe(before);
  });

  it('setTheme(null) = 恢复跟随工作区默认(显式清除,§3.2)', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('dark'));
    act(() => result.current.setTheme(null));
    expect(result.current.preferences.theme).toBeNull();
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

  it('resetPreferences 恢复默认(theme=null)', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => {
      result.current.setTheme('dark');
      result.current.setLocale('en-US');
      result.current.setTimezone('Asia/Shanghai');
    });
    act(() => result.current.resetPreferences());
    expect(result.current.preferences).toEqual(defaultPreferences());
    expect(result.current.preferences.theme).toBeNull();
  });

  it('偏好持久化到 mesh.settings.v1', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('light'));
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw as string) as {
      state: { preferences: { theme: string | null } };
    };
    expect(parsed.state.preferences.theme).toBe('light');
  });

  it('persist v1→v2 迁移:v1 默认 system → null,显式 light/dark 保留', async () => {
    localStorage.setItem(
      SETTINGS_STORAGE_KEY,
      JSON.stringify({
        state: { preferences: { theme: 'system', locale: 'zh-CN', timezone: 'UTC' } },
        version: 1,
      }),
    );
    await act(async () => {
      await useSettingsStore.persist.rehydrate();
    });
    expect(useSettingsStore.getState().preferences.theme).toBeNull();
    expect(useSettingsStore.getState().preferences.locale).toBe('zh-CN');

    localStorage.setItem(
      SETTINGS_STORAGE_KEY,
      JSON.stringify({
        state: { preferences: { theme: 'dark', locale: null, timezone: 'UTC' } },
        version: 1,
      }),
    );
    await act(async () => {
      await useSettingsStore.persist.rehydrate();
    });
    expect(useSettingsStore.getState().preferences.theme).toBe('dark');
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

  it('lastSyncError 初始为 null;clearSyncError 清除错误状态', () => {
    const { result } = renderHook(() => useSettingsStore());
    expect(result.current.lastSyncError).toBeNull();
    act(() => {
      useSettingsStore.setState({
        lastSyncError: { code: 'network', message: 'test', status: 0 },
      });
    });
    expect(result.current.lastSyncError).not.toBeNull();
    act(() => result.current.clearSyncError());
    expect(result.current.lastSyncError).toBeNull();
  });

  it('getPreferences/getLastSyncError 非 React 上下文读取', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('dark'));
    expect(getPreferences().theme).toBe('dark');
    act(() => {
      useSettingsStore.setState({ lastSyncError: { code: 'server', message: 'e', status: 500 } });
    });
    expect(getLastSyncError()?.code).toBe('server');
    act(() => useSettingsStore.getState().clearSyncError());
    expect(getLastSyncError()).toBeNull();
  });
});

describe('hydrateFromServer — 服务端回填裁决(theme.md §4.5)', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });

  it('服务端有值 → 覆盖本地同名镜像', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('dark')); // 本地匿名镜像
    act(() =>
      result.current.hydrateFromServer({ theme: 'light', locale: 'en', timezone: 'Asia/Shanghai' }),
    );
    expect(result.current.preferences.theme).toBe('light');
    expect(result.current.preferences.locale).toBe('en');
    expect(result.current.preferences.timezone).toBe('Asia/Shanghai');
    expect(result.current.lastSyncError).toBeNull();
  });

  it('服务端 absent/null → 偏好置 null(匿名本地值不充当账号偏好)', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('dark'));
    act(() => result.current.hydrateFromServer({ theme: null }));
    expect(result.current.preferences.theme).toBeNull();
  });

  it('服务端非法 theme 值白名单收敛为 null', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() =>
      result.current.hydrateFromServer({
        theme: 'neon' as unknown as 'light',
        locale: undefined,
      }),
    );
    expect(result.current.preferences.theme).toBeNull();
  });

  it('locale 键缺席时保留本地值,timezone 空值保留本地', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setLocale('zh-CN'));
    act(() => result.current.hydrateFromServer({ theme: 'dark', timezone: '' }));
    expect(result.current.preferences.locale).toBe('zh-CN');
    expect(result.current.preferences.timezone).toBe(detectTimezone());
  });
});

describe('跨标签页同步(theme.md §4.2 评审 T5②)', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });

  it('storage 事件即时同步其他标签页的偏好写入(不刷新)', () => {
    const teardown = initCrossTabSync();
    try {
      const newValue = JSON.stringify({
        state: { preferences: { theme: 'dark', locale: 'en', timezone: 'UTC' } },
        version: 2,
      });
      act(() => {
        window.dispatchEvent(
          new StorageEvent('storage', { key: SETTINGS_STORAGE_KEY, newValue }),
        );
      });
      expect(useSettingsStore.getState().preferences.theme).toBe('dark');
      expect(useSettingsStore.getState().preferences.locale).toBe('en');
    } finally {
      teardown();
    }
  });

  it('storage 事件中非法 theme 值白名单收敛', () => {
    const teardown = initCrossTabSync();
    try {
      const newValue = JSON.stringify({
        state: { preferences: { theme: 'evil', locale: null, timezone: 'UTC' } },
        version: 2,
      });
      act(() => {
        window.dispatchEvent(
          new StorageEvent('storage', { key: SETTINGS_STORAGE_KEY, newValue }),
        );
      });
      expect(useSettingsStore.getState().preferences.theme).toBeNull();
    } finally {
      teardown();
    }
  });

  it('locator 键变更派发 LOCATOR_CHANGE_EVENT', () => {
    const teardown = initCrossTabSync();
    const listener = vi.fn();
    window.addEventListener(LOCATOR_CHANGE_EVENT, listener);
    try {
      act(() => {
        window.dispatchEvent(
          new StorageEvent('storage', {
            key: THEME_LOCATOR_KEY,
            newValue: JSON.stringify({ id: 'x:app', mode: 'dark' }),
          }),
        );
      });
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(LOCATOR_CHANGE_EVENT, listener);
      teardown();
    }
  });

  it('非法 JSON 载荷被忽略(保持本标签现状)', () => {
    const teardown = initCrossTabSync();
    try {
      act(() => useSettingsStore.getState().setTheme('dark'));
      act(() => {
        window.dispatchEvent(
          new StorageEvent('storage', { key: SETTINGS_STORAGE_KEY, newValue: '{broken' }),
        );
      });
      expect(useSettingsStore.getState().preferences.theme).toBe('dark');
    } finally {
      teardown();
    }
  });

  it('initCrossTabSync 幂等且可拆卸', () => {
    const teardown1 = initCrossTabSync();
    const teardown2 = initCrossTabSync();
    teardown2(); // 第二个为 no-op 拆卸
    const newValue = JSON.stringify({
      state: { preferences: { theme: 'light', locale: null, timezone: 'UTC' } },
      version: 2,
    });
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: SETTINGS_STORAGE_KEY, newValue }));
    });
    expect(useSettingsStore.getState().preferences.theme).toBe('light');
    teardown1();
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: SETTINGS_STORAGE_KEY,
          newValue: JSON.stringify({
            state: { preferences: { theme: 'dark', locale: null, timezone: 'UTC' } },
            version: 2,
          }),
        }),
      );
    });
    // 拆卸后不再同步
    expect(useSettingsStore.getState().preferences.theme).toBe('light');
  });
});

describe('onLogoutCleanup — 登出清理(theme.md §2.3)', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });

  it('删除分区 locator 与遗留镜像键,偏好回到未表达', () => {
    localStorage.setItem(THEME_LOCATOR_KEY, JSON.stringify({ id: 'x:app', mode: 'dark' }));
    localStorage.setItem('mesh.theme', 'dark');
    useSettingsStore.setState((state) => ({
      preferences: { ...state.preferences, theme: 'dark', locale: 'zh-CN' },
    }));

    onLogoutCleanup();

    expect(localStorage.getItem(THEME_LOCATOR_KEY)).toBeNull();
    expect(localStorage.getItem('mesh.theme')).toBeNull();
    expect(useSettingsStore.getState().preferences.theme).toBeNull();
    expect(useSettingsStore.getState().preferences.locale).toBeNull();
  });
});

describe('settingsStore 服务端同步(auth.md §3.1,theme.md §3.2)', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });
  afterEach(() => {
    unbindSyncClient();
  });

  it('绑定 syncClient 后 setTheme 触发服务端 PATCH', async () => {
    const fetchImpl = successFetch();
    bindSyncClient(createMockClient(fetchImpl));

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

  it('setTheme(null) 向服务端发送显式 null(清除、恢复跟随默认)', async () => {
    const fetchImpl = successFetch();
    bindSyncClient(createMockClient(fetchImpl));

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme(null));

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    const body = JSON.parse(options.body as string) as { settings: { theme: null } };
    expect(body.settings.theme).toBeNull();
    expect(options.body).toContain('"theme":null');
  });

  it('绑定 syncClient 后 setLocale/setTimezone 触发服务端 PATCH', async () => {
    const fetchImpl = successFetch();
    bindSyncClient(createMockClient(fetchImpl));

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setLocale('zh-CN'));
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    let [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(options.body as string).settings.locale).toBe('zh-CN');

    act(() => result.current.setTimezone('Asia/Shanghai'));
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(options.body as string).timezone).toBe('Asia/Shanghai');
  });

  it('422 unsupported_locale / invalid_timezone / invalid_theme_mode 设置具名 lastSyncError', async () => {
    const localeErr = errorFetch(422, 'unsupported_locale', 'locale not supported');
    bindSyncClient(createMockClient(localeErr));
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setLocale('fr-FR'));
    await waitFor(() => expect(result.current.lastSyncError?.code).toBe('unsupported_locale'));
    expect(result.current.preferences.locale).toBe('fr-FR'); // 乐观不回滚

    const tzErr = errorFetch(422, 'invalid_timezone', 'bad tz');
    unbindSyncClient();
    bindSyncClient(createMockClient(tzErr));
    act(() => result.current.setTimezone('Bad/Zone'));
    await waitFor(() => expect(result.current.lastSyncError?.code).toBe('invalid_timezone'));

    const themeErr = errorFetch(422, 'invalid_theme_mode', 'unsupported theme');
    unbindSyncClient();
    bindSyncClient(createMockClient(themeErr));
    act(() => result.current.setTheme('neon' as unknown as 'light'));
    await waitFor(() => expect(result.current.lastSyncError?.code).toBe('invalid_theme_mode'));
    expect(result.current.lastSyncError?.status).toBe(422);
  });

  it('网络错误设置 lastSyncError code=network', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('net')) as unknown as typeof fetch;
    bindSyncClient(createMockClient(fetchImpl));

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

  it('resetPreferences 绑定时触发全量同步(theme=null)', async () => {
    const fetchImpl = successFetch();
    bindSyncClient(createMockClient(fetchImpl));

    const { result } = renderHook(() => useSettingsStore());
    act(() => {
      result.current.setTheme('dark');
      result.current.setLocale('zh-CN');
    });
    (fetchImpl as ReturnType<typeof vi.fn>).mockClear();

    act(() => result.current.resetPreferences());

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    const body = JSON.parse(options.body as string) as {
      timezone: string;
      settings: { theme: null };
    };
    expect(body.timezone).toBe(detectTimezone());
    expect(body.settings.theme).toBeNull();
  });

  it('每次 setter 调用先清除 lastSyncError', async () => {
    const fetchImpl = errorFetch(422, 'unsupported_locale', 'bad');
    bindSyncClient(createMockClient(fetchImpl));

    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setLocale('bad-locale'));
    await waitFor(() => {
      expect(result.current.lastSyncError?.code).toBe('unsupported_locale');
    });

    unbindSyncClient();
    bindSyncClient(createMockClient(successFetch()));
    act(() => result.current.setLocale('en'));
    expect(result.current.lastSyncError).toBeNull();
  });
});
