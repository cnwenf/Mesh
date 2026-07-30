/**
 * 技能详情页(skill.md §4.1/§4.2):概览 / 版本历史 / 脚本 / 资料 / 触发条件 五 Tab。
 * 版本历史支持查看与回滚入口(历史永不删除);脚本页可展开正文(含高危标注);
 * 右侧操作区承载安装 / 停用 / 弃用。实时:skill.changed 触发重拉(README §6.7)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import { Button, EmptyState, ErrorState, Icon, Select, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import {
  getSkill,
  getVersion,
  installSkill,
  listInstallations,
  listVersions,
  rollbackInstallation,
  updateInstallation,
  updateSkill,
  workspaceSkillsChannel,
} from './api';
import type { SkillDetail, SkillInstallation, SkillVersion } from './types';
import './skills.css';

type TabKey = 'overview' | 'versions' | 'scripts' | 'references' | 'triggers';
const TAB_KEYS: readonly TabKey[] = ['overview', 'versions', 'scripts', 'references', 'triggers'];

/** 高危能力关键词(§4.2 网络 / 写文件等高亮提示)。 */
const RISKY_CAPABILITY_PATTERN = /net:|write|exec:/i;

type DiffLine = { kind: 'add' | 'del' | 'eq'; text: string };

/** Minimal line diff (LCS) for the §4.3 version changelog/diff view. */
function lineDiff(from: string, to: string): DiffLine[] {
  const a = from.split('\n');
  const b = to.split('\n');
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i -= 1) {
    for (let j = n - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      out.push({ kind: 'eq', text: a[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: 'del', text: a[i] });
      i += 1;
    } else {
      out.push({ kind: 'add', text: b[j] });
      j += 1;
    }
  }
  while (i < m) {
    out.push({ kind: 'del', text: a[i] });
    i += 1;
  }
  while (j < n) {
    out.push({ kind: 'add', text: b[j] });
    j += 1;
  }
  return out;
}

function DiffView({
  from,
  fromLabel,
  to,
  toLabel,
  t,
}: {
  from: string;
  fromLabel: string;
  to: string;
  toLabel: string;
  t: (k: string) => string;
}): React.JSX.Element {
  return (
    <div className="mesh-skills-detail__diff" data-testid="skill-diff-view">
      <h4>{t('skills.diffTitle')}</h4>
      <pre>
        {lineDiff(from, to).map((line, idx) => (
          <span key={idx} className={`mesh-skills-detail__diff-${line.kind}`}>
            {line.kind === 'add' ? '+ ' : line.kind === 'del' ? '- ' : '  '}
            {line.text}
            {'\n'}
          </span>
        ))}
      </pre>
      <p className="mesh-skills-detail__diff-legend">
        {t('skills.diffFrom')}: {fromLabel} → {t('skills.diffTo')}: {toLabel}
      </p>
    </div>
  );
}

