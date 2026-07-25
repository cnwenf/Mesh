/**
 * ProjectSettingsPage + ProjectMembersSection 测试(project.md §4.1 设置侧栏)。
 * 表单保存经 useOptimisticMutation(PATCH 携 If-Match: updated_at);409 conflict
 * 自动收敛(重取 + 重放)并提示 conflictToast;成员管理(列表/添加/改角色/移除);
 * 危险区归档切换与删除二次确认。
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
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
}

function stubFetch(opts: StubOptions = {}) {
  const calls: RecordedCall[] = [];
  let project = makeProject();
  let patchFailures = opts.conflictPatchTimes ?? 0;
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
      return fakeResponse({ body: { data: project } });
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
    (c) =>
      (c.init?.method ?? 'GET') === 'PATCH' && /\/projects\/[^/]+$/.test(c.url.split('?')[0]),
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
    expect((screen.getByTestId('settings-visibility') as HTMLSelectElement).value).toBe(
      'public',
    );
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
      // 409 → GET 重取 → 以服务端版本重放 PATCH,共 2 次 PATCH
      expect(patchProjectCalls(calls).length).toBe(2);
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
            (c.init?.method ?? 'GET') === 'DELETE' && /\/projects\/[^/]+$/.test(c.url.split('?')[0]),
        ).length,
      ).toBe(1);
    });
    expect(await screen.findByTestId('projects-list-page')).toBeDefined();
  });
});
