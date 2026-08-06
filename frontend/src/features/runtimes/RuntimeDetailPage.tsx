/**
 * Runtime 详情页(runtime.md §4.2):头部元数据(状态 / 主机 / OS / CPU / 内存 /
 * 并发 / 守护进程版本)+ 标签 / 能力 chips + 正在执行(带取消)+ 历史任务;
 * 动作:暂停 / 恢复 / 轮换 token(新 token 仅一次性呈现,复制 + 警告,§3.1)。
 *
 * 实时:workspace:{ws}:runtimes(runtime.* 命中本 id)+ workspace:{ws}:executions
 * (队列 / 领取 / 启动 / 审批挂起)触发重拉(README §6.7)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import {
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Skeleton,
  StatusDot,
  useToast,
} from '../../design';
import type { StatusDotTone } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import type { TranslateFn } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { useWorkspaceMembership, workspaceRoute } from '../members/useWorkspaceMembership';
import {
  cancelExecution,
  executionChannel,
  getRuntime,
  listRuntimeExecutions,
  pauseRuntime,
  resumeRuntime,
  rotateRuntimeToken,
  workspaceExecutionsChannel,
  workspaceRuntimesChannel,
} from './api';
import { executionDisplayLabel } from './executionLabel';
import { formatDurationSeconds, formatMemoryMb } from './format';
import type {
  ExecutionDetail,
  RuntimeDetail,
  RuntimeOperationalState,
  RuntimeStatus,
} from './types';
import './runtimes.css';

const STATUS_TONE: Record<RuntimeStatus, StatusDotTone> = {
  pending: 'info',
  online: 'success',
  unavailable: 'danger',
  paused: 'warn',
  draining: 'warn',
  decommissioned: 'neutral',
};

const OPERATIONAL_TONE: Record<RuntimeOperationalState, StatusDotTone> = {
  online: 'success',
  degraded: 'warn',
  isolated: 'danger',
  paused: 'neutral',
};

const OPERATIONAL_FALLBACK: Record<RuntimeOperationalState, string> = {
  online: 'Online',
  degraded: 'Degraded',
  isolated: 'Isolated',
  paused: 'Paused',
};

const REREGISTER_COMMAND = 'mesh-runtime activate --config <config-file> --activation-code-stdin';

function localText(
  t: TranslateFn,
  key: string,
  fallback: string,
  values?: Record<string, unknown>,
): string {
  const translated = t(key, values);
  return translated === key || translated.includes(`[${key}]`) ? fallback : translated;
}

function operationalState(runtime: RuntimeDetail): RuntimeOperationalState {
  if (runtime.operational_state !== undefined) return runtime.operational_state;
  if (runtime.status === 'paused') return 'paused';
  return runtime.status === 'online' ? 'online' : 'degraded';
}

/** 在途执行:逻辑态未到终态且非审批挂起(§4.2「正在执行」区块)。 */
const INFLIGHT_STATUSES: ReadonlySet<string> = new Set([
  'queued',
  'claimed',
  'running',
  'cancelling',
]);

const RUNTIME_DETAIL_EVENTS: ReadonlySet<string> = new Set([
  'runtime.activated',
  'runtime.online',
  'runtime.offline',
  'runtime.degraded',
  'runtime.isolated',
  'runtime.paused',
]);

const EXECUTION_TERMINAL_EVENTS: ReadonlySet<string> = new Set([
  'execution.completed',
  'execution.failed',
  'execution.timeout',
  'execution.cancelled',
]);

