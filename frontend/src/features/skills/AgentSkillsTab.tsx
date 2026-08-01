/**
 * Agent 详情「技能与工具」真实双列管理面(agent.md §4.3 / skill.md §4.2):
 * 左列管理 agent_skills 绑定;右列通过 Agent Tools 薄封装管理
 * skill_installations.granted_capabilities，capability key 是稳定标识。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import {
  Button,
  EmptyState,
  ErrorState,
  Icon,
  Input,
  Select,
  Skeleton,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import {
  bindAgentTool,
  bindSkill,
  listAgentSkills,
  listAgentTools,
  listInstallations,
  unbindAgentTool,
  unbindSkill,
  updateAgentTool,
  updateBinding,
} from './api';
import { permissionTone } from './capabilities';
import type {
  AgentSkillRow,
  AgentToolGrant,
  CapabilityPermission,
  SkillInstallation,
} from './types';

export function AgentSkillsTab({
  workspaceId,
  agentId,
  canManage,
  reloadSignal = 0,
}: {
  workspaceId: string;
  agentId: string;
  canManage: boolean;
  reloadSignal?: number;
}): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [rows, setRows] = useState<AgentSkillRow[]>([]);
  const [installations, setInstallations] = useState<SkillInstallation[]>([]);
  const [tools, setTools] = useState<AgentToolGrant[]>([]);
  const [pick, setPick] = useState('');
  const [toolCapability, setToolCapability] = useState('');
  const [toolPermission, setToolPermission] = useState<CapabilityPermission>('confirm_required');
  const [reloadKey, setReloadKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [mutationPending, setMutationPending] = useState(false);
  const mutationPendingRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    Promise.all([
      listAgentSkills(client, workspaceId, agentId, { limit: 100 }),
      listInstallations(client, workspaceId, { scope: 'workspace', limit: 100 }),
      listAgentTools(client, agentId),
    ])
      .then(([bound, available, grantedTools]) => {
        if (cancelled) return;
        setRows(bound.data);
        setInstallations(available.data);
        setTools(grantedTools.data);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, agentId, reloadKey, reloadSignal]);

  const refresh = useCallback(() => setReloadKey((k) => k + 1), []);

  const runMutation = useCallback(
    async (operation: () => Promise<void>, onError: () => void): Promise<void> => {
      if (mutationPendingRef.current) return;
      mutationPendingRef.current = true;
      setMutationPending(true);
      try {
        await operation();
      } catch {
        onError();
      } finally {
        mutationPendingRef.current = false;
        setMutationPending(false);
      }
    },
    [],
  );

  const onBind = useCallback(async () => {
    if (pick === '') return;
    await runMutation(
      async () => {
        await bindSkill(client, workspaceId, agentId, { skill_installation_id: pick });
        setPick('');
        refresh();
      },
      () => {
        toast.addToast(t('skills.bindFailed'), {
          tone: 'danger',
          closeLabel: t('a11y.closeDialog'),
        });
      },
    );
  }, [client, workspaceId, agentId, pick, refresh, runMutation, t, toast]);

  const onBindTool = useCallback(async () => {
    const capability = toolCapability.trim();
    if (capability === '') return;
    await runMutation(
      async () => {
        await bindAgentTool(client, agentId, { capability, permission: toolPermission });
        setToolCapability('');
        setToolPermission('confirm_required');
        refresh();
      },
      () => {
        toast.addToast(t('skills.toolSaveFailed'), {
          tone: 'danger',
          closeLabel: t('a11y.closeDialog'),
        });
      },
    );
  }, [agentId, client, refresh, runMutation, t, toast, toolCapability, toolPermission]);

  const bindable = installations.filter(
    (installation) =>
      installation.install_status !== 'disabled' &&
      !rows.some((row) => row.skill.id === installation.skill_id),
  );

  if (loading) {
    return (
      <section className="mesh-agents-detail__panel" data-testid="agent-panel-skills">
        <Skeleton loadingLabel={t('state.loading')} />
      </section>
    );
  }

  if (loadError) {
    return (
      <section className="mesh-agents-detail__panel" data-testid="agent-panel-skills">
        <ErrorState
          title={t('state.errorTitle')}
          description={t('skills.loadError')}
          retryLabel={t('common.retry')}
          onRetry={refresh}
        />
      </section>
    );
  }

  return (
    <section
      className="mesh-agents-detail__panel"
      data-testid="agent-panel-skills"
      aria-busy={mutationPending}
    >
      <div className="mesh-skills-agent-grid">
        <section className="mesh-skills-agent-grid__column" data-testid="agent-skills-column">
          <h2 className="mesh-text-title-3">{t('skills.agentSkillsTitle')}</h2>
          {rows.length === 0 ? (
            <EmptyState
              title={t('skills.agentEmptyTitle')}
              description={t('skills.agentEmptyDescription')}
            />
          ) : (
            <div className="mesh-skills__table-scroll">
              <table className="mesh-skills-detail__versions" data-testid="agent-skills-table">
                <thead>
                  <tr>
                    <th scope="col">{t('skills.agentEnabledCol')}</th>
                    <th scope="col">{t('skills.agentNameCol')}</th>
                    <th scope="col">{t('skills.agentVersionCol')}</th>
                    <th scope="col">{t('skills.agentAutoTriggerCol')}</th>
                    <th scope="col">{t('skills.agentPriorityCol')}</th>
                    <th scope="col">{t('skills.agentActionsCol')}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.binding_id} data-testid={`agent-skill-${row.binding_id}`}>
                      <td>
                        <input
                          type="checkbox"
                          checked={row.enabled}
                          disabled={!canManage || mutationPending}
                          onChange={(event) => {
                            void runMutation(
                              async () => {
                                await updateBinding(client, workspaceId, agentId, row.binding_id, {
                                  enabled: event.target.checked,
                                });
                                refresh();
                              },
                              () => {
                                toast.addToast(t('skills.bindFailed'), {
                                  tone: 'danger',
                                  closeLabel: t('a11y.closeDialog'),
                                });
                              },
                            );
                          }}
                          aria-label={`${t('skills.agentEnabledCol')} ${row.skill.name}`}
                        />
                      </td>
                      <th scope="row">
                        {row.skill.source_type === 'url' ||
                        row.skill.source_type === 'marketplace' ? (
                          <span
                            className="mesh-skills__source-flag"
                            title={t(`skills.source.${row.skill.source_type}`)}
                          >
                            <Icon name="warning" size={16} />
                          </span>
                        ) : null}
                        {row.skill.name}
                      </th>
                      <td>
                        {row.version}
                        {row.install_status === 'updated_available' ? (
                          <span className="mesh-skills__update-flag">
                            <Icon name="cycle" size={16} /> {t('skills.updateAvailable')}
                          </span>
                        ) : null}
                      </td>
                      <td>
                        <input
                          type="checkbox"
                          checked={row.auto_trigger}
                          disabled={!canManage || mutationPending}
                          onChange={(event) => {
                            void runMutation(
                              async () => {
                                await updateBinding(client, workspaceId, agentId, row.binding_id, {
                                  auto_trigger: event.target.checked,
                                });
                                refresh();
                              },
                              () => {
                                toast.addToast(t('skills.bindFailed'), {
                                  tone: 'danger',
                                  closeLabel: t('a11y.closeDialog'),
                                });
                              },
                            );
                          }}
                          aria-label={`${t('skills.agentAutoTriggerCol')} ${row.skill.name}`}
                        />
                      </td>
                      <td>
                        {canManage ? (
                          <input
                            type="number"
                            min={0}
                            max={1000}
                            defaultValue={row.priority}
                            disabled={mutationPending}
                            aria-label={`${t('skills.agentPriorityCol')} ${row.skill.name}`}
                            data-testid={`agent-priority-${row.binding_id}`}
                            onBlur={(event) => {
                              const value = Number(event.target.value);
                              if (Number.isNaN(value) || value === row.priority) return;
                              void runMutation(
                                async () => {
                                  await updateBinding(
                                    client,
                                    workspaceId,
                                    agentId,
                                    row.binding_id,
                                    {
                                      priority: Math.min(1000, Math.max(0, value)),
                                    },
                                  );
                                  refresh();
                                },
                                () => {
                                  toast.addToast(t('skills.bindFailed'), {
                                    tone: 'danger',
                                    closeLabel: t('a11y.closeDialog'),
                                  });
                                },
                              );
                            }}
                          />
                        ) : (
                          row.priority
                        )}
                      </td>
                      <td>
                        {canManage ? (
                          <Button
                            variant="secondary"
                            disabled={mutationPending}
                            aria-label={`${t('skills.unbindButton')} ${row.skill.name}`}
                            onClick={() => {
                              void runMutation(
                                async () => {
                                  await unbindSkill(client, workspaceId, agentId, row.binding_id);
                                  refresh();
                                },
                                () => {
                                  toast.addToast(t('skills.unbindFailed'), {
                                    tone: 'danger',
                                    closeLabel: t('a11y.closeDialog'),
                                  });
                                },
                              );
                            }}
                            data-testid={`agent-unbind-${row.binding_id}`}
                          >
                            {t('skills.unbindButton')}
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {canManage ? (
            <div className="mesh-skills__bind-row" data-testid="agent-skill-bind-row">
              <Select
                label={t('skills.bindPick')}
                value={pick}
                disabled={mutationPending}
                onChange={(event) => setPick(event.target.value)}
              >
                <option value="">{t('skills.bindPickPlaceholder')}</option>
                {bindable.map((installation) => (
                  <option key={installation.id} value={installation.id}>
                    {installation.skill_id.slice(0, 8)} (
                    {t(`skills.installStatus.${installation.install_status}`)})
                  </option>
                ))}
              </Select>
              <Button
                onClick={() => void onBind()}
                disabled={pick === '' || mutationPending}
                data-testid="agent-skill-bind"
              >
                {t('skills.bindButton')}
              </Button>
            </div>
          ) : null}
        </section>

        <section className="mesh-skills-agent-grid__column" data-testid="agent-tools-column">
          <h2 className="mesh-text-title-3">{t('skills.agentToolsTitle')}</h2>
          {tools.length === 0 ? (
            <EmptyState
              title={t('skills.agentToolsEmptyTitle')}
              description={t('skills.agentToolsEmptyDescription')}
            />
          ) : (
            <div className="mesh-skills__table-scroll">
              <table
                className="mesh-skills-detail__versions mesh-skills-agent-grid__tools-table"
                data-testid="agent-tools-table"
              >
                <thead>
                  <tr>
                    <th scope="col">{t('skills.toolEnabledCol')}</th>
                    <th scope="col">{t('skills.toolCapabilityCol')}</th>
                    <th scope="col">{t('skills.toolPermissionCol')}</th>
                    <th scope="col">{t('skills.agentActionsCol')}</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.map((tool) => (
                    <tr
                      key={tool.capability}
                      className={`mesh-skills__permission-row mesh-skills__permission-row--${permissionTone(tool.permission)}`}
                      data-testid={`agent-tool-${tool.capability}`}
                    >
                      <td>
                        <input
                          type="checkbox"
                          checked={tool.enabled}
                          disabled={!canManage || mutationPending}
                          data-testid={`agent-tool-enabled-${tool.capability}`}
                          onChange={(event) => {
                            void runMutation(
                              async () => {
                                await updateAgentTool(client, agentId, tool.capability, {
                                  enabled: event.target.checked,
                                });
                                refresh();
                              },
                              () => {
                                toast.addToast(t('skills.toolSaveFailed'), {
                                  tone: 'danger',
                                  closeLabel: t('a11y.closeDialog'),
                                });
                              },
                            );
                          }}
                          aria-label={`${t('skills.toolEnabledCol')} ${tool.capability}`}
                        />
                      </td>
                      <th scope="row">
                        <code>{tool.capability}</code>
                      </th>
                      <td>
                        <Select
                          label={t('skills.toolPermissionCol')}
                          aria-label={`${t('skills.toolPermissionCol')} ${tool.capability}`}
                          value={tool.permission}
                          disabled={!canManage || mutationPending}
                          data-testid={`agent-tool-permission-${tool.capability}`}
                          onChange={(event) => {
                            const permission = event.target.value as CapabilityPermission;
                            void runMutation(
                              async () => {
                                await updateAgentTool(client, agentId, tool.capability, {
                                  permission,
                                });
                                refresh();
                              },
                              () => {
                                toast.addToast(t('skills.toolSaveFailed'), {
                                  tone: 'danger',
                                  closeLabel: t('a11y.closeDialog'),
                                });
                              },
                            );
                          }}
                        >
                          <option value="read_only">{t('skills.permission.read_only')}</option>
                          <option value="write">{t('skills.permission.write')}</option>
                          <option value="confirm_required">
                            {t('skills.permission.confirm_required')}
                          </option>
                        </Select>
                      </td>
                      <td>
                        {canManage ? (
                          <Button
                            variant="secondary"
                            disabled={mutationPending}
                            aria-label={`${t('skills.toolRemove')} ${tool.capability}`}
                            data-testid={`agent-tool-remove-${tool.capability}`}
                            onClick={() => {
                              void runMutation(
                                async () => {
                                  await unbindAgentTool(client, agentId, tool.capability);
                                  refresh();
                                },
                                () => {
                                  toast.addToast(t('skills.toolSaveFailed'), {
                                    tone: 'danger',
                                    closeLabel: t('a11y.closeDialog'),
                                  });
                                },
                              );
                            }}
                          >
                            {t('skills.toolRemove')}
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {canManage ? (
            <div className="mesh-skills__bind-row" data-testid="agent-tool-bind-row">
              <Input
                label={t('skills.toolCapabilityInput')}
                value={toolCapability}
                disabled={mutationPending}
                onChange={(event) => setToolCapability(event.target.value)}
              />
              <Select
                label={t('skills.toolNewPermission')}
                value={toolPermission}
                disabled={mutationPending}
                onChange={(event) => setToolPermission(event.target.value as CapabilityPermission)}
              >
                <option value="read_only">{t('skills.permission.read_only')}</option>
                <option value="write">{t('skills.permission.write')}</option>
                <option value="confirm_required">{t('skills.permission.confirm_required')}</option>
              </Select>
              <Button
                data-testid="agent-tool-add"
                disabled={toolCapability.trim() === '' || mutationPending}
                onClick={() => void onBindTool()}
              >
                {t('skills.toolAdd')}
              </Button>
            </div>
          ) : null}
          <p className="mesh-skills__bind-note">{t('skills.toolManageHint')}</p>
        </section>
      </div>
      <p className="mesh-skills__bind-note">{t('skills.agentBindNote')}</p>
    </section>
  );
}
