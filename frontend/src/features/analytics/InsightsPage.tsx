/**
 * 工作区「洞察」仪表盘(analytics.md §4.3):吞吐量趋势 + workload 排行 +
 * agent 统计区。聚合按请求者项目可见性过滤,UI 给轻提示(§4.3 R3)。
 * 数据获取走 fetchWorkspaceDashboard 一次聚合端点。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import { EmptyState, ErrorState, Skeleton } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import { fetchWorkspaceDashboard } from './api';
import { LineChart } from './charts';
import { formatDurationSeconds, formatRate, rateTone, windowEndIso, windowStartIso } from './format';
import type { Granularity, WorkspaceDashboardData } from './types';
import './analytics.css';

const RANGE_PRESETS = [30, 90] as const;
type RangeDays = (typeof RANGE_PRESETS)[number];
const GRANULARITIES: readonly Granularity[] = ['day', 'week', 'month'];

export function InsightsPage(): React.JSX.Element {
  const t = useT();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [data, setData] = useState<WorkspaceDashboardData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rangeDays, setRangeDays] = useState<RangeDays>(30);
  const [granularity, setGranularity] = useState<Granularity>('day');
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((me) => {
        if (!cancelled) setWorkspace(activeWorkspace(me.memberships));
      })
      .catch(() => {
        if (!cancelled) setError(t('analytics.state.error'));
      });
    return () => {
      cancelled = true;
    };
  }, [client, t]);

  const load = useCallback(() => {
    if (workspace === null) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    const now = new Date();
    fetchWorkspaceDashboard(client, workspace.workspace_id, {
      from: windowStartIso(rangeDays, now),
      to: windowEndIso(now),
      granularity,
    })
      .then((result) => setData(result))
      .catch(() => setError(t('analytics.state.error')))
      .finally(() => setIsLoading(false));
  }, [client, workspace, rangeDays, granularity, t]);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  if (isLoading) {
    return <Skeleton loadingLabel={t('analytics.state.loading')} className="mesh-analytics__card" />;
  }
  if (error !== null) {
    return (
      <ErrorState
        title={t('analytics.state.errorTitle')}
        description={error}
        retryLabel={t('analytics.state.retry')}
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    );
  }
  if (workspace === null || data === null) {
    return <EmptyState title={t('analytics.state.empty')} />;
  }

  const throughput = data.throughput;
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
    <main className="mesh-page">
      <h1 className="mesh-page__title">{t('analytics.insights.title')}</h1>
      {data.meta.visibility_filtered ? (
        <p className="mesh-analytics__visibility-note" data-testid="insights-visibility-note">
          {t('analytics.insights.visibilityNote')}
        </p>
      ) : null}
      <div className="mesh-analytics__toolbar">
        <label>
          {t('analytics.range.label')}
          <select
            value={String(rangeDays)}
            data-testid="insights-range"
            onChange={(e) => setRangeDays(Number(e.target.value) as RangeDays)}
          >
            {RANGE_PRESETS.map((d) => (
              <option key={d} value={d}>
                {t('analytics.range.days', { count: d })}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('analytics.granularity.label')}
          <select
            value={granularity}
            data-testid="insights-granularity"
            onChange={(e) => setGranularity(e.target.value as Granularity)}
          >
            {GRANULARITIES.map((g) => (
              <option key={g} value={g}>
                {t(`analytics.granularity.${g}`)}
              </option>
            ))}
          </select>
        </label>
        <span className="mesh-analytics__card-note">
          {t('analytics.tz.echo', { tz: throughput.meta.calendar_timezone })}
        </span>
      </div>

      <div className="mesh-analytics__grid-layout">
        <section className="mesh-analytics__card" data-testid="insights-throughput">
          <h2 className="mesh-analytics__card-title">{t('analytics.throughput.title')}</h2>
          {throughput.series.length === 0 ? (
            <EmptyState title={t('analytics.state.noData')} />
          ) : (
            <>
              <LineChart
                series={throughputSeries}
                xLabels={throughput.series.map((b) => b.label)}
                ariaLabel={t('analytics.throughput.chartAria')}
              />
              <div className="mesh-analytics__legend">
                <span className="mesh-analytics__legend-item">
                  <span
                    className="mesh-analytics__legend-swatch"
                    style={{ borderTopColor: 'var(--color-info)' }}
                  />
                  {t('analytics.throughput.created')}
                </span>
                <span className="mesh-analytics__legend-item">
                  <span
                    className="mesh-analytics__legend-swatch"
                    style={{ borderTopColor: 'var(--color-success)' }}
                  />
                  {t('analytics.throughput.completed')}
                </span>
              </div>
              <p className="mesh-analytics__card-note">
                {t('analytics.throughput.net', { net: throughput.meta.net_window })}
              </p>
            </>
          )}
        </section>

        <section className="mesh-analytics__card" data-testid="insights-workload">
          <h2 className="mesh-analytics__card-title">{t('analytics.workload.title')}</h2>
          {data.workload.data.length === 0 ? (
            <EmptyState title={t('analytics.state.noData')} />
          ) : (
            <table className="mesh-analytics__table">
              <thead>
                <tr>
                  <th>{t('analytics.workload.member')}</th>
                  <th>{t('analytics.workload.openIssues')}</th>
                  <th>{t('analytics.workload.running')}</th>
                  <th>{t('analytics.workload.queued')}</th>
                  <th>{t('analytics.workload.awaitingApproval')}</th>
                </tr>
              </thead>
              <tbody>
                {data.workload.data.map((row) => (
                  <tr key={row.member_id}>
                    <td>
                      {row.display_name}{' '}
                      {t(
                        row.member_type === 'agent'
                          ? 'analytics.workload.typeAgent'
                          : 'analytics.workload.typeHuman',
                      )}
                    </td>
                    <td>{row.open_issues}</td>
                    <td>{row.running ?? '—'}</td>
                    <td>{row.queued ?? '—'}</td>
                    <td>{row.awaiting_approval ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="mesh-analytics__card" data-testid="insights-agents">
          <h2 className="mesh-analytics__card-title">{t('analytics.agents.title')}</h2>
          {data.agent_stats.agents.length === 0 ? (
            <EmptyState title={t('analytics.state.noAgents')} />
          ) : (
            <div className="mesh-analytics__agent-grid">
              {data.agent_stats.agents.map((agent) => {
                const tone = rateTone(agent.success_rate);
                return (
                  <div className="mesh-analytics__agent-card" key={agent.agent_id}>
                    <p className="mesh-analytics__agent-name">{agent.display_name}</p>
                    <div className="mesh-analytics__agent-metrics">
                      <span>
                        {t('analytics.agents.successRate')}:{' '}
                        <strong className={`mesh-analytics__kpi-value--${tone}`}>
                          {formatRate(agent.success_rate)}
                        </strong>
                      </span>
                      <span>
                        {t('analytics.agents.executions')}: {agent.executions}
                      </span>
                      <span>
                        {t('analytics.agents.avgDuration')}:{' '}
                        {formatDurationSeconds(agent.avg_duration_seconds)}
                      </span>
                      <span>
                        {t('analytics.agents.retryRate')}: {formatRate(agent.retry_rate)}
                      </span>
                    </div>
                    {agent.tokens.token_coverage !== null && agent.tokens.token_coverage < 1 ? (
                      <p className="mesh-analytics__token-note">
                        {t('analytics.agents.tokenNote')}
                      </p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
