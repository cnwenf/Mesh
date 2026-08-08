/**
 * 单个任务执行详情页(runtime.md §4.4):状态头 + runtime + 已运行 / 超时进度;
 * agent / issue / 触发 / 分支元信息;四 Tab —— 实时日志 / 尝试审计 / 产物 / 凭证(已脱敏),
 * 选中 Tab 同步 URL search params(与 AgentDetailPage 同模式)。
 *
 * 日志三段合一(§4.9):① REST listExecutionLogs(?offset=N)补历史 → ② WS 主通道
 * execution:{id}:logs 实时尾部(断线由频道 resume_from 自动续传)→ ③ end 帧收尾;
 * 客户端按 offset 去重衔接,不丢不重。实时态未连通时降级 SSE(EventSource 订阅
 * 同一 offset 协议的 JSON 行帧,§3.3)。凭证 Tab 仅元信息,值恒为 `***`(§4.10 红线)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams, Link } from 'react-router';
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
import type { TranslateFn } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { useWorkspaceMembership, workspaceRoute } from '../members/useWorkspaceMembership';
import {
  cancelExecution,
  executionChannel,
  executionLogsChannel,
  executionLogsStreamUrl,
  getExecution,
  latestAttempt,
  listExecutionLogs,
  workspaceExecutionsChannel,
} from './api';
import { executionDisplayLabel } from './executionLabel';
import { formatDurationSeconds } from './format';
import type { AttemptStatus, ExecutionDetail, ExecutionStatus, LogFrame, LogLine } from './types';
import { SUCCESS_EXECUTION_STATUSES, TERMINAL_EXECUTION_STATUSES } from './types';
import './runtimes.css';

type TabKey = 'logs' | 'audit' | 'artifacts' | 'credentials';

const TAB_KEYS: readonly TabKey[] = ['logs', 'audit', 'artifacts', 'credentials'];

/** reaper 审批过期取消时写入的 failure_reason(与后端 reaper 对齐)。 */
const APPROVAL_EXPIRED_REASON = 'approval_expired';

function localText(t: TranslateFn, key: string, fallback: string): string {
  const translated = t(key);
  return translated === key || translated.includes(`[${key}]`) ? fallback : translated;
}

function auditValue(value: string | number | boolean): string {
  return typeof value === 'boolean' ? (value ? 'true' : 'false') : String(value);
}

const AUDIT_LABEL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$/;
const AUDIT_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,511}$/;
const REPOSITORY_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const BRANCH_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const DECIMAL_PATTERN = /^\d+(?:\.\d+)?$/;
const APPROVAL_AUDIT_FIELD_KEYS: ReadonlySet<string> = new Set([
  'repository',
  'branch',
  'operation',
  'resource',
  'scope',
  'method',
  'target_type',
  'target_id',
]);

function safeApprovalFields(
  fields: Readonly<Record<string, string | number | boolean>>,
): readonly (readonly [string, string | number | boolean])[] {
  return Object.entries(fields).filter(([key, value]) => {
    if (!APPROVAL_AUDIT_FIELD_KEYS.has(key)) return false;
    if (typeof value === 'boolean') return true;
    if (typeof value === 'number') return Number.isFinite(value);
    if (key === 'repository') return REPOSITORY_PATTERN.test(value);
    if (key === 'branch') return BRANCH_PATTERN.test(value);
    return AUDIT_LABEL_PATTERN.test(value);
  });
}

function safeAuditRef(value: unknown): string | null {
  return typeof value === 'string' && AUDIT_REF_PATTERN.test(value) ? value : null;
}

function recordOf(value: unknown): Readonly<Record<string, unknown>> | null {
  return typeof value === 'object' && value !== null
    ? (value as Readonly<Record<string, unknown>>)
    : null;
}

/**
 * 二次收口服务端的安全 result DTO。只复制公开 schema 的字段；未知键、路径、
 * provider 私有会话及任意嵌套对象都不会因 JSON.stringify 被意外展开。
 */
