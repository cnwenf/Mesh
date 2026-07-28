/**
 * 自动化规则列表页(autopilot.md §4.1):名称 / 触发器 / 状态徽章 / 上次运行 /
 * 近30天成功率 / 下次运行(定时)/ 操作(暂停·启用·详情)。顶部常驻全局
 * kill switch(§3.1,二次确认 + 理由)。行级实时:workspace:{ws}:autopilots
 * 频道的 autopilot.updated / autopilot.rate_limited 帧触发整列重拉(§3.5)。
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import {
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
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
import { EmptyAutomation } from '../onboarding/illustrations';
import {
  getKillSwitchState,
  listAutopilots,
  pauseAutopilot,
  resumeAutopilot,
  setKillSwitch,
  workspaceAutopilotsChannel,
} from './api';
import {
  RULE_STATUS_TONE,
  RUN_STATUS_TONE,
  formatRelativeTime,
  formatSuccessRate,
  scheduleSummary,
} from './format';
import type { AutopilotRule } from './types';
import { TRIGGER_TYPES } from './types';
import './autopilots.css';

const PAGE_LIMIT = 50;
const STATUS_ALL = 'all';
const TYPE_ALL = 'all';

/** §4.1 触发器列「图标 + 文案」。 */
const TRIGGER_ICONS: Record<string, string> = {
  schedule: '⏰',
  issue_status_changed: '🔀',
  issue_created: '➕',
  issue_field_changed: '📝',
  comment_created: '💬',
  agent_mentioned: '📣',
  webhook_received: '🔗',
};

/** 监听即重拉的实时事件(§3.5)。 */
const AUTOPILOT_LIST_EVENTS: ReadonlySet<string> = new Set([
  'autopilot.updated',
  'autopilot.rate_limited',
  'autopilot_runs.status_changed',
]);

interface AutopilotRowProps {
  readonly rule: AutopilotRule;
  readonly nowMs: number;
  readonly locale: string;
  readonly onOpen: (rule: AutopilotRule) => void;
  readonly onPause: (rule: AutopilotRule) => void;
  readonly onResume: (rule: AutopilotRule) => void;
}

function AutopilotRow(props: AutopilotRowProps): React.JSX.Element {
  const { rule, nowMs, locale, onOpen, onPause, onResume } = props;
  const t = useT();
  const summary =
    rule.trigger_type === 'schedule'
      ? (scheduleSummary(rule.trigger_config) ?? '')
      : t(`autopilots.trigger.${rule.trigger_type}`);
  const successRate = formatSuccessRate(rule.stats?.success_rate ?? null);

  return (
    <tr
      className="mesh-autopilots__row"
      data-testid={`autopilot-row-${rule.id}`}
      onClick={() => onOpen(rule)}
    >
      <td className="mesh-autopilots__cell-name" data-testid={`autopilot-name-${rule.id}`}>
        {rule.name}
      </td>
      <td data-testid={`autopilot-trigger-${rule.id}`}>
        <span aria-hidden="true">{TRIGGER_ICONS[rule.trigger_type] ?? '⚙️'} </span>
        {t(`autopilots.trigger.${rule.trigger_type}`)}
        {summary && rule.trigger_type === 'schedule' ? ` · ${summary}` : ''}
      </td>
      <td>
        <StatusDot tone={RULE_STATUS_TONE[rule.status]} label={t(`autopilots.status.${rule.status}`)} />
      </td>
      <td data-testid={`autopilot-last-run-${rule.id}`}>
        {rule.last_run_status !== null && rule.last_run_status !== undefined ? (
          <StatusDot
            tone={RUN_STATUS_TONE[rule.last_run_status]}
            label={t(`autopilots.runStatus.${rule.last_run_status}`)}
          />
        ) : null}{' '}
        {formatRelativeTime(rule.last_run_at, nowMs, locale) ?? t('autopilots.runs.never')}
      </td>
      <td data-testid={`autopilot-success-${rule.id}`}>
        {successRate ?? t('autopilots.stats.none')}
        {rule.stats ? ` (${rule.stats.runs_30d})` : ''}
      </td>
      <td>
        {rule.trigger_type === 'schedule'
          ? (formatRelativeTime(rule.next_run_at, nowMs, locale) ?? t('autopilots.schedule.none'))
          : '—'}
      </td>
      <td className="mesh-autopilots__actions" onClick={(event) => event.stopPropagation()}>
        {rule.status === 'active' && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onPause(rule)}
            aria-label={t('autopilots.actions.pause')}
            data-testid={`autopilot-pause-${rule.id}`}
          >
            {t('autopilots.actions.pause')}
          </Button>
        )}
        {rule.status === 'paused' && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onResume(rule)}
            aria-label={t('autopilots.actions.resume')}
            data-testid={`autopilot-resume-${rule.id}`}
          >
            {t('autopilots.actions.resume')}
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={() => onOpen(rule)}>
          {t('autopilots.actions.detail')}
        </Button>
      </td>
    </tr>
  );
}

