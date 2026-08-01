/**
 * 项目列表页(project.md §4.1):筛选(状态 / 已归档 / 我参与的,URL 同源)+
 * 卡片网格(名称 / 状态徽章 / 健康度灯 / 进度条 / 负责人 / 目标日)+ 游标 Load more。
 * 实时经 workspace:{ws}:projects 频道按可见性水位合并(§3.5/§6.7)。
 * 状态渲染序:无工作区空态 → 错误态(可重试)→ 骨架 → 空态 → 内容(对齐 MembersPage)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import {
  Button,
  DataView,
  EmptyState,
  ErrorState,
  Icon,
  Select,
  Skeleton,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import { EmptyFolder } from '../onboarding/illustrations';
import { listProjects, workspaceProjectsChannel } from './api';
import { CreateProjectDialog } from './CreateProjectDialog';
import { HealthUpdateDialog } from './HealthUpdateDialog';
import { applyProjectListFrame } from './realtime';
import type { ProjectStatus, ProjectSummary } from './types';
import { PROJECT_STATUS_ORDER } from './types';
import { AvatarInitial, HealthIndicator, ProgressBar, StatusBadge } from './widgets';
import './projects.css';

const PAGE_LIMIT = 20;
const STATUS_ALL = 'all';

function matchesListFilters(project: ProjectSummary, status: string, archived: boolean): boolean {
  if (project.archived !== archived) return false;
  if (status !== STATUS_ALL && project.status !== status) return false;
  return true;
}

interface ProjectCardProps {
  readonly project: ProjectSummary;
  readonly onHealthClick: (project: ProjectSummary) => void;
}

function ProjectCard(props: ProjectCardProps): React.JSX.Element {
  const t = useT();
  const { project, onHealthClick } = props;
  const total = project.done_issues + project.open_issues;
  const progressTitle = t('projects.card.progress', { done: project.done_issues, total });
  return (
    <li className="mesh-projects__card" data-testid={`project-card-${project.id}`}>
      <div className="mesh-projects__card-head">
        <span className="mesh-projects__card-identity">
          {project.color !== null ? (
            <span
              className="mesh-projects__color-swatch"
              data-testid={`project-color-${project.id}`}
              style={{ background: project.color }}
              aria-hidden="true"
            />
          ) : null}
          {project.icon !== null ? (
            <span
              className="mesh-projects__icon"
              data-testid={`project-icon-${project.id}`}
              aria-hidden="true"
            >
              {project.icon}
            </span>
          ) : null}
          <Link to={`/projects/${project.id}`} className="mesh-projects__card-name">
            {project.name}
          </Link>
        </span>
        <span className="mesh-projects__card-badges">
          <StatusBadge status={project.status} label={t(`projects.status.${project.status}`)} />
          {project.archived ? (
            <span className="mesh-projects__archive-badge">{t('projects.filter.archived')}</span>
          ) : null}
        </span>
      </div>
      <div className="mesh-projects__card-meta">
        <HealthIndicator
          health={project.health}
          updateLabel={t('projects.detail.updateStatus')}
          onClick={() => onHealthClick(project)}
        />
        {project.lead !== null ? (
          <AvatarInitial name={project.lead.name} accessibleName={project.lead.name} />
        ) : null}
        {project.target_date !== null ? (
          <span className="mesh-projects__card-date" data-testid={`project-date-${project.id}`}>
            {t('projects.card.due', { date: new Date(project.target_date) })}
          </span>
        ) : null}
      </div>
      <ProgressBar progress={project.progress} title={progressTitle} />
    </li>
  );
}

export function ProjectsPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();

  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get('status') ?? STATUS_ALL;
  const showArchived = searchParams.get('archived') === 'true';
  const mineOnly = searchParams.get('mine') === 'true';
  const viewMode = searchParams.get('view') === 'grid' ? 'grid' : 'list';

  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isFetchingMore, setIsFetchingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  /** 列表卡片健康度灯点击 → 页面级更新对话框的目标项目(§4.2 点击更新) */
  const [healthTarget, setHealthTarget] = useState<ProjectSummary | null>(null);

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

  const loadProjects = useCallback(() => {
    if (workspace === null) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    listProjects(client, workspace.workspace_id, {
      status: statusFilter === STATUS_ALL ? undefined : (statusFilter as ProjectStatus),
      archived: showArchived,
      mine: mineOnly,
      limit: PAGE_LIMIT,
    })
      .then((page) => {
        setProjects([...page.data]);
        setNextCursor(page.nextCursor);
      })
      .catch((err) => setError(err instanceof Error ? err.message : t('state.errorDescription')))
      .finally(() => setIsLoading(false));
  }, [client, workspace, statusFilter, showArchived, mineOnly, t]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects, reloadKey]);

  // 实时合并:belongs 按当前筛选判定可见性水位;ref 避免随筛选重订阅
  const belongsRef = useRef<(project: ProjectSummary) => boolean>(() => true);
  belongsRef.current = (project) => matchesListFilters(project, statusFilter, showArchived);

  useEffect(() => {
    if (realtime === null || workspace === null) return;
    const channel = workspaceProjectsChannel(workspace.workspace_id);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      setProjects((prev) => applyProjectListFrame(prev, frame, belongsRef.current));
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspace]);

  const handleLoadMore = (): void => {
    if (workspace === null || nextCursor === null || isFetchingMore) return;
    setIsFetchingMore(true);
    listProjects(client, workspace.workspace_id, {
      status: statusFilter === STATUS_ALL ? undefined : (statusFilter as ProjectStatus),
      archived: showArchived,
      mine: mineOnly,
      limit: PAGE_LIMIT,
      cursor: nextCursor,
    })
      .then((page) => {
        setProjects((prev) => [...prev, ...page.data]);
        setNextCursor(page.nextCursor);
      })
      .catch(() => {
        toast.addToast(t('common.unknownError'), { tone: 'danger', closeLabel: t('common.close') });
      })
      .finally(() => setIsFetchingMore(false));
  };

  const updateParam = (key: string, value: string | null): void => {
    const params = new URLSearchParams(searchParams);
    if (value === null) params.delete(key);
    else params.set(key, value);
    setSearchParams(params, { replace: true });
  };

  return (
    <main className="mesh-projects">
      <DataView
        title={t('projects.title')}
        actions={
          workspace !== null ? (
            <Button
              variant="primary"
              data-testid="new-project-button"
              onClick={() => setCreateOpen(true)}
            >
              {t('projects.new')}
            </Button>
          ) : null
        }
        toolbar={
          <div
            className="mesh-projects__toolbar"
            role="group"
            aria-label={t('projects.filterLabel')}
          >
            <Select
              label={t('projects.filter.status')}
              value={statusFilter}
              data-testid="projects-status-filter"
              onChange={(event) =>
                updateParam('status', event.target.value === STATUS_ALL ? null : event.target.value)
              }
            >
              <option value={STATUS_ALL}>{t('projects.status.all')}</option>
              {PROJECT_STATUS_ORDER.map((status) => (
                <option key={status} value={status}>
                  {t(`projects.status.${status}`)}
                </option>
              ))}
            </Select>
            <label className="mesh-projects__check">
              <input
                type="checkbox"
                checked={showArchived}
                data-testid="projects-archived-filter"
                onChange={(event) => updateParam('archived', event.target.checked ? 'true' : null)}
              />
              {t('projects.filter.archived')}
            </label>
            <label className="mesh-projects__check">
              <input
                type="checkbox"
                checked={mineOnly}
                data-testid="projects-mine-filter"
                onChange={(event) => updateParam('mine', event.target.checked ? 'true' : null)}
              />
              {t('projects.filter.mine')}
            </label>
            <div
              className="mesh-projects__view-toggle"
              role="group"
              aria-label={t('board.viewLayoutLabel')}
            >
              <Button
                size="sm"
                variant={viewMode === 'list' ? 'secondary' : 'ghost'}
                aria-pressed={viewMode === 'list'}
                data-testid="projects-view-list"
                onClick={() => updateParam('view', null)}
              >
                <Icon name="list" size={16} />
                {t('board.layout.list')}
              </Button>
              <Button
                size="sm"
                variant={viewMode === 'grid' ? 'secondary' : 'ghost'}
                aria-pressed={viewMode === 'grid'}
                data-testid="projects-view-grid"
                onClick={() => updateParam('view', 'grid')}
              >
                <Icon name="board" size={16} />
                {t('board.layout.board')}
              </Button>
            </div>
          </div>
        }
        footer={
          nextCursor !== null ? (
            <Button
              variant="secondary"
              data-testid="projects-load-more"
              disabled={isFetchingMore}
              onClick={handleLoadMore}
            >
              {t('projects.loadMore')}
            </Button>
          ) : undefined
        }
      >
        {workspace === null && !isLoading && error === null ? (
          <EmptyState title={t('state.emptyTitle')} description={t('projects.noWorkspace')} />
        ) : error !== null ? (
          <ErrorState
            title={t('state.errorTitle')}
            description={error}
            retryLabel={t('common.retry')}
            onRetry={() => setReloadKey((key) => key + 1)}
          />
        ) : isLoading ? (
          <Skeleton loadingLabel={t('common.loading')} />
        ) : projects.length === 0 ? (
          <EmptyState
            illustration={<EmptyFolder />}
            title={t('onboarding.empty.projects.title')}
            description={t('onboarding.empty.projects.description')}
            action={
              <Button
                variant="primary"
                data-testid="projects-empty-create"
                onClick={() => setCreateOpen(true)}
              >
                {t('onboarding.empty.projects.action')}
              </Button>
            }
          />
        ) : (
          <ul
            className={`mesh-projects__view mesh-projects__view--${viewMode}`}
            data-testid="projects-view"
            aria-label={t('projects.title')}
          >
            {projects.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                onHealthClick={(p) => setHealthTarget(p)}
              />
            ))}
          </ul>
        )}
      </DataView>

      {workspace !== null ? (
        <CreateProjectDialog
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          client={client}
          workspaceId={workspace.workspace_id}
          onCreated={(projectId) => {
            setReloadKey((key) => key + 1);
            navigate(`/projects/${projectId}`); // §4.3 创建后进入新项目
          }}
        />
      ) : null}

      {workspace !== null && healthTarget !== null ? (
        <HealthUpdateDialog
          open
          onClose={() => setHealthTarget(null)}
          client={client}
          projectId={healthTarget.id}
          onSaved={() => {
            setHealthTarget(null);
            setReloadKey((key) => key + 1);
          }}
        />
      ) : null}
    </main>
  );
}
