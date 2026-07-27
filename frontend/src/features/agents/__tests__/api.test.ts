/**
 * Agent API 契约层测试:每个函数命中正确的方法/路径/请求体,包络解包正确。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  agentPresenceChannel,
  createAgent,
  deleteAgent,
  getAgent,
  listAgents,
  listConfigVersions,
  rollbackConfig,
  transferAgent,
  transitionAgentLifecycle,
  updateAgent,
  updateAgentConfig,
  workspaceAgentsChannel,
} from '../api';

function makeClient(result: unknown = {}) {
  const request = vi.fn(async () => result);
  const list = vi.fn(async () => ({ data: result as unknown[], next_cursor: null }));
  return { client: { request, list } as unknown as MeshApiClient, request, list };
}

describe('agent 频道助手', () => {
  it('workspace 级与 agent 级频道名', () => {
    expect(workspaceAgentsChannel('ws-1')).toBe('workspace:ws-1:agents');
    expect(agentPresenceChannel('a-1')).toBe('agent:a-1:presence');
  });
});

describe('agent API 路径与包络', () => {
  it('listAgents 透传筛选参数', async () => {
    const { client, list } = makeClient([{ id: 'a' }]);
    const res = await listAgents(client, 'ws-1', {
      status: 'active',
      visibility: 'private',
      ownerId: 'u-1',
      q: 'x',
      limit: 5,
      cursor: 'c',
    });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/agents', {
      query: {
        status: 'active',
        visibility: 'private',
        owner_id: 'u-1',
        q: 'x',
        limit: 5,
        cursor: 'c',
      },
    });
    expect(res.data).toEqual([{ id: 'a' }]);
  });

  it('listAgents 缺省参数', async () => {
    const { client, list } = makeClient([]);
    await listAgents(client, 'ws-1');
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/agents', { query: {} });
  });

  it('getAgent 命中详情路径', async () => {
    const { client, request } = makeClient({ id: 'a-1' });
    await getAgent(client, 'ws-1', 'a-1');
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/workspaces/ws-1/agents/a-1');
  });

  it('createAgent POST 请求体', async () => {
    const { client, request } = makeClient({ id: 'a-new' });
    await createAgent(client, 'ws-1', { name: '小测', visibility: 'workspace' });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/agents', {
      body: { name: '小测', visibility: 'workspace' },
    });
  });

  it('updateAgent PATCH 请求体', async () => {
    const { client, request } = makeClient();
    await updateAgent(client, 'ws-1', 'a-1', { visibility: 'private' });
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/workspaces/ws-1/agents/a-1', {
      body: { visibility: 'private' },
    });
  });

  it('updateAgentConfig 命中 /config', async () => {
    const { client, request } = makeClient();
    const body = { model_config: { temperature: 0.7 } };
    await updateAgentConfig(client, 'ws-1', 'a-1', body);
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/workspaces/ws-1/agents/a-1/config', {
      body,
    });
  });

  it('listConfigVersions 命中 /config-versions', async () => {
    const { client, list } = makeClient([]);
    await listConfigVersions(client, 'ws-1', 'a-1', { limit: 10, cursor: 'c' });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/agents/a-1/config-versions', {
      query: { limit: 10, cursor: 'c' },
    });
  });

  it('listConfigVersions 缺省参数', async () => {
    const { client, list } = makeClient([]);
    await listConfigVersions(client, 'ws-1', 'a-1');
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/agents/a-1/config-versions', {
      query: {},
    });
  });

  it('rollbackConfig 命中 :rollback', async () => {
    const { client, request } = makeClient();
    await rollbackConfig(client, 'ws-1', 'a-1', 'v-1');
    expect(request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/workspaces/ws-1/agents/a-1/config-versions/v-1:rollback',
    );
  });

  it('transitionAgentLifecycle 命中 :verb 并携带 body', async () => {
    const { client, request } = makeClient();
    await transitionAgentLifecycle(client, 'ws-1', 'a-1', 'pause', {
      in_flight_policy: 'cancel_current',
      reason: '维护',
    });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/agents/a-1:pause', {
      body: { in_flight_policy: 'cancel_current', reason: '维护' },
    });
  });

  it('transitionAgentLifecycle 无 body 时发空对象', async () => {
    const { client, request } = makeClient();
    await transitionAgentLifecycle(client, 'ws-1', 'a-1', 'resume');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/agents/a-1:resume', {
      body: {},
    });
  });

  it('transferAgent 命中 :transfer', async () => {
    const { client, request } = makeClient();
    await transferAgent(client, 'ws-1', 'a-1', 'u-9');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/agents/a-1:transfer', {
      body: { new_owner_user_id: 'u-9' },
    });
  });

  it('deleteAgent 命中 DELETE', async () => {
    const { client, request } = makeClient();
    await deleteAgent(client, 'ws-1', 'a-1');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/workspaces/ws-1/agents/a-1');
  });
});
