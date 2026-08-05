import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, ErrorState, Skeleton, StatusDot, useToast } from '../../design';
import type { StatusDotTone } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import {
  cancelExecution,
  listWorkspaceExecutions,
  workspaceExecutionsChannel,
} from '../runtimes/api';
import type { AttemptSummary, ExecutionDetail, ExecutionStatus } from '../runtimes/types';

const ACTIVE_STATUSES: ReadonlySet<ExecutionStatus> = new Set([
  'queued',
  'claimed',
  'running',
  'cancelling',
  'awaiting_approval',
]);

const CANCELLABLE_STATUSES: ReadonlySet<ExecutionStatus> = new Set([
  'queued',
  'claimed',
  'running',
  'awaiting_approval',
]);

const STATUS_TONE: Readonly<Record<ExecutionStatus, StatusDotTone>> = {
  queued: 'neutral',
  claimed: 'info',
  running: 'info',
  cancelling: 'warn',
  awaiting_approval: 'warn',
  completed: 'success',
  failed: 'danger',
  timeout: 'danger',
  cancelled: 'neutral',
};

export interface IssueExecutionsPanelProps {
  readonly workspaceId: string;
  readonly workspaceSlug: string;
  readonly issueId: string;
  readonly reviewable?: boolean;
  readonly onApprove?: (executionId: string) => void | Promise<void>;
  readonly onRequestChanges?: (executionId: string) => void | Promise<void>;
}

function latestAttempt(execution: ExecutionDetail): AttemptSummary | null {
  const attempts = execution.attempts ?? [];
  return attempts.length === 0 ? null : (attempts[attempts.length - 1] ?? null);
}

function executionHref(workspaceSlug: string, executionId: string): string {
  return `/w/${encodeURIComponent(workspaceSlug)}/executions/${encodeURIComponent(executionId)}`;
}

/**
 * Issue 详情内的执行反查面板。列表一次返回 execution + 全部 attempts，避免详情页
 * 出现 N+1；活跃运行可在原地停止，终态均保留深链供日志和审计复核。
 */
