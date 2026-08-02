/** Human-facing DingTalk commands plus an approval/notification view backed by Mesh truth. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, ErrorState, Input, Skeleton, StatusDot } from '../../design';
import type { StatusDotTone } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { getApproval } from '../approvals/api';
import type { Approval, ApprovalActionSummary, ApprovalStatus } from '../approvals/api';
import type { DingTalkVerbosity } from './types';
import './integrations.css';

const EXECUTION_STATUS_KEYS: ReadonlySet<string> = new Set([
  'queued',
  'claimed',
  'running',
  'awaiting_approval',
  'cancelling',
  'completed',
  'failed',
  'timeout',
  'cancelled',
]);
const APPROVAL_POLL_INTERVAL_MS = 4000;

const APPROVAL_TONES: Readonly<Record<ApprovalStatus, StatusDotTone>> = {
  pending: 'warn',
  approved: 'success',
  rejected: 'danger',
  expired: 'warn',
  cancelled: 'neutral',
};

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

function executionTone(status: string | null): StatusDotTone {
  if (status === 'completed') return 'success';
  if (status === 'failed' || status === 'timeout' || status === 'cancelled') return 'danger';
  if (status === 'awaiting_approval') return 'warn';
  return status === null ? 'neutral' : 'info';
}

function displayValue(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() === '' ? null : value;
  if (typeof value !== 'object' || value === null) return null;
  return JSON.stringify(value) ?? null;
}

function permissionValue(summary: ApprovalActionSummary): string | null {
  const parts = [summary.capability, summary.permission].filter(
    (value): value is string => typeof value === 'string' && value.trim() !== '',
  );
  return parts.length === 0 ? null : parts.join(' · ');
}

function resumeValue(summary: ApprovalActionSummary): string | null {
  const context = summary.resume_context;
  if (typeof context !== 'object' || context === null) return null;
  const parts: string[] = [];
  if (typeof context.completed_steps === 'number') parts.push(String(context.completed_steps));
  const pending = displayValue(context.pending_tool_call);
  if (pending !== null) parts.push(pending);
  return parts.length === 0 ? null : parts.join(' · ');
}

function approvalLink(workspaceSlug: string, approvalId: string): string {
  return `/w/${encodeURIComponent(workspaceSlug)}/approvals?approval_id=${encodeURIComponent(approvalId)}`;
}

// mesh-emoji-ok: 钉钉机器人对外停止反馈的原始文案，不作为 Mesh UI 图标使用
const DINGTALK_STOPPING_FEEDBACK = '⏳';
// mesh-emoji-ok: 钉钉机器人对外停止反馈的原始文案，不作为 Mesh UI 图标使用
const DINGTALK_STOPPED_FEEDBACK = '🛑';

export interface DingTalkInteractionGuideProps {
  readonly workspaceId: string;
  readonly workspaceSlug: string;
  readonly verbosity: DingTalkVerbosity;
  readonly ackTemplate: string;
}

export function DingTalkInteractionGuide(props: DingTalkInteractionGuideProps): React.JSX.Element {
  const { workspaceId, workspaceSlug, verbosity, ackTemplate } = props;
  const t = useT();
  const requestSequence = useRef(0);
  const pollOwnerSequence = useRef<number | null>(null);
  const currentApprovalIdRef = useRef('');
  const [command, setCommand] = useState('');
  const [approvalId, setApprovalId] = useState('');
  const [approval, setApproval] = useState<Approval | null>(null);
  const [approvalLoading, setApprovalLoading] = useState(false);
  const [approvalErrorKey, setApprovalErrorKey] = useState<string | null>(null);

  useEffect(
    () => () => {
      requestSequence.current += 1;
    },
    [],
  );

  const loadApproval = useCallback(
    async (silent = false): Promise<void> => {
      const requestedId = approvalId.trim();
      if (requestedId === '') return;
      // A timer callback from the previous render can already be queued when
      // the input changes, before React runs the old effect cleanup. Reject it
      // both before starting I/O and again before committing its response.
      if (requestedId !== currentApprovalIdRef.current.trim()) return;
      if (silent && pollOwnerSequence.current !== null) return;
      const sequence = requestSequence.current + 1;
      requestSequence.current = sequence;
      // Every request synchronously owns the polling slot. In particular, a
      // manual refresh claims it before React can clean up an already-queued
      // interval callback; a manual request may supersede an in-flight poll.
      pollOwnerSequence.current = sequence;
      if (!silent) setApprovalLoading(true);
      setApprovalErrorKey(null);
      try {
        const loaded = await getApproval(newClient(), workspaceId, requestedId);
        if (
          requestSequence.current !== sequence ||
          requestedId !== currentApprovalIdRef.current.trim()
        )
          return;
        setApproval(loaded);
      } catch (error) {
        if (
          requestSequence.current !== sequence ||
          requestedId !== currentApprovalIdRef.current.trim()
        )
          return;
        setApprovalErrorKey(
          error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown',
        );
      } finally {
        if (pollOwnerSequence.current === sequence) {
          pollOwnerSequence.current = null;
        }
        if (!silent && requestSequence.current === sequence) setApprovalLoading(false);
      }
    },
    [approvalId, workspaceId],
  );

  useEffect(() => {
    if (approval?.status !== 'pending' || approvalLoading) return;
    const interval = window.setInterval(() => {
      void loadApproval(true);
    }, APPROVAL_POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [approval?.status, approvalLoading, loadApproval]);

  const executionStatus = approval?.execution_status ?? null;
  const executionStatusLabel =
    approval === null
      ? t('integrations.dingtalk.notification.noApproval')
      : executionStatus === null
        ? t('integrations.dingtalk.notification.noExecution')
        : EXECUTION_STATUS_KEYS.has(executionStatus)
          ? t(`runtimes.execution.status.${executionStatus}`)
          : executionStatus;
  const action = approval === null ? null : displayValue(approval.action_summary.action);
  const permission = approval === null ? null : permissionValue(approval.action_summary);
  const impact = approval === null ? null : displayValue(approval.action_summary.impact_scope);
  const cost = approval === null ? null : displayValue(approval.action_summary.estimated_cost);
  const resume = approval === null ? null : resumeValue(approval.action_summary);

  return (
    <div className="mesh-integrations__interaction-guide">
      <section className="mesh-integrations__section" data-testid="dingtalk-command-help">
        <div className="mesh-integrations__header">
          <h3>{t('integrations.dingtalk.commands.title')}</h3>
          <span className="mesh-integrations__tag" data-testid="dingtalk-verbosity-preview">
            {t(`integrations.dingtalk.verbosity.${verbosity}`)}
          </span>
        </div>
        <dl className="mesh-integrations__kv">
          <dt>
            <code>/btw &lt;context&gt;</code>
          </dt>
          <dd>{t('integrations.dingtalk.commands.btwHelp')}</dd>
          <dt>
            <code>/stop [reason]</code>
          </dt>
          <dd>{t('integrations.dingtalk.commands.stopHelp')}</dd>
          <dt>
            <code>/help</code>
          </dt>
          <dd>{t('integrations.dingtalk.commands.helpHelp')}</dd>
        </dl>
        <div className="mesh-integrations__toolbar">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setCommand('/btw ')}
            data-testid="dingtalk-command-btw"
          >
            /btw
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setCommand('/stop ')}
            data-testid="dingtalk-command-stop"
          >
            /stop
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setCommand('/help')}
            data-testid="dingtalk-command-help-button"
          >
            /help
          </Button>
        </div>
        <Input
          label={t('integrations.dingtalk.commands.inputLabel')}
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          hint={t('integrations.dingtalk.commands.inputHint')}
          data-testid="dingtalk-command-input"
        />
        <code className="mesh-integrations__command-preview" data-testid="dingtalk-command-preview">
          {command || t('integrations.dingtalk.commands.previewEmpty')}
        </code>

        <div className="mesh-integrations__preview-grid">
          <div className="mesh-integrations__preview-box" data-testid="dingtalk-ack-preview">
            <strong>{t('integrations.dingtalk.commands.ack')}</strong>
            <span>{ackTemplate || t('integrations.dingtalk.commands.ackDisabled')}</span>
          </div>
          <div className="mesh-integrations__preview-box" data-testid="dingtalk-position-preview">
            <strong>{t('integrations.dingtalk.commands.queue')}</strong>
            <span>{t('integrations.queue.position', { position: 2 })}</span>
          </div>
          <div className="mesh-integrations__preview-box" data-testid="dingtalk-stop-feedback">
            <strong>{t('integrations.dingtalk.commands.stopStages')}</strong>
            <span>
              {DINGTALK_STOPPING_FEEDBACK} {t('integrations.dingtalk.commands.stopping')}
            </span>
            <span>
              {DINGTALK_STOPPED_FEEDBACK} {t('integrations.dingtalk.commands.stopped')}
            </span>
          </div>
        </div>
      </section>

      <section className="mesh-integrations__section">
        <div className="mesh-integrations__header">
          <div>
            <h3>{t('integrations.dingtalk.card.previewTitle')}</h3>
            <p className="mesh-integrations__muted">
              {t('integrations.dingtalk.card.previewHint')}
            </p>
          </div>
        </div>

        <div className="mesh-integrations__approval-loader">
          <Input
            label={t('integrations.dingtalk.card.approvalId')}
            value={approvalId}
            onChange={(event) => {
              currentApprovalIdRef.current = event.target.value;
              // Invalidate an in-flight poll for the previous id before its
              // response can repaint the newly cleared preview.
              requestSequence.current += 1;
              pollOwnerSequence.current = null;
              setApprovalLoading(false);
              setApprovalId(event.target.value);
              setApproval(null);
              setApprovalErrorKey(null);
            }}
            data-testid="dingtalk-approval-id"
          />
          <div className="mesh-integrations__toolbar">
            <Button
              variant="secondary"
              size="sm"
              disabled={approvalId.trim() === ''}
              isLoading={approvalLoading}
              onClick={() => void loadApproval()}
              data-testid="dingtalk-approval-load"
            >
              {t('integrations.dingtalk.card.load')}
            </Button>
            {approval !== null && (
              <Button
                variant="secondary"
                size="sm"
                isLoading={approvalLoading}
                onClick={() => void loadApproval()}
                data-testid="dingtalk-approval-refresh"
              >
                {t('integrations.dingtalk.card.refresh')}
              </Button>
            )}
          </div>
        </div>

        {approvalLoading && approval === null && (
          <Skeleton loadingLabel={t('integrations.dingtalk.card.loading')} />
        )}
        {approvalErrorKey !== null && (
          <div data-testid="dingtalk-approval-error">
            <ErrorState
              title={t(approvalErrorKey)}
              description={t('integrations.dingtalk.card.loadErrorHint')}
              retryLabel={t('common.retry')}
              onRetry={() => void loadApproval()}
            />
          </div>
        )}

        <article
          className="mesh-integrations__approval-card"
          data-testid="dingtalk-notification-preview"
        >
          <div className="mesh-integrations__header">
            <strong>{t('integrations.dingtalk.notification.title')}</strong>
            <StatusDot tone={executionTone(executionStatus)} label={executionStatusLabel} />
          </div>
          <p data-testid="dingtalk-notification-body">
            {approval === null
              ? t(`integrations.dingtalk.notification.${verbosity}`)
              : t('integrations.dingtalk.notification.executionBody', {
                  status: executionStatusLabel,
                })}
          </p>
          <p className="mesh-integrations__muted">
            <strong>{t(`integrations.dingtalk.verbosity.${verbosity}`)}</strong>
            {' · '}
            {t(`integrations.dingtalk.notification.${verbosity}`)}
          </p>
          <p className="mesh-integrations__muted">
            {t('integrations.dingtalk.notification.sourceTruth')}
          </p>
        </article>

        {approval === null ? (
          !approvalLoading && approvalErrorKey === null ? (
            <p className="mesh-integrations__muted" data-testid="dingtalk-approval-empty">
              {t('integrations.dingtalk.card.empty')}
            </p>
          ) : null
        ) : (
          <article className="mesh-integrations__approval-card" data-testid="dingtalk-card-preview">
            <div className="mesh-integrations__header">
              <strong>{t('integrations.dingtalk.card.title')}</strong>
              <StatusDot
                tone={APPROVAL_TONES[approval.status]}
                label={t(`integrations.dingtalk.card.state.${approval.status}`)}
              />
            </div>

            <dl className="mesh-integrations__kv">
              <dt>{t('integrations.dingtalk.card.approvalId')}</dt>
              <dd>{approval.id}</dd>
              <dt>{t('integrations.dingtalk.card.action')}</dt>
              <dd>{action ?? t('integrations.dingtalk.card.notAvailable')}</dd>
              <dt>{t('integrations.dingtalk.card.permission')}</dt>
              <dd>{permission ?? t('integrations.dingtalk.card.notAvailable')}</dd>
              <dt>{t('integrations.dingtalk.card.impact')}</dt>
              <dd>{impact ?? t('integrations.dingtalk.card.notAvailable')}</dd>
              <dt>{t('integrations.dingtalk.card.cost')}</dt>
              <dd>{cost ?? t('integrations.dingtalk.card.notAvailable')}</dd>
              <dt>{t('integrations.dingtalk.card.resume')}</dt>
              <dd>{resume ?? t('integrations.dingtalk.card.notAvailable')}</dd>
            </dl>

            <p
              className={`mesh-integrations__card-state mesh-integrations__card-state--${APPROVAL_TONES[approval.status]}`}
            >
              {t(`integrations.dingtalk.card.feedback.${approval.status}`)}
            </p>

            <div className="mesh-integrations__toolbar">
              <Link
                to={approvalLink(workspaceSlug, approval.id)}
                data-testid="dingtalk-card-fallback"
              >
                {t('integrations.dingtalk.card.backToMesh')}
              </Link>
            </div>
            {approval.status !== 'pending' && (
              <p className="mesh-integrations__muted">
                {t('integrations.dingtalk.card.terminalHint')}
              </p>
            )}
          </article>
        )}
      </section>
    </div>
  );
}
