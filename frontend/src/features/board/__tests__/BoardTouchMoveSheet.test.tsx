/**
 * 触摸移动底部 sheet 测试(design-quality §8.3):长按 350ms(coarse pointer)打开
 * Drawer 列目标列表;block 满载列禁用并附原因;选择列 → onDropCard + 关闭;
 * 列内排序(顶/底/上移/下移)。
 *
 * 长按经 fake timers 推进;指针事件见 dragTestUtils。
 */
import { act, fireEvent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { BoardColumns } from '../BoardColumns';
import { BoardTouchMoveSheet } from '../BoardTouchMoveSheet';
import type { BoardCard } from '../projection';
import type { BoardColumn } from '../types';
import { ensurePointerEvent } from './dragTestUtils';

function card(id: string, position: number): BoardCard {
  return {
    id, identifier: `WEB-${id}`, title: `Card ${id}`, state_category: 'todo',
    status: { id: 'st', name: 'Todo', category: 'todo' }, status_id: 'st', priority: 'high',
    assignee: null, assignee_id: null, project_id: null, position, version: 1, updated_at: '',
  };
}

function column(key: string, overrides: Partial<BoardColumn> = {}): BoardColumn {
  return {
    key, label: `board.category.${key}`, collapsed: false, wip: null, count: 0,
    placeholder: false, ...overrides,
  };
}

function renderTouch(overrides: { columns?: BoardColumn[]; cardsByKey?: Record<string, BoardCard[]> } = {}) {
  const onDropCard = vi.fn();
  renderWithProviders(
    <BoardColumns
      columns={overrides.columns ?? [
        column('todo'),
        column('in_progress', { wip: { limit: 1, enforcement: 'block' }, count: 1 }),
        column('done'),
      ]}
      groupBy="state_category"
      cardsByKey={overrides.cardsByKey ?? { todo: [card('a', 1), card('b', 2)], in_progress: [card('x', 5)] }}
      canWrite
      dragEnabled
      onToggleCollapse={vi.fn()}
      onDropCard={onDropCard}
      onQuickCreate={vi.fn()}
    />,
  );
  return { onDropCard };
}

describe('BoardTouchMoveSheet 触摸移动(§8.3)', () => {
  beforeEach(() => {
    ensurePointerEvent();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('长按 350ms 打开列目标 sheet;长按后移动不再触发拖拽', () => {
    renderTouch();
    const cardA = screen.getByTestId('board-card-a');
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'touch' });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(screen.getByTestId('board-touch-sheet')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    // 长按已消费 pending:后续 pointermove 不再进入指针拖拽(无浮层)。
    fireEvent.pointerMove(document, { clientX: 300, clientY: 300 });
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
  });

  it('长按第二分组的卡片亦可打开 sheet(跨组查找)', () => {
    renderTouch();
    fireEvent.pointerDown(screen.getByTestId('board-card-x'), { clientX: 10, clientY: 10, button: 0, pointerType: 'touch' });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(screen.getByTestId('board-touch-sheet')).toBeInTheDocument();
  });

  it('短按(<350ms)不打开 sheet', () => {
    renderTouch();
    const cardA = screen.getByTestId('board-card-a');
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'touch' });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    fireEvent.pointerUp(cardA, { clientX: 10, clientY: 10 });
    expect(screen.queryByTestId('board-touch-sheet')).not.toBeInTheDocument();
  });

  it('block 满载列禁用并附原因', () => {
    renderTouch();
    fireEvent.pointerDown(screen.getByTestId('board-card-a'), { clientX: 10, clientY: 10, button: 0, pointerType: 'touch' });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    const blocked = screen.getByTestId('touch-column-in_progress');
    expect(blocked).toBeDisabled();
    expect(screen.getByTestId('touch-blocked-reason-in_progress').textContent).toContain(
      'This column has reached its WIP limit; drop is blocked',
    );
  });

  it('选择列 → onDropCard(末尾位置) + 关闭 sheet', () => {
    const { onDropCard } = renderTouch();
    fireEvent.pointerDown(screen.getByTestId('board-card-a'), { clientX: 10, clientY: 10, button: 0, pointerType: 'touch' });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    fireEvent.click(screen.getByTestId('touch-column-done'));
    // done 空列 → computeDropPosition([], null) = 1。
    expect(onDropCard).toHaveBeenCalledWith('a', 'done', 1);
    expect(screen.queryByTestId('board-touch-sheet')).not.toBeInTheDocument();
  });

  it('列内排序:顶/上移/下移/底 计算正确位置', () => {
    const { onDropCard } = renderTouch();
    fireEvent.pointerDown(screen.getByTestId('board-card-a'), { clientX: 10, clientY: 10, button: 0, pointerType: 'touch' });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    // a 在 todo(index 0, pos 1;b pos 2)。置顶 → index 0 → pos = 1-1 = 0。
    fireEvent.click(screen.getByTestId('touch-move-top'));
    expect(onDropCard).toHaveBeenLastCalledWith('a', 'todo', 0);
    // 置底 → index null → pos = 2+1 = 3。
    fireEvent.click(screen.getByTestId('touch-move-bottom'));
    expect(onDropCard).toHaveBeenLastCalledWith('a', 'todo', 3);
    // 上移 → index max(0,-1)=0 → pos 0(当前 index 0)。
    fireEvent.click(screen.getByTestId('touch-move-up'));
    expect(onDropCard).toHaveBeenLastCalledWith('a', 'todo', expect.any(Number));
    // 下移 → index min(1, 1)=1 → 中点 (1+2)/2 = 1.5。
    fireEvent.click(screen.getByTestId('touch-move-down'));
    expect(onDropCard).toHaveBeenLastCalledWith('a', 'todo', 1.5);
  });
});

describe('BoardTouchMoveSheet 边界(直接渲染)', () => {
  it('卡片不在任何列时列内排序为无操作(列键缺失走空数组回退)', () => {
    const onDropCard = vi.fn();
    renderWithProviders(
      <BoardTouchMoveSheet
        card={card('z', 1)}
        columns={[column('todo'), column('done')]}
        cardsByKey={{ todo: [card('a', 1)] }}
        computePosition={() => 5}
        onDropCard={onDropCard}
        onClose={vi.fn()}
        announce={vi.fn()}
        getColumnLabel={(key) => key}
      />,
    );
    fireEvent.click(screen.getByTestId('touch-move-top'));
    expect(onDropCard).not.toHaveBeenCalled();
  });

  it('warn 列与 block 未满列均非阻止(可选中);选列经 computePosition 计算位置', () => {
    const onDropCard = vi.fn();
    renderWithProviders(
      <BoardTouchMoveSheet
        card={card('a', 1)}
        columns={[
          column('warned', { wip: { limit: 5, enforcement: 'warn' }, count: 6 }),
          column('blockish', { wip: { limit: 5, enforcement: 'block' }, count: 2 }),
          column('done'),
        ]}
        cardsByKey={{}}
        computePosition={() => 7}
        onDropCard={onDropCard}
        onClose={vi.fn()}
        announce={vi.fn()}
        getColumnLabel={(key) => key}
      />,
    );
    // warn 与 block 未满均不禁用(仅 block 满载禁用)。
    expect(screen.getByTestId('touch-column-warned')).not.toBeDisabled();
    expect(screen.getByTestId('touch-column-blockish')).not.toBeDisabled();
    fireEvent.click(screen.getByTestId('touch-column-done'));
    expect(onDropCard).toHaveBeenCalledWith('a', 'done', 7);
  });
});
