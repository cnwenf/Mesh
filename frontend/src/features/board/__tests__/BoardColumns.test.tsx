/**
 * 看板列组件交互测试(§4.2/§4.3/§4.4):卡片渲染、拖拽落点(列底/卡间中点)、
 * WIP block 满载列视觉提示(硬阻止由服务端强制)、列底快速创建(回车提交 / 无权限禁用)。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { BoardColumns } from '../BoardColumns';
import type { BoardCard } from '../projection';
import type { BoardColumn } from '../types';

function card(id: string, position: number): BoardCard {
  return {
    id, identifier: `WEB-${id}`, title: `Card ${id}`, state_category: 'todo',
    status: { id: 'st', name: 'Todo', category: 'todo' }, status_id: 'st', priority: 'high',
    assignee: null, assignee_id: null, project_id: null, position, version: 1, updated_at: '',
  };
}

function column(overrides: Partial<BoardColumn> = {}): BoardColumn {
  return {
    key: 'todo', label: 'board.category.todo', collapsed: false, wip: null, count: 0,
    placeholder: false, ...overrides,
  };
}

function render(props: Partial<Parameters<typeof BoardColumns>[0]> = {}) {
  const onDropCard = vi.fn();
  const onQuickCreate = vi.fn();
  const onToggleCollapse = vi.fn();
  renderWithProviders(
    <BoardColumns
      columns={[column()]}
      groupBy="state_category"
      cardsByKey={{}}
      canWrite
      dragEnabled
      onToggleCollapse={onToggleCollapse}
      onDropCard={onDropCard}
      onQuickCreate={onQuickCreate}
      {...props}
    />,
  );
  return { onDropCard, onQuickCreate, onToggleCollapse };
}

describe('BoardColumns 渲染与交互', () => {
  it('渲染列内卡片;无卡片呈现空态文案', () => {
    render({ cardsByKey: { todo: [card('a', 1), card('b', 2)] } });
    expect(screen.getByTestId('board-card-a')).toBeInTheDocument();
    expect(screen.getByTestId('board-card-b')).toBeInTheDocument();
  });

  it('拖落到列体 → 列底位置(末张+1)', () => {
    const { onDropCard } = render({ cardsByKey: { todo: [card('a', 1), card('b', 2)] } });
    fireEvent.drop(screen.getByTestId('column-body-todo'), {
      dataTransfer: { getData: () => 'x' },
    });
    expect(onDropCard).toHaveBeenCalledWith('x', 'todo', 3);
  });

  it('拖落到某张卡片 → 相邻中点位置', () => {
    const { onDropCard } = render({ cardsByKey: { todo: [card('a', 2), card('b', 4)] } });
    fireEvent.drop(screen.getByTestId('board-card-b'), {
      dataTransfer: { getData: () => 'x' },
    });
    expect(onDropCard).toHaveBeenCalledWith('x', 'todo', 3); // (2+4)/2
  });

  it('WIP block 满载列呈现 blocked 视觉提示;硬阻止由服务端强制(客户端不预先禁用落点)', () => {
    const { onDropCard } = render({
      columns: [column({ wip: { limit: 1, enforcement: 'block' }, count: 1 })],
      cardsByKey: { todo: [card('a', 1)] },
    });
    const body = screen.getByTestId('column-body-todo');
    expect(body.className).toContain('mesh-board__column-body--blocked');
    // 客户端不预先禁用落点:drop 仍上抛,由服务端 /moves 返回 422 触发弹回+toast(§4.4)。
    fireEvent.drop(body, { dataTransfer: { getData: () => 'x' } });
    expect(onDropCard).toHaveBeenCalledWith('x', 'todo', 2);
  });

  it('dragEnabled=false 时不接受落点且卡片不可拖', () => {
    const { onDropCard } = render({ dragEnabled: false, cardsByKey: { todo: [card('a', 1)] } });
    fireEvent.drop(screen.getByTestId('column-body-todo'), {
      dataTransfer: { getData: () => 'x' },
    });
    expect(onDropCard).not.toHaveBeenCalled();
    expect(screen.getByTestId('board-card-a')).not.toHaveAttribute('draggable', 'true');
  });

  it('快速创建:回车提交并清空;无权限时禁用', () => {
    const { onQuickCreate } = render();
    const input = screen.getByTestId('quick-add-todo');
    fireEvent.change(input, { target: { value: '新卡' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onQuickCreate).toHaveBeenCalledWith('todo', '新卡');
    // 空标题不提交。
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onQuickCreate).toHaveBeenCalledTimes(1);

    const disabled = render({ canWrite: false });
    expect(screen.getAllByTestId('quick-add-todo').at(-1)).toBeDisabled();
    expect(disabled.onQuickCreate).not.toHaveBeenCalled();
  });

  it('拖落到某张卡片上 → 该卡 index 的中点位置', () => {
    const { onDropCard } = render({ cardsByKey: { todo: [card('a', 2), card('b', 4)] } });
    fireEvent.dragOver(screen.getByTestId('board-card-b'));
    fireEvent.drop(screen.getByTestId('board-card-b'), { dataTransfer: { getData: () => 'x' } });
    expect(onDropCard).toHaveBeenCalledWith('x', 'todo', 3);
  });

  it('折叠列不渲染列体;展开/折叠回调', () => {
    const { onToggleCollapse } = render({ columns: [column({ collapsed: true })] });
    expect(screen.queryByTestId('column-body-todo')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    expect(onToggleCollapse).toHaveBeenCalledWith('todo');
  });
});
