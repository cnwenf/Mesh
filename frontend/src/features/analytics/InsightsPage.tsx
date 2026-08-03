/**
 * 工作区「洞察」仪表盘(analytics.md §4.3,design-quality.md §3.2 Analytics 行):
 * 标题 + 口径说明(可见性/时区/粒度)→ KPI 条(窗内聚合,客户端派生不加请求)
 * → 图表网格(吞吐/workload/agent)。数字 tabular-nums;空窗/成本超限/通用错误
 * 四部分呈现;骨架与最终布局同形。聚合按请求者可见性过滤并给轻提示(§4.3 R3)。
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Banner, Button, EmptyState, ErrorState, PageHeader, Select, Toolbar } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useDocumentTitle } from '../../shell/hooks';
import { useWorkspace, WorkspaceGate } from '../../workspace/WorkspaceProvider';
import { ChartFrame } from './ChartFrame';
import { Kpi } from './Kpi';
import { KpiStrip } from './KpiStrip';
import { fetchWorkspaceDashboard } from './api';
import { LineChart } from './charts';
import { InsightsAgentsPanel, InsightsWorkloadPanel } from './InsightsPanels';
import { windowEndIso, windowStartIso } from './format';
import type { Granularity, WorkspaceDashboardData } from './types';
import './analytics.css';

const RANGE_PRESETS = [30, 90] as const;
type RangeDays = (typeof RANGE_PRESETS)[number];
const GRANULARITIES: readonly Granularity[] = ['day', 'week', 'month'];
const COST_EXCEEDED_CODE = 'query_cost_exceeded';

/** 窗内聚合 KPI:全部由已取回的仪表盘响应客户端派生,不新增后端请求。 */
export interface WindowKpis {
  readonly created: number;
  readonly completed: number;
  readonly net: number;
  readonly openIssues: number;
  readonly agentsTracked: number;
}

export function deriveWindowKpis(data: WorkspaceDashboardData): WindowKpis {
  const series = data.throughput.series;
  return {
    created: series.reduce((acc, b) => acc + b.created, 0),
    completed: series.reduce((acc, b) => acc + b.completed, 0),
    net: data.throughput.meta.net_window,
    openIssues: data.workload.data.reduce((acc, row) => acc + row.open_issues, 0),
    agentsTracked: data.agent_stats.agents.length,
  };
}

/** 整窗为空:三区块同时无数据 → 页面级空态(调整范围/新建 issue,§4.6)。 */
export function isWindowEmpty(data: WorkspaceDashboardData): boolean {
  return (
    data.throughput.series.length === 0 &&
    data.workload.data.length === 0 &&
    data.agent_stats.agents.length === 0
  );
}

/** 归一错误:非 MeshApiError 归为 unknown(映射到既有 error.unknown 文案)。 */
function toApiError(err: unknown): MeshApiError {
  if (err instanceof MeshApiError) return err;
  return new MeshApiError({ status: 0, code: 'unknown', message: 'unknown error' });
}

function diagnosticOf(err: MeshApiError): string | undefined {
  const value = err.details?.diagnostic_id;
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function isPermissionError(error: MeshApiError): boolean {
  return error.status === 403 || error.code === 'forbidden' || error.code === 'project_not_visible';
}

/** 口径回显时区:优先顶层 meta.display_timezone,回退吞吐 calendar_timezone。 */
function echoTimezone(data: WorkspaceDashboardData): string {
  return data.meta.display_timezone ?? data.throughput.meta.calendar_timezone;
}

/** 加载骨架:与最终布局同形(KPI 条 + 三张图表卡),单一 status 区。 */
function InsightsSkeleton(props: { readonly label: string }): React.JSX.Element {
  return (
    <div role="status" data-testid="insights-loading">
      <span className="sr-only">{props.label}</span>
      <div
        className="mesh-analytics__kpi-strip mesh-analytics__kpi-strip--skeleton"
        aria-hidden="true"
      >
        {[0, 1, 2, 3, 4].map((i) => (
          <span className="mesh-skeleton__shape" key={i} />
        ))}
      </div>
      <div className="mesh-analytics__charts" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span className="mesh-skeleton__shape mesh-analytics__card-skeleton" key={i} />
        ))}
      </div>
    </div>
  );
}

