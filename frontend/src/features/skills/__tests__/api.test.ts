/**
 * 技能 API 契约层测试:每个函数命中正确的方法/路径/请求体,包络解包正确(skill.md §3.1)。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  approveSkill,
  bindSkill,
  bulkBindSkill,
  createSkill,
  createVersion,
  deleteSkill,
  getImportTask,
  getSkill,
  getVersion,
  installSkill,
  listAgentSkills,
  listInstallations,
  listMarketplace,
  listSkills,
  listVersions,
  rollbackInstallation,
  startImport,
  unbindSkill,
  uninstallSkill,
  updateBinding,
  updateInstallation,
  updateSkill,
  workspaceSkillsChannel,
} from '../api';

function makeClient(result: unknown = {}) {
  const request = vi.fn(async () => result);
  const list = vi.fn(async () => ({ data: result as unknown[], next_cursor: null }));
  return { client: { request, list } as unknown as MeshApiClient, request, list };
}

describe('skills 频道助手', () => {
  it('workspace 级 skills 频道名', () => {
    expect(workspaceSkillsChannel('ws-1')).toBe('workspace:ws-1:skills');
  });
});

describe('skills CRUD 路径与包络', () => {
  it('listSkills 透传过滤参数', async () => {
    const { client, list } = makeClient([{ id: 's' }]);
    const res = await listSkills(client, 'ws-1', {
      status: 'published',
      source_type: 'url',
      q: '评审',
      limit: 5,
      cursor: 'c',
    });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/skills', {
      query: { status: 'published', source_type: 'url', q: '评审', limit: 5, cursor: 'c' },
    });
    expect(res.data).toEqual([{ id: 's' }]);
    expect(res.nextCursor).toBeNull();
  });

  it('getSkill / createSkill / updateSkill / deleteSkill 路径与方法', async () => {
    const { client, request } = makeClient({ id: 's-1' });
    await getSkill(client, 'ws-1', 's-1');
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/workspaces/ws-1/skills/s-1');

    await createSkill(client, 'ws-1', { name: 'n', summary: 's', slug: 'sl' });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/skills', {
      body: { name: 'n', summary: 's', slug: 'sl' },
    });

    await updateSkill(client, 'ws-1', 's-1', { status: 'disabled' });
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/workspaces/ws-1/skills/s-1', {
      body: { status: 'disabled' },
    });

    await deleteSkill(client, 'ws-1', 's-1');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/workspaces/ws-1/skills/s-1');
  });
});

describe('versions 路径', () => {
  it('listVersions / getVersion(include_content) / createVersion', async () => {
    const { client, request, list } = makeClient([]);
    await listVersions(client, 'ws-1', 's-1', { limit: 10 });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/skills/s-1/versions', {
      query: { limit: 10, cursor: undefined },
    });

    await getVersion(client, 'ws-1', 's-1', 'v-1', true);
    expect(request).toHaveBeenCalledWith(
      'GET',
      '/api/v1/workspaces/ws-1/skills/s-1/versions/v-1',
      { query: { include_content: 'true' } },
    );

    const body = { version: '1.0.0', instructions: 'x', publish: true };
    await createVersion(client, 'ws-1', 's-1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/skills/s-1/versions', {
      body,
    });
  });
});

describe('import / approve / marketplace 路径', () => {
  it('startImport → 202 任务;getImportTask 查询进度', async () => {
    const { client, request } = makeClient({ task_id: 't-1' });
    await startImport(client, 'ws-1', { source_type: 'url', uri: 'https://x/m.json' });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/skills/import', {
      body: { source_type: 'url', uri: 'https://x/m.json' },
    });
    await getImportTask(client, 'ws-1', 't-1');
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/workspaces/ws-1/skills/import/t-1');
  });

  it('approveSkill 携带审批决策', async () => {
    const { client, request } = makeClient();
    await approveSkill(client, 'ws-1', 's-1', {
      task_id: 't-1',
      granted_capabilities: ['exec:shell'],
      decision: 'approve',
      comment: 'ok',
    });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/skills/s-1/approve', {
      body: {
        task_id: 't-1',
        granted_capabilities: ['exec:shell'],
        decision: 'approve',
        comment: 'ok',
      },
    });
  });

  it('listMarketplace 透传搜索', async () => {
    const { client, list } = makeClient([]);
    await listMarketplace(client, 'ws-1', { q: 'doc', limit: 20 });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/marketplace/skills', {
      query: { q: 'doc', limit: 20 },
    });
  });
});

describe('installations 路径', () => {
  it('install / list / update / uninstall / rollback', async () => {
    const { client, request, list } = makeClient([]);
    await installSkill(client, 'ws-1', {
      skill_id: 's-1',
      skill_version_id: 'v-1',
      scope: 'workspace',
    });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/skill-installations', {
      body: { skill_id: 's-1', skill_version_id: 'v-1', scope: 'workspace' },
    });

    await listInstallations(client, 'ws-1', { skill_id: 's-1', scope: 'workspace' });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/skill-installations', {
      query: { skill_id: 's-1', scope: 'workspace', limit: undefined, cursor: undefined },
    });

    await updateInstallation(client, 'ws-1', 'i-1', { install_status: 'disabled' });
    expect(request).toHaveBeenCalledWith(
      'PATCH',
      '/api/v1/workspaces/ws-1/skill-installations/i-1',
      { body: { install_status: 'disabled' } },
    );

    await uninstallSkill(client, 'ws-1', 'i-1');
    expect(request).toHaveBeenCalledWith(
      'DELETE',
      '/api/v1/workspaces/ws-1/skill-installations/i-1',
    );

    await rollbackInstallation(client, 'ws-1', 'i-1', { target_version_id: 'v-0' });
    expect(request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/workspaces/ws-1/skill-installations/i-1/rollback',
      { body: { target_version_id: 'v-0' } },
    );
  });
});

describe('agent 绑定路径', () => {
  it('list / bind / update / unbind', async () => {
    const { client, request, list } = makeClient([]);
    await listAgentSkills(client, 'ws-1', 'a-1', { limit: 100 });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/agents/a-1/skills', {
      query: { limit: 100, cursor: undefined },
    });

    await bindSkill(client, 'ws-1', 'a-1', { skill_installation_id: 'i-1', priority: 120 });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/agents/a-1/skills', {
      body: { skill_installation_id: 'i-1', priority: 120 },
    });

    await updateBinding(client, 'ws-1', 'a-1', 'b-1', { enabled: false });
    expect(request).toHaveBeenCalledWith(
      'PATCH',
      '/api/v1/workspaces/ws-1/agents/a-1/skills/b-1',
      { body: { enabled: false } },
    );

    await unbindSkill(client, 'ws-1', 'a-1', 'b-1');
    expect(request).toHaveBeenCalledWith(
      'DELETE',
      '/api/v1/workspaces/ws-1/agents/a-1/skills/b-1',
    );
  });

  it('bulkBindSkill 命中 skills/bulk-bind 并透传多 agent 请求体(L247)', async () => {
    const { client, request } = makeClient({ bound: [], errors: [] });
    const result = await bulkBindSkill(client, 'ws-1', {
      skill_installation_id: 'i-1',
      agent_ids: ['a-1', 'a-2'],
    });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/skills/bulk-bind', {
      body: { skill_installation_id: 'i-1', agent_ids: ['a-1', 'a-2'] },
    });
    expect(result).toEqual({ bound: [], errors: [] });
  });
});