function safeArtifactResult(
  result: Readonly<Record<string, unknown>> | null | undefined,
): Readonly<Record<string, unknown>> | null {
  if (result == null) return null;
  const safe: Record<string, unknown> = {};
  const schemaVersion = result.schema_version;
  if (typeof schemaVersion === 'number' && Number.isInteger(schemaVersion)) {
    safe.schema_version = schemaVersion;
  }

  const provider = recordOf(result.provider);
  if (provider !== null) {
    const safeProvider: Record<string, unknown> = {};
    for (const key of ['name', 'version', 'model'] as const) {
      const value = provider[key];
      if (typeof value === 'string' && AUDIT_LABEL_PATTERN.test(value)) safeProvider[key] = value;
    }
    if (typeof provider.session_recorded === 'boolean') {
      safeProvider.session_recorded = provider.session_recorded;
    }
    if (Object.keys(safeProvider).length > 0) safe.provider = safeProvider;
  }

  const usage = recordOf(result.usage);
  if (usage !== null) {
    const safeUsage: Record<string, unknown> = {};
    for (const key of [
      'input_tokens',
      'cache_creation_tokens',
      'cache_read_tokens',
      'output_tokens',
      'total_tokens',
      'turns',
    ] as const) {
      const value = usage[key];
      if (typeof value === 'number' && Number.isInteger(value) && value >= 0) {
        safeUsage[key] = value;
      }
    }
    if (typeof usage.cost_usd === 'string' && DECIMAL_PATTERN.test(usage.cost_usd)) {
      safeUsage.cost_usd = usage.cost_usd;
    }
    if (Object.keys(safeUsage).length > 0) safe.usage = safeUsage;
  }

  const outcome = recordOf(result.outcome);
  if (outcome !== null) {
    const safeOutcome: Record<string, unknown> = {};
    if (typeof outcome.exit_code === 'number' && Number.isInteger(outcome.exit_code)) {
      safeOutcome.exit_code = outcome.exit_code;
    }
    if (typeof outcome.termination === 'string' && AUDIT_LABEL_PATTERN.test(outcome.termination)) {
      safeOutcome.termination = outcome.termination;
    }
    if (typeof outcome.summary === 'string') safeOutcome.summary = outcome.summary.slice(0, 8192);
    if (Object.keys(safeOutcome).length > 0) safe.outcome = safeOutcome;
  }

  const artifacts = recordOf(result.artifacts);
  if (artifacts !== null) {
    const safeArtifacts: Record<string, unknown> = {};
    for (const key of ['checkout_id', 'diff_ref'] as const) {
      if (artifacts[key] === null) safeArtifacts[key] = null;
      else {
        const value = safeAuditRef(artifacts[key]);
        if (value !== null) safeArtifacts[key] = value;
      }
    }
    if (Object.keys(safeArtifacts).length > 0) safe.artifacts = safeArtifacts;
  }

  const redaction = recordOf(result.redaction);
  if (redaction !== null) {
    const safeRedaction: Record<string, unknown> = {};
    if (
      typeof redaction.rule_version === 'string' &&
      AUDIT_LABEL_PATTERN.test(redaction.rule_version)
    ) {
      safeRedaction.rule_version = redaction.rule_version;
    }
    if (
      typeof redaction.hit_count === 'number' &&
      Number.isInteger(redaction.hit_count) &&
      redaction.hit_count >= 0
    ) {
      safeRedaction.hit_count = redaction.hit_count;
    }
    if (Object.keys(safeRedaction).length > 0) safe.redaction = safeRedaction;
  }

  return Object.keys(safe).length === 0 ? null : safe;
}

function tabFromParam(value: string | null): TabKey {
  return TAB_KEYS.includes(value as TabKey) ? (value as TabKey) : 'logs';
}

/** execution:{id} 的终态 / 重排帧。 */
const EXECUTION_DETAIL_REFRESH_EVENTS: ReadonlySet<string> = new Set([
  'execution.completed',
  'execution.failed',
  'execution.timeout',
  'execution.cancelled',
  'execution.requeued',
]);

