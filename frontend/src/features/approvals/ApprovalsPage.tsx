/**
 * 统一审批页(README §6.10,G10 深链):平直 /approvals 与
 * /w/:workspaceSlug/approvals 双路由(工作区解析见 useApprovalsWorkspace)。
 * pending 默认视图(role=mine),状态页签经 URL ?status= 同步;人类专属——
 * agent principal 呈现门控空态(§6.10:agent 不可审批,防自批)。
 * 决定走乐观更新 + 失败回滚 + toast;后端决定幂等,刷新即对账。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, Dialog, EmptyState, ErrorState, Tabs, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useDocumentTitle } from '../../shell/hooks';
import { ApprovalCard } from './ApprovalCard';
import type { Approval, ApprovalStatus } from './api';
import { approveApproval, listApprovals, rejectApproval } from './api';
import { useApprovalsWorkspace } from './useApprovalsWorkspace';
import './approvals.css';

const STATUS_FILTERS: readonly ApprovalStatus[] = [
  'pending',
  'approved',
  'rejected',
  'expired',
  'cancelled',
];

/** URL ?status= 归一:非法值落回 pending(默认视图)。 */
export function parseStatusParam(value: string | null): ApprovalStatus {
  if (value !== null && (STATUS_FILTERS as readonly string[]).includes(value)) {
    return value as ApprovalStatus;
  }
  return 'pending';
}

function toastKeyForError(err: unknown): string {
  return err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown';
}

/** 列表骨架:与卡片列表同形的占位(单一 status 区)。 */
function ApprovalsSkeleton(props: { readonly label: string }): React.JSX.Element {
  return (
    <div role="status" data-testid="approvals-loading" className="mesh-approvals__list">
      <span className="sr-only">{props.label}</span>
      {[0, 1, 2].map((i) => (
        <span
          className="mesh-skeleton__shape mesh-approvals__card-skeleton"
          key={i}
          aria-hidden="true"
        />
      ))}
    </div>
  );
}

