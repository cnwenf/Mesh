/**
 * ProjectMembersSection 分支级测试(project.md §2.2):成员加载渲染(member 为 null
 * 回退 member_id)、多成员角色切换(map 非命中分支)、添加成员(候选过滤:排除已入项
 * 与非 active)、移除成员、移除非 API 失败 toast。
 *
 * fetch 桩是「有状态服务端」:GET 返回随 PATCH/POST/DELETE 演进的成员集,
 * 与真实服务端一致(组件在 toast 触发重渲染后会经 load() 重新 GET)。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import type { RecordedCall } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import type { MemberSummary } from '../../members/types';
import { ProjectMembersSection } from '../ProjectMembersSection';
import type { ProjectMemberEntry } from '../types';

function makeEntry(overrides: Partial<ProjectMemberEntry> = {}): ProjectMemberEntry {
  return {
    id: 'pm-1',
    project_id: 'prj-1',
    member_id: 'mem-1',
    member: { id: 'mem-1', name: 'Alice', member_type: 'human' },
    role: 'member',
    created_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function makeRosterMember(overrides: Partial<MemberSummary> = {}): MemberSummary {
  return {
    id: 'mem-3',
    member_type: 'human',
    role: 'member',
    status: 'active',
    display_name: 'Carol',
    joined_at: null,
    profile: null,
    ...overrides,
  };
}

interface StubOptions {
  readonly entries?: readonly ProjectMemberEntry[];
  /** DELETE 行为覆盖(模拟失败) */
  readonly removeImpl?: typeof fetch;
}

