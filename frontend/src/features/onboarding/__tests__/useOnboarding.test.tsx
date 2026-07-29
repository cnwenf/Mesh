/**
 * useOnboarding 钩子测试(onboarding.md §3/§3.7/§4.5):
 * 状态加载(me → members → state)、写操作后重拉(DB 是唯一真源)、
 * 实时帧触发重拉、实时缺省 30s 降级轮询、错误分支。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { notifyOnboardingExternalChange, requestOptimisticStepComplete } from '../notify';
import { ONBOARDING_POLL_INTERVAL_MS, useOnboarding } from '../useOnboarding';

const ME = {
  user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'WS',
      workspace_slug: 'team',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};
const ROSTER = {
  data: [
    {
      id: 'mem-1',
      member_type: 'human',
      role: 'owner',
      status: 'active',
      display_name: 'Owner',
      joined_at: null,
      profile: { id: 'usr-1', full_name: 'Owner', email: 'o@c.com', avatar_url: null },
    },
  ],
  next_cursor: null,
};

function stateBody(completed: number, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'obs-1',
    workspace_id: 'ws-1',
    member_id: 'mem-1',
    checklist: 'activation',
    aha_reached_at: null,
    dismissed_at: null,
    progress: { total: 5, completed, skipped: 0 },
    steps: [
      { step_key: 'create_workspace', status: 'completed', completed_via: 'auto', completed_at: '2026-07-24T10:00:00Z' },
      { step_key: 'invite_member_or_add_agent', status: completed >= 2 ? 'completed' : 'pending', completed_via: completed >= 2 ? 'auto' : null, completed_at: completed >= 2 ? '2026-07-24T10:12:33Z' : null },
      { step_key: 'create_first_issue', status: 'pending', completed_via: null, completed_at: null },
      { step_key: 'dispatch_or_mention_agent', status: 'pending', completed_via: null, completed_at: null },
      { step_key: 'see_agent_reply_in_inbox', status: 'pending', completed_via: null, completed_at: null },
    ],
    created_at: '2026-07-24T10:00:00Z',
    updated_at: '2026-07-24T10:12:33Z',
    ...extra,
  };
}

interface RoutedCall {
  url: string;
  method: string;
}

interface RoutedStub {
  calls: RoutedCall[];
  stateLoads: () => number;
}

/** URL 路由 fetch 桩:state 每次 GET 返回递增进度,便于观察重拉。 */
function routedFetch(): { fetchImpl: typeof fetch; routed: RoutedStub } {
  const calls: RoutedCall[] = [];
  let loads = 0;
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/onboarding/state')) {
      loads += 1;
      return fakeResponse({ body: { data: stateBody(loads) } });
    }
    if (url.includes('/onboarding/dismiss')) {
      return fakeResponse({ body: { data: { id: 'obs-1', dismissed_at: '2026-07-25T08:30:00Z' } } });
    }
    if (url.includes('/onboarding/restore')) {
      return fakeResponse({ body: { data: { id: 'obs-1', dismissed_at: null } } });
    }
    if (url.includes('/onboarding/steps/')) {
      return fakeResponse({
        body: {
          data: { step_key: 'create_first_issue', status: 'completed', completed_via: 'manual', completed_at: '2026-07-25T08:00:00Z' },
        },
      });
    }
    if (url.includes('/issues')) {
      return fakeResponse({
        body: { data: [{ id: 'iss-1', title: 'T', identifier: 'WS-1' }], next_cursor: null },
      });
    }
    if (url.includes('/members')) return fakeResponse({ body: ROSTER });
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nope' } } });
  }) as typeof fetch;
  const routed: RoutedStub = { calls, stateLoads: () => loads };
  return { fetchImpl, routed };
}

let pageFrame: ((frame: RealtimeEventFrame) => void) | null = null;
const fakeClient = {
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  onFrame: (cb: (frame: RealtimeEventFrame) => void) => {
    pageFrame = cb;
    return () => {
      pageFrame = null;
    };
  },
};
const realtimeValue = { state: 'connected', client: fakeClient } as unknown as RealtimeContextValue;

function withRealtime(props: { children: ReactNode }): React.JSX.Element {
  return (
    <RealtimeContext.Provider value={realtimeValue}>{props.children}</RealtimeContext.Provider>
  );
}

