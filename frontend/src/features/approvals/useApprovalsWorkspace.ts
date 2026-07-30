/**
 * 审批页工作区解析(双路由支持):
 * - `/w/:workspaceSlug/approvals`(AppShell 命中 WorkspaceProvider)→ 用上下文;
 * - 平直 `/approvals`(Provider 外)→ fetchMe → activeWorkspace(memberships)。
 * 同时探测当前 principal 是否为 agent 型成员(审批仅对人类开放,§6.10)。
 * 返回值经 useMemo 稳定,供消费方放心放入 effect 依赖。
 */
import { useEffect, useMemo, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { activeWorkspace, fetchMe } from '../members/api';
import type { MeResponse } from '../members/types';
import { useOptionalWorkspace } from '../../workspace/WorkspaceProvider';

/** 防御性扩展:principal 类型标记(后端若随 /users/me 下发即生效)。 */
interface MeWithPrincipal extends MeResponse {
  readonly member_type?: 'human' | 'agent';
}

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

/** 工作区上下文可用时同步派生;为 null 时返回 null 走 fetchMe 路径。 */
function fromProvider(context: ReturnType<typeof useOptionalWorkspace>): ApprovalsWorkspaceState | null {
  if (context === null) return null;
  if (context.status === 'loading') return LOADING;
  if (context.status === 'ready' && context.workspace !== null) {
    return { kind: 'ready', workspaceId: context.workspace.id, isAgentPrincipal: false };
  }
  return ERROR; // not_found / error:与 WorkspaceGate 同语义
}

export function useApprovalsWorkspace(client: MeshApiClient): ApprovalsWorkspaceState {
  const provider = useOptionalWorkspace();
  const providerState = fromProvider(provider);
  const [flatState, setFlatState] = useState<ApprovalsWorkspaceState>(LOADING);

  useEffect(() => {
    if (providerState !== null) return; // 工作区路由已由 Provider 解析
    let cancelled = false;
    setFlatState(LOADING);
    fetchMe(client)
      .then((me) => {
        if (cancelled) return;
        const membership = activeWorkspace(me.memberships);
        if (membership === null) {
          setFlatState(NO_WORKSPACE);
          return;
        }
        setFlatState({
          kind: 'ready',
          workspaceId: membership.workspace_id,
          isAgentPrincipal: (me as MeWithPrincipal).member_type === 'agent',
        });
      })
      .catch(() => {
        if (!cancelled) setFlatState(ERROR);
      });
    return () => {
      cancelled = true;
    };
  }, [client, providerState]);

  return useMemo<ApprovalsWorkspaceState>(() => {
    if (providerState !== null) return providerState;
    return flatState;
  }, [providerState, flatState]);
}
