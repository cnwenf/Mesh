/**
 * 成员名册 API 契约层测试:路径/方法/请求体与 member.md §3.1 一致,包络解包正确。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  activeWorkspace,
  createInvitation,
  fetchMe,
  getMember,
  listAvailableAgents,
  listMembers,
  listProjectAccess,
  reassignIssues,
  removeMember,
  updateOwnProfile,
  updateMember,
} from '../api';

function makeClient() {
  const request = vi.fn(async () => ({}));
  const list = vi.fn(async (): Promise<{ data: unknown[]; next_cursor: string | null }> => ({
    data: [],
    next_cursor: null,
  }));
  const client = { request, list } as unknown as MeshApiClient;
  return { client, request, list };
}

describe('activeWorkspace', () => {
  it('返回首个成员身份;空列表返回 null', () => {
    const memberships = [{ workspace_id: 'a' }, { workspace_id: 'b' }] as never;
    expect(activeWorkspace(memberships)?.workspace_id).toBe('a');
    expect(activeWorkspace([])).toBeNull();
  });
});

describe('成员名册 API 路径与包络', () => {
  it('fetchMe 命中 GET /users/me 并返回 data', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ user: { id: 'u' }, memberships: [] });
    const me = await fetchMe(client);
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/users/me');
    expect(me.user.id).toBe('u');
  });

  it('updateOwnProfile 以 PATCH 写入当前用户资料并返回更新结果', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({
      id: 'u',
      display_name: 'Jane Doe',
      avatar_url: 'https://cdn.example/avatar.png',
    });
    const user = await updateOwnProfile(client, {
      display_name: 'Jane Doe',
      avatar_url: 'https://cdn.example/avatar.png',
    });
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/users/me', {
      body: {
        display_name: 'Jane Doe',
        avatar_url: 'https://cdn.example/avatar.png',
      },
    });
    expect(user.display_name).toBe('Jane Doe');
  });

  it('listMembers 透传筛选查询并解包 {data,next_cursor}', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [{ id: 'm1' }], next_cursor: 'c' });
    const result = await listMembers(client, 'ws-1', {
      memberType: 'agent',
      status: 'active',
      q: 'bot',
      limit: 10,
      cursor: 'prev',
    });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/members', {
      query: { member_type: 'agent', status: 'active', q: 'bot', limit: 10, cursor: 'prev' },
    });
    expect(result.data).toEqual([{ id: 'm1' }]);
    expect(result.nextCursor).toBe('c');
  });

  it('getMember 命中成员详情路径', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'm1', counts: { open_issues_assigned: 0 } });
    const detail = await getMember(client, 'ws-1', 'm1');
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/workspaces/ws-1/members/m1');
    expect(detail.counts.open_issues_assigned).toBe(0);
  });

  it('updateMember 以 PATCH 提交 role/status/display_override', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'm1' });
    await updateMember(client, 'ws-1', 'm1', { role: 'admin' });
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/workspaces/ws-1/members/m1', {
      body: { role: 'admin' },
    });
  });

  it('removeMember 携带 reassign_to 查询参数', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ removed: true, reassigned_issues: 2 });
    const result = await removeMember(client, 'ws-1', 'm1', 'm2');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/workspaces/ws-1/members/m1', {
      query: { reassign_to: 'm2' },
    });
    expect(result.reassigned_issues).toBe(2);
  });

  it('reassignIssues POST /members/reassign', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ reassigned_issues: 3 });
    const result = await reassignIssues(client, 'ws-1', 'from', 'to');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/members/reassign', {
      body: { from_member_id: 'from', to_member_id: 'to' },
    });
    expect(result.reassigned_issues).toBe(3);
  });

  it('listAvailableAgents 命中 agents/available 并解包 data', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [], next_cursor: null });
    const agents = await listAvailableAgents(client, 'ws-1');
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/agents/available');
    expect(agents).toEqual([]);
  });

  it('createInvitation 返回首个邀请条目', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce([{ invite_link: '/invite/tok' }]);
    const result = await createInvitation(client, 'ws-1', 'a@b.com', 'member');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/invitations', {
      body: { emails: ['a@b.com'], role: 'member' },
    });
    expect(result.invite_link).toBe('/invite/tok');
  });

  it('listProjectAccess 命中 project-access 并解包 data', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [{ id: 'pa' }], next_cursor: null });
    const rows = await listProjectAccess(client, 'ws-1', 'm1');
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/members/m1/project-access');
    expect(rows).toEqual([{ id: 'pa' }]);
  });
});
