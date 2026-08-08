/**
 * 视图模式切换器测试(看板 ↔ 泳道 ↔ 列表 三视图直切):
 * - deriveViewMode 由 layout + sub_group_by 派生模式(纯函数);
 * - 分段控件渲染三个选项、active 用 aria-pressed 表达、点击触发 onChange、
 *   点击当前模式不触发、disabled 时整体不可用。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { deriveViewMode, ViewModeSwitcher } from '../ViewModeSwitcher';

describe('deriveViewMode', () => {
  it('maps board layout without sub-group to board', () => {
    expect(deriveViewMode({ layout: 'board', sub_group_by: null })).toBe('board');
  });

  it('maps board layout with sub-group to swimlane', () => {
    expect(deriveViewMode({ layout: 'board', sub_group_by: 'priority' })).toBe('swimlane');
  });

  it('maps list layout to list regardless of sub-group', () => {
    expect(deriveViewMode({ layout: 'list', sub_group_by: null })).toBe('list');
    expect(deriveViewMode({ layout: 'list', sub_group_by: 'assignee' })).toBe('list');
  });

  it('falls back to board for reserved layouts', () => {
    expect(deriveViewMode({ layout: 'timeline', sub_group_by: null })).toBe('board');
  });
});

describe('ViewModeSwitcher', () => {
  function render(props: Partial<React.ComponentProps<typeof ViewModeSwitcher>> = {}) {
    const onChange = vi.fn();
    renderWithProviders(<ViewModeSwitcher value="board" onChange={onChange} {...props} />);
    return onChange;
  }

  it('renders three mode buttons with the active one pressed', () => {
    render({ value: 'swimlane' });
    const board = screen.getByTestId('view-mode-board');
    const swimlane = screen.getByTestId('view-mode-swimlane');
    const list = screen.getByTestId('view-mode-list');
    expect(swimlane).toHaveAttribute('aria-pressed', 'true');
    expect(board).toHaveAttribute('aria-pressed', 'false');
    expect(list).toHaveAttribute('aria-pressed', 'false');
  });

  it('calls onChange when a different mode is clicked', () => {
    const onChange = render({ value: 'board' });
    fireEvent.click(screen.getByTestId('view-mode-list'));
    expect(onChange).toHaveBeenCalledWith('list');
  });

  it('does not call onChange when the active mode is clicked', () => {
    const onChange = render({ value: 'board' });
    fireEvent.click(screen.getByTestId('view-mode-board'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('disables all buttons when disabled', () => {
    render({ value: 'board', disabled: true });
    expect(screen.getByTestId('view-mode-board')).toBeDisabled();
    expect(screen.getByTestId('view-mode-swimlane')).toBeDisabled();
    expect(screen.getByTestId('view-mode-list')).toBeDisabled();
  });
});
