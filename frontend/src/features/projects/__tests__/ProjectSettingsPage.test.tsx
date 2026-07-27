/**
 * ProjectSettingsPage + ProjectMembersSection 测试(project.md §4.1 设置侧栏)。
 * 表单保存经 useOptimisticMutation(PATCH 携 If-Match: updated_at);409 conflict
 * 自动收敛(重取 + 重放)并提示 conflictToast;成员管理(列表/添加/改角色/移除);
 * 危险区归档切换与删除二次确认。
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import type { RecordedCall } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { ProjectSettingsPage } from '../ProjectSettingsPage';

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

const ROSTER = [
  {
    id: 'mem-lead',
    member_type: 'human',
    display_name: 'Jane Doe',
    role: 'owner',
    status: 'active',
    joined_at: null,
    profile: { kind: 'human', email: 'jane@acme.com' },
  },
  {
    id: 'mem-2',
    member_type: 'human',
    display_name: 'John Smith',
    role: 'member',
    status: 'active',
    joined_at: null,
    profile: { kind: 'human', email: 'john@acme.com' },
  },
];

const PROJECT_MEMBER_ENTRY = {
  id: 'pm-1',
  project_id: 'prj-1',
  member_id: 'mem-2',
  member: { id: 'mem-2', name: 'John Smith', member_type: 'human' },
  role: 'member',
  created_at: '2026-01-01T00:00:00Z',
};

function makeProject(overrides: Record<string, unknown> = {}) {
  return {
    id: 'prj-1',
    workspace_id: 'ws-1',
    name: 'Apollo',
    key: 'APL',
    description: 'Moon landing',
    icon: null,
    color: null,
    status: 'active',
    health: 'on_track',
    visibility: 'public',
    lead: { id: 'mem-lead', name: 'Jane Doe', member_type: 'human' },
    lead_member_id: 'mem-lead',
    start_date: '2026-01-01',
    target_date: '2026-09-30',
    progress: 0.5,
    open_issues: 5,
    done_issues: 5,
    issue_seq: 10,
    archived: false,
    archived_at: null,
    my_role: 'lead',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

interface StubOptions {
  /** 前 N 次 PATCH /projects/prj-1 返回 409 conflict(乐观并发收敛路径) */
  readonly conflictPatchTimes?: number;
  /** 409 重取时服务端返回的并发编辑后的 name */
  readonly serverNameAfterConflict?: string;
  /** 覆盖默认项目 fixture(如字段全 null 的三态预填场景) */
  readonly project?: ReturnType<typeof makeProject>;
}

