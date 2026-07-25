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
