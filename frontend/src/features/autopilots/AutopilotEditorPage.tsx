/**
 * 规则编辑器(autopilot.md §4.2):四段可折叠区块——触发器 → 过滤 → 动作 →
 * 护栏与重试,底部固定 [取消][保存草稿(paused)][保存并启用]。cron 提供
 * 「下次 5 次运行预览」实时刷新(cron/时区变更防抖重算,经无状态
 * preview-schedule 端点,新建态同样可用,非法式显示无效提示);
 * 动作区可增删排序;run_agent_prompt 强制选执行者 agent + prompt(§5.1
 * executor_required)。新建时护栏以推荐默认值预填,体现「护栏默认开启」。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import { Button, ErrorState, Icon, Input, Select, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { listAgents } from '../agents/api';
import type { AgentSummary } from '../agents/types';
import { useWorkspaceMembership, workspaceRoute } from '../members/useWorkspaceMembership';
import {
  createAutopilot,
  getAutopilot,
  listWebhookSecrets,
  patchAutopilot,
  previewScheduleParams,
} from './api';
import type {
  ActionConfigItem,
  ActionKind,
  AutopilotRule,
  AutopilotTriggerType,
  RetryBackoff,
  WebhookSecretPublic,
} from './types';
import { ACTION_KINDS, TRIGGER_TYPES } from './types';
import './autopilots.css';

const TIMEZONE_SUGGESTIONS = ['Asia/Shanghai', 'UTC', 'America/New_York', 'Europe/Berlin'] as const;

/** §4.2 cron 常用周期下拉(选中即填 cron;手填值显示「自定义」)。 */
const CRON_PRESETS: ReadonlyArray<{ readonly key: string; readonly cron: string }> = [
  { key: 'weekdays9', cron: '0 9 * * 1-5' },
  { key: 'daily9', cron: '0 9 * * *' },
  { key: 'hourly', cron: '0 * * * *' },
  { key: 'monday9', cron: '0 9 * * 1' },
  { key: 'monthly1', cron: '0 9 1 * *' },
];

/** §4.2 模板变量插入(run_agent_prompt 提示词辅助)。 */
const TEMPLATE_VARIABLES: ReadonlyArray<string> = [
  '{{trigger.issue.title}}',
  '{{trigger.comment.body}}',
  '{{trigger.actor.name}}',
  '{{trigger.webhook.payload}}',
  '{{steps.0.output}}',
  '{{run.id}}',
  '{{now}}',
];

/** IANA timezone candidates for the datalist (client-side validation aid). */
const IANA_TIMEZONES: readonly string[] =
  typeof Intl !== 'undefined' && typeof Intl.supportedValuesOf === 'function'
    ? Intl.supportedValuesOf('timeZone')
    : TIMEZONE_SUGGESTIONS;
type SectionKey = 'trigger' | 'filter' | 'actions' | 'guardrails';

interface EditorState {
  name: string;
  description: string;
  triggerType: AutopilotTriggerType;
  cron: string;
  timezone: string;
  misfirePolicy: string;
  oneTimeAt: string;
  fromStatus: string;
  toStatus: string;
  watchFields: string;
  targetAgentIds: string;
  scopeProjectIds: string;
  secretId: string;
  eventTypes: string;
  filterProjectIds: string;
  filterActorIds: string;
  filterLabels: string;
  filterPriorities: string;
  filterKeywordInclude: string;
  filterKeywordExclude: string;
  filterPayloadMatch: string;
  executorAgentId: string;
  actions: ActionConfigItem[];
  maxRetries: number;
  retryBackoff: RetryBackoff;
  retryBaseSeconds: number;
  retryMaxSeconds: number;
  rateLimitMax: number;
  rateLimitWindowSeconds: number;
  rateLimitOverflow: 'drop' | 'queue' | 'alert_only';
  concurrencyLimit: number;
  requireApproval: boolean;
  dedupWindowSeconds: number;
  dailyRunBudget: number;
  dailyTokenBudget: number;
  cascadeMaxDepth: number;
  agentLoopDetection: boolean;
  approvalHttp: boolean;
  approvalCreateIssue: boolean;
}

const DEFAULT_STATE: EditorState = {
  name: '',
  description: '',
  triggerType: 'schedule',
  cron: '0 9 * * 1-5',
  timezone: 'Asia/Shanghai',
  misfirePolicy: 'run_once',
  oneTimeAt: '',
  fromStatus: '',
  toStatus: '',
  watchFields: '',
  targetAgentIds: '',
  scopeProjectIds: '',
  secretId: '',
  eventTypes: '',
  filterProjectIds: '',
  filterActorIds: '',
  filterLabels: '',
  filterPriorities: '',
  filterKeywordInclude: '',
  filterKeywordExclude: '',
  filterPayloadMatch: '',
  executorAgentId: '',
  actions: [{ type: 'run_agent_prompt', prompt: '' }],
  maxRetries: 3,
  retryBackoff: 'exponential',
  retryBaseSeconds: 30,
  retryMaxSeconds: 1800,
  rateLimitMax: 10,
  rateLimitWindowSeconds: 3600,
  rateLimitOverflow: 'drop',
  concurrencyLimit: 1,
  requireApproval: false,
  dedupWindowSeconds: 300,
  dailyRunBudget: 200,
  dailyTokenBudget: 2000000,
  cascadeMaxDepth: 3,
  agentLoopDetection: true,
  approvalHttp: true,
  approvalCreateIssue: true,
};

