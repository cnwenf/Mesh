/**
 * SettingsPage — 主题即时切换(落 <html data-theme>)/locale 切换目录语言/时区即时更新 tz-sample/
 * 偏好同步错误横幅(MES-24 HIGH-3:422 具名 code → i18n 文案 + 可关闭)。
 */
import { act, fireEvent, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useSettingsStore } from '../../state/settingsStore';
import { renderWithProviders } from '../../test-utils/render';
import { SettingsPage } from '../pages/SettingsPage';

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
});
