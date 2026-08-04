import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { BoardSwimlanes } from '../BoardSwimlanes';
import type { BoardCard, BoardLane, BoardProjectionColumn } from '../projection';

function card(id: string): BoardCard {
  return {
    id,
    identifier: `WEB-${id}`,
    title: `Card ${id}`,
    state_category: 'todo',
    status: null,
    status_id: 'status-todo',
    priority: 'high',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position: 1,
    version: 1,
    updated_at: '',
  };
}

const COLUMNS: readonly BoardProjectionColumn[] = [
  { key: 'todo', label: 'Todo', count: 2, wip: null },
  { key: 'done', label: 'Done', count: 0, wip: null },
];

const LANES: readonly BoardLane[] = [
  {
    key: 'high',
    label: 'High',
    count: 1,
    groups: [
      { key: 'todo', count: 1, data: [card('high')] },
      { key: 'done', count: 0, data: [] },
    ],
  },
  {
    key: 'low',
    label: 'Low',
    count: 1,
    groups: [
      { key: 'todo', count: 1, data: [card('low')] },
      { key: 'done', count: 0, data: [] },
    ],
  },
];

describe('BoardSwimlanes', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('呈现共享列骨架与 lane-local cell，DOM id 按 lane 隔离', () => {
    renderWithProviders(
      <BoardSwimlanes
        columns={COLUMNS}
        lanes={LANES}
        groupBy="state_category"
        subGroupBy="priority"
        collapsedColumns={[]}
        canWrite
        dragEnabled
        onToggleCollapse={vi.fn()}
        onDropCard={vi.fn()}
        onQuickCreate={vi.fn()}
      />,
    );

    expect(screen.getByTestId('board-swimlanes')).toBeInTheDocument();
    expect(screen.getByTestId('board-swimlane-high')).toHaveTextContent('High');
    expect(screen.getByTestId('board-swimlane-low')).toHaveTextContent('Low');
    expect(
      within(screen.getByTestId('board-column-high-todo')).getByTestId('board-card-high'),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId('board-column-low-todo')).getByTestId('board-card-low'),
    ).toBeInTheDocument();
  });

  it('快速创建回传列与泳道两轴', async () => {
    const onQuickCreate = vi.fn();
    renderWithProviders(
      <BoardSwimlanes
        columns={COLUMNS}
        lanes={LANES}
        groupBy="state_category"
        subGroupBy="priority"
        collapsedColumns={[]}
        canWrite
        dragEnabled
        onToggleCollapse={vi.fn()}
        onDropCard={vi.fn()}
        onQuickCreate={onQuickCreate}
      />,
    );

    const input = screen.getByTestId('quick-add-low-done');
    fireEvent.change(input, { target: { value: '  cell card  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onQuickCreate).toHaveBeenCalledWith('done', 'cell card', 'low');
    await waitFor(() =>
      expect(screen.queryByTestId('quick-add-pending-low-done')).not.toBeInTheDocument(),
    );
  });

  it('紧凑视口一次只显示一条 lane，可通过 lane tab 切换', () => {
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
    renderWithProviders(
      <BoardSwimlanes
        columns={COLUMNS}
        lanes={LANES}
        groupBy="state_category"
        subGroupBy="priority"
        collapsedColumns={[]}
        canWrite
        dragEnabled
        onToggleCollapse={vi.fn()}
        onDropCard={vi.fn()}
        onQuickCreate={vi.fn()}
      />,
    );

    expect(screen.getByTestId('board-swimlane-high')).toBeInTheDocument();
    expect(screen.queryByTestId('board-swimlane-low')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Low (1)' }));
    expect(screen.queryByTestId('board-swimlane-high')).not.toBeInTheDocument();
    expect(screen.getByTestId('board-swimlane-low')).toBeInTheDocument();
  });

  it('动态主轴使用服务端列标签，并尊重全 lane 共享折叠状态', () => {
    renderWithProviders(
      <BoardSwimlanes
        columns={COLUMNS}
        lanes={LANES}
        groupBy="status"
        subGroupBy="priority"
        collapsedColumns={['done']}
        canWrite
        dragEnabled
        onToggleCollapse={vi.fn()}
        onDropCard={vi.fn()}
        onQuickCreate={vi.fn()}
      />,
    );

    expect(within(screen.getByTestId('board-column-high-todo')).getByText('Todo')).toBeVisible();
    expect(
      within(screen.getByTestId('board-column-low-done')).queryByTestId('column-body-low-done'),
    ).not.toBeInTheDocument();
  });

  it('空 lane 集与缺失 cell 都 fail closed 为空态', () => {
    const props = {
      columns: COLUMNS,
      groupBy: 'state_category',
      subGroupBy: 'priority',
      collapsedColumns: [],
      canWrite: true,
      dragEnabled: true,
      onToggleCollapse: vi.fn(),
      onDropCard: vi.fn(),
      onQuickCreate: vi.fn(),
    } as const;
    const empty = renderWithProviders(<BoardSwimlanes {...props} lanes={[]} />);
    expect(screen.getByTestId('board-swimlane-grid')).toBeEmptyDOMElement();
    empty.unmount();

    const sparseLane: BoardLane = {
      key: 'high',
      label: 'High',
      count: 1,
      groups: [{ key: 'todo', count: 1, data: [card('only')] }],
    };
    renderWithProviders(<BoardSwimlanes {...props} lanes={[sparseLane]} />);
    expect(
      within(screen.getByTestId('board-column-high-done')).getByText('No cards'),
    ).toBeInTheDocument();
  });
});
