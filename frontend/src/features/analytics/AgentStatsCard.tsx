/**
 * agent 详情统计卡(analytics.md §4.4):成功率/平均时长/重试率/超时率 KPI +
 * token 覆盖标注(仅 autopilot 触发执行有 token,§2.3 口径诚实披露)。
 * 内嵌于 agent 详情页 overview 页签——成员名册深链为唯一入口(§6.12)。
 */
import { useEffect, useId, useState } from 'react';
import { useIntl } from 'react-intl';
import type { MeshApiClient } from '../../api';
import { Skeleton } from '../../design';
import { useT } from '../../i18n';
import { Kpi } from './Kpi';
import { KpiStrip } from './KpiStrip';
import { fetchAgentStats } from './api';
import {
  formatDurationSeconds,
  formatRate,
  rateTone,
  windowEndIso,
  windowStartIso,
} from './format';
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

function clampRate(value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  return Math.min(1, Math.max(0, value));
}

export function AgentStatsCard(props: AgentStatsCardProps): React.JSX.Element {
  const { client, workspaceId, agentId } = props;
  const t = useT();
  const intl = useIntl();
  const outcomesTitleId = useId();
  const tokenTitleId = useId();
  const [stats, setStats] = useState<AgentStatsRow | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    const now = new Date();
    fetchAgentStats(client, workspaceId, {
      agentId,
      from: windowStartIso(30, now),
      to: windowEndIso(now),
      signal: controller.signal,
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
      controller.abort();
    };
  }, [client, workspaceId, agentId, t]);

  if (isLoading) {
    return (
      <Skeleton loadingLabel={t('analytics.state.loading')} className="mesh-analytics__card" />
    );
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
  const kpiTone = tone === 'success' ? 'success' : tone === 'warn' ? 'warning' : 'danger';
  const coverage = stats.tokens.token_coverage;
  const successRate = clampRate(stats.success_rate);
  const timeoutRate = clampRate(stats.timeout_rate);
  const failureRate =
    successRate === null || timeoutRate === null
      ? null
      : Math.max(0, 1 - successRate - timeoutRate);
  // KPI 口径:近 30 天窗(本卡固定窗口,§4.4),大数字不孤立。
  const windowHint = t('analytics.agents.windowHint');

  return (
    <section className="mesh-analytics__card" data-testid="agent-stats-card">
      <h2 className="mesh-analytics__card-title">{t('analytics.agents.cardTitle')}</h2>
      <KpiStrip>
        <Kpi
          label={t('analytics.agents.successRate')}
          value={formatRate(stats.success_rate)}
          tone={kpiTone}
          hint={windowHint}
        />
        <Kpi
          label={t('analytics.agents.avgDuration')}
          value={formatDurationSeconds(stats.avg_duration_seconds)}
          hint={windowHint}
        />
        <Kpi
          label={t('analytics.agents.retryRate')}
          value={formatRate(stats.retry_rate)}
          hint={windowHint}
        />
        <Kpi
          label={t('analytics.agents.timeoutRate')}
          value={formatRate(stats.timeout_rate)}
          hint={windowHint}
        />
      </KpiStrip>
      <p className="mesh-analytics__card-note" data-testid="agent-stats-executions">
        {t('analytics.agents.executionsCount', { count: stats.executions })}
      </p>
      <section className="mesh-analytics__outcomes" aria-labelledby={outcomesTitleId}>
        <h3 id={outcomesTitleId} className="mesh-analytics__subheading">
          {t('analytics.agents.outcomesTitle')}
        </h3>
        <div
          className="mesh-analytics__outcome-track"
          data-testid="agent-stats-outcomes"
          role="img"
          aria-label={t('analytics.agents.outcomesAria', {
            success: formatRate(successRate),
            failure: formatRate(failureRate),
            timeout: formatRate(timeoutRate),
          })}
        >
          <span
            className="mesh-analytics__outcome-segment mesh-analytics__outcome-segment--success"
            style={{ inlineSize: `${(successRate ?? 0) * 100}%` }}
          />
          <span
            className="mesh-analytics__outcome-segment mesh-analytics__outcome-segment--failure"
            style={{ inlineSize: `${(failureRate ?? 0) * 100}%` }}
          />
          <span
            className="mesh-analytics__outcome-segment mesh-analytics__outcome-segment--timeout"
            style={{ inlineSize: `${(timeoutRate ?? 0) * 100}%` }}
          />
        </div>
        <dl className="mesh-analytics__outcome-legend">
          <div>
            <dt>{t('analytics.agents.successRate')}</dt>
            <dd className="mesh-tnum">{formatRate(successRate)}</dd>
          </div>
          <div>
            <dt>{t('analytics.agents.failureRate')}</dt>
            <dd className="mesh-tnum">{formatRate(failureRate)}</dd>
          </div>
          <div>
            <dt>{t('analytics.agents.timeoutRate')}</dt>
            <dd className="mesh-tnum">{formatRate(timeoutRate)}</dd>
          </div>
        </dl>
      </section>
      <section className="mesh-analytics__token-summary" aria-labelledby={tokenTitleId}>
        <h3 id={tokenTitleId} className="mesh-analytics__subheading">
          {t('analytics.agents.tokenTitle')}
        </h3>
        <dl className="mesh-analytics__token-metrics">
          <div>
            <dt>{t('analytics.agents.totalTokens')}</dt>
            <dd className="mesh-tnum" data-testid="agent-token-total">
              {intl.formatNumber(stats.tokens.total_tokens)}
            </dd>
          </div>
          <div>
            <dt>{t('analytics.agents.promptTokens')}</dt>
            <dd className="mesh-tnum" data-testid="agent-token-prompt">
              {intl.formatNumber(stats.tokens.prompt_tokens)}
            </dd>
          </div>
          <div>
            <dt>{t('analytics.agents.completionTokens')}</dt>
            <dd className="mesh-tnum" data-testid="agent-token-completion">
              {intl.formatNumber(stats.tokens.completion_tokens)}
            </dd>
          </div>
          <div>
            <dt>{t('analytics.agents.tokenCoverage')}</dt>
            <dd className="mesh-tnum" data-testid="agent-token-coverage">
              {formatRate(coverage)}
            </dd>
          </div>
        </dl>
      </section>
      {coverage !== null && coverage < 1 ? (
        <p className="mesh-analytics__token-note" data-testid="agent-stats-token-note">
          {t('analytics.agents.tokenNote')}
        </p>
      ) : null}
    </section>
  );
}
