/**
 * 单次运行详情页(autopilot.md §4.1 / §4.2):输入快照(JSON 只读)、产物列表、
 * 尝试明细(每次尝试的起止 / 错误 / token)。waiting_approval 提供审批
 * 批准 / 拒绝(README §6.10 统一审批的 autopilot 薄封装);在途运行可取消
 * (两段式语义由后端收口)。run 状态经 autopilot:{id} 频道实时刷新(§3.5)。
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import { Button, ErrorState, Skeleton, StatusDot, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import {
  approveRun,
  autopilotChannel,
  cancelRun,
  getAutopilotRun,
  rejectRun,
} from './api';
import { RUN_STATUS_TONE, errorSummary, formatDurationMs } from './format';
import type { AutopilotRun, RunArtifact } from './types';

/** §4.2 产物列表「带跳转」:按 ref_table 映射到应用内路由。 */
function artifactLink(run: AutopilotRun, artifact: RunArtifact): string | null {
  if (artifact.ref_table === 'issues') return `/issues/${artifact.ref_id}`;
  if (artifact.ref_table === 'task_executions') return `/executions/${artifact.ref_id}`;
  if (artifact.ref_table === 'notifications') return '/inbox';
  if (artifact.ref_table === 'comments') {
    const issue = run.trigger_snapshot.issue as { id?: string } | undefined;
    return issue !== undefined && typeof issue.id === 'string' ? `/issues/${issue.id}` : null;
  }
  return null;
}
import './autopilots.css';

/** 可审批 / 可取消的状态集(§4.4 状态机)。 */
const APPROVABLE_STATUSES: ReadonlySet<string> = new Set(['waiting_approval']);
const CANCELLABLE_STATUSES: ReadonlySet<string> = new Set([
  'pending',
  'running',
  'retrying',
  'waiting_approval',
]);

