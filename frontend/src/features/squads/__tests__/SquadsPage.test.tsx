/**
 * SquadsPage 组件测试(squad.md §4.1):
 * 卡片渲染 / kind·status 过滤参数 / 错误态重试 / 新建对话框 / 空态 / 无工作区空态。
 * fetch 桩按调用序驱动:users/me → squads(→ 后续写/重拉)。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { SquadsPage } from '../SquadsPage';

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

function squadFixture(id: string, name: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    workspace_id: 'ws-1',
    name,
    description: null,
    instructions: null,
    avatar_url: null,
    kind: 'standing',
    status: 'active',
    leader_mode: 'single',
    primary_leader_id: null,
    primary_leader: null,
    require_plan_approval: false,
    max_decompose_depth: 2,
    member_count: 3,
    active_task_count: 2,
    leaders: [],
    member_preview: [],
    archived_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

const SQUAD_1 = squadFixture('sq-1', 'Platform', {
  member_preview: [
    { member_id: 'mem-1', member_type: 'human', name: 'Owner', role: 'leader' },
    { member_id: 'mem-2', member_type: 'agent', name: 'Builder', role: 'member' },
  ],
});
const SQUAD_2 = squadFixture('sq-2', 'Growth', { kind: 'adhoc' });

/** 工作区名册(新建对话框成员选择器来源)。 */
const ROSTER_PAGE = {
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
  ],
  next_cursor: null,
};

function queueInitialLoad(...extra: Response[]) {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: { data: [SQUAD_1, SQUAD_2], next_cursor: null } }),
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

