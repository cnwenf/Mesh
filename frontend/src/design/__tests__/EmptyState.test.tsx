import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { EmptyState } from '../components/EmptyState';

describe('EmptyState(异常态矩阵 empty 行)', () => {
  it('渲染 title 与 description(均来自 prop,无硬编码文案)', () => {
    render(<EmptyState title="No issues yet" description="Create your first issue to begin." />);
    expect(screen.getByText('No issues yet')).toBeInTheDocument();
    expect(screen.getByText('Create your first issue to begin.')).toBeInTheDocument();
  });

  it('description 可选:省略时不渲染', () => {
    render(<EmptyState title="Empty" />);
    expect(screen.getByText('Empty')).toBeInTheDocument();
  });

  it('action 插槽渲染主操作并可点击', async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();
    render(
      <EmptyState
        title="No issues"
        action={
          <button type="button" onClick={onCreate}>
            New issue
          </button>
        }
      />,
    );
    await user.click(screen.getByRole('button', { name: 'New issue' }));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it('illustration 插槽渲染', () => {
    render(<EmptyState title="Empty" illustration={<svg data-testid="illo" aria-hidden="true" />} />);
    expect(screen.getByTestId('illo')).toBeInTheDocument();
  });
});

describe('EmptyState(design-quality.md §7.7 四部分扩展)', () => {
  it('help 插槽渲染帮助链接/示例', () => {
    render(<EmptyState title="暂无视图" help={<a href="/docs/views">了解保存视图</a>} />);
    expect(screen.getByRole('link', { name: '了解保存视图' })).toBeInTheDocument();
  });

  it('四部分齐备时全部渲染(插画/缘由/主操作/帮助)', () => {
    render(
      <EmptyState
        title="暂无工作项"
        description="创建第一个工作项开始协作"
        illustration={<svg data-testid="illo2" aria-hidden="true" />}
        action={<button type="button">新建工作项</button>}
        help={<a href="/docs">帮助</a>}
      />,
    );
    expect(screen.getByTestId('illo2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建工作项' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '帮助' })).toBeInTheDocument();
  });
});