function stubFetch(opts: StubOptions = {}) {
  const calls: RecordedCall[] = [];
  let project = opts.project === undefined ? makeProject() : opts.project;
  let patchFailures = opts.conflictPatchTimes ?? 0;
  let conflictHappened = false;
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, init });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (method === 'GET' && url.includes('/members')) {
      if (url.includes('/projects/')) {
        return fakeResponse({ body: { data: [PROJECT_MEMBER_ENTRY], next_cursor: null } });
      }
      return fakeResponse({ body: { data: ROSTER, next_cursor: null } });
    }
    if (method === 'POST' && url.includes('/members')) {
      const body = JSON.parse(String(init?.body)) as { member_id: string; role: string };
      return fakeResponse({
        status: 201,
        body: {
          data: {
            ...PROJECT_MEMBER_ENTRY,
            id: 'pm-new',
            member_id: body.member_id,
            role: body.role,
            member: { id: body.member_id, name: 'Jane Doe', member_type: 'human' },
          },
        },
      });
    }
    if (method === 'PATCH' && url.includes('/members/')) {
      const body = JSON.parse(String(init?.body)) as { role: string };
      return fakeResponse({
        body: { data: { ...PROJECT_MEMBER_ENTRY, role: body.role } },
      });
    }
    if (method === 'DELETE' && url.includes('/members/')) {
      return fakeResponse({ body: { data: { id: 'mem-2', removed: true } } });
    }
    if (method === 'PATCH' && url.match(/\/projects\/[^/]+$/)) {
      if (patchFailures > 0) {
        patchFailures -= 1;
        conflictHappened = true;
        return fakeResponse({
          status: 409,
          body: { error: { code: 'conflict', message: 'conflict' } },
        });
      }
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      project = { ...project, ...body, updated_at: '2026-07-02T00:00:00Z' };
      return fakeResponse({ body: { data: project } });
    }
    if (method === 'POST' && url.includes('/archive')) {
      project = { ...project, archived: !project.archived };
      return fakeResponse({ body: { data: project } });
    }
    if (method === 'DELETE' && url.match(/\/projects\/[^/]+$/)) {
      return fakeResponse({ body: { data: { id: 'prj-1', deleted: true } } });
    }
    if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
      const served =
        opts.serverNameAfterConflict !== undefined && conflictHappened
          ? { ...project, name: opts.serverNameAfterConflict }
          : project;
      return fakeResponse({ body: { data: served } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function renderSettings(): void {
  renderWithProviders(
    <Routes>
      <Route path="/projects" element={<div data-testid="projects-list-page" />} />
      <Route path="/projects/:projectId/settings" element={<ProjectSettingsPage />} />
    </Routes>,
    { route: '/projects/prj-1/settings' },
  );
}

const patchProjectCalls = (calls: RecordedCall[]): RecordedCall[] =>
  calls.filter(
    (c) => (c.init?.method ?? 'GET') === 'PATCH' && /\/projects\/[^/]+$/.test(c.url.split('?')[0]),
  );

describe('ProjectSettingsPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('pre-fills the form from the project', async () => {
    stubFetch();
    renderSettings();
    const nameInput = (await screen.findByTestId('settings-name')) as HTMLInputElement;
    expect(nameInput.value).toBe('Apollo');
    expect((screen.getByTestId('settings-status') as HTMLSelectElement).value).toBe('active');
    expect((screen.getByTestId('settings-visibility') as HTMLSelectElement).value).toBe('public');
    expect((screen.getByTestId('settings-lead') as HTMLSelectElement).value).toBe('mem-lead');
  });

  it('saves changes with If-Match optimistic concurrency', async () => {
    const calls = stubFetch();
    renderSettings();
    const user = userEvent.setup();
    const nameInput = await screen.findByTestId('settings-name');
    await user.clear(nameInput);
    await user.type(nameInput, 'Apollo II');
    await user.click(screen.getByTestId('settings-save'));
    await waitFor(() => {
      expect(patchProjectCalls(calls).length).toBe(1);
    });
    const patch = patchProjectCalls(calls)[0];
    const headers = patch.init?.headers as Record<string, string>;
    expect(String(patch.init?.body)).toContain('"name":"Apollo II"');
    // If-Match = 加载时的 updated_at
    const ifMatch = headers['If-Match'] ?? headers['if-match'];
    expect(ifMatch).toBe('2026-07-01T00:00:00Z');
    expect(await screen.findByText('Settings saved.')).toBeDefined();
  });

  it('converges on 409 conflict and shows the conflict toast', async () => {
    const calls = stubFetch({ conflictPatchTimes: 1 });
    renderSettings();
    const user = userEvent.setup();
    const nameInput = await screen.findByTestId('settings-name');
    await user.clear(nameInput);
    await user.type(nameInput, 'Apollo III');
    await user.click(screen.getByTestId('settings-save'));
    await waitFor(() => {
      // 409 → GET 重取 → onConflict 收敛(不再盲重放陈旧 changes),故仅 1 次 PATCH
      expect(patchProjectCalls(calls).length).toBe(1);
    });
    expect(await screen.findByText(/changed elsewhere/)).toBeDefined();
  });

  it('sends null for cleared description (tri-state)', async () => {
    const calls = stubFetch();
    renderSettings();
    const user = userEvent.setup();
    await screen.findByTestId('settings-name');
    const description = screen.getByLabelText('Description');
    await user.clear(description);
    await user.click(screen.getByTestId('settings-save'));
    await waitFor(() => {
      expect(patchProjectCalls(calls).length).toBe(1);
    });
    expect(String(patchProjectCalls(calls)[0].init?.body)).toContain('"description":null');
  });

  it('lists members, adds, changes role and removes', async () => {
    const calls = stubFetch();
    renderSettings();
    const user = userEvent.setup();
    const memberList = await screen.findByTestId('project-member-list');
    expect(within(memberList).getByText('John Smith')).toBeDefined();
    // 改角色
    await user.selectOptions(screen.getByTestId('member-role-mem-2'), 'viewer');
    await waitFor(() => {
      const patches = calls.filter(
        (c) => (c.init?.method ?? 'GET') === 'PATCH' && c.url.includes('/members/'),
      );
      expect(patches.length).toBe(1);
      expect(String(patches[0].init?.body)).toContain('"role":"viewer"');
    });
    // 添加成员
    await user.selectOptions(screen.getByTestId('add-member-select'), 'mem-lead');
    await user.click(screen.getByTestId('add-member-submit'));
    await waitFor(() => {
      const posts = calls.filter(
        (c) => (c.init?.method ?? 'GET') === 'POST' && c.url.includes('/members'),
      );
      expect(posts.length).toBe(1);
    });
    // 移除
    await user.click(screen.getByTestId('member-remove-mem-2'));
    await waitFor(() => {
      const deletes = calls.filter(
        (c) => (c.init?.method ?? 'GET') === 'DELETE' && c.url.includes('/members/'),
      );
      expect(deletes.length).toBe(1);
    });
  });

  it('toggles archive via the danger zone', async () => {
    const calls = stubFetch();
    renderSettings();
    const user = userEvent.setup();
    await screen.findByTestId('settings-name');
    await user.click(screen.getByTestId('settings-archive-toggle'));
    await waitFor(() => {
      expect(
        calls.filter((c) => (c.init?.method ?? 'GET') === 'POST' && c.url.includes('/archive'))
          .length,
      ).toBe(1);
    });
    expect(await screen.findByText('Project archived.')).toBeDefined();
  });

  it('deletes the project after confirmation and navigates away', async () => {
    const calls = stubFetch();
    renderSettings();
    const user = userEvent.setup();
    await screen.findByTestId('settings-name');
    await user.click(screen.getByTestId('settings-delete'));
    expect(await screen.findByTestId('settings-delete-confirm-text')).toBeDefined();
    await user.click(screen.getByTestId('settings-delete-confirm'));
    await waitFor(() => {
      expect(
        calls.filter(
          (c) =>
            (c.init?.method ?? 'GET') === 'DELETE' &&
            /\/projects\/[^/]+$/.test(c.url.split('?')[0]),
        ).length,
      ).toBe(1);
    });
    expect(await screen.findByTestId('projects-list-page')).toBeDefined();
  });

  it('renders the error state when the project fails to load', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/members')) return fakeResponse({ body: { data: [], next_cursor: null } });
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderSettings();
    expect(await screen.findByText('Something went wrong')).toBeDefined();
  });

  it('shows a danger toast when archiving fails', async () => {
    const calls: RecordedCall[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, init });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'GET' && url.includes('/members')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (method === 'POST' && url.includes('/archive')) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
        return fakeResponse({ body: { data: makeProject() } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderSettings();
    const user = userEvent.setup();
    await screen.findByTestId('settings-name');
    await user.click(screen.getByTestId('settings-archive-toggle'));
    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeDefined();
  });

  it('shows a danger toast and closes the dialog when deleting fails', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'GET' && url.includes('/members')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (method === 'DELETE' && url.match(/\/projects\/[^/]+$/)) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
        return fakeResponse({ body: { data: makeProject() } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderSettings();
    const user = userEvent.setup();
    await screen.findByTestId('settings-name');
    await user.click(screen.getByTestId('settings-delete'));
    await user.click(await screen.findByTestId('settings-delete-confirm'));
    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeDefined();
    expect(screen.queryByTestId('projects-list-page')).toBeNull();
  });

  it('shows danger toasts when member add, role change and remove fail', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'GET' && url.includes('/members')) {
        if (url.includes('/projects/')) {
          return fakeResponse({ body: { data: [PROJECT_MEMBER_ENTRY], next_cursor: null } });
        }
        return fakeResponse({ body: { data: ROSTER, next_cursor: null } });
      }
      if (method !== 'GET' && url.includes('/members')) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
        return fakeResponse({ body: { data: makeProject() } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderSettings();
    const user = userEvent.setup();
    await screen.findByTestId('project-member-list');
    // 改角色失败
    await user.selectOptions(screen.getByTestId('member-role-mem-2'), 'viewer');
    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeDefined();
    // 添加失败
    await user.selectOptions(screen.getByTestId('add-member-select'), 'mem-lead');
    await user.click(screen.getByTestId('add-member-submit'));
    await waitFor(() => {
      expect(
        screen.getAllByText('An internal error occurred. Please try again.').length,
      ).toBeGreaterThan(0);
    });
    // 移除失败
    await user.click(screen.getByTestId('member-remove-mem-2'));
    await waitFor(() => {
      expect(
        screen.getAllByText('An internal error occurred. Please try again.').length,
      ).toBeGreaterThan(0);
    });
  });

  it('disables the lead selector for non-lead, non-admin viewers (PJ-H1)', async () => {
    // 工作区 member + 项目 member(非 lead):后端对 lead 改派返回 403,
    // 选择器只读并给出提示(§3.4 / §4.2,后端为权威校验)。
    const meMember = {
      ...ME,
      user: { id: 'usr-mem', email: 'member@acme.com', display_name: 'Member' },
      memberships: [{ ...ME.memberships[0], role: 'member' }],
    };
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: meMember } });
      if (method === 'GET' && url.includes('/members')) {
        if (url.includes('/projects/')) {
          return fakeResponse({ body: { data: [PROJECT_MEMBER_ENTRY], next_cursor: null } });
        }
        return fakeResponse({ body: { data: ROSTER, next_cursor: null } });
      }
      if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
        return fakeResponse({ body: { data: makeProject({ my_role: 'member' }) } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderSettings();
    const leadSelect = (await screen.findByTestId('settings-lead')) as HTMLSelectElement;
    expect(leadSelect.disabled).toBe(true);
    expect(await screen.findByTestId('settings-lead-hint')).toBeDefined();
  });

  it('keeps the lead selector editable for workspace admins (PJ-H1)', async () => {
    stubFetch(); // 工作区 owner + 项目 my_role 'lead'
    renderSettings();
    const leadSelect = (await screen.findByTestId('settings-lead')) as HTMLSelectElement;
    expect(leadSelect.disabled).toBe(false);
    expect(screen.queryByTestId('settings-lead-hint')).toBeNull();
  });

  it('keeps the lead selector editable for a project lead without admin role (PJ-H1)', async () => {
    // 工作区角色仅 member,但项目内是 lead → 仍可改派(§3.4:现 lead 或 admin)。
    const meMember = { ...ME, memberships: [{ ...ME.memberships[0], role: 'member' }] };
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: meMember } });
      if (method === 'GET' && url.includes('/members')) {
        if (url.includes('/projects/')) {
          return fakeResponse({ body: { data: [PROJECT_MEMBER_ENTRY], next_cursor: null } });
        }
        return fakeResponse({ body: { data: ROSTER, next_cursor: null } });
      }
      if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
        return fakeResponse({ body: { data: makeProject({ my_role: 'lead' }) } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderSettings();
    const leadSelect = (await screen.findByTestId('settings-lead')) as HTMLSelectElement;
    expect(leadSelect.disabled).toBe(false);
    expect(screen.queryByTestId('settings-lead-hint')).toBeNull();
  });
});

