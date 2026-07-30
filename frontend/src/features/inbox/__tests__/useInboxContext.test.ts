/**
 * useInboxContext 钩子测试:matchMemberId 的各分支(agent 跳过 / 空 profile 跳过 /
 * 邮箱兜底匹配 / 全不匹配返回 null)、无活跃工作区(active===null → error)、
 * fetchMe 失败(catch → error)。fetch 经 stubFetch 按序:me → members。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, failingFetch, stubFetch } from '../../../api/__tests__/fetchStub';
import { useAuthStore } from '../../../state/authStore';
import { useInboxContext } from '../useInboxContext';

const membership = {
  workspace_id: 'ws-1', workspace_name: 'WS', workspace_slug: 'ws', role: 'owner', status: 'active', joined_at: null,
};
const me = {
  user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
  memberships: [membership],
};

function member(id: string, memberType: string, profile: unknown): Record<string, unknown> {
  return { id, member_type: memberType, role: 'member', status: 'active', display_name: 'M', joined_at: null, profile };
}
const profile = (id: string, email: string): Record<string, unknown> => ({
  id, full_name: 'M', email, avatar_url: null,
});

// 收件箱上下文解析为鉴权请求(MES-106 M1):用例以登录态为前置。
beforeEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.getState().setToken('tok_test');
});
afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
});

describe('useInboxContext', () => {
  it('skips agent and null-profile members and matches by email fallback (branches L26/L28/L29)', async () => {
    const roster = {
      data: [
        member('mem-agent', 'agent', profile('agent-1', 'agent@c.com')),
        member('mem-null', 'human', null),
        member('mem-email', 'human', profile('usr-OTHER', 'o@c.com')),
      ],
      next_cursor: null,
    };
    const stub = stubFetch(fakeResponse({ body: { data: me } }), fakeResponse({ body: roster }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    const { result } = renderHook(() => useInboxContext());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.workspaceId).toBe('ws-1');
    // profile.id 不同但邮箱命中 → 邮箱兜底匹配
    expect(result.current.memberId).toBe('mem-email');
  });

  it('returns a null memberId when no member matches (branch L30 / return null)', async () => {
    const roster = {
      data: [member('mem-x', 'human', profile('usr-other', 'someone-else@c.com'))],
      next_cursor: null,
    };
    const stub = stubFetch(fakeResponse({ body: { data: me } }), fakeResponse({ body: roster }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    const { result } = renderHook(() => useInboxContext());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.workspaceId).toBe('ws-1');
    expect(result.current.memberId).toBeNull();
  });

  it('reports error when there is no active workspace (branch L46)', async () => {
    const stub = stubFetch(fakeResponse({ body: { data: { ...me, memberships: [] } } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    const { result } = renderHook(() => useInboxContext());
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.workspaceId).toBeNull();
    expect(result.current.memberId).toBeNull();
  });

  it('reports error when fetching /users/me fails (catch branch L55)', async () => {
    vi.stubGlobal('fetch', failingFetch());
    const { result } = renderHook(() => useInboxContext());
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.workspaceId).toBeNull();
  });

  it('未登录时不发起鉴权请求(MES-106 M1):保持 loading,fetch 零调用', async () => {
    useAuthStore.getState().clearToken();
    const stub = stubFetch(fakeResponse({ body: { data: me } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    const { result } = renderHook(() => useInboxContext());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(stub.calls).toEqual([]);
    expect(result.current.status).toBe('loading');
    expect(result.current.workspaceId).toBeNull();
  });

  it('token 写入后随依赖补取(登录态变化 → 上下文解析)', async () => {
    useAuthStore.getState().clearToken();
    const roster = {
      data: [member('mem-1', 'human', profile('usr-1', 'o@c.com'))],
      next_cursor: null,
    };
    const stub = stubFetch(fakeResponse({ body: { data: me } }), fakeResponse({ body: roster }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    const { result } = renderHook(() => useInboxContext());
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(stub.calls).toEqual([]);
    act(() => {
      useAuthStore.getState().setToken('tok_new');
    });
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.workspaceId).toBe('ws-1');
    expect(result.current.memberId).toBe('mem-1');
  });
});
