import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import {
  acceptInvitation,
  createInvitations,
  listInvitations,
  previewInvitation,
  revokeInvitation,
} from '../invitations';
import type { Invitation } from '../invitations';

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

const INVITATION: Invitation = {
  id: 'inv-1',
  email: null,
  role: 'member',
  status: 'active',
  max_uses: 10,
  used_count: 0,
  expires_at: '2026-08-01T10:00:00Z',
  token_prefix: 'invtk_Ab3Xy9zzzz',
  invited_by: 'mem-1',
  created_at: '2026-07-25T10:00:00Z',
  invite_link: '/invite/invtk_Ab3Xy9zzzzzzzzzzzzzzzzzzzzzzz',
};

describe('invitations API(workspace.md §3 邀请全生命周期)', () => {
  it('createInvitations 链接模式 POST 并返回含 invite_link 的数组', async () => {
    // 后端创建响应为 {data: [...], next_cursor: null};request 取 data。
    const fetchImpl = createMockFetch(201, { data: [INVITATION], next_cursor: null });
    const client = createClient(fetchImpl);

    const result = await createInvitations(client, 'ws-1', { role: 'member', max_uses: 10 });

    expect(result).toEqual([INVITATION]);
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/ws-1/invitations');
    expect(JSON.parse(String(calledInit(fetchImpl).body))).toEqual({
      role: 'member',
      max_uses: 10,
    });
  });

  it('createInvitations 邮箱批量模式透传 emails', async () => {
    const fetchImpl = createMockFetch(201, { data: [INVITATION], next_cursor: null });
    const client = createClient(fetchImpl);

    await createInvitations(client, 'ws-1', { emails: ['a@b.com'], role: 'admin' });

    expect(JSON.parse(String(calledInit(fetchImpl).body))).toEqual({
      emails: ['a@b.com'],
      role: 'admin',
    });
  });

  it('createInvitations 超上限返回 422 invitation_limits_exceeded 与 caps details', async () => {
    const fetchImpl = createMockFetch(422, {
      error: {
        code: 'invitation_limits_exceeded',
        message: 'limits exceeded',
        details: { max_uses: 500, cap: 100 },
      },
    });
    const client = createClient(fetchImpl);

    await expect(
      createInvitations(client, 'ws-1', { max_uses: 500 }),
    ).rejects.toMatchObject({
      status: 422,
      code: 'invitation_limits_exceeded',
      details: { max_uses: 500, cap: 100 },
    });
  });

  it('createInvitations 重复邮箱返回 409 conflict 与 email details', async () => {
    const fetchImpl = createMockFetch(409, {
      error: { code: 'conflict', message: 'conflict', details: { email: 'a@b.com' } },
    });
    const client = createClient(fetchImpl);

    await expect(
      createInvitations(client, 'ws-1', { emails: ['a@b.com'] }),
    ).rejects.toMatchObject({ status: 409, code: 'conflict', details: { email: 'a@b.com' } });
  });

  it('listInvitations 返回游标分页列表', async () => {
    const fetchImpl = createMockFetch(200, { data: [INVITATION], next_cursor: 'c2' });
    const client = createClient(fetchImpl);

    const result = await listInvitations(client, 'ws-1', { cursor: 'c1' });

    expect(result).toEqual({ data: [INVITATION], next_cursor: 'c2' });
    expect(calledUrl(fetchImpl)).toContain('cursor=c1');
  });

  it('revokeInvitation DELETE 返回 revoked 邀请', async () => {
    const revoked = { ...INVITATION, status: 'revoked', invite_link: undefined };
    const fetchImpl = createMockFetch(200, { data: revoked });
    const client = createClient(fetchImpl);

    const result = await revokeInvitation(client, 'ws-1', 'inv-1');

    expect(result.status).toBe('revoked');
    expect(calledInit(fetchImpl).method).toBe('DELETE');
    expect(calledUrl(fetchImpl)).toContain('/invitations/inv-1');
  });

  it('revokeInvitation 非 active 链接返回 409 conflict 与当前状态', async () => {
    const fetchImpl = createMockFetch(409, {
      error: { code: 'conflict', message: 'conflict', details: { status: 'expired' } },
    });
    const client = createClient(fetchImpl);

    await expect(revokeInvitation(client, 'ws-1', 'inv-1')).rejects.toMatchObject({
      status: 409,
      code: 'conflict',
      details: { status: 'expired' },
    });
  });

  it('previewInvitation 公开端点以 query 传 token,有效时返回工作区信息', async () => {
    const fetchImpl = createMockFetch(200, {
      data: {
        valid: true,
        workspace_name: 'Acme',
        workspace_logo_url: null,
        role: 'member',
        expires_at: '2026-08-01T10:00:00Z',
      },
    });
    const client = createClient(fetchImpl);

    const result = await previewInvitation(client, 'invtk_x');

    expect(result).toMatchObject({ valid: true, workspace_name: 'Acme' });
    expect(calledUrl(fetchImpl)).toContain('/api/v1/invitations/preview?token=invtk_x');
  });

  it('previewInvitation 无效时返回 reason 枚举(恒 200)', async () => {
    const fetchImpl = createMockFetch(200, { data: { valid: false, reason: 'expired' } });
    const client = createClient(fetchImpl);

    await expect(previewInvitation(client, 'invtk_x')).resolves.toEqual({
      valid: false,
      reason: 'expired',
    });
  });

  it('acceptInvitation 成功返回名册条目与工作区', async () => {
    const fetchImpl = createMockFetch(200, {
      data: {
        member: { id: 'mem-9', role: 'member', status: 'active' },
        workspace: { id: 'ws-1', name: 'Acme', slug: 'acme' },
      },
    });
    const client = createClient(fetchImpl);

    const result = await acceptInvitation(client, 'invtk_x');

    expect(result.workspace.slug).toBe('acme');
    expect(JSON.parse(String(calledInit(fetchImpl).body))).toEqual({ token: 'invtk_x' });
  });

  it.each(['not_found', 'expired', 'exhausted', 'revoked'] as const)(
    'acceptInvitation 失败 422 invitation_invalid,reason=%s 在 details',
    async (reason) => {
      const fetchImpl = createMockFetch(422, {
        error: {
          code: 'invitation_invalid',
          message: 'invitation is not valid',
          details: { reason },
        },
      });
      const client = createClient(fetchImpl);

      await expect(acceptInvitation(client, 'invtk_x')).rejects.toMatchObject({
        status: 422,
        code: 'invitation_invalid',
        details: { reason },
      });
    },
  );
});