it('409 收敛后表单对齐服务端态,下次保存不覆盖他人编辑', async () => {
  const calls = stubFetch({ conflictPatchTimes: 1, serverNameAfterConflict: 'Server Edit' });
  const user = userEvent.setup();
  renderSettings();
  const nameInput = (await screen.findByTestId('settings-name')) as HTMLInputElement;
  await user.clear(nameInput);
  await user.type(nameInput, 'Stale Edit');
  await user.click(screen.getByTestId('settings-save'));
  // 409 → 重取(返回 Server Edit)→ onConflict 把表单对齐到服务端态(不盲重放)
  await waitFor(() => expect(patchProjectCalls(calls).length).toBe(1));
  await waitFor(() =>
    expect((screen.getByTestId('settings-name') as HTMLInputElement).value).toBe('Server Edit'),
  );
  // 下一次保存:陈旧值已被服务端态覆盖;改成 Final 后 diff 仅含 Final
  const nameInput2 = screen.getByTestId('settings-name') as HTMLInputElement;
  await user.clear(nameInput2);
  await user.type(nameInput2, 'Final');
  await user.click(screen.getByTestId('settings-save'));
  await waitFor(() => expect(patchProjectCalls(calls).length).toBe(2));
  const lastBody = String(patchProjectCalls(calls)[1].init?.body);
  expect(lastBody).toContain('"name":"Final"');
  expect(lastBody).not.toContain('Stale Edit');
});

