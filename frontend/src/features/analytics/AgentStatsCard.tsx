/**
 * agent 详情统计卡(analytics.md §4.4):成功率/平均时长/重试率/超时率 KPI +
 * token 覆盖标注(仅 autopilot 触发执行有 token,§2.3 口径诚实披露)。
 * 内嵌于 agent 详情页 overview 页签——成员名册深链为唯一入口(§6.12)。
 */
import { useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { Skeleton } from '../../design';
import { useT } from '../../i18n';
import { fetchAgentStats } from './api';
import { formatDurationSeconds, formatRate, rateTone, windowEndIso, windowStartIso } from './format';
import type { AgentStatsRow } from './types';
import './analytics.css';

export interface AgentStatsCardProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly agentId: string;
}

function isSingleStats(value: AgentStatsRow | { agents: unknown }): value is AgentStatsRow {
  return (value as AgentStatsRow).agent_id !== undefined;
}

export function AgentStatsCard(props: AgentStatsCardProps): React.JSX.Element {
  const { client, workspaceId, agentId } = props;
  const t = useT();
  const [stats, setStats] = useState<AgentStatsRow | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    const now = new Date();
    fetchAgentStats(client, workspaceId, {
      agentId,
      from: windowStartIso(30, now),
      to: windowEndIso(now),
    })
      .then((result) => {
        if (cancelled) return;
        setStats(isSingleStats(result) ? result : null);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : t('analytics.state.error'));
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, agentId, t]);

  if (isLoading) {
    return <Skeleton loadingLabel={t('analytics.state.loading')} className="mesh-analytics__card" />;
  }
  if (error !== null) {
    // private agent 不可见等:静默提示,不泄露统计存在性(§3.4 agent_not_visible)
    return (
      <section className="mesh-analytics__card" data-testid="agent-stats-card">
        <p className="mesh-analytics__card-note">{t('analytics.agents.unavailable')}</p>
      </section>
    );
  }
  if (stats === null) {
    return (
      <section className="mesh-analytics__card" data-testid="agent-stats-card">
        <p className="mesh-analytics__card-note">{t('analytics.state.noData')}</p>
      </section>
    );
  }

  const tone = rateTone(stats.success_rate);
  const coverage = stats.tokens.token_coverage;

  return (
    <section className="mesh-analytics__card" data-testid="agent-stats-card">
      <h2 className="mesh-analytics__card-title">{t('analytics.agents.cardTitle')}</h2>
      <div className="mesh-analytics__kpi-row">
        <div className="mesh-analytics__kpi">
          <p className="mesh-analytics__kpi-label">{t('analytics.agents.successRate')}</p>
          <p className={`mesh-analytics__kpi-value mesh-analytics__kpi-value--${tone}`}>
            {formatRate(stats.success_rate)}
          </p>
        </div>
        <div className="mesh-analytics__kpi">
          <p className="mesh-analytics__kpi-label">{t('analytics.agents.avgDuration')}</p>
          <p className="mesh-analytics__kpi-value">
            {formatDurationSeconds(stats.avg_duration_seconds)}
          </p>
        </div>
        <div className="mesh-analytics__kpi">
          <p className="mesh-analytics__kpi-label">{t('analytics.agents.retryRate')}</p>
          <p className="mesh-analytics__kpi-value">{formatRate(stats.retry_rate)}</p>
        </div>
        <div className="mesh-analytics__kpi">
          <p className="mesh-analytics__kpi-label">{t('analytics.agents.timeoutRate')}</p>
          <p className="mesh-analytics__kpi-value">{formatRate(stats.timeout_rate)}</p>
        </div>
      </div>
      <p className="mesh-analytics__card-note" data-testid="agent-stats-executions">
        {t('analytics.agents.executionsCount', { count: stats.executions })}
      </p>
      <p className="mesh-analytics__card-note">
        {t('analytics.agents.tokens', { total: stats.tokens.total_tokens })}
      </p>
      {coverage !== null && coverage < 1 ? (
        <p className="mesh-analytics__token-note" data-testid="agent-stats-token-note">
          {t('analytics.agents.tokenNote')}
        </p>
      ) : null}
    </section>
  );
}
