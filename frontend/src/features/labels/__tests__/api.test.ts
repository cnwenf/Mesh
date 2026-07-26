/**
 * 标签与自定义字段(定义层)API 模块测试:校验路径/方法/请求体/查询参数逐字正确。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import * as api from '../api';

function stubClient() {
  return {
    request: vi.fn().mockResolvedValue({ id: 'x', deleted: true }),
    list: vi.fn().mockResolvedValue({ data: [], next_cursor: null }),
  } as unknown as MeshApiClient & {
    request: ReturnType<typeof vi.fn>;
    list: ReturnType<typeof vi.fn>;
  };
}

describe('labels api', () => {
  it('listLabels walks the workspace labels path with query params', async () => {
    const client = stubClient();
    await api.listLabels(client, 'ws-1', { project_id: 'p-1', limit: 10, cursor: 'c' });
    expect(client.list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/labels', {
      query: { project_id: 'p-1', limit: 10, cursor: 'c' },
    });
  });

  it('createLabel posts to the workspace collection', async () => {
    const client = stubClient();
    await api.createLabel(client, 'ws-1', { name: 'bug', color: '#e5484d' });
    expect(client.request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/labels', {
      body: { name: 'bug', color: '#e5484d' },
    });
  });

  it('updateLabel patches with If-Match', async () => {
    const client = stubClient();
    await api.updateLabel(client, 'lbl-1', { name: 'x' }, 'v-1');
    expect(client.request).toHaveBeenCalledWith('PATCH', '/api/v1/labels/lbl-1', {
      body: { name: 'x' },
      ifMatch: 'v-1',
    });
  });

  it('deleteLabel deletes the workspace-less path', async () => {
    const client = stubClient();
    await api.deleteLabel(client, 'lbl-1');
    expect(client.request).toHaveBeenCalledWith('DELETE', '/api/v1/labels/lbl-1');
  });
});

describe('custom fields api', () => {
  it('listCustomFields passes is_active and pagination', async () => {
    const client = stubClient();
    await api.listCustomFields(client, 'ws-1', { is_active: true, limit: 5 });
    expect(client.list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/custom-fields', {
      query: { project_id: undefined, is_active: true, limit: 5, cursor: undefined },
    });
  });

  it('createCustomField posts the definition body', async () => {
    const client = stubClient();
    await api.createCustomField(client, 'ws-1', {
      name: 'Severity',
      field_key: 'severity',
      type: 'single_select',
      options: [{ name: 'Major' }],
    });
    expect(client.request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/workspaces/ws-1/custom-fields',
      {
        body: {
          name: 'Severity',
          field_key: 'severity',
          type: 'single_select',
          options: [{ name: 'Major' }],
        },
      },
    );
  });

  it('updateCustomField patches with If-Match', async () => {
    const client = stubClient();
    await api.updateCustomField(client, 'cf-1', { is_active: false }, 'v-9');
    expect(client.request).toHaveBeenCalledWith('PATCH', '/api/v1/custom-fields/cf-1', {
      body: { is_active: false },
      ifMatch: 'v-9',
    });
  });

  it('deleteCustomField hits the workspace-less path', async () => {
    const client = stubClient();
    await api.deleteCustomField(client, 'cf-1');
    expect(client.request).toHaveBeenCalledWith('DELETE', '/api/v1/custom-fields/cf-1');
  });
});

describe('options api', () => {
  it('listOptions paginates under the field', async () => {
    const client = stubClient();
    await api.listOptions(client, 'cf-1', { limit: 20, cursor: 'k' });
    expect(client.list).toHaveBeenCalledWith('/api/v1/custom-fields/cf-1/options', {
      query: { limit: 20, cursor: 'k' },
    });
  });

  it('createOption posts under the field', async () => {
    const client = stubClient();
    await api.createOption(client, 'cf-1', { name: 'Major', color: '#f5a623', position: 1 });
    expect(client.request).toHaveBeenCalledWith('POST', '/api/v1/custom-fields/cf-1/options', {
      body: { name: 'Major', color: '#f5a623', position: 1 },
    });
  });

  it('updateOption patches the option under its field', async () => {
    const client = stubClient();
    await api.updateOption(client, 'cf-1', 'opt-1', { is_active: false }, 'v-2');
    expect(client.request).toHaveBeenCalledWith(
      'PATCH',
      '/api/v1/custom-fields/cf-1/options/opt-1',
      { body: { is_active: false }, ifMatch: 'v-2' },
    );
  });

  it('deleteOption deletes the option under its field', async () => {
    const client = stubClient();
    await api.deleteOption(client, 'cf-1', 'opt-1');
    expect(client.request).toHaveBeenCalledWith(
      'DELETE',
      '/api/v1/custom-fields/cf-1/options/opt-1',
    );
  });
});

describe('channel helpers', () => {
  it('build the §3.5 channel names', () => {
    expect(api.workspaceLabelsChannel('w')).toBe('workspace:w:labels');
    expect(api.workspaceCustomFieldsChannel('w')).toBe('workspace:w:custom_fields');
    expect(api.projectChannel('p')).toBe('project:p');
  });
});
