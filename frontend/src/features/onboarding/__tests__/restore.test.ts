/**
 * 帮助菜单 / 命令面板共用恢复编排测试(onboarding.md §4.2 流程 3)。
 */
import { beforeEach, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, failingFetch, stubFetch } from '../../../api/__tests__/fetchStub';
import { restoreActiveOnboarding } from '../restore';

const ME = {
  user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'WS',
      workspace_slug: 'ws',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

it('restores the active workspace checklist and returns true', async () => {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: { data: { id: 'obs-1', dismissed_at: null } } }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  await expect(restoreActiveOnboarding(client)).resolves.toBe(true);
  expect(stub.calls[1].url).toBe('http://api/api/v1/onboarding/restore?workspace_id=ws-1');
});

it('is a no-op without an active workspace', async () => {
  const stub = stubFetch(fakeResponse({ body: { data: { ...ME, memberships: [] } } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
  await expect(restoreActiveOnboarding(client)).resolves.toBe(false);
  expect(stub.calls).toHaveLength(1);
});

it('propagates network failures to the caller', async () => {
  vi.stubGlobal('fetch', failingFetch());
  await expect(restoreActiveOnboarding(client)).rejects.toThrow();
});
