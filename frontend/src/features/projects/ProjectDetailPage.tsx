/**
 * 项目详情页(project.md §4.1/§4.3):头部(名称 / 状态 / 健康度 / 进度 / 负责人 / 目标日,
 * [更新状态] [设置] [归档/取消归档] [删除])+ 三 Tab(概览 / 里程碑 / 更新动态,?tab= 同源)。
 * 实时订阅 project:{id}:project.updated 合并头部,milestone.* 合并里程碑列表,
 * project_update.added 头插动态,archived/unarchived 置位,deleted 回列表页(§3.5/§4.5)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, Dialog, EmptyState, ErrorState, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { activeWorkspace, fetchMe } from '../members/api';
import {
  archiveProject,
  deleteProject,
  getProject,
  listProjectUpdates,
  projectChannel,
  unarchiveProject,
} from './api';
import { ExportDialog } from '../data-jobs/ExportDialog';
import { ImportWizard } from '../data-jobs/ImportWizard';
import { HealthUpdateDialog } from './HealthUpdateDialog';
import { MilestonesPanel } from './MilestonesPanel';
import { applyMilestoneFrame, applyUpdateFrame, mergeProjectHeader } from './realtime';
import { UpdatesPanel } from './UpdatesPanel';
import type { Membership } from '../members/types';
import type { Milestone, ProjectDetail, ProjectUpdateEntry } from './types';
import { AvatarInitial, HealthIndicator, ProgressBar, StatusBadge } from './widgets';
import './projects.css';

type TabKey = 'overview' | 'milestones' | 'updates';

const TAB_KEYS: readonly TabKey[] = ['overview', 'milestones', 'updates'];

function tabFromParam(raw: string | null): TabKey {
  return TAB_KEYS.includes(raw as TabKey) ? (raw as TabKey) : 'overview';
}

function upsertById<T extends { id: string }>(items: readonly T[], item: T): T[] {
  const index = items.findIndex((existing) => existing.id === item.id);
  if (index === -1) return [...items, item];
  return items.map((existing, i) => (i === index ? item : existing));
}

export function ProjectDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const { projectId } = useParams<{ projectId: string }>();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();

  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = tabFromParam(searchParams.get('tab'));

  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [meResolved, setMeResolved] = useState(false);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [updates, setUpdates] = useState<ProjectUpdateEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [healthOpen, setHealthOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((me) => {
        if (!cancelled) setWorkspace(activeWorkspace(me.memberships));
      })
      .catch(() => {
        if (!cancelled) setError(t('state.errorDescription'));
      })
      .finally(() => {
        if (!cancelled) setMeResolved(true);
      });
    return () => {
      cancelled = true;
    };
  }, [client, t]);

  useEffect(() => {
    if (workspace === null || projectId === undefined) return;
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    Promise.all([getProject(client, projectId), listProjectUpdates(client, projectId, { limit: 50 })])
      .then(([detail, page]) => {
        if (cancelled) return;
        setProject(detail);
        setMilestones([...detail.milestones]);
        setUpdates([...page.data]);
      })
      .catch((err) => {
        if (cancelled) return;
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(t(key));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspace, projectId, reloadKey, t]);

  // 实时:project:{id} 全量事件(含 private)
  useEffect(() => {
    if (realtime === null || projectId === undefined) return;
    const channel = projectChannel(projectId);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.event === 'project.deleted') {
        navigate('/projects');
        return;
      }
      if (frame.event === 'project.archived' || frame.event === 'project.unarchived') {
        const archived = frame.event === 'project.archived';
        setProject((prev) => (prev === null ? prev : { ...prev, archived }));
        return;
      }
      if (frame.event === 'project.updated') {
        setProject((prev) => (prev === null ? prev : mergeProjectHeader(prev, frame)));
        return;
      }
      if (frame.event.startsWith('milestone.')) {
        setMilestones((prev) => applyMilestoneFrame(prev, frame));
        return;
      }
      if (frame.event === 'project_update.added') {
        setUpdates((prev) => applyUpdateFrame(prev, frame));
      }
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, projectId, navigate]);

  const selectTab = (tab: TabKey): void => {
    const params = new URLSearchParams(searchParams);
    if (tab === 'overview') params.delete('tab');
    else params.set('tab', tab);
    setSearchParams(params, { replace: true });
  };

  const handleArchiveToggle = async (): Promise<void> => {
    if (project === null || projectId === undefined) return;
    try {
      const updated = project.archived
        ? await unarchiveProject(client, projectId)
        : await archiveProject(client, projectId);
      setProject((prev) => (prev === null ? prev : { ...prev, ...updated }));
      toast.addToast(t(project.archived ? 'projects.detail.unarchived' : 'projects.detail.archived'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch (err) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    }
  };

  const handleDelete = async (): Promise<void> => {
    if (projectId === undefined) return;
    setIsDeleting(true);
    try {
      await deleteProject(client, projectId);
      toast.addToast(t('projects.detail.deleted'), { tone: 'success', closeLabel: t('common.close') });
      navigate('/projects');
    } catch (err) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setIsDeleting(false);
      setDeleteOpen(false);
    }
  };

  if (meResolved && workspace === null && error === null) {
    return (
      <main className="mesh-projects">
        <EmptyState title={t('state.emptyTitle')} description={t('projects.noWorkspace')} />
      </main>
    );
  }
  if (error !== null) {
    return (
      <main className="mesh-projects">
        <ErrorState
          title={t('state.errorTitle')}
          description={error}
          retryLabel={t('common.retry')}
          onRetry={reload}
        />
      </main>
    );
  }
  if (isLoading || project === null) {
    return (
      <main className="mesh-projects">
        <Skeleton loadingLabel={t('common.loading')} />
      </main>
    );
  }

  const total = project.done_issues + project.open_issues;
  const progressTitle = t('projects.card.progress', { done: project.done_issues, total });

  return (
    <main className="mesh-projects">
      <div className="mesh-projects__detail-header" data-testid="project-detail-header">
        <div className="mesh-projects__detail-title-row">
          {project.color !== null ? (
            <span
              className="mesh-projects__color-swatch"
              data-testid="project-color"
              style={{ background: project.color }}
              aria-hidden="true"
            />
          ) : null}
          {project.icon !== null ? (
            <span className="mesh-projects__icon" data-testid="project-icon" aria-hidden="true">
              {project.icon}
            </span>
          ) : null}
          <h1 className="mesh-projects__title">{project.name}</h1>
          <StatusBadge status={project.status} label={t(`projects.status.${project.status}`)} />
          {/* §4.2 健康度灯可点击更新;归档只读时不可点 */}
          <HealthIndicator
            health={project.health}
            onClick={project.archived ? undefined : () => setHealthOpen(true)}
          />
        </div>
        <div className="mesh-projects__detail-meta">
          <ProgressBar progress={project.progress} title={progressTitle} />
          {project.lead !== null ? (
            <span className="mesh-projects__detail-lead">
              <AvatarInitial name={project.lead.name} accessibleName={project.lead.name} />
              {project.lead.name}
            </span>
          ) : null}
          {project.target_date !== null ? (
            <span>{t('projects.card.due', { date: project.target_date })}</span>
          ) : null}
        </div>
        <div className="mesh-projects__detail-actions">
          <Button
            variant="secondary"
            data-testid="update-status-button"
            onClick={() => setHealthOpen(true)}
          >
            {t('projects.detail.updateStatus')}
          </Button>
          <Link
            to={`/projects/${project.id}/settings`}
            className="mesh-projects__settings-link"
            data-testid="settings-link"
          >
            {t('projects.detail.settings')}
          </Link>
          <Button
            variant="secondary"
            data-testid="archive-toggle-button"
            onClick={() => void handleArchiveToggle()}
          >
            {project.archived ? t('projects.detail.unarchive') : t('projects.detail.archive')}
          </Button>
          <Button
            variant="danger"
            data-testid="delete-project-button"
            onClick={() => setDeleteOpen(true)}
          >
            {t('projects.detail.delete')}
          </Button>
          {/* import-export.md §4.1 情境入口:导出本项目(读权限)/ 导入到本项目(写权限) */}
          <Button
            variant="secondary"
            data-testid="export-project-button"
            onClick={() => setExportOpen(true)}
          >
            {t('dataJobs.page.exportProject')}
          </Button>
          {(workspace?.role === 'admin' || workspace?.role === 'owner') && (
            <Button
              variant="secondary"
              data-testid="import-project-button"
              onClick={() => setImportOpen(true)}
            >
              {t('dataJobs.page.importProject')}
            </Button>
          )}
        </div>
      </div>

      {workspace !== null && (
        <>
          <ExportDialog
            open={exportOpen}
            onClose={() => setExportOpen(false)}
            workspaceId={workspace.workspace_id}
            defaultScope="project"
            projectId={project.id}
            filters={{ project_id: project.id }}
          />
          <ImportWizard
            open={importOpen}
            onClose={() => setImportOpen(false)}
            workspaceId={workspace.workspace_id}
            targetProjectId={project.id}
          />
        </>
      )}

      <div className="mesh-members__tabs" role="tablist" aria-label={t('projects.tab.label')}>
        {TAB_KEYS.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            className="mesh-members__tab"
            data-testid={`tab-${tab}`}
            onClick={() => selectTab(tab)}
          >
            {t(`projects.tab.${tab}`)}
          </button>
        ))}
      </div>

      {activeTab === 'overview' ? (
        <section className="mesh-projects__panel" aria-label={t('projects.tab.overview')}>
          <p className="mesh-projects__description" data-testid="project-description">
            {project.description !== null && project.description !== ''
              ? project.description
              : t('projects.detail.noDescription')}
          </p>
          {milestones.length === 0 ? (
            <p className="mesh-projects__sub">{t('projects.milestones.empty')}</p>
          ) : (
            <ul className="mesh-projects__milestone-list" data-testid="overview-milestone-list">
              {milestones.map((milestone) => (
                <li
                  key={milestone.id}
                  className={
                    milestone.overdue
                      ? 'mesh-projects__milestone mesh-projects__milestone--overdue'
                      : 'mesh-projects__milestone'
                  }
                >
                  <span className="mesh-projects__milestone-title">{milestone.title}</span>
                  <span className="mesh-projects__milestone-sub">
                    {t(`projects.milestones.state.${milestone.state}`)}
                    {milestone.target_date !== null ? ` · ${milestone.target_date}` : ''}
                    {milestone.overdue ? ` · ${t('projects.milestones.overdue')}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      {activeTab === 'milestones' ? (
        <MilestonesPanel
          client={client}
          projectId={project.id}
          milestones={milestones}
          upsertMilestone={(milestone) => setMilestones((prev) => upsertById(prev, milestone))}
          removeMilestone={(milestoneId) =>
            setMilestones((prev) => prev.filter((m) => m.id !== milestoneId))
          }
        />
      ) : null}

      {activeTab === 'updates' ? (
        <UpdatesPanel
          client={client}
          projectId={project.id}
          updates={updates}
          prependUpdate={(update) => setUpdates((prev) => [update, ...prev])}
          onSubmitted={reload}
        />
      ) : null}

      <HealthUpdateDialog
        open={healthOpen}
        onClose={() => setHealthOpen(false)}
        client={client}
        projectId={project.id}
        onSaved={reload}
      />

      {deleteOpen ? (
        <Dialog
          open
          onClose={() => setDeleteOpen(false)}
          title={t('projects.detail.deleteTitle')}
          closeLabel={t('common.close')}
        >
          <p data-testid="delete-confirm-text">{t('projects.detail.deleteConfirm', { name: project.name })}</p>
          <div className="mesh-projects__form-actions">
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              disabled={isDeleting}
              data-testid="delete-confirm"
              onClick={() => void handleDelete()}
            >
              {t('projects.detail.deleteSubmit')}
            </Button>
          </div>
        </Dialog>
      ) : null}
    </main>
  );
}