export function SkillDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const { skillId } = useParams<{ skillId: string }>();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);

  const [membership, setMembership] = useState<Membership | null>(null);
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<SkillVersion | null>(null);
  const [installations, setInstallations] = useState<SkillInstallation[]>([]);
  const [tab, setTab] = useState<TabKey>('overview');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((me) => {
        if (!cancelled) setMembership(activeWorkspace(me.memberships));
      })
      .catch(() => {
        /* keep empty state when there is no membership */
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  const workspaceId = membership?.workspace_id ?? null;
  const canManage = membership?.role === 'admin' || membership?.role === 'owner';

  useEffect(() => {
    if (workspaceId === null || skillId === undefined) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      getSkill(client, workspaceId, skillId),
      listVersions(client, workspaceId, skillId, { limit: 50 }),
      listInstallations(client, workspaceId, { skill_id: skillId, limit: 50 }),
    ])
      .then(([detail, versionPage, installationPage]) => {
        if (cancelled) return;
        setSkill(detail);
        setVersions(versionPage.data);
        setInstallations(installationPage.data);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError(t('skills.loadError'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, skillId, reloadKey, t]);

  // 实时重拉(skill.changed / update_available)。
  useEffect(() => {
    if (realtime === null || workspaceId === null) return;
    const channel = workspaceSkillsChannel(workspaceId);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (String(frame.channel) === channel) setReloadKey((k) => k + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspaceId]);

  const openVersion = useCallback(
    async (version: SkillVersion) => {
      if (workspaceId === null || skillId === undefined) return;
      const full = await getVersion(client, workspaceId, skillId, version.id, true);
      setSelectedVersion(full);
      setTab('scripts');
    },
    [client, workspaceId, skillId],
  );

  const doInstall = useCallback(
    async (version: SkillVersion) => {
      if (workspaceId === null || skill === null) return;
      try {
        await installSkill(client, workspaceId, {
          skill_id: skill.id,
          skill_version_id: version.id,
          scope: 'workspace',
        });
        toast.addToast(t('skills.installSucceeded'), { tone: 'success', closeLabel: t('a11y.closeDialog') });
        setReloadKey((k) => k + 1);
      } catch {
        toast.addToast(t('skills.installFailed'), { tone: 'danger', closeLabel: t('a11y.closeDialog') });
      }
    },
    [client, workspaceId, skill, t, toast],
  );

  const doStatusChange = useCallback(
    async (status: string) => {
      if (workspaceId === null || skill === null) return;
      try {
        await updateSkill(client, workspaceId, skill.id, { status });
        setReloadKey((k) => k + 1);
      } catch {
        toast.addToast(t('skills.statusChangeFailed'), { tone: 'danger', closeLabel: t('a11y.closeDialog') });
      }
    },
    [client, workspaceId, skill, t, toast],
  );

  const toggleInstallation = useCallback(
    async (installation: SkillInstallation, disable: boolean) => {
      if (workspaceId === null) return;
      try {
        await updateInstallation(client, workspaceId, installation.id, {
          install_status: disable ? 'disabled' : 'installed',
        });
        setReloadKey((k) => k + 1);
      } catch {
        toast.addToast(t('skills.statusChangeFailed'), { tone: 'danger', closeLabel: t('a11y.closeDialog') });
      }
    },
    [client, workspaceId, t, toast],
  );

  const [diffVersionId, setDiffVersionId] = useState<string | null>(null);

  // CRITICAL-3: roll the installation back to a historic version (§4.2/§5.1).
  const doRollback = useCallback(
    async (installation: SkillInstallation, version: SkillVersion) => {
      if (workspaceId === null) return;
      try {
        await rollbackInstallation(client, workspaceId, installation.id, {
          target_version_id: version.id,
        });
        toast.addToast(t('skills.rollbackSucceeded'), { tone: 'success', closeLabel: t('a11y.closeDialog') });
        setReloadKey((k) => k + 1);
      } catch {
        toast.addToast(t('skills.rollbackFailed'), { tone: 'danger', closeLabel: t('a11y.closeDialog') });
      }
    },
    [client, workspaceId, t, toast],
  );

  // HIGH-5: upgrade the installation to the current version (§4.3 [立即更新]).
  const doUpdateNow = useCallback(
    async (installation: SkillInstallation) => {
      if (workspaceId === null || skill === null || skill.current_version_id === null) return;
      try {
        await updateInstallation(client, workspaceId, installation.id, {
          skill_version_id: skill.current_version_id,
        });
        toast.addToast(t('skills.updateSucceeded'), { tone: 'success', closeLabel: t('a11y.closeDialog') });
        setReloadKey((k) => k + 1);
      } catch {
        // 422 approval_required surfaces here when scripts changed (§4.4).
        toast.addToast(t('skills.updateNeedsApproval'), { tone: 'danger', closeLabel: t('a11y.closeDialog') });
      }
    },
    [client, workspaceId, skill, t, toast],
  );

  if (error !== null) {
    return <ErrorState title={t('state.errorTitle')} description={error} />;
  }
  if (loading || skill === null) {
    return <Skeleton loadingLabel={t('state.loading')} />;
  }

  const currentVersion = versions.find((v) => v.id === skill.current_version_id) ?? null;
  const installation = installations[0] ?? null;

  return (
    <div className="mesh-skills-detail" data-testid="skill-detail">
      <header className="mesh-skills-detail__header">
        <h1 data-testid="skill-detail-name">{skill.name}</h1>
        <span className={`mesh-skills__status mesh-skills__status--${skill.status}`}>
          {t(`skills.status.${skill.status}`)}
        </span>
        {skill.has_scripts ? (
          <span className="mesh-skills__script-flag">
            <Icon name="warning" size={16} />
            {t('skills.hasScripts')}
          </span>
        ) : null}
      </header>

      <nav className="mesh-skills-detail__tabs" aria-label={t('skills.detailTabs')}>
        {TAB_KEYS.map((key) => (
          <button
            key={key}
            type="button"
            className={tab === key ? 'is-active' : ''}
            onClick={() => setTab(key)}
            data-testid={`skill-tab-${key}`}
          >
            {t(`skills.tab.${key}`)}
          </button>
        ))}
      </nav>

      <div className="mesh-skills-detail__body">
        <section className="mesh-skills-detail__main">
          {tab === 'overview' ? (
            <div data-testid="skill-panel-overview">
              <p>{skill.summary}</p>
              {currentVersion !== null ? (
                <pre className="mesh-skills-wizard__instructions">{currentVersion.instructions}</pre>
              ) : (
                <EmptyState title={t('skills.noVersionTitle')} description={t('skills.noVersionDescription')} />
              )}
            </div>
          ) : null}

          {tab === 'versions' ? (
            <table className="mesh-skills-detail__versions" data-testid="skill-panel-versions">
              <thead>
                <tr>
                  <th>{t('skills.versionCol')}</th>
                  <th>{t('skills.versionStatusCol')}</th>
                  <th>{t('skills.versionChangelogCol')}</th>
                  <th>{t('skills.versionActionsCol')}</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((version) => (
                  <tr key={version.id} data-testid={`skill-version-${version.version}`}>
                    <td>
                      {version.version}
                      {version.is_current ? (
                        <Icon name="check" size={16} className="mesh-skills__current-mark" />
                      ) : null}
                    </td>
                    <td>{version.status}</td>
                    <td>{version.changelog ?? ''}</td>
                    <td>
                      <Button
                        variant="secondary"
                        onClick={() => void openVersion(version)}
                        data-testid={`skill-view-${version.version}`}
                      >
                        {t('skills.versionView')}
                      </Button>
                      {canManage && !version.is_current && installation !== null ? (
                        <Button
                          variant="secondary"
                          onClick={() => void doRollback(installation, version)}
                          data-testid={`skill-rollback-${version.version}`}
                        >
                          {t('skills.versionRollback')}
                        </Button>
                      ) : null}
                      <Button
                        variant="secondary"
                        onClick={() =>
                          setDiffVersionId((cur) => (cur === version.id ? null : version.id))
                        }
                        data-testid={`skill-diff-${version.version}`}
                      >
                        {t('skills.versionDiff')}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}

          {tab === 'versions' && diffVersionId !== null && currentVersion !== null ? (
            <DiffView
              from={versions.find((v) => v.id === diffVersionId)?.instructions ?? ''}
              fromLabel={versions.find((v) => v.id === diffVersionId)?.version ?? ''}
              to={currentVersion.instructions}
              toLabel={currentVersion.version}
              t={t}
            />
          ) : null}

          {tab === 'scripts' ? (
            <div data-testid="skill-panel-scripts">
              {selectedVersion === null ? (
                <EmptyState title={t('skills.pickVersionTitle')} description={t('skills.pickVersionDescription')} />
              ) : (selectedVersion.scripts ?? []).length === 0 ? (
                <EmptyState title={t('skills.noScriptsTitle')} description={t('skills.noScriptsDescription')} />
              ) : (
                (selectedVersion.scripts ?? []).map((script) => (
                  <details key={script.id} className="mesh-skills-detail__script" open>
                    <summary>
                      <code>{script.path}</code> · {script.runtime}
                      {script.entrypoint ? ` · ${t('skills.entrypoint')}` : ''}
                    </summary>
                    <ul className="mesh-skills-detail__script-caps">
                      {(script.required_capabilities ?? []).map((cap, index) => {
                        const key = typeof cap === 'string' ? cap : cap.capability;
                        return (
                          <li key={`${key}-${index}`} className={RISKY_CAPABILITY_PATTERN.test(key) ? 'is-risky' : ''}>
                            {key}
                          </li>
                        );
                      })}
                    </ul>
                    <pre>{script.content ?? script.content_ref}</pre>
                  </details>
                ))
              )}
            </div>
          ) : null}

          {tab === 'references' ? (
            <div data-testid="skill-panel-references">
              {selectedVersion === null || (selectedVersion.references ?? []).length === 0 ? (
                <EmptyState title={t('skills.noReferencesTitle')} description={t('skills.noReferencesDescription')} />
              ) : (
                <ul>
                  {(selectedVersion.references ?? []).map((reference) => (
                    <li key={reference.id}>
                      <code>{reference.path}</code> · {reference.media_type}
                      {reference.summary !== null ? ` — ${reference.summary}` : ''}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}

          {tab === 'triggers' ? (
            <div data-testid="skill-panel-triggers">
              {selectedVersion === null || (selectedVersion.triggers ?? []).length === 0 ? (
                <EmptyState title={t('skills.noTriggersTitle')} description={t('skills.noTriggersDescription')} />
              ) : (
                <ul>
                  {(selectedVersion.triggers ?? []).map((trigger) => (
                    <li key={trigger.id}>
                      {t(`skills.triggerType.${trigger.trigger_type}`)}: {trigger.pattern} (×{trigger.weight})
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </section>

        <aside className="mesh-skills-detail__side" data-testid="skill-side-actions">
          <h3>{t('skills.sideTitle')}</h3>
          <dl>
            <dt>{t('skills.sideSource')}</dt>
            <dd>{skill.source_type === null ? '—' : t(`skills.source.${skill.source_type}`)}</dd>
            <dt>{t('skills.sideTrust')}</dt>
            <dd>{skill.trust_level === null ? '—' : t(`skills.trust.${skill.trust_level}`)}</dd>
            <dt>{t('skills.sideVersion')}</dt>
            <dd>{skill.current_version ?? '—'}</dd>
            <dt>{t('skills.sideRequiredCaps')}</dt>
            <dd>
              {(skill.required_capabilities ?? []).map((cap, index) => {
                const key = typeof cap === 'string' ? cap : cap.capability;
                return <span key={`${key}-${index}`} className="mesh-skills__cap-chip">{key}</span>;
              })}
            </dd>
            <dt>{t('skills.sideTags')}</dt>
            <dd>{(skill.tags ?? []).join(', ') || '—'}</dd>
          </dl>

          {canManage && currentVersion !== null ? (
            <Button onClick={() => void doInstall(currentVersion)} data-testid="skill-install">
              {t('skills.installButton')}
            </Button>
          ) : null}

          {canManage && installation !== null ? (
            <div className="mesh-skills-detail__installation" data-testid="skill-installation-row">
              <span>{t(`skills.installStatus.${installation.install_status}`)}</span>
              {installation.install_status === 'disabled' ? (
                <Button
                  variant="secondary"
                  onClick={() => void toggleInstallation(installation, false)}
                  data-testid="skill-enable-button"
                >
                  {t('skills.enableButton')}
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  onClick={() => void toggleInstallation(installation, true)}
                  data-testid="skill-disable-button"
                >
                  {t('skills.disableButton')}
                </Button>
              )}
            </div>
          ) : null}

          {canManage && installation !== null && installation.install_status === 'updated_available' ? (
            <div className="mesh-skills-detail__update" data-testid="skill-update-row">
              <span className="mesh-skills__update-flag">
                <Icon name="cycle" size={16} /> {t('skills.updateAvailable')}
              </span>
              <Button onClick={() => void doUpdateNow(installation)} data-testid="skill-update-now">
                {t('skills.updateNow')}
              </Button>
              <Button
                variant="secondary"
                onClick={() => setDiffVersionId(null)}
                data-testid="skill-update-later"
              >
                {t('skills.updateLater')}
              </Button>
            </div>
          ) : null}

          {canManage ? (
            <div className="mesh-skills-detail__lifecycle">
              <Select
                label={t('skills.lifecycleAction')}
                value=""
                onChange={(event) => {
                  if (event.target.value !== '') void doStatusChange(event.target.value);
                }}
                data-testid="skill-lifecycle-select"
              >
                <option value="">{t('skills.lifecyclePlaceholder')}</option>
                {skill.status === 'published' ? (
                  <>
                    <option value="deprecated">{t('skills.actionDeprecate')}</option>
                    <option value="disabled">{t('skills.actionDisable')}</option>
                  </>
                ) : null}
                {skill.status === 'deprecated' ? <option value="disabled">{t('skills.actionDisable')}</option> : null}
                {skill.status === 'disabled' ? (
                  <>
                    <option value="published">{t('skills.actionRestore')}</option>
                    <option value="deprecated">{t('skills.actionDeprecate')}</option>
                  </>
                ) : null}
              </Select>
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
