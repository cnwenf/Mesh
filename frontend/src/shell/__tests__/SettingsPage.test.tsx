/**
 * SettingsPage — 主题即时切换(落 <html data-theme>)/locale 切换目录语言/时区即时更新 tz-sample/
 * 偏好同步错误横幅(MES-24 HIGH-3:422 具名 code → i18n 文案 + 可关闭)。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSettingsStore } from '../../state/settingsStore';
import { renderWithProviders } from '../../test-utils/render';
import { SettingsPage } from '../pages/SettingsPage';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ME_BODY = {
  data: {
    id: 'u-1',
    email: 'jane@corp.com',
    email_verified: true,
    display_name: 'Jane',
    avatar_url: null,
    status: 'active',
    timezone: null,
    settings: { locale: null, theme: 'light' },
    mfa_enabled: false,
    last_login_at: null,
    created_at: '2026-07-01T00:00:00.000Z',
  },
};

describe('SettingsPage', () => {
  beforeEach(() => {
    useSettingsStore.getState().resetPreferences();
    useSettingsStore.getState().clearSyncError();
  });

  it('切换 theme-select 即时落到 document.documentElement.dataset.theme', () => {
    renderWithProviders(<SettingsPage />);
    fireEvent.change(screen.getByTestId('theme-select'), { target: { value: 'dark' } });
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(useSettingsStore.getState().preferences.theme).toBe('dark');
    fireEvent.change(screen.getByTestId('theme-select'), { target: { value: 'light' } });
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('切换 locale-select 使已渲染目录语言变化(en ↔ zh-CN)', () => {
    renderWithProviders(<SettingsPage />);
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading.textContent).toBe('Settings');
    fireEvent.change(screen.getByTestId('locale-select'), { target: { value: 'zh-CN' } });
    expect(heading.textContent).toBe('设置');
    expect(useSettingsStore.getState().preferences.locale).toBe('zh-CN');
    fireEvent.change(screen.getByTestId('locale-select'), { target: { value: '' } });
    expect(heading.textContent).toBe('Settings');
    expect(useSettingsStore.getState().preferences.locale).toBeNull();
  });

  it('切换 timezone-select 即时更新 tz-sample', () => {
    useSettingsStore.getState().setTimezone('UTC');
    renderWithProviders(<SettingsPage />);
    const sample = screen.getByTestId('tz-sample');
    expect(sample.textContent).toContain('2026-07-25 18:00');
    expect(sample.textContent).toContain('UTC');
    fireEvent.change(screen.getByTestId('timezone-select'), { target: { value: 'Asia/Shanghai' } });
    expect(sample.textContent).toContain('2026-07-26 02:00');
    expect(useSettingsStore.getState().preferences.timezone).toBe('Asia/Shanghai');
  });

  it('timezone 选项包含基础候选与当前检测时区(去重)', () => {
    useSettingsStore.getState().setTimezone('Australia/Sydney');
    renderWithProviders(<SettingsPage />);
    const options = [...(screen.getByTestId('timezone-select') as HTMLSelectElement).options].map(
      (option) => option.value,
    );
    expect(options).toContain('UTC');
    expect(options).toContain('Australia/Sydney');
    expect(options.filter((value) => value === 'UTC')).toHaveLength(1);
  });

  describe('偏好同步错误横幅(MES-24 HIGH-3)', () => {
    it('lastSyncError unsupported_locale 渲染 role=alert 横幅与 i18n 文案', () => {
      act(() => {
        useSettingsStore.setState({
          lastSyncError: { code: 'unsupported_locale', message: 'bad', status: 422 },
        });
      });
      renderWithProviders(<SettingsPage />);
      const alert = screen.getByRole('alert');
      expect(alert).toBeInTheDocument();
      expect(alert.textContent).toContain('That language is not supported');
    });

    it('lastSyncError invalid_timezone 渲染对应 i18n 文案', () => {
      act(() => {
        useSettingsStore.setState({
          lastSyncError: { code: 'invalid_timezone', message: 'bad tz', status: 422 },
        });
      });
      renderWithProviders(<SettingsPage />);
      const alert = screen.getByRole('alert');
      expect(alert.textContent).toContain('That timezone is not valid');
    });

    it('lastSyncError server 渲染通用服务端错误文案', () => {
      act(() => {
        useSettingsStore.setState({
          lastSyncError: { code: 'server', message: 'oops', status: 500 },
        });
      });
      renderWithProviders(<SettingsPage />);
      const alert = screen.getByRole('alert');
      expect(alert.textContent).toContain('Could not save your preference');
    });

    it('lastSyncError network 渲染网络错误文案', () => {
      act(() => {
        useSettingsStore.setState({
          lastSyncError: { code: 'network', message: 'net', status: 0 },
        });
      });
      renderWithProviders(<SettingsPage />);
      const alert = screen.getByRole('alert');
      expect(alert.textContent).toContain('Network error');
    });

    it('点击关闭按钮后横幅消失(clearSyncError)', () => {
      act(() => {
        useSettingsStore.setState({
          lastSyncError: { code: 'unsupported_locale', message: 'bad', status: 422 },
        });
      });
      renderWithProviders(<SettingsPage />);
      expect(screen.getByRole('alert')).toBeInTheDocument();
      const dismissButton = screen.getByRole('button', { name: 'Dismiss' });
      fireEvent.click(dismissButton);
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(useSettingsStore.getState().lastSyncError).toBeNull();
    });

    it('无 lastSyncError 时不渲染横幅', () => {
      renderWithProviders(<SettingsPage />);
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
  });

  describe('安全设置区(auth.md §4.2,仅登录态)', () => {
    it('fetchMe 成功返回当前用户 → 渲染安全设置区', async () => {
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/api/v1/sessions')) {
          return jsonResponse({ data: [], next_cursor: null });
        }
        if (url.includes('/api/v1/me')) return jsonResponse(ME_BODY);
        return jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404);
      });
      vi.stubGlobal('fetch', fetchMock);

      renderWithProviders(<SettingsPage />);

      await waitFor(() =>
        expect(screen.getByRole('heading', { name: 'Security' })).toBeInTheDocument(),
      );
    });

    it('fetchMe 失败 → 不渲染安全设置区(优雅降级)', async () => {
      const fetchMock = vi.fn().mockRejectedValue(new TypeError('connection refused'));
      vi.stubGlobal('fetch', fetchMock);

      renderWithProviders(<SettingsPage />);

      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      expect(screen.queryByRole('heading', { name: 'Security' })).not.toBeInTheDocument();
      // 页面其余分区不受影响
      expect(screen.getByRole('heading', { name: 'Appearance' })).toBeInTheDocument();
    });

    it('fetchMe 在卸载后才落定 → 不向已拆除的渲染树派发更新', async () => {
      let settleMe!: (value: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        settleMe = resolve;
      });
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/api/v1/me')) return pending;
        return jsonResponse({ data: [], next_cursor: null });
      });
      vi.stubGlobal('fetch', fetchMock);

      const result = renderWithProviders(<SettingsPage />);
      expect(fetchMock).toHaveBeenCalled();
      result.unmount();

      await act(async () => {
        settleMe(jsonResponse(ME_BODY));
        await Promise.resolve();
      });
      expect(document.body.textContent).not.toContain('Security');
    });
  });

  describe('系统外观感知(§6.12 system 档)', () => {
    it('系统深浅色偏好变化 → 「跟随系统」占位解析值即时更新', () => {
      const listeners = new Set<(event: MediaQueryListEvent) => void>();
      const mediaQueryList = {
        matches: false,
        media: '(prefers-color-scheme: dark)',
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn((_event: string, callback: (event: MediaQueryListEvent) => void) => {
          listeners.add(callback);
        }),
        removeEventListener: vi.fn(
          (_event: string, callback: (event: MediaQueryListEvent) => void) => {
            listeners.delete(callback);
          },
        ),
        dispatchEvent: vi.fn(),
      };
      vi.stubGlobal('matchMedia', vi.fn(() => mediaQueryList));

      renderWithProviders(<SettingsPage />);
      const select = screen.getByTestId('theme-select');
      expect(select.textContent).toContain('System (Light)');

      act(() => {
        for (const callback of listeners) {
          callback({ matches: true } as unknown as MediaQueryListEvent);
        }
      });

      expect(select.textContent).toContain('System (Dark)');
    });
  });

  it('主题选择「跟随默认」→ 写入 null,解析值回落协商链(测试环境为 light)', () => {
    useSettingsStore.getState().setTheme('dark');
    renderWithProviders(<SettingsPage />);
    expect(document.documentElement.dataset.theme).toBe('dark');

    fireEvent.change(screen.getByTestId('theme-select'), { target: { value: '' } });

    expect(useSettingsStore.getState().preferences.theme).toBeNull();
    expect((screen.getByTestId('theme-select') as HTMLSelectElement).value).toBe('');
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});
