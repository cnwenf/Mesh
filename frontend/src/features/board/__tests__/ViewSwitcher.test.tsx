/**
 * 视图切换器测试(kanban.md §4.2):视图条目选择、默认视图星标、
 * 删除二次确认(§13.3 destructive 明确确认)。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { ViewSwitcher } from '../ViewSwitcher';
import type { View } from '../types';

function view(overrides: Partial<View> = {}): View {
  return {
    id: 'v1', workspace_id: 'ws-1', project_id: null, owner_member_id: 'm1', name: '看板一',
    layout: 'board', visibility: 'private', filters: {}, group_by: null, sub_group_by: null,
    sort: [], display_fields: [], board_settings: {}, position: 1, is_default: true,
    created_at: '', updated_at: '', can_write: true, ...overrides,
  };
}

function render(overrides: Partial<React.ComponentProps<typeof ViewSwitcher>> = {}) {
  const props = {
    views: [view()],
    selectedId: 'v1',
    canWrite: () => true,
    onSelect: vi.fn(),
    onCreate: vi.fn().mockResolvedValue(undefined),
    onRename: vi.fn().mockResolvedValue(undefined),
    onDuplicate: vi.fn().mockResolvedValue(undefined),
    onSetDefault: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  renderWithProviders(<ViewSwitcher {...props} />);
  return props;
}

describe('ViewSwitcher', () => {
  it('视图条目可选;默认视图渲染星标(label 经 board.defaultView)', () => {
    const { onSelect } = render();
    fireEvent.click(screen.getByTestId('view-entry-v1'));
    expect(onSelect).toHaveBeenCalledWith('v1');
    expect(screen.getByRole('img', { name: 'Default view' })).toBeInTheDocument();
  });

  it('删除视图先弹确认,确认后才调用 onDelete;取消不调用(§13.3)', () => {
    const { onDelete } = render();
    // 行内菜单 → 删除入口 → 确认对话框。
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByTestId('view-delete-open-v1'));
    expect(screen.getByTestId('view-delete-confirm-body').textContent).toContain('看板一');
    // 取消路径:不调用删除。
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onDelete).not.toHaveBeenCalled();
    // 再次打开并确认:调用删除(携带该视图)。
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByTestId('view-delete-open-v1'));
    fireEvent.click(screen.getByTestId('view-delete-confirm'));
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: 'v1' }));
  });

  it('无写权限时不渲染行内菜单入口', () => {
    render({ canWrite: () => false });
    expect(screen.queryByTestId('view-menu-v1')).toBeNull();
  });
});
