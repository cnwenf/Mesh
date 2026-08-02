/**
 * SquadDetailPage 组件测试(squad.md §4.2–§4.5):
 * 头部 / 成员(改角色·移除·加成员)/ 任务深链 / 活动时间线过滤 / 消息(kind Tab·发送)/
 * 归档切换 / squad:{id} 实时订阅与帧触发重载。
 * fetch 桩按调用序驱动:users/me → squad → members → tasks → activity → messages(→ 写/重拉)。
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import type { RealtimeClient } from '../../../realtime';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { SquadDetailPage } from '../SquadDetailPage';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

const ME = {
  user: { id: 'usr-1', email: 'owner@acme.com', display_name: 'Owner' },
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

const OWNER_SNAP = { member_id: 'mem-1', member_type: 'human', name: 'Owner' };
const AGENT_SNAP = { member_id: 'mem-2', member_type: 'agent', name: 'Builder' };

function squadFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'sq-1',
    workspace_id: 'ws-1',
    name: 'Platform',
    description: null,
    instructions: 'Keep patches small',
    avatar_url: null,
    kind: 'standing',
    status: 'active',
    leader_mode: 'single',
    primary_leader_id: 'mem-1',
    primary_leader: OWNER_SNAP,
    require_plan_approval: false,
    max_decompose_depth: 2,
    member_count: 2,
    active_task_count: 1,
    leaders: [OWNER_SNAP],
    member_preview: [],
    archived_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

const MEMBERS = {
  data: [
    {
      id: 'sm-1',
      member_id: 'mem-1',
      member_type: 'human',
      name: 'Owner',
      role: 'leader',
      joined_at: '2026-07-01T00:00:00Z',
    },
    {
      id: 'sm-2',
      member_id: 'mem-2',
      member_type: 'agent',
      name: 'Builder',
      role: 'member',
      joined_at: '2026-07-01T00:00:00Z',
    },
  ],
  next_cursor: null,
};

function taskFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'tk-1',
    squad_id: 'sq-1',
    issue_id: 'iss-1',
    parent_task_id: null,
    root_task_id: null,
    depth: 0,
    title_snapshot: 'Fix login',
    status: 'in_progress',
    assignee: AGENT_SNAP,
    stage: null,
    execution_id: null,
    plan_markdown: null,
    result_summary: null,
    failure_reason: null,
    depends_on: [],
    blocked_by: [],
    dispatched_at: null,
    started_at: null,
    finished_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

const TASKS = { data: [taskFixture()], next_cursor: null };

const ACTIVITY = {
  data: [
    {
      id: 'act-1',
      task_id: 'tk-1',
      actor_kind: 'member',
      actor: AGENT_SNAP,
      action: 'task_started',
      target_type: 'task',
      target_id: 'tk-1',
      payload: null,
      created_at: '2026-07-02T00:00:00Z',
    },
    {
      id: 'act-2',
      task_id: null,
      actor_kind: 'member',
      actor: OWNER_SNAP,
      action: 'squad_created',
      target_type: 'squad',
      target_id: 'sq-1',
      payload: null,
      created_at: '2026-07-01T00:00:00Z',
    },
  ],
  next_cursor: null,
};

const MESSAGES = {
  data: [
    {
      id: 'msg-1',
      squad_id: 'sq-1',
      task_id: 'tk-1',
      sender: AGENT_SNAP,
      recipient: null,
      kind: 'report',
      body_markdown: 'Build is green',
      body_html: null,
      pinned: false,
      attachment_ids: [],
      created_at: '2026-07-02T00:00:00Z',
    },
    {
      id: 'msg-2',
      squad_id: 'sq-1',
      task_id: null,
      sender: OWNER_SNAP,
      recipient: AGENT_SNAP,
      kind: 'instruction',
      body_markdown: 'Ship it today',
      body_html: null,
      pinned: false,
      attachment_ids: [],
      created_at: '2026-07-02T01:00:00Z',
    },
  ],
  next_cursor: null,
};

const ROSTER = {
  data: [
    {
      id: 'mem-1',
      member_type: 'human',
      role: 'owner',
      status: 'active',
      display_name: 'Owner',
      joined_at: null,
      profile: null,
    },
    {
      id: 'mem-2',
      member_type: 'agent',
      role: 'member',
      status: 'active',
      display_name: 'Builder',
      joined_at: null,
      profile: null,
    },
    {
      id: 'mem-3',
      member_type: 'human',
      role: 'member',
      status: 'active',
      display_name: 'Newbie',
      joined_at: null,
      profile: null,
    },
  ],
  next_cursor: null,
};

function ToastLayer(props: { children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

function makeFakeRealtime(): {
  value: RealtimeContextValue;
  subscribe: ReturnType<typeof vi.fn>;
  emit: (frame: RealtimeEventFrame) => void;
} {
  const listeners: Array<(frame: RealtimeEventFrame) => void> = [];
  const subscribe = vi.fn();
  const client = {
    subscribe,
    unsubscribe: vi.fn(),
    onFrame: vi.fn((listener: (frame: RealtimeEventFrame) => void) => {
      listeners.push(listener);
      return () => {
        const index = listeners.indexOf(listener);
        if (index >= 0) listeners.splice(index, 1);
      };
    }),
  };
  return {
    value: { state: 'connected', client: client as unknown as RealtimeClient },
    subscribe,
    emit: (frame) => {
      for (const listener of listeners) listener(frame);
    },
  };
}

function renderPage(realtime: RealtimeContextValue): void {
  render(
    <MemoryRouter initialEntries={['/squads/sq-1']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <RealtimeContext.Provider value={realtime}>
              <Routes>
                <Route path="/squads/:squadId" element={<SquadDetailPage />} />
              </Routes>
            </RealtimeContext.Provider>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/** 初始加载 6 连发:me → squad → members → tasks → activity → messages。 */
