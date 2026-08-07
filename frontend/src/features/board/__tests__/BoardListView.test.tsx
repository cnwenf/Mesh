/**
 * 看板「列表」布局组件测试:分组折叠、表头排序、列选取、内联编辑(成功/失败保留)、
 * 状态/优先级 PATCH、批量(状态/删除)、移动卡片、300 行规模化。
 *
 * updateIssue/bulkIssues 模块级 mock;getApiClient 返回占位客户端(不被实际调用)。
 * 断言按 data-testid/role 定位,文案以 en 目录实际英文(测试 locale=en)校验。
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiError } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import { bulkIssues, updateIssue } from '../../issues/api';
import type { IssueSummary } from '../../issues/types';
import { BoardListView } from '../BoardListView';
import type { BoardListViewProps } from '../BoardListView';
import type { BoardCard, BoardGroup } from '../projection';
import type { View } from '../types';

const fakeClient = {};

vi.mock('../../../api/instance', () => ({ getApiClient: () => fakeClient }));
vi.mock('../../issues/api', () => ({ updateIssue: vi.fn(), bulkIssues: vi.fn() }));

const UPDATED_AT = '2026-07-26T10:00:00Z';

function card(id: string, overrides: Partial<BoardCard> = {}): BoardCard {
  return {
    id,
    identifier: `WEB-${id}`,
    title: `Card ${id}`,
    state_category: 'todo',
    status: { id: 'st_todo', name: 'Todo', category: 'todo' },
    status_id: 'st_todo',
    priority: 'high',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position: 1,
    version: 1,
    updated_at: UPDATED_AT,
    ...overrides,
  };
}

function view(overrides: Partial<View> = {}): View {
  return {
    id: 'v1',
    workspace_id: 'ws-1',
    project_id: null,
    owner_member_id: 'm1',
    name: 'L',
    layout: 'list',
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
    updated_at: UPDATED_AT,
    can_write: true,
    ...overrides,
  };
}

const COLUMN_TARGET_STATUS: Record<string, string> = {
  backlog: 'st_backlog',
  todo: 'st_todo',
  in_progress: 'st_ip',
  in_review: 'st_ir',
  blocked: 'st_blocked',
  done: 'st_done',
};

function defaultGroups(): BoardGroup[] {
  return [
    { key: 'todo', label: 'Todo', count: 1, wip: null, data: [card('i1')] },
    {
      key: 'in_progress',
      label: 'In Progress',
      count: 1,
      wip: null,
      data: [
        card('i2', {
          state_category: 'in_progress',
          priority: 'low',
          assignee: { id: 'm1', name: 'Alice' },
        }),
      ],
    },
  ];
}

function renderList(overrides: Partial<BoardListViewProps> = {}) {
  const props: BoardListViewProps = {
    view: view(),
    groups: defaultGroups(),
    columnTargetStatus: COLUMN_TARGET_STATUS,
    canWrite: true,
    onOpenIssue: vi.fn(),
    onChanged: vi.fn(),
    ...overrides,
  };
  const utils = renderWithProviders(<BoardListView {...props} />);
  return { props, ...utils };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(updateIssue).mockResolvedValue({} as unknown as IssueSummary);
  vi.mocked(bulkIssues).mockResolvedValue({ succeeded: 2, failed: 0, errors: [] });
});

describe('BoardListView', () => {
  it('渲染分组为可折叠区段并携带计数', () => {
    renderList();
    const toggle = screen.getByTestId('list-group-toggle-todo');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle.textContent).toContain('1');
    expect(screen.getByTestId('list-group-toggle-in_progress')).toBeInTheDocument();
    expect(screen.getByTestId('list-row-i1')).toBeInTheDocument();
    // 折叠后隐藏行
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('list-row-i1')).not.toBeInTheDocument();
  });

  it('表头点击循环 asc → desc → none 且组内重排', () => {
    renderList({
      groups: [
        {
          key: 'todo',
          label: 'Todo',
          count: 2,
          wip: null,
          data: [card('i1', { priority: 'high' }), card('i3', { priority: 'urgent' })],
        },
      ],
    });
    const th = screen.getByTestId('list-th-priority');
    const rowOrder = (): (string | null)[] =>
      within(screen.getByTestId('list-group-todo'))
        .getAllByTestId(/^list-row-/)
        .map((row) => row.getAttribute('data-testid'));
    expect(th).toHaveAttribute('aria-sort', 'none');
    expect(rowOrder()).toEqual(['list-row-i1', 'list-row-i3']);

    fireEvent.click(screen.getByTestId('list-sort-priority'));
    expect(th).toHaveAttribute('aria-sort', 'ascending');
    expect(rowOrder()).toEqual(['list-row-i3', 'list-row-i1']);

    fireEvent.click(screen.getByTestId('list-sort-priority'));
    expect(th).toHaveAttribute('aria-sort', 'descending');
    expect(rowOrder()).toEqual(['list-row-i1', 'list-row-i3']);

    fireEvent.click(screen.getByTestId('list-sort-priority'));
    expect(th).toHaveAttribute('aria-sort', 'none');
  });

  it('列选取菜单隐藏/显示列', () => {
    renderList();
    expect(screen.getByTestId('list-th-status')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Columns' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Status' }));
    expect(screen.queryByTestId('list-th-status')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Columns' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Status' }));
    expect(screen.getByTestId('list-th-status')).toBeInTheDocument();
  });

  it('内联标题编辑成功 → updateIssue({title,version}) + onChanged', async () => {
    const { props } = renderList();
    fireEvent.click(screen.getByTestId('list-title-i1'));
    const input = screen.getByTestId('list-title-input-i1');
    fireEvent.change(input, { target: { value: 'New title' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(updateIssue).toHaveBeenCalledTimes(1));
    expect(updateIssue).toHaveBeenCalledWith(
      fakeClient,
      'i1',
      { title: 'New title', version: 1 },
      UPDATED_AT,
    );
    expect(props.onChanged).toHaveBeenCalledTimes(1);
  });

  it('优先级菜单 PATCH {priority}', async () => {
    renderList();
    fireEvent.click(
      within(screen.getByTestId('list-row-i1')).getByRole('button', { name: 'Change priority' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Urgent' }));
    await waitFor(() =>
      expect(updateIssue).toHaveBeenCalledWith(
        fakeClient,
        'i1',
        { priority: 'urgent', version: 1 },
        UPDATED_AT,
      ),
    );
  });

  it('状态菜单 PATCH {status_id: columnTargetStatus[cat]}', async () => {
    renderList();
    fireEvent.click(
      within(screen.getByTestId('list-row-i1')).getByRole('button', { name: 'Change status' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'In Progress' }));
    await waitFor(() =>
      expect(updateIssue).toHaveBeenCalledWith(
        fakeClient,
        'i1',
        { status_id: 'st_ip', version: 1 },
        UPDATED_AT,
      ),
    );
  });

  it('全选 + 批量状态 + 批量删除调用 bulkIssues', async () => {
    const { props } = renderList();
    fireEvent.click(screen.getByTestId('list-select-all'));
    expect(screen.getByTestId('bulk-bar')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Set status' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'In Progress' }));
    await waitFor(() =>
      expect(bulkIssues).toHaveBeenCalledWith(fakeClient, {
        issue_ids: ['i1', 'i2'],
        changes: { status_id: 'st_ip' },
      }),
    );
    await waitFor(() => expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument());
    expect(props.onChanged).toHaveBeenCalled();

    // 批量后选择被清空;重新全选再删除
    fireEvent.click(screen.getByTestId('list-select-all'));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('list-delete-confirm'));
    await waitFor(() =>
      expect(bulkIssues).toHaveBeenCalledWith(fakeClient, {
        issue_ids: ['i1', 'i2'],
        delete: true,
      }),
    );
  });

  it('内联编辑失败保留已输入值并显示 role=alert', async () => {
    vi.mocked(updateIssue).mockRejectedValue(
      new MeshApiError({ status: 409, code: 'conflict', message: 'conflict' }),
    );
    renderList();
    fireEvent.click(screen.getByTestId('list-title-i1'));
    const input = screen.getByTestId('list-title-input-i1');
    fireEvent.change(input, { target: { value: 'Bad edit' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByTestId('list-title-input-i1')).toHaveValue('Bad edit');
  });

  it('非 MeshApiError 失败回退通用错误文案', async () => {
    vi.mocked(updateIssue).mockRejectedValue(new Error('boom'));
    renderList();
    fireEvent.click(screen.getByTestId('list-title-i1'));
    const input = screen.getByTestId('list-title-input-i1');
    fireEvent.change(input, { target: { value: 'Bad edit' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('could not load'));
  });

  it('批量部分失败呈现 成功/失败 结果 toast', async () => {
    vi.mocked(bulkIssues).mockRejectedValue(
      new MeshApiError({
        status: 422,
        code: 'bulk_partial_failure',
        message: 'x',
        details: { succeeded: 1, failed: 1, errors: [] },
      }),
    );
    renderList();
    fireEvent.click(screen.getByTestId('list-select-all'));
    fireEvent.click(screen.getByRole('button', { name: 'Set status' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'In Progress' }));
    await waitFor(() => expect(screen.getByText(/Succeeded 1 \/ failed 1/)).toBeInTheDocument());
  });

  it('批量 MeshApiError(非部分失败)呈现错误 toast', async () => {
    vi.mocked(bulkIssues).mockRejectedValue(
      new MeshApiError({ status: 403, code: 'definitely_missing_code', message: 'x' }),
    );
    renderList();
    fireEvent.click(screen.getByTestId('list-select-all'));
    fireEvent.click(screen.getByRole('button', { name: 'Set priority' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Urgent' }));
    await waitFor(() => expect(screen.getByText(/definitely_missing_code/)).toBeInTheDocument());
  });

  it('移动端卡片行渲染(与表格同源数据)', () => {
    renderList();
    expect(screen.getByTestId('list-card-i1')).toBeInTheDocument();
    expect(screen.getByTestId('list-card-i2')).toBeInTheDocument();
  });

  it('只读模式(canWrite=false)标题点击打开 issue 且不进入编辑', () => {
    const { props } = renderList({ canWrite: false });
    fireEvent.click(screen.getByTestId('list-title-i1'));
    expect(props.onOpenIssue).toHaveBeenCalledWith('i1');
    expect(screen.queryByTestId('list-title-input-i1')).not.toBeInTheDocument();
  });

  it('分组标签按 group_by 翻译(priority / i18n 键 label / 原样)', () => {
    renderList({
      view: view({ group_by: 'priority' }),
      groups: [
        { key: 'high', label: 'whatever', count: 1, wip: null, data: [card('p1')] },
        { key: 'zzz', label: 'board.category.done', count: 1, wip: null, data: [card('p2')] },
      ],
    });
    expect(screen.getByTestId('list-group-toggle-high').textContent).toContain('High');
    expect(screen.getByTestId('list-group-toggle-zzz').textContent).toContain('Done');

    renderList({
      view: view({ group_by: 'assignee' }),
      groups: [{ key: 'm1', label: 'Alice', count: 1, wip: null, data: [card('a1')] }],
    });
    expect(screen.getByTestId('list-group-toggle-m1').textContent).toContain('Alice');
  });

  it('空分组呈现空态', () => {
    renderList({ groups: [] });
    expect(screen.getByTestId('list-empty')).toBeInTheDocument();
  });

  it('300 行渲染不崩溃', () => {
    const manyCards = Array.from({ length: 300 }, (_, index) => card(`c${index}`));
    renderList({
      groups: [{ key: 'todo', label: 'Todo', count: 300, wip: null, data: manyCards }],
    });
    expect(screen.getByTestId('list-row-c299')).toBeInTheDocument();
    expect(screen.getByTestId('list-card-c299')).toBeInTheDocument();
  });

  it('单行勾选/取消驱动批量条', () => {
    renderList();
    const rowBox = screen.getByTestId('list-select-i1');
    fireEvent.click(rowBox);
    expect(screen.getByTestId('bulk-bar')).toBeInTheDocument();
    fireEvent.click(rowBox);
    expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument();
  });

  it('移动卡片勾选与标题打开 issue', () => {
    const { props } = renderList();
    const mobileCard = screen.getByTestId('list-card-i1');
    fireEvent.click(within(mobileCard).getByRole('checkbox'));
    expect(screen.getByTestId('bulk-bar')).toBeInTheDocument();
    fireEvent.click(within(mobileCard).getByRole('button', { name: 'Card i1' }));
    expect(props.onOpenIssue).toHaveBeenCalledWith('i1');
  });

  it('标题编辑:Esc 取消、无改动回车均不触发保存', () => {
    renderList();
    // Esc 取消
    fireEvent.click(screen.getByTestId('list-title-i1'));
    fireEvent.keyDown(screen.getByTestId('list-title-input-i1'), { key: 'Escape' });
    expect(screen.queryByTestId('list-title-input-i1')).not.toBeInTheDocument();
    // 无改动回车 → 直接关闭
    fireEvent.click(screen.getByTestId('list-title-i1'));
    fireEvent.keyDown(screen.getByTestId('list-title-input-i1'), { key: 'Enter' });
    expect(screen.queryByTestId('list-title-input-i1')).not.toBeInTheDocument();
    expect(updateIssue).not.toHaveBeenCalled();
  });

  it('批量条取消选择按钮清空选择', () => {
    renderList();
    fireEvent.click(screen.getByTestId('list-select-all'));
    expect(screen.getByTestId('bulk-bar')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear selection' }));
    expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument();
  });

  it('删除确认对话框可经取消/关闭按钮撤销', () => {
    renderList();
    fireEvent.click(screen.getByTestId('list-select-all'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // 取消仅关闭对话框,不清空选择;批量条仍在,可再次打开删除确认
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(bulkIssues).not.toHaveBeenCalled();
  });

  it('行操作菜单:打开 issue 与删除确认', async () => {
    const { props } = renderList();
    fireEvent.click(
      within(screen.getByTestId('list-row-i1')).getByRole('button', { name: 'Row actions' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Open' }));
    expect(props.onOpenIssue).toHaveBeenCalledWith('i1');

    fireEvent.click(
      within(screen.getByTestId('list-row-i1')).getByRole('button', { name: 'Row actions' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    fireEvent.click(screen.getByTestId('list-delete-confirm'));
    await waitFor(() =>
      expect(bulkIssues).toHaveBeenCalledWith(fakeClient, { issue_ids: ['i1'], delete: true }),
    );
  });

  it('行操作菜单:未收藏显示添加条目,点击回调 issue id(L222)', () => {
    const onToggleFavorite = vi.fn();
    renderList({ favoriteIssueIds: new Set<string>(), onToggleFavorite });
    fireEvent.click(
      within(screen.getByTestId('list-row-i1')).getByRole('button', { name: 'Row actions' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add to favorites' }));
    expect(onToggleFavorite).toHaveBeenCalledWith('i1');
  });

  it('行操作菜单:已收藏 issue 显示移除条目(L222)', () => {
    renderList({ favoriteIssueIds: new Set(['i1']), onToggleFavorite: vi.fn() });
    fireEvent.click(
      within(screen.getByTestId('list-row-i1')).getByRole('button', { name: 'Row actions' }),
    );
    expect(screen.getByRole('menuitem', { name: 'Remove from favorites' })).toBeInTheDocument();
  });

  it('行操作菜单:未提供收藏回调时不渲染收藏条目(L222)', () => {
    renderList();
    fireEvent.click(
      within(screen.getByTestId('list-row-i1')).getByRole('button', { name: 'Row actions' }),
    );
    expect(screen.queryByRole('menuitem', { name: 'Add to favorites' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'Remove from favorites' })).toBeNull();
  });

  it('批量非 MeshApiError 回退通用错误 toast', async () => {
    vi.mocked(bulkIssues).mockRejectedValue(new Error('boom'));
    renderList();
    fireEvent.click(screen.getByTestId('list-select-all'));
    fireEvent.click(screen.getByRole('button', { name: 'Set status' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'In Progress' }));
    await waitFor(() => expect(screen.getByText(/could not load/)).toBeInTheDocument());
  });

  it('批量部分失败缺失明细时按 0 计', async () => {
    vi.mocked(bulkIssues).mockRejectedValue(
      new MeshApiError({ status: 422, code: 'bulk_partial_failure', message: 'x' }),
    );
    renderList();
    fireEvent.click(screen.getByTestId('list-select-all'));
    fireEvent.click(screen.getByRole('button', { name: 'Set status' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'In Progress' }));
    await waitFor(() => expect(screen.getByText(/Succeeded 0 \/ failed 0/)).toBeInTheDocument());
  });

  it('优先级内联编辑失败显示 role=alert', async () => {
    vi.mocked(updateIssue).mockRejectedValue(
      new MeshApiError({ status: 409, code: 'conflict', message: 'x' }),
    );
    renderList();
    fireEvent.click(
      within(screen.getByTestId('list-row-i1')).getByRole('button', { name: 'Change priority' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Urgent' }));
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
  });

  it('状态内联编辑失败显示 role=alert', async () => {
    vi.mocked(updateIssue).mockRejectedValue(
      new MeshApiError({ status: 409, code: 'conflict', message: 'x' }),
    );
    renderList();
    fireEvent.click(
      within(screen.getByTestId('list-row-i1')).getByRole('button', { name: 'Change status' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'In Progress' }));
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
  });

  it('状态为空与非法更新时间降级渲染', () => {
    renderList({
      groups: [
        {
          key: 'todo',
          label: 'Todo',
          count: 1,
          wip: null,
          data: [card('z1', { status: null, updated_at: 'not-a-date' })],
        },
      ],
    });
    // StatusBadge 回退分类文案;UpdatedCell 对非法时间回退原始串
    expect(screen.getByTestId('list-row-z1').textContent).toContain('not-a-date');
  });

  it('group_by=state_category 翻译分组标签(含未知分类兜底)', () => {
    renderList({
      view: view({ group_by: 'state_category' }),
      groups: [
        { key: 'done', label: 'x', count: 1, wip: null, data: [card('d1')] },
        { key: 'mystery', label: 'y', count: 1, wip: null, data: [card('m1')] },
      ],
    });
    expect(screen.getByTestId('list-group-toggle-done').textContent).toContain('Done');
    // 未知分类键回退到 board.category.todo
    expect(screen.getByTestId('list-group-toggle-mystery').textContent).toContain('Todo');
  });
});
