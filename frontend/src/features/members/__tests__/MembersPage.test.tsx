/**
 * MembersPage 组件测试(member.md §4,README §6.12/T35)。
 * 以 fetch 桩驱动:名册渲染(人+agent 同表 + AI 徽章)、「仅 Agent」筛选投影为同一路由/
 * 同一组件、唯一 `[ + 新建 Agent ]` 入口、agent 行 owner 选项禁用、角色变更经 PATCH、
 * 停用/移除经底座 Menu 二次确认、空态;A-05 手机卡片结构 + 运行态五态徽标(§9.8 实时帧)。
 *
 * 桌面表格与手机卡片同源渲染(卡片默认 CSS 隐藏),故文本断言一律收敛到表格容器内
 * (within(table)),行操作经「Row actions」菜单展开后按 menuitem 可访问名点击。
 */
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, Route, Routes, useParams } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { RealtimeContext } from '../../../shell/AppShell';
import { WorkspaceProvider } from '../../../workspace/WorkspaceProvider';
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
    capacity: { running: 2, queued: 3, awaiting_approval: 4 },
  },
};

const AGENT_DETAIL = {
  id: 'agt-9',
  member: null,
  display_name: 'Code Bot',
  name: 'Code Bot',
  avatar_url: null,
  role_tag: '测试工程师',
  badge_kind: 'ai',
  lifecycle_status: 'active',
  visibility: 'workspace',
  trigger_on_assign: true,
  owner_user_id: 'usr-owner',
  created_at: '2026-02-01T08:00:00Z',
  updated_at: '2026-02-02T08:00:00Z',
  slug: null,
  bio: 'fixes code',
  system_instructions: null,
  model_config: { model_tier: 'balanced' },
  default_runtime_id: 'runtime-7',
  active_config_version_id: 'cfg-2',
  current_version: null,
};

const AGENT_CONFIG_VERSIONS = [
  {
    id: 'cfg-newer-but-inactive',
    agent_id: 'agt-9',
    snapshot: { capability_grants: ['inactive.grant'] },
    change_summary: 'Inactive',
    changed_by: 'mem-owner',
    created_at: '2026-02-03T08:00:00Z',
  },
  {
    id: 'cfg-2',
    agent_id: 'agt-9',
    snapshot: {
      model_config: { model_tier: 'balanced' },
      capability_grants: ['filesystem.write', { capability: 'issues.update' }],
    },
    change_summary: 'Current',
    changed_by: 'mem-owner',
    created_at: '2026-02-02T08:00:00Z',
  },
];

const ASSIGNED_ISSUE = {
  id: 'issue-1',
  workspace_id: 'ws-1',
  project_id: null,
  project: null,
  identifier_namespace_key: 'TEAM',
  number: 7,
  identifier: 'TEAM-7',
  title: 'Fix the build',
  description: null,
  status: null,
  status_id: 'status-1',
  state_category: 'in_progress',
  priority: 'high',
  assignee: null,
  assignee_id: 'mem-h',
  reporter: null,
  reporter_id: null,
  estimate: null,
  estimate_unit: null,
  due_date: null,
  start_date: null,
  milestone_id: null,
  cycle_id: null,
  parent_id: null,
  position: 1,
  completed_at: null,
  version: 1,
  created_at: '2026-02-01T08:00:00Z',
  updated_at: '2026-02-03T08:00:00Z',
};

interface Recorded {
  url: string;
  init?: RequestInit;
}