function splitCsv(value: string): string[] {
  return value
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

function stateFromRule(rule: AutopilotRule): EditorState {
  const trigger = rule.trigger_config as Record<string, unknown>;
  const filter = rule.filter_config as Record<string, unknown>;
  const guardrails = rule.guardrails;
  const approvalActions = guardrails.approval_required_actions ?? [];
  return {
    name: rule.name,
    description: rule.description ?? '',
    triggerType: rule.trigger_type,
    cron: typeof trigger.cron === 'string' ? trigger.cron : '0 9 * * 1-5',
    timezone: typeof trigger.timezone === 'string' ? trigger.timezone : 'UTC',
    misfirePolicy: typeof trigger.misfire_policy === 'string' ? trigger.misfire_policy : 'run_once',
    oneTimeAt: typeof trigger.one_time_at === 'string' ? trigger.one_time_at : '',
    fromStatus: Array.isArray(trigger.from_status)
      ? (trigger.from_status as string[]).join(', ')
      : '',
    toStatus: Array.isArray(trigger.to_status) ? (trigger.to_status as string[]).join(', ') : '',
    watchFields: Array.isArray(trigger.watch_fields)
      ? (trigger.watch_fields as string[]).join(', ')
      : '',
    targetAgentIds: Array.isArray(trigger.target_agent_ids)
      ? (trigger.target_agent_ids as string[]).join(', ')
      : '',
    scopeProjectIds: Array.isArray(trigger.scope_project_ids)
      ? (trigger.scope_project_ids as string[]).join(', ')
      : '',
    secretId: typeof trigger.secret_id === 'string' ? trigger.secret_id : '',
    eventTypes: Array.isArray(trigger.event_types)
      ? (trigger.event_types as string[]).join(', ')
      : '',
    filterProjectIds: Array.isArray(filter.project_ids)
      ? (filter.project_ids as string[]).join(', ')
      : '',
    filterActorIds: Array.isArray(filter.actor_ids)
      ? (filter.actor_ids as string[]).join(', ')
      : '',
    filterLabels: Array.isArray(filter.labels) ? (filter.labels as string[]).join(', ') : '',
    filterPriorities: Array.isArray(filter.priorities)
      ? (filter.priorities as string[]).join(', ')
      : '',
    filterKeywordInclude: Array.isArray(filter.keyword_include)
      ? (filter.keyword_include as string[]).join(', ')
      : '',
    filterKeywordExclude: Array.isArray(filter.keyword_exclude)
      ? (filter.keyword_exclude as string[]).join(', ')
      : '',
    filterPayloadMatch: Array.isArray(filter.payload_match)
      ? JSON.stringify(filter.payload_match, null, 2)
      : '',
    executorAgentId: rule.executor_agent_id ?? '',
    actions:
      rule.action_config.length > 0
        ? [...rule.action_config]
        : [{ type: 'run_agent_prompt', prompt: '' }],
    maxRetries: rule.max_retries,
    retryBackoff: rule.retry_backoff,
    retryBaseSeconds: rule.retry_base_seconds,
    retryMaxSeconds: rule.retry_max_seconds,
    rateLimitMax: rule.rate_limit_max,
    rateLimitWindowSeconds: rule.rate_limit_window_seconds,
    rateLimitOverflow: guardrails.rate_limit_overflow,
    concurrencyLimit: rule.concurrency_limit,
    requireApproval: rule.require_approval,
    dedupWindowSeconds: guardrails.dedup_window_seconds,
    dailyRunBudget: guardrails.daily_run_budget,
    dailyTokenBudget: guardrails.daily_token_budget,
    cascadeMaxDepth: guardrails.cascade_max_depth,
    agentLoopDetection: guardrails.agent_loop_detection,
    approvalHttp: approvalActions.includes('http_request'),
    approvalCreateIssue: approvalActions.includes('create_issue'),
  };
}

/** Malformed payload_match JSON in the filter editor — surfaced with a
 * dedicated i18n toast instead of the generic error (R2 LOW). */
class PayloadMatchInvalidError extends Error {
  constructor() {
    super('payload_match must be a JSON array');
    this.name = 'PayloadMatchInvalidError';
  }
}

function buildPayload(state: EditorState): Record<string, unknown> {
  const triggerConfig: Record<string, unknown> = {};
  if (state.triggerType === 'schedule') {
    triggerConfig.cron = state.cron;
    triggerConfig.timezone = state.timezone;
    triggerConfig.misfire_policy = state.misfirePolicy;
    if (state.oneTimeAt) triggerConfig.one_time_at = state.oneTimeAt;
  } else if (state.triggerType === 'issue_status_changed') {
    if (splitCsv(state.fromStatus).length > 0)
      triggerConfig.from_status = splitCsv(state.fromStatus);
    if (splitCsv(state.toStatus).length > 0) triggerConfig.to_status = splitCsv(state.toStatus);
  } else if (state.triggerType === 'issue_field_changed') {
    if (splitCsv(state.watchFields).length > 0)
      triggerConfig.watch_fields = splitCsv(state.watchFields);
  } else if (state.triggerType === 'agent_mentioned') {
    if (splitCsv(state.targetAgentIds).length > 0)
      triggerConfig.target_agent_ids = splitCsv(state.targetAgentIds);
  } else if (state.triggerType === 'webhook_received') {
    triggerConfig.secret_id = state.secretId;
    if (splitCsv(state.eventTypes).length > 0)
      triggerConfig.event_types = splitCsv(state.eventTypes);
  }
  // §2.6 event triggers: optional project scope
  if (state.triggerType !== 'schedule' && state.triggerType !== 'webhook_received') {
    if (splitCsv(state.scopeProjectIds).length > 0)
      triggerConfig.scope_project_ids = splitCsv(state.scopeProjectIds);
  }

  const filterConfig: Record<string, unknown> = {};
  if (splitCsv(state.filterProjectIds).length > 0)
    filterConfig.project_ids = splitCsv(state.filterProjectIds);
  if (splitCsv(state.filterActorIds).length > 0)
    filterConfig.actor_ids = splitCsv(state.filterActorIds);
  if (splitCsv(state.filterLabels).length > 0) filterConfig.labels = splitCsv(state.filterLabels);
  if (splitCsv(state.filterPriorities).length > 0)
    filterConfig.priorities = splitCsv(state.filterPriorities);
  if (splitCsv(state.filterKeywordInclude).length > 0)
    filterConfig.keyword_include = splitCsv(state.filterKeywordInclude);
  if (splitCsv(state.filterKeywordExclude).length > 0)
    filterConfig.keyword_exclude = splitCsv(state.filterKeywordExclude);
  if (state.filterPayloadMatch.trim()) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(state.filterPayloadMatch);
    } catch {
      throw new PayloadMatchInvalidError();
    }
    if (!Array.isArray(parsed)) throw new PayloadMatchInvalidError();
    filterConfig.payload_match = parsed;
  }

  const approvalRequiredActions: string[] = [];
  if (state.approvalHttp) approvalRequiredActions.push('http_request');
  if (state.approvalCreateIssue) approvalRequiredActions.push('create_issue');

  const actions = state.actions.map((action) => {
    const item: Record<string, unknown> = { type: action.type };
    if (action.type === 'run_agent_prompt') {
      item.executor_agent_id = action.executor_agent_id ?? state.executorAgentId ?? undefined;
      item.prompt = action.prompt ?? '';
    }
    if (action.type === 'add_comment') item.content = action.content ?? '';
    if (action.type === 'send_notification') {
      item.message = action.message ?? '';
      item.to = ['owner'];
    }
    if (action.type === 'create_issue') {
      item.title = action.title ?? '';
      if (action.description) item.description = action.description;
    }
    if (action.type === 'http_request') {
      item.url = action.url ?? '';
      item.method = action.method ?? 'POST';
    }
    return item;
  });

  const guardrailsPayload: Record<string, unknown> = {
    rate_limit_overflow: state.rateLimitOverflow,
    dedup_window_seconds: state.dedupWindowSeconds,
    daily_run_budget: state.dailyRunBudget,
    daily_token_budget: state.dailyTokenBudget,
    approval_required_actions: approvalRequiredActions,
    cascade_max_depth: state.cascadeMaxDepth,
    agent_loop_detection: state.agentLoopDetection,
  };

  return {
    name: state.name.trim(),
    description: state.description.trim() || null,
    trigger_type: state.triggerType,
    trigger_config: triggerConfig,
    filter_config: filterConfig,
    action_config: actions,
    executor_agent_id: state.executorAgentId || null,
    guardrails: guardrailsPayload,
    max_retries: state.maxRetries,
    retry_backoff: state.retryBackoff,
    retry_base_seconds: state.retryBaseSeconds,
    retry_max_seconds: state.retryMaxSeconds,
    rate_limit_max: state.rateLimitMax,
    rate_limit_window_seconds: state.rateLimitWindowSeconds,
    concurrency_limit: state.concurrencyLimit,
    require_approval: state.requireApproval,
  };
}

