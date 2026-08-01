/**
 * Agent 创建/编辑向导(agent.md §4.4):四步 —— 基本信息 → 模型与指令 →
 * 技能与工具(从合法安装记录选择,创建后绑定)→ 可见性 → 完成。
 *
 * 唯一创建入口:仅从成员名册页「+ 新建 Agent」打开(README §6.12,T35)。
 * 编辑复用同一组件(预填现有值,完成时 PATCH);每步独立校验、可后退不丢数据。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { MeshApiError, errorToI18nKey } from '../../api/errors';
import { Button, Dialog, Input, Select, useToast } from '../../design';
import { useT } from '../../i18n';
import {
  bindSkill,
  listAgentSkills,
  listInstallations,
  listSkills,
  unbindSkill,
} from '../skills/api';
import { effectiveGrants, permissionTone } from '../skills/capabilities';
import type { AgentSkillRow, SkillInstallation, SkillSummary } from '../skills/types';
import { createAgent, getAgent, listAgents, updateAgent, updateAgentConfig } from './api';
import type {
  AgentDetail,
  AgentModelConfig,
  AgentSummary,
  AgentVisibility,
  ModelTier,
  ReasoningEffort,
} from './types';
import { MODEL_TIER_ORDER, PLATFORM_MODELS } from './types';
import './agents.css';

type WizardStep = 'basic' | 'model' | 'skills' | 'visibility';

const STEP_ORDER: readonly WizardStep[] = ['basic', 'model', 'skills', 'visibility'];

/** §2.4 参数预设:一键套用的模板值(字段全必填,运行期恒有定义)。 */
interface PresetParams {
  readonly model_tier: ModelTier;
  readonly temperature: number;
  readonly max_tokens: number;
  readonly reasoning_effort: ReasoningEffort;
}

const PRESETS: Record<string, PresetParams> = {
  strict_engineering: {
    model_tier: 'balanced',
    temperature: 0.2,
    max_tokens: 8192,
    reasoning_effort: 'medium',
  },
  creative_draft: {
    model_tier: 'strong_reasoning',
    temperature: 0.9,
    max_tokens: 8192,
    reasoning_effort: 'high',
  },
  fast_triage: {
    model_tier: 'lightweight_fast',
    temperature: 0.3,
    max_tokens: 2048,
    reasoning_effort: 'low',
  },
};

const NAME_MAX = 120;
const TEMPERATURE_MIN = 0;
const TEMPERATURE_MAX = 2;

interface SkillChoice {
  readonly skill: SkillSummary;
  readonly installation: SkillInstallation;
}

interface WizardState {
  readonly name: string;
  readonly avatarUrl: string;
  readonly roleTag: string;
  readonly bio: string;
  readonly systemInstructions: string;
  readonly modelTier: ModelTier;
  readonly model: string;
  readonly temperature: string;
  readonly topP: string;
  readonly maxTokens: string;
  readonly reasoningEffort: 'low' | 'medium' | 'high';
  readonly preset: string;
  readonly visibility: AgentVisibility;
  readonly triggerOnAssign: boolean;
}

/** §4.4 模板:预置 profile + 模型参数,一键起步。 */
const TEMPLATES: Record<string, Partial<WizardState>> = {
  test: {
    name: '小测',
    roleTag: '测试工程师',
    systemInstructions: '你是测试工程师。收到 issue 后先复现问题,再给最小复现步骤与修复建议。',
    modelTier: 'balanced',
    preset: 'strict_engineering',
  },
  docs: {
    name: '文档助手',
    roleTag: '文档撰写',
    systemInstructions: '你是文档工程师,负责把变更整理成清晰、可检索的文档。',
    modelTier: 'strong_reasoning',
    preset: 'creative_draft',
  },
  ops: {
    name: '值班运维',
    roleTag: '运维',
    systemInstructions: '你是值班运维,优先定位告警根因并给出止血步骤,高风险操作需人工确认。',
    modelTier: 'lightweight_fast',
    preset: 'fast_triage',
  },
};

function stateFromAgent(agent: AgentDetail | null): WizardState {
  const config = agent?.model_config ?? {};
  return {
    name: agent?.name ?? '',
    avatarUrl: agent?.avatar_url ?? '',
    roleTag: agent?.role_tag ?? '',
    bio: agent?.bio ?? '',
    systemInstructions: agent?.system_instructions ?? '',
    modelTier: config.model_tier ?? 'balanced',
    model: config.model ?? '',
    temperature: String(config.temperature ?? 0.2),
    topP: String(config.top_p ?? 1),
    maxTokens: String(config.max_tokens ?? 8192),
    reasoningEffort: config.reasoning_effort ?? 'medium',
    preset: config.preset ?? 'strict_engineering',
    visibility: agent?.visibility ?? 'workspace',
    triggerOnAssign: agent?.trigger_on_assign ?? true,
  };
}

