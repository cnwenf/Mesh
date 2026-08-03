/**
 * 项目仪表盘页签(analytics.md §4.2):velocity + burndown + cycle time,
 * 时间范围预设切换即重查。burndown 的 count/points 切换单独重查该端点。
 * 图表经 ChartFrame 统一外壳(标题 + 图例:文字 + 线型/色块,颜色非唯一信号);
 * cycle time 数值走 KPI 条(tabular + 口径 hint),insufficient 诚实标注(§4.6)。
 */
import { useEffect, useState } from 'react';
import { Navigate } from 'react-router';
import { MeshApiError, errorToI18nKey, type MeshApiClient } from '../../api';
import { Banner, Button, EmptyState, ErrorState, Select, Skeleton } from '../../design';
import { useT } from '../../i18n';
import { ChartFrame } from './ChartFrame';
import { Kpi } from './Kpi';
import { KpiStrip } from './KpiStrip';
import { fetchBurndown, fetchProjectDashboard } from './api';
import { GroupedBarChart, LineChart } from './charts';
import { formatDurationSeconds, windowEndIso, windowStartIso } from './format';
import type { BurndownData, BurndownMetric, ProjectDashboardData } from './types';
import './analytics.css';

const RANGE_PRESETS = [30, 90] as const;
type RangeDays = (typeof RANGE_PRESETS)[number];

function toApiError(error: unknown): MeshApiError {
  if (error instanceof MeshApiError) return error;
  return new MeshApiError({ status: 0, code: 'unknown', message: 'unknown error' });
}

function isPermissionError(error: MeshApiError): boolean {
  return error.status === 403 || error.code === 'forbidden' || error.code === 'project_not_visible';
}

export interface ProjectDashboardPanelProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  /** 403 恢复页使用规范 workspace slug；API scope 仍唯一使用 workspaceId。 */
  readonly workspaceSlug?: string;
  readonly projectId: string;
}

/** burndown 理想线(虚线)与实际线(实线)序列;线型即信号(§4.5)。 */
/** burndown 系列组装;调用方保证 burndown 非空(null 时呈现空态,不绘图) */
function burndownSeriesOf(
  burndown: BurndownData,
  idealLabel: string,
  actualLabel: string,
): Parameters<typeof LineChart>[0]['series'] {
  return [
    {
      name: idealLabel,
      colorToken: 'neutral' as const,
      dashed: true,
      points: burndown.ideal.map((p, i) => ({ x: i, y: p.remaining })),
    },
    {
      name: actualLabel,
      colorToken: 'info' as const,
      points: burndown.actual.map((p, i) => ({ x: i, y: p.remaining })),
    },
  ];
}

