/**
 * 共享相对时间测试——i18n.md §4.3/§4.4。
 * 可见文案随时间推进，tooltip 保留用户时区本地值与 UTC 原值。
 */
import { act, fireEvent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSettingsStore } from '../../state/settingsStore';
import { renderWithProviders } from '../../test-utils/render';
import { RelativeTime } from '../RelativeTime';

const UTC_VALUE = '2026-07-25T12:00:00Z';
const INITIAL_NOW = new Date('2026-07-25T12:03:10Z');
const originalPreferences = useSettingsStore.getState().preferences;

describe('RelativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(INITIAL_NOW);
    act(() => {
      useSettingsStore.setState({
        preferences: { theme: null, locale: 'en', timezone: 'Asia/Shanghai' },
      });
    });
  });

  afterEach(() => {
    act(() => {
      useSettingsStore.setState({ preferences: originalPreferences });
    });
    vi.useRealTimers();
  });

  it('随页面停留时间自动刷新相对时间(§4.4 checklist 234)', () => {
    renderWithProviders(<RelativeTime utcIso={UTC_VALUE} locale="en" />);

    expect(screen.getByText('3 minutes ago')).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    expect(screen.getByText('4 minutes ago')).toBeTruthy();
  });

  it('在 tooltip 显示本地时间、时区与 UTC 原值，切换时区即时重渲染(§4.3 checklist 136)', () => {
    renderWithProviders(<RelativeTime utcIso={UTC_VALUE} locale="en" />);

    const time = screen.getByText('3 minutes ago');
    expect(time.tagName).toBe('TIME');
    expect(time).toHaveAttribute('datetime', UTC_VALUE);
    expect(screen.getByRole('tooltip').textContent).toBe(
      '2026-07-25 20:00 (GMT+8) · UTC original: 2026-07-25T12:00:00Z',
    );

    act(() => {
      useSettingsStore.setState((state) => ({
        preferences: { ...state.preferences, timezone: 'America/New_York' },
      }));
    });

    expect(screen.getByRole('tooltip').textContent).toBe(
      '2026-07-25 08:00 (GMT-4) · UTC original: 2026-07-25T12:00:00Z',
    );
  });

  it('为键盘和触屏提供可聚焦的绝对时间披露入口', () => {
    renderWithProviders(<RelativeTime utcIso={UTC_VALUE} locale="en" />);

    const trigger = screen.getByRole('button', { name: '3 minutes ago' });
    const tooltip = screen.getByRole('tooltip');
    expect(trigger).toHaveAttribute('aria-describedby', tooltip.id);

    act(() => trigger.focus());
    expect(trigger).toHaveFocus();
    fireEvent.blur(trigger);
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(trigger.closest('.mesh-tooltip-anchor')).toHaveClass('mesh-relative-time--revealed');
    fireEvent.keyDown(trigger, { key: 'Enter' });
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(trigger);
    fireEvent.keyDown(trigger, { key: 'Escape' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });
});
