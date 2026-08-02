/**
 * 统一审批页(README §6.10,G10 深链):平直 /approvals 与
 * /w/:workspaceSlug/approvals 双路由(工作区解析见 useApprovalsWorkspace)。
 * pending 默认视图(role=mine),状态页签经 URL ?status= 同步;人类专属——
 * agent principal 呈现门控空态(§6.10:agent 不可审批,防自批)。
 * 决定走乐观更新 + 单行失败回滚;成功后立即采用后端幂等真值。
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
import { approveApproval, getApproval, listApprovals, rejectApproval } from './api';
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
  const focusedApprovalId = searchParams.get('approval_id')?.trim() || null;
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
    const request =
      focusedApprovalId === null
        ? listApprovals(
            client,
            workspaceId,
            status === 'pending' ? { role: 'mine' as const } : { status },
          ).then((envelope) => envelope.data)
        : getApproval(client, workspaceId, focusedApprovalId).then((approval) => [approval]);
    request
      .then((rows) => {
        if (!cancelled) setApprovals(rows);
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
  }, [client, workspaceId, status, focusedApprovalId, reloadKey, isAgentGated]);

  const applyDecision = useCallback(
    async (approval: Approval, decision: 'approved' | 'rejected', comment?: string) => {
      if (workspaceId === null) return;
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
        const committed =
          decision === 'approved'
            ? await approveApproval(client, workspaceId, approval.id, {})
            : await rejectApproval(client, workspaceId, approval.id, { comment });
        setApprovals((prev) => prev.map((row) => (row.id === approval.id ? committed : row)));
        const committedToastKey =
          committed.status === 'approved' || committed.status === 'rejected'
            ? `approvals.toast.${committed.status}`
            : `approvals.status.${committed.status}`;
        addToast(
          t(committedToastKey),
          {
            tone:
              committed.status === 'approved' || committed.status === 'rejected'
                ? 'success'
                : 'info',
            closeLabel: t('a11y.dismiss'),
          },
        );
      } catch (err: unknown) {
        // Restore only this operation's row. A concurrent decision on a
        // different approval must never be erased by this request failing.
        setApprovals((prev) => prev.map((row) => (row.id === approval.id ? approval : row)));
        addToast(t(toastKeyForError(err)), { tone: 'danger', closeLabel: t('a11y.dismiss') });
      } finally {
        setDecidingId(null);
      }
    },
    [workspaceId, client, addToast, t],
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
      {focusedApprovalId === null ? (
        <Tabs
          items={tabItems.map((item) => ({ ...item, content: listPanel }))}
          value={status}
          onChange={selectTab}
          label={t('approvals.tabsLabel')}
        />
      ) : (
        <section data-testid="approvals-focused-detail">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSearchParams({})}
            data-testid="approvals-focused-back"
          >
            {t('common.back')}
          </Button>
          {listPanel}
        </section>
      )}
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
