/**
 * 成员详情页(README §6.12 规范深链 /w/{ws}/members/{member_id})。
 *
 * 名册条目详情,按 member_type 分区:
 * - `agent` → agent 详情即 member_type='agent' 的成员详情(Agent 入口去重,
 *   §6.12 R5):经 profile.id 解析至同页别名路由 /w/{ws}/agents/{agent_id}
 *   (AgentDetailPage),不维护第二份 agent 详情;
 * - `human` → 人类资料卡(名称/邮箱/角色/状态/加入时间/在办 issue 数)。
 */
import { useEffect, useState } from 'react';
import { Navigate, useParams } from 'react-router';
import { getApiClient } from '../../api/instance';
import { ErrorState, Skeleton } from '../../design';
import { useT } from '../../i18n';
import { useWorkspace } from '../../workspace/WorkspaceProvider';
import { getMember } from './api';
import type { HumanProfile, MemberDetail } from './types';

type LoadStatus = 'loading' | 'ready' | 'error';

/**
 * 工作区门控自持(不经 WorkspaceGate):非成员/不存在 → not_found 异常态
 * (§6.12 permission denied 同态,不泄漏存在性),加载 → 骨架。
 */
export function MemberDetailPage(): React.JSX.Element {
  const t = useT();
  const { status: wsStatus, workspace, refresh } = useWorkspace();
  const [status, setStatus] = useState<LoadStatus>('loading');
  const [member, setMember] = useState<MemberDetail | null>(null);
  const [agentRedirect, setAgentRedirect] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const { memberId } = useParams<{ memberId: string }>();

  useEffect(() => {
    if (workspace === null || memberId === undefined) return;
    let cancelled = false;
    setStatus('loading');
    setAgentRedirect(null);
    void (async () => {
      try {
        const detail = await getMember(getApiClient(), workspace.id, memberId);
        if (cancelled) return;
        if (detail.member_type === 'agent' && detail.profile !== null) {
          // agent 名册行 → 同页别名路由(/w/{ws}/agents/{agent_id})渲染 agent 详情。
          setAgentRedirect(`/w/${workspace.slug}/agents/${detail.profile.id}`);
          return;
        }
        setMember(detail);
        setStatus('ready');
      } catch {
        if (!cancelled) setStatus('error');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspace, memberId, reloadKey]);

  if (agentRedirect !== null) {
    return <Navigate to={agentRedirect} replace />;
  }
  if (wsStatus === 'not_found') {
    return (
      <div className="mesh-page" data-testid="member-detail-not-found">
        <h1>{t('workspace.notFoundTitle')}</h1>
        <p>{t('workspace.notFoundDescription')}</p>
      </div>
    );
  }
  if (wsStatus === 'error') {
    return (
      <div className="mesh-page" data-testid="member-detail-ws-error">
        <ErrorState
          title={t('state.errorTitle')}
          description={t('state.errorDescription')}
          retryLabel={t('common.retry')}
          onRetry={() => void refresh()}
        />
      </div>
    );
  }
  if (workspace === null || wsStatus === 'loading') {
    return (
      <div className="mesh-page" data-testid="member-detail-loading">
        <Skeleton loadingLabel={t('common.loading')} />
      </div>
    );
  }
  if (status === 'loading') {
    return (
      <div className="mesh-page" data-testid="member-detail-loading">
        <Skeleton loadingLabel={t('common.loading')} />
      </div>
    );
  }
  if (status === 'error' || member === null) {
    return (
      <div className="mesh-page" data-testid="member-detail-error">
        <ErrorState
          title={t('state.errorTitle')}
          description={t('state.errorDescription')}
          retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      </div>
    );
  }

  const profile = member.profile as HumanProfile | null;

  return (
    <div className="mesh-page mesh-member-detail" data-testid="member-detail">
      <h1 className="mesh-page__title" data-testid="member-detail-name">
        {member.display_name}
      </h1>
      <dl className="mesh-member-detail__card">
        <dt>{t('members.col.contact')}</dt>
        <dd data-testid="member-detail-email">{profile !== null ? profile.email : '—'}</dd>
        <dt>{t('members.col.role')}</dt>
        <dd data-testid="member-detail-role">{t('members.role.' + member.role)}</dd>
        <dt>{t('members.col.status')}</dt>
        <dd data-testid="member-detail-status">{t('members.status.' + member.status)}</dd>
        <dt>{t('members.detail.openIssues')}</dt>
        <dd data-testid="member-detail-open-issues">{member.counts.open_issues_assigned}</dd>
      </dl>
      {member.joined_at !== null ? (
        <p className="mesh-member-detail__joined" data-testid="member-detail-joined">
          {member.joined_at}
        </p>
      ) : null}
    </div>
  );
}
