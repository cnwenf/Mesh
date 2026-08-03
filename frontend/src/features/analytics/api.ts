/**
 * 统计报表 API 调用(契约层,analytics.md §3 / README §6.14 包络)。
 * 一切时间参数为 RFC3339 UTC;单对象走 `request`,workload 列表走 `list`。
 */
import type { MeshApiClient } from '../../api';
import type {
  AgentStatsMulti,
  AgentStatsRow,
  BurndownData,
  BurndownMetric,
  CycleTimeData,
  Granularity,
  ProjectDashboardData,
  ThroughputData,
  VelocityData,
  WorkloadEnvelope,
  WorkspaceDashboardData,
} from './types';

const analyticsPath = (workspaceId: string, metric: string): string =>
  `/api/v1/workspaces/${workspaceId}/analytics/${metric}`;

export interface WindowParams {
  readonly from?: string;
  readonly to?: string;
  readonly tz?: string;
  readonly refresh?: boolean;
  /** 页面筛选变化或卸载时取消已过期的只读聚合请求。 */
  readonly signal?: AbortSignal;
}

/** GET /analytics/cycle-time */
export async function fetchCycleTime(
  client: MeshApiClient,
  workspaceId: string,
  params: WindowParams & { readonly projectId?: string; readonly fromCategory?: string } = {},
): Promise<CycleTimeData> {
  return client.request<CycleTimeData>('GET', analyticsPath(workspaceId, 'cycle-time'), {
    query: {
      project_id: params.projectId,
      from: params.from,
      to: params.to,
      from_category: params.fromCategory,
      tz: params.tz,
      refresh: params.refresh,
    },
    signal: params.signal,
  });
}

/** GET /analytics/velocity */
export async function fetchVelocity(
  client: MeshApiClient,
  workspaceId: string,
  params: WindowParams & {
    readonly projectId?: string;
    readonly cycleIds?: readonly string[];
  } = {},
): Promise<VelocityData> {
  return client.request<VelocityData>('GET', analyticsPath(workspaceId, 'velocity'), {
    query: {
      project_id: params.projectId,
      cycle_ids: params.cycleIds !== undefined ? params.cycleIds.join(',') : undefined,
      from: params.from,
      to: params.to,
      tz: params.tz,
      refresh: params.refresh,
    },
    signal: params.signal,
  });
}

/** GET /analytics/throughput */
export async function fetchThroughput(
  client: MeshApiClient,
  workspaceId: string,
  params: WindowParams & {
    readonly projectId?: string;
    readonly granularity?: Granularity;
    readonly calendarTimezone?: string;
  } = {},
): Promise<ThroughputData> {
  return client.request<ThroughputData>('GET', analyticsPath(workspaceId, 'throughput'), {
    query: {
      project_id: params.projectId,
      from: params.from,
      to: params.to,
      granularity: params.granularity,
      tz: params.tz,
      calendar_timezone: params.calendarTimezone,
      refresh: params.refresh,
    },
    signal: params.signal,
  });
}

/** GET /analytics/workload(列表包络 {data, next_cursor}) */
export async function fetchWorkload(
  client: MeshApiClient,
  workspaceId: string,
  params: {
    readonly projectId?: string;
    readonly memberType?: 'human' | 'agent';
    readonly cursor?: string;
    readonly limit?: number;
    readonly signal?: AbortSignal;
  } = {},
): Promise<WorkloadEnvelope> {
  return client.list<WorkloadEnvelope['data'][number]>(analyticsPath(workspaceId, 'workload'), {
    query: {
      project_id: params.projectId,
      member_type: params.memberType,
      cursor: params.cursor,
      limit: params.limit,
    },
    signal: params.signal,
  });
}

/** GET /analytics/burndown(cycle_id / milestone_id 恰好一个) */
export async function fetchBurndown(
  client: MeshApiClient,
  workspaceId: string,
  params: WindowParams & {
    readonly cycleId?: string;
    readonly milestoneId?: string;
    readonly metric?: BurndownMetric;
  },
): Promise<BurndownData> {
  return client.request<BurndownData>('GET', analyticsPath(workspaceId, 'burndown'), {
    query: {
      cycle_id: params.cycleId,
      milestone_id: params.milestoneId,
      metric: params.metric,
      tz: params.tz,
      refresh: params.refresh,
    },
    signal: params.signal,
  });
}

/** GET /analytics/agents/stats(单 agent 返回一行,多 agent 返回 {agents}) */
export async function fetchAgentStats(
  client: MeshApiClient,
  workspaceId: string,
  params: WindowParams & { readonly agentId?: string } = {},
): Promise<AgentStatsRow | AgentStatsMulti> {
  return client.request<AgentStatsRow | AgentStatsMulti>(
    'GET',
    analyticsPath(workspaceId, 'agents/stats'),
    {
      query: {
        agent_id: params.agentId,
        from: params.from,
        to: params.to,
        refresh: params.refresh,
      },
      signal: params.signal,
    },
  );
}

/** GET /dashboards/project/{project_id} */
export async function fetchProjectDashboard(
  client: MeshApiClient,
  workspaceId: string,
  projectId: string,
  params: WindowParams & { readonly cycleId?: string } = {},
): Promise<ProjectDashboardData> {
  return client.request<ProjectDashboardData>(
    'GET',
    `/api/v1/workspaces/${workspaceId}/dashboards/project/${projectId}`,
    {
      query: {
        from: params.from,
        to: params.to,
        cycle_id: params.cycleId,
        tz: params.tz,
        refresh: params.refresh,
      },
      signal: params.signal,
    },
  );
}

/** GET /dashboards/workspace */
export async function fetchWorkspaceDashboard(
  client: MeshApiClient,
  workspaceId: string,
  params: WindowParams & {
    readonly granularity?: Granularity;
    readonly calendarTimezone?: string;
  } = {},
): Promise<WorkspaceDashboardData> {
  return client.request<WorkspaceDashboardData>(
    'GET',
    `/api/v1/workspaces/${workspaceId}/dashboards/workspace`,
    {
      query: {
        from: params.from,
        to: params.to,
        granularity: params.granularity,
        tz: params.tz,
        calendar_timezone: params.calendarTimezone,
        refresh: params.refresh,
      },
      signal: params.signal,
    },
  );
}