function queueInitialLoad(...extra: Response[]) {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: { data: squadFixture() } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: TASKS }),
    fakeResponse({ body: ACTIVITY }),
    fakeResponse({ body: MESSAGES }),
    ...extra,
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SquadDetailPage', () => {
  it('renders header, members, tasks, activity and messages; subscribes to squad channel', async () => {
    queueInitialLoad();
    const rt = makeFakeRealtime();
    renderPage(rt.value);
    await screen.findByTestId('squad-detail-page');
    expect(screen.getByTestId('squad-title').textContent).toBe('Platform');
    // members
    expect(screen.getByTestId('squad-member-mem-1')).toBeTruthy();
    expect(screen.getByTestId('squad-member-mem-2')).toBeTruthy();
    // task deep link
    expect(screen.getByText('Fix login')).toBeTruthy();
    // activity timeline entries(action 文案亦出现在过滤下拉,经 testid 断言时间线条目)
    expect(screen.getByTestId('squad-activity-act-1')).toBeTruthy();
    expect(screen.getByTestId('squad-activity-act-2')).toBeTruthy();
    // messages
    expect(screen.getByText('Build is green')).toBeTruthy();
    expect(rt.subscribe).toHaveBeenCalledWith('squad:sq-1');
  });

  it('shows the error state with retry when the squad request fails', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } }),
      // retry round (full load):
      fakeResponse({ body: { data: squadFixture() } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: TASKS }),
      fakeResponse({ body: ACTIVITY }),
      fakeResponse({ body: MESSAGES }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value);
    const retry = await screen.findByText('Retry');
    fireEvent.click(retry);
    await screen.findByTestId('squad-detail-page');
  });

  it('archives the squad and flips the header to archived/restore', async () => {
    const stub = queueInitialLoad(
      fakeResponse({
        body: { data: squadFixture({ status: 'archived', archived_at: '2026-07-03T00:00:00Z' }) },
      }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByTestId('squad-detail-page');
    fireEvent.click(screen.getByTestId('squad-archive-toggle'));
    await screen.findByText('Archived');
    expect(screen.getByTestId('squad-archive-toggle').textContent).toBe('Restore');
    const posts = stub.calls.filter((c) => c.init?.method === 'POST');
    expect(String(posts[0].url)).toContain('/squads/sq-1/archive');
  });

  it('edits the squad via the dialog and PATCHes updateSquad', async () => {
    const updated = squadFixture({ name: 'Platform', description: 'Owns the platform' });
    const stub = queueInitialLoad(fakeResponse({ body: { data: updated } }));
    renderPage(makeFakeRealtime().value);
    await screen.findByTestId('squad-detail-page');
    fireEvent.click(screen.getByTestId('squad-edit-toggle'));
    const form = await screen.findByTestId('squad-edit-form');
    // 既有字段预填(含 instructions)
    expect((screen.getByTestId('squad-edit-name') as HTMLInputElement).value).toBe('Platform');
    expect((screen.getByTestId('squad-edit-instructions') as HTMLTextAreaElement).value).toBe(
      'Keep patches small',
    );
    fireEvent.change(screen.getByTestId('squad-edit-description'), {
      target: { value: 'Owns the platform' },
    });
    fireEvent.change(screen.getByTestId('squad-edit-depth'), { target: { value: '3' } });
    fireEvent.submit(form);
    await waitFor(() => {
      const patches = stub.calls.filter((c) => c.init?.method === 'PATCH');
      expect(patches.length).toBe(1);
      expect(String(patches[0].url)).toContain('/squads/sq-1');
      expect(JSON.parse(String(patches[0].init?.body))).toMatchObject({
        name: 'Platform',
        description: 'Owns the platform',
        instructions: 'Keep patches small',
        kind: 'standing',
        leader_mode: 'single',
        max_decompose_depth: 3,
      });
    });
  });

  it('changes a member role in place via PATCH', async () => {
    const stub = queueInitialLoad(fakeResponse({ body: { data: squadFixture() } }));
    renderPage(makeFakeRealtime().value);
    await screen.findByTestId('squad-detail-page');
    const roleSelect = screen.getByTestId('squad-member-role-mem-2') as HTMLSelectElement;
    fireEvent.change(roleSelect, { target: { value: 'observer' } });
    await waitFor(() => {
      const patches = stub.calls.filter((c) => c.init?.method === 'PATCH');
      expect(patches.length).toBe(1);
      expect(String(patches[0].url)).toContain('/squads/sq-1/members/mem-2');
      expect(JSON.parse(String(patches[0].init?.body))).toEqual({ role: 'observer' });
    });
    await waitFor(() => {
      expect((screen.getByTestId('squad-member-role-mem-2') as HTMLSelectElement).value).toBe(
        'observer',
      );
    });
  });

  it('removes a member and drops the row locally', async () => {
    const stub = queueInitialLoad(fakeResponse({ body: { data: squadFixture() } }));
    renderPage(makeFakeRealtime().value);
    await screen.findByTestId('squad-detail-page');
    fireEvent.click(screen.getByTestId('squad-member-remove-mem-2'));
    await waitFor(() => {
      expect(screen.queryByTestId('squad-member-mem-2')).toBeNull();
    });
    const deletes = stub.calls.filter((c) => c.init?.method === 'DELETE');
    expect(String(deletes[0].url)).toContain('/squads/sq-1/members/mem-2');
  });

  it('adds a member from the workspace roster then refreshes the member list', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    let memberAdded = false;
    const refreshedMembers = {
      data: [
        ...MEMBERS.data,
        {
          id: 'sm-3',
          member_id: 'mem-3',
          member_type: 'human',
          name: 'Newbie',
          role: 'member',
          joined_at: '2026-07-03T00:00:00Z',
        },
      ],
      next_cursor: null,
    };
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = new URL(url, 'http://mesh.test').pathname;
      const method = init?.method ?? 'GET';
      calls.push({ url, init });

      if (path === '/api/v1/users/me' && method === 'GET') {
        return fakeResponse({ body: { data: ME } });
      }
      if (path === '/api/v1/workspaces/ws-1/members' && method === 'GET') {
        return fakeResponse({ body: ROSTER });
      }

      const squadPath = '/api/v1/workspaces/ws-1/squads/sq-1';
      if (path === squadPath && method === 'GET') {
        return fakeResponse({ body: { data: squadFixture() } });
      }
      if (path === `${squadPath}/members` && method === 'GET') {
        return fakeResponse({ body: memberAdded ? refreshedMembers : MEMBERS });
      }
      if (path === `${squadPath}/tasks` && method === 'GET') {
        return fakeResponse({ body: TASKS });
      }
      if (path === `${squadPath}/activity` && method === 'GET') {
        return fakeResponse({ body: ACTIVITY });
      }
      if (path === `${squadPath}/messages` && method === 'GET') {
        return fakeResponse({ body: MESSAGES });
      }
      if (path === `${squadPath}/members` && method === 'POST') {
        memberAdded = true;
        return fakeResponse({ body: { data: squadFixture({ member_count: 3 }) } });
      }
      throw new Error(`Unexpected ${method} ${path}`);
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);

    renderPage(makeFakeRealtime().value);
    await screen.findByTestId('squad-detail-page');
    fireEvent.click(screen.getByTestId('squad-add-member'));
    await screen.findByTestId('squad-add-member-select');
    fireEvent.change(screen.getByTestId('squad-add-member-select'), { target: { value: 'mem-3' } });
    fireEvent.submit(screen.getByTestId('squad-add-member-form'));
    await screen.findByTestId('squad-member-mem-3', undefined, { timeout: 15_000 });
    const posts = calls.filter((c) => c.init?.method === 'POST');
    expect(String(posts[0].url)).toContain('/squads/sq-1/members');
    expect(JSON.parse(String(posts[0].init?.body))).toEqual({
      members: [{ member_id: 'mem-3', role: 'member' }],
    });
  }, 15_000);

  it('filters messages by kind tabs (client-side)', async () => {
    queueInitialLoad();
    renderPage(makeFakeRealtime().value);
    await screen.findByText('Build is green');
    expect(screen.getByText('Ship it today')).toBeTruthy();
    fireEvent.click(screen.getByTestId('squad-msgtab-instruction'));
    await waitFor(() => expect(screen.queryByText('Build is green')).toBeNull());
    expect(screen.getByText('Ship it today')).toBeTruthy();
  });

  it('switches back to the all tab and changes the composer kind', async () => {
    queueInitialLoad();
    renderPage(makeFakeRealtime().value);
    await screen.findByText('Build is green');
    fireEvent.click(screen.getByTestId('squad-msgtab-instruction'));
    await waitFor(() => expect(screen.queryByText('Build is green')).toBeNull());
    fireEvent.click(screen.getByTestId('squad-msgtab-all'));
    await screen.findByText('Build is green');
    const composerKind = screen.getByTestId('squad-composer-kind') as HTMLSelectElement;
    fireEvent.change(composerKind, { target: { value: 'report' } });
    expect((screen.getByTestId('squad-composer-kind') as HTMLSelectElement).value).toBe('report');
  });

  it('picks a role then closes the add-member dialog without submitting', async () => {
    queueInitialLoad(fakeResponse({ body: ROSTER }));
    renderPage(makeFakeRealtime().value);
    await screen.findByTestId('squad-detail-page');
    fireEvent.click(screen.getByTestId('squad-add-member'));
    await screen.findByTestId('squad-add-member-select');
    fireEvent.change(screen.getByTestId('squad-add-member-role'), { target: { value: 'leader' } });
    expect((screen.getByTestId('squad-add-member-role') as HTMLSelectElement).value).toBe('leader');
    // 对话框关闭控件(Dialog onClose)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByTestId('squad-add-member-form')).toBeNull());
  });

  it('sends a message and appends it locally', async () => {
    const sent = {
      id: 'msg-3',
      squad_id: 'sq-1',
      task_id: null,
      sender: OWNER_SNAP,
      recipient: null,
      kind: 'chat',
      body_markdown: 'Hello squad',
      body_html: null,
      pinned: false,
      attachment_ids: [],
      created_at: '2026-07-03T00:00:00Z',
    };
    const stub = queueInitialLoad(fakeResponse({ status: 201, body: { data: sent } }));
    renderPage(makeFakeRealtime().value);
    await screen.findByText('Build is green');
    fireEvent.change(screen.getByTestId('squad-composer-body'), {
      target: { value: 'Hello squad' },
    });
    fireEvent.submit(screen.getByTestId('squad-composer'));
    await screen.findByText('Hello squad');
    const posts = stub.calls.filter((c) => c.init?.method === 'POST');
    expect(JSON.parse(String(posts[0].init?.body))).toEqual({
      kind: 'chat',
      body_markdown: 'Hello squad',
    });
  });

  it('filters the activity timeline by action (client-side)', async () => {
    queueInitialLoad();
    renderPage(makeFakeRealtime().value);
    await screen.findByTestId('squad-activity-act-1');
    expect(screen.getByTestId('squad-activity-act-2')).toBeTruthy();
    fireEvent.change(screen.getByTestId('squad-activity-filter'), {
      target: { value: 'squad_created' },
    });
    await waitFor(() => expect(screen.queryByTestId('squad-activity-act-1')).toBeNull());
    expect(screen.getByTestId('squad-activity-act-2')).toBeTruthy();
  });

  it('reloads when a realtime frame arrives on the squad channel', async () => {
    queueInitialLoad(
      // reload round after frame: squad → members → tasks → activity → messages
      fakeResponse({ body: { data: squadFixture() } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({
        body: { data: [taskFixture({ title_snapshot: 'Fix login v2' })], next_cursor: null },
      }),
      fakeResponse({ body: ACTIVITY }),
      fakeResponse({ body: MESSAGES }),
    );
    const rt = makeFakeRealtime();
    renderPage(rt.value);
    await screen.findByText('Fix login');
    await act(async () => {
      rt.emit({
        op: 'event',
        channel: 'squad:sq-1',
        seq: 2,
        event: 'squad_task.updated',
        payload: { task_id: 'tk-1' },
      } as RealtimeEventFrame);
    });
    await screen.findByText('Fix login v2');
  });
});
