/**
 * 看板实时增量合并接线测试(§3.5/§6.12):订阅工作区 issue 频道 + 视图频道,
 * issue.* 帧单卡合并、view.updated → 整板重拉、重连/重同步 → 「正在重新同步」横幅。
 */
import { act, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { RealtimeContext } from '../../../shell/AppShell';
import { BoardPage } from '../BoardPage';
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

function stub(selectedView: View = view()) {
  const calls: string[] = [];
  const impl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/issues')) {
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
    if (url.includes('/views')) {
      return fakeResponse({ body: { data: [selectedView], next_cursor: null } });
    }
    return fakeResponse({ status: 404 });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
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

  it('重连/重同步态 → 呈现「正在重新同步」横幅(§6.12)', async () => {
    stub();
    const rt = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={{ state: 'connected', client: rt.client as never }}>
        <BoardPage />
      </RealtimeContext.Provider>,
      { route: '/views/v1' },
    );
    await screen.findByTestId('board-columns');
    expect(screen.queryByTestId('board-resync-banner')).not.toBeInTheDocument();
    act(() => rt.emitState('resyncing'));
    expect(await screen.findByTestId('board-resync-banner')).toBeInTheDocument();
    act(() => rt.emitState('reconnecting'));
    expect(await screen.findByTestId('board-resync-banner')).toBeInTheDocument();
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
