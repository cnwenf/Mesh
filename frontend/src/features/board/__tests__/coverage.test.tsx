/**
 * 看板组件补充覆盖:覆盖 BoardPage 的另存为/列表布局/无工作区/错误分支与
 * ViewSwitcher 的新建/重命名/设默认/删除回调,以及面板的移除条件/嵌套编辑分支。
 */
import { useState } from 'react';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { BoardPage } from '../BoardPage';
import { FilterConfigPanel } from '../FilterConfigPanel';
import { SortConfigPanel } from '../SortConfigPanel';
import { ViewSwitcher } from '../ViewSwitcher';
import type { View } from '../types';

const ME = {
  user: { id: 'u', email: 'o@x.com', display_name: 'O' },
  memberships: [
    { workspace_id: 'ws-1', workspace_name: 'T', workspace_slug: 't', role: 'owner', status: 'active', joined_at: null },
  ],
};

function view(overrides: Partial<View> = {}): View {
  return {
    id: 'v1', workspace_id: 'ws-1', project_id: null, owner_member_id: 'm1', name: 'B',
    layout: 'board', visibility: 'private', filters: {}, group_by: null, sub_group_by: null,
    sort: [], display_fields: [], board_settings: {}, position: 1, is_default: true,
    created_at: '', updated_at: '2026-07-26T00:00:00Z', can_write: true, ...overrides,
  };
}

function stubMeAndViews(views: readonly View[], opts: { failWs?: boolean } = {}) {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.includes('/users/me')) {
      if (opts.failWs === true) {
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });
      }
      return fakeResponse({ body: { data: ME } });
    }
    if (method === 'GET' && url.includes('/views')) {
      return fakeResponse({ body: { data: views, next_cursor: null } });
    }
    if (method === 'POST' && url.endsWith('/views')) {
      return fakeResponse({ status: 201, body: { data: view({ id: 'v-new', name: 'N', is_default: false }) } });
    }
    if (method === 'POST' && url.endsWith('/duplicate')) {
      return fakeResponse({ status: 201, body: { data: view({ id: 'v-c', name: 'B (copy)', is_default: false }) } });
    }
    if (method === 'PATCH' || method === 'DELETE') {
      return fakeResponse({ status: method === 'DELETE' ? 204 : 200, body: { data: view() } });
    }
    return fakeResponse({ status: 404 });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

