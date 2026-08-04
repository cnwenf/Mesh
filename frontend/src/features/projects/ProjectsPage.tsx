/**
 * 项目列表页(project.md §4.1):筛选(状态 / 已归档 / 我参与的,URL 同源)+
 * 紧凑表格(名称 / 状态徽章 / 健康度灯 / 进度条 / 负责人 / 目标日)+ 游标 Load more。
 * 实时经 workspace:{ws}:projects 频道按可见性水位合并(§3.5/§6.7)。
 * 状态渲染序:无工作区空态 → 错误态(可重试)→ 骨架 → 空态 → 内容(对齐 MembersPage)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import {
  Button,
  Checkbox,
  DataTableSurface,
  DataView,
  EmptyState,
  ErrorState,
  Select,
  Skeleton,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { useOptionalWorkspace } from '../../workspace/WorkspaceProvider';
import { fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import { EmptyFolder } from '../onboarding/illustrations';
import { listProjects, workspaceProjectsChannel } from './api';
import { CreateProjectDialog } from './CreateProjectDialog';
import { HealthUpdateDialog } from './HealthUpdateDialog';
import { applyProjectListFrame } from './realtime';
import { projectRoute, resolveProjectWorkspace } from './routing';
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

interface ProjectRowProps {
  readonly project: ProjectSummary;
  readonly workspaceSlug: string;
  readonly onHealthClick: (project: ProjectSummary) => void;
}

function ProjectRow(props: ProjectRowProps): React.JSX.Element {
  const t = useT();
  const { project, workspaceSlug, onHealthClick } = props;
  const total = project.done_issues + project.open_issues;
  const progressTitle = t('projects.card.progress', { done: project.done_issues, total });
  return (
    <tr className="mesh-projects__row" data-testid={`project-card-${project.id}`}>
      <td className="mesh-projects__cell mesh-projects__cell--name">
        <div className="mesh-projects__identity">
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
          <Link to={projectRoute(workspaceSlug, project.id)} className="mesh-projects__name">
            {project.name}
          </Link>
        </div>
      </td>
      <td className="mesh-projects__cell mesh-projects__cell--status">
        <StatusBadge status={project.status} label={t(`projects.status.${project.status}`)} />
      </td>
      <td className="mesh-projects__cell mesh-projects__cell--health">
        <HealthIndicator
          health={project.health}
          onClick={() => onHealthClick(project)}
          testId={`project-health-${project.id}`}
        />
      </td>
      <td className="mesh-projects__cell mesh-projects__cell--progress">
        <div className="mesh-projects__progress-cell">
          <ProgressBar progress={project.progress} title={progressTitle} />
          <span className="mesh-projects__progress-label">
            {project.done_issues}/{total}
          </span>
        </div>
      </td>
      <td className="mesh-projects__cell mesh-projects__cell--lead">
        {project.lead !== null ? (
          <span className="mesh-projects__lead">
            <AvatarInitial
              name={project.lead.name}
              accessibleName={project.lead.name}
              kind={project.lead.member_type}
              size={20}
            />
            <span>{project.lead.name}</span>
          </span>
        ) : (
          <span className="mesh-projects__cell-empty">{t('projects.settings.leadNone')}</span>
        )}
      </td>
      <td className="mesh-projects__cell mesh-projects__cell--date">
        {project.target_date !== null ? (
          <span className="mesh-projects__date" data-testid={`project-date-${project.id}`}>
            {t('projects.card.due', { date: new Date(project.target_date) })}
          </span>
        ) : (
          <span className="mesh-projects__cell-empty" aria-hidden="true">
            —
          </span>
        )}
      </td>
    </tr>
  );
}

export function ProjectsPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const { workspaceSlug } = useParams<{ workspaceSlug?: string }>();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();
  const workspaceContext = useOptionalWorkspace();
  const hasWorkspaceContext = workspaceContext !== null;
  const workspaceContextStatus = workspaceContext?.status ?? null;
  const contextWorkspace = workspaceContext?.workspace ?? null;

  const providerWorkspace = useMemo<Membership | null>(() => {
    if (workspaceContextStatus !== 'ready' || contextWorkspace === null) return null;
    return {
      workspace_id: contextWorkspace.id,
      workspace_name: contextWorkspace.name,
      workspace_slug: contextWorkspace.slug,
      role: contextWorkspace.my_role,
      status: 'active',
      joined_at: null,
    };
  }, [workspaceContextStatus, contextWorkspace]);

  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get('status') ?? STATUS_ALL;
  const showArchived = searchParams.get('archived') === 'true';
  const mineOnly = searchParams.get('mine') === 'true';

  const [standaloneWorkspace, setStandaloneWorkspace] = useState<Membership | null>(null);
  const [standaloneWorkspaceResolved, setStandaloneWorkspaceResolved] = useState(false);
  const workspace = hasWorkspaceContext ? providerWorkspace : standaloneWorkspace;
  const workspaceResolved = hasWorkspaceContext
    ? workspaceContextStatus !== 'loading'
    : standaloneWorkspaceResolved;
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isFetchingMore, setIsFetchingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  /** 列表卡片健康度灯点击 → 页面级更新对话框的目标项目(§4.2 点击更新) */
  const [healthTarget, setHealthTarget] = useState<ProjectSummary | null>(null);
  /** workspace / 筛选切换时单调递增,拒绝旧列表及分页响应回写当前视图。 */
  const listGenerationRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    listGenerationRef.current += 1;
    setStandaloneWorkspace(null);
    setStandaloneWorkspaceResolved(false);
    setProjects([]);
    setNextCursor(null);
    setIsLoading(!hasWorkspaceContext || workspaceContextStatus === 'loading');
    setIsFetchingMore(false);
    setError(null);
    setCreateOpen(false);
    setHealthTarget(null);
    if (hasWorkspaceContext) {
      return () => {
        cancelled = true;
        listGenerationRef.current += 1;
      };
    }
    fetchMe(client)
      .then((me) => {
        if (!cancelled) {
          setStandaloneWorkspace(resolveProjectWorkspace(me.memberships, workspaceSlug));
        }
      })
      .catch(() => {
        if (!cancelled) setError(t('state.errorDescription'));
      })
      .finally(() => {
        if (!cancelled) setStandaloneWorkspaceResolved(true);
      });
    return () => {
      cancelled = true;
      listGenerationRef.current += 1;
    };
  }, [client, t, workspaceSlug, hasWorkspaceContext, workspaceContextStatus, contextWorkspace?.id]);

  const loadProjects = useCallback(() => {
    if (!workspaceResolved) return;
    const generation = ++listGenerationRef.current;
    setIsFetchingMore(false);
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
        if (listGenerationRef.current !== generation) return;
        setProjects([...page.data]);
        setNextCursor(page.nextCursor);
      })
      .catch((err) => {
        if (listGenerationRef.current !== generation) return;
        setError(err instanceof Error ? err.message : t('state.errorDescription'));
      })
      .finally(() => {
        if (listGenerationRef.current === generation) setIsLoading(false);
      });
  }, [client, workspace, workspaceResolved, statusFilter, showArchived, mineOnly, t]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects, reloadKey]);

  // 实时合并:belongs 同时校验当前工作区与筛选水位;ref 避免随筛选重订阅。
  const belongsRef = useRef<(project: ProjectSummary) => boolean>(() => true);
  belongsRef.current = (project) =>
    workspace !== null &&
    project.workspace_id === workspace.workspace_id &&
    matchesListFilters(project, statusFilter, showArchived);

  useEffect(() => {
    if (realtime === null || workspace === null) return;
    const channel = workspaceProjectsChannel(workspace.workspace_id);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      setProjects((prev) => applyProjectListFrame(prev, frame, belongsRef.current));
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspace]);

  const handleLoadMore = (): void => {
    if (workspace === null || nextCursor === null || isFetchingMore) return;
    const generation = listGenerationRef.current;
    setIsFetchingMore(true);
    listProjects(client, workspace.workspace_id, {
      status: statusFilter === STATUS_ALL ? undefined : (statusFilter as ProjectStatus),
      archived: showArchived,
      mine: mineOnly,
      limit: PAGE_LIMIT,
      cursor: nextCursor,
    })
      .then((page) => {
        if (listGenerationRef.current !== generation) return;
        setProjects((prev) => [...prev, ...page.data]);
        setNextCursor(page.nextCursor);
      })
      .catch(() => {
        if (listGenerationRef.current !== generation) return;
        toast.addToast(t('common.unknownError'), { tone: 'danger', closeLabel: t('common.close') });
      })
      .finally(() => {
        if (listGenerationRef.current === generation) setIsFetchingMore(false);
      });
  };

  const updateParam = (key: string, value: string | null): void => {
    const params = new URLSearchParams(searchParams);
    if (value === null) params.delete(key);
    else params.set(key, value);
    setSearchParams(params, { replace: true });
  };

  const displayError = workspaceContextStatus === 'error' ? t('state.errorDescription') : error;

  return (
    <div className="mesh-projects">
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
          ) : undefined
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
            <Checkbox
              label={t('projects.filter.archived')}
              checked={showArchived}
              className="mesh-projects__check"
              data-testid="projects-archived-filter"
              onChange={(event) => updateParam('archived', event.target.checked ? 'true' : null)}
            />
            <Checkbox
              label={t('projects.filter.mine')}
              checked={mineOnly}
              className="mesh-projects__check"
              data-testid="projects-mine-filter"
              onChange={(event) => updateParam('mine', event.target.checked ? 'true' : null)}
            />
          </div>
        }
        footer={
          !isLoading && displayError === null && projects.length > 0 && nextCursor !== null ? (
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
        {workspace === null && workspaceResolved && displayError === null ? (
          <EmptyState title={t('state.emptyTitle')} description={t('projects.noWorkspace')} />
        ) : displayError !== null ? (
          <ErrorState
            title={t('state.errorTitle')}
            description={displayError}
            retryLabel={t('common.retry')}
            onRetry={() => {
              if (workspaceContext?.status === 'error') void workspaceContext.refresh();
              else setReloadKey((key) => key + 1);
            }}
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
          <div className="mesh-projects__table-wrap">
            <DataTableSurface className="mesh-projects__table" data-testid="projects-table">
              <caption className="sr-only">{t('projects.title')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('projects.settings.name')}</th>
                  <th scope="col">{t('projects.settings.status')}</th>
                  <th scope="col">{t('projects.health.label')}</th>
                  <th scope="col">{t('dataJobs.import.step.progress')}</th>
                  <th scope="col">{t('projects.settings.lead')}</th>
                  <th scope="col">{t('projects.settings.targetDate')}</th>
                </tr>
              </thead>
              <tbody>
                {projects.map((project) => (
                  <ProjectRow
                    key={project.id}
                    project={project}
                    workspaceSlug={workspace?.workspace_slug ?? workspaceSlug ?? ''}
                    onHealthClick={(p) => setHealthTarget(p)}
                  />
                ))}
              </tbody>
            </DataTableSurface>
          </div>
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
            navigate(projectRoute(workspace.workspace_slug, projectId));
          }}
        />
      ) : null}

      {workspace !== null &&
      healthTarget !== null &&
      healthTarget.workspace_id === workspace.workspace_id ? (
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
    </div>
  );
}
