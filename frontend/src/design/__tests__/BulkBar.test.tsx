import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { BulkBar } from '../patterns/BulkBar';

describe('BulkBar(粘底批量条,§3.2/§8.2)', () => {
  it('选中 0 项时不渲染', () => {
    const { container } = render(
      <BulkBar
        selectedCount={0}
        countLabel="已选 0 项"
        onClearSelection={vi.fn()}
        clearLabel="取消选择"
        actions={<button type="button">改状态</button>}
        ariaLabel="批量操作"
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('选中 ≥1 项时渲染计数/动作/取消,计数带 aria-live', async () => {
    const user = userEvent.setup();
    const onClear = vi.fn();
    render(
      <BulkBar
        selectedCount={3}
        countLabel="已选 3 项"
        onClearSelection={onClear}
        clearLabel="取消选择"
        actions={<button type="button">改状态</button>}
        ariaLabel="批量操作"
      />,
    );
    const region = screen.getByRole('region', { name: '批量操作' });
    expect(region).toBeInTheDocument();
    const count = screen.getByText('已选 3 项');
    expect(count).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByRole('button', { name: '改状态' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '取消选择' }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