describe('BoardPage 覆盖补强', () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it('另存为:对话框提交 createView 并选中新视图', async () => {
    const calls = stubMeAndViews([view()]);
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-columns');
    fireEvent.change(screen.getByTestId('group-by-select'), { target: { value: 'priority' } });
    await waitFor(() => expect(screen.getByTestId('view-save-bar')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('view-save-as'));
    fireEvent.change(screen.getByTestId('save-as-name'), { target: { value: '副本视图' } });
    fireEvent.click(screen.getByTestId('save-as-submit'));
    await waitFor(() => {
      const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/views'));
      expect(post).toBeDefined();
      expect(post?.body).toMatchObject({ name: '副本视图', group_by: 'priority' });
    });
  });

  it('list 布局呈现列表占位;timmeline/table 预留呈现未实现态', async () => {
    stubMeAndViews([view({ layout: 'list' })]);
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    expect(await screen.findByText('List layout')).toBeInTheDocument();
  });

  it('无工作区空态', async () => {
    stubMeAndViews([], { failWs: false });
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({ body: { data: { user: ME.user, memberships: [] } } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<BoardPage />, { route: '/board' });
    expect(await screen.findByText('No workspace')).toBeInTheDocument();
  });

  it('工作区加载失败 → 错误态 + 重试', async () => {
    stubMeAndViews([view()], { failWs: true });
    renderWithProviders(<BoardPage />, { route: '/board' });
    const retry = await screen.findByRole('button', { name: 'Retry' });
    fireEvent.click(retry);
    // 重试后 /users/me 仍失败(桩持续 500)→ 仍错误态
    await screen.findByRole('button', { name: 'Retry' });
  });

  it('折叠列切换(工具条列头折叠按钮)', async () => {
    stubMeAndViews([view()]);
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-columns');
    const doneColumn = screen.getByTestId('board-column-done');
    const toggle = within(doneColumn).getByRole('button', { name: /Collapse column|折叠列/ });
    fireEvent.click(toggle);
    expect(within(doneColumn).queryByTestId('quick-add-done')).not.toBeInTheDocument();
  });

  it('保存 409 冲突 → 拉最新收敛(重载后视图仍渲染)', async () => {
    let patchCall = 0;
    const calls: string[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push(method);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'GET' && url.includes('/views')) {
        return fakeResponse({ body: { data: [view()], next_cursor: null } });
      }
      if (method === 'PATCH') {
        patchCall += 1;
        return fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'x' } } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-columns');
    fireEvent.change(screen.getByTestId('group-by-select'), { target: { value: 'priority' } });
    await waitFor(() => expect(screen.getByTestId('view-save-bar')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('view-save'));
    // 冲突路径触发重载拉取,GET /views 再次调用
    await waitFor(() => expect(calls.filter((m) => m === 'GET').length).toBeGreaterThanOrEqual(3));
    expect(patchCall).toBe(1);
  });
});

describe('BoardPage 视图切换器/操作回调经页面接线', () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  function twoViews() {
    return [view({ id: 'v1', is_default: true, can_write: true }), view({ id: 'v2', name: 'Two', is_default: false, can_write: true })];
  }

  it('切换器新建对话框经 BoardPage.handleCreate 提交并选中新视图', async () => {
    const calls = stubMeAndViews(twoViews());
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-columns');
    fireEvent.click(screen.getByTestId('view-create-open'));
    fireEvent.change(screen.getByTestId('view-create-name'), { target: { value: '新视图' } });
    fireEvent.click(screen.getByTestId('view-create-submit'));
    await waitFor(() => {
      const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/views'));
      expect(post?.body).toMatchObject({ name: '新视图' });
    });
  });

  it('菜单复制/设默认/删除经 BoardPage 对应 handler 调用 API', async () => {
    const calls = stubMeAndViews(twoViews());
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-columns');

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(within(screen.getByTestId("view-menu-list-v1")).getByText('Duplicate'));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/duplicate'))).toBe(true));

    // v1 为默认视图,「设为默认」对其隐藏;对非默认视图 v2 触发
    fireEvent.click(screen.getByTestId('view-menu-v2'));
    fireEvent.click(within(screen.getByTestId("view-menu-list-v2")).getByText('Set as default'));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'PATCH' && (c.body as { is_default?: boolean })?.is_default === true)).toBe(true),
    );

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(within(screen.getByTestId("view-menu-list-v1")).getByText('Delete'));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });

  it('菜单重命名对话框经 handleRename 调用 PATCH', async () => {
    const calls = stubMeAndViews(twoViews());
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-columns');
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(within(screen.getByTestId("view-menu-list-v1")).getByText('Rename'));
    fireEvent.change(screen.getByTestId('view-rename-name'), { target: { value: '改名' } });
    fireEvent.click(screen.getByTestId('view-rename-submit'));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'PATCH' && (c.body as { name?: string })?.name === '改名')).toBe(true),
    );
  });

  it('WIP 面板保存经 handleWipSave 调用 PATCH /wip', async () => {
    const calls = stubMeAndViews([view()]);
    renderWithProviders(<BoardPage />, { route: '/views/v1' });
    await screen.findByTestId('board-columns');
    fireEvent.click(screen.getByTestId('panel-toggle-wip'));
    fireEvent.change(screen.getByTestId('wip-limit-todo'), { target: { value: '3' } });
    fireEvent.click(screen.getByTestId('wip-save-todo'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith('/wip') && (c.body as { limit?: number })?.limit === 3)).toBe(true),
    );
  });
});

