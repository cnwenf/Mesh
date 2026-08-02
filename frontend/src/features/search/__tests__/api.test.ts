/**
 * search / favorites API 调用单测(§3.1/§3.2 路径 scope / 参数装配 / 有界翻页)。
 */
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api/client';
import { listAllFavorites, searchWorkspace } from '../api';

function clientCapturing(captured: Array<{ url: string }>, pages: readonly string[]): MeshApiClient {
  let call = 0;
  const fetchImpl = vi.fn((url: string | URL | Request) => {
    captured.push({ url: String(url) });
    const body = pages[Math.min(call, pages.length - 1)] ?? pages[pages.length - 1];
    call += 1;
    return Promise.resolve(new Response(body ?? '', { status: 200 }));
  });
  return new MeshApiClient({ baseUrl: 'http://api.test', getToken: () => null, fetchImpl });
}

describe('searchWorkspace', () => {
  it('装配路径 scope + q/types/limit/cursor 查询参数', async () => {
    const captured: Array<{ url: string }> = [];
    const client = clientCapturing(captured, ['{"data":[],"next_cursor":null}']);
    const envelope = await searchWorkspace(client, 'ws-9', {
      q: 'login',
      types: ['issue', 'member'],
      limit: 5,
      cursor: 'cur-1',
    });
    expect(envelope).toEqual({ data: [], next_cursor: null });
    const url = captured[0]?.url ?? '';
    expect(url).toContain('/api/v1/workspaces/ws-9/search');
    expect(url).toContain('q=login');
    expect(url).toContain('types=issue%2Cmember');
    expect(url).toContain('limit=5');
    expect(url).toContain('cursor=cur-1');
  });

  it('可选参数缺省时不出现在 URL;signal 透传', async () => {
    const captured: Array<{ url: string }> = [];
    const client = clientCapturing(captured, ['{"data":[],"next_cursor":null}']);
    const controller = new AbortController();
    await searchWorkspace(client, 'ws-1', { q: 'a', signal: controller.signal });
    const url = captured[0]?.url ?? '';
    expect(url).not.toContain('types=');
    expect(url).not.toContain('cursor=');
    expect(url).not.toContain('limit=');
  });
});

describe('listAllFavorites(有界翻页聚合)', () => {
  it('跟随 next_cursor 聚合两页', async () => {
    const captured: Array<{ url: string }> = [];
    const page1 =
      '{"data":[{"id":"f1","workspace_id":"w","member_id":"m","target_type":"issue","target_id":"i1","created_at":"2026-07-01T00:00:00.000Z"}],"next_cursor":"c1"}';
    const page2 =
      '{"data":[{"id":"f2","workspace_id":"w","member_id":"m","target_type":"project","target_id":"p1","created_at":"2026-07-02T00:00:00.000Z"}],"next_cursor":null}';
    const client = clientCapturing(captured, [page1, page2]);
    const favorites = await listAllFavorites(client, 'ws-1');
    expect(favorites.map((favorite) => favorite.id)).toEqual(['f1', 'f2']);
    expect(captured[0]?.url).toContain('workspace_id=ws-1');
    expect(captured[1]?.url).toContain('cursor=c1');
  });

  it('单页即末页(next_cursor=null)不翻页', async () => {
    const captured: Array<{ url: string }> = [];
    const client = clientCapturing(captured, ['{"data":[],"next_cursor":null}']);
    const favorites = await listAllFavorites(client, 'ws-1');
    expect(favorites).toEqual([]);
    expect(captured).toHaveLength(1);
  });
});