function makeFetch(members: unknown[], issues: unknown[] = []) {
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
    if (method === 'POST' && url.includes('/onboarding/reset')) {
      return fakeResponse({ body: { data: {} } });
    }
    if (method === 'POST' && /\/agents\/[^/]+:(disable|enable)$/.test(url)) {
      return fakeResponse({ body: { data: AGENT } });
    }
    if (method === 'POST' && url.endsWith('/agents')) {
      return fakeResponse({ body: { data: { id: 'agt-new' } } });
    }
    if (method === 'GET' && url.includes('/config-versions')) {
      return fakeResponse({ body: { data: AGENT_CONFIG_VERSIONS, next_cursor: null } });
    }
    if (method === 'GET' && /\/agents\/agt-9(?:\?|$)/.test(url)) {
      return fakeResponse({ body: { data: AGENT_DETAIL } });
    }
    if (method === 'GET' && url.includes('/issues')) {
      return fakeResponse({ body: { data: issues, next_cursor: null } });
    }
    if (method === 'GET' && /\/members\/[^?]/.test(url)) {
      // member detail
      const memberId = url.match(/\/members\/([^/?]+)/)?.[1];
      const member = members.find((item) => (item as { id?: string }).id === memberId) ?? HUMAN;
      return fakeResponse({
        body: {
          data: {
            ...(member as object),
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
      const params = new URL(url, 'http://x').searchParams;
      let requested = url.includes('member_type=agent')
        ? members.filter((m) => (m as { member_type: string }).member_type === 'agent')
        : members;
      const status = params.get('status');
      if (status === 'default') {
        requested = requested.filter((m) => {
          const member = m as typeof AGENT;
          if (member.member_type !== 'agent') return member.status !== 'removed';
          const lifecycle = member.profile?.lifecycle_status;
          if (lifecycle === undefined) return member.status === 'active';
          return lifecycle === 'active' || lifecycle === 'paused';
        });
      }
      const q = params.get('q');
      if (q) {
        requested = requested.filter((m) =>
          String((m as { display_name: string }).display_name)
            .toLowerCase()
            .includes(q.toLowerCase()),
        );
      }
      return fakeResponse({ body: { data: requested, next_cursor: null } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
  }) as typeof fetch;
  return { impl, calls };
}

function stub(members: unknown[], issues: unknown[] = []) {
  const { impl, calls } = makeFetch(members, issues);
  vi.stubGlobal('fetch', impl);
  return calls;
}

/** 等待名册表格渲染(桌面表格容器),后续文本断言收敛其内(卡片同源渲染会重复文本)。 */
async function waitForTable(): Promise<HTMLElement> {
  return screen.findByRole('table');
}

/** 打开某行(桌面表格)的行操作菜单,返回后按 menuitem 可访问名点击。 */
async function openRowMenu(user: ReturnType<typeof userEvent.setup>, id: string): Promise<void> {
  const row = screen.getByTestId(`member-open-${id}`).closest('tr') as HTMLElement;
  await user.click(within(row).getByRole('button', { name: 'Row actions' }));
}

function WorkspaceSwitchHarness(props: { readonly client: unknown }): React.JSX.Element {
  const { workspaceSlug = 'alpha' } = useParams<{ workspaceSlug: string }>();
  const nextSlug = workspaceSlug === 'alpha' ? 'beta' : 'alpha';
  return (
    <>
      <Link to={`/w/${nextSlug}/members`}>Switch workspace</Link>
      <WorkspaceProvider slug={workspaceSlug} client={props.client as never}>
        <MembersPage />
      </WorkspaceProvider>
    </>
  );
}

describe('MembersPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染人与 agent 同名册,agent 行带 AI 徽章(design Badge accent)', async () => {
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });

    const table = await waitForTable();
    expect(within(table).getByText('Jane Doe')).toBeInTheDocument();
    expect(within(table).getByText('Code Bot')).toBeInTheDocument();
    expect(within(table).getByText('AI')).toBeInTheDocument();
    // 徽章出自 design Badge(accent tone,默认 sparkle 图标)
    expect(
      screen.getByTestId('ai-badge-mem-a').querySelector('.mesh-badge--accent'),
    ).not.toBeNull();
    expect(screen.getAllByRole('img', { name: 'Code Bot' })[0]).toHaveClass('mesh-avatar--agent');
    expect(
      screen.getAllByRole('tooltip').some((tooltip) => tooltip.textContent === 'fixes code'),
    ).toBe(true);
    // 标题为「成员」(en: Members),h1 用 title-1 工具类
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Members');
  });

  it('密集人员表保留加入活动列,与身份/状态/角色/运行态同屏', async () => {
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    const table = await waitForTable();

    expect(within(table).getByRole('columnheader', { name: 'Activity' })).toBeInTheDocument();
    expect(screen.getByTestId('member-activity-mem-h')).toHaveTextContent('Joined');
    expect(screen.getByTestId('member-activity-mem-a')).toHaveTextContent('Joined');
  });

  it('名册表位于受控横向滚动容器内(窄屏不溢出页面,首列粘住,design-quality A-05/§7.6)', async () => {
    stub([HUMAN, AGENT]);
    const { container } = renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    const wrap = container.querySelector('.mesh-members__table-wrap');
    expect(wrap).not.toBeNull();
    expect(wrap).toContainElement(screen.getByRole('table'));
  });

  it('「仅 Agent」是同一组件的筛选投影(?member_type=agent),仅显示 agent', async () => {
    const calls = stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members?member_type=agent' });

    const table = await waitForTable();
    expect(within(table).getByText('Code Bot')).toBeInTheDocument();
    expect(within(table).queryByText('Jane Doe')).not.toBeInTheDocument();
    // 名册请求确实带 member_type=agent(同一端点的投影)
    await waitFor(() =>
      expect(
        calls.some((c) => c.url.includes('/members') && c.url.includes('member_type=agent')),
      ).toBe(true),
    );
    // 单一 [+ New Agent] 入口仍然存在
    expect(screen.getByTestId('new-agent-button')).toBeInTheDocument();
  });

  it('「仅 Agent」默认投影隐藏 disabled/archived，显式 disabled 标签仍可访问', async () => {
    const disabledAgent = {
      ...AGENT,
      id: 'mem-disabled',
      status: 'disabled',
      display_name: 'Disabled Bot',
      profile: { ...AGENT.profile, id: 'agt-disabled', lifecycle_status: 'disabled' },
    };
    const archivedAgent = {
      ...AGENT,
      id: 'mem-archived',
      display_name: 'Archived Bot',
      profile: { ...AGENT.profile, id: 'agt-archived', lifecycle_status: 'archived' },
    };
    const calls = stub([AGENT, disabledAgent, archivedAgent]);
    renderWithProviders(<MembersPage />, {
      route: '/members?member_type=agent',
    });

    let table = await waitForTable();
    expect(within(table).getByText('Code Bot')).toBeInTheDocument();
    expect(within(table).queryByText('Disabled Bot')).not.toBeInTheDocument();
    expect(within(table).queryByText('Archived Bot')).not.toBeInTheDocument();
    expect(
      calls.some(
        (call) => call.url.includes('member_type=agent') && call.url.includes('status=default'),
      ),
    ).toBe(true);

    const user = userEvent.setup();
    await user.click(screen.getByTestId('tab-disabled'));
    table = await waitForTable();
    expect(await within(table).findByText('Disabled Bot')).toBeInTheDocument();
  });

  it('agent 行展示类型/角色标签/生命周期,并复用工作区角色下拉(owner 禁用)', async () => {
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    expect(screen.getByTestId('member-type-mem-a')).toHaveTextContent('Agent');
    expect(screen.getByTestId('member-role-tag-mem-a')).toHaveTextContent('测试工程师');
    expect(screen.getByTestId('member-lifecycle-mem-a')).toBeInTheDocument();
    const agentRole = screen.getByTestId('role-select-mem-a') as HTMLSelectElement;
    expect(agentRole).toBeInTheDocument();
    expect(
      (within(agentRole).getByRole('option', { name: 'Owner' }) as HTMLOptionElement).disabled,
    ).toBe(true);
    // 人类行保留完整角色下拉。
    expect(screen.getByTestId('role-select-mem-h')).toBeInTheDocument();
  });

  it('agent workspace role 变更复用 member PATCH helper', async () => {
    const user = userEvent.setup();
    const calls = stub([AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await user.selectOptions(screen.getByTestId('role-select-mem-a'), 'admin');
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.init?.method === 'PATCH' &&
            call.url.includes('/members/mem-a') &&
            String(call.init.body).includes('"role":"admin"'),
        ),
      ).toBe(true),
    );
  });

  it('agent 停用走 agent lifecycle helper,不再 PATCH member status', async () => {
    const user = userEvent.setup();
    const calls = stub([AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await openRowMenu(user, 'mem-a');
    await user.click(screen.getByRole('menuitem', { name: 'Disable' }));
    await user.click(await screen.findByTestId('remove-confirm'));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            (call.init?.method ?? 'GET') === 'POST' && call.url.includes('/agents/agt-9:disable'),
        ),
      ).toBe(true),
    );
    expect(
      calls.some((call) => call.init?.method === 'PATCH' && call.url.includes('/members/mem-a')),
    ).toBe(false);
  });

  it('agent 启用走 agent lifecycle helper,不再 PATCH member status', async () => {
    const user = userEvent.setup();
    const disabledAgent = {
      ...AGENT,
      status: 'disabled',
      profile: { ...AGENT.profile, lifecycle_status: 'disabled' },
    };
    const calls = stub([disabledAgent]);
    renderWithProviders(<MembersPage />, { route: '/members?status=disabled' });
    await waitForTable();

    await openRowMenu(user, 'mem-a');
    await user.click(screen.getByRole('menuitem', { name: 'Enable' }));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            (call.init?.method ?? 'GET') === 'POST' && call.url.includes('/agents/agt-9:enable'),
        ),
      ).toBe(true),
    );
    expect(
      calls.some((call) => call.init?.method === 'PATCH' && call.url.includes('/members/mem-a')),
    ).toBe(false);
  });

  it('agent 移除走 DELETE agent helper,不再 DELETE member endpoint', async () => {
    const user = userEvent.setup();
    const calls = stub([AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await openRowMenu(user, 'mem-a');
    await user.click(screen.getByRole('menuitem', { name: 'Remove' }));
    await user.click(await screen.findByTestId('remove-confirm'));

    await waitFor(() =>
      expect(
        calls.some((call) => call.init?.method === 'DELETE' && call.url.includes('/agents/agt-9')),
      ).toBe(true),
    );
    expect(
      calls.some((call) => call.init?.method === 'DELETE' && call.url.includes('/members/mem-a')),
    ).toBe(false);
  });

  it('当前人类成员以 profile user id 识别,不提供移除自己的破坏性动作', async () => {
    const user = userEvent.setup();
    const ownerMember = {
      ...HUMAN,
      id: 'mem-owner',
      role: 'owner',
      display_name: 'Owner',
      profile: {
        ...HUMAN.profile,
        id: ME.user.id,
        full_name: 'Owner',
        email: ME.user.email,
      },
    };
    stub([ownerMember]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await openRowMenu(user, ownerMember.id);
    expect(screen.queryByRole('menuitem', { name: 'Remove' })).not.toBeInTheDocument();
  });

  it('点击 [+ New Agent] 打开创建向导第一步(唯一创建入口,agent.md §4.4)', async () => {
    const user = userEvent.setup();
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await user.click(screen.getByTestId('new-agent-button'));
    expect(await screen.findByTestId('agent-wizard-basic')).toBeInTheDocument();
    expect(screen.getByTestId('agent-wizard-name')).toBeInTheDocument();
  });

  it('管理员变更角色经 PATCH 落库', async () => {
    const user = userEvent.setup();
    const calls = stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await user.selectOptions(screen.getByTestId('role-select-mem-h'), 'admin');
    await waitFor(() =>
      expect(
        calls.some((c) => c.init?.method === 'PATCH' && c.url.includes('/members/mem-h')),
      ).toBe(true),
    );
    const patch = calls.find((c) => c.init?.method === 'PATCH');
    expect(patch?.init?.body).toContain('"role":"admin"');
  });

  it('停用成员:行操作菜单 → 二次确认弹窗(Menu 承载低频行操作,§7.5)', async () => {
    const user = userEvent.setup();
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await openRowMenu(user, 'mem-h');
    await user.click(screen.getByRole('menuitem', { name: 'Disable' }));
    expect(await screen.findByText('Disable member')).toBeInTheDocument();
  });

  it('管理员可从人类行菜单确认重置 onboarding', async () => {
    const user = userEvent.setup();
    const calls = stub([HUMAN]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await openRowMenu(user, 'mem-h');
    await user.click(screen.getByRole('menuitem', { name: 'Reset onboarding' }));
    expect(await screen.findByTestId('reset-onboarding-body')).toBeInTheDocument();
    await user.click(screen.getByTestId('reset-onboarding-confirm'));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            (call.init?.method ?? 'GET') === 'POST' && call.url.includes('/onboarding/reset'),
        ),
      ).toBe(true),
    );
    expect(await screen.findByText('Onboarding progress reset')).toBeInTheDocument();
  });

  it('点击成员打开详情抽屉(经 GET 详情,展示名下进行中 issue)', async () => {
    const user = userEvent.setup();
    const calls = stub([HUMAN, AGENT], [ASSIGNED_ISSUE]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await user.click(screen.getByTestId('member-open-mem-h'));
    const drawer = await screen.findByTestId('member-drawer');
    expect(screen.getByRole('dialog', { name: 'Jane Doe' })).toBeInTheDocument();
    expect(drawer).toHaveTextContent('3'); // open_issues_assigned
    expect(drawer).toHaveTextContent('TEAM-7 · Fix the build');
    expect(drawer).toHaveTextContent('Recently updated assigned issues');
    expect(
      calls.some(
        (call) =>
          call.url.includes('/issues') &&
          call.url.includes('assignee_id=mem-h') &&
          call.url.includes('limit=100'),
      ),
    ).toBe(true);
  });

  it('agent 也在成员详情抽屉展示运行态、能力与配置入口', async () => {
    const user = userEvent.setup();
    const calls = stub([AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await user.click(screen.getByTestId('member-open-mem-a'));
    const drawer = await screen.findByTestId('member-drawer');
    expect(drawer).toHaveTextContent('fixes code');
    expect(screen.getByTestId('member-detail-runtime')).toBeInTheDocument();
    expect(screen.getByTestId('member-detail-model-tier')).toHaveTextContent('Balanced');
    expect(screen.getByTestId('member-detail-default-runtime')).toHaveTextContent('runtime-7');
    expect(screen.getByTestId('member-detail-capabilities')).toHaveTextContent('filesystem.write');
    expect(screen.getByTestId('member-detail-capabilities')).toHaveTextContent('issues.update');
    expect(screen.getByTestId('member-detail-capabilities')).not.toHaveTextContent(
      'inactive.grant',
    );
    expect(
      calls.some(
        (call) => call.url.includes('/agents/agt-9') && !call.url.includes('/config-versions'),
      ),
    ).toBe(true);
    expect(calls.some((call) => call.url.includes('/agents/agt-9/config-versions'))).toBe(true);
    expect(
      calls.some((call) => call.url.includes('/issues') && call.url.includes('assignee_id=mem-a')),
    ).toBe(true);
    expect(screen.getByRole('link', { name: 'Open agent settings' })).toHaveAttribute(
      'href',
      '/w/team/agents/agt-9',
    );
  });

  it('agent 成员抽屉复用 presence 实时帧呈现忙碌运行态', async () => {
    const user = userEvent.setup();
    const rt = makeFakeRealtime();
    stub([AGENT]);
    renderWithProviders(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- 测试替身
      <RealtimeContext.Provider value={rt as any}>
        <MembersPage />
      </RealtimeContext.Provider>,
      { route: '/members' },
    );
    await waitForTable();
    await waitFor(() => expect(rt.client.subscribe).toHaveBeenCalledWith('agent:agt-9:presence'));
    act(() => {
      rt.emit({
        channel: 'agent:agt-9:presence',
        payload: { running: 1, queued: 0, awaiting_approval: 0 },
      });
    });

    await user.click(screen.getByTestId('member-open-mem-a'));
    expect(
      (await screen.findByTestId('member-detail-runtime')).querySelector('[data-state="running"]'),
    ).not.toBeNull();
  });

  it('关闭成员详情抽屉会中止仍在进行的详情请求', async () => {
    const user = userEvent.setup();
    let detailSignal: AbortSignal | null = null;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes('/users/me')) return Promise.resolve(fakeResponse({ body: { data: ME } }));
        if (url.includes('/members/mem-h')) {
          detailSignal = init?.signal ?? null;
          return new Promise<Response>(() => undefined);
        }
        if (url.includes('/members')) {
          return Promise.resolve(fakeResponse({ body: { data: [HUMAN], next_cursor: null } }));
        }
        return Promise.resolve(fakeResponse({ status: 404 }));
      }) as typeof fetch,
    );
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await user.click(screen.getByTestId('member-open-mem-h'));
    expect(await screen.findByRole('dialog', { name: 'Jane Doe' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(detailSignal).not.toBeNull();
    expect((detailSignal as unknown as AbortSignal).aborted).toBe(true);
  });

  it('成员主详情读取失败时显示可重试错误态并可恢复', async () => {
    const user = userEvent.setup();
    let detailCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
        if (url.includes('/issues')) {
          return fakeResponse({ body: { data: [], next_cursor: null } });
        }
        if (url.includes('/members/mem-h')) {
          detailCalls += 1;
          return detailCalls === 1
            ? fakeResponse({
                status: 500,
                body: { error: { code: 'internal_error', message: 'failed' } },
              })
            : fakeResponse({
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
        if (url.includes('/members')) {
          return fakeResponse({ body: { data: [HUMAN], next_cursor: null } });
        }
        return fakeResponse({ status: 404 });
      }) as typeof fetch,
    );
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await user.click(screen.getByTestId('member-open-mem-h'));
    const dialog = await screen.findByRole('dialog', { name: 'Jane Doe' });
    expect(await within(dialog).findByText('Something went wrong')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(detailCalls).toBe(2));
    expect(within(dialog).queryByText('Something went wrong')).toBeNull();
    expect(within(dialog).getByText('3')).toBeInTheDocument();
  });

  it('切换工作区时关闭旧成员抽屉并中止旧租户请求', async () => {
    const user = userEvent.setup();
    let detailSignal: AbortSignal | null = null;
    const memberships = [
      { ...ME.memberships[0], workspace_id: 'ws-alpha', workspace_slug: 'alpha' },
      { ...ME.memberships[0], workspace_id: 'ws-beta', workspace_slug: 'beta' },
    ];
    const providerClient = {
      request: vi.fn(async (_method: string, path: string) => {
        const slug = path.endsWith('/beta') ? 'beta' : 'alpha';
        return {
          id: `ws-${slug}`,
          name: slug,
          slug,
          logo_url: null,
          timezone: 'UTC',
          settings: {},
          my_role: 'owner',
          created_at: '2026-08-04T00:00:00Z',
          updated_at: '2026-08-04T00:00:00Z',
        };
      }),
    };
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes('/users/me')) {
          return Promise.resolve(fakeResponse({ body: { data: { ...ME, memberships } } }));
        }
        if (url.includes('/workspaces/ws-alpha/members/mem-h')) {
          detailSignal = init?.signal ?? null;
          return new Promise<Response>(() => undefined);
        }
        if (url.includes('/issues')) {
          return Promise.resolve(fakeResponse({ body: { data: [], next_cursor: null } }));
        }
        if (url.includes('/workspaces/ws-alpha/members')) {
          return Promise.resolve(fakeResponse({ body: { data: [HUMAN], next_cursor: null } }));
        }
        if (url.includes('/workspaces/ws-beta/members')) {
          return Promise.resolve(fakeResponse({ body: { data: [], next_cursor: null } }));
        }
        return Promise.resolve(fakeResponse({ status: 404 }));
      }) as typeof fetch,
    );
    renderWithProviders(
      <Routes>
        <Route
          path="/w/:workspaceSlug/members"
          element={<WorkspaceSwitchHarness client={providerClient} />}
        />
      </Routes>,
      { route: '/w/alpha/members' },
    );
    await waitForTable();

    await user.click(screen.getByTestId('member-open-mem-h'));
    expect(await screen.findByRole('dialog', { name: 'Jane Doe' })).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Switch workspace' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Jane Doe' })).toBeNull());
    expect(detailSignal).not.toBeNull();
    expect((detailSignal as unknown as AbortSignal).aborted).toBe(true);
  });

  it('停用成员行菜单含启用项,点击以 PATCH status=active 恢复', async () => {
    const user = userEvent.setup();
    const calls = stub([{ ...HUMAN, status: 'disabled' }]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await openRowMenu(user, 'mem-h');
    await user.click(screen.getByRole('menuitem', { name: 'Enable' }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.init?.method === 'PATCH' && String(c.init?.body).includes('"status":"active"'),
        ),
      ).toBe(true),
    );
  });

  it('移除成员:行操作菜单(破坏性)→ 确认后 DELETE(不带转派目标)', async () => {
    const user = userEvent.setup();
    const calls = stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();

    await openRowMenu(user, 'mem-h');
    await user.click(screen.getByRole('menuitem', { name: 'Remove' }));
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
    expect(await screen.findByText('No members yet')).toBeInTheDocument();
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
    let table = await waitForTable();
    await user.click(screen.getByRole('tab', { name: 'Disabled' }));
    table = await waitForTable();
    expect(within(table).getByText('Left Guy')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Humans' }));
    table = await waitForTable();
    expect(within(table).getByText('Jane Doe')).toBeInTheDocument();
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
    await waitForTable();
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
    const table = await waitForTable();
    expect(within(table).getByText('No Mail')).toBeInTheDocument();
  });

  it('guest 角色:角色下拉禁用,无行操作菜单(canManage 否分支)', async () => {
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
    await waitForTable();
    // 人类行角色下拉渲染但禁用;行操作菜单整列不渲染。
    expect((screen.getByTestId(`role-select-${HUMAN.id}`) as HTMLSelectElement).disabled).toBe(
      true,
    );
    expect(screen.queryByRole('button', { name: 'Row actions' })).not.toBeInTheDocument();
  });

  it('邀请人类:打开弹窗,填邮箱选角色后发送(invite 全链路)', async () => {
    const user = userEvent.setup();
    const calls = stub([HUMAN]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    await user.click(screen.getByTestId('invite-human-button'));
    await user.type(screen.getByTestId('invite-email'), 'new@acme.com');
    await user.selectOptions(screen.getByTestId('invite-role'), 'admin');
    await user.click(screen.getByTestId('invite-submit'));
    await screen.findByTestId('invite-done');
    expect(
      calls.some((c) => (c.init?.method ?? 'GET') === 'POST' && c.url.includes('/invitations')),
    ).toBe(true);
  });

  it('邀请弹窗:邮箱为空禁用发送;取消按钮关闭(onClose)', async () => {
    const user = userEvent.setup();
    stub([HUMAN]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    await user.click(screen.getByTestId('invite-human-button'));
    expect((screen.getByTestId('invite-submit') as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByTestId('invite-email')).not.toBeInTheDocument());
  });

  it('新建 Agent:向导关闭(onClose)与完成创建(onSaved 重拉名册)', async () => {
    const user = userEvent.setup();
    const calls = stub([HUMAN]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    // 打开后以对话框关闭按钮关闭 → onClose。
    await user.click(screen.getByTestId('new-agent-button'));
    expect(await screen.findByTestId('agent-wizard-basic')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Close dialog' }));
    await waitFor(() => expect(screen.queryByTestId('agent-wizard-basic')).not.toBeInTheDocument());
    // 重开并走完四步创建 → POST /agents → onSaved 重拉。
    await user.click(screen.getByTestId('new-agent-button'));
    await user.type(screen.getByTestId('agent-wizard-name'), '名册小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-finish'));
    await waitFor(() =>
      expect(
        calls.some((c) => (c.init?.method ?? 'GET') === 'POST' && c.url.endsWith('/agents')),
      ).toBe(true),
    );
  });

  it('搜索框输入按名筛选(debounce 后生效)', async () => {
    const user = userEvent.setup();
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    await user.type(screen.getByTestId('member-search'), 'Code');
    await waitFor(() => expect(screen.queryAllByText('Jane Doe')).toHaveLength(0), {
      timeout: 3000,
    });
    const table = await waitForTable();
    expect(within(table).getByText('Code Bot')).toBeInTheDocument();
  });

  it('名册加载失败后点击重试恢复', async () => {
    let failedOnce = false;
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'GET' && url.includes('/members') && !/\/members\/[^?]/.test(url)) {
        if (!failedOnce) {
          failedOnce = true;
          return fakeResponse({
            status: 500,
            body: { error: { code: 'internal_error', message: 'server error' } },
          });
        }
        return fakeResponse({ body: { data: [HUMAN], next_cursor: null } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const user = userEvent.setup();
    renderWithProviders(<MembersPage />, { route: '/members' });
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    const table = await waitForTable();
    expect(within(table).getByText('Jane Doe')).toBeInTheDocument();
  });

  it('成员抽屉:打开后可经关闭按钮关闭', async () => {
    const user = userEvent.setup();
    stub([HUMAN]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    await user.click(screen.getByTestId(`member-open-${HUMAN.id}`));
    expect(await screen.findByTestId('member-drawer')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByTestId('member-drawer')).not.toBeInTheDocument());
  });

  it('agent 行无 profile 时点击开抽屉(非深链);详情获取失败不崩溃', async () => {
    const agentNoProfile = { ...AGENT, id: 'mem-anp', profile: null };
    const user = userEvent.setup();
    stub([agentNoProfile]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    await user.click(screen.getByTestId('member-open-mem-anp'));
    expect(await screen.findByTestId('member-drawer')).toBeInTheDocument();
  });

  // --- A-05 手机主次行卡片 + 运行态五态(§9.8)---------------------------------

  it('手机卡片与表格同源渲染(结构存在;桌面隐藏由 CSS 控制)', async () => {
    stub([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    // 卡片结构与表格同一 members 数组(每成员一张卡)。
    expect(screen.getByTestId('member-card-mem-h')).toBeInTheDocument();
    expect(screen.getByTestId('member-card-mem-a')).toBeInTheDocument();
    // 卡片含名称 / AI 徽章 / 行操作菜单(共享 handlers)。
    const card = screen.getByTestId('member-card-mem-a');
    expect(within(card).getByTestId('card-ai-badge-mem-a')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Row actions' })).toBeInTheDocument();
  });

  it('卡片点击名称共享 handlers:人类行开抽屉', async () => {
    const user = userEvent.setup();
    stub([HUMAN]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    await user.click(screen.getByTestId('member-card-open-mem-h'));
    expect(await screen.findByTestId('member-drawer')).toBeInTheDocument();
  });

  it('卡片角色下拉共享 handlers:变更经 PATCH', async () => {
    const user = userEvent.setup();
    const calls = stub([HUMAN]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    await user.selectOptions(screen.getByTestId('card-role-select-mem-h'), 'admin');
    await waitFor(() =>
      expect(
        calls.some((c) => c.init?.method === 'PATCH' && c.url.includes('/members/mem-h')),
      ).toBe(true),
    );
  });

  it('卡片加入时间 caption 渲染(joined_at);为 null 时不渲染', async () => {
    const noJoined = { ...HUMAN, id: 'mem-nj', joined_at: null };
    stub([HUMAN, noJoined]);
    renderWithProviders(<MembersPage />, { route: '/members' });
    await waitForTable();
    // 有 joined_at → 卡片含 Joined 文案;null → 无。
    expect(within(screen.getByTestId('member-card-mem-h')).getByText(/Joined/)).toBeInTheDocument();
    expect(
      within(screen.getByTestId('member-card-mem-nj')).queryByText(/Joined/),
    ).not.toBeInTheDocument();
  });

  it('agent 行运行态:REST 首屏快照后由 realtime 绝对值覆盖', async () => {
    const rt = makeFakeRealtime();
    stub([HUMAN, AGENT]);
    renderWithProviders(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- 测试替身
      <RealtimeContext.Provider value={rt as any}>
        <MembersPage />
      </RealtimeContext.Provider>,
      { route: '/members' },
    );
    await waitForTable();
    // 订阅该 agent 的 presence 频道(profile.id = agt-9)。
    await waitFor(() => expect(rt.client.subscribe).toHaveBeenCalledWith('agent:agt-9:presence'));
    // 无需等待帧:REST 快照立即驱动表格与卡片运行态和 N/M/K 文案。
    expect(
      screen.getByTestId('member-presence-mem-a').querySelector('[data-state="running"]'),
    ).not.toBeNull();
    expect(
      screen.getByTestId('card-member-presence-mem-a').querySelector('[data-state="running"]'),
    ).not.toBeNull();
    expect(screen.getByTestId('member-presence-caption-mem-a')).toHaveTextContent(
      'Running 2 / Queued 3 / Awaiting 4',
    );
    // 人类行无运行态徽标(presence 单元不渲染)。
    expect(screen.queryByTestId('member-presence-mem-h')).toBeNull();

    // presence 帧是绝对快照,覆盖而非累加 REST 值。
    act(() => {
      rt.emit({
        channel: 'agent:agt-9:presence',
        payload: { running: 1, queued: 0, awaiting_approval: 0 },
      });
    });
    await waitFor(() =>
      expect(
        screen.getByTestId('member-presence-mem-a').querySelector('[data-state="running"]'),
      ).not.toBeNull(),
    );
    expect(screen.getByTestId('member-presence-caption-mem-a')).toHaveTextContent(
      'Running 1 / Queued 0 / Awaiting 0',
    );
  });

  it('agent 行运行态:queued / waiting 归一(三元组优先级)', async () => {
    const rt = makeFakeRealtime();
    stub([AGENT]);
    renderWithProviders(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- 测试替身
      <RealtimeContext.Provider value={rt as any}>
        <MembersPage />
      </RealtimeContext.Provider>,
      { route: '/members' },
    );
    await waitForTable();
    // 全量 coverage 并发下 effect 订阅可能晚于表格首帧；先证明真实 handler
    // 已挂载再发帧，避免把调度时序误判为 presence 归一化失败。
    await waitFor(() => expect(rt.client.subscribe).toHaveBeenCalledWith('agent:agt-9:presence'));
    act(() => {
      rt.emit({ channel: 'agent:agt-9:presence', payload: { queued: 2 } });
    });
    await waitFor(() =>
      expect(
        screen.getByTestId('member-presence-mem-a').querySelector('[data-state="queued"]'),
      ).not.toBeNull(),
    );
    act(() => {
      rt.emit({ channel: 'agent:agt-9:presence', payload: { awaiting_approval: 1 } });
    });
    await waitFor(() =>
      expect(
        screen.getByTestId('member-presence-mem-a').querySelector('[data-state="waiting"]'),
      ).not.toBeNull(),
    );
    // 全 0 → idle
    act(() => {
      rt.emit({ channel: 'agent:agt-9:presence', payload: {} });
    });
    await waitFor(() =>
      expect(
        screen.getByTestId('member-presence-mem-a').querySelector('[data-state="idle"]'),
      ).not.toBeNull(),
    );
  });

  it('canonical workspace provider 选中第二个 membership,并保留 DataView/Tabs 与 workspace 深链', async () => {
    const user = userEvent.setup();
    const betaMembership = {
      ...ME.memberships[0],
      workspace_id: 'ws-2',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
      role: 'admin',
    };
    const me = { ...ME, memberships: [ME.memberships[0], betaMembership] };
    const calls: Recorded[] = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/workspaces/ws-2/issues')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('/workspaces/ws-2/members/mem-a')) {
        return fakeResponse({
          body: {
            data: {
              ...AGENT,
              display_override: null,
              disabled_at: null,
              counts: { open_issues_assigned: 0 },
            },
          },
        });
      }
      if (url.includes('/workspaces/ws-2/members')) {
        return fakeResponse({ body: { data: [AGENT], next_cursor: null } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch);
    const providerClient = {
      request: vi.fn(async () => ({
        id: 'ws-2',
        name: 'Beta',
        slug: 'beta',
        logo_url: null,
        timezone: 'UTC',
        settings: {},
        my_role: 'admin',
        created_at: '2026-08-04T00:00:00Z',
        updated_at: '2026-08-04T00:00:00Z',
      })),
    };

    renderWithProviders(
      <WorkspaceProvider slug="beta" client={providerClient as never}>
        <Routes>
          <Route path="/w/:workspaceSlug/members" element={<MembersPage />} />
          <Route
            path="/w/:workspaceSlug/agents/:agentId"
            element={<div data-testid="canonical-agent-route" />}
          />
        </Routes>
      </WorkspaceProvider>,
      { route: '/w/beta/members' },
    );

    await waitForTable();
    expect(screen.getByTestId('data-view')).toBeInTheDocument();
    expect(screen.getByTestId('tab-all')).toHaveAttribute('role', 'tab');
    expect(calls.some((call) => call.url.includes('/workspaces/ws-2/members'))).toBe(true);
    expect(calls.some((call) => call.url.includes('/workspaces/ws-1/members'))).toBe(false);

    await user.click(screen.getByTestId('member-open-mem-a'));
    expect(await screen.findByTestId('member-drawer')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open agent settings' })).toHaveAttribute(
      'href',
      '/w/beta/agents/agt-9',
    );
  });

  it('workspace member events reload the roster and reject other channels', async () => {
    const rt = makeFakeRealtime();
    const calls = stub([HUMAN]);
    renderWithProviders(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- 测试替身
      <RealtimeContext.Provider value={rt as any}>
        <MembersPage />
      </RealtimeContext.Provider>,
      { route: '/members' },
    );
    await waitForTable();
    await waitFor(() => expect(rt.client.subscribe).toHaveBeenCalledWith('workspace:ws-1'));
    const rosterCalls = (): number =>
      calls.filter(
        (call) =>
          (call.init?.method ?? 'GET') === 'GET' && call.url.includes('/workspaces/ws-1/members'),
      ).length;
    const before = rosterCalls();

    act(() => {
      rt.emit({
        channel: 'workspace:other',
        event: 'member.updated',
        payload: { member_id: 'mem-h', changes: { role: 'admin' } },
      });
    });
    expect(rosterCalls()).toBe(before);

    act(() => {
      rt.emit({
        channel: 'workspace:ws-1',
        event: 'member.updated',
        payload: { member_id: 'mem-h', changes: { role: 'admin' } },
      });
    });
    await waitFor(() => expect(rosterCalls()).toBeGreaterThan(before));
  });
});

interface FakeFrame {
  channel: string;
  event?: string;
  payload?: unknown;
}

function makeFakeRealtime() {
  const handlers: Array<(frame: FakeFrame) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((handler: (frame: FakeFrame) => void) => {
      handlers.push(handler);
      return (): void => {
        const index = handlers.indexOf(handler);
        if (index >= 0) handlers.splice(index, 1);
      };
    }),
  };
  return {
    client,
    emit: (frame: FakeFrame): void => {
      for (const handler of [...handlers]) handler(frame);
    },
  };
}
