import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import { listMembers, updateMemberRole } from '../members';
import type { MemberSummary } from '../members';

function createMockFetch(status: number, body: unknown): typeof fetch {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

function createClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({
    baseUrl: 'http://localhost:8901',
    getToken: () => 'test-token',
    fetchImpl,
  });
}

function calledUrl(fetchImpl: typeof fetch): string {
  return ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string])[0];
}

function calledInit(fetchImpl: typeof fetch): RequestInit {
  return ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit])[1];
}

const MEMBER: MemberSummary = {
  id: 'mem-1',
  member_type: 'human',
  role: 'member',
  status: 'active',
  display_name: 'Jane',
  joined_at: '2026-07-25T10:00:00Z',
};

describe('members API(member.md §3 名册消费契约)', () => {
  it('listMembers 游标分页请求名册端点', async () => {
    const fetchImpl = createMockFetch(200, { data: [MEMBER], next_cursor: null });
    const client = createClient(fetchImpl);

    const result = await listMembers(client, 'ws-1', { limit: 20 });

    expect(result.data).toEqual([MEMBER]);
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/ws-1/members?limit=20');
  });

  it('listMembers 端点未就绪时透传 404(调用方降级)', async () => {
    const fetchImpl = createMockFetch(404, {
      error: { code: 'not_found', message: 'not found' },
    });
    const client = createClient(fetchImpl);

    await expect(listMembers(client, 'ws-1')).rejects.toMatchObject({
      status: 404,
      code: 'not_found',
    });
  });

  it('updateMemberRole PATCH 角色并返回更新后条目', async () => {
    const promoted = { ...MEMBER, role: 'admin' };
    const fetchImpl = createMockFetch(200, { data: promoted });
    const client = createClient(fetchImpl);

    const result = await updateMemberRole(client, 'ws-1', 'mem-1', 'admin');

    expect(result.role).toBe('admin');
    expect(calledInit(fetchImpl).method).toBe('PATCH');
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/ws-1/members/mem-1');
    expect(JSON.parse(String(calledInit(fetchImpl).body))).toEqual({ role: 'admin' });
  });

  it('updateMemberRole 最后一个 owner 降级返回 409 last_owner', async () => {
    const fetchImpl = createMockFetch(409, {
      error: { code: 'last_owner', message: 'last owner' },
    });
    const client = createClient(fetchImpl);

    await expect(updateMemberRole(client, 'ws-1', 'mem-1', 'member')).rejects.toMatchObject({
      status: 409,
      code: 'last_owner',
    });
  });

  it('updateMemberRole agent 提为 owner 返回 409 agent_owner_not_allowed', async () => {
    const fetchImpl = createMockFetch(409, {
      error: { code: 'agent_owner_not_allowed', message: 'agent cannot be owner' },
    });
    const client = createClient(fetchImpl);

    await expect(updateMemberRole(client, 'ws-1', 'mem-1', 'owner')).rejects.toMatchObject({
      status: 409,
      code: 'agent_owner_not_allowed',
    });
  });
});