/** workspace / issue 频道里的生产非终态协议。 */
const EXECUTION_NON_TERMINAL_REFRESH_EVENTS: ReadonlySet<string> = new Set([
  'execution.queued',
  'execution.claimed',
  'execution.started',
  'execution.progress',
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

const ATTEMPT_STATUS_TONE: Record<
  AttemptStatus,
  'success' | 'warn' | 'danger' | 'info' | 'neutral'
> = {
  claimed: 'info',
  running: 'success',
  cancelling: 'warn',
  completed: 'success',
  failed: 'danger',
  timeout: 'danger',
  cancelled: 'neutral',
  reclaimed: 'warn',
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

  // 路由复用同一组件时，上一执行的日志水位与终态绝不能带入下一执行。
  // 此 effect 声明在所有加载 effect 之前，保证新 REST 日志从 offset=0 开始。
  useEffect(() => {
    setExecution(null);
    setIsLoading(true);
    setError(null);
    setCancelOpen(false);
    setLogs([]);
    setLogsUnavailable(false);
    setEndStatus(null);
    seenOffsetsRef.current = new Set();
    maxOffsetRef.current = 0;
  }, [executionId]);

  useEffect(() => {
    if (workspace === null || executionId === undefined) return;
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    void getExecution(client, workspace.workspace_id, executionId)
      .then((detail) => {
        if (!cancelled) setExecution(detail);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : t('state.errorDescription'));
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspace, executionId, reloadKey, t]);

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
        setLogsUnavailable(false);
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
      if (EXECUTION_DETAIL_REFRESH_EVENTS.has(frame.event)) setReloadKey((key) => key + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, executionId]);

  // public 非终态在 workspace 频道；private issue run 只在 issue:{id} 频道。
  useEffect(() => {
    if (realtime === null || workspace === null || executionId === undefined) return;
    const workspaceChannel = workspaceExecutionsChannel(workspace.workspace_id);
    const issueChannel = execution?.issue_id == null ? null : `issue:${execution.issue_id}`;
    realtime.client.subscribe(workspaceChannel);
    if (issueChannel !== null) realtime.client.subscribe(issueChannel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== workspaceChannel && frame.channel !== issueChannel) return;
      if (!EXECUTION_NON_TERMINAL_REFRESH_EVENTS.has(frame.event)) return;
      const payload = frame.payload as { execution_id?: unknown };
      if (payload.execution_id === executionId) setReloadKey((key) => key + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(workspaceChannel);
      if (issueChannel !== null) realtime.client.unsubscribe(issueChannel);
    };
  }, [execution?.issue_id, executionId, realtime, workspace]);

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
  // L186 审批过期专项恢复入口(§6.12):reaper 将过期审批的待决执行取消并置
  // failure_reason=approval_expired —— 终态横幅给本地化「已过期」说明 + 重新发起。
  const isApprovalExpired =
    execution !== null && isTerminal && execution.failure_reason === APPROVAL_EXPIRED_REASON;
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
  const safeResult = safeArtifactResult(attempt?.result);
  const diffRef = safeAuditRef(execution.checkout?.diff_ref);

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
          {isApprovalExpired ? (
            <div
              className="mesh-executions__approval-expired"
              data-testid="execution-approval-expired"
            >
              <span>{t('runtimes.execution.approvalExpiredNote')}</span>
              {workspace !== null && execution.issue_id !== null ? (
                <Link
                  data-testid="execution-approval-expired-relaunch"
                  to={workspaceRoute(
                    workspace.workspace_slug,
                    `/issues/${encodeURIComponent(execution.issue_id)}`,
                  )}
                >
                  {t('approvals.relaunch')}
                </Link>
              ) : null}
            </div>
          ) : null}
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
            {tab === 'audit'
              ? localText(t, 'runtimes.execution.tab.audit', 'Attempt audit')
              : t(`runtimes.execution.tab.${tab}`)}
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

      {activeTab === 'audit' ? (
        <section className="mesh-executions__panel" data-testid="execution-panel-audit">
          <div className="mesh-executions__audit-summary">
            <h2>{localText(t, 'runtimes.execution.audit.budgetTitle', 'Frozen budget')}</h2>
            <dl>
              <dt>{localText(t, 'runtimes.execution.audit.costLimit', 'Cost limit')}</dt>
              <dd>{execution.frozen_budget?.max_cost_usd ?? '—'}</dd>
              <dt>{localText(t, 'runtimes.execution.audit.turnLimit', 'Turn limit')}</dt>
              <dd>{execution.frozen_budget?.max_turns ?? '—'}</dd>
              <dt>{localText(t, 'runtimes.execution.audit.unitLimit', 'Unit limit')}</dt>
              <dd>{execution.frozen_budget?.max_tokens ?? '—'}</dd>
              <dt>{localText(t, 'runtimes.execution.audit.wallLimit', 'Wall time limit')}</dt>
              <dd>
                {execution.frozen_budget?.max_wall_time_seconds === undefined
                  ? '—'
                  : formatDurationSeconds(execution.frozen_budget.max_wall_time_seconds)}
              </dd>
              <dt>{localText(t, 'runtimes.execution.audit.idleLimit', 'Idle time limit')}</dt>
              <dd>
                {execution.frozen_budget?.max_idle_time_seconds === undefined
                  ? '—'
                  : formatDurationSeconds(execution.frozen_budget.max_idle_time_seconds)}
              </dd>
              <dt>{localText(t, 'runtimes.execution.audit.retryCount', 'Retries')}</dt>
              <dd>{execution.retry_count ?? Math.max(execution.attempts.length - 1, 0)}</dd>
            </dl>
          </div>

          <div className="mesh-executions__audit-attempts">
            <h2>{localText(t, 'runtimes.execution.audit.attemptsTitle', 'Attempts')}</h2>
            {execution.attempts.length === 0 ? (
              <p className="mesh-executions__logs-note">
                {localText(t, 'runtimes.execution.audit.noAttempts', 'No attempts recorded.')}
              </p>
            ) : (
              execution.attempts.map((auditAttempt) => {
                const budget = auditAttempt.frozen_budget ?? execution.frozen_budget;
                const usage = auditAttempt.actual_usage;
                return (
                  <article
                    key={auditAttempt.id}
                    className="mesh-executions__audit-card"
                    data-testid={`execution-attempt-audit-${auditAttempt.id}`}
                  >
                    <header>
                      <h3>
                        {localText(t, 'runtimes.execution.audit.attempt', 'Attempt')} #
                        {auditAttempt.attempt_number}
                      </h3>
                      <StatusDot
                        tone={ATTEMPT_STATUS_TONE[auditAttempt.status]}
                        label={auditAttempt.status}
                      />
                    </header>
                    <dl className="mesh-executions__audit-grid">
                      <dt>{localText(t, 'runtimes.execution.audit.provider', 'Provider')}</dt>
                      <dd>{auditAttempt.provider ?? '—'}</dd>
                      <dt>{localText(t, 'runtimes.execution.audit.version', 'Version')}</dt>
                      <dd>{auditAttempt.provider_version ?? '—'}</dd>
                      <dt>{localText(t, 'runtimes.execution.audit.model', 'Model')}</dt>
                      <dd>{auditAttempt.model ?? '—'}</dd>
                      <dt>{localText(t, 'runtimes.execution.audit.costLimit', 'Cost limit')}</dt>
                      <dd>{budget?.max_cost_usd ?? '—'}</dd>
                      <dt>
                        {localText(t, 'runtimes.execution.audit.wallLimit', 'Wall time limit')}
                      </dt>
                      <dd>
                        {budget?.max_wall_time_seconds === undefined
                          ? '—'
                          : formatDurationSeconds(budget.max_wall_time_seconds)}
                      </dd>
                      <dt>
                        {localText(t, 'runtimes.execution.audit.idleLimit', 'Idle time limit')}
                      </dt>
                      <dd>
                        {budget?.max_idle_time_seconds === undefined
                          ? '—'
                          : formatDurationSeconds(budget.max_idle_time_seconds)}
                      </dd>
                      <dt>{localText(t, 'runtimes.execution.audit.actualCost', 'Actual cost')}</dt>
                      <dd>{usage?.cost_usd ?? '—'}</dd>
                      <dt>
                        {localText(t, 'runtimes.execution.audit.promptUnits', 'Prompt units')}
                      </dt>
                      <dd>{usage?.prompt_tokens ?? '—'}</dd>
                      <dt>
                        {localText(
                          t,
                          'runtimes.execution.audit.completionUnits',
                          'Completion units',
                        )}
                      </dt>
                      <dd>{usage?.completion_tokens ?? '—'}</dd>
                      <dt>{localText(t, 'runtimes.execution.audit.cacheUnits', 'Cache units')}</dt>
                      <dd>{usage?.cache_tokens ?? '—'}</dd>
                      <dt>{localText(t, 'runtimes.execution.audit.totalUnits', 'Total units')}</dt>
                      <dd>{usage?.total_tokens ?? '—'}</dd>
                      <dt>{localText(t, 'runtimes.execution.audit.turns', 'Turns')}</dt>
                      <dd>{usage?.turns ?? '—'}</dd>
                    </dl>
                    <p className="mesh-executions__audit-redaction">
                      {localText(t, 'runtimes.execution.audit.redacted', 'Redacted')}:{' '}
                      {auditAttempt.redaction_hits ?? 0}
                    </p>
                    <p className="mesh-executions__audit-redaction">
                      {localText(t, 'runtimes.execution.audit.payloadRedacted', 'Payload redacted')}
                      : {auditAttempt.redacted === true ? 'true' : 'false'}
                    </p>
                    <p className="mesh-executions__audit-redaction">
                      {localText(t, 'runtimes.execution.audit.securityAlert', 'Security alert')}:{' '}
                      {auditAttempt.security_alert === 'result_redacted'
                        ? auditAttempt.security_alert
                        : '—'}
                    </p>
                    <ol className="mesh-executions__audit-timeline">
                      {(auditAttempt.timeline ?? []).map((event, index) => (
                        <li key={`${event.event}-${event.at}-${index}`}>
                          <code>{event.event}</code>
                          <time dateTime={event.at}>{event.at}</time>
                          {event.status !== undefined ? <span>{event.status}</span> : null}
                          {event.reason_code !== undefined && event.reason_code !== null ? (
                            <span>{event.reason_code}</span>
                          ) : null}
                        </li>
                      ))}
                    </ol>
                  </article>
                );
              })
            )}
          </div>

          <div className="mesh-executions__approval-audits">
            <h2>{localText(t, 'runtimes.execution.audit.approvalsTitle', 'Approvals')}</h2>
            {(execution.approval_audits ?? []).length === 0 ? (
              <p className="mesh-executions__logs-note">
                {localText(t, 'runtimes.execution.audit.noApprovals', 'No approvals recorded.')}
              </p>
            ) : (
              (execution.approval_audits ?? []).map((approval) => (
                <article
                  key={approval.id}
                  className="mesh-executions__approval-card"
                  data-testid={`execution-approval-audit-${approval.id}`}
                >
                  <div className="mesh-executions__approval-step">
                    <h3>{localText(t, 'runtimes.execution.audit.request', 'Request')}</h3>
                    <strong>{approval.request.action}</strong>
                    <span>{approval.requested_by_member_id}</span>
                    <time dateTime={approval.requested_at}>{approval.requested_at}</time>
                    <dl>
                      {safeApprovalFields(approval.request.fields).map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>{auditValue(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                  <span className="mesh-executions__approval-arrow" aria-hidden="true">
                    →
                  </span>
                  <div className="mesh-executions__approval-step">
                    <h3>{localText(t, 'runtimes.execution.audit.decision', 'Decision')}</h3>
                    <strong>{approval.decision.status}</strong>
                    <span>{approval.decision.decided_by_member_id ?? '—'}</span>
                    <time dateTime={approval.decision.decided_at ?? undefined}>
                      {approval.decision.decided_at ?? '—'}
                    </time>
                  </div>
                  <span className="mesh-executions__approval-arrow" aria-hidden="true">
                    →
                  </span>
                  <div className="mesh-executions__approval-step">
                    <h3>{localText(t, 'runtimes.execution.audit.grant', 'Grant')}</h3>
                    <strong>{approval.grant?.action ?? '—'}</strong>
                    {approval.grant === null ? null : (
                      <dl>
                        {safeApprovalFields(approval.grant.fields).map(([key, value]) => (
                          <div key={key}>
                            <dt>{key}</dt>
                            <dd>{auditValue(value)}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </div>
                  <span className="mesh-executions__approval-arrow" aria-hidden="true">
                    →
                  </span>
                  <div className="mesh-executions__approval-step">
                    <h3>{localText(t, 'runtimes.execution.audit.result', 'Result')}</h3>
                    <strong>{approval.result?.status ?? '—'}</strong>
                    <span>{approval.result?.termination ?? '—'}</span>
                  </div>
                </article>
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
              {diffRef !== null ? (
                <p>
                  {localText(t, 'runtimes.execution.diffReference', 'Diff reference')}:{' '}
                  <code data-testid="execution-artifact-diff-ref">{diffRef}</code>
                </p>
              ) : null}
              {safeResult !== null ? (
                <pre className="mesh-executions__pre" data-testid="execution-artifact-result">
                  {JSON.stringify(safeResult, null, 2)}
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
