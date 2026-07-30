import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FilterChips } from '../patterns/FilterChips';
import type { FilterChip } from '../patterns/FilterChips';

function chip(key: string, value: string | undefined, onRemove = vi.fn()): FilterChip {
  return { key, label: `字段${key}`, value, removeLabel: `移除${key}`, onRemove };
}

describe('FilterChips(过滤 chips,§3.2)', () => {
  it('空 chips 不渲染', () => {
    const { container } = render(<FilterChips chips={[]} ariaLabel="过滤" />);
    expect(container.firstChild).toBeNull();
  });

  it('渲染 label + value,并带可访问移除按钮', async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    render(<FilterChips chips={[chip('p', '高', onRemove)]} ariaLabel="生效过滤" />);
    const region = screen.getByRole('region', { name: '生效过滤' });
    expect(region).toBeInTheDocument();
    expect(screen.getByTestId('filter-chip-p')).toBeInTheDocument();
    expect(screen.getByText('字段p')).toBeInTheDocument();
    expect(screen.getByText('高')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '移除p' }));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it('无 value 时只渲染 label', () => {
    render(<FilterChips chips={[chip('x', undefined)]} ariaLabel="a" />);
    expect(screen.getByText('字段x')).toBeInTheDocument();
    expect(screen.getByTestId('filter-chip-x').querySelector('.mesh-filter-chips__value')).toBeNull();
  });

  it('仅一项时不渲染清除全部,两项时渲染并回调', async () => {
    const user = userEvent.setup();
    const onClearAll = vi.fn();
    const { rerender } = render(
      <FilterChips chips={[chip('a', '1')]} ariaLabel="a" onClearAll={onClearAll} clearAllLabel="清除全部" />,
    );
    expect(screen.queryByRole('button', { name: '清除全部' })).toBeNull();
    rerender(
      <FilterChips
        chips={[chip('a', '1'), chip('b', '2')]}
        ariaLabel="a"
        onClearAll={onClearAll}
        clearAllLabel="清除全部"
      />,
    );
    await user.click(screen.getByRole('button', { name: '清除全部' }));
    expect(onClearAll).toHaveBeenCalledTimes(1);
  });

  it('未提供 clearAllLabel 时即使两项也不渲染清除按钮', () => {
    render(<FilterChips chips={[chip('a', '1'), chip('b', '2')]} ariaLabel="a" onClearAll={vi.fn()} />);
    expect(document.querySelector('.mesh-filter-chips__clear')).toBeNull();
  });
});