export function RuntimeDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const { runtimeId } = useParams<{ runtimeId: string }>();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const membershipState = useWorkspaceMembership(client);
  const workspace = membershipState.kind === 'ready' ? membershipState.membership : null;
  const canManage = workspace?.role === 'owner' || workspace?.role === 'admin';
  const canTrigger = workspace !== null && workspace.role !== 'guest';

  const [runtime, setRuntime] = useState<RuntimeDetail | null>(null);
  const [executions, setExecutions] = useState<ExecutionDetail[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [rotatedToken, setRotatedToken] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const load = useCallback(() => {
    if (workspace === null || runtimeId === undefined) return;
    setIsLoading(true);
    setError(null);
    Promise.all([
      getRuntime(client, workspace.workspace_id, runtimeId),
      listRuntimeExecutions(client, workspace.workspace_id, runtimeId, { limit: 50 }),
    ])
      .then(([detail, page]) => {
        setRuntime(detail);
        setExecutions([...page.data]);
      })
      .catch((err) => setError(err instanceof Error ? err.message : t('state.errorDescription')))
      .finally(() => setIsLoading(false));
  }, [client, workspace, runtimeId, t]);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // 实时重拉:本 runtime 生命周期 + broad 工作区非终态 + 当前列表 execution 终态。
  // 私有 issue 的终态不会进入 broad workspace 频道，因此必须按已知 execution 订阅。
  useEffect(() => {
    if (realtime === null || workspace === null) return;
    const runtimesChannel = workspaceRuntimesChannel(workspace.workspace_id);
    const executionsChannel = workspaceExecutionsChannel(workspace.workspace_id);
    const detailChannels = executions.map((execution) => executionChannel(execution.id));
    const detailChannelSet = new Set(detailChannels);
    realtime.client.subscribe(runtimesChannel);
    realtime.client.subscribe(executionsChannel);
    for (const channel of detailChannels) realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel === runtimesChannel) {
        if (!RUNTIME_DETAIL_EVENTS.has(frame.event)) return;
        const payload = frame.payload as { data?: { id?: string }; id?: string };
        const frameId = payload.data?.id ?? payload.id;
        if (frameId !== undefined && frameId !== runtimeId) return;
        setReloadKey((key) => key + 1);
        return;
      }
      if (frame.channel === executionsChannel) {
        setReloadKey((key) => key + 1);
        return;
      }
      if (detailChannelSet.has(frame.channel) && EXECUTION_TERMINAL_EVENTS.has(frame.event)) {
        setReloadKey((key) => key + 1);
      }
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(runtimesChannel);
      realtime.client.unsubscribe(executionsChannel);
      for (const channel of detailChannels) realtime.client.unsubscribe(channel);
    };
  }, [executions, realtime, workspace, runtimeId]);

  const act = async (action: () => Promise<unknown>, successMessage: string): Promise<void> => {
    try {
      await action();
      toast.addToast(successMessage, { tone: 'success', closeLabel: t('common.close') });
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const handleRotate = async (): Promise<void> => {
    if (workspace === null || runtimeId === undefined) return;
    try {
      const result = await rotateRuntimeToken(client, workspace.workspace_id, runtimeId);
      setRotatedToken(result.runtime_token);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const copyToken = async (): Promise<void> => {
    if (rotatedToken === null) return;
    try {
      await navigator.clipboard.writeText(rotatedToken);
      toast.addToast(t('runtimes.wizard.copied'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch {
      toast.addToast(t('runtimes.wizard.copyFailed'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const copyRepairCommand = async (command: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(command);
      toast.addToast(t('runtimes.wizard.copied'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch {
      toast.addToast(t('runtimes.wizard.copyFailed'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const exportDiagnostics = (): void => {
    if (runtime === null) return;
    const payload = {
      runtime_id: runtime.id,
      operational_state: operationalState(runtime),
      diagnostics: runtime.diagnostics ?? [],
      exported_at: new Date().toISOString(),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `runtime-${runtime.id}-diagnostics.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const inflight = useMemo(
    () => executions.filter((execution) => INFLIGHT_STATUSES.has(execution.status)),
    [executions],
  );
  const history = useMemo(
    () => executions.filter((execution) => !INFLIGHT_STATUSES.has(execution.status)),
    [executions],
  );

  const membershipError = membershipState.kind === 'error' ? t('state.errorDescription') : null;
  const visibleError = membershipError ?? error;

  if (visibleError !== null) {
    return (
      <div className="mesh-runtimes-detail">
        <ErrorState
          title={t('state.errorTitle')}
          description={visibleError}
          retryLabel={t('common.retry')}
          onRetry={
            membershipState.kind === 'error'
              ? membershipState.retry
              : () => setReloadKey((key) => key + 1)
          }
        />
      </div>
    );
  }

  if (membershipState.kind === 'no_workspace') {
    return (
      <div className="mesh-runtimes-detail">
        <EmptyState title={t('state.emptyTitle')} description={t('runtimes.noWorkspace')} />
      </div>
    );
  }

  if (membershipState.kind === 'loading' || isLoading || runtime === null) {
    return (
      <div className="mesh-runtimes-detail">
        <Skeleton loadingLabel={t('common.loading')} />
      </div>
    );
  }

  const memory = formatMemoryMb(runtime.memory_mb);
  const labelEntries = Object.entries(runtime.labels);
  const runtimeOperationalState = operationalState(runtime);
  const diagnostics = runtime.diagnostics ?? [];
  const canPause = runtime.status === 'online' || runtime.status === 'draining';
  const canResume = runtime.status === 'paused';

  return (
    <div className="mesh-runtimes-detail" data-testid="runtime-detail-page">
      <div className="mesh-runtimes-detail__header">
        <Button
          variant="ghost"
          data-testid="runtime-detail-back"
          onClick={() =>
            navigate(
              workspace === null
                ? '/runtimes'
                : workspaceRoute(workspace.workspace_slug, '/automations/runtimes'),
            )
          }
        >
          {t('runtimes.detail.back')}
        </Button>
        <h1 className="mesh-runtimes-detail__title" data-testid="runtime-detail-name">
          {runtime.name}
        </h1>
        <span data-testid="runtime-detail-status">
          <StatusDot
            tone={STATUS_TONE[runtime.status]}
            label={t(`runtimes.status.${runtime.status}`)}
          />
        </span>
        <div className="mesh-runtimes-detail__actions">
          {canManage && canPause ? (
            <Button
              variant="secondary"
              size="sm"
              data-testid="runtime-detail-pause"
              onClick={() =>
                void act(
                  () => pauseRuntime(client, workspace?.workspace_id ?? '', runtime.id),
                  t('runtimes.toast.paused'),
                )
              }
            >
              {t('runtimes.action.pause')}
            </Button>
          ) : null}
          {canManage && canResume ? (
            <Button
              variant="secondary"
              size="sm"
              data-testid="runtime-detail-resume"
              onClick={() =>
                void act(
                  () => resumeRuntime(client, workspace?.workspace_id ?? '', runtime.id),
                  t('runtimes.toast.resumed'),
                )
              }
            >
              {t('runtimes.action.resume')}
            </Button>
          ) : null}
          {canManage ? (
            <Button
              variant="secondary"
              size="sm"
              data-testid="runtime-detail-rotate"
              onClick={() => void handleRotate()}
            >
              {t('runtimes.action.rotateToken')}
            </Button>
          ) : null}
        </div>
      </div>

      <section
        className={`mesh-runtimes-detail__operational mesh-runtimes-detail__operational--${runtimeOperationalState}`}
        data-testid="runtime-operational-panel"
      >
        <div className="mesh-runtimes-detail__operational-header">
          <h2>{localText(t, 'runtimes.operational.title', 'Operational state')}</h2>
          <span data-testid="runtime-operational-state">
            <StatusDot
              tone={OPERATIONAL_TONE[runtimeOperationalState]}
              label={localText(
                t,
                `runtimes.operational.state.${runtimeOperationalState}`,
                OPERATIONAL_FALLBACK[runtimeOperationalState],
              )}
            />
          </span>
        </div>

        {runtimeOperationalState === 'online' ? (
          <p>
            {localText(
              t,
              'runtimes.operational.onlineDescription',
              'This runtime is ready to accept work.',
            )}
          </p>
        ) : null}

        {runtimeOperationalState === 'paused' ? (
          <p>
            {localText(
              t,
              'runtimes.operational.pausedDescription',
              'New work is paused until an administrator resumes this runtime.',
            )}
          </p>
        ) : null}

        {runtimeOperationalState === 'degraded' ? (
          <div className="mesh-runtimes-detail__diagnostics">
            {diagnostics.map((diagnostic) => (
              <article
                key={diagnostic.reason_code}
                className="mesh-runtimes-detail__diagnostic"
                data-testid={`runtime-diagnostic-${diagnostic.reason_code}`}
              >
                <h3>{diagnostic.reason_code.replaceAll('_', ' ')}</h3>
                <dl>
                  <dt>
                    {localText(
                      t,
                      'runtimes.operational.missingCapabilities',
                      'Missing capabilities',
                    )}
                  </dt>
                  <dd>{diagnostic.missing_capabilities.join(', ') || '—'}</dd>
                  <dt>
                    {localText(t, 'runtimes.operational.affectedTaskTypes', 'Affected task types')}
                  </dt>
                  <dd>{diagnostic.affected_task_types.join(', ') || '—'}</dd>
                </dl>
                <div className="mesh-runtimes-detail__repair">
                  <code>{diagnostic.repair_command}</code>
                  <Button
                    variant="secondary"
                    size="sm"
                    data-testid={`runtime-diagnostic-copy-${diagnostic.reason_code}`}
                    onClick={() => void copyRepairCommand(diagnostic.repair_command)}
                  >
                    {localText(t, 'runtimes.operational.copyCommand', 'Copy command')}
                  </Button>
                </div>
              </article>
            ))}
          </div>
        ) : null}

        {runtimeOperationalState === 'isolated' ? (
          <div className="mesh-runtimes-detail__isolation-actions">
            <p>
              {localText(
                t,
                'runtimes.operational.isolatedDescription',
                'Execution is isolated. Export the redacted diagnostics before re-registering.',
              )}
            </p>
            <code data-testid="runtime-reregister-command">{REREGISTER_COMMAND}</code>
            <div>
              <Button
                variant="secondary"
                size="sm"
                data-testid="runtime-export-diagnostics"
                onClick={exportDiagnostics}
              >
                {localText(t, 'runtimes.operational.exportDiagnostics', 'Export diagnostics')}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                data-testid="runtime-reregister-copy"
                onClick={() => void copyRepairCommand(REREGISTER_COMMAND)}
              >
                {localText(t, 'runtimes.operational.copyReregister', 'Copy re-register command')}
              </Button>
            </div>
          </div>
        ) : null}
      </section>

      <dl className="mesh-runtimes-detail__meta">
        <dt>{t('runtimes.field.hostname')}</dt>
        <dd data-testid="runtime-detail-host">{runtime.hostname ?? '—'}</dd>
        <dt>{t('runtimes.field.os')}</dt>
        <dd data-testid="runtime-detail-os">{runtime.os ?? '—'}</dd>
        <dt>{t('runtimes.field.cpu')}</dt>
        <dd data-testid="runtime-detail-cpu">
          {runtime.cpu_cores === null ? '—' : t('runtimes.cpuCores', { count: runtime.cpu_cores })}
        </dd>
        <dt>{t('runtimes.field.memory')}</dt>
        <dd data-testid="runtime-detail-memory">{memory ?? '—'}</dd>
        <dt>{t('runtimes.field.concurrency')}</dt>
        <dd data-testid="runtime-detail-concurrency">
          {runtime.current_load}/{runtime.max_concurrent}
        </dd>
        <dt>{t('runtimes.field.version')}</dt>
        <dd data-testid="runtime-detail-version">
          {runtime.version === null ? '—' : `v${runtime.version}`}
        </dd>
      </dl>

      <div className="mesh-runtimes-detail__chips" data-testid="runtime-detail-labels">
        <span className="mesh-runtimes-detail__chips-title">{t('runtimes.field.labels')}</span>
        {labelEntries.length === 0 ? <span>—</span> : null}
        {labelEntries.map(([key, value]) => (
          <span key={key} className="mesh-runtimes-detail__chip">
            {key}={value}
          </span>
        ))}
      </div>

      <div className="mesh-runtimes-detail__chips" data-testid="runtime-detail-capabilities">
        <span className="mesh-runtimes-detail__chips-title">
          {t('runtimes.field.capabilities')}
        </span>
        {runtime.capabilities.length === 0 ? <span>—</span> : null}
        {runtime.capabilities.map((capability) => (
          <span key={capability} className="mesh-runtimes-detail__chip">
            {capability}
          </span>
        ))}
      </div>

      <section className="mesh-runtimes-detail__section">
        <h2>{t('runtimes.detail.inflight', { count: inflight.length })}</h2>
        {inflight.length === 0 ? (
          <EmptyState title={t('state.emptyTitle')} description={t('runtimes.detail.noInflight')} />
        ) : (
          <ul className="mesh-runtimes-detail__executions" data-testid="runtime-inflight-list">
            {inflight.map((execution) => {
              const startAt = execution.attempts.find((a) => a.started_at !== null)?.started_at;
              const elapsedSeconds =
                startAt !== undefined && startAt !== null
                  ? Math.max(0, Math.floor((nowMs - Date.parse(startAt)) / 1000))
                  : 0;
              return (
                <li key={execution.id} data-testid={`runtime-inflight-${execution.id}`}>
                  <span className="mesh-runtimes-detail__execution-name">
                    {executionDisplayLabel(t, execution)}
                  </span>
                  <span data-testid={`runtime-inflight-status-${execution.id}`}>
                    {t(`runtimes.execution.status.${execution.status}`)}
                  </span>
                  <span>{formatDurationSeconds(elapsedSeconds)}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    data-testid={`runtime-view-${execution.id}`}
                    onClick={() =>
                      navigate(
                        workspace === null
                          ? `/executions/${execution.id}`
                          : workspaceRoute(workspace.workspace_slug, `/executions/${execution.id}`),
                      )
                    }
                  >
                    {t('runtimes.action.view')}
                  </Button>
                  {canTrigger ? (
                    <Button
                      variant="danger"
                      size="sm"
                      data-testid={`runtime-cancel-${execution.id}`}
                      onClick={() =>
                        void act(
                          () =>
                            cancelExecution(client, workspace?.workspace_id ?? '', execution.id),
                          t('runtimes.toast.cancelled'),
                        )
                      }
                    >
                      {t('runtimes.action.cancel')}
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="mesh-runtimes-detail__section">
        <h2>{t('runtimes.detail.history')}</h2>
        {history.length === 0 ? (
          <EmptyState title={t('state.emptyTitle')} description={t('runtimes.detail.noHistory')} />
        ) : (
          <ul className="mesh-runtimes-detail__executions" data-testid="runtime-history-list">
            {history.map((execution) => (
              <li key={execution.id} data-testid={`runtime-history-${execution.id}`}>
                <span className="mesh-runtimes-detail__execution-name">
                  {executionDisplayLabel(t, execution)}
                </span>
                <span>{t(`runtimes.execution.status.${execution.status}`)}</span>
                <span>{execution.finished_at ?? execution.queued_at}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  data-testid={`runtime-history-view-${execution.id}`}
                  onClick={() =>
                    navigate(
                      workspace === null
                        ? `/executions/${execution.id}`
                        : workspaceRoute(workspace.workspace_slug, `/executions/${execution.id}`),
                    )
                  }
                >
                  {t('runtimes.action.view')}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Dialog
        open={rotatedToken !== null}
        onClose={() => setRotatedToken(null)}
        title={t('runtimes.rotate.title')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-runtimes-rotate" data-testid="runtime-rotate-dialog">
          <p className="mesh-runtimes-rotate__warning">{t('runtimes.rotate.warning')}</p>
          <code className="mesh-runtimes-rotate__token" data-testid="runtime-rotate-token">
            {rotatedToken ?? ''}
          </code>
          <div className="mesh-runtimes-rotate__actions">
            <Button
              variant="secondary"
              size="sm"
              data-testid="runtime-rotate-copy"
              onClick={() => void copyToken()}
            >
              {t('runtimes.wizard.copy')}
            </Button>
            <Button
              size="sm"
              data-testid="runtime-rotate-close"
              onClick={() => setRotatedToken(null)}
            >
              {t('common.close')}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
