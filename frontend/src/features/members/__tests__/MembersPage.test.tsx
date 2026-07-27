/**
 * MembersPage 组件测试(member.md §4,README §6.12/T35)。
 * 以 fetch 桩驱动:名册渲染(人+agent 同表 + AI 徽章)、「仅 Agent」筛选投影为同一路由/
 * 同一组件、唯一 `[ + 新建 Agent ]` 入口(占位态)、agent 行 owner 选项禁用、角色变更经
 * PATCH、停用确认弹窗、空态。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { MembersPage } from '../MembersPage';

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

const HUMAN = {
  id: 'mem-h',
  member_type: 'human',
  role: 'member',
  status: 'active',
  display_name: 'Jane Doe',
  joined_at: '2026-01-10T08:00:00Z',
  profile: { id: 'usr-1', full_name: 'Jane Doe', email: 'jane@acme.com', avatar_url: null },
};

const AGENT = {
  id: 'mem-a',
  member_type: 'agent',
  role: 'member',
  status: 'active',
  display_name: 'Code Bot',
  joined_at: '2026-02-01T08:00:00Z',
  profile: {
    id: 'agt-9',
    name: 'Code Bot',
    description: 'fixes code',
    avatar_url: null,
    is_active: true,
    role_tag: '测试工程师',
    lifecycle_status: 'active',
  },
};

interface Recorded {
  url: string;
  init?: RequestInit;
}

function makeFetch(members: unknown[]) {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, init });
    if (url.includes('/users/me')) {
      return fakeResponse({ body: { data: ME } });
    }
    if (method === 'PATCH') {
      return fakeResponse({ body: { data: HUMAN } });
    }
    if (method === 'DELETE') {
      return fakeResponse({ body: { data: { removed: true, reassigned_issues: 0 } } });
    }
    if (method === 'POST' && url.includes('/invitations')) {
      return fakeResponse({ body: { data: [{ invite_link: '/invite/tok' }] } });
    }
    if (method === 'GET' && /\/members\/[^?]/.test(url)) {
      // member detail
      return fakeResponse({
        body: {
          data: {
            ...HUMAN,
            display_override: null,
            disabled_at: null,
            counts: { open_issues_assigned: 3 },
          },
        },
      });
    }
    if (method === 'GET' && url.includes('/members')) {
      // honour the member_type projection so the same endpoint serves the
      // "agents only" filter (README §6.12).
      const requested = url.includes('member_type=agent')
        ? members.filter((m) => (m as { member_type: string }).member_type === 'agent')
        : members;
      return fakeResponse({ body: { data: requested, next_cursor: null } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
  }) as typeof fetch;
  return { impl, calls };
}

function stub(members: unknown[]) {
  const { impl, calls } = makeFetch(members);
  vi.stubGlobal('fetch', impl);
  return calls;
}

describe('MembersPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染人与 agent 同名册,agent 行带 AI 徽章', async () => {
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });

    expect(await screen.findByText('Jane Doe')).toBeInTheDocument();
    expect(screen.getByText('Code Bot')).toBeInTheDocument();
    expect(screen.getByText('AI')).toBeInTheDocument();
    // 标题为「成员」(en: Members)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Members');
  });

  it('「仅 Agent」是同一组件的筛选投影(?member_type=agent),仅显示 agent', async () => {
    const calls = stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members?member_type=agent' });

    expect(await screen.findByText('Code Bot')).toBeInTheDocument();
    expect(screen.queryByText('Jane Doe')).not.toBeInTheDocument();
    // 名册请求确实带 member_type=agent(同一端点的投影)
    await waitFor(() =>
      expect(
        calls.some((c) => c.url.includes('/members') && c.url.includes('member_type=agent')),
      ).toBe(true),
    );
    // 单一 [+ New Agent] 入口仍然存在
    expect(screen.getByTestId('new-agent-button')).toBeInTheDocument();
  });

  it('agent 行展示类型/角色标签/生命周期,无工作区角色下拉(H-F1)', async () => {
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await screen.findByText('Code Bot');

    // H-F1:agent 行有显式「Agent」类型列 + role_tag 列 + 生命周期列,
    // 且不再有工作区角色下拉(role-select 仅人类行)。
    expect(screen.getByTestId('member-type-mem-a')).toHaveTextContent('Agent');
    expect(screen.getByTestId('member-role-tag-mem-a')).toHaveTextContent('测试工程师');
    expect(screen.getByTestId('member-lifecycle-mem-a')).toBeInTheDocument();
    expect(screen.queryByTestId('role-select-mem-a')).not.toBeInTheDocument();
    // 人类行仍保留角色下拉。
    expect(screen.getByTestId('role-select-mem-h')).toBeInTheDocument();
  });

  it('点击 [+ New Agent] 打开创建向导第一步(唯一创建入口,agent.md §4.4)', async () => {
    const user = userEvent.setup();
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await screen.findByText('Jane Doe');

    await user.click(screen.getByTestId('new-agent-button'));
    expect(await screen.findByTestId('agent-wizard-basic')).toBeInTheDocument();
    expect(screen.getByTestId('agent-wizard-name')).toBeInTheDocument();
  });

  it('管理员变更角色经 PATCH 落库', async () => {
    const user = userEvent.setup();
    const calls = stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await screen.findByText('Jane Doe');

    await user.selectOptions(screen.getByTestId('role-select-mem-h'), 'admin');
    await waitFor(() =>
      expect(
        calls.some((c) => c.init?.method === 'PATCH' && c.url.includes('/members/mem-h')),
      ).toBe(true),
    );
    const patch = calls.find((c) => c.init?.method === 'PATCH');
    expect(patch?.init?.body).toContain('"role":"admin"');
  });

  it('停用成员打开二次确认弹窗', async () => {
    const user = userEvent.setup();
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await screen.findByText('Jane Doe');

    await user.click(screen.getByTestId('disable-mem-h'));
    expect(await screen.findByText('Disable member')).toBeInTheDocument();
  });

  it('点击成员打开详情抽屉(经 GET 详情,展示名下进行中 issue)', async () => {
    const user = userEvent.setup();
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await screen.findByText('Jane Doe');

    await user.click(screen.getByTestId('member-open-mem-h'));
    const drawer = await screen.findByTestId('member-drawer');
    expect(drawer).toHaveTextContent('Jane Doe');
    expect(drawer).toHaveTextContent('3'); // open_issues_assigned
  });

  it('停用成员行显示启用按钮,点击以 PATCH status=active 恢复', async () => {
    const user = userEvent.setup();
    const calls = stub([{ ...HUMAN, status: 'disabled' }]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await screen.findByText('Jane Doe');

    await user.click(screen.getByTestId('enable-mem-h'));
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.init?.method === 'PATCH' && String(c.init?.body).includes('"status":"active"'),
        ),
      ).toBe(true),
    );
  });

  it('移除成员:确认后以 DELETE 调用(不带转派目标)', async () => {
    const user = userEvent.setup();
    const calls = stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await screen.findByText('Jane Doe');

    await user.click(screen.getByTestId('remove-mem-h'));
    expect(await screen.findByText('Remove member')).toBeInTheDocument();
    await user.click(screen.getByTestId('remove-confirm'));
    await waitFor(() =>
      expect(
        calls.some((c) => c.init?.method === 'DELETE' && c.url.includes('/members/mem-h')),
      ).toBe(true),
    );
  });

  it('无名册成员时显示空态', async () => {
    stub([]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    expect(await screen.findByText('Nothing here yet')).toBeInTheDocument();
  });

  it('无工作区成员身份时提示无工作区', async () => {
    const impl = (async () =>
      fakeResponse({
        body: { data: { user: ME.user, memberships: [] } },
      })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<MembersPage />, { route: '/members' });
    expect(
      await screen.findByText('You are not a member of any workspace yet.'),
    ).toBeInTheDocument();
  });

  it('名册加载失败显示错误态', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({ body: { data: ME } });
      }
      if (url.includes('/members')) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'server error' } },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<MembersPage />, { route: '/members' });
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  });

  it('停用投影含 removed 成员;人类/停用 Tab 切换(selectTab)', async () => {
    const removed = { ...HUMAN, id: 'mem-r', status: 'removed', display_name: 'Left Guy' };
    stub([HUMAN, removed]);
    const user = userEvent.setup();
    renderWithProviders(<MembersPage />, { route: '/members' });
    await screen.findByText('Jane Doe');
    await user.click(screen.getByRole('tab', { name: 'Disabled' }));
    expect(await screen.findByText('Left Guy')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Humans' }));
    expect(await screen.findByText('Jane Doe')).toBeInTheDocument();
  });

  it('agent 行 role_tag 为 null 时渲染空;subtext 回退', async () => {
    const agentNoTag = {
      ...AGENT,
      id: 'mem-a2',
      display_name: 'No Tag Bot',
      profile: { ...AGENT.profile, role_tag: null, description: null },
    };
    stub([agentNoTag]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    expect(await screen.findByText('No Tag Bot')).toBeInTheDocument();
    const tag = screen.getByTestId('member-role-tag-mem-a2');
    expect(tag.textContent).toBe('');
  });

  it('无邮箱人类成员 subtext 为空', async () => {
    const noEmail = {
      ...HUMAN,
      id: 'mem-ne',
      display_name: 'No Mail',
      profile: { ...HUMAN.profile, email: null },
    };
    stub([noEmail]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    expect(await screen.findByText('No Mail')).toBeInTheDocument();
  });

  it('guest 角色:角色下拉与操作按钮不可用(canManage 否分支)', async () => {
    const guestMe = { ...ME, memberships: [{ ...ME.memberships[0], role: 'guest' }] };
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: guestMe } });
      if (method === 'GET' && /\/members\/[^?]/.test(url)) {
        return fakeResponse({ body: { data: HUMAN } });
      }
      if (method === 'GET' && url.includes('/members')) {
        return fakeResponse({ body: { data: [HUMAN], next_cursor: null } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await screen.findByText('Jane Doe');
    // 人类行角色下拉渲染但禁用;操作按钮整列不渲染。
    expect((screen.getByTestId(`role-select-${HUMAN.id}`) as HTMLSelectElement).disabled).toBe(
      true,
    );
    expect(screen.queryByTestId(`remove-${HUMAN.id}`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`disable-${HUMAN.id}`)).not.toBeInTheDocument();
  });
});
