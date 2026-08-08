/**
 * 工作区首页(/w/:workspaceSlug)—— 当前工作区概览(workspace.md §4.1)。
 *
 * 呈现工作区名称/slug/我的角色/默认 locale;admin+ 提供设置入口(§6.12 角色可见性)。
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { getApiClient } from '../../api';
import type { MeshApiClient } from '../../api';
import { Badge, Icon, PageHeader, Skeleton, buttonClasses } from '../../design';
import type { BadgeTone, IconName } from '../../design';
import { listInbox } from '../../features/inbox/api';
import { isUnread } from '../../features/inbox/types';
import type { Notification } from '../../features/inbox/types';
import { listIssues } from '../../features/issues/api';
import type { IssueSummary } from '../../features/issues/types';
import { listProjects } from '../../features/projects/api';
import type { ProjectSummary } from '../../features/projects/types';
import { listWorkspaceExecutions } from '../../features/runtimes/api';
import { executionDisplayLabel } from '../../features/runtimes/executionLabel';
import type { ExecutionStatus, ExecutionSummary } from '../../features/runtimes/types';
import { useT } from '../../i18n';
import { useWorkspace, WorkspaceGate } from '../WorkspaceProvider';
import './WorkspaceHomePage.css';

const EXECUTION_STATUS_LABEL_KEY: Readonly<Record<ExecutionStatus, string>> = Object.freeze({
  queued: 'home.execStatus.queued',
  claimed: 'home.execStatus.claimed',
  running: 'home.execStatus.running',
  cancelling: 'home.execStatus.cancelling',
  awaiting_approval: 'home.execStatus.awaitingApproval',
  completed: 'home.execStatus.completed',
  failed: 'home.execStatus.failed',
  timeout: 'home.execStatus.timeout',
  cancelled: 'home.execStatus.cancelled',
});

const EXECUTION_STATUS_TONE: Readonly<Record<ExecutionStatus, BadgeTone>> = Object.freeze({
  queued: 'neutral',
  claimed: 'info',
  running: 'info',
  cancelling: 'warning',
  awaiting_approval: 'warning',
  completed: 'success',
  failed: 'danger',
  timeout: 'danger',
  cancelled: 'neutral',
});

type SummarySlot<T> =
  | { readonly status: 'loading' }
  | { readonly status: 'ready'; readonly item: T | null }
  | { readonly status: 'error' };

interface WorkspaceActivity {
  readonly project: SummarySlot<ProjectSummary>;
  readonly issue: SummarySlot<IssueSummary>;
  readonly inbox: SummarySlot<Notification>;
  readonly execution: SummarySlot<ExecutionSummary>;
}

interface SummaryPage<T> {
  readonly data: readonly T[];
}

interface ActivityItem {
  readonly to: string;
  readonly primary: string;
  readonly secondary?: string;
  readonly badge?: string;
  readonly badgeTone?: BadgeTone;
}

interface ActivityCardProps<T> {
  readonly state: SummarySlot<T>;
  readonly title: string;
  readonly icon: IconName;
  readonly sectionPath: string;
  readonly emptyLabel: string;
  readonly unavailableLabel: string;
  readonly loadingLabel: string;
  readonly testId: string;
  readonly renderItem: (item: T) => ActivityItem;
}

function loadingActivity(): WorkspaceActivity {
  return {
    project: { status: 'loading' },
    issue: { status: 'loading' },
    inbox: { status: 'loading' },
    execution: { status: 'loading' },
  };
}

function firstSlot<T>(page: SummaryPage<T>): SummarySlot<T> {
  return { status: 'ready', item: page.data[0] ?? null };
}

function ActivityCard<T>(props: ActivityCardProps<T>): React.JSX.Element {
  const {
    state,
    title,
    icon,
    sectionPath,
    emptyLabel,
    unavailableLabel,
    loadingLabel,
    testId,
    renderItem,
  } = props;
  let target = sectionPath;
  let body: React.JSX.Element;

  if (state.status === 'loading') {
    body = <Skeleton loadingLabel={loadingLabel} className="mesh-ws-home__activity-skeleton" />;
  } else if (state.status === 'error') {
    body = <span className="mesh-ws-home__activity-placeholder">{unavailableLabel}</span>;
  } else if (state.item === null) {
    body = <span className="mesh-ws-home__activity-placeholder">{emptyLabel}</span>;
  } else {
    const item = renderItem(state.item);
    target = item.to;
    body = (
      <>
        <span className="mesh-ws-home__activity-primary">{item.primary}</span>
        {item.secondary !== undefined || item.badge !== undefined ? (
          <span className="mesh-ws-home__activity-footer">
            {item.secondary !== undefined ? (
              <span className="mesh-ws-home__activity-secondary">{item.secondary}</span>
            ) : null}
            {item.badge !== undefined ? (
              <Badge tone={item.badgeTone ?? 'neutral'} icon={null}>
                {item.badge}
              </Badge>
            ) : null}
          </span>
        ) : null}
      </>
    );
  }

  return (
    <Link to={target} className="mesh-ws-home__activity-card" data-testid={testId}>
      <span className="mesh-ws-home__activity-card-header">
        <Icon name={icon} size={16} />
        <span className="mesh-ws-home__activity-card-title">{title}</span>
        <Icon name="chevron-right" size={16} className="mesh-ws-home__activity-arrow" />
      </span>
      {body}
    </Link>
  );
}

export interface WorkspaceHomePageProps {
  readonly client?: MeshApiClient;
}

export function WorkspaceHomePage(props: WorkspaceHomePageProps = {}): React.JSX.Element {
  const client = props.client ?? getApiClient();
  return (
    <WorkspaceGate>
      <WorkspaceOverview client={client} />
    </WorkspaceGate>
  );
}

function WorkspaceOverview(props: { readonly client: MeshApiClient }): React.JSX.Element {
  const { client } = props;
  const { workspace: gatedWorkspace, isAdmin } = useWorkspace();
  const t = useT();
  // WorkspaceGate 只在当前工作区已解析时渲染子树。
  const workspace = gatedWorkspace!;
  const defaultLocale =
    typeof workspace.settings.default_locale === 'string'
      ? workspace.settings.default_locale
      : 'en';
  const roleLabel = t(`roles.${workspace.my_role}`);
  const workspacePath = `/w/${encodeURIComponent(workspace.slug)}`;
  const [activity, setActivity] = useState<WorkspaceActivity>(loadingActivity);

  useEffect(() => {
    let cancelled = false;
    setActivity(loadingActivity());

    void listProjects(client, workspace.id, { limit: 1 }).then(
      (page) => {
        if (!cancelled) {
          setActivity((current) => ({ ...current, project: firstSlot(page) }));
        }
      },
      () => {
        if (!cancelled) {
          setActivity((current) => ({ ...current, project: { status: 'error' } }));
        }
      },
    );
    void listIssues(client, workspace.id, {
      sort: 'created_at',
      order: 'desc',
      limit: 1,
    }).then(
      (page) => {
        if (!cancelled) {
          setActivity((current) => ({ ...current, issue: firstSlot(page) }));
        }
      },
      () => {
        if (!cancelled) {
          setActivity((current) => ({ ...current, issue: { status: 'error' } }));
        }
      },
    );
    void listInbox(client, { workspaceId: workspace.id, limit: 1 }).then(
      (page) => {
        if (!cancelled) {
          setActivity((current) => ({ ...current, inbox: firstSlot(page) }));
        }
      },
      () => {
        if (!cancelled) {
          setActivity((current) => ({ ...current, inbox: { status: 'error' } }));
        }
      },
    );
    void listWorkspaceExecutions(client, workspace.id, { limit: 1 }).then(
      (page) => {
        if (!cancelled) {
          setActivity((current) => ({ ...current, execution: firstSlot(page) }));
        }
      },
      () => {
        if (!cancelled) {
          setActivity((current) => ({ ...current, execution: { status: 'error' } }));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [client, workspace.id]);

  const quickLinks: ReadonlyArray<{ label: string; path: string; icon: IconName; testId: string }> =
    [
      {
        label: t('nav.projects'),
        path: `${workspacePath}/projects`,
        icon: 'folder',
        testId: 'ws-quick-projects',
      },
      {
        label: t('nav.issues'),
        path: `${workspacePath}/issues`,
        icon: 'issues',
        testId: 'ws-quick-issues',
      },
      {
        label: t('nav.board'),
        path: `${workspacePath}/board`,
        icon: 'board',
        testId: 'ws-quick-board',
      },
      {
        label: t('nav.members'),
        path: `${workspacePath}/members`,
        icon: 'user',
        testId: 'ws-quick-members',
      },
    ];
  return (
    <section className="mesh-ws-home" aria-label={t('workspace.homeTitle')}>
      <div className="mesh-ws-home__page-title" data-testid="ws-home-name">
        <PageHeader title={workspace.name} />
      </div>
      <p className="mesh-ws-home__meta" data-testid="ws-home-meta">
        {t('workspace.homeSlug', { slug: workspace.slug })}
        {' · '}
        {t('workspace.homeRole', { role: roleLabel })}
        {' · '}
        {t('workspace.homeLocale', { locale: defaultLocale })}
      </p>

      <ul className="mesh-ws-home__facts">
        <li>
          <span className="mesh-ws-home__fact-label">{t('workspace.slugLabel')}</span>
          <span className="mesh-ws-home__fact-value">{workspace.slug}</span>
        </li>
        <li>
          <span className="mesh-ws-home__fact-label">{t('workspace.localeLabel')}</span>
          <span className="mesh-ws-home__fact-value">{defaultLocale}</span>
        </li>
        <li>
          <span className="mesh-ws-home__fact-label">{t('workspace.timezoneLabel')}</span>
          <span className="mesh-ws-home__fact-value">{workspace.timezone}</span>
        </li>
        <li>
          <span className="mesh-ws-home__fact-label">
            {t('roles.roleLabel', { name: workspace.name })}
          </span>
          <Badge tone={isAdmin ? 'accent' : 'neutral'}>{roleLabel}</Badge>
        </li>
      </ul>

      <section className="mesh-ws-home__activity" aria-labelledby="workspace-activity-title">
        <h2 id="workspace-activity-title" className="mesh-ws-home__section-title">
          {t('workspace.activityTitle')}
        </h2>
        <div className="mesh-ws-home__activity-grid">
          <ActivityCard
            state={activity.project}
            title={t('workspace.recentProject')}
            icon="folder"
            sectionPath={`${workspacePath}/projects`}
            emptyLabel={t('workspace.noRecentProjects')}
            unavailableLabel={t('workspace.activityUnavailable')}
            loadingLabel={t('common.loading')}
            testId="ws-activity-project"
            renderItem={(project) => ({
              to: `${workspacePath}/projects/${project.id}`,
              primary: project.name,
              secondary: `${project.key} · ${t('home.projectOpenIssues', {
                count: project.open_issues,
              })}`,
            })}
          />
          <ActivityCard
            state={activity.issue}
            title={t('workspace.recentIssue')}
            icon="issues"
            sectionPath={`${workspacePath}/issues`}
            emptyLabel={t('workspace.noRecentIssues')}
            unavailableLabel={t('workspace.activityUnavailable')}
            loadingLabel={t('common.loading')}
            testId="ws-activity-issue"
            renderItem={(issue) => ({
              to: `${workspacePath}/issues/${issue.id}`,
              primary: `${issue.identifier} ${issue.title}`,
            })}
          />
          <ActivityCard
            state={activity.inbox}
            title={t('workspace.recentInbox')}
            icon="inbox"
            sectionPath={`${workspacePath}/inbox`}
            emptyLabel={t('workspace.noRecentInbox')}
            unavailableLabel={t('workspace.activityUnavailable')}
            loadingLabel={t('common.loading')}
            testId="ws-activity-inbox"
            renderItem={(notification) => ({
              to: `${workspacePath}/inbox/${notification.id}`,
              // 旧行/缺快照的 title 可能为 null,退回 preview 保证卡片有可读主文案。
              primary: notification.title ?? notification.preview,
              secondary: notification.preview,
              badge: isUnread(notification) ? t('inbox.filter.unread') : undefined,
              badgeTone: 'info',
            })}
          />
          <ActivityCard
            state={activity.execution}
            title={t('workspace.recentRun')}
            icon="activity"
            sectionPath={`${workspacePath}/runtimes`}
            emptyLabel={t('workspace.noRecentRuns')}
            unavailableLabel={t('workspace.activityUnavailable')}
            loadingLabel={t('common.loading')}
            testId="ws-activity-execution"
            renderItem={(execution) => ({
              to: `${workspacePath}/executions/${execution.id}`,
              primary: executionDisplayLabel(t, execution),
              badge: t(EXECUTION_STATUS_LABEL_KEY[execution.status]),
              badgeTone: EXECUTION_STATUS_TONE[execution.status],
            })}
          />
        </div>
      </section>

      <nav className="mesh-ws-home__quick-nav" aria-label={t('workspace.homeTitle')}>
        <div className="mesh-ws-home__quick-grid">
          {quickLinks.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className="mesh-ws-home__quick-link"
              data-testid={item.testId}
            >
              <Icon name={item.icon} size={20} />
              <span>{item.label}</span>
              <Icon name="chevron-right" size={16} className="mesh-ws-home__quick-arrow" />
            </Link>
          ))}
        </div>
      </nav>

      {isAdmin ? (
        <Link
          to={`${workspacePath}/settings`}
          className={buttonClasses('secondary', 'md', 'mesh-ws-home__settings-link')}
          data-testid="ws-settings-link"
        >
          <Icon name="settings" size={16} />
          {t('workspace.settingsEntry')}
        </Link>
      ) : (
        <p className="mesh-ws-home__hint">{t('workspace.settingsHiddenHint')}</p>
      )}
    </section>
  );
}
