/**
 * 审批页工作区解析(双路由支持):
 * - 先经统一 `/me` 判定当前 credential 的真实 principal;
 * - agent 直接使用其 workspace-scoped active roster identity 并门控;
 * - human 再经 `/users/me` 解析平直路由的 active workspace,或匹配 Provider workspace。
 * 两条路由都必须在 principal 判定完成后才进入 ready,避免 agent 抢先发审批列表请求。
 */
import { useEffect, useState } from 'react';
import { fetchPrincipal, isAgentPrincipal } from '../../api';
import type { MeshApiClient } from '../../api';
import { activeWorkspace, fetchMe as fetchMemberships } from '../members/api';
import { useOptionalWorkspace } from '../../workspace/WorkspaceProvider';

export type ApprovalsWorkspaceState =
  | { readonly kind: 'loading' }
  | {
      readonly kind: 'ready';
      readonly workspaceId: string;
      readonly isAgentPrincipal: boolean;
    }
  | { readonly kind: 'no_workspace' }
  | { readonly kind: 'error' };

const LOADING: ApprovalsWorkspaceState = { kind: 'loading' };
const NO_WORKSPACE: ApprovalsWorkspaceState = { kind: 'no_workspace' };
const ERROR: ApprovalsWorkspaceState = { kind: 'error' };

/** Provider 尚不可解析时阻断请求;null 表示可以开始 principal + membership 解析。 */
function providerBarrier(
  context: ReturnType<typeof useOptionalWorkspace>,
): ApprovalsWorkspaceState | null {
  if (context === null || (context.status === 'ready' && context.workspace !== null)) return null;
  if (context.status === 'loading') return LOADING;
  return ERROR; // not_found / error:与 WorkspaceGate 同语义
}

interface TaggedResolution {
  readonly client: MeshApiClient;
  readonly targetKey: string;
  readonly value: ApprovalsWorkspaceState;
}

export function useApprovalsWorkspace(client: MeshApiClient): ApprovalsWorkspaceState {
  const provider = useOptionalWorkspace();
  const barrier = providerBarrier(provider);
  const providerWorkspaceId =
    provider !== null && provider.status === 'ready' && provider.workspace !== null
      ? provider.workspace.id
      : null;
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
    void (async () => {
      try {
        const principal = await fetchPrincipal(client);
        if (cancelled) return;

        if (isAgentPrincipal(principal)) {
          if (providerWorkspaceId !== null && principal.workspace_id !== providerWorkspaceId) {
            setResolution({ client, targetKey, value: NO_WORKSPACE });
            return;
          }
          setResolution({
            client,
            targetKey,
            value: {
              kind: 'ready',
              workspaceId: providerWorkspaceId ?? principal.workspace_id,
              isAgentPrincipal: true,
            },
          });
          return;
        }

        const me = await fetchMemberships(client);
        if (cancelled) return;
        const membership =
          providerWorkspaceId === null
            ? activeWorkspace(me.memberships)
            : (me.memberships.find((item) => item.workspace_id === providerWorkspaceId) ?? null);
        if (membership === null) {
          setResolution({ client, targetKey, value: NO_WORKSPACE });
          return;
        }
        setResolution({
          client,
          targetKey,
          value: {
            kind: 'ready',
            workspaceId: membership.workspace_id,
            isAgentPrincipal: false,
          },
        });
      } catch {
        if (!cancelled) setResolution({ client, targetKey, value: ERROR });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [barrier, client, providerWorkspaceId, targetKey]);

  if (barrier !== null) return barrier;
  if (resolution.client !== client || resolution.targetKey !== targetKey) return LOADING;
  return resolution.value;
}
