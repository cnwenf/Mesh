/**
 * api.ts 契约层测试(analytics.md §3.1):路径拼装 + 查询参数 + 包络。
 * client 以最小桩替代(记录 method/path/query)。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  fetchAgentStats,
  fetchBurndown,
  fetchCycleTime,
  fetchProjectDashboard,
  fetchThroughput,
  fetchVelocity,
  fetchWorkload,
  fetchWorkspaceDashboard,
} from '../api';

interface Call {
  method?: string;
  path: string;
  opts?: { query?: Record<string, string | number | boolean | undefined> };
}

function makeClient(): { client: MeshApiClient; calls: Call[] } {
  const calls: Call[] = [];
  const client = {
    request: vi.fn(async (method: string, path: string, opts?: Call['opts']) => {
      calls.push({ method, path, opts });
      return {};
    }),
    list: vi.fn(async (path: string, opts?: Call['opts']) => {
      calls.push({ path, opts });
      return { data: [], next_cursor: null };
    }),
  };
  return { client: client as unknown as MeshApiClient, calls };
}

describe('analytics api paths and params', () => {
  it('fetchCycleTime assembles path and query', async () => {
    const { client, calls } = makeClient();
    await fetchCycleTime(client, 'ws1', {
      projectId: 'p1',
      from: '2026-07-01T00:00:00Z',
      to: '2026-07-08T00:00:00Z',
      fromCategory: 'in_review',
      tz: 'Asia/Shanghai',
      refresh: true,
    });
    expect(calls[0].method).toBe('GET');
    expect(calls[0].path).toBe('/api/v1/workspaces/ws1/analytics/cycle-time');
    expect(calls[0].opts?.query).toMatchObject({
      project_id: 'p1',
      from_category: 'in_review',
      tz: 'Asia/Shanghai',
      refresh: true,
    });
  });

  it('fetchVelocity joins cycle ids and defaults empty params', async () => {
    const { client, calls } = makeClient();
    await fetchVelocity(client, 'ws1', { cycleIds: ['c1', 'c2'] });
    expect(calls[0].path).toBe('/api/v1/workspaces/ws1/analytics/velocity');
    expect(calls[0].opts?.query?.cycle_ids).toBe('c1,c2');
    // no params → all queries undefined
    await fetchVelocity(client, 'ws1');
    expect(calls[1].opts?.query?.cycle_ids).toBeUndefined();
  });

  it('fetchThroughput maps granularity and calendar timezone', async () => {
    const { client, calls } = makeClient();
    await fetchThroughput(client, 'ws1', {
      granularity: 'week',
      calendarTimezone: 'America/New_York',
    });
    expect(calls[0].path).toBe('/api/v1/workspaces/ws1/analytics/throughput');
    expect(calls[0].opts?.query).toMatchObject({
      granularity: 'week',
      calendar_timezone: 'America/New_York',
    });
  });

  it('fetchWorkload uses the list envelope', async () => {
    const { client, calls } = makeClient();
    const result = await fetchWorkload(client, 'ws1', {
      memberType: 'agent',
      limit: 10,
      cursor: 'cur',
    });
    expect(calls[0].path).toBe('/api/v1/workspaces/ws1/analytics/workload');
    expect(calls[0].opts?.query).toMatchObject({ member_type: 'agent', limit: 10, cursor: 'cur' });
    expect(result.next_cursor).toBeNull();
  });

  it('fetchBurndown passes exactly one scope', async () => {
    const { client, calls } = makeClient();
    await fetchBurndown(client, 'ws1', { cycleId: 'c1', metric: 'count' });
    expect(calls[0].path).toBe('/api/v1/workspaces/ws1/analytics/burndown');
    expect(calls[0].opts?.query).toMatchObject({ cycle_id: 'c1', metric: 'count' });
  });

  it('fetchAgentStats single and multi mode', async () => {
    const { client, calls } = makeClient();
    await fetchAgentStats(client, 'ws1', { agentId: 'a1' });
    await fetchAgentStats(client, 'ws1');
    expect(calls[0].path).toBe('/api/v1/workspaces/ws1/analytics/agents/stats');
    expect(calls[0].opts?.query?.agent_id).toBe('a1');
    expect(calls[1].opts?.query?.agent_id).toBeUndefined();
  });

  it('fetchProjectDashboard targets the project path', async () => {
    const { client, calls } = makeClient();
    await fetchProjectDashboard(client, 'ws1', 'p1', { cycleId: 'c9' });
    expect(calls[0].path).toBe('/api/v1/workspaces/ws1/dashboards/project/p1');
    expect(calls[0].opts?.query?.cycle_id).toBe('c9');
  });

  it('fetchWorkspaceDashboard maps params', async () => {
    const { client, calls } = makeClient();
    await fetchWorkspaceDashboard(client, 'ws1', { granularity: 'month' });
    expect(calls[0].path).toBe('/api/v1/workspaces/ws1/dashboards/workspace');
    expect(calls[0].opts?.query?.granularity).toBe('month');
  });
});
