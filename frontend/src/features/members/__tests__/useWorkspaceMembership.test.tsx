import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import type { WorkspaceContextValue } from '../../../workspace/WorkspaceProvider';
import { useWorkspaceMembership } from '../useWorkspaceMembership';

const workspaceContext = vi.hoisted(() => ({
  value: null as WorkspaceContextValue | null,
}));

vi.mock('../../../workspace/WorkspaceProvider', () => ({
  useOptionalWorkspace: () => workspaceContext.value,
}));

const ME = {
  user: {
    id: 'user-1',
    email: 'owner@example.test',
    display_name: 'Owner',
  },
  memberships: [
    {
      workspace_id: 'ws-a',
      workspace_name: 'Alpha',
      workspace_slug: 'alpha',
      role: 'owner' as const,
      status: 'active' as const,
      joined_at: null,
    },
    {
      workspace_id: 'ws-b',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
      role: 'admin' as const,
      status: 'active' as const,
      joined_at: null,
    },
  ],
};

function provider(
  status: WorkspaceContextValue['status'],
  workspaceId: string | null = null,
): WorkspaceContextValue {
  return {
    status,
    workspace:
      workspaceId === null
        ? null
        : {
            id: workspaceId,
            name: workspaceId === 'ws-b' ? 'Beta' : 'Alpha',
            slug: workspaceId === 'ws-b' ? 'beta' : 'alpha',
            logo_url: null,
            timezone: 'UTC',
            settings: {},
            my_role: workspaceId === 'ws-b' ? 'admin' : 'owner',
            created_at: '2026-08-04T00:00:00Z',
            updated_at: '2026-08-04T00:00:00Z',
          },
    error: null,
    isAdmin: workspaceId !== null,
    isOwner: workspaceId === 'ws-a',
    refresh: vi.fn(async () => undefined),
    patch: vi.fn(),
  } as WorkspaceContextValue;
}

function clientReturning(value: unknown): MeshApiClient {
  return {
    request: vi.fn(async () => value),
  } as unknown as MeshApiClient;
}

describe('useWorkspaceMembership', () => {
  beforeEach(() => {
    workspaceContext.value = null;
  });

  it('selects the membership matching the canonical provider workspace', async () => {
    workspaceContext.value = provider('ready', 'ws-b');
    const client = clientReturning(ME);

    const { result } = renderHook(() => useWorkspaceMembership(client));

    await waitFor(() => expect(result.current.kind).toBe('ready'));
    expect(result.current).toMatchObject({
      kind: 'ready',
      membership: ME.memberships[1],
      user: ME.user,
    });
  });

  it('blocks membership requests while the provider is loading or unavailable', () => {
    const client = clientReturning(ME);
    workspaceContext.value = provider('loading');
    const loading = renderHook(() => useWorkspaceMembership(client));
    expect(loading.result.current).toMatchObject({ kind: 'loading' });
    expect(client.request).not.toHaveBeenCalled();
    loading.unmount();

    workspaceContext.value = provider('not_found');
    const unavailable = renderHook(() => useWorkspaceMembership(client));
    expect(unavailable.result.current).toMatchObject({ kind: 'error' });
    expect(client.request).not.toHaveBeenCalled();
  });

  it('delegates retry to an unavailable canonical workspace provider', async () => {
    const client = clientReturning(ME);
    const unavailable = provider('not_found');
    workspaceContext.value = unavailable;
    const { result } = renderHook(() => useWorkspaceMembership(client));

    expect(result.current.kind).toBe('error');
    await act(async () => result.current.retry());

    expect(unavailable.refresh).toHaveBeenCalledTimes(1);
    expect(client.request).not.toHaveBeenCalled();
  });

  it('falls back to the first membership only outside a workspace provider', async () => {
    const client = clientReturning(ME);
    const { result } = renderHook(() => useWorkspaceMembership(client));

    await waitFor(() => expect(result.current.kind).toBe('ready'));
    expect(result.current).toMatchObject({
      kind: 'ready',
      membership: ME.memberships[0],
      user: ME.user,
    });
  });

  it('returns no_workspace when the canonical workspace is absent from memberships', async () => {
    workspaceContext.value = provider('ready', 'ws-missing');
    const client = clientReturning(ME);
    const { result } = renderHook(() => useWorkspaceMembership(client));

    await waitFor(() => expect(result.current).toMatchObject({ kind: 'no_workspace' }));
  });

  it('returns error when the membership request fails', async () => {
    const client = {
      request: vi.fn(async () => Promise.reject(new Error('offline'))),
    } as unknown as MeshApiClient;
    const { result } = renderHook(() => useWorkspaceMembership(client));

    await waitFor(() => expect(result.current).toMatchObject({ kind: 'error' }));
  });

  it('retries a failed membership request and reaches ready', async () => {
    const client = {
      request: vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(ME),
    } as unknown as MeshApiClient;
    const { result } = renderHook(() => useWorkspaceMembership(client));

    await waitFor(() => expect(result.current.kind).toBe('error'));
    act(() => result.current.retry());

    await waitFor(() => expect(result.current.kind).toBe('ready'));
    expect(client.request).toHaveBeenCalledTimes(2);
    expect(result.current).toMatchObject({
      kind: 'ready',
      membership: ME.memberships[0],
      user: ME.user,
    });
  });

  it('ignores a stale request that settles after a retry', async () => {
    let resolveFirst!: (value: unknown) => void;
    let resolveSecond!: (value: unknown) => void;
    const first = new Promise((resolve) => {
      resolveFirst = resolve;
    });
    const second = new Promise((resolve) => {
      resolveSecond = resolve;
    });
    const client = {
      request: vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second),
    } as unknown as MeshApiClient;
    const { result } = renderHook(() => useWorkspaceMembership(client));
    await waitFor(() => expect(client.request).toHaveBeenCalledTimes(1));

    act(() => result.current.retry());
    await waitFor(() => expect(client.request).toHaveBeenCalledTimes(2));

    const retried = {
      ...ME,
      user: { ...ME.user, display_name: 'Retried Owner' },
    };
    await act(async () => resolveSecond(retried));
    await waitFor(() => expect(result.current.kind).toBe('ready'));
    expect(result.current).toMatchObject({ kind: 'ready', user: retried.user });

    const stale = {
      ...ME,
      user: { ...ME.user, display_name: 'Stale Owner' },
    };
    await act(async () => resolveFirst(stale));
    expect(result.current).toMatchObject({ kind: 'ready', user: retried.user });
  });

  it('drops a stale response when the provider workspace changes', async () => {
    let resolveFirst!: (value: unknown) => void;
    const client = {
      request: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              resolveFirst = resolve;
            }),
        )
        .mockResolvedValueOnce(ME),
    } as unknown as MeshApiClient;
    workspaceContext.value = provider('ready', 'ws-a');
    const { result, rerender } = renderHook(() => useWorkspaceMembership(client));

    workspaceContext.value = provider('ready', 'ws-b');
    rerender();
    await waitFor(() => {
      expect(result.current).toMatchObject({
        kind: 'ready',
        membership: ME.memberships[1],
        user: ME.user,
      });
    });

    await act(async () => resolveFirst(ME));
    expect(result.current).toMatchObject({
      kind: 'ready',
      membership: ME.memberships[1],
      user: ME.user,
    });
  });
});
