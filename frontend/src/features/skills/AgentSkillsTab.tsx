/**
 * Agent 详情「技能与工具」真实双列管理面(agent.md §4.3 / skill.md §4.2):
 * 左列管理合法的 agent_skills 绑定;右列从已绑定安装记录的
 * granted_capabilities 推导有效工具与权限。当前后端没有合法的 per-agent
 * capability mutation,因此权限控件如实只读,不模拟保存成功。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import { Button, EmptyState, ErrorState, Icon, Select, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { bindSkill, listAgentSkills, listInstallations, unbindSkill, updateBinding } from './api';
import { effectiveCapabilities, permissionTone } from './capabilities';
import type { AgentSkillRow, SkillInstallation } from './types';

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
  const [pick, setPick] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    Promise.all([
      listAgentSkills(client, workspaceId, agentId, { limit: 100 }),
      listInstallations(client, workspaceId, { scope: 'workspace', limit: 100 }),
    ])
      .then(([bound, available]) => {
        if (cancelled) return;
        setRows(bound.data);
        setInstallations(available.data);
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

  const onBind = useCallback(async () => {
    if (pick === '') return;
    try {
      await bindSkill(client, workspaceId, agentId, { skill_installation_id: pick });
      setPick('');
      refresh();
    } catch {
      toast.addToast(t('skills.bindFailed'), { tone: 'danger', closeLabel: t('a11y.closeDialog') });
    }
  }, [client, workspaceId, agentId, pick, refresh, t, toast]);

  const bindable = installations.filter(
    (installation) =>
      installation.install_status !== 'disabled' &&
      !rows.some((row) => row.skill.id === installation.skill_id),
  );

  const boundInstallations = installations.filter((installation) =>
    rows.some((row) => row.skill.id === installation.skill_id),
  );
  const effectiveTools = effectiveCapabilities(
    boundInstallations.flatMap((installation) => installation.granted_capabilities),
  );
  const enabledSkillIds = new Set(rows.filter((row) => row.enabled).map((row) => row.skill.id));
  const enabledToolKeys = new Set(
    effectiveCapabilities(
      boundInstallations
        .filter(
          (installation) =>
            installation.install_status !== 'disabled' &&
            enabledSkillIds.has(installation.skill_id),
        )
        .flatMap((installation) => installation.granted_capabilities),
    ).map((tool) => tool.capability),
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
    <section className="mesh-agents-detail__panel" data-testid="agent-panel-skills">
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
                          disabled={!canManage}
                          onChange={(event) => {
                            void (async () => {
                              try {
                                await updateBinding(client, workspaceId, agentId, row.binding_id, {
                                  enabled: event.target.checked,
                                });
                                refresh();
                              } catch {
                                toast.addToast(t('skills.bindFailed'), {
                                  tone: 'danger',
                                  closeLabel: t('a11y.closeDialog'),
                                });
                              }
                            })();
                          }}
                          aria-label={t('skills.agentEnabledCol')}
                        />
                      </td>
                      <td>
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
                      </td>
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
                          disabled={!canManage}
                          onChange={(event) => {
                            void (async () => {
                              try {
                                await updateBinding(client, workspaceId, agentId, row.binding_id, {
                                  auto_trigger: event.target.checked,
                                });
                                refresh();
                              } catch {
                                toast.addToast(t('skills.bindFailed'), {
                                  tone: 'danger',
                                  closeLabel: t('a11y.closeDialog'),
                                });
                              }
                            })();
                          }}
                          aria-label={t('skills.agentAutoTriggerCol')}
                        />
                      </td>
                      <td>
                        {canManage ? (
                          <input
                            type="number"
                            min={0}
                            max={1000}
                            defaultValue={row.priority}
                            aria-label={t('skills.agentPriorityCol')}
                            data-testid={`agent-priority-${row.binding_id}`}
                            onBlur={(event) => {
                              const value = Number(event.target.value);
                              if (Number.isNaN(value) || value === row.priority) return;
                              void (async () => {
                                try {
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
                                } catch {
                                  toast.addToast(t('skills.bindFailed'), {
                                    tone: 'danger',
                                    closeLabel: t('a11y.closeDialog'),
                                  });
                                }
                              })();
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
                            onClick={() => {
                              void (async () => {
                                try {
                                  await unbindSkill(client, workspaceId, agentId, row.binding_id);
                                  refresh();
                                } catch {
                                  toast.addToast(t('skills.unbindFailed'), {
                                    tone: 'danger',
                                    closeLabel: t('a11y.closeDialog'),
                                  });
                                }
                              })();
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
                disabled={pick === ''}
                data-testid="agent-skill-bind"
              >
                {t('skills.bindButton')}
              </Button>
            </div>
          ) : null}
        </section>

        <section className="mesh-skills-agent-grid__column" data-testid="agent-tools-column">
          <h2 className="mesh-text-title-3">{t('skills.agentToolsTitle')}</h2>
          {effectiveTools.length === 0 ? (
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
                  </tr>
                </thead>
                <tbody>
                  {effectiveTools.map((tool) => (
                    <tr
                      key={tool.capability}
                      className={`mesh-skills__permission-row mesh-skills__permission-row--${permissionTone(tool.permission)}`}
                      data-testid={`agent-tool-${tool.capability}`}
                    >
                      <td>
                        <input
                          type="checkbox"
                          checked={enabledToolKeys.has(tool.capability)}
                          disabled
                          aria-label={t('skills.toolEnabledCol')}
                        />
                      </td>
                      <td>
                        <code>{tool.capability}</code>
                      </td>
                      <td>
                        <Select
                          label={t('skills.toolPermissionCol')}
                          value={tool.permission}
                          disabled
                          data-testid={`agent-tool-permission-${tool.capability}`}
                        >
                          <option value="read_only">{t('skills.permission.read_only')}</option>
                          <option value="write">{t('skills.permission.write')}</option>
                          <option value="confirm_required">
                            {t('skills.permission.confirm_required')}
                          </option>
                        </Select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mesh-skills__bind-note">{t('skills.toolReadOnlyHint')}</p>
        </section>
      </div>
      <p className="mesh-skills__bind-note">{t('skills.agentBindNote')}</p>
    </section>
  );
}
