/**
 * Resolve the signed-in human's membership for the current page.
 *
 * Canonical `/w/:workspaceSlug/*` routes are wrapped in `WorkspaceProvider`;
 * that provider is authoritative even when `/users/me` returns another
 * membership first. The first-membership fallback exists only for legacy flat
 * routes while `FlatRouteMigration` replaces them with canonical URLs.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { useOptionalWorkspace } from '../../workspace/WorkspaceProvider';
import { activeWorkspace, fetchMe } from './api';
import type { MeResponse, Membership } from './types';

export type WorkspaceMembershipState =
  | { readonly kind: 'loading' }
  | {
      readonly kind: 'ready';
      readonly membership: Membership;
      readonly user: MeResponse['user'];
    }
  | { readonly kind: 'no_workspace' }
  | { readonly kind: 'error' };

/**
 * Keep the existing discriminated-state surface while exposing one shared retry action.
 * Consumers can continue narrowing on `kind`; membership errors no longer need a page-local
 * reload counter that cannot restart the `/users/me` request.
 */
export type WorkspaceMembershipResult = WorkspaceMembershipState & {
  readonly retry: () => void;
};

const LOADING: WorkspaceMembershipState = { kind: 'loading' };
const NO_WORKSPACE: WorkspaceMembershipState = { kind: 'no_workspace' };
const ERROR: WorkspaceMembershipState = { kind: 'error' };

interface TaggedResolution {
  readonly client: MeshApiClient;
  readonly targetKey: string;
  readonly value: WorkspaceMembershipState;
}

export function useWorkspaceMembership(client: MeshApiClient): WorkspaceMembershipResult {
  const provider = useOptionalWorkspace();
  const providerWorkspaceId =
    provider !== null && provider.status === 'ready' && provider.workspace !== null
      ? provider.workspace.id
      : null;
  const [retryRevision, setRetryRevision] = useState(0);
  const retry = useCallback(() => {
    setRetryRevision((revision) => revision + 1);
    if (provider !== null && providerWorkspaceId === null && provider.status !== 'loading') {
      void provider.refresh().catch(() => undefined);
    }
  }, [provider, providerWorkspaceId]);
  const barrier: WorkspaceMembershipState | null =
    provider === null || providerWorkspaceId !== null
      ? null
      : provider.status === 'loading'
        ? LOADING
        : ERROR;
  const targetKey = provider === null ? 'flat' : `provider:${providerWorkspaceId ?? 'unavailable'}`;
  const [resolution, setResolution] = useState<TaggedResolution>({
    client,
    targetKey: '',
    value: LOADING,
  });

  useEffect(() => {
    if (barrier !== null) return;
    let cancelled = false;
    setResolution({ client, targetKey, value: LOADING });
    void fetchMe(client)
      .then((me) => {
        if (cancelled) return;
        const membership =
          providerWorkspaceId === null
            ? activeWorkspace(me.memberships)
            : (me.memberships.find((item) => item.workspace_id === providerWorkspaceId) ?? null);
        setResolution({
          client,
          targetKey,
          value: membership === null ? NO_WORKSPACE : { kind: 'ready', membership, user: me.user },
        });
      })
      .catch(() => {
        if (!cancelled) setResolution({ client, targetKey, value: ERROR });
      });
    return () => {
      cancelled = true;
    };
  }, [barrier, client, providerWorkspaceId, targetKey, retryRevision]);

  const state =
    barrier ??
    (resolution.client !== client || resolution.targetKey !== targetKey
      ? LOADING
      : resolution.value);
  return useMemo(() => ({ ...state, retry }), [state, retry]);
}

/** Build a canonical route without allowing callers to drop workspace context. */
export function workspaceRoute(workspaceSlug: string, path = ''): string {
  const suffix = path === '' ? '' : path.startsWith('/') ? path : `/${path}`;
  return `/w/${encodeURIComponent(workspaceSlug)}${suffix}`;
}
