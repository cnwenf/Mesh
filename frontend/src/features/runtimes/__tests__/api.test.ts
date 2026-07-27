/**
 * Runtime / Execution API 契约层测试(runtime.md §3.1):每个函数命中正确的
 * 方法 / 路径(workspace 作用域)/ 请求体 / 查询参数,包络解包正确;频道助手与
 * SSE 降级 URL、latestAttempt 派生一并覆盖。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  cancelExecution,
  createCredential,
  createRuntime,
  deleteCredential,
  deleteRuntime,
  executionChannel,
  executionLogsChannel,
  executionLogsStreamUrl,
  freezeExecution,
  getExecution,
  getRuntime,
  latestAttempt,
  listCredentials,
  listExecutionLogs,
  listRuntimeExecutions,
  listRuntimes,
  patchRuntime,
  pauseRuntime,
  resumeRuntime,
  rotateRuntimeToken,
  workspaceExecutionsChannel,
  workspaceQueueChannel,
  workspaceRuntimesChannel,
} from '../api';
import type { ExecutionDetail } from '../types';

function makeClient(result: unknown = {}) {
  const request = vi.fn(async () => result);
  const list = vi.fn(async () => ({ data: result as unknown[], next_cursor: null }));
  return { client: { request, list } as unknown as MeshApiClient, request, list };
}

describe('runtime 频道助手(§3.6)', () => {
  it('workspace / execution / logs / queue 频道名', () => {
    expect(workspaceRuntimesChannel('ws-1')).toBe('workspace:ws-1:runtimes');
    expect(workspaceExecutionsChannel('ws-1')).toBe('workspace:ws-1:executions');
    expect(executionChannel('e-1')).toBe('execution:e-1');
    expect(executionLogsChannel('e-1')).toBe('execution:e-1:logs');
    expect(workspaceQueueChannel('ws-1')).toBe('workspace:ws-1:queue');
  });

  it('SSE 降级 URL 携带 offset(§3.3)', () => {
    const url = executionLogsStreamUrl('ws-1', 'e-1', 42);
    expect(url).toContain('/api/v1/workspaces/ws-1/executions/e-1/logs/stream?offset=42');
  });
});

describe('runtime 控制台 API 路径与包络(§3.1)', () => {
  it('listRuntimes 透传筛选 / 分页参数', async () => {
    const { client, list } = makeClient([{ id: 'r-1' }]);
    const res = await listRuntimes(client, 'ws-1', {
      status: 'online',
      kind: 'self_hosted',
      cursor: 'c-1',
      limit: 10,
    });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/runtimes', {
      query: { status: 'online', kind: 'self_hosted', cursor: 'c-1', limit: 10 },
    });
    expect(res.data).toEqual([{ id: 'r-1' }]);
  });

  it('listRuntimes 缺省参数发空 query', async () => {
    const { client, list } = makeClient([]);
    await listRuntimes(client, 'ws-1');
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/runtimes', { query: {} });
  });

  it('createRuntime POST 请求体 → 影子记录 + 激活码', async () => {
    const { client, request } = makeClient({ id: 'r-new', activation: { code: 'ACT-X' } });
    const res = await createRuntime(client, 'ws-1', {
      name: 'build-01',
      kind: 'self_hosted',
      labels: { region: 'intranet' },
      max_concurrent: 4,
    });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/runtimes', {
      body: {
        name: 'build-01',
        kind: 'self_hosted',
        labels: { region: 'intranet' },
        max_concurrent: 4,
      },
    });
    expect(res.activation.code).toBe('ACT-X');
  });

  it('getRuntime 命中详情路径', async () => {
    const { client, request } = makeClient({ id: 'r-1' });
    await getRuntime(client, 'ws-1', 'r-1');
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/workspaces/ws-1/runtimes/r-1');
  });

  it('patchRuntime PATCH 请求体', async () => {
    const { client, request } = makeClient();
    await patchRuntime(client, 'ws-1', 'r-1', { name: 'new', max_concurrent: 2 });
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/workspaces/ws-1/runtimes/r-1', {
      body: { name: 'new', max_concurrent: 2 },
    });
  });

  it('pauseRuntime / resumeRuntime 命中 :pause / :resume 动词端点', async () => {
    const { client, request } = makeClient();
    await pauseRuntime(client, 'ws-1', 'r-1');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/runtimes/r-1:pause', {
      body: {},
    });
    await resumeRuntime(client, 'ws-1', 'r-1');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/runtimes/r-1:resume', {
      body: {},
    });
  });

  it('rotateRuntimeToken 命中 /tokens:rotate 并回传明文 token', async () => {
    const { client, request } = makeClient({ runtime_token: 'rt_live_x' });
    const res = await rotateRuntimeToken(client, 'ws-1', 'r-1');
    expect(request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/workspaces/ws-1/runtimes/r-1/tokens:rotate',
      { body: {} },
    );
    expect(res.runtime_token).toBe('rt_live_x');
  });

  it('deleteRuntime 命中 DELETE', async () => {
    const { client, request } = makeClient();
    await deleteRuntime(client, 'ws-1', 'r-1');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/workspaces/ws-1/runtimes/r-1');
  });

  it('listRuntimeExecutions 命中 /executions 子资源', async () => {
    const { client, list } = makeClient([{ id: 'e-1' }]);
    await listRuntimeExecutions(client, 'ws-1', 'r-1', { cursor: 'c', limit: 5 });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/runtimes/r-1/executions', {
      query: { cursor: 'c', limit: 5 },
    });
  });

  it('listRuntimeExecutions 缺省参数', async () => {
    const { client, list } = makeClient([]);
    await listRuntimeExecutions(client, 'ws-1', 'r-1');
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/runtimes/r-1/executions', {
      query: {},
    });
  });
});

describe('execution 控制台 API(§3.1)', () => {
  it('getExecution 命中详情路径', async () => {
    const { client, request } = makeClient({ id: 'e-1' });
    await getExecution(client, 'ws-1', 'e-1');
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/workspaces/ws-1/executions/e-1');
  });

  it('cancelExecution 命中 :cancel', async () => {
    const { client, request } = makeClient({ id: 'e-1', status: 'cancelling' });
    await cancelExecution(client, 'ws-1', 'e-1');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/executions/e-1:cancel', {
      body: {},
    });
  });

  it('freezeExecution 命中 :freeze', async () => {
    const { client, request } = makeClient();
    await freezeExecution(client, 'ws-1', 'e-1');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/executions/e-1:freeze', {
      body: {},
    });
  });

  it('listExecutionLogs 携带 offset / stream 查询,解包内层 {lines, next_offset}', async () => {
    const page = { lines: [{ stream: 'stdout', offset: 1, line: 'x' }], next_offset: 2 };
    const { client, request } = makeClient(page);
    const res = await listExecutionLogs(client, 'ws-1', 'e-1', { offset: 0, stream: 'stdout' });
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/workspaces/ws-1/executions/e-1/logs', {
      query: { offset: 0, stream: 'stdout' },
    });
    expect(res).toEqual(page);
  });

  it('listExecutionLogs 缺省参数', async () => {
    const { client, request } = makeClient({ lines: [], next_offset: 0 });
    await listExecutionLogs(client, 'ws-1', 'e-1');
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/workspaces/ws-1/executions/e-1/logs', {
      query: {},
    });
  });
});

describe('credentials API(§3.1:明文只进不出)', () => {
  it('listCredentials 走列表包络', async () => {
    const { client, list } = makeClient([{ id: 'cr-1', name: 'a', kind: 'env' }]);
    const res = await listCredentials(client, 'ws-1');
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/credentials');
    expect(res).toEqual([{ id: 'cr-1', name: 'a', kind: 'env' }]);
  });

  it('createCredential POST 明文仅在请求体', async () => {
    const { client, request } = makeClient({ id: 'cr-new', name: 'a', kind: 'env' });
    await createCredential(client, 'ws-1', { name: 'a', kind: 'env', value: 'sk-xxx' });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/credentials', {
      body: { name: 'a', kind: 'env', value: 'sk-xxx' },
    });
  });

  it('deleteCredential 命中 DELETE', async () => {
    const { client, request } = makeClient();
    await deleteCredential(client, 'ws-1', 'cr-1');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/workspaces/ws-1/credentials/cr-1');
  });
});

describe('latestAttempt 派生(§2.1:attempt 按 attempt_number 追加)', () => {
  const base = {
    id: 'e-1',
    agent_id: null,
    trigger: 'assign',
    status: 'running',
    priority: 100,
    required_capabilities: [],
    label_requirements: {},
    timeout_seconds: 1800,
    queued_at: '2026-01-01T00:00:00Z',
    finished_at: null,
    failure_reason: null,
    result: null,
    max_attempts: 3,
  } as const;

  it('空 attempts → null', () => {
    expect(latestAttempt({ ...base, attempts: [] })).toBeNull();
  });

  it('取最后一个 attempt(requeue 新行在末尾)', () => {
    const execution: ExecutionDetail = {
      ...base,
      attempts: [
        {
          id: 'a-1',
          attempt_number: 1,
          status: 'reclaimed',
          claimed_at: null,
          started_at: null,
          finished_at: null,
          working_branch: null,
          failure_reason: 'lease_expired',
        },
        {
          id: 'a-2',
          attempt_number: 2,
          status: 'running',
          claimed_at: null,
          started_at: null,
          finished_at: null,
          working_branch: 'agent/e-1/a2',
          failure_reason: null,
        },
      ],
    };
    expect(latestAttempt(execution)?.id).toBe('a-2');
  });
});