export function AutopilotRunDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const { runId } = useParams<{ runId: string }>();

  const [membership, setMembership] = useState<Membership | null>(null);
  const [run, setRun] = useState<AutopilotRun | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const loadRun = useCallback(async () => {
    if (membership === null || runId === undefined) return;
    try {
      const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
      setRun(await getAutopilotRun(client, membership!.workspace_id, runId));
      setErrorKey(null);
    } catch (error) {
      setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
    }
  }, [membership, runId]);

  useEffect(() => {
    let cancelled = false;
    const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
    void (async () => {
      const me = await fetchMe(client);
      const workspace = activeWorkspace(me.memberships);
      if (!cancelled) setMembership(workspace);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void loadRun();
  }, [loadRun, reloadKey]);

  useEffect(() => {
    if (realtime === null || run === null) return;
    const channel = autopilotChannel(run.autopilot_id);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel || frame.event !== 'autopilot_runs.status_changed') return;
      const payload = frame.payload as { run_id?: string } | undefined;
      if (payload !== undefined && payload.run_id === run.id) setReloadKey((key) => key + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, run]);

  const decide = useCallback(
    async (approve: boolean) => {
      if (membership === null || runId === undefined) return;
      try {
        const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
        if (approve) await approveRun(client, membership!.workspace_id, runId);
        else await rejectRun(client, membership!.workspace_id, runId);
        toast.addToast(
          approve ? t('autopilots.runDetail.approved') : t('autopilots.runDetail.rejected'),
          { tone: 'success', closeLabel: t('common.close') },
        );
        setReloadKey((key) => key + 1);
      } catch (error) {
        toast.addToast(
          t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'),
          { tone: 'danger', closeLabel: t('common.close') },
        );
      }
    },
    [membership, runId, toast, t],
  );

  const cancel = useCallback(async () => {
    if (membership === null || runId === undefined) return;
    try {
      const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
      await cancelRun(client, membership!.workspace_id, runId);
      toast.addToast(t('autopilots.runDetail.cancelled'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setReloadKey((key) => key + 1);
    } catch (error) {
      toast.addToast(
        t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'),
        { tone: 'danger', closeLabel: t('common.close') },
      );
    }
  }, [membership, runId, toast, t]);

  if (errorKey !== null) {
    return (
      <div className="mesh-autopilots__page">
        <ErrorState title={t(errorKey)} retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((key) => key + 1)} />
      </div>
    );
  }
  if (run === null) {
    return (
      <div className="mesh-autopilots__page">
        <Skeleton loadingLabel={t('autopilots.loading')} />
      </div>
    );
  }

  return (
    <div className="mesh-autopilots__page mesh-autopilots__run-detail" data-testid="autopilot-run-detail">
      <div className="mesh-autopilots__header">
        <h1 className="mesh-autopilots__title">
          {t('autopilots.runDetail.title')}
          {run.is_test ? ` · ${t('autopilots.runs.test')}` : ''}
        </h1>
        <div className="mesh-autopilots__toolbar">
          <Button variant="ghost" size="sm" onClick={() => navigate(`/autopilots/${run.autopilot_id}`)}>
            {t('autopilots.runDetail.backToRule')}
          </Button>
          {APPROVABLE_STATUSES.has(run.status) && (
            <>
              <Button
                variant="primary"
                size="sm"
                onClick={() => void decide(true)}
                data-testid="autopilot-run-approve"
              >
                {t('autopilots.runDetail.approve')}
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => void decide(false)}
                data-testid="autopilot-run-reject"
              >
                {t('autopilots.runDetail.reject')}
              </Button>
            </>
          )}
          {CANCELLABLE_STATUSES.has(run.status) && (
            <Button variant="secondary" size="sm" onClick={() => void cancel()} data-testid="autopilot-run-cancel">
              {t('autopilots.actions.cancelRun')}
            </Button>
          )}
        </div>
      </div>

      <div className="mesh-autopilots__card">
        <h3>{t('autopilots.runDetail.overview')}</h3>
        <dl className="mesh-autopilots__kv">
          <dt>{t('autopilots.runs.status')}</dt>
          <dd data-testid="autopilot-run-status">
            <StatusDot tone={RUN_STATUS_TONE[run.status]} label={t(`autopilots.runStatus.${run.status}`)} />
          </dd>
          <dt>{t('autopilots.runDetail.triggerType')}</dt>
          <dd>{t(`autopilots.trigger.${run.trigger_type}`)}</dd>
          <dt>{t('autopilots.runDetail.cascadeDepth')}</dt>
          <dd>{run.cascade_depth}</dd>
          <dt>{t('autopilots.runDetail.startedAt')}</dt>
          <dd>{run.started_at ? new Date(run.started_at).toLocaleString() : '—'}</dd>
          <dt>{t('autopilots.runDetail.finishedAt')}</dt>
          <dd>{run.finished_at ? new Date(run.finished_at).toLocaleString() : '—'}</dd>
          <dt>{t('autopilots.runs.duration')}</dt>
          <dd>{formatDurationMs(run.duration_ms) ?? '—'}</dd>
          <dt>{t('autopilots.runs.retries')}</dt>
          <dd>{run.retry_count}</dd>
          <dt>{t('autopilots.runs.tokens')}</dt>
          <dd>
            {run.total_tokens} ({run.prompt_tokens ?? 0} + {run.completion_tokens ?? 0})
          </dd>
          {run.execution_id !== null && (
            <>
              <dt>{t('autopilots.runDetail.execution')}</dt>
              <dd>
                <Button variant="ghost" size="sm" onClick={() => navigate(`/executions/${run.execution_id}`)}>
                  {run.execution_id}
                </Button>
              </dd>
            </>
          )}
          {run.error !== null && (
            <>
              <dt>{t('autopilots.runs.error')}</dt>
              <dd data-testid="autopilot-run-error">{errorSummary(run.error as Record<string, unknown>)}</dd>
            </>
          )}
        </dl>
      </div>

      <div className="mesh-autopilots__card">
        <h3>{t('autopilots.runDetail.snapshotTitle')}</h3>
        <pre className="mesh-autopilots__json" data-testid="autopilot-run-snapshot">
          {JSON.stringify(run.trigger_snapshot, null, 2)}
        </pre>
      </div>

      <div className="mesh-autopilots__card">
        <h3>{t('autopilots.runDetail.attemptsTitle')}</h3>
        {run.attempts === undefined || run.attempts.length === 0 ? (
          <p>{t('autopilots.runDetail.noAttempts')}</p>
        ) : (
          <table className="mesh-autopilots__runs-table" data-testid="autopilot-run-attempts">
            <thead>
              <tr>
                <th>#</th>
                <th>{t('autopilots.runs.status')}</th>
                <th>{t('autopilots.runDetail.startedAt')}</th>
                <th>{t('autopilots.runDetail.finishedAt')}</th>
                <th>{t('autopilots.runs.tokens')}</th>
                <th>{t('autopilots.runs.error')}</th>
              </tr>
            </thead>
            <tbody>
              {run.attempts.map((attempt) => (
                <tr key={attempt.attempt_number}>
                  <td>{attempt.attempt_number}</td>
                  <td>{attempt.status}</td>
                  <td>{attempt.started_at ? new Date(attempt.started_at).toLocaleString() : '—'}</td>
                  <td>{attempt.finished_at ? new Date(attempt.finished_at).toLocaleString() : '—'}</td>
                  <td>{(attempt.prompt_tokens ?? 0) + (attempt.completion_tokens ?? 0)}</td>
                  <td>{errorSummary(attempt.error as Record<string, unknown> | null) ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="mesh-autopilots__card">
        <h3>{t('autopilots.runDetail.artifactsTitle')}</h3>
        {run.artifacts === undefined || run.artifacts.length === 0 ? (
          <p>{t('autopilots.runDetail.noArtifacts')}</p>
        ) : (
          <table className="mesh-autopilots__runs-table" data-testid="autopilot-run-artifacts">
            <thead>
              <tr>
                <th>{t('autopilots.runDetail.artifactType')}</th>
                <th>{t('autopilots.runDetail.artifactRef')}</th>
                <th>{t('autopilots.runDetail.artifactSummary')}</th>
              </tr>
            </thead>
            <tbody>
              {run.artifacts.map((artifact) => {
                const link = artifactLink(run, artifact);
                return (
                  <tr key={artifact.id}>
                    <td>{t(`autopilots.artifact.${artifact.artifact_type}`)}</td>
                    <td>
                      {link !== null ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => navigate(link)}
                          data-testid={`autopilot-artifact-link-${artifact.id}`}
                        >
                          {artifact.ref_table}:{artifact.ref_id}
                        </Button>
                      ) : (
                        <>
                          {artifact.ref_table}:{artifact.ref_id}
                        </>
                      )}
                    </td>
                    <td>{artifact.summary ?? '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
