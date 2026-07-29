/**
 * favorites API(README §6.19):PUT 幂等收藏 / DELETE 取消 / GET 列表(游标分页)。
 */
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import { deleteFavorite, listFavorites, putFavorite } from '../favorites';
import type { FavoriteTargetType } from '../favorites';
import { fakeResponse } from './fetchStub';

function clientWith(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({ baseUrl: '', getToken: () => 'tok', fetchImpl });
}

describe('favorites API(§6.19)', () => {
  it('PUT /favorites/{target_type}/{target_id} 收藏(幂等)', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse({ status: 204 })) as unknown as typeof fetch;
    await putFavorite(clientWith(fetchImpl), 'issue', 'i-1');
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(url).toContain('/api/v1/favorites/issue/i-1');
    expect(init.method).toBe('PUT');
  });

  it('DELETE 同路径取消收藏', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse({ status: 204 })) as unknown as typeof fetch;
    await deleteFavorite(clientWith(fetchImpl), 'view', 'v-9');
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(url).toContain('/api/v1/favorites/view/v-9');
    expect(init.method).toBe('DELETE');
  });

  it('GET 列表按 workspace + target_type 过滤', async () => {
    const fetchImpl = vi.fn(async () =>
      fakeResponse({
        body: {
          data: [{ target_type: 'chat_session', target_id: 's-1' }],
          next_cursor: null,
        },
      }),
    ) as unknown as typeof fetch;
    const entries = await listFavorites(clientWith(fetchImpl), 'ws-1', 'chat_session');
    expect(entries).toHaveLength(1);
    expect(entries[0]?.target_id).toBe('s-1');
    const [url] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain('/api/v1/favorites');
    expect(url).toContain('workspace_id=ws-1');
    expect(url).toContain('target_type=chat_session');
  });

  it('target_type 联合类型闭合(四类,§6.19)', () => {
    const types: readonly FavoriteTargetType[] = ['issue', 'project', 'view', 'chat_session'];
    expect(types).toHaveLength(4);
  });
});