interface SectionProps {
  readonly title: string;
  readonly sectionKey: SectionKey;
  readonly open: SectionKey | null;
  readonly onToggle: (key: SectionKey) => void;
  readonly children: React.ReactNode;
  readonly testId: string;
}

function EditorSection(props: SectionProps): React.JSX.Element {
  const { title, sectionKey, open, onToggle, children, testId } = props;
  const expanded = open === sectionKey;
  return (
    <div className="mesh-autopilots__section">
      <button
        type="button"
        className="mesh-autopilots__section-toggle"
        aria-expanded={expanded}
        onClick={() => onToggle(sectionKey)}
        data-testid={`${testId}-toggle`}
      >
        <span>{title}</span>
        <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
      </button>
      {expanded && (
        <div className="mesh-autopilots__section-body" data-testid={`${testId}-body`}>
          {children}
        </div>
      )}
    </div>
  );
}

export function AutopilotEditorPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const { autopilotId } = useParams<{ autopilotId: string }>();
  const isEdit = autopilotId !== undefined && autopilotId !== 'new';
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const membershipState = useWorkspaceMembership(client);
  const membership = membershipState.kind === 'ready' ? membershipState.membership : null;
  const canManage = membership?.role === 'owner' || membership?.role === 'admin';

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [secrets, setSecrets] = useState<WebhookSecretPublic[]>([]);
  const [state, setState] = useState<EditorState>(DEFAULT_STATE);
  const [openSection, setOpenSection] = useState<SectionKey | null>('trigger');
  const [loading, setLoading] = useState(isEdit);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<string[] | null>(null);

  const patch = useCallback((partial: Partial<EditorState>) => {
    setState((prev) => ({ ...prev, ...partial }));
  }, []);

  useEffect(() => {
    if (membershipState.kind === 'loading') return;
    if (membershipState.kind !== 'ready' || membership === null || !canManage) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const [agentListing, secretListing] = await Promise.all([
          listAgents(client, membership.workspace_id, { limit: 100 }),
          listWebhookSecrets(client, membership.workspace_id),
        ]);
        if (cancelled) return;
        setAgents(agentListing.data);
        setSecrets(secretListing);
        if (isEdit && autopilotId) {
          const rule = await getAutopilot(client, membership.workspace_id, autopilotId);
          if (cancelled) return;
          setState(stateFromRule(rule));
          setLoading(false);
        }
      } catch (error) {
        if (cancelled) return;
        setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, membership, membershipState.kind, canManage, isEdit, autopilotId]);

  // §4.2 live cron preview: recompute on cron/timezone change (debounced),
  // available in CREATE mode too (stateless preview endpoint, no rule id).
  const [previewInvalid, setPreviewInvalid] = useState(false);
  useEffect(() => {
    if (membership === null || state.triggerType !== 'schedule') {
      setPreview(null);
      setPreviewInvalid(false);
      return;
    }
    if (!state.cron.trim() || !state.timezone.trim()) {
      setPreview(null);
      setPreviewInvalid(false);
      return;
    }
    let cancelled = false;
    const handle = setTimeout(() => {
      previewScheduleParams(client, membership.workspace_id, {
        cron: state.cron,
        timezone: state.timezone,
        count: 5,
      })
        .then((result) => {
          if (cancelled) return;
          setPreview([...result.next_runs]);
          setPreviewInvalid(false);
        })
        .catch(() => {
          if (cancelled) return;
          setPreview(null);
          setPreviewInvalid(true);
        });
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [client, membership, state.triggerType, state.cron, state.timezone]);

  const updateAction = useCallback((index: number, partial: Partial<ActionConfigItem>) => {
    setState((prev) => ({
      ...prev,
      actions: prev.actions.map((action, actionIndex) =>
        actionIndex === index ? { ...action, ...partial } : action,
      ),
    }));
  }, []);

  const moveAction = useCallback((index: number, direction: -1 | 1) => {
    setState((prev) => {
      const target = index + direction;
      if (target < 0 || target >= prev.actions.length) return prev;
      const next = [...prev.actions];
      const [moved] = next.splice(index, 1);
      next.splice(target, 0, moved);
      return { ...prev, actions: next };
    });
  }, []);

  const save = useCallback(
    async (activate: boolean) => {
      setSaving(true);
      try {
        const payload = buildPayload(state);
        if (isEdit && autopilotId) {
          await patchAutopilot(client, membership!.workspace_id, autopilotId, payload);
          if (!activate) {
            // 保存草稿语义:暂停规则
            await patchAutopilot(client, membership!.workspace_id, autopilotId, {
              status: 'paused',
            });
          }
        } else {
          const created = await createAutopilot(client, membership!.workspace_id, {
            ...payload,
            status: activate ? 'active' : 'paused',
          });
          toast.addToast(t('autopilots.toast.created'), {
            tone: 'success',
            closeLabel: t('common.close'),
          });
          navigate(
            membership === null
              ? `/autopilots/${created.id}`
              : workspaceRoute(membership.workspace_slug, `/automations/autopilots/${created.id}`),
          );
          return;
        }
        toast.addToast(t('autopilots.toast.saved'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
        navigate(
          membership === null
            ? `/autopilots/${autopilotId}`
            : workspaceRoute(membership.workspace_slug, `/automations/autopilots/${autopilotId}`),
        );
      } catch (error) {
        const messageKey =
          error instanceof PayloadMatchInvalidError
            ? 'autopilots.editor.payloadMatchInvalid'
            : error instanceof MeshApiError
              ? errorToI18nKey(error)
              : 'error.unknown';
        toast.addToast(t(messageKey), { tone: 'danger', closeLabel: t('common.close') });
      } finally {
        setSaving(false);
      }
    },
    [client, membership, state, isEdit, autopilotId, toast, t, navigate],
  );

  const listPath =
    membership === null
      ? '/autopilots'
      : workspaceRoute(membership.workspace_slug, '/automations/autopilots');

  if (membershipState.kind === 'loading' || loading) {
    return (
      <div className="mesh-autopilots__page">
        <Skeleton loadingLabel={t('autopilots.loading')} />
      </div>
    );
  }

  if (membershipState.kind === 'no_workspace') {
    return (
      <div className="mesh-autopilots__page">
        <ErrorState
          title={t('autopilots.noWorkspace.title')}
          description={t('autopilots.noWorkspace.description')}
        />
      </div>
    );
  }

  if (membershipState.kind === 'error') {
    return (
      <div className="mesh-autopilots__page">
        <ErrorState title={t('error.unknown')} />
      </div>
    );
  }

  if (membershipState.kind === 'ready' && !canManage) {
    return (
      <div className="mesh-autopilots__page">
        <ErrorState
          title={t('state.permissionTitle')}
          description={t('state.permissionDescription')}
        />
      </div>
    );
  }

  if (errorKey !== null) {
    return (
      <div className="mesh-autopilots__page">
        <ErrorState
          title={t(errorKey)}
          retryLabel={t('common.retry')}
          onRetry={() => navigate(listPath)}
        />
      </div>
    );
  }

  const nameValid = state.name.trim().length > 0 && state.name.trim().length <= 200;
  const scheduleValid =
    state.triggerType !== 'schedule' ||
    (state.cron.trim().length > 0 && state.timezone.trim().length > 0);
  const webhookValid = state.triggerType !== 'webhook_received' || state.secretId.length > 0;
  const promptActionsValid = state.actions.every(
    (action) =>
      action.type !== 'run_agent_prompt' ||
      (action.executor_agent_id ?? state.executorAgentId ?? '').length > 0,
  );
  const canSave = nameValid && scheduleValid && webhookValid && promptActionsValid;

  return (
    <div className="mesh-autopilots__page" data-testid="autopilot-editor">
      <div className="mesh-autopilots__header">
        <h1 className="mesh-autopilots__title">
          {isEdit ? t('autopilots.editor.titleEdit') : t('autopilots.editor.titleCreate')}
        </h1>
      </div>

      <datalist id="autopilot-tz-list">
        {IANA_TIMEZONES.map((zone) => (
          <option key={zone} value={zone} />
        ))}
      </datalist>
      <div className="mesh-autopilots__editor">
        <div className="mesh-autopilots__field">
          <Input
            label={t('autopilots.editor.nameLabel')}
            value={state.name}
            onChange={(event) => patch({ name: event.target.value })}
            error={
              state.name.length > 0 && !nameValid ? t('autopilots.editor.nameInvalid') : undefined
            }
            data-testid="autopilot-editor-name"
          />
        </div>
        <div className="mesh-autopilots__field">
          <Input
            label={t('autopilots.editor.descriptionLabel')}
            value={state.description}
            onChange={(event) => patch({ description: event.target.value })}
            data-testid="autopilot-editor-description"
          />
        </div>

        <EditorSection
          title={t('autopilots.editor.sectionTrigger')}
          sectionKey="trigger"
          open={openSection}
          onToggle={(key) => setOpenSection(key === openSection ? null : key)}
          testId="autopilot-section-trigger"
        >
          <Select
            label={t('autopilots.editor.triggerTypeLabel')}
            value={state.triggerType}
            onChange={(event) => patch({ triggerType: event.target.value as AutopilotTriggerType })}
            data-testid="autopilot-editor-trigger-type"
          >
            {TRIGGER_TYPES.map((triggerType) => (
              <option key={triggerType} value={triggerType}>
                {t(`autopilots.trigger.${triggerType}`)}
              </option>
            ))}
          </Select>

          {state.triggerType === 'schedule' && (
            <>
              <div className="mesh-autopilots__field-grid">
                <Select
                  label={t('autopilots.editor.cronPresetLabel')}
                  data-testid="autopilot-editor-cron-preset"
                  value={CRON_PRESETS.find((preset) => preset.cron === state.cron)?.key ?? 'custom'}
                  onChange={(event) => {
                    const preset = CRON_PRESETS.find((item) => item.key === event.target.value);
                    if (preset) patch({ cron: preset.cron });
                  }}
                >
                  <option value="custom">{t('autopilots.editor.preset.custom')}</option>
                  {CRON_PRESETS.map((preset) => (
                    <option key={preset.key} value={preset.key}>
                      {t(`autopilots.editor.preset.${preset.key}`)}
                    </option>
                  ))}
                </Select>
                <Input
                  label={t('autopilots.editor.cronLabel')}
                  value={state.cron}
                  onChange={(event) => patch({ cron: event.target.value })}
                  hint={t('autopilots.editor.cronHint')}
                  data-testid="autopilot-editor-cron"
                />
                <Input
                  label={t('autopilots.editor.timezoneLabel')}
                  value={state.timezone}
                  onChange={(event) => patch({ timezone: event.target.value })}
                  hint={t('autopilots.editor.timezoneHint')}
                  list="autopilot-tz-list"
                  data-testid="autopilot-editor-timezone"
                />
                <Select
                  label={t('autopilots.editor.misfireLabel')}
                  data-testid="autopilot-editor-misfire"
                  value={state.misfirePolicy}
                  onChange={(event) => patch({ misfirePolicy: event.target.value })}
                >
                  <option value="run_once">{t('autopilots.misfire.run_once')}</option>
                  <option value="skip">{t('autopilots.misfire.skip')}</option>
                  <option value="run_all">{t('autopilots.misfire.run_all')}</option>
                </Select>
                <Input
                  label={t('autopilots.editor.oneTimeLabel')}
                  data-testid="autopilot-editor-one-time"
                  value={state.oneTimeAt}
                  onChange={(event) => patch({ oneTimeAt: event.target.value })}
                  hint={t('autopilots.editor.oneTimeHint')}
                />
              </div>
              {preview !== null && (
                <div className="mesh-autopilots__preview" data-testid="autopilot-schedule-preview">
                  {t('autopilots.editor.previewTitle')}
                  <ul>
                    {preview.map((moment) => (
                      <li key={moment}>{new Date(moment).toLocaleString()}</li>
                    ))}
                  </ul>
                </div>
              )}
              {previewInvalid && (
                <div className="mesh-autopilots__preview" data-testid="autopilot-preview-invalid">
                  {t('autopilots.editor.previewInvalid')}
                </div>
              )}
            </>
          )}

          {state.triggerType === 'issue_status_changed' && (
            <div className="mesh-autopilots__field-grid">
              <Input
                label={t('autopilots.editor.fromStatusLabel')}
                data-testid="autopilot-editor-from-status"
                value={state.fromStatus}
                onChange={(event) => patch({ fromStatus: event.target.value })}
                hint={t('autopilots.editor.csvHint')}
              />
              <Input
                label={t('autopilots.editor.toStatusLabel')}
                data-testid="autopilot-editor-to-status"
                value={state.toStatus}
                onChange={(event) => patch({ toStatus: event.target.value })}
                hint={t('autopilots.editor.csvHint')}
              />
            </div>
          )}

          {state.triggerType === 'issue_field_changed' && (
            <Input
              label={t('autopilots.editor.watchFieldsLabel')}
              data-testid="autopilot-editor-watch-fields"
              value={state.watchFields}
              onChange={(event) => patch({ watchFields: event.target.value })}
              hint={t('autopilots.editor.csvHint')}
            />
          )}

          {state.triggerType === 'agent_mentioned' && (
            <Input
              label={t('autopilots.editor.targetAgentsLabel')}
              data-testid="autopilot-editor-target-agents"
              value={state.targetAgentIds}
              onChange={(event) => patch({ targetAgentIds: event.target.value })}
              hint={t('autopilots.editor.targetAgentsHint')}
            />
          )}

          {state.triggerType !== 'schedule' && state.triggerType !== 'webhook_received' && (
            <Input
              label={t('autopilots.editor.scopeProjectsLabel')}
              data-testid="autopilot-editor-scope-projects"
              value={state.scopeProjectIds}
              onChange={(event) => patch({ scopeProjectIds: event.target.value })}
              hint={t('autopilots.editor.csvHint')}
            />
          )}

          {state.triggerType === 'webhook_received' && (
            <div className="mesh-autopilots__field-grid">
              <Select
                label={t('autopilots.editor.secretLabel')}
                value={state.secretId}
                onChange={(event) => patch({ secretId: event.target.value })}
                data-testid="autopilot-editor-secret"
              >
                <option value="">{t('autopilots.editor.secretPlaceholder')}</option>
                {secrets.map((secret) => (
                  <option key={secret.id} value={secret.id}>
                    {secret.label} ({secret.status})
                  </option>
                ))}
              </Select>
              <Input
                label={t('autopilots.editor.eventTypesLabel')}
                data-testid="autopilot-editor-event-types"
                value={state.eventTypes}
                onChange={(event) => patch({ eventTypes: event.target.value })}
                hint={t('autopilots.editor.csvHint')}
              />
            </div>
          )}
        </EditorSection>

        <EditorSection
          title={t('autopilots.editor.sectionFilter')}
          sectionKey="filter"
          open={openSection}
          onToggle={(key) => setOpenSection(key === openSection ? null : key)}
          testId="autopilot-section-filter"
        >
          <div className="mesh-autopilots__field-grid">
            <Input
              label={t('autopilots.editor.filterProjects')}
              data-testid="autopilot-editor-filter-projects"
              value={state.filterProjectIds}
              onChange={(event) => patch({ filterProjectIds: event.target.value })}
              hint={t('autopilots.editor.csvHint')}
            />
            <Input
              label={t('autopilots.editor.filterActors')}
              data-testid="autopilot-editor-filter-actors"
              value={state.filterActorIds}
              onChange={(event) => patch({ filterActorIds: event.target.value })}
              hint={t('autopilots.editor.csvHint')}
            />
            <Input
              label={t('autopilots.editor.filterLabels')}
              data-testid="autopilot-editor-filter-labels"
              value={state.filterLabels}
              onChange={(event) => patch({ filterLabels: event.target.value })}
              hint={t('autopilots.editor.csvHint')}
            />
            <Input
              label={t('autopilots.editor.filterPriorities')}
              data-testid="autopilot-editor-filter-priorities"
              value={state.filterPriorities}
              onChange={(event) => patch({ filterPriorities: event.target.value })}
              hint={t('autopilots.editor.csvHint')}
            />
            <Input
              label={t('autopilots.editor.keywordInclude')}
              data-testid="autopilot-editor-keyword-include"
              value={state.filterKeywordInclude}
              onChange={(event) => patch({ filterKeywordInclude: event.target.value })}
              hint={t('autopilots.editor.csvHint')}
            />
            <Input
              label={t('autopilots.editor.keywordExclude')}
              data-testid="autopilot-editor-keyword-exclude"
              value={state.filterKeywordExclude}
              onChange={(event) => patch({ filterKeywordExclude: event.target.value })}
              hint={t('autopilots.editor.csvHint')}
            />
          </div>
          <div className="mesh-autopilots__field">
            <label htmlFor="autopilot-payload-match">{t('autopilots.editor.payloadMatch')}</label>
            <textarea
              id="autopilot-payload-match"
              rows={4}
              value={state.filterPayloadMatch}
              onChange={(event) => patch({ filterPayloadMatch: event.target.value })}
              placeholder='[{"path": "alert.severity", "op": "in", "value": ["critical"]}]'
              data-testid="autopilot-editor-payload-match"
            />
          </div>
        </EditorSection>

        <EditorSection
          title={t('autopilots.editor.sectionActions')}
          sectionKey="actions"
          open={openSection}
          onToggle={(key) => setOpenSection(key === openSection ? null : key)}
          testId="autopilot-section-actions"
        >
          <Select
            label={t('autopilots.editor.executorLabel')}
            value={state.executorAgentId}
            onChange={(event) => patch({ executorAgentId: event.target.value })}
            data-testid="autopilot-editor-executor"
          >
            <option value="">{t('autopilots.editor.executorPlaceholder')}</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </Select>
          {state.actions.map((action, index) => (
            <div
              className="mesh-autopilots__action-item"
              key={index}
              data-testid={`autopilot-action-${index}`}
            >
              <div className="mesh-autopilots__action-head">
                <Select
                  label={t('autopilots.editor.actionTypeLabel')}
                  value={action.type}
                  data-testid={`autopilot-action-type-${index}`}
                  onChange={(event) =>
                    updateAction(index, { type: event.target.value as ActionKind })
                  }
                >
                  {ACTION_KINDS.map((kind) => (
                    <option key={kind} value={kind}>
                      {t(`autopilots.action.${kind}`)}
                    </option>
                  ))}
                </Select>
                <div className="mesh-autopilots__actions">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => moveAction(index, -1)}
                    aria-label={t('autopilots.editor.moveUp')}
                  >
                    ↑
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => moveAction(index, 1)}
                    aria-label={t('autopilots.editor.moveDown')}
                  >
                    ↓
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setState((prev) => ({
                        ...prev,
                        actions: prev.actions.filter((_, filtered) => filtered !== index),
                      }))
                    }
                    disabled={state.actions.length <= 1}
                    aria-label={t('autopilots.editor.removeAction')}
                  >
                    <Icon name="close" size={16} />
                  </Button>
                </div>
              </div>
              {action.type === 'run_agent_prompt' && (
                <>
                  <Select
                    label={t('autopilots.editor.actionExecutorLabel')}
                    data-testid="autopilot-editor-action-executor"
                    value={action.executor_agent_id ?? state.executorAgentId}
                    onChange={(event) =>
                      updateAction(index, { executor_agent_id: event.target.value })
                    }
                  >
                    <option value="">{t('autopilots.editor.executorPlaceholder')}</option>
                    {agents.map((agent) => (
                      <option key={agent.id} value={agent.id}>
                        {agent.name}
                      </option>
                    ))}
                  </Select>
                  <div className="mesh-autopilots__field">
                    <label htmlFor={`autopilot-prompt-${index}`}>
                      {t('autopilots.editor.promptLabel')}
                    </label>
                    <div
                      className="mesh-autopilots__template-vars"
                      data-testid={`autopilot-template-vars-${index}`}
                    >
                      <span>{t('autopilots.editor.templateVarsLabel')}</span>
                      {TEMPLATE_VARIABLES.map((variable) => (
                        <button
                          key={variable}
                          type="button"
                          className="mesh-autopilots__template-var"
                          onClick={() =>
                            updateAction(index, {
                              prompt: `${action.prompt ?? ''}${variable}`,
                            })
                          }
                        >
                          {variable}
                        </button>
                      ))}
                    </div>
                    <textarea
                      id={`autopilot-prompt-${index}`}
                      rows={3}
                      value={action.prompt ?? ''}
                      onChange={(event) => updateAction(index, { prompt: event.target.value })}
                      data-testid={`autopilot-action-prompt-${index}`}
                    />
                  </div>
                </>
              )}
              {action.type === 'add_comment' && (
                <Input
                  label={t('autopilots.editor.commentLabel')}
                  data-testid="autopilot-editor-action-content"
                  value={action.content ?? ''}
                  onChange={(event) => updateAction(index, { content: event.target.value })}
                />
              )}
              {action.type === 'send_notification' && (
                <Input
                  label={t('autopilots.editor.notificationLabel')}
                  data-testid="autopilot-editor-action-message"
                  value={action.message ?? ''}
                  onChange={(event) => updateAction(index, { message: event.target.value })}
                />
              )}
              {action.type === 'create_issue' && (
                <div className="mesh-autopilots__field-grid">
                  <Input
                    label={t('autopilots.editor.issueTitleLabel')}
                    data-testid="autopilot-editor-action-issue-title"
                    value={action.title ?? ''}
                    onChange={(event) => updateAction(index, { title: event.target.value })}
                  />
                  <Input
                    label={t('autopilots.editor.issueDescriptionLabel')}
                    data-testid="autopilot-editor-action-issue-description"
                    value={action.description ?? ''}
                    onChange={(event) => updateAction(index, { description: event.target.value })}
                  />
                </div>
              )}
              {action.type === 'http_request' && (
                <div className="mesh-autopilots__field-grid">
                  <Input
                    label={t('autopilots.editor.urlLabel')}
                    data-testid="autopilot-editor-action-url"
                    value={action.url ?? ''}
                    onChange={(event) => updateAction(index, { url: event.target.value })}
                    hint={t('autopilots.editor.urlHint')}
                  />
                  <Select
                    label={t('autopilots.editor.methodLabel')}
                    data-testid="autopilot-editor-action-method"
                    value={action.method ?? 'POST'}
                    onChange={(event) => updateAction(index, { method: event.target.value })}
                  >
                    <option value="POST">POST</option>
                    <option value="PUT">PUT</option>
                    <option value="PATCH">PATCH</option>
                  </Select>
                </div>
              )}
            </div>
          ))}
          <Button
            variant="secondary"
            size="sm"
            onClick={() =>
              setState((prev) => ({
                ...prev,
                actions: [...prev.actions, { type: 'send_notification', message: '' }],
              }))
            }
            data-testid="autopilot-add-action"
          >
            {t('autopilots.editor.addAction')}
          </Button>
        </EditorSection>

        <EditorSection
          title={t('autopilots.editor.sectionGuardrails')}
          sectionKey="guardrails"
          open={openSection}
          onToggle={(key) => setOpenSection(key === openSection ? null : key)}
          testId="autopilot-section-guardrails"
        >
          <div className="mesh-autopilots__field-grid">
            <Input
              label={t('autopilots.editor.rateLimitLabel')}
              data-testid="autopilot-editor-rate-max"
              type="number"
              min={0}
              value={state.rateLimitMax}
              onChange={(event) => patch({ rateLimitMax: Number(event.target.value) })}
            />
            <Input
              label={t('autopilots.editor.rateWindowLabel')}
              data-testid="autopilot-editor-rate-window"
              type="number"
              min={1}
              value={state.rateLimitWindowSeconds}
              onChange={(event) => patch({ rateLimitWindowSeconds: Number(event.target.value) })}
            />
            <Select
              label={t('autopilots.editor.overflowLabel')}
              data-testid="autopilot-editor-overflow"
              value={state.rateLimitOverflow}
              onChange={(event) =>
                patch({ rateLimitOverflow: event.target.value as 'drop' | 'queue' | 'alert_only' })
              }
            >
              <option value="drop">{t('autopilots.editor.overflow.drop')}</option>
              <option value="queue">{t('autopilots.editor.overflow.queue')}</option>
              <option value="alert_only">{t('autopilots.editor.overflow.alert_only')}</option>
            </Select>
            <Input
              label={t('autopilots.editor.concurrencyLabel')}
              data-testid="autopilot-editor-concurrency"
              type="number"
              min={1}
              value={state.concurrencyLimit}
              onChange={(event) => patch({ concurrencyLimit: Number(event.target.value) })}
            />
            <Input
              label={t('autopilots.editor.dedupWindowLabel')}
              data-testid="autopilot-editor-dedup-window"
              type="number"
              min={0}
              value={state.dedupWindowSeconds}
              onChange={(event) => patch({ dedupWindowSeconds: Number(event.target.value) })}
            />
            <Input
              label={t('autopilots.editor.maxRetriesLabel')}
              data-testid="autopilot-editor-max-retries"
              type="number"
              min={0}
              value={state.maxRetries}
              onChange={(event) => patch({ maxRetries: Number(event.target.value) })}
            />
            <Select
              label={t('autopilots.editor.backoffLabel')}
              data-testid="autopilot-editor-backoff"
              value={state.retryBackoff}
              onChange={(event) => patch({ retryBackoff: event.target.value as RetryBackoff })}
            >
              <option value="exponential">{t('autopilots.backoff.exponential')}</option>
              <option value="linear">{t('autopilots.backoff.linear')}</option>
              <option value="fixed">{t('autopilots.backoff.fixed')}</option>
            </Select>
            <Input
              label={t('autopilots.editor.dailyRunBudgetLabel')}
              data-testid="autopilot-editor-daily-runs"
              type="number"
              min={0}
              value={state.dailyRunBudget}
              onChange={(event) => patch({ dailyRunBudget: Number(event.target.value) })}
            />
            <Input
              label={t('autopilots.editor.dailyTokenBudgetLabel')}
              data-testid="autopilot-editor-daily-tokens"
              type="number"
              min={0}
              value={state.dailyTokenBudget}
              onChange={(event) => patch({ dailyTokenBudget: Number(event.target.value) })}
            />
            <Input
              label={t('autopilots.editor.cascadeDepthLabel')}
              data-testid="autopilot-editor-cascade"
              type="number"
              min={0}
              value={state.cascadeMaxDepth}
              onChange={(event) => patch({ cascadeMaxDepth: Number(event.target.value) })}
            />
          </div>
          <label>
            <input
              type="checkbox"
              checked={state.requireApproval}
              onChange={(event) => patch({ requireApproval: event.target.checked })}
              data-testid="autopilot-editor-require-approval"
            />{' '}
            {t('autopilots.editor.requireApprovalLabel')}
          </label>
          <label>
            <input
              type="checkbox"
              checked={state.agentLoopDetection}
              onChange={(event) => patch({ agentLoopDetection: event.target.checked })}
              data-testid="autopilot-editor-loop-detection"
            />{' '}
            {t('autopilots.editor.loopDetectionLabel')}
          </label>
          <label>
            <input
              type="checkbox"
              checked={state.approvalHttp}
              onChange={(event) => patch({ approvalHttp: event.target.checked })}
              data-testid="autopilot-editor-approval-http"
            />{' '}
            {t('autopilots.editor.approvalHttpLabel')}
          </label>
          <label>
            <input
              type="checkbox"
              checked={state.approvalCreateIssue}
              onChange={(event) => patch({ approvalCreateIssue: event.target.checked })}
              data-testid="autopilot-editor-approval-create-issue"
            />{' '}
            {t('autopilots.editor.approvalCreateIssueLabel')}
          </label>
        </EditorSection>

        <div className="mesh-autopilots__footer">
          <Button variant="ghost" onClick={() => navigate(listPath)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="secondary"
            isLoading={saving}
            disabled={!canSave}
            onClick={() => void save(false)}
          >
            {t('autopilots.editor.saveDraft')}
          </Button>
          <Button
            variant="primary"
            isLoading={saving}
            disabled={!canSave}
            onClick={() => void save(true)}
            data-testid="autopilot-editor-save"
          >
            {t('autopilots.editor.saveActivate')}
          </Button>
        </div>
      </div>
    </div>
  );
}
