/**
 * 移动紧凑看板测试(design-quality §8.3):容器 ≤599px 单泳道 + 顶部 chips 切列、
 * 上一个/下一个按钮、横向滑动切列(阈值 50px)、当前列快速创建。
 *
 * 紧凑判定基准为视口模式(useIsCompactViewport,matchMedia,§8.1 模式表)。
 * 测试以 matchMedia 桩(matches: true)驱动 compact 形态,无测量时序。
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
    id,
    identifier: `WEB-${id}`,
    title: `Card ${id}`,
    state_category: 'todo',
    status: { id: 'st', name: 'Todo', category: 'todo' },
    status_id: 'st',
    priority: 'high',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position,
    version: 1,
    updated_at: '',
  };
}

function column(key: string): BoardColumn {
  return {
    key,
    label: `board.category.${key}`,
    collapsed: false,
    wip: null,
    count: 1,
    placeholder: false,
  };
}

/** compact 视口桩:matchMedia('(max-width: 599px)') → matches: true。 */
function stubCompactViewport(): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches: query === '(max-width: 599px)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

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
  return { onDropCard, onQuickCreate };
}

describe('BoardCompact 紧凑看板(§8.3)', () => {
  beforeEach(() => {
    ensurePointerEvent();
    stubCompactViewport();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('≤599px 呈现紧凑(单泳道),隐藏完整列容器', () => {
    renderCompact();
    expect(screen.getByTestId('board-compact')).toBeInTheDocument();
    expect(screen.queryByTestId('board-columns')).not.toBeInTheDocument();
  });

  it('chips 列出全部列;默认展示第一列,其余列不渲染', () => {
    renderCompact();
    const todoTab = screen.getByTestId('compact-chip-todo');
    expect(todoTab).toBeInTheDocument();
    expect(todoTab).toHaveAttribute('data-slot', 'button');
    expect(screen.getByTestId('compact-chip-in_progress')).toBeInTheDocument();
    expect(screen.getByTestId('compact-chip-done')).toBeInTheDocument();
    expect(screen.getByTestId('board-column-todo')).toBeInTheDocument();
    expect(screen.queryByTestId('board-column-in_progress')).not.toBeInTheDocument();
    const panel = screen.getByRole('tabpanel');
    expect(todoTab).toHaveAttribute('aria-controls', panel.id);
    expect(panel).toHaveAttribute('aria-labelledby', todoTab.id);
    expect(todoTab).toHaveAttribute('tabindex', '0');
    expect(screen.getByTestId('compact-chip-in_progress')).toHaveAttribute('tabindex', '-1');
  });

  it('列 tabs 支持方向键与 Home/End 漫游，并同步唯一可见列', () => {
    renderCompact();
    const todo = screen.getByTestId('compact-chip-todo');
    todo.focus();
    fireEvent.keyDown(todo, { key: 'ArrowRight' });
    expect(screen.getByTestId('compact-chip-in_progress')).toHaveFocus();
    expect(screen.getByTestId('board-column-in_progress')).toBeInTheDocument();

    fireEvent.keyDown(screen.getByTestId('compact-chip-in_progress'), { key: 'End' });
    expect(screen.getByTestId('compact-chip-done')).toHaveFocus();
    expect(screen.getByTestId('board-column-done')).toBeInTheDocument();

    fireEvent.keyDown(screen.getByTestId('compact-chip-done'), { key: 'Home' });
    expect(screen.getByTestId('compact-chip-todo')).toHaveFocus();
    expect(screen.getByTestId('board-column-todo')).toBeInTheDocument();

    // 反向方向键同样循环；非导航键不改变当前 tab。
    fireEvent.keyDown(screen.getByTestId('compact-chip-todo'), { key: 'ArrowLeft' });
    expect(screen.getByTestId('compact-chip-done')).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId('compact-chip-done'), { key: 'ArrowUp' });
    expect(screen.getByTestId('compact-chip-in_progress')).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId('compact-chip-in_progress'), { key: 'Enter' });
    expect(screen.getByTestId('compact-chip-in_progress')).toHaveFocus();
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
    // 向右滑(dx=100)→ 上一列。
    fireEvent.pointerDown(body, { clientX: 100, clientY: 50, button: 0, pointerType: 'touch' });
    fireEvent.pointerUp(body, { clientX: 200, clientY: 50 });
    expect(screen.getByTestId('board-column-todo')).toBeInTheDocument();
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
    expect(screen.getByTestId('compact-prev')).toBeDisabled();
    expect(screen.getByTestId('compact-next')).toBeDisabled();
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

  it('仅一列时滑动保持原列且不产生无效索引', () => {
    const onSelectIndex = vi.fn();
    renderWithProviders(
      <BoardCompact
        columns={[column('todo')]}
        cardsByKey={{}}
        activeIndex={-1}
        onSelectIndex={onSelectIndex}
        getColumnLabel={(key) => key}
        renderCardBody={(col) => <div data-testid={`body-${col.key}`} />}
      />,
    );
    const body = screen.getByTestId('compact-body');
    fireEvent.pointerDown(body, { clientX: 100, clientY: 50, pointerType: 'touch' });
    fireEvent.pointerUp(body, { clientX: 200, clientY: 50, pointerType: 'touch' });
    expect(onSelectIndex).not.toHaveBeenCalled();
    expect(screen.getByTestId('body-todo')).toBeInTheDocument();
  });
});
