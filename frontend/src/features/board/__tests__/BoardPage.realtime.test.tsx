/**
 * 看板实时增量合并接线测试(§3.5/§6.12):订阅工作区 issue 频道 + 视图频道,
 * issue.* 帧单卡合并、view.updated → 整板重拉、重连/重同步 → 「正在重新同步」横幅。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { RealtimeContext } from '../../../shell/AppShell';
import { BoardPage } from '../BoardPage';
import type { CustomFieldDef } from '../../labels/types';
import type { View } from '../types';

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

const CARD = {
  id: 'i1',
  identifier: 'WEB-1',
  title: 'Card',
  state_category: 'todo',
  status: { id: 'st', name: 'Todo', category: 'todo' },
  status_id: 'st',
  priority: 'high',
  assignee: null,
  assignee_id: null,
  project_id: null,
  position: 1,
  version: 1,
  updated_at: '2026-07-26T10:00:00Z',
};

type IssueResponses = Readonly<Record<string, Response | Promise<Response>>>;

function issueIdFromUrl(url: string): string | null {
  const match = /\/api\/v1\/issues\/([^/?]+)/.exec(url);
  return match?.[1] === undefined ? null : decodeURIComponent(match[1]);
}

function missingIssueResponse(): Response {
  return fakeResponse({
    status: 404,
    body: { error: { code: 'not_found', message: 'missing' } },
  });
}

type ProjectionBody = Readonly<Record<string, unknown>>;

function stub(
  selectedView: View = view(),
  issueResponses: IssueResponses = {},
  projectionBodies: readonly ProjectionBody[] = [],
  customFields: readonly CustomFieldDef[] = [],
) {
  const calls: string[] = [];
  let projectionIndex = 0;
  const impl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/custom-fields')) {
      return fakeResponse({ body: { data: customFields, next_cursor: null } });
    }
    const issueId = issueIdFromUrl(url);
    if (issueId !== null) return issueResponses[issueId] ?? missingIssueResponse();
    if (url.includes('/issues')) {
      const projected = projectionBodies[Math.min(projectionIndex, projectionBodies.length - 1)];
      projectionIndex += 1;
      if (projected !== undefined) return fakeResponse({ body: projected });
      return fakeResponse({
        body: {
          layout: 'board',
          group_by: selectedView.group_by ?? 'state_category',
          column_target_status: {},
          groups: [{ key: 'todo', label: 'Todo', count: 1, wip: null, data: [CARD] }],
          next_cursor: null,
        },
      });
    }
    if (url.includes('/views')) {
      return fakeResponse({ body: { data: [selectedView], next_cursor: null } });
    }
    return fakeResponse({ status: 404 });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function stubSwimlanes(issueResponses: IssueResponses = {}) {
  const selectedView = view({ group_by: 'state_category', sub_group_by: 'priority' });
  const calls: string[] = [];
  const impl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    const issueId = issueIdFromUrl(url);
    if (issueId !== null) return issueResponses[issueId] ?? missingIssueResponse();
    if (url.includes('/issues')) {
      return fakeResponse({
        body: {
          layout: 'board',
          group_by: 'state_category',
          sub_group_by: 'priority',
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
                { key: 'todo', count: 1, data: [CARD] },
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
          next_cursor: null,
        },
      });
    }
    if (url.includes('/views')) {
      return fakeResponse({ body: { data: [selectedView], next_cursor: null } });
    }
    return fakeResponse({ status: 404 });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolveValue: (value: T) => void = () => undefined;
  const promise = new Promise<T>((resolve) => {
    resolveValue = resolve;
  });
  return { promise, resolve: resolveValue };
}

type FrameCb = (frame: unknown) => void;
type StateCb = (state: string) => void;

function makeRealtime() {
  let frameCb: FrameCb | null = null;
  let stateCb: StateCb | null = null;
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((cb: FrameCb) => {
      frameCb = cb;
      return () => {};
    }),
    onState: vi.fn((cb: StateCb) => {
      stateCb = cb;
      return () => {};
    }),
  };
  return {
    client,
    emitFrame: (f: unknown) => frameCb?.(f),
    emitState: (s: string) => stateCb?.(s),
  };
}

describe('看板实时增量合并接线', () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it('订阅工作区 issue 频道 + 视图频道', async () => {
    stub();
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-columns');
    expect(rt.client.subscribe).toHaveBeenCalledWith('workspace:ws-1:issues');
    expect(rt.client.subscribe).toHaveBeenCalledWith('view:v1');
  });

  it('issue.* 帧单卡合并(插入新卡)', async () => {
    stub();
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-card-i1');
    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 2,
        event: 'issue.created',
        payload: { issue: { ...CARD, id: 'i2', identifier: 'WEB-2' } },
      });
    });
    expect(await screen.findByTestId('board-card-i2')).toBeInTheDocument();
  });

  it('一维缺失卡仅 GET /issues/{id} 对账并按当前列插入，不整板 refetch', async () => {
    const projectId = 'project-1';
    const entered = {
      ...CARD,
      id: 'i-enter',
      identifier: 'WEB-2',
      workspace_id: 'ws-1',
      project_id: projectId,
      state_category: 'done',
      status: { id: 'st-done', name: 'Done', category: 'done' },
      status_id: 'st-done',
      updated_at: '2026-07-26T11:00:00Z',
    };
    // 固定项目视图还必须通过 project scope，不能只依赖 filters。
    const calls = stub(view({ project_id: projectId }), {
      'i-enter': fakeResponse({ body: { data: entered } }),
    });
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-card-i1');
    const projectionCallsBefore = calls.filter((url) => url.includes('/views/v1/issues')).length;

    act(() => {
      const enteringFrame = {
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 3,
        event: 'issue.updated',
        payload: { id: 'i-enter', updated_at: entered.updated_at },
      };
      rt.emitFrame(enteringFrame);
      // 同一事件可能同时经 workspace/view 频道到达；在途对账必须按 view+id 去重。
      rt.emitFrame({ ...enteringFrame, channel: 'view:v1', seq: 4 });
    });

    expect(await screen.findByTestId('board-card-i-enter')).toBeInTheDocument();
    expect(calls.filter((url) => url.includes('/api/v1/issues/i-enter'))).toHaveLength(1);
    expect(calls.filter((url) => url.includes('/views/v1/issues'))).toHaveLength(
      projectionCallsBefore,
    );
  });

  it('缺失卡对账在 GET 在途时合并最新帧，且不以旧帧覆盖较新的权威响应', async () => {
    const gate = deferred<Response>();
    const calls = stub(view(), { 'i-race': gate.promise });
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-card-i1');

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 3,
        event: 'issue.updated',
        payload: {
          id: 'i-race',
          changes: { title: 'Older frame', state_category: 'todo' },
          updated_at: '2026-07-26T11:00:00Z',
        },
      });
    });
    await waitFor(() =>
      expect(calls.filter((url) => url.includes('/api/v1/issues/i-race'))).toHaveLength(1),
    );

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'view:v1',
        seq: 4,
        event: 'issue.updated',
        payload: {
          id: 'i-race',
          changes: { title: 'Newest frame', state_category: 'done' },
          updated_at: '2026-07-26T12:00:00Z',
        },
      });
    });
    await act(async () => {
      gate.resolve(
        fakeResponse({
          body: {
            data: {
              ...CARD,
              id: 'i-race',
              identifier: 'WEB-9',
              workspace_id: 'ws-1',
              title: 'Newest server state',
              state_category: 'done',
              status: { id: 'st-done', name: 'Done', category: 'done' },
              status_id: 'st-done',
              updated_at: '2026-07-26T12:00:00Z',
            },
          },
        }),
      );
      await gate.promise;
    });

    const card = await screen.findByTestId('board-card-i-race');
    expect(card).toHaveTextContent('Newest server state');
    expect(screen.getByTestId('board-column-done')).toContainElement(card);
    expect(calls.filter((url) => url.includes('/api/v1/issues/i-race'))).toHaveLength(1);
  });

  it('二维缺失卡仅单卡对账并按当前 lane/cell 插入，不整板 refetch', async () => {
    const entered = {
      ...CARD,
      id: 'i-enter',
      identifier: 'WEB-2',
      workspace_id: 'ws-1',
      state_category: 'done',
      status: { id: 'st-done', name: 'Done', category: 'done' },
      status_id: 'st-done',
      priority: 'low',
      updated_at: '2026-07-26T11:00:00Z',
    };
    const calls = stubSwimlanes({
      'i-enter': fakeResponse({ body: { data: entered } }),
    });
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-card-i1');
    const projectionCallsBefore = calls.filter((url) => url.includes('/views/v1/issues')).length;

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 3,
        event: 'issue.project_changed',
        payload: { id: 'i-enter', updated_at: entered.updated_at },
      });
    });

    const enteredCard = await screen.findByTestId('board-card-i-enter');
    expect(screen.getByTestId('board-column-low-done')).toContainElement(enteredCard);
    expect(screen.getByTestId('count-low-done')).toHaveTextContent('1');
    expect(calls.filter((url) => url.includes('/api/v1/issues/i-enter'))).toHaveLength(1);
    expect(calls.filter((url) => url.includes('/views/v1/issues'))).toHaveLength(
      projectionCallsBefore,
    );
  });

  it('单卡 404 或当前 filters 不可见时安全忽略', async () => {
    const filtered = {
      ...CARD,
      id: 'i-filtered',
      identifier: 'WEB-3',
      workspace_id: 'ws-1',
      priority: 'low',
    };
    const selectedView = view({
      filters: {
        operator: 'AND',
        conditions: [{ field: 'priority', op: 'eq', value: 'high' }],
      },
    });
    const calls = stub(selectedView, {
      'i-filtered': fakeResponse({ body: { data: filtered } }),
      'i-gone': missingIssueResponse(),
    });
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-card-i1');

    act(() => {
      for (const id of ['i-filtered', 'i-gone']) {
        rt.emitFrame({
          op: 'event',
          channel: 'workspace:ws-1:issues',
          seq: 4,
          event: 'issue.updated',
          payload: { id },
        });
      }
    });

    await waitFor(() => {
      expect(calls.filter((url) => url.includes('/api/v1/issues/'))).toHaveLength(2);
    });
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByTestId('board-card-i-filtered')).not.toBeInTheDocument();
    expect(screen.queryByTestId('board-card-i-gone')).not.toBeInTheDocument();
    expect(calls.filter((url) => url.includes('/views/v1/issues'))).toHaveLength(1);
  });

  it('切换视图后丢弃旧视图晚到的单卡对账结果', async () => {
    const firstView = view({ id: 'v1', name: 'First' });
    const secondView = view({ id: 'v2', name: 'Second', is_default: false });
    const lateGate = deferred<Response>();
    const calls: string[] = [];
    const secondCard = { ...CARD, id: 'i-v2', identifier: 'WEB-2', title: 'Second card' };
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/api/v1/issues/i-late')) return lateGate.promise;
      if (url.includes('/views/v1/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: {},
            groups: [{ key: 'todo', label: 'Todo', count: 1, wip: null, data: [CARD] }],
            next_cursor: null,
          },
        });
      }
      if (url.includes('/views/v2/issues')) {
        return fakeResponse({
          body: {
            layout: 'board',
            group_by: 'state_category',
            column_target_status: {},
            groups: [{ key: 'todo', label: 'Todo', count: 1, wip: null, data: [secondCard] }],
            next_cursor: null,
          },
        });
      }
      if (url.includes('/views')) {
        return fakeResponse({ body: { data: [firstView, secondView], next_cursor: null } });
      }
      return missingIssueResponse();
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <Routes>
          <Route path="/views/:viewId" element={<BoardPage />} />
        </Routes>
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-card-i1');

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 5,
        event: 'issue.moved',
        payload: { id: 'i-late' },
      });
    });
    await waitFor(() =>
      expect(calls.filter((url) => url.includes('/api/v1/issues/i-late'))).toHaveLength(1),
    );

    fireEvent.click(await screen.findByTestId('view-entry-v2'));
    expect(await screen.findByTestId('board-card-i-v2')).toBeInTheDocument();

    await act(async () => {
      lateGate.resolve(
        fakeResponse({
          body: {
            data: {
              ...CARD,
              id: 'i-late',
              identifier: 'WEB-9',
              title: 'Late first-view card',
              workspace_id: 'ws-1',
            },
          },
        }),
      );
      await lateGate.promise;
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.queryByTestId('board-card-i-late')).not.toBeInTheDocument();
    expect(screen.getByTestId('board-card-i-v2')).toBeInTheDocument();
  });

  it('view.updated → 整板重拉(再次 GET /issues)', async () => {
    const calls = stub();
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-columns');
    const issuesCallsBefore = calls.filter((u) => u.includes('/issues')).length;
    act(() => {
      rt.emitFrame({ op: 'event', channel: 'view:v1', seq: 3, event: 'view.updated', payload: {} });
    });
    await waitFor(() => {
      expect(calls.filter((u) => u.includes('/issues')).length).toBeGreaterThan(issuesCallsBefore);
    });
  });

  it('label 多值轴按 payload.labels 增量改 placements 与真实列名，不整板 refetch', async () => {
    const labelView = view({ group_by: 'label' });
    const oldCard = {
      ...CARD,
      labels: [{ id: 'lbl-old', name: 'Old', color: '#336699' }],
    };
    const newCard = {
      ...CARD,
      labels: [{ id: 'lbl-new', name: 'Backend', color: '#30a46c' }],
    };
    const calls = stub(labelView, {}, [
      {
        layout: 'board',
        group_by: 'label',
        sub_group_by: null,
        multi_value_axis: true,
        column_target_status: {},
        groups: [
          { key: 'lbl-old', label: 'Old', count: 1, wip: null, data: [oldCard] },
          { key: '__none__', label: 'No label', count: 0, wip: null, data: [] },
        ],
        next_cursor: null,
      },
    ]);
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-column-lbl-old');
    const issuesCallsBefore = calls.filter((url) => url.includes('/views/v1/issues')).length;

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 4,
        event: 'issue.labels_changed',
        payload: { issue_id: 'i1', labels: newCard.labels },
      });
    });

    const newColumn = await screen.findByTestId('board-column-lbl-new');
    expect(newColumn).toHaveTextContent('Backend');
    expect(screen.getByTestId('board-card-i1')).toBeInTheDocument();
    expect(calls.filter((url) => url.includes('/views/v1/issues'))).toHaveLength(issuesCallsBefore);
    expect(calls.some((url) => url.includes('/api/v1/issues/i1'))).toBe(false);
  });

  it('label.created/updated/deleted 局部维护动态 skeleton，不整板 refetch', async () => {
    const labelView = view({ group_by: 'label' });
    const calls = stub(labelView, {}, [
      {
        layout: 'board',
        group_by: 'label',
        sub_group_by: null,
        multi_value_axis: true,
        column_target_status: {},
        groups: [{ key: '__none__', label: 'No label', count: 0, wip: null, data: [] }],
        next_cursor: null,
      },
    ]);
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-column-__none__');
    const before = calls.filter((url) => url.includes('/views/v1/issues')).length;

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:labels',
        seq: 5,
        event: 'label.created',
        payload: { id: 'lbl-new', name: 'Backend', color: '#30a46c' },
      });
    });
    expect(await screen.findByTestId('board-column-lbl-new')).toHaveTextContent('Backend');

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:labels',
        seq: 6,
        event: 'label.updated',
        payload: { id: 'lbl-new', name: 'Platform', color: '#336699' },
      });
    });
    expect(screen.getByTestId('board-column-lbl-new')).toHaveTextContent('Platform');

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:labels',
        seq: 7,
        event: 'label.deleted',
        payload: { id: 'lbl-new', name: 'Platform' },
      });
    });
    expect(screen.queryByTestId('board-column-lbl-new')).not.toBeInTheDocument();
    expect(calls.filter((url) => url.includes('/views/v1/issues'))).toHaveLength(before);
  });

  it('multi_select 自定义轴按 changed value 增量差分，不整板 refetch', async () => {
    const fieldId = '11111111-1111-4111-8111-111111111111';
    const oldOption = '22222222-2222-4222-8222-222222222222';
    const newOption = '33333333-3333-4333-8333-333333333333';
    const customField: CustomFieldDef = {
      id: fieldId,
      workspace_id: 'ws-1',
      project_id: null,
      name: 'Teams',
      field_key: 'teams',
      type: 'multi_select',
      is_required: false,
      required_on: [],
      default_value: null,
      config: {},
      position: 0,
      is_active: true,
      options: [
        {
          id: oldOption,
          field_def_id: fieldId,
          name: 'Old',
          color: null,
          position: 0,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
        {
          id: newOption,
          field_def_id: fieldId,
          name: 'Backend',
          color: null,
          position: 1,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
      ],
      created_at: '',
      updated_at: '',
    };
    const selected = view({ group_by: fieldId });
    const customCard = {
      ...CARD,
      custom_field_values: [{ field_def_id: fieldId, value_json: [oldOption] }],
    };
    const calls = stub(
      selected,
      {},
      [
        {
          layout: 'board',
          group_by: fieldId,
          sub_group_by: null,
          multi_value_axis: true,
          column_target_status: {},
          groups: [
            { key: oldOption, label: 'Old', count: 1, wip: null, data: [customCard] },
            { key: '__none__', label: 'No Teams', count: 0, wip: null, data: [] },
          ],
          next_cursor: null,
        },
      ],
      [customField],
    );
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId(`board-column-${oldOption}`);
    await waitFor(() => expect(calls.some((url) => url.includes('/custom-fields'))).toBe(true));
    const before = calls.filter((url) => url.includes('/views/v1/issues')).length;
    const definitionCallsBefore = calls.filter((url) => url.includes('/custom-fields')).length;

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:custom_fields',
        seq: 7,
        event: 'custom_field_option.updated',
        payload: {
          field_def_id: fieldId,
          change: 'created',
          option: { id: newOption, name: 'Backend', is_active: true },
        },
      });
    });
    expect(await screen.findByTestId(`board-column-${newOption}`)).toHaveTextContent('Backend');
    await waitFor(() =>
      expect(calls.filter((url) => url.includes('/custom-fields')).length).toBeGreaterThan(
        definitionCallsBefore,
      ),
    );

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 8,
        event: 'issue.custom_field_changed',
        payload: {
          issue_id: 'i1',
          field_def_id: fieldId,
          value: { field_def_id: fieldId, value_json: [newOption] },
        },
      });
    });

    expect(screen.getByTestId(`board-column-${newOption}`)).toContainElement(
      screen.getByTestId('board-card-i1'),
    );
    expect(screen.queryByTestId(`board-card-i1`)).toBeInTheDocument();
    expect(calls.filter((url) => url.includes('/views/v1/issues'))).toHaveLength(before);
    const definitionCallsBeforeRename = calls.filter((url) =>
      url.includes('/custom-fields'),
    ).length;

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:custom_fields',
        seq: 9,
        event: 'custom_field_option.updated',
        payload: {
          field_def_id: fieldId,
          change: 'updated',
          option: { id: newOption, name: 'Platform', is_active: true },
        },
      });
    });
    expect(screen.getByTestId(`board-column-${newOption}`)).toHaveTextContent('Platform');
    await waitFor(() =>
      expect(calls.filter((url) => url.includes('/custom-fields')).length).toBeGreaterThan(
        definitionCallsBeforeRename,
      ),
    );
    expect(calls.filter((url) => url.includes('/views/v1/issues'))).toHaveLength(before);

    const definitionCallsBeforeDisable = calls.filter((url) =>
      url.includes('/custom-fields'),
    ).length;
    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:ws-1:custom_fields',
        seq: 10,
        event: 'custom_field.updated',
        payload: { id: fieldId, name: 'Teams', change: 'updated', is_active: false },
      });
    });
    expect(screen.queryByTestId(`board-column-${newOption}`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`board-column-${oldOption}`)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(calls.filter((url) => url.includes('/custom-fields')).length).toBeGreaterThan(
        definitionCallsBeforeDisable,
      ),
    );
    expect(calls.filter((url) => url.includes('/views/v1/issues'))).toHaveLength(before);
  });

  it('二维 issue.* 帧在 cell 内增量收敛，不整板 refetch', async () => {
    const calls = stubSwimlanes();
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-card-i1');
    const issuesCallsBefore = calls.filter((url) => url.includes('/issues')).length;

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'view:v1',
        seq: 3,
        event: 'issue.moved',
        payload: {
          id: 'i1',
          to: { group_key: 'done' },
          to_sub_group: 'low',
          position: 7,
          updated_at: '2026-07-26T11:00:00Z',
        },
      });
    });

    expect(await screen.findByTestId('board-column-low-done')).toContainElement(
      screen.getByTestId('board-card-i1'),
    );
    expect(screen.getByTestId('count-high-todo')).toHaveTextContent('0');
    expect(screen.getByTestId('count-low-done')).toHaveTextContent('1');
    expect(calls.filter((url) => url.includes('/issues'))).toHaveLength(issuesCallsBefore);
  });

  it('重连/重同步态 → 呈现「正在重新同步」横幅(§6.12)', async () => {
    const calls = stub();
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-columns');
    expect(screen.queryByTestId('board-resync-banner')).not.toBeInTheDocument();
    const issuesCallsBefore = calls.filter((url) => url.includes('/issues')).length;
    act(() => rt.emitState('resyncing'));
    expect(await screen.findByTestId('board-resync-banner')).toBeInTheDocument();
    await waitFor(() =>
      expect(calls.filter((url) => url.includes('/issues')).length).toBeGreaterThan(
        issuesCallsBefore,
      ),
    );
    const issuesCallsAfterResync = calls.filter((url) => url.includes('/issues')).length;
    act(() => rt.emitState('reconnecting'));
    expect(await screen.findByTestId('board-resync-banner')).toBeInTheDocument();
    expect(calls.filter((url) => url.includes('/issues'))).toHaveLength(issuesCallsAfterResync);
    act(() => rt.emitState('connected'));
    await waitFor(() =>
      expect(screen.queryByTestId('board-resync-banner')).not.toBeInTheDocument(),
    );
  });

  it('卸载时取消订阅', async () => {
    stub();
    const rt = makeRealtime();
    const { unmount } = renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-columns');
    unmount();
    expect(rt.client.unsubscribe).toHaveBeenCalledWith('view:v1');
  });

  it('view.wip_exceeded → 顶部 warn toast(§4.4/§5.1)', async () => {
    stub();
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-columns');
    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'view:v1',
        seq: 4,
        event: 'view.wip_exceeded',
        payload: { view_id: 'v1', group_key: 'in_progress', limit: 2, count: 3 },
      });
    });
    // group key 与 count/limit 在两语言文案中均出现,断言 toast 已渲染。
    await screen.findByText(
      (content) => content.includes('in_progress') && content.includes('3/2'),
    );
    expect(document.querySelector('.mesh-toast--warn')).toBeInTheDocument();
  });

  it('忽略无关/在线帧并为缺省 WIP 载荷使用安全回退值', async () => {
    stub();
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-card-i1');

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'workspace:other:issues',
        seq: 5,
        event: 'issue.deleted',
        payload: { issue_id: 'i1' },
      });
      rt.emitFrame({
        op: 'event',
        channel: 'view:v1',
        seq: 6,
        event: 'view.presence',
        payload: {},
      });
    });
    expect(screen.getByTestId('board-card-i1')).toBeInTheDocument();

    act(() => {
      rt.emitFrame({
        op: 'event',
        channel: 'view:v1',
        seq: 7,
        event: 'view.wip_exceeded',
        payload: {},
      });
    });
    await screen.findByText((content) => content.includes('0/0'));
  });

  it('非投影布局不注册实时频道', async () => {
    stub(view({ layout: 'timeline' }));
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );

    expect(await screen.findByText('Layout not implemented')).toBeInTheDocument();
    expect(rt.client.subscribe).not.toHaveBeenCalled();
  });
});
