/**
 * 首页 —— 真实产品首页 / 工作区仪表盘(MES-107 去脚手架化)。
 *
 * 数据全部来自真实 API(README §6.14 包络):
 * 1. GET /api/v1/users/me → 问候语 + 工作区列表(memberships);
 * 2. 活跃工作区(首个成员身份)issue 仪表盘:listIssues 游标分页(§6.14 keyset)+
 *    workspace:{ws}:issues 频道实时增量合并(issue.md §3.6,不整页刷新)+ 快捷创建;
 * 3. 三态齐备(README §6.12):loading 骨架 / error 具名错误 + 重试 / empty 空态;
 * 4. 无成员身份 → 空态 + 创建工作区向导入口(workspace.md §4.2)。
 */
import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router';
import { errorToI18nKey, getApiClient, MeshApiError } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button, EmptyState, ErrorState, Input, Skeleton, useToast } from '../../design';
import { useT } from '../../i18n';
import { createIssue, listIssues, workspaceIssuesChannel } from '../../features/issues/api';
import { applyIssueListFrame } from '../../features/issues/realtime';
import type { IssueSummary } from '../../features/issues/types';
import { activeWorkspace, fetchMe } from '../../features/members/api';
import type { MeResponse, Membership } from '../../features/members/types';
import { CreateWorkspaceWizard } from '../../workspace/CreateWorkspaceWizard';
import { useRealtimeContext } from '../AppShell';

const DASHBOARD_PAGE_SIZE = 5;

export interface HomePageProps {
  client?: MeshApiClient;
}

export function HomePage(props: HomePageProps): React.JSX.Element {
  const client = props.client ?? getApiClient();
  const t = useT();
  const [me, setMe] = useState<MeResponse | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((result) => {
        if (!cancelled) setMe(result);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.network');
      });
    return () => {
      cancelled = true;
    };
  }, [client, reloadKey]);

  const handleRetry = useCallback((): void => {
    setErrorKey(null);
    setMe(null);
    setReloadKey((key) => key + 1);
  }, []);

  if (errorKey !== null) {
    return (
      <div className="mesh-page mesh-home" data-testid="home-error">
        <ErrorState
          title={t('state.errorTitle')}
          description={t(errorKey)}
          retryLabel={t('common.retry')}
          onRetry={handleRetry}
        />
      </div>
    );
  }

  if (me === null) {
    return (
      <div className="mesh-page mesh-home" data-testid="home-loading">
        <Skeleton loadingLabel={t('state.loading')} />
      </div>
    );
  }

  return <HomeContent me={me} client={client} />;
}

interface HomeContentProps {
  me: MeResponse;
  client: MeshApiClient;
}

function HomeContent(props: HomeContentProps): React.JSX.Element {
  const { me, client } = props;
  const t = useT();
  const [wizardOpen, setWizardOpen] = useState(false);
  const active = activeWorkspace(me.memberships);
  const displayName = me.user.display_name !== '' ? me.user.display_name : me.user.email;

  const openWizard = useCallback(() => setWizardOpen(true), []);
  const closeWizard = useCallback(() => setWizardOpen(false), []);

  return (
    <div className="mesh-page mesh-home">
      <header className="mesh-home__hero">
        <h1 className="mesh-page__title" data-testid="home-greeting">
          {t('home.greeting', { name: displayName })}
        </h1>
        <p className="mesh-home__subtitle">{t('home.subtitle')}</p>
      </header>

      <section className="mesh-home__section" aria-label={t('home.workspacesTitle')}>
        <h2 className="mesh-home__heading">{t('home.workspacesTitle')}</h2>
        {me.memberships.length === 0 ? (
          <div className="mesh-home__empty" data-testid="home-no-workspaces">
            <EmptyState
              title={t('home.noWorkspacesTitle')}
              description={t('home.noWorkspacesDescription')}
            />
            <Button data-testid="home-create-workspace" onClick={openWizard}>
              {t('home.createWorkspace')}
            </Button>
          </div>
        ) : (
          <ul className="mesh-home__workspace-list" data-testid="home-workspace-list">
            {me.memberships.map((membership) => (
              <WorkspaceCard key={membership.workspace_id} membership={membership} />
            ))}
          </ul>
        )}
      </section>

      {active !== null ? (
        <IssueFeedSection
          client={client}
          workspaceId={active.workspace_id}
          workspaceName={active.workspace_name}
        />
      ) : null}

      <CreateWorkspaceWizard open={wizardOpen} onClose={closeWizard} client={client} />
    </div>
  );
}

interface WorkspaceCardProps {
  membership: Membership;
}

function WorkspaceCard(props: WorkspaceCardProps): React.JSX.Element {
  const t = useT();
  const { membership } = props;
  return (
    <li
      className="mesh-home__workspace"
      data-testid={'home-workspace-' + membership.workspace_slug}
    >
      <Link className="mesh-home__workspace-link" to={'/w/' + membership.workspace_slug}>
        <span className="mesh-home__workspace-name">{membership.workspace_name}</span>
        <span className="mesh-home__workspace-meta">
          {membership.workspace_slug}
          {' · '}
          {t('roles.' + membership.role)}
        </span>
      </Link>
    </li>
  );
}

interface IssueFeedSectionProps {
  client: MeshApiClient;
  workspaceId: string;
  workspaceName: string;
}

