import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StatusDot } from '../components/StatusDot';

describe('StatusDot(§6.12:颜色/脉冲不得作为唯一状态信号)', () => {
  it('始终渲染文本标签(label 必填),颜色点 aria-hidden', () => {
    const { container } = render(<StatusDot tone="success" label="Connected" />);
    expect(screen.getByText('Connected')).toBeInTheDocument();
    const dot = container.querySelector('.mesh-status__dot');
    expect(dot).not.toBeNull();
    expect(dot).toHaveAttribute('aria-hidden', 'true');
  });

  it.each(['success', 'warn', 'danger', 'info', 'neutral'] as const)(
    'tone=%s 落到点的类名(供主题 token 着色)',
    (tone) => {
      const { container } = render(<StatusDot tone={tone} label={`state-${tone}`} />);
      const dot = container.querySelector('.mesh-status__dot');
      expect(dot?.className).toContain(`mesh-status__dot--${tone}`);
    },
  );

  it('pulse 为可选叠加动画(文本信号始终存在)', () => {
    const { container } = render(<StatusDot tone="warn" label="Reconnecting" pulse />);
    expect(container.querySelector('.mesh-status__dot--pulse')).not.toBeNull();
    expect(screen.getByText('Reconnecting')).toBeInTheDocument();
  });
});
