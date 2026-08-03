/**
 * 单个任务执行详情页(runtime.md §4.4):状态头 + runtime + 已运行 / 超时进度;
 * agent / issue / 触发 / 分支元信息;三 Tab —— 实时日志 / 产物 / Diff / 凭证(已脱敏),
 * 选中 Tab 同步 URL search params(与 AgentDetailPage 同模式)。
 *
 * 日志三段合一(§4.9):① REST listExecutionLogs(?offset=N)补历史 → ② WS 主通道
 * execution:{id}:logs 实时尾部(断线由频道 resume_from 自动续传)→ ③ end 帧收尾;
 * 客户端按 offset 去重衔接,不丢不重。实时态未连通时降级 SSE(EventSource 订阅
 * 同一 offset 协议的 JSON 行帧,§3.3)。凭证 Tab 仅元信息,值恒为 `***`(§4.10 红线)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import {
  Banner,
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Skeleton,
  StatusDot,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { useWorkspaceMembership } from '../members/useWorkspaceMembership';
import {
  cancelExecution,
  executionChannel,
  executionLogsChannel,
  executionLogsStreamUrl,
  getExecution,
  latestAttempt,
  listExecutionLogs,
} from './api';
import { executionDisplayLabel } from './executionLabel';
import { formatDurationSeconds } from './format';
import type { ExecutionDetail, ExecutionStatus, LogFrame, LogLine } from './types';
import { SUCCESS_EXECUTION_STATUSES, TERMINAL_EXECUTION_STATUSES } from './types';
import './runtimes.css';

type TabKey = 'logs' | 'artifacts' | 'credentials';

const TAB_KEYS: readonly TabKey[] = ['logs', 'artifacts', 'credentials'];

function tabFromParam(value: string | null): TabKey {
  return TAB_KEYS.includes(value as TabKey) ? (value as TabKey) : 'logs';
}

/** 终态帧触发执行详情重拉的事件(§3.6 execution:{id})。 */
const EXECUTION_REFRESH_EVENTS: ReadonlySet<string> = new Set([
  'execution.completed',
  'execution.failed',
  'execution.timeout',
  'execution.cancelled',
  'execution.requeued',
  'execution.awaiting_approval',
]);

const STATUS_TONE: Record<ExecutionStatus, 'success' | 'warn' | 'danger' | 'info' | 'neutral'> = {
  queued: 'info',
  claimed: 'info',
  running: 'success',
  cancelling: 'warn',
  awaiting_approval: 'warn',
  completed: 'success',
  failed: 'danger',
  timeout: 'danger',
  cancelled: 'neutral',
};

