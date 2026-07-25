import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defaultPreferences, useSettingsStore } from '../../state/settingsStore';
import { ThemeProvider, resolveTheme } from '../ThemeProvider';

type ChangeListener = (event: { matches: boolean }) => void;

interface MatchMediaControl {
  setMatches: (next: boolean) => void;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
}

const originalMatchMedia = window.matchMedia;

function stubMatchMedia(initialMatches: boolean): MatchMediaControl {
  const listeners = new Set<ChangeListener>();
  const state = { matches: initialMatches };
  const addEventListener = vi.fn((_event: string, listener: ChangeListener) => {
    listeners.add(listener);
  });
  const removeEventListener = vi.fn((_event: string, listener: ChangeListener) => {
    listeners.delete(listener);
  });
  window.matchMedia = vi.fn().mockImplementation(
    (query: string) =>
      ({
        get matches(): boolean {
          return state.matches;
        },
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener,
        removeEventListener,
        dispatchEvent: () => true,
      }) as unknown as MediaQueryList,
  );
  return {
    setMatches: (next: boolean) => {
      state.matches = next;
      for (const listener of [...listeners]) listener({ matches: next });
    },
    addEventListener,
    removeEventListener,
  };
}

beforeEach(() => {
  useSettingsStore.setState({ preferences: defaultPreferences() });
});

afterEach(() => {
  window.matchMedia = originalMatchMedia;
  vi.restoreAllMocks();
});

describe('resolveTheme(纯函数)', () => {
  it('light/dark 模式直接返回,不咨询系统', () => {
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('light', false)).toBe('light');
    expect(resolveTheme('dark', true)).toBe('dark');
    expect(resolveTheme('dark', false)).toBe('dark');
  });

  it('system 模式跟随系统偏好', () => {
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
  });
});

describe('ThemeProvider(README §6.12:即时切换、无刷新)', () => {
  it('light/dark 模式立即落到 <html data-theme>', () => {
    act(() => useSettingsStore.getState().setTheme('light'));
    const { unmount } = render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('light');
    unmount();

    act(() => useSettingsStore.getState().setTheme('dark'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('system 模式按 matchMedia 初值解析', () => {
    stubMatchMedia(false);
    const { unmount } = render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('light');
    unmount();

    stubMatchMedia(true);
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('system 模式实时响应 prefers-color-scheme 变化', () => {
    const control = stubMatchMedia(false);
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('light');
    act(() => control.setMatches(true));
    expect(document.documentElement.dataset.theme).toBe('dark');
    act(() => control.setMatches(false));
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('显式 light/dark 模式下系统偏好变化不影响结果', () => {
    const control = stubMatchMedia(false);
    act(() => useSettingsStore.getState().setTheme('light'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    act(() => control.setMatches(true));
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('偏好变更即时生效(无刷新)', () => {
    stubMatchMedia(false);
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('light');
    act(() => useSettingsStore.getState().setTheme('dark'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    act(() => useSettingsStore.getState().setTheme('system'));
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('卸载时注销 matchMedia 监听器', () => {
    const control = stubMatchMedia(false);
    const { unmount } = render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(control.addEventListener).toHaveBeenCalledWith('change', expect.any(Function));
    unmount();
    expect(control.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function));
  });
});
