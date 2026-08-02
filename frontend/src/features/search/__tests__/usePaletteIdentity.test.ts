/**
 * 面板身份解析单测(§2.1 隔离键输入 / §3.4 workspace 解析序)。
 */
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api/client';
import { useAuthStore } from '../../../state/authStore';
import type { MemberRole, Membership } from '../../members/types';
import {
  ANONYMOUS_USER_ID,
  DEFAULT_WORKSPACE_ID,
  resetPaletteIdentityCache,
  resolvePaletteMembership,
  usePaletteIdentity,
  workspaceSlugFromPath,
} from '../usePaletteIdentity';

function membership(workspaceId: string, slug: string, role: MemberRole): Membership {
  return {
    workspace_id: workspaceId,
    workspace_name: 'W',
    workspace_slug: slug,
    role,
    status: 'active',
    joined_at: null,
  };
}

/** 仅 getItem 有意义的内存 Storage(解析序 ② 注入用) */
function memStorage(entries: Record<string, string>): Storage {
  return {
    getItem: (key: string) => entries[key] ?? null,
    setItem: () => undefined,
    removeItem: () => undefined,
    clear: () => undefined,
    key: () => null,
    length: Object.keys(entries).length,
  };
}

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
  beforeEach(() => {
    // 已登录态前置:未登录时 hook 不探测 users/me(匿名降级,见下条用例与函数头注)。
    useAuthStore.getState().setToken('tok_test');
  });
  afterEach(() => {
    useAuthStore.getState().clearToken();
  });

  it('未登录不探测 users/me:anon/default 降级,fetch 零调用(防公开页 401 兜底跳转打断 OAuth 回调)', async () => {
    useAuthStore.getState().clearToken();
    const fetchImpl = vi.fn();
    const client = new MeshApiClient({ baseUrl: 'http://api.test', getToken: () => null, fetchImpl });
    const { result } = renderHook(() => usePaletteIdentity({ client, pathname: '/' }));
    expect(result.current.userId).toBe(ANONYMOUS_USER_ID);
    expect(result.current.workspaceId).toBe(DEFAULT_WORKSPACE_ID);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('未登录 + URL slug:scope 取 slug、角色 null(公开页规范深链直达同样不探测)', () => {
    useAuthStore.getState().clearToken();
    const fetchImpl = vi.fn();
    const client = new MeshApiClient({ baseUrl: 'http://api.test', getToken: () => null, fetchImpl });
    const { result } = renderHook(() => usePaletteIdentity({ client, pathname: '/w/acme/board' }));
    expect(result.current.userId).toBe(ANONYMOUS_USER_ID);
    expect(result.current.workspaceId).toBe('acme');
    expect(result.current.workspaceSlug).toBe('acme');
    expect(result.current.role).toBeNull();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('slug 切换后迟到的 me 解析被 cancelled 守卫丢弃(不覆写新 scope)', async () => {
    let resolveMe: (response: Response) => void = () => undefined;
    const fetchImpl = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveMe = resolve;
        }),
    );
    const client = new MeshApiClient({ baseUrl: 'http://api.test', getToken: () => 'tok', fetchImpl });
    const { result, rerender } = renderHook(
      ({ pathname }: { pathname: string }) => usePaletteIdentity({ client, pathname }),
      { initialProps: { pathname: '/' } },
    );
    // slug 变化 → 旧 effect 清理(cancelled=true);迟到的首轮 me 不得覆写新 scope。
    rerender({ pathname: '/w/other/board' });
    resolveMe(
      new Response(
        JSON.stringify({ data: { user: { id: 'late', email: 'e', display_name: 'd' }, memberships: [] } }),
        { status: 200 },
      ),
    );
    await waitFor(() => expect(result.current.workspaceSlug).toBe('other'));
    expect(result.current.workspaceId).toBe('other');
  });

  it('token 出现后自动升级为真身解析(登录/OAuth 回调换牌路径)', async () => {
    useAuthStore.getState().clearToken();
    const client = clientReturning({
      data: {
        user: { id: 'u9', email: 'a@b.c', display_name: 'A' },
        memberships: [{ workspace_id: 'ws-9', workspace_name: 'W', workspace_slug: 'w', role: 'member', status: 'active', joined_at: null }],
      },
    });
    const { result } = renderHook(() => usePaletteIdentity({ client, pathname: '/' }));
    expect(result.current.userId).toBe(ANONYMOUS_USER_ID);
    // 模拟 OAuth 回调 setSession 写入 token → 订阅触发真身解析。
    useAuthStore.getState().setToken('tok_new');
    await waitFor(() => expect(result.current.userId).toBe('u9'));
    expect(result.current.workspaceId).toBe('ws-9');
  });

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

  it('暴露解析所得成员角色与 slug(H4 canCreateIssue / H6 深链输入)', async () => {
    const client = clientReturning({
      data: {
        user: { id: 'u9', email: 'a@b.c', display_name: 'A' },
        memberships: [
          { workspace_id: 'ws-9', workspace_name: 'W', workspace_slug: 'w', role: 'admin', status: 'active', joined_at: null },
        ],
      },
    });
    const { result } = renderHook(() => usePaletteIdentity({ client, pathname: '/' }));
    await waitFor(() => expect(result.current.userId).toBe('u9'));
    expect(result.current.role).toBe('admin');
    expect(result.current.workspaceSlug).toBe('w');
  });
});