export interface AgentWizardProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  /** 非空 = 编辑模式(预填 + PATCH);空 = 创建模式(POST)。 */
  readonly agent?: AgentDetail | null;
  /** 完成(创建或保存)后回调,参数为最新的 agent id。 */
  readonly onSaved: (agentId: string) => void;
}

export function AgentWizard(props: AgentWizardProps): React.JSX.Element {
  const { open, onClose, client, workspaceId, agent = null, onSaved } = props;
  const t = useT();
  const { addToast } = useToast();

  const [step, setStep] = useState<WizardStep>('basic');
  const [state, setState] = useState<WizardState>(() => stateFromAgent(agent));
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // M-F3:「从现有 agent 复制」候选列表(仅创建态拉取)。
  const [copySources, setCopySources] = useState<AgentSummary[]>([]);
  const [skillChoices, setSkillChoices] = useState<SkillChoice[]>([]);
  const [selectedSkillIds, setSelectedSkillIds] = useState<ReadonlySet<string>>(() => new Set());
  const [existingBindings, setExistingBindings] = useState<AgentSkillRow[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillsLoadError, setSkillsLoadError] = useState(false);
  const [skillsReloadKey, setSkillsReloadKey] = useState(0);

  const isEdit = agent !== null;

  // 每次打开时按 agent 重新预填(创建为空态,编辑为当前值)。
  useEffect(() => {
    if (open) {
      setStep('basic');
      setState(stateFromAgent(agent));
      setErrorKey(null);
      setIsSubmitting(false);
      setSelectedSkillIds(new Set());
      setExistingBindings([]);
    }
  }, [open, agent]);

  // M-F3:创建态拉取可复制的现有 agent 列表。
  useEffect(() => {
    if (!open || isEdit) {
      setCopySources([]);
      return;
    }
    let cancelled = false;
    listAgents(client, workspaceId, { limit: 100 })
      .then((res) => {
        if (!cancelled) setCopySources(res.data);
      })
      .catch(() => {
        if (!cancelled) setCopySources([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, isEdit, client, workspaceId]);

  // 技能定义与安装记录是两层实体:只有已安装且未禁用的技能才可绑定。
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setSkillsLoading(true);
    setSkillsLoadError(false);
    Promise.all([
      listSkills(client, workspaceId, { limit: 100 }),
      listInstallations(client, workspaceId, { scope: 'workspace', limit: 100 }),
      agent === null
        ? Promise.resolve({ data: [] as AgentSkillRow[], nextCursor: null })
        : listAgentSkills(client, workspaceId, agent.id, { limit: 100 }),
    ])
      .then(([skills, installations, bindings]) => {
        if (cancelled) return;
        const installationBySkill = new Map<string, SkillInstallation>();
        for (const installation of installations.data) {
          if (
            installation.install_status !== 'disabled' &&
            !installationBySkill.has(installation.skill_id)
          ) {
            installationBySkill.set(installation.skill_id, installation);
          }
        }
        setSkillChoices(
          skills.data.flatMap((skill) => {
            const installation = installationBySkill.get(skill.id);
            return installation === undefined ? [] : [{ skill, installation }];
          }),
        );
        setExistingBindings(bindings.data);
        if (agent !== null) {
          setSelectedSkillIds(new Set(bindings.data.map((binding) => binding.skill.id)));
        }
      })
      .catch(() => {
        if (!cancelled) setSkillsLoadError(true);
      })
      .finally(() => {
        if (!cancelled) setSkillsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agent, client, open, skillsReloadKey, workspaceId]);

  const applyTemplate = (key: string): void => {
    const tpl = TEMPLATES[key];
    if (tpl === undefined) return;
    patch(tpl);
  };

  const applyCopyFrom = async (sourceId: string): Promise<void> => {
    if (sourceId === '') return;
    try {
      const detail = await getAgent(client, workspaceId, sourceId);
      patch({
        name: detail.name,
        avatarUrl: detail.avatar_url ?? '',
        roleTag: detail.role_tag ?? '',
        bio: detail.bio ?? '',
        systemInstructions: detail.system_instructions ?? '',
        modelTier: detail.model_config.model_tier ?? 'balanced',
        model: detail.model_config.model ?? '',
        temperature: String(detail.model_config.temperature ?? 0.2),
        topP: String(detail.model_config.top_p ?? 1),
        maxTokens: String(detail.model_config.max_tokens ?? 8192),
        reasoningEffort: detail.model_config.reasoning_effort ?? 'medium',
        preset: detail.model_config.preset ?? 'strict_engineering',
        visibility: detail.visibility,
        triggerOnAssign: detail.trigger_on_assign,
      });
      const sourceBindings = await listAgentSkills(client, workspaceId, sourceId, { limit: 100 });
      setSelectedSkillIds(new Set(sourceBindings.data.map((binding) => binding.skill.id)));
    } catch (err) {
      addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const patch = useCallback((partial: Partial<WizardState>): void => {
    setState((current) => ({ ...current, ...partial }));
    setErrorKey(null);
  }, []);

  const nameValid = state.name.trim().length >= 1 && state.name.trim().length <= NAME_MAX;
  const temperature = Number(state.temperature);
  const temperatureValid =
    state.temperature !== '' &&
    Number.isFinite(temperature) &&
    temperature >= TEMPERATURE_MIN &&
    temperature <= TEMPERATURE_MAX;
  const maxTokens = Number(state.maxTokens);
  const maxTokensValid = state.maxTokens !== '' && Number.isInteger(maxTokens) && maxTokens >= 1;
  const topPNum = Number(state.topP);
  const topPValid = state.topP !== '' && Number.isFinite(topPNum) && topPNum >= 0 && topPNum <= 1;
  const avatarValid = state.avatarUrl === '' || state.avatarUrl.startsWith('https://');

  const stepIndex = STEP_ORDER.indexOf(step);

  const goTo = (target: WizardStep): void => {
    setErrorKey(null);
    setStep(target);
  };

  const applyPreset = (preset: string): void => {
    const values = PRESETS[preset];
    if (values === undefined) return;
    patch({
      preset,
      modelTier: values.model_tier,
      temperature: String(values.temperature),
      maxTokens: String(values.max_tokens),
      reasoningEffort: values.reasoning_effort,
    });
  };

  const buildModelConfig = (): AgentModelConfig => ({
    model: state.model === '' ? undefined : state.model,
    model_tier: state.modelTier,
    temperature,
    top_p: Number(state.topP),
    max_tokens: maxTokens,
    reasoning_effort: state.reasoningEffort,
    preset: state.preset,
  });

  const selectedTools = useMemo(
    () =>
      effectiveGrants(
        skillChoices
          .filter((choice) => selectedSkillIds.has(choice.skill.id))
          .flatMap((choice) => choice.installation.granted_capabilities),
      ),
    [selectedSkillIds, skillChoices],
  );

  const toggleSkill = (skillId: string, checked: boolean): void => {
    setSelectedSkillIds((current) => {
      const next = new Set(current);
      if (checked) next.add(skillId);
      else next.delete(skillId);
      return next;
    });
  };

  const reconcileSkillBindings = async (agentId: string): Promise<boolean> => {
    // A load failure must never be interpreted as "remove every binding".
    if (skillsLoading || skillsLoadError) return true;
    const currentSkillIds = new Set(existingBindings.map((binding) => binding.skill.id));
    const operations: Promise<unknown>[] = [];
    for (const binding of existingBindings) {
      if (!selectedSkillIds.has(binding.skill.id)) {
        operations.push(unbindSkill(client, workspaceId, agentId, binding.binding_id));
      }
    }
    for (const choice of skillChoices) {
      if (selectedSkillIds.has(choice.skill.id) && !currentSkillIds.has(choice.skill.id)) {
        operations.push(
          bindSkill(client, workspaceId, agentId, {
            skill_installation_id: choice.installation.id,
          }),
        );
      }
    }
    const results = await Promise.allSettled(operations);
    return results.every((result) => result.status === 'fulfilled');
  };

  const finish = async (): Promise<void> => {
    setIsSubmitting(true);
    setErrorKey(null);
    try {
      if (isEdit && agent !== null) {
        // 编辑:profile PATCH + 配置 PATCH(生成新版本)。
        await updateAgent(client, workspaceId, agent.id, {
          name: state.name.trim(),
          avatar_url: state.avatarUrl === '' ? null : state.avatarUrl,
          role_tag: state.roleTag === '' ? null : state.roleTag,
          bio: state.bio === '' ? null : state.bio,
          visibility: state.visibility,
          trigger_on_assign: state.triggerOnAssign,
        });
        await updateAgentConfig(client, workspaceId, agent.id, {
          model_config: buildModelConfig(),
          system_instructions: state.systemInstructions === '' ? null : state.systemInstructions,
        });
        const skillsSaved = await reconcileSkillBindings(agent.id);
        addToast(t('agents.toast.updated'), { tone: 'success', closeLabel: t('common.close') });
        if (!skillsSaved) {
          addToast(t('skills.bindFailed'), { tone: 'danger', closeLabel: t('common.close') });
        }
        onSaved(agent.id);
      } else {
        const created = await createAgent(client, workspaceId, {
          name: state.name.trim(),
          avatar_url: state.avatarUrl === '' ? null : state.avatarUrl,
          role_tag: state.roleTag === '' ? null : state.roleTag,
          bio: state.bio === '' ? null : state.bio,
          visibility: state.visibility,
          trigger_on_assign: state.triggerOnAssign,
          system_instructions: state.systemInstructions === '' ? null : state.systemInstructions,
          model_config: buildModelConfig(),
        });
        const skillsSaved = await reconcileSkillBindings(created.id);
        addToast(t('agents.toast.created'), { tone: 'success', closeLabel: t('common.close') });
        if (!skillsSaved) {
          addToast(t('skills.bindFailed'), { tone: 'danger', closeLabel: t('common.close') });
        }
        onSaved(created.id);
      }
      onClose();
    } catch (err) {
      setIsSubmitting(false);
      setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown');
    }
  };

  const title = isEdit ? t('agents.wizard.titleEdit') : t('agents.wizard.titleCreate');

  return (
    <Dialog open={open} onClose={onClose} title={title} closeLabel={t('a11y.closeDialog')}>
      <div className="mesh-agents-wizard">
        <ol className="mesh-agents-wizard__steps" aria-label={t('agents.wizard.progress')}>
          {STEP_ORDER.map((key, index) => (
            <li
              key={key}
              className={
                index === stepIndex
                  ? 'mesh-agents-wizard__step mesh-agents-wizard__step--active'
                  : 'mesh-agents-wizard__step'
              }
              aria-current={index === stepIndex ? 'step' : undefined}
              data-testid={`agent-wizard-step-${key}`}
            >
              {t(`agents.wizard.step.${key}`)}
            </li>
          ))}
        </ol>

        {step === 'basic' ? (
          <div className="mesh-agents-wizard__body" data-testid="agent-wizard-basic">
            <Input
              label={t('agents.field.name')}
              value={state.name}
              data-testid="agent-wizard-name"
              error={
                state.name.trim().length > NAME_MAX ? t('agents.validation.nameTooLong') : undefined
              }
              onChange={(event) => patch({ name: event.target.value })}
            />
            <Input
              label={t('agents.field.avatarUrl')}
              value={state.avatarUrl}
              hint={t('agents.field.avatarHint')}
              data-testid="agent-wizard-avatar"
              error={!avatarValid ? t('agents.validation.httpsOnly') : undefined}
              onChange={(event) => patch({ avatarUrl: event.target.value })}
            />
            <Input
              label={t('agents.field.roleTag')}
              value={state.roleTag}
              data-testid="agent-wizard-role-tag"
              onChange={(event) => patch({ roleTag: event.target.value })}
            />
            {/* M-F3:从模板 / 从现有 agent 复制(§4.4 快捷入口)。 */}
            {!isEdit ? (
              <>
                <Select
                  label={t('agents.wizard.template')}
                  value=""
                  data-testid="agent-wizard-template"
                  onChange={(event) => applyTemplate(event.target.value)}
                >
                  <option value="">{t('agents.preset.none')}</option>
                  <option value="test">{t('agents.wizard.templateTest')}</option>
                  <option value="docs">{t('agents.wizard.templateDocs')}</option>
                  <option value="ops">{t('agents.wizard.templateOps')}</option>
                </Select>
                <Select
                  label={t('agents.wizard.copyFrom')}
                  value=""
                  data-testid="agent-wizard-copy-from"
                  onChange={(event) => void applyCopyFrom(event.target.value)}
                >
                  <option value="">{t('agents.wizard.copyNone')}</option>
                  {copySources.map((src) => (
                    <option key={src.id} value={src.id}>
                      {src.display_name}
                    </option>
                  ))}
                </Select>
              </>
            ) : null}
            <label className="mesh-agents-wizard__label" htmlFor="agent-wizard-bio">
              {t('agents.field.bio')}
            </label>
            <textarea
              id="agent-wizard-bio"
              className="mesh-agents-wizard__textarea"
              data-testid="agent-wizard-bio"
              rows={3}
              value={state.bio}
              onChange={(event) => patch({ bio: event.target.value })}
            />
          </div>
        ) : null}

        {step === 'model' ? (
          <div className="mesh-agents-wizard__body" data-testid="agent-wizard-model">
            <fieldset className="mesh-agents-wizard__fieldset">
              <legend>{t('agents.field.modelTier')}</legend>
              {MODEL_TIER_ORDER.map((tier) => (
                <label key={tier} className="mesh-agents-wizard__radio">
                  <input
                    type="radio"
                    name="agent-model-tier"
                    value={tier}
                    checked={state.modelTier === tier}
                    data-testid={`agent-wizard-tier-${tier}`}
                    onChange={() => patch({ modelTier: tier })}
                  />
                  {t(`agents.tier.${tier}`)}
                </label>
              ))}
            </fieldset>
            <Select
              label={t('agents.field.model')}
              value={state.model}
              data-testid="agent-wizard-model-select"
              onChange={(event) => patch({ model: event.target.value })}
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
              value={state.preset}
              data-testid="agent-wizard-preset"
              onChange={(event) => applyPreset(event.target.value)}
            >
              <option value="strict_engineering">{t('agents.preset.strict_engineering')}</option>
              <option value="creative_draft">{t('agents.preset.creative_draft')}</option>
              <option value="fast_triage">{t('agents.preset.fast_triage')}</option>
            </Select>
            <label className="mesh-agents-wizard__label" htmlFor="agent-wizard-instructions">
              {t('agents.field.systemInstructions')}
            </label>
            <textarea
              id="agent-wizard-instructions"
              className="mesh-agents-wizard__textarea"
              data-testid="agent-wizard-instructions"
              rows={5}
              value={state.systemInstructions}
              onChange={(event) => patch({ systemInstructions: event.target.value })}
            />
            <Input
              label={t('agents.field.temperature')}
              value={state.temperature}
              data-testid="agent-wizard-temperature"
              error={!temperatureValid ? t('agents.validation.temperatureRange') : undefined}
              onChange={(event) => patch({ temperature: event.target.value })}
            />
            <Input
              label={t('agents.field.topP')}
              value={state.topP}
              data-testid="agent-wizard-top-p"
              error={!topPValid ? t('agents.validation.topPRange') : undefined}
              onChange={(event) => patch({ topP: event.target.value })}
            />
            <Input
              label={t('agents.field.maxTokens')}
              value={state.maxTokens}
              data-testid="agent-wizard-max-tokens"
              error={!maxTokensValid ? t('agents.validation.maxTokensMin') : undefined}
              onChange={(event) => patch({ maxTokens: event.target.value })}
            />
            <Select
              label={t('agents.field.reasoningEffort')}
              value={state.reasoningEffort}
              data-testid="agent-wizard-effort"
              onChange={(event) =>
                patch({ reasoningEffort: event.target.value as 'low' | 'medium' | 'high' })
              }
            >
              <option value="low">{t('agents.effort.low')}</option>
              <option value="medium">{t('agents.effort.medium')}</option>
              <option value="high">{t('agents.effort.high')}</option>
            </Select>
          </div>
        ) : null}

        {step === 'skills' ? (
          <div className="mesh-agents-wizard__body" data-testid="agent-wizard-skills">
            <p className="mesh-agents-wizard__skills-hint">{t('agents.wizard.skillsOptional')}</p>
            {skillsLoading ? <p role="status">{t('state.loading')}</p> : null}
            {skillsLoadError ? (
              <div className="mesh-agents-wizard__skills-error" role="alert">
                <p>{t('skills.loadError')}</p>
                <Button
                  variant="secondary"
                  onClick={() => setSkillsReloadKey((value) => value + 1)}
                >
                  {t('common.retry')}
                </Button>
              </div>
            ) : null}
            {!skillsLoading && !skillsLoadError && skillChoices.length === 0 ? (
              <p>{t('skills.agentEmptyDescription')}</p>
            ) : null}
            {!skillsLoading && !skillsLoadError && skillChoices.length > 0 ? (
              <fieldset className="mesh-agents-wizard__fieldset mesh-agents-wizard__skills-list">
                <legend>{t('skills.agentSkillsTitle')}</legend>
                {skillChoices.map((choice) => (
                  <label key={choice.skill.id} className="mesh-agents-wizard__skill-choice">
                    <input
                      type="checkbox"
                      checked={selectedSkillIds.has(choice.skill.id)}
                      data-testid={`agent-wizard-skill-${choice.skill.id}`}
                      onChange={(event) => toggleSkill(choice.skill.id, event.target.checked)}
                    />
                    <span>
                      <strong>{choice.skill.name}</strong>
                      <span className="mesh-agents-wizard__skill-meta">
                        {choice.skill.source_type === null
                          ? null
                          : t(`skills.source.${choice.skill.source_type}`)}
                        {' · '}
                        {t(`skills.installStatus.${choice.installation.install_status}`)}
                      </span>
                    </span>
                  </label>
                ))}
              </fieldset>
            ) : null}
            {selectedTools.length > 0 ? (
              <section className="mesh-agents-wizard__tools" aria-labelledby="wizard-tools-title">
                <h3 id="wizard-tools-title">{t('skills.agentToolsTitle')}</h3>
                <ul>
                  {selectedTools.map((tool) => (
                    <li
                      key={tool.capability}
                      className={`mesh-agents-wizard__tool mesh-agents-wizard__tool--${permissionTone(tool.permission)}`}
                      data-testid={`agent-wizard-tool-${tool.capability}`}
                    >
                      <code>{tool.capability}</code>
                      <span>{t(`skills.permission.${tool.permission}`)}</span>
                    </li>
                  ))}
                </ul>
                <p className="mesh-agents-wizard__skills-hint">{t('skills.toolReadOnlyHint')}</p>
              </section>
            ) : null}
          </div>
        ) : null}

        {step === 'visibility' ? (
          <div className="mesh-agents-wizard__body" data-testid="agent-wizard-visibility">
            <fieldset className="mesh-agents-wizard__fieldset">
              <legend>{t('agents.field.visibility')}</legend>
              <label className="mesh-agents-wizard__radio">
                <input
                  type="radio"
                  name="agent-visibility"
                  value="workspace"
                  checked={state.visibility === 'workspace'}
                  data-testid="agent-wizard-visibility-workspace"
                  onChange={() => patch({ visibility: 'workspace' })}
                />
                {t('agents.visibility.workspace')}
              </label>
              <label className="mesh-agents-wizard__radio">
                <input
                  type="radio"
                  name="agent-visibility"
                  value="private"
                  checked={state.visibility === 'private'}
                  data-testid="agent-wizard-visibility-private"
                  onChange={() => patch({ visibility: 'private' })}
                />
                {t('agents.visibility.private')}
              </label>
            </fieldset>
            <label className="mesh-agents-wizard__radio">
              <input
                type="checkbox"
                checked={state.triggerOnAssign}
                data-testid="agent-wizard-trigger-on-assign"
                onChange={(event) => patch({ triggerOnAssign: event.target.checked })}
              />
              {t('agents.field.triggerOnAssign')}
            </label>
          </div>
        ) : null}

        {errorKey !== null ? (
          <p role="alert" className="mesh-agents-wizard__error" data-testid="agent-wizard-error">
            {t(errorKey)}
          </p>
        ) : null}

        <div className="mesh-agents-wizard__footer">
          {stepIndex > 0 ? (
            <Button
              variant="secondary"
              data-testid="agent-wizard-back"
              onClick={() => goTo(STEP_ORDER[stepIndex - 1])}
            >
              {t('agents.wizard.back')}
            </Button>
          ) : null}
          {stepIndex < STEP_ORDER.length - 1 ? (
            <Button
              data-testid="agent-wizard-next"
              disabled={
                (step === 'basic' && (!nameValid || !avatarValid)) ||
                (step === 'model' && (!temperatureValid || !topPValid || !maxTokensValid))
              }
              onClick={() => goTo(STEP_ORDER[stepIndex + 1])}
            >
              {t('agents.wizard.next')}
            </Button>
          ) : (
            <Button
              data-testid="agent-wizard-finish"
              isLoading={isSubmitting}
              onClick={() => void finish()}
            >
              {t('agents.wizard.finish')}
            </Button>
          )}
        </div>
      </div>
    </Dialog>
  );
}
