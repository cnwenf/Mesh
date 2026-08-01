/**
 * Agent 详情页(agent.md §4.3):概览 / 配置 / 技能与工具 / 可见性与权限 / 历史 五 Tab。
 *
 * 从成员名册页 agent 行深链进入(README §6.12 唯一名册入口的详情深链)。
 * 配置 Tab 保存即生成 NEW 配置版本(不可变历史,§2.7);历史 Tab 支持回滚
 * (= 复制旧快照为新版本)。生命周期动作按 §4.8 状态机呈现可用动词。
 * 实时:订阅 workspace:{ws}:agents 频道,agent.updated / lifecycle_changed
 * 触发重拉,agent.deleted 回名册(README §6.7)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AgentStatsCard } from '../analytics/AgentStatsCard';
import { AgentSkillsTab } from '../skills/AgentSkillsTab';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import {
  Avatar,
  Badge,
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  RunStateBadge,
  Select,
  Skeleton,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import {
  agentPresenceChannel,
  getAgent,
  listConfigVersions,
  rollbackConfig,
  transferAgent,
  transitionAgentLifecycle,
  updateAgent,
  updateAgentConfig,
  workspaceAgentsChannel,
} from './api';
import type { AgentLifecycleVerb } from './api';
import { AgentWizard } from './AgentWizard';
import { presenceToRunState } from './runState';
import type { AgentConfigVersion, AgentDetail } from './types';
import { MODEL_TIER_ORDER, PLATFORM_MODELS, REASONING_EFFORT_ORDER } from './types';
import './agents.css';

type TabKey = 'overview' | 'config' | 'skills' | 'visibility' | 'history';

const TAB_KEYS: readonly TabKey[] = ['overview', 'config', 'skills', 'visibility', 'history'];

function tabFromParam(value: string | null): TabKey {
  return TAB_KEYS.includes(value as TabKey) ? (value as TabKey) : 'overview';
}

/** §4.8 状态机:每个状态可用的动作动词。 */
const VERBS_BY_STATUS: Record<string, readonly AgentLifecycleVerb[]> = {
  active: ['pause', 'disable', 'archive'],
  paused: ['resume', 'disable', 'archive'],
  disabled: ['enable', 'archive'],
  archived: ['restore'],
};

