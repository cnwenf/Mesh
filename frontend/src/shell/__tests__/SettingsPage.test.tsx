/**
 * SettingsPage — 主题即时切换(落 <html data-theme>)/locale 切换目录语言/时区即时更新 tz-sample。
 */
import { fireEvent, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useSettingsStore } from '../../state/settingsStore';
import { renderWithProviders } from '../../test-utils/render';
import { SettingsPage } from '../pages/SettingsPage';

describe('SettingsPage', () => {
  beforeEach(() => {
    useSettingsStore.getState().resetPreferences();
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
});