describe('resolvePaletteMembership(§3.4 解析序 ①→⑤,纯函数)', () => {
  const memberships = [membership('ws-a', 'alpha', 'member'), membership('ws-b', 'beta', 'admin')];

  it('① URL slug 命中:workspaceId=slug,角色取同 slug 成员', () => {
    expect(resolvePaletteMembership(memberships, { slug: 'beta', userId: 'u' })).toEqual({
      workspaceId: 'beta',
      workspaceSlug: 'beta',
      role: 'admin',
    });
  });

  it('① URL slug 失权(成员资格无此 slug)→ role null(scope 仍用 slug)', () => {
    expect(resolvePaletteMembership(memberships, { slug: 'gamma', userId: 'u' })).toEqual({
      workspaceId: 'gamma',
      workspaceSlug: 'gamma',
      role: null,
    });
  });

  it('② 本地记忆 slug 经成员资格校验 → 对应成员身份', () => {
    const storage = memStorage({ 'mesh.last_workspace:host.test:u': 'alpha' });
    expect(
      resolvePaletteMembership(memberships, { slug: null, userId: 'u', storage, host: 'host.test' }),
    ).toEqual({ workspaceId: 'ws-a', workspaceSlug: 'alpha', role: 'member' });
  });

  it('② 记忆 slug 失效(改名/退区)→ 落 ③ 服务端提示', () => {
    const storage = memStorage({ 'mesh.last_workspace:host.test:u': 'renamed-away' });
    expect(
      resolvePaletteMembership(memberships, {
        slug: null,
        userId: 'u',
        lastActiveWorkspaceId: 'ws-b',
        storage,
        host: 'host.test',
      }),
    ).toEqual({ workspaceId: 'ws-b', workspaceSlug: 'beta', role: 'admin' });
  });

  it('③ 服务端 last_active_workspace_id 匹配成员资格', () => {
    expect(
      resolvePaletteMembership(memberships, { slug: null, userId: 'u', lastActiveWorkspaceId: 'ws-b' }),
    ).toEqual({ workspaceId: 'ws-b', workspaceSlug: 'beta', role: 'admin' });
  });

  it('④ 恰一个成员身份 → 直接采用', () => {
    expect(
      resolvePaletteMembership([membership('ws-only', 'solo', 'guest')], { slug: null, userId: 'u' }),
    ).toEqual({ workspaceId: 'ws-only', workspaceSlug: 'solo', role: 'guest' });
  });

  it('⑤ 多工作区无线索 → 兜底首个成员身份', () => {
    expect(resolvePaletteMembership(memberships, { slug: null, userId: 'u' })).toEqual({
      workspaceId: 'ws-a',
      workspaceSlug: 'alpha',
      role: 'member',
    });
  });

  it('零成员身份 → default + role null', () => {
    expect(resolvePaletteMembership([], { slug: null, userId: 'u' })).toEqual({
      workspaceId: DEFAULT_WORKSPACE_ID,
      workspaceSlug: null,
      role: null,
    });
  });
});
