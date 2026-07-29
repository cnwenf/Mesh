/**
 * 统一「待我审批」页(README §6.10 / §6.12 规范深链 /w/{ws}/approvals)。
 *
 * 列出工作区待审批条目(runtime 审批端点),卡片内联批准/拒绝(决策权限由
 * 后端强制:admin/owner 或 agent owner;无权决策时按钮操作经 toast 反馈失败)。
 * 一切快捷键有等价鼠标路径(§6.12):本页即审批命令面板条目的落点。
 */
import { useCallback, useEffect, useState } from 'react';
import { getApiClient } from '../../api/instance';
import { MeshApiError } from '../../api/errors';
import { Button, ErrorState, Skeleton, useToast } from '../../design';
import { useT } from '../../i18n';
import { useWorkspace, WorkspaceGate } from '../../workspace/WorkspaceProvider';
import { approveApproval, listApprovals, rejectApproval } from './api';
import type { Approval } from './types';

type LoadStatus = 'loading' | 'ready' | 'error';

export function ApprovalsPage(): React.JSX.Element {
  return (
    <WorkspaceGate>
      <ApprovalsList />
    </WorkspaceGate>
  );
}

function ApprovalsList(): React.JSX.Element {
  const t = useT();
  const { workspace } = useWorkspace();
  const { addToast } = useToast();
  const [status, setStatus] = useState<LoadStatus>('loading');
  const [approvals, setApprovals] = useState<readonly Approval[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (workspace === null) return;
    setStatus('loading');
    try {
      const rows = await listApprovals(getApiClient(), workspace.id, { status: 'pending' });
      setApprovals(rows);
      setStatus('ready');
    } catch {
      setStatus('error');
    }
  }, [workspace]);

  useEffect(() => {
    void load();
  }, [load]);

  if (workspace === null) return <></>;

  const decide = async (approval: Approval, approve: boolean): Promise<void> => {
    setBusyId(approval.id);
    try {
      const decideFn = approve ? approveApproval : rejectApproval;
      await decideFn(getApiClient(), workspace.id, approval.id, {});
      setApprovals((prev) => prev.filter((item) => item.id !== approval.id));
    } catch (error) {
      const message =
        error instanceof MeshApiError ? t('error.' + error.code) : t('common.unknownError');
      addToast(message, { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="mesh-page" data-testid="approvals-page">
      <h1 className="mesh-page__title">{t('nav.approvals')}</h1>
      {status === 'loading' ? (
        <Skeleton loadingLabel={t('common.loading')} />
      ) : status === 'error' ? (
        <ErrorState
          title={t('state.errorTitle')}
          description={t('state.errorDescription')}
          retryLabel={t('common.retry')}
          onRetry={() => void load()}
        />
      ) : approvals.length === 0 ? (
        <p data-testid="approvals-empty">
          {t('state.emptyTitle')} · {t('state.emptyDescription')}
        </p>
      ) : (
        <ul className="mesh-approvals__list" data-testid="approvals-list">
          {approvals.map((approval) => (
            <li
              key={approval.id}
              className="mesh-approvals__card"
              data-testid={`approval-card-${approval.id}`}
            >
              <div className="mesh-approvals__summary">
                <span className="mesh-approvals__action" data-testid={`approval-summary-${approval.id}`}>
                  {approval.action_summary}
                </span>
                <span className="mesh-approvals__subject">{approval.subject_type}</span>
              </div>
              <div className="mesh-approvals__actions">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={busyId !== null}
                  data-testid={`approval-approve-${approval.id}`}
                  onClick={() => void decide(approval, true)}
                >
                  {t('autopilots.runDetail.approve')}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busyId !== null}
                  data-testid={`approval-reject-${approval.id}`}
                  onClick={() => void decide(approval, false)}
                >
                  {t('autopilots.runDetail.reject')}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
