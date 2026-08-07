/**
 * 视图切换器测试(kanban.md §4.2):视图条目选择、默认视图星标、
 * 删除二次确认(§13.3 destructive 明确确认)。
 */
import { act, fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { ViewSwitcher } from '../ViewSwitcher';
import type { View } from '../types';

function view(overrides: Partial<View> = {}): View {
  return {
    id: 'v1',
    workspace_id: 'ws-1',
    project_id: null,
    owner_member_id: 'm1',
    name: '看板一',
    layout: 'board',
    visibility: 'private',
    filters: {},
    group_by: null,
    sub_group_by: null,
    sort: [],
    display_fields: [],
    board_settings: {},
    position: 1,
    is_default: true,
    created_at: '',
    updated_at: '',
    can_write: true,
    ...overrides,
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
    const entry = screen.getByTestId('view-entry-v1');
    expect(entry).toHaveAttribute('data-slot', 'button');
    fireEvent.click(entry);
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
    // 对话框自身关闭入口同样不删除。
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByTestId('view-delete-open-v1'));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
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

  it('L543:只读视图提供「导出本视图」情境入口,隐藏写操作条目(§4.1)', () => {
    const onExportView = vi.fn();
    const target = view({ can_write: false });
    render({ views: [target], canWrite: () => false, onExportView });
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    // 读权限条目:导出;写权限条目(重命名/删除等)不渲染。
    expect(screen.getByRole('menuitem', { name: 'Export this view' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Rename' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'Duplicate' })).toBeNull();
    expect(screen.queryByTestId('view-delete-open-v1')).toBeNull();
    fireEvent.click(screen.getByTestId('view-export-v1'));
    expect(onExportView).toHaveBeenCalledWith(target);
    // 选择后菜单收起。
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('L543:可写视图的 ⋯ 菜单同时含导出与写操作条目', () => {
    const onExportView = vi.fn();
    render({ onExportView });
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    expect(screen.getByRole('menuitem', { name: 'Export this view' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toBeInTheDocument();
  });

  it('新建视图表单通过共享控件提交名称、布局与可见性', async () => {
    const { onCreate } = render();
    fireEvent.click(screen.getByTestId('view-create-open'));
    expect(screen.getByTestId('view-create-layout')).toHaveClass('mesh-field__control');
    expect(screen.getByTestId('view-create-visibility')).toHaveClass('mesh-field__control');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('view-create-name')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('view-create-open'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('view-create-name')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('view-create-open'));
    fireEvent.change(screen.getByTestId('view-create-name'), { target: { value: '列表视图' } });
    fireEvent.change(screen.getByTestId('view-create-layout'), { target: { value: 'list' } });
    fireEvent.change(screen.getByTestId('view-create-visibility'), {
      target: { value: 'shared' },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('view-create-submit'));
    });
    expect(onCreate).toHaveBeenCalledWith('列表视图', 'list', 'shared');
  });

  it('行内菜单可收起，并路由复制、设默认与重命名操作', async () => {
    const editable = view({ is_default: false });
    const { onDuplicate, onSetDefault, onRename } = render({ views: [editable] });

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Duplicate' }));
    expect(onDuplicate).toHaveBeenCalledWith(editable);

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Set as default' }));
    expect(onSetDefault).toHaveBeenCalledWith(editable);

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }));
    expect(screen.getByTestId('view-rename-name')).toHaveValue('看板一');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('view-rename-name')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('view-rename-name')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }));
    fireEvent.change(screen.getByTestId('view-rename-name'), { target: { value: '重命名看板' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('view-rename-submit'));
    });
    expect(onRename).toHaveBeenCalledWith(editable, '重命名看板');
  });

  it('行内菜单含收藏条目:未收藏显示添加文案,点击回调该视图并收起菜单(L222)', () => {
    const onToggleFavorite = vi.fn();
    render({ onToggleFavorite, favoriteViewIds: new Set<string>() });
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add to favorites' }));
    expect(onToggleFavorite).toHaveBeenCalledWith(expect.objectContaining({ id: 'v1' }));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('已收藏视图的行内菜单条目显示移除文案(L222)', () => {
    const onToggleFavorite = vi.fn();
    render({ onToggleFavorite, favoriteViewIds: new Set(['v1']) });
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    expect(screen.getByRole('menuitem', { name: 'Remove from favorites' })).toBeInTheDocument();
  });

  it('未提供收藏回调时仍渲染原有菜单(不出现收藏条目)(L222)', () => {
    render();
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    expect(screen.queryByTestId('view-favorite-toggle-v1')).toBeNull();
  });
});
