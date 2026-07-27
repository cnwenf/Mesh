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

const ME = {
  user: { id: 'u', email: 'o@x.com', display_name: 'O' },
  memberships: [
    { workspace_id: 'ws-1', workspace_name: 'T', workspace_slug: 't', role: 'owner', status: 'active', joined_at: null },
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
    id: 'v1', workspace_id: 'ws-1', project_id: null, owner_member_id: 'm1', name: 'B',
    layout: 'board', visibility: 'private', filters: {}, group_by: null, sub_group_by: null,
    sort: [], display_fields: [], board_settings: {}, position: 1, is_default: true,
    created_at: '', updated_at: '2026-07-26T00:00:00Z', can_write: true, ...overrides,
  };
}

interface StubOptions {
  view?: View;
  groups?: unknown[];
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
      key: 'in_progress', label: 'In Progress', count: 1,
      wip: { limit: 1, enforcement: 'block' }, data: [card('i2', { state_category: 'in_progress' })],
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
          column_target_status: options.columnTargetStatus ?? { todo: 'st_todo', in_progress: 'st_ip' },
          groups,
          next_cursor: null,
        },
      });
    }
    if (method === 'GET' && url.includes('/views')) {
      return fakeResponse({ body: { data: [v], next_cursor: null } });
    }
    if (method === 'POST' && url.includes('/moves')) {
      return fakeResponse({ status: options.moveStatus ?? 200, body: options.moveBody ?? { data: card('i1', { state_category: 'in_progress', version: 2 }) } });
    }
    if (method === 'PATCH' && url.includes('/wip')) {
      return fakeResponse({
        status: options.wipStatus ?? 200,
        body: options.wipStatus && options.wipStatus >= 400
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

function dropCard(target: HTMLElement, issueId: string): void {
  fireEvent.drop(target, { dataTransfer: { getData: () => issueId } });
}

describe('看板投影层交互', () => {
  beforeEach(() => vi.unstubAllGlobals());
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
    const ipBody = screen.getByTestId('column-body-in_progress');
    dropCard(ipBody, 'i1');
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
        { key: 'in_progress', label: 'In Progress', count: 0, wip: { limit: 1, enforcement: 'block' }, data: [] },
      ],
      moveStatus: 422,
      moveBody: { error: { code: 'wip_limit_exceeded', message: 'x', details: { group_key: 'in_progress', limit: 1, count: 1 } } },
    });
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-column-todo');
    dropCard(screen.getByTestId('column-body-in_progress'), 'i1');
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.includes('/moves'))).toBe(true);
    });
    // 弹回:i1 仍在 todo 列。
    await waitFor(() => {
      expect(within(screen.getByTestId('board-column-todo')).getByTestId('board-card-i1')).toBeInTheDocument();
    });
  });

  it('跨项目拖拽未确认 → 预览模态,确认后携 confirm(§4.3/T22)', async () => {
    const projectView = view({ group_by: 'project' });
    const calls = stubBoard({
      view: projectView,
      columnTargetStatus: {},
      groups: [
        { key: 'p-src', label: 'Src', count: 1, wip: null, data: [card('i1', { project_id: 'p-src' })] },
        { key: 'p-dst', label: 'Dst', count: 1, wip: null, data: [card('i3', { project_id: 'p-dst' })] },
      ],
      moveStatus: 422,
      moveBody: {
        error: {
          code: 'move_confirmation_required', message: 'x',
          details: { preview: { issue_id: 'i1', mapped_fields: [{ field: 'status' }], cleared_fields: [{ field: 'milestone_id' }], kept_fields: [] } },
        },
      },
    });
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-column-p-src');
    dropCard(screen.getByTestId('column-body-p-dst'), 'i1');
    // 预览模态出现(映射/清除清单)。
    expect(await screen.findByTestId('move-preview-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('move-preview-mapped')).toBeInTheDocument();
    expect(screen.getByTestId('move-preview-cleared')).toBeInTheDocument();
    // 确认 → 再次 POST 携 confirm:true。
    fireEvent.click(screen.getByTestId('move-preview-confirm'));
    await waitFor(() => {
      const confirmCall = calls.find(
        (c) => c.method === 'POST' && c.url.includes('/moves') && (c.body as { confirm?: boolean })?.confirm === true,
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
      const create = calls.find((c) => c.method === 'POST' && c.url.includes('/workspaces/ws-1/issues'));
      expect(create).toBeDefined();
      expect(create?.body).toMatchObject({ title: '新卡片', status_id: 'st_todo' });
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
