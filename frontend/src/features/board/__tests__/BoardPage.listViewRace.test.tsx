/**
 * 验收必修 1 回归:list 布局视图必须经自身投影请求收敛到正确数据;切换视图后,
 * 旧视图的在途投影响应一律丢弃(loadSeq + 视图 id 双重校验),不得覆盖新视图。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
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

function card(id: string, title: string, category = 'todo'): BoardCard {
  return {
    id,
    identifier: `WEB-${id}`,
    title,
    state_category: category,
    status: { id: `st_${category}`, name: category, category },
    status_id: `st_${category}`,
    priority: 'high',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position: 1,
    version: 1,
    updated_at: '2026-07-26T10:00:00Z',
  };
}

function view(overrides: Partial<View> = {}): View {
  return {
    id: 'v1', workspace_id: 'ws-1', project_id: null, owner_member_id: 'm1', name: 'Board',
    layout: 'board', visibility: 'private', filters: {}, group_by: null, sub_group_by: null,
    sort: [], display_fields: [], board_settings: {}, position: 1, is_default: true,
    created_at: '', updated_at: '2026-07-26T00:00:00Z', can_write: true, ...overrides,
  };
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolveFn: (value: T) => void = () => undefined;
  const promise = new Promise<T>((resolve) => {
    resolveFn = resolve;
  });
  return { promise, resolve: resolveFn };
}

/** 带路由声明的渲染:useParams 需要 <Route> 匹配,切换视图走同路由参数变更(不重挂载)。 */
function renderBoardAt(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<BoardPage />} />
      <Route path="/views/:viewId" element={<BoardPage />} />
    </Routes>,
    { route },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('list 布局投影加载(验收必修 1)', () => {
  it('list 视图经 /views/{id}/issues 收敛渲染真实行(不再是占位空态)', async () => {
    const listView = view({ id: 'vl', layout: 'list', name: 'List' });
    const issuesCalls: string[] = [];
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/views/vl/issues')) {
        issuesCalls.push(url);
        return fakeResponse({
          body: {
            layout: 'list',
            group_by: 'state_category',
            column_target_status: { todo: 'st_todo' },
            groups: [{ key: 'todo', label: 'Todo', count: 1, wip: null, data: [card('n1', '列表行一')] }],
            next_cursor: null,
          },
        });
      }
      if (url.includes('/views')) return fakeResponse({ body: { data: [listView], next_cursor: null } });
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);

    renderBoardAt('/');
    await screen.findByTestId('board-list-view', {}, { timeout: 5000 });
    expect(await screen.findByTestId('list-row-n1', {}, { timeout: 5000 })).toBeTruthy();
    expect(issuesCalls.length).toBeGreaterThan(0);
  });

  it('视图切换后旧视图在途响应丢弃,不覆盖新视图数据', async () => {
    const boardView = view({ id: 'v1', layout: 'board', name: 'Old' });
    const listView = view({ id: 'v2', layout: 'list', name: 'New', is_default: false });
    // 旧视图投影可延迟解析,模拟切换时仍在途的分页请求。
    const oldGate = deferred<Response>();
    const issuesCalls: string[] = [];
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/views/v1/issues')) {
        issuesCalls.push(url);
        return oldGate.promise;
      }
      if (url.includes('/views/v2/issues')) {
        issuesCalls.push(url);
        return fakeResponse({
          body: {
            layout: 'list',
            group_by: 'state_category',
            column_target_status: { todo: 'st_todo' },
            groups: [{ key: 'todo', label: 'Todo', count: 1, wip: null, data: [card('n1', '新视图行')] }],
            next_cursor: null,
          },
        });
      }
      if (url.includes('/views')) {
        return fakeResponse({ body: { data: [boardView, listView], next_cursor: null } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);

    // 初始位于 /views/v1:默认选中 v1(board),在途加载挂起于 oldGate。
    renderBoardAt('/views/v1');
    await waitFor(() => expect(issuesCalls.some((u) => u.includes('/views/v1/issues'))).toBe(true));

    // 同一路由参数变更切换到 v2(list 视图),BoardPage 实例不重挂载。
    fireEvent.click(await screen.findByTestId('view-entry-v2'));
    expect(await screen.findByTestId('list-row-n1', {}, { timeout: 5000 })).toBeTruthy();

    // 此刻才让旧视图响应到达:不得覆盖新视图数据。
    oldGate.resolve(
      fakeResponse({
        body: {
          layout: 'board',
          group_by: 'state_category',
          column_target_status: { todo: 'st_todo' },
          groups: [{ key: 'todo', label: 'Todo', count: 1, wip: null, data: [card('o1', '旧视图卡')] }],
          next_cursor: null,
        },
      }),
    );
    // 等待一个异步周期,确认旧数据不出现、新数据仍在。
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText('旧视图卡')).toBeNull();
    expect(screen.getByTestId('list-row-n1')).toBeTruthy();
  });
});
