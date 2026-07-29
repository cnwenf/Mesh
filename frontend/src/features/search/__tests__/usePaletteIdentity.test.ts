/**
 * 面板身份解析单测(§2.1 隔离键输入 / §3.4 workspace 解析序)。
 */
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api/client';
import {
  ANONYMOUS_USER_ID,
  DEFAULT_WORKSPACE_ID,
  resetPaletteIdentityCache,
  usePaletteIdentity,
  workspaceSlugFromPath,
} from '../usePaletteIdentity';

function clientReturning(body: unknown, status = 200): MeshApiClient {
  const fetchImpl = vi.fn(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
    ),
  );
  return new MeshApiClient({ baseUrl: 'http://api.test', getToken: () => null, fetchImpl });
}

beforeEach(() => {
  resetPaletteIdentityCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('workspaceSlugFromPath', () => {
  it('从 /w/{slug}/… 解析 slug(含百分号解码)', () => {
    expect(workspaceSlugFromPath('/w/acme/board')).toBe('acme');
    expect(workspaceSlugFromPath('/w/a%20b')).toBe('a b');
    expect(workspaceSlugFromPath('/board')).toBeNull();
    expect(workspaceSlugFromPath('/')).toBeNull();
  });
});

describe('usePaletteIdentity', () => {
  it('me 成功:user.id + 首个成员身份 workspace_id(无 URL slug 时)', async () => {
    const client = clientReturning({
      data: {
        user: { id: 'u9', email: 'a@b.c', display_name: 'A' },
        memberships: [{ workspace_id: 'ws-9', workspace_name: 'W', workspace_slug: 'w', role: 'member', status: 'active', joined_at: null }],
      },
    });
    const { result } = renderHook(() => usePaletteIdentity({ client, pathname: '/' }));
    await waitFor(() => expect(result.current.userId).toBe('u9'));
    expect(result.current.workspaceId).toBe('ws-9');
  });

  it('URL slug 优先于成员身份(§3.4 解析序 ①>②)', async () => {
    const client = clientReturning({
      data: {
        user: { id: 'u9', email: 'a@b.c', display_name: 'A' },
        memberships: [{ workspace_id: 'ws-9', workspace_name: 'W', workspace_slug: 'w', role: 'member', status: 'active', joined_at: null }],
      },
    });
    const { result } = renderHook(() => usePaletteIdentity({ client, pathname: '/w/acme/board' }));
    await waitFor(() => expect(result.current.userId).toBe('u9'));
    expect(result.current.workspaceId).toBe('acme');
  });

  it('me 失败 → 回退 anon / default(不阻断)', async () => {
    const client = clientReturning({ error: { code: 'unauthorized', message: 'x' } }, 401);
    const { result } = renderHook(() => usePaletteIdentity({ client, pathname: '/' }));
    await waitFor(() => expect(result.current.userId).toBe(ANONYMOUS_USER_ID));
    expect(result.current.workspaceId).toBe(DEFAULT_WORKSPACE_ID);
  });

  it('无成员身份且无 slug → default', async () => {
    const client = clientReturning({
      data: { user: { id: 'u1', email: 'a@b.c', display_name: 'A' }, memberships: [] },
    });
    const { result } = renderHook(() => usePaletteIdentity({ client, pathname: '/' }));
    await waitFor(() => expect(result.current.userId).toBe('u1'));
    expect(result.current.workspaceId).toBe(DEFAULT_WORKSPACE_ID);
  });

  it('me 请求模块级缓存:多次 hook 挂载不重复请求', async () => {
    const fetchImpl = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({ data: { user: { id: 'u1', email: 'e', display_name: 'd' }, memberships: [] } }),
          { status: 200 },
        ),
      ),
    );
    const client = new MeshApiClient({ baseUrl: 'http://api.test', getToken: () => null, fetchImpl });
    const first = renderHook(() => usePaletteIdentity({ client, pathname: '/' }));
    await waitFor(() => expect(first.result.current.userId).toBe('u1'));
    const second = renderHook(() => usePaletteIdentity({ client, pathname: '/' }));
    await waitFor(() => expect(second.result.current.userId).toBe('u1'));
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
