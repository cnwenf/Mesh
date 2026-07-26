import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import { listAuditLogs } from '../audit';
import type { AuditLogEntry } from '../audit';

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

const ENTRY: AuditLogEntry = {
  id: 'aud-1',
  actor_member_id: 'mem-1',
  actor_kind: 'member',
  action: 'token.created',
  resource_type: 'api_token',
  resource_id: 'tok-1',
  ip_address: '127.0.0.1',
  metadata: {},
  created_at: '2026-07-25T10:00:00Z',
};

describe('audit API(auth.md §3.3 / §5.3)', () => {
  it('listAuditLogs 返回审计条目(游标包络)', async () => {
    const fetchImpl = createMockFetch(200, { data: [ENTRY], next_cursor: null });
    const result = await listAuditLogs(createClient(fetchImpl), 'ws-1');
    expect(result.data).toEqual([ENTRY]);
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/ws-1/audit-logs');
  });

  it('listAuditLogs 透传 action/actor/before/after 过滤(§5.3 时间范围)', async () => {
    const fetchImpl = createMockFetch(200, { data: [], next_cursor: null });
    await listAuditLogs(createClient(fetchImpl), 'ws-1', {
      action: 'token.created',
      actor_member_id: 'mem-1',
      before: '2026-07-26T00:00:00Z',
      after: '2026-07-24T00:00:00Z',
      limit: 10,
    });
    const url = calledUrl(fetchImpl);
    expect(url).toContain('action=token.created');
    expect(url).toContain('actor_member_id=mem-1');
    expect(url).toContain('before=2026-07-26T00%3A00%3A00Z');
    expect(url).toContain('after=2026-07-24T00%3A00%3A00Z');
    expect(url).toContain('limit=10');
  });

  it('listAuditLogs 省略 undefined 过滤项', async () => {
    const fetchImpl = createMockFetch(200, { data: [], next_cursor: null });
    await listAuditLogs(createClient(fetchImpl), 'ws-1', {});
    const url = calledUrl(fetchImpl);
    expect(url).not.toContain('action=');
    expect(url).not.toContain('before=');
  });
});