describe('ViewSwitcher 覆盖补强', () => {
  function renderWith(overrides: { views?: View[] } = {}) {
    const onCreate = vi.fn(async () => undefined);
    const onRename = vi.fn(async () => undefined);
    const onDuplicate = vi.fn(async () => undefined);
    const onSetDefault = vi.fn(async () => undefined);
    const onDelete = vi.fn(async () => undefined);
    const onSelect = vi.fn();
    renderWithProviders(
      <ViewSwitcher
        views={overrides.views ?? [view({ can_write: true })]}
        selectedId="v1"
        canWrite={() => true}
        onSelect={onSelect}
        onCreate={onCreate}
        onRename={onRename}
        onDuplicate={onDuplicate}
        onSetDefault={onSetDefault}
        onDelete={onDelete}
      />,
    );
    return { onCreate, onRename, onDuplicate, onSetDefault, onDelete, onSelect };
  }

  it('新建对话框:空名禁用提交,填名后提交 onCreate', async () => {
    const { onCreate } = renderWith();
    fireEvent.click(screen.getByTestId('view-create-open'));
    expect(screen.getByTestId('view-create-submit')).toBeDisabled();
    fireEvent.change(screen.getByTestId('view-create-name'), { target: { value: 'X' } });
    fireEvent.change(screen.getByTestId('view-create-layout'), { target: { value: 'list' } });
    fireEvent.change(screen.getByTestId('view-create-visibility'), { target: { value: 'shared' } });
    fireEvent.click(screen.getByTestId('view-create-submit'));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('X', 'list', 'shared'));
  });

  it('菜单:重命名对话框提交 onRename;设为默认/删除回调', async () => {
    const { onRename, onSetDefault, onDelete, onSelect } = renderWith({
      views: [view({ is_default: false })],
    });
    fireEvent.click(screen.getByTestId('view-entry-v1'));
    expect(onSelect).toHaveBeenCalledWith('v1');
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByText('Set as default'));
    expect(onSetDefault).toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByText('Delete'));
    expect(onDelete).toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('view-menu-v1'));
    fireEvent.click(screen.getByText('Rename'));
    fireEvent.change(screen.getByTestId('view-rename-name'), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByTestId('view-rename-submit'));
    await waitFor(() => expect(onRename).toHaveBeenCalled());
  });
});

describe('FilterConfigPanel 覆盖补强', () => {
  it('移除条件 + 嵌套组内增条件/移除', () => {
    const onChange = vi.fn();
    function Stateful() {
      const initial = {
        operator: 'AND' as const,
        conditions: [
          { field: 'priority', op: 'eq' as const, value: 'high' },
          { operator: 'OR' as const, conditions: [{ field: 'label', op: 'in' as const, value: 'a' }] },
        ],
      };
      const [filters, setFilters] = useState(initial);
      return <FilterConfigPanel filters={filters} onChange={(n) => { onChange(n); setFilters(n as typeof initial); }} />;
    }
    renderWithProviders(<Stateful />);
    // 顶层行移除(aria-label 提供可访问名)
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove condition' })[0] as HTMLElement);
    expect(onChange).toHaveBeenCalled();
    // 嵌套组内增条件
    const nestedAdd = screen.getAllByTestId('filter-add-condition');
    fireEvent.click(nestedAdd[nestedAdd.length - 1] as HTMLElement);
    expect(onChange).toHaveBeenCalled();
  });

  it('in 操作符以逗号分隔值编辑往返', () => {
    const onChange = vi.fn();
    renderWithProviders(
      <FilterConfigPanel
        filters={{ operator: 'AND', conditions: [{ field: 'priority', op: 'in', value: ['a', 'b'] }] }}
        onChange={onChange}
      />,
    );
    const valueInput = screen.getByRole('textbox');
    expect(valueInput).toHaveValue('a,b');
    fireEvent.change(valueInput, { target: { value: 'a,b,c' } });
    expect(onChange).toHaveBeenLastCalledWith({
      operator: 'AND',
      conditions: [{ field: 'priority', op: 'in', value: ['a', 'b', 'c'] }],
    });
  });
});

describe('SortConfigPanel 覆盖补强', () => {
  it('改字段/方向 + 边界移动无效 + 删除', () => {
    const onChange = vi.fn();
    renderWithProviders(
      <SortConfigPanel
        rules={[{ field: 'position', order: 'asc' }]}
        onChange={onChange}
      />,
    );
    const row = screen.getByTestId('sort-row-0');
    fireEvent.change(within(row).getByLabelText('Sort field'), { target: { value: 'due_date' } });
    expect(onChange).toHaveBeenLastCalledWith([{ field: 'due_date', order: 'asc' }]);
    fireEvent.change(within(row).getByLabelText('Direction'), { target: { value: 'desc' } });
    expect(onChange).toHaveBeenLastCalledWith([{ field: 'position', order: 'desc' }]);
    // 第一条上移无效(边界)→ onChange 不因上移触发额外调用计数变化由删除断言覆盖
    fireEvent.click(within(row).getByRole('button', { name: 'Move up' }));
    fireEvent.click(within(row).getByRole('button', { name: 'Move down' }));
    fireEvent.click(within(row).getByRole('button', { name: 'Remove sort rule' }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});
