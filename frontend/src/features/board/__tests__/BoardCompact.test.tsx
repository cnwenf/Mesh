/**
 * 移动紧凑看板测试(design-quality §8.3):容器 ≤599px 单泳道 + 顶部 chips 切列、
 * 上一个/下一个按钮、横向滑动切列(阈值 50px)、当前列快速创建。
 *
 * 紧凑判定为结构性切换(useContainerWidth,ResizeObserver)。测试以可编程
 * ResizeObserver 桩捕获回调,defineProperty clientWidth 后触发回调驱动宽度。
 */
import { act, fireEvent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { BoardCompact } from '../BoardCompact';
import { BoardColumns } from '../BoardColumns';
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

function column(key: string): BoardColumn {
  return { key, label: `board.category.${key}`, collapsed: false, wip: null, count: 1, placeholder: false };
}

let roCallback: (() => void) | null = null;

/** 可编程 ResizeObserver:捕获回调以驱动容器宽度(结构切换,非纯视觉)。 */
class ProgrammableRO {
  constructor(cb: () => void) {
    roCallback = cb;
  }
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

// 全局 setup 以 writable(非 configurable)定义 ResizeObserver,vi.stubGlobal 无法重定义;
// 直接赋值并在 afterEach 复原。
const originalResizeObserver = window.ResizeObserver;

function renderCompact() {
  const onDropCard = vi.fn();
  const onQuickCreate = vi.fn();
  renderWithProviders(
    <BoardColumns
      columns={[column('todo'), column('in_progress'), column('done')]}
      groupBy="state_category"
      cardsByKey={{ todo: [card('a', 1)], in_progress: [card('b', 1)], done: [card('c', 1)] }}
      canWrite
      dragEnabled
      onToggleCollapse={vi.fn()}
      onDropCard={onDropCard}
      onQuickCreate={onQuickCreate}
    />,
  );
  const wrap = screen.getByTestId('board-columns-wrap');
  Object.defineProperty(wrap, 'clientWidth', { configurable: true, value: 400 });
  act(() => {
    roCallback?.();
  });
  return { onDropCard, onQuickCreate };
}

describe('BoardCompact 紧凑看板(§8.3)', () => {
  beforeEach(() => {
    ensurePointerEvent();
    roCallback = null;
    window.ResizeObserver = ProgrammableRO as unknown as typeof ResizeObserver;
  });
  afterEach(() => {
    window.ResizeObserver = originalResizeObserver;
    vi.unstubAllGlobals();
  });

  it('≤599px 呈现紧凑(单泳道),隐藏完整列容器', () => {
    renderCompact();
    expect(screen.getByTestId('board-compact')).toBeInTheDocument();
    expect(screen.queryByTestId('board-columns')).not.toBeInTheDocument();
  });

  it('chips 列出全部列;默认展示第一列,其余列不渲染', () => {
    renderCompact();
    expect(screen.getByTestId('compact-chip-todo')).toBeInTheDocument();
    expect(screen.getByTestId('compact-chip-in_progress')).toBeInTheDocument();
    expect(screen.getByTestId('compact-chip-done')).toBeInTheDocument();
    expect(screen.getByTestId('board-column-todo')).toBeInTheDocument();
    expect(screen.queryByTestId('board-column-in_progress')).not.toBeInTheDocument();
  });

  it('点击 chip 切换可见列', () => {
    renderCompact();
    fireEvent.click(screen.getByTestId('compact-chip-done'));
    expect(screen.getByTestId('board-column-done')).toBeInTheDocument();
    expect(screen.queryByTestId('board-column-todo')).not.toBeInTheDocument();
  });

  it('上一个/下一个按钮循环切列', () => {
    renderCompact();
    fireEvent.click(screen.getByTestId('compact-next'));
    expect(screen.getByTestId('board-column-in_progress')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('compact-prev'));
    expect(screen.getByTestId('board-column-todo')).toBeInTheDocument();
    // 循环:第一列向前 → 末列。
    fireEvent.click(screen.getByTestId('compact-prev'));
    expect(screen.getByTestId('board-column-done')).toBeInTheDocument();
  });

  it('横向滑动(≥50px)切列;纵向占优不切', () => {
    renderCompact();
    const body = screen.getByTestId('compact-body');
    // 向左滑(dx=-100)→ 下一列。
    fireEvent.pointerDown(body, { clientX: 200, clientY: 50, button: 0, pointerType: 'touch' });
    fireEvent.pointerUp(body, { clientX: 100, clientY: 50 });
    expect(screen.getByTestId('board-column-in_progress')).toBeInTheDocument();
    // 纵向为主(dy 大)→ 不切列。
    fireEvent.pointerDown(body, { clientX: 100, clientY: 50, button: 0, pointerType: 'touch' });
    fireEvent.pointerUp(body, { clientX: 90, clientY: 300 });
    expect(screen.getByTestId('board-column-in_progress')).toBeInTheDocument();
  });

  it('当前列内快速创建可用', async () => {
    const { onQuickCreate } = renderCompact();
    const input = screen.getByTestId('quick-add-todo');
    fireEvent.change(input, { target: { value: '紧凑新卡' } });
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
    });
    expect(onQuickCreate).toHaveBeenCalledWith('todo', '紧凑新卡');
  });
});

describe('BoardCompact 边界(直接渲染)', () => {
  beforeEach(() => ensurePointerEvent());
  afterEach(() => vi.unstubAllGlobals());

  it('空列集合:仅渲染导航,无卡片区', () => {
    renderWithProviders(
      <BoardCompact
        columns={[]}
        cardsByKey={{}}
        activeIndex={0}
        onSelectIndex={vi.fn()}
        getColumnLabel={(key) => key}
        renderCardBody={() => <div />}
      />,
    );
    expect(screen.getByTestId('compact-chips')).toBeInTheDocument();
    expect(screen.queryByTestId('compact-body')).not.toBeInTheDocument();
  });

  it('无 pointerdown 的 pointerup 为无操作(不切列)', () => {
    const onSelectIndex = vi.fn();
    renderWithProviders(
      <BoardCompact
        columns={[column('todo'), column('done')]}
        cardsByKey={{}}
        activeIndex={0}
        onSelectIndex={onSelectIndex}
        getColumnLabel={(key) => key}
        renderCardBody={(col) => <div data-testid={`body-${col.key}`} />}
      />,
    );
    fireEvent.pointerUp(screen.getByTestId('compact-body'), { clientX: 100, clientY: 50 });
    expect(onSelectIndex).not.toHaveBeenCalled();
  });
});