describe('ProjectSettingsPage 分支级补充(MES-30 覆盖加固)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('字段为 null 的项目预填回退空串(三态),改名保存 diff 仅含 name', async () => {
    const calls = stubFetch({
      project: makeProject({
        description: null,
        start_date: null,
        target_date: null,
        lead: null,
        lead_member_id: null,
      }),
    });
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId('settings-form');

    expect((screen.getByLabelText('Description') as HTMLInputElement).value).toBe('');
    expect((screen.getByTestId('settings-start-date') as HTMLInputElement).value).toBe('');
    expect((screen.getByTestId('settings-target-date') as HTMLInputElement).value).toBe('');
    expect((screen.getByTestId('settings-lead') as HTMLSelectElement).value).toBe('');

    // null 字段经 ?? '' 预填后未改动 → diff 不含三态字段,仅含 name
    const nameInput = screen.getByTestId('settings-name');
    await user.clear(nameInput);
    await user.type(nameInput, 'Apollo Named');
    await user.click(screen.getByTestId('settings-save'));

    await waitFor(() => expect(patchProjectCalls(calls).length).toBe(1));
    const body = JSON.parse(String(patchProjectCalls(calls)[0].init?.body)) as Record<
      string,
      unknown
    >;
    expect(body).toEqual({ name: 'Apollo Named' });
  });

  it('一次保存涵盖状态/可见性/日期/负责人变更', async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId('settings-form');

    await user.selectOptions(screen.getByTestId('settings-status'), 'paused');
    await user.selectOptions(screen.getByTestId('settings-visibility'), 'private');
    fireEvent.change(screen.getByTestId('settings-start-date'), {
      target: { value: '2026-02-01' },
    });
    fireEvent.change(screen.getByTestId('settings-target-date'), {
      target: { value: '2026-12-31' },
    });
    await user.selectOptions(screen.getByTestId('settings-lead'), 'mem-2');
    await user.click(screen.getByTestId('settings-save'));

    await waitFor(() => expect(patchProjectCalls(calls).length).toBe(1));
    const body = JSON.parse(String(patchProjectCalls(calls)[0].init?.body)) as Record<
      string,
      unknown
    >;
    expect(body).toEqual({
      status: 'paused',
      visibility: 'private',
      start_date: '2026-02-01',
      target_date: '2026-12-31',
      lead_member_id: 'mem-2',
    });
    expect(await screen.findByText('Settings saved.')).toBeDefined();
  });

  it('清空日期与负责人发送 null(三态)', async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId('settings-form');

    fireEvent.change(screen.getByTestId('settings-start-date'), { target: { value: '' } });
    fireEvent.change(screen.getByTestId('settings-target-date'), { target: { value: '' } });
    await user.selectOptions(screen.getByTestId('settings-lead'), '');
    await user.click(screen.getByTestId('settings-save'));

    await waitFor(() => expect(patchProjectCalls(calls).length).toBe(1));
    const body = JSON.parse(String(patchProjectCalls(calls)[0].init?.body)) as Record<
      string,
      unknown
    >;
    expect(body).toEqual({ start_date: null, target_date: null, lead_member_id: null });
  });

  it('无变更点击保存不发 PATCH', async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId('settings-form');

    await user.click(screen.getByTestId('settings-save'));

    expect(patchProjectCalls(calls).length).toBe(0);
    expect(screen.queryByText('Settings saved.')).toBeNull();
  });

  it('/users/me 失败显示错误态', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderSettings();

    expect(await screen.findByText('Something went wrong')).toBeDefined();
  });

  it('无活动工作区时不请求项目(加载守卫)', async () => {
    const calls: RecordedCall[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.includes('/users/me')) {
        return fakeResponse({ body: { data: { ...ME, memberships: [] } } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderSettings();

    await screen.findByText('Loading…');
    expect(calls.some((c) => c.url.includes('/users/me'))).toBe(true);
    expect(calls.some((c) => c.url.includes('/projects/'))).toBe(false);
  });
});

describe('ProjectSettingsPage 加载竞态守卫(MES-30 覆盖加固)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('组件卸载后到达的 /users/me 与项目加载结果被丢弃(cancelled 守卫)', async () => {
    // 场景一:/users/me 成功结果在卸载后到达
    let resolveMe: (response: Response) => void = () => undefined;
    const pendingMe = new Promise<Response>((resolve) => {
      resolveMe = resolve;
    });
    const implMe = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return pendingMe;
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', implMe);
    const first = renderWithProviders(
      <Routes>
        <Route path="/projects" element={<div data-testid="projects-list-page" />} />
        <Route path="/projects/:projectId/settings" element={<ProjectSettingsPage />} />
      </Routes>,
      { route: '/projects/prj-1/settings' },
    );
    await screen.findByText('Loading…');
    first.unmount();
    await act(async () => {
      resolveMe(fakeResponse({ body: { data: ME } }));
    });
    expect(first.container.innerHTML).toBe('');

    // 场景二:项目加载失败结果在卸载后到达
    let rejectProject: (err: Error) => void = () => undefined;
    const pendingProject = new Promise<Response>((_, reject) => {
      rejectProject = reject;
    });
    // 源头 promise 预挂空 catch:无论派生链时序如何,该 rejection 始终有承接者,
    // 杜绝 CI 慢机器上 rejection 在测试结束后才被定结、被 vitest 记为 unhandled
    // rejection(late failure,致 quality job 退出码 1)。组件侧行为不受影响:
    // 每次 fetch 调用经 mock 的 async 包装取得独立派生链,仍由组件自身的
    // cancelled 守卫 .catch 消费(下方断言验证卸载后结果被丢弃)。
    pendingProject.catch(() => undefined);
    const implProject = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/members')) {
        return fakeResponse({ body: { data: ROSTER, next_cursor: null } });
      }
      // 每次调用返回独立派生 promise 并预挂空 catch:即使组件链在任何时序下
      // 尚未承接,该派生链也永不处于无处理器状态(同上,消除 late failure)。
      const branch = pendingProject.then(
        (response) => response,
        (error: unknown) => Promise.reject(error),
      );
      branch.catch(() => undefined);
      return branch;
    }) as typeof fetch;
    vi.stubGlobal('fetch', implProject);
    const second = renderWithProviders(
      <Routes>
        <Route path="/projects" element={<div data-testid="projects-list-page" />} />
        <Route path="/projects/:projectId/settings" element={<ProjectSettingsPage />} />
      </Routes>,
      { route: '/projects/prj-1/settings' },
    );
    await screen.findByText('Loading…');
    second.unmount();
    await act(async () => {
      rejectProject(new Error('late failure'));
      // 充分排空微任务队列,让 rejection 在测试体内确定性地穿完全部派生链
      // (mock async 包装 → client execute/request → Promise.all → 组件 cancelled
      // 守卫 .catch → .finally),而非留到测试结束后才落定。
      for (let i = 0; i < 16; i += 1) {
        await Promise.resolve();
      }
    });
    expect(second.container.innerHTML).toBe('');
  });
});
