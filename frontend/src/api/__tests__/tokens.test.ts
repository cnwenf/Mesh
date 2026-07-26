import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import { createToken, listTokens, revokeToken, tokenWhoami } from '../tokens';
import type { CreatedApiToken } from '../tokens';

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

const CREATED: CreatedApiToken = {
  id: 'tok-1',
  name: 'ci',
  prefix: 'mesh_pat_Ab3',
  scopes: ['issue:read'],
  role_override: null,
  owner_member_id: 'mem-1',
  expires_at: null,
  last_used_at: null,
  revoked_at: null,
  created_at: '2026-07-25T10:00:00Z',
  token: 'mesh_pat_Ab3-plaintext-shown-once',
};

describe('tokens API(auth.md §3.2 PAT/agent 凭证)', () => {
  it('listTokens 请求工作区 token 端点并返回 data', async () => {
    const fetchImpl = createMockFetch(200, { data: [CREATED], next_cursor: null });
    const client = createClient(fetchImpl);
    const result = await listTokens(client, 'ws-1');
    expect(result).toEqual([CREATED]);
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/ws-1/api-tokens');
  });

  it('createToken 发送 body 并返回一次性明文 token', async () => {
    const fetchImpl = createMockFetch(201, { data: CREATED });
    const client = createClient(fetchImpl);
    const result = await createToken(client, 'ws-1', {
      name: 'ci',
      scopes: ['issue:read'],
      role_override: null,
    });
    expect(result.token).toBe('mesh_pat_Ab3-plaintext-shown-once');
    const body = JSON.parse((calledInit(fetchImpl).body as string) ?? '{}');
    expect(body).toEqual({ name: 'ci', scopes: ['issue:read'], role_override: null });
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/ws-1/api-tokens');
  });

  it('revokeToken 发 DELETE 到具体 token', async () => {
    const fetchImpl = createMockFetch(200, { data: { status: 'ok' } });
    const client = createClient(fetchImpl);
    await revokeToken(client, 'ws-1', 'tok-1');
    expect(calledInit(fetchImpl).method).toBe('DELETE');
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/ws-1/api-tokens/tok-1');
  });

  it('tokenWhoami 以 PAT 自身作 Bearer(独立客户端)', async () => {
    const fetchImpl = createMockFetch(200, {
      data: {
        token_id: 'tok-1',
        workspace_id: 'ws-1',
        owner_member_id: 'mem-1',
        member_type: 'human',
        role: 'member',
        scopes: ['issue:read'],
        name: 'ci',
      },
    });
    const result = await tokenWhoami('http://localhost:8901', 'mesh_pat_secret', fetchImpl);
    expect(result.role).toBe('member');
    // 独立客户端用 PAT 作 Bearer,而非会话 access token
    const init = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const headers = init[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer mesh_pat_secret');
  });
});