export function IssueExecutionsPanel(props: IssueExecutionsPanelProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [executions, setExecutions] = useState<ExecutionDetail[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [busyExecutionId, setBusyExecutionId] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [locallyReviewedExecutionIds, setLocallyReviewedExecutionIds] = useState<
    ReadonlySet<string>
  >(new Set());

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    void listWorkspaceExecutions(client, props.workspaceId, {
      issue_id: props.issueId,
      limit: 100,
    })
      .then((page) => {
        if (!cancelled) {
          setExecutions([...page.data] as ExecutionDetail[]);
          setNextCursor(page.nextCursor);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(t(key));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, props.issueId, props.workspaceId, reloadKey, t]);

  const loadMore = useCallback(async () => {
    if (nextCursor === null || isLoadingMore) return;
    setIsLoadingMore(true);
    try {
      const page = await listWorkspaceExecutions(client, props.workspaceId, {
        issue_id: props.issueId,
        limit: 100,
        cursor: nextCursor,
      });
      setExecutions((current) => {
        const known = new Set(current.map((execution) => execution.id));
        return [
          ...current,
          ...(page.data.filter((execution) => !known.has(execution.id)) as ExecutionDetail[]),
        ];
      });
      setNextCursor(page.nextCursor);
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setIsLoadingMore(false);
    }
  }, [client, isLoadingMore, nextCursor, props.issueId, props.workspaceId, t, toast]);

  // queued/started 走工作区频道，terminal 走 execution 频道。任一权威帧只触发一次
  // REST 对账；展示不依赖不完整的实时 payload，也不会把旧帧覆盖到新 attempt 上。
  useEffect(() => {
    if (realtime === null) return;
    const workspaceChannel = workspaceExecutionsChannel(props.workspaceId);
    const executionChannels = executions.map((execution) => `execution:${execution.id}`);
    realtime.client.subscribe(workspaceChannel);
    for (const channel of executionChannels) realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (!frame.event.startsWith('execution.')) return;
      const payload = frame.payload as { execution_id?: unknown; issue_id?: unknown };
      const executionId = typeof payload.execution_id === 'string' ? payload.execution_id : null;
      const belongsToIssue = payload.issue_id === props.issueId;
      const isKnown =
        executionId !== null && executions.some((execution) => execution.id === executionId);
      if (belongsToIssue || isKnown) setReloadKey((value) => value + 1);
    });
    return () => {
      off();
      realtime.client.unsubscribe(workspaceChannel);
      for (const channel of executionChannels) realtime.client.unsubscribe(channel);
    };
  }, [executions, props.issueId, props.workspaceId, realtime]);

  const cancel = useCallback(
    async (execution: ExecutionDetail) => {
      setBusyExecutionId(execution.id);
      try {
        const updated = await cancelExecution(client, props.workspaceId, execution.id);
        setExecutions((current) =>
          current.map((item) =>
            item.id === execution.id
              ? { ...item, ...updated, attempts: updated.attempts ?? item.attempts }
              : item,
          ),
        );
        toast.addToast(t('issues.executions.cancelled'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      } finally {
        setBusyExecutionId(null);
      }
    },
    [client, props.workspaceId, t, toast],
  );

  const review = useCallback(
    async (executionId: string, decision: 'approved' | 'rejected') => {
      setBusyExecutionId(executionId);
      try {
        if (decision === 'approved') await props.onApprove?.(executionId);
        else await props.onRequestChanges?.(executionId);
        setLocallyReviewedExecutionIds((current) => new Set(current).add(executionId));
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      } finally {
        setBusyExecutionId(null);
      }
    },
    [props, t, toast],
  );

  if (isLoading) {
    return <Skeleton loadingLabel={t('common.loading')} />;
  }
  if (error !== null) {
    return (
      <ErrorState
        title={t('issues.executions.loadError')}
        description={error}
        retryLabel={t('common.retry')}
        onRetry={() => setReloadKey((value) => value + 1)}
      />
    );
  }

  return (
    <section className="mesh-issue-executions" data-testid="issue-executions-panel">
      <header className="mesh-issue-executions__header">
        <h2>{t('issues.executions.title')}</h2>
        <span className="mesh-issue-executions__count" data-testid="issue-executions-count">
          {executions.length}
        </span>
      </header>

      {executions.length === 0 ? (
        <p className="mesh-issues-detail__empty" data-testid="issue-executions-empty">
          {t('issues.executions.empty')}
        </p>
      ) : (
        <ol className="mesh-issue-executions__list">
          {executions.map((execution) => {
            const attempt = latestAttempt(execution);
            const active = ACTIVE_STATUSES.has(execution.status);
            // API ordering is newest-first. Only that exact execution is the
            // current review candidate; an older completed run can never close
            // an issue after a newer run has started or completed.
            const reviewActions =
              props.reviewable === true &&
              execution.id === executions[0]?.id &&
              execution.status === 'completed' &&
              execution.output_review == null &&
              !locallyReviewedExecutionIds.has(execution.id);
            return (
              <li
                className="mesh-issue-executions__item"
                data-active={active ? 'true' : 'false'}
                key={execution.id}
              >
                <div className="mesh-issue-executions__summary">
                  <Link
                    className="mesh-issue-executions__link"
                    data-testid={`issue-execution-link-${execution.id}`}
                    to={executionHref(props.workspaceSlug, execution.id)}
                  >
                    {t('issues.executions.runNumber', {
                      number: execution.id.slice(0, 8),
                    })}
                  </Link>
                  <span
                    data-status={execution.status}
                    data-testid={`issue-execution-status-${execution.id}`}
                  >
                    <StatusDot
                      tone={STATUS_TONE[execution.status]}
                      label={t(`runtimes.execution.status.${execution.status}`)}
                    />
                  </span>
                </div>

                <dl className="mesh-issue-executions__meta">
                  <div>
                    <dt>{t('issues.executions.runtime')}</dt>
                    <dd data-testid={`issue-execution-runtime-${execution.id}`}>
                      {attempt?.runtime_name ?? '—'}
                    </dd>
                  </div>
                  <div>
                    <dt>{t('issues.executions.attempts')}</dt>
                    <dd>{execution.attempts?.length ?? 0}</dd>
                  </div>
                  <div>
                    <dt>{t('issues.executions.trigger')}</dt>
                    <dd>{t(`runtimes.execution.triggerKind.${execution.trigger}`)}</dd>
                  </div>
                </dl>

                {active ? (
                  <div className="mesh-issue-executions__active">
                    <div
                      className="mesh-issue-executions__progress"
                      role="progressbar"
                      aria-label={t('issues.executions.progress')}
                      aria-valuetext={t(`runtimes.execution.status.${execution.status}`)}
                      data-testid={`issue-execution-progress-${execution.id}`}
                    >
                      <span />
                    </div>
                    {CANCELLABLE_STATUSES.has(execution.status) ? (
                      <Button
                        size="sm"
                        variant="danger"
                        isLoading={busyExecutionId === execution.id}
                        data-testid={`issue-execution-cancel-${execution.id}`}
                        onClick={() => void cancel(execution)}
                      >
                        {t('issues.executions.stop')}
                      </Button>
                    ) : null}
                  </div>
                ) : null}

                {execution.failure_reason !== null ? (
                  <p className="mesh-issue-executions__failure">
                    {t('issues.executions.failure', { reason: execution.failure_reason })}
                  </p>
                ) : null}

                {reviewActions ? (
                  <div className="mesh-issue-executions__review">
                    <Button
                      size="sm"
                      isLoading={busyExecutionId === execution.id}
                      data-testid={`issue-execution-approve-${execution.id}`}
                      onClick={() => void review(execution.id, 'approved')}
                    >
                      {t('issues.executions.approve')}
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      isLoading={busyExecutionId === execution.id}
                      data-testid={`issue-execution-reject-${execution.id}`}
                      onClick={() => void review(execution.id, 'rejected')}
                    >
                      {t('issues.executions.requestChanges')}
                    </Button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      {nextCursor !== null ? (
        <Button
          size="sm"
          variant="secondary"
          isLoading={isLoadingMore}
          data-testid="issue-executions-load-more"
          onClick={() => void loadMore()}
        >
          {t('issues.executions.loadMore')}
        </Button>
      ) : null}
    </section>
  );
}
