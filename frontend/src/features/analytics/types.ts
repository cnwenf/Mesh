/**
 * 统计报表响应类型(analytics.md §3 端点契约;只读聚合)。
 * 字段名与后端 JSON 一致(snake_case 为线上形态,前端不做改名)。
 */

export type Granularity = 'day' | 'week' | 'month';
export type BurndownMetric = 'count' | 'points';

export interface AnalyticsMeta {
  readonly display_timezone?: string;
  readonly cached?: boolean;
  readonly [key: string]: unknown;
}

/** GET /analytics/cycle-time (§2.2.1) */
export interface CycleTimeData {
  readonly project_id: string | null;
  readonly from_category: string;
  readonly p50_seconds: number | null;
  readonly p90_seconds: number | null;
  readonly sample_size: number;
  readonly meta: AnalyticsMeta & { readonly insufficient_data: number };
}

/** GET /analytics/throughput (§2.2.3,calendar_timezone 本地日历分桶) */
export interface ThroughputBucket {
  readonly label: string;
  readonly bucket: string;
  readonly window_start: string;
  readonly window_end: string;
  readonly created: number;
  readonly completed: number;
  readonly net: number;
}

export interface ThroughputData {
  readonly granularity: Granularity;
  readonly series: readonly ThroughputBucket[];
  readonly meta: AnalyticsMeta & {
    readonly calendar_timezone: string;
    readonly net_window: number;
  };
}

/** GET /analytics/velocity (§2.2.2,当前归属口径) */
export interface VelocityCycle {
  readonly cycle_id: string;
  readonly name: string;
  readonly starts_at: string;
  readonly ends_at: string;
  readonly state: string;
  readonly completed_issues: number;
  readonly completed_points: number;
  readonly completed_points_by_unit: {
    readonly points: number;
    readonly hours: number;
  };
}

export interface VelocityData {
  readonly cycles: readonly VelocityCycle[];
  readonly meta: AnalyticsMeta & { readonly scope_caliber: string };
}

/** GET /analytics/workload (§2.2.4,成员维度统一) */
export interface WorkloadRow {
  readonly member_id: string;
  readonly display_name: string;
  readonly member_type: 'human' | 'agent';
  readonly open_issues: number;
  readonly running: number | null;
  readonly queued: number | null;
  readonly awaiting_approval: number | null;
}

export interface WorkloadEnvelope {
  readonly data: readonly WorkloadRow[];
  readonly next_cursor: string | null;
}

/** GET /analytics/burndown (§2.2.5,理想线 vs 实际线) */
export interface BurndownPoint {
  readonly date: string;
  readonly remaining: number;
}

export interface BurndownData {
  readonly scope: { readonly type: 'cycle' | 'milestone'; readonly id: string };
  readonly window: { readonly start: string; readonly end: string };
  readonly metric: BurndownMetric;
  readonly total: number;
  readonly ideal: readonly BurndownPoint[];
  readonly actual: readonly BurndownPoint[];
  readonly meta: AnalyticsMeta & { readonly scope_caliber: string };
}

/** GET /analytics/agents/stats (§2.3,token 仅覆盖 autopilot 触发执行) */
export interface AgentTokenStats {
  readonly prompt_tokens: number;
  readonly completion_tokens: number;
  readonly total_tokens: number;
  readonly token_coverage: number | null;
}

export interface AgentStatsRow {
  readonly agent_id: string;
  readonly display_name: string;
  readonly member_type: string;
  readonly executions: number;
  readonly succeeded: number;
  readonly terminal: number;
  readonly cancelled_count: number;
  readonly success_rate: number | null;
  readonly timeout_rate: number | null;
  readonly avg_duration_seconds: number | null;
  readonly retry_rate: number | null;
  readonly tokens: AgentTokenStats;
  readonly meta: { readonly token_note: string };
}

/** 单 agent 模式:直接返回一行;多 agent 模式:{agents:[...]} */
export interface AgentStatsMulti {
  readonly agents: readonly AgentStatsRow[];
  readonly meta: AnalyticsMeta;
}

/** GET /dashboards/workspace (§4.3,按请求者可见性过滤) */
export interface WorkspaceDashboardData {
  readonly throughput: ThroughputData;
  readonly workload: WorkloadEnvelope;
  readonly agent_stats: AgentStatsMulti;
  readonly meta: {
    readonly visibility_filtered: boolean;
    /** 口径回显时区(若顶层未给,UI 回退 throughput.meta.calendar_timezone) */
    readonly display_timezone?: string;
  };
}

/** GET /dashboards/project/{id} (§4.2) */
export interface ProjectDashboardData {
  readonly project_id: string;
  readonly velocity: VelocityData;
  readonly burndown: BurndownData | null;
  readonly cycle_time: CycleTimeData;
}
