/**
 * 洞察仪表盘的两个图表卡(analytics.md §4.3):workload 排行表与 agent 统计网格。
 * 自 InsightsPage 拆出以控文件尺度;数字一律 tabular-nums(.mesh-tnum,§6.3),
 * 成员类型/成功率语义色均带文本兜底(颜色非唯一信号)。
 */
import { EmptyState, Icon } from '../../design';
import { useT } from '../../i18n';
import { ChartFrame } from './ChartFrame';
import { formatDurationSeconds, formatRate, rateTone } from './format';
import type { AgentStatsRow, WorkloadEnvelope } from './types';

/** workload 排行表:成员/agent 同表,在途计数仅 agent 行呈现。 */
export function InsightsWorkloadPanel(props: {
  readonly workload: WorkloadEnvelope;
}): React.JSX.Element {
  const { workload } = props;
  const t = useT();
  return (
    <ChartFrame testId="insights-workload" title={t('analytics.workload.title')}>
      {workload.data.length === 0 ? (
        <EmptyState title={t('analytics.state.noData')} />
      ) : (
        <div
          className="mesh-analytics__table-scroll"
          role="region"
          aria-label={t('analytics.workload.title')}
          tabIndex={0}
        >
          <table className="mesh-analytics__table">
            <caption className="sr-only">{t('analytics.workload.title')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('analytics.workload.member')}</th>
                <th scope="col">{t('analytics.workload.openIssues')}</th>
                <th scope="col">{t('analytics.workload.running')}</th>
                <th scope="col">{t('analytics.workload.queued')}</th>
                <th scope="col">{t('analytics.workload.awaitingApproval')}</th>
              </tr>
            </thead>
            <tbody>
              {workload.data.map((row) => (
                <tr key={row.member_id}>
                  <td>
                    <span className="mesh-analytics__member-label">
                      <Icon
                        name={row.member_type === 'agent' ? 'agent' : 'user'}
                        size={16}
                        className="mesh-analytics__member-type-icon"
                      />
                      <span>
                        {row.display_name}{' '}
                        {t(
                          row.member_type === 'agent'
                            ? 'analytics.workload.typeAgent'
                            : 'analytics.workload.typeHuman',
                        )}
                      </span>
                    </span>
                  </td>
                  <td>
                    <span className="mesh-tnum">{row.open_issues}</span>
                  </td>
                  <td>
                    <span className="mesh-tnum">{row.running ?? '—'}</span>
                  </td>
                  <td>
                    <span className="mesh-tnum">{row.queued ?? '—'}</span>
                  </td>
                  <td>
                    <span className="mesh-tnum">{row.awaiting_approval ?? '—'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ChartFrame>
  );
}

/** agent 统计网格:成功率语义色 + 文本双信号;token 覆盖不足显式口径标注。 */
export function InsightsAgentsPanel(props: {
  readonly agents: readonly AgentStatsRow[];
}): React.JSX.Element {
  const { agents } = props;
  const t = useT();
  return (
    <ChartFrame testId="insights-agents" title={t('analytics.agents.title')}>
      {agents.length === 0 ? (
        <EmptyState title={t('analytics.state.noAgents')} />
      ) : (
        <div className="mesh-analytics__agent-grid">
          {agents.map((agent) => (
            <AgentMetricCard key={agent.agent_id} agent={agent} />
          ))}
        </div>
      )}
    </ChartFrame>
  );
}

function AgentMetricCard(props: { readonly agent: AgentStatsRow }): React.JSX.Element {
  const { agent } = props;
  const t = useT();
  const tone = rateTone(agent.success_rate);
  return (
    <div className="mesh-analytics__agent-card">
      <p className="mesh-analytics__agent-name">{agent.display_name}</p>
      <div className="mesh-analytics__agent-metrics">
        <span>
          {t('analytics.agents.successRate')}:{' '}
          <strong className={`mesh-analytics__metric--${tone} mesh-tnum`}>
            {formatRate(agent.success_rate)}
          </strong>
        </span>
        <span>
          {t('analytics.agents.executions')}: <span className="mesh-tnum">{agent.executions}</span>
        </span>
        <span>
          {t('analytics.agents.avgDuration')}:{' '}
          <span className="mesh-tnum">{formatDurationSeconds(agent.avg_duration_seconds)}</span>
        </span>
        <span>
          {t('analytics.agents.retryRate')}:{' '}
          <span className="mesh-tnum">{formatRate(agent.retry_rate)}</span>
        </span>
      </div>
      {agent.tokens.token_coverage !== null && agent.tokens.token_coverage < 1 ? (
        <p className="mesh-analytics__token-note">{t('analytics.agents.tokenNote')}</p>
      ) : null}
    </div>
  );
}
