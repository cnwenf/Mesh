/**
 * BoardPage 页面级快捷键契约：覆盖加载阶段的安全空操作，以及投影就绪后的
 * 选择、快速创建、状态/负责人变更、详情导航与筛选面板切换。
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { useShortcutRegistry } from '../../../shortcuts';
import { renderWithProviders } from '../../../test-utils/render';
import { BoardPage } from '../BoardPage';
import type { BoardCard } from '../projection';
import type { View } from '../types';

const workspaceContextState = vi.hoisted(() => ({
  status: 'ready',
  workspacePresent: true,
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
  refresh: vi.fn(async () => undefined),
}));

vi.mock('../../../workspace/WorkspaceProvider', () => ({
  useOptionalWorkspace: () => ({
    status: workspaceContextState.status,
    workspace:
      workspaceContextState.status === 'ready' && workspaceContextState.workspacePresent
        ? workspaceContextState.workspace
        : null,
    error: null,
    isAdmin: true,
    isOwner: true,
    refresh: workspaceContextState.refresh,
    patch: vi.fn(async () => workspaceContextState.workspace),
  }),
}));

function makeView(overrides: Partial<View> = {}): View {
  return {
    id: 'view-1',
    workspace_id: 'ws-1',
    project_id: null,
    owner_member_id: 'member-owner',
    name: 'Keyboard board',
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
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    can_write: true,
    ...overrides,
  };
}

function makeCard(id: string, overrides: Partial<BoardCard> = {}): BoardCard {
  return {
    id,
    identifier: `MES-${id}`,
    title: `Card ${id}`,
    state_category: 'todo',
    status: { id: 'status-todo', name: 'Todo', category: 'todo' },
    status_id: 'status-todo',
    priority: 'high',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position: 1,
    version: 1,
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function shortcut(id: string): () => void {
  const run = useShortcutRegistry.getState().shortcuts.find((entry) => entry.id === id)?.run;
  expect(run).toBeTypeOf('function');
  return run as () => void;
}

function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  return <output data-testid="location-probe">{location.pathname + location.search}</output>;
}

describe('BoardPage keyboard actions', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    workspaceContextState.status = 'ready';
    workspaceContextState.workspacePresent = true;
    workspaceContextState.workspace.slug = 'team';
    workspaceContextState.refresh.mockClear();
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  });

  it('keeps every board shortcut safe while workspace context is still loading', async () => {
    workspaceContextState.status = 'loading';

    const rendered = renderWithProviders(<BoardPage />, { route: '/w/team/board' });

    await waitFor(() => {
      expect(useShortcutRegistry.getState().shortcuts).toHaveLength(13);
    });
    expect(screen.getByTestId('board-page')).toBeInTheDocument();

    act(() => {
      shortcut('board.move.up')();
      shortcut('board.new.card')();
      shortcut('board.change.status')();
      shortcut('board.change.assignee')();
      shortcut('board.open.card')();
      shortcut('board.filter')();
    });

    expect(screen.getByTestId('board-page')).toBeInTheDocument();
    rendered.unmount();
    expect(useShortcutRegistry.getState().shortcuts).toHaveLength(0);
  });

  it('uses provider refresh for an errored workspace and renders provider empty states', async () => {
    workspaceContextState.status = 'error';
    const errored = renderWithProviders(<BoardPage />, { route: '/w/team/board' });

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(workspaceContextState.refresh).toHaveBeenCalledOnce();
    errored.unmount();

    workspaceContextState.status = 'not_found';
    const missing = renderWithProviders(<BoardPage />, { route: '/w/team/board' });
    expect(await screen.findByText('No workspace')).toBeInTheDocument();
    missing.unmount();

    workspaceContextState.status = 'ready';
    workspaceContextState.workspacePresent = false;
    renderWithProviders(<BoardPage />, { route: '/w/team/board' });
    expect(await screen.findByText('No workspace')).toBeInTheDocument();
  });

  it('ignores card-only shortcuts on an empty projection and falls back to issue creation', async () => {
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({
          body: {
            data: [makeView({ board_settings: { columns: ['unknown-column'] } })],
            next_cursor: null,
          },
        });
      }
      if (url.includes('/views/view-1/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: {},
            groups: [],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(
      <Routes>
        <Route path="/w/:workspaceSlug/board" element={<BoardPage />} />
        <Route path="/w/:workspaceSlug/issues" element={<div data-testid="issue-create-route" />} />
      </Routes>,
      { route: '/w/team/board' },
    );

    await screen.findByTestId('board-columns');
    act(() => {
      shortcut('board.move.down')();
      shortcut('board.change.status')();
      shortcut('board.change.assignee')();
      shortcut('board.open.card')();
    });
    expect(screen.getByTestId('board-columns')).toBeInTheDocument();

    act(() => shortcut('board.new.card')());
    expect(await screen.findByTestId('issue-create-route')).toBeInTheDocument();
  });

  it('executes selection, quick-create, status, assignee, filter and open actions on real cards', async () => {
    const calls: Array<{ url: string; method: string; body?: unknown }> = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({
        url,
        method,
        body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      });
      if (method === 'GET' && url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({ body: { data: [makeView()], next_cursor: null } });
      }
      if (method === 'GET' && url.includes('/views/view-1/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: {
              todo: 'status-todo',
              in_progress: 'status-progress',
            },
            groups: [
              {
                key: 'todo',
                label: 'Todo',
                count: 1,
                wip: null,
                data: [makeCard('one')],
              },
              {
                key: 'in_progress',
                label: 'In progress',
                count: 1,
                wip: null,
                data: [
                  makeCard('two', {
                    state_category: 'in_progress',
                    status: {
                      id: 'status-progress',
                      name: 'In progress',
                      category: 'in_progress',
                    },
                    status_id: 'status-progress',
                  }),
                ],
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (method === 'POST' && url.endsWith('/views/view-1/moves')) {
        return fakeResponse({
          body: {
            data: makeCard('one', {
              state_category: 'in_progress',
              status: {
                id: 'status-progress',
                name: 'In progress',
                category: 'in_progress',
              },
              status_id: 'status-progress',
              version: 2,
            }),
          },
        });
      }
      if (method === 'GET' && url.includes('/workspaces/ws-1/members')) {
        return fakeResponse({
          body: {
            data: [
              {
                id: 'member-next',
                member_type: 'human',
                role: 'member',
                status: 'active',
                display_name: 'Next owner',
                joined_at: null,
                profile: null,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (method === 'PATCH' && url.endsWith('/issues/one')) {
        return fakeResponse({ body: { data: makeCard('one', { assignee_id: 'member-next' }) } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(
      <Routes>
        <Route path="/w/:workspaceSlug/board" element={<BoardPage />} />
        <Route
          path="/w/:workspaceSlug/issues/:issueId"
          element={<div data-testid="issue-detail-route" />}
        />
      </Routes>,
      { route: '/w/team/board' },
    );

    await screen.findByTestId('board-card-one');
    await waitFor(() => expect(useShortcutRegistry.getState().shortcuts).toHaveLength(13));

    act(() => shortcut('board.move.right')());
    await waitFor(() => expect(screen.getByTestId('board-card-one')).toHaveFocus());

    act(() => shortcut('board.new.card')());
    expect(screen.getByTestId('quick-add-todo')).toHaveFocus();

    act(() => shortcut('board.filter')());
    expect(await screen.findByTestId('filter-config-panel')).toBeInTheDocument();
    act(() => shortcut('board.filter')());
    await waitFor(() =>
      expect(screen.queryByTestId('filter-config-panel')).not.toBeInTheDocument(),
    );

    act(() => shortcut('board.change.status')());
    await waitFor(() => {
      expect(
        calls.some(
          (call) =>
            call.method === 'POST' &&
            call.url.endsWith('/views/view-1/moves') &&
            (call.body as { issue_id?: string; to_group_key?: string }).issue_id === 'one' &&
            (call.body as { issue_id?: string; to_group_key?: string }).to_group_key ===
              'in_progress',
        ),
      ).toBe(true);
    });

    act(() => shortcut('board.change.assignee')());
    await waitFor(() => {
      expect(
        calls.some(
          (call) =>
            call.method === 'PATCH' &&
            call.url.endsWith('/issues/one') &&
            (call.body as { assignee_id?: string }).assignee_id === 'member-next',
        ),
      ).toBe(true);
    });

    act(() => shortcut('board.open.card')());
    expect(await screen.findByTestId('issue-detail-route')).toBeInTheDocument();
  });

  it('percent-encodes the provider slug and selected view id as separate URL segments', async () => {
    workspaceContextState.workspace.slug = 'team/ops %';
    const nextView = makeView({ id: 'view/next ?', name: 'Encoded view', is_default: false });
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({
          body: { data: [makeView(), nextView], next_cursor: null },
        });
      }
      if (url.includes('/views/view-1/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: {},
            groups: [],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(
      <Routes>
        <Route path="/w/:workspaceSlug/board" element={<BoardPage />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>,
      { route: '/w/source/board' },
    );

    await screen.findByTestId('board-columns');
    fireEvent.click(screen.getByTestId('view-entry-view/next ?'));
    expect(await screen.findByTestId('location-probe')).toHaveTextContent(
      '/w/team%2Fops%20%25/views/view%2Fnext%20%3F',
    );
  });

  it('percent-encodes the provider slug and issue id when opening a selected card', async () => {
    workspaceContextState.workspace.slug = 'team/ops %';
    const issueId = 'issue/one ?';
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({ body: { data: [makeView()], next_cursor: null } });
      }
      if (url.includes('/views/view-1/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: { todo: 'status-todo' },
            groups: [
              {
                key: 'todo',
                label: 'Todo',
                count: 1,
                wip: null,
                data: [makeCard(issueId)],
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
        <Route path="*" element={<LocationProbe />} />
      </Routes>,
      { route: '/w/source/board' },
    );

    await screen.findByTestId(`board-card-${issueId}`);
    act(() => shortcut('board.move.right')());
    await waitFor(() => expect(screen.getByTestId(`board-card-${issueId}`)).toHaveFocus());
    act(() => shortcut('board.open.card')());
    expect(await screen.findByTestId('location-probe')).toHaveTextContent(
      '/w/team%2Fops%20%25/issues/issue%2Fone%20%3F',
    );
  });

  it.each([
    { layout: 'board' as const, readyTestId: 'board-columns' },
    { layout: 'list' as const, readyTestId: 'list-empty' },
  ])('retries a failed $layout projection in place', async ({ layout, readyTestId }) => {
    let attempts = 0;
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({ body: { data: [makeView({ layout })], next_cursor: null } });
      }
      if (url.includes('/views/view-1/issues')) {
        attempts += 1;
        if (attempts === 1) {
          return fakeResponse({
            status: 500,
            body: { error: { code: 'internal_error', message: 'projection failed' } },
          });
        }
        return fakeResponse({
          body: {
            layout,
            group_by: 'state_category',
            column_target_status: {},
            groups: [],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(<BoardPage />, { route: '/w/team/board' });

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId(readyTestId)).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it('renders reserved layouts without requesting a projection', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({
          body: { data: [makeView({ layout: 'timeline' })], next_cursor: null },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(<BoardPage />, { route: '/w/team/board' });

    expect(await screen.findByText('Layout not implemented')).toBeInTheDocument();
    expect(calls.some((url) => url.includes('/views/view-1/issues'))).toBe(false);
  });

  it('wires subgroup, filter, sort and save-as dialog callbacks through the page', async () => {
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({ body: { data: [makeView()], next_cursor: null } });
      }
      if (url.includes('/views/view-1/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: {},
            groups: [],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(<BoardPage />, { route: '/w/team/board' });
    await screen.findByTestId('board-columns');

    fireEvent.change(screen.getByTestId('sub-group-by-select'), { target: { value: 'status' } });
    expect(await screen.findByTestId('view-save-bar')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('sub-group-by-select'), { target: { value: '' } });

    fireEvent.click(screen.getByTestId('panel-toggle-filter'));
    fireEvent.click(await screen.findByTestId('filter-add-condition'));
    fireEvent.click(screen.getByTestId('panel-toggle-filter'));
    await waitFor(() =>
      expect(screen.queryByTestId('filter-config-panel')).not.toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId('panel-toggle-sort'));
    fireEvent.click(await screen.findByTestId('sort-add'));

    fireEvent.click(screen.getByTestId('view-save-as'));
    let dialog = await screen.findByRole('dialog', { name: 'Save as new view' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Save as new view' })).not.toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId('view-save-as'));
    dialog = await screen.findByRole('dialog', { name: 'Save as new view' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Save as new view' })).not.toBeInTheDocument(),
    );
  });

  it('wires both empty-board actions to their production destinations', async () => {
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(
      <Routes>
        <Route path="/w/:workspaceSlug/board" element={<BoardPage />} />
        <Route
          path="/w/:workspaceSlug/issues"
          element={<div data-testid="empty-issue-create-route" />}
        />
      </Routes>,
      { route: '/w/team/board' },
    );

    await screen.findByText('The board is empty');
    fireEvent.click(screen.getByTestId('board-empty-create'));
    const dialog = await screen.findByRole('dialog', { name: 'Create view' });
    expect(dialog).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));

    fireEvent.click(screen.getByTestId('board-empty-new-issue'));
    expect(await screen.findByTestId('empty-issue-create-route')).toBeInTheDocument();
  });

  it('refreshes a list projection after a successful inline title edit', async () => {
    let projectionCalls = 0;
    const calls: Array<{ url: string; method: string; body?: unknown }> = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({
        url,
        method,
        body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      });
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({
          body: { data: [makeView({ layout: 'list' })], next_cursor: null },
        });
      }
      if (method === 'GET' && url.includes('/views/view-1/issues')) {
        projectionCalls += 1;
        return fakeResponse({
          body: {
            layout: 'list',
            group_by: 'state_category',
            column_target_status: { todo: 'status-todo' },
            groups: [
              {
                key: 'todo',
                label: 'Todo',
                count: 1,
                wip: null,
                data: [makeCard('one')],
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (method === 'PATCH' && url.endsWith('/issues/one')) {
        return fakeResponse({ body: { data: makeCard('one', { title: 'Renamed card' }) } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(<BoardPage />, { route: '/w/team/board' });

    fireEvent.click(await screen.findByTestId('list-title-one'));
    const input = await screen.findByTestId('list-title-input-one');
    fireEvent.change(input, { target: { value: 'Renamed card' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(
        calls.some(
          (call) =>
            call.method === 'PATCH' &&
            call.url.endsWith('/issues/one') &&
            (call.body as { title?: string }).title === 'Renamed card',
        ),
      ).toBe(true);
      expect(projectionCalls).toBeGreaterThanOrEqual(2);
    });
  });

  it.each([
    {
      groupBy: 'status' as const,
      groupKey: 'status-todo',
      expected: { status_id: 'status-todo' },
    },
    { groupBy: 'priority' as const, groupKey: 'high', expected: { priority: 'high' } },
    { groupBy: 'assignee' as const, groupKey: '__none__', expected: { assignee_id: null } },
    { groupBy: 'project' as const, groupKey: '__none__', expected: { project_id: null } },
  ])('inherits the $groupBy column value during repeated quick create', async (scenario) => {
    const calls: Array<{ method: string; body?: unknown }> = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({
        method,
        body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      });
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({
          body: { data: [makeView({ group_by: scenario.groupBy })], next_cursor: null },
        });
      }
      if (method === 'GET' && url.includes('/views/view-1/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: scenario.groupBy,
            column_target_status: { todo: 'status-todo' },
            groups: [
              {
                key: scenario.groupKey,
                label: scenario.groupKey,
                count: 1,
                wip: null,
                data: [makeCard('one')],
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (method === 'POST' && url.includes('/views/view-1/issues')) {
        const createIndex = calls.filter((call) => call.method === 'POST').length;
        return fakeResponse({
          status: 201,
          body: { data: makeCard(`created-${createIndex}`) },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(<BoardPage />, { route: '/w/team/board' });

    for (const title of ['First card', 'Second card']) {
      const input = await screen.findByTestId(`quick-add-${scenario.groupKey}`);
      fireEvent.change(input, { target: { value: title } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await waitFor(() =>
        expect(calls.filter((call) => call.method === 'POST')).toHaveLength(
          title === 'First card' ? 1 : 2,
        ),
      );
    }

    for (const call of calls.filter((entry) => entry.method === 'POST')) {
      expect(call.body).toMatchObject({ group_key: scenario.groupKey });
    }
  });

  it.each(['empty', 'error'] as const)(
    'keeps single-column status/assignee shortcuts safe when the member roster is $0',
    async (memberMode) => {
      const calls: Array<{ url: string; method: string }> = [];
      vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        calls.push({ url, method });
        if (url.includes('/workspaces/ws-1/views')) {
          return fakeResponse({
            body: { data: [makeView({ group_by: 'status' })], next_cursor: null },
          });
        }
        if (method === 'GET' && url.includes('/views/view-1/issues')) {
          return fakeResponse({
            body: {
              layout: 'board',
              group_by: 'status',
              column_target_status: {},
              groups: [
                {
                  key: 'status-todo',
                  label: 'Todo',
                  count: 1,
                  wip: null,
                  data: [makeCard('one')],
                },
              ],
              next_cursor: null,
            },
          });
        }
        if (method === 'GET' && url.includes('/workspaces/ws-1/members')) {
          if (memberMode === 'error') {
            // 破损的成员项会在页面名册消费路径产生原生 TypeError，验证未知错误兜底。
            return fakeResponse({ body: { data: [null], next_cursor: null } });
          }
          return fakeResponse({ body: { data: [], next_cursor: null } });
        }
        return fakeResponse({ status: 404 });
      }) as typeof fetch);

      renderWithProviders(<BoardPage />, { route: '/w/team/board' });
      await screen.findByTestId('board-card-one');
      act(() => shortcut('board.move.right')());
      await waitFor(() => expect(screen.getByTestId('board-card-one')).toHaveFocus());

      act(() => shortcut('board.change.status')());
      expect(calls.some((call) => call.method === 'POST' && call.url.includes('/moves'))).toBe(
        false,
      );

      act(() => shortcut('board.change.assignee')());
      await waitFor(() => {
        expect(calls.some((call) => call.url.includes('/workspaces/ws-1/members'))).toBe(true);
      });
      expect(calls.some((call) => call.method === 'PATCH')).toBe(false);
      if (memberMode === 'error') {
        expect(
          await screen.findByText('Something went wrong. Please try again.'),
        ).toBeInTheDocument();
      }
    },
  );

  it('falls back to the first view when the URL id and default view are both absent', async () => {
    const first = makeView({ id: 'first', name: 'First fallback', is_default: false });
    const second = makeView({ id: 'second', name: 'Second fallback', is_default: false });
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({ body: { data: [first, second], next_cursor: null } });
      }
      if (url.includes('/views/first/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: {},
            groups: [],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(
      <Routes>
        <Route path="/w/:workspaceSlug/views/:viewId" element={<BoardPage />} />
      </Routes>,
      { route: '/w/team/views/missing' },
    );

    expect(await screen.findByTestId('board-title')).toHaveTextContent('First fallback');
  });

  it('keeps the view switcher usable when rename, duplicate, default and delete APIs fail', async () => {
    const first = makeView({ id: 'first', name: 'First', is_default: true });
    const second = makeView({ id: 'second', name: 'Second', is_default: false });
    const calls: Array<{ url: string; method: string; body?: unknown }> = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({
        url,
        method,
        body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      });
      if (method === 'GET' && url.includes('/workspaces/ws-1/views')) {
        return fakeResponse({ body: { data: [first, second], next_cursor: null } });
      }
      if (method === 'GET' && url.includes('/views/first/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: {},
            groups: [],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'mutation failed' } },
      });
    }) as typeof fetch);

    renderWithProviders(<BoardPage />, { route: '/w/team/board' });
    await screen.findByTestId('board-columns');

    fireEvent.click(screen.getByTestId('view-menu-first'));
    fireEvent.click(within(screen.getByTestId('view-menu-list-first')).getByText('Rename'));
    fireEvent.change(screen.getByTestId('view-rename-name'), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByTestId('view-rename-submit'));
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.method === 'PATCH' &&
            call.url.endsWith('/views/first') &&
            (call.body as { name?: string }).name === 'Renamed',
        ),
      ).toBe(true),
    );

    fireEvent.click(screen.getByTestId('view-menu-first'));
    fireEvent.click(within(screen.getByTestId('view-menu-list-first')).getByText('Duplicate'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/views/first/duplicate'))).toBe(true),
    );

    fireEvent.click(screen.getByTestId('view-menu-second'));
    fireEvent.click(
      within(screen.getByTestId('view-menu-list-second')).getByText('Set as default'),
    );
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.method === 'PATCH' &&
            call.url.endsWith('/views/second') &&
            (call.body as { is_default?: boolean }).is_default === true,
        ),
      ).toBe(true),
    );

    fireEvent.click(screen.getByTestId('view-menu-first'));
    fireEvent.click(within(screen.getByTestId('view-menu-list-first')).getByText('Delete'));
    fireEvent.click(screen.getByTestId('view-delete-confirm'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.method === 'DELETE' && call.url.endsWith('/views/first')),
      ).toBe(true),
    );

    expect(screen.getByTestId('board-columns')).toBeInTheDocument();
  });
});
