/**
 * StatusBanner — offline/reconnecting 共用离线横幅(§6.12),resyncing 独立横幅,其余 null。
 */
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { StatusBanner } from '../StatusBanner';

describe('StatusBanner', () => {
  it('offline 呈现离线横幅(status-banner-offline,assertive)', () => {
    renderWithProviders(<StatusBanner state="offline" />);
    const banner = screen.getByTestId('status-banner-offline');
    expect(banner).toBeInTheDocument();
    expect(banner.closest('[role="alert"]')).not.toBeNull();
    expect(banner.textContent).toContain('offline');
  });

  it('reconnecting 同样呈现离线横幅(掉线转重连,§6.12 共用 testid)', () => {
    renderWithProviders(<StatusBanner state="reconnecting" />);
    expect(screen.getByTestId('status-banner-offline')).toBeInTheDocument();
  });

  it('resyncing 呈现重新同步横幅(polite)', () => {
    renderWithProviders(<StatusBanner state="resyncing" />);
    const banner = screen.getByTestId('status-banner-resyncing');
    expect(banner).toBeInTheDocument();
    expect(banner.closest('[role="status"]')).not.toBeNull();
  });

  it.each(['idle', 'connecting', 'connected'] as const)('%s 不渲染横幅', (state) => {
    renderWithProviders(<StatusBanner state={state} />);
    expect(screen.queryByTestId('status-banner-offline')).not.toBeInTheDocument();
    expect(screen.queryByTestId('status-banner-resyncing')).not.toBeInTheDocument();
  });

  it('提供 onRetry 时不抛错(接口预留,当前不渲染重试控件)', () => {
    renderWithProviders(<StatusBanner state="offline" onRetry={() => undefined} />);
    expect(screen.getByTestId('status-banner-offline')).toBeInTheDocument();
  });
});