describe('SquadsPage', () => {
  it('renders squad cards with kind badge, status label and counts', async () => {
    queueInitialLoad();
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('Platform');
    expect(screen.getByText('Growth')).toBeTruthy();
    expect(screen.getByTestId('squad-kind-sq-1').textContent).toBe('Standing');
    expect(screen.getByTestId('squad-kind-sq-2').textContent).toBe('Ad hoc');
    expect(screen.getByTestId('squad-tasks-sq-1').textContent).toBe('2 active tasks');
    expect(screen.getByTestId('squad-members-sq-1').textContent).toBe('3 members');
    // 状态点文本信号(§6.12:颜色非唯一信号;过滤下拉亦含 Active 选项)
    expect(screen.getAllByText('Active').length).toBeGreaterThanOrEqual(2);
  });

  it('passes kind/status/q filters to the list request', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: [SQUAD_2], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<SquadsPage />, { route: '/squads?kind=adhoc&status=active&q=gro' });
    await screen.findByText('Growth');
    const squadCalls = stub.calls.filter((c) => String(c.url).includes('/squads?'));
    expect(String(squadCalls[0].url)).toContain('kind=adhoc');
    expect(String(squadCalls[0].url)).toContain('status=active');
    expect(String(squadCalls[0].url)).toContain('q=gro');
  });

  it('shows the error state with retry when the list request fails', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } }),
      // retry round:
      fakeResponse({ body: { data: [SQUAD_1], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    const retry = await screen.findByText('Retry');
    fireEvent.click(retry);
    await screen.findByText('Platform');
  });

  it('creates a squad via the dialog with a leader and prepends the card', async () => {
    const created = squadFixture('sq-3', 'Brand new');
    const stub = queueInitialLoad(
      fakeResponse({ body: ROSTER_PAGE }),
      fakeResponse({ status: 201, body: { data: created } }),
    );
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('Platform');
    fireEvent.click(screen.getByTestId('squad-open-create'));
    await screen.findByTestId('squad-create-member-select');
    fireEvent.change(screen.getByTestId('squad-create-name'), { target: { value: 'Brand new' } });
    fireEvent.change(screen.getByTestId('squad-create-kind'), { target: { value: 'task_scoped' } });
    fireEvent.click(screen.getByTestId('squad-create-require-approval'));
    // 选取 mem-1 为组长(leader 闸门满足后方可创建)
    fireEvent.change(screen.getByTestId('squad-create-member-select'), { target: { value: 'mem-1' } });
    fireEvent.change(screen.getByTestId('squad-create-member-role'), { target: { value: 'leader' } });
    fireEvent.click(screen.getByTestId('squad-create-member-add'));
    fireEvent.click(screen.getByTestId('squad-create-submit'));
    await screen.findByText('Brand new');
    const posts = stub.calls.filter((c) => c.init?.method === 'POST');
    expect(posts.length).toBe(1);
    expect(JSON.parse(String(posts[0].init?.body))).toEqual({
      name: 'Brand new',
      kind: 'task_scoped',
      leader_mode: 'single',
      require_plan_approval: true,
      max_decompose_depth: 2,
      members: [{ member_id: 'mem-1', role: 'leader', member_type: 'human' }],
    });
  });

  it('disables Create until at least one leader is added (leader gate)', async () => {
    queueInitialLoad(fakeResponse({ body: ROSTER_PAGE }));
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('Platform');
    fireEvent.click(screen.getByTestId('squad-open-create'));
    await screen.findByTestId('squad-create-member-select');
    fireEvent.change(screen.getByTestId('squad-create-name'), { target: { value: 'No leader' } });
    // 未加组长:Create 置灰,且提示存在
    const submit = screen.getByTestId('squad-create-submit') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(screen.getByTestId('squad-create-leader-gate')).toBeTruthy();
    // 加一名 member(非组长)→ 仍置灰
    fireEvent.change(screen.getByTestId('squad-create-member-select'), { target: { value: 'mem-2' } });
    fireEvent.change(screen.getByTestId('squad-create-member-role'), { target: { value: 'member' } });
    fireEvent.click(screen.getByTestId('squad-create-member-add'));
    expect((screen.getByTestId('squad-create-submit') as HTMLButtonElement).disabled).toBe(true);
    // 把该成员改为 leader → 闸门满足,Create 启用
    fireEvent.change(screen.getByTestId('squad-create-picked-role-mem-2'), { target: { value: 'leader' } });
    expect((screen.getByTestId('squad-create-submit') as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId('squad-create-leader-gate')).toBeNull();
  });

  it('renders the member_preview avatar wall with a leader mark on squad cards', async () => {
    queueInitialLoad();
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('Platform');
    expect(screen.getByTestId('squad-avatarwall')).toBeTruthy();
    expect(screen.getByTestId('squad-avatar-mem-1')).toBeTruthy();
    expect(screen.getByTestId('squad-avatar-mem-2')).toBeTruthy();
    // leader 角标仅标注 mem-1
    expect(screen.getByTestId('squad-avatar-leader-mem-1')).toBeTruthy();
    expect(screen.queryByTestId('squad-avatar-leader-mem-2')).toBeNull();
  });

  it('renders the empty state when there are no squads', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('No squads yet');
  });

  it('renders the no-workspace empty state when the user has no membership', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: { ...ME, memberships: [] } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('Select a workspace to view its squads.');
  });

  it('filters by kind and status via the filter selects', async () => {
    const stub = queueInitialLoad(
      fakeResponse({ body: { data: [SQUAD_2], next_cursor: null } }),
      fakeResponse({ body: { data: [SQUAD_1], next_cursor: null } }),
    );
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('Platform');
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[0], { target: { value: 'adhoc' } });
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('kind=adhoc'))).toBe(true);
    });
    fireEvent.change(selects[1], { target: { value: 'archived' } });
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('status=archived'))).toBe(true);
    });
  });

  it('captures description, instructions and decompose depth in the create dialog', async () => {
    const created = squadFixture('sq-4', 'Deep squad');
    const stub = queueInitialLoad(
      fakeResponse({ body: ROSTER_PAGE }),
      fakeResponse({ status: 201, body: { data: created } }),
    );
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('Platform');
    fireEvent.click(screen.getByTestId('squad-open-create'));
    await screen.findByTestId('squad-create-member-select');
    fireEvent.change(screen.getByTestId('squad-create-name'), { target: { value: 'Deep squad' } });
    fireEvent.change(screen.getByTestId('squad-create-description'), {
      target: { value: 'Owns depth' },
    });
    fireEvent.change(screen.getByTestId('squad-create-instructions'), {
      target: { value: 'Prefer small batches' },
    });
    fireEvent.change(screen.getByTestId('squad-create-depth'), { target: { value: '4' } });
    // leader 闸门:加一名组长后方可创建
    fireEvent.change(screen.getByTestId('squad-create-member-select'), { target: { value: 'mem-1' } });
    fireEvent.change(screen.getByTestId('squad-create-member-role'), { target: { value: 'leader' } });
    fireEvent.click(screen.getByTestId('squad-create-member-add'));
    fireEvent.click(screen.getByTestId('squad-create-submit'));
    await screen.findByText('Deep squad');
    const posts = stub.calls.filter((c) => c.init?.method === 'POST');
    expect(JSON.parse(String(posts[0].init?.body))).toMatchObject({
      name: 'Deep squad',
      description: 'Owns depth',
      instructions: 'Prefer small batches',
      max_decompose_depth: 4,
    });
  });

  it('opens the create dialog from the empty state action', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('No squads yet');
    const newButtons = screen.getAllByText('New Squad');
    fireEvent.click(newButtons[newButtons.length - 1]);
    await screen.findByTestId('squad-create-form');
  });

  it('reloads after the debounced search input writes the q param', async () => {
    const stub = queueInitialLoad(
      fakeResponse({ body: { data: [SQUAD_2], next_cursor: null } }),
    );
    renderWithProviders(<SquadsPage />, { route: '/squads' });
    await screen.findByText('Platform');
    fireEvent.change(screen.getByTestId('squad-filter-q'), { target: { value: 'growth' } });
    await waitFor(() => {
      const urls = stub.calls.map((c) => String(c.url));
      expect(urls.some((u) => u.includes('/squads?') && u.includes('q=growth'))).toBe(true);
    });
  });
});
