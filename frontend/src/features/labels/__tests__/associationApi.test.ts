/**
 * issue 关联层 API 模块测试:路径/方法/请求体/If-Match 逐字正确。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import * as api from '../associationApi';

function stubClient(requestResult: unknown = {}) {
  return {
    request: vi.fn().mockResolvedValue(requestResult),
    list: vi.fn().mockResolvedValue({ data: [], next_cursor: null }),
  } as unknown as MeshApiClient & {
    request: ReturnType<typeof vi.fn>;
    list: ReturnType<typeof vi.fn>;
  };
}

describe('issue labels association api', () => {
  it('listIssueLabels lists the issue sub-resource', async () => {
    const client = stubClient();
    await api.listIssueLabels(client, 'iss-1');
    expect(client.list).toHaveBeenCalledWith('/api/v1/issues/iss-1/labels');
  });

  it('replaceIssueLabels PUTs the whole set with If-Match', async () => {
    const client = stubClient();
    await api.replaceIssueLabels(client, 'iss-1', ['a', 'b'], 'v-1');
    expect(client.request).toHaveBeenCalledWith('PUT', '/api/v1/issues/iss-1/labels', {
      body: { label_ids: ['a', 'b'] },
      ifMatch: 'v-1',
    });
  });

  it('addIssueLabel posts to the label sub-path', async () => {
    const client = stubClient();
    await api.addIssueLabel(client, 'iss-1', 'lbl-1');
    expect(client.request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/issues/iss-1/labels/lbl-1',
    );
  });

  it('removeIssueLabel deletes the label sub-path', async () => {
    const client = stubClient();
    await api.removeIssueLabel(client, 'iss-1', 'lbl-1');
    expect(client.request).toHaveBeenCalledWith(
      'DELETE',
      '/api/v1/issues/iss-1/labels/lbl-1',
    );
  });

  it('mergeLabel posts the target to the source merge path', async () => {
    const client = stubClient();
    await api.mergeLabel(client, 'src-1', 'tgt-1');
    expect(client.request).toHaveBeenCalledWith('POST', '/api/v1/labels/src-1/merge', {
      body: { target_label_id: 'tgt-1' },
    });
  });
});

describe('issue field values association api', () => {
  it('listIssueFieldValues lists the values sub-resource', async () => {
    const client = stubClient();
    await api.listIssueFieldValues(client, 'iss-1');
    expect(client.list).toHaveBeenCalledWith('/api/v1/issues/iss-1/custom-field-values');
  });

  it('setIssueFieldValues PUTs values with If-Match', async () => {
    const client = stubClient([]);
    const values = [{ field_def_id: 'fd-1', value_text: 'x' }];
    await api.setIssueFieldValues(client, 'iss-1', values, 'v-9');
    expect(client.request).toHaveBeenCalledWith(
      'PUT',
      '/api/v1/issues/iss-1/custom-field-values',
      { body: { values }, ifMatch: 'v-9' },
    );
  });

  it('setIssueFieldValues copies the input array (no caller mutation)', async () => {
    const client = stubClient([]);
    const values: { field_def_id: string; value_text?: string }[] = [
      { field_def_id: 'fd-1', value_text: 'x' },
    ];
    await api.setIssueFieldValues(client, 'iss-1', values);
    const sent = client.request.mock.calls[0][2].body.values;
    expect(sent).not.toBe(values);
    expect(sent).toEqual(values);
  });
});
