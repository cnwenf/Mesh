/**
 * 看板投影层交互测试(§4.3/§4.4/T22):真实卡片渲染、拖拽原子 move(乐观 + 收敛)、
 * WIP block 弹回、跨项目预览确认、列底快速创建。fetch 桩按路由返回投影分组包络。
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { BoardPage } from '../BoardPage';
import type { BoardCard } from '../projection';
import type { View } from '../types';
import { ensurePointerEvent, mockRect } from './dragTestUtils';

const ME = {
  user: { id: 'u', email: 'o@x.com', display_name: 'O' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'T',
      workspace_slug: 't',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

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
    updated_at: '2026-07-26T10:00:00Z',
    ...overrides,
  };
}

function view(overrides: Partial<View> = {}): View {
  return {
    id: 'v1',
    workspace_id: 'ws-1',
    project_id: null,
    owner_member_id: 'm1',
    name: 'B',
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
    updated_at: '2026-07-26T00:00:00Z',
    can_write: true,
    ...overrides,
  };
}

interface StubOptions {
  view?: View;
  groups?: unknown[];
  columns?: unknown[];
  lanes?: unknown[];
  columnTargetStatus?: Record<string, string>;
  moveStatus?: number;
  moveBody?: unknown;
  wipStatus?: number;
}

function stubBoard(options: StubOptions = {}) {
  const v = options.view ?? view();
  const groups = options.groups ?? [
    { key: 'todo', label: 'Todo', count: 1, wip: null, data: [card('i1')] },
    {
      key: 'in_progress',
      label: 'In Progress',
      count: 1,
      wip: { limit: 1, enforcement: 'block' },
      data: [card('i2', { state_category: 'in_progress' })],
    },
  ];
  const calls: { url: string; method: string; body?: unknown }[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (method === 'GET' && url.includes('/issues')) {
      return fakeResponse({
        body: {
          layout: 'board',
          group_by: v.group_by ?? 'state_category',
          sub_group_by: v.sub_group_by,
          column_target_status: options.columnTargetStatus ?? {
            todo: 'st_todo',
            in_progress: 'st_ip',
          },
          ...(v.sub_group_by === null
            ? { groups }
            : { columns: options.columns ?? [], lanes: options.lanes ?? [] }),
          next_cursor: null,
        },
      });
    }
    if (method === 'GET' && url.includes('/views')) {
      return fakeResponse({ body: { data: [v], next_cursor: null } });
    }
    if (method === 'POST' && url.includes('/moves')) {
      return fakeResponse({
        status: options.moveStatus ?? 200,
        body: options.moveBody ?? {
          data: card('i1', { state_category: 'in_progress', version: 2 }),
        },
      });
    }
    if (method === 'POST' && url.includes('/reorder')) {
      return fakeResponse({
        body: {
          data: { id: 'i1', group_key: 'todo', sub_group_key: 'high', position: 2 },
        },
      });
    }
    if (method === 'PATCH' && url.includes('/wip')) {
      return fakeResponse({
        status: options.wipStatus ?? 200,
        body:
          options.wipStatus && options.wipStatus >= 400
            ? { error: { code: 'internal_error', message: 'x' } }
            : { data: v },
      });
    }
    if (method === 'POST' && url.includes('/issues')) {
      return fakeResponse({ status: 201, body: { data: card('i-new') } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

/**
 * 指针拖拽落点模拟(替换旧 HTML5 DnD,§9.4):源卡 → 越阈值 → 命中目标列 → 抬起。
 * jsdom 无真实布局,逐元素 mock getBoundingClientRect(见 dragTestUtils)。
 */
function dropCard(issueId: string, sourceColumnKey: string, targetColumnKey: string): void {
  const sourceCard = screen.getByTestId(`board-card-${issueId}`);
  mockRect(sourceCard, { left: 0, top: 0, right: 100, bottom: 40 });
  mockRect(screen.getByTestId(`board-column-${sourceColumnKey}`), {
    left: 0,
    top: 0,
    right: 100,
    bottom: 600,
  });
  mockRect(screen.getByTestId(`board-column-${targetColumnKey}`), {
    left: 200,
    top: 0,
    right: 300,
    bottom: 600,
  });
  fireEvent.pointerDown(sourceCard, { clientX: 10, clientY: 10, button: 0, pointerType: 'mouse' });
  fireEvent.pointerMove(document, { clientX: 20, clientY: 10 }); // 越阈值进入拖拽
  fireEvent.pointerMove(document, { clientX: 250, clientY: 300 }); // 命中目标列
  fireEvent.pointerUp(document, { clientX: 250, clientY: 300 });
}