function CycleTimeDistribution(props: {
  readonly p50: number | null;
  readonly p90: number | null;
}): React.JSX.Element | null {
  const { p50, p90 } = props;
  const t = useT();
  if (p50 === null && p90 === null) return null;

  const maximum = Math.max(p50 ?? 0, p90 ?? 0, 1);
  const values = [
    { label: t('analytics.cycleTime.p50'), value: p50 },
    { label: t('analytics.cycleTime.p90'), value: p90 },
  ];

  return (
    <div
      className="mesh-analytics__quantiles"
      data-testid="project-dashboard-cycle-distribution"
      role="img"
      aria-label={t('analytics.cycleTime.distributionAria', {
        p50: formatDurationSeconds(p50),
        p90: formatDurationSeconds(p90),
      })}
    >
      {values.map((item) => (
        <div className="mesh-analytics__quantile" key={item.label}>
          <span className="mesh-analytics__quantile-label">{item.label}</span>
          <span className="mesh-analytics__quantile-track" aria-hidden="true">
            <span
              className="mesh-analytics__quantile-fill"
              style={{ inlineSize: `${((item.value ?? 0) / maximum) * 100}%` }}
            />
          </span>
          <span className="mesh-analytics__quantile-value">
            {formatDurationSeconds(item.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function ProjectDashboardPanel(props: ProjectDashboardPanelProps): React.JSX.Element {
  const { client, workspaceId, workspaceSlug, projectId } = props;
  const t = useT();
  const scopeKey = `${workspaceId}:${projectId}`;
  const [loadedDashboard, setLoadedDashboard] = useState<{
    readonly scopeKey: string;
    readonly data: ProjectDashboardData;
  } | null>(null);
  // 项目路由原地切换时绝不渲染上一项目的数据，范围刷新仍保留同 scope 的成功响应。
  const data = loadedDashboard?.scopeKey === scopeKey ? loadedDashboard.data : null;
  const [burndownOverride, setBurndownOverride] = useState<BurndownData | null>(null);
  const [metric, setMetric] = useState<BurndownMetric>('points');
  const [rangeDays, setRangeDays] = useState<RangeDays>(30);
  const [isLoading, setIsLoading] = useState(true);
  const [failure, setFailure] = useState<{
    readonly scopeKey: string;
    readonly error: MeshApiError;
  } | null>(null);
  const error = failure?.scopeKey === scopeKey ? failure.error : null;
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setFailure(null);
    const now = new Date();
    fetchProjectDashboard(client, workspaceId, projectId, {
      from: windowStartIso(rangeDays, now),
      to: windowEndIso(now),
      signal: controller.signal,
    })
      .then((result) => {
        if (controller.signal.aborted) return;
        setLoadedDashboard({ scopeKey, data: result });
        setBurndownOverride(null);
      })
      .catch((loadError: unknown) => {
        if (!controller.signal.aborted) {
          setFailure({ scopeKey, error: toApiError(loadError) });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });
    return () => controller.abort();
  }, [client, workspaceId, projectId, scopeKey, rangeDays, reloadKey]);

  // count/points 切换:单独重查 burndown(保持其 scope)
  useEffect(() => {
    if (data === null || data.burndown === null) return;
    if (data.burndown.metric === metric) return;
    const controller = new AbortController();
    fetchBurndown(client, workspaceId, {
      cycleId: data.burndown.scope.type === 'cycle' ? data.burndown.scope.id : undefined,
      milestoneId: data.burndown.scope.type === 'milestone' ? data.burndown.scope.id : undefined,
      metric,
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) setBurndownOverride(result);
      })
      .catch(() => {
        /* 保留原曲线;失败不打断整页 */
      });
    return () => controller.abort();
  }, [client, workspaceId, data, metric]);

  if (error !== null && isPermissionError(error)) {
    const workspacePath =
      workspaceSlug === undefined ? undefined : `/w/${encodeURIComponent(workspaceSlug)}`;
    const query =
      workspacePath === undefined ? '' : `?workspace=${encodeURIComponent(workspacePath)}`;
    return <Navigate to={`/forbidden${query}`} replace />;
  }
  if (data === null && (isLoading || (loadedDashboard?.scopeKey !== scopeKey && error === null))) {
    return (
      <Skeleton loadingLabel={t('analytics.state.loading')} className="mesh-analytics__card" />
    );
  }
  if (data === null) {
    return (
      <ErrorState
        title={t('analytics.state.errorTitle')}
        description={error !== null ? t(errorToI18nKey(error)) : t('analytics.state.error')}
        retryLabel={t('analytics.state.retry')}
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    );
  }

  const burndown = burndownOverride ?? data.burndown;
  const idealLabel = t('analytics.burndown.ideal');
  const actualLabel = t('analytics.burndown.actual');
  const velocityGroups = data.velocity.cycles.map((cycle) => ({
    label:
      cycle.state === 'active'
        ? t('analytics.velocity.activeCycle', { name: cycle.name })
        : cycle.name,
    emphasized: cycle.state === 'active',
    bars: [
      {
        name: t('analytics.velocity.issues'),
        value: cycle.completed_issues,
        colorToken: 'info' as const,
      },
      {
        name: t('analytics.velocity.points'),
        value: cycle.completed_points,
        colorToken: 'success' as const,
      },
    ],
  }));

  return (
    <div
      className="mesh-analytics__grid-layout"
      data-testid="project-dashboard"
      aria-busy={isLoading ? true : undefined}
    >
      {isLoading ? (
        <p className="mesh-analytics__refreshing" role="status">
          {t('analytics.state.refreshing')}
        </p>
      ) : null}
      {error !== null ? (
        <div data-testid="project-dashboard-refresh-error">
          <Banner tone="danger" politeness="assertive">
            <div className="mesh-analytics__inline-error">
              <div>
                <strong>{t('analytics.state.refreshErrorTitle')}</strong>
                <p>{t('analytics.state.refreshErrorImpact')}</p>
              </div>
              <Button variant="secondary" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
                {t('analytics.state.retry')}
              </Button>
            </div>
          </Banner>
        </div>
      ) : null}
      <div className="mesh-analytics__toolbar">
        <Select
          label={t('analytics.range.label')}
          value={String(rangeDays)}
          data-testid="project-dashboard-range"
          onChange={(e) => setRangeDays(Number(e.target.value) as RangeDays)}
        >
          {RANGE_PRESETS.map((d) => (
            <option key={d} value={d}>
              {t('analytics.range.days', { count: d })}
            </option>
          ))}
        </Select>
      </div>

      <ChartFrame
        testId="project-dashboard-velocity"
        title={t('analytics.velocity.title')}
        legend={[
          { label: t('analytics.velocity.issues'), colorToken: 'info', mark: 'bar' },
          { label: t('analytics.velocity.points'), colorToken: 'success', mark: 'bar' },
        ]}
        note={t('analytics.caliber.currentAttribution')}
      >
        {velocityGroups.length === 0 ? (
          <EmptyState title={t('analytics.state.noData')} />
        ) : (
          <GroupedBarChart groups={velocityGroups} ariaLabel={t('analytics.velocity.chartAria')} />
        )}
      </ChartFrame>

      <ChartFrame
        testId="project-dashboard-burndown"
        title={t('analytics.burndown.title')}
        legend={
          burndown !== null
            ? [
                { label: idealLabel, colorToken: 'neutral', lineStyle: 'dashed' as const },
                { label: actualLabel, colorToken: 'info', lineStyle: 'solid' as const },
              ]
            : []
        }
        note={burndown !== null ? t('analytics.caliber.currentAttribution') : undefined}
      >
        {burndown === null ? (
          <EmptyState title={t('analytics.burndown.noScope')} />
        ) : (
          <>
            <div className="mesh-analytics__toolbar">
              <Select
                label={t('analytics.burndown.metricLabel')}
                value={metric}
                data-testid="project-dashboard-metric"
                onChange={(e) => setMetric(e.target.value as BurndownMetric)}
              >
                <option value="points">{t('analytics.burndown.points')}</option>
                <option value="count">{t('analytics.burndown.count')}</option>
              </Select>
              <span className="mesh-analytics__card-note">
                <span className="mesh-tnum">
                  {t('analytics.burndown.total', { total: burndown.total })}
                </span>
              </span>
            </div>
            <LineChart
              series={burndownSeriesOf(burndown, idealLabel, actualLabel)}
              xLabels={burndown.ideal.map((p) => p.date.slice(5))}
              ariaLabel={t('analytics.burndown.chartAria')}
            />
          </>
        )}
      </ChartFrame>

      <ChartFrame testId="project-dashboard-cycletime" title={t('analytics.cycleTime.title')}>
        <KpiStrip>
          <Kpi
            label={t('analytics.cycleTime.p50')}
            value={formatDurationSeconds(data.cycle_time.p50_seconds)}
            hint={t('analytics.cycleTime.caliberHint')}
            tabular={false}
          />
          <Kpi
            label={t('analytics.cycleTime.p90')}
            value={formatDurationSeconds(data.cycle_time.p90_seconds)}
            hint={t('analytics.cycleTime.caliberHint')}
            tabular={false}
          />
          <Kpi
            label={t('analytics.cycleTime.sample')}
            value={data.cycle_time.sample_size}
            hint={t('analytics.cycleTime.caliberHint')}
          />
        </KpiStrip>
        <CycleTimeDistribution
          p50={data.cycle_time.p50_seconds}
          p90={data.cycle_time.p90_seconds}
        />
        {data.cycle_time.meta.insufficient_data > 0 ? (
          <p className="mesh-analytics__card-note" data-testid="project-dashboard-insufficient">
            {t('analytics.cycleTime.insufficient', {
              count: data.cycle_time.meta.insufficient_data,
            })}
          </p>
        ) : null}
      </ChartFrame>
    </div>
  );
}
