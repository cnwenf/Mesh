/**
 * 收件箱上下文解析:规范工作区路由以 WorkspaceProvider 为权威,旧扁平入口才退回
 * 首个成员身份;再经该工作区名册解析当前用户的 members.id(收件箱实时频道所需)。
 * 人类成员的人档案 profile.id 即 users.id,据此匹配(邮箱兜底)。
 */
import { useEffect, useMemo, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import { env } from '../../env';
import { useAuthStore } from '../../state/authStore';
import { useOptionalWorkspace } from '../../workspace/WorkspaceProvider';
import { listMembers } from '../members/api';
import type { HumanProfile } from '../members/types';
import { useWorkspaceMembership } from '../members/useWorkspaceMembership';

export type InboxContextStatus = 'loading' | 'ready' | 'error';

export interface InboxContext {
  readonly status: InboxContextStatus;
  readonly workspaceId: string | null;
  /** 仅规范 WorkspaceProvider 路由有值;账号设置/旧扁平入口保持 null。 */
  readonly workspaceSlug: string | null;
  readonly memberId: string | null;
}

interface RosterResolution {
  readonly targetKey: string;
  readonly status: InboxContextStatus;
  readonly memberId: string | null;
}

/**
 * 保留 MES-106 的匿名挂载安全性:仍调用共享 hook 以保持 hook 顺序稳定,但在 token
 * 不存在时注入永不发网络请求的 client。登录后 client identity 切回真实实例并解析。
 */
const DISABLED_CLIENT = {
  request: () => new Promise<never>(() => undefined),
} as unknown as MeshApiClient;

function matchMemberId(
  members: readonly { id: string; member_type: string; profile: unknown }[],
  userId: string,
  userEmail: string,
): string | null {
  for (const member of members) {
    if (member.member_type !== 'human') continue;
    const profile = member.profile as HumanProfile | null;
    if (profile === null) continue;
    if (profile.id === userId || profile.email === userEmail) return member.id;
  }
  return null;
}

export function useInboxContext(): InboxContext {
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const provider = useOptionalWorkspace();
  // MES-106(验收 M1):收件箱上下文解析为鉴权请求——未登录(匿名 shell 挂载,
  // 如守卫跳转窗口期 / 公开邀请页)不发起,保持 loading;token 写入后随依赖补取。
  const hasToken = useAuthStore((state) => state.token !== null);
  const membership = useWorkspaceMembership(hasToken ? client : DISABLED_CLIENT);
  const [roster, setRoster] = useState<RosterResolution>({
    targetKey: '',
    status: 'loading',
    memberId: null,
  });

  useEffect(() => {
    if (!hasToken || membership.kind !== 'ready') return;
    const targetKey = `${membership.membership.workspace_id}:${membership.user.id}`;
    let cancelled = false;
    setRoster({ targetKey, status: 'loading', memberId: null });
    void (async () => {
      try {
        const members = await listMembers(client, membership.membership.workspace_id, { limit: 100 });
        if (cancelled) return;
        setRoster({
          targetKey,
          status: 'ready',
          memberId: matchMemberId(members.data, membership.user.id, membership.user.email),
        });
      } catch {
        if (!cancelled) setRoster({ targetKey, status: 'error', memberId: null });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, hasToken, membership]);

  if (!hasToken || membership.kind === 'loading') {
    return { status: 'loading', workspaceId: null, workspaceSlug: null, memberId: null };
  }
  if (membership.kind === 'error' || membership.kind === 'no_workspace') {
    return { status: 'error', workspaceId: null, workspaceSlug: null, memberId: null };
  }

  const targetKey = `${membership.membership.workspace_id}:${membership.user.id}`;
  const workspaceSlug =
    provider !== null && provider.status === 'ready' && provider.workspace !== null
      ? provider.workspace.slug
      : null;
  if (roster.targetKey !== targetKey || roster.status === 'loading') {
    return {
      status: 'loading',
      workspaceId: membership.membership.workspace_id,
      workspaceSlug,
      memberId: null,
    };
  }
  return {
    status: roster.status,
    workspaceId: membership.membership.workspace_id,
    workspaceSlug,
    memberId: roster.memberId,
  };
}
