import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Banner } from '../components/Banner';

describe('Banner(aria-live 横幅)', () => {
  it('默认 politeness=polite → role=status + aria-live=polite', () => {
    render(<Banner tone="info">Synced</Banner>);
    const banner = screen.getByRole('status');
    expect(banner).toHaveAttribute('aria-live', 'polite');
    expect(banner).toHaveTextContent('Synced');
    expect(banner.className).toContain('mesh-banner--info');
  });

  it('politeness=assertive → role=alert + aria-live=assertive', () => {
    render(
      <Banner tone="danger" politeness="assertive">
        Connection lost
      </Banner>,
    );
    const banner = screen.getByRole('alert');
    expect(banner).toHaveAttribute('aria-live', 'assertive');
    expect(banner.className).toContain('mesh-banner--danger');
  });

  it.each(['info', 'warn', 'danger', 'success'] as const)('tone=%s 落到类名', (tone) => {
    render(<Banner tone={tone}>t</Banner>);
    expect(screen.getByRole('status').className).toContain(`mesh-banner--${tone}`);
  });

  it('onDismiss + dismissLabel → 关闭按钮(label 来自 prop)', async () => {
    const onDismiss = vi.fn();
    const user = userEvent.setup();
    render(
      <Banner tone="warn" onDismiss={onDismiss} dismissLabel="Dismiss notification">
        Heads up
      </Banner>,
    );
    await user.click(screen.getByRole('button', { name: 'Dismiss notification' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('无 onDismiss 时不渲染关闭按钮', () => {
    render(<Banner tone="info">info</Banner>);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('有 onDismiss 但缺 dismissLabel 时不渲染按钮(避免无标签控件)', () => {
    render(
      <Banner tone="info" onDismiss={() => undefined}>
        info
      </Banner>,
    );
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