export function InsightsPage(): React.JSX.Element {
  const t = useT();
  useDocumentTitle(t('analytics.insights.title')); // G19 标签页标题
  return (
    <div className="mesh-page mesh-analytics__workbench">
      <PageHeader title={t('analytics.insights.title')} />
      <WorkspaceGate>
        <InsightsDashboard />
      </WorkspaceGate>
    </div>
  );
}

interface LoadedDashboard {
  readonly scopeKey: string;
  readonly data: WorkspaceDashboardData;
  readonly rangeDays: RangeDays;
  readonly granularity: Granularity;
}

/** WorkspaceGate 只在 ready 时挂载；route slug 再封住 Provider A→B 的过渡帧。 */
function InsightsDashboard(): React.JSX.Element {
  const t = useT();
  const { workspace } = useWorkspace();
  const { workspaceSlug } = useParams<{ workspaceSlug: string }>();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const routeMatchesWorkspace =
    workspace !== null && (workspaceSlug === undefined || workspace.slug === workspaceSlug);
  const scopeKey = routeMatchesWorkspace ? workspace.id : null;
  const [loadedDashboard, setLoadedDashboard] = useState<LoadedDashboard | null>(null);
  const [isDashboardLoading, setIsDashboardLoading] = useState(false);
  const [failure, setFailure] = useState<{
    readonly scopeKey: string;
    readonly error: MeshApiError;
  } | null>(null);
  const [rangeDays, setRangeDays] = useState<RangeDays>(30);
  const [granularity, setGranularity] = useState<Granularity>('day');
  const [dashboardReloadKey, setDashboardReloadKey] = useState(0);
  const data =
    scopeKey !== null && loadedDashboard?.scopeKey === scopeKey ? loadedDashboard.data : null;
  const dashboardError = scopeKey !== null && failure?.scopeKey === scopeKey ? failure.error : null;

  useEffect(() => {
    if (scopeKey === null) return;
    const controller = new AbortController();
    const requestedRange = rangeDays;
    const requestedGranularity = granularity;
    setIsDashboardLoading(true);
    setFailure(null);
    const now = new Date();
    fetchWorkspaceDashboard(client, scopeKey, {
      from: windowStartIso(requestedRange, now),
      to: windowEndIso(now),
      granularity: requestedGranularity,
      signal: controller.signal,
    })
      .then((result) => {
        if (controller.signal.aborted) return;
        setLoadedDashboard({
          scopeKey,
          data: result,
          rangeDays: requestedRange,
          granularity: requestedGranularity,
        });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setFailure({ scopeKey, error: toApiError(err) });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsDashboardLoading(false);
      });
    return () => controller.abort();
  }, [client, scopeKey, rangeDays, granularity, dashboardReloadKey]);

  if (dashboardError !== null && isPermissionError(dashboardError)) {
    const workspacePath =
      workspace !== null && routeMatchesWorkspace
        ? `/w/${encodeURIComponent(workspace.slug)}`
        : undefined;
    const query =
      workspacePath === undefined ? '' : `?workspace=${encodeURIComponent(workspacePath)}`;
    return <Navigate to={`/forbidden${query}`} replace />;
  }

  const initialLoading = scopeKey === null || (data === null && dashboardError === null);
  if (initialLoading) {
    return <InsightsSkeleton label={t('analytics.state.loading')} />;
  }

  if (dashboardError !== null && data === null) {
    const isCostExceeded = dashboardError.code === COST_EXCEEDED_CODE;
    return (
      <ErrorState
        title={t('analytics.state.errorTitle')}
        description={t(
          isCostExceeded ? 'analytics.state.costExceeded' : errorToI18nKey(dashboardError),
        )}
        impact={isCostExceeded ? undefined : t('analytics.state.errorImpact')}
        retryLabel={t('analytics.state.retry')}
        onRetry={() => setDashboardReloadKey((key) => key + 1)}
        diagnosticId={diagnosticOf(dashboardError)}
      />
    );
  }
  if (workspace === null || data === null || loadedDashboard === null) return <></>;

  const throughput = data.throughput;
  const kpis = deriveWindowKpis(data);
  const windowHint = t('analytics.kpi.windowHint', { days: loadedDashboard.rangeDays });
  const throughputSeries = [
    {
      name: t('analytics.throughput.created'),
      colorToken: 'info' as const,
      points: throughput.series.map((b, i) => ({ x: i, y: b.created })),
    },
    {
      name: t('analytics.throughput.completed'),
      colorToken: 'success' as const,
      points: throughput.series.map((b, i) => ({ x: i, y: b.completed })),
    },
  ];

  return (
    <div data-testid="insights-content" aria-busy={isDashboardLoading ? true : undefined}>
      {isDashboardLoading ? (
        <p className="mesh-analytics__refreshing" role="status" data-testid="insights-refreshing">
          {t('analytics.state.refreshing')}
        </p>
      ) : null}
      {dashboardError !== null ? (
        <div data-testid="insights-refresh-error">
          <Banner tone="danger" politeness="assertive">
            <div className="mesh-analytics__inline-error">
              <div>
                <strong>{t('analytics.state.refreshErrorTitle')}</strong>
                <p>{t('analytics.state.refreshErrorImpact')}</p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setDashboardReloadKey((key) => key + 1)}
              >
                {t('analytics.state.retry')}
              </Button>
            </div>
          </Banner>
        </div>
      ) : null}
      <div className="mesh-analytics__caliber">
        {data.meta.visibility_filtered ? (
          <p data-testid="insights-visibility-note">{t('analytics.insights.visibilityNote')}</p>
        ) : null}
        <p data-testid="insights-tz-note">{t('analytics.tzNote', { tz: echoTimezone(data) })}</p>
        <p data-testid="insights-caliber">
          {t('analytics.caliber.window', {
            days: loadedDashboard.rangeDays,
            granularity: t(`analytics.granularity.${loadedDashboard.granularity}`),
          })}
        </p>
      </div>
      <Toolbar label={t('analytics.controls.label')} className="mesh-analytics__toolbar">
        <Select
          label={t('analytics.range.label')}
          value={String(rangeDays)}
          data-testid="insights-range"
          onChange={(event) => setRangeDays(Number(event.target.value) as RangeDays)}
        >
          {RANGE_PRESETS.map((days) => (
            <option key={days} value={days}>
              {t('analytics.range.days', { count: days })}
            </option>
          ))}
        </Select>
        <Select
          label={t('analytics.granularity.label')}
          value={granularity}
          data-testid="insights-granularity"
          onChange={(event) => setGranularity(event.target.value as Granularity)}
        >
          {GRANULARITIES.map((item) => (
            <option key={item} value={item}>
              {t(`analytics.granularity.${item}`)}
            </option>
          ))}
        </Select>
      </Toolbar>

      {isWindowEmpty(data) ? (
        <EmptyState
          title={t('analytics.state.windowEmpty')}
          description={t('analytics.state.windowEmptyHint')}
          action={
            <Link className="mesh-page__link" to={`/w/${workspace.slug}/issues?create=1`}>
              {t('analytics.state.createIssue')}
            </Link>
          }
        />
      ) : (
        <>
          <KpiStrip label={t('analytics.kpi.stripLabel')}>
            <Kpi label={t('analytics.kpi.created')} value={kpis.created} hint={windowHint} />
            <Kpi
              label={t('analytics.kpi.completed')}
              value={kpis.completed}
              tone="success"
              hint={windowHint}
            />
            <Kpi
              label={t('analytics.kpi.net')}
              value={kpis.net}
              tone={kpis.net < 0 ? 'warning' : 'default'}
              hint={windowHint}
            />
            <Kpi
              label={t('analytics.kpi.openWorkload')}
              value={kpis.openIssues}
              hint={t('analytics.kpi.snapshotHint')}
            />
            <Kpi
              label={t('analytics.kpi.agentsTracked')}
              value={kpis.agentsTracked}
              hint={windowHint}
            />
          </KpiStrip>

          <div className="mesh-analytics__charts">
            <ChartFrame
              testId="insights-throughput"
              title={t('analytics.throughput.title')}
              legend={[
                { label: t('analytics.throughput.created'), colorToken: 'info' },
                { label: t('analytics.throughput.completed'), colorToken: 'success' },
              ]}
              note={t('analytics.throughput.net', { net: throughput.meta.net_window })}
            >
              {throughput.series.length === 0 ? (
                <EmptyState title={t('analytics.state.noData')} />
              ) : (
                <LineChart
                  series={throughputSeries}
                  xLabels={throughput.series.map((bucket) => bucket.label)}
                  ariaLabel={t('analytics.throughput.chartAria')}
                />
              )}
            </ChartFrame>

            <InsightsWorkloadPanel workload={data.workload} />
            <InsightsAgentsPanel agents={data.agent_stats.agents} />
          </div>
        </>
      )}
    </div>
  );
}
