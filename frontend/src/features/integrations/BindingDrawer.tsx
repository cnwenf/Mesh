/**
 * 绑定配置(integrations.md §4.2):绑定表(external_ref / 作用域 / 目标 agent /
 * 状态)+ 绑定抽屉(外部身份 + 作用域 workspace/project + 项目下拉 + 匹配规则
 * 表单 + 目标 agent)。匹配规则按集成 kind 取字段:IM → @触发/关键词;VCS → 事件
 * 勾选/分支模式。目标 agent 留空 = 仅审计不触发(显式提示,README §6.9)。
 * `(provider, provider_tenant_key, external_ref)` 全局唯一冲突 → 409
 * binding_conflict toast(§2.3)。
 */
import { useCallback, useEffect, useState } from 'react';
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
import { listMembers } from '../members/api';
import type { MemberSummary } from '../members/types';
import type { ProjectSummary } from '../projects/types';
import { createBinding, deleteBinding, listBindings } from './api';
import { BINDING_STATUS_TONE } from './format';
import { listAllVisibleProjects } from './projectVisibility';
import type { Binding, IntegrationKind, MatchConfig } from './types';
import './integrations.css';

const SCOPE_WORKSPACE = 'workspace';
const SCOPE_PROJECT = 'project';
const AGENT_NONE = '';

const IM_TRIGGERS: ReadonlyArray<'mention' | 'direct_message' | 'keyword'> = [
  'mention',
  'direct_message',
  'keyword',
];

const VCS_EVENT_OPTIONS: ReadonlyArray<string> = [
  'pull_request',
  'merge_request',
  'push',
  'commit_comment',
];

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

function isImKind(kind: IntegrationKind): boolean {
  return kind.startsWith('im_');
}

function isVcsKind(kind: IntegrationKind): boolean {
  return kind.startsWith('vcs_');
}

function splitKeywords(value: string): string[] {
  return value
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry !== '');
}

/** 名册行的 `id` 是 members.id；绑定外键需要 profile 内的 agents.id。 */
function agentEntityId(agent: MemberSummary): string {
  return agent.profile?.id ?? agent.id;
}

export interface BindingDrawerProps {
  readonly workspaceId: string;
  readonly integrationId: string;
  readonly integrationKind: IntegrationKind;
  readonly isAdmin: boolean;
  readonly reloadKey?: number;
}

