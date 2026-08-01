/**
 * 账号设置分页覆盖:
 * - SecuritySettingsSection:fetchMe 成功渲染安全区、失败渲染占位(空用户);
 * - AppearanceSettingsSection:系统主题监听(media change)更新「跟随系统(X)」解析标注。
 */
import { act, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { AppearanceSettingsSection } from '../pages/settings/AppearanceSettingsSection';
import { SecuritySettingsSection } from '../pages/settings/SecuritySettingsSection';

const fetchMeMock = vi.fn();
vi.mock('../../api/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/auth')>();
  return { ...actual, fetchMe: (...args: unknown[]) => fetchMeMock(...args) };
});
vi.mock('../../features/auth', () => ({
  SecuritySettings: () => <div data-testid="security-panel" />,
}));

describe('SecuritySettingsSection', () => {
  it('fetchMe 成功 → 渲染安全设置区', async () => {
    fetchMeMock.mockResolvedValueOnce({ id: 'u1', email: 'a@b.com' });
    renderWithProviders(<SecuritySettingsSection />);
    expect(await screen.findByTestId('security-panel')).toBeInTheDocument();
  });

  it('fetchMe 失败 → 渲染占位(无用户不渲染安全区)', async () => {
    fetchMeMock.mockRejectedValueOnce(new Error('no session'));
    renderWithProviders(<SecuritySettingsSection />);
    expect(await screen.findByTestId('security-pending')).toBeInTheDocument();
    expect(screen.queryByTestId('security-panel')).not.toBeInTheDocument();
  });
});

describe('AppearanceSettingsSection 系统主题解析标注(theme.md §4.1)', () => {
  function stubMatchMedia(): { fire: (matches: boolean) => void } {
    const listeners = new Set<(event: { matches: boolean }) => void>();
    const matchMedia = (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        addEventListener: (_event: string, cb: (e: { matches: boolean }) => void) => {
          listeners.add(cb);
        },
        removeEventListener: (_event: string, cb: (e: { matches: boolean }) => void) => {
          listeners.delete(cb);
        },
      }) as unknown as MediaQueryList;
    Object.defineProperty(window, 'matchMedia', {
      value: matchMedia,
      configurable: true,
      writable: true,
    });
    return { fire: (matches: boolean) => listeners.forEach((cb) => cb({ matches })) };
  }

  function systemOptionLabel(): string {
    const select = screen.getByTestId('theme-select') as HTMLSelectElement;
    const option = [...select.options].find((item) => item.value === 'system');
    return option?.textContent ?? '';
  }

  it('系统外观变化时「跟随系统(X)」标注随之更新', () => {
    const media = stubMatchMedia();
    renderWithProviders(<AppearanceSettingsSection />);
    expect(systemOptionLabel()).toContain('Light');
    act(() => media.fire(true));
    expect(systemOptionLabel()).toContain('Dark');
  });
});