export function ExecutionDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const { executionId } = useParams<{ executionId: string }>();
  const realtime = useRealtimeContext();
  const realtimeState = realtime?.state ?? 'absent';
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const membershipState = useWorkspaceMembership(client);
  const workspace = membershipState.kind === 'ready' ? membershipState.membership : null;
  const canCancelExecution = workspace !== null && workspace.role !== 'guest';

  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = tabFromParam(searchParams.get('tab'));

  const [execution, setExecution] = useState<ExecutionDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());

  // 日志态:追加行 + 去重集合(offset 单调,§2.3)+ 续传水位 + 跟随尾部开关。
  const [logs, setLogs] = useState<readonly LogLine[]>([]);
  const [followTail, setFollowTail] = useState(true);
  const [logsUnavailable, setLogsUnavailable] = useState(false);
  const [endStatus, setEndStatus] = useState<string | null>(null);
  const seenOffsetsRef = useRef<Set<number>>(new Set());
  const maxOffsetRef = useRef(0);
  const logPanelRef = useRef<HTMLDivElement | null>(null);

  const loadExecution = useCallback(() => {
    if (workspace === null || executionId === undefined) return;
    setIsLoading(true);
    setError(null);
    getExecution(client, workspace.workspace_id, executionId)
      .then(setExecution)
      .catch((err) => setError(err instanceof Error ? err.message : t('state.errorDescription')))
      .finally(() => setIsLoading(false));
  }, [client, workspace, executionId, t]);

  useEffect(() => {
    loadExecution();
  }, [loadExecution, reloadKey]);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  /** 去重追加(补发与实时流边界可能重叠,按 offset 去重,§3.3)。 */
  const ingestLines = useCallback((incoming: readonly LogLine[]) => {
    const seen = seenOffsetsRef.current;
    const additions = incoming.filter((line) => {
      if (seen.has(line.offset)) return false;
      seen.add(line.offset);
      return true;
    });
    if (additions.length === 0) return;
    for (const line of additions) {
      if (line.offset > maxOffsetRef.current) maxOffsetRef.current = line.offset;
    }
    setLogs((prev) => [...prev, ...additions]);
  }, []);

  /** §3.3 帧分派:log 追加 / status 重拉 / end 收尾。 */
  const ingestFrame = useCallback(
    (frame: LogFrame) => {
      if (frame.type === 'log') {
        if (typeof frame.line !== 'string' || typeof frame.offset !== 'number') return;
        ingestLines([{ stream: frame.stream ?? 'stdout', offset: frame.offset, line: frame.line }]);
        return;
      }
      if (frame.type === 'status') {
        setReloadKey((key) => key + 1);
        return;
      }
      if (frame.type === 'end') {
        if (frame.status !== undefined) setEndStatus(frame.status);
        if (typeof frame.final_offset === 'number') {
          maxOffsetRef.current = Math.max(maxOffsetRef.current, frame.final_offset);
        }
        setReloadKey((key) => key + 1);
      }
    },
    [ingestLines],
  );

  // ① REST 补历史(挂载即拉 [0, sealed),§4.9 三段合一第一段)。
  useEffect(() => {
    if (workspace === null || executionId === undefined) return;
    let cancelled = false;
    listExecutionLogs(client, workspace.workspace_id, executionId, {
      offset: maxOffsetRef.current,
    })
      .then((page) => {
        if (cancelled) return;
        ingestLines(page.lines);
        maxOffsetRef.current = Math.max(maxOffsetRef.current, page.next_offset);
      })
      .catch(() => {
        if (!cancelled) setLogsUnavailable(true);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspace, executionId, ingestLines, reloadKey]);

  // ② WS 主通道实时尾部;断线重连经频道 resume_from 自动续传(RealtimeClient 游标)。
  useEffect(() => {
    if (realtime === null || executionId === undefined) return;
    if (realtimeState !== 'connected') return;
    const channel = executionLogsChannel(executionId);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      ingestFrame(frame.payload as LogFrame);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, realtimeState, executionId, ingestFrame]);

  // 终态 / 重排事件(§3.6 execution:{id})→ 重拉执行详情。
  useEffect(() => {
    if (realtime === null || executionId === undefined) return;
    const channel = executionChannel(executionId);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      if (EXECUTION_REFRESH_EVENTS.has(frame.event)) setReloadKey((key) => key + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, executionId]);

  // ②' SSE 降级(§3.3 / §4.9):实时态未连通且环境支持 EventSource 时订阅同一 offset 协议。
  useEffect(() => {
    if (realtimeState === 'connected') return;
    if (typeof EventSource === 'undefined') return;
    if (workspace === null || executionId === undefined) return;
    const source = new EventSource(
      executionLogsStreamUrl(workspace.workspace_id, executionId, maxOffsetRef.current),
    );
    source.onmessage = (event: MessageEvent) => {
      try {
        ingestFrame(JSON.parse(String(event.data)) as LogFrame);
      } catch {
        // 非法 JSON 行:跳过该行,连接保持(§6.8 降级通道容错)。
      }
    };
    return () => source.close();
  }, [realtimeState, workspace, executionId, ingestFrame]);

  // 跟随尾部:新行到达且开关开启时滚到底。
  useEffect(() => {
    if (!followTail) return;
    const panel = logPanelRef.current;
    if (panel !== null) panel.scrollTop = panel.scrollHeight;
  }, [logs, followTail]);

  const selectTab = (tab: TabKey): void => {
    const params = new URLSearchParams(searchParams);
    if (tab === 'logs') params.delete('tab');
    else params.set('tab', tab);
    setSearchParams(params, { replace: true });
  };

  const confirmCancel = async (): Promise<void> => {
    if (workspace === null || executionId === undefined) return;
    try {
      await cancelExecution(client, workspace.workspace_id, executionId);
      toast.addToast(t('runtimes.toast.cancelled'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setCancelOpen(false);
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
      setCancelOpen(false);
    }
  };

  const attempt = execution !== null ? latestAttempt(execution) : null;
  const startIso = attempt?.started_at ?? attempt?.claimed_at ?? null;
  const elapsedSeconds =
    startIso !== null ? Math.max(0, Math.floor((nowMs - Date.parse(startIso)) / 1000)) : 0;
  const isTerminal = execution !== null && TERMINAL_EXECUTION_STATUSES.has(execution.status);
  const isSuccess = execution !== null && SUCCESS_EXECUTION_STATUSES.has(execution.status);
  const canCancel =
    canCancelExecution && execution !== null && !TERMINAL_EXECUTION_STATUSES.has(execution.status);
  const timeoutPct =
    execution !== null && execution.timeout_seconds > 0
      ? Math.min(100, Math.round((elapsedSeconds / execution.timeout_seconds) * 100))
      : 0;

  const membershipError = membershipState.kind === 'error' ? t('state.errorDescription') : null;
  const visibleError = membershipError ?? error;

  if (visibleError !== null) {
    return (
      <div className="mesh-executions">
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
      <div className="mesh-executions">
        <EmptyState title={t('state.emptyTitle')} description={t('runtimes.noWorkspace')} />
      </div>
    );
  }

  if (membershipState.kind === 'loading' || isLoading || execution === null) {
    return (
      <div className="mesh-executions">
        <Skeleton loadingLabel={t('common.loading')} />
      </div>
    );
  }

  const credentials = execution.credentials ?? [];

  return (
    <div className="mesh-executions" data-testid="execution-detail-page">
      <div className="mesh-executions__header">
        <Button variant="ghost" data-testid="execution-back" onClick={() => navigate(-1)}>
          {t('runtimes.execution.back')}
        </Button>
        <h1 className="mesh-executions__title" data-testid="execution-title">
          {executionDisplayLabel(t, execution)}
        </h1>
        <span data-testid="execution-status">
          <StatusDot
            tone={STATUS_TONE[execution.status]}
            label={t(`runtimes.execution.status.${execution.status}`)}
          />
        </span>
        {canCancel ? (
          <Button
            variant="danger"
            size="sm"
            data-testid="execution-cancel-button"
            onClick={() => setCancelOpen(true)}
          >
            {t('runtimes.action.cancel')}
          </Button>
        ) : null}
      </div>

      {isTerminal ? (
        <Banner tone={isSuccess ? 'success' : 'danger'}>
          <span data-testid="execution-terminal-banner">
            {isSuccess
              ? t('runtimes.execution.terminalSuccess')
              : t('runtimes.execution.terminalFailure', {
                  reason: execution.failure_reason ?? t('runtimes.execution.unknownReason'),
                })}
          </span>
        </Banner>
      ) : null}

      <dl className="mesh-executions__meta">
        <dt>{t('runtimes.execution.runtime')}</dt>
        <dd data-testid="execution-runtime-name">{attempt?.runtime_name ?? '—'}</dd>
        <dt>{t('runtimes.execution.agent')}</dt>
        <dd data-testid="execution-agent">{execution.agent_id ?? '—'}</dd>
        <dt>{t('runtimes.execution.issue')}</dt>
        <dd data-testid="execution-issue">{execution.issue_id ?? '—'}</dd>
        <dt>{t('runtimes.execution.trigger')}</dt>
        <dd data-testid="execution-trigger">
          {t(`runtimes.execution.triggerKind.${execution.trigger}`)}
        </dd>
        <dt>{t('runtimes.execution.branch')}</dt>
        <dd data-testid="execution-branch">{attempt?.working_branch ?? '—'}</dd>
        <dt>{t('runtimes.execution.elapsed')}</dt>
        <dd data-testid="execution-elapsed">
          {formatDurationSeconds(elapsedSeconds)} /{' '}
          {formatDurationSeconds(execution.timeout_seconds)}
        </dd>
      </dl>

      <div
        className="mesh-executions__progress"
        role="meter"
        aria-valuenow={timeoutPct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={t('runtimes.execution.timeoutProgress')}
        data-testid="execution-progress"
      >
        <div className="mesh-executions__progress-fill" style={{ width: `${timeoutPct}%` }} />
      </div>

      <div
        className="mesh-executions__tabs"
        role="tablist"
        aria-label={t('runtimes.execution.tabsLabel')}
      >
        {TAB_KEYS.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            className="mesh-executions__tab"
            data-testid={`execution-tab-${tab}`}
            onClick={() => selectTab(tab)}
          >
            {t(`runtimes.execution.tab.${tab}`)}
          </button>
        ))}
      </div>

      {activeTab === 'logs' ? (
        <section className="mesh-executions__panel" data-testid="execution-panel-logs">
          <div className="mesh-executions__log-toolbar">
            <label className="mesh-executions__follow">
              <input
                type="checkbox"
                checked={followTail}
                data-testid="execution-follow-toggle"
                onChange={(event) => setFollowTail(event.target.checked)}
              />
              {t('runtimes.execution.followTail')}
            </label>
            <span className="mesh-executions__offset" data-testid="execution-offset">
              {t('runtimes.execution.logOffset', { offset: maxOffsetRef.current })}
            </span>
          </div>
          {logsUnavailable ? (
            <p className="mesh-executions__logs-note" data-testid="execution-logs-unavailable">
              {t('runtimes.execution.logsUnavailable')}
            </p>
          ) : null}
          {endStatus !== null ? (
            <p className="mesh-executions__logs-note" data-testid="execution-log-end">
              {t('runtimes.execution.logEnded', { status: endStatus })}
            </p>
          ) : null}
          <div className="mesh-executions__log-panel" ref={logPanelRef}>
            {logs.length === 0 ? (
              <p className="mesh-executions__log-empty">{t('runtimes.execution.noLogs')}</p>
            ) : (
              logs.map((line) => (
                <div
                  key={line.offset}
                  className={`mesh-executions__log-line mesh-executions__log-line--${line.stream}`}
                  data-testid={`execution-log-line-${line.offset}`}
                >
                  <span className="mesh-executions__log-stream">{line.stream}</span>
                  <span className="mesh-executions__log-text">{line.line}</span>
                </div>
              ))
            )}
          </div>
        </section>
      ) : null}

      {activeTab === 'artifacts' ? (
        <section className="mesh-executions__panel" data-testid="execution-panel-artifacts">
          {attempt === null || attempt.working_branch === null ? (
            <EmptyState
              title={t('state.emptyTitle')}
              description={t('runtimes.execution.noArtifacts')}
            />
          ) : (
            <>
              <p data-testid="execution-artifact-branch">
                {t('runtimes.execution.artifactBranch', { branch: attempt.working_branch })}
              </p>
              {attempt.result !== null && attempt.result !== undefined ? (
                <pre className="mesh-executions__pre" data-testid="execution-artifact-result">
                  {JSON.stringify(attempt.result, null, 2)}
                </pre>
              ) : null}
            </>
          )}
        </section>
      ) : null}

      {activeTab === 'credentials' ? (
        <section className="mesh-executions__panel" data-testid="execution-panel-credentials">
          {credentials.length === 0 ? (
            <EmptyState
              title={t('state.emptyTitle')}
              description={t('runtimes.execution.noCredentials')}
            />
          ) : (
            <table className="mesh-executions__credentials">
              <caption className="sr-only">{t('runtimes.execution.tab.credentials')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('runtimes.credential.name')}</th>
                  <th scope="col">{t('runtimes.credential.kind')}</th>
                  <th scope="col">{t('runtimes.credential.value')}</th>
                </tr>
              </thead>
              <tbody>
                {credentials.map((credential) => (
                  <tr key={credential.id} data-testid={`execution-credential-${credential.id}`}>
                    <td>{credential.name}</td>
                    <td>{t(`runtimes.credential.kindValue.${credential.kind}`)}</td>
                    {/* §4.10 红线:值恒为 ***,服务端永不回显明文。 */}
                    <td data-testid={`execution-credential-value-${credential.id}`}>***</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ) : null}

      <Dialog
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        title={t('runtimes.execution.cancelTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-executions__cancel-dialog" data-testid="execution-cancel-dialog">
          <p>{t('runtimes.execution.cancelDescription')}</p>
          <div className="mesh-executions__cancel-actions">
            <Button
              variant="secondary"
              data-testid="execution-cancel-dismiss"
              onClick={() => setCancelOpen(false)}
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              data-testid="execution-cancel-confirm"
              onClick={() => void confirmCancel()}
            >
              {t('runtimes.execution.cancelConfirm')}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
