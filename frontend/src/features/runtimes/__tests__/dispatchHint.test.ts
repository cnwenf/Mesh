/**
 * dispatchHint 单元测试(L186,README §6.12 专项恢复入口「agent 无可用 runtime」):
 * agent 执行需有 runtime 可认领(§6.4);分派时工作区在线 runtime 探测——
 * 有在线 → true;空列表 → false;探测失败(网络/非 2xx)→ true(不确定不误报,
 * 提示仅在确定无在线 runtime 时出现,绝不阻断分派)。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { workspaceHasOnlineRuntime } from '../dispatchHint';

function client(): MeshApiClient {
  return new MeshApiClient({ baseUrl: 'http://api.test', getToken: () => 'token-1' });
}

function runtimeRow(id: string): Record<string, unknown> {
  return {
    id,
    workspace_id: 'ws-1',
    name: 'rt-1',
    kind: 'self_hosted',
    status: 'online',
    host_name: 'host-1',
    ip_address: '10.0.0.1',
    labels: [],
    capabilities: [],
    max_concurrency: 1,
    running_executions: 0,
    waiting_executions: 0,
    version: '1.0.0',
    last_seen_at: '2026-08-07T00:00:00Z',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('workspaceHasOnlineRuntime', () => {
  it('列表含在线 runtime → true,且以 status=online&limit=1 探测', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return fakeResponse({ body: { data: [runtimeRow('rt-1')], next_cursor: null } });
    }) as typeof fetch);

    await expect(workspaceHasOnlineRuntime(client(), 'ws-1')).resolves.toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain('/workspaces/ws-1/runtimes');
    expect(calls[0]).toContain('status=online');
    expect(calls[0]).toContain('limit=1');
  });

  it('空列表(确定无在线 runtime)→ false', async () => {
    vi.stubGlobal('fetch', (async () =>
      fakeResponse({ body: { data: [], next_cursor: null } })) as typeof fetch);

    await expect(workspaceHasOnlineRuntime(client(), 'ws-1')).resolves.toBe(false);
  });

  it('网络失败 → true(不确定不误报)', async () => {
    vi.stubGlobal('fetch', (async () => {
      throw new Error('boom');
    }) as typeof fetch);

    await expect(workspaceHasOnlineRuntime(client(), 'ws-1')).resolves.toBe(true);
  });

  it('非 2xx(如 403)→ true(不确定不误报)', async () => {
    vi.stubGlobal('fetch', (async () =>
      fakeResponse({
        status: 403,
        body: { error: { code: 'forbidden', message: 'denied' } },
      })) as typeof fetch);

    await expect(workspaceHasOnlineRuntime(client(), 'ws-1')).resolves.toBe(true);
  });
});