function stubMembersApi(opts: StubOptions = {}): RecordedCall[] {
  const calls: RecordedCall[] = [];
  // 服务端状态:随写操作演进,GET 总返回当前快照
  let members: ProjectMemberEntry[] = [...(opts.entries ?? [makeEntry()])];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, init });
    if (method === 'GET' && url.includes('/members')) {
      return fakeResponse({ body: { data: [...members], next_cursor: null } });
    }
    if (method === 'PATCH' && url.includes('/members/')) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      const memberId = url.split('/members/')[1];
      const updated = {
        ...(members.find((entry) => entry.member_id === memberId) ?? makeEntry()),
        member_id: memberId,
        role: body.role as ProjectMemberEntry['role'],
      };
      members = members.map((entry) => (entry.member_id === memberId ? updated : entry));
      return fakeResponse({ body: { data: updated } });
    }
    if (method === 'POST' && url.includes('/members')) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      const created = makeEntry({
        id: 'pm-new',
        member_id: String(body.member_id),
        member: { id: String(body.member_id), name: 'Carol', member_type: 'human' },
        role: (body.role as ProjectMemberEntry['role']) ?? 'member',
      });
      members = [...members, created];
      return fakeResponse({ status: 201, body: { data: created } });
    }
    if (method === 'DELETE' && url.includes('/members/')) {
      if (opts.removeImpl !== undefined) return opts.removeImpl(input, init);
      const memberId = url.split('/members/')[1];
      members = members.filter((entry) => entry.member_id !== memberId);
      return fakeResponse({ body: { data: { id: memberId, deleted: true } } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function renderSection(roster: readonly MemberSummary[]): void {
  renderWithProviders(
    <ProjectMembersSection
      client={new MeshApiClient({ baseUrl: '', getToken: () => 'tok-test' })}
      projectId="prj-1"
      roster={roster}
    />,
  );
}

describe('ProjectMembersSection', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders member rows and falls back to member_id when member is null', async () => {
    stubMembersApi({
      entries: [
        makeEntry(),
        makeEntry({ id: 'pm-2', member_id: 'mem-orphan', member: null, role: 'viewer' }),
      ],
    });
    renderSection([]);

    expect(await screen.findByTestId('member-row-mem-1')).toBeDefined();
    expect(screen.getByText('Alice')).toBeDefined();
    expect(screen.getByTestId('member-row-mem-orphan').textContent).toContain('mem-orphan');
  });

  it('renders the empty state when the project has no members', async () => {
    stubMembersApi({ entries: [] });
    renderSection([]);
    expect(await screen.findByText('No project members yet.')).toBeDefined();
  });

  it('updates the role of one member without touching the other', async () => {
    const calls = stubMembersApi({
      entries: [makeEntry(), makeEntry({ id: 'pm-2', member_id: 'mem-2', role: 'viewer' })],
    });
    const user = userEvent.setup();
    renderSection([]);
    await screen.findByTestId('member-row-mem-1');

    await user.selectOptions(screen.getByTestId('member-role-mem-1'), 'lead');

    await waitFor(() => {
      const patches = calls.filter((c) => c.init?.method === 'PATCH');
      expect(patches.length).toBe(1);
      expect(patches[0].url).toContain('/members/mem-1');
    });
    expect(await screen.findByText('Project role updated.')).toBeDefined();
    await waitFor(() =>
      expect((screen.getByTestId('member-role-mem-1') as HTMLSelectElement).value).toBe('lead'),
    );
    expect((screen.getByTestId('member-role-mem-2') as HTMLSelectElement).value).toBe('viewer');
  });

  it('only offers active non-member candidates and adds the picked member', async () => {
    const calls = stubMembersApi({ entries: [makeEntry()] });
    const user = userEvent.setup();
    renderSection([
      makeRosterMember({ id: 'mem-1', display_name: 'Alice (already in)' }),
      makeRosterMember({ id: 'mem-off', display_name: 'Offboarded', status: 'disabled' }),
      makeRosterMember({ id: 'mem-3', display_name: 'Carol' }),
    ]);
    await screen.findByTestId('member-row-mem-1');

    const addSelect = screen.getByTestId('add-member-select') as HTMLSelectElement;
    expect(Array.from(addSelect.options).map((option) => option.value)).toEqual(['', 'mem-3']);

    await user.selectOptions(addSelect, 'mem-3');
    await user.selectOptions(screen.getByTestId('add-member-role'), 'viewer');
    await user.click(screen.getByTestId('add-member-submit'));

    await waitFor(() => {
      const posts = calls.filter((c) => c.init?.method === 'POST');
      expect(posts.length).toBe(1);
    });
    const body = JSON.parse(
      String(calls.find((c) => c.init?.method === 'POST')?.init?.body),
    ) as Record<string, unknown>;
    expect(body).toEqual({ member_id: 'mem-3', role: 'viewer' });
    expect(await screen.findByTestId('member-row-mem-3')).toBeDefined();
    expect(await screen.findByText('Member added to the project.')).toBeDefined();
    await waitFor(() =>
      expect((screen.getByTestId('add-member-select') as HTMLSelectElement).value).toBe(''),
    );
  });

  it('keeps the add button disabled until a candidate is picked', async () => {
    stubMembersApi({ entries: [makeEntry()] });
    renderSection([makeRosterMember()]);
    await screen.findByTestId('member-row-mem-1');
    expect((screen.getByTestId('add-member-submit') as HTMLButtonElement).disabled).toBe(true);
  });

  it('removes a member and toasts on success', async () => {
    const calls = stubMembersApi({ entries: [makeEntry()] });
    const user = userEvent.setup();
    renderSection([]);
    await screen.findByTestId('member-row-mem-1');

    await user.click(screen.getByTestId('member-remove-mem-1'));

    await waitFor(() => {
      expect(calls.filter((c) => c.init?.method === 'DELETE').length).toBe(1);
    });
    expect(await screen.findByText('Member removed from the project.')).toBeDefined();
    await waitFor(() => expect(screen.queryByTestId('member-row-mem-1')).toBeNull());
  });

  it('toasts the network error and keeps the member when removal fails', async () => {
    stubMembersApi({
      entries: [makeEntry()],
      removeImpl: (async () => {
        throw new TypeError('network down');
      }) as typeof fetch,
    });
    const user = userEvent.setup();
    renderSection([]);
    await screen.findByTestId('member-row-mem-1');

    await user.click(screen.getByTestId('member-remove-mem-1'));

    expect(
      await screen.findByText('Network error. Please check your connection and try again.'),
    ).toBeDefined();
    expect(screen.getByTestId('member-row-mem-1')).toBeDefined();
  });
});