export function AgentDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const { agentId } = useParams<{ agentId: string }>();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);

  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = tabFromParam(searchParams.get('tab'));

  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [versions, setVersions] = useState<AgentConfigVersion[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [editOpen, setEditOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  // 配置 Tab 的本地编辑态(保存时 PATCH /config → 新版本)。
  const [instructions, setInstructions] = useState('');
  const [temperature, setTemperature] = useState('0.2');
  const [topP, setTopP] = useState('1');
  const [maxTokens, setMaxTokens] = useState('8192');
  const [reasoningEffort, setReasoningEffort] = useState<'low' | 'medium' | 'high'>('medium');
  const [modelTier, setModelTier] = useState<string>('balanced');
  const [model, setModel] = useState('');
  const [preset, setPreset] = useState('');

  // M-F1:暂停弹窗(选 in_flight_policy + 原因)。
  const [pauseOpen, setPauseOpen] = useState(false);
  const [pausePolicy, setPausePolicy] = useState<'finish_current' | 'cancel_current'>(
    'finish_current',
  );
  const [pauseReason, setPauseReason] = useState('');

  // H-F5:可见性 Tab 的转移所有权弹窗。
  const [transferOpen, setTransferOpen] = useState(false);
  const [transferUserId, setTransferUserId] = useState('');

  // H-F4:历史 Tab 「对比上一版」展开的版本 id。
  const [compareId, setCompareId] = useState<string | null>(null);

  // M-F2:presence 容量三元组(运行中/排队/需审批);数据来自 runtime,延后落地,
  // 此处订阅脚手架就位,无帧时为 null(渲染「—」)。
  const [presence, setPresence] = useState<{
    running: number;
    queued: number;
    awaiting: number;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((me) => {
        if (!cancelled) setWorkspace(activeWorkspace(me.memberships));
      })
      .catch(() => {
        if (!cancelled) setError(t('state.errorDescription'));
      });
    return () => {
      cancelled = true;
    };
  }, [client, t]);

  const loadAgent = useCallback(() => {
    if (workspace === null || agentId === undefined) return;
    setIsLoading(true);
    setError(null);
    getAgent(client, workspace.workspace_id, agentId)
      .then((detail) => {
        setAgent(detail);
        setInstructions(detail.system_instructions ?? '');
        setTemperature(String(detail.model_config.temperature ?? 0.2));
        setTopP(String(detail.model_config.top_p ?? 1));
        setMaxTokens(String(detail.model_config.max_tokens ?? 8192));
        setReasoningEffort(detail.model_config.reasoning_effort ?? 'medium');
        setModelTier(detail.model_config.model_tier ?? 'balanced');
        setModel(detail.model_config.model ?? '');
        setPreset(detail.model_config.preset ?? '');
      })
      .catch((err) => setError(err instanceof Error ? err.message : t('state.errorDescription')))
      .finally(() => setIsLoading(false));
  }, [client, workspace, agentId, t]);

  useEffect(() => {
    loadAgent();
  }, [loadAgent, reloadKey]);

  // 历史 Tab 需要版本列表。
  useEffect(() => {
    if (workspace === null || agentId === undefined || activeTab !== 'history') return;
    let cancelled = false;
    listConfigVersions(client, workspace.workspace_id, agentId, { limit: 50 })
      .then((result) => {
        if (!cancelled) setVersions(result.data);
      })
      .catch(() => {
        if (!cancelled) setVersions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspace, agentId, activeTab, reloadKey]);

  // 实时:agent 域事件 → 重拉或回名册(README §6.7)。
  useEffect(() => {
    if (realtime === null || workspace === null) return;
    const channel = workspaceAgentsChannel(workspace.workspace_id);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      const payload = frame.payload as { data?: { id?: string; agent_id?: string } };
      const targetId = payload.data?.agent_id ?? payload.data?.id;
      if (targetId !== agentId) return;
      if (frame.event === 'agent.deleted') {
        navigate('/members');
        return;
      }
      if (frame.event === 'agent.updated' || frame.event === 'agent.lifecycle_changed') {
        setReloadKey((key) => key + 1);
      }
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspace, agentId, navigate]);

  // M-F2:presence 容量三元组订阅脚手架(§4.9/§6.12)。runtime 落地 task_executions
  // 后才会发 agent.presence 帧;在此之前 presence 保持 null,UI 渲染「—」。
  useEffect(() => {
    if (realtime === null || agentId === undefined) return;
    const channel = agentPresenceChannel(agentId);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.event !== 'agent.presence') return;
      const data = frame.payload as {
        running?: number;
        queued?: number;
        awaiting_approval?: number;
      };
      setPresence({
        running: data.running ?? 0,
        queued: data.queued ?? 0,
        awaiting: data.awaiting_approval ?? 0,
      });
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, agentId]);

  const selectTab = (tab: TabKey): void => {
    const params = new URLSearchParams(searchParams);
    if (tab === 'overview') params.delete('tab');
    else params.set('tab', tab);
    setSearchParams(params, { replace: true });
  };

  const handleLifecycle = async (verb: AgentLifecycleVerb): Promise<void> => {
    if (workspace === null || agent === null) return;
    // M-F1:暂停需要选 in_flight_policy,走弹窗;其它动词直接发。
    if (verb === 'pause') {
      setPauseReason('');
      setPausePolicy('finish_current');
      setPauseOpen(true);
      return;
    }
    try {
      await transitionAgentLifecycle(client, workspace.workspace_id, agent.id, verb);
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const confirmPause = async (): Promise<void> => {
    if (workspace === null || agent === null) return;
    try {
      await transitionAgentLifecycle(client, workspace.workspace_id, agent.id, 'pause', {
        in_flight_policy: pausePolicy,
        reason: pauseReason === '' ? undefined : pauseReason,
      });
      setPauseOpen(false);
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const handleTransfer = async (): Promise<void> => {
    if (workspace === null || agent === null) return;
    const trimmed = transferUserId.trim();
    if (trimmed === '') return;
    try {
      await transferAgent(client, workspace.workspace_id, agent.id, trimmed);
      setTransferOpen(false);
      setTransferUserId('');
      toast.addToast(t('agents.toast.transferred'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const handleVisibilityChange = async (next: 'workspace' | 'private'): Promise<void> => {
    if (workspace === null || agent === null || next === agent.visibility) return;
    try {
      await updateAgent(client, workspace.workspace_id, agent.id, { visibility: next });
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  /** §2.4 预设套用:一键填充模型参数(与向导同源)。 */
  const PRESETS: Record<
    string,
    { tier: string; temp: string; top: string; max: string; effort: 'low' | 'medium' | 'high' }
  > = {
    strict_engineering: { tier: 'balanced', temp: '0.2', top: '1', max: '8192', effort: 'medium' },
    creative_draft: {
      tier: 'strong_reasoning',
      temp: '0.9',
      top: '1',
      max: '8192',
      effort: 'high',
    },
    fast_triage: { tier: 'lightweight_fast', temp: '0.3', top: '1', max: '2048', effort: 'low' },
  };
  const applyPreset = (key: string): void => {
    setPreset(key);
    const p = PRESETS[key];
    if (p === undefined) return;
    setModelTier(p.tier);
    setTemperature(p.temp);
    setTopP(p.top);
    setMaxTokens(p.max);
    setReasoningEffort(p.effort);
  };

  // H-F2:保存前越界校验(与后端 §2.4 同口径),红字拦截,禁用保存按钮。
  const tempNum = Number(temperature);
  const topPNum = Number(topP);
  const maxNum = Number(maxTokens);
  const tempError =
    temperature === '' || !Number.isFinite(tempNum) || tempNum < 0 || tempNum > 2
      ? t('agents.validation.temperatureRange')
      : null;
  const topPError =
    topP === '' || !Number.isFinite(topPNum) || topPNum < 0 || topPNum > 1
      ? t('agents.validation.topPRange')
      : null;
  const maxError =
    maxTokens === '' || !Number.isInteger(maxNum) || maxNum < 1
      ? t('agents.validation.maxTokensMin')
      : null;
  const configError = tempError ?? topPError ?? maxError;

  const saveConfig = async (): Promise<void> => {
    if (workspace === null || agent === null || configError !== null) return;
    setIsSaving(true);
    try {
      await updateAgentConfig(client, workspace.workspace_id, agent.id, {
        model_config: {
          model: model === '' ? undefined : model,
          model_tier: modelTier as 'balanced',
          temperature: tempNum,
          top_p: topPNum,
          max_tokens: maxNum,
          reasoning_effort: reasoningEffort,
          preset: preset === '' ? undefined : preset,
        },
        system_instructions: instructions === '' ? null : instructions,
      });
      toast.addToast(t('agents.toast.configSaved'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleRollback = async (versionId: string): Promise<void> => {
    if (workspace === null || agent === null) return;
    try {
      await rollbackConfig(client, workspace.workspace_id, agent.id, versionId);
      toast.addToast(t('agents.toast.rolledBack'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setReloadKey((key) => key + 1);
      selectTab('config');
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const canManage =
    workspace !== null && (workspace.role === 'owner' || workspace.role === 'admin');

  if (error !== null) {
    return (
      <div className="mesh-agents-detail">
        <ErrorState
          title={t('state.errorTitle')}
          description={error}
          retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      </div>
    );
  }

  if (isLoading || agent === null) {
    return (
      <div className="mesh-agents-detail">
        <Skeleton loadingLabel={t('common.loading')} />
      </div>
    );
  }

  const verbs = VERBS_BY_STATUS[agent.lifecycle_status] ?? [];
  // 运行态五态归一(§9.8):presence 三元组 → RunState;帧未至(null)→ unknown。
  const runState = presenceToRunState(presence);

  return (
    <div className="mesh-agents-detail" data-testid="agent-detail-page">
      <div className="mesh-agents-detail__header">
        <div className="mesh-agents-detail__identity">
          <Button
            variant="ghost"
            data-testid="agent-detail-back"
            onClick={() => navigate('/members')}
          >
            {t('agents.detail.back')}
          </Button>
          <Avatar kind="agent" size={40} name={agent.name} src={agent.avatar_url ?? undefined} />
          <h1
            className="mesh-agents-detail__title mesh-text-title-2"
            data-testid="agent-detail-name"
          >
            {agent.name}
          </h1>
          <span data-testid="agent-detail-badge">
            <Badge tone="accent">{t('members.badge.agent')}</Badge>
          </span>
          <span
            className="mesh-agents-detail__status mesh-text-micro"
            data-testid="agent-detail-status"
          >
            {t(`agents.lifecycle.${agent.lifecycle_status}`)}
          </span>
          {/* 运行态五态徽标(§9.8 统一语言);data-state 供测试与样式钩子。 */}
          <span className="mesh-agents-detail__presence" data-testid="agent-detail-presence">
            <RunStateBadge state={runState} label={t(`runState.${runState}`)} />
          </span>
        </div>
        {canManage ? (
          <div className="mesh-agents-detail__actions">
            {verbs.map((verb) => (
              <Button
                key={verb}
                variant={verb === 'disable' || verb === 'archive' ? 'danger' : 'secondary'}
                size="sm"
                data-testid={`agent-${verb}-button`}
                onClick={() => void handleLifecycle(verb)}
              >
                {t(`agents.verb.${verb}`)}
              </Button>
            ))}
            <Button
              variant="primary"
              size="sm"
              data-testid="agent-edit-button"
              onClick={() => setEditOpen(true)}
            >
              {t('agents.detail.edit')}
            </Button>
          </div>
        ) : null}
      </div>

      {/* M-F2:容量三元组说明(§4.9/§6.12);帧未至时为「Capacity: —」。 */}
      <p
        className="mesh-agents-detail__presence-caption mesh-text-caption mesh-tnum"
        data-testid="agent-detail-presence-caption"
      >
        {presence === null
          ? t('agents.presence.unknown')
          : t('agents.presence.triple', {
              running: presence.running,
              queued: presence.queued,
              awaiting: presence.awaiting,
            })}
      </p>

      {agent.role_tag !== null || agent.bio !== null ? (
        <p className="mesh-agents-detail__subtitle mesh-text-body-sm">
          {agent.role_tag ?? ''}
          {agent.bio !== null && agent.bio !== '' ? ` · ${agent.bio}` : ''}
        </p>
      ) : null}

      <div className="mesh-members__tabs" role="tablist" aria-label={t('agents.detail.tabsLabel')}>
        {TAB_KEYS.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            className="mesh-members__tab mesh-text-body"
            data-testid={`agent-tab-${tab}`}
            onClick={() => selectTab(tab)}
          >
            {t(`agents.tab.${tab}`)}
          </button>
        ))}
      </div>

      {activeTab === 'overview' ? (
        <section className="mesh-agents-detail__panel" data-testid="agent-panel-overview">
          <dl className="mesh-agents-detail__dl">
            <dt className="mesh-text-caption">{t('agents.field.visibility')}</dt>
            <dd className="mesh-text-body">{t(`agents.visibility.${agent.visibility}`)}</dd>
            <dt className="mesh-text-caption">{t('agents.field.triggerOnAssign')}</dt>
            <dd className="mesh-text-body">
              {agent.trigger_on_assign ? t('common.yes') : t('common.no')}
            </dd>
            <dt className="mesh-text-caption">{t('agents.detail.modelTier')}</dt>
            <dd className="mesh-text-body">
              {t(`agents.tier.${agent.model_config.model_tier ?? 'balanced'}`)}
            </dd>
            <dt className="mesh-text-caption">{t('agents.detail.created')}</dt>
            <dd className="mesh-text-body mesh-tnum">{agent.created_at}</dd>
          </dl>
          {/* 统计报表(analytics.md §4.4):agent 运行统计卡(名册深链唯一入口) */}
          {workspace !== null ? (
            <AgentStatsCard
              client={client}
              workspaceId={workspace.workspace_id}
              agentId={agent.id}
            />
          ) : null}
        </section>
      ) : null}

      {activeTab === 'config' ? (
        <section className="mesh-agents-detail__panel" data-testid="agent-panel-config">
          <fieldset className="mesh-agents-wizard__fieldset">
            <legend className="mesh-text-body-strong">{t('agents.field.modelTier')}</legend>
            {MODEL_TIER_ORDER.map((tier) => (
              <label key={tier} className="mesh-agents-wizard__radio">
                <input
                  type="radio"
                  name="agent-detail-tier"
                  value={tier}
                  checked={modelTier === tier}
                  disabled={!canManage}
                  data-testid={`agent-detail-tier-${tier}`}
                  onChange={() => setModelTier(tier)}
                />
                {t(`agents.tier.${tier}`)}
              </label>
            ))}
          </fieldset>
          <Select
            label={t('agents.field.model')}
            value={model}
            data-testid="agent-detail-model"
            disabled={!canManage}
            onChange={(event) => setModel(event.target.value)}
          >
            <option value="">{t('agents.model.byTier')}</option>
            {PLATFORM_MODELS.map((m) => (
              <option key={m.value} value={m.value}>
                {t(m.labelKey)}
              </option>
            ))}
          </Select>
          <Select
            label={t('agents.field.preset')}
            value={preset}
            data-testid="agent-detail-preset"
            disabled={!canManage}
            onChange={(event) => applyPreset(event.target.value)}
          >
            <option value="">{t('agents.preset.none')}</option>
            <option value="strict_engineering">{t('agents.preset.strict_engineering')}</option>
            <option value="creative_draft">{t('agents.preset.creative_draft')}</option>
            <option value="fast_triage">{t('agents.preset.fast_triage')}</option>
          </Select>
          <label className="mesh-agents-wizard__label" htmlFor="agent-detail-instructions">
            {t('agents.field.systemInstructions')}
          </label>
          <textarea
            id="agent-detail-instructions"
            className="mesh-agents-wizard__textarea"
            data-testid="agent-detail-instructions"
            rows={6}
            value={instructions}
            disabled={!canManage}
            onChange={(event) => setInstructions(event.target.value)}
          />
          <Input
            label={t('agents.field.temperature')}
            value={temperature}
            data-testid="agent-detail-temperature"
            disabled={!canManage}
            error={tempError ?? undefined}
            onChange={(event) => setTemperature(event.target.value)}
          />
          <Input
            label={t('agents.field.topP')}
            value={topP}
            data-testid="agent-detail-top-p"
            disabled={!canManage}
            error={topPError ?? undefined}
            onChange={(event) => setTopP(event.target.value)}
          />
          <Input
            label={t('agents.field.maxTokens')}
            value={maxTokens}
            data-testid="agent-detail-max-tokens"
            disabled={!canManage}
            error={maxError ?? undefined}
            onChange={(event) => setMaxTokens(event.target.value)}
          />
          <Select
            label={t('agents.field.reasoningEffort')}
            value={reasoningEffort}
            data-testid="agent-detail-effort"
            disabled={!canManage}
            onChange={(event) =>
              setReasoningEffort(event.target.value as 'low' | 'medium' | 'high')
            }
          >
            {REASONING_EFFORT_ORDER.map((effort) => (
              <option key={effort} value={effort}>
                {t(`agents.effort.${effort}`)}
              </option>
            ))}
          </Select>
          {configError !== null ? (
            <p className="mesh-agents-wizard__error" role="alert" data-testid="agent-config-error">
              {configError}
            </p>
          ) : null}
          {canManage ? (
            <div className="mesh-agents-detail__panel-footer">
              <Button
                data-testid="agent-config-save"
                isLoading={isSaving}
                disabled={configError !== null}
                onClick={() => void saveConfig()}
              >
                {t('agents.detail.saveConfig')}
              </Button>
            </div>
          ) : null}
        </section>
      ) : null}

      {activeTab === 'skills' && workspace !== null && agentId !== undefined ? (
        /* agent 技能绑定区(skill.md §4.2):已绑定列表 + 从库绑定(MES-107 接通;
           面板外壳与 testid 由 AgentSkillsTab 自持,与其他 Tab 一致)。 */
        <AgentSkillsTab
          workspaceId={workspace.workspace_id}
          agentId={agentId}
          canManage={canManage}
        />
      ) : null}

      {activeTab === 'visibility' ? (
        <section className="mesh-agents-detail__panel" data-testid="agent-panel-visibility">
          <fieldset className="mesh-agents-wizard__fieldset" disabled={!canManage}>
            <legend className="mesh-text-body-strong">{t('agents.field.visibility')}</legend>
            <label className="mesh-agents-wizard__radio">
              <input
                type="radio"
                name="agent-detail-visibility"
                value="workspace"
                checked={agent.visibility === 'workspace'}
                data-testid="agent-detail-visibility-workspace"
                onChange={() => void handleVisibilityChange('workspace')}
              />
              {t('agents.visibility.workspace')}
            </label>
            <label className="mesh-agents-wizard__radio">
              <input
                type="radio"
                name="agent-detail-visibility"
                value="private"
                checked={agent.visibility === 'private'}
                data-testid="agent-detail-visibility-private"
                onChange={() => void handleVisibilityChange('private')}
              />
              {t('agents.visibility.private')}
            </label>
          </fieldset>
          <dl className="mesh-agents-detail__dl">
            <dt className="mesh-text-caption">{t('agents.field.triggerOnAssign')}</dt>
            <dd className="mesh-text-body">
              {agent.trigger_on_assign ? t('common.yes') : t('common.no')}
            </dd>
          </dl>
          {canManage ? (
            <div className="mesh-agents-detail__panel-footer">
              <Button
                variant="secondary"
                data-testid="agent-transfer-button"
                onClick={() => {
                  setTransferUserId('');
                  setTransferOpen(true);
                }}
              >
                {t('agents.visibility.transfer')}
              </Button>
            </div>
          ) : null}
        </section>
      ) : null}

      {activeTab === 'history' ? (
        <section className="mesh-agents-detail__panel" data-testid="agent-panel-history">
          {versions.length === 0 ? (
            <EmptyState title={t('state.emptyTitle')} description={t('agents.history.empty')} />
          ) : (
            <table className="mesh-agents-detail__versions">
              <caption className="sr-only">{t('agents.tab.history')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('agents.history.summary')}</th>
                  <th scope="col">{t('agents.history.createdAt')}</th>
                  <th scope="col">{t('agents.history.action')}</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((version, index) => {
                  const previous = versions[index + 1];
                  const comparing = compareId === version.id;
                  return (
                    <tr key={version.id} data-testid={`agent-version-${version.id}`}>
                      <td>{version.change_summary ?? ''}</td>
                      <td className="mesh-tnum">{version.created_at}</td>
                      <td>
                        <div className="mesh-agents-detail__version-actions">
                          {previous !== undefined ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              data-testid={`agent-compare-${version.id}`}
                              onClick={() => setCompareId(comparing ? null : version.id)}
                            >
                              {comparing
                                ? t('agents.history.hideCompare')
                                : t('agents.history.compare')}
                            </Button>
                          ) : null}
                          {canManage && version.id !== agent.active_config_version_id ? (
                            <Button
                              size="sm"
                              variant="secondary"
                              data-testid={`agent-rollback-${version.id}`}
                              onClick={() => void handleRollback(version.id)}
                            >
                              {t('agents.history.rollback')}
                            </Button>
                          ) : version.id === agent.active_config_version_id ? (
                            <span data-testid={`agent-current-${version.id}`}>
                              {t('agents.history.current')}
                            </span>
                          ) : null}
                        </div>
                        {comparing && previous !== undefined ? (
                          <div
                            className="mesh-agents-detail__compare"
                            data-testid={`agent-compare-body-${version.id}`}
                          >
                            <pre>{JSON.stringify(version.snapshot, null, 2)}</pre>
                            <pre>{JSON.stringify(previous.snapshot, null, 2)}</pre>
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
      ) : null}

      {workspace !== null ? (
        <AgentWizard
          open={editOpen}
          onClose={() => setEditOpen(false)}
          client={client}
          workspaceId={workspace.workspace_id}
          agent={agent}
          onSaved={() => setReloadKey((key) => key + 1)}
        />
      ) : null}

      {/* M-F1:暂停弹窗——选 in_flight_policy(§3.2/§4.10)+ 可选原因。 */}
      <Dialog
        open={pauseOpen}
        onClose={() => setPauseOpen(false)}
        title={t('agents.pause.title')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-agents-detail__dialog-body" data-testid="agent-pause-dialog">
          <label className="mesh-agents-wizard__radio">
            <input
              type="radio"
              name="agent-pause-policy"
              checked={pausePolicy === 'finish_current'}
              data-testid="agent-pause-finish"
              onChange={() => setPausePolicy('finish_current')}
            />
            {t('agents.pause.finishCurrent')}
          </label>
          <label className="mesh-agents-wizard__radio">
            <input
              type="radio"
              name="agent-pause-policy"
              checked={pausePolicy === 'cancel_current'}
              data-testid="agent-pause-cancel"
              onChange={() => setPausePolicy('cancel_current')}
            />
            {t('agents.pause.cancelCurrent')}
          </label>
          <Input
            label={t('agents.pause.reason')}
            value={pauseReason}
            data-testid="agent-pause-reason"
            onChange={(event) => setPauseReason(event.target.value)}
          />
          <Button
            variant="secondary"
            data-testid="agent-pause-cancel-btn"
            onClick={() => setPauseOpen(false)}
          >
            {t('common.cancel')}
          </Button>
          <Button data-testid="agent-pause-confirm" onClick={() => void confirmPause()}>
            {t('agents.verb.pause')}
          </Button>
        </div>
      </Dialog>

      {/* H-F5:所有权转移弹窗(§3.1 :transfer)。 */}
      <Dialog
        open={transferOpen}
        onClose={() => setTransferOpen(false)}
        title={t('agents.visibility.transferTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-agents-detail__dialog-body" data-testid="agent-transfer-dialog">
          <Input
            label={t('agents.visibility.transferLabel')}
            value={transferUserId}
            data-testid="agent-transfer-user-id"
            onChange={(event) => setTransferUserId(event.target.value)}
          />
          <Button
            variant="secondary"
            data-testid="agent-transfer-cancel-btn"
            onClick={() => setTransferOpen(false)}
          >
            {t('common.cancel')}
          </Button>
          <Button
            data-testid="agent-transfer-confirm"
            disabled={transferUserId.trim() === ''}
            onClick={() => void handleTransfer()}
          >
            {t('agents.visibility.transfer')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
