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

  it('页面级错误可选择 h1 标题语义', () => {
    render(<ErrorState title="Failed to load" titleElement="h1" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Failed to load' })).toBeInTheDocument();
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

describe('ErrorState(design-quality.md §7.7 四部分扩展)', () => {
  it('impact 渲染影响说明(第 2 部分)', () => {
    render(<ErrorState title="保存失败" impact="草稿已保留,稍后可重试" />);
    const impact = screen.getByText('草稿已保留,稍后可重试');
    expect(impact).toHaveClass('mesh-error-state__impact');
  });

  it('diagnosticId 渲染为可复制等宽块(第 4 部分);空值不渲染', () => {
    const { rerender } = render(<ErrorState title="失败" diagnosticId="req_9f8a7b" />);
    const diag = screen.getByText('req_9f8a7b');
    expect(diag.tagName.toLowerCase()).toBe('code');
    expect(diag).toHaveClass('mesh-error-state__diagnostic');
    rerender(<ErrorState title="失败" diagnosticId="" />);
    expect(screen.queryByText('req_9f8a7b')).toBeNull();
  });

  it('action 插槽优先于 onRetry 按钮(自定义恢复动作)', () => {
    render(
      <ErrorState
        title="失败"
        onRetry={() => undefined}
        retryLabel="重试"
        action={<button type="button">返回上一页</button>}
      />,
    );
    expect(screen.getByRole('button', { name: '返回上一页' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull();
  });

  it('help 插槽渲染帮助链接', () => {
    render(<ErrorState title="失败" help={<a href="/docs">查看状态页</a>} />);
    expect(screen.getByRole('link', { name: '查看状态页' })).toBeInTheDocument();
  });
});