export function AutopilotsPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const [membership, setMembership] = useState<Membership | null>(null);
  const [rules, setRules] = useState<AutopilotRule[] | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [killSwitchOn, setKillSwitchOn] = useState<boolean | null>(null);
  const [killDialogOpen, setKillDialogOpen] = useState(false);
  const [killReason, setKillReason] = useState('');
  const [killBusy, setKillBusy] = useState(false);

  const statusFilter = searchParams.get('status') ?? STATUS_ALL;
  const typeFilter = searchParams.get('trigger_type') ?? TYPE_ALL;
  const search = searchParams.get('q') ?? '';

  const updateParam = useCallback(
    (key: string, value: string | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === null || value === '') next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  useEffect(() => {
    let cancelled = false;
    const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
    void (async () => {
      try {
        const me = await fetchMe(client);
        const workspace = activeWorkspace(me.memberships);
        if (cancelled) return;
        if (workspace === null) {
          setMembership(null);
          setRules([]);
          return;
        }
        setMembership(workspace);
        const listing = await listAutopilots(client, workspace.workspace_id, {
          status: statusFilter === STATUS_ALL ? undefined : statusFilter,
          trigger_type: typeFilter === TYPE_ALL ? undefined : typeFilter,
          search: search || undefined,
          limit: PAGE_LIMIT,
        });
        const state = await getKillSwitchState(client, workspace.workspace_id);
        if (cancelled) return;
        setRules(listing.data);
        setKillSwitchOn(state.kill_switch);
        setErrorKey(null);
      } catch (error) {
        if (cancelled) return;
        setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
        setRules(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [statusFilter, typeFilter, search, reloadKey]);

  useEffect(() => {
    if (realtime === null || membership === null) return;
    const channel = workspaceAutopilotsChannel(membership!.workspace_id);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      if (AUTOPILOT_LIST_EVENTS.has(frame.event)) setReloadKey((key) => key + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, membership]);

  const runAction = useCallback(
    async (action: () => Promise<unknown>, successMessage: string) => {
      try {
        await action();
        toast.addToast(successMessage, { tone: 'success', closeLabel: t('common.close') });
        setReloadKey((key) => key + 1);
      } catch (error) {
        toast.addToast(
          t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'),
          { tone: 'danger', closeLabel: t('common.close') },
        );
      }
    },
    [toast, t],
  );

  const handlePause = useCallback(
    (rule: AutopilotRule) => {
        const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
      void runAction(
        () => pauseAutopilot(client, membership!.workspace_id, rule.id),
        t('autopilots.toast.paused'),
      );
    },
    [membership, runAction, t],
  );

  const handleResume = useCallback(
    (rule: AutopilotRule) => {
        const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
      void runAction(
        () => resumeAutopilot(client, membership!.workspace_id, rule.id),
        t('autopilots.toast.resumed'),
      );
    },
    [membership, runAction, t],
  );

  const applyKillSwitch = useCallback(
    async (enabled: boolean) => {
        setKillBusy(true);
      try {
        const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
        const result = await setKillSwitch(client, membership!.workspace_id, {
          enabled,
          reason: killReason || undefined,
        });
        setKillSwitchOn(result.kill_switch);
        toast.addToast(
          enabled
            ? t('autopilots.killSwitch.enabledToast', { count: result.paused_autopilots })
            : t('autopilots.killSwitch.disabledToast', { count: result.paused_autopilots }),
          { tone: 'success', closeLabel: t('common.close') },
        );
        setKillDialogOpen(false);
        setKillReason('');
        setReloadKey((key) => key + 1);
      } catch (error) {
        toast.addToast(
          t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'),
          { tone: 'danger', closeLabel: t('common.close') },
        );
      } finally {
        setKillBusy(false);
      }
    },
    [membership, killReason, toast, t],
  );

  const nowMs = Date.now();
  const locale = navigator.language;

  if (membership === null && rules !== null && rules.length === 0 && errorKey === null) {
    return (
      <div className="mesh-autopilots__page">
        <EmptyState
          title={t('autopilots.noWorkspace.title')}
          description={t('autopilots.noWorkspace.description')}
        />
      </div>
    );
  }

  return (
    <div className="mesh-autopilots__page" data-testid="autopilots-page">
      <div className="mesh-autopilots__header">
        <h1 className="mesh-autopilots__title">{t('autopilots.title')}</h1>
        <div className="mesh-autopilots__toolbar">
          <div className="mesh-autopilots__kill-switch" data-testid="autopilot-kill-switch">
            <StatusDot
              tone={killSwitchOn === true ? 'warn' : 'success'}
              label={
                killSwitchOn === true
                  ? t('autopilots.killSwitch.paused')
                  : t('autopilots.killSwitch.on')
              }
            />
            <Button
              variant="danger"
              size="sm"
              onClick={() => setKillDialogOpen(true)}
              data-testid="autopilot-kill-switch-button"
            >
              {killSwitchOn === true
                ? t('autopilots.killSwitch.restore')
                : t('autopilots.killSwitch.trigger')}
            </Button>
          </div>
          <Button
            variant="primary"
            onClick={() => navigate('/autopilots/new')}
            data-testid="autopilot-create"
          >
            {t('autopilots.actions.create')}
          </Button>
        </div>
      </div>

      <div className="mesh-autopilots__toolbar">
        <Select
          label={t('autopilots.filters.status')}
          value={statusFilter}
          onChange={(event) => updateParam('status', event.target.value)}
          data-testid="autopilot-filter-status"
        >
          <option value={STATUS_ALL}>{t('autopilots.filters.all')}</option>
          <option value="active">{t('autopilots.status.active')}</option>
          <option value="paused">{t('autopilots.status.paused')}</option>
          <option value="archived">{t('autopilots.status.archived')}</option>
        </Select>
        <Select
          label={t('autopilots.filters.triggerType')}
          value={typeFilter}
          onChange={(event) => updateParam('trigger_type', event.target.value)}
          data-testid="autopilot-filter-type"
        >
          <option value={TYPE_ALL}>{t('autopilots.filters.all')}</option>
          {TRIGGER_TYPES.map((triggerType) => (
            <option key={triggerType} value={triggerType}>
              {t(`autopilots.trigger.${triggerType}`)}
            </option>
          ))}
        </Select>
        <Input
          label={t('autopilots.filters.search')}
          value={search}
          onChange={(event) => updateParam('q', event.target.value)}
          placeholder={t('autopilots.filters.searchPlaceholder')}
          data-testid="autopilot-search"
        />
        <Button variant="ghost" size="sm" onClick={() => navigate('/webhooks')}>
          {t('autopilots.webhook.nav')}
        </Button>
      </div>

      {errorKey !== null && (
        <ErrorState title={t(errorKey)} retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((key) => key + 1)} />
      )}
      {rules === null && errorKey === null && <Skeleton loadingLabel={t('autopilots.loading')} />}
      {rules !== null && rules.length === 0 && errorKey === null && (
        <EmptyState
          illustration={<EmptyAutomation />}
          title={t('onboarding.empty.automation.title')}
          description={t('onboarding.empty.automation.description')}
          action={
            <Button
              variant="primary"
              data-testid="autopilot-empty-create"
              onClick={() => navigate('/autopilots/new')}
            >
              {t('onboarding.empty.automation.action')}
            </Button>
          }
        />
      )}
      {rules !== null && rules.length > 0 && (
        <table className="mesh-autopilots__table" data-testid="autopilots-table">
          <thead>
            <tr>
              <th>{t('autopilots.columns.name')}</th>
              <th>{t('autopilots.columns.trigger')}</th>
              <th>{t('autopilots.columns.status')}</th>
              <th>{t('autopilots.columns.lastRun')}</th>
              <th>{t('autopilots.columns.successRate')}</th>
              <th>{t('autopilots.columns.nextRun')}</th>
              <th>{t('autopilots.columns.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <AutopilotRow
                key={rule.id}
                rule={rule}
                nowMs={nowMs}
                locale={locale}
                onOpen={(opened) => navigate(`/autopilots/${opened.id}`)}
                onPause={handlePause}
                onResume={handleResume}
              />
            ))}
          </tbody>
        </table>
      )}

      <Dialog
        open={killDialogOpen}
        onClose={() => setKillDialogOpen(false)}
        title={t('autopilots.killSwitch.dialogTitle')}
        closeLabel={t('common.close')}
      >
        <p data-testid="autopilot-kill-dialog-text">
          {killSwitchOn === true
            ? t('autopilots.killSwitch.restoreConfirm')
            : t('autopilots.killSwitch.triggerConfirm')}
        </p>
        {killSwitchOn !== true && (
          <Input
            label={t('autopilots.killSwitch.reasonLabel')}
            value={killReason}
            onChange={(event) => setKillReason(event.target.value)}
            hint={t('autopilots.killSwitch.reasonHint')}
            data-testid="autopilot-kill-reason"
          />
        )}
        <div className="mesh-autopilots__footer">
          <Button variant="ghost" onClick={() => setKillDialogOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            isLoading={killBusy}
            disabled={killSwitchOn !== true && killReason.trim().length === 0}
            onClick={() => void applyKillSwitch(killSwitchOn !== true)}
            data-testid="autopilot-kill-confirm"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
