/**
 * BoardPage 组件测试(kanban.md §4.1/§4.2,README §6.12 异常态)。
 * fetch 桩驱动:列骨架渲染、视图切换、分组切换、配置草稿保存条、WIP 徽章、
 * 空态(无视图 → 主操作)。
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import type { RecordedCall } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import { BoardPage, parseViewDraft } from '../BoardPage';
import type { View } from '../types';

const workspaceContextState = vi.hoisted(() => ({
  workspace: {
    id: 'ws-1',
    name: 'Team',
    slug: 'team',
    logo_url: null,
    timezone: 'UTC',
    settings: {},
    my_role: 'owner' as const,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  },
}));

vi.mock('../../../workspace/WorkspaceProvider', () => ({
  useOptionalWorkspace: () => ({
    status: 'ready',
    workspace: workspaceContextState.workspace,
    error: null,
    isAdmin: true,
    isOwner: true,
    refresh: vi.fn(async () => undefined),
    patch: vi.fn(async () => workspaceContextState.workspace),
  }),
}));

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

/** L251 观众名册桩:subject(用户 id)→ 成员显示名映射来源。 */
const VIEWER_ROSTER = [
  {
    id: 'mem-1',
    member_type: 'human',
    role: 'owner',
    status: 'active',
    display_name: 'Owner',
    joined_at: null,
    profile: { id: 'usr-owner', full_name: 'Owner', email: 'owner@acme.com', avatar_url: null },
  },
  {
    id: 'mem-2',
    member_type: 'human',
    role: 'member',
    status: 'active',
    display_name: 'Alice',
    joined_at: null,
    profile: { id: 'usr-2', full_name: 'Alice', email: 'alice@acme.com', avatar_url: null },
  },
];

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
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
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
      return fakeResponse({
        status: 201,
        body: {
          data: makeView({ id: 'view-copy', name: 'Sprint Board (copy)', is_default: false }),
        },
      });
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
    if (method === 'GET' && url.includes('/members')) {
      return fakeResponse({ body: { data: VIEWER_ROSTER, next_cursor: null } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

/** L92 URL 断言探针:记录当前 search(含 ?draft=)。 */
let latestSearch = '';
function BoardSearchProbe(): null {
  latestSearch = useLocation().search;
  return null;
}

describe('BoardPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    latestSearch = '';
    workspaceContextState.workspace = {
      id: 'ws-1',
      name: 'Team',
      slug: 'team',
      logo_url: null,
      timezone: 'UTC',
      settings: {},
      my_role: 'owner',
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    };
  });
  afterEach(() => vi.unstubAllGlobals());

  it('uses the routed WorkspaceProvider workspace for views, quick create and realtime', async () => {
    workspaceContextState.workspace = {
      id: 'ws-2',
      name: 'Second',
      slug: 'second',
      logo_url: null,
      timezone: 'UTC',
      settings: {},
      my_role: 'owner',
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    };
    const secondView = makeView({
      id: 'view-second',
      workspace_id: 'ws-2',
      name: 'Second board',
    });
    const firstView = makeView({ id: 'view-first', name: 'First board' });
    const calls: Array<{ url: string; method: string }> = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      if (url.includes('/users/me')) {
        return fakeResponse({
          body: {
            data: {
              ...ME,
              memberships: [
                ME.memberships[0],
                {
                  workspace_id: 'ws-2',
                  workspace_name: 'Second',
                  workspace_slug: 'second',
                  role: 'owner',
                  status: 'active',
                  joined_at: null,
                },
              ],
            },
          },
        });
      }
      if (method === 'GET' && url.includes('/workspaces/ws-2/views')) {
        return fakeResponse({ body: { data: [secondView], next_cursor: null } });
      }
      if (method === 'GET' && url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({ body: { data: [firstView], next_cursor: null } });
      }
      if (method === 'GET' && url.includes('/views/') && url.includes('/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: { todo: 'st-todo' },
            groups: [],
            next_cursor: null,
          },
        });
      }
      if (method === 'POST' && url.includes('/views/view-second/issues')) {
        return fakeResponse({
          status: 201,
          body: { data: { id: 'iss-created', title: 'Created in second' } },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);
    const realtimeClient = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      onFrame: vi.fn(() => () => undefined),
      onState: vi.fn(() => () => undefined),
    };

    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: realtimeClient as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/w/second/board' },
    );

    await screen.findByText('Second board');
    await screen.findByTestId('board-columns');
    expect(calls.some((call) => call.url.includes('/workspaces/ws-2/views'))).toBe(true);
    expect(calls.some((call) => call.url.includes('/workspaces/ws-1/views'))).toBe(false);
    expect(realtimeClient.subscribe).toHaveBeenCalledWith('workspace:ws-2:issues');

    const input = screen.getByTestId('quick-add-todo');
    fireEvent.change(input, { target: { value: 'Created in second' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => {
      expect(
        calls.some(
          (call) => call.method === 'POST' && call.url.includes('/views/view-second/issues'),
        ),
      ).toBe(true);
    });
  });

  it('keeps list-view issue deep links under the routed workspace slug', async () => {
    workspaceContextState.workspace = {
      id: 'ws-2',
      name: 'Second',
      slug: 'second',
      logo_url: null,
      timezone: 'UTC',
      settings: {},
      my_role: 'owner',
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    };
    const secondView = makeView({
      id: 'view-second',
      workspace_id: 'ws-2',
      name: 'Second list',
      layout: 'list',
      can_write: false,
    });
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/ws-2/views')) {
        return fakeResponse({ body: { data: [secondView], next_cursor: null } });
      }
      if (url.includes('/views/view-second/issues')) {
        return fakeResponse({
          body: {
            layout: 'list',
            group_by: 'state_category',
            column_target_status: { todo: 'st-todo' },
            groups: [
              {
                key: 'todo',
                label: 'Todo',
                count: 1,
                wip: null,
                data: [
                  {
                    id: 'iss-second',
                    identifier: 'SECOND-1',
                    title: 'Second list issue',
                    state_category: 'todo',
                    status: { id: 'st-todo', name: 'Todo', category: 'todo' },
                    status_id: 'st-todo',
                    priority: 'high',
                    assignee: null,
                    assignee_id: null,
                    project_id: null,
                    position: 1,
                    version: 1,
                    updated_at: '2026-07-26T10:00:00Z',
                  },
                ],
              },
            ],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(
      <Routes>
        <Route path="/w/:workspaceSlug/board" element={<BoardPage />} />
        <Route
          path="/w/:workspaceSlug/issues/:issueId"
          element={<div data-testid="workspace-issue-detail" />}
        />
      </Routes>,
      { route: '/w/second/board' },
    );

    const titleButtons = await screen.findAllByRole('button', { name: 'Second list issue' });
    fireEvent.click(titleButtons[0]!);
    expect(await screen.findByTestId('workspace-issue-detail')).toBeInTheDocument();
  });

  it('默认视图渲染 7 个状态类别列 + 视图切换器', async () => {
    stubFetchByRoute();
    renderWithProviders(<BoardPage />, { route: '/board' });

    await screen.findByTestId('board-columns');
    for (const key of [
      'backlog',
      'todo',
      'in_progress',
      'in_review',
      'blocked',
      'done',
      'cancelled',
    ]) {
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
      expect(JSON.parse(String(post?.init?.body))).toMatchObject({
        name: 'My Board',
        layout: 'board',
        visibility: 'private',
      });
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

  it('L543:视图 ⋯「导出本视图」打开范围预选 view 的导出对话框(import-export.md §4.1)', async () => {
    stubFetchByRoute();
    renderWithProviders(<BoardPage />, { route: '/board' });
    await screen.findByTestId('board-columns');

    fireEvent.click(screen.getByTestId('view-menu-view-1'));
    fireEvent.click(screen.getByTestId('view-export-view-1'));
    const dialog = await screen.findByRole('dialog', { name: 'Export data' });
    const scopeSelect = within(dialog).getByTestId('export-scope-select') as HTMLSelectElement;
    expect(scopeSelect.value).toBe('view');
  });

  it('改动分组后草稿序列化进 ?draft=,丢弃后清除(L92)', async () => {
    stubFetchByRoute();
    renderWithProviders(
      <>
        <BoardSearchProbe />
        <BoardPage />
      </>,
      { route: '/board' },
    );
    await screen.findByTestId('board-columns');

    fireEvent.change(screen.getByTestId('group-by-select'), { target: { value: 'priority' } });
    await waitFor(() => expect(screen.getByTestId('view-save-bar')).toBeInTheDocument());
    await waitFor(() => {
      const raw = new URLSearchParams(latestSearch).get('draft');
      expect(raw).not.toBeNull();
      expect((JSON.parse(raw as string) as { group_by: string }).group_by).toBe('priority');
    });

    fireEvent.click(screen.getByTestId('view-discard'));
    await waitFor(() => expect(screen.queryByTestId('view-save-bar')).not.toBeInTheDocument());
    await waitFor(() => expect(new URLSearchParams(latestSearch).get('draft')).toBeNull());
  });

  it('深链 ?draft= 恢复未保存草稿(脏态保存条 + 草稿列投影,L92)', async () => {
    stubFetchByRoute();
    const draftJson = JSON.stringify({
      group_by: 'priority',
      sub_group_by: null,
      filters: {},
      sort: [],
      board_settings: {},
    });
    renderWithProviders(<BoardPage />, {
      route: `/board?draft=${encodeURIComponent(draftJson)}`,
    });
    await screen.findByTestId('board-columns');

    expect(screen.getByTestId('view-save-bar')).toBeInTheDocument();
    expect(screen.getByTestId('board-column-urgent')).toBeInTheDocument();
  });

  it('损坏的 ?draft= 回落视图原值(L92 容错)', async () => {
    stubFetchByRoute();
    renderWithProviders(<BoardPage />, { route: '/board?draft=%7Bnot-json' });
    await screen.findByTestId('board-columns');

    expect(screen.queryByTestId('view-save-bar')).not.toBeInTheDocument();
    expect(screen.getByTestId('board-column-todo')).toBeInTheDocument();
  });

  it('view.presence 帧渲染同看板观众簇与计数,异视图帧忽略(L251)', async () => {
    stubFetchByRoute();
    const rt = makeEmittingRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/board' },
    );
    await screen.findByTestId('board-columns');
    expect(screen.queryByTestId('board-view-presence')).toBeNull();

    // 其他视图的 presence 帧不渲染本看板观众簇。
    act(() => {
      rt.emit({
        channel: 'view:view-1',
        event: 'view.presence',
        payload: { view_id: 'view-other', online: 3, members: ['usr-2'] },
      });
    });
    expect(screen.queryByTestId('board-view-presence')).toBeNull();

    act(() => {
      rt.emit({
        channel: 'view:view-1',
        event: 'view.presence',
        payload: {
          view_id: 'view-1',
          online: 2,
          subject: 'usr-2',
          joined: true,
          members: ['usr-owner', 'usr-2'],
        },
      });
    });
    const chip = await screen.findByTestId('board-view-presence');
    expect(chip.textContent).toContain('2 viewing');
    // 名册惰性加载完成后,subject(用户 id)映射为成员显示名。
    await waitFor(() => expect(chip.getAttribute('title')).toBe('Owner, Alice'));
  });

  it('view.presence online=0 不渲染观众簇(L251)', async () => {
    stubFetchByRoute();
    const rt = makeEmittingRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/board' },
    );
    await screen.findByTestId('board-columns');

    act(() => {
      rt.emit({
        channel: 'view:view-1',
        event: 'view.presence',
        payload: { view_id: 'view-1', online: 0, subject: 'usr-2', joined: false, members: [] },
      });
    });
    expect(screen.queryByTestId('board-view-presence')).toBeNull();
  });
});

interface EmitFrame {
  channel: string;
  event?: string;
  payload?: unknown;
}

/** 可主动发帧的 realtime 测试替身(BoardPage 需要 onFrame + onState)。 */
function makeEmittingRealtime() {
  const handlers: Array<(frame: EmitFrame) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((handler: (frame: EmitFrame) => void) => {
      handlers.push(handler);
      return (): void => {
        const index = handlers.indexOf(handler);
        if (index >= 0) handlers.splice(index, 1);
      };
    }),
    onState: vi.fn(() => () => undefined),
  };
  return {
    client,
    emit: (frame: EmitFrame): void => {
      for (const handler of [...handlers]) handler(frame);
    },
  };
}

describe('parseViewDraft(L92 URL 草稿反序列化)', () => {
  const validDraft = {
    group_by: 'priority',
    sub_group_by: null,
    filters: {},
    sort: [],
    board_settings: {},
  };

  it('null 或空串返回 null', () => {
    expect(parseViewDraft(null)).toBeNull();
    expect(parseViewDraft('')).toBeNull();
  });

  it('非法 JSON 返回 null', () => {
    expect(parseViewDraft('{not-json')).toBeNull();
  });

  it('非对象顶层值(数字/数组/null)返回 null', () => {
    expect(parseViewDraft('5')).toBeNull();
    expect(parseViewDraft('[1,2]')).toBeNull();
    expect(parseViewDraft('null')).toBeNull();
  });

  it('group_by 非字符串且非 null 返回 null', () => {
    expect(parseViewDraft(JSON.stringify({ ...validDraft, group_by: 5 }))).toBeNull();
  });

  it('sub_group_by 非字符串且非 null 返回 null', () => {
    expect(parseViewDraft(JSON.stringify({ ...validDraft, sub_group_by: true }))).toBeNull();
  });

  it('filters 缺失/为 null/为数组均返回 null', () => {
    const { filters: _omit, ...withoutFilters } = validDraft;
    expect(parseViewDraft(JSON.stringify(withoutFilters))).toBeNull();
    expect(parseViewDraft(JSON.stringify({ ...validDraft, filters: null }))).toBeNull();
    expect(parseViewDraft(JSON.stringify({ ...validDraft, filters: [] }))).toBeNull();
  });

  it('sort 非数组返回 null', () => {
    expect(parseViewDraft(JSON.stringify({ ...validDraft, sort: 'created_at' }))).toBeNull();
  });

  it('board_settings 为数组返回 null', () => {
    expect(parseViewDraft(JSON.stringify({ ...validDraft, board_settings: [] }))).toBeNull();
  });

  it('合法草稿原样返回', () => {
    expect(parseViewDraft(JSON.stringify(validDraft))).toEqual(validDraft);
  });
});