export function ApprovalsPage(): React.JSX.Element {
  const t = useT();
  useDocumentTitle(t('approvals.title')); // G19 标签页标题
  const { addToast } = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const ws = useApprovalsWorkspace(client);
  const [searchParams, setSearchParams] = useSearchParams();
  const status = parseStatusParam(searchParams.get('status'));
  const workspaceId = ws.kind === 'ready' ? ws.workspaceId : null;

  const [approvals, setApprovals] = useState<readonly Approval[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<MeshApiError | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [rejectTarget, setRejectTarget] = useState<Approval | null>(null);
  const [rejectComment, setRejectComment] = useState('');

  // 门控前置:agent principal 不发起列表请求(§6.10 agent 不可审批)
  const isAgentGated = ws.kind === 'ready' && ws.isAgentPrincipal;
  useEffect(() => {
    if (workspaceId === null || isAgentGated) {
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    const params = status === 'pending' ? { role: 'mine' as const } : { status };
    listApprovals(client, workspaceId, params)
      .then((envelope) => {
        if (!cancelled) setApprovals(envelope.data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof MeshApiError
            ? err
            : new MeshApiError({ status: 0, code: 'unknown', message: 'unknown error' }),
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, status, reloadKey, isAgentGated]);

  const applyDecision = useCallback(
    async (approval: Approval, decision: 'approved' | 'rejected', comment?: string) => {
      if (workspaceId === null) return;
      const snapshot = approvals;
      setApprovals((prev) =>
        prev.map((a) =>
          a.id === approval.id
            ? {
                ...a,
                status: decision,
                decided_at: new Date().toISOString(),
                decision_comment: comment ?? a.decision_comment,
              }
            : a,
        ),
      );
      setDecidingId(approval.id);
      try {
        if (decision === 'approved') {
          await approveApproval(client, workspaceId, approval.id, {});
        } else {
          await rejectApproval(client, workspaceId, approval.id, { comment });
        }
        addToast(
          t(decision === 'approved' ? 'approvals.toast.approved' : 'approvals.toast.rejected'),
          {
            tone: 'success',
            closeLabel: t('a11y.dismiss'),
          },
        );
      } catch (err: unknown) {
        setApprovals(snapshot); // 回滚并给出可见反馈
        addToast(t(toastKeyForError(err)), { tone: 'danger', closeLabel: t('a11y.dismiss') });
      } finally {
        setDecidingId(null);
      }
    },
    [approvals, workspaceId, client, addToast, t],
  );

  const handleRejectConfirm = useCallback(() => {
    if (rejectTarget === null) return;
    const comment = rejectComment.trim() === '' ? undefined : rejectComment.trim();
    void applyDecision(rejectTarget, 'rejected', comment);
    setRejectTarget(null);
    setRejectComment('');
  }, [rejectTarget, rejectComment, applyDecision]);

  const selectTab = useCallback(
    (next: string) => {
      setSearchParams(next === 'pending' ? {} : { status: next });
    },
    [setSearchParams],
  );

  const tabItems = useMemo(
    () =>
      STATUS_FILTERS.map((s) => ({
        value: s,
        label: t(`approvals.status.${s}`),
        content: null,
      })),
    [t],
  );

  if (ws.kind === 'loading') {
    return (
      <main className="mesh-page mesh-approvals">
        <h1 className="mesh-text-title-1">{t('approvals.title')}</h1>
        <ApprovalsSkeleton label={t('common.loading')} />
      </main>
    );
  }
  if (ws.kind === 'error') {
    return (
      <main className="mesh-page mesh-approvals">
        <h1 className="mesh-text-title-1">{t('approvals.title')}</h1>
        <ErrorState
          title={t('state.errorTitle')}
          description={t('error.unknown')}
          retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((k) => k + 1)}
        />
      </main>
    );
  }
  if (ws.kind === 'no_workspace') {
    return (
      <main className="mesh-page mesh-approvals">
        <h1 className="mesh-text-title-1">{t('approvals.title')}</h1>
        <EmptyState title={t('approvals.noWorkspace.title')} />
      </main>
    );
  }
  if (ws.isAgentPrincipal) {
    return (
      <main className="mesh-page mesh-approvals" data-testid="approvals-agent-gated">
        <h1 className="mesh-text-title-1">{t('approvals.title')}</h1>
        <EmptyState
          title={t('approvals.agentGated.title')}
          description={t('approvals.agentGated.hint')}
        />
      </main>
    );
  }

  const nowMs = Date.now();
  const listPanel =
    isLoading === true ? (
      <ApprovalsSkeleton label={t('common.loading')} />
    ) : error !== null ? (
      <ErrorState
        title={t('state.errorTitle')}
        description={t(errorToI18nKey(error))}
        retryLabel={t('common.retry')}
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    ) : approvals.length === 0 ? (
      <EmptyState
        title={t(status === 'pending' ? 'approvals.empty.title' : 'approvals.emptyStatus.title')}
        description={t(
          status === 'pending' ? 'approvals.empty.hint' : 'approvals.emptyStatus.hint',
        )}
      />
    ) : (
      <div className="mesh-approvals__list" data-testid="approvals-list">
        {approvals.map((approval) => (
          <ApprovalCard
            key={approval.id}
            approval={approval}
            nowMs={nowMs}
            isDeciding={decidingId === approval.id}
            onApprove={(id) => {
              const target = approvals.find((a) => a.id === id);
              if (target !== undefined) void applyDecision(target, 'approved');
            }}
            onReject={(target) => {
              setRejectComment('');
              setRejectTarget(target);
            }}
          />
        ))}
      </div>
    );

  return (
    <main className="mesh-page mesh-approvals" data-testid="approvals-page">
      <h1 className="mesh-text-title-1">{t('approvals.title')}</h1>
      <Tabs
        items={tabItems.map((item) => ({ ...item, content: listPanel }))}
        value={status}
        onChange={selectTab}
        label={t('approvals.tabsLabel')}
      />
      <Dialog
        open={rejectTarget !== null}
        onClose={() => setRejectTarget(null)}
        title={t('approvals.reject.title')}
        closeLabel={t('a11y.closeDialog')}
      >
        <label className="mesh-approvals__comment-field">
          {t('approvals.reject.commentLabel')}
          <textarea
            className="mesh-approvals__comment-input"
            rows={4}
            value={rejectComment}
            data-testid="approval-reject-comment"
            onChange={(e) => setRejectComment(e.target.value)}
          />
        </label>
        <div className="mesh-approvals__dialog-actions">
          <Button
            variant="danger"
            onClick={handleRejectConfirm}
            data-testid="approval-reject-confirm"
          >
            {t('approvals.reject.confirm')}
          </Button>
          <Button variant="secondary" onClick={() => setRejectTarget(null)}>
            {t('approvals.reject.cancel')}
          </Button>
        </div>
      </Dialog>
    </main>
  );
}
