/**
 * 账号设置(SettingsLayout 二级导航 + 子路由分页):
 * - 索引重定向 /settings → /settings/appearance;
 * - 外观:主题即时切换(落 <html data-theme>)/locale 切换目录语言/时区即时更新 tz-sample;
 * - 二级导航子路由切换(appearance ↔ notifications);
 * - 偏好同步错误横幅(MES-24:422 具名 code → i18n 文案 + 可关闭)。
 */
import { act, fireEvent, screen } from '@testing-library/react';
import { Navigate, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { NotificationPreferencesSection } from '../../features/inbox';
import { useSettingsStore } from '../../state/settingsStore';
import { renderWithProviders } from '../../test-utils/render';
import { SettingsPage } from '../pages/SettingsPage';
import { AppearanceSettingsSection } from '../pages/settings/AppearanceSettingsSection';
import { SecuritySettingsSection } from '../pages/settings/SecuritySettingsSection';

function SettingsRoutes(): React.JSX.Element {
  return (
    <Routes>
      <Route path="/settings" element={<SettingsPage />}>
        <Route index element={<Navigate to="appearance" replace />} />
        <Route path="appearance" element={<AppearanceSettingsSection />} />
        <Route path="notifications" element={<NotificationPreferencesSection />} />
        <Route path="security" element={<SecuritySettingsSection />} />
      </Route>
    </Routes>
  );
}

function renderAt(route: string): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(<SettingsRoutes />, { route });
}

describe('账号设置(SettingsLayout + 子路由)', () => {
  beforeEach(() => {
    useSettingsStore.getState().resetPreferences();
    useSettingsStore.getState().clearSyncError();
  });

  it('索引路由重定向到 appearance(主题控件可见)', () => {
    renderAt('/settings');
    expect(screen.getByTestId('theme-select')).toBeInTheDocument();
    expect(screen.getByTestId('settings-nav-appearance')).toBeInTheDocument();
  });

  it('二级导航呈现三个分页项,当前项高亮', () => {
    renderAt('/settings/notifications');
    expect(screen.getByTestId('settings-nav-appearance')).toBeInTheDocument();
    expect(screen.getByTestId('settings-nav-notifications').className).toContain('is-active');
    expect(screen.getByTestId('settings-nav-security')).toBeInTheDocument();
  });

  it('切换到 notifications 子路由替换外观内容(路由化分页)', () => {
    renderAt('/settings/notifications');
    // 外观控件不再渲染,证明分页经子路由切换(通知区数据加载属 inbox 模块,此处不断言)。
    expect(screen.queryByTestId('theme-select')).not.toBeInTheDocument();
    expect(screen.getByTestId('settings-nav-notifications').className).toContain('is-active');
  });

  it('切换 theme-select 即时落到 document.documentElement.dataset.theme', () => {
    renderAt('/settings/appearance');
    fireEvent.change(screen.getByTestId('theme-select'), { target: { value: 'dark' } });
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(useSettingsStore.getState().preferences.theme).toBe('dark');
    fireEvent.change(screen.getByTestId('theme-select'), { target: { value: 'light' } });
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('切换 locale-select 使已渲染目录语言变化(en ↔ zh-CN)', () => {
    renderAt('/settings/appearance');
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
    renderAt('/settings/appearance');
    const sample = screen.getByTestId('tz-sample');
    expect(sample.textContent).toContain('2026-07-25 18:00');
    expect(sample.textContent).toContain('UTC');
    fireEvent.change(screen.getByTestId('timezone-select'), { target: { value: 'Asia/Shanghai' } });
    expect(sample.textContent).toContain('2026-07-26 02:00');
    expect(useSettingsStore.getState().preferences.timezone).toBe('Asia/Shanghai');
  });

  it('timezone 选项包含基础候选与当前检测时区(去重)', () => {
    useSettingsStore.getState().setTimezone('Australia/Sydney');
    renderAt('/settings/appearance');
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
      renderAt('/settings/appearance');
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
      renderAt('/settings/appearance');
      expect(screen.getByRole('alert').textContent).toContain('That timezone is not valid');
    });

    it('lastSyncError server 渲染通用服务端错误文案', () => {
      act(() => {
        useSettingsStore.setState({
          lastSyncError: { code: 'server', message: 'oops', status: 500 },
        });
      });
      renderAt('/settings/appearance');
      expect(screen.getByRole('alert').textContent).toContain('Could not save your preference');
    });

    it('lastSyncError network 渲染网络错误文案', () => {
      act(() => {
        useSettingsStore.setState({
          lastSyncError: { code: 'network', message: 'net', status: 0 },
        });
      });
      renderAt('/settings/appearance');
      expect(screen.getByRole('alert').textContent).toContain('Network error');
    });

    it('点击关闭按钮后横幅消失(clearSyncError)', () => {
      act(() => {
        useSettingsStore.setState({
          lastSyncError: { code: 'unsupported_locale', message: 'bad', status: 422 },
        });
      });
      renderAt('/settings/appearance');
      expect(screen.getByRole('alert')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(useSettingsStore.getState().lastSyncError).toBeNull();
    });

    it('无 lastSyncError 时不渲染横幅', () => {
      renderAt('/settings/appearance');
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
  });
});