beforeEach(() => {
  pageFrame = null;
  fakeClient.subscribe.mockClear();
  fakeClient.unsubscribe.mockClear();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('useOnboarding', () => {
  it('loads state via me → members → state and derives workspace/member/slug', async () => {
    const { fetchImpl, routed } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding());
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.state).not.toBeNull());
    expect(result.current.loading).toBe(false);
    expect(result.current.workspaceId).toBe('ws-1');
    expect(result.current.memberId).toBe('mem-1');
    expect(result.current.workspaceSlug).toBe('team');
    expect(result.current.errorKey).toBeNull();
    expect(routed.stateLoads()).toBe(1);
    // 无实时上下文 → 不订阅(降级轮询路径)
    expect(fakeClient.subscribe).not.toHaveBeenCalled();
  });

  it('subscribes to the member onboarding channel when realtime is available', async () => {
    const { fetchImpl } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding(), { wrapper: withRealtime });
    await waitFor(() => expect(result.current.memberId).toBe('mem-1'));
    expect(fakeClient.subscribe).toHaveBeenCalledWith('member:mem-1:onboarding');
  });

  it('refetches after dismiss / restore / completeStep (DB is truth)', async () => {
    const { fetchImpl, routed } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding(), { wrapper: withRealtime });
    await waitFor(() => expect(result.current.state).not.toBeNull());

    await act(async () => {
      await result.current.dismiss();
    });
    expect(routed.calls.some((call) => call.url.includes('/onboarding/dismiss'))).toBe(true);

    await act(async () => {
      await result.current.restore();
    });
    expect(routed.calls.some((call) => call.url.includes('/onboarding/restore'))).toBe(true);

    await act(async () => {
      await result.current.completeStep('create_first_issue');
    });
    expect(
      routed.calls.some(
        (call) => call.url.includes('/onboarding/steps/create_first_issue/complete'),
      ),
    ).toBe(true);
    // 三次写操作各触发一次重拉(首次加载共 4 次 state GET)
    expect(routed.stateLoads()).toBe(4);
  });

  it('refetches when an onboarding frame arrives on the member channel', async () => {
    const { fetchImpl, routed } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding(), { wrapper: withRealtime });
    await waitFor(() => expect(result.current.state).not.toBeNull());
    expect(routed.stateLoads()).toBe(1);

    act(() => {
      pageFrame?.({
        op: 'event',
        channel: 'member:mem-1:onboarding',
        seq: 2,
        event: 'onboarding.progress',
        payload: { state_id: 'obs-1', step_key: 'create_first_issue', status: 'completed' },
      });
    });
    await waitFor(() => expect(routed.stateLoads()).toBe(2));

    // 无关频道 / 事件不触发重拉
    act(() => {
      pageFrame?.({
        op: 'event',
        channel: 'member:mem-1:inbox',
        seq: 3,
        event: 'notification.created',
        payload: {},
      });
    });
    expect(routed.stateLoads()).toBe(2);
  });

  it('refetches when an external restore broadcasts (onboarding.md §4.2 流程 3)', async () => {
    const { fetchImpl, routed } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding(), { wrapper: withRealtime });
    await waitFor(() => expect(result.current.state).not.toBeNull());
    expect(routed.stateLoads()).toBe(1);

    act(() => {
      notifyOnboardingExternalChange();
    });
    await waitFor(() => expect(routed.stateLoads()).toBe(2));
  });

  it('falls back to 30s polling when the realtime context is null (onboarding.md §3.7)', async () => {
    const { fetchImpl, routed } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    vi.useFakeTimers(); // 须先于挂载:轮询 interval 方能被假时钟驱动
    const { result } = renderHook(() => useOnboarding());
    // vi.waitFor 在假时钟下自动推进时间,初始加载(纯微任务)可等出
    await vi.waitFor(() => expect(result.current.state).not.toBeNull());
    expect(routed.stateLoads()).toBe(1);
    expect(fakeClient.subscribe).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(ONBOARDING_POLL_INTERVAL_MS);
    });
    await act(async () => {
      vi.advanceTimersByTime(ONBOARDING_POLL_INTERVAL_MS);
    });
    expect(routed.stateLoads()).toBe(3);
  });

  it('reports an error key when state loading fails', async () => {
    const calls: RoutedCall[] = [];
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? 'GET' });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/members')) return fakeResponse({ body: ROSTER });
      return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding());
    await waitFor(() => expect(result.current.errorKey).toBe('error.internal_error'));
    expect(result.current.state).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it('skips agent / null-profile members and matches by email fallback', async () => {
    const roster = {
      data: [
        { id: 'mem-agent', member_type: 'agent', role: 'member', status: 'active', display_name: 'Bot', joined_at: null, profile: { id: 'agt-1', name: 'Bot', description: null, avatar_url: null } },
        { id: 'mem-null', member_type: 'human', role: 'member', status: 'active', display_name: 'X', joined_at: null, profile: null },
        { id: 'mem-email', member_type: 'human', role: 'member', status: 'active', display_name: 'O', joined_at: null, profile: { id: 'usr-OTHER', full_name: 'O', email: 'o@c.com', avatar_url: null } },
      ],
      next_cursor: null,
    };
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/members')) return fakeResponse({ body: roster });
      return fakeResponse({ body: { data: stateBody(1) } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding());
    await waitFor(() => expect(result.current.memberId).toBe('mem-email'));
  });

  it('sets errorKey when a mutation fails', async () => {
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/members')) return fakeResponse({ body: ROSTER });
      if (method === 'POST' && url.includes('/onboarding/dismiss')) {
        return fakeResponse({ status: 422, body: { error: { code: 'checklist_completed', message: 'dismissed' } } });
      }
      return fakeResponse({ body: { data: stateBody(1) } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding());
    await waitFor(() => expect(result.current.state).not.toBeNull());
    await act(async () => {
      await result.current.dismiss();
    });
    expect(result.current.errorKey).toBe('error.checklist_completed');
  });

  it('mutations are no-ops without an active workspace', async () => {
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({ body: { data: { ...ME, memberships: [] } } });
      }
      return fakeResponse({ body: ROSTER });
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding());
    await waitFor(() => expect(result.current.errorKey).toBe('state.errorDescription'));
    // workspaceId null → 写操作安全短路,不抛错
    await act(async () => {
      await result.current.completeStep('create_first_issue');
    });
    expect(result.current.state).toBeNull();
  });

  it('reports an error when there is no active workspace', async () => {
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({ body: { data: { ...ME, memberships: [] } } });
      }
      return fakeResponse({ body: ROSTER });
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding());
    await waitFor(() => expect(result.current.errorKey).toBe('state.errorDescription'));
    expect(result.current.workspaceId).toBeNull();
  });

  it('derives the latest issue id for the step-4 shared deeplink', async () => {
    const { fetchImpl } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding(), { wrapper: withRealtime });
    await waitFor(() => expect(result.current.latestIssueId).toBe('iss-1'));
  });

  it('optimistically completes a step on request, POSTs, then refetches (§1.2.2)', async () => {
    const { fetchImpl, routed } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding(), { wrapper: withRealtime });
    await waitFor(() => expect(result.current.state).not.toBeNull());
    const loadsBefore = routed.stateLoads();

    act(() => {
      requestOptimisticStepComplete('create_first_issue');
    });
    // 本地即时置位(乐观)
    expect(
      result.current.state?.steps.find((s2) => s2.step_key === 'create_first_issue')?.status,
    ).toBe('completed');
    // POST 手动完成 + 成功后重拉(服务端为真)
    await waitFor(() =>
      expect(
        routed.calls.some((call) =>
          call.url.includes('/onboarding/steps/create_first_issue/complete'),
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(routed.stateLoads()).toBe(loadsBefore + 1));
  });

  it('rolls back the optimistic mark when the manual-complete POST fails', async () => {
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/members')) return fakeResponse({ body: ROSTER });
      if (url.includes('/issues')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (method === 'POST' && url.includes('/onboarding/steps/')) {
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
      }
      return fakeResponse({ body: { data: stateBody(1) } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding());
    await waitFor(() => expect(result.current.state).not.toBeNull());

    act(() => {
      requestOptimisticStepComplete('create_first_issue');
    });
    // 失败 → 回滚至快照(pending)+ errorKey
    await waitFor(() =>
      expect(
        result.current.state?.steps.find((s2) => s2.step_key === 'create_first_issue')?.status,
      ).toBe('pending'),
    );
    expect(result.current.errorKey).toBe('error.internal_error');
  });


  it('ignores optimistic requests for an already-completed step (guard, no POST)', async () => {
    const { fetchImpl, routed } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => useOnboarding(), { wrapper: withRealtime });
    await waitFor(() => expect(result.current.state).not.toBeNull());
    const callsBefore = routed.calls.length;

    act(() => {
      // stateBody(1) 中 create_workspace 已 completed → 守卫短路,不发 POST
      requestOptimisticStepComplete('create_workspace');
    });
    await Promise.resolve();
    expect(routed.calls.length).toBe(callsBefore);
    expect(
      result.current.state?.steps.find((s2) => s2.step_key === 'create_workspace')?.completed_via,
    ).toBe('auto'); // 未被改写
  });

  it('ignores async derivation results after unmount (cancel guards)', async () => {
    const { fetchImpl } = routedFetch();
    vi.stubGlobal('fetch', fetchImpl);
    const { result, unmount } = renderHook(() => useOnboarding());
    expect(result.current.loading).toBe(true);
    unmount(); // 派生/加载在途时卸载 → cancelled 分支静默丢弃结果
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(result.current.state).toBeNull();
  });

});
