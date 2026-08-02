/**
 * approvals/api 契约测试:路径 / 查询参数 / 决定请求体字段(`comment`,
 * 后端 ApprovalDecideRequest)精确镜像 runtime/routes.py。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { approveApproval, getApproval, listApprovals, rejectApproval } from '../api';

function makeClient() {
  return {
    request: vi.fn(async () => ({ id: 'ap1' })),
    list: vi.fn(async () => ({ data: [], next_cursor: null })),
  } as unknown as MeshApiClient;
}

describe('listApprovals', () => {
  it('hits the workspace approvals path with role/status/limit query', async () => {
    const client = makeClient();
    await listApprovals(client, 'ws1', { role: 'mine', status: 'pending', limit: 50 });
    expect(client.list).toHaveBeenCalledWith('/api/v1/workspaces/ws1/approvals', {
      query: { role: 'mine', status: 'pending', limit: 50 },
    });
  });

  it('returns the list envelope untouched', async () => {
    const client = {
      request: vi.fn(),
      list: vi.fn(async () => ({ data: [{ id: 'ap1' }], next_cursor: null })),
    } as unknown as MeshApiClient;
    const envelope = await listApprovals(client, 'ws1');
    expect(envelope.data).toHaveLength(1);
    expect(envelope.next_cursor).toBeNull();
  });
});

describe('getApproval', () => {
  it('requests the single approval object', async () => {
    const client = makeClient();
    await getApproval(client, 'ws1', 'ap1');
    expect(client.request).toHaveBeenCalledWith('GET', '/api/v1/workspaces/ws1/approvals/ap1');
  });

  it('encodes the approval id as one path segment', async () => {
    const client = makeClient();
    await getApproval(client, 'ws1', 'approval/with space');
    expect(client.request).toHaveBeenCalledWith(
      'GET',
      '/api/v1/workspaces/ws1/approvals/approval%2Fwith%20space',
    );
  });
});

describe('approveApproval / rejectApproval', () => {
  it('posts comment body to /approve', async () => {
    const client = makeClient();
    await approveApproval(client, 'ws1', 'ap1', { comment: 'lgtm' });
    expect(client.request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/workspaces/ws1/approvals/ap1/approve',
      { body: { comment: 'lgtm' } },
    );
  });

  it('posts null comment when omitted (backend field is nullable)', async () => {
    const client = makeClient();
    await rejectApproval(client, 'ws1', 'ap1');
    expect(client.request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/workspaces/ws1/approvals/ap1/reject',
      { body: { comment: null } },
    );
  });
});
