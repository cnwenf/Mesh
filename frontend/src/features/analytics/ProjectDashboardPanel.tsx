/**
 * 项目仪表盘页签(analytics.md §4.2):velocity + burndown + cycle time,
 * 时间范围预设切换即重查。burndown 的 count/points 切换单独重查该端点。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { EmptyState, ErrorState, Skeleton } from '../../design';
import { useT } from '../../i18n';
import { fetchBurndown, fetchProjectDashboard } from './api';
import { GroupedBarChart, LineChart } from './charts';
import { formatDurationSeconds, windowEndIso, windowStartIso } from './format';
import type { BurndownData, BurndownMetric, ProjectDashboardData } from './types';
import './analytics.css';

const RANGE_PRESETS = [30, 90] as const;
type RangeDays = (typeof RANGE_PRESETS)[number];

export interface ProjectDashboardPanelProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly projectId: string;
}

export function ProjectDashboardPanel(props: ProjectDashboardPanelProps): React.JSX.Element {
  const { client, workspaceId, projectId } = props;
  const t = useT();
  const [data, setData] = useState<ProjectDashboardData | null>(null);
  const [burndownOverride, setBurndownOverride] = useState<BurndownData | null>(null);
  const [metric, setMetric] = useState<BurndownMetric>('points');
  const [rangeDays, setRangeDays] = useState<RangeDays>(30);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(() => {
    setIsLoading(true);
    setError(null);
    const now = new Date();
    fetchProjectDashboard(client, workspaceId, projectId, {
      from: windowStartIso(rangeDays, now),
      to: windowEndIso(now),
    })
      .then((result) => {
        setData(result);
        setBurndownOverride(null);
      })
      .catch(() => setError(t('analytics.state.error')))
      .finally(() => setIsLoading(false));
  }, [client, workspaceId, projectId, rangeDays, t]);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  // count/points 切换:单独重查 burndown(保持其 scope)
  useEffect(() => {
    if (data === null || data.burndown === null) return;
    if (data.burndown.metric === metric) return;
    let cancelled = false;
    fetchBurndown(client, workspaceId, {
      cycleId: data.burndown.scope.type === 'cycle' ? data.burndown.scope.id : undefined,
      milestoneId: data.burndown.scope.type === 'milestone' ? data.burndown.scope.id : undefined,
      metric,
    })
      .then((result) => {
        if (!cancelled) setBurndownOverride(result);
      })
      .catch(() => {
        /* 保留原曲线;失败不打断整页 */
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, data, metric]);

  if (isLoading) {
    return <Skeleton loadingLabel={t('analytics.state.loading')} className="mesh-analytics__card" />;
  }
  if (error !== null || data === null) {
    return (
      <ErrorState
        title={t('analytics.state.errorTitle')}
        description={error ?? undefined}
        retryLabel={t('analytics.state.retry')}
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    );
  }

  const burndown = burndownOverride ?? data.burndown;
  const velocityGroups = data.velocity.cycles.map((cycle) => ({
    label: cycle.name,
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

  const burndownSeries =
    burndown !== null
      ? [
          {
            name: t('analytics.burndown.ideal'),
            colorToken: 'neutral' as const,
            dashed: true,
            points: burndown.ideal.map((p, i) => ({ x: i, y: p.remaining })),
          },
          {
            name: t('analytics.burndown.actual'),
            colorToken: 'info' as const,
            points: burndown.actual.map((p, i) => ({ x: i, y: p.remaining })),
          },
        ]
      : [];

  return (
    <div className="mesh-analytics__grid-layout" data-testid="project-dashboard">
      <div className="mesh-analytics__toolbar">
        <label>
          {t('analytics.range.label')}
          <select
            value={String(rangeDays)}
            data-testid="project-dashboard-range"
            onChange={(e) => setRangeDays(Number(e.target.value) as RangeDays)}
          >
            {RANGE_PRESETS.map((d) => (
              <option key={d} value={d}>
                {t('analytics.range.days', { count: d })}
              </option>
            ))}
          </select>
        </label>
      </div>

      <section className="mesh-analytics__card" data-testid="project-dashboard-velocity">
        <h2 className="mesh-analytics__card-title">{t('analytics.velocity.title')}</h2>
        {velocityGroups.length === 0 ? (
          <EmptyState title={t('analytics.state.noData')} />
        ) : (
          <GroupedBarChart groups={velocityGroups} ariaLabel={t('analytics.velocity.chartAria')} />
        )}
        <p className="mesh-analytics__card-note">
          {t('analytics.caliber.currentAttribution')}
        </p>
      </section>

      <section className="mesh-analytics__card" data-testid="project-dashboard-burndown">
        <h2 className="mesh-analytics__card-title">{t('analytics.burndown.title')}</h2>
        {burndown === null ? (
          <EmptyState title={t('analytics.burndown.noScope')} />
        ) : (
          <>
            <div className="mesh-analytics__toolbar">
              <label>
                {t('analytics.burndown.metricLabel')}
                <select
                  value={metric}
                  data-testid="project-dashboard-metric"
                  onChange={(e) => setMetric(e.target.value as BurndownMetric)}
                >
                  <option value="points">{t('analytics.burndown.points')}</option>
                  <option value="count">{t('analytics.burndown.count')}</option>
                </select>
              </label>
              <span className="mesh-analytics__card-note">
                {t('analytics.burndown.total', { total: burndown.total })}
              </span>
            </div>
            <LineChart
              series={burndownSeries}
              xLabels={burndown.ideal.map((p) => p.date.slice(5))}
              ariaLabel={t('analytics.burndown.chartAria')}
            />
            <div className="mesh-analytics__legend">
              <span className="mesh-analytics__legend-item">
                <span
                  className="mesh-analytics__legend-swatch mesh-analytics__legend-swatch--dashed"
                  style={{ borderTopColor: 'var(--color-text-muted)' }}
                />
                {t('analytics.burndown.ideal')}
              </span>
              <span className="mesh-analytics__legend-item">
                <span
                  className="mesh-analytics__legend-swatch"
                  style={{ borderTopColor: 'var(--color-info)' }}
                />
                {t('analytics.burndown.actual')}
              </span>
            </div>
            <p className="mesh-analytics__card-note">
              {t('analytics.caliber.currentAttribution')}
            </p>
          </>
        )}
      </section>

      <section className="mesh-analytics__card" data-testid="project-dashboard-cycletime">
        <h2 className="mesh-analytics__card-title">{t('analytics.cycleTime.title')}</h2>
        <div className="mesh-analytics__kpi-row">
          <div className="mesh-analytics__kpi">
            <p className="mesh-analytics__kpi-label">{t('analytics.cycleTime.p50')}</p>
            <p className="mesh-analytics__kpi-value">
              {formatDurationSeconds(data.cycle_time.p50_seconds)}
            </p>
          </div>
          <div className="mesh-analytics__kpi">
            <p className="mesh-analytics__kpi-label">{t('analytics.cycleTime.p90')}</p>
            <p className="mesh-analytics__kpi-value">
              {formatDurationSeconds(data.cycle_time.p90_seconds)}
            </p>
          </div>
          <div className="mesh-analytics__kpi">
            <p className="mesh-analytics__kpi-label">{t('analytics.cycleTime.sample')}</p>
            <p className="mesh-analytics__kpi-value">{data.cycle_time.sample_size}</p>
          </div>
        </div>
        {data.cycle_time.meta.insufficient_data > 0 ? (
          <p className="mesh-analytics__card-note" data-testid="project-dashboard-insufficient">
            {t('analytics.cycleTime.insufficient', {
              count: data.cycle_time.meta.insufficient_data,
            })}
          </p>
        ) : null}
      </section>
    </div>
  );
}