export function BindingDrawer(props: BindingDrawerProps): React.JSX.Element {
  const { workspaceId, integrationId, integrationKind, isAdmin, reloadKey = 0 } = props;
  const t = useT();
  const toast = useToast();

  const [bindings, setBindings] = useState<Binding[] | null>(null);
  const [agents, setAgents] = useState<MemberSummary[]>([]);
  const [projects, setProjects] = useState<ReadonlyArray<ProjectSummary>>([]);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [localReloadKey, setLocalReloadKey] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [externalRef, setExternalRef] = useState('');
  const [scope, setScope] = useState<'workspace' | 'project'>(SCOPE_WORKSPACE);
  const [projectId, setProjectId] = useState('');
  const [boundAgentId, setBoundAgentId] = useState(AGENT_NONE);
  const [triggerOn, setTriggerOn] = useState<ReadonlyArray<string>>([]);
  const [keywordInclude, setKeywordInclude] = useState('');
  const [keywordExclude, setKeywordExclude] = useState('');
  const [vcsEvents, setVcsEvents] = useState<ReadonlyArray<string>>([]);
  const [branchPattern, setBranchPattern] = useState('');

  useEffect(() => {
    let cancelled = false;
    const client = newClient();
    void (async () => {
      try {
        const [bindingListing, agentListing, visibleProjects] = await Promise.all([
          listBindings(client, workspaceId, integrationId),
          listMembers(client, workspaceId, { memberType: 'agent', limit: 100 }),
          listAllVisibleProjects(client, workspaceId),
        ]);
        if (cancelled) return;
        const visibleProjectIds = new Set(visibleProjects.map((project) => project.id));
        setBindings(
          isAdmin
            ? bindingListing.data
            : bindingListing.data.filter(
                (binding) =>
                  binding.scope === SCOPE_WORKSPACE ||
                  (binding.project_id !== null && visibleProjectIds.has(binding.project_id)),
              ),
        );
        setAgents(agentListing.data);
        setProjects(visibleProjects.filter((project) => !project.archived));
        setErrorKey(null);
      } catch (error) {
        if (cancelled) return;
        setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
        setBindings(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId, integrationId, isAdmin, reloadKey, localReloadKey]);

  const resetDrawer = useCallback((): void => {
    setExternalRef('');
    setScope(SCOPE_WORKSPACE);
    setProjectId('');
    setBoundAgentId(AGENT_NONE);
    setTriggerOn([]);
    setKeywordInclude('');
    setKeywordExclude('');
    setVcsEvents([]);
    setBranchPattern('');
  }, []);

  const toggleEntry = useCallback(
    (list: ReadonlyArray<string>, value: string, setter: (next: string[]) => void): void => {
      setter(list.includes(value) ? list.filter((entry) => entry !== value) : [...list, value]);
    },
    [],
  );

  const buildMatchConfig = useCallback((): MatchConfig => {
    const config: Record<string, unknown> = {};
    if (isImKind(integrationKind)) {
      if (triggerOn.length > 0) {
        config.trigger_on = [...triggerOn];
      }
      const include = splitKeywords(keywordInclude);
      const exclude = splitKeywords(keywordExclude);
      if (include.length > 0) config.keyword_include = include;
      if (exclude.length > 0) config.keyword_exclude = exclude;
    }
    if (isVcsKind(integrationKind)) {
      if (vcsEvents.length > 0) config.vcs_events = [...vcsEvents];
      if (branchPattern.trim() !== '') config.branch_pattern = branchPattern.trim();
    }
    return config as MatchConfig;
  }, [integrationKind, triggerOn, keywordInclude, keywordExclude, vcsEvents, branchPattern]);

  const submitBinding = useCallback(async (): Promise<void> => {
    if (externalRef.trim() === '') return;
    if (scope === SCOPE_PROJECT && projectId === '') return;
    setBusy(true);
    try {
      await createBinding(newClient(), workspaceId, integrationId, {
        external_ref: externalRef.trim(),
        scope,
        project_id: scope === SCOPE_PROJECT ? projectId : undefined,
        match_config: buildMatchConfig(),
        bound_agent_id: boundAgentId === AGENT_NONE ? null : boundAgentId,
      });
      toast.addToast(t('integrations.bindings.createdToast'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setDrawerOpen(false);
      resetDrawer();
      // 触发重拉以反映新绑定。
      setLocalReloadKey((key) => key + 1);
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setBusy(false);
    }
  }, [
    externalRef,
    scope,
    projectId,
    boundAgentId,
    workspaceId,
    integrationId,
    buildMatchConfig,
    resetDrawer,
    toast,
    t,
  ]);

  const removeBinding = useCallback(
    async (bindingId: string): Promise<void> => {
      try {
        await deleteBinding(newClient(), workspaceId, bindingId);
        toast.addToast(t('integrations.bindings.deletedToast'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
        setBindings((prev) =>
          prev === null ? prev : prev.filter((binding) => binding.id !== bindingId),
        );
      } catch (error) {
        toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      }
    },
    [workspaceId, toast, t],
  );

  const agentName = useCallback(
    (agentId: string | null): string => {
      if (agentId === null) return t('integrations.bindings.auditOnly');
      return agents.find((agent) => agentEntityId(agent) === agentId)?.display_name ?? agentId;
    },
    [agents, t],
  );

  const showImFields = isImKind(integrationKind);
  const showVcsFields = isVcsKind(integrationKind);
  const canSubmit = externalRef.trim() !== '' && (scope === SCOPE_WORKSPACE || projectId !== '');

  return (
    <div data-testid="binding-drawer">
      <div className="mesh-integrations__header">
        <h3>{t('integrations.bindings.title')}</h3>
        {isAdmin && (
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              resetDrawer();
              setDrawerOpen(true);
            }}
            data-testid="binding-create"
          >
            {t('integrations.bindings.create')}
          </Button>
        )}
      </div>

      {errorKey !== null && (
        <ErrorState
          title={t(errorKey)}
          retryLabel={t('common.retry')}
          onRetry={() => setLocalReloadKey((key) => key + 1)}
        />
      )}
      {bindings === null && errorKey === null && (
        <Skeleton loadingLabel={t('integrations.loading')} />
      )}
      {bindings !== null && bindings.length === 0 && errorKey === null && (
        <EmptyState title={t('integrations.bindings.empty')} description="" />
      )}
      {bindings !== null && bindings.length > 0 && (
        <table className="mesh-integrations__table" data-testid="binding-table">
          <caption className="sr-only">{t('integrations.bindings.title')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('integrations.bindings.externalRef')}</th>
              <th scope="col">{t('integrations.bindings.scope')}</th>
              <th scope="col">{t('integrations.bindings.agent')}</th>
              <th scope="col">{t('integrations.bindings.status')}</th>
              <th scope="col">{t('integrations.columns.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {bindings.map((binding) => (
              <tr key={binding.id} data-testid={`binding-row-${binding.id}`}>
                <td>{binding.external_ref}</td>
                <td>
                  {binding.scope === SCOPE_PROJECT
                    ? `${t('integrations.bindings.scopeProject')}`
                    : t('integrations.bindings.scopeWorkspace')}
                </td>
                <td data-testid={`binding-agent-${binding.id}`}>
                  {agentName(binding.bound_agent_id)}
                </td>
                <td>
                  <StatusDot
                    tone={BINDING_STATUS_TONE[binding.status]}
                    label={t(`integrations.status.${binding.status}`)}
                  />
                </td>
                <td>
                  {isAdmin && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void removeBinding(binding.id)}
                      data-testid={`binding-delete-${binding.id}`}
                    >
                      {t('integrations.actions.delete')}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Dialog
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={t('integrations.bindings.create')}
        closeLabel={t('common.close')}
      >
        <Input
          label={t('integrations.bindings.externalRefLabel')}
          value={externalRef}
          onChange={(event) => setExternalRef(event.target.value)}
          hint={t('integrations.bindings.externalRefHint')}
          data-testid="binding-external-ref"
        />
        <Select
          label={t('integrations.bindings.scopeLabel')}
          value={scope}
          onChange={(event) =>
            setScope(event.target.value === SCOPE_PROJECT ? SCOPE_PROJECT : SCOPE_WORKSPACE)
          }
          data-testid="binding-scope"
        >
          <option value={SCOPE_WORKSPACE}>{t('integrations.bindings.scopeWorkspace')}</option>
          <option value={SCOPE_PROJECT}>{t('integrations.bindings.scopeProject')}</option>
        </Select>
        {scope === SCOPE_PROJECT && (
          <Select
            label={t('integrations.bindings.projectLabel')}
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
            data-testid="binding-project"
          >
            <option value="">{t('integrations.bindings.projectPlaceholder')}</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </Select>
        )}

        {showImFields && (
          <div className="mesh-integrations__field">
            <span>{t('integrations.bindings.triggerOn')}</span>
            <div className="mesh-integrations__checks">
              {IM_TRIGGERS.map((trigger) => (
                <label key={trigger}>
                  <input
                    type="checkbox"
                    checked={triggerOn.includes(trigger)}
                    onChange={() => toggleEntry(triggerOn, trigger, setTriggerOn)}
                    data-testid={`binding-trigger-${trigger}`}
                  />{' '}
                  {t(`integrations.bindings.trigger.${trigger}`)}
                </label>
              ))}
            </div>
            <Input
              label={t('integrations.bindings.keywordInclude')}
              value={keywordInclude}
              onChange={(event) => setKeywordInclude(event.target.value)}
              hint={t('integrations.bindings.keywordHint')}
              data-testid="binding-keyword-include"
            />
            <Input
              label={t('integrations.bindings.keywordExclude')}
              value={keywordExclude}
              onChange={(event) => setKeywordExclude(event.target.value)}
              data-testid="binding-keyword-exclude"
            />
          </div>
        )}

        {showVcsFields && (
          <div className="mesh-integrations__field">
            <span>{t('integrations.bindings.vcsEvents')}</span>
            <div className="mesh-integrations__checks">
              {VCS_EVENT_OPTIONS.map((eventName) => (
                <label key={eventName}>
                  <input
                    type="checkbox"
                    checked={vcsEvents.includes(eventName)}
                    onChange={() => toggleEntry(vcsEvents, eventName, setVcsEvents)}
                    data-testid={`binding-vcs-event-${eventName}`}
                  />{' '}
                  {eventName}
                </label>
              ))}
            </div>
            <Input
              label={t('integrations.bindings.branchPattern')}
              value={branchPattern}
              onChange={(event) => setBranchPattern(event.target.value)}
              hint={t('integrations.bindings.branchHint')}
              data-testid="binding-branch-pattern"
            />
          </div>
        )}

        <Select
          label={t('integrations.bindings.agentLabel')}
          value={boundAgentId}
          onChange={(event) => setBoundAgentId(event.target.value)}
          data-testid="binding-agent-select"
        >
          <option value={AGENT_NONE}>{t('integrations.bindings.auditOnly')}</option>
          {agents.map((agent) => (
            <option key={agent.id} value={agentEntityId(agent)}>
              {agent.display_name}
            </option>
          ))}
        </Select>
        <p className="mesh-integrations__muted" data-testid="binding-audit-hint">
          {t('integrations.bindings.auditHint')}
        </p>

        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setDrawerOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            isLoading={busy}
            disabled={!canSubmit}
            onClick={() => void submitBinding()}
            data-testid="binding-submit"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