/** 帧归属判定(§6.7 可见性水位):仅合并本工作区的 issue。 */
function belongsToWorkspace(workspaceId: string): (issue: IssueSummary) => boolean {
  return (issue) => issue.workspace_id === workspaceId;
}

function IssueFeedSection(props: IssueFeedSectionProps): React.JSX.Element {
  const { client, workspaceId, workspaceName } = props;
  const t = useT();
  const realtime = useRealtimeContext();
  const { addToast } = useToast();
  // null = 首载未完成(骨架);[] = 已加载且为空(空态)
  const [issues, setIssues] = useState<readonly IssueSummary[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [feedErrorKey, setFeedErrorKey] = useState<string | null>(null);
  const [feedReloadKey, setFeedReloadKey] = useState(0);
  const [isCreating, setIsCreating] = useState(false);
  const [newTitle, setNewTitle] = useState('');

  useEffect(() => {
    let cancelled = false;
    setFeedErrorKey(null);
    setIssues(null);
    listIssues(client, workspaceId, {
      limit: DASHBOARD_PAGE_SIZE,
      sort: 'created_at',
      order: 'desc',
    })
      .then((page) => {
        if (cancelled) return;
        setIssues([...page.data]);
        setNextCursor(page.nextCursor);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setFeedErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.network');
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, feedReloadKey]);

  const retryFeed = useCallback((): void => {
    setFeedReloadKey((key) => key + 1);
  }, []);

  // 实时增量合并(issue.md §3.6 / README §6.7):按 id 合并,不整页刷新。
  useEffect(() => {
    if (realtime === null) return;
    const channel = workspaceIssuesChannel(workspaceId);
    const belongs = belongsToWorkspace(workspaceId);
    realtime.client.subscribe(channel);
    const unsubscribeFrame = realtime.client.onFrame((frame) => {
      setIssues((prev) => (prev === null ? prev : applyIssueListFrame(prev, frame, belongs)));
    });
    return () => {
      unsubscribeFrame();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspaceId]);

  const reportError = useCallback(
    (error: unknown): void => {
      const key = error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown';
      addToast(t(key), { tone: 'danger', closeLabel: t('a11y.dismiss') });
    },
    [addToast, t],
  );

  const handleLoadMore = async (): Promise<void> => {
    if (nextCursor === null) return;
    try {
      const page = await listIssues(client, workspaceId, {
        limit: DASHBOARD_PAGE_SIZE,
        sort: 'created_at',
        order: 'desc',
        cursor: nextCursor,
      });
      setIssues((prev) => {
        if (prev === null) return prev;
        const seen = new Set(prev.map((issue) => issue.id));
        return [...prev, ...page.data.filter((issue) => !seen.has(issue.id))];
      });
      setNextCursor(page.nextCursor);
    } catch (error) {
      reportError(error);
    }
  };

  const handleCreate = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const title = newTitle.trim();
    if (title.length === 0) return;
    setIsCreating(true);
    try {
      const created = await createIssue(client, workspaceId, { title });
      // created 帧与本响应可能先后到达:按 id 去重,避免重复行。
      setIssues((prev) =>
        prev === null || prev.some((issue) => issue.id === created.id)
          ? prev
          : [...prev, created],
      );
      setNewTitle('');
    } catch (error) {
      reportError(error);
    } finally {
      setIsCreating(false);
    }
  };

  const sectionTitle = t('home.dashboardTitle', { workspace: workspaceName });

  return (
    <section
      className="mesh-home__section"
      aria-label={sectionTitle}
      data-testid="home-dashboard"
    >
      <h2 className="mesh-home__heading">{sectionTitle}</h2>

      {feedErrorKey !== null ? (
        <ErrorState
          title={t('state.errorTitle')}
          description={t(feedErrorKey)}
          retryLabel={t('common.retry')}
          onRetry={retryFeed}
        />
      ) : null}

      {feedErrorKey === null && issues === null ? <Skeleton loadingLabel={t('state.loading')} /> : null}

      {feedErrorKey === null && issues !== null ? (
        <div className="mesh-home__feed">
          <form className="mesh-home__row" onSubmit={(event) => void handleCreate(event)}>
            <Input
              data-testid="home-new-title"
              label={t('home.quickCreateLabel')}
              value={newTitle}
              onChange={(event) => setNewTitle(event.target.value)}
            />
            <Button data-testid="home-create" type="submit" isLoading={isCreating}>
              {t('common.create')}
            </Button>
          </form>

          {issues.length === 0 ? (
            <EmptyState
              title={t('home.feedEmptyTitle')}
              description={t('home.feedEmptyDescription')}
            />
          ) : (
            <ul className="mesh-home__issue-list" data-testid="home-issue-list">
              {issues.map((issue) => (
                <li
                  key={issue.id}
                  className="mesh-home__issue"
                  data-testid={'home-issue-' + issue.identifier}
                >
                  <Link className="mesh-home__issue-link" to={'/issues/' + issue.id}>
                    <span className="mesh-home__issue-key">{issue.identifier}</span>
                    <span className="mesh-home__issue-title">{issue.title}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {nextCursor !== null ? (
            <Button
              data-testid="home-load-more"
              variant="secondary"
              onClick={() => void handleLoadMore()}
            >
              {t('home.loadMore')}
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
