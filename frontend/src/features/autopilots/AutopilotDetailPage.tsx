/**
 * 规则详情页(autopilot.md §4.1):上半只读配置卡片([编辑][暂停/启用]
 * [手动运行]);下半执行历史时间线(按状态过滤,行点击进入运行详情)。
 * 订阅 autopilot:{id} 频道:autopilot.updated 重拉配置,
 * autopilot_runs.status_changed 重拉时间线(§3.5)。
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import {
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Select,
  Skeleton,
  StatusDot,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import {
  autopilotChannel,
  deleteAutopilot,
  getAutopilot,
  listAutopilotRuns,
  pauseAutopilot,
  previewSchedule,
  resumeAutopilot,
  testRunAutopilot,
} from './api';
import {
  RULE_STATUS_TONE,
  RUN_STATUS_TONE,
  errorSummary,
  formatDurationMs,
  formatRelativeTime,
} from './format';
import type { AutopilotRule, AutopilotRun, AutopilotRunStatus } from './types';
import './autopilots.css';

const RUN_STATUS_ALL = 'all';

const RUN_LIST_EVENTS: ReadonlySet<string> = new Set(['autopilot_runs.status_changed']);

export function AutopilotDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const { autopilotId } = useParams<{ autopilotId: string }>();

  const [membership, setMembership] = useState<Membership | null>(null);
  const [rule, setRule] = useState<AutopilotRule | null>(null);
  const [runs, setRuns] = useState<AutopilotRun[] | null>(null);
  const [preview, setPreview] = useState<string[] | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>(RUN_STATUS_ALL);
  const [reloadRunsKey, setReloadRunsKey] = useState(0);
  const [testDialogOpen, setTestDialogOpen] = useState(false);
  const [testPayload, setTestPayload] = useState('{}');
  const [testDryRun, setTestDryRun] = useState(false);
  const [testBusy, setTestBusy] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
    void (async () => {
      try {
        const me = await fetchMe(client);
        const workspace = activeWorkspace(me.memberships);
        if (cancelled || workspace === null || autopilotId === undefined) return;
        setMembership(workspace);
        const loaded = await getAutopilot(client, workspace.workspace_id, autopilotId);
        if (cancelled) return;
        setRule(loaded);
        setErrorKey(null);
        if (loaded.trigger_type === 'schedule') {
          try {
            const schedule = await previewSchedule(client, workspace.workspace_id, loaded.id, 5);
            if (!cancelled) setPreview([...schedule.next_runs]);
          } catch {
            setPreview(null);
          }
        }
      } catch (error) {
        if (cancelled) return;
        setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [autopilotId]);

  useEffect(() => {
    if (membership === null || autopilotId === undefined) return;
    let cancelled = false;
    const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
    void (async () => {
      try {
        const listing = await listAutopilotRuns(client, membership!.workspace_id, autopilotId, {
          status: statusFilter === RUN_STATUS_ALL ? undefined : statusFilter,
          limit: 30,
        });
        if (!cancelled) setRuns(listing.data);
      } catch (error) {
        if (!cancelled)
          setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [membership, autopilotId, statusFilter, reloadRunsKey]);

  useEffect(() => {
    if (realtime === null || autopilotId === undefined) return;
    const channel = autopilotChannel(autopilotId);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      if (frame.event === 'autopilot.updated') {
        const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
        if (membership !== null) {
          void getAutopilot(client, membership!.workspace_id, autopilotId)
            .then(setRule)
            .catch(() => undefined);
        }
      }
      if (RUN_LIST_EVENTS.has(frame.event)) setReloadRunsKey((key) => key + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, autopilotId, membership]);

  const runAction = useCallback(
    async (action: () => Promise<unknown>, successMessage: string) => {
      try {
        await action();
        toast.addToast(successMessage, { tone: 'success', closeLabel: t('common.close') });
        const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
        if (membership !== null && autopilotId !== undefined) {
          setRule(await getAutopilot(client, membership!.workspace_id, autopilotId));
        }
        setReloadRunsKey((key) => key + 1);
      } catch (error) {
        toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      }
    },
    [membership, autopilotId, toast, t],
  );

  const submitTestRun = useCallback(async () => {
    if (membership === null || autopilotId === undefined) return;
    setTestBusy(true);
    try {
      let payload: Record<string, unknown> = {};
      if (testPayload.trim()) payload = JSON.parse(testPayload) as Record<string, unknown>;
      const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
      const result = await testRunAutopilot(client, membership!.workspace_id, autopilotId, {
        simulate_trigger_payload: payload,
        dry_run: testDryRun,
      });
      if (testDryRun) {
        toast.addToast(
          result.would_run ? t('autopilots.testRun.wouldRun') : t('autopilots.testRun.wouldNotRun'),
          { tone: 'success', closeLabel: t('common.close') },
        );
      } else {
        toast.addToast(t('autopilots.testRun.started'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
        if (result.run_id) navigate(`/autopilots/runs/${result.run_id}`);
      }
      setTestDialogOpen(false);
      setReloadRunsKey((key) => key + 1);
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setTestBusy(false);
    }
  }, [membership, autopilotId, testPayload, testDryRun, toast, t, navigate]);

  if (errorKey !== null) {
    return (
      <div className="mesh-autopilots__page">
        <ErrorState
          title={t(errorKey)}
          retryLabel={t('common.retry')}
          onRetry={() => navigate('/autopilots')}
        />
      </div>
    );
  }
  if (rule === null) {
    return (
      <div className="mesh-autopilots__page">
        <Skeleton loadingLabel={t('autopilots.loading')} />
      </div>
    );
  }

  const locale = navigator.language;
  const nowMs = Date.now();

  return (
    <div className="mesh-autopilots__page" data-testid="autopilot-detail">
      <div className="mesh-autopilots__header">
        <h1 className="mesh-autopilots__title" data-testid="autopilot-detail-name">
          {rule.name}
        </h1>
        <div className="mesh-autopilots__toolbar">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => navigate(`/autopilots/${rule.id}/edit`)}
          >
            {t('autopilots.actions.edit')}
          </Button>
          {rule.status === 'active' && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                membership !== null &&
                runAction(
                  () =>
                    pauseAutopilot(
                      new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }),
                      membership!.workspace_id,
                      rule.id,
                    ),
                  t('autopilots.toast.paused'),
                )
              }
              data-testid="autopilot-detail-pause"
            >
              {t('autopilots.actions.pause')}
            </Button>
          )}
          {rule.status === 'paused' && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                membership !== null &&
                runAction(
                  () =>
                    resumeAutopilot(
                      new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }),
                      membership!.workspace_id,
                      rule.id,
                    ),
                  t('autopilots.toast.resumed'),
                )
              }
              data-testid="autopilot-detail-resume"
            >
              {t('autopilots.actions.resume')}
            </Button>
          )}
          <Button
            variant="primary"
            size="sm"
            onClick={() => setTestDialogOpen(true)}
            data-testid="autopilot-detail-test-run"
          >
            {t('autopilots.actions.testRun')}
          </Button>
          <Button variant="danger" size="sm" onClick={() => setConfirmDeleteOpen(true)}>
            {t('autopilots.actions.delete')}
          </Button>
        </div>
      </div>

      <div className="mesh-autopilots__card">
        <h2>{t('autopilots.detail.configTitle')}</h2>
        <dl className="mesh-autopilots__kv">
          <dt>{t('autopilots.columns.status')}</dt>
          <dd>
            <StatusDot
              tone={RULE_STATUS_TONE[rule.status]}
              label={t(`autopilots.status.${rule.status}`)}
            />
          </dd>
          <dt>{t('autopilots.columns.trigger')}</dt>
          <dd data-testid="autopilot-detail-trigger">
            {t(`autopilots.trigger.${rule.trigger_type}`)}
          </dd>
          <dt>{t('autopilots.detail.triggerConfig')}</dt>
          <dd>
            <pre className="mesh-autopilots__json" data-testid="autopilot-detail-trigger-config">
              {JSON.stringify(rule.trigger_config, null, 2)}
            </pre>
          </dd>
          <dt>{t('autopilots.detail.filterConfig')}</dt>
          <dd>
            <pre className="mesh-autopilots__json">
              {JSON.stringify(rule.filter_config, null, 2)}
            </pre>
          </dd>
          <dt>{t('autopilots.detail.actions')}</dt>
          <dd data-testid="autopilot-detail-actions">
            {rule.action_config.map((action, index) => (
              <div key={index}>
                {index + 1}. {t(`autopilots.action.${action.type}`)}
              </div>
            ))}
          </dd>
          <dt>{t('autopilots.detail.guardrails')}</dt>
          <dd>
            <pre className="mesh-autopilots__json">{JSON.stringify(rule.guardrails, null, 2)}</pre>
          </dd>
          <dt>{t('autopilots.detail.retry')}</dt>
          <dd>
            {rule.max_retries} · {t(`autopilots.backoff.${rule.retry_backoff}`)} ·{' '}
            {rule.retry_base_seconds}s → {rule.retry_max_seconds}s
          </dd>
          <dt>{t('autopilots.detail.rateLimit')}</dt>
          <dd>
            {rule.rate_limit_max} / {rule.rate_limit_window_seconds}s ·{' '}
            {t('autopilots.detail.concurrency')} {rule.concurrency_limit}
          </dd>
          {preview !== null && (
            <>
              <dt>{t('autopilots.editor.previewTitle')}</dt>
              <dd data-testid="autopilot-detail-preview">
                <ul>
                  {preview.map((moment) => (
                    <li key={moment}>{new Date(moment).toLocaleString()}</li>
                  ))}
                </ul>
              </dd>
            </>
          )}
        </dl>
      </div>

      <div className="mesh-autopilots__card">
        <div className="mesh-autopilots__header">
          <h2>{t('autopilots.runs.title')}</h2>
          <Select
            label={t('autopilots.filters.status')}
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value={RUN_STATUS_ALL}>{t('autopilots.filters.all')}</option>
            {(
              [
                'pending',
                'running',
                'waiting_approval',
                'retrying',
                'succeeded',
                'failed',
                'cancelled',
              ] as AutopilotRunStatus[]
            ).map((status) => (
              <option key={status} value={status}>
                {t(`autopilots.runStatus.${status}`)}
              </option>
            ))}
          </Select>
        </div>
        {runs === null && <Skeleton loadingLabel={t('autopilots.loading')} />}
        {runs !== null && runs.length === 0 && (
          <EmptyState title={t('autopilots.runs.empty')} description="" />
        )}
        {runs !== null && runs.length > 0 && (
          <table className="mesh-autopilots__runs-table" data-testid="autopilot-runs-table">
            <caption className="sr-only">{t('autopilots.runs.title')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('autopilots.runs.status')}</th>
                <th scope="col">{t('autopilots.runs.triggered')}</th>
                <th scope="col">{t('autopilots.runs.duration')}</th>
                <th scope="col">{t('autopilots.runs.tokens')}</th>
                <th scope="col">{t('autopilots.runs.retries')}</th>
                <th scope="col">{t('autopilots.runs.error')}</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr
                  key={run.id}
                  className="mesh-autopilots__row"
                  data-testid={`autopilot-run-row-${run.id}`}
                  onClick={() => navigate(`/autopilots/runs/${run.id}`)}
                >
                  <td>
                    <StatusDot
                      tone={RUN_STATUS_TONE[run.status]}
                      label={t(`autopilots.runStatus.${run.status}`)}
                    />
                    {run.is_test ? ` · ${t('autopilots.runs.test')}` : ''}
                  </td>
                  <td>{formatRelativeTime(run.created_at, nowMs, locale)}</td>
                  <td>{formatDurationMs(run.duration_ms) ?? '—'}</td>
                  <td>{run.total_tokens > 0 ? run.total_tokens : '—'}</td>
                  <td>{run.retry_count}</td>
                  <td>{errorSummary(run.error as Record<string, unknown> | null) ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Dialog
        open={testDialogOpen}
        onClose={() => setTestDialogOpen(false)}
        title={t('autopilots.testRun.dialogTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-autopilots__field">
          <label htmlFor="autopilot-test-payload">{t('autopilots.testRun.payloadLabel')}</label>
          <textarea
            id="autopilot-test-payload"
            rows={5}
            value={testPayload}
            onChange={(event) => setTestPayload(event.target.value)}
            data-testid="autopilot-test-payload"
          />
        </div>
        <label>
          <input
            type="checkbox"
            checked={testDryRun}
            onChange={(event) => setTestDryRun(event.target.checked)}
            data-testid="autopilot-test-dry-run"
          />{' '}
          {t('autopilots.testRun.dryRunLabel')}
        </label>
        <div className="mesh-autopilots__footer">
          <Button variant="ghost" onClick={() => setTestDialogOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            isLoading={testBusy}
            onClick={() => void submitTestRun()}
            data-testid="autopilot-test-submit"
          >
            {t('autopilots.testRun.submit')}
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={confirmDeleteOpen}
        onClose={() => setConfirmDeleteOpen(false)}
        title={t('autopilots.delete.dialogTitle')}
        closeLabel={t('common.close')}
      >
        <p>{t('autopilots.delete.confirmText')}</p>
        <div className="mesh-autopilots__footer">
          <Button variant="ghost" onClick={() => setConfirmDeleteOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
              void deleteAutopilot(client, membership!.workspace_id, rule.id)
                .then(() => navigate('/autopilots'))
                .catch((error: unknown) =>
                  toast.addToast(
                    t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'),
                    { tone: 'danger', closeLabel: t('common.close') },
                  ),
                );
            }}
            data-testid="autopilot-delete-confirm"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
