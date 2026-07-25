import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  SETTINGS_STORAGE_KEY,
  THEME_MIRROR_KEY,
  defaultPreferences,
  detectTimezone,
  useSettingsStore,
} from '../settingsStore';

describe('settingsStore(展示偏好,README §6.12/§6.18)', () => {
  it('默认偏好:theme=system、locale=null(跟随工作区默认)、timezone=检测值', () => {
    const { result } = renderHook(() => useSettingsStore());
    expect(result.current.preferences.theme).toBe('system');
    expect(result.current.preferences.locale).toBeNull();
    expect(result.current.preferences.timezone).toBe(detectTimezone());
  });

  it('setTheme 不可变更新并镜像 mesh.theme 键(供防闪烁脚本读取)', () => {
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

  it('setTimezone 更新 IANA 时区(仅展示层)', () => {
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

  it('resetPreferences 在镜像写失败时同样静默', () => {
    const { result } = renderHook(() => useSettingsStore());
    act(() => result.current.setTheme('dark'));
    const original = Storage.prototype.setItem;
    const setter = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(function (this: Storage, key: string, value: string) {
        if (key === THEME_MIRROR_KEY) throw new Error('quota');
        original.call(this, key, value);
      });
    expect(() => act(() => result.current.resetPreferences())).not.toThrow();
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
});
