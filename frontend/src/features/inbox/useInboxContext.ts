/**
 * 收件箱上下文解析:从 GET /users/me 取活跃工作区(首个成员身份),再经名册解析
 * 当前用户在该工作区的 members.id(收件箱频道 member:{member_id}:inbox 与偏好所需)。
 * 人类成员的人档案 profile.id 即 users.id,据此匹配(邮箱兜底)。
 */
import { useEffect, useMemo, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import { env } from '../../env';
import { activeWorkspace, fetchMe, listMembers } from '../members/api';
import type { HumanProfile } from '../members/types';

export type InboxContextStatus = 'loading' | 'ready' | 'error';

export interface InboxContext {
  readonly status: InboxContextStatus;
  readonly workspaceId: string | null;
  readonly memberId: string | null;
}

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
  const [status, setStatus] = useState<InboxContextStatus>('loading');
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [memberId, setMemberId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const me = await fetchMe(client);
        const active = activeWorkspace(me.memberships);
        if (active === null) {
          if (!cancelled) setStatus('error');
          return;
        }
        const roster = await listMembers(client, active.workspace_id, { limit: 100 });
        if (cancelled) return;
        setWorkspaceId(active.workspace_id);
        setMemberId(matchMemberId(roster.data, me.user.id, me.user.email));
        setStatus('ready');
      } catch {
        if (!cancelled) setStatus('error');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client]);

  return { status, workspaceId, memberId };
}
