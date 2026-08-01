/**
 * agent 详情页「技能」Tab(skill.md §4.2 agent 绑定区):已绑定技能列表
 * (启用复选 + 名称 + 版本 + 自动触发开关 + 优先级 + 解绑),底部「从库中绑定」
 * 选择 workspace 级安装;第三方来源技能以警示图标标注(执行受沙箱与已授予权限约束)。
 * 替换 MES-60 留下的占位(agent.md 范围说明)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import { Button, EmptyState, Icon, Select, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { bindSkill, listAgentSkills, listInstallations, unbindSkill, updateBinding } from './api';
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

  useEffect(() => {
    let cancelled = false;
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
        if (!cancelled)
          toast.addToast(t('skills.loadError'), {
            tone: 'danger',
            closeLabel: t('a11y.closeDialog'),
          });
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, agentId, reloadKey, reloadSignal, t, toast]);

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

  const boundInstallationIds = new Set(rows.map((row) => row.binding_id));
  const bindable = installations.filter(
    (installation) => !rows.some((row) => row.skill.id === installation.skill_id),
  );

  return (
    <section className="mesh-agents-detail__panel" data-testid="agent-panel-skills">
      {rows.length === 0 ? (
        <EmptyState
          title={t('skills.agentEmptyTitle')}
          description={t('skills.agentEmptyDescription')}
        />
      ) : (
        <table className="mesh-skills-detail__versions" data-testid="agent-skills-table">
          <caption className="sr-only">{t('skills.agentNameCol')}</caption>
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
                  {row.skill.source_type === 'url' || row.skill.source_type === 'marketplace' ? (
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
                            await updateBinding(client, workspaceId, agentId, row.binding_id, {
                              priority: Math.min(1000, Math.max(0, value)),
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
                {installation.skill_id.slice(0, 8)}… (
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
      <p className="mesh-skills__bind-note">{t('skills.agentBindNote')}</p>
      <span hidden>{boundInstallationIds.size}</span>
    </section>
  );
}
