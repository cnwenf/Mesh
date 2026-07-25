import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ErrorState } from '../components/ErrorState';

describe('ErrorState(异常态矩阵 retry 行)', () => {
  it('渲染 title 与 description', () => {
    render(<ErrorState title="Failed to load" description="The server returned an error." />);
    expect(screen.getByText('Failed to load')).toBeInTheDocument();
    expect(screen.getByText('The server returned an error.')).toBeInTheDocument();
  });

  it('onRetry + retryLabel → 重试按钮,点击回调', async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<ErrorState title="Failed" onRetry={onRetry} retryLabel="Retry" />);
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('无 onRetry 时不渲染重试按钮', () => {
    render(<ErrorState title="Failed" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('有 onRetry 但缺 retryLabel 时不渲染按钮(避免无标签控件)', () => {
    render(<ErrorState title="Failed" onRetry={() => undefined} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('提供 illustration 插槽时渲染插画区', () => {
    render(<ErrorState title="Failed" illustration={<span>art</span>} />);
    expect(screen.getByText('art')).toBeInTheDocument();
  });
});
