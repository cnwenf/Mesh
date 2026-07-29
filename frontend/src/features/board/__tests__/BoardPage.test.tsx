/**
 * BoardPage 组件测试(kanban.md §4.1/§4.2,README §6.12 异常态)。
 * fetch 桩驱动:列骨架渲染、视图切换、分组切换、配置草稿保存条、WIP 徽章、
 * 空态(无视图 → 主操作)。
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import type { RecordedCall } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { BoardPage } from '../BoardPage';
import type { View } from '../types';

const ME = {
  user: { id: 'usr-owner', email: 'owner@acme.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'Team',
      workspace_slug: 'team',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

function makeView(overrides: Partial<View> = {}): View {
  return {
    id: 'view-1',
    workspace_id: 'ws-1',
    project_id: null,
    owner_member_id: 'mem-1',
    name: 'Sprint Board',
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
    created_at: '2026-07-26T00:00:00Z',
    updated_at: '2026-07-26T00:00:00Z',
    can_write: true,
    ...overrides,
  };
}

interface StubOptions {
  readonly views?: readonly View[];
  readonly failListOnce?: boolean;
}

function stubFetchByRoute(options: StubOptions = {}): RecordedCall[] {
  const views = options.views ?? [makeView()];
  const calls: RecordedCall[] = [];
  let listFailed = false;
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, init });
    if (url.includes('/api/v1/users/me')) {
      return fakeResponse({ body: { data: ME } });
    }
    if (method === 'GET' && url.includes('/issues')) {
      return fakeResponse({
        body: {
          layout: 'board',
          group_by: views[0]?.group_by ?? 'state_category',
          column_target_status: {},
          groups: [],
          next_cursor: null,
        },
      });
    }
    if (method === 'GET' && url.includes('/views')) {
      if (options.failListOnce === true && !listFailed) {
        listFailed = true;
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });
      }
      return fakeResponse({ body: { data: views, next_cursor: null } });
    }
    if (method === 'POST' && url.endsWith('/views')) {
      const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
      return fakeResponse({
        status: 201,
        body: { data: makeView({ id: 'view-new', name: String(body.name), is_default: false }) },
      });
    }
    if (method === 'POST' && url.endsWith('/duplicate')) {
      return fakeResponse({ status: 201, body: { data: makeView({ id: 'view-copy', name: 'Sprint Board (copy)', is_default: false }) } });
    }
    if (method === 'PATCH' && url.endsWith('/wip')) {
      return fakeResponse({ body: { data: makeView() } });
    }
    if (method === 'PATCH') {
      return fakeResponse({ body: { data: makeView() } });
    }
    if (method === 'DELETE') {
      return fakeResponse({ status: 204 });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

describe('BoardPage', () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it('默认视图渲染 7 个状态类别列 + 视图切换器', async () => {
    stubFetchByRoute();
    renderWithProviders(<BoardPage />, { route: '/board' });

    await screen.findByTestId('board-columns');
    for (const key of ['backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled']) {
      expect(screen.getByTestId(`board-column-${key}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId('board-title')).toHaveTextContent('Sprint Board');
    expect(screen.getByTestId('view-entry-view-1')).toBeInTheDocument();
  });

  it('group_by=priority 时渲染 5 档优先级列', async () => {
    stubFetchByRoute({ views: [makeView({ group_by: 'priority' })] });
    renderWithProviders(<BoardPage />, { route: '/board' });

    await screen.findByTestId('board-columns');
    for (const key of ['urgent', 'high', 'medium', 'low', 'none']) {
      expect(screen.getByTestId(`board-column-${key}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId('board-column-todo')).not.toBeInTheDocument();
  });

  it('切换分组下拉后呈现保存条,丢弃后消失(§4.2 保存/另存/丢弃)', async () => {
    stubFetchByRoute();
    renderWithProviders(<BoardPage />, { route: '/board' });
    await screen.findByTestId('board-columns');

    expect(screen.queryByTestId('view-save-bar')).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('group-by-select'), { target: { value: 'priority' } });
    await waitFor(() => expect(screen.getByTestId('view-save-bar')).toBeInTheDocument());
    // 列即时反映草稿:5 档优先级
    expect(screen.getByTestId('board-column-urgent')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('view-discard'));
    await waitFor(() => expect(screen.queryByTestId('view-save-bar')).not.toBeInTheDocument());
    expect(screen.getByTestId('board-column-todo')).toBeInTheDocument();
  });

  it('保存配置经 PATCH /views/{id} 携带 If-Match', async () => {
    const calls = stubFetchByRoute();
    renderWithProviders(<BoardPage />, { route: '/board' });
    await screen.findByTestId('board-columns');

    fireEvent.change(screen.getByTestId('group-by-select'), { target: { value: 'priority' } });
    await waitFor(() => expect(screen.getByTestId('view-save-bar')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('view-save'));

    await waitFor(() => {
      const patch = calls.find(
        (c) => (c.init?.method ?? 'GET') === 'PATCH' && c.url.includes('/api/v1/views/view-1'),
      );
      expect(patch).toBeDefined();
      const headers = patch?.init?.headers as Record<string, string>;
      expect(headers['If-Match']).toBe('2026-07-26T00:00:00Z');
      const body = JSON.parse(String(patch?.init?.body)) as { group_by: string };
      expect(body.group_by).toBe('priority');
    });
  });

  it('WIP 徽章呈现 limit(enforcement 提示),折叠列不渲染列体', async () => {
    stubFetchByRoute({
      views: [
        makeView({
          board_settings: {
            collapsed_columns: ['done'],
            wip: { in_progress: { limit: 5, enforcement: 'block' } },
          },
        }),
      ],
    });
    renderWithProviders(<BoardPage />, { route: '/board' });
    await screen.findByTestId('board-columns');

    const badge = screen.getByTestId('wip-badge-in_progress');
    expect(badge).toHaveTextContent('0/5');

    const doneColumn = screen.getByTestId('board-column-done');
    expect(within(doneColumn).queryByTestId('quick-add-done')).not.toBeInTheDocument();
    expect(within(doneColumn).getByRole('button', { expanded: false })).toBeInTheDocument();
    // 展开后出现快速创建输入框(有写权限时可用)
    fireEvent.click(within(doneColumn).getByRole('button', { expanded: false }));
    expect(within(doneColumn).getByTestId('quick-add-done')).toBeEnabled();
  });

  it('无视图时呈现空态与新建主操作(§6.12 empty;onboarding 四要素 + 视图创建次操作)', async () => {
    stubFetchByRoute({ views: [] });
    renderWithProviders(<BoardPage />, { route: '/board' });
    await waitFor(() => {
      expect(screen.getByText('The board is empty')).toBeInTheDocument();
    });
    // 主操作深链既有 issue 快速创建;视图创建入口仍保留(次操作)
    expect(screen.getByTestId('board-empty-new-issue')).toBeInTheDocument();
    expect(screen.getByTestId('view-create-open')).toBeInTheDocument();
  });

  it('新建视图对话框提交 POST 并选中新视图', async () => {
    const calls = stubFetchByRoute({ views: [] });
    renderWithProviders(<BoardPage />, { route: '/board' });
    await waitFor(() => expect(screen.getByTestId('view-create-open')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('view-create-open'));
    fireEvent.change(screen.getByTestId('view-create-name'), { target: { value: 'My Board' } });
    fireEvent.click(screen.getByTestId('view-create-submit'));

    await waitFor(() => {
      const post = calls.find(
        (c) => (c.init?.method ?? 'GET') === 'POST' && c.url.endsWith('/views'),
      );
      expect(post).toBeDefined();
      expect(JSON.parse(String(post?.init?.body))).toMatchObject({ name: 'My Board', layout: 'board', visibility: 'private' });
    });
  });

  it('列表加载失败呈现错误态并可重试(§6.12 retry)', async () => {
    stubFetchByRoute({ failListOnce: true });
    renderWithProviders(<BoardPage />, { route: '/board' });
    const retry = await screen.findByRole('button', { name: 'Retry' });
    fireEvent.click(retry);
    await screen.findByTestId('board-columns');
  });

  it('视图菜单支持复制/删除', async () => {
    const calls = stubFetchByRoute();
    renderWithProviders(<BoardPage />, { route: '/board' });
    await screen.findByTestId('board-columns');

    fireEvent.click(screen.getByTestId('view-menu-view-1'));
    fireEvent.click(screen.getByText('Duplicate'));
    await waitFor(() => {
      expect(
        calls.some((c) => (c.init?.method ?? 'GET') === 'POST' && c.url.endsWith('/duplicate')),
      ).toBe(true);
    });
  });
});
