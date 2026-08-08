/**
 * StatusBanner — offline → 离线横幅(§6.12);reconnecting/resyncing → 「正在重新同步」
 * 横幅(§6.7:重连/重放过期显示「正在重新同步」);其余 null。
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

  it('reconnecting 呈现「正在重新同步」横幅(§6.7:重连时显示重新同步)', () => {
    renderWithProviders(<StatusBanner state="reconnecting" />);
    expect(screen.getByTestId('status-banner-resyncing')).toBeInTheDocument();
    expect(screen.queryByTestId('status-banner-offline')).not.toBeInTheDocument();
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

  it('offline 且有待回放操作时附排队计数提示(L182)', () => {
    renderWithProviders(<StatusBanner state="offline" queuedCount={3} />);
    expect(screen.getByTestId('status-banner-offline-queued')).toBeInTheDocument();
    expect(screen.getByTestId('status-banner-offline-queued').textContent).toContain('3');
  });

  it('offline 且 queuedCount 缺省/为 0 时不渲染排队提示(L182)', () => {
    const { unmount } = renderWithProviders(<StatusBanner state="offline" />);
    expect(screen.queryByTestId('status-banner-offline-queued')).not.toBeInTheDocument();
    unmount();
    renderWithProviders(<StatusBanner state="offline" queuedCount={0} />);
    expect(screen.queryByTestId('status-banner-offline-queued')).not.toBeInTheDocument();
  });
});