describe('看板投影层交互', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    ensurePointerEvent();
  });
  afterEach(() => vi.unstubAllGlobals());

  it('渲染真实卡片到对应列', async () => {
    stubBoard();
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    const todo = await screen.findByTestId('board-column-todo');
    expect(within(todo).getByTestId('board-card-i1')).toBeInTheDocument();
    const ip = screen.getByTestId('board-column-in_progress');
    expect(within(ip).getByTestId('board-card-i2')).toBeInTheDocument();
    // 计数来自投影响应。
    expect(screen.getByTestId('count-in_progress')).toHaveTextContent('1');
  });

  it('拖拽跨列 → 调用 moves 并乐观收敛(§4.3)', async () => {
    const calls = stubBoard({
      groups: [
        { key: 'todo', label: 'Todo', count: 1, wip: null, data: [card('i1')] },
        { key: 'in_progress', label: 'In Progress', count: 0, wip: null, data: [] },
      ],
    });
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-column-todo');
    dropCard('i1', 'todo', 'in_progress');
    await waitFor(() => {
      const move = calls.find((c) => c.method === 'POST' && c.url.includes('/moves'));
      expect(move).toBeDefined();
      expect(move?.body).toMatchObject({ issue_id: 'i1', to_group_key: 'in_progress' });
    });
  });

  it('WIP block 服务端拒绝 → 422 弹回原列并提示(§4.4)', async () => {
    // 客户端看目标列未满(允许落位),服务端计数已满 → 422 → 弹回。
    const calls = stubBoard({
      groups: [
        { key: 'todo', label: 'Todo', count: 1, wip: null, data: [card('i1')] },
        {
          key: 'in_progress',
          label: 'In Progress',
          count: 0,
          wip: { limit: 1, enforcement: 'block' },
          data: [],
        },
      ],
      moveStatus: 422,
      moveBody: {
        error: {
          code: 'wip_limit_exceeded',
          message: 'x',
          details: { group_key: 'in_progress', limit: 1, count: 1 },
        },
      },
    });
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-column-todo');
    dropCard('i1', 'todo', 'in_progress');
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.includes('/moves'))).toBe(true);
    });
    // 弹回:i1 仍在 todo 列。
    await waitFor(() => {
      expect(
        within(screen.getByTestId('board-column-todo')).getByTestId('board-card-i1'),
      ).toBeInTheDocument();
    });
  });

  it('跨项目拖拽未确认 → 预览模态,确认后携 confirm(§4.3/T22)', async () => {
    const projectView = view({ group_by: 'project' });
    const calls = stubBoard({
      view: projectView,
      columnTargetStatus: {},
      groups: [
        {
          key: 'p-src',
          label: 'Src',
          count: 1,
          wip: null,
          data: [card('i1', { project_id: 'p-src' })],
        },
        {
          key: 'p-dst',
          label: 'Dst',
          count: 1,
          wip: null,
          data: [card('i3', { project_id: 'p-dst' })],
        },
      ],
      moveStatus: 422,
      moveBody: {
        error: {
          code: 'move_confirmation_required',
          message: 'x',
          details: {
            preview: {
              issue_id: 'i1',
              mapped_fields: [{ field: 'status' }],
              cleared_fields: [{ field: 'milestone_id' }],
              kept_fields: [],
            },
          },
        },
      },
    });
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-column-p-src');
    dropCard('i1', 'p-src', 'p-dst');
    // 预览模态出现(映射/清除清单)。
    expect(await screen.findByTestId('move-preview-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('move-preview-mapped')).toBeInTheDocument();
    expect(screen.getByTestId('move-preview-cleared')).toBeInTheDocument();
    // 确认 → 再次 POST 携 confirm:true。
    fireEvent.click(screen.getByTestId('move-preview-confirm'));
    await waitFor(() => {
      const confirmCall = calls.find(
        (c) =>
          c.method === 'POST' &&
          c.url.includes('/moves') &&
          (c.body as { confirm?: boolean })?.confirm === true,
      );
      expect(confirmCall).toBeDefined();
    });
  });

  it('列底快速创建 → POST issues 并刷新(§4.5)', async () => {
    const calls = stubBoard();
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-column-todo');
    const input = screen.getByTestId('quick-add-todo');
    fireEvent.change(input, { target: { value: '新卡片' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => {
      const create = calls.find((c) => c.method === 'POST' && c.url.includes('/views/v1/issues'));
      expect(create).toBeDefined();
      expect(create?.body).toMatchObject({ title: '新卡片', group_key: 'todo' });
    });
  });

  it('二维泳道渲染 cell，快速创建携带两轴', async () => {
    const calls = stubBoard({
      view: view({ group_by: 'state_category', sub_group_by: 'priority' }),
      columns: [
        { key: 'todo', label: 'Todo', count: 1, wip: null },
        { key: 'done', label: 'Done', count: 0, wip: null },
      ],
      lanes: [
        {
          key: 'high',
          label: 'High',
          count: 1,
          groups: [
            { key: 'todo', count: 1, data: [card('i1')] },
            { key: 'done', count: 0, data: [] },
          ],
        },
      ],
    });
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    expect(await screen.findByTestId('board-swimlane-high')).toBeInTheDocument();
    expect(
      within(screen.getByTestId('board-column-high-todo')).getByTestId('board-card-i1'),
    ).toBeInTheDocument();

    const input = screen.getByTestId('quick-add-high-done');
    fireEvent.change(input, { target: { value: 'Two dimensional' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => {
      const create = calls.find(
        (call) => call.method === 'POST' && call.url.includes('/views/v1/issues'),
      );
      expect(create?.body).toEqual({
        title: 'Two dimensional',
        group_key: 'done',
        sub_group_key: 'high',
      });
    });
  });

  it('跨泳道拖拽携带 to_sub_group_key', async () => {
    const calls = stubBoard({
      view: view({ group_by: 'state_category', sub_group_by: 'priority' }),
      columns: [
        { key: 'todo', label: 'Todo', count: 1, wip: null },
        { key: 'done', label: 'Done', count: 0, wip: null },
      ],
      lanes: [
        {
          key: 'high',
          label: 'High',
          count: 1,
          groups: [
            { key: 'todo', count: 1, data: [card('i1')] },
            { key: 'done', count: 0, data: [] },
          ],
        },
        {
          key: 'low',
          label: 'Low',
          count: 0,
          groups: [
            { key: 'todo', count: 0, data: [] },
            { key: 'done', count: 0, data: [] },
          ],
        },
      ],
    });
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    const sourceCard = await screen.findByTestId('board-card-i1');
    mockRect(sourceCard, { left: 220, top: 20, right: 320, bottom: 60 });
    mockRect(screen.getByTestId('board-column-high-todo'), {
      left: 200,
      top: 0,
      right: 340,
      bottom: 200,
    });
    mockRect(screen.getByTestId('board-column-low-done'), {
      left: 400,
      top: 240,
      right: 540,
      bottom: 440,
    });
    fireEvent.pointerDown(sourceCard, {
      clientX: 230,
      clientY: 30,
      button: 0,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(document, { clientX: 245, clientY: 30 });
    fireEvent.pointerMove(document, { clientX: 470, clientY: 320 });
    expect(screen.getByTestId('board-column-low-done')).toHaveClass(
      'mesh-board__column--drag-over',
    );
    fireEvent.pointerUp(document, { clientX: 470, clientY: 320 });

    await waitFor(() => {
      const move = calls.find((call) => call.method === 'POST' && call.url.includes('/moves'));
      expect(move?.body).toMatchObject({
        issue_id: 'i1',
        to_group_key: 'done',
        to_sub_group_key: 'low',
      });
    });
  });

  it('二维 cell 内排序使用 reorder 并携带 sub_group_key', async () => {
    const calls = stubBoard({
      view: view({ group_by: 'state_category', sub_group_by: 'priority' }),
      columns: [{ key: 'todo', label: 'Todo', count: 1, wip: null }],
      lanes: [
        {
          key: 'high',
          label: 'High',
          count: 1,
          groups: [{ key: 'todo', count: 1, data: [card('i1')] }],
        },
      ],
    });
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    const issue = await screen.findByTestId('board-card-i1');
    fireEvent.keyDown(issue, { key: 'ArrowDown' });
    fireEvent.keyDown(issue, { key: 'Enter' });

    await waitFor(() => {
      const reorder = calls.find((call) => call.method === 'POST' && call.url.includes('/reorder'));
      expect(reorder?.body).toMatchObject({
        issue_id: 'i1',
        to_group_key: 'todo',
        sub_group_key: 'high',
      });
      expect(calls.some((call) => call.method === 'POST' && call.url.includes('/moves'))).toBe(
        false,
      );
    });
  });

  it('WIP 配置保存失败 → 顶部 toast(§6.12 retry 反馈)', async () => {
    stubBoard({
      wipStatus: 500,
      view: view({ board_settings: { wip: { in_progress: { limit: 1, enforcement: 'block' } } } }),
    });
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-column-todo');
    fireEvent.click(screen.getByTestId('panel-toggle-wip'));
    fireEvent.click(screen.getByTestId('wip-save-in_progress'));
    await waitFor(() => {
      expect(document.querySelector('.mesh-toast--danger')).toBeInTheDocument();
    });
  });
});
