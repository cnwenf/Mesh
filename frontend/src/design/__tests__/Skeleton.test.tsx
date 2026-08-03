import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Skeleton } from '../components/Skeleton';

describe('Skeleton(异常态矩阵 loading 行)', () => {
  it('容器 role=status 并携带 sr-only 加载文案(状态不止于动画)', () => {
    render(<Skeleton loadingLabel="Loading board" />);
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('Loading board');
  });

  it('占位形状 aria-hidden,不进入可访问树', () => {
    const { container } = render(<Skeleton loadingLabel="Loading" />);
    const shape = container.querySelector('.mesh-skeleton__shape');
    expect(shape).not.toBeNull();
    expect(shape).toHaveAttribute('data-slot', 'skeleton');
    expect(shape).toHaveAttribute('aria-hidden', 'true');
  });

  it('className 控制占位形状(宽高等由调用方定义)', () => {
    const { container } = render(<Skeleton loadingLabel="Loading" className="demo-wide" />);
    const shape = container.querySelector('.mesh-skeleton__shape');
    expect(shape?.className).toContain('demo-wide');
  });
});
